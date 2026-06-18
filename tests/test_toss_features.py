"""tests/test_toss_features.py — Toss 3 new features unit tests.

① fetch_toss_orders / sync_trades_from_toss
② fetch_toss_market_calendar / is_kr_business_day_toss / is_us_market_open_now / check_kr_holiday_drift
③ fetch_toss_stock_warnings / check_watch_warnings

단위 테스트는 항상 실행. @pytest.mark.live 는 --run-live 시만 실행.
"""
import copy
import json
import os
import pytest

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# FEATURE ① — 체결이력 fixtures
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# Wire 형식으로 실제 Toss 주문 응답을 모사
_ORDER_BUY_005930 = {
    "orderId":    "ORD001",
    "symbol":     "005930",
    "side":       "BUY",
    "status":     "FILLED",
    "price":      "70000",
    "quantity":   "10",
    "currency":   "KRW",
    "orderedAt":  "2026-06-01T10:00:00+09:00",
    "execution":  {"filledQuantity": "10", "averageFilledPrice": "70000"},
}
_ORDER_BUY_005930_2 = {
    "orderId":    "ORD002",
    "symbol":     "005930",
    "side":       "BUY",
    "status":     "FILLED",
    "price":      "80000",
    "quantity":   "5",
    "currency":   "KRW",
    "orderedAt":  "2026-06-05T10:00:00+09:00",
    "execution":  {"filledQuantity": "5", "averageFilledPrice": "80000"},
}
_ORDER_SELL_005930 = {
    "orderId":    "ORD003",
    "symbol":     "005930",
    "side":       "SELL",
    "status":     "FILLED",
    "price":      "90000",
    "quantity":   "5",
    "currency":   "KRW",
    "orderedAt":  "2026-06-10T14:00:00+09:00",
    "execution":  {"filledQuantity": "5", "averageFilledPrice": "90000"},
}
_ORDER_CANCELED = {
    "orderId":    "ORD004",
    "symbol":     "000660",
    "side":       "BUY",
    "status":     "CANCELED",
    "price":      "50000",
    "quantity":   "3",
    "currency":   "KRW",
    "orderedAt":  "2026-06-11T09:00:00+09:00",
    "execution":  {"filledQuantity": "0", "averageFilledPrice": "0"},  # not filled
}
_ORDER_BAD_PRICE = {
    "orderId":    "ORD005",
    "symbol":     "NVDA",
    "side":       "BUY",
    "status":     "FILLED",
    "price":      "100",
    "quantity":   "2",
    "currency":   "USD",
    "orderedAt":  "2026-06-12T11:00:00+09:00",
    "execution":  {"filledQuantity": "2", "averageFilledPrice": "inf"},  # bad price → skip
}

_ALL_ORDERS = [
    _ORDER_BUY_005930,
    _ORDER_BUY_005930_2,
    _ORDER_SELL_005930,
    _ORDER_CANCELED,
    _ORDER_BAD_PRICE,
]


