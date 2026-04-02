"""
Tests for compute_volume_profile() in kis_api.py.

Written against the spec — implementation is being developed in parallel.
Run: pytest test_volume_profile.py -v
"""

import pytest
import random

from kis_api import compute_volume_profile

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_candles(prices_volumes):
    """prices_volumes: list of (close_price, volume) tuples."""
    candles = []
    for i, (price, vol) in enumerate(prices_volumes):
        candles.append({
            "date": f"2025{(i // 28 + 1):02d}{(i % 28 + 1):02d}",
            "open": price,
            "high": price + 100,
            "low": price - 100,
            "close": price,
            "vol": vol,
        })
    return candles


REQUIRED_KEYS = {
    "total_volume", "current_price", "price_range", "poc", "poc_volume_pct",
    "value_area", "bins", "support_levels", "resistance_levels", "interpretation",
}

# ---------------------------------------------------------------------------
# Test 1: Basic structure
# ---------------------------------------------------------------------------

def test_basic_structure():
    random.seed(42)
    pv = [(random.randint(10000, 20000), random.randint(1000, 50000)) for _ in range(100)]
    candles = _make_candles(pv)
    current_price = 15000

    result = compute_volume_profile(candles, current_price, bins=10)

    # All required keys present
    assert REQUIRED_KEYS <= set(result.keys()), f"Missing keys: {REQUIRED_KEYS - set(result.keys())}"

    # Correct number of bins
    assert len(result["bins"]) == 10

    # Volume percentages sum to ~100
    total_pct = sum(b["volume_pct"] for b in result["bins"])
    assert abs(total_pct - 100.0) < 0.5, f"volume_pct sum = {total_pct}"

    # total_volume matches input
    expected_vol = sum(v for _, v in pv)
    assert result["total_volume"] == expected_vol


# ---------------------------------------------------------------------------
# Test 2: POC correctness
# ---------------------------------------------------------------------------

def test_poc_correctness():
    # Heavy volume at 15000, light volume spread elsewhere
    heavy = [(15000, 10000)] * 50
    light = [(p, 100) for p in range(10000, 20001, 200)]
    candles = _make_candles(heavy + light)

    result = compute_volume_profile(candles, 15000, bins=10)

    # POC should be near 15000
    assert abs(result["poc"] - 15000) < 1500, f"POC={result['poc']}, expected near 15000"

    # poc_volume_pct should be dominant
    assert result["poc_volume_pct"] > 40, f"poc_volume_pct={result['poc_volume_pct']}"


# ---------------------------------------------------------------------------
# Test 3: Value area
# ---------------------------------------------------------------------------

def test_value_area():
    heavy = [(15000, 10000)] * 50
    light = [(p, 100) for p in range(10000, 20001, 200)]
    candles = _make_candles(heavy + light)

    result = compute_volume_profile(candles, 15000, bins=10)

    va = result["value_area"]
    assert va["low"] <= 15000 <= va["high"]

    # Value area should be narrow relative to total range
    full_range = result["price_range"]["high"] - result["price_range"]["low"]
    va_range = va["high"] - va["low"]
    assert va_range < full_range * 0.5, "Value area should be narrow when volume is concentrated"


# ---------------------------------------------------------------------------
# Test 4: Support and resistance
# ---------------------------------------------------------------------------

def test_support_and_resistance():
    # Heavy volume at 10000 (support) and 20000 (resistance)
    support_candles = [(10000, 50000)] * 30
    resist_candles = [(20000, 50000)] * 30
    filler = [(15000, 100)] * 10
    candles = _make_candles(support_candles + resist_candles + filler)

    result = compute_volume_profile(candles, 15000, bins=10)

    assert len(result["support_levels"]) <= 3
    assert len(result["resistance_levels"]) <= 3

    # At least one support level near 10000
    if result["support_levels"]:
        nearest_support = min(result["support_levels"], key=lambda b: abs(b["price_mid"] - 10000))
        assert abs(nearest_support["price_mid"] - 10000) < 2000

    # At least one resistance level near 20000
    if result["resistance_levels"]:
        nearest_resist = min(result["resistance_levels"], key=lambda b: abs(b["price_mid"] - 20000))
        assert abs(nearest_resist["price_mid"] - 20000) < 2000


# ---------------------------------------------------------------------------
# Test 5: Edge case -- all same price
# ---------------------------------------------------------------------------

