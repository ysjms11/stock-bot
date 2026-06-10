"""SEC EDGAR 1차 공시 통합.

Free REST API (https://data.sec.gov), rate limit 10 req/s.
User-Agent 명시는 SEC 룰 (https://www.sec.gov/os/accessing-edgar-data).

사용 함수:
    ticker_to_cik(ticker)           → CIK 문자열 (10자리 zfill) or None
    get_company_filings(...)        → 공시 목록 list[dict]
    bulk_fetch_cik_map(tickers)     → {ticker: cik} dict (배치 CIK 조회)
    ensure_cik_map_loaded()         → data/sec_cik_map.json 초기화/갱신
    upsert_sec_filings(filings)     → stock.db sec_filings 테이블 upsert
"""

import asyncio
import json
import logging
import ssl
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
import certifi

from ._config import _DATA_DIR, _DB_PATH

# certifi CA 번들을 명시한 SSL context (Python 3.12 macOS 내장 CA 없음 대응)
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 상수
# ━━━━━━━━━━━━━━━━━━━━━━━━━
SEC_BASE      = "https://data.sec.gov"
SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
USER_AGENT    = "stock-bot arcturusnd@gmail.com"   # SEC 룰: 연락처 포함

# CIK 캐시 파일 경로
_CIK_MAP_FILE = Path(_DATA_DIR) / "sec_cik_map.json"

# 폼 분류 (priority 기준: de-SPAC/IPO 관련이 최우선)
FILING_FORMS_CRITICAL = {"F-1", "F-1/A", "S-1", "S-1/A", "EFFECT",
                          "424B3", "424B4", "424B5", "424B1", "424B2"}
FILING_FORMS_WATCH    = {"8-K", "8-K/A", "6-K", "6-K/A", "SC 13D",
                          "SC 13G", "SC 13G/A", "SC 13D/A", "4"}
FILING_FORMS_DEFAULT  = FILING_FORMS_CRITICAL | FILING_FORMS_WATCH

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Rate limiter
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_last_call_ts: float = 0.0
_RATE_INTERVAL = 0.11  # ~9 req/s (여유 버퍼)


