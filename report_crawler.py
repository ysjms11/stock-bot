"""증권사 리포트 자동 수집 — 네이버증권 리서치 크롤링 + PDF 텍스트 추출"""
import os
import json
import time
import tempfile
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

KST = ZoneInfo("Asia/Seoul")
REPORTS_FILE = "/data/reports.json"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/research/company_list.naver",
}
_MAX_DAILY = 50       # 하루 최대 수집 건수
_MAX_TEXT = 10000      # PDF 텍스트 최대 글자수
_MAX_PDF_BYTES = 50 * 1024 * 1024  # PDF 최대 50MB
_RETAIN_DAYS = 90      # 보관 기간
_MAX_PER_TICKER = 5    # 종목당 최대 보관 건수
_ALLOWED_PDF_DOMAINS = {"ssl.pstatic.net", "finance.naver.com", "stock.pstatic.net"}

# ━━━━━━━━━━━━━━━━━━━━━━━━━ 파일 저장/로드 ━━━━━━━━━━━━━━━━━━━━━━━━━

def load_reports() -> dict:
    """reports.json 로드. 구조: {"reports": [...], "last_collected": "..."}"""
    if os.path.exists(REPORTS_FILE):
        try:
            with open(REPORTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"reports": [], "last_collected": ""}


def save_reports(data: dict):
    os.makedirs(os.path.dirname(REPORTS_FILE), exist_ok=True)
    with open(REPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 크롤링 ━━━━━━━━━━━━━━━━━━━━━━━━━

def crawl_naver_reports(ticker: str, name: str, existing_urls: set) -> list:
    """네이버증권 리서치에서 종목 리포트 목록 크롤링.

    페이지 구조 (table.type_1):
      th 행: 종목명 | 제목 | 증권사 | 첨부 | 작성일 | 조회수
      td 6개인 행이 실제 리포트 데이터.
      td 1개인 행은 구분선/공백 → 스킵.

    Returns: [{"date", "ticker", "name", "source", "title", "pdf_url"}, ...]
    existing_urls에 있는 건 스킵 (중복 방지).
    """
    url = (
        "https://finance.naver.com/research/company_list.naver"
        f"?searchType=itemCode&itemCode={ticker}"
    )
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[report] 크롤링 요청 실패 ({ticker}): {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="type_1")
    if not table:
        print(f"[report] 테이블 미발견 ({ticker})")
        return []

    reports = []
    for row in table.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) != 6:
            continue  # 헤더(th) 행이나 구분선(td 1개) 행 스킵

        # td[0]: 종목명 (링크), td[1]: 제목 (링크), td[2]: 증권사
        # td[3]: 첨부 PDF (a 태그 href), td[4]: 작성일, td[5]: 조회수
        title_tag = tds[1].find("a")
        title = title_tag.get_text(strip=True) if title_tag else tds[1].get_text(strip=True)

        # PDF URL 추출
        file_td = tds[3]
        pdf_link = file_td.find("a")
        if not pdf_link:
            continue
        pdf_url = pdf_link.get("href", "").strip()
        if not pdf_url:
            continue
        # 절대경로 변환
        if pdf_url.startswith("//"):
            pdf_url = "https:" + pdf_url
        elif pdf_url.startswith("/"):
            pdf_url = "https://finance.naver.com" + pdf_url

        if pdf_url in existing_urls:
            continue

        source = tds[2].get_text(strip=True)  # 증권사명

        # 날짜: "26.03.23" → "2026-03-23"
        raw_date = tds[4].get_text(strip=True)
        date_str = _parse_date(raw_date)

        reports.append({
            "date": date_str,
            "ticker": ticker,
            "name": name,
            "source": source,
            "title": title,
            "pdf_url": pdf_url,
        })

    return reports


def _parse_date(raw: str) -> str:
    """'26.03.23' → '2026-03-23' 형태로 변환."""
    raw = raw.strip()
    if not raw:
        return ""
    parts = raw.split(".")
    if len(parts) == 3:
        yy, mm, dd = parts
        yy = yy.strip()
        mm = mm.strip().zfill(2)
        dd = dd.strip().zfill(2)
        year = int(yy)
        if year < 100:
            year += 2000
        return f"{year}-{mm}-{dd}"
    return raw


# ━━━━━━━━━━━━━━━━━━━━━━━━━ PDF 텍스트 추출 ━━━━━━━━━━━━━━━━━━━━━━━━━

def _validate_korean_text(text: str) -> bool:
    """추출 텍스트에 한글이 10% 이상 포함되어 있는지 검증."""
    if not text:
        return False
    korean_chars = sum(1 for c in text if '\uac00' <= c <= '\ud7a3')
    return korean_chars / len(text) >= 0.10


def _is_chart_image_text(text: str) -> bool:
    """숫자+좌표+기호만으로 이루어진 차트 이미지 PDF 판정.
    숫자·소수점·콤마·공백·줄바꿈·좌표기호((),-.)가 90% 이상이면 차트."""
    if not text or len(text) < 20:
        return False
    chart_chars = sum(1 for c in text if c in '0123456789.,()-+/% \t\n\r')
    return chart_chars / len(text) >= 0.90


