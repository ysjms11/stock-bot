"""증권사 리포트 자동 수집 — 네이버증권 리서치 크롤링 + PDF 텍스트 추출"""
import os
import sqlite3
import time
import tempfile
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

KST = ZoneInfo("Asia/Seoul")
REPORTS_FILE = os.environ.get("DATA_DIR", "/data") + "/reports.json"
DB_PATH = os.environ.get("DATA_DIR", "/data") + "/stock.db"
_PDF_DIR = os.environ.get("DATA_DIR", "/data") + "/report_pdfs"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/research/company_list.naver",
}
_MAX_TEXT = 10000      # PDF 텍스트 최대 글자수
_MAX_PDF_BYTES = 50 * 1024 * 1024  # PDF 최대 50MB
_RETAIN_DAYS = 90      # 레거시 (현재 미사용, 영구 보관)
_MAX_PER_TICKER = 10   # 레거시 (현재 미사용, 영구 보관)
_ALLOWED_PDF_DOMAINS = {"ssl.pstatic.net", "finance.naver.com", "stock.pstatic.net",
                        "consensus.hankyung.com"}

# ━━━━━━━━━━━━━━━━━━━━━━━━━ DB 연결 & 파일 저장/로드 ━━━━━━━━━━━━━━━━━━━━━━━━━

def _get_report_db() -> sqlite3.Connection:
    """SQLite 연결 반환. reports 테이블 및 인덱스 자동 생성."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT DEFAULT '',
            source TEXT DEFAULT '',
            analyst TEXT DEFAULT '',
            title TEXT DEFAULT '',
            pdf_url TEXT DEFAULT '',
            full_text TEXT DEFAULT '',
            pdf_path TEXT DEFAULT '',
            target_price INTEGER DEFAULT 0,
            opinion TEXT DEFAULT '',
            extraction_status TEXT DEFAULT '',
            collected_at TEXT DEFAULT '',
            UNIQUE(date, source, ticker)
        );

        CREATE INDEX IF NOT EXISTS idx_rpt_ticker ON reports(ticker);
        CREATE INDEX IF NOT EXISTS idx_rpt_date ON reports(date);
    """)
    # 기존 DB 마이그레이션: 컬럼 없으면 추가 (오류 무시)
    try:
        conn.execute("ALTER TABLE reports ADD COLUMN pdf_path TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # 이미 컬럼 존재 시 무시
    try:
        conn.execute("ALTER TABLE reports ADD COLUMN target_price INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE reports ADD COLUMN opinion TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE reports ADD COLUMN category TEXT DEFAULT 'company'")
        conn.commit()
        conn.execute("UPDATE reports SET category='company' WHERE category IS NULL OR category=''")
        conn.commit()
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rpt_category ON reports(category)")
        conn.commit()
    except Exception:
        pass
    return conn


def load_reports() -> dict:
    """SQLite에서 리포트 로드. 기존 dict 포맷 호환."""
    try:
        conn = _get_report_db()
        rows = conn.execute("SELECT * FROM reports ORDER BY date DESC").fetchall()
        conn.close()
        return {"reports": [dict(r) for r in rows], "last_collected": ""}
    except Exception:
        return {"reports": [], "last_collected": ""}


def save_reports(data: dict):
    """No-op: 저장은 collect_reports()에서 SQLite INSERT로 처리."""
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 목표가/투자의견 추출 ━━━━━━━━━━━━━━━━━━━━━━━━━

def extract_report_meta(full_text: str) -> dict:
    """리포트 텍스트에서 목표가/투자의견 추출.

    Returns: {"target_price": int or None, "opinion": str or ""}
    """
    import re
    target = None
    opinion = ""

    if not full_text:
        return {"target_price": target, "opinion": opinion}

    # 목표주가 추출 패턴들 (괄호 안 유지/상향/12M 등 허용)
    patterns = [
        r'목표주가\s*(?:\([^)]*\))?\s*([0-9,]+)\s*원',
        r'목표가\s*(?:\([^)]*\))?\s*([0-9,]+)\s*원',
        r'Target\s*Price\s*(?:\([^)]*\))?\s*([0-9,]+)',
        r'TP\s*(?:\([^)]*\))?\s*([0-9,]+)\s*원',
    ]
    for p in patterns:
        m = re.search(p, full_text, re.IGNORECASE)
        if m:
            try:
                val = int(m.group(1).replace(",", ""))
                if val >= 1000:  # 최소 1,000원 이상만 (연도 오탐 방지)
                    target = val
                    break
            except Exception:
                pass

    # 투자의견 추출
    opinion_patterns = [
        r'투자의견[:\s]*(매수|BUY|Buy|중립|HOLD|Hold|매도|SELL|Sell|Overweight|Underweight|시장수익률상회|시장수익률)',
        r'(BUY|HOLD|SELL|매수|중립|매도)\s*(?:유지|상향|하향|신규)',
    ]
    for p in opinion_patterns:
        m = re.search(p, full_text, re.IGNORECASE)
        if m:
            raw = m.group(1).upper()
            if raw in ("매수", "BUY", "OVERWEIGHT", "시장수익률상회"):
                opinion = "매수"
            elif raw in ("중립", "HOLD", "시장수익률"):
                opinion = "중립"
            elif raw in ("매도", "SELL", "UNDERWEIGHT"):
                opinion = "매도"
            else:
                opinion = m.group(1)
            break

    return {"target_price": target, "opinion": opinion}


def _normalize_wise_opinion(recomm: str) -> str:
    """와이즈리포트 RECOMM 값을 매수/중립/매도로 정규화."""
    if not recomm:
        return ""
    r = recomm.strip().upper()
    if r in ("BUY", "매수", "STRONG BUY", "STRONGBUY", "OVERWEIGHT", "시장수익률상회"):
        return "매수"
    if r in ("HOLD", "NEUTRAL", "MARKET PERFORM", "중립", "시장수익률"):
        return "중립"
    if r in ("SELL", "UNDERWEIGHT", "UNDERPERFORM", "매도"):
        return "매도"
    return recomm.strip()


def backfill_report_meta() -> int:
    """기존 DB 리포트의 full_text에서 목표가/투자의견을 소급 추출하여 UPDATE.

    target_price=0 AND opinion='' 인 행만 처리 (이미 채워진 것은 건드리지 않음).
    Returns: 업데이트된 행 수.
    """
    conn = _get_report_db()
    rows = conn.execute(
        "SELECT id, full_text, extraction_status FROM reports "
        "WHERE (target_price IS NULL OR target_price = 0) AND (opinion IS NULL OR opinion = '')"
    ).fetchall()
    updated = 0
    for row in rows:
        status = row["extraction_status"] or ""
        full_text = row["full_text"] or ""
        # 와이즈 메타는 full_text에 목표가가 없으니 스킵
        if status == "meta_only":
            continue
        if not full_text:
            continue
        meta = extract_report_meta(full_text)
        tp = meta["target_price"] or 0
        op = meta["opinion"]
        if tp or op:
            conn.execute(
                "UPDATE reports SET target_price=?, opinion=? WHERE id=?",
                (tp, op, row["id"]),
            )
            updated += 1
    conn.commit()
    conn.close()
    print(f"[report] backfill_report_meta: {updated}건 업데이트")
    return updated


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
            "analyst": "",
            "title": title,
            "pdf_url": pdf_url,
        })

    return reports


