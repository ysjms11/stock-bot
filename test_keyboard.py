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
ext_stub.TypeHandler = object
ext_stub.ApplicationHandlerStop = type("ApplicationHandlerStop", (Exception,), {})

sys.modules["telegram"] = telegram_stub
sys.modules["telegram.ext"] = ext_stub

# 다른 테스트 파일이 no-op telegram stub으로 main_pkg.telegram_bot 을 먼저 import 하면
# MAIN_KEYBOARD 가 .keyboard 없이 캐시됨. good stub 설치 후 강제 재import 하여 재구성.
import importlib as _importlib
for _m in ("main_pkg._entry", "main_pkg.telegram_bot"):
    sys.modules.pop(_m, None)
_importlib.import_module("main_pkg.telegram_bot")
_importlib.import_module("main_pkg._entry")


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
        from main_pkg.telegram_bot import MAIN_KEYBOARD
        kb = MAIN_KEYBOARD.keyboard
        assert len(kb) == 3, f"행 수 {len(kb)} != 3"
        assert len(kb[0]) == 2
        assert len(kb[1]) == 2
        assert len(kb[2]) == 2

    def test_keyboard_resize(self):
        from main_pkg.telegram_bot import MAIN_KEYBOARD
        assert MAIN_KEYBOARD.resize_keyboard is True

    def test_keyboard_button_texts(self):
        from main_pkg.telegram_bot import MAIN_KEYBOARD
        flat = [btn for row in MAIN_KEYBOARD.keyboard for btn in row]
        expected = ["📊 포트폴리오", "🚨 알림현황", "📈 매크로",
                    "🔍 워치리스트", "📰 리포트", "📋 전체현황"]
        assert flat == expected

    def test_no_shopping_button(self):
        """쇼핑리스트 버튼 삭제 확인"""
        from main_pkg.telegram_bot import MAIN_KEYBOARD
        flat = [btn for row in MAIN_KEYBOARD.keyboard for btn in row]
        assert "💰 쇼핑리스트" not in flat


# ─────────────────────────────────────────────────────────────────────
# 2. 버튼 매핑 완전성 — behavioral routing tests
# _BUTTON_MAP is now function-local in main_pkg/_entry._button_handler.
# Tests verify routing by calling _button_handler with each button label.
# ─────────────────────────────────────────────────────────────────────
class TestButtonMap:
    @pytest.mark.asyncio
    async def test_all_buttons_mapped(self):
        """MAIN_KEYBOARD의 모든 버튼이 _button_handler를 통해 라우팅됨"""
        from main_pkg._entry import _button_handler
        from main_pkg.telegram_bot import (
            MAIN_KEYBOARD,
            portfolio_cmd, alert_cmd, macro, watchlist_cmd, reports_cmd, status_cmd,
        )
        cmd_mocks = {
            "📊 포트폴리오": AsyncMock(),
            "🚨 알림현황": AsyncMock(),
            "📈 매크로": AsyncMock(),
            "🔍 워치리스트": AsyncMock(),
            "📰 리포트": AsyncMock(),
            "📋 전체현황": AsyncMock(),
        }
        flat = [btn for row in MAIN_KEYBOARD.keyboard for btn in row]
        with patch("main_pkg.telegram_bot.portfolio_cmd", cmd_mocks["📊 포트폴리오"]), \
             patch("main_pkg.telegram_bot.alert_cmd",    cmd_mocks["🚨 알림현황"]), \
             patch("main_pkg.telegram_bot.macro",        cmd_mocks["📈 매크로"]), \
             patch("main_pkg.telegram_bot.watchlist_cmd", cmd_mocks["🔍 워치리스트"]), \
             patch("main_pkg.telegram_bot.reports_cmd",  cmd_mocks["📰 리포트"]), \
             patch("main_pkg.telegram_bot.status_cmd",   cmd_mocks["📋 전체현황"]):
            for btn in flat:
                update, ctx = _make_update_context()
                update.message.text = btn
                await _button_handler(update, ctx)
                # Each button must have triggered exactly one call total so far —
                # check the corresponding mock was called.
                assert cmd_mocks[btn].called, f"버튼 '{btn}' 핸들러 미호출"

    @pytest.mark.asyncio
    async def test_map_values_are_callable(self):
        """_button_handler가 callable 함수로 라우팅하는지 — 임의 버튼 라우팅 확인"""
        from main_pkg._entry import _button_handler
        update, ctx = _make_update_context()
        update.message.text = "📊 포트폴리오"
        mock_portfolio = AsyncMock()
        with patch("main_pkg.telegram_bot.portfolio_cmd", mock_portfolio):
            await _button_handler(update, ctx)
        mock_portfolio.assert_called_once_with(update, ctx)

    @pytest.mark.asyncio
    async def test_no_shopping_in_map(self):
        """쇼핑리스트 버튼은 핸들러 없음 — reply_text 미호출"""
        from main_pkg._entry import _button_handler
        update, ctx = _make_update_context()
        update.message.text = "💰 쇼핑리스트"
        await _button_handler(update, ctx)
        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_in_map(self):
        """전체현황 버튼이 status_cmd로 라우팅됨"""
        from main_pkg._entry import _button_handler
        update, ctx = _make_update_context()
        update.message.text = "📋 전체현황"
        mock_status = AsyncMock()
        with patch("main_pkg.telegram_bot.status_cmd", mock_status):
            await _button_handler(update, ctx)
        mock_status.assert_called_once_with(update, ctx)


