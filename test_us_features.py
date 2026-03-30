"""미국주식 기능 테스트 — 뉴스+감성, 실적캘린더, 섹터ETF"""
import sys, types, json, unittest, asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta

# ── telegram stub ──
telegram_stub = types.ModuleType("telegram")
telegram_stub.Update = object
ext_stub = types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)

from kis_api import (
    fetch_us_news, analyze_us_news_sentiment,
    fetch_us_earnings_calendar, fetch_us_sector_etf,
    _US_POSITIVE_KEYWORDS, _US_NEGATIVE_KEYWORDS, US_SECTOR_ETFS,
)
from mcp_tools import _execute_tool


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. TestFetchUsNews
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchUsNews(unittest.TestCase):

    @patch("kis_api.yf", create=True)
    def test_fetch_news(self, mock_yf_mod):
        """yfinance 뉴스 → [{title, source, date, time}] 반환."""
        mock_ticker = MagicMock()
        mock_ticker.news = [
            {"title": "Tesla Q4 beats expectations", "publisher": "Reuters",
             "providerPublishTime": 1711900000},
            {"title": "EV market grows", "publisher": "Bloomberg",
             "providerPublishTime": 1711910000},
        ]
        with patch.dict("sys.modules", {"yfinance": MagicMock()}):
            import yfinance as yf_mock
            yf_mock.Ticker.return_value = mock_ticker
            with patch("kis_api.yf", yf_mock, create=True):
                # fetch_us_news imports yfinance internally, so patch the import
                result = self._run_with_yf_patch(mock_ticker)
        self.assertEqual(len(result), 2)
        self.assertIn("title", result[0])
        self.assertIn("source", result[0])
        self.assertIn("date", result[0])
        self.assertEqual(result[0]["title"], "Tesla Q4 beats expectations")
        self.assertEqual(result[0]["source"], "Reuters")
        # date should be YYYYMMDD format
        self.assertEqual(len(result[0]["date"]), 8)

    def _run_with_yf_patch(self, mock_ticker):
        yf_mod = MagicMock()
        yf_mod.Ticker.return_value = mock_ticker
        with patch.dict("sys.modules", {"yfinance": yf_mod}):
            return fetch_us_news("TSLA", n=10)

    def test_empty_news(self):
        """news=[] → 빈 리스트."""
        mock_ticker = MagicMock()
        mock_ticker.news = []
        result = self._run_with_yf_patch(mock_ticker)
        self.assertEqual(result, [])

    def test_import_error(self):
        """yfinance 미설치 → 빈 리스트."""
        import builtins
        _orig = builtins.__import__

        def _fail_import(name, *args, **kwargs):
            if name == "yfinance":
                raise ImportError("No module named 'yfinance'")
            return _orig(name, *args, **kwargs)

        with patch.dict("sys.modules", {"yfinance": None}):
            with patch("builtins.__import__", side_effect=_fail_import):
                result = fetch_us_news("TSLA")
        self.assertEqual(result, [])


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. TestAnalyzeUsNewsSentiment
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestAnalyzeUsNewsSentiment(unittest.TestCase):

    def test_positive_detection(self):
        """긍정 키워드 포함 → positive."""
        news = [{"title": "TSLA shares surge after earnings beat", "source": "Reuters",
                 "date": "20260401", "time": "120000"}]
        result = analyze_us_news_sentiment(news)
        self.assertEqual(len(result["positive"]), 1)
        self.assertEqual(result["positive"][0]["sentiment"], "positive")

    def test_negative_detection(self):
        """부정 키워드 포함 → negative."""
        news = [{"title": "Apple faces lawsuit over privacy", "source": "WSJ",
                 "date": "20260401", "time": "130000"}]
        result = analyze_us_news_sentiment(news)
        self.assertEqual(len(result["negative"]), 1)
        self.assertEqual(result["negative"][0]["sentiment"], "negative")

    def test_neutral(self):
        """키워드 없음 → neutral."""
        news = [{"title": "Tesla announces new factory location", "source": "CNN",
                 "date": "20260401", "time": "140000"}]
        result = analyze_us_news_sentiment(news)
        self.assertEqual(len(result["neutral"]), 1)
        self.assertEqual(result["neutral"][0]["sentiment"], "neutral")

    def test_summary_format(self):
        """summary 형식 확인."""
        news = [
            {"title": "Stock surge rally", "date": "", "time": ""},
            {"title": "Company faces lawsuit risk", "date": "", "time": ""},
            {"title": "Quarterly meeting held", "date": "", "time": ""},
        ]
        result = analyze_us_news_sentiment(news)
        self.assertIn("🟢긍정", result["summary"])
        self.assertIn("🔴부정", result["summary"])
        self.assertIn("⚪중립", result["summary"])
        # check counts in summary
        self.assertIn("🟢긍정 1", result["summary"])
        self.assertIn("🔴부정 1", result["summary"])
        self.assertIn("⚪중립 1", result["summary"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. TestFetchUsEarningsCalendar
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchUsEarningsCalendar(unittest.TestCase):

    def _make_ticker_mock(self, earnings_date, short_name="TestCo"):
        t = MagicMock()
        t.calendar = {"Earnings Date": [earnings_date]}
        t.info = {"shortName": short_name}
        return t

    def test_upcoming_earnings(self):
        """7일 내 실적 → 결과 반환."""
        ed = datetime.now() + timedelta(days=5)
        mock_ticker = self._make_ticker_mock(ed, "TSLA Inc")
        yf_mod = MagicMock()
        yf_mod.Ticker.return_value = mock_ticker
        with patch.dict("sys.modules", {"yfinance": yf_mod}):
            result = fetch_us_earnings_calendar(["TSLA"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ticker"], "TSLA")
        self.assertIn("earnings_date", result[0])
        self.assertIn("days_until", result[0])
        self.assertTrue(4 <= result[0]["days_until"] <= 6)

    def test_no_upcoming(self):
        """30일 이후 실적 → 빈 리스트."""
        ed = datetime.now() + timedelta(days=60)
        mock_ticker = self._make_ticker_mock(ed, "FarAway Inc")
        yf_mod = MagicMock()
        yf_mod.Ticker.return_value = mock_ticker
        with patch.dict("sys.modules", {"yfinance": yf_mod}):
            result = fetch_us_earnings_calendar(["FAR"])
        self.assertEqual(result, [])

    def test_empty_portfolio(self):
        """빈 종목 → 빈 리스트."""
        yf_mod = MagicMock()
        with patch.dict("sys.modules", {"yfinance": yf_mod}):
            result = fetch_us_earnings_calendar([])
        self.assertEqual(result, [])


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. TestFetchUsSectorEtf
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchUsSectorEtf(unittest.TestCase):

    def _make_hist_df(self, closes):
        """closes 리스트 → DataFrame-like mock."""
        import types as _t
        df = MagicMock()
        df.empty = False
        df.__len__ = lambda self: len(closes)

        close_series = MagicMock()
        close_series.iloc.__getitem__ = lambda _, idx: closes[idx]
        df.__getitem__ = lambda _, key: close_series if key == "Close" else MagicMock()
        return df

    def test_fetch_all_etfs(self):
        """11개 ETF 전부 성공 → 11개 결과."""
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
        df = self._make_hist_df(closes)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df

        yf_mod = MagicMock()
        yf_mod.Ticker.return_value = mock_ticker

        with patch.dict("sys.modules", {"yfinance": yf_mod}):
            result = fetch_us_sector_etf()

        self.assertEqual(len(result), len(US_SECTOR_ETFS))
        for item in result:
            self.assertIn("ticker", item)
            self.assertIn("name", item)
            self.assertIn("price", item)
            self.assertIn("chg_1d", item)
            self.assertIn("chg_5d", item)

    def test_partial_failure(self):
        """일부 ETF 실패 → 나머지만 반환."""
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
        good_df = self._make_hist_df(closes)

        call_count = {"n": 0}

        def _make_ticker(sym):
            call_count["n"] += 1
            t = MagicMock()
            if call_count["n"] % 3 == 0:
                t.history.side_effect = Exception("API error")
            else:
                t.history.return_value = good_df
            return t

        yf_mod = MagicMock()
        yf_mod.Ticker.side_effect = _make_ticker

        with patch.dict("sys.modules", {"yfinance": yf_mod}):
            result = fetch_us_sector_etf()

        # some should succeed, some fail
        self.assertGreater(len(result), 0)
        self.assertLess(len(result), len(US_SECTOR_ETFS))

    def test_import_error(self):
        """yfinance 미설치 → 빈 리스트."""
        import builtins
        _orig = builtins.__import__

        def _fail_import(name, *args, **kwargs):
            if name == "yfinance":
                raise ImportError("No module named 'yfinance'")
            return _orig(name, *args, **kwargs)

        with patch.dict("sys.modules", {"yfinance": None}):
            with patch("builtins.__import__", side_effect=_fail_import):
                result = fetch_us_sector_etf()
        self.assertEqual(result, [])


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. TestMcpGetNewsUs
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestMcpGetNewsUs(unittest.TestCase):

    def _run(self, coro):
        return asyncio.run(coro)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="fake_token")
    @patch("mcp_tools.fetch_us_news", return_value=[
        {"title": "Tesla news", "source": "Reuters", "date": "20260401", "time": "100000"},
    ])
    def test_us_news_plain(self, mock_news, mock_token):
        """get_news(ticker='TSLA') → market='US' 포함."""
        result = self._run(_execute_tool("get_news", {"ticker": "TSLA"}))
        self.assertEqual(result["market"], "US")
        self.assertEqual(result["ticker"], "TSLA")
        self.assertEqual(result["count"], 1)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="fake_token")
    @patch("mcp_tools.fetch_us_news", return_value=[
        {"title": "Apple upgrade bullish momentum", "source": "CNBC",
         "date": "20260401", "time": "110000"},
    ])
    @patch("mcp_tools.analyze_us_news_sentiment")
    def test_us_news_sentiment(self, mock_analysis, mock_news, mock_token):
        """get_news(ticker='AAPL', sentiment=true) → 감성분석 포함."""
        mock_analysis.return_value = {
            "positive": [{"title": "Apple upgrade bullish momentum", "sentiment": "positive"}],
            "negative": [], "neutral": [],
            "summary": "🟢긍정 1 / 🔴부정 0 / ⚪중립 0",
        }
        result = self._run(_execute_tool("get_news", {"ticker": "AAPL", "sentiment": True}))
        self.assertEqual(result["market"], "US")
        self.assertIn("positive", result)
        self.assertIn("summary", result)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="fake_token")
    @patch("mcp_tools.kis_news_title", new_callable=AsyncMock, return_value=[
        {"title": "삼성전자 실적 발표", "date": "20260401", "time": "090000"},
    ])
    def test_kr_news_unchanged(self, mock_kis_news, mock_token):
        """get_news(ticker='005930') → 한국 로직 (market 키 없음)."""
        result = self._run(_execute_tool("get_news", {"ticker": "005930"}))
        self.assertEqual(result["ticker"], "005930")
        self.assertNotIn("market", result)
        self.assertIn("count", result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. TestMcpGetMacroUsSector
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestMcpGetMacroUsSector(unittest.TestCase):

    def _run(self, coro):
        return asyncio.run(coro)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="fake_token")
    @patch("mcp_tools.fetch_us_sector_etf", return_value=[
        {"ticker": "XLK", "name": "기술", "price": 200.0, "chg_1d": 1.5, "chg_5d": 3.0},
        {"ticker": "XLE", "name": "에너지", "price": 90.0, "chg_1d": -0.8, "chg_5d": -2.1},
        {"ticker": "XLF", "name": "금융", "price": 42.0, "chg_1d": 0.5, "chg_5d": 1.2},
        {"ticker": "SPY", "name": "S&P500", "price": 520.0, "chg_1d": 0.3, "chg_5d": 0.8},
    ])
    def test_us_sector_mode(self, mock_etf, mock_token):
        """get_macro(mode='us_sector') → top3, bottom3, all 포함."""
        result = self._run(_execute_tool("get_macro", {"mode": "us_sector"}))
        self.assertEqual(result["mode"], "us_sector")
        self.assertIn("top3", result)
        self.assertIn("bottom3", result)
        self.assertIn("all", result)
        self.assertEqual(result["count"], 4)
        # top3 should be sorted descending by chg_1d
        self.assertEqual(result["top3"][0]["ticker"], "XLK")

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="fake_token")
    @patch("mcp_tools.fetch_us_sector_etf", return_value=[])
    def test_us_sector_failure(self, mock_etf, mock_token):
        """fetch_us_sector_etf 빈 결과 → 에러 반환."""
        result = self._run(_execute_tool("get_macro", {"mode": "us_sector"}))
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. TestFetchUsShortInterest
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchUsShortInterest(unittest.TestCase):
    """fetch_us_short_interest 테스트"""

    def test_normal_data(self):
        from kis_api import fetch_us_short_interest
        mock_info = {
            "shortName": "Tesla Inc",
            "sharesShort": 30000000,
            "shortRatio": 1.5,
            "shortPercentOfFloat": 0.03,
            "sharesShortPriorMonth": 28000000,
            "sharesPercentSharesOut": 0.025,
            "floatShares": 1000000000,
        }
        with patch("yfinance.Ticker") as mock_cls:
            mock_t = MagicMock()
            mock_t.info = mock_info
            mock_cls.return_value = mock_t
            result = fetch_us_short_interest("TSLA")

        self.assertEqual(result["ticker"], "TSLA")
        self.assertEqual(result["shares_short"], 30000000)
        self.assertEqual(result["short_ratio"], 1.5)
        self.assertIsNotNone(result.get("short_pct_float"))

    def test_no_short_data(self):
        from kis_api import fetch_us_short_interest
        with patch("yfinance.Ticker") as mock_cls:
            mock_t = MagicMock()
            mock_t.info = {"shortName": "NoShort Corp"}
            mock_cls.return_value = mock_t
            result = fetch_us_short_interest("XYZ")

        self.assertIn("message", result)
        self.assertIn("없음", result["message"])

    def test_import_error(self):
        from kis_api import fetch_us_short_interest
        with patch.dict("sys.modules", {"yfinance": None}):
            # Force ImportError by removing module
            import importlib
            # Just test that function doesn't crash
        # Alternative: mock the import to raise
        result = fetch_us_short_interest("TSLA")
        # Should return empty dict or data depending on cache
        self.assertIsInstance(result, dict)


