import os
import sys
import json
import re
import socket
import asyncio
import hashlib
import html as _html
from datetime import datetime, timedelta, time as dtime
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from aiohttp import web

from kis_api import *
from kis_api import (
    _DATA_DIR, _is_us_ticker, _is_us_market_hours_kst, _is_us_market_closed, _guess_excd,
    ws_manager, get_ws_tickers, close_session,
    fetch_us_earnings_calendar, fetch_us_sector_etf,
    fetch_and_cache_disclosure, parse_disclosure_summary,
)
from krx_crawler import KRX_DB_DIR, _cleanup_old_db, load_krx_db
try:
    from db_collector import collect_daily, collect_financial_weekly
    _HAS_DB_COLLECTOR = True
except ImportError:
    _HAS_DB_COLLECTOR = False


async def _refresh_ws():
    """WebSocket 구독 목록 갱신 헬퍼"""
    try:
        await ws_manager.update_tickers(get_ws_tickers())
    except Exception as e:
        print(f"[WS] refresh 오류: {e}")
from mcp_tools import (
    mcp_sse_handler, mcp_messages_handler,
    mcp_streamable_post_handler, mcp_streamable_delete_handler, mcp_streamable_options_handler,
)

try:
    from report_crawler import collect_reports, get_collection_tickers, DB_PATH as REPORT_DB_PATH
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False
    REPORT_DB_PATH = os.path.join(os.environ.get("DATA_DIR", "data"), "stock.db")


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

_REGIME_EMOJI = {"offensive": "🟢", "neutral": "🟡", "crisis": "🔴"}


