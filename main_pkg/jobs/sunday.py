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

# ── sunday_30_reminder ──

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

        await _safe_send(context, msg)
        _sent["sunday_30"] = _key
        save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"sunday_30_reminder 오류: {e}")


