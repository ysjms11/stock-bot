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
    KR: KIS 배치조회 / US: KIS REST → Yahoo → carry-forward(age<=2) / 현금: portfolio.json의 cash_krw, cash_usd
    2026-07 브레이커 입력 픽스: US 가격 경로에서 WS 캐시 제거(REST 우선 고정), NAV 체인(flow-adjusted,
    additive: nav_r/nav_index/holdings.price_ext/holdings.price_age_days) 계산 추가."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    portfolio = load_json(PORTFOLIO_FILE, {})
    kr_stocks = {k: v for k, v in portfolio.items()
                 if k != "us_stocks" and not _is_us_ticker(k) and isinstance(v, dict)}
    us_stocks  = portfolio.get("us_stocks", {})
    cash_krw   = float(portfolio.get("cash_krw", 0) or 0)
    cash_usd   = float(portfolio.get("cash_usd", 0) or 0)

    # 직전 스냅샷 (carry-forward + NAV 체인용 — 갭 체인, 날짜 연속 불필요)
    history = load_json(PORTFOLIO_HISTORY_FILE, {"snapshots": []})
    existing_snaps = sorted(
        [s for s in history.get("snapshots", []) if s.get("date") != today],
        key=lambda x: x.get("date", ""),
    )
    prev_snapshot = existing_snaps[-1] if existing_snaps else None
    prev_holdings = (prev_snapshot or {}).get("holdings", {}) or {}

    # USD/KRW 환율
    try:
        fx = await get_yahoo_quote("KRW=X")
        usd_krw = float(fx.get("price", 1300) or 1300) if fx else 1300.0
    except Exception:
        usd_krw = 1300.0

    # KR 평가 (배치 조회) — 무변경
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

    # US 평가 — 1) KIS REST 2) Yahoo 폴백 3) carry-forward(age<=2) 4) age>2 → 제외 (WS 캐시 완전 제거)
    us_eval_usd = 0.0
    for sym, info in us_stocks.items():
        try:
            qty = info.get("qty", 0)
            price = None
            age = 0

            # 1. KIS REST (기존 함수, 성공 시 age=0)
            try:
                d = await _fetch_us_price_simple(sym, token)
                p = float(d.get("last", 0) or 0)
                if p > 0:
                    price, age = p, 0
            except Exception:
                pass
            await asyncio.sleep(0.2)

            # 2. Yahoo 폴백 (성공 시 age=0)
            if price is None:
                try:
                    yq = await get_yahoo_quote(sym)
                    p = float((yq or {}).get("price", 0) or 0)
                    if p > 0:
                        price, age = p, 0
                except Exception:
                    pass

            # 3. carry-forward: 직전 스냅샷 가격(price_ext 우선, 없으면 price), age=직전age+1, age<=2까지만
            # 4. age>2 또는 직전 스냅샷에 해당 종목 없음 → 이 종목은 스냅샷에서 제외 + DQ 로그
            if price is None:
                prev_h = prev_holdings.get(sym)
                if prev_h:
                    prev_age = int(prev_h.get("price_age_days", 0) or 0)
                    carry_price = prev_h.get("price_ext")
                    if carry_price is None:
                        carry_price = prev_h.get("price")
                    new_age = prev_age + 1
                    if carry_price is not None and new_age <= 2:
                        price, age = float(carry_price), new_age
                    else:
                        print(f"[snapshot] DQ ERROR: {sym} carry-forward age {new_age} > 2 (한도 초과) — 스냅샷 제외")
                else:
                    print(f"[snapshot] DQ ERROR: {sym} REST+Yahoo 조회 실패, 직전 스냅샷 없어 carry-forward 불가 — 스냅샷 제외")

            if price is None:
                continue  # age>2 혹은 조달 완전 실패 — holdings 미기록

            eval_usd = round(price * qty, 2)
            us_eval_usd += eval_usd
            holdings[sym] = {
                "price": price, "qty": qty, "eval_usd": eval_usd,
                "price_age_days": age, "price_ext": price,
            }
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

    # ── NAV 체인 (flow-adjusted, additive: nav_r / nav_index) ──
    # r = [Σ q_prev·P_now·fx_now + cash_krw_prev + cash_usd_prev·fx_now]
    #   / [Σ q_prev·P_prev·fx_prev + cash_krw_prev + cash_usd_prev·fx_prev] − 1
    # 공통보유(prev∩now)만, q=prev qty, KR fx=1, US는 해당 시점 환율.
    # P_prev=prev의 price_ext 우선(없으면 price) — carry-forward/백필 스테일 저장가로 체인 오염 방지.
    if prev_snapshot is not None:
        prev_nav_index = prev_snapshot.get("nav_index")
        try:
            fx_prev = float(prev_snapshot.get("usd_krw_rate", 0) or 0)
            cash_krw_prev = float(prev_snapshot.get("cash_krw", 0) or 0)
            cash_usd_prev = float(prev_snapshot.get("cash_usd", 0) or 0)

            numerator   = cash_krw_prev + cash_usd_prev * usd_krw
            denominator = cash_krw_prev + cash_usd_prev * fx_prev

            for ticker, ph in prev_holdings.items():
                now_h = holdings.get(ticker)
                if now_h is None:
                    continue  # 공통보유(prev∩now)만
                q_prev = ph.get("qty", 0) or 0
                p_prev = ph.get("price_ext")
                if p_prev is None:
                    p_prev = ph.get("price")
                p_now = now_h.get("price_ext")
                if p_now is None:
                    p_now = now_h.get("price")
                if p_prev is None or p_now is None:
                    continue
                is_us = _is_us_ticker(ticker)
                fx_n = usd_krw if is_us else 1.0
                fx_p = fx_prev if is_us else 1.0
                numerator   += q_prev * float(p_now) * fx_n
                denominator += q_prev * float(p_prev) * fx_p

            if denominator > 0:
                nav_r = (numerator / denominator) - 1
                base_index = float(prev_nav_index) if prev_nav_index is not None else 100.0
                snapshot["nav_r"] = round(nav_r, 6)
                snapshot["nav_index"] = round(base_index * (1 + nav_r), 4)
            elif prev_nav_index is not None:
                # denominator<=0(체인 계산 불가) — 백필 스크립트와 동일하게
                # 직전 nav_index를 그대로 캐리포워드. nav_r은 수익률이 미정의이므로 기록 안 함.
                snapshot["nav_index"] = prev_nav_index
        except Exception as e:
            print(f"[snapshot] NAV 체인 계산 오류: {e}")
            if prev_nav_index is not None:
                snapshot["nav_index"] = prev_nav_index
    else:
        snapshot["nav_index"] = 100.0  # 최초 스냅샷 — 기준점

    snaps = existing_snaps + [snapshot]
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
    스냅샷 부족 시 해당 지표는 None.
    2026-07 브레이커 입력 픽스: nav_index(flow-adjusted) 우선 사용 — 윈도우 내 스냅샷 중
    nav_index가 하나라도 결측이면 구방식(_total, total_asset_krw 기준)으로 폴백.
    임계값(-4/-7)·알림 레벨·이후 로직(consecutive_stops 등)은 무변경."""
    history = load_json(PORTFOLIO_HISTORY_FILE, {"snapshots": []})
    snaps = sorted(history.get("snapshots", []), key=lambda x: x.get("date", ""))

    def _total(s):
        return s.get("total_asset_krw") or s.get("total_eval_krw") or 0

    def _nav(s):
        return s.get("nav_index")

    weekly_return = monthly_return = monthly_max_dd = None
    weekly_nav_based = monthly_nav_based = False

    if len(snaps) >= 2:
        today_total = _total(snaps[-1])
        if len(snaps) >= 6:
            week_window = snaps[-6:]
            if all(_nav(s) is not None for s in week_window):
                today_nav = _nav(snaps[-1])
                week_nav = _nav(snaps[-6])
                if week_nav > 0:
                    weekly_return = round((today_nav - week_nav) / week_nav * 100, 2)
                    weekly_nav_based = True
            else:
                week_total = _total(snaps[-6])
                if week_total > 0:
                    weekly_return = round((today_total - week_total) / week_total * 100, 2)
        if len(snaps) >= 21:
            month_window = snaps[-21:]
            if all(_nav(s) is not None for s in month_window):
                today_nav = _nav(snaps[-1])
                month_nav = _nav(snaps[-21])
                if month_nav > 0:
                    monthly_return = round((today_nav - month_nav) / month_nav * 100, 2)
                month_highs = [_nav(s) for s in month_window if _nav(s) and _nav(s) > 0]
                if month_highs:
                    peak = max(month_highs)
                    monthly_max_dd = round((today_nav - peak) / peak * 100, 2) if peak > 0 else None
                monthly_nav_based = True
            else:
                month_total = _total(snaps[-21])
                if month_total > 0:
                    monthly_return = round((today_total - month_total) / month_total * 100, 2)
                month_highs = [_total(s) for s in month_window if _total(s) > 0]
                if month_highs:
                    peak = max(month_highs)
                    monthly_max_dd = round((today_total - peak) / peak * 100, 2) if peak > 0 else None
    else:
        today_total = 0

    alerts = []
    if weekly_return is not None and weekly_return <= -4:
        suffix = " (flow-adjusted)" if weekly_nav_based else ""
        alerts.append({"level": "WARNING",
                        "message": f"주간 손실 {weekly_return:.1f}%{suffix} > -4% 한도. 이번 주 신규매수 금지"})
    if monthly_max_dd is not None and monthly_max_dd <= -7:
        suffix = " (flow-adjusted)" if monthly_nav_based else ""
        alerts.append({"level": "CRITICAL",
                        "message": f"월간 드로다운 {monthly_max_dd:.1f}%{suffix} > -7% 한도. 신규매수 중단 + 포트 점검 필요"})
    elif monthly_return is not None and monthly_return <= -7:
        suffix = " (flow-adjusted)" if monthly_nav_based else ""
        alerts.append({"level": "CRITICAL",
                        "message": f"월간 수익률 {monthly_return:.1f}%{suffix} > -7% 한도. 신규매수 중단 + 포트 점검 필요"})

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
