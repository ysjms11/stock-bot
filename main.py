import os
import json
import re
import asyncio
import aiohttp
from aiohttp import web
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
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

WATCHLIST_FILE = "/app/watchlist.json"
STOPLOSS_FILE = "/app/stoploss.json"
US_WATCHLIST_FILE = "/app/us_watchlist.json"
DART_SEEN_FILE = "/app/dart_seen.json"

_token_cache = {"token": None, "expires": None}

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
        return d.get("output", [])


async def kis_sector_price(token):
    today = datetime.now().strftime("%Y%m%d")
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-daily-sector-price",
            "FHKUP03500100", token,
            {"fid_cond_mrkt_div_code": "U", "fid_input_iscd": "0001",
             "fid_input_date_1": today, "fid_period_div_code": "D"})
        return d.get("output", [])


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
    if now.weekday() >= 5:
        return
    try:
        token = await get_kis_token()
        if not token:
            return
        watchlist = load_watchlist()
        if not watchlist:
            return

        msg = f"📊 *한국 장 마감 요약* ({now.strftime('%m/%d %H:%M')})\n\n"
        signals = []

        for ticker, name in watchlist.items():
            try:
                pd = await get_stock_price(ticker, token)
                await asyncio.sleep(0.4)
                price = int(pd.get("stck_prpr", 0))
                change = pd.get("prdy_ctrt", "0")
                vol_rate = pd.get("prdy_vrss_vol_rate", "0")
                mcap = int(pd.get("hts_avls", 0))

                inv = await get_investor_trend(ticker, token)
                await asyncio.sleep(0.4)
                fn, ins, fr = 0, 0, 0.0
                if inv and len(inv) > 0:
                    t = inv[0] if isinstance(inv, list) else inv
                    fn = int(t.get("frgn_ntby_qty", 0))
                    ins = int(t.get("orgn_ntby_qty", 0))
                    if mcap > 0 and price > 0:
                        fr = (fn * price) / (mcap * 1e8) * 100

                cs = "🔴" if float(change) < 0 else "🟢" if float(change) > 0 else "⚪"
                fe = "🔵" if fr > 0.03 else "🟢" if fr > 0 else "🔴" if fr < -0.03 else "⚪"

                msg += f"{cs} *{name}* {price:,}원 ({change}%)\n"
                msg += f"  {fe} 외국인 {fn:+,}주 (시총 {fr:+.3f}%) | 기관 {ins:+,}주\n\n"

                try:
                    if float(vol_rate) >= 150 and fr > 0.03:
                        signals.append(f"🚨 *{name}*: 거래량 {vol_rate}%↑ + 외국인 {fr:+.3f}%")
                except:
                    pass
            except Exception:
                msg += f"❌ *{name}* 조회 실패\n\n"

        if signals:
            msg += "━━━━━━━━━━━━━━━━\n🚨 *복합 매수 신호*\n\n"
            for s in signals:
                msg += f"{s}\n"
        msg += "\n💡 Claude에서 심층 분석하세요"
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"한국 요약 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 2: 미국 장 마감 요약
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def daily_us_summary(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() in (0, 6):
        return
    try:
        us_wl = load_us_watchlist()
        fx = await get_yahoo_quote("KRW=X")
        fx_rate = fx.get("price", 1300)

        msg = f"🇺🇸 *미국 장 마감 요약* ({now.strftime('%m/%d %H:%M')})\n💱 환율: {fx_rate:,.0f}원\n\n"

        for sym, info in us_wl.items():
            try:
                name = info["name"]
                qty = info["qty"]
                d = await get_yahoo_quote(sym)
                await asyncio.sleep(0.3)
                p, c = d["price"], d["change_pct"]
                cs = "🔴" if c < 0 else "🟢" if c > 0 else "⚪"
                val_krw = p * qty * fx_rate
                msg += f"{cs} *{name}* ${p:,.2f} ({c:+.1f}%) | {qty}주 ₩{val_krw:,.0f}\n\n"
            except Exception:
                msg += f"❌ *{info.get('name', sym)}* 조회 실패\n\n"

        sp = await get_yahoo_quote("^GSPC")
        vix = await get_yahoo_quote("^VIX")
        msg += f"━━━━━━━━━━━━━━━━\n"
        msg += f"{'🔴' if sp['change_pct'] < 0 else '🟢'} S&P500 {sp['price']:,.0f} ({sp['change_pct']:+.1f}%)\n"
        msg += f"😰 VIX {vix['price']:.1f} ({vix['change_pct']:+.1f}%)\n"

        v = vix["price"]
        if v > 25: msg += "\n🔴 *레짐: 위기* — 신규매수 금지"
        elif v > 20: msg += "\n🟠 *레짐: 경계*"
        elif v > 15: msg += "\n🟡 *레짐: 중립*"
        else: msg += "\n🟢 *레짐: 공격*"

        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"미국 요약 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 3: 손절선 도달 (10분마다)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_stoploss(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() >= 5 or now.hour < 9 or (now.hour >= 15 and now.minute > 30):
        return
    stops = load_stoploss()
    if not stops:
        return
    try:
        token = await get_kis_token()
        if not token:
            return
        alerts = []
        for ticker, info in stops.items():
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
                        f"  {'손실: ' + f'{drop:.1f}%' if ep > 0 else ''}\n"
                        f"  → *즉시 매도 검토!*"
                    )
            except Exception:
                pass
        if alerts:
            msg = "🔴🔴🔴 *손절선 도달!* 🔴🔴🔴\n\n"
            for a in alerts:
                msg += f"{a}\n\n"
            msg += "⚠️ Thesis 붕괴 시 가격 무관 즉시 매도"
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"손절 체크 오류: {e}")


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
            "사용법: /setstop 코드 이름 손절가 [진입가]\n"
            "예) /setstop 034020 두산에너빌리티 88000 98000"
        ); return
    ticker, name = context.args[0], context.args[1]
    try: stop = float(context.args[2])
    except: await update.message.reply_text("❌ 손절가는 숫자"); return
    entry = 0
    if len(context.args) >= 4:
        try: entry = float(context.args[3])
        except: pass
    stops = load_stoploss()
    stops[ticker] = {"name": name, "stop_price": stop, "entry_price": entry}
    save_json(STOPLOSS_FILE, stops)
    lp = f" (진입가 대비 {((stop - entry) / entry * 100):.1f}%)" if entry > 0 else ""
    await update.message.reply_text(f"🛑 *{name}* 손절선 {stop:,.0f}원{lp}\n장중 10분마다 체크", parse_mode="Markdown")


