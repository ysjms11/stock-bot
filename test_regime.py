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
    _regime_label, _REGIME_ORDER, apply_debounce,
    compute_turbulence, cmd_regime,
    load_json, save_json,
    REGIME_STATE_FILE,
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

    def test_override_crisis(self):
        result = asyncio.get_event_loop().run_until_complete(
            cmd_regime(mode="override", regime="crisis", reason="블랙스완"))
        self.assertIn("위기", result["regime"])
        self.assertEqual(result["mode"], "override")
        self.assertEqual(result["reason"], "블랙스완")
        # 파일에 저장되었는지 확인
        state = load_json(self.state_file)
        self.assertEqual(state["current"]["regime"], "defensive")
        self.assertTrue(state["current"]["override"])

    def test_override_invalid(self):
        result = asyncio.get_event_loop().run_until_complete(
            cmd_regime(mode="override", regime="invalid"))
        self.assertIn("error", result)

    def test_override_offensive(self):
        result = asyncio.get_event_loop().run_until_complete(
            cmd_regime(mode="override", regime="offensive", reason="강세전환"))
        self.assertIn("공격", result["regime"])


class TestHistoryMode(unittest.TestCase):
    """history 모드 테스트."""

    def setUp(self):
        history = [
            {"date": f"2026-03-{25+i:02d}", "combined_score": 50 + i, "regime": "neutral"}
            for i in range(5)
        ]
        save_json("/tmp/test_data/regime_state.json",
                  {"history": history, "current": {"regime": "neutral"}})

    def test_history_default(self):
        result = asyncio.get_event_loop().run_until_complete(
            cmd_regime(mode="history", days=3))
        self.assertEqual(len(result["history"]), 3)
        self.assertEqual(result["total_records"], 5)

    def test_history_all(self):
        result = asyncio.get_event_loop().run_until_complete(
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

        with patch("kis_api._yf_history", side_effect=mock_yf):
            result = asyncio.get_event_loop().run_until_complete(
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


if __name__ == "__main__":
    unittest.main()