def extract_pdf_text(pdf_url: str) -> tuple[str, str]:
    """PDF 다운로드 후 pdfplumber로 텍스트 추출.
    Returns: (text, status) — status: 'success'|'failed'|'partial'
    최대 _MAX_TEXT(10000)자. 임시 파일은 처리 후 삭제.
    """
    try:
        import pdfplumber
    except ImportError:
        print("[report] pdfplumber 미설치")
        return ("", "failed")

    # URL 도메인 검증 — 허용된 네이버 도메인만 다운로드
    try:
        host = urlparse(pdf_url).hostname or ""
        if host not in _ALLOWED_PDF_DOMAINS:
            print(f"[report] 허용되지 않은 PDF 도메인: {host}")
            return ("", "failed")
    except Exception:
        return ("", "failed")

    tmp_path = None
    try:
        resp = requests.get(pdf_url, headers=_HEADERS, timeout=30, stream=True)
        if resp.status_code != 200:
            return ("", "failed")

        # Content-Length 사전 검사
        content_len = resp.headers.get("Content-Length")
        if content_len and int(content_len) > _MAX_PDF_BYTES:
            print(f"[report] PDF 크기 초과 ({int(content_len)//1024//1024}MB): {pdf_url}")
            resp.close()
            return ("", "failed")

        downloaded = 0
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
            for chunk in resp.iter_content(8192):
                downloaded += len(chunk)
                if downloaded > _MAX_PDF_BYTES:
                    print(f"[report] PDF 다운로드 크기 초과 ({downloaded//1024//1024}MB): {pdf_url}")
                    break
                tmp.write(chunk)

        if downloaded > _MAX_PDF_BYTES:
            return ("", "failed")

        text = ""
        truncated = False
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
                if len(text) >= _MAX_TEXT:
                    truncated = True
                    break

        text = text[:_MAX_TEXT].strip()

        if not text:
            return ("", "failed")

        if _is_chart_image_text(text):
            return ("[PDF 텍스트 추출 실패 - 차트/이미지 PDF]", "failed")

        if not _validate_korean_text(text):
            return ("[PDF 텍스트 추출 실패 - 이미지 기반 PDF]", "failed")

        if truncated:
            return (text, "partial")

        return (text, "success")
    except Exception as e:
        print(f"[report] PDF 추출 실패 ({pdf_url}): {e}")
        return ("", "failed")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 수집 메인 ━━━━━━━━━━━━━━━━━━━━━━━━━

def collect_reports(tickers_dict: dict, max_count: int = _MAX_DAILY) -> list:
    """종목 딕셔너리({ticker: name})에 대해 리포트 수집.
    Returns: 새로 수집된 리포트 리스트.
    """
    data = load_reports()
    existing_urls = {r["pdf_url"] for r in data["reports"] if r.get("pdf_url")}

    new_reports = []
    count = 0

    for ticker, name in tickers_dict.items():
        if count >= max_count:
            break
        try:
            reports = crawl_naver_reports(ticker, name, existing_urls)
            for r in reports:
                if count >= max_count:
                    break
                # PDF 텍스트 추출
                full_text, status = extract_pdf_text(r["pdf_url"])
                r["full_text"] = full_text
                r["extraction_status"] = status
                r["collected_at"] = datetime.now(KST).isoformat()
                new_reports.append(r)
                existing_urls.add(r["pdf_url"])
                count += 1
                time.sleep(1)  # 크롤링 딜레이
        except Exception as e:
            print(f"[report] {name}({ticker}) 수집 오류: {e}")
            continue
        time.sleep(1)  # 종목간 딜레이

    if new_reports:
        data["reports"].extend(new_reports)
        data["last_collected"] = datetime.now(KST).isoformat()
        # 정리: 90일 초과 삭제
        cutoff = (datetime.now(KST) - timedelta(days=_RETAIN_DAYS)).strftime("%Y-%m-%d")
        data["reports"] = [r for r in data["reports"] if r.get("date", "") >= cutoff]
        # 종목별 최신 _MAX_PER_TICKER건만 유지
        by_ticker: dict[str, list] = {}
        for r in sorted(data["reports"], key=lambda x: x.get("date", ""), reverse=True):
            t = r.get("ticker", "")
            by_ticker.setdefault(t, []).append(r)
        kept = []
        for t, rs in by_ticker.items():
            kept.extend(rs[:_MAX_PER_TICKER])
        data["reports"] = sorted(kept, key=lambda x: x.get("date", ""), reverse=True)
        save_reports(data)

    return new_reports


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 수집 대상 티커 ━━━━━━━━━━━━━━━━━━━━━━━━━

def get_collection_tickers() -> dict:
    """watchlist + watchalert + stoploss + portfolio에서 한국 종목 티커→이름 딕셔너리 반환."""
    from kis_api import (load_watchlist, load_watchalert, load_stoploss,
                         load_json, PORTFOLIO_FILE, _is_us_ticker)
    tickers: dict[str, str] = {}
    # 1) watchlist.json {ticker: name}
    try:
        wl = load_watchlist()
        for t, n in wl.items():
            if not _is_us_ticker(t):
                tickers[t] = n
    except Exception:
        pass
    # 2) watchalert.json (매수감시) {ticker: {name, buy_price, ...}}
    try:
        wa = load_watchalert()
        for t, v in wa.items():
            if not _is_us_ticker(t) and t not in tickers:
                tickers[t] = v.get("name", t) if isinstance(v, dict) else t
    except Exception:
        pass
    # 3) stoploss.json (손절감시) {ticker: {name, stop_price, ...}}
    try:
        sl = load_stoploss()
        for t, v in sl.items():
            if t == "us_stocks" or not isinstance(v, dict):
                continue
            if not _is_us_ticker(t) and t not in tickers:
                tickers[t] = v.get("name", t)
    except Exception:
        pass
    # 4) portfolio.json {ticker: {name, qty, avg_price}}
    try:
        pf = load_json(PORTFOLIO_FILE, {})
        for t, v in pf.items():
            if t in ("us_stocks", "cash_krw", "cash_usd") or not isinstance(v, dict):
                continue
            if not _is_us_ticker(t) and t not in tickers:
                tickers[t] = v.get("name", t)
    except Exception:
        pass
    return tickers