async def delstop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("사용법: /delstop 코드"); return
    stops = load_stoploss()
    if context.args[0] in stops:
        n = stops.pop(context.args[0])["name"]
        save_json(STOPLOSS_FILE, stops)
        await update.message.reply_text(f"🗑 *{n}* 손절선 삭제!", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ 없음")


async def stops_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stops = load_stoploss()
    if not stops:
        await update.message.reply_text("📭 손절선 없음\n/setstop 코드 이름 손절가 진입가"); return
    msg = "🛑 *손절선 목록*\n\n"
    for t, i in stops.items():
        lp = ""
        if i.get("entry_price", 0) > 0:
            lp = f" | 진입 {i['entry_price']:,.0f} ({((i['stop_price']-i['entry_price'])/i['entry_price']*100):.1f}%)"
        msg += f"• *{i['name']}* ({t}): {i['stop_price']:,.0f}원{lp}\n"
    msg += "\n장중 10분마다 자동 체크"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def manual_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 요약 생성 중...")
    await daily_kr_summary(context)


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
        "• 🎯 목표가변동: 평일 16:10\n"
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
# MCP 공식 라이브러리 서버
# ━━━━━━━━━━━━━━━━━━━━━━━━━
mcp_server = Server("stock-bot-mcp")


@mcp_server.list_tools()
async def _list_tools() -> list[Tool]:
    return [
        Tool(
            name="scan_market",
            description="거래량 상위 종목 스캔. 시장 과열/침체 신호 포함.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_portfolio",
            description="한국 워치리스트 전 종목의 현재가·등락률·수급 조회",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_stock_detail",
            description="개별 종목 상세: 현재가·등락률·PER·PBR·외국인수급",
            inputSchema={
                "type": "object",
                "properties": {"ticker": {"type": "string", "description": "종목코드 (예: 005930)"}},
                "required": ["ticker"],
            },
        ),
        Tool(
            name="get_foreign_rank",
            description="외국인 순매수 상위 종목 리스트",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_dart",
            description="워치리스트 종목의 최근 DART 중요 공시 조회",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_macro",
            description="KOSPI·KOSDAQ 지수 + 환율(USD/KRW) 매크로 현황",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@mcp_server.call_tool()
async def _call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        token = await get_kis_token()
        if not token:
            raise RuntimeError("KIS 토큰 발급 실패")

        if name == "scan_market":
            rows = await kis_volume_rank_api(token)
            result = [{"ticker": r.get("mksc_shrn_iscd"), "name": r.get("hts_kor_isnm"),
                       "vol": r.get("acml_vol"), "chg": r.get("prdy_ctrt")} for r in rows[:15]]

        elif name == "get_portfolio":
            wl = load_watchlist()
            result = []
            for ticker, sname in list(wl.items())[:10]:
                d = await kis_stock_price(ticker, token)
                result.append({"ticker": ticker, "name": sname,
                                "price": d.get("stck_prpr"), "chg": d.get("prdy_ctrt")})

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
            rows = await kis_foreigner_trend(token)
            result = [{"ticker": r.get("mksc_shrn_iscd"), "name": r.get("hts_kor_isnm"),
                       "net_buy": r.get("frgn_ntby_qty")} for r in rows[:15]]

        elif name == "get_dart":
            disclosures = await search_dart_disclosures(days_back=3)
            wl = load_watchlist()
            wl_names = list(wl.values())
            important = filter_important_disclosures(disclosures, wl_names)
            result = [{"corp": d.get("corp_name"), "title": d.get("report_nm"),
                       "date": d.get("rcept_dt")} for d in important[:10]]

        elif name == "get_macro":
            kospi  = await get_kis_index(token, "0001")
            kosdaq = await get_kis_index(token, "1001")
            usd    = await get_yahoo_quote("USDKRW=X")
            result = {
                "kospi":  {"index": kospi.get("bstp_nmix_prpr"),  "chg": kospi.get("bstp_nmix_prdy_ctrt")},
                "kosdaq": {"index": kosdaq.get("bstp_nmix_prpr"), "chg": kosdaq.get("bstp_nmix_prdy_ctrt")},
                "usd_krw": {"price": usd.get("price") if usd else None, "chg_pct": usd.get("change_pct") if usd else None},
            }

        else:
            result = {"error": f"unknown tool: {name}"}

    except Exception as e:
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


def _build_mcp_starlette() -> Starlette:
    sse = SseServerTransport("/mcp/messages")

    async def handle_sse(request: Request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await mcp_server.run(
                streams[0], streams[1],
                mcp_server.create_initialization_options(),
            )

    return Starlette(routes=[
        Route("/mcp", endpoint=handle_sse),
        Mount("/mcp/messages", app=sse.handle_post_message),
        Route("/health", endpoint=lambda r: JSONResponse({"status": "ok"})),
    ])


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
    jq.run_daily(daily_kr_summary, time=datetime.strptime("06:40", "%H:%M").time(), name="kr_summary")
    jq.run_daily(daily_us_summary, time=datetime.strptime("22:00", "%H:%M").time(), name="us_summary")
    jq.run_daily(weekly_review, time=datetime.strptime("01:00", "%H:%M").time(), days=(6,), name="weekly")

    port = int(os.environ.get("PORT", 8080))
    print(f"봇 실행! MCP SSE 서버 포트: {port}")
    asyncio.run(_run_all(app, port))


async def _run_all(app, port):
    # MCP Starlette + uvicorn 서버 시작
    starlette_app = _build_mcp_starlette()
    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=port, log_level="warning")
    uv_server = uvicorn.Server(config)
    print(f"MCP SSE 서버 시작: 0.0.0.0:{port}/mcp")

    # 텔레그램 봇 비동기 실행
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        await uv_server.serve()  # uvicorn이 이벤트루프 점유, 봇은 백그라운드로 동작


if __name__ == "__main__":
    main()

# ci trigger 2
