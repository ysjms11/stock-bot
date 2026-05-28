"""main_pkg jobs — auto-split from main.py. See main_pkg/__init__.py."""
import asyncio
import os
import json
import re
import calendar as _cal
from datetime import datetime, timedelta, time as dtime

from telegram.ext import ContextTypes

from main_pkg._ctx import (
    _KR_SECTORS, _SECTOR_LIMIT, _STOCK_LIMIT,
    _is_kr_trading_time, _read_regime, _safe_send,
    _track_silent_failure, _reset_silent_failure, _alert_silent_failure,
    _extract_grade, _grade_arrow,
)
from kis_api import *
from kis_api import (
    _DATA_DIR, _is_us_ticker, _is_us_market_hours_kst, _is_us_market_closed, _guess_excd,
    ws_manager, get_ws_tickers, close_session,
    fetch_us_earnings_calendar, fetch_us_sector_etf,
    fetch_and_cache_disclosure, parse_disclosure_summary,
)

INSIDER_SENT_FILE = f"{_DATA_DIR}/insider_sent.json"
INSIDER_CLUSTER_MIN_BUYERS = 3   # 30일 내 매수자 3명+ 시 플래그
INSIDER_COOLDOWN_DAYS = 7        # 종목당 알림 재발송 쿨다운

# ── check_insider_cluster ──

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
        await _safe_send(context, msg)
    except Exception as e:
        print(f"[insider] 체크 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림: 워치 변화 감지 (19:00)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
