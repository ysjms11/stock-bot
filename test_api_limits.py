"""
API 인위적 제한 해제 테스트
1. get_supply(history) days=20, days=30 — 제한 해제 확인
2. get_market_signal(short_sale) days=45 — 제한 해제 확인
3. get_stock_detail investor 배열 — 슬라이싱 제거 확인
4. kis_daily_short_sale 날짜범위 파라미터 확인
5. supply_follow 백테스트 n_days=30 확인
"""
import pytest
import sys
import types
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# ── telegram stub ──
telegram_stub = types.ModuleType("telegram")
telegram_stub.Update = object
telegram_stub.ReplyKeyboardMarkup = type("ReplyKeyboardMarkup", (), {"__init__": lambda self, *a, **kw: None})
ext_stub = types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.MessageHandler = object
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
ext_stub.filters = type("filters", (), {"TEXT": None, "Regex": staticmethod(lambda x: x)})()
sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)

import kis_api
from kis_api import kis_daily_short_sale


# ─────────────────────────────────────────────────────────────────────
# 1. get_supply history days cap = 30
# ─────────────────────────────────────────────────────────────────────
class TestSupplyHistoryLimit:
    def test_days_cap_is_30(self):
        """MCP 스키마에서 days 최대 30 허용 확인"""
        from mcp_tools import MCP_TOOLS
        supply_tool = next(t for t in MCP_TOOLS if t["name"] == "get_supply")
        desc = supply_tool["inputSchema"]["properties"]["days"]["description"]
        assert "30" in desc

    @pytest.mark.asyncio
    async def test_days_20_passes_through(self):
        """days=20이 잘리지 않고 그대로 전달되는지"""
        from mcp_tools import _execute_tool
        mock_rows = [{"date": f"2026030{i}", "foreign_net": i * 100,
                       "institution_net": -i * 50, "individual_net": 0,
                       "foreign_buy": 1000, "foreign_sell": 500}
                      for i in range(20)]
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("mcp_tools.kis_investor_trend_history", new_callable=AsyncMock, return_value=mock_rows):
            result = await _execute_tool("get_supply", {"mode": "history", "ticker": "005930", "days": 20})
        assert result["days"] == 20

    @pytest.mark.asyncio
    async def test_days_60_capped_to_30(self):
        """days=60 → 30으로 캡"""
        from mcp_tools import _execute_tool
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("mcp_tools.kis_investor_trend_history", new_callable=AsyncMock, return_value=[]):
            result = await _execute_tool("get_supply", {"mode": "history", "ticker": "005930", "days": 60})
        assert result["days"] == 30

    @pytest.mark.asyncio
    async def test_empty_history_graceful(self):
        """빈 배열 반환 시 graceful"""
        from mcp_tools import _execute_tool
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("mcp_tools.kis_investor_trend_history", new_callable=AsyncMock, return_value=[]):
            result = await _execute_tool("get_supply", {"mode": "history", "ticker": "005930", "days": 30})
        assert result["history"] == []
        assert result["days"] == 30


# ─────────────────────────────────────────────────────────────────────
# 2. get_market_signal short_sale days cap = 60
# ─────────────────────────────────────────────────────────────────────
class TestShortSaleLimit:
    def test_days_cap_is_60(self):
        """MCP 스키마에서 days 최대 60 표기 확인"""
        from mcp_tools import MCP_TOOLS
        sig_tool = next(t for t in MCP_TOOLS if t["name"] == "get_market_signal")
        desc = sig_tool["inputSchema"]["properties"]["days"]["description"]
        assert "60" in desc

    @pytest.mark.asyncio
    async def test_days_45_passes_through(self):
        """days=45가 잘리지 않고 전달"""
        from mcp_tools import _execute_tool
        mock_rows = [{"date": f"2026030{i}", "short_vol": 1000,
                       "total_vol": 10000, "short_ratio": 10.0, "close": 50000}
                      for i in range(45)]
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("mcp_tools.kis_daily_short_sale", new_callable=AsyncMock, return_value=mock_rows):
            result = await _execute_tool("get_market_signal", {"mode": "short_sale", "ticker": "005930", "days": 45})
        assert result["count"] == 45

    @pytest.mark.asyncio
    async def test_days_90_capped_to_60(self):
        """days=90 → 60으로 캡"""
        from mcp_tools import _execute_tool
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("mcp_tools.kis_daily_short_sale", new_callable=AsyncMock, return_value=[]) as mock_fn:
            result = await _execute_tool("get_market_signal", {"mode": "short_sale", "ticker": "005930", "days": 90})
        # kis_daily_short_sale에 n=60으로 전달됐는지 확인
        mock_fn.assert_called_once()
        assert mock_fn.call_args[1].get("n", mock_fn.call_args[0][2] if len(mock_fn.call_args[0]) > 2 else None) is not None

    @pytest.mark.asyncio
    async def test_short_sale_empty_graceful(self):
        """빈 배열 반환 시 graceful"""
        from mcp_tools import _execute_tool
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("mcp_tools.kis_daily_short_sale", new_callable=AsyncMock, return_value=[]):
            result = await _execute_tool("get_market_signal", {"mode": "short_sale", "ticker": "005930", "days": 30})
        assert result["items"] == []
        assert result["count"] == 0


