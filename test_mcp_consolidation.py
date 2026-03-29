"""MCP 도구 통합 라우팅 테스트 — 32→18 리팩터링 검증"""
import sys
import types
import unittest
import asyncio
import json
import os
from unittest.mock import AsyncMock, patch, MagicMock

# telegram 스텁 (import 시 telegram 패키지 불필요하게)
telegram_stub = types.ModuleType("telegram")
telegram_stub.Update = object
ext_stub = types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)

from mcp_tools import _execute_tool


def _run(coro):
    """asyncio.run wrapper for test methods."""
    return asyncio.run(coro)


def _mock_ws_manager():
    """ws_manager mock that returns a proper coroutine from update_tickers."""
    m = MagicMock()
    m.update_tickers = AsyncMock()
    return m


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. TestGetRank — 4개 type 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestGetRank(unittest.TestCase):

    @patch("mcp_tools.kis_fluctuation_rank", new_callable=AsyncMock,
           return_value=[{"ticker": "005930", "name": "삼성전자", "chg": "3.5"}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_price_rank(self, mock_token, mock_fluct):
        result = _run(_execute_tool("get_rank", {"type": "price"}))
        mock_fluct.assert_called_once()
        self.assertIn("items", result)
        self.assertEqual(result["sort"], "rise")

    @patch("mcp_tools.kis_us_updown_rate", new_callable=AsyncMock,
           return_value=[{"ticker": "TSLA", "name": "Tesla", "chg": "5.0"}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_us_price_rank(self, mock_token, mock_us):
        result = _run(_execute_tool("get_rank", {"type": "us_price"}))
        mock_us.assert_called_once()
        self.assertIn("items", result)
        self.assertEqual(result["exchange"], "NAS")

    @patch("mcp_tools.kis_volume_power_rank", new_callable=AsyncMock,
           return_value=[{"ticker": "005930", "name": "삼성전자", "power": "130"}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_volume_rank(self, mock_token, mock_vol):
        result = _run(_execute_tool("get_rank", {"type": "volume"}))
        mock_vol.assert_called_once()
        self.assertIn("items", result)

    @patch("mcp_tools.kis_foreigner_trend", new_callable=AsyncMock,
           return_value=[{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
                          "frgn_ntby_qty": "500"}])
    @patch("mcp_tools.kis_volume_rank_api", new_callable=AsyncMock,
           return_value=[{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
                          "acml_vol": "10000", "prdy_ctrt": "2.0"}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_scan_market(self, mock_token, mock_vol_rank, mock_frgn):
        result = _run(_execute_tool("get_rank", {"type": "scan"}))
        mock_vol_rank.assert_called_once()
        mock_frgn.assert_called_once()
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)
        self.assertEqual(result[0]["ticker"], "005930")
        self.assertTrue(result[0]["frgn_buy"])

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_invalid_type(self, mock_token):
        """Unknown type returns error."""
        result = _run(_execute_tool("get_rank", {"type": "invalid"}))
        self.assertIn("error", result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. TestGetSupply — 5개 mode 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestGetSupply(unittest.TestCase):

    @patch("mcp_tools.kis_investor_trend", new_callable=AsyncMock,
           return_value=[{"stck_bsop_date": "20260329",
                          "frgn_shnu_vol": "100", "frgn_seln_vol": "50", "frgn_ntby_qty": "50",
                          "orgn_shnu_vol": "200", "orgn_seln_vol": "100", "orgn_ntby_qty": "100",
                          "prsn_shnu_vol": "300", "prsn_seln_vol": "400", "prsn_ntby_qty": "-100"}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_daily(self, mock_token, mock_inv):
        result = _run(_execute_tool("get_supply", {"mode": "daily", "ticker": "005930"}))
        mock_inv.assert_called_once()
        self.assertEqual(result["ticker"], "005930")
        self.assertIn("foreign", result)
        self.assertIn("institution", result)
        self.assertIn("individual", result)
        self.assertEqual(result["foreign"]["net"], 50)

    @patch("mcp_tools.kis_investor_trend_history", new_callable=AsyncMock,
           return_value=[{"date": "20260329", "frgn": 100, "orgn": 200}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_history(self, mock_token, mock_hist):
        result = _run(_execute_tool("get_supply", {"mode": "history", "ticker": "005930", "days": 5}))
        mock_hist.assert_called_once()
        self.assertEqual(result["ticker"], "005930")
        self.assertIn("history", result)

    @patch("mcp_tools.kis_investor_trend_estimate", new_callable=AsyncMock,
           return_value={"frgn_est": 1000, "orgn_est": 2000})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_estimate(self, mock_token, mock_est):
        result = _run(_execute_tool("get_supply", {"mode": "estimate", "ticker": "005930"}))
        mock_est.assert_called_once()
        self.assertNotIn("error", result)

    @patch("mcp_tools.kis_foreigner_trend", new_callable=AsyncMock,
           return_value=[{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
                          "frgn_ntby_qty": "500"}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_foreign_rank(self, mock_token, mock_frgn):
        result = _run(_execute_tool("get_supply", {"mode": "foreign_rank"}))
        mock_frgn.assert_called_once()
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["ticker"], "005930")

    @patch("mcp_tools.kis_foreign_institution_total", new_callable=AsyncMock,
           return_value=[{"ticker": "005930", "name": "삼성전자", "net": 1000}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_combined_rank(self, mock_token, mock_combined):
        result = _run(_execute_tool("get_supply", {"mode": "combined_rank"}))
        mock_combined.assert_called_once()
        self.assertIn("items", result)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_missing_ticker(self, mock_token):
        result = _run(_execute_tool("get_supply", {"mode": "daily"}))
        self.assertIn("error", result)
        self.assertIn("ticker", result["error"])

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_invalid_mode(self, mock_token):
        result = _run(_execute_tool("get_supply", {"mode": "invalid"}))
        self.assertIn("error", result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. TestGetMarketSignal — 3개 mode 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestGetMarketSignal(unittest.TestCase):

    @patch("mcp_tools.kis_daily_short_sale", new_callable=AsyncMock,
           return_value=[{"date": "20260329", "short_vol": 1000}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_short_sale(self, mock_token, mock_short):
        result = _run(_execute_tool("get_market_signal", {"mode": "short_sale", "ticker": "005930"}))
        mock_short.assert_called_once()
        self.assertEqual(result["ticker"], "005930")
        self.assertIn("items", result)

    @patch("mcp_tools.kis_vi_status", new_callable=AsyncMock,
           return_value=[{"ticker": "005930", "vi_type": "static"}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_vi_status(self, mock_token, mock_vi):
        result = _run(_execute_tool("get_market_signal", {"mode": "vi"}))
        mock_vi.assert_called_once()
        self.assertIn("items", result)

    @patch("mcp_tools.kis_program_trade_today", new_callable=AsyncMock,
           return_value=[{"investor": "외국인", "buy_amt": 1000}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_program_trade(self, mock_token, mock_prog):
        result = _run(_execute_tool("get_market_signal", {"mode": "program_trade"}))
        mock_prog.assert_called_once()
        self.assertIn("items", result)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_invalid_mode(self, mock_token):
        result = _run(_execute_tool("get_market_signal", {"mode": "invalid"}))
        self.assertIn("error", result)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_short_sale_missing_ticker(self, mock_token):
        result = _run(_execute_tool("get_market_signal", {"mode": "short_sale"}))
        self.assertIn("error", result)
        self.assertIn("ticker", result["error"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. TestGetSector — 2개 mode 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestGetSector(unittest.TestCase):

    @patch("mcp_tools.save_sector_flow_cache")
    @patch("mcp_tools.load_sector_flow_cache", return_value={})
    @patch("mcp_tools._fetch_sector_flow", new_callable=AsyncMock, return_value=(100, 200))
    @patch("mcp_tools._kis_get", new_callable=AsyncMock,
           return_value=(200, {"output": {"stck_prpr": "50000", "prdy_ctrt": "1.5"}}))
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_flow(self, mock_token, mock_kis_get, mock_sector, mock_cache, mock_save_cache):
        result = _run(_execute_tool("get_sector", {"mode": "flow"}))
        mock_sector.assert_called()
        self.assertIn("top_inflow", result)
        self.assertIn("top_outflow", result)
        self.assertIn("all", result)

    @patch("mcp_tools.detect_sector_rotation", new_callable=AsyncMock,
           return_value={"rotation": [{"from": "IT", "to": "방산", "signal": "strong"}]})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_rotation(self, mock_token, mock_rot):
        result = _run(_execute_tool("get_sector", {"mode": "rotation"}))
        mock_rot.assert_called_once()
        self.assertIn("rotation", result)

    @patch("mcp_tools.load_sector_flow_cache", return_value={})
    @patch("mcp_tools._fetch_sector_flow", new_callable=AsyncMock, return_value=(0, 0))
    @patch("mcp_tools.kis_foreigner_trend", new_callable=AsyncMock, return_value=[])
    @patch("mcp_tools._kis_get", new_callable=AsyncMock,
           return_value=(200, {"output": {"stck_prpr": "50000", "prdy_ctrt": "1.5"}}))
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_flow_default_mode(self, mock_token, mock_kis_get, mock_frgn, mock_sector, mock_cache):
        """mode 생략 시 flow가 기본값."""
        result = _run(_execute_tool("get_sector", {}))
        mock_sector.assert_called()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. TestManageWatch — 2개 action 라우팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestManageWatch(unittest.TestCase):

    @patch("mcp_tools.append_watchlist_log")
    @patch("mcp_tools.ws_manager", _mock_ws_manager())
    @patch("mcp_tools.get_ws_tickers", return_value=[])
    @patch("mcp_tools.save_json")
    @patch("mcp_tools.load_watchlist", return_value={"000660": "SK하이닉스"})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_add(self, mock_token, mock_load_wl, mock_save, mock_ws, mock_log):
        result = _run(_execute_tool("manage_watch",
                                    {"action": "add", "ticker": "005930", "name": "삼성전자"}))
        self.assertTrue(result.get("ok"))
        self.assertIn("추가", result["message"])
        mock_save.assert_called_once()

    @patch("mcp_tools.append_watchlist_log")
    @patch("mcp_tools.ws_manager", _mock_ws_manager())
    @patch("mcp_tools.get_ws_tickers", return_value=[])
    @patch("mcp_tools.save_json")
    @patch("mcp_tools.load_watchlist", return_value={"005930": "삼성전자"})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_remove(self, mock_token, mock_load_wl, mock_save, mock_ws, mock_log):
        result = _run(_execute_tool("manage_watch",
                                    {"action": "remove", "ticker": "005930"}))
        self.assertTrue(result.get("ok"))
        self.assertIn("제거", result["message"])

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_invalid_action(self, mock_token):
        result = _run(_execute_tool("manage_watch",
                                    {"action": "invalid", "ticker": "005930"}))
        self.assertIn("error", result)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_add_missing_name(self, mock_token):
        result = _run(_execute_tool("manage_watch",
                                    {"action": "add", "ticker": "005930"}))
        self.assertIn("error", result)

    @patch("mcp_tools.load_watchlist", return_value={})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_remove_not_found(self, mock_token, mock_load_wl):
        result = _run(_execute_tool("manage_watch",
                                    {"action": "remove", "ticker": "999999"}))
        self.assertIn("error", result)

    @patch("mcp_tools.ws_manager", _mock_ws_manager())
    @patch("mcp_tools.get_ws_tickers", return_value=[])
    @patch("mcp_tools.save_json")
    @patch("mcp_tools.load_watchalert", return_value={"005930": {"name": "삼성전자", "buy_price": 60000}})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_remove_buy_alert(self, mock_token, mock_wa, mock_save, mock_ws):
        result = _run(_execute_tool("manage_watch",
                                    {"action": "remove", "ticker": "005930",
                                     "alert_type": "buy_alert"}))
        self.assertTrue(result.get("ok"))
        self.assertIn("매수감시 제거", result["message"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. TestGetNewsExtended — sentiment 분기
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestGetNewsExtended(unittest.TestCase):

    @patch("mcp_tools.kis_news_title", new_callable=AsyncMock,
           return_value=[{"title": "삼성전자 실적 호조", "date": "20260329"}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_news_only(self, mock_token, mock_news):
        result = _run(_execute_tool("get_news", {"ticker": "005930"}))
        mock_news.assert_called_once()
        self.assertEqual(result["ticker"], "005930")
        self.assertIn("items", result)

    @patch("mcp_tools.analyze_news_sentiment",
           return_value={"positive": [{"title": "호조"}], "negative": [], "neutral": []})
    @patch("mcp_tools.kis_news_title", new_callable=AsyncMock,
           return_value=[{"title": "삼성전자 실적 호조", "date": "20260329"}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_with_sentiment(self, mock_token, mock_news, mock_sentiment):
        result = _run(_execute_tool("get_news",
                                    {"ticker": "005930", "sentiment": True}))
        mock_news.assert_called_once()
        mock_sentiment.assert_called_once()
        self.assertEqual(result["ticker"], "005930")
        self.assertIn("positive", result)

    @patch("mcp_tools.analyze_news_sentiment",
           return_value={"positive": [], "negative": [], "neutral": [{"title": "뉴스"}]})
    @patch("mcp_tools.kis_news_title", new_callable=AsyncMock,
           return_value=[{"title": "뉴스"}])
    @patch("mcp_tools.load_watchlist", return_value={"005930": "삼성전자"})
    @patch("mcp_tools.load_json", return_value={})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_sentiment_all(self, mock_token, mock_pf, mock_wl, mock_news, mock_sent):
        result = _run(_execute_tool("get_news", {"sentiment": True}))
        self.assertIn("stocks", result)
        self.assertIn("total_summary", result)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_news_missing_ticker(self, mock_token):
        """sentiment=false, ticker 미지정 시 에러."""
        result = _run(_execute_tool("get_news", {}))
        self.assertIn("error", result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. TestSetAlertExtended — delete 분기
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestSetAlertExtended(unittest.TestCase):

    @patch("mcp_tools.append_watchlist_log")
    @patch("mcp_tools.ws_manager", _mock_ws_manager())
    @patch("mcp_tools.get_ws_tickers", return_value=[])
    @patch("mcp_tools.save_json")
    @patch("mcp_tools.load_stoploss",
           return_value={"005930": {"name": "삼성전자", "stop_price": 55000, "target_price": 80000}})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_delete_kr(self, mock_token, mock_stops, mock_save, mock_ws, mock_log):
        result = _run(_execute_tool("set_alert",
                                    {"log_type": "delete", "ticker": "005930"}))
        self.assertTrue(result.get("ok"))
        self.assertIn("삭제", result["message"])

    @patch("mcp_tools.append_watchlist_log")
    @patch("mcp_tools.save_json")
    @patch("mcp_tools.load_stoploss",
           return_value={"us_stocks": {"TSLA": {"name": "Tesla", "stop_price": 150, "target_price": 300}}})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_delete_us(self, mock_token, mock_stops, mock_save, mock_log):
        result = _run(_execute_tool("set_alert",
                                    {"log_type": "delete", "ticker": "TSLA"}))
        self.assertTrue(result.get("ok"))
        self.assertIn("삭제", result["message"])

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_delete_missing_ticker(self, mock_token):
        result = _run(_execute_tool("set_alert", {"log_type": "delete"}))
        self.assertIn("error", result)

    @patch("mcp_tools.load_stoploss", return_value={})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_delete_not_found(self, mock_token, mock_stops):
        result = _run(_execute_tool("set_alert",
                                    {"log_type": "delete", "ticker": "999999"}))
        self.assertFalse(result.get("ok"))

    @patch("mcp_tools.save_json")
    @patch("mcp_tools.load_decision_log", return_value={})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_decision_mode(self, mock_token, mock_log, mock_save):
        result = _run(_execute_tool("set_alert", {
            "log_type": "decision",
            "date": "2026-03-29",
            "regime": "경계",
            "grades": {"삼성전자": "A"},
            "actions": ["삼성전자 5주 매수"],
        }))
        self.assertTrue(result.get("ok"))
        self.assertIn("투자판단", result["message"])

    @patch("mcp_tools.save_trade_log")
    @patch("mcp_tools.load_trade_log", return_value=[])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_trade_mode(self, mock_token, mock_load, mock_save):
        result = _run(_execute_tool("set_alert", {
            "log_type": "trade",
            "ticker": "005930",
            "name": "삼성전자",
            "side": "buy",
            "qty": 10,
            "price": 60000,
        }))
        self.assertTrue(result.get("ok"))
        self.assertIn("기록", result["message"])

    @patch("mcp_tools.save_json")
    @patch("mcp_tools.load_compare_log", return_value=[])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_compare_mode(self, mock_token, mock_log, mock_save):
        result = _run(_execute_tool("set_alert", {
            "log_type": "compare",
            "held_ticker": "005930",
            "candidate_ticker": "000660",
            "held_score": 70,
            "candidate_score": 85,
            "reasoning": "반도체 사이클 회복",
        }))
        self.assertTrue(result.get("ok"))
        self.assertIn("비교", result["message"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. TestGetStockDetailExtended — batch 분기
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestGetStockDetailExtended(unittest.TestCase):

    @patch("mcp_tools.kis_estimate_perform", new_callable=AsyncMock, return_value={})
    @patch("mcp_tools.kis_investor_trend", new_callable=AsyncMock,
           return_value=[{"frgn_ntby_qty": "100"}])
    @patch("mcp_tools.kis_stock_price", new_callable=AsyncMock,
           return_value={"stck_prpr": "60000", "prdy_ctrt": "1.5", "acml_vol": "5000",
                         "w52_hgpr": "70000", "w52_lwpr": "50000",
                         "per": "10", "pbr": "1.2", "eps": "6000", "bps": "50000"})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_single_ticker(self, mock_token, mock_price, mock_inv, mock_earn):
        result = _run(_execute_tool("get_stock_detail", {"ticker": "005930"}))
        mock_price.assert_called_once()
        mock_inv.assert_called_once()
        self.assertEqual(result["ticker"], "005930")
        self.assertEqual(result["market"], "KR")
        self.assertEqual(result["price"], "60000")

    @patch("mcp_tools.batch_stock_detail", new_callable=AsyncMock,
           return_value=[{"ticker": "005930", "price": 60000},
                         {"ticker": "000660", "price": 150000}])
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_batch_tickers(self, mock_token, mock_batch):
        result = _run(_execute_tool("get_stock_detail",
                                    {"tickers": "005930,000660"}))
        mock_batch.assert_called_once()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)

    @patch("mcp_tools.kis_us_stock_detail", new_callable=AsyncMock,
           return_value={"open": "200", "high": "210", "low": "195",
                         "h52p": "250", "l52p": "150",
                         "perx": "25", "pbrx": "5", "epsx": "8",
                         "tomv": "500B", "e_icod": "Tech"})
    @patch("mcp_tools.kis_us_stock_price", new_callable=AsyncMock,
           return_value={"last": "205", "base": "200", "rate": "2.5", "tvol": "10000"})
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_us_ticker(self, mock_token, mock_price, mock_detail):
        result = _run(_execute_tool("get_stock_detail", {"ticker": "AAPL"}))
        self.assertEqual(result["market"], "US")
        self.assertEqual(result["price"], 205.0)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    def test_batch_empty_tickers(self, mock_token):
        result = _run(_execute_tool("get_stock_detail", {"tickers": "  , , "}))
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
