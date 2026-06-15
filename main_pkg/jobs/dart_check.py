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

# ── check_dart_disclosure ──

async def check_dart_disclosure(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    if not (8 <= now.hour < 20):
        return
    if not DART_API_KEY:
        return

    try:
        disclosures = await search_dart_disclosures(days_back=1)
        if not disclosures:
            return

        # 관심 기업명 목록 (워치리스트 + 포트폴리오 + watchalert)
        watchlist = load_watchlist()
        portfolio = load_json(PORTFOLIO_FILE, {})
        wa = load_json(WATCHALERT_FILE, {})
        wl_names = list(watchlist.values())
        wl_names += [v.get("name", "") for k, v in portfolio.items()
                     if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)]
        wl_names += [v.get("name", "") for v in wa.values() if isinstance(v, dict)]
        wl_names = list(set(n for n in wl_names if n))

        # 중요 공시 필터링
        important = filter_important_disclosures(disclosures, wl_names)
        if not important:
            return

        # 이미 알림 보낸 공시 제외
        seen_data = load_dart_seen()
        seen_ids = set(seen_data.get("ids", []))

        new_disclosures = [d for d in important if d.get("rcept_no", "") not in seen_ids]
        if not new_disclosures:
            return

        msg = f"📢 *DART 공시 알림* ({now.strftime('%H:%M')})\n\n"
        new_ids = []

        # 요약 파싱 대상 키워드
        _DART_SUMMARY_KEYWORDS = (
            "잠정실적", "영업(잠정)실적",
            "자기주식취득결정", "자기주식 취득",
            "주식소각", "자기주식소각",
            "현금배당", "현금·현물배당", "현금ㆍ현물배당", "배당결정",
            "풍문", "해명",
        )

        for d in new_disclosures[:5]:  # 최대 5개
            corp = d.get("corp_name", "?")
            title = d.get("report_nm", "?")
            date = d.get("rcept_dt", "?")
            rcept_no = d.get("rcept_no", "")
            link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

            msg += f"🏢 *{corp}*\n"
            msg += f"📄 {title}\n"
            msg += f"📅 {date}\n"

            # 🆕 요약 시도 (실패해도 알림은 계속 발송)
            if any(kw in title for kw in _DART_SUMMARY_KEYWORDS):
                try:
                    stock_code = (d.get("stock_code", "") or "").strip() or "000000"
                    body_text = await fetch_and_cache_disclosure(stock_code, rcept_no)
                    if body_text:
                        parsed = parse_disclosure_summary(title, body_text)
                        if parsed and parsed.get("summary"):
                            for line in parsed["summary"]:
                                msg += f"{line}\n"
                except Exception as _e:
                    print(f"[DART 알림] 요약 파싱 실패 {rcept_no}: {_e}")

            msg += f"🔗 [공시 원문]({link})\n\n"

            new_ids.append(rcept_no)

        msg += "💡 Claude에서 영향 분석하세요"

        # 발송 성공 후에만 seen_ids 저장 (중복 발송 방지)
        ok = await _safe_send_dart(context, msg, disable_web_page_preview=True)
        if ok:
            seen_ids.update(new_ids)
            seen_list = list(seen_ids)[-500:]
            save_json(DART_SEEN_FILE, {"ids": seen_list})

    except Exception as e:
        print(f"DART 체크 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림: 내부자 클러스터 매수 감지 (매일 20:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSIDER_SENT_FILE = f"{_DATA_DIR}/insider_sent.json"
INSIDER_CLUSTER_MIN_BUYERS = 3  # 30일 내 매수자 3명+ 시 플래그
INSIDER_COOLDOWN_DAYS = 7       # 종목당 알림 재발송 쿨다운


