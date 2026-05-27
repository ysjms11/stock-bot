# mcp_tools/tools/scan.py — get_scan, get_change_scan, get_finance_rank, get_highlow, get_broker
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


_TREND_PRIORITY = {"연속증가": 0, "흑자전환": 1, "감소": 2, "적자전환": 3, "적자지속": 4}

async def handle_get_scan(arguments: dict) -> dict | list:
    result = None
    scan_date = (arguments.get("date") or "").strip() or None
    db = load_krx_db(scan_date)
    if not db:
        result = {"error": "KRX DB 없음. 장 마감 후 자동 갱신되거나, 수동 크롤링이 필요합니다."}
    else:
        preset = (arguments.get("preset") or "").strip() or None
        filters = {}
        for key in ["market_cap_min", "market_cap_max", "chg_pct_min", "chg_pct_max",
                    "foreign_ratio_min", "fi_ratio_min", "per_min", "per_max",
                    "pbr_max", "turnover_min"]:
            val = arguments.get(key)
            if val is not None:
                filters[key] = float(val)
        for key in ["sort", "market"]:
            val = arguments.get(key)
            if val:
                filters[key] = val
        val = arguments.get("n")
        if val is not None:
            filters["n"] = int(val)
        result = scan_stocks(db, filters, preset=preset)

    return result


