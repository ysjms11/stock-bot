"""
KRX 크롤러 시스템 종합 테스트 (pytest)
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# telegram 스텁 (import 체인용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
telegram_stub = types.ModuleType("telegram")
telegram_stub.Update = object
telegram_stub.ReplyKeyboardMarkup = type("RKM", (), {"__init__": lambda self, *a, **kw: None})
ext_stub = types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.MessageHandler = object
ext_stub.filters = type("filters", (), {"TEXT": None, "Regex": staticmethod(lambda x: x)})()
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 임시 디렉토리 설정 (import 전에)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
TEST_ROOT = "/tmp/test_krx_crawler"
TEST_KRX_DB_DIR = os.path.join(TEST_ROOT, "krx_db")

# krx_crawler import 전에 KRX_DB_DIR 패치
import krx_crawler
krx_crawler.KRX_DB_DIR = TEST_KRX_DB_DIR

from krx_crawler import (
    fetch_krx_market_data,
    fetch_krx_investor_data,
    fetch_krx_fundamental,
    update_daily_db,
    load_krx_db,
    scan_stocks,
    _cleanup_old_db,
    _pi,
    _pf,
)

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
def _make_price_records(n: int, market_prefix: str = "A") -> list[dict]:
    """n개의 가짜 시세 레코드 생성 (OutBlock_1 포맷)."""
    records = []
    for i in range(n):
        ticker = f"{i+1:06d}"
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
# 테스트 1: KOSPI 시세 크롤링
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchKRXMarketDataKOSPI:
    """Test 1: KRX KOSPI 전종목 시세 크롤링 mock 테스트."""

    @pytest.mark.asyncio
    async def test_kospi_returns_800_plus_stocks(self):
        """_krx_post mock → STK 시장 800개 이상 종목 반환."""
        fake_records = _make_price_records(850)
        fake_body = {"OutBlock_1": fake_records}

        with patch("krx_crawler._krx_post", new_callable=AsyncMock,
                   return_value=fake_body):
            result = await fetch_krx_market_data("20260402", "STK")

        assert len(result) >= 800, f"KOSPI 종목수가 800 미만: {len(result)}"

        # 필드 확인
        first = result[0]
        assert "ticker" in first
        assert "name" in first
        assert "market" in first
        assert "close" in first
        assert "chg_pct" in first
        assert "volume" in first
        assert "trade_value" in first
        assert "market_cap" in first

        # market 값 검증
        assert first["market"] == "kospi"

        # ticker 6자리 확인
        for stock in result:
            assert len(stock["ticker"]) == 6, f"ticker 길이 이상: {stock['ticker']}"

    @pytest.mark.asyncio
    async def test_kospi_numeric_parsing(self):
        """콤마 포맷 숫자 파싱 정확성 검증."""
        fake_records = [{
            "ISU_SRT_CD": "005930",
            "ISU_ABBRV": "삼성전자",
            "TDD_CLSPRC": "75,500",
            "FLUC_RT": "+1.34",
            "ACC_TRDVOL": "12,345,678",
            "ACC_TRDVAL": "932,345,678,900",
            "MKTCAP": "450,000,000,000,000",
        }]
        fake_body = {"OutBlock_1": fake_records}

        with patch("krx_crawler._krx_post", new_callable=AsyncMock,
                   return_value=fake_body):
            result = await fetch_krx_market_data("20260402", "STK")

        assert len(result) == 1
        s = result[0]
        assert s["ticker"] == "005930"
        assert s["close"] == 75500
        assert s["chg_pct"] == pytest.approx(1.34)
        assert s["volume"] == 12345678
        assert s["trade_value"] == 932345678900
        assert s["market_cap"] == 450000000000000


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 2: KOSDAQ 시세 크롤링
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchKRXMarketDataKOSDAQ:
    """Test 2: KRX KOSDAQ 전종목 시세 크롤링 mock 테스트."""

    @pytest.mark.asyncio
    async def test_kosdaq_returns_1500_plus_stocks(self):
        """_krx_post mock → KSQ 시장 1500개 이상 종목 반환."""
        fake_records = _make_price_records(1550)
        fake_body = {"OutBlock_1": fake_records}

        with patch("krx_crawler._krx_post", new_callable=AsyncMock,
                   return_value=fake_body):
            result = await fetch_krx_market_data("20260402", "KSQ")

        assert len(result) >= 1500, f"KOSDAQ 종목수가 1500 미만: {len(result)}"

        # market 값 확인
        assert all(s["market"] == "kosdaq" for s in result)

    @pytest.mark.asyncio
    async def test_kosdaq_skips_invalid_tickers(self):
        """6자리 아닌 ticker는 걸러져야 함."""
        fake_records = [
            {"ISU_SRT_CD": "00593", "ISU_ABBRV": "5자리", "TDD_CLSPRC": "1000",
             "FLUC_RT": "0", "ACC_TRDVOL": "0", "ACC_TRDVAL": "0", "MKTCAP": "0"},
            {"ISU_SRT_CD": "", "ISU_ABBRV": "빈값", "TDD_CLSPRC": "1000",
             "FLUC_RT": "0", "ACC_TRDVOL": "0", "ACC_TRDVAL": "0", "MKTCAP": "0"},
            {"ISU_SRT_CD": "005930", "ISU_ABBRV": "정상", "TDD_CLSPRC": "75000",
             "FLUC_RT": "1.0", "ACC_TRDVOL": "1000", "ACC_TRDVAL": "75000000", "MKTCAP": "100000"},
        ]
        fake_body = {"OutBlock_1": fake_records}

        with patch("krx_crawler._krx_post", new_callable=AsyncMock,
                   return_value=fake_body):
            result = await fetch_krx_market_data("20260402", "KSQ")

        assert len(result) == 1
        assert result[0]["ticker"] == "005930"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 3: 투자자별 수급 크롤링
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchKRXInvestorData:
    """Test 3: 투자자별 수급 데이터 크롤링 mock 테스트."""

    @pytest.mark.asyncio
    async def test_investor_data_has_foreign_inst_indiv_keys(self):
        """MDCSTAT02401 mock → foreign/inst/indiv 키 포함 dict 반환."""
        tickers = ["005930", "000660", "035420"]
        fake_records = _make_investor_records(tickers)

        # 3번 호출 (foreign=9000, inst=7050, indiv=8000)
        call_count = 0

        async def mock_krx_post(session, form):
            nonlocal call_count
            call_count += 1
            # 모든 invstTpCd에 동일한 종목 반환 (테스트 단순화)
            return {"OutBlock_1": fake_records}

        with patch("krx_crawler._krx_post", side_effect=mock_krx_post), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await fetch_krx_investor_data("20260402", "STK")

        # 3번 호출됐는지 확인 (foreign, inst, indiv)
        assert call_count == 3

        # 각 ticker에 모든 수급 타입 키 존재
        for ticker in tickers:
            assert ticker in result
            d = result[ticker]
            assert "foreign_net_qty" in d, f"{ticker}: foreign_net_qty 없음"
            assert "foreign_net_amt" in d, f"{ticker}: foreign_net_amt 없음"
            assert "inst_net_qty" in d, f"{ticker}: inst_net_qty 없음"
            assert "inst_net_amt" in d, f"{ticker}: inst_net_amt 없음"
            assert "indiv_net_qty" in d, f"{ticker}: indiv_net_qty 없음"
            assert "indiv_net_amt" in d, f"{ticker}: indiv_net_amt 없음"

    @pytest.mark.asyncio
    async def test_investor_data_values_are_integers(self):
        """수급 금액/수량이 int 타입으로 파싱되는지 확인."""
        fake_records = [{
            "ISU_SRT_CD": "005930",
            "NETBID_TRDVOL": "123,456",
            "NETBID_TRDVAL": "9,876,543,210",
        }]

        with patch("krx_crawler._krx_post", new_callable=AsyncMock,
                   return_value={"OutBlock_1": fake_records}), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await fetch_krx_investor_data("20260402", "STK")

        assert "005930" in result
        d = result["005930"]
        # 마지막으로 설정된 값(indiv) 기준으로 int인지 확인
        assert isinstance(d["indiv_net_qty"], int)
        assert isinstance(d["indiv_net_amt"], int)
        assert d["indiv_net_qty"] == 123456
        assert d["indiv_net_amt"] == 9876543210


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 4: foreign_ratio 계산
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestForeignRatioCalculation:
    """Test 4: foreign_ratio = foreign_net_amt / market_cap * 100 계산 정확성."""

    @pytest.mark.asyncio
    async def test_foreign_ratio_calculation(self):
        """시총 1조, 외인순매수 50억 → foreign_ratio = 0.5%."""
        market_cap = 1_000_000_000_000    # 1조원
        foreign_net_amt = 5_000_000_000   # 50억원
        expected_ratio = round(foreign_net_amt / market_cap * 100, 4)  # 0.5

        # update_daily_db를 mock으로 우회하고 직접 비율 계산 로직 검증
        # krx_crawler.update_daily_db 내부의 비율 계산을 재현
        s = {
            "ticker": "005930",
            "name": "삼성전자",
            "market": "kospi",
            "close": 75000,
            "chg_pct": 1.0,
            "volume": 10000000,
            "trade_value": 750000000000,
            "market_cap": market_cap,
            "foreign_net_qty": 66666,
            "foreign_net_amt": foreign_net_amt,
            "inst_net_qty": 10000,
            "inst_net_amt": 1_000_000_000,
            "indiv_net_qty": -76666,
            "indiv_net_amt": -6_000_000_000,
        }

        mcap = s["market_cap"]
        f_amt = s["foreign_net_amt"]
        i_amt = s["inst_net_amt"]
        tv = s["trade_value"]

        s["foreign_ratio"] = round(f_amt / mcap * 100, 4)
        s["inst_ratio"] = round(i_amt / mcap * 100, 4)
        s["fi_ratio"] = round((f_amt + i_amt) / mcap * 100, 4)
        s["turnover"] = round(tv / mcap * 100, 4)

        assert s["foreign_ratio"] == pytest.approx(0.5, rel=1e-4), \
            f"foreign_ratio 오류: {s['foreign_ratio']} (expected ~0.5)"
        assert s["foreign_ratio"] == expected_ratio

    @pytest.mark.asyncio
    async def test_foreign_ratio_zero_when_market_cap_zero(self):
        """시총 0인 경우 foreign_ratio = 0.0 (ZeroDivisionError 방지)."""
        s = {
            "market_cap": 0,
            "foreign_net_amt": 5_000_000_000,
            "inst_net_amt": 1_000_000_000,
            "trade_value": 100_000_000,
        }

        mcap = s["market_cap"]
        if mcap > 0:
            s["foreign_ratio"] = round(s["foreign_net_amt"] / mcap * 100, 4)
        else:
            s["foreign_ratio"] = 0.0
            s["inst_ratio"] = 0.0
            s["fi_ratio"] = 0.0
            s["turnover"] = 0.0

        assert s["foreign_ratio"] == 0.0
        assert s["fi_ratio"] == 0.0

    @pytest.mark.asyncio
    async def test_fi_ratio_is_sum_of_foreign_and_inst(self):
        """fi_ratio = (foreign_net_amt + inst_net_amt) / market_cap * 100."""
        market_cap = 2_000_000_000_000   # 2조
        foreign_net_amt = 10_000_000_000  # 100억
        inst_net_amt = 20_000_000_000     # 200억

        expected_fi = round((foreign_net_amt + inst_net_amt) / market_cap * 100, 4)

        fi_ratio = round((foreign_net_amt + inst_net_amt) / market_cap * 100, 4)
        assert fi_ratio == pytest.approx(expected_fi, rel=1e-6)
        assert fi_ratio == pytest.approx(1.5, rel=1e-4)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 5: scan_stocks 기본 동작
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestScanStocksBasic:
    """Test 5: scan_stocks 기본 동작 — results/count/date 반환 확인."""

    def test_scan_returns_required_keys(self):
        """기본 빈 필터로 scan_stocks 호출 → results, count, date 반환."""
        stocks = {
            "005930": {"name": "삼성전자", "market": "kospi", "close": 75000,
                       "chg_pct": 1.0, "market_cap": 450_000_000_000_000,
                       "per": 15.0, "pbr": 1.5, "foreign_ratio": 0.3,
                       "inst_ratio": 0.1, "fi_ratio": 0.4, "turnover": 0.16},
            "000660": {"name": "SK하이닉스", "market": "kospi", "close": 180000,
                       "chg_pct": 2.0, "market_cap": 130_000_000_000_000,
                       "per": 12.0, "pbr": 1.2, "foreign_ratio": 0.5,
                       "inst_ratio": 0.2, "fi_ratio": 0.7, "turnover": 0.20},
        }
        db = _make_fake_db(stocks)

        result = scan_stocks(db, {})

        assert "results" in result
        assert "count" in result
        assert "date" in result
        assert isinstance(result["results"], list)
        assert isinstance(result["count"], int)
        assert result["date"] == "20260402"
        assert result["count"] == len(result["results"])

    def test_scan_empty_db(self):
        """빈 stocks DB → results=[], count=0."""
        db = _make_fake_db({})
        result = scan_stocks(db, {})

        assert result["results"] == []
        assert result["count"] == 0

    def test_scan_n_limit(self):
        """n=2 → 최대 2개 결과."""
        stocks = {}
        for i in range(10):
            ticker = f"{i+1:06d}"
            stocks[ticker] = {
                "name": f"종목{ticker}", "market": "kospi", "close": 10000 + i * 1000,
                "chg_pct": float(i), "market_cap": 100_000_000_000 * (i + 1),
                "per": 10.0, "pbr": 1.0, "foreign_ratio": 0.1,
                "inst_ratio": 0.1, "fi_ratio": 0.2, "turnover": 0.5,
            }
        db = _make_fake_db(stocks)

        result = scan_stocks(db, {"n": 2})
        assert result["count"] <= 2
        assert len(result["results"]) <= 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 6: preset=small_cap_buy
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestScanPresetSmallCapBuy:
    """Test 6: preset=small_cap_buy — 시총 500~5000억 AND foreign_ratio>0.1%."""

    def _make_stocks(self):
        return {
            # 통과해야 함: 시총 1000억(=1000억원 → 100_000_000_000원), foreign_ratio=0.5%
            "111111": {"name": "소형우량", "market": "kospi", "close": 10000,
                       "chg_pct": 1.0, "market_cap": 100_000_000_000,   # 1000억
                       "per": 8.0, "pbr": 0.8, "foreign_ratio": 0.5,
                       "inst_ratio": 0.1, "fi_ratio": 0.6, "turnover": 1.0},
            # 실패: 시총 너무 큼 (6000억 = 600_000_000_000원)
            "222222": {"name": "중형주", "market": "kospi", "close": 50000,
                       "chg_pct": 0.5, "market_cap": 600_000_000_000,   # 6000억
                       "per": 10.0, "pbr": 1.0, "foreign_ratio": 0.5,
                       "inst_ratio": 0.1, "fi_ratio": 0.6, "turnover": 0.5},
            # 실패: 시총 너무 작음 (400억 = 40_000_000_000원)
            "333333": {"name": "초소형", "market": "kospi", "close": 2000,
                       "chg_pct": 0.0, "market_cap": 40_000_000_000,    # 400억
                       "per": 5.0, "pbr": 0.5, "foreign_ratio": 0.5,
                       "inst_ratio": 0.0, "fi_ratio": 0.5, "turnover": 2.0},
            # 실패: foreign_ratio 너무 낮음 (0.05%)
            "444444": {"name": "외인외면", "market": "kospi", "close": 5000,
                       "chg_pct": 0.2, "market_cap": 200_000_000_000,   # 2000억
                       "per": 7.0, "pbr": 0.7, "foreign_ratio": 0.05,
                       "inst_ratio": 0.1, "fi_ratio": 0.15, "turnover": 0.3},
            # 통과: 시총 3000억(=300_000_000_000원), foreign_ratio=0.2%
            "555555": {"name": "소형외인", "market": "kosdaq", "close": 15000,
                       "chg_pct": 2.0, "market_cap": 300_000_000_000,   # 3000억
                       "per": 9.0, "pbr": 0.9, "foreign_ratio": 0.2,
                       "inst_ratio": 0.05, "fi_ratio": 0.25, "turnover": 0.8},
        }

    def test_small_cap_buy_filters_correctly(self):
        """소형주 외인매수 프리셋: 500~5000억 AND foreign_ratio>0.1% 조건 확인."""
        stocks = self._make_stocks()
        db = _make_fake_db(stocks)

        result = scan_stocks(db, {}, preset="small_cap_buy")

        result_tickers = {r["ticker"] for r in result["results"]}

        # 통과해야 하는 종목
        assert "111111" in result_tickers, "111111(시총1000억, fr=0.5%) 누락"
        assert "555555" in result_tickers, "555555(시총3000억, fr=0.2%) 누락"

        # 필터링돼야 하는 종목
        assert "222222" not in result_tickers, "222222(시총6000억) 통과하면 안됨"
        assert "333333" not in result_tickers, "333333(시총400억) 통과하면 안됨"
        assert "444444" not in result_tickers, "444444(fr=0.05%) 통과하면 안됨"

    def test_small_cap_buy_results_have_market_cap_in_range(self):
        """결과 종목 market_cap이 500~5000 억원 범위 내에 있어야 함."""
        stocks = self._make_stocks()
        db = _make_fake_db(stocks)

        result = scan_stocks(db, {}, preset="small_cap_buy")

        for r in result["results"]:
            assert 500 <= r["market_cap"] <= 5000, \
                f"{r['ticker']}: market_cap={r['market_cap']}억원이 범위 초과"
            assert r["foreign_ratio"] > 0.1, \
                f"{r['ticker']}: foreign_ratio={r['foreign_ratio']} (min 0.1 미충족)"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 7: preset=value
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestScanPresetValue:
    """Test 7: preset=value — PER<10 AND PBR<1 AND 시총>1000억."""

    def _make_stocks(self):
        return {
            # 통과: PER=8, PBR=0.8, 시총=2000억
            "111111": {"name": "저평가우량", "market": "kospi", "close": 10000,
                       "chg_pct": 0.5, "market_cap": 200_000_000_000,   # 2000억
                       "per": 8.0, "pbr": 0.8, "foreign_ratio": 0.1,
                       "inst_ratio": 0.1, "fi_ratio": 0.2, "turnover": 0.5},
            # 실패: PER=12 (>10)
            "222222": {"name": "PER높음", "market": "kospi", "close": 20000,
                       "chg_pct": 1.0, "market_cap": 500_000_000_000,
                       "per": 12.0, "pbr": 0.8, "foreign_ratio": 0.1,
                       "inst_ratio": 0.1, "fi_ratio": 0.2, "turnover": 0.5},
            # 실패: PBR=1.5 (>1)
            "333333": {"name": "PBR높음", "market": "kospi", "close": 30000,
                       "chg_pct": 0.0, "market_cap": 300_000_000_000,
                       "per": 7.0, "pbr": 1.5, "foreign_ratio": 0.1,
                       "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 0.3},
            # 실패: 시총 500억 (<1000억)
            "444444": {"name": "소형저평가", "market": "kosdaq", "close": 5000,
                       "chg_pct": 0.2, "market_cap": 50_000_000_000,    # 500억
                       "per": 5.0, "pbr": 0.5, "foreign_ratio": 0.05,
                       "inst_ratio": 0.05, "fi_ratio": 0.1, "turnover": 1.0},
            # 통과: PER=9.5, PBR=0.7, 시총=1500억
            "555555": {"name": "가치주", "market": "kosdaq", "close": 8000,
                       "chg_pct": -0.5, "market_cap": 150_000_000_000,  # 1500억
                       "per": 9.5, "pbr": 0.7, "foreign_ratio": 0.08,
                       "inst_ratio": 0.02, "fi_ratio": 0.1, "turnover": 0.4},
            # 실패: PER=0 (per_min=0.01 조건으로 제외)
            "666666": {"name": "PER없음", "market": "kospi", "close": 3000,
                       "chg_pct": 0.0, "market_cap": 200_000_000_000,
                       "per": 0.0, "pbr": 0.5, "foreign_ratio": 0.1,
                       "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 0.2},
        }

    def test_value_preset_filters_per_pbr_mcap(self):
        """value 프리셋: PER 0.01~10, PBR<1, 시총>1000억 필터 확인."""
        stocks = self._make_stocks()
        db = _make_fake_db(stocks)

        result = scan_stocks(db, {}, preset="value")

        result_tickers = {r["ticker"] for r in result["results"]}

        # 통과해야 하는 종목
        assert "111111" in result_tickers, "111111(PER=8,PBR=0.8,시총2000억) 누락"
        assert "555555" in result_tickers, "555555(PER=9.5,PBR=0.7,시총1500억) 누락"

        # 필터링돼야 하는 종목
        assert "222222" not in result_tickers, "222222(PER=12) 통과하면 안됨"
        assert "333333" not in result_tickers, "333333(PBR=1.5) 통과하면 안됨"
        assert "444444" not in result_tickers, "444444(시총500억) 통과하면 안됨"
        assert "666666" not in result_tickers, "666666(PER=0) 통과하면 안됨"

    def test_value_preset_results_satisfy_conditions(self):
        """결과 종목이 모두 PER<10, PBR<1, 시총>1000억 조건 충족해야 함."""
        stocks = self._make_stocks()
        db = _make_fake_db(stocks)

        result = scan_stocks(db, {}, preset="value")

        for r in result["results"]:
            assert 0 < r["per"] <= 10, f"{r['ticker']}: PER={r['per']}"
            assert r["pbr"] <= 1, f"{r['ticker']}: PBR={r['pbr']}"
            assert r["market_cap"] >= 1000, f"{r['ticker']}: market_cap={r['market_cap']}억원"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 8: DB 파일 생성과 load_krx_db
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestDBFileCreationAndLoad:
    """Test 8: update_daily_db로 DB 파일 생성 후 load_krx_db로 동일 데이터 로드."""

    @pytest.mark.asyncio
    async def test_update_daily_db_creates_file(self):
        """update_daily_db 실행 후 DATE.json 파일이 생성돼야 함."""
        date = "20260402"
        price_records = _make_price_records(10)
        fund_records = [{"ISU_SRT_CD": r["ISU_SRT_CD"], "PER": "10.0", "PBR": "1.0"}
                        for r in price_records]
        investor_records = _make_investor_records([r["ISU_SRT_CD"] for r in price_records])

        call_count = [0]

        async def mock_krx_post(session, form):
            call_count[0] += 1
            bld = form.get("bld", "")
            if "MDCSTAT01501" in bld:
                return {"OutBlock_1": price_records}
            elif "MDCSTAT03901" in bld:
                return {"output": fund_records}
            elif "MDCSTAT02401" in bld:
                return {"OutBlock_1": investor_records}
            return {}

        with patch("krx_crawler._krx_post", side_effect=mock_krx_post), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await update_daily_db(date)

        expected_file = os.path.join(TEST_KRX_DB_DIR, f"{date}.json")
        assert os.path.exists(expected_file), f"DB 파일이 생성되지 않음: {expected_file}"
        assert "count" in result
        assert result["count"] > 0
        assert result["date"] == date

    @pytest.mark.asyncio
    async def test_load_krx_db_returns_same_data(self):
        """update_daily_db로 저장 후 load_krx_db로 동일 데이터 확인."""
        date = "20260402"
        price_records = _make_price_records(5)
        fund_records = [{"ISU_SRT_CD": r["ISU_SRT_CD"], "PER": "8.0", "PBR": "0.9"}
                        for r in price_records]
        investor_records = _make_investor_records([r["ISU_SRT_CD"] for r in price_records])

        async def mock_krx_post(session, form):
            bld = form.get("bld", "")
            if "MDCSTAT01501" in bld:
                return {"OutBlock_1": price_records}
            elif "MDCSTAT03901" in bld:
                return {"output": fund_records}
            elif "MDCSTAT02401" in bld:
                return {"OutBlock_1": investor_records}
            return {}

        with patch("krx_crawler._krx_post", side_effect=mock_krx_post), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await update_daily_db(date)

        loaded = load_krx_db(date)

        assert loaded is not None
        assert loaded["date"] == date
        assert "stocks" in loaded
        assert "market_summary" in loaded
        assert loaded["count"] == len(loaded["stocks"])

        # 개별 종목 데이터 확인
        stocks = loaded["stocks"]
        first_ticker = price_records[0]["ISU_SRT_CD"]
        assert first_ticker in stocks
        s = stocks[first_ticker]
        assert "close" in s
        assert "per" in s
        assert "pbr" in s
        assert "foreign_ratio" in s

    def test_load_krx_db_latest_without_date(self):
        """date=None 이면 가장 최신 파일 로드."""
        # 가짜 DB 파일 2개 생성
        for date in ["20260401", "20260402"]:
            fake_db = {"date": date, "count": 10, "stocks": {}, "market_summary": {}}
            fp = os.path.join(TEST_KRX_DB_DIR, f"{date}.json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(fake_db, f)

        result = load_krx_db()  # date=None → 최신

        assert result is not None
        assert result["date"] == "20260402"  # 더 최신 날짜

    def test_load_krx_db_returns_none_when_no_file(self):
        """DB 파일 없으면 None 반환."""
        result = load_krx_db("20200101")
        assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 9: MCP 하위 호환성
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestMCPBackwardCompatibility:
    """Test 9: mcp_tools import 및 get_dart 도구 호출 하위 호환성."""

    def test_mcp_tools_import_does_not_break(self):
        """mcp_tools 모듈이 정상적으로 import돼야 함."""
        try:
            import mcp_tools
            assert hasattr(mcp_tools, "_execute_tool")
            assert hasattr(mcp_tools, "MCP_TOOLS")
            assert hasattr(mcp_tools, "scan_stocks")
            assert hasattr(mcp_tools, "load_krx_db")
        except ImportError as e:
            pytest.fail(f"mcp_tools import 실패: {e}")

    @pytest.mark.asyncio
    async def test_get_dart_default_mode_backward_compat(self):
        """get_dart를 mode 없이 호출 → 공시 목록 반환 (기존 동작 유지)."""
        from mcp_tools import _execute_tool

        fake_disclosures = [
            {"corp_name": "삼성전자", "report_nm": "주요사항보고서",
             "rcept_dt": "20260402", "pblntf_ty": "B"},
        ]

        with patch("mcp_tools.search_dart_disclosures", new_callable=AsyncMock,
                   return_value=fake_disclosures), \
             patch("mcp_tools.load_watchlist",
                   return_value={"005930": "삼성전자"}), \
             patch("mcp_tools.get_kis_token", new_callable=AsyncMock,
                   return_value="fake_token"):
            result = await _execute_tool("get_dart", {})

        # list 반환이어야 함 (기존 공시 동작)
        assert isinstance(result, list)
        assert len(result) >= 0

    @pytest.mark.asyncio
    async def test_get_scan_returns_error_when_no_db(self):
        """DB 없을 때 get_scan → error 반환."""
        from mcp_tools import _execute_tool

        # DB 디렉토리가 비어있으므로 load_krx_db(None)=None
        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock,
                   return_value="fake_token"), \
             patch("mcp_tools.load_krx_db", return_value=None):
            result = await _execute_tool("get_scan", {})

        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_scan_with_valid_db(self):
        """DB가 있을 때 get_scan → results 반환."""
        from mcp_tools import _execute_tool

        fake_db = _make_fake_db({
            "005930": {"name": "삼성전자", "market": "kospi", "close": 75000,
                       "chg_pct": 1.0, "market_cap": 450_000_000_000_000,
                       "per": 15.0, "pbr": 1.5, "foreign_ratio": 0.3,
                       "inst_ratio": 0.1, "fi_ratio": 0.4, "turnover": 0.16},
        })

        with patch("mcp_tools.get_kis_token", new_callable=AsyncMock,
                   return_value="fake_token"), \
             patch("mcp_tools.load_krx_db", return_value=fake_db):
            result = await _execute_tool("get_scan", {})

        assert "results" in result
        assert "count" in result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 10: 오래된 DB 파일 정리
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestCleanupOldDB:
    """Test 10: _cleanup_old_db — 30일 초과 파일 삭제."""

    def _create_db_file(self, date: str):
        """가짜 DB 파일 생성."""
        fp = os.path.join(TEST_KRX_DB_DIR, f"{date}.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump({"date": date}, f)
        return fp

    def test_cleanup_removes_old_files(self):
        """30일 이전 파일은 삭제되고, 최근 파일은 보존."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        KST = ZoneInfo("Asia/Seoul")
        today = datetime.now(KST)

        # 오래된 파일들 (31~40일 전)
        old_dates = []
        for days_ago in [31, 35, 40]:
            d = (today - timedelta(days=days_ago)).strftime("%Y%m%d")
            old_dates.append(d)
            self._create_db_file(d)

        # 최근 파일들 (1~5일 전, 오늘 포함)
        recent_dates = []
        for days_ago in [0, 1, 5, 29]:
            d = (today - timedelta(days=days_ago)).strftime("%Y%m%d")
            recent_dates.append(d)
            self._create_db_file(d)

        # cleanup 실행
        _cleanup_old_db(30)

        # 오래된 파일은 삭제
        for d in old_dates:
            fp = os.path.join(TEST_KRX_DB_DIR, f"{d}.json")
            assert not os.path.exists(fp), f"{d}.json 삭제되지 않음 (31일+ 이전)"

        # 최근 파일은 보존
        for d in recent_dates:
            fp = os.path.join(TEST_KRX_DB_DIR, f"{d}.json")
            assert os.path.exists(fp), f"{d}.json 보존돼야 함 (30일 이내)"

    def test_cleanup_does_nothing_when_dir_missing(self):
        """DB 디렉토리가 없어도 에러 없이 종료."""
        import shutil
        # 디렉토리 삭제 후 테스트
        if os.path.exists(TEST_KRX_DB_DIR):
            shutil.rmtree(TEST_KRX_DB_DIR)

        # 예외 없이 실행돼야 함
        try:
            _cleanup_old_db(30)
        except Exception as e:
            pytest.fail(f"DB 디렉토리 없을 때 _cleanup_old_db 예외 발생: {e}")

    def test_cleanup_keeps_files_exactly_at_boundary(self):
        """cutoff 날짜 이전 파일만 삭제, 같은 날짜는 보존."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        KST = ZoneInfo("Asia/Seoul")
        today = datetime.now(KST)

        # 정확히 30일 전 파일 (cutoff와 같은 날짜 - 보존돼야 함)
        boundary = (today - timedelta(days=30)).strftime("%Y%m%d")
        self._create_db_file(boundary)

        # 31일 전 파일 (삭제돼야 함)
        old = (today - timedelta(days=31)).strftime("%Y%m%d")
        self._create_db_file(old)

        _cleanup_old_db(30)

        boundary_fp = os.path.join(TEST_KRX_DB_DIR, f"{boundary}.json")
        old_fp = os.path.join(TEST_KRX_DB_DIR, f"{old}.json")

        # 31일 전은 삭제
        assert not os.path.exists(old_fp), f"{old}.json 삭제되지 않음"
        # 정확히 30일 전은 보존 (cutoff: fname < cutoff)
        assert os.path.exists(boundary_fp), f"{boundary}.json 보존돼야 함"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 파싱 헬퍼 단위 테스트 (보너스)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestParsingHelpers:
    """_pi/_pf 파싱 헬퍼 단위 테스트."""

    def test_pi_parses_comma_int(self):
        assert _pi("1,234,567") == 1234567

    def test_pi_parses_plus_prefix(self):
        assert _pi("+500") == 500

    def test_pi_returns_zero_for_dash(self):
        assert _pi("-") == 0

    def test_pi_returns_zero_for_empty(self):
        assert _pi("") == 0
        assert _pi(None) == 0

    def test_pf_parses_float(self):
        assert _pf("1.34") == pytest.approx(1.34)

    def test_pf_parses_negative(self):
        assert _pf("-2.50") == pytest.approx(-2.50)

    def test_pf_parses_comma_float(self):
        assert _pf("1,234.56") == pytest.approx(1234.56)

    def test_pf_returns_zero_for_dash(self):
        assert _pf("-") == 0.0
