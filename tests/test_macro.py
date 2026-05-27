"""매크로 함수 unit test — mock 위주, _yf_history는 1건 live 검증.

대상:
- judge_regime: 매크로 → 🟢🟡🔴 분류
- format_macro_msg: 텔레그램 메시지 포맷
- collect_macro_data: 통합 수집 (mock yahoo + KIS)
- _yf_history: 라이브 (yfinance 자체) — pytest.mark.live
"""
import asyncio
from unittest.mock import patch, AsyncMock

import pytest

from kis_api.macro import judge_regime, format_macro_msg, collect_macro_data
from kis_api.news import _yf_history


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. judge_regime — v6 3단계 분류
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_judge_regime_offensive_green():
    """S&P 200MA 위 + VIX<20 → 🟢 공격."""
    data = {
        "SP500": {"price": 5200.0, "ma200": 5000.0},  # +4% > 3% 버퍼
        "VIX":   {"price": 15.0},
    }
    r = judge_regime(data)
    assert r["regime"] == "🟢"
    assert r["label"] == "공격"


def test_judge_regime_crisis_red():
    """S&P 200MA 아래 + VIX>30 → 🔴 위기."""
    data = {
        "SP500": {"price": 4500.0, "ma200": 5000.0},  # -10% < -3% 버퍼
        "VIX":   {"price": 35.0},
    }
    r = judge_regime(data)
    assert r["regime"] == "🔴"
    assert r["label"] == "위기"


def test_judge_regime_caution_yellow():
    """둘 중 하나만 이탈 → 🟡 경계."""
    # S&P 위 + VIX 중간(20~30)
    data = {
        "SP500": {"price": 5200.0, "ma200": 5000.0},
        "VIX":   {"price": 25.0},
    }
    r = judge_regime(data)
    assert r["regime"] == "🟡"
    assert r["label"] == "경계"


def test_judge_regime_buffer_zone_neutral():
    """S&P가 200MA ±3% 버퍼존 → 🟡 (neutral)."""
    data = {
        "SP500": {"price": 5050.0, "ma200": 5000.0},  # +1% < 3%
        "VIX":   {"price": 15.0},
    }
    r = judge_regime(data)
    assert r["regime"] == "🟡"


def test_judge_regime_missing_data_caution():
    """데이터 부재 시 안전 측 → 🟡 경계."""
    data = {}
    r = judge_regime(data)
    assert r["regime"] == "🟡"
    assert "데이터 없음" in " ".join(r["reasons"])


def test_judge_regime_question_marks():
    """'?' 값 입력 → None 처리 → 🟡 경계."""
    data = {
        "SP500": {"price": "?", "ma200": "?"},
        "VIX":   {"price": "?"},
    }
    r = judge_regime(data)
    assert r["regime"] == "🟡"


def test_judge_regime_string_prices_parsed():
    """문자열 가격도 float 변환 후 판단."""
    data = {
        "SP500": {"price": "5,200.00", "ma200": "5,000.00"},
        "VIX":   {"price": "15.5"},
    }
    r = judge_regime(data)
    assert r["regime"] == "🟢"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. format_macro_msg — 포맷 회귀
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_format_macro_msg_contains_sections():
    """필수 섹션 헤더 포함."""
    data = {
        "VIX":   {"price": 18.0, "change_pct": 0.5},
        "KOSPI": {"price": 2500.0, "change_pct": -0.3},
        "SP500": {"price": 5100.0, "ma200": 5000.0, "change_pct": 0.2},
        "WTI":   {"price": 75.0, "change_pct": 1.0},
        "GOLD":  {"price": 2200.0, "change_pct": 0.1},
        "USDKRW": {"price": "1380.0", "change_pct": 0.05},
        "DXY":   {"price": 104.0, "change_pct": -0.1},
        "US10Y": {"price": 4.3, "change_pct": 0.0},
        "COPPER": {"price": 4.1, "change_pct": 0.0},
    }
    msg = format_macro_msg(data)
    assert "매크로 대시보드" in msg
    assert "VIX" in msg
    assert "KOSPI" in msg


