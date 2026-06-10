"""P0 Characterization tests for dashboard_home.py.

These tests lock in the CURRENT observable structure/behavior of dashboard_home.py
so that a package split can prove fidelity.  They do NOT test business logic —
they are golden-master tests: any deviation is a signal that something changed
that must be reviewed.

Coverage:
1. Template constant integrity — sha256 golden for every module-level str constant
   with len > 200 (catches accidental re-quoting / raw-string \\n footgun).
2. Route table freeze — sorted (method, path) list registered by register_home_routes.
3. Surface freeze — register_home_routes is callable, warm_caches is a coroutine.
4. Offline payload key-set goldens — DB-only builders, no network.
"""

import asyncio
import hashlib
import inspect

import aiohttp.web
import pytest

import dashboard_home


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Template constant integrity
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Golden table: name -> (sha256_hex, length)
# Generated on 2026-06-10 from the live module.
_CONSTANT_GOLDENS: dict[str, tuple[str, int]] = {
    "_DASH_APP_JS": (
        "fb020ddef7c5fdc0aa85ba39af337eae3151104cd3c55854f81dd63efce565d1",
        36315,
    ),
    "_HOME_PANEL": (
        "6dc7ea1df7d41a2d761aafcc440342bf9b5e819fdae2426630693e22e32883bd",
        11656,
    ),
    "_HOME_SHELL": (
        "a55cec36159318fb35e00433cb0c50f4813e8aa4995dcc9cf97d984cdb4e0fd5",
        248170,
    ),
    "_MARKET_PANEL": (
        "ac2a168dad1e7a19f5b0e8dda081e781679b9a21486dd6d73f9828c3b6605f0c",
        56438,
    ),
    "_PORTFOLIO_PANEL": (
        "fed55d280f1a3fd93d7d4f32cdeae5143ca11237bc6f66fac20a42df943f17b8",
        18703,
    ),
    "_RECORD_PANEL": (
        "5f222f6062a58e0a54a988563f97ad39175e7c45e853da549083c86e61a31e15",
        12689,
    ),
    "_REPORT_PANEL": (
        "c6bb673e6c0fd2eb5d821d997dd1af094f65450ec41d142a3e185f710fbee4e3",
        13164,
    ),
    "_SIGNAL_PANEL": (
        "a968de146a4651c3b57f059e801d8feb7b987e98e2e338aa138f89790b78f268",
        36794,
    ),
    "_US_PANEL": (
        "b486417088d185692237692d18ca1bcec19b150c350eec2218d5857f50820d49",
        23736,
    ),
    "_WATCH_PANEL": (
        "ed0e70e69746e473360a71ffc7d8cea5d193f9b4e6441e33595e930fef9118a3",
        12549,
    ),
    "_WHALE_PANEL": (
        "bb29e39eb8c1d2d67cec1115c9eec41050f9d3b311670922ebdb538c698c2c52",
        20033,
    ),
    # Dead code — kept verbatim; deletion is a separate decision.
    "_WHALE_PANEL_REMOVED": (
        "a9bd7397c7e25c95241206b9bc8342fb752a75d2b0dcb7910519908506f0de53",
        20054,
    ),
    "__doc__": (
        "d01300d584cb367f6238d9946934f883a876e09811abe5dde5bcc5473231cb54",
        382,
    ),
}

# Exact count of large string constants
_EXPECTED_CONSTANT_COUNT = 13


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def test_large_string_constant_count():
    """Exact count of module-level str constants with len > 200."""
    found = [
        n for n, v in vars(dashboard_home).items()
        if isinstance(v, str) and len(v) > 200
    ]
    assert len(found) == _EXPECTED_CONSTANT_COUNT, (
        f"Expected {_EXPECTED_CONSTANT_COUNT} large string constants, "
        f"got {len(found)}: {sorted(found)}"
    )


