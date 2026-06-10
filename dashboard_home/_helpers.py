"""dashboard_home/_helpers.py — 캐시 인프라 + DB 연결 헬퍼 (P3 박리).

TTL/SWR 캐시(_cache, _cached), 에러 검사(_tool_err), API 래퍼(_api),
DB 연결 헬퍼(_open_db). 순환 방지: 다른 dashboard_home 서브모듈 import 금지.
"""

import time
import asyncio
import sqlite3 as _sqlite3

from aiohttp import web

from kis_api import _DATA_DIR

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

