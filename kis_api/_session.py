"""공유 aiohttp 세션 + KIS 토큰 캐시 + _kis_get 래퍼."""
import os
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta

from ._config import (
    KIS_BASE_URL, KIS_APP_KEY, KIS_APP_SECRET, TOKEN_CACHE_FILE,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 공유 aiohttp 세션 (TCP 연결 풀 재사용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_shared_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    """공유 aiohttp 세션 반환. 없거나 닫혔으면 새로 생성."""
    global _shared_session
    if _shared_session is None or _shared_session.closed:
        connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=30)
        _shared_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _shared_session


async def close_session():
    """서버 종료 시 세션 정리."""
    global _shared_session
    if _shared_session and not _shared_session.closed:
        await _shared_session.close()
        _shared_session = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KIS 토큰 캐시 (23h)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_token_cache = {"token": None, "expires": None}


async def get_kis_token():
    now = datetime.now()
    # 1) 메모리 캐시 확인
    if _token_cache["token"] and _token_cache["expires"] and _token_cache["expires"] > now:
        return _token_cache["token"]
    # 2) 파일 캐시 확인 (재시작 후에도 23시간 재사용)
    try:
        if os.path.exists(TOKEN_CACHE_FILE):
            with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            exp = datetime.fromisoformat(cached.get("expires", "2000-01-01"))
            if cached.get("token") and exp > now:
                _token_cache["token"] = cached["token"]
                _token_cache["expires"] = exp
                return cached["token"]
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"[session] 토큰 파일 캐시 읽기 실패 (신규 발급 진행): {e}")
    # 3) 신규 발급
    url = f"{KIS_BASE_URL}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET}
    session = _get_session()
    async with session.post(url, headers={"content-type": "application/json"}, json=body) as resp:
        data = await resp.json()
        token = data.get("access_token")
        if token:
            expires = now + timedelta(hours=23)
            _token_cache["token"] = token
            _token_cache["expires"] = expires
            try:
                os.makedirs(os.path.dirname(TOKEN_CACHE_FILE) or ".", exist_ok=True)
                with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"token": token, "expires": expires.isoformat()}, f)
            except OSError as e:
                print(f"[session] 토큰 파일 캐시 저장 실패 (무시): {e}")
        return token


def _kis_headers(token, tr_id):
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
    }


async def _kis_get(session, path, tr_id, token, params):
    """KIS API GET 호출 (429/5xx 자동 재시도, 공유 세션 fallback)."""
    s = session if session and not getattr(session, 'closed', False) else _get_session()
    url = f"{KIS_BASE_URL}{path}"
    headers = _kis_headers(token, tr_id)
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        async with s.get(url, headers=headers, params=params) as r:
            if r.status == 429 and attempt < max_retries:
                print(f"[RETRY] {path} → 429, attempt {attempt}/{max_retries}")
                await asyncio.sleep(1.0 * attempt)
                continue
            if r.status in (500, 502, 503) and attempt < max_retries:
                print(f"[RETRY] {path} → {r.status}, attempt {attempt}/{max_retries}")
                await asyncio.sleep(2.0)
                continue
            data = await r.json(content_type=None)
            return r.status, data
    return 500, {}
