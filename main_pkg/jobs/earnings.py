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

# ── check_earnings_calendar, check_us_earnings_calendar, check_dividend_calendar ──

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
                await _safe_send(context, msg)
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
            await _safe_send(context, msg)
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
            await _safe_send(context, msg)
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
            await _safe_send(context, msg)
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