def test_format_macro_msg_handles_missing_values():
    """'?' 값 안전하게 표시."""
    data = {
        "VIX":   {"price": "?", "change_pct": "?"},
        "KOSPI": {"price": "?", "change_pct": "?"},
        "SP500": {"price": "?", "change_pct": "?", "ma200": "?"},
    }
    msg = format_macro_msg(data)
    assert "?" in msg
    # 예외 안 던지면 OK


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. collect_macro_data — Yahoo + KIS mock 통합
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_collect_macro_data_returns_required_keys():
    """수집 결과에 핵심 키 포함."""
    async def fake_quote(symbol):
        # 매크로 심볼별 가짜 값
        return {"price": 100.0, "prev": 99.0, "change_pct": 1.01}

    async def fake_token():
        return None  # token=None → MARKET_FLOW 빈 dict 경로

    with patch("kis_api.macro.get_yahoo_quote", side_effect=fake_quote), \
         patch("kis_api.macro.get_kis_token", side_effect=fake_token), \
         patch("kis_api.macro._yf_history", return_value=[]), \
         patch("kis_api.macro.asyncio.sleep", new=AsyncMock(return_value=None)):
        result = asyncio.run(collect_macro_data())

    # 필수 매크로 심볼
    for key in ("VIX", "WTI", "GOLD", "COPPER", "DXY", "US10Y", "SP500",
                "KOSPI", "USDKRW", "FOREIGN_FLOW", "EVENTS"):
        assert key in result, f"missing key: {key}"


def test_collect_macro_data_handles_yahoo_failure():
    """get_yahoo_quote 모두 실패해도 키 존재 + '?'."""
    async def failing_quote(symbol):
        raise RuntimeError("network down")

    async def fake_token():
        return None

    with patch("kis_api.macro.get_yahoo_quote", side_effect=failing_quote), \
         patch("kis_api.macro.get_kis_token", side_effect=fake_token), \
         patch("kis_api.macro._yf_history", return_value=[]), \
         patch("kis_api.macro.asyncio.sleep", new=AsyncMock(return_value=None)):
        result = asyncio.run(collect_macro_data())

    assert result["VIX"]["price"] == "?"
    assert result["KOSPI"]["price"] == "?"


def test_collect_macro_data_attaches_sp500_ma200():
    """_yf_history가 200일 이상 데이터 반환 시 SP500.ma200 계산."""
    fake_history = [5000.0 + i * 0.5 for i in range(250)]  # 250일

    async def fake_quote(symbol):
        return {"price": 5200.0, "prev": 5180.0, "change_pct": 0.4}

    async def fake_token():
        return None

    with patch("kis_api.macro.get_yahoo_quote", side_effect=fake_quote), \
         patch("kis_api.macro.get_kis_token", side_effect=fake_token), \
         patch("kis_api.macro._yf_history", return_value=fake_history), \
         patch("kis_api.macro.asyncio.sleep", new=AsyncMock(return_value=None)):
        result = asyncio.run(collect_macro_data())

    ma = result["SP500"].get("ma200")
    assert ma is not None and ma != "?"
    assert isinstance(ma, (int, float))
    # 마지막 200일 평균: arithmetic series → 약 5000 + 0.5*(50+249)/2*200/200 등
    assert 5000 < ma < 5200


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. _yf_history — yfinance 라이브 (선택, mark live)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_yf_history_returns_list_type():
    """결과는 list 타입 (성공/실패 무관)."""
    result = _yf_history("invalid_symbol_xyz_zzz", "5d")
    assert isinstance(result, list)


@pytest.mark.live
def test_yf_history_real_symbol_returns_floats():
    """실제 yfinance: AAPL 5일 데이터 → float list."""
    result = _yf_history("AAPL", "5d")
    if result:  # 시장 휴장 등으로 빈 결과 가능
        assert all(isinstance(v, float) for v in result)
        assert all(v > 0 for v in result)
