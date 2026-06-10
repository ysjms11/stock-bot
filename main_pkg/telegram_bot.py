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
    await update.message.reply_text(msg, parse_mode="Markdown")


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
# 주간 무결성 체크 (일 07:05 KST)
# 최근 영업일 5일 daily_snapshot 누락 시 텔레그램 경고
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# KRX 공휴일 — db_collector._KR_MARKET_HOLIDAYS를 단일 소스로 사용(중복 제거).
# 갱신은 거기 한 곳에서. collect_daily 휴장일 가드와 동일 집합 보장.
from db_collector import _KR_MARKET_HOLIDAYS as _KRX_HOLIDAYS, db_write_lock


def _is_krx_business_day(d) -> bool:
    """KRX 영업일 판정. d: datetime.date 또는 datetime."""
    if hasattr(d, "date"):
        d = d.date()
    if d.weekday() >= 5:  # 토(5)/일(6)
        return False
    return d.strftime("%Y%m%d") not in _KRX_HOLIDAYS


async def weekly_sanity_check(context):
    """매주 일요일 07:05: 최근 영업일 5일 daily_snapshot 존재 확인.
    KRX 공휴일(근로자의 날·신정·설·추석·임시공휴일 등)은 영업일에서 제외.
    당해 _KRX_HOLIDAYS 등록 카운트 부족 시 갱신 알림 (매주 발송 → 잊지 않게).
    """
    try:
        from db_collector import _get_db
        conn = _get_db()
        cur = conn.execute(
            "SELECT trade_date, COUNT(*) FROM daily_snapshot "
            "WHERE trade_date >= ? GROUP BY trade_date ORDER BY trade_date DESC",
            ((datetime.now(KST) - timedelta(days=14)).strftime("%Y%m%d"),)
        )
        rows = cur.fetchall()
        conn.close()
        # 지난 5 영업일 역산 — KRX 공휴일 제외
        bizdays = []
        d = datetime.now(KST).date() - timedelta(days=1)
        # 안전 상한: 14일 뒤로까지 (장기 연휴 대비)
        for _ in range(14):
            if len(bizdays) >= 5:
                break
            if _is_krx_business_day(d):
                bizdays.append(d.strftime("%Y%m%d"))
            d -= timedelta(days=1)
        have = {r[0] for r in rows if r[1] > 1500}
        missing = [b for b in bizdays if b not in have]
        if missing:
            msg = f"⚠️ daily_snapshot 누락 영업일: {', '.join(missing)}"
            await context.bot.send_message(chat_id=CHAT_ID, text=msg)

            # 누락 영업일 감지 후 자동 백필 (학습 #28 영구 대응)
            try:
                import json as _json
                from db_collector import backfill_day_via_chart
                universe_data = (_json.load(open(UNIVERSE_FILE))
                                 if os.path.exists(UNIVERSE_FILE) else {})
                tickers = list(universe_data.get("codes", {}).keys())
                if not tickers:
                    print("[catchup] universe 비어있음, 스킵", flush=True)
                else:
                    for d in missing:
                        try:
                            r = await backfill_day_via_chart(d, tickers)
                            print(f"[catchup] {d}: ok={r['ok']} fail={r['fail']}",
                                  flush=True)
                        except Exception as e:
                            print(f"[catchup] {d} 오류: {e}", flush=True)
            except Exception as e:
                print(f"[catchup] 오류: {e}", flush=True)

        # KRX 공휴일 list 연 1회 갱신 알림
        # 정상 한 해 13~16건. 8건 미만이면 list 미갱신/누락으로 간주
        this_year_str = str(datetime.now(KST).year)
        krx_cnt = sum(1 for d in _KRX_HOLIDAYS if d.startswith(this_year_str))
        if krx_cnt < 8:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=(f"📅 KRX 공휴일 list 갱신 필요\n"
                      f"{this_year_str}년 등록: {krx_cnt}건 (정상 13~16건)\n"
                      f"db_collector/_config.py `_KR_MARKET_HOLIDAYS` frozenset 갱신 (단일 소스)\n"
                      f"https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp")
            )

        # 5/9 추가: derived 컬럼 / 별도 테이블 stale 감지 (학습 #28 후속)
        # daily_snapshot row count 만으로는 컬럼/테이블 침묵 영구 0 미감지
        sanity_warnings = []
        try:
            import sqlite3 as _s
            db_path = f"{_DATA_DIR}/stock.db"
            with _s.connect(db_path, timeout=10) as conn:
                conn.execute("PRAGMA cache_size = -65536;")
                conn.execute("PRAGMA temp_store = MEMORY;")
                conn.execute("PRAGMA mmap_size = 268435456;")
                conn.execute("PRAGMA busy_timeout = 30000;")
                # 최신 영업일 종목 총카운트 (mscore/fscore 비율 기준)
                total = conn.execute(
                    "SELECT COUNT(*) FROM daily_snapshot "
                    "WHERE trade_date=(SELECT MAX(trade_date) FROM daily_snapshot)"
                ).fetchone()[0]
                # mscore non-null count (Phase 4 alpha)
                # 비율 기반: 데이터 미수집 (m=0) 시 silent skip,
                # 부분 채워진 (0 < m < 30%) 경우만 경고. critic 5/10 권장.
                m = conn.execute(
                    "SELECT COUNT(*) FROM daily_snapshot "
                    "WHERE trade_date=(SELECT MAX(trade_date) FROM daily_snapshot) "
                    "AND mscore IS NOT NULL"
                ).fetchone()[0]
                if total > 0 and 0 < m < total * 0.3:
                    sanity_warnings.append(
                        f"⚠️ mscore 신선도 낮음: {m}/{total} (30% 임계 미달)"
                    )
                # fscore non-null count — 비율 기반 (20% 임계)
                # 자연 한계: DART 재무제표 있는 종목만 27% (마이크로/우선주/SPAC 제외)
                # 5/10 사용자 알림 후 50% → 20% 조정 (false alarm 방지)
                f = conn.execute(
                    "SELECT COUNT(*) FROM daily_snapshot "
                    "WHERE trade_date=(SELECT MAX(trade_date) FROM daily_snapshot) "
                    "AND fscore IS NOT NULL"
                ).fetchone()[0]
                if total > 0 and 0 < f < total * 0.2:
                    sanity_warnings.append(
                        f"⚠️ fscore 신선도 낮음: {f}/{total} (20% 임계 미달)"
                    )
                # wi_5pct_changes 14일 이내 (분기 보고이므로 여유)
                wi = conn.execute(
                    "SELECT julianday('now') - julianday(MAX(report_date)) "
                    "FROM wi_5pct_changes"
                ).fetchone()[0]
                if wi and wi > 14:
                    sanity_warnings.append(f"⚠️ wi_5pct_changes {wi:.0f}일 stale (기대 <14일)")
                # pension_flow_daily 7일 이내 (평일 매일 갱신)
                pf = conn.execute(
                    "SELECT julianday('now') - julianday(MAX(trade_date)) "
                    "FROM pension_flow_daily"
                ).fetchone()[0]
                if pf and pf > 7:
                    sanity_warnings.append(f"⚠️ pension_flow_daily {pf:.0f}일 stale (기대 <7일)")
                # dart_5pct_changes 7일 이내 (정상이면 매일 갱신)
                dart5 = conn.execute(
                    "SELECT julianday('now') - julianday(MAX(rcept_dt)) "
                    "FROM dart_5pct_changes WHERE rcept_dt IS NOT NULL"
                ).fetchone()[0]
                if dart5 and dart5 > 7:
                    sanity_warnings.append(f"⚠️ dart_5pct_changes {dart5:.0f}일 stale")
        except Exception as e:
            sanity_warnings.append(f"sanity 확장 검증 오류: {e}")

        if sanity_warnings:
            warn_msg = "🔍 *데이터 품질 경고*\n\n" + "\n".join(sanity_warnings)
            await _safe_send(context, warn_msg)
    except Exception as e:
        print(f"[weekly_sanity] 실패: {e}")


