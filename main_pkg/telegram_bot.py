"""main_pkg telegram_bot — 텔레그램 명령어 핸들러.
auto-split from main.py.
"""
import asyncio
import os
import json
import re
from datetime import datetime, timedelta, time as dtime

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from main_pkg._ctx import (
    _KR_SECTORS, _SECTOR_LIMIT, _STOCK_LIMIT,
    _is_kr_trading_time, _read_regime, _safe_send,
    _extract_grade, _grade_arrow, _refresh_ws,
)
from kis_api import *
from kis_api import (
    _DATA_DIR, _is_us_ticker, _is_us_market_hours_kst, _is_us_market_closed, _guess_excd,
    ws_manager, get_ws_tickers, close_session,
    fetch_us_earnings_calendar, fetch_us_sector_etf,
    fetch_and_cache_disclosure, parse_disclosure_summary,
)

try:
    from report_crawler import (collect_reports, get_collection_tickers,
                                  collect_market_reports, DB_PATH as REPORT_DB_PATH)
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False
    REPORT_DB_PATH = os.path.join(os.environ.get("DATA_DIR", "data"), "stock.db")

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📊 포트폴리오", "🚨 알림현황"],
        ["📈 매크로", "🔍 워치리스트"],
        ["📰 리포트", "📋 전체현황"],
    ],
    resize_keyboard=True,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *부자가될거야 봇 v7*\n\n"
        "아래 버튼 또는 명령어를 사용하세요!\n\n"
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
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


# 포트폴리오 조회
async def portfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    portfolio = load_json(PORTFOLIO_FILE, {})
    _meta_keys = {"us_stocks", "cash_krw", "cash_usd"}
    kr_stocks = {k: v for k, v in portfolio.items() if k not in _meta_keys}
    us_stocks = portfolio.get("us_stocks", {})
    if not kr_stocks and not us_stocks:
        await update.message.reply_text("📭 포트폴리오 비어있음\n/setportfolio 로 등록"); return
    await update.message.reply_text("⏳ 포트폴리오 조회 중...")
    token = await get_kis_token()
    msg = "📊 *포트폴리오 현황*\n\n"
    total_eval = total_cost = 0
    if kr_stocks:
        msg += "🇰🇷 *한국*\n"
        for t, info in kr_stocks.items():
            try:
                qty = info.get("qty", 0)
                avg = float(info.get("avg_price", 0))
                cur = ws_manager.get_cached_price(t)
                if cur is None:
                    d = await kis_stock_price(t, token) if token else {}
                    cur = int(d.get("stck_prpr", 0) or 0)
                    await asyncio.sleep(0.3)
                else:
                    cur = int(cur)
                eval_amt = cur * qty
                cost_amt = int(avg) * qty
                pnl = eval_amt - cost_amt
                pnl_pct = (cur - avg) / avg * 100 if avg else 0
                total_eval += eval_amt
                total_cost += cost_amt
                icon = "🔺" if pnl >= 0 else "🔻"
                msg += f"{icon} *{info.get('name', t)}* {qty}주\n  {cur:,}원 ({pnl_pct:+.1f}%) P&L {pnl:+,}원\n"
            except Exception:
                msg += f"⚪ *{info.get('name', t)}* — 조회실패\n"
        msg += "\n"
    if us_stocks:
        msg += "🇺🇸 *미국*\n"
        for sym, info in us_stocks.items():
            try:
                qty = info.get("qty", 0)
                avg = float(info.get("avg_price", 0))
                cur = ws_manager.get_cached_price(sym)
                if cur is None:
                    d = await kis_us_stock_price(sym, token) if token else {}
                    cur = float(d.get("last", 0) or 0)
                    await asyncio.sleep(0.3)
                else:
                    cur = float(cur)
                eval_amt = cur * qty
                cost_amt = avg * qty
                pnl = eval_amt - cost_amt
                pnl_pct = (cur - avg) / avg * 100 if avg else 0
                icon = "🔺" if pnl >= 0 else "🔻"
                msg += f"{icon} *{info.get('name', sym)}* {qty}주\n  ${cur:,.2f} ({pnl_pct:+.1f}%) P&L ${pnl:+,.2f}\n"
            except Exception:
                msg += f"⚪ *{info.get('name', sym)}* — 조회실패\n"
        msg += "\n"
    cash_krw = portfolio.get("cash_krw", 0)
    cash_usd = portfolio.get("cash_usd", 0)
    if cash_krw or cash_usd:
        msg += "💵 *현금*\n"
        if cash_krw:
            msg += f"  KRW {cash_krw:,.0f}원\n"
        if cash_usd:
            msg += f"  USD ${cash_usd:,.2f}\n"
    if total_cost > 0:
        total_pnl = total_eval - total_cost
        total_pct = total_pnl / total_cost * 100
        msg += f"\n📈 *KR 총계* 평가 {total_eval:,}원 ({total_pct:+.1f}%)"
    await update.message.reply_text(msg, parse_mode="Markdown")


