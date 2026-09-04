"""test_regime.py — 시장 레짐 판정 로직 단위 테스트."""
import json
import os
import sys
import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta, timezone

import pytest

# kis_api 모듈 로드 전에 /data 디렉토리 문제 방지
os.makedirs("/tmp/test_data", exist_ok=True)

# ── kis_api import를 위한 환경 패치 ──
# /data → /tmp/test_data 로 리다이렉트
import kis_api
kis_api.REGIME_STATE_FILE = "/tmp/test_data/regime_state.json"
kis_api.PORTFOLIO_FILE = "/tmp/test_data/portfolio.json"

from kis_api import (
    _calc_zscore, _rolling_ma_pct, _rolling_momentum,
    _realized_vol, _rolling_realized_vol, _sig_entry,
    cmd_regime,
    load_json, save_json,
    REGIME_STATE_FILE,
)
from kis_api.regime import (
    _apply_regime_debounce,
    _pct_rank,
    _realized_vol_series,
    _dist_from_ma,
)

KST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def _isolate_kis_api_regime_state_file(tmp_path, monkeypatch):
    """kis_api.regime.REGIME_STATE_FILE을 모든 테스트에서 강제 tmp 격리(안전망).

    kis_api.regime 모듈은 `from ._config import REGIME_STATE_FILE`로 자기 네임스페이스에
    독립 바인딩하므로, 위 파일 상단의 `kis_api.REGIME_STATE_FILE = ...`(패키지 표면 심볼)
    패치는 kis_api.regime에 전파되지 않는다. 이 파일의 cmd_regime 호출 테스트는 전부
    `@patch("kis_api.regime.REGIME_STATE_FILE", ...)` 데코레이터로 개별 보호되지만, 향후
    데코레이터 없이 추가되는 테스트가 실수로 프로덕션 data/regime_state.json에 쓰는 사고를
    막기 위한 마지막 방어선. autouse라 unittest.TestCase 메서드에도 자동 적용된다
    (pytest는 데코레이터 없는 autouse fixture만 TestCase에 주입 가능).
    기존 @patch 데코레이터는 테스트 본문 실행 중에만 이 값을 한 번 더 덮어쓰므로 충돌 없음.
    """
    monkeypatch.setattr(
        kis_api.regime, "REGIME_STATE_FILE", str(tmp_path / "regime_state.json")
    )
    yield


class TestZScore(unittest.TestCase):
    """z-score 계산 정확성."""

    def test_basic_zscore(self):
        """평균=50, std≈약 29.15 인 1~100 시리즈에서 마지막 값(100) z-score."""
        values = list(range(1, 101))
        result = _calc_zscore(values, lookback=100, min_data=10)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["value"], 100.0)
        self.assertGreater(result["z"], 1.5)  # 확실히 양수

    def test_zscore_zero_for_mean(self):
        """모든 값이 같으면 z=0."""
        values = [50.0] * 100
        result = _calc_zscore(values, lookback=100, min_data=10)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["z"], 0.0)

    def test_insufficient_data(self):
        """min_data 미만이면 None."""
        values = list(range(50))
        result = _calc_zscore(values, lookback=252, min_data=60)
        self.assertIsNone(result)

    def test_exactly_min_data(self):
        """min_data와 정확히 같은 길이 → 정상 계산."""
        values = list(range(60))
        result = _calc_zscore(values, lookback=252, min_data=60)
        self.assertIsNotNone(result)

    def test_lookback_window(self):
        """lookback보다 데이터가 많으면 최근 lookback만 사용."""
        values = [0] * 300 + list(range(100))  # 400개, 최근 100개는 0~99
        result = _calc_zscore(values, lookback=100, min_data=60)
        self.assertIsNotNone(result)
        # lookback=100이면 최근 100개(0~99)만 사용 → 평균≈49.5, 현재=99
        self.assertGreater(result["z"], 1.0)


class TestScoreConversion(unittest.TestCase):
    """z-score → 점수 (norm.cdf) 변환."""

    def test_z_zero_gives_50(self):
        """z=0 → CDF=0.5 → 50점."""
        from scipy.stats import norm
        score = norm.cdf(0) * 100
        self.assertAlmostEqual(score, 50.0)

    def test_z_positive_2(self):
        """z=+2 → ~97.7점."""
        from scipy.stats import norm
        score = norm.cdf(2) * 100
        self.assertAlmostEqual(score, 97.72, places=1)

    def test_z_negative_2(self):
        """z=-2 → ~2.3점."""
        from scipy.stats import norm
        score = norm.cdf(-2) * 100
        self.assertAlmostEqual(score, 2.28, places=1)



class TestOverrideMode(unittest.TestCase):
    """override 모드 테스트."""

    def setUp(self):
        self.state_file = "/tmp/test_data/regime_state.json"
        save_json(self.state_file, {"history": [], "current": {"regime": "neutral"}})

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_override_crisis(self):
        result = asyncio.run(
            cmd_regime(mode="override", regime="crisis", reason="블랙스완"))
        self.assertIn("공포", result["regime"])
        self.assertEqual(result["mode"], "override")
        self.assertEqual(result["reason"], "블랙스완")
        # 파일에 저장되었는지 확인
        state = load_json(self.state_file)
        self.assertEqual(state["current"]["current"], "crisis")
        self.assertTrue(state["current"]["override"])

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_override_invalid(self):
        result = asyncio.run(
            cmd_regime(mode="override", regime="invalid"))
        self.assertIn("error", result)

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_override_offensive(self):
        result = asyncio.run(
            cmd_regime(mode="override", regime="offensive", reason="강세전환"))
        self.assertIn("탐욕", result["regime"])


class TestHistoryMode(unittest.TestCase):
    """history 모드 테스트."""

    def setUp(self):
        history = [
            {"date": f"2026-03-{25+i:02d}", "combined_score": 50 + i, "regime": "neutral"}
            for i in range(5)
        ]
        save_json("/tmp/test_data/regime_state.json",
                  {"history": history, "current": {"regime": "neutral"}})

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_history_default(self):
        result = asyncio.run(
            cmd_regime(mode="history", days=3))
        self.assertEqual(len(result["history"]), 3)
        self.assertEqual(result["total_records"], 5)

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_history_all(self):
        result = asyncio.run(
            cmd_regime(mode="history", days=100))
        self.assertEqual(len(result["history"]), 5)


