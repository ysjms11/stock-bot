import os
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 환경변수 & 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
KIS_APP_KEY = os.environ.get("KIS_APP_KEY")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET")

KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
KST = timezone(timedelta(hours=9))

WATCHLIST_FILE = "/app/watchlist.json"
STOPLOSS_FILE = "/app/stoploss.json"

_token_cache = {"token": None, "expires": None}

# 미국 보유 종목 (텔레그램에서 수정 가능하도록 나중에 파일로 전환 가능)
US_WATCHLIST = {
    "TSLA": ("테슬라", 12),
    "CRSP": ("크리스퍼", 70),
    "AMD": ("AMD", 17),
    "LITE": ("루멘텀", 4),
}


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
        "009540": "HD한국조선해양",
        "298040": "효성중공업",
        "010120": "LS ELECTRIC",
        "267260": "HD현대일렉트릭",
        "034020": "두산에너빌리티",
    })


def load_stoploss():
    return load_json(STOPLOSS_FILE, {})


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
        "FID_INPUT_PRICE_1": "0", "FID_INPUT_PRICE_2": "0", "FID_VOL_CNT": "0", "FID_INPUT_DATE_1": ""
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            return (await resp.json()).get("output", [])


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
                fn = 0
                ins = 0
                fr = 0.0
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
        fx = await get_yahoo_quote("KRW=X")
        fx_rate = fx.get("price", 1300)

        msg = f"🇺🇸 *미국 장 마감 요약* ({now.strftime('%m/%d %H:%M')})\n💱 환율: {fx_rate:,.0f}원\n\n"

        for sym, (name, qty) in US_WATCHLIST.items():
            try:
                d = await get_yahoo_quote(sym)
                await asyncio.sleep(0.3)
                p = d["price"]
                c = d["change_pct"]
                cs = "🔴" if c < 0 else "🟢" if c > 0 else "⚪"
                val_krw = p * qty * fx_rate
                msg += f"{cs} *{name}* ${p:,.2f} ({c:+.1f}%) | {qty}주 ₩{val_krw:,.0f}\n\n"
            except Exception:
                msg += f"❌ *{name}* 조회 실패\n\n"

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
            impact = "원화약세 → 미국주식 원화이익↑, 수입물가↑" if c > 0 else "원화강세 → 미국주식 원화이익↓"
            msg = f"💱 *환율 {direction}*\n\nUSD/KRW: {rate:,.1f}원 ({c:+.1f}%)\n\n📌 {impact}"
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
                fr = 0.0
                fn = 0
                if inv and len(inv) > 0:
                    t = inv[0] if isinstance(inv, list) else inv
                    fn = int(t.get("frgn_ntby_qty", 0))
                    if mcap > 0 and price > 0:
                        fr = (fn * price) / (mcap * 1e8) * 100

                # 복합 신호만 (노이즈 차단)
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
            msg = f"🔔 *복합 신호 감지* ({now.strftime('%H:%M')})\n\n"
            for a in alerts:
                msg += f"{a}\n\n"
            msg += "💡 Claude에서 진입 여부 분석하세요"
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"이상 신호 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 6: 주간 리뷰 리마인더 (일 10:00)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def weekly_review(context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📋 *주간 리뷰 시간입니다*\n\n"
        "Claude에서 점검하세요:\n\n"
        "1️⃣ 보유 종목 Thesis 유효한가?\n"
        "2️⃣ 손절/익절 대상 있는가?\n"
        "3️⃣ 섹터 모멘텀 살아있는가?\n"
        "4️⃣ 다음 주 매크로 이벤트?\n"
        "5️⃣ 현금 비중 적절한가?\n\n"
        "💡 포트폴리오 스크린샷 + \"리뷰해줘\" 보내세요"
    )
    try:
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"주간 리뷰 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 텔레그램 명령어
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *부자가될거야 봇 v5*\n\n"
        "📌 *조회*\n"
        "/analyze 코드 - 종목분석(수급포함)\n"
        "/scan - 거래량 급등 TOP10\n"
        "/macro - VIX/환율/유가/금리\n"
        "/summary - 장마감 요약(수동)\n\n"
        "👀 *워치리스트*\n"
        "/watchlist · /watch 코드 이름 · /unwatch 코드\n\n"
        "🛑 *손절관리*\n"
        "/setstop 코드 이름 손절가 진입가\n"
        "/delstop 코드 · /stops\n\n"
        "🔔 *자동알림* — 설정 불필요, 자동 작동!"
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
        symbols = {"^VIX": "VIX", "KRW=X": "USD/KRW", "CL=F": "WTI유가", "^TNX": "10년금리", "^GSPC": "S&P500", "^KS11": "KOSPI"}
        msg = "🌐 *매크로 현황*\n\n"
        vix_val = 0
        for sym, name in symbols.items():
            d = await get_yahoo_quote(sym)
            await asyncio.sleep(0.3)
            p, c = d["price"], d["change_pct"]
            cs = "🔴" if c < 0 else "🟢" if c > 0 else "⚪"
            if "환율" in name or "KRW" in name: ps = f"{p:,.1f}원"
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

        msg += "\n━━━━━━━━━━━━━━━━\n"
        if vix_val > 25: msg += "🔴 *레짐: 위기* — 신규매수 금지"
        elif vix_val > 20: msg += "🟠 *레짐: 경계* — 기존 포지션만 관리"
        elif vix_val > 15: msg += "🟡 *레짐: 중립* — 확신 높은 것만"
        else: msg += "🟢 *레짐: 공격* — 핵심 섹터 적극 매수"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {str(e)}")