async def weekly_log_rotate(context):
    """매주 일요일 23:30 KST - /tmp/stock-bot.log 트림 (100MB 초과 시 마지막 10MB).

    학습 #?? (5/9): mac /tmp 는 RAM-backed (APFS), 무한 성장 시 launchd
    stdout 드롭 + working set eviction. launchd plist StandardOutPath
    직접 쏟음 → 자동 트림 필요.

    inode 보존 (POSIX append FD 호환): launchd 가 시작 시 O_APPEND 로 연
    FD 를 보유함. `mv tmp file` 패턴은 path 가 새 inode 를 가리키게 만들지만
    launchd 의 기존 FD 는 unlinked old inode 에 계속 write → 트림 효과 무효화.
    `cat tmp > file` 은 file 의 기존 내용을 truncate 후 새 내용 write 하여
    inode 를 유지함 → launchd FD valid, 다음 append write 가 truncated file
    끝에 정상 추가됨.
    """
    import os as _os
    import subprocess as _sp
    log_path = "/tmp/stock-bot.log"
    try:
        size = _os.path.getsize(log_path)
        if size > 100 * 1024 * 1024:
            _sp.run(
                f"tail -c 10485760 {log_path} > {log_path}.tmp && cat {log_path}.tmp > {log_path} && rm {log_path}.tmp",
                shell=True, check=True
            )
            print(f"[log_rotate] {size/1e6:.1f}MB -> 10MB 트림 (inode 보존)", flush=True)
    except FileNotFoundError:
        # 로그 파일 부재 (개발/테스트 환경)
        pass
    except Exception as e:
        print(f"[log_rotate] 오류: {e}", flush=True)