class TestPartialFailure(unittest.TestCase):
    """신호 하나 실패해도 나머지로 계산."""

    def test_partial_us_signals(self):
        """일부 yfinance 실패해도 점수 산출."""
        # _yf_history를 mock해서 VIX만 성공, 나머지 실패
        import numpy as np
        np.random.seed(42)
        good_data = list(np.random.randn(300) * 5 + 20)

        def mock_yf(symbol, period="2y"):
            if symbol == "^VIX":
                return good_data
            return []

        with patch("kis_api.news._yf_history", side_effect=mock_yf):
            result = asyncio.run(
                compute_us_signals())
        # VIX만 성공, 나머지 5개 실패
        self.assertGreater(result["n_signals"], 0)
        self.assertGreater(len(result["failed"]), 0)
        # 점수는 여전히 산출됨
        self.assertIsInstance(result["score"], float)


class TestHelpers(unittest.TestCase):
    """헬퍼 함수들."""

    def test_rolling_ma_pct(self):
        closes = [100] * 10 + [110]
        result = _rolling_ma_pct(closes, 10)
        self.assertEqual(len(result), 1)
        # MA = (100*9 + 110)/10 = 101, pct = (110-101)/101*100 ≈ 8.9%
        self.assertAlmostEqual(result[0], 8.9, places=0)

    def test_rolling_momentum(self):
        closes = [100, 105, 110, 115, 120]
        result = _rolling_momentum(closes, 2)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0], 10.0, places=1)  # 110/100 - 1

    def test_realized_vol_basic(self):
        # 일정한 종가 → 변동성 ≈ 0
        closes = [100.0] * 25
        vol = _realized_vol(closes, 20)
        self.assertIsNotNone(vol)
        self.assertAlmostEqual(vol, 0.0, places=3)

    def test_realized_vol_insufficient(self):
        vol = _realized_vol([100, 101], 20)
        self.assertIsNone(vol)

    def test_sig_entry_invert(self):
        s = _sig_entry(25.0, 1.5, "역수", invert=True)
        self.assertAlmostEqual(s["z"], -1.5)
        self.assertAlmostEqual(s["raw_z"], 1.5)

    def test_sig_entry_normal(self):
        s = _sig_entry(100, 0.8, "%")
        self.assertAlmostEqual(s["z"], 0.8)


# compute_us_signals / compute_kr_signals 에서 yfinance를 mock
from kis_api import compute_us_signals, compute_kr_signals


class TestSigEntry(unittest.TestCase):
    def test_value_preserved(self):
        s = _sig_entry(42.5, 1.23, "test")
        self.assertEqual(s["value"], 42.5)
        self.assertEqual(s["label"], "test")


class TestJudgeRegimeV6(unittest.TestCase):
    """judge_regime() v6 — INVESTMENT_RULES v6 3단계 판정 (S&P 200MA + VIX)."""

    def _make_data(self, sp_price=None, sp_ma200=None, vix=None):
        d = {}
        if sp_price is not None or sp_ma200 is not None:
            sp = {}
            if sp_price is not None:
                sp["price"] = sp_price
            if sp_ma200 is not None:
                sp["ma200"] = sp_ma200
            d["SP500"] = sp
        if vix is not None:
            d["VIX"] = {"price": vix}
        return d

    def test_green_offensive(self):
        """S&P > 200MA+3% AND VIX < 20 → 🟢 공격."""
        from kis_api import judge_regime
        data = self._make_data(sp_price=5000, sp_ma200=4500, vix=15)
        r = judge_regime(data)
        self.assertEqual(r["regime"], "🟢")
        self.assertEqual(r["label"], "공격")

    def test_yellow_vix_mid(self):
        """S&P > 200MA+3% 이지만 VIX 중간 → 🟡 경계."""
        from kis_api import judge_regime
        data = self._make_data(sp_price=5000, sp_ma200=4500, vix=25)
        r = judge_regime(data)
        self.assertEqual(r["regime"], "🟡")
        self.assertEqual(r["label"], "경계")

    def test_yellow_sp_below(self):
        """S&P 소폭 이탈 (<200MA, VIX 낮음) → 🟡 경계."""
        from kis_api import judge_regime
        # 4400 < 4500 - 3%(=4365) 아니므로 버퍼존 → 중립. 더 낮게.
        data = self._make_data(sp_price=4200, sp_ma200=4500, vix=18)
        r = judge_regime(data)
        self.assertEqual(r["regime"], "🟡")

    def test_yellow_buffer_zone(self):
        """S&P 200MA 버퍼존(±3%) → 🟡 경계."""
        from kis_api import judge_regime
        # 4500 기준 ±3% = 4365 ~ 4635. 4550은 버퍼존.
        data = self._make_data(sp_price=4550, sp_ma200=4500, vix=15)
        r = judge_regime(data)
        self.assertEqual(r["regime"], "🟡")

    def test_red_crisis(self):
        """S&P < 200MA-3% AND VIX > 30 → 🔴 위기."""
        from kis_api import judge_regime
        data = self._make_data(sp_price=4000, sp_ma200=4500, vix=35)
        r = judge_regime(data)
        self.assertEqual(r["regime"], "🔴")
        self.assertEqual(r["label"], "위기")

    def test_red_requires_both(self):
        """S&P 하향 but VIX 낮으면 🔴 아님 → 🟡."""
        from kis_api import judge_regime
        data = self._make_data(sp_price=4000, sp_ma200=4500, vix=15)
        r = judge_regime(data)
        self.assertEqual(r["regime"], "🟡")

    def test_vix_missing_defensive(self):
        """VIX 없음 방어 → 🟡 경계 (🟢/🔴 둘 다 불가)."""
        from kis_api import judge_regime
        data = self._make_data(sp_price=5000, sp_ma200=4500, vix=None)
        r = judge_regime(data)
        self.assertEqual(r["regime"], "🟡")

    def test_sp500_missing_defensive(self):
        """S&P 없음 방어 → 🟡 경계."""
        from kis_api import judge_regime
        data = self._make_data(sp_price=None, sp_ma200=None, vix=15)
        r = judge_regime(data)
        self.assertEqual(r["regime"], "🟡")

    def test_empty_data_defensive(self):
        """빈 데이터 → 🟡 경계 (예외 없이)."""
        from kis_api import judge_regime
        r = judge_regime({})
        self.assertEqual(r["regime"], "🟡")

    def test_question_mark_values(self):
        """'?' 문자열 값 방어."""
        from kis_api import judge_regime
        data = {"SP500": {"price": "?", "ma200": "?"}, "VIX": {"price": "?"}}
        r = judge_regime(data)
        self.assertEqual(r["regime"], "🟡")


