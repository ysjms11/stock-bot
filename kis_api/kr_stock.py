"""KIS 국내주식 API 함수 (31 TR_ID)."""
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


async def get_investor_trend(ticker, token):
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHKST01010900"
    }
    session = _get_session()
    async with session.get(url, headers=headers, params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}) as resp:
        return (await resp.json()).get("output", [])


async def get_volume_rank(token):
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHPST01710000"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20101",
        "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "0", "FID_INPUT_PRICE_2": "0",
        "FID_VOL_CNT": "0", "FID_INPUT_DATE_1": ""
    }
    session = _get_session()
    async with session.get(url, headers=headers, params=params) as resp:
        return (await resp.json()).get("output", [])


async def get_kis_index(token, index_code="0001"):
    """KIS API로 KOSPI/KOSDAQ 지수 조회 (0001=KOSPI, 1001=KOSDAQ)"""
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHPUP02100000"
    }
    params = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": index_code}
    session = _get_session()
    async with session.get(url, headers=headers, params=params) as resp:
        return (await resp.json()).get("output", {})


def _kis_headers(token, tr_id):
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
    }


async def _kis_get(session, path, tr_id, token, params):
    """KIS API GET 호출 (429/5xx 자동 재시도, 공유 세션 fallback)."""
    s = session if session and not getattr(session, 'closed', False) else _get_session()
    url = f"{KIS_BASE_URL}{path}"
    headers = _kis_headers(token, tr_id)
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        async with s.get(url, headers=headers, params=params) as r:
            if r.status == 429 and attempt < max_retries:
                print(f"[RETRY] {path} → 429, attempt {attempt}/{max_retries}")
                await asyncio.sleep(1.0 * attempt)
                continue
            if r.status in (500, 502, 503) and attempt < max_retries:
                print(f"[RETRY] {path} → {r.status}, attempt {attempt}/{max_retries}")
                await asyncio.sleep(2.0)
                continue
            data = await r.json(content_type=None)
            return r.status, data
    return 500, {}


async def kis_stock_price(ticker, token, session=None):
    s = session or aiohttp.ClientSession()
    try:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        return d.get("output", {})
    finally:
        if session is None:
            await s.close()


async def kis_stock_info(ticker, token):
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/search-stock-info",
        "CTPF1002R", token,
        {"PRDT_TYPE_CD": "300", "PDNO": ticker})
    return d.get("output", {})


