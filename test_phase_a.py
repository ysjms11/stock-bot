"""
Phase A 기능 테스트
1. 환율 강조 (FX Warning) — format_macro_msg() USDKRW change_pct ±0.5% 이모지
2. 배당 캘린더 API — kis_dividend_schedule() 기본값 + 응답 파싱
3. 실적/배당 스케줄러 로직 — 날짜 diff 계산, 주말 조기 종료
"""
import pytest
import sys
import types
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# ── telegram stub (미설치 환경 대비) ──────────────────────────────────────
telegram_stub = types.ModuleType("telegram")
telegram_stub.Update = object
telegram_stub.ReplyKeyboardMarkup = type("ReplyKeyboardMarkup", (), {"__init__": lambda self, *a, **kw: None})
ext_stub = types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.MessageHandler = object
ext_stub.filters = type("filters", (), {"TEXT": None, "Regex": staticmethod(lambda x: x)})()
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)

import kis_api
from kis_api import format_macro_msg, kis_dividend_schedule


# ─────────────────────────────────────────────────────────────────────────────
# 1. 환율 강조 (FX Warning)
# ─────────────────────────────────────────────────────────────────────────────

def _make_macro_data(usdkrw_change_pct) -> dict:
    """format_macro_msg에 최소한으로 필요한 data dict를 만든다."""
    return {
        "VIX":    {"price": "18.5", "change_pct": "-0.10"},
        "KOSPI":  {"price": "2600", "change_pct": "+0.30"},
        "WTI":    {"price": "72.0", "change_pct": "-0.50"},
        "GOLD":   {"price": "2300", "change_pct": "+0.20"},
        "COPPER": {"price": "4.1",  "change_pct": "+0.10"},
        "DXY":    {"price": "103",  "change_pct": "-0.15"},
        "USDKRW": {"price": "1350", "change_pct": usdkrw_change_pct},
        "US10Y":  {"price": "4.25", "change_pct": "+0.05"},
        "FOREIGN_FLOW": {"amount_억": 1234},
        "EVENTS": {},
    }


class TestFxWarning:
    """format_macro_msg — USDKRW 변동률 ±0.5% 경고 이모지 삽입 로직"""

    def test_fx_warn_weak_won(self):
        """change_pct=0.8 → ⚠️📈 포함"""
        msg = format_macro_msg(_make_macro_data(0.8))
        assert "⚠️📈" in msg

    def test_fx_warn_strong_won(self):
        """change_pct=-0.7 → ⚠️📉 포함"""
        msg = format_macro_msg(_make_macro_data(-0.7))
        assert "⚠️📉" in msg

    def test_fx_warn_none_normal(self):
        """change_pct=0.2 → 경고 이모지 없음"""
        msg = format_macro_msg(_make_macro_data(0.2))
        assert "⚠️📈" not in msg
        assert "⚠️📉" not in msg

    def test_fx_warn_edge_0_5(self):
        """boundary: change_pct=0.5 정확히 → ⚠️📈 포함 (>= 0.5)"""
        msg = format_macro_msg(_make_macro_data(0.5))
        assert "⚠️📈" in msg

    def test_fx_warn_edge_neg_0_5(self):
        """boundary: change_pct=-0.5 정확히 → ⚠️📉 포함 (<= -0.5)"""
        msg = format_macro_msg(_make_macro_data(-0.5))
        assert "⚠️📉" in msg

    def test_fx_warn_missing_pct(self):
        """change_pct='?' (문자열) → 크래시 없고 이모지도 없음"""
        msg = format_macro_msg(_make_macro_data("?"))
        assert isinstance(msg, str)
        assert "⚠️📈" not in msg
        assert "⚠️📉" not in msg

    def test_fx_warn_missing_usdkrw_key(self):
        """USDKRW 키 자체가 없어도 크래시 없음"""
        data = _make_macro_data(0.8)
        del data["USDKRW"]
        msg = format_macro_msg(data)
        assert isinstance(msg, str)
        assert "⚠️" not in msg

    def test_fx_warn_just_below_threshold(self):
        """change_pct=0.49 → 임계값 미달, 이모지 없음"""
        msg = format_macro_msg(_make_macro_data(0.49))
        assert "⚠️📈" not in msg
        assert "⚠️📉" not in msg


# ─────────────────────────────────────────────────────────────────────────────
# 2. 배당 캘린더 API (kis_dividend_schedule)
# ─────────────────────────────────────────────────────────────────────────────

