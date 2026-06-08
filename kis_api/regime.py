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
# C1 분할 시 누락된 cross-module import
from .us_stock import get_yahoo_quote
from .news import _yf_history


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


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 헬퍼 (무인증, FDR + yfinance)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _fdr_closes(symbol: str, years: int = 2) -> list:
    """FDR 종가 리스트 (오래된 순). 실패 시 []."""
    try:
        import FinanceDataReader as fdr
        from datetime import date
        start = (datetime.now(KST) - timedelta(days=int(years * 365))).strftime("%Y-%m-%d")
        df = fdr.DataReader(symbol, start)
        if df is None or df.empty:
            return []
        col = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
        return [float(v) for v in col.dropna().tolist()]
    except Exception as e:
        print(f"[regime/_fdr_closes] {symbol}: {e}")
        return []


def _realized_vol_series(closes: list, window: int = 20) -> list:
    """롤링 실현변동성 시리즈 (연율화%, 유효값만). numpy 사용."""
    import numpy as np
    arr = np.array(closes, dtype=float)
    if len(arr) < window + 2:
        return []
    log_ret = np.log(arr[1:] / arr[:-1])
    result = []
    for i in range(window - 1, len(log_ret)):
        window_ret = log_ret[i - window + 1: i + 1]
        rv = float(np.std(window_ret, ddof=1) * np.sqrt(252) * 100)
        result.append(rv)
    return result


def _pct_rank(series: list, lookback: int = 252) -> float | None:
    """series 마지막값의 트레일링 lookback 내 백분위(0~100). 데이터<30이면 None."""
    import numpy as np
    arr = [v for v in series if v is not None and not (isinstance(v, float) and np.isnan(v))]
    if len(arr) < 30:
        return None
    window = arr[-lookback:] if len(arr) >= lookback else arr
    last = window[-1]
    rank = sum(1 for x in window if x <= last) / len(window) * 100
    return round(rank, 1)


def _dist_from_ma(closes: list, w: int = 200) -> float | None:
    """(마지막 - SMA(w)) / SMA(w) * 100. 데이터 부족 시 None."""
    if len(closes) < w:
        return None
    sma = sum(closes[-w:]) / w
    if sma == 0:
        return None
    return round((closes[-1] - sma) / sma * 100, 2)


def _regime_emoji(regime_en: str) -> str:
    return {"offensive": "🟢 탐욕", "neutral": "🟡 중립", "crisis": "🔴 공포"}.get(regime_en, "🟡 중립")


async def _fetch_usd_krw_value() -> dict:
    """USD/KRW 환율 (참고용, 레짐 판정에 미사용)."""
    usd_krw = None
    try:
        fx = await get_yahoo_quote("KRW=X")
        if fx:
            usd_krw = float(fx.get("price", 0) or 0)
    except Exception as e:
        print(f"[regime] USD/KRW 조회 실패 (무시): {e}")
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