async def kis_investor_trend(ticker, token):
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-investor",
        "FHKST01010900", token,
        {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
    return d.get("output", [])


async def kis_credit_balance(ticker, token):
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-credit-by-company",
        "FHKST01010600", token,
        {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
    return d.get("output", {})


async def kis_short_selling(ticker, token):
    today = datetime.now().strftime("%Y%m%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-short-selling",
        "FHKST01010700", token,
        {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker,
         "fid_begin_dt": week_ago, "fid_end_dt": today})
    return d.get("output", [])


async def kis_volume_rank_api(token):
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/volume-rank",
        "FHPST01710000", token,
        {"fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20171",
         "fid_input_iscd": "0000", "fid_div_cls_code": "0", "fid_blng_cls_code": "0",
         "fid_trgt_cls_code": "111111111", "fid_trgt_exls_cls_code": "000000",
         "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "", "fid_input_date_1": ""})
    return d.get("output", [])


async def kis_foreigner_trend(token):
    today = datetime.now().strftime("%Y%m%d")
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-foreigner-trend",
        "FHPTJ04060100", token,
        {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": "0000", "fid_input_date_1": today})
    if not d:
        return []
    output = d.get("output") or []
    return [r for r in output if r is not None]


async def kis_sector_price(token):
    today = datetime.now().strftime("%Y%m%d")
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-daily-sector-price",
        "FHKUP03500100", token,
        {"fid_cond_mrkt_div_code": "U", "fid_input_iscd": "0001",
         "fid_input_date_1": today, "fid_period_div_code": "D"})
    return d.get("output", [])


WI26_SECTORS = [
    ("001", "반도체"), ("004", "조선"),   ("006", "전력기기"),
    ("007", "방산"),   ("010", "2차전지"), ("012", "건설"),
    ("021", "바이오"),
]

# 외국인 순매수 상위 fallback용 티커→업종 매핑
_TICKER_SECTOR = {
    "005930": "반도체", "000660": "반도체", "012510": "반도체", "042700": "반도체",
    "009540": "조선",   "042660": "조선",   "010140": "조선",   "267250": "조선",
    "012510": "전력기기","028260": "전력기기","267260": "전력기기","298040": "전력기기",
    "012450": "방산",   "047810": "방산",   "329180": "방산",   "272210": "방산",
    "006400": "2차전지","051910": "2차전지","373220": "2차전지","247540": "2차전지",
    "000720": "건설",   "097950": "건설",   "047040": "건설",   "028260": "건설",
    "207940": "바이오", "068270": "바이오", "196170": "바이오", "091990": "바이오",
}


async def _fetch_market_investor_flow(token: str, market: str) -> dict:
    """시장별 투자자매매동향(일별) FHPTJ04040000.
    market: "KSP"(코스피) or "KSQ"(코스닥)
    Returns: {"frgn": 백만원, "orgn": 백만원, "prsn": 백만원}
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    params = {
        "fid_cond_mrkt_div_code": "U",
        "fid_input_iscd": "0001",
        "fid_input_date_1": today,
        "fid_input_iscd_1": market,
        "fid_input_date_2": today,
        "fid_input_iscd_2": "0001",
    }
    try:
        s = _get_session()
        _, d = await _kis_get(
            s,
            "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market",
            "FHPTJ04040000",
            token,
            params,
        )
        if not d or d.get("rt_cd") != "0":
            return {"frgn": 0, "orgn": 0, "prsn": 0}
        rows = d.get("output") or []
        if isinstance(rows, list) and rows:
            row = rows[0]
        elif isinstance(rows, dict):
            row = rows
        else:
            return {"frgn": 0, "orgn": 0, "prsn": 0}
        frgn = int(row.get("frgn_ntby_tr_pbmn", 0) or 0)
        orgn = int(row.get("orgn_ntby_tr_pbmn", 0) or 0)
        prsn = int(row.get("prsn_ntby_tr_pbmn", 0) or 0)
        return {"frgn": frgn, "orgn": orgn, "prsn": prsn}
    except Exception:
        return {"frgn": 0, "orgn": 0, "prsn": 0}


async def _fetch_sector_flow(token: str, sector_code: str) -> tuple:
    """업종 외국인+기관 순매수금액(백만원) 반환. 실패 시 (0, 0)."""
    today = datetime.now().strftime("%Y%m%d")
    params = {
        "fid_cond_mrkt_div_code": "U",
        "fid_input_iscd": sector_code,
        "fid_input_date_1": today,
        "fid_period_div_code": "D",
    }
    for path in [
        "/uapi/domestic-stock/v1/quotations/inquire-member-daily-by-group",
        "/uapi/domestic-stock/v1/quotations/inquire-daily-sector-price",
    ]:
        try:
            s = _get_session()
            _, d = await _kis_get(s, path, "FHKUP03500100", token, params)
            if not d or d.get("rt_cd") != "0":
                continue
            out = d.get("output2") or d.get("output") or {}
            if isinstance(out, list):
                out = out[0] if out else {}
            frgn = int(out.get("frgn_ntby_tr_pbmn", 0) or 0)
            orgn = int(out.get("orgn_ntby_tr_pbmn", 0) or 0)
            if frgn != 0 or orgn != 0:
                return frgn, orgn
        except Exception:
            continue
    return 0, 0


async def detect_sector_rotation(token: str) -> dict:
    """WI26 업종별 외인+기관 순매수 수집 → 전일 대비 자금 이동 감지.
    Returns: {sectors: [{name, frgn, orgn, total, prev_total, change}],
             rotations: ["반도체→전력기기", ...], date: str}
    """
    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 오늘 업종별 수급 수집
    today_data = {}
    for code, name in WI26_SECTORS:
        try:
            frgn, orgn = await _fetch_sector_flow(token, code)
            today_data[name] = {"frgn": frgn, "orgn": orgn, "total": frgn + orgn}
            await asyncio.sleep(0.3)
        except Exception:
            today_data[name] = {"frgn": 0, "orgn": 0, "total": 0}

    # 전일 데이터 로드
    prev = load_json(SECTOR_ROTATION_FILE, {})
    prev_data = prev.get("sectors", {})
    prev_date = prev.get("date", "")

    # 변화량 계산
    sectors = []
    for name, cur in today_data.items():
        prev_total = prev_data.get(name, {}).get("total", 0)
        change = cur["total"] - prev_total if prev_date and prev_date != today else 0
        sectors.append({
            "name": name,
            "frgn": cur["frgn"],
            "orgn": cur["orgn"],
            "total": cur["total"],
            "prev_total": prev_total,
            "change": change,
        })

    # 유입/유출 상위 감지 → 로테이션 패턴
    sectors.sort(key=lambda x: x["change"], reverse=True)
    inflow = [s for s in sectors if s["change"] > 0]
    outflow = [s for s in sectors if s["change"] < 0]

    rotations = []
    for out_s in outflow[:2]:
        for in_s in inflow[:2]:
            if abs(out_s["change"]) > 100 and abs(in_s["change"]) > 100:
                rotations.append(f"{out_s['name']}→{in_s['name']}")

    # 오늘 데이터 저장 (내일 비교용)
    save_json(SECTOR_ROTATION_FILE, {"date": today, "sectors": today_data})

    return {
        "date": today,
        "prev_date": prev_date,
        "sectors": sectors,
        "rotations": rotations,
        "top_inflow": inflow[:3] if inflow else [],
        "top_outflow": outflow[:3] if outflow else [],
    }


def _previous_trading_day(date_str: str) -> str:
    """YYYYMMDD → 직전 영업일 YYYYMMDD (주말만 건너뜀, 공휴일 미반영).
    공휴일엔 KIS API가 빈응답 반환하므로 호출자가 추가 fallback 처리 권장."""
    dt = datetime.strptime(date_str, "%Y%m%d") - timedelta(days=1)
    while dt.weekday() >= 5:  # 5=토, 6=일
        dt -= timedelta(days=1)
    return dt.strftime("%Y%m%d")


async def kis_investor_trend_history(ticker: str, token: str, n_days: int = 5, session=None) -> list:
    """종목별 투자자 일별 수급 히스토리 (FHPTJ04160001).

    Returns: [{date, foreign_net, institution_net, individual_net,
               foreign_buy, foreign_sell}, ...] 최신순, 최대 n_days일

    Fallback: KIS API가 today 지정 호출에 빈 응답을 주는 경우(장중 미확정, 공휴일 등)
    직전 영업일로 한 번 재시도한 뒤에도 비면 빈 리스트 반환.
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    s = session or aiohttp.ClientSession()

    async def _call(base_date: str):
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
            "FHPTJ04160001", token,
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         ticker,
                "FID_INPUT_DATE_1":       base_date,
                "FID_ORG_ADJ_PRC":        "",
                "FID_ETC_CLS_CODE":       "",
            })
        # output1=단일 현재가 dict, output2=일별 수급 list (최대 30일)
        return d.get("output2") if d else None

    try:
        rows = await _call(today)
        if not rows:  # 장중 빈 응답 → 직전 영업일로 1회 재시도
            rows = await _call(_previous_trading_day(today))
    finally:
        if session is None:
            await s.close()
    if not isinstance(rows, list):
        rows = []
    result = []
    for row in rows[:n_days]:
        result.append({
            "date":            row.get("stck_bsop_date", ""),
            "foreign_net":     int(row.get("frgn_ntby_qty",  0) or 0),
            "institution_net": int(row.get("orgn_ntby_qty",  0) or 0),
            "individual_net":  int(row.get("prsn_ntby_qty",  0) or 0),
            "foreign_buy":     int(row.get("frgn_shnu_vol",  0) or 0),
            "foreign_sell":    int(row.get("frgn_seln_vol",  0) or 0),
        })
    return result


async def save_supply_snapshot(token: str):
    """보유+감시 종목의 외인/기관 수급을 /data/supply_history.json에 일별 저장.
    구조: {ticker: [{date, foreign_net, institution_net}, ...]}
    3개월 후 수급 기반 백테스트 정밀화 가능."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    history = load_json(SUPPLY_HISTORY_FILE, {})

    portfolio = load_json(PORTFOLIO_FILE, {})
    wl = load_watchlist()
    tickers = {}
    for t, v in portfolio.items():
        if t not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict):
            tickers[t] = True
    for t in wl:
        tickers[t] = True

    for ticker_code in tickers:
        if _is_us_ticker(ticker_code):
            continue  # 국내만
        try:
            hist = await kis_investor_trend_history(ticker_code, token, n_days=1)
            if hist:
                entry = {"date": today, "foreign_net": hist[0]["foreign_net"],
                         "institution_net": hist[0]["institution_net"]}
                if ticker_code not in history:
                    history[ticker_code] = []
                # 중복 방지
                if not history[ticker_code] or history[ticker_code][-1].get("date") != today:
                    history[ticker_code].append(entry)
                    # 최대 180일 보관
                    history[ticker_code] = history[ticker_code][-180:]
            await asyncio.sleep(0.3)
        except Exception:
            pass

    save_json(SUPPLY_HISTORY_FILE, history)
    print(f"[supply_snapshot] {len(tickers)}종목 수급 저장 완료")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 장기 일봉 / 수급 데이터 (FDR · yfinance · KRX)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def get_historical_ohlcv(ticker: str, years: int = 3) -> list:
    """FinanceDataReader(한국) / yfinance(미국)로 장기 일봉 OHLCV 조회.
    Returns: [{"date": "YYYYMMDD", "open": ..., "high": ..., "low": ..., "close": ..., "vol": int}, ...]
    시간순(오래된→최신) 정렬. 동기 함수 — run_in_executor로 호출할 것.
    """
    end_dt = datetime.now(KST)
    start_dt = end_dt - timedelta(days=years * 365)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    is_us = _is_us_ticker(ticker)

    if is_us:
        try:
            import yfinance as yf
            df = yf.download(ticker, start=start_str, end=end_str, progress=False, auto_adjust=True)
            if df is None or df.empty:
                return []
            # yfinance >=1.2 returns MultiIndex columns for single ticker
            if isinstance(df.columns, __import__('pandas').MultiIndex):
                df.columns = df.columns.droplevel("Ticker")
            result = []
            for idx, row in df.iterrows():
                dt_str = idx.strftime("%Y%m%d")
                result.append({
                    "date": dt_str,
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "vol": int(row["Volume"]),
                })
            return result
        except Exception as e:
            print(f"[get_historical_ohlcv] yfinance 오류 ({ticker}): {e}")
            return []
    else:
        try:
            import FinanceDataReader as fdr
            df = fdr.DataReader(ticker, start_str, end_str)
            if df is None or df.empty:
                return []
            result = []
            for idx, row in df.iterrows():
                dt_str = idx.strftime("%Y%m%d")
                result.append({
                    "date": dt_str,
                    "open": int(row.get("Open", 0) or 0),
                    "high": int(row.get("High", 0) or 0),
                    "low": int(row.get("Low", 0) or 0),
                    "close": int(row.get("Close", 0) or 0),
                    "vol": int(row.get("Volume", 0) or 0),
                })
            return result
        except Exception as e:
            print(f"[get_historical_ohlcv] FDR 오류 ({ticker}): {e}")
            return []


def compute_volume_profile(candles: list, current_price: float, bins: int = 20) -> dict:
    """일봉 데이터에서 볼륨 프로파일(매물대) 계산.
    candles: get_historical_ohlcv() 반환값 [{"close":..., "vol":...}, ...]
    """
    if not candles:
        return {"error": "일봉 데이터 없음"}

    valid = [c for c in candles if c.get("close") and c.get("vol")]
    if not valid:
        return {"error": "종가 데이터 없음"}

    all_lows = [c.get("low", c["close"]) for c in valid]
    all_highs = [c.get("high", c["close"]) for c in valid]
    closes = [c["close"] for c in valid]
    volumes = [c["vol"] for c in valid]

    price_low = min(all_lows)
    price_high = max(all_highs)
    if price_high == price_low:
        price_high = price_low * 1.01 if price_low else 1  # avoid zero-division

    bin_size = (price_high - price_low) / bins
    total_volume = sum(volumes)

    # Build bins
    bin_list = []
    for i in range(bins):
        b_low = price_low + i * bin_size
        b_high = price_low + (i + 1) * bin_size
        b_mid = (b_low + b_high) / 2
        bin_list.append({
            "price_low": round(b_low, 2),
            "price_high": round(b_high, 2),
            "price_mid": round(b_mid, 2),
            "volume": 0,
        })

    # Assign volumes to bins (distribute across low~high range)
    for c in valid:
        c_low = c.get("low", c["close"])
        c_high = c.get("high", c["close"])
        vol = c["vol"]
        idx_lo = max(0, min(int((c_low - price_low) / bin_size), bins - 1))
        idx_hi = max(0, min(int((c_high - price_low) / bin_size), bins - 1))
        span = idx_hi - idx_lo + 1
        per_bin = vol / span
        for i in range(idx_lo, idx_hi + 1):
            bin_list[i]["volume"] += int(per_bin)

    # Calculate volume_pct and bar
    max_vol = max(b["volume"] for b in bin_list) or 1
    for b in bin_list:
        b["volume_pct"] = round(b["volume"] / total_volume * 100, 2) if total_volume else 0
        filled = int(round(b["volume"] / max_vol * 10))
        b["bar"] = "\u2588" * filled + "\u2591" * (10 - filled)

    # POC (Point of Control)
    poc_idx = max(range(bins), key=lambda i: bin_list[i]["volume"])
    poc = bin_list[poc_idx]["price_mid"]
    poc_volume_pct = bin_list[poc_idx]["volume_pct"]

    # Value Area (70% of total volume, expand from POC)
    va_volume = bin_list[poc_idx]["volume"]
    va_low_idx = poc_idx
    va_high_idx = poc_idx
    target = total_volume * 0.70

    while va_volume < target:
        expand_down = bin_list[va_low_idx - 1]["volume"] if va_low_idx > 0 else -1
        expand_up = bin_list[va_high_idx + 1]["volume"] if va_high_idx < bins - 1 else -1
        if expand_down < 0 and expand_up < 0:
            break
        if expand_down >= expand_up:
            va_low_idx -= 1
            va_volume += bin_list[va_low_idx]["volume"]
        else:
            va_high_idx += 1
            va_volume += bin_list[va_high_idx]["volume"]

    value_area_low = bin_list[va_low_idx]["price_low"]
    value_area_high = bin_list[va_high_idx]["price_high"]

    # Support / Resistance levels
    support_bins = [b for b in bin_list if b["price_mid"] < current_price]
    resistance_bins = [b for b in bin_list if b["price_mid"] > current_price]
    support_levels = sorted(support_bins, key=lambda b: b["volume"], reverse=True)[:3]
    resistance_levels = sorted(resistance_bins, key=lambda b: b["volume"], reverse=True)[:3]

    # Format for output
    is_decimal = any(isinstance(c["close"], float) and c["close"] != int(c["close"]) for c in valid[:5])
    def _fmt_level(b):
        if is_decimal:
            return {"price_range": f"{b['price_low']:.2f}~{b['price_high']:.2f}",
                    "price_mid": b["price_mid"], "volume_pct": b["volume_pct"]}
        return {"price_range": f"{b['price_low']:,.0f}~{b['price_high']:,.0f}",
                "price_mid": b["price_mid"], "volume_pct": b["volume_pct"]}

    support_out = [_fmt_level(b) for b in support_levels]
    resistance_out = [_fmt_level(b) for b in resistance_levels]

    # Interpretation
    cp = current_price
    _pf = ".2f" if is_decimal else ",.0f"
    poc_diff_pct = (cp - poc) / poc * 100 if poc else 0
    interp_parts = []
    if abs(poc_diff_pct) < 2:
        interp_parts.append(f"현재가가 POC({poc:{_pf}}) 부근 → 매물대 중심에서 거래 중")
    elif poc_diff_pct > 0:
        interp_parts.append(f"현재가가 POC({poc:{_pf}}) 위 {poc_diff_pct:.1f}% → 매물 소화 후 상승 구간")
    else:
        interp_parts.append(f"현재가가 POC({poc:{_pf}}) 아래 {abs(poc_diff_pct):.1f}% → 매물대 저항 가능")

    if value_area_low <= cp <= value_area_high:
        interp_parts.append(f"Value Area({value_area_low:{_pf}}~{value_area_high:{_pf}}) 내부 위치")
    elif cp > value_area_high:
        interp_parts.append(f"Value Area({value_area_low:{_pf}}~{value_area_high:{_pf}}) 상단 돌파 → 강세")
    else:
        interp_parts.append(f"Value Area({value_area_low:{_pf}}~{value_area_high:{_pf}}) 하단 이탈 → 약세 주의")

    if support_out:
        interp_parts.append(f"주요 지지대: {support_out[0]['price_range']}")
    if resistance_out:
        interp_parts.append(f"주요 저항대: {resistance_out[0]['price_range']}")

    return {
        "total_candles": len(candles),
        "total_volume": total_volume,
        "current_price": current_price,
        "price_range": {"low": round(price_low, 2), "high": round(price_high, 2)},
        "poc": round(poc, 2),
        "poc_volume_pct": poc_volume_pct,
        "value_area": {"low": round(value_area_low, 2), "high": round(value_area_high, 2)},
        "bins": bin_list,
        "support_levels": support_out,
        "resistance_levels": resistance_out,
        "interpretation": ". ".join(interp_parts),
    }


def get_historical_supply(ticker: str, days: int = 365) -> list:
    """KRX 크롤링으로 종목별 투자자 매매동향 (외인/기관) 조회.
    Returns: [{"date": "YYYYMMDD", "foreign_net": int, "institution_net": int}, ...]
    시간순 정렬. 국내 전용 — 미국 종목은 빈 리스트. 동기 함수.
    """
    if _is_us_ticker(ticker):
        return []

    import requests as _req
    end_dt = datetime.now(KST)
    start_dt = end_dt - timedelta(days=days)

    url = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
    }
    # KRX isuCd는 'A005930' 형식 (시장구분 접두사 + 6자리)
    isu_cd = f"A{ticker}" if len(ticker) == 6 and ticker.isdigit() else ticker
    payload = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT02303",
        "locale": "ko_KR",
        "isuCd": isu_cd,
        "isuCd2": isu_cd,
        "strtDd": start_dt.strftime("%Y%m%d"),
        "endDd": end_dt.strftime("%Y%m%d"),
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
    }

    try:
        resp = _req.post(url, data=payload, headers=headers, timeout=30)
        data = resp.json()
        rows = data.get("output", [])
        result = []
        for row in rows:
            dt = row.get("TRD_DD", "").replace("/", "").replace("-", "")
            if len(dt) != 8:
                continue
            frgn = int(str(row.get("FORN_PURE_QTY", row.get("foreignNetBuy", 0)) or 0).replace(",", "") or 0)
            inst = int(str(row.get("ORGN_PURE_QTY", row.get("organNetBuy", 0)) or 0).replace(",", "") or 0)
            result.append({
                "date": dt,
                "foreign_net": frgn,
                "institution_net": inst,
            })
        result.sort(key=lambda x: x["date"])
        return result
    except Exception as e:
        print(f"[get_historical_supply] KRX 크롤링 오류 ({ticker}): {e}")
        return []


async def kis_daily_volumes(ticker: str, token: str, n: int = 21) -> list:
    """최근 n거래일 거래량 리스트 반환 (최신이 [0]). FHKST03010100 일봉 API."""
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
    return [int(c.get("acml_vol", 0) or 0) for c in candles[:n]]


async def check_momentum_exit(ticker: str, token: str) -> dict:
    """모멘텀 종료 복합 신호 체크 (5개 조건, 2개 이상 해당 시 warning=True).

    Returns:
        {"ticker", "conditions": [{"condition", "triggered", "detail"}],
         "triggered": [...triggered conditions...], "count": int, "warning": bool}
    """
    conditions = []

    # ── 조건 1·2·5: 수급 히스토리 ──
    try:
        hist = await kis_investor_trend_history(ticker, token, n_days=5)
        await asyncio.sleep(0.3)

        frgn_vals = [h["foreign_net"] for h in hist]
        inst_vals  = [h["institution_net"] for h in hist]

        # 조건 1: 외국인 3일 연속 순매도
        f3 = frgn_vals[:3]
        frgn_consec = len(f3) == 3 and all(x < 0 for x in f3)
        frgn_detail = "/".join(f"{x:+,}" for x in frgn_vals[:5]) if frgn_vals else "-"
        conditions.append({"condition": "외인3일연속매도", "triggered": frgn_consec, "detail": frgn_detail})

        # 조건 2: 기관 3일 연속 순매도
        i3 = inst_vals[:3]
        inst_consec = len(i3) == 3 and all(x < 0 for x in i3)
        inst_detail = "/".join(f"{x:+,}" for x in inst_vals[:5]) if inst_vals else "-"
        conditions.append({"condition": "기관3일연속매도", "triggered": inst_consec, "detail": inst_detail})

        # 조건 5: 당일 외인+기관 동시 순매도
        if hist:
            t = hist[0]
            both = t["foreign_net"] < 0 and t["institution_net"] < 0
            conditions.append({"condition": "당일외인+기관동시매도", "triggered": both,
                                "detail": f"외인{t['foreign_net']:+,} 기관{t['institution_net']:+,}"})
        else:
            conditions.append({"condition": "당일외인+기관동시매도", "triggered": False, "detail": "데이터 없음"})
    except Exception as e:
        for cond in ["외인3일연속매도", "기관3일연속매도", "당일외인+기관동시매도"]:
            conditions.append({"condition": cond, "triggered": False, "detail": f"오류: {e}"})

    # ── 조건 3: 거래량 20일 평균 대비 50% 이하 ──
    try:
        vols = await kis_daily_volumes(ticker, token, n=21)
        await asyncio.sleep(0.3)
        if len(vols) >= 21:
            today_vol = vols[0]
            avg20 = sum(vols[1:21]) / 20
            ratio = today_vol / avg20 * 100 if avg20 > 0 else 100
            conditions.append({"condition": "거래량감소(20일평균50%이하)", "triggered": ratio <= 50,
                                "detail": f"오늘{today_vol:,} 20일평균{int(avg20):,} ({ratio:.0f}%)"})
        else:
            conditions.append({"condition": "거래량감소(20일평균50%이하)", "triggered": False, "detail": "데이터 부족"})
    except Exception as e:
        conditions.append({"condition": "거래량감소(20일평균50%이하)", "triggered": False, "detail": f"오류: {e}"})

    # ── 조건 4: 52주 고점 대비 -10% 이상 하락 ──
    try:
        p = await kis_stock_price(ticker, token)
        await asyncio.sleep(0.3)
        cur = int(p.get("stck_prpr", 0) or 0)
        h52 = int(p.get("w52_hgpr", 0) or 0)
        if cur > 0 and h52 > 0:
            drop = (cur - h52) / h52 * 100
            conditions.append({"condition": "52주고점대비-10%이상", "triggered": drop <= -10,
                                "detail": f"현재{cur:,} 52주고{h52:,} ({drop:.1f}%)"})
        else:
            conditions.append({"condition": "52주고점대비-10%이상", "triggered": False, "detail": "데이터 없음"})
    except Exception as e:
        conditions.append({"condition": "52주고점대비-10%이상", "triggered": False, "detail": f"오류: {e}"})

    # ── 조건 6: 추정수급 외인+기관 동시 순매도 ──
    try:
        est = await kis_investor_trend_estimate(ticker, token)
        f_est = est.get("foreign_est_net", 0)
        i_est = est.get("institution_est_net", 0)
        both_est = f_est < 0 and i_est < 0
        conditions.append({"condition": "추정수급외인+기관동시매도", "triggered": both_est,
                            "detail": f"외인{f_est:+,} 기관{i_est:+,} (추정)"})
    except Exception as e:
        conditions.append({"condition": "추정수급외인+기관동시매도", "triggered": False, "detail": f"오류: {e}"})

    triggered = [c for c in conditions if c["triggered"]]
    return {
        "ticker": ticker,
        "conditions": conditions,
        "triggered": triggered,
        "count": len(triggered),
        "warning": len(triggered) >= 2,
    }


async def batch_stock_detail(tickers: list, token: str, delay: float = 0.3) -> list:
    """여러 종목을 순차 조회해 간소화된 상세 정보 리스트 반환.

    각 종목: {ticker, name, price, chg_pct, vol, w52h, w52l, per, pbr, frgn_net, inst_net}
    실패 종목: {ticker, error: "..."}
    """
    results = []
    for ticker in tickers:
        row = {"ticker": ticker}
        try:
            p = await kis_stock_price(ticker, token)
            await asyncio.sleep(delay * 0.6)
            inv = await kis_investor_trend(ticker, token)
            await asyncio.sleep(delay * 0.4)
            row.update({
                "name":     p.get("hts_kor_isnm", ticker),
                "price":    int(p.get("stck_prpr", 0) or 0),
                "chg_pct":  float(p.get("prdy_ctrt", 0) or 0),
                "vol":      int(p.get("acml_vol", 0) or 0),
                "w52h":     int(p.get("w52_hgpr", 0) or 0),
                "w52l":     int(p.get("w52_lwpr", 0) or 0),
                "per":      p.get("per"),
                "pbr":      p.get("pbr"),
                "frgn_net": int(inv[0].get("frgn_ntby_qty", 0) or 0) if inv else 0,
                "inst_net": int(inv[0].get("orgn_ntby_qty", 0) or 0) if inv else 0,
            })
        except Exception as e:
            row["error"] = str(e)
        results.append(row)
    return results


async def kis_program_trade_today(token: str, market: str = "kospi") -> list:
    """프로그램매매 투자자별 당일 동향 (HHPPG046600C1).

    market: "kospi"(1) or "kosdaq"(4)
    Returns: [{investor, total_net_qty, total_net_amt, arb_net_qty, non_arb_net_qty}, ...]
    """
    mrkt_code = "1" if market.lower() == "kospi" else "4"
    s = _get_session()
    _, d = await _kis_get(s,
        "/uapi/domestic-stock/v1/quotations/investor-program-trade-today",
        "HHPPG046600C1", token,
        {"MRKT_DIV_CLS_CODE": mrkt_code})
    result = []
    for row in d.get("output1", []):
        name = (row.get("invr_cls_name") or "").strip()
        if not name:
            continue
        result.append({
            "investor":        name,
            "total_net_qty":   int(row.get("all_ntby_qty",  0) or 0),
            "total_net_amt":   int(row.get("all_ntby_amt",  0) or 0),
            "arb_net_qty":     int(row.get("arbt_ntby_qty", 0) or 0),
            "non_arb_net_qty": int(row.get("nabt_ntby_qty", 0) or 0),
        })
    return result


async def kis_investor_trend_estimate(ticker: str, token: str) -> dict:
    """장중 투자자 추정 수급 가집계 (HHPTJ04160200).
    외국인·기관 추정 순매수 수량 (확정치 아님, 장중 업데이트).
    Returns: {ticker, foreign_est_net, institution_est_net, sum_est_net, is_estimate: True}
    """
    try:
        s = _get_session()
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/investor-trend-estimate",
            "HHPTJ04160200", token,
            {"MKSC_SHRN_ISCD": ticker})
        rows = d.get("output2", [])
        row = rows[-1] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})
        return {
            "ticker":              ticker,
            "foreign_est_net":     int(row.get("frgn_fake_ntby_qty", 0) or 0),
            "institution_est_net": int(row.get("orgn_fake_ntby_qty", 0) or 0),
            "sum_est_net":         int(row.get("sum_fake_ntby_qty",  0) or 0),
            "is_estimate":         True,
        }
    except Exception as e:
        print(f"[kis_investor_trend_estimate] 오류: {e}")
        return {"ticker": ticker, "error": str(e)}


async def kis_foreign_institution_total(token: str, sort: str = "buy", n: int = 20) -> list:
    """외국인+기관 합산 순매수 상위 종목 가집계 (FHPTJ04400000).

    sort: "buy"=순매수 상위, "sell"=순매도 상위
    Returns: [{ticker, name, price, chg_pct, foreign_net, institution_net, fi_total_net}, ...]
    """
    rank_code = "0" if sort == "buy" else "1"
    hdrs = _kis_headers(token, "FHPTJ04400000")
    params = {
        "FID_COND_MRKT_DIV_CODE": "V",
        "FID_COND_SCR_DIV_CODE":  "16449",
        "FID_INPUT_ISCD":         "0000",
        "FID_DIV_CLS_CODE":       "0",
        "FID_RANK_SORT_CLS_CODE": rank_code,
        "FID_ETC_CLS_CODE":       "0",
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/foreign-institution-total",
                             headers=hdrs, params=params) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        print(f"[kis_foreign_institution_total] 오류: {e}")
        return []

    result = []
    for item in data.get("output", [])[:n]:
        ticker = (item.get("mksc_shrn_iscd") or "").strip()
        if not ticker:
            continue
        frgn = int(item.get("frgn_ntby_qty", 0) or 0)
        orgn = int(item.get("orgn_ntby_qty", 0) or 0)
        result.append({
            "ticker":          ticker,
            "name":            (item.get("hts_kor_isnm") or "").strip(),
            "price":           int(item.get("stck_prpr", 0) or 0),
            "chg_pct":         float(item.get("prdy_ctrt", 0) or 0),
            "foreign_net":     frgn,
            "institution_net": orgn,
            "fi_total_net":    frgn + orgn,
        })
    return result


async def kis_daily_short_sale(ticker: str, token: str, n: int = 10, session=None) -> list:
    """국내주식 공매도 일별추이 (FHPST04830000).

    Returns: [{date, short_vol, total_vol, short_ratio, close}, ...]
    날짜범위 파라미터로 조회 (페이징 없음, 범위 내 전체 반환).
    """
    try:
        today = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=int(n * 1.6))).strftime("%Y%m%d")
        s = session or aiohttp.ClientSession()
        try:
            _, d = await _kis_get(s,
                "/uapi/domestic-stock/v1/quotations/daily-short-sale",
                "FHPST04830000", token,
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD":         ticker,
                    "FID_INPUT_DATE_1":       start,
                    "FID_INPUT_DATE_2":       today,
                })
        finally:
            if session is None:
                await s.close()
        result = []
        for row in d.get("output2", [])[:n]:
            result.append({
                "date":        row.get("stck_bsop_date", ""),
                "short_vol":   int(row.get("ssts_cntg_qty",  0) or 0),
                "total_vol":   int(row.get("acml_vol",        0) or 0),
                "short_ratio": float(row.get("ssts_vol_rlim", 0) or 0),
                "close":       int(row.get("stck_clpr",       0) or 0),
            })
        return result
    except Exception as e:
        print(f"[kis_daily_short_sale] 오류: {e}")
        return []


async def kis_news_title(ticker: str, token: str, n: int = 10) -> list:
    """종목 관련 뉴스 제목 조회 (FHKST01011800).

    Returns: [{date, time, title, source}, ...]
    """
    try:
        s = _get_session()
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/news-title",
            "FHKST01011800", token,
            {
                "FID_NEWS_OFER_ENTP_CODE": "",
                "FID_COND_MRKT_CLS_CODE":  "",
                "FID_INPUT_ISCD":          ticker,
                "FID_TITL_CNTT":           "",
                "FID_INPUT_DATE_1":        "",
                "FID_INPUT_HOUR_1":        "",
                "FID_RANK_SORT_CLS_CODE":  "0",
                "FID_INPUT_SRNO":          "",
            })
        result = []
        for row in d.get("output", [])[:n]:
            title = (row.get("hts_pbnt_titl_cntt") or "").strip()
            if not title:
                continue
            result.append({
                "date":   row.get("data_dt", ""),
                "time":   row.get("data_tm", ""),
                "title":  title,
                "source": (row.get("dorg") or "").strip(),
            })
        return result
    except Exception as e:
        print(f"[kis_news_title] 오류: {e}")
        return []


def analyze_news_sentiment(news_items: list) -> dict:
    """뉴스 헤드라인 감성 분석 (KNU 사전 + 금융 특화 규칙).

    알고리즘:
    1. 기계적 순위 기사 패턴 → 즉시 neutral
    2. FINANCE_PHRASE_SCORES (다단어, 우선 적용) → score 누적
    3. KNU 사전 단어 점수 (finance phrase 커버 범위 제외) → score 누적
    4. 부정어 반전 (않/없/못/안, 앞 키워드 3자 이내) → 부호 반전
    5. score > 0 → positive | score < 0 → negative | else → neutral

    Returns: {positive: [...], negative: [...], neutral: [...], summary: str}
    """
    knu = _load_knu_senti_lex()
    positive, negative, neutral = [], [], []

    for item in news_items:
        title = item.get("title", "")
        entry = {**item}

        # 1. 기계적 순위 기사 필터
        if _RANKING_RE.search(title):
            entry["sentiment"] = "neutral"
            entry["matched_keywords"] = ["[순위기사]"]
            entry["score"] = 0
            neutral.append(entry)
            continue

        score = 0
        matched = []
        covered = set()  # 이미 finance phrase가 커버한 문자 인덱스

        # 2. 금융 특화 구문 (다단어, 긴 것 먼저 — 이미 covered된 위치는 스킵)
        for phrase, phrase_score in _FINANCE_PHRASE_SCORES:
            start = 0
            while True:
                idx = title.find(phrase, start)
                if idx == -1:
                    break
                # 더 긴 구문이 이미 이 위치를 커버했으면 스킵 (중복 점수 방지)
                if not covered.isdisjoint(range(idx, idx + len(phrase))):
                    start = idx + len(phrase)
                    continue
                # 구문 직후 부정어 반전 확인
                suffix = title[idx + len(phrase): idx + len(phrase) + 10]
                actual_score = -phrase_score if re.search(r'않|없(?!지만|더라도)|못|안\s|아닌(?!지만|데)|아니(?!지만|더라도|라도)', suffix) else phrase_score
                score += actual_score
                matched.append(f"{phrase}({'+' if actual_score > 0 else ''}{actual_score})")
                for i in range(idx, idx + len(phrase)):
                    covered.add(i)
                start = idx + len(phrase)

        # 3. KNU 사전 단어 점수 (covered 범위 제외, 1자 단어는 오매칭 위험으로 제외)
        for word, word_score in knu.items():
            if not word_score or not word or len(word) < 2:
                continue
            start = 0
            while True:
                idx = title.find(word, start)
                if idx == -1:
                    break
                # covered 범위와 겹치면 스킵
                if covered.isdisjoint(range(idx, idx + len(word))):
                    # 4. 부정어 반전 확인 (키워드 직후 10자 이내)
                    suffix = title[idx + len(word): idx + len(word) + 10]
                    if re.search(r'않|없(?!지만|더라도)|못|안\s|아닌(?!지만|데)|아니(?!지만|더라도|라도)', suffix):
                        score -= word_score  # 부호 반전
                        matched.append(f"{word}(반전:{-word_score:+d})")
                    else:
                        score += word_score
                        if abs(word_score) >= 1:
                            matched.append(f"{word}({word_score:+d})")
                start = idx + len(word)

        # 5. 점수 → 감성 판정 (임계값 1)
        entry["matched_keywords"] = matched[:10]  # 상위 10개만 노출
        entry["score"] = score
        if score > 0:
            entry["sentiment"] = "positive"
            positive.append(entry)
        elif score < 0:
            entry["sentiment"] = "negative"
            negative.append(entry)
        else:
            entry["sentiment"] = "neutral"
            neutral.append(entry)

    summary = f"🟢긍정 {len(positive)} / 🔴부정 {len(negative)} / ⚪중립 {len(neutral)}"
    return {"positive": positive, "negative": negative, "neutral": neutral, "summary": summary}


async def kis_vi_status(token: str) -> list:
    """변동성완화장치(VI) 발동 종목 현황 (FHPST01390000).

    Returns: [{ticker, name, vi_type, vi_price, base_price, trigger_time, release_time, count}, ...]
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    try:
        s = _get_session()
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/inquire-vi-status",
            "FHPST01390000", token,
            {
                "FID_DIV_CLS_CODE":       "0",
                "FID_COND_SCR_DIV_CODE":  "20139",
                "FID_MRKT_CLS_CODE":      "0",
                "FID_INPUT_ISCD":         "",
                "FID_RANK_SORT_CLS_CODE": "0",
                "FID_INPUT_DATE_1":       today,
                "FID_TRGT_CLS_CODE":      "",
                "FID_TRGT_EXLS_CLS_CODE": "",
            })
        result = []
        for row in d.get("output", []):
            ticker = (row.get("mksc_shrn_iscd") or "").strip()
            if not ticker:
                continue
            vi_kind = row.get("vi_kind_code", "")
            vi_type = {"1": "정적VI", "2": "동적VI", "3": "정적+동적VI"}.get(vi_kind, vi_kind)
            result.append({
                "ticker":       ticker,
                "name":         (row.get("hts_kor_isnm") or "").strip(),
                "vi_type":      vi_type,
                "vi_price":     int(row.get("vi_prc",      0) or 0),
                "base_price":   int(row.get("vi_stnd_prc", 0) or 0),
                "trigger_time": row.get("cntg_vi_hour", ""),
                "release_time": row.get("vi_cncl_hour", ""),
                "count":        int(row.get("vi_count",    0) or 0),
            })
        return result
    except Exception as e:
        print(f"[kis_vi_status] 오류: {e}")
        return []


