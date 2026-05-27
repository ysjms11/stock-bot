"""공유 상수 및 공통 헬퍼 — 모든 main_pkg 모듈이 여기서 import."""
import os
import json
import asyncio
from datetime import datetime

from kis_api import *
from kis_api import (
    _DATA_DIR, _is_us_ticker, _is_us_market_hours_kst, _is_us_market_closed, _guess_excd,
    ws_manager, get_ws_tickers, close_session,
    fetch_us_earnings_calendar, fetch_us_sector_etf,
    fetch_and_cache_disclosure, parse_disclosure_summary,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TELEGRAM 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TELEGRAM_TOKEN, CHAT_ID, KST, ET 등은 kis_api star-import로 주입됨

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 섹터 분류 (포트 비중 경고용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_KR_SECTORS = {
    "조선":   {"009540"},
    "전력기기": {"298040", "010120", "267260"},
}
_SECTOR_LIMIT = 50   # 섹터 한도 %
_STOCK_LIMIT  = 35   # 단일종목 한도 %

_REGIME_EMOJI = {"offensive": "🟢", "neutral": "🟡", "crisis": "🔴"}


def _is_kr_trading_time(now=None):
    """평일 08:00~18:00 KST 여부"""
    if now is None:
        now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    if not (8 <= now.hour < 18):
        return False
    return True


def _read_regime() -> tuple[str, str]:
    """regime_state.json에서 (regime_en, emoji) 반환."""
    state = load_json(REGIME_STATE_FILE, {})
    cur = state.get("current", {})
    regime_en = cur.get("current", "neutral")
    return regime_en, _REGIME_EMOJI.get(regime_en, "⚪")


async def _safe_send(context, text: str, parse_mode: str = "Markdown", **kwargs) -> bool:
    """텔레그램 메시지 안전 발송.
    - 1차: parse_mode 시도
    - 2차: parse 실패 시 plain text fallback
    Returns: 발송 성공 시 True
    """
    try:
        await context.bot.send_message(chat_id=CHAT_ID, text=text,
                                       parse_mode=parse_mode, **kwargs)
        return True
    except Exception as e:
        emsg = str(e).lower()
        if "parse entities" in emsg or "can't find end of the entity" in emsg or "can't parse entities" in emsg:
            try:
                await context.bot.send_message(chat_id=CHAT_ID, text=text, **kwargs)
                print(f"[telegram] Markdown 파싱 실패 → plain text 발송 (offset 추적: {str(e)[:80]})")
                return True
            except Exception as e2:
                print(f"[telegram] plain text fallback 실패: {e2}")
                return False
        else:
            print(f"[telegram] 발송 실패: {e}")
            return False


def _track_silent_failure(key: str, threshold: int = 3) -> int:
    """silent failure 카운트 추적. threshold 도달하고 오늘 알림 미발송이면 카운트 반환."""
    log = load_json(SILENT_FAILURE_LOG, {})
    today = datetime.now(KST).strftime("%Y-%m-%d")
    entry = log.get(key, {"count": 0, "first_failure": today, "last_alerted": None})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_failure"] = today
    log[key] = entry
    save_json(SILENT_FAILURE_LOG, log)
    if entry["count"] >= threshold and entry.get("last_alerted") != today:
        return entry["count"]
    return 0


def _reset_silent_failure(key: str) -> None:
    """잡 성공 시 카운트 리셋."""
    log = load_json(SILENT_FAILURE_LOG, {})
    if key in log:
        del log[key]
        save_json(SILENT_FAILURE_LOG, log)


async def _alert_silent_failure(context, key: str, count: int, message: str) -> None:
    """텔레그램 알림 + last_alerted 갱신 (24h cooldown)."""
    log = load_json(SILENT_FAILURE_LOG, {})
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if key in log:
        log[key]["last_alerted"] = today
        save_json(SILENT_FAILURE_LOG, log)
    try:
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"🚨 *Silent failure 감지*\n\n{message}\n\n_{count}일/회 연속 누적_",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"[silent_failure] 알림 전송 실패: {e}")


def _extract_grade(entry: dict, ticker: str, name: str) -> str | None:
    """decision_log entry에서 종목의 확신등급 추출"""
    grades = entry.get("grades", {})
    for key in [ticker, name]:
        gv = grades.get(key)
        if gv is None:
            continue
        if isinstance(gv, str):
            return gv
        elif isinstance(gv, dict):
            return gv.get("grade")
    return None


def _grade_arrow(prev: str, cur: str) -> str:
    """등급 변동 화살표 문자열. 변동 없거나 null이면 ''"""
    if not prev or not cur or prev == cur:
        return ""
    order = {"S": -1, "A": 0, "B": 1, "C": 2, "D": 3}
    if order.get(cur, 9) < order.get(prev, 9):
        return f" ⬆️{prev}→{cur}"
    return f" ⬇️{prev}→{cur}"


def _refresh_ws_coro():
    """WebSocket 구독 목록 갱신 코루틴 팩토리."""
    async def _do():
        try:
            await ws_manager.update_tickers(get_ws_tickers())
        except Exception as e:
            print(f"[WS] refresh 오류: {e}")
    return _do()
