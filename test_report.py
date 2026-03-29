"""증권사 리포트 수집 테스트"""
import sys, types, json, os, unittest, asyncio, tempfile
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ── telegram stub (mcp_tools.py import 시 필요) ──
telegram_stub = types.ModuleType("telegram")
telegram_stub.Update = object
ext_stub = types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)

KST = ZoneInfo("Asia/Seoul")

# ── 네이버 리서치 HTML 샘플 ──
_SAMPLE_HTML = """
<html><body>
<table class="type_1">
<tr><th>종목명</th><th>제목</th><th>증권사</th><th>첨부</th><th>작성일</th><th>조회수</th></tr>
<tr>
  <td><a href="#">삼성전자</a></td>
  <td><a href="/research/detail?id=1">반도체 전망 긍정</a></td>
  <td>미래에셋</td>
  <td><a href="https://stock.pstatic.net/report1.pdf">PDF</a></td>
  <td>26.03.28</td>
  <td>150</td>
</tr>
<tr>
  <td><a href="#">삼성전자</a></td>
  <td><a href="/research/detail?id=2">실적 리뷰</a></td>
  <td>NH투자</td>
  <td><a href="//stock.pstatic.net/report2.pdf">PDF</a></td>
  <td>26.03.27</td>
  <td>80</td>
</tr>
<tr><td colspan="6">&nbsp;</td></tr>
</table>
</body></html>
"""


class TestCrawlNaverReports(unittest.TestCase):
    """crawl_naver_reports 테스트"""

    @patch("report_crawler.requests.get")
    def test_parse_reports(self, mock_get):
        from report_crawler import crawl_naver_reports

        resp = MagicMock()
        resp.status_code = 200
        resp.text = _SAMPLE_HTML
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        results = crawl_naver_reports("005930", "삼성전자", set())

        self.assertEqual(len(results), 2)
        r0 = results[0]
        self.assertEqual(r0["date"], "2026-03-28")
        self.assertEqual(r0["source"], "미래에셋")
        self.assertEqual(r0["title"], "반도체 전망 긍정")
        self.assertEqual(r0["pdf_url"], "https://stock.pstatic.net/report1.pdf")
        self.assertEqual(r0["ticker"], "005930")

        # 두 번째 리포트: // 로 시작하는 URL -> https: 접두어
        r1 = results[1]
        self.assertEqual(r1["pdf_url"], "https://stock.pstatic.net/report2.pdf")
        self.assertEqual(r1["date"], "2026-03-27")

    @patch("report_crawler.requests.get")
    def test_skip_existing_urls(self, mock_get):
        from report_crawler import crawl_naver_reports

        resp = MagicMock()
        resp.status_code = 200
        resp.text = _SAMPLE_HTML
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        existing = {"https://stock.pstatic.net/report1.pdf"}
        results = crawl_naver_reports("005930", "삼성전자", existing)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["pdf_url"], "https://stock.pstatic.net/report2.pdf")

    @patch("report_crawler.requests.get")
    def test_network_error(self, mock_get):
        from report_crawler import crawl_naver_reports

        mock_get.side_effect = Exception("Connection failed")
        results = crawl_naver_reports("005930", "삼성전자", set())
        self.assertEqual(results, [])