# 알림현황
async def alert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stops = load_stoploss()
    wa = load_watchalert()
    kr_stops = {k: v for k, v in stops.items() if k != "us_stocks" and isinstance(v, dict)}
    us_stops = stops.get("us_stocks") or {}
    if not kr_stops and not us_stops and not wa:
        await update.message.reply_text("📭 설정된 알림 없음"); return
    await update.message.reply_text("⏳ 알림현황 조회 중...")
    token = await get_kis_token()
    msg = "🚨 *알림 현황*\n\n"
    if kr_stops:
        msg += "🛑 *한국 손절선*\n"
        for t, info in kr_stops.items():
            try:
                sp = float(info.get("stop_price") or info.get("stop") or 0)
                tgt = float(info.get("target_price") or 0)
                d = await kis_stock_price(t, token) if token else {}
                cur = int(d.get("stck_prpr", 0) or 0)
                gap = (sp - cur) / cur * 100 if cur else 0
                icon = "🔴" if gap >= -3 else "⚪"
                tgt_str = f" → 목표 {tgt:,.0f}원" if tgt > 0 else ""
                msg += f"{icon} *{info.get('name', t)}* 현재 {cur:,}원 | 손절 {sp:,.0f}원 ({gap:+.1f}%){tgt_str}\n"
                await asyncio.sleep(0.3)
            except Exception:
                msg += f"⚪ *{info.get('name', t)}* — 조회실패\n"
        msg += "\n"
    if us_stops:
        msg += "🛑 *미국 손절선*\n"
        for sym, info in us_stops.items():
            try:
                sp = float(info.get("stop_price") or info.get("stop") or 0)
                tgt = float(info.get("target_price") or 0)
                d = await get_yahoo_quote(sym)
                cur = float(d.get("price", 0) or 0) if d else 0
                gap = (sp - cur) / cur * 100 if cur else 0
                icon = "🔴" if gap >= -3 else "⚪"
                tgt_str = f" → 목표 ${tgt:,.2f}" if tgt > 0 else ""
                msg += f"{icon} *{info.get('name', sym)}* ${cur:,.2f} | 손절 ${sp:,.2f} ({gap:+.1f}%){tgt_str}\n"
            except Exception:
                msg += f"⚪ *{info.get('name', sym)}* — 조회실패\n"
        msg += "\n"
    if wa:
        msg += "👀 *매수감시*\n"
        for t, info in wa.items():
            bp = float(info.get("buy_price", 0))
            name = info.get("name", t)
            msg += f"• *{name}* 감시가 {bp:,.0f}원\n"
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
        d = await kis_stock_price(ticker, token)
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
        except Exception: pass

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
                if p > 30: ps += " 🔴위기"
                elif p < 20: ps += " 🟢안정"
                else: ps += " 🟡경계"
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
        # INVESTMENT_RULES v6: VIX 30 / 20 경계 (S&P 200MA 판정은 /regime·get_macro 참조)
        if vix_val > 30: msg += "🔴 *레짐: 위기* — 축적 현금 투입, A등급 리더 집중"
        elif vix_val < 20: msg += "🟢 *레짐: 공격* — 산업 흐름 + 리더 확인 시 진입 OK"
        else: msg += "🟡 *레짐: 경계* — 근거 더 엄격히, 현금 8~15% 축적"

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


