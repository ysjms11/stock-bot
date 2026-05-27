"""매크로 대시보드 데이터 수집 및 포맷."""
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
from .kr_stock import _fetch_market_investor_flow


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 매크로 대시보드
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_DEFAULT_EVENTS = {
    "FOMC":    "2026-04-28",
    "CPI":     "2026-04-10",
    "PPI":     "2026-04-11",
    "고용보고서": "2026-04-03",
    "다음FOMC": "2026-06-16",
    "이란":     "진행중",
}


def load_events() -> dict:
    """이벤트 캘린더 로드 (/data/events.json, 없으면 기본값으로 초기화)"""
    return load_json(EVENTS_FILE, _DEFAULT_EVENTS)


async def collect_macro_data() -> dict:
    """매크로 지표 전체 수집 — 텔레그램 자동발송 + MCP 공용"""
    data = {}

    # 1. Yahoo Finance 매크로 심볼
    for key, symbol in MACRO_SYMBOLS.items():
        try:
            q = await get_yahoo_quote(symbol)
            p = q.get("price", 0)
            c = q.get("change_pct", 0)
            data[key] = {
                "price":      round(float(p), 2) if p else "?",
                "change_pct": round(float(c), 2) if c is not None else "?",
            }
        except Exception:
            data[key] = {"price": "?", "change_pct": "?"}
        await asyncio.sleep(0.3)

    # 1b. S&P 500 200일 이동평균 (judge_regime v6 기준)
    try:
        sp_hist = _yf_history("^GSPC", "1y")
        if sp_hist and len(sp_hist) >= 200:
            ma200 = sum(sp_hist[-200:]) / 200.0
            data.setdefault("SP500", {})["ma200"] = round(ma200, 2)
            # 현재가가 비어 있으면 히스토리 마지막 값으로 보강
            if data["SP500"].get("price") in (None, "?", 0):
                data["SP500"]["price"] = round(sp_hist[-1], 2)
        else:
            data.setdefault("SP500", {})["ma200"] = "?"
    except Exception:
        data.setdefault("SP500", {})["ma200"] = "?"

    # 2. KOSPI
    try:
        q = await get_yahoo_quote("^KS11")
        data["KOSPI"] = {
            "price":      round(float(q.get("price", 0)), 2),
            "change_pct": round(float(q.get("change_pct", 0)), 2),
        }
    except Exception:
        data["KOSPI"] = {"price": "?", "change_pct": "?"}

    # 3. USD/KRW
    try:
        q = await get_yahoo_quote("KRW=X")
        krw = float(q.get("price", 0) or 0)
        data["USDKRW"] = {
            "price":      f"{krw:.1f}" if krw else "?",
            "change_pct": round(float(q.get("change_pct", 0)), 2),
        }
    except Exception:
        data["USDKRW"] = {"price": "?", "change_pct": "?"}

    # 4. 시장별 투자자매매동향 (KOSPI만, FHPTJ04040000)
    # KOSDAQ은 API 응답 전부 0 → 공식 문의 필요, 당분간 KOSPI만
    try:
        token = await get_kis_token()
        if token:
            kospi_flow = await _fetch_market_investor_flow(token, "KSP")
            data["MARKET_FLOW"] = {"kospi": kospi_flow}
            # judge_regime 호환: KOSPI 외인 순매수금(백만원 → 억원)
            data["FOREIGN_FLOW"] = {"amount_억": kospi_flow["frgn"] // 100}
        else:
            data["MARKET_FLOW"]  = {}
            data["FOREIGN_FLOW"] = {"amount_억": "?"}
    except Exception:
        data["MARKET_FLOW"]  = {}
        data["FOREIGN_FLOW"] = {"amount_억": "?"}

    # 5. 이벤트 캘린더 (날짜 미래 항목만 포함)
    events = load_events()
    now = datetime.now(KST)
    upcoming = {}
    for key, val in events.items():
        try:
            evt = datetime.strptime(val, "%Y-%m-%d")
            if evt.date() >= now.date():
                upcoming[key] = val
        except Exception:
            upcoming[key] = val   # "진행중" 같은 비날짜 값도 포함
    data["EVENTS"] = upcoming

    # 6. 시간외 급등락 (SQLite daily_snapshot, pm 슬롯용)
    try:
        from db_collector import _get_db
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        conn = _get_db()
        rows = conn.execute("""
            SELECT s.symbol, m.name_kr, s.ovtm_change_pct
            FROM daily_snapshot s
            LEFT JOIN stock_master m ON m.symbol = s.symbol
            WHERE s.trade_date = ?
              AND s.ovtm_change_pct IS NOT NULL
              AND s.ovtm_change_pct != 0
            ORDER BY s.ovtm_change_pct DESC
        """, (today_str,)).fetchall()
        conn.close()
        top    = [{"name": r["name_kr"] or r["symbol"], "pct": r["ovtm_change_pct"]}
                  for r in rows[:3]]
        bottom = [{"name": r["name_kr"] or r["symbol"], "pct": r["ovtm_change_pct"]}
                  for r in rows[-3:] if r["ovtm_change_pct"] < 0]
        data["OVERTIME_MOVERS"] = {"top": top, "bottom": bottom}
    except Exception:
        data["OVERTIME_MOVERS"] = {"top": [], "bottom": []}

    return data


def format_macro_msg(data: dict) -> str:
    """매크로 데이터 → 텔레그램 메시지 포맷"""
    def _p(d, prefix="", suffix=""):
        v = d.get("price", "?")
        return f"{prefix}{v}{suffix}" if v != "?" else "?"

    def _c(d):
        c = d.get("change_pct", "?")
        if c == "?":
            return "?"
        try:
            return f"{float(c):+.2f}%"
        except Exception:
            return str(c)

    now = datetime.now(KST)
    msg = f"📊 *매크로 대시보드* ({now.strftime('%m/%d %H:%M')} KST)\n\n"

    # [시장심리]
    vix   = data.get("VIX",   {})
    kospi = data.get("KOSPI", {})
    sp500 = data.get("SP500", {})
    msg += "[시장심리]\n"
    msg += f"VIX: {_p(vix)} ({_c(vix)}) | KOSPI: {_p(kospi)} ({_c(kospi)})\n"
    # S&P 500 + 200MA (레짐 판정 기준)
    sp_p = sp500.get("price", "?")
    sp_ma = sp500.get("ma200", "?")
    if sp_p != "?" and sp_ma != "?":
        try:
            diff_pct = (float(sp_p) / float(sp_ma) - 1) * 100
            msg += f"S&P500: {sp_p:,} (200MA {sp_ma:,}, {diff_pct:+.1f}%)\n"
        except Exception:
            msg += f"S&P500: {_p(sp500)} ({_c(sp500)}) | 200MA: {sp_ma}\n"
    else:
        msg += f"S&P500: {_p(sp500)} ({_c(sp500)})\n"
    msg += "\n"

    # [가격지표]
    wti    = data.get("WTI",    {})
    gold   = data.get("GOLD",   {})
    copper = data.get("COPPER", {})
    dxy    = data.get("DXY",    {})
    usdkrw = data.get("USDKRW",{})
    us10y  = data.get("US10Y",  {})
    msg += "[가격지표]\n"
    msg += f"WTI: ${_p(wti)} ({_c(wti)}) | 금: ${_p(gold)} ({_c(gold)})\n"
    msg += f"구리: ${_p(copper)} ({_c(copper)}) | DXY: {_p(dxy)} ({_c(dxy)})\n"
    # 환율 변동률 ±0.5% 이상 시 경고 이모지
    _fx_chg = usdkrw.get("change_pct", "?")
    _fx_warn = ""
    try:
        _fx_val = float(_fx_chg)
        if _fx_val >= 0.5:
            _fx_warn = " ⚠️📈"
        elif _fx_val <= -0.5:
            _fx_warn = " ⚠️📉"
    except (TypeError, ValueError):
        pass
    msg += f"USD/KRW: {_p(usdkrw)} ({_c(usdkrw)}){_fx_warn} | US10Y: {_p(us10y)}% ({_c(us10y)})\n\n"

    # [수급]
    def _flow_str(flow_dict: dict, label: str) -> str:
        """시장별 투자자 흐름 → "외인 +1,064억 | 기관 -203억 | 개인 -1,228억" """
        frgn = flow_dict.get("frgn", 0)
        orgn = flow_dict.get("orgn", 0)
        prsn = flow_dict.get("prsn", 0)
        frgn_억 = frgn // 100
        orgn_억 = orgn // 100
        prsn_억 = prsn // 100
        return (f"{label}: 외인 {frgn_억:+,}억 | "
                f"기관 {orgn_억:+,}억 | 개인 {prsn_억:+,}억")

    mf = data.get("MARKET_FLOW", {})
    msg += "[수급]\n"
    if mf.get("kospi"):
        msg += _flow_str(mf["kospi"], "KOSPI") + "\n"
    if not mf:
        # fallback: FOREIGN_FLOW만 있을 때
        ff  = data.get("FOREIGN_FLOW", {})
        amt = ff.get("amount_억", "?")
        if isinstance(amt, (int, float)):
            msg += f"외인 KOSPI: {amt:+,}억\n"
        else:
            msg += f"외인 KOSPI: {amt}\n"
    msg += "\n"

    # [이벤트]
    events = data.get("EVENTS", {})
    if events:
        msg += "[이벤트]\n"
        for k, v in list(events.items())[:5]:
            msg += f"{k}: {v}\n"
        msg += "\n"

    regime = judge_regime(data)
    msg += f"→ 자동판정: {regime['regime']} {regime['label']} ({', '.join(regime['reasons'])})"
    return msg


def judge_regime(data: dict) -> dict:
    """매크로 데이터 기반 레짐 자동 판정 v6 (2026-04-23 개정, INVESTMENT_RULES v6)

    3단계 판정:
    - 🟢 공격: S&P 500 > 200MA (3% 버퍼) AND VIX < 20
    - 🔴 위기: S&P 500 < 200MA (-3% 버퍼) AND VIX > 30
    - 🟡 경계: 그 외 (둘 중 하나 이탈)

    USD/KRW / WTI / KOSPI 낙폭 / 외인 순매수는 판정에서 제외.
    (USD/KRW는 한국 종목 사이징 참고용으로만 사용)
    """
    def _sf(d, key="price"):
        v = d.get(key, "?")
        if v == "?" or v is None:
            return None
        try:
            return float(str(v).replace(",", ""))
        except (ValueError, TypeError):
            return None

    vix         = _sf(data.get("VIX",   {}))
    sp500_price = _sf(data.get("SP500", {}), "price")
    sp500_ma200 = _sf(data.get("SP500", {}), "ma200")

    reasons = []

    # S&P 500 200MA (3% 버퍼) 판정
    sp_above_ma = None  # True=위, False=아래, "neutral"=버퍼존
    if sp500_price is not None and sp500_ma200 is not None and sp500_ma200 > 0:
        buffer = sp500_ma200 * 0.03
        if sp500_price > sp500_ma200 + buffer:
            sp_above_ma = True
            reasons.append(f"S&P {sp500_price:,.0f} > 200MA {sp500_ma200:,.0f}+3%")
        elif sp500_price < sp500_ma200 - buffer:
            sp_above_ma = False
            reasons.append(f"S&P {sp500_price:,.0f} < 200MA {sp500_ma200:,.0f}-3%")
        else:
            sp_above_ma = "neutral"
            reasons.append(f"S&P 200MA 버퍼존 ({sp500_price:,.0f}/{sp500_ma200:,.0f})")
    else:
        reasons.append("S&P/200MA 데이터 없음")

    # VIX 판정
    vix_zone = None  # "low"=<20, "mid"=20~30, "high"=>30
    if vix is not None:
        reasons.append(f"VIX {vix:.2f}")
        if vix < 20:
            vix_zone = "low"
        elif vix > 30:
            vix_zone = "high"
        else:
            vix_zone = "mid"
    else:
        reasons.append("VIX 데이터 없음")

    # 종합 판정
    if sp_above_ma is True and vix_zone == "low":
        return {"regime": "🟢", "label": "공격", "reasons": reasons}
    if sp_above_ma is False and vix_zone == "high":
        return {"regime": "🔴", "label": "위기", "reasons": reasons}
    return {"regime": "🟡", "label": "경계", "reasons": reasons}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART API - 공시 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━
