"""
엣지 케이스 테스트 — 돈/알림에 관여하는 핵심 경로 보강

1. load_json / save_json: 빈 파일, 깨진 JSON, 디렉터리 미존재
2. KIS 토큰 만료 시 재발급
3. 빈 포트폴리오에서 get_portfolio 조회
4. 없는 종목코드로 get_stock_detail 호출
5. check_stoploss: 손절선 로직 (일일 발송 제한, 빈 stops)
"""
import sys
import types
import json
import os
import asyncio
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# ── telegram stub ──
telegram_stub = types.ModuleType("telegram")
telegram_stub.Update = object
telegram_stub.ReplyKeyboardMarkup = type("ReplyKeyboardMarkup", (), {"__init__": lambda self, *a, **kw: None})
ext_stub = types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.MessageHandler = object
ext_stub.filters = type("filters", (), {"TEXT": None, "Regex": staticmethod(lambda x: x)})()
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)

import kis_api
from kis_api import load_json, save_json, _token_cache
from mcp_tools import _execute_tool


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. load_json / save_json 엣지 케이스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestLoadSaveJson(unittest.TestCase):

    def test_load_nonexistent_file_returns_empty_dict(self):
        """존재하지 않는 파일 → 기본값 {} 반환"""
        result = load_json("/tmp/_test_nonexistent_abc123.json")
        self.assertEqual(result, {})

    def test_load_nonexistent_with_default(self):
        """존재하지 않는 파일 + default 지정 → default 반환 + 파일 생성"""
        path = "/tmp/_test_load_default.json"
        if os.path.exists(path):
            os.remove(path)
        default_val = {"key": "value"}
        result = load_json(path, default=default_val)
        self.assertEqual(result, default_val)
        # 파일이 생성되었는지 확인
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            self.assertEqual(json.load(f), default_val)
        os.remove(path)

    def test_load_empty_file_returns_empty_dict(self):
        """빈 파일 → JSONDecodeError → {} 반환"""
        path = "/tmp/_test_empty.json"
        with open(path, "w") as f:
            f.write("")
        result = load_json(path)
        self.assertEqual(result, {})
        os.remove(path)

    def test_load_corrupted_json_returns_empty_dict(self):
        """깨진 JSON → JSONDecodeError → {} 반환"""
        path = "/tmp/_test_corrupted.json"
        with open(path, "w") as f:
            f.write("{invalid json content;;;")
        result = load_json(path)
        self.assertEqual(result, {})
        os.remove(path)

    def test_load_corrupted_with_default_creates_file(self):
        """깨진 JSON + default → default로 파일 재생성"""
        path = "/tmp/_test_corrupted_default.json"
        with open(path, "w") as f:
            f.write("not json")
        default_val = {"restored": True}
        result = load_json(path, default=default_val)
        self.assertEqual(result, default_val)
        # 파일이 default로 복구되었는지 확인
        with open(path) as f:
            self.assertEqual(json.load(f), default_val)
        os.remove(path)

    def test_save_json_creates_file(self):
        """save_json이 파일을 정상적으로 생성"""
        path = "/tmp/_test_save.json"
        if os.path.exists(path):
            os.remove(path)
        save_json(path, {"a": 1, "b": [2, 3]})
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data, {"a": 1, "b": [2, 3]})
        os.remove(path)

    def test_save_json_overwrites(self):
        """save_json이 기존 내용을 덮어쓰기"""
        path = "/tmp/_test_overwrite.json"
        save_json(path, {"old": True})
        save_json(path, {"new": True})
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data, {"new": True})
        os.remove(path)

    def test_save_json_korean_text(self):
        """한글 텍스트가 ensure_ascii=False로 올바르게 저장"""
        path = "/tmp/_test_korean.json"
        save_json(path, {"name": "삼성전자"})
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        self.assertIn("삼성전자", raw)  # 이스케이프 없이 한글 직접 저장
        os.remove(path)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. KIS 토큰 캐시 및 만료 재발급
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestKisTokenCache(unittest.TestCase):

    def setUp(self):
        # 매 테스트 전에 토큰 캐시 초기화
        _token_cache["token"] = None
        _token_cache["expires"] = None

    def tearDown(self):
        _token_cache["token"] = None
        _token_cache["expires"] = None

    @patch("kis_api.aiohttp.ClientSession")
    def test_token_fresh_issue(self, mock_session_cls):
        """토큰 캐시 비었을 때 → 새 토큰 발급"""
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={"access_token": "new_token_abc"})
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session

        token = _run(kis_api.get_kis_token())
        self.assertEqual(token, "new_token_abc")
        self.assertEqual(_token_cache["token"], "new_token_abc")
        self.assertIsNotNone(_token_cache["expires"])

    def test_token_cache_hit(self):
        """유효한 캐시 토큰 → API 호출 없이 반환"""
        _token_cache["token"] = "cached_token"
        _token_cache["expires"] = datetime.now() + timedelta(hours=10)
        token = _run(kis_api.get_kis_token())
        self.assertEqual(token, "cached_token")

    @patch("kis_api.aiohttp.ClientSession")
    def test_token_expired_reissue(self, mock_session_cls):
        """만료된 캐시 → 재발급"""
        _token_cache["token"] = "expired_token"
        _token_cache["expires"] = datetime.now() - timedelta(hours=1)  # 이미 만료

        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={"access_token": "refreshed_token"})
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session

        token = _run(kis_api.get_kis_token())
        self.assertEqual(token, "refreshed_token")

    @patch("kis_api.aiohttp.ClientSession")
    def test_token_api_failure_returns_none(self, mock_session_cls):
        """토큰 API가 access_token 없이 응답 → None 반환"""
        _token_cache["token"] = None
        _token_cache["expires"] = None

        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={"error": "invalid credentials"})
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session

        token = _run(kis_api.get_kis_token())
        self.assertIsNone(token)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. 빈 포트폴리오에서 get_portfolio 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestEmptyPortfolio(unittest.TestCase):

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    @patch("mcp_tools.load_json", return_value={})
    def test_empty_portfolio_returns_message(self, mock_load, mock_token):
        """포트폴리오가 비어있을 때 안내 메시지 반환"""
        result = _run(_execute_tool("get_portfolio", {}))
        self.assertIn("message", result)
        self.assertIn("비어있습니다", result["message"])

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    @patch("mcp_tools.load_json", return_value={"us_stocks": {}, "cash_krw": 0, "cash_usd": 0})
    def test_only_meta_keys_portfolio_returns_message(self, mock_load, mock_token):
        """메타키만 있고 종목이 없는 포트폴리오 → 비어있음"""
        result = _run(_execute_tool("get_portfolio", {}))
        self.assertIn("message", result)
        self.assertIn("비어있습니다", result["message"])

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    @patch("mcp_tools.load_json", return_value={})
    @patch("mcp_tools.save_json")
    @patch("mcp_tools.ws_manager", MagicMock())
    def test_set_portfolio_without_holdings_returns_error(self, mock_save, mock_load, mock_token):
        """holdings 없이 set 모드 → 에러"""
        result = _run(_execute_tool("get_portfolio", {"mode": "set"}))
        self.assertIn("error", result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. 없는 종목코드로 get_stock_detail 호출
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestInvalidTicker(unittest.TestCase):

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    @patch("mcp_tools.kis_stock_price", new_callable=AsyncMock, return_value={})
    @patch("mcp_tools.kis_investor_trend", new_callable=AsyncMock, return_value=[])
    @patch("mcp_tools.kis_estimate_perform", new_callable=AsyncMock, return_value={})
    def test_nonexistent_kr_ticker_returns_zeros(self, mock_est, mock_inv, mock_price, mock_token):
        """존재하지 않는 국내 종목코드 → 빈 응답, 0 값 반환 (크래시 없음)"""
        result = _run(_execute_tool("get_stock_detail", {"ticker": "999999"}))
        # 에러가 아니라 데이터가 반환되되 비어있어야 함
        self.assertNotIn("error", result)
        self.assertEqual(result.get("ticker"), "999999")

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    @patch("mcp_tools.kis_us_stock_price", new_callable=AsyncMock, return_value={})
    @patch("mcp_tools.kis_us_stock_detail", new_callable=AsyncMock, return_value={})
    def test_nonexistent_us_ticker_returns_zeros(self, mock_detail, mock_price, mock_token):
        """존재하지 않는 미국 종목코드 → 빈 응답, 크래시 없음"""
        result = _run(_execute_tool("get_stock_detail", {"ticker": "ZZZZZ"}))
        self.assertNotIn("error", result)
        self.assertEqual(result.get("ticker"), "ZZZZZ")

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value="mock_token")
    @patch("mcp_tools.kis_stock_price", new_callable=AsyncMock, return_value={})
    @patch("mcp_tools.kis_investor_trend", new_callable=AsyncMock, return_value=[])
    @patch("mcp_tools.kis_estimate_perform", new_callable=AsyncMock, return_value={})
    def test_empty_ticker_string(self, mock_est, mock_inv, mock_price, mock_token):
        """빈 문자열 ticker → 크래시 없이 처리"""
        result = _run(_execute_tool("get_stock_detail", {"ticker": ""}))
        # 빈 티커도 에러 없이 처리되어야 함
        self.assertIsInstance(result, dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. check_stoploss 보조 함수 및 KIS 토큰 실패 시 _execute_tool 핸들링
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TestStoplossHelpers(unittest.TestCase):

    def test_stoploss_sent_count_empty(self):
        """빈 sent dict → count 0"""
        from main import _get_stoploss_sent_count
        self.assertEqual(_get_stoploss_sent_count({}, "005930", "20260329"), 0)

    def test_stoploss_sent_count_existing(self):
        """기존 카운트가 있는 경우"""
        from main import _get_stoploss_sent_count
        sent = {"005930": {"date": "20260329", "count": 2}}
        self.assertEqual(_get_stoploss_sent_count(sent, "005930", "20260329"), 2)

    def test_stoploss_sent_count_different_date(self):
        """다른 날짜의 카운트 → 0"""
        from main import _get_stoploss_sent_count
        sent = {"005930": {"date": "20260328", "count": 3}}
        self.assertEqual(_get_stoploss_sent_count(sent, "005930", "20260329"), 0)

    def test_increment_stoploss_sent(self):
        """카운트 증가"""
        from main import _increment_stoploss_sent
        sent = {}
        _increment_stoploss_sent(sent, "005930", "20260329")
        self.assertEqual(sent["005930"]["count"], 1)
        self.assertEqual(sent["005930"]["date"], "20260329")
        _increment_stoploss_sent(sent, "005930", "20260329")
        self.assertEqual(sent["005930"]["count"], 2)


class TestTokenFailureInExecuteTool(unittest.TestCase):

    @patch("mcp_tools.get_kis_token", new_callable=AsyncMock, return_value=None)
    def test_no_token_returns_error(self, mock_token):
        """토큰 발급 실패 → RuntimeError → error dict 반환"""
        result = _run(_execute_tool("get_portfolio", {}))
        self.assertIn("error", result)
        self.assertIn("토큰", result["error"])


class TestIsKrTradingTime(unittest.TestCase):

    def test_weekday_market_hours(self):
        """평일 장중 → True"""
        from main import _is_kr_trading_time
        # 2026-03-30 is Monday
        dt = datetime(2026, 3, 30, 10, 0, tzinfo=KST)
        self.assertTrue(_is_kr_trading_time(dt))

    def test_weekend_returns_false(self):
        """주말 → False"""
        from main import _is_kr_trading_time
        # 2026-03-29 is Sunday
        dt = datetime(2026, 3, 29, 10, 0, tzinfo=KST)
        self.assertFalse(_is_kr_trading_time(dt))

    def test_late_night_returns_false(self):
        """심야 → False"""
        from main import _is_kr_trading_time
        dt = datetime(2026, 3, 30, 2, 0, tzinfo=KST)
        self.assertFalse(_is_kr_trading_time(dt))


if __name__ == "__main__":
    unittest.main()
