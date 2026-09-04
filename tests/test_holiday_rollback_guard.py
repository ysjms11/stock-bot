"""tests/test_holiday_rollback_guard.py — 미등록 휴장일 자가롤백 가드 (2026-09, B1/W1/W2 리뷰 반영).

커버리지:
- db_collector.collect._detect_holiday_duplicate: 순수 read 판정 (최소행 가드 tot>=100,
  W2 prev 90%커버리지 탐색, zero_ratio 사전계산). DB를 쓰지 않는다.
- db_collector.collect._kis_confirms_trading_day: W1 — KIS 캔들로 실거래일 재확증.
- db_collector.collect._rollback_holiday_duplicate: DELETE는 반드시 db_write_lock 안에서
  commit까지 한 블록(B1), zero_ratio>0.5면 롤백하되 마커 미기록(W1 close=0 가드).
- db_collector.collect._resolve_holiday_duplicate: 판정→W1 확증→롤백 전체 오케스트레이션.
- db_collector.is_rolled_back_today: 마커 존재/부재 판정, 부재 시 파일 생성 부작용 없음,
  "at" 30일 초과 경과 마커는 만료로 간주해 무시, marker가 dict 아니면(배열/None) False
  가드, entry의 "at" 파싱 자체가 불가능하면 보수적으로 True(유효한 롤백) 취급(2026-09 리뷰).
- db_collector._persist_rollback_marker: upsert 시 30일 초과 경과 항목을 함께 prune —
  is_rolled_back_today의 읽기측 만료만으론 파일이 줄지 않으므로 쓰기측에서도 걷어냄.
- main_pkg.jobs.collect.daily_collect_sanity_check: 휴장일/롤백일 스킵(재실행+알림 안 함),
  정상 거래일 0건은 기존대로 재실행
- main_pkg.jobs.collect.daily_collect_job: holiday_duplicate_rollback / duplicate_but_trading_day
  사유별 운영자 알림(plain text, W3), 기존 주말/공휴일 조용한 스킵은 무변경
- collect_daily ↔ daily_collect_job 계약: 실제 collect_daily()(KIS/pykrx 네트워크 구간만 mock)가
  반환하는 report 키 구조를 daily_collect_job이 그대로 소비하는지 end-to-end로 확인
- main_pkg.jobs.sanity.weekly_sanity_check: 자가롤백 마커로 결손에서 제외한 날짜를 조용히
  빼지 않고 주간 메시지에 "📅 자가롤백 처리일" 로 명시(2026-09 리뷰, 침묵-0 금지 원칙).
  마커일+진짜 누락일이 동시에 있어도 누락 라인엔 마커일이 섞이지 않고, 백필
  (backfill_day_via_chart, 이 파일에서는 전부 AsyncMock)은 진짜 누락일에만 호출됨.

실 DB/실 data 디렉토리는 건드리지 않는다 — 전부 tmp_path sqlite + monkeypatch
(db_collector.DB_PATH / db_collector._DATA_DIR).
"""
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

import db_collector
import kis_api
import main_pkg.jobs.collect as job_mod
import main_pkg.jobs.sanity as sanity_mod


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """db_collector.DB_PATH → tmp sqlite, db_collector._DATA_DIR → tmp_path.
    둘 다 _PackageModule 프록시가 _BACKING 전 모듈(_config/_db/collect)로 전파한다."""
    db_path = str(tmp_path / "test_holiday_rollback.db")
    monkeypatch.setattr(db_collector, "DB_PATH", db_path)
    monkeypatch.setattr(db_collector, "_DATA_DIR", str(tmp_path))
    return tmp_path


def _seed_snapshot(conn, trade_date: str, n: int, *, same_close_as: str | None = None,
                    offset: int = 0):
    """daily_snapshot에 n종목 시딩. same_close_as가 주어지면 그 날짜의 close를 그대로
    복제(휴장일 복제 시나리오). 아니면 1000+i+offset — 서로 다른 날짜를 "정상 거래일"로
    시딩할 때는 offset을 다르게 줘서 종가가 실제로 달라지게 한다(그렇지 않으면 같은 공식이
    우연히 같은 값을 내 롤백 오탐/오검이 생김).
    daily_snapshot.symbol → stock_master(symbol) FK가 있어 stock_master도 함께 upsert."""
    for i in range(n):
        symbol = f"{i:06d}"
        conn.execute(
            "INSERT OR IGNORE INTO stock_master (symbol, name, market) VALUES (?, ?, ?)",
            (symbol, f"종목{i}", "kospi"),
        )
        if same_close_as:
            row = conn.execute(
                "SELECT close FROM daily_snapshot WHERE trade_date=? AND symbol=?",
                (same_close_as, symbol),
            ).fetchone()
            close = row[0] if row else 1000 + i
        else:
            close = 1000 + i + offset
        conn.execute(
            "INSERT OR REPLACE INTO daily_snapshot (trade_date, symbol, close) VALUES (?, ?, ?)",
            (trade_date, symbol, close),
        )
    conn.commit()