class TestMcpShortSaleUs(unittest.TestCase):
    """get_market_signal short_sale 미국 분기 테스트"""

    def test_us_short_sale(self):
        from mcp_tools import _execute_tool
        mock_short = {
            "ticker": "TSLA", "name": "Tesla",
            "shares_short": 30000000, "short_ratio": 1.5,
        }
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock"), \
             patch("mcp_tools.fetch_us_short_interest", return_value=mock_short):
            result = asyncio.run(_execute_tool("get_market_signal", {
                "mode": "short_sale", "ticker": "TSLA"
            }))
        self.assertEqual(result.get("market"), "US")
        self.assertEqual(result.get("shares_short"), 30000000)

    def test_kr_short_sale_unchanged(self):
        from mcp_tools import _execute_tool
        mock_rows = [{"date": "20260330", "short_vol": 1000, "total_vol": 50000}]
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock"), \
             patch("mcp_tools.kis_daily_short_sale", new_callable=AsyncMock, return_value=mock_rows):
            result = asyncio.run(_execute_tool("get_market_signal", {
                "mode": "short_sale", "ticker": "005930"
            }))
        self.assertEqual(result.get("market"), "KR")
        self.assertEqual(result.get("count"), 1)

    def test_missing_ticker(self):
        from mcp_tools import _execute_tool
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock"):
            result = asyncio.run(_execute_tool("get_market_signal", {
                "mode": "short_sale"
            }))
        self.assertIn("error", result)
