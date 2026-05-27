"""AST 기반 unresolved module-level names 회귀 방지.

C1 분할 이후 cross-module 함수 참조가 import 없이 사용되는 silent NameError를
방지하기 위한 테스트. try/except로 묻히는 에러를 사전 차단.

검사 방법:
- ast.walk로 각 모듈의 함수 정의·import 수집
- 모듈 전역에서 사용된 이름 중 정의되지 않은 것 탐지
- 공통 라이브러리 이름(asyncio, json 등)은 제외
"""
import ast
import os
import builtins

import pytest

BASE = "/Users/kreuzer/stock-bot"

MODULES = [
    "kis_api/macro.py",
    "kis_api/regime.py",
    "kis_api/portfolio.py",
    "kis_api/kr_stock.py",
    "kis_api/us_stock.py",
    "kis_api/consensus.py",
    "kis_api/dart.py",
    "kis_api/pension.py",
    "kis_api/us_ratings.py",
    "kis_api/ranks.py",
    "kis_api/fmp.py",
    "kis_api/polymarket.py",
    "kis_api/news.py",
    "kis_api/backup.py",
    "kis_api/universe.py",
    "kis_api/websocket.py",
    "kis_api/_session.py",
]

# 공통 stdlib / 타입 이름 — false positive 제외
_COMMON = frozenset({
    "asyncio", "json", "os", "sys", "re", "time", "datetime", "logging",
    "aiohttp", "requests", "sqlite3", "pytz", "zoneinfo", "pathlib", "Path",
    "List", "Dict", "Tuple", "Optional", "Any", "Union", "cast", "TYPE_CHECKING",
    "ClientSession", "ClientTimeout", "defaultdict", "dataclass", "field",
    "BeautifulSoup", "StringIO", "BytesIO", "contextmanager", "wraps",
    "partial", "lru_cache", "cached_property", "abstractmethod",
    "traceback", "functools", "itertools", "collections", "copy", "math",
    "hashlib", "base64", "urllib", "http", "xml", "csv", "io", "struct",
    "threading", "multiprocessing", "subprocess", "shutil", "tempfile", "glob",
    "socket", "ssl", "uuid", "decimal", "fractions", "random", "statistics",
    "heapq", "bisect", "array", "queue", "weakref", "gc", "inspect",
    "enum", "typing", "types", "abc", "contextlib", "warnings", "textwrap",
    "pprint", "difflib", "fnmatch", "pickle", "shelve", "dbm", "gzip", "zipfile",
    "tarfile", "configparser", "argparse", "getopt", "signal", "atexit",
    "ZoneInfo", "timezone", "date", "timedelta", "Counter", "OrderedDict",
    "namedtuple", "deque", "ChainMap", "UserDict", "UserList", "UserString",
    "Literal", "ClassVar", "Final", "TypeVar", "Generic", "Protocol",
    "NamedTuple", "TypedDict", "overload", "runtime_checkable", "dataclasses",
    "ET",  # xml.etree.ElementTree as ET
    "np",  # numpy (선택적 import, try/except 안에서 사용)
    "pandas", "pd",
})

_BUILTINS = frozenset(dir(builtins))


def _find_unresolved(filepath: str) -> list[str]:
    """모듈 전역 스코프에서 정의되지 않은 이름 반환.

    Note: AST walk는 스코프를 구분하지 못해 함수 내부 지역변수도 같이 수집됨.
    단순 단음절/숫자 변수는 false positive이므로 len > 2 필터 적용.
    핵심 목적은 함수/클래스/모듈 레벨 이름(get_yahoo_quote 등) 탐지.
    """
    with open(filepath, encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        raise ValueError(f"SyntaxError in {filepath}: {e}")

    defined: set[str] = set()
    used: set[str] = set()

    for node in ast.walk(tree):
        # 이름 정의
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            defined.add(elt.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                defined.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                defined.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                defined.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.Global):
            for name in node.names:
                defined.add(name)
        elif isinstance(node, ast.For):
            if isinstance(node.target, ast.Name):
                defined.add(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        defined.add(elt.id)
        # 이름 사용
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)

    # 진짜 누락 후보: 정의 없음 + 빌트인 아님 + 공통 라이브러리 아님 + 길이 > 2
    missing = sorted(
        n for n in (used - defined - _BUILTINS - _COMMON)
        if not n.startswith("__") and len(n) > 2
    )
    return missing


# 함수 단위 false positive가 많아서 실제 cross-module 핵심 이름만 검사
# (AST walk 스코프 미구분 한계 — 지역변수 false positive 다수)
_CRITICAL_CROSS_MODULE = {
    "kis_api/macro.py": ["get_yahoo_quote", "_yf_history", "_fetch_market_investor_flow"],
    "kis_api/regime.py": ["_yf_history", "get_yahoo_quote"],
    "kis_api/portfolio.py": ["get_yahoo_quote", "batch_stock_detail", "ws_manager"],
}


@pytest.mark.parametrize("module,required_names", _CRITICAL_CROSS_MODULE.items())
def test_critical_cross_module_imports(module, required_names):
    """핵심 cross-module 함수가 모듈 전역에 import되어 있는지 검사."""
    p = os.path.join(BASE, module)
    assert os.path.exists(p), f"모듈 파일 없음: {p}"

    with open(p, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            imported.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    imported.add(target.id)

    missing = [n for n in required_names if n not in imported]
    assert not missing, (
        f"{module}: 누락된 cross-module import {missing}\n"
        f"  hint: 해당 함수가 정의된 모듈에서 import 추가 필요"
    )


@pytest.mark.parametrize("module", MODULES)
def test_module_parseable(module):
    """모든 kis_api 모듈이 SyntaxError 없이 파싱 가능한지 검사."""
    p = os.path.join(BASE, module)
    if not os.path.exists(p):
        pytest.skip(f"파일 없음: {p}")
    with open(p, encoding="utf-8") as f:
        src = f.read()
    try:
        ast.parse(src)
    except SyntaxError as e:
        pytest.fail(f"{module}: SyntaxError — {e}")
