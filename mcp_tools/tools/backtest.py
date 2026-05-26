# mcp_tools/tools/backtest.py — get_backtest, backup_data
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


async def handle_get_backtest(arguments: dict, token=None) -> dict | list:
    result = None
    ticker = arguments.get("ticker", "").strip().upper()
    period = arguments.get("period", "D250").strip().upper()
    strategy = arguments.get("strategy", "ma_cross").strip().lower()

    if not ticker:
        result = {"error": "ticker는 필수입니다"}
    elif strategy not in ("ma_cross", "momentum_exit", "supply_follow", "bollinger", "hybrid"):
        result = {"error": f"지원 전략: ma_cross, momentum_exit, supply_follow, bollinger, hybrid"}
    else:
        is_us = _is_us_ticker(ticker)

        # ── 일봉 데이터 조회 ──
        period_type = period[0] if period else "D"
        try:
            n = int(period[1:])
        except ValueError:
            n = 250

        _krx_supply_map = {}   # Y모드 supply_follow용
        _data_error = None     # 데이터 조회 실패 시 에러 메시지

        if period_type == "Y":
            # ── 장기 데이터: FDR/yfinance ──
            years = max(1, min(n, 5))  # 1~5년 제한
            loop = asyncio.get_running_loop()
            candles = await loop.run_in_executor(None, get_historical_ohlcv, ticker, years)
            if not candles:
                _data_error = f"장기 데이터 조회 실패 ({ticker}, {years}년). FDR/yfinance 설치 확인: pip install finance-datareader yfinance"
            else:
                # supply_follow 전략 시 KRX 수급도 로드
                if strategy == "supply_follow" and not is_us:
                    krx_supply = await loop.run_in_executor(None, get_historical_supply, ticker, years * 365)
                    if krx_supply:
                        _krx_supply_map = {s["date"]: s for s in krx_supply}
        else:
            # ── 기존: KIS API 일봉 ──
            today_str = datetime.now(KST).strftime("%Y%m%d")
            buf = {"D": 2, "W": 8, "M": 40}.get(period_type, 2)
            start_dt = (datetime.now(KST) - timedelta(days=n * buf)).strftime("%Y%m%d")

            if is_us:
                excd = _guess_excd(ticker)
                s = _get_session()
                _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/dailyprice",
                    "HHDFS76240000", token,
                    {"AUTH": "", "EXCD": excd, "SYMB": ticker,
                     "GUBN": "0", "BYMD": today_str, "MODP": "0"})
                raw_candles = d.get("output2", [])
                candles = []
                for c in raw_candles[:n]:
                    candles.append({
                        "date": c.get("xymd", ""),
                        "open": float(c.get("open", 0) or 0),
                        "high": float(c.get("high", 0) or 0),
                        "low": float(c.get("low", 0) or 0),
                        "close": float(c.get("clos", 0) or 0),
                        "vol": int(c.get("tvol", 0) or 0),
                    })
            else:
                # 국내 일봉 API 1회 최대 100건 → 분할 호출
                candles = []
                _chunk = 100
                _end = today_str
                _remaining = n
                _seen_dates = set()
                s = _get_session()
                while _remaining > 0:
                    _start = (datetime.strptime(_end, "%Y%m%d") - timedelta(days=_chunk * 2)).strftime("%Y%m%d")
                    _, d = await _kis_get(s,
                        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                        "FHKST03010100", token,
                        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
                         "FID_INPUT_DATE_1": _start, "FID_INPUT_DATE_2": _end,
                         "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
                    batch = d.get("output2", [])
                    if not batch:
                        break
                    added = 0
                    for c in batch:
                        dt = c.get("stck_bsop_date", "")
                        if not dt or dt in _seen_dates:
                            continue
                        _seen_dates.add(dt)
                        candles.append({
                            "date": dt,
                            "open": int(c.get("stck_oprc", 0) or 0),
                            "high": int(c.get("stck_hgpr", 0) or 0),
                            "low": int(c.get("stck_lwpr", 0) or 0),
                            "close": int(c.get("stck_clpr", 0) or 0),
                            "vol": int(c.get("acml_vol", 0) or 0),
                        })
                        added += 1
                    _remaining -= added
                    if added < 10:
                        break  # 더 이상 데이터 없음
                    # 다음 구간: 가장 오래된 날짜 전일부터
                    oldest = min(_seen_dates)
                    _end = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
                    await asyncio.sleep(0.3)

        # 시간순 정렬 (API는 최신순, Y모드는 이미 정렬)
        if candles:
            candles.sort(key=lambda x: x["date"])

        if _data_error:
            result = {"error": _data_error}
        elif len(candles) < 20:
            result = {"error": f"일봉 데이터 부족 ({len(candles)}개). 최소 20개 필요."}
        else:
            # ── 비용 설정 ──
            if is_us:
                buy_cost_pct = 0.15 + 0.1    # 환전 0.15% + 슬리피지 0.1%
                sell_cost_pct = 0.15 + 0.1
            else:
                buy_cost_pct = 0.015 + 0.1   # 수수료 0.015% + 슬리피지 0.1%
                sell_cost_pct = 0.015 + 0.18 + 0.1  # +거래세 0.18%

            # ── 이동평균 / 표준편차 헬퍼 ──
            closes = [c["close"] for c in candles]
            volumes = [c["vol"] for c in candles]

            def _ma(arr, period_len, idx):
                if idx < period_len - 1:
                    return None
                return sum(arr[idx - period_len + 1:idx + 1]) / period_len

            def _std(arr, period_len, idx):
                if idx < period_len - 1:
                    return None
                subset = arr[idx - period_len + 1:idx + 1]
                avg = sum(subset) / period_len
                return (sum((x - avg) ** 2 for x in subset) / period_len) ** 0.5

            # ── 신호 생성 (look-ahead bias 방지: i일 종가 신호 → i+1일 시가 체결) ──
            signals = [None] * len(candles)

            if strategy == "ma_cross":
                for i in range(20, len(candles)):
                    ma5p = _ma(closes, 5, i - 1)
                    ma20p = _ma(closes, 20, i - 1)
                    ma5c = _ma(closes, 5, i)
                    ma20c = _ma(closes, 20, i)
                    if ma5p and ma20p and ma5c and ma20c:
                        if ma5p <= ma20p and ma5c > ma20c:
                            signals[i] = "buy"
                        elif ma5p >= ma20p and ma5c < ma20c:
                            signals[i] = "sell"

            elif strategy == "momentum_exit":
                for i in range(20, len(candles)):
                    lookback = min(i, 250)
                    high_max = max(c["high"] for c in candles[i - lookback:i])
                    if candles[i]["close"] > high_max:
                        signals[i] = "buy"
                    recent_high = max(c["high"] for c in candles[max(0, i - 20):i + 1])
                    drop_pct = (recent_high - candles[i]["close"]) / recent_high * 100 if recent_high > 0 else 0
                    vol_ma20 = _ma(volumes, 20, i)
                    vol_ratio = candles[i]["vol"] / vol_ma20 if vol_ma20 and vol_ma20 > 0 else 1
                    if drop_pct >= 10 and vol_ratio <= 0.5:
                        signals[i] = "sell"

            elif strategy == "supply_follow":
                supply_by_date = {}

                # 1순위: KRX 크롤링 데이터 (Y 모드에서 조회했으면)
                if _krx_supply_map:
                    supply_by_date = _krx_supply_map

                # 2순위: 기존 축적 데이터
                if not supply_by_date:
                    supply_hist = load_json(SUPPLY_HISTORY_FILE, {})
                    ticker_supply = supply_hist.get(ticker, [])
                    supply_by_date = {s["date"].replace("-", ""): s for s in ticker_supply}

                # 3순위: KIS API 30일 (FHPTJ04160001 단일 페이지 최대)
                if not supply_by_date:
                    try:
                        api_hist = await kis_investor_trend_history(ticker, token, n_days=30)
                        api_hist.reverse()
                        ticker_supply = [{"date": h["date"][:4]+"-"+h["date"][4:6]+"-"+h["date"][6:],
                                          "foreign_net": h["foreign_net"],
                                          "institution_net": h["institution_net"]} for h in api_hist]
                        supply_by_date = {s["date"].replace("-", ""): s for s in ticker_supply}
                    except Exception:
                        pass
                for i in range(2, len(candles)):
                    dates_3 = [candles[j]["date"] for j in range(i - 2, i + 1)]
                    frgn_3 = []
                    for dt in dates_3:
                        s_data = supply_by_date.get(dt)
                        if s_data:
                            frgn_3.append(s_data.get("foreign_net", 0))
                    if len(frgn_3) == 3:
                        if all(f > 0 for f in frgn_3):
                            signals[i] = "buy"
                        elif all(f < 0 for f in frgn_3):
                            signals[i] = "sell"

            elif strategy == "bollinger":
                for i in range(19, len(candles)):
                    ma20 = _ma(closes, 20, i)
                    sd = _std(closes, 20, i)
                    if ma20 is not None and sd is not None:
                        upper = ma20 + 2 * sd
                        lower = ma20 - 2 * sd
                        if candles[i]["close"] <= lower:
                            signals[i] = "buy"
                        elif candles[i]["close"] >= upper:
                            signals[i] = "sell"

            elif strategy == "hybrid":
                for i in range(60, len(candles)):
                    ma5 = _ma(closes, 5, i)
                    ma20 = _ma(closes, 20, i)
                    ma60 = _ma(closes, 60, i)
                    vol_ma20 = _ma(volumes, 20, i)
                    if ma5 and ma20 and ma60 and vol_ma20:
                        aligned = ma5 > ma20 > ma60
                        vol_up = candles[i]["vol"] > vol_ma20
                        above_ma5 = candles[i]["close"] > ma5
                        if aligned and vol_up and above_ma5:
                            signals[i] = "buy"
                        if ma5 < ma20:
                            signals[i] = "sell"
                    recent_high = max(c["high"] for c in candles[max(0, i - 20):i + 1])
                    drop_pct = (recent_high - candles[i]["close"]) / recent_high * 100 if recent_high > 0 else 0
                    if drop_pct >= 10:
                        signals[i] = "sell"

            # ── 매매 시뮬레이션 (익일 시가 체결) ──
            trades = []
            position = None

            for i in range(len(candles) - 1):
                sig = signals[i]
                next_open = candles[i + 1]["open"]
                next_date = candles[i + 1]["date"]

                if next_open <= 0:
                    continue

                if sig == "buy" and position is None:
                    entry_price = next_open * (1 + buy_cost_pct / 100)
                    position = {"entry_date": next_date, "entry_price": entry_price, "entry_idx": i + 1}

                elif sig == "sell" and position is not None:
                    exit_price = next_open * (1 - sell_cost_pct / 100)
                    pnl_pct = (exit_price - position["entry_price"]) / position["entry_price"] * 100
                    hold_days = i + 1 - position["entry_idx"]
                    trades.append({
                        "entry_date": position["entry_date"],
                        "entry_price": round(position["entry_price"], 2),
                        "exit_date": next_date,
                        "exit_price": round(exit_price, 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "hold_days": hold_days,
                    })
                    position = None

            # 미청산 포지션 (마지막 종가로 평가)
            if position is not None:
                last = candles[-1]
                exit_price = last["close"] * (1 - sell_cost_pct / 100)
                pnl_pct = (exit_price - position["entry_price"]) / position["entry_price"] * 100
                hold_days = len(candles) - 1 - position["entry_idx"]
                trades.append({
                    "entry_date": position["entry_date"],
                    "entry_price": round(position["entry_price"], 2),
                    "exit_date": last["date"] + "(미청산)",
                    "exit_price": round(exit_price, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "hold_days": hold_days,
                    "open_position": True,
                })

            # ── 성과 계산 ──
            wins = [t for t in trades if t["pnl_pct"] > 0]
            losses = [t for t in trades if t["pnl_pct"] <= 0]
            total_return = 1.0
            for t in trades:
                total_return *= (1 + t["pnl_pct"] / 100)
            total_return_pct = round((total_return - 1) * 100, 2)

            # MDD
            peak = 1.0
            mdd = 0.0
            cumulative = 1.0
            for t in trades:
                cumulative *= (1 + t["pnl_pct"] / 100)
                if cumulative > peak:
                    peak = cumulative
                dd = (peak - cumulative) / peak * 100
                if dd > mdd:
                    mdd = dd

            # Buy & Hold 벤치마크
            bh_entry = candles[0]["close"]
            bh_exit = candles[-1]["close"]
            bh_cost = buy_cost_pct + sell_cost_pct
            bh_return = (bh_exit - bh_entry) / bh_entry * 100 - bh_cost if bh_entry > 0 else 0

            avg_hold = round(sum(t["hold_days"] for t in trades) / len(trades), 1) if trades else 0

            # supply_follow 경고
            supply_warning = None
            if strategy == "supply_follow":
                if _krx_supply_map:
                    krx_days = len(_krx_supply_map)
                    if krx_days < 60:
                        supply_warning = f"KRX 수급 데이터 {krx_days}일분 조회됨. 데이터가 적어 신호 정밀도가 낮을 수 있음."
                else:
                    supply_hist_data = load_json(SUPPLY_HISTORY_FILE, {})
                    ticker_days = len(supply_hist_data.get(ticker, []))
                    if ticker_days < 60:
                        supply_warning = f"수급 데이터 {ticker_days}일분만 축적됨 (KIS API 최대 10일). Y모드(FDR+KRX) 또는 3개월 축적 후 정밀화 가능."

            result = {
                "ticker": ticker,
                "market": "US" if is_us else "KR",
                "strategy": strategy,
                "period": period,
                "candle_count": len(candles),
                "date_range": f"{candles[0]['date']}~{candles[-1]['date']}",
                "total_return_pct": total_return_pct,
                "benchmark_bh_pct": round(bh_return, 2),
                "alpha_pct": round(total_return_pct - bh_return, 2),
                "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
                "trade_count": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "max_drawdown_pct": round(mdd, 2),
                "avg_hold_days": avg_hold,
                "costs": {"buy_pct": buy_cost_pct, "sell_pct": sell_cost_pct,
                          "note": "한국: 수수료+거래세+슬리피지" if not is_us else "미국: 환전스프레드+슬리피지"},
                "trades": trades,
            }
            if supply_warning:
                result["supply_warning"] = supply_warning

    return result


async def handle_backup_data(arguments: dict) -> dict | list:
    result = None
    action = arguments.get("action", "status").strip().lower()
    if action == "backup":
        result = await backup_data_files()
    elif action == "restore":
        result = await restore_data_files(force=False)
    elif action == "restore_force":
        result = await restore_data_files(force=True)
    elif action == "status":
        result = await get_backup_status()
    else:
        result = {"error": f"알 수 없는 action: {action}. 'backup'|'restore'|'restore_force'|'status' 중 하나"}

    return result