async def insider_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/insider <ticker> [days]  → 종목 내부자 매수/매도 집계."""
    if not DART_API_KEY:
        await update.message.reply_text("❌ DART_API_KEY 미설정")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "사용법: /insider <종목코드> [일수]\n예: /insider 005930 30"
        )
        return
    ticker = args[0].strip()
    days = int(args[1]) if len(args) > 1 and args[1].isdigit() else 30
    if _is_us_ticker(ticker):
        await update.message.reply_text("❌ 내부자 거래는 한국 종목만 지원합니다.")
        return

    await update.message.reply_text(f"⏳ {ticker} 내부자 거래 조회 중 ({days}일)...")
    try:
        universe = get_stock_universe() or {}
        corp_map = await get_dart_corp_map(universe) if universe else {}
        corp_code = corp_map.get(ticker, "")
        if not corp_code:
            await update.message.reply_text(f"❌ {ticker} corp_code 매핑 없음 (유니버스 외)")
            return
        records = await kis_elestock(corp_code)
        upsert_insider_transactions(ticker, corp_code, records)
        agg = aggregate_insider_cluster(ticker, days=days)

        flag = "🚩" if agg["buyers"] >= 3 and agg["buy_qty"] > agg["sell_qty"] else "  "
        msg = f"🕵️ *{ticker} 내부자 거래* (최근 {days}일) {flag}\n\n"
        msg += f"매수 {agg['buyers']}명 / 매도 {agg['sellers']}명\n"
        msg += f"순매수 {agg['buy_qty'] - agg['sell_qty']:,}주 "
        msg += f"(+{agg['buy_qty']:,} / -{agg['sell_qty']:,})\n\n"
        if agg["recent"]:
            msg += "*최근 거래:*\n"
            for r in agg["recent"][:10]:
                delta = r.get("delta") or 0
                sign = "+" if delta > 0 else ""
                msg += f"• {r['date']} {r['name']}({r['ofcps']}) {sign}{delta:,}\n"
        else:
            msg += "_최근 거래 없음_"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")


# 워치리스트 (매수감시 종목 — grade 정렬)
_GRADE_ORDER = {"A": 0, "B+": 1, "B": 2, "B-": 3, "C+": 4, "C": 5, "D": 6, "": 7}

async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wa = load_watchalert()
    if not wa:
        await update.message.reply_text("📭 매수감시 종목 없음\nset_alert으로 등록"); return
    await update.message.reply_text("⏳ 워치리스트 조회 중...")
    token = await get_kis_token()
    today = datetime.now(KST)
    today_str = today.strftime("%Y-%m-%d")

    items = []
    for t, info in wa.items():
        buy_p = float(info.get("buy_price", 0))
        name = info.get("name", t)
        grade = info.get("grade", "")
        if not grade:
            import re as _re
            m = _re.search(r"\(([ABCD][+-]?)\)", info.get("memo", ""))
            grade = m.group(1) if m else ""
        mkt = info.get("market", "")
        if not mkt:
            mkt = "US" if _is_us_ticker(t) else "KR"
        memo = info.get("memo", "")
        updated = info.get("updated_at", info.get("created_at", ""))
        # 가격 조회
        cur = 0.0
        try:
            if mkt == "US":
                if _is_us_market_hours_kst():
                    d = await kis_us_stock_price(t, token) if token else {}
                    cur = float(d.get("last") or 0)
                # 미장마감이면 cur=0 유지
            else:
                d = await kis_stock_price(t, token) if token else {}
                cur = int(d.get("stck_prpr", 0) or 0)
            await asyncio.sleep(0.15)
        except Exception:
            pass
        gap_pct = (cur - buy_p) / buy_p * 100 if cur > 0 and buy_p > 0 else None
        triggered = cur > 0 and cur <= buy_p
        near = gap_pct is not None and -5 <= gap_pct <= 0 and not triggered
        # 30일 미갱신
        stale = False
        if updated:
            try:
                upd_dt = datetime.strptime(updated[:10], "%Y-%m-%d")
                stale = (today - upd_dt.replace(tzinfo=None)).days >= 30 if upd_dt.tzinfo is None else (today.replace(tzinfo=None) - upd_dt.replace(tzinfo=None)).days >= 30
            except Exception:
                pass
        blocked = "차단" in memo
        items.append({
            "t": t, "name": name, "grade": grade, "mkt": mkt,
            "buy_p": buy_p, "cur": cur, "gap_pct": gap_pct,
            "triggered": triggered, "near": near, "stale": stale,
            "blocked": blocked, "updated": updated,
        })

    # 정렬
    triggered_items = sorted([i for i in items if i["triggered"]],
                             key=lambda x: (_GRADE_ORDER.get(x["grade"], 7), abs(x["gap_pct"] or 0)))
    near_items = sorted([i for i in items if i["near"]],
                        key=lambda x: (_GRADE_ORDER.get(x["grade"], 7), abs(x["gap_pct"] or 0)))
    kr_items = sorted([i for i in items if i["mkt"] == "KR" and not i["triggered"] and not i["near"]],
                      key=lambda x: (_GRADE_ORDER.get(x["grade"], 7), abs(x["gap_pct"] or 999)))
    us_items = sorted([i for i in items if i["mkt"] == "US" and not i["triggered"] and not i["near"]],
                      key=lambda x: (_GRADE_ORDER.get(x["grade"], 7), abs(x["gap_pct"] or 999)))

    def _fmt(i):
        g = i["grade"] or "·"
        block = "🚨 " if i["blocked"] else ""
        stale = "⏰ " if i["stale"] else ""
        date_str = f" ({i['updated'][5:10]})" if i["updated"] else ""
        if i["mkt"] == "US":
            bp = f"${i['buy_p']:,.1f}"
            cp = f"현${i['cur']:,.1f}" if i["cur"] > 0 else "미장마감"
        else:
            bp = f"{i['buy_p']/1000:.0f}K" if i["buy_p"] >= 1000 else f"{i['buy_p']:,.0f}"
            cp = f"현{i['cur']/1000:.0f}K" if i["cur"] >= 1000 else f"현{i['cur']:,}" if i["cur"] > 0 else "?"
        gap = f" {i['gap_pct']:+.1f}%" if i["gap_pct"] is not None else ""
        return f"{block}{stale}{g} {i['name']} {bp} {cp}{gap}{date_str}\n"

    msgs = []
    msg = "👀 *매수감시 워치리스트*\n\n"

    if triggered_items:
        msg += "⚡ *감시가 도달*\n"
        for i in triggered_items:
            msg += _fmt(i)
        msg += "\n"
    if near_items:
        msg += "🔔 *5% 이내 근접*\n"
        for i in near_items:
            msg += _fmt(i)
        msg += "\n"

    kr_msg = ""
    if kr_items:
        kr_msg = "🇰🇷 *한국*\n"
        for i in kr_items:
            kr_msg += _fmt(i)
        kr_msg += "\n"

    us_msg = ""
    if us_items:
        us_msg = "🇺🇸 *미국*\n"
        for i in us_items:
            us_msg += _fmt(i)
        us_msg += "\n"

    stale_cnt = sum(1 for i in items if i["stale"])
    blocked_cnt = sum(1 for i in items if i["blocked"])
    footer = f"총 {len(items)}개"
    if stale_cnt:
        footer += f" | ⏰ 30일+ 미갱신 {stale_cnt}"
    if blocked_cnt:
        footer += f" | 🚨 차단 {blocked_cnt}"

    # 4096자 제한 처리
    combined = msg + kr_msg + us_msg + footer
    if len(combined) <= 4000:
        await update.message.reply_text(combined, parse_mode="Markdown")
    else:
        # 분할 전송
        if len(msg) > 10:
            await update.message.reply_text(msg.rstrip(), parse_mode="Markdown")
        if kr_msg:
            kr_full = "👀 *워치 — 한국*\n\n" + kr_msg + footer
            if len(kr_full) > 4000:
                kr_full = kr_full[:3950] + "\n_(일부 생략)_"
            await update.message.reply_text(kr_full, parse_mode="Markdown")
        if us_msg:
            us_full = "👀 *워치 — 미국*\n\n" + us_msg
            await update.message.reply_text(us_full, parse_mode="Markdown")


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /watch: watchalert.json에 KR 워치 추가 (buy_price=0 = 순수 워치)
    if len(context.args) < 2:
        await update.message.reply_text("사용법: /watch 005930 삼성전자"); return
    ticker, wname = context.args[0], context.args[1]
    wa = load_watchalert()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    prev = wa.get(ticker, {})
    is_update = bool(prev)
    old_name = prev.get("name", "")
    wa[ticker] = {
        "name": wname,
        "market": "KR",  # /watch 는 KR 전용 (사용자 오입력 방어). 미국은 /addus 사용.
        "buy_price": float(prev.get("buy_price") or 0.0),
        "qty": int(prev.get("qty") or 0),
        "memo": prev.get("memo", ""),
        "grade": prev.get("grade"),
        "created_at": prev.get("created_at", today),
        "updated_at": today,
    }
    save_json(WATCHALERT_FILE, wa)
    await _refresh_ws()
    if is_update:
        bp = float(prev.get("buy_price") or 0)
        extra = f" (매수감시 {bp:,.0f}원 유지)" if bp > 0 else ""
        if old_name and old_name != wname:
            msg = f"🔄 *{ticker}* 이름 갱신: {old_name} → *{wname}*{extra}"
        else:
            msg = f"🔄 *{wname}* 이미 존재 (갱신){extra}"
    else:
        msg = f"✅ *{wname}* 추가!"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /unwatch: watchalert 엔트리 제거. buy_price>0이면 매수감시 보호 차원에서 거부
    if not context.args:
        await update.message.reply_text("사용법: /unwatch 005930"); return
    ticker = context.args[0]
    wa = load_watchalert()
    if ticker in wa:
        entry = wa[ticker]
        nm = entry.get("name") or ticker
        if float(entry.get("buy_price") or 0) > 0:
            await update.message.reply_text(
                f"⚠️ *{nm}* 매수감시 활성 중입니다. 먼저 매수감시 해제 후 삭제하세요.",
                parse_mode="Markdown")
            return
        wa.pop(ticker)
        save_json(WATCHALERT_FILE, wa)
        await _refresh_ws()
        await update.message.reply_text(f"🗑 *{nm}* 삭제!", parse_mode="Markdown")
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
    # /addus: watchalert.json에 US 워치 추가 (qty 포함, buy_price 보존)
    if len(context.args) < 3:
        await update.message.reply_text("사용법: /addus TSLA 테슬라 12\n(심볼 이름 수량)"); return
    sym = context.args[0].upper()
    name = context.args[1]
    try:
        qty = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ 수량은 숫자로"); return
    wa = load_watchalert()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    prev = wa.get(sym, {})
    is_update = bool(prev)
    old_qty = int(prev.get("qty") or 0)
    old_name = prev.get("name", "")
    wa[sym] = {
        "name": name,
        "market": "US",
        "buy_price": float(prev.get("buy_price") or 0.0),
        "qty": qty,
        "memo": prev.get("memo", ""),
        "grade": prev.get("grade"),
        "created_at": prev.get("created_at", today),
        "updated_at": today,
    }
    save_json(WATCHALERT_FILE, wa)
    if is_update:
        changes = []
        if old_name and old_name != name: changes.append(f"이름 {old_name}→{name}")
        if old_qty != qty: changes.append(f"수량 {old_qty}→{qty}주")
        detail = ", ".join(changes) if changes else "동일"
        msg = f"🔄 🇺🇸 *{name}* ({sym}) 갱신: {detail}"
    else:
        msg = f"✅ 🇺🇸 *{name}* ({sym}) {qty}주 추가!"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def remus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /remus: watchalert US 엔트리 제거. buy_price>0이면 매수감시 보호 차원에서 거부
    if not context.args:
        await update.message.reply_text("사용법: /remus TSLA"); return
    sym = context.args[0].upper()
    wa = load_watchalert()
    if sym in wa:
        entry = wa[sym]
        name = entry.get("name") or sym
        if float(entry.get("buy_price") or 0) > 0:
            await update.message.reply_text(
                f"⚠️ *{name}* ({sym}) 매수감시 활성 중입니다. 먼저 매수감시 해제 후 삭제하세요.",
                parse_mode="Markdown")
            return
        wa.pop(sym)
        save_json(WATCHALERT_FILE, wa)
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
    except Exception: await update.message.reply_text("❌ 손절가는 숫자"); return
    fourth = 0.0
    if len(context.args) >= 4:
        try: fourth = float(context.args[3])
        except Exception: pass
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
        stops[ticker] = {"name": name, "stop_price": stop, "entry_price": fourth, "target_price": fourth}
        save_json(STOPLOSS_FILE, stops)
        await _refresh_ws()
        lp = f" (진입가 대비 {((stop - fourth) / fourth * 100):.1f}%)" if fourth > 0 else ""
        await update.message.reply_text(
            f"🛑 *{name}* 손절선 {stop:,.0f}원{lp}\n장중 실시간 체결가 감시 중", parse_mode="Markdown")


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


# 전체현황 → 대시보드
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    portfolio = load_json(PORTFOLIO_FILE, {})
    _meta_keys = {"us_stocks", "cash_krw", "cash_usd"}
    kr_pf = {k: v for k, v in portfolio.items() if k not in _meta_keys}
    us_pf = portfolio.get("us_stocks", {})
    wa = load_watchalert()
    today = datetime.now(KST)

    # ── 보유종목 집계 ──
    hold_cnt = len(kr_pf) + len(us_pf)
    total_eval = 0
    total_cost = 0
    token = await get_kis_token()
    for t, info in kr_pf.items():
        qty = int(info.get("qty", 0))
        avg = float(info.get("avg_price", 0))
        total_cost += qty * avg
        try:
            d = await kis_stock_price(t, token) if token else {}
            p = int(d.get("stck_prpr", 0) or 0)
            total_eval += qty * p
            await asyncio.sleep(0.15)
        except Exception:
            total_eval += qty * avg
    for sym, info in us_pf.items():
        qty = int(info.get("qty", 0))
        avg = float(info.get("avg_price", 0))
        total_cost += qty * avg * 1400
        try:
            if _is_us_market_hours_kst():
                d = await kis_us_stock_price(sym, token) if token else {}
                p = float(d.get("last") or 0)
                total_eval += qty * p * 1400
            else:
                total_eval += qty * avg * 1400
            await asyncio.sleep(0.15)
        except Exception:
            total_eval += qty * avg * 1400
    pnl_pct = round((total_eval - total_cost) / total_cost * 100, 1) if total_cost > 0 else 0
    eval_m = f"{total_eval / 1_000_000:.0f}M" if total_eval >= 1_000_000 else f"{total_eval:,.0f}"

    # ── 워치 집계 ──
    watch_cnt = len(wa)
    triggered_cnt = 0
    near_cnt = 0
    blocked_cnt = 0
    stale_cnt = 0
    for t, info in wa.items():
        buy_p = float(info.get("buy_price", 0))
        memo = info.get("memo", "")
        updated = info.get("updated_at", info.get("created_at", ""))
        if "차단" in memo:
            blocked_cnt += 1
        if updated:
            try:
                upd_dt = datetime.strptime(updated[:10], "%Y-%m-%d")
                if (today - upd_dt.replace(tzinfo=None)).days >= 30:
                    stale_cnt += 1
            except Exception:
                pass
        # 가격 체크 (간이 — KR만, US는 미장이면 스킵)
        try:
            if _is_us_ticker(t):
                pass  # 미장시간 체크 비용 줄이기
            else:
                d = await kis_stock_price(t, token) if token else {}
                cur = int(d.get("stck_prpr", 0) or 0)
                if cur > 0 and buy_p > 0:
                    if cur <= buy_p:
                        triggered_cnt += 1
                    elif (cur - buy_p) / buy_p * 100 <= 5:
                        near_cnt += 1
                await asyncio.sleep(0.1)
        except Exception:
            pass

    # ── 레짐 ──
    regime_name, regime_emoji = _read_regime()
    regime_kr = {"offensive": "공격", "neutral": "중립", "crisis": "위기"}.get(regime_name, "미정")
    regime_cur = load_json(REGIME_STATE_FILE, {}).get("current", {})

    # ── 메시지 조립 ──
    msg = "📊 *전체현황*\n\n"
    msg += f"💼 보유 {hold_cnt}종목 | 총{eval_m} | {pnl_pct:+.1f}%\n"
    msg += f"👁 워치 {watch_cnt}종목 | ⚡도달 {triggered_cnt} | 🔔근접 {near_cnt}\n"
    if blocked_cnt:
        msg += f"🚨 진입차단 {blocked_cnt}종목\n"
    if stale_cnt:
        msg += f"⏰ 30일+ 미갱신 {stale_cnt}종목\n"
    msg += f"{regime_emoji} 레짐 {regime_kr}"
    if regime_cur.get("combined_score"):
        msg += f" (점수 {regime_cur['combined_score']})"
    msg += "\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


# 리포트
async def reports_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _REPORT_AVAILABLE:
        await update.message.reply_text("📭 리포트 기능 미설치 (pdfplumber/bs4 필요)"); return
    import sqlite3 as _sqlite3
    from report_crawler import DB_PATH as _REPORT_DB_PATH
    cutoff = (datetime.now(KST) - timedelta(days=3)).strftime("%Y-%m-%d")
    try:
        _conn = _sqlite3.connect(_REPORT_DB_PATH, timeout=10)
        _conn.execute("PRAGMA cache_size = -65536")
        _conn.execute("PRAGMA temp_store = MEMORY")
        _conn.execute("PRAGMA mmap_size = 268435456")
        _conn.execute("PRAGMA busy_timeout = 30000")
        _conn.row_factory = _sqlite3.Row
        rows = _conn.execute(
            "SELECT date, ticker, name, source, title FROM reports WHERE date >= ? ORDER BY date DESC",
            (cutoff,)).fetchall()
        _conn.close()
        recent = [dict(r) for r in rows]
    except Exception as _e:
        await update.message.reply_text(f"📭 DB 조회 오류: {_e}"); return
    if not recent:
        await update.message.reply_text("📭 최근 3일 리포트 없음"); return
    # 종목별 그룹핑
    by_stock = {}
    for r in recent:
        key = r.get("name") or r.get("ticker", "?")
        by_stock.setdefault(key, []).append(r)
    msg = "📰 *최근 3일 리포트*\n\n"
    for stock, reps in by_stock.items():
        msg += f"📌 *{stock}*\n"
        for r in reps[:5]:  # 종목당 최대 5건
            src = r.get("source", "?")
            title = r.get("title", "?")
            date = r.get("date", "?")
            msg += f"  • {src}: {title} ({date})\n"
        msg += "\n"
    # 텔레그램 메시지 길이 제한
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n_(일부 생략)_"
    try:
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(msg)


async def manual_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 요약 생성 중...")
    from main_pkg.jobs.kr_summary import daily_kr_summary
    from main_pkg.jobs.us_summary import daily_us_summary
    await daily_kr_summary(context)
    await daily_us_summary(context, force=True)


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
    await _refresh_ws()

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
        "/insider 코드 [일수] - 내부자 매수/매도 집계 (기본 30일)\n"
        "/summary - 한국 장마감 요약(수동)\n\n"
        "📊 *빠른 조회 (버튼)*\n"
        "/portfolio - 보유종목 손익\n"
        "/alert - 손절선/매수감시 현황\n"
        "/status - 전체현황 (보유+매수감시 통합)\n"
        "/reports - 최근 3일 증권사 리포트\n\n"
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
        "• 📋 주간리뷰: 일 10:00\n\n"
        "💡 심층 분석은 Claude.ai에서!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# US-analyst + sanity jobs extracted to jobs/ (2026-06-13); re-exported for back-compat
# ━━━━━━━━━━━━━━━━━━━━━━━━━
from .jobs.us_analyst import (
    daily_us_rating_scan, weekly_us_ratings_universe_scan, weekly_us_analyst_sync,
    hourly_us_holdings_check, weekly_us_analyst_report,
    _US_SELL_RATINGS, _US_DOWNGRADE_PT_THRESHOLD,
    _detect_new_downgrades, _md_escape, _rating_elapsed,
    _format_urgent_downgrade_alert, _format_daily_rating_summary,
)
from .jobs.sanity import weekly_sanity_check, weekly_log_rotate, _is_krx_business_day


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 봇 시작