async def kis_volume_power_rank(token: str, market: str = "all", n: int = 20) -> list:
    """체결강도 상위 종목 순위 (FHPST01680000).

    market: "all"=전체, "kospi"=코스피, "kosdaq"=코스닥
    Returns: [{ticker, name, price, chg_pct, volume_power_pct, buy_vol, sell_vol}, ...]
    """
    market_code = {"all": "0000", "kospi": "0001", "kosdaq": "1001"}.get(market.lower(), "0000")
    hdrs = _kis_headers(token, "FHPST01680000")
    params = {
        "fid_trgt_exls_cls_code": "0",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code":  "20168",
        "fid_input_iscd":         market_code,
        "fid_div_cls_code":       "0",
        "fid_input_price_1":      "",
        "fid_input_price_2":      "",
        "fid_vol_cnt":            "",
        "fid_trgt_cls_code":      "0",
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/ranking/volume-power",
                             headers=hdrs, params=params) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        print(f"[kis_volume_power_rank] 오류: {e}")
        return []

    result = []
    for item in data.get("output", [])[:n]:
        ticker = (item.get("stck_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "ticker":           ticker,
            "name":             (item.get("hts_kor_isnm") or "").strip(),
            "price":            int(item.get("stck_prpr",      0) or 0),
            "chg_pct":          float(item.get("prdy_ctrt",    0) or 0),
            "volume_power_pct": float(item.get("tday_rltv",    0) or 0),
            "buy_vol":          int(item.get("shnu_cnqn_smtn", 0) or 0),
            "sell_vol":         int(item.get("seln_cnqn_smtn", 0) or 0),
        })
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 재무비율 순위 / 52주 신고가·신저가 / 거래원
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def kis_finance_ratio_rank(token: str, market: str = "0000",
                                  year: str = "", quarter: str = "3",
                                  sort: str = "7", n: int = 30) -> list:
    """전종목 재무비율 순위 (FHPST01750000).

    market: 0000=전체, 0001=거래소, 1001=코스닥, 2001=코스피200
    year: 회계연도 (기본=전년도)
    quarter: 0=1Q, 1=반기, 2=3Q, 3=결산
    sort: 7=수익성, 11=안정성, 15=성장성, 20=활동성
    """
    if not year:
        year = str(datetime.now(KST).year - 1)

    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ranking/finance-ratio",
                          "FHPST01750000", token, {
        "fid_trgt_cls_code": "0",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20175",
        "fid_input_iscd": market,
        "fid_div_cls_code": "0",
        "fid_input_price_1": "",
        "fid_input_price_2": "",
        "fid_vol_cnt": "",
        "fid_input_option_1": year,
        "fid_input_option_2": quarter,
        "fid_rank_sort_cls_code": sort,
        "fid_blng_cls_code": "0",
        "fid_trgt_exls_cls_code": "0",
    })
    items = d.get("output", [])
    if os.environ.get("DEBUG") and items:
        print(f"[DEBUG] finance_ratio keys: {list(items[0].keys())}")
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
            # 수익성 (sort=7)
            "capital_profit_rate": float(item.get("cptl_op_prfi", 0) or 0),    # 총자본경상이익률
            "capital_net_rate": float(item.get("cptl_ntin_rate", 0) or 0),     # 총자본순이익률
            "sales_gross_rate": float(item.get("sale_totl_rate", 0) or 0),     # 매출액총이익률
            "sales_net_rate": float(item.get("sale_ntin_rate", 0) or 0),       # 매출액순이익률
            # 안정성 (sort=11)
            "equity_ratio": float(item.get("bis", 0) or 0),                    # 자기자본비율
            "debt_ratio": float(item.get("lblt_rate", 0) or 0),               # 부채비율
            "borrowing_dep": float(item.get("bram_depn", 0) or 0),            # 차입금의존도
            "reserve_rate": float(item.get("rsrv_rate", 0) or 0),             # 유보비율
            # 성장성 (sort=15)
            "revenue_growth": float(item.get("grs", 0) or 0),                 # 매출액증가율
            "op_profit_growth": float(item.get("bsop_prfi_inrt", 0) or 0),    # 영업이익증가율
            "net_profit_growth": float(item.get("ntin_inrt", 0) or 0),        # 순이익증가율
            "equity_growth": float(item.get("equt_inrt", 0) or 0),            # 자기자본증가율
            "total_asset_growth": float(item.get("totl_aset_inrt", 0) or 0),  # 총자산증가율
            # 활동성 (sort=20)
            "capital_turnover": float(item.get("cptl_tnrt", 0) or 0),         # 총자본회전율
            "volume": int(item.get("acml_vol", 0) or 0),
        })
    return result