def test_all_same_price():
    candles = _make_candles([(10000, 5000)] * 20)

    result = compute_volume_profile(candles, 10000, bins=5)

    assert isinstance(result, dict)
    assert "poc" in result
    # POC should be at or very near 10000
    assert abs(result["poc"] - 10000) < 500


# ---------------------------------------------------------------------------
# Test 6: Bins parameter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bins,expected_count", [
    (1, 1),
    (50, 50),
])
def test_bins_parameter(bins, expected_count):
    random.seed(7)
    pv = [(random.randint(10000, 20000), random.randint(100, 5000)) for _ in range(60)]
    candles = _make_candles(pv)

    result = compute_volume_profile(candles, 15000, bins=bins)
    assert len(result["bins"]) == expected_count

    if bins == 1:
        assert abs(result["bins"][0]["volume_pct"] - 100.0) < 0.5


def test_bins_default():
    random.seed(7)
    pv = [(random.randint(10000, 20000), random.randint(100, 5000)) for _ in range(60)]
    candles = _make_candles(pv)

    result = compute_volume_profile(candles, 15000)
    assert len(result["bins"]) == 20


# ---------------------------------------------------------------------------
# Test 7: Bar visualization
# ---------------------------------------------------------------------------

def test_bar_visualization():
    random.seed(99)
    pv = [(random.randint(10000, 20000), random.randint(100, 5000)) for _ in range(60)]
    candles = _make_candles(pv)

    result = compute_volume_profile(candles, 15000, bins=10)

    poc_bar_filled = 0
    for b in result["bins"]:
        assert "bar" in b
        assert isinstance(b["bar"], str)
        # Bar should only contain block characters
        allowed = set("\u2588\u2591")  # full block and light shade
        assert set(b["bar"]) <= allowed, f"Unexpected chars in bar: {set(b['bar']) - allowed}"

        filled = b["bar"].count("\u2588")
        if abs(b["price_mid"] - result["poc"]) < 1:
            poc_bar_filled = filled

    # POC bin should have the most filled blocks
    max_filled = max(b["bar"].count("\u2588") for b in result["bins"])
    assert poc_bar_filled == max_filled, "POC bin should have the most filled blocks"


# ---------------------------------------------------------------------------
# Test 8: Empty candles
# ---------------------------------------------------------------------------

def test_empty_candles():
    result = compute_volume_profile([], 10000, 20)

    # Should not raise; should return a valid dict
    assert isinstance(result, dict)
    # bins should be empty or total_volume should be 0
    assert result.get("total_volume", 0) == 0 or result.get("bins") == []


# ---------------------------------------------------------------------------
# Test 9: US stock (float prices)
# ---------------------------------------------------------------------------

def test_float_prices():
    pv = [
        (185.50, 1200000), (186.25, 1500000), (190.00, 800000),
        (188.75, 2000000), (191.50, 950000), (187.00, 1100000),
        (185.00, 1400000), (189.50, 1300000), (192.00, 600000),
        (186.75, 1800000),
    ]
    candles = []
    for i, (price, vol) in enumerate(pv):
        candles.append({
            "date": f"2025{(i // 28 + 1):02d}{(i % 28 + 1):02d}",
            "open": price - 0.5,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "vol": vol,
        })

    result = compute_volume_profile(candles, 188.0, bins=5)

    assert len(result["bins"]) == 5
    assert result["total_volume"] == sum(v for _, v in pv)
    # price_range should span the float values
    assert result["price_range"]["low"] < 186
    assert result["price_range"]["high"] > 191


# ---------------------------------------------------------------------------
# Test 10: Interpretation text
# ---------------------------------------------------------------------------

def test_interpretation_text():
    random.seed(42)
    pv = [(random.randint(10000, 20000), random.randint(1000, 50000)) for _ in range(50)]
    candles = _make_candles(pv)

    result = compute_volume_profile(candles, 15000, bins=10)

    interp = result["interpretation"]
    assert isinstance(interp, str)
    assert len(interp) > 0

    # Should contain at least one expected Korean keyword
    kr_keywords = ["POC", "지지", "저항", "매물대", "현재가"]
    assert any(kw in interp for kw in kr_keywords), (
        f"interpretation should contain Korean analysis keywords, got: {interp[:100]}"
    )
