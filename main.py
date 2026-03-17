import os
import json
import re
import uuid
import asyncio
import aiohttp
from aiohttp import web
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

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

os.makedirs("/data", exist_ok=True)

WATCHLIST_FILE = "/data/watchlist.json"
STOPLOSS_FILE = "/data/stoploss.json"
US_WATCHLIST_FILE = "/data/us_watchlist.json"
DART_SEEN_FILE = "/data/dart_seen.json"
PORTFOLIO_FILE = "/data/portfolio.json"
WATCHALERT_FILE = "/data/watchalert.json"

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
    """미국 장 시간 여부 (KST 기준: 월~금 23:30~06:00)"""
    now = datetime.now(KST)
    wd, h, m = now.weekday(), now.hour, now.minute
    if h == 23 and m >= 30:
        return wd < 5          # 월~금 밤 KST → 미국 장중
    if h < 6:
        return 1 <= wd <= 5    # 화~토 새벽 KST (전날 미국 장중)
    return False

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
# 뉴스 조회 (Google News RSS)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def fetch_news(query="주식 시장 한국", max_items=8):
    """Google News RSS로 뉴스 헤드라인 가져오기"""
    import urllib.parse
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 1: 한국 장 마감 수급 요약
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def daily_kr_summary(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    try:
        # 1. 매크로
        macro = await get_yahoo_quote("^KS11") or {}
        kospi_p = macro.get("price", "?")
        kospi_c = macro.get("change_pct", "?")
        fx = await get_yahoo_quote("KRW=X") or {}
        krw = int(float(fx.get("price", 0)))

        # 1. 헤더: KOSPI 소수점 2자리, 환율
        kospi_c_f = round(float(kospi_c or 0), 2)
        kospi_e = "🔴" if kospi_c_f < 0 else "🟢"
        msg = f"📊 *한국 장 마감* ({now.strftime('%m/%d %H:%M')})\n"
        msg += f"{kospi_e} KOSPI {kospi_p} ({kospi_c_f:.2f}%)  💱 {krw:,}원\n\n"

        # 2. scan_market 미리 조회 (시장분위기 + 급등테마에 재사용)
        portfolio = load_json(PORTFOLIO_FILE, {})
        kr_stocks = {k: v for k, v in portfolio.items() if k != "us_stocks"}
        token = await get_kis_token()
        stops = load_stoploss()
        kr_stops = {k: v for k, v in stops.items() if k != "us_stocks" and isinstance(v, dict)}

        scan_rows = []
        try:
            scan_rows = await kis_volume_rank_api(token) or []
        except:
            pass

        # 시장 분위기: 인버스 ETF 상위 2개 안에 있으면 경고
        try:
            top2_names = [r.get("hts_kor_isnm", "") for r in scan_rows[:2]]
            if any("인버스" in n for n in top2_names):
                msg += "⚡ 인버스 ETF 강세 → 기관 하락 헤지 중\n\n"
        except:
            pass

        # 3. 내 포트
        # 가격 캐시 (손절선 섹션에서 재사용)
        price_cache = {}
        msg += "💼 *내 포트*\n"
        total_eval = 0
        total_pnl = 0
        for ticker, info in kr_stocks.items():
            try:
                pd = await get_stock_price(ticker, token)
                await asyncio.sleep(0.3)
                price = int(pd.get("stck_prpr", 0))
                chg = float(pd.get("prdy_ctrt", 0))
                price_cache[ticker] = price
                qty = info.get("qty", 0)
                avg = info.get("avg_price", 0)
                eval_amt = price * qty
                pnl = (price - avg) * qty
                total_eval += eval_amt
                total_pnl += pnl
                e = "🟢" if chg >= 1 else ("🔴" if chg <= -1 else "🟡")
                pnl_str = f"+{pnl:,}" if pnl >= 0 else f"{pnl:,}"
                # 목표가 달성률 (stoploss.json의 target)
                tgt_str = ""
                stop_info = kr_stops.get(ticker, {})
                tgt = float(stop_info.get("target_price") or stop_info.get("target") or 0)
                if tgt > 0 and price > 0:
                    tgt_pct = (tgt - price) / price * 100
                    tgt_str = f" | 목표 {tgt:,.0f} ({tgt_pct:+.1f}%)"
                msg += f"{e} {info.get('name', ticker)} {price:,}원 ({chg:+.1f}%) | {pnl_str}원{tgt_str}\n\n"
            except:
                pass
        pnl_str = f"+{total_pnl:,}" if total_pnl >= 0 else f"{total_pnl:,}"
        msg += f"┄ 총평가 {total_eval:,}원 | 손익 {pnl_str}원\n\n"

        # 4. 손절선 현황
        danger = []
        for ticker, info in kr_stops.items():
            if ticker == "us_stocks":
                continue
            try:
                if ticker in price_cache:
                    cur = price_cache[ticker]
                else:
                    pd = await get_stock_price(ticker, token)
                    await asyncio.sleep(0.2)
                    cur = int(pd.get("stck_prpr", 0))
                    price_cache[ticker] = cur
                stop = float(info.get("stop_price") or info.get("stop") or 0)
                if stop > 0 and cur > 0:
                    gap = (cur - stop) / cur * 100
                    if gap <= 7:
                        danger.append((info.get("name", ticker), cur, stop, gap))
            except:
                pass
        if danger:
            msg += "🛑 *손절선 현황*\n"
            for name, cur, stop, gap in sorted(danger, key=lambda x: x[3]):
                msg += f"⚠️ {name} {cur:,}원 → 손절 {stop:,}원 ({gap:.1f}% 남음)\n"
            msg += "\n"
        else:
            msg += "✅ 전 종목 손절선 여유\n\n"

        # 5. 오늘 급등 테마 (ETF/인버스 제외, +10% 이상)
        try:
            surge = [
                r for r in scan_rows
                if "ETF" not in r.get("hts_kor_isnm", "")
                and "인버스" not in r.get("hts_kor_isnm", "")
                and float(r.get("prdy_ctrt", 0) or 0) >= 10
            ]
            if surge:
                parts = [f"{r.get('hts_kor_isnm', '?')} {float(r.get('prdy_ctrt', 0)):+.0f}%" for r in surge[:3]]
                msg += f"🔥 *오늘 급등* {' · '.join(parts)}\n\n"
        except:
            pass

        # 6. 섹터 흐름
        try:
            sectors = []
            for code, label in WI26_SECTORS:
                frgn, orgn = await _fetch_sector_flow(token, code)
                sectors.append({"sector": label, "total": frgn + orgn})
            sectors.sort(key=lambda x: x["total"], reverse=True)
            if any(s["total"] != 0 for s in sectors):
                msg += f"📡 *섹터* 유입 {sectors[0]['sector']} | 유출 {sectors[-1]['sector']}\n\n"
        except:
            pass

        # 7. 오늘 DART 공시 (있을 때만)
        try:
            disclosures = await search_dart_disclosures(days_back=1)
            watchlist = load_watchlist()
            important = filter_important_disclosures(disclosures, list(watchlist.values()))
            if important:
                msg += "📢 *오늘 공시*\n"
                for d in important[:3]:
                    msg += f"• {d.get('corp_name', '?')} — {d.get('report_nm', '?')}\n"
                msg += "\n"
        except:
            pass

        # 8. 내일 할 일 (손절선 가장 가까운 종목 1개)
        if danger:
            top = sorted(danger, key=lambda x: x[3])[0]
            msg += f"📌 *내일 체크*\n⚠️ {top[0]} 손절선 {top[3]:.1f}% 근접 주시"

        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

    except Exception as e:
        print(f"daily_kr_summary 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 2: 미국 장 마감 요약
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def daily_us_summary(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() in (0, 6):
        return
    try:
        # ── 1. 헤더: 나스닥 / S&P500 / VIX / 환율 ───────────────────
        sp500 = await get_yahoo_quote("^GSPC")
        nasdaq = await get_yahoo_quote("^IXIC")
        vix   = await get_yahoo_quote("^VIX")
        fx    = await get_yahoo_quote("KRW=X")
        sp_p  = sp500.get("price", 0) if sp500 else 0
        sp_c  = sp500.get("change_pct", 0) if sp500 else 0
        nq_p  = nasdaq.get("price", 0) if nasdaq else 0
        nq_c  = nasdaq.get("change_pct", 0) if nasdaq else 0
        vix_p = vix.get("price", 0) if vix else 0
        fx_rate = float(fx.get("price", 1300) or 1300) if fx else 1300

        ss = "🔴" if sp_c < 0 else "🟢"
        ns = "🔴" if nq_c < 0 else "🟢"
        vix_label = "🔴 위기" if vix_p > 25 else "🟠 경계" if vix_p > 20 else "🟡 중립" if vix_p > 15 else "🟢 공격"
        msg = (
            f"🇺🇸 *미국 장 마감* ({now.strftime('%m/%d %H:%M')})\n"
            f"{ss} S&P500 {sp_p:,.0f} ({sp_c:+.1f}%)  "
            f"{ns} NASDAQ {nq_p:,.0f} ({nq_c:+.1f}%)\n"
            f"😰 VIX {vix_p:.1f} — {vix_label} | 💱 {fx_rate:,.0f}원\n"
        )

        # ── 2. 미국 포트 ─────────────────────────────────────────────
        portfolio = load_json(PORTFOLIO_FILE, {})
        us_port = portfolio.get("us_stocks", {})
        if us_port:
            msg += "\n💼 *미국 포트*\n"
            total_eval = total_cost = 0.0
            for sym, info in us_port.items():
                try:
                    d = await get_yahoo_quote(sym)
                    await asyncio.sleep(0.3)
                    cur = float(d.get("price", 0) or 0) if d else 0
                    chg = float(d.get("change_pct", 0) or 0) if d else 0
                    qty = info.get("qty", 0)
                    avg = float(info.get("avg_price", 0))
                    eval_amt = round(cur * qty, 2)
                    cost_amt = round(avg * qty, 2)
                    pnl = round(eval_amt - cost_amt, 2)
                    total_eval += eval_amt
                    total_cost += cost_amt
                    em = "🟢" if chg >= 1 else "⚠️" if chg <= -1 else "⚪"
                    msg += f"{em} *{info.get('name', sym)}* ${cur:,.2f} ({chg:+.1f}%) | {qty}주 손익 ${pnl:+,.2f}\n"
                except Exception:
                    msg += f"⚪ *{info.get('name', sym)}* 조회 실패\n"
            if total_cost > 0:
                total_pnl = round(total_eval - total_cost, 2)
                total_pnl_pct = total_pnl / total_cost * 100
                total_krw = total_eval * fx_rate
                msg += f"┄ 총평가 ${total_eval:,.2f} (₩{total_krw:,.0f}) | 손익 *${total_pnl:+,.2f}* ({total_pnl_pct:+.1f}%)\n"

        # ── 3. 손절선 현황 ────────────────────────────────────────────
        stops = load_stoploss()
        us_stops = stops.get("us_stocks", {})
        if us_stops:
            msg += "\n🛑 *손절선 현황*\n"
            danger = []
            for sym, info in us_stops.items():
                try:
                    d = await get_yahoo_quote(sym)
                    await asyncio.sleep(0.2)
                    cur = float(d.get("price", 0) or 0) if d else 0
                    sp = float(info.get("stop_price") or info.get("stop") or 0)
                    if cur > 0 and sp > 0:
                        gap = (sp - cur) / cur * 100
                        if gap >= -7:
                            danger.append(f"⚠️ *{info.get('name', sym)}* 손절 ${sp:,.2f} ({gap:+.1f}%)")
                except Exception:
                    pass
            if danger:
                msg += "\n".join(danger) + "\n"
            else:
                msg += "전 종목 손절선 여유 있음\n"

        # ── 4. 내일 할 일 ─────────────────────────────────────────────
        action_lines = []
        closest = None
        closest_gap = -999
        for sym, info in us_stops.items():
            try:
                d = await get_yahoo_quote(sym)
                cur = float(d.get("price", 0) or 0) if d else 0
                sp = float(info.get("stop_price") or info.get("stop") or 0)
                if cur > 0 and sp > 0:
                    gap = (sp - cur) / cur * 100
                    if gap > closest_gap:
                        closest_gap = gap
                        closest = (info.get("name", sym), sp, gap)
            except Exception:
                pass
        if closest:
            action_lines.append(f"🎯 *{closest[0]}* 손절선 ${closest[1]:,.2f} ({closest[2]:+.1f}%) 모니터링")
        for sym, info in us_port.items():
            try:
                d = await get_yahoo_quote(sym)
                cur = float(d.get("price", 0) or 0) if d else 0
                tgt = float(info.get("target_price") or 0)
                if cur > 0 and tgt > 0 and (tgt - cur) / cur * 100 <= 5:
                    action_lines.append(f"🏁 *{info.get('name', sym)}* 목표가 ${tgt:,.2f}까지 {((tgt-cur)/cur*100):+.1f}%")
            except Exception:
                pass
        if action_lines:
            msg += "\n📌 *내일 할 일*\n" + "\n".join(action_lines) + "\n"

        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"미국 요약 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 3: 손절선 도달 (10분마다)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_stoploss(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    is_kr = not (now.weekday() >= 5 or now.hour < 9 or (now.hour >= 15 and now.minute > 30))
    is_us = _is_us_market_hours_kst()
    if not is_kr and not is_us:
        return

    stops = load_stoploss()
    kr_stops = {k: v for k, v in stops.items() if k != "us_stocks"}
    us_stops = stops.get("us_stocks", {})
    wa = load_watchalert()
    if not kr_stops and not us_stops and not wa:
        return

    alerts = []

    if is_kr and kr_stops:
        try:
            token = await get_kis_token()
            if token:
                for ticker, info in kr_stops.items():
                    try:
                        d = await get_stock_price(ticker, token)
                        await asyncio.sleep(0.3)
                        price = int(d.get("stck_prpr", 0))
                        sp = info.get("stop_price", 0)
                        if price > 0 and sp > 0 and price <= sp:
                            ep = info.get("entry_price", 0)
                            drop = ((price - ep) / ep * 100) if ep > 0 else 0
                            alerts.append(
                                f"🚨🚨 *{info['name']}* ({ticker})\n"
                                f"  현재가: {price:,}원 ← 손절선 {sp:,}원 도달!\n"
                                + (f"  손실: {drop:.1f}%\n" if ep > 0 else "")
                                + "  → *즉시 매도 검토!*"
                            )
                    except Exception:
                        pass
        except Exception as e:
            print(f"KR 손절 체크 오류: {e}")

    if is_us and us_stops:
        for sym, info in us_stops.items():
            try:
                d = await get_yahoo_quote(sym)
                await asyncio.sleep(0.3)
                if not d:
                    continue
                price = float(d.get("price", 0) or 0)
                sp = info.get("stop_price", 0)
                if price > 0 and sp > 0 and price <= sp:
                    tp = info.get("target_price", 0)
                    alerts.append(
                        f"🚨🇺🇸 *{info['name']}* ({sym})\n"
                        f"  현재가: ${price:,.2f} ← 손절선 ${sp:,.2f} 도달!\n"
                        + (f"  목표가: ${tp:,.2f}\n" if tp else "")
                        + "  → *즉시 매도 검토!*"
                    )
            except Exception:
                pass

    if alerts:
        msg = "🔴🔴🔴 *손절선 도달!* 🔴🔴🔴\n\n" + "\n\n".join(alerts) + "\n\n⚠️ Thesis 붕괴 시 가격 무관 즉시 매도"
        try:
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        except Exception as e:
            print(f"손절 알림 전송 오류: {e}")

    # ── 매수 희망가 감시 (watchalert) ──
    try:
        wa = load_watchalert()
        if wa:
            token_wa = await get_kis_token()
            buy_alerts = []
            for ticker, info in wa.items():
                try:
                    buy_price = info.get("buy_price", 0)
                    if buy_price <= 0:
                        continue
                    cur = 0.0
                    if _is_us_ticker(ticker):
                        if not is_us:
                            continue
                        d = await kis_us_stock_price(ticker, token_wa)
                        cur = float(d.get("last", 0) or 0)
                    else:
                        if not is_kr:
                            continue
                        d = await get_stock_price(ticker, token_wa)
                        cur = int(d.get("stck_prpr", 0) or 0)
                    await asyncio.sleep(0.3)
                    if cur > 0 and cur <= buy_price:
                        memo = info.get("memo", "")
                        if _is_us_ticker(ticker):
                            buy_alerts.append(
                                f"🟢🇺🇸 *{info['name']}* ({ticker})\n"
                                f"  현재가: ${cur:,.2f} ← 매수희망가 ${buy_price:,.2f} 도달!\n"
                                + (f"  📝 {memo}\n" if memo else "")
                                + "  → *매수 검토!*"
                            )
                        else:
                            buy_alerts.append(
                                f"🟢🇰🇷 *{info['name']}* ({ticker})\n"
                                f"  현재가: {cur:,}원 ← 매수희망가 {buy_price:,.0f}원 도달!\n"
                                + (f"  📝 {memo}\n" if memo else "")
                                + "  → *매수 검토!*"
                            )
                except Exception:
                    pass
            if buy_alerts:
                msg = "🟢🟢🟢 *매수 희망가 도달!* 🟢🟢🟢\n\n" + "\n\n".join(buy_alerts)
                try:
                    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                except Exception as e:
                    print(f"매수감시 알림 전송 오류: {e}")
    except Exception as e:
        print(f"매수감시 체크 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 4: 환율 급변 (1시간마다)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_fx_alert(context: ContextTypes.DEFAULT_TYPE):
    try:
        d = await get_yahoo_quote("KRW=X")
        c = d.get("change_pct", 0)
        if abs(c) >= 1.0:
            rate = d["price"]
            direction = "급등 📈" if c > 0 else "급락 📉"
            impact = "원화약세 → 미국주식 원화이익↑" if c > 0 else "원화강세 → 미국주식 원화이익↓"
            msg = f"💱 *환율 {direction}*\n\nUSD/KRW: {rate:,.1f}원 ({c:+.1f}%)\n📌 {impact}"
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"환율 체크 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 5: 복합 이상 신호 (30분마다)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_anomaly(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() >= 5 or now.hour < 9 or (now.hour >= 15 and now.minute > 30):
        return
    try:
        token = await get_kis_token()
        if not token:
            return
        watchlist = load_watchlist()
        alerts = []
        for ticker, name in watchlist.items():
            try:
                pd = await get_stock_price(ticker, token)
                await asyncio.sleep(0.4)
                price = int(pd.get("stck_prpr", 0))
                change = pd.get("prdy_ctrt", "0")
                vol_rate = pd.get("prdy_vrss_vol_rate", "0")
                mcap = int(pd.get("hts_avls", 0))

                vol_ok = False
                try: vol_ok = float(vol_rate) >= 150
                except: pass

                inv = await get_investor_trend(ticker, token)
                await asyncio.sleep(0.4)
                fr, fn = 0.0, 0
                if inv and len(inv) > 0:
                    t = inv[0] if isinstance(inv, list) else inv
                    fn = int(t.get("frgn_ntby_qty", 0))
                    if mcap > 0 and price > 0:
                        fr = (fn * price) / (mcap * 1e8) * 100

                if vol_ok and fr > 0.03:
                    alerts.append(
                        f"🚨 *{name}* ({ticker})\n"
                        f"  {price:,}원 ({change}%)\n"
                        f"  거래량 {vol_rate}%↑ + 외국인 {fr:+.3f}%\n"
                        f"  → *복합 매수 신호!*"
                    )
            except Exception:
                pass
        if alerts:
            msg = f"🔔 *복합 신호* ({now.strftime('%H:%M')})\n\n"
            for a in alerts:
                msg += f"{a}\n\n"
            msg += "💡 Claude에서 진입 분석하세요"
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"이상 신호 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 6: 주간 리뷰 리마인더 (일 10:00)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def weekly_review(context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📋 *주간 리뷰 시간입니다*\n\n"
        "Claude에서 점검하세요:\n"
        "1️⃣ 보유 종목 Thesis 유효?\n"
        "2️⃣ 손절/익절 대상?\n"
        "3️⃣ 섹터 모멘텀 생존?\n"
        "4️⃣ 다음 주 매크로 이벤트?\n"
        "5️⃣ 현금 비중 적절?\n\n"
        "💡 스크린샷 + \"리뷰해줘\" 보내세요"
    )
    try:
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"주간 리뷰 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 7: DART 공시 체크 (30분마다)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_dart_disclosure(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    # 장중 + 전후 1시간 (08:00~16:30)
    if now.hour < 8 or now.hour > 16:
        return
    if not DART_API_KEY:
        return

    try:
        disclosures = await search_dart_disclosures(days_back=1)
        if not disclosures:
            return

        # 워치리스트 기업명 목록
        watchlist = load_watchlist()
        wl_names = list(watchlist.values())

        # 중요 공시 필터링
        important = filter_important_disclosures(disclosures, wl_names)
        if not important:
            return

        # 이미 알림 보낸 공시 제외
        seen_data = load_dart_seen()
        seen_ids = set(seen_data.get("ids", []))

        new_disclosures = [d for d in important if d.get("rcept_no", "") not in seen_ids]
        if not new_disclosures:
            return

        msg = f"📢 *DART 공시 알림* ({now.strftime('%H:%M')})\n\n"
        new_ids = []

        for d in new_disclosures[:5]:  # 최대 5개
            corp = d.get("corp_name", "?")
            title = d.get("report_nm", "?")
            date = d.get("rcept_dt", "?")
            rcept_no = d.get("rcept_no", "")
            link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

            msg += f"🏢 *{corp}*\n"
            msg += f"📄 {title}\n"
            msg += f"📅 {date}\n"
            msg += f"🔗 [공시 원문]({link})\n\n"

            new_ids.append(rcept_no)

        msg += "💡 Claude에서 영향 분석하세요"
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown", disable_web_page_preview=True)

        # 알림 보낸 공시 ID 저장
        seen_ids.update(new_ids)
        # 최근 500개만 유지
        seen_list = list(seen_ids)[-500:]
        save_json(DART_SEEN_FILE, {"ids": seen_list})

    except Exception as e:
        print(f"DART 체크 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 텔레그램 명령어
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *부자가될거야 봇 v7*\n\n"
        "📌 *조회*\n"
        "/analyze 코드 · /scan · /macro · /news\n"
        "/summary · /dart\n\n"
        "👀 *한국 워치리스트*\n"
        "/watchlist · /watch · /unwatch\n\n"
        "🇺🇸 *미국 종목 관리*\n"
        "/uslist · /addus · /remus\n\n"
        "🛑 *손절관리*\n"
        "/setstop · /delstop · /stops\n\n"
        "🔔 *자동알림* — 설정 불필요!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("사용법: /analyze 005930")
        return
    ticker = context.args[0]
    await update.message.reply_text(f"⏳ {ticker} 분석 중...")
    try:
        token = await get_kis_token()
        if not token:
            await update.message.reply_text("❌ KIS 토큰 실패"); return
        d = await get_stock_price(ticker, token)
        if not d or not d.get("stck_prpr"):
            await update.message.reply_text(f"❌ {ticker} 없음"); return

        price = int(d.get("stck_prpr", 0))
        change = d.get("prdy_ctrt", "0")
        chg_amt = int(d.get("prdy_vrss", 0))
        vol = int(d.get("acml_vol", 0))
        hi = int(d.get("stck_hgpr", 0))
        lo = int(d.get("stck_lwpr", 0))
        op = int(d.get("stck_oprc", 0))
        mcap = int(d.get("hts_avls", 0))
        per = d.get("per", "-")
        pbr = d.get("pbr", "-")
        vr = d.get("prdy_vrss_vol_rate", "0")

        inv = await get_investor_trend(ticker, token)
        fn, ins, fr = 0, 0, 0.0
        if inv and len(inv) > 0:
            t = inv[0] if isinstance(inv, list) else inv
            fn = int(t.get("frgn_ntby_qty", 0))
            ins = int(t.get("orgn_ntby_qty", 0))
            if mcap > 0 and price > 0:
                fr = (fn * price) / (mcap * 1e8) * 100

        cs = "📉" if float(change) < 0 else "📈" if float(change) > 0 else "➡️"
        vt = ""
        try:
            v = float(vr)
            if v >= 200: vt = "🔥 급증"
            elif v >= 150: vt = "⚡ 증가"
            elif v <= 50: vt = "😴 감소"
        except: pass

        msg = (
            f"{cs} *{ticker} 분석*\n\n"
            f"💰 *{price:,}원* ({chg_amt:+,} / {change}%)\n\n"
            f"📊 시가 {op:,} | 고 {hi:,} | 저 {lo:,}\n"
            f"📦 거래량 {vol:,}주 ({vr}%) {vt}\n\n"
            f"👥 *수급*\n"
            f"  외국인: {fn:+,}주 (시총 {fr:+.4f}%)\n"
            f"  기관: {ins:+,}주\n\n"
            f"🏢 시총 {mcap:,}억 | PER {per} | PBR {pbr}\n"
            f"⏰ {datetime.now(KST).strftime('%Y-%m-%d %H:%M')}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {str(e)}")


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 스캔 중...")
    try:
        token = await get_kis_token()
        if not token:
            await update.message.reply_text("❌ 토큰 실패"); return
        results = await get_volume_rank(token)
        if not results:
            await update.message.reply_text("📭 급등 종목 없음"); return
        msg = "🔍 *거래량 급등 TOP 10*\n\n"
        for i, item in enumerate(results[:10], 1):
            n = item.get("hts_kor_isnm", "?")
            t = item.get("mksc_shrn_iscd", "?")
            p = int(item.get("stck_prpr", 0))
            c = item.get("prdy_ctrt", "0")
            v = item.get("prdy_vol_vrss_acml_vol_rate", "0")
            cs = "🔴" if float(c) < 0 else "🟢" if float(c) > 0 else "⚪"
            msg += f"{i}. {cs} *{n}* ({t})\n   {p:,}원 ({c}%) | 거래량 {v}%↑\n\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {str(e)}")


async def macro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 매크로 조회 중...")
    try:
        # KIS API로 KOSPI 조회
        token = await get_kis_token()
        kospi_data = {}
        kosdaq_data = {}
        if token:
            try:
                kospi_data = await get_kis_index(token, "0001")
                await asyncio.sleep(0.3)
                kosdaq_data = await get_kis_index(token, "1001")
            except Exception:
                pass

        # Yahoo로 나머지 조회
        yahoo_symbols = {"^VIX": "VIX", "KRW=X": "USD/KRW", "CL=F": "WTI유가", "^TNX": "10년금리", "^GSPC": "S&P500"}
        msg = "🌐 *매크로 현황*\n\n"
        vix_val = 0

        for sym, name in yahoo_symbols.items():
            d = await get_yahoo_quote(sym)
            await asyncio.sleep(0.3)
            p, c = d["price"], d["change_pct"]
            cs = "🔴" if c < 0 else "🟢" if c > 0 else "⚪"
            if "KRW" in name: ps = f"{p:,.1f}원"
            elif "금리" in name: ps = f"{p:.2f}%"
            elif "VIX" in name:
                ps = f"{p:.1f}"
                vix_val = p
                if p > 25: ps += " 🔴위기"
                elif p > 20: ps += " 🟠경계"
                elif p < 15: ps += " 🟢안정"
            elif "유가" in name: ps = f"${p:.1f}"
            else: ps = f"{p:,.1f}"
            msg += f"{cs} *{name}* {ps} ({c:+.1f}%)\n"

        # KIS KOSPI/KOSDAQ
        if kospi_data:
            kp = kospi_data.get("bstp_nmix_prpr", "0")
            kc = kospi_data.get("bstp_nmix_prdy_ctrt", "0")
            kcs = "🔴" if float(kc) < 0 else "🟢" if float(kc) > 0 else "⚪"
            msg += f"{kcs} *KOSPI* {float(kp):,.1f} ({kc}%)\n"

        if kosdaq_data:
            kqp = kosdaq_data.get("bstp_nmix_prpr", "0")
            kqc = kosdaq_data.get("bstp_nmix_prdy_ctrt", "0")
            kqcs = "🔴" if float(kqc) < 0 else "🟢" if float(kqc) > 0 else "⚪"
            msg += f"{kqcs} *KOSDAQ* {float(kqp):,.1f} ({kqc}%)\n"

        msg += "\n━━━━━━━━━━━━━━━━\n"
        if vix_val > 25: msg += "🔴 *레짐: 위기* — 신규매수 금지"
        elif vix_val > 20: msg += "🟠 *레짐: 경계* — 기존 포지션만 관리"
        elif vix_val > 15: msg += "🟡 *레짐: 중립* — 확신 높은 것만"
        else: msg += "🟢 *레짐: 공격* — 핵심 섹터 적극 매수"

        msg += f"\n\n⏰ {datetime.now(KST).strftime('%Y-%m-%d %H:%M')}"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {str(e)}")


# /news 뉴스 요약
async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args) if context.args else "주식 증시 코스피"
    await update.message.reply_text(f"⏳ 뉴스 조회 중... ({query})")

    try:
        articles = await fetch_news(query, max_items=8)
        if not articles:
            await update.message.reply_text("📭 뉴스를 가져올 수 없습니다.")
            return

        msg = f"📰 *뉴스* ({query})\n\n"
        for i, a in enumerate(articles, 1):
            title = a["title"]
            source = a.get("source", "")
            # 제목이 너무 길면 자르기
            if len(title) > 60:
                title = title[:57] + "..."
            msg += f"{i}. {title}\n"
            if source:
                msg += f"   _{source}_\n"
            msg += "\n"

        msg += "💡 Claude에서 \"이 뉴스가 내 포트폴리오에 영향?\" 물어보세요"
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ 뉴스 오류: {str(e)}")


# /dart 수동 공시 조회
async def dart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not DART_API_KEY:
        await update.message.reply_text("❌ DART API 키 미설정\nRailway Variables에 DART_API_KEY 추가하세요.")
        return

    await update.message.reply_text("⏳ DART 공시 조회 중...")

    try:
        disclosures = await search_dart_disclosures(days_back=3)
        if not disclosures:
            await update.message.reply_text("📭 최근 3일간 공시가 없습니다.")
            return

        watchlist = load_watchlist()
        wl_names = list(watchlist.values())
        important = filter_important_disclosures(disclosures, wl_names)

        if not important:
            # 워치리스트 관련 중요 공시 없으면 전체 중 최근 5개
            msg = "📢 *최근 DART 공시* (워치리스트 관련 없음)\n\n"
            for d in disclosures[:5]:
                corp = d.get("corp_name", "?")
                title = d.get("report_nm", "?")
                date = d.get("rcept_dt", "?")
                msg += f"• *{corp}* - {title} ({date})\n\n"
            msg += "워치리스트 종목 관련 중요 공시는 없습니다."
        else:
            msg = f"📢 *워치리스트 관련 공시* (최근 3일)\n\n"
            for d in important[:10]:
                corp = d.get("corp_name", "?")
                title = d.get("report_nm", "?")
                date = d.get("rcept_dt", "?")
                rcept_no = d.get("rcept_no", "")
                link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
                msg += f"🏢 *{corp}*\n📄 {title}\n📅 {date}\n🔗 [원문]({link})\n\n"

        await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

    except Exception as e:
        await update.message.reply_text(f"❌ DART 오류: {str(e)}")


# 워치리스트
async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wl = load_watchlist()
    if not wl:
        await update.message.reply_text("📭 비어있음. /watch 코드 이름"); return
    msg = "👀 *한국 워치리스트*\n\n"
    for t, n in wl.items():
        msg += f"• {n} ({t})\n"
    msg += f"\n총 {len(wl)}개 감시 중"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("사용법: /watch 005930 삼성전자"); return
    wl = load_watchlist()
    wl[context.args[0]] = context.args[1]
    save_json(WATCHLIST_FILE, wl)
    await update.message.reply_text(f"✅ *{context.args[1]}* 추가!", parse_mode="Markdown")


async def unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("사용법: /unwatch 005930"); return
    wl = load_watchlist()
    if context.args[0] in wl:
        n = wl.pop(context.args[0])
        save_json(WATCHLIST_FILE, wl)
        await update.message.reply_text(f"🗑 *{n}* 삭제!", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ 없음")


# 🇺🇸 미국 종목 관리
async def uslist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    us = load_us_watchlist()
    if not us:
        await update.message.reply_text("📭 비어있음. /addus TSLA 테슬라 12"); return
    msg = "🇺🇸 *미국 보유 종목*\n\n"
    for sym, info in us.items():
        msg += f"• *{info['name']}* ({sym}) - {info['qty']}주\n"
    msg += f"\n총 {len(us)}개 종목"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def addus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("사용법: /addus TSLA 테슬라 12\n(심볼 이름 수량)"); return
    sym = context.args[0].upper()
    name = context.args[1]
    try:
        qty = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ 수량은 숫자로"); return
    us = load_us_watchlist()
    us[sym] = {"name": name, "qty": qty}
    save_json(US_WATCHLIST_FILE, us)
    await update.message.reply_text(f"✅ 🇺🇸 *{name}* ({sym}) {qty}주 추가!", parse_mode="Markdown")


async def remus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("사용법: /remus TSLA"); return
    sym = context.args[0].upper()
    us = load_us_watchlist()
    if sym in us:
        name = us.pop(sym)["name"]
        save_json(US_WATCHLIST_FILE, us)
        await update.message.reply_text(f"🗑 *{name}* ({sym}) 삭제!", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ {sym} 없음")


# 손절 관리
async def setstop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text(
            "사용법: /setstop 코드 이름 손절가 [진입가/목표가]\n"
            "KR: /setstop 034020 두산에너빌리티 88000 98000\n"
            "US: /setstop TSLA TSLA 372 425"
        ); return
    ticker, name = context.args[0].upper(), context.args[1]
    try: stop = float(context.args[2])
    except: await update.message.reply_text("❌ 손절가는 숫자"); return
    fourth = 0.0
    if len(context.args) >= 4:
        try: fourth = float(context.args[3])
        except: pass
    stops = load_stoploss()
    if _is_us_ticker(ticker):
        us = stops.get("us_stocks", {})
        us[ticker] = {"name": name, "stop_price": stop, "target_price": fourth}
        stops["us_stocks"] = us
        save_json(STOPLOSS_FILE, stops)
        tp = f", 목표가 ${fourth:,.2f}" if fourth else ""
        await update.message.reply_text(
            f"🇺🇸 *{name}* 손절 ${stop:,.2f}{tp}\n장중 자동 체크", parse_mode="Markdown")
    else:
        stops[ticker] = {"name": name, "stop_price": stop, "entry_price": fourth}
        save_json(STOPLOSS_FILE, stops)
        lp = f" (진입가 대비 {((stop - fourth) / fourth * 100):.1f}%)" if fourth > 0 else ""
        await update.message.reply_text(
            f"🛑 *{name}* 손절선 {stop:,.0f}원{lp}\n장중 10분마다 체크", parse_mode="Markdown")


async def delstop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("사용법: /delstop 코드"); return
    ticker = context.args[0].upper()
    stops = load_stoploss()
    if _is_us_ticker(ticker):
        us = stops.get("us_stocks", {})
        if ticker in us:
            n = us.pop(ticker)["name"]
            stops["us_stocks"] = us
            save_json(STOPLOSS_FILE, stops)
            await update.message.reply_text(f"🗑 *{n}* 손절선 삭제!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 없음")
    else:
        if ticker in stops:
            n = stops.pop(ticker)["name"]
            save_json(STOPLOSS_FILE, stops)
            await update.message.reply_text(f"🗑 *{n}* 손절선 삭제!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 없음")


async def stops_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stops = load_stoploss()
    kr = {k: v for k, v in stops.items() if k != "us_stocks" and isinstance(v, dict)}
    us = stops.get("us_stocks") or {}
    if not kr and not us:
        await update.message.reply_text("📭 손절선 없음\n/setstop 코드 이름 손절가 [진입가/목표가]"); return
    msg = "🛑 *손절선 목록*\n\n"
    if kr:
        msg += "🇰🇷 *한국 종목*\n"
        for t, i in kr.items():
            try:
                sp = float(i.get("stop_price") or i.get("stop") or 0)
                ep = float(i.get("entry_price") or 0)
                tgt = float(i.get("target_price") or 0)
                lp = f" | 진입 {ep:,.0f} ({((sp-ep)/ep*100):.1f}%)" if ep > 0 else ""
                tp = f" → 목표 {tgt:,.0f}원" if tgt > 0 else ""
                msg += f"• *{i.get('name', t)}* ({t}): {sp:,.0f}원{lp}{tp}\n"
            except Exception as e:
                msg += f"• ({t}): 읽기 오류 {e}\n"
        msg += "\n"
    if us:
        msg += "🇺🇸 *미국 종목*\n"
        for sym, i in us.items():
            try:
                sp = float(i.get("stop_price") or i.get("stop") or 0)
                tgt = float(i.get("target_price") or i.get("target") or 0)
                tp = f" → 목표 ${tgt:,.2f}" if tgt > 0 else ""
                msg += f"• *{i.get('name', sym)}* ({sym}): ${sp:,.2f}{tp}\n"
            except Exception as e:
                msg += f"• ({sym}): 읽기 오류 {e}\n"
        msg += "\n"
    msg += "장중 10분마다 자동 체크"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def manual_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 요약 생성 중...")
    await daily_kr_summary(context)


async def setportfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """형식: /setportfolio 종목코드,수량,평단가 ..."""
    if not context.args:
        await update.message.reply_text(
            "사용법: /setportfolio 종목코드,수량,평단가 ...\n"
            "예시: /setportfolio 009540,50,413590 298040,2,2800000"
        )
        return

    portfolio = load_json(PORTFOLIO_FILE, {})
    added, errors = [], []

    for arg in context.args:
        parts = arg.split(",")
        if len(parts) != 3:
            errors.append(f"❌ 형식 오류: {arg}")
            continue
        ticker, qty_s, avg_s = parts
        ticker = ticker.strip()
        try:
            qty = int(qty_s.strip())
            avg = int(avg_s.strip())
        except ValueError:
            errors.append(f"❌ 숫자 오류: {arg}")
            continue
        wl = load_watchlist()
        name = wl.get(ticker, ticker)
        portfolio[ticker] = {"name": name, "qty": qty, "avg_price": avg}
        added.append(f"✅ {name}({ticker}) {qty}주 @ {avg:,}원")

    save_json(PORTFOLIO_FILE, portfolio)

    lines = ["📁 *포트폴리오 저장 완료*\n"] + added + (errors or [])
    lines.append(f"\n총 {len(portfolio)}종목 저장됨")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def setusportfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """형식: /setusportfolio 심볼,수량,평단가(USD) ..."""
    if not context.args:
        await update.message.reply_text(
            "사용법: /setusportfolio 심볼,수량,평단가 ...\n"
            "예시: /setusportfolio TSLA,12,431.92 CRSP,70,55.03"
        )
        return

    portfolio = load_json(PORTFOLIO_FILE, {})
    us_stocks = portfolio.get("us_stocks", {})
    added, errors = [], []

    for arg in context.args:
        parts = arg.split(",")
        if len(parts) != 3:
            errors.append(f"❌ 형식 오류: {arg}")
            continue
        symbol, qty_s, avg_s = parts
        symbol = symbol.strip().upper()
        try:
            qty = int(qty_s.strip())
            avg = float(avg_s.strip())
        except ValueError:
            errors.append(f"❌ 숫자 오류: {arg}")
            continue
        us_stocks[symbol] = {"name": symbol, "qty": qty, "avg_price": avg}
        added.append(f"✅ {symbol} {qty}주 @ ${avg:,.2f}")

    portfolio["us_stocks"] = us_stocks
    save_json(PORTFOLIO_FILE, portfolio)

    lines = ["🇺🇸 *해외 포트폴리오 저장 완료*\n"] + added + (errors or [])
    lines.append(f"\n총 {len(us_stocks)}종목 저장됨")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📖 *도움말 v7*\n\n"
        "📌 *조회*\n"
        "/analyze 코드 - 종목분석(수급포함)\n"
        "/scan - 거래량 급등 TOP10\n"
        "/macro - VIX/환율/유가/금리/KOSPI/KOSDAQ\n"
        "/news [키워드] - 뉴스 헤드라인\n"
        "/dart - 워치리스트 DART 공시\n"
        "/summary - 한국 장마감 요약(수동)\n\n"
        "👀 *한국 워치리스트*\n"
        "/watchlist · /watch 코드 이름 · /unwatch 코드\n\n"
        "🇺🇸 *미국 종목*\n"
        "/uslist · /addus 심볼 이름 수량 · /remus 심볼\n\n"
        "🛑 *손절관리*\n"
        "/setstop 코드 이름 손절가 진입가\n"
        "/delstop 코드 · /stops\n\n"
        "🔔 *자동 알림*\n"
        "• 🔴 손절선: 장중 10분마다\n"
        "• 🔴 복합신호: 장중 30분마다\n"
        "• 📢 DART공시: 장중 30분마다\n"
        "• 📊 한국요약: 평일 15:40\n"
        "• 🇺🇸 미국요약: 평일 07:00\n"
        "• 💱 환율급변: 1시간마다\n"
        "• 📋 주간리뷰: 일 10:00\n\n"
        "💡 심층 분석은 Claude.ai에서!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 봇 시작
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def post_init(application: Application):
    dart_status = "✅ DART 활성" if DART_API_KEY else "❌ DART 미설정 (DART_API_KEY 필요)"
    try:
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"✅ *부자가될거야 v7 시작!*\n\n"
                f"🔔 알림: 손절/복합신호/DART/장마감/미국/환율/주간리뷰\n"
                f"📢 {dart_status}\n"
                f"/help"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"시작 알림 실패: {e}")

    # ── KIS API 시작 테스트 ──────────────────────────────────────
    if KIS_APP_KEY and KIS_APP_SECRET:
        lines = ["🔬 *KIS API 시작테스트* (005930 삼성전자)\n"]
        try:
            token = await get_kis_token()
            lines.append(f"🔑 토큰 발급: ✅")

            async def chk(label, coro):
                try:
                    r = await coro
                    ok = bool(r)
                    lines.append(f"{'✅' if ok else '❌'} {label}")
                except Exception as e:
                    lines.append(f"❌ {label}: {str(e)[:50]}")

            await chk("현재가/등락률/거래량",  kis_stock_price("005930", token))
            await chk("PER/PBR/EPS",          kis_stock_info("005930", token))
            await chk("외국인+기관 수급",       kis_investor_trend("005930", token))
            await chk("신용잔고",               kis_credit_balance("005930", token))
            await chk("공매도",                kis_short_selling("005930", token))
            await chk("거래량 상위",            kis_volume_rank_api(token))
            await chk("외국인순매수 상위",       kis_foreigner_trend(token))
            await chk("업종별 시세",            kis_sector_price(token))
        except Exception as e:
            lines.append(f"❌ 토큰 발급 실패: {e}")
        try:
            await application.bot.send_message(
                chat_id=CHAT_ID, text="\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            print(f"KIS 테스트 결과 전송 실패: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP over SSE (순수 aiohttp)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_mcp_sessions: dict = {}   # session_id → asyncio.Queue

MCP_TOOLS = [
    {"name": "scan_market",    "description": "거래량 상위 종목 스캔",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_portfolio",  "description": "워치리스트 전 종목 현재가·등락률",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_stock_detail","description": "개별 종목 상세: 현재가·PER·PBR·수급",
     "inputSchema": {"type": "object",
                     "properties": {"ticker": {"type": "string", "description": "종목코드 (예: 005930)"}},
                     "required": ["ticker"]}},
    {"name": "get_foreign_rank","description": "외국인 순매수 상위 종목",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_dart",       "description": "워치리스트 최근 3일 DART 공시",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_macro",      "description": "KOSPI·KOSDAQ 지수 + USD/KRW 환율",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_sector_flow","description": "WI26 주요 업종별 외국인+기관 순매수금액 상위/하위 3개",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "add_watch",      "description": "한국 워치리스트에 종목 추가",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드 (예: 005930)"},
                         "name":   {"type": "string", "description": "종목명 (예: 삼성전자)"},
                     },
                     "required": ["ticker", "name"]}},
    {"name": "remove_watch",   "description": "한국 워치리스트에서 종목 제거",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드 (예: 005930)"},
                     },
                     "required": ["ticker"]}},
    {"name": "get_alerts",     "description": "손절가 목록 + 현재가 대비 손절까지 남은 %",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "set_alert",      "description": "손절가/목표가 등록 및 수정",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker":       {"type": "string", "description": "종목코드 (예: 034020)"},
                         "name":         {"type": "string", "description": "종목명"},
                         "stop_price":   {"type": "number", "description": "손절가"},
                         "target_price": {"type": "number", "description": "목표가 (선택)"},
                     },
                     "required": ["ticker", "name", "stop_price"]}},
    {"name": "get_us_stock_detail", "description": "미국 개별 종목 상세: 현재가·등락률·PER·PBR·시총·52주·거래량",
     "inputSchema": {"type": "object",
                     "properties": {"ticker": {"type": "string", "description": "미국 티커 (예: TSLA, AAPL)"}},
                     "required": ["ticker"]}},
    {"name": "set_watch_alert",    "description": "미보유 종목 매수 희망가 감시 등록 (가격 도달 시 텔레그램 알림)",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker":    {"type": "string", "description": "종목코드 (한국: 012450, 미국: AAPL)"},
                         "name":      {"type": "string", "description": "종목명"},
                         "buy_price": {"type": "number", "description": "매수 희망가 (이 가격 이하일 때 알림)"},
                         "memo":      {"type": "string", "description": "매수 근거 메모 (선택)"},
                     },
                     "required": ["ticker", "name", "buy_price"]}},
    {"name": "get_watch_alerts",   "description": "매수 희망가 감시 목록 조회",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "remove_watch_alert", "description": "매수 희망가 감시 제거",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드"},
                     },
                     "required": ["ticker"]}},
]


