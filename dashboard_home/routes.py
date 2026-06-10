"""dashboard_home/routes.py — HTTP 핸들러 + 라우트 등록 (P3 박리).

warm_caches, _handle_* 핸들러 전체, register_home_routes.
"""

import time
import asyncio
import json
from datetime import datetime

from aiohttp import web

from kis_api import (
    load_json,
    load_stoploss,
    load_watchalert,
    load_dart_seen,
    load_decision_log,
    get_yahoo_quote,
    load_signal_feed,
    PORTFOLIO_HISTORY_FILE,
    _DATA_DIR,
    KST,
)
from mcp_tools import execute_tool

from ._helpers import _cache, _cached, _tool_err, _api
from ._assets import _HOME_SHELL
from .payloads import (
    build_home_payload,
    build_market_payload,
    build_macro_panel_payload,
    _build_portfolio_with_grand,
    _build_watch_payload,
    _build_signals_payload,
    _build_supply_payload,
    _build_alpha_payload,
    _build_us_candidates_payload,
    _build_us_scan_payload,
    _build_us_analysts_payload,
    _build_decisions_payload,
    _build_trades_payload,
    _build_invest_todo,
    _fetch_candles_sync,
    _fetch_consensus_history_sync,
    _is_us_ticker_simple,
    _build_sector_heatmap_payload,
    _build_marketmap_payload,
)
from .reports import build_reports_payload, _reports_by_ticker
from .whale import build_whale_payload


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
# API 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _handle_home(request: web.Request) -> web.Response:
    return web.Response(text=_HOME_SHELL, content_type="text/html")


async def _handle_api_regime(request: web.Request) -> web.Response:
    return await _api(execute_tool("get_regime", {"mode": "current"}))


async def _handle_api_alerts(request: web.Request) -> web.Response:
    # TTL 240s: /api/watch가 stoploss_alerts를 직접 포함하므로 이 엔드포인트는 brief 요약용
    return await _api(_cached("alerts", 240.0, lambda: execute_tool("get_alerts", {"brief": True})))

async def _handle_api_portfolio(request: web.Request) -> web.Response:
    # W2: 람다 팩토리 — 캐시 hit 시 코루틴 미생성으로 RuntimeWarning 방지
    # TTL 240s: 장 마감 후 가격 변동 작고 글랜스 대시보드라 4분 staleness 무방
    return await _api(_cached("portfolio", 240.0, _build_portfolio_with_grand))


async def _handle_api_home(request: web.Request) -> web.Response:
    # W2: 람다 팩토리 — 캐시 hit 시 코루틴 미생성으로 RuntimeWarning 방지
    # TTL 240s: 프론트 자동갱신 60초 유지, 대부분 캐시 히트 → 4분마다 1회 콜드
    return await _api(_cached("home", 240.0, lambda: build_home_payload()))

async def _handle_api_watch_get(request: web.Request) -> web.Response:
    # TTL 240s: home/portfolio와 동일 (4분 staleness 무방)
    return await _api(_cached("watch", 240.0, _build_watch_payload))

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
        loop = asyncio.get_running_loop()
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
        loop = asyncio.get_running_loop()
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

async def _handle_api_trades(request: web.Request) -> web.Response:
    """GET /api/trades — 매매 성과 (240s TTL)."""
    return await _api(_cached("trades", 240.0, _build_trades_payload))

async def _handle_api_invest_todo(request: web.Request) -> web.Response:
    """GET /api/invest_todo — TODO_invest.md 텍스트 (120s TTL)."""
    return await _api(_cached("invest_todo", 120.0, _build_invest_todo))

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

async def _handle_api_macro_panel(request: web.Request) -> web.Response:
    """GET /api/macro_panel — 매크로 패널 집계 (600s TTL, SWR)."""
    return await _api(_cached("macro_panel", 600.0, build_macro_panel_payload))

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

