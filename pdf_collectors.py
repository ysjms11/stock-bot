"""증권사별 PDF 직접 다운로드 collectors — 무료 폴백 시스템

각 collector 인터페이스:
    fetch_pdf(ticker, date, title, **kwargs) -> bytes | None

통합 함수:
    fetch_pdf_with_fallback(ticker, date, title, broker_hint=None) -> (bytes, source_used) | None

source_used 라벨:
    "samsungpop_direct", "eugenefn_direct", "miraeasset_direct",
    "hanaw_direct", "dbfi_direct", "naver_research", "wisereport_paid"

wisereport URL 유틸리티:
    parse_wisereport_url(url) -> dict | None   — brk_cd, fpath, rpt_id 파싱
    WISEREPORT_BROKER_MAP                      — brk_cd → broker 식별자 매핑
"""

import re
import time
import logging
from typing import Optional
from urllib.parse import urlparse, urlencode, quote

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━ 상수 & 세션 ━━━━━━━━━━━━━━━━━━━━━━━━━

_TIMEOUT = 30  # 초
_MAX_PDF_BYTES = 50 * 1024 * 1024  # 50MB

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.9",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# 모듈 레벨 Session 재사용 (연결 풀)
_SESSION: Optional["requests.Session"] = None


def _get_session() -> "requests.Session":
    global _SESSION
    if _SESSION is None:
        if not _REQUESTS_AVAILABLE:
            raise RuntimeError("requests 패키지가 설치되어 있지 않습니다.")
        _SESSION = requests.Session()
        _SESSION.headers.update(_DEFAULT_HEADERS)
        _SESSION.max_redirects = 5
    return _SESSION


# ━━━━━━━━━━━━━━━━━━━━━━━━━ robots.txt 캐시 ━━━━━━━━━━━━━━━━━━━━━━━━━

_ROBOTS_CACHE: dict[str, bool] = {}  # domain -> crawl_allowed


def _is_crawl_allowed(domain: str, path: str = "/") -> bool:
    """robots.txt 확인. 캐시 후 재사용. 오류 시 허용으로 처리."""
    cache_key = domain
    if cache_key in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[cache_key]
    try:
        session = _get_session()
        robots_url = f"https://{domain}/robots.txt"
        resp = session.get(robots_url, timeout=10)
        if resp.status_code == 404:
            _ROBOTS_CACHE[cache_key] = True
            return True
        text = resp.text.lower()
        # Disallow: / (전체 차단) 확인 — 단순 파싱
        in_user_agent_all = False
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("user-agent:"):
                ua = line.split(":", 1)[1].strip()
                in_user_agent_all = (ua == "*")
            if in_user_agent_all and line.startswith("disallow:"):
                disallow_path = line.split(":", 1)[1].strip()
                if disallow_path == "/":
                    _ROBOTS_CACHE[cache_key] = False
                    return False
        _ROBOTS_CACHE[cache_key] = True
        return True
    except Exception:
        _ROBOTS_CACHE[cache_key] = True
        return True


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 공통 헬퍼 ━━━━━━━━━━━━━━━━━━━━━━━━━

def _download_pdf(url: str, extra_headers: Optional[dict] = None) -> Optional[bytes]:
    """URL에서 PDF bytes 다운로드. 성공 시 bytes, 실패 시 None."""
    if not _REQUESTS_AVAILABLE:
        return None
    try:
        session = _get_session()
        domain = urlparse(url).netloc
        if not _is_crawl_allowed(domain):
            logger.info(f"[pdf_collectors] robots.txt 차단: {domain}")
            return None
        headers = {}
        if extra_headers:
            headers.update(extra_headers)
        resp = session.get(url, headers=headers, timeout=_TIMEOUT, stream=True)
        if resp.status_code != 200:
            logger.debug(f"[pdf_collectors] HTTP {resp.status_code}: {url}")
            return None
        content_type = resp.headers.get("Content-Type", "")
        if "pdf" not in content_type.lower() and "octet-stream" not in content_type.lower():
            # HTML 응답이면 로그인 페이지 가능성 — 내용 확인
            first_bytes = b""
            for chunk in resp.iter_content(chunk_size=512):
                first_bytes = chunk
                break
            # PDF 매직바이트 %PDF 확인
            if not first_bytes.startswith(b"%PDF"):
                logger.debug(f"[pdf_collectors] PDF 아님 (Content-Type={content_type}): {url}")
                return None
            # 나머지 수신
            body = first_bytes
            for chunk in resp.iter_content(chunk_size=65536):
                body += chunk
                if len(body) > _MAX_PDF_BYTES:
                    logger.warning(f"[pdf_collectors] PDF 크기 초과: {url}")
                    return None
            return body if body.startswith(b"%PDF") else None
        # PDF content-type
        body = b""
        for chunk in resp.iter_content(chunk_size=65536):
            body += chunk
            if len(body) > _MAX_PDF_BYTES:
                logger.warning(f"[pdf_collectors] PDF 크기 초과: {url}")
                return None
        return body if body.startswith(b"%PDF") else None
    except Exception as e:
        logger.debug(f"[pdf_collectors] 다운로드 실패 ({url}): {e}")
        return None


