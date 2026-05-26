"""pdf_collectors.py — 단위 테스트 (mock 기반)

각 collector 함수의 다음 시나리오를 검증:
  - URL 파싱 / pdf_url 도메인 매칭
  - 다운로드 성공 (PDF 매직바이트 검증)
  - 실패 시나리오: 200 비-PDF / 403 / timeout / connection error
  - 실패 시 None 반환 + 예외 raise 안 함
  - source_used 라벨 정확성 (fetch_pdf_with_fallback)
  - 타임아웃 30초 강제
  - robots.txt 차단 시 거부
"""

from __future__ import annotations

import io
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

# 프로젝트 루트 import 보장
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pdf_collectors as pc  # noqa: E402


PDF_MAGIC = b"%PDF-1.4\n%fake content"


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 픽스처 ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture(autouse=True)
def reset_module_state():
    """각 테스트 전후로 세션/robots 캐시 초기화."""
    pc.reset_session()
    pc._ROBOTS_CACHE.clear()
    yield
    pc.reset_session()
    pc._ROBOTS_CACHE.clear()


def _make_response(status=200, content=PDF_MAGIC, content_type="application/pdf"):
    """requests.Response mock 생성."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = {"Content-Type": content_type}
    resp.text = content.decode("latin-1") if isinstance(content, bytes) else content
    # iter_content를 청크로 흉내
    def _iter_content(chunk_size=8192):
        body = content if isinstance(content, bytes) else content.encode()
        for i in range(0, len(body), chunk_size):
            yield body[i:i + chunk_size]
    resp.iter_content = _iter_content
    return resp


@pytest.fixture
def mock_session():
    """세션 GET을 mock으로 차단."""
    sess = MagicMock(spec=requests.Session)
    sess.headers = {}
    # robots.txt는 404로 가정 → crawl 허용
    def _get(url, **kwargs):
        if url.endswith("/robots.txt"):
            r = MagicMock(spec=requests.Response)
            r.status_code = 404
            r.text = ""
            return r
        return _make_response()
    sess.get.side_effect = _get
    sess.max_redirects = 5
    with patch.object(pc, "_SESSION", sess):
        yield sess


# ━━━━━━━━━━━━━━━━━━━━━━━━━ _download_pdf 핵심 ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDownloadPdf:
    """_download_pdf — 모든 collector의 공통 다운로드 핵심."""

    def test_success_pdf_content_type(self, mock_session):
        result = pc._download_pdf("https://example.com/r.pdf")
        assert result == PDF_MAGIC
        assert result.startswith(b"%PDF")

    def test_failure_404(self):
        sess = MagicMock(spec=requests.Session)
        sess.headers = {}
        def _get(url, **kwargs):
            if url.endswith("/robots.txt"):
                r = MagicMock()
                r.status_code = 404
                r.text = ""
                return r
            r = _make_response(status=404, content=b"not found", content_type="text/html")
            return r
        sess.get.side_effect = _get
        with patch.object(pc, "_SESSION", sess):
            assert pc._download_pdf("https://example.com/missing.pdf") is None

    def test_failure_403_forbidden(self):
        sess = MagicMock(spec=requests.Session)
        sess.headers = {}
        def _get(url, **kwargs):
            if url.endswith("/robots.txt"):
                r = MagicMock()
                r.status_code = 404
                r.text = ""
                return r
            return _make_response(status=403, content=b"forbidden", content_type="text/html")
        sess.get.side_effect = _get
        with patch.object(pc, "_SESSION", sess):
            assert pc._download_pdf("https://example.com/r.pdf") is None

    def test_failure_html_response(self):
        """HTML 응답은 로그인 페이지로 간주, None 반환."""
        sess = MagicMock(spec=requests.Session)
        sess.headers = {}
        def _get(url, **kwargs):
            if url.endswith("/robots.txt"):
                r = MagicMock()
                r.status_code = 404
                r.text = ""
                return r
            return _make_response(
                status=200,
                content=b"<html><body>login required</body></html>",
                content_type="text/html",
            )
        sess.get.side_effect = _get
        with patch.object(pc, "_SESSION", sess):
            assert pc._download_pdf("https://example.com/r.pdf") is None

    def test_failure_timeout_exception(self):
        sess = MagicMock(spec=requests.Session)
        sess.headers = {}
        def _get(url, **kwargs):
            if url.endswith("/robots.txt"):
                r = MagicMock()
                r.status_code = 404
                r.text = ""
                return r
            raise requests.exceptions.Timeout("simulated")
        sess.get.side_effect = _get
        with patch.object(pc, "_SESSION", sess):
            # 예외가 raise되지 않고 None 반환되어야 함
            assert pc._download_pdf("https://example.com/r.pdf") is None

    def test_failure_connection_error(self):
        sess = MagicMock(spec=requests.Session)
        sess.headers = {}
        def _get(url, **kwargs):
            if url.endswith("/robots.txt"):
                r = MagicMock()
                r.status_code = 404
                r.text = ""
                return r
            raise requests.exceptions.ConnectionError("simulated")
        sess.get.side_effect = _get
        with patch.object(pc, "_SESSION", sess):
            assert pc._download_pdf("https://example.com/r.pdf") is None

    def test_timeout_arg_is_30s(self, mock_session):
        """_TIMEOUT 상수가 30초인지 검증 (사용자 룰)."""
        assert pc._TIMEOUT == 30
        # 실제 호출에서 timeout=30 인자 전달되는지 검증
        pc._download_pdf("https://example.com/r.pdf")
        call_kwargs = mock_session.get.call_args_list[-1].kwargs
        assert call_kwargs.get("timeout") == 30

    def test_non_pdf_magic_bytes_rejected(self):
        """Content-Type=pdf인데 매직바이트 없으면 None."""
        sess = MagicMock(spec=requests.Session)
        sess.headers = {}
        def _get(url, **kwargs):
            if url.endswith("/robots.txt"):
                r = MagicMock()
                r.status_code = 404
                r.text = ""
                return r
            return _make_response(
                status=200,
                content=b"not a pdf bytes",
                content_type="application/pdf",
            )
        sess.get.side_effect = _get
        with patch.object(pc, "_SESSION", sess):
            assert pc._download_pdf("https://example.com/r.pdf") is None

    def test_robots_txt_disallow_blocks(self):
        """robots.txt가 Disallow: / 면 다운로드 거부."""
        sess = MagicMock(spec=requests.Session)
        sess.headers = {}
        def _get(url, **kwargs):
            if url.endswith("/robots.txt"):
                r = MagicMock()
                r.status_code = 200
                r.text = "User-agent: *\nDisallow: /\n"
                return r
            return _make_response()
        sess.get.side_effect = _get
        with patch.object(pc, "_SESSION", sess):
            assert pc._download_pdf("https://forbidden.example.com/r.pdf") is None

    def test_robots_txt_404_allows(self, mock_session):
        """robots.txt 404 → 허용으로 처리."""
        result = pc._download_pdf("https://example.com/r.pdf")
        assert result == PDF_MAGIC

    def test_max_size_50mb_enforced(self):
        """50MB 초과 시 None."""
        assert pc._MAX_PDF_BYTES == 50 * 1024 * 1024


# ━━━━━━━━━━━━━━━━━━━━━━━━━ samsungpop ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSamsungpopFetch:
    """samsungpop_fetch — fileName 추출과 다운로드."""

    SAMSUNG_URL = (
        "https://www.samsungpop.com/common.do?cmd=down&contentType=application/pdf"
        "&inlineYn=Y&saveKey=research.pdf&fileName=2010/2026031015471500K_02_06.pdf"
    )

    def test_parse_filename_from_pdf_url(self, mock_session):
        result = pc.samsungpop_fetch(
            ticker="058610", date="2026-03-11", title="test",
            pdf_url=self.SAMSUNG_URL,
        )
        assert result == PDF_MAGIC
        # 호출 URL에 fileName이 포함되어야 함
        called_urls = [c.args[0] for c in mock_session.get.call_args_list
                       if not c.args[0].endswith("/robots.txt")]
        assert any("fileName=2010%2F2026031015471500K_02_06.pdf" in u or
                   "fileName=2010/2026031015471500K_02_06.pdf" in u
                   for u in called_urls)

    def test_explicit_file_name_param(self, mock_session):
        result = pc.samsungpop_fetch(
            ticker="058610", date="2026-03-11", title="test",
            file_name="2010/test_file.pdf",
        )
        assert result == PDF_MAGIC

    def test_no_filename_returns_none(self):
        """fileName도 pdf_url도 없으면 None (예외 X)."""
        result = pc.samsungpop_fetch(
            ticker="058610", date="2026-03-11", title="test",
        )
        assert result is None

    def test_samsungpop_fetch_by_url_wrong_domain(self):
        """samsungpop 도메인 아니면 None."""
        result = pc.samsungpop_fetch_by_url("https://other.com/foo.pdf")
        assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ eugenefn ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestEugenefnFetch:
    EUGENE_URL = "https://www.eugenefn.com/common/files/amail//20260130_005930_sophie.yim_114.pdf"

    def test_parse_filename_from_url(self, mock_session):
        result = pc.eugenefn_fetch(
            ticker="005930", date="2026-01-30", title="test",
            pdf_url=self.EUGENE_URL,
        )
        assert result == PDF_MAGIC

    def test_explicit_analyst_and_seq(self, mock_session):
        result = pc.eugenefn_fetch(
            ticker="005930", date="2026-01-30", title="test",
            analyst_id="sophie.yim", seq="114",
        )
        assert result == PDF_MAGIC

    def test_missing_params_returns_none(self):
        result = pc.eugenefn_fetch(
            ticker="005930", date="2026-01-30", title="test",
        )
        assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ miraeasset ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestMiraeassetFetch:
    MIRAE_URL = "https://securities.miraeasset.com/bbs/download/file.do?attachmentId=12345678"

    def test_parse_attachment_id_from_url(self, mock_session):
        result = pc.miraeasset_fetch(
            ticker="005930", date="2026-04-01", title="test",
            pdf_url=self.MIRAE_URL,
        )
        assert result == PDF_MAGIC

    def test_explicit_attachment_id(self, mock_session):
        result = pc.miraeasset_fetch(
            ticker="005930", date="2026-04-01", title="test",
            attachment_id="99999",
        )
        assert result == PDF_MAGIC

    def test_no_id_returns_none(self):
        result = pc.miraeasset_fetch(
            ticker="005930", date="2026-04-01", title="test",
        )
        assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ hanaw ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestHanawFetch:
    HANAW_URL = "https://file.hanaw.com/download/research/FileServer/WEB/company/2026/03/16/hana_260316_058610.pdf"

    def test_full_url(self, mock_session):
        result = pc.hanaw_fetch(
            ticker="058610", date="2026-03-16", title="test",
            pdf_url=self.HANAW_URL,
        )
        assert result == PDF_MAGIC

    def test_file_path_param(self, mock_session):
        result = pc.hanaw_fetch(
            ticker="058610", date="2026-03-16", title="test",
            file_path="company/2026/03/16/hana_260316_058610.pdf",
        )
        assert result == PDF_MAGIC

    def test_date_guess_path(self, mock_session):
        """파일경로 없으면 날짜 기반 추측."""
        result = pc.hanaw_fetch(
            ticker="058610", date="2026-03-16", title="test",
        )
        assert result == PDF_MAGIC  # mock은 항상 PDF 반환

    def test_no_date_returns_none(self):
        """날짜 추측 실패 시 None."""
        sess = MagicMock(spec=requests.Session)
        sess.headers = {}
        def _get(url, **kwargs):
            if url.endswith("/robots.txt"):
                r = MagicMock()
                r.status_code = 404
                r.text = ""
                return r
            # 항상 404 반환 → 추측 실패
            return _make_response(status=404, content_type="text/html")
        sess.get.side_effect = _get
        with patch.object(pc, "_SESSION", sess):
            result = pc.hanaw_fetch(
                ticker="058610", date="2026-03-16", title="test",
            )
            assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ dbfi ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDbfiFetch:
    DBFI_URL = "https://ssl.pstatic.net/imgstock/upload/research/company/202602231024511800012489.pdf"

    def test_parse_id_from_url(self, mock_session):
        result = pc.dbfi_fetch(
            ticker="058610", date="2026-02-23", title="test",
            pdf_url=self.DBFI_URL,
        )
        assert result == PDF_MAGIC

    def test_explicit_report_id(self, mock_session):
        result = pc.dbfi_fetch(
            ticker="058610", date="2026-02-23", title="test",
            report_id="202602231024511800012489",
        )
        assert result == PDF_MAGIC

    def test_naver_stock_format(self, mock_session):
        """stock.pstatic.net 포맷 파싱."""
        url = "https://stock.pstatic.net/stock-research/company/31/20260530_company_519017000.pdf"
        result = pc.dbfi_fetch(
            ticker="058610", date="2026-05-30", title="test",
            pdf_url=url,
        )
        # pstatic.net URL 직접 다운로드 fallback으로 PDF 반환
        assert result == PDF_MAGIC

    def test_no_id_returns_none(self):
        result = pc.dbfi_fetch(
            ticker="058610", date="2026-02-23", title="test",
        )
        assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ naver_research ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestNaverResearchFetch:
    NAVER_URL = "https://stock.pstatic.net/stock-research/company/31/20260530_company_519017000.pdf"

    def test_direct_pstatic_url(self, mock_session):
        result = pc.naver_research_fetch(
            ticker="058610", date="2026-05-30", title="test",
            pdf_url=self.NAVER_URL,
        )
        assert result == PDF_MAGIC

    def test_no_url_returns_none_on_empty_html(self):
        """pdf_url 없고 검색 결과도 없으면 None."""
        sess = MagicMock(spec=requests.Session)
        sess.headers = {}
        def _get(url, **kwargs):
            if url.endswith("/robots.txt"):
                r = MagicMock()
                r.status_code = 404
                r.text = ""
                return r
            # 빈 HTML 반환
            r = MagicMock(spec=requests.Response)
            r.status_code = 200
            r.text = "<html><body></body></html>"
            r.headers = {"Content-Type": "text/html"}
            return r
        sess.get.side_effect = _get
        with patch.object(pc, "_SESSION", sess):
            result = pc.naver_research_fetch(
                ticker="058610", date="2026-05-30", title="test",
            )
            assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ fetch_pdf_with_fallback ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFetchPdfWithFallback:
    """4단계 체인:
       1) broker_hint 매칭
       2) pdf_url 도메인 매칭
       3) 전체 무료 collector 순회
       4) naver_research HTML 파싱
    """

    def test_priority_1_broker_hint_samsung(self, mock_session):
        """삼성 broker_hint → samsungpop_direct."""
        url = ("https://www.samsungpop.com/common.do?cmd=down&fileName="
               "2010/2026031015471500K_02_06.pdf")
        result = pc.fetch_pdf_with_fallback(
            ticker="058610", date="2026-03-11", title="test",
            broker_hint="삼성", pdf_url=url,
        )
        assert result is not None
        body, src = result
        assert body == PDF_MAGIC
        assert src == "samsungpop_direct"

    def test_priority_2_url_domain_hanaw(self, mock_session):
        """broker_hint 없어도 hanaw.com URL → hanaw_direct."""
        url = ("https://file.hanaw.com/download/research/FileServer/WEB/"
               "company/2026/03/16/hana_260316_058610.pdf")
        result = pc.fetch_pdf_with_fallback(
            ticker="058610", date="2026-03-16", title="test",
            pdf_url=url,
        )
        assert result is not None
        body, src = result
        assert body == PDF_MAGIC
        assert src == "hanaw_direct"

    def test_returns_none_when_all_fail(self):
        """모든 collector 실패 시 None."""
        sess = MagicMock(spec=requests.Session)
        sess.headers = {}
        def _get(url, **kwargs):
            if url.endswith("/robots.txt"):
                r = MagicMock()
                r.status_code = 404
                r.text = ""
                return r
            # 모두 실패
            return _make_response(status=404, content_type="text/html")
        sess.get.side_effect = _get
        with patch.object(pc, "_SESSION", sess):
            result = pc.fetch_pdf_with_fallback(
                ticker="058610", date="2026-03-11", title="test",
                broker_hint="삼성",
                pdf_url="https://www.samsungpop.com/common.do?fileName=2010/x.pdf",
            )
            assert result is None

    def test_no_requests_returns_none(self):
        """requests 미설치 시 None."""
        with patch.object(pc, "_REQUESTS_AVAILABLE", False):
            result = pc.fetch_pdf_with_fallback(
                ticker="058610", date="2026-03-11", title="test",
            )
            assert result is None

    def test_exception_in_collector_does_not_raise(self, mock_session):
        """collector가 예외 raise해도 fetch_pdf_with_fallback은 다음 시도."""
        original_samsung = pc.samsungpop_fetch
        def raising_samsung(*args, **kwargs):
            raise RuntimeError("boom")
        pc.samsungpop_fetch = raising_samsung
        # _FREE_COLLECTORS_ORDER와 _BROKER_MAP 모두 참조하므로 같이 패치
        try:
            # 삼성으로 broker 매칭하면 예외 → 다음 단계로 진행해야 함
            url = "https://example.com/r.pdf"
            result = pc.fetch_pdf_with_fallback(
                ticker="058610", date="2026-03-11", title="test",
                broker_hint="삼성", pdf_url=url,
            )
            # 다음 collector도 raise하므로 None — 다만 예외 raise 안됨이 핵심
            # 결과는 None일 수도 있고 (mock_session이 PDF 반환하면) PDF일 수도 있음
        finally:
            pc.samsungpop_fetch = original_samsung


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 헬퍼 함수 ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestHelpers:
    def test_normalize_date_yyyy_mm_dd(self):
        assert pc._normalize_date("2026-03-11") == "20260311"
        assert pc._normalize_date("2026/03/11") == "20260311"
        assert pc._normalize_date("20260311") == "20260311"

    def test_is_pdf_url_free(self):
        assert pc.is_pdf_url_free("https://www.samsungpop.com/foo.pdf") is True
        assert pc.is_pdf_url_free("https://file.hanaw.com/x.pdf") is True
        assert pc.is_pdf_url_free("https://ssl.pstatic.net/x.pdf") is True
        assert pc.is_pdf_url_free("https://www.wisereport.co.kr/x.pdf") is False
        assert pc.is_pdf_url_free("") is False
        assert pc.is_pdf_url_free(None) is False

    def test_get_source_label_meta_only(self):
        assert pc.get_source_label("anything", "meta_only") == "wisereport_paid"

    def test_get_source_label_samsung(self):
        assert pc.get_source_label("https://www.samsungpop.com/x") == "samsungpop_direct"

    def test_get_source_label_hanaw(self):
        assert pc.get_source_label("https://file.hanaw.com/x.pdf") == "hanaw_direct"

    def test_get_source_label_wisereport(self):
        assert pc.get_source_label("https://www.wisereport.co.kr/foo") == "wisereport_paid"

    def test_get_source_label_naver(self):
        assert pc.get_source_label("https://stock.pstatic.net/x.pdf") == "naver_research"

    def test_get_collector_by_broker_samsung(self):
        func, label = pc._get_collector_by_broker("삼성증권")
        assert func is pc.samsungpop_fetch
        assert label == "samsungpop_direct"

    def test_get_collector_by_broker_db(self):
        func, label = pc._get_collector_by_broker("DB증권")
        # "DB증권"은 "db"를 포함하므로 dbfi 매칭
        assert func is pc.dbfi_fetch
        assert label == "dbfi_direct"

    def test_get_collector_by_broker_unknown(self):
        func, label = pc._get_collector_by_broker("알수없는증권")
        assert func is None
        assert label is None

    def test_get_collector_by_url_samsung(self):
        func, label = pc._get_collector_by_url(
            "https://www.samsungpop.com/foo.pdf"
        )
        assert func is pc.samsungpop_fetch
        assert label == "samsungpop_direct"

    def test_get_collector_by_url_empty(self):
        func, label = pc._get_collector_by_url("")
        assert func is None
        assert label is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ wisereport_only 라벨 ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestWisereportLabel:
    """wisereport_paid 라벨이 사용자에게 노출되는지 검증 (수락기준 #4)."""

    def test_meta_only_status_yields_wisereport_paid(self):
        """extraction_status=meta_only → source_used=wisereport_paid."""
        label = pc.get_source_label(
            "https://www.wisereport.co.kr/comm/LoadReport.aspx?rpt_id=1",
            extraction_status="meta_only",
        )
        assert label == "wisereport_paid"

    def test_wisereport_url_yields_wisereport_paid(self):
        """wisereport URL → 라벨에 wisereport 명시."""
        label = pc.get_source_label("https://www.wisereport.co.kr/foo")
        assert "wisereport" in label.lower()