def _seed_zero_close_majority(conn, prev_date: str, date: str, n: int = 150, zero_from: int = 60):
    """prev/date 양쪽에 종목 n개를 심되, zero_from 이후는 close=0(KRX 폴백 실패 재현).
    same=n(전부 매칭)이지만 zero_ratio = (n-zero_from)/n > 0.5가 되도록 설계."""
    for i in range(n):
        symbol = f"{i:06d}"
        conn.execute(
            "INSERT OR IGNORE INTO stock_master (symbol, name, market) VALUES (?, ?, ?)",
            (symbol, f"종목{i}", "kospi"),
        )
        close = 0 if i >= zero_from else 1000 + i
        for d in (prev_date, date):
            conn.execute(
                "INSERT OR REPLACE INTO daily_snapshot (trade_date, symbol, close) VALUES (?, ?, ?)",
                (d, symbol, close),
            )
    conn.commit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. db_collector._detect_holiday_duplicate — 순수 read 판정 (B1: DB 쓰기 없음)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestDetectHolidayDuplicate:
    def test_detects_duplicate_and_does_not_mutate_db(self, tmp_env):
        """tot>=100 & 98%+ 동일종가 → 복제 후보 dict 반환. B1: read-only이므로 date 행이
        그대로 남아있어야 한다(삭제는 _rollback_holiday_duplicate의 몫)."""
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 150)
        _seed_snapshot(conn, "20261005", 150, same_close_as="20261002")
        conn.close()

        detect = db_collector._detect_holiday_duplicate("20261005")
        assert detect is not None
        assert "skipped_reason" not in detect
        assert detect["date"] == "20261005"
        assert detect["prev"] == "20261002"
        assert detect["tot"] == 150
        assert detect["same"] == 150
        assert detect["pct"] == 100
        assert detect["zero_ratio"] == 0.0

        conn = db_collector._get_db()
        remaining = conn.execute(
            "SELECT COUNT(*) FROM daily_snapshot WHERE trade_date=?", ("20261005",)
        ).fetchone()[0]
        conn.close()
        assert remaining == 150  # 삭제되지 않음 — 판정과 쓰기 분리(B1)

    def test_below_min_row_gate_returns_none(self, tmp_env):
        """tot<100(부분 수집 실패로 수십 행)이면 100% 동일해도 복제 후보로 보지 않음
        — 부분 실패를 휴장일로 오판하는 것 방지."""
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 50)
        _seed_snapshot(conn, "20261005", 50, same_close_as="20261002")
        conn.close()

        assert db_collector._detect_holiday_duplicate("20261005") is None

    def test_below_threshold_ratio_returns_none(self, tmp_env):
        """tot>=100이어도 동일종가 비율이 98% 미만이면 복제 아님(정상 거래일 대조군)."""
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 120)
        _seed_snapshot(conn, "20261005", 120, offset=7)  # 서로 다른 close (정상 거래일)
        conn.close()

        assert db_collector._detect_holiday_duplicate("20261005") is None

    def test_prev_partial_when_no_earlier_data_at_all(self, tmp_env):
        """W2 — 직전 거래일 데이터 자체가 전혀 없으면 판정 보류."""
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261010", 120)
        conn.close()

        detect = db_collector._detect_holiday_duplicate("20261010")
        assert detect == {"skipped_reason": "prev_partial"}

    def test_prev_partial_when_only_candidate_too_small(self, tmp_env):
        """W2 — 최근 10거래일 내 유일한 후보가 rows(prev)>=0.9*tot(date) 미달이면 판정 보류
        (부분수집된 날을 prev로 잘못 채택해 종가 비교가 무의미해지는 것 방지)."""
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261009", 20, offset=50)   # 유일 후보, tot(120)의 90%(108) 미달
        _seed_snapshot(conn, "20261010", 120, offset=77)
        conn.close()

        detect = db_collector._detect_holiday_duplicate("20261010")
        assert detect == {"skipped_reason": "prev_partial"}

    def test_prev_selects_most_recent_day_meeting_90pct_coverage(self, tmp_env):
        """W2 — 더 최근이지만 부분수집(20행)인 20261009는 건너뛰고, rows>=0.9*tot인
        20261008(115행)을 prev로 선택해야 한다."""
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261008", 115)                              # 유효 후보
        _seed_snapshot(conn, "20261009", 20, offset=99)                    # 최근이지만 부분수집
        _seed_snapshot(conn, "20261010", 115, same_close_as="20261008")    # date == 20261008 복제
        conn.close()

        detect = db_collector._detect_holiday_duplicate("20261010")
        assert detect is not None
        assert "skipped_reason" not in detect
        assert detect["prev"] == "20261008"   # 20261009(더 최근)가 아니라
        assert detect["tot"] == 115
        assert detect["pct"] == 100

    def test_zero_ratio_reflects_zero_close_matches(self, tmp_env):
        """close=0끼리 매칭된 비율이 zero_ratio에 정확히 계산되는지(마커 저장 여부 결정은
        _rollback_holiday_duplicate가 수행 — 여기서는 값 계산만 검증)."""
        conn = db_collector._get_db()
        _seed_zero_close_majority(conn, "20261002", "20261005", n=150, zero_from=60)
        conn.close()

        detect = db_collector._detect_holiday_duplicate("20261005")
        assert detect is not None
        assert detect["same"] == 150
        assert detect["zero_ratio"] == pytest.approx(90 / 150)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. db_collector._rollback_holiday_duplicate — write half (B1 락 규율 + W1 마커 가드)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestRollbackHolidayDuplicate:
    def test_deletes_rows_and_persists_marker(self, tmp_env):
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 150)
        _seed_snapshot(conn, "20261005", 150, same_close_as="20261002")
        conn.close()

        detect = db_collector._detect_holiday_duplicate("20261005")
        assert detect is not None
        result = asyncio.run(db_collector._rollback_holiday_duplicate(detect))

        assert result["deleted"] == 150
        assert result["prev"] == "20261002"
        assert result["pct"] == 100
        assert "marker_skipped" not in result

        conn = db_collector._get_db()
        remaining = conn.execute(
            "SELECT COUNT(*) FROM daily_snapshot WHERE trade_date=?", ("20261005",)
        ).fetchone()[0]
        conn.close()
        assert remaining == 0
        assert db_collector.is_rolled_back_today("20261005") is True

    def test_marker_file_content_matches_spec_shape(self, tmp_env):
        """{"YYYYMMDD": {"deleted": n, "same_pct": x, "at": iso}} 형태로 기록되는지."""
        from kis_api._files import load_json

        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 120)
        _seed_snapshot(conn, "20261005", 120, same_close_as="20261002")
        conn.close()

        detect = db_collector._detect_holiday_duplicate("20261005")
        asyncio.run(db_collector._rollback_holiday_duplicate(detect))

        marker_path = f"{tmp_env}/holiday_rollback.json"
        marker = load_json(marker_path)
        assert "20261005" in marker
        entry = marker["20261005"]
        assert entry["deleted"] == 120
        assert entry["same_pct"] == 100
        assert isinstance(entry["at"], str) and entry["at"]  # ISO 문자열

    def test_zero_close_majority_rolls_back_but_skips_marker(self, tmp_env):
        """W1 close=0 가드 — zero_ratio>0.5면 롤백은 하되(중복 데이터 방지) 마커는
        남기지 않아 sanity가 다음 시도에서 자가치유할 수 있게 한다."""
        conn = db_collector._get_db()
        _seed_zero_close_majority(conn, "20261002", "20261005", n=150, zero_from=60)
        conn.close()

        detect = db_collector._detect_holiday_duplicate("20261005")
        assert detect["zero_ratio"] > 0.5
        result = asyncio.run(db_collector._rollback_holiday_duplicate(detect))

        assert result["marker_skipped"] == "zero_close_majority"

        conn = db_collector._get_db()
        remaining = conn.execute(
            "SELECT COUNT(*) FROM daily_snapshot WHERE trade_date=?", ("20261005",)
        ).fetchone()[0]
        conn.close()
        assert remaining == 0  # 롤백 자체는 수행됨

        assert db_collector.is_rolled_back_today("20261005") is False  # 마커는 없음

    def test_delete_executes_while_db_write_lock_held(self, tmp_env, monkeypatch):
        """B1 — DELETE가 db_write_lock을 잡은 상태에서 실행되는지 커넥션을 래핑해 확인.
        연결/커밋 외의 await(네트워크·sleep)이 락 안에 없어야 하므로, DELETE 실행 시점에
        정확히 locked()=True여야 한다."""
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 120)
        _seed_snapshot(conn, "20261005", 120, same_close_as="20261002")
        conn.close()

        detect = db_collector._detect_holiday_duplicate("20261005")
        assert detect is not None

        real_get_db = db_collector._get_db
        observed = {}

        class _LockObservingConn:
            def __init__(self, real_conn):
                self._real = real_conn

            def execute(self, sql, *a, **kw):
                if sql.strip().upper().startswith("DELETE"):
                    observed["locked_during_delete"] = db_collector.db_write_lock.locked()
                return self._real.execute(sql, *a, **kw)

            def commit(self):
                return self._real.commit()

            def close(self):
                return self._real.close()

        monkeypatch.setattr(db_collector, "_get_db", lambda: _LockObservingConn(real_get_db()))

        result = asyncio.run(db_collector._rollback_holiday_duplicate(detect))

        assert observed.get("locked_during_delete") is True
        assert result["deleted"] == 120
        assert db_collector.db_write_lock.locked() is False  # 종료 후 해제됨


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. db_collector._kis_confirms_trading_day — W1 KIS 캔들 확증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestKisConfirmsTradingDay:
    def test_returns_true_when_candle_present(self, monkeypatch):
        monkeypatch.setattr(kis_api, "get_kis_token", AsyncMock(return_value="tok"))
        monkeypatch.setattr(kis_api, "_kis_get", AsyncMock(return_value=(
            200, {"output2": [{"stck_bsop_date": "20261005"}, {"stck_bsop_date": "20261002"}]},
        )))
        assert asyncio.run(db_collector._kis_confirms_trading_day("20261005")) is True

    def test_returns_false_when_candle_absent(self, monkeypatch):
        monkeypatch.setattr(kis_api, "get_kis_token", AsyncMock(return_value="tok"))
        monkeypatch.setattr(kis_api, "_kis_get", AsyncMock(return_value=(
            200, {"output2": [{"stck_bsop_date": "20261002"}]},
        )))
        assert asyncio.run(db_collector._kis_confirms_trading_day("20261005")) is False

    def test_returns_none_on_fetch_failure(self, monkeypatch):
        monkeypatch.setattr(kis_api, "get_kis_token", AsyncMock(side_effect=RuntimeError("boom")))
        assert asyncio.run(db_collector._kis_confirms_trading_day("20261005")) is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. db_collector._resolve_holiday_duplicate — 전체 오케스트레이션
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestResolveHolidayDuplicate:
    def test_not_duplicate_returns_none_without_confirming(self, tmp_env, monkeypatch):
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 120)
        _seed_snapshot(conn, "20261005", 120, offset=7)  # 정상 거래일 (다른 종가)
        conn.close()

        confirm = AsyncMock()
        monkeypatch.setattr(db_collector, "_kis_confirms_trading_day", confirm)

        assert asyncio.run(db_collector._resolve_holiday_duplicate("20261005")) is None
        confirm.assert_not_called()

    def test_prev_partial_returns_without_confirming_or_deleting(self, tmp_env, monkeypatch):
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261010", 120)  # 직전 거래일 데이터 없음
        conn.close()

        confirm = AsyncMock()
        monkeypatch.setattr(db_collector, "_kis_confirms_trading_day", confirm)

        outcome = asyncio.run(db_collector._resolve_holiday_duplicate("20261010"))
        assert outcome == {"reason": "prev_partial", "skipped_reason": "prev_partial"}
        confirm.assert_not_called()

    def test_confirmed_trading_day_keeps_data_and_skips_marker(self, tmp_env, monkeypatch):
        """W1 핵심 시나리오 — 복제로 감지됐지만 KIS 캔들상 실거래일이면 롤백/마커 모두
        보류하고 데이터를 보존한다."""
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 120)
        _seed_snapshot(conn, "20261005", 120, same_close_as="20261002")
        conn.close()

        monkeypatch.setattr(db_collector, "_kis_confirms_trading_day", AsyncMock(return_value=True))

        outcome = asyncio.run(db_collector._resolve_holiday_duplicate("20261005"))
        assert outcome["reason"] == "duplicate_but_trading_day"
        assert outcome["duplicate_check"]["pct"] == 100

        conn = db_collector._get_db()
        remaining = conn.execute(
            "SELECT COUNT(*) FROM daily_snapshot WHERE trade_date=?", ("20261005",)
        ).fetchone()[0]
        conn.close()
        assert remaining == 120  # 롤백 안 됨 — 데이터 보존
        assert db_collector.is_rolled_back_today("20261005") is False  # 마커도 없음

    def test_confirmed_not_trading_day_rolls_back(self, tmp_env, monkeypatch):
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 120)
        _seed_snapshot(conn, "20261005", 120, same_close_as="20261002")
        conn.close()

        monkeypatch.setattr(db_collector, "_kis_confirms_trading_day", AsyncMock(return_value=False))

        outcome = asyncio.run(db_collector._resolve_holiday_duplicate("20261005"))
        assert outcome["reason"] == "holiday_duplicate_rollback"
        assert outcome["rolled_back"]["deleted"] == 120
        assert db_collector.is_rolled_back_today("20261005") is True

    def test_confirmation_fetch_failure_falls_back_to_rollback(self, tmp_env, monkeypatch):
        """캔들 조회 자체가 실패(None)하면 기존대로 롤백+마커 진행."""
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 120)
        _seed_snapshot(conn, "20261005", 120, same_close_as="20261002")
        conn.close()

        monkeypatch.setattr(db_collector, "_kis_confirms_trading_day", AsyncMock(return_value=None))

        outcome = asyncio.run(db_collector._resolve_holiday_duplicate("20261005"))
        assert outcome["reason"] == "holiday_duplicate_rollback"
        assert db_collector.is_rolled_back_today("20261005") is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. db_collector.is_rolled_back_today
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestIsRolledBackToday:
    def test_false_when_no_marker_file(self, tmp_env):
        assert db_collector.is_rolled_back_today("20260101") is False

    def test_false_for_unrelated_date(self, tmp_env):
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 120)
        _seed_snapshot(conn, "20261005", 120, same_close_as="20261002")
        conn.close()
        detect = db_collector._detect_holiday_duplicate("20261005")
        asyncio.run(db_collector._rollback_holiday_duplicate(detect))

        assert db_collector.is_rolled_back_today("20261005") is True
        assert db_collector.is_rolled_back_today("20261006") is False

    def test_no_side_effect_file_created_when_marker_absent(self, tmp_env):
        """사소(a) — 마커 파일이 없을 때 조회만으로 빈 마커+.lock 파일을 생성하면 안 됨."""
        import os
        marker_path = str(tmp_env / "holiday_rollback.json")
        assert not os.path.exists(marker_path)

        assert db_collector.is_rolled_back_today("20260101") is False

        assert not os.path.exists(marker_path)
        assert not os.path.exists(marker_path + ".lock")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. main_pkg.jobs.collect.daily_collect_sanity_check — 휴장일/롤백일 스킵
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestDailyCollectSanityCheckGuards:
    """_is_kr_trading_day는 "%Y%m%d" 문자열 전용(date 객체는 fail-open True) — 잡 코드가
    now.strftime("%Y%m%d")로 호출하는지까지 이 테스트로 확인한다."""

    @staticmethod
    def _freeze(monkeypatch, y, m, d, hh=19, mm=15):
        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(y, m, d, hh, mm, tzinfo=tz)
        monkeypatch.setattr(job_mod, "datetime", _FixedDatetime)

    @staticmethod
    def _make_context():
        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock(return_value=None)
        return ctx

    def test_skips_on_registered_holiday_20261005_no_rerun_no_alert(self, tmp_env, monkeypatch):
        """2026-10-05(개천절 대체휴일, 월요일)는 _KR_MARKET_HOLIDAYS 등록 휴장일
        → collect_daily 재실행/알림 없음. (구 테스트명이 "미등록"이라 오기 — 20261005는
        db_collector/_config.py에 등록된 휴장일이므로 명칭 정정)"""
        self._freeze(monkeypatch, 2026, 10, 5)
        rerun = AsyncMock()
        monkeypatch.setattr(job_mod, "daily_collect_job", rerun)
        ctx = self._make_context()

        asyncio.run(job_mod.daily_collect_sanity_check(ctx))

        rerun.assert_not_called()
        ctx.bot.send_message.assert_not_called()

    def test_skips_when_already_rolled_back_today(self, tmp_env, monkeypatch):
        """정상 거래일이라도 오늘 이미 자가롤백 마커가 있으면 스킵(재실행/알림 안 함)."""
        self._freeze(monkeypatch, 2026, 6, 9)  # 화요일, 정상 거래일
        monkeypatch.setattr(db_collector, "is_rolled_back_today", lambda d: True)
        rerun = AsyncMock()
        monkeypatch.setattr(job_mod, "daily_collect_job", rerun)
        ctx = self._make_context()

        asyncio.run(job_mod.daily_collect_sanity_check(ctx))

        rerun.assert_not_called()
        ctx.bot.send_message.assert_not_called()

    def test_normal_trading_day_zero_rows_still_reruns(self, tmp_env, monkeypatch):
        """회귀 방지 — 정상 거래일 + 당일 0건이면 기존대로 재실행+알림."""
        self._freeze(monkeypatch, 2026, 6, 9)  # 화요일, 정상 거래일, 휴일 아님
        monkeypatch.setattr(db_collector, "is_rolled_back_today", lambda d: False)
        rerun = AsyncMock()
        monkeypatch.setattr(job_mod, "daily_collect_job", rerun)
        ctx = self._make_context()
        # DB는 tmp_env로 이미 빈 상태 (daily_snapshot 0건)

        asyncio.run(job_mod.daily_collect_sanity_check(ctx))

        rerun.assert_awaited_once_with(ctx)
        ctx.bot.send_message.assert_called_once()
        sent_text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "daily_collect 미실행 감지" in sent_text

    def test_normal_trading_day_nonzero_rows_no_rerun(self, tmp_env, monkeypatch):
        """정상 거래일 + 당일 데이터 이미 있으면 조용히 반환(기존 동작)."""
        self._freeze(monkeypatch, 2026, 6, 9)
        monkeypatch.setattr(db_collector, "is_rolled_back_today", lambda d: False)
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20260609", 5)
        conn.close()
        rerun = AsyncMock()
        monkeypatch.setattr(job_mod, "daily_collect_job", rerun)
        ctx = self._make_context()

        asyncio.run(job_mod.daily_collect_sanity_check(ctx))

        rerun.assert_not_called()
        ctx.bot.send_message.assert_not_called()

    def test_weekend_still_skips_before_any_guard(self, tmp_env, monkeypatch):
        """기존 로직 무변경 — 주말은 휴장일/롤백 가드 이전에 이미 return."""
        self._freeze(monkeypatch, 2026, 6, 6)  # 토요일
        rerun = AsyncMock()
        monkeypatch.setattr(job_mod, "daily_collect_job", rerun)
        ctx = self._make_context()

        asyncio.run(job_mod.daily_collect_sanity_check(ctx))

        rerun.assert_not_called()
        ctx.bot.send_message.assert_not_called()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. main_pkg.jobs.collect.daily_collect_job — 운영자 알림 (W1/W3)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestDailyCollectJobRollbackAlert:
    @staticmethod
    def _freeze(monkeypatch, y, m, d, hh=18, mm=30):
        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(y, m, d, hh, mm, tzinfo=tz)
        monkeypatch.setattr(job_mod, "datetime", _FixedDatetime)

    @staticmethod
    def _make_context():
        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock(return_value=None)
        return ctx

    def test_alerts_once_on_holiday_duplicate_rollback(self, monkeypatch):
        self._freeze(monkeypatch, 2026, 10, 5)  # 평일(가드 통과용, weekday<5)
        report = {
            "skipped": True, "reason": "holiday_duplicate_rollback", "date": "20261005",
            "rolled_back": {"date": "20261005", "prev": "20261002", "deleted": 2864, "pct": 99},
        }
        monkeypatch.setattr(job_mod, "collect_daily", AsyncMock(return_value=report))
        ctx = self._make_context()

        asyncio.run(job_mod.daily_collect_job(ctx))

        ctx.bot.send_message.assert_called_once()
        call = ctx.bot.send_message.call_args
        sent_text = call.kwargs.get("text", "")
        # W3: plain text 발송 — parse_mode를 지정하지 않는다(이 모듈의 다른 알림과 동일 관례).
        assert "parse_mode" not in call.kwargs or call.kwargs["parse_mode"] is None
        assert "복제 감지" in sent_text
        assert "휴장일 추정" in sent_text
        assert "20261005" in sent_text
        assert "99%" in sent_text
        assert "2864" in sent_text
        assert "_KR_MARKET_HOLIDAYS" in sent_text

    def test_no_alert_on_plain_weekend_skip(self, monkeypatch):
        """기존 동작 무변경 — 주말/일반 공휴일 스킵은 여전히 조용히 반환."""
        self._freeze(monkeypatch, 2026, 5, 25)  # weekday 값은 collect_daily mock이 대체하므로 무관
        report = {"skipped": True, "reason": "weekend", "date": "20260523"}
        monkeypatch.setattr(job_mod, "collect_daily", AsyncMock(return_value=report))
        ctx = self._make_context()

        asyncio.run(job_mod.daily_collect_job(ctx))

        ctx.bot.send_message.assert_not_called()

    def test_alerts_suspicion_on_duplicate_but_trading_day(self, monkeypatch):
        """W1 — 복제로 감지됐지만 KIS 캔들상 실거래일로 확증된 경우: 정상완료 메시지 +
        별도 의심 경고, 총 2회 발송."""
        self._freeze(monkeypatch, 2026, 6, 9)
        report = {
            "date": "20260609", "phases": {}, "total": 2500, "duration": 120.0,
            "reason": "duplicate_but_trading_day",
            "duplicate_check": {"date": "20260609", "prev": "20260608", "pct": 98,
                                 "tot": 2500, "same": 2450, "zero_ratio": 0.0},
        }
        monkeypatch.setattr(job_mod, "collect_daily", AsyncMock(return_value=report))
        ctx = self._make_context()

        asyncio.run(job_mod.daily_collect_job(ctx))

        assert ctx.bot.send_message.call_count == 2
        texts = [c.kwargs.get("text", "") for c in ctx.bot.send_message.call_args_list]
        assert any("DB 수집 완료" in t for t in texts)
        assert any("KIS 캔들상 거래일" in t and "98%" in t and "확인 필요" in t for t in texts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. collect_daily ↔ daily_collect_job 계약 — 실제 collect_daily() end-to-end
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestCollectDailyJobContract:
    """실제 db_collector.collect_daily()를 호출(KIS Phase 1-4/pykrx/KIS 캔들확증만 mock)해
    복제-감지→롤백 경로를 처음부터 끝까지 태우고, 그 report를 daily_collect_job에 그대로
    흘려 skipped/reason/rolled_back 계약과 알림 발송까지 한 번에 검증한다."""

    def test_collect_daily_rollback_report_shape_and_job_alerts_once(self, tmp_env, monkeypatch):
        # date는 20261006(화, 미등록/평일) 사용 — 20261005는 이미 _KR_MARKET_HOLIDAYS에
        # 등록돼 있어 collect_daily 진입 즉시 reason="holiday"로 조기 반환되므로,
        # 이 테스트가 검증하려는 "미등록 휴장일 복제-감지" 경로를 태우지 못한다.
        conn = db_collector._get_db()
        _seed_snapshot(conn, "20261002", 120)
        conn.close()

        async def _fake_fetch_krx(date, mkt):
            if mkt != "STK":
                return []
            return [
                {"ticker": f"{i:06d}", "name": f"종목{i}", "market": "kospi",
                 "close": 1000 + i, "open": 1000 + i, "high": 1000 + i, "low": 1000 + i,
                 "chg_pct": 0.0, "volume": 100, "trade_value": 100, "market_cap": 100}
                for i in range(120)
            ]

        monkeypatch.setattr(db_collector, "fetch_krx_market_data", AsyncMock(side_effect=_fake_fetch_krx))
        monkeypatch.setattr(kis_api, "get_kis_token", AsyncMock(return_value="fake-token"))
        monkeypatch.setattr(
            db_collector, "_collect_phase",
            AsyncMock(return_value={"results": {}, "success": 0, "failed": 0}),
        )
        monkeypatch.setattr(
            db_collector, "_fetch_supply_data",
            lambda date: {"rows": [], "empty": 0, "errors": []},
        )
        # W1 확증: 캔들 없음(휴장 추정) → 기존 롤백 로직 진행
        monkeypatch.setattr(db_collector, "_kis_confirms_trading_day", AsyncMock(return_value=False))

        report = asyncio.run(db_collector.collect_daily("20261006"))

        assert report.get("skipped") is True
        assert report.get("reason") == "holiday_duplicate_rollback"
        assert "rolled_back" in report
        assert report["rolled_back"]["deleted"] == 120
        assert report["rolled_back"]["prev"] == "20261002"

        conn = db_collector._get_db()
        remaining = conn.execute(
            "SELECT COUNT(*) FROM daily_snapshot WHERE trade_date=?", ("20261006",)
        ).fetchone()[0]
        conn.close()
        assert remaining == 0
        assert db_collector.is_rolled_back_today("20261006") is True

        # 같은 report를 daily_collect_job에 흘려보내 알림 계약도 함께 확인
        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 6, 18, 30, tzinfo=tz)  # 평일
        monkeypatch.setattr(job_mod, "datetime", _FixedDatetime)
        monkeypatch.setattr(job_mod, "collect_daily", AsyncMock(return_value=report))
        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock(return_value=None)

        asyncio.run(job_mod.daily_collect_job(ctx))

        ctx.bot.send_message.assert_called_once()
        sent_text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "20261006" in sent_text
        assert "120" in sent_text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. db_collector.is_rolled_back_today — 마커 30일 만료 (2026-09 리뷰)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestRollbackMarkerExpiry:
    def test_marker_expires_after_30_days(self, tmp_env):
        """"at"이 30일 초과 경과한 마커는 무시(False), 30일 이내는 여전히 유효(True) —
        holiday_rollback.json 무기한 누적 방지 + 오래된 마커가 결손 감시를 영구히
        죽이는 것 방지(2026-09 리뷰)."""
        marker_path = str(tmp_env / "holiday_rollback.json")
        stale_at = (datetime.now(db_collector.KST) - timedelta(days=31)).isoformat()
        fresh_at = (datetime.now(db_collector.KST) - timedelta(days=29)).isoformat()
        with open(marker_path, "w", encoding="utf-8") as f:
            json.dump({
                "20260101": {"deleted": 10, "same_pct": 99, "at": stale_at},
                "20260201": {"deleted": 5, "same_pct": 98, "at": fresh_at},
            }, f)

        assert db_collector.is_rolled_back_today("20260101") is False  # 31일 경과 → 만료
        assert db_collector.is_rolled_back_today("20260201") is True   # 29일 경과 → 유효

    def test_write_side_prunes_stale_entries_on_upsert(self, tmp_env):
        """_persist_rollback_marker가 upsert 시 30일 초과 경과 항목을 실제로 제거하는지
        (쓰기측 prune) — is_rolled_back_today의 30일 만료는 읽기측 판정만 바꿀 뿐 파일
        자체를 줄이지 않으므로, 이 테스트가 없으면 "누적 방지" 문구가 docstring에만
        존재하는 거짓 서술로 남는다(2026-09 리뷰)."""
        marker_path = str(tmp_env / "holiday_rollback.json")
        stale_at = (datetime.now(db_collector.KST) - timedelta(days=45)).isoformat()
        with open(marker_path, "w", encoding="utf-8") as f:
            json.dump({"20260101": {"deleted": 10, "same_pct": 99, "at": stale_at}}, f)

        db_collector._persist_rollback_marker("20260901", {"deleted": 1, "pct": 100})

        with open(marker_path, encoding="utf-8") as f:
            marker = json.load(f)
        assert "20260101" not in marker  # 45일 경과 → prune됨
        assert "20260901" in marker      # 새로 upsert한 항목은 유지

    def test_at_unparseable_treated_as_valid_rollback(self, tmp_env):
        """verifier 케이스 (b): 마커 항목은 있는데 그 안의 "at"이 파싱 불가능한 값이면
        만료 여부를 판단할 수 없으므로 보수적으로 "유효한 롤백"으로 취급(True)한다 — False로
        떨어뜨리면 이미 처리된 휴장일을 weekly_sanity_check가 매주 헛백필
        (backfill_day_via_chart)+헛알림으로 반복하게 된다. 이미 "📅 자가롤백 처리일"
        라인으로 날짜가 노출되므로 침묵 위험은 없다(2026-09 리뷰, is_rolled_back_today
        docstring 상충 정정과 짝을 이루는 회귀 테스트)."""
        marker_path = str(tmp_env / "holiday_rollback.json")
        with open(marker_path, "w", encoding="utf-8") as f:
            json.dump({"20260301": {"deleted": 3, "same_pct": 99, "at": "not-a-timestamp"}}, f)

        assert db_collector.is_rolled_back_today("20260301") is True

    def test_marker_not_dict_returns_false(self, tmp_env):
        """holiday_rollback.json이 (손상 등으로) dict가 아니라 배열/None이면
        AttributeError 없이 보수적으로 False(미롤백 취급) — isinstance 가드(2026-09 리뷰)."""
        marker_path = str(tmp_env / "holiday_rollback.json")
        with open(marker_path, "w", encoding="utf-8") as f:
            json.dump(["20260101", "20260102"], f)

        assert db_collector.is_rolled_back_today("20260101") is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. main_pkg.jobs.sanity.weekly_sanity_check — 자가롤백 제외일 가시화 (2026-09 리뷰)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestWeeklySanityRollbackVisibility:
    """조용히 빼지 않기 — 자가롤백 마커로 결손에서 제외한 날짜가 있으면(다른 진짜 누락
    영업일이 없어 종전이면 메시지 자체가 발송 안 됐을 상황에도) 주간 메시지에
    "📅 자가롤백 처리일"로 명시한다."""

    @pytest.fixture(autouse=True)
    def _safety(self, tmp_env, monkeypatch):
        """weekly_sanity_check의 백필 분기(if missing: ... backfill_day_via_chart)가 진짜
        누락일이 있는 테스트에서 실행될 수 있으므로, 실 KIS API를 때리지 않도록
        backfill_day_via_chart를 AsyncMock으로 무력화 + UNIVERSE_FILE을 프로덕션
        data/stock_universe.json 대신 소규모 tmp 파일로 patch한다(2026-09 리뷰 —
        기존 테스트는 missing이 항상 빈 리스트라 이 분기가 실행되지 않아 드러나지 않았음)."""
        mock_backfill = AsyncMock(return_value={"ok": 1, "fail": 0})
        monkeypatch.setattr(db_collector, "backfill_day_via_chart", mock_backfill)
        universe_path = tmp_env / "stock_universe.json"
        universe_path.write_text(json.dumps({"codes": {"000001": "테스트종목"}}), encoding="utf-8")
        monkeypatch.setattr(sanity_mod, "UNIVERSE_FILE", str(universe_path))
        self.mock_backfill = mock_backfill

    @staticmethod
    def _bizdays(now):
        bizdays = []
        d = now.date() - timedelta(days=1)
        for _ in range(21):
            if len(bizdays) >= 10:
                break
            if sanity_mod._is_krx_business_day(d):
                bizdays.append(d.strftime("%Y%m%d"))
            d -= timedelta(days=1)
        return bizdays

    def test_rolled_back_day_shown_even_when_nothing_else_missing(self, tmp_env, monkeypatch):
        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 6, 22, 7, 5, tzinfo=tz)
        monkeypatch.setattr(sanity_mod, "datetime", _FixedDatetime)
        # 확장 sanity_warnings 블록(raw sqlite3.connect)이 프로덕션 data/stock.db를 건드리지
        # 않도록 _DATA_DIR도 tmp로 리다이렉트한다. main_pkg.jobs.sanity는 kis_api._DATA_DIR을
        # 별도로 바인딩하므로(`from kis_api import _DATA_DIR`) db_collector 쪽 tmp_env 패치와는
        # 무관 — 여기서 직접 패치해야 한다.
        monkeypatch.setattr(sanity_mod, "_DATA_DIR", str(tmp_env))

        bizdays = self._bizdays(datetime(2026, 6, 22, 7, 5))
        excluded = bizdays[3]
        prev_for_dup = bizdays[4]  # _detect_holiday_duplicate가 자동 선택할 직전 거래일
        keep = [b for b in bizdays if b != excluded]

        conn = db_collector._get_db()
        for idx, b in enumerate(keep):
            # 500은 have 판정 임계(>=500)의 경계값이라 브리틀함 — 600으로 여유를 둔다
            # (2026-09 리뷰).
            _seed_snapshot(conn, b, 600, offset=idx * 3)
        # excluded를 prev_for_dup 종가 복제로 시딩 → 휴장일 복제로 판정되게 함
        _seed_snapshot(conn, excluded, 120, same_close_as=prev_for_dup)
        conn.close()

        detect = db_collector._detect_holiday_duplicate(excluded)
        assert detect is not None
        asyncio.run(db_collector._rollback_holiday_duplicate(detect))
        assert db_collector.is_rolled_back_today(excluded) is True

        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock(return_value=None)
        asyncio.run(sanity_mod.weekly_sanity_check(ctx))

        sent_texts = [c.kwargs.get("text", "") for c in ctx.bot.send_message.call_args_list]
        assert any(excluded in t and "자가롤백 처리일" in t for t in sent_texts), sent_texts
        # 진짜 결손이 아니므로 "누락 영업일" 경고는 없어야 함 (조용히 빼는 대신 명시만 추가)
        assert not any("daily_snapshot 누락 영업일" in t for t in sent_texts)
        # 진짜 누락이 없으므로 백필도 호출되지 않아야 함
        self.mock_backfill.assert_not_called()

    def test_marker_day_excluded_from_missing_line_and_backfill_targets_real_gap_only(
        self, tmp_env, monkeypatch
    ):
        """verifier 케이스 (a): 마커일 + 진짜 누락일이 동시에 있을 때 —
        (1) "⚠️ daily_snapshot 누락 영업일" 라인에 마커일은 없고 진짜 누락일만 있어야 하며
        (2) backfill_day_via_chart는 진짜 누락일에 대해서만 정확히 1회 호출돼야 한다
        (마커일까지 재백필하면 헛수집 반복, 2026-09 리뷰)."""
        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 6, 22, 7, 5, tzinfo=tz)
        monkeypatch.setattr(sanity_mod, "datetime", _FixedDatetime)
        monkeypatch.setattr(sanity_mod, "_DATA_DIR", str(tmp_env))

        bizdays = self._bizdays(datetime(2026, 6, 22, 7, 5))
        marker_day = bizdays[3]
        missing_day = bizdays[5]  # snapshot 자체를 시딩하지 않음 → 진짜 결손
        prev_for_dup = bizdays[4]  # _detect_holiday_duplicate가 자동 선택할 직전 거래일
        keep = [b for b in bizdays if b not in (marker_day, missing_day)]

        conn = db_collector._get_db()
        for idx, b in enumerate(keep):
            _seed_snapshot(conn, b, 600, offset=idx * 3)
        # marker_day를 prev_for_dup 종가 복제로 시딩 → 휴장일 복제 판정 → 롤백+마커
        _seed_snapshot(conn, marker_day, 120, same_close_as=prev_for_dup)
        conn.close()

        detect = db_collector._detect_holiday_duplicate(marker_day)
        assert detect is not None
        asyncio.run(db_collector._rollback_holiday_duplicate(detect))
        assert db_collector.is_rolled_back_today(marker_day) is True
        assert db_collector.is_rolled_back_today(missing_day) is False  # 마커 없음, 진짜 결손

        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock(return_value=None)
        asyncio.run(sanity_mod.weekly_sanity_check(ctx))

        # missing과 rolled_back_days가 둘 다 있으면 weekly_sanity_check가 한 메시지에
        # "\n".join으로 라인을 합쳐 보낸다 — 메시지 전체가 아니라 각 라인 단위로 검사해야
        # marker_day가 (다른 라인인) "자가롤백 처리일" 쪽에 있는 것과 혼동하지 않는다.
        sent_texts = [c.kwargs.get("text", "") for c in ctx.bot.send_message.call_args_list]
        combined = next((t for t in sent_texts if "daily_snapshot 누락 영업일" in t), None)
        assert combined is not None, sent_texts
        lines = combined.splitlines()
        missing_line = next(l for l in lines if l.startswith("⚠️ daily_snapshot 누락 영업일"))
        assert missing_day in missing_line
        assert marker_day not in missing_line  # 마커일은 누락 라인에서 부재해야 함

        rollback_line = next(l for l in lines if l.startswith("📅 자가롤백 처리일"))
        assert marker_day in rollback_line

        # 백필은 진짜 누락일에 대해서만 정확히 1회
        called_dates = [c.args[0] for c in self.mock_backfill.call_args_list]
        assert called_dates == [missing_day]
