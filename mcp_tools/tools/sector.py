# mcp_tools/tools/sector.py — get_sector
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


async def handle_get_sector(arguments: dict, token=None) -> dict | list:
    result = None
    sector_mode = arguments.get("mode", "flow").strip().lower()
    if not sector_mode:
        sector_mode = "flow"

    if sector_mode == "rotation":
        # ← 기존 get_sector_rotation 핸들러
        rot = await detect_sector_rotation(token)
        result = rot
    elif sector_mode == "flow":
        # ← 기존 get_sector_flow 핸들러 (mode="flow" 기본)
        now_kst = datetime.now(KST)
        today = now_kst.strftime("%Y%m%d")
        market_closed = now_kst.hour > 15 or (now_kst.hour == 15 and now_kst.minute >= 30)
        data_finalized = now_kst.hour >= 17 or (now_kst.hour == 16 and now_kst.minute >= 30)

        # ── 캐시 확인: 16:30 이후 확정 데이터 캐시만 사용 ──
        cache = load_sector_flow_cache()
        if data_finalized and cache.get("date") == today and "data" in cache:
            result = dict(cache["data"])
            result["cached"] = True
            result["cached_at"] = cache.get("cached_at", "")
        else:
            sectors = []
            for code, label in WI26_SECTORS:
                frgn, orgn = await _fetch_sector_flow(token, code)
                sectors.append({
                    "sector": label, "code": code,
                    "frgn": frgn, "orgn": orgn,
                    "total": frgn + orgn,
                })

            has_data = any(s["total"] != 0 for s in sectors)
            note = None

            if not has_data:
                # Fallback: 외국인 순매수 상위 기반 업종 근사치 (수량 기준)
                frgn_rows = await kis_foreigner_trend(token)
                sector_frgn = {label: 0 for _, label in WI26_SECTORS}
                for r in frgn_rows:
                    sect = _TICKER_SECTOR.get(r.get("mksc_shrn_iscd", ""))
                    if sect:
                        sector_frgn[sect] += int(r.get("frgn_ntby_qty", 0) or 0)
                sectors = [
                    {"sector": label, "code": code,
                     "frgn": sector_frgn.get(label, 0), "orgn": 0,
                     "total": sector_frgn.get(label, 0)}
                    for code, label in WI26_SECTORS
                ]
                note = ("장중 업종별 수급 데이터 미제공 — 외국인 순매수 상위 기반 근사치(수량). "
                       "ETF 시세로 섹터 동향을 확인하세요.")

            sorted_s = sorted(sectors, key=lambda x: x["total"], reverse=True)
            result = {
                "date": today,
                "top_inflow":  [{"sector": s["sector"], "frgn": s["frgn"], "orgn": s["orgn"]}
                                 for s in sorted_s[:3]],
                "top_outflow": [{"sector": s["sector"], "frgn": s["frgn"], "orgn": s["orgn"]}
                                 for s in sorted_s[-3:][::-1]],
                "all": [{"sector": s["sector"], "frgn": s["frgn"], "orgn": s["orgn"]}
                        for s in sorted_s],
            }
            if note:
                result["note"] = note

            # ── 섹터 ETF 시세 ──
            SECTOR_ETFS = [
                ("140710", "KODEX 조선"),
                ("464520", "TIGER 방산"),
                ("305720", "KODEX 2차전지"),
                ("469150", "TIGER AI반도체"),
                ("244580", "KODEX 바이오"),
                ("261070", "KODEX 전력에너지"),
            ]
            etf_prices = []
            for etf_code, etf_name in SECTOR_ETFS:
                try:
                    s = _get_session()
                    _, ed = await _kis_get(s, "/uapi/etfetn/v1/quotations/inquire-price",
                        "FHPST02400000", token,
                        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": etf_code})
                    out = ed.get("output", {})
                    etf_prices.append({
                        "code": etf_code, "name": etf_name,
                        "price": out.get("stck_prpr"), "chg": out.get("prdy_ctrt"),
                    })
                    await asyncio.sleep(0.05)
                except Exception:
                    pass
            result["etf_prices"] = etf_prices

            # ── 장마감 후 캐시 저장 (fallback 데이터는 캐시하지 않음) ──
            if data_finalized and has_data:
                save_sector_flow_cache({
                    "date": today,
                    "cached_at": now_kst.strftime("%H:%M:%S"),
                    "data": result,
                })
            result["cached"] = False

    else:
        result = {"error": f"알 수 없는 mode: {sector_mode}. flow/rotation 중 하나"}

    return result