async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wl = load_watchlist()
    if not wl:
        await update.message.reply_text("📭 비어있음. /watch 코드 이름"); return
    msg = "👀 *워치리스트*\n\n"
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
        lp = f" | 진입 {i['entry_price']:,.0f} ({((i['stop_price']-i['entry_price'])/i['entry_price']*100):.1f}%)" if i.get("entry_price", 0) > 0 else ""
        msg += f"• *{i['name']}* ({t}): {i['stop_price']:,.0f}원{lp}\n"
    msg += "\n장중 10분마다 자동 체크"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def manual_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 요약 생성 중...")
    await daily_kr_summary(context)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📖 *도움말*\n\n"
        "📌 /analyze 코드 · /scan · /macro · /summary\n"
        "👀 /watchlist · /watch · /unwatch\n"
        "🛑 /setstop · /delstop · /stops\n\n"
        "🔔 *자동 알림*\n"
        "• 🔴 손절선: 장중 10분마다\n"
        "• 🔴 복합신호: 장중 30분마다\n"
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
    try:
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text="✅ *부자가될거야 v5 시작!*\n\n🔔 손절선/복합신호/장마감/미국/환율/주간리뷰 알림 활성화\n/help",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"시작 알림 실패: {e}")


def main():
    print("봇 시작 중...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    for cmd, fn in [
        ("start", start), ("analyze", analyze), ("scan", scan), ("macro", macro),
        ("summary", manual_summary), ("watchlist", watchlist_cmd), ("watch", watch),
        ("unwatch", unwatch), ("setstop", setstop), ("delstop", delstop),
        ("stops", stops_cmd), ("help", help_cmd),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    jq = app.job_queue
    jq.run_repeating(check_stoploss, interval=600, first=60, name="stoploss")
    jq.run_repeating(check_anomaly, interval=1800, first=120, name="anomaly")
    jq.run_repeating(check_fx_alert, interval=3600, first=300, name="fx")
    jq.run_daily(daily_kr_summary, time=datetime.strptime("06:40", "%H:%M").time(), name="kr_summary")
    jq.run_daily(daily_us_summary, time=datetime.strptime("22:00", "%H:%M").time(), name="us_summary")
    jq.run_daily(weekly_review, time=datetime.strptime("01:00", "%H:%M").time(), days=(6,), name="weekly")

    print("봇 실행! 알림: 손절(10분)/복합(30분)/한국(15:40)/미국(07:00)/환율(1h)/주간(일10시)")
    app.run_polling()


if __name__ == "__main__":
    main()
