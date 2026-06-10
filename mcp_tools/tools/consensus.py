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


def _compute_date_distribution(broker_targets: list) -> dict:
    """broker_targets 리스트에서 발행일자 분포 필드 3개를 계산한다.

    Returns:
        {
            "broker_dist": [...],          # broker_targets 그대로 (date 기준 내림차순 정렬)
            "date_dist": {
                "last_7_days": int,
                "last_30_days": int,
                "last_90_days": int,
            },
            "median_age_days": int | None, # 중앙값 발행 경과일 (오늘 기준)
        }
    """
    today = datetime.now().date()
    ages = []
    for row in broker_targets:
        raw = row.get("date", "")
        if not raw:
            continue
        try:
            pub = datetime.strptime(raw[:10], "%Y-%m-%d").date()
            ages.append((today - pub).days)
        except Exception:
            pass

    last_7  = sum(1 for a in ages if a <= 7)
    last_30 = sum(1 for a in ages if a <= 30)
    last_90 = sum(1 for a in ages if a <= 90)

    if ages:
        sorted_ages = sorted(ages)
        mid = len(sorted_ages) // 2
        if len(sorted_ages) % 2 == 1:
            median_age = sorted_ages[mid]
        else:
            median_age = (sorted_ages[mid - 1] + sorted_ages[mid]) // 2
    else:
        median_age = None

    sorted_dist = sorted(broker_targets, key=lambda r: r.get("date", ""), reverse=True)

    return {
        "broker_dist": sorted_dist,
        "date_dist": {
            "last_7_days":  last_7,
            "last_30_days": last_30,
            "last_90_days": last_90,
        },
        "median_age_days": median_age,
    }


async def handle_get_consensus(arguments: dict) -> dict | list:
    result = None
    ticker = arguments.get("ticker", "").strip().upper()
    brief = arguments.get("brief", False)
    if isinstance(brief, str):
        brief = brief.lower() in ("true", "1", "yes")
    if not ticker:
        result = {"error": "ticker는 필수입니다"}
    elif ticker.isdigit():
        result = await asyncio.get_running_loop().run_in_executor(
            None, fetch_fnguide_consensus, ticker
        )
    else:
        r = await asyncio.get_running_loop().run_in_executor(
            None, get_us_consensus, ticker
        )
        result = r if r else {"error": f"{ticker} 컨센서스 데이터 없음"}

    # 발행일자 분포 추가 (KR only — broker_targets 있을 때만)
    if isinstance(result, dict) and "error" not in result and result.get("broker_targets"):
        dist = _compute_date_distribution(result["broker_targets"])
        result["broker_dist"]   = dist["broker_dist"]
        result["date_dist"]     = dist["date_dist"]
        result["median_age_days"] = dist["median_age_days"]

    if brief and isinstance(result, dict) and "error" not in result:
        if "reports" in result:
            result["reports"] = [
                {k: r[k] for k in ("broker", "date", "target", "title") if k in r}
                for r in result["reports"][:5]
            ]
        if "broker_targets" in result:
            result["broker_targets"] = result["broker_targets"][:5]
        if "broker_dist" in result:
            result["broker_dist"] = result["broker_dist"][:5]

    return result


