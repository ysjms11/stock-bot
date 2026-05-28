# mcp_tools/tools/manage_report.py — manage_report
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


def _compute_pdf_size_kb(pdf_path: str | None) -> float | None:
    """PDF 파일 크기 KB. 파일 없거나 경로 없으면 None."""
    if not pdf_path:
        return None
    try:
        return round(os.path.getsize(pdf_path) / 1024, 1)
    except (OSError, FileNotFoundError):
        return None


async def handle_manage_report(arguments: dict) -> dict | list:
    result = None
    if not _REPORT_AVAILABLE:
        result = {"error": "report_crawler 모듈 미설치"}
    else:
        action = arguments.get("action", "list").strip().lower()

        if action == "list":
            import sqlite3 as _sqlite3
            days = int(arguments.get("days", 7) or 7)
            ticker_filter = arguments.get("ticker", "").strip()
            category_filter = arguments.get("category", "").strip().lower()
            brief = arguments.get("brief", False)

            cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
            where = ["date >= ?"]
            params = [cutoff]
            if ticker_filter:
                where.append("ticker = ?")
                params.append(ticker_filter)
            if category_filter:
                where.append("category = ?")
                params.append(category_filter)
            sql = f"SELECT * FROM reports WHERE {' AND '.join(where)} ORDER BY date DESC"
            try:
                _conn = _sqlite3.connect(REPORT_DB_PATH, timeout=10)
                _conn.execute("PRAGMA cache_size = -65536")
                _conn.execute("PRAGMA temp_store = MEMORY")
                _conn.execute("PRAGMA mmap_size = 268435456")
                _conn.execute("PRAGMA busy_timeout = 30000")
                _conn.row_factory = _sqlite3.Row
                rows = _conn.execute(sql, tuple(params)).fetchall()
                reports = [dict(r) for r in rows]
                _conn.close()
            except Exception as _e:
                reports = []
                print(f"[manage_report list] SQLite 오류: {_e}")

            if brief:
                reports = [{"report_id": r.get("id"),
                            "date": r.get("date"), "ticker": r.get("ticker"),
                            "name": r.get("name"), "source": r.get("source"),
                            "title": r.get("title"),
                            "extraction_status": r.get("extraction_status", "unknown"),
                            "source_used": r.get("source_used", ""),
                            "pdf_readable": bool(r.get("pdf_path", "")),
                            "pdf_size_kb": _compute_pdf_size_kb(r.get("pdf_path"))} for r in reports]
            else:
                # full_text 3000자 제한 + extraction_status 하위호환 + pdf_readable 플래그
                for r in reports:
                    r["report_id"] = r.get("id")  # alias for read_report_pdf
                    if not r.get("extraction_status"):
                        r["extraction_status"] = "unknown"
                    if r.get("full_text") and len(r["full_text"]) > 3000:
                        r["full_text"] = r["full_text"][:3000] + "...(truncated)"
                    r["pdf_readable"] = bool(r.get("pdf_path", ""))
                    if not r.get("source_used"):
                        r["source_used"] = ""
                    r["pdf_size_kb"] = _compute_pdf_size_kb(r.get("pdf_path"))

            result = {
                "count": len(reports),
                "days": days,
                "reports": reports,
            }

        elif action == "collect":
            ticker_filter = arguments.get("ticker", "").strip()
            category_arg = arguments.get("category", "").strip().lower()

            loop = asyncio.get_running_loop()
            if category_arg in ("industry", "market", "strategy", "economy", "bond"):
                # 비종목 카테고리 수집
                from report_crawler import collect_market_reports
                new_reports = await loop.run_in_executor(
                    None, collect_market_reports, [category_arg])
            else:
                # 종목 분석 (기본)
                tickers = get_collection_tickers()
                if ticker_filter:
                    name_for_ticker = tickers.get(ticker_filter, ticker_filter)
                    tickers = {ticker_filter: name_for_ticker}
                new_reports = await loop.run_in_executor(
                    None, lambda: collect_reports(tickers, force_retry_meta_only=True)
                )

            result = {
                "collected": len(new_reports),
                "category": category_arg or "company",
                "reports": [{"date": r.get("date"), "ticker": r.get("ticker"),
                             "name": r.get("name"), "source": r.get("source"),
                             "title": r.get("title"),
                             "category": r.get("category", "company"),
                             "extraction_status": r.get("extraction_status", "unknown")} for r in new_reports],
            }

        elif action == "tickers":
            tickers = get_collection_tickers()
            result = {
                "count": len(tickers),
                "tickers": [{"ticker": t, "name": n} for t, n in tickers.items()],
            }

        else:
            result = {"error": f"알 수 없는 action: {action}. list|collect|tickers"}

    return result


