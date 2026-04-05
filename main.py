import os
import json
import asyncio
from datetime import datetime, timedelta, time as dtime
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from aiohttp import web

from kis_api import *
from kis_api import (
    _is_us_ticker, _is_us_market_hours_kst, _is_us_market_closed, _guess_excd,
    ws_manager, get_ws_tickers, close_session,
    fetch_us_earnings_calendar, fetch_us_sector_etf,
)
from krx_crawler import KRX_DB_DIR, _cleanup_old_db


async def _refresh_ws():
    """WebSocket 구독 목록 갱신 헬퍼"""
    try:
        await ws_manager.update_tickers(get_ws_tickers())
    except Exception as e:
        print(f"[WS] refresh 오류: {e}")
from mcp_tools import mcp_sse_handler, mcp_messages_handler

try:
    from report_crawler import collect_reports, get_collection_tickers, load_reports
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reply Keyboard 버튼 레이아웃
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📊 포트폴리오", "🚨 알림현황"],
        ["📈 매크로", "🔍 워치리스트"],
        ["📰 리포트", "📋 전체현황"],
    ],
    resize_keyboard=True,
)


def _is_kr_trading_time(now=None):
    """평일 08:00~18:00 KST 여부"""
    if now is None:
        now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    if not (8 <= now.hour < 18):
        return False
    return True


# ── 섹터 분류 (한국 포트 비중 경고용) ──
_KR_SECTORS = {
    "조선":   {"009540"},
    "전력기기": {"298040", "010120", "267260"},
}
_SECTOR_LIMIT = 50   # 섹터 한도 %
_STOCK_LIMIT  = 35   # 단일종목 한도 %


def _extract_grade(entry: dict, ticker: str, name: str) -> str | None:
    """decision_log entry에서 종목의 확신등급 추출"""
    grades = entry.get("grades", {})
    for key in [ticker, name]:
        gv = grades.get(key)
        if gv is None:
            continue
        if isinstance(gv, str):
            return gv
        elif isinstance(gv, dict):
            return gv.get("grade")
    return None


