"""DART 공시/사업보고서/내부자거래 API."""
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


async def search_dart_disclosures(days_back=1):
    """최근 N일 공시 검색 (전체 기업)"""
    if not DART_API_KEY:
        return []

    now = datetime.now(KST)
    end_date = now.strftime("%Y%m%d")
    start_date = (now - timedelta(days=days_back)).strftime("%Y%m%d")

    url = f"{DART_BASE_URL}/list.json"
    params = {
        "crtfc_key": DART_API_KEY,
        "bgn_de": start_date,
        "end_de": end_date,
        "page_count": 100,
        "sort": "date",
        "sort_mth": "desc",
    }

    try:
        session = _get_session()
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "000":
                    return data.get("list", [])
    except Exception as e:
        print(f"DART API 오류: {e}")
    return []


def filter_important_disclosures(disclosures, watchlist_names):
    """관심 기업의 공시 전부 반환 (키워드 필터 제거, 나중에 필요시 추가)."""
    return [d for d in disclosures
            if any(name in d.get("corp_name", "") for name in watchlist_names if name)]


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART corp_code 매핑 & 재무 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━
DART_CORP_MAP_FILE = f"{_DATA_DIR}/dart_corp_map.json"


async def build_dart_corp_map(universe: dict) -> dict:
    """corpCode.xml zip 다운로드 → stock_code ↔ corp_code 매핑 생성 후 저장."""
    import zipfile, io, traceback as _tb
    from xml.etree import ElementTree as ET

    if not DART_API_KEY:
        print("[DART] build_dart_corp_map: DART_API_KEY 미설정")
        return {}
    url = f"{DART_BASE_URL}/corpCode.xml?crtfc_key={DART_API_KEY}"
    print(f"[DART] corpCode.xml 다운로드 시작: {url[:60]}...")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(url) as resp:
                print(f"[DART] corpCode.xml HTTP {resp.status}")
                raw = await resp.read()
        print(f"[DART] 다운로드 완료: {len(raw)} bytes")
        zf = zipfile.ZipFile(io.BytesIO(raw))
        print(f"[DART] zip 파일 목록: {zf.namelist()}")
        xml_data = zf.read("CORPCODE.xml")
        root = ET.fromstring(xml_data)

        mapping = {}
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code  = (item.findtext("corp_code")  or "").strip()
            if stock_code and stock_code in universe:
                mapping[stock_code] = corp_code

        try:
            with open(DART_CORP_MAP_FILE, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False)
            print(f"[DART] corp_map 저장 완료: {DART_CORP_MAP_FILE}")
        except Exception as save_e:
            print(f"[DART] corp_map 저장 실패 (메모리에서 계속): {save_e}")
        print(f"[DART] corp_map 생성: {len(mapping)}개 종목")
        return mapping
    except Exception as e:
        print(f"[DART] corp_map 생성 실패: {e}\n{_tb.format_exc()}")
        return {}


async def get_dart_corp_map(universe: dict) -> dict:
    """dart_corp_map.json 로드. 파일 없으면 빈 dict 반환 (다운로드 시도 안 함).

    파일 탐색 순서:
      1. /data/dart_corp_map.json  (DATA_DIR 운영 오버라이드)
      2. <레포 루트>/dart_corp_map.json  (커밋 파일)
    """
    import os
    candidates = [
        DART_CORP_MAP_FILE,
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "dart_corp_map.json"),
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[DART] corp_map 로드: {path} ({len(data)}종목)")
                return data
        except Exception as e:
            print(f"[DART] corp_map 로드 실패 ({path}): {e}")
    print("[DART] dart_corp_map.json 없음 — dart_op_growth 사용 불가")
    return {}