async def kis_near_new_highlow(token: str, mode: str = "high",
                                market: str = "0000", gap_min: int = 0,
                                gap_max: int = 10, n: int = 30) -> list:
    """52주 신고가/신저가 근접 종목 (FHPST01870000).

    mode: "high"=신고가 근접, "low"=신저가 근접
    market: 0000=전체, 0001=거래소, 1001=코스닥
    gap_min/gap_max: 괴리율 범위 (%)
    """
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ranking/near-new-highlow",
                          "FHPST01870000", token, {
        "fid_aply_rang_vol": "0",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20187",
        "fid_div_cls_code": "0",
        "fid_input_cnt_1": str(gap_min),
        "fid_input_cnt_2": str(gap_max),
        "fid_prc_cls_code": "0" if mode == "high" else "1",
        "fid_input_iscd": market,
        "fid_trgt_cls_code": "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_aply_rang_prc_1": "0",
        "fid_aply_rang_prc_2": "10000000",
    })
    items = d.get("output", [])
    if os.environ.get("DEBUG") and items:
        print(f"[DEBUG] near_new_highlow keys: {list(items[0].keys())}")
    result = []
    for i, item in enumerate(items[:n]):
        ticker = (item.get("stck_shrn_iscd") or item.get("mksc_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "rank": i + 1,
            "ticker": ticker,
            "name": (item.get("hts_kor_isnm") or "").strip(),
            "price": int(item.get("stck_prpr", 0) or 0),
            "chg_pct": float(item.get("prdy_ctrt", 0) or 0),
            "base_price": int(item.get("stck_sdpr", 0) or 0),
            "new_high": int(item.get("new_hgpr", 0) or 0),
            "high_gap_pct": float(item.get("hprc_near_rate", 0) or 0),
            "new_low": int(item.get("new_lwpr", 0) or 0),
            "low_gap_pct": float(item.get("lwpr_near_rate", 0) or 0),
            "volume": int(item.get("acml_vol", 0) or 0),
        })
    return result


