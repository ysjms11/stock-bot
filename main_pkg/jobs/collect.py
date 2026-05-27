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

# ── daily_collect_job, daily_collect_sanity_check ──

async def daily_collect_job(context):
    """장후 KIS API 풀수집 (18:30 KST, 평일)."""
    if not _HAS_DB_COLLECTOR:
        return

    # 주말 이중 가드
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return

    try:
        report = await asyncio.wait_for(collect_daily(), timeout=2400)  # 40분
    except asyncio.TimeoutError:
        await context.bot.send_message(chat_id=CHAT_ID, text="⚠️ DB 수집 40분 초과 타임아웃")
        cnt = _track_silent_failure("daily_collect_error", threshold=2)
        if cnt:
            await _alert_silent_failure(context, "daily_collect_error", cnt,
                f"daily_collect_job 연속 {cnt}회 타임아웃")
        return
    except Exception as e:
        print(f"[daily_collect] 오류: {e}")
        cnt = _track_silent_failure("daily_collect_error", threshold=2)
        if cnt:
            await _alert_silent_failure(context, "daily_collect_error", cnt,
                f"daily_collect_job 연속 {cnt}회 실패\n오류: {e}")
        return

    if report.get("skipped"):
        return  # 주말/공휴일 조용히 스킵

    if "error" not in report:
        _PHASE_KR = {"basic": "시세/밸류", "overtime": "시간외", "supply": "수급", "short": "공매도"}
        dur = report['duration']
        msg = (f"📊 DB 수집 완료\n"
               f"종목: {report['total']}개 | 소요: {int(dur//60)}분 {int(dur%60)}초")
        for phase, pr in report.get("phases", {}).items():
            name = _PHASE_KR.get(phase, phase)
            msg += f"\n  {name}: {pr['success']}✓ {pr['failed']}✗"
        await context.bot.send_message(chat_id=CHAT_ID, text=msg)
        _reset_silent_failure("daily_collect_error")
        try:
            from db_collector import backup_to_icloud
            backup_to_icloud()
        except Exception as e:
            print(f"[backup] iCloud 백업 실패: {e}")
    else:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"⚠️ DB 수집 실패: {report['error']}")
        cnt = _track_silent_failure("daily_collect_error", threshold=2)
        if cnt:
            await _alert_silent_failure(context, "daily_collect_error", cnt,
                f"daily_collect_job 연속 {cnt}회 실패\n오류: {report['error']}")


async def daily_collect_sanity_check(context):
    """평일 저녁 정기 자가진단 — 당일 daily_snapshot 0건이면 collect_daily 재실행.

    스케줄: 19:15 / 20:15 / 21:15 / 22:15 (18:30 정규잡 실패 방어).
    2026-04-24 18:30 미실행 사건(ccd 세션 retry로 이벤트루프 블록 추정) 재발 방지.
    """
    if not _HAS_DB_COLLECTOR:
        return
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    today = now.strftime("%Y%m%d")
    try:
        from db_collector import _get_db
        conn = _get_db()
        row = conn.execute(
            "SELECT COUNT(*) FROM daily_snapshot WHERE trade_date=?",
            (today,),
        ).fetchone()
        conn.close()
        if row and row[0] > 0:
            return  # 이미 수집 완료
    except Exception as e:
        print(f"[sanity] DB 체크 실패: {e}")
        return

    hhmm = now.strftime("%H:%M")
    print(f"[sanity {hhmm}] 당일 ({today}) daily_snapshot 0건 — collect_daily 재시작")
    try:
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"⚠️ daily_collect 미실행 감지 ({today} {hhmm}) — 재실행 시작",
        )
    except Exception:
        pass
    await daily_collect_job(context)


