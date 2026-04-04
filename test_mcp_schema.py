"""
MCP 도구 스키마 ↔ 핸들러 동기화 검증 테스트.
MCP_TOOLS 등록과 _execute_tool 핸들러가 1:1 매칭되는지 자동 검증.
"""

import re
import pytest


def _read_mcp_tools_py():
    with open("mcp_tools.py", "r") as f:
        return f.read()


def _extract_registered_tools(content: str) -> list[str]:
    """MCP_TOOLS 배열에서 등록된 도구 이름 추출."""
    # MCP_TOOLS는 _execute_tool 함수 정의 전까지
    cut = content.find("async def _execute_tool")
    schema_section = content[:cut]
    return re.findall(r'"name":\s*"([^"]+)"', schema_section)


def _extract_handler_names(content: str) -> set[str]:
    """_execute_tool 함수 내 elif name == '...' 핸들러 추출."""
    exec_section = content[content.find("async def _execute_tool"):]
    return set(re.findall(r'(?:if|elif)\s+name\s*==\s*"([^"]+)"', exec_section))


def _extract_enum_values(content: str, tool_name: str, field_name: str) -> list[str]:
    """특정 도구의 특정 필드 enum 값 추출."""
    idx = content.find(f'"name": "{tool_name}"')
    if idx < 0:
        return []
    chunk = content[idx:idx + 1500]
    # Find the field's enum
    field_pattern = f'"{field_name}".*?"enum":\\s*\\[([^\\]]+)\\]'
    match = re.search(field_pattern, chunk, re.DOTALL)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def _extract_handler_submodes(content: str, tool_name: str, var_name: str) -> set[str]:
    """_execute_tool 내 특정 도구의 서브모드(elif var == '...') 추출."""
    exec_section = content[content.find("async def _execute_tool"):]
    # Find the tool handler block
    tool_idx = exec_section.find(f'name == "{tool_name}"')
    if tool_idx < 0:
        return set()
    # Find next top-level elif (same indentation as name ==)
    # Find the next top-level tool handler to limit scope
    next_tool = re.search(r'\n        elif name == "', exec_section[tool_idx + 50:])
    end = tool_idx + 50 + next_tool.start() if next_tool else tool_idx + 15000
    block = exec_section[tool_idx:end]
    # Extract submodes
    return set(re.findall(
        rf'(?:if|elif)\s+{var_name}\s*==\s*"([^"]+)"', block
    ))


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 1: 모든 핸들러가 MCP_TOOLS에 등록되어 있는지
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_all_handlers_registered():
    content = _read_mcp_tools_py()
    registered = set(_extract_registered_tools(content))
    handlers = _extract_handler_names(content)

    missing = handlers - registered
    assert not missing, f"_execute_tool에 핸들러가 있지만 MCP_TOOLS에 미등록: {missing}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 2: 모든 MCP_TOOLS 등록 도구에 핸들러가 있는지
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_all_registered_have_handlers():
    content = _read_mcp_tools_py()
    registered = set(_extract_registered_tools(content))
    handlers = _extract_handler_names(content)

    orphaned = registered - handlers
    assert not orphaned, f"MCP_TOOLS에 등록됐지만 _execute_tool에 핸들러 없음: {orphaned}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 3: get_rank type enum과 핸들러 서브모드 동기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_get_rank_type_sync():
    content = _read_mcp_tools_py()
    enum_vals = set(_extract_enum_values(content, "get_rank", "type"))
    handler_modes = _extract_handler_submodes(content, "get_rank", "rank_type")

    missing_handler = enum_vals - handler_modes
    missing_enum = handler_modes - enum_vals
    assert not missing_handler, f"get_rank enum에 있지만 핸들러 없음: {missing_handler}"
    assert not missing_enum, f"get_rank 핸들러에 있지만 enum 미등록: {missing_enum}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 4: get_supply mode enum과 핸들러 서브모드 동기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_get_supply_mode_sync():
    content = _read_mcp_tools_py()
    enum_vals = set(_extract_enum_values(content, "get_supply", "mode"))
    handler_modes = _extract_handler_submodes(content, "get_supply", "supply_mode")

    missing_handler = enum_vals - handler_modes
    missing_enum = handler_modes - enum_vals
    assert not missing_handler, f"get_supply enum에 있지만 핸들러 없음: {missing_handler}"
    assert not missing_enum, f"get_supply 핸들러에 있지만 enum 미등록: {missing_enum}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 5: get_market_signal mode enum과 핸들러 서브모드 동기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_get_market_signal_mode_sync():
    content = _read_mcp_tools_py()
    enum_vals = set(_extract_enum_values(content, "get_market_signal", "mode"))
    handler_modes = _extract_handler_submodes(content, "get_market_signal", "signal_mode")

    missing_handler = enum_vals - handler_modes
    missing_enum = handler_modes - enum_vals
    assert not missing_handler, f"get_market_signal enum에 있지만 핸들러 없음: {missing_handler}"
    assert not missing_enum, f"get_market_signal 핸들러에 있지만 enum 미등록: {missing_enum}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 6: get_stock_detail mode enum과 핸들러 서브모드 동기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_get_stock_detail_mode_sync():
    content = _read_mcp_tools_py()
    enum_vals = set(_extract_enum_values(content, "get_stock_detail", "mode"))
    handler_modes = _extract_handler_submodes(content, "get_stock_detail", "mode")

    missing_handler = enum_vals - handler_modes
    missing_enum = handler_modes - enum_vals
    assert not missing_handler, f"get_stock_detail enum에 있지만 핸들러 없음: {missing_handler}"
    assert not missing_enum, f"get_stock_detail 핸들러에 있지만 enum 미등록: {missing_enum}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 7: MCP_TOOLS 도구 수 일관성
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tool_count():
    content = _read_mcp_tools_py()
    tools = _extract_registered_tools(content)
    handlers = _extract_handler_names(content)

    assert len(tools) == len(handlers), \
        f"MCP_TOOLS({len(tools)}개) != 핸들러({len(handlers)}개)"
    # 중복 도구명 없어야 함
    assert len(tools) == len(set(tools)), \
        f"MCP_TOOLS에 중복 도구명: {[t for t in tools if tools.count(t) > 1]}"