def _normalize_date(date_str: str) -> str:
    """YYYY-MM-DD → YYYYMMDD 변환."""
    return date_str.replace("-", "").replace("/", "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 1. 삼성증권 ━━━━━━━━━━━━━━━━━━━━━━━━━

# 삼성증권 fileName 패턴:
#   2010/YYYYMMDDHHMMSS00K_02_06.pdf
#   포맷: "2010/" + YYYYMMDD + HHMMSS + "00K_02_06.pdf"
# pdf_url 원형 예시:
#   https://www.samsungpop.com/common.do?cmd=down&contentType=application/pdf&inlineYn=Y&saveKey=research.pdf&fileName=2010/2026031015471500K_02_06.pdf

_SAMSUNGPOP_BASE = "https://www.samsungpop.com/common.do"
_SAMSUNGPOP_DOMAIN = "www.samsungpop.com"

_SAMSUNGPOP_REFERER = "https://www.samsungpop.com/mbdd.do?cmd=research_company"


def samsungpop_fetch(
    ticker: str,
    date: str,
    title: str,
    file_name: Optional[str] = None,
    pdf_url: Optional[str] = None,
    **kwargs,
) -> Optional[bytes]:
    """삼성증권 직접 PDF 다운로드.

    Args:
        ticker: 종목코드 (예: "058610")
        date: 날짜 (YYYY-MM-DD 또는 YYYYMMDD)
        title: 리포트 제목 (현재 미사용, 향후 검색용)
        file_name: fileName 파라미터 (예: "2010/2026031015471500K_02_06.pdf")
        pdf_url: 원래 수집된 pdf_url (fileName 파싱 시도)

    Returns:
        PDF bytes 또는 None
    """
    # file_name 추출 시도
    fn = file_name
    if not fn and pdf_url:
        m = re.search(r"fileName=([^&]+)", pdf_url)
        if m:
            fn = m.group(1)

    if not fn:
        logger.debug(f"[samsungpop] fileName 없음: {ticker}/{date}")
        return None

    params = {
        "cmd": "down",
        "contentType": "application/pdf",
        "inlineYn": "Y",
        "saveKey": "research.pdf",
        "fileName": fn,
    }
    url = _SAMSUNGPOP_BASE + "?" + urlencode(params)
    return _download_pdf(url, extra_headers={"Referer": _SAMSUNGPOP_REFERER})


def samsungpop_fetch_by_url(pdf_url: str) -> Optional[bytes]:
    """pdf_url이 samsungpop.com URL인 경우 직접 다운로드."""
    if "samsungpop.com" not in pdf_url:
        return None
    return _download_pdf(pdf_url, extra_headers={"Referer": _SAMSUNGPOP_REFERER})


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 2. 유진투자증권 ━━━━━━━━━━━━━━━━━━━━━━━━━

# URL 패턴:
#   https://www.eugenefn.com/common/files/amail//20260130_005930_sophie.yim_114.pdf
# 파일명 구조: {YYYYMMDD}_{ticker}_{analyst_id}_{seq}.pdf

_EUGENEFN_BASE = "https://www.eugenefn.com/common/files/amail/"
_EUGENEFN_DOMAIN = "www.eugenefn.com"
_EUGENEFN_REFERER = "https://www.eugenefn.com/invest/research/companyresearch/index.do"

# 유진 analyst ID 패턴 — 미리 알기 어려우므로 pdf_url에서 파싱
_EUGENEFN_FILENAME_RE = re.compile(
    r"/(\d{8})_(\d{5,6})_([a-zA-Z0-9._-]+)_(\d+)\.pdf", re.IGNORECASE
)


def eugenefn_fetch(
    ticker: str,
    date: str,
    title: str,
    analyst_id: Optional[str] = None,
    seq: Optional[str] = None,
    pdf_url: Optional[str] = None,
    **kwargs,
) -> Optional[bytes]:
    """유진투자증권 직접 PDF 다운로드.

    Args:
        ticker: 종목코드
        date: 날짜 (YYYY-MM-DD 또는 YYYYMMDD)
        analyst_id: 애널리스트 식별자 (예: "sophie.yim")
        seq: 파일 시퀀스 번호
        pdf_url: 원래 pdf_url (파싱 시도)

    Returns:
        PDF bytes 또는 None
    """
    date_clean = _normalize_date(date)

    # pdf_url에서 파싱 시도
    if pdf_url:
        m = _EUGENEFN_FILENAME_RE.search(pdf_url)
        if m:
            file_name = f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}.pdf"
            url = _EUGENEFN_BASE + file_name
            return _download_pdf(url, extra_headers={"Referer": _EUGENEFN_REFERER})

    # analyst_id와 seq가 주어진 경우 직접 생성
    if analyst_id and seq:
        file_name = f"{date_clean}_{ticker}_{analyst_id}_{seq}.pdf"
        url = _EUGENEFN_BASE + file_name
        return _download_pdf(url, extra_headers={"Referer": _EUGENEFN_REFERER})

    logger.debug(f"[eugenefn] 파라미터 부족: {ticker}/{date}")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 3. 미래에셋증권 ━━━━━━━━━━━━━━━━━━━━━━━━━

