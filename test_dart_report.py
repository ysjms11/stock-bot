"""
DART 사업보고서 저장 기능 테스트 (pytest)
모든 외부 API 호출은 mock 처리.
"""
import asyncio
import json
import os
import shutil
import sys
import types
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# telegram 스텁 (mcp_tools.py → main.py import 체인용)
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

# 테스트용 /data 대신 임시 디렉토리 사용
TEST_DATA_DIR = "/tmp/test_dart_report_data"
TEST_REPORTS_DIR = os.path.join(TEST_DATA_DIR, "dart_reports")

# ── kis_api 상수를 테스트용으로 패치 (import 전에) ──
import kis_api
kis_api.DART_REPORTS_DIR = TEST_REPORTS_DIR
kis_api.CORP_CODES_FILE = os.path.join(TEST_DATA_DIR, "corp_codes.json")


from kis_api import (
    load_corp_codes, search_dart_reports, fetch_dart_document,
    save_dart_report, list_dart_reports, read_dart_report,
    _report_file_exists, DART_API_KEY,
)


@pytest.fixture(autouse=True)
def clean_test_dir():
    """테스트 전후로 임시 디렉토리 정리."""
    os.makedirs(TEST_DATA_DIR, exist_ok=True)
    yield
    if os.path.exists(TEST_DATA_DIR):
        shutil.rmtree(TEST_DATA_DIR)


# ── 공통 mock 데이터 ──
FAKE_CORP_CODES = {
    "005930": {"corp_code": "00126380", "corp_name": "삼성전자"},
    "000660": {"corp_code": "00164779", "corp_name": "SK하이닉스"},
}

FAKE_REPORT_LIST = [
    {"rcept_no": "20260310000123", "rcept_dt": "20260310",
     "corp_name": "삼성전자", "report_nm": "사업보고서"},
]

FAKE_DOCUMENT_HTML = """
<html><body>
<h1>사업보고서</h1>
<p>I. 회사의 개요</p>
<p>삼성전자 주식회사는 반도체, IT, 모바일 사업을 영위합니다.</p>
<table><tr><td>매출액</td><td>302조원</td></tr></table>
</body></html>
"""


class TestSaveDartReport:
    """save_dart_report: txt 파일 생성 및 중복 방지"""

    @pytest.mark.asyncio
    async def test_creates_txt_file(self):
        """mode='report', ticker='005930' → txt 파일 생성 확인"""
        with patch("kis_api.fetch_dart_document", new_callable=AsyncMock,
                    return_value="삼성전자 사업보고서 본문 내용입니다. " * 10):
            result = await save_dart_report("005930", "삼성전자", "20260310000123", "20260310")

        assert result is not None
        assert result["ticker"] == "005930"
        assert result["name"] == "삼성전자"
        assert result["file_size_kb"] > 0
        assert result["skipped"] is False
        assert os.path.exists(result["file_path"])

    @pytest.mark.asyncio
    async def test_meta_header_format(self):
        """txt 파일 메타 헤더 포맷 검증"""
        with patch("kis_api.fetch_dart_document", new_callable=AsyncMock,
                    return_value="본문 내용 " * 20):
            result = await save_dart_report("005930", "삼성전자", "20260310000123", "20260310")

        with open(result["file_path"], encoding="utf-8") as f:
            content = f.read()

        assert "===== DART 사업보고서 =====" in content
        assert "종목: 삼성전자 (005930)" in content
        assert "보고서일: 20260310" in content
        assert "접수번호: 20260310000123" in content
        assert "저장일시:" in content
        assert "본문 내용 " in content

    @pytest.mark.asyncio
    async def test_duplicate_prevention(self):
        """중복 저장 방지: 같은 접수번호 → skipped=True"""
        with patch("kis_api.fetch_dart_document", new_callable=AsyncMock,
                    return_value="사업보고서 본문 텍스트 내용 " * 10):
            result1 = await save_dart_report("005930", "삼성전자", "20260310000123", "20260310")
            result2 = await save_dart_report("005930", "삼성전자", "20260310000123", "20260310")

        assert result1["skipped"] is False
        assert result2["skipped"] is True

    @pytest.mark.asyncio
    async def test_empty_document_returns_none(self):
        """본문이 빈 경우 None 반환"""
        with patch("kis_api.fetch_dart_document", new_callable=AsyncMock,
                    return_value=""):
            result = await save_dart_report("005930", "삼성전자", "20260310999999", "20260310")

        assert result is None


class TestListDartReports:
    """list_dart_reports: 저장된 파일 목록 반환"""

    @pytest.mark.asyncio
    async def test_report_list_returns_saved_files(self):
        """mode='report_list' → 저장된 파일 목록 반환"""
        with patch("kis_api.fetch_dart_document", new_callable=AsyncMock,
                    return_value="본문 내용 " * 20):
            await save_dart_report("005930", "삼성전자", "20260310000123", "20260310")

        result = list_dart_reports()
        assert result["total"] == 1
        assert result["files"][0]["ticker"] == "005930"
        assert result["files"][0]["file_size_kb"] > 0

    def test_report_list_empty(self):
        """파일 없으면 빈 목록"""
        result = list_dart_reports()
        assert result["total"] == 0
        assert result["files"] == []


