"""
텔레그램 Reply Keyboard + 명령어 테스트
1. ReplyKeyboardMarkup 생성 검증
2. 버튼 매핑 완전성
3. 워치리스트 (보유종목 간결 요약)
4. 전체현황 (보유+매수감시 통합, 3섹션 분류)
5. 리포트 목록 포맷
6. portfolio_cmd / alert_cmd 기본 흐름
"""
import pytest
import sys
import types
import json
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# ── telegram stub ──
telegram_stub = types.ModuleType("telegram")

class _FakeReplyKeyboardMarkup:
    def __init__(self, keyboard, **kwargs):
        self.keyboard = keyboard
        self.resize_keyboard = kwargs.get("resize_keyboard", False)

telegram_stub.Update = object
telegram_stub.ReplyKeyboardMarkup = _FakeReplyKeyboardMarkup

ext_stub = types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.MessageHandler = object
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
ext_stub.filters = type("filters", (), {"TEXT": None, "Regex": lambda self, x: x})()

sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)


# ── helpers ──
def _make_update_context():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.message.text = ""
    context = MagicMock()
    context.args = []
    return update, context


# ─────────────────────────────────────────────────────────────────────
# 1. Reply Keyboard 생성 테스트
# ─────────────────────────────────────────────────────────────────────
class TestReplyKeyboard:
    def test_keyboard_layout(self):
        """MAIN_KEYBOARD가 3행 2열 레이아웃으로 생성되는지"""
        from main import MAIN_KEYBOARD
        kb = MAIN_KEYBOARD.keyboard
        assert len(kb) == 3, f"행 수 {len(kb)} != 3"
        assert len(kb[0]) == 2
        assert len(kb[1]) == 2
        assert len(kb[2]) == 2

    def test_keyboard_resize(self):
        from main import MAIN_KEYBOARD
        assert MAIN_KEYBOARD.resize_keyboard is True

    def test_keyboard_button_texts(self):
        from main import MAIN_KEYBOARD
        flat = [btn for row in MAIN_KEYBOARD.keyboard for btn in row]
        expected = ["📊 포트폴리오", "🚨 알림현황", "📈 매크로",
                    "🔍 워치리스트", "📰 리포트", "📋 전체현황"]
        assert flat == expected

    def test_no_shopping_button(self):
        """쇼핑리스트 버튼 삭제 확인"""
        from main import MAIN_KEYBOARD
        flat = [btn for row in MAIN_KEYBOARD.keyboard for btn in row]
        assert "💰 쇼핑리스트" not in flat


# ─────────────────────────────────────────────────────────────────────
# 2. 버튼 매핑 완전성
# ─────────────────────────────────────────────────────────────────────
class TestButtonMap:
    def test_all_buttons_mapped(self):
        from main import MAIN_KEYBOARD, _BUTTON_MAP
        flat = [btn for row in MAIN_KEYBOARD.keyboard for btn in row]
        for btn in flat:
            assert btn in _BUTTON_MAP, f"버튼 '{btn}' 핸들러 매핑 없음"

    def test_map_values_are_callable(self):
        from main import _BUTTON_MAP
        for key, fn in _BUTTON_MAP.items():
            assert callable(fn), f"'{key}' 핸들러가 callable이 아님"

    def test_no_shopping_in_map(self):
        """쇼핑리스트 매핑 삭제 확인"""
        from main import _BUTTON_MAP
        assert "💰 쇼핑리스트" not in _BUTTON_MAP

    def test_status_in_map(self):
        """전체현황 매핑 확인"""
        from main import _BUTTON_MAP
        assert "📋 전체현황" in _BUTTON_MAP