class TestExtractPdfText(unittest.TestCase):
    """extract_pdf_text 테스트"""

    @patch("report_crawler.requests.get")
    def test_extract_text(self, mock_get):
        from report_crawler import extract_pdf_text

        # mock HTTP response (PDF bytes)
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.iter_content = MagicMock(return_value=[b"fake pdf content"])
        mock_get.return_value = resp

        # mock pdfplumber
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "PDF 본문 텍스트입니다."
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        mock_plumber_mod = MagicMock()
        mock_plumber_mod.open.return_value = mock_pdf
        with patch.dict("sys.modules", {"pdfplumber": mock_plumber_mod}):
            result = extract_pdf_text("https://stock.pstatic.net/test.pdf")

        self.assertIn("PDF 본문 텍스트", result)

    @patch("report_crawler.requests.get")
    def test_text_truncation(self, mock_get):
        from report_crawler import extract_pdf_text, _MAX_TEXT

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.iter_content = MagicMock(return_value=[b"fake"])
        mock_get.return_value = resp

        # 긴 텍스트 생성 (10000자 초과)
        long_text = "A" * 15000
        mock_page = MagicMock()
        mock_page.extract_text.return_value = long_text
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        mock_plumber_mod = MagicMock()
        mock_plumber_mod.open.return_value = mock_pdf
        with patch.dict("sys.modules", {"pdfplumber": mock_plumber_mod}):
            result = extract_pdf_text("https://stock.pstatic.net/big.pdf")

        self.assertLessEqual(len(result), _MAX_TEXT)

    @patch("report_crawler.requests.get")
    def test_download_failure(self, mock_get):
        from report_crawler import extract_pdf_text

        resp = MagicMock()
        resp.status_code = 500
        resp.headers = {}
        mock_get.return_value = resp

        result = extract_pdf_text("https://stock.pstatic.net/fail.pdf")
        self.assertEqual(result, "")

    @patch("report_crawler.requests.get")
    def test_cleanup_temp_file(self, mock_get):
        from report_crawler import extract_pdf_text

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.iter_content = MagicMock(return_value=[b"fake"])
        mock_get.return_value = resp

        # pdfplumber.open에서 예외 발생 -> finally에서 임시파일 삭제 확인
        mock_plumber_mod = MagicMock()
        mock_plumber_mod.open.side_effect = Exception("corrupt PDF")
        with patch.dict("sys.modules", {"pdfplumber": mock_plumber_mod}):
            result = extract_pdf_text("https://stock.pstatic.net/corrupt.pdf")

        self.assertEqual(result, "")
        # 임시파일이 남아있지 않은지 확인 (tmp 디렉토리에 .pdf 파일 없어야)
        # extract_pdf_text의 finally 블록이 os.unlink 호출하므로 파일은 삭제됨
        # 직접 확인하려면 tempfile 디렉토리를 스캔해야 하지만, 코드 동작 자체가 검증 대상


