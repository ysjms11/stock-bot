import os
import json
import re
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 환경변수 & 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
KIS_APP_KEY = os.environ.get("KIS_APP_KEY")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET")
DART_API_KEY = os.environ.get("DART_API_KEY", "")

KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
DART_BASE_URL = "https://opendart.fss.or.kr/api"
KST = timezone(timedelta(hours=9))
ET  = ZoneInfo('America/New_York')  # DST 자동 감지 (서머타임 EDT/표준시 EST)

os.makedirs("/data", exist_ok=True)

WATCHLIST_FILE    = "/data/watchlist.json"
STOPLOSS_FILE     = "/data/stoploss.json"
US_WATCHLIST_FILE = "/data/us_watchlist.json"
DART_SEEN_FILE    = "/data/dart_seen.json"
PORTFOLIO_FILE    = "/data/portfolio.json"
WATCHALERT_FILE   = "/data/watchalert.json"
WATCH_SENT_FILE      = "/data/watch_sent.json"
STOPLOSS_SENT_FILE   = "/data/stoploss_sent.json"
DECISION_LOG_FILE = "/data/decision_log.json"
COMPARE_LOG_FILE  = "/data/compare_log.json"
EVENTS_FILE       = "/data/events.json"
WEEKLY_BASE_FILE  = "/data/weekly_base.json"
UNIVERSE_FILE     = "/data/stock_universe.json"