class TestApplyRegimeDebounce(unittest.TestCase):
    """_apply_regime_debounce — per-market 디바운스 순수함수 테스트."""

    def _fresh(self):
        """빈 state (신규 market 슬롯)."""
        return {}

    # ── crisis: 3거래일 확정 (E 하이브리드) ──

    def test_crisis_day1_not_confirmed(self):
        """빈 state에서 'crisis' 1회 → current 'neutral' 유지(미확정)."""
        state = self._fresh()
        result = _apply_regime_debounce(state, "crisis", "2026-06-01")
        self.assertEqual(result["current"], "neutral")   # 아직 neutral
        self.assertEqual(result["pending_regime"], "crisis")
        self.assertEqual(result["debounce_count"], 1)
        self.assertFalse(result["confirmed"])

    def test_crisis_day2_still_pending(self):
        """1일차 pending state에서 다른 날 'crisis' 재입력 → 아직 미확정(2일차, 3일 필요)."""
        state = {
            "current": "neutral",
            "debounce_count": 1,
            "pending_regime": "crisis",
            "days_in_regime": 0,
            "last_updated": "2026-06-01",
        }
        result = _apply_regime_debounce(state, "crisis", "2026-06-02")
        self.assertEqual(result["current"], "neutral")   # 아직 neutral
        self.assertEqual(result["debounce_count"], 2)
        self.assertFalse(result["confirmed"])

    def test_crisis_day3_confirmed(self):
        """2일차 pending state에서 다른 날 'crisis' 재입력 → 3일차 'crisis' 확정."""
        state = {
            "current": "neutral",
            "debounce_count": 2,
            "pending_regime": "crisis",
            "days_in_regime": 0,
            "last_updated": "2026-06-02",
        }
        result = _apply_regime_debounce(state, "crisis", "2026-06-03")
        self.assertEqual(result["current"], "crisis")
        self.assertIsNone(result["pending_regime"])
        self.assertTrue(result["confirmed"])

    # ── offensive: 8거래일 확정 (E 하이브리드) ──

    def test_offensive_7days_not_confirmed(self):
        """neutral → 'offensive' 7일차 → 아직 미확정(8일 필요)."""
        state = {
            "current": "neutral",
            "debounce_count": 7,
            "pending_regime": "offensive",
            "days_in_regime": 6,
            "last_updated": "2026-06-07",
        }
        result = _apply_regime_debounce(state, "offensive", "2026-06-08")
        # 7+1=8일 — threshold == 8, 확정
        self.assertEqual(result["current"], "offensive")
        self.assertTrue(result["confirmed"])

    def test_offensive_6days_still_pending(self):
        """neutral → 'offensive' 6일차 → 아직 미확정, 7일차 이상 필요."""
        state = {
            "current": "neutral",
            "debounce_count": 5,
            "pending_regime": "offensive",
            "days_in_regime": 4,
            "last_updated": "2026-06-06",
        }
        result = _apply_regime_debounce(state, "offensive", "2026-06-07")
        self.assertEqual(result["current"], "neutral")  # 아직 neutral
        self.assertFalse(result["confirmed"])

    # ── neutral: 즉시(1회) 확정 ──

    def test_neutral_immediate_from_crisis(self):
        """crisis → 'neutral' 1회 → 즉시 확정."""
        state = {
            "current": "crisis",
            "debounce_count": 5,
            "pending_regime": None,
            "days_in_regime": 3,
            "last_updated": "2026-06-01",
        }
        result = _apply_regime_debounce(state, "neutral", "2026-06-02")
        self.assertEqual(result["current"], "neutral")
        self.assertTrue(result["confirmed"])

    def test_neutral_immediate_from_offensive(self):
        """offensive → 'neutral' 1회 → 즉시 확정."""
        state = {
            "current": "offensive",
            "debounce_count": 7,
            "pending_regime": None,
            "days_in_regime": 5,
            "last_updated": "2026-06-01",
        }
        result = _apply_regime_debounce(state, "neutral", "2026-06-03")
        self.assertEqual(result["current"], "neutral")
        self.assertTrue(result["confirmed"])

    # ── same_day 중복 누적 금지 ──

    def test_same_day_no_double_count(self):
        """last_updated == today 면 debounce_count 증가 없음."""
        state = {
            "current": "neutral",
            "debounce_count": 1,
            "pending_regime": "crisis",
            "days_in_regime": 0,
            "last_updated": "2026-06-05",
        }
        # 같은 날 2회 호출 — count 불변
        result1 = _apply_regime_debounce(state, "crisis", "2026-06-05")
        self.assertEqual(result1["debounce_count"], 1)   # 그대로 1
        # 다른 날 → 증가
        result2 = _apply_regime_debounce(state, "crisis", "2026-06-06")
        self.assertEqual(result2["debounce_count"], 2)

    def test_same_day_current_no_double_count(self):
        """현재 레짐과 동일 신호, 같은 날 → days_in_regime 불변."""
        state = {
            "current": "offensive",
            "debounce_count": 3,
            "pending_regime": None,
            "days_in_regime": 5,
            "last_updated": "2026-06-05",
        }
        result = _apply_regime_debounce(state, "offensive", "2026-06-05")
        self.assertEqual(result["days_in_regime"], 5)   # 불변


class TestPctRank(unittest.TestCase):
    """_pct_rank 백분위 계산."""

    def test_last_is_max(self):
        """1~100 시리즈 마지막(100) → 100%ile."""
        series = list(range(1, 101))
        result = _pct_rank(series, 252)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 100.0, places=0)

    def test_last_is_min(self):
        """마지막이 최솟값 → 낮은 %ile."""
        series = list(range(50, 150)) + [49]
        result = _pct_rank(series, 252)
        self.assertIsNotNone(result)
        self.assertLess(result, 5.0)

    def test_too_short_returns_none(self):
        """30개 미만 → None."""
        result = _pct_rank(list(range(29)), 252)
        self.assertIsNone(result)

    def test_exactly_30_not_none(self):
        """정확히 30개 → None 아님."""
        result = _pct_rank(list(range(30)), 252)
        self.assertIsNotNone(result)

    def test_lookback_window_applied(self):
        """lookback 크면 전체 사용, 작으면 최근 window만."""
        long_series = [50.0] * 200 + [100.0]
        # lookback=10 → 마지막 10개: [50,50,...,50,100] → 100은 최대
        result = _pct_rank(long_series, 10)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 100.0, places=0)


