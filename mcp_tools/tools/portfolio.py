# mcp_tools/tools/portfolio.py — get_portfolio, get_portfolio_history, get_trade_stats, simulate_trade
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


async def handle_get_portfolio(arguments: dict, token=None) -> dict | list:
    result = None
    mode = arguments.get("mode", "").strip().lower()

    if mode == "set":
        # ── 포트폴리오 저장 모드 ──
        market   = arguments.get("market", "KR").strip().upper()
        holdings = arguments.get("holdings") or {}
        cash_krw = arguments.get("cash_krw")
        cash_usd = arguments.get("cash_usd")
        portfolio = load_json(PORTFOLIO_FILE, {})
        # 현금 잔고 업데이트
        if cash_krw is not None:
            portfolio["cash_krw"] = float(cash_krw)
        if cash_usd is not None:
            portfolio["cash_usd"] = float(cash_usd)
        if not holdings and cash_krw is None and cash_usd is None:
            result = {"error": "holdings, cash_krw, cash_usd 중 하나는 필요합니다"}
        else:
            if market == "US" and holdings:
                portfolio["us_stocks"] = holdings
            elif holdings:
                # 기존 KR 종목 제거 후 새로 설정
                _meta_keys = {"us_stocks", "cash_krw", "cash_usd"}
                old_kr = [k for k in portfolio if k not in _meta_keys]
                for k in old_kr:
                    del portfolio[k]
                for ticker, info in holdings.items():
                    portfolio[ticker] = info
            save_json(PORTFOLIO_FILE, portfolio)
            asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
            kr_count = sum(1 for k in portfolio if k not in ("us_stocks", "cash_krw", "cash_usd"))
            us_count = len(portfolio.get("us_stocks", {}))
            result = {"ok": True,
                      "message": f"포트폴리오 저장됨 (KR {kr_count}종목, US {us_count}종목)",
                      "cash_krw": portfolio.get("cash_krw"),
                      "cash_usd": portfolio.get("cash_usd")}

    else:
        # ── 조회 모드 (기존) ──
        portfolio = load_json(PORTFOLIO_FILE, {})
        _meta_keys = {"us_stocks", "cash_krw", "cash_usd"}
        kr_stocks = {k: v for k, v in portfolio.items() if k not in _meta_keys}
        us_stocks = portfolio.get("us_stocks", {})
        if not kr_stocks and not us_stocks:
            result = {"message": "포트폴리오가 비어있습니다. /setportfolio 또는 /setusportfolio 로 등록하세요."}
        else:
            kr_holdings, us_holdings = [], []
            kr_eval = kr_cost = us_eval = us_cost = 0

            for ticker, info in kr_stocks.items():
                qty = info.get("qty", 0)
                avg = info.get("avg_price", 0)
                chg_today = None
                cached = ws_manager.get_cached_price(ticker)
                if cached is not None:
                    cur = int(cached)
                else:
                    d = await kis_stock_price(ticker, token)
                    cur = int(d.get("stck_prpr", 0) or 0)
                    chg_today = d.get("prdy_ctrt")
                    await asyncio.sleep(0.3)
                eval_amt = cur * qty
                cost_amt = int(avg) * qty
                pnl = eval_amt - cost_amt
                pnl_pct = round((cur - avg) / avg * 100, 2) if avg else 0
                kr_eval += eval_amt
                kr_cost += cost_amt
                kr_holdings.append({
                    "ticker": ticker, "name": info.get("name", ticker),
                    "qty": qty, "avg_price": avg, "cur_price": cur,
                    "eval_amt": eval_amt, "pnl": pnl, "pnl_pct": pnl_pct,
                    "chg_today": chg_today,
                })

            for symbol, info in us_stocks.items():
                qty = info.get("qty", 0)
                avg = info.get("avg_price", 0)
                chg_today = None
                cached = ws_manager.get_cached_price(symbol)
                if cached is not None:
                    cur = float(cached)
                else:
                    d = await kis_us_stock_price(symbol, token)
                    cur = float(d.get("last", 0) or d.get("stck_prpr", 0) or 0)
                    chg_today = d.get("rate")
                    await asyncio.sleep(0.3)
                eval_amt = round(cur * qty, 2)
                cost_amt = round(avg * qty, 2)
                pnl = round(eval_amt - cost_amt, 2)
                pnl_pct = round((cur - avg) / avg * 100, 2) if avg else 0
                us_eval += eval_amt
                us_cost += cost_amt
                us_holdings.append({
                    "ticker": symbol, "name": info.get("name", symbol),
                    "qty": qty, "avg_price": avg, "cur_price": cur,
                    "eval_amt": eval_amt, "pnl": pnl, "pnl_pct": pnl_pct,
                    "chg_today": chg_today,
                })

            result = {
                "kr": {
                    "holdings": kr_holdings,
                    "summary": {
                        "total_eval": kr_eval, "total_cost": kr_cost,
                        "total_pnl": kr_eval - kr_cost,
                        "total_pnl_pct": round((kr_eval - kr_cost) / kr_cost * 100, 2) if kr_cost else 0,
                    },
                },
                "us": {
                    "holdings": us_holdings,
                    "summary": {
                        "total_eval": round(us_eval, 2), "total_cost": round(us_cost, 2),
                        "total_pnl": round(us_eval - us_cost, 2),
                        "total_pnl_pct": round((us_eval - us_cost) / us_cost * 100, 2) if us_cost else 0,
                    },
                },
                "cash_krw": portfolio.get("cash_krw", 0),
                "cash_usd": portfolio.get("cash_usd", 0),
            }

    return result


