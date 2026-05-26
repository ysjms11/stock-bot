# mcp_tools/tools/dart.py — get_dart
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
from mcp_tools._helpers import _dart_tag

try:
    from report_crawler import (
        collect_reports, get_collection_tickers,
        DB_PATH as REPORT_DB_PATH,
    )
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False
    REPORT_DB_PATH = ""


async def handle_get_dart(arguments: dict) -> dict | list:
    result = None
    dart_mode = arguments.get("mode", "").strip().lower()

    if dart_mode == "report_list":
        _filter_ticker = (arguments.get("ticker") or "").strip()
        raw = list_dart_reports()
        if _filter_ticker:
            raw["files"] = [f for f in raw.get("files", []) if f.get("ticker") == _filter_ticker]
            raw["total"] = len(raw["files"])
        result = raw

    elif dart_mode == "read":
        target_ticker = (arguments.get("ticker") or "").strip()
        if not target_ticker:
            result = {"error": "ticker를 지정하세요. 예: get_dart(mode='read', ticker='092780')"}
        else:
            result = read_dart_report(target_ticker)

    elif dart_mode == "report":
        target_ticker = arguments.get("ticker", "").strip()
        report_error = None
        # 종목 목록: 포트폴리오 + 워치리스트 + 매수감시
        tickers = {}  # {ticker: name}
        pf = load_json(PORTFOLIO_FILE, {})
        for t, v in pf.items():
            if t != "us_stocks" and not _is_us_ticker(t) and isinstance(v, dict):
                tickers[t] = v.get("name", t)
        wl = load_watchlist()
        for t, n in wl.items():
            if not _is_us_ticker(t):
                tickers[t] = n
        wa = load_watchalert()
        for t, v in wa.items():
            if not _is_us_ticker(t) and isinstance(v, dict):
                tickers[t] = v.get("name", t)

        if target_ticker:
            if _is_us_ticker(target_ticker):
                report_error = "미국 종목은 지원하지 않습니다."
            elif target_ticker in tickers:
                tickers = {target_ticker: tickers[target_ticker]}
            else:
                tickers = {target_ticker: target_ticker}

        if report_error:
            result = {"error": report_error}
        elif not tickers:
            result = {"error": "대상 종목이 없습니다. 포트폴리오 또는 워치리스트에 종목을 추가하세요."}
        else:
            # corp_code 매핑 로드
            corp_codes = await load_corp_codes()
            if not corp_codes:
                result = {"error": "corp_codes 매핑을 가져올 수 없습니다. DART_API_KEY를 확인하세요."}
            else:
                saved = []
                skipped_no_report = []
                failed = []
                for ticker, name in tickers.items():
                    try:
                        cc_info = corp_codes.get(ticker)
                        if not cc_info:
                            print(f"[DART report] {ticker} corp_code 없음, 스킵")
                            skipped_no_report.append({"ticker": ticker, "name": name, "reason": "corp_code 없음"})
                            continue
                        corp_code = cc_info["corp_code"] if isinstance(cc_info, dict) else cc_info
                        corp_name = cc_info.get("corp_name", name) if isinstance(cc_info, dict) else name
                        print(f"[DART report] {ticker} ({corp_name}) → corp_code={corp_code}")

                        reports = await search_dart_reports(corp_code)
                        if not reports:
                            print(f"[DART report] {ticker} ({corp_name}) 사업보고서 없음")
                            skipped_no_report.append({"ticker": ticker, "name": corp_name,
                                                      "reason": "사업보고서 없음", "corp_code": corp_code})
                            await asyncio.sleep(0.5)
                            continue

                        # 우선순위 순으로 최대 3건 시도 (원본 > 정정 > 첨부정정)
                        got_saved = False
                        tried = []
                        for rpt in reports[:3]:
                            rcept_no = rpt.get("rcept_no", "")
                            rpt_date = rpt.get("rcept_dt", "")
                            rpt_title = rpt.get("report_nm", "")
                            tried.append({"rcept_no": rcept_no, "title": rpt_title})
                            print(f"[DART report] {ticker} 시도: {rpt_title} ({rpt_date}) rcept={rcept_no}")
                            res = await save_dart_report(ticker, corp_name, rcept_no, rpt_date)
                            if res:
                                saved.append(res)
                                got_saved = True
                                break
                            print(f"[DART report] {ticker} rcept={rcept_no} 실패, 다음 시도...")
                            await asyncio.sleep(0.5)
                        if not got_saved:
                            best_rcept = tried[0]["rcept_no"] if tried else ""
                            dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={best_rcept}" if best_rcept else ""
                            failed.append({
                                "ticker": ticker, "name": corp_name,
                                "tried": tried,
                                "dart_url": dart_url,
                                "reason": "PDF 전용 보고서 — document.xml API 미지원. DART 사이트에서 PDF로 확인 가능.",
                                "hint": "PDF를 다운로드해서 Claude에게 직접 전달하면 분석 가능합니다.",
                            })
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        print(f"[DART report] {ticker} 처리 중 예외: {e}")
                        failed.append({"ticker": ticker, "name": name,
                                       "reason": f"예외: {str(e)[:200]}"})

                result = {
                    "saved": saved,
                    "skipped": skipped_no_report,
                    "failed": failed,
                    "total_saved": len(saved),
                    "total_skipped": len(skipped_no_report),
                    "total_failed": len(failed),
                }

    elif dart_mode == "disclosure_list":
        target_ticker = (arguments.get("ticker") or "").strip()
        if not target_ticker:
            result = {"error": "ticker를 지정하세요. 예: get_dart(mode='disclosure_list', ticker='064350', days=3)"}
        elif _is_us_ticker(target_ticker):
            result = {"error": "수시공시는 한국 종목만 지원합니다."}
        else:
            days = int(arguments.get("days", 7) or 7)
            disclosures = await list_disclosures_for_ticker(target_ticker, days)
            result = {
                "ticker": target_ticker,
                "days": days,
                "count": len(disclosures),
                "disclosures": disclosures,
            }

    elif dart_mode == "disclosure_read":
        target_ticker = (arguments.get("ticker") or "").strip()
        rcept_no = (arguments.get("rcept_no") or "").strip()
        if not target_ticker or not rcept_no:
            result = {"error": "ticker와 rcept_no를 모두 지정하세요. 예: get_dart(mode='disclosure_read', ticker='064350', rcept_no='20260424000123')"}
        else:
            body = await fetch_and_cache_disclosure(target_ticker, rcept_no)
            MAX_BYTES = 50_000
            body_bytes = body.encode("utf-8") if body else b""
            truncated = len(body_bytes) > MAX_BYTES
            if truncated:
                body = body_bytes[:MAX_BYTES].decode("utf-8", errors="ignore")
            result = {
                "ticker": target_ticker,
                "rcept_no": rcept_no,
                "body": body,
                "truncated": truncated,
                "bytes": len(body_bytes),
            }

    elif dart_mode == "insider":
        target_ticker = (arguments.get("ticker") or "").strip()
        days = int(arguments.get("days", 30) or 30)
        if not target_ticker:
            result = {"error": "ticker를 지정하세요. 예: get_dart(mode='insider', ticker='005930')"}
        elif _is_us_ticker(target_ticker):
            result = {"error": "내부자 거래는 한국 종목만 지원합니다."}
        else:
            # DB에 데이터 없으면 실시간 수집
            universe = get_stock_universe() or {}
            corp_map = await get_dart_corp_map(universe) if universe else {}
            corp_code = corp_map.get(target_ticker, "")
            fetched_new = 0
            if corp_code:
                records = await kis_elestock(corp_code)
                fetched_new = upsert_insider_transactions(target_ticker, corp_code, records)
            agg = aggregate_insider_cluster(target_ticker, days=days)
            agg["fetched_new"] = fetched_new
            agg["cluster_flag"] = agg["buyers"] >= 3 and agg["buy_qty"] > agg["sell_qty"]
            # recent는 최대 20건으로 제한
            agg["recent"] = agg["recent"][:20]
            result = agg

    else:
        # 기존 동작: 워치리스트 최근 3일 공시
        disclosures = await search_dart_disclosures(days_back=3)
        wl = load_watchlist()
        important = filter_important_disclosures(disclosures, list(wl.values()))
        result = []
        for d in important[:10]:
            title = d.get("report_nm", "") or ""
            tag = _dart_tag(title)
            tagged_title = f"[{tag}] {title}" if tag != "일반" else title
            result.append({
                "corp": d.get("corp_name", ""),
                "title": tagged_title,
                "date": d.get("rcept_dt", ""),
                "importance": tag,
            })

    return result