# URL 패턴:
#   https://securities.miraeasset.com/bbs/download/file.do?attachmentId=12345678
# attachmentId는 HTML 파싱 또는 pdf_url에서 추출

_MIRAEASSET_BASE = "https://securities.miraeasset.com/bbs/download/file.do"
_MIRAEASSET_DOMAIN = "securities.miraeasset.com"
_MIRAEASSET_REFERER = "https://securities.miraeasset.com/hki/hki3028/r/hki3028r00.do"

_MIRAEASSET_ID_RE = re.compile(r"(?:attachmentId|download/?)[\=\/](\d+)", re.IGNORECASE)


def miraeasset_fetch(
    ticker: str,
    date: str,
    title: str,
    attachment_id: Optional[str] = None,
    pdf_url: Optional[str] = None,
    **kwargs,
) -> Optional[bytes]:
    """미래에셋증권 직접 PDF 다운로드.

    Args:
        ticker: 종목코드
        date: 날짜
        attachment_id: 첨부파일 ID
        pdf_url: 원래 pdf_url (ID 파싱 시도)

    Returns:
        PDF bytes 또는 None
    """
    aid = attachment_id

    # pdf_url에서 파싱 시도
    if not aid and pdf_url and "miraeasset.com" in pdf_url:
        m = _MIRAEASSET_ID_RE.search(pdf_url)
        if m:
            aid = m.group(1)

    if not aid:
        # 대체 URL 형식 시도 (securities.miraeasset.com/bbs/download/{id}.pdf)
        if pdf_url and "miraeasset.com" in pdf_url:
            url_path = urlparse(pdf_url).path
            parts = url_path.rstrip("/").rsplit("/", 1)
            if parts:
                candidate = parts[-1].replace(".pdf", "")
                if candidate.isdigit():
                    aid = candidate

    if not aid:
        logger.debug(f"[miraeasset] attachmentId 없음: {ticker}/{date}")
        return None

    url = f"{_MIRAEASSET_BASE}?attachmentId={aid}"
    return _download_pdf(url, extra_headers={"Referer": _MIRAEASSET_REFERER})


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 4. 하나증권 ━━━━━━━━━━━━━━━━━━━━━━━━━

# URL 패턴:
#   https://file.hanaw.com/download/research/FileServer/WEB/{path}
# path 예시: "company/2026/03/16/hana_260316_001450.pdf"
#            "strategy/market/2026/04/24/lee_260427.pdf"

_HANAW_BASE = "https://file.hanaw.com/download/research/FileServer/WEB"
_HANAW_DOMAIN = "file.hanaw.com"
_HANAW_REFERER = "https://www.hanaw.com/main/research/company/list.aspx"

_HANAW_PATH_RE = re.compile(
    r"file\.hanaw\.com/download/research/FileServer/WEB/(.+?)(?:\?|$)"
)


def hanaw_fetch(
    ticker: str,
    date: str,
    title: str,
    file_path: Optional[str] = None,
    pdf_url: Optional[str] = None,
    **kwargs,
) -> Optional[bytes]:
    """하나증권 직접 PDF 다운로드.

    Args:
        ticker: 종목코드
        date: 날짜 (YYYY-MM-DD)
        title: 리포트 제목 (현재 미사용)
        file_path: WEB 이후 경로 (예: "company/2026/03/16/hana_260316_001450.pdf")
        pdf_url: 원래 pdf_url

    Returns:
        PDF bytes 또는 None
    """
    # pdf_url이 이미 완성된 형태인 경우
    if pdf_url and "hanaw.com" in pdf_url:
        m = _HANAW_PATH_RE.search(pdf_url)
        if m:
            path = m.group(1)
            url = f"{_HANAW_BASE}/{path}"
            return _download_pdf(url, extra_headers={"Referer": _HANAW_REFERER})
        # pdf_url 자체가 직접 다운로드 URL인 경우
        if pdf_url.startswith("https://file.hanaw.com"):
            return _download_pdf(pdf_url, extra_headers={"Referer": _HANAW_REFERER})

    if file_path:
        url = f"{_HANAW_BASE}/{file_path.lstrip('/')}"
        return _download_pdf(url, extra_headers={"Referer": _HANAW_REFERER})

    # 날짜 기반 경로 추측 시도
    date_clean = _normalize_date(date)  # YYYYMMDD
    if len(date_clean) == 8:
        yyyy = date_clean[:4]
        mm = date_clean[4:6]
        dd = date_clean[6:8]
        # 일반적 패턴: company/YYYY/MM/DD/hana_YYMMDD_{ticker}.pdf
        yy = yyyy[2:]
        guessed_paths = [
            f"company/{yyyy}/{mm}/{dd}/hana_{yy}{mm}{dd}_{ticker}.pdf",
            f"company/{yyyy}/{mm}/{dd}/hana_{yy}{mm}{dd}.pdf",
        ]
        for path in guessed_paths:
            url = f"{_HANAW_BASE}/{path}"
            result = _download_pdf(url, extra_headers={"Referer": _HANAW_REFERER})
            if result:
                return result

    logger.debug(f"[hanaw] 경로 추측 실패: {ticker}/{date}")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 5. DB금융투자 (네이버 호스팅) ━━━━━━━━━━━━━━━━━━━━━━━━━

