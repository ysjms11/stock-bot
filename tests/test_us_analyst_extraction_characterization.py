"""Characterization safety net for Phase B extraction of 7 US-analyst jobs
from main_pkg/telegram_bot.py into main_pkg/jobs/.

Three test groups:
 A) Golden tests for PURE helpers (exact current outputs)
 B) Import-presence + coroutine-kind assertions for all 7 jobs + 5 helpers
 C) Free-name (LOAD_GLOBAL) resolution check — catches missing imports after move

Rollback: rm tests/test_us_analyst_extraction_characterization.py
"""

import asyncio
import dis
import builtins
import sys
import types

import pytest

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Telegram stub (matches test_keyboard.py pattern; PTB 21.10 installed but stubs
# prevent real network/bot init at import time)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _install_telegram_stubs():
    """Install minimal telegram stubs so telegram_bot.py can be imported."""
    if "telegram" not in sys.modules:
        telegram_stub = types.ModuleType("telegram")

        class _FakeReplyKeyboardMarkup:
            def __init__(self, keyboard, **kwargs):
                self.keyboard = keyboard
                self.resize_keyboard = kwargs.get("resize_keyboard", False)

        telegram_stub.Update = object
        telegram_stub.ReplyKeyboardMarkup = _FakeReplyKeyboardMarkup
        sys.modules["telegram"] = telegram_stub

    if "telegram.ext" not in sys.modules:
        ext_stub = types.ModuleType("telegram.ext")
        ext_stub.Application = object
        ext_stub.CommandHandler = object
        ext_stub.MessageHandler = object
        ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
        ext_stub.filters = type("filters", (), {"TEXT": None, "Regex": lambda s, x: x})()
        ext_stub.TypeHandler = object
        ext_stub.ApplicationHandlerStop = type(
            "ApplicationHandlerStop", (Exception,), {}
        )
        sys.modules["telegram.ext"] = ext_stub


_install_telegram_stubs()

# Import the module under test (once, cached by sys.modules)
import main_pkg.telegram_bot as _tb  # noqa: E402

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Import symbols needed by tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from main_pkg.telegram_bot import (  # noqa: E402
    # pure helpers
    _md_escape,
    _rating_elapsed,
    _detect_new_downgrades,
    _format_urgent_downgrade_alert,
    # impure (needs DB) — tested only for presence / callability
    _format_daily_rating_summary,
    # module constants used by helpers
    _US_SELL_RATINGS,
    _US_DOWNGRADE_PT_THRESHOLD,
    # the 7 jobs
    daily_us_rating_scan,
    weekly_us_ratings_universe_scan,
    weekly_us_analyst_sync,
    hourly_us_holdings_check,
    weekly_us_analyst_report,
    weekly_sanity_check,
    weekly_log_rotate,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Group A — Golden tests for PURE helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestMdEscapeGoldens:
    """_md_escape: deterministic, no I/O — pure."""

    def test_none_returns_dash(self):
        assert _md_escape(None) == "—"

    def test_empty_string_returns_dash(self):
        assert _md_escape("") == "—"

    def test_plain_string_unchanged(self):
        assert _md_escape("hello") == "hello"

    def test_numeric_string_unchanged(self):
        assert _md_escape("123") == "123"

    def test_underscore_escaped(self):
        assert _md_escape("under_score") == "under\\_score"

    def test_star_escaped(self):
        assert _md_escape("star*world") == "star\\*world"

    def test_backslash_escaped(self):
        # input with one backslash → two backslashes in output
        assert _md_escape("back\\slash") == "back\\\\slash"

    def test_bracket_escaped(self):
        assert _md_escape("[link]") == "\\[link]"

    def test_backtick_escaped(self):
        assert _md_escape("backtick`tick") == "backtick\\`tick"

    def test_all_special_chars_escaped(self):
        # a_b*c[d`e\f  (backslash first, then others)
        assert _md_escape("a_b*c[d`e\\f") == "a\\_b\\*c\\[d\\`e\\\\f"

    def test_mixed_string_golden(self):
        assert _md_escape("mixed_test *here* [ok] `code`") == (
            "mixed\\_test \\*here\\* \\[ok] \\`code\\`"
        )

    def test_non_string_coerced(self):
        # int → str → no special chars → unchanged
        assert _md_escape(42) == "42"

    def test_returns_string(self):
        assert isinstance(_md_escape("test"), str)


class TestRatingElapsedGoldens:
    """_rating_elapsed: pure for empty/bad inputs; uses clock for date math.

    Classification: IMPURE (calls datetime.now(KST) internally).
    We test the deterministic outputs (empty/bad) and the FORMAT of a real date
    without pinning the exact day count.
    """

    def test_empty_returns_empty(self):
        assert _rating_elapsed("") == ""

    def test_none_returns_empty(self):
        assert _rating_elapsed(None) == ""

    def test_bad_format_returns_empty(self):
        assert _rating_elapsed("not-a-date") == ""

    def test_valid_date_format(self):
        result = _rating_elapsed("2026-01-15")
        # Must match " (YYYY-MM-DD, Nd 전)" pattern
        assert result.startswith(" (2026-01-15,")
        assert result.endswith("일 전)")
        assert result.startswith(" (")

    def test_valid_date_contains_date(self):
        result = _rating_elapsed("2025-06-01")
        assert "2025-06-01" in result

    def test_valid_date_returns_string(self):
        result = _rating_elapsed("2026-03-01")
        assert isinstance(result, str)
        assert len(result) > 0


class TestDetectNewDowngradesGoldens:
    """_detect_new_downgrades: deterministic, no I/O — pure."""

    def _make_event(self, action="Maintains", rating_new="Buy", rating_old="Buy",
                    pt_change_pct=None):
        return {
            "action": action,
            "rating_new": rating_new,
            "rating_old": rating_old,
            "pt_change_pct": pt_change_pct,
        }

    def test_empty_list_returns_empty(self):
        assert _detect_new_downgrades("AAPL", []) == []

    def test_action_downgrades_detected(self):
        e = self._make_event(action="Downgrades")
        result = _detect_new_downgrades("AAPL", [e])
        assert len(result) == 1
        assert result[0] is e

    def test_new_sell_old_buy_detected(self):
        e = self._make_event(rating_new="Sell", rating_old="Buy")
        assert len(_detect_new_downgrades("AAPL", [e])) == 1

    def test_new_strong_sell_old_buy_detected(self):
        e = self._make_event(rating_new="Strong Sell", rating_old="Buy")
        assert len(_detect_new_downgrades("AAPL", [e])) == 1

    def test_pt_drop_beyond_threshold_detected(self):
        e = self._make_event(pt_change_pct=_US_DOWNGRADE_PT_THRESHOLD - 1.0)
        assert len(_detect_new_downgrades("AAPL", [e])) == 1

    def test_pt_drop_at_threshold_not_detected(self):
        # pt_change_pct must be STRICTLY less than threshold (-15.0)
        e = self._make_event(pt_change_pct=_US_DOWNGRADE_PT_THRESHOLD)
        assert len(_detect_new_downgrades("AAPL", [e])) == 0

    def test_normal_maintains_not_detected(self):
        e = self._make_event(action="Maintains", rating_new="Buy", rating_old="Buy",
                             pt_change_pct=-5.0)
        assert len(_detect_new_downgrades("AAPL", [e])) == 0

    def test_sell_to_sell_not_detected(self):
        # old already in _US_SELL_RATINGS → no new downgrade
        e = self._make_event(action="Upgrades", rating_new="Strong Sell",
                             rating_old="Sell")
        assert len(_detect_new_downgrades("AAPL", [e])) == 0

    def test_mixed_list_returns_correct_subset(self):
        e_dg = self._make_event(action="Downgrades")
        e_ok = self._make_event(action="Maintains", pt_change_pct=-3.0)
        e_pt = self._make_event(pt_change_pct=-20.0)
        result = _detect_new_downgrades("AAPL", [e_dg, e_ok, e_pt])
        assert len(result) == 2
        assert e_dg in result
        assert e_pt in result
        assert e_ok not in result

    def test_case_insensitive_action(self):
        # action "downgrades" lowercase should match
        e = self._make_event(action="downgrades")
        assert len(_detect_new_downgrades("AAPL", [e])) == 1

    def test_sell_ratings_constant(self):
        assert "Sell" in _US_SELL_RATINGS
        assert "Strong Sell" in _US_SELL_RATINGS

    def test_threshold_constant(self):
        assert _US_DOWNGRADE_PT_THRESHOLD == -15.0


class TestFormatUrgentDowngradeAlertGoldens:
    """_format_urgent_downgrade_alert: pure (calls _md_escape + _rating_elapsed only)."""

    def _make_event(self, tier_s=False, watched=False, action="Downgrades",
                    rating_new="Sell", rating_old="Buy", pt_change_pct=-20.0,
                    firm="Goldman", stars=4.5, pt_now=150.0, date="2026-06-10"):
        return {
            "action": action, "rating_new": rating_new, "rating_old": rating_old,
            "pt_change_pct": pt_change_pct, "firm": firm, "stars": stars,
            "watched": watched, "tier_s": tier_s, "pt_now": pt_now, "date": date,
        }

    def test_non_watched_header(self):
        e = self._make_event()
        result = _format_urgent_downgrade_alert("AAPL", [e], [e])
        first_line = result.split("\n")[0]
        assert "⚠️" in first_line
        assert "AAPL" in first_line
        assert "일반" in first_line

    def test_tier_s_single_header(self):
        e = self._make_event(tier_s=True, watched=True)
        result = _format_urgent_downgrade_alert("NVDA", [e], [e])
        first_line = result.split("\n")[0]
        assert "🚨🚨" in first_line
        assert "엘리트" in first_line

    def test_tier_a_single_header(self):
        e = self._make_event(tier_s=False, watched=True)
        result = _format_urgent_downgrade_alert("TSLA", [e], [e])
        first_line = result.split("\n")[0]
        assert "🚨" in first_line
        assert "톱" in first_line

    def test_result_contains_firm(self):
        e = self._make_event(firm="Morgan Stanley")
        result = _format_urgent_downgrade_alert("AAPL", [e], [e])
        assert "Morgan Stanley" in result

    def test_result_contains_rating_change(self):
        e = self._make_event(rating_old="Buy", rating_new="Sell")
        result = _format_urgent_downgrade_alert("AAPL", [e], [e])
        assert "Buy" in result
        assert "Sell" in result

    def test_result_is_string_under_4096(self):
        e = self._make_event()
        result = _format_urgent_downgrade_alert("AAPL", [e], [e])
        assert isinstance(result, str)
        assert len(result) < 4096

    def test_empty_events_header_is_general(self):
        result = _format_urgent_downgrade_alert("TSLA", [], [])
        assert "TSLA" in result.split("\n")[0]

    def test_two_tier_s_double_exclamation_header(self):
        e1 = self._make_event(tier_s=True, watched=True, firm="Goldman")
        e2 = self._make_event(tier_s=True, watched=True, firm="MS")
        result = _format_urgent_downgrade_alert("AAPL", [e1, e2], [e1, e2])
        first_line = result.split("\n")[0]
        assert "🚨🚨🚨" in first_line
        assert "2명" in first_line

    def test_tier_s_and_tier_a_together(self):
        es = self._make_event(tier_s=True, watched=True, firm="Goldman")
        ea = self._make_event(tier_s=False, watched=True, firm="MS")
        result = _format_urgent_downgrade_alert("AAPL", [es, ea], [es, ea])
        first_line = result.split("\n")[0]
        assert "🚨🚨" in first_line
        assert "엘리트+톱" in first_line


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Group B — Import presence + coroutine kind
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_SEVEN_JOBS = [
    "daily_us_rating_scan",
    "weekly_us_ratings_universe_scan",
    "weekly_us_analyst_sync",
    "hourly_us_holdings_check",
    "weekly_us_analyst_report",
    "weekly_sanity_check",
    "weekly_log_rotate",
]

_FIVE_HELPERS = [
    "_detect_new_downgrades",
    "_md_escape",
    "_rating_elapsed",
    "_format_urgent_downgrade_alert",
    "_format_daily_rating_summary",
]


@pytest.mark.parametrize("name", _SEVEN_JOBS)
def test_job_importable(name):
    """All 7 jobs must be importable from telegram_bot."""
    fn = getattr(_tb, name, None)
    assert fn is not None, f"{name} missing from main_pkg.telegram_bot"
    assert callable(fn), f"{name} is not callable"


@pytest.mark.parametrize("name", _SEVEN_JOBS)
def test_job_is_coroutine(name):
    """All 7 jobs must be async (coroutine functions)."""
    fn = getattr(_tb, name)
    assert asyncio.iscoroutinefunction(fn), f"{name} is not async"


@pytest.mark.parametrize("name", _FIVE_HELPERS)
def test_helper_importable(name):
    """All 5 helpers must be importable from telegram_bot."""
    fn = getattr(_tb, name, None)
    assert fn is not None, f"{name} missing from main_pkg.telegram_bot"
    assert callable(fn), f"{name} is not callable"


@pytest.mark.parametrize("name", ["_md_escape", "_rating_elapsed",
                                   "_detect_new_downgrades", "_format_urgent_downgrade_alert"])
def test_helper_is_sync(name):
    """Pure/impure helpers that are sync (not coroutines)."""
    fn = getattr(_tb, name)
    assert not asyncio.iscoroutinefunction(fn), f"{name} should be sync"


def test_format_daily_rating_summary_is_sync():
    """_format_daily_rating_summary is sync (queries DB internally)."""
    assert not asyncio.iscoroutinefunction(_format_daily_rating_summary)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Group C — Free-name (LOAD_GLOBAL) resolution check
#
# After Phase B move, re-point this check at the NEW module. Any name that
# appears in a LOAD_GLOBAL instruction but is absent from __globals__ will
# be caught HERE before a runtime NameError occurs.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_BUILTIN_NAMES = frozenset(dir(builtins))


def _get_load_globals_recursive(code_obj, visited=None):
    """Return all names loaded via LOAD_GLOBAL in a code object and its nested consts."""
    if visited is None:
        visited = set()
    if id(code_obj) in visited:
        return set()
    visited.add(id(code_obj))
    result = set()
    for instr in dis.get_instructions(code_obj):
        if instr.opname == "LOAD_GLOBAL":
            result.add(instr.argval)
    for const in code_obj.co_consts:
        if hasattr(const, "co_names"):  # nested code object (inner function)
            result |= _get_load_globals_recursive(const, visited)
    return result


def _assert_all_globals_resolve(fn, fn_name: str, module=None):
    """Assert every LOAD_GLOBAL name (non-builtin) resolves in the function's __globals__.

    Pass `module` after Phase B move to redirect the check to the new module's globals.
    """
    globs = (module.__dict__ if module is not None else fn.__globals__)
    load_globals = _get_load_globals_recursive(fn.__code__)
    missing = sorted(
        n for n in load_globals
        if not n.startswith("__")
        and n not in _BUILTIN_NAMES
        and n not in globs
    )
    assert not missing, (
        f"{fn_name}: LOAD_GLOBAL names missing from module globals after move:\n"
        f"  {missing}\n"
        f"  → add these imports to the new module."
    )


@pytest.mark.parametrize("name", _SEVEN_JOBS + _FIVE_HELPERS)
def test_load_global_resolution(name):
    """Every LOAD_GLOBAL reference in the function resolves in its current module globals.

    Pre-move baseline: all must pass against telegram_bot.
    Post-move: re-run after updating the import path of `_tb` to the new module.
    """
    fn = getattr(_tb, name)
    _assert_all_globals_resolve(fn, name)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dependency map documentation test
# (Asserts the known dependency structure — will catch accidental changes)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Expected LOAD_GLOBAL dependencies per function (non-builtins, non-dunder)
# Captured 2026-06-13 from pre-move telegram_bot.py
_EXPECTED_GLOBALS = {
    "daily_us_rating_scan": {
        "_format_daily_rating_summary", "_safe_send", "asyncio", "db_write_lock"
    },
    "weekly_us_ratings_universe_scan": {"CHAT_ID", "asyncio", "db_write_lock"},
    "weekly_us_analyst_sync": {"CHAT_ID", "asyncio", "db_write_lock"},
    "hourly_us_holdings_check": {
        "ET", "_detect_new_downgrades", "_format_urgent_downgrade_alert",
        "_safe_send", "asyncio", "datetime", "db_write_lock"
    },
    "weekly_us_analyst_report": {
        "KST", "_md_escape", "_safe_send", "datetime", "timedelta"
    },
    "weekly_sanity_check": {
        "CHAT_ID", "KST", "UNIVERSE_FILE", "_DATA_DIR", "_KRX_HOLIDAYS",
        "_is_krx_business_day", "_safe_send", "datetime", "os", "timedelta"
    },
    "weekly_log_rotate": set(),
    "_detect_new_downgrades": {"_US_DOWNGRADE_PT_THRESHOLD", "_US_SELL_RATINGS"},
    "_md_escape": set(),
    "_rating_elapsed": {"KST", "datetime"},
    "_format_urgent_downgrade_alert": {"_md_escape", "_rating_elapsed"},
    "_format_daily_rating_summary": {"KST", "_md_escape", "_rating_elapsed", "datetime"},
}


@pytest.mark.parametrize("name", list(_EXPECTED_GLOBALS))
def test_dependency_map_stable(name):
    """Assert LOAD_GLOBAL set matches the captured dependency map.

    Failing here means the function's global dependencies changed — update the
    map AND the Phase B import list accordingly.
    """
    fn = getattr(_tb, name)
    actual = _get_load_globals_recursive(fn.__code__)
    non_builtin = {n for n in actual if not n.startswith("__") and n not in _BUILTIN_NAMES}
    expected = _EXPECTED_GLOBALS[name]
    assert non_builtin == expected, (
        f"{name}: dependency map changed.\n"
        f"  expected: {sorted(expected)}\n"
        f"  actual:   {sorted(non_builtin)}\n"
        f"  added:    {sorted(non_builtin - expected)}\n"
        f"  removed:  {sorted(expected - non_builtin)}"
    )
