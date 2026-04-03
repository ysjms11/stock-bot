"""
Tests for KRX OTP-based crawling in scripts/krx_update.py.
Mocks all HTTP calls — no real KRX API access needed.
"""

import io
import sys
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, PropertyMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

# Import target module from scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
import krx_update  # noqa: E402
from krx_update import _pi, _pf, _otp_download_csv, fetch_market_data, \
    fetch_fundamental, fetch_investor_data, build_db, _last_trading_date

KST = ZoneInfo("Asia/Seoul")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 1: _pi and _pf parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestParseHelpers:
    def test_pi_comma_formatted(self):
        assert _pi("1,234,567") == 1234567

    def test_pi_dash_returns_zero(self):
        assert _pi("-") == 0

    def test_pi_empty_returns_zero(self):
        assert _pi("") == 0

    def test_pi_none_returns_zero(self):
        assert _pi(None) == 0

    def test_pi_positive_sign(self):
        assert _pi("+100") == 100

    def test_pf_comma_formatted(self):
        assert _pf("1,234.56") == 1234.56

    def test_pf_positive_sign(self):
        assert _pf("+3.14") == 3.14

    def test_pf_dash_returns_zero(self):
        assert _pf("-") == 0.0

    def test_pf_empty_returns_zero(self):
        assert _pf("") == 0.0

    def test_pf_none_returns_zero(self):
        assert _pf(None) == 0.0

    def test_pf_negative(self):
        assert _pf("-2.5") == -2.5


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 2: OTP download CSV success
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestOtpDownloadCsvSuccess:
    def test_two_step_otp_returns_dataframe(self):
        csv_text = "종목코드,종목명,종가\n005930,삼성전자,70000\n000660,SK하이닉스,150000"
        csv_bytes = csv_text.encode("cp949")

        # Mock session with two sequential post calls
        sess = MagicMock()
        otp_resp = MagicMock()
        otp_resp.status_code = 200
        otp_resp.text = "FAKE_OTP_TOKEN_1234567890"  # len > 10

        csv_resp = MagicMock()
        csv_resp.status_code = 200
        csv_resp.content = csv_bytes

        sess.post = MagicMock(side_effect=[otp_resp, csv_resp])

        df = _otp_download_csv(sess, {"url": "test", "name": "fileDown"})

        assert len(df) == 2
        assert "종목코드" in df.columns
        assert "종목명" in df.columns
        assert "종가" in df.columns
        assert str(df.iloc[0]["종목코드"]).zfill(6) == "005930"
        assert df.iloc[1]["종목명"] == "SK하이닉스"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 3: OTP download CSV failure -> RuntimeError
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestOtpDownloadCsvFailure:
    def test_otp_403_raises_runtime_error(self):
        sess = MagicMock()
        otp_resp = MagicMock()
        otp_resp.status_code = 403
        otp_resp.text = "Forbidden"
        sess.post = MagicMock(return_value=otp_resp)

        with pytest.raises(RuntimeError, match="OTP 생성 실패"):
            _otp_download_csv(sess, {"url": "test"})

    def test_otp_short_body_raises_runtime_error(self):
        """OTP body shorter than 10 chars should also fail."""
        sess = MagicMock()
        otp_resp = MagicMock()
        otp_resp.status_code = 200
        otp_resp.text = "short"  # len < 10
        sess.post = MagicMock(return_value=otp_resp)

        with pytest.raises(RuntimeError, match="OTP 생성 실패"):
            _otp_download_csv(sess, {"url": "test"})

    def test_csv_download_failure_raises_runtime_error(self):
        """OTP succeeds but CSV download returns 500."""
        sess = MagicMock()
        otp_resp = MagicMock()
        otp_resp.status_code = 200
        otp_resp.text = "VALID_OTP_TOKEN_12345"

        csv_resp = MagicMock()
        csv_resp.status_code = 500

        sess.post = MagicMock(side_effect=[otp_resp, csv_resp])

        with pytest.raises(RuntimeError, match="CSV 다운로드 실패"):
            _otp_download_csv(sess, {"url": "test"})


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 4: fetch_market_data fallback chain
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchMarketDataFallback:
    @patch("krx_update._market_data_pykrx")
    @patch("krx_update._krx_json_post")
    @patch("krx_update._otp_download_csv", side_effect=RuntimeError("OTP fail"))
    @patch("krx_update._get_krx_session")
    def test_json_fallback(self, mock_sess, mock_otp, mock_json, mock_pykrx):
        """OTP fails -> JSON fallback returns data."""
        mock_json.return_value = {
            "OutBlock_1": [
                {
                    "ISU_SRT_CD": "005930",
                    "ISU_ABBRV": "삼성전자",
                    "TDD_CLSPRC": "70,000",
                    "FLUC_RT": "1.50",
                    "ACC_TRDVOL": "10,000,000",
                    "ACC_TRDVAL": "700,000,000,000",
                    "MKTCAP": "418,000,000,000,000",
                },
            ]
        }

        result = fetch_market_data("20260403", "STK")

        assert len(result) == 1
        assert result[0]["ticker"] == "005930"
        assert result[0]["name"] == "삼성전자"
        assert result[0]["close"] == 70000
        assert result[0]["market"] == "kospi"
        mock_pykrx.assert_not_called()

    @patch("krx_update._market_data_pykrx")
    @patch("krx_update._krx_json_post", side_effect=RuntimeError("JSON fail"))
    @patch("krx_update._otp_download_csv", side_effect=RuntimeError("OTP fail"))
    @patch("krx_update._get_krx_session")
    def test_pykrx_fallback(self, mock_sess, mock_otp, mock_json, mock_pykrx):
        """OTP + JSON both fail -> pykrx fallback is called."""
        mock_pykrx.return_value = [
            {"ticker": "005930", "name": "삼성전자", "market": "kospi",
             "close": 70000, "chg_pct": 1.5, "volume": 10000000,
             "trade_value": 700000000000, "market_cap": 418000000000000}
        ]

        result = fetch_market_data("20260403", "STK")

        assert len(result) == 1
        assert result[0]["ticker"] == "005930"
        mock_pykrx.assert_called_once_with("20260403", "STK")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 5: build_db ratio calculations
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestBuildDbRatios:
    @patch("krx_update.fetch_investor_data")
    @patch("krx_update.fetch_fundamental")
    @patch("krx_update.fetch_market_data")
    @patch("krx_update.time.sleep")
    def test_ratio_calculations(self, mock_sleep, mock_market, mock_fund, mock_inv):
        # Market data: 2 stocks
        stock_a = {
            "ticker": "005930", "name": "삼성전자", "market": "kospi",
            "close": 70000, "chg_pct": 1.5, "volume": 10000000,
            "trade_value": 500_000_000_000, "market_cap": 10_000_000_000_000,
        }
        stock_b = {
            "ticker": "000660", "name": "SK하이닉스", "market": "kospi",
            "close": 150000, "chg_pct": -0.5, "volume": 5000000,
            "trade_value": 200_000_000_000, "market_cap": 5_000_000_000_000,
        }
        # STK returns both, KSQ returns empty
        mock_market.side_effect = [[stock_a, stock_b], []]

        # Fundamentals
        mock_fund.side_effect = [
            {"005930": {"per": 12.5, "pbr": 1.2}, "000660": {"per": 8.0, "pbr": 1.5}},
            {},  # KSQ empty
        ]

        # Investor data
        mock_inv.side_effect = [
            {
                "005930": {"foreign_net_qty": 100000, "foreign_net_amt": 50_000_000_000,
                           "inst_net_qty": 50000, "inst_net_amt": 30_000_000_000,
                           "indiv_net_qty": -150000, "indiv_net_amt": -80_000_000_000},
                "000660": {"foreign_net_qty": -20000, "foreign_net_amt": -10_000_000_000,
                           "inst_net_qty": 10000, "inst_net_amt": 5_000_000_000,
                           "indiv_net_qty": 10000, "indiv_net_amt": 5_000_000_000},
            },
            {},  # KSQ empty
        ]

        db = build_db("20260403")

        assert db["count"] == 2
        assert db["date"] == "20260403"

        sa = db["stocks"]["005930"]
        sb = db["stocks"]["000660"]

        # foreign_ratio = foreign_net_amt / market_cap * 100
        expected_fr_a = round(50_000_000_000 / 10_000_000_000_000 * 100, 4)
        assert sa["foreign_ratio"] == expected_fr_a  # 0.5

        # fi_ratio = (foreign + inst) / market_cap * 100
        expected_fi_a = round((50_000_000_000 + 30_000_000_000) / 10_000_000_000_000 * 100, 4)
        assert sa["fi_ratio"] == expected_fi_a  # 0.8

        # turnover = trade_value / market_cap * 100
        expected_to_a = round(500_000_000_000 / 10_000_000_000_000 * 100, 4)
        assert sa["turnover"] == expected_to_a  # 5.0

        # Stock B
        expected_fr_b = round(-10_000_000_000 / 5_000_000_000_000 * 100, 4)
        assert sb["foreign_ratio"] == expected_fr_b  # -0.2

        expected_fi_b = round((-10_000_000_000 + 5_000_000_000) / 5_000_000_000_000 * 100, 4)
        assert sb["fi_ratio"] == expected_fi_b  # -0.1

        expected_to_b = round(200_000_000_000 / 5_000_000_000_000 * 100, 4)
        assert sb["turnover"] == expected_to_b  # 4.0

        # PER/PBR merged
        assert sa["per"] == 12.5
        assert sa["pbr"] == 1.2
        assert sb["per"] == 8.0

        # Market summary
        summary = db["market_summary"]
        assert summary["kospi_count"] == 2
        assert summary["kospi_up"] == 1
        assert summary["kospi_down"] == 1

    @patch("krx_update.fetch_investor_data")
    @patch("krx_update.fetch_fundamental")
    @patch("krx_update.fetch_market_data")
    @patch("krx_update.time.sleep")
    def test_zero_market_cap_ratios(self, mock_sleep, mock_market, mock_fund, mock_inv):
        """When market_cap is 0, all ratios should be 0."""
        stock = {
            "ticker": "999990", "name": "TestCo", "market": "kosdaq",
            "close": 100, "chg_pct": 0, "volume": 0,
            "trade_value": 0, "market_cap": 0,
        }
        mock_market.side_effect = [[], [stock]]
        mock_fund.side_effect = [{}, {}]
        mock_inv.side_effect = [{}, {}]

        db = build_db("20260403")
        s = db["stocks"]["999990"]
        assert s["foreign_ratio"] == 0.0
        assert s["fi_ratio"] == 0.0
        assert s["turnover"] == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 6: _last_trading_date
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestLastTradingDate:
    def _mock_now(self, year, month, day, hour, minute):
        return datetime(year, month, day, hour, minute, tzinfo=KST)

    @patch("krx_update.datetime")
    def test_friday_after_close(self, mock_dt):
        """Friday 16:00 -> returns today (Friday)."""
        mock_dt.now.return_value = self._mock_now(2026, 4, 3, 16, 0)  # Friday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = _last_trading_date()
        assert result == "20260403"

    @patch("krx_update.datetime")
    def test_monday_before_close(self, mock_dt):
        """Monday 10:00 -> returns previous Friday."""
        mock_dt.now.return_value = self._mock_now(2026, 4, 6, 10, 0)  # Monday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = _last_trading_date()
        assert result == "20260403"  # Previous Friday

    @patch("krx_update.datetime")
    def test_saturday(self, mock_dt):
        """Saturday -> returns previous Friday."""
        mock_dt.now.return_value = self._mock_now(2026, 4, 4, 12, 0)  # Saturday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = _last_trading_date()
        assert result == "20260403"  # Previous Friday

    @patch("krx_update.datetime")
    def test_sunday(self, mock_dt):
        """Sunday -> returns previous Friday."""
        mock_dt.now.return_value = self._mock_now(2026, 4, 5, 12, 0)  # Sunday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = _last_trading_date()
        assert result == "20260403"  # Previous Friday

    @patch("krx_update.datetime")
    def test_friday_before_close(self, mock_dt):
        """Friday 10:00 -> returns previous Thursday."""
        mock_dt.now.return_value = self._mock_now(2026, 4, 3, 10, 0)  # Friday 10:00
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = _last_trading_date()
        assert result == "20260402"  # Thursday


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 7: CSV encoding fallback
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestCsvEncodingFallback:
    def _make_session(self, csv_bytes):
        sess = MagicMock()
        otp_resp = MagicMock()
        otp_resp.status_code = 200
        otp_resp.text = "VALID_OTP_TOKEN_12345"

        csv_resp = MagicMock()
        csv_resp.status_code = 200
        csv_resp.content = csv_bytes

        sess.post = MagicMock(side_effect=[otp_resp, csv_resp])
        return sess

    def test_euc_kr_encoding(self):
        """CSV encoded in euc-kr should parse successfully."""
        csv_text = "종목코드,종목명,종가\n005930,삼성전자,70000"
        csv_bytes = csv_text.encode("euc-kr")
        sess = self._make_session(csv_bytes)

        df = _otp_download_csv(sess, {"url": "test"})
        assert len(df) == 1
        assert str(df.iloc[0]["종목코드"]).zfill(6) == "005930"

    def test_utf8_encoding(self):
        """CSV encoded in utf-8 should parse successfully."""
        csv_text = "종목코드,종목명,종가\n005930,삼성전자,70000"
        csv_bytes = csv_text.encode("utf-8")
        sess = self._make_session(csv_bytes)

        df = _otp_download_csv(sess, {"url": "test"})
        assert len(df) == 1
        assert str(df.iloc[0]["종목코드"]).zfill(6) == "005930"

    def test_cp949_encoding(self):
        """CSV encoded in cp949 should parse successfully (primary)."""
        csv_text = "종목코드,종목명,종가\n005930,삼성전자,70000"
        csv_bytes = csv_text.encode("cp949")
        sess = self._make_session(csv_bytes)

        df = _otp_download_csv(sess, {"url": "test"})
        assert len(df) == 1
        assert str(df.iloc[0]["종목코드"]).zfill(6) == "005930"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 8: LOGOUT response handling
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestLogoutResponse:
    @patch("krx_update.requests.post")
    def test_logout_response_raises_runtime_error(self, mock_post):
        """JSON POST returning LOGOUT should raise RuntimeError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"RESULT": "LOGOUT"}
        mock_post.return_value = mock_resp

        from krx_update import _krx_json_post
        with pytest.raises(RuntimeError, match="LOGOUT"):
            _krx_json_post({"bld": "test"})

    @patch("krx_update.requests.post")
    def test_json_http_error_raises_runtime_error(self, mock_post):
        """JSON POST returning non-200 should raise RuntimeError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp

        from krx_update import _krx_json_post
        with pytest.raises(RuntimeError, match="KRX HTTP 500"):
            _krx_json_post({"bld": "test"})