async def kis_inquire_member(ticker: str, token: str) -> dict:
    """종목별 거래원(증권사) 매매 정보 (FHKST01010600, inquire-member).

    Returns: {ticker, name, buy_members: [...], sell_members: [...]}
    """
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-member",
                          "FHKST01010600", token, {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
    })
    output = d.get("output", {})
    if os.environ.get("DEBUG") and output:
        keys = list(output.keys()) if isinstance(output, dict) else list(output[0].keys()) if output else []
        print(f"[DEBUG] inquire_member keys: {keys}")
    # output은 단일 dict, 필드가 seln_mbcr_name1~5, total_seln_qty1~5 등 번호 접미사
    if isinstance(output, list):
        output = output[0] if output else {}
    sell_members = []
    buy_members = []
    for i in range(1, 6):
        sname = (output.get(f"seln_mbcr_name{i}") or "").strip()
        sqty = int(output.get(f"total_seln_qty{i}", 0) or 0)
        srlim = float(output.get(f"seln_mbcr_rlim{i}", 0) or 0)
        if sname:
            sell_members.append({"name": sname, "volume": sqty, "ratio": srlim})
        bname = (output.get(f"shnu_mbcr_name{i}") or "").strip()
        bqty = int(output.get(f"total_shnu_qty{i}", 0) or 0)
        brlim = float(output.get(f"shnu_mbcr_rlim{i}", 0) or 0)
        if bname:
            buy_members.append({"name": bname, "volume": bqty, "ratio": brlim})
    note = None
    if not sell_members and not buy_members:
        note = "거래원 데이터 없음 (휴장일이거나 장중 미제공)"
    result = {
        "ticker": ticker,
        "buy_members": buy_members,
        "sell_members": sell_members,
    }
    if note:
        result["note"] = note
    return result