def _read_regime() -> tuple[str, str]:
    """regime_state.json에서 (regime_en, emoji) 반환."""
    state = load_json(REGIME_STATE_FILE, {})
    cur = state.get("current", {})
    regime_en = cur.get("current", "neutral")
    return regime_en, _REGIME_EMOJI.get(regime_en, "⚪")


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
    # 중복 발송 방지
    _kr_sent = load_json(MACRO_SENT_FILE, {})
    _kr_key = f"{now.strftime('%Y-%m-%d')}_kr_summary"
    if _kr_sent.get("kr_summary") == _kr_key:
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

        # ── [포트 건강] 규칙 위반 체크 ──
        try:
            pf = load_json(PORTFOLIO_FILE, {})
            kr_pf = {k: v for k, v in pf.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)}
            us_pf = pf.get("us_stocks", {})
            cash_krw = float(pf.get("cash_krw", 0) or 0)
            cash_usd = float(pf.get("cash_usd", 0) or 0)

            # 총 자산 계산 (간이)
            total_kr = sum(float(v.get("avg_price", 0)) * float(v.get("qty", 0)) for v in kr_pf.values())
            total_us = sum(float(v.get("avg_price", 0)) * float(v.get("qty", 0)) for v in us_pf.values())
            total_asset = total_kr + (total_us * krw) + cash_krw + (cash_usd * krw)

            health_warnings = []
            if total_asset > 0:
                # 단일종목 비중 35% 초과
                for t, v in {**kr_pf, **us_pf}.items():
                    val = float(v.get("avg_price", 0)) * float(v.get("qty", 0))
                    if t in us_pf:
                        val *= krw
                    pct = val / total_asset * 100
                    if pct > 35:
                        health_warnings.append(f"⚠️ {v.get('name', t)} {pct:.0f}% → 한도 35% 초과")

                # 현금 비중
                cash_total = cash_krw + cash_usd * krw
                cash_pct = cash_total / total_asset * 100
                if cash_pct < 10:
                    health_warnings.append(f"⚠️ 현금 {cash_pct:.1f}% → 최소 10% 미달")

                # 레짐 체크
                regime_en, regime_e = _read_regime()
                if regime_en == "crisis" and cash_pct < 25:
                    health_warnings.append(f"⚠️ {regime_e} 레짐 현금 {cash_pct:.1f}% → 25% 권장")

            if health_warnings:
                msg += "\n[포트 건강]\n" + "\n".join(health_warnings) + "\n"
            else:
                msg += "\n✅ 포트 건강: 이상 없음\n"
        except Exception as e:
            print(f"포트 건강 체크 오류: {e}")

        # ── [이벤트] 7일 내 일정 (events.json) ──
        try:
            events = load_json(f"{_DATA_DIR}/events.json", {})
            today = now.date()
            upcoming = []
            for key, date_str in events.items():
                if not isinstance(date_str, str) or len(date_str) != 10:
                    continue
                try:
                    ev_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                diff = (ev_date - today).days
                if 0 <= diff <= 7:
                    label = key.replace("_", " ")
                    if diff == 0:
                        upcoming.append(f"• {label} (오늘)")
                    elif diff == 1:
                        upcoming.append(f"• {label} (내일)")
                    else:
                        upcoming.append(f"• {label} (D-{diff}, {ev_date.strftime('%m/%d')})")
            if upcoming:
                msg += "\n[이벤트] 7일 내\n" + "\n".join(upcoming) + "\n"
        except Exception as e:
            print(f"이벤트 섹션 오류: {e}")

        msg += "\n→ Claude에서 점검하세요"
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

        # 발송 기록
        _kr_sent["kr_summary"] = _kr_key
        save_json(MACRO_SENT_FILE, _kr_sent)

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
        # INVESTMENT_RULES v6: VIX 30 / 20 경계 (3단계)
        vix_label = "🔴 위기" if vix_p > 30 else "🟢 공격" if vix_p < 20 else "🟡 경계"
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
    # 중복 발송 방지
    _us_sent = load_json(MACRO_SENT_FILE, {})
    _us_key = f"{now.strftime('%Y-%m-%d')}_us_summary"
    if _us_sent.get("us_summary") == _us_key:
        return
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
        # INVESTMENT_RULES v6: VIX 30 / 20 경계 (3단계)
        vix_label = "🔴 위기" if vix_p > 30 else "🟢 공격" if vix_p < 20 else "🟡 경계"
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
        _us_sent["us_summary"] = _us_key
        save_json(MACRO_SENT_FILE, _us_sent)
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
    if now.weekday() >= 5:
        return  # 주말 스킵
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
                        cached = ws_manager.get_cached_price(ticker)
                        if cached is not None:
                            price = int(cached)
                        else:
                            d = await kis_stock_price(ticker, token)
                            await asyncio.sleep(0.3)
                            price = int(d.get("stck_prpr", 0))
                            if price > 0:
                                ws_manager.set_cached_price(ticker, price)
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
                cached = ws_manager.get_cached_price(sym)
                if cached is not None:
                    price = float(cached)
                else:
                    d = await get_yahoo_quote(sym)
                    await asyncio.sleep(0.3)
                    if not d:
                        continue
                    price = float(d.get("price", 0) or 0)
                    if price > 0:
                        ws_manager.set_cached_price(sym, price)
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
        if _now_w.weekday() >= 5:
            return  # 주말 스킵
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
                        cached = ws_manager.get_cached_price(ticker)
                        if cached is not None:
                            cur = float(cached)
                        else:
                            d = await kis_us_stock_price(ticker, token_wa)
                            cur = float(d.get("last", 0) or 0)
                            await asyncio.sleep(0.3)
                            if cur > 0:
                                ws_manager.set_cached_price(ticker, cur)
                    else:
                        if not is_kr:
                            continue
                        cached = ws_manager.get_cached_price(ticker)
                        if cached is not None:
                            cur = float(cached)
                        else:
                            d = await kis_stock_price(ticker, token_wa)
                            cur = int(d.get("stck_prpr", 0) or 0)
                            await asyncio.sleep(0.3)
                            if cur > 0:
                                ws_manager.set_cached_price(ticker, int(cur))
                    # US 종목은 장중(is_us)일 때만 알림 발송
                    if cur > 0 and cur <= buy_price and watch_sent.get(ticker) != today_w and (not _is_us_ticker(ticker) or is_us):
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
                # 브리핑 추가
                regime_en, regime_str = _read_regime()
                regime_ok = "매수 가능" if regime_en != "crisis" else "⚠️ 분할 1차만"
                pf = load_json(PORTFOLIO_FILE, {})
                cash_k = float(pf.get("cash_krw", 0) or 0)
                cash_u = float(pf.get("cash_usd", 0) or 0)
                events = load_json(EVENTS_FILE, {})
                today_ev = events.get(now.strftime("%Y-%m-%d"), "")

                extra = f"\n📊 레짐: {regime_str} → {regime_ok}"
                extra += f"\n💰 현금: {cash_k:,.0f}원 / ${cash_u:,.0f}"
                if today_ev:
                    extra += f"\n⚠️ 이벤트: {today_ev}"

                msg = "🟢🟢🟢 *매수 감시가 도달!* 🟢🟢🟢\n\n" + "\n\n".join(buy_alerts) + "\n" + extra + "\n\n→ 채팅에서 매수 검토"
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
                pd = await kis_stock_price(ticker, token)
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
# 🔔 자동알림 7: 모멘텀 종료 감지 (16:30)
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
            msg = ("⚠️ *모멘텀 종료 경고* (16:30)\n\n"
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
# 📋 컨센서스 배치 캐시 (매주 일요일 07:05 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def weekly_consensus_update(context: ContextTypes.DEFAULT_TYPE):
    """매주 일요일 07:05 KST — 포트폴리오+워치리스트+유니버스 전체 컨센서스 배치 업데이트."""
    now = datetime.now(KST)
    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_consensus_weekly"
    if _sent.get("consensus_weekly") == _key:
        return

    try:
        from copy import deepcopy
        print("[consensus_update] 컨센서스 배치 업데이트 시작")
        old_cache = deepcopy(load_json(CONSENSUS_CACHE_FILE, {}))

        # stock_master 전종목 (일요일이니 시간 무관)
        all_kr = {}
        try:
            from db_collector import _get_db
            conn = _get_db()
            rows = conn.execute("SELECT symbol, name FROM stock_master").fetchall()
            all_kr = {r["symbol"]: r["name"] for r in rows}
            conn.close()
        except Exception:
            pass
        # stock_master에 없는 감시 종목도 추가
        wa = load_watchalert()
        for t, v in wa.items():
            if not _is_us_ticker(t) and t not in all_kr:
                all_kr[t] = v.get("name", t) if isinstance(v, dict) else t
        wl = load_watchlist()
        for t, n in wl.items():
            if not _is_us_ticker(t) and t not in all_kr:
                all_kr[t] = n
        print(f"[consensus_update] 대상: {len(all_kr)}종목 (universe+portfolio+watch)")

        cache = await update_consensus_cache(kr_tickers=all_kr)
        kr_cnt = len(cache.get("kr", {}))
        us_cnt = len(cache.get("us", {}))
        print(f"[consensus_update] 완료: KR {kr_cnt}종목, US {us_cnt}종목")

        # 변화 감지 (10% 이상 목표가 변동 or 신규 커버리지)
        changes = detect_consensus_changes(
            old_cache.get("kr", {}), cache.get("kr", {}),
            target_pct=10.0, detect_new_cover=True
        )
        if changes:
            msg = f"📊 *주간 컨센서스 변화* ({len(changes)}건)\n\n"
            for c in changes[:15]:
                if c["type"] == "target_up":
                    msg += f"📈 *{c['name']}* — 목표가 상향 {c['detail']}\n"
                elif c["type"] == "target_down":
                    msg += f"📉 *{c['name']}* — 목표가 하향 {c['detail']}\n"
                elif c["type"] == "opinion_change":
                    msg += f"🔄 *{c['name']}* — 의견 변경 {c['detail']}\n"
                elif c["type"] == "new_cover":
                    msg += f"🆕 *{c['name']}* — 신규 커버리지 {c['detail']}\n"
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            _sent["consensus_weekly"] = _key
            save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"[consensus_update] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📋 일간 컨센서스 변화 감지 (평일 19:30 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def daily_consensus_check(context: ContextTypes.DEFAULT_TYPE):
    """매일 19:30 평일 — 보유+감시 종목 컨센서스 변화 감지."""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return

    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_consensus_daily"
    if _sent.get("consensus_daily") == _key:
        return

    try:
        from copy import deepcopy
        old_cache = deepcopy(load_json(CONSENSUS_CACHE_FILE, {}))

        # 보유+감시 한국 종목
        kr_tickers = {}
        portfolio = load_json(PORTFOLIO_FILE, {})
        for t, v in portfolio.items():
            if t not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict) and not _is_us_ticker(t):
                kr_tickers[t] = v.get("name", t)
        wa = load_watchalert()
        for t, v in wa.items():
            if not _is_us_ticker(t) and t not in kr_tickers:
                kr_tickers[t] = v.get("name", t) if isinstance(v, dict) else t
        wl = load_watchlist()
        for t, n in wl.items():
            if not _is_us_ticker(t) and t not in kr_tickers:
                kr_tickers[t] = n

        if not kr_tickers:
            return

        await update_consensus_cache(kr_tickers=kr_tickers)
        new_cache = load_json(CONSENSUS_CACHE_FILE, {})

        changes = detect_consensus_changes(
            old_cache.get("kr", {}), new_cache.get("kr", {}),
            target_pct=5.0, detect_new_cover=False
        )

        if changes:
            msg = f"📊 *컨센서스 변화 감지* ({len(changes)}건)\n\n"
            for c in changes[:10]:
                if c["type"] == "target_up":
                    msg += f"📈 *{c['name']}* — 목표가 상향 {c['detail']}\n"
                elif c["type"] == "target_down":
                    msg += f"📉 *{c['name']}* — 목표가 하향 {c['detail']}\n"
                elif c["type"] == "opinion_change":
                    msg += f"🔄 *{c['name']}* — 의견 변경 {c['detail']}\n"
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            _sent["consensus_daily"] = _key
            save_json(MACRO_SENT_FILE, _sent)

        print(f"[daily_consensus] {len(kr_tickers)}종목 수집, {len(changes)}건 변화")
    except Exception as e:
        print(f"[daily_consensus] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 일일 발굴 알림 (매일 19:05 KST, 평일)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHANGE_SCAN_SENT_FILE = f"{_DATA_DIR}/change_scan_sent.json"


async def daily_change_scan_alert(context: ContextTypes.DEFAULT_TYPE):
    """매일 19:05 평일 — turnaround/fscore_jump/insider_cluster_buy 스캔 → 워치/포트 제외 → 텔레그램 푸시."""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return

    try:
        from mcp_tools import _execute_tool

        # 워치+포트 제외 집합 (한국만)
        excluded = set()
        try:
            wl = load_watchlist()
            for t in wl.keys():
                if not _is_us_ticker(t):
                    excluded.add(t)
            pf = load_json(PORTFOLIO_FILE, {})
            for t, v in pf.items():
                if t in ("us_stocks", "cash_krw", "cash_usd"):
                    continue
                if isinstance(v, dict) and not _is_us_ticker(t):
                    excluded.add(t)
        except Exception as e:
            print(f"[change_scan] 워치/포트 로드 실패: {e}")

        # 쿨다운 기록 (7일)
        sent = load_json(CHANGE_SCAN_SENT_FILE, {})
        today_str = now.strftime("%Y-%m-%d")
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        cooldown_set = {t for t, d in sent.items() if isinstance(d, str) and d >= cutoff}

        # 프리셋별 스캔
        presets_config = [
            ("turnaround",           "📈 적자→흑자 전환"),
            ("fscore_jump",          "🚀 F-Score 도약"),
            ("insider_cluster_buy",  "👥 내부자 군집매수"),
        ]

        sections = []  # [(label, [item, ...])]
        new_sent_symbols = []

        for preset_name, label in presets_config:
            try:
                res = await _execute_tool("get_change_scan", {"preset": preset_name, "n": 10, "market": "all"})
            except Exception as e:
                print(f"[change_scan] {preset_name} 실행 오류: {e}")
                continue
            if not isinstance(res, dict):
                continue
            rows = res.get("results", []) or []
            filtered = []
            for r in rows:
                tk = r.get("ticker", "")
                if not tk or tk in excluded or tk in cooldown_set:
                    continue
                filtered.append(r)
                if len(filtered) >= 5:
                    break
            if filtered:
                sections.append((preset_name, label, filtered))
                for r in filtered:
                    new_sent_symbols.append(r.get("ticker", ""))

        total_hits = sum(len(items) for _, _, items in sections)
        if total_hits == 0:
            print("[change_scan] 결과 0건 — 발송 스킵")
            return

        msg = "🔔 *오늘의 발굴* (워치/보유 제외)\n"
        for preset_name, label, items in sections:
            msg += f"\n*{label}* ({len(items)}건)\n"
            for r in items:
                tk = r.get("ticker", "")
                nm = r.get("name", tk)
                if preset_name == "turnaround":
                    delta = r.get("op_profit_delta")
                    latest = r.get("op_profit_latest")
                    prev = r.get("op_profit_prev")
                    msg += f" • `{tk}` {nm} 영업이익 {latest:+.1f} (전: {prev:+.1f})\n" if (latest is not None and prev is not None) else f" • `{tk}` {nm}\n"
                elif preset_name == "fscore_jump":
                    fn = r.get("fscore_now")
                    fp = r.get("fscore_past")
                    fd = r.get("fscore_delta")
                    msg += f" • `{tk}` {nm} F {fp}→{fn} (ΔF={fd})\n" if (fn is not None and fp is not None) else f" • `{tk}` {nm}\n"
                elif preset_name == "insider_cluster_buy":
                    nrep = r.get("insider_reprors")
                    nq = r.get("insider_net_qty")
                    msg += f" • `{tk}` {nm} 30일 {nrep}명 {nq:+,}주\n" if (nrep is not None and nq is not None) else f" • `{tk}` {nm}\n"
                else:
                    msg += f" • `{tk}` {nm}\n"

        try:
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        except Exception as e:
            print(f"[change_scan] 텔레그램 발송 실패: {e}")
            return

        # 쿨다운 업데이트 (발송 성공 후)
        for tk in new_sent_symbols:
            if tk:
                sent[tk] = today_str
        # 만료된 항목 정리
        sent = {t: d for t, d in sent.items() if isinstance(d, str) and d >= cutoff}
        save_json(CHANGE_SCAN_SENT_FILE, sent)
        print(f"[change_scan] {total_hits}건 발송 완료")
    except Exception as e:
        print(f"[change_scan] 오류: {e}")


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
    """매주 월요일 07:00 KST — KOSPI250 + KOSDAQ350 기준으로 stock_universe.json 자동 갱신 (~600종목)."""
    now = datetime.now(KST)
    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_universe"
    if _sent.get("universe") == _key:
        return

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
        # 비정상적으로 적으면 덮어쓰기 방지 (주말 KIS API 제한 대응)
        if len(new) < 100 and len(old) > 100:
            print(f"[universe_update] {len(new)}종목 < 100 — 비정상 응답, 기존 {len(old)}종목 유지")
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
        _sent["universe"] = _key
        save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"[universe_update] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📅 실적 캘린더 알림 (매일 07:00 KST, 3일 전 알림)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_earnings_calendar(context: ContextTypes.DEFAULT_TYPE):
    """포트폴리오+워치리스트 종목의 실적 일정 확인.
    1) events.json 확정 일정 D-3 알림 (우선)
    2) KIS 추정실적 분기 결산월 기반 (보조)
    """
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return

    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_earnings_cal"
    if _sent.get("earnings_cal") == _key:
        return

    # ── events.json 기반 D-3 알림 (확정 일정) ──
    try:
        events = load_json(f"{_DATA_DIR}/events.json", {})
        today = now.date()
        # 보유/워치 티커 수집
        wl = load_watchlist()
        pf = load_json(PORTFOLIO_FILE, {})
        us_wl = load_json(f"{_DATA_DIR}/us_watchlist.json", {})

        known_tickers = set()
        for t in wl:
            known_tickers.add(t.upper())
        for t, v in pf.items():
            if t in ("cash_krw", "cash_usd"):
                continue
            if t == "us_stocks":
                if isinstance(v, dict):
                    known_tickers.update(k.upper() for k in v.keys())
                continue
            known_tickers.add(t.upper())
        for t in us_wl:
            known_tickers.add(t.upper())

        ev_alerts = []
        for key, date_str in events.items():
            if not isinstance(date_str, str) or len(date_str) != 10:
                continue
            try:
                ev_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except Exception:
                continue
            diff = (ev_date - today).days
            if diff != 3:
                continue  # D-3만

            # 종목 실적 이벤트 (TICKER_label 형식)
            if "_" in key:
                ticker_candidate = key.split("_")[0].upper()
                if ticker_candidate in known_tickers:
                    label = key.replace("_", " ")
                    ev_alerts.append(f"🔔 *{label}* → 3일 후 ({ev_date.strftime('%m/%d')})")
                elif ticker_candidate.isupper() and len(ticker_candidate) <= 6:
                    # 보유/워치 아닌 종목은 스킵
                    continue
                else:
                    # 매크로 이벤트 형식 (예외)
                    ev_alerts.append(f"🔔 *{key}* → 3일 후 ({ev_date.strftime('%m/%d')})")
            else:
                # 매크로 이벤트 (FOMC, CPI, PPI 등) — 전체 알림
                ev_alerts.append(f"📢 *{key}* → 3일 후 ({ev_date.strftime('%m/%d')})")

        if ev_alerts:
            msg = "📅 *어닝/이벤트 D-3 알림*\n\n" + "\n".join(ev_alerts)
            try:
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            except Exception as e:
                print(f"[earnings D-3] 전송 오류: {e}")
    except Exception as e:
        print(f"[earnings D-3] 오류: {e}")

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

    _sent["earnings_cal"] = _key
    save_json(MACRO_SENT_FILE, _sent)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📅 미국 실적 캘린더 알림 (매일 07:10 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def check_us_earnings_calendar(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return

    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_us_earnings_cal"
    if _sent.get("us_earnings_cal") == _key:
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
            _sent["us_earnings_cal"] = _key
            save_json(MACRO_SENT_FILE, _sent)
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

    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_dividend_cal"
    if _sent.get("dividend_cal") == _key:
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
            _sent["dividend_cal"] = _key
            save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"[dividend_calendar] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📄 증권사 리포트 자동 수집 (매일 07:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KRX 전종목 데이터는 db_collector.collect_daily() (18:30)에서 KRX OPEN API로 수집

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# db_collector 기반 KIS API 풀수집 (db_collector.py 존재 시 활성화)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def daily_collect_job(context):
    """장후 KIS API 풀수집 (18:30 KST, 평일)."""
    if not _HAS_DB_COLLECTOR:
        return

    # 주말 이중 가드
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return

    try:
        report = await asyncio.wait_for(collect_daily(), timeout=2400)  # 40분
    except asyncio.TimeoutError:
        await context.bot.send_message(chat_id=CHAT_ID, text="⚠️ DB 수집 40분 초과 타임아웃")
        return
    except Exception as e:
        print(f"[daily_collect] 오류: {e}")
        return

    if report.get("skipped"):
        return  # 주말/공휴일 조용히 스킵

    if "error" not in report:
        _PHASE_KR = {"basic": "시세/밸류", "overtime": "시간외", "supply": "수급", "short": "공매도"}
        dur = report['duration']
        msg = (f"📊 DB 수집 완료\n"
               f"종목: {report['total']}개 | 소요: {int(dur//60)}분 {int(dur%60)}초")
        for phase, pr in report.get("phases", {}).items():
            name = _PHASE_KR.get(phase, phase)
            msg += f"\n  {name}: {pr['success']}✓ {pr['failed']}✗"
        await context.bot.send_message(chat_id=CHAT_ID, text=msg)
        try:
            from db_collector import backup_to_icloud
            backup_to_icloud()
        except Exception as e:
            print(f"[backup] iCloud 백업 실패: {e}")
    else:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"⚠️ DB 수집 실패: {report['error']}")


async def daily_collect_sanity_check(context):
    """평일 저녁 정기 자가진단 — 당일 daily_snapshot 0건이면 collect_daily 재실행.

    스케줄: 19:15 / 20:15 / 21:15 / 22:15 (18:30 정규잡 실패 방어).
    2026-04-24 18:30 미실행 사건(ccd 세션 retry로 이벤트루프 블록 추정) 재발 방지.
    """
    if not _HAS_DB_COLLECTOR:
        return
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    today = now.strftime("%Y%m%d")
    try:
        from db_collector import _get_db
        conn = _get_db()
        row = conn.execute(
            "SELECT COUNT(*) FROM daily_snapshot WHERE trade_date=?",
            (today,),
        ).fetchone()
        conn.close()
        if row and row[0] > 0:
            return  # 이미 수집 완료
    except Exception as e:
        print(f"[sanity] DB 체크 실패: {e}")
        return

    hhmm = now.strftime("%H:%M")
    print(f"[sanity {hhmm}] 당일 ({today}) daily_snapshot 0건 — collect_daily 재시작")
    try:
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"⚠️ daily_collect 미실행 감지 ({today} {hhmm}) — 재실행 시작",
        )
    except Exception:
        pass
    await daily_collect_job(context)


async def weekly_financial_job(context):
    """주 1회 재무 수집 (일요일 07:15 KST)."""
    if not _HAS_DB_COLLECTOR:
        return
    try:
        await asyncio.wait_for(collect_financial_weekly(), timeout=1800)  # 30분
        await context.bot.send_message(chat_id=CHAT_ID, text="📊 주간 재무 수집 완료")
    except asyncio.TimeoutError:
        print("[weekly_financial] 30분 타임아웃")
        await context.bot.send_message(chat_id=CHAT_ID, text="⚠️ 주간 재무 수집 30분 초과 타임아웃")
    except Exception as e:
        print(f"[weekly_financial] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 증분 수집 (매일 02:00 KST)
# collect_financial_on_disclosure: 지난 2일 정기공시만 수집 → 알파 재계산
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def daily_dart_incremental(context):
    """매일 02:00 KST — DART 신규 정기공시 증분 수집 + 알파 메트릭 재계산.

    days=2 (전날+당일)로 중복 허용 → 놓친 공시 복구 여지.
    max_calls=1000 으로 DART 분당 1000콜 상한 보호.
    신규 수집>0일 때만 텔레그램 알림.
    """
    if not _HAS_DB_COLLECTOR:
        return
    try:
        from db_collector import collect_financial_on_disclosure
    except ImportError as e:
        print(f"[dart_incr] collect_financial_on_disclosure import 실패: {e}")
        return

    try:
        # 타임아웃 20분 (최악: 1000콜 × 0.067초 ≈ 67초, 여유 포함)
        report = await asyncio.wait_for(
            collect_financial_on_disclosure(days=2, max_calls=1000),
            timeout=1200,
        )
    except asyncio.TimeoutError:
        print("[dart_incr] 20분 타임아웃")
        try:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text="⚠️ DART 증분 수집 20분 초과 타임아웃",
            )
        except Exception:
            pass
        return
    except Exception as e:
        print(f"[dart_incr] 오류: {e}")
        return

    newly = report.get("newly_collected", 0)
    if newly <= 0:
        # 조용히 스킵 (신규 공시 없음 — 대다수 평일이 그럼)
        print(f"[dart_incr] 신규 공시 없음 — 공시 {report.get('disclosures_found',0)}건, "
              f"중복 {report.get('already_in_db',0)}")
        return

    alpha = report.get("alpha_recalc") or {}
    alpha_line = ""
    if isinstance(alpha, dict) and "success" in alpha:
        alpha_line = (f"\n• 알파 재계산: {alpha.get('success', 0)}종목 "
                      f"(F:{alpha.get('fscore_filled',0)} / "
                      f"M:{alpha.get('mscore_filled',0)} / "
                      f"FCF:{alpha.get('fcf_filled',0)})")
    elif isinstance(alpha, dict) and "error" in alpha:
        alpha_line = f"\n• 알파 재계산 실패: {alpha['error'][:60]}"

    msg = (
        f"📥 DART 증분 수집 완료\n"
        f"• 공시 발견: {report.get('disclosures_found',0)}건\n"
        f"• 신규 수집: {newly}건\n"
        f"• 기존 중복: {report.get('already_in_db',0)}건\n"
        f"• 쿼터 사용: {report.get('quota_used_estimate',0)}콜\n"
        f"• 소요: {report.get('duration_sec',0):.0f}초"
        f"{alpha_line}"
    )
    try:
        await context.bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        print(f"[dart_incr] 텔레그램 전송 실패: {e}")


async def collect_reports_daily(context: ContextTypes.DEFAULT_TYPE):
    """매일 08:30 KST — 보유+감시 종목 증권사 리포트 수집"""
    if not _REPORT_AVAILABLE:
        return
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return  # 주말 스킵

    # 중복 발송 방지
    _rpt_sent = load_json(MACRO_SENT_FILE, {})
    _rpt_key = f"{now.strftime('%Y-%m-%d')}_report"
    if _rpt_sent.get("report") == _rpt_key:
        print(f"[report_daily] 이미 발송됨: {_rpt_key}, 스킵")
        return

    try:
        tickers = get_collection_tickers()
        if not tickers:
            return

        loop = asyncio.get_running_loop()
        new_reports = await loop.run_in_executor(None, collect_reports, tickers)

        if new_reports:
            def _esc(s: str) -> str:
                """Telegram Markdown v1 특수문자 이스케이프"""
                for ch in ("*", "_", "`", "["):
                    s = s.replace(ch, "\\" + ch)
                return s

            # 종목별 그룹핑 → 각 종목 최신 1건만, 리포트 수 내림차순
            from collections import defaultdict
            by_name: dict = defaultdict(list)
            for r in new_reports:
                key = r.get("name") or r.get("ticker", "?")
                by_name[key].append(r)

            lines = []
            for name, reports in sorted(by_name.items(), key=lambda x: -len(x[1])):
                latest = max(reports, key=lambda x: x.get("date", ""))
                src   = _esc(latest.get("source", ""))
                title = _esc(latest.get("title", ""))[:30]
                date  = latest.get("date", "")[-5:]
                lines.append(f"• {_esc(name)} ({len(reports)}건) — {src} \"{title}\" ({date})")

            failed = sum(1 for r in new_reports if r.get("extraction_status") == "failed")
            header = f"📄 *증권사 리포트 수집* ({len(new_reports)}건, {len(by_name)}종목"
            if failed:
                header += f", 추출실패 {failed}건"
            header += ")"
            msg = header + "\n\n" + "\n".join(lines[:15])  # 최대 15종목
            if len(by_name) > 15:
                msg += f"\n... 외 {len(by_name) - 15}종목"

            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

            # 발송 기록
            _rpt_sent["report"] = _rpt_key
            save_json(MACRO_SENT_FILE, _rpt_sent)
    except Exception as e:
        print(f"[report_daily] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📊 매크로 대시보드 (매일 18:00 + 06:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _format_overtime_movers(data: dict) -> str:
    """시간외 급등락 섹션 포맷 (pm 슬롯 전용)"""
    movers = data.get("OVERTIME_MOVERS", {})
    top    = movers.get("top", [])
    bottom = movers.get("bottom", [])
    if not top and not bottom:
        return ""
    lines = ["\n[시간외 급등락]"]
    if top:
        lines.append("📈 " + " | ".join(f"{m['name']} {m['pct']:+.1f}%" for m in top))
    if bottom:
        lines.append("📉 " + " | ".join(f"{m['name']} {m['pct']:+.1f}%" for m in bottom))
    return "\n".join(lines)


async def macro_dashboard(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    # 18:35 실행: 평일만 / 06:00 실행: 일요일 제외 (토요일은 금요일 결과)
    if now.hour >= 12 and now.weekday() >= 5:
        return
    if now.hour < 12 and now.weekday() == 6:
        return

    # 중복 발송 방지: 같은 날짜_슬롯이면 스킵
    slot = "pm" if now.hour >= 12 else "am"
    slot_key = f"{now.strftime('%Y-%m-%d')}_{slot}"
    sent_data = load_json(MACRO_SENT_FILE, {})
    if sent_data.get("last") == slot_key:
        print(f"[macro_dashboard] 이미 발송됨: {slot_key}, 스킵")
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

        # pm 슬롯에만 시간외 급등락 추가
        if slot == "pm":
            overtime_section = _format_overtime_movers(data)
            if overtime_section:
                msg += overtime_section

        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

        # 발송 성공 후 기록 (기존 키 보존)
        sent_data["last"] = slot_key
        save_json(MACRO_SENT_FILE, sent_data)
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

        # 관심 기업명 목록 (워치리스트 + 포트폴리오 + watchalert)
        watchlist = load_watchlist()
        portfolio = load_json(PORTFOLIO_FILE, {})
        wa = load_json(WATCHALERT_FILE, {})
        wl_names = list(watchlist.values())
        wl_names += [v.get("name", "") for k, v in portfolio.items()
                     if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)]
        wl_names += [v.get("name", "") for v in wa.values() if isinstance(v, dict)]
        wl_names = list(set(n for n in wl_names if n))

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

        # 요약 파싱 대상 키워드
        _DART_SUMMARY_KEYWORDS = (
            "잠정실적", "영업(잠정)실적",
            "자기주식취득결정", "자기주식 취득",
            "주식소각", "자기주식소각",
            "현금배당", "현금·현물배당", "현금ㆍ현물배당", "배당결정",
            "풍문", "해명",
        )

        for d in new_disclosures[:5]:  # 최대 5개
            corp = d.get("corp_name", "?")
            title = d.get("report_nm", "?")
            date = d.get("rcept_dt", "?")
            rcept_no = d.get("rcept_no", "")
            link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

            msg += f"🏢 *{corp}*\n"
            msg += f"📄 {title}\n"
            msg += f"📅 {date}\n"

            # 🆕 요약 시도 (실패해도 알림은 계속 발송)
            if any(kw in title for kw in _DART_SUMMARY_KEYWORDS):
                try:
                    stock_code = (d.get("stock_code", "") or "").strip() or "000000"
                    body_text = await fetch_and_cache_disclosure(stock_code, rcept_no)
                    if body_text:
                        parsed = parse_disclosure_summary(title, body_text)
                        if parsed and parsed.get("summary"):
                            for line in parsed["summary"]:
                                msg += f"{line}\n"
                except Exception as _e:
                    print(f"[DART 알림] 요약 파싱 실패 {rcept_no}: {_e}")

            msg += f"🔗 [공시 원문]({link})\n\n"

            new_ids.append(rcept_no)

        msg += "💡 Claude에서 영향 분석하세요"

        # 발송 전에 먼저 저장 (중복 발송 방지)
        seen_ids.update(new_ids)
        seen_list = list(seen_ids)[-500:]
        save_json(DART_SEEN_FILE, {"ids": seen_list})

        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown", disable_web_page_preview=True)

    except Exception as e:
        print(f"DART 체크 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림: 내부자 클러스터 매수 감지 (매일 20:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSIDER_SENT_FILE = f"{_DATA_DIR}/insider_sent.json"
INSIDER_CLUSTER_MIN_BUYERS = 3  # 30일 내 매수자 3명+ 시 플래그
INSIDER_COOLDOWN_DAYS = 7       # 종목당 알림 재발송 쿨다운


async def check_insider_cluster(context: ContextTypes.DEFAULT_TYPE):
    """워치/보유 종목의 DART 임원 소유보고 수집 → 30일 3명+ 매수 클러스터 감지."""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    if not DART_API_KEY:
        return

    # 대상 종목: 워치 + 보유 + 매수감시 (한국만)
    watchlist = load_watchlist()
    portfolio = load_json(PORTFOLIO_FILE, {})
    wa = load_watchalert()
    tickers: dict = {}
    for k, v in watchlist.items():
        if not _is_us_ticker(k):
            tickers[k] = v
    for k, v in portfolio.items():
        if k in ("us_stocks", "cash_krw", "cash_usd"):
            continue
        if isinstance(v, dict) and not _is_us_ticker(k):
            tickers[k] = v.get("name", "")
    for k, v in wa.items():
        if not _is_us_ticker(k) and isinstance(v, dict):
            tickers[k] = v.get("name", "")

    if not tickers:
        return

    try:
        # corp_code 매핑 (universe 기반)
        universe = get_stock_universe() or {}
        corp_map = await get_dart_corp_map(universe) if universe else {}
        if not corp_map:
            print("[insider] corp_map 없음, 스킵")
            return

        # 수집
        stats = await collect_insider_for_tickers(list(tickers.keys()), corp_map)

        # 쿨다운 체크 & 집계
        sent = load_json(INSIDER_SENT_FILE, {})
        cooldown_cutoff = (now - timedelta(days=INSIDER_COOLDOWN_DAYS)).strftime("%Y-%m-%d")
        today = now.strftime("%Y-%m-%d")

        alerts = []
        for sym in stats.keys():
            last_sent = sent.get(sym, "")
            if last_sent and last_sent >= cooldown_cutoff:
                continue  # 쿨다운 중
            agg = aggregate_insider_cluster(sym, days=30)
            if agg["buyers"] >= INSIDER_CLUSTER_MIN_BUYERS and agg["buy_qty"] > agg["sell_qty"]:
                alerts.append((sym, tickers.get(sym, sym), agg))

        if not alerts:
            return

        msg = f"🕵️ *내부자 클러스터 매수 감지* ({now.strftime('%m/%d %H:%M')})\n\n"
        for sym, name, agg in alerts[:5]:
            msg += f"🏢 *{name}* ({sym})\n"
            msg += f"📅 30일: 매수 {agg['buyers']}명 / 매도 {agg['sellers']}명\n"
            msg += f"📊 순매수 {agg['buy_qty'] - agg['sell_qty']:,}주 "
            msg += f"(매수 {agg['buy_qty']:,} / 매도 {agg['sell_qty']:,})\n"
            # 최근 3건 매수
            recent_buys = [r for r in agg["recent"] if (r.get("delta") or 0) > 0][:3]
            for r in recent_buys:
                msg += f"  • {r['date']} {r['name']}({r['ofcps']}) +{r['delta']:,}\n"
            msg += "\n"
            sent[sym] = today

        save_json(INSIDER_SENT_FILE, sent)
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[insider] 체크 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림: 워치 변화 감지 (19:00)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def watch_change_detect(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return

    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_watch_change"
    if _sent.get("watch_change") == _key:
        return

    try:
        db = load_krx_db()
        if not db:
            return
        stocks = db.get("stocks", {})
        today = now.strftime("%Y-%m-%d")

        # 대상: 보유 + 워치리스트
        portfolio = load_json(PORTFOLIO_FILE, {})
        wa = load_watchalert()
        watch_tickers = set()
        for k in portfolio:
            if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(portfolio[k], dict):
                watch_tickers.add(k)
        for k in wa:
            if not _is_us_ticker(k):
                watch_tickers.add(k)

        # 당일 중복 방지
        change_sent_file = f"{REGIME_STATE_FILE.rsplit('/', 1)[0]}/watch_change_sent.json"
        change_sent = load_json(change_sent_file, {})
        if change_sent.get("date") == today:
            return

        alerts = []
        for ticker in watch_tickers:
            s = stocks.get(ticker, {})
            if not s:
                continue
            name = s.get("name", ticker)

            # 감시가 근접 5%
            if ticker in wa:
                buy_p = float(wa[ticker].get("buy_price", 0) or 0)
                cur = s.get("close", 0)
                if buy_p > 0 and cur > 0:
                    gap = (cur - buy_p) / buy_p * 100
                    if 0 <= gap <= 5:
                        alerts.append(f"👀 {name}: 감시가 {buy_p:,.0f}원 근접 ({gap:.1f}%)")

            # 외인 매수 전환 (foreign_trend_5d >= 0.6)
            ft5 = s.get("foreign_trend_5d")
            ft20 = s.get("foreign_trend_20d")
            if ft5 is not None and ft5 >= 0.6 and ft20 is not None and ft20 < 0.4:
                alerts.append(f"🔥 {name}: 외인 매수 전환 (5d {ft5:.0%} vs 20d {ft20:.0%})")

            # 공매도 비중 과열
            sr = s.get("short_ratio", 0)
            if sr and sr >= 10:
                alerts.append(f"⚠️ {name}: 공매도 {sr:.1f}% 과열")

            # 공매도 숏커버
            sc5 = s.get("short_change_5d")
            if sc5 is not None and sc5 <= -20:
                alerts.append(f"📊 {name}: 숏커버 진행 ({sc5:+.1f}%)")

            # 이평선 수렴
            spread = s.get("ma_spread")
            if spread is not None and abs(spread) < 3:
                alerts.append(f"📊 {name}: 이평선 수렴 ({spread:+.1f}%)")

            # RSI 과매도
            rsi = s.get("rsi14")
            if rsi is not None and rsi < 30:
                alerts.append(f"📉 {name}: RSI {rsi:.1f} 과매도")

        if alerts:
            msg = f"📡 *워치 변화 감지* ({now.strftime('%m/%d')})\n\n" + "\n".join(alerts)
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

        save_json(change_sent_file, {"date": today, "sent": True})
        _sent["watch_change"] = _key
        save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"watch_change_detect 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림: 레짐 전환 가이드 (전환 확정 시)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def regime_transition_alert(context: ContextTypes.DEFAULT_TYPE):
    try:
        state = load_json(REGIME_STATE_FILE, {})
        prev_en = state.get("prev_regime", "")
        cur = state.get("current", {})
        curr_en = cur.get("current", "")
        if not prev_en or not curr_en or prev_en == curr_en:
            return

        emoji_map = {"offensive": "🟢", "neutral": "🟡", "crisis": "🔴"}
        prev_e = emoji_map.get(prev_en, "?")
        curr_e = emoji_map.get(curr_en, "?")

        # 전환당 1회만
        trans_file = f"{REGIME_STATE_FILE.rsplit('/', 1)[0]}/regime_transition_sent.json"
        trans_sent = load_json(trans_file, {})
        key = f"{prev_e}→{curr_e}"
        if trans_sent.get("transition") == key:
            return

        guides = {
            "🔴→🟡": "1. A등급 감시가 재평가\n2. B등급 이하 비중 초과분 트림 검토\n3. 신규 진입: 확신 높은 것만, 소규모 분할\n4. 현금 비율: 25% → 15% OK",
            "🟡→🟢": "1. 핵심 섹터 적극 확대\n2. A등급 풀사이즈 가능\n3. 감시가 터치 시 즉시 대응",
            "🟢→🟡": "1. 신규 소규모만\n2. 기존 포지션 관리 집중\n3. 손절선 점검",
            "🟡→🔴": "1. 신규 동결\n2. 현금 25%+ 확보\n3. C/D등급 점검\n4. 손절선 15% → 10% 타이트",
        }
        guide = guides.get(key, "레짐 전환 확인 필요")

        ind = cur.get("indicators", {})
        sp = ind.get("sp500_vs_200ma", {})
        vix = ind.get("vix", {})
        msg = f"🔄 *레짐 전환 확정* {prev_e} → {curr_e}\n"
        msg += f"S&P {sp.get('distance_pct', '?')}% from 200MA | VIX {vix.get('value', '?')}\n\n"
        msg += f"📋 행동 가이드:\n{guide}"

        # 감시가 근접 A등급
        wa = load_watchalert()
        near_a = []
        for t, info in wa.items():
            if info.get("grade", "").upper() == "A":
                buy_p = float(info.get("buy_price", 0) or 0)
                if buy_p > 0:
                    near_a.append(f"• {info.get('name', t)} {buy_p:,.0f}")
        if near_a:
            msg += "\n\n👀 A등급 감시 종목:\n" + "\n".join(near_a[:5])

        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        save_json(trans_file, {"transition": key, "date": datetime.now(KST).strftime("%Y-%m-%d")})
    except Exception as e:
        print(f"regime_transition_alert 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림: Sunday 30 리마인더 (일 19:00)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def sunday_30_reminder(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_sunday_30"
    if _sent.get("sunday_30") == _key:
        return

    try:
        msg = f"📋 *주간점검 Sunday 30 리마인더* ({now.strftime('%m/%d')})\n\n"

        # 레짐
        r_en, r_emoji = _read_regime()
        state_cur = load_json(REGIME_STATE_FILE, {}).get("current", {})
        r_score = float(state_cur.get("debounce_count", 0) or 0)
        msg += f"[레짐] {r_emoji} ({r_en}) {r_score:.0f}일차\n"

        # 포트 요약
        pf = load_json(PORTFOLIO_FILE, {})
        kr_total = sum(float(v.get("avg_price", 0)) * float(v.get("qty", 0))
                       for k, v in pf.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict))
        us_pf = pf.get("us_stocks", {})
        us_total = sum(float(v.get("avg_price", 0)) * float(v.get("qty", 0)) for v in us_pf.values())
        cash_k = float(pf.get("cash_krw", 0) or 0)
        cash_u = float(pf.get("cash_usd", 0) or 0)
        msg += f"[포트] KR {kr_total/10000:,.0f}만 | US ${us_total:,.0f} | 현금 {cash_k:,.0f}원/${cash_u:,.0f}\n"

        # 포트 건강 위반
        warnings = []
        total_asset = kr_total + cash_k  # 간이
        if total_asset > 0:
            for t, v in {k: v for k, v in pf.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)}.items():
                val = float(v.get("avg_price", 0)) * float(v.get("qty", 0))
                pct = val / total_asset * 100
                if pct > 35:
                    warnings.append(f"• {v.get('name', t)} {pct:.0f}% → 한도 35% 초과")

        if warnings:
            msg += "\n⚠️ 점검 필요:\n" + "\n".join(warnings) + "\n"

        # 감시가 근접 TOP 3
        wa = load_watchalert()
        near = []
        db = load_krx_db()
        db_stocks = db.get("stocks", {}) if db else {}
        for t, info in wa.items():
            buy_p = float(info.get("buy_price", 0) or 0)
            if buy_p <= 0:
                continue
            s = db_stocks.get(t, {})
            cur = s.get("close", 0)
            if cur > 0:
                gap = (cur - buy_p) / buy_p * 100
                if gap <= 10:
                    near.append((info.get("name", t), buy_p, gap))
        near.sort(key=lambda x: x[2])
        if near:
            msg += "\n👀 감시가 근접:\n"
            for name, bp, gap in near[:3]:
                msg += f"• {name} {bp:,.0f} ({gap:+.1f}%)\n"

        # 이벤트
        events = load_json(EVENTS_FILE, {})
        next_week = []
        for i in range(7):
            d = (now + timedelta(days=i)).strftime("%Y-%m-%d")
            ev = events.get(d, "")
            if ev:
                next_week.append(f"• {d[5:]} {ev}")
        if next_week:
            msg += "\n📅 이번 주 이벤트:\n" + "\n".join(next_week) + "\n"

        # Sunday 30 체크리스트
        msg += (
            "\n━━━━━━━━━━━━━━━━━━\n"
            "📋 *Sunday 30 체크리스트* (30분)\n\n"
            "0~3분: 레짐+알림\n"
            " □ get\\_regime → 변화?\n"
            " □ get\\_alerts → triggered?\n\n"
            "3~8분: 스마트머니 스캔\n"
            " □ get\\_supply(combined\\_rank)\n"
            " □ get\\_change\\_scan\n\n"
            "8~15분: thesis 스캔\n"
            " □ 웹서치: 산업 트렌드\n"
            " □ get\\_macro(op\\_growth)\n\n"
            "15~25분: 1종목 딥체크\n"
            " □ get\\_stock\\_detail\n"
            " □ get\\_consensus\n"
            " □ manage\\_report\n\n"
            "25~30분: 기록+결론\n"
            " □ set\\_alert(decision)\n"
            " □ 결론: 늘릴것/줄일것/유지"
        )

        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        _sent["sunday_30"] = _key
        save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"sunday_30_reminder 오류: {e}")


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
async def weekly_sanity_check(context):
    """매주 일요일 07:05: 최근 영업일 5일 daily_snapshot 존재 확인"""
    try:
        from db_collector import _get_db
        conn = _get_db()
        cur = conn.execute(
            "SELECT trade_date, COUNT(*) FROM daily_snapshot "
            "WHERE trade_date >= ? GROUP BY trade_date ORDER BY trade_date DESC",
            ((datetime.now(KST) - timedelta(days=9)).strftime("%Y%m%d"),)
        )
        rows = cur.fetchall()
        conn.close()
        # 지난 5 영업일(월-금) 역산
        bizdays = []
        d = datetime.now(KST).date() - timedelta(days=1)
        while len(bizdays) < 5:
            if d.weekday() < 5:
                bizdays.append(d.strftime("%Y%m%d"))
            d -= timedelta(days=1)
        have = {r[0] for r in rows if r[1] > 1500}
        missing = [b for b in bizdays if b not in have]
        if missing:
            msg = f"⚠️ daily_snapshot 누락 영업일: {', '.join(missing)}"
            await context.bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        print(f"[weekly_sanity] 실패: {e}")


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
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
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
                    new_n = _save_us_ratings_to_db(result)
                    inserted_total += new_n
                    try:
                        _save_consensus_snapshot(result)
                    except Exception:
                        pass
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
        result = await asyncio.to_thread(sync_us_analyst_master)
        msg = (
            "🔄 US 애널 마스터 동기화 완료\n"
            f"• 신규 애널: {result['inserted']}명\n"
            f"• 자동 watched=1: {result['auto_watched']}명 (별점≥{result['min_stars']} 콜≥{result['min_calls']})\n"
            f"• 마스터 총: {result['total_master']}명 / watched: {result['total_watched']}명"
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
                    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
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
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
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
# ━━━━━━━━━━━━━━━━━━━━━━━━━
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

    # 자동 알림 스케줄
    jq = app.job_queue
    jq.run_repeating(check_stoploss, interval=600, first=60, name="stoploss")
    jq.run_repeating(check_anomaly, interval=1800, first=120, name="anomaly")
    # 환율 알림: 매크로 대시보드(macro_pm/macro_am)로 통합 완료
    jq.run_repeating(check_dart_disclosure, interval=300, first=180, name="dart")  # 5분 (실시간화, 8~20시 내부 필터)
    # 모든 run_daily time은 KST-aware(tzinfo=KST)로 지정 → Railway(UTC 서버)에서도 정확한 시각에 실행됨
    jq.run_daily(daily_kr_summary, time=dtime(15, 40, tzinfo=KST), days=(0,1,2,3,4), name="kr_summary")
    # 미국 장 마감 요약: 서머타임(05:05 KST) + 표준시(06:05 KST) 두 시각 등록
    # _is_us_market_closed() 가드로 실제 마감 30분 이내일 때만 발송, 이중 발송 없음
    jq.run_daily(us_market_summary, time=dtime(5,  5, tzinfo=KST), days=(1,2,3,4,5), name="us_summary_dst")
    jq.run_daily(us_market_summary, time=dtime(6,  5, tzinfo=KST), days=(1,2,3,4,5), name="us_summary_std")
    jq.run_daily(check_supply_drain,   time=dtime(15, 40, tzinfo=KST), days=(0,1,2,3,4), name="supply_drain")
    jq.run_daily(momentum_exit_check,  time=dtime(16, 30, tzinfo=KST), days=(0,1,2,3,4), name="momentum_check")
    jq.run_daily(snapshot_and_drawdown, time=dtime(15, 50, tzinfo=KST), days=(0,1,2,3,4), name="snapshot_dd")
    jq.run_daily(weekly_review,           time=dtime(7,  0, tzinfo=KST), days=(5,), name="weekly")
    jq.run_daily(weekly_universe_update,  time=dtime(7,  0, tzinfo=KST), days=(0,), name="universe_update")
    jq.run_daily(weekly_consensus_update, time=dtime(7,  5, tzinfo=KST), days=(6,), name="consensus_update")
    jq.run_daily(daily_consensus_check,  time=dtime(19, 30, tzinfo=KST), days=(0,1,2,3,4), name="daily_consensus")
    jq.run_daily(daily_change_scan_alert, time=dtime(19,  5, tzinfo=KST), days=(0,1,2,3,4), name="daily_change_scan")
    jq.run_daily(auto_backup,            time=dtime(22, 0, tzinfo=KST), name="auto_backup")
    # 매크로 대시보드: 18:55(daily_collect 18:30+~21분 완료 후) + 06:00(미국장 마감)
    jq.run_daily(macro_dashboard, time=dtime(18, 55, tzinfo=KST), name="macro_pm")
    jq.run_daily(macro_dashboard, time=dtime(6,  0, tzinfo=KST), name="macro_am")
    # 실적/배당 캘린더: 매일 07:00 KST 평일만
    jq.run_daily(check_earnings_calendar,  time=dtime(7,  0, tzinfo=KST), days=(0,1,2,3,4), name="earnings_cal")
    jq.run_daily(check_dividend_calendar,  time=dtime(7,  0, tzinfo=KST), days=(0,1,2,3,4), name="dividend_cal")
    jq.run_daily(check_us_earnings_calendar, time=dtime(7, 10, tzinfo=KST), days=(0,1,2,3,4), name="us_earnings_cal")
    jq.run_daily(collect_reports_daily,    time=dtime(8, 30, tzinfo=KST), days=(0,1,2,3,4), name="report_collect")
    # KRX 전종목 DB 갱신: db_collector가 18:30에 KRX OPEN API로 수집
    jq.run_daily(daily_collect_job,       time=dtime(18, 30, tzinfo=KST), days=(0,1,2,3,4), name="daily_collect")
    # 자가진단: 18:30 정규잡 실패 방어 (2026-04-24 미실행 사건 재발 방지)
    jq.run_daily(daily_collect_sanity_check, time=dtime(19, 15, tzinfo=KST), days=(0,1,2,3,4), name="collect_sanity_1")
    jq.run_daily(daily_collect_sanity_check, time=dtime(20, 15, tzinfo=KST), days=(0,1,2,3,4), name="collect_sanity_2")
    jq.run_daily(daily_collect_sanity_check, time=dtime(21, 15, tzinfo=KST), days=(0,1,2,3,4), name="collect_sanity_3")
    jq.run_daily(daily_collect_sanity_check, time=dtime(22, 15, tzinfo=KST), days=(0,1,2,3,4), name="collect_sanity_4")
    jq.run_daily(daily_us_rating_scan,    time=dtime(7, 30, tzinfo=KST), days=(0,1,2,3,4,5,6), name="us_ratings")
    # 주간 S&P 500 유니버스 스캔 — 일요일 03:00 KST (애널 풀 축적용, 약 17분 소요)
    jq.run_daily(weekly_us_ratings_universe_scan, time=dtime(3, 0, tzinfo=KST), days=(6,), name="weekly_us_harvest")
    # harvest 33분 + 여유 → 04:00에 마스터 sync (ratings → us_analysts 자동 인구)
    jq.run_daily(weekly_us_analyst_sync,        time=dtime(4, 0, tzinfo=KST), days=(6,), name="weekly_us_analyst_sync")
    # 미국 보유 종목 실시간 감시 (ET 12:00 / 16:30 — DST 자동, 평일만. ET는 kis_api에서 import)
    jq.run_daily(hourly_us_holdings_check, time=dtime(12, 0, tzinfo=ET), days=(0,1,2,3,4), name="us_holdings_noon")
    jq.run_daily(hourly_us_holdings_check, time=dtime(16, 30, tzinfo=ET), days=(0,1,2,3,4), name="us_holdings_close")
    # 주간 미국 애널 리포트 — 일요일 19:00 KST (다음주 월요일 준비)
    jq.run_daily(weekly_us_analyst_report, time=dtime(19, 0, tzinfo=KST), days=(6,), name="weekly_us_analyst")
    jq.run_daily(weekly_financial_job,    time=dtime(7,  15, tzinfo=KST), days=(6,),         name="weekly_financial")
    # DART 증분 수집: 매일 02:00 KST — 신규 정기공시만 수집 후 알파 재계산
    jq.run_daily(daily_dart_incremental,  time=dtime(2,  0, tzinfo=KST),                     name="dart_incremental")
    jq.run_daily(watch_change_detect,     time=dtime(19, 0, tzinfo=KST), days=(0,1,2,3,4), name="watch_change")
    jq.run_daily(check_insider_cluster,   time=dtime(20, 0, tzinfo=KST), days=(0,1,2,3,4), name="insider_cluster")
    jq.run_daily(sunday_30_reminder,      time=dtime(19, 0, tzinfo=KST), days=(6,), name="sunday_30")
    # 주간 무결성 체크: 매주 일요일 07:05 KST — daily_snapshot 영업일 누락 감시
    jq.run_daily(weekly_sanity_check,     time=dtime(7,  5, tzinfo=KST), days=(6,), name="weekly_sanity")
    jq.run_repeating(regime_transition_alert, interval=3600, first=300, name="regime_transition")

    port = int(os.environ.get("PORT", 8080))
    print(f"봇 실행! MCP SSE 서버 포트: {port}")
    asyncio.run(_run_all(app, port))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 웹 대시보드 (/dash)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_DASH_CSS = """
<style>
:root{--bg:#1a1a2e;--bg2:#16213e;--fg:#e0e0e0;--fg2:#a0a0b0;--accent:#4fc3f7;--red:#ef5350;--green:#66bb6a;--border:#2a2a4a}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,sans-serif;padding:16px;max-width:860px;margin:0 auto;line-height:1.6}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:1.6em;margin:16px 0 8px}h2{font-size:1.3em;margin:24px 0 8px;border-bottom:1px solid var(--border);padding-bottom:4px}
h3{font-size:1.1em;margin:16px 0 4px;color:var(--accent)}
p{margin:8px 0}
code{background:var(--bg2);padding:2px 6px;border-radius:3px;font-family:'SF Mono',monospace;font-size:0.9em}
pre{background:var(--bg2);padding:12px;border-radius:6px;overflow-x:auto;margin:8px 0;font-size:0.85em;border:1px solid var(--border)}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:0.9em}
th{background:var(--bg2);padding:8px;text-align:left;border:1px solid var(--border);font-weight:600}
td{padding:6px 8px;border:1px solid var(--border)}
tr:nth-child(even){background:rgba(255,255,255,0.03)}
ul,ol{margin:8px 0 8px 20px}li{margin:2px 0}
.check{display:flex;align-items:center;gap:6px;margin:2px 0}
.check input{width:16px;height:16px;accent-color:var(--accent)}
.section{margin:24px 0;padding:16px;background:var(--bg2);border-radius:8px;border:1px solid var(--border)}
.nav{display:flex;gap:12px;padding:8px 0;border-bottom:1px solid var(--border);margin-bottom:16px;flex-wrap:wrap}
.nav a{padding:4px 10px;border-radius:4px;background:var(--bg2);font-size:0.9em}
.pos{color:var(--green)}.neg{color:var(--red)}
@media(max-width:600px){body{padding:8px}table{font-size:0.8em}th,td{padding:4px}}
</style>
"""


def _md_to_html(md: str) -> str:
    """Markdown → HTML (정규식 기반 경량 변환)."""
    lines = md.split("\n")
    html_lines = []
    in_code = False
    in_table = False
    in_list = False

    for line in lines:
        # code block
        if line.strip().startswith("```"):
            if in_code:
                html_lines.append("</code></pre>")
                in_code = False
            else:
                lang = line.strip()[3:].strip()
                html_lines.append(f"<pre><code>")
                in_code = True
            continue
        if in_code:
            html_lines.append(line.replace("<", "&lt;").replace(">", "&gt;"))
            continue

        stripped = line.strip()

        # close table
        if in_table and not stripped.startswith("|"):
            html_lines.append("</tbody></table>")
            in_table = False

        # close list
        if in_list and not stripped.startswith("- ") and not stripped.startswith("* ") and stripped:
            html_lines.append("</ul>")
            in_list = False

        # empty line
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("")
            continue

        # headers
        if stripped.startswith("### "):
            html_lines.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif stripped.startswith("> "):
            html_lines.append(f"<blockquote style='border-left:3px solid var(--accent);padding-left:12px;color:var(--fg2)'>{_inline(stripped[2:])}</blockquote>")
        # checkbox
        elif stripped.startswith("- [x] ") or stripped.startswith("- [X] "):
            html_lines.append(f"<div class='check'><input type='checkbox' checked disabled><span style='text-decoration:line-through;color:var(--fg2)'>{_inline(stripped[6:])}</span></div>")
        elif stripped.startswith("- [ ] "):
            html_lines.append(f"<div class='check'><input type='checkbox' disabled><span>{_inline(stripped[6:])}</span></div>")
        # table
        elif stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue  # separator row
            if not in_table:
                html_lines.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                html_lines.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
        # list
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline(stripped[2:])}</li>")
        # hr
        elif stripped.startswith("---"):
            html_lines.append("<hr style='border-color:var(--border);margin:16px 0'>")
        else:
            html_lines.append(f"<p>{_inline(stripped)}</p>")

    if in_code:
        html_lines.append("</code></pre>")
    if in_table:
        html_lines.append("</tbody></table>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def _md_to_html_editable(md: str, file_key: str) -> str:
    """Markdown → HTML (체크박스 클릭 가능 버전, data-* 속성 추가).

    file_key: "dev" | "invest" | "todo" — POST /dash/todo/toggle 에서 파일 식별용.
    각 체크박스 라인에 data-todo-file/line/hash 속성 부여.
    라인 번호는 원본 md 의 1-indexed (요청 시 그대로 수정).
    """
    lines = md.split("\n")
    html_lines = []
    in_code = False
    in_table = False
    in_list = False

    for idx, line in enumerate(lines):
        line_num = idx + 1  # 1-indexed

        # code block
        if line.strip().startswith("```"):
            if in_code:
                html_lines.append("</code></pre>")
                in_code = False
            else:
                lang = line.strip()[3:].strip()
                html_lines.append(f"<pre><code>")
                in_code = True
            continue
        if in_code:
            html_lines.append(line.replace("<", "&lt;").replace(">", "&gt;"))
            continue

        stripped = line.strip()

        # close table
        if in_table and not stripped.startswith("|"):
            html_lines.append("</tbody></table>")
            in_table = False

        # close list
        if in_list and not stripped.startswith("- ") and not stripped.startswith("* ") and stripped:
            html_lines.append("</ul>")
            in_list = False

        # empty line
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("")
            continue

        # headers
        if stripped.startswith("### "):
            html_lines.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif stripped.startswith("> "):
            html_lines.append(f"<blockquote style='border-left:3px solid var(--accent);padding-left:12px;color:var(--fg2)'>{_inline(stripped[2:])}</blockquote>")
        # checkbox (editable)
        elif stripped.startswith("- [x] ") or stripped.startswith("- [X] "):
            line_hash = hashlib.sha1(line.encode("utf-8")).hexdigest()[:12]
            html_lines.append(
                f"<div class='check'>"
                f"<input type='checkbox' checked "
                f"data-todo-file='{_html.escape(file_key)}' "
                f"data-todo-line='{line_num}' "
                f"data-todo-hash='{line_hash}'>"
                f"<span style='text-decoration:line-through;color:var(--fg2)'>{_inline(stripped[6:])}</span>"
                f"</div>"
            )
        elif stripped.startswith("- [ ] "):
            line_hash = hashlib.sha1(line.encode("utf-8")).hexdigest()[:12]
            html_lines.append(
                f"<div class='check'>"
                f"<input type='checkbox' "
                f"data-todo-file='{_html.escape(file_key)}' "
                f"data-todo-line='{line_num}' "
                f"data-todo-hash='{line_hash}'>"
                f"<span>{_inline(stripped[6:])}</span>"
                f"</div>"
            )
        # table
        elif stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue  # separator row
            if not in_table:
                html_lines.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                html_lines.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
        # list
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline(stripped[2:])}</li>")
        # hr
        elif stripped.startswith("---"):
            html_lines.append("<hr style='border-color:var(--border);margin:16px 0'>")
        else:
            html_lines.append(f"<p>{_inline(stripped)}</p>")

    if in_code:
        html_lines.append("</code></pre>")
    if in_table:
        html_lines.append("</tbody></table>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def _atomic_write(filepath: str, content: str) -> None:
    """파일 쓰기 전 임시 파일에 쓰고 os.replace 로 교체 (전원 나가도 안전)."""
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, filepath)


# 편집 대상 TODO 파일 화이트리스트 (경로 조작 방어)
_TODO_FILE_MAP = {
    "dev": "TODO_dev.md",
    "invest": "TODO_invest.md",
    "todo": "TODO.md",
}


_SAFE_URL_SCHEMES = ("http://", "https://", "/", "#", "mailto:")


def _sanitize_url(url: str) -> str:
    """href URL 화이트리스트 — javascript:/data: 등 XSS 벡터 차단 + 속성 탈출 방지.

    허용: http://, https://, 절대경로(/), 앵커(#), mailto:
    그 외 (javascript:, data:, vbscript: 등) → "#" 으로 치환.
    쌍따옴표 이스케이프로 href="" 속성 탈출 방어.
    """
    u = url.strip()
    u_lower = u.lower()
    if not any(u_lower.startswith(s) for s in _SAFE_URL_SCHEMES):
        return "#"
    # href 속성값 탈출 방지
    return u.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(text: str) -> str:
    """인라인 마크다운 (bold, code, link)."""
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # 링크: URL 은 _sanitize_url 로 스킴 화이트리스트 + 속성 이스케이프
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        lambda m: f'<a href="{_sanitize_url(m.group(2))}">{m.group(1)}</a>',
        text,
    )
    return text


def _json_to_table(data, title: str = "") -> str:
    """JSON 데이터를 HTML 테이블로."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        keys = list(data[0].keys())
        rows = "".join(
            "<tr>" + "".join(f"<td>{_html.escape(str(r.get(k, '')))}</td>" for k in keys) + "</tr>"
            for r in data[:50]
        )
        header = "".join(f"<th>{_html.escape(str(k))}</th>" for k in keys)
        return f"<table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>"
    elif isinstance(data, dict):
        rows = "".join(
            f"<tr><td><strong>{_html.escape(str(k))}</strong></td><td>{_format_val(v)}</td></tr>"
            for k, v in list(data.items())[:100]
        )
        return f"<table>{rows}</table>"
    return f"<pre>{_html.escape(json.dumps(data, ensure_ascii=False, indent=2)[:5000])}</pre>"


def _format_val(v):
    if isinstance(v, dict):
        return "<code>" + _html.escape(json.dumps(v, ensure_ascii=False)[:200]) + "</code>"
    if isinstance(v, list):
        return f"[{len(v)} items]"
    if isinstance(v, (int, float)) and abs(v) >= 10000:
        return f"{v:,.0f}"
    return _html.escape(str(v))


def _build_portfolio_html() -> str:
    """portfolio.json + KRX DB 현재가 → 포트폴리오 테이블."""
    pf = load_json(PORTFOLIO_FILE, {})
    kr = {k: v for k, v in pf.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)}
    us = pf.get("us_stocks", {})

    # KRX DB에서 현재가
    db = load_krx_db()
    db_stocks = db.get("stocks", {}) if db else {}
    db_date = db.get("date", "") if db else ""

    html = ""
    kr_total_cost = kr_total_eval = 0
    if kr:
        html += f"<h3>🇰🇷 한국</h3><table><thead><tr><th>종목</th><th>수량</th><th>평단가</th><th>현재가</th><th>손익</th></tr></thead><tbody>"
        for t, v in kr.items():
            qty = int(v.get("qty", 0))
            avg = int(v.get("avg_price", 0))
            cur = db_stocks.get(t, {}).get("close", 0)
            cost = qty * avg
            ev = qty * cur if cur else 0
            kr_total_cost += cost
            kr_total_eval += ev
            if cur and avg:
                pnl_pct = (cur - avg) / avg * 100
                cls = "pos" if pnl_pct >= 0 else "neg"
                pnl_str = f"<span class='{cls}'>{pnl_pct:+.1f}%</span>"
                cur_str = f"{cur:,}원"
            else:
                pnl_str = "-"
                cur_str = "-"
            html += f"<tr><td>{_html.escape(v.get('name', t))}</td><td>{qty:,}</td><td>{avg:,}원</td><td>{cur_str}</td><td>{pnl_str}</td></tr>"
        html += "</tbody></table>"
        if kr_total_cost > 0:
            kr_pnl = (kr_total_eval - kr_total_cost) / kr_total_cost * 100
            cls = "pos" if kr_pnl >= 0 else "neg"
            html += f"<p>KR 합계: 평가 {kr_total_eval:,.0f}원 / 매입 {kr_total_cost:,.0f}원 = <span class='{cls}'>{kr_pnl:+.1f}%</span></p>"

    if us:
        html += "<h3>🇺🇸 미국</h3><table><thead><tr><th>종목</th><th>수량</th><th>평단가</th></tr></thead><tbody>"
        for t, v in us.items():
            html += f"<tr><td>{_html.escape(v.get('name', t))} ({_html.escape(t)})</td><td>{int(v.get('qty', 0)):,}</td><td>${float(v.get('avg_price', 0)):,.2f}</td></tr>"
        html += "</tbody></table>"

    cash_k = float(pf.get("cash_krw", 0) or 0)
    cash_u = float(pf.get("cash_usd", 0) or 0)
    if cash_k or cash_u:
        html += f"<p>💰 현금: {cash_k:,.0f}원 / ${cash_u:,.2f}</p>"
    if db_date:
        html += f"<p style='color:var(--fg2);font-size:0.85em'>현재가 기준: {db_date}</p>"
    return html or "<p>포트폴리오 비어있음</p>"


async def _build_portfolio_v2_html() -> str:
    """portfolio.json + KRX DB(KR) + KIS API(US) 현재가 → 증권사 앱 스타일 포트폴리오 (v2 전용)."""
    pf = load_json(PORTFOLIO_FILE, {})
    kr = {k: v for k, v in pf.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)}
    us = pf.get("us_stocks", {})

    # ── KR 현재가: WebSocket 캐시 우선 → REST fallback ──
    kr_prices: dict[str, int] = {}
    rest_needed = []
    for t in kr.keys():
        cached = ws_manager.get_cached_price(t)
        if cached is not None:
            kr_prices[t] = cached
        else:
            rest_needed.append(t)
    if rest_needed:
        try:
            token = await asyncio.wait_for(get_kis_token(), timeout=5)
            for t in rest_needed:
                try:
                    data = await asyncio.wait_for(kis_stock_price(t, token), timeout=5)
                    price = int(data.get("stck_prpr", 0) or 0)
                    if price:
                        kr_prices[t] = price
                except Exception:
                    pass
                await asyncio.sleep(0.3)
        except Exception:
            pass

    # ── US 현재가: WebSocket 캐시 우선 → REST fallback ──
    us_prices: dict[str, float] = {}
    usd_krw = 0.0
    if us:
        us_rest_needed = []
        for sym in us.keys():
            cached = ws_manager.get_cached_price(sym)
            if cached is not None:
                us_prices[sym] = float(cached)
            else:
                us_rest_needed.append(sym)
        if us_rest_needed:
            try:
                token = await asyncio.wait_for(get_kis_token(), timeout=5)
                for sym in us_rest_needed:
                    try:
                        data = await asyncio.wait_for(kis_us_stock_price(sym, token), timeout=5)
                        price = float(data.get("last", 0) or 0)
                        if price:
                            us_prices[sym] = price
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)
            except Exception:
                pass

        # 환율 조회 (Yahoo Finance KRW=X)
        try:
            fx = await asyncio.wait_for(get_yahoo_quote("KRW=X"), timeout=5)
            usd_krw = float(fx.get("price", 0) or 0) if fx else 0.0
        except Exception:
            pass

    # ── 합계 계산 ──
    cash_k = float(pf.get("cash_krw", 0) or 0)
    cash_u = float(pf.get("cash_usd", 0) or 0)

    kr_total_cost = kr_total_eval = 0
    for t, v in kr.items():
        qty = int(v.get("qty", 0))
        avg = int(v.get("avg_price", 0))
        cur = kr_prices.get(t, 0)
        kr_total_cost += qty * avg
        kr_total_eval += qty * cur if cur else 0

    us_total_cost_usd = us_total_eval_usd = 0.0
    for sym, info in us.items():
        qty = float(info.get("qty", 0) or 0)
        avg = float(info.get("avg_price", 0) or 0)
        cur = us_prices.get(sym, 0.0)
        us_total_cost_usd += qty * avg
        us_total_eval_usd += qty * cur if cur else 0.0

    us_eval_krw = us_total_eval_usd * usd_krw if usd_krw else 0.0
    us_cost_krw = us_total_cost_usd * usd_krw if usd_krw else 0.0
    cash_total_krw = cash_k + (cash_u * usd_krw if usd_krw else 0.0)

    grand_eval = kr_total_eval + us_eval_krw + cash_total_krw
    grand_cost = kr_total_cost + us_cost_krw
    grand_pnl = grand_eval - grand_cost - cash_total_krw   # 현금은 손익 계산 제외
    grand_pnl_pct = grand_pnl / grand_cost * 100 if grand_cost else 0.0

    def _pc(val: float) -> str:
        return "pos" if val >= 0 else "neg"

    def _sign(val: float) -> str:
        return "+" if val >= 0 else ""

    # ── 상단 요약 카드 ──
    pnl_cls = _pc(grand_pnl)
    html = '<div class="pf-summary">'
    html += f'<div class="pf-total">{grand_eval:,.0f}원</div>'
    html += f'<div class="pf-pnl {pnl_cls}">{_sign(grand_pnl)}{grand_pnl:,.0f}원 ({_sign(grand_pnl_pct)}{grand_pnl_pct:.1f}%)</div>'

    cash_parts = []
    if cash_k:
        cash_parts.append(f"KRW {cash_k:,.0f}원")
    if cash_u:
        cash_parts.append(f"USD ${cash_u:,.2f}")
    if cash_parts:
        html += f'<div class="pf-cash">현금 {" | ".join(cash_parts)}</div>'
    html += '</div>'

    # ── 🇰🇷 한국 섹션 ──
    if kr:
        kr_pnl = kr_total_eval - kr_total_cost
        kr_pnl_pct = kr_pnl / kr_total_cost * 100 if kr_total_cost else 0.0
        kr_pnl_cls = _pc(kr_pnl)
        html += '<div class="pf-section-header">'
        html += '<span class="pf-section-title">🇰🇷 한국 주식</span>'
        html += (f'<span class="pf-section-summary">'
                 f'평가 {kr_total_eval:,.0f}원'
                 f' &nbsp; <span class="{kr_pnl_cls}">{_sign(kr_pnl)}{kr_pnl:,.0f}원 ({_sign(kr_pnl_pct)}{kr_pnl_pct:.1f}%)</span>'
                 f'</span>')
        html += '</div>'

        # 정렬 버튼
        html += ('<div class="pf-sort-bar">'
                 '<button class="pf-sort-btn active" data-section="kr" data-sort="eval">평가금순</button>'
                 '<button class="pf-sort-btn" data-section="kr" data-sort="pnl-pct">수익률순</button>'
                 '<button class="pf-sort-btn" data-section="kr" data-sort="pnl-amt">손익금순</button>'
                 '</div>')

        # 평가금 내림차순 정렬
        kr_items = []
        for t, v in kr.items():
            qty = int(v.get("qty", 0))
            avg = int(v.get("avg_price", 0))
            cur = kr_prices.get(t, 0)
            ev = qty * cur if cur else qty * avg  # 현재가 없으면 매입금
            pnl_amt = (qty * cur - qty * avg) if cur else 0
            pnl_pct = (cur - avg) / avg * 100 if (cur and avg) else 0.0
            kr_items.append((t, v, qty, avg, cur, ev, pnl_amt, pnl_pct))
        kr_items.sort(key=lambda x: x[5], reverse=True)

        html += '<div id="pf-kr-list">'
        for t, v, qty, avg, cur, ev, pnl_amt, pnl_pct in kr_items:
            name = _html.escape(v.get("name", t))
            pc = _pc(pnl_amt)
            cost = qty * avg

            cur_str = (f'<span class="pf-label">현재가</span>{cur:,}원' if cur else "-")
            ev_str = f'<span class="pf-label">평가</span>{ev:,.0f}원'
            pnl_str = (f'<span class="pf-label">손익</span><span class="{pc}">{_sign(pnl_amt)}{pnl_amt:,.0f}원 {_sign(pnl_pct)}{pnl_pct:.1f}%</span>'
                       if cur else "-")
            detail = (f'{qty:,}주 · <span class="pf-label">평단</span>{avg:,}원 · '
                      f'<span class="pf-label">매입</span>{cost:,.0f}원')

            html += (f'<div class="pf-card" data-eval="{ev}" data-pnl-pct="{pnl_pct:.4f}" data-pnl-amt="{pnl_amt}">'
                     f'<div class="pf-left"><div class="pf-name">{name}</div><div class="pf-detail">{detail}</div></div>'
                     f'<div class="pf-right"><div class="pf-price">{cur_str}</div>'
                     f'<div class="pf-eval">{ev_str}</div>'
                     f'<div class="pf-pnl-row">{pnl_str}</div></div>'
                     f'</div>')
        html += '</div>'

    # ── 🇺🇸 미국 섹션 ──
    if us:
        us_pnl_usd = us_total_eval_usd - us_total_cost_usd
        us_pnl_pct = us_pnl_usd / us_total_cost_usd * 100 if us_total_cost_usd else 0.0
        us_pnl_cls = _pc(us_pnl_usd)
        fx_str = f" (USD/KRW {usd_krw:,.1f})" if usd_krw else ""
        eval_krw_str = f" = {us_eval_krw:,.0f}원" if usd_krw else ""

        html += '<div class="pf-section-header" style="margin-top:16px">'
        html += f'<span class="pf-section-title">🇺🇸 미국 주식{fx_str}</span>'
        html += (f'<span class="pf-section-summary">'
                 f'평가 ${us_total_eval_usd:,.2f}{eval_krw_str}'
                 f' &nbsp; <span class="{us_pnl_cls}">{_sign(us_pnl_usd)}${us_pnl_usd:,.2f} ({_sign(us_pnl_pct)}{us_pnl_pct:.1f}%)</span>'
                 f'</span>')
        html += '</div>'

        # 정렬 버튼
        html += ('<div class="pf-sort-bar">'
                 '<button class="pf-sort-btn active" data-section="us" data-sort="eval">평가금순</button>'
                 '<button class="pf-sort-btn" data-section="us" data-sort="pnl-pct">수익률순</button>'
                 '<button class="pf-sort-btn" data-section="us" data-sort="pnl-amt">손익금순</button>'
                 '</div>')

        # 평가금(USD) 내림차순 정렬
        us_items = []
        for sym, info in us.items():
            qty = float(info.get("qty", 0) or 0)
            avg = float(info.get("avg_price", 0) or 0)
            cur = us_prices.get(sym, 0.0)
            ev_usd = qty * cur if cur else qty * avg  # 현재가 없으면 매입금
            pnl_usd = (qty * cur - qty * avg) if cur else 0.0
            pnl_pct = (cur - avg) / avg * 100 if (cur and avg) else 0.0
            # 정렬용 평가금은 원화 환산값 우선, 없으면 USD 그대로
            ev_sort = ev_usd * usd_krw if usd_krw else ev_usd
            us_items.append((sym, info, qty, avg, cur, ev_usd, pnl_usd, pnl_pct, ev_sort))
        us_items.sort(key=lambda x: x[8], reverse=True)

        html += '<div id="pf-us-list">'
        for sym, info, qty, avg, cur, ev_usd, pnl_usd, pnl_pct, ev_sort in us_items:
            name = _html.escape(info.get("name", sym))
            pc = _pc(pnl_usd)
            cost_usd = qty * avg

            cur_str = (f'<span class="pf-label">현재가</span>${cur:,.2f}' if cur else "-")
            ev_usd_str = f'<span class="pf-label">평가</span>${ev_usd:,.2f}'
            ev_krw_str = f" ({ev_usd * usd_krw:,.0f}원)" if (ev_usd and usd_krw) else ""
            pnl_str = (f'<span class="pf-label">손익</span><span class="{pc}">{_sign(pnl_usd)}${pnl_usd:,.2f} {_sign(pnl_pct)}{pnl_pct:.1f}%</span>'
                       if cur else "-")
            detail = (f'{qty:,.0f}주 · <span class="pf-label">평단</span>${avg:,.2f} · '
                      f'<span class="pf-label">매입</span>${cost_usd:,.2f}')

            html += (f'<div class="pf-card" data-eval="{ev_sort:.2f}" data-pnl-pct="{pnl_pct:.4f}" data-pnl-amt="{pnl_usd:.4f}">'
                     f'<div class="pf-left"><div class="pf-name">{name} <span style="color:var(--fg2);font-size:0.8em">({_html.escape(sym)})</span></div>'
                     f'<div class="pf-detail">{detail}</div></div>'
                     f'<div class="pf-right"><div class="pf-price">{cur_str}</div>'
                     f'<div class="pf-eval">{ev_usd_str}{ev_krw_str}</div>'
                     f'<div class="pf-pnl-row">{pnl_str}</div></div>'
                     f'</div>')
        html += '</div>'

    if not kr and not us:
        return "<p>포트폴리오 비어있음</p>"
    return html


def _build_watchalert_html() -> str:
    """watchalert.json → 감시가 테이블."""
    wa = load_watchalert()
    if not wa:
        return "<p>감시 종목 없음</p>"
    items = []
    for t, v in wa.items():
        bp = float(v.get("buy_price", 0) or 0)
        items.append({"name": v.get("name", t), "ticker": t, "buy_price": bp,
                       "grade": v.get("grade", ""), "memo": v.get("memo", "")[:40]})
    items.sort(key=lambda x: x["buy_price"], reverse=True)
    html = "<table><thead><tr><th>종목</th><th>코드</th><th>감시가</th><th>등급</th><th>메모</th></tr></thead><tbody>"
    for i in items[:30]:
        bp = f"${i['buy_price']:,.2f}" if _is_us_ticker(i["ticker"]) else f"{i['buy_price']:,.0f}원"
        html += (f"<tr><td>{_html.escape(i['name'])}</td><td>{_html.escape(i['ticker'])}</td>"
                 f"<td>{bp}</td><td>{_html.escape(i['grade'])}</td><td>{_html.escape(i['memo'])}</td></tr>")
    html += "</tbody></table>"
    if len(items) > 30:
        html += f"<p>... 외 {len(items) - 30}종목</p>"
    return html


async def _handle_dash_file(request: web.Request) -> web.Response:
    """GET /dash/file/{filename} — data/ 파일 렌더링."""
    try:
        filename = request.match_info.get("filename", "")

        # 보안
        if ".." in filename or "/" in filename or "\\" in filename:
            return web.Response(text="Forbidden", status=403)
        if filename.endswith((".py", ".env", ".sh")):
            return web.Response(text="Forbidden", status=403)

        filepath = os.path.join(_DATA_DIR, filename)
        if not os.path.isfile(filepath):
            return web.Response(text="Not Found", status=404)
        if os.path.getsize(filepath) > 500 * 1024:
            return web.Response(text="File too large", status=413)

        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        safe_filename = _html.escape(filename)
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_filename}</title>{_DASH_CSS}</head><body>
<div class="nav"><a href="/dash">← 대시보드</a></div>
<h1>{safe_filename}</h1>"""

        if filename.endswith(".md") or filename.endswith(".txt"):
            html += _md_to_html(content)
        elif filename.endswith(".json"):
            try:
                data = json.loads(content)
                if filename == "portfolio.json":
                    html += _build_portfolio_html()
                elif filename == "watchalert.json":
                    html += _build_watchalert_html()
                else:
                    html += _json_to_table(data)
            except Exception:
                html += f"<pre>{_html.escape(content[:10000])}</pre>"
        else:
            html += f"<pre>{_html.escape(content[:10000])}</pre>"

        html += "</body></html>"
        return web.Response(text=html, content_type="text/html")
    except Exception as e:
        import traceback
        print(f"[Dash] file 오류: {e}\n{traceback.format_exc()}")
        return web.Response(text=f"Error: {e}", status=500)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 웹 대시보드 v2 (/dash-v2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_DASH_V2_CSS = """
<style>
:root{--bg:#1a1a2e;--bg2:#16213e;--fg:#e0e0e0;--fg2:#a0a0b0;--accent:#4fc3f7;--red:#ef5350;--green:#66bb6a;--border:#2a2a4a}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,sans-serif;padding:16px;padding-top:72px;max-width:900px;margin:0 auto;line-height:1.6}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:1.5em;margin:16px 0 8px}
h2{font-size:1.2em;margin:0 0 12px;color:var(--fg)}
h3{font-size:1.0em;margin:12px 0 4px;color:var(--accent)}
p{margin:8px 0}
code{background:var(--bg2);padding:2px 6px;border-radius:3px;font-family:'SF Mono',monospace;font-size:0.9em}
pre{background:var(--bg2);padding:12px;border-radius:6px;overflow-x:auto;margin:8px 0;font-size:0.85em;border:1px solid var(--border)}
pre code{background:none;padding:0}
ul,ol{margin:8px 0 8px 20px}li{margin:2px 0}
.check{display:flex;align-items:center;gap:6px;margin:2px 0}
.check input{width:16px;height:16px;accent-color:var(--accent)}
.tab-nav{position:sticky;top:0;z-index:100;background:var(--bg);display:flex;gap:4px;padding:8px 0;border-bottom:2px solid var(--border);overflow-x:auto;margin-bottom:0}
.tab-nav a{padding:6px 14px;border-radius:16px;white-space:nowrap;font-size:0.85em;color:var(--fg2);text-decoration:none;transition:background 0.2s,color 0.2s}
.tab-nav a:hover{background:var(--bg2);color:var(--fg)}
.tab-nav a.active{background:var(--accent);color:#000;font-weight:600}
.section{background:var(--bg2);border-radius:8px;padding:16px;margin:16px 0;border:1px solid var(--border);scroll-margin-top:60px;transition:border-color 0.2s}
.section:hover{border-color:rgba(79,195,247,0.3)}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:0.9em}
thead th{color:var(--accent);font-weight:600;font-size:0.85em;padding:8px 10px;text-align:left;border-bottom:1px solid var(--border)}
tbody td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--border)}
tbody tr:hover{background:rgba(255,255,255,0.03)}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:0.75em;font-weight:600}
.badge-A{background:rgba(239,83,80,0.2);color:#ef5350}
.badge-Bp{background:rgba(255,167,38,0.2);color:#ffa726}
.badge-B{background:rgba(102,187,106,0.2);color:#66bb6a}
.badge-Bm{background:rgba(120,144,156,0.2);color:#78909c}
.badge-C{background:rgba(120,144,156,0.2);color:#78909c}
.badge-buy{background:rgba(102,187,106,0.15);color:var(--green)}
.badge-sell{background:rgba(239,83,80,0.15);color:var(--red)}
.pos{color:var(--green)}.neg{color:var(--red)}
.pf-summary{background:var(--bg);border-radius:12px;padding:16px;margin-bottom:16px;text-align:center}
.pf-total{font-size:1.8em;font-weight:700;margin:4px 0}
.pf-pnl{font-size:1.2em;font-weight:600}
.pf-cash{font-size:0.85em;color:var(--fg2);margin-top:8px}
.pf-section-header{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);margin-bottom:8px}
.pf-section-title{font-weight:600}
.pf-section-summary{font-size:0.85em;color:var(--fg2);text-align:right}
.pf-card{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.05)}
.pf-card:last-child{border-bottom:none}
.pf-left{}
.pf-name{font-weight:600;font-size:0.95em}
.pf-detail{font-size:0.8em;color:var(--fg2);margin-top:2px}
.pf-right{text-align:right}
.pf-price{font-weight:600}
.pf-eval{font-size:0.85em;color:var(--fg2);margin-top:1px}
.pf-pnl-row{font-size:0.85em;margin-top:2px}
.pf-label{font-size:0.7em;color:var(--fg2);margin-right:2px}
.pf-sort-bar{display:flex;gap:4px;margin-bottom:8px}
.pf-sort-btn{padding:4px 10px;border-radius:12px;border:1px solid var(--border);background:transparent;color:var(--fg2);cursor:pointer;font-size:0.75em;transition:background 0.2s,color 0.2s}
.pf-sort-btn.active{background:var(--accent);color:#000;border-color:var(--accent)}
.dday{font-weight:700;color:var(--accent);white-space:nowrap;text-align:center}
.dday-0{font-weight:700;color:var(--red);animation:pulse 1s infinite;white-space:nowrap;text-align:center}
@keyframes pulse{50%{opacity:0.6}}
.doc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
.doc-card{background:var(--bg);border-radius:8px;padding:12px;border:1px solid var(--border);text-decoration:none;color:var(--fg);transition:border-color 0.2s,transform 0.2s;display:block}
.doc-card:hover{border-color:var(--accent);transform:translateY(-2px);text-decoration:none}
.doc-icon{font-size:1.5em;margin-bottom:4px}
.doc-name{font-size:0.85em;font-weight:600}
.doc-desc{font-size:0.75em;color:var(--fg2)}
.search-box{width:100%;padding:8px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--fg);font-size:0.9em;margin-bottom:8px}
.search-box:focus{outline:none;border-color:var(--accent)}
.filter-bar{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px;align-items:center}
.filter-btn{padding:4px 10px;border-radius:12px;border:1px solid var(--border);background:transparent;color:var(--fg2);cursor:pointer;font-size:0.75em;transition:background 0.2s,color 0.2s}
.filter-btn.active{background:var(--accent);color:#000;border-color:var(--accent)}
.refresh-bar{display:flex;justify-content:space-between;align-items:center;padding:4px 0;font-size:0.75em;color:var(--fg2);margin-bottom:8px}
.toggle{cursor:pointer;user-select:none}
details summary{cursor:pointer;user-select:none}
details summary h2{display:inline}
.sector-group{margin-bottom:8px;border:1px solid var(--border);border-radius:8px;overflow:hidden}
.sector-group[open]{border-color:rgba(79,195,247,0.3)}
.sector-header{padding:8px 12px;cursor:pointer;font-weight:600;font-size:0.9em;background:var(--bg);list-style:none;display:flex;align-items:center;gap:6px}
.sector-header::-webkit-details-marker{display:none}
.watch-sector-table{margin:0}
.decision-card{background:var(--bg);border-radius:8px;padding:8px 12px;margin-bottom:8px;border:1px solid var(--border)}
.decision-card[open]{border-color:var(--accent)}
.decision-card summary{cursor:pointer;display:flex;align-items:center;gap:8px;flex-wrap:wrap;list-style:none}
.decision-card summary::-webkit-details-marker{display:none}
.decision-date{font-weight:600;font-size:0.9em;min-width:90px}
.decision-preview{color:var(--fg2);font-size:0.8em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.decision-body{margin-top:10px;padding-top:10px;border-top:1px solid var(--border)}
.decision-actions{margin-bottom:8px}
.decision-actions li{font-size:0.85em;margin:2px 0;list-style:none;padding-left:12px}
.decision-actions li::before{content:"→ ";color:var(--accent)}
.decision-notes{font-size:0.82em;color:var(--fg2);background:rgba(255,255,255,0.02);padding:8px;border-radius:4px;margin-bottom:8px}
.decision-grades{font-size:0.82em}
.badge-neutral{background:rgba(255,193,7,0.15);color:#ffc107}
.badge-bull{background:rgba(102,187,106,0.15);color:var(--green)}
.badge-bear{background:rgba(239,83,80,0.15);color:var(--red)}
@media(max-width:600px){body{padding:8px;padding-top:72px}.tab-nav{font-size:0.8em}table{font-size:0.8em}.doc-grid{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}}
</style>
"""


def _dash_v2_js() -> str:
    """대시보드 v2 JS (탭 하이라이트 + 자동새로고침 + 감시종목 검색/필터)."""
    return """<script>
// 1. 탭 하이라이트 (IntersectionObserver)
const sections = document.querySelectorAll('.section[id]');
const tabs = document.querySelectorAll('.tab-nav a');
const obs = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      tabs.forEach(t => t.classList.remove('active'));
      const tab = document.querySelector('.tab-nav a[href="#' + e.target.id + '"]');
      if (tab) tab.classList.add('active');
    }
  });
}, { rootMargin: '-60px 0px -70% 0px' });
sections.forEach(s => obs.observe(s));

// 2. 자동 새로고침
let refreshInterval = null;
const REFRESH_MS = 300000;
const refreshToggle = document.getElementById('refresh-toggle');
const refreshTime = document.getElementById('refresh-time');

function startRefresh() {
  refreshInterval = setInterval(() => location.reload(), REFRESH_MS);
  localStorage.setItem('autoRefresh', 'on');
  if (refreshToggle) refreshToggle.textContent = '⏸ 자동갱신 끄기';
}
function stopRefresh() {
  clearInterval(refreshInterval);
  localStorage.setItem('autoRefresh', 'off');
  if (refreshToggle) refreshToggle.textContent = '▶ 자동갱신 켜기';
}
if (refreshToggle) {
  refreshToggle.addEventListener('click', () => {
    if (localStorage.getItem('autoRefresh') === 'off') startRefresh();
    else stopRefresh();
  });
}
if (localStorage.getItem('autoRefresh') !== 'off') startRefresh();
else stopRefresh();
if (refreshTime) refreshTime.textContent = new Date().toLocaleTimeString('ko-KR');

// 3. 감시종목 검색/필터
const searchInput = document.getElementById('watch-search');
const filterBtns = document.querySelectorAll('.filter-btn');
const watchCount = document.getElementById('watch-count');
let currentFilter = 'all';

function filterWatch() {
  const tables = document.querySelectorAll('.watch-sector-table');
  if (!tables.length) return;
  const q = (searchInput ? searchInput.value : '').toLowerCase();
  let visible = 0, total = 0;
  tables.forEach(tbl => {
    const rows = tbl.querySelectorAll('tbody tr');
    let groupVisible = 0;
    rows.forEach(r => {
      const name = (r.dataset.name || '').toLowerCase();
      const ticker = (r.dataset.ticker || '').toLowerCase();
      const grade = r.dataset.grade || '';
      const market = r.dataset.market || '';
      const sectorName = (tbl.closest('.sector-group')?.dataset.sector || '').toLowerCase();
      const matchSearch = !q || name.includes(q) || ticker.includes(q) || sectorName.includes(q);
      const matchFilter = currentFilter === 'all'
        || (currentFilter === 'kr' && market === 'kr')
        || (currentFilter === 'us' && market === 'us')
        || grade.startsWith(currentFilter);
      const show = matchSearch && matchFilter;
      r.style.display = show ? '' : 'none';
      if (show) { visible++; groupVisible++; }
      total++;
    });
    // 그룹 내 visible 종목이 없으면 details 자체를 숨김
    const details = tbl.closest('.sector-group');
    if (details) details.style.display = groupVisible === 0 ? 'none' : '';
  });
  if (watchCount) watchCount.textContent = visible + '/' + total + '종목';
}

if (searchInput) searchInput.addEventListener('input', filterWatch);
filterBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    filterBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentFilter = btn.dataset.filter;
    filterWatch();
  });
});

// 4. 포트폴리오 정렬
document.querySelectorAll('.pf-sort-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const section = btn.dataset.section; // 'kr' or 'us'
    const sortKey = btn.dataset.sort;    // 'eval', 'pnl-pct', 'pnl-amt'
    const container = document.getElementById('pf-' + section + '-list');
    if (!container) return;
    const cards = [...container.querySelectorAll('.pf-card')];
    const attr = sortKey === 'eval' ? 'eval'
               : sortKey === 'pnl-pct' ? 'pnlPct'
               : 'pnlAmt';
    cards.sort((a, b) => {
      const av = parseFloat(a.dataset[attr] || 0);
      const bv = parseFloat(b.dataset[attr] || 0);
      return bv - av;
    });
    cards.forEach(c => container.appendChild(c));
    // 같은 section의 버튼만 토글
    btn.closest('.pf-sort-bar').querySelectorAll('.pf-sort-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

// 5. TODO 체크박스 토글 (클릭 → 서버에 [ ] ↔ [x] 반영)
document.addEventListener('change', async (e) => {
  const cb = e.target;
  if (cb.type !== 'checkbox' || !cb.dataset.todoFile) return;
  const payload = {
    file: cb.dataset.todoFile,
    line: parseInt(cb.dataset.todoLine),
    hash: cb.dataset.todoHash,
    checked: cb.checked
  };
  try {
    const r = await fetch('/dash/todo/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    if (!r.ok) {
      cb.checked = !cb.checked;  // rollback
      const d = await r.json().catch(() => ({}));
      if (r.status === 409) {
        if (confirm('다른 세션이 이 파일을 편집했습니다.\n페이지를 새로고침하고 다시 시도할까요?')) {
          location.reload();
        }
      } else {
        alert('토글 실패: ' + (d.error || r.status));
      }
      return;
    }
    const d = await r.json();
    if (d.new_hash) cb.dataset.todoHash = d.new_hash;
    // 시각 효과: 옆 텍스트 line-through 토글
    const span = cb.nextElementSibling;
    if (span) {
      if (cb.checked) {
        span.style.textDecoration = 'line-through';
        span.style.color = 'var(--fg2)';
      } else {
        span.style.textDecoration = '';
        span.style.color = '';
      }
    }
  } catch (err) {
    cb.checked = !cb.checked;
    alert('네트워크 오류: ' + err.message);
  }
});

// 6. TODO 항목 추가 폼
document.addEventListener('submit', async (e) => {
  const form = e.target;
  if (!form.classList.contains('todo-add-form')) return;
  e.preventDefault();
  const text = form.querySelector('[name=text]').value.trim();
  if (!text) return;
  const payload = {file: form.dataset.file, text: text};
  try {
    const r = await fetch('/dash/todo/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert('추가 실패: ' + (d.error || r.status));
      return;
    }
    location.reload();
  } catch (err) {
    alert('네트워크 오류: ' + err.message);
  }
});

// 7. 투자판단 저장 폼
document.getElementById('decision-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const payload = {
    date: form.date.value,
    regime: form.regime.value,
    notes: form.notes.value,
    actions: form.actions.value,
    grades: form.grades.value
  };
  try {
    const r = await fetch('/dash/decisions/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert('저장 실패: ' + (d.error || r.status));
      return;
    }
    location.reload();
  } catch (err) {
    alert('네트워크 오류: ' + err.message);
  }
});
</script>"""


def _build_events_v2_html() -> str:
    """이벤트 D-day 카운트 + 과거/미래 분리.

    events.json 형식 지원:
      - {"FOMC": "2026-04-28"}  (key=이벤트명, val=날짜) ← 신규
      - {"2026-04-28": "FOMC"}  (key=날짜, val=이벤트명) ← 구버전 호환
      - {"이란": "진행중"}       (날짜 없음 → 기타)
    """
    events = load_json(f"{_DATA_DIR}/events.json", {})
    if not events:
        return "<p>등록된 이벤트 없음</p>"
    today = datetime.now(KST).date()
    future, past = [], []
    for key, val in events.items():
        # 키-값 중 어느 쪽이 날짜인지 판별
        if re.match(r'^\d{4}-\d{2}-\d{2}$', str(val)):
            event_name, date_str = key, str(val)
        elif re.match(r'^\d{4}-\d{2}-\d{2}$', str(key)):
            event_name, date_str = str(val), key
        else:
            # 날짜 없음 → 미래 목록 맨 뒤
            future.append((str(key), str(val), None))
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            delta = (d - today).days
            if delta >= 0:
                future.append((event_name, date_str, delta))
            else:
                past.append((event_name, date_str, delta))
        except Exception:
            future.append((event_name, date_str, None))

    # 미래: D-day 오름차순 (None은 맨 뒤)
    future.sort(key=lambda x: (x[2] is None, x[2] if x[2] is not None else 9999))
    # 과거: 최근 먼저 (delta 내림차순 → 절댓값 오름차순)
    past.sort(key=lambda x: x[2] if x[2] is not None else -9999, reverse=True)

    html = ""
    if future:
        html += '<div class="table-wrap"><table><thead><tr><th style="width:60px;min-width:60px;text-align:center">D-day</th><th>날짜</th><th>이벤트</th></tr></thead><tbody>'
        for event_name, ds, delta in future:
            if delta is None:
                dday_cls, dday_text = "dday", "—"
            elif delta == 0:
                dday_cls, dday_text = "dday-0", "D-DAY"
            else:
                dday_cls, dday_text = "dday", f"D-{delta}"
            display_name = _html.escape(event_name.replace("_", " "))
            html += f'<tr><td class="{dday_cls}">{dday_text}</td><td>{_html.escape(ds)}</td><td>{display_name}</td></tr>'
        html += '</tbody></table></div>'

    if past:
        html += f'<details><summary style="color:var(--fg2);font-size:0.85em;margin-top:12px;padding:4px 0">지난 이벤트 ({len(past)}건)</summary>'
        html += '<div class="table-wrap"><table><thead><tr><th>날짜</th><th>이벤트</th></tr></thead><tbody>'
        for event_name, ds, _ in past:
            display_name = _html.escape(event_name.replace("_", " "))
            html += f'<tr style="color:var(--fg2)"><td>{_html.escape(ds)}</td><td>{display_name}</td></tr>'
        html += '</tbody></table></div></details>'
    return html


_US_SECTOR_MAP = {
    "NVDA": "반도체", "AMD": "반도체", "AVGO": "반도체", "MRVL": "반도체", "ON": "반도체",
    "LITE": "광통신", "GLW": "광통신",
    "VRT": "전력기기", "ETN": "전력기기", "NVT": "전력기기", "MOD": "전력기기",
    "BWXT": "방산", "LEU": "원전",
    "TSLA": "자동차", "META": "인터넷/플랫폼", "PANW": "사이버보안",
    "UNH": "헬스케어", "ASTS": "통신/우주", "TTD": "광고/미디어",
    "XYL": "환경/수처리", "CRSP": "바이오",
}


def _build_watchalert_v2_html() -> str:
    """감시종목 섹터별 그룹핑 + 현재가 + 검색 + 등급 필터 + 뱃지."""
    from collections import defaultdict
    wa = load_json(WATCHALERT_FILE, {})
    if not wa:
        return "<p>감시 종목 없음</p>"

    # 현재가: WS 캐시(장중 실시간 + stoploss 갱신) → SQLite DB fallback
    cur_prices = {}
    # 1차: WS 캐시 (check_stoploss에서 10분마다 갱신됨)
    for ticker, _ in wa.items():
        cached = ws_manager.get_cached_price(ticker)
        if cached is not None:
            cur_prices[ticker] = cached
    # 2차: 캐시에 없는 종목은 SQLite DB에서 (KR 섹터 정보도 함께 수집)
    kr_sector_map: dict[str, str] = {}
    try:
        from db_collector import _get_db
        conn = _get_db()
        latest = conn.execute("SELECT MAX(trade_date) FROM daily_snapshot").fetchone()[0]
        if latest:
            rows = conn.execute("SELECT symbol, close FROM daily_snapshot WHERE trade_date=?", (latest,)).fetchall()
            for r in rows:
                if r["symbol"] not in cur_prices:
                    cur_prices[r["symbol"]] = r["close"]
        # 섹터 정보
        try:
            sec_rows = conn.execute("SELECT symbol, sector FROM stock_master").fetchall()
            kr_sector_map = {r["symbol"]: r["sector"] for r in sec_rows if r["sector"]}
        except Exception:
            pass
        conn.close()
    except Exception:
        pass

    # 종목별 섹터 부여 후 그룹핑
    groups: dict[str, list] = defaultdict(list)
    for ticker, info in wa.items():
        is_us = not ticker.isdigit()
        if is_us:
            sector = _US_SECTOR_MAP.get(ticker, "기타")
        else:
            sector = kr_sector_map.get(ticker, "기타")
        groups[sector].append((ticker, info))

    # 각 그룹 내 등급순 → 같은 등급 내 buy_price 내림차순
    for sector in groups:
        groups[sector].sort(key=lambda x: (
            _GRADE_ORDER.get(x[1].get("grade", ""), 7),
            -float(x[1].get("buy_price", 0) or 0),
        ))

    # 섹터 정렬: 종목 수 많은 순
    sorted_sectors = sorted(groups.keys(), key=lambda s: -len(groups[s]))

    total = sum(len(v) for v in groups.values())
    all_items = [(t, i) for s in sorted_sectors for t, i in groups[s]]

    # 검색 + 필터 UI
    html = '<input id="watch-search" class="search-box" placeholder="종목명 또는 코드 검색...">'
    html += '<div class="filter-bar">'
    html += '<button class="filter-btn active" data-filter="all">전체</button>'
    html += '<button class="filter-btn" data-filter="kr">🇰🇷</button>'
    html += '<button class="filter-btn" data-filter="us">🇺🇸</button>'
    grades = sorted(set(v.get("grade", "") for _, v in all_items if v.get("grade")))
    for g in grades:
        html += f'<button class="filter-btn" data-filter="{g}">{g}</button>'
    html += f'<span id="watch-count" style="margin-left:auto;color:var(--fg2);font-size:0.8em">{total}/{total}종목</span>'
    html += '</div>'

    def _render_row(ticker: str, info: dict) -> str:
        name = _html.escape(info.get("name", ticker))
        bp = float(info.get("buy_price", 0) or 0)
        grade = _html.escape(info.get("grade", ""))
        memo = _html.escape(str(info.get("memo", ""))[:60])
        ticker_esc = _html.escape(ticker)
        is_us = not ticker.isdigit()
        market = "us" if is_us else "kr"
        price_str = f"${bp:,.2f}" if is_us else f"{int(bp):,}원"
        cur = cur_prices.get(ticker, 0)
        if cur:
            cur_str = f"${float(cur):,.2f}" if is_us else f"{int(cur):,}원"
            gap_pct = (float(cur) - bp) / bp * 100 if bp else 0
            gap_cls = "pos" if gap_pct >= 0 else "neg"
            gap_str = f"<span class='{gap_cls}'>{gap_pct:+.1f}%</span>"
        else:
            cur_str = "-"
            gap_str = "-"
        reg_date = info.get("updated_at") or info.get("created", "")
        reg_date_esc = _html.escape(str(reg_date)[:10]) if reg_date else "-"
        grade_key = grade.replace("+", "p").replace("-", "m")
        badge_cls = f"badge-{grade_key}" if grade else ""
        grade_html = f'<span class="badge {badge_cls}">{grade}</span>' if grade else ""
        return (f'<tr data-name="{name}" data-ticker="{ticker_esc}" data-grade="{grade}" data-market="{market}">'
                f'<td>{name}</td><td>{ticker_esc}</td><td>{price_str}</td>'
                f'<td>{cur_str}</td><td>{gap_str}</td>'
                f'<td>{grade_html}</td>'
                f'<td style="font-size:0.8em;color:var(--fg2)">{reg_date_esc}</td>'
                f'<td style="font-size:0.8em;color:var(--fg2)">{memo}</td></tr>')

    # 섹터별 그룹 렌더링
    for sector in sorted_sectors:
        items = groups[sector]
        count = len(items)
        sector_esc = _html.escape(sector)
        html += f'<div class="sector-group" data-sector="{sector_esc}">'
        html += (f'<div class="sector-header">{sector_esc}'
                 f' <span style="color:var(--fg2);font-size:0.85em">({count}종목)</span></div>')
        html += ('<div class="table-wrap"><table class="watch-sector-table">'
                 '<thead><tr><th>종목</th><th>코드</th><th>감시가</th><th>현재가</th>'
                 '<th>괴리</th><th>등급</th><th>등록일</th><th>메모</th></tr></thead><tbody>')
        for ticker, info in items:
            html += _render_row(ticker, info)
        html += '</tbody></table></div></div>'

    return html


_DOC_META_V2 = {
    "TODO.md": ("📋", "할일 목록"),
    "INVESTMENT_RULES.md": ("📏", "투자 규칙"),
    "PROGRESS.md": ("🧭", "세션 인수인계"),
    "bot_guide.md": ("📖", "도구 사용법"),
    "bot_reference.txt": ("📘", "도구 파라미터"),
    "bot_scenarios.md": ("🎯", "활용 시나리오"),
    "bot_samples.md": ("🔬", "입출력 샘플"),
    "FILES.md": ("📁", "파일 설명서"),
    "krx_db_design.md": ("🗄️", "KRX DB 설계"),
    "regime_update_notes.md": ("📝", "레짐 수정노트"),
    "US_DEEPSEARCH_v3.md": ("🇺🇸", "미국주식 딥서치 v3"),
    "KR_DEEPSEARCH.md": ("🇰🇷", "한국주식 10 Step"),
}


def _build_docs_v2_html() -> str:
    """문서 카드 그리드 + research/ 서브폴더."""
    html = '<div class="doc-grid">'
    try:
        doc_files = sorted(
            f for f in os.listdir(_DATA_DIR)
            if f.endswith((".md", ".txt")) and not f.startswith(".")
        )
    except Exception:
        doc_files = []

    for f in doc_files:
        if f in ("TODO.md", "TODO_invest.md", "TODO_dev.md"):
            continue  # TODO 파일은 독립 탭에 있으므로 문서 카드에서 제외
        icon, desc = _DOC_META_V2.get(f, ("📄", ""))
        html += (f'<a href="/dash/file/{f}" class="doc-card">'
                 f'<div class="doc-icon">{icon}</div>'
                 f'<div class="doc-name">{f}</div>'
                 f'<div class="doc-desc">{desc}</div></a>')
    html += '</div>'

    for subdir, section_icon, section_label, card_icon, card_desc in (
        ("research", "📊", "종목 리서치", "📊", "딥리서치"),
        ("thesis", "💡", "투자 테제", "💡", "Thesis"),
    ):
        sub_path = os.path.join(_DATA_DIR, subdir)
        # 엔트리 수집: [(display_name, relative_path), ...]
        sub_entries: list[tuple[str, str]] = []
        if subdir == "research":
            # research/: {TICKER}/{file}.md 계층. TICKER 디렉토리 내부 파일을 카드로
            try:
                for ticker_dir in sorted(os.listdir(sub_path)) if os.path.isdir(sub_path) else []:
                    if ticker_dir.startswith("."):
                        continue
                    ticker_path = os.path.join(sub_path, ticker_dir)
                    if not os.path.isdir(ticker_path):
                        continue
                    try:
                        for f in sorted(os.listdir(ticker_path)):
                            if f.endswith(".md") and not f.startswith("."):
                                stem = f.replace(".md", "")
                                disp = ticker_dir if stem == "main" else f"{ticker_dir} / {stem}"
                                sub_entries.append((disp, f"{ticker_dir}/{f}"))
                    except Exception:
                        pass
            except Exception:
                pass
        else:
            # thesis/: flat 유지
            try:
                for f in sorted(os.listdir(sub_path)) if os.path.isdir(sub_path) else []:
                    if f.endswith(".md") and not f.startswith("."):
                        sub_entries.append((f.replace(".md", ""), f))
            except Exception:
                pass

        if sub_entries:
            html += f'<h3 style="margin-top:16px">{section_icon} {section_label}</h3><div class="doc-grid">'
            for disp, rel in sub_entries:
                html += (f'<a href="/dash/file/{subdir}/{rel}" class="doc-card">'
                         f'<div class="doc-icon">{card_icon}</div>'
                         f'<div class="doc-name">{disp}</div>'
                         f'<div class="doc-desc">{card_desc}</div></a>')
            html += '</div>'
    return html


async def _handle_dash_v2(request: web.Request) -> web.Response:
    """GET /dash-v2 — 개선된 대시보드 v2."""
    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Stock Bot Dashboard v2</title>{_DASH_V2_CSS}</head><body>')
    html += '<h1>📊 Stock Bot</h1>'
    html += ('<div class="refresh-bar">'
             '<span>갱신: <span id="refresh-time">-</span></span>'
             '<span id="refresh-toggle" class="toggle">⏸ 자동갱신 끄기</span>'
             '</div>')
    html += ('<nav class="tab-nav">'
             '<a href="#portfolio" class="active">💰 포트폴리오</a>'
             '<a href="#events">📅 이벤트</a>'
             '<a href="#watch">👀 감시종목</a>'
             '<a href="#decision">📝 투자판단</a>'
             '<a href="#trade">💼 매매</a>'
             '<a href="#invest">📈 투자</a>'
             '<a href="#dev">🔧 봇개발</a>'
             '<a href="#reports">📄 리포트</a>'
             '<a href="#docs">📚 문서</a>'
             '</nav>')

    # 1. 포트폴리오
    try:
        html += f'<div class="section" id="portfolio"><h2>💰 포트폴리오</h2>{await _build_portfolio_v2_html()}</div>'
    except Exception:
        html += '<div class="section" id="portfolio"><h2>💰 포트폴리오</h2><p>로드 실패</p></div>'

    # 2. 이벤트
    try:
        html += f'<div class="section" id="events"><h2>📅 이벤트</h2>{_build_events_v2_html()}</div>'
    except Exception:
        html += '<div class="section" id="events"><h2>📅 이벤트</h2><p>로드 실패</p></div>'

    # 3. 감시종목
    try:
        html += f'<div class="section" id="watch"><h2>👀 감시종목</h2>{_build_watchalert_v2_html()}</div>'
    except Exception:
        html += '<div class="section" id="watch"><h2>👀 감시종목</h2><p>로드 실패</p></div>'

    # 4. 투자판단
    try:
        dl = load_json(f"{_DATA_DIR}/decision_log.json", {})
        total_decisions = len(dl) if dl else 0
        cards_html = ""
        if dl:
            recent = sorted(dl.items(), key=lambda x: x[0], reverse=True)[:5]
            for idx, (date, entry) in enumerate(recent):
                regime_raw = str(entry.get("regime", "?"))
                regime_esc = _html.escape(regime_raw)
                # 레짐 뱃지 클래스
                if "강세" in regime_raw or "bull" in regime_raw.lower():
                    badge_cls = "badge-bull"
                elif "약세" in regime_raw or "bear" in regime_raw.lower():
                    badge_cls = "badge-bear"
                else:
                    badge_cls = "badge-neutral"
                # 액션 목록
                actions_list = entry.get("actions", [])
                if not actions_list and entry.get("summary"):
                    actions_list = [str(entry["summary"])]
                # 프리뷰: 첫 액션 축약
                preview_txt = _html.escape((actions_list[0] if actions_list else "")[:60])
                # 액션 li
                actions_html = ""
                for act in actions_list:
                    actions_html += f"<li>{_html.escape(str(act))}</li>"
                actions_block = f'<ul class="decision-actions">{actions_html}</ul>' if actions_html else ""
                # notes
                notes_raw = entry.get("notes", "")
                notes_block = (f'<div class="decision-notes">{_html.escape(str(notes_raw))}</div>'
                               if notes_raw else "")
                # grades
                grades = entry.get("grades", {})
                grades_lines = ""
                if isinstance(grades, dict):
                    for ticker, ginfo in grades.items():
                        if isinstance(ginfo, dict):
                            g = _html.escape(str(ginfo.get("grade", "")))
                            reason = _html.escape(str(ginfo.get("reason", "")))
                            grades_lines += f'<div><strong>{_html.escape(ticker)}</strong>: <span class="badge badge-{g}">{g}</span> {reason}</div>'
                        else:
                            grades_lines += f'<div><strong>{_html.escape(ticker)}</strong>: {_html.escape(str(ginfo))}</div>'
                grades_block = f'<div class="decision-grades">{grades_lines}</div>' if grades_lines else ""
                open_attr = " open" if idx == 0 else ""
                cards_html += (
                    f'<details class="decision-card"{open_attr}>'
                    f'<summary>'
                    f'<span class="decision-date">{_html.escape(date)}</span>'
                    f'<span class="badge {badge_cls}">{regime_esc}</span>'
                    f'<span class="decision-preview">{preview_txt}</span>'
                    f'</summary>'
                    f'<div class="decision-body">'
                    f'{actions_block}'
                    f'{notes_block}'
                    f'{grades_block}'
                    f'</div>'
                    f'</details>'
                )

        # 투자판단 작성 폼 (날짜 기본값: 오늘 KST)
        _today_kst = datetime.now(KST).strftime("%Y-%m-%d")
        decision_form = (
            f'<details class="decision-new" style="margin:12px 0;background:var(--bg);padding:12px;border-radius:6px;border:1px dashed var(--accent)">'
            f'<summary style="cursor:pointer;color:var(--accent);font-weight:600">➕ 새 투자판단 기록</summary>'
            f'<form id="decision-form" style="margin-top:12px;display:flex;flex-direction:column;gap:10px">'
            f'<label>날짜 <input type="date" name="date" value="{_today_kst}" required></label>'
            f'<label>레짐 '
            f'<select name="regime" required>'
            f'<option value="🟢 공격">🟢 공격</option>'
            f'<option value="🟡 경계">🟡 경계</option>'
            f'<option value="🔴 위기">🔴 위기</option>'
            f'</select>'
            f'</label>'
            f'<label>메모 (notes)'
            f'<textarea name="notes" rows="3" maxlength="5000" placeholder="오늘 시장 관찰, 포지션 조정 근거..." '
            f'style="width:100%;padding:8px;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:4px"></textarea>'
            f'</label>'
            f'<label>액션 (한 줄에 하나씩)'
            f'<textarea name="actions" rows="3" maxlength="5000" placeholder="HD현대일렉 1주 추가 매수&#10;삼성전자 감시가 72000 → 70000 하향" '
            f'style="width:100%;padding:8px;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:4px"></textarea>'
            f'</label>'
            f'<label>등급 (티커:등급:이유, 한 줄에 하나씩)'
            f'<textarea name="grades" rows="3" maxlength="5000" placeholder="005930:A:thesis 유효&#10;066570:B+:실적 개선" '
            f'style="width:100%;padding:8px;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:4px"></textarea>'
            f'</label>'
            f'<button type="submit" style="padding:8px 16px;background:var(--accent);color:#000;border:none;border-radius:4px;cursor:pointer;align-self:flex-start;font-weight:600">저장</button>'
            f'</form>'
            f'</details>'
        )

        html += (f'<div class="section" id="decision">'
                 f'<div style="display:flex;justify-content:space-between;align-items:center">'
                 f'<h2 style="margin:0">📝 최근 투자판단</h2>'
                 f'<a href="/dash/decisions" style="color:var(--accent);text-decoration:none;font-size:0.85em">'
                 f'전체 {total_decisions}건 보기 →</a>'
                 f'</div>'
                 f'{decision_form}'
                 f'{cards_html}'
                 f'</div>')
    except Exception:
        pass

    # 5. 매매기록
    try:
        tl = load_json(f"{_DATA_DIR}/trade_log.json", [])
        trades = tl if isinstance(tl, list) else tl.get("trades", [])
        if trades:
            total_trades = len(trades)
            recent_t = list(reversed(trades))[:5]
            trade_cards = ""
            for idx, t in enumerate(recent_t):
                trade_cards += _build_trade_card(t, is_open=(idx == 0))
            html += (f'<div class="section" id="trade">'
                     f'<div style="display:flex;justify-content:space-between;align-items:center">'
                     f'<h2 style="margin:0">💼 최근 매매</h2>'
                     f'<a href="/dash/trades" style="color:var(--accent);text-decoration:none;font-size:0.85em">'
                     f'전체 {total_trades}건 보기 →</a>'
                     f'</div>'
                     f'{trade_cards}'
                     f'</div>')
    except Exception:
        pass

    # 6. 투자 TODO (체크박스 토글 + 항목 추가)
    try:
        invest_path = os.path.join(_DATA_DIR, "TODO_invest.md")
        if os.path.exists(invest_path):
            with open(invest_path, encoding="utf-8") as f:
                _invest_md = f.read()
            html += (
                f'<div class="section" id="invest"><h2>📈 투자</h2>'
                f'{_md_to_html_editable(_invest_md, "invest")}'
                f'<details class="todo-add" style="margin-top:16px;background:var(--bg);padding:12px;border-radius:6px;border:1px dashed var(--border)">'
                f'<summary style="cursor:pointer;color:var(--accent);font-size:0.9em">➕ 항목 추가</summary>'
                f'<form class="todo-add-form" data-file="invest" style="margin-top:12px;display:flex;flex-direction:column;gap:8px">'
                f'<input type="text" name="text" placeholder="새 TODO 항목..." required maxlength="500" '
                f'style="padding:8px;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:4px">'
                f'<button type="submit" style="padding:6px 12px;background:var(--accent);color:#000;border:none;border-radius:4px;cursor:pointer;align-self:flex-start">추가</button>'
                f'</form>'
                f'</details>'
                f'</div>'
            )
    except Exception:
        pass

    # 6b. 봇개발 TODO (체크박스 토글 + 항목 추가)
    try:
        dev_path = os.path.join(_DATA_DIR, "TODO_dev.md")
        if os.path.exists(dev_path):
            with open(dev_path, encoding="utf-8") as f:
                _dev_md = f.read()
            html += (
                f'<div class="section" id="dev"><h2>🔧 봇개발</h2>'
                f'{_md_to_html_editable(_dev_md, "dev")}'
                f'<details class="todo-add" style="margin-top:16px;background:var(--bg);padding:12px;border-radius:6px;border:1px dashed var(--border)">'
                f'<summary style="cursor:pointer;color:var(--accent);font-size:0.9em">➕ 항목 추가</summary>'
                f'<form class="todo-add-form" data-file="dev" style="margin-top:12px;display:flex;flex-direction:column;gap:8px">'
                f'<input type="text" name="text" placeholder="새 TODO 항목..." required maxlength="500" '
                f'style="padding:8px;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:4px">'
                f'<button type="submit" style="padding:6px 12px;background:var(--accent);color:#000;border:none;border-radius:4px;cursor:pointer;align-self:flex-start">추가</button>'
                f'</form>'
                f'</details>'
                f'</div>'
            )
    except Exception:
        pass

    # 7. 리포트
    try:
        import sqlite3 as _sqlite3_rpt
        rpt_conn = _sqlite3_rpt.connect(REPORT_DB_PATH, timeout=10)
        rpt_conn.row_factory = _sqlite3_rpt.Row
        ticker_counts = rpt_conn.execute("""
            SELECT ticker, name, COUNT(*) as cnt, MAX(date) as latest
            FROM reports GROUP BY ticker ORDER BY cnt DESC
        """).fetchall()
        rpt_conn.close()
        html += '<div class="section" id="reports"><h2>📄 리포트</h2>'
        if ticker_counts:
            html += '<div class="doc-grid">'
            for tc in ticker_counts:
                html += (f'<a href="/dash/reports/{_html.escape(tc["ticker"])}" class="doc-card">'
                         f'<div class="doc-icon">📄</div>'
                         f'<div class="doc-name">{_html.escape(tc["name"])}</div>'
                         f'<div class="doc-desc">{tc["cnt"]}건 | 최신 {_html.escape(tc["latest"])}</div>'
                         f'</a>')
            html += '</div>'
        else:
            html += '<p style="color:var(--fg2)">리포트 없음</p>'
        html += '</div>'
    except Exception:
        pass

    # 8. 문서
    try:
        html += f'<div class="section" id="docs"><h2>📚 문서</h2>{_build_docs_v2_html()}</div>'
    except Exception:
        html += '<div class="section" id="docs"><h2>📚 문서</h2><p>로드 실패</p></div>'

    html += _dash_v2_js()
    html += "</body></html>"
    return web.Response(text=html, content_type="text/html")


async def _handle_dash_research_file(request: web.Request) -> web.Response:
    """GET /dash/file/research/{TICKER}/{filename} 또는 /dash/file/thesis/{filename}.
    research는 2단계 (TICKER 디렉토리), thesis는 flat."""
    try:
        filename = request.match_info.get("filename", "")
        subdir = "thesis" if "/thesis/" in request.path else "research"
        # research만 1회 "/" 허용 (TICKER/file 형식). 나머지 path traversal 방어.
        if ".." in filename or "\\" in filename:
            return web.Response(text="Forbidden", status=403)
        max_slashes = 1 if subdir == "research" else 0
        if filename.count("/") > max_slashes:
            return web.Response(text="Forbidden", status=403)
        if filename.endswith((".py", ".env", ".sh")):
            return web.Response(text="Forbidden", status=403)

        filepath = os.path.join(_DATA_DIR, subdir, filename)
        # realpath 검증: 최종 경로가 subdir 하위여야 함 (심볼릭 링크 등 방어)
        real_base = os.path.realpath(os.path.join(_DATA_DIR, subdir))
        real_target = os.path.realpath(filepath)
        if not real_target.startswith(real_base + os.sep):
            return web.Response(text="Forbidden", status=403)
        if not os.path.isfile(filepath):
            return web.Response(text="Not Found", status=404)
        if os.path.getsize(filepath) > 500 * 1024:
            return web.Response(text="File too large", status=413)

        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{filename}</title>{_DASH_CSS}</head><body>'
                f'<div class="nav"><a href="/dash-v2">← 대시보드 v2</a></div>'
                f'<h1>{filename}</h1>')

        if filename.endswith(".md") or filename.endswith(".txt"):
            html += _md_to_html(content)
        elif filename.endswith(".json"):
            try:
                html += _json_to_table(json.loads(content))
            except Exception:
                html += f"<pre>{_html.escape(content[:10000])}</pre>"
        else:
            html += f"<pre>{_html.escape(content[:10000])}</pre>"

        html += "</body></html>"
        return web.Response(text=html, content_type="text/html")
    except Exception as e:
        import traceback
        print(f"[Dash] research file 오류: {e}\n{traceback.format_exc()}")
        return web.Response(text=f"Error: {e}", status=500)


async def _handle_dash_reports(request: web.Request) -> web.Response:
    """GET /dash/reports/{ticker} — 종목별 리포트 목록."""
    ticker = request.match_info.get("ticker", "")
    if ".." in ticker or "/" in ticker or "\\" in ticker:
        return web.Response(status=400, text="Invalid ticker")

    import sqlite3 as _sqlite3_rpt2
    try:
        conn = _sqlite3_rpt2.connect(REPORT_DB_PATH, timeout=10)
        conn.row_factory = _sqlite3_rpt2.Row
        rows = conn.execute("""
            SELECT date, source, analyst, title, pdf_path, extraction_status,
                   COALESCE(target_price, 0) AS target_price,
                   COALESCE(opinion, '') AS opinion
            FROM reports WHERE ticker=? ORDER BY date DESC
        """, (ticker,)).fetchall()
        name_row = conn.execute(
            "SELECT name FROM reports WHERE ticker=? LIMIT 1", (ticker,)
        ).fetchone()
        name = name_row["name"] if name_row else ticker
        conn.close()
    except Exception as e:
        return web.Response(status=500, text=f"DB 오류: {e}")

    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_html.escape(name)} 리포트</title>{_DASH_V2_CSS}'
            f'<style>'
            f'.rpt-wrap{{overflow-x:auto;-webkit-overflow-scrolling:touch}}'
            f'.rpt-table{{width:100%;border-collapse:collapse;font-size:0.88em}}'
            f'.rpt-table th{{background:var(--card);color:var(--fg2);font-weight:600;'
            f'padding:8px 10px;border-bottom:1px solid var(--border);white-space:nowrap;text-align:left}}'
            f'.rpt-table td{{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:middle}}'
            f'.rpt-table tr:hover td{{background:var(--card)}}'
            f'.rpt-date{{white-space:nowrap;color:var(--fg2);font-size:0.85em}}'
            f'.rpt-title{{max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}'
            f'.op-buy{{color:var(--green)}}.op-sell{{color:var(--red)}}.op-neutral{{color:var(--fg2)}}'
            f'</style>'
            f'</head><body>')
    html += (f'<div style="margin-bottom:16px">'
             f'<a href="/dash#reports" style="color:var(--accent);text-decoration:none">← 대시보드</a>'
             f'</div>')
    html += (f'<h1>📄 {_html.escape(name)} ({_html.escape(ticker)}) '
             f'리포트 ({len(rows)}건)</h1>')

    if not rows:
        html += '<p style="color:var(--fg2)">리포트 없음</p>'
    else:
        html += '<div class="rpt-wrap"><table class="rpt-table">'
        html += ('<thead><tr>'
                 '<th>날짜</th><th>증권사</th><th>애널리스트</th>'
                 '<th>제목</th><th>목표가</th><th>투자의견</th><th>PDF</th>'
                 '</tr></thead><tbody>')

        for r in rows:
            date = _html.escape(r["date"] or "")
            source = _html.escape(r["source"] or "")
            analyst = _html.escape(r["analyst"] or "")
            title = _html.escape(r["title"] or "")
            pdf_path = r["pdf_path"] or ""
            target_price = r["target_price"] or 0
            opinion = r["opinion"] or ""

            # 목표가 셀
            tp_cell = f'🎯 {target_price:,}원' if target_price else '<span style="color:var(--fg2)">—</span>'

            # 투자의견 셀
            if opinion == "매수":
                op_cell = f'<span class="op-buy">{_html.escape(opinion)}</span>'
            elif opinion == "매도":
                op_cell = f'<span class="op-sell">{_html.escape(opinion)}</span>'
            elif opinion:
                op_cell = f'<span class="op-neutral">{_html.escape(opinion)}</span>'
            else:
                op_cell = '<span style="color:var(--fg2)">—</span>'

            # PDF 셀
            if pdf_path:
                fname = os.path.basename(pdf_path)
                pdf_cell = (f'<a href="/dash/pdf/{_html.escape(ticker)}/{_html.escape(fname)}" '
                            f'target="_blank" style="color:var(--accent);text-decoration:none">PDF</a>')
            else:
                pdf_cell = '<span style="color:var(--fg2)">—</span>'

            html += (f'<tr>'
                     f'<td class="rpt-date">{date}</td>'
                     f'<td>{source}</td>'
                     f'<td style="color:var(--fg2);font-size:0.85em">{analyst}</td>'
                     f'<td class="rpt-title" title="{title}">{title}</td>'
                     f'<td style="white-space:nowrap">{tp_cell}</td>'
                     f'<td>{op_cell}</td>'
                     f'<td>{pdf_cell}</td>'
                     f'</tr>')

        html += '</tbody></table></div>'

    html += "</body></html>"
    return web.Response(text=html, content_type="text/html")


