"""DART API 핵심 함수 unit test — mock 기반.

대상:
- _to_int_safe / _to_float_safe: 안전 변환 (콤마, 빈값, 음수)
- dart_quarterly_full: 분기 재무 응답 파싱 (CFS/OFS fallback)
- _report_name_priority: 사업보고서 정렬 우선순위
- upsert_insider_transactions + aggregate_insider_cluster:
  3명+ 클러스터 매수 감지
- load_corp_codes: 24h 캐시 사용
"""
import asyncio
import json
import os
import sqlite3
import tempfile
import time
from unittest.mock import patch, AsyncMock

import pytest

from kis_api.dart import (
    _to_int_safe, _to_float_safe,
    _report_name_priority,
    dart_quarterly_full,
    upsert_insider_transactions, aggregate_insider_cluster,
    load_corp_codes,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. _to_int_safe / _to_float_safe
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_to_int_safe_handles_comma_strings():
    """천 단위 콤마 제거 후 int 변환."""
    assert _to_int_safe("1,234,567") == 1234567
    assert _to_int_safe("0") == 0
    assert _to_int_safe("-500") == -500


def test_to_int_safe_handles_empty_and_dash():
    """빈 문자열, '-', None → 0."""
    assert _to_int_safe(None) == 0
    assert _to_int_safe("") == 0
    assert _to_int_safe("-") == 0
    assert _to_int_safe("  ") == 0


def test_to_int_safe_handles_invalid_string():
    """파싱 불가능한 문자열 → 0 (예외 없음)."""
    assert _to_int_safe("abc") == 0
    assert _to_int_safe("1.5") == 1   # float 폴백 → int 변환


def test_to_float_safe_handles_comma_and_negatives():
    """콤마 + 음수 + 0."""
    assert _to_float_safe("1,234.56") == 1234.56
    assert _to_float_safe("-2.5") == -2.5
    assert _to_float_safe(None) == 0.0
    assert _to_float_safe("-") == 0.0
    assert _to_float_safe("abc") == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. _report_name_priority
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_report_priority_original_first():
    """원본 사업보고서가 가장 우선 (0)."""
    assert _report_name_priority("사업보고서") == 0


def test_report_priority_correction_second():
    """[정정] 보고서가 2번째 (1)."""
    assert _report_name_priority("[정정]사업보고서") == 1


def test_report_priority_attachment_correction_third():
    """[첨부정정] 보고서가 3번째 (2)."""
    assert _report_name_priority("[첨부정정]사업보고서") == 2


def test_report_priority_other_last():
    """그 외 (3)."""
    assert _report_name_priority("기타") == 3
    assert _report_name_priority("") == 3


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. dart_quarterly_full — 분기 재무 응답 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_dart_quarterly_full_empty_corp_code():
    """corp_code 빈 값 → None."""
    result = asyncio.run(dart_quarterly_full("", 2026, 1))
    assert result is None


def test_dart_quarterly_full_invalid_quarter():
    """quarter 1~4 외 값 → None."""
    with patch("kis_api.dart.DART_API_KEY", "test_key"):
        result = asyncio.run(dart_quarterly_full("00000001", 2026, 5))
        assert result is None


def test_dart_quarterly_full_no_api_key():
    """DART_API_KEY 없으면 None."""
    with patch("kis_api.dart.DART_API_KEY", ""):
        result = asyncio.run(dart_quarterly_full("00000001", 2026, 1))
        assert result is None


def test_dart_quarterly_full_parses_pl_bs_cf():
    """fnlttSinglAcntAll 응답에서 매출/영업이익/총자산 파싱.

    원 단위 입력 → 억원 단위 출력 (//100_000_000).
    """
    mock_response = {
        "status": "000",
        "list": [
            # IS — PL
            {"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "1000000000000"},
            {"sj_div": "IS", "account_nm": "매출원가", "thstrm_amount": "700000000000"},
            {"sj_div": "IS", "account_nm": "매출총이익", "thstrm_amount": "300000000000"},
            {"sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "150000000000"},
            {"sj_div": "IS", "account_nm": "당기순이익", "thstrm_amount": "100000000000"},
            # BS
            {"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "5000000000000"},
            {"sj_div": "BS", "account_nm": "부채총계", "thstrm_amount": "2000000000000"},
            {"sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "3000000000000"},
            # CF
            {"sj_div": "CF", "account_nm": "영업활동으로 인한 현금흐름",
             "thstrm_amount": "200000000000"},
            {"sj_div": "CF", "account_nm": "유형자산의 취득", "thstrm_amount": "50000000000"},
        ],
    }

    class _Resp:
        def __init__(self, d):
            self._d = d
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self, content_type=None): return self._d

    class _Sess:
        def __init__(self, d):
            self._d = d
            self.closed = False
        def get(self, *args, **kwargs):
            return _Resp(self._d)
        async def close(self):
            self.closed = True

    sess = _Sess(mock_response)
    with patch("kis_api.dart.DART_API_KEY", "k"), \
         patch("aiohttp.ClientSession", return_value=sess):
        result = asyncio.run(dart_quarterly_full("00000001", 2026, 1))

    assert result is not None
    # 원 → 억원: 1조 / 1e8 = 10000억
    assert result["revenue"] == 10000
    assert result["operating_profit"] == 1500
    assert result["net_income"] == 1000
    assert result["total_assets"] == 50000
    assert result["total_liab"] == 20000
    assert result["total_equity"] == 30000
    assert result["cfo"] == 2000
    assert result["capex"] == 500
    # FCF = CFO - CapEx = 2000 - 500 = 1500 (억원)
    assert result["fcf"] == 1500
    assert result["fs_source"] == "CFS"
    assert result["report_period"] == "202603"


def test_dart_quarterly_full_falls_back_to_ofs():
    """CFS 응답 비어 있으면 OFS fallback."""
    cfs_empty = {"status": "013", "message": "no data"}
    ofs_data = {
        "status": "000",
        "list": [
            {"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "500000000000"},
            {"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "1000000000000"},
        ],
    }

    class _Resp:
        def __init__(self, d):
            self._d = d
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self, content_type=None): return self._d

    class _Sess:
        def __init__(self, responses):
            self._r = list(responses)
            self.closed = False
        def get(self, *args, **kwargs):
            d = self._r.pop(0) if self._r else {"status": "000", "list": []}
            return _Resp(d)
        async def close(self):
            self.closed = True

    sess = _Sess([cfs_empty, ofs_data])
    with patch("kis_api.dart.DART_API_KEY", "k"), \
         patch("aiohttp.ClientSession", return_value=sess):
        result = asyncio.run(dart_quarterly_full("00000001", 2026, 1))

    assert result is not None
    assert result["fs_source"] == "OFS"
    assert result["revenue"] == 5000  # 500B → 5000억


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. upsert_insider_transactions + aggregate_insider_cluster
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _setup_insider_db(tmp_path):
    """insider_transactions 테이블만 있는 임시 SQLite DB 경로 반환."""
    db_path = str(tmp_path / "test_stock.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS stock_master (
        symbol TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        market TEXT NOT NULL
    );
    INSERT INTO stock_master (symbol, name, market) VALUES ('005930', '삼성전자', 'kospi');
    CREATE TABLE IF NOT EXISTS insider_transactions (
        rcept_no TEXT NOT NULL,
        symbol TEXT NOT NULL,
        corp_code TEXT DEFAULT '',
        rcept_dt TEXT NOT NULL,
        repror TEXT DEFAULT '',
        ofcps TEXT DEFAULT '',
        rgist TEXT DEFAULT '',
        main_shrholdr TEXT DEFAULT '',
        stock_cnt INTEGER DEFAULT 0,
        stock_irds_cnt INTEGER DEFAULT 0,
        stock_rate REAL DEFAULT 0,
        stock_irds_rate REAL DEFAULT 0,
        collected_at TEXT DEFAULT '',
        PRIMARY KEY (rcept_no, repror)
    );
    """)
    conn.commit()
    conn.close()
    return db_path


def test_upsert_insider_transactions_inserts_new_rows(tmp_path):
    """신규 레코드 INSERT, 중복 IGNORE."""
    db_path = _setup_insider_db(tmp_path)
    records = [
        {
            "rcept_no": "20260520000001", "rcept_dt": "2026-05-20",
            "repror": "홍길동", "isu_exctv_ofcps": "대표이사",
            "sp_stock_lmp_cnt": "10000", "sp_stock_lmp_irds_cnt": "1000",
            "sp_stock_lmp_rate": "0.5", "sp_stock_lmp_irds_rate": "0.05",
        },
        {
            "rcept_no": "20260520000002", "rcept_dt": "2026-05-20",
            "repror": "김철수", "isu_exctv_ofcps": "이사",
            "sp_stock_lmp_cnt": "5000", "sp_stock_lmp_irds_cnt": "500",
            "sp_stock_lmp_rate": "0.2", "sp_stock_lmp_irds_rate": "0.02",
        },
    ]
    with patch("kis_api.dart.DB_PATH_FOR_INSIDER", db_path):
        inserted = upsert_insider_transactions("005930", "00000001", records)
        assert inserted == 2

        # 재실행 — 중복 IGNORE
        inserted2 = upsert_insider_transactions("005930", "00000001", records)
        assert inserted2 == 0


def test_upsert_insider_transactions_empty_records(tmp_path):
    """records 빈 리스트 → 0."""
    db_path = _setup_insider_db(tmp_path)
    with patch("kis_api.dart.DB_PATH_FOR_INSIDER", db_path):
        assert upsert_insider_transactions("005930", "00000001", []) == 0


def test_aggregate_insider_cluster_detects_3plus_buyers(tmp_path):
    """3명 이상 매수 + 순매수>0 → buyers>=3."""
    db_path = _setup_insider_db(tmp_path)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    records = [
        # 3명 매수 (irds_cnt > 0), 1명 매도
        {"rcept_no": "RC1", "rcept_dt": today, "repror": "A",
         "isu_exctv_ofcps": "이사",
         "sp_stock_lmp_cnt": "10000", "sp_stock_lmp_irds_cnt": "1000",
         "sp_stock_lmp_rate": "0.5", "sp_stock_lmp_irds_rate": "0.05"},
        {"rcept_no": "RC2", "rcept_dt": today, "repror": "B",
         "isu_exctv_ofcps": "이사",
         "sp_stock_lmp_cnt": "5000", "sp_stock_lmp_irds_cnt": "500",
         "sp_stock_lmp_rate": "0.2", "sp_stock_lmp_irds_rate": "0.02"},
        {"rcept_no": "RC3", "rcept_dt": today, "repror": "C",
         "isu_exctv_ofcps": "대표",
         "sp_stock_lmp_cnt": "20000", "sp_stock_lmp_irds_cnt": "2000",
         "sp_stock_lmp_rate": "1.0", "sp_stock_lmp_irds_rate": "0.10"},
        {"rcept_no": "RC4", "rcept_dt": today, "repror": "D",
         "isu_exctv_ofcps": "이사",
         "sp_stock_lmp_cnt": "1000", "sp_stock_lmp_irds_cnt": "-200",
         "sp_stock_lmp_rate": "0.05", "sp_stock_lmp_irds_rate": "-0.01"},
    ]
    with patch("kis_api.dart.DB_PATH_FOR_INSIDER", db_path):
        upsert_insider_transactions("005930", "00000001", records)
        result = aggregate_insider_cluster("005930", days=30)

    assert result["symbol"] == "005930"
    assert result["buyers"] == 3
    assert result["sellers"] == 1
    assert result["buy_qty"] == 3500   # 1000 + 500 + 2000
    assert result["sell_qty"] == 200   # |-200|
    assert set(result["buy_names"]) == {"A", "B", "C"}
    assert set(result["sell_names"]) == {"D"}
    assert len(result["recent"]) == 4


def test_aggregate_insider_cluster_empty_for_unknown_ticker(tmp_path):
    """기록 없는 종목 → 빈 집계."""
    db_path = _setup_insider_db(tmp_path)
    with patch("kis_api.dart.DB_PATH_FOR_INSIDER", db_path):
        result = aggregate_insider_cluster("999999", days=30)
    assert result["buyers"] == 0
    assert result["sellers"] == 0
    assert result["recent"] == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. load_corp_codes — 캐시 동작
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_load_corp_codes_uses_cache_when_fresh(tmp_path):
    """24h 이내 캐시 파일 존재 → 즉시 반환 (네트워크 미사용)."""
    cache_path = str(tmp_path / "corp_codes.json")
    cached_data = {
        "005930": {"corp_code": "00126380", "corp_name": "삼성전자"},
        "000660": {"corp_code": "00164742", "corp_name": "SK하이닉스"},
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cached_data, f, ensure_ascii=False)

    with patch("kis_api.dart.CORP_CODES_FILE", cache_path):
        result = asyncio.run(load_corp_codes())
    assert result == cached_data


def test_load_corp_codes_no_cache_triggers_download(tmp_path):
    """캐시 없으면 _download_corp_codes 호출."""
    cache_path = str(tmp_path / "absent.json")
    fake_download_result = {"005930": {"corp_code": "00126380", "corp_name": "삼성전자"}}

    async def fake_download():
        return fake_download_result

    with patch("kis_api.dart.CORP_CODES_FILE", cache_path), \
         patch("kis_api.dart._download_corp_codes", side_effect=fake_download):
        result = asyncio.run(load_corp_codes())
    assert result == fake_download_result
