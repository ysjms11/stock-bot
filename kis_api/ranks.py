"""순위 관련 API — 시간외/거래원/증권사/배당."""
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


async def kis_overtime_fluctuation(token: str, sort: str = "rise",
                                    market: str = "0000", n: int = 20) -> list:
    """시간외 등락률 순위 (FHPST02340000).

    sort: "rise"=상승률 상위, "fall"=하락률 상위
    market: 0000=전체, 0001=코스피, 1001=코스닥
    """
    div_code = "2" if sort == "rise" else "5"  # 2=상승률, 5=하락률
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ranking/overtime-fluctuation",
                          "FHPST02340000", token, {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_MRKT_CLS_CODE": "",
        "FID_COND_SCR_DIV_CODE": "20234",
        "FID_INPUT_ISCD": market,
        "FID_DIV_CLS_CODE": div_code,
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_TRGT_CLS_CODE": "",
        "FID_TRGT_EXLS_CLS_CODE": "",
    })
    items = d.get("output2", d.get("output", []))
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        ticker = (item.get("mksc_shrn_iscd") or item.get("stck_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "rank": int(item.get("data_rank", 0) or 0),
            "ticker": ticker,
            "name": (item.get("hts_kor_isnm") or "").strip(),
            "overtime_price": int(item.get("ovtm_untp_prpr", 0) or item.get("stck_prpr", 0) or 0),
            "chg_pct": float(item.get("ovtm_untp_prdy_ctrt", 0) or item.get("prdy_ctrt", 0) or 0),
            "volume": int(item.get("ovtm_untp_vol", 0) or item.get("acml_vol", 0) or 0),
            "close": int(item.get("stck_prpr", 0) or 0),
        })
    return result


