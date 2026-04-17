"""F/M/FCF Phase6 — DART 증분 수집 단위 테스트.

- _parse_rpt_nm: 5 케이스 (분기/반기/사업/기재정정/첨부정정)
- collect_financial_on_disclosure: monkeypatch로 DART 3 API(list/full/shares) mock.
  실제 네트워크 호출 금지.

실행:
    /Users/kreuzer/stock-bot/venv/bin/python -m pytest test_dart_incremental.py -v
"""
import os
import sys
import sqlite3
import asyncio

import pytest

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _parse_rpt_nm 파싱 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━
from kis_api import _parse_rpt_nm  # noqa: E402


class TestParseRptNm:
    def test_quarterly_q1(self):
        period, rtype = _parse_rpt_nm("분기보고서 (2024.03)")
        assert period == "202403"
        assert rtype == "quarterly"

    def test_quarterly_q3(self):
        period, rtype = _parse_rpt_nm("분기보고서 (2024.09)")
        assert period == "202409"
        assert rtype == "quarterly"

    def test_semi_annual(self):
        period, rtype = _parse_rpt_nm("반기보고서 (2024.06)")
        assert period == "202406"
        assert rtype == "semi"

    def test_annual(self):
        period, rtype = _parse_rpt_nm("사업보고서 (2023.12)")
        assert period == "202312"
        assert rtype == "annual"

    def test_jaejeong_rejected(self):
        # 기재정정 — 정정 공시는 skip (None, None)
        period, rtype = _parse_rpt_nm("[기재정정]분기보고서 (2024.03)")
        assert period is None and rtype is None

    def test_cheompu_jeongjeong_rejected(self):
        # 첨부정정 — 정정 공시는 skip
        period, rtype = _parse_rpt_nm("[첨부정정]사업보고서 (2023.12)")
        assert period is None and rtype is None

    def test_wrong_month_quarterly(self):
        # 분기보고서인데 월이 06(반기에 해당) — 정합성 위반, skip
        period, rtype = _parse_rpt_nm("분기보고서 (2024.06)")
        assert period is None and rtype is None

    def test_wrong_month_annual(self):
        # 사업보고서인데 월이 03 — 비정상, skip
        period, rtype = _parse_rpt_nm("사업보고서 (2024.03)")
        assert period is None and rtype is None

    def test_non_periodic_report(self):
        # 주요사항보고서 등 — 정기공시 아님, skip
        period, rtype = _parse_rpt_nm("주요사항보고서 (자기주식취득결정)")
        assert period is None and rtype is None

    def test_empty_string(self):
        period, rtype = _parse_rpt_nm("")
        assert period is None and rtype is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# collect_financial_on_disclosure 통합 테스트 (monkeypatch)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _make_tmp_db(tmp_path) -> str:
    """테스트용 임시 SQLite 파일 경로. 실제 스키마는 _get_db() 호출 시
    db_schema.sql(IF NOT EXISTS)로 자동 생성됨."""
    db_path = tmp_path / "test_stock.db"
    return str(db_path)