# URL 패턴 (네이버 imgstock 호스팅):
#   https://ssl.pstatic.net/imgstock/upload/research/company/{id}.pdf
# id 예시: "202602231024511800012489" (24자리 숫자)

_DBFI_BASE = "https://ssl.pstatic.net/imgstock/upload/research/company"
_DBFI_DOMAIN = "ssl.pstatic.net"
_DBFI_REFERER = "https://finance.naver.com/research/company_list.naver"

_DBFI_ID_RE = re.compile(r"/imgstock/upload/research/company/([^/.]+)\.pdf")
_DBFI_STOCK_RE = re.compile(r"/stock-research/company/(?:[^/]+)/\d{8}_company_([^.]+)\.pdf")


def dbfi_fetch(
    ticker: str,
    date: str,
    title: str,
    report_id: Optional[str] = None,
    pdf_url: Optional[str] = None,
    **kwargs,
) -> Optional[bytes]:
    """DB금융투자 (네이버 imgstock 호스팅) 직접 PDF 다운로드.

    Args:
        ticker: 종목코드
        date: 날짜
        report_id: 리포트 ID (24자리 숫자)
        pdf_url: 원래 pdf_url

    Returns:
        PDF bytes 또는 None
    """
    rid = report_id

    if not rid and pdf_url:
        # ssl.pstatic.net/imgstock 패턴
        m = _DBFI_ID_RE.search(pdf_url)
        if m:
            rid = m.group(1)
        # stock.pstatic.net/stock-research 패턴
        if not rid:
            m = _DBFI_STOCK_RE.search(pdf_url)
            if m:
                rid = m.group(1)

    if rid:
        url = f"{_DBFI_BASE}/{rid}.pdf"
        result = _download_pdf(url, extra_headers={"Referer": _DBFI_REFERER})
        if result:
            return result

    # pdf_url 자체가 ssl.pstatic.net URL인 경우 직접 시도
    if pdf_url and "pstatic.net" in pdf_url:
        return _download_pdf(pdf_url, extra_headers={"Referer": _DBFI_REFERER})

    logger.debug(f"[dbfi] report_id 없음: {ticker}/{date}")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 6. 네이버 증권 리서치 ━━━━━━━━━━━━━━━━━━━━━━━━━

# URL 패턴:
#   https://stock.pstatic.net/stock-research/company/{brk_cd}/{date}_company_{id}.pdf
# brk_cd 예: "005930" 아님, 증권사코드 (2자리 or 3자리)
# 네이버는 보통 HTML 파싱이 필요하지만, pdf_url이 제공되면 직접 다운로드 가능

_NAVER_STOCK_RE = re.compile(
    r"stock\.pstatic\.net/stock-research/company/([^/]+)/(\d{8}_company_[^.]+)\.pdf"
)
_NAVER_IMGSTOCK_RE = re.compile(
    r"ssl\.pstatic\.net/imgstock/upload/research/company/([^/.]+)\.pdf"
)
_NAVER_REFERER = "https://finance.naver.com/research/company_list.naver"

# 네이버 증권 리서치 목록 API
_NAVER_RESEARCH_API = "https://finance.naver.com/research/company_list.naver"
_NAVER_RESEARCH_API_V2 = "https://m.stock.naver.com/api/research/company"


