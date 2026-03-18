import json
import asyncio
import uuid
import aiohttp
from datetime import datetime, timedelta
from aiohttp import web

from kis_api import *

_mcp_sessions: dict = {}   # session_id → asyncio.Queue

MCP_TOOLS = [
    {"name": "scan_market",    "description": "거래량 상위 종목 스캔",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_portfolio",  "description": "워치리스트 전 종목 현재가·등락률",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_stock_detail","description": "개별 종목 상세: 현재가·PER·PBR·수급 또는 일봉 조회. 한국/미국 자동 판별. period 지정 시 일봉 반환.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드(예: 005930) 또는 미국 티커(예: TSLA, AAPL)"},
                         "period": {"type": "string", "description": "일봉 조회 시 지정 (예: D60=최근 60일, D30=30일, W20=20주). 생략 시 현재가 상세 반환"},
                     },
                     "required": ["ticker"]}},
    {"name": "get_foreign_rank","description": "외국인 순매수 상위 종목",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_dart",       "description": "워치리스트 최근 3일 DART 공시",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_macro",      "description": "KOSPI·KOSDAQ 지수 + USD/KRW 환율",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_sector_flow","description": "WI26 주요 업종별 외국인+기관 순매수금액 상위/하위 3개",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "add_watch",      "description": "한국 워치리스트에 종목 추가",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드 (예: 005930)"},
                         "name":   {"type": "string", "description": "종목명 (예: 삼성전자)"},
                     },
                     "required": ["ticker", "name"]}},
    {"name": "remove_watch",   "description": "한국 워치리스트에서 종목 제거. alert_type='buy_alert' 시 매수감시 제거",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드 (예: 005930) 또는 미국 티커"},
                         "alert_type": {"type": "string", "description": "삭제 대상: 'watchlist'(기본) 또는 'buy_alert'(매수감시 제거)"},
                     },
                     "required": ["ticker"]}},
    {"name": "get_alerts",     "description": "손절가 목록 + 현재가 대비 손절까지 남은 % + 매수감시 목록",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "set_alert",      "description": "손절가/목표가 등록 및 수정. buy_price 입력 시 미보유 종목 매수감시 등록 (가격 도달 시 텔레그램 알림)",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker":       {"type": "string", "description": "종목코드 (예: 034020) 또는 미국 티커 (예: AAPL)"},
                         "name":         {"type": "string", "description": "종목명"},
                         "stop_price":   {"type": "number", "description": "손절가 (매수감시 시 생략 가능, 기본 0)"},
                         "target_price": {"type": "number", "description": "목표가 (선택)"},
                         "buy_price":    {"type": "number", "description": "매수 희망가 — 이 값이 >0이면 매수감시 모드 (이 가격 이하일 때 텔레그램 알림)"},
                         "memo":         {"type": "string", "description": "매수 근거 메모 (매수감시 시 선택)"},
                     },
                     "required": ["ticker", "name"]}},
]


