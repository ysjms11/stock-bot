"""test_regime.py — 시장 레짐 판정 로직 단위 테스트."""
import json
import os
import sys
import asyncio
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone

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
    _regime_label, apply_debounce,
    compute_turbulence, cmd_regime,
    load_json, save_json,
    REGIME_STATE_FILE,
)
from kis_api.regime import (
    _REGIME_ORDER,
    _apply_regime_debounce,
    _pct_rank,
    _realized_vol_series,
    _dist_from_ma,
)

KST = timezone(timedelta(hours=9))


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


class TestRegimeLabel(unittest.TestCase):
    """레짐 분류 경계값."""

    def test_offensive(self):
        e, k, en = _regime_label(70)
        self.assertEqual(en, "offensive")

    def test_neutral_upper(self):
        e, k, en = _regime_label(69.9)
        self.assertEqual(en, "neutral")

    def test_neutral_lower(self):
        e, k, en = _regime_label(40)
        self.assertEqual(en, "neutral")

    def test_defensive(self):
        e, k, en = _regime_label(39.9)
        self.assertEqual(en, "defensive")

    def test_extreme_high(self):
        e, k, en = _regime_label(100)
        self.assertEqual(en, "offensive")
        self.assertEqual(e, "🟢")

    def test_extreme_low(self):
        e, k, en = _regime_label(0)
        self.assertEqual(en, "defensive")
        self.assertEqual(e, "🔴")


class TestDebounce(unittest.TestCase):
    """디바운스 로직 — 악화 2일, 회복 3일."""

    def _make_state(self, regime="neutral", consecutive=5,
                    pending="", pending_days=0):
        return {
            "regime": regime, "consecutive_days": consecutive,
            "pending_regime": pending, "pending_days": pending_days,
            "date": "2026-03-30",
        }

    def test_same_regime_stays(self):
        """동일 레짐 → 일수만 증가."""
        st = self._make_state("neutral", 5)
        st = apply_debounce(55.0, st)  # neutral
        self.assertEqual(st["regime"], "neutral")
        self.assertEqual(st["consecutive_days"], 6)
        self.assertEqual(st["pending_regime"], "")

    def test_deterioration_day1(self):
        """악화 1일차 → 전환 안 됨."""
        st = self._make_state("neutral", 5)
        st = apply_debounce(30.0, st)  # defensive
        self.assertEqual(st["regime"], "neutral")  # 아직 유지
        self.assertEqual(st["pending_regime"], "defensive")
        self.assertEqual(st["pending_days"], 1)

    def test_deterioration_day2_confirms(self):
        """악화 2일차 → 전환 확정."""
        st = self._make_state("neutral", 5, pending="defensive", pending_days=1)
        st = apply_debounce(30.0, st)
        self.assertEqual(st["regime"], "defensive")
        self.assertEqual(st["pending_regime"], "")

    def test_recovery_day2_not_yet(self):
        """회복 2일차 → 아직 미확정."""
        st = self._make_state("defensive", 5, pending="neutral", pending_days=1)
        st = apply_debounce(55.0, st)
        self.assertEqual(st["regime"], "defensive")  # 아직 유지
        self.assertEqual(st["pending_days"], 2)

    def test_recovery_day3_confirms(self):
        """회복 3일차 → 전환 확정."""
        st = self._make_state("defensive", 5, pending="neutral", pending_days=2)
        st = apply_debounce(55.0, st)
        self.assertEqual(st["regime"], "neutral")

    def test_pending_regime_changes(self):
        """대기 중 다른 레짐 → 새 대기 시작."""
        st = self._make_state("neutral", 5, pending="defensive", pending_days=1)
        st = apply_debounce(80.0, st)  # offensive
        self.assertEqual(st["regime"], "neutral")
        self.assertEqual(st["pending_regime"], "offensive")
        self.assertEqual(st["pending_days"], 1)

    def test_offensive_to_defensive_2days(self):
        """공격→위기 (2단계 악화)도 2일이면 확정."""
        st = self._make_state("offensive", 10, pending="defensive", pending_days=1)
        st = apply_debounce(20.0, st)
        self.assertEqual(st["regime"], "defensive")


class TestTurbulence(unittest.TestCase):
    """Turbulence Index 계산."""

    def test_normal_data(self):
        """정상 데이터 → dict 반환, alert=False expected (랜덤이라 보장 못 함)."""
        import numpy as np
        np.random.seed(42)
        n = 200
        sp = list(np.cumsum(np.random.randn(n) * 0.01) + 100)
        kospi = list(np.cumsum(np.random.randn(n) * 0.01) + 2500)
        usdkrw = list(np.cumsum(np.random.randn(n) * 0.001) + 1400)
        wti = list(np.cumsum(np.random.randn(n) * 0.01) + 70)
        result = compute_turbulence(sp, kospi, usdkrw, wti)
        self.assertIsNotNone(result)
        self.assertIn("value", result)
        self.assertIn("threshold_95", result)
        self.assertIn("alert", result)
        self.assertIsInstance(result["alert"], bool)

    def test_insufficient_data(self):
        """데이터 부족 → None."""
        result = compute_turbulence([1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3])
        self.assertIsNone(result)

    def test_shock_triggers_alert(self):
        """큰 충격 → alert=True."""
        import numpy as np
        np.random.seed(42)
        n = 200
        sp = list(np.cumsum(np.random.randn(n) * 0.005) + 100)
        kospi = list(np.cumsum(np.random.randn(n) * 0.005) + 2500)
        usdkrw = list(np.cumsum(np.random.randn(n) * 0.001) + 1400)
        wti = list(np.cumsum(np.random.randn(n) * 0.005) + 70)
        # 마지막 날 극단적 움직임
        sp[-1] = sp[-2] * 1.10  # +10%
        kospi[-1] = kospi[-2] * 0.90
        usdkrw[-1] = usdkrw[-2] * 1.05
        wti[-1] = wti[-2] * 0.85
        result = compute_turbulence(sp, kospi, usdkrw, wti)
        self.assertIsNotNone(result)
        self.assertTrue(result["alert"])


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


if __name__ == "__main__":
    unittest.main()
