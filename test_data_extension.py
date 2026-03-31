"""백테스트 데이터 확장 테스트 — FDR/yfinance/KRX 연동 + Y모드 백테스트"""
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
from datetime import datetime, timedelta
import types
import pandas as pd

# telegram 스텁
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


def make_candles(n, start_date="20230101", base_price=10000):
    """테스트용 캔들 생성 헬퍼."""
    candles = []
    dt = datetime.strptime(start_date, "%Y%m%d")
    price = base_price
    for i in range(n):
        change = (i % 7 - 3) * 50
        price = max(1000, price + change)
        candles.append({
            "date": dt.strftime("%Y%m%d"),
            "open": price - 50,
            "high": price + 100,
            "low": price - 100,
            "close": price,
            "vol": 100000 + i * 1000,
        })
        dt += timedelta(days=1)
        while dt.weekday() >= 5:
            dt += timedelta(days=1)
    return candles


def _make_kr_dataframe(n=5):
    """한국 종목용 pandas DataFrame 생성."""
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "Open": [10000 + i * 100 for i in range(n)],
        "High": [10500 + i * 100 for i in range(n)],
        "Low": [9500 + i * 100 for i in range(n)],
        "Close": [10200 + i * 100 for i in range(n)],
        "Volume": [1000000 + i * 100000 for i in range(n)],
    }, index=dates)


def _make_us_dataframe(n=5):
    """미국 종목용 pandas DataFrame 생성."""
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "Open": [150.0 + i * 1.5 for i in range(n)],
        "High": [155.0 + i * 1.5 for i in range(n)],
        "Low": [145.0 + i * 1.5 for i in range(n)],
        "Close": [152.0 + i * 1.5 for i in range(n)],
        "Volume": [50000000 + i * 1000000 for i in range(n)],
    }, index=dates)


class TestGetHistoricalOhlcv(unittest.TestCase):
    """get_historical_ohlcv 테스트."""

    def test_kr_stock_fdr(self):
        """FDR mock으로 한국 종목 3년 일봉 조회."""
        df = _make_kr_dataframe(5)
        fdr_mock = MagicMock()
        fdr_mock.DataReader.return_value = df

        with patch.dict(sys.modules, {"FinanceDataReader": fdr_mock}):
            from kis_api import get_historical_ohlcv
            result = get_historical_ohlcv("005930", years=3)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 5)
        # 날짜 형식 YYYYMMDD
        for r in result:
            self.assertEqual(len(r["date"]), 8)
            self.assertTrue(r["date"].isdigit())
        # 시간순 정렬 (오래된→최신)
        dates = [r["date"] for r in result]
        self.assertEqual(dates, sorted(dates))
        # 한국 종목: int 가격
        self.assertIsInstance(result[0]["close"], int)
        self.assertIsInstance(result[0]["vol"], int)

    def test_us_stock_yfinance(self):
        """yfinance mock으로 미국 종목 3년 일봉 조회."""
        df = _make_us_dataframe(5)
        yf_mock = MagicMock()
        yf_mock.download.return_value = df

        with patch.dict(sys.modules, {"yfinance": yf_mock}):
            from kis_api import get_historical_ohlcv
            result = get_historical_ohlcv("AAPL", years=3)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 5)
        for r in result:
            self.assertEqual(len(r["date"]), 8)
            self.assertTrue(r["date"].isdigit())
        dates = [r["date"] for r in result]
        self.assertEqual(dates, sorted(dates))
        # 미국 종목: float 가격
        self.assertIsInstance(result[0]["close"], float)

    def test_fdr_import_error(self):
        """FDR 미설치 시 빈 리스트 반환."""
        # FinanceDataReader import가 실패하도록 설정
        with patch.dict(sys.modules, {"FinanceDataReader": None}):
            from kis_api import get_historical_ohlcv
            result = get_historical_ohlcv("005930", years=3)

        self.assertEqual(result, [])

    def test_yfinance_import_error(self):
        """yfinance 미설치 시 빈 리스트 반환."""
        with patch.dict(sys.modules, {"yfinance": None}):
            from kis_api import get_historical_ohlcv
            result = get_historical_ohlcv("AAPL", years=3)

        self.assertEqual(result, [])

    def test_empty_dataframe(self):
        """빈 DataFrame 반환 시 빈 리스트."""
        fdr_mock = MagicMock()
        fdr_mock.DataReader.return_value = pd.DataFrame()

        with patch.dict(sys.modules, {"FinanceDataReader": fdr_mock}):
            from kis_api import get_historical_ohlcv
            result = get_historical_ohlcv("005930", years=3)

        self.assertEqual(result, [])


class TestGetHistoricalSupply(unittest.TestCase):
    """get_historical_supply 테스트."""

    @patch("requests.post")
    def test_kr_stock_krx(self, mock_post):
        """KRX API mock으로 한국 종목 수급 조회."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "output": [
                {"TRD_DD": "2025/03/28", "FORN_PURE_QTY": "1,234", "ORGN_PURE_QTY": "-567"},
                {"TRD_DD": "2025/03/27", "FORN_PURE_QTY": "-890", "ORGN_PURE_QTY": "2,345"},
            ]
        }
        mock_post.return_value = mock_resp

        from kis_api import get_historical_supply
        result = get_historical_supply("005930", days=365)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        # 시간순 정렬
        self.assertEqual(result[0]["date"], "20250327")
        self.assertEqual(result[1]["date"], "20250328")
        # 값 파싱
        self.assertEqual(result[1]["foreign_net"], 1234)
        self.assertEqual(result[1]["institution_net"], -567)

    def test_us_stock_skip(self):
        """미국 종목은 빈 리스트 즉시 반환."""
        from kis_api import get_historical_supply
        result = get_historical_supply("AAPL", days=365)
        self.assertEqual(result, [])

    @patch("requests.post", side_effect=Exception("Connection timeout"))
    def test_krx_timeout(self, mock_post):
        """requests.post timeout 시 빈 리스트 반환."""
        from kis_api import get_historical_supply
        result = get_historical_supply("005930", days=365)
        self.assertEqual(result, [])

    @patch("requests.post")
    def test_krx_empty_response(self, mock_post):
        """빈 JSON 응답 시 빈 리스트."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_post.return_value = mock_resp

        from kis_api import get_historical_supply
        result = get_historical_supply("005930", days=365)
        self.assertEqual(result, [])