# ─────────────────────────────────────────────────────────────────────
# 3. 워치리스트 (보유종목 간결 요약)
# ─────────────────────────────────────────────────────────────────────
class TestWatchlistCmd:
    @pytest.mark.asyncio
    async def test_empty_portfolio(self):
        """매수감시 목록이 비었을 때 안내 메시지 1회 발송"""
        from main_pkg.telegram_bot import watchlist_cmd
        update, ctx = _make_update_context()
        with patch("main_pkg.telegram_bot.load_watchalert", return_value={}):
            await watchlist_cmd(update, ctx)
        update.message.reply_text.assert_called_once()
        assert "매수감시 종목 없음" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_watchlist_shows_holdings(self):
        """매수감시 종목이 있으면 로딩 메시지 → 목록 메시지 2회 발송"""
        from main_pkg.telegram_bot import watchlist_cmd
        update, ctx = _make_update_context()
        # watchalert 형식: {ticker: {name, buy_price, ...}}
        wa = {"005930": {"name": "삼성전자", "buy_price": 60000}}
        price_data = {"stck_prpr": "68000"}
        with patch("main_pkg.telegram_bot.load_watchalert", return_value=wa), \
             patch("main_pkg.telegram_bot.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main_pkg.telegram_bot.kis_stock_price", new_callable=AsyncMock, return_value=price_data):
            await watchlist_cmd(update, ctx)
        calls = update.message.reply_text.call_args_list
        assert len(calls) == 2
        result_msg = calls[1][0][0]
        assert "삼성전자" in result_msg
        assert "60K" in result_msg   # buy_price 60000 → "60K"
        assert "68K" in result_msg   # current price 68000 → "68K"
        assert "+13.3%" in result_msg  # (68000-60000)/60000*100 = +13.3%

    @pytest.mark.asyncio
    async def test_watchlist_negative_change(self):
        """현재가가 매수감시가보다 낮으면 음수 갭 표시"""
        from main_pkg.telegram_bot import watchlist_cmd
        update, ctx = _make_update_context()
        wa = {"005930": {"name": "삼성전자", "buy_price": 60000}}
        price_data = {"stck_prpr": "58000"}
        with patch("main_pkg.telegram_bot.load_watchalert", return_value=wa), \
             patch("main_pkg.telegram_bot.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main_pkg.telegram_bot.kis_stock_price", new_callable=AsyncMock, return_value=price_data):
            await watchlist_cmd(update, ctx)
        result_msg = update.message.reply_text.call_args_list[1][0][0]
        assert "-3.3%" in result_msg  # (58000-60000)/60000*100 = -3.3% → triggered