def crawl_wisereport_meta(ticker: str, name: str, existing_urls: set) -> list:
    """와이즈리포트 JSON API에서 종목 리포트 메타데이터 수집 (PDF 본문 없음).

    URL: https://comp.wisereport.co.kr/company/ajax/c1080001_data.aspx?cmp_cd={ticker}
    응답: {"lists": [{RPT_ID, ANL_DT, RPT_TITLE, BRK_NM_KOR, ANL_NM_KOR,
                     TARGET_PRC, RECOMM, COMMENT2, PRC_ACTION_TYP_NM, EPS_ACTION_TYP_NM, ...}]}

    PDF는 유료라 다운로드 불가. 메타데이터(목표가, 투자의견, 요약, 변동) 만 reports.json에 저장.
    pdf_url은 LoadReport URL 형태로 보존 (참고용).
    extraction_status="meta_only"로 표시.
    """
    url = "https://comp.wisereport.co.kr/company/ajax/c1080001_data.aspx"
    headers = dict(_HEADERS)
    headers["Referer"] = f"https://comp.wisereport.co.kr/company/c1080001.aspx?cmp_cd={ticker}"
    headers["X-Requested-With"] = "XMLHttpRequest"
    try:
        # perPage=100으로 전체 가져오기 (기본 20건 → 최대 100건)
        resp = requests.get(url, headers=headers,
                            params={"cmp_cd": ticker, "perPage": "100"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[wise] 요청 실패 ({ticker}): {e}")
        return []

    items = data.get("lists", [])
    if items:
        first_dt = items[0].get("ANL_DT", "?")
        last_dt = items[-1].get("ANL_DT", "?")
        total = data.get("tc", len(items))
        print(f"[wise] {ticker}: {len(items)}건 수신 ({first_dt} ~ {last_dt}, 전체 {total}건)")
    reports = []
    for item in items:
        # 날짜: "26/04/09" → "2026-04-09"
        anl_dt = item.get("ANL_DT", "").strip()
        if not anl_dt:
            continue
        date_str = _parse_date(anl_dt.replace("/", "."))

        rpt_id = item.get("RPT_ID")
        brk_cd = item.get("BRK_CD")
        file_nm = item.get("FILE_NM", "")

        # pdf_url: LoadReport (실제 다운은 유료라 실패함, 식별자 용도)
        if rpt_id and brk_cd and file_nm:
            pdf_url = f"https://www.wisereport.co.kr/comm/LoadReport.aspx?rpt_id={rpt_id}&brk_cd={brk_cd}&fpath={file_nm}&target=comp"
        else:
            pdf_url = f"https://comp.wisereport.co.kr/company/c1080001.aspx?cmp_cd={ticker}#rpt_{rpt_id}"

        if pdf_url in existing_urls:
            continue

        # COMMENT2 (요약 ▶ 불릿) - HTML 태그 제거
        comment2 = item.get("COMMENT2", "") or ""
        # 간단한 태그 제거
        import re as _re
        summary = _re.sub(r"<[^>]+>", " ", comment2).replace("&nbsp;", " ")
        summary = _re.sub(r"\s+", " ", summary).strip()

        title = item.get("RPT_TITLE", "").strip()
        broker = item.get("BRK_NM_KOR", "").strip()
        analyst = item.get("ANL_NM_KOR", "").strip()
        target_prc = item.get("TARGET_PRC", "").strip()
        recomm = (item.get("RECOMM") or "").strip() if item.get("RECOMM") else ""

        reports.append({
            "date": date_str,
            "ticker": ticker,
            "name": name,
            "source": broker,
            "title": title,
            "pdf_url": pdf_url,
            # 와이즈리포트 메타데이터 추가 필드
            "_wise_meta": True,
            "analyst": analyst,
            "target_price": target_prc,
            "recommendation": recomm,
            "summary": summary,
            "target_action": item.get("PRC_ACTION_TYP_NM", ""),
            "eps_action": item.get("EPS_ACTION_TYP_NM", ""),
            "recomm_action": item.get("RECOMM_ACTION_TYP_NM", ""),
        })

    return reports


def crawl_hankyung_reports(ticker: str, name: str, existing_urls: set) -> list:
    """한경컨센서스에서 종목 리포트 목록 크롤링.

    URL: https://consensus.hankyung.com/analysis/list?sdate=...&edate=...&search_text={ticker}&report_type=CO

    테이블 구조 (9 td):
      [0] 작성일 YYYY-MM-DD | [1] 제목(a href) | [2] 적정가격 | [3] 투자의견
      [4] 작성자 | [5] 제공출처 | [6] 기업정보 | [7] 차트 | [8] 첨부파일(a)

    Returns: [{"date", "ticker", "name", "source", "title", "pdf_url"}, ...]
    """
    today = datetime.now(KST).strftime("%Y-%m-%d")
    sdate = (datetime.now(KST) - timedelta(days=180)).strftime("%Y-%m-%d")
    url = "https://consensus.hankyung.com/analysis/list"
    params = {
        "sdate": sdate, "edate": today, "now_page": "1",
        "search_text": ticker, "pagenum": "20", "report_type": "CO",
    }
    try:
        resp = requests.get(url, headers=_HEADERS, params=params, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[hankyung] 요청 실패 ({ticker}): {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    reports = []
    for row in table.find_all("tr")[1:]:  # 헤더 제외
        tds = row.find_all("td")
        if len(tds) < 9:
            continue

        date_str = tds[0].get_text(strip=True)
        if not date_str or "결과가 없습니다" in date_str:
            continue

        title_a = tds[1].find("a")
        title_full = title_a.get_text(strip=True) if title_a else tds[1].get_text(strip=True)
        # "HD한국조선해양(009540) ..." → 종목코드 부분 제거하고 제목만
        title = title_full
        if "(" in title and ")" in title:
            # 종목코드와 종목명 부분 제거
            try:
                close_idx = title.index(")")
                title = title[close_idx + 1:].strip()
            except Exception:
                pass

        # PDF URL: tds[1] 또는 tds[8]에서 추출
        pdf_a = tds[8].find("a") or title_a
        if not pdf_a:
            continue
        pdf_href = pdf_a.get("href", "").strip()
        if not pdf_href or "downpdf" not in pdf_href:
            continue
        pdf_url = "https://consensus.hankyung.com" + pdf_href if pdf_href.startswith("/") else pdf_href

        if pdf_url in existing_urls:
            continue

        source = tds[5].get_text(strip=True)  # 제공출처
        analyst = tds[4].get_text(strip=True)  # 작성자

        reports.append({
            "date": date_str,
            "ticker": ticker,
            "name": name,
            "source": source,
            "analyst": analyst,
            "title": title,
            "pdf_url": pdf_url,
        })

    return reports


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 비종목 리포트 listing 크롤러 ━━━━━━━━━━━━━━━━━━━━━━━━━

_NAVER_LISTING_URLS = {
    "industry": "https://finance.naver.com/research/industry_list.naver",
    "market":   "https://finance.naver.com/research/market_info_list.naver",
    "strategy": "https://finance.naver.com/research/invest_list.naver",
    "economy":  "https://finance.naver.com/research/economy_list.naver",
    "bond":     "https://finance.naver.com/research/debenture_list.naver",
}

# 노이즈 차단 룰 (수집 단계 SKIP) — 실측 기반 broker × 카테고리 × 제목 패턴
# 1주일치 정독 결과 발견된 데일리 시리즈만 차단 (정밀, 실수 적게)
_NOISE_RULES = [
    # 시장 데일리 모닝/마감 (모든 broker)
    {"title_keywords": ["morning brief", "wake up", "snapshot",
                         "Daily Bond Morning Brief", "안녕하세요 데일리에요",
                         "Morning Letter", "시황 데일리", "장마감",
                         "마감 시황", "DAILY MARKET VIEW", "아침에 슥",
                         "아침에 슼", "morning meeting", "start with ibks",
                         "ibks morning brief", "ibks daily"]},
    # 유진투자증권 산업 News Comment (단일종목 영문 데일리)
    {"source": "유진투자증권", "category": "industry",
     "title_keywords": ["News Comment"]},
    # 키움증권 시황/FICC 데일리 (전략 카테고리에 분류된 거)
    {"source": "키움증권", "category": "strategy",
     "title_pattern": r"^\d{2}/\d{2}[, ]"},  # "04/24, 미 증시" / "04/24 달러"
    # 대신증권 퀀틴전시 플랜 (이경민 매일 시장 코멘트)
    {"source": "대신증권", "category": "strategy",
     "title_keywords": ["퀀틴전시 플랜"]},
]


def _is_noise(title: str, source: str, category: str) -> bool:
    """수집 SKIP 여부. _NOISE_RULES 실측 기반 패턴 매칭."""
    import re as _re
    title_lower = title.lower()
    for rule in _NOISE_RULES:
        # broker 매칭 (있을 때만)
        if "source" in rule and rule["source"] != source:
            continue
        # category 매칭 (있을 때만)
        if "category" in rule and rule["category"] != category:
            continue
        # title_keywords (소문자 contains)
        if "title_keywords" in rule:
            if any(kw.lower() in title_lower for kw in rule["title_keywords"]):
                return True
        # title_pattern (정규식)
        if "title_pattern" in rule:
            if _re.search(rule["title_pattern"], title):
                return True
    return False

_HANKYUNG_REPORT_TYPES = {
    "industry": "IN",
    "market":   "MA",
    "strategy": "ST",
    "economy":  "EC",
}

_CATEGORY_TICKER_PREFIX = {
    "industry": "_IND_",
    "market":   "_MKT_",
    "strategy": "_STR_",
    "economy":  "_ECO_",
    "bond":     "_BND_",
}


def _category_ticker(category: str, pdf_url: str) -> str:
    """비종목 리포트의 합성 ticker. UNIQUE(date, source, ticker) 충돌 회피용."""
    import hashlib
    h = hashlib.sha1(pdf_url.encode("utf-8")).hexdigest()[:10]
    prefix = _CATEGORY_TICKER_PREFIX.get(category, "_OTH_")
    return f"{prefix}{h}"


def crawl_naver_listing(category: str, existing_urls: set) -> list:
    """네이버 리서치 비종목 카테고리 listing 크롤링.

    category: 'industry' | 'market' | 'strategy' | 'economy' | 'bond'
    페이지 컬럼 수가 카테고리마다 다름 (industry=6, others=5). PDF 링크는 끝에서 3번째 td.
    PDF 없는 행(HTML view only)은 스킵.
    """
    url = _NAVER_LISTING_URLS.get(category)
    if not url:
        return []
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        # 네이버 리서치는 EUC-KR
        resp.encoding = "euc-kr"
    except Exception as e:
        print(f"[naver-{category}] 요청 실패: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="type_1")
    if not table:
        return []

    reports = []
    for row in table.find_all("tr"):
        tds = row.find_all("td")
        # 데이터 행은 5 또는 6 컬럼
        if len(tds) not in (5, 6):
            continue

        # 컬럼 매핑
        # 6 cols: 분류 | 제목 | 증권사 | 첨부 | 작성일 | 조회수
        # 5 cols: 제목 | 증권사 | 첨부 | 작성일 | 조회수
        if len(tds) == 6:
            sector = tds[0].get_text(strip=True)
            title_td, source_td, file_td, date_td = tds[1], tds[2], tds[3], tds[4]
        else:
            sector = ""
            title_td, source_td, file_td, date_td = tds[0], tds[1], tds[2], tds[3]

        # PDF URL (끝에서 3번째 td 안의 a)
        pdf_link = file_td.find("a")
        if not pdf_link:
            continue
        pdf_url = (pdf_link.get("href") or "").strip()
        if not pdf_url or ".pdf" not in pdf_url.lower():
            continue
        if pdf_url.startswith("//"):
            pdf_url = "https:" + pdf_url
        elif pdf_url.startswith("/"):
            pdf_url = "https://finance.naver.com" + pdf_url
        if pdf_url in existing_urls:
            continue

        title_a = title_td.find("a")
        title = (title_a.get_text(strip=True) if title_a
                 else title_td.get_text(strip=True))
        source = source_td.get_text(strip=True)
        date_str = _parse_date(date_td.get_text(strip=True))

        # 노이즈 차단 (실측 기반 broker × 패턴)
        if _is_noise(title, source, category):
            continue

        reports.append({
            "date": date_str,
            "ticker": _category_ticker(category, pdf_url),
            "name": sector,  # 산업분석은 섹터 분류 들어감, 나머지는 ""
            "source": source,
            "analyst": "",
            "title": title,
            "pdf_url": pdf_url,
            "category": category,
        })

    return reports


def crawl_hankyung_listing(category: str, existing_urls: set) -> list:
    """한경컨센 비종목 카테고리 listing 크롤링 (180일 윈도우).

    category: 'industry' | 'market' | 'strategy' | 'economy'

    비종목 7-col 구조: [0]날짜 [1]제목+PDF [2]적정가(-) [3]작성자 [4]출처
                       [5]차트 [6]첨부+PDF
    종목분석(CO)은 9-col이라 다른 함수(crawl_hankyung_reports) 사용.
    """
    rtype = _HANKYUNG_REPORT_TYPES.get(category)
    if not rtype:
        return []

    today = datetime.now(KST).strftime("%Y-%m-%d")
    sdate = (datetime.now(KST) - timedelta(days=180)).strftime("%Y-%m-%d")
    url = "https://consensus.hankyung.com/analysis/list"

    # 페이지네이션 (최대 5페이지 = 500건). cap 도달 broker 분류용.
    all_rows = []
    for page in range(1, 6):
        params = {
            "sdate": sdate, "edate": today, "now_page": str(page),
            "search_text": "", "pagenum": "100", "report_type": rtype,
        }
        try:
            resp = requests.get(url, headers=_HEADERS, params=params, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            print(f"[hankyung-{category}] page {page} 요청 실패: {e}")
            break

        page_soup = BeautifulSoup(resp.text, "html.parser")
        page_table = page_soup.find("table")
        if not page_table:
            break

        page_data_rows = [tr for tr in page_table.find_all("tr")[1:]
                          if tr.find_all("td")]
        # 빈 페이지 (결과 없음)면 종료
        if not page_data_rows:
            break
        # "결과가 없습니다" 행만 있으면 종료
        first_text = page_data_rows[0].get_text(strip=True) if page_data_rows else ""
        if "결과가 없습니다" in first_text:
            break

        all_rows.extend(page_data_rows)
        # 100건 미만이면 마지막 페이지
        if len(page_data_rows) < 100:
            break
        time.sleep(0.5)  # 페이지 간 짧은 휴식

    # 첫 페이지가 비었으면 빈 리스트 반환
    if not all_rows:
        return []

    # 호환을 위한 더미 table-like
    class _DummyTable:
        def __init__(self, rows): self._rows = rows
        def find_all(self, name):
            if name == "tr":
                # [1:] 슬라이스를 위해 첫 row를 헤더 더미로 추가
                return [None] + self._rows
            return []
    table = _DummyTable(all_rows)

    # 한경컨센은 카테고리에 따라 컬럼 구조 다름:
    #   IN (7col): 날짜|제목|적정가|작성자|broker|차트|첨부
    #   MA (6col): 날짜|제목|작성자|broker|차트|첨부 (라벨 X)
    #   EC (6col): 날짜|"경제"라벨|제목|작성자|broker|첨부 (라벨 O)
    #   ST (5col): 날짜|제목|작성자|broker|첨부 (드물게 발간)
    # 차이: td[1]에 라벨("기업"/"산업"/"경제"/"시황"/"투자"/"채권")이 있으면 +1 시프트
    CATEGORY_LABELS = {"기업", "산업", "경제", "시황", "투자", "채권"}
    reports = []
    for row in table.find_all("tr")[1:]:
        tds = row.find_all("td")
        n = len(tds)

        date_str = tds[0].get_text(strip=True) if tds else ""
        if not date_str or "결과가 없습니다" in date_str:
            continue

        # td[1]이 짧은 카테고리 라벨이면 시프트
        label_text = tds[1].get_text(strip=True) if len(tds) > 1 else ""
        has_label = (label_text in CATEGORY_LABELS and not tds[1].find("a"))
        title_idx = 2 if has_label else 1

        # 컬럼 매핑 (n + has_label 조합)
        if n == 7 and not has_label:
            # IN: 날짜|제목|적정가|작성자|broker|차트|첨부
            analyst_idx, source_idx, pdf_idx = 3, 4, 6
        elif n == 6 and has_label:
            # EC: 날짜|라벨|제목|작성자|broker|첨부
            analyst_idx, source_idx, pdf_idx = 3, 4, 5
        elif n == 6 and not has_label:
            # MA: 날짜|제목|작성자|broker|차트|첨부
            analyst_idx, source_idx, pdf_idx = 2, 3, 5
        elif n == 5 and not has_label:
            # ST 등: 날짜|제목|작성자|broker|첨부
            analyst_idx, source_idx, pdf_idx = 2, 3, 4
        else:
            continue

        title_a = tds[title_idx].find("a")
        title = (title_a.get_text(strip=True) if title_a
                 else tds[title_idx].get_text(strip=True))

        # PDF: 첨부 col 우선, 없으면 제목 링크
        pdf_a = tds[pdf_idx].find("a") or title_a
        if not pdf_a:
            continue
        pdf_href = (pdf_a.get("href") or "").strip()
        if not pdf_href or "downpdf" not in pdf_href:
            continue
        pdf_url = ("https://consensus.hankyung.com" + pdf_href
                   if pdf_href.startswith("/") else pdf_href)
        if pdf_url in existing_urls:
            continue

        analyst = tds[analyst_idx].get_text(strip=True) if analyst_idx < n else ""
        source = tds[source_idx].get_text(strip=True) if source_idx < n else ""

        # 노이즈 차단 (실측 기반 broker × 패턴)
        if _is_noise(title, source, category):
            continue

        reports.append({
            "date": date_str,
            "ticker": _category_ticker(category, pdf_url),
            "name": "",
            "source": source,
            "analyst": analyst,
            "title": title,
            "pdf_url": pdf_url,
            "category": category,
        })

    return reports


def collect_market_reports(categories: list = None) -> list:
    """비종목 리포트 수집. 네이버 + 한경컨센 통합.

    categories: ['industry','market','strategy','economy','bond']. None=기본 4개.
    Returns: 새로 수집된 리포트 리스트.
    """
    if categories is None:
        categories = ["industry", "market", "strategy", "economy"]

    _conn = _get_report_db()
    rows = _conn.execute("SELECT pdf_url, date, source, ticker FROM reports").fetchall()
    _conn.close()
    existing_urls = {r["pdf_url"] for r in rows if r["pdf_url"]}
    existing_keys = {(r["date"], r["source"], r["ticker"]) for r in rows}

    new_reports = []
    for cat in categories:
        merged = []
        try:
            merged.extend(crawl_naver_listing(cat, existing_urls))
        except Exception as e:
            print(f"[market-{cat}] naver 실패: {e}")
        if cat in _HANKYUNG_REPORT_TYPES:
            try:
                merged.extend(crawl_hankyung_listing(cat, existing_urls))
            except Exception as e:
                print(f"[market-{cat}] hankyung 실패: {e}")

        # 같은 카테고리 내 중복 제거 (pdf_url 기준)
        seen_urls = set()
        unique = []
        for r in merged:
            if r["pdf_url"] in seen_urls:
                continue
            seen_urls.add(r["pdf_url"])
            unique.append(r)
        unique.sort(key=lambda x: x.get("date", ""), reverse=True)

        for r in unique:
            key = (r.get("date", ""), r.get("source", ""), r.get("ticker", ""))
            if key in existing_keys:
                continue
            try:
                full_text, status, pdf_bytes = extract_pdf_text(r["pdf_url"])
                r["full_text"] = full_text
                r["extraction_status"] = status
                r["pdf_path"] = ""
                if pdf_bytes:
                    try:
                        r["pdf_path"] = _save_pdf_local(
                            pdf_bytes, r["ticker"], r.get("date", ""),
                            r.get("source", ""), r.get("analyst", ""),
                        )
                    except Exception as e:
                        print(f"[market-{cat}] PDF 저장 실패: {e}")
                meta = extract_report_meta(full_text)
                r["target_price"] = meta["target_price"] or 0
                r["opinion"] = meta["opinion"]
            except Exception as e:
                print(f"[market-{cat}] 추출 실패 ({r.get('title','')[:30]}): {e}")
                r["full_text"] = ""
                r["extraction_status"] = "failed"
                r["pdf_path"] = ""
                r["target_price"] = 0
                r["opinion"] = ""

            r["collected_at"] = datetime.now(KST).isoformat()
            new_reports.append(r)
            existing_urls.add(r["pdf_url"])
            existing_keys.add(key)
            time.sleep(1)

    if new_reports:
        conn = _get_report_db()
        for r in new_reports:
            conn.execute(
                """INSERT OR IGNORE INTO reports
                   (date, ticker, name, source, analyst, title, pdf_url,
                    full_text, pdf_path, extraction_status, collected_at,
                    target_price, opinion, category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (r.get("date", ""), r.get("ticker", ""), r.get("name", ""),
                 r.get("source", ""), r.get("analyst", ""), r.get("title", ""),
                 r.get("pdf_url", ""), r.get("full_text", ""),
                 r.get("pdf_path", ""), r.get("extraction_status", ""),
                 r.get("collected_at", ""),
                 r.get("target_price", 0), r.get("opinion", ""),
                 r.get("category", "")),
            )
        conn.commit()
        conn.close()

    return new_reports


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

def _save_pdf_local(pdf_bytes: bytes, ticker: str, date: str, source: str, analyst: str) -> str:
    """PDF를 로컬에 저장. 반환: 저장 경로."""
    import re

    # ── path traversal 방지 ─────────────────────────────────────────────
    # 1) 원본 값에서 '..' 포함 여부를 먼저 차단 (악의적 입력 명시적 거부)
    for val, name in ((ticker, "ticker"), (date, "date"), (source, "source"), (analyst, "analyst")):
        if ".." in val:
            raise ValueError(f"_save_pdf_local: invalid {name} value: {val!r}")

    # 2) basename만 남기기 (슬래시/백슬래시 계열 절대경로·상대경로 제거)
    ticker = os.path.basename(ticker)
    date = os.path.basename(date)
    # source/analyst는 아래 re.sub으로 충분하지만 basename 적용도 추가
    source = os.path.basename(source)
    analyst = os.path.basename(analyst)

    # 3) ticker는 영숫자·한글·하이픈·언더스코어만 허용 (안전한 디렉토리명)
    ticker = re.sub(r'[^\w가-힣\-]', '', ticker)
    if not ticker:
        raise ValueError("_save_pdf_local: ticker is empty after sanitization")

    dir_path = os.path.join(_PDF_DIR, ticker)
    os.makedirs(dir_path, exist_ok=True)

    # 4) realpath로 _PDF_DIR 하위인지 최종 확인
    real_base = os.path.realpath(_PDF_DIR)
    real_dir = os.path.realpath(dir_path)
    if not real_dir.startswith(real_base + os.sep) and real_dir != real_base:
        raise ValueError(f"_save_pdf_local: dir_path escapes _PDF_DIR: {real_dir!r}")

    # ── 파일명 생성 ─────────────────────────────────────────────────────
    # 파일명: 2026-04-09_유안타증권_백길현.pdf (특수문자 제거)
    safe_date = re.sub(r'[^\d\-]', '', date)
    safe_source = re.sub(r'[^\w가-힣]', '', source)
    safe_analyst = re.sub(r'[^\w가-힣,]', '', analyst)
    if safe_analyst:
        fname = f"{safe_date}_{safe_source}_{safe_analyst}.pdf"
    else:
        fname = f"{safe_date}_{safe_source}.pdf"

    fpath = os.path.join(dir_path, fname)

    # 5) 최종 파일 경로도 _PDF_DIR 하위인지 realpath 체크
    real_fpath = os.path.realpath(fpath)
    if not real_fpath.startswith(real_base + os.sep):
        raise ValueError(f"_save_pdf_local: fpath escapes _PDF_DIR: {real_fpath!r}")

    with open(fpath, 'wb') as f:
        f.write(pdf_bytes)
    return fpath


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


def extract_pdf_text(pdf_url: str) -> tuple[str, str, bytes]:
    """PDF 다운로드 후 pdfplumber로 텍스트 추출.
    Returns: (text, status, pdf_bytes)
      - status: 'success'|'failed'|'partial'
      - pdf_bytes: 다운로드된 PDF 바이트 (실패 시 빈 bytes)
    최대 _MAX_TEXT(10000)자. 임시 파일은 처리 후 삭제.
    """
    try:
        import pdfplumber
    except ImportError:
        print("[report] pdfplumber 미설치")
        return ("", "failed", b"")

    # URL 도메인 검증 — 허용된 네이버 도메인만 다운로드
    try:
        host = urlparse(pdf_url).hostname or ""
        if host not in _ALLOWED_PDF_DOMAINS:
            print(f"[report] 허용되지 않은 PDF 도메인: {host}")
            return ("", "failed", b"")
    except Exception:
        return ("", "failed", b"")

    tmp_path = None
    pdf_bytes = b""
    try:
        resp = requests.get(pdf_url, headers=_HEADERS, timeout=30, stream=True)
        if resp.status_code != 200:
            return ("", "failed", b"")

        # Content-Length 사전 검사
        content_len = resp.headers.get("Content-Length")
        if content_len and int(content_len) > _MAX_PDF_BYTES:
            print(f"[report] PDF 크기 초과 ({int(content_len)//1024//1024}MB): {pdf_url}")
            resp.close()
            return ("", "failed", b"")

        downloaded = 0
        chunks = []
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
            for chunk in resp.iter_content(8192):
                downloaded += len(chunk)
                if downloaded > _MAX_PDF_BYTES:
                    print(f"[report] PDF 다운로드 크기 초과 ({downloaded//1024//1024}MB): {pdf_url}")
                    break
                chunks.append(chunk)
                tmp.write(chunk)

        if downloaded > _MAX_PDF_BYTES:
            return ("", "failed", b"")

        pdf_bytes = b"".join(chunks)

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
            return ("", "failed", pdf_bytes)

        if _is_chart_image_text(text):
            return ("[PDF 텍스트 추출 실패 - 차트/이미지 PDF]", "failed", pdf_bytes)

        if not _validate_korean_text(text):
            return ("[PDF 텍스트 추출 실패 - 이미지 기반 PDF]", "failed", pdf_bytes)

        if truncated:
            return (text, "partial", pdf_bytes)

        return (text, "success", pdf_bytes)
    except Exception as e:
        print(f"[report] PDF 추출 실패 ({pdf_url}): {e}")
        return ("", "failed", b"")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 수집 메인 ━━━━━━━━━━━━━━━━━━━━━━━━━

def collect_reports(tickers_dict: dict) -> list:
    """종목 딕셔너리({ticker: name})에 대해 리포트 수집.
    Returns: 새로 수집된 리포트 리스트.
    """
    # SQLite에서 기존 URL/키 로드 (중복 방지)
    _conn = _get_report_db()
    rows = _conn.execute("SELECT pdf_url, date, source, ticker FROM reports").fetchall()
    _conn.close()
    existing_urls = {r["pdf_url"] for r in rows if r["pdf_url"]}
    existing_keys = {(r["date"], r["source"], r["ticker"]) for r in rows}

    new_reports = []

    for ticker, name in tickers_dict.items():
        try:
            # 한경 + 네이버 + 와이즈 통합 (우선순위: 한경 → 네이버 → 와이즈)
            # PDF 본문이 있는 한경/네이버를 우선, 와이즈는 메타데이터로 보강
            reports = []
            try:
                reports.extend(crawl_hankyung_reports(ticker, name, existing_urls))
            except Exception as e:
                print(f"[hankyung] {name}({ticker}) 실패: {e}")
            reports.extend(crawl_naver_reports(ticker, name, existing_urls))
            try:
                reports.extend(crawl_wisereport_meta(ticker, name, existing_urls))
            except Exception as e:
                print(f"[wise] {name}({ticker}) 실패: {e}")

            # 같은 배치 내 중복 제거 (date+source+ticker 기준, 먼저 들어온 것 우선)
            seen_keys: set[tuple] = set()
            seen_urls: set[str] = set()
            unique_reports = []
            for r in reports:
                k = (r.get("date", ""), r.get("source", ""), r.get("ticker", ""))
                if k in seen_keys or r["pdf_url"] in seen_urls:
                    continue
                seen_keys.add(k)
                seen_urls.add(r["pdf_url"])
                unique_reports.append(r)
            # 최신순 정렬
            unique_reports.sort(key=lambda x: x.get("date", ""), reverse=True)
            for r in unique_reports:
                # 복합키 중복 체크 (date+source+ticker)
                key = (r.get("date", ""), r.get("source", ""), r.get("ticker", ""))
                if key in existing_keys:
                    continue
                # 와이즈 메타데이터는 PDF 추출 스킵
                if r.get("_wise_meta"):
                    r["full_text"] = "[와이즈리포트 메타데이터만 — PDF는 유료]"
                    r["extraction_status"] = "meta_only"
                    r["pdf_path"] = ""
                    r.pop("_wise_meta", None)
                    # 와이즈 메타에서 직접 제공되는 목표가/투자의견 사용
                    try:
                        tp_raw = r.pop("target_price", "") or ""
                        r["target_price"] = int(str(tp_raw).replace(",", "")) if tp_raw else 0
                    except (ValueError, TypeError):
                        r["target_price"] = 0
                    r["opinion"] = _normalize_wise_opinion(r.pop("recommendation", "") or "")
                else:
                    full_text, status, pdf_bytes = extract_pdf_text(r["pdf_url"])
                    r["full_text"] = full_text
                    r["extraction_status"] = status
                    r["pdf_path"] = ""
                    if pdf_bytes:
                        try:
                            r["pdf_path"] = _save_pdf_local(
                                pdf_bytes, ticker,
                                r.get("date", ""), r.get("source", ""), r.get("analyst", ""),
                            )
                        except Exception as e:
                            print(f"[report] PDF 로컬 저장 실패 ({ticker}): {e}")
                    # PDF full_text에서 목표가/투자의견 추출
                    meta = extract_report_meta(full_text)
                    r["target_price"] = meta["target_price"] or 0
                    r["opinion"] = meta["opinion"]
                r["collected_at"] = datetime.now(KST).isoformat()
                new_reports.append(r)
                existing_urls.add(r["pdf_url"])
                existing_keys.add(key)
                time.sleep(1)  # 크롤링 딜레이
        except Exception as e:
            print(f"[report] {name}({ticker}) 수집 오류: {e}")
            continue
        time.sleep(1)  # 종목간 딜레이

    if new_reports:
        # SQLite에 INSERT (UNIQUE 제약으로 중복 자동 스킵)
        conn = _get_report_db()
        for r in new_reports:
            conn.execute(
                """INSERT OR IGNORE INTO reports
                   (date, ticker, name, source, analyst, title, pdf_url,
                    full_text, pdf_path, extraction_status, collected_at,
                    target_price, opinion, category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (r.get("date", ""), r.get("ticker", ""), r.get("name", ""),
                 r.get("source", ""), r.get("analyst", ""), r.get("title", ""),
                 r.get("pdf_url", ""), r.get("full_text", ""),
                 r.get("pdf_path", ""), r.get("extraction_status", ""),
                 r.get("collected_at", ""),
                 r.get("target_price", 0), r.get("opinion", ""),
                 "company"),
            )
        # 영구 보관 (삭제 없음)
        conn.commit()
        conn.close()

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
