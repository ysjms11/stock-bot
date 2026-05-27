"""종목 유니버스 조회 (DB 기반 + KIS API fallback)."""
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


def get_stock_universe() -> dict:
    """stock_universe.json에서 종목 유니버스 로드. {ticker: name} 반환.
    /data/stock_universe.json 없으면 kis_api.py 위치 기준 절대경로로 시도.
    """
    _repo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_universe.json")
    for path in [UNIVERSE_FILE, _repo_path, "stock_universe.json"]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                codes = data.get("codes", {})
                if codes:
                    print(f"[universe] {len(codes)}종목 로드 ({path})")
                    return codes
        except Exception:
            pass
    print("[universe] stock_universe.json 로드 실패 — 빈 유니버스 반환")
    return {}


async def fetch_universe_from_krx(token: str) -> dict:
    """시가총액 기준 유니버스 조회.

    1차: stock.db daily_snapshot 최신 날짜 기준 시총 상위 (KOSPI 250 + KOSDAQ 350)
    2차 fallback: FHPST01740000 API (응답 상한 30건/시장 — 주말·DB 미수집 대비)

    Returns: {ticker: name}
    """
    import sqlite3 as _sqlite3

    # ── 1차: DB 기반 시총 상위 ─────────────────────────────────────────
    try:
        conn = _sqlite3.connect(_DB_PATH, timeout=10)
        conn.execute("PRAGMA cache_size = -65536")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA mmap_size = 268435456")
        conn.execute("PRAGMA busy_timeout = 30000")
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM daily_snapshot")
        row = cur.fetchone()
        latest_date = row[0] if row else None
        if latest_date:
            result: dict = {}
            for market, limit in (("kospi", 250), ("kosdaq", 350)):
                cur.execute(
                    """SELECT sm.symbol, sm.name
                       FROM stock_master sm
                       JOIN daily_snapshot ds ON ds.symbol = sm.symbol
                                              AND ds.trade_date = ?
                       WHERE sm.market = ? AND ds.market_cap > 0
                       ORDER BY ds.market_cap DESC
                       LIMIT ?""",
                    (latest_date, market, limit),
                )
                rows = cur.fetchall()
                for sym, name in rows:
                    result[sym] = name
            conn.close()
            print(f"[fetch_universe] DB({latest_date}) 합계={len(result)}")
            if len(result) >= 100:
                return result
        conn.close()
    except Exception as e:
        print(f"[fetch_universe] DB 조회 실패: {e} — KIS API fallback")

    # ── 2차 fallback: KIS API (FHPST01740000, 최대 30건/시장) ──────────
    # 주의: 이 API는 페이지네이션 없이 30건이 상한임 (tr_cont 항상 '' 반환)
    BASE_PATH = "/uapi/domestic-stock/v1/ranking/market-cap"
    TR_ID     = "FHPST01740000"
    fallback: dict = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        for iscd, label in (("0001", "KOSPI"), ("1001", "KOSDAQ")):
            hdrs = {**_kis_headers(token, TR_ID), "tr_cont": ""}
            params = {
                "fid_input_price_2":      "",
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code":  "20174",
                "fid_div_cls_code":       "1",
                "fid_input_iscd":         iscd,
                "fid_trgt_cls_code":      "0",
                "fid_trgt_exls_cls_code": "0",
                "fid_input_price_1":      "",
                "fid_vol_cnt":            "",
            }
            try:
                async with s.get(f"{KIS_BASE_URL}{BASE_PATH}",
                                 headers=hdrs, params=params) as r:
                    data = await r.json(content_type=None)
            except Exception as e:
                print(f"[fetch_universe] fallback {label} 오류: {e}")
                continue
            for item in data.get("output", []):
                ticker = (item.get("mksc_shrn_iscd") or "").strip()
                name   = (item.get("hts_kor_isnm")   or "").strip()
                if ticker and name:
                    fallback[ticker] = name
            await asyncio.sleep(0.3)
    print(f"[fetch_universe] fallback(KIS API) 합계={len(fallback)}")
    return fallback


async def kis_daily_closes(ticker: str, token: str, n: int = 65) -> list:
    """최근 n거래일 종가 리스트 반환 (최신이 [0])
    FHKST03010100 일봉 API 사용. 8초 timeout으로 hang 방지.
    """
    today_str = datetime.now(KST).strftime("%Y%m%d")
    start_dt = (datetime.now(KST) - timedelta(days=n * 2)).strftime("%Y%m%d")
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100", token,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
             "FID_INPUT_DATE_1": start_dt, "FID_INPUT_DATE_2": today_str,
             "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
    candles = d.get("output2") or []
    return [int(c.get("stck_clpr", 0) or 0) for c in candles[:n]]


