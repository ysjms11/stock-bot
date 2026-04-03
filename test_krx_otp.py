"""
Tests for KRX session-based crawling in scripts/krx_update.py.
Mocks all HTTP calls — no real KRX API access needed.
"""

import io
import sys
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

# Import target module from scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
import krx_update  # noqa: E402
from krx_update import (
    _pi, _pf, _otp_download_csv, _krx_json_post,
    fetch_market_data, fetch_fundamental, fetch_investor_data,
    build_db, _last_trading_date, _get_krx_session,
)

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

    def test_pi_nan_returns_zero(self):
        assert _pi(float("nan")) == 0

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

    def test_pf_nan_returns_zero(self):
        assert _pf(float("nan")) == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 2: 세션 생성 — JSESSIONID 쿠키 로깅
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestSessionCreation:
    @patch("krx_update.requests.Session")
    def test_session_visits_page_and_stores_cookies(self, mock_session_cls):
        mock_sess = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_sess.get.return_value = mock_resp
        mock_sess.cookies = {"JSESSIONID": "abc123", "WMONID": "xyz"}
        mock_session_cls.return_value = mock_sess

        sess = _get_krx_session()

        # 페이지 방문 확인
        mock_sess.get.assert_called_once()
        call_url = mock_sess.get.call_args[0][0]
        assert "menuId=MDC0201020101" in call_url
        assert sess is mock_sess

    @patch("krx_update.requests.Session")
    def test_session_survives_page_visit_failure(self, mock_session_cls):
        mock_sess = MagicMock()
        mock_sess.get.side_effect = Exception("Connection error")
        mock_sess.cookies = {}
        mock_session_cls.return_value = mock_sess

        # 실패해도 세션 반환
        sess = _get_krx_session()
        assert sess is mock_sess


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 3: 세션 기반 JSON — LOGOUT 감지
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestSessionJsonPost:
    def test_successful_json_post(self):
        sess = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"OutBlock_1": [{"ISU_SRT_CD": "005930"}]}'
        resp.json.return_value = {"OutBlock_1": [{"ISU_SRT_CD": "005930"}]}
        sess.post.return_value = resp

        body = _krx_json_post(sess, {"bld": "test"})
        assert "OutBlock_1" in body

    def test_logout_response_raises(self):
        sess = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"RESULT": "LOGOUT"}'
        resp.json.return_value = {"RESULT": "LOGOUT"}
        sess.post.return_value = resp

        with pytest.raises(RuntimeError, match="LOGOUT"):
            _krx_json_post(sess, {"bld": "test"})

    def test_http_error_raises(self):
        sess = MagicMock()
        resp = MagicMock()
        resp.status_code = 403
        resp.text = "Forbidden"
        sess.post.return_value = resp

        with pytest.raises(RuntimeError, match="KRX HTTP 403"):
            _krx_json_post(sess, {"bld": "test"})


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 4: OTP download CSV
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestOtpDownloadCsv:
    def test_two_step_otp_returns_dataframe(self):
        csv_text = "종목코드,종목명,종가\n005930,삼성전자,70000\n000660,SK하이닉스,150000"
        csv_bytes = csv_text.encode("cp949")

        sess = MagicMock()
        otp_resp = MagicMock()
        otp_resp.status_code = 200
        otp_resp.text = "FAKE_OTP_TOKEN_1234567890"

        csv_resp = MagicMock()
        csv_resp.status_code = 200
        csv_resp.content = csv_bytes

        sess.post = MagicMock(side_effect=[otp_resp, csv_resp])

        df = _otp_download_csv(sess, {"url": "test", "name": "fileDown"})

        assert len(df) == 2
        assert "종목코드" in df.columns

    def test_otp_403_raises(self):
        sess = MagicMock()
        otp_resp = MagicMock()
        otp_resp.status_code = 403
        otp_resp.text = "Forbidden"
        sess.post = MagicMock(return_value=otp_resp)

        with pytest.raises(RuntimeError, match="OTP 생성 실패"):
            _otp_download_csv(sess, {"url": "test"})

    def test_csv_download_500_raises(self):
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
# Test 5: fetch_market_data fallback 체인 (JSON → OTP → pykrx)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchMarketDataFallback:
    def test_json_primary_success(self):
        """세션 기반 JSON이 성공하면 바로 반환."""
        sess = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"OutBlock_1": [...]}'
        resp.json.return_value = {
            "OutBlock_1": [{
                "ISU_SRT_CD": "005930",
                "ISU_ABBRV": "삼성전자",
                "TDD_CLSPRC": "70,000",
                "FLUC_RT": "1.50",
                "ACC_TRDVOL": "10,000,000",
                "ACC_TRDVAL": "700,000,000,000",
                "MKTCAP": "418,000,000,000,000",
            }]
        }
        sess.post.return_value = resp

        result = fetch_market_data("20260403", "STK", sess=sess)
        assert len(result) == 1
        assert result[0]["ticker"] == "005930"
        assert result[0]["close"] == 70000
        assert result[0]["market"] == "kospi"

    @patch("krx_update._market_data_pykrx")
    @patch("krx_update._otp_download_csv", side_effect=RuntimeError("OTP fail"))
    @patch("krx_update._krx_json_post", side_effect=RuntimeError("LOGOUT"))
    def test_all_fail_to_pykrx(self, mock_json, mock_otp, mock_pykrx):
        """JSON + OTP 모두 실패 → pykrx fallback."""
        mock_pykrx.return_value = [
            {"ticker": "005930", "name": "삼성전자", "market": "kospi",
             "close": 70000, "chg_pct": 1.5, "volume": 10000000,
             "trade_value": 700000000000, "market_cap": 418000000000000}
        ]
        sess = MagicMock()
        result = fetch_market_data("20260403", "STK", sess=sess)
        assert len(result) == 1
        mock_pykrx.assert_called_once_with("20260403", "STK")

    @patch("krx_update._market_data_pykrx")
    @patch("krx_update._otp_download_csv")
    @patch("krx_update._krx_json_post", side_effect=RuntimeError("LOGOUT"))
    def test_json_fail_otp_success(self, mock_json, mock_otp, mock_pykrx):
        """JSON 실패 → OTP CSV 성공."""
        csv_text = "종목코드,종목명,종가,등락률,거래량,거래대금,시가총액\n005930,삼성전자,70000,1.5,10000000,700000000000,418000000000000"
        df = pd.read_csv(io.StringIO(csv_text), dtype={"종목코드": str})
        mock_otp.return_value = df

        sess = MagicMock()
        result = fetch_market_data("20260403", "STK", sess=sess)
        assert len(result) == 1
        assert result[0]["ticker"] == "005930"
        mock_pykrx.assert_not_called()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 6: build_db 비율 계산
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestBuildDbRatios:
    @patch("krx_update._get_krx_session")
    @patch("krx_update.fetch_investor_data")
    @patch("krx_update.fetch_fundamental")
    @patch("krx_update.fetch_market_data")
    @patch("krx_update.time.sleep")
    def test_ratio_calculations(self, mock_sleep, mock_market, mock_fund, mock_inv, mock_sess):
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
        mock_market.side_effect = [[stock_a, stock_b], []]
        mock_fund.side_effect = [
            {"005930": {"per": 12.5, "pbr": 1.2}, "000660": {"per": 8.0, "pbr": 1.5}},
            {},
        ]
        mock_inv.side_effect = [
            {
                "005930": {"foreign_net_qty": 100000, "foreign_net_amt": 50_000_000_000,
                           "inst_net_qty": 50000, "inst_net_amt": 30_000_000_000,
                           "indiv_net_qty": -150000, "indiv_net_amt": -80_000_000_000},
                "000660": {"foreign_net_qty": -20000, "foreign_net_amt": -10_000_000_000,
                           "inst_net_qty": 10000, "inst_net_amt": 5_000_000_000,
                           "indiv_net_qty": 10000, "indiv_net_amt": 5_000_000_000},
            },
            {},
        ]

        db = build_db("20260403")

        assert db["count"] == 2
        sa = db["stocks"]["005930"]
        assert sa["foreign_ratio"] == round(50_000_000_000 / 10_000_000_000_000 * 100, 4)
        assert sa["fi_ratio"] == round(80_000_000_000 / 10_000_000_000_000 * 100, 4)
        assert sa["turnover"] == round(500_000_000_000 / 10_000_000_000_000 * 100, 4)
        assert sa["per"] == 12.5

    @patch("krx_update._get_krx_session")
    @patch("krx_update.fetch_investor_data")
    @patch("krx_update.fetch_fundamental")
    @patch("krx_update.fetch_market_data")
    @patch("krx_update.time.sleep")
    def test_zero_market_cap(self, mock_sleep, mock_market, mock_fund, mock_inv, mock_sess):
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
# Test 7: _last_trading_date
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestLastTradingDate:
    def _mock_now(self, year, month, day, hour, minute):
        return datetime(year, month, day, hour, minute, tzinfo=KST)

    @patch("krx_update.datetime")
    def test_friday_after_close(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(2026, 4, 3, 16, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        assert _last_trading_date() == "20260403"

    @patch("krx_update.datetime")
    def test_monday_before_close(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(2026, 4, 6, 10, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        assert _last_trading_date() == "20260403"

    @patch("krx_update.datetime")
    def test_saturday(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(2026, 4, 4, 12, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        assert _last_trading_date() == "20260403"

    @patch("krx_update.datetime")
    def test_sunday(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(2026, 4, 5, 12, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        assert _last_trading_date() == "20260403"

    @patch("krx_update.datetime")
    def test_friday_before_close(self, mock_dt):
        mock_dt.now.return_value = self._mock_now(2026, 4, 3, 10, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        assert _last_trading_date() == "20260402"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 8: CSV 인코딩 fallback
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

    @pytest.mark.parametrize("encoding", ["cp949", "euc-kr", "utf-8"])
    def test_encoding(self, encoding):
        csv_text = "종목코드,종목명,종가\n005930,삼성전자,70000"
        csv_bytes = csv_text.encode(encoding)
        sess = self._make_session(csv_bytes)

        df = _otp_download_csv(sess, {"url": "test"})
        assert len(df) == 1
        assert str(df.iloc[0]["종목코드"]).zfill(6) == "005930"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 9: 헤더 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestHeaders:
    def test_required_headers_present(self):
        """브라우저 흉내에 필수적인 헤더가 설정되어 있는지 확인."""
        from krx_update import KRX_HEADERS
        assert "X-Requested-With" in KRX_HEADERS
        assert KRX_HEADERS["X-Requested-With"] == "XMLHttpRequest"
        assert "application/json" in KRX_HEADERS["Accept"]
        assert "application/x-www-form-urlencoded" in KRX_HEADERS["Content-Type"]
        assert "https://data.krx.co.kr" in KRX_HEADERS["Referer"]
        assert "https://data.krx.co.kr" in KRX_HEADERS["Origin"]

    def test_page_url_has_menu_id(self):
        from krx_update import KRX_PAGE_URL
        assert "menuId=MDC0201020101" in KRX_PAGE_URL


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Test 10: 투자자 데이터 fallback (JSON → OTP)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchInvestorFallback:
    @patch("krx_update._otp_download_csv")
    @patch("krx_update._krx_json_post", side_effect=RuntimeError("LOGOUT"))
    def test_json_fail_otp_success(self, mock_json, mock_otp):
        """JSON 실패 → OTP CSV로 투자자 데이터 획득."""
        csv_text = "종목코드,종목명,순매수량,순매수금액\n005930,삼성전자,100000,5000000000"
        df = pd.read_csv(io.StringIO(csv_text), dtype={"종목코드": str})
        mock_otp.return_value = df

        sess = MagicMock()
        result = fetch_investor_data("20260403", "STK", sess=sess)

        assert "005930" in result
        assert result["005930"]["foreign_net_qty"] == 100000
