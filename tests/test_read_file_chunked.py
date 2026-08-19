# tests/test_read_file_chunked.py — read_file 대용량 자율 순회 회귀 테스트
#
# 배경: mcp_tools/tools/files.py의 handle_read_file이 100KB 크기 게이트를
# lines/offset 분기보다 먼저 평가해, 큰 파일(예: DART 보고서 txt 211KB~817KB)은
# 분할읽기 자체가 불가능했음. 이후 "안내형 에러" 1차 수정을 거쳐, 2차 수정으로
# 클라이언트가 path(+offset)만으로 next_offset을 따라가며 파일 전체를 자동
# 순회할 수 있게 바뀜(안내형 에러 폐기 → 첫 청크 즉시 반환). 이 테스트는:
#   1. 100KB 초과 + lines 없음 → 안내에러가 아니라 "첫 청크 + next_offset" 즉시 반환
#   2. lines 지정 시에도 남은 라인 있으면 next_offset 포함, 다 읽었으면 미포함(종료 신호)
#   3. offset 이어읽기가 원본과 정확히 일치
#   4. 한 청크 요청이 100KB를 넘으면 truncated=true + 콘텐츠 ≤100KB + next_offset 포함
#   5. ≤100KB 파일은 lines 생략 시 기존처럼 전체 반환 (회귀 없음, next_offset 없음)
#   6. 보안 가드(경로탈출/확장자/민감경로)가 리팩터 후에도 여전히 작동
#   7. 자율 순회 시뮬레이션: path(+offset)만으로 next_offset을 따라 반복 호출 →
#      전체 이어붙임이 원본과 바이트 단위로 일치
#   8. 단일 거대 라인(>100KB) → partial_line=true + next_offset이 그 라인을 건너뜀
#   9. lines/offset 정수 가드, 음수 offset 보정

import os

import pytest

from mcp_tools.tools.files import handle_read_file

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "data")

_LINE_BYTES = 100  # "line%06d" (10) + padding(89) + "\n" (1) = 100 bytes, ASCII-only
_BUDGET = 100 * 1024


def _make_line(idx: int) -> str:
    head = "line%06d" % idx
    pad = " " * (_LINE_BYTES - 1 - len(head))
    return head + pad + "\n"


