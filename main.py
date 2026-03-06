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

# KIS API 토큰 발급
async def get_kis_token():
    url = "https://openapivts.koreainvestment.com:29443/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            data = await resp.json()
            return data.get("access_token")

# 주식 현재가 조회
async def get_stock_price(ticker: str, token: str):
    url = "https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "content-type": "application/json",
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

# 외국인 순매수 조회
async def get_foreign_flow(ticker: str, token: str):
    url = "https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-investor"
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": "FHKST01010900"
    }
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": ticker
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            data = await resp.json()
            return data.get("output", [])

# /start 명령어
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *주식 분석 봇 시작!*\n\n"
        "사용 가능한 명령어:\n"
        "/portfolio - 보유 종목 현황\n"
        "/analyze 종목코드 - 종목 분석\n"
        "/help - 도움말"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# /portfolio 명령어
async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 포트폴리오 조회 중...")

    # 보유 종목 (티커: 종목명, 수량, 평균단가)
    holdings = {
        "009540": ("HD한국조선해양", 50, 0),
        "298040": ("효성중공업", 2, 0),
        "010120": ("LS ELECTRIC", 5, 0),
        "267260": ("HD현대일렉트릭", 3, 0),
        "034020": ("두산에너빌리티", 19, 0),
    }

    try:
        token = await get_kis_token()
        msg = "📊 *보유 종목 현황*\n\n"

        for ticker, (name, qty, avg) in holdings.items():
            price_data = await get_stock_price(ticker, token)
            await asyncio.sleep(0.3)  # API 제한

        msg += "_KIS API 실제 연동 후 가격 표시됩니다_"
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ 오류 발생: {str(e)}\n\nAPI 키를 확인해주세요.")

# /analyze 명령어
async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("사용법: /analyze 005930 (삼성전자 예시)")
        return

    ticker = context.args[0]
    await update.message.reply_text(f"⏳ {ticker} 분석 중...")

    msg = (
        f"📈 *{ticker} 분석*\n\n"
        "현재가: KIS API 연동 필요\n"
        "등락률: -\n"
        "거래량: -\n"
        "외국인: -\n"
        "기관: -\n\n"
        "_실제 데이터는 KIS API 키 설정 후 표시됩니다_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# /help 명령어
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📖 *도움말*\n\n"
        "/portfolio - 보유 종목 손익 현황\n"
        "/analyze [코드] - 특정 종목 분석\n"
        "  예) /analyze 034020 (두산에너빌리티)\n"
        "/help - 이 메시지\n\n"
        "🔔 *자동 알림*\n"
        "- 매일 장 마감 후 외국인 순매수 요약\n"
        "- 이상 신호 감지 시 즉시 알림"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# 봇 시작 후 알림 (실패해도 봇은 계속 작동)
async def post_init(application: Application):
    try:
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text="✅ *주식 봇 시작됨!*\n\n/help 로 명령어 확인하세요",
            parse_mode="Markdown"
        )
        print("시작 알림 전송 성공!")
    except Exception as e:
        print(f"시작 알림 전송 실패 (봇은 정상 작동 중): {e}")
        print(f"CHAT_ID: {CHAT_ID}")
        print("텔레그램에서 봇에게 /start 를 먼저 보내주세요.")

def main():
    print("봇 시작 중...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("portfolio", portfolio))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("help", help_cmd))

    print("봇 실행 중!")
    app.run_polling()

if __name__ == "__main__":
    main()
