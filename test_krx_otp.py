"""
Tests for KRX OPEN API + scraping fallback in scripts/krx_update.py.
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
import krx_update  # noqa: E402
from krx_update import (
    _pi, _pf, _open_api_get, _extract_short_ticker,
    fetch_market_data_openapi, fetch_market_data,
    fetch_fundamental, fetch_investor_data,
    build_db, _last_trading_date, _get_krx_session,
    _krx_json_post, _otp_download_csv,
)

KST = ZoneInfo("Asia/Seoul")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 파싱 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestParseHelpers:
    def test_pi_comma(self):
        assert _pi("1,234,567") == 1234567

    def test_pi_edge(self):
        assert _pi("-") == 0
        assert _pi("") == 0
        assert _pi(None) == 0
        assert _pi(float("nan")) == 0

    def test_pi_sign(self):
        assert _pi("+100") == 100

    def test_pf_comma(self):
        assert _pf("1,234.56") == 1234.56

    def test_pf_edge(self):
        assert _pf("-") == 0.0
        assert _pf("") == 0.0
        assert _pf(None) == 0.0
        assert _pf(float("nan")) == 0.0

    def test_pf_negative(self):
        assert _pf("-2.5") == -2.5


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# ISIN → 6자리 종목코드 변환
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestExtractShortTicker:
    def test_isin_to_ticker(self):
        assert _extract_short_ticker("KR7005930003") == "005930"
        assert _extract_short_ticker("KR7000660001") == "000660"

    def test_already_6digit(self):
        assert _extract_short_ticker("005930") == "005930"

    def test_empty(self):
        assert _extract_short_ticker("") == ""
        assert _extract_short_ticker(None) == ""

    def test_invalid(self):
        assert _extract_short_ticker("INVALID") == ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# OPEN API 호출
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestOpenApiGet:
    @patch("krx_update.KRX_API_KEY", "test_key_123")
    @patch("krx_update.requests.get")
    def test_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"OutBlock_1": [{"ISU_CD": "KR7005930003"}]}'
        mock_resp.json.return_value = {
            "OutBlock_1": [{"ISU_CD": "KR7005930003", "ISU_NM": "삼성전자"}]
        }
        mock_get.return_value = mock_resp

        records = _open_api_get("https://example.com/api", "20260403")
        assert len(records) == 1
        assert records[0]["ISU_CD"] == "KR7005930003"

        # AUTH_KEY가 쿼리 파라미터로 전달되는지 확인
        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["params"]["AUTH_KEY"] == "test_key_123"
        assert call_kwargs[1]["params"]["basDd"] == "20260403"

    @patch("krx_update.KRX_API_KEY", "")
    def test_no_api_key(self):
        with pytest.raises(RuntimeError, match="KRX_API_KEY 환경변수 미설정"):
            _open_api_get("https://example.com/api", "20260403")

    @patch("krx_update.KRX_API_KEY", "test_key")
    @patch("krx_update.requests.get")
    def test_auth_failure(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_get.return_value = mock_resp

        with pytest.raises(RuntimeError, match="인증 실패"):
            _open_api_get("https://example.com/api", "20260403")

    @patch("krx_update.KRX_API_KEY", "test_key")
    @patch("krx_update.requests.get")
    def test_rate_limit(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Too Many Requests"
        mock_get.return_value = mock_resp

        with pytest.raises(RuntimeError, match="요청 한도"):
            _open_api_get("https://example.com/api", "20260403")

    @patch("krx_update.KRX_API_KEY", "test_key")
    @patch("krx_update.requests.get")
    def test_error_response(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"respCode":"401","respMsg":"Unauthorized API Call"}'
        mock_resp.json.return_value = {"respCode": "401", "respMsg": "Unauthorized API Call"}
        mock_get.return_value = mock_resp

        with pytest.raises(RuntimeError, match="OPEN API 에러"):
            _open_api_get("https://example.com/api", "20260403")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# OPEN API 전종목 시세
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchMarketDataOpenapi:
    @patch("krx_update._open_api_get")
    def test_kospi(self, mock_api):
        mock_api.return_value = [
            {
                "ISU_CD": "KR7005930003",
                "ISU_NM": "삼성전자",
                "TDD_CLSPRC": "70000",
                "FLUC_RT": "1.50",
                "ACC_TRDVOL": "10000000",
                "ACC_TRDVAL": "700000000000",
                "MKTCAP": "418000000000000",
            }
        ]

        result = fetch_market_data_openapi("20260403", "STK")
        assert len(result) == 1
        assert result[0]["ticker"] == "005930"
        assert result[0]["name"] == "삼성전자"
        assert result[0]["market"] == "kospi"
        assert result[0]["close"] == 70000
        assert result[0]["chg_pct"] == 1.5

    @patch("krx_update._open_api_get")
    def test_kosdaq(self, mock_api):
        mock_api.return_value = [
            {"ISU_CD": "KR7247540002", "ISU_NM": "에코프로비엠",
             "TDD_CLSPRC": "250000", "FLUC_RT": "-2.00",
             "ACC_TRDVOL": "500000", "ACC_TRDVAL": "125000000000",
             "MKTCAP": "10000000000000"}
        ]
        result = fetch_market_data_openapi("20260403", "KSQ")
        assert result[0]["market"] == "kosdaq"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 시세 fallback 체인: OPEN API → 스크래핑 → pykrx
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestFetchMarketDataFallback:
    @patch("krx_update.fetch_market_data_openapi")
    def test_openapi_success(self, mock_api):
        """OPEN API 성공 시 바로 반환."""
        mock_api.return_value = [
            {"ticker": "005930", "name": "삼성전자", "market": "kospi",
             "close": 70000, "chg_pct": 1.5, "volume": 10000000,
             "trade_value": 700000000000, "market_cap": 418000000000000}
        ]
        result = fetch_market_data("20260403", "STK")
        assert len(result) == 1
        assert result[0]["ticker"] == "005930"

    @patch("krx_update._market_data_pykrx")
    @patch("krx_update._krx_json_post")
    @patch("krx_update.fetch_market_data_openapi", side_effect=RuntimeError("no key"))
    def test_openapi_fail_scrape_success(self, mock_api, mock_json, mock_pykrx):
        """OPEN API 실패 → 스크래핑 성공."""
        mock_json.return_value = {
            "OutBlock_1": [{
                "ISU_SRT_CD": "005930", "ISU_ABBRV": "삼성전자",
                "TDD_CLSPRC": "70,000", "FLUC_RT": "1.50",
                "ACC_TRDVOL": "10,000,000", "ACC_TRDVAL": "700,000,000,000",
                "MKTCAP": "418,000,000,000,000",
            }]
        }
        sess = MagicMock()
        result = fetch_market_data("20260403", "STK", sess=sess)
        assert len(result) == 1
        mock_pykrx.assert_not_called()

    @patch("krx_update._market_data_pykrx")
    @patch("krx_update._krx_json_post", side_effect=RuntimeError("LOGOUT"))
    @patch("krx_update.fetch_market_data_openapi", side_effect=RuntimeError("no key"))
    def test_all_fail_to_pykrx(self, mock_api, mock_json, mock_pykrx):
        """OPEN API + 스크래핑 모두 실패 → pykrx."""
        mock_pykrx.return_value = [
            {"ticker": "005930", "name": "삼성전자", "market": "kospi",
             "close": 70000, "chg_pct": 1.5, "volume": 10000000,
             "trade_value": 700000000000, "market_cap": 418000000000000}
        ]
        sess = MagicMock()
        result = fetch_market_data("20260403", "STK", sess=sess)
        assert len(result) == 1
        mock_pykrx.assert_called_once()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 세션 생성
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestSessionCreation:
    @patch("krx_update.requests.Session")
    def test_session_visits_page(self, mock_cls):
        mock_sess = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_sess.get.return_value = mock_resp
        mock_sess.cookies = {"JSESSIONID": "abc123"}
        mock_cls.return_value = mock_sess

        sess = _get_krx_session()
        mock_sess.get.assert_called_once()
        assert "menuId=MDC0201020101" in mock_sess.get.call_args[0][0]


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 스크래핑 JSON — LOGOUT 감지
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestScrapeJsonPost:
    def test_logout_raises(self):
        sess = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"RESULT": "LOGOUT"}'
        resp.json.return_value = {"RESULT": "LOGOUT"}
        sess.post.return_value = resp

        with pytest.raises(RuntimeError, match="LOGOUT"):
            _krx_json_post(sess, {"bld": "test"})

    def test_http_error(self):
        sess = MagicMock()
        resp = MagicMock()
        resp.status_code = 403
        resp.text = "Forbidden"
        sess.post.return_value = resp

        with pytest.raises(RuntimeError, match="KRX HTTP 403"):
            _krx_json_post(sess, {"bld": "test"})


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# OTP CSV
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestOtpCsv:
    def test_success(self):
        csv_bytes = "종목코드,종목명,종가\n005930,삼성전자,70000".encode("cp949")
        sess = MagicMock()
        otp_resp = MagicMock(status_code=200, text="FAKE_OTP_TOKEN_1234567890")
        csv_resp = MagicMock(status_code=200, content=csv_bytes)
        sess.post = MagicMock(side_effect=[otp_resp, csv_resp])

        df = _otp_download_csv(sess, {"url": "test"})
        assert len(df) == 1

    def test_otp_fail(self):
        sess = MagicMock()
        resp = MagicMock(status_code=403, text="Forbidden")
        sess.post = MagicMock(return_value=resp)

        with pytest.raises(RuntimeError, match="OTP 생성 실패"):
            _otp_download_csv(sess, {"url": "test"})


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# build_db 비율 계산
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestBuildDb:
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
        mock_market.side_effect = [[stock_a], []]
        mock_fund.side_effect = [{"005930": {"per": 12.5, "pbr": 1.2}}, {}]
        mock_inv.side_effect = [
            {"005930": {"foreign_net_qty": 100000, "foreign_net_amt": 50_000_000_000,
                        "inst_net_qty": 50000, "inst_net_amt": 30_000_000_000,
                        "indiv_net_qty": -150000, "indiv_net_amt": -80_000_000_000}},
            {},
        ]

        db = build_db("20260403")
        sa = db["stocks"]["005930"]
        assert sa["foreign_ratio"] == round(50_000_000_000 / 10_000_000_000_000 * 100, 4)
        assert sa["fi_ratio"] == round(80_000_000_000 / 10_000_000_000_000 * 100, 4)
        assert sa["per"] == 12.5

    @patch("krx_update._get_krx_session")
    @patch("krx_update.fetch_investor_data")
    @patch("krx_update.fetch_fundamental")
    @patch("krx_update.fetch_market_data")
    @patch("krx_update.time.sleep")
    def test_zero_market_cap(self, mock_sleep, mock_market, mock_fund, mock_inv, mock_sess):
        stock = {
            "ticker": "999990", "name": "Test", "market": "kosdaq",
            "close": 100, "chg_pct": 0, "volume": 0,
            "trade_value": 0, "market_cap": 0,
        }
        mock_market.side_effect = [[], [stock]]
        mock_fund.side_effect = [{}, {}]
        mock_inv.side_effect = [{}, {}]

        db = build_db("20260403")
        s = db["stocks"]["999990"]
        assert s["foreign_ratio"] == 0.0
        assert s["turnover"] == 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# _last_trading_date
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# CSV 인코딩 fallback
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestCsvEncoding:
    @pytest.mark.parametrize("encoding", ["cp949", "euc-kr", "utf-8"])
    def test_encoding(self, encoding):
        csv_bytes = "종목코드,종목명,종가\n005930,삼성전자,70000".encode(encoding)
        sess = MagicMock()
        otp_resp = MagicMock(status_code=200, text="VALID_OTP_TOKEN_12345")
        csv_resp = MagicMock(status_code=200, content=csv_bytes)
        sess.post = MagicMock(side_effect=[otp_resp, csv_resp])

        df = _otp_download_csv(sess, {"url": "test"})
        assert len(df) == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 환경변수 & 설정 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━
class TestConfig:
    def test_open_api_endpoints(self):
        from krx_update import OPEN_API_ENDPOINTS
        assert "STK" in OPEN_API_ENDPOINTS
        assert "KSQ" in OPEN_API_ENDPOINTS
        assert "data-dbg.krx.co.kr" in OPEN_API_ENDPOINTS["STK"]
        assert "stk_bydd_trd" in OPEN_API_ENDPOINTS["STK"]
        assert "ksq_bydd_trd" in OPEN_API_ENDPOINTS["KSQ"]

    def test_headers(self):
        from krx_update import KRX_HEADERS
        assert "X-Requested-With" in KRX_HEADERS
        assert "XMLHttpRequest" in KRX_HEADERS["X-Requested-With"]