async def _handle_dash_pdf(request: web.Request) -> web.Response:
    """GET /dash/pdf/{ticker}/{filename} — PDF 파일 직접 서빙."""
    ticker = request.match_info.get("ticker", "")
    filename = request.match_info.get("filename", "")

    # 보안: path traversal 방지
    if ".." in ticker or "/" in ticker or "\\" in ticker:
        return web.Response(status=400, text="Invalid ticker")
    if ".." in filename or "/" in filename or "\\" in filename:
        return web.Response(status=400, text="Invalid filename")
    if not filename.lower().endswith(".pdf"):
        return web.Response(status=400, text="PDF only")

    pdf_dir = os.path.join(os.environ.get("DATA_DIR", "data"), "report_pdfs")
    fpath = os.path.join(pdf_dir, ticker, filename)

    if not os.path.isfile(fpath):
        return web.Response(status=404, text="PDF not found")

    with open(fpath, "rb") as f:
        content = f.read()
    return web.Response(body=content, content_type="application/pdf")


def _build_trade_card(t: dict, is_open: bool = False) -> str:
    """trade_log 1건 → details 카드 HTML."""
    ticker = t.get("ticker", "")
    is_us = bool(ticker) and not ticker.isdigit()
    side_cls = "badge-buy" if t.get("side") == "buy" else "badge-sell"
    side_txt = "매수" if t.get("side") == "buy" else "매도"
    price = t.get("price", 0)
    try:
        price_str = f"${float(price):,.2f}" if is_us else f"{int(price):,}원"
    except (TypeError, ValueError):
        price_str = str(price)
    qty = t.get("qty", 0)
    name = _html.escape(str(t.get("name", ticker)))
    date = _html.escape(str(t.get("date", "?")))
    grade = _html.escape(str(t.get("grade_at_trade", "")))
    reason = _html.escape(str(t.get("reason", "")))
    target = t.get("target_price", 0)
    stop = t.get("stop_price", 0)

    open_attr = " open" if is_open else ""

    # grade 뱃지
    grade_key = grade.replace("+", "p").replace("-", "m")
    grade_html = f'<span class="badge badge-{grade_key}">{grade}</span>' if grade else ""

    # 목표/손절 메타
    meta_parts = []
    if grade:
        meta_parts.append(f"등급: {grade_html}")
    if target:
        try:
            t_str = f"${float(target):,.2f}" if is_us else f"{int(target):,}원"
        except (TypeError, ValueError):
            t_str = str(target)
        meta_parts.append(f"목표: {t_str}")
    if stop:
        try:
            s_str = f"${float(stop):,.2f}" if is_us else f"{int(stop):,}원"
        except (TypeError, ValueError):
            s_str = str(stop)
        meta_parts.append(f"손절: {s_str}")
    meta_html = (f'<div style="font-size:0.85em;margin-bottom:6px">'
                 f'{" | ".join(meta_parts)}</div>') if meta_parts else ""

    reason_html = f'<div class="decision-notes">{reason}</div>' if reason else ""

    return (f'<details class="decision-card"{open_attr}><summary>'
            f'<span class="decision-date">{date}</span>'
            f'<span class="badge {side_cls}">{side_txt}</span>'
            f'<span style="font-weight:600">{name}</span>'
            f'<span style="color:var(--fg2);font-size:0.85em">{price_str} × {qty}</span>'
            f'</summary><div class="decision-body">'
            f'{meta_html}{reason_html}'
            f'</div></details>')


