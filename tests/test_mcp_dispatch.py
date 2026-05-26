"""46개 MCP 도구 자동 smoke test — dispatcher TypeError 회귀 방지."""
import asyncio
import pytest
from mcp_tools import MCP_TOOLS, _execute_tool


@pytest.mark.parametrize("tool_name", [t["name"] for t in MCP_TOOLS])
def test_tool_invokable(tool_name):
    """각 도구가 빈 인자로 호출 시 TypeError 없음.

    외부 의존성 누락(DB 없음, API 키 없음 등)으로 인한 에러는 OK.
    TypeError / positional argument 시그니처 불일치만 금지.
    """
    result = asyncio.run(_execute_tool(tool_name, {}))
    assert isinstance(result, (dict, list)), (
        f"{tool_name} returned unexpected type {type(result)}"
    )
    if isinstance(result, dict) and "error" in result:
        err = str(result["error"])
        assert "TypeError" not in err, f"{tool_name}: TypeError in result — {err}"
        # positional argument 시그니처 mismatch 감지
        if "positional argument" in err and "takes" in err:
            pytest.fail(f"{tool_name}: signature mismatch — {err}")
