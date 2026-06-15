"""dashboard_home/payloads.py — 네트워크/DB payload 빌더 (P3 박리).

build_home_payload, build_market_payload, build_macro_panel_payload,
_build_portfolio_with_grand, _build_watch_payload, _build_signals_payload,
_build_supply_payload, _build_alpha_payload, _build_us_*_payload, 기록 빌더 등.
"""

import re
import time
import asyncio
import sqlite3 as _sqlite3
from datetime import datetime, date, timedelta, timezone

from aiohttp import web

import json

import os

from kis_api import (
    load_json,
    load_stoploss,
    load_watchalert,
    load_watchlist,
    load_dart_seen,
    load_events,
    load_decision_log,
    get_yahoo_quote,
    get_trade_stats,
    load_signal_feed,
    get_kis_index,
    CONSENSUS_CACHE_FILE,
    DART_SEEN_FILE,
    EVENTS_FILE,
    DECISION_LOG_FILE,
    PORTFOLIO_HISTORY_FILE,
    _DATA_DIR,
    KST,
)
from mcp_tools import execute_tool

from ._helpers import _cached, _tool_err, _api, _open_db

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# build_home_payload — 홈 집계 (부분 실패 허용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _regime_color(regime_en: str) -> str:
    """레짐 라벨을 Tailwind 색 클래스로 변환."""
    if regime_en == "offensive":
        return "green"
    if regime_en == "crisis":
        return "red"
    return "amber"


def _parse_events_upcoming(events: dict, max_items: int = 5) -> list:
    """events.json에서 오늘 이후 임박 이벤트 추출.

    W4: 이모지 접두사(🚨, ✅ 등)가 붙은 값도 처리.
    re.search로 값 어디든 박힌 ISO 날짜(YYYY-MM-DD)를 추출.
    매칭 없는 항목(---구분자, 2026-07-하순 등 비ISO)은 자연 제외.
    D-day 오름차순 정렬 후 max_items 반환.
    """
    today_date = datetime.now(KST).date()
    today_str = today_date.strftime("%Y-%m-%d")
    items = []
    for name, value in events.items():
        if not isinstance(value, str):
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", value)
        if not m:
            continue
        raw_date = m.group(1)
        if raw_date < today_str:
            continue
        try:
            event_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        dday = (event_date - today_date).days
        items.append({"name": name, "date": raw_date, "dday": dday})
    items.sort(key=lambda x: x["dday"])
    return items[:max_items]


async def build_home_payload() -> dict:
    """홈 화면용 집계 payload. 각 소스 개별 try/except로 부분 실패 허용."""
    payload: dict = {}
    errors: list = []

    # 1. regime — W1: 에러 dict 반환 시 가짜 neutral 만들지 않고 키 omit
    try:
        rdata = await execute_tool("get_regime", {"mode": "current"})
        if _tool_err(rdata):
            errors.append({"source": "regime", "msg": rdata["error"]})
        else:
            regime_en = rdata.get("regime_en", "neutral")
            payload["regime"] = {
                "label": rdata.get("regime", regime_en),
                "regime_en": regime_en,
                "color": _regime_color(regime_en),
                "days_in_regime": rdata.get("debounce", {}).get("days"),
            }
    except Exception as e:
        errors.append({"source": "regime", "msg": str(e)})

    # 2. portfolio summary — W1: 에러 dict 감지
    try:
        pdata = await execute_tool("get_portfolio", {})
        if _tool_err(pdata):
            errors.append({"source": "portfolio", "msg": pdata["error"]})
        elif "kr" in pdata or "us" in pdata:
            kr_sum = pdata.get("kr", {}).get("summary", {})
            us_sum = pdata.get("us", {}).get("summary", {})
            payload["portfolio"] = {
                "kr_eval": kr_sum.get("total_eval", 0),
                "kr_pnl": kr_sum.get("total_pnl", 0),
                "kr_pnl_pct": kr_sum.get("total_pnl_pct", 0),
                "us_eval": us_sum.get("total_eval", 0),
                "us_pnl": us_sum.get("total_pnl", 0),
                "us_pnl_pct": us_sum.get("total_pnl_pct", 0),
                "cash_krw": pdata.get("cash_krw", 0),
                "cash_usd": pdata.get("cash_usd", 0),
            }
        else:
            payload["portfolio"] = {"empty": True}
    except Exception as e:
        errors.append({"source": "portfolio", "msg": str(e)})

    # 3. alerts — W1: 에러 dict 감지 / I1: 손절 근접 필터+정렬 교정
    # gap_pct 부호 규약: (stop_price - cur) / cur * 100
    #   양수  = 현재가가 손절가 아래(이탈)          → 가장 위험
    #   0 근처= 손절가에 근접                       → 위험
    #   큰 음수= 손절가가 현재가에서 멀리 아래(안전) → 제외
    # 손절 근접 조건: gap_pct >= -10 (손절가 10% 이내 또는 이탈만 표시)
    # 정렬: 내림차순(양수/큰 값 = 가장 위험이 맨 위)
    try:
        adata = await execute_tool("get_alerts", {"brief": True})
        if _tool_err(adata):
            errors.append({"source": "alerts", "msg": adata["error"]})
        else:
            raw_stops = adata.get("alerts", [])
            raw_watch = adata.get("watch_alerts", [])
            # 손절: gap_pct >= -10 (손절가 10% 이내 근접 or 이미 이탈), 내림차순(가장 위험 먼저)
            # gap_pct < -10 인 안전 종목(SK하이닉스 -66% 등)은 제외
            stoploss_near = sorted(
                [a for a in raw_stops if a.get("gap_pct") is not None and a["gap_pct"] >= -10],
                key=lambda x: x["gap_pct"],
                reverse=True,
            )[:5]
            # 워치: triggered 또는 gap_pct 0~5% (희망가 5% 이내), triggered 먼저 → gap_pct 오름차순
            watch_near = sorted(
                [
                    w for w in raw_watch
                    if w.get("triggered")
                    or (w.get("gap_pct") is not None and 0 <= w["gap_pct"] <= 5)
                ],
                key=lambda x: (not x.get("triggered", False), x.get("gap_pct") if x.get("gap_pct") is not None else float("inf")),
            )[:5]
            payload["alerts"] = {
                "stoploss": stoploss_near,
                "watch": watch_near,
            }
    except Exception as e:
        errors.append({"source": "alerts", "msg": str(e)})

    # 4. events (오늘 이후 임박)
    try:
        events = load_events()
        payload["events"] = _parse_events_upcoming(events, max_items=5)
    except Exception as e:
        errors.append({"source": "events", "msg": str(e)})

    # 5. consensus (prev_avg 대비 변동 상위 N)
    # W3: abs(chg_pct) > 30 제외 — 액면분할/TP base 리셋 노이즈 차단
    try:
        cc = load_json(CONSENSUS_CACHE_FILE, {})
        kr = cc.get("kr", {})
        changed = []
        for ticker, info in kr.items():
            avg = info.get("avg", 0) or 0
            prev = info.get("prev_avg", 0) or 0
            if prev > 0 and avg > 0 and avg != prev:
                chg_pct = round((avg - prev) / prev * 100, 1)
                if abs(chg_pct) >= 1.0 and abs(chg_pct) <= 30:
                    changed.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "avg": avg,
                        "prev_avg": prev,
                        "chg_pct": chg_pct,
                    })
        changed.sort(key=lambda x: abs(x["chg_pct"]), reverse=True)
        if changed:
            payload["consensus"] = changed[:5]
    except Exception as e:
        errors.append({"source": "consensus", "msg": str(e)})

    # 6. scan — change_scan_sent.json 최근 날짜 + 건수
    # I2: os 직접 사용 불필요 — _DATA_DIR이 모듈 상단에서 이미 import됨
    try:
        scan_file = f"{_DATA_DIR}/change_scan_sent.json"
        scan_data = load_json(scan_file, {})
        if scan_data:
            dates = [v for v in scan_data.values() if isinstance(v, str)]
            latest_date = max(dates) if dates else None
            payload["scan"] = {"date": latest_date, "count": len(scan_data)}
        else:
            payload["scan"] = {"date": None, "count": 0}
    except Exception as e:
        errors.append({"source": "scan", "msg": str(e)})

    # 7. dart — dart_seen.json 누적 감지 건수 (라벨만, 상세는 시그널 탭)
    # 무거운 get_dart 호출 없이 count만 집계해 홈 응답 속도 유지
    try:
        dart_data = load_json(DART_SEEN_FILE, {"ids": []})
        ids = dart_data.get("ids", [])
        payload["dart"] = {"count": len(ids), "label": f"공시 {len(ids):,}건 누적 감지"}
    except Exception as e:
        errors.append({"source": "dart", "msg": str(e)})

    # 8. signal_feed — 최근 5건 (수급이탈/모멘텀이탈/이상급등 피드)
    try:
        feed = load_signal_feed(limit=5)
        if feed:
            payload["signal_feed"] = list(reversed(feed))
    except Exception as e:
        errors.append({"source": "signal_feed", "msg": str(e)})

    # 9. indices — 홈 상단 지수 띠용 (KOSPI/KOSDAQ/S&P500/NASDAQ)
    # 무거운 movers는 건너뛰고 지수 4개만 빠르게 가져옴
    try:
        macro_r2 = await execute_tool("get_macro", {})
        home_indices = []
        if not _tool_err(macro_r2):
            def _hf(v):
                try:
                    return float(v) if v not in (None, "", "-") else None
                except (TypeError, ValueError):
                    return None
            kp = macro_r2.get("kospi",  {})
            kd = macro_r2.get("kosdaq", {})
            kp_p = _hf(kp.get("index"))
            kd_p = _hf(kd.get("index"))
            if kp_p:
                home_indices.append({"name": "KOSPI",  "price": kp_p, "change_pct": _hf(kp.get("chg")), "market": "KR"})
            if kd_p:
                home_indices.append({"name": "KOSDAQ", "price": kd_p, "change_pct": _hf(kd.get("chg")), "market": "KR"})
        try:
            sp_q2 = await get_yahoo_quote("^GSPC")
            if sp_q2 and sp_q2.get("price"):
                home_indices.append({"name": "S&P500", "price": round(float(sp_q2["price"]), 2), "change_pct": round(float(sp_q2.get("change_pct", 0)), 2), "market": "US"})
        except Exception:
            pass
        try:
            nq_q2 = await get_yahoo_quote("^IXIC")
            if nq_q2 and nq_q2.get("price"):
                home_indices.append({"name": "NASDAQ", "price": round(float(nq_q2["price"]), 2), "change_pct": round(float(nq_q2.get("change_pct", 0)), 2), "market": "US"})
        except Exception:
            pass
        if home_indices:
            payload["indices"] = home_indices
    except Exception as e:
        errors.append({"source": "indices", "msg": str(e)})

    payload["_errors"] = errors
    return payload


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# daily_snapshot DB 헬퍼 — KR 등락/거래량 + 가격 폴백
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 세션 수명 동안 캐시 (symbol → close). 장 마감 후 스냅샷은 하루 1회만 바뀜.
_close_cache: dict[str, float | None] = {}
# 최신 trade_date 캐시 (재조회 방지)
_snapshot_date_cache: dict[str, str | None] = {}