# ─────────────────────────────────────────────────────────────────────
# 3. 워치리스트 (보유종목 간결 요약)
# ─────────────────────────────────────────────────────────────────────
class TestWatchlistCmd:
    @pytest.mark.asyncio
    async def test_empty_portfolio(self):
        from main import watchlist_cmd
        update, ctx = _make_update_context()
        with patch("main.load_json", return_value={}):
            await watchlist_cmd(update, ctx)
        update.message.reply_text.assert_called_once()
        assert "보유종목 없음" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_watchlist_shows_holdings(self):
        from main import watchlist_cmd
        update, ctx = _make_update_context()
        pf = {"005930": {"name": "삼성전자", "qty": 10, "avg_price": 60000}}
        price_data = {"stck_prpr": "68000", "prdy_ctrt": "+2.0"}
        with patch("main.load_json", return_value=pf), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, return_value=price_data):
            await watchlist_cmd(update, ctx)
        calls = update.message.reply_text.call_args_list
        assert len(calls) == 2
        result_msg = calls[1][0][0]
        assert "삼성전자" in result_msg
        assert "68,000" in result_msg
        assert "🔺" in result_msg  # 양수 등락률

    @pytest.mark.asyncio
    async def test_watchlist_negative_change(self):
        from main import watchlist_cmd
        update, ctx = _make_update_context()
        pf = {"005930": {"name": "삼성전자", "qty": 10, "avg_price": 60000}}
        price_data = {"stck_prpr": "58000", "prdy_ctrt": "-1.5"}
        with patch("main.load_json", return_value=pf), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, return_value=price_data):
            await watchlist_cmd(update, ctx)
        result_msg = update.message.reply_text.call_args_list[1][0][0]
        assert "🔻" in result_msg  # 음수 등락률


# ─────────────────────────────────────────────────────────────────────
# 4. 전체현황 (status_cmd) — 3섹션 분류
# ─────────────────────────────────────────────────────────────────────
class TestStatusCmd:
    @pytest.mark.asyncio
    async def test_empty_all(self):
        """보유/감시 모두 비었을 때"""
        from main import status_cmd
        update, ctx = _make_update_context()
        with patch("main.load_json", return_value={}), \
             patch("main.load_watchalert", return_value={}), \
             patch("main.load_stoploss", return_value={}):
            await status_cmd(update, ctx)
        assert "보유/감시 종목 없음" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_holding_section(self):
        """보유종목 섹션 표시"""
        from main import status_cmd
        update, ctx = _make_update_context()
        pf = {"005930": {"name": "삼성전자", "qty": 10, "avg_price": 60000}}
        stops = {"005930": {"name": "삼성전자", "stop_price": 55000, "target_price": 80000}}
        price_data = {"stck_prpr": "65000"}
        with patch("main.load_json", return_value=pf), \
             patch("main.load_watchalert", return_value={}), \
             patch("main.load_stoploss", return_value=stops), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, return_value=price_data), \
             patch("main._is_us_ticker", return_value=False), \
             patch("main._is_us_market_hours_kst", return_value=False):
            await status_cmd(update, ctx)
        result_msg = update.message.reply_text.call_args_list[1][0][0]
        assert "보유종목" in result_msg
        assert "삼성전자" in result_msg
        assert "보유 1개" in result_msg

    @pytest.mark.asyncio
    async def test_reached_section(self):
        """감시가 도달 종목 → 🔴 섹션에 분류"""
        from main import status_cmd
        update, ctx = _make_update_context()
        wa = {"005930": {"name": "삼성전자", "buy_price": 70000}}
        stops = {"005930": {"stop_price": 60000, "target_price": 80000}}
        price_data = {"stck_prpr": "65000"}  # 65000 <= 70000 → 도달
        with patch("main.load_json", return_value={}), \
             patch("main.load_watchalert", return_value=wa), \
             patch("main.load_stoploss", return_value=stops), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, return_value=price_data), \
             patch("main._is_us_ticker", return_value=False), \
             patch("main._is_us_market_hours_kst", return_value=False):
            await status_cmd(update, ctx)
        result_msg = update.message.reply_text.call_args_list[1][0][0]
        assert "감시가 도달" in result_msg
        assert "🔴" in result_msg
        assert "감시 도달 1개" in result_msg

    @pytest.mark.asyncio
    async def test_waiting_section(self):
        """감시가 미도달 종목 → ⚪ 대기 섹션"""
        from main import status_cmd
        update, ctx = _make_update_context()
        wa = {"005930": {"name": "삼성전자", "buy_price": 60000}}
        price_data = {"stck_prpr": "68000"}  # 68000 > 60000 → 대기
        with patch("main.load_json", return_value={}), \
             patch("main.load_watchalert", return_value=wa), \
             patch("main.load_stoploss", return_value={}), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, return_value=price_data), \
             patch("main._is_us_ticker", return_value=False), \
             patch("main._is_us_market_hours_kst", return_value=False):
            await status_cmd(update, ctx)
        result_msg = update.message.reply_text.call_args_list[1][0][0]
        assert "대기" in result_msg
        assert "⚪" in result_msg
        assert "대기 1개" in result_msg

    @pytest.mark.asyncio
    async def test_rr_calculation(self):
        """RR 비율 계산 확인"""
        from main import status_cmd
        update, ctx = _make_update_context()
        # buy=65000, stop=60000, target=80000 → risk=5000, reward=15000 → RR 1:3.0
        wa = {"005930": {"name": "삼성전자", "buy_price": 65000}}
        stops = {"005930": {"stop_price": 60000, "target_price": 80000}}
        price_data = {"stck_prpr": "63000"}  # 도달
        with patch("main.load_json", return_value={}), \
             patch("main.load_watchalert", return_value=wa), \
             patch("main.load_stoploss", return_value=stops), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, return_value=price_data), \
             patch("main._is_us_ticker", return_value=False), \
             patch("main._is_us_market_hours_kst", return_value=False):
            await status_cmd(update, ctx)
        result_msg = update.message.reply_text.call_args_list[1][0][0]
        assert "RR 1:3.0" in result_msg

    @pytest.mark.asyncio
    async def test_mixed_holding_and_watch(self):
        """보유 + 감시 혼합"""
        from main import status_cmd
        update, ctx = _make_update_context()
        pf = {"009540": {"name": "HD한국조선해양", "qty": 5, "avg_price": 300000}}
        wa = {"005930": {"name": "삼성전자", "buy_price": 70000}}
        price_009540 = {"stck_prpr": "344000"}
        price_005930 = {"stck_prpr": "68000"}

        async def mock_price(ticker, token):
            return price_009540 if ticker == "009540" else price_005930

        with patch("main.load_json", return_value=pf), \
             patch("main.load_watchalert", return_value=wa), \
             patch("main.load_stoploss", return_value={}), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, side_effect=mock_price), \
             patch("main._is_us_ticker", return_value=False), \
             patch("main._is_us_market_hours_kst", return_value=False):
            await status_cmd(update, ctx)
        result_msg = update.message.reply_text.call_args_list[1][0][0]
        assert "보유종목" in result_msg
        assert "HD한국조선해양" in result_msg
        assert "삼성전자" in result_msg
        assert "보유 1개" in result_msg
        assert "감시 도달 1개" in result_msg  # 68000 <= 70000

    @pytest.mark.asyncio
    async def test_no_buy_price_filtered(self):
        """buy_price 없는 watchalert 항목은 무시"""
        from main import status_cmd
        update, ctx = _make_update_context()
        wa = {"005930": {"name": "삼성전자"}}  # buy_price 없음
        with patch("main.load_json", return_value={}), \
             patch("main.load_watchalert", return_value=wa), \
             patch("main.load_stoploss", return_value={}):
            await status_cmd(update, ctx)
        assert "보유/감시 종목 없음" in update.message.reply_text.call_args[0][0]