def _write_file(path: str, content: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return content


def _write_lines_file(path: str, n_lines: int) -> str:
    content = "".join(_make_line(i) for i in range(n_lines))
    written = _write_file(path, content)
    assert os.path.getsize(path) == n_lines * _LINE_BYTES
    return written


@pytest.fixture
def big_file():
    """150KB 파일 (1536 lines * 100 bytes = 153600 bytes = 150KB)."""
    fname = "_test_read_file_chunked_big.txt"
    fpath = os.path.join(_DATA_DIR, fname)
    n_lines = 1536
    content = _write_lines_file(fpath, n_lines)
    try:
        yield f"data/{fname}", n_lines, content
    finally:
        if os.path.exists(fpath):
            os.remove(fpath)


@pytest.fixture
def small_file():
    """50KB 파일 (512 lines * 100 bytes = 51200 bytes = 50KB) — 기존 동작 회귀 확인용."""
    fname = "_test_read_file_chunked_small.txt"
    fpath = os.path.join(_DATA_DIR, fname)
    n_lines = 512
    content = _write_lines_file(fpath, n_lines)
    try:
        yield f"data/{fname}", n_lines, content
    finally:
        if os.path.exists(fpath):
            os.remove(fpath)


@pytest.fixture
def giant_line_file():
    """단일 거대 라인(150000 bytes, >100KB) + 뒤이은 짧은 라인 2개."""
    fname = "_test_read_file_chunked_giant_line.txt"
    fpath = os.path.join(_DATA_DIR, fname)
    giant = ("A" * 150000) + "\n"
    content = giant + "short1\n" + "short2\n"
    _write_file(fpath, content)
    try:
        yield f"data/{fname}", content, giant
    finally:
        if os.path.exists(fpath):
            os.remove(fpath)


# ── 1. 100KB 초과 + lines 없음 → 첫 청크 즉시 반환 (안내에러 폐기) ────────

async def test_oversized_no_lines_returns_first_chunk(big_file):
    rel, n_lines, content = big_file
    result = await handle_read_file({"path": rel})
    assert "error" not in result
    assert "hint" not in result  # 구 안내형 에러 폐기 확인
    assert result["path"] == rel
    assert result["total_lines"] == n_lines
    assert result["offset"] == 0
    assert result["truncated"] is True
    # 100바이트/줄 * 1024줄 = 정확히 100KB 예산 소진
    assert result["lines_returned"] == 1024
    assert result["next_offset"] == 1024
    assert "note" in result
    content_bytes = len(result["content"].encode("utf-8"))
    assert content_bytes <= _BUDGET
    expected = "".join(_make_line(i) for i in range(1024))
    assert result["content"] == expected


# ── 2. lines 지정 시에도 next_offset 포함/미포함 규칙 동일 적용 ──────────

async def test_lines_specified_chunk_includes_next_offset(big_file):
    rel, n_lines, content = big_file
    result = await handle_read_file({"path": rel, "lines": 100, "offset": 0})
    assert "error" not in result
    assert result["lines_returned"] == 100
    assert result["total_lines"] == n_lines
    assert result["offset"] == 0
    assert result["next_offset"] == 100  # 아직 남은 라인 있음
    assert result.get("truncated") is not True
    expected = "".join(_make_line(i) for i in range(0, 100))
    assert result["content"] == expected


async def test_lines_specified_fully_read_omits_next_offset(small_file):
    rel, n_lines, content = small_file
    # 512줄 전부 한 번에 요청 (50KB < 100KB 캡이라 안 잘림)
    result = await handle_read_file({"path": rel, "lines": n_lines, "offset": 0})
    assert "error" not in result
    assert result["lines_returned"] == n_lines
    assert "next_offset" not in result  # 종료 신호
    assert "note" not in result
    assert result["content"] == content


# ── 3. offset 이어읽기가 원본과 정확히 일치 ──────────────────────────────

async def test_offset_continuation_matches_original(big_file):
    rel, n_lines, content = big_file
    chunk1 = await handle_read_file({"path": rel, "lines": 100, "offset": 0})
    assert chunk1["next_offset"] == 100
    chunk2 = await handle_read_file({"path": rel, "lines": 100, "offset": chunk1["next_offset"]})
    assert chunk2["offset"] == 100
    combined = chunk1["content"] + chunk2["content"]
    expected_first_200 = "".join(_make_line(i) for i in range(0, 200))
    assert combined == expected_first_200
    assert content.startswith(combined)


# ── 4. 한 청크 요청이 100KB 초과 → truncated + 콘텐츠 캡 + next_offset ───

async def test_chunk_exceeding_cap_is_truncated(big_file):
    rel, n_lines, content = big_file
    # 전체 1536줄(153600 bytes)을 한 번에 요청 — 응답 100KB 캡 초과
    result = await handle_read_file({"path": rel, "lines": n_lines, "offset": 0})
    assert result.get("truncated") is True
    content_bytes = len(result["content"].encode("utf-8"))
    assert content_bytes <= _BUDGET
    assert result["lines_returned"] < n_lines
    assert result["total_lines"] == n_lines
    assert result["next_offset"] == result["lines_returned"]
    expected = "".join(_make_line(i) for i in range(result["lines_returned"]))
    assert result["content"] == expected


# ── 5. ≤100KB 파일은 lines 생략 시 기존처럼 전체 반환 (회귀 없음) ────────

async def test_small_file_full_read_unchanged(small_file):
    rel, n_lines, content = small_file
    result = await handle_read_file({"path": rel})
    assert "error" not in result
    assert result["content"] == content
    assert "total_lines" not in result
    assert "lines_returned" not in result
    assert "next_offset" not in result


async def test_small_file_with_lines_still_works(small_file):
    rel, n_lines, content = small_file
    result = await handle_read_file({"path": rel, "lines": 50, "offset": 10})
    assert "error" not in result
    assert result["lines_returned"] == 50
    assert result["total_lines"] == n_lines
    assert result["next_offset"] == 60
    expected = "".join(_make_line(i) for i in range(10, 60))
    assert result["content"] == expected


# ── 6. 보안 가드 회귀 확인 (경로탈출/확장자/민감경로) ────────────────────

async def test_path_traversal_still_blocked():
    result = await handle_read_file({"path": "../etc/passwd"})
    assert "error" in result
    assert "상위 디렉토리" in result["error"]


async def test_disallowed_extension_still_blocked():
    result = await handle_read_file({"path": "data/nope.exe"})
    assert "error" in result
    assert "허용 확장자" in result["error"]


async def test_sensitive_path_still_blocked():
    result = await handle_read_file({"path": ".env"})
    assert "error" in result
    assert "보안상 보호된" in result["error"]


# ── 7. 자율 순회 시뮬레이션 (path(+offset)만으로 next_offset 추적) ───────

async def test_autonomous_traversal_path_only_matches_original(big_file):
    rel, n_lines, content = big_file
    collected = ""
    args = {"path": rel}  # 첫 호출: path만
    iterations = 0
    while True:
        iterations += 1
        assert iterations < 20, "무한루프 의심 — next_offset이 수렴하지 않음"
        result = await handle_read_file(args)
        assert "error" not in result
        collected += result["content"]
        next_offset = result.get("next_offset")
        if next_offset is None:
            break
        args = {"path": rel, "offset": next_offset}  # lines는 계속 생략
    assert collected == content
    assert iterations >= 2  # 실제로 여러 청크로 나뉘어 순회했는지 확인


# ── 8. 단일 거대 라인(>100KB) → partial_line + next_offset이 그 줄을 건너뜀 ─

async def test_giant_line_sets_partial_line_and_skips_it(giant_line_file):
    rel, content, giant = giant_line_file
    result = await handle_read_file({"path": rel})
    assert "error" not in result
    assert result["partial_line"] is True
    assert result["truncated"] is True
    assert result["lines_returned"] == 1
    content_bytes = len(result["content"].encode("utf-8"))
    assert content_bytes <= _BUDGET
    assert "next_offset" in result
    assert result["next_offset"] == 1  # 절단된 라인 "다음" 라인을 가리킴 (무한 재요청 방지)
    assert "note" in result and "바이트절단" in result["note"]

    # 다음 호출은 절단된 라인을 재요청하지 않고 그 다음 줄부터 정상적으로 이어짐
    follow = await handle_read_file({"path": rel, "offset": result["next_offset"]})
    assert "error" not in follow
    assert follow.get("partial_line") is not True
    assert follow["content"] == "short1\nshort2\n"
    assert "next_offset" not in follow  # 파일 끝 — 순회 종료


async def test_giant_line_as_only_line_has_no_next_offset():
    """거대 라인이 파일의 마지막(유일한) 라인이면 next_offset이 없어야 함(잔여 접근 불가 안내만)."""
    fname = "_test_read_file_chunked_giant_only.txt"
    fpath = os.path.join(_DATA_DIR, fname)
    content = ("B" * 150000)  # 개행 없는 단일 라인
    _write_file(fpath, content)
    try:
        result = await handle_read_file({"path": f"data/{fname}"})
        assert result["partial_line"] is True
        assert "next_offset" not in result
        assert "바이트절단" in result["note"]
    finally:
        if os.path.exists(fpath):
            os.remove(fpath)


# ── 9. lines/offset 정수 가드 + 음수 offset 보정 ─────────────────────────

async def test_non_integer_lines_returns_error(big_file):
    rel, *_ = big_file
    result = await handle_read_file({"path": rel, "lines": "abc"})
    assert result == {"error": "lines/offset은 정수여야 합니다"}


async def test_non_integer_offset_returns_error(big_file):
    rel, *_ = big_file
    result = await handle_read_file({"path": rel, "lines": 10, "offset": "xyz"})
    assert result == {"error": "lines/offset은 정수여야 합니다"}


async def test_negative_offset_clamped_to_zero(big_file):
    rel, n_lines, content = big_file
    result = await handle_read_file({"path": rel, "lines": 100, "offset": -50})
    assert "error" not in result
    assert result["offset"] == 0
    expected = "".join(_make_line(i) for i in range(0, 100))
    assert result["content"] == expected
