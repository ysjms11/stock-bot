# 새 MCP 도구 추가 방법

> ⚠️ 2026-05 리팩터 후 절차. 구 버전(`mcp_tools.py` 단일파일 + `_execute_tool` elif 체인)은 폐기됨.

**Step 1 — API 함수 작성** (`kis_api/`의 적절한 도메인 서브모듈, 예 `kis_api/kr_stock.py`에 추가)

```python
async def kis_new_api(ticker: str, token: str) -> dict:
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/...", "TR_ID", token, {"param": ticker})
        return d.get("output", {})
```

**Step 2 — MCP_TOOLS 배열에 스키마 추가** (`mcp_tools/__init__.py`의 `MCP_TOOLS` 배열 끝)

```python
{"name": "new_tool_name", "description": "도구 설명",
 "inputSchema": {"type": "object",
                 "properties": {"ticker": {"type": "string", "description": "종목코드"}},
                 "required": ["ticker"]}},
```

**Step 3 — 핸들러 작성** (`mcp_tools/tools/<도메인>.py`. 시그니처 `handle_xxx(arguments, token=None)`. token 인자가 있으면 `execute_tool`이 자동 발급)

```python
async def handle_new_tool(arguments: dict, token=None) -> dict | list:
    ticker = arguments.get("ticker", "").strip()
    d = await kis_new_api(ticker, token)
    return {"ticker": ticker, "field": d.get("field_name")}
```

**Step 4 — `TOOL_HANDLERS`에 등록** (`mcp_tools/_registry.py`: 핸들러 import + dict 항목 추가)

```python
from .tools.<도메인> import handle_new_tool
TOOL_HANDLERS = {
    ...
    "new_tool_name": handle_new_tool,
}
```

**Step 5** — `test_mcp_schema.py`가 `MCP_TOOLS` ↔ `TOOL_HANDLERS` 1:1 매칭을 자동 검증하므로 실행해 확인. 커밋 & push → 맥미니 서버 재시작.