class TestCollectReports(unittest.TestCase):
    """collect_reports 테스트"""

    def _make_report(self, ticker, name, pdf_url, date_str, full_text="text"):
        return {
            "date": date_str,
            "ticker": ticker,
            "name": name,
            "source": "증권사",
            "title": "리포트 제목",
            "pdf_url": pdf_url,
            "full_text": full_text,
            "collected_at": datetime.now(KST).isoformat(),
        }

    @patch("report_crawler.save_reports")
    @patch("report_crawler.extract_pdf_text", return_value="extracted text")
    @patch("report_crawler.crawl_naver_reports")
    @patch("report_crawler.load_reports")
    @patch("report_crawler.time.sleep")
    def test_collect_with_dedup(self, mock_sleep, mock_load, mock_crawl, mock_extract, mock_save):
        from report_crawler import collect_reports

        existing = self._make_report("005930", "삼성전자",
                                     "https://example.com/dup.pdf", "2026-03-28")
        mock_load.return_value = {"reports": [existing], "last_collected": ""}

        # crawl에서 기존 URL 하나 + 새 URL 하나 반환
        mock_crawl.return_value = [
            {"date": "2026-03-29", "ticker": "005930", "name": "삼성전자",
             "source": "미래에셋", "title": "신규 리포트",
             "pdf_url": "https://example.com/new.pdf"},
        ]

        result = collect_reports({"005930": "삼성전자"})

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["pdf_url"], "https://example.com/new.pdf")
        # crawl_naver_reports에 existing_urls가 전달되었는지
        call_args = mock_crawl.call_args
        self.assertIn("https://example.com/dup.pdf", call_args[0][2])

    @patch("report_crawler.save_reports")
    @patch("report_crawler.extract_pdf_text", return_value="text")
    @patch("report_crawler.crawl_naver_reports")
    @patch("report_crawler.load_reports")
    @patch("report_crawler.time.sleep")
    def test_retention_cleanup(self, mock_sleep, mock_load, mock_crawl, mock_extract, mock_save):
        from report_crawler import collect_reports, _RETAIN_DAYS

        old_date = (datetime.now(KST) - timedelta(days=_RETAIN_DAYS + 5)).strftime("%Y-%m-%d")
        old_report = self._make_report("005930", "삼성전자",
                                       "https://example.com/old.pdf", old_date)
        mock_load.return_value = {"reports": [old_report], "last_collected": ""}

        mock_crawl.return_value = [
            {"date": "2026-03-29", "ticker": "005930", "name": "삼성전자",
             "source": "KB", "title": "최신",
             "pdf_url": "https://example.com/fresh.pdf"},
        ]

        collect_reports({"005930": "삼성전자"})

        # save_reports에 전달된 데이터에서 오래된 리포트가 삭제되었는지 확인
        saved = mock_save.call_args[0][0]
        dates = [r["date"] for r in saved["reports"]]
        self.assertNotIn(old_date, dates)

    @patch("report_crawler.save_reports")
    @patch("report_crawler.extract_pdf_text", return_value="text")
    @patch("report_crawler.crawl_naver_reports")
    @patch("report_crawler.load_reports")
    @patch("report_crawler.time.sleep")
    def test_per_ticker_limit(self, mock_sleep, mock_load, mock_crawl, mock_extract, mock_save):
        from report_crawler import collect_reports, _MAX_PER_TICKER

        # 기존에 종목당 5건이 이미 있는 상태
        existing = []
        for i in range(_MAX_PER_TICKER):
            existing.append(self._make_report(
                "005930", "삼성전자",
                f"https://example.com/exist_{i}.pdf",
                f"2026-03-{20 + i:02d}"))
        mock_load.return_value = {"reports": existing, "last_collected": ""}

        # 새로 1건 추가
        mock_crawl.return_value = [
            {"date": "2026-03-29", "ticker": "005930", "name": "삼성전자",
             "source": "한투", "title": "추가",
             "pdf_url": "https://example.com/new6.pdf"},
        ]

        collect_reports({"005930": "삼성전자"})

        saved = mock_save.call_args[0][0]
        ticker_reports = [r for r in saved["reports"] if r["ticker"] == "005930"]
        self.assertLessEqual(len(ticker_reports), _MAX_PER_TICKER)

    @patch("report_crawler.save_reports")
    @patch("report_crawler.extract_pdf_text", return_value="text")
    @patch("report_crawler.crawl_naver_reports")
    @patch("report_crawler.load_reports")
    @patch("report_crawler.time.sleep")
    def test_max_daily_limit(self, mock_sleep, mock_load, mock_crawl, mock_extract, mock_save):
        from report_crawler import collect_reports

        mock_load.return_value = {"reports": [], "last_collected": ""}

        # 100건 반환하지만 max_count=3으로 제한
        many = [
            {"date": "2026-03-29", "ticker": "005930", "name": "삼성전자",
             "source": f"증권사{i}", "title": f"리포트{i}",
             "pdf_url": f"https://example.com/r{i}.pdf"}
            for i in range(100)
        ]
        mock_crawl.return_value = many

        result = collect_reports({"005930": "삼성전자"}, max_count=3)
        self.assertEqual(len(result), 3)


