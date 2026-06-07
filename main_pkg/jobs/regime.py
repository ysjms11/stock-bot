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
        emoji_map = {"offensive": "🟢", "neutral": "🟡", "crisis": "🔴"}
        dial_map = {
            "offensive": "평상 — 현금 5~8% 상비",
            "neutral":   "경계 — 현금 8~15% 실탄 비축",
            "crisis":    "발사 — 비축현금 투입·풀투자 지향(현금 최소)",
        }

        trans_file = f"{REGIME_STATE_FILE.rsplit('/', 1)[0]}/regime_transition_sent.json"
        trans_sent = load_json(trans_file, {})

        changed = False
        lines = []

        for mkt, flag, ind_fmt in [
            ("kr", "🇰🇷 KR", "kr"),
            ("us", "🇺🇸 US", "us"),
        ]:
            blk = state.get(mkt, {})
            curr_en = blk.get("current", "")
            if not curr_en:
                continue
            prev_sent = trans_sent.get(mkt, "")
            if not prev_sent:
                # 최초 — 잡음 방지: 기록만, 알림 없음
                trans_sent[mkt] = curr_en
                changed = True
                continue
            if prev_sent == curr_en:
                continue
            # 전환 발생
            prev_e = emoji_map.get(prev_sent, "?")
            curr_e = emoji_map.get(curr_en, "?")
            ind = blk.get("indicators", {})
            if mkt == "kr":
                vol_pct = ind.get("vol_pct")
                ma_dist = ind.get("ma_dist")
                _v = vol_pct if vol_pct is not None else "?"
                _m = ma_dist if ma_dist is not None else "?"
                ind_str = f"변동성 {_v}%ile | 200MA {_m}%"
            else:
                sp_dist = ind.get("sp_dist")
                vix_pct = ind.get("vix_pct")
                _s = sp_dist if sp_dist is not None else "?"
                _x = vix_pct if vix_pct is not None else "?"
                ind_str = f"S&P {_s}% | VIX {_x}%ile"
            dial_str = dial_map.get(curr_en, curr_en)
            lines.append(f"{flag} {prev_e}→{curr_e}\n{ind_str}\n💰 {dial_str}")
            trans_sent[mkt] = curr_en
            changed = True

        if changed:
            save_json(trans_file, trans_sent)

        if not lines:
            return

        msg = "🔄 *레짐 전환 (현금 다이얼)*\n\n" + "\n\n".join(lines)
        await _safe_send(context, msg)
    except Exception as e:
        print(f"regime_transition_alert 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림: Sunday 30 리마인더 (일 19:00)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