class TestCorpCodes:
    """corp_code 캐시 생성/갱신 동작"""

    @pytest.mark.asyncio
    async def test_cache_creation(self):
        """corp_codes 캐시 파일 생성 확인"""
        with patch("kis_api._download_corp_codes", new_callable=AsyncMock,
                    return_value=FAKE_CORP_CODES):
            codes = await load_corp_codes()

        assert "005930" in codes
        assert codes["005930"]["corp_code"] == "00126380"

    @pytest.mark.asyncio
    async def test_cache_reuse(self):
        """캐시 파일 존재 시 다운로드 안 함"""
        # 캐시 파일 직접 생성
        os.makedirs(TEST_DATA_DIR, exist_ok=True)
        cache_path = kis_api.CORP_CODES_FILE
        with open(cache_path, "w") as f:
            json.dump(FAKE_CORP_CODES, f)

        with patch("kis_api._download_corp_codes", new_callable=AsyncMock) as mock_dl:
            codes = await load_corp_codes()
            mock_dl.assert_not_called()

        assert "005930" in codes


class TestUSTickerSkip:
    """미국 종목 스킵 확인"""

    def test_us_ticker_detected(self):
        from kis_api import _is_us_ticker
        assert _is_us_ticker("AAPL") is True
        assert _is_us_ticker("TSLA") is True
        assert _is_us_ticker("005930") is False
        assert _is_us_ticker("000660") is False


class TestReadDartReport:
    """read_dart_report: 저장된 사업보고서 txt 내용 반환"""

    @pytest.mark.asyncio
    async def test_read_returns_content(self):
        """mode='read', ticker='005930' → 저장된 txt 내용 반환"""
        with patch("kis_api.fetch_dart_document", new_callable=AsyncMock,
                    return_value="삼성전자 사업보고서 본문 " * 50):
            await save_dart_report("005930", "삼성전자", "20260310000123", "20260310")

        result = read_dart_report("005930")
        assert result["ticker"] == "005930"
        assert result["name"] == "삼성전자"
        assert result["report_date"] == "2026-03-10"
        assert "삼성전자 사업보고서 본문" in result["content"]
        assert result["truncated"] is False
        assert result["file_size_kb"] > 0

    @pytest.mark.asyncio
    async def test_read_latest_when_multiple(self):
        """여러 파일이 있으면 가장 최신 날짜 반환"""
        with patch("kis_api.fetch_dart_document", new_callable=AsyncMock,
                    return_value="구 보고서 내용 " * 10):
            await save_dart_report("005930", "삼성전자", "20250310000100", "20250310")
        with patch("kis_api.fetch_dart_document", new_callable=AsyncMock,
                    return_value="신 보고서 내용 " * 10):
            await save_dart_report("005930", "삼성전자", "20260310000200", "20260310")

        result = read_dart_report("005930")
        assert result["report_date"] == "2026-03-10"
        assert "신 보고서 내용" in result["content"]

    def test_read_no_file_returns_error(self):
        """파일 없으면 에러 메시지 반환"""
        result = read_dart_report("999999")
        assert "error" in result
        assert "사업보고서 없음" in result["error"]
        assert "mode='report'" in result["error"]

    @pytest.mark.asyncio
    async def test_read_truncation(self):
        """50,000자 초과 시 truncated=True"""
        long_text = "가" * 60_000
        with patch("kis_api.fetch_dart_document", new_callable=AsyncMock,
                    return_value=long_text):
            await save_dart_report("005930", "삼성전자", "20260310000123", "20260310")

        result = read_dart_report("005930", max_chars=50_000)
        assert result["truncated"] is True
        # 메타 헤더가 포함되므로 content 길이는 max_chars
        assert len(result["content"]) == 50_000

    @pytest.mark.asyncio
    async def test_read_via_mcp_execute(self):
        """MCP _execute_tool로 mode='read' 호출"""
        from mcp_tools import _execute_tool

        with patch("kis_api.fetch_dart_document", new_callable=AsyncMock,
                    return_value="보고서 본문 " * 50):
            await save_dart_report("092780", "동양생명", "20260401000999", "20260401")

        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock,
                    return_value="fake_token"):
            result = await _execute_tool("get_dart", {"mode": "read", "ticker": "092780"})

        assert result["ticker"] == "092780"
        assert "content" in result
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_read_via_mcp_no_ticker_error(self):
        """MCP mode='read' ticker 미지정 → 에러"""
        from mcp_tools import _execute_tool

        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock,
                    return_value="fake_token"):
            result = await _execute_tool("get_dart", {"mode": "read"})

        assert "error" in result
        assert "ticker" in result["error"]

    @pytest.mark.asyncio
    async def test_read_via_mcp_ticker_none_error(self):
        """MCP mode='read' ticker=None → 에러 (AttributeError 방지)"""
        from mcp_tools import _execute_tool

        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock,
                    return_value="fake_token"):
            result = await _execute_tool("get_dart", {"mode": "read", "ticker": None})

        assert "error" in result
        assert "ticker" in result["error"]


class TestBackwardCompatibility:
    """하위호환: mode 없이 호출 시 기존 동작"""

    @pytest.mark.asyncio
    async def test_default_mode_returns_disclosures(self):
        """mode 없이 호출 → 기존 공시 목록 (search_dart_disclosures 호출)"""
        from mcp_tools import _execute_tool

        fake_disclosures = [
            {"corp_name": "삼성전자", "report_nm": "주요사항보고서",
             "rcept_dt": "20260401", "pblntf_ty": "B"},
        ]
        with patch("mcp_tools.search_dart_disclosures", new_callable=AsyncMock,
                    return_value=fake_disclosures), \
             patch("mcp_tools.load_watchlist",
                    return_value={"005930": "삼성전자"}), \
             patch("mcp_tools.get_kis_token", new_callable=AsyncMock,
                    return_value="fake_token"):
            result = await _execute_tool("get_dart", {})

        assert isinstance(result, list)
        assert result[0]["corp"] == "삼성전자"
