"""Tests for _resolve_corp_code — dart_corp_map fallback to corp_codes."""
import pytest
from unittest.mock import AsyncMock, patch


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Unit tests (non-live, always run)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FAST_MAP = {"005930": "00126380"}
FULL_MAP = {"064400": {"corp_code": "00139834", "corp_name": "LG씨엔에스"}}


@pytest.fixture(autouse=True)
def patch_corp_maps():
    """Patch both map loaders so no file I/O or network is needed."""
    with (
        patch("kis_api.dart.get_dart_corp_map", new=AsyncMock(return_value=FAST_MAP)),
        patch("kis_api.dart.load_corp_codes", new=AsyncMock(return_value=FULL_MAP)),
    ):
        yield


@pytest.mark.asyncio
async def test_resolve_fast_path_str_shape():
    """005930 is in dart_corp_map (str value) — fast path returns it directly."""
    from kis_api.dart import _resolve_corp_code
    result = await _resolve_corp_code("005930")
    assert result == "00126380"


@pytest.mark.asyncio
async def test_resolve_fallback_dict_shape():
    """064400 (LG CNS) absent from dart_corp_map, present in corp_codes (dict value)."""
    from kis_api.dart import _resolve_corp_code
    result = await _resolve_corp_code("064400")
    assert result == "00139834"


@pytest.mark.asyncio
async def test_resolve_neither_map():
    """Unknown ticker returns empty string."""
    from kis_api.dart import _resolve_corp_code
    result = await _resolve_corp_code("999999")
    assert result == ""


@pytest.mark.asyncio
async def test_resolve_empty_ticker():
    """Empty ticker short-circuits before any map lookup."""
    from kis_api.dart import _resolve_corp_code
    result = await _resolve_corp_code("")
    assert result == ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Live test (requires --run-live + DART_API_KEY)
# Live-validated 2026-06-16: 064400 임원·주요주주 보고서 확인됨
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.live
@pytest.mark.asyncio
async def test_live_list_disclosures_lg_cns():
    """064400 (LG CNS) disclosure_list returns ≥1 disclosure including 6/12 임원 보고서.

    Root-cause fix verification: before fix, get_dart_corp_map returned {} for 064400
    (not in dart_corp_map.json 211 entries), so list_disclosures_for_ticker returned [].
    After fix, corp_codes.json fallback resolves to 00139834 and DART list.json returns filings.
    """
    from kis_api.dart import list_disclosures_for_ticker

    disclosures = await list_disclosures_for_ticker("064400", days=10)

    assert len(disclosures) >= 1, (
        "064400 should have ≥1 disclosure in last 10 days (6/12 임원·주요주주 보고서 확인됨)"
    )

    rcept_nos = [d.get("rcept_no", "") for d in disclosures]
    assert "20260612000483" in rcept_nos, (
        f"Expected rcept_no 20260612000483 (6/12 임원·주요주주특정증권등소유상황보고서) "
        f"in results. Got: {rcept_nos}"
    )