def _calc_regime_v2() -> dict:
    """하위호환 래퍼 — calc_us_regime()으로 위임. __init__.py import 유지용."""
    return calc_us_regime()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KR 레짐 — 원화 sleeve
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def calc_kr_regime() -> dict:
    """KOSPI 실현변동성 퍼센타일 주신호 + 200MA 거리 보조 → KR 레짐 판정."""
    closes = _fdr_closes("KS11", 2)

    # 데이터 부족 → neutral + 노트
    if len(closes) < 230:
        return {
            "market": "KR",
            "regime_en": "neutral",
            "regime": _regime_emoji("neutral"),
            "cash_posture": "경계 8~15% (실탄 비축)",
            "indicators": {
                "vol_pct": None, "vol_abs": None,
                "ma_dist": None, "usdkrw_chg60": None, "foreign_5d": None,
            },
            "confirmations": {},
            "logic": "KOSPI 데이터 부족(<230일) — 실현변동성 계산 불가, neutral 폴백",
        }

    rv_series = _realized_vol_series(closes, 20)
    vol_pct = _pct_rank(rv_series, 252)
    vol_abs = round(rv_series[-1], 2) if rv_series else None
    ma_dist = _dist_from_ma(closes, 200)

    # 확인용 지표 (점수 미포함)
    usdkrw_chg60 = None
    try:
        fx_closes = _fdr_closes("USD/KRW", 1)
        if len(fx_closes) >= 61:
            usdkrw_chg60 = round((fx_closes[-1] / fx_closes[-61] - 1) * 100, 2)
    except Exception as e:
        print(f"[regime/kr] USD/KRW 60일 변화 조회 실패 (무시): {e}")

    # 판정 (변동성 우선)
    logic_parts = []
    confirmations = {}

    if vol_pct is not None:
        pct_str = f"vol_pct={vol_pct:.1f}%ile"
    else:
        pct_str = f"vol_pct=None(데이터부족)"

    if vol_abs is not None:
        abs_str = f"vol_abs={vol_abs:.1f}%"
    else:
        abs_str = "vol_abs=None"

    # 확인 플래그 (게이팅 아님)
    if ma_dist is not None and ma_dist < -10:
        confirmations["ma_below_minus10"] = True
    if usdkrw_chg60 is not None and usdkrw_chg60 > 5:
        confirmations["usdkrw_surge"] = True

    # E 하이브리드 (백테스트 확정): 추세게이트 + 극단vol 우회, vol_abs 절대폴백 제거.
    #   지속형위기 7/8 적중(2008 26일前·2020 33일前 트로프前), whipsaw 35→11, 멜트업🔴 26→11.
    crisis_condition = (
        (vol_pct is not None and vol_pct > 80 and ma_dist is not None and ma_dist < -3)
        or (vol_pct is not None and vol_pct > 92)
    )
    if crisis_condition:
        regime_en = "crisis"
        if vol_pct is not None and vol_pct > 92:
            logic_parts.append(f"{pct_str} > 92%ile (극단 우회) → 🔴 Crisis (발사)")
        else:
            logic_parts.append(f"{pct_str} > 80%ile & 200MA {ma_dist:.2f}% < -3% (추세게이트) → 🔴 Crisis (발사)")
        if confirmations:
            logic_parts.append(f"확인: {confirmations}")
    elif (vol_pct is not None and 50 <= vol_pct <= 80) or (ma_dist is not None and ma_dist < -5):
        regime_en = "neutral"
        logic_parts.append(f"{pct_str} 50~80%ile 또는 ma_dist={ma_dist}")
        logic_parts.append("→ 🟡 Neutral")
    else:
        regime_en = "offensive"
        logic_parts.append(pct_str)
        if ma_dist is not None:
            logic_parts.append(f"ma_dist={ma_dist:+.2f}%")
        logic_parts.append("🔴/🟡 조건 미해당 → 🟢 Offensive")

    cash_posture_map = {
        "offensive": "평상 5~8%",
        "neutral": "경계 8~15% (실탄 비축)",
        "crisis": "🔴 발사 — 풀투자 지향(현금 최소)",
    }

    return {
        "market": "KR",
        "regime_en": regime_en,
        "regime": _regime_emoji(regime_en),
        "cash_posture": cash_posture_map[regime_en],
        "indicators": {
            "vol_pct": vol_pct,
            "vol_abs": vol_abs,
            "ma_dist": ma_dist,
            "usdkrw_chg60": usdkrw_chg60,
            "foreign_5d": None,  # best-effort, 현재 미수집
        },
        "confirmations": confirmations,
        "logic": " | ".join(logic_parts),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# US 레짐 — 달러 sleeve
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def calc_us_regime() -> dict:
    """S&P 500 200MA + VIX 퍼센타일 기반 US 레짐 판정."""
    # ── S&P 500 ──
    sp_data = {
        "price": None, "sma200": None, "distance_pct": None,
        "sma200_slope": None, "signal": "🟡",
    }
    sp_hist = _yf_history("^GSPC", "2y")
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

    # ── VIX + 퍼센타일 ──
    vix_data = {
        "value": None, "vix3m": None, "term_ratio": None,
        "backwardation": False, "signal": "🟡", "vix_pct": None,
    }
    try:
        vix_hist = _yf_history("^VIX", "2y")
        vix_val = vix_hist[-1] if vix_hist else None
        vix_pct = _pct_rank(vix_hist, 252)

        vix3m_val = None
        try:
            v3m_hist = _yf_history("^VIX3M", "1mo")
            vix3m_val = v3m_hist[-1] if v3m_hist else None
        except Exception as e:
            print(f"[regime/us] VIX3M 조회 실패 (무시): {e}")
        if vix3m_val is None:
            try:
                v9d_hist = _yf_history("^VIX9D", "1mo")
                vix3m_val = v9d_hist[-1] if v9d_hist else None
            except Exception as e:
                print(f"[regime/us] VIX9D 조회 실패 (무시): {e}")

        if vix_val:
            term_ratio = round(vix_val / vix3m_val, 4) if vix3m_val and vix3m_val > 0 else None
            backwardation = bool(term_ratio and term_ratio > 1.0)
            sig = "🟢" if (vix_pct is not None and vix_pct < 67) else ("🔴" if (vix_pct is not None and vix_pct > 90) or backwardation else "🟡")
            vix_data = {
                "value": round(vix_val, 2),
                "vix_pct": vix_pct,
                "vix3m": round(vix3m_val, 2) if vix3m_val else None,
                "term_ratio": term_ratio,
                "backwardation": backwardation,
                "signal": sig,
            }
    except Exception as e:
        print(f"[regime/us] VIX 조회 실패: {e}")

    # ── 판정 (퍼센타일 임계) ──
    sp_dist = sp_data.get("distance_pct")
    sp_slope = sp_data.get("sma200_slope")
    vix_val = vix_data.get("value")
    vix_pct = vix_data.get("vix_pct")
    vix_back = vix_data.get("backwardation", False)

    regime_en = "neutral"
    logic_parts = []

    vix_pct_str = f"vix_pct={vix_pct:.1f}%ile" if vix_pct is not None else "vix_pct=None"

    # 🟢 Offensive: dist>3 AND vix_pct<67 AND slope=="rising"
    if (sp_dist is not None and sp_dist > 3 and
            vix_pct is not None and vix_pct < 67 and
            sp_slope == "rising"):
        regime_en = "offensive"
        logic_parts.append(f"S&P +{sp_dist:.2f}% above 200MA (🟢)")
        logic_parts.append(f"{vix_pct_str} < 67%ile (🟢)")
        logic_parts.append("SMA200 rising → 🟢 Offensive")
    # 🔴 Crisis: dist<-3 AND (vix_pct>90 OR backwardation)
    elif (sp_dist is not None and sp_dist < -3 and
            ((vix_pct is not None and vix_pct > 90) or vix_back)):
        regime_en = "crisis"
        logic_parts.append(f"S&P {sp_dist:.2f}% below 200MA (🔴)")
        if vix_pct is not None and vix_pct > 90:
            logic_parts.append(f"{vix_pct_str} > 90%ile (🔴) → 🔴 Crisis (발사)")
        else:
            tr = vix_data.get("term_ratio")
            logic_parts.append(f"VIX backwardation(term_ratio={tr}) → 🔴 Crisis (발사)")
    # 🟡 Neutral
    else:
        if sp_dist is not None:
            logic_parts.append(f"S&P {sp_dist:+.2f}% from 200MA")
        logic_parts.append(f"{vix_pct_str}")
        logic_parts.append("→ 🟡 Neutral")

    cash_posture_map = {
        "offensive": "평상 5~8%",
        "neutral": "경계 8~15% (실탄 비축)",
        "crisis": "🔴 발사 — 풀투자 지향(현금 최소)",
    }

    return {
        "market": "US",
        "regime_en": regime_en,
        "regime": _regime_emoji(regime_en),
        "cash_posture": cash_posture_map[regime_en],
        "indicators": {
            "sp_dist": sp_dist,
            "sp_slope": sp_slope,
            "vix_val": vix_val,
            "vix_pct": vix_pct,
            "vix3m": vix_data.get("vix3m"),
            "backwardation": vix_back,
            "term_ratio": vix_data.get("term_ratio"),
        },
        "logic": " | ".join(logic_parts),
        # 백워드호환: 구 indicators 키 (dashboard_home.py 등)
        "_compat_indicators": {
            "sp500_vs_200ma": sp_data,
            "vix": vix_data,
        },
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 디바운스 — per-market
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _apply_regime_debounce(prev_cur: dict, new_regime: str, today: str) -> dict:
    """per-market 디바운스 적용. prev_cur = 시장별 현재 state dict.
    임계: crisis 진입 3일 / offensive 복귀 8일 / neutral 즉시(1).
    같은 날 중복호출 카운트 누적 금지.
    반환: {current, days_in_regime, debounce_count, pending_regime, confirmed, last_updated}
    """
    cur = prev_cur or {}
    current = cur.get("current", "neutral")
    days_in_regime = int(cur.get("days_in_regime", 0) or 0)
    debounce_count = int(cur.get("debounce_count", 0) or 0)
    last_updated = cur.get("last_updated", "")
    same_day_call = (last_updated == today)

    # E 하이브리드 임계값: 🔴 3일, 🟢 8일, 🟡 1일(즉시)
    _thresholds = {"crisis": 3, "offensive": 8, "neutral": 1}

    confirmed_regime = current
    pending_regime = cur.get("pending_regime")

    if new_regime == current:
        if not same_day_call:
            days_in_regime += 1
            debounce_count += 1
        pending_regime = None
        confirmed_regime = current
    else:
        # 다른 레짐 감지
        if pending_regime == new_regime:
            if not same_day_call:
                debounce_count += 1
        else:
            debounce_count = 1
            pending_regime = new_regime

        threshold = _thresholds.get(new_regime, 1)
        if debounce_count >= threshold:
            confirmed_regime = new_regime
            days_in_regime = 1
            pending_regime = None
        else:
            confirmed_regime = current

    return {
        "current": confirmed_regime,
        "days_in_regime": days_in_regime,
        "debounce_count": debounce_count,
        "pending_regime": pending_regime,
        "confirmed": (confirmed_regime == new_regime),
        "last_updated": today,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# cmd_regime — 통합 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def cmd_regime(mode: str = "current", market: str = "both", days: int = 5,
                     regime: str = "", reason: str = "", **_kwargs) -> dict:
    """시장 레짐 판정 v3 — KR(KOSPI 실현변동성) + US(S&P 200MA + VIX 퍼센타일) 이중 엔진."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    state = load_json(REGIME_STATE_FILE, {"kr": {}, "us": {}, "history": [], "current": {}, "prev_regime": "neutral"})

    # ── override ──
    if mode == "override":
        if regime not in ("crisis", "neutral", "offensive"):
            return {"error": "regime must be one of: crisis, neutral, offensive"}
        mkt_list = ["kr", "us"] if market in ("both", "") else [market.lower()]
        for mkt in mkt_list:
            if mkt in ("kr", "us"):
                state[mkt] = {
                    "current": regime,
                    "days_in_regime": 1, "debounce_count": 99,
                    "confirmed": True, "pending_regime": None,
                    "last_updated": today,
                    "override": True, "override_reason": reason or "수동 강제",
                }
        # 백워드호환 current 미러 (US 기준)
        us_cur = state.get("us", {})
        state["current"] = {
            "current": us_cur.get("current", regime),
            "days_in_regime": 1, "debounce_count": 99, "confirmed": True,
            "last_updated": today,
            "override": True, "override_reason": reason or "수동 강제",
            "indicators": {},  # FIX-2: 구조 대칭 (current 모드와 동일 키셋)
        }
        entry = {"date": today,
                 "regime": state["us"].get("current"),  # FIX-3: 백워드호환 키 (override branch)
                 "kr": state["kr"].get("current"), "us": state["us"].get("current"),
                 "override": True, "reason": reason or "수동 강제"}
        hist = state.get("history", [])
        hist = [h for h in hist if h.get("date") != today]
        hist.append(entry)
        state["history"] = hist[-90:]
        save_json(REGIME_STATE_FILE, state)
        return {"regime": _regime_emoji(regime), "regime_en": regime,
                "mode": "override", "market": market, "reason": reason, "date": today}

    # ── history ──
    if mode == "history":
        h = state.get("history", [])
        return {"history": h[-days:], "total_records": len(h)}

    # ── current ──
    kr_calc = calc_kr_regime()
    us_calc = calc_us_regime()

    # per-market 디바운스 적용
    kr_state = _apply_regime_debounce(state.get("kr", {}), kr_calc["regime_en"], today)
    us_state = _apply_regime_debounce(state.get("us", {}), us_calc["regime_en"], today)

    # 백워드호환 indicators (구 대시보드가 "sp500_vs_200ma", "vix" 키로 읽을 수 있도록)
    us_compat_indicators = us_calc.pop("_compat_indicators", {})
    us_calc_indicators = us_calc.get("indicators", {})

    # state 업데이트
    state["kr"] = {**kr_state,
                   "cash_posture": kr_calc["cash_posture"],
                   "indicators": kr_calc["indicators"]}
    state["us"] = {**us_state,
                   "cash_posture": us_calc["cash_posture"],
                   "indicators": us_calc_indicators}

    # FIX-1: state["current"] 덮어쓰기 직전에 직전 confirmed 캡처 (텔레그램 전환알림용)
    prev_us_confirmed = (state.get("current") or {}).get("current", "neutral")

    # ★ 백워드호환 current — US 미러 (구 _read_regime/대시보드)
    us_confirmed = us_state["current"]
    state["current"] = {
        "current": us_confirmed,
        "days_in_regime": us_state["days_in_regime"],
        "debounce_count": us_state["debounce_count"],
        "confirmed": us_state["confirmed"],
        "pending_regime": us_state.get("pending_regime"),
        "last_updated": today,
        "indicators": {**us_compat_indicators},
    }
    state["prev_regime"] = prev_us_confirmed   # FIX-1: 직전 confirmed 캡처 (텔레그램 전환알림용)

    # history 기록 (같은 날 단일 row)
    h_entry = {
        "date": today,
        "regime": us_confirmed,  # FIX-3: 백워드호환 — history 소비처 "regime" 키 기대
        "kr": kr_state["current"],
        "us": us_confirmed,
        "kr_vol_pct": kr_calc["indicators"].get("vol_pct"),
        "us_vix_pct": us_calc_indicators.get("vix_pct"),
        "us_sp_dist": us_calc_indicators.get("sp_dist"),
    }
    hist = state.get("history", [])
    hist = [h for h in hist if h.get("date") != today]
    hist.append(h_entry)
    hist.sort(key=lambda h: h.get("date", ""))
    state["history"] = hist[-90:]
    save_json(REGIME_STATE_FILE, state)

    # ── 결과 조립 (superset, 백워드호환 top-level = US 미러) ──
    us_pending = us_state.get("pending_regime")
    us_days = us_state["days_in_regime"]
    us_debounce_msg = (
        f"{_regime_emoji(us_confirmed)} {us_days}일차 (확정)"
        if us_pending is None
        else f"→{_regime_emoji(us_pending)} 전환 대기 {us_state['debounce_count']}일차"
    )

    kr_pending = kr_state.get("pending_regime")
    kr_confirmed = kr_state["current"]
    kr_days = kr_state["days_in_regime"]
    kr_debounce_msg = (
        f"{_regime_emoji(kr_confirmed)} {kr_days}일차 (확정)"
        if kr_pending is None
        else f"→{_regime_emoji(kr_pending)} 전환 대기 {kr_state['debounce_count']}일차"
    )

    return {
        # ★ 백워드호환: top-level = US 미러
        "regime_en": us_confirmed,
        "regime": _regime_emoji(us_confirmed),
        "cash_posture": us_calc["cash_posture"],
        "debounce": {
            "current": us_confirmed,
            "days": us_days,
            "confirmed": us_state["confirmed"],
            "pending": us_pending,
            "text": us_debounce_msg,
        },
        "indicators": {**us_compat_indicators},
        "logic": us_calc.get("logic", ""),
        # per-market 상세
        "kr": {
            **kr_calc,
            "debounce": {
                "current": kr_confirmed,
                "days": kr_days,
                "confirmed": kr_state["confirmed"],
                "pending": kr_pending,
                "text": kr_debounce_msg,
            },
        },
        "us": {
            **us_calc,
            "indicators": us_calc_indicators,
            "debounce": {
                "current": us_confirmed,
                "days": us_days,
                "confirmed": us_state["confirmed"],
                "pending": us_pending,
                "text": us_debounce_msg,
            },
        },
        "date": today,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# YouTube 자막 추출
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_YT_URL_RE = re.compile(
    r"(?:v=|vi=|/v/|/vi/|/shorts/|/embed/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})"
)


