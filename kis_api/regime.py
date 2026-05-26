"""시장 국면(Regime) 판단 로직."""
import os
import json
import re
import asyncio
import aiohttp
import sqlite3
import xml.etree.ElementTree as ET
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from ._config import *
from ._config import (
    KIS_BASE_URL, KIS_APP_KEY, KIS_APP_SECRET, KST, ET, _DATA_DIR, _DB_PATH,
    WATCHLIST_FILE, STOPLOSS_FILE, US_WATCHLIST_FILE, DART_SEEN_FILE,
    PORTFOLIO_FILE, WATCHALERT_FILE, WATCH_SENT_FILE, STOPLOSS_SENT_FILE,
    US_HOLDINGS_SENT_FILE, DECISION_LOG_FILE, COMPARE_LOG_FILE,
    WATCHLIST_LOG_FILE, EVENTS_FILE, WEEKLY_BASE_FILE, UNIVERSE_FILE,
    CONSENSUS_CACHE_FILE, PORTFOLIO_HISTORY_FILE, TRADE_LOG_FILE,
    SECTOR_FLOW_CACHE_FILE, SECTOR_ROTATION_FILE, SUPPLY_HISTORY_FILE,
    REPORTS_FILE, REGIME_STATE_FILE, MACRO_SENT_FILE, TOKEN_CACHE_FILE,
    GITHUB_TOKEN, _BACKUP_GIST_ENV, _BACKUP_FILES_LIST, MACRO_SYMBOLS,
    DART_BASE_URL,
)
from ._session import _get_session, _kis_get, _kis_headers, get_kis_token, _token_cache
from ._helpers import (
    _is_us_ticker, _guess_excd, _is_us_market_hours_kst, _is_us_market_closed,
    DART_KEYWORDS, _load_knu_senti_lex, _FINANCE_PHRASE_SCORES, _RANKING_RE,
    _US_POSITIVE_KEYWORDS, _US_NEGATIVE_KEYWORDS, _NYSE_TICKERS, _AMEX_TICKERS,
)
from ._files import (
    load_json, save_json, load_watchlist, load_stoploss, load_us_watchlist,
    load_dart_seen, load_watchalert, _wa_market, load_kr_watch_tickers,
    load_us_watch_tickers, load_kr_watch_dict, load_us_watch_dict,
    load_decision_log, load_trade_log, save_trade_log, get_trade_stats,
    load_consensus_cache, load_sector_flow_cache, save_sector_flow_cache,
    load_compare_log, load_watchlist_log, append_watchlist_log, load_events,
)


def compute_turbulence(sp: list, kospi: list,
                       usdkrw: list, wti: list,
                       window: int = 60):
    """Turbulence Index (마할라노비스 거리). Returns dict or None."""
    import numpy as np
    ml = min(len(sp), len(kospi), len(usdkrw), len(wti))
    if ml < window + 2:
        return None

    def _ret(arr):
        return np.diff(np.log(np.array(arr[-ml:], dtype=float)))

    R = np.column_stack([_ret(sp), _ret(kospi), _ret(usdkrw), _ret(wti)])
    n = len(R)
    if n < window + 1:
        return None

    cov_win = R[-(window + 1):-1]
    cov_mat = np.cov(cov_win, rowvar=False)
    try:
        cov_inv = np.linalg.inv(cov_mat)
    except np.linalg.LinAlgError:
        cov_inv = np.linalg.pinv(cov_mat)

    mean_v = np.mean(cov_win, axis=0)
    diff = R[-1] - mean_v
    turb = float(diff @ cov_inv @ diff)

    # 히스토리 95퍼센타일
    turb_hist = []
    for i in range(window + 1, n):
        cw = R[i - window:i]
        cm = np.cov(cw, rowvar=False)
        try:
            ci = np.linalg.inv(cm)
        except np.linalg.LinAlgError:
            ci = np.linalg.pinv(cm)
        mv = np.mean(cw, axis=0)
        d = R[i] - mv
        turb_hist.append(float(d @ ci @ d))

    p95 = float(np.percentile(turb_hist, 95)) if turb_hist else turb * 2
    return {"value": round(turb, 2), "threshold_95": round(p95, 2),
            "alert": turb > p95}


