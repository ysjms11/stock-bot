"""main_pkg _entry — post_init, main, _run_all.
auto-split from main.py.
"""
import asyncio
import os
import sys
import socket
import signal
from datetime import datetime, time as dtime

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from aiohttp import web

from main_pkg._ctx import (
    _safe_send,
)
from main_pkg.telegram_bot import (
    MAIN_KEYBOARD,
    start, analyze, scan, macro, news_cmd, dart_cmd, insider_cmd, manual_summary,
    watchlist_cmd, watch, unwatch,
    uslist_cmd, addus, remus,
    setstop, delstop, stops_cmd,
    setportfolio_cmd, setusportfolio_cmd,
    portfolio_cmd, alert_cmd,
    status_cmd, reports_cmd, help_cmd,
)

# ── Reply Keyboard 버튼 핸들러 (_button_handler는 _entry.py에 정의) ──
async def _button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from main_pkg.telegram_bot import (
        portfolio_cmd as _portfolio_cmd,
        alert_cmd as _alert_cmd,
        macro as _macro,
        watchlist_cmd as _watchlist_cmd,
        reports_cmd as _reports_cmd,
        status_cmd as _status_cmd,
    )
    _BUTTON_MAP = {
        "📊 포트폴리오": _portfolio_cmd,
        "🚨 알림현황": _alert_cmd,
        "📈 매크로": _macro,
        "🔍 워치리스트": _watchlist_cmd,
        "📰 리포트": _reports_cmd,
        "📋 전체현황": _status_cmd,
    }
    text = (update.message.text or "").strip()
    handler = _BUTTON_MAP.get(text)
    if handler:
        await handler(update, context)
from main_pkg.schedule import register_all_schedules
from main_pkg.jobs.collect import daily_collect_job
from mcp_tools import (
    mcp_sse_handler, mcp_messages_handler,
    mcp_streamable_post_handler, mcp_streamable_delete_handler, mcp_streamable_options_handler,
)
import dashboard

from kis_api import *
from kis_api import (
    _DATA_DIR, _is_us_ticker, _is_us_market_hours_kst, _is_us_market_closed, _guess_excd,
    ws_manager, get_ws_tickers, close_session,
    fetch_us_earnings_calendar, fetch_us_sector_etf,
    fetch_and_cache_disclosure, parse_disclosure_summary,
)

try:
    from db_collector import collect_daily, collect_financial_weekly
    _HAS_DB_COLLECTOR = True
except ImportError:
    _HAS_DB_COLLECTOR = False