# ─────────────────────────────────────────────────────────────────────
# 4. 전체현황 (status_cmd) — 3섹션 분류
# ─────────────────────────────────────────────────────────────────────
class TestStatusCmd:
    @pytest.mark.asyncio
    async def test_empty_all(self):
        """보유/감시 모두 비었을 때 전체현황 요약 1회 발송"""
        from main_pkg.telegram_bot import status_cmd
        update, ctx = _make_update_context()
        with patch("main_pkg.telegram_bot.load_json", return_value={}), \
             patch("main_pkg.telegram_bot.load_watchalert", return_value={}), \
             patch("main_pkg.telegram_bot.load_stoploss", return_value={}), \
             patch("main_pkg.telegram_bot.get_kis_token", new_callable=AsyncMock, return_value="tok"):
            await status_cmd(update, ctx)
        msg = update.message.reply_text.call_args[0][0]
        assert "전체현황" in msg
        assert "보유 0종목" in msg
        assert "워치 0종목" in msg

    @pytest.mark.asyncio
    async def test_holding_section(self):
        """보유종목이 있으면 보유 카운트 1로 표시"""
        from main_pkg.telegram_bot import status_cmd
        update, ctx = _make_update_context()
        pf = {"005930": {"name": "삼성전자", "qty": 10, "avg_price": 60000}}
        stops = {"005930": {"name": "삼성전자", "stop_price": 55000, "target_price": 80000}}
        price_data = {"stck_prpr": "65000"}
        with patch("main_pkg.telegram_bot.load_json", return_value=pf), \
             patch("main_pkg.telegram_bot.load_watchalert", return_value={}), \
             patch("main_pkg.telegram_bot.load_stoploss", return_value=stops), \
             patch("main_pkg.telegram_bot.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main_pkg.telegram_bot.kis_stock_price", new_callable=AsyncMock, return_value=price_data), \
             patch("main_pkg.telegram_bot._is_us_ticker", return_value=False), \
             patch("main_pkg.telegram_bot._is_us_market_hours_kst", return_value=False):
            await status_cmd(update, ctx)
        msg = update.message.reply_text.call_args[0][0]
        assert "전체현황" in msg
        assert "보유 1종목" in msg

    @pytest.mark.asyncio
    async def test_reached_section(self):
        """감시가 도달 종목은 ⚡도달 카운트에 포함"""
        from main_pkg.telegram_bot import status_cmd
        update, ctx = _make_update_context()
        wa = {"005930": {"name": "삼성전자", "buy_price": 70000}}
        stops = {"005930": {"stop_price": 60000, "target_price": 80000}}
        price_data = {"stck_prpr": "65000"}  # 65000 <= 70000 → 도달
        with patch("main_pkg.telegram_bot.load_json", return_value={}), \
             patch("main_pkg.telegram_bot.load_watchalert", return_value=wa), \
             patch("main_pkg.telegram_bot.load_stoploss", return_value=stops), \
             patch("main_pkg.telegram_bot.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main_pkg.telegram_bot.kis_stock_price", new_callable=AsyncMock, return_value=price_data), \
             patch("main_pkg.telegram_bot._is_us_ticker", return_value=False), \
             patch("main_pkg.telegram_bot._is_us_market_hours_kst", return_value=False):
            await status_cmd(update, ctx)
        msg = update.message.reply_text.call_args[0][0]
        assert "워치 1종목" in msg
        assert "⚡도달 1" in msg

    @pytest.mark.asyncio
    async def test_waiting_section(self):
        """감시가 미도달 종목 (현재가 > 감시가)은 도달 카운트 0"""
        from main_pkg.telegram_bot import status_cmd
        update, ctx = _make_update_context()
        wa = {"005930": {"name": "삼성전자", "buy_price": 60000}}
        price_data = {"stck_prpr": "68000"}  # 68000 > 60000 → 미도달
        with patch("main_pkg.telegram_bot.load_json", return_value={}), \
             patch("main_pkg.telegram_bot.load_watchalert", return_value=wa), \
             patch("main_pkg.telegram_bot.load_stoploss", return_value={}), \
             patch("main_pkg.telegram_bot.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main_pkg.telegram_bot.kis_stock_price", new_callable=AsyncMock, return_value=price_data), \
             patch("main_pkg.telegram_bot._is_us_ticker", return_value=False), \
             patch("main_pkg.telegram_bot._is_us_market_hours_kst", return_value=False):
            await status_cmd(update, ctx)
        msg = update.message.reply_text.call_args[0][0]
        assert "워치 1종목" in msg
        assert "⚡도달 0" in msg  # 미도달이므로 도달 카운트 0

    @pytest.mark.asyncio
    async def test_rr_calculation(self):
        """감시가 도달 종목이 있으면 ⚡도달 카운트에 반영"""
        from main_pkg.telegram_bot import status_cmd
        update, ctx = _make_update_context()
        # buy=65000, cur=63000 → cur <= buy → 도달
        wa = {"005930": {"name": "삼성전자", "buy_price": 65000}}
        stops = {"005930": {"stop_price": 60000, "target_price": 80000}}
        price_data = {"stck_prpr": "63000"}  # 도달
        with patch("main_pkg.telegram_bot.load_json", return_value={}), \
             patch("main_pkg.telegram_bot.load_watchalert", return_value=wa), \
             patch("main_pkg.telegram_bot.load_stoploss", return_value=stops), \
             patch("main_pkg.telegram_bot.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main_pkg.telegram_bot.kis_stock_price", new_callable=AsyncMock, return_value=price_data), \
             patch("main_pkg.telegram_bot._is_us_ticker", return_value=False), \
             patch("main_pkg.telegram_bot._is_us_market_hours_kst", return_value=False):
            await status_cmd(update, ctx)
        msg = update.message.reply_text.call_args[0][0]
        assert "⚡도달 1" in msg

    @pytest.mark.asyncio
    async def test_mixed_holding_and_watch(self):
        """보유 1종목 + 감시 1종목 (도달) 혼합"""
        from main_pkg.telegram_bot import status_cmd
        update, ctx = _make_update_context()
        pf = {"009540": {"name": "HD한국조선해양", "qty": 5, "avg_price": 300000}}
        wa = {"005930": {"name": "삼성전자", "buy_price": 70000}}
        price_009540 = {"stck_prpr": "344000"}
        price_005930 = {"stck_prpr": "68000"}  # 68000 <= 70000 → 도달

        async def mock_price(ticker, token):
            return price_009540 if ticker == "009540" else price_005930

        with patch("main_pkg.telegram_bot.load_json", return_value=pf), \
             patch("main_pkg.telegram_bot.load_watchalert", return_value=wa), \
             patch("main_pkg.telegram_bot.load_stoploss", return_value={}), \
             patch("main_pkg.telegram_bot.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main_pkg.telegram_bot.kis_stock_price", new_callable=AsyncMock, side_effect=mock_price), \
             patch("main_pkg.telegram_bot._is_us_ticker", return_value=False), \
             patch("main_pkg.telegram_bot._is_us_market_hours_kst", return_value=False):
            await status_cmd(update, ctx)
        msg = update.message.reply_text.call_args[0][0]
        assert "보유 1종목" in msg
        assert "워치 1종목" in msg
        assert "⚡도달 1" in msg  # 68000 <= 70000

    @pytest.mark.asyncio
    async def test_no_buy_price_filtered(self):
        """buy_price 없는 watchalert 항목도 watch 카운트에는 포함, 도달은 불가"""
        from main_pkg.telegram_bot import status_cmd
        update, ctx = _make_update_context()
        wa = {"005930": {"name": "삼성전자"}}  # buy_price 없음
        with patch("main_pkg.telegram_bot.load_json", return_value={}), \
             patch("main_pkg.telegram_bot.load_watchalert", return_value=wa), \
             patch("main_pkg.telegram_bot.load_stoploss", return_value={}), \
             patch("main_pkg.telegram_bot.get_kis_token", new_callable=AsyncMock, return_value="tok"):
            await status_cmd(update, ctx)
        msg = update.message.reply_text.call_args[0][0]
        assert "전체현황" in msg
        assert "⚡도달 0" in msg  # buy_price 없으면 도달 불가