async def kis_daily_credit_balance(ticker: str, token: str, n: int = 20) -> list:
    """신용잔고 일별추이 (FHPST04760000).

    Returns: [{date, credit_balance, credit_ratio, change, ...}, ...]
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/daily-credit-balance",
                          "FHPST04760000", token, {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20476",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": today,
    })
    items = d.get("output", d.get("output1", []))
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        result.append({
            "date": (item.get("deal_date") or item.get("bsop_date") or "").strip(),
            "credit_balance": int(item.get("whol_loan_rmnd_stcn", 0) or 0),
            "credit_ratio": float(item.get("whol_loan_rmnd_rate", 0) or 0),
            "credit_new": int(item.get("whol_loan_new_stcn", 0) or 0),
            "credit_repay": int(item.get("whol_loan_rdmp_stcn", 0) or 0),
            "close": int(item.get("stck_prpr", 0) or 0),
        })
    # 전일 대비 증감 계산
    for i, row in enumerate(result):
        if i + 1 < len(result):
            row["change"] = row["credit_balance"] - result[i + 1]["credit_balance"]
        else:
            row["change"] = 0
    return result


async def kis_daily_loan_trans(ticker: str, token: str, n: int = 20) -> list:
    """대차거래 일별추이 (HHPST074500C0).

    Returns: [{date, loan_balance, loan_new, loan_repay, ...}, ...]
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    start = (datetime.now(KST) - timedelta(days=n * 2)).strftime("%Y%m%d")
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/daily-loan-trans",
                          "HHPST074500C0", token, {
        "MRKT_DIV_CLS_CODE": "3",
        "MKSC_SHRN_ISCD": ticker,
        "START_DATE": start,
        "END_DATE": today,
        "CTS": "",
    })
    items = d.get("output1", d.get("output", []))
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        result.append({
            "date": (item.get("bsop_date") or "").strip(),
            "loan_balance": int(item.get("rmnd_stcn", 0) or 0),
            "loan_new": int(item.get("new_stcn", 0) or 0),
            "loan_repay": int(item.get("rdmp_stcn", 0) or 0),
            "loan_balance_amt": int(item.get("rmnd_amt", 0) or 0),
        })
    # 전일 대비 증감
    for i, row in enumerate(result):
        if i + 1 < len(result):
            row["change"] = row["loan_balance"] - result[i + 1]["loan_balance"]
        else:
            row["change"] = 0
    return result