async def _execute_tool(name: str, arguments: dict) -> dict | list:
    """툴 실행 → 결과 반환 (에러 시 {"error": ...})"""
    arguments = arguments or {}
    print(f"툴 호출: {name} {arguments}")
    try:
        token = await get_kis_token()
        if not token:
            raise RuntimeError("KIS 토큰 발급 실패")

        if name == "scan_market":
            rows = await kis_volume_rank_api(token)
            result = [{"ticker": r.get("mksc_shrn_iscd"), "name": r.get("hts_kor_isnm"),
                       "vol": r.get("acml_vol"), "chg": r.get("prdy_ctrt")} for r in rows[:15]]

        elif name == "get_portfolio":
            portfolio = load_json(PORTFOLIO_FILE, {})
            kr_stocks = {k: v for k, v in portfolio.items() if k != "us_stocks"}
            us_stocks = portfolio.get("us_stocks", {})
            if not kr_stocks and not us_stocks:
                result = {"message": "포트폴리오가 비어있습니다. /setportfolio 또는 /setusportfolio 로 등록하세요."}
            else:
                kr_holdings, us_holdings = [], []
                kr_eval = kr_cost = us_eval = us_cost = 0

                for ticker, info in kr_stocks.items():
                    qty = info.get("qty", 0)
                    avg = info.get("avg_price", 0)
                    d = await kis_stock_price(ticker, token)
                    cur = int(d.get("stck_prpr", 0) or 0)
                    eval_amt = cur * qty
                    cost_amt = int(avg) * qty
                    pnl = eval_amt - cost_amt
                    pnl_pct = round((cur - avg) / avg * 100, 2) if avg else 0
                    kr_eval += eval_amt
                    kr_cost += cost_amt
                    kr_holdings.append({
                        "ticker": ticker, "name": info.get("name", ticker),
                        "qty": qty, "avg_price": avg, "cur_price": cur,
                        "eval_amt": eval_amt, "pnl": pnl, "pnl_pct": pnl_pct,
                        "chg_today": d.get("prdy_ctrt"),
                    })

                for symbol, info in us_stocks.items():
                    qty = info.get("qty", 0)
                    avg = info.get("avg_price", 0)
                    d = await kis_us_stock_price(symbol, token)
                    cur = float(d.get("last", 0) or d.get("stck_prpr", 0) or 0)
                    eval_amt = round(cur * qty, 2)
                    cost_amt = round(avg * qty, 2)
                    pnl = round(eval_amt - cost_amt, 2)
                    pnl_pct = round((cur - avg) / avg * 100, 2) if avg else 0
                    us_eval += eval_amt
                    us_cost += cost_amt
                    us_holdings.append({
                        "ticker": symbol, "name": info.get("name", symbol),
                        "qty": qty, "avg_price": avg, "cur_price": cur,
                        "eval_amt": eval_amt, "pnl": pnl, "pnl_pct": pnl_pct,
                        "chg_today": d.get("rate"),
                    })

                result = {
                    "kr": {
                        "holdings": kr_holdings,
                        "summary": {
                            "total_eval": kr_eval, "total_cost": kr_cost,
                            "total_pnl": kr_eval - kr_cost,
                            "total_pnl_pct": round((kr_eval - kr_cost) / kr_cost * 100, 2) if kr_cost else 0,
                        },
                    },
                    "us": {
                        "holdings": us_holdings,
                        "summary": {
                            "total_eval": round(us_eval, 2), "total_cost": round(us_cost, 2),
                            "total_pnl": round(us_eval - us_cost, 2),
                            "total_pnl_pct": round((us_eval - us_cost) / us_cost * 100, 2) if us_cost else 0,
                        },
                    },
                }

        elif name == "get_stock_detail":
            ticker = arguments.get("ticker", "005930")
            price = await kis_stock_price(ticker, token)
            info  = await kis_stock_info(ticker, token)
            inv   = await kis_investor_trend(ticker, token)
            result = {
                "ticker": ticker,
                "price": price.get("stck_prpr"), "chg": price.get("prdy_ctrt"),
                "vol": price.get("acml_vol"),
                "w52h": price.get("w52_hgpr"), "w52l": price.get("w52_lwpr"),
                "per": info.get("per"), "pbr": info.get("pbr"), "eps": info.get("eps"),
                "investor": inv[:3] if isinstance(inv, list) else inv,
            }

        elif name == "get_foreign_rank":
            try:
                rows = await kis_foreigner_trend(token)
                if not rows:
                    result = {"error": "데이터 없음", "items": []}
                else:
                    result = [
                        {
                            "ticker": r.get("mksc_shrn_iscd", ""),
                            "name": r.get("hts_kor_isnm", ""),
                            "net_buy": r.get("frgn_ntby_qty", "0"),
                        }
                        for r in rows[:15]
                    ]
            except Exception as e:
                result = {"error": str(e), "items": []}

        elif name == "get_dart":
            disclosures = await search_dart_disclosures(days_back=3)
            wl = load_watchlist()
            important = filter_important_disclosures(disclosures, list(wl.values()))
            result = [{"corp": d.get("corp_name"), "title": d.get("report_nm"),
                       "date": d.get("rcept_dt")} for d in important[:10]]

        elif name == "get_macro":
            kospi  = await get_kis_index(token, "0001")
            kosdaq = await get_kis_index(token, "1001")
            usd    = await get_yahoo_quote("USDKRW=X")
            result = {
                "kospi":  {"index": kospi.get("bstp_nmix_prpr"),  "chg": kospi.get("bstp_nmix_prdy_ctrt")},
                "kosdaq": {"index": kosdaq.get("bstp_nmix_prpr"), "chg": kosdaq.get("bstp_nmix_prdy_ctrt")},
                "usd_krw": {"price": usd.get("price") if usd else None,
                            "chg_pct": usd.get("change_pct") if usd else None},
            }

        elif name == "get_sector_flow":
            today = datetime.now().strftime("%Y%m%d")
            sectors = []
            for code, label in WI26_SECTORS:
                frgn, orgn = await _fetch_sector_flow(token, code)
                sectors.append({
                    "sector": label, "code": code,
                    "frgn": frgn, "orgn": orgn,
                    "total": frgn + orgn,
                })

            has_data = any(s["total"] != 0 for s in sectors)
            note = None

            if not has_data:
                # Fallback: 외국인 순매수 상위 기반 업종 근사치 (수량 기준)
                frgn_rows = await kis_foreigner_trend(token)
                sector_frgn = {label: 0 for _, label in WI26_SECTORS}
                for r in frgn_rows:
                    sect = _TICKER_SECTOR.get(r.get("mksc_shrn_iscd", ""))
                    if sect:
                        sector_frgn[sect] += int(r.get("frgn_ntby_qty", 0) or 0)
                sectors = [
                    {"sector": label, "code": code,
                     "frgn": sector_frgn.get(label, 0), "orgn": 0,
                     "total": sector_frgn.get(label, 0)}
                    for code, label in WI26_SECTORS
                ]
                note = "업종별 투자자 API 미지원 — 외국인 순매수 상위 기반 근사치(수량)"

            sorted_s = sorted(sectors, key=lambda x: x["total"], reverse=True)
            result = {
                "date": today,
                "top_inflow":  [{"sector": s["sector"], "frgn": s["frgn"], "orgn": s["orgn"]}
                                 for s in sorted_s[:3]],
                "top_outflow": [{"sector": s["sector"], "frgn": s["frgn"], "orgn": s["orgn"]}
                                 for s in sorted_s[-3:][::-1]],
                "all": [{"sector": s["sector"], "frgn": s["frgn"], "orgn": s["orgn"]}
                        for s in sorted_s],
            }
            if note:
                result["note"] = note

        elif name == "get_alerts":
            stops = load_stoploss()
            kr_stops = {k: v for k, v in stops.items() if k != "us_stocks"}
            us_stops = stops.get("us_stocks", {})
            if not kr_stops and not us_stops:
                result = {"alerts": [], "message": "손절선 없음. /setstop 으로 등록하세요."}
            else:
                alerts = []
                for ticker, info in kr_stops.items():
                    stop   = info.get("stop_price", 0)
                    entry  = info.get("entry_price", 0)
                    target = info.get("target_price", 0)
                    cur = 0
                    try:
                        d = await kis_stock_price(ticker, token)
                        cur = int(d.get("stck_prpr", 0) or 0)
                    except Exception:
                        pass
                    gap_pct = round((stop - cur) / cur * 100, 2) if cur else None
                    item = {
                        "ticker": ticker, "name": info.get("name", ticker),
                        "market": "KR", "stop": stop, "entry": entry,
                        "cur": cur, "gap_pct": gap_pct,
                    }
                    if target:
                        item["target"] = target
                        item["target_pct"] = round((target - cur) / cur * 100, 2) if cur else None
                    alerts.append(item)
                for sym, info in us_stops.items():
                    stop   = info.get("stop_price", 0)
                    target = info.get("target_price", 0)
                    cur = 0.0
                    try:
                        d = await get_yahoo_quote(sym)
                        cur = float(d.get("price", 0) or 0) if d else 0.0
                    except Exception:
                        pass
                    gap_pct = round((stop - cur) / cur * 100, 2) if cur else None
                    item = {
                        "ticker": sym, "name": info.get("name", sym),
                        "market": "US", "stop": stop,
                        "cur": cur, "gap_pct": gap_pct,
                    }
                    if target:
                        item["target"] = target
                        item["target_pct"] = round((target - cur) / cur * 100, 2) if cur else None
                    alerts.append(item)
                result = {"alerts": alerts}

        elif name == "set_alert":
            ticker       = arguments.get("ticker", "").strip().upper()
            aname        = arguments.get("name", ticker).strip()
            stop_price   = float(arguments.get("stop_price", 0))
            target_price = float(arguments.get("target_price", 0) or 0)
            if not ticker or stop_price <= 0:
                result = {"error": "ticker와 stop_price는 필수입니다"}
            else:
                stops = load_stoploss()
                if _is_us_ticker(ticker):
                    us = stops.get("us_stocks", {})
                    us[ticker] = {"name": aname, "stop_price": stop_price, "target_price": target_price}
                    stops["us_stocks"] = us
                    save_json(STOPLOSS_FILE, stops)
                    result = {
                        "ok": True,
                        "message": f"{aname}({ticker}) 손절가 ${stop_price:,.2f} 저장됨"
                                   + (f", 목표가 ${target_price:,.2f}" if target_price else ""),
                    }
                else:
                    stops[ticker] = {
                        "name":         aname,
                        "stop_price":   stop_price,
                        "entry_price":  stops.get(ticker, {}).get("entry_price", 0),
                        "target_price": target_price,
                    }
                    save_json(STOPLOSS_FILE, stops)
                    result = {
                        "ok": True,
                        "message": f"{aname}({ticker}) 손절가 {stop_price:,.0f}원 저장됨"
                                   + (f", 목표가 {target_price:,.0f}원" if target_price else ""),
                    }

        elif name == "add_watch":
            ticker = arguments.get("ticker", "").strip()
            wname  = arguments.get("name", "").strip()
            if not ticker or not wname:
                result = {"error": "ticker와 name은 필수입니다"}
            else:
                wl = load_watchlist()
                wl[ticker] = wname
                save_json(WATCHLIST_FILE, wl)
                result = {"ok": True, "message": f"{wname}({ticker}) 워치리스트 추가됨", "total": len(wl)}

        elif name == "remove_watch":
            ticker = arguments.get("ticker", "").strip()
            if not ticker:
                result = {"error": "ticker는 필수입니다"}
            else:
                wl = load_watchlist()
                if ticker in wl:
                    removed = wl.pop(ticker)
                    save_json(WATCHLIST_FILE, wl)
                    result = {"ok": True, "message": f"{removed}({ticker}) 워치리스트 제거됨", "total": len(wl)}
                else:
                    result = {"error": f"{ticker} 워치리스트에 없음"}

        elif name == "get_us_stock_detail":
            symbol = arguments.get("ticker", "TSLA").strip().upper()
            excd = _guess_excd(symbol)
            price_d = await kis_us_stock_price(symbol, token, excd)
            detail_d = await kis_us_stock_detail(symbol, token, excd)
            cur = float(price_d.get("last", 0) or 0)
            base = float(price_d.get("base", 0) or 0)
            result = {
                "ticker": symbol,
                "price": cur,
                "chg_pct": float(price_d.get("rate", 0) or 0),
                "volume": int(price_d.get("tvol", 0) or 0),
                "open": float(detail_d.get("open", 0) or 0),
                "high": float(detail_d.get("high", 0) or 0),
                "low": float(detail_d.get("low", 0) or 0),
                "prev_close": base,
                "w52h": float(detail_d.get("h52p", 0) or 0),
                "w52l": float(detail_d.get("l52p", 0) or 0),
                "per": float(detail_d.get("perx", 0) or 0) or None,
                "pbr": float(detail_d.get("pbrx", 0) or 0) or None,
                "eps": float(detail_d.get("epsx", 0) or 0) or None,
                "market_cap": detail_d.get("tomv", ""),
                "sector": detail_d.get("e_icod", ""),
            }

        elif name == "set_watch_alert":
            ticker    = arguments.get("ticker", "").strip().upper()
            wname     = arguments.get("name", "").strip()
            buy_price = float(arguments.get("buy_price", 0))
            memo      = arguments.get("memo", "").strip()
            if not ticker or not wname or buy_price <= 0:
                result = {"error": "ticker, name, buy_price(>0) 필수"}
            else:
                wa = load_watchalert()
                wa[ticker] = {
                    "name": wname,
                    "buy_price": buy_price,
                    "memo": memo,
                    "created": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                }
                save_json(WATCHALERT_FILE, wa)
                if _is_us_ticker(ticker):
                    msg = f"{wname}({ticker}) 매수감시 ${buy_price:,.2f} 등록됨"
                else:
                    msg = f"{wname}({ticker}) 매수감시 {buy_price:,.0f}원 등록됨"
                if memo:
                    msg += f" | 메모: {memo}"
                result = {"ok": True, "message": msg, "total": len(wa)}

        elif name == "get_watch_alerts":
            wa = load_watchalert()
            if not wa:
                result = {"alerts": [], "message": "감시 종목 없음. set_watch_alert로 등록하세요."}
            else:
                alerts = []
                for ticker, info in wa.items():
                    buy_price = info.get("buy_price", 0)
                    cur = 0.0
                    try:
                        if _is_us_ticker(ticker):
                            d = await kis_us_stock_price(ticker, token)
                            cur = float(d.get("last", 0) or 0)
                        else:
                            d = await kis_stock_price(ticker, token)
                            cur = int(d.get("stck_prpr", 0) or 0)
                    except Exception:
                        pass
                    gap_pct = round((cur - buy_price) / buy_price * 100, 2) if buy_price else None
                    alerts.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "buy_price": buy_price,
                        "cur_price": cur,
                        "gap_pct": gap_pct,
                        "triggered": cur > 0 and cur <= buy_price,
                        "memo": info.get("memo", ""),
                        "created": info.get("created", ""),
                    })
                result = {"alerts": alerts}

        elif name == "remove_watch_alert":
            ticker = arguments.get("ticker", "").strip().upper()
            if not ticker:
                result = {"error": "ticker 필수"}
            else:
                wa = load_watchalert()
                if ticker in wa:
                    removed = wa.pop(ticker)
                    save_json(WATCHALERT_FILE, wa)
                    result = {"ok": True, "message": f"{removed['name']}({ticker}) 매수감시 제거됨", "total": len(wa)}
                else:
                    result = {"error": f"{ticker} 감시 목록에 없음"}

        else:
            result = {"error": f"unknown tool: {name}"}

    except Exception as e:
        result = {"error": str(e)}
        print(f"에러: {name} → {e}")

    print(f"툴 결과: {name} → {json.dumps(result, ensure_ascii=False)[:200]}")
    return result


