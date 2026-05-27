"""Gist backup 회귀 방지 mock test.

5/28 If-Match 400 사고 회귀 차단:
- _gist_patch_with_retry: If-Match 헤더 부재 / 200 OK / 409 retry / 429 Retry-After
- backup_data_files: GITHUB_TOKEN 누락 시 ok=False
"""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kis_api.backup import _gist_patch_with_retry, backup_data_files


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼: FakeResponse
# ━━━━━━━━━━━━━━━━━━━━━━━━━

class _FakeResponse:
    """aiohttp.ClientResponse 최소 mock."""

    def __init__(self, status: int, body: bytes = b"{}", headers: dict | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def text(self) -> str:
        return self._body.decode() if isinstance(self._body, bytes) else self._body

    async def json(self) -> dict:
        return json.loads(await self.text())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _FakeSession:
    """aiohttp.ClientSession 최소 mock. patch() 호출 기록 보존."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[tuple] = []

    def patch(self, url, json=None, headers=None, **kwargs):
        self.calls.append(("PATCH", url, json, headers))
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._responses.pop(0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. If-Match 헤더 부재 (핵심 회귀 차단)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_if_match_header_NOT_present():
    """PATCH 헤더에 If-Match가 포함되지 않음을 확인 (5/27 버그 회귀 차단)."""
    session = _FakeSession([
        _FakeResponse(200, b'{"id": "abc", "updated_at": "2026-01-01"}'),
    ])
    headers = {"Authorization": "token test_token", "Accept": "application/vnd.github.v3+json"}

    async def run():
        return await _gist_patch_with_retry(
            session, "test_gist", {"test.json": {"content": "{}"}}, headers, "test desc"
        )

    result = asyncio.run(run())
    assert result.get("ok") is True

    assert len(session.calls) >= 1
    method, url, payload, sent_headers = session.calls[0]
    assert method == "PATCH"
    assert sent_headers is not None
    assert "If-Match" not in sent_headers, (
        "If-Match 헤더가 PATCH에 포함됨 — 5/27 버그 재발. "
        "GitHub Gist API는 conditional PATCH 미지원 (400 반환)."
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 200 OK → ok=True
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_200_returns_ok_true():
    """200 OK 응답은 ok=True, action=updated 반환."""
    session = _FakeSession([
        _FakeResponse(200, b'{"id": "abc", "updated_at": "2026-05-28T12:00:00Z"}'),
    ])
    headers = {"Authorization": "token test_token"}

    async def run():
        return await _gist_patch_with_retry(
            session, "test_gist", {"f.json": {"content": "{}"}}, headers, "desc"
        )

    result = asyncio.run(run())
    assert result.get("ok") is True
    assert result.get("action") == "updated"
    assert result.get("gist_id") == "abc"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. 409 Conflict → max_retries 후 ok=False
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_409_retries_up_to_max():
    """409 Conflict는 max_retries=3 까지 재시도 후 ok=False."""
    session = _FakeSession([
        _FakeResponse(409, b'{"message": "conflict"}'),
        _FakeResponse(409, b'{"message": "conflict"}'),
        _FakeResponse(409, b'{"message": "conflict"}'),
    ])
    headers = {"Authorization": "token test_token"}

    async def run():
        with patch("asyncio.sleep", new=AsyncMock()):  # backoff 스킵
            return await _gist_patch_with_retry(
                session, "test_gist", {}, headers, "desc", max_retries=3
            )

    result = asyncio.run(run())
    assert result.get("ok") is False
    assert len(session.calls) == 3, f"409 3회 retry 안 함: calls={len(session.calls)}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. 429 Rate Limit → Retry-After 준수
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_429_honors_retry_after():
    """429 응답의 Retry-After 헤더 값만큼 sleep 호출."""
    session = _FakeSession([
        _FakeResponse(429, b'{"message": "rate limit"}', {"Retry-After": "5"}),
        _FakeResponse(200, b'{"id": "abc", "updated_at": "2026-01-01"}'),
    ])
    headers = {"Authorization": "token test_token"}
    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(float(seconds))

    async def run():
        with patch("asyncio.sleep", new=fake_sleep):
            return await _gist_patch_with_retry(
                session, "test_gist", {}, headers, "desc"
            )

    result = asyncio.run(run())
    assert result.get("ok") is True, f"2차 시도 200 성공해야 함: {result}"
    assert 5.0 in sleep_calls, f"Retry-After: 5 헤더 무시됨. sleep_calls={sleep_calls}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. GITHUB_TOKEN 누락 → ok=False
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_missing_token_returns_error():
    """GITHUB_TOKEN 없으면 즉시 ok=False with error."""
    async def run():
        # kis_api.backup 모듈 내 GITHUB_TOKEN 상수를 None으로 패치
        with patch("kis_api.backup.GITHUB_TOKEN", None):
            return await backup_data_files()

    result = asyncio.run(run())
    assert result.get("ok") is False
    err = str(result.get("error", "")).lower()
    assert any(kw in err for kw in ("token", "github")), (
        f"에러 메시지에 'token'/'github' 없음: {result.get('error')}"
    )