async def _sec_get(session: aiohttp.ClientSession, url: str, params: dict = None) -> dict | str:
    """SEC EDGAR GET 요청 + rate limit 준수."""
    global _last_call_ts
    elapsed = asyncio.get_running_loop().time() - _last_call_ts
    if elapsed < _RATE_INTERVAL:
        await asyncio.sleep(_RATE_INTERVAL - elapsed)
    _last_call_ts = asyncio.get_running_loop().time()

    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    try:
        async with session.get(url, headers=headers, params=params,
                               ssl=_SSL_CTX,
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            ct = r.headers.get("content-type", "")
            if r.status == 200:
                if "json" in ct:
                    return await r.json()
                return await r.text()
            logger.warning("SEC EDGAR %s → HTTP %s", url, r.status)
            return {}
    except Exception as exc:
        logger.error("SEC EDGAR request error %s: %s", url, exc)
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# CIK 매핑
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_cik_cache: dict[str, str] = {}   # 메모리 캐시 {upper_ticker: cik_10}


def _load_cik_file() -> dict:
    """파일에서 CIK 맵 로드. 없으면 {}."""
    try:
        if _CIK_MAP_FILE.exists():
            with open(_CIK_MAP_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cik_file(data: dict) -> None:
    try:
        with open(_CIK_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("sec_cik_map.json 저장 실패: %s", exc)


async def ensure_cik_map_loaded() -> dict:
    """SEC company_tickers.json 다운로드 → data/sec_cik_map.json 초기 구축.

    이미 50건 이상이면 파일 재사용.
    Returns: {TICKER: "CIK_10자리"} dict
    """
    global _cik_cache

    # 메모리 캐시 우선
    if len(_cik_cache) > 50:
        return _cik_cache

    # 파일 캐시 (24시간 내)
    existing = _load_cik_file()
    if existing.get("_meta"):
        fetched_at = existing.get("_meta", {}).get("fetched_at", "")
        if fetched_at:
            age_h = (datetime.utcnow() - datetime.fromisoformat(fetched_at)).total_seconds() / 3600
            if age_h < 24 and len(existing) > 50:
                _cik_cache = {k: v for k, v in existing.items() if not k.startswith("_")}
                return _cik_cache

    # SEC에서 전종목 ticker → CIK 다운로드
    logger.info("SEC company_tickers.json 다운로드 중...")
    async with aiohttp.ClientSession() as s:
        raw = await _sec_get(s, SEC_TICKER_URL)

    if not raw or not isinstance(raw, dict):
        logger.warning("company_tickers.json 다운로드 실패. 기존 파일 사용")
        _cik_cache = {k: v for k, v in existing.items() if not k.startswith("_")}
        return _cik_cache

    # 파싱: {"0": {"cik_str": 789019, "ticker": "MSFT", "title": "..."}, ...}
    new_map: dict[str, str] = {}
    for entry in raw.values():
        t = str(entry.get("ticker", "")).upper().strip()
        c = str(entry.get("cik_str", "")).strip()
        if t and c:
            new_map[t] = c.zfill(10)

    # 저장
    to_save = dict(new_map)
    to_save["_meta"] = {"fetched_at": datetime.utcnow().isoformat(), "count": len(new_map)}
    _save_cik_file(to_save)
    _cik_cache = new_map
    logger.info("CIK 맵 구축 완료: %d 종목", len(new_map))
    return new_map


async def ticker_to_cik(ticker: str) -> str | None:
    """Ticker → CIK (10자리 문자열). 없으면 None."""
    t = ticker.upper().strip()
    # 메모리 캐시
    if t in _cik_cache:
        return _cik_cache[t]
    # 파일 캐시
    file_data = _load_cik_file()
    if t in file_data:
        _cik_cache[t] = file_data[t]
        return file_data[t]
    # 전체 맵 로드 후 재시도
    await ensure_cik_map_loaded()
    return _cik_cache.get(t)


async def bulk_fetch_cik_map(tickers: list[str]) -> dict[str, str]:
    """여러 티커의 CIK를 한 번에 조회. {ticker: cik} 반환 (없는 것 제외)."""
    await ensure_cik_map_loaded()
    result = {}
    for t in tickers:
        cik = _cik_cache.get(t.upper().strip())
        if cik:
            result[t.upper()] = cik
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# EDGAR 공시 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def get_company_filings(
    cik: str,
    types: set[str] | list[str] | None = None,
    days: int = 30,
) -> list[dict]:
    """CIK 기준 최근 공시 목록 조회.

    Args:
        cik:   SEC CIK (숫자 문자열, zfill 자동)
        types: 조회할 form 종류. None 이면 FILING_FORMS_DEFAULT
        days:  최근 N일 이내만 반환

    Returns:
        list[dict]: filing_date 내림차순 정렬
            {cik, ticker, form, filing_date, accession_number, primary_document,
             url, description, is_critical}
    """
    if types is None:
        types = FILING_FORMS_DEFAULT
    types_set = set(types)

    cik_str = str(int(cik)).zfill(10)
    url = f"{SEC_BASE}/submissions/CIK{cik_str}.json"

    async with aiohttp.ClientSession() as s:
        data = await _sec_get(s, url)

    if not data or not isinstance(data, dict):
        return []

    # ticker 역매핑 (CIK → ticker)
    tickers_field = data.get("tickers", [])
    ticker = tickers_field[0].upper() if tickers_field else ""

    recent = data.get("filings", {}).get("recent", {})
    if not recent:
        return []

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # 필드 배열 (모두 같은 인덱스)
    forms        = recent.get("form", [])
    dates        = recent.get("filingDate", [])
    accessions   = recent.get("accessionNumber", [])
    documents    = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    results = []
    for i in range(len(accessions)):
        date = dates[i] if i < len(dates) else ""
        if date < cutoff:
            # filings는 날짜 내림차순 → cutoff 이전부터는 모두 skip
            break
        form = forms[i] if i < len(forms) else ""
        if types_set and form not in types_set:
            continue

        acc_no = accessions[i]
        doc    = documents[i]    if i < len(documents)    else ""
        desc   = descriptions[i] if i < len(descriptions) else ""

        # URL 생성: https://www.sec.gov/Archives/edgar/data/{int_cik}/{acc_no_no_dash}/{doc}
        cik_int = int(cik)
        acc_slug = acc_no.replace("-", "")
        if doc:
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_slug}/{doc}"
        else:
            filing_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_str}&type={form}&dateb=&owner=include&count=10"

        results.append({
            "cik":              cik_str,
            "ticker":           ticker,
            "form":             form,
            "filing_date":      date,
            "accession_number": acc_no,
            "primary_document": doc,
            "description":      desc,
            "url":              filing_url,
            "is_critical":      form in FILING_FORMS_CRITICAL,
        })

    # 날짜 내림차순 (보통 이미 그렇지만 보장)
    results.sort(key=lambda x: x["filing_date"], reverse=True)
    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DB 저장 (sec_filings 테이블)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_SEC_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS sec_filings (
    cik                TEXT NOT NULL,
    ticker             TEXT,
    form               TEXT NOT NULL,
    filing_date        TEXT NOT NULL,
    accession_number   TEXT NOT NULL,
    primary_document   TEXT,
    description        TEXT,
    url                TEXT,
    is_critical        INTEGER DEFAULT 0,
    is_alerted         INTEGER DEFAULT 0,
    collected_at       TEXT,
    PRIMARY KEY (cik, accession_number)
);
CREATE INDEX IF NOT EXISTS idx_sec_filings_form_date ON sec_filings(form, filing_date);
CREATE INDEX IF NOT EXISTS idx_sec_filings_ticker     ON sec_filings(ticker);
CREATE INDEX IF NOT EXISTS idx_sec_filings_alert      ON sec_filings(is_alerted, filing_date);
"""


def _ensure_sec_table() -> None:
    """sec_filings 테이블이 없으면 생성."""
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=15)
        conn.execute("PRAGMA cache_size = -65536")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA mmap_size = 268435456")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.executescript(_SEC_SCHEMA_DDL)
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("sec_filings 테이블 생성 실패: %s", exc)


def upsert_sec_filings(filings: list[dict]) -> int:
    """filing 목록을 sec_filings 테이블에 upsert. 저장된 건수 반환."""
    if not filings:
        return 0
    _ensure_sec_table()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        (
            f["cik"],
            f.get("ticker", ""),
            f["form"],
            f["filing_date"],
            f["accession_number"],
            f.get("primary_document", ""),
            f.get("description", ""),
            f.get("url", ""),
            1 if f.get("is_critical") else 0,
            0,   # is_alerted = 0 (Phase 2에서 처리)
            now,
        )
        for f in filings
    ]
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=15)
        conn.execute("PRAGMA cache_size = -65536")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA mmap_size = 268435456")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.executemany(
            """INSERT OR REPLACE INTO sec_filings
               (cik, ticker, form, filing_date, accession_number,
                primary_document, description, url, is_critical, is_alerted, collected_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        n = conn.total_changes
        conn.close()
        return n
    except Exception as exc:
        logger.error("upsert_sec_filings 실패: %s", exc)
        return 0


def query_sec_filings(
    ticker: str | None = None,
    forms: list[str] | None = None,
    days: int = 30,
    unalerted_only: bool = False,
    limit: int = 100,
) -> list[dict]:
    """DB에서 공시 조회 (Phase 2 polling 용).

    Returns: list[dict] (filing_date 내림차순)
    """
    _ensure_sec_table()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    wheres = ["filing_date >= ?"]
    params: list = [cutoff]

    if ticker:
        wheres.append("ticker = ?")
        params.append(ticker.upper())
    if forms:
        placeholders = ",".join("?" * len(forms))
        wheres.append(f"form IN ({placeholders})")
        params.extend(forms)
    if unalerted_only:
        wheres.append("is_alerted = 0")

    where_clause = " AND ".join(wheres)
    sql = f"""
        SELECT cik, ticker, form, filing_date, accession_number,
               primary_document, description, url, is_critical, is_alerted, collected_at
        FROM sec_filings
        WHERE {where_clause}
        ORDER BY filing_date DESC
        LIMIT ?
    """
    params.append(limit)

    try:
        conn = sqlite3.connect(_DB_PATH, timeout=10)
        conn.execute("PRAGMA cache_size = -65536")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA mmap_size = 268435456")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("query_sec_filings 실패: %s", exc)
        return []