MACRO_SYMBOLS = {
    "VIX":    "^VIX",
    "WTI":    "CL=F",
    "GOLD":   "GC=F",
    "COPPER": "HG=F",
    "DXY":    "DX-Y.NYB",
    "US10Y":  "^TNX",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 환경변수 기반 데이터 복원 (Railway Volume 미마운트 시 fallback)
# Railway Variables에 BACKUP_PORTFOLIO, BACKUP_STOPLOSS 등을 JSON 문자열로 설정하면
# /data/ 파일이 없을 때 자동 복원됨
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_BACKUP_MAP = {
    "BACKUP_PORTFOLIO":    PORTFOLIO_FILE,
    "BACKUP_STOPLOSS":     STOPLOSS_FILE,
    "BACKUP_WATCHLIST":    WATCHLIST_FILE,
    "BACKUP_US_WATCHLIST": US_WATCHLIST_FILE,
    "BACKUP_WATCHALERT":   WATCHALERT_FILE,
    "BACKUP_DECISION_LOG": DECISION_LOG_FILE,
    "BACKUP_COMPARE_LOG":  COMPARE_LOG_FILE,
    "BACKUP_EVENTS":       EVENTS_FILE,
    "BACKUP_WEEKLY_BASE":  WEEKLY_BASE_FILE,
}
for _env_key, _filepath in _BACKUP_MAP.items():
    if not os.path.exists(_filepath):
        _backup_val = os.environ.get(_env_key, "")
        if _backup_val:
            try:
                _data = json.loads(_backup_val)
                with open(_filepath, "w", encoding="utf-8") as _f:
                    json.dump(_data, _f, ensure_ascii=False, indent=2)
                print(f"[복원] {_filepath} ← 환경변수 {_env_key}")
            except Exception as _e:
                print(f"[복원 실패] {_env_key}: {_e}")

_token_cache = {"token": None, "expires": None}


def _is_us_ticker(ticker: str) -> bool:
    """영문 티커면 미국 종목으로 판별 (숫자 포함 없으면 US)"""
    return bool(ticker) and ticker.replace(".", "").replace("-", "").isalpha()


# NYSE 대표 종목 (나머지는 NASDAQ 기본)
_NYSE_TICKERS = {
    "BRK.A", "BRK.B", "JNJ", "V", "WMT", "PG", "MA", "HD", "DIS", "BA",
    "KO", "PFE", "MRK", "VZ", "T", "NKE", "MMM", "CAT", "GS", "JPM",
    "BAC", "C", "WFC", "UNH", "CVX", "XOM", "CRM", "ORCL", "IBM", "GE",
    "LMT", "RTX", "NOC", "PM", "MCD", "UPS", "FDX", "GM", "F",
}

def _guess_excd(symbol: str) -> str:
    """미국 종목 거래소코드 추정 (NYS/NAS/AMS)"""
    return "NYS" if symbol.upper() in _NYSE_TICKERS else "NAS"


def _is_us_market_hours_kst() -> bool:
    """미국 장 시간 여부 (ET 09:30~16:00, DST 자동 감지)"""
    now_et = datetime.now(ET)
    wd = now_et.weekday()
    if wd >= 5:
        return False  # 토/일 ET → 미국 장 없음
    h, m = now_et.hour, now_et.minute
    if h < 9 or (h == 9 and m < 30):
        return False  # ET 09:30 이전
    if h >= 16:
        return False  # ET 16:00 이후
    return True


def _is_us_market_closed() -> bool:
    """미국 정규장 마감 후 30분 이내 여부 (DST 자동 감지)

    DST(UTC-4) 시: KST 05:00~05:30
    표준시(UTC-5) 시: KST 06:00~06:30
    """
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return False  # 토/일 ET → 미국 장 없음
    close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    diff_sec = (now_et - close_et).total_seconds()
    return 0 <= diff_sec <= 1800  # 마감 후 0~30분 이내

# DART 중요 공시 키워드
DART_KEYWORDS = [
    "수주", "계약", "공급계약", "납품", "유상증자", "무상증자",
    "전환사채", "신주인수권", "자기주식", "배당", "합병",
    "분할", "영업양수", "영업양도", "소송", "상장폐지",
    "실적", "매출", "영업이익", "감자", "대규모",
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 파일 저장/로드
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def load_json(filepath, default=None):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        if default is not None:
            save_json(filepath, default)
            return default
        return {}


def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_watchlist():
    return load_json(WATCHLIST_FILE, {
        "009540": "HD한국조선해양", "298040": "효성중공업",
        "010120": "LS ELECTRIC", "267260": "HD현대일렉트릭",
        "034020": "두산에너빌리티",
    })


def load_stoploss():
    return load_json(STOPLOSS_FILE, {})


def load_us_watchlist():
    return load_json(US_WATCHLIST_FILE, {
        "TSLA": {"name": "테슬라", "qty": 12},
        "CRSP": {"name": "크리스퍼", "qty": 70},
        "AMD": {"name": "AMD", "qty": 17},
        "LITE": {"name": "루멘텀", "qty": 4},
    })


def load_dart_seen():
    return load_json(DART_SEEN_FILE, {"ids": []})


def load_watchalert():
    return load_json(WATCHALERT_FILE, {})

def load_decision_log():
    return load_json(DECISION_LOG_FILE, {})

def load_compare_log():
    return load_json(COMPARE_LOG_FILE, [])


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KIS API
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def get_kis_token():
    now = datetime.now()
    if _token_cache["token"] and _token_cache["expires"] and _token_cache["expires"] > now:
        return _token_cache["token"]
    url = f"{KIS_BASE_URL}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers={"content-type": "application/json"}, json=body) as resp:
            data = await resp.json()
            token = data.get("access_token")
            if token:
                _token_cache["token"] = token
                _token_cache["expires"] = now + timedelta(hours=20)
            return token


async def get_stock_price(ticker, token):
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHKST01010100"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker}) as resp:
            return (await resp.json()).get("output", {})


async def get_investor_trend(ticker, token):
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHKST01010900"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}) as resp:
            return (await resp.json()).get("output", [])


async def get_volume_rank(token):
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHPST01710000"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20101",
        "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "0", "FID_INPUT_PRICE_2": "0",
        "FID_VOL_CNT": "0", "FID_INPUT_DATE_1": ""
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            return (await resp.json()).get("output", [])


async def get_kis_index(token, index_code="0001"):
    """KIS API로 KOSPI/KOSDAQ 지수 조회 (0001=KOSPI, 1001=KOSDAQ)"""
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHPUP02100000"
    }
    params = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": index_code}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            return (await resp.json()).get("output", {})


def _kis_headers(token, tr_id):
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
    }


async def _kis_get(session, path, tr_id, token, params):
    url = f"{KIS_BASE_URL}{path}"
    async with session.get(url, headers=_kis_headers(token, tr_id), params=params) as r:
        data = await r.json(content_type=None)
        return r.status, data


async def kis_stock_price(ticker, token):
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        return d.get("output", {})


async def kis_stock_info(ticker, token):
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/search-stock-info",
            "CTPF1002R", token,
            {"PRDT_TYPE_CD": "300", "PDNO": ticker})
        return d.get("output", {})


async def kis_investor_trend(ticker, token):
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        return d.get("output", [])


async def kis_credit_balance(ticker, token):
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-credit-by-company",
            "FHKST01010600", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        return d.get("output", {})


