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

# ── check_stoploss ──

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
            await _safe_send(context, msg)
            for ticker, _ in full_alerts:
                _increment_stoploss_sent(sent, ticker, today)
        except Exception as e:
            print(f"손절 알림 전송 오류: {e}")

    if remind_alerts:
        lines = [text for _, text in remind_alerts]
        msg = "🔔 *손절선 리마인더*\n\n" + "\n".join(lines)
        try:
            await _safe_send(context, msg)
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
                        # gap = (buy_price - cur) / buy_price * 100, 음수=이하 진입
                        gap_pct = (cur - buy_price) / buy_price * 100 if buy_price > 0 else 0
                        if _is_us_ticker(ticker):
                            buy_alerts.append(
                                f"🟢🇺🇸 *{info['name']}* ({ticker})\n"
                                f"  현재가: ${cur:,.2f} ≤ 매수희망가 ${buy_price:,.2f} ({gap_pct:+.1f}%)\n"
                                + (f"  📝 {memo}\n" if memo else "")
                                + "  → *매수 검토!*"
                            )
                        else:
                            buy_alerts.append(
                                f"🟢🇰🇷 *{info['name']}* ({ticker})\n"
                                f"  현재가: {cur:,}원 ≤ 매수희망가 {buy_price:,.0f}원 ({gap_pct:+.1f}%)\n"
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

                msg = "🟢🟢🟢 *매수 감시가 진입!* 🟢🟢🟢\n_(현재가가 매수희망가 이하로 진입)_\n\n" + "\n\n".join(buy_alerts) + "\n" + extra + "\n\n→ 채팅에서 매수 검토"
                # 5/8 fix: Markdown 파싱 실패 시 plain text fallback
                await _safe_send(context, msg, parse_mode="Markdown")
    except Exception as e:
        print(f"매수감시 체크 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 4: 복합 이상 신호 (30분마다)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_anomaly_fired: dict = {}   # {"date": "YYYY-MM-DD", "sent": set()}