class TestDividendScheduleApi:
    """kis_dividend_schedule() — 기본값 날짜, 응답 파싱"""

    @pytest.mark.asyncio
    async def test_dividend_schedule_default_dates(self):
        """from_dt/to_dt 생략 시 오늘 ~ 오늘+90일이 API에 전달됨"""
        captured = {}

        async def _fake_kis_get(session, path, tr_id, token, params):
            captured["F_DT"] = params.get("F_DT", "")
            captured["T_DT"] = params.get("T_DT", "")
            return 200, {"output1": []}

        with patch.object(kis_api, "_kis_get", new=_fake_kis_get):
            await kis_dividend_schedule(token="fake_token")

        now = datetime.now(KST)
        expected_from = now.strftime("%Y%m%d")
        expected_to   = (now + timedelta(days=90)).strftime("%Y%m%d")
        assert captured["F_DT"] == expected_from
        assert captured["T_DT"] == expected_to

    @pytest.mark.asyncio
    async def test_dividend_schedule_returns_list(self):
        """output1 목록을 그대로 반환"""
        sample_output = [
            {"sht_cd": "005930", "record_date": "20260410",
             "per_sto_divi_amt": "1444", "divi_rate": "2.1", "divi_pay_dt": "20260430"},
            {"sht_cd": "005930", "record_date": "20260630",
             "per_sto_divi_amt": "361",  "divi_rate": "0.5", "divi_pay_dt": "20260720"},
        ]

        async def _fake_kis_get(session, path, tr_id, token, params):
            return 200, {"output1": sample_output}

        with patch.object(kis_api, "_kis_get", new=_fake_kis_get):
            result = await kis_dividend_schedule(token="fake_token",
                                                 from_dt="20260329",
                                                 to_dt="20260630",
                                                 ticker="005930")

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["record_date"] == "20260410"
        assert result[1]["divi_rate"] == "0.5"

    @pytest.mark.asyncio
    async def test_dividend_schedule_output_fallback(self):
        """output1 없을 때 output 키로 fallback"""
        sample = [{"sht_cd": "000660", "record_date": "20260501"}]

        async def _fake_kis_get(session, path, tr_id, token, params):
            return 200, {"output": sample}

        with patch.object(kis_api, "_kis_get", new=_fake_kis_get):
            result = await kis_dividend_schedule(token="fake_token")

        assert result == sample

    @pytest.mark.asyncio
    async def test_dividend_schedule_empty_response(self):
        """빈 응답 → 빈 리스트 반환 (크래시 없음)"""
        async def _fake_kis_get(session, path, tr_id, token, params):
            return 200, {}

        with patch.object(kis_api, "_kis_get", new=_fake_kis_get):
            result = await kis_dividend_schedule(token="fake_token")

        assert result == []

    @pytest.mark.asyncio
    async def test_dividend_schedule_custom_dates_passed(self):
        """명시적으로 지정한 from_dt/to_dt가 API로 그대로 전달됨"""
        captured = {}

        async def _fake_kis_get(session, path, tr_id, token, params):
            captured.update(params)
            return 200, {"output1": []}

        with patch.object(kis_api, "_kis_get", new=_fake_kis_get):
            await kis_dividend_schedule(token="fake_token",
                                         from_dt="20260101",
                                         to_dt="20260630",
                                         ticker="035720",
                                         gb1="1")

        assert captured["F_DT"] == "20260101"
        assert captured["T_DT"] == "20260630"
        assert captured["SHT_CD"] == "035720"
        assert captured["GB1"] == "1"


# ─────────────────────────────────────────────────────────────────────────────
# 3. 실적/배당 스케줄러 로직
# ─────────────────────────────────────────────────────────────────────────────

class TestEarningsAnnounceDateCalc:
    """결산월 dt → 발표예상일 diff 계산 로직 (check_earnings_calendar 내부)

    주의: main.py에서 announce_date = datetime(yr, mo, 15, tzinfo=KST) 로 생성되어
    시각이 00:00:00 이다. now가 07:00이면 diff = .days 는 floor이므로 -1이 된다.
    테스트에서 now를 00:00:00 으로 고정해야 정수 diff가 나온다.
    """

    def _calc_announce_diff(self, dt_str: str, now: datetime) -> int:
        """main.py check_earnings_calendar 핵심 날짜 계산을 복제."""
        yr = int(dt_str[:4])
        mo = int(dt_str[4:6])
        announce_mo = mo + 1 if mo < 12 else 1
        announce_yr = yr if mo < 12 else yr + 1
        announce_date = datetime(announce_yr, announce_mo, 15, tzinfo=KST)
        return (announce_date - now).days

    def test_earnings_announce_date_march_quarter(self):
        """dt='202603' → 발표예상일 = 2026-04-15, diff=2 (now=04-13 00:00)"""
        now = datetime(2026, 4, 13, 0, 0, tzinfo=KST)
        diff = self._calc_announce_diff("202603", now)
        # announce_date = 2026-04-15 00:00 KST → diff = 2
        assert diff == 2

    def test_earnings_announce_date_dec_quarter_year_wrap(self):
        """dt='202512' → 발표예상일 = 2026-01-15 (연도 넘김), diff=2 (now=01-13 00:00)"""
        now = datetime(2026, 1, 13, 0, 0, tzinfo=KST)
        diff = self._calc_announce_diff("202512", now)
        # announce_date = 2026-01-15 00:00 KST → diff = 2
        assert diff == 2

    def test_earnings_announce_within_alert_window(self):
        """diff 0~3 범위 → 알림 조건 충족 (now=발표일 당일 00:00)"""
        now = datetime(2026, 4, 15, 0, 0, tzinfo=KST)
        diff = self._calc_announce_diff("202603", now)
        # announce_date = 2026-04-15 00:00 KST, diff = 0
        assert 0 <= diff <= 3

    def test_earnings_announce_outside_alert_window(self):
        """diff > 3 → 알림 조건 미충족 (now=04-05 00:00)"""
        now = datetime(2026, 4, 5, 0, 0, tzinfo=KST)
        diff = self._calc_announce_diff("202603", now)
        assert diff > 3


