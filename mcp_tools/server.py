# mcp_tools/server.py — SSE 서버 + JSON-RPC handler
import json
import os
import time
import uuid
import asyncio
from aiohttp import web

from ._helpers import _check_mcp_auth
from ._execute import _execute_tool

_mcp_sessions: dict = {}       # session_id → asyncio.Queue
_streamable_sessions: dict = {}  # session_id → {"created": float}


async def _handle_jsonrpc(body: dict) -> dict | None:
    """JSON-RPC 요청 처리 → 응답 dict (notification이면 None)"""
    # MCP_TOOLS는 __init__에서 import
    from . import MCP_TOOLS
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
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS, "nextCursor": None}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        result = await _execute_tool(tool_name, tool_args)
        if isinstance(result, list):
            content = result
        else:
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"content": content}}

    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


async def mcp_sse_handler(request: web.Request) -> web.StreamResponse:
    """GET /mcp  → SSE 스트림 수립, endpoint 이벤트 전송"""
    if not _check_mcp_auth(request):
        return web.Response(status=401, text="Unauthorized")
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
    if not _check_mcp_auth(request):
        return web.Response(status=401, text="Unauthorized")
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Streamable HTTP transport (MCP 2025-03-26)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def mcp_streamable_post_handler(request: web.Request) -> web.Response:
    """POST /mcp  → Streamable HTTP: JSON-RPC 요청을 받아 JSON으로 직접 응답"""
    if not _check_mcp_auth(request):
        return web.Response(status=401, text="Unauthorized")

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    if isinstance(body, list):
        return web.json_response({"error": "batch requests not supported"}, status=400)

    now = time.time()
    expired = [sid for sid, info in _streamable_sessions.items() if now - info["created"] > 1800]
    for sid in expired:
        _streamable_sessions.pop(sid, None)

    method = body.get("method", "")
    session_id = request.headers.get("Mcp-Session-Id", "")

    if method == "initialize":
        session_id = str(uuid.uuid4())
        _streamable_sessions[session_id] = {"created": time.time()}
    elif not session_id:
        return web.json_response({"error": "Mcp-Session-Id header required"}, status=400)
    else:
        if session_id not in _streamable_sessions:
            return web.json_response({"error": "session not found"}, status=404)

    response = await _handle_jsonrpc(body)

    if response is None:
        return web.Response(status=202, text="Accepted", headers={
            "Mcp-Session-Id": session_id,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "Mcp-Session-Id",
        })

    return web.json_response(response, headers={
        "Mcp-Session-Id": session_id,
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Mcp-Session-Id",
    })


async def mcp_streamable_delete_handler(request: web.Request) -> web.Response:
    """DELETE /mcp  → Streamable HTTP: 세션 종료"""
    if not _check_mcp_auth(request):
        return web.Response(status=401, text="Unauthorized")
    session_id = request.headers.get("Mcp-Session-Id", "")
    _streamable_sessions.pop(session_id, None)
    return web.Response(status=200, text="Session deleted")


async def mcp_streamable_options_handler(request: web.Request) -> web.Response:
    """OPTIONS /mcp  → CORS preflight 응답"""
    return web.Response(status=204, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Mcp-Session-Id, Authorization",
        "Access-Control-Expose-Headers": "Mcp-Session-Id",
    })
