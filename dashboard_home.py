"""dashboard_home — 새 대시보드 P0/P1/P2/P3a/P3b.

/home 경로에 서빙. /dash(dashboard.py)는 무수정.
P0: HTML 쉘 + Alpine 탭 네비 + 빈 패널.
P1: JSON API (/api/home, /api/regime, /api/alerts, /api/portfolio) + 홈 화면 실데이터 바인딩.
P2: 포트폴리오 + 워치·알림 탭.
P3a: Whale 탭 — /api/whale?p=<preset> + Alpine 서브탭 5개.
P3b: 리포트 탭 — /api/reports + /api/reports/{ticker}, 기록 탭 — /api/decisions + /api/trades + /api/invest_todo.
"""

import re
import time
import asyncio
import sqlite3 as _sqlite3
from datetime import datetime, timezone, timedelta

from aiohttp import web

import json

import os

from kis_api import (
    load_json,
    load_stoploss,
    load_watchalert,
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TTL 캐시 + stale-while-revalidate (asyncio 단일스레드 — lock 불필요)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_cache: dict = {}         # {key: {"ts": float, "data": any}}
_refreshing: set = set()  # 백그라운드 갱신 중인 key 집합 (중복 방지)


async def _cached(key: str, ttl: float, factory):
    """stale-while-revalidate(SWR) 캐시.

    동작:
      - fresh (age <= ttl): 즉시 data 반환.
      - stale (age > ttl) 이지만 data 존재: 즉시 stale data 반환
        + 백그라운드 asyncio.create_task로 factory 재실행해 캐시 갱신.
      - cold (캐시 없음): await factory() 후 저장 및 반환 (최초 1회만 블로킹).

    중복 갱신 가드: _refreshing set으로 동시 백그라운드 refresh 1개만 허용.
    갱신 실패 시 기존 data 유지 + 플래그 해제 (try/finally).
    W2: factory는 콜러블. miss일 때만 await해 코루틴 누수 방지.
    """
    entry = _cache.get(key)
    now = time.monotonic()

    if entry is not None:
        age = now - entry["ts"]
        if age <= ttl:
            # fresh — 즉시 반환
            return entry["data"]
        # stale — 즉시 반환 + 백그라운드 갱신
        if key not in _refreshing:
            _refreshing.add(key)
            async def _bg_refresh(k, f):
                try:
                    new_data = await f()
                    _cache[k] = {"ts": time.monotonic(), "data": new_data}
                except Exception as _bg_err:
                    print(f"[cache] 백그라운드 갱신 실패 ({k}): {_bg_err}")
                finally:
                    _refreshing.discard(k)
            asyncio.create_task(_bg_refresh(key, factory))
        return entry["data"]

    # cold — 블로킹 최초 로드
    data = await factory()
    _cache[key] = {"ts": time.monotonic(), "data": data}
    return data


async def warm_caches() -> None:
    """서버 시작 직후 주요 캐시를 백그라운드에서 미리 채움.

    core 4개(home/portfolio/watch/market)를 먼저 순차 워밍 후,
    무거운 것(macro_panel ~40s, us_candidates ~40s)을 순차 워밍.
    각 소스 독립 try/except — 일부 실패해도 나머지 계속 진행.
    asyncio.create_task로 호출 → 봇 기동을 블로킹하지 않음.
    """
    print("[cache] warm_caches 시작 — core 4개 프리워밍")
    for label, key, factory in [
        ("home",      "home",      lambda: build_home_payload()),
        ("portfolio", "portfolio", lambda: _build_portfolio_with_grand()),
        ("watch",     "watch",     lambda: _build_watch_payload()),
        ("market",    "market",    lambda: build_market_payload()),
    ]:
        try:
            data = await factory()
            _cache[key] = {"ts": time.monotonic(), "data": data}
            print(f"[cache] warm OK: {label}")
        except Exception as e:
            print(f"[cache] warm FAIL: {label} — {e}")

    # core 완료 후 무거운 엔드포인트 추가 프리워밍 (~40s 각)
    print("[cache] warm_caches — 무거운 것(macro_panel, us_candidates) 프리워밍 시작")
    for label, key, factory in [
        ("macro_panel",   "macro_panel",   lambda: build_macro_panel_payload()),
        ("us_candidates", "us_candidates", lambda: _build_us_candidates_payload()),
    ]:
        try:
            data = await factory()
            _cache[key] = {"ts": time.monotonic(), "data": data}
            print(f"[cache] warm OK: {label}")
        except Exception as e:
            print(f"[cache] warm FAIL: {label} — {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 에러 dict 검사 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _tool_err(r) -> bool:
    """execute_tool이 raise 대신 {"error": ...}를 반환할 때 감지 (W1).

    execute_tool은 내부에서 예외를 잡아 {"error": msg, "tool": name}을 반환.
    이를 호출자 try/except가 못 잡으므로 명시적 검사 필요.
    """
    return isinstance(r, dict) and "error" in r


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 API 래퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _api(coro) -> web.Response:
    try:
        return web.json_response(await coro)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=200)


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
    loop = asyncio.get_event_loop()
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
    loop = asyncio.get_event_loop()
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Alpine dashApp JS (인라인 <script> 본문)
# Python 문자열 안에 들어가므로 JS 문자열 리터럴 내
# 제어문자는 쓰지 않음 — \n 버그 방지.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_DASH_APP_JS = r"""
function dashApp() {
  return {
    activeTab: 'home',
    loading: false,
    lastUpdated: '',
    autoRefresh: true,
    home: null,
    _refreshTimer: null,

    /* P2: portfolio tab */
    portfolio: null,
    portHistory: null,
    portHistoryLoading: false,
    portChartPeriod: '3M',
    _portChart: null,
    _portSeries: null,
    _portResizeObs: null,
    _portChartRetry: false,

    /* market tab */
    market: null,
    marketMoverSeg: 'kr',
    marketStockQuery: '',
    marketStockResult: null,
    marketStockLoading: false,

    /* market: marketmap treemap */
    marketmap: {},
    marketmapMarket: 'kospi',
    marketmapLoading: false,
    _mmChart: null,

    /* P3b: report tab */
    report: null,
    reportSeg: 'kr',
    reportModal: null,
    reportModalList: null,
    reportModalLoading: false,

    /* P4: signal tab */
    signals: null,
    signalSeg: 'feed',

    /* P3b: record tab */
    record: null,
    recordSection: 'decisions',
    decisionsLimit: 20,
    decisionForm: { show: false, date: '', regime: '', memo: '' },
    recordToast: '',
    portSort: 'eval',
    portModal: null,
    portModalLoading: false,
    portModalCandlePeriod: '3M',
    _candleChart: null,
    _candleSeries: null,
    _volChart: null,
    _volSeries: null,
    _candleResizeObs: null,
    _candleChartRetry: false,

    /* P2: portfolio view toggle */
    portView: 'list',

    /* market: sector heatmap */
    sectorHeatmap: null,
    sectorHeatmapLoading: false,

    /* market: macro panel */
    macroPanel: null,
    macroPanelLoading: false,

    /* alpha screener (signal tab) */
    alphaSeg: 'change',
    alphaData: {},
    alphaLoading: false,

    /* supply panel (market tab) */
    supplySeg: 'foreign_rank',
    supplyData: {},
    supplyLoading: false,

    /* P2: watch/alert tab */
    watch: null,
    watchForm: { show: false, ticker: '', name: '', stop: '', target: '', buy: '' },
    watchToast: '',

    async init() {
      await this.loadHome();
      this.refreshIcons();
      this._startAutoRefresh();
    },

    _startAutoRefresh() {
      if (this._refreshTimer) clearInterval(this._refreshTimer);
      this._refreshTimer = setInterval(async () => {
        if (this.autoRefresh) {
          await this.loadHome();
          this.refreshIcons();
        }
      }, 60000);
    },

    toggleAutoRefresh() {
      this.autoRefresh = !this.autoRefresh;
    },

    async loadHome() {
      /* stale-while-revalidate: 데이터 이미 있으면 loading 화면 안 띄움.
         fetch 중 기존 데이터 유지 → 도착 시 교체. */
      if (!this.home) this.loading = true;
      const data = await this.api('/api/home');
      this.loading = false;
      if (!data.error) {
        this.home = data;
        this.lastUpdated = new Date().toLocaleTimeString('ko-KR');
      }
    },

    /* ── portfolio tab ── */
    async loadPortfolio() {
      if (this.portfolio) return;
      const data = await this.api('/api/portfolio');
      if (!data.error) this.portfolio = data;
    },

    async loadPortfolioHistory() {
      this.portHistoryLoading = true;
      const data = await this.api('/api/portfolio_history');
      this.portHistoryLoading = false;
      if (!data.error) {
        this.portHistory = data;
        this.$nextTick(() => this._mountPortChart());
      }
    },

    _portChartData() {
      if (!this.portHistory || !this.portHistory.snapshots) return [];
      const snaps = this.portHistory.snapshots;
      const now = new Date();
      let cutoff = new Date(now);
      if (this.portChartPeriod === '1M') cutoff.setMonth(cutoff.getMonth() - 1);
      else if (this.portChartPeriod === '3M') cutoff.setMonth(cutoff.getMonth() - 3);
      else cutoff.setFullYear(cutoff.getFullYear() - 1);
      return snaps
        .filter(s => s.date && s.total_asset_krw > 0 && new Date(s.date) >= cutoff)
        .map(s => ({ time: s.date, value: s.total_asset_krw }));
    },

    _mountPortChart() {
      if (typeof LightweightCharts === 'undefined') return;
      const el = document.getElementById('port-chart-container');
      if (!el) return;
      const chartData = this._portChartData();
      // 빈 상태
      const emptyEl = document.getElementById('port-chart-empty');
      if (chartData.length < 2) {
        if (emptyEl) emptyEl.style.display = 'flex';
        el.style.display = 'none';
        return;
      }
      if (emptyEl) emptyEl.style.display = 'none';
      el.style.display = 'block';
      // 레이아웃 전(컨테이너 폭 0)이면 0-width 차트 생성 방지 → rAF 1회 재시도 후 return.
      // _portChartRetry 플래그로 무한루프 가드(1회만 재시도).
      if (el.clientWidth === 0) {
        if (!this._portChartRetry) {
          this._portChartRetry = true;
          requestAnimationFrame(() => this._mountPortChart());
        }
        return;
      }
      this._portChartRetry = false;
      // 이전 차트 제거
      if (this._portChart) {
        try { this._portChart.remove(); } catch(e) {}
        this._portChart = null;
        this._portSeries = null;
      }
      if (this._portResizeObs) { this._portResizeObs.disconnect(); this._portResizeObs = null; }
      const isMobile = window.innerWidth < 768;
      const h = isMobile ? 200 : 260;
      const chart = LightweightCharts.createChart(el, {
        width: el.clientWidth,
        height: h,
        layout: { background: { color: '#ffffff' }, textColor: '#94a3b8', fontSize: 11 },
        grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
        rightPriceScale: { borderColor: '#e2e8f0' },
        timeScale: { borderColor: '#e2e8f0', timeVisible: true, secondsVisible: false },
        handleScroll: !isMobile,
        handleScale: !isMobile,
      });
      const first = chartData[0].value;
      const last = chartData[chartData.length - 1].value;
      const isUp = last >= first;
      const lineColor = isUp ? '#16a34a' : '#dc2626';
      const topColor = isUp ? 'rgba(34,197,94,0.3)' : 'rgba(220,38,38,0.3)';
      const series = chart.addAreaSeries({
        lineColor,
        topColor,
        bottomColor: 'rgba(255,255,255,0)',
        lineWidth: 2,
        priceFormat: {
          type: 'custom',
          formatter: v => (v / 1e8).toFixed(1) + '억',
        },
      });
      series.setData(chartData);
      chart.timeScale().fitContent();
      this._portChart = chart;
      this._portSeries = series;
      // ResizeObserver
      const ro = new ResizeObserver(entries => {
        for (const entry of entries) {
          if (this._portChart) this._portChart.applyOptions({ width: entry.contentRect.width });
        }
      });
      ro.observe(el);
      this._portResizeObs = ro;
    },

    setPortChartPeriod(p) {
      this.portChartPeriod = p;
      this.$nextTick(() => {
        if (!this._portChart || !this._portSeries) { this._mountPortChart(); return; }
        const chartData = this._portChartData();
        if (chartData.length < 2) { this._mountPortChart(); return; }
        this._portSeries.setData(chartData);
        this._portChart.timeScale().fitContent();
      });
    },

    portSorted(holdings) {
      if (!holdings || !holdings.length) return [];
      const arr = [...holdings];
      if (this.portSort === 'eval') arr.sort((a, b) => b.eval_amt - a.eval_amt);
      else if (this.portSort === 'pnl_pct') arr.sort((a, b) => b.pnl_pct - a.pnl_pct);
      else if (this.portSort === 'pnl') arr.sort((a, b) => b.pnl - a.pnl);
      return arr;
    },

    async openStockModal(ticker) {
      this._destroyCandleChart();
      this.portModal = { ticker, loading: true };
      this.portModalLoading = true;
      this.portModalCandlePeriod = '3M';
      this.$nextTick(() => this.refreshIcons());
      const data = await this.api('/api/stock/' + ticker);
      this.portModal = data.error ? { ticker, error: data.error } : data;
      this.portModalLoading = false;
      /* 캔들 mount: 모달 DOM이 보인 뒤(x-if 렌더 + 레이아웃) mount.
         nextTick + rAF 한 번 더로 모달 폭 0-width 생성 방지(_mountCandleChart에도 가드 있음). */
      this.$nextTick(() => {
        this.refreshIcons();
        requestAnimationFrame(() => this._mountCandleChart());
      });
    },

    closeModal() {
      this._destroyCandleChart();
      this.portModal = null;
    },

    _destroyCandleChart() {
      if (this._candleResizeObs) { this._candleResizeObs.disconnect(); this._candleResizeObs = null; }
      if (this._candleChart) {
        try { this._candleChart.remove(); } catch(e) {}
        this._candleChart = null;
        this._candleSeries = null;
      }
      if (this._volChart) {
        try { this._volChart.remove(); } catch(e) {}
        this._volChart = null;
        this._volSeries = null;
      }
    },

    _candleChartData() {
      if (!this.portModal || !this.portModal.candles) return [];
      const now = new Date();
      let cutoff = new Date(now);
      if (this.portModalCandlePeriod === '1M') cutoff.setMonth(cutoff.getMonth() - 1);
      else if (this.portModalCandlePeriod === '3M') cutoff.setMonth(cutoff.getMonth() - 3);
      else cutoff.setMonth(cutoff.getMonth() - 6);
      return this.portModal.candles
        .filter(c => c.open > 0 && c.close > 0 && c.date)
        .filter(c => {
          const d = c.date.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');
          return new Date(d) >= cutoff;
        })
        .map(c => {
          const t = c.date.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');
          return { time: t, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume };
        });
    },

    _mountCandleChart() {
      if (typeof LightweightCharts === 'undefined') return;
      if (!this.portModal || !this.portModal.candles) return;
      const candleEl = document.getElementById('modal-candle-container');
      const volEl = document.getElementById('modal-vol-container');
      if (!candleEl) return;
      const chartData = this._candleChartData();
      if (chartData.length === 0) return;
      // 모달 레이아웃 전(컨테이너 폭 0)이면 0-width 생성 방지 → rAF 1회 재시도 후 return.
      // _candleChartRetry 플래그로 무한루프 가드(1회만 재시도).
      if (candleEl.clientWidth === 0) {
        if (!this._candleChartRetry) {
          this._candleChartRetry = true;
          requestAnimationFrame(() => this._mountCandleChart());
        }
        return;
      }
      this._candleChartRetry = false;
      this._destroyCandleChart();
      const isMobile = window.innerWidth < 768;
      const commonOpts = {
        layout: { background: { color: '#ffffff' }, textColor: '#94a3b8', fontSize: 11 },
        grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
        rightPriceScale: { borderColor: '#e2e8f0' },
        timeScale: { borderColor: '#e2e8f0', timeVisible: false, secondsVisible: false },
        handleScroll: !isMobile,
        handleScale: !isMobile,
      };
      const cChart = LightweightCharts.createChart(candleEl, {
        ...commonOpts,
        width: candleEl.clientWidth,
        height: isMobile ? 180 : 220,
      });
      const cSeries = cChart.addCandlestickSeries({
        upColor: '#16a34a',
        downColor: '#dc2626',
        borderUpColor: '#16a34a',
        borderDownColor: '#dc2626',
        wickUpColor: '#16a34a',
        wickDownColor: '#dc2626',
      });
      cSeries.setData(chartData.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
      cChart.timeScale().fitContent();
      this._candleChart = cChart;
      this._candleSeries = cSeries;
      // 거래량 히스토그램
      if (volEl) {
        const vChart = LightweightCharts.createChart(volEl, {
          ...commonOpts,
          width: volEl.clientWidth,
          height: 50,
          rightPriceScale: { visible: false },
          leftPriceScale: { visible: false },
        });
        const vSeries = vChart.addHistogramSeries({
          priceFormat: { type: 'volume' },
          priceScaleId: '',
        });
        vSeries.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0 } });
        vSeries.setData(chartData.map(c => ({
          time: c.time,
          value: c.volume,
          color: c.close >= c.open ? 'rgba(22,163,74,0.6)' : 'rgba(220,38,38,0.6)',
        })));
        vChart.timeScale().fitContent();
        this._volChart = vChart;
        this._volSeries = vSeries;
        // 두 차트 timeScale 동기화
        cChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
          if (range && this._volChart) this._volChart.timeScale().setVisibleLogicalRange(range);
        });
        vChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
          if (range && this._candleChart) this._candleChart.timeScale().setVisibleLogicalRange(range);
        });
      }
      // ResizeObserver: 기기 회전/뷰포트 변화 시 캔들+거래량 폭 동기화
      const cro = new ResizeObserver(entries => {
        for (const entry of entries) {
          const w = entry.contentRect.width;
          if (this._candleChart) this._candleChart.applyOptions({ width: w });
          if (this._volChart) this._volChart.applyOptions({ width: w });
        }
      });
      cro.observe(candleEl);
      this._candleResizeObs = cro;
    },

    setCandlePeriod(p) {
      this.portModalCandlePeriod = p;
      this.$nextTick(() => {
        if (!this._candleChart || !this._candleSeries) { this._mountCandleChart(); return; }
        const chartData = this._candleChartData();
        if (chartData.length === 0) return;
        this._candleSeries.setData(chartData.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
        this._candleChart.timeScale().fitContent();
        if (this._volSeries && this._volChart) {
          this._volSeries.setData(chartData.map(c => ({
            time: c.time,
            value: c.volume,
            color: c.close >= c.open ? 'rgba(22,163,74,0.6)' : 'rgba(220,38,38,0.6)',
          })));
          this._volChart.timeScale().fitContent();
        }
      });
    },

    /* ── watch/alert tab ── */
    async loadWatch() {
      /* stale-while-revalidate: 데이터 이미 있으면 null로 비우지 않고
         백그라운드로 fetch 후 도착 시 교체. 탭 최초 진입 시에만 로딩 표시. */
      const data = await this.api('/api/watch');
      if (!data.error) this.watch = data;
    },

    async removeWatch(ticker, alertType) {
      const body = JSON.stringify({ action: 'remove', ticker, alert_type: alertType || 'watchlist' });
      const r = await fetch('/api/watch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
      const d = await r.json();
      if (d.error) { this.showToast('오류: ' + d.error); return; }
      this.showToast('삭제됨: ' + ticker);
      this.watch = null;
      await this.loadWatch();
      this.$nextTick(() => this.refreshIcons());
    },

    async submitWatchForm() {
      const f = this.watchForm;
      if (!f.ticker) { this.showToast('티커를 입력하세요'); return; }
      let body;
      if (f.buy) {
        body = JSON.stringify({ action: 'set_alert', log_type: 'watch', ticker: f.ticker.toUpperCase(), name: f.name || f.ticker.toUpperCase(), buy_price: parseFloat(f.buy), stop_price: parseFloat(f.stop || 0), target_price: parseFloat(f.target || 0) });
      } else {
        body = JSON.stringify({ action: 'add', ticker: f.ticker.toUpperCase(), name: f.name || f.ticker.toUpperCase() });
      }
      const r = await fetch('/api/watch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
      const d = await r.json();
      if (d.error) { this.showToast('오류: ' + d.error); return; }
      this.showToast(d.message || '저장됨');
      this.watchForm = { show: false, ticker: '', name: '', stop: '', target: '', buy: '' };
      this.watch = null;
      await this.loadWatch();
      this.$nextTick(() => this.refreshIcons());
    },

    showToast(msg) {
      this.watchToast = msg;
      setTimeout(() => { this.watchToast = ''; }, 3000);
    },

    hmColor(v) {
      if (v == null) return 'bg-slate-100 text-slate-500';
      if (v >= 5)  return 'bg-green-700 text-white';
      if (v >= 3)  return 'bg-green-500 text-white';
      if (v >= 1)  return 'bg-green-400 text-white';
      if (v >= 0)  return 'bg-green-100 text-green-800';
      if (v >= -1) return 'bg-red-100 text-red-800';
      if (v >= -3) return 'bg-red-300 text-white';
      if (v >= -5) return 'bg-red-500 text-white';
      return 'bg-red-700 text-white';
    },

    hmPortItems() {
      if (!this.portfolio) return [];
      const krHoldings = (this.portfolio.kr && this.portfolio.kr.holdings) ? this.portfolio.kr.holdings : [];
      const usHoldings = (this.portfolio.us && this.portfolio.us.holdings) ? this.portfolio.us.holdings : [];
      const usdKrw = this.portfolio.usd_krw || 1400;
      const allItems = [];
      for (const h of krHoldings) {
        allItems.push({ ...h, eval_krw: h.eval_amt || 0, market: 'KR' });
      }
      for (const h of usHoldings) {
        allItems.push({ ...h, eval_krw: (h.eval_amt || 0) * usdKrw, market: 'US' });
      }
      const total = allItems.reduce((s, i) => s + i.eval_krw, 0);
      for (const item of allItems) {
        item.weight = total > 0 ? item.eval_krw / total * 100 : 0;
      }
      allItems.sort((a, b) => b.weight - a.weight);
      return allItems;
    },

    async loadSectorHeatmap() {
      /* SWR: keep stale data, never null during refresh */
      if (!this.sectorHeatmap) this.sectorHeatmapLoading = true;
      const data = await this.api('/api/sector_heatmap');
      this.sectorHeatmapLoading = false;
      if (!data.error) this.sectorHeatmap = data;
    },

    /* ── marketmap treemap ── */
    mmHeight() {
      return window.innerWidth >= 768 ? 480 : 320;
    },

    async loadMarketmap(market) {
      market = market || this.marketmapMarket;
      this.marketmapMarket = market;
      if (this.marketmap[market]) {
        this._renderMarketmap();
        this._bgRefreshMarketmap(market);
        return;
      }
      this.marketmapLoading = true;
      const data = await this.api('/api/marketmap?market=' + market);
      this.marketmapLoading = false;
      if (data && !data.error) {
        this.marketmap = Object.assign({}, this.marketmap, { [market]: data });
        this._renderMarketmap();
      }
    },

    async _bgRefreshMarketmap(market) {
      const data = await this.api('/api/marketmap?market=' + market);
      if (data && !data.error) {
        this.marketmap = Object.assign({}, this.marketmap, { [market]: data });
        this._renderMarketmap();
      }
    },

    mmColor(pct) {
      if (pct == null) return '#e2e8f0';
      const stops = [[-5,[185,28,28]],[-3,[239,68,68]],[-1,[252,165,165]],[0,[241,245,249]],[1,[134,239,172]],[3,[34,197,94]],[5,[21,128,61]]];
      let p = Math.max(-5, Math.min(5, pct));
      for (let i = 0; i < stops.length - 1; i++) {
        const a = stops[i][0], ca = stops[i][1], b = stops[i+1][0], cb = stops[i+1][1];
        if (p >= a && p <= b) {
          const t = (p - a) / (b - a);
          const c = ca.map((v, k) => Math.round(v + (cb[k] - v) * t));
          return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')';
        }
      }
      return '#f1f5f9';
    },
    mmEchartsData(raw) {
      if (!raw || !raw.data) return [];
      return raw.data.map(sector => {
        return {
          name: sector.name,
          value: [sector.value, sector.change_pct != null ? sector.change_pct : 0],
          change_pct: sector.change_pct,
          itemStyle: { color: this.mmColor(sector.change_pct) },
          children: (sector.children || []).map(child => {
            return {
              name: child.name,
              ticker: child.ticker,
              value: [child.value, child.change_pct != null ? child.change_pct : 0],
              change_pct: child.change_pct,
              itemStyle: { color: this.mmColor(child.change_pct) },
            };
          }),
        };
      });
    },

    _renderMarketmap() {
      const raw = this.marketmap[this.marketmapMarket];
      if (!raw || !raw.data || !raw.data.length) return;
      if (typeof echarts === 'undefined') {
        this._mmTries = (this._mmTries || 0) + 1;
        if (this._mmTries < 60) requestAnimationFrame(() => this._renderMarketmap());
        return;
      }
      const el = document.getElementById('marketmap-container');
      if (!el || el.offsetWidth === 0) {
        this._mmTries = (this._mmTries || 0) + 1;
        if (this._mmTries < 60) requestAnimationFrame(() => this._renderMarketmap());
        return;
      }
      this._mmTries = 0;
      el.style.height = this.mmHeight() + 'px';
      if (!this._mmChart) {
        this._mmChart = echarts.init(el, null, { renderer: 'svg' });
        const self = this;
        this._mmChart.on('click', function(p) {
          if (p.data && p.data.ticker) self.openStockModal(p.data.ticker);
        });
        if (window.ResizeObserver) {
          let _mmt;
          const ro = new ResizeObserver(() => {
            clearTimeout(_mmt);
            _mmt = setTimeout(() => {
              const e = document.getElementById('marketmap-container');
              if (self._mmChart && self._mmOpt && e && e.offsetWidth > 0 && e.offsetWidth !== self._mmLastW) { self._mmLastW = e.offsetWidth; self._mmChart.resize(); self._mmChart.setOption(self._mmOpt, true); }
            }, 120);
          });
          ro.observe(el);
          this._mmResizeObs = ro;
        }
      }
      const treeData = this.mmEchartsData(raw);
      const opt = {
        tooltip: {
          formatter: function(info) {
            const d = info.data;
            if (!d) return '';
            if (d.ticker) {
              const chgStr = d.change_pct != null ? ((d.change_pct >= 0 ? '+' : '') + d.change_pct.toFixed(2) + '%') : '-';
              const cap = Array.isArray(d.value) ? d.value[0] : d.value;
              const capStr = cap >= 10000 ? (cap / 10000).toFixed(1) + '조' : (cap.toFixed(0) + '억');
              return d.name + ' (' + d.ticker + ')<br/>' + chgStr + ' | 시총 ' + capStr;
            }
            return d.name || info.name;
          }
        },
        series: [{
          type: 'treemap',
          roam: false,
          nodeClick: 'zoomToNode',
          breadcrumb: { show: true, height: 28 },
          label: {
            show: true,
            formatter: function(p) {
              const d = p.data;
              if (!d || !d.ticker) return '';
              const chg = d.change_pct;
              const chgStr = chg != null ? ((chg >= 0 ? '+' : '') + chg.toFixed(2) + '%') : '';
              const rect = p.value;
              const area = Array.isArray(rect) ? rect[0] : rect;
              if (area > 30000) return d.name + '\n' + chgStr;
              return chgStr;
            },
            fontSize: 11,
            color: '#1e293b',
            overflow: 'truncate',
          },
          upperLabel: {
            show: true,
            height: 24,
            fontSize: 12,
            fontWeight: 'bold',
            color: '#1e293b',
            backgroundColor: 'rgba(255,255,255,0.7)',
            formatter: function(p) {
              const d = p.data;
              if (!d || d.ticker) return '';
              const chg = d.change_pct;
              const chgStr = chg != null ? (' ' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%') : '';
              return (d.name || '') + chgStr;
            }
          },
          levels: [
            { itemStyle: { borderWidth: 2, borderColor: '#e2e8f0', gapWidth: 2 } },
            { itemStyle: { borderWidth: 1, borderColor: '#cbd5e1', gapWidth: 1 } }
          ],
          data: treeData,
        }],
      };
      this._mmOpt = opt;
      this._mmChart.resize();
      this._mmChart.setOption(opt, true);
      this._mmLastW = el.offsetWidth;
    },

    async loadMacroPanel() {
      /* SWR: keep stale data, never null during refresh */
      if (!this.macroPanel) this.macroPanelLoading = true;
      const data = await this.api('/api/macro_panel');
      this.macroPanelLoading = false;
      if (!data.error) this.macroPanel = data;
    },

    setTab(t) {
      this.activeTab = t;
      if (t === 'portfolio') {
        this.loadPortfolio();
        /* 차트 A mount 트리거: 패널이 x-show로 보이게 된 뒤 mount.
           portHistory가 이미 있으면 즉시 재mount(이전 차트 remove 후 재생성 → 누수 없음),
           없으면 loadPortfolioHistory()가 fetch 후 mount.
           x-show 레이아웃 적용을 기다리려고 nextTick + rAF 한 번 더. */
        if (this.portHistory) {
          this.$nextTick(() => requestAnimationFrame(() => this._mountPortChart()));
        } else {
          this.loadPortfolioHistory();
        }
      }
      if (t === 'watch') this.loadWatch();
      if (t === 'signal') { this.loadSignal(); this.loadAlpha(this.alphaSeg); }
      if (t === 'report') this.loadReport();
      if (t === 'record') this.loadRecord();
      if (t === 'market') { this.loadMarket(); this.loadSectorHeatmap(); this.loadMacroPanel(); this.loadSupply(this.supplySeg); this.$nextTick(() => this.loadMarketmap()); }
      if (t === 'us') { this.loadUsCandidates(); this.loadUsScan(); }
      this.$nextTick(() => this.refreshIcons());
    },

    /* ── signal tab ── */
    async loadSignal() {
      const data = await this.api('/api/signals');
      if (!data.error) this.signals = data;
    },

    signalKindIcon(kind) {
      if (kind === 'supply_drain') return '🔵';
      if (kind === 'momentum_exit') return '🔴';
      return '⚡';
    },

    signalKindLabel(kind) {
      if (kind === 'supply_drain') return '수급이탈';
      if (kind === 'momentum_exit') return '모멘텀이탈';
      return '이상급등';
    },

    signalKindClass(kind) {
      if (kind === 'supply_drain') return 'bg-blue-100 text-blue-700';
      if (kind === 'momentum_exit') return 'bg-red-100 text-red-700';
      return 'bg-amber-100 text-amber-700';
    },

    dDayLabel(dday) {
      if (dday === 0) return 'D-day';
      if (dday < 0) return 'D' + dday;
      return 'D-' + dday;
    },

    dDayClass(dday) {
      if (dday === 0) return 'text-red-600 font-bold';
      if (dday <= 3) return 'text-orange-500 font-semibold';
      if (dday <= 7) return 'text-amber-600';
      return 'text-slate-500';
    },

    /* ── alpha screener (signal tab sub) ── */
    async loadAlpha(preset) {
      this.alphaSeg = preset;
      const cached = this.alphaData[preset];
      if (cached) {
        /* SWR: stale 즉시 반환 후 bg refresh */
        this._bgRefreshAlpha(preset);
        return;
      }
      this.alphaLoading = true;
      const data = await this.api('/api/alpha?preset=' + preset);
      this.alphaLoading = false;
      if (!data.error) {
        this.alphaData = Object.assign({}, this.alphaData, { [preset]: data });
      }
    },
    async _bgRefreshAlpha(preset) {
      const data = await this.api('/api/alpha?preset=' + preset);
      if (!data.error) {
        this.alphaData = Object.assign({}, this.alphaData, { [preset]: data });
      }
    },

    /* ── supply panel (market tab sub) ── */
    async loadSupply(mode) {
      this.supplySeg = mode;
      const cached = this.supplyData[mode];
      if (cached) {
        this._bgRefreshSupply(mode);
        return;
      }
      this.supplyLoading = true;
      const data = await this.api('/api/supply?mode=' + mode);
      this.supplyLoading = false;
      if (!data.error) {
        this.supplyData = Object.assign({}, this.supplyData, { [mode]: data });
      }
    },
    async _bgRefreshSupply(mode) {
      const data = await this.api('/api/supply?mode=' + mode);
      if (!data.error) {
        this.supplyData = Object.assign({}, this.supplyData, { [mode]: data });
      }
    },

    /* ── report tab ── */
    async loadReport() {
      if (this.report) return;
      const data = await this.api('/api/reports');
      if (!data.error) this.report = data;
    },

    async openReportModal(ticker) {
      this.reportModal = { ticker, loading: true };
      this.reportModalList = null;
      this.reportModalLoading = true;
      this.$nextTick(() => this.refreshIcons());
      const data = await this.api('/api/reports/' + ticker);
      this.reportModalLoading = false;
      if (data.error) {
        this.reportModal = { ticker, error: data.error };
      } else {
        this.reportModal = { ticker };
        this.reportModalList = Array.isArray(data) ? data : (data.list || []);
      }
      this.$nextTick(() => this.refreshIcons());
    },

    closeReportModal() {
      this.reportModal = null;
      this.reportModalList = null;
    },

    pdfUrl(ticker, basename) {
      if (!basename) return '';
      return '/dash/pdf/' + encodeURIComponent(ticker) + '/' + encodeURIComponent(basename);
    },

    /* ── record tab ── */
    async loadRecord() {
      if (this.record) return;
      const [decisions, trades, todo] = await Promise.all([
        this.api('/api/decisions'),
        this.api('/api/trades'),
        this.api('/api/invest_todo'),
      ]);
      this.record = {
        decisions: decisions.error ? [] : (decisions.items || []),
        trades: trades.error ? {} : trades,
        todo: todo.error ? '' : (todo.text || ''),
      };
    },

    regimeColor(regime) {
      if (!regime) return 'bg-slate-100 text-slate-600';
      const r = regime.toLowerCase();
      if (r.includes('공격') || r === 'offensive') return 'bg-green-100 text-green-700';
      if (r.includes('위기') || r === 'crisis') return 'bg-red-100 text-red-700';
      return 'bg-amber-100 text-amber-700';
    },

    async submitDecision() {
      const f = this.decisionForm;
      if (!f.regime) { this.showRecordToast('레짐을 선택하세요'); return; }
      const body = JSON.stringify({
        log_type: 'decision',
        date: f.date || new Date().toISOString().slice(0,10),
        regime: f.regime,
        notes: f.memo,
      });
      const r = await fetch('/api/decisions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
      const d = await r.json();
      if (d.error) { this.showRecordToast('오류: ' + d.error); return; }
      this.showRecordToast(d.message || '저장됨');
      this.decisionForm = { show: false, date: '', regime: '', memo: '' };
      this.record = null;
      await this.loadRecord();
    },

    showRecordToast(msg) {
      this.recordToast = msg;
      setTimeout(() => { this.recordToast = ''; }, 3000);
    },

    refreshIcons() {
      if (window.lucide) lucide.createIcons();
    },

    async api(path) {
      try {
        const r = await fetch(path);
        if (!r.ok) throw new Error(r.status);
        return await r.json();
      } catch (e) {
        console.error('api', path, e);
        return { error: String(e) };
      }
    },

    won(n) {
      if (n == null || isNaN(Number(n))) return '-';
      return Number(n).toLocaleString('ko-KR') + '원';
    },

    pct(n) {
      if (n == null || isNaN(Number(n))) return '-';
      const v = Number(n);
      return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
    },

    usd(n) {
      if (n == null || isNaN(Number(n))) return '-';
      return '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    },

    regimeBadgeClass(color) {
      if (color === 'green') return 'bg-green-100 text-green-700';
      if (color === 'red') return 'bg-red-100 text-red-700';
      return 'bg-amber-100 text-amber-700';
    },

    /* ── market tab ── */
    async loadMarket() {
      /* stale-while-revalidate: 데이터 이미 있으면 null로 비우지 않고
         백그라운드로 fetch 후 도착 시 교체. 탭 최초 진입 시에만 로딩 표시. */
      const data = await this.api('/api/market');
      if (!data.error) this.market = data;
    },

    chgClass(v) {
      if (v == null || isNaN(Number(v))) return 'text-slate-500';
      return Number(v) > 0 ? 'text-green-600' : (Number(v) < 0 ? 'text-red-500' : 'text-slate-500');
    },

    chgStr(v) {
      if (v == null || isNaN(Number(v))) return '-';
      const n = Number(v);
      return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
    },

    async searchMarketStock() {
      const q = this.marketStockQuery.trim().toUpperCase();
      if (!q) return;
      this.marketStockLoading = true;
      this.marketStockResult = null;
      const data = await this.api('/api/stock/' + encodeURIComponent(q));
      this.marketStockLoading = false;
      this.marketStockResult = data;
      this.$nextTick(() => this.refreshIcons());
    },

    gapClass(gap) {
      if (gap === null || gap === undefined) return 'text-slate-500';
      if (gap >= 0) return 'text-red-600 font-bold';
      if (gap >= -5) return 'text-orange-500 font-semibold';
      return 'text-slate-600';
    },

    pnlClass(v) {
      if (v == null || isNaN(Number(v))) return 'text-slate-500';
      return Number(v) >= 0 ? 'text-green-600' : 'text-red-600';
    },

    consBadgeClass(chg) {
      if (chg == null) return 'text-slate-500';
      return Number(chg) >= 0 ? 'text-green-600' : 'text-red-600';
    },

    /* ── US 애널리스트 탭 ── */
    usSeg: 'candidates',
    usCandidates: null,
    usScan: null,
    usAnalysts: null,
    usAnalystsLoading: false,
    usCandidatesMinUpside: 20,
    usCandidatesTierSOnly: false,
    usModal: null,
    usModalRatings: null,
    usModalConsensus: null,
    usModalResearch: null,

    setUsSeg(s) {
      this.usSeg = s;
      if (s === 'analysts' && !this.usAnalysts) this.loadUsAnalysts();
      this.$nextTick(() => this.refreshIcons());
    },

    async loadUsCandidates() {
      const data = await this.api('/api/us/candidates');
      if (!data.error) this.usCandidates = data;
    },

    async loadUsScan() {
      const data = await this.api('/api/us/scan');
      if (!data.error) this.usScan = data;
    },

    async loadUsAnalysts() {
      this.usAnalystsLoading = true;
      const data = await this.api('/api/us/analysts');
      this.usAnalystsLoading = false;
      if (!data.error) this.usAnalysts = data;
    },

    filteredCandidates() {
      if (!this.usCandidates || !this.usCandidates.candidates) return [];
      let list = this.usCandidates.candidates.filter(c => {
        if (c.upside_pct < this.usCandidatesMinUpside) return false;
        if (this.usCandidatesTierSOnly && c.tier_s_count < 1) return false;
        return true;
      });
      return list.slice().sort((a, b) => b.upside_pct - a.upside_pct);
    },

    hmColorUs(upside) {
      if (upside >= 80) return 'bg-emerald-100 text-emerald-700';
      if (upside >= 50) return 'bg-green-100 text-green-700';
      if (upside >= 30) return 'bg-lime-100 text-lime-700';
      return 'bg-slate-100 text-slate-600';
    },

    usSuccessColor(rate) {
      const r = Number(rate);
      if (r >= 60) return 'text-emerald-600 font-semibold';
      if (r >= 45) return 'text-green-600';
      if (r >= 30) return 'text-amber-600';
      return 'text-slate-500';
    },

    usActionBadge(action) {
      if (!action) return 'bg-slate-100 text-slate-600';
      const a = action.toLowerCase();
      if (a === 'upgrades') return 'bg-green-100 text-green-700';
      if (a === 'downgrades') return 'bg-red-100 text-red-700';
      if (a === 'initiates') return 'bg-blue-100 text-blue-700';
      return 'bg-slate-100 text-slate-600';
    },

    async openUsModal(ticker) {
      this.usModal = { ticker, loading: true };
      this.usModalRatings = null;
      this.usModalConsensus = null;
      this.usModalResearch = null;
      this.$nextTick(() => this.refreshIcons());
      const [r, c, res] = await Promise.all([
        this.api('/api/us/ratings?ticker=' + encodeURIComponent(ticker)),
        this.api('/api/us/consensus?ticker=' + encodeURIComponent(ticker)),
        this.api('/api/us/analyst_research?ticker=' + encodeURIComponent(ticker)),
      ]);
      this.usModal = { ticker, loading: false };
      this.usModalRatings = r.error ? null : r;
      this.usModalConsensus = c.error ? null : c;
      this.usModalResearch = res;
      this.$nextTick(() => this.refreshIcons());
    },

    closeUsModal() {
      this.usModal = null;
      this.usModalRatings = null;
      this.usModalConsensus = null;
      this.usModalResearch = null;
    },

    usdCompact(n) {
      if (n == null || isNaN(Number(n))) return '-';
      const v = Number(n);
      if (v >= 1e12) return '$' + (v / 1e12).toFixed(1) + 'T';
      if (v >= 1e9) return '$' + (v / 1e9).toFixed(1) + 'B';
      if (v >= 1e6) return '$' + (v / 1e6).toFixed(1) + 'M';
      return '$' + v.toLocaleString('en-US', {maximumFractionDigits: 0});
    }
  };
}
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시세 탭 패널 HTML
# 지수 4카드 / 급등락(KR+US) / 거래량 / 종목 직접 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_MARKET_PANEL = (
    '    <!-- 시세 탭 패널 -->\n'
    '    <section x-show="activeTab===\'market\'" x-cloak>\n'
    '\n'
    '      <!-- 로딩 스켈레톤 (최초 1회만) -->\n'
    '      <template x-if="!market">\n'
    '        <div class="animate-pulse">\n'
    '          <!-- 지수 4카드 -->\n'
    '          <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">\n'
    '            <template x-for="i in [1,2,3,4]" :key="i">\n'
    '              <div class="bg-white rounded-xl border border-slate-100 p-4">\n'
    '                <div class="h-2.5 w-14 bg-slate-200 rounded mb-2"></div>\n'
    '                <div class="h-5 w-20 bg-slate-200 rounded mb-1"></div>\n'
    '                <div class="h-3 w-12 bg-slate-200 rounded"></div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '          <!-- 종목 조회 박스 -->\n'
    '          <div class="bg-white rounded-xl border border-slate-100 p-5 mb-6">\n'
    '            <div class="h-3 w-24 bg-slate-200 rounded mb-3"></div>\n'
    '            <div class="h-9 w-full bg-slate-200 rounded-lg"></div>\n'
    '          </div>\n'
    '          <!-- 급등락 리스트 -->\n'
    '          <div class="bg-white rounded-xl border border-slate-100 p-5">\n'
    '            <div class="flex gap-2 mb-4">\n'
    '              <template x-for="p in [1,2,3]" :key="p">\n'
    '                <div class="h-7 w-16 bg-slate-200 rounded-full"></div>\n'
    '              </template>\n'
    '            </div>\n'
    '            <template x-for="r in [1,2,3,4,5]" :key="r">\n'
    '              <div class="flex items-center gap-3 py-2.5 border-b border-slate-50 last:border-0">\n'
    '                <div class="flex-1">\n'
    '                  <div class="h-3 w-32 bg-slate-200 rounded mb-1"></div>\n'
    '                  <div class="h-3 w-20 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '                <div class="h-4 w-14 bg-slate-200 rounded ml-auto"></div>\n'
    '                <div class="h-4 w-12 bg-slate-200 rounded"></div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '      <template x-if="market">\n'
    '        <div>\n'
    '\n'
    '          <!-- 지수 4카드 -->\n'
    '          <template x-if="market.indices && market.indices.length">\n'
    '            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">\n'
    '              <template x-for="idx in market.indices" :key="idx.name">\n'
    '                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                  <div class="flex items-center justify-between mb-1">\n'
    '                    <span class="text-xs font-semibold text-slate-500 uppercase tracking-wide" x-text="idx.name"></span>\n'
    '                    <span class="text-xs px-1.5 py-0.5 rounded"\n'
    '                          :class="idx.market===\'US\' ? \'bg-blue-50 text-blue-500\' : \'bg-slate-100 text-slate-500\'"\n'
    '                          x-text="idx.market"></span>\n'
    '                  </div>\n'
    '                  <div class="text-lg font-bold text-slate-800"\n'
    '                       x-text="idx.price != null ? idx.price.toLocaleString(\'ko-KR\', {maximumFractionDigits: 2}) : \'-\'"></div>\n'
    '                  <div :class="chgClass(idx.change_pct)" class="text-sm font-semibold mt-0.5"\n'
    '                       x-text="chgStr(idx.change_pct)"></div>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- KR 섹터 히트맵 -->\n'
    '          <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-6">\n'
    '            <div class="flex items-center gap-3 mb-3">\n'
    '              <h2 class="text-sm font-semibold text-slate-700">KR 섹터 동향</h2>\n'
    '              <template x-if="sectorHeatmap && sectorHeatmap.date">\n'
    '                <span class="text-xs text-slate-400" x-text="sectorHeatmap.date.slice(0,4)+\'.\'+sectorHeatmap.date.slice(4,6)+\'.\'+sectorHeatmap.date.slice(6)"></span>\n'
    '              </template>\n'
    '            </div>\n'
    '            <!-- 로딩 스켈레톤 -->\n'
    '            <template x-if="sectorHeatmapLoading && !sectorHeatmap">\n'
    '              <div class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-7 gap-1.5 animate-pulse">\n'
    '                <template x-for="i in [1,2,3,4,5,6,7,8,9,10,11,12,13,14]" :key="i">\n'
    '                  <div class="bg-slate-100 rounded-lg h-16"></div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '            <!-- 데이터 -->\n'
    '            <template x-if="sectorHeatmap && sectorHeatmap.sectors && sectorHeatmap.sectors.length">\n'
    '              <div class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-7 gap-1.5">\n'
    '                <template x-for="s in sectorHeatmap.sectors" :key="s.sector">\n'
    '                  <div class="rounded-lg p-2 text-center h-16 md:h-20 flex flex-col items-center justify-center cursor-pointer hover:brightness-110 hover:ring-2 hover:ring-white/60 transition-all"\n'
    '                       :class="hmColor(s.avg_chg)">\n'
    '                    <div class="text-xs font-semibold truncate w-full text-center leading-tight" x-text="s.sector"></div>\n'
    '                    <div class="text-sm font-bold mt-0.5" x-text="(s.avg_chg >= 0 ? \'+\' : \'\') + s.avg_chg.toFixed(2) + \'%\'"></div>\n'
    '                    <div class="text-xs opacity-80" x-text="s.n_stocks + \'종목\'"></div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '            <!-- 빈 상태 -->\n'
    '            <template x-if="!sectorHeatmapLoading && (!sectorHeatmap || !sectorHeatmap.sectors || !sectorHeatmap.sectors.length)">\n'
    '              <div class="text-slate-400 text-sm py-4 text-center">섹터 데이터 없음 (장 마감 후 반영)</div>\n'
    '            </template>\n'
    '          </div>\n'
    '\n'
    '          <!-- 마켓맵 트리맵 -->\n'
    '          <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-6">\n'
    '            <!-- 헤더 -->\n'
    '            <div class="flex flex-wrap items-center gap-3 mb-3">\n'
    '              <h2 class="text-sm font-semibold text-slate-700">마켓맵</h2>\n'
    '              <template x-if="marketmap[marketmapMarket] && marketmap[marketmapMarket].as_of">\n'
    '                <span class="text-xs text-slate-400"\n'
    '                      x-text="marketmap[marketmapMarket] && marketmap[marketmapMarket].as_of ? marketmap[marketmapMarket].as_of.slice(0,4)+\'.\'+marketmap[marketmapMarket].as_of.slice(4,6)+\'.\'+marketmap[marketmapMarket].as_of.slice(6) : \'\'"></span>\n'
    '              </template>\n'
    '              <!-- KOSPI / KOSDAQ 토글 -->\n'
    '              <div class="flex gap-1 ml-auto">\n'
    '                <button @click="loadMarketmap(\'kospi\')"\n'
    '                        :class="marketmapMarket===\'kospi\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                        class="text-xs px-3 py-1.5 rounded-full transition-colors">KOSPI</button>\n'
    '                <button @click="loadMarketmap(\'kosdaq\')"\n'
    '                        :class="marketmapMarket===\'kosdaq\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                        class="text-xs px-3 py-1.5 rounded-full transition-colors">KOSDAQ</button>\n'
    '              </div>\n'
    '              <!-- 색 범례 (sm 이상에서만 표시) -->\n'
    '              <div class="hidden sm:flex items-center gap-1">\n'
    '                <span class="text-xs text-slate-400">-5%</span>\n'
    '                <div class="w-24 h-2 rounded-full" style="background:linear-gradient(to right,#b91c1c,#ef4444,#fca5a5,#f1f5f9,#86efac,#22c55e,#15803d)"></div>\n'
    '                <span class="text-xs text-slate-400">+5%</span>\n'
    '              </div>\n'
    '            </div>\n'
    '            <!-- 로딩 스켈레톤 -->\n'
    '            <template x-if="marketmapLoading && !marketmap[marketmapMarket]">\n'
    '              <div class="animate-pulse bg-slate-100 rounded-lg" :style="`height:${mmHeight()}px`"></div>\n'
    '            </template>\n'
    '            <!-- 트리맵 컨테이너 -->\n'
    '            <div id="marketmap-container"\n'
    '                 x-show="marketmap[marketmapMarket] && marketmap[marketmapMarket].data && marketmap[marketmapMarket].data.length"\n'
    '                 :style="`height:${mmHeight()}px`"\n'
    '                 class="w-full"></div>\n'
    '            <!-- 빈 상태 -->\n'
    '            <template x-if="!marketmapLoading && (!marketmap[marketmapMarket] || !marketmap[marketmapMarket].data || !marketmap[marketmapMarket].data.length)">\n'
    '              <div class="text-slate-400 text-sm py-8 text-center">마켓맵 데이터 없음 (장 마감 후 반영)</div>\n'
    '            </template>\n'
    '            <!-- 풋노트 -->\n'
    '            <template x-if="marketmap[marketmapMarket] && marketmap[marketmapMarket].as_of">\n'
    '              <div class="text-xs text-slate-400 mt-2 text-right"\n'
    '                   x-text="\'시총상위 \' + (marketmap[marketmapMarket].shown_stocks||0) + \'종목 표시 / 전체 \' + (marketmap[marketmapMarket].total_stocks||0) + \'종목\'"></div>\n'
    '            </template>\n'
    '          </div>\n'
    '\n'
    '          <!-- 종목 시세 직접 조회 -->\n'
    '          <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-6">\n'
    '            <h2 class="text-sm font-semibold text-slate-700 mb-3">종목 시세 조회</h2>\n'
    '            <div class="flex gap-2">\n'
    '              <input x-model="marketStockQuery"\n'
    '                     @keyup.enter="searchMarketStock()"\n'
    '                     placeholder="티커 입력 (예: 005930 / NVDA)"\n'
    '                     class="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '              <button @click="searchMarketStock()"\n'
    '                      class="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors">\n'
    '                조회\n'
    '              </button>\n'
    '            </div>\n'
    '            <!-- 조회 결과 -->\n'
    '            <template x-if="marketStockLoading">\n'
    '              <div class="animate-pulse mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">\n'
    '                <template x-for="s in [1,2,3,4]" :key="s">\n'
    '                  <div class="bg-slate-100 rounded-lg p-3">\n'
    '                    <div class="h-2.5 w-16 bg-slate-200 rounded mb-2"></div>\n'
    '                    <div class="h-5 w-20 bg-slate-200 rounded"></div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '            <template x-if="!marketStockLoading && marketStockResult && marketStockResult.error">\n'
    '              <div class="text-red-500 text-sm mt-3" x-text="\'오류: \' + marketStockResult.error"></div>\n'
    '            </template>\n'
    '            <template x-if="!marketStockLoading && marketStockResult && !marketStockResult.error && marketStockResult.ticker">\n'
    '              <div class="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">\n'
    '                <div class="col-span-2 md:col-span-4 flex items-baseline gap-2 mb-1">\n'
    '                  <span class="text-base font-bold text-slate-800" x-text="marketStockResult.name || marketStockResult.ticker"></span>\n'
    '                  <span class="text-xs text-slate-400" x-text="marketStockResult.ticker"></span>\n'
    '                  <span class="text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-500" x-text="marketStockResult.market || \'\'"></span>\n'
    '                </div>\n'
    '                <div class="bg-slate-50 rounded-lg p-3">\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">현재가</div>\n'
    '                  <div class="font-semibold text-slate-800"\n'
    '                       x-text="marketStockResult.cur_price != null ? (marketStockResult.market===\'US\' ? usd(marketStockResult.cur_price) : won(marketStockResult.cur_price)) : \'-\'"></div>\n'
    '                  <div :class="pnlClass(marketStockResult.chg_rate)" class="text-xs"\n'
    '                       x-text="marketStockResult.chg_rate != null ? chgStr(marketStockResult.chg_rate) : \'\'"></div>\n'
    '                </div>\n'
    '                <div class="bg-slate-50 rounded-lg p-3">\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">PER / PBR</div>\n'
    '                  <div class="font-semibold text-slate-800"\n'
    '                       x-text="(marketStockResult.per != null ? marketStockResult.per : \'-\') + \' / \' + (marketStockResult.pbr != null ? marketStockResult.pbr : \'-\')"></div>\n'
    '                </div>\n'
    '                <div class="bg-slate-50 rounded-lg p-3">\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">외인 순매수</div>\n'
    '                  <div :class="pnlClass(marketStockResult.foreign_net)" class="font-semibold"\n'
    '                       x-text="marketStockResult.foreign_net != null ? marketStockResult.foreign_net.toLocaleString(\'ko-KR\') : \'-\'"></div>\n'
    '                </div>\n'
    '                <div class="bg-slate-50 rounded-lg p-3">\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">기관 순매수</div>\n'
    '                  <div :class="pnlClass(marketStockResult.inst_net)" class="font-semibold"\n'
    '                       x-text="marketStockResult.inst_net != null ? marketStockResult.inst_net.toLocaleString(\'ko-KR\') : \'-\'"></div>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '\n'
    '          <!-- 급등락 / 거래량 탭 -->\n'
    '          <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">\n'
    '            <div class="flex gap-2 mb-4">\n'
    '              <button @click="marketMoverSeg=\'kr\'"\n'
    '                :class="marketMoverSeg===\'kr\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">KR 등락</button>\n'
    '              <button @click="marketMoverSeg=\'us\'"\n'
    '                :class="marketMoverSeg===\'us\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">US 등락</button>\n'
    '              <button @click="marketMoverSeg=\'vol\'"\n'
    '                :class="marketMoverSeg===\'vol\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">거래량</button>\n'
    '              <button @click="marketMoverSeg=\'macro\'; loadMacroPanel()"\n'
    '                :class="marketMoverSeg===\'macro\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">매크로</button>\n'
    '              <button @click="marketMoverSeg=\'supply\'; loadSupply(supplySeg)"\n'
    '                :class="marketMoverSeg===\'supply\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">수급</button>\n'
    '            </div>\n'
    '\n'
    '            <!-- KR 등락 -->\n'
    '            <template x-if="marketMoverSeg===\'kr\'">\n'
    '              <div class="grid grid-cols-1 md:grid-cols-2 gap-6">\n'
    '                <!-- KR 상승 -->\n'
    '                <div>\n'
    '                  <div class="flex items-center gap-2 mb-2">\n'
    '                    <h3 class="text-xs font-semibold text-green-600 uppercase tracking-wider">KR 상승 TOP</h3>\n'
    '                    <template x-if="market.movers_kr_as_of">\n'
    '                      <span class="text-xs text-slate-400" x-text="market.movers_kr_as_of ? market.movers_kr_as_of.slice(0,4)+\'.\'+market.movers_kr_as_of.slice(4,6)+\'.\'+market.movers_kr_as_of.slice(6)+\' 종가 기준\' : \'\'"></span>\n'
    '                    </template>\n'
    '                  </div>\n'
    '                  <template x-if="!market.movers_kr_up || !market.movers_kr_up.length">\n'
    '                    <div class="text-slate-400 text-sm py-2">데이터 없음 (장 마감 시간 외)</div>\n'
    '                  </template>\n'
    '                  <template x-if="market.movers_kr_up && market.movers_kr_up.length">\n'
    '                    <table class="w-full text-sm">\n'
    '                      <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                        <th class="text-left py-1.5 font-medium">종목</th>\n'
    '                        <th class="text-right py-1.5 font-medium">현재가</th>\n'
    '                        <th class="text-right py-1.5 font-medium">등락</th>\n'
    '                      </tr></thead>\n'
    '                      <tbody>\n'
    '                        <template x-for="s in market.movers_kr_up" :key="s.ticker">\n'
    '                          <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                            <td class="py-1.5">\n'
    '                              <span class="font-medium text-slate-800 text-sm" x-text="s.name"></span>\n'
    '                              <span class="text-xs text-slate-400 ml-1" x-text="s.ticker"></span>\n'
    '                            </td>\n'
    '                            <td class="py-1.5 text-right text-slate-700 text-sm" x-text="s.price != null ? s.price.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                            <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.chg_pct)" x-text="chgStr(s.chg_pct)"></td>\n'
    '                          </tr>\n'
    '                        </template>\n'
    '                      </tbody>\n'
    '                    </table>\n'
    '                  </template>\n'
    '                </div>\n'
    '                <!-- KR 하락 -->\n'
    '                <div>\n'
    '                  <div class="flex items-center gap-2 mb-2">\n'
    '                    <h3 class="text-xs font-semibold text-red-500 uppercase tracking-wider">KR 하락 TOP</h3>\n'
    '                    <template x-if="market.movers_kr_as_of">\n'
    '                      <span class="text-xs text-slate-400" x-text="market.movers_kr_as_of ? market.movers_kr_as_of.slice(0,4)+\'.\'+market.movers_kr_as_of.slice(4,6)+\'.\'+market.movers_kr_as_of.slice(6)+\' 종가 기준\' : \'\'"></span>\n'
    '                    </template>\n'
    '                  </div>\n'
    '                  <template x-if="!market.movers_kr_down || !market.movers_kr_down.length">\n'
    '                    <div class="text-slate-400 text-sm py-2">데이터 없음 (장 마감 시간 외)</div>\n'
    '                  </template>\n'
    '                  <template x-if="market.movers_kr_down && market.movers_kr_down.length">\n'
    '                    <table class="w-full text-sm">\n'
    '                      <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                        <th class="text-left py-1.5 font-medium">종목</th>\n'
    '                        <th class="text-right py-1.5 font-medium">현재가</th>\n'
    '                        <th class="text-right py-1.5 font-medium">등락</th>\n'
    '                      </tr></thead>\n'
    '                      <tbody>\n'
    '                        <template x-for="s in market.movers_kr_down" :key="s.ticker">\n'
    '                          <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                            <td class="py-1.5">\n'
    '                              <span class="font-medium text-slate-800 text-sm" x-text="s.name"></span>\n'
    '                              <span class="text-xs text-slate-400 ml-1" x-text="s.ticker"></span>\n'
    '                            </td>\n'
    '                            <td class="py-1.5 text-right text-slate-700 text-sm" x-text="s.price != null ? s.price.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                            <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.chg_pct)" x-text="chgStr(s.chg_pct)"></td>\n'
    '                          </tr>\n'
    '                        </template>\n'
    '                      </tbody>\n'
    '                    </table>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- US 등락 -->\n'
    '            <template x-if="marketMoverSeg===\'us\'">\n'
    '              <div class="grid grid-cols-1 md:grid-cols-2 gap-6">\n'
    '                <!-- US 상승 -->\n'
    '                <div>\n'
    '                  <h3 class="text-xs font-semibold text-green-600 uppercase tracking-wider mb-2">US 상승 TOP (NAS)</h3>\n'
    '                  <template x-if="!market.movers_us_up || !market.movers_us_up.length">\n'
    '                    <div class="text-slate-400 text-sm py-2">데이터 없음 (미장 마감 시간 외)</div>\n'
    '                  </template>\n'
    '                  <template x-if="market.movers_us_up && market.movers_us_up.length">\n'
    '                    <table class="w-full text-sm">\n'
    '                      <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                        <th class="text-left py-1.5 font-medium">종목</th>\n'
    '                        <th class="text-right py-1.5 font-medium">가격</th>\n'
    '                        <th class="text-right py-1.5 font-medium">등락</th>\n'
    '                      </tr></thead>\n'
    '                      <tbody>\n'
    '                        <template x-for="s in market.movers_us_up" :key="s.ticker">\n'
    '                          <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                            <td class="py-1.5">\n'
    '                              <span class="font-medium text-slate-800 text-sm" x-text="s.name || s.ticker"></span>\n'
    '                              <span class="text-xs text-slate-400 ml-1" x-text="s.ticker"></span>\n'
    '                            </td>\n'
    '                            <td class="py-1.5 text-right text-slate-700 text-sm" x-text="s.price != null ? usd(s.price) : \'-\'"></td>\n'
    '                            <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.chg_pct)" x-text="chgStr(s.chg_pct)"></td>\n'
    '                          </tr>\n'
    '                        </template>\n'
    '                      </tbody>\n'
    '                    </table>\n'
    '                  </template>\n'
    '                </div>\n'
    '                <!-- US 하락 -->\n'
    '                <div>\n'
    '                  <h3 class="text-xs font-semibold text-red-500 uppercase tracking-wider mb-2">US 하락 TOP (NAS)</h3>\n'
    '                  <template x-if="!market.movers_us_down || !market.movers_us_down.length">\n'
    '                    <div class="text-slate-400 text-sm py-2">데이터 없음 (미장 마감 시간 외)</div>\n'
    '                  </template>\n'
    '                  <template x-if="market.movers_us_down && market.movers_us_down.length">\n'
    '                    <table class="w-full text-sm">\n'
    '                      <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                        <th class="text-left py-1.5 font-medium">종목</th>\n'
    '                        <th class="text-right py-1.5 font-medium">가격</th>\n'
    '                        <th class="text-right py-1.5 font-medium">등락</th>\n'
    '                      </tr></thead>\n'
    '                      <tbody>\n'
    '                        <template x-for="s in market.movers_us_down" :key="s.ticker">\n'
    '                          <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                            <td class="py-1.5">\n'
    '                              <span class="font-medium text-slate-800 text-sm" x-text="s.name || s.ticker"></span>\n'
    '                              <span class="text-xs text-slate-400 ml-1" x-text="s.ticker"></span>\n'
    '                            </td>\n'
    '                            <td class="py-1.5 text-right text-slate-700 text-sm" x-text="s.price != null ? usd(s.price) : \'-\'"></td>\n'
    '                            <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.chg_pct)" x-text="chgStr(s.chg_pct)"></td>\n'
    '                          </tr>\n'
    '                        </template>\n'
    '                      </tbody>\n'
    '                    </table>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- 거래량 상위 -->\n'
    '            <template x-if="marketMoverSeg===\'vol\'">\n'
    '              <div>\n'
    '                <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">KR 체결강도/거래량 상위</h3>\n'
    '                <template x-if="!market.volume_top || !market.volume_top.length">\n'
    '                  <div class="text-slate-400 text-sm py-2">데이터 없음 (장 마감 시간 외)</div>\n'
    '                </template>\n'
    '                <template x-if="market.volume_top && market.volume_top.length">\n'
    '                  <table class="w-full text-sm">\n'
    '                    <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                      <th class="text-left py-1.5 font-medium">종목</th>\n'
    '                      <th class="text-right py-1.5 font-medium">현재가</th>\n'
    '                      <th class="text-right py-1.5 font-medium">등락</th>\n'
    '                      <th class="text-right py-1.5 font-medium">거래량</th>\n'
    '                    </tr></thead>\n'
    '                    <tbody>\n'
    '                      <template x-for="s in market.volume_top" :key="s.ticker">\n'
    '                        <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                          <td class="py-1.5">\n'
    '                            <span class="font-medium text-slate-800 text-sm" x-text="s.name"></span>\n'
    '                            <span class="text-xs text-slate-400 ml-1" x-text="s.ticker"></span>\n'
    '                          </td>\n'
    '                          <td class="py-1.5 text-right text-slate-700 text-sm" x-text="s.price != null ? s.price.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                          <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.chg_pct)" x-text="chgStr(s.chg_pct)"></td>\n'
    '                          <td class="py-1.5 text-right text-xs text-slate-500"\n'
    '                              x-text="s.volume != null ? Number(s.volume).toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                        </tr>\n'
    '                      </template>\n'
    '                    </tbody>\n'
    '                  </table>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '          </div><!-- /급등락·거래량 카드 -->\n'
    '\n'
    '          <!-- 매크로 패널 -->\n'
    '          <template x-if="marketMoverSeg===\'macro\'">\n'
    '            <div class="space-y-4">\n'
    '\n'
    '              <!-- 로딩 스켈레톤 -->\n'
    '              <template x-if="macroPanelLoading && !macroPanel">\n'
    '                <div class="space-y-4">\n'
    '                  <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 h-12 animate-pulse bg-slate-100"></div>\n'
    '                  <div class="grid grid-cols-2 md:grid-cols-4 gap-3">\n'
    '                    <template x-for="i in [1,2,3,4]" :key="i"><div class="bg-slate-100 animate-pulse rounded-xl h-16"></div></template>\n'
    '                  </div>\n'
    '                  <div class="grid grid-cols-1 md:grid-cols-2 gap-3">\n'
    '                    <div class="bg-slate-100 animate-pulse rounded-xl h-24"></div>\n'
    '                    <div class="bg-slate-100 animate-pulse rounded-xl h-24"></div>\n'
    '                  </div>\n'
    '                  <div class="bg-slate-100 animate-pulse rounded-xl h-32"></div>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 실데이터 -->\n'
    '              <template x-if="macroPanel">\n'
    '                <div class="space-y-4">\n'
    '\n'
    '                  <!-- A: 레짐 배너 -->\n'
    '                  <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4 flex items-center gap-3">\n'
    '                    <template x-if="macroPanel.regime">\n'
    '                      <div class="flex items-center gap-3 w-full">\n'
    '                        <span class="text-xs font-semibold px-2.5 py-1 rounded-full"\n'
    '                              :class="regimeBadgeClass(macroPanel.regime.color)"\n'
    '                              x-text="macroPanel.regime.label"></span>\n'
    '                        <span class="text-sm text-slate-600"\n'
    '                              x-text="macroPanel.regime.regime_en === \'offensive\' ? \'공격형\' : macroPanel.regime.regime_en === \'defensive\' ? \'수비형\' : macroPanel.regime.regime_en === \'crisis\' ? \'위기\' : macroPanel.regime.regime_en"></span>\n'
    '                        <template x-if="macroPanel.regime.days != null">\n'
    '                          <span class="text-xs text-slate-400" x-text="macroPanel.regime.days + \'일째\'"></span>\n'
    '                        </template>\n'
    '                      </div>\n'
    '                    </template>\n'
    '                    <template x-if="!macroPanel.regime">\n'
    '                      <span class="text-sm text-slate-400">레짐 데이터 없음</span>\n'
    '                    </template>\n'
    '                  </div>\n'
    '\n'
    '                  <!-- B: 핵심 지표 카드 -->\n'
    '                  <template x-if="macroPanel.indicators && macroPanel.indicators.length">\n'
    '                    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">\n'
    '                      <template x-for="ind in macroPanel.indicators" :key="ind.label">\n'
    '                        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                          <div class="text-xs text-slate-500 mb-1" x-text="ind.label"></div>\n'
    '                          <div class="text-lg font-bold text-slate-800"\n'
    '                               :class="ind.label===\'VIX\' && ind.value != null ? (Number(ind.value) >= 30 ? \'text-red-600\' : Number(ind.value) >= 20 ? \'text-amber-500\' : \'text-slate-800\') : \'text-slate-800\'"\n'
    '                               x-text="ind.value != null ? ind.value : \'-\'"></div>\n'
    '                          <div class="text-xs mt-1"\n'
    '                               :class="chgClass(ind.chg_pct != null ? ind.chg_pct : ind.chg)"\n'
    '                               x-text="(ind.chg_pct != null ? chgStr(ind.chg_pct) : (ind.chg != null ? (Number(ind.chg) > 0 ? \'+\' : \'\') + Number(ind.chg).toFixed(2) : \'-\'))"></div>\n'
    '                        </div>\n'
    '                      </template>\n'
    '                    </div>\n'
    '                  </template>\n'
    '\n'
    '                  <!-- C: 수익률 곡선 / 침체 시그널 -->\n'
    '                  <template x-if="macroPanel.curve || macroPanel.recession_signal">\n'
    '                    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">\n'
    '                      <!-- 수익률 곡선 -->\n'
    '                      <template x-if="macroPanel.curve">\n'
    '                        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                          <div class="flex items-center gap-2 mb-3">\n'
    '                            <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider">수익률 곡선</h3>\n'
    '                            <template x-if="macroPanel.curve.spread != null && macroPanel.curve.spread < 0">\n'
    '                              <span class="text-xs font-semibold bg-red-100 text-red-600 px-1.5 py-0.5 rounded">역전중</span>\n'
    '                            </template>\n'
    '                          </div>\n'
    '                          <div class="space-y-2">\n'
    '                            <div class="flex justify-between text-sm">\n'
    '                              <span class="text-slate-500">2Y</span>\n'
    '                              <span class="font-semibold text-slate-800" x-text="macroPanel.curve.y2 != null ? macroPanel.curve.y2.toFixed(2) + \'%\' : \'-\'"></span>\n'
    '                            </div>\n'
    '                            <div class="flex justify-between text-sm">\n'
    '                              <span class="text-slate-500">10Y</span>\n'
    '                              <span class="font-semibold text-slate-800" x-text="macroPanel.curve.y10 != null ? macroPanel.curve.y10.toFixed(2) + \'%\' : \'-\'"></span>\n'
    '                            </div>\n'
    '                            <div class="flex justify-between text-sm border-t border-slate-100 pt-2">\n'
    '                              <span class="text-slate-500">스프레드</span>\n'
    '                              <span class="font-semibold"\n'
    '                                    :class="macroPanel.curve.spread != null && macroPanel.curve.spread < 0 ? \'text-red-600\' : \'text-green-600\'"\n'
    '                                    x-text="macroPanel.curve.spread != null ? (macroPanel.curve.spread > 0 ? \'+\' : \'\') + macroPanel.curve.spread.toFixed(2) + \'%\' : \'-\'"></span>\n'
    '                            </div>\n'
    '                          </div>\n'
    '                        </div>\n'
    '                      </template>\n'
    '                      <!-- 침체 시그널 (Estrella-Mishkin 1998) -->\n'
    '                      <template x-if="macroPanel.recession_signal">\n'
    '                        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                          <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">침체 시그널 (Estrella-Mishkin)</h3>\n'
    '                          <div class="flex items-center gap-2 mb-2">\n'
    '                            <span class="text-lg font-bold"\n'
    '                                  :class="macroPanel.recession_signal.includes(\'역전\') ? \'text-red-600\' : macroPanel.recession_signal.includes(\'주의\') ? \'text-amber-500\' : \'text-green-600\'"\n'
    '                                  x-text="macroPanel.recession_signal"></span>\n'
    '                          </div>\n'
    '                          <div class="text-xs text-slate-400">10Y-2Y 스프레드 기반 선행지표</div>\n'
    '                        </div>\n'
    '                      </template>\n'
    '                      <!-- recession_prob (숫자 데이터 있을 때) -->\n'
    '                      <template x-if="macroPanel.recession_prob != null">\n'
    '                        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                          <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">침체확률 (12개월 선행)</h3>\n'
    '                          <div class="flex items-end gap-2 mb-1">\n'
    '                            <span class="text-3xl font-bold"\n'
    '                                  :class="macroPanel.recession_prob >= 40 ? \'text-red-600\' : macroPanel.recession_prob >= 20 ? \'text-amber-500\' : \'text-green-600\'"\n'
    '                                  x-text="macroPanel.recession_prob.toFixed(1) + \'%\'"></span>\n'
    '                          </div>\n'
    '                          <div class="text-xs text-slate-400">Estrella-Mishkin 1998</div>\n'
    '                        </div>\n'
    '                      </template>\n'
    '                    </div>\n'
    '                  </template>\n'
    '\n'
    '                  <!-- D: Polymarket Fed -->\n'
    '                  <template x-if="macroPanel.polymarket_fed && macroPanel.polymarket_fed.length">\n'
    '                    <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                      <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Polymarket — Fed</h3>\n'
    '                      <div class="space-y-2">\n'
    '                        <template x-for="m in macroPanel.polymarket_fed" :key="m.title">\n'
    '                          <div class="flex items-center justify-between gap-2 py-1.5 border-b border-slate-50">\n'
    '                            <div class="text-sm text-slate-700 flex-1 min-w-0 truncate" x-text="m.title"></div>\n'
    '                            <div class="flex items-center gap-2 flex-shrink-0">\n'
    '                              <span class="text-sm font-semibold"\n'
    '                                    :class="m.yes_pct >= 60 ? \'text-green-600\' : m.yes_pct <= 40 ? \'text-red-500\' : \'text-slate-700\'"\n'
    '                                    x-text="m.yes_pct + \'%\'"></span>\n'
    '                              <span class="text-xs text-slate-400" x-text="\'$\' + (m.volume_usd / 1e6).toFixed(1) + \'M\'"></span>\n'
    '                            </div>\n'
    '                          </div>\n'
    '                        </template>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '\n'
    '                  <!-- E: 섹터 로테이션 -->\n'
    '                  <template x-if="macroPanel.sector_rotation && macroPanel.sector_rotation.length">\n'
    '                    <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                      <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">섹터 로테이션 (외인+기관)</h3>\n'
    '                      <table class="w-full text-sm">\n'
    '                        <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                          <th class="text-left py-1.5 font-medium">섹터</th>\n'
    '                          <th class="text-right py-1.5 font-medium">외인</th>\n'
    '                          <th class="text-right py-1.5 font-medium">기관</th>\n'
    '                          <th class="text-right py-1.5 font-medium">합산</th>\n'
    '                        </tr></thead>\n'
    '                        <tbody>\n'
    '                          <template x-for="s in macroPanel.sector_rotation" :key="s.sector">\n'
    '                            <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                              <td class="py-1.5 font-medium text-slate-800" x-text="s.sector"></td>\n'
    '                              <td class="py-1.5 text-right text-xs" :class="chgClass(s.foreign_net)" x-text="s.foreign_net != null ? Number(s.foreign_net).toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                              <td class="py-1.5 text-right text-xs" :class="chgClass(s.inst_net)" x-text="s.inst_net != null ? Number(s.inst_net).toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                              <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.combined)" x-text="s.combined != null ? Number(s.combined).toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                            </tr>\n'
    '                          </template>\n'
    '                        </tbody>\n'
    '                      </table>\n'
    '                    </div>\n'
    '                  </template>\n'
    '\n'
    '                  <!-- 오류 정보 -->\n'
    '                  <template x-if="macroPanel._errors && macroPanel._errors.length">\n'
    '                    <div class="text-xs text-slate-400 px-1">\n'
    '                      <span x-text="\'일부 소스 미수신: \' + macroPanel._errors.map(e => e.source).join(\', \')"></span>\n'
    '                    </div>\n'
    '                  </template>\n'
    '\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '            </div>\n'
    '          </template>\n'
    '          <!-- /매크로 패널 -->\n'
    '\n'
    '          <!-- ── 수급 패널 ── -->\n'
    '          <template x-if="marketMoverSeg===\'supply\'">\n'
    '            <div>\n'
    '              <!-- 수급유형 필터 pill (가로스크롤, teal-600 선택) -->\n'
    '              <div class="flex gap-2 mb-4 overflow-x-auto pb-1">\n'
    '                <button @click="loadSupply(\'foreign_rank\')"\n'
    '                  :class="supplySeg===\'foreign_rank\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">외인순매수TOP</button>\n'
    '                <button @click="loadSupply(\'combined_rank\')"\n'
    '                  :class="supplySeg===\'combined_rank\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">외인+기관합산</button>\n'
    '                <button @click="loadSupply(\'short_sale\')"\n'
    '                  :class="supplySeg===\'short_sale\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">공매도</button>\n'
    '                <button @click="loadSupply(\'credit\')"\n'
    '                  :class="supplySeg===\'credit\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">신용잔고</button>\n'
    '                <button @click="loadSupply(\'lending\')"\n'
    '                  :class="supplySeg===\'lending\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">대차</button>\n'
    '              </div>\n'
    '\n'
    '              <!-- 로딩 스켈레톤 -->\n'
    '              <template x-if="supplyLoading && !supplyData[supplySeg]">\n'
    '                <div class="animate-pulse space-y-2">\n'
    '                  <template x-for="i in [1,2,3,4,5]" :key="i">\n'
    '                    <div class="bg-white rounded-xl border border-slate-100 p-4 h-14"></div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 외인순매수TOP (foreign_rank) -->\n'
    '              <template x-if="supplySeg===\'foreign_rank\' && supplyData[\'foreign_rank\']">\n'
    '                <div>\n'
    '                  <template x-if="supplyData[\'foreign_rank\'].error">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400"\n'
    '                      x-text="supplyData[\'foreign_rank\'].error"></div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'foreign_rank\'].error && supplyData[\'foreign_rank\'].items && supplyData[\'foreign_rank\'].items.length">\n'
    '                    <div>\n'
    '                      <div class="text-xs text-slate-400 mb-2"\n'
    '                        x-text="(supplyData[\'foreign_rank\'].as_of ? supplyData[\'foreign_rank\'].as_of.slice(0,4)+\'.\'+supplyData[\'foreign_rank\'].as_of.slice(4,6)+\'.\'+supplyData[\'foreign_rank\'].as_of.slice(6) : \'\') + \' 외인 순매수 상위\'"></div>\n'
    '                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">\n'
    '                        <table class="w-full text-sm">\n'
    '                          <thead><tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">#</th>\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">종목</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">순매수주</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">순매수금액</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">등락</th>\n'
    '                          </tr></thead>\n'
    '                          <tbody>\n'
    '                            <template x-for="(s, idx) in supplyData[\'foreign_rank\'].items" :key="idx">\n'
    '                              <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                                <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="idx+1"></td>\n'
    '                                <td class="px-4 py-2.5">\n'
    '                                  <div class="font-medium text-slate-800 text-sm" x-text="s.name"></div>\n'
    '                                  <div class="text-xs text-slate-400" x-text="s.ticker"></div>\n'
    '                                </td>\n'
    '                                <td class="px-4 py-2.5 text-right text-sm"\n'
    '                                  :class="s.foreign_net_qty >= 0 ? \'text-green-600\' : \'text-red-600\'"\n'
    '                                  x-text="s.foreign_net_qty != null ? s.foreign_net_qty.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-600 hidden sm:table-cell"\n'
    '                                  x-text="s.foreign_net_amt != null ? (s.foreign_net_amt/100000000).toFixed(0)+\'억\' : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-sm font-semibold"\n'
    '                                  :class="s.chg_pct >= 0 ? \'text-green-600\' : \'text-red-600\'"\n'
    '                                  x-text="s.chg_pct != null ? (s.chg_pct >= 0 ? \'+\' : \'\')+s.chg_pct.toFixed(1)+\'%\' : \'-\'"></td>\n'
    '                              </tr>\n'
    '                            </template>\n'
    '                          </tbody>\n'
    '                        </table>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'foreign_rank\'].error && (!supplyData[\'foreign_rank\'].items || !supplyData[\'foreign_rank\'].items.length)">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400">수급 데이터 없음 (장중 반영)\n'
    '                      <button @click="loadSupply(\'foreign_rank\')" class="ml-2 text-teal-600 underline text-xs">재시도</button>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 외인+기관합산 (combined_rank) -->\n'
    '              <template x-if="supplySeg===\'combined_rank\' && supplyData[\'combined_rank\']">\n'
    '                <div>\n'
    '                  <template x-if="supplyData[\'combined_rank\'].error">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400"\n'
    '                      x-text="supplyData[\'combined_rank\'].error"></div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'combined_rank\'].error && supplyData[\'combined_rank\'].items && supplyData[\'combined_rank\'].items.length">\n'
    '                    <div>\n'
    '                      <div class="text-xs text-slate-400 mb-2">외인+기관 합산 순매수 상위</div>\n'
    '                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">\n'
    '                        <table class="w-full text-sm">\n'
    '                          <thead><tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">#</th>\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">종목</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">외인+기관(주)</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">비율%</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">등락</th>\n'
    '                          </tr></thead>\n'
    '                          <tbody>\n'
    '                            <template x-for="(s, idx) in supplyData[\'combined_rank\'].items" :key="idx">\n'
    '                              <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                                <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="idx+1"></td>\n'
    '                                <td class="px-4 py-2.5">\n'
    '                                  <div class="font-medium text-slate-800 text-sm" x-text="s.name"></div>\n'
    '                                  <div class="text-xs text-slate-400" x-text="s.ticker"></div>\n'
    '                                </td>\n'
    '                                <td class="px-4 py-2.5 text-right text-sm"\n'
    '                                  :class="s.fi_total_net >= 0 ? \'text-green-600\' : \'text-red-600\'"\n'
    '                                  x-text="s.fi_total_net != null ? s.fi_total_net.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-500 hidden sm:table-cell"\n'
    '                                  x-text="s.fi_ratio_pct != null ? s.fi_ratio_pct.toFixed(2)+\'%\' : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-sm font-semibold"\n'
    '                                  :class="s.chg_pct >= 0 ? \'text-green-600\' : \'text-red-600\'"\n'
    '                                  x-text="s.chg_pct != null ? (s.chg_pct >= 0 ? \'+\' : \'\')+s.chg_pct.toFixed(1)+\'%\' : \'-\'"></td>\n'
    '                              </tr>\n'
    '                            </template>\n'
    '                          </tbody>\n'
    '                        </table>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'combined_rank\'].error && (!supplyData[\'combined_rank\'].items || !supplyData[\'combined_rank\'].items.length)">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400">수급 데이터 없음 (장중 반영)\n'
    '                      <button @click="loadSupply(\'combined_rank\')" class="ml-2 text-teal-600 underline text-xs">재시도</button>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 공매도 (short_sale) -->\n'
    '              <template x-if="supplySeg===\'short_sale\' && supplyData[\'short_sale\']">\n'
    '                <div>\n'
    '                  <template x-if="supplyData[\'short_sale\'].error">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400"\n'
    '                      x-text="supplyData[\'short_sale\'].error"></div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'short_sale\'].error && supplyData[\'short_sale\'].items && supplyData[\'short_sale\'].items.length">\n'
    '                    <div>\n'
    '                      <div class="text-xs text-slate-400 mb-2" x-text="(supplyData[\'short_sale\'].ticker || \'\') + \' 공매도 추이 (최근 \' + supplyData[\'short_sale\'].items.length + \'일)\'"></div>\n'
    '                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">\n'
    '                        <table class="w-full text-sm">\n'
    '                          <thead><tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">날짜</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">공매도량</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">비율%</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">종가</th>\n'
    '                          </tr></thead>\n'
    '                          <tbody>\n'
    '                            <template x-for="(s, idx) in supplyData[\'short_sale\'].items" :key="idx">\n'
    '                              <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                                <td class="px-4 py-2.5 text-xs text-slate-500"\n'
    '                                  x-text="s.date ? s.date.slice(0,4)+\'.\'+s.date.slice(4,6)+\'.\'+s.date.slice(6) : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-700"\n'
    '                                  x-text="s.short_vol != null ? s.short_vol.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs font-semibold"\n'
    '                                  :class="s.short_ratio >= 5 ? \'text-red-600\' : s.short_ratio >= 3 ? \'text-amber-600\' : \'text-slate-600\'"\n'
    '                                  x-text="s.short_ratio != null ? s.short_ratio.toFixed(2)+\'%\' : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-600 hidden sm:table-cell"\n'
    '                                  x-text="s.close != null ? s.close.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                              </tr>\n'
    '                            </template>\n'
    '                          </tbody>\n'
    '                        </table>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'short_sale\'].error && (!supplyData[\'short_sale\'].items || !supplyData[\'short_sale\'].items.length)">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400">공매도 데이터 없음\n'
    '                      <button @click="loadSupply(\'short_sale\')" class="ml-2 text-teal-600 underline text-xs">재시도</button>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 신용잔고 (credit) -->\n'
    '              <template x-if="supplySeg===\'credit\' && supplyData[\'credit\']">\n'
    '                <div>\n'
    '                  <template x-if="supplyData[\'credit\'].error">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400"\n'
    '                      x-text="supplyData[\'credit\'].error"></div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'credit\'].error && supplyData[\'credit\'].items && supplyData[\'credit\'].items.length">\n'
    '                    <div>\n'
    '                      <!-- 10% 초과 경고 배너 -->\n'
    '                      <template x-if="supplyData[\'credit\'].warning">\n'
    '                        <div class="mb-3 bg-red-50 border border-red-200 rounded-lg px-4 py-2 text-red-700 text-xs font-semibold"\n'
    '                          x-text="\'⚠️ 신용잔고 과열 경고: \' + supplyData[\'credit\'].warning"></div>\n'
    '                      </template>\n'
    '                      <div class="text-xs text-slate-400 mb-2" x-text="(supplyData[\'credit\'].ticker || \'\') + \' 신용잔고 추이\'"></div>\n'
    '                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">\n'
    '                        <table class="w-full text-sm">\n'
    '                          <thead><tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">날짜</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">신용잔고율%</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">신규</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">상환</th>\n'
    '                          </tr></thead>\n'
    '                          <tbody>\n'
    '                            <template x-for="(s, idx) in supplyData[\'credit\'].items" :key="idx">\n'
    '                              <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                                <td class="px-4 py-2.5 text-xs text-slate-500"\n'
    '                                  x-text="s.date ? s.date.slice(0,4)+\'.\'+s.date.slice(4,6)+\'.\'+s.date.slice(6) : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs font-semibold"\n'
    '                                  :class="s.credit_ratio >= 10 ? \'text-red-600\' : s.credit_ratio >= 5 ? \'text-amber-600\' : \'text-slate-600\'"\n'
    '                                  x-text="s.credit_ratio != null ? s.credit_ratio.toFixed(2)+\'%\' : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-600 hidden sm:table-cell"\n'
    '                                  x-text="s.credit_new != null ? s.credit_new.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-500 hidden sm:table-cell"\n'
    '                                  x-text="s.credit_repay != null ? s.credit_repay.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                              </tr>\n'
    '                            </template>\n'
    '                          </tbody>\n'
    '                        </table>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'credit\'].error && (!supplyData[\'credit\'].items || !supplyData[\'credit\'].items.length)">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400">신용잔고 데이터 없음\n'
    '                      <button @click="loadSupply(\'credit\')" class="ml-2 text-teal-600 underline text-xs">재시도</button>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 대차 (lending) -->\n'
    '              <template x-if="supplySeg===\'lending\' && supplyData[\'lending\']">\n'
    '                <div>\n'
    '                  <template x-if="supplyData[\'lending\'].error">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400"\n'
    '                      x-text="supplyData[\'lending\'].error"></div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'lending\'].error && supplyData[\'lending\'].items && supplyData[\'lending\'].items.length">\n'
    '                    <div>\n'
    '                      <div class="text-xs text-slate-400 mb-2" x-text="(supplyData[\'lending\'].ticker || \'\') + \' 대차잔고 추이\'"></div>\n'
    '                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">\n'
    '                        <table class="w-full text-sm">\n'
    '                          <thead><tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">날짜</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">대차잔고(주)</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">전일대비</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">잔고금액(백만)</th>\n'
    '                          </tr></thead>\n'
    '                          <tbody>\n'
    '                            <template x-for="(s, idx) in supplyData[\'lending\'].items" :key="idx">\n'
    '                              <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                                <td class="px-4 py-2.5 text-xs text-slate-500"\n'
    '                                  x-text="s.date ? s.date.slice(0,4)+\'.\'+s.date.slice(4,6)+\'.\'+s.date.slice(6) : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-700"\n'
    '                                  x-text="s.loan_balance != null ? s.loan_balance.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs hidden sm:table-cell"\n'
    '                                  :class="s.change >= 0 ? \'text-red-500\' : \'text-green-500\'"\n'
    '                                  x-text="s.change != null ? (s.change >= 0 ? \'+\' : \'\')+s.change.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-600 hidden sm:table-cell"\n'
    '                                  x-text="s.loan_balance_amt != null ? s.loan_balance_amt.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                              </tr>\n'
    '                            </template>\n'
    '                          </tbody>\n'
    '                        </table>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'lending\'].error && (!supplyData[\'lending\'].items || !supplyData[\'lending\'].items.length)">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400">대차 데이터 없음\n'
    '                      <button @click="loadSupply(\'lending\')" class="ml-2 text-teal-600 underline text-xs">재시도</button>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '            </div>\n'
    '          </template>\n'
    '          <!-- /수급 패널 -->\n'
    '\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '    </section>\n'
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 포트폴리오 패널 HTML (P2)
# 카드 클릭 → 종목 상세 모달 (GET /api/stock/{ticker})
# 정렬 pill: 평가금/수익률/손익금 — Alpine 클라이언트 정렬
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_PORTFOLIO_PANEL = (
    '    <!-- 포트폴리오 패널 -->\n'
    '    <section x-show="activeTab===\'portfolio\'" x-cloak>\n'
    '\n'
    '      <!-- 로딩 스켈레톤 (최초 1회만) -->\n'
    '      <template x-if="!portfolio">\n'
    '        <div class="animate-pulse">\n'
    '          <!-- 요약 바 셀 x4 -->\n'
    '          <div class="bg-white rounded-xl border border-slate-100 p-5 mb-5">\n'
    '            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">\n'
    '              <template x-for="i in [1,2,3,4]" :key="i">\n'
    '                <div>\n'
    '                  <div class="h-2.5 w-20 bg-slate-200 rounded mb-2"></div>\n'
    '                  <div class="h-6 w-28 bg-slate-200 rounded mb-1"></div>\n'
    '                  <div class="h-3 w-16 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </div>\n'
    '          <!-- 차트 박스 h-48 -->\n'
    '          <div class="bg-slate-100 rounded-xl h-48 mb-5"></div>\n'
    '          <!-- 종목 카드 grid x4 -->\n'
    '          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">\n'
    '            <template x-for="j in [1,2,3,4]" :key="j">\n'
    '              <div class="bg-white rounded-xl border border-slate-100 p-4">\n'
    '                <div class="flex items-start justify-between mb-3">\n'
    '                  <div>\n'
    '                    <div class="h-4 w-28 bg-slate-200 rounded mb-1"></div>\n'
    '                    <div class="h-3 w-16 bg-slate-200 rounded"></div>\n'
    '                  </div>\n'
    '                  <div class="h-4 w-14 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '                <div class="grid grid-cols-3 gap-2 mb-3">\n'
    '                  <template x-for="k in [1,2,3]" :key="k">\n'
    '                    <div>\n'
    '                      <div class="h-2.5 w-10 bg-slate-200 rounded mb-1"></div>\n'
    '                      <div class="h-3.5 w-16 bg-slate-200 rounded"></div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '                <div class="border-t border-slate-50 pt-2">\n'
    '                  <div class="h-3 w-full bg-slate-200 rounded mb-1"></div>\n'
    '                  <div class="h-3 w-3/4 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '      <template x-if="portfolio">\n'
    '        <div>\n'
    '\n'
    '          <!-- grand 요약 바 -->\n'
    '          <template x-if="portfolio.grand_eval_krw != null">\n'
    '            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-5">\n'
    '              <div class="grid grid-cols-2 md:grid-cols-4 gap-4">\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">총 자산 (원화환산)</div>\n'
    '                  <div class="text-xl font-bold text-slate-800" x-text="won(portfolio.grand_eval_krw)"></div>\n'
    '                </div>\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">총 손익</div>\n'
    '                  <div :class="pnlClass(portfolio.grand_pnl_krw)" class="text-xl font-bold"\n'
    '                       x-text="won(portfolio.grand_pnl_krw)"></div>\n'
    '                  <div :class="pnlClass(portfolio.grand_pnl_pct)" class="text-sm"\n'
    '                       x-text="pct(portfolio.grand_pnl_pct)"></div>\n'
    '                </div>\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">현금 (원)</div>\n'
    '                  <div class="text-lg font-semibold text-slate-700" x-text="won(portfolio.cash_krw)"></div>\n'
    '                </div>\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">현금 ($) / 환율</div>\n'
    '                  <div class="text-lg font-semibold text-slate-700" x-text="usd(portfolio.cash_usd)"></div>\n'
    '                  <div class="text-xs text-slate-400" x-text="portfolio.usd_krw ? \'1$=\' + Math.round(portfolio.usd_krw).toLocaleString(\'ko-KR\') + \'원\' : \'\'"></div>\n'
    '                </div>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 차트 A: 자산 추이 -->\n'
    '          <div class="bg-white rounded-xl border border-slate-200 p-4 mb-5">\n'
    '            <div class="flex items-center justify-between mb-3">\n'
    '              <span class="text-sm font-semibold text-slate-700">자산 추이</span>\n'
    '              <div class="flex gap-1">\n'
    '                <template x-for="p in [\'1M\',\'3M\',\'1Y\']" :key="p">\n'
    '                  <button @click="setPortChartPeriod(p)"\n'
    '                    :class="portChartPeriod===p ? \'bg-blue-600 text-white\' : \'bg-slate-100 text-slate-600 hover:bg-slate-200\'"\n'
    '                    class="text-xs px-2.5 py-1 rounded font-medium transition-colors"\n'
    '                    x-text="p"></button>\n'
    '                </template>\n'
    '              </div>\n'
    '            </div>\n'
    '            <template x-if="portHistoryLoading && !portHistory">\n'
    '              <div class="bg-slate-100 rounded-lg animate-pulse h-48"></div>\n'
    '            </template>\n'
    '            <div id="port-chart-empty"\n'
    '                 style="display:none"\n'
    '                 class="h-48 flex flex-col items-center justify-center text-slate-400 text-sm gap-1">\n'
    '              <span>자산 스냅샷 없음</span>\n'
    '              <span class="text-xs text-slate-300">(매일 15:50 자동 수집)</span>\n'
    '            </div>\n'
    '            <div id="port-chart-container" style="display:none"></div>\n'
    '          </div>\n'
    '\n'
    '          <!-- 정렬 pill + 보기 토글 -->\n'
    '          <div class="flex items-center gap-2 mb-4 flex-wrap">\n'
    '            <button @click="portSort=\'eval\'"\n'
    '              :class="portSort===\'eval\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">평가금순</button>\n'
    '            <button @click="portSort=\'pnl_pct\'"\n'
    '              :class="portSort===\'pnl_pct\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">수익률순</button>\n'
    '            <button @click="portSort=\'pnl\'"\n'
    '              :class="portSort===\'pnl\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">손익금순</button>\n'
    '            <div class="ml-auto flex gap-1">\n'
    '              <button @click="portView=\'list\'"\n'
    '                :class="portView===\'list\' ? \'bg-slate-700 text-white\' : \'bg-white text-slate-500 border border-slate-200\'"\n'
    '                class="p-1.5 rounded-lg transition-colors" title="리스트 보기">\n'
    '                <i data-lucide="layout-list" class="w-4 h-4"></i>\n'
    '              </button>\n'
    '              <button @click="portView=\'heatmap\'"\n'
    '                :class="portView===\'heatmap\' ? \'bg-slate-700 text-white\' : \'bg-white text-slate-500 border border-slate-200\'"\n'
    '                class="p-1.5 rounded-lg transition-colors" title="히트맵 보기">\n'
    '                <i data-lucide="grid-2x2" class="w-4 h-4"></i>\n'
    '              </button>\n'
    '            </div>\n'
    '          </div>\n'
    '\n'
    '          <!-- 히트맵 보기 -->\n'
    '          <template x-if="portView===\'heatmap\'">\n'
    '            <div>\n'
    '              <template x-if="hmPortItems().length === 0">\n'
    '                <div class="text-slate-400 text-center py-20">보유 종목이 없습니다</div>\n'
    '              </template>\n'
    '              <template x-if="hmPortItems().length > 0">\n'
    '                <div class="flex flex-wrap gap-1.5">\n'
    '                  <template x-for="item in hmPortItems()" :key="item.ticker">\n'
    '                    <div @click="openStockModal(item.ticker)"\n'
    '                         :style="\'flex-grow:\' + item.weight"\n'
    '                         class="min-w-[60px] md:min-w-[72px] h-14 md:h-20 rounded-lg flex flex-col items-center justify-center cursor-pointer hover:brightness-110 hover:ring-2 hover:ring-white/60 transition-all px-1"\n'
    '                         :class="hmColor(item.pnl_pct)">\n'
    '                      <div class="text-xs font-semibold truncate w-full text-center leading-tight" x-text="item.name && item.name.length <= 5 ? item.name : item.ticker"></div>\n'
    '                      <div class="text-sm font-bold" x-text="(item.pnl_pct >= 0 ? \'+\' : \'\') + (item.pnl_pct != null ? item.pnl_pct.toFixed(1) : \'-\') + \'%\'"></div>\n'
    '                      <div class="text-xs opacity-80 truncate w-full text-center" x-text="item.market===\'US\' ? (item.eval_amt != null ? \'$\' + Number(item.eval_amt).toLocaleString(\'en-US\', {maximumFractionDigits:0}) : \'-\') : (item.eval_amt != null ? Math.round(item.eval_amt/10000) + \'만\' : \'-\')"></div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 리스트 보기 -->\n'
    '          <template x-if="portView===\'list\'">\n'
    '            <div>\n'
    '\n'
    '          <!-- KR 종목 -->\n'
    '          <template x-if="portfolio.kr && portfolio.kr.holdings && portfolio.kr.holdings.length">\n'
    '            <div class="mb-6">\n'
    '              <h3 class="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">국내 (KR)</h3>\n'
    '              <div class="grid grid-cols-1 md:grid-cols-2 gap-3">\n'
    '                <template x-for="h in portSorted(portfolio.kr.holdings)" :key="h.ticker">\n'
    '                  <div @click="openStockModal(h.ticker)"\n'
    '                       class="bg-white rounded-xl shadow-sm border border-slate-200 p-4 cursor-pointer hover:shadow-md hover:border-blue-300 transition-all">\n'
    '                    <div class="flex items-start justify-between mb-2">\n'
    '                      <div>\n'
    '                        <div class="text-sm font-semibold text-slate-800" x-text="h.name"></div>\n'
    '                        <div class="text-xs text-slate-400" x-text="h.ticker"></div>\n'
    '                      </div>\n'
    '                      <div :class="pnlClass(h.pnl_pct)" class="text-sm font-bold" x-text="pct(h.pnl_pct)"></div>\n'
    '                    </div>\n'
    '                    <div class="grid grid-cols-3 gap-2 text-xs text-slate-500">\n'
    '                      <div><div class="text-slate-400">수량</div><div class="font-medium text-slate-700" x-text="h.qty.toLocaleString(\'ko-KR\')"></div></div>\n'
    '                      <div><div class="text-slate-400">평단</div><div class="font-medium text-slate-700" x-text="won(h.avg_price)"></div></div>\n'
    '                      <div><div class="text-slate-400">현재가</div><div class="font-medium text-slate-700 flex items-center gap-1"><span x-text="won(h.cur_price)"></span><template x-if="h.price_stale"><span class="text-xs text-amber-500 font-normal">종가</span></template></div></div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center justify-between mt-2 pt-2 border-t border-slate-50">\n'
    '                      <div class="text-xs text-slate-400">평가금액</div>\n'
    '                      <div class="text-sm font-semibold text-slate-800" x-text="won(h.eval_amt)"></div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center justify-between">\n'
    '                      <div class="text-xs text-slate-400">손익</div>\n'
    '                      <div :class="pnlClass(h.pnl)" class="text-sm font-medium" x-text="won(h.pnl)"></div>\n'
    '                    </div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- US 종목 -->\n'
    '          <template x-if="portfolio.us && portfolio.us.holdings && portfolio.us.holdings.length">\n'
    '            <div class="mb-6">\n'
    '              <h3 class="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">해외 (US)</h3>\n'
    '              <div class="grid grid-cols-1 md:grid-cols-2 gap-3">\n'
    '                <template x-for="h in portSorted(portfolio.us.holdings)" :key="h.ticker">\n'
    '                  <div @click="openStockModal(h.ticker)"\n'
    '                       class="bg-white rounded-xl shadow-sm border border-slate-200 p-4 cursor-pointer hover:shadow-md hover:border-blue-300 transition-all">\n'
    '                    <div class="flex items-start justify-between mb-2">\n'
    '                      <div>\n'
    '                        <div class="text-sm font-semibold text-slate-800" x-text="h.name"></div>\n'
    '                        <div class="text-xs text-slate-400" x-text="h.ticker"></div>\n'
    '                      </div>\n'
    '                      <div :class="pnlClass(h.pnl_pct)" class="text-sm font-bold" x-text="pct(h.pnl_pct)"></div>\n'
    '                    </div>\n'
    '                    <div class="grid grid-cols-3 gap-2 text-xs text-slate-500">\n'
    '                      <div><div class="text-slate-400">수량</div><div class="font-medium text-slate-700" x-text="h.qty"></div></div>\n'
    '                      <div><div class="text-slate-400">평단</div><div class="font-medium text-slate-700" x-text="usd(h.avg_price)"></div></div>\n'
    '                      <div><div class="text-slate-400">현재가</div><div class="font-medium text-slate-700" x-text="usd(h.cur_price)"></div></div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center justify-between mt-2 pt-2 border-t border-slate-50">\n'
    '                      <div class="text-xs text-slate-400">평가금액</div>\n'
    '                      <div class="text-sm font-semibold text-slate-800" x-text="usd(h.eval_amt)"></div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center justify-between">\n'
    '                      <div class="text-xs text-slate-400">손익</div>\n'
    '                      <div :class="pnlClass(h.pnl)" class="text-sm font-medium" x-text="usd(h.pnl)"></div>\n'
    '                    </div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 빈 상태 -->\n'
    '          <template x-if="(!portfolio.kr || !portfolio.kr.holdings || !portfolio.kr.holdings.length) && (!portfolio.us || !portfolio.us.holdings || !portfolio.us.holdings.length)">\n'
    '            <div class="text-slate-400 text-center py-20">보유 종목이 없습니다</div>\n'
    '          </template>\n'
    '\n'
    '            </div>\n'
    '          </template><!-- /portView list -->\n'
    '\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '      <!-- 종목 상세 모달 -->\n'
    '      <template x-if="portModal">\n'
    '        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="closeModal()">\n'
    '          <div class="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 p-6 relative max-h-[90vh] overflow-y-auto">\n'
    '            <button @click="closeModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-700">\n'
    '              <i data-lucide="x" class="w-5 h-5"></i>\n'
    '            </button>\n'
    '            <!-- 로딩 스켈레톤 -->\n'
    '            <template x-if="portModalLoading">\n'
    '              <div class="animate-pulse">\n'
    '                <div class="flex items-baseline gap-2 mb-3">\n'
    '                  <div class="h-5 w-32 bg-slate-200 rounded"></div>\n'
    '                  <div class="h-4 w-16 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '                <div class="h-7 w-24 bg-slate-200 rounded mb-4"></div>\n'
    '                <div class="bg-slate-100 rounded-lg h-40 mb-4"></div>\n'
    '                <div class="grid grid-cols-2 gap-3">\n'
    '                  <template x-for="n in [1,2,3,4,5,6]" :key="n">\n'
    '                    <div class="bg-slate-50 rounded-lg h-12"></div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '            <!-- 에러 -->\n'
    '            <template x-if="!portModalLoading && portModal.error">\n'
    '              <div>\n'
    '                <div class="text-sm font-bold text-slate-700 mb-2" x-text="portModal.ticker"></div>\n'
    '                <div class="text-red-500 text-sm" x-text="portModal.error"></div>\n'
    '              </div>\n'
    '            </template>\n'
    '            <!-- 데이터 -->\n'
    '            <template x-if="!portModalLoading && !portModal.error && portModal.ticker">\n'
    '              <div>\n'
    '                <!-- 헤더: 종목명 + 현재가 -->\n'
    '                <div class="flex items-baseline gap-2 mb-3">\n'
    '                  <span class="text-lg font-bold text-slate-800" x-text="portModal.name || portModal.ticker"></span>\n'
    '                  <span class="text-xs text-slate-400" x-text="portModal.ticker"></span>\n'
    '                </div>\n'
    '                <div class="flex items-baseline gap-3 mb-4">\n'
    '                  <span class="text-xl font-bold text-slate-800" x-text="portModal.cur_price != null ? (portModal.market===\'US\' ? usd(portModal.cur_price) : won(portModal.cur_price)) : \'-\'"></span>\n'
    '                  <span :class="pnlClass(portModal.chg_rate)" class="text-sm font-semibold" x-text="portModal.chg_rate != null ? pct(portModal.chg_rate) : \'\'"></span>\n'
    '                </div>\n'
    '                <!-- 캔들 차트 B -->\n'
    '                <template x-if="portModal.candles && portModal.candles.length > 0">\n'
    '                  <div class="mb-3">\n'
    '                    <div id="modal-candle-container"></div>\n'
    '                    <div id="modal-vol-container" class="mt-1"></div>\n'
    '                    <div class="flex gap-1 mt-2">\n'
    '                      <template x-for="p in [\'1M\',\'3M\',\'6M\']" :key="p">\n'
    '                        <button @click="setCandlePeriod(p)"\n'
    '                          :class="portModalCandlePeriod===p ? \'bg-blue-600 text-white\' : \'bg-slate-100 text-slate-600 hover:bg-slate-200\'"\n'
    '                          class="text-xs px-2.5 py-1 rounded font-medium transition-colors"\n'
    '                          x-text="p"></button>\n'
    '                      </template>\n'
    '                    </div>\n'
    '                  </div>\n'
    '                </template>\n'
    '                <template x-if="portModal.candles && portModal.candles.length === 0">\n'
    '                  <div class="text-center text-slate-400 text-xs py-3 mb-3">\n'
    '                    <span x-text="portModal.market===\'US\' ? \'US 종목 캔들 미지원\' : \'캔들 데이터 없음\'"></span>\n'
    '                  </div>\n'
    '                </template>\n'
    '                <!-- 메타 그리드: PER/PBR/외인/기관 -->\n'
    '                <div class="grid grid-cols-2 gap-3 text-sm">\n'
    '                  <div class="bg-slate-50 rounded-lg p-3">\n'
    '                    <div class="text-xs text-slate-400 mb-0.5">PER / PBR</div>\n'
    '                    <div class="font-semibold text-slate-800" x-text="(portModal.per != null ? portModal.per : \'-\') + \' / \' + (portModal.pbr != null ? portModal.pbr : \'-\')"></div>\n'
    '                  </div>\n'
    '                  <template x-if="portModal.foreign_net != null">\n'
    '                    <div class="bg-slate-50 rounded-lg p-3">\n'
    '                      <div class="text-xs text-slate-400 mb-0.5">외인 순매수</div>\n'
    '                      <div :class="pnlClass(portModal.foreign_net)" class="font-semibold" x-text="portModal.foreign_net != null ? portModal.foreign_net.toLocaleString(\'ko-KR\') : \'-\'"></div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="portModal.inst_net != null">\n'
    '                    <div class="bg-slate-50 rounded-lg p-3">\n'
    '                      <div class="text-xs text-slate-400 mb-0.5">기관 순매수</div>\n'
    '                      <div :class="pnlClass(portModal.inst_net)" class="font-semibold" x-text="portModal.inst_net != null ? portModal.inst_net.toLocaleString(\'ko-KR\') : \'-\'"></div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '    </section>\n'
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 워치·알림 패널 HTML (P2)
# 섹션: 손절/목표 알림 | 매수감시 | 감시종목 목록 | 추가 폼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_WATCH_PANEL = (
    '    <!-- 워치·알림 패널 -->\n'
    '    <section x-show="activeTab===\'watch\'" x-cloak>\n'
    '\n'
    '      <!-- 로딩 스켈레톤 (최초 1회만, SWR) -->\n'
    '      <template x-if="!watch">\n'
    '        <div class="animate-pulse">\n'
    '          <!-- 헤더 행 -->\n'
    '          <div class="flex items-center justify-between mb-4">\n'
    '            <div class="h-5 w-32 bg-slate-200 rounded"></div>\n'
    '            <div class="h-7 w-16 bg-slate-200 rounded-lg"></div>\n'
    '          </div>\n'
    '          <!-- 섹션 카드 x2 -->\n'
    '          <template x-for="s in [1,2]" :key="s">\n'
    '            <div class="bg-white rounded-xl border border-slate-100 p-5 mb-4">\n'
    '              <div class="h-3 w-24 bg-slate-200 rounded mb-4"></div>\n'
    '              <template x-for="r in [1,2,3,4]" :key="r">\n'
    '                <div class="flex items-center gap-3 py-2.5 border-b border-slate-50 last:border-0">\n'
    '                  <div class="rounded-full h-8 w-8 bg-slate-200 shrink-0"></div>\n'
    '                  <div class="flex-1">\n'
    '                    <div class="h-3 w-32 bg-slate-200 rounded mb-1"></div>\n'
    '                    <div class="h-3 w-20 bg-slate-200 rounded"></div>\n'
    '                  </div>\n'
    '                  <div class="ml-auto h-4 w-14 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </template>\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '      <template x-if="watch">\n'
    '        <div>\n'
    '\n'
    '          <!-- 토스트 -->\n'
    '          <template x-if="watchToast">\n'
    '            <div class="fixed top-20 right-4 z-50 bg-slate-800 text-white text-sm px-4 py-2 rounded-lg shadow-lg" x-text="watchToast"></div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 추가 폼 토글 버튼 -->\n'
    '          <div class="flex items-center justify-between mb-4">\n'
    '            <h2 class="text-base font-semibold text-slate-700">워치 &amp; 알림 관리</h2>\n'
    '            <button @click="watchForm.show = !watchForm.show"\n'
    '              :class="watchForm.show ? \'bg-slate-600\' : \'bg-blue-600\'"\n'
    '              class="text-xs text-white px-3 py-1.5 rounded-lg font-medium">\n'
    '              <span x-text="watchForm.show ? \'닫기\' : \'+ 추가\'"></span>\n'
    '            </button>\n'
    '          </div>\n'
    '\n'
    '          <!-- 추가 폼 (슬라이드) -->\n'
    '          <template x-if="watchForm.show">\n'
    '            <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-5">\n'
    '              <h3 class="text-sm font-semibold text-slate-700 mb-3">워치 / 손절·목표 / 매수감시 추가</h3>\n'
    '              <div class="grid grid-cols-2 md:grid-cols-3 gap-3">\n'
    '                <div>\n'
    '                  <label class="text-xs text-slate-500 block mb-1">티커 *</label>\n'
    '                  <input x-model="watchForm.ticker" placeholder="005930 / NVDA"\n'
    '                    class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '                </div>\n'
    '                <div>\n'
    '                  <label class="text-xs text-slate-500 block mb-1">종목명</label>\n'
    '                  <input x-model="watchForm.name" placeholder="이름 (선택)"\n'
    '                    class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '                </div>\n'
    '                <div>\n'
    '                  <label class="text-xs text-slate-500 block mb-1">매수감시가</label>\n'
    '                  <input x-model="watchForm.buy" placeholder="0 = 순수 워치"\n'
    '                    class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '                </div>\n'
    '                <div>\n'
    '                  <label class="text-xs text-slate-500 block mb-1">손절가</label>\n'
    '                  <input x-model="watchForm.stop" placeholder="선택"\n'
    '                    class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '                </div>\n'
    '                <div>\n'
    '                  <label class="text-xs text-slate-500 block mb-1">목표가</label>\n'
    '                  <input x-model="watchForm.target" placeholder="선택"\n'
    '                    class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '                </div>\n'
    '              </div>\n'
    '              <button @click="submitWatchForm()"\n'
    '                class="mt-3 bg-blue-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors">저장</button>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 손절/목표 알림 섹션 — watch.stoploss_alerts (cur/stop_price/target_price 실값) -->\n'
    '          <template x-if="watch && watch.stoploss_alerts && watch.stoploss_alerts.length">\n'
    '            <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-4">\n'
    '              <div class="flex items-center gap-2 mb-3">\n'
    '                <i data-lucide="alert-triangle" class="w-4 h-4 text-red-500"></i>\n'
    '                <h3 class="text-sm font-semibold text-slate-700">손절·목표 알림</h3>\n'
    '              </div>\n'
    '              <div class="overflow-x-auto">\n'
    '                <table class="w-full text-sm">\n'
    '                  <thead>\n'
    '                    <tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                      <th class="text-left py-2 pr-3 font-medium">종목</th>\n'
    '                      <th class="text-right py-2 pr-3 font-medium">현재가</th>\n'
    '                      <th class="text-right py-2 pr-3 font-medium">손절가</th>\n'
    '                      <th class="text-right py-2 pr-3 font-medium">목표가</th>\n'
    '                      <th class="text-right py-2 font-medium">손절 gap</th>\n'
    '                      <th class="py-2 pl-3"></th>\n'
    '                    </tr>\n'
    '                  </thead>\n'
    '                  <tbody>\n'
    '                    <template x-for="a in watch.stoploss_alerts" :key="a.ticker">\n'
    '                      <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                        <td class="py-2 pr-3">\n'
    '                          <div class="font-medium text-slate-800" x-text="a.name"></div>\n'
    '                          <div class="text-xs text-slate-400" x-text="a.ticker"></div>\n'
    '                        </td>\n'
    '                        <td class="text-right py-2 pr-3 text-slate-700"><span x-text="a.market===\'US\' ? usd(a.cur) : won(a.cur)"></span><template x-if="a.price_stale"><span class="text-xs text-amber-500 ml-1">종가</span></template></td>\n'
    '                        <td class="text-right py-2 pr-3 text-slate-600" x-text="a.stop_price ? (a.market===\'US\' ? usd(a.stop_price) : won(a.stop_price)) : \'-\'"></td>\n'
    '                        <td class="text-right py-2 pr-3 text-slate-600" x-text="a.target_price ? (a.market===\'US\' ? usd(a.target_price) : won(a.target_price)) : \'-\'"></td>\n'
    '                        <td class="text-right py-2">\n'
    '                          <span :class="gapClass(a.gap_pct)" x-text="a.gap_pct != null ? (a.gap_pct > 0 ? \'+\' : \'\') + a.gap_pct.toFixed(1) + \'%\' : \'-\'"></span>\n'
    '                        </td>\n'
    '                        <td class="pl-3 py-2">\n'
    '                          <button @click="removeWatch(a.ticker, \'alert\')" class="text-xs text-slate-300 hover:text-red-500 transition-colors">\n'
    '                            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>\n'
    '                          </button>\n'
    '                        </td>\n'
    '                      </tr>\n'
    '                    </template>\n'
    '                  </tbody>\n'
    '                </table>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 매수감시 섹션 — watch.buy_watch (cur_price=0이면 gap 표시 안 함) -->\n'
    '          <template x-if="watch && watch.buy_watch && watch.buy_watch.length">\n'
    '            <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-4">\n'
    '              <div class="flex items-center gap-2 mb-3">\n'
    '                <i data-lucide="target" class="w-4 h-4 text-blue-500"></i>\n'
    '                <h3 class="text-sm font-semibold text-slate-700">매수 감시</h3>\n'
    '              </div>\n'
    '              <div class="space-y-2">\n'
    '                <template x-for="bw in watch.buy_watch" :key="bw.ticker">\n'
    '                  <div class="flex items-center justify-between py-2 border-b border-slate-50 last:border-0">\n'
    '                    <div class="flex items-center gap-2">\n'
    '                      <div>\n'
    '                        <span class="text-sm font-medium text-slate-800" x-text="bw.name"></span>\n'
    '                        <span class="text-xs text-slate-400 ml-1" x-text="bw.ticker"></span>\n'
    '                        <template x-if="bw.triggered">\n'
    '                          <span class="ml-1 text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 font-medium">도달!</span>\n'
    '                        </template>\n'
    '                        <template x-if="bw.grade">\n'
    '                          <span class="ml-1 text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-600" x-text="bw.grade"></span>\n'
    '                        </template>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center gap-4">\n'
    '                      <div class="text-right">\n'
    '                        <div class="text-xs text-slate-400">희망가</div>\n'
    '                        <div class="text-sm text-slate-700" x-text="bw.market===\'US\' ? usd(bw.buy_price) : won(bw.buy_price)"></div>\n'
    '                      </div>\n'
    '                      <div class="text-right">\n'
    '                        <div class="text-xs text-slate-400">현재가</div>\n'
    '                        <div class="text-sm text-slate-700 flex items-center justify-end gap-1"><span x-text="bw.cur_price ? (bw.market===\'US\' ? usd(bw.cur_price) : won(bw.cur_price)) : \'가격없음\'"></span><template x-if="bw.price_stale"><span class="text-xs text-amber-500">종가</span></template></div>\n'
    '                      </div>\n'
    '                      <div class="text-right w-16">\n'
    '                        <div class="text-xs text-slate-400">gap</div>\n'
    '                        <div class="text-sm" :class="bw.gap_pct != null && bw.gap_pct <= 0 ? \'text-green-600 font-semibold\' : \'text-slate-600\'"\n'
    '                             x-text="bw.gap_pct != null ? (bw.gap_pct > 0 ? \'+\' : \'\') + bw.gap_pct.toFixed(1) + \'%\' : \'—\'"></div>\n'
    '                      </div>\n'
    '                      <button @click="removeWatch(bw.ticker, \'buy_alert\')" class="text-xs text-slate-300 hover:text-red-500 transition-colors">\n'
    '                        <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>\n'
    '                      </button>\n'
    '                    </div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 감시종목 목록 -->\n'
    '          <template x-if="watch && watch.watchlist && watch.watchlist.length">\n'
    '            <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-4">\n'
    '              <div class="flex items-center gap-2 mb-3">\n'
    '                <i data-lucide="bookmark" class="w-4 h-4 text-indigo-500"></i>\n'
    '                <h3 class="text-sm font-semibold text-slate-700">감시 종목</h3>\n'
    '                <span class="text-xs text-slate-400" x-text="\'(\' + watch.watchlist.length + \'종목)\'"></span>\n'
    '              </div>\n'
    '              <div class="grid grid-cols-1 md:grid-cols-2 gap-2">\n'
    '                <template x-for="w in watch.watchlist" :key="w.ticker">\n'
    '                  <div class="flex items-center justify-between py-2 px-3 bg-slate-50 rounded-lg">\n'
    '                    <div class="flex items-center gap-2">\n'
    '                      <div>\n'
    '                        <span class="text-sm font-medium text-slate-800" x-text="w.name"></span>\n'
    '                        <span class="text-xs text-slate-400 ml-1" x-text="w.ticker"></span>\n'
    '                        <template x-if="w.grade">\n'
    '                          <span class="ml-1 text-xs px-1.5 py-0.5 rounded bg-slate-200 text-slate-600" x-text="w.grade"></span>\n'
    '                        </template>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center gap-2">\n'
    '                      <span class="text-xs text-slate-400 bg-white px-1.5 py-0.5 rounded" x-text="w.market || \'KR\'"></span>\n'
    '                      <button @click="removeWatch(w.ticker, \'watchlist\')" class="text-slate-300 hover:text-red-500 transition-colors">\n'
    '                        <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>\n'
    '                      </button>\n'
    '                    </div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 빈 상태 -->\n'
    '          <template x-if="watch && (!watch.watchlist || !watch.watchlist.length) && (!watch.stoploss_alerts || !watch.stoploss_alerts.length) && (!watch.buy_watch || !watch.buy_watch.length)">\n'
    '            <div class="text-slate-400 text-center py-20">워치·알림이 없습니다</div>\n'
    '          </template>\n'
    '\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '    </section>\n'
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P3b: 리포트 탭 패널 HTML
# JS 문자열 안 개행은 \\n, raw 문자열 사용.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_REPORT_PANEL = r"""
    <!-- 리포트 탭 -->
    <section x-show="activeTab==='report'" x-cloak>

      <!-- 로딩 스켈레톤 (최초 1회만) -->
      <template x-if="!report">
        <div class="animate-pulse">
          <!-- 세그먼트 pill x4 -->
          <div class="flex gap-1 mb-5 flex-wrap">
            <template x-for="i in [1,2,3,4]" :key="i">
              <div class="h-7 w-20 bg-slate-200 rounded-full"></div>
            </template>
          </div>
          <!-- 리포트 카드 x5 -->
          <div class="space-y-2">
            <template x-for="j in [1,2,3,4,5]" :key="j">
              <div class="bg-white rounded-xl border border-slate-100 p-4 flex items-start gap-3">
                <div class="rounded-full h-8 w-8 bg-slate-200 shrink-0"></div>
                <div class="flex-1">
                  <div class="h-3 w-28 bg-slate-200 rounded mb-2"></div>
                  <div class="h-3 w-full bg-slate-200 rounded mb-1"></div>
                  <div class="h-3 w-3/4 bg-slate-200 rounded"></div>
                </div>
                <div class="h-3 w-16 bg-slate-200 rounded"></div>
              </div>
            </template>
          </div>
        </div>
      </template>

      <template x-if="report">
        <div>

          <!-- 에러 -->
          <template x-if="report._error">
            <div class="bg-red-50 text-red-600 text-sm rounded-xl p-4 mb-4" x-text="report._error"></div>
          </template>

          <!-- 세그먼트 서브탭 -->
          <div class="flex gap-1 mb-5 flex-wrap">
            <button @click="reportSeg='kr'"
              :class="reportSeg==='kr' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              KR 한국 종목
              <span class="ml-1 text-[10px]" x-text="'(' + (report.kr_total || 0) + ')'"></span>
            </button>
            <button @click="reportSeg='us'"
              :class="reportSeg==='us' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              US 미국 종목
              <span class="ml-1 text-[10px]" x-text="'(' + (report.us_total || 0) + ')'"></span>
            </button>
            <button @click="reportSeg='industry'"
              :class="reportSeg==='industry' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              산업
              <span class="ml-1 text-[10px]" x-text="report.industry_total > 200 ? '최근200/' + report.industry_total : '(' + (report.industry_total || 0) + ')'"></span>
            </button>
            <button @click="reportSeg='macro'"
              :class="reportSeg==='macro' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              시황·전략
              <span class="ml-1 text-[10px]" x-text="report.macro_total > 200 ? '최근200/' + report.macro_total : '(' + (report.macro_total || 0) + ')'"></span>
            </button>
          </div>

          <!-- KR 종목 카드 그리드 -->
          <template x-if="reportSeg==='kr'">
            <div>
              <template x-if="!report.kr || !report.kr.length">
                <div class="text-slate-400 text-center py-16">리포트 없음</div>
              </template>
              <template x-if="report.kr && report.kr.length">
                <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                  <template x-for="c in report.kr" :key="c.ticker">
                    <div @click="openReportModal(c.ticker)"
                         class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 cursor-pointer hover:shadow-md hover:border-blue-300 transition-all">
                      <div class="text-sm font-semibold text-slate-800 truncate" x-text="c.name"></div>
                      <div class="text-xs text-slate-400 mb-2" x-text="c.ticker"></div>
                      <div class="flex items-center justify-between">
                        <span class="text-xs text-blue-600 font-bold" x-text="c.cnt + '건'"></span>
                        <span class="text-[10px] text-slate-400" x-text="c.latest"></span>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </template>

          <!-- US 종목 카드 그리드 -->
          <template x-if="reportSeg==='us'">
            <div>
              <template x-if="!report.us || !report.us.length">
                <div class="text-slate-400 text-center py-16">수집된 미국 종목 리포트 없음</div>
              </template>
              <template x-if="report.us && report.us.length">
                <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                  <template x-for="c in report.us" :key="c.ticker">
                    <div @click="openReportModal(c.ticker)"
                         class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 cursor-pointer hover:shadow-md hover:border-blue-300 transition-all">
                      <div class="text-sm font-semibold text-slate-800 truncate" x-text="c.name"></div>
                      <div class="text-xs text-slate-400 mb-2" x-text="c.ticker"></div>
                      <div class="flex items-center justify-between">
                        <span class="text-xs text-blue-600 font-bold" x-text="c.cnt + '건'"></span>
                        <span class="text-[10px] text-slate-400" x-text="c.latest"></span>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </template>

          <!-- 산업 리포트 리스트 -->
          <template x-if="reportSeg==='industry'">
            <div>
              <template x-if="!report.industry || !report.industry.length">
                <div class="text-slate-400 text-center py-16">산업 리포트 없음</div>
              </template>
              <template x-if="report.industry && report.industry.length">
                <div class="space-y-2">
                  <template x-for="(r, idx) in report.industry" :key="r.ticker + idx">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-start gap-3">
                      <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 mb-1 flex-wrap">
                          <span class="text-[10px] font-bold text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded-full border border-indigo-100" x-text="r.sector || '-'"></span>
                          <span class="text-[10px] text-slate-400" x-text="r.source"></span>
                          <span class="text-[10px] text-slate-300" x-text="r.date"></span>
                        </div>
                        <div class="text-sm text-slate-800 font-medium truncate" x-text="r.title"></div>
                      </div>
                      <template x-if="r.pdf_basename">
                        <a :href="pdfUrl(r.ticker, r.pdf_basename)" target="_blank"
                           class="flex-shrink-0 text-[10px] text-blue-600 font-semibold hover:underline flex items-center gap-0.5">
                          <i data-lucide="file-down" class="w-3 h-3"></i>PDF
                        </a>
                      </template>
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </template>

          <!-- 시황·전략·경제·채권 리스트 -->
          <template x-if="reportSeg==='macro'">
            <div>
              <template x-if="!report.macro || !report.macro.length">
                <div class="text-slate-400 text-center py-16">시황·전략 리포트 없음</div>
              </template>
              <template x-if="report.macro && report.macro.length">
                <div class="space-y-2">
                  <template x-for="(r, idx) in report.macro" :key="r.ticker + idx">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-start gap-3">
                      <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 mb-1 flex-wrap">
                          <span class="text-[10px] font-bold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-100" x-text="r.category"></span>
                          <span class="text-[10px] text-slate-400" x-text="r.source"></span>
                          <span class="text-[10px] text-slate-300" x-text="r.date"></span>
                        </div>
                        <div class="text-sm text-slate-800 font-medium truncate" x-text="r.title"></div>
                      </div>
                      <template x-if="r.pdf_basename">
                        <a :href="pdfUrl(r.ticker, r.pdf_basename)" target="_blank"
                           class="flex-shrink-0 text-[10px] text-blue-600 font-semibold hover:underline flex items-center gap-0.5">
                          <i data-lucide="file-down" class="w-3 h-3"></i>PDF
                        </a>
                      </template>
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </template>

        </div>
      </template>

      <!-- 종목 리포트 목록 모달 -->
      <template x-if="reportModal">
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="closeReportModal()">
          <div class="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 p-6 relative max-h-[80vh] flex flex-col">
            <button @click="closeReportModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-700">
              <i data-lucide="x" class="w-5 h-5"></i>
            </button>
            <div class="text-sm font-bold text-slate-700 mb-3" x-text="reportModal.ticker + ' 리포트 목록'"></div>
            <template x-if="reportModalLoading">
              <div class="animate-pulse">
                <div class="flex items-baseline gap-2 mb-3">
                  <div class="h-5 w-32 bg-slate-200 rounded"></div>
                  <div class="h-4 w-16 bg-slate-200 rounded"></div>
                </div>
                <div class="space-y-2">
                  <template x-for="n in [1,2,3,4]" :key="n">
                    <div class="border border-slate-100 rounded-lg p-3">
                      <div class="h-3 w-full bg-slate-200 rounded mb-2"></div>
                      <div class="h-3 w-3/4 bg-slate-200 rounded"></div>
                    </div>
                  </template>
                </div>
              </div>
            </template>
            <template x-if="!reportModalLoading && reportModal.error">
              <div class="text-red-500 text-sm" x-text="reportModal.error"></div>
            </template>
            <template x-if="!reportModalLoading && reportModalList">
              <div class="overflow-y-auto flex-1 space-y-2 pr-1">
                <template x-if="!reportModalList.length">
                  <div class="text-slate-400 text-sm py-4 text-center">리포트 없음</div>
                </template>
                <template x-for="(r, idx) in reportModalList" :key="idx">
                  <div class="border border-slate-100 rounded-lg p-3">
                    <div class="flex items-start justify-between gap-2">
                      <div class="flex-1 min-w-0">
                        <div class="text-sm text-slate-800 font-medium truncate" x-text="r.title"></div>
                        <div class="flex gap-2 mt-0.5 text-[10px] text-slate-400 flex-wrap">
                          <span x-text="r.date"></span>
                          <span x-text="r.source"></span>
                          <template x-if="r.analyst">
                            <span x-text="r.analyst"></span>
                          </template>
                          <template x-if="r.target_price">
                            <span class="text-blue-600 font-semibold" x-text="'TP ' + Number(r.target_price).toLocaleString('ko-KR') + '원'"></span>
                          </template>
                          <template x-if="r.opinion">
                            <span class="text-slate-600" x-text="r.opinion"></span>
                          </template>
                        </div>
                      </div>
                      <template x-if="r.pdf_basename">
                        <a :href="pdfUrl(reportModal.ticker, r.pdf_basename)" target="_blank"
                           class="flex-shrink-0 text-[10px] text-blue-600 font-semibold hover:underline flex items-center gap-0.5 mt-0.5">
                          <i data-lucide="file-down" class="w-3 h-3"></i>PDF
                        </a>
                      </template>
                    </div>
                  </div>
                </template>
              </div>
            </template>
          </div>
        </div>
      </template>

    </section>
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시그널 탭 패널 HTML
# 섹션: 임박이벤트 / 신호피드 / 발굴스캔 / DART / 컨센서스
# Alpine 서브탭(signalSeg) 으로 전환
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_SIGNAL_PANEL = r"""
    <!-- 시그널 탭 -->
    <section x-show="activeTab==='signal'" x-cloak>

      <!-- 로딩 스켈레톤 (최초 1회만) -->
      <template x-if="!signals">
        <div class="animate-pulse">
          <!-- 서브탭 pill x5 -->
          <div class="flex flex-wrap gap-2 mb-5">
            <template x-for="i in [1,2,3,4,5]" :key="i">
              <div class="h-7 w-20 bg-slate-200 rounded-full"></div>
            </template>
          </div>
          <!-- 피드 카드 x4 -->
          <div class="space-y-2">
            <template x-for="j in [1,2,3,4]" :key="j">
              <div class="bg-white rounded-xl border border-slate-100 p-4 flex items-start gap-3">
                <div class="rounded-full h-8 w-8 bg-slate-200 shrink-0"></div>
                <div class="flex-1">
                  <div class="flex items-center gap-2 mb-2">
                    <div class="h-4 w-16 bg-slate-200 rounded"></div>
                    <div class="h-4 w-20 bg-slate-200 rounded"></div>
                  </div>
                  <div class="h-3 w-full bg-slate-200 rounded mb-1"></div>
                  <div class="h-3 w-2/3 bg-slate-200 rounded"></div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </template>

      <template x-if="signals">
        <div>

          <!-- 서브탭 pill -->
          <div class="flex flex-wrap gap-2 mb-5">
            <button @click="signalSeg='feed'"
              :class="signalSeg==='feed' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              ⚡ 신호 피드
            </button>
            <button @click="signalSeg='events'"
              :class="signalSeg==='events' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              🚨 임박 이벤트
            </button>
            <button @click="signalSeg='scan'; loadAlpha(alphaSeg)"
              :class="signalSeg==='scan' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              🔍 알파스크리너
            </button>
            <button @click="signalSeg='dart'"
              :class="signalSeg==='dart' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              📑 DART
            </button>
            <button @click="signalSeg='consensus'"
              :class="signalSeg==='consensus' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              📈 컨센서스
            </button>
          </div>

          <!-- ── ⚡ 신호 피드 ── -->
          <template x-if="signalSeg==='feed'">
            <div>
              <template x-if="signals.feed && signals.feed.length">
                <div class="space-y-2">
                  <template x-for="(item, idx) in signals.feed" :key="idx">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-start gap-3">
                      <span class="text-lg mt-0.5" x-text="signalKindIcon(item.kind)"></span>
                      <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 flex-wrap mb-1">
                          <span class="text-xs px-1.5 py-0.5 rounded font-medium"
                            :class="signalKindClass(item.kind)"
                            x-text="signalKindLabel(item.kind)"></span>
                          <span class="text-sm font-semibold text-slate-800" x-text="item.name || item.ticker"></span>
                          <span class="text-xs text-slate-400" x-text="item.ticker"></span>
                        </div>
                        <div class="text-xs text-slate-600 truncate" x-text="item.detail"></div>
                        <div class="text-xs text-slate-400 mt-1" x-text="item.ts"></div>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
              <template x-if="!signals.feed || !signals.feed.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">최근 발화 신호 없음</div>
              </template>
            </div>
          </template>

          <!-- ── 🚨 임박 이벤트 ── -->
          <template x-if="signalSeg==='events'">
            <div>
              <template x-if="signals.events && signals.events.length">
                <div class="space-y-2">
                  <template x-for="(ev, idx) in signals.events" :key="idx">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-center gap-4">
                      <div class="text-center w-14 shrink-0">
                        <div class="text-lg font-bold" :class="dDayClass(ev.dday)" x-text="dDayLabel(ev.dday)"></div>
                        <div class="text-xs text-slate-400 mt-0.5" x-text="ev.date"></div>
                      </div>
                      <div class="flex-1 min-w-0">
                        <div class="text-sm font-medium text-slate-800 truncate">
                          <span x-text="ev.dday <= 3 ? '🚨 ' : ''"></span>
                          <span x-text="ev.name"></span>
                        </div>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
              <template x-if="!signals.events || !signals.events.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">임박 이벤트 없음</div>
              </template>
            </div>
          </template>

          <!-- ── 🔍 알파스크리너 ── -->
          <template x-if="signalSeg==='scan'">
            <div>
              <!-- 전략 필터 pill row (가로스크롤) -->
              <div class="flex gap-2 mb-4 overflow-x-auto pb-1">
                <button @click="loadAlpha('change')"
                  :class="alphaSeg==='change' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  변화감지
                </button>
                <button @click="loadAlpha('fscore')"
                  :class="alphaSeg==='fscore' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  F-Score 우량
                </button>
                <button @click="loadAlpha('mscore')"
                  :class="alphaSeg==='mscore' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  M-Score 안전
                </button>
                <button @click="loadAlpha('fcf')"
                  :class="alphaSeg==='fcf' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  FCF 수익률
                </button>
                <button @click="loadAlpha('high52')"
                  :class="alphaSeg==='high52' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  52주 신고가
                </button>
                <button @click="loadAlpha('low52')"
                  :class="alphaSeg==='low52' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  52주 신저가
                </button>
              </div>

              <!-- 로딩 스켈레톤 -->
              <template x-if="alphaLoading && !alphaData[alphaSeg]">
                <div class="animate-pulse space-y-2">
                  <template x-for="i in [1,2,3,4,5]" :key="i">
                    <div class="bg-white rounded-xl border border-slate-100 p-4 h-14"></div>
                  </template>
                </div>
              </template>

              <!-- 변화감지 (change) — 기존 카드 그리드 유지 -->
              <template x-if="alphaSeg==='change' && alphaData['change']">
                <div>
                  <template x-if="alphaData['change'].items && alphaData['change'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['change'].meta && alphaData['change'].meta.as_of ? alphaData['change'].meta.as_of : '') + ' · ' + (alphaData['change'].meta && alphaData['change'].meta.count != null ? alphaData['change'].meta.count + '종목' : '')">
                      </div>
                      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <template x-for="(s, idx) in alphaData['change'].items" :key="idx">
                          <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
                            <div class="flex items-start justify-between mb-2">
                              <div>
                                <div class="text-sm font-semibold text-slate-800" x-text="s.name || s.ticker"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker + (s.market ? ' · ' + s.market : '')"></div>
                              </div>
                              <div :class="s.chg_pct >= 0 ? 'text-green-600' : 'text-red-600'"
                                class="text-sm font-bold"
                                x-text="s.chg_pct != null ? (s.chg_pct >= 0 ? '+' : '') + s.chg_pct.toFixed(1) + '%' : '-'"></div>
                            </div>
                            <div class="flex flex-wrap gap-1.5 text-xs">
                              <template x-if="s.op_profit_delta != null">
                                <span class="bg-green-50 text-green-700 px-1.5 py-0.5 rounded">
                                  적자→흑자 Δ<span x-text="s.op_profit_delta.toFixed(0)"></span>억
                                </span>
                              </template>
                              <template x-if="s.fscore_delta != null">
                                <span class="bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded">
                                  F-Score +<span x-text="s.fscore_delta"></span>
                                </span>
                              </template>
                              <template x-if="s.insider_reprors != null">
                                <span class="bg-purple-50 text-purple-700 px-1.5 py-0.5 rounded">
                                  내부자 <span x-text="s.insider_reprors"></span>명 순매수
                                </span>
                              </template>
                            </div>
                          </div>
                        </template>
                      </div>
                    </div>
                  </template>
                  <template x-if="alphaData['change'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400" x-text="alphaData['change'].error"></div>
                  </template>
                  <template x-if="!alphaData['change'].error && (!alphaData['change'].items || !alphaData['change'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      발굴된 종목 없음
                      <button @click="loadAlpha('change')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

              <!-- F-Score 우량 (fscore) — 테이블 -->
              <template x-if="alphaSeg==='fscore' && alphaData['fscore']">
                <div>
                  <template x-if="alphaData['fscore'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400" x-text="alphaData['fscore'].error"></div>
                  </template>
                  <template x-if="!alphaData['fscore'].error && alphaData['fscore'].items && alphaData['fscore'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['fscore'].meta && alphaData['fscore'].meta.as_of ? alphaData['fscore'].meta.as_of + ' · ' : '') + (alphaData['fscore'].meta && alphaData['fscore'].meta.count != null ? alphaData['fscore'].meta.count + '종목' : '')">
                      </div>
                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                        <!-- 데스크탑 테이블 -->
                        <table class="w-full text-sm hidden sm:table">
                          <thead>
                            <tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">
                              <th class="text-left px-4 py-2.5 font-medium">#</th>
                              <th class="text-left px-4 py-2.5 font-medium">종목</th>
                              <th class="text-center px-4 py-2.5 font-medium">F-Score</th>
                              <th class="text-right px-4 py-2.5 font-medium hidden md:table-cell">시총(억)</th>
                            </tr>
                          </thead>
                          <tbody>
                            <template x-for="(s, idx) in alphaData['fscore'].items" :key="idx">
                              <tr class="border-b border-slate-50 hover:bg-slate-50">
                                <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="s.rank"></td>
                                <td class="px-4 py-2.5">
                                  <div class="font-medium text-slate-800" x-text="s.name"></div>
                                  <div class="text-xs text-slate-400" x-text="s.ticker + ' · ' + (s.market || '')"></div>
                                </td>
                                <td class="px-4 py-2.5 text-center">
                                  <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                    :class="s.fscore >= 8 ? 'bg-emerald-100 text-emerald-700' : 'bg-green-100 text-green-700'"
                                    x-text="s.fscore"></span>
                                </td>
                                <td class="px-4 py-2.5 text-right text-slate-600 text-xs hidden md:table-cell"
                                  x-text="s.market_cap != null ? s.market_cap.toLocaleString('ko-KR') : '-'"></td>
                              </tr>
                            </template>
                          </tbody>
                        </table>
                        <!-- 모바일 카드 -->
                        <div class="sm:hidden divide-y divide-slate-100">
                          <template x-for="(s, idx) in alphaData['fscore'].items" :key="idx">
                            <div class="p-3 flex items-center gap-3">
                              <span class="text-slate-400 text-xs w-5 text-right" x-text="s.rank"></span>
                              <div class="flex-1 min-w-0">
                                <div class="text-sm font-medium text-slate-800" x-text="s.name"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker"></div>
                              </div>
                              <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                :class="s.fscore >= 8 ? 'bg-emerald-100 text-emerald-700' : 'bg-green-100 text-green-700'"
                                x-text="'F' + s.fscore"></span>
                            </div>
                          </template>
                        </div>
                      </div>
                    </div>
                  </template>
                  <template x-if="!alphaData['fscore'].error && (!alphaData['fscore'].items || !alphaData['fscore'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      F-Score 데이터 없음
                      <button @click="loadAlpha('fscore')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

              <!-- M-Score 안전 (mscore) — 테이블 -->
              <template x-if="alphaSeg==='mscore' && alphaData['mscore']">
                <div>
                  <template x-if="alphaData['mscore'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-amber-600"
                      x-text="alphaData['mscore'].error || 'M-Score 데이터 수집 대기 중'"></div>
                  </template>
                  <template x-if="!alphaData['mscore'].error && alphaData['mscore'].items && alphaData['mscore'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['mscore'].meta && alphaData['mscore'].meta.as_of ? alphaData['mscore'].meta.as_of + ' · ' : '') + (alphaData['mscore'].meta && alphaData['mscore'].meta.count != null ? alphaData['mscore'].meta.count + '종목' : '')">
                      </div>
                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                        <table class="w-full text-sm hidden sm:table">
                          <thead>
                            <tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">
                              <th class="text-left px-4 py-2.5 font-medium">#</th>
                              <th class="text-left px-4 py-2.5 font-medium">종목</th>
                              <th class="text-center px-4 py-2.5 font-medium">M-Score</th>
                              <th class="text-right px-4 py-2.5 font-medium hidden md:table-cell">시총(억)</th>
                            </tr>
                          </thead>
                          <tbody>
                            <template x-for="(s, idx) in alphaData['mscore'].items" :key="idx">
                              <tr class="border-b border-slate-50 hover:bg-slate-50">
                                <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="s.rank"></td>
                                <td class="px-4 py-2.5">
                                  <div class="font-medium text-slate-800" x-text="s.name"></div>
                                  <div class="text-xs text-slate-400" x-text="s.ticker + ' · ' + (s.market || '')"></div>
                                </td>
                                <td class="px-4 py-2.5 text-center">
                                  <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                    :class="s.mscore <= -2.22 ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'"
                                    x-text="s.mscore != null ? s.mscore.toFixed(2) : '-'"></span>
                                  <span class="ml-1 text-xs"
                                    :class="s.mscore <= -2.22 ? 'text-green-600' : 'text-amber-600'"
                                    x-text="s.mscore <= -2.22 ? '안전' : '주의'"></span>
                                </td>
                                <td class="px-4 py-2.5 text-right text-slate-600 text-xs hidden md:table-cell"
                                  x-text="s.market_cap != null ? s.market_cap.toLocaleString('ko-KR') : '-'"></td>
                              </tr>
                            </template>
                          </tbody>
                        </table>
                        <div class="sm:hidden divide-y divide-slate-100">
                          <template x-for="(s, idx) in alphaData['mscore'].items" :key="idx">
                            <div class="p-3 flex items-center gap-3">
                              <span class="text-slate-400 text-xs w-5 text-right" x-text="s.rank"></span>
                              <div class="flex-1 min-w-0">
                                <div class="text-sm font-medium text-slate-800" x-text="s.name"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker"></div>
                              </div>
                              <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                :class="s.mscore <= -2.22 ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'"
                                x-text="s.mscore != null ? s.mscore.toFixed(2) : '-'"></span>
                            </div>
                          </template>
                        </div>
                      </div>
                    </div>
                  </template>
                  <template x-if="!alphaData['mscore'].error && (!alphaData['mscore'].items || !alphaData['mscore'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      M-Score 데이터 없음
                      <button @click="loadAlpha('mscore')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

              <!-- FCF 수익률 (fcf) — 테이블 -->
              <template x-if="alphaSeg==='fcf' && alphaData['fcf']">
                <div>
                  <template x-if="alphaData['fcf'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400" x-text="alphaData['fcf'].error"></div>
                  </template>
                  <template x-if="!alphaData['fcf'].error && alphaData['fcf'].items && alphaData['fcf'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['fcf'].meta && alphaData['fcf'].meta.as_of ? alphaData['fcf'].meta.as_of + ' · ' : '') + (alphaData['fcf'].meta && alphaData['fcf'].meta.count != null ? alphaData['fcf'].meta.count + '종목' : '')">
                      </div>
                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                        <table class="w-full text-sm hidden sm:table">
                          <thead>
                            <tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">
                              <th class="text-left px-4 py-2.5 font-medium">#</th>
                              <th class="text-left px-4 py-2.5 font-medium">종목</th>
                              <th class="text-right px-4 py-2.5 font-medium">FCF/EV(%)</th>
                              <th class="text-right px-4 py-2.5 font-medium hidden md:table-cell">시총(억)</th>
                            </tr>
                          </thead>
                          <tbody>
                            <template x-for="(s, idx) in alphaData['fcf'].items" :key="idx">
                              <tr class="border-b border-slate-50 hover:bg-slate-50">
                                <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="s.rank"></td>
                                <td class="px-4 py-2.5">
                                  <div class="font-medium text-slate-800" x-text="s.name"></div>
                                  <div class="text-xs text-slate-400" x-text="s.ticker + ' · ' + (s.market || '')"></div>
                                </td>
                                <td class="px-4 py-2.5 text-right">
                                  <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                    :class="s.fcf_yield >= 10 ? 'bg-emerald-100 text-emerald-700' : s.fcf_yield >= 5 ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-600'"
                                    x-text="s.fcf_yield != null ? s.fcf_yield.toFixed(1) + '%' : '-'"></span>
                                </td>
                                <td class="px-4 py-2.5 text-right text-slate-600 text-xs hidden md:table-cell"
                                  x-text="s.market_cap != null ? s.market_cap.toLocaleString('ko-KR') : '-'"></td>
                              </tr>
                            </template>
                          </tbody>
                        </table>
                        <div class="sm:hidden divide-y divide-slate-100">
                          <template x-for="(s, idx) in alphaData['fcf'].items" :key="idx">
                            <div class="p-3 flex items-center gap-3">
                              <span class="text-slate-400 text-xs w-5 text-right" x-text="s.rank"></span>
                              <div class="flex-1 min-w-0">
                                <div class="text-sm font-medium text-slate-800" x-text="s.name"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker"></div>
                              </div>
                              <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                :class="s.fcf_yield >= 10 ? 'bg-emerald-100 text-emerald-700' : s.fcf_yield >= 5 ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-600'"
                                x-text="s.fcf_yield != null ? s.fcf_yield.toFixed(1) + '%' : '-'"></span>
                            </div>
                          </template>
                        </div>
                      </div>
                    </div>
                  </template>
                  <template x-if="!alphaData['fcf'].error && (!alphaData['fcf'].items || !alphaData['fcf'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      FCF 데이터 없음
                      <button @click="loadAlpha('fcf')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

              <!-- 52주 신고가 근접 (high52) — 카드그리드 -->
              <template x-if="alphaSeg==='high52' && alphaData['high52']">
                <div>
                  <template x-if="alphaData['high52'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400" x-text="alphaData['high52'].error"></div>
                  </template>
                  <template x-if="!alphaData['high52'].error && alphaData['high52'].items && alphaData['high52'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['high52'].meta && alphaData['high52'].meta.as_of ? alphaData['high52'].meta.as_of + ' · ' : '') + (alphaData['high52'].meta && alphaData['high52'].meta.count != null ? alphaData['high52'].meta.count + '종목' : '')">
                      </div>
                      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <template x-for="(s, idx) in alphaData['high52'].items" :key="idx">
                          <div class="bg-white rounded-xl border border-emerald-200 shadow-sm p-4">
                            <div class="flex items-start justify-between mb-2">
                              <div>
                                <div class="text-sm font-semibold text-slate-800" x-text="s.name || s.ticker"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker"></div>
                              </div>
                              <div :class="s.chg_pct >= 0 ? 'text-green-600' : 'text-red-600'"
                                class="text-sm font-bold"
                                x-text="s.chg_pct != null ? (s.chg_pct >= 0 ? '+' : '') + s.chg_pct.toFixed(1) + '%' : '-'"></div>
                            </div>
                            <div class="flex gap-2 text-xs flex-wrap">
                              <span class="bg-emerald-50 text-emerald-700 px-1.5 py-0.5 rounded"
                                x-text="'52주고 ' + (s.new_high != null ? s.new_high.toLocaleString('ko-KR') : '-')"></span>
                              <span class="text-slate-500"
                                x-text="'현재 ' + (s.price != null ? s.price.toLocaleString('ko-KR') : '-')"></span>
                              <span class="font-semibold text-emerald-600"
                                x-text="'괴리 ' + (s.high_gap_pct != null ? s.high_gap_pct.toFixed(1) + '%' : '-')"></span>
                            </div>
                          </div>
                        </template>
                      </div>
                    </div>
                  </template>
                  <template x-if="!alphaData['high52'].error && (!alphaData['high52'].items || !alphaData['high52'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      신고가 근접 종목 없음
                      <button @click="loadAlpha('high52')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

              <!-- 52주 신저가 근접 (low52) — 카드그리드 -->
              <template x-if="alphaSeg==='low52' && alphaData['low52']">
                <div>
                  <template x-if="alphaData['low52'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400" x-text="alphaData['low52'].error"></div>
                  </template>
                  <template x-if="!alphaData['low52'].error && alphaData['low52'].items && alphaData['low52'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['low52'].meta && alphaData['low52'].meta.as_of ? alphaData['low52'].meta.as_of + ' · ' : '') + (alphaData['low52'].meta && alphaData['low52'].meta.count != null ? alphaData['low52'].meta.count + '종목' : '')">
                      </div>
                      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <template x-for="(s, idx) in alphaData['low52'].items" :key="idx">
                          <div class="bg-white rounded-xl border border-red-200 shadow-sm p-4">
                            <div class="flex items-start justify-between mb-2">
                              <div>
                                <div class="text-sm font-semibold text-slate-800" x-text="s.name || s.ticker"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker"></div>
                              </div>
                              <div :class="s.chg_pct >= 0 ? 'text-green-600' : 'text-red-600'"
                                class="text-sm font-bold"
                                x-text="s.chg_pct != null ? (s.chg_pct >= 0 ? '+' : '') + s.chg_pct.toFixed(1) + '%' : '-'"></div>
                            </div>
                            <div class="flex gap-2 text-xs flex-wrap">
                              <span class="bg-red-50 text-red-700 px-1.5 py-0.5 rounded"
                                x-text="'52주저 ' + (s.new_low != null ? s.new_low.toLocaleString('ko-KR') : '-')"></span>
                              <span class="text-slate-500"
                                x-text="'현재 ' + (s.price != null ? s.price.toLocaleString('ko-KR') : '-')"></span>
                              <span class="font-semibold text-red-600"
                                x-text="'괴리 ' + (s.low_gap_pct != null ? s.low_gap_pct.toFixed(1) + '%' : '-')"></span>
                            </div>
                          </div>
                        </template>
                      </div>
                    </div>
                  </template>
                  <template x-if="!alphaData['low52'].error && (!alphaData['low52'].items || !alphaData['low52'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      신저가 근접 종목 없음
                      <button @click="loadAlpha('low52')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

            </div>
          </template>

          <!-- ── 📑 DART ── -->
          <template x-if="signalSeg==='dart'">
            <div>
              <template x-if="signals.dart && signals.dart.length">
                <div class="space-y-2">
                  <template x-for="(d, idx) in signals.dart" :key="idx">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-start gap-3">
                      <div class="text-slate-400 text-xs w-20 shrink-0 mt-0.5" x-text="d.date"></div>
                      <div class="flex-1 min-w-0">
                        <div class="text-xs font-semibold text-slate-700 mb-0.5" x-text="d.corp"></div>
                        <div class="text-xs text-slate-600 leading-snug" x-text="d.title"></div>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
              <template x-if="!signals.dart || !signals.dart.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">최근 DART 공시 없음</div>
              </template>
            </div>
          </template>

          <!-- ── 📈 컨센서스 ── -->
          <template x-if="signalSeg==='consensus'">
            <div>
              <template x-if="signals.consensus && signals.consensus.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                  <table class="w-full text-sm">
                    <thead>
                      <tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">
                        <th class="text-left px-4 py-2.5 font-medium">종목</th>
                        <th class="text-right px-4 py-2.5 font-medium">현재 TP</th>
                        <th class="text-right px-4 py-2.5 font-medium">이전 TP</th>
                        <th class="text-right px-4 py-2.5 font-medium">변동</th>
                      </tr>
                    </thead>
                    <tbody>
                      <template x-for="(c, idx) in signals.consensus" :key="idx">
                        <tr class="border-b border-slate-50 hover:bg-slate-50">
                          <td class="px-4 py-2.5">
                            <div class="font-medium text-slate-800" x-text="c.name"></div>
                            <div class="text-xs text-slate-400" x-text="c.ticker"></div>
                          </td>
                          <td class="px-4 py-2.5 text-right text-slate-700" x-text="c.avg ? c.avg.toLocaleString('ko-KR') + '원' : '-'"></td>
                          <td class="px-4 py-2.5 text-right text-slate-500" x-text="c.prev_avg ? c.prev_avg.toLocaleString('ko-KR') + '원' : '-'"></td>
                          <td class="px-4 py-2.5 text-right font-semibold"
                            :class="c.chg_pct >= 0 ? 'text-green-600' : 'text-red-600'"
                            x-text="c.chg_pct != null ? (c.chg_pct >= 0 ? '+' : '') + c.chg_pct.toFixed(1) + '%' : '-'">
                          </td>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
              </template>
              <template x-if="!signals.consensus || !signals.consensus.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">컨센서스 변동 없음</div>
              </template>
            </div>
          </template>

        </div>
      </template>

    </section>
"""

# P3b: 기록 탭 패널 HTML
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_RECORD_PANEL = r"""
    <!-- 기록 탭 -->
    <section x-show="activeTab==='record'" x-cloak>

      <!-- 로딩 스켈레톤 (최초 1회만) -->
      <template x-if="!record">
        <div class="animate-pulse">
          <!-- 섹션 서브탭 pill x3 -->
          <div class="flex gap-1 mb-5">
            <template x-for="i in [1,2,3]" :key="i">
              <div class="h-7 w-20 bg-slate-200 rounded-full"></div>
            </template>
          </div>
          <!-- 기록 카드 x3 -->
          <div class="space-y-3">
            <template x-for="j in [1,2,3]" :key="j">
              <div class="bg-white rounded-xl border border-slate-100 p-4">
                <div class="flex items-start justify-between mb-3">
                  <div class="h-4 w-24 bg-slate-200 rounded"></div>
                  <div class="h-5 w-14 bg-slate-200 rounded-full"></div>
                </div>
                <div class="h-3 w-full bg-slate-200 rounded mb-1"></div>
                <div class="h-3 w-3/4 bg-slate-200 rounded"></div>
              </div>
            </template>
          </div>
        </div>
      </template>

      <template x-if="record">
        <div>

          <!-- 토스트 -->
          <template x-if="recordToast">
            <div class="fixed top-20 right-4 z-50 bg-slate-800 text-white text-sm px-4 py-2 rounded-lg shadow-lg" x-text="recordToast"></div>
          </template>

          <!-- 섹션 서브탭 -->
          <div class="flex gap-1 mb-5">
            <button @click="recordSection='decisions'"
              :class="recordSection==='decisions' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              투자판단
            </button>
            <button @click="recordSection='trades'"
              :class="recordSection==='trades' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              매매 성과
            </button>
            <button @click="recordSection='todo'"
              :class="recordSection==='todo' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              투자 TODO
            </button>
          </div>

          <!-- 투자판단 섹션 -->
          <template x-if="recordSection==='decisions'">
            <div>
              <!-- 새 투자판단 폼 토글 -->
              <div class="flex items-center justify-between mb-4">
                <h2 class="text-base font-semibold text-slate-700">투자판단 기록</h2>
                <button @click="decisionForm.show = !decisionForm.show"
                  :class="decisionForm.show ? 'bg-slate-600' : 'bg-blue-600'"
                  class="text-xs text-white px-3 py-1.5 rounded-lg font-medium">
                  <span x-text="decisionForm.show ? '닫기' : '+ 새 판단'"></span>
                </button>
              </div>

              <!-- 폼 -->
              <template x-if="decisionForm.show">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-5">
                  <div class="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
                    <div>
                      <label class="text-xs text-slate-500 block mb-1">날짜</label>
                      <input type="date" x-model="decisionForm.date"
                        class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
                    </div>
                    <div>
                      <label class="text-xs text-slate-500 block mb-1">레짐 *</label>
                      <select x-model="decisionForm.regime"
                        class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
                        <option value="">선택</option>
                        <option value="공격">공격</option>
                        <option value="경계">경계</option>
                        <option value="위기">위기</option>
                      </select>
                    </div>
                    <div class="col-span-2 md:col-span-1">
                      <label class="text-xs text-slate-500 block mb-1">메모</label>
                      <input x-model="decisionForm.memo" placeholder="간단 메모 (선택)"
                        class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
                    </div>
                  </div>
                  <button @click="submitDecision()"
                    class="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors">저장</button>
                </div>
              </template>

              <!-- 판단 카드 목록 -->
              <template x-if="record.decisions && record.decisions.length">
                <div class="space-y-3">
                  <template x-for="d in record.decisions.slice(0, decisionsLimit)" :key="d.date">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
                      <div class="flex items-start justify-between gap-2 mb-2">
                        <div>
                          <span class="text-sm font-bold text-slate-800" x-text="d.date"></span>
                          <template x-if="d.saved_at">
                            <span class="text-[10px] text-slate-400 ml-2" x-text="'저장 ' + d.saved_at"></span>
                          </template>
                        </div>
                        <span :class="regimeColor(d.regime)"
                              class="text-xs font-bold px-2 py-0.5 rounded-full"
                              x-text="d.regime || '-'"></span>
                      </div>
                      <template x-if="d.notes">
                        <p class="text-sm text-slate-600" x-text="d.notes"></p>
                      </template>
                      <template x-if="d.actions && d.actions.length">
                        <ul class="mt-2 space-y-0.5">
                          <template x-for="(a, ai) in d.actions" :key="ai">
                            <li class="text-xs text-slate-500 flex gap-1">
                              <span class="text-slate-300">&#183;</span>
                              <span x-text="typeof a === 'string' ? a : JSON.stringify(a)"></span>
                            </li>
                          </template>
                        </ul>
                      </template>
                    </div>
                  </template>
                  <!-- 더보기 버튼 — 전체 건수보다 limit이 작을 때만 표시 -->
                  <template x-if="decisionsLimit < record.decisions.length">
                    <div class="text-center pt-1">
                      <button @click="decisionsLimit = record.decisions.length"
                        class="text-sm text-blue-600 hover:text-blue-700 px-4 py-2 rounded-lg border border-blue-200 hover:bg-blue-50 transition-colors"
                        x-text="'더보기 (' + (record.decisions.length - decisionsLimit) + '건 더)'">
                      </button>
                    </div>
                  </template>
                </div>
              </template>

              <template x-if="!record.decisions || !record.decisions.length">
                <div class="text-slate-400 text-center py-16">기록된 투자판단 없음</div>
              </template>
            </div>
          </template>

          <!-- 매매 성과 섹션 -->
          <template x-if="recordSection==='trades'">
            <div>
              <h2 class="text-base font-semibold text-slate-700 mb-4">매매 성과</h2>
              <template x-if="record.trades && record.trades.total_trades != null">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-5">
                  <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                      <div class="text-xs text-slate-400 mb-0.5">총 매매건수</div>
                      <div class="text-xl font-bold text-slate-800" x-text="record.trades.total_trades || 0"></div>
                    </div>
                    <div>
                      <div class="text-xs text-slate-400 mb-0.5">승률</div>
                      <div class="text-xl font-bold text-slate-800"
                           x-text="record.trades.win_rate_pct != null ? record.trades.win_rate_pct.toFixed(1) + '%' : '-'"></div>
                    </div>
                    <div>
                      <div class="text-xs text-slate-400 mb-0.5">평균 손익/건</div>
                      <div :class="pnlClass(record.trades.avg_pnl_per_trade)" class="text-xl font-bold"
                           x-text="record.trades.avg_pnl_per_trade != null ? (record.trades.avg_pnl_per_trade >= 0 ? '+' : '') + Number(record.trades.avg_pnl_per_trade).toLocaleString('ko-KR') : '-'"></div>
                    </div>
                    <div>
                      <div class="text-xs text-slate-400 mb-0.5">평균 보유</div>
                      <div class="text-xl font-bold text-slate-800"
                           x-text="record.trades.avg_holding_days != null ? Math.abs(record.trades.avg_holding_days).toFixed(0) + '일' : '-'"></div>
                    </div>
                  </div>
                </div>
              </template>
              <template x-if="record.trades && record.trades.trades && record.trades.trades.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                  <div class="px-4 py-3 border-b border-slate-100">
                    <h3 class="text-sm font-semibold text-slate-700">최근 매매 기록</h3>
                  </div>
                  <div class="overflow-x-auto">
                    <table class="w-full text-sm">
                      <thead>
                        <tr class="text-xs text-slate-400 border-b border-slate-100">
                          <th class="text-left py-2 px-4 font-medium">날짜</th>
                          <th class="text-left py-2 px-2 font-medium">종목</th>
                          <th class="text-center py-2 px-2 font-medium">구분</th>
                          <th class="text-right py-2 px-4 font-medium">이유</th>
                        </tr>
                      </thead>
                      <tbody>
                        <template x-for="(t, ti) in record.trades.trades.slice(0,20)" :key="ti">
                          <tr class="border-b border-slate-50 hover:bg-slate-50">
                            <td class="py-2 px-4 text-xs text-slate-500" x-text="t.date || '-'"></td>
                            <td class="py-2 px-2">
                              <span class="font-medium text-slate-800" x-text="t.name || t.ticker || '-'"></span>
                              <span class="text-xs text-slate-400 ml-1" x-text="t.ticker || ''"></span>
                            </td>
                            <td class="py-2 px-2 text-center">
                              <span :class="t.side === 'buy' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600'"
                                    class="text-[10px] font-bold px-1.5 py-0.5 rounded"
                                    x-text="t.side === 'buy' ? '매수' : '매도'"></span>
                            </td>
                            <td class="py-2 px-4 text-right text-xs text-slate-500 truncate max-w-[120px]"
                                x-text="t.reason || '-'"></td>
                          </tr>
                        </template>
                      </tbody>
                    </table>
                  </div>
                </div>
              </template>
              <template x-if="!record.trades || record.trades.total_trades == null">
                <div class="text-slate-400 text-center py-16">매매 기록 없음</div>
              </template>
            </div>
          </template>

          <!-- 투자 TODO 섹션 -->
          <template x-if="recordSection==='todo'">
            <div>
              <h2 class="text-base font-semibold text-slate-700 mb-4">투자 TODO</h2>
              <template x-if="record.todo">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
                  <pre class="text-sm text-slate-700 whitespace-pre-wrap font-sans leading-relaxed" x-text="record.todo"></pre>
                </div>
              </template>
              <template x-if="!record.todo">
                <div class="text-slate-400 text-center py-16">TODO 파일 없음</div>
              </template>
            </div>
          </template>

        </div>
      </template>

    </section>
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# US 애널리스트 탭 패널 HTML
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_US_PANEL = r"""
    <!-- US 애널리스트 탭 -->
    <section x-show="activeTab==='us'" x-cloak>

      <!-- 서브탭 pill 바 -->
      <div class="flex gap-2 mb-5 overflow-x-auto">
        <button @click="setUsSeg('candidates')"
          :class="usSeg==='candidates' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
          class="px-4 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors">
          매수후보
        </button>
        <button @click="setUsSeg('scan')"
          :class="usSeg==='scan' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
          class="px-4 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors">
          레이팅변화
        </button>
        <button @click="setUsSeg('analysts')"
          :class="usSeg==='analysts' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
          class="px-4 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors">
          톱애널
        </button>
      </div>

      <!-- ── 매수후보 서브탭 ── -->
      <template x-if="usSeg==='candidates'">
        <div>
          <!-- 스켈레톤 -->
          <template x-if="!usCandidates">
            <div class="animate-pulse">
              <div class="flex gap-3 mb-4">
                <div class="h-8 w-32 bg-slate-200 rounded-lg"></div>
                <div class="h-8 w-24 bg-slate-200 rounded-lg"></div>
              </div>
              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <template x-for="i in [1,2,3,4,5,6]" :key="i">
                  <div class="bg-white rounded-xl border border-slate-100 p-4">
                    <div class="h-4 w-16 bg-slate-200 rounded mb-2"></div>
                    <div class="h-6 w-24 bg-slate-200 rounded mb-3"></div>
                    <div class="h-3 w-full bg-slate-200 rounded mb-1"></div>
                    <div class="h-3 w-2/3 bg-slate-200 rounded"></div>
                  </div>
                </template>
              </div>
            </div>
          </template>

          <!-- 데이터 있음 -->
          <template x-if="usCandidates">
            <div>
              <!-- 필터 바 -->
              <div class="flex flex-wrap items-center gap-3 mb-4">
                <div class="flex items-center gap-2">
                  <label class="text-sm text-slate-600 whitespace-nowrap">최소 업사이드</label>
                  <select x-model.number="usCandidatesMinUpside"
                    class="text-sm border border-slate-200 rounded px-2 py-1">
                    <option value="20">20%+</option>
                    <option value="30">30%+</option>
                    <option value="50">50%+</option>
                  </select>
                </div>
                <label class="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer">
                  <input type="checkbox" x-model="usCandidatesTierSOnly" class="rounded">
                  Tier S 포함
                </label>
                <span class="text-xs text-slate-400" x-text="'총 ' + filteredCandidates().length + '건'"></span>
              </div>

              <!-- 카드 그리드 -->
              <template x-if="filteredCandidates().length === 0">
                <div class="text-center py-12 text-slate-400">
                  <i data-lucide="search-x" class="w-10 h-10 mx-auto mb-2 opacity-40"></i>
                  <p class="text-sm">필터 조건을 만족하는 매수후보가 없습니다.</p>
                </div>
              </template>

              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <template x-for="c in filteredCandidates()" :key="c.ticker">
                  <div @click="openUsModal(c.ticker)"
                    class="bg-white rounded-xl border border-slate-100 p-4 cursor-pointer hover:shadow-md transition-shadow">
                    <div class="flex items-center justify-between mb-2">
                      <span class="font-bold text-slate-800" x-text="c.ticker"></span>
                      <span class="text-xs px-2 py-0.5 rounded-full font-medium"
                        :class="hmColorUs(c.upside_pct)"
                        x-text="'+' + c.upside_pct.toFixed(1) + '%'"></span>
                    </div>
                    <div class="text-sm text-slate-600 mb-1">
                      <span x-text="usd(c.price)"></span>
                      <span class="text-slate-400 mx-1">→</span>
                      <span class="font-semibold text-slate-800" x-text="usd(c.avg_target)"></span>
                    </div>
                    <div class="flex items-center gap-2 mb-3">
                      <template x-if="c.tier_s_count > 0">
                        <span class="text-xs px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium"
                          x-text="'S×' + c.tier_s_count"></span>
                      </template>
                      <template x-if="c.tier_a_count > 0">
                        <span class="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 font-medium"
                          x-text="'A×' + c.tier_a_count"></span>
                      </template>
                      <span class="text-xs text-slate-400" x-text="'총 ' + c.total_advisors + '명'"></span>
                    </div>
                    <div class="text-xs text-slate-500 flex items-center gap-1">
                      <i data-lucide="clock" class="w-3 h-3 opacity-60"></i>
                      <span x-text="c.latest_call_days_ago + '일 전'"></span>
                      <template x-if="c.tier_s_analysts && c.tier_s_analysts.length > 0">
                        <span class="ml-1 text-amber-600 truncate" x-text="c.tier_s_analysts[0].name"></span>
                      </template>
                      <template x-if="(!c.tier_s_analysts || c.tier_s_analysts.length === 0) && c.tier_a_analysts && c.tier_a_analysts.length > 0">
                        <span class="ml-1 text-blue-600 truncate" x-text="c.tier_a_analysts[0].name"></span>
                      </template>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- ── 레이팅변화 서브탭 ── -->
      <template x-if="usSeg==='scan'">
        <div>
          <!-- 스켈레톤 -->
          <template x-if="!usScan">
            <div class="animate-pulse space-y-3">
              <template x-for="i in [1,2,3,4,5]" :key="i">
                <div class="bg-white rounded-xl border border-slate-100 p-4">
                  <div class="flex items-center gap-3">
                    <div class="h-4 w-12 bg-slate-200 rounded"></div>
                    <div class="h-4 w-8 bg-slate-200 rounded"></div>
                    <div class="h-4 w-8 bg-slate-200 rounded"></div>
                  </div>
                </div>
              </template>
            </div>
          </template>

          <template x-if="usScan">
            <div>
              <template x-if="!usScan.data || usScan.data.length === 0">
                <div class="text-center py-12 text-slate-400">
                  <i data-lucide="inbox" class="w-10 h-10 mx-auto mb-2 opacity-40"></i>
                  <p class="text-sm">최근 레이팅 변화 데이터가 없습니다.</p>
                </div>
              </template>
              <div class="space-y-3">
                <template x-for="item in (usScan.data || [])" :key="item.ticker">
                  <div class="bg-white rounded-xl border border-slate-100 p-4">
                    <div class="flex items-center gap-2 mb-2">
                      <span class="font-bold text-slate-800 text-sm" x-text="item.ticker"></span>
                      <template x-if="item.upgrades > 0">
                        <span class="text-xs px-1.5 py-0.5 rounded bg-green-100 text-green-700 font-medium"
                          x-text="'↑' + item.upgrades"></span>
                      </template>
                      <template x-if="item.downgrades > 0">
                        <span class="text-xs px-1.5 py-0.5 rounded bg-red-100 text-red-700 font-medium"
                          x-text="'↓' + item.downgrades"></span>
                      </template>
                      <span class="text-xs text-slate-400" x-text="item.events + '건'"></span>
                      <button @click="openUsModal(item.ticker)"
                        class="ml-auto text-xs text-blue-500 hover:underline">상세 ›</button>
                    </div>
                    <div class="space-y-1.5">
                      <template x-for="(ev, idx) in (item.latest || []).slice(0, 3)" :key="idx">
                        <div class="flex items-center gap-2 text-xs text-slate-600">
                          <span class="text-slate-400 w-12 shrink-0"
                            x-text="ev.date ? ev.date.slice(5) : ''"></span>
                          <span class="text-slate-600 truncate max-w-24 shrink-0" x-text="ev.firm"></span>
                          <span class="px-1.5 py-0.5 rounded text-xs font-medium shrink-0"
                            :class="usActionBadge(ev.action)"
                            x-text="ev.action"></span>
                          <span class="font-medium shrink-0" x-text="ev.rating_new"></span>
                          <template x-if="ev.pt_now">
                            <span class="text-slate-400 shrink-0" x-text="'TP $' + ev.pt_now"></span>
                          </template>
                        </div>
                      </template>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- ── 톱애널 서브탭 ── -->
      <template x-if="usSeg==='analysts'">
        <div>
          <!-- 스켈레톤 -->
          <template x-if="usAnalystsLoading || !usAnalysts">
            <div class="animate-pulse">
              <div class="hidden md:block bg-white rounded-xl border border-slate-100 overflow-hidden">
                <div class="h-10 bg-slate-100 w-full"></div>
                <template x-for="i in [1,2,3,4,5,6,7,8,9,10]" :key="i">
                  <div class="flex gap-4 p-3 border-b border-slate-50">
                    <div class="h-3 w-6 bg-slate-200 rounded"></div>
                    <div class="h-3 w-24 bg-slate-200 rounded"></div>
                    <div class="h-3 w-20 bg-slate-200 rounded"></div>
                    <div class="h-3 w-12 bg-slate-200 rounded"></div>
                  </div>
                </template>
              </div>
            </div>
          </template>

          <template x-if="!usAnalystsLoading && usAnalysts">
            <div>
              <template x-if="!usAnalysts.analysts || usAnalysts.analysts.length === 0">
                <div class="text-center py-12 text-slate-400">
                  <i data-lucide="user-x" class="w-10 h-10 mx-auto mb-2 opacity-40"></i>
                  <p class="text-sm">톱 애널리스트 데이터가 없습니다.</p>
                </div>
              </template>

              <!-- 데스크탑 테이블 -->
              <div class="hidden md:block bg-white rounded-xl border border-slate-100 overflow-hidden">
                <table class="w-full text-sm">
                  <thead>
                    <tr class="bg-slate-50 border-b border-slate-100">
                      <th class="text-left px-4 py-3 text-xs text-slate-500 font-medium w-10">#</th>
                      <th class="text-left px-4 py-3 text-xs text-slate-500 font-medium">이름</th>
                      <th class="text-left px-4 py-3 text-xs text-slate-500 font-medium">증권사</th>
                      <th class="text-right px-4 py-3 text-xs text-slate-500 font-medium">별점</th>
                      <th class="text-right px-4 py-3 text-xs text-slate-500 font-medium">적중률</th>
                      <th class="text-right px-4 py-3 text-xs text-slate-500 font-medium">콜수</th>
                    </tr>
                  </thead>
                  <tbody>
                    <template x-for="(a, idx) in (usAnalysts.analysts || [])" :key="a.slug || a.analyst">
                      <tr class="border-b border-slate-50 hover:bg-slate-50 transition-colors">
                        <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="idx + 1"></td>
                        <td class="px-4 py-2.5 font-medium text-slate-800" x-text="a.analyst"></td>
                        <td class="px-4 py-2.5 text-slate-600 text-xs" x-text="a.firm"></td>
                        <td class="px-4 py-2.5 text-right">
                          <span class="text-amber-500 font-semibold text-xs">
                            <template x-for="s in Math.round(a.avg_stars)" :key="s">★</template>
                          </span>
                          <span class="text-xs text-slate-400 ml-1" x-text="Number(a.avg_stars).toFixed(1)"></span>
                        </td>
                        <td class="px-4 py-2.5 text-right text-xs"
                          :class="usSuccessColor(a.avg_success_rate)"
                          x-text="Number(a.avg_success_rate).toFixed(1) + '%'"></td>
                        <td class="px-4 py-2.5 text-right text-xs text-slate-600" x-text="a.call_count"></td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>

              <!-- 모바일 카드 -->
              <div class="md:hidden space-y-3">
                <template x-for="(a, idx) in (usAnalysts.analysts || [])" :key="a.slug || a.analyst">
                  <div class="bg-white rounded-xl border border-slate-100 p-4">
                    <div class="flex items-center gap-2 mb-1">
                      <span class="text-xs text-slate-400" x-text="idx + 1 + '.'"></span>
                      <span class="font-medium text-slate-800 text-sm" x-text="a.analyst"></span>
                    </div>
                    <div class="text-xs text-slate-500 mb-2" x-text="a.firm"></div>
                    <div class="flex items-center gap-4 text-xs">
                      <span class="text-amber-500 font-semibold" x-text="'★ ' + Number(a.avg_stars).toFixed(1)"></span>
                      <span :class="usSuccessColor(a.avg_success_rate)"
                        x-text="'적중 ' + Number(a.avg_success_rate).toFixed(1) + '%'"></span>
                      <span class="text-slate-400" x-text="a.call_count + '콜'"></span>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- ── US 종목 상세 모달 ── -->
      <template x-if="usModal">
        <div class="fixed inset-0 z-50 flex items-center justify-center p-4"
          @click.self="closeUsModal()">
          <div class="absolute inset-0 bg-black/40 backdrop-blur-sm"></div>
          <div class="relative bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div class="flex items-center justify-between px-6 py-4 border-b border-slate-100 sticky top-0 bg-white">
              <div class="flex items-center gap-2">
                <i data-lucide="star" class="w-5 h-5 text-amber-500"></i>
                <h2 class="font-bold text-slate-800 text-lg" x-text="usModal.ticker + ' 애널리스트 상세'"></h2>
              </div>
              <button @click="closeUsModal()"
                class="text-slate-400 hover:text-slate-600 text-xl leading-none">&times;</button>
            </div>

            <!-- 로딩 스켈레톤 -->
            <template x-if="usModal.loading">
              <div class="p-6 animate-pulse space-y-4">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <template x-for="i in [1,2,3,4]" :key="i">
                    <div class="h-16 bg-slate-100 rounded-lg"></div>
                  </template>
                </div>
                <div class="h-4 w-32 bg-slate-200 rounded mt-4"></div>
                <template x-for="i in [1,2,3,4,5]" :key="i">
                  <div class="h-10 bg-slate-100 rounded"></div>
                </template>
              </div>
            </template>

            <!-- 모달 컨텐츠 -->
            <template x-if="!usModal.loading">
              <div class="p-6 space-y-6">

                <!-- 컨센서스 요약 4그리드 -->
                <template x-if="usModalConsensus && usModalConsensus.data">
                  <div>
                    <h3 class="text-sm font-semibold text-slate-600 mb-3">컨센서스</h3>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-xs text-slate-400 mb-1">등급</div>
                        <div class="text-sm font-bold text-slate-800"
                          x-text="usModalConsensus.data.consensus_rating || '-'"></div>
                      </div>
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-xs text-slate-400 mb-1">평균 TP</div>
                        <div class="text-sm font-bold text-slate-800"
                          x-text="usModalConsensus.data.target_avg ? '$' + Number(usModalConsensus.data.target_avg).toFixed(0) : '-'"></div>
                      </div>
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-xs text-slate-400 mb-1">커버 수</div>
                        <div class="text-sm font-bold text-slate-800"
                          x-text="(usModalConsensus.data.analyst_count || '-') + '명'"></div>
                      </div>
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-xs text-slate-400 mb-1">기준일</div>
                        <div class="text-xs font-medium text-slate-600"
                          x-text="usModalConsensus.data.snapshot_date || '-'"></div>
                      </div>
                    </div>
                  </div>
                </template>

                <!-- 레이팅 이벤트 테이블 (최대 15) -->
                <template x-if="usModalRatings && usModalRatings.events && usModalRatings.events.length > 0">
                  <div>
                    <h3 class="text-sm font-semibold text-slate-600 mb-3">
                      레이팅 이력 <span class="text-slate-400 text-xs font-normal"
                        x-text="'(' + usModalRatings.count + '건 · 최근 15개)'"></span>
                    </h3>
                    <div class="overflow-x-auto">
                      <table class="w-full text-xs">
                        <thead>
                          <tr class="bg-slate-50 border-b border-slate-100">
                            <th class="text-left px-3 py-2 text-slate-500 font-medium">날짜</th>
                            <th class="text-left px-3 py-2 text-slate-500 font-medium">증권사</th>
                            <th class="text-left px-3 py-2 text-slate-500 font-medium">액션</th>
                            <th class="text-left px-3 py-2 text-slate-500 font-medium">등급</th>
                            <th class="text-right px-3 py-2 text-slate-500 font-medium">TP</th>
                          </tr>
                        </thead>
                        <tbody>
                          <template x-for="(ev, i) in usModalRatings.events.slice(0, 15)" :key="i">
                            <tr class="border-b border-slate-50">
                              <td class="px-3 py-2 text-slate-400" x-text="ev.date"></td>
                              <td class="px-3 py-2 text-slate-600 max-w-28 truncate" x-text="ev.firm"></td>
                              <td class="px-3 py-2">
                                <span class="px-1.5 py-0.5 rounded text-xs font-medium"
                                  :class="usActionBadge(ev.action)"
                                  x-text="ev.action"></span>
                              </td>
                              <td class="px-3 py-2 font-medium text-slate-800" x-text="ev.rating_new || '-'"></td>
                              <td class="px-3 py-2 text-right text-slate-600"
                                x-text="ev.pt_now ? '$' + ev.pt_now : '-'"></td>
                            </tr>
                          </template>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </template>

                <!-- FMP TP Summary (연구 데이터) -->
                <template x-if="usModalResearch && !usModalResearch.error && usModalResearch.price_target_summary">
                  <div>
                    <h3 class="text-sm font-semibold text-slate-600 mb-3">FMP Price Target</h3>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                      <div class="bg-blue-50 rounded-lg p-3 text-center">
                        <div class="text-slate-400 mb-1">최근 1개월</div>
                        <div class="font-bold text-slate-800"
                          x-text="usModalResearch.price_target_summary.last_month ? '$' + Number(usModalResearch.price_target_summary.last_month.avg_target).toFixed(0) : '-'"></div>
                        <div class="text-slate-400"
                          x-text="usModalResearch.price_target_summary.last_month ? usModalResearch.price_target_summary.last_month.count + '명' : ''"></div>
                      </div>
                      <div class="bg-blue-50 rounded-lg p-3 text-center">
                        <div class="text-slate-400 mb-1">최근 1분기</div>
                        <div class="font-bold text-slate-800"
                          x-text="usModalResearch.price_target_summary.last_quarter ? '$' + Number(usModalResearch.price_target_summary.last_quarter.avg_target).toFixed(0) : '-'"></div>
                        <div class="text-slate-400"
                          x-text="usModalResearch.price_target_summary.last_quarter ? usModalResearch.price_target_summary.last_quarter.count + '명' : ''"></div>
                      </div>
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-slate-400 mb-1">최근 1년</div>
                        <div class="font-bold text-slate-800"
                          x-text="usModalResearch.price_target_summary.last_year ? '$' + Number(usModalResearch.price_target_summary.last_year.avg_target).toFixed(0) : '-'"></div>
                        <div class="text-slate-400"
                          x-text="usModalResearch.price_target_summary.last_year ? usModalResearch.price_target_summary.last_year.count + '명' : ''"></div>
                      </div>
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-slate-400 mb-1">전체</div>
                        <div class="font-bold text-slate-800"
                          x-text="usModalResearch.price_target_summary.all_time ? '$' + Number(usModalResearch.price_target_summary.all_time.avg_target).toFixed(0) : '-'"></div>
                        <div class="text-slate-400"
                          x-text="usModalResearch.price_target_summary.all_time ? usModalResearch.price_target_summary.all_time.count + '명' : ''"></div>
                      </div>
                    </div>
                  </div>
                </template>

                <!-- 빈 상태 -->
                <template x-if="!usModalRatings && !usModalConsensus && !usModalResearch">
                  <div class="text-center py-8 text-slate-400 text-sm">데이터를 불러오지 못했습니다.</div>
                </template>

              </div>
            </template>
          </div>
        </div>
      </template>

    </section>
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P3a: Whale 탭 패널 HTML (반드시 _HOME_SHELL 이전 정의)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# NOTE: 모든 JS 문자열 리터럴 안 개행은 \\n 사용. 표현식은 중괄호 이스케이프 불필요(raw 문자열).
_WHALE_PANEL = r"""
    <!-- Whale 탭 -->
    <section x-show="activeTab==='whale'" x-cloak
             x-data="{
               wTab: 'pension',
               wCache: {},
               wData: null,
               wLoading: false,
               async wLoad(p) {
                 if (this.wCache[p]) { this.wData = this.wCache[p]; return; }
                 this.wLoading = true;
                 this.wData = null;
                 const d = await (async path => {
                   try { const r = await fetch(path); return await r.json(); }
                   catch(e) { return {error: String(e)}; }
                 })('/api/whale?p=' + p);
                 this.wCache[p] = d;
                 this.wData = d;
                 this.wLoading = false;
                 this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
               },
               setWTab(p) {
                 this.wTab = p;
                 this.wLoad(p);
               }
             }"
             x-init="wLoad(wTab)">

      <!-- 서브탭 바 -->
      <div class="flex flex-wrap gap-2 mb-5">
        <template x-for="tab in [
          {key:'pension', label:'연기금 흐름'},
          {key:'kr_5pct', label:'KR 5%룰'},
          {key:'kr_full', label:'KR 풀포트'},
          {key:'us_13f',  label:'US 13F'},
          {key:'insider', label:'내부자'}
        ]" :key="tab.key">
          <button @click="setWTab(tab.key)"
                  :class="wTab===tab.key ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'"
                  class="px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                  x-text="tab.label">
          </button>
        </template>
      </div>

      <!-- 로딩 스켈레톤 -->
      <template x-if="wLoading">
        <div class="animate-pulse">
          <template x-for="i in [1,2,3,4,5,6,7,8]" :key="i">
            <div class="flex items-center gap-3 py-2.5 border-b border-slate-100 last:border-0">
              <div class="h-3 w-6 bg-slate-200 rounded shrink-0"></div>
              <div class="flex-1">
                <div class="h-3 w-32 bg-slate-200 rounded mb-1"></div>
                <div class="h-3 w-20 bg-slate-200 rounded"></div>
              </div>
              <div class="h-4 w-16 bg-slate-200 rounded ml-auto"></div>
              <div class="h-4 w-14 bg-slate-200 rounded"></div>
            </div>
          </template>
        </div>
      </template>

      <!-- 연기금 흐름 -->
      <template x-if="!wLoading && wTab==='pension' && wData">
        <div>
          <template x-if="wData.error">
            <div class="text-red-500 text-sm py-4" x-text="'오류: ' + wData.error"></div>
          </template>
          <template x-if="!wData.error">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="'기간: ' + (wData.period || '-') + ' | 5일 누적 순매매 시총% 정규화'"></p>
              <h3 class="text-sm font-semibold text-green-600 mb-2">매수 TOP 50</h3>
              <template x-if="!wData.buy_top || !wData.buy_top.length">
                <div class="text-slate-400 text-sm py-2">매수 없음</div>
              </template>
              <template x-if="wData.buy_top && wData.buy_top.length">
                <div class="overflow-x-auto mb-6">
                  <table class="w-full text-sm border-collapse">
                    <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                      <th class="text-left pb-2 font-medium">#</th>
                      <th class="text-left pb-2 font-medium">종목</th>
                      <th class="text-right pb-2 font-medium">순매수</th>
                      <th class="text-right pb-2 font-medium">시총%</th>
                    </tr></thead>
                    <tbody>
                      <template x-for="(e, idx) in wData.buy_top" :key="e.symbol + idx">
                        <tr class="border-b border-slate-100 hover:bg-slate-50">
                          <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                          <td class="py-1.5">
                            <span class="font-medium text-slate-800" x-text="e.name"></span>
                            <span class="text-xs text-slate-400 ml-1" x-text="e.symbol"></span>
                          </td>
                          <td class="py-1.5 text-right text-green-600 font-semibold"
                              x-text="e.net_eok != null ? '+' + e.net_eok.toFixed(0) + '억' : '-'"></td>
                          <td class="py-1.5 text-right text-green-600"
                              x-text="e.cap_pct != null ? '+' + e.cap_pct.toFixed(2) + '%' : '-'"></td>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
              </template>
              <h3 class="text-sm font-semibold text-red-600 mb-2">매도 TOP 50</h3>
              <template x-if="!wData.sell_top || !wData.sell_top.length">
                <div class="text-slate-400 text-sm py-2">매도 없음</div>
              </template>
              <template x-if="wData.sell_top && wData.sell_top.length">
                <div class="overflow-x-auto">
                  <table class="w-full text-sm border-collapse">
                    <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                      <th class="text-left pb-2 font-medium">#</th>
                      <th class="text-left pb-2 font-medium">종목</th>
                      <th class="text-right pb-2 font-medium">순매도</th>
                      <th class="text-right pb-2 font-medium">시총%</th>
                    </tr></thead>
                    <tbody>
                      <template x-for="(e, idx) in wData.sell_top" :key="e.symbol + idx">
                        <tr class="border-b border-slate-100 hover:bg-slate-50">
                          <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                          <td class="py-1.5">
                            <span class="font-medium text-slate-800" x-text="e.name"></span>
                            <span class="text-xs text-slate-400 ml-1" x-text="e.symbol"></span>
                          </td>
                          <td class="py-1.5 text-right text-red-600 font-semibold"
                              x-text="e.net_eok != null ? e.net_eok.toFixed(0) + '억' : '-'"></td>
                          <td class="py-1.5 text-right text-red-600"
                              x-text="e.cap_pct != null ? e.cap_pct.toFixed(2) + '%' : '-'"></td>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
              </template>
            </div>
          </template>
        </div>
      </template>

      <!-- KR 5%룰 -->
      <template x-if="!wLoading && wTab==='kr_5pct' && wData">
        <div>
          <template x-if="wData[0] && wData[0].error">
            <div class="text-red-500 text-sm" x-text="wData[0].error"></div>
          </template>
          <template x-if="wData.length && !(wData[0] && wData[0].error)">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="wData[0] ? wData[0].quarter + ' | 총 ' + wData.length + '건 | 10%+ 빨강' : ''"></p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                    <th class="text-left pb-2 font-medium">#</th>
                    <th class="text-left pb-2 font-medium">보고일</th>
                    <th class="text-left pb-2 font-medium">종목</th>
                    <th class="text-right pb-2 font-medium">지분%</th>
                    <th class="text-right pb-2 font-medium">전분기</th>
                  </tr></thead>
                  <tbody>
                    <template x-for="(r, idx) in wData" :key="r.symbol + r.report_date + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5 text-xs text-slate-500" x-text="r.report_date"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="r.company_name"></span>
                          <span class="text-xs text-slate-400 ml-1" x-text="r.symbol"></span>
                        </td>
                        <td class="py-1.5 text-right"
                            :class="r.ratio_pct >= 10 ? 'text-red-600 font-bold' : 'text-slate-700'"
                            x-text="r.ratio_pct != null ? r.ratio_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-xs">
                          <template x-if="r.change_label === 'NEW'">
                            <span class="text-green-600 font-semibold">NEW</span>
                          </template>
                          <template x-if="r.change_label === 'UP' && r.change != null">
                            <span class="text-green-600">+<span x-text="r.change.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="r.change_label === 'DOWN' && r.change != null">
                            <span class="text-red-500"><span x-text="r.change.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="r.change_label === 'FLAT' || r.change_label === ''">
                            <span class="text-slate-400">—</span>
                          </template>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
          <template x-if="!wData.length">
            <div class="text-slate-400 text-sm py-4">데이터 없음</div>
          </template>
        </div>
      </template>

      <!-- KR 풀포트 -->
      <template x-if="!wLoading && wTab==='kr_full' && wData">
        <div>
          <template x-if="wData.error">
            <div class="text-red-500 text-sm py-4" x-text="'오류: ' + wData.error"></div>
          </template>
          <template x-if="!wData.error">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="(wData.quarter_label || '-') + ' | 스냅샷 ' + (wData.snapshot_date || '-') + ' | 총 ' + (wData.total_holdings || 0) + '종목'"></p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                    <th class="text-left pb-2 font-medium">#</th>
                    <th class="text-left pb-2 font-medium">종목</th>
                    <th class="text-right pb-2 font-medium">비중%</th>
                    <th class="text-right pb-2 font-medium">평가액</th>
                    <th class="text-right pb-2 font-medium">지분%</th>
                    <th class="text-right pb-2 font-medium">전년대비</th>
                  </tr></thead>
                  <tbody>
                    <template x-for="(x, idx) in (wData.rows || [])" :key="(x.symbol || x.name) + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="x.name"></span>
                          <span class="text-xs text-slate-400 ml-1" x-text="x.symbol"></span>
                        </td>
                        <td class="py-1.5 text-right text-slate-700"
                            x-text="x.weight_pct != null ? x.weight_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-slate-600 text-xs"
                            x-text="x.valuation_eok != null ? x.valuation_eok.toLocaleString('ko-KR') + '억' : '-'"></td>
                        <td class="py-1.5 text-right"
                            :class="x.share_curr_pct >= 10 ? 'text-red-600 font-bold' : 'text-slate-600'"
                            x-text="x.share_curr_pct != null ? x.share_curr_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-xs">
                          <template x-if="x.data_missing || x.share_change_p == null">
                            <span class="text-slate-400">—</span>
                          </template>
                          <template x-if="!x.data_missing && x.share_change_p != null && x.share_change_p > 0.05">
                            <span class="text-green-600">+<span x-text="x.share_change_p.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="!x.data_missing && x.share_change_p != null && x.share_change_p < -0.05">
                            <span class="text-red-500"><span x-text="x.share_change_p.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="!x.data_missing && x.share_change_p != null && x.share_change_p >= -0.05 && x.share_change_p <= 0.05">
                            <span class="text-slate-400">—</span>
                          </template>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- US 13F -->
      <template x-if="!wLoading && wTab==='us_13f' && wData">
        <div>
          <template x-if="wData.error">
            <div class="text-red-500 text-sm py-4" x-text="'오류: ' + wData.error"></div>
          </template>
          <template x-if="!wData.error">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="(wData.quarter || '-') + ' | 분기말 ' + (wData.period_end || '-') + ' | TOP 100 / ' + (wData.total_holdings || 0) + '종목'"></p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                    <th class="text-left pb-2 font-medium">#</th>
                    <th class="text-left pb-2 font-medium">종목</th>
                    <th class="text-right pb-2 font-medium">가치</th>
                    <th class="text-right pb-2 font-medium">비중%</th>
                    <th class="text-right pb-2 font-medium">주식변화</th>
                  </tr></thead>
                  <tbody>
                    <template x-for="(x, idx) in (wData.rows || [])" :key="(x.cusip || x.name_of_issuer) + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="x.name_of_issuer || '-'"></span>
                          <template x-if="x.ticker">
                            <span class="text-xs text-slate-400 ml-1" x-text="x.ticker"></span>
                          </template>
                        </td>
                        <td class="py-1.5 text-right text-slate-700 text-xs"
                            x-text="x.value_usd != null ? (x.value_usd >= 1e9 ? '$' + (x.value_usd/1e9).toFixed(2) + 'B' : '$' + (x.value_usd/1e6).toFixed(0) + 'M') : '-'"></td>
                        <td class="py-1.5 text-right text-slate-600 text-xs"
                            x-text="x.weight_pct != null ? x.weight_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-xs">
                          <template x-if="x.status === 'NEW'">
                            <span class="text-green-600 font-semibold">NEW</span>
                          </template>
                          <template x-if="x.status === 'UP' && x.share_change_pct != null">
                            <span class="text-green-600">+<span x-text="x.share_change_pct.toFixed(1)"></span>%</span>
                          </template>
                          <template x-if="x.status === 'DOWN' && x.share_change_pct != null">
                            <span class="text-red-500"><span x-text="x.share_change_pct.toFixed(1)"></span>%</span>
                          </template>
                          <template x-if="!x.status || (x.status !== 'NEW' && x.status !== 'UP' && x.status !== 'DOWN')">
                            <span class="text-slate-400">—</span>
                          </template>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- 내부자 -->
      <template x-if="!wLoading && wTab==='insider' && wData">
        <div>
          <template x-if="wData[0] && wData[0].error">
            <div class="text-red-500 text-sm py-4" x-text="wData[0].error"></div>
          </template>
          <template x-if="wData.length && !(wData[0] && wData[0].error)">
            <div>
              <p class="text-xs text-slate-400 mb-4">
                최근 90일 | 5%+ 주요주주·임원 | <span x-text="wData.length + '건'"></span>
              </p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                    <th class="text-left pb-2 font-medium">#</th>
                    <th class="text-left pb-2 font-medium">보고일</th>
                    <th class="text-left pb-2 font-medium">종목</th>
                    <th class="text-left pb-2 font-medium">보고자</th>
                    <th class="text-right pb-2 font-medium">증감</th>
                    <th class="text-right pb-2 font-medium">지분%</th>
                  </tr></thead>
                  <tbody>
                    <template x-for="(r, idx) in wData" :key="(r.rcept_dt || '') + (r.symbol || '') + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5 text-xs text-slate-500" x-text="r.rcept_dt"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="r.company_name || ''"></span>
                          <span class="text-xs text-slate-400 ml-1" x-text="r.symbol"></span>
                        </td>
                        <td class="py-1.5 text-xs">
                          <span class="text-slate-700" x-text="r.repror"></span>
                          <span class="text-slate-400 ml-1" x-text="r.role"></span>
                        </td>
                        <td class="py-1.5 text-right font-semibold"
                            :class="r.direction === 'buy' ? 'text-green-600' : 'text-red-500'"
                            x-text="r.irds_cnt != null ? (r.irds_cnt > 0 ? '+' : '') + r.irds_cnt.toLocaleString('ko-KR') : '-'"></td>
                        <td class="py-1.5 text-right text-xs"
                            :class="r.stock_rate >= 10 ? 'text-red-600 font-bold' : 'text-slate-600'"
                            x-text="r.stock_rate != null ? r.stock_rate.toFixed(2) + '%' : '-'"></td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
          <template x-if="!wData.length">
            <div class="text-slate-400 text-sm py-4">최근 90일 5%+ 보유자 매매 없음</div>
          </template>
        </div>
      </template>

    </section>
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 홈 패널 HTML (Alpine 템플릿)
# 완전히 별도 문자열로 분리 — JS 중괄호와 충돌 없음.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_HOME_PANEL = (
    '    <!-- 홈 패널 -->\n'
    '    <section x-show="activeTab===\'home\'">\n'
    '\n'
    '      <!-- 로딩 스켈레톤 (최초 1회만) -->\n'
    '      <template x-if="!home">\n'
    '        <div class="animate-pulse">\n'
    '          <!-- 지수 칩 x4 -->\n'
    '          <div class="flex gap-3 overflow-x-auto pb-1 mb-5">\n'
    '            <template x-for="i in [1,2,3,4]" :key="i">\n'
    '              <div class="flex-shrink-0 bg-white rounded-xl border border-slate-100 px-4 py-3 min-w-[110px] flex flex-col gap-2">\n'
    '                <div class="h-2.5 w-14 bg-slate-200 rounded"></div>\n'
    '                <div class="h-5 w-20 bg-slate-200 rounded"></div>\n'
    '                <div class="h-3 w-10 bg-slate-200 rounded"></div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '          <!-- 자산 요약 카드 -->\n'
    '          <div class="bg-white rounded-xl border border-slate-100 p-5 mb-6">\n'
    '            <div class="h-3 w-24 bg-slate-200 rounded mb-4"></div>\n'
    '            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">\n'
    '              <template x-for="j in [1,2,3,4]" :key="j">\n'
    '                <div>\n'
    '                  <div class="h-2.5 w-16 bg-slate-200 rounded mb-2"></div>\n'
    '                  <div class="h-6 w-24 bg-slate-200 rounded mb-1"></div>\n'
    '                  <div class="h-3 w-20 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </div>\n'
    '          <!-- 신호 카드 그리드 x3 -->\n'
    '          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">\n'
    '            <template x-for="k in [1,2,3]" :key="k">\n'
    '              <div class="bg-white rounded-xl border border-slate-100 p-4">\n'
    '                <div class="h-3 w-24 bg-slate-200 rounded mb-3"></div>\n'
    '                <template x-for="m in [1,2,3]" :key="m">\n'
    '                  <div class="flex items-center gap-3 py-2.5 border-b border-slate-50 last:border-0">\n'
    '                    <div class="rounded-full h-8 w-8 bg-slate-200 shrink-0"></div>\n'
    '                    <div class="flex-1">\n'
    '                      <div class="h-3 w-32 bg-slate-200 rounded mb-1"></div>\n'
    '                      <div class="h-3 w-20 bg-slate-200 rounded"></div>\n'
    '                    </div>\n'
    '                    <div class="ml-auto h-4 w-14 bg-slate-200 rounded"></div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '      <template x-if="home">\n'
    '        <div>\n'
    '\n'
    '          <!-- 지수 띠 -->\n'
    '          <template x-if="home.indices && home.indices.length">\n'
    '            <div class="flex gap-3 overflow-x-auto pb-1 mb-5 scrollbar-hide">\n'
    '              <template x-for="idx in home.indices" :key="idx.name">\n'
    '                <div class="flex-shrink-0 bg-white rounded-xl shadow-sm border border-slate-200 px-4 py-3 flex flex-col min-w-[110px]">\n'
    '                  <span class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-0.5" x-text="idx.name"></span>\n'
    '                  <span class="text-base font-bold text-slate-800"\n'
    '                        x-text="idx.price != null ? idx.price.toLocaleString(\'ko-KR\', {maximumFractionDigits:2}) : \'-\'"></span>\n'
    '                  <span :class="chgClass(idx.change_pct)" class="text-xs font-semibold mt-0.5"\n'
    '                        x-text="chgStr(idx.change_pct)"></span>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 자산 요약 카드 -->\n'
    '          <template x-if="home.portfolio && !home.portfolio.empty">\n'
    '            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-6">\n'
    '              <h2 class="text-sm font-semibold text-slate-500 mb-3">자산 요약</h2>\n'
    '              <div class="grid grid-cols-2 md:grid-cols-4 gap-4">\n'
    '                <!-- KR 평가 -->\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">국내 평가</div>\n'
    '                  <div class="text-lg font-bold text-slate-800" x-text="won(home.portfolio.kr_eval)"></div>\n'
    '                  <div :class="pnlClass(home.portfolio.kr_pnl)" class="text-sm"\n'
    '                       x-text="won(home.portfolio.kr_pnl) + \' (\' + pct(home.portfolio.kr_pnl_pct) + \')\'"></div>\n'
    '                </div>\n'
    '                <!-- US 평가 -->\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">해외 평가</div>\n'
    '                  <div class="text-lg font-bold text-slate-800" x-text="usd(home.portfolio.us_eval)"></div>\n'
    '                  <div :class="pnlClass(home.portfolio.us_pnl)" class="text-sm"\n'
    '                       x-text="usd(home.portfolio.us_pnl) + \' (\' + pct(home.portfolio.us_pnl_pct) + \')\'"></div>\n'
    '                </div>\n'
    '                <!-- 현금 KRW -->\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">현금 (원)</div>\n'
    '                  <div class="text-lg font-bold text-slate-700" x-text="won(home.portfolio.cash_krw)"></div>\n'
    '                </div>\n'
    '                <!-- 현금 USD -->\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">현금 ($)</div>\n'
    '                  <div class="text-lg font-bold text-slate-700" x-text="usd(home.portfolio.cash_usd)"></div>\n'
    '                </div>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 신호 카드 그리드 -->\n'
    '          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">\n'
    '\n'
    '            <!-- 손절 근접 카드 -->\n'
    '            <template x-if="home.alerts && home.alerts.stoploss && home.alerts.stoploss.length">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="alert-triangle" class="w-4 h-4 text-red-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">손절 근접</span>\n'
    '                </div>\n'
    '                <template x-for="a in home.alerts.stoploss" :key="a.ticker">\n'
    '                  <div class="flex items-center justify-between py-1.5 border-b border-slate-50 last:border-0">\n'
    '                    <div>\n'
    '                      <span class="text-sm font-medium text-slate-800" x-text="a.name"></span>\n'
    '                      <span class="text-xs text-slate-400 ml-1" x-text="a.ticker"></span>\n'
    '                    </div>\n'
    '                    <div :class="gapClass(a.gap_pct)" class="text-sm"\n'
    '                         x-text="a.gap_pct != null ? (a.gap_pct > 0 ? \'+\' : \'\') + a.gap_pct.toFixed(1) + \'%\' : \'-\'"></div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- 워치 근접 카드 -->\n'
    '            <template x-if="home.alerts && home.alerts.watch && home.alerts.watch.length">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="target" class="w-4 h-4 text-blue-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">매수 근접</span>\n'
    '                </div>\n'
    '                <template x-for="w in home.alerts.watch" :key="w.ticker">\n'
    '                  <div class="flex items-center justify-between py-1.5 border-b border-slate-50 last:border-0">\n'
    '                    <div class="flex items-center gap-1.5">\n'
    '                      <span class="text-sm font-medium text-slate-800" x-text="w.name"></span>\n'
    '                      <template x-if="w.triggered">\n'
    '                        <span class="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">도달</span>\n'
    '                      </template>\n'
    '                    </div>\n'
    '                    <div class="text-sm text-slate-600"\n'
    '                         x-text="w.gap_pct != null ? (w.gap_pct > 0 ? \'+\' : \'\') + w.gap_pct.toFixed(1) + \'%\' : \'-\'"></div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- 임박 이벤트 카드 -->\n'
    '            <template x-if="home.events && home.events.length">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="calendar" class="w-4 h-4 text-purple-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">임박 이벤트</span>\n'
    '                </div>\n'
    '                <template x-for="ev in home.events" :key="ev.name">\n'
    '                  <div class="flex items-center justify-between py-1.5 border-b border-slate-50 last:border-0">\n'
    '                    <span class="text-sm text-slate-700 truncate max-w-[160px]" x-text="ev.name"></span>\n'
    '                    <span class="text-xs text-slate-500 whitespace-nowrap ml-2"\n'
    '                          x-text="\'D-\' + ev.dday + \' (\' + ev.date + \')\'"></span>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- 발굴 스캔 카드 -->\n'
    '            <template x-if="home.scan && home.scan.count > 0">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="search" class="w-4 h-4 text-teal-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">변화감지 스캔</span>\n'
    '                </div>\n'
    '                <div class="text-slate-700">\n'
    '                  <span class="text-2xl font-bold" x-text="home.scan.count"></span>\n'
    '                  <span class="text-sm text-slate-400 ml-1">건</span>\n'
    '                </div>\n'
    '                <div class="text-xs text-slate-400 mt-1" x-text="home.scan.date ? \'최근: \' + home.scan.date : \'\'"></div>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- 컨센서스 변동 카드 -->\n'
    '            <template x-if="home.consensus && home.consensus.length">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="trending-up" class="w-4 h-4 text-indigo-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">컨센서스 변동</span>\n'
    '                </div>\n'
    '                <template x-for="c in home.consensus" :key="c.ticker">\n'
    '                  <div class="flex items-center justify-between py-1.5 border-b border-slate-50 last:border-0">\n'
    '                    <span class="text-sm text-slate-700" x-text="c.name"></span>\n'
    '                    <span :class="consBadgeClass(c.chg_pct)" class="text-sm"\n'
    '                          x-text="(c.chg_pct >= 0 ? \'+\' : \'\') + c.chg_pct + \'%\'"></span>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- DART 카드 -->\n'
    '            <template x-if="home.dart && home.dart.count > 0">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="file-text" class="w-4 h-4 text-orange-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">DART 공시</span>\n'
    '                </div>\n'
    '                <div class="text-slate-700 mb-2" x-text="home.dart.label || (home.dart.count + \'건 누적 감지\')"></div>\n'
    '                <button @click="setTab(\'signal\'); signalSeg=\'dart\'"\n'
    '                  class="text-xs text-blue-600 hover:text-blue-700 hover:underline">\n'
    '                  시그널 탭에서 보기\n'
    '                </button>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '          </div><!-- /신호 카드 그리드 -->\n'
    '\n'
    '          <!-- 에러 디버그 (있을 때만) -->\n'
    '          <template x-if="home._errors && home._errors.length">\n'
    '            <div class="mt-4 text-xs text-slate-400">\n'
    '              <template x-for="err in home._errors" :key="err.source">\n'
    '                <div x-text="err.source + \': \' + err.msg"></div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '    </section>\n'
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 완성된 HTML 문서 (일반 문자열 — f-string 아님)
# JS 중괄호와 충돌 없음. Alpine 속성은 HTML 어트리뷰트라 OK.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_HOME_SHELL = (
    "<!DOCTYPE html>\n"
    '<html lang="ko">\n'
    "<head>\n"
    '  <meta charset="utf-8">\n'
    '  <meta name="viewport" content="width=device-width,initial-scale=1">\n'
    "  <title>\U0001f4ca Stock Bot</title>\n"
    '  <script src="https://cdn.tailwindcss.com"></script>\n'
    '  <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>\n'
    '  <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>\n'
    '  <script src="https://unpkg.com/lucide@latest"></script>\n'
    '  <script defer src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js"></script>\n'
    "  <style>\n"
    "    @import url('https://fonts.googleapis.com/css2?family=Pretendard:wght@400;600;800&display=swap');\n"
    "    body { font-family: 'Pretendard', sans-serif; background-color: #f8fafc; }\n"
    "    [x-cloak] { display: none !important; }\n"
    "  </style>\n"
    "</head>\n"
    '<body class="min-h-screen">\n'
    '\n'
    '<!-- Alpine 루트 -->\n'
    '<div x-data="dashApp()" x-init="init()">\n'
    '\n'
    '  <!-- 상단 sticky 바 -->\n'
    '  <header class="sticky top-0 z-50 bg-white border-b border-slate-200 shadow-sm">\n'
    '    <div class="max-w-6xl mx-auto px-4 flex items-center justify-between h-12">\n'
    '      <div class="flex items-center gap-2">\n'
    '        <span class="text-lg font-bold text-slate-800">\U0001f4ca Stock Bot</span>\n'
    '        <template x-if="home && home.regime">\n'
    '          <span\n'
    '            :class="[\'text-xs px-2 py-0.5 rounded-full\', regimeBadgeClass(home.regime.color)]"\n'
    '            x-text="home.regime.label"\n'
    '          ></span>\n'
    '        </template>\n'
    '        <template x-if="!home || !home.regime">\n'
    '          <span class="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-400">로딩...</span>\n'
    '        </template>\n'
    '      </div>\n'
    '      <div class="flex items-center gap-3">\n'
    '        <span x-text="lastUpdated" class="text-xs text-slate-400"></span>\n'
    '        <button\n'
    '          @click="toggleAutoRefresh()"\n'
    '          :class="autoRefresh ? \'bg-blue-50 border-blue-200 text-blue-600\' : \'border-slate-200 text-slate-500\'"\n'
    '          class="text-xs px-2 py-1 rounded border hover:opacity-80 transition-opacity"\n'
    '          x-text="autoRefresh ? \'자동갱신 ON\' : \'자동갱신 OFF\'"\n'
    '        ></button>\n'
    '      </div>\n'
    '    </div>\n'
    '  </header>\n'
    '\n'
    '  <!-- 탭 네비 (9개) -->\n'
    '  <nav class="bg-white border-b border-slate-200 sticky top-12 z-40">\n'
    '    <div class="max-w-6xl mx-auto px-4">\n'
    '      <div class="overflow-x-auto">\n'
    '        <div class="flex gap-1 py-2 whitespace-nowrap">\n'
    '\n'
    '          <!-- 홈 -->\n'
    '          <button\n'
    '            @click="setTab(\'home\')"\n'
    '            :class="activeTab===\'home\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="home" class="w-4 h-4"></i>\n'
    '            홈\n'
    '          </button>\n'
    '\n'
    '          <!-- 시세 -->\n'
    '          <button\n'
    '            @click="setTab(\'market\')"\n'
    '            :class="activeTab===\'market\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="trending-up" class="w-4 h-4"></i>\n'
    '            시세\n'
    '          </button>\n'
    '\n'
    '          <!-- 포트폴리오 -->\n'
    '          <button\n'
    '            @click="setTab(\'portfolio\')"\n'
    '            :class="activeTab===\'portfolio\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="bar-chart-2" class="w-4 h-4"></i>\n'
    '            포트폴리오\n'
    '          </button>\n'
    '\n'
    '          <!-- 워치·알림 -->\n'
    '          <button\n'
    '            @click="setTab(\'watch\')"\n'
    '            :class="activeTab===\'watch\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="bell" class="w-4 h-4"></i>\n'
    '            워치·알림\n'
    '          </button>\n'
    '\n'
    '          <!-- 시그널 -->\n'
    '          <button\n'
    '            @click="setTab(\'signal\')"\n'
    '            :class="activeTab===\'signal\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="zap" class="w-4 h-4"></i>\n'
    '            시그널\n'
    '          </button>\n'
    '\n'
    '          <!-- 기록 -->\n'
    '          <button\n'
    '            @click="setTab(\'record\')"\n'
    '            :class="activeTab===\'record\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="clipboard-list" class="w-4 h-4"></i>\n'
    '            기록\n'
    '          </button>\n'
    '\n'
    '          <!-- US 애널 -->\n'
    '          <button\n'
    '            @click="setTab(\'us\')"\n'
    '            :class="activeTab===\'us\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="star" class="w-4 h-4"></i>\n'
    '            US 애널\n'
    '          </button>\n'
    '\n'
    '          <!-- Whale -->\n'
    '          <button\n'
    '            @click="setTab(\'whale\')"\n'
    '            :class="activeTab===\'whale\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="fish" class="w-4 h-4"></i>\n'
    '            Whale\n'
    '          </button>\n'
    '\n'
    '          <!-- 리포트 -->\n'
    '          <button\n'
    '            @click="setTab(\'report\')"\n'
    '            :class="activeTab===\'report\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="file-text" class="w-4 h-4"></i>\n'
    '            리포트\n'
    '          </button>\n'
    '\n'
    '        </div>\n'
    '      </div>\n'
    '    </div>\n'
    '  </nav>\n'
    '\n'
    '  <!-- 탭 패널 -->\n'
    '  <main class="max-w-6xl mx-auto px-4 py-6">\n'
    '\n'
    + _HOME_PANEL
    + _MARKET_PANEL
    + _PORTFOLIO_PANEL
    + _WATCH_PANEL
    + _SIGNAL_PANEL
    + _RECORD_PANEL
    + _US_PANEL
    + _WHALE_PANEL
    + _REPORT_PANEL
    + '\n'
    '  </main>\n'
    '\n'
    '</div><!-- /Alpine 루트 -->\n'
    '\n'
    "<script>\n"
    + _DASH_APP_JS
    + "\n</script>\n"
    "\n"
    "</body>\n"
    "</html>\n"
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P3a: Whale — DB 헬퍼 + build_whale_payload
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _open_db() -> _sqlite3.Connection:
    """stock.db 읽기전용 연결 (Row factory). Whale + Reports 공용."""
    conn = _sqlite3.connect(f"{_DATA_DIR}/stock.db", timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size = -32768;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA busy_timeout = 20000;")
    conn.row_factory = _sqlite3.Row
    return conn


# 레거시 alias (P3a 코드에서 _open_whale_db() 호출됨)
_open_whale_db = _open_db


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P3b: 리포트 탭 — build_reports_payload
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _sync_reports_payload() -> dict:
    """reports 테이블에서 4세그먼트 집계 (동기 본체).

    kr: category='company' AND ticker GLOB '[0-9]*'  — 종목 카드 그리드
    us: category='company' AND ticker GLOB '[A-Za-z]*' — 종목 카드 그리드
    industry: category='industry' — 날짜 내림차순 LIMIT 200
    macro: category IN ('market','strategy','economy','bond') — 날짜 내림차순 LIMIT 200

    stock_master 조인으로 종목명 보강 (symbol 컬럼).
    """
    result: dict = {
        "kr": [], "us": [], "industry": [], "macro": [],
        "kr_total": 0, "us_total": 0,
        "industry_total": 0, "macro_total": 0,
    }
    try:
        conn = _open_db()

        # KR 종목 — 티커별 집계 + stock_master 이름 보강
        rows = conn.execute(
            "SELECT r.ticker,"
            " COALESCE(NULLIF(sm.name,''), NULLIF(r.name,''), r.ticker) AS rname,"
            " COUNT(*) AS cnt, MAX(r.date) AS latest"
            " FROM reports r"
            " LEFT JOIN stock_master sm ON sm.symbol = r.ticker"
            " WHERE r.category = 'company' AND r.ticker GLOB '[0-9]*'"
            " GROUP BY r.ticker ORDER BY cnt DESC"
        ).fetchall()
        result["kr_total"] = len(rows)
        result["kr"] = [
            {"ticker": r["ticker"], "name": r["rname"], "cnt": r["cnt"], "latest": r["latest"]}
            for r in rows
        ]

        # US 종목
        rows = conn.execute(
            "SELECT r.ticker,"
            " COALESCE(NULLIF(sm.name,''), NULLIF(r.name,''), r.ticker) AS rname,"
            " COUNT(*) AS cnt, MAX(r.date) AS latest"
            " FROM reports r"
            " LEFT JOIN stock_master sm ON sm.symbol = r.ticker"
            " WHERE r.category = 'company' AND r.ticker GLOB '[A-Za-z]*'"
            " GROUP BY r.ticker ORDER BY cnt DESC"
        ).fetchall()
        result["us_total"] = len(rows)
        result["us"] = [
            {"ticker": r["ticker"], "name": r["rname"], "cnt": r["cnt"], "latest": r["latest"]}
            for r in rows
        ]

        # 산업 리포트
        rows = conn.execute(
            "SELECT date, name AS sector, title, source, ticker, pdf_path"
            " FROM reports WHERE category = 'industry'"
            " ORDER BY date DESC LIMIT 200"
        ).fetchall()
        cnt_q = conn.execute(
            "SELECT COUNT(*) AS n FROM reports WHERE category = 'industry'"
        ).fetchone()
        result["industry_total"] = cnt_q["n"] if cnt_q else 0
        result["industry"] = [
            {
                "date": r["date"], "sector": r["sector"],
                "title": r["title"], "source": r["source"],
                "ticker": r["ticker"],
                "pdf_basename": os.path.basename(r["pdf_path"]) if r["pdf_path"] else "",
            }
            for r in rows
        ]

        # 시황·전략·경제·채권
        rows = conn.execute(
            "SELECT date, category, name AS label, title, source, ticker, pdf_path"
            " FROM reports"
            " WHERE category IN ('market','strategy','economy','bond')"
            " ORDER BY date DESC LIMIT 200"
        ).fetchall()
        cnt_q = conn.execute(
            "SELECT COUNT(*) AS n FROM reports"
            " WHERE category IN ('market','strategy','economy','bond')"
        ).fetchone()
        result["macro_total"] = cnt_q["n"] if cnt_q else 0
        result["macro"] = [
            {
                "date": r["date"], "category": r["category"],
                "label": r["label"],
                "title": r["title"], "source": r["source"],
                "ticker": r["ticker"],
                "pdf_basename": os.path.basename(r["pdf_path"]) if r["pdf_path"] else "",
            }
            for r in rows
        ]

        conn.close()
    except Exception as exc:
        result["_error"] = str(exc)
    return result


async def build_reports_payload() -> dict:
    """_sync_reports_payload를 executor에서 실행 (whale과 동일 패턴)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_reports_payload)


def _sync_reports_by_ticker(ticker: str) -> list:
    """종목별 리포트 목록 — 날짜 내림차순."""
    try:
        conn = _open_db()
        rows = conn.execute(
            "SELECT date, source, analyst, title, target_price, opinion, pdf_path"
            " FROM reports WHERE ticker = ? ORDER BY date DESC",
            (ticker,),
        ).fetchall()
        conn.close()
        return [
            {
                "date": r["date"], "source": r["source"],
                "analyst": r["analyst"], "title": r["title"],
                "target_price": r["target_price"], "opinion": r["opinion"],
                "pdf_basename": os.path.basename(r["pdf_path"]) if r["pdf_path"] else "",
            }
            for r in rows
        ]
    except Exception as exc:
        return [{"error": str(exc)}]


async def _reports_by_ticker(ticker: str) -> list:
    """_sync_reports_by_ticker를 executor에서 실행 (whale과 동일 패턴)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_reports_by_ticker, ticker)


def _whale_home() -> dict:
    """home 프리셋 — 각 소스별 최신 날짜 + 건수 요약."""
    result: dict = {}
    try:
        conn = _open_whale_db()
        # kr_full
        r = conn.execute(
            "SELECT snapshot_date, COUNT(*) AS cnt FROM nps_kr_full_holdings"
            " GROUP BY snapshot_date ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        result["kr_full"] = {"snapshot_date": r["snapshot_date"], "count": r["cnt"]} if r else {}

        # us_13f
        r = conn.execute(
            "SELECT quarter, period_end, COUNT(*) AS cnt FROM nps_us_holdings"
            " GROUP BY quarter ORDER BY period_end DESC LIMIT 1"
        ).fetchone()
        result["us_13f"] = {"quarter": r["quarter"], "period_end": r["period_end"], "count": r["cnt"]} if r else {}

        # kr_5pct
        r = conn.execute(
            "SELECT quarter, COUNT(*) AS cnt FROM nps_holdings_disclosed"
            " WHERE quarter != '' GROUP BY quarter ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
        result["kr_5pct"] = {"quarter": r["quarter"], "count": r["cnt"]} if r else {}

        # pension
        r = conn.execute(
            "SELECT trade_date, COUNT(DISTINCT symbol) AS cnt"
            " FROM pension_flow_daily GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        result["pension"] = {"latest_date": r["trade_date"], "symbols": r["cnt"]} if r else {}

        # insider
        r = conn.execute(
            "SELECT COUNT(*) AS cnt, MAX(rcept_dt) AS latest FROM insider_transactions"
            " WHERE stock_irds_cnt != 0"
        ).fetchone()
        result["insider"] = {"latest_date": r["latest"] or "", "count": r["cnt"]} if r else {}

        conn.close()
    except Exception as exc:
        result["_error"] = str(exc)
    return result


def _whale_kr_5pct() -> list:
    """NPS 5%룰 최신 분기 전체 — 실제 컬럼만 사용."""
    try:
        conn = _open_whale_db()
        latest_q_row = conn.execute(
            "SELECT quarter FROM nps_holdings_disclosed WHERE quarter != ''"
            " ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
        if not latest_q_row:
            conn.close()
            return []
        latest_q = latest_q_row["quarter"]

        prev_q_row = conn.execute(
            "SELECT DISTINCT quarter FROM nps_holdings_disclosed"
            " WHERE quarter != '' AND quarter < ? ORDER BY quarter DESC LIMIT 1",
            (latest_q,),
        ).fetchone()
        prev_q = prev_q_row["quarter"] if prev_q_row else None

        prev_map: dict = {}
        if prev_q:
            for pr in conn.execute(
                "SELECT symbol, MAX(ratio_pct) AS max_r FROM nps_holdings_disclosed"
                " WHERE quarter = ? AND symbol != '' GROUP BY symbol",
                (prev_q,),
            ).fetchall():
                prev_map[pr["symbol"]] = float(pr["max_r"] or 0)

        rows = conn.execute(
            "SELECT report_date, company_name, symbol, ratio_pct"
            " FROM nps_holdings_disclosed WHERE quarter = ?"
            " ORDER BY ratio_pct DESC, report_date DESC",
            (latest_q,),
        ).fetchall()
        conn.close()

        out = []
        for r in rows:
            cur_r = float(r["ratio_pct"] or 0)
            sym = r["symbol"] or ""
            prev_r = prev_map.get(sym) if sym and prev_q else None
            if prev_q and sym:
                if prev_r is None:
                    change_label = "NEW"
                    change_val = None
                else:
                    change_val = round(cur_r - prev_r, 4)
                    change_label = "UP" if change_val > 0.05 else ("DOWN" if change_val < -0.05 else "FLAT")
            else:
                change_label = ""
                change_val = None
            out.append({
                "report_date": r["report_date"],
                "company_name": r["company_name"],
                "symbol": sym,
                "ratio_pct": cur_r,
                "prev_ratio": prev_map.get(sym),
                "change": change_val,
                "change_label": change_label,
                "is_new": change_label == "NEW",
                "quarter": latest_q,
                "prev_quarter": prev_q,
            })
        return out
    except Exception as exc:
        return [{"error": str(exc)}]


def _whale_kr_full() -> dict:
    """NPS KR 풀포트 — fetch_nps_kr_full_holdings 래핑."""
    try:
        from kis_api import fetch_nps_kr_full_holdings
        return fetch_nps_kr_full_holdings(top=200)
    except Exception as exc:
        return {"error": str(exc), "rows": []}


def _whale_us_13f() -> dict:
    """NPS US 13F — fetch_nps_us_holdings 래핑."""
    try:
        from kis_api import fetch_nps_us_holdings
        return fetch_nps_us_holdings(top=100, include_changes=True)
    except Exception as exc:
        return {"error": str(exc), "rows": []}


def _whale_pension() -> list:
    """연기금 5일 누적 순매매 — 직접 SQL (시총% 포함)."""
    try:
        conn = _open_whale_db()
        dates = [r["trade_date"] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM pension_flow_daily"
            " ORDER BY trade_date DESC LIMIT 5"
        ).fetchall()]
        if not dates:
            conn.close()
            return []
        ph = ",".join("?" for _ in dates)
        agg_rows = conn.execute(
            f"SELECT pf.symbol, pf.name, pf.market,"
            f" SUM(pf.net_amount_won) AS net_total"
            f" FROM pension_flow_daily pf"
            f" WHERE pf.trade_date IN ({ph})"
            f" GROUP BY pf.symbol HAVING net_total != 0",
            dates,
        ).fetchall()
        symbols = [r["symbol"] for r in agg_rows]
        cap_map: dict = {}
        if symbols:
            sph = ",".join("?" for _ in symbols)
            cap_rows = conn.execute(
                f"SELECT symbol, MAX(trade_date) AS d FROM daily_snapshot"
                f" WHERE symbol IN ({sph}) GROUP BY symbol",
                symbols,
            ).fetchall()
            for cr in cap_rows:
                cap = conn.execute(
                    "SELECT market_cap FROM daily_snapshot WHERE symbol=? AND trade_date=?",
                    (cr["symbol"], cr["d"]),
                ).fetchone()
                if cap and cap["market_cap"]:
                    cap_map[cr["symbol"]] = int(cap["market_cap"]) * 100_000_000
        conn.close()

        period = ""
        if dates:
            d0, d1 = dates[-1], dates[0]
            period = (f"{d0[:4]}-{d0[4:6]}-{d0[6:]} ~ {d1[:4]}-{d1[4:6]}-{d1[6:]}")

        out = []
        for r in agg_rows:
            cap = cap_map.get(r["symbol"], 0)
            pct = round(r["net_total"] * 100.0 / cap, 4) if cap > 0 else None
            out.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "market": r["market"],
                "net_won": r["net_total"],
                "net_eok": round(r["net_total"] / 100_000_000, 2),
                "cap_won": cap,
                "cap_pct": pct,
            })
        # 매수/매도 분리 정렬 후 재합산
        buy = sorted([e for e in out if e["net_won"] > 0],
                     key=lambda x: (-(x["cap_pct"] or 0) if x["cap_won"] else 0, -x["net_won"]))[:50]
        sell = sorted([e for e in out if e["net_won"] < 0],
                      key=lambda x: ((x["cap_pct"] or 0) if x["cap_won"] else 0, x["net_won"]))[:50]
        return {"period": period, "buy_top": buy, "sell_top": sell}
    except Exception as exc:
        return {"error": str(exc)}


def _whale_insider() -> list:
    """임원·5%↑ 주주 최근 90일 매매 — stock_master JOIN."""
    try:
        conn = _open_whale_db()
        cutoff = (datetime.now(KST) - timedelta(days=90)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT it.rcept_dt, it.symbol, sm.name AS company_name,"
            " it.repror, it.ofcps, it.main_shrholdr,"
            " it.stock_irds_cnt, it.stock_rate, it.stock_irds_rate"
            " FROM insider_transactions it"
            " LEFT JOIN stock_master sm ON sm.symbol = it.symbol"
            " WHERE it.rcept_dt >= ? AND it.stock_irds_cnt != 0 AND it.stock_rate >= 5"
            " ORDER BY it.rcept_dt DESC, ABS(it.stock_irds_rate) DESC",
            (cutoff,),
        ).fetchall()
        conn.close()
        out = []
        for r in rows:
            irds = r["stock_irds_cnt"] or 0
            role = (r["main_shrholdr"] or "") or (r["ofcps"] or "")
            out.append({
                "rcept_dt": r["rcept_dt"],
                "symbol": r["symbol"],
                "company_name": r["company_name"] or "",
                "repror": r["repror"] or "",
                "role": role,
                "irds_cnt": irds,
                "direction": "buy" if irds > 0 else "sell",
                "stock_rate": float(r["stock_rate"] or 0),
                "stock_irds_rate": float(r["stock_irds_rate"] or 0),
            })
        return out
    except Exception as exc:
        return [{"error": str(exc)}]


async def build_whale_payload(preset: str) -> dict | list:
    """preset ∈ home|kr_5pct|kr_full|us_13f|pension|insider — 구조화 데이터 반환."""
    loop = asyncio.get_event_loop()
    if preset == "home":
        return await loop.run_in_executor(None, _whale_home)
    elif preset == "kr_5pct":
        return await loop.run_in_executor(None, _whale_kr_5pct)
    elif preset == "kr_full":
        return await loop.run_in_executor(None, _whale_kr_full)
    elif preset == "us_13f":
        return await loop.run_in_executor(None, _whale_us_13f)
    elif preset == "pension":
        return await loop.run_in_executor(None, _whale_pension)
    elif preset == "insider":
        return await loop.run_in_executor(None, _whale_insider)
    else:
        return {"error": f"unknown preset: {preset}"}


_WHALE_PANEL_REMOVED = r"""  # (삭제됨 — 이 변수 사용 안 함)
    <!-- Whale 탭 -->
    <section x-show="activeTab==='whale'" x-cloak
             x-data="{
               wTab: 'pension',
               wCache: {},
               wData: null,
               wLoading: false,
               async wLoad(p) {
                 if (this.wCache[p]) { this.wData = this.wCache[p]; return; }
                 this.wLoading = true;
                 this.wData = null;
                 const d = await this.$root.__proto__.constructor.prototype.api
                   ? await this.$root.api('/api/whale?p=' + p)
                   : await (async path => {
                       try { const r = await fetch(path); return await r.json(); }
                       catch(e) { return {error: String(e)}; }
                     })('/api/whale?p=' + p);
                 this.wCache[p] = d;
                 this.wData = d;
                 this.wLoading = false;
                 this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
               },
               setWTab(p) {
                 this.wTab = p;
                 this.wLoad(p);
               }
             }"
             x-init="wLoad(wTab)">

      <!-- 서브탭 바 -->
      <div class="flex flex-wrap gap-2 mb-5">
        <template x-for="tab in [
          {key:'pension', label:'연기금 흐름'},
          {key:'kr_5pct', label:'KR 5%룰'},
          {key:'kr_full', label:'KR 풀포트'},
          {key:'us_13f',  label:'US 13F'},
          {key:'insider', label:'내부자'}
        ]" :key="tab.key">
          <button @click="setWTab(tab.key)"
                  :class="wTab===tab.key ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'"
                  class="px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                  x-text="tab.label">
          </button>
        </template>
      </div>

      <!-- 로딩 -->
      <template x-if="wLoading">
        <div class="text-slate-400 text-center py-20">로딩 중...</div>
      </template>

      <!-- ── 연기금 흐름 ── -->
      <template x-if="!wLoading && wTab==='pension' && wData">
        <div>
          <template x-if="wData.error">
            <div class="text-red-500 text-sm py-4" x-text="'오류: ' + wData.error"></div>
          </template>
          <template x-if="!wData.error">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="'기간: ' + (wData.period || '-') + ' | 5일 누적 순매매 · 시총% 정규화'"></p>

              <!-- 매수 -->
              <h3 class="text-sm font-semibold text-green-600 mb-2">매수 TOP 50</h3>
              <template x-if="!wData.buy_top || !wData.buy_top.length">
                <div class="text-slate-400 text-sm py-2">매수 없음</div>
              </template>
              <template x-if="wData.buy_top && wData.buy_top.length">
                <div class="overflow-x-auto mb-6">
                  <table class="w-full text-sm border-collapse">
                    <thead>
                      <tr class="text-xs text-slate-400 border-b border-slate-200">
                        <th class="text-left pb-2 font-medium">#</th>
                        <th class="text-left pb-2 font-medium">종목</th>
                        <th class="text-right pb-2 font-medium">순매수</th>
                        <th class="text-right pb-2 font-medium">시총%</th>
                      </tr>
                    </thead>
                    <tbody>
                      <template x-for="(e, idx) in wData.buy_top" :key="e.symbol + idx">
                        <tr class="border-b border-slate-100 hover:bg-slate-50">
                          <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                          <td class="py-1.5">
                            <span class="font-medium text-slate-800" x-text="e.name"></span>
                            <span class="text-xs text-slate-400 ml-1" x-text="e.symbol"></span>
                          </td>
                          <td class="py-1.5 text-right text-green-600 font-semibold"
                              x-text="(e.net_eok != null ? '+' + e.net_eok.toFixed(0) + '억' : '-')"></td>
                          <td class="py-1.5 text-right text-green-600"
                              x-text="(e.cap_pct != null ? '+' + e.cap_pct.toFixed(2) + '%' : '-')"></td>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
              </template>

              <!-- 매도 -->
              <h3 class="text-sm font-semibold text-red-600 mb-2">매도 TOP 50</h3>
              <template x-if="!wData.sell_top || !wData.sell_top.length">
                <div class="text-slate-400 text-sm py-2">매도 없음</div>
              </template>
              <template x-if="wData.sell_top && wData.sell_top.length">
                <div class="overflow-x-auto">
                  <table class="w-full text-sm border-collapse">
                    <thead>
                      <tr class="text-xs text-slate-400 border-b border-slate-200">
                        <th class="text-left pb-2 font-medium">#</th>
                        <th class="text-left pb-2 font-medium">종목</th>
                        <th class="text-right pb-2 font-medium">순매도</th>
                        <th class="text-right pb-2 font-medium">시총%</th>
                      </tr>
                    </thead>
                    <tbody>
                      <template x-for="(e, idx) in wData.sell_top" :key="e.symbol + idx">
                        <tr class="border-b border-slate-100 hover:bg-slate-50">
                          <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                          <td class="py-1.5">
                            <span class="font-medium text-slate-800" x-text="e.name"></span>
                            <span class="text-xs text-slate-400 ml-1" x-text="e.symbol"></span>
                          </td>
                          <td class="py-1.5 text-right text-red-600 font-semibold"
                              x-text="(e.net_eok != null ? e.net_eok.toFixed(0) + '억' : '-')"></td>
                          <td class="py-1.5 text-right text-red-600"
                              x-text="(e.cap_pct != null ? e.cap_pct.toFixed(2) + '%' : '-')"></td>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
              </template>
            </div>
          </template>
        </div>
      </template>

      <!-- ── KR 5%룰 ── -->
      <template x-if="!wLoading && wTab==='kr_5pct' && wData">
        <div>
          <template x-if="wData[0] && wData[0].error">
            <div class="text-red-500 text-sm" x-text="wData[0].error"></div>
          </template>
          <template x-if="wData.length && !(wData[0] && wData[0].error)">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="wData[0] ? wData[0].quarter + ' | 총 ' + wData.length + '건 | 10%↑ 빨강' : ''"></p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead>
                    <tr class="text-xs text-slate-400 border-b border-slate-200">
                      <th class="text-left pb-2 font-medium">#</th>
                      <th class="text-left pb-2 font-medium">보고일</th>
                      <th class="text-left pb-2 font-medium">종목</th>
                      <th class="text-right pb-2 font-medium">지분%</th>
                      <th class="text-right pb-2 font-medium">전분기</th>
                    </tr>
                  </thead>
                  <tbody>
                    <template x-for="(r, idx) in wData" :key="r.symbol + r.report_date + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5 text-xs text-slate-500" x-text="r.report_date"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="r.company_name"></span>
                          <span class="text-xs text-slate-400 ml-1" x-text="r.symbol"></span>
                        </td>
                        <td class="py-1.5 text-right"
                            :class="r.ratio_pct >= 10 ? 'text-red-600 font-bold' : 'text-slate-700'"
                            x-text="r.ratio_pct != null ? r.ratio_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-xs">
                          <template x-if="r.change_label === 'NEW'">
                            <span class="text-green-600 font-semibold">NEW</span>
                          </template>
                          <template x-if="r.change_label === 'UP' && r.change != null">
                            <span class="text-green-600">+<span x-text="r.change.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="r.change_label === 'DOWN' && r.change != null">
                            <span class="text-red-500"><span x-text="r.change.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="r.change_label === 'FLAT' || r.change_label === ''">
                            <span class="text-slate-400">—</span>
                          </template>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
          <template x-if="!wData.length">
            <div class="text-slate-400 text-sm py-4">데이터 없음</div>
          </template>
        </div>
      </template>

      <!-- ── KR 풀포트 ── -->
      <template x-if="!wLoading && wTab==='kr_full' && wData">
        <div>
          <template x-if="wData.error">
            <div class="text-red-500 text-sm py-4" x-text="'오류: ' + wData.error"></div>
          </template>
          <template x-if="!wData.error">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="(wData.quarter_label || '-') + ' | 스냅샷 ' + (wData.snapshot_date || '-') + ' | 총 ' + (wData.total_holdings || 0) + '종목 | 지분 10%↑ 빨강'"></p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead>
                    <tr class="text-xs text-slate-400 border-b border-slate-200">
                      <th class="text-left pb-2 font-medium">#</th>
                      <th class="text-left pb-2 font-medium">종목</th>
                      <th class="text-right pb-2 font-medium">비중%</th>
                      <th class="text-right pb-2 font-medium">평가액</th>
                      <th class="text-right pb-2 font-medium">지분%</th>
                      <th class="text-right pb-2 font-medium">전년대비</th>
                    </tr>
                  </thead>
                  <tbody>
                    <template x-for="(x, idx) in (wData.rows || [])" :key="(x.symbol || x.name) + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="x.name"></span>
                          <span class="text-xs text-slate-400 ml-1" x-text="x.symbol"></span>
                        </td>
                        <td class="py-1.5 text-right text-slate-700"
                            x-text="x.weight_pct != null ? x.weight_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-slate-600 text-xs"
                            x-text="x.valuation_eok != null ? x.valuation_eok.toLocaleString('ko-KR') + '억' : '-'"></td>
                        <td class="py-1.5 text-right"
                            :class="x.share_curr_pct >= 10 ? 'text-red-600 font-bold' : 'text-slate-600'"
                            x-text="x.share_curr_pct != null ? x.share_curr_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-xs">
                          <template x-if="x.data_missing || x.share_change_p == null">
                            <span class="text-slate-400">—</span>
                          </template>
                          <template x-if="!x.data_missing && x.share_change_p != null && x.share_change_p > 0.05">
                            <span class="text-green-600">+<span x-text="x.share_change_p.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="!x.data_missing && x.share_change_p != null && x.share_change_p < -0.05">
                            <span class="text-red-500"><span x-text="x.share_change_p.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="!x.data_missing && x.share_change_p != null && x.share_change_p >= -0.05 && x.share_change_p <= 0.05">
                            <span class="text-slate-400">—</span>
                          </template>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- ── US 13F ── -->
      <template x-if="!wLoading && wTab==='us_13f' && wData">
        <div>
          <template x-if="wData.error">
            <div class="text-red-500 text-sm py-4" x-text="'오류: ' + wData.error"></div>
          </template>
          <template x-if="!wData.error">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="(wData.quarter || '-') + ' | 분기말 ' + (wData.period_end || '-') + ' | TOP 100 / ' + (wData.total_holdings || 0) + '종목'"></p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead>
                    <tr class="text-xs text-slate-400 border-b border-slate-200">
                      <th class="text-left pb-2 font-medium">#</th>
                      <th class="text-left pb-2 font-medium">종목</th>
                      <th class="text-right pb-2 font-medium">가치</th>
                      <th class="text-right pb-2 font-medium">비중%</th>
                      <th class="text-right pb-2 font-medium">주식변화</th>
                    </tr>
                  </thead>
                  <tbody>
                    <template x-for="(x, idx) in (wData.rows || [])" :key="(x.cusip || x.name_of_issuer) + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="x.name_of_issuer || '-'"></span>
                          <template x-if="x.ticker">
                            <span class="text-xs text-slate-400 ml-1" x-text="x.ticker"></span>
                          </template>
                        </td>
                        <td class="py-1.5 text-right text-slate-700 text-xs"
                            x-text="x.value_usd != null ? (x.value_usd >= 1e9 ? '$' + (x.value_usd/1e9).toFixed(2) + 'B' : '$' + (x.value_usd/1e6).toFixed(0) + 'M') : '-'"></td>
                        <td class="py-1.5 text-right text-slate-600 text-xs"
                            x-text="x.weight_pct != null ? x.weight_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-xs">
                          <template x-if="x.status === 'NEW'">
                            <span class="text-green-600 font-semibold">NEW</span>
                          </template>
                          <template x-if="x.status === 'UP' && x.share_change_pct != null">
                            <span class="text-green-600">+<span x-text="x.share_change_pct.toFixed(1)"></span>%</span>
                          </template>
                          <template x-if="x.status === 'DOWN' && x.share_change_pct != null">
                            <span class="text-red-500"><span x-text="x.share_change_pct.toFixed(1)"></span>%</span>
                          </template>
                          <template x-if="!x.status || (x.status !== 'NEW' && x.status !== 'UP' && x.status !== 'DOWN')">
                            <span class="text-slate-400">—</span>
                          </template>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- ── 내부자 ── -->
      <template x-if="!wLoading && wTab==='insider' && wData">
        <div>
          <template x-if="wData[0] && wData[0].error">
            <div class="text-red-500 text-sm py-4" x-text="wData[0].error"></div>
          </template>
          <template x-if="wData.length && !(wData[0] && wData[0].error)">
            <div>
              <p class="text-xs text-slate-400 mb-4">
                최근 90일 | 5%↑ 주요주주·임원 | <span x-text="wData.length + '건'"></span> | 10%↑ 빨강
              </p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead>
                    <tr class="text-xs text-slate-400 border-b border-slate-200">
                      <th class="text-left pb-2 font-medium">#</th>
                      <th class="text-left pb-2 font-medium">보고일</th>
                      <th class="text-left pb-2 font-medium">종목</th>
                      <th class="text-left pb-2 font-medium">보고자</th>
                      <th class="text-right pb-2 font-medium">증감</th>
                      <th class="text-right pb-2 font-medium">지분%</th>
                    </tr>
                  </thead>
                  <tbody>
                    <template x-for="(r, idx) in wData" :key="(r.rcept_dt || '') + (r.symbol || '') + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5 text-xs text-slate-500" x-text="r.rcept_dt"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="r.company_name || ''"></span>
                          <span class="text-xs text-slate-400 ml-1" x-text="r.symbol"></span>
                        </td>
                        <td class="py-1.5 text-xs">
                          <span class="text-slate-700" x-text="r.repror"></span>
                          <span class="text-slate-400 ml-1" x-text="r.role"></span>
                        </td>
                        <td class="py-1.5 text-right font-semibold"
                            :class="r.direction === 'buy' ? 'text-green-600' : 'text-red-500'"
                            x-text="r.irds_cnt != null ? (r.irds_cnt > 0 ? '+' : '') + r.irds_cnt.toLocaleString('ko-KR') : '-'"></td>
                        <td class="py-1.5 text-right text-xs"
                            :class="r.stock_rate >= 10 ? 'text-red-600 font-bold' : 'text-slate-600'"
                            x-text="r.stock_rate != null ? r.stock_rate.toFixed(2) + '%' : '-'"></td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
          <template x-if="!wData.length">
            <div class="text-slate-400 text-sm py-4">최근 90일 5%↑ 보유자 매매 없음</div>
          </template>
        </div>
      </template>

    </section>
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _handle_home(request: web.Request) -> web.Response:
    return web.Response(text=_HOME_SHELL, content_type="text/html")


async def _handle_api_regime(request: web.Request) -> web.Response:
    return await _api(execute_tool("get_regime", {"mode": "current"}))


async def _handle_api_alerts(request: web.Request) -> web.Response:
    # TTL 240s: /api/watch가 stoploss_alerts를 직접 포함하므로 이 엔드포인트는 brief 요약용
    return await _api(_cached("alerts", 240.0, lambda: execute_tool("get_alerts", {"brief": True})))


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


async def _handle_api_portfolio(request: web.Request) -> web.Response:
    # W2: 람다 팩토리 — 캐시 hit 시 코루틴 미생성으로 RuntimeWarning 방지
    # TTL 240s: 장 마감 후 가격 변동 작고 글랜스 대시보드라 4분 staleness 무방
    return await _api(_cached("portfolio", 240.0, _build_portfolio_with_grand))


async def _handle_api_home(request: web.Request) -> web.Response:
    # W2: 람다 팩토리 — 캐시 hit 시 코루틴 미생성으로 RuntimeWarning 방지
    # TTL 240s: 프론트 자동갱신 60초 유지, 대부분 캐시 히트 → 4분마다 1회 콜드
    return await _api(_cached("home", 240.0, lambda: build_home_payload()))


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


async def _handle_api_watch_get(request: web.Request) -> web.Response:
    # TTL 240s: home/portfolio와 동일 (4분 staleness 무방)
    return await _api(_cached("watch", 240.0, _build_watch_payload))


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


async def _handle_api_stock_detail(request: web.Request) -> web.Response:
    ticker = request.match_info.get("ticker", "").strip().upper()
    if not ticker:
        return web.json_response({"error": "ticker required"}, status=200)

    is_us = _is_us_ticker_simple(ticker)

    async def _fetch():
        raw = await execute_tool("get_stock_detail", {"ticker": ticker})
        if _tool_err(raw):
            return raw

        # 캔들 + 컨센서스 히스토리 (KR만, US는 빈 배열)
        loop = asyncio.get_event_loop()
        if is_us:
            candles = []
            consensus_history = []
        else:
            candles, consensus_history = await asyncio.gather(
                loop.run_in_executor(None, _fetch_candles_sync, ticker),
                loop.run_in_executor(None, _fetch_consensus_history_sync, ticker),
            )

        return {
            "ticker": ticker,
            "name": raw.get("name") or raw.get("hts_kor_isnm") or ticker,
            "market": raw.get("market", "US" if is_us else "KR"),
            "cur_price": raw.get("cur_price") or raw.get("stck_prpr"),
            "chg_rate": raw.get("chg_rate") or raw.get("prdy_ctrt"),
            "per": raw.get("per"),
            "pbr": raw.get("pbr"),
            "foreign_net": raw.get("foreign_net") or raw.get("frgnr_ntby_qty"),
            "inst_net": raw.get("inst_net") or raw.get("orgn_ntby_qty"),
            "candles": candles,
            "consensus_history": consensus_history,
        }

    return await _api(_cached(f"stock_{ticker}", 60.0, _fetch))


async def _handle_api_watch_post(request: web.Request) -> web.Response:
    """POST /api/watch — action에 따라 manage_watch 또는 set_alert 호출.

    body 예시:
        {"action":"add","ticker":"NVDA","name":"NVIDIA"}
        {"action":"remove","ticker":"NVDA","alert_type":"watchlist"}
        {"action":"set_alert","log_type":"watch","ticker":"005930","name":"삼성전자","buy_price":60000}
    에러는 200 + {"error":"..."} 로 반환 (Alpine이 d.error로 감지).
    실제 상태 변경 발생 — 읽기 전용 경로 아님.
    캐시 무효화: watch/alerts 60s TTL 캐시 제거 (다음 fetch가 fresh 호출).
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=200)
    action = body.get("action", "").strip().lower()
    if action in ("add", "remove"):
        result = await execute_tool("manage_watch", body)
    elif action == "set_alert":
        result = await execute_tool("set_alert", body)
    else:
        return web.json_response({"error": f"unknown action: {action}"}, status=200)
    # 캐시 무효화 (watch + alerts — 다음 GET이 fresh 데이터 반환)
    _cache.pop("watch", None)
    _cache.pop("alerts", None)
    if _tool_err(result):
        return web.json_response(result, status=200)
    return web.json_response(result)


async def _handle_api_portfolio_history(request: web.Request) -> web.Response:
    """GET /api/portfolio_history — 자산 추이 스냅샷 (300s TTL)."""

    def _load_sync():
        try:
            raw = load_json(PORTFOLIO_HISTORY_FILE, default=[])
            if isinstance(raw, list):
                snaps = raw
            elif isinstance(raw, dict):
                snaps = raw.get("snapshots", [])
            else:
                snaps = []
            result = []
            for s in snaps:
                if not isinstance(s, dict):
                    continue
                d = s.get("date", "")
                v = s.get("total_asset_krw") or s.get("total_eval_krw")
                if d and v:
                    result.append({"date": d, "total_asset_krw": float(v)})
            result.sort(key=lambda x: x["date"])
            return {"snapshots": result, "count": len(result)}
        except Exception as e:
            return {"snapshots": [], "count": 0, "_error": str(e)}

    async def _factory():
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _load_sync)

    return await _api(_cached("portfolio_history", 300.0, _factory))


async def _handle_api_whale(request: web.Request) -> web.Response:
    """GET /api/whale?p=<preset> — 240s TTL 캐시."""
    preset = request.rel_url.query.get("p", "home").strip()
    valid = {"home", "kr_5pct", "kr_full", "us_13f", "pension", "insider"}
    if preset not in valid:
        return web.json_response({"error": f"unknown preset: {preset}"}, status=400)
    return await _api(_cached(f"whale_{preset}", 240.0, lambda: build_whale_payload(preset)))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P3b: 리포트·기록 탭 API 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _handle_api_reports(request: web.Request) -> web.Response:
    """GET /api/reports — 4세그먼트 집계 (240s TTL)."""
    return await _api(_cached("reports", 240.0, lambda: build_reports_payload()))


async def _handle_api_reports_ticker(request: web.Request) -> web.Response:
    """GET /api/reports/{ticker} — 종목별 리포트 목록 (60s TTL)."""
    ticker = request.match_info.get("ticker", "").strip().upper()
    if not ticker:
        return web.json_response({"error": "ticker required"}, status=200)
    return await _api(_cached(f"reports_{ticker}", 60.0, lambda: _reports_by_ticker(ticker)))


async def _build_decisions_payload() -> dict:
    """decision_log.json → 날짜 내림차순 목록."""
    log = load_decision_log()
    items = sorted(log.values(), key=lambda x: x.get("date", ""), reverse=True)
    return {"items": items}


async def _handle_api_decisions_get(request: web.Request) -> web.Response:
    """GET /api/decisions — 투자판단 목록 (120s TTL)."""
    return await _api(_cached("decisions", 120.0, _build_decisions_payload))


async def _handle_api_decisions_post(request: web.Request) -> web.Response:
    """POST /api/decisions — 투자판단 저장 (set_alert log_type=decision 위임).

    body: {date?, regime, notes?}
    set_alert decision 인자: log_type, date, regime, notes
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=200)
    args = {
        "log_type": "decision",
        "date": body.get("date", "").strip() or datetime.now(KST).strftime("%Y-%m-%d"),
        "regime": body.get("regime", "").strip(),
        "notes": body.get("notes", body.get("memo", "")).strip(),
    }
    if not args["regime"]:
        return web.json_response({"error": "regime 필드가 필요합니다"}, status=200)
    result = await execute_tool("set_alert", args)
    # 캐시 무효화
    _cache.pop("decisions", None)
    if _tool_err(result):
        return web.json_response(result, status=200)
    return web.json_response(result)


async def _build_trades_payload() -> dict:
    """get_trade_stats 동기 함수 → 비동기 래퍼."""
    return get_trade_stats("all")


async def _handle_api_trades(request: web.Request) -> web.Response:
    """GET /api/trades — 매매 성과 (240s TTL)."""
    return await _api(_cached("trades", 240.0, _build_trades_payload))


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


async def _handle_api_invest_todo(request: web.Request) -> web.Response:
    """GET /api/invest_todo — TODO_invest.md 텍스트 (120s TTL)."""
    return await _api(_cached("invest_todo", 120.0, _build_invest_todo))


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


async def _handle_api_signals(request: web.Request) -> web.Response:
    """GET /api/signals — 시그널 피드 (30s TTL, 실시간성 우선)."""
    return await _api(_cached("signals", 240.0, _build_signals_payload))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시세 탭 API 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _handle_api_market(request: web.Request) -> web.Response:
    """GET /api/market — 시세 집계 (240s TTL).

    build_market_payload() 결과: indices/movers_kr_up/down/movers_us_up/down/volume_top
    """
    return await _api(_cached("market", 240.0, build_market_payload))


async def _handle_api_sector_heatmap(request: web.Request) -> web.Response:
    """GET /api/sector_heatmap — KR 섹터별 평균 등락률 (300s TTL, SWR)."""
    return await _api(_cached("sector_heatmap", 300.0, _build_sector_heatmap_payload))


async def _handle_api_marketmap(request: web.Request) -> web.Response:
    """GET /api/marketmap?market=kospi|kosdaq — ECharts 트리맵용 마켓맵 (3600s TTL, SWR)."""
    market = request.rel_url.query.get("market", "kospi").lower()
    if market not in ("kospi", "kosdaq"):
        market = "kospi"
    key = f"marketmap:{market}"
    return await _api(_cached(key, 3600.0, lambda: _build_marketmap_payload(market)))


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


async def _handle_api_macro_panel(request: web.Request) -> web.Response:
    """GET /api/macro_panel — 매크로 패널 집계 (600s TTL, SWR)."""
    return await _api(_cached("macro_panel", 600.0, build_macro_panel_payload))


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


async def _handle_api_us_candidates(request: web.Request) -> web.Response:
    """GET /api/us/candidates — 매수후보 (600s TTL, SWR)."""
    return await _api(_cached("us_candidates", 600.0, _build_us_candidates_payload))


async def _handle_api_us_scan(request: web.Request) -> web.Response:
    """GET /api/us/scan — 워치/보유 레이팅 변화 (300s TTL, SWR)."""
    return await _api(_cached("us_scan", 300.0, _build_us_scan_payload))


async def _handle_api_us_analysts(request: web.Request) -> web.Response:
    """GET /api/us/analysts — 톱애널 리스트 (600s TTL, SWR)."""
    return await _api(_cached("us_analysts", 600.0, _build_us_analysts_payload))


async def _handle_api_us_ratings(request: web.Request) -> web.Response:
    """GET /api/us/ratings?ticker=NVDA — 종목별 레이팅 이벤트 (온디맨드, 캐시 없음)."""
    ticker = request.rel_url.query.get("ticker", "").strip().upper()
    if not ticker:
        return web.json_response({"error": "ticker 파라미터 필요"})
    return await _api(execute_tool("get_us_ratings", {"ticker": ticker, "mode": "events", "days": 180}))


async def _handle_api_us_consensus(request: web.Request) -> web.Response:
    """GET /api/us/consensus?ticker=NVDA — 종목 컨센서스 (60s TTL)."""
    ticker = request.rel_url.query.get("ticker", "").strip().upper()
    if not ticker:
        return web.json_response({"error": "ticker 파라미터 필요"})
    return await _api(_cached(
        f"us_consensus_{ticker}", 60.0,
        lambda: execute_tool("get_us_ratings", {"ticker": ticker, "mode": "consensus"})
    ))


async def _handle_api_us_analyst_research(request: web.Request) -> web.Response:
    """GET /api/us/analyst_research?ticker=NVDA — FMP TP/grades (온디맨드, 에러 허용)."""
    ticker = request.rel_url.query.get("ticker", "").strip().upper()
    if not ticker:
        return web.json_response({"error": "ticker 파라미터 필요"})
    return await _api(execute_tool("get_us_analyst_research", {"ticker": ticker}))


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


async def _handle_api_alpha(request: web.Request) -> web.Response:
    """GET /api/alpha?preset=change|fscore|mscore|fcf|high52|low52."""
    preset = request.rel_url.query.get("preset", "change").strip().lower()
    valid = {"change", "fscore", "mscore", "fcf", "high52", "low52"}
    if preset not in valid:
        preset = "change"
    # change: 600s TTL, 나머지: 300s
    ttl = 600.0 if preset == "change" else 300.0
    cache_key = f"alpha_{preset}"
    return await _api(_cached(cache_key, ttl, lambda: _build_alpha_payload(preset)))


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


async def _handle_api_supply(request: web.Request) -> web.Response:
    """GET /api/supply?mode=foreign_rank|combined_rank|short_sale|credit|lending&ticker=XXX."""
    mode = request.rel_url.query.get("mode", "foreign_rank").strip().lower()
    valid = {"foreign_rank", "combined_rank", "short_sale", "credit", "lending"}
    if mode not in valid:
        mode = "foreign_rank"
    ticker = request.rel_url.query.get("ticker", "").strip().upper()
    # foreign_rank/combined_rank: 300~600s TTL, SWR
    # short_sale/credit/lending: 종목별 캐시 180s
    if mode in ("foreign_rank", "combined_rank"):
        ttl = 300.0
        cache_key = f"supply_{mode}"
        return await _api(_cached(cache_key, ttl, lambda: _build_supply_payload(mode)))
    else:
        ttl = 180.0
        cache_key = f"supply_{mode}_{ticker or 'default'}"
        return await _api(_cached(cache_key, ttl,
                                   lambda: _build_supply_payload(mode, ticker)))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 라우트 등록
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def register_home_routes(app: web.Application) -> None:
    app.router.add_get("/home", _handle_home)
    app.router.add_get("/api/regime", _handle_api_regime)
    app.router.add_get("/api/alerts", _handle_api_alerts)
    app.router.add_get("/api/portfolio", _handle_api_portfolio)
    app.router.add_get("/api/home", _handle_api_home)
    # P2 추가
    app.router.add_get("/api/watch", _handle_api_watch_get)
    app.router.add_get("/api/stock/{ticker}", _handle_api_stock_detail)
    app.router.add_post("/api/watch", _handle_api_watch_post)
    # 차트 Pass 1 추가
    app.router.add_get("/api/portfolio_history", _handle_api_portfolio_history)
    # P3a 추가
    app.router.add_get("/api/whale", _handle_api_whale)
    # P3b 추가
    app.router.add_get("/api/reports", _handle_api_reports)
    app.router.add_get("/api/reports/{ticker}", _handle_api_reports_ticker)
    app.router.add_get("/api/decisions", _handle_api_decisions_get)
    app.router.add_post("/api/decisions", _handle_api_decisions_post)
    app.router.add_get("/api/trades", _handle_api_trades)
    app.router.add_get("/api/invest_todo", _handle_api_invest_todo)
    # P4 추가
    app.router.add_get("/api/signals", _handle_api_signals)
    # 시세 탭 추가
    app.router.add_get("/api/market", _handle_api_market)
    # 히트맵 추가
    app.router.add_get("/api/sector_heatmap", _handle_api_sector_heatmap)
    app.router.add_get("/api/marketmap", _handle_api_marketmap)
    # 매크로 패널 추가
    app.router.add_get("/api/macro_panel", _handle_api_macro_panel)
    # 알파스크리너 + 수급 추가
    app.router.add_get("/api/alpha", _handle_api_alpha)
    app.router.add_get("/api/supply", _handle_api_supply)
    # US 애널리스트 탭 추가
    app.router.add_get("/api/us/candidates", _handle_api_us_candidates)
    app.router.add_get("/api/us/scan", _handle_api_us_scan)
    app.router.add_get("/api/us/analysts", _handle_api_us_analysts)
    app.router.add_get("/api/us/ratings", _handle_api_us_ratings)
    app.router.add_get("/api/us/consensus", _handle_api_us_consensus)
    app.router.add_get("/api/us/analyst_research", _handle_api_us_analyst_research)