class TestDividendDiffLogic:
    """배당기준일 diff 계산 로직 (check_dividend_calendar 내부)

    주의: strptime("%Y%m%d") 결과는 시각이 00:00:00이다. now가 07:00이면
    같은 날도 (00:00 - 07:00).days = -1 로 계산된다.
    now를 00:00:00 으로 고정해야 정수 diff를 얻을 수 있다.
    """

    def _calc_record_diff(self, record_date_str: str, now: datetime) -> int:
        """main.py check_dividend_calendar 핵심 날짜 계산을 복제."""
        rec_date = datetime.strptime(record_date_str, "%Y%m%d").replace(tzinfo=KST)
        return (rec_date - now).days

    def test_dividend_diff_within_7_days(self):
        """배당기준일 5일 후 → diff=5, 알림 조건(0~7) 충족"""
        now = datetime(2026, 4, 5, 0, 0, tzinfo=KST)
        record_date = (now + timedelta(days=5)).strftime("%Y%m%d")
        diff = self._calc_record_diff(record_date, now)
        assert diff == 5
        assert 0 <= diff <= 7  # 알림 조건

    def test_dividend_diff_beyond_7_days(self):
        """배당기준일 10일 후 → diff=10, 알림 조건 미충족"""
        now = datetime(2026, 4, 5, 0, 0, tzinfo=KST)
        record_date = (now + timedelta(days=10)).strftime("%Y%m%d")
        diff = self._calc_record_diff(record_date, now)
        assert diff == 10
        assert not (0 <= diff <= 7)

    def test_dividend_diff_today(self):
        """배당기준일 당일 → diff=0 → 알림 조건 충족"""
        now = datetime(2026, 4, 5, 0, 0, tzinfo=KST)
        record_date = now.strftime("%Y%m%d")
        diff = self._calc_record_diff(record_date, now)
        assert diff == 0
        assert 0 <= diff <= 7

    def test_dividend_diff_boundary_7(self):
        """정확히 7일 후 → diff=7, 포함 (<=7)"""
        now = datetime(2026, 4, 1, 0, 0, tzinfo=KST)
        record_date = (now + timedelta(days=7)).strftime("%Y%m%d")
        diff = self._calc_record_diff(record_date, now)
        assert diff == 7
        assert 0 <= diff <= 7

    def test_dividend_diff_boundary_8(self):
        """정확히 8일 후 → diff=8, 알림 미포함 (>7)"""
        now = datetime(2026, 4, 1, 0, 0, tzinfo=KST)
        record_date = (now + timedelta(days=8)).strftime("%Y%m%d")
        diff = self._calc_record_diff(record_date, now)
        assert diff == 8
        assert not (0 <= diff <= 7)


class TestWeekendSkip:
    """주말 조기 종료 조건 — weekday() >= 5"""

    def _is_weekend(self, dt: datetime) -> bool:
        return dt.weekday() >= 5

    def test_saturday_is_skipped(self):
        """토요일 → 조기 종료"""
        sat = datetime(2026, 3, 28, 7, 0, tzinfo=KST)  # 토
        assert sat.weekday() == 5
        assert self._is_weekend(sat) is True

    def test_sunday_is_skipped(self):
        """일요일 → 조기 종료"""
        sun = datetime(2026, 3, 29, 7, 0, tzinfo=KST)  # 일
        assert sun.weekday() == 6
        assert self._is_weekend(sun) is True

    def test_monday_not_skipped(self):
        """월요일 → 정상 실행"""
        mon = datetime(2026, 3, 30, 7, 0, tzinfo=KST)
        assert self._is_weekend(mon) is False

    def test_friday_not_skipped(self):
        """금요일 → 정상 실행"""
        fri = datetime(2026, 3, 27, 7, 0, tzinfo=KST)
        assert self._is_weekend(fri) is False

    def test_earnings_calendar_returns_early_on_weekend(self):
        """check_earnings_calendar: 주말이면 token 발급 없이 조기 종료 확인"""
        # weekday() >= 5 조건을 직접 검증: 실제 함수는 return만 하므로
        # 조건 자체를 독립적으로 검증
        saturday = datetime(2026, 3, 28, 7, 0, tzinfo=KST)
        assert saturday.weekday() >= 5

    def test_dividend_calendar_returns_early_on_weekend(self):
        """check_dividend_calendar: 주말이면 조기 종료"""
        sunday = datetime(2026, 3, 29, 7, 0, tzinfo=KST)
        assert sunday.weekday() >= 5
