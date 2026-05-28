"""연기금(NPS) 수집: pension_flow/NPS 13F/KR full."""
import os
import json
import re
import asyncio
import aiohttp
import sqlite3
import xml.etree.ElementTree as ET
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from ._config import *
from ._config import (
    KIS_BASE_URL, KIS_APP_KEY, KIS_APP_SECRET, KST, ET, _DATA_DIR, _DB_PATH,
    WATCHLIST_FILE, STOPLOSS_FILE, US_WATCHLIST_FILE, DART_SEEN_FILE,
    PORTFOLIO_FILE, WATCHALERT_FILE, WATCH_SENT_FILE, STOPLOSS_SENT_FILE,
    US_HOLDINGS_SENT_FILE, DECISION_LOG_FILE, COMPARE_LOG_FILE,
    WATCHLIST_LOG_FILE, EVENTS_FILE, WEEKLY_BASE_FILE, UNIVERSE_FILE,
    CONSENSUS_CACHE_FILE, PORTFOLIO_HISTORY_FILE, TRADE_LOG_FILE,
    SECTOR_FLOW_CACHE_FILE, SECTOR_ROTATION_FILE, SUPPLY_HISTORY_FILE,
    REPORTS_FILE, REGIME_STATE_FILE, MACRO_SENT_FILE, TOKEN_CACHE_FILE,
    GITHUB_TOKEN, _BACKUP_GIST_ENV, _BACKUP_FILES_LIST, MACRO_SYMBOLS,
    DART_BASE_URL,
)
from ._session import _get_session, _kis_get, _kis_headers, get_kis_token, _token_cache
from ._helpers import (
    _is_us_ticker, _guess_excd, _is_us_market_hours_kst, _is_us_market_closed,
    DART_KEYWORDS, _load_knu_senti_lex, _FINANCE_PHRASE_SCORES, _RANKING_RE,
    _US_POSITIVE_KEYWORDS, _US_NEGATIVE_KEYWORDS, _NYSE_TICKERS, _AMEX_TICKERS,
)
from ._files import (
    load_json, save_json, load_watchlist, load_stoploss, load_us_watchlist,
    load_dart_seen, load_watchalert, _wa_market, load_kr_watch_tickers,
    load_us_watch_tickers, load_kr_watch_dict, load_us_watch_dict,
    load_decision_log, load_trade_log, save_trade_log, get_trade_stats,
    load_consensus_cache, load_sector_flow_cache, save_sector_flow_cache,
    load_compare_log, load_watchlist_log, append_watchlist_log, load_events,
)
from .polymarket import NPS_DATA_GO_KR_PAGE, NPS_FALLBACK_ATCH_FILE_ID, _KO_EN_GROUP_MAP


def _normalize_company_name(name: str) -> str:
    """발행기관명 → stock_master 매칭용 표준화.

    - "(주)" prefix/suffix 제거
    - 한글표기 그룹명 → 영문약자 (엘지→LG 등)
    - 공백 제거 + 영문 대문자 통일
    """
    if not name:
        return ""
    s = name.strip()
    # 양쪽 (주) 제거
    s = re.sub(r"^\(주\)\s*", "", s)
    s = re.sub(r"\s*\(주\)$", "", s)
    s = s.replace("주식회사", "")
    # 공백 제거
    s = re.sub(r"\s+", "", s)
    # 한글 → 영문약자 prefix 변환
    for ko, en in _KO_EN_GROUP_MAP:
        if s.startswith(ko):
            s = en + s[len(ko):]
            break
    # 자주 쓰는 한글 단어 → 영문약자 (어디 위치하든)
    s = s.replace("엔터테인먼트", "ENT.")
    s = s.replace("에프엔에프", "F&F")
    s = s.replace("일렉트릭", "ELECTRIC")
    # 잘 알려진 동의어 prefix 후처리
    if s.startswith("현대자동차") and len(s) <= 6:
        s = "현대차" + s[5:]
    return s.upper()