async def _handle_dash_trades(request: web.Request) -> web.Response:
    """GET /dash/trades — 매매 기록 전체."""
    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>매매 기록</title>{_DASH_V2_CSS}</head><body>'
            f'<div style="margin-bottom:16px">'
            f'<a href="/dash-v2" style="color:var(--accent);text-decoration:none">← 대시보드</a>'
            f'</div>')
    try:
        tl = load_json(f"{_DATA_DIR}/trade_log.json", [])
        trades = tl if isinstance(tl, list) else tl.get("trades", [])
        total = len(trades)
        html += f'<h1>💼 매매 기록 ({total}건)</h1>'
        if trades:
            for t in reversed(trades):
                html += _build_trade_card(t, is_open=False)
        else:
            html += '<p>매매 기록이 없습니다.</p>'
    except Exception as e:
        html += f'<p style="color:red">로드 실패: {_html.escape(str(e))}</p>'
    html += "</body></html>"
    return web.Response(text=html, content_type="text/html")


async def _handle_dash_decisions(request: web.Request) -> web.Response:
    """GET /dash/decisions — 투자판단 전체 로그."""
    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>투자판단 기록</title>{_DASH_V2_CSS}</head><body>'
            f'<div style="margin-bottom:16px">'
            f'<a href="/dash-v2" style="color:var(--accent);text-decoration:none">← 대시보드</a>'
            f'</div>')

    try:
        dl = load_json(f"{_DATA_DIR}/decision_log.json", {})
        total = len(dl)
        html += f'<h1>📝 투자판단 기록 ({total}건)</h1>'

        for date in sorted(dl.keys(), reverse=True):
            entry = dl[date]
            regime_raw = str(entry.get("regime", "?"))
            regime_esc = _html.escape(regime_raw)

            if "강세" in regime_raw or "bull" in regime_raw.lower():
                badge_cls = "badge-bull"
            elif "약세" in regime_raw or "bear" in regime_raw.lower():
                badge_cls = "badge-bear"
            else:
                badge_cls = "badge-neutral"

            actions_list = entry.get("actions", [])
            if not actions_list and entry.get("summary"):
                actions_list = [str(entry["summary"])]

            preview_txt = _html.escape((actions_list[0] if actions_list else "")[:60])

            actions_html = ""
            for act in actions_list:
                actions_html += f"<li>{_html.escape(str(act))}</li>"
            actions_block = f'<ul class="decision-actions">{actions_html}</ul>' if actions_html else ""

            notes_raw = entry.get("notes", "")
            notes_block = (f'<div class="decision-notes">{_html.escape(str(notes_raw))}</div>'
                           if notes_raw else "")

            grades = entry.get("grades", {})
            grades_lines = ""
            if isinstance(grades, dict):
                for ticker, ginfo in grades.items():
                    if isinstance(ginfo, dict):
                        g = _html.escape(str(ginfo.get("grade", "")))
                        reason = _html.escape(str(ginfo.get("reason", "")))
                        grades_lines += (f'<div><strong>{_html.escape(ticker)}</strong>: '
                                         f'<span class="badge badge-{g}">{g}</span> {reason}</div>')
                    else:
                        grades_lines += f'<div><strong>{_html.escape(ticker)}</strong>: {_html.escape(str(ginfo))}</div>'
            grades_block = f'<div class="decision-grades">{grades_lines}</div>' if grades_lines else ""

            html += (
                f'<details class="decision-card">'
                f'<summary>'
                f'<span class="decision-date">{_html.escape(date)}</span>'
                f'<span class="badge {badge_cls}">{regime_esc}</span>'
                f'<span class="decision-preview">{preview_txt}</span>'
                f'</summary>'
                f'<div class="decision-body">'
                f'{actions_block}'
                f'{notes_block}'
                f'{grades_block}'
                f'</div>'
                f'</details>'
            )
    except Exception as e:
        html += f'<p style="color:red">로드 실패: {_html.escape(str(e))}</p>'

    html += "</body></html>"
    return web.Response(text=html, content_type="text/html")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 대시보드 편집 POST 핸들러 (TODO 토글/추가, 투자판단 저장)
