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

# ── daily_event_d1_alert, weekly_*_notify ──

async def daily_event_d1_alert(context: ContextTypes.DEFAULT_TYPE):
    """매일 19:30 KST — 내일 발생할 주요 이벤트(FOMC/어닝) D-1 알림.

    events.json에서 tomorrow 날짜 매칭 + 핵심 키워드 (FOMC/AMD/NVDA/SK하이닉스/HD현대일렉/효성/LS/삼성전자/AVGO/CRSP/LITE/코웨이/HD조선)
    감지 시 Polymarket + Treasury 시그널 첨부해서 발송.
    """
    now = datetime.now(KST)
    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_event_d1"
    if _sent.get("event_d1") == _key:
        return

    try:
        from kis_api import fetch_external_macro_signals, EVENTS_FILE as _EV_FILE
        events = load_json(_EV_FILE, {})
        if not isinstance(events, dict):
            return

        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        # 핵심 키워드 (보유/워치 종목 + FOMC + 매크로 지표)
        IMPORTANT_KEYWORDS = (
            "FOMC", "CPI", "PPI", "고용", "GDP",
            "AMD", "NVDA", "SK하이닉스", "HD현대일렉", "효성", "LS_ELECTRIC", "LS ELECTRIC",
            "삼성전자", "AVGO", "CRSP", "LITE", "코웨이", "HD조선", "한국조선해양",
        )
        matches = []
        for k, v in events.items():
            if not isinstance(v, str):
                continue
            if v == tomorrow and any(kw in k for kw in IMPORTANT_KEYWORDS):
                matches.append(k)

        if not matches:
            return  # 내일 핵심 이벤트 없음

        is_fomc = any("FOMC" in m for m in matches)
        is_macro = any(kw in m for m in matches for kw in ("CPI", "PPI", "GDP", "고용"))

        msg = f"🔥 *D-1 이벤트 알림* ({(now + timedelta(days=1)).strftime('%m/%d')})\n\n"
        msg += "내일 핵심 이벤트:\n"
        for m in matches[:8]:
            msg += f"• {m}\n"

        # FOMC/매크로 이벤트면 Polymarket + Treasury 첨부
        if is_fomc or is_macro:
            try:
                ext = await fetch_external_macro_signals(top_polymarket=4)
                ext_section = _format_external_signals(ext)
                if ext_section:
                    msg += ext_section
            except Exception as _e:
                print(f"[event_d1] 외부 시그널 실패: {_e}")

        msg += "\n\n_헤지 vs 풀 노출 결정 시간._"

        # 5/8 fix: Markdown 파싱 실패 시 plain text fallback
        ok = await _safe_send(context, msg, parse_mode="Markdown",
                                disable_web_page_preview=True)
        if not ok:
            return
        _sent["event_d1"] = _key
        save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"daily_event_d1_alert 오류: {e}")


async def weekly_sat_port_check_notify(context: ContextTypes.DEFAULT_TYPE):
    """매주 토요일 09:00 — SAT_PORT_CHECK 시작 알림."""
    now = datetime.now(KST)
    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_sat_port_check"
    if _sent.get("sat_port_check") == _key:
        return
    try:
        msg = (
            "🛡️ *토요일 포트폴리오 점검 시간*\n\n"
            "방어 모드 · 디폴트 HOLD · 30~40분\n\n"
            "🤖 Claude.ai 붙여넣기:\n"
            "```\n"
            "data/SAT_PORT_CHECK (토요일_포트관리).md 보고 진행해\n"
            "```"
        )
        await _safe_send(context, msg, disable_web_page_preview=True)
        _sent["sat_port_check"] = _key
        save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"weekly_sat_port_check_notify 오류: {e}")


async def weekly_sun_discovery_notify(context: ContextTypes.DEFAULT_TYPE):
    """매주 일요일 09:00 — SUN_DISCOVERY 시작 알림."""
    now = datetime.now(KST)
    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_sun_discovery"
    if _sent.get("sun_discovery") == _key:
        return
    try:
        msg = (
            "🔍 *일요일 신규 발굴 시간*\n\n"
            "탐색 모드 · 워치 thesis review 80% · 60~90분\n\n"
            "🤖 Claude.ai 붙여넣기:\n"
            "```\n"
            "data/SUN_DISCOVERY (일요일_신규발굴).md 보고 진행해\n"
            "```"
        )
        await _safe_send(context, msg, disable_web_page_preview=True)
        _sent["sun_discovery"] = _key
        save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"weekly_sun_discovery_notify 오류: {e}")


async def weekly_report_digest_notify(context: ContextTypes.DEFAULT_TYPE):
    """매주 일요일 19:00 — 비종목 리포트 분석 시작 알림.

    봇 역할: 통계 + 프롬프트 템플릿만 (판단 X). 실제 분석은 Claude.ai에서.
    """
    if not _REPORT_AVAILABLE:
        return
    now = datetime.now(KST)
    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_weekly_report_digest"
    if _sent.get("weekly_report_digest") == _key:
        return

    try:
        import sqlite3
        # 1주일치 카테고리별 카운트
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        conn = sqlite3.connect(REPORT_DB_PATH, timeout=10)
        conn.execute("PRAGMA cache_size = -65536")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA mmap_size = 268435456")
        conn.execute("PRAGMA busy_timeout = 30000")
        counts = {}
        for cat in ("industry", "strategy", "economy", "market"):
            cur = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(full_text)), 0) "
                "FROM reports WHERE category=? AND date >= ?",
                (cat, cutoff),
            )
            cnt, chars = cur.fetchone()
            counts[cat] = (cnt, chars)
        conn.close()

        total_cnt = sum(c for c, _ in counts.values())
        total_chars = sum(ch for _, ch in counts.values())
        token_estimate = int(total_chars * 1.2 / 1000)

        msg = (
            f"📊 *이번주 비종목 리포트 분석 시간*\n"
            f"({(now - timedelta(days=7)).strftime('%m/%d')}~{now.strftime('%m/%d')})\n\n"
            f"수집 현황:\n"
            f"• 산업 {counts['industry'][0]}건 / 전략 {counts['strategy'][0]}건 / "
            f"경제 {counts['economy'][0]}건 / 시황 {counts['market'][0]}건\n"
            f"• 합계 {total_cnt}건, 약 {token_estimate}K 토큰\n\n"
            f"🤖 Claude.ai에 그대로 붙여넣기:\n"
            f"```\n"
            f"이번주 산업·전략·경제 리포트 텍스트 싹 읽고, "
            f"가치 있어 보이는 거 PDF 풀 정독해서 새 투자 아이디어 5개 뽑아줘. "
            f"내 포트 직격 종목 thesis 점검도.\n"
            f"```\n\n"
            f"📱 https://claude.ai"
        )

        await _safe_send(context, msg, disable_web_page_preview=True)
        _sent["weekly_report_digest"] = _key
        save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"weekly_report_digest_notify 오류: {e}")



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 텔레그램 명령어
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