def test_collect_incremental_flow(tmp_path, monkeypatch):
    """신규 2건, 중복 1건, 무ticker 1건 → 신규 2건 수집 + alpha_recalc 호출 확인."""
    import db_collector as _dbc
    import kis_api as _kis

    db_path = _make_tmp_db(tmp_path)
    monkeypatch.setattr(_dbc, "DB_PATH", db_path)

    # 스키마 초기화 (db_schema.sql 실행) 후 stock_master + financial_quarterly pre-insert
    init_conn = _dbc._get_db()
    for sym, name in [("005930", "삼성전자"), ("005380", "현대차"),
                      ("000660", "SK하이닉스")]:
        init_conn.execute(
            "INSERT OR IGNORE INTO stock_master(symbol, name, market) VALUES (?,?,?)",
            (sym, name, "kospi"),
        )
    init_conn.execute(
        "INSERT INTO financial_quarterly(symbol, report_period, cfo, fs_source) "
        "VALUES ('005930', '202312', 100000, 'CFS')"
    )
    init_conn.commit()
    init_conn.close()

    # ── Mock 1: search_dart_periodic_new ──
    fake_disclosures = [
        # 신규 1 — 005380(현대차) 2024Q1
        {"corp_code": "00164742", "ticker": "005380", "corp_name": "현대차",
         "rcept_no": "20250515000001", "rcept_dt": "20250515",
         "rpt_nm": "분기보고서 (2024.03)",
         "report_period": "202403", "report_type": "quarterly"},
        # 중복 — 005930 이미 있음
        {"corp_code": "00126380", "ticker": "005930", "corp_name": "삼성전자",
         "rcept_no": "20250515000002", "rcept_dt": "20250515",
         "rpt_nm": "사업보고서 (2023.12)",
         "report_period": "202312", "report_type": "annual"},
        # 신규 2 — 000660(SK하이닉스) 2024Q2
        {"corp_code": "00164779", "ticker": "000660", "corp_name": "SK하이닉스",
         "rcept_no": "20250815000003", "rcept_dt": "20250815",
         "rpt_nm": "반기보고서 (2024.06)",
         "report_period": "202406", "report_type": "semi"},
        # 무ticker — 비상장 지주사
        {"corp_code": "99999999", "ticker": "", "corp_name": "가짜지주",
         "rcept_no": "20250515000004", "rcept_dt": "20250515",
         "rpt_nm": "분기보고서 (2024.03)",
         "report_period": "202403", "report_type": "quarterly"},
        # dedup — 005380 2024Q1 중복 들어옴 (정정 공시 원본 같은 건 여러 line) → seen_pairs 필터
        {"corp_code": "00164742", "ticker": "005380", "corp_name": "현대차",
         "rcept_no": "20250515000099", "rcept_dt": "20250515",
         "rpt_nm": "분기보고서 (2024.03)",
         "report_period": "202403", "report_type": "quarterly"},
    ]

    async def mock_search(days=2, session=None):
        return fake_disclosures

    # ── Mock 2: dart_quarterly_full ──
    full_calls = []

    async def mock_full(corp_code, year, quarter, session=None):
        full_calls.append((corp_code, year, quarter))
        return {
            "report_period": f"{year}{quarter*3:02d}",
            "fs_source": "CFS",
            "revenue": 1000, "cost_of_sales": 700, "gross_profit": 300,
            "operating_profit": 150, "net_income": 100,
            "net_income_parent": 95,
            "sga": 150,
            "current_assets": 500, "total_assets": 2000,
            "current_liab": 400, "total_liab": 800,
            "capital": 100, "total_equity": 1200, "equity_parent": 1100,
            "receivables": 200, "inventory": 150,
            "cfo": 180, "capex": 50, "fcf": 130, "depreciation": 40,
            "shares_out": None,
        }

    # ── Mock 3: dart_shares_outstanding ──
    shares_calls = []

    async def mock_shares(corp_code, year, quarter, session=None):
        shares_calls.append((corp_code, year, quarter))
        return 1_000_000_000  # 10억주

    # ── Mock 4: load_corp_codes ──
    async def mock_load_corp_codes():
        return {
            "005930": {"corp_code": "00126380", "corp_name": "삼성전자"},
            "005380": {"corp_code": "00164742", "corp_name": "현대차"},
            "000660": {"corp_code": "00164779", "corp_name": "SK하이닉스"},
        }

    # ── Mock 5: update_all_alpha_metrics (호출만 확인) ──
    alpha_calls = []

    def mock_alpha(end_period=None, trade_date=None):
        alpha_calls.append({"end_period": end_period, "trade_date": trade_date})
        return {
            "tickers": 2, "success": 2,
            "fscore_filled": 2, "mscore_filled": 2, "fcf_filled": 2,
            "duration_sec": 0.1,
            "end_period": end_period, "trade_date": "20260416",
        }

    monkeypatch.setattr(_kis, "search_dart_periodic_new", mock_search)
    monkeypatch.setattr(_kis, "dart_quarterly_full", mock_full)
    monkeypatch.setattr(_kis, "dart_shares_outstanding", mock_shares)
    monkeypatch.setattr(_kis, "load_corp_codes", mock_load_corp_codes)
    monkeypatch.setattr(_dbc, "update_all_alpha_metrics", mock_alpha)
    # rate limit 무시 (테스트 속도)
    monkeypatch.setattr(_dbc, "_DART_INTERVAL", 0.0)

    # ── 실행 ──
    report = asyncio.run(_dbc.collect_financial_on_disclosure(days=2, max_calls=1000))

    # ── 검증 ──
    # list.json 5건(중복 포함) → dedup 후 4 pair
    assert report["disclosures_found"] == 5
    # 005930/202312 중복
    assert report["already_in_db"] == 1
    # 99999999 무ticker
    assert report["skipped_no_ticker"] == 1
    # 005380/202403 + 000660/202406 = 2건
    assert report["newly_collected"] == 2
    assert report["fnltt_calls"] == 2
    assert report["shares_calls"] == 2
    assert report["quota_used_estimate"] == 4
    assert report["errors"] == 0
    # alpha_recalc 호출됨 (latest period = 202406)
    assert report["alpha_recalc"] is not None
    assert isinstance(report["alpha_recalc"], dict)
    assert len(alpha_calls) == 1
    assert alpha_calls[0]["end_period"] == "202406"

    # ── DB 상태 확인 ──
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT symbol, report_period, cfo, shares_out, fs_source "
        "FROM financial_quarterly ORDER BY symbol, report_period"
    ).fetchall()
    conn.close()
    by_key = {(r[0], r[1]): r for r in rows}

    # 005380/202403: 신규 수집 cfo=180, shares_out=1e9
    assert ("005380", "202403") in by_key
    assert by_key[("005380", "202403")][2] == 180
    assert by_key[("005380", "202403")][3] == 1_000_000_000
    assert by_key[("005380", "202403")][4] == "CFS"

    # 000660/202406: 신규 수집
    assert ("000660", "202406") in by_key
    assert by_key[("000660", "202406")][2] == 180

    # 005930/202312: pre-insert값 유지 (cfo=100000, fs_source=CFS)
    assert ("005930", "202312") in by_key
    assert by_key[("005930", "202312")][2] == 100000


