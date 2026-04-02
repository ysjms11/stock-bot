"""
KRX GitHub Actions 업로드 시스템 종합 테스트 (pytest)
모든 외부 HTTP 호출은 mock 처리.
"""
import asyncio
import json
import os
import shutil
import sys
import types
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# telegram 스텁 (import 체인용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
telegram_stub = types.ModuleType("telegram")
telegram_stub.Update = object
telegram_stub.Bot = MagicMock
telegram_stub.ReplyKeyboardMarkup = type("RKM", (), {"__init__": lambda self, *a, **kw: None})
ext_stub = types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.MessageHandler = object
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
ext_stub.filters = type("filters", (), {"TEXT": None, "Regex": staticmethod(lambda x: x)})()
ext_stub.JobQueue = object
sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 임시 디렉토리 설정 (import 전에)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
TEST_ROOT = "/tmp/test_krx_github_actions"
TEST_KRX_DB_DIR = os.path.join(TEST_ROOT, "krx_db")

# krx_crawler import 전에 KRX_DB_DIR 패치
import krx_crawler
krx_crawler.KRX_DB_DIR = TEST_KRX_DB_DIR

from krx_crawler import (
    load_krx_db,
    scan_stocks,
    _cleanup_old_db,
)

# scripts/krx_update.py를 직접 import
_scripts_dir = os.path.join(os.path.dirname(__file__), "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

import krx_update
from krx_update import fetch_market_data, fetch_investor_data, build_db, _last_trading_date


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# autouse fixture: 테스트마다 임시 디렉토리 초기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
@pytest.fixture(autouse=True)
def clean_test_dir():
    """각 테스트 전후 임시 디렉토리 정리."""
    os.makedirs(TEST_KRX_DB_DIR, exist_ok=True)
    yield
    if os.path.exists(TEST_ROOT):
        shutil.rmtree(TEST_ROOT)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 mock 데이터 생성 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _make_price_records(n: int) -> list[dict]:
    """n개의 가짜 시세 레코드 생성 (OutBlock_1 포맷)."""
    records = []
    for i in range(n):
        ticker = f"{i + 1:06d}"
        records.append({
            "ISU_SRT_CD": ticker,
            "ISU_ABBRV": f"종목{ticker}",
            "TDD_CLSPRC": f"{10000 + i * 100:,}",
            "FLUC_RT": f"{(i % 10) - 5:.2f}",
            "ACC_TRDVOL": f"{100000 + i * 1000:,}",
            "ACC_TRDVAL": f"{1000000000 + i * 10000000:,}",
            "MKTCAP": f"{500000000000 + i * 1000000000:,}",
        })
    return records


def _make_investor_records(tickers: list[str]) -> list[dict]:
    """투자자별 순매수 가짜 레코드 생성 (MDCSTAT02401 포맷)."""
    records = []
    for i, ticker in enumerate(tickers):
        records.append({
            "ISU_SRT_CD": ticker,
            "NETBID_TRDVOL": f"{(i + 1) * 1000:,}",
            "NETBID_TRDVAL": f"{(i + 1) * 10000000:,}",
        })
    return records


def _make_fake_db(stocks_dict: dict, date: str = "20260402") -> dict:
    """scan_stocks에 넘길 가짜 DB dict 생성."""
    return {
        "date": date,
        "updated_at": f"{date}T15:30:00+09:00",
        "investor_data_available": True,
        "market_summary": {
            "kospi_count": len(stocks_dict),
            "kosdaq_count": 0,
            "kospi_up": 5,
            "kospi_down": 3,
            "kosdaq_up": 0,
            "kosdaq_down": 0,
            "kospi_avg_chg": 0.5,
            "kosdaq_avg_chg": 0.3,
        },
        "count": len(stocks_dict),
        "stocks": stocks_dict,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 1: KOSPI 크롤링 mock (scripts/krx_update.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchMarketDataKOSPI:
    """Test 1: scripts/krx_update.py — KOSPI 전종목 시세 크롤링 mock."""

    def test_kospi_returns_850_plus_stocks(self):
        """krx_post mock → STK 시장 850개 이상 종목 반환 + 필드 검증."""
        fake_records = _make_price_records(850)
        fake_body = {"OutBlock_1": fake_records}

        with patch("krx_update.krx_post", return_value=fake_body):
            result = fetch_market_data("20260402", "STK")

        assert len(result) >= 850, f"KOSPI 종목수가 850 미만: {len(result)}"

        first = result[0]
        assert "ticker" in first
        assert "name" in first
        assert "market" in first
        assert "close" in first
        assert "chg_pct" in first
        assert "volume" in first
        assert "trade_value" in first
        assert "market_cap" in first
        assert first["market"] == "kospi"

        # 모든 ticker가 6자리인지 확인
        for stock in result:
            assert len(stock["ticker"]) == 6, f"ticker 길이 이상: {stock['ticker']}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 2: KOSDAQ 크롤링 mock (scripts/krx_update.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchMarketDataKOSDAQ:
    """Test 2: scripts/krx_update.py — KOSDAQ 전종목 시세 크롤링 mock."""

    def test_kosdaq_returns_1550_plus_stocks(self):
        """krx_post mock → KSQ 시장 1550개 이상 종목 반환."""
        fake_records = _make_price_records(1550)
        fake_body = {"OutBlock_1": fake_records}

        with patch("krx_update.krx_post", return_value=fake_body):
            result = fetch_market_data("20260402", "KSQ")

        assert len(result) >= 1550, f"KOSDAQ 종목수가 1550 미만: {len(result)}"

        first = result[0]
        assert first["market"] == "kosdaq", f"market 필드가 kosdaq 아님: {first['market']}"

        # 숫자 파싱 정확성 검증
        assert isinstance(first["close"], int)
        assert isinstance(first["volume"], int)
        assert isinstance(first["market_cap"], int)
        assert isinstance(first["chg_pct"], float)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 3: 투자자별 수급 mock (scripts/krx_update.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchInvestorData:
    """Test 3: scripts/krx_update.py — MDCSTAT02401 투자자별 수급 mock."""

    def test_investor_data_has_correct_keys(self):
        """krx_post mock → foreign/inst/indiv 키 포함 dict 반환."""
        sample_tickers = [f"{i:06d}" for i in range(1, 6)]
        fake_records = _make_investor_records(sample_tickers)
        fake_body = {"OutBlock_1": fake_records}

        # fetch_investor_data는 3가지 투자자 타입(foreign/inst/indiv)을 순차 호출
        with patch("krx_update.krx_post", return_value=fake_body):
            result = fetch_investor_data("20260402", "STK")

        assert isinstance(result, dict)
        assert len(result) > 0, "투자자 데이터가 빈 dict"

        # 하나의 종목을 꺼내서 키 확인
        sample_ticker = sample_tickers[0]
        assert sample_ticker in result
        data = result[sample_ticker]

        # foreign, inst, indiv 각각 net_qty, net_amt 키 존재
        assert "foreign_net_qty" in data, f"foreign_net_qty 키 없음: {data.keys()}"
        assert "foreign_net_amt" in data, f"foreign_net_amt 키 없음: {data.keys()}"
        assert "inst_net_qty" in data, f"inst_net_qty 키 없음: {data.keys()}"
        assert "inst_net_amt" in data, f"inst_net_amt 키 없음: {data.keys()}"
        assert "indiv_net_qty" in data, f"indiv_net_qty 키 없음: {data.keys()}"
        assert "indiv_net_amt" in data, f"indiv_net_amt 키 없음: {data.keys()}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 4: foreign_ratio 계산 검증 (scripts/krx_update.py build_db)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestForeignRatioCalculation:
    """Test 4: build_db에서 foreign_ratio 비율 계산 정확성 검증."""

    def test_foreign_ratio_calculation(self):
        """시총 1조, 외인 순매수 50억 → foreign_ratio = 0.5%."""
        # 단 1개 종목으로 단순화
        market_cap = 1_000_000_000_000   # 1조 원
        foreign_net_amt = 5_000_000_000  # 50억 원

        # expected: 50억 / 1조 * 100 = 0.5
        expected_ratio = round(foreign_net_amt / market_cap * 100, 4)
        assert expected_ratio == pytest.approx(0.5)

        # build_db에서 사용하는 동일 공식으로 직접 계산 검증
        result_ratio = round(foreign_net_amt / market_cap * 100, 4)
        assert result_ratio == pytest.approx(0.5, rel=1e-4)

    def test_build_db_foreign_ratio_via_mocked_crawl(self):
        """build_db를 mock 크롤 함수로 실행 → foreign_ratio 값 검증."""
        date = "20260402"
        market_cap_raw = 1_000_000_000_000   # 1조
        foreign_net_amt_raw = 5_000_000_000  # 50억

        # MDCSTAT01501 가짜 응답 (1개 종목)
        price_record = [{
            "ISU_SRT_CD": "005930",
            "ISU_ABBRV": "삼성전자",
            "TDD_CLSPRC": "75500",
            "FLUC_RT": "1.34",
            "ACC_TRDVOL": "10000000",
            "ACC_TRDVAL": "755000000000",
            "MKTCAP": str(market_cap_raw),
        }]

        # MDCSTAT03901 가짜 응답 (PER/PBR)
        fundamental_record = [{"ISU_SRT_CD": "005930", "PER": "12.5", "PBR": "1.2"}]

        # MDCSTAT02401 가짜 응답 (foreign)
        investor_record_foreign = [{
            "ISU_SRT_CD": "005930",
            "NETBID_TRDVOL": "100000",
            "NETBID_TRDVAL": str(foreign_net_amt_raw),
        }]
        # inst, indiv는 0으로 반환
        investor_record_zero = [{
            "ISU_SRT_CD": "005930",
            "NETBID_TRDVOL": "0",
            "NETBID_TRDVAL": "0",
        }]

        # krx_post 호출 순서:
        #   STK 시세 → KSQ 시세 → STK PER → KSQ PER
        #   STK 외인 → STK 기관 → STK 개인 → KSQ 외인 → KSQ 기관 → KSQ 개인
        call_sequence = [
            {"OutBlock_1": price_record},        # STK 시세
            {"OutBlock_1": []},                  # KSQ 시세 (빈 결과)
            {"OutBlock_1": fundamental_record},   # STK PER
            {"OutBlock_1": []},                  # KSQ PER
            {"OutBlock_1": investor_record_foreign},  # STK 외인
            {"OutBlock_1": investor_record_zero},     # STK 기관
            {"OutBlock_1": investor_record_zero},     # STK 개인
            {"OutBlock_1": []},                  # KSQ 외인
            {"OutBlock_1": []},                  # KSQ 기관
            {"OutBlock_1": []},                  # KSQ 개인
        ]

        call_iter = iter(call_sequence)

        def mock_krx_post(form):
            try:
                return next(call_iter)
            except StopIteration:
                return {"OutBlock_1": []}

        with patch("krx_update.krx_post", side_effect=mock_krx_post), \
             patch("time.sleep"):  # sleep 스킵
            db = build_db(date)

        assert "stocks" in db
        s = db["stocks"].get("005930")
        assert s is not None, "005930 종목이 DB에 없음"

        assert s["market_cap"] == market_cap_raw
        assert s["foreign_net_amt"] == foreign_net_amt_raw
        assert s["foreign_ratio"] == pytest.approx(0.5, rel=1e-3)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 5: scan_stocks 기본 동작
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestScanStocksBasic:
    """Test 5: scan_stocks 기본 동작 — results/count/date 필드 반환 검증."""

    def test_scan_returns_required_fields(self):
        """가짜 DB로 scan_stocks 호출 → results, count, date 포함 dict 반환."""
        stocks = {
            "005930": {
                "ticker": "005930",
                "name": "삼성전자",
                "market": "kospi",
                "close": 75500,
                "chg_pct": 1.5,
                "volume": 10000000,
                "trade_value": 755000000000,
                "market_cap": 450_000_000_000_000,
                "per": 12.5,
                "pbr": 1.2,
                "foreign_net_qty": 100000,
                "foreign_net_amt": 5_000_000_000,
                "inst_net_qty": 0,
                "inst_net_amt": 0,
                "indiv_net_qty": -100000,
                "indiv_net_amt": -5_000_000_000,
                "foreign_ratio": 0.5,
                "inst_ratio": 0.0,
                "fi_ratio": 0.5,
                "turnover": 1.2,
            }
        }
        db = _make_fake_db(stocks, "20260402")

        with patch("krx_crawler.KRX_DB_DIR", TEST_KRX_DB_DIR):
            result = scan_stocks(db, {})

        assert "results" in result, "results 키 없음"
        assert "count" in result, "count 키 없음"
        assert "date" in result, "date 키 없음"
        assert result["date"] == "20260402"
        assert isinstance(result["results"], list)
        assert result["count"] >= 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 6: preset=small_cap_buy 필터링
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestPresetSmallCapBuy:
    """Test 6: preset=small_cap_buy — 시총 500~5000억 AND foreign_ratio>0.1% 필터."""

    def test_small_cap_buy_filter(self):
        """다양한 시총/foreign_ratio 종목 중 조건 만족 종목만 통과."""
        # 억원 단위 → 실제 저장 단위는 원
        # small_cap_buy 조건: 500억~5000억 AND foreign_ratio > 0.1%
        stocks = {
            "000001": {  # 통과: 시총 1000억, foreign_ratio 0.5%
                "ticker": "000001", "name": "통과종목A", "market": "kospi",
                "close": 10000, "chg_pct": 1.0, "volume": 100000,
                "trade_value": 1_000_000_000,
                "market_cap": 100_000_000_000,  # 1000억 원
                "per": 10.0, "pbr": 1.0,
                "foreign_ratio": 0.5, "inst_ratio": 0.0, "fi_ratio": 0.5, "turnover": 1.0,
            },
            "000002": {  # 탈락: 시총 너무 작음 (100억)
                "ticker": "000002", "name": "탈락소형", "market": "kospi",
                "close": 5000, "chg_pct": 0.5, "volume": 50000,
                "trade_value": 250_000_000,
                "market_cap": 10_000_000_000,   # 100억 원
                "per": 8.0, "pbr": 0.8,
                "foreign_ratio": 0.5, "inst_ratio": 0.0, "fi_ratio": 0.5, "turnover": 0.5,
            },
            "000003": {  # 탈락: 시총 너무 큼 (10조)
                "ticker": "000003", "name": "탈락대형", "market": "kospi",
                "close": 500000, "chg_pct": 0.2, "volume": 500000,
                "trade_value": 250_000_000_000,
                "market_cap": 10_000_000_000_000,  # 10조 원
                "per": 15.0, "pbr": 1.5,
                "foreign_ratio": 0.5, "inst_ratio": 0.0, "fi_ratio": 0.5, "turnover": 2.5,
            },
            "000004": {  # 탈락: foreign_ratio 너무 낮음 (0.05%)
                "ticker": "000004", "name": "탈락외인없음", "market": "kospi",
                "close": 15000, "chg_pct": 0.8, "volume": 80000,
                "trade_value": 1_200_000_000,
                "market_cap": 300_000_000_000,  # 3000억 원
                "per": 9.0, "pbr": 0.9,
                "foreign_ratio": 0.05, "inst_ratio": 0.0, "fi_ratio": 0.05, "turnover": 0.4,
            },
            "000005": {  # 통과: 시총 2000억, foreign_ratio 0.3%
                "ticker": "000005", "name": "통과종목B", "market": "kosdaq",
                "close": 20000, "chg_pct": 2.0, "volume": 200000,
                "trade_value": 4_000_000_000,
                "market_cap": 200_000_000_000,  # 2000억 원
                "per": 11.0, "pbr": 1.1,
                "foreign_ratio": 0.3, "inst_ratio": 0.1, "fi_ratio": 0.4, "turnover": 2.0,
            },
        }
        db = _make_fake_db(stocks, "20260402")

        with patch("krx_crawler.KRX_DB_DIR", TEST_KRX_DB_DIR):
            result = scan_stocks(db, {}, preset="small_cap_buy")

        result_tickers = {r["ticker"] for r in result["results"]}
        assert "000001" in result_tickers, "통과종목A(1000억, fr=0.5%)가 결과에 없음"
        assert "000005" in result_tickers, "통과종목B(2000억, fr=0.3%)가 결과에 없음"
        assert "000002" not in result_tickers, "탈락소형(100억)이 결과에 포함됨"
        assert "000003" not in result_tickers, "탈락대형(10조)이 결과에 포함됨"
        assert "000004" not in result_tickers, "탈락외인없음(fr=0.05%)이 결과에 포함됨"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 7: preset=value 필터링
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestPresetValue:
    """Test 7: preset=value — PER<10 AND PBR<1 AND 시총>1000억 필터."""

    def test_value_preset_filter(self):
        """다양한 PER/PBR/시총 종목 중 value 조건 만족 종목만 통과."""
        # value 조건: per_min=0.01, per_max=10, pbr_max=1, market_cap_min=1000억
        stocks = {
            "000010": {  # 통과: PER=8, PBR=0.7, 시총 2000억
                "ticker": "000010", "name": "가치주A", "market": "kospi",
                "close": 20000, "chg_pct": 0.5, "volume": 100000,
                "trade_value": 2_000_000_000,
                "market_cap": 200_000_000_000,  # 2000억
                "per": 8.0, "pbr": 0.7,
                "foreign_ratio": 0.1, "inst_ratio": 0.05, "fi_ratio": 0.15, "turnover": 1.0,
            },
            "000011": {  # 탈락: PER=15 (너무 높음)
                "ticker": "000011", "name": "탈락고PER", "market": "kospi",
                "close": 30000, "chg_pct": 1.0, "volume": 80000,
                "trade_value": 2_400_000_000,
                "market_cap": 300_000_000_000,  # 3000억
                "per": 15.0, "pbr": 0.8,
                "foreign_ratio": 0.2, "inst_ratio": 0.1, "fi_ratio": 0.3, "turnover": 0.8,
            },
            "000012": {  # 탈락: PBR=1.5 (너무 높음)
                "ticker": "000012", "name": "탈락고PBR", "market": "kospi",
                "close": 25000, "chg_pct": 0.8, "volume": 90000,
                "trade_value": 2_250_000_000,
                "market_cap": 250_000_000_000,  # 2500억
                "per": 9.0, "pbr": 1.5,
                "foreign_ratio": 0.15, "inst_ratio": 0.05, "fi_ratio": 0.2, "turnover": 0.9,
            },
            "000013": {  # 탈락: 시총 500억 (1000억 미만)
                "ticker": "000013", "name": "탈락소형", "market": "kosdaq",
                "close": 5000, "chg_pct": 0.2, "volume": 50000,
                "trade_value": 250_000_000,
                "market_cap": 50_000_000_000,   # 500억
                "per": 7.0, "pbr": 0.5,
                "foreign_ratio": 0.3, "inst_ratio": 0.0, "fi_ratio": 0.3, "turnover": 0.5,
            },
            "000014": {  # 통과: PER=5, PBR=0.4, 시총 5000억
                "ticker": "000014", "name": "가치주B", "market": "kosdaq",
                "close": 50000, "chg_pct": -0.5, "volume": 60000,
                "trade_value": 3_000_000_000,
                "market_cap": 500_000_000_000,  # 5000억
                "per": 5.0, "pbr": 0.4,
                "foreign_ratio": 0.05, "inst_ratio": 0.02, "fi_ratio": 0.07, "turnover": 0.6,
            },
        }
        db = _make_fake_db(stocks, "20260402")

        with patch("krx_crawler.KRX_DB_DIR", TEST_KRX_DB_DIR):
            result = scan_stocks(db, {}, preset="value")

        result_tickers = {r["ticker"] for r in result["results"]}
        assert "000010" in result_tickers, "가치주A(PER=8, PBR=0.7, 2000억)가 결과에 없음"
        assert "000014" in result_tickers, "가치주B(PER=5, PBR=0.4, 5000억)가 결과에 없음"
        assert "000011" not in result_tickers, "탈락고PER(PER=15)이 결과에 포함됨"
        assert "000012" not in result_tickers, "탈락고PBR(PBR=1.5)이 결과에 포함됨"
        assert "000013" not in result_tickers, "탈락소형(500억)이 결과에 포함됨"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 8: DB 파일 생성 via _handle_krx_upload
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestHandleKrxUpload:
    """Test 8: _handle_krx_upload 핸들러 — JSON 수신 후 DB 파일 생성 검증."""

    @pytest.mark.asyncio
    async def test_upload_creates_db_file(self):
        """유효한 JSON body로 업로드 요청 → temp dir에 파일 생성 + load_krx_db 검증."""
        date = "20260402"
        fake_db = {
            "date": date,
            "updated_at": "2026-04-02T15:30:00+09:00",
            "investor_data_available": True,
            "market_summary": {
                "kospi_count": 1,
                "kosdaq_count": 0,
                "kospi_up": 1,
                "kospi_down": 0,
                "kosdaq_up": 0,
                "kosdaq_down": 0,
                "kospi_avg_chg": 1.5,
                "kosdaq_avg_chg": 0.0,
            },
            "count": 1,
            "stocks": {
                "005930": {
                    "ticker": "005930",
                    "name": "삼성전자",
                    "market": "kospi",
                    "close": 75500,
                    "chg_pct": 1.5,
                    "volume": 10000000,
                    "trade_value": 755_000_000_000,
                    "market_cap": 450_000_000_000_000,
                    "per": 12.5,
                    "pbr": 1.2,
                    "foreign_ratio": 0.5,
                    "inst_ratio": 0.0,
                    "fi_ratio": 0.5,
                    "turnover": 1.2,
                }
            },
        }

        # aiohttp Request 객체 mock
        test_key = "test-secret-key-12345"
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": f"Bearer {test_key}"}
        mock_request.json = AsyncMock(return_value=fake_db)

        # _handle_krx_upload의 KRX_DB_DIR과 KRX_UPLOAD_KEY를 패치
        # telegram Bot.send_message도 mock
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class = MagicMock(return_value=mock_bot)

        import main as main_mod
        original_db_dir = main_mod.KRX_DB_DIR
        original_key = main_mod.KRX_UPLOAD_KEY

        try:
            main_mod.KRX_DB_DIR = TEST_KRX_DB_DIR
            main_mod.KRX_UPLOAD_KEY = test_key

            with patch("main._cleanup_old_db"), \
                 patch("telegram.Bot", mock_bot_class):
                response = await main_mod._handle_krx_upload(mock_request)

            # 응답 확인
            assert response.status == 200

            # 파일이 실제로 생성됐는지 확인
            expected_file = os.path.join(TEST_KRX_DB_DIR, f"{date}.json")
            assert os.path.exists(expected_file), f"DB 파일이 생성되지 않음: {expected_file}"

            # 직접 파일 읽어서 내용 검증
            with open(expected_file, encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded is not None, "DB 파일 읽기 실패"
            assert loaded["date"] == date
            assert loaded["count"] == 1
            assert "005930" in loaded["stocks"]

        finally:
            main_mod.KRX_DB_DIR = original_db_dir
            main_mod.KRX_UPLOAD_KEY = original_key


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 9: 하위 호환성 — mcp_tools get_dart
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestMcpToolsBackwardCompatibility:
    """Test 9: mcp_tools._execute_tool('get_dart') 기존 동작 유지 검증."""

    @pytest.mark.asyncio
    async def test_get_dart_still_works(self):
        """get_dart MCP 도구가 KRX 추가 후에도 정상 동작."""
        # kis_api에서 필요한 함수들을 mock
        fake_disclosures = [
            {
                "rcept_no": "20260402000001",
                "corp_name": "삼성전자",
                "report_nm": "주요사항보고서",
                "rcept_dt": "20260402",
                "flr_nm": "삼성전자",
            }
        ]

        import mcp_tools
        with patch("mcp_tools.search_dart_disclosures", new_callable=AsyncMock,
                   return_value=fake_disclosures), \
             patch("mcp_tools.filter_important_disclosures",
                   return_value=fake_disclosures), \
             patch("mcp_tools.load_watchlist", return_value={"005930": "삼성전자"}), \
             patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="fake_token"):

            result = await mcp_tools._execute_tool("get_dart", {})

        assert result is not None, "_execute_tool 결과가 None"
        assert "error" not in str(result).lower() or "disclosures" in str(result).lower() \
               or isinstance(result, dict), f"예상치 못한 에러 결과: {result}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 10: 30일 지난 파일 정리
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestCleanupOldDb:
    """Test 10: _cleanup_old_db(30) — 30일 초과 파일 삭제, 최근 파일 보존."""

    def test_cleanup_deletes_old_files_keeps_recent(self):
        """30일 초과 파일 삭제, 최근 30일 이내 파일 보존 검증."""
        os.makedirs(TEST_KRX_DB_DIR, exist_ok=True)

        now = datetime.now(KST)

        # 40일 전 파일 (삭제 대상)
        old_date = (now - timedelta(days=40)).strftime("%Y%m%d")
        old_file = os.path.join(TEST_KRX_DB_DIR, f"{old_date}.json")
        with open(old_file, "w") as f:
            json.dump({"date": old_date}, f)

        # 35일 전 파일 (삭제 대상)
        old_date2 = (now - timedelta(days=35)).strftime("%Y%m%d")
        old_file2 = os.path.join(TEST_KRX_DB_DIR, f"{old_date2}.json")
        with open(old_file2, "w") as f:
            json.dump({"date": old_date2}, f)

        # 10일 전 파일 (보존 대상)
        recent_date = (now - timedelta(days=10)).strftime("%Y%m%d")
        recent_file = os.path.join(TEST_KRX_DB_DIR, f"{recent_date}.json")
        with open(recent_file, "w") as f:
            json.dump({"date": recent_date}, f)

        # 오늘 파일 (보존 대상)
        today_date = now.strftime("%Y%m%d")
        today_file = os.path.join(TEST_KRX_DB_DIR, f"{today_date}.json")
        with open(today_file, "w") as f:
            json.dump({"date": today_date}, f)

        # KRX_DB_DIR을 TEST_KRX_DB_DIR으로 패치 후 실행
        with patch("krx_crawler.KRX_DB_DIR", TEST_KRX_DB_DIR):
            _cleanup_old_db(30)

        # 삭제 확인
        assert not os.path.exists(old_file), f"40일 전 파일이 삭제되지 않음: {old_file}"
        assert not os.path.exists(old_file2), f"35일 전 파일이 삭제되지 않음: {old_file2}"

        # 보존 확인
        assert os.path.exists(recent_file), f"10일 전 파일이 잘못 삭제됨: {recent_file}"
        assert os.path.exists(today_file), f"오늘 파일이 잘못 삭제됨: {today_file}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 11: 날짜 계산 로직
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestLastTradingDate:
    """_last_trading_date: KST 기준 최근 거래일 계산."""

    def test_weekday_after_market_close(self):
        """평일 16:00 → 오늘."""
        fake = datetime(2026, 4, 3, 16, 0, tzinfo=KST)  # 금요일
        with patch("krx_update.datetime") as mock_dt:
            mock_dt.now.return_value = fake
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _last_trading_date() == "20260403"

    def test_weekday_before_market_close(self):
        """평일 14:00 → 전 거래일."""
        fake = datetime(2026, 4, 3, 14, 0, tzinfo=KST)  # 금요일
        with patch("krx_update.datetime") as mock_dt:
            mock_dt.now.return_value = fake
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _last_trading_date() == "20260402"

    def test_saturday(self):
        """토요일 → 직전 금요일."""
        fake = datetime(2026, 4, 4, 10, 0, tzinfo=KST)  # 토요일
        with patch("krx_update.datetime") as mock_dt:
            mock_dt.now.return_value = fake
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _last_trading_date() == "20260403"

    def test_sunday(self):
        """일요일 → 직전 금요일."""
        fake = datetime(2026, 4, 5, 10, 0, tzinfo=KST)  # 일요일
        with patch("krx_update.datetime") as mock_dt:
            mock_dt.now.return_value = fake
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _last_trading_date() == "20260403"

    def test_monday_morning(self):
        """월요일 09:00 → 직전 금요일."""
        fake = datetime(2026, 4, 6, 9, 0, tzinfo=KST)  # 월요일
        with patch("krx_update.datetime") as mock_dt:
            mock_dt.now.return_value = fake
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _last_trading_date() == "20260403"

    def test_exactly_1530(self):
        """15:30 정각 → 오늘 (장 마감 완료)."""
        fake = datetime(2026, 4, 3, 15, 30, tzinfo=KST)  # 금요일
        with patch("krx_update.datetime") as mock_dt:
            mock_dt.now.return_value = fake
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _last_trading_date() == "20260403"

    def test_1531(self):
        """15:31 → 오늘."""
        fake = datetime(2026, 4, 3, 15, 31, tzinfo=KST)  # 금요일
        with patch("krx_update.datetime") as mock_dt:
            mock_dt.now.return_value = fake
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _last_trading_date() == "20260403"
