import os
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

# 환경변수
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
KIS_APP_KEY = os.environ.get("KIS_APP_KEY")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET")

# 실전투자 API URL
KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"

# 토큰 캐시 (매번 재발급 방지)
_token_cache = {"token": None, "expires": None}


async def get_kis_token():
    """KIS API 토큰 발급 (캐시 사용)"""
    now = datetime.now()
    if _token_cache["token"] and _token_cache["expires"] and _token_cache["expires"] > now:
        return _token_cache["token"]

    url = f"{KIS_BASE_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            data = await resp.json()
            token = data.get("access_token")
            if token:
                from datetime import timedelta
                _token_cache["token"] = token
                _token_cache["expires"] = now + timedelta(hours=20)
            return token


async def get_stock_price(ticker: str, token: str):
    """주식 현재가 조회"""
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": "FHKST01010100"
    }
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": ticker
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            data = await resp.json()
            return data.get("output", {})


async def get_volume_rank(token: str):
    """거래량 급등 종목 조회"""
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": "FHPST01710000"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20101",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "0",
        "FID_INPUT_PRICE_2": "0",
        "FID_VOL_CNT": "0",
        "FID_INPUT_DATE_1": ""
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            data = await resp.json()
            return data.get("output", [])


# /start 명령어
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *부자가될거야 봇 시작!*\n\n"
        "📌 *명령어 목록*\n"
        "/portfolio - 보유 종목 현황 (실시간)\n"
        "/analyze 종목코드 - 종목 심층 분석\n"
        "/scan - 거래량 급등 종목 스캔\n"
        "/help - 도움말"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# /portfolio 명령어
async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 포트폴리오 조회 중...")

    # 보유 종목 (티커: 종목명, 수량, 평균단가)
    # ※ 평균단가를 실제 매수가로 업데이트하세요
    holdings = {
        "009540": ("HD한국조선해양", 50, 0),
        "298040": ("효성중공업", 2, 0),
        "010120": ("LS ELECTRIC", 5, 0),
        "267260": ("HD현대일렉트릭", 3, 0),
        "034020": ("두산에너빌리티", 19, 0),
    }

    try:
        token = await get_kis_token()
        if not token:
            await update.message.reply_text("❌ KIS API 토큰 발급 실패\n\nAPI 키를 확인해주세요.")
            return

        msg = "📊 *보유 종목 현황*\n\n"
        total_value = 0

        for ticker, (name, qty, avg) in holdings.items():
            try:
                data = await get_stock_price(ticker, token)
                await asyncio.sleep(0.3)

                price = int(data.get("stck_prpr", 0))          # 현재가
                change = data.get("prdy_ctrt", "0")             # 전일 대비 등락률
                volume = int(data.get("acml_vol", 0))           # 누적 거래량
                change_sign = "🔴" if float(change) < 0 else "🟢" if float(change) > 0 else "⚪"

                value = price * qty
                total_value += value

                # 평균단가가 0이면 손익 계산 생략
                if avg > 0:
                    pnl = (price - avg) * qty
                    pnl_pct = ((price - avg) / avg) * 100
                    pnl_text = f"손익: {pnl:+,}원 ({pnl_pct:+.1f}%)"
                else:
                    pnl_text = "평균단가 미입력"

                msg += (
                    f"{change_sign} *{name}*\n"
                    f"  현재가: {price:,}원 ({change}%)\n"
                    f"  {qty}주 | 평가: {value:,}원\n"
                    f"  {pnl_text}\n\n"
                )
            except Exception as e:
                msg += f"❌ *{name}* - 조회 실패\n\n"

        msg += f"💰 *총 평가금액: {total_value:,}원*\n"
        msg += f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ 오류 발생: {str(e)}")


