"""tests/test_market_data.py — db_collector/market_data.py 저장소 + 소비자 배선 단위 테스트.

커버리지:
- 테이블 생성 멱등성 / upsert (macro_daily, market_flow_daily)
- latest_asof / series_asof_window 룩어헤드 컷오프 규칙 (US=kst_date-1, KR/FX=kst_date)
- kis_api.polymarket._calc_macro_thresholds: 돌파 / 미돌파 / 데이터부족 3케이스
- kis_api.regime.calc_kr_regime: foreign_5d 채움 + None 폴백 + -2조원 확인 플래그
- kis_api.kr_stock._fetch_market_investor_flow_range: 응답 파싱 + 300행 청크 분기

실 DB는 건드리지 않는다 — 전부 tmp_path sqlite + monkeypatch(db_collector.DB_PATH).
"""
import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

os.makedirs("/tmp/test_data", exist_ok=True)

import db_collector
from db_collector import market_data as md

import kis_api.kr_stock as kr_stock
import kis_api.regime as regime
from kis_api.polymarket import _calc_macro_thresholds
import main_pkg.jobs.market_data as job_mod


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """db_collector.DB_PATH를 tmp sqlite로 교체 (패키지 프록시가 _db 모듈로 전파)."""
    db_path = str(tmp_path / "test_market_data.db")
    monkeypatch.setattr(db_collector, "DB_PATH", db_path)
    return db_path


def _seed_macro(conn, series, rows):
    """rows: [(date_str, value), ...] — macro_daily에 직접 INSERT."""
    for date_str, value in rows:
        conn.execute(
            "INSERT OR REPLACE INTO macro_daily (series, date, value, source, updated_at) "
            "VALUES (?, ?, ?, 'test', 'test')",
            (series, date_str, value),
        )
    conn.commit()


def _seed_flow(conn, market, rows):
    """rows: [(date_str, frgn_net), ...] — market_flow_daily에 직접 INSERT."""
    for date_str, frgn in rows:
        conn.execute(
            "INSERT OR REPLACE INTO market_flow_daily "
            "(date, market, frgn_net, orgn_net, prsn_net, updated_at) "
            "VALUES (?, ?, ?, 0, 0, 'test')",
            (date_str, market, frgn),
        )
    conn.commit()