# ─────────────────────────────────────────────────────────────────────
# 5. 리포트 목록 테스트
# reports_cmd reads from sqlite DB directly (no load_reports helper).
# Patch sqlite3.connect to return a fake connection.
# ─────────────────────────────────────────────────────────────────────
class TestReportsCmd:
    @pytest.mark.asyncio
    async def test_reports_not_available(self):
        from main_pkg.telegram_bot import reports_cmd
        update, ctx = _make_update_context()
        with patch("main_pkg.telegram_bot._REPORT_AVAILABLE", False):
            await reports_cmd(update, ctx)
        assert "미설치" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_reports_empty(self):
        from main_pkg.telegram_bot import reports_cmd
        update, ctx = _make_update_context()

        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = []
        fake_conn.__enter__ = lambda s: s
        fake_conn.__exit__ = MagicMock(return_value=False)

        import sqlite3 as _sqlite3
        with patch("main_pkg.telegram_bot._REPORT_AVAILABLE", True), \
             patch("sqlite3.connect", return_value=fake_conn):
            await reports_cmd(update, ctx)
        assert "최근 3일 리포트 없음" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_reports_grouped(self):
        from main_pkg.telegram_bot import reports_cmd
        today = datetime.now(KST).strftime("%Y-%m-%d")

        import sqlite3 as _sqlite3

        class _FakeRow(dict):
            def keys(self): return super().keys()

        rows = [
            _FakeRow({"date": today, "ticker": "005930", "name": "삼성전자", "source": "미래에셋", "title": "반도체 전망"}),
            _FakeRow({"date": today, "ticker": "005930", "name": "삼성전자", "source": "한투", "title": "실적 리뷰"}),
            _FakeRow({"date": today, "ticker": "000660", "name": "SK하이닉스", "source": "삼성증권", "title": "HBM 성장"}),
        ]

        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = rows
        fake_conn.row_factory = _sqlite3.Row

        update, ctx = _make_update_context()
        with patch("main_pkg.telegram_bot._REPORT_AVAILABLE", True), \
             patch("sqlite3.connect", return_value=fake_conn):
            await reports_cmd(update, ctx)
        result_msg = update.message.reply_text.call_args[0][0]
        assert "삼성전자" in result_msg
        assert "SK하이닉스" in result_msg
        assert "미래에셋" in result_msg
        assert "HBM 성장" in result_msg

    @pytest.mark.asyncio
    async def test_reports_old_filtered(self):
        """3일 이전 리포트는 필터링 — DB returns empty for cutoff query"""
        from main_pkg.telegram_bot import reports_cmd
        update, ctx = _make_update_context()

        # Empty rows means no recent reports
        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = []

        with patch("main_pkg.telegram_bot._REPORT_AVAILABLE", True), \
             patch("sqlite3.connect", return_value=fake_conn):
            await reports_cmd(update, ctx)
        assert "최근 3일 리포트 없음" in update.message.reply_text.call_args[0][0]