# /analyze 명령어
async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "사용법: /analyze 종목코드\n"
            "예) /analyze 005930 (삼성전자)\n"
            "예) /analyze 034020 (두산에너빌리티)"
        )
        return

    ticker = context.args[0]
    await update.message.reply_text(f"⏳ {ticker} 분석 중...")

    try:
        token = await get_kis_token()
        if not token:
            await update.message.reply_text("❌ KIS API 토큰 발급 실패")
            return

        data = await get_stock_price(ticker, token)

        if not data or not data.get("stck_prpr"):
            await update.message.reply_text(f"❌ {ticker} 종목을 찾을 수 없습니다.\n종목코드를 확인해주세요.")
            return

        # 데이터 파싱
        name = data.get("stck_shrn_iscd", ticker)
        price = int(data.get("stck_prpr", 0))              # 현재가
        change = data.get("prdy_ctrt", "0")                  # 등락률
        change_amt = int(data.get("prdy_vrss", 0))           # 전일대비
        volume = int(data.get("acml_vol", 0))                # 누적거래량
        avg_vol = int(data.get("avrg_vol", 0))               # 평균거래량 (없을 수 있음)
        high = int(data.get("stck_hgpr", 0))                 # 고가
        low = int(data.get("stck_lwpr", 0))                  # 저가
        open_p = int(data.get("stck_oprc", 0))               # 시가
        high_52 = int(data.get("stck_dryc_hgpr", 0))         # 52주 최고
        low_52 = int(data.get("stck_dryc_lwpr", 0))          # 52주 최저
        market_cap = int(data.get("hts_avls", 0))             # 시가총액(억)
        per = data.get("per", "-")                             # PER
        pbr = data.get("pbr", "-")                             # PBR
        vol_rate = data.get("prdy_vrss_vol_rate", "0")        # 전일대비 거래량 비율

        change_sign = "📉" if float(change) < 0 else "📈" if float(change) > 0 else "➡️"

        # 거래량 분석
        vol_analysis = ""
        try:
            vol_r = float(vol_rate)
            if vol_r >= 200:
                vol_analysis = "🔥 거래량 급증!"
            elif vol_r >= 150:
                vol_analysis = "⚡ 거래량 증가"
            elif vol_r <= 50:
                vol_analysis = "😴 거래량 감소"
        except:
            pass

        msg = (
            f"{change_sign} *{ticker} 분석*\n\n"
            f"💰 *현재가: {price:,}원* ({change_amt:+,} / {change}%)\n\n"
            f"📊 *가격 정보*\n"
            f"  시가: {open_p:,} | 고가: {high:,} | 저가: {low:,}\n"
            f"  52주 고: {high_52:,} | 52주 저: {low_52:,}\n\n"
            f"📦 *거래량*\n"
            f"  오늘: {volume:,}주\n"
            f"  전일대비: {vol_rate}% {vol_analysis}\n\n"
            f"🏢 *기업 정보*\n"
            f"  시총: {market_cap:,}억원\n"
            f"  PER: {per} | PBR: {pbr}\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ 분석 오류: {str(e)}")


# /scan 명령어 (거래량 급등 종목)
async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 거래량 급등 종목 스캔 중...")

    try:
        token = await get_kis_token()
        if not token:
            await update.message.reply_text("❌ KIS API 토큰 발급 실패")
            return

        results = await get_volume_rank(token)

        if not results:
            await update.message.reply_text("📭 거래량 급등 종목이 없습니다.")
            return

        msg = "🔍 *거래량 급등 TOP 10*\n\n"
        for i, item in enumerate(results[:10], 1):
            name = item.get("hts_kor_isnm", "?")
            ticker = item.get("mksc_shrn_iscd", "?")
            price = int(item.get("stck_prpr", 0))
            change = item.get("prdy_ctrt", "0")
            volume = int(item.get("acml_vol", 0))
            vol_rate = item.get("prdy_vol_vrss_acml_vol_rate", "0")

            change_sign = "🔴" if float(change) < 0 else "🟢" if float(change) > 0 else "⚪"

            msg += (
                f"{i}. {change_sign} *{name}* ({ticker})\n"
                f"   {price:,}원 ({change}%) | 거래량 {vol_rate}%↑\n\n"
            )

        msg += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        msg += "분석: /analyze 종목코드"
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ 스캔 오류: {str(e)}")


# /help 명령어
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📖 *도움말*\n\n"
        "📌 *명령어*\n"
        "/portfolio - 보유 종목 실시간 현황\n"
        "/analyze [종목코드] - 종목 심층 분석\n"
        "  예) /analyze 005930 (삼성전자)\n"
        "/scan - 거래량 급등 종목 TOP 10\n"
        "/help - 이 메시지\n\n"
        "🔔 *자동 알림 (예정)*\n"
        "- 매일 장 마감 후 외국인 순매수 요약\n"
        "- 이상 신호 감지 시 즉시 알림\n"
        "- 공시 알림"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# 봇 시작 후 알림 (실패해도 봇은 계속 작동)
async def post_init(application: Application):
    try:
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text="✅ *부자가될거야 봇 시작됨!*\n\n/help 로 명령어 확인하세요",
            parse_mode="Markdown"
        )
        print("시작 알림 전송 성공!")
    except Exception as e:
        print(f"시작 알림 전송 실패 (봇은 정상 작동 중): {e}")


def main():
    print("봇 시작 중...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("portfolio", portfolio))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("help", help_cmd))

    print("봇 실행 중!")
    app.run_polling()


if __name__ == "__main__":
    main()
