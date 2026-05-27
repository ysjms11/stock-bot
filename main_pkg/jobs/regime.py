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

# ── regime_transition_alert ──

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

        await _safe_send(context, msg)
        save_json(trans_file, {"transition": key, "date": datetime.now(KST).strftime("%Y-%m-%d")})
    except Exception as e:
        print(f"regime_transition_alert 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림: Sunday 30 리마인더 (일 19:00)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