async def kis_short_selling(ticker, token):
    today = datetime.now().strftime("%Y%m%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-short-selling",
            "FHKST01010700", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker,
             "fid_begin_dt": week_ago, "fid_end_dt": today})
        return d.get("output", [])


async def kis_volume_rank_api(token):
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/volume-rank",
            "FHPST01710000", token,
            {"fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20171",
             "fid_input_iscd": "0000", "fid_div_cls_code": "0", "fid_blng_cls_code": "0",
             "fid_trgt_cls_code": "111111111", "fid_trgt_exls_cls_code": "000000",
             "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "", "fid_input_date_1": ""})
        return d.get("output", [])


async def kis_foreigner_trend(token):
    today = datetime.now().strftime("%Y%m%d")
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-foreigner-trend",
            "FHPTJ04060100", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": "0000", "fid_input_date_1": today})
        if not d:
            return []
        output = d.get("output") or []
        return [r for r in output if r is not None]


async def kis_sector_price(token):
    today = datetime.now().strftime("%Y%m%d")
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-daily-sector-price",
            "FHKUP03500100", token,
            {"fid_cond_mrkt_div_code": "U", "fid_input_iscd": "0001",
             "fid_input_date_1": today, "fid_period_div_code": "D"})
        return d.get("output", [])


WI26_SECTORS = [
    ("001", "반도체"), ("004", "조선"),   ("006", "전력기기"),
    ("007", "방산"),   ("010", "2차전지"), ("012", "건설"),
    ("021", "바이오"),
]

# 외국인 순매수 상위 fallback용 티커→업종 매핑
_TICKER_SECTOR = {
    "005930": "반도체", "000660": "반도체", "012510": "반도체", "042700": "반도체",
    "009540": "조선",   "042660": "조선",   "010140": "조선",   "267250": "조선",
    "012510": "전력기기","028260": "전력기기","267260": "전력기기","298040": "전력기기",
    "012450": "방산",   "047810": "방산",   "329180": "방산",   "272210": "방산",
    "006400": "2차전지","051910": "2차전지","373220": "2차전지","247540": "2차전지",
    "000720": "건설",   "097950": "건설",   "047040": "건설",   "028260": "건설",
    "207940": "바이오", "068270": "바이오", "196170": "바이오", "091990": "바이오",
}


async def _fetch_sector_flow(token: str, sector_code: str) -> tuple:
    """업종 외국인+기관 순매수금액(백만원) 반환. 실패 시 (0, 0)."""
    today = datetime.now().strftime("%Y%m%d")
    params = {
        "fid_cond_mrkt_div_code": "U",
        "fid_input_iscd": sector_code,
        "fid_input_date_1": today,
        "fid_period_div_code": "D",
    }
    for path in [
        "/uapi/domestic-stock/v1/quotations/inquire-member-daily-by-group",
        "/uapi/domestic-stock/v1/quotations/inquire-daily-sector-price",
    ]:
        try:
            async with aiohttp.ClientSession() as s:
                _, d = await _kis_get(s, path, "FHKUP03500100", token, params)
            if not d or d.get("rt_cd") != "0":
                continue
            out = d.get("output2") or d.get("output") or {}
            if isinstance(out, list):
                out = out[0] if out else {}
            frgn = int(out.get("frgn_ntby_tr_pbmn", 0) or 0)
            orgn = int(out.get("orgn_ntby_tr_pbmn", 0) or 0)
            if frgn != 0 or orgn != 0:
                return frgn, orgn
        except Exception:
            continue
    return 0, 0


async def kis_us_stock_price(symbol: str, token: str, excd: str = "") -> dict:
    """KIS API 해외주식 현재가 (HHDFS00000300)"""
    if not excd:
        excd = _guess_excd(symbol)
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price",
            "HHDFS00000300", token,
            {"AUTH": "", "EXCD": excd, "SYMB": symbol})
        return d.get("output", {})


async def kis_us_stock_detail(symbol: str, token: str, excd: str = "") -> dict:
    """KIS API 해외주식 현재가상세 (HHDFS76200200) — PER/PBR/시총/52주 등"""
    if not excd:
        excd = _guess_excd(symbol)
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price-detail",
            "HHDFS76200200", token,
            {"AUTH": "", "EXCD": excd, "SYMB": symbol})
        return d.get("output", {})


