"""포트폴리오 스냅샷, 드로다운 체크."""
import os
import json
import re
import asyncio
import aiohttp
import sqlite3
import xml.etree.ElementTree as ET
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from ._config import *
from ._config import (
    KIS_BASE_URL, KIS_APP_KEY, KIS_APP_SECRET, KST, ET, _DATA_DIR, _DB_PATH,
    WATCHLIST_FILE, STOPLOSS_FILE, US_WATCHLIST_FILE, DART_SEEN_FILE,
    PORTFOLIO_FILE, WATCHALERT_FILE, WATCH_SENT_FILE, STOPLOSS_SENT_FILE,
    US_HOLDINGS_SENT_FILE, DECISION_LOG_FILE, COMPARE_LOG_FILE,
    WATCHLIST_LOG_FILE, EVENTS_FILE, WEEKLY_BASE_FILE, UNIVERSE_FILE,
    CONSENSUS_CACHE_FILE, PORTFOLIO_HISTORY_FILE, TRADE_LOG_FILE,
    SECTOR_FLOW_CACHE_FILE, SECTOR_ROTATION_FILE, SUPPLY_HISTORY_FILE,
    REPORTS_FILE, REGIME_STATE_FILE, MACRO_SENT_FILE, TOKEN_CACHE_FILE,
    GITHUB_TOKEN, _BACKUP_GIST_ENV, _BACKUP_FILES_LIST, MACRO_SYMBOLS,
    DART_BASE_URL,
)
from ._session import _get_session, _kis_get, _kis_headers, get_kis_token, _token_cache
from ._helpers import (
    _is_us_ticker, _guess_excd, _is_us_market_hours_kst, _is_us_market_closed,
    DART_KEYWORDS, _load_knu_senti_lex, _FINANCE_PHRASE_SCORES, _RANKING_RE,
    _US_POSITIVE_KEYWORDS, _US_NEGATIVE_KEYWORDS, _NYSE_TICKERS, _AMEX_TICKERS,
)
from ._files import (
    load_json, save_json, load_watchlist, load_stoploss, load_us_watchlist,
    load_dart_seen, load_watchalert, _wa_market, load_kr_watch_tickers,
    load_us_watch_tickers, load_kr_watch_dict, load_us_watch_dict,
    load_decision_log, load_trade_log, save_trade_log, get_trade_stats,
    load_consensus_cache, load_sector_flow_cache, save_sector_flow_cache,
    load_compare_log, load_watchlist_log, append_watchlist_log, load_events,
)
# C1 분할 시 누락된 cross-module import
from .us_stock import get_yahoo_quote
from .kr_stock import batch_stock_detail
from .websocket import ws_manager


async def save_portfolio_snapshot(token: str) -> dict:
    """장마감 후 포트폴리오 스냅샷 저장 (/data/portfolio_history.json).
    KR: KIS 배치조회 / US: KIS 해외현재가 / 현금: portfolio.json의 cash_krw, cash_usd"""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    portfolio = load_json(PORTFOLIO_FILE, {})
    kr_stocks = {k: v for k, v in portfolio.items()
                 if k != "us_stocks" and not _is_us_ticker(k) and isinstance(v, dict)}
    us_stocks  = portfolio.get("us_stocks", {})
    cash_krw   = float(portfolio.get("cash_krw", 0) or 0)
    cash_usd   = float(portfolio.get("cash_usd", 0) or 0)

    # USD/KRW 환율
    try:
        fx = await get_yahoo_quote("KRW=X")
        usd_krw = float(fx.get("price", 1300) or 1300) if fx else 1300.0
    except Exception:
        usd_krw = 1300.0

    # KR 평가 (배치 조회)
    kr_eval = 0.0
    holdings: dict = {}
    if kr_stocks:
        batch = await batch_stock_detail(list(kr_stocks.keys()), token, delay=0.2)
        for row in batch:
            ticker = row.get("ticker", "")
            if row.get("error") or not ticker:
                continue
            price = row.get("price", 0)
            qty   = kr_stocks.get(ticker, {}).get("qty", 0)
            eval_amt = price * qty
            kr_eval += eval_amt
            holdings[ticker] = {"price": price, "qty": qty, "eval": int(eval_amt)}

    # US 평가
    us_eval_usd = 0.0
    for sym, info in us_stocks.items():
        try:
            cached = ws_manager.get_cached_price(sym)
            if cached is not None:
                price = float(cached)
            else:
                d = await _fetch_us_price_simple(sym, token)
                price = float(d.get("last", 0) or 0)
                await asyncio.sleep(0.2)
            qty   = info.get("qty", 0)
            eval_usd = round(price * qty, 2)
            us_eval_usd += eval_usd
            holdings[sym] = {"price": price, "qty": qty, "eval_usd": eval_usd}
        except Exception:
            pass

    us_eval_krw   = us_eval_usd * usd_krw
    cash_usd_krw  = cash_usd * usd_krw
    total_eval_krw  = int(kr_eval + us_eval_krw)
    total_asset_krw = int(kr_eval + us_eval_krw + cash_krw + cash_usd_krw)

    # 비중 계산
    for ticker, h in holdings.items():
        ev = h.get("eval", 0) or (h.get("eval_usd", 0) * usd_krw)
        h["weight_pct"] = round(ev / total_asset_krw * 100, 1) if total_asset_krw > 0 else 0.0

    cash_weight_pct = round((cash_krw + cash_usd_krw) / total_asset_krw * 100, 1) if total_asset_krw > 0 else 0.0

    snapshot = {
        "date": today,
        "total_eval_krw": total_eval_krw,
        "cash_krw": int(cash_krw),
        "cash_usd": round(cash_usd, 2),
        "usd_krw_rate": round(usd_krw, 1),
        "total_asset_krw": total_asset_krw,
        "kr_eval": int(kr_eval),
        "us_eval_krw": int(us_eval_krw),
        "holdings": holdings,
        "cash_weight_pct": cash_weight_pct,
    }

    history = load_json(PORTFOLIO_HISTORY_FILE, {"snapshots": []})
    snaps = [s for s in history.get("snapshots", []) if s.get("date") != today]
    snaps.append(snapshot)
    snaps = sorted(snaps, key=lambda x: x.get("date", ""))
    if len(snaps) > 365:
        snaps = snaps[-365:]
    save_json(PORTFOLIO_HISTORY_FILE, {"snapshots": snaps})
    print(f"[snapshot] 저장: {today}, 총자산 {total_asset_krw:,}원")
    return snapshot