class TestRealizedVolSeries(unittest.TestCase):
    """_realized_vol_series 롤링 실현변동성 시리즈."""

    def test_too_short_returns_empty(self):
        """window+2 미만 → []."""
        closes = [100.0] * 20   # window=20 → 필요: 22개, 20개 → 빈 리스트
        result = _realized_vol_series(closes, window=20)
        self.assertEqual(result, [])

    def test_barely_short_returns_empty(self):
        """정확히 window+1개 → []."""
        closes = [100.0] * 21   # window=20 → 필요: 22개
        result = _realized_vol_series(closes, window=20)
        self.assertEqual(result, [])

    def test_normal_input_positive_list(self):
        """충분한 입력 → 양수 값의 리스트 반환, 길이 확인."""
        import numpy as np
        np.random.seed(0)
        closes = list(np.exp(np.cumsum(np.random.randn(60) * 0.01)) * 100)
        result = _realized_vol_series(closes, window=20)
        self.assertGreater(len(result), 0)
        for v in result:
            self.assertGreater(v, 0.0)

    def test_flat_prices_near_zero_vol(self):
        """종가 모두 동일 → 변동성 ≈ 0."""
        closes = [100.0] * 50
        result = _realized_vol_series(closes, window=20)
        self.assertGreater(len(result), 0)
        for v in result:
            self.assertAlmostEqual(v, 0.0, places=3)

    def test_result_length(self):
        """len(result) = len(closes) - window (log_ret 길이 - window + 1)."""
        closes = [100.0 + i * 0.1 for i in range(50)]
        window = 20
        result = _realized_vol_series(closes, window=window)
        # log_ret has len(closes)-1 = 49 rows
        # rolling window from index window-1 to 48 → 49 - window + 1 = 30
        expected_len = len(closes) - 1 - window + 1
        self.assertEqual(len(result), expected_len)


class TestDistFromMa(unittest.TestCase):
    """_dist_from_ma (종가 - SMAw) / SMAw * 100."""

    def test_too_short_returns_none(self):
        """w보다 짧으면 None."""
        result = _dist_from_ma([100.0] * 50, w=200)
        self.assertIsNone(result)

    def test_flat_series_returns_near_zero(self):
        """모두 동일 종가 → 0."""
        closes = [200.0] * 200
        result = _dist_from_ma(closes, w=200)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 0.0, places=2)

    def test_rising_above_ma(self):
        """마지막 종가가 SMA200 보다 높음 → 양수."""
        closes = [100.0] * 200 + [120.0]
        result = _dist_from_ma(closes, w=200)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0.0)

    def test_falling_below_ma(self):
        """마지막 종가가 SMA200 보다 낮음 → 음수."""
        closes = [100.0] * 200 + [80.0]
        result = _dist_from_ma(closes, w=200)
        self.assertIsNotNone(result)
        self.assertLess(result, 0.0)

    def test_exact_w_length_uses_all(self):
        """len(closes)==w 이면 None 아님 (경계 확인)."""
        closes = [100.0] * 200
        result = _dist_from_ma(closes, w=200)
        self.assertIsNotNone(result)


class TestCmdRegimeOverrideMarket(unittest.TestCase):
    """cmd_regime(mode='override', market=...) — 비동기, 네트워크 없음."""

    def setUp(self):
        save_json("/tmp/test_data/regime_state.json",
                  {"kr": {}, "us": {}, "history": [], "current": {"current": "neutral"}})

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_override_kr_only(self):
        """market='kr' → state['kr']['current']='crisis', state['us'] 무변화."""
        asyncio.run(
            cmd_regime(mode="override", regime="crisis", market="kr", reason="테스트"))
        state = load_json("/tmp/test_data/regime_state.json")
        self.assertEqual(state["kr"]["current"], "crisis")
        # us는 그대로 — 초기 빈 dict 이므로 'current' 키 없거나 변경 없음
        self.assertNotEqual(state.get("us", {}).get("current"), "crisis")

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_override_us_only(self):
        """market='us' → state['us']['current']='offensive', state['kr'] 무변화."""
        asyncio.run(
            cmd_regime(mode="override", regime="offensive", market="us"))
        state = load_json("/tmp/test_data/regime_state.json")
        self.assertEqual(state["us"]["current"], "offensive")
        self.assertNotEqual(state.get("kr", {}).get("current"), "offensive")

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_override_both(self):
        """market='both' → kr + us 모두 설정."""
        result = asyncio.run(
            cmd_regime(mode="override", regime="neutral", market="both"))
        state = load_json("/tmp/test_data/regime_state.json")
        self.assertEqual(state["kr"]["current"], "neutral")
        self.assertEqual(state["us"]["current"], "neutral")
        self.assertEqual(result["mode"], "override")
        self.assertEqual(result["market"], "both")

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_override_invalid_regime_returns_error(self):
        """잘못된 regime → {'error': ...} 반환."""
        result = asyncio.run(
            cmd_regime(mode="override", regime="bullish", market="kr"))
        self.assertIn("error", result)

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_override_sets_current_mirror(self):
        """override 후 state['current']['current']도 갱신됨 (US 미러)."""
        asyncio.run(
            cmd_regime(mode="override", regime="crisis", market="us"))
        state = load_json("/tmp/test_data/regime_state.json")
        self.assertEqual(state["current"]["current"], "crisis")

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_override_regime_emoji_in_result(self):
        """반환값 'regime' 필드에 레짐 이모지 포함."""
        result = asyncio.run(
            cmd_regime(mode="override", regime="offensive"))
        self.assertIn("탐욕", result["regime"])