# ─────────────────────────────────────────────────────────────────────
# 5. 리포트 목록 테스트
# ─────────────────────────────────────────────────────────────────────
class TestReportsCmd:
    @pytest.mark.asyncio
    async def test_reports_not_available(self):
        from main import reports_cmd
        update, ctx = _make_update_context()
        with patch("main._REPORT_AVAILABLE", False):
            await reports_cmd(update, ctx)
        assert "미설치" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_reports_empty(self):
        from main import reports_cmd
        update, ctx = _make_update_context()
        with patch("main._REPORT_AVAILABLE", True), \
             patch("main.load_reports", return_value={"reports": [], "last_collected": ""}):
            await reports_cmd(update, ctx)
        assert "수집된 리포트 없음" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_reports_grouped(self):
        from main import reports_cmd
        today = datetime.now(KST).strftime("%Y-%m-%d")
        reports = {
            "reports": [
                {"date": today, "ticker": "005930", "name": "삼성전자",
                 "source": "미래에셋", "title": "반도체 전망"},
                {"date": today, "ticker": "005930", "name": "삼성전자",
                 "source": "한투", "title": "실적 리뷰"},
                {"date": today, "ticker": "000660", "name": "SK하이닉스",
                 "source": "삼성증권", "title": "HBM 성장"},
            ],
            "last_collected": f"{today}T07:00:00",
        }
        update, ctx = _make_update_context()
        with patch("main._REPORT_AVAILABLE", True), \
             patch("main.load_reports", return_value=reports):
            await reports_cmd(update, ctx)
        result_msg = update.message.reply_text.call_args[0][0]
        assert "삼성전자" in result_msg
        assert "SK하이닉스" in result_msg
        assert "미래에셋" in result_msg
        assert "HBM 성장" in result_msg

    @pytest.mark.asyncio
    async def test_reports_old_filtered(self):
        """3일 이전 리포트는 필터링"""
        from main import reports_cmd
        old_date = (datetime.now(KST) - timedelta(days=5)).strftime("%Y-%m-%d")
        reports = {
            "reports": [
                {"date": old_date, "ticker": "005930", "name": "삼성전자",
                 "source": "미래에셋", "title": "오래된 리포트"},
            ],
            "last_collected": old_date,
        }
        update, ctx = _make_update_context()
        with patch("main._REPORT_AVAILABLE", True), \
             patch("main.load_reports", return_value=reports):
            await reports_cmd(update, ctx)
        assert "최근 3일 리포트 없음" in update.message.reply_text.call_args[0][0]