def _regime_label(score: float) -> tuple:
    """점수 → (emoji, 한글, 영문)"""
    if score >= 70:
        return ("🟢", "공격", "offensive")
    elif score >= 40:
        return ("🟡", "중립", "neutral")
    else:
        return ("🔴", "위기", "defensive")


_REGIME_ORDER = {"offensive": 2, "neutral": 1, "defensive": 0}


def apply_debounce(new_score: float, state: dict) -> dict:
    """디바운스 적용 → state 업데이트 반환."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    _, _, new_regime = _regime_label(new_score)
    prev_regime = state.get("regime", new_regime)
    prev_pending = state.get("pending_regime", "")

    if new_regime == prev_regime:
        state["regime"] = new_regime
        state["consecutive_days"] = state.get("consecutive_days", 0) + 1
        state["pending_regime"] = ""
        state["pending_days"] = 0
    elif new_regime == prev_pending:
        pd = state.get("pending_days", 0) + 1
        state["pending_days"] = pd
        is_worse = _REGIME_ORDER.get(new_regime, 1) < _REGIME_ORDER.get(prev_regime, 1)
        threshold = 2 if is_worse else 3
        if pd >= threshold:
            state["regime"] = new_regime
            state["consecutive_days"] = pd
            state["pending_regime"] = ""
            state["pending_days"] = 0
    else:
        state.setdefault("regime", prev_regime)
        state["pending_regime"] = new_regime
        state["pending_days"] = 1

    state["date"] = today
    return state


def _calc_regime_v2() -> dict:
    """S&P 500 200MA + VIX 기반 레짐 판정 (조건부 로직)."""
    indicators = {}

    # 1. S&P 500 vs 200MA
    sp_signal = "🟡"
    sp_data = {"price": None, "sma200": None, "distance_pct": None,
               "sma200_slope": None, "signal": "🟡"}
    try:
        sp_hist = _yf_history("^GSPC", "1y")
        if len(sp_hist) >= 220:
            price = sp_hist[-1]
            sma200 = sum(sp_hist[-200:]) / 200
            sma200_20d_ago = sum(sp_hist[-220:-20]) / 200
            dist_pct = (price - sma200) / sma200 * 100
            slope_change = (sma200 - sma200_20d_ago) / sma200_20d_ago * 100
            slope = "rising" if slope_change > 0.3 else ("declining" if slope_change < -0.3 else "flat")
            sp_data = {
                "price": round(price, 2),
                "sma200": round(sma200, 2),
                "distance_pct": round(dist_pct, 2),
                "sma200_slope": slope,
                "signal": "🟢" if dist_pct > 3 else ("🔴" if dist_pct < -3 else "🟡"),
            }
    except Exception as e:
        print(f"[regime] S&P 조회 실패: {e}")
    indicators["sp500_vs_200ma"] = sp_data

    # 2. VIX + VIX 텀스트럭처
    vix_data = {"value": None, "vix3m": None, "term_ratio": None,
                "backwardation": False, "signal": "🟡"}
    try:
        vix_hist = _yf_history("^VIX", "1mo")
        vix_val = vix_hist[-1] if vix_hist else None

        vix3m_val = None
        try:
            v3m_hist = _yf_history("^VIX3M", "1mo")
            vix3m_val = v3m_hist[-1] if v3m_hist else None
        except Exception:
            pass
        if vix3m_val is None:
            try:
                v9d_hist = _yf_history("^VIX9D", "1mo")
                vix3m_val = v9d_hist[-1] if v9d_hist else None
            except Exception:
                pass

        if vix_val:
            term_ratio = round(vix_val / vix3m_val, 4) if vix3m_val and vix3m_val > 0 else None
            backwardation = bool(term_ratio and term_ratio > 1.0)
            sig = "🟢" if vix_val < 20 else ("🔴" if (vix_val > 30 or backwardation) else "🟡")
            vix_data = {
                "value": round(vix_val, 2),
                "vix3m": round(vix3m_val, 2) if vix3m_val else None,
                "term_ratio": term_ratio,
                "backwardation": backwardation,
                "signal": sig,
            }
    except Exception as e:
        print(f"[regime] VIX 조회 실패: {e}")
    indicators["vix"] = vix_data

    # 3. 레짐 판정 (조건부)
    sp_dist = sp_data.get("distance_pct")
    sp_slope = sp_data.get("sma200_slope")
    vix_val = vix_data.get("value")
    vix_back = vix_data.get("backwardation", False)

    regime_en = "neutral"
    logic_parts = []

    # 🟢 Offensive
    if (sp_dist is not None and sp_dist > 3 and
        vix_val is not None and vix_val < 20 and
        sp_slope == "rising"):
        regime_en = "offensive"
        logic_parts.append(f"S&P +{sp_dist:.2f}% above 200MA (🟢)")
        logic_parts.append(f"VIX {vix_val:.1f} < 20 (🟢)")
        logic_parts.append("SMA200 rising → 🟢 Offensive")
    # 🔴 Crisis
    elif (sp_dist is not None and sp_dist < -3 and
          vix_val is not None and (vix_val > 30 or vix_back)):
        regime_en = "crisis"
        logic_parts.append(f"S&P {sp_dist:.2f}% below 200MA (🔴)")
        if vix_val > 30:
            logic_parts.append(f"VIX {vix_val:.1f} > 30 (🔴) → 🔴 Crisis")
        else:
            logic_parts.append(f"VIX backwardation (term_ratio={vix_data['term_ratio']:.3f}) → 🔴 Crisis")
    else:
        if sp_dist is not None:
            logic_parts.append(f"S&P {sp_dist:+.2f}% from 200MA")
        if vix_val is not None:
            logic_parts.append(f"VIX {vix_val:.1f}")
        logic_parts.append("→ 🟡 Neutral")

    return {
        "regime_en": regime_en,
        "indicators": indicators,
        "logic": " AND ".join(logic_parts),
    }


def _regime_emoji(regime_en: str) -> str:
    return {"offensive": "🟢 탐욕", "neutral": "🟡 중립", "crisis": "🔴 공포"}.get(regime_en, "🟡 중립")


async def _fetch_usd_krw_value() -> dict:
    """USD/KRW 환율 (참고용, 레짐 판정에 미사용)."""
    usd_krw = None
    try:
        fx = await get_yahoo_quote("KRW=X")
        if fx:
            usd_krw = float(fx.get("price", 0) or 0)
    except Exception:
        pass
    return {
        "value": round(usd_krw, 1) if usd_krw else None,
        "note": "참고용 (레짐 판정에 미사용)",
    }


def _calc_tranche_level(vix_val: float | None) -> int | None:
    """VIX 트랜치 레벨 (🔴 내부 단계). VIX 30~40=1, 40~50=2, 50+=3."""
    if vix_val is None:
        return None
    if vix_val < 30:
        return None
    if vix_val < 40:
        return 1
    if vix_val < 50:
        return 2
    return 3


async def cmd_regime(mode: str = "current", days: int = 5,
                     regime: str = "", reason: str = "", **_kwargs) -> dict:
    """시장 레짐 판정 v2 — S&P 500 200MA + VIX 2개 지표 기반 조건부 로직."""
    state = load_json(REGIME_STATE_FILE, {"history": [], "current": {}})

    # ── override ──
    if mode == "override":
        if regime not in ("crisis", "neutral", "offensive"):
            return {"error": "regime must be one of: crisis, neutral, offensive"}
        today = datetime.now(KST).strftime("%Y-%m-%d")
        entry = {"date": today, "regime": regime, "override": True,
                 "reason": reason or "수동 강제"}
        state["current"] = {
            "current": regime,
            "days_in_regime": 1, "debounce_count": 99, "confirmed": True,
            "tranche_level": None, "last_updated": today,
            "override": True, "override_reason": reason or "수동 강제",
        }
        state.setdefault("history", []).append(entry)
        state["history"] = state["history"][-90:]
        save_json(REGIME_STATE_FILE, state)
        return {"regime": _regime_emoji(regime), "regime_en": regime,
                "mode": "override", "reason": reason, "date": today}

    # ── history ──
    if mode == "history":
        h = state.get("history", [])
        return {"history": h[-days:], "total_records": len(h)}

    # ── current ──
    today = datetime.now(KST).strftime("%Y-%m-%d")
    calc = _calc_regime_v2()
    new_regime = calc["regime_en"]
    indicators = calc["indicators"]
    vix_val = indicators["vix"]["value"]

    cur = state.get("current", {}) or {}
    prev_regime = cur.get("current", "neutral")
    debounce_count = int(cur.get("debounce_count", 0) or 0)
    days_in_regime = int(cur.get("days_in_regime", 0) or 0)
    last_updated = cur.get("last_updated", "")
    same_day_call = (last_updated == today)

    # 디바운스 로직 — 같은 날 중복 호출 시 카운트 누적 안 함 (버그 수정)
    confirmed_regime = prev_regime
    if new_regime == prev_regime:
        # 같은 레짐 유지 — 다른 날 호출일 때만 +1
        if not same_day_call:
            debounce_count += 1
            days_in_regime += 1
        confirmed_regime = prev_regime
    else:
        # 다른 레짐 감지 → 디바운스 카운트 (다른 날 호출일 때만 증가)
        if cur.get("pending_regime") == new_regime:
            if not same_day_call:
                debounce_count += 1
        else:
            debounce_count = 1

        threshold = 5 if new_regime == "offensive" else (3 if new_regime == "crisis" else 1)

        # 🟢→🟡, 🔴→🟡 즉시 가능 (Crisis exit는 별도 조건)
        if new_regime == "neutral":
            if prev_regime == "offensive":
                confirmed_regime = "neutral"
                debounce_count = 1
                days_in_regime = 1
            elif prev_regime == "crisis":
                sp_dist = indicators["sp500_vs_200ma"].get("distance_pct")
                if (vix_val is not None and vix_val < 25) or (sp_dist is not None and sp_dist > -3):
                    confirmed_regime = "neutral"
                    debounce_count = 1
                    days_in_regime = 1
                else:
                    confirmed_regime = prev_regime
        elif debounce_count >= threshold:
            confirmed_regime = new_regime
            days_in_regime = 1

    pending = new_regime if confirmed_regime != new_regime else None
    tranche = _calc_tranche_level(vix_val) if confirmed_regime == "crisis" else None

    # USD/KRW (참고용, indicators에 포함)
    indicators["usd_krw"] = await _fetch_usd_krw_value()

    # state 저장
    new_state_cur = {
        "current": confirmed_regime,
        "days_in_regime": days_in_regime,
        "debounce_count": debounce_count,
        "confirmed": confirmed_regime == new_regime,
        "tranche_level": tranche,
        "pending_regime": pending,
        "last_updated": today,
        "indicators": indicators,
    }
    state["current"] = new_state_cur
    state["prev_regime"] = prev_regime  # 텔레그램 알림용

    # history 기록 — 어떤 위치든 같은 날짜 entry는 단일 row 보장
    h_entry = {"date": today, "regime": confirmed_regime,
               "sp_distance_pct": indicators["sp500_vs_200ma"].get("distance_pct"),
               "vix": vix_val}
    hist = state.get("history", [])
    hist = [h for h in hist if h.get("date") != today]
    hist.append(h_entry)
    hist.sort(key=lambda h: h.get("date", ""))
    state["history"] = hist[-90:]
    save_json(REGIME_STATE_FILE, state)

    # 결과 조립
    debounce_msg = (
        f"{_regime_emoji(confirmed_regime)} {days_in_regime}일차 (확정)"
        if pending is None
        else f"→{_regime_emoji(pending)} 전환 대기 {debounce_count}일차"
    )

    return {
        "regime": _regime_emoji(confirmed_regime),
        "regime_en": confirmed_regime,
        "indicators": indicators,
        "tranche_level": tranche,
        "debounce": {
            "current": confirmed_regime,
            "days": days_in_regime,
            "confirmed": pending is None,
            "pending": pending,
            "text": debounce_msg,
        },
        "logic": calc["logic"],
        "date": today,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# YouTube 자막 추출
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_YT_URL_RE = re.compile(
    r"(?:v=|vi=|/v/|/vi/|/shorts/|/embed/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})"
)


