"""
scan_stocks 프리셋 6종 + market filter + market_avg_chg 테스트 (pytest)
외부 HTTP 호출 없음 — 가짜 DB dict을 직접 구성해 테스트.
"""
import json
import os
import shutil
import sys
import types
import pytest

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
TEST_ROOT = "/tmp/test_scan_presets"
TEST_KRX_DB_DIR = os.path.join(TEST_ROOT, "krx_db")

import krx_crawler
krx_crawler.KRX_DB_DIR = TEST_KRX_DB_DIR

from krx_crawler import scan_stocks, _get_foreign_streak_data, KRX_DB_DIR


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# autouse fixture: 각 테스트마다 임시 디렉토리 초기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
@pytest.fixture(autouse=True)
def clean_test_dir():
    """각 테스트 전후 임시 디렉토리 정리."""
    os.makedirs(TEST_KRX_DB_DIR, exist_ok=True)
    yield
    if os.path.exists(TEST_ROOT):
        shutil.rmtree(TEST_ROOT)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 DB 빌더 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _make_db(stocks: dict, kospi_avg_chg: float = 0.0, kosdaq_avg_chg: float = 0.0,
             date: str = "20260403") -> dict:
    """테스트용 fake DB dict 생성."""
    return {
        "date": date,
        "market_summary": {
            "kospi_avg_chg": kospi_avg_chg,
            "kosdaq_avg_chg": kosdaq_avg_chg,
        },
        "stocks": stocks,
    }