def naver_research_fetch(
    ticker: str,
    date: str,
    title: str,
    pdf_url: Optional[str] = None,
    **kwargs,
) -> Optional[bytes]:
    """네이버 증권 리서치 PDF 다운로드.

    Args:
        ticker: 종목코드
        date: 날짜 (YYYY-MM-DD)
        title: 리포트 제목 (검색 후보)
        pdf_url: 원래 수집된 URL (있으면 직접 시도)

    Returns:
        PDF bytes 또는 None
    """
    # pdf_url이 이미 pstatic.net이면 직접 다운로드
    if pdf_url and "pstatic.net" in pdf_url:
        result = _download_pdf(pdf_url, extra_headers={"Referer": _NAVER_REFERER})
        if result:
            return result

    # 네이버 리서치 API로 해당 종목 최근 리포트 검색
    try:
        session = _get_session()
        date_clean = _normalize_date(date)  # YYYYMMDD
        params = {
            "stock_code": ticker,
            "celuUseYn": "N",
            "pageSize": "20",
            "page": "1",
        }
        headers = {"Referer": _NAVER_REFERER}
        resp = session.get(_NAVER_RESEARCH_API, params=params, headers=headers, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return None
        # HTML에서 PDF 링크 파싱
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        # 날짜 매칭 시도
        target_date_str = f"{date_clean[:4]}.{date_clean[4:6]}.{date_clean[6:8]}"
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "pstatic.net" in href and date_clean in href.replace("-", "").replace(".", ""):
                result = _download_pdf(href, extra_headers={"Referer": _NAVER_REFERER})
                if result:
                    return result
    except Exception as e:
        logger.debug(f"[naver_research] 파싱 실패 {ticker}/{date}: {e}")

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ wisereport URL 유틸리티 ━━━━━━━━━━━━━━━━━━━━━━━━━

# wisereport brk_cd → (broker 식별자, collector_func, source_label) 매핑
# brk_cd는 wisereport LoadReport URL의 brk_cd 파라미터값 (문자열)
# 새 brk_cd 발견 시 이 딕셔너리에 추가하면 자동으로 폴백 체인에 포함됨
WISEREPORT_BROKER_MAP: dict[str, dict] = {
    "1":   {"broker": "삼성증권",    "func_name": "samsungpop",  "label": "samsungpop_direct"},
    "16":  {"broker": "DB금융투자",   "func_name": "dbfi",        "label": "dbfi_direct"},
    "27":  {"broker": "한국투자증권",  "func_name": None,          "label": None},   # 공개 PDF 없음
    "34":  {"broker": "하나증권",     "func_name": "hanaw",       "label": "hanaw_direct"},
    "39":  {"broker": "키움증권",     "func_name": None,          "label": None},   # 네이버 pstatic
    "44":  {"broker": "다올투자증권",  "func_name": None,          "label": None},   # 공개 PDF 없음
    "56":  {"broker": "미래에셋증권",  "func_name": "miraeasset",  "label": "miraeasset_direct"},
    "57":  {"broker": "하나증권",     "func_name": "hanaw",       "label": "hanaw_direct"},
    "63":  {"broker": "유진투자증권",  "func_name": "eugenefn",    "label": "eugenefn_direct"},
}

_WISEREPORT_URL_RE = re.compile(
    r"wisereport\.co\.kr/comm/LoadReport\.aspx"
    r"\?rpt_id=(\d+)&brk_cd=(\d+)&fpath=([^&]+)&target=comp",
    re.IGNORECASE,
)


def parse_wisereport_url(url: str) -> Optional[dict]:
    """wisereport LoadReport URL에서 rpt_id, brk_cd, fpath 파싱.

    Args:
        url: wisereport LoadReport.aspx URL

    Returns:
        {"rpt_id": "...", "brk_cd": "...", "fpath": "...", "broker_info": {...}} 또는 None
    """
    if not url or "wisereport" not in url:
        return None
    m = _WISEREPORT_URL_RE.search(url)
    if not m:
        return None
    rpt_id, brk_cd, fpath = m.group(1), m.group(2), m.group(3)
    broker_info = WISEREPORT_BROKER_MAP.get(brk_cd, {})
    return {
        "rpt_id": rpt_id,
        "brk_cd": brk_cd,
        "fpath": fpath,
        "broker_info": broker_info,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 6. 한국투자증권 (stub) ━━━━━━━━━━━━━━━━━━━━━━━━━

# 한국투자증권(truefriend.com)은 공개 PDF URL이 없음 (로그인 필요).
# wisereport fpath 패턴: 1F027{YYYYMMDD}_{ticker}.pdf
# 향후 공개 엔드포인트 발견 시 이 함수를 구현.

def koreainvest_fetch(
    ticker: str,
    date: str,
    title: str,
    fpath: Optional[str] = None,
    pdf_url: Optional[str] = None,
    **kwargs,
) -> Optional[bytes]:
    """한국투자증권 직접 PDF 다운로드 (현재 stub — 공개 URL 없음).

    한국투자증권(truefriend.com)은 공개 PDF 서빙 엔드포인트가 없어
    로그인 없이 접근 불가. wisereport fpath 패턴(1F027YYYYMMDD_ticker.pdf)이
    있으나 공개 서버 경로 미확인.

    Args:
        ticker: 종목코드
        date: 날짜 (YYYY-MM-DD)
        title: 리포트 제목
        fpath: wisereport fpath (예: "1F02720260420_058610.pdf")
        pdf_url: wisereport LoadReport URL

    Returns:
        None (현재 구현 불가)
    """
    logger.debug(f"[koreainvest] 공개 PDF URL 없음 (stub): {ticker}/{date}")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 7. 다올투자증권 (stub) ━━━━━━━━━━━━━━━━━━━━━━━━━

# 다올투자증권(구 KTB투자증권)은 공개 PDF URL이 없음.
# wisereport fpath 패턴: 1F044{YYYYMMDD}_{ticker}.pdf / 1L044{YYYYMMDD}_{ticker}.pdf
# 향후 공개 엔드포인트 발견 시 이 함수를 구현.

def daol_fetch(
    ticker: str,
    date: str,
    title: str,
    fpath: Optional[str] = None,
    pdf_url: Optional[str] = None,
    **kwargs,
) -> Optional[bytes]:
    """다올투자증권 직접 PDF 다운로드 (현재 stub — 공개 URL 없음).

    다올투자증권(daolco.com)은 공개 PDF 서빙 엔드포인트가 없어
    로그인 없이 접근 불가. wisereport fpath 패턴(1F/1L044YYYYMMDD_ticker.pdf)이
    있으나 공개 서버 경로 미확인.

    Args:
        ticker: 종목코드
        date: 날짜 (YYYY-MM-DD)
        title: 리포트 제목
        fpath: wisereport fpath (예: "1F04420260316_058610.pdf")
        pdf_url: wisereport LoadReport URL

    Returns:
        None (현재 구현 불가)
    """
    logger.debug(f"[daol] 공개 PDF URL 없음 (stub): {ticker}/{date}")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 브로커 힌트 매핑 ━━━━━━━━━━━━━━━━━━━━━━━━━

# source 이름 → (collector_func, source_label)
_BROKER_MAP: dict[str, tuple] = {
    # 삼성증권
    "삼성": (samsungpop_fetch, "samsungpop_direct"),
    "samsung": (samsungpop_fetch, "samsungpop_direct"),
    "삼성증권": (samsungpop_fetch, "samsungpop_direct"),
    "samsung securities": (samsungpop_fetch, "samsungpop_direct"),
    # 유진투자증권
    "유진": (eugenefn_fetch, "eugenefn_direct"),
    "eugenefn": (eugenefn_fetch, "eugenefn_direct"),
    "유진투자": (eugenefn_fetch, "eugenefn_direct"),
    # 미래에셋
    "미래에셋": (miraeasset_fetch, "miraeasset_direct"),
    "miraeasset": (miraeasset_fetch, "miraeasset_direct"),
    "미래에셋증권": (miraeasset_fetch, "miraeasset_direct"),
    "mirae": (miraeasset_fetch, "miraeasset_direct"),
    # 하나증권
    "하나": (hanaw_fetch, "hanaw_direct"),
    "hanaw": (hanaw_fetch, "hanaw_direct"),
    "하나증권": (hanaw_fetch, "hanaw_direct"),
    "hana": (hanaw_fetch, "hanaw_direct"),
    # DB금융투자
    "db": (dbfi_fetch, "dbfi_direct"),
    "db금융": (dbfi_fetch, "dbfi_direct"),
    "db금융투자": (dbfi_fetch, "dbfi_direct"),
    "dbfi": (dbfi_fetch, "dbfi_direct"),
    # 한국투자증권 (stub — 공개 PDF 없음, 향후 엔드포인트 발견 시 활성화)
    "한국투자": (koreainvest_fetch, "koreainvest_direct"),
    "한국투자증권": (koreainvest_fetch, "koreainvest_direct"),
    "koreainvest": (koreainvest_fetch, "koreainvest_direct"),
    "truefriend": (koreainvest_fetch, "koreainvest_direct"),
    # 다올투자증권 (stub — 공개 PDF 없음, 향후 엔드포인트 발견 시 활성화)
    "다올": (daol_fetch, "daol_direct"),
    "다올투자": (daol_fetch, "daol_direct"),
    "다올투자증권": (daol_fetch, "daol_direct"),
    "daol": (daol_fetch, "daol_direct"),
}

# URL 도메인 → (collector_func, source_label)
_URL_DOMAIN_MAP: dict[str, tuple] = {
    "samsungpop.com": (samsungpop_fetch, "samsungpop_direct"),
    "www.samsungpop.com": (samsungpop_fetch, "samsungpop_direct"),
    "eugenefn.com": (eugenefn_fetch, "eugenefn_direct"),
    "www.eugenefn.com": (eugenefn_fetch, "eugenefn_direct"),
    "securities.miraeasset.com": (miraeasset_fetch, "miraeasset_direct"),
    "file.hanaw.com": (hanaw_fetch, "hanaw_direct"),
    "ssl.pstatic.net": (dbfi_fetch, "dbfi_direct"),
    "stock.pstatic.net": (naver_research_fetch, "naver_research"),
}


def _get_collector_by_url(pdf_url: str):
    """pdf_url 도메인으로 collector 함수와 source_label 반환."""
    if not pdf_url:
        return None, None
    domain = urlparse(pdf_url).netloc.lower()
    if domain in _URL_DOMAIN_MAP:
        return _URL_DOMAIN_MAP[domain]
    # 부분 매칭
    for d, pair in _URL_DOMAIN_MAP.items():
        if d in domain:
            return pair
    return None, None


def _get_collector_by_broker(broker: str):
    """broker 힌트로 collector 함수와 source_label 반환."""
    if not broker:
        return None, None
    b = broker.lower().strip()
    for key, pair in _BROKER_MAP.items():
        if key in b:
            return pair
    return None, None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 통합 폴백 함수 ━━━━━━━━━━━━━━━━━━━━━━━━━

_FREE_COLLECTORS_ORDER = [
    (samsungpop_fetch, "samsungpop_direct"),
    (eugenefn_fetch, "eugenefn_direct"),
    (miraeasset_fetch, "miraeasset_direct"),
    (hanaw_fetch, "hanaw_direct"),
    (dbfi_fetch, "dbfi_direct"),
    (koreainvest_fetch, "koreainvest_direct"),  # stub — 현재 항상 None 반환
    (daol_fetch, "daol_direct"),                # stub — 현재 항상 None 반환
]


def _try_wisereport_fpath_collector(
    url: str,
    ticker: str,
    date: str,
    title: str,
) -> Optional[tuple[bytes, str]]:
    """wisereport URL의 fpath + brk_cd로 broker 직접 collector 시도.

    fpath 패턴: 1F{brk_cd_3digit}{YYYYMMDD}_{ticker}[_suffix].pdf
    brk_cd에 대응하는 collector별 적절한 파라미터로 변환해 시도.

    브로커별 전략:
    - hanaw (brk_cd=34,57): fpath에서 YYYYMMDD 추출 → 날짜 기반 경로 추측
    - samsungpop (brk_cd=1): fileName은 타임스탬프 포함이라 추측 불가 → 스킵
    - miraeasset (brk_cd=56): attachmentId는 숫자 시퀀스라 추측 불가 → 스킵
    - eugenefn (brk_cd=63): analyst_id 없으면 추측 불가 → 스킵
    - dbfi (brk_cd=16): report_id는 24자리 타임스탬프라 추측 불가 → 스킵

    Returns:
        (pdf_bytes, source_label) 또는 None
    """
    parsed = parse_wisereport_url(url)
    if not parsed:
        return None
    broker_info = parsed.get("broker_info", {})
    func_name = broker_info.get("func_name")
    label = broker_info.get("label")
    fpath = parsed.get("fpath", "")
    if not func_name or not label or not fpath:
        return None

    # fpath에서 YYYYMMDD 추출 (1F027{YYYYMMDD}_{ticker}... 패턴)
    _FPATH_DATE_RE = re.compile(r"1[FLC]\d{3}(\d{8})_", re.IGNORECASE)
    m = _FPATH_DATE_RE.search(fpath)
    fpath_date = m.group(1) if m else _normalize_date(date)  # YYYYMMDD 형식

    try:
        # hanaw: fpath에서 날짜 추출 후 날짜 기반 경로로 시도
        if func_name == "hanaw":
            result = hanaw_fetch(
                ticker=ticker, date=fpath_date, title=title, pdf_url=url
            )
            if result:
                logger.info(f"[pdf_collectors] wisereport→hanaw 성공 ({label}): {ticker}/{fpath_date}")
                return (result, label)
        # 다른 브로커는 fpath만으로 추측 불가 → 스킵
    except Exception as e:
        logger.debug(f"[pdf_collectors] wisereport fpath collector 실패 ({label}): {e}")
    return None


def fetch_pdf_with_fallback(
    ticker: str,
    date: str,
    title: str,
    broker_hint: Optional[str] = None,
    pdf_url: Optional[str] = None,
    **kwargs,
) -> Optional[tuple[bytes, str]]:
    """PDF 폴백 체인 (6단계):
      1. broker_hint 매칭 collector
      2. pdf_url 도메인 매칭 collector
      3. wisereport fpath 기반 broker 직접 collector (신규)
      4. 전체 무료 collector 순서대로
      5. 네이버 리서치 HTML 파싱
      6. (wisereport 유료 — caller에서 meta_only 처리)

    Args:
        ticker: 종목코드 (예: "058610")
        date: 날짜 (YYYY-MM-DD)
        title: 리포트 제목
        broker_hint: 증권사 이름 힌트 (예: "삼성", "하나", "DB금융투자")
        pdf_url: 원래 수집된 pdf_url
        **kwargs: 각 collector에 전달 (file_name, analyst_id, attachment_id 등)

    Returns:
        (pdf_bytes, source_used) 또는 None
    """
    if not _REQUESTS_AVAILABLE:
        logger.warning("[pdf_collectors] requests 미설치 — PDF 다운로드 불가")
        return None

    common_kwargs = dict(
        ticker=ticker,
        date=date,
        title=title,
        pdf_url=pdf_url,
        **kwargs,
    )

    # 1순위: broker_hint로 매칭된 collector
    if broker_hint:
        func, label = _get_collector_by_broker(broker_hint)
        if func:
            try:
                result = func(**common_kwargs)
                if result:
                    logger.info(f"[pdf_collectors] 성공 ({label}): {ticker}/{date}")
                    return (result, label)
            except Exception as e:
                logger.debug(f"[pdf_collectors] {label} 실패: {e}")

    # 2순위: pdf_url 도메인으로 매칭된 collector
    if pdf_url:
        func, label = _get_collector_by_url(pdf_url)
        if func:
            try:
                result = func(**common_kwargs)
                if result:
                    logger.info(f"[pdf_collectors] 성공 ({label} via url): {ticker}/{date}")
                    return (result, label)
            except Exception as e:
                logger.debug(f"[pdf_collectors] {label} (url) 실패: {e}")

    # 3순위: wisereport fpath → broker 직접 collector (신규)
    if pdf_url and "wisereport" in pdf_url:
        result = _try_wisereport_fpath_collector(pdf_url, ticker, date, title)
        if result:
            return result

    # 4순위: 전체 무료 collector 순서대로
    tried_labels: set[str] = set()
    if broker_hint:
        _, b_label = _get_collector_by_broker(broker_hint)
        if b_label:
            tried_labels.add(b_label)
    if pdf_url:
        _, u_label = _get_collector_by_url(pdf_url)
        if u_label:
            tried_labels.add(u_label)

    for func, label in _FREE_COLLECTORS_ORDER:
        if label in tried_labels:
            continue
        try:
            result = func(**common_kwargs)
            if result:
                logger.info(f"[pdf_collectors] 성공 ({label} fallback): {ticker}/{date}")
                return (result, label)
        except Exception as e:
            logger.debug(f"[pdf_collectors] {label} fallback 실패: {e}")

    # 5순위: 네이버 리서치 HTML 파싱
    try:
        result = naver_research_fetch(**common_kwargs)
        if result:
            logger.info(f"[pdf_collectors] 성공 (naver_research): {ticker}/{date}")
            return (result, "naver_research")
    except Exception as e:
        logger.debug(f"[pdf_collectors] naver_research 실패: {e}")

    logger.info(f"[pdf_collectors] 전체 실패: {ticker}/{date}/{title}")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 브로커 PDF URL 직접 다운로드 ━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_pdf_by_url(pdf_url: str, broker_hint: Optional[str] = None) -> Optional[tuple[bytes, str]]:
    """pdf_url만으로 PDF 다운로드 시도.

    report_crawler의 기존 wisereport URL이 아닌 경우 직접 다운로드에 사용.

    Args:
        pdf_url: 다운로드할 URL
        broker_hint: 증권사 힌트 (선택)

    Returns:
        (pdf_bytes, source_used) 또는 None
    """
    if not pdf_url or not _REQUESTS_AVAILABLE:
        return None

    # URL 도메인 기반 collector
    func, label = _get_collector_by_url(pdf_url)
    if func:
        try:
            result = func(ticker="", date="", title="", pdf_url=pdf_url)
            if result:
                return (result, label)
        except Exception as e:
            logger.debug(f"[pdf_collectors] url collector 실패 ({label}): {e}")

    # broker_hint 기반 collector
    if broker_hint:
        func, label = _get_collector_by_broker(broker_hint)
        if func:
            try:
                result = func(ticker="", date="", title="", pdf_url=pdf_url)
                if result:
                    return (result, label)
            except Exception as e:
                logger.debug(f"[pdf_collectors] broker collector 실패 ({label}): {e}")

    # 마지막: 직접 다운로드 시도
    result = _download_pdf(pdf_url)
    if result:
        # 도메인으로 라벨 추정
        domain = urlparse(pdf_url).netloc
        if "wisereport" in domain:
            label = "wisereport_paid"
        elif "pstatic.net" in domain:
            label = "naver_research"
        else:
            label = "direct"
        return (result, label)

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 유틸리티 ━━━━━━━━━━━━━━━━━━━━━━━━━

def is_pdf_url_free(pdf_url: str) -> bool:
    """PDF URL이 무료 직접 다운로드 가능한지 판단."""
    if not pdf_url:
        return False
    domain = urlparse(pdf_url).netloc.lower()
    free_domains = {
        "www.samsungpop.com", "samsungpop.com",
        "www.eugenefn.com", "eugenefn.com",
        "securities.miraeasset.com",
        "file.hanaw.com",
        "ssl.pstatic.net",
        "stock.pstatic.net",
        "consensus.hankyung.com",
    }
    return any(d in domain for d in free_domains)


def get_source_label(pdf_url: str, extraction_status: str = "") -> str:
    """pdf_url과 extraction_status로 source_used 라벨 결정."""
    if extraction_status == "meta_only":
        return "wisereport_paid"
    _, label = _get_collector_by_url(pdf_url)
    if label:
        return label
    if "wisereport" in pdf_url:
        return "wisereport_paid"
    if "pstatic.net" in pdf_url:
        return "naver_research"
    return ""


def reset_session():
    """테스트용: 세션 초기화."""
    global _SESSION
    _SESSION = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 셀프 테스트 ━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # 058610 에스피지 삼성증권 2026-03-11 검증
    SAMSUNG_URL = (
        "https://www.samsungpop.com/common.do?cmd=down&contentType=application/pdf"
        "&inlineYn=Y&saveKey=research.pdf&fileName=2010/2026031015471500K_02_06.pdf"
    )
    print("[TEST] 삼성증권 직접 다운로드 테스트 (058610 2026-03-11)")
    res = samsungpop_fetch_by_url(SAMSUNG_URL)
    if res:
        print(f"  성공: {len(res)} bytes, 매직바이트={res[:4]}")
    else:
        print("  실패")

    # 유진투자증권 005930 2026-01-30 검증
    EUGENE_URL = "https://www.eugenefn.com/common/files/amail//20260130_005930_sophie.yim_114.pdf"
    print("[TEST] 유진투자증권 직접 다운로드 테스트 (005930 2026-01-30)")
    res = eugenefn_fetch(ticker="005930", date="2026-01-30", title="테스트", pdf_url=EUGENE_URL)
    if res:
        print(f"  성공: {len(res)} bytes")
    else:
        print("  실패")

    # fetch_pdf_with_fallback 통합 테스트
    print("[TEST] 통합 폴백 테스트")
    result = fetch_pdf_with_fallback(
        ticker="058610",
        date="2026-03-11",
        title="에스피지 리포트",
        broker_hint="삼성",
        pdf_url=SAMSUNG_URL,
    )
    if result:
        pdf_bytes, source = result
        print(f"  성공: source={source}, {len(pdf_bytes)} bytes")
    else:
        print("  전체 실패")