def _grade_arrow(prev: str, cur: str) -> str:
    """등급 변동 화살표 문자열. 변동 없거나 null이면 ''"""
    if not prev or not cur or prev == cur:
        return ""
    order = {"S": -1, "A": 0, "B": 1, "C": 2, "D": 3}
    if order.get(cur, 9) < order.get(prev, 9):
        return f" ⬆️{prev}→{cur}"
    return f" ⬇️{prev}→{cur}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 1: 한국 장 마감 요약 (15:40)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def daily_kr_summary(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    try:
        token = await get_kis_token()

        # ── [시장] KOSPI + 환율 ──
        macro = await get_yahoo_quote("^KS11") or {}
        kospi_p = macro.get("price", "?")
        kospi_c = macro.get("change_pct", "?")

        fx = await get_yahoo_quote("KRW=X") or {}
        krw = int(float(fx.get("price", 0) or 0))
        kospi_c_f = round(float(kospi_c or 0), 2)
        kospi_e = "🔴" if kospi_c_f < 0 else "🟢"
        msg = f"📊 *한국 장 마감* ({now.strftime('%m/%d %H:%M')})\n\n"
        msg += f"[시장] {kospi_e} KOSPI {kospi_p} ({kospi_c_f:+.2f}%) | 💱 {krw:,}원\n"

        # ── [섹터] ETF 4개 ──
        SECTOR_ETF_4 = [
            ("140710", "조선"), ("261070", "전력"),
            ("464520", "방산"), ("469150", "AI반도체"),
        ]
        sector_parts = []
        for code, label in SECTOR_ETF_4:
            try:
                d = await kis_stock_price(code, token)
                chg = float(d.get("prdy_ctrt", 0) or 0)
                sector_parts.append(f"{label}{chg:+.2f}%")
                await asyncio.sleep(0.05)
            except Exception:
                pass
        if sector_parts:
            msg += f"[섹터] {' | '.join(sector_parts)}\n"

        # ── 포트폴리오 데이터 수집 (배치 조회) ──
        portfolio = load_json(PORTFOLIO_FILE, {})
        kr_stocks = {k: v for k, v in portfolio.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)}
        stops = load_stoploss()
        kr_stops = {k: v for k, v in stops.items() if k != "us_stocks" and isinstance(v, dict)}
        port_rows = []
        total_eval = 0.0
        total_prev_eval = 0.0
        batch = await batch_stock_detail(list(kr_stocks.keys()), token, delay=0.3)
        batch_map = {r["ticker"]: r for r in batch}
        for ticker, info in kr_stocks.items():
            d = batch_map.get(ticker, {})
            if d.get("error"):
                port_rows.append({
                    "ticker": ticker, "info": info,
                    "price": 0, "chg": 0.0,
                    "frgn_qty": 0, "tgt": 0, "tgt_pct": None,
                    "error": d["error"],
                })
                continue
            try:
                price = d.get("price", 0)
                chg   = d.get("chg_pct", 0.0)
                qty   = info.get("qty", 0)
                eval_amt   = price * qty
                prev_price = price / (1 + chg / 100) if chg != -100 else price
                total_eval      += eval_amt
                total_prev_eval += prev_price * qty
                stop_info = kr_stops.get(ticker, {})
                tgt = float(stop_info.get("target_price") or stop_info.get("target") or 0)
                tgt_pct = (tgt - price) / price * 100 if tgt > 0 and price > 0 else None
                port_rows.append({
                    "ticker": ticker, "info": info,
                    "price": price, "chg": chg,
                    "frgn_qty": d.get("frgn_net", 0), "tgt": tgt, "tgt_pct": tgt_pct,
                })
            except Exception:
                pass

        # ── [포트] 오늘 변동 + 주간 수익률 ──
        today_delta = int(total_eval - total_prev_eval)
        weekly_base = load_json(WEEKLY_BASE_FILE, {})
        base_amt = float(weekly_base.get("base_amt", 0))
        week_pct = (total_eval - base_amt) / base_amt * 100 if base_amt > 0 else 0.0
        # 월요일이면 weekly_base 갱신
        if now.weekday() == 0 and total_eval > 0:
            this_monday = now.strftime("%Y-%m-%d")
            if weekly_base.get("date") != this_monday:
                save_json(WEEKLY_BASE_FILE, {"date": this_monday, "base_amt": int(total_eval)})
        today_str = f"+{today_delta:,}" if today_delta >= 0 else f"{today_delta:,}"
        msg += f"[포트] 오늘 {today_str}원 | 이번 주 {week_pct:+.1f}%\n"
        if week_pct <= -4:
            msg += f"🔴 주간 {week_pct:.1f}% — 신규매수 금지 규칙 발동!\n"
        elif week_pct <= -3:
            msg += f"⚠️ 주간 {week_pct:.1f}% — 신규매수 주의\n"

        # ── 컨센서스 목표가 수집 (캐시 우선, 7일 초과 시 실시간 폴백) ──
        consensus_map = {}
        try:
            _cc = load_consensus_cache()
            if _cc:
                _upd = _cc.get("updated", "")
                _cache_ok = False
                if _upd:
                    try:
                        _age = (datetime.now(KST) - datetime.fromisoformat(_upd)).total_seconds() / 86400
                        _cache_ok = _age < 7
                    except Exception:
                        pass
                if _cache_ok:
                    for row in port_rows:
                        _cd = _cc.get("kr", {}).get(row["ticker"])
                        if _cd and _cd.get("avg"):
                            consensus_map[row["ticker"]] = {
                                "avg": float(_cd["avg"]),
                                "buy": int(_cd.get("buy", 0)),
                            }
        except Exception:
            pass
        # 캐시 미스 종목만 실시간 조회
        loop = asyncio.get_event_loop()
        for row in port_rows:
            if row["ticker"] in consensus_map:
                continue
            try:
                c = await asyncio.wait_for(
                    loop.run_in_executor(None, fetch_fnguide_consensus, row["ticker"]),
                    timeout=5.0,
                )
                avg = c.get("consensus_target", {}).get("avg") if c else None
                if avg:
                    consensus_map[row["ticker"]] = {
                        "avg": float(avg),
                        "buy": (c.get("opinion") or {}).get("buy", 0),
                    }
                await asyncio.sleep(0.5)
            except Exception:
                pass

        # ── 확신등급 변동 수집 ──
        grade_change_map: dict = {}  # ticker → grade_arrow string
        try:
            dec_log = load_decision_log()
            dec_entries = sorted(dec_log.values(), key=lambda x: x.get("date", ""), reverse=True)
            if len(dec_entries) >= 2:
                cur_entry  = dec_entries[0]
                prev_entry = dec_entries[1]
                for row in port_rows:
                    t = row["ticker"]
                    n = row["info"].get("name", t)
                    cur_g  = _extract_grade(cur_entry,  t, n)
                    prev_g = _extract_grade(prev_entry, t, n)
                    grade_change_map[t] = _grade_arrow(prev_g, cur_g)
        except Exception:
            pass

        # ── [보유] 종목별 ──
        if port_rows:
            msg += "\n[보유]\n"
            for row in port_rows:
                name = row["info"].get("name", row["ticker"])
                if row.get("error"):
                    msg += f"{name} ({row['ticker']}) — 조회실패\n"
                    continue
                price = row["price"]
                chg = row["chg"]
                qty   = row["info"].get("qty", 0)
                eval_amt = price * qty
                w_pct = round(eval_amt / total_eval * 100) if total_eval > 0 else 0
                limit_warn = f" ⚠️>{_STOCK_LIMIT}%한도" if w_pct > _STOCK_LIMIT else ""
                grade_str = grade_change_map.get(row["ticker"], "")
                frgn_qty = row["frgn_qty"]
                fire = " 🔥" if chg >= 5 else (" ⚠️" if chg <= -3 else "")
                frgn_abs = abs(frgn_qty)
                frgn_k = frgn_abs // 1000
                frgn_disp = (f"+{frgn_k}K" if frgn_qty >= 0 else f"-{frgn_k}K") if frgn_k > 0 else f"{frgn_qty:+}"
                frgn_ok = " ✅" if frgn_qty > 0 else ""
                tgt_str = f" | 목표{row['tgt']:,.0f} {row['tgt_pct']:+.1f}%" if row["tgt_pct"] is not None else ""
                # 컨센서스 비교
                cons_str = ""
                try:
                    cdata = consensus_map.get(row["ticker"])
                    if cdata:
                        cavg = cdata["avg"]
                        buy_cnt = cdata["buy"]
                        our_tgt = row["tgt"]
                        if our_tgt > 0 and cavg > 0:
                            ratio = our_tgt / cavg
                            diff_pct = (our_tgt - cavg) / cavg * 100
                            if ratio < 0.8:
                                cons_str = f" ⚠️목표{our_tgt:,.0f} vs 컨센{cavg:,.0f} (↑{abs(diff_pct):.0f}%)"
                            elif ratio > 1.2:
                                cons_str = f" ⚠️목표{our_tgt:,.0f} vs 컨센{cavg:,.0f} (↓{abs(diff_pct):.0f}%)"
                            else:
                                cons_str = f" 📊컨센{cavg:,.0f}(매수{buy_cnt})"
                        elif cavg > 0:
                            cons_str = f" 📊컨센{cavg:,.0f}(매수{buy_cnt})"
                except Exception:
                    pass
                msg += f"{name} {price:,} ({chg:+.2f}%){fire} 비중{w_pct}%{limit_warn} | 외인{frgn_disp}{frgn_ok}{tgt_str}{cons_str}{grade_str}\n"

            # ── 섹터 비중 경고 ──
            try:
                sector_lines = []
                for sector_name, sector_tickers in _KR_SECTORS.items():
                    sector_eval = sum(
                        row["price"] * row["info"].get("qty", 0)
                        for row in port_rows
                        if row["ticker"] in sector_tickers and not row.get("error")
                    )
                    if sector_eval <= 0 or total_eval <= 0:
                        continue
                    s_pct = round(sector_eval / total_eval * 100)
                    if s_pct > _SECTOR_LIMIT:
                        sector_lines.append(f"⚠️ {sector_name} 섹터 {s_pct}% (한도{_SECTOR_LIMIT}% 초과)")
                    elif s_pct >= 30:
                        sector_lines.append(f"📊 {sector_name} 섹터 {s_pct}% (한도{_SECTOR_LIMIT}% OK)")
                if sector_lines:
                    msg += "\n".join(sector_lines) + "\n"
            except Exception:
                pass

        # ── [뉴스 감성] ──
        try:
            all_tickers = list(kr_stocks.keys())
            wl = load_watchlist()
            for t in wl:
                if t not in all_tickers:
                    all_tickers.append(t)
            neg_alerts = []
            for t in all_tickers:
                try:
                    news = await kis_news_title(t, token, n=5)
                    sa = analyze_news_sentiment(news)
                    neg_count = len(sa.get("negative", []))
                    if neg_count >= 2:
                        name = kr_stocks.get(t, {}).get("name") or wl.get(t, t)
                        top_neg = sa["negative"][0]["title"] if sa["negative"] else ""
                        neg_alerts.append(f"🔴 {name}: 부정 {neg_count}건 — {top_neg[:20]}")
                    await asyncio.sleep(0.2)
                except Exception:
                    pass
            if neg_alerts:
                msg += "\n[뉴스 감성]\n" + "\n".join(neg_alerts[:5]) + "\n"
        except Exception:
            pass

        # ── [감시 접근] gap_pct <= 5% ──
        try:
            wa = load_watchalert()
            near = []
            for wa_ticker, wa_info in wa.items():
                buy_p = float(wa_info.get("buy_price", 0) or 0)
                if buy_p <= 0:
                    continue
                try:
                    if _is_us_ticker(wa_ticker):
                        wd = await kis_us_stock_price(wa_ticker, token)
                        cur_p = float(wd.get("last", 0) or 0)
                    else:
                        wd = await kis_stock_price(wa_ticker, token)
                        cur_p = int(wd.get("stck_prpr", 0) or 0)
                    await asyncio.sleep(0.2)
                    if cur_p > 0 and (cur_p - buy_p) / buy_p * 100 <= 5:
                        near.append((wa_info.get("name", wa_ticker), cur_p, buy_p,
                                     (cur_p - buy_p) / buy_p * 100, _is_us_ticker(wa_ticker)))
                except Exception:
                    pass
            if near:
                msg += "\n[감시 접근]\n"
                for name, cur, buy, gap, is_us in near:
                    sign = "🟢" if cur <= buy else "·"
                    if is_us:
                        msg += f"{sign} {name}: ${cur:,.2f} ← 감시 ${buy:,.2f} ({gap:+.1f}%)\n"
                    else:
                        msg += f"{sign} {name}: {cur:,}원 ← 감시 {buy:,.0f}원 ({gap:+.1f}%)\n"
        except Exception:
            pass

        msg += "\n→ Claude에서 점검하세요"
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

        # ── 수급 히스토리 축적 (백테스트용) ──
        try:
            await save_supply_snapshot(token)
        except Exception:
            pass

    except Exception as e:
        print(f"daily_kr_summary 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 2: 미국 장 마감 요약
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def daily_us_summary(context: ContextTypes.DEFAULT_TYPE, force: bool = False):
    now = datetime.now(KST)
    if not force and now.weekday() == 6:
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
# 🔔 자동알림 2b: 미국 장 마감 요약 (06:05)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def us_market_summary(context: ContextTypes.DEFAULT_TYPE):
    # 미국 정규장 마감 후 30분 이내가 아니면 스킵 (DST 자동 감지)
    if not _is_us_market_closed():
        return
    now = datetime.now(KST)
    try:
        sp500 = await get_yahoo_quote("^GSPC")
        nasdaq = await get_yahoo_quote("^IXIC")
        vix = await get_yahoo_quote("^VIX")
        fx = await get_yahoo_quote("KRW=X")
        sp_p = float(sp500.get("price", 0) or 0) if sp500 else 0
        sp_c = float(sp500.get("change_pct", 0) or 0) if sp500 else 0
        nq_p = float(nasdaq.get("price", 0) or 0) if nasdaq else 0
        nq_c = float(nasdaq.get("change_pct", 0) or 0) if nasdaq else 0
        vix_p = float(vix.get("price", 0) or 0) if vix else 0
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

        portfolio = load_json(PORTFOLIO_FILE, {})
        us_port = portfolio.get("us_stocks", {})
        if us_port:
            msg += "\n💼 *미국 포트*\n"
            total_eval = total_cost = 0.0
            # 컨센서스 배치 수집
            us_stops = load_stoploss().get("us_stocks", {})
            us_consensus_map = {}
            loop_c = asyncio.get_event_loop()
            for sym in us_port:
                try:
                    c = await asyncio.wait_for(
                        loop_c.run_in_executor(None, get_us_consensus, sym),
                        timeout=5.0,
                    )
                    if c:
                        us_consensus_map[sym] = c
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
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
                    # 컨센서스 비교
                    cons_str = ""
                    try:
                        cdata = us_consensus_map.get(sym)
                        if cdata:
                            cavg = cdata["consensus_target"]["avg"]
                            stop_info = us_stops.get(sym, {})
                            our_tgt = float(stop_info.get("target_price") or 0)
                            if our_tgt > 0 and cavg > 0:
                                ratio = our_tgt / cavg
                                diff_pct = (our_tgt - cavg) / cavg * 100
                                if ratio < 0.8:
                                    cons_str = f" ⚠️목표${our_tgt:.0f} vs 컨센${cavg:.0f}(↑{abs(diff_pct):.0f}%)"
                                elif ratio > 1.2:
                                    cons_str = f" ⚠️목표${our_tgt:.0f} vs 컨센${cavg:.0f}(↓{abs(diff_pct):.0f}%)"
                                else:
                                    cons_str = f" 📊컨센${cavg:.0f}"
                            elif cavg > 0:
                                cons_str = f" 📊컨센${cavg:.0f}"
                    except Exception:
                        pass
                    msg += f"{em} *{info.get('name', sym)}* ${cur:,.2f} ({chg:+.1f}%) | 손익 ${pnl:+,.2f}{cons_str}\n"
                except Exception:
                    msg += f"⚪ *{info.get('name', sym)}* 조회 실패\n"
            if total_cost > 0:
                total_pnl = round(total_eval - total_cost, 2)
                total_pnl_pct = total_pnl / total_cost * 100
                total_krw = total_eval * fx_rate
                msg += f"┄ 총평가 ${total_eval:,.2f} (₩{total_krw:,.0f}) | 손익 *${total_pnl:+,.2f}* ({total_pnl_pct:+.1f}%)\n"

        stops = load_stoploss()
        us_stops = stops.get("us_stocks", {})
        danger = []
        for sym, info in us_stops.items():
            try:
                d = await get_yahoo_quote(sym)
                await asyncio.sleep(0.2)
                cur = float(d.get("price", 0) or 0) if d else 0
                sp = float(info.get("stop_price") or info.get("stop") or 0)
                if cur > 0 and sp > 0 and (sp - cur) / cur * 100 >= -7:
                    danger.append(f"⚠️ *{info.get('name', sym)}* 손절 ${sp:,.2f} ({(sp-cur)/cur*100:+.1f}%)")
            except Exception:
                pass
        if danger:
            msg += "\n🛑 *손절선 근접*\n" + "\n".join(danger) + "\n"

        # ── 섹터 ETF top/bottom ──
        try:
            loop = asyncio.get_running_loop()
            etfs = await loop.run_in_executor(None, fetch_us_sector_etf)
            if etfs:
                sorted_e = sorted(etfs, key=lambda x: x["chg_1d"], reverse=True)
                top3 = sorted_e[:3]
                bot3 = sorted_e[-3:]
                msg += "\n[섹터]\n"
                for e in top3:
                    msg += f"🟢 {e['name']} {e['chg_1d']:+.1f}%\n"
                for e in bot3:
                    msg += f"🔴 {e['name']} {e['chg_1d']:+.1f}%\n"
        except Exception:
            pass

        msg += "\n→ Claude에서 점검하세요"
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"us_market_summary 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 3: 손절선 도달 (10분마다)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _get_stoploss_sent_count(sent: dict, ticker: str, today: str) -> int:
    """오늘 해당 ticker 손절 알림 발송 횟수 반환. 날짜가 다르면 0."""
    entry = sent.get(ticker, {})
    if entry.get("date") != today:
        return 0
    return entry.get("count", 0)

def _increment_stoploss_sent(sent: dict, ticker: str, today: str):
    """손절 알림 발송 횟수를 1 증가시키고 dict를 직접 수정."""
    entry = sent.get(ticker, {})
    if entry.get("date") != today:
        entry = {"date": today, "count": 0}
    entry["count"] = entry["count"] + 1
    sent[ticker] = entry


async def check_stoploss(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if not _is_kr_trading_time(now):
        return
    is_kr = not (now.hour < 9 or (now.hour >= 15 and now.minute > 30))
    is_us = _is_us_market_hours_kst()

    stops = load_stoploss()
    kr_stops = {k: v for k, v in stops.items() if k != "us_stocks"}
    us_stops = stops.get("us_stocks", {})
    wa = load_watchalert()
    if not kr_stops and not us_stops and not wa:
        return

    today = now.strftime("%Y%m%d")
    sent = load_json(STOPLOSS_SENT_FILE, {})
    full_alerts = []   # count==0: 풀 알림
    remind_alerts = [] # count==1: 리마인더

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
                            cnt = _get_stoploss_sent_count(sent, ticker, today)
                            if cnt >= 2:
                                continue  # 하루 2회 초과 → 스킵
                            ep = info.get("entry_price", 0)
                            drop = ((price - ep) / ep * 100) if ep > 0 else 0
                            if cnt == 0:
                                full_alerts.append(
                                    (ticker, f"🚨🚨 *{info['name']}* ({ticker})\n"
                                     f"  현재가: {price:,}원 ← 손절선 {sp:,}원 도달!\n"
                                     + (f"  손실: {drop:.1f}%\n" if ep > 0 else "")
                                     + "  → *즉시 매도 검토!*")
                                )
                            else:  # cnt == 1
                                remind_alerts.append(
                                    (ticker, f"⚠️ *{info['name']}* 여전히 손절 아래 {price:,}원 (손절선 {sp:,}원)")
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
                    cnt = _get_stoploss_sent_count(sent, sym, today)
                    if cnt >= 2:
                        continue
                    tp = info.get("target_price", 0)
                    if cnt == 0:
                        full_alerts.append(
                            (sym, f"🚨🇺🇸 *{info['name']}* ({sym})\n"
                             f"  현재가: ${price:,.2f} ← 손절선 ${sp:,.2f} 도달!\n"
                             + (f"  목표가: ${tp:,.2f}\n" if tp else "")
                             + "  → *즉시 매도 검토!*")
                        )
                    else:
                        remind_alerts.append(
                            (sym, f"⚠️ *{info['name']}* 여전히 손절 아래 ${price:,.2f} (손절선 ${sp:,.2f})")
                        )
            except Exception:
                pass

    if full_alerts:
        lines = [text for _, text in full_alerts]
        msg = "🔴🔴🔴 *손절선 도달!* 🔴🔴🔴\n\n" + "\n\n".join(lines) + "\n\n⚠️ Thesis 붕괴 시 가격 무관 즉시 매도"
        try:
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            for ticker, _ in full_alerts:
                _increment_stoploss_sent(sent, ticker, today)
        except Exception as e:
            print(f"손절 알림 전송 오류: {e}")

    if remind_alerts:
        lines = [text for _, text in remind_alerts]
        msg = "🔔 *손절선 리마인더*\n\n" + "\n".join(lines)
        try:
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            for ticker, _ in remind_alerts:
                _increment_stoploss_sent(sent, ticker, today)
        except Exception as e:
            print(f"손절 리마인더 전송 오류: {e}")

    if full_alerts or remind_alerts:
        save_json(STOPLOSS_SENT_FILE, sent)

    # ── 매수 희망가 감시 (watchalert) ──
    try:
        _now_w = datetime.now(KST)
        if _now_w.weekday() >= 5 or not (8 <= _now_w.hour < 18):
            return  # 주말 또는 장외 시간(08:00~18:00)이면 스킵
        wa = load_watchalert()
        if wa:
            token_wa = await get_kis_token()
            buy_alerts = []
            today_w = _now_w.strftime("%Y-%m-%d")
            watch_sent = load_json(WATCH_SENT_FILE, {})
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
                    if cur > 0 and cur <= buy_price and watch_sent.get(ticker) != today_w:
                        watch_sent[ticker] = today_w
                        save_json(WATCH_SENT_FILE, watch_sent)
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
# 🔔 자동알림 4: 복합 이상 신호 (30분마다)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_anomaly_fired: dict = {}   # {"date": "YYYY-MM-DD", "sent": set()}


async def check_anomaly(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if not _is_kr_trading_time(now):
        return
    try:
        token = await get_kis_token()
        if not token:
            return

        # 일일 중복 방지 초기화
        today = now.strftime("%Y-%m-%d")
        global _anomaly_fired
        if _anomaly_fired.get("date") != today:
            _anomaly_fired = {"date": today, "sent": set()}
        fired = _anomaly_fired["sent"]

        portfolio = load_json(PORTFOLIO_FILE, {})
        kr_portfolio = {k for k in portfolio if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(portfolio[k], dict)}
        stops = load_stoploss()
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
                try:
                    vol_ok = float(vol_rate) >= 150
                except Exception:
                    pass

                inv = await get_investor_trend(ticker, token)
                await asyncio.sleep(0.4)
                fr = 0.0
                if inv and len(inv) > 0:
                    t = inv[0] if isinstance(inv, list) else inv
                    fn = int(t.get("frgn_ntby_qty", 0))
                    if mcap > 0 and price > 0:
                        fr = (fn * price) / (mcap * 1e8) * 100

                if not (vol_ok and fr > 0.03):
                    continue

                # ── 보유 여부에 따라 신호 분류 ──
                is_held = ticker in kr_portfolio
                stop_info = stops.get(ticker, {})
                stop_p   = float(stop_info.get("stop_price",   0) or 0)
                target_p = float(stop_info.get("target_price", 0) or 0)

                if is_held:
                    if stop_p > 0 and price <= stop_p * 1.05:
                        signal, icon = "손절 경고", "🛑"
                    elif target_p > 0 and price >= target_p * 0.95:
                        signal, icon = "익절 검토", "🎯"
                    else:
                        signal, icon = "추세 확인", "📊"
                else:
                    signal, icon = "매수 관심", "👀"

                dedup_key = f"{ticker}:{signal}"
                if dedup_key in fired:
                    continue
                fired.add(dedup_key)

                alerts.append(
                    f"{icon} *{name}* ({ticker}) — {signal}\n"
                    f"  {price:,}원 ({change}%)\n"
                    f"  거래량 {vol_rate}%↑ · 외국인 {fr:+.3f}%"
                )
            except Exception:
                pass

        if alerts:
            msg = f"🔔 *복합 신호* ({now.strftime('%H:%M')})\n\n"
            msg += "\n\n".join(alerts)
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"이상 신호 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 6: 수급이탈 경고 (15:40)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_drain_sent_today: dict = {}      # {"date": "YYYY-MM-DD", "sent": set()}
_momentum_sent_today: dict = {}   # {"date": "YYYY-MM-DD", "sent": set()}


async def check_supply_drain(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if not _is_kr_trading_time(now):
        return
    try:
        token = await get_kis_token()
        if not token:
            return
        portfolio = load_json(PORTFOLIO_FILE, {})
        kr_stocks = {k: v for k, v in portfolio.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)}
        if not kr_stocks:
            return

        today = now.strftime("%Y-%m-%d")
        global _drain_sent_today
        if _drain_sent_today.get("date") != today:
            _drain_sent_today = {"date": today, "sent": set()}
        drain_sent = _drain_sent_today["sent"]

        alerts = []
        for ticker, info in kr_stocks.items():
            if ticker in drain_sent:
                continue
            try:
                rows = await kis_investor_trend(ticker, token)
                await asyncio.sleep(0.3)
                if len(rows) < 3:
                    continue
                if all(int(rows[i].get("frgn_ntby_qty", 0) or 0) < 0 for i in range(3)):
                    name = info.get("name", ticker)
                    qty_3 = [int(rows[i].get("frgn_ntby_qty", 0) or 0) for i in range(3)]
                    drain_sent.add(ticker)
                    alerts.append(
                        f"📉 *{name}* ({ticker}) 외인 3일 연속 순매도\n"
                        f"  최근: {qty_3[0]:+,} / {qty_3[1]:+,} / {qty_3[2]:+,}주"
                    )
            except Exception:
                pass

        if alerts:
            msg = ("⚠️ *수급이탈 경고* — 외인 3일 연속 순매도\n\n"
                   + "\n\n".join(alerts)
                   + "\n\n→ 매도 검토 또는 포지션 점검")
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"check_supply_drain 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 7: 모멘텀 종료 감지 (15:45)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def momentum_exit_check(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    try:
        token = await get_kis_token()
        if not token:
            return
        portfolio = load_json(PORTFOLIO_FILE, {})
        kr_stocks = {k: v for k, v in portfolio.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)}
        if not kr_stocks:
            return

        today = now.strftime("%Y-%m-%d")
        global _momentum_sent_today
        if _momentum_sent_today.get("date") != today:
            _momentum_sent_today = {"date": today, "sent": set()}
        sent = _momentum_sent_today["sent"]

        alerts = []
        for ticker, info in kr_stocks.items():
            if ticker in sent:
                continue
            try:
                result = await check_momentum_exit(ticker, token)
                if not result["warning"]:
                    continue
                sent.add(ticker)
                name = info.get("name", ticker)
                total = len(result["conditions"])
                count = result["count"]
                lines = [f"🔴 *{name}* ({ticker}) — {count}/{total} 신호\n"]
                for c in result["conditions"]:
                    icon = "✅" if c["triggered"] else "❌"
                    lines.append(f"{icon} {c['condition']}: {c['detail']}")
                alerts.append("\n".join(lines))
            except Exception as e:
                print(f"[momentum] {ticker} 오류: {e}")

        if alerts:
            msg = ("⚠️ *모멘텀 종료 경고* (15:45)\n\n"
                   + "\n\n".join(alerts)
                   + "\n\n→ 등급 재평가 필요")
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"momentum_exit_check 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 8: 주간 리뷰 리마인더 (일 10:00)
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
    # ── 컨센서스 변동 (전주 vs 이번주) ──
    try:
        _cc = load_consensus_cache()
        changes = []
        for ticker, cd in _cc.get("kr", {}).items():
            prev_avg = cd.get("prev_avg")
            cur_avg  = cd.get("avg")
            if prev_avg and cur_avg and int(prev_avg) != int(cur_avg):
                diff_pct = (cur_avg - prev_avg) / prev_avg * 100
                name = cd.get("name", ticker)
                k_p = f"{int(prev_avg/1000)}K" if prev_avg >= 1000 else str(int(prev_avg))
                k_c = f"{int(cur_avg/1000)}K"  if cur_avg  >= 1000 else str(int(cur_avg))
                arrow = "↑" if diff_pct > 0 else "↓"
                changes.append(f"{name} {k_p}→{k_c} ({arrow}{abs(diff_pct):.1f}%)")
        for ticker, cd in _cc.get("us", {}).items():
            prev_avg = cd.get("prev_avg")
            cur_avg  = cd.get("avg")
            if prev_avg and cur_avg and round(float(prev_avg), 1) != round(float(cur_avg), 1):
                diff_pct = (cur_avg - prev_avg) / prev_avg * 100
                name = cd.get("name", ticker)
                arrow = "↑" if diff_pct > 0 else "↓"
                changes.append(f"{ticker}({name}) ${prev_avg:.0f}→${cur_avg:.0f} ({arrow}{abs(diff_pct):.1f}%)")
        if changes:
            msg += "\n\n📊 *컨센서스 변동*\n" + "\n".join(changes)
    except Exception:
        pass

    # ── 이번 주 매매 요약 ──
    try:
        stats = get_trade_stats("month")
        trades_this_week = [
            t for t in stats.get("trades", [])
            if t.get("side") == "sell" and t.get("date", "") >= (datetime.now(KST) - timedelta(days=7)).strftime("%Y-%m-%d")
        ]
        if trades_this_week:
            total_pnl_w = sum(t.get("pnl", 0) or 0 for t in trades_this_week)
            wins_w  = sum(1 for t in trades_this_week if t.get("result") == "win")
            lines_w = [f"\n\n💼 *이번 주 매매* ({len(trades_this_week)}건, 승률 {wins_w}/{len(trades_this_week)}, 손익 {total_pnl_w:+,.0f})"]
            for t in trades_this_week:
                pnl_str = f"{t.get('pnl', 0):+,.0f}" if t.get("pnl") is not None else "?"
                pnl_pct = f"{t.get('pnl_pct', 0):+.1f}%" if t.get("pnl_pct") is not None else ""
                icon = "✅" if t.get("result") == "win" else ("❌" if t.get("result") == "loss" else "⚪")
                lines_w.append(f"{icon} {t.get('name', t['ticker'])} {pnl_str}원 ({pnl_pct})")
            msg += "\n".join(lines_w)
        # 월말이면 이번 달 전체 성과 추가 (남은 날이 7일 이하)
        now_dt = datetime.now(KST)
        import calendar as _cal
        last_day = _cal.monthrange(now_dt.year, now_dt.month)[1]
        if now_dt.day >= last_day - 6:
            ms = get_trade_stats("month")
            if ms.get("total_trades", 0) > 0:
                wr = ms.get("win_rate_pct")
                msg += (
                    f"\n\n📅 *{ms['period']} 월간 성과*"
                    f"\n승률 {wr}% ({ms['wins']}승 {ms['losses']}패 / {ms['total_trades']}건)"
                    f"\n총손익 {ms['total_pnl']:+,.0f}원 | 평균보유 {ms.get('avg_holding_days') or '?'}일"
                )
                if ms.get("best_trade"):
                    b = ms["best_trade"]
                    msg += f"\n🏆 최고: {b.get('name', b['ticker'])} {b.get('pnl_pct', 0):+.1f}%"
                if ms.get("worst_trade"):
                    w = ms["worst_trade"]
                    msg += f"\n💀 최저: {w.get('name', w['ticker'])} {w.get('pnl_pct', 0):+.1f}%"
    except Exception:
        pass
    try:
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"주간 리뷰 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📸 포트 스냅샷 + 드로다운 감지 (15:50 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def snapshot_and_drawdown(context: ContextTypes.DEFAULT_TYPE):
    """장마감 후 포트 스냅샷 저장 + 드로다운 경고 (규칙 위반 시에만 텔레그램 발송)"""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    try:
        token = await get_kis_token()
        await save_portfolio_snapshot(token)
    except Exception as e:
        print(f"[snapshot] 스냅샷 저장 오류: {e}")
    try:
        dd = check_drawdown()
        alerts = dd.get("alerts", [])
        if not alerts:
            return
        lines = [f"⚠️ *리스크 한도 경고* ({now.strftime('%H:%M')})"]
        wr  = dd.get("weekly_return_pct")
        mdd = dd.get("monthly_max_drawdown_pct")
        mr  = dd.get("monthly_return_pct")
        cw  = dd.get("cash_weight_pct")
        if wr is not None:
            warn = " ⚠️ 한도 초과!" if wr <= -4 else ""
            lines.append(f"\n📉 주간 수익률: {wr:+.1f}%{warn}")
        if mdd is not None:
            warn = " 🚨 한도 초과!" if mdd <= -7 else ""
            lines.append(f"📉 월간 드로다운: {mdd:.1f}%{warn}")
        elif mr is not None:
            lines.append(f"📉 월간 수익률: {mr:+.1f}%")
        if cw is not None:
            lines.append(f"💰 현금비중: {cw:.1f}%")
        for a in alerts:
            lvl = "🚨" if a["level"] == "CRITICAL" else "⚠️"
            lines.append(f"{lvl} {a['message']}")
        await context.bot.send_message(
            chat_id=CHAT_ID, text="\n".join(lines), parse_mode="Markdown"
        )
    except Exception as e:
        print(f"[drawdown] 드로다운 체크 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📋 컨센서스 배치 캐시 (매주 월요일 07:05 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def weekly_consensus_update(context: ContextTypes.DEFAULT_TYPE):
    """매주 월요일 07:05 KST — 포트폴리오+워치리스트 컨센서스 배치 업데이트."""
    try:
        print("[consensus_update] 컨센서스 배치 업데이트 시작")
        cache = await update_consensus_cache()
        kr_cnt = len(cache.get("kr", {}))
        us_cnt = len(cache.get("us", {}))
        print(f"[consensus_update] 완료: KR {kr_cnt}종목, US {us_cnt}종목")
    except Exception as e:
        print(f"[consensus_update] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 💾 /data/ 자동 백업 (매일 22:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def auto_backup(context: ContextTypes.DEFAULT_TYPE):
    """매일 22:00 KST — /data/*.json GitHub Gist 자동 백업"""
    try:
        result = await backup_data_files()
        if result.get("ok"):
            files = result.get("files", [])
            print(f"[backup] 완료: {len(files)}개 파일 — {result.get('action', '')}")
        else:
            err = result.get("error", "알 수 없는 오류")
            print(f"[backup] 실패: {err}")
            if GITHUB_TOKEN:  # 설정은 됐는데 오류면 텔레그램 알림
                try:
                    await context.bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"⚠️ 자동 백업 실패: {err}"
                    )
                except Exception:
                    pass
    except Exception as e:
        print(f"[backup] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📋 유니버스 자동 갱신 (매주 월요일 07:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def weekly_universe_update(context: ContextTypes.DEFAULT_TYPE):
    """매주 월요일 07:00 KST — KOSPI200 + KOSDAQ150 기준으로 stock_universe.json 자동 갱신."""
    try:
        token = await get_kis_token()
        if not token:
            print("[universe_update] KIS 토큰 발급 실패")
            return

        old = get_stock_universe()
        new = await fetch_universe_from_krx(token)
        if not new:
            print("[universe_update] 종목 조회 결과 없음 — 갱신 스킵")
            return

        added   = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))

        updated_data = {
            "updated": datetime.now(KST).strftime("%Y-%m-%d"),
            "note":    "KIS 시가총액 상위 자동 갱신 (KOSPI200 + KOSDAQ 상위 150)",
            "codes":   new,
        }
        save_json(UNIVERSE_FILE, updated_data)
        print(f"[universe_update] 저장 완료: {len(new)}종목 (추가 {len(added)}, 삭제 {len(removed)})")

        if not added and not removed:
            return  # 변경 없으면 텔레그램 알림 생략

        msg = f"📋 *유니버스 갱신 완료* ({len(new)}종목)\n"
        if added:
            names = [new.get(t, t) for t in added]
            msg += f"\n✅ 추가 {len(added)}종목: {', '.join(names)}"
        if removed:
            names = [old.get(t, t) for t in removed]
            msg += f"\n❌ 삭제 {len(removed)}종목: {', '.join(names)}"

        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[universe_update] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📅 실적 캘린더 알림 (매일 07:00 KST, 3일 전 알림)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_earnings_calendar(context: ContextTypes.DEFAULT_TYPE):
    """포트폴리오+워치리스트 종목의 추정실적 분기 일정 확인.
    KIS에 실적발표일 API가 없어 추정실적 데이터(분기 dt)로 대체.
    다가올 분기 결산월 3일 전이면 알림."""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    try:
        token = await get_kis_token()
        wl = load_watchlist()
        pf = load_json(PORTFOLIO_FILE, {})
        tickers = {}
        for t, n in wl.items():
            tickers[t] = n
        for t, v in pf.items():
            if t in ("cash_krw", "cash_usd", "us_stocks"):
                continue
            tickers[t] = v.get("name", t)

        alerts = []
        for ticker, name in tickers.items():
            try:
                est = await kis_estimate_perform(ticker, token)
                for q in est.get("quarterly", []):
                    dt_str = q.get("dt", "")
                    if not dt_str or len(dt_str) < 6 or not dt_str[:6].isdigit():
                        continue
                    # dt 형식: "202603" → 해당 월 말일을 결산일로 추정
                    yr = int(dt_str[:4])
                    mo = int(dt_str[4:6])
                    if not (1 <= mo <= 12):
                        continue
                    # 결산월 다음달 중순을 발표 예상일로 추정 (실제 일정과 다를 수 있음)
                    announce_mo = mo + 1 if mo < 12 else 1
                    announce_yr = yr if mo < 12 else yr + 1
                    announce_date = datetime(announce_yr, announce_mo, 15, tzinfo=KST)
                    diff = (announce_date - now).days
                    if 0 <= diff <= 3:
                        op = q.get("op", "?")
                        eps = q.get("eps", "?")
                        alerts.append(f"📊 *{name}*({ticker}) {dt_str} 실적발표 예상 ~{diff}일 전 (추정)\n  영업이익: {op} | EPS: {eps}")
                        break  # 가장 가까운 분기 1건만 알림
                await asyncio.sleep(0.3)
            except Exception:
                continue

        if alerts:
            msg = "📅 *실적 캘린더 알림*\n\n" + "\n\n".join(alerts)
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[earnings_calendar] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📅 미국 실적 캘린더 알림 (매일 07:10 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_us_earnings_calendar(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    try:
        portfolio = load_json(PORTFOLIO_FILE, {})
        us_stocks = portfolio.get("us_stocks", {})
        us_wl = load_us_watchlist()
        tickers = list(set(list(us_stocks.keys()) + list(us_wl.keys())))
        if not tickers:
            return
        loop = asyncio.get_running_loop()
        earnings = await loop.run_in_executor(None, fetch_us_earnings_calendar, tickers)
        upcoming = [e for e in earnings if 0 <= e["days_until"] <= 7]
        if upcoming:
            msg = "📅 *미국 실적 발표 예정*\n\n"
            for e in upcoming:
                msg += f"• {e['name']}({e['ticker']}) — {e['earnings_date']} ({e['days_until']}일 후)\n"
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[us_earnings_calendar] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 💰 배당 캘린더 알림 (매일 07:00 KST, 7일 전 알림)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_dividend_calendar(context: ContextTypes.DEFAULT_TYPE):
    """포트폴리오+워치리스트 종목의 배당기준일 7일 전 알림.
    참고: 배당락일은 기준일 전 영업일."""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    try:
        token = await get_kis_token()
        wl = load_watchlist()
        pf = load_json(PORTFOLIO_FILE, {})
        tickers = {}
        for t, n in wl.items():
            tickers[t] = n
        for t, v in pf.items():
            if t in ("cash_krw", "cash_usd", "us_stocks"):
                continue
            tickers[t] = v.get("name", t)

        today_str = now.strftime("%Y%m%d")
        end_str = (now + timedelta(days=30)).strftime("%Y%m%d")
        alerts = []

        for ticker, name in tickers.items():
            try:
                rows = await kis_dividend_schedule(token, from_dt=today_str,
                                                    to_dt=end_str, ticker=ticker)
                for r in (rows if isinstance(rows, list) else []):
                    record_dt = r.get("record_date", "") or r.get("bass_dt", "")
                    if not record_dt or len(record_dt) < 8:
                        continue
                    rec_date = datetime.strptime(record_dt, "%Y%m%d").replace(tzinfo=KST)
                    diff = (rec_date - now).days
                    if 0 <= diff <= 7:
                        amt = r.get("per_sto_divi_amt", "?")
                        rate = r.get("divi_rate", "?")
                        pay_dt = r.get("divi_pay_dt", "?")
                        alerts.append(
                            f"💰 *{name}*({ticker}) 배당기준일 {record_dt} (~{diff}일 전)\n"
                            f"  배당금: {amt}원 | 배당률: {rate}% | 지급일: {pay_dt}\n"
                            f"  ※ 배당락일은 기준일 전 영업일 (매수 마감)"
                        )
                        break  # 종목당 1건만 알림
                await asyncio.sleep(0.3)
            except Exception:
                continue

        if alerts:
            msg = "📅 *배당 캘린더 알림*\n\n" + "\n\n".join(alerts)
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[dividend_calendar] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📄 증권사 리포트 자동 수집 (매일 07:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# update_krx_db_job 비활성화 — GitHub Actions에서 크롤링 후 /api/krx_upload로 업로드
# async def update_krx_db_job(context): ...

KRX_UPLOAD_KEY = os.environ.get("KRX_UPLOAD_KEY", "")


async def collect_reports_daily(context: ContextTypes.DEFAULT_TYPE):
    """매일 07:00 KST — 보유+감시 종목 증권사 리포트 수집"""
    if not _REPORT_AVAILABLE:
        return
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return  # 주말 스킵
    try:
        tickers = get_collection_tickers()
        if not tickers:
            return

        loop = asyncio.get_running_loop()
        new_reports = await loop.run_in_executor(None, collect_reports, tickers)

        if new_reports:
            failed = sum(1 for r in new_reports if r.get("extraction_status") == "failed")
            msg = f"📄 *증권사 리포트 수집* ({len(new_reports)}건"
            if failed:
                msg += f", 추출실패 {failed}건"
            msg += ")\n\n"
            def _esc(s: str) -> str:
                """Telegram Markdown v1 특수문자 이스케이프"""
                for ch in ("*", "_", "`", "["):
                    s = s.replace(ch, "\\" + ch)
                return s
            for r in new_reports[:10]:  # 최대 10건 표시
                msg += f"• {_esc(r.get('name', ''))} - {_esc(r.get('source', ''))} \"{_esc(r.get('title', ''))}\" ({r.get('date', '')[-5:]})\n"
            if len(new_reports) > 10:
                msg += f"\n... 외 {len(new_reports) - 10}건"
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[report_daily] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📊 매크로 대시보드 (매일 18:00 + 06:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def macro_dashboard(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    # 18:00 실행: 평일만 / 06:00 실행: 일요일 제외 (토요일은 금요일 결과)
    if now.hour >= 12 and now.weekday() >= 5:
        return
    if now.hour < 12 and now.weekday() == 6:
        return
    try:
        data = await collect_macro_data()
        msg = format_macro_msg(data)

        # 섹터 로테이션 추가
        try:
            token = await get_kis_token()
            rot = await detect_sector_rotation(token)
            if rot.get("rotations"):
                msg += "\n[자금 이동] " + " | ".join(rot["rotations"])
            elif rot.get("top_inflow"):
                inflow_names = [s["name"] for s in rot["top_inflow"][:2]]
                msg += f"\n[자금 유입] {', '.join(inflow_names)}"
        except Exception:
            pass

        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"매크로 대시보드 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 7: DART 공시 체크 (30분마다)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_dart_disclosure(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    if not (8 <= now.hour < 20):
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
                d = await kis_stock_price(t, token) if token else {}
                cur = int(d.get("stck_prpr", 0) or 0)
                eval_amt = cur * qty
                cost_amt = int(avg) * qty
                pnl = eval_amt - cost_amt
                pnl_pct = (cur - avg) / avg * 100 if avg else 0
                total_eval += eval_amt
                total_cost += cost_amt
                icon = "🔺" if pnl >= 0 else "🔻"
                msg += f"{icon} *{info.get('name', t)}* {qty}주\n  {cur:,}원 ({pnl_pct:+.1f}%) P&L {pnl:+,}원\n"
                await asyncio.sleep(0.3)
            except Exception:
                msg += f"⚪ *{info.get('name', t)}* — 조회실패\n"
        msg += "\n"
    if us_stocks:
        msg += "🇺🇸 *미국*\n"
        for sym, info in us_stocks.items():
            try:
                qty = info.get("qty", 0)
                avg = float(info.get("avg_price", 0))
                d = await kis_us_stock_price(sym, _guess_excd(sym), token) if token else {}
                cur = float(d.get("last", 0) or 0)
                eval_amt = cur * qty
                cost_amt = avg * qty
                pnl = eval_amt - cost_amt
                pnl_pct = (cur - avg) / avg * 100 if avg else 0
                icon = "🔺" if pnl >= 0 else "🔻"
                msg += f"{icon} *{info.get('name', sym)}* {qty}주\n  ${cur:,.2f} ({pnl_pct:+.1f}%) P&L ${pnl:+,.2f}\n"
                await asyncio.sleep(0.3)
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
                    d = await kis_us_stock_price(t, _guess_excd(t), token) if token else {}
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
    if len(context.args) < 2:
        await update.message.reply_text("사용법: /watch 005930 삼성전자"); return
    wl = load_watchlist()
    wl[context.args[0]] = context.args[1]
    save_json(WATCHLIST_FILE, wl)
    await _refresh_ws()
    await update.message.reply_text(f"✅ *{context.args[1]}* 추가!", parse_mode="Markdown")


async def unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("사용법: /unwatch 005930"); return
    wl = load_watchlist()
    if context.args[0] in wl:
        n = wl.pop(context.args[0])
        save_json(WATCHLIST_FILE, wl)
        await _refresh_ws()
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
        stops[ticker] = {"name": name, "stop_price": stop, "entry_price": fourth}
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
                d = await kis_us_stock_price(sym, _guess_excd(sym), token) if token else {}
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
    regime_state = load_json(REGIME_STATE_FILE, {"current": {}})
    regime_cur = regime_state.get("current", {})
    regime_name = regime_cur.get("regime", "")
    regime_emoji = {"offensive": "🟢", "neutral": "🟡", "defensive": "🔴"}.get(regime_name, "⚪")
    regime_kr = {"offensive": "공격", "neutral": "중립", "defensive": "방어"}.get(regime_name, "미정")

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
    data = load_reports()
    reports = data.get("reports", [])
    if not reports:
        await update.message.reply_text("📭 수집된 리포트 없음"); return
    cutoff = (datetime.now(KST) - timedelta(days=3)).strftime("%Y-%m-%d")
    recent = [r for r in reports if r.get("date", "") >= cutoff]
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
    last = data.get("last_collected", "")
    if last:
        msg += f"_마지막 수집: {last[:16]}_"
    # 텔레그램 메시지 길이 제한
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n_(일부 생략)_"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def manual_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 요약 생성 중...")
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
# 봇 시작
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def post_init(application: Application):
    # ── 자동 복원 체크: 핵심 파일 없으면 Gist에서 복원 ──────────────────
    _critical = [PORTFOLIO_FILE, STOPLOSS_FILE, WATCHLIST_FILE]
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reply Keyboard 버튼 텍스트 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_BUTTON_MAP = {
    "📊 포트폴리오": portfolio_cmd,
    "🚨 알림현황": alert_cmd,
    "📈 매크로": macro,
    "🔍 워치리스트": watchlist_cmd,
    "📰 리포트": reports_cmd,
    "📋 전체현황": status_cmd,
}

async def _button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    handler = _BUTTON_MAP.get(text)
    if handler:
        await handler(update, context)


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

    # 자동 알림 스케줄
    jq = app.job_queue
    jq.run_repeating(check_stoploss, interval=600, first=60, name="stoploss")
    jq.run_repeating(check_anomaly, interval=1800, first=120, name="anomaly")
    # 환율 알림: 매크로 대시보드(macro_pm/macro_am)로 통합 완료
    jq.run_repeating(check_dart_disclosure, interval=1800, first=180, name="dart")
    # 모든 run_daily time은 KST-aware(tzinfo=KST)로 지정 → Railway(UTC 서버)에서도 정확한 시각에 실행됨
    jq.run_daily(daily_kr_summary, time=dtime(15, 40, tzinfo=KST), days=(0,1,2,3,4), name="kr_summary")
    # 미국 장 마감 요약: 서머타임(05:05 KST) + 표준시(06:05 KST) 두 시각 등록
    # _is_us_market_closed() 가드로 실제 마감 30분 이내일 때만 발송, 이중 발송 없음
    jq.run_daily(us_market_summary, time=dtime(5,  5, tzinfo=KST), days=(1,2,3,4,5), name="us_summary_dst")
    jq.run_daily(us_market_summary, time=dtime(6,  5, tzinfo=KST), days=(1,2,3,4,5), name="us_summary_std")
    jq.run_daily(check_supply_drain,   time=dtime(15, 40, tzinfo=KST), days=(0,1,2,3,4), name="supply_drain")
    jq.run_daily(momentum_exit_check,  time=dtime(15, 45, tzinfo=KST), days=(0,1,2,3,4), name="momentum_check")
    jq.run_daily(snapshot_and_drawdown, time=dtime(15, 50, tzinfo=KST), days=(0,1,2,3,4), name="snapshot_dd")
    jq.run_daily(weekly_review,           time=dtime(7,  0, tzinfo=KST), days=(5,), name="weekly")
    jq.run_daily(weekly_universe_update,  time=dtime(7,  0, tzinfo=KST), days=(0,), name="universe_update")
    jq.run_daily(weekly_consensus_update, time=dtime(7,  5, tzinfo=KST), days=(0,), name="consensus_update")
    jq.run_daily(auto_backup,            time=dtime(22, 0, tzinfo=KST), name="auto_backup")
    # 매크로 대시보드: 18:00(한국장 마감) + 06:00(미국장 마감)
    jq.run_daily(macro_dashboard, time=dtime(18, 0, tzinfo=KST), name="macro_pm")
    jq.run_daily(macro_dashboard, time=dtime(6,  0, tzinfo=KST), name="macro_am")
    # 실적/배당 캘린더: 매일 07:00 KST 평일만
    jq.run_daily(check_earnings_calendar,  time=dtime(7,  0, tzinfo=KST), days=(0,1,2,3,4), name="earnings_cal")
    jq.run_daily(check_dividend_calendar,  time=dtime(7,  0, tzinfo=KST), days=(0,1,2,3,4), name="dividend_cal")
    jq.run_daily(check_us_earnings_calendar, time=dtime(7, 10, tzinfo=KST), days=(0,1,2,3,4), name="us_earnings_cal")
    jq.run_daily(collect_reports_daily,    time=dtime(7,  0, tzinfo=KST), days=(0,1,2,3,4), name="report_collect")
    # KRX 전종목 DB 갱신: GitHub Actions에서 크롤링 → /api/krx_upload로 업로드
    # jq.run_daily(update_krx_db_job, time=dtime(15, 55, tzinfo=KST), days=(0,1,2,3,4), name="krx_db")

    port = int(os.environ.get("PORT", 8080))
    print(f"봇 실행! MCP SSE 서버 포트: {port}")
    asyncio.run(_run_all(app, port))


async def _handle_krx_upload(request: web.Request) -> web.Response:
    """POST /api/krx_upload — GitHub Actions에서 KRX DB 업로드 수신."""
    # 인증 (KRX_UPLOAD_KEY 미설정 시 모든 요청 거부)
    if not KRX_UPLOAD_KEY:
        return web.json_response({"error": "KRX_UPLOAD_KEY not configured"}, status=503)
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {KRX_UPLOAD_KEY}":
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        db = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    date = db.get("date", "")
    if not date or len(date) != 8:
        return web.json_response({"error": "date 필드 필요 (YYYYMMDD)"}, status=400)

    stocks = db.get("stocks")
    if not isinstance(stocks, dict) or not stocks:
        return web.json_response({"error": "stocks 필드 필요 (비어있음)"}, status=400)
    count = len(stocks)
    db["count"] = count

    # atomic write
    os.makedirs(KRX_DB_DIR, exist_ok=True)
    filepath = os.path.join(KRX_DB_DIR, f"{date}.json")
    tmp_path = filepath + ".tmp"
    payload = json.dumps(db, ensure_ascii=False)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp_path, filepath)

    size_kb = round(os.path.getsize(filepath) / 1024, 1)
    print(f"[KRX Upload] 저장 완료: {filepath} ({size_kb}KB, {count}종목)")

    # 30일 이전 파일 삭제
    _cleanup_old_db(30)

    # 텔레그램 알림
    summary = db.get("market_summary", {})
    investor_ok = db.get("investor_data_available", False)
    try:
        msg = (
            f"📊 *KRX DB 갱신 완료* (GitHub Actions)\n"
            f"종목수: {count}개\n"
            f"코스피: {summary.get('kospi_count', 0)}개 "
            f"(↑{summary.get('kospi_up', 0)} ↓{summary.get('kospi_down', 0)})\n"
            f"코스닥: {summary.get('kosdaq_count', 0)}개 "
            f"(↑{summary.get('kosdaq_up', 0)} ↓{summary.get('kosdaq_down', 0)})\n"
            f"수급: {'✅' if investor_ok else '❌ 미수집'} | "
            f"파일: {size_kb}KB"
        )
        from telegram import Bot
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[KRX Upload] 텔레그램 알림 실패: {e}")

    return web.json_response({"ok": True, "date": date, "count": count, "file_size_kb": size_kb})


async def _run_all(app, port):
    # MCP aiohttp 서버 시작
    mcp_app = web.Application(client_max_size=50 * 1024 * 1024)  # 50MB for KRX upload
    mcp_app.router.add_get("/mcp", mcp_sse_handler)
    mcp_app.router.add_post("/mcp/messages", mcp_messages_handler)
    mcp_app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))
    mcp_app.router.add_post("/api/krx_upload", _handle_krx_upload)
    runner = web.AppRunner(mcp_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
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
            except Exception:
                pass

    await ws_manager.start(_ws_alert_cb, get_ws_tickers())
    print(f"[WS] 실시간 매니저 시작 ({len(ws_manager._subscribed)}개 KR 종목)")

    # 텔레그램 봇 비동기 실행
    try:
        async with app:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            await asyncio.Event().wait()  # 무한 대기
    finally:
        await close_session()
        print("[Shutdown] aiohttp 공유 세션 정리 완료")


if __name__ == "__main__":
    main()

# ci trigger 2