# Cloudflare Access 가 /dash/* 앞단 인증. backend 가드는 입력 검증만 수행.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _handle_dash_todo_toggle(request: web.Request) -> web.Response:
    """POST /dash/todo/toggle — TODO 체크박스 [ ] ↔ [x] 토글."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    file_key = body.get("file")
    line_num = body.get("line")
    req_hash = body.get("hash")
    checked = body.get("checked")

    if file_key not in _TODO_FILE_MAP:
        return web.json_response({"error": "unknown file key"}, status=400)
    if not isinstance(line_num, int) or line_num < 1:
        return web.json_response({"error": "invalid line"}, status=400)
    if not isinstance(req_hash, str) or len(req_hash) != 12:
        return web.json_response({"error": "invalid hash"}, status=400)
    if not isinstance(checked, bool):
        return web.json_response({"error": "invalid checked"}, status=400)

    filename = _TODO_FILE_MAP[file_key]
    filepath = os.path.join(_DATA_DIR, filename)

    if not os.path.isfile(filepath):
        return web.json_response({"error": "file not found"}, status=404)
    try:
        if os.path.getsize(filepath) > 500 * 1024:
            return web.json_response({"error": "file too large"}, status=413)
    except OSError as e:
        return web.json_response({"error": f"stat failed: {e}"}, status=500)

    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return web.json_response({"error": f"read failed: {e}"}, status=500)

    lines = content.split("\n")
    idx = line_num - 1
    if idx < 0 or idx >= len(lines):
        return web.json_response({"error": "line out of range"}, status=400)

    orig_line = lines[idx]
    cur_hash = hashlib.sha1(orig_line.encode("utf-8")).hexdigest()[:12]
    if cur_hash != req_hash:
        return web.json_response({"error": "hash mismatch (file changed)"}, status=409)

    # 코드블록 내부 라인은 편집 거부 (critic #2)
    # lines[0..idx-1] 에서 ``` 개수가 홀수면 idx 는 코드블록 내부
    fence_count = 0
    for prev_line in lines[:idx]:
        if prev_line.strip().startswith("```"):
            fence_count += 1
    if fence_count % 2 == 1:
        return web.json_response(
            {"error": "line is inside code block, edit refused"}, status=400
        )

    # 체크박스 패턴 확인
    if checked:
        # [ ] → [x]
        if "[ ]" not in orig_line:
            return web.json_response({"error": "no [ ] found on line"}, status=400)
        new_line = orig_line.replace("[ ]", "[x]", 1)
    else:
        # [x] or [X] → [ ]
        if "[x]" in orig_line:
            new_line = orig_line.replace("[x]", "[ ]", 1)
        elif "[X]" in orig_line:
            new_line = orig_line.replace("[X]", "[ ]", 1)
        else:
            return web.json_response({"error": "no [x]/[X] found on line"}, status=400)

    lines[idx] = new_line
    new_content = "\n".join(lines)

    try:
        _atomic_write(filepath, new_content)
    except Exception as e:
        return web.json_response({"error": f"write failed: {e}"}, status=500)

    new_hash = hashlib.sha1(new_line.encode("utf-8")).hexdigest()[:12]
    return web.json_response({"ok": True, "new_hash": new_hash})


async def _handle_dash_todo_add(request: web.Request) -> web.Response:
    """POST /dash/todo/add — 파일 상단 첫 ## 섹션 바로 다음에 `- [ ] {text}` 삽입."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    file_key = body.get("file")
    text = body.get("text", "")

    if file_key not in _TODO_FILE_MAP:
        return web.json_response({"error": "unknown file key"}, status=400)
    if not isinstance(text, str):
        return web.json_response({"error": "text must be string"}, status=400)
    text = text.strip()
    if not text:
        return web.json_response({"error": "text empty"}, status=400)
    if len(text) > 500:
        return web.json_response({"error": "text too long (max 500)"}, status=400)
    if "\n" in text or "\r" in text:
        return web.json_response({"error": "newline not allowed"}, status=400)

    filename = _TODO_FILE_MAP[file_key]
    filepath = os.path.join(_DATA_DIR, filename)

    if not os.path.isfile(filepath):
        return web.json_response({"error": "file not found"}, status=404)
    try:
        if os.path.getsize(filepath) > 500 * 1024:
            return web.json_response({"error": "file too large"}, status=413)
    except OSError as e:
        return web.json_response({"error": f"stat failed: {e}"}, status=500)

    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return web.json_response({"error": f"read failed: {e}"}, status=500)

    lines = content.split("\n")
    new_item = f"- [ ] {text}"

    # 첫 ## 헤더 찾기 → 그 다음 빈 줄 뒤에 삽입
    insert_at = None
    for i, ln in enumerate(lines):
        if ln.startswith("## "):
            # ## 다음 빈 줄 찾기
            j = i + 1
            while j < len(lines) and lines[j].strip() != "":
                j += 1
            # 빈 줄이 있으면 그 뒤에, 없으면 파일 끝에
            insert_at = j + 1 if j < len(lines) else len(lines)
            break

    if insert_at is None:
        # ## 없으면 파일 최상단에 삽입
        insert_at = 0

    lines.insert(insert_at, new_item)
    new_content = "\n".join(lines)

    try:
        _atomic_write(filepath, new_content)
    except Exception as e:
        return web.json_response({"error": f"write failed: {e}"}, status=500)

    return web.json_response({"ok": True})