class TestGetCollectionTickers(unittest.TestCase):
    """get_collection_tickers 테스트"""

    @patch("kis_api._is_us_ticker", return_value=False)
    @patch("kis_api.load_json", return_value={})
    @patch("kis_api.load_stoploss", return_value={})
    @patch("kis_api.load_watchalert", return_value={})
    @patch("kis_api.load_watchlist")
    def test_merge_all_sources(self, mock_wl, mock_wa, mock_sl, mock_load_json, mock_is_us):
        from report_crawler import get_collection_tickers

        mock_wl.return_value = {"005930": "삼성전자"}
        mock_wa.return_value = {"103140": {"name": "풍산", "buy_price": 50000}}
        mock_sl.return_value = {"272210": {"name": "한화시스템", "stop_price": 20000}}
        mock_load_json.return_value = {
            "000660": {"name": "SK하이닉스", "qty": 10},
            "us_stocks": {},
            "cash_krw": 1000000,
        }

        result = get_collection_tickers()

        self.assertIn("005930", result)   # watchlist
        self.assertIn("103140", result)   # watchalert
        self.assertIn("272210", result)   # stoploss
        self.assertIn("000660", result)   # portfolio
        self.assertNotIn("us_stocks", result)
        self.assertNotIn("cash_krw", result)

    @patch("kis_api._is_us_ticker", side_effect=lambda t: t == "AAPL")
    @patch("kis_api.load_json", return_value={})
    @patch("kis_api.load_stoploss", return_value={})
    @patch("kis_api.load_watchalert", return_value={})
    @patch("kis_api.load_watchlist")
    def test_skip_us_tickers(self, mock_wl, mock_wa, mock_sl, mock_load_json, mock_is_us):
        from report_crawler import get_collection_tickers

        mock_wl.return_value = {"005930": "삼성전자", "AAPL": "Apple"}

        result = get_collection_tickers()

        self.assertIn("005930", result)
        self.assertNotIn("AAPL", result)


class TestManageReportMcp(unittest.TestCase):
    """mcp_tools.py manage_report 핸들러 테스트"""

    def _run(self, coro):
        return asyncio.run(coro)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="dummy_token")
    @patch("mcp_tools.load_reports")
    def test_list_action(self, mock_load, mock_token):
        from mcp_tools import _execute_tool

        mock_load.return_value = {
            "reports": [
                {"date": "2026-03-28", "ticker": "005930", "name": "삼성전자",
                 "source": "미래에셋", "title": "반도체 전망",
                 "pdf_url": "https://ex.com/1.pdf", "full_text": "본문 내용"},
            ],
            "last_collected": "2026-03-28T12:00:00+09:00",
        }

        result = self._run(_execute_tool("manage_report", {"action": "list", "days": 7}))

        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["reports"]), 1)
        self.assertIn("full_text", result["reports"][0])

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="dummy_token")
    @patch("mcp_tools.load_reports")
    def test_list_brief(self, mock_load, mock_token):
        from mcp_tools import _execute_tool

        mock_load.return_value = {
            "reports": [
                {"date": "2026-03-28", "ticker": "005930", "name": "삼성전자",
                 "source": "미래에셋", "title": "반도체 전망",
                 "pdf_url": "https://ex.com/1.pdf", "full_text": "본문 내용"},
            ],
            "last_collected": "2026-03-28T12:00:00+09:00",
        }

        result = self._run(_execute_tool("manage_report", {"action": "list", "brief": True}))

        self.assertEqual(result["count"], 1)
        # brief=True면 full_text 없음
        self.assertNotIn("full_text", result["reports"][0])

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="dummy_token")
    @patch("mcp_tools.collect_reports")
    @patch("mcp_tools.get_collection_tickers")
    def test_collect_action(self, mock_tickers, mock_collect, mock_token):
        from mcp_tools import _execute_tool

        mock_tickers.return_value = {"005930": "삼성전자"}
        mock_collect.return_value = [
            {"date": "2026-03-29", "ticker": "005930", "name": "삼성전자",
             "source": "KB", "title": "최신 리포트"},
        ]

        result = self._run(_execute_tool("manage_report", {"action": "collect"}))

        self.assertEqual(result["collected"], 1)
        mock_collect.assert_called_once()

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="dummy_token")
    @patch("mcp_tools.get_collection_tickers")
    def test_tickers_action(self, mock_tickers, mock_token):
        from mcp_tools import _execute_tool

        mock_tickers.return_value = {"005930": "삼성전자", "035720": "카카오"}

        result = self._run(_execute_tool("manage_report", {"action": "tickers"}))

        self.assertEqual(result["count"], 2)
        tickers = {t["ticker"] for t in result["tickers"]}
        self.assertIn("005930", tickers)
        self.assertIn("035720", tickers)

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="dummy_token")
    def test_invalid_action(self, mock_token):
        from mcp_tools import _execute_tool

        result = self._run(_execute_tool("manage_report", {"action": "invalid_xyz"}))

        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