async def _handle_jsonrpc(body: dict) -> dict | None:
    """JSON-RPC 요청 처리 → 응답 dict (notification이면 None)"""
    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "kis-stock-bot", "version": "1.0.0"},
        }}

    if method.startswith("notifications/"):
        return None  # notification은 응답 없음

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS, "nextCursor": None}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        result = await _execute_tool(tool_name, tool_args)
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
        }}

    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


async def mcp_sse_handler(request: web.Request) -> web.StreamResponse:
    """GET /mcp  → SSE 스트림 수립, endpoint 이벤트 전송"""
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _mcp_sessions[session_id] = queue
    print(f"SSE 연결됨: {session_id}")

    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
    })
    await resp.prepare(request)

    # 클라이언트에 메시지 POST URL 전달
    await resp.write(
        ("event: endpoint\n"
         f"data: /mcp/messages?sessionId={session_id}\n\n").encode()
    )

    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30)
                if msg is None:
                    break
                data = json.dumps(msg, ensure_ascii=False)
                await resp.write(
                    ("event: message\n" + f"data: {data}\n\n").encode()
                )
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception as e:
        print(f"에러: SSE [{session_id}] {e}")
    finally:
        _mcp_sessions.pop(session_id, None)
        print(f"SSE 종료: {session_id}")

    return resp