async def _handle_dash_decision_add(request: web.Request) -> web.Response:
    """POST /dash/decisions/add — decision_log.json 에 새 엔트리 추가/병합."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    date = body.get("date", "")
    regime = body.get("regime", "")
    notes = body.get("notes", "")
    actions_raw = body.get("actions", "")
    grades_raw = body.get("grades", "")

    # 입력 검증
    if not isinstance(date, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return web.json_response({"error": "invalid date (YYYY-MM-DD)"}, status=400)
    if not isinstance(regime, str) or not regime.strip():
        return web.json_response({"error": "regime required"}, status=400)
    if len(regime) > 200:
        return web.json_response({"error": "regime too long"}, status=400)
    for field_name, field_val in [("notes", notes), ("actions", actions_raw), ("grades", grades_raw)]:
        if not isinstance(field_val, str):
            return web.json_response({"error": f"{field_name} must be string"}, status=400)
        if len(field_val) > 5000:
            return web.json_response({"error": f"{field_name} too long (max 5000)"}, status=400)

    # actions 파싱
    actions_list = [ln.strip() for ln in actions_raw.split("\n") if ln.strip()]

    # grades 파싱: "티커:등급:이유" 형식, 콜론 부족 라인 무시
    grades_dict = {}
    for ln in grades_raw.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        parts = ln.split(":", 2)
        if len(parts) < 2:
            continue  # 콜론 부족 → 무시
        ticker = parts[0].strip()
        grade = parts[1].strip()
        reason = parts[2].strip() if len(parts) >= 3 else ""
        if not ticker or not grade:
            continue
        grades_dict[ticker] = {"grade": grade, "reason": reason}

    # decision_log.json 로드
    filepath = os.path.join(_DATA_DIR, "decision_log.json")
    try:
        if os.path.isfile(filepath):
            with open(filepath, encoding="utf-8") as f:
                dl = json.load(f)
            if not isinstance(dl, dict):
                dl = {}
        else:
            dl = {}
    except Exception as e:
        return web.json_response({"error": f"load failed: {e}"}, status=500)

    # 병합 or 신규
    existing = dl.get(date)
    if isinstance(existing, dict):
        # 기존 entry 와 병합
        # notes: 기존 + "\n---\n" + 새 (새 notes 있을 때만 구분자 append)
        old_notes = str(existing.get("notes", ""))
        if notes.strip():
            merged_notes = old_notes + ("\n---\n" if old_notes else "") + notes
        else:
            merged_notes = old_notes
        # actions: list 연장
        old_actions = existing.get("actions", [])
        if not isinstance(old_actions, list):
            old_actions = []
        merged_actions = old_actions + actions_list
        # grades: dict 병합 (새 값 우선)
        old_grades = existing.get("grades", {})
        if not isinstance(old_grades, dict):
            old_grades = {}
        merged_grades = dict(old_grades)
        merged_grades.update(grades_dict)
        # regime: 새 값으로 덮어쓰기 (비어있으면 유지)
        merged_regime = regime if regime.strip() else existing.get("regime", "")

        dl[date] = {
            "regime": merged_regime,
            "notes": merged_notes,
            "actions": merged_actions,
            "grades": merged_grades,
        }
    else:
        dl[date] = {
            "regime": regime,
            "notes": notes,
            "actions": actions_list,
            "grades": grades_dict,
        }

    # 저장 (atomic)
    try:
        _atomic_write(filepath, json.dumps(dl, ensure_ascii=False, indent=2))
    except Exception as e:
        return web.json_response({"error": f"save failed: {e}"}, status=500)

    return web.json_response({"ok": True})


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
    mcp_app.router.add_get("/dash", _handle_dash_v2)
    mcp_app.router.add_get("/dash/file/{filename}", _handle_dash_file)
    mcp_app.router.add_get("/dash-v2", _handle_dash_v2)
    mcp_app.router.add_get("/dash/decisions", _handle_dash_decisions)
    mcp_app.router.add_get("/dash/trades", _handle_dash_trades)
    mcp_app.router.add_get("/dash/file/research/{filename:.+}", _handle_dash_research_file)
    mcp_app.router.add_get("/dash/file/thesis/{filename:.+}", _handle_dash_research_file)
    mcp_app.router.add_get("/dash/reports/{ticker}", _handle_dash_reports)
    mcp_app.router.add_get("/dash/pdf/{ticker}/{filename}", _handle_dash_pdf)
    # 대시보드 편집 기능 (Cloudflare Access 로 인증 통과한 요청만)
    mcp_app.router.add_post("/dash/todo/toggle", _handle_dash_todo_toggle)
    mcp_app.router.add_post("/dash/todo/add", _handle_dash_todo_add)
    mcp_app.router.add_post("/dash/decisions/add", _handle_dash_decision_add)
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
    print(f"[WS] 실시간 매니저 시작 (KR {len(ws_manager._subscribed)}개 + US {len(ws_manager._subscribed_us)}개)")

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
