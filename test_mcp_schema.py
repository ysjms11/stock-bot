"""
MCP 도구 스키마 ↔ 핸들러 동기화 검증 테스트.
MCP_TOOLS 등록과 TOOL_HANDLERS dispatch dict가 1:1 매칭되는지,
그리고 서브모드 enum이 각 핸들러의 실제 분기와 일치하는지 자동 검증.

리팩터 후: mcp_tools.py(flat) → mcp_tools/ 패키지. 더 이상 소스 파일을
regex로 긁지 않고 MCP_TOOLS / TOOL_HANDLERS 를 직접 import 한다.
서브모드(예: rank_type == "...")는 핸들러 함수 본문에 있으므로
해당 tools/<module>.py 를 AST로 파싱해 추출한다.
"""
import ast
from pathlib import Path

from mcp_tools import MCP_TOOLS
from mcp_tools._registry import TOOL_HANDLERS

_PKG = Path(__file__).parent / "mcp_tools"


def _registered_tools() -> list[str]:
    return [t["name"] for t in MCP_TOOLS]


def _handler_names() -> set[str]:
    return set(TOOL_HANDLERS.keys())


def _enum_values(tool_name: str, field: str) -> set[str]:
    for t in MCP_TOOLS:
        if t["name"] == tool_name:
            props = t.get("inputSchema", {}).get("properties", {})
            return set(props.get(field, {}).get("enum", []) or [])
    return set()


def _handler_submodes(module_rel: str, func_name: str, var_name: str) -> set[str]:
    """tools/<module>.py 의 func_name 함수 본문에서 `var_name == "리터럴"` 비교를 AST로 수집."""
    tree = ast.parse((_PKG / module_rel).read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func_name),
        None,
    )
    assert fn is not None, f"{func_name} 함수를 {module_rel} 에서 못 찾음"
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == var_name:
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                    out.add(comp.value)
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 1: 모든 핸들러가 MCP_TOOLS에 등록되어 있는지
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_all_handlers_registered():
    missing = _handler_names() - set(_registered_tools())
    assert not missing, f"TOOL_HANDLERS에 있지만 MCP_TOOLS에 미등록: {missing}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 2: 모든 MCP_TOOLS 등록 도구에 핸들러가 있는지
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_all_registered_have_handlers():
    orphaned = set(_registered_tools()) - _handler_names()
    assert not orphaned, f"MCP_TOOLS에 등록됐지만 TOOL_HANDLERS에 없음: {orphaned}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 3: get_rank type enum과 핸들러 서브모드 동기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_get_rank_type_sync():
    enum = _enum_values("get_rank", "type")
    submodes = _handler_submodes("tools/price.py", "handle_get_rank", "rank_type")
    missing_handler = enum - submodes
    missing_enum = submodes - enum
    assert not missing_handler, f"get_rank enum에 있지만 핸들러 없음: {missing_handler}"
    assert not missing_enum, f"get_rank 핸들러에 있지만 enum 미등록: {missing_enum}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 4: get_supply mode enum과 핸들러 서브모드 동기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_get_supply_mode_sync():
    enum = _enum_values("get_supply", "mode")
    submodes = _handler_submodes("tools/supply.py", "handle_get_supply", "supply_mode")
    missing_handler = enum - submodes
    missing_enum = submodes - enum
    assert not missing_handler, f"get_supply enum에 있지만 핸들러 없음: {missing_handler}"
    assert not missing_enum, f"get_supply 핸들러에 있지만 enum 미등록: {missing_enum}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 5: get_market_signal mode enum과 핸들러 서브모드 동기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_get_market_signal_mode_sync():
    enum = _enum_values("get_market_signal", "mode")
    submodes = _handler_submodes("tools/market_signal.py", "handle_get_market_signal", "signal_mode")
    missing_handler = enum - submodes
    missing_enum = submodes - enum
    assert not missing_handler, f"get_market_signal enum에 있지만 핸들러 없음: {missing_handler}"
    assert not missing_enum, f"get_market_signal 핸들러에 있지만 enum 미등록: {missing_enum}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 6: get_stock_detail mode enum과 핸들러 서브모드 동기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_get_stock_detail_mode_sync():
    enum = _enum_values("get_stock_detail", "mode")
    submodes = _handler_submodes("tools/price.py", "handle_get_stock_detail", "mode")
    missing_handler = enum - submodes
    missing_enum = submodes - enum
    assert not missing_handler, f"get_stock_detail enum에 있지만 핸들러 없음: {missing_handler}"
    assert not missing_enum, f"get_stock_detail 핸들러에 있지만 enum 미등록: {missing_enum}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 7: MCP_TOOLS 도구 수 일관성
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_tool_count():
    tools = _registered_tools()
    assert len(tools) == len(_handler_names()), \
        f"MCP_TOOLS({len(tools)}개) != TOOL_HANDLERS({len(_handler_names())}개)"
    assert len(tools) == len(set(tools)), \
        f"MCP_TOOLS에 중복 도구명: {[t for t in tools if tools.count(t) > 1]}"