async def daily_us_rating_scan(context):
    """매일 KST 07:30 (UTC 22:30) — 감시+보유 미국 종목 애널 레이팅 수집 + 텔레그램 요약.
    60종목 × 2초 ≈ 2분 예상.
    """
    try:
        from kis_api import (_stockanalysis_ratings, _save_us_ratings_to_db,
                              _save_consensus_snapshot, load_us_watchlist,
                              PORTFOLIO_FILE, load_json, _load_us_holdings_sent)
        tickers = set()
        for t in load_us_watchlist().keys():
            tickers.add(t.upper())
        portfolio = load_json(PORTFOLIO_FILE, {})
        for t in portfolio.get("us_stocks", {}).keys():
            tickers.add(t.upper())
        if not tickers:
            print("[us_ratings] 대상 종목 없음")
            return
        print(f"[us_ratings] 일일 스캔 시작 ({len(tickers)}종목)")
        inserted = 0
        failed = []
        for ticker in sorted(tickers):
            try:
                result = await _stockanalysis_ratings(ticker)
                if result:
                    async with db_write_lock:
                        inserted += _save_us_ratings_to_db(result)
                        _save_consensus_snapshot(result)
                else:
                    failed.append(ticker)
            except Exception as e:
                print(f"[us_ratings] {ticker} 실패: {e}")
                failed.append(ticker)
            await asyncio.sleep(2.0)
        print(f"[us_ratings] 완료: 신규 {inserted}건, 실패 {len(failed)}종목")

        # ━━━━━━ 신규: 텔레그램 요약 발송 ━━━━━━
        try:
            urgent_sent = _load_us_holdings_sent()
            urgent_sent_tickers = {k.split("_")[0] for k in urgent_sent.keys()}
            msg = _format_daily_rating_summary(
                tickers=sorted(tickers),
                inserted=inserted,
                failed=failed,
                urgent_sent_tickers=urgent_sent_tickers,
            )
            if msg:
                await _safe_send(context, msg)
        except Exception as e:
            print(f"[us_ratings] 텔레그램 요약 전송 실패: {e}")

    except Exception as e:
        print(f"[us_ratings] 스캔 전체 실패: {e}")


async def weekly_us_ratings_universe_scan(context):
    """매주 일요일 03:00 KST — S&P 500 ∪ Russell 1000 전체 유니버스 레이팅 수집 (애널 풀 축적용).
    ~1000종목 × 2초 ≈ 33분 예상. 진행 50종목마다 로그.
    알림은 완료 요약 1건만 (개별 이벤트 알림 없음).
    """
    import time as _time
    try:
        from kis_api import (
            _stockanalysis_ratings, _save_us_ratings_to_db, _save_consensus_snapshot,
            load_sp500_tickers, load_russell1000_tickers, load_us_scan_universe,
        )
        tickers = load_us_scan_universe()
        if not tickers:
            print("[weekly_harvest] US 유니버스 로드 실패 — 스캔 건너뜀")
            return
        sp500_n = len(load_sp500_tickers())
        russell_n = len(load_russell1000_tickers())
        total = len(tickers)
        print(f"[weekly_harvest] 시작 — {total}종목")
        start_ts = _time.monotonic()
        inserted_total = 0
        failed_count = 0
        for idx, ticker in enumerate(sorted(tickers), start=1):
            try:
                result = await _stockanalysis_ratings(ticker)
                if result:
                    async with db_write_lock:
                        new_n = _save_us_ratings_to_db(result)
                        try:
                            _save_consensus_snapshot(result)
                        except Exception:
                            pass
                    inserted_total += new_n
                    if idx % 50 == 0 or idx == total:
                        print(f"[weekly_harvest] {idx}/{total} — {ticker} {new_n}건 신규 (누적 {inserted_total})")
                else:
                    failed_count += 1
                    if idx % 50 == 0 or idx == total:
                        print(f"[weekly_harvest] {idx}/{total} — {ticker} 응답 없음 (누적 실패 {failed_count})")
            except Exception as e:
                failed_count += 1
                print(f"[weekly_harvest] {ticker} 실패: {type(e).__name__}: {e}")
            await asyncio.sleep(2.0)
        elapsed_min = (_time.monotonic() - start_ts) / 60
        print(f"[weekly_harvest] 완료: {total}종목, 신규 {inserted_total}건, 실패 {failed_count}, {elapsed_min:.1f}분")

        # 완료 알림 (1건만)
        try:
            msg = (
                "📊 주간 US 레이팅 수집 완료\n"
                f"• 스캔: {total:,}종목 (S&P500 {sp500_n} ∪ Russell1000 {russell_n})\n"
                f"• 신규 레이팅: {inserted_total}건\n"
                f"• 실패: {failed_count}종목\n"
                f"• 소요: {elapsed_min:.1f}분"
            )
            await context.bot.send_message(chat_id=CHAT_ID, text=msg)
        except Exception as e:
            print(f"[weekly_harvest] 완료 알림 실패: {e}")
    except Exception as e:
        print(f"[weekly_harvest] 전체 실패: {type(e).__name__}: {e}")