async def kis_fluctuation_rank(token: str, market: str = "0000",
                              sort: str = "rise", n: int = 20) -> list:
    """등락률 순위 조회 (FHPST01700000).

    market: "0000"=전체, "0001"=KOSPI, "1001"=KOSDAQ
    sort: "rise"=상승률 상위, "fall"=하락률 상위
    Returns: [{ticker, name, price, chg_pct, volume}, ...]
    """
    # 등락 필터: rise=양수 구간, fall=음수 구간
    rate1, rate2 = ("0", "") if sort == "rise" else ("", "0")
    hdrs = _kis_headers(token, "FHPST01700000")
    params = {
        "fid_rsfl_rate2":         rate2,
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code":  "20170",
        "fid_input_iscd":         market,
        "fid_rank_sort_cls_code": "0000",
        "fid_input_cnt_1":        str(min(n, 30)),
        "fid_prc_cls_code":       "0",
        "fid_input_price_1":      "1000",
        "fid_input_price_2":      "",
        "fid_vol_cnt":            "",
        "fid_trgt_cls_code":      "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code":       "0",
        "fid_rsfl_rate1":         rate1,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/ranking/fluctuation",
                             headers=hdrs, params=params) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        print(f"[kis_fluctuation_rank] 오류: {e}")
        return []

    result = []
    for item in data.get("output", [])[:n]:
        ticker = (item.get("mksc_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "ticker":  ticker,
            "name":    (item.get("hts_kor_isnm") or "").strip(),
            "price":   int(item.get("stck_prpr", 0) or 0),
            "chg_pct": float(item.get("prdy_ctrt", 0) or 0),
            "volume":  int(item.get("acml_vol", 0) or 0),
        })
    # fall 모드: 하락률 큰 순(음수 방향) 정렬
    if sort == "fall":
        result.sort(key=lambda x: x["chg_pct"])
    return result


