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

from db_collector import load_krx_db

# ── watch_change_detect ──

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

            # 감시가 근접 2% (5% → 2%, 5/5 노이즈 컷)
            if ticker in wa:
                buy_p = float(wa[ticker].get("buy_price", 0) or 0)
                cur = s.get("close", 0)
                if buy_p > 0 and cur > 0:
                    gap = (cur - buy_p) / buy_p * 100
                    if 0 <= gap <= 2:
                        alerts.append(f"👀 {name}: 감시가 {buy_p:,.0f}원 근접 ({gap:.1f}%)")

            # 외인 매수 전환 (5d>=70% / 20d<40%, 5/5 4일+ 매수일로 강화)
            ft5 = s.get("foreign_trend_5d")
            ft20 = s.get("foreign_trend_20d")
            if ft5 is not None and ft5 >= 0.7 and ft20 is not None and ft20 < 0.4:
                alerts.append(f"🔥 {name}: 외인 매수 전환 (5d {ft5:.0%} vs 20d {ft20:.0%})")

            # 공매도 비중 과열
            sr = s.get("short_ratio", 0)
            if sr and sr >= 10:
                alerts.append(f"⚠️ {name}: 공매도 {sr:.1f}% 과열")

            # 공매도 숏커버
            sc5 = s.get("short_change_5d")
            if sc5 is not None and sc5 <= -20:
                alerts.append(f"📊 {name}: 숏커버 진행 ({sc5:+.1f}%)")

            # 이평선 수렴 (abs<1.5% AND 수렴 중, 5/5 노이즈 컷)
            # ma_spread_change_10d < 0 = 10일 전보다 spread 좁아짐(=수렴 중)
            spread = s.get("ma_spread")
            spread_chg = s.get("ma_spread_change_10d")
            if (spread is not None and abs(spread) < 1.5
                and spread_chg is not None and spread_chg < 0):
                alerts.append(f"📊 {name}: 이평선 수렴 ({spread:+.1f}%, 10d {spread_chg:+.1f})")

            # RSI 과매도
            rsi = s.get("rsi14")
            if rsi is not None and rsi < 30:
                alerts.append(f"📉 {name}: RSI {rsi:.1f} 과매도")

        if alerts:
            msg = f"📡 *워치 변화 감지* ({now.strftime('%m/%d')})\n\n" + "\n".join(alerts)
            await _safe_send(context, msg)

        save_json(change_sent_file, {"date": today, "sent": True})
        _sent["watch_change"] = _key
        save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"watch_change_detect 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림: 레짐 전환 가이드 (전환 확정 시)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
