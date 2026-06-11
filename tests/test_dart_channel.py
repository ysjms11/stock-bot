"""DART 채널 분리 — _safe_send_dart 라우팅 단위 테스트 (네트워크 없음).

3분기 동작 검증:
1) 미설정 (default) → _safe_send 경로
2) DART_CHAT_ID만 설정 → context.bot.send_message(chat_id=DART_CHAT_ID)
3) DART_TELEGRAM_TOKEN 설정 → aiohttp POST + 토큰/chat_id 확인
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Fake context + bot helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _make_context(send_ok: bool = True):
    """가짜 telegram context — bot.send_message 기록."""
    bot = MagicMock()
    calls = []

    async def fake_send_message(**kwargs):
        calls.append(kwargs)
        if not send_ok:
            raise Exception("parse entities failed")

    bot.send_message = fake_send_message
    ctx = MagicMock()
    ctx.bot = bot
    return ctx, calls


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 미설정 → _safe_send 폴백
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_dart_send_fallback_to_safe_send_when_unconfigured():
    """DART_TELEGRAM_TOKEN='' 이고 DART_CHAT_ID='' → _safe_send 그대로 호출."""
    safe_send_calls = []

    async def mock_safe_send(context, text, parse_mode="Markdown", **kwargs):
        safe_send_calls.append({"text": text, "parse_mode": parse_mode, **kwargs})
        return True

    import main_pkg._ctx as ctx_mod
    with patch.object(ctx_mod, "DART_TELEGRAM_TOKEN", ""), \
         patch.object(ctx_mod, "DART_CHAT_ID", ""), \
         patch.object(ctx_mod, "_safe_send", mock_safe_send):
        ctx, _ = _make_context()
        result = await ctx_mod._safe_send_dart(ctx, "test msg", disable_web_page_preview=True)

    assert result is True
    assert len(safe_send_calls) == 1
    assert safe_send_calls[0]["text"] == "test msg"
    assert safe_send_calls[0].get("disable_web_page_preview") is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. DART_CHAT_ID만 설정 → 같은 봇, 다른 채팅방
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_dart_send_uses_dart_chat_id_when_only_chat_id_set():
    """DART_TELEGRAM_TOKEN 미설정, DART_CHAT_ID='dart_chan' → send_message(chat_id='dart_chan')."""
    import main_pkg._ctx as ctx_mod

    ctx, calls = _make_context(send_ok=True)
    with patch.object(ctx_mod, "DART_TELEGRAM_TOKEN", ""), \
         patch.object(ctx_mod, "DART_CHAT_ID", "dart_chan"):
        result = await ctx_mod._safe_send_dart(ctx, "hello dart")

    assert result is True
    assert len(calls) == 1
    assert calls[0]["chat_id"] == "dart_chan"
    assert calls[0]["text"] == "hello dart"


@pytest.mark.asyncio
async def test_dart_send_chat_id_plain_fallback_on_parse_error():
    """DART_CHAT_ID만 설정 + parse_mode 오류 → plain text fallback."""
    import main_pkg._ctx as ctx_mod

    call_log = []
    attempt = [0]

    async def fake_send(**kwargs):
        attempt[0] += 1
        call_log.append(kwargs)
        if attempt[0] == 1 and kwargs.get("parse_mode"):
            raise Exception("can't parse entities: offset=5")
        # 두 번째 호출(plain) 성공

    ctx, _ = _make_context()
    ctx.bot.send_message = fake_send

    with patch.object(ctx_mod, "DART_TELEGRAM_TOKEN", ""), \
         patch.object(ctx_mod, "DART_CHAT_ID", "dart_chan"):
        result = await ctx_mod._safe_send_dart(ctx, "bad *markup*")

    assert result is True
    assert attempt[0] == 2
    # 두 번째 호출에는 parse_mode 없어야 함
    assert "parse_mode" not in call_log[1]


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. DART_TELEGRAM_TOKEN 설정 → aiohttp raw HTTP
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_dart_send_uses_aiohttp_when_token_set():
    """DART_TELEGRAM_TOKEN='mytoken' → aiohttp POST URL에 토큰 포함, chat_id 정확."""
    import main_pkg._ctx as ctx_mod

    posted = []

    class FakeResp:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self, content_type=None):
            return {"ok": True, "result": {}}

    class FakeSession:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def post(self, url, json=None, **kwargs):
            posted.append({"url": url, "json": json})
            return FakeResp()
        async def close(self): pass

    ctx, _ = _make_context()
    with patch.object(ctx_mod, "DART_TELEGRAM_TOKEN", "mytoken"), \
         patch.object(ctx_mod, "DART_CHAT_ID", "dart999"), \
         patch("main_pkg._ctx.aiohttp", create=True) as mock_aio:
        # We need to patch aiohttp inside the function — use a different approach
        pass

    # Patch aiohttp.ClientSession directly via import path used in _safe_send_dart
    import aiohttp as real_aiohttp
    with patch.object(ctx_mod, "DART_TELEGRAM_TOKEN", "mytoken"), \
         patch.object(ctx_mod, "DART_CHAT_ID", "dart999"), \
         patch.object(real_aiohttp, "ClientSession", FakeSession):
        result = await ctx_mod._safe_send_dart(ctx, "aiohttp msg", parse_mode="Markdown")

    assert result is True
    assert len(posted) == 1
    assert "mytoken" in posted[0]["url"]
    assert posted[0]["json"]["chat_id"] == "dart999"
    assert posted[0]["json"]["text"] == "aiohttp msg"
    assert posted[0]["json"]["parse_mode"] == "Markdown"


@pytest.mark.asyncio
async def test_dart_send_aiohttp_parse_error_retries_without_parse_mode():
    """aiohttp 경로에서 parse entity 에러 → parse_mode 없이 재시도."""
    import main_pkg._ctx as ctx_mod
    import aiohttp as real_aiohttp

    posted = []
    attempt = [0]

    class FakeResp:
        def __init__(self, ok, desc=""):
            self._ok = ok
            self._desc = desc
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self, content_type=None):
            return {"ok": self._ok, "description": self._desc}

    class FakeSession:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def post(self, url, json=None, **kwargs):
            attempt[0] += 1
            posted.append({"url": url, "json": dict(json or {})})
            if attempt[0] == 1:
                return FakeResp(False, "can't parse entities at offset 3")
            return FakeResp(True)
        async def close(self): pass

    ctx, _ = _make_context()
    with patch.object(ctx_mod, "DART_TELEGRAM_TOKEN", "tok2"), \
         patch.object(ctx_mod, "DART_CHAT_ID", "chan2"), \
         patch.object(real_aiohttp, "ClientSession", FakeSession):
        result = await ctx_mod._safe_send_dart(ctx, "bad *markup*", parse_mode="Markdown")

    assert result is True
    assert attempt[0] == 2
    # 재시도에는 parse_mode 없어야 함
    assert "parse_mode" not in posted[1]["json"]