async def kis_investor_trend_history(ticker: str, token: str, n_days: int = 5) -> list:
    """종목별 투자자 일별 수급 히스토리 (FHPTJ04160001).

    Returns: [{date, foreign_net, institution_net, individual_net,
               foreign_buy, foreign_sell}, ...] 최신순, 최대 n_days일
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
            "FHPTJ04160001", token,
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         ticker,
                "FID_INPUT_DATE_1":       today,
                "FID_ORG_ADJ_PRC":        "",
                "FID_ETC_CLS_CODE":       "",
            })
    rows = d.get("output1", [])
    result = []
    for row in rows[:n_days]:
        result.append({
            "date":            row.get("stck_bsop_date", ""),
            "foreign_net":     int(row.get("frgn_ntby_qty",  0) or 0),
            "institution_net": int(row.get("orgn_ntby_qty",  0) or 0),
            "individual_net":  int(row.get("prsn_ntby_qty",  0) or 0),
            "foreign_buy":     int(row.get("frgn_shnu_vol",  0) or 0),
            "foreign_sell":    int(row.get("frgn_seln_vol",  0) or 0),
        })
    return result


async def kis_program_trade_today(token: str, market: str = "kospi") -> list:
    """프로그램매매 투자자별 당일 동향 (HHPPG046600C1).

    market: "kospi"(1) or "kosdaq"(4)
    Returns: [{investor, total_net_qty, total_net_amt, arb_net_qty, non_arb_net_qty}, ...]
    """
    mrkt_code = "1" if market.lower() == "kospi" else "4"
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/investor-program-trade-today",
            "HHPPG046600C1", token,
            {"MRKT_DIV_CLS_CODE": mrkt_code})
    result = []
    for row in d.get("output1", []):
        name = (row.get("invr_cls_name") or "").strip()
        if not name:
            continue
        result.append({
            "investor":        name,
            "total_net_qty":   int(row.get("all_ntby_qty",  0) or 0),
            "total_net_amt":   int(row.get("all_ntby_amt",  0) or 0),
            "arb_net_qty":     int(row.get("arbt_ntby_qty", 0) or 0),
            "non_arb_net_qty": int(row.get("nabt_ntby_qty", 0) or 0),
        })
    return result


async def kis_estimate_perform(ticker: str, token: str) -> dict:
    """국내주식 종목추정실적 (HHKST668300C0)
    output2: 연간 추정실적 / output3: 분기 추정실적
    필드: dt(결산년월) data1(매출액) data2(영업이익) data3(세전이익) data4(순이익) data5(EPS)
    """
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/estimate-perform",
            "HHKST668300C0", token, {"SHT_CD": ticker})

    def _row(r):
        return {
            "dt":  r.get("dt", ""),
            "rev": r.get("data1", ""),
            "op":  r.get("data2", ""),
            "ebt": r.get("data3", ""),
            "np":  r.get("data4", ""),
            "eps": r.get("data5", ""),
        }

    annual = d.get("output2") or []
    qtly   = d.get("output3") or []
    return {
        "annual":    [_row(r) for r in (annual if isinstance(annual, list) else [annual])],
        "quarterly": [_row(r) for r in (qtly   if isinstance(qtly,   list) else [qtly])],
    }


def get_stock_universe() -> dict:
    """stock_universe.json에서 종목 유니버스 로드. {ticker: name} 반환.
    /data/stock_universe.json 없으면 kis_api.py 위치 기준 절대경로로 시도.
    """
    _repo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_universe.json")
    for path in [UNIVERSE_FILE, _repo_path, "stock_universe.json"]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                codes = data.get("codes", {})
                if codes:
                    print(f"[universe] {len(codes)}종목 로드 ({path})")
                    return codes
        except Exception:
            pass
    print("[universe] stock_universe.json 로드 실패 — 빈 유니버스 반환")
    return {}


async def fetch_universe_from_krx(token: str) -> dict:
    """KIS 시가총액 상위 API로 유니버스 자동 조회.

    - KOSPI200 구성종목 전체 (fid_input_iscd="2001")
    - KOSDAQ 시총 상위 150종목 (fid_input_iscd="1001")
    페이지네이션: 응답 헤더 tr_cont="M" 이면 다음 페이지 요청.

    Returns: {ticker: name}
    """
    BASE_PATH = "/uapi/domestic-stock/v1/ranking/market-cap"
    TR_ID     = "FHPST01740000"

    async def _fetch_market(iscd: str, max_count: int) -> dict:
        collected: dict = {}
        tr_cont = ""
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            while len(collected) < max_count:
                hdrs = {**_kis_headers(token, TR_ID), "tr_cont": tr_cont}
                params = {
                    "fid_input_price_2":       "",
                    "fid_cond_mrkt_div_code":  "J",
                    "fid_cond_scr_div_code":   "20174",
                    "fid_div_cls_code":        "1",   # 보통주만 (우선주·ETF 제외)
                    "fid_input_iscd":          iscd,
                    "fid_trgt_cls_code":       "0",
                    "fid_trgt_exls_cls_code":  "0",
                    "fid_input_price_1":       "",
                    "fid_vol_cnt":             "",
                }
                try:
                    async with s.get(f"{KIS_BASE_URL}{BASE_PATH}",
                                     headers=hdrs, params=params) as r:
                        data           = await r.json(content_type=None)
                        resp_tr_cont   = r.headers.get("tr_cont", "D")
                except Exception as e:
                    print(f"[fetch_universe] iscd={iscd} 요청 오류: {e}")
                    break

                items = data.get("output", [])
                if not items:
                    break
                for item in items:
                    ticker = (item.get("mksc_shrn_iscd") or "").strip()
                    name   = (item.get("hts_kor_isnm")   or "").strip()
                    if ticker and name:
                        collected[ticker] = name
                        if len(collected) >= max_count:
                            break

                if resp_tr_cont != "M":
                    break
                tr_cont = "N"
                await asyncio.sleep(0.15)

        return collected

    kospi200  = await _fetch_market("2001", 250)  # KOSPI200 전체
    await asyncio.sleep(0.3)
    kosdaq150 = await _fetch_market("1001", 150)  # KOSDAQ 시총 상위 150
    universe  = {**kospi200, **kosdaq150}
    print(f"[fetch_universe] KOSPI200={len(kospi200)}, KOSDAQ={len(kosdaq150)}, 합계={len(universe)}")
    return universe


async def batch_fetch(codes: list, fetch_fn, token: str, delay: float = 0.06) -> dict:
    """종목 리스트에 대해 rate limit 지키면서 배치 조회.
    codes: list of tickers
    fetch_fn(ticker, token) → result
    returns: {ticker: result}
    """
    results = {}
    for code in codes:
        try:
            results[code] = await fetch_fn(code, token)
        except Exception:
            pass
        await asyncio.sleep(delay)
    return results


async def kis_daily_closes(ticker: str, token: str, n: int = 65) -> list:
    """최근 n거래일 종가 리스트 반환 (최신이 [0])
    FHKST03010100 일봉 API 사용. 8초 timeout으로 hang 방지.
    """
    today_str = datetime.now(KST).strftime("%Y%m%d")
    start_dt = (datetime.now(KST) - timedelta(days=n * 2)).strftime("%Y%m%d")
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100", token,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
             "FID_INPUT_DATE_1": start_dt, "FID_INPUT_DATE_2": today_str,
             "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
    candles = d.get("output2") or []
    return [int(c.get("stck_clpr", 0) or 0) for c in candles[:n]]


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KIS WebSocket 실시간 체결가 (국내주식 전용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_ws_key_cache: dict = {"key": None, "expires": 0.0}


async def get_kis_ws_approval_key() -> str:
    """WebSocket 접속키 발급 (23시간 캐시)"""
    import time as _t
    now = _t.time()
    if _ws_key_cache["key"] and now < _ws_key_cache["expires"]:
        return _ws_key_cache["key"]
    url = f"{KIS_BASE_URL}/oauth2/Approval"
    body = {"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "secretkey": KIS_APP_SECRET}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body) as r:
                d = await r.json(content_type=None)
                key = d.get("approval_key", "")
                if key:
                    _ws_key_cache["key"] = key
                    _ws_key_cache["expires"] = now + 82800
                return _ws_key_cache.get("key") or ""
    except Exception as e:
        print(f"[WS] 접속키 발급 오류: {e}")
        return ""


class KisRealtimeManager:
    """KIS WebSocket 국내주식 실시간 체결가 매니저
    평일 09:00~16:00 KST에만 연결. 끊김 시 30초 후 자동 재연결.
    미국 주식은 _is_us_ticker() 로 걸러서 구독하지 않음.
    """
    _WS_URL = "wss://ops.koreainvestment.com:21000"

    def __init__(self):
        self._subscribed: set = set()
        self._ws = None
        self._alert_cb = None
        self._running = False
        self._task = None
        self._fired: dict = {}  # {ticker: set(alert_types)} — 당일 발송 추적

    async def start(self, alert_callback, tickers: set):
        self._alert_cb = alert_callback
        self._subscribed = {t for t in tickers if not _is_us_ticker(t)}
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def update_tickers(self, new_tickers: set):
        """구독 종목 변경 (KR만 필터링)"""
        kr_new    = {t for t in new_tickers if not _is_us_ticker(t)}
        to_add    = kr_new - self._subscribed
        to_remove = self._subscribed - kr_new
        self._subscribed = kr_new
        if self._ws and not self._ws.closed:
            for t in to_add:
                await self._send_sub(t, "1")
            for t in to_remove:
                await self._send_sub(t, "0")

    def reset_fired(self):
        self._fired = {}

    async def _run_loop(self):
        while self._running:
            now = datetime.now(KST)
            if now.weekday() < 5 and 9 <= now.hour < 16:
                try:
                    await self._connect_and_run()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"[WS] 오류: {e}, 30초 후 재연결...")
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(60)   # 장외: 1분마다 체크

    async def _connect_and_run(self):
        self.reset_fired()
        key = await get_kis_ws_approval_key()
        if not key:
            print("[WS] 접속키 없음, 스킵")
            return
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self._WS_URL, heartbeat=30,
                timeout=aiohttp.ClientTimeout(total=None)
            ) as ws:
                self._ws = ws
                print(f"[WS] 연결됨 ({len(self._subscribed)}개 구독)")
                for t in list(self._subscribed):
                    await self._send_sub_raw(ws, key, t, "1")
                    await asyncio.sleep(0.05)
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._on_text(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        print("[WS] 연결 종료됨")
                        break
        self._ws = None

    async def _send_sub_raw(self, ws, key, ticker, tr_type):
        await ws.send_json({
            "header": {
                "approval_key": key, "custtype": "P",
                "tr_type": tr_type, "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": "H0STCNT0", "tr_key": ticker}},
        })

    async def _send_sub(self, ticker, tr_type):
        if self._ws and not self._ws.closed:
            key = await get_kis_ws_approval_key()
            await self._send_sub_raw(self._ws, key, ticker, tr_type)

    async def _on_text(self, raw: str):
        # 포맷: "0|H0STCNT0|001|종목코드^체결시간^현재가^..."
        if raw.startswith("{"):
            return   # JSON ACK 무시
        parts = raw.split("|")
        if len(parts) < 4 or parts[1] != "H0STCNT0":
            return
        count = int(parts[2])
        all_fields = parts[3].split("^")
        if count == 0 or not all_fields:
            return
        per_rec = len(all_fields) // count
        for i in range(count):
            f = all_fields[i * per_rec: (i + 1) * per_rec]
            if len(f) < 3:
                continue
            ticker = f[0]
            try:
                price = int(f[2])
            except (ValueError, IndexError):
                continue
            if price > 0 and self._alert_cb:
                await self._alert_cb(ticker, price)


# KisRealtimeManager 싱글톤
ws_manager = KisRealtimeManager()


def get_ws_tickers() -> set:
    """WebSocket 구독 대상 KR 종목 수집 (포트폴리오 + 손절 + 워치알러트 + 워치리스트)"""
    tickers = set()
    for t in load_json(PORTFOLIO_FILE, {}):
        if t != "us_stocks" and not _is_us_ticker(t):
            tickers.add(t)
    for t in load_stoploss():
        if t != "us_stocks" and not _is_us_ticker(t):
            tickers.add(t)
    for t in load_watchalert():
        if not _is_us_ticker(t):
            tickers.add(t)
    for t in load_watchlist():
        if not _is_us_ticker(t):
            tickers.add(t)
    return tickers


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Yahoo Finance
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def get_yahoo_quote(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status == 200:
                    meta = (await resp.json()).get("chart", {}).get("result", [{}])[0].get("meta", {})
                    price = meta.get("regularMarketPrice", 0)
                    prev = meta.get("chartPreviousClose", 0)
                    return {"price": price, "prev": prev, "change_pct": ((price - prev) / prev * 100) if prev else 0}
    except Exception:
        pass
    return {"price": 0, "prev": 0, "change_pct": 0}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 매크로 대시보드
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_DEFAULT_EVENTS = {
    "FOMC":    "2026-04-28",
    "CPI":     "2026-04-10",
    "PPI":     "2026-04-11",
    "고용보고서": "2026-04-03",
    "다음FOMC": "2026-06-16",
    "이란":     "진행중",
}


def load_events() -> dict:
    """이벤트 캘린더 로드 (/data/events.json, 없으면 기본값으로 초기화)"""
    return load_json(EVENTS_FILE, _DEFAULT_EVENTS)


async def collect_macro_data() -> dict:
    """매크로 지표 전체 수집 — 텔레그램 자동발송 + MCP 공용"""
    data = {}

    # 1. Yahoo Finance 매크로 심볼
    for key, symbol in MACRO_SYMBOLS.items():
        try:
            q = await get_yahoo_quote(symbol)
            p = q.get("price", 0)
            c = q.get("change_pct", 0)
            data[key] = {
                "price":      round(float(p), 2) if p else "?",
                "change_pct": round(float(c), 2) if c is not None else "?",
            }
        except Exception:
            data[key] = {"price": "?", "change_pct": "?"}
        await asyncio.sleep(0.3)

    # 2. KOSPI
    try:
        q = await get_yahoo_quote("^KS11")
        data["KOSPI"] = {
            "price":      round(float(q.get("price", 0)), 2),
            "change_pct": round(float(q.get("change_pct", 0)), 2),
        }
    except Exception:
        data["KOSPI"] = {"price": "?", "change_pct": "?"}

    # 3. USD/KRW
    try:
        q = await get_yahoo_quote("KRW=X")
        krw = float(q.get("price", 0) or 0)
        data["USDKRW"] = {
            "price":      f"{krw:.1f}" if krw else "?",
            "change_pct": round(float(q.get("change_pct", 0)), 2),
        }
    except Exception:
        data["USDKRW"] = {"price": "?", "change_pct": "?"}

    # 4. 외국인 KOSPI 수급 (업종별 합산)
    try:
        token = await get_kis_token()
        if token:
            total_frgn = 0
            for code, _ in WI26_SECTORS:
                frgn, _ = await _fetch_sector_flow(token, code)
                total_frgn += frgn
                await asyncio.sleep(0.1)
            data["FOREIGN_FLOW"] = {"amount_억": total_frgn}
        else:
            data["FOREIGN_FLOW"] = {"amount_억": "?"}
    except Exception:
        data["FOREIGN_FLOW"] = {"amount_억": "?"}

    # 5. 이벤트 캘린더 (날짜 미래 항목만 포함)
    events = load_events()
    now = datetime.now(KST)
    upcoming = {}
    for key, val in events.items():
        try:
            evt = datetime.strptime(val, "%Y-%m-%d")
            if evt.date() >= now.date():
                upcoming[key] = val
        except Exception:
            upcoming[key] = val   # "진행중" 같은 비날짜 값도 포함
    data["EVENTS"] = upcoming

    return data


def format_macro_msg(data: dict) -> str:
    """매크로 데이터 → 텔레그램 메시지 포맷"""
    def _p(d, prefix="", suffix=""):
        v = d.get("price", "?")
        return f"{prefix}{v}{suffix}" if v != "?" else "?"

    def _c(d):
        c = d.get("change_pct", "?")
        if c == "?":
            return "?"
        try:
            return f"{float(c):+.2f}%"
        except Exception:
            return str(c)

    now = datetime.now(KST)
    msg = f"📊 *매크로 대시보드* ({now.strftime('%m/%d %H:%M')} KST)\n\n"

    # [시장심리]
    vix   = data.get("VIX",   {})
    kospi = data.get("KOSPI", {})
    msg += "[시장심리]\n"
    msg += f"VIX: {_p(vix)} ({_c(vix)}) | KOSPI: {_p(kospi)} ({_c(kospi)})\n\n"

    # [가격지표]
    wti    = data.get("WTI",    {})
    gold   = data.get("GOLD",   {})
    copper = data.get("COPPER", {})
    dxy    = data.get("DXY",    {})
    usdkrw = data.get("USDKRW",{})
    us10y  = data.get("US10Y",  {})
    msg += "[가격지표]\n"
    msg += f"WTI: ${_p(wti)} ({_c(wti)}) | 금: ${_p(gold)} ({_c(gold)})\n"
    msg += f"구리: ${_p(copper)} ({_c(copper)}) | DXY: {_p(dxy)} ({_c(dxy)})\n"
    msg += f"USD/KRW: {_p(usdkrw)} ({_c(usdkrw)}) | US10Y: {_p(us10y)}% ({_c(us10y)})\n\n"

    # [수급]
    ff  = data.get("FOREIGN_FLOW", {})
    amt = ff.get("amount_억", "?")
    msg += "[수급]\n"
    if isinstance(amt, (int, float)):
        msg += f"외인 KOSPI: {amt:+,}억\n\n"
    else:
        msg += f"외인 KOSPI: {amt}\n\n"

    # [이벤트]
    events = data.get("EVENTS", {})
    if events:
        msg += "[이벤트]\n"
        for k, v in list(events.items())[:5]:
            msg += f"{k}: {v}\n"
        msg += "\n"

    msg += "→ Claude에서 레짐 점검하세요"
    return msg


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART API - 공시 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━
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
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "000":
                        return data.get("list", [])
    except Exception as e:
        print(f"DART API 오류: {e}")
    return []


def filter_important_disclosures(disclosures, watchlist_names):
    """워치리스트 기업의 중요 공시만 필터링"""
    important = []
    for d in disclosures:
        corp_name = d.get("corp_name", "")
        report_nm = d.get("report_nm", "")

        # 워치리스트 기업인지 확인
        is_watched = any(name in corp_name for name in watchlist_names)
        if not is_watched:
            continue

        # 중요 키워드 매칭
        is_important = any(kw in report_nm for kw in DART_KEYWORDS)
        # 주요사항보고서(B), 발행공시(C)는 항상 중요
        pblntf_ty = d.get("pblntf_ty", "")
        if pblntf_ty in ("B", "C"):
            is_important = True

        if is_important:
            important.append(d)

    return important


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART corp_code 매핑 & 재무 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━
DART_CORP_MAP_FILE = "/data/dart_corp_map.json"


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
      1. /data/dart_corp_map.json  (Railway Volume)
      2. <kis_api.py 디렉토리>/dart_corp_map.json  (레포 커밋 파일)
    """
    import os
    candidates = [
        DART_CORP_MAP_FILE,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "dart_corp_map.json"),
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
# 뉴스 조회 (Google News RSS)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def fetch_news(query="주식 시장 한국", max_items=8):
    """Google News RSS로 뉴스 헤드라인 가져오기"""
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # 간단한 XML 파싱
                    root = ET.fromstring(text)
                    items = root.findall(".//item")
                    results = []
                    for item in items[:max_items]:
                        title = item.find("title").text if item.find("title") is not None else ""
                        pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                        source = item.find("source").text if item.find("source") is not None else ""
                        results.append({"title": title, "date": pub_date, "source": source})
                    return results
    except Exception as e:
        print(f"뉴스 조회 오류: {e}")
    return []
