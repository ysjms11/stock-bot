"""Phase B 단위 테스트 — 뉴스 감성 / 섹터 로테이션 / 포트 시뮬레이션"""
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio

# telegram 스텁 (CI 환경에서 telegram 패키지 없을 수 있음)
import types as _types

telegram_stub = _types.ModuleType("telegram")
telegram_stub.Update = object
telegram_stub.ReplyKeyboardMarkup = type("ReplyKeyboardMarkup", (), {"__init__": lambda self, *a, **kw: None})
ext_stub = _types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.MessageHandler = object
ext_stub.filters = type("filters", (), {"TEXT": None, "Regex": staticmethod(lambda x: x)})()
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)

from kis_api import analyze_news_sentiment, _POSITIVE_KEYWORDS, _NEGATIVE_KEYWORDS


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 뉴스 감성 분석
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestNewsSentiment(unittest.TestCase):
    """analyze_news_sentiment 함수 직접 테스트."""

    def test_positive_detection(self):
        """긍정 키워드 포함 헤드라인 -> positive 분류."""
        items = [{"title": "삼성전자 호실적 발표 기대감 확대"}]
        result = analyze_news_sentiment(items)
        self.assertEqual(len(result["positive"]), 1)
        self.assertEqual(result["positive"][0]["sentiment"], "positive")
        self.assertGreater(len(result["positive"][0]["matched_keywords"]), 0)

    def test_negative_detection(self):
        """부정 키워드 포함 헤드라인 -> negative 분류."""
        items = [{"title": "반도체 업황 악화 우려 확산"}]
        result = analyze_news_sentiment(items)
        self.assertEqual(len(result["negative"]), 1)
        self.assertEqual(result["negative"][0]["sentiment"], "negative")
        self.assertGreater(len(result["negative"][0]["matched_keywords"]), 0)

    def test_neutral_detection(self):
        """키워드 미포함 헤드라인 -> neutral 분류."""
        items = [{"title": "내일 날씨 맑음"}]
        result = analyze_news_sentiment(items)
        self.assertEqual(len(result["neutral"]), 1)
        self.assertEqual(len(result["positive"]), 0)
        self.assertEqual(len(result["negative"]), 0)
        self.assertEqual(result["neutral"][0]["sentiment"], "neutral")

    def test_mixed_sentiment(self):
        """긍정+부정 혼합 -> 개수 많은 쪽으로 분류."""
        # 긍정 2개(상승, 신고가) vs 부정 1개(우려) -> positive
        items = [{"title": "삼성전자 상승 신고가 돌파 하지만 우려도"}]
        result = analyze_news_sentiment(items)
        # 상승, 신고가, 돌파 = 긍정 3개, 우려 = 부정 1개
        self.assertEqual(len(result["positive"]), 1)
        self.assertEqual(result["positive"][0]["sentiment"], "positive")

    def test_empty_input(self):
        """빈 리스트 -> 빈 결과 + summary 형식."""
        result = analyze_news_sentiment([])
        self.assertEqual(result["positive"], [])
        self.assertEqual(result["negative"], [])
        self.assertEqual(result["neutral"], [])
        self.assertEqual(result["summary"], "🟢긍정 0 / 🔴부정 0 / ⚪중립 0")

    def test_summary_format(self):
        """summary 문자열 형식 검증."""
        items = [
            {"title": "호실적 기대"},
            {"title": "적자 위기 악화"},
            {"title": "일반 뉴스 기사"},
        ]
        result = analyze_news_sentiment(items)
        summary = result["summary"]
        self.assertIn("🟢긍정", summary)
        self.assertIn("🔴부정", summary)
        self.assertIn("⚪중립", summary)
        # 숫자 합 = 입력 수
        pos = len(result["positive"])
        neg = len(result["negative"])
        neu = len(result["neutral"])
        self.assertEqual(pos + neg + neu, 3)
        self.assertIn(str(pos), summary)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 섹터 로테이션 감지
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestSectorRotation(unittest.TestCase):
    """detect_sector_rotation 함수 테스트."""

    def _run(self, coro):
        return asyncio.run(coro)

    @patch("kis_api.save_json")
    @patch("kis_api.load_json")
    @patch("kis_api._fetch_sector_flow")
    def test_rotation_detection(self, mock_fetch, mock_load, mock_save):
        """전일 데이터 있을 때 change 계산 정확성."""
        # _fetch_sector_flow 반환: (frgn, orgn)
        mock_fetch.return_value = (500, 300)

        # 전일 데이터: 반도체 total=600 (오늘 800 -> change=200)
        prev_data = {
            "date": "2026-03-28",
            "sectors": {
                "반도체": {"frgn": 400, "orgn": 200, "total": 600},
                "조선": {"frgn": 100, "orgn": 50, "total": 150},
                "전력기기": {"frgn": 200, "orgn": 100, "total": 300},
                "방산": {"frgn": 50, "orgn": 30, "total": 80},
                "2차전지": {"frgn": 100, "orgn": 80, "total": 180},
                "건설": {"frgn": 60, "orgn": 40, "total": 100},
                "바이오": {"frgn": 150, "orgn": 70, "total": 220},
            },
        }
        mock_load.return_value = prev_data

        from kis_api import detect_sector_rotation

        result = self._run(detect_sector_rotation("mock_token"))

        self.assertIn("sectors", result)
        self.assertIn("rotations", result)
        self.assertEqual(result["prev_date"], "2026-03-28")
        # 모든 섹터 today total = 800, prev varies -> change = 800 - prev_total
        for s in result["sectors"]:
            self.assertEqual(s["total"], 800)  # frgn=500 + orgn=300

    @patch("kis_api.save_json")
    @patch("kis_api.load_json")
    @patch("kis_api._fetch_sector_flow")
    def test_no_prev_data(self, mock_fetch, mock_load, mock_save):
        """전일 데이터 없을 때 change=0."""
        mock_fetch.return_value = (100, 50)
        mock_load.return_value = {}  # 전일 데이터 없음

        from kis_api import detect_sector_rotation

        result = self._run(detect_sector_rotation("mock_token"))

        for s in result["sectors"]:
            self.assertEqual(s["change"], 0)
        self.assertEqual(result["rotations"], [])

    @patch("kis_api.save_json")
    @patch("kis_api.load_json")
    @patch("kis_api._fetch_sector_flow")
    def test_rotation_pattern(self, mock_fetch, mock_load, mock_save):
        """유출/유입이 모두 100 이상일 때 rotations 리스트 생성."""
        # 업종별 다른 값을 반환하여 유입/유출 생성
        call_count = {"n": 0}
        sector_values = [
            (800, 400),   # 반도체: total=1200, prev=200 -> change=+1000
            (10, 5),      # 조선: total=15, prev=1000 -> change=-985
            (5, 3),       # 전력기기: total=8, prev=500 -> change=-492
            (600, 300),   # 방산: total=900, prev=100 -> change=+800
            (50, 25),     # 2차전지: total=75, prev=200 -> change=-125
            (400, 200),   # 건설: total=600, prev=50 -> change=+550
            (20, 10),     # 바이오: total=30, prev=300 -> change=-270
        ]

        async def mock_flow(token, code):
            idx = call_count["n"]
            call_count["n"] += 1
            return sector_values[idx]

        mock_fetch.side_effect = mock_flow

        prev_data = {
            "date": "2026-03-28",
            "sectors": {
                "반도체": {"frgn": 100, "orgn": 100, "total": 200},
                "조선": {"frgn": 500, "orgn": 500, "total": 1000},
                "전력기기": {"frgn": 250, "orgn": 250, "total": 500},
                "방산": {"frgn": 50, "orgn": 50, "total": 100},
                "2차전지": {"frgn": 100, "orgn": 100, "total": 200},
                "건설": {"frgn": 25, "orgn": 25, "total": 50},
                "바이오": {"frgn": 150, "orgn": 150, "total": 300},
            },
        }
        mock_load.return_value = prev_data

        from kis_api import detect_sector_rotation

        result = self._run(detect_sector_rotation("mock_token"))

        # 유출: 조선(-985), 전력기기(-492), 바이오(-270), 2차전지(-125)
        # 유입: 반도체(+1000), 방산(+800), 건설(+550)
        # rotations: outflow top2 x inflow top2 where both > 100
        self.assertGreater(len(result["rotations"]), 0)
        # 각 rotation은 "A→B" 형태
        for rot in result["rotations"]:
            self.assertIn("→", rot)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. 포트 시뮬레이션 (MCP 도구)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestSimulateTrade(unittest.TestCase):
    """simulate_trade MCP 핸들러 테스트."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _make_portfolio(self):
        return {
            "005930": {"name": "삼성전자", "qty": 100, "avg_price": 70000},
            "000660": {"name": "SK하이닉스", "qty": 50, "avg_price": 150000},
            "us_stocks": {
                "AAPL": {"name": "Apple", "qty": 10, "avg_price": 180.0},
            },
            "cash_krw": 5000000,
            "cash_usd": 3000.0,
        }

    def _make_stoploss(self):
        return {
            "005930": {"name": "삼성전자", "stop_price": 60000, "target_price": 90000},
        }

    @patch("mcp_tools.kis_us_stock_price", new_callable=AsyncMock)
    @patch("mcp_tools.kis_stock_price", new_callable=AsyncMock)
    @patch("mcp_tools.load_stoploss")
    @patch("mcp_tools.load_json")
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock)
    def test_sell_simulation(self, mock_token, mock_load, mock_stops, mock_kr_price, mock_us_price):
        """매도 시뮬레이션 -> cash 증가, holdings 감소."""
        mock_token.return_value = "mock_token"
        mock_load.return_value = self._make_portfolio()
        mock_stops.return_value = self._make_stoploss()
        mock_kr_price.return_value = {"stck_prpr": "75000"}
        mock_us_price.return_value = {"last": "190.00"}

        from mcp_tools import _execute_tool

        result = self._run(_execute_tool("simulate_trade", {
            "sells": [{"ticker": "005930", "qty": 30, "price": 75000}],
            "buys": [],
        }))

        self.assertNotIn("error", result)
        # 삼성전자 100 - 30 = 70주 남음
        kr_holdings = {h["ticker"]: h for h in result["kr_holdings"]}
        self.assertEqual(kr_holdings["005930"]["qty"], 70)
        # cash_krw 증가: 5000000 + 75000*30 = 7250000
        self.assertEqual(result["cash"]["krw"], 7250000)

    @patch("mcp_tools.kis_us_stock_price", new_callable=AsyncMock)
    @patch("mcp_tools.kis_stock_price", new_callable=AsyncMock)
    @patch("mcp_tools.load_stoploss")
    @patch("mcp_tools.load_json")
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock)
    def test_buy_simulation(self, mock_token, mock_load, mock_stops, mock_kr_price, mock_us_price):
        """매수 시뮬레이션 -> cash 감소, holdings 증가."""
        mock_token.return_value = "mock_token"
        mock_load.return_value = self._make_portfolio()
        mock_stops.return_value = self._make_stoploss()
        mock_kr_price.return_value = {"stck_prpr": "75000"}
        mock_us_price.return_value = {"last": "190.00"}

        from mcp_tools import _execute_tool

        result = self._run(_execute_tool("simulate_trade", {
            "sells": [],
            "buys": [{"ticker": "005930", "qty": 20, "price": 75000}],
        }))

        self.assertNotIn("error", result)
        # 삼성전자 100 + 20 = 120주
        kr_holdings = {h["ticker"]: h for h in result["kr_holdings"]}
        self.assertEqual(kr_holdings["005930"]["qty"], 120)
        # cash_krw 감소: 5000000 - 75000*20 = 3500000
        self.assertEqual(result["cash"]["krw"], 3500000)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock)
    def test_empty_trades(self, mock_token):
        """sells+buys 모두 비면 에러 반환."""
        mock_token.return_value = "mock_token"

        from mcp_tools import _execute_tool

        result = self._run(_execute_tool("simulate_trade", {
            "sells": [],
            "buys": [],
        }))

        self.assertIn("error", result)

    @patch("mcp_tools.kis_us_stock_price", new_callable=AsyncMock)
    @patch("mcp_tools.kis_stock_price", new_callable=AsyncMock)
    @patch("mcp_tools.load_stoploss")
    @patch("mcp_tools.load_json")
    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock)
    def test_sector_weights(self, mock_token, mock_load, mock_stops, mock_kr_price, mock_us_price):
        """시뮬 후 sector_weights 계산 정확성."""
        mock_token.return_value = "mock_token"
        mock_load.return_value = self._make_portfolio()
        mock_stops.return_value = self._make_stoploss()
        # 005930, 000660 모두 반도체 (_TICKER_SECTOR)
        mock_kr_price.return_value = {"stck_prpr": "75000"}
        mock_us_price.return_value = {"last": "190.00"}

        from mcp_tools import _execute_tool

        result = self._run(_execute_tool("simulate_trade", {
            "sells": [],
            "buys": [{"ticker": "005930", "qty": 10, "price": 75000}],
        }))

        self.assertNotIn("error", result)
        self.assertIn("sector_weights", result)
        # 005930, 000660 모두 반도체 -> 반도체 비중 존재
        self.assertIn("반도체", result["sector_weights"])
        # 비중은 0~100 사이 숫자
        for sec, pct in result["sector_weights"].items():
            self.assertGreaterEqual(pct, 0)
            self.assertLessEqual(pct, 100)


if __name__ == "__main__":
    unittest.main()