async def mcp_messages_handler(request: web.Request) -> web.Response:
    """POST /mcp/messages?sessionId=UUID  → JSON-RPC 수신 후 SSE로 응답"""
    session_id = request.rel_url.query.get("sessionId")
    queue = _mcp_sessions.get(session_id)
    if not queue:
        return web.json_response({"error": "session not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    response = await _handle_jsonrpc(body)
    if response is not None:
        await queue.put(response)

    return web.Response(status=202, text="Accepted")

def main():
    print("봇 시작 중...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # 명령어 등록
    commands = [
        ("start", start), ("analyze", analyze), ("scan", scan), ("macro", macro),
        ("news", news_cmd), ("dart", dart_cmd), ("summary", manual_summary),
        ("watchlist", watchlist_cmd), ("watch", watch), ("unwatch", unwatch),
        ("uslist", uslist_cmd), ("addus", addus), ("remus", remus),
        ("setstop", setstop), ("delstop", delstop), ("stops", stops_cmd),
        ("setportfolio", setportfolio_cmd),
        ("setusportfolio", setusportfolio_cmd),
        ("help", help_cmd),
    ]
    for cmd, fn in commands:
        app.add_handler(CommandHandler(cmd, fn))

    # 자동 알림 스케줄
    jq = app.job_queue
    jq.run_repeating(check_stoploss, interval=600, first=60, name="stoploss")
    jq.run_repeating(check_anomaly, interval=1800, first=120, name="anomaly")
    jq.run_repeating(check_fx_alert, interval=3600, first=300, name="fx")
    jq.run_repeating(check_dart_disclosure, interval=1800, first=180, name="dart")
    jq.run_daily(daily_kr_summary, time=datetime.strptime("06:40", "%H:%M").time(), days=(0,1,2,3,4), name="kr_summary")
    jq.run_daily(daily_us_summary, time=datetime.strptime("22:00", "%H:%M").time(), name="us_summary")
    jq.run_daily(weekly_review, time=datetime.strptime("01:00", "%H:%M").time(), days=(6,), name="weekly")

    port = int(os.environ.get("PORT", 8080))
    print(f"봇 실행! MCP SSE 서버 포트: {port}")
    asyncio.run(_run_all(app, port))


async def _run_all(app, port):
    # MCP aiohttp 서버 시작
    mcp_app = web.Application()
    mcp_app.router.add_get("/mcp", mcp_sse_handler)
    mcp_app.router.add_post("/mcp/messages", mcp_messages_handler)
    mcp_app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))
    runner = web.AppRunner(mcp_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"MCP SSE 서버 시작: 0.0.0.0:{port}/mcp")

    # 텔레그램 봇 비동기 실행
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()  # 무한 대기


if __name__ == "__main__":
    main()

# ci trigger 2
