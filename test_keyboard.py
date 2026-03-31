"""
텔레그램 Reply Keyboard + 새 명령어 테스트
1. ReplyKeyboardMarkup 생성 검증
2. 버튼 매핑 완전성
3. 워치리스트 포맷 (현재가+감시가%)
4. 쇼핑리스트 필터 + 포맷
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
                    "🔍 워치리스트", "📰 리포트", "💰 쇼핑리스트"]
        assert flat == expected


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


# ─────────────────────────────────────────────────────────────────────
# 3. 워치리스트 포맷 테스트
# ─────────────────────────────────────────────────────────────────────
class TestWatchlistCmd:
    @pytest.mark.asyncio
    async def test_empty_watchlist(self):
        from main import watchlist_cmd
        update, ctx = _make_update_context()
        with patch("main.load_watchlist", return_value={}), \
             patch("main.load_watchalert", return_value={}):
            await watchlist_cmd(update, ctx)
        update.message.reply_text.assert_called_once()
        assert "비어있음" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_watchlist_with_alert(self):
        from main import watchlist_cmd
        update, ctx = _make_update_context()
        wl = {"005930": "삼성전자"}
        wa = {"005930": {"name": "삼성전자", "buy_price": 70000}}
        price_data = {"stck_prpr": "68000", "prdy_ctrt": "-1.5"}
        with patch("main.load_watchlist", return_value=wl), \
             patch("main.load_watchalert", return_value=wa), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, return_value=price_data):
            await watchlist_cmd(update, ctx)
        calls = update.message.reply_text.call_args_list
        # 두 번 호출: "조회 중..." + 결과
        assert len(calls) == 2
        result_msg = calls[1][0][0]
        assert "삼성전자" in result_msg
        assert "68,000" in result_msg
        assert "🔴" in result_msg  # 현재가 <= 감시가

    @pytest.mark.asyncio
    async def test_watchlist_above_alert(self):
        from main import watchlist_cmd
        update, ctx = _make_update_context()
        wl = {"005930": "삼성전자"}
        wa = {"005930": {"name": "삼성전자", "buy_price": 60000}}
        price_data = {"stck_prpr": "68000", "prdy_ctrt": "+1.0"}
        with patch("main.load_watchlist", return_value=wl), \
             patch("main.load_watchalert", return_value=wa), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, return_value=price_data):
            await watchlist_cmd(update, ctx)
        result_msg = update.message.reply_text.call_args_list[1][0][0]
        assert "⚪" in result_msg  # 현재가 > 감시가


# ─────────────────────────────────────────────────────────────────────
# 4. 쇼핑리스트 테스트
# ─────────────────────────────────────────────────────────────────────
class TestShoppingCmd:
    @pytest.mark.asyncio
    async def test_empty_shopping(self):
        from main import shopping_cmd
        update, ctx = _make_update_context()
        with patch("main.load_watchalert", return_value={}):
            await shopping_cmd(update, ctx)
        assert "매수감시 종목 없음" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_shopping_with_rr(self):
        from main import shopping_cmd
        update, ctx = _make_update_context()
        wa = {"005930": {"name": "삼성전자", "buy_price": 65000, "memo": "실적 기대"}}
        stops = {"005930": {"name": "삼성전자", "stop_price": 60000, "target_price": 80000}}
        price_data = {"stck_prpr": "63000", "prdy_ctrt": "-1.0"}
        with patch("main.load_watchalert", return_value=wa), \
             patch("main.load_stoploss", return_value=stops), \
             patch("main.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main.kis_stock_price", new_callable=AsyncMock, return_value=price_data), \
             patch("main._is_us_ticker", return_value=False):
            await shopping_cmd(update, ctx)
        calls = update.message.reply_text.call_args_list
        result_msg = calls[1][0][0]
        assert "삼성전자" in result_msg
        assert "RR" in result_msg
        assert "🔴" in result_msg  # 현재가 <= 감시가
        assert "실적 기대" in result_msg

    @pytest.mark.asyncio
    async def test_shopping_no_buy_price_filtered(self):
        """buy_price 없는 항목은 필터링됨"""
        from main import shopping_cmd
        update, ctx = _make_update_context()
        wa = {"005930": {"name": "삼성전자"}}  # buy_price 없음
        with patch("main.load_watchalert", return_value=wa):
            await shopping_cmd(update, ctx)
        assert "매수감시 종목 없음" in update.message.reply_text.call_args[0][0]


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
    async def test_unknown_text_ignored(self):
        from main import _button_handler
        update, ctx = _make_update_context()
        update.message.text = "아무 텍스트"
        await _button_handler(update, ctx)
        update.message.reply_text.assert_not_called()