def _dates_back(end_str, n):
    """end_str("YYYY-MM-DD")부터 역순 n개 연속 날짜(달력일 기준, 테스트 편의)."""
    end_dt = datetime.strptime(end_str, "%Y-%m-%d")
    return [(end_dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _asc_dates(end_str, n):
    """end_str("YYYY-MM-DD")로 끝나는 오름차순 n개 연속 날짜 (index[-1] == end_str)."""
    end_dt = datetime.strptime(end_str, "%Y-%m-%d")
    return [(end_dt - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d") for i in range(n)]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 테이블 생성 멱등성 + upsert
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestSchemaAndUpsert:
    def test_ensure_tables_idempotent(self, tmp_db):
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        md._ensure_market_data_tables(conn)  # 2회 호출해도 에러 없음
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "macro_daily" in tables
        assert "market_flow_daily" in tables
        conn.close()

    def test_macro_daily_upsert_overwrites_same_pk(self, tmp_db):
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        _seed_macro(conn, "vix", [("2026-09-01", 15.0)])
        _seed_macro(conn, "vix", [("2026-09-01", 22.5)])  # 같은 (series,date) 재기록
        row = conn.execute(
            "SELECT value FROM macro_daily WHERE series='vix' AND date='2026-09-01'"
        ).fetchone()
        assert row["value"] == 22.5
        cnt = conn.execute("SELECT COUNT(*) c FROM macro_daily WHERE series='vix'").fetchone()["c"]
        assert cnt == 1  # 중복 행 없이 덮어쓰기
        conn.close()

    def test_market_flow_daily_upsert_overwrites_same_pk(self, tmp_db):
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        _seed_flow(conn, "KSP", [("2026-09-01", 1000)])
        _seed_flow(conn, "KSP", [("2026-09-01", -500)])
        row = conn.execute(
            "SELECT frgn_net FROM market_flow_daily WHERE date='2026-09-01' AND market='KSP'"
        ).fetchone()
        assert row["frgn_net"] == -500
        conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. latest_asof / series_asof_window — 룩어헤드 컷오프 규칙
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestLookaheadCutoff:
    def test_us_series_excludes_same_day_row(self, tmp_db):
        """US 시리즈(sp500)는 kst_date 당일 행이 있어도 전일까지만 조회."""
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        _seed_macro(conn, "sp500", [("2026-09-02", 6800.0), ("2026-09-03", 6850.0)])
        conn.close()

        result = md.latest_asof("sp500", "2026-09-03")
        assert result is not None
        assert result["date"] == "2026-09-02"  # 당일(09-03)행은 제외, 전일까지만

    def test_kr_series_includes_same_day_row(self, tmp_db):
        """KR 시리즈(kospi)는 kst_date 당일 행까지 조회 가능."""
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        _seed_macro(conn, "kospi", [("2026-09-02", 3400.0), ("2026-09-03", 3420.0)])
        conn.close()

        result = md.latest_asof("kospi", "2026-09-03")
        assert result is not None
        assert result["date"] == "2026-09-03"

    def test_fx_series_includes_same_day_row(self, tmp_db):
        """FX 시리즈(usdkrw)도 KR과 동일 — 당일까지 조회 가능."""
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        _seed_macro(conn, "usdkrw", [("2026-09-03", 1380.0)])
        conn.close()

        result = md.latest_asof("usdkrw", "2026-09-03")
        assert result is not None and result["date"] == "2026-09-03"

    def test_no_data_returns_none(self, tmp_db):
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        conn.close()
        assert md.latest_asof("vix", "2026-09-03") is None

    def test_series_asof_window_orders_desc_and_limits(self, tmp_db):
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        _seed_macro(conn, "kospi", [(d, 100.0 + i) for i, d in enumerate(
            ["2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03"])])
        conn.close()

        w = md.series_asof_window("kospi", "2026-09-03", n=3)
        assert [r["date"] for r in w] == ["2026-09-03", "2026-09-02", "2026-09-01"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. kis_api.polymarket._calc_macro_thresholds — 돌파/미돌파/데이터부족
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestMacroThresholds:
    def _seed_full(self, conn, kst_date, *, vix_latest,
                    usdkrw_latest, usdkrw_prior,
                    us10y_latest, us10y_prior,
                    us2y_latest=4.10,
                    wti_latest=76.0, wti_prior=80.0, wti_rows=True,
                    dxy_latest=99.3, dxy_prior=99.0,
                    kospi_latest_mult=0.85, sp500_rows=60):
        """items 8종(W5부터 US10Y-2Y 스프레드 포함)을 명시값으로 시딩 — 각 케이스가 어떤
        item을 왜 돌파/미돌파/부족시키는지 호출부에서 한눈에 알 수 있게 전부 키워드 인자로 노출.

        ⚠️ vix/us10y/us2y/wti/dxy/sp500은 MACRO_SERIES group="US" → series_asof_window
        컷오프가 kst_date-1일까지만 잡히므로(룩어헤드 방지), "최신"행은 kst_date가
        아니라 kst_date-1일에 심어야 latest_asof에 잡힌다. usdkrw/kospi는 group=
        "FX"/"KR"이라 kst_date 당일까지 포함된다 — 두 앵커(us_end/kr_end)를 분리."""
        us_end = (datetime.strptime(kst_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        kr_end = kst_date

        _seed_macro(conn, "vix", [(us_end, vix_latest)])
        _seed_macro(conn, "us2y", [(us_end, us2y_latest)])

        d6_kr = _asc_dates(kr_end, 6)
        _seed_macro(conn, "usdkrw", [(d6_kr[0], usdkrw_prior)] + [(d, usdkrw_latest) for d in d6_kr[1:]])

        d6_us = _asc_dates(us_end, 6)
        _seed_macro(conn, "us10y", [(d6_us[0], us10y_prior)] + [(d, us10y_latest) for d in d6_us[1:]])

        if wti_rows:
            _seed_macro(conn, "wti", [(d6_us[0], wti_prior)] + [(d, wti_latest) for d in d6_us[1:]])
        else:
            _seed_macro(conn, "wti", [(us_end, wti_latest)])  # 1행만 → 데이터 부족 유도

        _seed_macro(conn, "dxy", [(d6_us[0], dxy_prior)] + [(d, dxy_latest) for d in d6_us[1:]])

        # KOSPI: KR 시리즈 → kst_date 당일까지 포함. 60행 중 마지막(최신)만 편차 부여.
        d60_kr = _asc_dates(kr_end, 60)
        base = 3000.0
        vals = [base] * 60
        vals[-1] = base * kospi_latest_mult
        _seed_macro(conn, "kospi", list(zip(d60_kr, vals)))

        # SP500: US 시리즈 → kst_date-1까지만 조회됨. sp500_rows<60이면 MA60 데이터 부족.
        d_sp = _asc_dates(us_end, sp500_rows)
        _seed_macro(conn, "sp500", [(d, 4500.0) for d in d_sp])
        conn.commit()

    def test_breach_case(self, tmp_db):
        """8개 항목 전부(SP500 제외) 임계 돌파하도록 시딩 — breaches 카운트까지 검증."""
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        kst_date = "2026-09-03"
        self._seed_full(
            conn, kst_date,
            vix_latest=45.0,                              # >=40 밴드
            usdkrw_latest=1400.0, usdkrw_prior=1350.0,      # +3.70% > 2%
            us10y_latest=4.75, us10y_prior=4.50,            # +25bp > 20bp
            us2y_latest=4.60,                               # 스프레드 0.15 < 0.25 → 돌파
            wti_latest=76.0, wti_prior=80.0,                # -5.00% >= 5%
            dxy_latest=100.5, dxy_prior=99.0,               # +1.52% > 1%
            kospi_latest_mult=0.85,                         # MA60 대비 대폭 하회
            sp500_rows=60,                                  # SP500은 평탄(미돌파) 대조군
        )
        conn.close()

        result = _calc_macro_thresholds(kst_date)
        by_name = {it["name"]: it for it in result["items"]}

        assert by_name["VIX"]["breached"] is True
        assert by_name["VIX"]["note"] == "40 밴드 돌파"
        assert by_name["USD/KRW"]["breached"] is True
        assert by_name["US10Y"]["breached"] is True
        assert by_name["WTI"]["breached"] is True
        assert by_name["DXY"]["breached"] is True
        assert by_name["KOSPI_vs_MA60"]["breached"] is True
        assert by_name["SP500_vs_MA60"]["breached"] is False  # 대조군 — 평탄값은 미돌파
        assert by_name["US10Y-2Y"]["breached"] is True
        assert by_name["US10Y-2Y"]["value"] == 0.15
        assert result["breaches"] == 7

    def test_no_breach_case(self, tmp_db):
        """8개 항목 전부 임계 미돌파하도록 시딩 — breaches == 0 검증."""
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        kst_date = "2026-09-03"
        self._seed_full(
            conn, kst_date,
            vix_latest=14.0,                                # < 20 밴드
            usdkrw_latest=1360.0, usdkrw_prior=1358.0,       # +0.15%
            us10y_latest=4.55, us10y_prior=4.50,             # +5bp
            us2y_latest=4.10,                                # 스프레드 0.45 >= 0.25 → 미돌파
            wti_latest=79.0, wti_prior=80.0,                 # -1.25%
            dxy_latest=99.2, dxy_prior=99.0,                 # +0.20%
            kospi_latest_mult=1.02,                          # MA60 상회(이탈 아님)
            sp500_rows=60,
        )
        conn.close()

        result = _calc_macro_thresholds(kst_date)
        by_name = {it["name"]: it for it in result["items"]}

        for name in ("VIX", "USD/KRW", "US10Y", "WTI", "DXY", "KOSPI_vs_MA60", "SP500_vs_MA60", "US10Y-2Y"):
            assert by_name[name]["breached"] is False, f"{name} 예상외 breach"
        assert by_name["VIX"]["note"] == "정상 밴드"
        # treasury 인자 미전달(=None) → macro_daily 선물 프록시(2YY=F) 폴백 경로.
        assert by_name["US10Y-2Y"]["note"] == "정상 · 선물 프록시(2YY=F)"
        assert result["breaches"] == 0

    def test_insufficient_data_case(self, tmp_db):
        """WTI(1행)·SP500(10행<60)만 데이터 부족 유도 — 나머지는 정상 계산됨을 함께 확인."""
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        kst_date = "2026-09-03"
        self._seed_full(
            conn, kst_date,
            vix_latest=18.0,
            usdkrw_latest=1360.0, usdkrw_prior=1358.0,
            us10y_latest=4.55, us10y_prior=4.50,
            us2y_latest=4.10,     # 스프레드 0.45 → 정상 계산(부족 유도 대상 아님)
            wti_rows=False,      # → 데이터 부족
            kospi_latest_mult=1.0,
            sp500_rows=10,       # → 데이터 부족
        )
        conn.close()

        result = _calc_macro_thresholds(kst_date)
        by_name = {it["name"]: it for it in result["items"]}

        assert by_name["WTI"]["breached"] is None
        assert by_name["WTI"]["note"] == "데이터 부족"
        assert by_name["WTI"]["value"] is None
        assert by_name["SP500_vs_MA60"]["breached"] is None
        assert by_name["SP500_vs_MA60"]["note"] == "데이터 부족"
        # 나머지 항목은 정상 계산되어 있어야 함 (부분 실패가 전체를 덮지 않음)
        assert by_name["VIX"]["value"] == 18.0
        assert by_name["USD/KRW"]["breached"] is False
        assert by_name["US10Y-2Y"]["breached"] is False
        # breaches는 True인 항목만 카운트 (None은 제외)
        assert result["breaches"] == sum(1 for it in result["items"] if it["breached"] is True)

    def test_no_db_collector_returns_empty_items(self, tmp_db, monkeypatch):
        """db_collector.market_data import 실패 시에도 예외 없이 빈 items 반환."""
        import kis_api.polymarket as poly_mod
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "db_collector.market_data":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        result = _calc_macro_thresholds("2026-09-03")
        assert result["items"] == []
        assert result["breaches"] == 0

    def test_treasury_official_value_overrides_db_proxy(self, tmp_db, monkeypatch):
        """treasury 공식값(FRED) 제공 시 US10Y-2Y가 macro_daily 선물 프록시가 아니라
        treasury['spread_10y_2y']를 사용 — change는 bp 아니라 %p 소수 2자리, 나머지
        7항목/전체 개수는 그대로.

        W4 PIT 가드: treasury override는 kst_date_str이 "오늘"일 때만 적용되므로,
        이 테스트가 실제 실행 날짜와 무관하게 결정적이도록 "오늘"을 kst_date로 고정한다."""
        import kis_api.polymarket as poly_mod

        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        kst_date = "2026-09-03"

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 3, 15, 0, tzinfo=tz)
        monkeypatch.setattr(poly_mod, "datetime", _FixedDatetime)

        # us10y/us2y 프록시로는 0.45(us10y=4.55, us2y=4.10)가 나오도록 시딩 — treasury
        # 공식값(0.15)과 확실히 달라야 override 여부를 판별할 수 있다.
        self._seed_full(
            conn, kst_date,
            vix_latest=14.0,
            usdkrw_latest=1360.0, usdkrw_prior=1358.0,
            us10y_latest=4.55, us10y_prior=4.50,
            us2y_latest=4.10,
            wti_latest=79.0, wti_prior=80.0,
            dxy_latest=99.2, dxy_prior=99.0,
            kospi_latest_mult=1.02,
            sp500_rows=60,
        )
        conn.close()

        treasury = {"spread_10y_2y": 0.15, "spread_10y_2y_1w_ago": 0.55,
                    "recession_signal": "주의 (역전 임박)"}
        result = _calc_macro_thresholds(kst_date, treasury=treasury)
        by_name = {it["name"]: it for it in result["items"]}

        item = by_name["US10Y-2Y"]
        assert item["value"] == 0.15           # treasury 공식값 — DB 프록시(0.45)가 아님
        assert item["change"] == -0.4          # 0.15 - 0.55, %p 소수 2자리(bp 변환 없음)
        assert item["breached"] is True         # 0.15 < 0.25
        assert "Treasury 공식" in item["note"]
        assert "1주 -0.40%p" in item["note"]    # 단위 표기(misc d) — %p, bp 아님
        assert len(result["items"]) == 8        # 8항목 수 유지
        # 다른 7항목은 이 케이스와 무관하게 정상 계산되어 있어야 함
        assert by_name["VIX"]["value"] == 14.0

    def test_treasury_ignored_when_kst_date_str_not_today(self, tmp_db, monkeypatch):
        """W4 PIT 가드 — fetch_treasury_curve()는 항상 "현재" 시점의 최신 값만 반환하므로,
        kst_date_str이 오늘(KST)이 아니면 treasury가 유효해도 무시하고 macro_daily
        프록시로 폴백해야 한다(과거/미래 날짜에 오늘자 스프레드가 잘못 붙는 것 방지)."""
        import kis_api.polymarket as poly_mod

        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        kst_date = "2026-09-03"  # "오늘"로 고정할 09-05보다 이전 날짜

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 5, 10, 0, tzinfo=tz)  # 오늘 = 09-05 ≠ kst_date
        monkeypatch.setattr(poly_mod, "datetime", _FixedDatetime)

        self._seed_full(
            conn, kst_date,
            vix_latest=14.0,
            usdkrw_latest=1360.0, usdkrw_prior=1358.0,
            us10y_latest=4.55, us10y_prior=4.50,
            us2y_latest=4.10,      # DB 프록시 스프레드 = 0.45
            wti_latest=79.0, wti_prior=80.0,
            dxy_latest=99.2, dxy_prior=99.0,
            kospi_latest_mult=1.02,
            sp500_rows=60,
        )
        conn.close()

        # treasury 자체는 완전히 유효한 값 — 그럼에도 날짜가 "오늘"이 아니므로 무시돼야 함.
        treasury = {"spread_10y_2y": 0.15, "spread_10y_2y_1w_ago": 0.55,
                    "recession_signal": "주의 (역전 임박)"}
        result = _calc_macro_thresholds(kst_date, treasury=treasury)
        by_name = {it["name"]: it for it in result["items"]}

        item = by_name["US10Y-2Y"]
        assert item["value"] == 0.45           # DB 프록시 — treasury(0.15) 무시됨
        assert item["breached"] is False
        assert "선물 프록시(2YY=F)" in item["note"]
        assert len(result["items"]) == 8

    def test_treasury_missing_or_error_falls_back_to_db_proxy(self, tmp_db):
        """treasury가 None / error / spread_10y_2y 없음 → macro_daily 선물 프록시로 폴백
        (기존 동작 그대로), note에 출처 표기."""
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        kst_date = "2026-09-03"
        self._seed_full(
            conn, kst_date,
            vix_latest=14.0,
            usdkrw_latest=1360.0, usdkrw_prior=1358.0,
            us10y_latest=4.55, us10y_prior=4.50,
            us2y_latest=4.10,      # DB 프록시 스프레드 = 0.45
            wti_latest=79.0, wti_prior=80.0,
            dxy_latest=99.2, dxy_prior=99.0,
            kospi_latest_mult=1.02,
            sp500_rows=60,
        )
        conn.close()

        for bad_treasury in (None, {"error": "fetch failed"}, {"spread_10y_2y": None}):
            result = _calc_macro_thresholds(kst_date, treasury=bad_treasury)
            by_name = {it["name"]: it for it in result["items"]}
            item = by_name["US10Y-2Y"]
            assert item["value"] == 0.45, f"폴백 실패: {bad_treasury!r}"
            assert item["breached"] is False
            assert "선물 프록시(2YY=F)" in item["note"]
            assert len(result["items"]) == 8


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. kis_api.regime.calc_kr_regime — foreign_5d 채움 + None 폴백 + -2조 플래그
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _fake_kospi_closes(symbol, years=2):
    if symbol == "KS11":
        return [3000.0 + (i * 0.3) for i in range(300)]
    return []  # USD/KRW 등 — usdkrw_chg60 None 유지, 테스트 무관


class TestRegimeForeign5d:
    def test_foreign_5d_filled_and_no_flag(self, tmp_db, monkeypatch):
        monkeypatch.setattr(regime, "_fdr_closes", _fake_kospi_closes)
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        # 5행 합계 = -1,000,000백만원 = -10,000억원 (임계 -20,000억원 미만이라 플래그 X)
        _seed_flow(conn, "KSP", [(d, -200_000) for d in _dates_back("2026-09-03", 5)])
        conn.close()

        result = regime.calc_kr_regime()
        assert result["indicators"]["foreign_5d"] == -10000
        assert "foreign_outflow_5d" not in result["confirmations"]

    def test_foreign_5d_triggers_outflow_flag(self, tmp_db, monkeypatch):
        monkeypatch.setattr(regime, "_fdr_closes", _fake_kospi_closes)
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        # 5행 합계 = -2,500,000백만원 = -25,000억원 (임계 -20,000억원 이하 → 플래그 발동)
        _seed_flow(conn, "KSP", [(d, -500_000) for d in _dates_back("2026-09-03", 5)])
        conn.close()

        result = regime.calc_kr_regime()
        assert result["indicators"]["foreign_5d"] == -25000
        assert result["confirmations"].get("foreign_outflow_5d") is True

    def test_foreign_5d_none_when_fewer_than_5_rows(self, tmp_db, monkeypatch):
        monkeypatch.setattr(regime, "_fdr_closes", _fake_kospi_closes)
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        _seed_flow(conn, "KSP", [(d, -500_000) for d in _dates_back("2026-09-03", 3)])
        conn.close()

        result = regime.calc_kr_regime()
        assert result["indicators"]["foreign_5d"] is None
        assert "foreign_outflow_5d" not in result["confirmations"]

    def test_foreign_5d_none_on_db_error(self, tmp_db, monkeypatch):
        """DB 조회 자체가 실패해도 calc_kr_regime은 예외를 내지 않고 None 폴백."""
        monkeypatch.setattr(regime, "_fdr_closes", _fake_kospi_closes)

        import db_collector.market_data as md_mod

        def _boom():
            raise RuntimeError("simulated db failure")

        monkeypatch.setattr(md_mod, "_get_db", _boom)

        result = regime.calc_kr_regime()
        assert result["indicators"]["foreign_5d"] is None
        assert result["regime_en"] in ("offensive", "neutral", "crisis")  # 판정 자체는 정상 진행

    def test_foreign_5d_none_when_stale(self, tmp_db, monkeypatch):
        """W6 — 5행이 다 있어도 최신 date가 오늘-7일보다 오래되면(잡 중단 등) 스테일
        값을 쓰지 않고 foreign_5d=None 유지 (확인 플래그도 미설정). 실제 wall-clock
        "오늘"에 상관없이 항상 오래된 것으로 남도록 2020년 날짜로 시딩."""
        monkeypatch.setattr(regime, "_fdr_closes", _fake_kospi_closes)
        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        _seed_flow(conn, "KSP", [(d, -500_000) for d in
                                  ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04", "2020-01-05"]])
        conn.close()

        result = regime.calc_kr_regime()
        assert result["indicators"]["foreign_5d"] is None
        assert "foreign_outflow_5d" not in result["confirmations"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. db_collector._BACKING 등록 (W7) — 순환 import 없이 로드되는지는
#    이 파일이 애초에 db_collector/kis_api/main_pkg를 모두 import해서 collection
#    되는 것 자체가 회귀 방지 스모크 테스트다.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestDbCollectorBacking:
    def test_market_data_registered_in_backing(self):
        assert md in db_collector._BACKING
        assert "market_data" in [m.__name__.rsplit(".", 1)[-1] for m in db_collector._BACKING]

    def test_setattr_on_db_collector_propagates_to_market_data(self, monkeypatch):
        """market_data 전용 심볼(MACRO_SERIES)도 db_collector.X 패치가 전파돼야 한다 —
        _BACKING에 market_data가 등록되기 전에는 이 이름이 여기까지 전파되지 않았다(W7).
        monkeypatch 자체가 db_collector 경유로 setattr하므로 teardown도 대칭적으로 전파된다."""
        fake_series = {"fake": {"source": "fdr", "symbol": "X", "group": "KR"}}
        monkeypatch.setattr(db_collector, "MACRO_SERIES", fake_series)
        assert md.MACRO_SERIES == fake_series


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. main_pkg.jobs.market_data.daily_market_data_collect — W8 휴장일 가드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestDailyMarketDataCollectHolidayGuard:
    """`_is_kr_trading_day`는 "%Y%m%d" 문자열만 받는다(date 객체·대시(-) 포맷은
    strptime 실패 시 fail-open으로 True 반환 — 가드 무력화). 잡 코드가 반드시
    strftime("%Y%m%d")로 호출하는지까지 이 테스트로 확인한다."""

    def test_weekday_holiday_skips_flow_but_runs_macro(self, tmp_path, monkeypatch):
        # SILENT_FAILURE_LOG를 tmp로 돌려 실제 data/silent_failure_log.json을 건드리지 않는다.
        import main_pkg._ctx as ctx_mod
        monkeypatch.setattr(ctx_mod, "SILENT_FAILURE_LOG", str(tmp_path / "silent_failure_log.json"))

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                # 2026-09-24 = 목요일(평일)이면서 추석 연휴로 _KR_MARKET_HOLIDAYS 등록된 휴장일.
                return datetime(2026, 9, 24, 10, 0, tzinfo=tz)

        monkeypatch.setattr(job_mod, "datetime", _FixedDatetime)

        macro_mock = AsyncMock(return_value={"vix": 1})
        flow_mock = AsyncMock(return_value={"KSP": 1})
        token_mock = AsyncMock(return_value="tok")
        monkeypatch.setattr(job_mod, "collect_macro_daily", macro_mock)
        monkeypatch.setattr(job_mod, "collect_market_flow_daily", flow_mock)
        monkeypatch.setattr(job_mod, "get_kis_token", token_mock)

        asyncio.run(job_mod.daily_market_data_collect(None))

        macro_mock.assert_called_once()
        flow_mock.assert_not_called()
        token_mock.assert_not_called()

    def test_weekday_trading_day_runs_both(self, tmp_path, monkeypatch):
        """대조군 — 평일이면서 정상 거래일(휴장일 아님)이면 flow도 수행."""
        import main_pkg._ctx as ctx_mod
        monkeypatch.setattr(ctx_mod, "SILENT_FAILURE_LOG", str(tmp_path / "silent_failure_log.json"))

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 3, 10, 0, tzinfo=tz)  # 목요일, 정상 거래일

        monkeypatch.setattr(job_mod, "datetime", _FixedDatetime)

        macro_mock = AsyncMock(return_value={"vix": 1})
        flow_mock = AsyncMock(return_value={"KSP": 1})
        token_mock = AsyncMock(return_value="tok")
        monkeypatch.setattr(job_mod, "collect_macro_daily", macro_mock)
        monkeypatch.setattr(job_mod, "collect_market_flow_daily", flow_mock)
        monkeypatch.setattr(job_mod, "get_kis_token", token_mock)

        asyncio.run(job_mod.daily_market_data_collect(None))

        macro_mock.assert_called_once()
        flow_mock.assert_called_once()
        token_mock.assert_called_once()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. kis_api.kr_stock._fetch_market_investor_flow_range — 응답 파싱 + 청크 분기
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _flow_row(date_str, frgn=1000, orgn=-500, prsn=200):
    return {
        "stck_bsop_date": date_str,
        "frgn_ntby_tr_pbmn": str(frgn),
        "orgn_ntby_tr_pbmn": str(orgn),
        "prsn_ntby_tr_pbmn": str(prsn),
    }


def _gen_desc_rows(end_yyyymmdd, n):
    end_dt = datetime.strptime(end_yyyymmdd, "%Y%m%d")
    return [_flow_row((end_dt - timedelta(days=i)).strftime("%Y%m%d")) for i in range(n)]


class TestFetchMarketInvestorFlowRange:
    def test_single_chunk_parses_and_filters_range(self, monkeypatch):
        """< 300행 응답 → 1회 호출로 종료, start~end 범위 필터 + 오름차순 정렬."""
        rows = [_flow_row(d) for d in
                ["20260903", "20260902", "20260901", "20260828", "20260820"]]  # 20260820은 start 밖

        call_count = {"n": 0}

        async def fake_kis_get(session, path, tr_id, token, params):
            call_count["n"] += 1
            assert params["fid_input_date_1"] == "20260903"
            return 200, {"rt_cd": "0", "output": rows}

        monkeypatch.setattr(kr_stock, "_kis_get", fake_kis_get)

        result = asyncio.run(kr_stock._fetch_market_investor_flow_range(
            "tok", "KSP", "20260821", "20260903"))

        assert call_count["n"] == 1
        dates = [r["date"] for r in result]
        assert dates == sorted(dates)  # 오름차순
        assert "20260820" not in dates  # start(20260821) 미만 제외
        assert dates[-1] == "20260903"
        assert result[-1]["frgn"] == 1000 and result[-1]["orgn"] == -500 and result[-1]["prsn"] == 200

    def test_multi_chunk_when_300_rows_returned(self, monkeypatch):
        """정확히 300행(가득 찬 페이지) 응답 시 oldest-1일로 재요청(청크) 확인."""
        calls = []

        async def fake_kis_get(session, path, tr_id, token, params):
            end = params["fid_input_date_1"]
            calls.append(end)
            if len(calls) == 1:
                rows = _gen_desc_rows(end, 300)  # 가득 참 → 다음 청크 유발
            else:
                rows = _gen_desc_rows(end, 50)  # < 300 → 종료
            return 200, {"rt_cd": "0", "output": rows}

        monkeypatch.setattr(kr_stock, "_kis_get", fake_kis_get)
        monkeypatch.setattr(kr_stock.asyncio, "sleep", AsyncMock(return_value=None))

        end = "20260903"
        start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=320)).strftime("%Y%m%d")
        result = asyncio.run(kr_stock._fetch_market_investor_flow_range("tok", "KSP", start, end))

        assert len(calls) == 2  # 청크 2회 확인
        assert calls[1] < calls[0]  # 두번째 호출의 date_1이 첫 청크 oldest-1일로 당겨짐
        dates = [r["date"] for r in result]
        assert dates == sorted(dates)
        assert len(dates) == len(set(dates))  # 청크 경계 중복 없음
        assert all(start <= d <= end for d in dates)

    def test_empty_output_returns_empty_list(self, monkeypatch):
        async def fake_kis_get(session, path, tr_id, token, params):
            return 200, {"rt_cd": "0", "output": []}

        monkeypatch.setattr(kr_stock, "_kis_get", fake_kis_get)
        result = asyncio.run(kr_stock._fetch_market_investor_flow_range(
            "tok", "KSP", "20260801", "20260903"))
        assert result == []

    def test_error_rt_cd_returns_empty_list(self, monkeypatch):
        async def fake_kis_get(session, path, tr_id, token, params):
            return 200, {"rt_cd": "1", "msg1": "오류"}

        monkeypatch.setattr(kr_stock, "_kis_get", fake_kis_get)
        result = asyncio.run(kr_stock._fetch_market_investor_flow_range(
            "tok", "KSP", "20260801", "20260903"))
        assert result == []

    def test_malformed_rows_skipped_without_crash(self, monkeypatch):
        """stck_bsop_date 누락/형식오류 행은 스킵, 나머지는 정상 파싱."""
        rows = [
            {"stck_bsop_date": "20260903", "frgn_ntby_tr_pbmn": "100",
             "orgn_ntby_tr_pbmn": "-50", "prsn_ntby_tr_pbmn": "10"},
            {"stck_bsop_date": "", "frgn_ntby_tr_pbmn": "999"},  # 빈 날짜 → 스킵
            {"frgn_ntby_tr_pbmn": "999"},  # 날짜 필드 자체 없음 → 스킵
        ]

        async def fake_kis_get(session, path, tr_id, token, params):
            return 200, {"rt_cd": "0", "output": rows}

        monkeypatch.setattr(kr_stock, "_kis_get", fake_kis_get)
        result = asyncio.run(kr_stock._fetch_market_investor_flow_range(
            "tok", "KSP", "20260801", "20260903"))
        assert len(result) == 1
        assert result[0]["date"] == "20260903"
        assert result[0]["frgn"] == 100

    def test_iscd_matches_market_ksp_and_ksq(self, monkeypatch):
        """W4 회귀 방지 — fid_input_iscd(및 _2)가 fid_input_iscd_1과 같은 시장으로
        일치해야 한다 (KOSPI="0001", 코스닥="1001"). 불일치 시 rt_cd="0"인 채로
        frgn/orgn/prsn만 조용히 0이 되는 침묵-0 함정(`_MARKET_IDX_CODE` 주석 참고)."""
        captured = []

        async def fake_kis_get(session, path, tr_id, token, params):
            captured.append(dict(params))
            return 200, {"rt_cd": "0", "output": []}

        monkeypatch.setattr(kr_stock, "_kis_get", fake_kis_get)

        asyncio.run(kr_stock._fetch_market_investor_flow_range("tok", "KSP", "20260801", "20260903"))
        asyncio.run(kr_stock._fetch_market_investor_flow_range("tok", "KSQ", "20260801", "20260903"))

        ksp_params, ksq_params = captured
        assert ksp_params["fid_input_iscd"] == "0001"
        assert ksp_params["fid_input_iscd_1"] == "KSP"
        assert ksp_params["fid_input_iscd_2"] == "0001"
        assert ksq_params["fid_input_iscd"] == "1001"
        assert ksq_params["fid_input_iscd_1"] == "KSQ"
        assert ksq_params["fid_input_iscd_2"] == "1001"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. db_collector.market_data.collect_market_flow_daily — B1/W4 저장 가드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestCollectMarketFlowDailyGuards:
    def test_empty_range_result_does_not_fallback_to_wallclock_row(self, tmp_db, monkeypatch):
        """B1 — 범위조회가 빈 결과를 반환해도(예: 휴장일) 벽시계 오늘 날짜로 폴백
        저장하지 않는다 (예전 `_fetch_market_investor_flow` 단건 폴백 제거 확인)."""
        async def fake_range(token, market, start, end):
            return []

        monkeypatch.setattr(kr_stock, "_fetch_market_investor_flow_range", fake_range)
        monkeypatch.setattr(md.asyncio, "sleep", AsyncMock(return_value=None))

        result = asyncio.run(md.collect_market_flow_daily("tok"))

        assert result == {"KSP": 0, "KSQ": 0}
        conn = md._get_db()
        cnt = conn.execute("SELECT COUNT(*) c FROM market_flow_daily").fetchone()["c"]
        conn.close()
        assert cnt == 0  # 벽시계 오늘 날짜 행이 생기지 않았음

    def test_all_zero_chunk_not_saved_and_flagged_error(self, tmp_db, monkeypatch):
        """W4 — 조회된 rows가 있는데 전부 frgn=orgn=prsn=0이면(침묵-0 함정 재발 신호)
        저장하지 않고 {"error": "all_zero", ...}로 표시한다. 정상 시장(KSQ)은 그대로 저장."""
        async def fake_range(token, market, start, end):
            if market == "KSP":
                return [
                    {"date": "20260901", "frgn": 0, "orgn": 0, "prsn": 0},
                    {"date": "20260902", "frgn": 0, "orgn": 0, "prsn": 0},
                ]
            return [{"date": "20260901", "frgn": 100, "orgn": -50, "prsn": -50}]

        monkeypatch.setattr(kr_stock, "_fetch_market_investor_flow_range", fake_range)
        monkeypatch.setattr(md.asyncio, "sleep", AsyncMock(return_value=None))

        result = asyncio.run(md.collect_market_flow_daily("tok"))

        assert result["KSP"] == {"error": "all_zero", "market": "KSP"}
        assert result["KSQ"] == 1

        conn = md._get_db()
        ksp_cnt = conn.execute("SELECT COUNT(*) c FROM market_flow_daily WHERE market='KSP'").fetchone()["c"]
        ksq_cnt = conn.execute("SELECT COUNT(*) c FROM market_flow_daily WHERE market='KSQ'").fetchone()["c"]
        conn.close()
        assert ksp_cnt == 0  # all_zero 청크는 저장되지 않음
        assert ksq_cnt == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. W9 — "latest >= today면 조기 종료" 제거 확인 (같은 날 재실행이 no-op 되던 결함)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestSameDayRerunRefetches:
    """오늘 날짜 행이 이미 있어도(예: 장중 잠정치 수동 백필) 재실행하면 fetch가 다시
    호출되고 INSERT OR REPLACE로 오늘 행이 새 값으로 덮어써져야 한다 — 예전에는
    "latest >= today"면 조기 종료해 이 재조회 자체가 스킵됐다(라이브 결함 재현)."""

    def test_macro_same_day_rerun_refetches_and_overwrites(self, tmp_db, monkeypatch):
        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 4, 19, 8, tzinfo=tz)
        monkeypatch.setattr(md, "datetime", _FixedDatetime)

        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        # 오늘(2026-09-04) 이미 잠정치가 들어가 있는 상태로 시딩.
        _seed_macro(conn, "vix", [("2026-09-04", 15.0)])
        conn.close()

        calls = []

        def fake_fetch(meta, start, end):
            calls.append((start, end))
            return [("2026-09-04", 99.0)]

        monkeypatch.setattr(md, "_fetch_series_closes", fake_fetch)

        result = asyncio.run(md.collect_macro_daily())

        # 조기 종료 없이 시리즈 전부(11개) fetch가 호출됨 — "latest>=today"로 스킵된
        # 시리즈가 하나도 없어야 한다.
        assert len(calls) == len(md.MACRO_SERIES)
        assert all(v == 1 for v in result.values())

        conn = md._get_db()
        row = conn.execute(
            "SELECT value FROM macro_daily WHERE series='vix' AND date='2026-09-04'"
        ).fetchone()
        cnt = conn.execute("SELECT COUNT(*) c FROM macro_daily WHERE series='vix'").fetchone()["c"]
        conn.close()
        assert row["value"] == 99.0  # 잠정치(15.0)가 새 값(99.0)으로 덮어써짐
        assert cnt == 1  # 중복 행 없이 upsert

    def test_flow_same_day_rerun_refetches_and_overwrites(self, tmp_db, monkeypatch):
        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 4, 19, 8, tzinfo=tz)
        monkeypatch.setattr(md, "datetime", _FixedDatetime)

        conn = md._get_db()
        md._ensure_market_data_tables(conn)
        # 오늘(2026-09-04) 이미 잠정 수급 행이 들어가 있는 상태로 시딩 (KSP/KSQ 둘 다).
        _seed_flow(conn, "KSP", [("2026-09-04", 1000)])
        _seed_flow(conn, "KSQ", [("2026-09-04", 1000)])
        conn.close()

        calls = []

        async def fake_range(token, market, start, end):
            calls.append(market)
            return [{"date": "20260904", "frgn": 555, "orgn": -111, "prsn": -444}]

        monkeypatch.setattr(kr_stock, "_fetch_market_investor_flow_range", fake_range)
        monkeypatch.setattr(md.asyncio, "sleep", AsyncMock(return_value=None))

        result = asyncio.run(md.collect_market_flow_daily("tok"))

        # 조기 종료 없이 KSP/KSQ 둘 다 재조회됨.
        assert sorted(calls) == ["KSP", "KSQ"]
        assert result == {"KSP": 1, "KSQ": 1}

        conn = md._get_db()
        ksp_row = conn.execute(
            "SELECT frgn_net FROM market_flow_daily WHERE date='2026-09-04' AND market='KSP'"
        ).fetchone()
        ksp_cnt = conn.execute(
            "SELECT COUNT(*) c FROM market_flow_daily WHERE market='KSP'"
        ).fetchone()["c"]
        conn.close()
        assert ksp_row["frgn_net"] == 555  # 잠정치(1000)가 새 값(555)으로 덮어써짐
        assert ksp_cnt == 1  # 중복 행 없이 upsert
