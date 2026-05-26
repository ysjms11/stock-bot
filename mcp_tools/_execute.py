# mcp_tools/_execute.py — _execute_tool() entry point
"""
기존 _execute_tool() 의 완전한 재구현.
dispatch dict (_registry.TOOL_HANDLERS) 기반으로 elif chain 제거.
"""
import json
import traceback

from ._registry import execute_tool
from ._helpers import _NO_TOKEN_TOOLS


async def _execute_tool(name: str, arguments: dict) -> dict | list:
    """툴 실행 → 결과 반환 (에러 시 {"error": ...})"""
    arguments = arguments or {}
    print(f"툴 호출: {name} {arguments}")
    try:
        result = await execute_tool(name, arguments)
    except Exception as e:
        tb = traceback.format_exc()
        result = {"error": str(e), "traceback": tb}
        print(f"에러: {name} → {e}\n{tb}")

    if isinstance(result, list):
        print(f"툴 결과: {name} → [content list, {len(result)}개 항목]")
    else:
        print(f"툴 결과: {name} → {json.dumps(result, ensure_ascii=False)[:200]}")
    return result