def _ensure_nps_holdings_table(db_path: str):
    """nps_holdings_disclosed 테이블 생성 (idempotent)."""
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nps_holdings_disclosed (
            report_date    TEXT NOT NULL,    -- 보고서 작성기준일 (YYYY-MM-DD)
            company_name   TEXT NOT NULL,    -- 발행기관명 (원본 그대로)
            symbol         TEXT DEFAULT '',  -- stock_master 매칭 ticker (없으면 빈 문자열)
            ratio_pct      REAL DEFAULT 0,   -- 지분율(%)
            quarter        TEXT DEFAULT '',  -- '2025Q4' 등
            source_file    TEXT DEFAULT '',  -- atchFileId
            collected_at   TEXT DEFAULT '',
            PRIMARY KEY (report_date, company_name)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nps_date ON nps_holdings_disclosed(report_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nps_symbol ON nps_holdings_disclosed(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nps_quarter ON nps_holdings_disclosed(quarter)")
    conn.commit()
    conn.close()


async def _discover_nps_atch_file_id() -> str:
    """data.go.kr 메타페이지에서 최신 atchFileId 추출.
    실패 시 fallback 상수 반환.
    """
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as s:
            async with s.get(NPS_DATA_GO_KR_PAGE) as r:
                if r.status != 200:
                    return NPS_FALLBACK_ATCH_FILE_ID
                html = await r.text()
        m = re.findall(r"atchFileId=(FILE_\d+)", html)
        # 의미없는 'FILE_null' 같은 패턴 필터, 가장 흔한 ID 채택
        ids = [x for x in m if x.startswith("FILE_") and x != "FILE_null"]
        if ids:
            # 같은 ID가 여러 번 나오면 그게 정답
            from collections import Counter
            top = Counter(ids).most_common(1)[0][0]
            return top
    except Exception as e:
        print(f"[nps_5pct] atchFileId 자동 추출 실패: {e}")
    return NPS_FALLBACK_ATCH_FILE_ID


async def _download_nps_5pct_csv(atch_file_id: str = None) -> tuple:
    """NPS 5%룰 CSV 다운로드 → (rows, atch_file_id) 반환.

    Returns:
        (rows, atch_file_id) — rows = [{"name": str, "report_date": str, "ratio_pct": float}, ...]
    """
    if atch_file_id is None:
        atch_file_id = await _discover_nps_atch_file_id()

    url = (
        f"https://www.data.go.kr/cmm/cmm/fileDownload.do"
        f"?atchFileId={atch_file_id}&fileDetailSn=1&insertDataPrcus=N"
    )
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)
    raw_bytes = b""
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return ([], atch_file_id)
                raw_bytes = await r.read()
    except Exception as e:
        print(f"[nps_5pct] CSV 다운로드 실패: {e}")
        return ([], atch_file_id)

    # EUC-KR / CP949 디코딩 시도
    text = ""
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            text = raw_bytes.decode(enc)
            break
        except Exception:
            continue
    if not text:
        return ([], atch_file_id)

    rows = []
    import csv as _csv
    import io as _io
    reader = _csv.reader(_io.StringIO(text))
    header = None
    for line in reader:
        if not line or all(not c.strip() for c in line):
            continue
        if header is None:
            header = [c.strip() for c in line]
            continue
        if len(line) < 4:
            continue
        # 컬럼: 번호, 발행기관명, 보고서 작성기준일, 지분율(퍼센트)
        try:
            name = line[1].strip()
            report_date = line[2].strip()
            ratio_str = line[3].strip().replace(",", "")
            if not name or not report_date:
                continue
            try:
                ratio = float(ratio_str)
            except Exception:
                ratio = 0.0
            rows.append({
                "name": name,
                "report_date": report_date,
                "ratio_pct": ratio,
            })
        except Exception:
            continue
    return (rows, atch_file_id)


def _date_to_quarter(date_str: str) -> str:
    """'2025-10-14' → '2025Q4'."""
    try:
        y, mo, _ = date_str.split("-")
        m = int(mo)
        q = (m - 1) // 3 + 1
        return f"{y}Q{q}"
    except Exception:
        return ""


def _build_name_to_symbol_map(db_path: str) -> tuple:
    """stock_master 전체에서 (정규화명 → symbol) 매핑 + (정규화명, symbol) 리스트 반환.

    리스트는 substring fallback 매칭용.
    """
    import sqlite3 as _s
    direct = {}
    name_list = []  # [(normalized_name, symbol)]
    try:
        conn = _s.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        cur = conn.execute("SELECT symbol, name FROM stock_master WHERE name IS NOT NULL AND name != ''")
        for sym, nm in cur.fetchall():
            key = _normalize_company_name(nm)
            if key and key not in direct:
                direct[key] = sym
            if key:
                name_list.append((key, sym))
        conn.close()
    except Exception as e:
        print(f"[nps_5pct] name→symbol map 빌드 실패: {e}")
    return (direct, name_list)


def _match_company_to_symbol(name_csv: str, direct: dict, name_list: list) -> str:
    """발행기관명 → symbol 매칭.

    1차: 직접 정규화 비교
    2차: stock_master 이름이 csv명의 prefix (예: "한국단자" ↔ "한국단자공업")
    3차: csv명이 stock_master 이름의 prefix (예: "코오롱인더스트리" → "코오롱인더")
    길이차 ≤ 7 제한 (오매칭 방지)
    """
    key = _normalize_company_name(name_csv)
    if not key:
        return ""
    if key in direct:
        return direct[key]
    # 2차/3차 fallback
    best_sym = ""
    best_diff = 999
    for nm, sym in name_list:
        if not nm or len(nm) < 3:
            continue
        # case 2: stock_master 이름이 csv 키의 prefix
        if key.startswith(nm) and len(key) - len(nm) <= 7:
            diff = len(key) - len(nm)
            if diff < best_diff:
                best_diff = diff
                best_sym = sym
        # case 3: csv 키가 stock_master 이름의 prefix
        elif nm.startswith(key) and len(nm) - len(key) <= 7:
            diff = len(nm) - len(key)
            if diff < best_diff:
                best_diff = diff
                best_sym = sym
    return best_sym


async def collect_nps_5percent_disclosed() -> dict:
    """NPS 5%룰 CSV 다운로드 + DB 저장 (분기 갱신 시 신규 row만 누적).

    Returns:
        {"total_csv": int, "matched": int, "unmatched": int,
         "inserted_new": int, "atch_file_id": str, "quarters": [...]}
    """
    rows, atch_id = await _download_nps_5pct_csv()
    if not rows:
        return {"error": "CSV 다운로드 실패", "atch_file_id": atch_id}

    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_holdings_table(db_path)
    direct_map, name_list = _build_name_to_symbol_map(db_path)

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    now_iso = datetime.now(KST).isoformat()

    matched = 0
    unmatched_names = []
    inserted_new = 0
    quarters = set()

    for r in rows:
        name = r["name"]
        report_date = r["report_date"]
        ratio = r["ratio_pct"]
        symbol = _match_company_to_symbol(name, direct_map, name_list)
        if symbol:
            matched += 1
        else:
            unmatched_names.append(name)
        quarter = _date_to_quarter(report_date)
        if quarter:
            quarters.add(quarter)

        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO nps_holdings_disclosed
                   (report_date, company_name, symbol, ratio_pct, quarter, source_file, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (report_date, name, symbol, ratio, quarter, atch_id, now_iso),
            )
            if cur.rowcount > 0:
                inserted_new += 1
        except Exception as e:
            print(f"[nps_5pct] insert 실패 {name}: {e}")

    conn.commit()
    conn.close()

    return {
        "total_csv": len(rows),
        "matched": matched,
        "unmatched": len(unmatched_names),
        "unmatched_names": unmatched_names[:20],  # 디버그용 샘플
        "inserted_new": inserted_new,
        "atch_file_id": atch_id,
        "quarters": sorted(quarters),
        "fetched_at": now_iso,
    }


def fetch_nps_holdings(quarter: str = None, days: int = 90, ratio_min: float = 0.0,
                        held_watch_only: bool = False, limit: int = 100) -> dict:
    """NPS 5%룰 보유종목 조회.

    Args:
        quarter: '2025Q4' 등 분기 필터. None이면 days 기간으로.
        days: report_date 기준 최근 N일 (quarter=None일 때만)
        ratio_min: 최소 지분율(%) 필터
        held_watch_only: True면 보유+워치만
        limit: 최대 반환 건수

    Returns:
        {"period": str, "total": int, "rows": [{report_date, company_name, symbol, ratio_pct, ...}]}
    """
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_holdings_table(db_path)

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.row_factory = _s.Row

    where = ["ratio_pct >= ?"]
    params = [ratio_min]

    if quarter:
        where.append("quarter = ?")
        params.append(quarter)
        period_str = quarter
    else:
        cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
        where.append("report_date >= ?")
        params.append(cutoff)
        period_str = f"최근 {days}일"

    sql = f"""
        SELECT report_date, company_name, symbol, ratio_pct, quarter
        FROM nps_holdings_disclosed
        WHERE {' AND '.join(where)}
        ORDER BY report_date DESC, ratio_pct DESC
    """
    rows = []
    for r in conn.execute(sql, params).fetchall():
        rows.append({
            "report_date": r["report_date"],
            "company_name": r["company_name"],
            "symbol": r["symbol"] or "",
            "ratio_pct": float(r["ratio_pct"] or 0),
            "quarter": r["quarter"] or "",
        })
    conn.close()

    # 보유+워치 필터
    if held_watch_only:
        held_watch = set()
        try:
            portfolio = load_json(PORTFOLIO_FILE, {})
            for k in portfolio.keys():
                if k not in ("us_stocks", "cash_krw", "cash_usd") and not _is_us_ticker(k):
                    held_watch.add(k)
            for k in load_watchalert().keys():
                if not _is_us_ticker(k):
                    held_watch.add(k)
        except Exception:
            pass
        rows = [r for r in rows if r["symbol"] in held_watch]

    return {
        "period": period_str,
        "ratio_min": ratio_min,
        "total": len(rows),
        "rows": rows[:limit],
        "fetched_at": datetime.now(KST).isoformat(),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NPS 미국 보유 — SEC EDGAR Form 13F-HR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CIK 0001608046 (National Pension Service)
# 13F-HR: 분기 끝 후 ~45일 내 제출 (예: 4Q25 → 2026-02-10)
# 컬럼: nameOfIssuer, cusip, value(천달러), sshPrnamt(주식수)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NPS_US_CIK = "0001608046"
SEC_USER_AGENT = "stock-bot research arcturusnd@gmail.com"
SEC_BASE = "https://www.sec.gov"
SEC_DATA_BASE = "https://data.sec.gov"


def _ensure_nps_us_table(db_path: str):
    """nps_us_holdings 테이블 생성 (idempotent)."""
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nps_us_holdings (
            accession      TEXT NOT NULL,    -- 13F 파일 ID (예: 0001608046-26-000001)
            filing_date    TEXT,             -- 파일 제출일 (YYYY-MM-DD)
            period_end     TEXT,             -- 분기말일 (YYYY-MM-DD)
            quarter        TEXT,             -- '2025Q4' 등
            cusip          TEXT NOT NULL,
            name_of_issuer TEXT,
            value_usd      INTEGER DEFAULT 0,  -- 13F의 value × 1000 (실제 달러)
            shares         INTEGER DEFAULT 0,
            ticker         TEXT DEFAULT '',
            collected_at   TEXT DEFAULT '',
            PRIMARY KEY (accession, cusip)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npsus_quarter ON nps_us_holdings(quarter)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npsus_period ON nps_us_holdings(period_end)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npsus_ticker ON nps_us_holdings(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npsus_cusip ON nps_us_holdings(cusip)")
    conn.commit()
    conn.close()


def _period_to_quarter(period_end: str) -> str:
    """'2025-12-31' → '2025Q4'."""
    try:
        y, m, _ = period_end.split("-")
        mi = int(m)
        q = (mi - 1) // 3 + 1
        return f"{y}Q{q}"
    except Exception:
        return ""


async def _sec_fetch_text(url: str) -> str:
    """SEC EDGAR/data.sec.gov 호출 — UA 필수, ssl 우회 안전."""
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as s:
        async with s.get(url) as r:
            if r.status != 200:
                raise RuntimeError(f"SEC HTTP {r.status} {url}")
            return await r.text()


async def _sec_list_nps_13f_filings(max_quarters: int = 8) -> list:
    """NPS submissions JSON에서 최근 13F-HR 목록 추출.

    Returns:
        [{"accession", "accession_nodash", "filing_date", "period_end", "quarter"}, ...]
    """
    url = f"{SEC_DATA_BASE}/submissions/CIK{NPS_US_CIK}.json"
    txt = await _sec_fetch_text(url)
    import json as _json
    d = _json.loads(txt)
    recent = d.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    fdates = recent.get("filingDate", [])
    rdates = recent.get("reportDate", [])
    accs = recent.get("accessionNumber", [])
    out = []
    for i, f in enumerate(forms):
        if "13F" not in f:
            continue
        acc = accs[i]
        fd = fdates[i] if i < len(fdates) else ""
        rd = rdates[i] if i < len(rdates) else ""
        out.append({
            "accession": acc,
            "accession_nodash": acc.replace("-", ""),
            "filing_date": fd,
            "period_end": rd,
            "quarter": _period_to_quarter(rd),
        })
        if len(out) >= max_quarters:
            break
    return out


async def _sec_locate_holdings_xml(accession_nodash: str) -> str:
    """13F 파일링 폴더에서 holdings xml 파일명 식별.

    /Archives/edgar/data/{cik}/{acc_nodash}/index.json 에 파일 목록 있음.
    primary_doc.xml 외의 .xml 이 holdings (보통 분기명_v2.xml).
    """
    url = (f"{SEC_BASE}/Archives/edgar/data/"
           f"{int(NPS_US_CIK)}/{accession_nodash}/index.json")
    txt = await _sec_fetch_text(url)
    import json as _json
    d = _json.loads(txt)
    items = d.get("directory", {}).get("item", [])
    for it in items:
        name = it.get("name", "")
        if name.endswith(".xml") and name != "primary_doc.xml":
            return name
    raise RuntimeError(f"holdings xml not found in {accession_nodash}")


async def _sec_fetch_holdings(accession_nodash: str, holdings_filename: str) -> list:
    """holdings xml 다운로드 + 파싱.

    Returns:
        [{cusip, name_of_issuer, value_usd, shares}, ...]
    """
    url = (f"{SEC_BASE}/Archives/edgar/data/"
           f"{int(NPS_US_CIK)}/{accession_nodash}/{holdings_filename}")
    txt = await _sec_fetch_text(url)
    import xml.etree.ElementTree as _ET
    # ns 무시: tag 끝부분만 비교
    root = _ET.fromstring(txt)
    rows = []
    for info in root.iter():
        tag = info.tag.split("}")[-1] if "}" in info.tag else info.tag
        if tag != "infoTable":
            continue
        rec = {}
        for child in info.iter():
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag == "nameOfIssuer":
                rec["name_of_issuer"] = (child.text or "").strip()
            elif ctag == "cusip":
                rec["cusip"] = (child.text or "").strip()
            elif ctag == "value":
                # 2023 SEC 개정 후 actual dollars (이전 thousands였음).
                # NPS 13F는 2025년 분기만 사용 → actual dollars 그대로.
                try:
                    rec["value_usd"] = int(float(child.text or 0))
                except Exception:
                    rec["value_usd"] = 0
            elif ctag == "sshPrnamt":
                try:
                    rec["shares"] = int(float(child.text or 0))
                except Exception:
                    rec["shares"] = 0
        if rec.get("cusip"):
            rec.setdefault("name_of_issuer", "")
            rec.setdefault("value_usd", 0)
            rec.setdefault("shares", 0)
            rows.append(rec)
    return rows


async def collect_nps_us_13f(max_quarters: int = 4) -> dict:
    """NPS 미국 13F-HR 분기 holdings 자동 수집 → DB 누적.

    Args:
        max_quarters: 직전 N개 분기까지 수집 (기본 4 = 최근 1년)

    Returns:
        {"quarters_processed": N, "total_rows_inserted": int, "filings": [...]}
    """
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_us_table(db_path)

    try:
        filings = await _sec_list_nps_13f_filings(max_quarters=max_quarters)
    except Exception as e:
        return {"error": f"submissions JSON 실패: {e}"}

    if not filings:
        return {"error": "13F-HR 파일링 없음"}

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    now_iso = datetime.now(KST).isoformat()

    total_inserted = 0
    processed = []

    for fl in filings:
        acc = fl["accession"]
        acc_nd = fl["accession_nodash"]
        # 이미 수집된 accession은 스킵 (속도 최적화)
        existing = conn.execute(
            "SELECT COUNT(*) FROM nps_us_holdings WHERE accession=?",
            (acc,),
        ).fetchone()[0]
        if existing > 0:
            processed.append({**fl, "status": "skip-existing", "rows": existing})
            continue

        try:
            holdings_fn = await _sec_locate_holdings_xml(acc_nd)
            # SEC fair-use rate limit (10 req/sec). 보수적으로 0.2초.
            await asyncio.sleep(0.2)
            rows = await _sec_fetch_holdings(acc_nd, holdings_fn)
            await asyncio.sleep(0.2)
        except Exception as e:
            processed.append({**fl, "status": "fetch-fail", "error": str(e)})
            continue

        ins = 0
        for r in rows:
            try:
                cur = conn.execute(
                    """INSERT OR REPLACE INTO nps_us_holdings
                       (accession, filing_date, period_end, quarter,
                        cusip, name_of_issuer, value_usd, shares, ticker, collected_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (acc, fl["filing_date"], fl["period_end"], fl["quarter"],
                     r["cusip"], r.get("name_of_issuer", ""),
                     r.get("value_usd", 0), r.get("shares", 0),
                     "", now_iso),
                )
                if cur.rowcount > 0:
                    ins += 1
            except Exception:
                pass
        total_inserted += ins
        processed.append({**fl, "status": "inserted", "rows": ins})

    conn.commit()
    conn.close()

    return {
        "quarters_processed": len(processed),
        "total_rows_inserted": total_inserted,
        "filings": processed,
        "fetched_at": now_iso,
    }


def fetch_nps_us_holdings(quarter: str = None, top: int = 30,
                            include_changes: bool = False) -> dict:
    """NPS 미국 보유 종목 조회 (가치 정렬).

    Args:
        quarter: '2025Q4' 등. None이면 가장 최신 분기 자동.
        top: TOP N (기본 30)
        include_changes: True면 직전 분기 대비 ▲/▼ 정보 포함

    Returns:
        {"quarter", "period_end", "total_holdings", "total_value_usd",
         "rows": [{name, cusip, ticker, value_usd, shares, weight_pct,
                   change_pct (전 분기 대비), status (NEW/UP/DOWN/EXIT/HELD)}]}
    """
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_us_table(db_path)

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.row_factory = _s.Row

    if not quarter:
        row = conn.execute(
            "SELECT quarter FROM nps_us_holdings ORDER BY period_end DESC LIMIT 1"
        ).fetchone()
        if not row:
            conn.close()
            return {"error": "데이터 없음. collect_nps_us_13f 호출 필요"}
        quarter = row["quarter"]

    cur_rows = conn.execute(
        """SELECT period_end, cusip, name_of_issuer, value_usd, shares, ticker
           FROM nps_us_holdings WHERE quarter = ? ORDER BY value_usd DESC""",
        (quarter,),
    ).fetchall()

    if not cur_rows:
        conn.close()
        return {"error": f"분기 {quarter} 데이터 없음"}

    period_end = cur_rows[0]["period_end"]
    total_value = sum(r["value_usd"] for r in cur_rows)

    # 직전 분기 비교용 데이터
    prev_map = {}  # cusip → {value_usd, shares}
    if include_changes:
        prev_q_row = conn.execute(
            "SELECT DISTINCT quarter FROM nps_us_holdings "
            "WHERE quarter < ? ORDER BY quarter DESC LIMIT 1",
            (quarter,),
        ).fetchone()
        if prev_q_row:
            prev_q = prev_q_row["quarter"]
            for pr in conn.execute(
                """SELECT cusip, value_usd, shares
                   FROM nps_us_holdings WHERE quarter = ?""",
                (prev_q,),
            ).fetchall():
                prev_map[pr["cusip"]] = {
                    "value_usd": pr["value_usd"], "shares": pr["shares"]
                }
    conn.close()

    # EXIT 판정용: 현재 분기 *전체* cusip 집합
    cur_cusips_all = set(r["cusip"] for r in cur_rows)

    rows = []
    for r in cur_rows[:top]:
        d = {
            "cusip": r["cusip"],
            "name_of_issuer": r["name_of_issuer"],
            "ticker": r["ticker"] or "",
            "value_usd": r["value_usd"],
            "shares": r["shares"],
            "weight_pct": (r["value_usd"] * 100.0 / total_value) if total_value > 0 else 0,
        }
        if include_changes:
            prev = prev_map.get(r["cusip"])
            if not prev:
                d["status"] = "NEW"
                d["share_change_pct"] = None
                d["value_change_pct"] = None
            else:
                prev_sh = prev["shares"] or 0
                prev_v = prev["value_usd"] or 0
                d["share_change_pct"] = ((r["shares"] - prev_sh) * 100.0 / prev_sh) if prev_sh > 0 else None
                d["value_change_pct"] = ((r["value_usd"] - prev_v) * 100.0 / prev_v) if prev_v > 0 else None
                if d["share_change_pct"] is None:
                    d["status"] = "HELD"
                elif d["share_change_pct"] > 1:
                    d["status"] = "UP"
                elif d["share_change_pct"] < -1:
                    d["status"] = "DOWN"
                else:
                    d["status"] = "HELD"
        rows.append(d)

    # EXIT 종목 (직전 분기에만 있음)
    exits = []
    if include_changes and prev_map:
        for cusip, prev in prev_map.items():
            if cusip in cur_cusips_all:
                continue
            # 이름은 직전 분기에서 가져와야 함
            conn = _s.connect(db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA cache_size = -65536;")
            conn.execute("PRAGMA temp_store = MEMORY;")
            conn.execute("PRAGMA mmap_size = 268435456;")
            conn.execute("PRAGMA busy_timeout = 30000;")
            conn.row_factory = _s.Row
            nm_row = conn.execute(
                "SELECT name_of_issuer FROM nps_us_holdings WHERE cusip=? LIMIT 1",
                (cusip,),
            ).fetchone()
            conn.close()
            exits.append({
                "cusip": cusip,
                "name_of_issuer": nm_row["name_of_issuer"] if nm_row else "?",
                "prev_value_usd": prev["value_usd"],
                "prev_shares": prev["shares"],
                "status": "EXIT",
            })
        exits.sort(key=lambda x: -x.get("prev_value_usd", 0))

    return {
        "quarter": quarter,
        "period_end": period_end,
        "total_holdings": len(cur_rows),
        "total_value_usd": total_value,
        "rows": rows,
        "exits_top10": exits[:10] if include_changes else [],
        "fetched_at": datetime.now(KST).isoformat(),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NPS 한국 풀 포트 200종목 — whale-insight.com 데이터
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# data.js의 NPS_KR.krTimeline (200종목, 분기 갱신)
# 5%룰 보고 + NPS 사업보고서를 수동 큐레이션한 데이터.
# 컬럼: name, weight%, valuation(억), change%, share24%, share25Q4%
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHALE_INSIGHT_DATA_JS = "https://whale-insight.com/assets/js/data.js"


def _ensure_nps_kr_full_table(db_path: str):
    """nps_kr_full_holdings 테이블 — whale-insight 분기 풀포트 미러."""
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nps_kr_full_holdings (
            snapshot_date  TEXT NOT NULL,    -- 우리 수집 시점 (YYYY-MM-DD)
            quarter_label  TEXT,             -- '2025Q4' (data.js의 share25Q4 참조)
            name           TEXT NOT NULL,    -- whale-insight 원본 종목명
            symbol         TEXT DEFAULT '',  -- stock_master 매칭 ticker
            weight_pct     REAL DEFAULT 0,   -- 포트 비중%
            valuation_eok  INTEGER DEFAULT 0, -- 평가액 억원
            change_pct     REAL DEFAULT 0,   -- 전 분기 대비 (whale-insight 원본)
            share_prev_pct REAL DEFAULT 0,   -- share24 (전 연도 지분율)
            share_curr_pct REAL DEFAULT 0,   -- share25Q4 (현 분기 지분율)
            data_missing   INTEGER DEFAULT 0,
            source_version TEXT,             -- data.js v 쿼리값
            collected_at   TEXT,
            PRIMARY KEY (snapshot_date, name)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npskr_full_date ON nps_kr_full_holdings(snapshot_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npskr_full_symbol ON nps_kr_full_holdings(symbol)")
    conn.commit()
    conn.close()


def _parse_pct(s) -> tuple:
    """'+0.5%' / '-1.2%' / '데이터 부족' → (float, missing_flag)."""
    if s is None:
        return (0.0, 1)
    txt = str(s).strip()
    if "부족" in txt or not txt or txt == "—":
        return (0.0, 1)
    try:
        return (float(txt.replace("%", "").replace("+", "").strip()), 0)
    except Exception:
        return (0.0, 1)


def _parse_eok(s) -> int:
    """'230,421억' → 230421 (억원)."""
    if not s:
        return 0
    try:
        v = str(s).replace(",", "").replace("억", "").strip()
        return int(v)
    except Exception:
        return 0


async def collect_nps_kr_full_from_whale_insight() -> dict:
    """whale-insight.com data.js 다운로드 → NPS_KR.krTimeline 추출 → DB 저장.

    출처 표기 의무: 카드에 '출처: whale-insight.com' 명시.
    """
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_kr_full_table(db_path)

    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as s:
            async with s.get(WHALE_INSIGHT_DATA_JS) as r:
                if r.status != 200:
                    return {"error": f"HTTP {r.status}"}
                txt = await r.text()
    except Exception as e:
        return {"error": f"download failed: {e}"}

    # 버전 추출 (URL에 ?v=20260427_0 형태로 노출되는 게 있다면 변경 시점 식별)
    src_version = ""
    vm = re.search(r"data\.js\?v=([^\"'>\s]+)", txt)
    if vm:
        src_version = vm.group(1)

    # NPS_KR.krTimeline 블록만 추출
    # id: 'NPS_KR' ... krTimeline: [ ... ] 다음 } 닫힘 전까지
    block_match = re.search(
        r"id:\s*['\"]NPS_KR['\"].*?krTimeline:\s*\[(.*?)\]",
        txt, re.DOTALL,
    )
    if not block_match:
        return {"error": "NPS_KR.krTimeline 블록 못 찾음"}
    block = block_match.group(1)

    # 각 row: { name: '...', weight: '...%', valuation: '...억', change: '...', share24: '...%', share25Q4: '...%' },
    row_pattern = re.compile(
        r"\{\s*"
        r"name:\s*['\"]([^'\"]+)['\"],\s*"
        r"weight:\s*['\"]([^'\"]+)['\"],\s*"
        r"valuation:\s*['\"]([^'\"]+)['\"],\s*"
        r"change:\s*['\"]([^'\"]+)['\"],\s*"
        r"share24:\s*['\"]([^'\"]+)['\"],\s*"
        r"share25Q4:\s*['\"]([^'\"]+)['\"]\s*"
        r"\}"
    )
    matches = row_pattern.findall(block)
    if not matches:
        return {"error": "row 파싱 실패"}

    # 추출한 quarter 라벨 (share25Q4 → 2025Q4)
    quarter_label = "2025Q4"  # 데이터 컬럼명에서 추정. 차후 분기 바뀌면 갱신 필요.
    snap_date = datetime.now(KST).strftime("%Y-%m-%d")

    direct_map, name_list = _build_name_to_symbol_map(db_path)

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    now_iso = datetime.now(KST).isoformat()

    inserted = 0
    matched = 0
    unmatched_names = []
    for (name, weight, valuation, change, share24, share25q4) in matches:
        symbol = _match_company_to_symbol(name, direct_map, name_list)
        if symbol:
            matched += 1
        else:
            unmatched_names.append(name)
        weight_v, weight_miss = _parse_pct(weight)
        chg_v, chg_miss = _parse_pct(change)
        s_prev, sp_miss = _parse_pct(share24)
        s_curr, sc_miss = _parse_pct(share25q4)
        eok = _parse_eok(valuation)
        miss = 1 if (weight_miss or chg_miss or sp_miss or sc_miss) else 0
        try:
            conn.execute(
                """INSERT OR REPLACE INTO nps_kr_full_holdings
                   (snapshot_date, quarter_label, name, symbol,
                    weight_pct, valuation_eok, change_pct,
                    share_prev_pct, share_curr_pct,
                    data_missing, source_version, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (snap_date, quarter_label, name, symbol,
                 weight_v, eok, chg_v, s_prev, s_curr,
                 miss, src_version, now_iso),
            )
            inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()

    return {
        "snapshot_date": snap_date,
        "quarter_label": quarter_label,
        "total_rows": len(matches),
        "inserted": inserted,
        "matched": matched,
        "unmatched": len(unmatched_names),
        "unmatched_sample": unmatched_names[:10],
        "source_version": src_version,
        "fetched_at": now_iso,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# whale-insight 5%룰/10%룰 변동 데이터 미러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# major_stock.js (5%↑ 변동) + elestock.js (10%↑ 보유자 매매)
# 우리 자체 수집보다 더 풍부한 데이터 (DART 직파싱 추정)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WI_MAJOR_JS = "https://whale-insight.com/assets/js/nps_major_stock.js"
WI_ELE_JS = "https://whale-insight.com/assets/js/nps_elestock.js"


def _ensure_wi_change_tables(db_path: str):
    """whale-insight 변동 데이터 테이블."""
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wi_5pct_changes (
            report_date    TEXT NOT NULL,
            company        TEXT NOT NULL,
            symbol         TEXT DEFAULT '',
            stkqy          INTEGER DEFAULT 0,
            stkqy_irds     INTEGER DEFAULT 0,
            stkrt          REAL DEFAULT 0,
            stkrt_irds     REAL DEFAULT 0,
            report_resn    TEXT DEFAULT '',
            source_version TEXT DEFAULT '',
            collected_at   TEXT DEFAULT '',
            PRIMARY KEY (report_date, company, stkrt_irds)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wi5_date ON wi_5pct_changes(report_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wi5_symbol ON wi_5pct_changes(symbol)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wi_10pct_insiders (
            report_date    TEXT NOT NULL,
            company        TEXT NOT NULL,
            symbol         TEXT DEFAULT '',
            stkqy          INTEGER DEFAULT 0,
            stkqy_irds     INTEGER DEFAULT 0,
            stkrt          REAL DEFAULT 0,
            stkrt_irds     REAL DEFAULT 0,
            shrholdr_role  TEXT DEFAULT '',
            source_version TEXT DEFAULT '',
            collected_at   TEXT DEFAULT '',
            PRIMARY KEY (report_date, company, stkrt_irds)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wi10_date ON wi_10pct_insiders(report_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wi10_symbol ON wi_10pct_insiders(symbol)")
    conn.commit()
    conn.close()


def _parse_int_with_sign(s) -> int:
    """'+193731' / '-25,733,062' / '0' → int."""
    if s is None:
        return 0
    txt = str(s).replace(",", "").strip()
    try:
        return int(float(txt))
    except Exception:
        return 0


def _parse_float_with_sign(s) -> float:
    """'-13.63' / '+0.60' / '0' → float."""
    if s is None:
        return 0.0
    txt = str(s).replace(",", "").replace("%", "").strip()
    try:
        return float(txt)
    except Exception:
        return 0.0


async def _fetch_wi_js_array(url: str, var_name: str) -> list:
    """whale-insight JS 파일 다운로드 → const VAR_NAME = [...] 배열 추출."""
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return []
                txt = await r.text()
    except Exception as e:
        print(f"[wi_fetch] {url} 실패: {e}")
        return []

    # const VAR_NAME = [ ... ];
    m = re.search(rf"{var_name}\s*=\s*\[(.*?)\];", txt, re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    # 객체 단위로 파싱 — { key: 'value', ... }
    obj_pattern = re.compile(r"\{([^}]+)\}", re.DOTALL)
    field_pattern = re.compile(r"(\w+):\s*'([^']*)'")
    out = []
    for obj_match in obj_pattern.finditer(block):
        body = obj_match.group(1)
        rec = {}
        for fm in field_pattern.finditer(body):
            rec[fm.group(1)] = fm.group(2)
        if rec:
            out.append(rec)
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 5%룰 (D001) + 임원·10%↑ (D002) 직접 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _ensure_dart_change_tables(db_path: str):
    """DART 5%룰/10%룰 테이블 — rcept_no PK로 중복 차단."""
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dart_5pct_changes (
            rcept_no       TEXT PRIMARY KEY,
            rcept_dt       TEXT NOT NULL,
            corp_code      TEXT,
            company        TEXT NOT NULL,
            symbol         TEXT DEFAULT '',
            repror         TEXT DEFAULT '',
            stkqy          INTEGER DEFAULT 0,
            stkqy_irds     INTEGER DEFAULT 0,
            stkrt          REAL DEFAULT 0,
            stkrt_irds     REAL DEFAULT 0,
            report_resn    TEXT DEFAULT '',
            collected_at   TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_d5_dt ON dart_5pct_changes(rcept_dt)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_d5_sym ON dart_5pct_changes(symbol)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dart_10pct_insiders (
            rcept_no       TEXT NOT NULL,
            seq            INTEGER NOT NULL,    -- elestock는 한 보고에 여러 row
            rcept_dt       TEXT NOT NULL,
            corp_code      TEXT,
            company        TEXT NOT NULL,
            symbol         TEXT DEFAULT '',
            repror         TEXT DEFAULT '',
            shrholdr_role  TEXT DEFAULT '',
            stkqy          INTEGER DEFAULT 0,
            stkqy_irds     INTEGER DEFAULT 0,
            stkrt          REAL DEFAULT 0,
            stkrt_irds     REAL DEFAULT 0,
            collected_at   TEXT DEFAULT '',
            PRIMARY KEY (rcept_no, seq)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_d10_dt ON dart_10pct_insiders(rcept_dt)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_d10_sym ON dart_10pct_insiders(symbol)")
    conn.commit()
    conn.close()


async def _dart_get(session: aiohttp.ClientSession, path: str, params: dict) -> dict:
    """DART API GET — JSON 반환."""
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        return {"status": "ERR", "message": "DART_API_KEY 없음"}
    p = {**params, "crtfc_key": key}
    url = f"https://opendart.fss.or.kr/api/{path}"
    try:
        async with session.get(url, params=p, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return {"status": "HTTP", "code": r.status}
            return await r.json(content_type=None)
    except Exception as e:
        return {"status": "EXC", "message": str(e)}


async def _dart_list_disclosures(detail_ty: str, days: int = 14) -> list:
    """DART list.json 페이징 검색 → 모든 페이지 합쳐 반환.

    Returns: [{corp_code, corp_name, rcept_no, rcept_dt, report_nm}, ...]
    """
    end = datetime.now(KST)
    start = end - timedelta(days=days)
    bgn = start.strftime("%Y%m%d")
    fin = end.strftime("%Y%m%d")
    out = []
    page = 1
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        while True:
            d = await _dart_get(s, "list.json", {
                "pblntf_detail_ty": detail_ty,
                "bgn_de": bgn,
                "end_de": fin,
                "page_count": "100",
                "page_no": str(page),
            })
            if d.get("status") != "000":
                if d.get("status") == "013":
                    break  # 데이터 없음
                print(f"[dart_list {detail_ty}] page {page} status={d.get('status')}: {d.get('message')}")
                break
            items = d.get("list", []) or []
            out.extend(items)
            total = d.get("total_count", 0)
            seen = page * 100
            if seen >= total or not items:
                break
            page += 1
            await asyncio.sleep(0.05)
    return out


async def collect_nps_dart_increments(days: int = 7) -> dict:
    """NPS 5%룰 DART 증분 수집 — 분기 베이스라인에 살붙이기.

    전략:
      1) D001 list.json 최근 N일 검색 (corp_code 없으면 3개월 한도, days≤90)
      2) 각 종목 majorstock.json → repror == '국민연금공단' 필터
      3) nps_holdings_disclosed 테이블에 INSERT OR REPLACE
         (data.go.kr 분기 보고와 같은 테이블 — source 컬럼으로 구분)

    NPS 분기 보고 사이의 일별 변동을 누적.
    """
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_holdings_table(db_path)
    # 컬럼 보강 (idempotent)
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    for col, ddl in [
        ("stkqy", "INTEGER DEFAULT 0"),
        ("stkqy_irds", "INTEGER DEFAULT 0"),
        ("report_resn", "TEXT DEFAULT ''"),
        ("source", "TEXT DEFAULT 'data.go.kr'"),
        ("rcept_no", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE nps_holdings_disclosed ADD COLUMN {col} {ddl}")
        except Exception:
            pass  # 이미 존재
    conn.commit()
    conn.close()

    days = max(1, min(days, 90))
    direct_map, name_list = _build_name_to_symbol_map(db_path)

    disc = await _dart_list_disclosures("D001", days)
    if not disc:
        return {"error": "D001 검색 결과 없음"}

    period_rcepts = {it.get("rcept_no") for it in disc if it.get("rcept_no")}
    corp_codes = {}
    for it in disc:
        cc = it.get("corp_code")
        if cc:
            corp_codes[cc] = it.get("corp_name", "")

    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    now_iso = datetime.now(KST).isoformat()
    nps_inserted = 0
    nps_seen = 0
    fetched_corps = 0

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        for cc, cname in corp_codes.items():
            d = await _dart_get(s, "majorstock.json", {"corp_code": cc})
            await asyncio.sleep(0.1)
            if d.get("status") != "000":
                continue
            fetched_corps += 1
            for rec in d.get("list", []) or []:
                rep = rec.get("repror", "") or ""
                # NPS 매칭 — 한글/영문/한자 모두
                if "국민연금" not in rep and "National Pension" not in rep:
                    continue
                rcept_no = rec.get("rcept_no", "")
                # 우리 검색 기간 외 보고는 건너뜀
                if rcept_no and rcept_no not in period_rcepts:
                    continue
                nps_seen += 1
                sym = _match_company_to_symbol(cname, direct_map, name_list)
                rcept_dt = rec.get("rcept_dt", "")  # YYYY-MM-DD (DART)
                # data.go.kr 분기 보고 PK = (report_date, company_name)
                # 같은 NPS 보고가 한 분기에 한 종목에 중복될 수 있어 (작성기준일 다름)
                # → DART의 rcept_dt를 report_date로 사용
                quarter = _date_to_quarter(rcept_dt)
                try:
                    cur = conn.execute(
                        """INSERT OR REPLACE INTO nps_holdings_disclosed
                           (report_date, company_name, symbol, ratio_pct, quarter,
                            source_file, collected_at,
                            stkqy, stkqy_irds, report_resn, source, rcept_no)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (rcept_dt, cname, sym,
                         _parse_float_with_sign(rec.get("stkrt")),
                         quarter, "", now_iso,
                         _parse_int_with_sign(rec.get("stkqy")),
                         _parse_int_with_sign(rec.get("stkqy_irds")),
                         (rec.get("report_resn") or "").replace("\n", " ")[:200],
                         "dart",
                         rcept_no),
                    )
                    if cur.rowcount > 0:
                        nps_inserted += 1
                except Exception:
                    pass
    conn.commit()
    conn.close()

    return {
        "disclosures_searched": len(disc),
        "corps_fetched": fetched_corps,
        "nps_reports_found": nps_seen,
        "nps_inserted": nps_inserted,
        "fetched_at": now_iso,
    }


async def collect_dart_5pct_changes(days: int = 14) -> dict:
    """DART 5%룰 (D001) 최근 N일 수집.

    list.json → unique corp_code → majorstock.json → DB.
    """
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_dart_change_tables(db_path)
    direct_map, name_list = _build_name_to_symbol_map(db_path)

    # 1) D001 list 검색 → unique corp_code + 우리 기간 내 rcept_no 집합
    disc = await _dart_list_disclosures("D001", days)
    if not disc:
        return {"error": "D001 검색 결과 없음"}

    period_rcepts = {it.get("rcept_no") for it in disc if it.get("rcept_no")}
    corp_codes = {}  # corp_code → corp_name
    for it in disc:
        cc = it.get("corp_code")
        if cc:
            corp_codes[cc] = it.get("corp_name", "")

    # 2) 각 corp_code에 majorstock.json 호출 → period 내 보고만 저장
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    now_iso = datetime.now(KST).isoformat()
    inserted = 0
    fetched_corps = 0

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        for cc, cname in corp_codes.items():
            d = await _dart_get(s, "majorstock.json", {"corp_code": cc})
            await asyncio.sleep(0.1)  # rate limit
            if d.get("status") != "000":
                continue
            fetched_corps += 1
            for rec in d.get("list", []) or []:
                rcept_no = rec.get("rcept_no", "")
                if not rcept_no or rcept_no not in period_rcepts:
                    continue  # 우리 기간 내 보고만
                sym = _match_company_to_symbol(cname, direct_map, name_list)
                try:
                    cur = conn.execute(
                        """INSERT OR REPLACE INTO dart_5pct_changes
                           (rcept_no, rcept_dt, corp_code, company, symbol,
                            repror, stkqy, stkqy_irds, stkrt, stkrt_irds,
                            report_resn, collected_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (rcept_no, rec.get("rcept_dt", ""), cc, cname, sym,
                         rec.get("repror", ""),
                         _parse_int_with_sign(rec.get("stkqy")),
                         _parse_int_with_sign(rec.get("stkqy_irds")),
                         _parse_float_with_sign(rec.get("stkrt")),
                         _parse_float_with_sign(rec.get("stkrt_irds")),
                         (rec.get("report_resn") or "").replace("\n", " ")[:200],
                         now_iso),
                    )
                    if cur.rowcount > 0:
                        inserted += 1
                except Exception:
                    pass
    conn.commit()
    conn.close()

    return {
        "disclosures_found": len(disc),
        "corps_fetched": fetched_corps,
        "rows_inserted": inserted,
        "fetched_at": now_iso,
    }


async def collect_dart_10pct_insiders(days: int = 14) -> dict:
    """DART 임원·10%↑ (D002) 최근 N일 수집.

    list.json → unique corp_code → elestock.json → DB.
    elestock는 보고서당 여러 row (보고자별 + 일자별) → seq로 구분.
    """
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_dart_change_tables(db_path)
    direct_map, name_list = _build_name_to_symbol_map(db_path)

    disc = await _dart_list_disclosures("D002", days)
    if not disc:
        return {"error": "D002 검색 결과 없음"}

    period_rcepts = {it.get("rcept_no") for it in disc if it.get("rcept_no")}
    corp_codes = {}
    for it in disc:
        cc = it.get("corp_code")
        if cc:
            corp_codes[cc] = it.get("corp_name", "")

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    now_iso = datetime.now(KST).isoformat()
    inserted = 0
    fetched_corps = 0

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        for cc, cname in corp_codes.items():
            d = await _dart_get(s, "elestock.json", {"corp_code": cc})
            await asyncio.sleep(0.1)
            if d.get("status") != "000":
                continue
            fetched_corps += 1
            # 같은 rcept_no 내 row를 seq 부여
            seq_counter = {}
            for rec in d.get("list", []) or []:
                rcept_no = rec.get("rcept_no", "")
                if not rcept_no or rcept_no not in period_rcepts:
                    continue
                seq = seq_counter.get(rcept_no, 0)
                seq_counter[rcept_no] = seq + 1
                sym = _match_company_to_symbol(cname, direct_map, name_list)
                try:
                    cur = conn.execute(
                        """INSERT OR REPLACE INTO dart_10pct_insiders
                           (rcept_no, seq, rcept_dt, corp_code, company, symbol,
                            repror, shrholdr_role, stkqy, stkqy_irds, stkrt, stkrt_irds,
                            collected_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (rcept_no, seq, rec.get("rcept_dt", ""), cc, cname, sym,
                         rec.get("repror", ""),
                         rec.get("isu_main_shrholdr", "") or "임원",
                         _parse_int_with_sign(rec.get("sp_stock_lmp_cnt")),
                         _parse_int_with_sign(rec.get("sp_stock_lmp_irds_cnt")),
                         _parse_float_with_sign(rec.get("sp_stock_lmp_rate")),
                         _parse_float_with_sign(rec.get("sp_stock_lmp_irds_rate")),
                         now_iso),
                    )
                    if cur.rowcount > 0:
                        inserted += 1
                except Exception:
                    pass
    conn.commit()
    conn.close()

    return {
        "disclosures_found": len(disc),
        "corps_fetched": fetched_corps,
        "rows_inserted": inserted,
        "fetched_at": now_iso,
    }


async def collect_wi_changes() -> dict:
    """whale-insight 5%룰 변동 + 10%↑ 보유자 매매 데이터 미러링 (fallback)."""
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_wi_change_tables(db_path)

    direct_map, name_list = _build_name_to_symbol_map(db_path)

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    now_iso = datetime.now(KST).isoformat()

    # ── 5%룰 변동 (major_stock) ──
    major = await _fetch_wi_js_array(WI_MAJOR_JS, "MAJOR_DATA")
    major_inserted = 0
    for rec in major:
        try:
            sym = _match_company_to_symbol(rec.get("company", ""), direct_map, name_list)
            cur = conn.execute(
                """INSERT OR REPLACE INTO wi_5pct_changes
                   (report_date, company, symbol, stkqy, stkqy_irds,
                    stkrt, stkrt_irds, report_resn, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.get("date", ""),
                    rec.get("company", ""),
                    sym,
                    _parse_int_with_sign(rec.get("stkqy")),
                    _parse_int_with_sign(rec.get("stkqy_irds")),
                    _parse_float_with_sign(rec.get("stkrt")),
                    _parse_float_with_sign(rec.get("stkrt_irds")),
                    rec.get("report_resn", ""),
                    now_iso,
                ),
            )
            if cur.rowcount > 0:
                major_inserted += 1
        except Exception:
            pass

    # ── 10%↑ 보유자 매매 (elestock) ──
    ele = await _fetch_wi_js_array(WI_ELE_JS, "ELESTOCK_DATA")
    ele_inserted = 0
    for rec in ele:
        try:
            sym = _match_company_to_symbol(rec.get("company", ""), direct_map, name_list)
            cur = conn.execute(
                """INSERT OR REPLACE INTO wi_10pct_insiders
                   (report_date, company, symbol, stkqy, stkqy_irds,
                    stkrt, stkrt_irds, shrholdr_role, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.get("date", ""),
                    rec.get("company", ""),
                    sym,
                    _parse_int_with_sign(rec.get("stkqy")),
                    _parse_int_with_sign(rec.get("stkqy_irds")),
                    _parse_float_with_sign(rec.get("stkrt")),
                    _parse_float_with_sign(rec.get("stkrt_irds")),
                    rec.get("isu_main_shrholdr", ""),
                    now_iso,
                ),
            )
            if cur.rowcount > 0:
                ele_inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()

    return {
        "major_total": len(major),
        "major_inserted": major_inserted,
        "ele_total": len(ele),
        "ele_inserted": ele_inserted,
        "fetched_at": now_iso,
    }


def fetch_nps_kr_full_holdings(top: int = 30) -> dict:
    """NPS KR 풀 포트 조회 (가장 최근 snapshot, 비중 내림차순)."""
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_kr_full_table(db_path)
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 268435456;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.row_factory = _s.Row
    snap_row = conn.execute(
        "SELECT snapshot_date FROM nps_kr_full_holdings ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    if not snap_row:
        conn.close()
        return {"error": "데이터 없음. collect_nps_kr_full_from_whale_insight() 호출 필요"}
    snap = snap_row["snapshot_date"]
    rows = conn.execute(
        """SELECT name, symbol, weight_pct, valuation_eok, change_pct,
                  share_prev_pct, share_curr_pct, data_missing, quarter_label
           FROM nps_kr_full_holdings
           WHERE snapshot_date = ?
           ORDER BY weight_pct DESC""",
        (snap,),
    ).fetchall()
    total_eok = sum(r["valuation_eok"] for r in rows)
    conn.close()
    out = []
    for r in rows[:top]:
        out.append({
            "name": r["name"],
            "symbol": r["symbol"] or "",
            "weight_pct": float(r["weight_pct"] or 0),
            "valuation_eok": int(r["valuation_eok"] or 0),
            "change_pct": float(r["change_pct"] or 0),
            "share_prev_pct": float(r["share_prev_pct"] or 0),
            "share_curr_pct": float(r["share_curr_pct"] or 0),
            "data_missing": bool(r["data_missing"]),
            "share_change_p": (
                float(r["share_curr_pct"]) - float(r["share_prev_pct"])
                if not r["data_missing"] else None
            ),
        })
    return {
        "snapshot_date": snap,
        "quarter_label": rows[0]["quarter_label"] if rows else "",
        "total_holdings": len(rows),
        "total_valuation_eok": total_eok,
        "rows": out,
        "fetched_at": datetime.now(KST).isoformat(),
    }
