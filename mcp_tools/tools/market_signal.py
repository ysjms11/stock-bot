# mcp_tools/tools/market_signal.py — get_market_signal, get_alpha_metrics
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


async def handle_get_market_signal(arguments: dict, token=None) -> dict | list:
    result = None
    signal_mode = arguments.get("mode", "").strip().lower()

    if signal_mode == "short_sale":
        ticker = arguments.get("ticker", "").strip()
        if not ticker:
            result = {"error": "ticker는 필수입니다"}
        elif _is_us_ticker(ticker):
            # ── 미국 종목: yfinance short interest ──
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, fetch_us_short_interest, ticker.upper())
            if not result:
                result = {"ticker": ticker, "market": "US", "message": "공매도 데이터 조회 실패"}
            else:
                result["market"] = "US"
        else:
            # ── 한국 종목: KIS API 일별 공매도 ──
            n     = int(arguments.get("days", 10) or 10)
            n     = max(1, min(n, 60))
            rows  = await kis_daily_short_sale(ticker, token, n=n)
            result = {
                "ticker": ticker,
                "market": "KR",
                "count":  len(rows),
                "items":  rows,
            }

    elif signal_mode == "vi":
        # ← 기존 get_vi_status 핸들러
        rows   = await kis_vi_status(token)
        result = {
            "count": len(rows),
            "items": rows,
        }

    elif signal_mode == "program_trade":
        # ← 기존 get_program_trade 핸들러
        market = arguments.get("market", "kospi").strip().lower()
        rows   = await kis_program_trade_today(token, market=market)
        result = {
            "market": market,
            "count":  len(rows),
            "items":  rows,
        }

    elif signal_mode == "credit":
        ticker = arguments.get("ticker", "").strip()
        if not ticker:
            result = {"error": "ticker는 필수입니다"}
        else:
            n = min(int(arguments.get("days", 20) or 20), 60)
            rows = await kis_daily_credit_balance(ticker, token, n=n)
            warning = any(r.get("credit_ratio", 0) >= 10 for r in rows[:3])
            result = {
                "ticker": ticker,
                "count": len(rows),
                "warning": "⚠️ 신용잔고 비율 10% 이상 — 투기적 과열 주의" if warning else None,
                "items": rows,
            }

    elif signal_mode == "lending":
        ticker = arguments.get("ticker", "").strip()
        if not ticker:
            result = {"error": "ticker는 필수입니다"}
        else:
            n = min(int(arguments.get("days", 20) or 20), 60)
            rows = await kis_daily_loan_trans(ticker, token, n=n)
            result = {
                "ticker": ticker,
                "count": len(rows),
                "items": rows,
            }

    else:
        result = {"error": f"알 수 없는 mode: {signal_mode}. short_sale/vi/program_trade/credit/lending 중 하나"}

    return result


async def handle_get_alpha_metrics(arguments: dict) -> dict | list:
    result = None
    # F/M/FCF Phase4: daily_snapshot 컬럼 직접 조회
    ticker = (arguments.get("ticker") or "").strip()
    if not ticker:
        result = {"error": "ticker 필수"}
    else:
        import sqlite3 as _sqlite3
        from db_collector import DB_PATH as _DB_PATH
        try:
            conn = _sqlite3.connect(_DB_PATH, timeout=10)
            conn.row_factory = _sqlite3.Row
            # 종목 기본정보
            master = conn.execute(
                "SELECT symbol, name, market FROM stock_master WHERE symbol = ?",
                (ticker,)
            ).fetchone()
            if not master:
                conn.close()
                result = {"error": f"stock_master에 종목 없음: {ticker}"}
            else:
                # 최신 trade_date의 알파 메트릭 + 재무 파생
                row = conn.execute(
                    "SELECT trade_date, fscore, mscore, "
                    "       fcf_to_assets, fcf_yield_ev, fcf_conversion, "
                    "       net_income, total_assets "
                    "FROM daily_snapshot "
                    "WHERE symbol = ? "
                    "ORDER BY trade_date DESC LIMIT 1",
                    (ticker,)
                ).fetchone()
                # 재무 분기 (period)
                fq = conn.execute(
                    "SELECT report_period FROM financial_quarterly "
                    "WHERE symbol = ? "
                    "ORDER BY report_period DESC LIMIT 1",
                    (ticker,)
                ).fetchone()
                conn.close()

                if not row:
                    result = {
                        "ticker": ticker,
                        "name":   master["name"],
                        "error":  "daily_snapshot에 해당 종목 데이터 없음 — 수집 대기",
                    }
                elif (row["fscore"] is None and row["mscore"] is None
                      and row["fcf_to_assets"] is None
                      and row["fcf_yield_ev"] is None
                      and row["fcf_conversion"] is None):
                    result = {
                        "ticker":     ticker,
                        "name":       master["name"],
                        "trade_date": row["trade_date"],
                        "error":      "데이터 수집 전 — Phase 3.5 대기",
                    }
                else:
                    # F-Score 해석
                    fscore_val = row["fscore"]
                    if fscore_val is None:
                        fscore_interp = "데이터 없음"
                    elif fscore_val >= 7:
                        fscore_interp = "우량"
                    elif fscore_val >= 4:
                        fscore_interp = "중립"
                    else:
                        fscore_interp = "부실"

                    # M-Score 해석
                    mscore_val = row["mscore"]
                    if mscore_val is None:
                        mscore_risk = "unknown"
                        mscore_interp = "데이터 없음"
                    elif mscore_val <= -2.22:
                        mscore_risk = "low"
                        mscore_interp = "조작 의심 없음"
                    elif mscore_val <= -1.78:
                        mscore_risk = "medium"
                        mscore_interp = "주의"
                    else:
                        mscore_risk = "high"
                        mscore_interp = "조작 의심"

                    # FCF 해석 (conversion 기준)
                    fcf_conv = row["fcf_conversion"]
                    if fcf_conv is None:
                        fcf_interp = "데이터 없음"
                    elif fcf_conv >= 80:
                        fcf_interp = "현금창출 양호"
                    elif fcf_conv >= 50:
                        fcf_interp = "보통"
                    else:
                        fcf_interp = "우려"

                    # FCF TTM 추정 (fcf_to_assets × total_assets / 100)
                    fcf_ttm = None
                    try:
                        if (row["fcf_to_assets"] is not None
                                and row["total_assets"] is not None):
                            fcf_ttm = int(row["fcf_to_assets"] *
                                          row["total_assets"] / 100.0)
                    except Exception:
                        fcf_ttm = None

                    is_complete = (
                        row["fscore"] is not None
                        and row["mscore"] is not None
                        and row["fcf_yield_ev"] is not None
                    )

                    result = {
                        "ticker":     ticker,
                        "name":       master["name"],
                        "market":     master["market"],
                        "trade_date": row["trade_date"],
                        "period":     fq["report_period"] if fq else None,
                        "fscore": {
                            "score":          fscore_val,
                            "interpretation": fscore_interp,
                        },
                        "mscore": {
                            "value":          mscore_val,
                            "risk":           mscore_risk,
                            "interpretation": mscore_interp,
                        },
                        "fcf": {
                            "fcf_ttm_won":        fcf_ttm,
                            "fcf_to_assets_pct":  row["fcf_to_assets"],
                            "fcf_yield_ev_pct":   row["fcf_yield_ev"],
                            "fcf_conversion_pct": fcf_conv,
                            "interpretation":     fcf_interp,
                        },
                        "is_complete": is_complete,
                    }
        except Exception as e:
            result = {"error": f"get_alpha_metrics 조회 실패: {e}"}

    return result


