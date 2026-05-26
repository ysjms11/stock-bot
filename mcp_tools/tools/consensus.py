# mcp_tools/tools/consensus.py — get_consensus
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


async def handle_get_consensus(arguments: dict) -> dict | list:
    result = None
    ticker = arguments.get("ticker", "").strip().upper()
    brief = arguments.get("brief", False)
    if isinstance(brief, str):
        brief = brief.lower() in ("true", "1", "yes")
    if not ticker:
        result = {"error": "ticker는 필수입니다"}
    elif ticker.isdigit():
        result = await asyncio.get_event_loop().run_in_executor(
            None, fetch_fnguide_consensus, ticker
        )
    else:
        r = await asyncio.get_event_loop().run_in_executor(
            None, get_us_consensus, ticker
        )
        result = r if r else {"error": f"{ticker} 컨센서스 데이터 없음"}
    if brief and isinstance(result, dict) and "error" not in result:
        if "reports" in result:
            result["reports"] = [
                {k: r[k] for k in ("broker", "date", "target", "title") if k in r}
                for r in result["reports"][:5]
            ]
        if "broker_targets" in result:
            result["broker_targets"] = result["broker_targets"][:5]

    return result


