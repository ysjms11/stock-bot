# mcp_tools/tools/us.py — get_us_ratings, get_us_scan, get_us_analyst, watch_analyst, get_us_buy_candidates, get_us_earnings_transcript, get_us_analyst_research
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
from mcp_tools._helpers import (
    _exec_us_ratings, _exec_us_scan, _exec_us_analyst, _exec_watch_analyst,
)

try:
    from report_crawler import (
        collect_reports, get_collection_tickers,
        DB_PATH as REPORT_DB_PATH,
    )
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False
    REPORT_DB_PATH = ""


async def handle_get_us_ratings(arguments: dict) -> dict | list:
    result = None
    result = await _exec_us_ratings(**arguments)
    return result


async def handle_get_us_scan(arguments: dict) -> dict | list:
    result = None
    result = await _exec_us_scan(**arguments)
    return result


async def handle_get_us_analyst(arguments: dict) -> dict | list:
    result = None
    result = await _exec_us_analyst(**arguments)
    return result


async def handle_watch_analyst(arguments: dict) -> dict | list:
    slug = (arguments.get("slug") or "").strip()
    if not slug:
        return {"error": "slug 파라미터 필수. 예: watch_analyst(slug='mark-strouse')"}
    watched = bool(arguments.get("watched", True))
    return await _exec_watch_analyst(slug=slug, watched=watched)


async def handle_get_us_buy_candidates(arguments: dict) -> dict | list:
    result = None
    from db_collector import find_us_buy_candidates
    days = int(arguments.get("days") or 180)
    days = max(1, min(days, 365))
    min_advisors = int(arguments.get("min_advisors") or 1)
    min_upside = float(arguments.get("min_upside") if arguments.get("min_upside") is not None else 20.0)
    exclude = bool(arguments.get("exclude_held_and_watch", True))
    limit = int(arguments.get("limit") or 50)
    limit = max(1, min(limit, 200))
    result = await asyncio.to_thread(
        find_us_buy_candidates,
        days, min_advisors, min_upside, exclude, limit
    )

    return result


async def handle_get_us_earnings_transcript(arguments: dict) -> dict | list:
    result = None
    ticker = (arguments.get("ticker") or "").strip().upper()
    year = int(arguments.get("year") or 0)
    quarter = int(str(arguments.get("quarter") or "0").lstrip("Qq") or 0)
    max_chars = int(arguments.get("max_chars") or 0)
    if not ticker or not year or quarter not in (1, 2, 3, 4):
        result = {"error": "ticker/year/quarter(1-4) 필수"}
    else:
        result = await fmp_earnings_transcript(ticker, year, quarter, max_chars)

    return result


async def handle_get_us_analyst_research(arguments: dict) -> dict | list:
    result = None
    ticker = (arguments.get("ticker") or "").strip().upper()
    period = (arguments.get("estimates_period") or "annual").lower()
    est_limit = int(arguments.get("estimates_limit") or 5)
    grades_limit = int(arguments.get("grades_limit") or 20)
    if not ticker:
        result = {"error": "ticker 필수"}
    else:
        summary = await fmp_price_target_summary(ticker)
        estimates = await fmp_analyst_estimates(ticker, period, est_limit)
        grades = await fmp_stock_grades(ticker, grades_limit)
        result = {
            "ticker": ticker,
            "price_target_summary": summary,
            "analyst_estimates": estimates,
            "stock_grades": grades,
        }

    return result