async def handle_get_portfolio_history(arguments: dict) -> dict | list:
    result = None
    days = min(int(arguments.get("days", 30) or 30), 365)
    brief = arguments.get("brief", False)
    if isinstance(brief, str):
        brief = brief.lower() in ("true", "1", "yes")
    history = load_json(PORTFOLIO_HISTORY_FILE, {"snapshots": []})
    snaps = sorted(history.get("snapshots", []), key=lambda x: x.get("date", ""))
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    recent = [s for s in snaps if s.get("date", "") >= cutoff]
    dd = check_drawdown()
    if brief:
        recent = [
            {k: s.get(k) for k in ("date", "total_asset_krw", "cash_weight_pct",
                                    "kr_eval", "us_eval_krw") if k in s}
            for s in recent
        ]
    result = {
        "days": days,
        "snapshot_count": len(recent),
        "snapshots": recent,
        "drawdown": dd,
    }

    return result


async def handle_get_trade_stats(arguments: dict) -> dict | list:
    result = None
    period = arguments.get("period", "month").strip().lower()
    result = get_trade_stats(period)

    return result


async def handle_simulate_trade(arguments: dict, token=None) -> dict | list:
    result = None
    sells = arguments.get("sells") or []
    buys = arguments.get("buys") or []
    if not sells and not buys:
        result = {"error": "sells 또는 buys 중 하나는 필요합니다"}
    else:
        portfolio = load_json(PORTFOLIO_FILE, {})
        _meta_keys = {"us_stocks", "cash_krw", "cash_usd"}
        kr_stocks = {k: dict(v) for k, v in portfolio.items() if k not in _meta_keys and isinstance(v, dict)}
        us_stocks = {k: dict(v) for k, v in portfolio.get("us_stocks", {}).items()}
        cash_krw = float(portfolio.get("cash_krw", 0) or 0)
        cash_usd = float(portfolio.get("cash_usd", 0) or 0)
        stops = load_stoploss()

        # 환율 조회 (fallback 1400)
        _fx = await get_yahoo_quote("USDKRW=X")
        _usd_krw = float(_fx.get("price", 1400)) if _fx else 1400

        # 현재가 캐시
        price_cache = {}

        async def get_cur_price(ticker):
            if ticker in price_cache:
                return price_cache[ticker]
            if _is_us_ticker(ticker):
                d = await kis_us_stock_price(ticker, token)
                p = float(d.get("last", 0) or 0)
            else:
                d = await kis_stock_price(ticker, token)
                p = int(d.get("stck_prpr", 0) or 0)
            price_cache[ticker] = p
            await asyncio.sleep(0.3)
            return p

        # 시뮬레이션: 매도 적용
        sim_kr = {k: dict(v) for k, v in kr_stocks.items()}
        sim_us = {k: dict(v) for k, v in us_stocks.items()}
        sim_cash_krw = cash_krw
        sim_cash_usd = cash_usd
        trade_log = []

        for s in sells:
            t = s.get("ticker", "").strip().upper()
            q = int(s.get("qty", 0))
            p = s.get("price")
            if not t or q <= 0:
                continue
            if p is None or p <= 0:
                p = await get_cur_price(t)
            if _is_us_ticker(t):
                if t in sim_us:
                    sim_us[t]["qty"] = max(0, sim_us[t].get("qty", 0) - q)
                    if sim_us[t]["qty"] == 0:
                        del sim_us[t]
                    sim_cash_usd += p * q
                    trade_log.append(f"매도 {t} {q}주 @${p:,.2f}")
            else:
                if t in sim_kr:
                    sim_kr[t]["qty"] = max(0, sim_kr[t].get("qty", 0) - q)
                    if sim_kr[t]["qty"] == 0:
                        del sim_kr[t]
                    sim_cash_krw += p * q
                    trade_log.append(f"매도 {t} {q}주 @{p:,.0f}원")

        # 시뮬레이션: 매수 적용
        for b in buys:
            t = b.get("ticker", "").strip().upper()
            q = int(b.get("qty", 0))
            p = b.get("price")
            if not t or q <= 0:
                continue
            if p is None or p <= 0:
                p = await get_cur_price(t)
            if _is_us_ticker(t):
                cost = p * q
                sim_cash_usd -= cost
                if t in sim_us:
                    old_qty = sim_us[t].get("qty", 0)
                    old_avg = sim_us[t].get("avg_price", 0)
                    new_qty = old_qty + q
                    sim_us[t]["qty"] = new_qty
                    sim_us[t]["avg_price"] = round((old_avg * old_qty + p * q) / new_qty, 2)
                else:
                    sim_us[t] = {"name": t, "qty": q, "avg_price": round(p, 2)}
                trade_log.append(f"매수 {t} {q}주 @${p:,.2f}")
            else:
                cost = p * q
                sim_cash_krw -= cost
                if t in sim_kr:
                    old_qty = sim_kr[t].get("qty", 0)
                    old_avg = sim_kr[t].get("avg_price", 0)
                    new_qty = old_qty + q
                    sim_kr[t]["qty"] = new_qty
                    sim_kr[t]["avg_price"] = round((old_avg * old_qty + p * q) / new_qty)
                else:
                    sim_kr[t] = {"name": t, "qty": q, "avg_price": round(p)}
                trade_log.append(f"매수 {t} {q}주 @{p:,.0f}원")

        # 시뮬레이션 결과 계산
        # 1) 종목별 비중
        sim_eval_kr = 0
        sim_holdings_kr = []
        for t, info in sim_kr.items():
            p = await get_cur_price(t)
            ev = p * info.get("qty", 0)
            sim_eval_kr += ev
            sim_holdings_kr.append({"ticker": t, "name": info.get("name", t), "qty": info["qty"], "eval": ev})

        sim_eval_us = 0
        sim_holdings_us = []
        for t, info in sim_us.items():
            p = await get_cur_price(t)
            ev = p * info.get("qty", 0)
            sim_eval_us += ev
            sim_holdings_us.append({"ticker": t, "name": info.get("name", t), "qty": info["qty"], "eval": ev})

        total_eval = sim_eval_kr + sim_eval_us * _usd_krw + sim_cash_krw + sim_cash_usd * _usd_krw

        # 2) 비중 계산
        for h in sim_holdings_kr:
            h["weight_pct"] = round(h["eval"] / total_eval * 100, 1) if total_eval > 0 else 0
        for h in sim_holdings_us:
            h["weight_pct"] = round(h["eval"] * _usd_krw / total_eval * 100, 1) if total_eval > 0 else 0

        # 3) 섹터 비중 (국내만, _TICKER_SECTOR 사용)
        sector_eval = {}
        for h in sim_holdings_kr:
            sec = _TICKER_SECTOR.get(h["ticker"], "기타")
            sector_eval[sec] = sector_eval.get(sec, 0) + h["eval"]
        sector_weights = {s: round(v / total_eval * 100, 1) for s, v in sector_eval.items() if total_eval > 0}

        # 4) 현금 비중
        cash_total_krw = sim_cash_krw + sim_cash_usd * _usd_krw
        cash_pct = round(cash_total_krw / total_eval * 100, 1) if total_eval > 0 else 0

        # 5) RR 비율 (목표수익/손절손실)
        rr_items = []
        for h in sim_holdings_kr:
            t = h["ticker"]
            stop_info = stops.get(t, {})
            stop_p = float(stop_info.get("stop_price", 0) or 0)
            target_p = float(stop_info.get("target_price") or stop_info.get("target", 0) or 0)
            cur_p = await get_cur_price(t)
            if stop_p > 0 and target_p > 0 and cur_p > 0:
                risk = (cur_p - stop_p) / cur_p * 100
                reward = (target_p - cur_p) / cur_p * 100
                rr = round(reward / risk, 2) if risk > 0 else 0
                rr_items.append({"ticker": t, "risk_pct": round(risk, 1), "reward_pct": round(reward, 1), "rr": rr})

        result = {
            "trades": trade_log,
            "kr_holdings": sorted(sim_holdings_kr, key=lambda x: x["eval"], reverse=True),
            "us_holdings": sorted(sim_holdings_us, key=lambda x: x["eval"], reverse=True),
            "sector_weights": dict(sorted(sector_weights.items(), key=lambda x: x[1], reverse=True)),
            "cash": {"krw": round(sim_cash_krw), "usd": round(sim_cash_usd, 2), "pct": cash_pct},
            "total_eval_krw": round(total_eval),
            "rr_ratios": rr_items,
        }

    return result