async def _fetch_us_price_simple(sym: str, token: str) -> dict:
    """해외 현재가 단순 조회 (save_portfolio_snapshot 전용)"""
    s = _get_session()
    excd = _guess_excd(sym)
    _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price",
        "HHDFS00000300", token, {"AUTH": "", "EXCD": excd, "SYMB": sym})
    return d.get("output", {})


def check_drawdown() -> dict:
    """portfolio_history.json 기반 드로다운·주간/월간 수익률 분석 + 투자규칙 경고.
    스냅샷 부족 시 해당 지표는 None."""
    history = load_json(PORTFOLIO_HISTORY_FILE, {"snapshots": []})
    snaps = sorted(history.get("snapshots", []), key=lambda x: x.get("date", ""))

    def _total(s):
        return s.get("total_asset_krw") or s.get("total_eval_krw") or 0

    weekly_return = monthly_return = monthly_max_dd = None

    if len(snaps) >= 2:
        today_total = _total(snaps[-1])
        if len(snaps) >= 6:
            week_total = _total(snaps[-6])
            if week_total > 0:
                weekly_return = round((today_total - week_total) / week_total * 100, 2)
        if len(snaps) >= 21:
            month_total = _total(snaps[-21])
            if month_total > 0:
                monthly_return = round((today_total - month_total) / month_total * 100, 2)
            month_highs = [_total(s) for s in snaps[-21:] if _total(s) > 0]
            if month_highs:
                peak = max(month_highs)
                monthly_max_dd = round((today_total - peak) / peak * 100, 2) if peak > 0 else None
    else:
        today_total = 0

    alerts = []
    if weekly_return is not None and weekly_return <= -4:
        alerts.append({"level": "WARNING",
                        "message": f"주간 손실 {weekly_return:.1f}% > -4% 한도. 이번 주 신규매수 금지"})
    if monthly_max_dd is not None and monthly_max_dd <= -7:
        alerts.append({"level": "CRITICAL",
                        "message": f"월간 드로다운 {monthly_max_dd:.1f}% > -7% 한도. 신규매수 중단 + 포트 점검 필요"})
    elif monthly_return is not None and monthly_return <= -7:
        alerts.append({"level": "CRITICAL",
                        "message": f"월간 수익률 {monthly_return:.1f}% > -7% 한도. 신규매수 중단 + 포트 점검 필요"})

    # 연속 손절 카운트 (decision_log actions 에서 매도/정리/손절 키워드)
    consecutive_stops = 0
    try:
        dec_log = load_decision_log()
        entries = sorted(dec_log.values(), key=lambda x: x.get("date", ""), reverse=True)
        for entry in entries[:10]:
            actions_text = " ".join(entry.get("actions", []))
            if any(kw in actions_text for kw in ["매도", "정리", "손절"]):
                consecutive_stops += 1
            else:
                break
    except Exception:
        pass

    if consecutive_stops >= 3:
        alerts.append({"level": "CRITICAL",
                        "message": f"연속 손절 {consecutive_stops}회. 48시간 매매 중단 권고"})

    cash_weight = snaps[-1].get("cash_weight_pct") if snaps else None

    return {
        "snapshot_count": len(snaps),
        "weekly_return_pct": weekly_return,
        "monthly_return_pct": monthly_return,
        "monthly_max_drawdown_pct": monthly_max_dd,
        "consecutive_stops": consecutive_stops,
        "trading_suspended": consecutive_stops >= 3,
        "cash_weight_pct": cash_weight,
        "alerts": alerts,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