async def handle_get_change_scan(arguments: dict) -> dict | list:
    result = None
    db = load_krx_db()
    if not db:
        result = {"error": "KRX DB 없음 (data/krx_db/ 비어있음)"}
    else:
        preset_str = (arguments.get("preset") or "").strip()
        n = max(1, min(int(arguments.get("n", 30) or 30), 100))
        market_filter = (arguments.get("market") or "all").strip().lower()
        sort_by = (arguments.get("sort") or "").strip()
        stocks = db.get("stocks", {})

        # 시장 필터
        if market_filter != "all":
            stocks = {t: s for t, s in stocks.items() if s.get("market") == market_filter}

        presets = [p.strip() for p in preset_str.split(",") if p.strip()] if preset_str else []
        matched = set(stocks.keys())
        preset_desc = []
        default_sort = "chg_pct"

        # 파라미터 (프리셋 임계값 오버라이드 가능)
        _t = lambda k, d: float(arguments.get(k, d) or d)

        for p in presets:
            if p == "ma_convergence":
                spread_max = _t("spread_max", 3)
                s_set = {t for t, s in stocks.items()
                         if s.get("ma_spread") is not None and abs(s["ma_spread"]) < spread_max
                         and s.get("ma_spread_change_30d") is not None and s["ma_spread_change_30d"] > 0}
                matched &= s_set
                preset_desc.append(f"MA수렴(spread<{spread_max}%+30d수렴)")
                default_sort = "ma_spread_change_30d"
            elif p == "volume_spike":
                ratio_min = _t("ratio_min", 2.0)
                s_set = {t for t, s in stocks.items()
                         if s.get("volume_ratio_10d") is not None and s["volume_ratio_10d"] > ratio_min}
                matched &= s_set
                preset_desc.append(f"거래량급증(10d>{ratio_min}x)")
                default_sort = "volume_ratio_10d"
            elif p == "earnings_disconnect":
                gap_min = _t("gap_min", 30)
                s_set = {t for t, s in stocks.items()
                         if s.get("earnings_gap") is not None and s["earnings_gap"] > gap_min}
                matched &= s_set
                preset_desc.append(f"실적괴리(gap>{gap_min})")
                default_sort = "earnings_gap"
            elif p == "consensus_undervalued":
                gap_min = _t("gap_min", 40)
                s_set = {t for t, s in stocks.items()
                         if s.get("consensus_gap", 0) > gap_min and s.get("consensus_count", 0) >= 3}
                matched &= s_set
                preset_desc.append(f"컨센서스저평가(gap>{gap_min}%)")
                default_sort = "consensus_gap"
            elif p == "oversold_bounce":
                rsi_max = _t("rsi_max", 30)
                s_set = {t for t, s in stocks.items()
                         if s.get("rsi14") is not None and s["rsi14"] < rsi_max}
                matched &= s_set
                preset_desc.append(f"과매도(RSI<{rsi_max})")
                default_sort = "rsi14"
            elif p == "vp_support":
                position_max = _t("position_max", 0.2)
                s_set = {t for t, s in stocks.items()
                         if s.get("vp_position") is not None and s["vp_position"] < position_max}
                matched &= s_set
                preset_desc.append(f"매물대지지(VP<{position_max})")
                default_sort = "vp_position"
            elif p == "golden_cross":
                s_set = set()
                import numpy as _np
                hist, hdates = _load_history(db["date"], 25)
                for t, s in stocks.items():
                    _m5 = s.get("ma5")
                    _m20 = s.get("ma20")
                    if _m5 is None or _m20 is None or _m5 <= _m20:
                        continue
                    hc = hist.get(t, {}).get("close", [])
                    if len(hc) >= 21:
                        pm5 = float(_np.mean(hc[1:6]))
                        pm20 = float(_np.mean(hc[1:21]))
                        if pm5 < pm20:
                            s_set.add(t)
                matched &= s_set
                preset_desc.append("골든크로스(MA5>MA20전환)")
                default_sort = "ma_spread"
            elif p == "sector_leader":
                strength_min = _t("strength_min", 5)
                s_set = {t for t, s in stocks.items()
                         if s.get("sector_rel_strength") is not None and s["sector_rel_strength"] > strength_min}
                matched &= s_set
                preset_desc.append(f"섹터선도(상대강도>{strength_min}%)")
                default_sort = "sector_rel_strength"
            elif p == "w52_breakout":
                position_min = _t("position_min", 0.95)
                s_set = {t for t, s in stocks.items()
                         if s.get("w52_position") is not None and s["w52_position"] > position_min}
                matched &= s_set
                preset_desc.append(f"52주신고가근접(>{position_min*100:.0f}%)")
                default_sort = "w52_position"

            elif p == "short_squeeze":
                change_max = _t("change_max", -30)
                s_set = {t for t, s in stocks.items()
                         if s.get("short_change_20d") is not None and s["short_change_20d"] < change_max}
                matched &= s_set
                preset_desc.append(f"숏스퀴즈(공매도20d<{change_max}%)")
                default_sort = "short_change_20d"
            elif p == "credit_unwind":
                s_set = set()
                hist_c, _ = _load_history(db["date"], 6)
                for t, s in stocks.items():
                    ch = hist_c.get(t, {}).get("loan_balance_rate", [])
                    # ch는 최신→과거 순. 최근 5일 연속 감소(= i가 작을수록 작음) 판정
                    if len(ch) >= 5 and all(ch[i] < ch[i+1] for i in range(4) if ch[i+1] > 0):
                        s_set.add(t)
                        # credit_change_5d: 최근 - 5일전 (음수=감소)
                        s["credit_change_5d"] = round(ch[0] - ch[4], 4) if ch[4] else None
                matched &= s_set
                preset_desc.append("신용청산(5일연속감소)")
                default_sort = "credit_change_5d"
            elif p == "foreign_reversal":
                s_set = {t for t, s in stocks.items()
                         if s.get("foreign_trend_5d") is not None and s["foreign_trend_5d"] >= 0.6
                         and s.get("foreign_trend_20d") is not None and s["foreign_trend_20d"] < 0.4}
                matched &= s_set
                preset_desc.append("외인전환(5d매수+20d매도)")
                default_sort = "foreign_trend_5d"
            elif p == "foreign_accumulation":
                hold_min = _t("hold_min", 1.0)
                s_set = set()
                hist_f, _ = _load_history(db["date"], 6)
                for t, s in stocks.items():
                    fh = hist_f.get(t, {}).get("foreign_own_pct", [])
                    # fh는 최신→과거 순. 최근 - 5일전
                    if len(fh) >= 5:
                        delta = fh[0] - fh[4]
                        s["foreign_hold_change_5d"] = round(delta, 4)
                        if delta > hold_min:
                            s_set.add(t)
                matched &= s_set
                preset_desc.append(f"외인축적(보유+{hold_min}%p/5d)")
                default_sort = "foreign_hold_change_5d"
            elif p == "turnaround":
                # 적자→흑자 전환: 최신 영업이익>0 AND 직전 분기<=0
                import sqlite3 as _sql3
                from db_collector import DB_PATH as _DB_PATH
                s_set = set()
                _conn = _sql3.connect(_DB_PATH)
                try:
                    _rows = _conn.execute("""
                        SELECT symbol, report_period, operating_profit
                        FROM financial_quarterly
                        WHERE operating_profit IS NOT NULL
                        ORDER BY symbol, report_period DESC
                    """).fetchall()
                finally:
                    _conn.close()
                _by_sym = {}
                for sym, period, op in _rows:
                    _by_sym.setdefault(sym, []).append((period, op))
                for sym, arr in _by_sym.items():
                    if len(arr) < 2 or sym not in stocks:
                        continue
                    latest_op = arr[0][1]
                    prev_op = arr[1][1]
                    if latest_op is None or prev_op is None:
                        continue
                    # prev_op=0.0 은 데이터 누락 마커(4k+건) → 엄격한 음수(<0) 만 적자로 인정
                    if latest_op > 0 and prev_op < 0:
                        s = stocks[sym]
                        s["op_profit_latest"] = round(latest_op, 2)
                        s["op_profit_prev"] = round(prev_op, 2)
                        s["op_profit_delta"] = round(latest_op - prev_op, 2)
                        s_set.add(sym)
                matched &= s_set
                preset_desc.append("적자→흑자전환")
                default_sort = "op_profit_delta"
            elif p == "fscore_jump":
                # F-Score 2점+ 상승: 최신 vs ~90일 전
                # NOTE: Phase4(2026-04-17) 배포 후부터 기록. 히스토리 축적 전에는 결과 0.
                #       ~7/15 이후 정상 작동 예상 (90d 이상 누적 필요).
                import sqlite3 as _sql3
                from db_collector import DB_PATH as _DB_PATH
                s_set = set()
                _conn = _sql3.connect(_DB_PATH)
                try:
                    # fscore 있는 전 행을 한 번에 메모리로 (2485행 수준)
                    _all = _conn.execute(
                        "SELECT symbol, trade_date, fscore FROM daily_snapshot "
                        "WHERE fscore IS NOT NULL ORDER BY trade_date DESC"
                    ).fetchall()
                finally:
                    _conn.close()
                if _all:
                    _latest_dt = _all[0][1]
                    _ref_dt = (datetime.strptime(_latest_dt, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
                    _by_sym = {}
                    for sym, dt, f in _all:
                        _by_sym.setdefault(sym, []).append((dt, f))
                    for sym, arr in _by_sym.items():
                        if sym not in stocks:
                            continue
                        # arr은 trade_date DESC 정렬
                        f_now = arr[0][1]
                        f_past = None
                        for dt, f in arr:
                            if dt <= _ref_dt:
                                f_past = f
                                break
                        if f_past is None or f_now is None:
                            continue
                        delta = f_now - f_past
                        if delta >= 2:
                            s = stocks[sym]
                            s["fscore_now"] = f_now
                            s["fscore_past"] = f_past
                            s["fscore_delta"] = delta
                            s_set.add(sym)
                matched &= s_set
                preset_desc.append("F-Score도약(ΔF>=2)")
                default_sort = "fscore_delta"
            elif p == "insider_cluster_buy":
                # 내부자 군집매수: 30일 내 3명+ 보고 AND 순매수 > 0
                import sqlite3 as _sql3
                from db_collector import DB_PATH as _DB_PATH
                s_set = set()
                _conn = _sql3.connect(_DB_PATH)
                try:
                    _rows = _conn.execute("""
                        SELECT symbol, COUNT(DISTINCT repror) AS n_repror, SUM(stock_irds_cnt) AS net_qty
                        FROM insider_transactions
                        WHERE rcept_dt >= date('now', '-30 days')
                        GROUP BY symbol
                        HAVING n_repror >= 3 AND net_qty > 0
                    """).fetchall()
                finally:
                    _conn.close()
                for sym, n_rep, net_q in _rows:
                    if sym not in stocks:
                        continue
                    s = stocks[sym]
                    s["insider_reprors"] = int(n_rep or 0)
                    s["insider_net_qty"] = int(net_q or 0)
                    s_set.add(sym)
                matched &= s_set
                preset_desc.append("내부자군집매수(30d 3명+순매수)")
                default_sort = "insider_net_qty"

        if not presets:
            preset_desc.append("전체 (프리셋 미지정)")

        sort_field = sort_by or default_sort
        reverse = sort_field not in ("rsi14", "vp_position")
        results = []
        for t in matched:
            s = stocks[t]
            results.append({
                "ticker": t,
                "name": s.get("name", t),
                "market": s.get("market", ""),
                "close": s.get("close", 0),
                "chg_pct": s.get("chg_pct", 0),
                "market_cap": round(s.get("market_cap", 0) / 1_0000_0000) if s.get("market_cap", 0) else 0,
                "per": s.get("per"),
                "pbr": s.get("pbr"),
                "rsi14": s.get("rsi14"),
                "ma_spread": s.get("ma_spread"),
                "ma_spread_change_10d": s.get("ma_spread_change_10d"),
                "ma_spread_change_30d": s.get("ma_spread_change_30d"),
                "volume_ratio_5d": s.get("volume_ratio_5d"),
                "volume_ratio_10d": s.get("volume_ratio_10d"),
                "volume_ratio_20d": s.get("volume_ratio_20d"),
                "rsi_change_5d": s.get("rsi_change_5d"),
                "rsi_change_20d": s.get("rsi_change_20d"),
                "consensus_gap": s.get("consensus_gap"),
                "consensus_target": s.get("consensus_target"),
                "earnings_gap": s.get("earnings_gap"),
                "eps_change_90d": s.get("eps_change_90d"),
                "vp_position": s.get("vp_position"),
                "vp_position_60d": s.get("vp_position_60d"),
                "w52_position": s.get("w52_position"),
                "sector_rel_strength": s.get("sector_rel_strength"),
                "sector_rank": s.get("sector_rank"),
                "ytd_return": s.get("ytd_return"),
                "foreign_trend_5d": s.get("foreign_trend_5d"),
                "foreign_trend_20d": s.get("foreign_trend_20d"),
                "short_change_5d": s.get("short_change_5d"),
                "short_change_20d": s.get("short_change_20d"),
                "credit_change_5d": s.get("credit_change_5d"),
                "foreign_hold_change_5d": s.get("foreign_hold_change_5d"),
                "op_profit_latest": s.get("op_profit_latest"),
                "op_profit_prev": s.get("op_profit_prev"),
                "op_profit_delta": s.get("op_profit_delta"),
                "fscore_now": s.get("fscore_now"),
                "fscore_past": s.get("fscore_past"),
                "fscore_delta": s.get("fscore_delta"),
                "insider_reprors": s.get("insider_reprors"),
                "insider_net_qty": s.get("insider_net_qty"),
                "short_balance": s.get("short_balance"),
                "short_ratio": s.get("short_ratio"),
                "foreign_hold_ratio": s.get("foreign_hold_ratio"),
                "credit_balance": s.get("credit_balance"),
            })
        results.sort(key=lambda x: x.get(sort_field) if x.get(sort_field) is not None else (-9999 if reverse else 9999), reverse=reverse)
        total = len(results)
        results = results[:n]

        result = {
            "date": db["date"],
            "preset": preset_str or "(none)",
            "preset_description": " + ".join(preset_desc),
            "sort": sort_field,
            "market": market_filter,
            "total_matched": total,
            "count": len(results),
            "results": results,
        }

    return result


async def handle_get_finance_rank(arguments: dict, token=None) -> dict | list:
    result = None
    rank_type = (arguments.get("rank_type") or "").strip().lower()
    n = min(int(arguments.get("n", 30) or 30), 100)

    if rank_type in ("fscore", "mscore_safe", "fcf_yield"):
        # F/M/FCF Phase4: daily_snapshot 알파 메트릭 순위
        import sqlite3 as _sqlite3
        from db_collector import DB_PATH as _DB_PATH
        market_param = (arguments.get("market") or "all").strip().lower()
        market_filter_sql = ""
        market_args: list = []
        if market_param in ("kospi", "kosdaq"):
            market_filter_sql = " AND m.market = ?"
            market_args = [market_param]

        try:
            conn = _sqlite3.connect(_DB_PATH, timeout=10)
            conn.execute("PRAGMA cache_size = -65536")
            conn.execute("PRAGMA temp_store = MEMORY")
            conn.execute("PRAGMA mmap_size = 268435456")
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.row_factory = _sqlite3.Row
            # 최신 trade_date
            latest = conn.execute(
                "SELECT MAX(trade_date) FROM daily_snapshot"
            ).fetchone()
            latest_date = latest[0] if latest else None
            if not latest_date:
                result = {"error": "daily_snapshot 비어있음 — Phase 3.5 대기"}
            else:
                # rank_type 별 SELECT
                if rank_type == "fscore":
                    sql = (
                        "SELECT d.symbol, m.name, m.market, d.market_cap, "
                        "       d.fscore AS metric "
                        "FROM daily_snapshot d "
                        "LEFT JOIN stock_master m ON d.symbol = m.symbol "
                        "WHERE d.trade_date = ? "
                        "  AND d.fscore IS NOT NULL "
                        "  AND d.fscore >= 7"
                        + market_filter_sql +
                        " ORDER BY d.fscore DESC, d.market_cap DESC "
                        "LIMIT ?"
                    )
                elif rank_type == "mscore_safe":
                    sql = (
                        "SELECT d.symbol, m.name, m.market, d.market_cap, "
                        "       d.mscore AS metric "
                        "FROM daily_snapshot d "
                        "LEFT JOIN stock_master m ON d.symbol = m.symbol "
                        "WHERE d.trade_date = ? "
                        "  AND d.mscore IS NOT NULL "
                        "  AND d.mscore <= -2.22"
                        + market_filter_sql +
                        " ORDER BY d.mscore ASC "
                        "LIMIT ?"
                    )
                else:  # fcf_yield
                    sql = (
                        "SELECT d.symbol, m.name, m.market, d.market_cap, "
                        "       d.fcf_yield_ev AS metric "
                        "FROM daily_snapshot d "
                        "LEFT JOIN stock_master m ON d.symbol = m.symbol "
                        "WHERE d.trade_date = ? "
                        "  AND d.fcf_yield_ev IS NOT NULL"
                        + market_filter_sql +
                        " ORDER BY d.fcf_yield_ev DESC "
                        "LIMIT ?"
                    )
                rows = conn.execute(sql, [latest_date] + market_args + [n]).fetchall()
                conn.close()

                if not rows:
                    result = {
                        "error": "데이터 수집 전 — Phase 3.5 대기",
                        "rank_type": rank_type,
                        "trade_date": latest_date,
                    }
                else:
                    label_map = {
                        "fscore":      "F-Score (>=7 우량)",
                        "mscore_safe": "M-Score (<=-2.22 안전)",
                        "fcf_yield":   "FCF Yield / EV (%)",
                    }
                    stocks_out = []
                    for i, r in enumerate(rows, 1):
                        stocks_out.append({
                            "rank":       i,
                            "symbol":     r["symbol"],
                            "name":       r["name"] or "",
                            "market":     r["market"] or "",
                            "market_cap": r["market_cap"],
                            "metric":     r["metric"],
                        })
                    result = {
                        "rank_type":  rank_type,
                        "label":      label_map[rank_type],
                        "trade_date": latest_date,
                        "count":      len(stocks_out),
                        "stocks":     stocks_out,
                    }
        except Exception as e:
            result = {"error": f"알파 메트릭 조회 실패: {e}"}
    else:
        # 기존 KIS API 재무비율 순위
        market = arguments.get("market", "0000").strip()
        year = arguments.get("year", "").strip()
        quarter = arguments.get("quarter", "3").strip()
        sort = arguments.get("sort", "7").strip()
        sort_labels = {"7": "수익성", "11": "안정성", "15": "성장성", "20": "활동성"}
        items = await kis_finance_ratio_rank(token, market=market, year=year,
                                             quarter=quarter, sort=sort, n=n)
        result = {
            "sort": sort_labels.get(sort, sort),
            "year": year or str(datetime.now(KST).year - 1),
            "quarter": quarter,
            "count": len(items),
            "stocks": items,
        }

    return result


async def handle_get_highlow(arguments: dict, token=None) -> dict | list:
    result = None
    mode = arguments.get("mode", "high").strip().lower()
    market = arguments.get("market", "0000").strip()
    gap_min = int(arguments.get("gap_min", 0) or 0)
    gap_max = int(arguments.get("gap_max", 10) or 10)
    n = min(int(arguments.get("n", 30) or 30), 100)
    items = await kis_near_new_highlow(token, mode=mode, market=market,
                                       gap_min=gap_min, gap_max=gap_max, n=n)
    result = {
        "mode": "52주 신고가 근접" if mode == "high" else "52주 신저가 근접",
        "gap_range": f"{gap_min}%~{gap_max}%",
        "count": len(items),
        "stocks": items,
    }

    return result


async def handle_get_broker(arguments: dict, token=None) -> dict | list:
    result = None
    ticker = arguments.get("ticker", "").strip()
    if not ticker:
        result = {"error": "ticker 필수"}
    else:
        data = await kis_inquire_member(ticker, token)
        result = data

    return result