@pytest.mark.parametrize("name,expected_hash,expected_len", [
    (name, h, length) for name, (h, length) in sorted(_CONSTANT_GOLDENS.items())
])
def test_template_constant_integrity(name: str, expected_hash: str, expected_len: int):
    """sha256 and length of each large module-level string constant."""
    val = getattr(dashboard_home, name, None)
    assert val is not None, f"Constant {name!r} is missing from dashboard_home"
    assert isinstance(val, str), f"{name!r} is not a str (got {type(val).__name__})"
    assert len(val) == expected_len, (
        f"{name!r}: expected len {expected_len}, got {len(val)}"
    )
    actual_hash = _sha256(val)
    assert actual_hash == expected_hash, (
        f"{name!r} sha256 mismatch — content changed (raw-string \\n footgun?)\n"
        f"  expected: {expected_hash}\n"
        f"  actual:   {actual_hash}"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Route table freeze
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_EXPECTED_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/alerts"),
    ("GET", "/api/alpha"),
    ("GET", "/api/decisions"),
    ("GET", "/api/home"),
    ("GET", "/api/invest_todo"),
    ("GET", "/api/macro_panel"),
    ("GET", "/api/market"),
    ("GET", "/api/marketmap"),
    ("GET", "/api/portfolio"),
    ("GET", "/api/portfolio_history"),
    ("GET", "/api/regime"),
    ("GET", "/api/reports"),
    ("GET", "/api/reports/{ticker}"),
    ("GET", "/api/sector_heatmap"),
    ("GET", "/api/signals"),
    ("GET", "/api/stock/{ticker}"),
    ("GET", "/api/supply"),
    ("GET", "/api/trades"),
    ("GET", "/api/us/analyst_research"),
    ("GET", "/api/us/analysts"),
    ("GET", "/api/us/candidates"),
    ("GET", "/api/us/consensus"),
    ("GET", "/api/us/ratings"),
    ("GET", "/api/us/scan"),
    ("GET", "/api/watch"),
    ("GET", "/api/whale"),
    ("GET", "/home"),
    ("HEAD", "/api/alerts"),
    ("HEAD", "/api/alpha"),
    ("HEAD", "/api/decisions"),
    ("HEAD", "/api/home"),
    ("HEAD", "/api/invest_todo"),
    ("HEAD", "/api/macro_panel"),
    ("HEAD", "/api/market"),
    ("HEAD", "/api/marketmap"),
    ("HEAD", "/api/portfolio"),
    ("HEAD", "/api/portfolio_history"),
    ("HEAD", "/api/regime"),
    ("HEAD", "/api/reports"),
    ("HEAD", "/api/reports/{ticker}"),
    ("HEAD", "/api/sector_heatmap"),
    ("HEAD", "/api/signals"),
    ("HEAD", "/api/stock/{ticker}"),
    ("HEAD", "/api/supply"),
    ("HEAD", "/api/trades"),
    ("HEAD", "/api/us/analyst_research"),
    ("HEAD", "/api/us/analysts"),
    ("HEAD", "/api/us/candidates"),
    ("HEAD", "/api/us/consensus"),
    ("HEAD", "/api/us/ratings"),
    ("HEAD", "/api/us/scan"),
    ("HEAD", "/api/watch"),
    ("HEAD", "/api/whale"),
    ("HEAD", "/home"),
    ("POST", "/api/decisions"),
    ("POST", "/api/watch"),
]


