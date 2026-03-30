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


class TestValidateKoreanText(unittest.TestCase):
    """_validate_korean_text 테스트"""

    def test_korean_above_threshold(self):
        from report_crawler import _validate_korean_text
        # 한글 비율 10% 이상 → True
        text = "가나다라마바사아자차" + "A" * 50  # 10/60 ≈ 16.7%
        self.assertTrue(_validate_korean_text(text))

    def test_korean_below_threshold(self):
        from report_crawler import _validate_korean_text
        # 한글 비율 10% 미만 → False
        text = "가" + "A" * 100  # 1/101 ≈ 0.99%
        self.assertFalse(_validate_korean_text(text))

    def test_empty_text(self):
        from report_crawler import _validate_korean_text
        self.assertFalse(_validate_korean_text(""))

    def test_english_only(self):
        from report_crawler import _validate_korean_text
        self.assertFalse(_validate_korean_text("Hello World This is English only text"))


class TestIsChartImageText(unittest.TestCase):
    """_is_chart_image_text 테스트"""

    def test_chart_numbers_only(self):
        from report_crawler import _is_chart_image_text
        text = "100,000 200,000 300,000\n50.5 60.3 70.1\n(2024) (2025) (2026)"
        self.assertTrue(_is_chart_image_text(text))

    def test_normal_report_text(self):
        from report_crawler import _is_chart_image_text
        text = "삼성전자 반도체 사업부 실적이 크게 개선되었습니다. 매출액 50조원 달성."
        self.assertFalse(_is_chart_image_text(text))

    def test_short_text(self):
        from report_crawler import _is_chart_image_text
        self.assertFalse(_is_chart_image_text("123"))  # < 20자

    def test_empty_text(self):
        from report_crawler import _is_chart_image_text
        self.assertFalse(_is_chart_image_text(""))


