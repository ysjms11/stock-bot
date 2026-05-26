"""KIS 해외주식 + Yahoo Finance + 볼륨 프로파일."""
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


async def kis_us_stock_price(symbol: str, token: str, excd: str = "") -> dict:
    """KIS API 해외주식 현재가 (HHDFS00000300). 거래소 코드 자동 fallback."""
    if not excd:
        excd = _guess_excd(symbol)
    # 1차 시도
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price",
        "HHDFS00000300", token,
        {"AUTH": "", "EXCD": excd, "SYMB": symbol})
    out = d.get("output", {})
    price = float(out.get("last", 0) or 0)
    if price > 0:
        return out
    # 2차: 다른 거래소로 fallback
    fallback_codes = [c for c in ("NYS", "NAS", "AMS") if c != excd]
    for fb in fallback_codes:
        await asyncio.sleep(0.2)
        _, d2 = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price",
            "HHDFS00000300", token,
            {"AUTH": "", "EXCD": fb, "SYMB": symbol})
        out2 = d2.get("output", {})
        p2 = float(out2.get("last", 0) or 0)
        if p2 > 0:
            print(f"[excd fallback] {symbol}: {excd}→{fb} 성공")
            return out2
    return out  # 모든 거래소에서 0이면 원래 결과 반환


async def kis_us_stock_detail(symbol: str, token: str, excd: str = "") -> dict:
    """KIS API 해외주식 현재가상세 (HHDFS76200200) — PER/PBR/시총/52주 등"""
    if not excd:
        excd = _guess_excd(symbol)
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price-detail",
        "HHDFS76200200", token,
        {"AUTH": "", "EXCD": excd, "SYMB": symbol})
    out = d.get("output", {})
    p = float(out.get("last", 0) or out.get("t_xprc", 0) or 0)
    if p > 0:
        return out
    fallback_codes = [c for c in ("NYS", "NAS", "AMS") if c != excd]
    for fb in fallback_codes:
        await asyncio.sleep(0.2)
        _, d2 = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price-detail",
            "HHDFS76200200", token,
            {"AUTH": "", "EXCD": fb, "SYMB": symbol})
        out2 = d2.get("output", {})
        p2 = float(out2.get("last", 0) or out2.get("t_xprc", 0) or 0)
        if p2 > 0:
            print(f"[excd fallback detail] {symbol}: {excd}→{fb} 성공")
            return out2
    return out  # 모든 거래소에서 0이면 원래 결과 반환


async def kis_fluctuation_rank(token: str, market: str = "0000",
                              sort: str = "rise", n: int = 20) -> list:
    """등락률 순위 조회 (FHPST01700000).

    market: "0000"=전체, "0001"=KOSPI, "1001"=KOSDAQ
    sort: "rise"=상승률 상위, "fall"=하락률 상위
    Returns: [{ticker, name, price, chg_pct, volume}, ...]
    """
    # 등락 필터: rise=양수 구간, fall=음수 구간
    rate1, rate2 = ("0", "") if sort == "rise" else ("", "0")
    hdrs = _kis_headers(token, "FHPST01700000")
    params = {
        "fid_rsfl_rate2":         rate2,
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code":  "20170",
        "fid_input_iscd":         market,
        "fid_rank_sort_cls_code": "0000",
        "fid_input_cnt_1":        str(min(n, 30)),
        "fid_prc_cls_code":       "0",
        "fid_input_price_1":      "1000",
        "fid_input_price_2":      "",
        "fid_vol_cnt":            "",
        "fid_trgt_cls_code":      "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code":       "0",
        "fid_rsfl_rate1":         rate1,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/ranking/fluctuation",
                             headers=hdrs, params=params) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        print(f"[kis_fluctuation_rank] 오류: {e}")
        return []

    result = []
    for item in data.get("output", [])[:n]:
        ticker = (item.get("mksc_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "ticker":  ticker,
            "name":    (item.get("hts_kor_isnm") or "").strip(),
            "price":   int(item.get("stck_prpr", 0) or 0),
            "chg_pct": float(item.get("prdy_ctrt", 0) or 0),
            "volume":  int(item.get("acml_vol", 0) or 0),
        })
    # fall 모드: 하락률 큰 순(음수 방향) 정렬
    if sort == "fall":
        result.sort(key=lambda x: x["chg_pct"])
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Yahoo Finance
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def get_yahoo_quote(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
        session = _get_session()
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status == 200:
                meta = (await resp.json()).get("chart", {}).get("result", [{}])[0].get("meta", {})
                price = meta.get("regularMarketPrice", 0)
                prev = meta.get("chartPreviousClose", 0)
                return {"price": price, "prev": prev, "change_pct": ((price - prev) / prev * 100) if prev else 0}
    except Exception:
        pass
    return {"price": 0, "prev": 0, "change_pct": 0}