async def kis_overtime_price(ticker: str, token: str, session=None) -> dict:
    """시간외 현재가 (FHPST02300000).

    Returns: {ticker, overtime_price, overtime_chg_rate, overtime_vol, ...}
    """
    s = session or aiohttp.ClientSession()
    try:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-overtime-price",
                              "FHPST02300000", token, {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        })
    finally:
        if session is None:
            await s.close()
    out = d.get("output", {})
    if isinstance(out, list):
        out = out[0] if out else {}
    return {
        "ticker": ticker,
        "overtime_price": int(out.get("ovtm_untp_prpr", 0) or 0),
        "overtime_chg_rate": float(out.get("ovtm_untp_prdy_ctrt", 0) or 0),
        "overtime_vol": int(out.get("ovtm_untp_vol", 0) or 0),
        "overtime_tr_pbmn": int(out.get("ovtm_untp_tr_pbmn", 0) or 0),
        "close": int(out.get("stck_prpr", 0) or 0),
        "base_price": int(out.get("stck_sdpr", 0) or 0),
        "chg_pct": float(out.get("prdy_ctrt", 0) or 0),
    }


async def kis_overtime_daily(ticker: str, token: str, session=None) -> dict:
    """시간외 일자별 주가 (FHPST02320000). 최근 30일."""
    s = session or aiohttp.ClientSession()
    try:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-daily-overtimeprice",
            "FHPST02320000", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        rows = d.get("output2", [])
        if not rows:
            return {}
        r = rows[0]  # 최신 1일
        return {
            "ovtm_close": int(r.get("ovtm_untp_prpr", 0) or 0),
            "ovtm_change_pct": float(r.get("ovtm_untp_prdy_ctrt", 0) or 0),
            "ovtm_volume": int(r.get("ovtm_untp_vol", 0) or 0),
        }
    finally:
        if session is None:
            await s.close()