def _latest_close(ticker: str) -> float | None:
    """daily_snapshot 최신 close를 반환. 없으면 None.

    동기 함수 — asyncio 이벤트 루프에서 짧게 호출됨. sqlite3 read는
    충분히 빠르므로 thread offload 없이 사용 (Whale 패턴과 동일).
    결과는 세션 메모리 캐시에 저장해 반복 조회를 최소화.
    """
    if ticker in _close_cache:
        return _close_cache[ticker]
    try:
        conn = _sqlite3.connect(f"{_DATA_DIR}/stock.db", timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.execute(
            "SELECT close FROM daily_snapshot WHERE symbol=? ORDER BY trade_date DESC LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
        conn.close()
        val = float(row[0]) if row and row[0] else None
        _close_cache[ticker] = val
        return val
    except Exception:
        _close_cache[ticker] = None
        return None


def _kr_movers_from_db(sort: str, n: int = 10) -> tuple[list[dict], str]:
    """daily_snapshot + stock_master JOIN으로 KR 등락 TOP N을 반환.

    Args:
        sort: "rise" (change_pct DESC) 또는 "fall" (change_pct ASC)
        n: 반환 개수

    Returns:
        (items, as_of) — items: [{ticker, name, price, chg_pct}],
                         as_of: "YYYYMMDD" 형식 최신 trade_date
    """
    order = "DESC" if sort == "rise" else "ASC"
    try:
        conn = _sqlite3.connect(f"{_DATA_DIR}/stock.db", timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        # 최신 trade_date 확정
        dt_row = conn.execute("SELECT MAX(trade_date) FROM daily_snapshot").fetchone()
        if not dt_row or not dt_row[0]:
            conn.close()
            return [], ""
        as_of = dt_row[0]
        rows = conn.execute(
            f"""
            SELECT ds.symbol, sm.name, ds.close, ds.change_pct
            FROM daily_snapshot ds
            JOIN stock_master sm ON ds.symbol = sm.symbol
            WHERE ds.trade_date = ?
              AND ds.close > 0
              AND ABS(ds.change_pct) < 31
              AND sm.name IS NOT NULL
              AND sm.name != ''
            ORDER BY ds.change_pct {order}
            LIMIT ?
            """,
            (as_of, n),
        ).fetchall()
        conn.close()
        items = [
            {
                "ticker": r[0],
                "name": r[1],
                "price": int(r[2]),
                "chg_pct": round(float(r[3]), 2),
                "as_of": as_of,
            }
            for r in rows
        ]
        return items, as_of
    except Exception:
        return [], ""


def _kr_volume_from_db(n: int = 10) -> tuple[list[dict], str]:
    """daily_snapshot 거래량 TOP N (KR). volume DESC."""
    try:
        conn = _sqlite3.connect(f"{_DATA_DIR}/stock.db", timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        dt_row = conn.execute("SELECT MAX(trade_date) FROM daily_snapshot").fetchone()
        if not dt_row or not dt_row[0]:
            conn.close()
            return [], ""
        as_of = dt_row[0]
        rows = conn.execute(
            """
            SELECT ds.symbol, sm.name, ds.close, ds.change_pct, ds.volume
            FROM daily_snapshot ds
            JOIN stock_master sm ON ds.symbol = sm.symbol
            WHERE ds.trade_date = ?
              AND ds.close > 0
              AND ds.volume > 0
              AND sm.name IS NOT NULL
              AND sm.name != ''
            ORDER BY ds.volume DESC
            LIMIT ?
            """,
            (as_of, n),
        ).fetchall()
        conn.close()
        items = [
            {
                "ticker": r[0],
                "name": r[1],
                "price": int(r[2]),
                "chg_pct": round(float(r[3]), 2),
                "volume": int(r[4]),
                "as_of": as_of,
            }
            for r in rows
        ]
        return items, as_of
    except Exception:
        return [], ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KR 섹터 히트맵 DB 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _kr_sector_heatmap_from_db() -> dict:
    """daily_snapshot + stock_master JOIN으로 섹터별 평균 등락률 집계.

    최신 trade_date 기준, sector != '' AND n_stocks >= 3, avg_chg DESC 정렬.
    동기 sqlite3 읽기 — loop에서 짧게 호출.
    """
    try:
        conn = _sqlite3.connect(f"{_DATA_DIR}/stock.db", timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        dt_row = conn.execute("SELECT MAX(trade_date) FROM daily_snapshot").fetchone()
        if not dt_row or not dt_row[0]:
            conn.close()
            return {"date": None, "sectors": []}
        as_of = dt_row[0]
        rows = conn.execute(
            """
            SELECT s.sector, AVG(d.change_pct) avg_chg, COUNT(*) n_stocks
            FROM daily_snapshot d
            JOIN stock_master s ON d.symbol = s.symbol
            WHERE d.trade_date = ?
              AND s.sector IS NOT NULL
              AND TRIM(s.sector) != ''
              AND d.close > 0
            GROUP BY s.sector
            HAVING n_stocks >= 3
            ORDER BY avg_chg DESC
            """,
            (as_of,),
        ).fetchall()
        conn.close()
        sectors = [
            {
                "sector": r[0].strip(),
                "avg_chg": round(float(r[1]), 2),
                "n_stocks": int(r[2]),
            }
            for r in rows
            if r[0] and r[0].strip()
        ]
        return {"date": as_of, "sectors": sectors}
    except Exception:
        return {"date": None, "sectors": []}


async def _build_sector_heatmap_payload() -> dict:
    """섹터 히트맵 payload — 동기 DB 조회를 executor로 래핑."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _kr_sector_heatmap_from_db)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 마켓맵 트리맵 DB 함수 (한경식 ECharts 트리맵용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _kr_marketmap_from_db(market: str = "kospi") -> dict:
    """daily_snapshot + stock_master JOIN으로 섹터별 종목 트리맵 데이터 집계.

    최신 trade_date 기준, 섹터별 시총상위 8종목 + 기타 합산 노드 구성.
    n_stocks >= 3 섹터만 포함. 시총 500억원 이상 필터 (market_cap 단위=억원).
    동기 sqlite3 읽기 — loop에서 run_in_executor로 호출할 것.
    """
    market = market.lower()
    if market not in ("kospi", "kosdaq"):
        market = "kospi"
    try:
        conn = _sqlite3.connect(f"{_DATA_DIR}/stock.db", timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        dt_row = conn.execute("SELECT MAX(trade_date) FROM daily_snapshot").fetchone()
        if not dt_row or not dt_row[0]:
            conn.close()
            return {"market": market, "as_of": None, "total_stocks": 0, "shown_stocks": 0, "data": []}
        as_of = dt_row[0]
        rows = conn.execute(
            """
            WITH latest AS (SELECT ? dt),
            ranked AS (
              SELECT d.symbol, d.close, d.change_pct, d.market_cap,
                     s.name, s.sector, s.market,
                     ROW_NUMBER() OVER (PARTITION BY s.sector ORDER BY d.market_cap DESC) rn
              FROM daily_snapshot d
              JOIN stock_master s ON d.symbol = s.symbol, latest
              WHERE d.trade_date = latest.dt
                AND d.close > 0
                AND d.market_cap > 500
                AND s.market = ?
                AND s.sector IS NOT NULL
                AND TRIM(s.sector) != ''
            )
            SELECT symbol, name, sector, market_cap, change_pct, rn
            FROM ranked
            ORDER BY sector, rn
            """,
            (as_of, market),
        ).fetchall()
        conn.close()

        # 섹터별 그룹화
        from collections import defaultdict
        sector_items: dict = defaultdict(list)
        for sym, name, sector, mktcap, chg_pct, rn in rows:
            sector = (sector or "").strip()
            if not sector:
                continue
            sector_items[sector].append({
                "symbol": sym,
                "name": (name or sym).strip(),
                "market_cap": int(mktcap) if mktcap else 0,
                "change_pct": round(float(chg_pct), 2) if chg_pct is not None else None,
                "rn": int(rn),
            })

        total_stocks = sum(len(v) for v in sector_items.values())
        shown_stocks = 0
        data = []

        for sector, items in sorted(sector_items.items()):
            n = len(items)
            if n < 3:
                continue
            # 섹터 시총 가중 평균 등락률
            total_cap = sum(it["market_cap"] for it in items)
            if total_cap > 0:
                sector_chg = sum(
                    it["change_pct"] * it["market_cap"]
                    for it in items
                    if it["change_pct"] is not None
                ) / total_cap
            else:
                sector_chg = 0.0

            children = []
            # rn <= 8 종목 노드
            top = [it for it in items if it["rn"] <= 8]
            rest = [it for it in items if it["rn"] > 8]
            for it in top:
                children.append({
                    "name": it["name"],
                    "ticker": it["symbol"],
                    "value": it["market_cap"],
                    "change_pct": it["change_pct"],
                })
                shown_stocks += 1
            # 기타 합산 노드
            if rest:
                rest_cap = sum(it["market_cap"] for it in rest)
                children.append({
                    "name": f"기타 ({len(rest)})",
                    "ticker": None,
                    "value": rest_cap,
                    "change_pct": None,
                })

            data.append({
                "name": sector,
                "value": total_cap,
                "change_pct": round(sector_chg, 2),
                "children": children,
            })

        # 섹터 시총 내림차순 정렬
        data.sort(key=lambda x: x["value"], reverse=True)

        return {
            "market": market,
            "as_of": as_of,
            "total_stocks": total_stocks,
            "shown_stocks": shown_stocks,
            "data": data,
        }
    except Exception:
        return {"market": market, "as_of": None, "total_stocks": 0, "shown_stocks": 0, "data": []}


async def _build_marketmap_payload(market: str = "kospi") -> dict:
    """마켓맵 payload — 동기 DB 조회를 executor로 래핑."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _kr_marketmap_from_db, market)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# build_market_payload — 시세 탭 집계 (TTL 240s)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def build_market_payload() -> dict:
    """시세 탭용 집계 payload. 각 소스 개별 try/except — 부분 실패 허용.

    반환:
        indices: [{name, price, change_pct, market}]  — KOSPI/KOSDAQ/S&P500/나스닥
        movers_kr_up:   KR 상승 TOP10 [{ticker, name, price, chg_pct}]
        movers_kr_down: KR 하락 TOP10
        movers_us_up:   US 상승 TOP10 [{ticker, name, price, chg_pct}]
        movers_us_down: US 하락 TOP10
        volume_top:     KR 거래량/체결강도 상위 10 [{ticker, name, price, chg_pct}]
    """
    payload: dict = {}
    errors: list = []

    # 1. 지수 — KOSPI/KOSDAQ: get_macro 기본 모드, S&P500/NASDAQ: Yahoo Finance
    try:
        macro_r = await execute_tool("get_macro", {})
        indices = []
        if not _tool_err(macro_r):
            kospi_data  = macro_r.get("kospi", {})
            kosdaq_data = macro_r.get("kosdaq", {})
            def _safe_float(v):
                try:
                    return float(v) if v not in (None, "", "-") else None
                except (TypeError, ValueError):
                    return None
            kospi_price  = _safe_float(kospi_data.get("index"))
            kospi_chg    = _safe_float(kospi_data.get("chg"))
            kosdaq_price = _safe_float(kosdaq_data.get("index"))
            kosdaq_chg   = _safe_float(kosdaq_data.get("chg"))
            if kospi_price:
                indices.append({"name": "KOSPI",  "price": kospi_price,  "change_pct": kospi_chg,  "market": "KR"})
            if kosdaq_price:
                indices.append({"name": "KOSDAQ", "price": kosdaq_price, "change_pct": kosdaq_chg, "market": "KR"})
        # S&P500 / 나스닥 — Yahoo Finance (별도 try/except)
        try:
            sp_q = await get_yahoo_quote("^GSPC")
            if sp_q and sp_q.get("price"):
                indices.append({
                    "name": "S&P500",
                    "price": round(float(sp_q["price"]), 2),
                    "change_pct": round(float(sp_q.get("change_pct", 0)), 2),
                    "market": "US",
                })
        except Exception:
            pass
        try:
            nq_q = await get_yahoo_quote("^IXIC")
            if nq_q and nq_q.get("price"):
                indices.append({
                    "name": "NASDAQ",
                    "price": round(float(nq_q["price"]), 2),
                    "change_pct": round(float(nq_q.get("change_pct", 0)), 2),
                    "market": "US",
                })
        except Exception:
            pass
        payload["indices"] = indices
    except Exception as e:
        errors.append({"source": "indices", "msg": str(e)})
        payload["indices"] = []

    # 2. KR 상승 TOP10 — daily_snapshot DB 우선, 비면 get_rank 시도
    try:
        db_up, as_of_up = _kr_movers_from_db("rise", 10)
        if db_up:
            payload["movers_kr_up"] = db_up
            payload["movers_kr_as_of"] = as_of_up
        else:
            r = await execute_tool("get_rank", {"type": "price", "market": "all", "sort": "rise", "n": 10})
            if _tool_err(r):
                payload["movers_kr_up"] = []
            else:
                payload["movers_kr_up"] = [
                    {"ticker": x.get("ticker"), "name": x.get("name"), "price": x.get("price"), "chg_pct": x.get("chg_pct")}
                    for x in (r.get("items") or [])
                ]
    except Exception as e:
        errors.append({"source": "movers_kr_up", "msg": str(e)})
        payload["movers_kr_up"] = []

    # 3. KR 하락 TOP10 — daily_snapshot DB 우선, 비면 get_rank 시도
    try:
        db_dn, as_of_dn = _kr_movers_from_db("fall", 10)
        if db_dn:
            payload["movers_kr_down"] = db_dn
            if not payload.get("movers_kr_as_of"):
                payload["movers_kr_as_of"] = as_of_dn
        else:
            await asyncio.sleep(0.3)
            r = await execute_tool("get_rank", {"type": "price", "market": "all", "sort": "fall", "n": 10})
            if _tool_err(r):
                payload["movers_kr_down"] = []
            else:
                payload["movers_kr_down"] = [
                    {"ticker": x.get("ticker"), "name": x.get("name"), "price": x.get("price"), "chg_pct": x.get("chg_pct")}
                    for x in (r.get("items") or [])
                ]
    except Exception as e:
        errors.append({"source": "movers_kr_down", "msg": str(e)})
        payload["movers_kr_down"] = []

    await asyncio.sleep(0.3)

    # 4. US 상승 TOP10 (NAS 기준)
    try:
        r = await execute_tool("get_rank", {"type": "us_price", "exchange": "NAS", "sort": "rise", "n": 10})
        if _tool_err(r):
            payload["movers_us_up"] = []
        else:
            payload["movers_us_up"] = [
                {"ticker": x.get("ticker"), "name": x.get("name"), "price": x.get("price"), "chg_pct": x.get("chg_pct")}
                for x in (r.get("items") or [])
            ]
    except Exception as e:
        errors.append({"source": "movers_us_up", "msg": str(e)})
        payload["movers_us_up"] = []

    await asyncio.sleep(0.3)

    # 5. US 하락 TOP10 (NAS 기준)
    try:
        r = await execute_tool("get_rank", {"type": "us_price", "exchange": "NAS", "sort": "fall", "n": 10})
        if _tool_err(r):
            payload["movers_us_down"] = []
        else:
            payload["movers_us_down"] = [
                {"ticker": x.get("ticker"), "name": x.get("name"), "price": x.get("price"), "chg_pct": x.get("chg_pct")}
                for x in (r.get("items") or [])
            ]
    except Exception as e:
        errors.append({"source": "movers_us_down", "msg": str(e)})
        payload["movers_us_down"] = []

    await asyncio.sleep(0.3)

    # 6. 거래량 상위 (체결강도 volume 모드; 장외이면 daily_snapshot DB 폴백)
    try:
        r = await execute_tool("get_rank", {"type": "volume", "n": 10})
        if _tool_err(r):
            items_raw = []
        else:
            items_raw = r.get("items") or []

        if items_raw:
            payload["volume_top"] = [
                {
                    "ticker":  x.get("ticker"),
                    "name":    x.get("name"),
                    "price":   x.get("price") or x.get("stck_prpr"),
                    "chg_pct": x.get("chg_pct") or x.get("chg"),
                    "volume":  x.get("vol") or x.get("volume"),
                }
                for x in items_raw
            ]
        else:
            # 장외 폴백 — daily_snapshot 거래량 DESC
            db_vol, as_of_vol = _kr_volume_from_db(10)
            payload["volume_top"] = db_vol
            if db_vol and not payload.get("movers_kr_as_of"):
                payload["movers_kr_as_of"] = as_of_vol
    except Exception as e:
        errors.append({"source": "volume_top", "msg": str(e)})
        payload["volume_top"] = []

    payload["_errors"] = errors
    return payload


async def _build_portfolio_with_grand() -> dict:
    """get_portfolio 결과에 원화환산 grand 합계를 추가.

    grand_eval_krw = kr_eval + us_eval * usd_krw
    grand_pnl_krw  = kr_pnl + us_pnl * usd_krw
    USDKRW 환율 실패 시 fallback 1400 사용.
    """
    pdata = await execute_tool("get_portfolio", {})
    if _tool_err(pdata):
        return pdata
    # USDKRW 환율 조회 (실패 허용)
    usd_krw = 1400.0
    try:
        fx = await get_yahoo_quote("USDKRW=X")
        if fx and fx.get("price"):
            usd_krw = float(fx["price"])
    except Exception:
        pass
    kr_sum = pdata.get("kr", {}).get("summary", {})
    us_sum = pdata.get("us", {}).get("summary", {})
    kr_eval = float(kr_sum.get("total_eval", 0) or 0)
    kr_pnl  = float(kr_sum.get("total_pnl", 0) or 0)
    kr_cost = float(kr_sum.get("total_cost", 0) or 0)
    us_eval = float(us_sum.get("total_eval", 0) or 0)
    us_pnl  = float(us_sum.get("total_pnl", 0) or 0)
    us_cost = float(us_sum.get("total_cost", 0) or 0)
    grand_eval_krw = kr_eval + us_eval * usd_krw
    grand_pnl_krw  = kr_pnl  + us_pnl  * usd_krw
    grand_cost_krw = kr_cost + us_cost * usd_krw
    grand_pnl_pct  = round(grand_pnl_krw / grand_cost_krw * 100, 2) if grand_cost_krw else 0
    pdata["usd_krw"]        = round(usd_krw, 2)
    pdata["grand_eval_krw"] = round(grand_eval_krw, 0)
    pdata["grand_pnl_krw"]  = round(grand_pnl_krw, 0)
    pdata["grand_pnl_pct"]  = grand_pnl_pct

    # 현재가 폴백 — KR holdings cur_price가 None/0이면 daily_snapshot 종가로 대체
    # (KIS API 장외 실패 시). price_stale=True 플래그로 프론트 "종가" 표기 가능.
    for h in (pdata.get("kr", {}).get("holdings") or []):
        cur = h.get("cur_price")
        if not cur:  # None or 0
            stale_close = _latest_close(h["ticker"]) if h.get("ticker") else None
            if stale_close:
                h["cur_price"] = stale_close
                h["price_stale"] = True

    return pdata


def _is_us_ticker_simple(ticker: str) -> bool:
    """숫자로만 구성 = KR, 알파벳 포함 = US (간단 판별)."""
    return bool(ticker) and not ticker.isdigit()


async def _build_watch_payload() -> dict:
    """GET /api/watch — load_watchalert() + execute_tool get_alerts(full) 병합.

    반환:
        watchlist: [감시종목 목록] — buy_price=0 포함 전체
        buy_watch: watch_alerts (현재가 포함)  ← execute_tool에서 실시간 현재가 반영
        stoploss_alerts: 손절/목표가 알림 — cur·stop_price·target_price·gap_pct 실값
    """
    wa = load_watchalert()
    # watchlist: watchalert 전체 항목 (순수 감시 + 매수감시 모두)
    watchlist = [
        {
            "ticker": ticker,
            "name": info.get("name", ticker),
            "market": info.get("market", ""),
            "grade": info.get("grade", ""),
            "buy_price": info.get("buy_price", 0),
            "memo": info.get("memo", ""),
            "created_at": info.get("created_at", ""),
        }
        for ticker, info in wa.items()
    ]

    # get_alerts full 호출 — cur/gap_pct/target_pct 확보
    adata: dict = {}
    try:
        adata = await execute_tool("get_alerts", {})
        if _tool_err(adata):
            adata = {}
    except Exception:
        adata = {}

    # buy_watch: watch_alerts (현재가 포함)
    buy_watch = adata.get("watch_alerts", [])
    # cur_price=0/None → daily_snapshot 종가 폴백 (KR 한정)
    for bw in buy_watch:
        bw_ticker = bw.get("ticker", "")
        if bw.get("cur_price") == 0 or bw.get("cur_price") is None:
            if bw_ticker and not _is_us_ticker_simple(bw_ticker):
                stale = _latest_close(bw_ticker)
                if stale:
                    bw["cur_price"] = stale
                    bw["price_stale"] = True
                    # gap_pct 재계산 (buy_price 대비)
                    buy_p = bw.get("buy_price") or 0
                    if buy_p and buy_p > 0:
                        bw["gap_pct"] = round((stale - buy_p) / buy_p * 100, 2)
                    else:
                        bw["gap_pct"] = None
                else:
                    bw["gap_pct"] = None
            else:
                bw["gap_pct"] = None

    # stoploss_alerts: get_alerts.alerts + load_stoploss() 절대가 병합
    raw_alerts = adata.get("alerts", [])
    sl_data = {}
    try:
        sl_raw = load_stoploss()
        # stoploss.json 구조: {ticker: {stop_price, target_price, name}, us_stocks: {ticker: ...}}
        for ticker, info in sl_raw.items():
            if ticker == "us_stocks":
                for us_ticker, us_info in (info or {}).items():
                    sl_data[us_ticker] = us_info
            elif isinstance(info, dict):
                sl_data[ticker] = info
    except Exception:
        pass

    stoploss_alerts = []
    for alert in raw_alerts:
        ticker = alert.get("ticker", "")
        sl_info = sl_data.get(ticker, {})
        is_us = _is_us_ticker_simple(ticker)
        stop_price_raw = sl_info.get("stop_price") or sl_info.get("stop")
        target_price_raw = sl_info.get("target_price") or sl_info.get("target")
        # 0 또는 0.0 = 미설정 → None으로 정규화 (템플릿에서 '-' 표시)
        stop_price = stop_price_raw if stop_price_raw else None
        target_price = target_price_raw if target_price_raw else None
        cur_val = alert.get("cur")
        gap_val = alert.get("gap_pct")
        price_stale = False
        # cur=0이면 프라이싱 실패 → KR이면 daily_snapshot 종가 폴백
        if not cur_val:
            if not is_us:
                stale = _latest_close(ticker)
                if stale:
                    cur_val = stale
                    price_stale = True
                    # gap_pct: stop_price 기준 재계산
                    sp = stop_price_raw if stop_price_raw else None
                    if sp and sp > 0:
                        gap_val = round((stale - sp) / sp * 100, 2)
                    else:
                        gap_val = None
                else:
                    cur_val = None
                    gap_val = None
            else:
                cur_val = None
                gap_val = None
        stoploss_alerts.append({
            "ticker": ticker,
            "name": alert.get("name", sl_info.get("name", ticker)),
            "market": "US" if is_us else "KR",
            "cur": cur_val,
            "stop_price": stop_price,
            "target_price": target_price,
            "gap_pct": gap_val,
            "target_pct": alert.get("target_pct"),
            "price_stale": price_stale,
        })

    return {"watchlist": watchlist, "buy_watch": buy_watch, "stoploss_alerts": stoploss_alerts}


def _fetch_candles_sync(ticker: str) -> list:
    """daily_snapshot에서 최근 ~120영업일 캔들 반환 (동기, run_in_executor용)."""
    try:
        conn = _open_db()
        from datetime import date, timedelta as _td
        cutoff = (date.today() - _td(days=170)).strftime("%Y%m%d")
        rows = conn.execute(
            "SELECT trade_date,open,high,low,close,volume FROM daily_snapshot"
            " WHERE symbol=? AND trade_date>=? ORDER BY trade_date ASC",
            (ticker, cutoff),
        ).fetchall()
        conn.close()
        return [
            {"date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
            for r in rows if r[1] and r[4]  # open/close not null
        ]
    except Exception:
        return []


def _fetch_consensus_history_sync(ticker: str) -> list:
    """consensus_history 최근 1년 반환 (동기, run_in_executor용)."""
    try:
        conn = _open_db()
        from datetime import date, timedelta as _td
        cutoff = (date.today() - _td(days=365)).strftime("%Y%m%d")
        rows = conn.execute(
            "SELECT trade_date,target_avg,target_high,target_low,buy_count,hold_count,sell_count"
            " FROM consensus_history WHERE symbol=? AND trade_date>=? ORDER BY trade_date ASC",
            (ticker, cutoff),
        ).fetchall()
        conn.close()
        return [
            {
                "date": r[0], "target_avg": r[1], "target_high": r[2],
                "target_low": r[3], "buy_count": r[4], "hold_count": r[5], "sell_count": r[6],
            }
            for r in rows
        ]
    except Exception:
        return []


async def _build_decisions_payload() -> dict:
    """decision_log.json → 날짜 내림차순 목록."""
    log = load_decision_log()
    items = sorted(log.values(), key=lambda x: x.get("date", ""), reverse=True)
    return {"items": items}


async def _build_trades_payload() -> dict:
    """get_trade_stats 동기 함수 → 비동기 래퍼."""
    return get_trade_stats("all")


async def _build_invest_todo() -> dict:
    """data/TODO_invest.md 읽어 텍스트 반환."""
    todo_path = os.path.join(_DATA_DIR, "TODO_invest.md")
    try:
        with open(todo_path, encoding="utf-8") as f:
            text = f.read()
        return {"text": text}
    except FileNotFoundError:
        return {"text": ""}
    except Exception as exc:
        return {"error": str(exc), "text": ""}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P4: /api/signals — 시그널 피드 + scan + dart 통합
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _build_signals_payload() -> dict:
    """시그널 탭 통합 payload — 부분 실패 허용.

    keys:
      events  — 오늘 이후 임박 이벤트 최대 20건 (dday 오름차순)
      feed    — signal_feed.json 최근 50건 (최신 먼저)
      scan    — get_change_scan turnaround/fscore_jump/insider_cluster_buy 결과
      dart    — 최근 워치리스트 DART 공시 10건
      consensus — consensus_cache.json 변동 상위 15건 (|chg|<=30 필터)
    """
    result: dict = {}
    errors: list = []

    # 1. events — 오늘 이후 임박 이벤트 최대 20건
    try:
        events = load_events()
        result["events"] = _parse_events_upcoming(events, max_items=20)
    except Exception as e:
        errors.append({"source": "events", "msg": str(e)})
        result["events"] = []

    # 2. feed — signal_feed.json 최근 50건 (최신 먼저)
    try:
        result["feed"] = list(reversed(load_signal_feed(limit=50)))
    except Exception as e:
        errors.append({"source": "feed", "msg": str(e)})
        result["feed"] = []

    # 3. scan — 발굴 스캔 3개 프리셋 (무거우므로 빈 결과면 요약만)
    try:
        scan_r = await execute_tool(
            "get_change_scan",
            {"preset": "turnaround,fscore_jump,insider_cluster_buy", "n": 20},
        )
        if _tool_err(scan_r):
            result["scan"] = {"error": scan_r["error"], "results": []}
        else:
            result["scan"] = {
                "date": scan_r.get("date"),
                "preset": scan_r.get("preset"),
                "preset_description": scan_r.get("preset_description"),
                "total_matched": scan_r.get("total_matched", 0),
                "count": scan_r.get("count", 0),
                "results": [
                    {
                        "ticker": s.get("ticker"),
                        "name": s.get("name"),
                        "market": s.get("market"),
                        "close": s.get("close"),
                        "chg_pct": s.get("chg_pct"),
                        "op_profit_delta": s.get("op_profit_delta"),
                        "fscore_delta": s.get("fscore_delta"),
                        "insider_reprors": s.get("insider_reprors"),
                        "insider_net_qty": s.get("insider_net_qty"),
                    }
                    for s in (scan_r.get("results") or [])
                ],
            }
    except Exception as e:
        errors.append({"source": "scan", "msg": str(e)})
        result["scan"] = {"results": []}

    # 4. dart — 워치리스트 최근 3일 DART 공시 (기본 모드)
    try:
        dart_r = await execute_tool("get_dart", {})
        if _tool_err(dart_r):
            result["dart"] = []
        elif isinstance(dart_r, list):
            result["dart"] = dart_r[:10]
        else:
            result["dart"] = []
    except Exception as e:
        errors.append({"source": "dart", "msg": str(e)})
        result["dart"] = []

    # 5. consensus — consensus_cache.json 변동 상위 15건 (|chg|<=30 필터)
    try:
        cc = load_json(CONSENSUS_CACHE_FILE, {})
        kr = cc.get("kr", {})
        changed = []
        for ticker, info in kr.items():
            avg = info.get("avg", 0) or 0
            prev = info.get("prev_avg", 0) or 0
            if prev > 0 and avg > 0 and avg != prev:
                chg_pct = round((avg - prev) / prev * 100, 1)
                if abs(chg_pct) >= 1.0 and abs(chg_pct) <= 30:
                    changed.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "avg": avg,
                        "prev_avg": prev,
                        "chg_pct": chg_pct,
                    })
        changed.sort(key=lambda x: abs(x["chg_pct"]), reverse=True)
        result["consensus"] = changed[:15]
    except Exception as e:
        errors.append({"source": "consensus", "msg": str(e)})
        result["consensus"] = []

    result["_errors"] = errors
    return result

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 매크로 패널 API (시세 탭 'macro' 서브)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def build_macro_panel_payload() -> dict:
    """매크로 패널 집계 payload. asyncio.gather 병렬 호출, 소스별 개별 try/except.

    반환 구조:
      regime: {label, regime_en, color, days}
      indicators: [{label, value, chg_pct|chg}]   — VIX/DXY/US10Y/WTI/GOLD/SP500/KOSPI/USDKRW
      curve: {y2, y10, spread}
      recession_signal: str | None   — "정상"/"주의 (역전 임박)"/"역전 (침체 선행)"/"데이터 부족"
      recession_prob: float | None   — 숫자 침체확률 (현재 API 미제공, 향후 대비)
      polymarket_fed: [{title, yes_pct, volume_usd}]  최대 3건
      sector_rotation: [{sector, foreign_net, inst_net, combined}]  합산 내림차순, 최대 15
      _errors: [{source, msg}]
    """
    payload: dict = {}
    errors: list = []

    # 병렬 호출 — 느린 polymarket/외부 호출도 동시에
    results = await asyncio.gather(
        execute_tool("get_regime", {"mode": "current"}),
        execute_tool("get_macro", {"mode": "dashboard"}),
        execute_tool("get_macro_external", {}),
        execute_tool("get_sector", {}),
        return_exceptions=True,
    )
    r_regime, r_macro, r_ext, r_sector = results

    # 1. regime
    try:
        if isinstance(r_regime, Exception):
            raise r_regime
        if _tool_err(r_regime):
            errors.append({"source": "regime", "msg": r_regime.get("error", "unknown")})
        else:
            regime_en = r_regime.get("regime_en", "neutral")
            days_val = None
            deb = r_regime.get("debounce", {})
            if isinstance(deb, dict):
                days_val = deb.get("days")
            payload["regime"] = {
                "label": r_regime.get("regime", regime_en),
                "regime_en": regime_en,
                "color": _regime_color(regime_en),
                "days": days_val,
            }
    except Exception as e:
        errors.append({"source": "regime", "msg": str(e)})

    # 2. indicators — get_macro mode=dashboard の data dict
    try:
        if isinstance(r_macro, Exception):
            raise r_macro
        if _tool_err(r_macro):
            errors.append({"source": "macro", "msg": r_macro.get("error", "unknown")})
        else:
            data = r_macro.get("data", {})
            if not data:
                # mode 없이 호출한 경우 최상위에 직접 있는 키 처리
                data = r_macro
            indicators = []
            # 주요 지표 순서 정의
            _ind_map = [
                ("VIX",    "VIX",    "price", "change_pct"),
                ("DXY",    "DXY",    "price", "change_pct"),
                ("US10Y",  "US10Y",  "price", "change_pct"),
                ("WTI",    "WTI",    "price", "change_pct"),
                ("GOLD",   "GOLD",   "price", "change_pct"),
                ("S&P500", "SP500",  "price", "change_pct"),
                ("KOSPI",  "KOSPI",  "price", "change_pct"),
                ("USD/KRW","USDKRW", "price", "change_pct"),
            ]
            for label, key, vf, chgf in _ind_map:
                d = data.get(key, {})
                if not isinstance(d, dict):
                    continue
                raw_val = d.get(vf)
                raw_chg = d.get(chgf)
                def _sf(x):
                    try:
                        return float(x) if x not in (None, "", "-") else None
                    except (TypeError, ValueError):
                        return None
                val = _sf(raw_val)
                chg = _sf(raw_chg)
                if val is None:
                    continue
                # 포맷: 정수계열(KOSPI/USDKRW/SP500) → 소수점 0, 나머지 소수점 2
                if key in ("KOSPI", "SP500"):
                    val_str = f"{val:,.2f}"
                elif key == "USDKRW":
                    val_str = f"{val:,.1f}"
                else:
                    val_str = f"{val:.2f}"
                indicators.append({"label": label, "value": val_str, "chg_pct": chg})
            if indicators:
                payload["indicators"] = indicators
    except Exception as e:
        errors.append({"source": "macro", "msg": str(e)})

    # 3. 수익률 곡선 + 침체확률 — get_macro_external
    try:
        if isinstance(r_ext, Exception):
            raise r_ext
        if _tool_err(r_ext):
            errors.append({"source": "macro_external", "msg": r_ext.get("error", "unknown")})
        else:
            # treasury 수익률 곡선
            # 반환 구조: treasury.yields = {"10y":float,"2y":float,"3m":float}
            #            treasury.spread_10y_2y = float|None
            #            treasury.recession_signal = "정상"/"주의 (역전 임박)"/"역전 (침체 선행)"/"데이터 부족"
            treasury = r_ext.get("treasury", {})
            if isinstance(treasury, dict):
                def _tf(x):
                    try:
                        return float(x) if x not in (None, "", "-") else None
                    except (TypeError, ValueError):
                        return None
                yields = treasury.get("yields", {})
                if not isinstance(yields, dict):
                    yields = {}
                y2  = _tf(yields.get("2y"))
                y10 = _tf(yields.get("10y"))
                # spread: treasury.spread_10y_2y 우선, 없으면 y10-y2 계산
                raw_spread = _tf(treasury.get("spread_10y_2y"))
                if raw_spread is None and y10 is not None and y2 is not None:
                    raw_spread = round(y10 - y2, 3)
                if y2 is not None or y10 is not None:
                    payload["curve"] = {"y2": y2, "y10": y10, "spread": raw_spread}
                # recession_signal 텍스트 → 프론트용 코드 변환 + 침체확률 대용
                rec_signal = treasury.get("recession_signal", "")
                payload["recession_signal"] = rec_signal
            # recession_prob 키는 fetch_treasury_curve에 없음.
            # recession_signal 텍스트를 그대로 노출 (프론트에서 -/주의/역전 색 처리).
            # Polymarket Fed 관련 시장 (get_macro_external 내 polymarket 키)
            pm_data = r_ext.get("polymarket", {})
            if not isinstance(pm_data, dict):
                pm_data = {}
            pm_markets = pm_data.get("markets", [])
            fed_markets = []
            for m in pm_markets:
                title = m.get("title", "")
                tags = m.get("tags", [])
                is_fed = any(t.lower() in ("fed", "fed rates", "economic policy") for t in tags) or "fed" in title.lower() or "fomc" in title.lower() or "rate" in title.lower()
                if not is_fed:
                    continue
                # binary: top_outcome prob = YES. non-binary: top_outcome prob
                top = m.get("top_outcome", {})
                yes_pct = None
                if m.get("is_binary"):
                    prob = top.get("prob")
                    if prob is not None:
                        yes_pct = round(float(prob) * 100, 1)
                else:
                    prob = top.get("prob")
                    outcome = top.get("outcome", "")
                    if prob is not None:
                        yes_pct = round(float(prob) * 100, 1)
                vol = m.get("vol_total", 0) or 0
                fed_markets.append({
                    "title": title,
                    "yes_pct": yes_pct,
                    "volume_usd": vol,
                    "outcome": top.get("outcome", ""),
                })
            if fed_markets:
                payload["polymarket_fed"] = fed_markets[:3]
    except Exception as e:
        errors.append({"source": "macro_external", "msg": str(e)})

    # 4. 섹터 로테이션 — get_sector all[] , 합산 내림차순 MAX 15
    try:
        if isinstance(r_sector, Exception):
            raise r_sector
        if _tool_err(r_sector):
            errors.append({"source": "sector", "msg": r_sector.get("error", "unknown")})
        else:
            all_sectors = r_sector.get("all", [])
            rotation = []
            for s in all_sectors:
                sector = s.get("sector", "")
                frgn = s.get("frgn", 0) or 0
                orgn = s.get("orgn", 0) or 0
                combined = frgn + orgn
                rotation.append({
                    "sector": sector,
                    "foreign_net": frgn,
                    "inst_net": orgn,
                    "combined": combined,
                })
            rotation.sort(key=lambda x: x["combined"], reverse=True)
            if rotation:
                payload["sector_rotation"] = rotation[:15]
    except Exception as e:
        errors.append({"source": "sector", "msg": str(e)})

    payload["_errors"] = errors
    return payload

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# US 애널리스트 탭 API 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _build_us_candidates_payload() -> dict:
    """get_us_buy_candidates 래핑. 에러 시 {"error": ..., "candidates": []} 반환."""
    r = await execute_tool("get_us_buy_candidates", {
        "days": 180, "min_advisors": 1, "min_upside": 20, "limit": 50
    })
    if isinstance(r, dict) and "error" in r:
        return {"error": r["error"], "candidates": [], "total_pool": 0, "after_upside_filter": 0}
    return r


async def _build_us_scan_payload() -> dict:
    """get_us_scan watchlist 래핑."""
    r = await execute_tool("get_us_scan", {"mode": "watchlist", "days": 14})
    if isinstance(r, dict) and "error" in r:
        return {"error": r["error"], "data": []}
    return r


async def _build_us_analysts_payload() -> dict:
    """get_us_analyst top 30 래핑."""
    r = await execute_tool("get_us_analyst", {"top": 30, "min_stars": 4.0, "days": 30})
    if isinstance(r, dict) and "error" in r:
        return {"error": r["error"], "analysts": []}
    return r

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 알파스크리너 API  /api/alpha?preset=change|fscore|mscore|fcf|high52|low52
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _build_alpha_payload(preset: str) -> dict:
    """알파스크리너 preset별 빌더. 부분 실패 허용.

    preset:
      change  — get_change_scan (turnaround,fscore_jump,insider_cluster_buy)
      fscore  — get_finance_rank rank_type=fscore
      mscore  — get_finance_rank rank_type=mscore_safe
      fcf     — get_finance_rank rank_type=fcf_yield
      high52  — get_highlow sort=high
      low52   — get_highlow sort=low
    """
    from datetime import datetime as _dt

    as_of = _dt.now().strftime("%Y-%m-%d %H:%M")

    if preset == "change":
        try:
            r = await execute_tool(
                "get_change_scan",
                {"preset": "turnaround,fscore_jump,insider_cluster_buy", "n": 30},
            )
            if _tool_err(r):
                return {"preset": preset, "error": r["error"], "items": [],
                        "meta": {"as_of": as_of, "count": 0}}
            items = [
                {
                    "ticker": s.get("ticker"),
                    "name": s.get("name"),
                    "market": s.get("market"),
                    "close": s.get("close"),
                    "chg_pct": s.get("chg_pct"),
                    "op_profit_delta": s.get("op_profit_delta"),
                    "fscore_delta": s.get("fscore_delta"),
                    "insider_reprors": s.get("insider_reprors"),
                }
                for s in (r.get("results") or [])
            ]
            return {"preset": preset, "items": items,
                    "meta": {"as_of": r.get("date") or as_of, "count": len(items)}}
        except Exception as e:
            return {"preset": preset, "error": str(e), "items": [],
                    "meta": {"as_of": as_of, "count": 0}}

    elif preset in ("fscore", "mscore", "fcf"):
        rank_map = {"fscore": "fscore", "mscore": "mscore_safe", "fcf": "fcf_yield"}
        try:
            r = await execute_tool(
                "get_finance_rank",
                {"rank_type": rank_map[preset], "n": 30},
            )
            if _tool_err(r):
                return {"preset": preset, "error": r["error"], "items": [],
                        "meta": {"as_of": as_of, "count": 0}}
            stocks = r.get("stocks") or []
            items = []
            for s in stocks:
                item: dict = {
                    "rank": s.get("rank"),
                    "ticker": s.get("symbol"),
                    "name": s.get("name"),
                    "market": s.get("market"),
                    "market_cap": s.get("market_cap"),
                }
                if preset == "fscore":
                    item["fscore"] = s.get("metric")
                elif preset == "mscore":
                    item["mscore"] = s.get("metric")
                elif preset == "fcf":
                    item["fcf_yield"] = s.get("metric")
                items.append(item)
            return {"preset": preset, "items": items,
                    "meta": {"as_of": r.get("trade_date") or as_of, "count": r.get("count", len(items))}}
        except Exception as e:
            return {"preset": preset, "error": str(e), "items": [],
                    "meta": {"as_of": as_of, "count": 0}}

    elif preset in ("high52", "low52"):
        sort = "high" if preset == "high52" else "low"
        try:
            r = await execute_tool("get_highlow", {"sort": sort, "n": 30})
            if _tool_err(r):
                return {"preset": preset, "error": r["error"], "items": [],
                        "meta": {"as_of": as_of, "count": 0}}
            stocks = r.get("stocks") or []
            items = [
                {
                    "rank": s.get("rank"),
                    "ticker": s.get("ticker"),
                    "name": s.get("name"),
                    "price": s.get("price"),
                    "chg_pct": s.get("chg_pct"),
                    "new_high": s.get("new_high"),
                    "new_low": s.get("new_low"),
                    "high_gap_pct": s.get("high_gap_pct"),
                    "low_gap_pct": s.get("low_gap_pct"),
                }
                for s in stocks
            ]
            return {"preset": preset, "items": items,
                    "meta": {"as_of": as_of, "count": r.get("count", len(items))}}
        except Exception as e:
            return {"preset": preset, "error": str(e), "items": [],
                    "meta": {"as_of": as_of, "count": 0}}

    return {"preset": preset, "error": f"알 수 없는 preset: {preset}", "items": [],
            "meta": {"as_of": as_of, "count": 0}}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 수급 API  /api/supply?mode=foreign_rank|combined_rank|short_sale|credit|lending
# short_sale/credit/lending 은 watchlist 첫 번째 종목 기준 (ticker 파라미터 지원)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _build_supply_payload(mode: str, ticker: str = "") -> dict:
    """수급 mode별 빌더. 부분 실패 허용."""
    from datetime import datetime as _dt
    as_of = _dt.now().strftime("%Y-%m-%d %H:%M")

    if mode == "foreign_rank":
        try:
            r = await execute_tool("get_supply", {"mode": "foreign_rank", "n": 20})
            if _tool_err(r):
                return {"mode": mode, "error": r["error"], "items": [], "as_of": as_of}
            items = r.get("items") or []
            return {"mode": mode, "items": items,
                    "as_of": r.get("trade_date") or as_of}
        except Exception as e:
            return {"mode": mode, "error": str(e), "items": [], "as_of": as_of}

    elif mode == "combined_rank":
        try:
            r = await execute_tool("get_supply", {"mode": "combined_rank", "n": 20})
            if _tool_err(r):
                return {"mode": mode, "error": r["error"], "items": [], "as_of": as_of}
            return {"mode": mode, "items": r.get("items") or [],
                    "as_of": as_of}
        except Exception as e:
            return {"mode": mode, "error": str(e), "items": [], "as_of": as_of}

    elif mode in ("short_sale", "credit", "lending"):
        # ticker 없으면 watchlist 첫 종목 사용
        if not ticker:
            try:
                wl = load_watchlist()
                ticker = next(iter(wl), "") if isinstance(wl, dict) else (wl[0] if wl else "")
            except Exception:
                ticker = "005930"
        if not ticker:
            ticker = "005930"  # 삼성전자 기본값
        try:
            r = await execute_tool("get_market_signal", {"mode": mode, "ticker": ticker})
            if _tool_err(r):
                return {"mode": mode, "error": r["error"], "items": [],
                        "ticker": ticker, "warning": None, "as_of": as_of}
            items = r.get("items") or []
            warning = r.get("warning")
            return {"mode": mode, "items": items, "ticker": ticker,
                    "warning": warning, "as_of": as_of}
        except Exception as e:
            return {"mode": mode, "error": str(e), "items": [],
                    "ticker": ticker, "warning": None, "as_of": as_of}

    return {"mode": mode, "error": f"알 수 없는 mode: {mode}", "items": [], "as_of": as_of}