def _make_trade_log(tmp_path, trades: list) -> str:
    p = tmp_path / "trade_log.json"
    p.write_text(json.dumps({"trades": trades}, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# ① 단위 테스트 — fetch_toss_orders
# ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFetchTossOrders:
    """fetch_toss_orders 단위 (HTTP 레이어 mock)."""

    @pytest.mark.asyncio
    async def test_single_page_returns_all_orders(self, monkeypatch):
        """단일 페이지: hasNext=False → 모든 주문 반환."""
        async def _fake_accounts():
            return [{"accountSeq": "TEST01"}]

        async def _fake_token():
            return "fake_token"

        import aiohttp

        class _FakeResponse:
            status = 200
            async def json(self, **kw):
                return {
                    "result": {
                        "orders":     copy.deepcopy(_ALL_ORDERS),
                        "nextCursor": None,
                        "hasNext":    False,
                    }
                }
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def text(self): return ""

        class _FakeSession:
            def get(self, url, **kw):
                return _FakeResponse()
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        monkeypatch.setattr("kis_api.toss.fetch_toss_accounts", _fake_accounts)
        monkeypatch.setattr("kis_api.toss.get_toss_token",      _fake_token)
        monkeypatch.setattr("aiohttp.ClientSession", lambda **kw: _FakeSession())

        from kis_api.toss import fetch_toss_orders
        result = await fetch_toss_orders(status="CLOSED")

        assert result is not None
        assert len(result) == len(_ALL_ORDERS)

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self, monkeypatch):
        """HTTP 오류 → None 반환 (빈 list와 구분)."""
        async def _fake_accounts():
            return [{"accountSeq": "TEST01"}]

        async def _fake_token():
            return "fake_token"

        class _FakeResponse:
            status = 500
            async def text(self): return "internal error"
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        class _FakeSession:
            def get(self, url, **kw):
                return _FakeResponse()
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        monkeypatch.setattr("kis_api.toss.fetch_toss_accounts", _fake_accounts)
        monkeypatch.setattr("kis_api.toss.get_toss_token",      _fake_token)
        monkeypatch.setattr("aiohttp.ClientSession", lambda **kw: _FakeSession())

        from kis_api.toss import fetch_toss_orders
        result = await fetch_toss_orders(status="CLOSED")
        assert result is None, "HTTP 오류 시 None이어야 함 (빈 list와 구분)"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# ① 단위 테스트 — sync_trades_from_toss
# ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSyncTradesFromToss:
    """sync_trades_from_toss 단위 (fetch_toss_orders + trade_log mock)."""

    @pytest.fixture(autouse=True)
    def _patch_trade_log_file(self, tmp_path, monkeypatch):
        """TRADE_LOG_FILE을 tmp_path로 리다이렉트."""
        self._trade_log_path = str(tmp_path / "trade_log.json")
        monkeypatch.setattr("kis_api._config.TRADE_LOG_FILE", self._trade_log_path)
        monkeypatch.setattr("kis_api.toss.TRADE_LOG_FILE",    self._trade_log_path, raising=False)
        import kis_api._config as _cfg
        monkeypatch.setattr(_cfg, "TRADE_LOG_FILE", self._trade_log_path)
        import kis_api._files as _files
        monkeypatch.setattr(_files, "TRADE_LOG_FILE", self._trade_log_path, raising=False)

    def _write_trade_log(self, trades: list):
        with open(self._trade_log_path, "w", encoding="utf-8") as f:
            json.dump({"trades": trades}, f, ensure_ascii=False)

    def _read_trade_log(self) -> list:
        try:
            with open(self._trade_log_path, encoding="utf-8") as f:
                return json.load(f).get("trades", [])
        except FileNotFoundError:
            return []

    @pytest.fixture
    def _patch_orders_all(self, monkeypatch):
        async def _fake_orders(status="CLOSED", account_seq=None):
            return copy.deepcopy(_ALL_ORDERS)
        monkeypatch.setattr("kis_api.toss.fetch_toss_orders", _fake_orders)

    @pytest.fixture
    def _patch_orders_none(self, monkeypatch):
        """fetch_toss_orders가 None 반환 (API 실패)."""
        async def _fake_orders(status="CLOSED", account_seq=None):
            return None
        monkeypatch.setattr("kis_api.toss.fetch_toss_orders", _fake_orders)

    # ── CANCELED → 스킵 ──
    @pytest.mark.asyncio
    async def test_canceled_order_skipped(self, _patch_orders_all):
        from kis_api.toss import sync_trades_from_toss
        r = await sync_trades_from_toss()
        assert r["ok"] is True
        trades = self._read_trade_log()
        order_ids = {t["id"] for t in trades}
        assert "ORD004" not in order_ids, "CANCELED 주문이 trade_log에 포함됨"

    # ── bad price → 스킵 (침묵-0 금지) ──
    @pytest.mark.asyncio
    async def test_bad_price_order_skipped(self, _patch_orders_all):
        from kis_api.toss import sync_trades_from_toss
        r = await sync_trades_from_toss()
        assert r["ok"] is True
        trades = self._read_trade_log()
        order_ids = {t["id"] for t in trades}
        assert "ORD005" not in order_ids, "inf price 주문이 trade_log에 포함됨 — 침묵-0 위반"

    # ── dedup: 재실행해도 추가 없음 ──
    @pytest.mark.asyncio
    async def test_dedup_by_order_id(self, _patch_orders_all):
        from kis_api.toss import sync_trades_from_toss
        # 1차 실행
        r1 = await sync_trades_from_toss()
        assert r1["ok"] is True
        added_first = r1["added"]
        # 2차 실행 — 같은 데이터
        r2 = await sync_trades_from_toss()
        assert r2["ok"] is True
        assert r2["added"] == 0, "재실행 시 신규 추가가 있어선 안 됨 (dedup 실패)"
        # 전체 건수 불변
        after = self._read_trade_log()
        assert len(after) == added_first

    # ── 수동 entry 보존 ──
    @pytest.mark.asyncio
    async def test_manual_entries_preserved(self, _patch_orders_all):
        manual = {
            "id":       "MANUAL001",
            "ticker":   "035720",
            "name":     "카카오",
            "side":     "buy",
            "qty":      10.0,
            "price":    45000.0,
            "date":     "2026-05-01",
            "currency": "KRW",
            "source":   "manual",
        }
        self._write_trade_log([manual])
        from kis_api.toss import sync_trades_from_toss
        r = await sync_trades_from_toss()
        assert r["ok"] is True
        trades = self._read_trade_log()
        ids = {t["id"] for t in trades}
        assert "MANUAL001" in ids, "수동 entry가 제거됨"

    # ── sell P&L running-avg (known sequence) ──
    @pytest.mark.asyncio
    async def test_sell_pnl_running_avg_correct(self, _patch_orders_all):
        """
        BUY 10주 @ 70,000 (ORD001)
        BUY  5주 @ 80,000 (ORD002)
        running avg cost = (10*70000 + 5*80000) / 15 = 73333.33...

        SELL 5주 @ 90,000 (ORD003)
        expected pnl = (90000 - 73333.33) * 5 = 83333.33
        """
        from kis_api.toss import sync_trades_from_toss
        r = await sync_trades_from_toss()
        assert r["ok"] is True
        assert r["sells_with_pnl"] >= 1

        trades = self._read_trade_log()
        sell = next((t for t in trades if t.get("id") == "ORD003"), None)
        assert sell is not None

        expected_avg_cost = (10 * 70000 + 5 * 80000) / 15
        expected_pnl = (90000 - expected_avg_cost) * 5
        assert sell.get("pnl") == pytest.approx(expected_pnl, rel=1e-4)
        assert sell.get("result") == "win"
        assert sell.get("pnl_note") == "running_avg"
        # pnl_pct must be PERCENT (e.g. 22.73), NOT fraction (e.g. 0.2273)
        expected_pnl_pct = round((90000 / expected_avg_cost - 1) * 100, 2)
        assert sell.get("pnl_pct") == pytest.approx(expected_pnl_pct, rel=1e-4)

    # ── breakeven sell (pnl==0) → result=="breakeven" ──
    @pytest.mark.asyncio
    async def test_sell_breakeven_result(self, monkeypatch):
        """SELL at exactly avg_cost → pnl==0, result=='breakeven'."""
        buy_price = 75000
        breakeven_orders = [
            {
                "orderId":   "ORD_BE_BUY",
                "symbol":    "035420",
                "side":      "BUY",
                "status":    "FILLED",
                "price":     str(buy_price),
                "quantity":  "10",
                "currency":  "KRW",
                "orderedAt": "2026-06-01T10:00:00+09:00",
                "execution": {"filledQuantity": "10", "averageFilledPrice": str(buy_price)},
            },
            {
                "orderId":   "ORD_BE_SELL",
                "symbol":    "035420",
                "side":      "SELL",
                "status":    "FILLED",
                "price":     str(buy_price),  # sell at exactly buy price → pnl==0
                "quantity":  "10",
                "currency":  "KRW",
                "orderedAt": "2026-06-10T14:00:00+09:00",
                "execution": {"filledQuantity": "10", "averageFilledPrice": str(buy_price)},
            },
        ]

        async def _fake_orders(status="CLOSED", account_seq=None):
            return breakeven_orders

        monkeypatch.setattr("kis_api.toss.fetch_toss_orders", _fake_orders)

        from kis_api.toss import sync_trades_from_toss
        r = await sync_trades_from_toss()
        assert r["ok"] is True

        trades = self._read_trade_log()
        sell = next((t for t in trades if t.get("id") == "ORD_BE_SELL"), None)
        assert sell is not None
        assert sell.get("pnl") == pytest.approx(0.0, abs=1e-6)
        assert sell.get("result") == "breakeven", \
            f"pnl==0 sell should be 'breakeven', got {sell.get('result')!r}"
        assert sell.get("pnl_pct") == pytest.approx(0.0, abs=1e-6)

    # ── SAFETY GATE: fetch=None → trade_log 바이트 불변 ──
    @pytest.mark.asyncio
    async def test_safety_gate_no_write_on_none(self, _patch_orders_none):
        initial = [{"id": "MANUAL001", "ticker": "005930", "side": "buy",
                    "qty": 5.0, "price": 70000.0, "date": "2026-06-01",
                    "currency": "KRW", "source": "manual"}]
        self._write_trade_log(initial)
        original_bytes = open(self._trade_log_path, "rb").read()

        from kis_api.toss import sync_trades_from_toss
        r = await sync_trades_from_toss()
        assert r["ok"] is False
        assert r["reason"] == "fetch_failed"
        # 파일 내용 바이트-동일
        assert open(self._trade_log_path, "rb").read() == original_bytes, \
            "fetch 실패 시 trade_log가 변경됨 — safety gate 실패"

    # ── sell 이전에 buy 없으면 pnl=None (침묵-0 금지) ──
    @pytest.mark.asyncio
    async def test_sell_without_prior_buy_no_pnl(self, monkeypatch):
        """buy 없이 sell만 있으면 pnl 필드 자체가 없어야 함 (0 주입 금지)."""
        sell_only = [
            {
                "orderId":   "ORD_SELL_ONLY",
                "symbol":    "UNKNOWN",
                "side":      "SELL",
                "status":    "FILLED",
                "price":     "100",
                "quantity":  "1",
                "currency":  "KRW",
                "orderedAt": "2026-06-10T09:00:00+09:00",
                "execution": {"filledQuantity": "1", "averageFilledPrice": "100"},
            }
        ]

        async def _fake_orders(status="CLOSED", account_seq=None):
            return sell_only

        monkeypatch.setattr("kis_api.toss.fetch_toss_orders", _fake_orders)

        from kis_api.toss import sync_trades_from_toss
        r = await sync_trades_from_toss()
        assert r["ok"] is True
        assert r["sells_with_pnl"] == 0

        trades = self._read_trade_log()
        sell = next((t for t in trades if t.get("id") == "ORD_SELL_ONLY"), None)
        assert sell is not None
        # pnl 없거나 None — 0으로 채워지면 안 됨
        assert sell.get("pnl") is None, "buy 없는 sell에 pnl=0 주입됨 — 침묵-0 위반"
        assert sell.get("result") is None, "buy 없는 sell에 result 주입됨"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# ② 단위 테스트 — 장 캘린더
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# 실제 Toss 응답 구조를 모사 (KR, 영업일)
_KR_CAL_BUSINESS = {
    "result": {
        "today": {
            "date":          "2026-06-18",
            "regularMarket": {"open": "09:00", "close": "15:30"},
        },
        "previousBusinessDay": {"date": "2026-06-17"},
        "nextBusinessDay":     {"date": "2026-06-19"},
    }
}

_KR_CAL_HOLIDAY = {
    "result": {
        "today": {
            "date":      "2026-06-18",
            "isHoliday": True,
        },
        "previousBusinessDay": {"date": "2026-06-17"},
        "nextBusinessDay":     {"date": "2026-06-19"},
    }
}

# US 캘린더 (장 중)
_US_CAL_OPEN = {
    "result": {
        "today": {
            "date":          "2026-06-18",
            "regularMarket": {
                "open":  "2026-06-18T09:30:00-04:00",
                "close": "2026-06-18T16:00:00-04:00",
            },
        },
        "previousBusinessDay": {"date": "2026-06-17"},
        "nextBusinessDay":     {"date": "2026-06-19"},
    }
}

_US_CAL_HOLIDAY = {
    "result": {
        "today": {
            "date":      "2026-06-18",
            "isHoliday": True,
        },
        "previousBusinessDay": {"date": "2026-06-17"},
        "nextBusinessDay":     {"date": "2026-06-19"},
    }
}


class TestMarketCalendar:

    @pytest.mark.asyncio
    async def test_fetch_returns_result_dict(self, monkeypatch):
        async def _fake_toss_get(path, account_seq=None):
            return copy.deepcopy(_KR_CAL_BUSINESS)
        monkeypatch.setattr("kis_api.toss._toss_get", _fake_toss_get)

        from kis_api.toss import fetch_toss_market_calendar
        cal = await fetch_toss_market_calendar("KR")
        assert cal is not None
        assert "today" in cal

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_none(self, monkeypatch):
        async def _fake_toss_get(path, account_seq=None):
            return None
        monkeypatch.setattr("kis_api.toss._toss_get", _fake_toss_get)

        from kis_api.toss import fetch_toss_market_calendar
        cal = await fetch_toss_market_calendar("KR")
        assert cal is None

    @pytest.mark.asyncio
    async def test_is_kr_business_day_true(self, monkeypatch):
        async def _fake_cal(country):
            return copy.deepcopy(_KR_CAL_BUSINESS)["result"]
        monkeypatch.setattr("kis_api.toss.fetch_toss_market_calendar", _fake_cal)

        from kis_api.toss import is_kr_business_day_toss
        result = await is_kr_business_day_toss("2026-06-18")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_kr_business_day_holiday(self, monkeypatch):
        async def _fake_cal(country):
            return copy.deepcopy(_KR_CAL_HOLIDAY)["result"]
        monkeypatch.setattr("kis_api.toss.fetch_toss_market_calendar", _fake_cal)

        from kis_api.toss import is_kr_business_day_toss
        result = await is_kr_business_day_toss("2026-06-18")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_kr_business_day_api_failure_returns_none(self, monkeypatch):
        async def _fake_cal(country):
            return None
        monkeypatch.setattr("kis_api.toss.fetch_toss_market_calendar", _fake_cal)

        from kis_api.toss import is_kr_business_day_toss
        result = await is_kr_business_day_toss("2026-06-18")
        assert result is None, "API 실패 시 None이어야 함 (caller fallback 위해)"

    @pytest.mark.asyncio
    async def test_is_us_market_open_holiday(self, monkeypatch):
        async def _fake_cal(country):
            return copy.deepcopy(_US_CAL_HOLIDAY)["result"]
        monkeypatch.setattr("kis_api.toss.fetch_toss_market_calendar", _fake_cal)

        from kis_api.toss import is_us_market_open_now
        result = await is_us_market_open_now()
        assert result is False

    @pytest.mark.asyncio
    async def test_is_us_market_open_api_failure_returns_false(self, monkeypatch):
        async def _fake_cal(country):
            return None
        monkeypatch.setattr("kis_api.toss.fetch_toss_market_calendar", _fake_cal)

        from kis_api.toss import is_us_market_open_now
        result = await is_us_market_open_now()
        assert result is False, "API 실패 시 보수적으로 False여야 함"

    @pytest.mark.asyncio
    async def test_drift_detection_finds_discrepancy(self, monkeypatch):
        """심어 둔 불일치를 drift-detection이 발견하는지 확인.

        하드코딩에 "20260617" (수요일) 가 없지만 Toss가 휴장으로 반환하면
        toss_holidays_not_in_hardcoded 에 포함돼야 함.
        """
        # 2026-06-17 이 수요일 — Toss는 휴장, hardcoded에는 없음
        async def _fake_is_kr_business(date_str=None):
            if date_str == "2026-06-17":
                return False  # Toss says holiday
            return True  # all other days = business

        monkeypatch.setattr("kis_api.toss.is_kr_business_day_toss", _fake_is_kr_business)

        from kis_api.toss import check_kr_holiday_drift
        # narrow range: 2026-06-17 ~ 2026-06-18 only (patch timedelta to avoid many API calls)
        from datetime import date as _dt_date
        monkeypatch.setattr("kis_api.toss._date", type("_FakeDate", (), {
            "today": staticmethod(lambda: _dt_date(2026, 6, 17))
        }))
        # We re-import after patching _date — but since toss.py uses the module-level _date alias,
        # patch it directly in the module
        import kis_api.toss as _toss_mod
        monkeypatch.setattr(_toss_mod, "_date", type("_FakeDate", (), {
            "today": staticmethod(lambda: _dt_date(2026, 6, 17))
        }))

        result = await check_kr_holiday_drift()

        assert result["ok"] is True
        assert "20260617" in result["toss_holidays_not_in_hardcoded"], \
            "불일치가 감지되지 않음"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# ③ 단위 테스트 — 종목 경고
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_FAKE_WARNING_OBJ = {
    "type":    "ADMIN_DESIGNATED",
    "message": "관리종목 지정",
    "since":   "2026-06-01",
}


class TestStockWarnings:

    @pytest.mark.asyncio
    async def test_no_warnings_returns_empty_list(self, monkeypatch):
        async def _fake_toss_get(path, account_seq=None):
            return {"result": []}
        monkeypatch.setattr("kis_api.toss._toss_get", _fake_toss_get)

        from kis_api.toss import fetch_toss_stock_warnings
        result = await fetch_toss_stock_warnings("005930")
        assert result == []

    @pytest.mark.asyncio
    async def test_warned_stock_returns_raw_list(self, monkeypatch):
        async def _fake_toss_get(path, account_seq=None):
            return {"result": [_FAKE_WARNING_OBJ]}
        monkeypatch.setattr("kis_api.toss._toss_get", _fake_toss_get)

        from kis_api.toss import fetch_toss_stock_warnings
        result = await fetch_toss_stock_warnings("123456")
        assert len(result) == 1
        assert result[0] == _FAKE_WARNING_OBJ

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty_list_no_crash(self, monkeypatch):
        async def _fake_toss_get(path, account_seq=None):
            return None  # API 실패
        monkeypatch.setattr("kis_api.toss._toss_get", _fake_toss_get)

        from kis_api.toss import fetch_toss_stock_warnings
        result = await fetch_toss_stock_warnings("000660")
        assert result == [], "API 실패 시 빈 list여야 함 (crash 없이)"

    @pytest.mark.asyncio
    async def test_check_watch_warnings_collects_warned_only(self, monkeypatch, tmp_path):
        """경고 있는 종목만 반환, 경고 없는 종목은 제외."""
        # 포트폴리오 및 워치리스트 세팅
        portfolio = {
            "005930": {"name": "삼성전자", "qty": 10, "avg_price": 70000.0},
            "000660": {"name": "SK하이닉스", "qty": 5,  "avg_price": 150000.0},
            "us_stocks": {},
            "cash_krw": 0.0,
            "cash_usd": 0.0,
        }
        port_path = str(tmp_path / "portfolio.json")
        with open(port_path, "w", encoding="utf-8") as f:
            json.dump(portfolio, f, ensure_ascii=False)

        watchalert_path = str(tmp_path / "watchalert.json")
        with open(watchalert_path, "w", encoding="utf-8") as f:
            json.dump({
                "298040": {"name": "효성중공업", "market": "KR", "buy_price": 200000}
            }, f, ensure_ascii=False)

        import kis_api._config as _cfg
        monkeypatch.setattr(_cfg, "PORTFOLIO_FILE",  port_path)
        monkeypatch.setattr(_cfg, "WATCHALERT_FILE", watchalert_path)
        import kis_api._files as _files
        monkeypatch.setattr(_files, "WATCHALERT_FILE", watchalert_path, raising=False)
        monkeypatch.setattr(_files, "PORTFOLIO_FILE",  port_path,       raising=False)
        import kis_api.toss as _toss_mod
        monkeypatch.setattr(_toss_mod, "PORTFOLIO_FILE", port_path, raising=False)

        # 005930 = 경고 있음, 000660 + 298040 = 경고 없음
        async def _fake_warnings(symbol: str) -> list:
            if symbol == "005930":
                return [_FAKE_WARNING_OBJ]
            return []

        monkeypatch.setattr("kis_api.toss.fetch_toss_stock_warnings", _fake_warnings)

        from kis_api.toss import check_watch_warnings
        result = await check_watch_warnings()

        assert len(result) == 1
        assert result[0]["ticker"] == "005930"
        assert len(result[0]["warnings"]) == 1

    @pytest.mark.asyncio
    async def test_check_watch_warnings_no_crash_on_partial_failure(self, monkeypatch, tmp_path):
        """일부 종목에서 exception이 나도 전체 crash 없이 진행."""
        portfolio = {
            "005930": {"name": "삼성전자", "qty": 10, "avg_price": 70000.0},
            "000660": {"name": "SK하이닉스", "qty": 5,  "avg_price": 150000.0},
            "us_stocks": {},
            "cash_krw": 0.0,
            "cash_usd": 0.0,
        }
        port_path = str(tmp_path / "portfolio.json")
        with open(port_path, "w", encoding="utf-8") as f:
            json.dump(portfolio, f, ensure_ascii=False)

        watchalert_path = str(tmp_path / "watchalert.json")
        with open(watchalert_path, "w", encoding="utf-8") as f:
            json.dump({}, f)

        import kis_api._config as _cfg
        monkeypatch.setattr(_cfg, "PORTFOLIO_FILE",  port_path)
        monkeypatch.setattr(_cfg, "WATCHALERT_FILE", watchalert_path)
        import kis_api._files as _files
        monkeypatch.setattr(_files, "WATCHALERT_FILE", watchalert_path, raising=False)
        monkeypatch.setattr(_files, "PORTFOLIO_FILE",  port_path,       raising=False)
        import kis_api.toss as _toss_mod
        monkeypatch.setattr(_toss_mod, "PORTFOLIO_FILE", port_path, raising=False)

        async def _fake_warnings(symbol: str) -> list:
            if symbol == "005930":
                raise RuntimeError("network error")  # 일부 종목 에러
            return []

        monkeypatch.setattr("kis_api.toss.fetch_toss_stock_warnings", _fake_warnings)

        from kis_api.toss import check_watch_warnings
        result = await check_watch_warnings()  # 크래시 없어야 함
        # 에러 종목은 스킵, 정상 종목만
        assert isinstance(result, list)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 라이브 테스트 (--run-live)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.live
@pytest.mark.asyncio
async def test_live_fetch_toss_orders():
    """실계좌 CLOSED 주문 조회 — 최소 0건 이상 (빈 계좌도 list 반환)."""
    from kis_api.toss import fetch_toss_orders
    orders = await fetch_toss_orders(status="CLOSED")
    assert orders is not None, "fetch_toss_orders가 None — API 실패"
    assert isinstance(orders, list)
    print(f"[live] CLOSED 주문 {len(orders)}건")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_fetch_toss_market_calendar_kr():
    """KR 장 캘린더 — result dict 구조 확인."""
    from kis_api.toss import fetch_toss_market_calendar
    cal = await fetch_toss_market_calendar("KR")
    assert cal is not None, "KR 캘린더 None — API 실패"
    assert "today" in cal, f"today 키 없음: {list(cal.keys())}"
    print(f"[live] KR today={cal.get('today', {}).get('date')}")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_fetch_toss_market_calendar_us():
    """US 장 캘린더 — result dict 구조 확인."""
    from kis_api.toss import fetch_toss_market_calendar
    cal = await fetch_toss_market_calendar("US")
    assert cal is not None, "US 캘린더 None — API 실패"
    assert "today" in cal, f"today 키 없음: {list(cal.keys())}"
    print(f"[live] US today={cal.get('today', {}).get('date')}")
    # 실제 응답 구조 출력 (warning field 탐색용)
    print(f"[live] US today full: {cal.get('today')}")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_fetch_toss_stock_warnings_samsung():
    """삼성전자(005930) 경고 조회 — 빈 list 예상 (정상 종목)."""
    from kis_api.toss import fetch_toss_stock_warnings
    warnings = await fetch_toss_stock_warnings("005930")
    assert isinstance(warnings, list)
    print(f"[live] 005930 경고: {warnings}")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_check_watch_warnings():
    """실계좌 보유+워치 경고 점검."""
    from kis_api.toss import check_watch_warnings
    warned = await check_watch_warnings()
    assert isinstance(warned, list)
    print(f"[live] 경고 종목: {[w['ticker'] for w in warned]}")
