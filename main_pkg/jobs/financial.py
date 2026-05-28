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

try:
    from db_collector import collect_financial_weekly
    _HAS_DB_COLLECTOR = True
except ImportError:
    _HAS_DB_COLLECTOR = False

# ── weekly_financial_job ──

async def weekly_financial_job(context):
    """주 1회 재무 수집 (일요일 07:15 KST).

    스코프: 2,861종목 × 3 Phase (KIS IS + KIS BS + DART CF 4분기) ≈ 25분 + 오버헤드.
    타임아웃 60분 (30분 → 60분, 5/2 첫 타임아웃 사고 후 보강).
    """
    if not _HAS_DB_COLLECTOR:
        return
    try:
        result = await asyncio.wait_for(collect_financial_weekly(), timeout=7200)  # 120분 (2864종목×2phase ~50분)
        if isinstance(result, dict):
            t = result.get("tickers", 0)
            ist = result.get("income_statement", 0)
            bst = result.get("balance_sheet", 0)
            dft = result.get("dart_full", 0)
            msg = (
                "📊 주간 재무 수집 완료\n"
                f"• 종목: {t}\n"
                f"• 손익계산서: {ist}/{t}\n"
                f"• 대차대조표: {bst}/{t}\n"
                f"• DART CF (4분기): {dft}"
            )
        else:
            msg = "📊 주간 재무 수집 완료"
        await context.bot.send_message(chat_id=CHAT_ID, text=msg)
    except asyncio.TimeoutError:
        print("[weekly_financial] 120분 타임아웃")
        await context.bot.send_message(chat_id=CHAT_ID, text="⚠️ 주간 재무 수집 120분 초과 타임아웃")
    except Exception as e:
        print(f"[weekly_financial] 오류: {e}")
        try:
            await context.bot.send_message(chat_id=CHAT_ID, text=f"⚠️ 주간 재무 수집 오류: {type(e).__name__}: {e}")
        except Exception:
            pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 증분 수집 (매일 02:00 KST)
# collect_financial_on_disclosure: 지난 2일 정기공시만 수집 → 알파 재계산
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
