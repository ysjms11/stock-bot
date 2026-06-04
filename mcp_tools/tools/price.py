# mcp_tools/tools/price.py — get_rank, get_stock_detail
import asyncio
import json
import os
import re
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from kis_api import *
from kis_api import (
    _DATA_DIR, _is_us_ticker, _guess_excd, _kis_get, _get_session,
    _fetch_sector_flow, _TICKER_SECTOR,
    ws_manager, get_ws_tickers,
    collect_macro_data, format_macro_msg,
    check_drawdown, PORTFOLIO_HISTORY_FILE,
    load_trade_log, save_trade_log, get_trade_stats as _get_trade_stats_fn, TRADE_LOG_FILE,
    backup_data_files, restore_data_files, get_backup_status,
    SUPPLY_HISTORY_FILE,
    get_historical_ohlcv, get_historical_supply, compute_volume_profile,
    fetch_us_news, analyze_us_news_sentiment,
    fetch_us_earnings_calendar, fetch_us_sector_etf,
    fetch_us_short_interest,
    cmd_regime,
    kis_finance_ratio_rank, kis_near_new_highlow, kis_inquire_member,
    kis_daily_credit_balance, kis_daily_loan_trans, kis_overtime_price, kis_asking_price,
    kis_overtime_fluctuation, kis_traded_by_company, kis_dividend_rate_rank,
    load_corp_codes, search_dart_reports, save_dart_report,
    list_dart_reports, read_dart_report, DART_REPORTS_DIR,
    list_disclosures_for_ticker, fetch_and_cache_disclosure,
    fetch_youtube_transcript,
    fmp_earnings_transcript, fmp_price_target_summary,
    fmp_analyst_estimates, fmp_stock_grades,
    fetch_polymarket, fetch_treasury_curve, fetch_external_macro_signals,
    fetch_pension_fund_flow,
    WI26_SECTORS, detect_sector_rotation,
    load_sector_flow_cache, save_sector_flow_cache,
    load_decision_log, load_compare_log, load_compare_log,
    append_watchlist_log,
    DECISION_LOG_FILE, COMPARE_LOG_FILE, WATCHALERT_FILE,
)
from db_collector import load_krx_db, scan_stocks, _load_history

try:
    from report_crawler import (
        collect_reports, get_collection_tickers,
        DB_PATH as REPORT_DB_PATH,
    )
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False
    REPORT_DB_PATH = ""


async def handle_get_rank(arguments: dict, token=None) -> dict | list:
    result = None
    rank_type = arguments.get("type", "scan").strip().lower()

    if rank_type == "price":
        # ← 기존 get_price_rank 핸들러
        sort   = arguments.get("sort", "rise").strip().lower()
        market = arguments.get("market", "all").strip().lower()
        n      = int(arguments.get("n", 20) or 20)
        n      = max(1, min(n, 30))
        market_code = {"all": "0000", "kospi": "0001", "kosdaq": "1001"}.get(market, "0000")
        items = await kis_fluctuation_rank(token, market=market_code, sort=sort, n=n)
        result = {
            "sort":   sort,
            "market": market,
            "count":  len(items),
            "items":  items,
        }
        if not items:
            result["note"] = ("장중 등락률 순위 미제공. "
                              "get_scan(preset='momentum' 또는 'oversold')으로 "
                              "KRX DB 기반 전일 데이터를 조회하세요.")

    elif rank_type == "us_price":
        # ← 기존 get_us_price_rank 핸들러
        sort     = arguments.get("sort", "rise").strip().lower()
        exchange = arguments.get("exchange", "NAS").strip().upper()
        n        = int(arguments.get("n", 20) or 20)
        n        = max(1, min(n, 50))
        items    = await kis_us_updown_rate(token, sort=sort, exchange=exchange, n=n)
        result   = {
            "sort":     sort,
            "exchange": exchange,
            "count":    len(items),
            "items":    items,
        }

    elif rank_type == "volume":
        # ← 기존 get_volume_power 핸들러
        market = arguments.get("market", "all").strip().lower()
        n      = int(arguments.get("n", 20) or 20)
        n      = max(1, min(n, 50))
        items  = await kis_volume_power_rank(token, market=market, n=n)
        result = {
            "market": market,
            "count":  len(items),
            "items":  items,
        }

    elif rank_type == "scan":
        # ← 기존 scan_market 핸들러
        rows = await kis_volume_rank_api(token)
        await asyncio.sleep(0.05)
        frgn_rows = await kis_foreigner_trend(token)
        # 외국인 순매수량 dict (ticker → qty)
        frgn_dict = {r.get("mksc_shrn_iscd", ""): int(r.get("frgn_ntby_qty", 0) or 0)
                     for r in frgn_rows}
        result = []
        for r in rows[:15]:
            ticker = r.get("mksc_shrn_iscd")
            frgn_qty = frgn_dict.get(ticker, 0)
            item = {
                "ticker": ticker, "name": r.get("hts_kor_isnm"),
                "vol": r.get("acml_vol"), "chg": r.get("prdy_ctrt"),
                "frgn_ntby_qty": frgn_qty,
                "frgn_buy": frgn_qty > 0,
            }
            if frgn_qty > 0:
                item["tag"] = "외인매수"
            result.append(item)

    elif rank_type == "after_hours":
        sort = arguments.get("sort", "rise").strip().lower()
        market_code = {"all": "0000", "kospi": "0001", "kosdaq": "1001"}.get(
            arguments.get("market", "all").strip().lower(), "0000")
        n = max(1, min(int(arguments.get("n", 20) or 20), 50))
        items = await kis_overtime_fluctuation(token, sort=sort, market=market_code, n=n)
        result = {
            "sort": sort,
            "count": len(items),
            "note": "장 마감 후 시간외 거래 등락률 순위",
            "stocks": items,
        }

    elif rank_type == "dividend":
        market = arguments.get("market", "0").strip()
        n = max(1, min(int(arguments.get("n", 30) or 30), 100))
        items = await kis_dividend_rate_rank(token, market=market, n=n)
        result = {
            "count": len(items),
            "note": "배당수익률 상위 종목 (전년 기준)",
            "stocks": items,
        }

    else:
        result = {"error": f"알 수 없는 type: {rank_type}. price/us_price/volume/scan/after_hours/dividend 중 하나"}

    return result


