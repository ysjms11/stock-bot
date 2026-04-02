"""
mcp_tools.py empty-array/fallback 동작 pytest (5개)
모든 외부 HTTP/API 호출은 mock 처리.
"""
import asyncio
import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# telegram 스텁 (import 체인용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
telegram_stub = types.ModuleType("telegram")
telegram_stub.Update = object
telegram_stub.ReplyKeyboardMarkup = type("RKM", (), {"__init__": lambda self, *a, **kw: None})
ext_stub = types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.MessageHandler = object
ext_stub.filters = type("filters", (), {"TEXT": None, "Regex": staticmethod(lambda x: x)})()
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)

import mcp_tools
from mcp_tools import _execute_tool


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. get_rank price empty → note 포함
# ━━━━━━━━━━━━━━━━━━━━━━━━━
@pytest.mark.asyncio
async def test_get_rank_price_empty_returns_note():
    """kis_fluctuation_rank 빈 리스트 반환 시 'note' 키에 'get_scan' 포함 여부."""
    with patch("mcp_tools.get_kis_token", new=AsyncMock(return_value="fake_token")), \
         patch("mcp_tools.kis_fluctuation_rank", new=AsyncMock(return_value=[])):
        result = await _execute_tool("get_rank", {"type": "price"})

    assert "note" in result, f"'note' 키 없음: {result}"
    assert "get_scan" in result["note"], (
        f"note에 'get_scan' 없음: {result['note']}"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. get_supply foreign_rank empty → note 포함
# ━━━━━━━━━━━━━━━━━━━━━━━━━
@pytest.mark.asyncio
async def test_get_supply_foreign_rank_empty_returns_note():
    """kis_foreigner_trend 빈 리스트 반환 시 'note' 키에 'combined_rank' 포함 여부."""
    with patch("mcp_tools.get_kis_token", new=AsyncMock(return_value="fake_token")), \
         patch("mcp_tools.kis_foreigner_trend", new=AsyncMock(return_value=[])):
        result = await _execute_tool("get_supply", {"mode": "foreign_rank"})

    assert "note" in result, f"'note' 키 없음: {result}"
    assert "combined_rank" in result["note"], (
        f"note에 'combined_rank' 없음: {result['note']}"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. get_supply history empty → note 포함
# ━━━━━━━━━━━━━━━━━━━━━━━━━
@pytest.mark.asyncio
async def test_get_supply_history_empty_returns_note():
    """kis_investor_trend_history 빈 리스트 반환 시 'note' 키에 'estimate' 포함 여부."""
    with patch("mcp_tools.get_kis_token", new=AsyncMock(return_value="fake_token")), \
         patch("mcp_tools.kis_investor_trend_history", new=AsyncMock(return_value=[])):
        result = await _execute_tool("get_supply", {"mode": "history", "ticker": "005930"})

    assert "note" in result, f"'note' 키 없음: {result}"
    assert "estimate" in result["note"], (
        f"note에 'estimate' 없음: {result['note']}"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. get_sector flow all-zero → note 포함 (ETF)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
@pytest.mark.asyncio
async def test_get_sector_flow_all_zero_returns_note_etf():
    """
    _fetch_sector_flow 가 (0, 0) 반환할 때 fallback note에 'ETF' 포함 여부.
    kis_foreigner_trend 는 빈 리스트 반환 (업종 근사치 없어도 note 는 설정됨).
    _kis_get 은 ETF 조회 시 빈 output 반환.
    load_sector_flow_cache 는 빈 dict 반환 (캐시 없음).
    """
    # ETF _kis_get mock: coroutine returning ({}, {"output": {}})
    async def _fake_kis_get(session, path, tr_id, token, params):
        return {}, {"output": {}}

    with patch("mcp_tools.get_kis_token", new=AsyncMock(return_value="fake_token")), \
         patch("mcp_tools._fetch_sector_flow", new=AsyncMock(return_value=(0, 0))), \
         patch("mcp_tools.kis_foreigner_trend", new=AsyncMock(return_value=[])), \
         patch("mcp_tools._kis_get", side_effect=_fake_kis_get), \
         patch("mcp_tools.load_sector_flow_cache", return_value={}):
        result = await _execute_tool("get_sector", {"mode": "flow"})

    assert "note" in result, f"'note' 키 없음: {result}"
    assert "ETF" in result["note"], (
        f"note에 'ETF' 없음: {result['note']}"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. combined_rank with KRX DB → fi_ratio_pct 필드
# ━━━━━━━━━━━━━━━━━━━━━━━━━
@pytest.mark.asyncio
async def test_combined_rank_with_krx_db_has_fi_ratio_pct():
    """
    kis_foreign_institution_total 이 티커 '005930' 포함 아이템 반환 시,
    KRX DB에 해당 시총이 있으면 fi_ratio_pct 필드가 계산되어야 한다.
    """
    # kis_foreign_institution_total 반환 형식: ticker, price, fi_total_net
    fake_items = [
        {"ticker": "005930", "name": "삼성전자", "price": 75000,
         "fi_total_net": 100000, "foreign_net": 80000, "institution_net": 20000},
    ]
    fake_db = {
        "stocks": {
            "005930": {
                "name": "삼성전자",
                "market_cap": 500_0000_0000_0000,  # 500조
            }
        }
    }

    with patch("mcp_tools.get_kis_token", new=AsyncMock(return_value="fake_token")), \
         patch("mcp_tools.kis_foreign_institution_total", new=AsyncMock(return_value=fake_items)), \
         patch("mcp_tools.load_krx_db", return_value=fake_db):
        result = await _execute_tool("get_supply", {"mode": "combined_rank"})

    assert "items" in result, f"'items' 키 없음: {result}"
    items = result["items"]
    assert len(items) == 1, f"items 개수 불일치: {items}"
    assert "fi_ratio_pct" in items[0], (
        f"fi_ratio_pct 필드 없음: {items[0]}"
    )
    # fi_total_net(100000) * price(75000) / market_cap(500조) * 100
    expected = round(100000 * 75000 / 500_0000_0000_0000 * 100, 4)
    assert items[0]["fi_ratio_pct"] == pytest.approx(expected, rel=1e-4), (
        f"fi_ratio_pct 값 불일치: {items[0]['fi_ratio_pct']} != {expected}"
    )