async def dart_quarterly_op(corp_code: str, year: int, quarter: int) -> dict | None:
    """DART fnlttSinglAcntAll로 연간/분기 영업이익·매출 조회.

    quarter: 1=1분기, 2=반기, 3=3분기, 4=사업보고서(연간)
    반환: {"year", "quarter", "op_profit"(억원), "revenue"(억원)} 또는 None
    """
    reprt_map = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
    reprt_code = reprt_map.get(quarter, "11011")
    url = f"{DART_BASE_URL}/fnlttSinglAcntAll.json"

    async def _fetch(fs_div: str):
        params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code,
                  "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": fs_div}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(url, params=params) as resp:
                return await resp.json(content_type=None)

    try:
        data = await _fetch("CFS")
        if data.get("status") != "000":
            data = await _fetch("OFS")
        if data.get("status") != "000":
            return None

        op_profit = revenue = None
        for item in data.get("list", []):
            acct    = (item.get("account_nm") or "").strip()
            amt_str = (item.get("thstrm_amount") or "").replace(",", "").replace(" ", "")
            if not amt_str:
                continue
            try:
                amt = int(amt_str) // 100_000_000  # 원 → 억원
            except Exception:
                continue
            if acct in ("영업이익", "영업이익(손실)") and op_profit is None:
                op_profit = amt
            elif acct in ("매출액", "수익(매출액)") and revenue is None:
                revenue = amt

        if op_profit is None:
            return None
        return {"year": year, "quarter": quarter, "op_profit": op_profit, "revenue": revenue}
    except Exception as e:
        print(f"[DART] dart_quarterly_op {corp_code} {year}Q{quarter} 오류: {e}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 전체 재무제표 파서 (F-Score / M-Score / FCF 용)
# fnlttSinglAcntAll 1회 호출로 PL/BS/CF 전체 계정 파싱
# CFS(연결) 우선, 없으면 OFS(별도) fallback. 지배주주 귀속 순이익/자본도 파싱.
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# 계정명 매칭용 토큰 (account_nm에 대한 "in" 포함 검사 — 변종 대응)
# 주의: 더 구체적인 패턴을 먼저 배치 (예: "매출총이익" 먼저, "매출액"은 별도 처리)
_DART_ACCT_TOKENS = {
    # 손익 (sj_div 주로 'IS' 또는 'CIS')
    "gross_profit":     [("매출총이익",), ("매출총손실",)],
    "operating_profit": [("영업이익",), ("영업손실",)],
    "cost_of_sales":    [("매출원가",)],
    "sga":              [("판매비와관리비",), ("판매비와 관리비",), ("판관비",)],
    # 당기순이익: 지배/비지배 분리 필요 → 별도 처리
    # 대차 (sj_div 주로 'BS')
    "current_assets":   [("유동자산",)],
    "total_assets":     [("자산총계",)],
    "current_liab":     [("유동부채",)],
    "total_liab":       [("부채총계",)],
    "total_equity":     [("자본총계",)],
    "capital":          [("자본금",)],
    "receivables":      [("매출채권",)],
    "inventory":        [("재고자산",)],
    # 현금흐름 (sj_div 주로 'CF')
    "cfo":              [("영업활동",)],   # '영업활동현금흐름' / '영업활동으로 인한 현금흐름'
    # CapEx / 감가상각 / 무형자산상각 → 별도 처리 (sj='CF' 한정)
}


def _dart_amt_to_int(amt_str: str) -> int | None:
    """DART amount 문자열 → int (원 단위)."""
    if not amt_str:
        return None
    s = str(amt_str).replace(",", "").replace(" ", "").strip()
    if not s or s == "-":
        return None
    # 음수는 괄호로 오는 경우 처리
    neg = False
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
        neg = True
    try:
        v = int(s)
        return -v if neg else v
    except ValueError:
        return None


def _dart_acct_match(acct_nm: str, tokens_list) -> bool:
    """account_nm이 토큰 조합 중 하나에 모두 매치되면 True.
    tokens_list: [(tok1,), (tok1, tok2), ...] — 각 튜플은 AND, 튜플 간은 OR.
    """
    for tokens in tokens_list:
        if all(t in acct_nm for t in tokens):
            return True
    return False


async def dart_quarterly_full(corp_code: str, year: int, quarter: int,
                              session: aiohttp.ClientSession | None = None) -> dict | None:
    """DART fnlttSinglAcntAll 1회 호출로 PL/BS/CF 전체 파싱.

    quarter: 1=1분기, 2=반기, 3=3분기, 4=사업보고서(연간)
    CFS(연결) 우선, status!='000'이면 OFS(별도) fallback.

    반환 dict (값은 원 단위 int, 없으면 None):
      report_period (YYYYMM),
      revenue, cost_of_sales, gross_profit, operating_profit,
      net_income, net_income_parent,
      sga,
      current_assets, total_assets, current_liab, total_liab,
      capital, total_equity, equity_parent,
      receivables, inventory,
      cfo, capex, fcf, depreciation,
      shares_out,
      fs_source ('CFS' | 'OFS')
    """
    if not DART_API_KEY or not corp_code:
        return None
    reprt_map = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
    reprt_code = reprt_map.get(quarter)
    if not reprt_code:
        return None
    url = f"{DART_BASE_URL}/fnlttSinglAcntAll.json"

    async def _fetch(sess, fs_div: str):
        params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code,
                  "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": fs_div}
        async with sess.get(url, params=params,
                            timeout=aiohttp.ClientTimeout(total=15)) as resp:
            return await resp.json(content_type=None)

    own_session = session is None
    sess = session or aiohttp.ClientSession()
    try:
        data = await _fetch(sess, "CFS")
        fs_source = "CFS"
        if data.get("status") != "000" or not data.get("list"):
            data = await _fetch(sess, "OFS")
            fs_source = "OFS"
        if data.get("status") != "000" or not data.get("list"):
            return None

        items = data.get("list", [])
        out = {
            "report_period": f"{year}{quarter * 3:02d}",
            "fs_source": fs_source,
        }
        # 표준 계정 first-match 파싱
        fields = ["gross_profit", "operating_profit", "cost_of_sales", "sga",
                  "current_assets", "total_assets", "current_liab", "total_liab",
                  "total_equity", "capital", "receivables", "inventory", "cfo"]
        for f in fields:
            out[f] = None

        # 당기순이익 / 지배귀속 / CapEx / 감가상각은 별도 처리
        net_income = None
        net_income_parent = None
        equity_parent = None
        capex = None
        dep_pt = None
        dep_intan = None
        revenue = None

        for item in items:
            acct = (item.get("account_nm") or "").strip()
            sj = (item.get("sj_div") or "").strip()   # IS/CIS/BS/CF
            amt = _dart_amt_to_int(item.get("thstrm_amount"))
            if amt is None:
                continue

            # 매출액: 변종 대응 ("매출액" / "매출" / "영업수익" / "수익(매출액)")
            # 매출총이익/매출원가 제외 (포함어)
            if revenue is None and sj in ("IS", "CIS"):
                if acct in ("매출액", "매출", "수익(매출액)", "영업수익") \
                        or acct.startswith("매출액"):
                    revenue = amt
                    continue

            # 표준 계정 (first-match 보존)
            for key, tokens_list in _DART_ACCT_TOKENS.items():
                if out.get(key) is None and _dart_acct_match(acct, tokens_list):
                    # sj 보조 검증 — cfo는 CF, BS 계정은 BS, PL 계정은 IS/CIS
                    if key == "cfo" and sj != "CF":
                        continue
                    if key in ("current_assets", "total_assets", "current_liab",
                               "total_liab", "total_equity", "capital",
                               "receivables", "inventory") and sj != "BS":
                        continue
                    if key in ("gross_profit", "operating_profit", "cost_of_sales",
                               "sga") and sj not in ("IS", "CIS"):
                        continue
                    out[key] = amt
                    break

            # 감가상각비 / 무형자산상각비 (CF 간접법 조정항목)
            if sj == "CF":
                if dep_pt is None and "감가상각" in acct:
                    dep_pt = amt
                if dep_intan is None and ("무형자산상각" in acct or "무형자산 상각" in acct):
                    dep_intan = amt

            # 지배주주 귀속 순이익 — IS 만 (CIS는 포괄손익이라 제외)
            # 계정명 변종: "지배기업 소유주지분", "지배기업 소유지분", "지배기업소유주지분"
            if net_income_parent is None and sj == "IS":
                if "지배기업" in acct and "지분" in acct:
                    net_income_parent = amt
                    continue

            # 당기순이익 (전체, 지배+비지배 합산) — IS 우선, 없으면 CIS
            # 변종: "당기순이익", "연결당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"
            # CIS의 "총포괄이익/총포괄손익"은 제외 (별도 지표)
            if net_income is None and sj in ("IS", "CIS"):
                if ("당기순이익" in acct or "분기순이익" in acct or "반기순이익" in acct) \
                        and "지배" not in acct and "비지배" not in acct \
                        and "포괄" not in acct:
                    net_income = amt
                    continue

            # 지배주주 귀속 자본 — BS (계정명 변종 포함)
            if equity_parent is None and sj == "BS":
                if "지배기업" in acct and "지분" in acct:
                    equity_parent = amt
                    continue

            # CapEx — CF의 '유형자산 취득' or '유형자산의 증가'
            if capex is None and sj == "CF":
                if "유형자산" in acct and ("취득" in acct or "증가" in acct):
                    capex = abs(amt)
                    continue

        # 감가상각 합산
        depreciation = None
        if dep_pt is not None or dep_intan is not None:
            depreciation = (dep_pt or 0) + (dep_intan or 0)

        out["revenue"] = revenue
        out["net_income"] = net_income
        out["net_income_parent"] = net_income_parent
        out["equity_parent"] = equity_parent
        out["capex"] = capex
        out["depreciation"] = depreciation

        # FCF = CFO - abs(CapEx)
        if out.get("cfo") is not None:
            cx = capex if capex is not None else 0
            out["fcf"] = out["cfo"] - cx
        else:
            out["fcf"] = None

        # 발행주식수 — fnlttSinglAcntAll에는 없음 (별도 API 필요, Phase1 범위 밖)
        out["shares_out"] = None

        # 단위 변환: 원 → 억원 (기존 financial_quarterly 컬럼 단위와 통일)
        _MONEY_KEYS = ("revenue", "cost_of_sales", "gross_profit", "operating_profit",
                       "sga", "net_income", "net_income_parent",
                       "current_assets", "total_assets", "current_liab", "total_liab",
                       "capital", "total_equity", "equity_parent",
                       "receivables", "inventory",
                       "cfo", "capex", "fcf", "depreciation")
        for k in _MONEY_KEYS:
            v = out.get(k)
            if v is not None:
                out[k] = v // 100_000_000

        return out
    except Exception as e:
        print(f"[DART] dart_quarterly_full {corp_code} {year}Q{quarter} 오류: {e}")
        return None
    finally:
        if own_session:
            await sess.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 주식 총수 (stockTotqySttus: 보통주 발행주식수)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def dart_shares_outstanding(corp_code: str, year: int, quarter: int,
                                   session: aiohttp.ClientSession | None = None
                                   ) -> int | None:
    """DART 주식 총수 API. 보고서 기준 보통주 발행주식수(주) 반환.

    quarter: 1/2/3/4 (reprt_code 11013/11012/11014/11011)
    우선주/기타주 제외, 보통주만 반환.
    응답 필드: se='보통주' row의 istc_totqy(발행주식총수).

    DART 분당 1000콜 제한 → 호출자가 0.067초/콜 sleep 삽입.
    """
    if not DART_API_KEY or not corp_code:
        return None
    reprt_map = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
    reprt_code = reprt_map.get(quarter)
    if not reprt_code:
        return None

    url = f"{DART_BASE_URL}/stockTotqySttus.json"
    params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code,
              "bsns_year": str(year), "reprt_code": reprt_code}

    own_session = session is None
    sess = session or aiohttp.ClientSession()
    try:
        async with sess.get(url, params=params,
                            timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json(content_type=None)
        if data.get("status") != "000":
            return None
        items = data.get("list") or []
        for it in items:
            se = (it.get("se") or "").strip()
            # "보통주" 우선 매칭 (일부 회사는 "보통주식" 변종 가능성 대비)
            if se == "보통주" or se.startswith("보통주"):
                # istc_totqy: 발행주식총수, totqy: (구버전) 총수
                raw = it.get("istc_totqy") or it.get("totqy")
                v = _dart_amt_to_int(raw)
                if v is not None and v > 0:
                    return v
        return None
    except Exception as e:
        print(f"[DART] dart_shares_outstanding {corp_code} {year}Q{quarter} 오류: {e}")
        return None
    finally:
        if own_session:
            await sess.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 내부자 거래 (elestock.json: 임원·주요주주 특정증권등 소유상황보고서)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
DB_PATH_FOR_INSIDER = f"{_DATA_DIR}/stock.db"


async def kis_elestock(corp_code: str) -> list:
    """DART 임원·주요주주 소유보고서 조회.

    Returns: [{rcept_no, rcept_dt, repror, isu_exctv_ofcps,
               sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt, ...}, ...]
    """
    if not DART_API_KEY or not corp_code:
        return []
    url = f"{DART_BASE_URL}/elestock.json"
    params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if data.get("status") != "000":
                    return []
                return data.get("list", [])
    except Exception as e:
        print(f"[DART elestock] {corp_code} 오류: {e}")
        return []


def _to_int_safe(v) -> int:
    if v is None:
        return 0
    s = str(v).replace(",", "").replace("-", "-").strip()
    if not s or s == "-":
        return 0
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return 0


def _to_float_safe(v) -> float:
    if v is None:
        return 0.0
    s = str(v).replace(",", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def upsert_insider_transactions(symbol: str, corp_code: str, records: list) -> int:
    """elestock 응답을 insider_transactions 테이블에 UPSERT. 신규 row 수 반환."""
    import sqlite3
    if not records:
        return 0
    conn = sqlite3.connect(DB_PATH_FOR_INSIDER, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA cache_size = -65536")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 268435456")
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    try:
        for r in records:
            rcept_no = (r.get("rcept_no") or "").strip()
            repror = (r.get("repror") or "").strip()
            if not rcept_no or not repror:
                continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO insider_transactions "
                    "(rcept_no, symbol, corp_code, rcept_dt, repror, ofcps, rgist, "
                    " main_shrholdr, stock_cnt, stock_irds_cnt, stock_rate, stock_irds_rate, collected_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rcept_no, symbol, corp_code,
                        (r.get("rcept_dt") or "").strip(),
                        repror,
                        (r.get("isu_exctv_ofcps") or "").strip(),
                        (r.get("isu_exctv_rgist_at") or "").strip(),
                        (r.get("isu_main_shrholdr") or "").strip(),
                        _to_int_safe(r.get("sp_stock_lmp_cnt")),
                        _to_int_safe(r.get("sp_stock_lmp_irds_cnt")),
                        _to_float_safe(r.get("sp_stock_lmp_rate")),
                        _to_float_safe(r.get("sp_stock_lmp_irds_rate")),
                        now_str,
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
            except Exception as e:
                print(f"[insider upsert] {symbol} {rcept_no} {repror}: {e}")
        conn.commit()
    finally:
        conn.close()
    return inserted


def aggregate_insider_cluster(symbol: str, days: int = 30) -> dict:
    """최근 N일간 해당 종목 내부자 매수/매도 집계.

    Returns: {buy_names: set, sell_names: set, buy_qty, sell_qty, buyers, sellers, recent: [...]}
    """
    import sqlite3
    conn = sqlite3.connect(DB_PATH_FOR_INSIDER, timeout=30)
    conn.execute("PRAGMA cache_size = -65536")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 268435456")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM insider_transactions "
        "WHERE symbol=? AND rcept_dt>=? ORDER BY rcept_dt DESC",
        (symbol, cutoff),
    ).fetchall()
    conn.close()

    buy_names, sell_names = set(), set()
    buy_qty = sell_qty = 0
    recent = []
    for r in rows:
        name = r["repror"]
        delta = r["stock_irds_cnt"] or 0
        if delta > 0:
            buy_names.add(name)
            buy_qty += delta
        elif delta < 0:
            sell_names.add(name)
            sell_qty += abs(delta)
        recent.append({
            "date": r["rcept_dt"], "name": name, "ofcps": r["ofcps"],
            "delta": delta, "total": r["stock_cnt"], "rate": r["stock_rate"],
        })
    return {
        "symbol": symbol,
        "days": days,
        "buyers": len(buy_names),
        "sellers": len(sell_names),
        "buy_qty": buy_qty,
        "sell_qty": sell_qty,
        "buy_names": sorted(buy_names),
        "sell_names": sorted(sell_names),
        "recent": recent,
    }


async def collect_insider_for_tickers(tickers: list, corp_map: dict) -> dict:
    """워치리스트 종목들의 내부자 보고 수집 → DB 저장.
    Returns: {symbol: {new: int, total: int}}
    """
    import asyncio
    result = {}
    for sym in tickers:
        corp_code = corp_map.get(sym, "")
        if not corp_code:
            continue
        records = await kis_elestock(corp_code)
        new_cnt = upsert_insider_transactions(sym, corp_code, records)
        result[sym] = {"new": new_cnt, "total": len(records)}
        await asyncio.sleep(0.3)  # DART rate limit 여유
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 사업보고서 본문 저장
# ━━━━━━━━━━━━━━━━━━━━━━━━━
DART_REPORTS_DIR = f"{_DATA_DIR}/dart_reports"
CORP_CODES_FILE  = f"{_DATA_DIR}/corp_codes.json"


async def load_corp_codes() -> dict:
    """corp_codes.json 로드. 1일 1회 갱신 (캐시)."""
    if os.path.exists(CORP_CODES_FILE):
        try:
            mtime = os.path.getmtime(CORP_CODES_FILE)
            age_hours = (datetime.now(KST).timestamp() - mtime) / 3600
            if age_hours < 24:
                with open(CORP_CODES_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                if data:
                    print(f"[DART] corp_codes 캐시 사용 ({len(data)}종목, {age_hours:.1f}h)")
                    return data
        except Exception as e:
            print(f"[DART] corp_codes 캐시 로드 실패: {e}")

    # 캐시 만료 또는 없음 → corpCode.xml 다운로드
    return await _download_corp_codes()


async def _download_corp_codes() -> dict:
    """OpenDART corpCode.xml zip → ticker↔corp_code 매핑 생성."""
    import zipfile, io
    if not DART_API_KEY:
        print("[DART] corp_codes: DART_API_KEY 미설정")
        return {}
    url = f"{DART_BASE_URL}/corpCode.xml?crtfc_key={DART_API_KEY}"
    print(f"[DART] corpCode.xml 다운로드 시작...")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(url) as resp:
                if resp.status != 200:
                    print(f"[DART] corpCode.xml HTTP {resp.status}")
                    return {}
                raw = await resp.read()
        from xml.etree import ElementTree as ET
        zf = zipfile.ZipFile(io.BytesIO(raw))
        xml_data = zf.read("CORPCODE.xml")
        root = ET.fromstring(xml_data)

        mapping = {}
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code  = (item.findtext("corp_code")  or "").strip()
            corp_name  = (item.findtext("corp_name")  or "").strip()
            if stock_code and corp_code:
                mapping[stock_code] = {"corp_code": corp_code, "corp_name": corp_name}

        os.makedirs(os.path.dirname(CORP_CODES_FILE), exist_ok=True)
        with open(CORP_CODES_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False)
        print(f"[DART] corp_codes 저장: {len(mapping)}종목")
        return mapping
    except Exception as e:
        print(f"[DART] corp_codes 다운로드 실패: {e}")
        return {}


def _report_name_priority(report_nm: str) -> int:
    """보고서명 우선순위. 낮을수록 우선. 원본 > 정정 > 첨부정정."""
    nm = (report_nm or "").strip()
    if nm == "사업보고서":
        return 0
    if nm.startswith("[정정]"):
        return 1
    if nm.startswith("[첨부정정]"):
        return 2
    return 3


async def search_dart_reports(corp_code: str, days_back: int = 365) -> list:
    """OpenDART list.json으로 사업보고서(A001) 검색.

    결과를 보고서명 우선순위로 정렬: 사업보고서 > [정정] > [첨부정정].
    """
    if not DART_API_KEY:
        return []
    now = datetime.now(KST)
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bgn_de": (now - timedelta(days=days_back)).strftime("%Y%m%d"),
        "end_de": now.strftime("%Y%m%d"),
        "pblntf_ty": "A",
        "pblntf_detail_ty": "A001",
        "page_count": 10,
        "sort": "date",
        "sort_mth": "desc",
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(f"{DART_BASE_URL}/list.json", params=params) as resp:
                if resp.status != 200:
                    print(f"[DART] list.json HTTP {resp.status} for {corp_code}")
                    return []
                data = await resp.json(content_type=None)
                status = data.get("status", "")
                if status == "000":
                    results = data.get("list", [])
                    # 원본 사업보고서 우선, 정정/첨부정정 후순위
                    results.sort(key=lambda r: _report_name_priority(r.get("report_nm", "")))
                    names = [r.get("report_nm", "") for r in results[:5]]
                    print(f"[DART] list.json {corp_code}: {len(results)}건 → {names}")
                    return results
                else:
                    print(f"[DART] list.json {corp_code}: status={status} msg={data.get('message','')}")
    except Exception as e:
        print(f"[DART] report search error ({corp_code}): {e}")
    return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 증분 수집용 — 최근 N일 정기공시(pblntf_ty=A) 전체 조회
# 기존 search_dart_reports는 특정 corp_code의 사업보고서(A001) 본문 수집용이라 분리.
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_RPT_PERIOD_RE = re.compile(r"(\d{4})\.(\d{2})")


def _parse_rpt_nm(rpt_nm: str) -> tuple[str | None, str | None]:
    """rpt_nm 문자열에서 (report_period YYYYMM, report_type) 파싱.

    규칙:
      - "[기재정정]" / "[첨부정정]" 접두가 붙어 있으면 (None, None) 반환 — 원본 공시만 수집.
      - "사업보고서 (2024.12)"  → ("202412", "annual")
      - "반기보고서 (2024.06)"  → ("202406", "semi")
      - "분기보고서 (2024.03)"  → ("202403", "quarterly")
      - "분기보고서 (2024.09)"  → ("202409", "quarterly")
      - 위 3유형이 아니면 (None, None).
    """
    nm = (rpt_nm or "").strip()
    if not nm:
        return None, None
    # 정정 공시는 skip (원본이 이미 DB에 있거나, 곧 원본 공시가 나올 것)
    if nm.startswith("[기재정정]") or nm.startswith("[첨부정정]") or nm.startswith("[정정]"):
        return None, None

    # 유형 분류 (첫 토큰만 본다; 일부는 "[첨부추가]" 등 변종이 뒤에 붙을 수 있음)
    if "사업보고서" in nm:
        rtype = "annual"
    elif "반기보고서" in nm:
        rtype = "semi"
    elif "분기보고서" in nm:
        rtype = "quarterly"
    else:
        return None, None

    m = _RPT_PERIOD_RE.search(nm)
    if not m:
        # 괄호 내 날짜 없음 — 비정형. 일단 스킵.
        return None, None
    year, month = m.group(1), m.group(2)
    # month 검증: 03/06/09/12만 유효
    if month not in ("03", "06", "09", "12"):
        return None, None
    # 유형-월 정합성 보조 검증 (사업=12, 반기=06)
    if rtype == "annual" and month != "12":
        return None, None
    if rtype == "semi" and month != "06":
        return None, None
    if rtype == "quarterly" and month not in ("03", "09"):
        return None, None
    return f"{year}{month}", rtype


async def search_dart_periodic_new(days: int = 7,
                                    session: aiohttp.ClientSession | None = None) -> list[dict]:
    """DART list.json 지난 N일 정기공시(pblntf_ty=A) 조회.

    기존 search_dart_reports(corp_code별 A001 본문 수집)와 분리 —
    전체 공시판에서 사업/반기/분기보고서 모두 긁어오는 증분 수집용.
    본문은 가져오지 않음 (corp_code + rcept_dt + rpt_nm만 필요).

    Args:
        days: 오늘 KST 기준 N일 전부터 조회 (기본 7).
        session: 재사용할 aiohttp 세션. None이면 내부에서 생성.

    Returns:
        [{"corp_code", "ticker" (stock_code, 없을 수 있음), "corp_name",
          "rcept_no", "rcept_dt", "rpt_nm",
          "report_period" (YYYYMM), "report_type" ("quarterly"|"semi"|"annual")}]
        정정공시([기재정정]/[첨부정정])는 제외.
    """
    if not DART_API_KEY:
        print("[DART-Incr] DART_API_KEY 미설정 — 빈 결과 반환")
        return []

    now = datetime.now(KST)
    end_de = now.strftime("%Y%m%d")
    bgn_de = (now - timedelta(days=max(days, 1))).strftime("%Y%m%d")
    url = f"{DART_BASE_URL}/list.json"

    own_session = session is None
    sess = session or aiohttp.ClientSession()

    results: list[dict] = []
    try:
        page_no = 1
        total_pages = 1
        while page_no <= total_pages:
            params = {
                "crtfc_key": DART_API_KEY,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "pblntf_ty": "A",
                "page_count": 100,
                "page_no": page_no,
                "sort": "date",
                "sort_mth": "desc",
            }
            try:
                async with sess.get(url, params=params,
                                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        print(f"[DART-Incr] list.json HTTP {resp.status} page={page_no}")
                        break
                    data = await resp.json(content_type=None)
            except Exception as e:
                print(f"[DART-Incr] list.json 호출 오류 page={page_no}: {e}")
                break

            status = data.get("status", "")
            if status != "000":
                # 013(조회된 데이터 없음) 포함 — 조용히 종료
                if status not in ("000", "013"):
                    print(f"[DART-Incr] list.json status={status} "
                          f"msg={data.get('message','')}")
                break

            page_list = data.get("list", []) or []
            total_pages = int(data.get("total_page", 1) or 1)

            for item in page_list:
                rpt_nm = (item.get("report_nm") or "").strip()
                period, rtype = _parse_rpt_nm(rpt_nm)
                if not period or not rtype:
                    continue
                corp_code = (item.get("corp_code") or "").strip()
                if not corp_code:
                    continue
                results.append({
                    "corp_code":     corp_code,
                    "ticker":        (item.get("stock_code") or "").strip(),
                    "corp_name":     (item.get("corp_name") or "").strip(),
                    "rcept_no":      (item.get("rcept_no") or "").strip(),
                    "rcept_dt":      (item.get("rcept_dt") or "").strip(),
                    "rpt_nm":        rpt_nm,
                    "report_period": period,
                    "report_type":   rtype,
                })

            # 과도한 페이징 방지 안전장치 (N일 × 100건/페이지 = 수천 페이지 이론상)
            if page_no >= 50:
                print(f"[DART-Incr] 50 페이지 상한 도달 — 중단 (page={page_no})")
                break
            page_no += 1

        print(f"[DART-Incr] list.json {bgn_de}~{end_de}: "
              f"원본 공시 {len(results)}건 (정정/비정기 제외 후)")
        return results
    finally:
        if own_session:
            await sess.close()


async def fetch_dart_document(rcept_no: str) -> str:
    """OpenDART document.xml → ZIP 내 HTML 파일들 → 순수 텍스트.

    document.xml 응답은 ZIP 파일 (다수 HTML 조각) 또는 XML wrapper.
    ZIP인 경우 내부 모든 텍스트 파일을 합쳐 추출.
    """
    import zipfile, io
    if not DART_API_KEY:
        return ""
    url = f"{DART_BASE_URL}/document.xml"
    params = {"crtfc_key": DART_API_KEY, "rcept_no": rcept_no}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
            async with s.get(url, params=params) as resp:
                status = resp.status
                ct = resp.headers.get("Content-Type", "")
                if status != 200:
                    print(f"[DART] document.xml HTTP {status} ct={ct} for {rcept_no}")
                    return ""
                raw = await resp.read()

        size_kb = len(raw) / 1024
        magic = raw[:4].hex() if len(raw) >= 4 else "empty"
        print(f"[DART] document.xml 응답: rcept={rcept_no} size={size_kb:.1f}KB "
              f"ct={ct} magic={magic}")

        # OpenDART 에러 감지 — JSON 형태
        if b'"status"' in raw[:200] and b'"message"' in raw[:500]:
            try:
                err = json.loads(raw)
                print(f"[DART] document.xml JSON 에러: {err.get('status')} {err.get('message')}")
                return ""
            except Exception:
                pass

        # OpenDART 에러 감지 — XML 형태 (<result><status>...)
        if raw[:50].lstrip().startswith(b'<?xml') or b'<result>' in raw[:200]:
            try:
                from xml.etree import ElementTree as _ET
                _root = _ET.fromstring(raw)
                _status = _root.findtext("status") or _root.findtext(".//status") or ""
                _msg = _root.findtext("message") or _root.findtext(".//message") or ""
                if _status and _status != "000":
                    print(f"[DART] document.xml XML 에러: status={_status} msg={_msg}")
                    return ""
            except Exception:
                pass  # XML 파싱 실패 → 본문일 수 있음

        from bs4 import BeautifulSoup
        import re

        # ZIP 파일인지 확인 (PK 매직넘버)
        if raw[:2] == b'PK':
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
                all_names = zf.namelist()
                print(f"[DART] ZIP 내부 파일({len(all_names)}): "
                      f"{[n for n in all_names[:10]]}")

                html_parts = []
                # 이미지/CSS/폰트 제외, 나머지 텍스트 파일 모두 처리
                skip_ext = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg',
                            '.css', '.js', '.ttf', '.woff', '.woff2', '.eot',
                            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt',
                            '.zip', '.hwp', '.mp3', '.mp4', '.avi', '.ico'}
                for name in sorted(all_names):
                    ext = os.path.splitext(name)[1].lower()
                    if ext in skip_ext:
                        continue
                    try:
                        part_raw = zf.read(name)
                        part = part_raw.decode("utf-8", errors="replace")
                        soup = BeautifulSoup(part, "html.parser")
                        text = soup.get_text(separator="\n")
                        text = re.sub(r'\n{3,}', '\n\n', text).strip()
                        if len(text) > 20:
                            html_parts.append(text)
                    except Exception as ze:
                        print(f"[DART] ZIP 내 파일 처리 실패 ({name}): {ze}")

                full_text = "\n\n".join(html_parts)
                if len(full_text) < 100:
                    print(f"[DART] ZIP 본문 너무 짧음 ({len(full_text)}자, "
                          f"파일{len(html_parts)}개): {rcept_no}")
                    return ""
                print(f"[DART] ZIP 문서 추출 성공: {rcept_no} "
                      f"({len(full_text)}자, {len(html_parts)}파일)")
                return full_text
            except zipfile.BadZipFile:
                print(f"[DART] ZIP 파일 손상 (BadZipFile): {rcept_no} "
                      f"raw[:20]={raw[:20]}")
                return ""

        # ZIP이 아닌 경우 — XML/HTML 직접 응답
        html = raw.decode("utf-8", errors="replace")
        print(f"[DART] non-ZIP 응답 처리: {rcept_no} 앞100자={html[:100]!r}")
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n")
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        if len(text) < 100:
            print(f"[DART] non-ZIP 본문 너무 짧음 ({len(text)}자): {rcept_no}")
            return ""
        return text
    except Exception as e:
        print(f"[DART] document fetch error ({rcept_no}): {e}")
        return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 수시공시 본문 조회 + 요약 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━
DART_DISCLOSURE_CACHE_DIR = f"{_DATA_DIR}/dart_disclosures"


async def list_disclosures_for_ticker(ticker: str, days: int = 7) -> list[dict]:
    """특정 종목의 최근 N일 DART 공시 목록 조회.

    Args:
        ticker: 종목코드 (6자리)
        days: 조회 기간 (일)

    Returns:
        [{"rcept_no", "report_nm", "rcept_dt"}, ...] 또는 빈 리스트 (에러/키없음)
    """
    if not DART_API_KEY:
        return []
    if not ticker or not isinstance(ticker, str):
        return []

    try:
        corp_map = await get_dart_corp_map({})
        corp_code = corp_map.get(ticker, "")
        if not corp_code:
            return []

        now = datetime.now(KST)
        end_date = now.strftime("%Y%m%d")
        start_date = (now - timedelta(days=days)).strftime("%Y%m%d")

        url = f"{DART_BASE_URL}/list.json"
        params = {
            "crtfc_key": DART_API_KEY,
            "corp_code": corp_code,
            "bgn_de": start_date,
            "end_de": end_date,
            "page_count": 100,
            "sort": "date",
            "sort_mth": "desc",
        }

        session = _get_session()
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            if data.get("status") != "000":
                return []

            out = []
            for item in data.get("list", []):
                out.append({
                    "rcept_no": item.get("rcept_no", ""),
                    "report_nm": item.get("report_nm", ""),
                    "rcept_dt": item.get("rcept_dt", ""),
                })
            return out
    except Exception as e:
        print(f"[DART] list_disclosures_for_ticker({ticker}) 오류: {e}")
        return []


async def fetch_and_cache_disclosure(ticker: str, rcept_no: str) -> str:
    """공시 본문을 다운로드하여 data/dart_disclosures/ticker_rcept.txt 캐시.

    - 이미 있으면 파일 로드
    - 없으면 fetch_dart_document 호출 → 저장 → 반환
    - path traversal 방지
    """
    if not ticker or not rcept_no:
        return ""
    # path traversal 차단
    for bad in ("/", "\\", ".."):
        if bad in ticker or bad in rcept_no:
            return ""

    try:
        os.makedirs(DART_DISCLOSURE_CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(DART_DISCLOSURE_CACHE_DIR,
                                  f"{ticker}_{rcept_no}.txt")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                print(f"[DART] 캐시 읽기 실패 {cache_path}: {e}")

        body = await fetch_dart_document(rcept_no)
        if body:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(body)
            except Exception as e:
                print(f"[DART] 캐시 저장 실패 {cache_path}: {e}")
        return body or ""
    except Exception as e:
        print(f"[DART] fetch_and_cache_disclosure({ticker}, {rcept_no}) 오류: {e}")
        return ""


def _fmt_krw_amount(val: int) -> str:
    """원 단위 금액 → 한국어 요약 포맷."""
    if val is None:
        return "?"
    absv = abs(val)
    sign = "-" if val < 0 else ""
    if absv >= 1_0000_0000_0000:  # 1조 이상
        return f"{sign}{absv / 1_0000_0000_0000:.2f}조"
    elif absv >= 100_000_000:  # 1억 이상
        return f"{sign}{absv / 100_000_000:,.0f}억"
    elif absv >= 1_000_000:  # 백만 이상
        return f"{sign}{absv / 1_000_000:,.0f}백만"
    else:
        return f"{sign}{absv:,}원"


def _parse_pct(s: str) -> float | None:
    """'23.9%' / '+23.9' / '(23.9)' 등에서 % 값 추출."""
    if not s:
        return None
    m = re.search(r'([+\-]?\d+\.?\d*)\s*%?', s.replace(",", ""))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _detect_krw_unit(body_text: str) -> int:
    """본문에서 '(단위: 백만원)', '(단위: 원)', '(단위: 천원)' 감지 → 원 환산 곱수."""
    m = re.search(r'단위\s*[:\：]?\s*([가-힣]+원)', body_text[:3000])
    if not m:
        return 1  # 기본 원
    unit = m.group(1)
    if "백만" in unit:
        return 1_000_000
    if "천원" in unit or "천 원" in unit:
        return 1_000
    if "억" in unit:
        return 100_000_000
    return 1


def parse_disclosure_summary(report_nm: str, body_text: str) -> dict | None:
    """공시 본문 → 요약 dict. 5종 타입 분기. 매칭 안 되면 None.

    Returns:
        {"type": str, "summary": [str, ...]} 또는 None
    """
    if not report_nm or not body_text:
        return None
    try:
        title = report_nm
        # ━━ 타입 1: 잠정실적 ━━
        if "잠정실적" in title or "영업(잠정)실적" in title:
            return _parse_earnings_preview(body_text)

        # ━━ 타입 2: 자기주식 취득/소각 ━━
        if ("자기주식취득결정" in title or "자기주식 취득" in title
                or "주식소각" in title or "자기주식소각" in title):
            return _parse_buyback(title, body_text)

        # ━━ 타입 3: 배당결정 ━━
        if ("현금배당" in title or "현금·현물배당" in title
                or "현금ㆍ현물배당" in title or "배당결정" in title):
            return _parse_dividend(body_text)

        # ━━ 타입 4: 풍문·보도해명 ━━
        if "풍문" in title or "해명" in title:
            return _parse_rumor(body_text)

        # ━━ 타입 5: 매칭 안됨 ━━
        return None
    except Exception as e:
        print(f"[DART] parse_disclosure_summary 오류 ({report_nm[:40]}): {e}")
        return None


def _parse_earnings_preview(body: str) -> dict | None:
    """잠정실적 파싱. 매출/영업이익/순이익 + YoY%."""
    try:
        unit = _detect_krw_unit(body)
        lines_out = []

        # 계정별 키워드 (순서 중요 — 매출총이익/영업이익 등 구분)
        targets = [
            ("매출", "💰 매출", ["매출액", "매출 액"]),
            ("영업이익", "💰 영업익", ["영업이익", "영업 이익"]),
            ("순이익", "💰 순익",
             ["당기순이익", "당기 순이익", "반기순이익", "분기순이익", "순이익"]),
        ]

        for label, emoji, keywords in targets:
            amount_won = None
            yoy_pct = None

            for kw in keywords:
                # 라인 단위로 찾기 — 키워드가 포함된 라인 검색
                # 잠정실적 테이블은 세로 라인 구조: 항목\n당기실적\n전년동기\n증감률...
                idx = body.find(kw)
                if idx < 0:
                    continue

                # 키워드 이후 500자 윈도우에서 숫자들 수집
                window = body[idx:idx + 800]
                # 숫자 (콤마 포함, 괄호음수, 소수점) 추출
                # 1,462,345 또는 (1,462) 또는 1462345
                nums = re.findall(r'\(?([\-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?|\d+\.\d+)\)?', window)
                # 필터: 연도(19xx/20xx)와 너무 작은 숫자 제거
                filtered = []
                for n in nums:
                    clean = n.replace(",", "")
                    try:
                        v = float(clean)
                    except ValueError:
                        continue
                    # 연도 제외 (2015~2030)
                    if 2015 <= v <= 2030 and "." not in clean:
                        continue
                    filtered.append((v, clean))

                if not filtered:
                    continue

                # 첫 번째 큰 숫자 = 당기실적
                # 영업이익/순이익은 작을 수 있음 → min threshold 완화
                for v, clean in filtered:
                    if abs(v) >= 100:  # 단위가 원일 경우 매우 큰 값. 단위가 백만원이면 작을 수도.
                        amount_won = int(v) * unit
                        break

                if amount_won is None and filtered:
                    v, _ = filtered[0]
                    amount_won = int(v) * unit

                # YoY% 찾기 — 증감률/전년대비 키워드 근처
                # 윈도우 내에서 "증감률" 또는 "전년동기대비" 뒤의 소수
                pct_match = re.search(
                    r'(?:증감률|전년(?:동기)?(?:대비)?|대비)[^%\n]{0,100}?([\-]?\d+\.?\d*)\s*%?',
                    window)
                if pct_match:
                    try:
                        yoy_pct = float(pct_match.group(1))
                    except ValueError:
                        pass
                # 또는 단순히 소수점 있는 % 값 (% 부호 있는 경우 우선)
                if yoy_pct is None:
                    pct_m2 = re.findall(r'([\-]?\d+\.\d+)\s*%', window)
                    if pct_m2:
                        try:
                            yoy_pct = float(pct_m2[0])
                        except ValueError:
                            pass
                # 마지막 fallback: 필터링된 숫자 중 소수점 있고 합리적 범위(-99~999)인 값
                # 보통 잠정실적 테이블은 [당기, 전년동기, 증감률] 3열이므로
                # filtered[2]이 증감률 (소수점)
                if yoy_pct is None:
                    for v, clean in filtered:
                        if "." in clean and -99 <= v <= 999 and v != amount_won:
                            yoy_pct = v
                            break
                break  # 첫 매칭 키워드 사용

            if amount_won is not None:
                amt_str = _fmt_krw_amount(amount_won)
                if yoy_pct is not None:
                    sign = "+" if yoy_pct >= 0 else ""
                    lines_out.append(f"{emoji} {amt_str} ({sign}{yoy_pct:.1f}%)")
                else:
                    lines_out.append(f"{emoji} {amt_str}")

        if not lines_out:
            return None
        return {"type": "earnings_preview", "summary": lines_out}
    except Exception as e:
        print(f"[DART] _parse_earnings_preview 오류: {e}")
        return None


def _parse_buyback(title: str, body: str) -> dict | None:
    """자기주식 취득/소각. 규모(원) + 주수."""
    try:
        summary = []
        is_cancel = ("소각" in title)
        amount_won = None
        shares = None

        # 취득예정금액 / 소각예정금액 / 총액
        amt_keywords = ["취득예정금액", "소각예정금액", "취득금액", "소각금액", "취득 예정 금액"]
        for kw in amt_keywords:
            idx = body.find(kw)
            if idx < 0:
                continue
            window = body[idx:idx + 300]
            # 숫자 찾기
            nums = re.findall(r'([\-]?\d{1,3}(?:,\d{3})+|\d{4,})', window)
            for n in nums:
                try:
                    v = int(n.replace(",", ""))
                    if v >= 1_000_000:  # 1백만원 이상만 유효
                        amount_won = v
                        break
                except ValueError:
                    continue
            if amount_won:
                break

        # 취득예정주식수 / 소각예정주식수
        share_keywords = ["취득예정주식", "소각예정주식", "취득 예정 주식",
                          "소각 예정 주식", "취득주식수", "소각주식수"]
        for kw in share_keywords:
            idx = body.find(kw)
            if idx < 0:
                continue
            window = body[idx:idx + 300]
            nums = re.findall(r'([\-]?\d{1,3}(?:,\d{3})+|\d{4,})', window)
            for n in nums:
                try:
                    v = int(n.replace(",", ""))
                    if 100 <= v <= 10_000_000_000:  # 합리적 주수 범위
                        shares = v
                        break
                except ValueError:
                    continue
            if shares:
                break

        if amount_won:
            summary.append(f"💼 규모 {_fmt_krw_amount(amount_won)}")
        if shares:
            summary.append(f"💼 주수 {shares:,}주")

        if not summary:
            return None
        return {"type": "buyback_cancel" if is_cancel else "buyback",
                "summary": summary}
    except Exception as e:
        print(f"[DART] _parse_buyback 오류: {e}")
        return None


def _parse_dividend(body: str) -> dict | None:
    """현금·현물배당. DPS + 기준일."""
    try:
        summary = []
        dps = None
        base_date = None

        # 1주당 배당금 (주식배당은 제외하고 현금배당만)
        dps_keywords = ["1주당 현금배당금", "1주당 배당금", "주당 배당금",
                        "1주당현금배당금", "1주당배당금"]
        for kw in dps_keywords:
            idx = body.find(kw)
            if idx < 0:
                continue
            window = body[idx:idx + 300]
            # 보통주/우선주가 나올 수 있음 → 첫 매칭만
            nums = re.findall(r'([\-]?\d{1,3}(?:,\d{3})+|\d{2,})', window)
            for n in nums:
                try:
                    v = int(n.replace(",", ""))
                    if 10 <= v <= 1_000_000:  # 합리적 DPS 범위
                        dps = v
                        break
                except ValueError:
                    continue
            if dps:
                break

        # 배당기준일
        base_keywords = ["배당기준일", "기준일"]
        for kw in base_keywords:
            idx = body.find(kw)
            if idx < 0:
                continue
            window = body[idx:idx + 200]
            # YYYY-MM-DD or YYYY.MM.DD or YYYY년 MM월 DD일 or YYYYMMDD
            m = re.search(r'(20\d{2})[\-.\s년]+(\d{1,2})[\-.\s월]+(\d{1,2})', window)
            if m:
                y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
                base_date = f"{y}-{mo}-{d}"
                break
            m2 = re.search(r'(20\d{6})', window)
            if m2:
                s = m2.group(1)
                base_date = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                break

        if dps is not None:
            summary.append(f"💵 DPS {dps:,}원")
        if base_date:
            summary.append(f"📅 기준일 {base_date}")

        if not summary:
            return None
        return {"type": "dividend", "summary": summary}
    except Exception as e:
        print(f"[DART] _parse_dividend 오류: {e}")
        return None


def _parse_rumor(body: str) -> dict | None:
    """풍문·보도해명. 상태 + 재공시예정일."""
    try:
        summary = []
        status = None

        # 상태 판정 (우선순위: 사실무근 > 부인 > 미확정 > 확인)
        if "사실무근" in body or "사실이 아닙니다" in body or "사실이 아님" in body:
            status = "사실무근"
        elif "부인" in body:
            status = "부인"
        elif "미확정" in body or "확정된 바 없" in body or "확정되지 않" in body:
            status = "미확정"
        elif "사실" in body and ("확인" in body or "인정" in body):
            status = "사실확인"

        # 재공시예정일
        redate = None
        redate_keywords = ["재공시예정일", "향후 재공시", "향후재공시", "추후 재공시"]
        for kw in redate_keywords:
            idx = body.find(kw)
            if idx < 0:
                continue
            window = body[idx:idx + 300]
            m = re.search(r'(20\d{2})[\-.\s년]+(\d{1,2})[\-.\s월]+(\d{1,2})', window)
            if m:
                y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
                redate = f"{y}-{mo}-{d}"
                break
            m2 = re.search(r'(20\d{6})', window)
            if m2:
                s = m2.group(1)
                redate = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                break

        if status:
            summary.append(f"📰 상태: {status}")
        if redate:
            summary.append(f"📅 재공시 예정일: {redate}")

        if not summary:
            return None
        return {"type": "rumor_clarification", "summary": summary}
    except Exception as e:
        print(f"[DART] _parse_rumor 오류: {e}")
        return None


def _report_file_exists(rcept_no: str) -> str | None:
    """접수번호로 기존 파일 검색. 있으면 파일경로, 없으면 None."""
    if not rcept_no or not os.path.exists(DART_REPORTS_DIR):
        return None
    for fname in os.listdir(DART_REPORTS_DIR):
        if rcept_no in fname:
            return os.path.join(DART_REPORTS_DIR, fname)
    return None


async def save_dart_report(ticker: str, name: str, rcept_no: str,
                           report_date: str) -> dict | None:
    """사업보고서 본문을 txt로 저장. 이미 존재하면 스킵."""
    if not rcept_no:
        return None
    existing = _report_file_exists(rcept_no)
    if existing:
        size_kb = os.path.getsize(existing) / 1024
        print(f"[DART] 이미 존재: {existing}")
        return {"ticker": ticker, "name": name, "report_date": report_date,
                "file_path": existing, "file_size_kb": round(size_kb, 1),
                "skipped": True}

    text = await fetch_dart_document(rcept_no)
    if not text:
        print(f"[DART] 본문 없음: {ticker} {rcept_no}")
        return None

    os.makedirs(DART_REPORTS_DIR, exist_ok=True)
    date_str = report_date.replace("-", "").replace(".", "")[:8]
    # path traversal 방지: 파일명에서 위험 문자 제거
    import re as _re
    safe_ticker = _re.sub(r'[^a-zA-Z0-9]', '', ticker)
    safe_name = _re.sub(r'[^\w]', '', name)
    safe_rcept = _re.sub(r'[^0-9]', '', rcept_no)
    safe_date = _re.sub(r'[^0-9]', '', date_str)
    if not safe_ticker or not safe_rcept:
        print(f"[DART] 잘못된 ticker/rcept_no: {ticker}/{rcept_no}")
        return None
    filename = f"{safe_ticker}_{safe_name}_{safe_date}_{safe_rcept}.txt"
    filepath = os.path.join(DART_REPORTS_DIR, filename)
    # 최종 경로가 DART_REPORTS_DIR 내인지 확인
    if not os.path.abspath(filepath).startswith(os.path.abspath(DART_REPORTS_DIR)):
        print(f"[DART] 경로 이탈 감지: {filepath}")
        return None

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    header = (
        f"===== DART 사업보고서 =====\n"
        f"종목: {name} ({ticker})\n"
        f"보고서일: {report_date}\n"
        f"접수번호: {rcept_no}\n"
        f"저장일시: {now_str}\n"
        f"{'=' * 30}\n\n"
    )
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(header + text)

    size_kb = os.path.getsize(filepath) / 1024
    print(f"[DART] 저장: {filepath} ({size_kb:.1f}KB)")
    return {"ticker": ticker, "name": name, "report_date": report_date,
            "file_path": filepath, "file_size_kb": round(size_kb, 1),
            "skipped": False}


def read_dart_report(ticker: str, max_chars: int = 50_000) -> dict:
    """저장된 사업보고서 txt 파일 내용 반환. 여러 개면 최신 날짜."""
    if not os.path.exists(DART_REPORTS_DIR):
        return {"error": f"사업보고서 없음. get_dart(mode='report', ticker='{ticker}')으로 먼저 저장하세요."}

    matches = []
    for fname in os.listdir(DART_REPORTS_DIR):
        if not fname.endswith(".txt"):
            continue
        parts = fname.replace(".txt", "").split("_")
        if parts[0] == ticker:
            matches.append(fname)

    if not matches:
        return {"error": f"사업보고서 없음. get_dart(mode='report', ticker='{ticker}')으로 먼저 저장하세요."}

    # 파일명: {ticker}_{name}_{date}_{rcept}.txt — name에 _가 포함될 수 있으므로 뒤에서 파싱
    def _parse_fname(f):
        stem = f.replace(".txt", "")
        parts = stem.split("_")
        # 뒤에서 rcept(숫자), date(8자리 숫자), 나머지가 ticker_name
        if len(parts) >= 4:
            rcept = parts[-1]
            date_str = parts[-2]
            name = "_".join(parts[1:-2])
        elif len(parts) >= 3:
            rcept = ""
            date_str = parts[-1]
            name = "_".join(parts[1:-1])
        else:
            rcept = ""
            date_str = ""
            name = parts[1] if len(parts) >= 2 else ""
        return name, date_str, rcept

    matches.sort(key=lambda f: (_parse_fname(f)[1], _parse_fname(f)[2]), reverse=True)
    fname = matches[0]
    filepath = os.path.join(DART_REPORTS_DIR, fname)

    name, date_str, _ = _parse_fname(fname)
    if len(date_str) == 8:
        report_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    else:
        report_date = date_str

    size_kb = os.path.getsize(filepath) / 1024

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    truncated = False
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True

    return {
        "ticker": ticker,
        "name": name,
        "report_date": report_date,
        "file_path": filepath,
        "file_size_kb": round(size_kb, 1),
        "content": content,
        "truncated": truncated,
    }


def list_dart_reports() -> dict:
    """저장된 사업보고서 txt 파일 목록 반환."""
    files = []
    if os.path.exists(DART_REPORTS_DIR):
        for fname in sorted(os.listdir(DART_REPORTS_DIR)):
            if not fname.endswith(".txt"):
                continue
            filepath = os.path.join(DART_REPORTS_DIR, fname)
            parts = fname.replace(".txt", "").split("_")
            ticker = parts[0] if len(parts) >= 1 else ""
            # 뒤에서 파싱: rcept(마지막), date(뒤에서 둘째), 나머지가 name
            if len(parts) >= 4:
                date_str = parts[-2]
                name = "_".join(parts[1:-2])
            elif len(parts) >= 3:
                date_str = parts[-1]
                name = "_".join(parts[1:-1])
            else:
                date_str = ""
                name = parts[1] if len(parts) >= 2 else ""
            if len(date_str) == 8:
                report_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            else:
                report_date = date_str
            size_kb = os.path.getsize(filepath) / 1024
            files.append({
                "ticker": ticker, "name": name,
                "report_date": report_date,
                "file_path": filepath,
                "file_size_kb": round(size_kb, 1),
            })
    return {"files": files, "total": len(files)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# StockAnalysis.com 애널 레이팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━