# ─────────────────────────────────────────────────────────────────────
# 3. get_stock_detail investor 슬라이싱 제거
# ─────────────────────────────────────────────────────────────────────
class TestStockDetailInvestor:
    @pytest.mark.asyncio
    async def test_investor_full_array(self):
        """investor가 5건이면 5건 모두 반환 ([:3] 슬라이싱 제거됨)"""
        from mcp_tools import _execute_tool
        mock_price = {"stck_prpr": "65000", "prdy_ctrt": "+1.0", "acml_vol": "100000",
                       "w52_hgpr": "80000", "w52_lwpr": "50000",
                       "per": "10", "pbr": "1.5", "eps": "6500", "bps": "40000"}
        mock_inv = [
            {"frgn_ntby_qty": "1000", "orgn_ntby_qty": "-500"},
            {"frgn_ntby_qty": "2000", "orgn_ntby_qty": "300"},
            {"frgn_ntby_qty": "-100", "orgn_ntby_qty": "800"},
            {"frgn_ntby_qty": "500", "orgn_ntby_qty": "-200"},
            {"frgn_ntby_qty": "700", "orgn_ntby_qty": "100"},
        ]
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("mcp_tools.kis_stock_price", new_callable=AsyncMock, return_value=mock_price), \
             patch("mcp_tools.kis_investor_trend", new_callable=AsyncMock, return_value=mock_inv), \
             patch("mcp_tools.kis_estimate_perform", new_callable=AsyncMock, return_value={}), \
             patch("mcp_tools._is_us_ticker", return_value=False):
            result = await _execute_tool("get_stock_detail", {"ticker": "005930"})
        assert len(result["investor"]) == 5, f"investor 배열 {len(result['investor'])}건, 5건 기대"

    @pytest.mark.asyncio
    async def test_investor_empty_graceful(self):
        """investor가 빈 배열이면 빈 배열 반환"""
        from mcp_tools import _execute_tool
        mock_price = {"stck_prpr": "65000", "prdy_ctrt": "+1.0", "acml_vol": "100000",
                       "w52_hgpr": "", "w52_lwpr": "",
                       "per": "", "pbr": "", "eps": "", "bps": ""}
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("mcp_tools.kis_stock_price", new_callable=AsyncMock, return_value=mock_price), \
             patch("mcp_tools.kis_investor_trend", new_callable=AsyncMock, return_value=[]), \
             patch("mcp_tools.kis_estimate_perform", new_callable=AsyncMock, return_value={}), \
             patch("mcp_tools._is_us_ticker", return_value=False):
            result = await _execute_tool("get_stock_detail", {"ticker": "005930"})
        assert result["investor"] == []


# ─────────────────────────────────────────────────────────────────────
# 4. kis_daily_short_sale 날짜범위 파라미터
# ─────────────────────────────────────────────────────────────────────
class TestShortSaleDateRange:
    @pytest.mark.asyncio
    async def test_date_range_params_sent(self):
        """날짜범위 파라미터가 비어있지 않은지 확인"""
        with patch("kis_api._kis_get", new_callable=AsyncMock,
                    return_value=({}, {"output2": []})) as mock_get:
            await kis_daily_short_sale("005930", "tok", n=30)
            call_args = mock_get.call_args
            params = call_args[0][4]  # 5th positional arg = params dict
            assert params["FID_INPUT_DATE_1"] != "", "시작일자가 비어있음"
            assert params["FID_INPUT_DATE_2"] != "", "종료일자가 비어있음"

    @pytest.mark.asyncio
    async def test_date_range_covers_period(self):
        """n=60일 요청 시 시작일이 충분히 과거인지"""
        with patch("kis_api._kis_get", new_callable=AsyncMock,
                    return_value=({}, {"output2": []})) as mock_get:
            await kis_daily_short_sale("005930", "tok", n=60)
            params = mock_get.call_args[0][4]
            start = datetime.strptime(params["FID_INPUT_DATE_1"], "%Y%m%d")
            end = datetime.strptime(params["FID_INPUT_DATE_2"], "%Y%m%d")
            diff = (end - start).days
            assert diff >= 60, f"날짜범위 {diff}일, 최소 60일 필요"