class TestKrCrisisDirectionGate(unittest.TestCase):
    """calc_kr_regime — 극단 vol 방향 게이트 (E 하이브리드 v2, 2026-07-17)."""

    def _run(self, vol_pct, ma_dist):
        import kis_api.regime as rg
        with patch.object(rg, "_fdr_closes", return_value=[100.0] * 300), \
             patch.object(rg, "_realized_vol_series", return_value=[10.0] * 260), \
             patch.object(rg, "_pct_rank", return_value=vol_pct), \
             patch.object(rg, "_dist_from_ma", return_value=ma_dist):
            return rg.calc_kr_regime()

    def test_meltup_extreme_vol_not_crisis(self):
        """2026-07-16 실사례: 상방 멜트업 극단 vol → 🔴 아님, 🟡 과열."""
        r = self._run(96.8, 30.84)
        self.assertEqual(r["regime_en"], "neutral")
        self.assertIn("과열", r["logic"])

    def test_downside_extreme_vol_is_crisis(self):
        """하락 맥락의 극단 vol → 🔴 유지 (2008/2020형)."""
        r = self._run(95.0, -5.0)
        self.assertEqual(r["regime_en"], "crisis")

    def test_trend_gate_crisis(self):
        """80%ile 초과 + 200MA -3% 이하 → 🔴 유지."""
        r = self._run(85.0, -4.0)
        self.assertEqual(r["regime_en"], "crisis")

    def test_mid_vol_shallow_dip_is_neutral_not_offensive(self):
        """v1 갭 버그 구간(vol 80~92 & ma -3~0): 🟢로 새면 안 됨 → 🟡."""
        r = self._run(85.0, -1.0)
        self.assertEqual(r["regime_en"], "neutral")

    def test_calm_uptrend_offensive(self):
        """평온 + 상승 추세 → 🟢 유지."""
        r = self._run(30.0, 5.0)
        self.assertEqual(r["regime_en"], "offensive")

    def test_extreme_bypass_shallow_dip_is_crisis(self):
        """극단우회(vol>92 & ma<0) 단독 격리 검증.

        추세게이트(vol>80 & ma<-3)는 ma_dist=-1.0이라 미충족 —
        오직 극단우회 조건만으로 crisis가 되는지 확인 (방향게이트 자체 검증).
        """
        r = self._run(95.0, -1.0)
        self.assertEqual(r["regime_en"], "crisis")

    def test_extreme_vol_at_exact_zero_ma_is_not_crisis(self):
        """ma_dist<0 strict 경계: ma_dist=0.0은 극단우회 미충족 → crisis 아님.

        off-by-one(<=0) 회귀 방어 — 0.0은 하락 맥락이 아니므로 🔴로 새면 안 됨.
        """
        r = self._run(95.0, 0.0)
        self.assertEqual(r["regime_en"], "neutral")

    def test_high_vol_at_exact_zero_ma_is_overheat_neutral(self):
        """ma_dist>=0 경계: vol=85(>80), ma_dist=0.0 → 과열 분기 🟡 Neutral."""
        r = self._run(85.0, 0.0)
        self.assertEqual(r["regime_en"], "neutral")
        self.assertIn("과열", r["logic"])


class TestYfHistoryThreadSafety(unittest.TestCase):
    """_yf_history — yf.download() 전역공유상태(shared._DFS/_ERRORS) 비스레드안전 회귀 방지.

    2026-09-04 사고: KR/US 레짐이 asyncio.to_thread로 동시 실행되며 yf.download()의
    모듈 전역 dict를 공유해 심볼 간 DataFrame이 뒤섞임(VIX↔VIX3M 값 교차오염) →
    US 레짐 offensive→neutral 오전환. yf.Ticker(symbol).history()는 전역 공유 상태가
    없어 스레드 안전 — download()를 더 이상 호출하지 않음을 고정한다.
    """

    def test_yf_history_uses_ticker_history_not_download(self):
        import kis_api.news as news_mod

        def _fail_if_download_called(*a, **kw):
            raise AssertionError("_yf_history가 yf.download()를 호출함 — 스레드 비안전 경로 회귀")

        import pandas as pd
        mock_yf = MagicMock()
        mock_yf.download.side_effect = _fail_if_download_called
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame({"Close": [1.0, 2.0, 3.0]})
        mock_yf.Ticker.return_value = mock_ticker

        with patch.dict(sys.modules, {"yfinance": mock_yf}):
            result = news_mod._yf_history("^VIX", "2y")

        self.assertEqual(result, [1.0, 2.0, 3.0])
        mock_yf.download.assert_not_called()
        mock_yf.Ticker.assert_called_once_with("^VIX")
        mock_ticker.history.assert_called_once_with(period="2y", auto_adjust=True)