# ─────────────────────────────────────────────────────────────────────
# 6. portfolio_cmd / alert_cmd 기본 흐름
# ─────────────────────────────────────────────────────────────────────
class TestPortfolioCmd:
    @pytest.mark.asyncio
    async def test_empty_portfolio(self):
        from main_pkg.telegram_bot import portfolio_cmd
        update, ctx = _make_update_context()
        with patch("main_pkg.telegram_bot.load_json", return_value={}):
            await portfolio_cmd(update, ctx)
        assert "비어있음" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_portfolio_kr(self):
        from main_pkg.telegram_bot import portfolio_cmd
        update, ctx = _make_update_context()
        pf = {"005930": {"name": "삼성전자", "qty": 10, "avg_price": 60000}}
        price_data = {"stck_prpr": "65000"}
        with patch("main_pkg.telegram_bot.load_json", return_value=pf), \
             patch("main_pkg.telegram_bot.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main_pkg.telegram_bot.kis_stock_price", new_callable=AsyncMock, return_value=price_data):
            await portfolio_cmd(update, ctx)
        calls = update.message.reply_text.call_args_list
        result_msg = calls[1][0][0]
        assert "삼성전자" in result_msg
        assert "🔺" in result_msg  # 수익


class TestAlertCmd:
    @pytest.mark.asyncio
    async def test_empty_alert(self):
        from main_pkg.telegram_bot import alert_cmd
        update, ctx = _make_update_context()
        with patch("main_pkg.telegram_bot.load_stoploss", return_value={}), \
             patch("main_pkg.telegram_bot.load_watchalert", return_value={}):
            await alert_cmd(update, ctx)
        assert "설정된 알림 없음" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_alert_with_stoploss(self):
        from main_pkg.telegram_bot import alert_cmd
        update, ctx = _make_update_context()
        stops = {"005930": {"name": "삼성전자", "stop_price": 55000}}
        price_data = {"stck_prpr": "65000"}
        with patch("main_pkg.telegram_bot.load_stoploss", return_value=stops), \
             patch("main_pkg.telegram_bot.load_watchalert", return_value={}), \
             patch("main_pkg.telegram_bot.get_kis_token", new_callable=AsyncMock, return_value="tok"), \
             patch("main_pkg.telegram_bot.kis_stock_price", new_callable=AsyncMock, return_value=price_data):
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
        from main_pkg._entry import _button_handler
        update, ctx = _make_update_context()
        update.message.text = "📈 매크로"
        mock_macro = AsyncMock()
        with patch("main_pkg.telegram_bot.macro", mock_macro):
            await _button_handler(update, ctx)
        mock_macro.assert_called_once_with(update, ctx)

    @pytest.mark.asyncio
    async def test_status_button_routes(self):
        """📋 전체현황 버튼 라우팅"""
        from main_pkg._entry import _button_handler
        update, ctx = _make_update_context()
        update.message.text = "📋 전체현황"
        mock_status = AsyncMock()
        with patch("main_pkg.telegram_bot.status_cmd", mock_status):
            await _button_handler(update, ctx)
        mock_status.assert_called_once_with(update, ctx)

    @pytest.mark.asyncio
    async def test_unknown_text_ignored(self):
        from main_pkg._entry import _button_handler
        update, ctx = _make_update_context()
        update.message.text = "아무 텍스트"
        await _button_handler(update, ctx)
        update.message.reply_text.assert_not_called()
