# 새 MCP 도구 추가 방법

**Step 1 — API 함수 작성** (`kis_api.py`에 추가)

```python
async def kis_new_api(ticker: str, token: str) -> dict:
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/...", "TR_ID", token, {"param": ticker})
        return d.get("output", {})
```

**Step 2 — MCP_TOOLS 배열에 스키마 추가** (`mcp_tools.py`의 `MCP_TOOLS` 배열 끝)

```python
{"name": "new_tool_name", "description": "도구 설명",
 "inputSchema": {"type": "object",
                 "properties": {"ticker": {"type": "string", "description": "종목코드"}},
                 "required": ["ticker"]}},
```

**Step 3 — `_execute_tool` 함수에 elif 핸들러 추가** (`mcp_tools.py`의 `else: result = {"error": ...}` 바로 위)

```python
elif name == "new_tool_name":
    ticker = arguments.get("ticker", "").strip()
    d = await kis_new_api(ticker, token)
    result = {"ticker": ticker, "field": d.get("field_name")}
```

**Step 4** — 커밋 & push → 맥미니 서버에서 git pull 후 재시작