async def weekly_us_analyst_sync(context):
    """주간 US 애널 마스터 자동 동기화 (일요일 04:00 KST, harvest 끝난 직후).

    us_analyst_ratings 1,902명 → us_analysts 마스터 자동 인구 + 별점 4.5+ 콜 5+ 자동 watched=1.
    discovery 시그널 풀 확장이 목적.
    """
    try:
        from db_collector import sync_us_analyst_master
        async with db_write_lock:
            result = await asyncio.to_thread(sync_us_analyst_master)
        msg = (
            "🔄 US 애널 마스터 동기화 완료\n"
            f"• 신규 애널: {result['inserted']}명\n"
            f"• 자동 watched=1 (Tier A): {result['auto_watched_a']}명\n"
            f"• Tier S 엘리트: {result['tier_s_count']}명\n"
            f"• 마스터 총: {result['total_master']}명 / watched: {result['total_watched']}명\n"
            f"• 기준: {result['criteria']}"
        )
        print(f"[us_analyst_sync] {result}")
        await context.bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        print(f"[us_analyst_sync] 실패: {type(e).__name__}: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 미국 애널 레이팅 — 실시간 감시 (2단계)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_US_SELL_RATINGS = frozenset({"Sell", "Strong Sell"})
_US_DOWNGRADE_PT_THRESHOLD = -15.0  # 타겟 15% 이상 하향 = 다운그레이드 간주


def _detect_new_downgrades(ticker: str, events_48h: list) -> list:
    """48h 이내 이벤트 중 다운그레이드 감지.
    조건 (OR):
      A) action == "Downgrades"
      B) rating_new ∈ _US_SELL_RATINGS 이고 rating_old ∉ _US_SELL_RATINGS
      C) pt_change_pct < _US_DOWNGRADE_PT_THRESHOLD (-15%)
    events_48h: list of dict with keys date, firm, action, rating_new, rating_old, pt_now, pt_old, pt_change_pct.
    반환: 다운그레이드 해당 이벤트 dict list.
    """
    out = []
    for e in events_48h:
        action = (e.get("action") or "").lower()
        new_r = e.get("rating_new") or ""
        old_r = e.get("rating_old") or ""
        pt_chg = e.get("pt_change_pct")
        if action == "downgrades":
            out.append(e)
            continue
        if new_r in _US_SELL_RATINGS and old_r not in _US_SELL_RATINGS:
            out.append(e)
            continue
        if pt_chg is not None and pt_chg < _US_DOWNGRADE_PT_THRESHOLD:
            out.append(e)
            continue
    return out


async def hourly_us_holdings_check(context):
    """보유 미국 종목 다운그레이드 실시간 감시. ET 12:00 / 16:30 두 번 실행.
    발송 조건 (AND):
      - 보유 종목 (portfolio.us_stocks)
      - 최근 48h 신규 이벤트 2건 이상
      - 그 중 다운그레이드 1건 이상
    중복 방지: us_holdings_sent.json 키 'TICKER_YYYY-MM-DD' 로 하루 1회만.
    """
    try:
        from kis_api import (
            _stockanalysis_ratings, _save_us_ratings_to_db, _save_consensus_snapshot,
            _load_us_holdings_sent, _save_us_holdings_sent,
            PORTFOLIO_FILE, load_json
        )
        from db_collector import _get_db

        portfolio = load_json(PORTFOLIO_FILE, {})
        tickers = sorted({t.upper() for t in portfolio.get("us_stocks", {}).keys()})
        if not tickers:
            print("[us_holdings] 보유 미국 종목 없음")
            return

        # 1. 신규 데이터 fetch (incremental)
        print(f"[us_holdings] 보유 {len(tickers)}종목 감시 시작")
        for ticker in tickers:
            try:
                result = await _stockanalysis_ratings(ticker)
                if result:
                    async with db_write_lock:
                        _save_us_ratings_to_db(result)
                        _save_consensus_snapshot(result)
            except Exception as e:
                print(f"[us_holdings] {ticker} fetch 실패: {e}")
            await asyncio.sleep(2.0)

        # 2. 다운그레이드 감지 + 알림
        sent = _load_us_holdings_sent()
        conn = _get_db()
        # ET 기준 날짜로 중복키 — 12:00/16:30 ET 이 KST 기준 날짜 경계 넘어도 같은 키
        today_str = datetime.now(ET).strftime("%Y-%m-%d")
        try:
            for ticker in tickers:
                sent_key = f"{ticker}_{today_str}"
                if sent_key in sent:
                    continue  # 오늘 이미 발송
                rows = conn.execute(
                    "SELECT r.rating_date, r.rating_time, r.firm, r.analyst, r.action, "
                    "       r.rating_new, r.rating_old, r.pt_now, r.pt_old, r.pt_change_pct, "
                    "       COALESCE(a.stars, r.stars) AS stars, "
                    "       COALESCE(a.watched, 0) AS watched, "
                    "       COALESCE(a.success_rate, r.success_rate) AS sr, "
                    "       COALESCE(a.total_ratings, r.total_ratings) AS calls, "
                    "       COALESCE(a.avg_return, r.avg_return) AS ret "
                    "FROM us_analyst_ratings r "
                    "LEFT JOIN us_analysts a ON r.analyst_slug = a.slug "
                    "WHERE r.ticker=? "
                    "  AND r.rating_date >= date('now', '-2 days') "
                    "ORDER BY r.rating_date DESC, r.rating_time DESC",
                    (ticker,)
                ).fetchall()
                if len(rows) < 2:
                    continue  # 48h 내 신규 2건 미만
                from db_collector import is_tier_s_analyst
                events = [
                    {"date": r[0], "time": r[1], "firm": r[2], "analyst": r[3],
                     "action": r[4], "rating_new": r[5], "rating_old": r[6],
                     "pt_now": r[7], "pt_old": r[8], "pt_change_pct": r[9],
                     "stars": r[10], "watched": bool(r[11]),
                     "tier_s": is_tier_s_analyst(r[10], r[12], r[13], r[14])}
                    for r in rows
                ]
                downgrades = _detect_new_downgrades(ticker, events)
                if not downgrades:
                    continue
                # 조건 충족 → 긴급 알림
                msg = _format_urgent_downgrade_alert(ticker, events, downgrades)
                try:
                    await _safe_send(context, msg)
                    sent[sent_key] = {
                        "sent_at": datetime.now().isoformat(),
                        "events_count": len(events),
                        "downgrades": [f"{d.get('firm')} {d.get('rating_old')}→{d.get('rating_new')}" for d in downgrades],
                    }
                    print(f"[us_holdings] 🚨 {ticker} 긴급 발송 ({len(downgrades)} downgrades)")
                except Exception as e:
                    print(f"[us_holdings] {ticker} 텔레그램 발송 실패: {e}")
        finally:
            conn.close()
        _save_us_holdings_sent(sent)
    except Exception as e:
        print(f"[us_holdings] 감시 전체 실패: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 주간 미국 애널 리포트 (일요일 19:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def weekly_us_analyst_report(context):
    """매주 일요일 19:00 KST — 주간 미국 애널 활동 요약.
    내용:
    1. 톱 애널 (watched=1) 이번주 활동
    2. Discovery TOP 10 (감시 밖 + 상향 집중 종목)
    3. 보유/감시 종목 컨센서스 변화 요약
    """
    try:
        from kis_api import load_us_watchlist, PORTFOLIO_FILE, load_json
        from db_collector import _get_db
        conn = _get_db()
        try:
            today_kst = datetime.now(KST)
            week_label = f"{(today_kst - timedelta(days=6)).strftime('%m/%d')}~{today_kst.strftime('%m/%d')}"

            lines = [f"📊 *Weekly Analyst Digest* ({week_label})", ""]

            # 1. 톱 애널 활동 (최근 7일)
            top_activity = conn.execute(
                "SELECT a.name, a.firm, "
                "       SUM(CASE WHEN r.action='Upgrades' THEN 1 ELSE 0 END) AS up_n, "
                "       SUM(CASE WHEN r.action='Downgrades' THEN 1 ELSE 0 END) AS down_n, "
                "       COUNT(*) AS total "
                "FROM us_analysts a "
                "LEFT JOIN us_analyst_ratings r ON a.slug = r.analyst_slug "
                "  AND r.rating_date >= date('now', '-7 days') "
                "WHERE a.watched = 1 "
                "GROUP BY a.slug "
                "HAVING total > 0 "
                "ORDER BY total DESC LIMIT 10"
            ).fetchall()
            if top_activity:
                lines.append("━━ *톱 애널 활동* ━━")
                for name, firm, up_n, down_n, total in top_activity[:10]:
                    lines.append(f"- {_md_escape(name)} ({_md_escape(firm)}): ↑{up_n} ↓{down_n} (총 {total})")
                lines.append("")
            else:
                # watched=1 없음 or 활동 없음
                top_count = conn.execute("SELECT COUNT(*) FROM us_analysts WHERE watched=1").fetchone()[0]
                if top_count == 0:
                    lines.append("_톱 애널 확정 없음 — `watch_analyst` 로 후보 확정 필요_")
                    lines.append("")

            # 2. Discovery TOP 10
            excluded = set()
            for t in load_us_watchlist().keys():
                excluded.add(t.upper())
            for t in load_json(PORTFOLIO_FILE, {}).get("us_stocks", {}).keys():
                excluded.add(t.upper())

            discovery_rows = conn.execute(
                "SELECT r.ticker, COUNT(*) AS n_up, AVG(r.pt_now) AS avg_target "
                "FROM us_analyst_ratings r "
                "JOIN us_analysts a ON r.analyst_slug = a.slug "
                "WHERE a.watched = 1 AND r.action = 'Upgrades' "
                "  AND r.rating_date >= date('now', '-7 days') "
                "GROUP BY r.ticker HAVING n_up >= 2 "
                "ORDER BY n_up DESC LIMIT 15"
            ).fetchall()
            discovery_filtered = [r for r in discovery_rows if r[0] not in excluded][:10]
            if discovery_filtered:
                lines.append("━━ *🚀 Discovery (감시 밖 신규)* ━━")
                for t, n, target in discovery_filtered:
                    target_s = f"${target:.0f}" if target else "—"
                    lines.append(f"- *{_md_escape(t)}*: {n}건 상향, avg {target_s}")
                lines.append("")

            # 3. 보유/감시 종목 컨센 변화 (최근 7일 이벤트 요약)
            tickers_union = sorted(excluded)
            if tickers_union:
                placeholders = ",".join("?" * len(tickers_union))
                portfolio_rows = conn.execute(
                    f"SELECT r.ticker, "
                    f"       SUM(CASE WHEN r.action='Upgrades' THEN 1 ELSE 0 END) AS up_n, "
                    f"       SUM(CASE WHEN r.action='Downgrades' THEN 1 ELSE 0 END) AS down_n, "
                    f"       COUNT(*) AS total "
                    f"FROM us_analyst_ratings r "
                    f"WHERE r.ticker IN ({placeholders}) "
                    f"  AND r.rating_date >= date('now', '-7 days') "
                    f"GROUP BY r.ticker HAVING total > 0 "
                    f"ORDER BY (up_n - down_n) DESC, total DESC",
                    tickers_union
                ).fetchall()
                if portfolio_rows:
                    lines.append("━━ *💼 내 종목 이번주 이벤트* ━━")
                    for t, up_n, down_n, total in portfolio_rows[:15]:
                        if up_n > 0 and down_n == 0:
                            lines.append(f"- {_md_escape(t)}: ↑{up_n}건")
                        elif down_n > 0 and up_n == 0:
                            lines.append(f"- {_md_escape(t)}: ↓{down_n}건 ⚠️")
                        else:
                            lines.append(f"- {_md_escape(t)}: ↑{up_n} ↓{down_n}")
                    lines.append("")

            # 이벤트 전무
            if len(lines) <= 3:
                lines.append("_이번주 활동 없음_")

            msg = "\n".join(lines)
            if len(msg) > 4000:
                msg = msg[:3900] + "\n\n_... 4000자 제한으로 일부 생략_"

            try:
                await _safe_send(context, msg)
                print(f"[weekly_us_report] 발송 완료 ({len(msg)}자)")
            except Exception as e:
                print(f"[weekly_us_report] 텔레그램 발송 실패: {e}")

        finally:
            conn.close()
    except Exception as e:
        print(f"[weekly_us_report] 전체 실패: {e}")


def _md_escape(s) -> str:
    """텔레그램 Markdown V1 특수문자 이스케이프 (_ * [ `). None → —."""
    if not s:
        return "—"
    s = str(s)
    for c in ("\\", "_", "*", "[", "`"):
        s = s.replace(c, "\\" + c)
    return s


def _rating_elapsed(rdate: str) -> str:
    """rating_date → ' (YYYY-MM-DD, N일 전)'. 날짜 없으면 ''."""
    if not rdate:
        return ""
    try:
        d = datetime.strptime(rdate[:10], "%Y-%m-%d").date()
        days = (datetime.now(KST).date() - d).days
        return f" ({rdate[:10]}, {days}일 전)"
    except Exception:
        return ""


def _format_urgent_downgrade_alert(ticker: str, all_events: list, downgrades: list) -> str:
    """긴급 다운그레이드 메시지 포맷. 4096자 미만.
    Tier S(엘리트) → Tier A(톱) → 일반 3단계 차등 강조 + 별점 표시.
    """
    tier_s_dgs = [d for d in downgrades if d.get("tier_s")]
    tier_a_dgs = [d for d in downgrades if d.get("watched") and not d.get("tier_s")]
    other_dgs = [d for d in downgrades if not d.get("watched")]

    # 헤더: Tier S 우선 (가장 강한 시그널)
    if len(tier_s_dgs) >= 2:
        header = f"🚨🚨🚨 *{_md_escape(ticker)}* 엘리트 애널 {len(tier_s_dgs)}명 동시 다운"
    elif len(tier_s_dgs) == 1 and len(tier_a_dgs) >= 1:
        header = f"🚨🚨 *{_md_escape(ticker)}* 엘리트+톱 다운그레이드"
    elif len(tier_s_dgs) == 1:
        header = f"🚨🚨 *{_md_escape(ticker)}* 엘리트 애널 다운그레이드"
    elif len(tier_a_dgs) >= 2:
        header = f"🚨 *{_md_escape(ticker)}* 톱 애널 {len(tier_a_dgs)}명 동시 다운"
    elif len(tier_a_dgs) == 1:
        header = f"🚨 *{_md_escape(ticker)}* 톱 애널 다운그레이드"
    else:
        header = f"⚠️ *{_md_escape(ticker)}* 다운그레이드 경고 (일반)"

    lines = [header, ""]
    lines.append(f"최근 48h: *{len(all_events)}건* 이벤트, *{len(downgrades)}건* 다운")
    if tier_s_dgs:
        lines.append(f"  └ 🥇 엘리트 (Tier S): *{len(tier_s_dgs)}명*")
    if tier_a_dgs:
        lines.append(f"  └ 🥈 톱 (Tier A): *{len(tier_a_dgs)}명*")
    lines.append("")

    def _fmt_dg(d):
        firm = _md_escape(d.get("firm"))
        old_r = _md_escape(d.get("rating_old") or "—")
        new_r = _md_escape(d.get("rating_new") or "—")
        pt_now = d.get("pt_now")
        pt_chg = d.get("pt_change_pct")
        pt_str = f"${pt_now:.0f}" if pt_now else "—"
        chg_str = f" ({pt_chg:+.1f}%)" if pt_chg is not None else ""
        elapsed_str = _rating_elapsed(d.get("date", ""))
        stars = d.get("stars")
        star_str = f" ⭐{stars:.1f}" if stars is not None else ""
        return f"- {firm}{star_str}: {old_r}→{new_r} {pt_str}{chg_str}{elapsed_str}"

    if tier_s_dgs:
        lines.append("*🥇 엘리트 다운그레이드:*")
        for d in tier_s_dgs[:5]:
            lines.append(_fmt_dg(d))
        if len(tier_s_dgs) > 5:
            lines.append(f"... +{len(tier_s_dgs) - 5}건 더")
        lines.append("")

    if tier_a_dgs:
        lines.append("*🥈 톱 다운그레이드:*")
        for d in tier_a_dgs[:5]:
            lines.append(_fmt_dg(d))
        if len(tier_a_dgs) > 5:
            lines.append(f"... +{len(tier_a_dgs) - 5}건 더")
        lines.append("")

    if other_dgs:
        lines.append(f"*일반 다운그레이드:* {len(other_dgs)}건")
        for d in other_dgs[:2]:
            lines.append(_fmt_dg(d))
        if len(other_dgs) > 2:
            lines.append(f"... +{len(other_dgs) - 2}건 더")

    # 비중 조정 권고 (강도 차등)
    if len(tier_s_dgs) >= 2:
        lines.append("")
        lines.append("→ *⚠️ 즉시 비중 축소 검토 (엘리트 동시)*")
    elif len(tier_s_dgs) >= 1 or len(tier_a_dgs) >= 2:
        lines.append("")
        lines.append("→ *비중 축소 검토 권장*")

    return "\n".join(lines)


def _format_daily_rating_summary(tickers: list, inserted: int, failed: list,
                                  urgent_sent_tickers: set) -> str:
    """일일 스캔 텔레그램 요약. 긴급 이미 발송된 종목은 '이미 알림' 마크.
    축약: 내 종목 10개 초과 시 '... N more'.
    """
    from db_collector import _get_db
    conn = _get_db()
    kst_now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    try:
        lines = [f"📊 *미국 애널 스캔* ({kst_now})", ""]

        # 내 종목 섹션 (최근 4일 이벤트, rating_date 기준)
        # us_analysts JOIN — 별점/적중률/콜수/평균수익률 모두 가져옴 → Tier S 판정
        from db_collector import is_tier_s_analyst
        my_section = []
        downgrade_section = []         # 일반 다운그레이드
        tier_a_downgrade_section = []  # Tier A 다운그레이드
        tier_s_downgrade_section = []  # Tier S 엘리트 다운그레이드 (최강조)
        for ticker in tickers:
            rows = conn.execute(
                "SELECT r.firm, r.action, r.rating_new, r.rating_old, "
                "       r.pt_now, r.pt_change_pct, r.rating_date, "
                "       COALESCE(a.stars, r.stars) AS stars, "
                "       COALESCE(a.watched, 0) AS watched, "
                "       COALESCE(a.success_rate, r.success_rate) AS sr, "
                "       COALESCE(a.total_ratings, r.total_ratings) AS calls, "
                "       COALESCE(a.avg_return, r.avg_return) AS ret "
                "FROM us_analyst_ratings r "
                "LEFT JOIN us_analysts a ON r.analyst_slug = a.slug "
                "WHERE r.ticker=? "
                "  AND r.rating_date >= date('now', '-4 days') "
                "ORDER BY r.rating_date DESC, r.rating_time DESC",
                (ticker,)
            ).fetchall()
            # Hold→Hold 무변화 제외 (Maintains/Reiterates + target 미변동)
            rows = [r for r in rows
                    if not ((r[1] or "").lower() in ("maintains", "reiterates") and not r[5])]
            if not rows:
                continue
            already_sent = "⚠️ 이미 알림" if ticker in urgent_sent_tickers else ""
            # 다운그레이드 분리 (Tier S / Tier A / 일반)
            dgs = [r for r in rows if (r[1] or "").lower() == "downgrades"]
            tier_s_dgs = [r for r in dgs if is_tier_s_analyst(r[7], r[9], r[10], r[11])]
            tier_a_dgs = [r for r in dgs if r[8] and not is_tier_s_analyst(r[7], r[9], r[10], r[11])]
            other_dgs = [r for r in dgs if not r[8]]

            def _fmt_row(r, prefix=""):
                firm, act, new_r, old_r, pt, pt_chg, rdate, stars = r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]
                pt_str = f"${pt:.0f}" if pt else "—"
                star_str = f" ⭐{stars:.1f}" if stars is not None else ""
                return f"{prefix}*{_md_escape(ticker)}*: {_md_escape(firm)}{star_str} {_md_escape(new_r)} {pt_str}{_rating_elapsed(rdate)} {already_sent}"

            if tier_s_dgs:
                for r in tier_s_dgs[:2]:
                    tier_s_downgrade_section.append(_fmt_row(r, "- 🥇 "))
                if len(tier_s_dgs) >= 2:
                    tier_s_downgrade_section.append(f"  ⚠️⚠️ {_md_escape(ticker)} 엘리트 {len(tier_s_dgs)}명 동시 다운 → 즉시 비중 축소")

            if tier_a_dgs and not tier_s_dgs:
                for r in tier_a_dgs[:2]:
                    tier_a_downgrade_section.append(_fmt_row(r, "- 🥈 "))
                if len(tier_a_dgs) >= 2:
                    tier_a_downgrade_section.append(f"  ⚠️ {_md_escape(ticker)} 톱 {len(tier_a_dgs)}명 동시 다운 → 비중 축소 검토")

            if other_dgs and not tier_s_dgs and not tier_a_dgs:
                # 엘리트/톱 다운 없을 때만 일반 표시
                for r in other_dgs[:2]:
                    downgrade_section.append(_fmt_row(r, "- "))

            if not dgs:
                # 상향/유지 표시 (날짜 + 별점 + Tier 마크)
                def _firm_str(r):
                    firm = _md_escape(r[0])
                    stars = r[7]
                    is_s = is_tier_s_analyst(r[7], r[9], r[10], r[11])
                    tier_mark = "🥇" if is_s else ("🥈" if r[8] else "")
                    star_mark = f"⭐{stars:.1f}" if stars is not None else ""
                    pt_str = f" ${r[4]:.0f}" if r[4] else ""
                    return f"{tier_mark}{firm}{star_mark}{pt_str}{_rating_elapsed(r[6])}"
                firms = ", ".join(_firm_str(r) for r in rows[:2])
                my_section.append(f"- {_md_escape(ticker)}: {len(rows)}건 ({firms}) {already_sent}")

        orig_my_count = len(my_section)  # 축약 전 원본 카운트 (폴백 메시지용)

        # 1. Tier S 엘리트 다운그레이드 (최우선, 최강 시그널)
        if tier_s_downgrade_section:
            lines.append("━━ 🥇 *엘리트 다운그레이드 (Tier S)* ━━")
            lines.extend(tier_s_downgrade_section[:10])
            if len(tier_s_downgrade_section) > 10:
                lines.append(f"... +{len(tier_s_downgrade_section) - 10}건 더")
            lines.append("")

        # 2. Tier A 톱 다운그레이드
        if tier_a_downgrade_section:
            lines.append("━━ 🥈 *톱 다운그레이드 (Tier A)* ━━")
            lines.extend(tier_a_downgrade_section[:10])
            if len(tier_a_downgrade_section) > 10:
                lines.append(f"... +{len(tier_a_downgrade_section) - 10}건 더")
            lines.append("")

        if my_section:
            # 축약 전략: 10개 초과면 잘라내기
            if len(my_section) > 10:
                cut = my_section[:10]
                cut.append(f"... +{len(my_section) - 10}종목 더")
                my_section = cut
            lines.append("━━ *내 종목* ━━")
            lines.extend(my_section)
            lines.append("")

        # 3. 일반 다운그레이드 (엘리트/톱 없을 때만 표시)
        if downgrade_section:
            lines.append("━━ *다운그레이드 (일반)* ━━")
            lines.extend(downgrade_section[:10])
            if len(downgrade_section) > 10:
                lines.append(f"... +{len(downgrade_section) - 10}건 더")
            lines.append("")

        # 통계
        lines.append("━━ *통계* ━━")
        lines.append(f"스캔 {len(tickers)}종목 / 신규 이벤트 {inserted}건 / 실패 {len(failed)}")

        msg = "\n".join(lines)
        # 4096자 체크 (안전 마진)
        if len(msg) > 4000:
            # 압축 — 내 종목 상세 생략, Tier S/A 보존
            lines = [f"📊 *미국 애널 스캔* ({kst_now})", ""]
            if tier_s_downgrade_section:
                lines.append("━━ 🥇 *엘리트 다운그레이드* ━━")
                lines.extend(tier_s_downgrade_section[:5])
                lines.append("")
            if tier_a_downgrade_section:
                lines.append("━━ 🥈 *톱 다운그레이드* ━━")
                lines.extend(tier_a_downgrade_section[:5])
                lines.append("")
            if downgrade_section:
                lines.append("━━ *다운그레이드 (일반)* ━━")
                lines.extend(downgrade_section[:3])
                lines.append("")
            lines.append(f"내 종목 이벤트: {orig_my_count}종목 (상세 생략)")
            lines.append(f"스캔 {len(tickers)}종목 / 신규 {inserted}건 / 실패 {len(failed)}")
            msg = "\n".join(lines)
        any_section = (my_section or downgrade_section or tier_a_downgrade_section or tier_s_downgrade_section)
        return msg if any_section else ""  # 이벤트 없으면 빈 문자열 → 발송 안 함
    finally:
        conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 봇 시작