class TestCmdRegimeDataUnavailable(unittest.TestCase):
    """calc_kr/us_regime이 data_unavailable을 반환하면 cmd_regime이 기존 state를 보존.

    2026-09-04 사고 재발방지: 지표 조회가 실패한 시장은 디바운스 재계산·state 덮어쓰기를
    건너뛰고 직전 확정 레짐을 그대로 유지해야 함 (오전환 방지). 판정/디바운스 임계값
    자체는 무변경 — 실패 시 '재계산을 아예 하지 않는다'는 게이트만 추가됨.
    """

    def setUp(self):
        self.state_file = "/tmp/test_data/regime_state.json"
        self.prev_state = {
            "kr": {"current": "neutral", "days_in_regime": 3, "debounce_count": 3,
                   "pending_regime": None, "confirmed": True, "last_updated": "2026-09-02",
                   "cash_posture": "평상 5~8%",
                   "indicators": {"vol_pct": 40.0, "vol_abs": 12.0, "ma_dist": 2.0,
                                  "usdkrw_chg60": 1.0, "foreign_5d": 100}},
            "us": {"current": "offensive", "days_in_regime": 5, "debounce_count": 8,
                   "pending_regime": None, "confirmed": True, "last_updated": "2026-09-02",
                   "cash_posture": "평상 5~8%",
                   "indicators": {"sp_dist": 8.56, "sp_slope": "rising", "vix_val": 14.32,
                                  "vix_pct": 2.8, "vix3m": 16.0, "backwardation": False,
                                  "term_ratio": 0.9}},
            "history": [],
            "current": {"current": "offensive", "days_in_regime": 5, "debounce_count": 8,
                        "confirmed": True, "pending_regime": None,
                        "last_updated": "2026-09-02", "indicators": {"vix": {"value": 14.32}}},
            "prev_regime": "offensive",
        }
        save_json(self.state_file, self.prev_state)

    @staticmethod
    def _unavailable_us():
        return {
            "market": "US", "regime_en": "neutral", "regime": "🟡 중립",
            "cash_posture": "경계 8~15% (실탄 비축)",
            "indicators": {"sp_dist": None, "sp_slope": None, "vix_val": None,
                           "vix_pct": None, "vix3m": None, "backwardation": False,
                           "term_ratio": None},
            "data_unavailable": True,
            "logic": "지표 조회 실패 — 이전 상태 유지",
            "_compat_indicators": {"sp500_vs_200ma": {}, "vix": {}},
        }

    @staticmethod
    def _unavailable_kr():
        return {
            "market": "KR", "regime_en": "neutral", "regime": "🟡 중립",
            "cash_posture": "경계 8~15% (실탄 비축)",
            "indicators": {"vol_pct": None, "vol_abs": None, "ma_dist": None,
                           "usdkrw_chg60": None, "foreign_5d": None},
            "confirmations": {},
            "data_unavailable": True,
            "logic": "지표 조회 실패 — 이전 상태 유지",
        }

    @staticmethod
    def _available_kr(regime_en="offensive"):
        return {
            "market": "KR", "regime_en": regime_en, "regime": "🟢 탐욕",
            "cash_posture": "평상 5~8%",
            "indicators": {"vol_pct": 35.0, "vol_abs": 11.0, "ma_dist": 3.0,
                           "usdkrw_chg60": 0.5, "foreign_5d": 200},
            "confirmations": {},
            "logic": "test-kr-available",
        }

    @staticmethod
    def _available_us(regime_en="offensive"):
        return {
            "market": "US", "regime_en": regime_en, "regime": "🟢 탐욕",
            "cash_posture": "평상 5~8%",
            "indicators": {"sp_dist": 9.0, "sp_slope": "rising", "vix_val": 13.0,
                           "vix_pct": 3.0, "vix3m": 15.0, "backwardation": False,
                           "term_ratio": 0.87},
            "logic": "test-us-available",
            "_compat_indicators": {"sp500_vs_200ma": {}, "vix": {}},
        }

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_us_data_unavailable_preserves_state(self):
        """(a) calc_us_regime이 data_unavailable → 기존 state['us'] 보존, history 당일
        row도 이전 값으로 채워짐(None 아님), state['current']['current'] 불변.
        W1/W3/W4(b) 확장: res['us'] 서브딕트도 보존값 노출, history row에
        data_unavailable=['us'] 마커, unavailable_streak 1회 누적."""
        import kis_api.regime as rg
        with patch.object(rg, "calc_us_regime", return_value=self._unavailable_us()), \
             patch.object(rg, "calc_kr_regime", return_value=self._available_kr()):
            result = asyncio.run(cmd_regime(mode="current"))

        state = load_json(self.state_file)
        # 기존 US state 그대로 (offensive, 5일차) — neutral로 오전환되지 않음
        self.assertEqual(state["us"]["current"], "offensive")
        self.assertEqual(state["us"]["days_in_regime"], 5)
        self.assertEqual(state["us"]["indicators"]["sp_dist"], 8.56)
        today = datetime.now(KST).strftime("%Y-%m-%d")
        today_row = next(h for h in state["history"] if h["date"] == today)
        self.assertEqual(today_row["us"], "offensive")
        self.assertIsNotNone(today_row["us_sp_dist"])
        self.assertEqual(today_row["us_sp_dist"], 8.56)
        # 상위 백워드호환 current 미러도 불변
        self.assertEqual(state["current"]["current"], "offensive")
        self.assertEqual(state["prev_regime"], "offensive")
        # W1: res["us"] 서브딕트가 실패 placeholder("neutral")가 아니라 보존값 노출
        self.assertEqual(result["us"]["regime_en"], "offensive")
        self.assertIn("탐욕", result["us"]["regime"])
        self.assertEqual(result["us"]["cash_posture"], "평상 5~8%")
        # W3: history row에 스테일 마커
        self.assertEqual(today_row.get("data_unavailable"), ["us"])
        # W4(b): unavailable_streak 1회 누적 (오늘 최초 실패)
        self.assertEqual(state["us"].get("unavailable_streak"), 1)
        self.assertEqual(state["us"].get("unavailable_date"), today)

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_us_unavailable_streak_no_double_count_same_day(self):
        """W4(b): 같은 날 두 번 호출해도 unavailable_streak이 2로 늘지 않음."""
        import kis_api.regime as rg
        with patch.object(rg, "calc_us_regime", return_value=self._unavailable_us()), \
             patch.object(rg, "calc_kr_regime", return_value=self._available_kr()):
            asyncio.run(cmd_regime(mode="current"))
            asyncio.run(cmd_regime(mode="current"))

        state = load_json(self.state_file)
        self.assertEqual(state["us"].get("unavailable_streak"), 1)

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_kr_data_unavailable_preserves_state(self):
        """(d) KR 대칭 케이스 — calc_kr_regime이 data_unavailable이면 기존 KR state 보존.
        W3/W4(b) 확장: history row data_unavailable=['kr'], unavailable_streak 누적."""
        import kis_api.regime as rg
        with patch.object(rg, "calc_kr_regime", return_value=self._unavailable_kr()), \
             patch.object(rg, "calc_us_regime", return_value=self._available_us()):
            asyncio.run(cmd_regime(mode="current"))

        state = load_json(self.state_file)
        # 기존 KR state 그대로 — days_in_regime 재확정(3→4) 안 되고 3 유지
        self.assertEqual(state["kr"]["current"], "neutral")
        self.assertEqual(state["kr"]["days_in_regime"], 3)
        self.assertEqual(state["kr"]["indicators"]["vol_pct"], 40.0)
        today = datetime.now(KST).strftime("%Y-%m-%d")
        today_row = next(h for h in state["history"] if h["date"] == today)
        self.assertEqual(today_row["kr"], "neutral")
        self.assertEqual(today_row["kr_vol_pct"], 40.0)
        # US는 정상 갱신됨 (KR만 게이팅됨을 대칭 확인)
        self.assertEqual(state["us"]["indicators"]["sp_dist"], 9.0)
        # W3: history row에 스테일 마커 (kr만)
        self.assertEqual(today_row.get("data_unavailable"), ["kr"])
        # W4(b): unavailable_streak 1회 누적
        self.assertEqual(state["kr"].get("unavailable_streak"), 1)
        self.assertEqual(state["kr"].get("unavailable_date"), today)

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_both_available_state_updates_normally(self):
        """(b) 양쪽 다 정상 계산이면 기존처럼 새 indicators로 state가 갱신됨 (회귀 없음).
        정상 경로에서 unavailable_streak/unavailable_date/history의 data_unavailable
        키가 전혀 생기지 않는지도 함께 확인 (키셋 불변 회귀 방지)."""
        import kis_api.regime as rg
        with patch.object(rg, "calc_us_regime", return_value=self._available_us()), \
             patch.object(rg, "calc_kr_regime", return_value=self._available_kr()):
            asyncio.run(cmd_regime(mode="current"))

        state = load_json(self.state_file)
        self.assertEqual(state["us"]["indicators"]["sp_dist"], 9.0)
        self.assertEqual(state["kr"]["indicators"]["vol_pct"], 35.0)
        # 정상 경로 키셋 불변 — unavailable_streak/unavailable_date 없어야 함
        self.assertNotIn("unavailable_streak", state["us"])
        self.assertNotIn("unavailable_date", state["us"])
        self.assertNotIn("unavailable_streak", state["kr"])
        self.assertNotIn("unavailable_date", state["kr"])
        today = datetime.now(KST).strftime("%Y-%m-%d")
        today_row = next(h for h in state["history"] if h["date"] == today)
        self.assertNotIn("data_unavailable", today_row)