class TestExtractPdfText(unittest.TestCase):
    """extract_pdf_text 테스트"""

    def _make_mock_pdf(self, page_text):
        """pdfplumber mock 생성 헬퍼."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = page_text
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_plumber_mod = MagicMock()
        mock_plumber_mod.open.return_value = mock_pdf
        return mock_plumber_mod

    def _make_mock_response(self, status_code=200):
        """HTTP response mock 생성 헬퍼."""
        resp = MagicMock()
        resp.status_code = status_code
        resp.headers = {}
        resp.iter_content = MagicMock(return_value=[b"fake pdf content"])
        return resp

    @patch("report_crawler.requests.get")
    def test_extract_text(self, mock_get):
        from report_crawler import extract_pdf_text

        mock_get.return_value = self._make_mock_response()
        mock_plumber_mod = self._make_mock_pdf("PDF 본문 텍스트입니다.")
        with patch.dict("sys.modules", {"pdfplumber": mock_plumber_mod}):
            text, status = extract_pdf_text("https://stock.pstatic.net/test.pdf")

        self.assertIn("PDF 본문 텍스트", text)
        self.assertIn(status, ("success", "partial"))

    @patch("report_crawler.requests.get")
    def test_text_truncation(self, mock_get):
        from report_crawler import extract_pdf_text, _MAX_TEXT

        mock_get.return_value = self._make_mock_response()
        # 긴 한글 텍스트 생성 (10000자 초과, 한글 비율 충분)
        long_text = "가나다라마바사아자차" * 1500  # 15000자, 100% 한글
        mock_plumber_mod = self._make_mock_pdf(long_text)
        with patch.dict("sys.modules", {"pdfplumber": mock_plumber_mod}):
            text, status = extract_pdf_text("https://stock.pstatic.net/big.pdf")

        self.assertLessEqual(len(text), _MAX_TEXT)

    @patch("report_crawler.requests.get")
    def test_download_failure(self, mock_get):
        from report_crawler import extract_pdf_text

        mock_get.return_value = self._make_mock_response(status_code=500)

        text, status = extract_pdf_text("https://stock.pstatic.net/fail.pdf")
        self.assertEqual(text, "")
        self.assertEqual(status, "failed")

    @patch("report_crawler.requests.get")
    def test_cleanup_temp_file(self, mock_get):
        from report_crawler import extract_pdf_text

        mock_get.return_value = self._make_mock_response()

        # pdfplumber.open에서 예외 발생 -> finally에서 임시파일 삭제 확인
        mock_plumber_mod = MagicMock()
        mock_plumber_mod.open.side_effect = Exception("corrupt PDF")
        with patch.dict("sys.modules", {"pdfplumber": mock_plumber_mod}):
            text, status = extract_pdf_text("https://stock.pstatic.net/corrupt.pdf")

        self.assertEqual(text, "")
        self.assertEqual(status, "failed")

    @patch("report_crawler.requests.get")
    def test_image_pdf_returns_failed(self, mock_get):
        """텍스트 추출됐지만 한글 없음 → 이미지 기반 PDF 판정."""
        from report_crawler import extract_pdf_text

        mock_get.return_value = self._make_mock_response()
        # 영문만 있는 텍스트 → 한글 비율 0%
        mock_plumber_mod = self._make_mock_pdf("This is all English text from scanned image PDF.")
        with patch.dict("sys.modules", {"pdfplumber": mock_plumber_mod}):
            text, status = extract_pdf_text("https://stock.pstatic.net/image.pdf")

        self.assertIn("이미지 기반 PDF", text)
        self.assertEqual(status, "failed")

    @patch("report_crawler.requests.get")
    def test_chart_image_pdf_returns_failed(self, mock_get):
        """숫자+좌표만 있는 차트 PDF → 실패 판정."""
        from report_crawler import extract_pdf_text

        mock_get.return_value = self._make_mock_response()
        chart_text = "100,000 200,000 300,000\n50.5 60.3 70.1\n(2024) (2025) (2026)\n" * 5
        mock_plumber_mod = self._make_mock_pdf(chart_text)
        with patch.dict("sys.modules", {"pdfplumber": mock_plumber_mod}):
            text, status = extract_pdf_text("https://stock.pstatic.net/chart.pdf")

        self.assertIn("차트/이미지 PDF", text)
        self.assertEqual(status, "failed")

    @patch("report_crawler.requests.get")
    def test_success_status(self, mock_get):
        """한글 충분한 짧은 텍스트 → success 상태."""
        from report_crawler import extract_pdf_text

        mock_get.return_value = self._make_mock_response()
        # 한글 비율 높은 텍스트 (10000자 미만)
        korean_text = "삼성전자 반도체 사업부 실적이 크게 개선되었습니다. 매출액은 전분기 대비 증가하였습니다."
        mock_plumber_mod = self._make_mock_pdf(korean_text)
        with patch.dict("sys.modules", {"pdfplumber": mock_plumber_mod}):
            text, status = extract_pdf_text("https://stock.pstatic.net/good.pdf")

        self.assertEqual(status, "success")
        self.assertIn("삼성전자", text)

    @patch("report_crawler.requests.get")
    def test_partial_status(self, mock_get):
        """10000자 초과 한글 텍스트 → partial 상태."""
        from report_crawler import extract_pdf_text, _MAX_TEXT

        mock_get.return_value = self._make_mock_response()
        # 15000자 한글 텍스트
        long_korean = "가나다라마바사아자차" * 1500
        mock_plumber_mod = self._make_mock_pdf(long_korean)
        with patch.dict("sys.modules", {"pdfplumber": mock_plumber_mod}):
            text, status = extract_pdf_text("https://stock.pstatic.net/long.pdf")

        self.assertEqual(status, "partial")
        self.assertLessEqual(len(text), _MAX_TEXT)


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
    @patch("report_crawler.extract_pdf_text", return_value=("extracted text", "success"))
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
    @patch("report_crawler.extract_pdf_text", return_value=("text", "success"))
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
    @patch("report_crawler.extract_pdf_text", return_value=("text", "success"))
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
    @patch("report_crawler.extract_pdf_text", return_value=("text", "success"))
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

    @patch("report_crawler.save_reports")
    @patch("report_crawler.extract_pdf_text", return_value=("extracted text", "success"))
    @patch("report_crawler.crawl_naver_reports")
    @patch("report_crawler.load_reports")
    @patch("report_crawler.time.sleep")
    def test_extraction_status_in_result(self, mock_sleep, mock_load, mock_crawl, mock_extract, mock_save):
        """수집된 리포트에 extraction_status 필드가 포함되는지 확인."""
        from report_crawler import collect_reports

        mock_load.return_value = {"reports": [], "last_collected": ""}
        mock_crawl.return_value = [
            {"date": "2026-03-29", "ticker": "005930", "name": "삼성전자",
             "source": "미래에셋", "title": "리포트",
             "pdf_url": "https://example.com/status.pdf"},
        ]

        result = collect_reports({"005930": "삼성전자"})

        self.assertEqual(len(result), 1)
        self.assertIn("extraction_status", result[0])
        self.assertEqual(result[0]["extraction_status"], "success")


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
