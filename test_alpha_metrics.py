"""F/M/FCF Phase3 — 알파 메트릭 단위 테스트.

In-memory SQLite DB에 가상 재무 데이터를 넣고
_compute_fscore / _compute_mscore / _compute_fcf_metrics 를 검증.

실행:
    /Users/kreuzer/stock-bot/venv/bin/python -m pytest test_alpha_metrics.py -v

주의: 실 DB(stock.db) 접근하지 않음. 전부 :memory: 또는 임시 파일 기반.
"""
import os
import sys
import sqlite3

import pytest

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

# 주의: _get_db()를 호출하면 실제 stock.db를 열기 때문에,
# 테스트에서는 절대 _get_db() 호출하지 말고 수동으로 in-memory DB 구성.
from db_collector import (  # noqa: E402
    _compute_fscore,
    _compute_mscore,
    _compute_fcf_metrics,
    _update_alpha_metrics,
    _ensure_alpha_columns,
)
import db_collector as _dbc  # update_all_alpha_metrics 전용 monkeypatch


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼 — 최소한의 스키마 생성
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_FQ_SCHEMA = """
CREATE TABLE financial_quarterly (
    symbol TEXT NOT NULL,
    report_period TEXT NOT NULL,
    revenue REAL, cost_of_sales REAL, gross_profit REAL,
    operating_profit REAL, op_profit REAL, net_income REAL,
    current_assets REAL, fixed_assets REAL, total_assets REAL,
    current_liab REAL, fixed_liab REAL, total_liab REAL,
    capital REAL, total_equity REAL,
    cfo INTEGER, capex INTEGER, fcf INTEGER,
    depreciation INTEGER, sga INTEGER,
    receivables INTEGER, inventory INTEGER,
    shares_out INTEGER,
    net_income_parent INTEGER, equity_parent INTEGER,
    fs_source TEXT,
    collected_at TEXT DEFAULT '',
    PRIMARY KEY (symbol, report_period)
);
"""
_DS_SCHEMA = """
CREATE TABLE daily_snapshot (
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    market_cap INTEGER DEFAULT 0,
    PRIMARY KEY (trade_date, symbol)
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_FQ_SCHEMA + _DS_SCHEMA)
    return conn


def _insert_fq(conn, ticker, period, **kwargs):
    """financial_quarterly INSERT 헬퍼.

    단위 규칙(실제 DB와 동일하게 맞춤):
      * 모든 money 필드(revenue, operating_profit, net_income, net_income_parent,
        cfo, capex, fcf, depreciation, sga, receivables, inventory,
        total_assets, equity_parent 등) → "억원" 단위 (DART 파서가 수집 시 //1e8 처리)
      * shares_out → 주 (발행주식수)
    """
    defaults = {
        "revenue": None, "cost_of_sales": None, "gross_profit": None,
        "operating_profit": None, "op_profit": None, "net_income": None,
        "current_assets": None, "fixed_assets": None, "total_assets": None,
        "current_liab": None, "fixed_liab": None, "total_liab": None,
        "capital": None, "total_equity": None,
        "cfo": None, "capex": None, "fcf": None,
        "depreciation": None, "sga": None,
        "receivables": None, "inventory": None, "shares_out": None,
        "net_income_parent": None, "equity_parent": None,
        "fs_source": "CFS",
    }
    defaults.update(kwargs)
    cols = ", ".join(["symbol", "report_period"] + list(defaults.keys()))
    placeholders = ", ".join(["?"] * (2 + len(defaults)))
    conn.execute(
        f"INSERT INTO financial_quarterly ({cols}) VALUES ({placeholders})",
        [ticker, period] + list(defaults.values()),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 데이터 픽스처 — Samsung-like 연간 (Q4 = 연간 누적)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 삼성전자 2024 유사 (축약 수치, 억원):
#   매출 3,000,000, 매출원가 2,000,000, 매출총이익 1,000,000,
#   영업이익 320,000, 순이익 340,000, CFO 650,000억 = 65,000,000,000,000원,
#   CapEx 540,000억, FCF 110,000억, 총자산 5,140,000, 유동자산 2,130,000,
#   유동부채 860,000, 총부채 1,170,000, 총자본 3,970,000
# YoY 개선: ROA 상승, CFO > NI, 부채비율 감소, 유동비율 증가, shares 동결,
# GPM 증가, 자산회전율 증가 → F-Score 7~9점 기대.

@pytest.fixture
def samsung_like_conn():
    conn = _make_conn()
    tk = "005930"

    # 전년 Q4 (202312) — prev TTM (Q4=그대로). 모든 money: 억원.
    # 삼성전자 2023: 매출 258조, 영업익 6.6조, 순익 15조 (축약 수치)
    _insert_fq(
        conn, tk, "202312",
        revenue=2_600_000, cost_of_sales=2_050_000, gross_profit=550_000,
        operating_profit=65_000, net_income=150_000,
        total_assets=4_800_000, fixed_assets=2_850_000, current_assets=1_950_000,
        current_liab=820_000, fixed_liab=430_000, total_liab=1_250_000,
        total_equity=3_550_000,
        cfo=450_000, capex=530_000, fcf=-80_000,
        depreciation=300_000, sga=420_000,
        receivables=520_000, inventory=400_000,
        shares_out=5_919_637_922,
        net_income_parent=145_000, equity_parent=3_400_000,
    )

    # 현재 Q4 (202412) — cur TTM (Q4=그대로, 모든 지표 개선 케이스)
    # 삼성전자 2024 유사: 매출 300조, 영업익 32조, 순익 34조
    _insert_fq(
        conn, tk, "202412",
        revenue=3_000_000, cost_of_sales=2_000_000, gross_profit=1_000_000,
        operating_profit=320_000, net_income=340_000,
        total_assets=5_140_000, fixed_assets=3_010_000, current_assets=2_130_000,
        current_liab=860_000, fixed_liab=310_000, total_liab=1_170_000,
        total_equity=3_970_000,
        cfo=650_000, capex=540_000, fcf=110_000,
        depreciation=350_000, sga=480_000,
        receivables=560_000, inventory=420_000,
        shares_out=5_919_637_922,  # 주식수 동결
        net_income_parent=335_000, equity_parent=3_800_000,
    )

    # daily_snapshot row (market_cap=5,000,000억 = 500조원)
    conn.execute(
        "INSERT INTO daily_snapshot (trade_date, symbol, market_cap) VALUES (?, ?, ?)",
        ("20260416", tk, 5_000_000),
    )
    conn.commit()
    return conn


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# F-Score 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFScore:
    def test_healthy_firm_scores_high(self, samsung_like_conn):
        """지표 전반 개선 케이스: 7점 이상 기대."""
        res = _compute_fscore(samsung_like_conn, "005930", "202412")
        assert res["score"] is not None
        assert res["score"] >= 7, (
            f"score={res['score']}, details={res['details']}"
        )
        # 핵심 지표는 True여야 함
        assert res["details"]["roa_pos"] is True
        assert res["details"]["cfo_pos"] is True
        assert res["details"]["delta_roa_pos"] is True
        assert res["details"]["cfo_gt_ni"] is True
        assert res["details"]["shares_not_increased"] is True

    def test_holdco_skipped(self, samsung_like_conn):
        """순수 지주사(OFS_HOLDCO)는 계산 스킵."""
        # 기존 202412 row의 fs_source 변경
        samsung_like_conn.execute(
            "UPDATE financial_quarterly SET fs_source='OFS_HOLDCO' "
            "WHERE symbol=? AND report_period=?",
            ("005930", "202412"),
        )
        res = _compute_fscore(samsung_like_conn, "005930", "202412")
        assert res["score"] is None
        assert res["skipped"] == "holdco"

    def test_missing_prev_data_not_complete(self):
        """YoY 데이터 없으면 is_complete=False."""
        conn = _make_conn()
        # 현재 분기만 있음 (prev 없음). 모든 money: 억원.
        _insert_fq(
            conn, "999999", "202412",
            revenue=1000, operating_profit=100, net_income=80,
            total_assets=5000, current_assets=2000,
            current_liab=800, total_liab=1500, total_equity=3500,
            cfo=100, shares_out=1_000_000, net_income_parent=80,
        )
        conn.commit()
        res = _compute_fscore(conn, "999999", "202412")
        # score는 계산되지만 (일부 지표만), is_complete=False
        assert res["is_complete"] is False

    def test_net_income_parent_fallback(self):
        """net_income_parent None이면 net_income으로 fallback."""
        conn = _make_conn()
        # prev Q4
        _insert_fq(
            conn, "TEST01", "202312",
            revenue=1000, gross_profit=300, cost_of_sales=700,
            operating_profit=50, net_income=40,
            total_assets=5000, fixed_assets=3000, current_assets=2000,
            current_liab=800, total_liab=1500, total_equity=3500,
            cfo=30, shares_out=1_000_000,
            net_income_parent=None,  # 명시적 None
        )
        # cur Q4
        _insert_fq(
            conn, "TEST01", "202412",
            revenue=1100, gross_profit=400, cost_of_sales=700,
            operating_profit=80, net_income=70,
            total_assets=5200, fixed_assets=3100, current_assets=2100,
            current_liab=750, total_liab=1400, total_equity=3800,
            cfo=50, shares_out=1_000_000,
            net_income_parent=None,
        )
        conn.commit()
        res = _compute_fscore(conn, "TEST01", "202412")
        # net_income(70)로 fallback → roa_pos 계산 가능
        assert res["details"]["roa_pos"] is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# M-Score 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestMScore:
    def test_healthy_firm_low_manipulation(self, samsung_like_conn):
        """정상 기업 — M이 합리적 범위 (-1 ~ -4 사이)."""
        res = _compute_mscore(samsung_like_conn, "005930", "202412")
        assert res["is_complete"] is True
        assert res["mscore"] is not None
        # 실무 범위: M은 보통 -4 ~ +2 사이. 이상치 아니면 OK
        assert -10 < res["mscore"] < 5, f"mscore={res['mscore']}, vars={res['variables']}"
        assert res["manipulation_risk"] in ("low", "moderate", "high")

    def test_variables_all_computed(self, samsung_like_conn):
        """8개 변수 모두 계산돼야 함."""
        res = _compute_mscore(samsung_like_conn, "005930", "202412")
        for k in ("DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "LVGI", "TATA"):
            assert res["variables"][k] is not None, (
                f"{k} 계산 실패: {res['variables']}"
            )

    def test_holdco_skipped(self, samsung_like_conn):
        samsung_like_conn.execute(
            "UPDATE financial_quarterly SET fs_source='OFS_HOLDCO' "
            "WHERE symbol=? AND report_period=?",
            ("005930", "202412"),
        )
        res = _compute_mscore(samsung_like_conn, "005930", "202412")
        assert res["mscore"] is None
        assert res["skipped"] == "holdco"

    def test_insufficient_data_incomplete(self):
        """prev YoY 없으면 is_complete=False."""
        conn = _make_conn()
        _insert_fq(
            conn, "EMPTY1", "202412",
            revenue=1000, total_assets=5000,
            current_assets=2000, current_liab=800, total_liab=1500,
        )
        conn.commit()
        res = _compute_mscore(conn, "EMPTY1", "202412")
        assert res["is_complete"] is False
        assert res["mscore"] is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# FCF 메트릭 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFCFMetrics:
    def test_basic_calculation(self, samsung_like_conn):
        """FCF/총자산, FCF/EV, FCF/순이익 모두 계산."""
        res = _compute_fcf_metrics(
            samsung_like_conn, "005930", "202412", market_cap=5_000_000
        )
        assert res["is_complete"] is True
        # fcf = 11조원 = 110,000억원
        assert res["fcf_ttm"] == pytest.approx(110_000, rel=0.01)
        # FCF / 총자산 = 110,000 / 5,140,000 ≈ 2.14%
        assert 1.5 < res["fcf_to_assets"] < 3.0, res["fcf_to_assets"]
        # FCF / EV = 110,000 / (5,000,000 + 1,170,000) ≈ 1.78%
        assert 1.2 < res["fcf_yield_ev"] < 2.5, res["fcf_yield_ev"]
        # FCF / 순이익 = 110,000 / 340,000 ≈ 32%
        assert 20 < res["fcf_conversion"] < 50, res["fcf_conversion"]

    def test_no_market_cap(self, samsung_like_conn):
        """market_cap 없으면 fcf_yield_ev는 None."""
        res = _compute_fcf_metrics(
            samsung_like_conn, "005930", "202412", market_cap=None
        )
        assert res["fcf_to_assets"] is not None
        assert res["fcf_yield_ev"] is None
        assert res["fcf_conversion"] is not None
        assert res["is_complete"] is False  # 3개 중 1개 빠짐

    def test_negative_net_income_skips_conversion(self):
        """순이익 음수면 fcf_conversion=None. 단위: 억원."""
        conn = _make_conn()
        _insert_fq(
            conn, "LOSS1", "202412",
            revenue=1000, net_income=-50, net_income_parent=-50,
            total_assets=5000, total_liab=1500,
            current_assets=2000, current_liab=800,
            fcf=10,  # 10억원
        )
        conn.commit()
        res = _compute_fcf_metrics(conn, "LOSS1", "202412", market_cap=1000)
        assert res["fcf_ttm"] == pytest.approx(10, rel=0.01)
        assert res["fcf_to_assets"] is not None
        assert res["fcf_conversion"] is None  # 순이익 < 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 통합 _update_alpha_metrics 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestUpdateAlphaMetrics:
    def test_upsert_daily_snapshot(self, samsung_like_conn):
        """daily_snapshot 에 5개 컬럼 UPDATE 성공."""
        ok = _update_alpha_metrics(
            samsung_like_conn, "005930", "202412",
            market_cap=5_000_000, trade_date="20260416"
        )
        assert ok is True

        row = samsung_like_conn.execute(
            "SELECT fscore, mscore, fcf_to_assets, fcf_yield_ev, fcf_conversion "
            "FROM daily_snapshot WHERE trade_date=? AND symbol=?",
            ("20260416", "005930"),
        ).fetchone()
        assert row["fscore"] is not None
        assert row["fscore"] >= 7
        assert row["mscore"] is not None
        assert row["fcf_to_assets"] is not None
        assert row["fcf_yield_ev"] is not None
        assert row["fcf_conversion"] is not None

    def test_alter_table_idempotent(self):
        """_ensure_alpha_columns 는 재실행해도 에러 없음."""
        conn = _make_conn()
        _ensure_alpha_columns(conn)
        _ensure_alpha_columns(conn)  # 2회 호출해도 OK
        # 컬럼 존재 확인
        cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_snapshot)").fetchall()]
        for c in ("fscore", "mscore", "fcf_to_assets", "fcf_yield_ev", "fcf_conversion"):
            assert c in cols, f"{c} 누락"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 배치 update_all_alpha_metrics 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestUpdateAllAlphaMetrics:
    def test_batch_updates_multiple_tickers(self, monkeypatch):
        """3종목 중 최소 2종목 이상 알파 메트릭 채워져야 함."""
        conn = _make_conn()

        # 종목 A: samsung-like 풀데이터 (prev + cur) — 모든 지표 계산 성공 기대
        _insert_fq(
            conn, "AAAA01", "202312",
            revenue=2_600_000, cost_of_sales=2_050_000, gross_profit=550_000,
            operating_profit=65_000, net_income=150_000,
            total_assets=4_800_000, fixed_assets=2_850_000, current_assets=1_950_000,
            current_liab=820_000, fixed_liab=430_000, total_liab=1_250_000,
            total_equity=3_550_000,
            cfo=450_000, capex=530_000, fcf=-80_000,
            depreciation=300_000, sga=420_000,
            receivables=520_000, inventory=400_000,
            shares_out=5_919_637_922,
            net_income_parent=145_000, equity_parent=3_400_000,
        )
        _insert_fq(
            conn, "AAAA01", "202412",
            revenue=3_000_000, cost_of_sales=2_000_000, gross_profit=1_000_000,
            operating_profit=320_000, net_income=340_000,
            total_assets=5_140_000, fixed_assets=3_010_000, current_assets=2_130_000,
            current_liab=860_000, fixed_liab=310_000, total_liab=1_170_000,
            total_equity=3_970_000,
            cfo=650_000, capex=540_000, fcf=110_000,
            depreciation=350_000, sga=480_000,
            receivables=560_000, inventory=420_000,
            shares_out=5_919_637_922,
            net_income_parent=335_000, equity_parent=3_800_000,
        )

        # 종목 B: 최소 데이터만 (prev 없음) — F-Score 일부만 + FCF 일부
        _insert_fq(
            conn, "BBBB02", "202412",
            revenue=1000, operating_profit=80, net_income=70,
            total_assets=5000, current_assets=2000,
            current_liab=800, total_liab=1500, total_equity=3500,
            cfo=80, capex=30, fcf=50,
            shares_out=1_000_000, net_income_parent=70,
        )

        # 종목 C: OFS_HOLDCO → F/M 스킵, FCF만 계산 가능
        _insert_fq(
            conn, "CCCC03", "202412",
            revenue=500, operating_profit=40, net_income=30, net_income_parent=30,
            total_assets=3000, current_assets=1000,
            current_liab=400, total_liab=800, total_equity=2200,
            cfo=40, capex=10, fcf=30,
            shares_out=500_000, fs_source="OFS_HOLDCO",
        )

        # daily_snapshot rows
        for tk, mcap in (("AAAA01", 5_000_000), ("BBBB02", 1500), ("CCCC03", 1000)):
            conn.execute(
                "INSERT INTO daily_snapshot (trade_date, symbol, market_cap) "
                "VALUES (?, ?, ?)",
                ("20260416", tk, mcap),
            )
        conn.commit()

        # _get_db() 가 모듈 레벨에서 stock.db를 열어버리므로 in-memory conn 주입.
        # sqlite3.Connection.close 는 read-only 속성이라 직접 패치 불가 →
        # 대리 래퍼로 우회 (모든 메서드 위임, close 만 no-op).
        class _ConnProxy:
            def __init__(self, real): self._real = real
            def __getattr__(self, name): return getattr(self._real, name)
            def close(self): pass  # finally: conn.close() 무력화
        proxy = _ConnProxy(conn)

        def _fake_get_db():
            return proxy

        monkeypatch.setattr(_dbc, "_get_db", _fake_get_db)

        res = _dbc.update_all_alpha_metrics(
            end_period="202412", trade_date="20260416"
        )

        assert res["tickers"] == 3, res
        # 3개 전부 대상. 최소 2개 이상 UPDATE 성공.
        assert res["success"] >= 2, res
        # AAAA01 은 풀데이터 → F + M + FCF 전부 계산되어야 함
        assert res["fscore_filled"] >= 1
        assert res["mscore_filled"] >= 1
        assert res["fcf_filled"] >= 2  # AAAA01 + BBBB02 (+ CCCC03)
        # 반환 메타
        assert res["end_period"] == "202412"
        assert res["trade_date"] == "20260416"
        assert "duration_sec" in res

        # AAAA01 실제 값 확인
        row = conn.execute(
            "SELECT fscore, mscore, fcf_to_assets, fcf_yield_ev, fcf_conversion "
            "FROM daily_snapshot WHERE trade_date=? AND symbol=?",
            ("20260416", "AAAA01"),
        ).fetchone()
        assert row["fscore"] is not None and row["fscore"] >= 7
        assert row["mscore"] is not None
        assert row["fcf_to_assets"] is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