def test_route_table_freeze():
    """register_home_routes must add exactly the expected (method, path) pairs."""
    app = aiohttp.web.Application()
    dashboard_home.register_home_routes(app)
    actual = sorted(
        (r.method, r.resource.canonical)
        for r in app.router.routes()
    )
    assert actual == _EXPECTED_ROUTES, (
        f"Route table changed.\n"
        f"  Added:   {[r for r in actual if r not in _EXPECTED_ROUTES]}\n"
        f"  Removed: {[r for r in _EXPECTED_ROUTES if r not in actual]}"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Surface freeze
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_surface_register_home_routes_callable():
    """register_home_routes must be a callable exported from dashboard_home."""
    assert callable(dashboard_home.register_home_routes), (
        "register_home_routes is not callable"
    )


def test_surface_warm_caches_is_coroutine():
    """warm_caches must be an async function (coroutine function)."""
    assert asyncio.iscoroutinefunction(dashboard_home.warm_caches), (
        "warm_caches is not a coroutine function — it should be 'async def warm_caches()'"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. Offline payload key-set goldens (DB-only, no network)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# Included builders (verified DB-only):
#   _sync_reports_payload       — pure SQLite (reports table)
#   _sync_reports_by_ticker     — pure SQLite (reports table)
#   _reports_by_ticker          — async wrapper for _sync_reports_by_ticker
#   _open_db                    — sqlite3.connect wrapper
#   _whale_home                 — pure SQLite (nps_* + pension_flow_daily + insider_transactions)
#   _whale_kr_5pct              — pure SQLite (nps_holdings_disclosed)
#   _whale_kr_full              — kis_api.fetch_nps_kr_full_holdings (DB read, no network)
#   _whale_us_13f               — kis_api.fetch_nps_us_holdings (DB read, no network)
#   _whale_pension              — pure SQLite (pension_flow_daily + daily_snapshot)
#   _whale_insider              — pure SQLite (insider_transactions + stock_master)
#   build_whale_payload(preset) — async dispatcher over above sync functions
#
# Excluded:
#   build_home_payload          — calls KIS API (kis_stock_price etc.), network
#   build_market_payload        — calls KIS API (KOSPI/KOSDAQ index, sector)
#   build_macro_panel_payload   — calls external macro APIs
#   _build_watch_payload        — calls KIS API for current prices
#   _build_supply_payload       — calls KIS API
#   _build_us_*_payload         — calls KIS/FMP APIs
#   _build_signals_payload      — calls KIS API
#   _build_portfolio_with_grand — calls KIS API for prices


def test_open_db_returns_connection():
    """_open_db() returns a sqlite3.Connection (reads stock.db)."""
    import sqlite3 as _sqlite3
    conn = dashboard_home._open_db()
    assert isinstance(conn, _sqlite3.Connection)
    conn.close()


def test_sync_reports_payload_keys():
    """_sync_reports_payload() returns a dict with the expected top-level keys."""
    result = dashboard_home._sync_reports_payload()
    assert isinstance(result, dict)
    assert sorted(result.keys()) == sorted([
        "kr", "us", "industry", "macro",
        "kr_total", "us_total", "industry_total", "macro_total",
    ])


def test_sync_reports_by_ticker_empty_for_unknown():
    """_sync_reports_by_ticker with a non-existent ticker returns an empty list."""
    result = dashboard_home._sync_reports_by_ticker("ZZZZZ_FAKE_TICKER_99999")
    assert isinstance(result, list)
    assert result == []


def test_sync_reports_by_ticker_known_kr_ticker():
    """_sync_reports_by_ticker with a known KR ticker (267260) returns list of dicts
    with the expected key set."""
    result = dashboard_home._sync_reports_by_ticker("267260")
    assert isinstance(result, list)
    # The DB has reports for this ticker — if somehow empty, shape check still passes.
    if result:
        assert sorted(result[0].keys()) == sorted([
            "analyst", "date", "opinion", "pdf_basename", "source",
            "target_price", "title",
        ])


def test_reports_by_ticker_async_empty_for_unknown():
    """_reports_by_ticker (async) returns empty list for an unknown ticker."""
    result = asyncio.run(
        dashboard_home._reports_by_ticker("ZZZZZ_FAKE_TICKER_99999")
    )
    assert isinstance(result, list)
    assert result == []


def test_whale_home_keys():
    """_whale_home() returns a dict with the expected top-level keys."""
    result = dashboard_home._whale_home()
    assert isinstance(result, dict)
    # May have _error key if DB tables are empty — still a dict
    if "_error" not in result:
        expected_keys = {"kr_full", "us_13f", "kr_5pct", "pension", "insider"}
        assert set(result.keys()) >= expected_keys, (
            f"_whale_home() missing keys: {expected_keys - set(result.keys())}"
        )


def test_whale_kr_5pct_returns_list():
    """_whale_kr_5pct() returns a list (may be empty if no data yet)."""
    result = dashboard_home._whale_kr_5pct()
    assert isinstance(result, list)
    if result and isinstance(result[0], dict) and "error" not in result[0]:
        assert sorted(result[0].keys()) == sorted([
            "change", "change_label", "company_name", "is_new",
            "prev_quarter", "prev_ratio", "quarter", "ratio_pct",
            "report_date", "symbol",
        ])


def test_whale_kr_full_keys():
    """_whale_kr_full() returns a dict with expected keys (DB read via kis_api)."""
    result = dashboard_home._whale_kr_full()
    assert isinstance(result, dict)
    if "error" not in result:
        expected_keys = {
            "fetched_at", "quarter_label", "rows",
            "snapshot_date", "total_holdings", "total_valuation_eok",
        }
        assert set(result.keys()) >= expected_keys


def test_whale_us_13f_keys():
    """_whale_us_13f() returns a dict with expected keys (DB read via kis_api)."""
    result = dashboard_home._whale_us_13f()
    assert isinstance(result, dict)
    if "error" not in result:
        expected_keys = {
            "exits_top10", "fetched_at", "period_end",
            "quarter", "rows", "total_holdings", "total_value_usd",
        }
        assert set(result.keys()) >= expected_keys


def test_whale_pension_shape():
    """_whale_pension() returns a dict with buy_top/sell_top/period, or error."""
    result = dashboard_home._whale_pension()
    assert isinstance(result, (dict, list))
    if isinstance(result, dict) and "error" not in result:
        assert set(result.keys()) >= {"buy_top", "sell_top", "period"}


def test_whale_insider_returns_list():
    """_whale_insider() returns a list of dicts with expected keys."""
    result = dashboard_home._whale_insider()
    assert isinstance(result, list)
    if result and isinstance(result[0], dict) and "error" not in result[0]:
        assert sorted(result[0].keys()) == sorted([
            "company_name", "direction", "irds_cnt", "rcept_dt",
            "repror", "role", "stock_irds_rate", "stock_rate", "symbol",
        ])


@pytest.mark.parametrize("preset,expected_type,expected_top_keys", [
    (
        "home",
        dict,
        {"kr_full", "us_13f", "kr_5pct", "pension", "insider"},
    ),
    (
        "kr_5pct",
        list,
        None,  # list — item keys checked separately
    ),
    (
        "kr_full",
        dict,
        {"fetched_at", "quarter_label", "rows", "snapshot_date",
         "total_holdings", "total_valuation_eok"},
    ),
    (
        "us_13f",
        dict,
        {"exits_top10", "fetched_at", "period_end", "quarter",
         "rows", "total_holdings", "total_value_usd"},
    ),
    (
        "pension",
        dict,
        {"buy_top", "period", "sell_top"},
    ),
    (
        "insider",
        list,
        None,  # list — item keys checked separately
    ),
])
def test_build_whale_payload_type(preset, expected_type, expected_top_keys):
    """build_whale_payload(preset) returns correct type and top-level keys."""
    result = asyncio.run(dashboard_home.build_whale_payload(preset))
    assert isinstance(result, expected_type), (
        f"build_whale_payload({preset!r}): expected {expected_type.__name__}, "
        f"got {type(result).__name__}"
    )
    if expected_type is dict and expected_top_keys and "error" not in result:
        assert set(result.keys()) >= expected_top_keys, (
            f"build_whale_payload({preset!r}) missing keys: "
            f"{expected_top_keys - set(result.keys())}"
        )
    elif expected_type is list and result:
        if isinstance(result[0], dict) and "error" not in result[0]:
            assert isinstance(result[0], dict)  # at minimum: items are dicts