def test_collect_no_disclosures(tmp_path, monkeypatch):
    """공시 0건일 때 early-return + alpha_recalc 미호출."""
    import db_collector as _dbc
    import kis_api as _kis

    db_path = _make_tmp_db(tmp_path)
    monkeypatch.setattr(_dbc, "DB_PATH", db_path)

    async def mock_search(days=2, session=None):
        return []

    alpha_called = []

    def mock_alpha(end_period=None, trade_date=None):
        alpha_called.append(1)
        return {}

    monkeypatch.setattr(_kis, "search_dart_periodic_new", mock_search)
    monkeypatch.setattr(_dbc, "update_all_alpha_metrics", mock_alpha)

    report = asyncio.run(_dbc.collect_financial_on_disclosure(days=2))
    assert report["disclosures_found"] == 0
    assert report["newly_collected"] == 0
    assert report["alpha_recalc"] is None
    assert len(alpha_called) == 0  # 신규 0 → alpha 재계산 skip


def test_collect_max_calls_limit(tmp_path, monkeypatch):
    """max_calls 안전장치 — 공시 많을 때 상한 제어."""
    import db_collector as _dbc
    import kis_api as _kis

    db_path = _make_tmp_db(tmp_path)
    monkeypatch.setattr(_dbc, "DB_PATH", db_path)

    # 5건 공시 + 사전에 stock_master에 삽입 (FK 충족)
    init_conn = _dbc._get_db()
    for i in range(5):
        init_conn.execute(
            "INSERT OR IGNORE INTO stock_master(symbol, name, market) "
            "VALUES (?,?,?)",
            (f"{100000+i:06d}", f"C{i}", "kospi"),
        )
    init_conn.commit()
    init_conn.close()

    fake_disclosures = [
        {"corp_code": f"CC{i:06d}", "ticker": f"{100000+i:06d}",
         "corp_name": f"C{i}", "rcept_no": "X", "rcept_dt": "20250515",
         "rpt_nm": "분기보고서 (2024.03)",
         "report_period": "202403", "report_type": "quarterly"}
        for i in range(5)
    ]

    async def mock_search(days=2, session=None):
        return fake_disclosures

    async def mock_full(corp_code, year, quarter, session=None):
        return {
            "report_period": f"{year}{quarter*3:02d}",
            "fs_source": "CFS",
            "revenue": 1, "cfo": 1, "capex": 0, "fcf": 1,
        }

    async def mock_shares(corp_code, year, quarter, session=None):
        return 1000

    async def mock_load_corp_codes():
        return {}  # list.json의 stock_code fallback 사용

    monkeypatch.setattr(_kis, "search_dart_periodic_new", mock_search)
    monkeypatch.setattr(_kis, "dart_quarterly_full", mock_full)
    monkeypatch.setattr(_kis, "dart_shares_outstanding", mock_shares)
    monkeypatch.setattr(_kis, "load_corp_codes", mock_load_corp_codes)
    monkeypatch.setattr(_dbc, "update_all_alpha_metrics",
                        lambda **kw: {"success": 0})
    monkeypatch.setattr(_dbc, "_DART_INTERVAL", 0.0)

    # max_calls=4 → max_pairs=2 → 최대 2종목만 수집
    report = asyncio.run(_dbc.collect_financial_on_disclosure(days=2, max_calls=4))

    assert report["disclosures_found"] == 5
    assert report["newly_collected"] == 2  # max_pairs 제한
    assert report["quota_used_estimate"] == 4  # 2종목 × 2콜