async def _execute_tool(name: str, arguments: dict) -> dict | list:
    """툴 실행 → 결과 반환 (에러 시 {"error": ...})"""
    arguments = arguments or {}
    print(f"툴 호출: {name} {arguments}")
    try:
        token = await get_kis_token()
        if not token:
            raise RuntimeError("KIS 토큰 발급 실패")

        if name == "scan_market":
            rows = await kis_volume_rank_api(token)
            await asyncio.sleep(0.05)
            frgn_rows = await kis_foreigner_trend(token)
            frgn_set = {r.get("mksc_shrn_iscd", "") for r in frgn_rows}
            result = [{"ticker": r.get("mksc_shrn_iscd"), "name": r.get("hts_kor_isnm"),
                       "vol": r.get("acml_vol"), "chg": r.get("prdy_ctrt"),
                       "frgn_buy": r.get("mksc_shrn_iscd") in frgn_set} for r in rows[:15]]

        elif name == "get_portfolio":
            portfolio = load_json(PORTFOLIO_FILE, {})
            kr_stocks = {k: v for k, v in portfolio.items() if k != "us_stocks"}
            us_stocks = portfolio.get("us_stocks", {})
            if not kr_stocks and not us_stocks:
                result = {"message": "포트폴리오가 비어있습니다. /setportfolio 또는 /setusportfolio 로 등록하세요."}
            else:
                kr_holdings, us_holdings = [], []
                kr_eval = kr_cost = us_eval = us_cost = 0

                for ticker, info in kr_stocks.items():
                    qty = info.get("qty", 0)
                    avg = info.get("avg_price", 0)
                    d = await kis_stock_price(ticker, token)
                    cur = int(d.get("stck_prpr", 0) or 0)
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
                        "chg_today": d.get("prdy_ctrt"),
                    })

                for symbol, info in us_stocks.items():
                    qty = info.get("qty", 0)
                    avg = info.get("avg_price", 0)
                    d = await kis_us_stock_price(symbol, token)
                    cur = float(d.get("last", 0) or d.get("stck_prpr", 0) or 0)
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
                        "chg_today": d.get("rate"),
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
                }

        elif name == "get_stock_detail":
            ticker = arguments.get("ticker", "005930").strip().upper()
            period = arguments.get("period", "").strip().upper()  # e.g. "D60", "W20"

            if period:
                # ── 일봉/주봉 조회 모드 ──
                period_type = period[0] if period else "D"  # D/W/M
                try:
                    n = int(period[1:])
                except ValueError:
                    n = 60
                today_str = datetime.now(KST).strftime("%Y%m%d")
                buffer = {"D": 2, "W": 8, "M": 40}.get(period_type, 2)
                start_dt = (datetime.now(KST) - timedelta(days=n * buffer)).strftime("%Y%m%d")

                if _is_us_ticker(ticker):
                    excd = _guess_excd(ticker)
                    async with aiohttp.ClientSession() as s:
                        _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/dailyprice",
                            "HHDFS76240000", token,
                            {"AUTH": "", "EXCD": excd, "SYMB": ticker,
                             "GUBN": "0", "BYMD": today_str, "MODP": "0"})
                    candles = d.get("output2", [])
                    result = {
                        "ticker": ticker, "market": "US", "period": period,
                        "candles": [{"date": c.get("xymd"), "open": c.get("open"),
                                     "high": c.get("high"), "low": c.get("low"),
                                     "close": c.get("clos"), "vol": c.get("tvol")}
                                    for c in candles[:n]],
                    }
                else:
                    async with aiohttp.ClientSession() as s:
                        _, d = await _kis_get(s,
                            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                            "FHKST03010100", token,
                            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
                             "FID_INPUT_DATE_1": start_dt, "FID_INPUT_DATE_2": today_str,
                             "FID_PERIOD_DIV_CODE": period_type, "FID_ORG_ADJ_PRC": "0"})
                    candles = d.get("output2", [])
                    result = {
                        "ticker": ticker, "market": "KR", "period": period,
                        "candles": [{"date": c.get("stck_bsop_date"),
                                     "open": c.get("stck_oprc"), "high": c.get("stck_hgpr"),
                                     "low": c.get("stck_lwpr"), "close": c.get("stck_clpr"),
                                     "vol": c.get("acml_vol")}
                                    for c in candles[:n]],
                    }

            elif _is_us_ticker(ticker):
                # ── 미국 주식 ──
                excd = _guess_excd(ticker)
                price_d = await kis_us_stock_price(ticker, token, excd)
                detail_d = await kis_us_stock_detail(ticker, token, excd)
                cur = float(price_d.get("last", 0) or 0)
                base = float(price_d.get("base", 0) or 0)
                result = {
                    "ticker": ticker, "market": "US",
                    "price": cur,
                    "chg_pct": float(price_d.get("rate", 0) or 0),
                    "volume": int(price_d.get("tvol", 0) or 0),
                    "open": float(detail_d.get("open", 0) or 0),
                    "high": float(detail_d.get("high", 0) or 0),
                    "low": float(detail_d.get("low", 0) or 0),
                    "prev_close": base,
                    "w52h": float(detail_d.get("h52p", 0) or 0),
                    "w52l": float(detail_d.get("l52p", 0) or 0),
                    "per": float(detail_d.get("perx", 0) or 0) or None,
                    "pbr": float(detail_d.get("pbrx", 0) or 0) or None,
                    "eps": float(detail_d.get("epsx", 0) or 0) or None,
                    "market_cap": detail_d.get("tomv", ""),
                    "sector": detail_d.get("e_icod", ""),
                }
            else:
                # ── 한국 주식 ──
                price = await kis_stock_price(ticker, token)
                info  = await kis_stock_info(ticker, token)
                inv   = await kis_investor_trend(ticker, token)
                result = {
                    "ticker": ticker, "market": "KR",
                    "price": price.get("stck_prpr"), "chg": price.get("prdy_ctrt"),
                    "vol": price.get("acml_vol"),
                    "w52h": price.get("w52_hgpr"), "w52l": price.get("w52_lwpr"),
                    "per": info.get("per"), "pbr": info.get("pbr"), "eps": info.get("eps"),
                    "investor": inv[:3] if isinstance(inv, list) else inv,
                }

        elif name == "get_foreign_rank":
            try:
                rows = await kis_foreigner_trend(token)
                if not rows:
                    result = {"error": "데이터 없음", "items": []}
                else:
                    result = [
                        {
                            "ticker": r.get("mksc_shrn_iscd", ""),
                            "name": r.get("hts_kor_isnm", ""),
                            "net_buy": r.get("frgn_ntby_qty", "0"),
                        }
                        for r in rows[:15]
                    ]
            except Exception as e:
                result = {"error": str(e), "items": []}

        elif name == "get_dart":
            disclosures = await search_dart_disclosures(days_back=3)
            wl = load_watchlist()
            important = filter_important_disclosures(disclosures, list(wl.values()))
            def _dart_importance(title: str) -> str:
                if any(k in title for k in ["유상증자", "전환사채", "신주인수권부사채", "분할", "합병", "공개매수"]):
                    return "긴급"
                if any(k in title for k in ["수주", "계약", "대규모", "공급계약", "납품"]):
                    return "주의"
                if any(k in title for k in ["임원", "지분", "자사주", "배당"]):
                    return "참고"
                return "일반"
            result = [{"corp": d.get("corp_name"), "title": d.get("report_nm"),
                       "date": d.get("rcept_dt"),
                       "importance": _dart_importance(d.get("report_nm", ""))}
                      for d in important[:10]]

        elif name == "get_macro":
            kospi  = await get_kis_index(token, "0001")
            kosdaq = await get_kis_index(token, "1001")
            usd    = await get_yahoo_quote("USDKRW=X")
            result = {
                "kospi":  {"index": kospi.get("bstp_nmix_prpr"),  "chg": kospi.get("bstp_nmix_prdy_ctrt")},
                "kosdaq": {"index": kosdaq.get("bstp_nmix_prpr"), "chg": kosdaq.get("bstp_nmix_prdy_ctrt")},
                "usd_krw": {"price": usd.get("price") if usd else None,
                            "chg_pct": usd.get("change_pct") if usd else None},
            }

        elif name == "get_sector_flow":
            today = datetime.now().strftime("%Y%m%d")
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
                note = "업종별 투자자 API 미지원 — 외국인 순매수 상위 기반 근사치(수량)"

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
                    async with aiohttp.ClientSession() as s:
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

        elif name == "get_alerts":
            stops = load_stoploss()
            kr_stops = {k: v for k, v in stops.items() if k != "us_stocks"}
            us_stops = stops.get("us_stocks", {})
            alerts = []
            for ticker, info in kr_stops.items():
                stop   = info.get("stop_price", 0)
                entry  = info.get("entry_price", 0)
                target = info.get("target_price", 0)
                cur = 0
                try:
                    d = await kis_stock_price(ticker, token)
                    cur = int(d.get("stck_prpr", 0) or 0)
                except Exception:
                    pass
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
            for sym, info in us_stops.items():
                stop   = info.get("stop_price", 0)
                target = info.get("target_price", 0)
                cur = 0.0
                try:
                    d = await get_yahoo_quote(sym)
                    cur = float(d.get("price", 0) or 0) if d else 0.0
                except Exception:
                    pass
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
            wa = load_watchalert()
            watch_alerts = []
            for wa_ticker, wa_info in wa.items():
                buy_price = wa_info.get("buy_price", 0)
                cur = 0.0
                try:
                    if _is_us_ticker(wa_ticker):
                        d = await kis_us_stock_price(wa_ticker, token)
                        cur = float(d.get("last", 0) or 0)
                    else:
                        d = await kis_stock_price(wa_ticker, token)
                        cur = int(d.get("stck_prpr", 0) or 0)
                except Exception:
                    pass
                gap_pct = round((cur - buy_price) / buy_price * 100, 2) if buy_price else None
                watch_alerts.append({
                    "ticker": wa_ticker,
                    "name": wa_info.get("name", wa_ticker),
                    "buy_price": buy_price,
                    "cur_price": cur,
                    "gap_pct": gap_pct,
                    "triggered": cur > 0 and cur <= buy_price,
                    "memo": wa_info.get("memo", ""),
                    "created": wa_info.get("created", ""),
                })
            result = {"alerts": alerts, "watch_alerts": watch_alerts}

        elif name == "set_alert":
            ticker       = arguments.get("ticker", "").strip().upper()
            aname        = arguments.get("name", ticker).strip()
            stop_price   = float(arguments.get("stop_price", 0) or 0)
            target_price = float(arguments.get("target_price", 0) or 0)
            buy_price    = float(arguments.get("buy_price", 0) or 0)
            memo         = arguments.get("memo", "").strip() if arguments.get("memo") else ""

            if not ticker or not aname:
                result = {"error": "ticker와 name은 필수입니다"}
            elif buy_price > 0:
                # ── 매수감시 모드 ──
                wa = load_watchalert()
                wa[ticker] = {
                    "name": aname,
                    "buy_price": buy_price,
                    "memo": memo,
                    "created": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                }
                save_json(WATCHALERT_FILE, wa)
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
                    result = {
                        "ok": True,
                        "message": f"{aname}({ticker}) 손절가 {stop_price:,.0f}원 저장됨"
                                   + (f", 목표가 {target_price:,.0f}원" if target_price else ""),
                    }
            else:
                result = {"error": "stop_price 또는 buy_price 중 하나는 필수입니다"}

        elif name == "add_watch":
            ticker = arguments.get("ticker", "").strip()
            wname  = arguments.get("name", "").strip()
            if not ticker or not wname:
                result = {"error": "ticker와 name은 필수입니다"}
            else:
                wl = load_watchlist()
                wl[ticker] = wname
                save_json(WATCHLIST_FILE, wl)
                result = {"ok": True, "message": f"{wname}({ticker}) 워치리스트 추가됨", "total": len(wl)}

        elif name == "remove_watch":
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
                    result = {"ok": True, "message": f"{removed['name']}({ticker}) 매수감시 제거됨", "total_watch": len(wa)}
                else:
                    result = {"error": f"{ticker} 매수감시 목록에 없음"}
            else:
                # ── 워치리스트 제거 ──
                wl = load_watchlist()
                if ticker in wl:
                    removed = wl.pop(ticker)
                    save_json(WATCHLIST_FILE, wl)
                    result = {"ok": True, "message": f"{removed}({ticker}) 워치리스트 제거됨", "total": len(wl)}
                else:
                    result = {"error": f"{ticker} 워치리스트에 없음"}

        else:
            result = {"error": f"unknown tool: {name}"}

    except Exception as e:
        result = {"error": str(e)}
        print(f"에러: {name} → {e}")

    print(f"툴 결과: {name} → {json.dumps(result, ensure_ascii=False)[:200]}")
    return result


