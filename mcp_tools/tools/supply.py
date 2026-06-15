# mcp_tools/tools/supply.py — get_supply, get_pension_flow
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


async def handle_get_supply(arguments: dict, token=None) -> dict | list:
    result = None
    supply_mode = arguments.get("mode", "daily").strip().lower()

    if supply_mode == "daily":
        # ← 기존 get_investor_flow 핸들러
        ticker = arguments.get("ticker", "").strip()
        if not ticker:
            result = {"error": "ticker는 필수입니다"}
        else:
            inv = await kis_investor_trend(ticker, token)
            if not inv:
                result = {"error": f"{ticker} 수급 데이터 없음"}
            else:
                row = inv[0]  # 가장 최근 영업일 (장중이면 당일 누적)
                # 장중 여부: 평일 09:00~15:30 KST
                now_kst = datetime.now(KST)
                wd = now_kst.weekday()
                tot_min = now_kst.hour * 60 + now_kst.minute
                is_live = (wd < 5 and 9 * 60 <= tot_min <= 15 * 60 + 30)
                result = {
                    "ticker": ticker,
                    "date": row.get("stck_bsop_date", ""),
                    "is_live": is_live,
                    "foreign":     {
                        "buy":  int(row.get("frgn_shnu_vol", 0) or 0),
                        "sell": int(row.get("frgn_seln_vol", 0) or 0),
                        "net":  int(row.get("frgn_ntby_qty", 0) or 0),
                    },
                    "institution": {
                        "buy":  int(row.get("orgn_shnu_vol", 0) or 0),
                        "sell": int(row.get("orgn_seln_vol", 0) or 0),
                        "net":  int(row.get("orgn_ntby_qty", 0) or 0),
                    },
                    "individual":  {
                        "buy":  int(row.get("prsn_shnu_vol", 0) or 0),
                        "sell": int(row.get("prsn_seln_vol", 0) or 0),
                        "net":  int(row.get("prsn_ntby_qty", 0) or 0),
                    },
                }

    elif supply_mode == "history":
        # ← 기존 get_investor_trend_history 핸들러
        ticker = arguments.get("ticker", "").strip()
        if not ticker:
            result = {"error": "ticker는 필수입니다"}
        else:
            days  = int(arguments.get("days", 5) or 5)
            days  = max(1, min(days, 30))
            rows  = await kis_investor_trend_history(ticker, token, n_days=days)
            result = {
                "ticker": ticker,
                "days":   days,
                "history": rows,
            }
            if not rows:
                result["note"] = ("수급 히스토리는 장 마감 후 데이터만 제공됩니다. "
                                  "장중에는 get_supply(mode='estimate')로 추정 수급을 확인하세요.")

    elif supply_mode == "estimate":
        # ← 기존 get_investor_estimate 핸들러
        ticker = arguments.get("ticker", "").strip()
        if not ticker:
            result = {"error": "ticker는 필수입니다"}
        else:
            result = await kis_investor_trend_estimate(ticker, token)

    elif supply_mode == "foreign_rank":
        # DB-first: daily_snapshot 최신 trade_date 외국인 순매수금액 랭킹
        sort = arguments.get("sort", "buy").strip().lower()
        n = int(arguments.get("n", 20) or 20)
        n = max(1, min(n, 50))
        import sqlite3 as _sqlite3
        db_path = f"{_DATA_DIR}/stock.db"
        db_rows = []
        db_trade_date = None
        try:
            _conn = _sqlite3.connect(db_path, timeout=10)
            try:
                db_trade_date = _conn.execute(
                    "SELECT MAX(trade_date) FROM daily_snapshot"
                ).fetchone()[0]
                if db_trade_date:
                    order = "DESC" if sort == "buy" else "ASC"
                    cond = "d.foreign_net_amt > 0" if sort == "buy" else "d.foreign_net_amt < 0"
                    cur = _conn.execute(f"""
                        SELECT d.symbol, COALESCE(m.name, ''), d.foreign_net_amt,
                               d.foreign_net_qty, d.close, d.change_pct, d.market_cap
                        FROM daily_snapshot d
                        LEFT JOIN stock_master m ON d.symbol = m.symbol
                        WHERE d.trade_date = ? AND {cond}
                        ORDER BY d.foreign_net_amt {order}
                        LIMIT ?
                    """, (db_trade_date, n))
                    for sym, name, amt, qty, close, chg, mcap in cur.fetchall():
                        db_rows.append({
                            "ticker": sym,
                            "name": name,
                            "foreign_net_amt": amt,
                            "foreign_net_qty": qty,
                            "close": close,
                            "chg_pct": chg,
                            "market_cap_억": mcap or 0,
                        })
            finally:
                _conn.close()
        except Exception as _e:
            db_rows = []

        if db_rows:
            result = {
                "sort": sort,
                "count": len(db_rows),
                "source": "daily_snapshot DB",
                "trade_date": db_trade_date,
                "items": db_rows,
            }
        else:
            # live KIS 폴백 (드문 경우: 당일 DB 미수집)
            try:
                kis_rows = await kis_foreigner_trend(token)
                if kis_rows:
                    result = {
                        "sort": sort,
                        "count": len(kis_rows[:n]),
                        "source": "KIS live",
                        "items": [
                            {
                                "ticker": r.get("mksc_shrn_iscd", ""),
                                "name": r.get("hts_kor_isnm", ""),
                                "foreign_net_amt": None,
                                "foreign_net_qty": int(r.get("frgn_ntby_qty", 0) or 0),
                            }
                            for r in kis_rows[:n]
                        ],
                    }
                else:
                    result = {
                        "items": [],
                        "source": "none",
                        "note": ("daily_snapshot DB에 데이터 없음. "
                                 "KIS live도 미제공 (장중). "
                                 "18:30 이후 재조회하거나 get_supply(mode='combined_rank') 사용."),
                    }
            except Exception as _e2:
                result = {
                    "items": [],
                    "source": "none",
                    "note": f"DB 없음, KIS 폴백 실패: {_e2}",
                }

    elif supply_mode == "combined_rank":
        # ← 기존 get_foreign_institution 핸들러
        sort = arguments.get("sort", "buy").strip().lower()
        n    = int(arguments.get("n", 20) or 20)
        n    = max(1, min(n, 50))
        items = await kis_foreign_institution_total(token, sort=sort, n=n)
        # KRX DB에서 시총 대비 비율 보강
        db = load_krx_db()
        if db and db.get("stocks"):
            db_stocks = db["stocks"]
            for item in items:
                ticker = item.get("ticker", "")
                s = db_stocks.get(ticker)
                mcap = s.get("market_cap", 0) if s else 0
                if mcap > 0:
                    # fi_total_net(수량) × price → 대략적 금액 추정
                    net_qty = item.get("fi_total_net", 0)
                    price = item.get("price", 0)
                    est_amt = net_qty * price
                    item["fi_ratio_pct"] = round(est_amt / mcap * 100, 4)
                    item["market_cap_억"] = round(mcap / 1_0000_0000)
        result = {
            "sort":  sort,
            "count": len(items),
            "items": items,
        }
        if not db:
            result["note"] = "시총 대비 비율은 KRX DB 갱신 후 사용 가능 (fi_ratio_pct 필드)"

    elif supply_mode == "broker_rank":
        sort = arguments.get("sort", "buy").strip().lower()
        broker = arguments.get("broker", "").strip()
        n = max(1, min(int(arguments.get("n", 20) or 20), 50))
        market_code = {"all": "0000", "kospi": "0001", "kosdaq": "1001"}.get(
            arguments.get("market", "all").strip().lower(), "0000")
        items = await kis_traded_by_company(token, broker=broker, sort=sort,
                                             market=market_code, n=n)
        result = {
            "sort": "매수상위" if sort == "buy" else "매도상위",
            "broker": broker or "전체",
            "count": len(items),
            "stocks": items,
        }

    else:
        result = {"error": f"알 수 없는 mode: {supply_mode}. daily/history/estimate/foreign_rank/combined_rank/broker_rank 중 하나"}

    return result


async def handle_get_pension_flow(arguments: dict) -> dict | list:
    result = None
    days = int(arguments.get("days") or 5)
    market = (arguments.get("market") or "ALL").upper()
    top = int(arguments.get("top") or 30)
    held_only = bool(arguments.get("held_watch_only", False))
    # pykrx 동기 함수 → executor에서 실행
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: fetch_pension_fund_flow(days=days, market=market,
                                         top=top, held_watch_only=held_only),
    )

    return result