# ─────────────────────────────────────────────────────────────────────
# 6. portfolio_cmd / alert_cmd 기본 흐름
# ─────────────────────────────────────────────────────────────────────
class TestPortfolioCmd:
    @pytest.mark.asyncio
    async def test_empty_portfolio(self):
        from main import portfolio_cmd
        update, ctx = _make_update_context()
        with patch("main.load_json", return_value={}):
            await portfolio_cmd(update, ctx)
        assert "비어있음" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_portfolio_kr(self):
        from main import portfolio_cmd
        update, ctx = _make_update_context()
        pf = {"005930": {"name": "삼성전자", "qty": 10, "avg_price": 60000}}
        price_data = {"stck_prpr": "65000"}
        with patch("main.load_json", return_value=pf), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, return_value=price_data):
            await portfolio_cmd(update, ctx)
        calls = update.message.reply_text.call_args_list
        result_msg = calls[1][0][0]
        assert "삼성전자" in result_msg
        assert "🔺" in result_msg  # 수익


class TestAlertCmd:
    @pytest.mark.asyncio
    async def test_empty_alert(self):
        from main import alert_cmd
        update, ctx = _make_update_context()
        with patch("main.load_stoploss", return_value={}), \
             patch("main.load_watchalert", return_value={}):
            await alert_cmd(update, ctx)
        assert "설정된 알림 없음" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_alert_with_stoploss(self):
        from main import alert_cmd
        update, ctx = _make_update_context()
        stops = {"005930": {"name": "삼성전자", "stop_price": 55000}}
        price_data = {"stck_prpr": "65000"}
        with patch("main.load_stoploss", return_value=stops), \
             patch("main.load_watchalert", return_value={}), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, return_value=price_data):
            await alert_cmd(update, ctx)
        calls = update.message.reply_text.call_args_list
        result_msg = calls[1][0][0]
        assert "삼성전자" in result_msg
        assert "55,000" in result_msg


# ─────────────────────────────────────────────────────────────────────
# 7. _button_handler 라우팅 테스트
# ─────────────────────────────────────────────────────────────────────
class TestButtonHandler:
    @pytest.mark.asyncio
    async def test_button_routes_to_handler(self):
        from main import _button_handler, _BUTTON_MAP
        update, ctx = _make_update_context()
        update.message.text = "📈 매크로"
        mock_macro = AsyncMock()
        original = _BUTTON_MAP["📈 매크로"]
        _BUTTON_MAP["📈 매크로"] = mock_macro
        try:
            await _button_handler(update, ctx)
            mock_macro.assert_called_once_with(update, ctx)
        finally:
            _BUTTON_MAP["📈 매크로"] = original

    @pytest.mark.asyncio
    async def test_status_button_routes(self):
        """📋 전체현황 버튼 라우팅"""
        from main import _button_handler, _BUTTON_MAP
        update, ctx = _make_update_context()
        update.message.text = "📋 전체현황"
        mock_status = AsyncMock()
        original = _BUTTON_MAP["📋 전체현황"]
        _BUTTON_MAP["📋 전체현황"] = mock_status
        try:
            await _button_handler(update, ctx)
            mock_status.assert_called_once_with(update, ctx)
        finally:
            _BUTTON_MAP["📋 전체현황"] = original

    @pytest.mark.asyncio
    async def test_unknown_text_ignored(self):
        from main import _button_handler
        update, ctx = _make_update_context()
        update.message.text = "아무 텍스트"
        await _button_handler(update, ctx)
        update.message.reply_text.assert_not_called()
