"""db_collector 핵심 함수 unit test — SQLite 임시 DB + mock.

대상:
- _pi / _pf: KRX comma-formatted 변환
- _parse_market_records: KRX 응답 파싱 (OPEN API / 크롤링)
- _ma / _rsi / _macd / _atr / _volatility_20d: 기술지표
- _calc_vp: 매물대 (volume profile)
- _volume_ratio: 거래량 비율
- _classify_sector: 섹터 분류
- scan_stocks: 프리셋 필터링
- collect_daily: 주말 가드
"""
import os
import sqlite3
import asyncio
from unittest.mock import patch

import pytest

# numpy는 db_collector가 module-level에서 사용
import numpy as np  # noqa: F401

from db_collector import (
    _pi, _pf,
    _parse_market_records,
    _ma, _rsi, _macd, _atr, _volatility_20d,
    _calc_vp, _volume_ratio,
    _classify_sector,
    scan_stocks, PRESETS,
    collect_daily,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. _pi / _pf — KRX 변환
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_pi_parses_comma_strings():
    """KRX comma-formatted → int."""
    assert _pi("1,234,567") == 1234567
    assert _pi("70,000") == 70000


def test_pi_handles_edge_cases():
    """빈값/None/'-' → 0."""
    assert _pi(None) == 0
    assert _pi("") == 0
    assert _pi("-") == 0
    assert _pi("+1,500") == 1500


def test_pf_parses_decimal_strings():
    """KRX float 변환."""
    assert _pf("1.45") == 1.45
    assert _pf("1,234.56") == 1234.56
    assert _pf("-2.5") == -2.5
    assert _pf(None) == 0.0
    assert _pf("-") == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. _parse_market_records — KRX 응답 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_parse_market_records_openapi_format():
    """OPEN API 필드 (ISU_CD/ISU_NM)."""
    records = [
        {"ISU_CD": "005930", "ISU_NM": "삼성전자",
         "TDD_CLSPRC": "70,000", "FLUC_RT": "1.45",
         "ACC_TRDVOL": "10,000,000", "ACC_TRDVAL": "700,000,000",
         "MKTCAP": "500,000,000,000"},
    ]
    result = _parse_market_records(records, "STK")
    assert len(result) == 1
    assert result[0]["ticker"] == "005930"
    assert result[0]["name"] == "삼성전자"
    assert result[0]["market"] == "kospi"
    assert result[0]["close"] == 70000
    assert result[0]["chg_pct"] == 1.45


def test_parse_market_records_isin_to_6_digit():
    """ISIN(KR7xxxxxxx000) → 6자리 ticker 추출."""
    records = [
        {"ISU_CD": "KR7005930003", "ISU_NM": "삼성전자",
         "TDD_CLSPRC": "70000", "FLUC_RT": "0",
         "ACC_TRDVOL": "0", "ACC_TRDVAL": "0", "MKTCAP": "0"},
    ]
    result = _parse_market_records(records, "STK")
    assert result[0]["ticker"] == "005930"


def test_parse_market_records_kosdaq_label():
    """market=KSQ → 'kosdaq'."""
    records = [
        {"ISU_SRT_CD": "247540", "ISU_ABBRV": "에코프로비엠",
         "TDD_CLSPRC": "200000", "FLUC_RT": "2",
         "ACC_TRDVOL": "0", "ACC_TRDVAL": "0", "MKTCAP": "0"},
    ]
    result = _parse_market_records(records, "KSQ")
    assert result[0]["market"] == "kosdaq"


def test_parse_market_records_skips_invalid_ticker():
    """6자리 아닌 ticker 스킵."""
    records = [
        {"ISU_CD": "", "ISU_NM": "잘못된"},
        {"ISU_CD": "12345", "ISU_NM": "짧은"},
    ]
    result = _parse_market_records(records, "STK")
    assert result == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. 기술지표 — _ma, _rsi, _macd, _atr, _volatility_20d
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_ma_simple_average():
    """MA = mean(first n elements)."""
    arr = [10.0, 20.0, 30.0, 40.0, 50.0]
    # _ma(arr, 3)는 arr[:3]의 평균 → (10+20+30)/3 = 20.0
    assert _ma(arr, 3) == 20.0
    assert _ma(arr, 5) == 30.0


def test_ma_insufficient_data():
    """len(arr) < n → None."""
    assert _ma([1.0, 2.0], 5) is None


def test_rsi_returns_value_in_range():
    """RSI는 0~100."""
    closes = [100.0 + i * 0.5 for i in range(50)]  # 상승 추세
    rsi = _rsi(closes, period=14)
    assert rsi is not None
    assert 0 <= rsi <= 100


def test_rsi_insufficient_data():
    """len < period+1 → None."""
    assert _rsi([100.0, 101.0], period=14) is None


def test_rsi_no_losses_returns_100():
    """단조 하락(closes는 최신→과거 순) → 음수 변화 = 손실 0 → RSI 100."""
    # closes는 최신→과거 순 → [i] - [i+1] > 0 이려면 [i]가 [i+1]보다 커야
    # 단조 상승 시 changes 모두 양수 → losses=0 → RSI=100
    closes = [200.0 - i for i in range(30)]  # 200, 199, 198... (최신=200 가장 큼)
    rsi = _rsi(closes, period=14)
    assert rsi == 100.0


def test_macd_returns_three_values():
    """MACD line, signal, histogram 반환."""
    closes = [100.0 + (i % 10) * 0.5 for i in range(60)]
    macd, signal, hist = _macd(closes)
    assert macd is not None
    assert signal is not None
    assert hist is not None
    # hist = macd - signal
    assert abs(hist - (macd - signal)) < 1e-6


def test_macd_insufficient_data():
    """slow+signal 부족 → (None, None, None)."""
    macd, signal, hist = _macd([100.0, 101.0], slow=26, signal=9)
    assert macd is None and signal is None and hist is None


def test_atr_returns_positive():
    """ATR = average true range."""
    closes = [100.0 + i * 0.3 for i in range(30)]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    atr = _atr(closes, highs, lows, period=14)
    assert atr is not None
    assert atr > 0


def test_volatility_20d_returns_positive():
    """20일 변동성 ≥ 0."""
    closes = [100.0, 102.0, 101.5, 103.0, 101.0] * 5  # 25개
    vol = _volatility_20d(closes)
    assert vol is not None
    assert vol >= 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. _calc_vp — 볼륨 프로파일
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_calc_vp_returns_poc_and_va():
    """매물대 결과 dict 구조."""
    closes = [100.0 + i * 0.5 for i in range(50)]
    volumes = [1000 + i * 10 for i in range(50)]
    result = _calc_vp(closes, volumes, n=50, n_bins=20)
    assert "poc" in result
    assert "va_high" in result
    assert "va_low" in result
    assert "position" in result
    # 정상 데이터 → 모두 not None
    assert result["poc"] is not None
    assert result["va_high"] >= result["va_low"]


def test_calc_vp_insufficient_data():
    """30개 미만 → None dict."""
    result = _calc_vp([100.0] * 20, [1000] * 20, n=20)
    assert result["poc"] is None
    assert result["position"] is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. _volume_ratio
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_volume_ratio_doubled():
    """최근 5일 vol 2배 / 그 이전 5일 vol → 2.0."""
    volumes = [2000, 2000, 2000, 2000, 2000, 1000, 1000, 1000, 1000, 1000]
    ratio = _volume_ratio(volumes, recent=5, prev_offset=5)
    assert ratio == 2.0


def test_volume_ratio_insufficient():
    """data 부족 → None."""
    ratio = _volume_ratio([100, 200], recent=5, prev_offset=5)
    assert ratio is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. _classify_sector — 섹터 분류 (override + std_code)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_classify_sector_returns_string():
    """결과는 항상 str."""
    s = _classify_sector("999999", "테스트", "")
    assert isinstance(s, str)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. scan_stocks — 프리셋 필터링
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _make_fake_db():
    """scan_stocks용 mock DB."""
    return {
        "date": "20260520",
        "stocks": {
            "005930": {
                "ticker": "005930", "name": "삼성전자", "market": "kospi",
                "close": 70000, "chg_pct": 1.5, "market_cap": 5000000000000,
                "foreign_ratio": 0.3, "fi_ratio": 0.4,
                "per": 12.5, "pbr": 1.2, "turnover": 1.2,
            },
            "000660": {
                "ticker": "000660", "name": "SK하이닉스", "market": "kospi",
                "close": 200000, "chg_pct": 2.0, "market_cap": 1500000000000,
                "foreign_ratio": 0.5, "fi_ratio": 0.6,
                "per": 8.0, "pbr": 0.8, "turnover": 2.0,
            },
            "247540": {
                "ticker": "247540", "name": "에코프로비엠", "market": "kosdaq",
                "close": 200000, "chg_pct": -5.0, "market_cap": 200000000000,
                "foreign_ratio": 0.05, "fi_ratio": -0.2,
                "per": 25.0, "pbr": 3.0, "turnover": 5.0,
            },
        },
        "market_summary": {"kospi_avg_chg": 0.5, "kosdaq_avg_chg": -1.0},
    }


def test_scan_stocks_value_preset_filters_low_per_pbr():
    """value 프리셋: PER<10 AND PBR<1 AND mcap>1000억."""
    db = _make_fake_db()
    result = scan_stocks(db, {}, preset="value")
    # SK하이닉스만 통과 (per=8, pbr=0.8, mcap 1.5조)
    tickers = {r["ticker"] for r in result["results"]}
    assert "000660" in tickers
    # 삼성전자는 pbr 1.2 → 탈락, 에코프로비엠은 per 25 → 탈락
    assert "005930" not in tickers
    assert "247540" not in tickers


def test_scan_stocks_oversold_preset():
    """oversold 프리셋: chg_pct ≤ -7 — fake db 중 없음 → 빈 결과."""
    db = _make_fake_db()
    result = scan_stocks(db, {}, preset="oversold")
    assert result["count"] == 0


def test_scan_stocks_custom_filter():
    """custom 필터: chg_pct ≥ 1.0."""
    db = _make_fake_db()
    result = scan_stocks(db, {"chg_pct_min": 1.0, "n": 100})
    tickers = {r["ticker"] for r in result["results"]}
    assert tickers == {"005930", "000660"}


def test_scan_stocks_n_limit():
    """n=1 → 결과 1건만."""
    db = _make_fake_db()
    result = scan_stocks(db, {"n": 1, "chg_pct_min": -10})
    assert result["count"] <= 1


def test_scan_stocks_presets_all_have_required_keys():
    """PRESETS dict 무결성."""
    assert isinstance(PRESETS, dict)
    assert len(PRESETS) >= 5
    for name, conf in PRESETS.items():
        assert "description" in conf
        assert "sort" in conf


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. collect_daily — 주말 가드 (실제 수집 미실행)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_collect_daily_skips_saturday():
    """토요일 → skipped=True 반환."""
    # 2026-05-23 SAT
    result = asyncio.run(collect_daily("20260523"))
    assert result.get("skipped") is True
    assert result.get("reason") == "weekend"


def test_collect_daily_skips_sunday():
    """일요일 → skipped=True."""
    # 2026-05-24 SUN
    result = asyncio.run(collect_daily("20260524"))
    assert result.get("skipped") is True
