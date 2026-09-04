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
    from db_collector import collect_daily, collect_dividends
    _HAS_DB_COLLECTOR = True
except ImportError:
    _HAS_DB_COLLECTOR = False

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
        if report.get("reason") == "holiday_duplicate_rollback":
            rolled = report.get("rolled_back") or {}
            # W3: plain text 발송 — _KR_MARKET_HOLIDAYS 같은 언더스코어 토큰이 Markdown
            # 파싱을 깨뜨릴 수 있어 이 모듈의 다른 알림과 동일하게 parse_mode 없이 보낸다.
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=f"📅 복제 감지({rolled.get('date', report.get('date', ''))}, "
                     f"{rolled.get('pct', '?')}%) — 휴장일 추정, "
                     f"{rolled.get('deleted', '?')}행 롤백. "
                     f"휴장일이면 _KR_MARKET_HOLIDAYS 등록",
            )
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
        # W1: 복제-감지됐지만 KIS 캔들상 실거래일로 확증된 경우 — 데이터는 보존했으나
        # 수집 이상이 의심되므로 별도 경고(휴장일 롤백 알림과 달리 조사 필요).
        if report.get("reason") == "duplicate_but_trading_day":
            dup = report.get("duplicate_check") or {}
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=f"⚠️ 복제 감지({dup.get('pct', '?')}%)지만 KIS 캔들상 거래일 — "
                     f"수집 이상 의심, 확인 필요",
            )
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


async def weekly_dividend_job(context):
    """주간 배당 DPS 수집 (일요일). KIS 예탁원으로 종목별 현금배당 → dividend_events 저장
    → div_yield 재계산. DPS는 sticky(연1회)라 주1회면 충분. ★KRX 불필요★."""
    if not _HAS_DB_COLLECTOR:
        return
    try:
        res = await asyncio.wait_for(collect_dividends(), timeout=1200)  # 20분
        print(f"[weekly_dividend] {res}")
    except asyncio.TimeoutError:
        print("[weekly_dividend] 20분 타임아웃")
    except Exception as e:
        print(f"[weekly_dividend] 오류: {e}")


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
        from db_collector import _is_kr_trading_day, is_rolled_back_today
        # _is_kr_trading_day는 "%Y%m%d" 문자열 전용 — date 객체를 넘기면 fail-open True.
        if not _is_kr_trading_day(today):
            print(f"[sanity] {today} 휴장일 → 스킵")
            return
        if is_rolled_back_today(today):
            print(f"[sanity] {today} 자가롤백 처리 완료 → 스킵 (재실행/알림 안 함)")
            return
    except Exception as e:
        print(f"[sanity] 휴장일/롤백 가드 확인 실패(무시): {e}")

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
    except Exception as e:
        print(f"[sanity] 알림 전송 실패 (무시): {e}")
    await daily_collect_job(context)