async def post_init(application: Application):
    # ── 자동 복원 체크: 핵심 파일 없으면 Gist에서 복원 ──────────────────
    _critical = [PORTFOLIO_FILE, STOPLOSS_FILE, WATCHALERT_FILE]
    if GITHUB_TOKEN and any(not os.path.exists(f) for f in _critical):
        try:
            res = await restore_data_files(force=False)
            if res.get("ok") and res.get("restored"):
                print(f"[restore] 자동 복원 완료: {res['restored']}")
                try:
                    await application.bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"♻️ 데이터 자동 복원 완료\n복원: {', '.join(res['restored'])}"
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"[restore] 자동 복원 실패: {e}")

    # 봇 시작 알림 — silent (학습 #?? 5/12: 재시작 시 텔레그램 노이즈 제거)
    # 사용자가 봇 가동 여부 확인하려면 /health 또는 /help 명령
    if not DART_API_KEY:
        try:
            await application.bot.send_message(
                chat_id=CHAT_ID,
                text="⚠️ DART_API_KEY 미설정 — DART 알림 비활성",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"DART 경고 알림 실패: {e}")
    print(f"✅ 부자가될거야 v7 시작 (DART {'✅' if DART_API_KEY else '❌'}) — silent startup", flush=True)

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
        # KIS API 시작테스트 결과 — 에러 시만 발사 (5/12 fix: 재시작 노이즈 제거)
        # 정상 시 print 만, 에러 (❌) 1건+ 있으면 텔레그램 알림
        has_error = any("❌" in line for line in lines)
        print("\n".join(lines), flush=True)
        if has_error:
            try:
                await application.bot.send_message(
                    chat_id=CHAT_ID, text="\n".join(lines), parse_mode="Markdown")
            except Exception as e:
                print(f"KIS 테스트 결과 전송 실패: {e}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 재시작 시 당일 미완 daily_collect_job 재실행
    # (포트 충돌/크래시 복구 — 2026-04-17 daily_collect 미실행 사건 재발 방지)
    # 평일 19시 이후 재시작인데 당일 daily_snapshot 0건이면 재실행
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    dt_kst = datetime.now(KST)
    if 0 <= dt_kst.weekday() <= 4 and dt_kst.hour >= 19:
        today = dt_kst.strftime("%Y%m%d")
        try:
            from db_collector import _get_db
            conn = _get_db()
            row = conn.execute(
                "SELECT COUNT(*) FROM daily_snapshot WHERE trade_date=?",
                (today,)
            ).fetchone()
            conn.close()
            if not row or row[0] == 0:
                print(f"[retry] 당일 ({today}) daily_snapshot 0건 — daily_collect_job 재실행")

                class _CtxShim:
                    """daily_collect_job(context) 시그니처 호환용 (bot 속성만 필요)"""
                    def __init__(self, bot):
                        self.bot = bot
                t = asyncio.create_task(daily_collect_job(_CtxShim(application.bot)))
                t.add_done_callback(
                    lambda f: print(f"[retry] job 에러: {f.exception()}") if f.exception() else None
                )
        except Exception as e:
            print(f"[retry] 미완 job 재실행 체크 실패: {e}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # US 애널 마스터 1회 동기화 (us_analysts 거의 비어있을 때만)
    # (정상 운영 후엔 매주 일요일 04:00 weekly_us_analyst_sync 잡이 처리)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    try:
        from db_collector import _get_db, sync_us_analyst_master
        conn = _get_db()
        master_count = conn.execute("SELECT COUNT(*) FROM us_analysts").fetchone()[0]
        ratings_count = conn.execute(
            "SELECT COUNT(DISTINCT analyst_slug) FROM us_analyst_ratings WHERE analyst_slug IS NOT NULL"
        ).fetchone()[0]
        conn.close()
        # ratings 풀 대비 마스터가 10% 미만이면 sync 필요
        if ratings_count > 100 and master_count < ratings_count * 0.1:
            print(f"[us_analyst_sync] 부트시 마스터({master_count}) << ratings({ratings_count}) — 1회 동기화 실행")
            r = await asyncio.to_thread(sync_us_analyst_master)
            print(f"[us_analyst_sync] 부트 완료: {r}")
    except Exception as e:
        print(f"[us_analyst_sync] 부트 동기화 실패: {e}")


def main():
    print("봇 시작 중...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # 명령어 등록
    commands = [
        ("start", start), ("analyze", analyze), ("scan", scan), ("macro", macro),
        ("news", news_cmd), ("dart", dart_cmd), ("insider", insider_cmd), ("summary", manual_summary),
        ("watchlist", watchlist_cmd), ("watch", watch), ("unwatch", unwatch),
        ("uslist", uslist_cmd), ("addus", addus), ("remus", remus),
        ("setstop", setstop), ("delstop", delstop), ("stops", stops_cmd),
        ("setportfolio", setportfolio_cmd),
        ("setusportfolio", setusportfolio_cmd),
        ("portfolio", portfolio_cmd), ("alert", alert_cmd),
        ("status", status_cmd), ("reports", reports_cmd),
        ("help", help_cmd),
    ]
    for cmd, fn in commands:
        app.add_handler(CommandHandler(cmd, fn))

    # Reply Keyboard 버튼 텍스트 핸들러
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^(📊 포트폴리오|🚨 알림현황|📈 매크로|🔍 워치리스트|📰 리포트|📋 전체현황)$"),
        _button_handler,
    ))

    # 자동 알림 스케줄 — register_all_schedules(jq)로 위임
    register_all_schedules(app.job_queue)

    port = int(os.environ.get("PORT", 8080))
    print(f"봇 실행! MCP SSE 서버 포트: {port}")
    asyncio.run(_run_all(app, port))


async def _run_all(app, port):
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 포트 바인드 안전장치: 충돌 시 5초×3회 재시도, 실패하면 정상 종료
    # (launchd 재시작 대기) — 2026-04-17 daily_collect 미실행 사건 재발 방지
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    for attempt in range(3):
        try:
            probe = socket.socket()
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("0.0.0.0", port))
            probe.close()
            break
        except OSError as e:
            print(f"[port] {port} 사용중 (시도 {attempt+1}/3): {e}")
            if attempt < 2:
                await asyncio.sleep(5)
            else:
                print(f"[port] 포트 해제 실패, 봇 종료 (launchd 재시작 대기)")
                sys.exit(1)

    # MCP aiohttp 서버 시작
    mcp_app = web.Application(client_max_size=50 * 1024 * 1024)  # 50MB for KRX upload
    mcp_app.router.add_get("/mcp", mcp_sse_handler)
    mcp_app.router.add_post("/mcp/messages", mcp_messages_handler)
    # Streamable HTTP transport (MCP 2025-03-26)
    mcp_app.router.add_post("/mcp", mcp_streamable_post_handler)
    mcp_app.router.add_delete("/mcp", mcp_streamable_delete_handler)
    mcp_app.router.add_options("/mcp", mcp_streamable_options_handler)
    mcp_app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))
    # 대시보드 라우트 (5/5 리팩토링으로 dashboard.py 분리)
    dashboard.register_routes(mcp_app)
    runner = web.AppRunner(mcp_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port, reuse_address=True)
    await site.start()
    print(f"MCP SSE 서버 시작: 0.0.0.0:{port}/mcp")

    # KIS WebSocket 실시간 알림 시작 (KR 전용, 평일 09:00~16:00 KST)
    async def _ws_alert_cb(ticker: str, price: int):
        """체결가 → 손절선/목표가/매수감시 도달 시 텔레그램 알림"""
        stops  = load_stoploss()
        wa     = load_watchalert()
        alerts = []

        info = stops.get(ticker, {})
        if info:
            stop   = float(info.get("stop_price", 0) or 0)
            target = float(info.get("target_price", 0) or 0)
            name   = info.get("name", ticker)
            fired  = ws_manager._fired.setdefault(ticker, set())
            if stop > 0 and price <= stop and "stop" not in fired:
                fired.add("stop")
                alerts.append(f"⚠️ {name} 손절선 도달! {price:,}원 ≤ {stop:,}원")
            if target > 0 and price >= target and "target" not in fired:
                fired.add("target")
                alerts.append(f"🎯 {name} 목표가 도달! {price:,}원 ≥ {target:,}원")

        _now_ws = datetime.now(KST)
        _ws_time_ok = _now_ws.weekday() < 5 and (8 <= _now_ws.hour < 18)
        wa_info = wa.get(ticker, {}) if _ws_time_ok else {}
        if wa_info:
            buy_p = float(wa_info.get("buy_price", 0) or 0)
            name  = wa_info.get("name", ticker)
            fired = ws_manager._fired.setdefault(ticker, set())
            if buy_p > 0 and price <= buy_p and "buy" not in fired:
                _today_w = datetime.now(KST).strftime("%Y-%m-%d")
                _ws = load_json(WATCH_SENT_FILE, {})
                if _ws.get(ticker) != _today_w:
                    fired.add("buy")
                    _ws[ticker] = _today_w
                    save_json(WATCH_SENT_FILE, _ws)
                    alerts.append(f"📢 {name} 매수감시가 도달! {price:,}원 ≤ {buy_p:,}원")
                else:
                    fired.add("buy")  # WS fired 표시만 하고 알림은 스킵

        for msg in alerts:
            try:
                await app.bot.send_message(chat_id=CHAT_ID, text=msg)
            except Exception as e:
                print(f"[entry] WS 알림 전송 실패 (무시): {e}")

    await ws_manager.start(_ws_alert_cb, get_ws_tickers())
    print(f"[WS] 실시간 매니저 시작 (KR {len(ws_manager._subscribed)}개 + US {len(ws_manager._subscribed_us)}개)")

    # 텔레그램 봇 비동기 실행
    stop_event = asyncio.Event()

    def _signal_handler():
        print("[Shutdown] SIGTERM/SIGINT 수신 — graceful 종료 시작", flush=True)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        async with app:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            await stop_event.wait()  # SIGTERM/SIGINT 까지 대기
            print("[Shutdown] 봇 updater 종료 중...", flush=True)
            await app.updater.stop()
    finally:
        try:
            await asyncio.wait_for(runner.cleanup(), timeout=8.0)
            print("[Shutdown] aiohttp runner cleanup 완료 (포트 release)", flush=True)
        except asyncio.TimeoutError:
            print("[Shutdown] runner.cleanup() 8초 timeout — 강제 진행", flush=True)
        await close_session()
        print("[Shutdown] aiohttp 공유 세션 정리 완료", flush=True)


if __name__ == "__main__":
    main()

