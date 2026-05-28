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
    import db_collector as _db_collector_mod  # noqa: F401
    _HAS_DB_COLLECTOR = True
except ImportError:
    _HAS_DB_COLLECTOR = False

try:
    from report_crawler import (collect_reports, get_collection_tickers,
                                  collect_market_reports)
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False

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
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text="⚠️ DART 증분 수집 20분 초과 타임아웃",
            )
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
        await context.bot.send_message(chat_id=CHAT_ID, text=msg)
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


async def collect_reports_daily(context: ContextTypes.DEFAULT_TYPE):
    """매일 08:30 KST — 보유+감시 종목 증권사 리포트 수집"""
    if not _REPORT_AVAILABLE:
        return
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return  # 주말 스킵

    # 중복 발송 방지
    _rpt_sent = load_json(MACRO_SENT_FILE, {})
    _rpt_key = f"{now.strftime('%Y-%m-%d')}_report"
    if _rpt_sent.get("report") == _rpt_key:
        print(f"[report_daily] 이미 발송됨: {_rpt_key}, 스킵")
        return

    try:
        tickers = get_collection_tickers()
        if not tickers:
            return

        loop = asyncio.get_running_loop()
        new_reports = await loop.run_in_executor(
            None, lambda: collect_reports(tickers, force_retry_meta_only=True)
        )

        # 비종목 리포트 (산업/시황/투자전략/경제) 수집
        try:
            market_reports = await loop.run_in_executor(
                None, collect_market_reports,
                ["industry", "market", "strategy", "economy"],
            )
        except Exception as e:
            print(f"[market_reports] 오류: {e}")
            market_reports = []

        if new_reports or market_reports:
            def _esc(s: str) -> str:
                """Telegram Markdown v1 특수문자 이스케이프"""
                for ch in ("*", "_", "`", "["):
                    s = s.replace(ch, "\\" + ch)
                return s

            # 종목별 그룹핑 → 각 종목 최신 1건만, 리포트 수 내림차순
            from collections import defaultdict
            by_name: dict = defaultdict(list)
            for r in new_reports:
                key = r.get("name") or r.get("ticker", "?")
                by_name[key].append(r)

            lines = []
            for name, reports in sorted(by_name.items(), key=lambda x: -len(x[1])):
                latest = max(reports, key=lambda x: x.get("date", ""))
                src   = _esc(latest.get("source", ""))
                title = _esc(latest.get("title", ""))[:30]
                date  = latest.get("date", "")[-5:]
                lines.append(f"• {_esc(name)} ({len(reports)}건) — {src} \"{title}\" ({date})")

            failed = sum(1 for r in new_reports if r.get("extraction_status") == "failed")
            header = f"📄 *증권사 리포트 수집* ({len(new_reports)}건, {len(by_name)}종목"
            if failed:
                header += f", 추출실패 {failed}건"
            header += ")"
            msg = header + "\n\n" + "\n".join(lines[:15])  # 최대 15종목
            if len(by_name) > 15:
                msg += f"\n... 외 {len(by_name) - 15}종목"

            # 비종목 카테고리 요약
            if market_reports:
                from collections import Counter
                cat_count = Counter(r.get("category", "") for r in market_reports)
                cat_label = {"industry": "🏭산업", "market": "🌐시황",
                             "strategy": "📊전략", "economy": "💹경제"}
                cat_summary = " / ".join(f"{cat_label.get(c, c)} {n}"
                                         for c, n in cat_count.most_common())
                msg += f"\n\n*비종목 ({len(market_reports)}건)*: {cat_summary}"

            await _safe_send(context, msg)

            # 발송 기록
            _rpt_sent["report"] = _rpt_key
            save_json(MACRO_SENT_FILE, _rpt_sent)
        _reset_silent_failure("report_daily_error")
    except Exception as e:
        print(f"[report_daily] 오류: {e}")
        cnt = _track_silent_failure("report_daily_error", threshold=3)
        if cnt:
            await _alert_silent_failure(context, "report_daily_error", cnt,
                f"collect_reports_daily 연속 {cnt}회 실패\n오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📊 매크로 대시보드 (매일 18:00 + 06:00 KST)
