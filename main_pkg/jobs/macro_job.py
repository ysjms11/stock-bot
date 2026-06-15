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

# ── _format_external + _format_overtime + macro_dashboard ──

def _format_external_signals(ext: dict) -> str:
    """외부 시그널 섹션 포맷 (Polymarket + Treasury). 매크로 대시보드 + D-1 알림 공용.

    Args:
        ext: fetch_external_macro_signals() 결과
    Returns: "\n[외부 시그널]\n• ..." 또는 ""
    """
    if not ext or ext.get("error"):
        return ""

    lines = ["\n[외부 시그널 — 돈 걸린 베팅]"]

    # Treasury Curve
    tr = ext.get("treasury", {})
    sp = tr.get("spread_10y_2y")
    sp_1w = tr.get("spread_10y_2y_1w_ago")
    sig = tr.get("recession_signal", "")
    if sp is not None:
        chg_str = ""
        if sp_1w is not None:
            d = sp - sp_1w
            chg_str = f" ({d:+.2f}pp 1주)"
        lines.append(f"• 10Y-2Y: {sp:+.2f}%{chg_str} — {sig}")

    # Fed Polymarket
    fed_data = ext.get("fed", {})
    fed_markets = fed_data.get("markets", []) if isinstance(fed_data, dict) else []
    if fed_markets:
        m = fed_markets[0]
        top_o = m.get("top_outcome", {}) or {}
        prob = top_o.get("prob")
        if prob is not None:
            outcome = top_o.get("outcome", "")[:20]
            chg = top_o.get("change_7d")
            chg_str = f" ({chg*100:+.0f}pp 1주)" if chg else ""
            title = m.get("title", "")[:30]
            lines.append(f"• Fed: {outcome} {prob*100:.0f}%{chg_str} ({title})")

    # Polymarket TOP 매크로/지정학
    poly = ext.get("polymarket", {})
    poly_markets = poly.get("markets", []) if isinstance(poly, dict) else []
    skip_first_fed = bool(fed_markets)
    shown = 0
    for m in poly_markets:
        # Fed 시장은 위에 따로 보였으니 스킵
        if skip_first_fed and "Fed" in m.get("title", ""):
            continue
        top_o = m.get("top_outcome", {}) or {}
        prob = top_o.get("prob")
        if prob is None:
            continue
        outcome = (top_o.get("outcome") or "")[:18]
        title = m.get("title", "")[:35]
        chg = top_o.get("change_7d")
        chg_str = f" ({chg*100:+.0f}pp 1주)" if chg and abs(chg) >= 0.02 else ""
        # 멀티 outcome이면 outcome도 표시
        if not m.get("is_binary") and outcome:
            lines.append(f"• {title} → {outcome} {prob*100:.0f}%{chg_str}")
        else:
            lines.append(f"• {title} {prob*100:.0f}%{chg_str}")
        shown += 1
        if shown >= 4:  # 최대 4개
            break

    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def _format_overtime_movers(data: dict) -> str:
    """시간외 급등락 섹션 포맷 (pm 슬롯 전용)"""
    movers = data.get("OVERTIME_MOVERS", {})
    top    = movers.get("top", [])
    bottom = movers.get("bottom", [])
    if not top and not bottom:
        return ""
    lines = ["\n[시간외 급등락]"]
    if top:
        lines.append("📈 " + " | ".join(f"{m['name']} {m['pct']:+.1f}%" for m in top))
    if bottom:
        lines.append("📉 " + " | ".join(f"{m['name']} {m['pct']:+.1f}%" for m in bottom))
    return "\n".join(lines)


async def macro_dashboard(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    # 18:35 실행: 평일만 / 06:00 실행: 일요일 제외 (토요일은 금요일 결과)
    if now.hour >= 12 and now.weekday() >= 5:
        return
    if now.hour < 12 and now.weekday() == 6:
        return

    # 중복 발송 방지: 같은 날짜_슬롯이면 스킵
    slot = "pm" if now.hour >= 12 else "am"
    slot_key = f"{now.strftime('%Y-%m-%d')}_{slot}"
    sent_data = load_json(MACRO_SENT_FILE, {})
    if sent_data.get("last") == slot_key:
        print(f"[macro_dashboard] 이미 발송됨: {slot_key}, 스킵")
        return

    try:
        data = await collect_macro_data()
        msg = format_macro_msg(data)

        # 섹터 로테이션 추가
        try:
            token = await get_kis_token()
            rot = await detect_sector_rotation(token)
            if rot.get("rotations"):
                msg += "\n[자금 이동] " + " | ".join(rot["rotations"])
            elif rot.get("top_inflow"):
                inflow_names = [s["name"] for s in rot["top_inflow"][:2]]
                msg += f"\n[자금 유입] {', '.join(inflow_names)}"
        except Exception:
            pass

        # 🆕 외부 시그널 — 돈 걸린 베팅 (Polymarket + Treasury)
        try:
            from kis_api import fetch_external_macro_signals
            ext = await fetch_external_macro_signals(top_polymarket=5)
            ext_section = _format_external_signals(ext)
            if ext_section:
                msg += ext_section
        except Exception as _e:
            print(f"[macro_dashboard] 외부 시그널 실패: {_e}")

        # pm 슬롯에만 시간외 급등락 추가
        if slot == "pm":
            overtime_section = _format_overtime_movers(data)
            if overtime_section:
                msg += overtime_section

        # 5/8 fix: parse 실패 시 plain text fallback
        ok = await _safe_send(context, msg, parse_mode="Markdown")
        if not ok:
            return  # 발송 실패 시 sent_data 갱신 안 함 (다음 호출 재시도)

        # 발송 성공 후 기록 — await 구간 동안 다른 잡이 파일에 쓸 수 있으므로
        # 여기서 파일을 다시 읽어 병합한다 (stale-read 덮어쓰기 방지)
        fresh = load_json(MACRO_SENT_FILE, {})
        fresh["last"] = slot_key
        save_json(MACRO_SENT_FILE, fresh)
    except Exception as e:
        print(f"매크로 대시보드 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 7: DART 공시 체크 (30분마다)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
