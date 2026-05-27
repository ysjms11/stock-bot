# mcp_tools/tools/alerts.py — get_alerts, set_alert, manage_watch
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


# ── XML artifact cleaner ───────────────────────────────────────────────────────
# Claude 이전 세션에서 <parameter name="..."> / </memo> 등 XML 태그가
# memo 필드에 잘못 삽입되는 경우 제거 (read + write 양쪽에 적용).
# 패턴: </memo>는 실제 메모 끝을 의미하므로 그 이후 내용을 전부 제거 (도구 호출 아티팩트).
# 이후 남은 XML 태그(<tag> / </tag>)도 제거.

def _clean_memo(text: str) -> str:
    """Remove XML/tool-call artifacts from memo strings.

    1. Strip everything from </memo> onward (tool-call parameter leakage).
    2. Strip remaining bare XML tags.
    3. Collapse double newlines, strip whitespace.
    """
    if not text:
        return text
    # </memo> 뒤에 붙은 아티팩트 제거
    cleaned = re.sub(r"</memo>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    # 남은 XML 태그 제거
    cleaned = re.sub(r"</?[a-zA-Z][^>]*>", "", cleaned)
    # 연속 개행 정리
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    return cleaned.strip()


async def handle_get_alerts(arguments: dict, token=None) -> dict | list:
    result = None
    stops = load_stoploss()
    kr_stops = {k: v for k, v in stops.items() if k != "us_stocks"}
    us_stops = stops.get("us_stocks", {})
    wa = load_watchalert()

    # ── 병렬 현재가 조회 ──
    async def _fetch_kr(ticker):
        try:
            d = await kis_stock_price(ticker, token)
            return int(d.get("stck_prpr", 0) or 0)
        except Exception:
            return 0

    async def _fetch_us_yahoo(sym):
        try:
            d = await get_yahoo_quote(sym)
            return float(d.get("price", 0) or 0) if d else 0.0
        except Exception:
            return 0.0

    async def _fetch_wa(wa_ticker):
        try:
            if _is_us_ticker(wa_ticker):
                d = await kis_us_stock_price(wa_ticker, token)
                return float(d.get("last", 0) or 0)
            else:
                d = await kis_stock_price(wa_ticker, token)
                return int(d.get("stck_prpr", 0) or 0)
        except Exception:
            return 0.0

    kr_tickers = list(kr_stops.keys())
    us_syms = list(us_stops.keys())
    wa_tickers = list(wa.keys())

    kr_prices, us_prices, wa_prices = await asyncio.gather(
        asyncio.gather(*[_fetch_kr(t) for t in kr_tickers]),
        asyncio.gather(*[_fetch_us_yahoo(s) for s in us_syms]),
        asyncio.gather(*[_fetch_wa(t) for t in wa_tickers]),
    )

    alerts = []
    for ticker, info, cur in zip(kr_tickers, list(kr_stops.values()), kr_prices):
        stop   = info.get("stop_price", 0)
        entry  = info.get("entry_price", 0)
        target = info.get("target_price", 0)
        gap_pct = round((stop - cur) / cur * 100, 2) if cur else None
        item = {
            "ticker": ticker, "name": info.get("name", ticker),
            "market": "KR", "stop": stop, "entry": entry,
            "cur": cur, "gap_pct": gap_pct,
        }
        if target:
            item["target"] = target
            item["target_pct"] = round((target - cur) / cur * 100, 2) if cur else None
        alerts.append(item)
    for sym, info, cur in zip(us_syms, list(us_stops.values()), us_prices):
        stop   = info.get("stop_price", 0)
        target = info.get("target_price", 0)
        gap_pct = round((stop - cur) / cur * 100, 2) if cur else None
        item = {
            "ticker": sym, "name": info.get("name", sym),
            "market": "US", "stop": stop,
            "cur": cur, "gap_pct": gap_pct,
        }
        if target:
            item["target"] = target
            item["target_pct"] = round((target - cur) / cur * 100, 2) if cur else None
        alerts.append(item)

    # ── 매수감시 목록 통합 ──
    watch_alerts = []
    for wa_ticker, wa_info, cur in zip(wa_tickers, list(wa.values()), wa_prices):
        buy_price = wa_info.get("buy_price", 0)
        gap_pct = round((cur - buy_price) / buy_price * 100, 2) if buy_price else None
        # grade: 저장된 값 우선, 없으면 memo에서 파싱
        grade = wa_info.get("grade", "")
        if not grade:
            m = re.search(r"\(([ABCD][+-]?)\)", wa_info.get("memo", ""))
            grade = m.group(1) if m else ""
        # market: 저장된 값 우선, 없으면 ticker 패턴
        mkt = wa_info.get("market", "")
        if not mkt:
            mkt = "US" if re.match(r"^[A-Z]+$", wa_ticker) else "KR"
        watch_alerts.append({
            "ticker": wa_ticker,
            "name": wa_info.get("name", wa_ticker),
            "buy_price": buy_price,
            "cur_price": cur,
            "gap_pct": gap_pct,
            "triggered": cur > 0 and cur <= buy_price,
            "grade": grade,
            "market": mkt,
            "memo": _clean_memo(wa_info.get("memo", "")),
            "created": wa_info.get("created", ""),
            "updated_at": wa_info.get("updated_at", ""),
        })
    # ── 투자판단/비교 최근 기록 ──
    dec_log = load_decision_log()
    recent_decisions = sorted(dec_log.values(), key=lambda x: x.get("date", ""), reverse=True)[:3]
    cmp_log = load_compare_log()
    if not isinstance(cmp_log, list):
        cmp_log = []
    recent_compares = cmp_log[-3:][::-1]
    brief = arguments.get("brief", False)
    if isinstance(brief, str):
        brief = brief.lower() in ("true", "1", "yes")

    if brief:
        alerts = [{"ticker": a["ticker"], "name": a["name"],
                   "gap_pct": a.get("gap_pct"), "target_pct": a.get("target_pct")}
                  for a in alerts]
        watch_alerts = [{"ticker": w["ticker"], "name": w["name"],
                         "buy_price": w["buy_price"], "cur_price": w["cur_price"],
                         "gap_pct": w["gap_pct"], "triggered": w["triggered"]}
                        for w in watch_alerts]
        recent_decisions = recent_decisions[:1]
        for d in recent_decisions:
            for k in ("notes", "watchlist", "changelog"):
                d.pop(k, None)
        result = {
            "alerts": alerts,
            "watch_alerts": watch_alerts,
            "recent_decisions": recent_decisions,
        }
    else:
        result = {
            "alerts": alerts,
            "watch_alerts": watch_alerts,
            "recent_decisions": recent_decisions,
            "recent_compares": recent_compares,
            "recent_changelog": load_watchlist_log()[-20:],
        }

    return result


async def handle_set_alert(arguments: dict) -> dict | list:
    result = None
    log_type     = arguments.get("log_type", "").strip().lower()
    ticker       = arguments.get("ticker", "").strip().upper()
    aname        = arguments.get("name", ticker).strip()
    stop_price   = float(arguments.get("stop_price", 0) or 0)
    target_price = float(arguments.get("target_price", 0) or 0)
    buy_price    = float(arguments.get("buy_price", 0) or 0)
    memo         = _clean_memo(arguments.get("memo", "") or "")

    if log_type == "decision":
        # ── 투자판단 기록 모드 ──
        date   = (arguments.get("date") or datetime.now(KST).strftime("%Y-%m-%d")).strip()
        regime = arguments.get("regime", "").strip()
        grades_raw = arguments.get("grades") or {}
        grades = {}
        for gk, gv in grades_raw.items():
            if isinstance(gv, str):
                grades[gk] = gv
            elif isinstance(gv, dict):
                obj = {"grade": gv.get("grade", "")}
                if gv.get("change"):
                    obj["change"] = gv["change"]
                if gv.get("reason"):
                    obj["reason"] = gv["reason"]
                grades[gk] = obj
            else:
                grades[gk] = gv
        actions  = arguments.get("actions") or []
        watchlist_dec = arguments.get("watchlist") or []
        notes  = arguments.get("notes", "").strip()
        log = load_decision_log()
        entry = {
            "date": date, "regime": regime,
            "grades": grades, "actions": actions,
            "watchlist": watchlist_dec, "notes": notes,
            "saved_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        }
        log[date] = entry
        # 365일 초과 항목 정리
        if len(log) > 365:
            sorted_dates = sorted(log.keys())
            for old_date in sorted_dates[:-365]:
                del log[old_date]
        save_json(DECISION_LOG_FILE, log)
        result = {"ok": True, "message": f"{date} 투자판단 저장됨", "date": date}

    elif log_type == "trade":
        # ── 매매 기록 모드 ──
        side  = arguments.get("side", "").strip().lower()
        qty   = int(arguments.get("qty", 0) or 0)
        price = float(arguments.get("price", 0) or 0)
        grade = arguments.get("grade", "").strip().upper()
        reason = arguments.get("reason", "").strip()
        date  = (arguments.get("date") or datetime.now(KST).strftime("%Y-%m-%d")).strip()
        tgt_t = float(arguments.get("target_price", 0) or 0)
        stp_t = float(arguments.get("stop_price", 0) or 0)
        if not ticker or not side or qty <= 0 or price <= 0:
            result = {"error": "ticker, side, qty, price는 필수입니다"}
        elif side not in ("buy", "sell"):
            result = {"error": "side는 'buy' 또는 'sell' 이어야 합니다"}
        else:
            trades = load_trade_log()
            trade_id = f"T{len(trades) + 1:03d}"
            market = "US" if _is_us_ticker(ticker) else "KR"
            entry = {
                "id": trade_id, "ticker": ticker, "name": aname,
                "market": market, "side": side, "qty": qty,
                "price": price, "date": date,
                "grade_at_trade": grade, "reason": reason,
            }
            if side == "buy":
                if tgt_t: entry["target_price"] = tgt_t
                if stp_t: entry["stop_price"]   = stp_t
                entry["linked_buy_id"] = None
            else:  # sell
                linked_buy = next(
                    (t for t in reversed(trades) if t["ticker"] == ticker and t["side"] == "buy"),
                    None,
                )
                entry["linked_buy_id"] = linked_buy["id"] if linked_buy else None
                if linked_buy:
                    buy_p = float(linked_buy["price"])
                    calc_qty = min(qty, int(linked_buy.get("qty", qty)))
                    pnl = round((price - buy_p) * calc_qty, 2)
                    pnl_pct = round((price - buy_p) / buy_p * 100, 2) if buy_p else 0
                    entry["pnl"]     = pnl
                    entry["pnl_pct"] = pnl_pct
                    entry["result"]  = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")
                    try:
                        from datetime import datetime as _ddt
                        bd = linked_buy.get("date", "")
                        if bd:
                            entry["holding_days"] = (_ddt.strptime(date, "%Y-%m-%d") - _ddt.strptime(bd, "%Y-%m-%d")).days
                    except Exception:
                        pass
            trades.append(entry)
            save_trade_log(trades)
            pnl_str = f" | 손익 {entry.get('pnl', 0):+,.0f}" if "pnl" in entry else ""
            fmt_p = f"${price:,.2f}" if market == "US" else f"{price:,.0f}원"
            result = {"ok": True,
                      "message": f"{aname}({ticker}) {side} {qty}주 @{fmt_p} 기록됨{pnl_str}",
                      "trade_id": trade_id}

    elif log_type == "compare":
        # ── 종목비교 스냅샷 모드 ──
        held_ticker      = arguments.get("held_ticker", "").strip().upper()
        candidate_ticker = arguments.get("candidate_ticker", "").strip().upper()
        held_score       = float(arguments.get("held_score", 0) or 0)
        candidate_score  = float(arguments.get("candidate_score", 0) or 0)
        reasoning        = arguments.get("reasoning", "").strip()
        compare_memo     = _clean_memo(arguments.get("memo", "") or "")
        log = load_compare_log()
        if not isinstance(log, list):
            log = []
        entry = {
            "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
            "held": held_ticker, "candidate": candidate_ticker,
            "held_score": held_score, "candidate_score": candidate_score,
            "reasoning": reasoning, "memo": compare_memo,
        }
        log.append(entry)
        log = log[-50:]   # 최대 50건 보관
        save_json(COMPARE_LOG_FILE, log)
        verdict = "교체 권장" if candidate_score > held_score else "보유 유지"
        result = {"ok": True, "message": f"{held_ticker} vs {candidate_ticker} 비교 저장됨 ({verdict})", "verdict": verdict}

    elif log_type == "delete":
        # ← 기존 delete_alert 핸들러
        if not ticker:
            result = {"error": "ticker는 필수입니다"}
        else:
            stops = load_stoploss()
            if _is_us_ticker(ticker):
                us = stops.get("us_stocks", {})
                if ticker not in us:
                    result = {"ok": False, "message": "해당 종목 알림이 없습니다"}
                else:
                    entry = us.pop(ticker)
                    stops["us_stocks"] = us
                    save_json(STOPLOSS_FILE, stops)
                    append_watchlist_log({
                        "date": datetime.now(KST).strftime("%Y-%m-%d"),
                        "action": "delete_alert",
                        "ticker": ticker,
                        "name": entry.get("name", ticker),
                        "stop_price": entry.get("stop_price"),
                        "target_price": entry.get("target_price"),
                    })
                    result = {"ok": True, "message": f"{entry.get('name', ticker)}({ticker}) 알림 삭제됨"}
            else:
                if ticker not in stops:
                    result = {"ok": False, "message": "해당 종목 알림이 없습니다"}
                else:
                    entry = stops.pop(ticker)
                    save_json(STOPLOSS_FILE, stops)
                    asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
                    append_watchlist_log({
                        "date": datetime.now(KST).strftime("%Y-%m-%d"),
                        "action": "delete_alert",
                        "ticker": ticker,
                        "name": entry.get("name", ticker),
                        "stop_price": entry.get("stop_price"),
                        "target_price": entry.get("target_price"),
                    })
                    result = {"ok": True, "message": f"{entry.get('name', ticker)}({ticker}) 알림 삭제됨"}

    elif not ticker or not aname:
        result = {"error": "ticker와 name은 필수입니다"}

    elif buy_price <= 0 and stop_price <= 0 and target_price <= 0:
        # ── grade만 단독 업데이트 모드 ──
        wa = load_watchalert()
        _g = (arguments.get("watch_grade") or arguments.get("grade") or "").strip().upper()
        if ticker in wa and _g in ("A", "B+", "B", "B-", "C+", "C", "D"):
            wa[ticker]["grade"] = _g
            wa[ticker]["updated_at"] = datetime.now(KST).strftime("%Y-%m-%d")
            save_json(WATCHALERT_FILE, wa)
            result = {"ok": True, "message": f"{aname}({ticker}) grade → {_g}"}
        elif ticker not in wa:
            result = {"error": f"{ticker} 매수감시 항목 없음. buy_price와 함께 등록하세요."}
        else:
            result = {"error": f"유효하지 않은 grade: {_g}. A/B+/B/B-/C+/C/D 중 하나"}

    elif buy_price > 0:
        # ── 매수감시 모드 ──
        wa = load_watchalert()
        old = wa.get(ticker, {})
        old_price = old.get("buy_price", None)
        log_action = "update" if old_price else "add"
        now_str = datetime.now(KST).strftime("%Y-%m-%d")
        # grade: watch_grade 또는 grade 파라미터, 없으면 기존 유지
        watch_grade = (arguments.get("watch_grade") or arguments.get("grade") or "").strip().upper()
        if watch_grade not in ("A", "B+", "B", "B-", "C+", "C", "D", ""):
            watch_grade = ""
        if not watch_grade:
            watch_grade = old.get("grade", "")
        # market 자동 감지
        if re.match(r"^\d{6}$", ticker):
            mkt = "KR"
        elif re.match(r"^[A-Z]+$", ticker):
            mkt = "US"
        else:
            mkt = "KR"
        entry = {
            "name": aname,
            "buy_price": buy_price,
            "memo": memo,
            "grade": watch_grade,
            "market": mkt,
            "created_at": old.get("created_at", now_str),
            "updated_at": now_str,
            # 하위호환: 기존 created 필드 유지
            "created": old.get("created") or datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        }
        if log_action == "add":
            entry["created_at"] = now_str
        wa[ticker] = entry
        save_json(WATCHALERT_FILE, wa)
        asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
        append_watchlist_log({
            "date": datetime.now(KST).strftime("%Y-%m-%d"),
            "action": log_action,
            "ticker": ticker, "name": aname,
            "buy_price": buy_price, "old_price": old_price, "reason": memo,
        })
        if _is_us_ticker(ticker):
            msg = f"{aname}({ticker}) 매수감시 ${buy_price:,.2f} 등록됨"
        else:
            msg = f"{aname}({ticker}) 매수감시 {buy_price:,.0f}원 등록됨"
        if memo:
            msg += f" | 메모: {memo}"
        result = {"ok": True, "message": msg, "total_watch": len(wa)}
    elif stop_price > 0:
        # ── 손절가 등록 모드 ──
        stops = load_stoploss()
        if _is_us_ticker(ticker):
            us = stops.get("us_stocks", {})
            us[ticker] = {"name": aname, "stop_price": stop_price, "target_price": target_price}
            stops["us_stocks"] = us
            save_json(STOPLOSS_FILE, stops)
            result = {
                "ok": True,
                "message": f"{aname}({ticker}) 손절가 ${stop_price:,.2f} 저장됨"
                           + (f", 목표가 ${target_price:,.2f}" if target_price else ""),
            }
        else:
            stops[ticker] = {
                "name":         aname,
                "stop_price":   stop_price,
                "entry_price":  stops.get(ticker, {}).get("entry_price", 0),
                "target_price": target_price,
            }
            save_json(STOPLOSS_FILE, stops)
            asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
            result = {
                "ok": True,
                "message": f"{aname}({ticker}) 손절가 {stop_price:,.0f}원 저장됨"
                           + (f", 목표가 {target_price:,.0f}원" if target_price else ""),
            }
    else:
        result = {"error": "stop_price 또는 buy_price 중 하나는 필수입니다"}

    return result


async def handle_manage_watch(arguments: dict) -> dict | list:
    result = None
    watch_action = arguments.get("action", "").strip().lower()

    if watch_action == "add":
        # watchalert.json 단일 저장소에 기록 (buy_price=0 → "순수 워치" 상태)
        ticker = arguments.get("ticker", "").strip()
        wname  = arguments.get("name", "").strip()
        if not ticker or not wname:
            result = {"error": "ticker와 name은 필수입니다"}
        else:
            wa = load_watchalert()
            today = datetime.now(KST).strftime("%Y-%m-%d")
            # NOTE: MCP 호출자가 명시적 market 인자 추가 권장 (현재는 ticker 형태로 추론).
            # 영문 티커 = US, 숫자 포함 = KR.
            market = arguments.get("market") or ("US" if _is_us_ticker(ticker) else "KR")
            if market not in ("KR", "US"):
                market = "US" if _is_us_ticker(ticker) else "KR"
            prev = wa.get(ticker, {})
            wa[ticker] = {
                "name": wname,
                "market": market,
                "buy_price": float(prev.get("buy_price") or 0.0),
                "qty": int(prev.get("qty") or 0),
                "memo": prev.get("memo", ""),
                "grade": prev.get("grade"),
                "created_at": prev.get("created_at", today),
                "updated_at": today,
            }
            save_json(WATCHALERT_FILE, wa)
            wl = load_watchlist()  # 집계용 (KR 워치 수)
            asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
            append_watchlist_log({
                "date": datetime.now(KST).strftime("%Y-%m-%d"),
                "action": "add",
                "ticker": ticker, "name": wname,
                "buy_price": None, "old_price": None, "reason": "",
            })
            result = {"ok": True, "message": f"{wname}({ticker}) 워치리스트 추가됨", "total": len(wl)}

    elif watch_action == "remove":
        # ← 기존 remove_watch 핸들러
        ticker = arguments.get("ticker", "").strip().upper()
        alert_type = arguments.get("alert_type", "watchlist").strip().lower()
        if not ticker:
            result = {"error": "ticker는 필수입니다"}
        elif alert_type == "buy_alert":
            # ── 매수감시 제거 ──
            wa = load_watchalert()
            if ticker in wa:
                removed = wa.pop(ticker)
                save_json(WATCHALERT_FILE, wa)
                asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
                result = {"ok": True, "message": f"{removed['name']}({ticker}) 매수감시 제거됨", "total_watch": len(wa)}
            else:
                result = {"error": f"{ticker} 매수감시 목록에 없음"}
        else:
            # ── 워치리스트 제거: watchalert 엔트리 처리 ──
            # buy_price>0(매수감시 활성)이면 엔트리는 유지하고 안내 (데이터 보호)
            wa = load_watchalert()
            if ticker in wa:
                entry = wa[ticker]
                removed_name = entry.get("name") or ticker
                if float(entry.get("buy_price") or 0) > 0:
                    result = {
                        "ok": False,
                        "error": f"{removed_name}({ticker})는 매수감시 활성 상태입니다. 먼저 alert_type='buy_alert'로 매수감시를 해제하세요.",
                    }
                else:
                    wa.pop(ticker)
                    save_json(WATCHALERT_FILE, wa)
                    asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
                    append_watchlist_log({
                        "date": datetime.now(KST).strftime("%Y-%m-%d"),
                        "action": "remove",
                        "ticker": ticker, "name": removed_name,
                        "buy_price": None, "old_price": None, "reason": "",
                    })
                    result = {"ok": True, "message": f"{removed_name}({ticker}) 워치리스트 제거됨", "total": len(load_watchlist())}
            else:
                result = {"error": f"{ticker} 워치리스트에 없음"}

    else:
        result = {"error": "action은 'add' 또는 'remove' 이어야 합니다"}

    return result