async def _fetch_us_detail(ticker: str, token: str) -> dict:
    """미국 종목 상세 1건 — 단일/일괄 조회 공용. 필드 shape는 단일조회와 동일."""
    excd = _guess_excd(ticker)
    price_d = await kis_us_stock_price(ticker, token, excd)
    detail_d = await kis_us_stock_detail(ticker, token, excd)
    cur = float(price_d.get("last", 0) or 0)
    base = float(price_d.get("base", 0) or 0)
    return {
        "ticker": ticker, "market": "US",
        "price": cur,
        "chg_pct": float(price_d.get("rate", 0) or 0),
        "volume": int(price_d.get("tvol", 0) or 0),
        "open": float(detail_d.get("open", 0) or 0),
        "high": float(detail_d.get("high", 0) or 0),
        "low": float(detail_d.get("low", 0) or 0),
        "prev_close": base,
        "w52h": float(detail_d.get("h52p", 0) or 0),
        "w52l": float(detail_d.get("l52p", 0) or 0),
        "per": float(detail_d.get("perx", 0) or 0) or None,
        "pbr": float(detail_d.get("pbrx", 0) or 0) or None,
        "eps": float(detail_d.get("epsx", 0) or 0) or None,
        "market_cap": detail_d.get("tomv", ""),
        "sector": detail_d.get("e_icod", ""),
    }