class TestB1MissingGateOR(unittest.TestCase):
    """B1: 결측 게이트 AND→OR. offensive/crisis 판정에 두 입력이 함께 필요하므로
    하나만 결측이어도 data_unavailable이어야 함 (기존 AND는 둘 다 없어야만 게이트됨)."""

    def test_us_gspc_only_missing_triggers_unavailable(self):
        """^GSPC만 실패(빈 리스트) → sp_dist=None, vix_pct는 정상이어도 data_unavailable."""
        import numpy as np
        np.random.seed(1)
        vix_series = list(np.random.randn(300) * 5 + 20)

        def mock_yf(symbol, period="2y"):
            if symbol == "^VIX":
                return vix_series
            return []  # ^GSPC, ^VIX3M, ^VIX9D 전부 실패

        import kis_api.regime as rg
        with patch.object(rg, "_yf_history", side_effect=mock_yf):
            result = rg.calc_us_regime()

        self.assertTrue(result.get("data_unavailable"))
        self.assertIsNone(result["indicators"]["sp_dist"])
        self.assertIsNotNone(result["indicators"]["vix_pct"])

    def test_us_vix_only_missing_triggers_unavailable(self):
        """^VIX만 실패(빈 리스트) → vix_pct=None, sp_dist는 정상이어도 data_unavailable."""
        sp_series = [float(4000 + i) for i in range(260)]

        def mock_yf(symbol, period="2y"):
            if symbol == "^GSPC":
                return sp_series
            return []  # ^VIX 및 파생(^VIX3M/^VIX9D) 전부 실패

        import kis_api.regime as rg
        with patch.object(rg, "_yf_history", side_effect=mock_yf):
            result = rg.calc_us_regime()

        self.assertTrue(result.get("data_unavailable"))
        self.assertIsNotNone(result["indicators"]["sp_dist"])
        self.assertIsNone(result["indicators"]["vix_pct"])

    def test_kr_vol_pct_only_missing_triggers_unavailable(self):
        """KR 주신호 vol_pct만 결측이어도(ma_dist는 정상) data_unavailable이어야 함."""
        import kis_api.regime as rg
        with patch.object(rg, "_fdr_closes", return_value=[100.0] * 300), \
             patch.object(rg, "_realized_vol_series", return_value=[10.0] * 260), \
             patch.object(rg, "_pct_rank", return_value=None), \
             patch.object(rg, "_dist_from_ma", return_value=5.0):
            result = rg.calc_kr_regime()

        self.assertTrue(result.get("data_unavailable"))
        self.assertIsNone(result["indicators"]["vol_pct"])
        self.assertIsNotNone(result["indicators"]["ma_dist"])

    def test_kr_ma_dist_only_missing_triggers_unavailable(self):
        """KR ma_dist만 결측이어도(vol_pct는 정상) data_unavailable이어야 함."""
        import kis_api.regime as rg
        with patch.object(rg, "_fdr_closes", return_value=[100.0] * 300), \
             patch.object(rg, "_realized_vol_series", return_value=[10.0] * 260), \
             patch.object(rg, "_pct_rank", return_value=40.0), \
             patch.object(rg, "_dist_from_ma", return_value=None):
            result = rg.calc_kr_regime()

        self.assertTrue(result.get("data_unavailable"))
        self.assertIsNotNone(result["indicators"]["vol_pct"])
        self.assertIsNone(result["indicators"]["ma_dist"])


class TestW1SubdictReflectsPreservedState(unittest.TestCase):
    """W1: 결측 시장의 res[mkt] 서브딕트(regime_en/regime/cash_posture)가 실패
    placeholder("neutral" 등)가 아니라 보존된 state 값을 노출해야 함 — 안 그러면
    상위(top-level, US 미러) 백워드호환 값과 res["kr"]/res["us"]가 서로 모순됨."""

    def setUp(self):
        self.state_file = "/tmp/test_data/regime_state.json"
        self.prev_state = {
            "kr": {"current": "crisis", "days_in_regime": 4, "debounce_count": 4,
                   "pending_regime": None, "confirmed": True, "last_updated": "2026-09-03",
                   "cash_posture": "🔴 발사 — 풀투자 지향(현금 최소)",
                   "indicators": {"vol_pct": 95.0, "vol_abs": 40.0, "ma_dist": -12.0,
                                  "usdkrw_chg60": 6.0, "foreign_5d": -25000}},
            "us": {"current": "offensive", "days_in_regime": 5, "debounce_count": 8,
                   "pending_regime": None, "confirmed": True, "last_updated": "2026-09-03",
                   "cash_posture": "평상 5~8%",
                   "indicators": {"sp_dist": 8.0, "sp_slope": "rising", "vix_val": 13.0,
                                  "vix_pct": 3.0, "vix3m": 15.0, "backwardation": False,
                                  "term_ratio": 0.87}},
            "history": [],
            "current": {"current": "offensive", "days_in_regime": 5, "debounce_count": 8,
                        "confirmed": True, "pending_regime": None,
                        "last_updated": "2026-09-03", "indicators": {}},
            "prev_regime": "offensive",
        }
        save_json(self.state_file, self.prev_state)

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_kr_unavailable_subdict_shows_preserved_crisis(self):
        import kis_api.regime as rg
        kr_unavail = {
            "market": "KR", "regime_en": "neutral", "regime": "🟡 중립",
            "cash_posture": "경계 8~15% (실탄 비축)",
            "indicators": {"vol_pct": None, "vol_abs": None, "ma_dist": None,
                           "usdkrw_chg60": None, "foreign_5d": None},
            "confirmations": {},
            "data_unavailable": True,
            "logic": "지표 조회 실패 — 이전 상태 유지",
        }
        us_available = {
            "market": "US", "regime_en": "offensive", "regime": "🟢 탐욕",
            "cash_posture": "평상 5~8%",
            "indicators": {"sp_dist": 8.5, "sp_slope": "rising", "vix_val": 13.5,
                           "vix_pct": 3.5, "vix3m": 15.5, "backwardation": False,
                           "term_ratio": 0.88},
            "logic": "test-us-available",
            "_compat_indicators": {"sp500_vs_200ma": {}, "vix": {}},
        }
        with patch.object(rg, "calc_kr_regime", return_value=kr_unavail), \
             patch.object(rg, "calc_us_regime", return_value=us_available):
            result = asyncio.run(cmd_regime(mode="current"))

        # W1: res["kr"]가 실패 placeholder("neutral")가 아니라 보존값("crisis") 노출
        self.assertEqual(result["kr"]["regime_en"], "crisis")
        self.assertIn("공포", result["kr"]["regime"])
        self.assertEqual(result["kr"]["cash_posture"], "🔴 발사 — 풀투자 지향(현금 최소)")
        # data_unavailable/logic은 calc_kr_regime이 반환한 그대로 유지
        self.assertTrue(result["kr"]["data_unavailable"])
        self.assertEqual(result["kr"]["logic"], "지표 조회 실패 — 이전 상태 유지")
        # US는 정상 계산 + 보존값과 일치 (동일 신호 반복 → 디바운스 유지)
        self.assertEqual(result["us"]["regime_en"], "offensive")


