"""_is_content_block / _normalize_content 회귀 테스트.

Bug fixed: handlers returning bare list[dict] (e.g. get_stock_detail multi-ticker,
get_rank scan, get_supply foreign_rank) previously placed raw dicts directly into
MCP content, failing client Pydantic validation (content.N.type/text required).
"""
import decimal
import datetime
import json

import pytest
from mcp_tools.server import _is_content_block, _normalize_content


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def assert_all_valid_content_blocks(blocks):
    """모든 출력 엘리먼트가 유효한 MCP content block인지 검증."""
    assert isinstance(blocks, list), f"expected list, got {type(blocks)}"
    for i, el in enumerate(blocks):
        assert _is_content_block(el), (
            f"element[{i}] is not a valid content block: {el!r}"
        )


# ---------------------------------------------------------------------------
# _is_content_block unit tests
# ---------------------------------------------------------------------------

class TestIsContentBlock:
    def test_valid_text_block(self):
        assert _is_content_block({"type": "text", "text": "hello"}) is True

    def test_valid_image_block(self):
        assert _is_content_block({"type": "image", "data": "AA==", "mimeType": "image/png"}) is True

    def test_valid_audio_block(self):
        assert _is_content_block({"type": "audio", "data": "AA==", "mimeType": "audio/mp3"}) is True

    def test_valid_resource_block(self):
        assert _is_content_block({
            "type": "resource",
            "resource": {"uri": "x", "mimeType": "application/pdf", "blob": "AA=="},
        }) is True

    def test_valid_resource_link_block(self):
        assert _is_content_block({"type": "resource_link", "uri": "https://example.com"}) is True

    # --- missing companion field → False ---

    def test_text_missing_text_field(self):
        assert _is_content_block({"type": "text"}) is False

    def test_image_missing_mimeType(self):
        assert _is_content_block({"type": "image", "data": "AA=="}) is False

    def test_image_missing_data(self):
        assert _is_content_block({"type": "image", "mimeType": "image/png"}) is False

    def test_resource_missing_resource_field(self):
        assert _is_content_block({"type": "resource"}) is False

    def test_resource_link_missing_uri(self):
        assert _is_content_block({"type": "resource_link"}) is False

    # --- unknown type ---

    def test_unknown_type(self):
        assert _is_content_block({"type": "dir", "name": "x"}) is False

    # --- non-dict inputs ---

    def test_string_is_not_block(self):
        assert _is_content_block("x") is False

    def test_int_is_not_block(self):
        assert _is_content_block(123) is False

    def test_none_is_not_block(self):
        assert _is_content_block(None) is False

    def test_empty_dict(self):
        assert _is_content_block({}) is False


# ---------------------------------------------------------------------------
# _normalize_content tests
# ---------------------------------------------------------------------------

class TestNormalizeContent:

    # 1. non-list dict result → single text block, round-trips back
    def test_dict_result_becomes_single_text_block(self):
        original = {"ticker": "AAPL", "price": 200, "pe": 30.5}
        result = _normalize_content(original)
        assert_all_valid_content_blocks(result)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert json.loads(result[0]["text"]) == original

    # 2. bare data list — the actual reported multi-ticker bug
    def test_multi_ticker_bare_list_wrapped(self):
        """get_stock_detail tickers= 멀티티커 버그 회귀 — 원시 dict 리스트가 text 블록으로 래핑."""
        rows = [{"ticker": "TREX", "price": 1}, {"ticker": "CPRT", "price": 2}]
        result = _normalize_content(rows)
        assert_all_valid_content_blocks(result)
        assert len(result) == 2
        for block, original_row in zip(result, rows):
            assert block["type"] == "text"
            assert json.loads(block["text"]) == original_row

    # 3. pre-formed content blocks pass through UNCHANGED (identity)
    def test_valid_blocks_pass_through_unchanged(self):
        image_block = {"type": "image", "data": "AA==", "mimeType": "image/png"}
        text_block = {"type": "text", "text": "meta"}
        resource_block = {
            "type": "resource",
            "resource": {"uri": "x", "mimeType": "application/pdf", "blob": "AA=="},
        }
        input_list = [image_block, text_block, resource_block]
        result = _normalize_content(input_list)
        assert_all_valid_content_blocks(result)
        assert len(result) == 3
        # identity — same objects (passed through, not re-serialised)
        assert result[0] == image_block
        assert result[1] == text_block
        assert result[2] == resource_block

    # 4. malformed/ambiguous dicts must be WRAPPED, not passed through
    def test_malformed_text_type_no_text_field_is_wrapped(self):
        bad = {"type": "text"}           # type matches but companion missing
        result = _normalize_content([bad])
        assert_all_valid_content_blocks(result)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert json.loads(result[0]["text"]) == bad

    def test_malformed_image_missing_mimeType_is_wrapped(self):
        bad = {"type": "image", "data": "AA=="}   # mimeType absent
        result = _normalize_content([bad])
        assert_all_valid_content_blocks(result)
        assert json.loads(result[0]["text"]) == bad

    def test_list_files_row_is_wrapped(self):
        """list_files 핸들러 반환 행(type=dir)은 content type이 아님 → 래핑."""
        bad = {"type": "dir", "name": "x"}
        result = _normalize_content([bad])
        assert_all_valid_content_blocks(result)
        assert json.loads(result[0]["text"]) == bad

    # 5. non-JSON-serialisable values must NOT raise and yield valid text block
    def test_datetime_date_no_exception(self):
        rows = [{"d": datetime.date(2026, 6, 3)}]
        # Must not raise
        result = _normalize_content(rows)
        assert_all_valid_content_blocks(result)
        assert len(result) == 1
        parsed = json.loads(result[0]["text"])
        # default=str converts date to its str representation
        assert "2026-06-03" in parsed["d"]

    def test_decimal_no_exception(self):
        rows = [{"v": decimal.Decimal("1.5")}]
        result = _normalize_content(rows)
        assert_all_valid_content_blocks(result)
        assert len(result) == 1
        parsed = json.loads(result[0]["text"])
        assert parsed["v"] == "1.5"

    # 6. edge cases
    def test_empty_list_returns_empty_list(self):
        result = _normalize_content([])
        assert result == []

    def test_none_returns_single_null_text_block(self):
        result = _normalize_content(None)
        assert_all_valid_content_blocks(result)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "null"

    # extra: ensure_ascii=False — Korean characters preserved
    def test_korean_text_not_ascii_escaped(self):
        data = {"이름": "삼성전자"}
        result = _normalize_content(data)
        assert_all_valid_content_blocks(result)
        assert "삼성전자" in result[0]["text"]
