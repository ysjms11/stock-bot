# mcp_tools/tools/macro.py — get_macro, get_polymarket, get_macro_external
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
    _scan_conv_one, _scan_op_one, _scan_turnaround_one,
    _scan_dart_op_one, _scan_dart_turnaround_one,
    _load_dart_screener_cache, _save_dart_screener_cache,
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


_TREND_PRIORITY = {"연속증가": 0, "흑자전환": 1, "감소": 2, "적자전환": 3, "적자지속": 4}

async def handle_get_macro(arguments: dict, token=None) -> dict | list:
    result = None
    mode = arguments.get("mode", "").strip().lower()
    print(f"[get_macro] mode={repr(mode)}")
    if mode == "dashboard":
        # ── 전체 매크로 대시보드 ──
        try:
            data = await collect_macro_data()
            result = {
                "data":    data,
                "message": format_macro_msg(data),
                "regime":  judge_regime(data),
            }
        except Exception as _me:
            _tb = traceback.format_exc()
            print(f"[get_macro/dashboard] 에러: {_me}\n{_tb}")
            result = {"error": str(_me), "mode": "dashboard", "traceback": _tb}
    elif mode == "sector_etf":
        # ── 섹터 ETF 시세 ── (kis_stock_price 사용: ETF 코드도 일반 주식 API로 조회 가능)
        SECTOR_ETFS = [
            ("140710", "KODEX 조선"),
            ("464520", "TIGER 방산"),
            ("305720", "KODEX 2차전지"),
            ("469150", "TIGER AI반도체"),
            ("244580", "KODEX 바이오"),
            ("261070", "KODEX 전력에너지"),
            ("069500", "KODEX 200"),
            ("252670", "KODEX 200선물인버스2X"),
        ]
        etf_results = []
        for etf_code, etf_name in SECTOR_ETFS:
            try:
                d = await kis_stock_price(etf_code, token)
                etf_results.append({
                    "code": etf_code, "name": etf_name,
                    "price": d.get("stck_prpr"),
                    "chg_pct": d.get("prdy_ctrt"),
                    "volume": d.get("acml_vol"),
                })
                await asyncio.sleep(0.05)
            except Exception:
                pass
        result = {"etfs": etf_results}
    elif mode in ("convergence", "convergence2"):
        # ── 이평선 수렴 스크리너 ──
        try:
            spread_threshold = float(arguments.get("spread", 5.0))
            sort_by = arguments.get("sort", "spread").strip().lower()
            if sort_by not in ("spread", "disp_20", "disp_60"):
                sort_by = "spread"
            # market 파라미터: convergence2는 'kosdaq'으로 고정
            if mode == "convergence2":
                market = "kosdaq"
            else:
                market = arguments.get("market", "all").strip().lower()
                if market not in ("kospi", "kosdaq", "all"):
                    market = "all"
            universe = get_stock_universe()
            if not universe:
                result = {"error": "stock_universe.json 로드 실패 — 파일 없음"}
            else:
                all_codes = list(universe.items())
                half = len(all_codes) // 2 + len(all_codes) % 2  # 110 for 221
                kospi_codes  = all_codes[:half]
                kosdaq_codes = all_codes[half:]
                if market == "kospi":
                    codes = kospi_codes
                elif market == "kosdaq":
                    codes = kosdaq_codes
                else:  # all
                    codes = all_codes
                print(f"[{mode}] {len(codes)}종목 병렬 스캔 시작 (market={market}, spread≤{spread_threshold}%, sort={sort_by})")
                sem_c = asyncio.Semaphore(10)
                items = await asyncio.gather(
                    *[_scan_conv_one(t, n, token, sem_c, spread_threshold) for t, n in codes]
                )
                conv_results = [x for x in items if x]
                if sort_by == "disp_20":
                    conv_results.sort(key=lambda x: abs(x["disp_20"]))
                elif sort_by == "disp_60":
                    conv_results.sort(key=lambda x: abs(x["disp_60"]))
                else:
                    conv_results.sort(key=lambda x: x["spread"])
                print(f"[{mode}] 완료: {len(conv_results)}개 수렴 종목")
                result = {
                    "mode": mode,
                    "market": market,
                    "spread_threshold": spread_threshold,
                    "sort": sort_by,
                    "count": len(conv_results),
                    "results": conv_results,
                }
        except Exception as _ce:
            _tb = traceback.format_exc()
            print(f"[get_macro/{mode}] 에러: {_ce}\n{_tb}")
            result = {"error": str(_ce), "mode": mode, "traceback": _tb}

    elif mode == "op_growth":
        # ── 영업이익 증가율 스크리너 (병렬 스캔) ──
        try:
            min_growth = float(arguments.get("min_growth", 50))
            sort_by    = arguments.get("sort", "yoy")
            universe = get_stock_universe()
            if not universe:
                result = {"error": "stock_universe.json 로드 실패 — 파일 없음"}
            else:
                codes = list(universe.items())
                print(f"[op_growth] {len(codes)}종목 병렬 스캔 시작 (최소 증가율: {min_growth}%)")
                sem_o = asyncio.Semaphore(5)
                items = await asyncio.gather(
                    *[_scan_op_one(t, n, token, sem_o, min_growth) for t, n in codes]
                )
                filtered = [x for x in items if x]
                if sort_by == "qoq":
                    op_results = sorted(filtered, key=lambda x: x.get("qoq_growth") if x.get("qoq_growth") is not None else -9999, reverse=True)
                elif sort_by == "trend":
                    op_results = sorted(filtered, key=lambda x: _TREND_PRIORITY.get(x.get("op_trend", ""), 9))
                else:  # yoy (default)
                    op_results = sorted(filtered, key=lambda x: x["growth_pct"], reverse=True)
                print(f"[op_growth] 완료: {len(op_results)}개 기준충족 종목")
                result = {
                    "mode": "op_growth",
                    "min_growth": min_growth,
                    "sort": sort_by,
                    "count": len(op_results),
                    "results": op_results,
                }
        except Exception as _oe:
            _tb = traceback.format_exc()
            print(f"[get_macro/op_growth] 에러: {_oe}\n{_tb}")
            result = {"error": str(_oe), "mode": "op_growth", "traceback": _tb}

    elif mode == "op_turnaround":
        # ── 영업이익 적자→흑자 전환 스크리너 ──
        try:
            sort_by = arguments.get("sort", "yoy")
            universe = get_stock_universe()
            if not universe:
                result = {"error": "stock_universe.json 로드 실패 — 파일 없음"}
            else:
                codes = list(universe.items())
                print(f"[op_turnaround] {len(codes)}종목 병렬 스캔 시작")
                sem_t = asyncio.Semaphore(5)
                items = await asyncio.gather(
                    *[_scan_turnaround_one(t, n, token, sem_t) for t, n in codes]
                )
                filtered = [x for x in items if x]
                if sort_by == "qoq":
                    ta_results = sorted(filtered, key=lambda x: x.get("qoq_growth") if x.get("qoq_growth") is not None else -9999, reverse=True)
                elif sort_by == "trend":
                    ta_results = sorted(filtered, key=lambda x: _TREND_PRIORITY.get(x.get("op_trend", ""), 9))
                else:  # yoy / default: 흑자전환이라 모두 op_recent 기준
                    ta_results = sorted(filtered, key=lambda x: x["op_recent"], reverse=True)
                print(f"[op_turnaround] 완료: {len(ta_results)}개 전환 종목")
                result = {
                    "mode": "op_turnaround",
                    "sort": sort_by,
                    "count": len(ta_results),
                    "results": ta_results,
                }
        except Exception as _te:
            _tb = traceback.format_exc()
            print(f"[get_macro/op_turnaround] 에러: {_te}\n{_tb}")
            result = {"error": str(_te), "mode": "op_turnaround", "traceback": _tb}

    elif mode in ("dart_op_growth", "dart_turnaround"):
        # ── DART 기반 연간 영업이익 스크리너 ──
        try:
            universe = get_stock_universe()
            if not universe:
                result = {"error": "stock_universe.json 로드 실패"}
            else:
                from kis_api import DART_API_KEY as _DART_KEY
                print(f"[{mode}] DART_API_KEY 설정 여부: {bool(_DART_KEY)}")
                corp_map = await get_dart_corp_map(universe)
                print(f"[{mode}] corp_map size: {len(corp_map)}")
                if not corp_map:
                    result = {"error": "dart_corp_map 로드 실패",
                              "hint": "DART_API_KEY 미설정 또는 corpCode.xml 다운로드 실패. Railway 로그 확인."}
                else:
                    now = datetime.now()
                    # 사업보고서 제출 마감: 3월 말. 4월 이후부터 전년도 데이터 안정적.
                    if now.month <= 3:
                        recent_year = now.year - 2  # 3월 이전: 2년 전 사업보고서 비교
                    else:
                        recent_year = now.year - 1  # 4월~: 전년도 사업보고서 비교
                    print(f"[{mode}] recent_year={recent_year} (month={now.month})")
                    codes = [(t, n, corp_map[t]) for t, n in universe.items() if t in corp_map]
                    # semaphore(15): 5→15로 확대, sleep 제거 → 첫 실행 속도 3배 향상
                    sem_d = asyncio.Semaphore(15)
                    sort_by = arguments.get("sort", "yoy")
                    if mode == "dart_op_growth":
                        min_growth = float(arguments.get("min_growth", 50))
                        # 당일 캐시 확인 (min_growth 포함해서 캐시 키 구성)
                        _ckey = f"dart_op_growth_{int(min_growth)}_{recent_year}"
                        cached = _load_dart_screener_cache(mode, _ckey)
                        if cached:
                            _raw_results = cached.get("results", [])
                        else:
                            print(f"[dart_op_growth] {len(codes)}종목 스캔 (최소 성장률: {min_growth}%)")
                            items = await asyncio.gather(
                                *[_scan_dart_op_one(t, n, c, sem_d, min_growth, recent_year, token) for t, n, c in codes]
                            )
                            _raw_results = [x for x in items if x]
                            _cache_result = {"mode": "dart_op_growth", "count": len(_raw_results), "results": sorted(_raw_results, key=lambda x: x["growth_pct"], reverse=True)}
                            _save_dart_screener_cache(_ckey, _cache_result)
                        # sort 적용 (캐시 히트 후에도 적용)
                        if sort_by == "qoq":
                            _sorted = sorted(_raw_results, key=lambda x: x.get("qoq_growth") if x.get("qoq_growth") is not None else -9999, reverse=True)
                        elif sort_by == "trend":
                            _sorted = sorted(_raw_results, key=lambda x: _TREND_PRIORITY.get(x.get("op_trend", ""), 9))
                        else:
                            _sorted = sorted(_raw_results, key=lambda x: x["growth_pct"], reverse=True)
                        result = {"mode": "dart_op_growth", "sort": sort_by, "count": len(_sorted), "results": _sorted}
                    else:  # dart_turnaround
                        _ckey = f"dart_turnaround_{recent_year}"
                        cached = _load_dart_screener_cache(mode, _ckey)
                        if cached:
                            _raw_results = cached.get("results", [])
                        else:
                            print(f"[dart_turnaround] {len(codes)}종목 스캔")
                            items = await asyncio.gather(
                                *[_scan_dart_turnaround_one(t, n, c, sem_d, recent_year, token) for t, n, c in codes]
                            )
                            _raw_results = [x for x in items if x]
                            _cache_result = {"mode": "dart_turnaround", "count": len(_raw_results), "results": sorted(_raw_results, key=lambda x: x["op_recent"], reverse=True)}
                            _save_dart_screener_cache(_ckey, _cache_result)
                        # sort 적용
                        if sort_by == "qoq":
                            _sorted = sorted(_raw_results, key=lambda x: x.get("qoq_growth") if x.get("qoq_growth") is not None else -9999, reverse=True)
                        elif sort_by == "trend":
                            _sorted = sorted(_raw_results, key=lambda x: _TREND_PRIORITY.get(x.get("op_trend", ""), 9))
                        else:
                            _sorted = sorted(_raw_results, key=lambda x: x["op_recent"], reverse=True)
                        result = {"mode": "dart_turnaround", "sort": sort_by, "count": len(_sorted), "results": _sorted}
        except Exception as _de:
            _tb = traceback.format_exc()
            print(f"[get_macro/{mode}] 에러: {_de}\n{_tb}")
            result = {"error": str(_de), "mode": mode, "traceback": _tb}

    elif mode == "us_sector":
        # ── 미국 섹터 ETF 등락률 ──
        loop = asyncio.get_running_loop()
        etfs = await loop.run_in_executor(None, fetch_us_sector_etf)
        if not etfs:
            result = {"error": "미국 섹터 ETF 데이터 조회 실패 (yfinance)"}
        else:
            sorted_etfs = sorted(etfs, key=lambda x: x["chg_1d"], reverse=True)
            result = {
                "mode": "us_sector",
                "count": len(sorted_etfs),
                "top3": sorted_etfs[:3],
                "bottom3": sorted_etfs[-3:][::-1],
                "all": sorted_etfs,
            }

    else:
        # ── 기본 모드: KOSPI/KOSDAQ/환율 ──
        kospi  = await get_kis_index(token, "0001")
        kosdaq = await get_kis_index(token, "1001")
        usd    = await get_yahoo_quote("USDKRW=X")
        result = {
            "kospi":  {"index": kospi.get("bstp_nmix_prpr"),  "chg": kospi.get("bstp_nmix_prdy_ctrt")},
            "kosdaq": {"index": kosdaq.get("bstp_nmix_prpr"), "chg": kosdaq.get("bstp_nmix_prdy_ctrt")},
            "usd_krw": {"price": usd.get("price") if usd else None,
                        "chg_pct": usd.get("change_pct") if usd else None},
        }

    return result


async def handle_get_polymarket(arguments: dict) -> dict | list:
    result = None
    top = int(arguments.get("top") or 10)
    min_volume = float(arguments.get("min_volume") if arguments.get("min_volume") is not None else 500_000)
    query = (arguments.get("query") or "").strip()
    result = await fetch_polymarket(top=top, min_volume=min_volume, query=query)

    return result


async def handle_get_macro_external(arguments: dict) -> dict | list:
    result = None
    top_poly = int(arguments.get("top_polymarket") or 8)
    result = await fetch_external_macro_signals(top_polymarket=top_poly)

    return result


