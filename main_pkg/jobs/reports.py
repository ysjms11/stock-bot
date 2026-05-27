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

# ── collect_reports_daily ──

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
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
