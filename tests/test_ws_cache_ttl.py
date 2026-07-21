"""WS 가격 캐시 TTL 테스트 (kis_api/websocket.py).

배경: KisRealtimeManager._price_cache 는 원래 TTL 없이(dict.get) 값을 무기한 서빙 —
WS 재연결로 틱이 끊기면 스테일가가 stoploss/telegram/MCP portfolio 소비자에게
그대로 노출됨(실사건: XNDU 10거래일 동결). _price_cache 를 (price, ts) 튜플로 바꾸고
get_cached_price 가 TTL(_PRICE_CACHE_TTL_SEC=180초) 초과 시 None 을 반환하도록 하여,
기존 `if cached is not None: 사용 / else: REST·Yahoo fallback` 소비자 패턴이
자동으로 신선 조회로 빠지게 한다.
"""
import time

from kis_api.websocket import KisRealtimeManager, _PRICE_CACHE_TTL_SEC


def _mgr() -> KisRealtimeManager:
    return KisRealtimeManager()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. set_cached_price → get_cached_price 즉시 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_set_then_get_returns_price_immediately():
    mgr = _mgr()
    mgr.set_cached_price("005930", 75000)
    assert mgr.get_cached_price("005930") == 75000


def test_set_cached_price_stores_tuple_with_timestamp():
    mgr = _mgr()
    mgr.set_cached_price("005930", 75000)
    entry = mgr._price_cache["005930"]
    assert isinstance(entry, tuple)
    price, ts = entry
    assert price == 75000
    assert isinstance(ts, float)
    assert abs(time.time() - ts) < 5


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. TTL 이내 — 신선 캐시 반환
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_get_within_ttl_returns_price():
    mgr = _mgr()
    mgr._price_cache["005930"] = (75000, time.time() - 10)
    assert mgr.get_cached_price("005930") == 75000


def test_get_just_under_ttl_boundary_returns_price():
    mgr = _mgr()
    mgr._price_cache["005930"] = (75000, time.time() - (_PRICE_CACHE_TTL_SEC - 1))
    assert mgr.get_cached_price("005930") == 75000


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. TTL 초과 — None 반환 (소비자 fallback 유도)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_get_ttl_exceeded_returns_none():
    mgr = _mgr()
    mgr._price_cache["XNDU"] = (11.75, time.time() - 200)
    assert mgr.get_cached_price("XNDU") is None


def test_get_far_stale_returns_none():
    """10거래일치(수일) 동결 시뮬 — 실사건 재현."""
    mgr = _mgr()
    ten_days_sec = 10 * 24 * 3600
    mgr._price_cache["XNDU"] = (11.75, time.time() - ten_days_sec)
    assert mgr.get_cached_price("XNDU") is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. 틱 경로(_on_text) 시뮬 — TTL 동일 적용
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_on_text_kr_tick_writes_fresh_cache_and_is_retrievable():
    import asyncio

    mgr = _mgr()
    # H0UNCNT0 포맷: "0|TR_ID|COUNT|필드1^필드2^..." KR: ticker=f[0], price=f[2]
    raw = "0|H0UNCNT0|001|005930^123456^76000"
    asyncio.run(mgr._on_text(raw))
    assert mgr.get_cached_price("005930") == 76000


def test_on_text_written_tick_expires_after_ttl():
    import asyncio

    mgr = _mgr()
    raw = "0|H0UNCNT0|001|005930^123456^76000"
    asyncio.run(mgr._on_text(raw))
    # 저장된 타임스탬프를 과거로 조작해 TTL 초과 시뮬레이트
    price, _ts = mgr._price_cache["005930"]
    mgr._price_cache["005930"] = (price, time.time() - (_PRICE_CACHE_TTL_SEC + 1))
    assert mgr.get_cached_price("005930") is None


def test_on_text_us_tick_writes_fresh_cache_and_is_retrievable():
    import asyncio

    mgr = _mgr()
    # HDFSCNT0 포맷: SYMB=f[0], LAST=f[10] (최소 11필드)
    fields = ["XNDU"] + ["x"] * 9 + ["11.75"]
    raw = "0|HDFSCNT0|001|" + "^".join(fields)
    asyncio.run(mgr._on_text(raw))
    assert mgr.get_cached_price("XNDU") == 11.75


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. 캐시 미존재 / 방어적 케이스
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_get_missing_ticker_returns_none():
    mgr = _mgr()
    assert mgr.get_cached_price("000000") is None


def test_get_non_tuple_legacy_value_returns_none():
    """구 bare 값(비-튜플)이 우연히 남아있어도 방어적으로 None."""
    mgr = _mgr()
    mgr._price_cache["005930"] = 75000  # 구 포맷 (마이그레이션 전 상태 시뮬)
    assert mgr.get_cached_price("005930") is None


def test_set_cached_price_ignores_zero_or_negative():
    mgr = _mgr()
    mgr.set_cached_price("005930", 0)
    mgr.set_cached_price("005930", -1)
    assert "005930" not in mgr._price_cache
    assert mgr.get_cached_price("005930") is None