async def kis_traded_by_company(token: str, broker: str = "", sort: str = "buy",
                                 market: str = "0000", n: int = 20) -> list:
    """증권사별 매매종목 순위 (FHPST01860000).

    broker: 증권사코드 (빈 문자열이면 자사)
    sort: "buy"=매수상위, "sell"=매도상위
    market: 0000=전체, 0001=거래소, 1001=코스닥
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    sort_code = "1" if sort == "buy" else "0"
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ranking/traded-by-company",
                          "FHPST01860000", token, {
        "fid_trgt_exls_cls_code": "0",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20186",
        "fid_div_cls_code": "0",
        "fid_rank_sort_cls_code": sort_code,
        "fid_input_date_1": today,
        "fid_input_date_2": today,
        "fid_input_iscd": broker if broker else market,
        "fid_trgt_cls_code": "0",
        "fid_aply_rang_vol": "0",
        "fid_aply_rang_prc_1": "",
        "fid_aply_rang_prc_2": "",
    })
    items = d.get("output", [])
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        ticker = (item.get("stck_shrn_iscd") or item.get("mksc_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "rank": int(item.get("data_rank", 0) or 0),
            "ticker": ticker,
            "name": (item.get("hts_kor_isnm") or "").strip(),
            "price": int(item.get("stck_prpr", 0) or 0),
            "chg_pct": float(item.get("prdy_ctrt", 0) or 0),
            "trade_amt": int(item.get("trad_pbmn", 0) or item.get("acml_tr_pbmn", 0) or 0),
            "trade_vol": int(item.get("trad_vol", 0) or item.get("acml_vol", 0) or 0),
            "broker_name": (item.get("mbcr_nm") or "").strip(),
        })
    return result


async def kis_dividend_rate_rank(token: str, market: str = "0",
                                  n: int = 30) -> list:
    """배당수익률 순위 (HHKDB13470100).

    market: 0=전체, 1=코스피, 3=코스닥
    """
    today = datetime.now(KST)
    year = str(today.year - 1)
    f_dt = f"{year}0101"
    t_dt = f"{year}1231"
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ranking/dividend-rate",
                          "HHKDB13470100", token, {
        "CTS_AREA": " ",
        "GB1": market,
        "UPJONG": "",
        "GB2": "0",
        "GB3": "2",
        "F_DT": f_dt,
        "T_DT": t_dt,
        "GB4": "0",
    })
    items = d.get("output", [])
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        ticker = (item.get("stck_shrn_iscd") or item.get("rank_iscd") or "").strip()
        if not ticker or len(ticker) != 6:
            continue
        result.append({
            "rank": int(item.get("data_rank", 0) or len(result) + 1),
            "ticker": ticker,
            "name": (item.get("hts_kor_isnm") or item.get("rank_isnm") or "").strip(),
            "price": int(item.get("stck_prpr", 0) or 0),
            "dividend": int(item.get("per_sto_divi_amt", 0) or item.get("dvdn_amt", 0) or 0),
            "dividend_yield": float(item.get("divi_rate", 0) or item.get("dvdn_rate", 0) or 0),
            "per": float(item.get("per", 0) or 0),
            "market_cap": int(item.get("lstg_stcn", 0) or 0),
        })
    return result


async def kis_us_updown_rate(token: str, sort: str = "rise",
                             exchange: str = "NAS", n: int = 20) -> list:
    """해외주식 등락률 상위/하위 종목 순위 (HHDFS76290000).

    sort: "rise"=상승률 상위, "fall"=하락률 상위
    exchange: "NYS", "NAS", "AMS"
    Returns: [{ticker, name, price, chg_pct, volume}, ...]
    """
    gubn = "1" if sort == "rise" else "0"
    try:
        s = _get_session()
        _, d = await _kis_get(s,
            "/uapi/overseas-stock/v1/ranking/updown-rate",
            "HHDFS76290000", token,
            {
                "AUTH":     "",
                "EXCD":     exchange.upper(),
                "NDAY":     "0",
                "GUBN":     gubn,
                "VOL_RANG": "0",
                "KEYB":     "",
            })
        result = []
        for item in d.get("output2", [])[:n]:
            symb = (item.get("symb") or "").strip()
            if not symb:
                continue
            result.append({
                "ticker":  symb,
                "name":    (item.get("name") or item.get("ename") or "").strip(),
                "price":   float(item.get("last", 0) or 0),
                "chg_pct": float(item.get("rate", 0) or 0),
                "volume":  int(item.get("tvol",  0) or 0),
            })
        if sort == "fall":
            result.sort(key=lambda x: x["chg_pct"])
        return result
    except Exception as e:
        print(f"[kis_us_updown_rate] 오류: {e}")
        return []


async def kis_estimate_perform(ticker: str, token: str) -> dict:
    """국내주식 종목추정실적 (HHKST668300C0)
    output2: 연간 추정실적 / output3: 분기 추정실적
    필드: dt(결산년월) data1(매출액) data2(영업이익) data3(세전이익) data4(순이익) data5(EPS)
    """
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/estimate-perform",
        "HHKST668300C0", token, {"SHT_CD": ticker})

    def _row(r):
        return {
            "dt":  r.get("dt", ""),
            "rev": r.get("data1", ""),
            "op":  r.get("data2", ""),
            "ebt": r.get("data3", ""),
            "np":  r.get("data4", ""),
            "eps": r.get("data5", ""),
        }

    annual = d.get("output2") or []
    qtly   = d.get("output3") or []
    return {
        "annual":    [_row(r) for r in (annual if isinstance(annual, list) else [annual])],
        "quarterly": [_row(r) for r in (qtly   if isinstance(qtly,   list) else [qtly])],
    }


async def kis_dividend_schedule(token: str, from_dt: str = "", to_dt: str = "",
                                ticker: str = "", gb1: str = "0") -> list:
    """예탁원정보 배당일정 (HHKDB669102C0)
    gb1: 0=전체, 1=결산배당, 2=중간배당
    반환: [{sht_cd, record_date, per_sto_divi_amt, divi_rate, divi_pay_dt, ...}, ...]
    """
    if not from_dt:
        from_dt = datetime.now(KST).strftime("%Y%m%d")
    if not to_dt:
        to_dt = (datetime.now(KST) + timedelta(days=90)).strftime("%Y%m%d")
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ksdinfo/dividend",
        "HHKDB669102C0", token,
        {"CTS": " ", "GB1": gb1, "F_DT": from_dt, "T_DT": to_dt,
         "SHT_CD": ticker or " ", "HIGH_GB": " "})
    return d.get("output1") or d.get("output") or []


