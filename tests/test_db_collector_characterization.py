"""db_collector 패키지 분해(2026-06)용 characterization 테스트 — 현재 동작을 골든값으로 고정.
분해 중 read-only; 실패=리팩토링이 동작을 바꿨다는 뜻.

골든값 생성 방법: 각 함수를 실제 호출하여 반환값을 직접 기록.
네트워크/DB 없는 순수(pure) 또는 in-memory-sqlite 함수만 대상.

커버리지 갭 대상:
  _spread_at, _rsi_at          — technicals (기존 테스트에 없음)
  _ma / _rsi / _atr edge       — 경계 케이스 보강
  _volatility_20d edge         — 경계 케이스 보강
  _is_kr_trading_day           — 주말/휴장/정상 판정
  _summarize_filters           — keys 필터링 동작
  _parse_period / _build_period / _prev_yoy_period — alpha 순수 헬퍼
  _safe_div / _pick_net_income — alpha 순수 헬퍼
  _div_num                     — 배당 파싱 헬퍼
  is_tier_s_analyst            — Tier S 판정 (3경로 OR)
"""

import pytest

from db_collector import (
    _spread_at,
    _rsi_at,
    _ma,
    _rsi,
    _macd,
    _atr,
    _volatility_20d,
    _is_kr_trading_day,
    _summarize_filters,
    _parse_period,
    _build_period,
    _prev_yoy_period,
    _safe_div,
    _pick_net_income,
    _div_num,
    is_tier_s_analyst,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _spread_at — (MA5-MA60)/MA60 × 100 at offset
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _desc100():
    """100-element newest-first list: 200.0, 199.5, ..., 150.5"""
    return [200.0 - i * 0.5 for i in range(100)]


def test_spread_at_offset0_golden():
    """offset=0, 100-element descending list."""
    assert round(_spread_at(_desc100(), 0), 6) == round(7.422402159244265, 6)


def test_spread_at_offset5_golden():
    """offset=5, 100-element list."""
    assert round(_spread_at(_desc100(), 5), 6) == round(7.523939808481532, 6)


def test_spread_at_flat_prices_zero():
    """Flat prices → MA5 == MA60 → spread = 0.0."""
    assert _spread_at([100.0] * 100, 0) == 0.0


def test_spread_at_ascending_prices_negative():
    """Ascending prices (newest=lowest) → MA5 < MA60 → negative spread."""
    closes_asc = [50.0 + i * 1.0 for i in range(100)]
    s = _spread_at(closes_asc, 0)
    assert s is not None
    assert s < 0


def test_spread_at_insufficient_returns_none():
    """len < offset + 60 → None."""
    assert _spread_at([100.0] * 59, 0) is None


def test_spread_at_exact_boundary_ok():
    """len == offset + 60 → not None (exactly enough)."""
    assert _spread_at([100.0] * 60, 0) is not None


def test_spread_at_offset_plus_60_exceeds_len():
    """offset=41 with 100 elements: 100 < 41+60=101 → None."""
    assert _spread_at([100.0] * 100, 41) is None


def test_spread_at_offset_40_exact_boundary():
    """offset=40 with 100 elements: 100 == 40+60 → 0.0 (flat)."""
    assert _spread_at([100.0] * 100, 40) == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _rsi_at — RSI at offset slice
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _alt30():
    """30-element alternating prices newest-first: 100, 102, 100, 102..."""
    return [(100.0 + (i % 2) * 2) for i in range(30)]


def test_rsi_at_offset0_golden():
    """offset=0, alternating 30-elem."""
    assert _rsi_at(_alt30(), 0) == 47.54


def test_rsi_at_offset5_golden():
    """offset=5, alternating 30-elem."""
    assert _rsi_at(_alt30(), 5) == 49.03


def test_rsi_at_declining_monotone():
    """Monotone declining newest-first → no gains → avg_gain=0 → RSI=0."""
    closes = [0.0 + i for i in range(30)]  # newest=0, oldest=29 -> changes negative
    r = _rsi_at(closes, 0)
    assert r == 0.0


def test_rsi_at_insufficient_returns_none():
    """len < offset+period+1 → None."""
    assert _rsi_at([100.0] * 14, 0) is None


def test_rsi_at_exact_boundary_ok():
    """len == 0+14+1 = 15 → not None."""
    closes = [float(i) for i in range(15)]
    assert _rsi_at(closes, 0) is not None


def test_rsi_at_offset_pushes_past_data():
    """offset=2 requires len >= 2+15=17; 16 elements → None."""
    assert _rsi_at([100.0] * 16, 2) is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _ma edge cases (补充 existing tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_ma_single_element():
    """Single element, n=1 → that element."""
    assert _ma([7.0], 1) == 7.0


def test_ma_empty_list():
    """Empty list → None."""
    assert _ma([], 1) is None


def test_ma_exact_window_boundary():
    """len == n → mean of all (not None)."""
    assert _ma([1.0, 2.0, 3.0, 4.0, 5.0], 5) == 3.0


def test_ma_one_over_window():
    """len == n+1 → uses arr[:n] only."""
    # _ma([10, 20, 30], 2) = mean([10, 20]) = 15.0
    assert _ma([10.0, 20.0, 30.0], 2) == 15.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _rsi edge cases
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_rsi_flat_prices_returns_100():
    """All identical prices → avg_loss=0 → RSI=100.0."""
    assert _rsi([100.0] * 20, 14) == 100.0


def test_rsi_alternating_golden():
    """Alternating 30-elem: golden=47.54."""
    assert _rsi(_alt30(), 14) == 47.54


def test_rsi_declining_returns_0():
    """Monotone declining newest-first → RSI=0."""
    closes = [0.0 + i for i in range(30)]
    assert _rsi(closes, 14) == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _macd golden values
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_macd_linear_35_golden():
    """35-element linear closes: golden (macd, signal, hist)."""
    closes = [100.0 + i * 0.1 for i in range(35)]
    m, s, h = _macd(closes)
    assert m == -0.6106
    assert s == -0.5811
    assert h == -0.0295


def test_macd_oscillating_50_golden():
    """50-element oscillating closes: golden values."""
    closes = [100.0 + (i % 5) * 2.0 for i in range(50)]
    m, s, h = _macd(closes)
    assert m == -0.4439
    assert s == -0.1805
    assert h == -0.2635


def test_macd_hist_is_macd_minus_signal():
    """hist is independently rounded; abs difference from (macd-signal) < 0.001."""
    closes = [100.0 + i * 0.1 for i in range(50)]
    m, sig, h = _macd(closes)
    # both h and (m-sig) are rounded to 4dp; they differ by at most 0.0001
    assert abs(h - round(m - sig, 4)) <= 0.0001


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _atr edge cases
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_atr_close_fallback_golden():
    """highs/lows = [] → falls back to close-based TR, golden = 0.2."""
    closes = [100.0 - i * 0.2 for i in range(30)]
    assert _atr(closes, [], [], 14) == 0.2


def test_atr_exact_boundary_15_returns_value():
    """len == period+1 = 15 → not None."""
    closes = [100.0 - i * 0.2 for i in range(15)]
    assert _atr(closes, [], [], 14) is not None


def test_atr_14_elem_insufficient():
    """len == period = 14 → None."""
    closes = [100.0 - i * 0.2 for i in range(14)]
    assert _atr(closes, [], [], 14) is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _volatility_20d edge cases
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_volatility_20d_constant_zero():
    """All same prices → std=0 → vol=0.0."""
    assert _volatility_20d([50.0] * 25) == 0.0


def test_volatility_20d_19_elem_insufficient():
    """len=19 → None."""
    assert _volatility_20d([100.0 + (i % 3) for i in range(19)]) is None


def test_volatility_20d_exactly_20_golden():
    """Exactly 20 elements golden."""
    closes = [100.0 + (i % 3) * 1.0 for i in range(20)]
    assert _volatility_20d(closes) == 0.7971


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _is_kr_trading_day
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_is_kr_trading_day_weekday_returns_true():
    """Normal weekday not in holidays → True."""
    assert _is_kr_trading_day("20260609") is True   # Tuesday
    assert _is_kr_trading_day("20260610") is True   # Wednesday


def test_is_kr_trading_day_saturday_false():
    assert _is_kr_trading_day("20260606") is False


def test_is_kr_trading_day_sunday_false():
    assert _is_kr_trading_day("20260607") is False


def test_is_kr_trading_day_new_year_holiday_false():
    """20260101 is in _KR_MARKET_HOLIDAYS."""
    assert _is_kr_trading_day("20260101") is False


def test_is_kr_trading_day_explicit_holiday_false():
    """20260603 is in _KR_MARKET_HOLIDAYS (weekday)."""
    assert _is_kr_trading_day("20260603") is False


def test_is_kr_trading_day_invalid_format_conservative_true():
    """Invalid format → conservative True (allow collection)."""
    assert _is_kr_trading_day("invalid") is True
    assert _is_kr_trading_day(None) is True
    assert _is_kr_trading_day("202606") is True  # wrong length


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _summarize_filters
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_summarize_filters_all_keys():
    """All 14 known keys with values → all included."""
    f = {
        "market_cap_min": 1000, "market_cap_max": 5000,
        "chg_pct_min": 1.0, "chg_pct_max": 10.0,
        "foreign_ratio_min": 0.1, "fi_ratio_min": 0.2,
        "per_min": 5, "per_max": 20,
        "pbr_min": 0.5, "pbr_max": 2.0,
        "turnover_min": 0.5, "sort": "chg_pct",
        "n": 50, "market": "kospi",
    }
    result = _summarize_filters(f)
    assert result == f


def test_summarize_filters_none_values_excluded():
    """Keys with None values are excluded; unknown keys excluded."""
    f = {"chg_pct_min": 2.0, "n": None, "market_cap_min": None, "sort": "market_cap",
         "unknown_key": 999}
    assert _summarize_filters(f) == {"chg_pct_min": 2.0, "sort": "market_cap"}


def test_summarize_filters_empty_dict():
    assert _summarize_filters({}) == {}


def test_summarize_filters_unknown_keys_excluded():
    """Unknown keys not in the 14-key list are silently excluded."""
    f = {"chg_pct_min": 1.0, "not_a_real_key": 999}
    assert _summarize_filters(f) == {"chg_pct_min": 1.0}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _parse_period / _build_period / _prev_yoy_period
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_parse_period_valid_quarters():
    assert _parse_period("202412") == (2024, 4)
    assert _parse_period("202403") == (2024, 1)
    assert _parse_period("202406") == (2024, 2)
    assert _parse_period("202409") == (2024, 3)


def test_parse_period_invalid_month_returns_none():
    """Month not in {3,6,9,12} → None."""
    assert _parse_period("202401") is None
    assert _parse_period("202407") is None


def test_parse_period_bad_format_returns_none():
    assert _parse_period("") is None
    assert _parse_period(None) is None
    assert _parse_period("20240") is None      # len != 6
    assert _parse_period("20240a") is None     # not digit
    assert _parse_period("abc012") is None


def test_build_period_all_quarters():
    assert _build_period(2024, 1) == "202403"
    assert _build_period(2024, 2) == "202406"
    assert _build_period(2024, 3) == "202409"
    assert _build_period(2024, 4) == "202412"
    assert _build_period(2025, 2) == "202506"


def test_prev_yoy_period_golden():
    assert _prev_yoy_period("202412") == "202312"
    assert _prev_yoy_period("202403") == "202303"
    assert _prev_yoy_period("202409") == "202309"


def test_prev_yoy_period_invalid_returns_none():
    assert _prev_yoy_period("invalid") is None
    assert _prev_yoy_period(None) is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _safe_div
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_safe_div_normal():
    assert _safe_div(10, 2) == 5.0
    assert _safe_div(7, 4) == 1.75
    assert _safe_div(0, 5) == 0.0


def test_safe_div_none_inputs_return_none():
    assert _safe_div(None, 5) is None
    assert _safe_div(5, None) is None
    assert _safe_div(None, None) is None


def test_safe_div_zero_denominator_returns_none():
    assert _safe_div(5, 0) is None
    assert _safe_div(0, 0) is None


def test_safe_div_non_integer():
    assert abs(_safe_div(100, 3) - 33.333333333333336) < 1e-10


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _pick_net_income
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_pick_net_income_parent_takes_priority():
    assert _pick_net_income({"net_income_parent": 500.0, "net_income": 600.0}) == 500.0


def test_pick_net_income_fallback_when_no_parent():
    assert _pick_net_income({"net_income": 600.0}) == 600.0


def test_pick_net_income_parent_none_uses_fallback():
    assert _pick_net_income({"net_income_parent": None, "net_income": 600.0}) == 600.0


def test_pick_net_income_parent_zero_not_none():
    """0.0 is not None — parent zero wins over fallback."""
    assert _pick_net_income({"net_income_parent": 0.0, "net_income": 600.0}) == 0.0


def test_pick_net_income_both_missing_returns_none():
    assert _pick_net_income({}) is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _div_num
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_div_num_comma_formatted():
    assert _div_num("1,234.5") == 1234.5


def test_div_num_plain_int_string():
    assert _div_num("500") == 500.0


def test_div_num_empty_string():
    assert _div_num("") == 0.0


def test_div_num_none():
    assert _div_num(None) == 0.0


def test_div_num_dash_string():
    """'-' cannot be parsed → 0.0."""
    assert _div_num("-") == 0.0


def test_div_num_numeric_input():
    """Numeric inputs converted via str()."""
    assert _div_num(1234) == 1234.0
    assert _div_num(-5.5) == -5.5


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# is_tier_s_analyst — 3-path OR logic
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_tier_s_path1_exact_boundary():
    """Path ①: stars>=4.5, sr>=70, n>=20."""
    assert is_tier_s_analyst(4.5, 70.0, 20) is True


def test_tier_s_path2_exact_boundary():
    """Path ②: stars>=4.8, sr>=80, n>=7."""
    assert is_tier_s_analyst(4.8, 80.0, 7) is True


def test_tier_s_path3_exact_boundary():
    """Path ③: stars>=4.5, ret>=50, n>=10."""
    assert is_tier_s_analyst(4.5, 0.0, 10, 50.0) is True


def test_tier_s_all_paths_true():
    assert is_tier_s_analyst(5.0, 90.0, 50, 60.0) is True


def test_tier_s_stars_none_always_false():
    assert is_tier_s_analyst(None, 70.0, 20) is False


def test_tier_s_stars_too_low():
    assert is_tier_s_analyst(4.4, 70.0, 20) is False


def test_tier_s_path1_sr_just_below():
    """sr=69.9 misses ①; no ② or ③ → False."""
    assert is_tier_s_analyst(4.5, 69.9, 20) is False


def test_tier_s_path1_n_just_below():
    """n=19 misses ①; no ② or ③ → False."""
    assert is_tier_s_analyst(4.5, 70.0, 19) is False


def test_tier_s_path2_sr_just_below():
    assert is_tier_s_analyst(4.8, 79.9, 7) is False


def test_tier_s_path3_ret_just_below():
    assert is_tier_s_analyst(4.5, 0.0, 10, 49.9) is False