async def _handle_jsonrpc(body: dict) -> dict | None:
    """JSON-RPC 요청 처리 → 응답 dict (notification이면 None)"""
    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "kis-stock-bot", "version": "1.0.0"},
        }}

    if method.startswith("notifications/"):
        return None  # notification은 응답 없음

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS, "nextCursor": None}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        result = await _execute_tool(tool_name, tool_args)
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
        }}

    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


async def mcp_sse_handler(request: web.Request) -> web.StreamResponse:
    """GET /mcp  → SSE 스트림 수립, endpoint 이벤트 전송"""
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _mcp_sessions[session_id] = queue
    print(f"SSE 연결됨: {session_id}")

    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
    })
    await resp.prepare(request)

    # 클라이언트에 메시지 POST URL 전달
    await resp.write(
        ("event: endpoint\n"
         f"data: /mcp/messages?sessionId={session_id}\n\n").encode()
    )

    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30)
                if msg is None:
                    break
                data = json.dumps(msg, ensure_ascii=False)
                await resp.write(
                    ("event: message\n" + f"data: {data}\n\n").encode()
                )
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception as e:
        print(f"에러: SSE [{session_id}] {e}")
    finally:
        _mcp_sessions.pop(session_id, None)
        print(f"SSE 종료: {session_id}")

    return resp


async def mcp_messages_handler(request: web.Request) -> web.Response:
    """POST /mcp/messages?sessionId=UUID  → JSON-RPC 수신 후 SSE로 응답"""
    session_id = request.rel_url.query.get("sessionId")
    queue = _mcp_sessions.get(session_id)
    if not queue:
        return web.json_response({"error": "session not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    response = await _handle_jsonrpc(body)
    if response is not None:
        await queue.put(response)

    return web.Response(status=202, text="Accepted")
