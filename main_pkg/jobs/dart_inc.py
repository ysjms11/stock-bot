"""main_pkg jobs — auto-split from main.py. See main_pkg/__init__.py."""
import asyncio
import os
import json
import re
import calendar as _cal
from datetime import datetime, timedelta, time as dtime

from main_pkg._ctx import (
    _KR_SECTORS, _SECTOR_LIMIT, _STOCK_LIMIT,
    _is_kr_trading_time, _read_regime, _safe_send, _safe_send_dart,
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
    import db_collector as _db_collector_mod  # noqa: F401
    _HAS_DB_COLLECTOR = True
except ImportError:
    _HAS_DB_COLLECTOR = False

# ── daily_dart_incremental, daily_dart_disclosure_collect ──

async def daily_dart_incremental(context):
    """매일 02:00 KST — DART 신규 정기공시 증분 수집 + 알파 메트릭 재계산.

    days=2 (전날+당일)로 중복 허용 → 놓친 공시 복구 여지.
    max_calls=1000 으로 DART 분당 1000콜 상한 보호.
    신규 수집>0일 때만 텔레그램 알림.
    """
    if not _HAS_DB_COLLECTOR:
        return
    try:
        from db_collector import collect_financial_on_disclosure
    except ImportError as e:
        print(f"[dart_incr] collect_financial_on_disclosure import 실패: {e}")
        return

    try:
        # 타임아웃 20분 (최악: 1000콜 × 0.067초 ≈ 67초, 여유 포함)
        report = await asyncio.wait_for(
            collect_financial_on_disclosure(days=2, max_calls=1000),
            timeout=1200,
        )
    except asyncio.TimeoutError:
        print("[dart_incr] 20분 타임아웃")
        try:
            await _safe_send_dart(context, "⚠️ DART 증분 수집 20분 초과 타임아웃", parse_mode=None)
        except Exception:
            pass
        return
    except Exception as e:
        print(f"[dart_incr] 오류: {e}")
        return

    newly = report.get("newly_collected", 0)
    if newly <= 0:
        # 조용히 스킵 (신규 공시 없음 — 대다수 평일이 그럼)
        print(f"[dart_incr] 신규 공시 없음 — 공시 {report.get('disclosures_found',0)}건, "
              f"중복 {report.get('already_in_db',0)}")
        cnt = _track_silent_failure("dart_incr_zero", threshold=3)
        if cnt:
            await _alert_silent_failure(context, "dart_incr_zero", cnt,
                f"DART 증분 0건 연속 {cnt}일 — 공시 발견 {report.get('disclosures_found',0)}건")
        return

    _reset_silent_failure("dart_incr_zero")
    alpha = report.get("alpha_recalc") or {}
    alpha_line = ""
    if isinstance(alpha, dict) and "success" in alpha:
        alpha_line = (f"\n• 알파 재계산: {alpha.get('success', 0)}종목 "
                      f"(F:{alpha.get('fscore_filled',0)} / "
                      f"M:{alpha.get('mscore_filled',0)} / "
                      f"FCF:{alpha.get('fcf_filled',0)})")
    elif isinstance(alpha, dict) and "error" in alpha:
        alpha_line = f"\n• 알파 재계산 실패: {alpha['error'][:60]}"

    msg = (
        f"📥 DART 증분 수집 완료\n"
        f"• 공시 발견: {report.get('disclosures_found',0)}건\n"
        f"• 신규 수집: {newly}건\n"
        f"• 기존 중복: {report.get('already_in_db',0)}건\n"
        f"• 쿼터 사용: {report.get('quota_used_estimate',0)}콜\n"
        f"• 소요: {report.get('duration_sec',0):.0f}초"
        f"{alpha_line}"
    )
    try:
        await _safe_send_dart(context, msg, parse_mode=None)
    except Exception as e:
        print(f"[dart_incr] 텔레그램 전송 실패: {e}")


async def daily_dart_disclosure_collect(context):
    """매일 04:05 KST — DART 5%룰(D001) + 10%룰(D002) 매일 증분 수집.

    schedule.md 기록: '04:05 dart_disclosure 매일 (~10초)'.
    4/28 신규 추가 후 jq.run_daily 등록 누락 사고 (학습 #13). 5/9 fix.
    """
    try:
        r5 = await collect_dart_5pct_changes(days=2)
        r10 = await collect_dart_10pct_insiders(days=2)
        print(f"[dart_disclosure] 5pct={r5} / 10pct={r10}", flush=True)
    except Exception as e:
        print(f"[dart_disclosure] 오류: {e}", flush=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📊 매크로 대시보드 (매일 18:00 + 06:00 KST)