async def handle_get_stock_detail(arguments: dict, token=None) -> dict | list:
    result = None
    # ── 다종목 일괄 조회 (tickers 파라미터) ──
    batch_tickers_raw = arguments.get("tickers", "")
    if batch_tickers_raw:
        # ← 기존 get_batch_detail 핸들러 (미국 티커 지원 추가)
        raw = batch_tickers_raw
        delay = float(arguments.get("delay", 0.3) or 0.3)
        tickers = [t.strip().upper() for t in raw.split(",") if t.strip()][:20]
        if not tickers:
            result = {"error": "tickers는 필수입니다 (콤마 구분 종목코드)"}
        else:
            # ── 한국/미국 분리 후 시장별 조회, 입력 순서 보존 ──
            us_list = [t for t in tickers if _is_us_ticker(t)]
            kr_list = [t for t in tickers if not _is_us_ticker(t)]
            by_ticker: dict = {}
            if kr_list:
                for r in await batch_stock_detail(kr_list, token, delay=delay):
                    by_ticker[r.get("ticker")] = r
            for t in us_list:
                try:
                    by_ticker[t] = await _fetch_us_detail(t, token)
                except Exception as e:
                    by_ticker[t] = {"ticker": t, "error": str(e)}
                await asyncio.sleep(delay)
            result = [by_ticker[t] for t in tickers if t in by_ticker]
    else:
        # ── 단일 종목 조회 (기존 로직) ──
        ticker = arguments.get("ticker", "005930").strip().upper()
        mode = arguments.get("mode", "").strip().lower()
        period = arguments.get("period", "").strip().upper()  # e.g. "D60", "W20"

        if mode == "volume_profile":
            # ── 볼륨 프로파일(매물대) 분석 ──
            if not period or period not in ("Y1", "Y2", "Y3"):
                period = "Y1"
            bins_count = min(int(arguments.get("bins", 20) or 20), 50)
            years_map = {"Y1": 1, "Y2": 2, "Y3": 3}
            years = years_map.get(period, 1)

            candles = await asyncio.to_thread(get_historical_ohlcv, ticker, years)
            if not candles:
                result = {"error": f"{ticker} 일봉 데이터를 가져올 수 없습니다"}
            else:
                # 현재가 조회
                if _is_us_ticker(ticker):
                    excd = _guess_excd(ticker)
                    price_d = await kis_us_stock_price(ticker, token, excd)
                    current_price = float(price_d.get("last", 0) or 0)
                    stock_name = price_d.get("rsym", ticker).replace("D", "").replace("N", "")
                else:
                    price_d = await kis_stock_price(ticker, token)
                    current_price = float(price_d.get("stck_prpr", 0) or 0)
                    stock_name = price_d.get("hts_kor_isnm", ticker)

                if current_price <= 0:
                    # fallback: 마지막 종가
                    current_price = float(candles[-1].get("close", 0))

                result = compute_volume_profile(candles, current_price, bins_count)
                result["ticker"] = ticker
                result["name"] = stock_name
                result["period"] = period
                result["market"] = "US" if _is_us_ticker(ticker) else "KR"

        elif mode == "after_hours":
            # ── 시간외 현재가 ──
            data = await kis_overtime_price(ticker, token)
            result = data

        elif mode == "orderbook":
            # ── 호가 잔량 ──
            data = await kis_asking_price(ticker, token)
            result = data

        elif period:
            # ── 일봉/주봉 조회 모드 ──
            period_type = period[0] if period else "D"  # D/W/M
            try:
                n = int(period[1:])
            except ValueError:
                n = 60
            today_str = datetime.now(KST).strftime("%Y%m%d")
            buffer = {"D": 2, "W": 8, "M": 40}.get(period_type, 2)
            start_dt = (datetime.now(KST) - timedelta(days=n * buffer)).strftime("%Y%m%d")

            if _is_us_ticker(ticker):
                # KIS HHDFS76240000은 단일 페이지 ~30건만 반환 → yfinance로 충분한 일봉 확보
                years_needed = max(1, (n // 250) + 1)
                all_candles = await asyncio.to_thread(get_historical_ohlcv, ticker, years_needed)
                candles_sliced = all_candles[-n:] if len(all_candles) >= n else all_candles
                result = {
                    "ticker": ticker, "market": "US", "period": period,
                    "candles": candles_sliced,
                }
            else:
                s = _get_session()
                _, d = await _kis_get(s,
                    "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                    "FHKST03010100", token,
                    {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
                     "FID_INPUT_DATE_1": start_dt, "FID_INPUT_DATE_2": today_str,
                     "FID_PERIOD_DIV_CODE": period_type, "FID_ORG_ADJ_PRC": "0"})
                candles = d.get("output2", [])
                result = {
                    "ticker": ticker, "market": "KR", "period": period,
                    "candles": [{"date": c.get("stck_bsop_date"),
                                 "open": c.get("stck_oprc"), "high": c.get("stck_hgpr"),
                                 "low": c.get("stck_lwpr"), "close": c.get("stck_clpr"),
                                 "vol": c.get("acml_vol")}
                                for c in candles[:n]],
                }

        elif _is_us_ticker(ticker):
            # ── 미국 주식 ──
            result = await _fetch_us_detail(ticker, token)
        else:
            # ── 한국 주식 ──
            price = await kis_stock_price(ticker, token)
            inv   = await kis_investor_trend(ticker, token)
            result = {
                "ticker": ticker, "market": "KR",
                "price": price.get("stck_prpr"), "chg": price.get("prdy_ctrt"),
                "vol": price.get("acml_vol"),
                "w52h": price.get("w52_hgpr"), "w52l": price.get("w52_lwpr"),
                "per": price.get("per"), "pbr": price.get("pbr"), "eps": price.get("eps"),
                "bps": price.get("bps"),
                "investor": inv if isinstance(inv, list) else [inv] if inv else [],
            }
            # 추정실적 (period 없을 때만)
            try:
                result["earnings"] = await kis_estimate_perform(ticker, token)
            except Exception:
                pass

    return result