def _mcap(eok: float) -> int:
    """억원 → 원 변환."""
    return int(eok * 1_0000_0000)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. relative_strength
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_relative_strength():
    """시장 avg_chg=-5%. threshold=-5+3=-2%. A(-1%)pass, B(-3%)fail, C(-8%)fail."""
    # market_avg_chg = (-5 + -5) / 2 = -5.0
    stocks = {
        "A": {"name": "A종목", "market": "kospi", "close": 10000,
              "chg_pct": -1.0, "market_cap": _mcap(2000),
              "per": 10.0, "pbr": 1.0,
              "foreign_ratio": 0.5, "inst_ratio": 0.0, "fi_ratio": 0.5, "turnover": 1.0},
        "B": {"name": "B종목", "market": "kospi", "close": 10000,
              "chg_pct": -3.0, "market_cap": _mcap(2000),
              "per": 10.0, "pbr": 1.0,
              "foreign_ratio": 0.2, "inst_ratio": 0.0, "fi_ratio": 0.2, "turnover": 1.0},
        "C": {"name": "C종목", "market": "kospi", "close": 10000,
              "chg_pct": -8.0, "market_cap": _mcap(2000),
              "per": 10.0, "pbr": 1.0,
              "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 1.0},
    }
    db = _make_db(stocks, kospi_avg_chg=-5.0, kosdaq_avg_chg=-5.0)
    result = scan_stocks(db, {}, preset="relative_strength")

    tickers = [r["ticker"] for r in result["results"]]
    assert "A" in tickers, f"A should pass: got {tickers}"
    assert "B" not in tickers, f"B should fail (chg=-3% < threshold=-2%): got {tickers}"
    assert "C" not in tickers, f"C should fail (chg=-8% < threshold=-2%): got {tickers}"
    assert "market_avg_chg" in result, "market_avg_chg field must be present"
    assert result["market_avg_chg"] == -5.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. small_cap_buy
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_small_cap_buy():
    """시총 300억(too small), 1000억(ok), 3000억(ok), 8000억(too big)."""
    stocks = {
        "SMALL": {"name": "소형미달", "market": "kospi", "close": 5000,
                  "chg_pct": 1.0, "market_cap": _mcap(300),
                  "per": 10.0, "pbr": 1.0,
                  "foreign_ratio": 0.5, "inst_ratio": 0.0, "fi_ratio": 0.5, "turnover": 1.0},
        "OK1":   {"name": "적정1", "market": "kospi", "close": 5000,
                  "chg_pct": 1.0, "market_cap": _mcap(1000),
                  "per": 10.0, "pbr": 1.0,
                  "foreign_ratio": 0.3, "inst_ratio": 0.0, "fi_ratio": 0.3, "turnover": 1.0},
        "OK2":   {"name": "적정2", "market": "kosdaq", "close": 5000,
                  "chg_pct": 1.0, "market_cap": _mcap(3000),
                  "per": 10.0, "pbr": 1.0,
                  "foreign_ratio": 0.5, "inst_ratio": 0.0, "fi_ratio": 0.5, "turnover": 1.0},
        "BIG":   {"name": "대형초과", "market": "kospi", "close": 5000,
                  "chg_pct": 1.0, "market_cap": _mcap(8000),
                  "per": 10.0, "pbr": 1.0,
                  "foreign_ratio": 0.8, "inst_ratio": 0.0, "fi_ratio": 0.8, "turnover": 1.0},
    }
    db = _make_db(stocks)
    result = scan_stocks(db, {}, preset="small_cap_buy")

    tickers = [r["ticker"] for r in result["results"]]
    assert "OK1" in tickers, f"OK1(1000억) should pass: {tickers}"
    assert "OK2" in tickers, f"OK2(3000억) should pass: {tickers}"
    assert "SMALL" not in tickers, f"SMALL(300억) should fail: {tickers}"
    assert "BIG" not in tickers, f"BIG(8000억) should fail: {tickers}"

    # foreign_ratio 내림차순 정렬 확인 (OK2=0.5 > OK1=0.3)
    if len(result["results"]) >= 2:
        assert result["results"][0]["ticker"] == "OK2", \
            "Sort by foreign_ratio desc: OK2(0.5) should come first"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. value
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_value():
    """PER>0, PER<10, PBR>0, PBR<1, 시총>1000억."""
    stocks = {
        # pass: PER=5, PBR=0.7, mcap=2000억
        "PASS":     {"name": "통과", "market": "kospi", "close": 10000,
                     "chg_pct": 0.0, "market_cap": _mcap(2000),
                     "per": 5.0, "pbr": 0.7,
                     "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 0.5},
        # fail: PER=15 (too high)
        "FAIL_PER": {"name": "PER과다", "market": "kospi", "close": 10000,
                     "chg_pct": 0.0, "market_cap": _mcap(2000),
                     "per": 15.0, "pbr": 0.5,
                     "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 0.5},
        # fail: PBR=1.5 (too high)
        "FAIL_PBR": {"name": "PBR과다", "market": "kospi", "close": 10000,
                     "chg_pct": 0.0, "market_cap": _mcap(2000),
                     "per": 3.0, "pbr": 1.5,
                     "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 0.5},
        # fail: PER=0 (excluded by per_min=0.01)
        "FAIL_PER0": {"name": "PER제로", "market": "kospi", "close": 10000,
                      "chg_pct": 0.0, "market_cap": _mcap(2000),
                      "per": 0.0, "pbr": 0.3,
                      "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 0.5},
        # fail: mcap=500억 (below 1000억 minimum)
        "FAIL_MCAP": {"name": "시총미달", "market": "kospi", "close": 10000,
                      "chg_pct": 0.0, "market_cap": _mcap(500),
                      "per": 7.0, "pbr": 0.8,
                      "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 0.5},
    }
    db = _make_db(stocks)
    result = scan_stocks(db, {}, preset="value")

    tickers = [r["ticker"] for r in result["results"]]
    assert tickers == ["PASS"], f"Only PASS should pass the value preset: {tickers}"

    # PBR 오름차순 정렬 확인 (preset sort='pbr', reverse=False)
    assert result["results"][0]["ticker"] == "PASS"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. momentum
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_momentum():
    """chg_pct>3% AND turnover>1% 통과 여부."""
    stocks = {
        "PASS1": {"name": "모멘텀1", "market": "kospi", "close": 10000,
                  "chg_pct": 5.0, "market_cap": _mcap(2000),
                  "per": 10.0, "pbr": 1.0,
                  "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 2.0},
        "PASS2": {"name": "모멘텀2", "market": "kospi", "close": 10000,
                  "chg_pct": 4.0, "market_cap": _mcap(2000),
                  "per": 10.0, "pbr": 1.0,
                  "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 1.5},
        # fail: chg=2% (below 3%)
        "FAIL_CHG": {"name": "등락미달", "market": "kospi", "close": 10000,
                     "chg_pct": 2.0, "market_cap": _mcap(2000),
                     "per": 10.0, "pbr": 1.0,
                     "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 2.0},
        # fail: turnover=0.5% (below 1%)
        "FAIL_TURN": {"name": "거래미달", "market": "kospi", "close": 10000,
                      "chg_pct": 5.0, "market_cap": _mcap(2000),
                      "per": 10.0, "pbr": 1.0,
                      "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 0.5},
    }
    db = _make_db(stocks)
    result = scan_stocks(db, {}, preset="momentum")

    tickers = [r["ticker"] for r in result["results"]]
    assert "PASS1" in tickers, f"PASS1 should pass: {tickers}"
    assert "PASS2" in tickers, f"PASS2 should pass: {tickers}"
    assert "FAIL_CHG" not in tickers, f"FAIL_CHG should fail: {tickers}"
    assert "FAIL_TURN" not in tickers, f"FAIL_TURN should fail: {tickers}"

    # chg_pct 내림차순 정렬 (PASS1=5% > PASS2=4%)
    assert result["results"][0]["ticker"] == "PASS1", \
        "Sort by chg_pct desc: PASS1(5%) should come first"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. oversold
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_oversold():
    """chg_pct<=-7% 통과. 낙폭 큰 순(오름차순) 정렬."""
    stocks = {
        "DROP10": {"name": "낙폭10", "market": "kospi", "close": 10000,
                   "chg_pct": -10.0, "market_cap": _mcap(2000),
                   "per": 10.0, "pbr": 1.0,
                   "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 1.0},
        "DROP7":  {"name": "낙폭7", "market": "kospi", "close": 10000,
                   "chg_pct": -7.0, "market_cap": _mcap(2000),
                   "per": 10.0, "pbr": 1.0,
                   "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 1.0},
        "FLAT":   {"name": "보합", "market": "kospi", "close": 10000,
                   "chg_pct": -5.0, "market_cap": _mcap(2000),
                   "per": 10.0, "pbr": 1.0,
                   "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 1.0},
        "PLUS":   {"name": "상승", "market": "kospi", "close": 10000,
                   "chg_pct": 2.0, "market_cap": _mcap(2000),
                   "per": 10.0, "pbr": 1.0,
                   "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 1.0},
    }
    db = _make_db(stocks)
    result = scan_stocks(db, {}, preset="oversold")

    tickers = [r["ticker"] for r in result["results"]]
    assert "DROP10" in tickers, f"DROP10(-10%) should pass: {tickers}"
    assert "DROP7" in tickers, f"DROP7(-7%) should pass: {tickers}"
    assert "FLAT" not in tickers, f"FLAT(-5%) should fail: {tickers}"
    assert "PLUS" not in tickers, f"PLUS(+2%) should fail: {tickers}"

    # 낙폭 큰 순 (chg_pct ascending): DROP10 먼저
    first = result["results"][0]["ticker"]
    assert first == "DROP10", f"DROP10(-10%) should come before DROP7(-7%), got {first} first"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. foreign_streak
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _write_fake_db_file(date: str, stocks: dict):
    """TEST_KRX_DB_DIR에 가짜 DB JSON 파일 쓰기."""
    db = {
        "date": date,
        "market_summary": {"kospi_avg_chg": 0.0, "kosdaq_avg_chg": 0.0},
        "stocks": stocks,
    }
    os.makedirs(TEST_KRX_DB_DIR, exist_ok=True)
    path = os.path.join(TEST_KRX_DB_DIR, f"{date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f)


def _make_streak_stock(foreign_net_amt: int, foreign_ratio: float = 0.2,
                        market: str = "kospi") -> dict:
    return {
        "name": "테스트", "market": market, "close": 10000,
        "chg_pct": 0.0, "market_cap": _mcap(2000),
        "per": 10.0, "pbr": 1.0,
        "foreign_net_amt": foreign_net_amt,
        "foreign_ratio": foreign_ratio,
        "inst_ratio": 0.0, "fi_ratio": 0.2, "turnover": 1.0,
    }


def test_foreign_streak_5days():
    """5일 연속 외인 순매수인 A 통과, 교대로 음수인 B 탈락."""
    dates = ["20260328", "20260329", "20260330", "20260331", "20260401"]

    for i, d in enumerate(dates):
        # A: 항상 양수
        # B: 짝수 인덱스는 양수, 홀수는 음수 → 연속 아님
        b_amt = 1000 if i % 2 == 0 else -1000
        stocks = {
            "A": _make_streak_stock(foreign_net_amt=1000, foreign_ratio=0.2),
            "B": _make_streak_stock(foreign_net_amt=b_amt, foreign_ratio=0.1),
        }
        _write_fake_db_file(d, stocks)

    # scan_stocks는 db["date"] 기준으로 _get_foreign_streak_data 호출
    latest_date = "20260401"
    latest_db_stocks = {
        "A": _make_streak_stock(foreign_net_amt=1000, foreign_ratio=0.2),
        "B": _make_streak_stock(foreign_net_amt=1000, foreign_ratio=0.1),
    }
    db = _make_db(latest_db_stocks, date=latest_date)
    result = scan_stocks(db, {}, preset="foreign_streak")

    tickers = [r["ticker"] for r in result["results"]]
    assert "A" in tickers, f"A(연속 5일 순매수) should pass: {tickers}"
    assert "B" not in tickers, f"B(중간에 음수) should fail: {tickers}"

    # cum_foreign_ratio 필드 존재 확인
    if result["results"]:
        assert "cum_foreign_ratio" in result["results"][0], \
            "cum_foreign_ratio field must be present in results"

    # days_available=5
    assert result.get("days_available") == 5, \
        f"days_available should be 5, got {result.get('days_available')}"


def test_foreign_streak_3days():
    """DB 파일이 3개뿐이면 days_available=3으로 동작."""
    dates = ["20260330", "20260331", "20260401"]

    for d in dates:
        stocks = {
            "A": _make_streak_stock(foreign_net_amt=1000, foreign_ratio=0.2),
        }
        _write_fake_db_file(d, stocks)

    latest_db_stocks = {
        "A": _make_streak_stock(foreign_net_amt=1000, foreign_ratio=0.2),
    }
    db = _make_db(latest_db_stocks, date="20260401")
    result = scan_stocks(db, {}, preset="foreign_streak")

    assert result.get("days_available") == 3, \
        f"days_available should be 3 when only 3 files exist, got {result.get('days_available')}"
    tickers = [r["ticker"] for r in result["results"]]
    assert "A" in tickers, f"A should pass with 3-day streak: {tickers}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. market filter
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_market_filter():
    """market 필터: kospi/kosdaq/all 각각 정확히 필터링되는지."""
    stocks = {
        "KP1": {"name": "코스피1", "market": "kospi", "close": 10000,
                "chg_pct": 1.0, "market_cap": _mcap(2000),
                "per": 10.0, "pbr": 1.0,
                "foreign_ratio": 0.1, "inst_ratio": 0.0, "fi_ratio": 0.1, "turnover": 1.0},
        "KP2": {"name": "코스피2", "market": "kospi", "close": 10000,
                "chg_pct": 2.0, "market_cap": _mcap(3000),
                "per": 10.0, "pbr": 1.0,
                "foreign_ratio": 0.2, "inst_ratio": 0.0, "fi_ratio": 0.2, "turnover": 1.0},
        "KQ1": {"name": "코스닥1", "market": "kosdaq", "close": 10000,
                "chg_pct": 1.5, "market_cap": _mcap(1500),
                "per": 10.0, "pbr": 1.0,
                "foreign_ratio": 0.15, "inst_ratio": 0.0, "fi_ratio": 0.15, "turnover": 1.0},
        "KQ2": {"name": "코스닥2", "market": "kosdaq", "close": 10000,
                "chg_pct": 0.5, "market_cap": _mcap(800),
                "per": 10.0, "pbr": 1.0,
                "foreign_ratio": 0.05, "inst_ratio": 0.0, "fi_ratio": 0.05, "turnover": 1.0},
    }
    db = _make_db(stocks)

    # kospi only
    result_kp = scan_stocks(db, {"market": "kospi"})
    kp_tickers = {r["ticker"] for r in result_kp["results"]}
    assert kp_tickers == {"KP1", "KP2"}, f"kospi filter: expected {{KP1, KP2}}, got {kp_tickers}"

    # kosdaq only
    result_kq = scan_stocks(db, {"market": "kosdaq"})
    kq_tickers = {r["ticker"] for r in result_kq["results"]}
    assert kq_tickers == {"KQ1", "KQ2"}, f"kosdaq filter: expected {{KQ1, KQ2}}, got {kq_tickers}"

    # all
    result_all = scan_stocks(db, {"market": "all"})
    all_tickers = {r["ticker"] for r in result_all["results"]}
    assert all_tickers == {"KP1", "KP2", "KQ1", "KQ2"}, \
        f"all filter: expected all 4 stocks, got {all_tickers}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. market_avg_chg in output
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def test_market_avg_chg_in_output():
    """어떤 프리셋 호출이든 결과에 market_avg_chg 필드가 있어야 한다."""
    stocks = {
        "X": {"name": "X종목", "market": "kospi", "close": 10000,
              "chg_pct": 0.5, "market_cap": _mcap(2000),
              "per": 8.0, "pbr": 0.8,
              "foreign_ratio": 0.2, "inst_ratio": 0.1, "fi_ratio": 0.3, "turnover": 1.5},
    }
    db = _make_db(stocks, kospi_avg_chg=1.2, kosdaq_avg_chg=0.8)

    # 프리셋 없이 기본 호출
    result = scan_stocks(db, {})
    assert "market_avg_chg" in result, "market_avg_chg must be in plain scan_stocks output"
    # (1.2 + 0.8) / 2 = 1.0
    assert result["market_avg_chg"] == 1.0, \
        f"market_avg_chg should be 1.0, got {result['market_avg_chg']}"

    # 각 프리셋별로도 확인
    for preset_name in ["relative_strength", "small_cap_buy", "value", "momentum", "oversold"]:
        r = scan_stocks(db, {}, preset=preset_name)
        assert "market_avg_chg" in r, \
            f"market_avg_chg missing from preset '{preset_name}' output"
