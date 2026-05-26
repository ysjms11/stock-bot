# mcp_tools/tools/news.py — get_news
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


async def handle_get_news(arguments: dict, token=None) -> dict | list:
    result = None
    sentiment = arguments.get("sentiment", False)
    if isinstance(sentiment, str):
        sentiment = sentiment.lower() in ("true", "1", "yes")

    if sentiment:
        # ← 감성분석 모드
        ticker = arguments.get("ticker", "").strip()
        if ticker and _is_us_ticker(ticker):
            # 미국 종목 감성분석
            loop = asyncio.get_running_loop()
            news = await loop.run_in_executor(None, fetch_us_news, ticker, 15)
            analysis = analyze_us_news_sentiment(news)
            result = {"ticker": ticker, "market": "US", **analysis}
        elif ticker:
            # 한국 종목 감성분석
            news = await kis_news_title(ticker, token, n=15)
            analysis = analyze_news_sentiment(news)
            result = {"ticker": ticker, **analysis}
        else:
            portfolio = load_json(PORTFOLIO_FILE, {})
            watchlist = load_watchlist()
            tickers = {}
            for t, v in portfolio.items():
                if t not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict):
                    tickers[t] = v.get("name", t)
            for t, n in watchlist.items():
                if t not in tickers:
                    tickers[t] = n
            all_results = []
            for t, nm in tickers.items():
                try:
                    news = await kis_news_title(t, token, n=10)
                    analysis = analyze_news_sentiment(news)
                    all_results.append({"ticker": t, "name": nm, **analysis})
                    await asyncio.sleep(0.3)
                except Exception:
                    pass
            all_results.sort(key=lambda x: len(x.get("negative", [])), reverse=True)
            total_pos = sum(len(r.get("positive", [])) for r in all_results)
            total_neg = sum(len(r.get("negative", [])) for r in all_results)
            total_neu = sum(len(r.get("neutral", [])) for r in all_results)
            result = {
                "stocks": all_results,
                "total_summary": f"긍정 {total_pos} / 부정 {total_neg} / 중립 {total_neu}",
            }
    else:
        # ← 뉴스 헤드라인 모드
        ticker = arguments.get("ticker", "").strip()
        if not ticker:
            result = {"error": "ticker는 필수입니다"}
        elif _is_us_ticker(ticker):
            # 미국 종목 뉴스
            n    = int(arguments.get("n", 10) or 10)
            n    = max(1, min(n, 30))
            loop = asyncio.get_running_loop()
            rows = await loop.run_in_executor(None, fetch_us_news, ticker, n)
            result = {
                "ticker": ticker,
                "market": "US",
                "count":  len(rows),
                "items":  rows,
            }
        else:
            # 한국 종목 뉴스
            n    = int(arguments.get("n", 10) or 10)
            n    = max(1, min(n, 30))
            rows = await kis_news_title(ticker, token, n=n)
            result = {
                "ticker": ticker,
                "count":  len(rows),
                "items":  rows,
            }

    return result