async def kis_income_statement(ticker: str, token: str, session=None) -> list:
    """손익계산서 분기별 (FHKST66430200). 최근 ~30분기."""
    s = session or aiohttp.ClientSession()
    try:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/finance/income-statement",
            "FHKST66430200", token,
            {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        rows = d.get("output", [])
        result = []
        for r in rows:
            period = str(r.get("stac_yymm", ""))
            if not period:
                continue
            def _pf(v):
                try:
                    return float(v)
                except Exception:
                    return 0.0
            result.append({
                "report_period":  period,
                "revenue":        _pf(r.get("sale_account")),
                "cost_of_sales":  _pf(r.get("sale_cost")),
                "gross_profit":   _pf(r.get("sale_totl_prfi")),
                "operating_profit": _pf(r.get("bsop_prti")),
                "op_prfi":        _pf(r.get("op_prfi")),
                "net_income":     _pf(r.get("thtr_ntin")),
            })
        return result
    finally:
        if session is None:
            await s.close()


async def kis_balance_sheet(ticker: str, token: str, session=None) -> list:
    """대차대조표 분기별 (FHKST66430100). 최근 ~30분기."""
    s = session or aiohttp.ClientSession()
    try:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/finance/balance-sheet",
            "FHKST66430100", token,
            {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        rows = d.get("output", [])
        result = []
        for r in rows:
            period = str(r.get("stac_yymm", ""))
            if not period:
                continue
            def _pf(v):
                try:
                    return float(v)
                except Exception:
                    return 0.0
            result.append({
                "report_period":  period,
                "current_assets": _pf(r.get("cras")),
                "fixed_assets":   _pf(r.get("fxas")),
                "total_assets":   _pf(r.get("total_aset")),
                "current_liab":   _pf(r.get("flow_lblt")),
                "fixed_liab":     _pf(r.get("fix_lblt")),
                "total_liab":     _pf(r.get("total_lblt")),
                "capital":        _pf(r.get("cpfn")),
                "total_equity":   _pf(r.get("total_cptl")),
            })
        return result
    finally:
        if session is None:
            await s.close()


async def kis_asking_price(ticker: str, token: str) -> dict:
    """호가 잔량 (FHKST01010200).

    Returns: {ticker, asks: [{price, volume}], bids: [{price, volume}],
             total_ask_vol, total_bid_vol, bid_ask_ratio}
    """
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                          "FHKST01010200", token, {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
    })
    out1 = d.get("output1", {})
    out2 = d.get("output2", {})
    if isinstance(out1, list):
        out1 = out1[0] if out1 else {}
    if isinstance(out2, list):
        out2 = out2[0] if out2 else {}

    asks = []  # 매도호가 (낮은 가격부터)
    bids = []  # 매수호가 (높은 가격부터)
    for i in range(1, 11):
        ask_p = int(out1.get(f"askp{i}", 0) or 0)
        ask_v = int(out1.get(f"askp_rsqn{i}", 0) or 0)
        bid_p = int(out1.get(f"bidp{i}", 0) or 0)
        bid_v = int(out1.get(f"bidp_rsqn{i}", 0) or 0)
        if ask_p:
            asks.append({"price": ask_p, "volume": ask_v})
        if bid_p:
            bids.append({"price": bid_p, "volume": bid_v})

    total_ask = int(out1.get("total_askp_rsqn", 0) or 0)
    total_bid = int(out1.get("total_bidp_rsqn", 0) or 0)
    ratio = round(total_bid / total_ask * 100, 1) if total_ask > 0 else 0

    return {
        "ticker": ticker,
        "asks": asks,
        "bids": bids,
        "total_ask_vol": total_ask,
        "total_bid_vol": total_bid,
        "bid_ask_ratio": ratio,
        "price": int(out2.get("stck_prpr", 0) or 0),
        "chg_pct": float(out2.get("prdy_ctrt", 0) or 0),
    }