class TestBacktestYMode(unittest.TestCase):
    """get_backtest Y모드 테스트."""

    def _run(self, coro):
        return asyncio.run(coro)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="test_token")
    @patch("mcp_tools.get_historical_ohlcv")
    def test_y3_backtest_kr(self, mock_ohlcv, mock_token):
        """Y3 period로 한국 종목 백테스트."""
        candles = make_candles(200, base_price=50000)
        mock_ohlcv.return_value = candles

        from mcp_tools import _execute_tool
        result = self._run(_execute_tool("get_backtest", {
            "ticker": "005930",
            "period": "Y3",
            "strategy": "ma_cross",
        }))

        mock_ohlcv.assert_called_once()
        args = mock_ohlcv.call_args
        self.assertEqual(args[0][0], "005930")  # ticker
        self.assertEqual(args[0][1], 3)          # years
        # 결과에 에러가 없어야 함
        self.assertNotIn("error", result)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="test_token")
    @patch("mcp_tools.get_historical_ohlcv")
    def test_y3_backtest_us(self, mock_ohlcv, mock_token):
        """Y3 period로 미국 종목 백테스트."""
        candles = make_candles(200, base_price=150)
        # 미국 종목: float 가격
        for c in candles:
            c["open"] = float(c["open"])
            c["high"] = float(c["high"])
            c["low"] = float(c["low"])
            c["close"] = float(c["close"])
        mock_ohlcv.return_value = candles

        from mcp_tools import _execute_tool
        result = self._run(_execute_tool("get_backtest", {
            "ticker": "AAPL",
            "period": "Y3",
            "strategy": "ma_cross",
        }))

        mock_ohlcv.assert_called_once()
        args = mock_ohlcv.call_args
        self.assertEqual(args[0][0], "AAPL")
        self.assertNotIn("error", result)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="test_token")
    @patch("mcp_tools.get_historical_supply")
    @patch("mcp_tools.get_historical_ohlcv")
    def test_y_mode_supply_follow_with_krx(self, mock_ohlcv, mock_supply, mock_token):
        """Y모드 + supply_follow 전략에서 get_historical_supply 호출 확인."""
        candles = make_candles(200, base_price=50000)
        mock_ohlcv.return_value = candles
        supply_data = [
            {"date": c["date"], "foreign_net": 1000 + i, "institution_net": -500 + i}
            for i, c in enumerate(candles)
        ]
        mock_supply.return_value = supply_data

        from mcp_tools import _execute_tool
        result = self._run(_execute_tool("get_backtest", {
            "ticker": "005930",
            "period": "Y3",
            "strategy": "supply_follow",
        }))

        mock_ohlcv.assert_called_once()
        mock_supply.assert_called_once()
        supply_args = mock_supply.call_args
        self.assertEqual(supply_args[0][0], "005930")
        self.assertNotIn("error", result)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="test_token")
    @patch("mcp_tools.get_historical_ohlcv")
    def test_y_mode_fallback_on_failure(self, mock_ohlcv, mock_token):
        """FDR/yfinance 실패 시 에러 메시지 반환."""
        mock_ohlcv.return_value = []

        from mcp_tools import _execute_tool
        result = self._run(_execute_tool("get_backtest", {
            "ticker": "005930",
            "period": "Y3",
            "strategy": "ma_cross",
        }))

        self.assertIn("error", result)
        # 빈 리스트 → candles < 20 체크에서 "일봉 데이터 부족" 에러 발생
        self.assertTrue(
            "장기 데이터 조회 실패" in result["error"]
            or "일봉 데이터 부족" in result["error"]
        )

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="test_token")
    @patch("mcp_tools.get_historical_ohlcv")
    def test_y_mode_years_clamp(self, mock_ohlcv, mock_token):
        """Y6 -> 5년 제한, Y0 -> 1년 제한."""
        candles = make_candles(200, base_price=50000)
        mock_ohlcv.return_value = candles

        from mcp_tools import _execute_tool

        # Y6 -> 5년으로 제한
        self._run(_execute_tool("get_backtest", {
            "ticker": "005930",
            "period": "Y6",
            "strategy": "ma_cross",
        }))
        args = mock_ohlcv.call_args
        self.assertEqual(args[0][1], 5)  # max(1, min(6, 5)) = 5

        mock_ohlcv.reset_mock()
        mock_ohlcv.return_value = candles

        # Y0 -> 1년으로 제한
        self._run(_execute_tool("get_backtest", {
            "ticker": "005930",
            "period": "Y0",
            "strategy": "ma_cross",
        }))
        args = mock_ohlcv.call_args
        self.assertEqual(args[0][1], 1)  # max(1, min(0, 5)) = 1


if __name__ == "__main__":
    unittest.main()