class TestW2IndicatorsNoneNoCrash(unittest.TestCase):
    """W2: state[mkt]["indicators"]가 명시적으로 None이어도 cmd_regime이 예외 없이
    반환해야 함 (.get("indicators", {})는 키가 있고 값이 None이면 방어되지 않음)."""

    def setUp(self):
        self.state_file = "/tmp/test_data/regime_state.json"
        save_json(self.state_file, {
            "kr": {"current": "neutral", "days_in_regime": 1, "debounce_count": 1,
                   "pending_regime": None, "confirmed": True, "last_updated": "2026-09-03",
                   "cash_posture": "경계 8~15% (실탄 비축)",
                   "indicators": {"vol_pct": 40.0, "vol_abs": 12.0, "ma_dist": 2.0,
                                  "usdkrw_chg60": 1.0, "foreign_5d": 100}},
            "us": {"current": "neutral", "days_in_regime": 1, "debounce_count": 1,
                   "pending_regime": None, "confirmed": True, "last_updated": "2026-09-03",
                   "cash_posture": "경계 8~15% (실탄 비축)",
                   "indicators": None},
            "history": [],
            "current": {"current": "neutral", "days_in_regime": 1, "debounce_count": 1,
                        "confirmed": True, "pending_regime": None,
                        "last_updated": "2026-09-03", "indicators": None},
            "prev_regime": "neutral",
        })

    @patch("kis_api.regime.REGIME_STATE_FILE", "/tmp/test_data/regime_state.json")
    def test_us_indicators_none_no_crash(self):
        import kis_api.regime as rg
        us_unavail = {
            "market": "US", "regime_en": "neutral", "regime": "🟡 중립",
            "cash_posture": "경계 8~15% (실탄 비축)",
            "indicators": {"sp_dist": None, "sp_slope": None, "vix_val": None,
                           "vix_pct": None, "vix3m": None, "backwardation": False,
                           "term_ratio": None},
            "data_unavailable": True,
            "logic": "지표 조회 실패 — 이전 상태 유지",
            "_compat_indicators": {"sp500_vs_200ma": {}, "vix": {}},
        }
        kr_available = {
            "market": "KR", "regime_en": "neutral", "regime": "🟡 중립",
            "cash_posture": "경계 8~15% (실탄 비축)",
            "indicators": {"vol_pct": 45.0, "vol_abs": 13.0, "ma_dist": 1.0,
                           "usdkrw_chg60": 0.2, "foreign_5d": -100},
            "confirmations": {},
            "logic": "test-kr-available",
        }
        with patch.object(rg, "calc_us_regime", return_value=us_unavail), \
             patch.object(rg, "calc_kr_regime", return_value=kr_available):
            try:
                result = asyncio.run(cmd_regime(mode="current"))
            except Exception as e:
                self.fail(f"cmd_regime이 indicators=None 상황에서 예외를 던짐: {e}")

        self.assertIsInstance(result, dict)
        self.assertIn("us", result)
        self.assertIn("kr", result)


class TestYfLockShared(unittest.TestCase):
    """W5: _yf_history가 kis_api._helpers.YF_LOCK(모든 yfinance 호출 지점 공용 락)을
    획득하는지 확인 — 모듈 속성 자체를 목으로 교체해 런타임 패치 가능함을 고정."""

    def test_yf_history_acquires_shared_yf_lock(self):
        import kis_api.news as news_mod
        import kis_api._helpers as helpers_mod
        import pandas as pd

        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)

        mock_yf = MagicMock()
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame({"Close": [1.0, 2.0]})
        mock_yf.Ticker.return_value = mock_ticker

        with patch.object(helpers_mod, "YF_LOCK", mock_lock), \
             patch.dict(sys.modules, {"yfinance": mock_yf}):
            result = news_mod._yf_history("^VIX", "2y")

        self.assertEqual(result, [1.0, 2.0])
        mock_lock.__enter__.assert_called_once()
        mock_lock.__exit__.assert_called_once()


class TestRegimeTransitionUnavailableWarning(unittest.TestCase):
    """W4(c): regime_transition_alert가 unavailable_streak>=1인 시장에 대해 하루 1회
    "조회 실패 N일째 — 이전 상태 유지" 경고를 텔레그램 메시지에 포함해야 함
    (무음 동결 방지 — 사용자가 봇 침묵을 실패로 오인하지 않도록)."""

    def setUp(self):
        self.state_file = "/tmp/test_data/regime_state.json"
        self.trans_file = "/tmp/test_data/regime_transition_sent.json"
        save_json(self.state_file, {
            "kr": {"current": "offensive", "unavailable_streak": 3,
                   "unavailable_date": "2026-09-04",
                   "indicators": {"vol_pct": 20.0, "ma_dist": 5.0}},
            "us": {"current": "crisis",
                   "indicators": {"sp_dist": -5.0, "vix_pct": 95.0}},
        })
        # kr/us 모두 이미 "발송됨" 상태로 기록 — 전환 알림과 분리해 경고만 단독 검증
        save_json(self.trans_file, {"kr": "offensive", "us": "crisis"})

    @staticmethod
    def _make_context():
        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock(return_value=None)
        return ctx

    def test_unavailable_streak_warning_sent_once(self):
        import main_pkg.jobs.regime as jr
        with patch.object(jr, "REGIME_STATE_FILE", self.state_file):
            ctx = self._make_context()
            asyncio.run(jr.regime_transition_alert(ctx))

        self.assertEqual(ctx.bot.send_message.call_count, 1)
        sent_text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        self.assertIn("🇰🇷 KR 레짐 지표 조회 실패 3일째", sent_text)
        self.assertIn("이전 상태(🟢) 유지 중", sent_text)

        trans_sent = load_json(self.trans_file)
        today = datetime.now(KST).strftime("%Y-%m-%d")
        self.assertEqual(trans_sent.get("kr_unavail_warned"), today)

    def test_unavailable_streak_warning_not_repeated_same_day(self):
        import main_pkg.jobs.regime as jr
        with patch.object(jr, "REGIME_STATE_FILE", self.state_file):
            asyncio.run(jr.regime_transition_alert(self._make_context()))
            ctx2 = self._make_context()
            asyncio.run(jr.regime_transition_alert(ctx2))

        # 2차 호출은 오늘 이미 경고를 보냈으므로 추가 발송 없음 (전환도 없음 → 완전 무음)
        self.assertEqual(ctx2.bot.send_message.call_count, 0)


if __name__ == "__main__":
    unittest.main()
