"""tests/test_toss_sync.py — 토스증권 잔고 동기화 단위 테스트.

단위 테스트 (live 마커 없음): 항상 실행
라이브 테스트 (@pytest.mark.live): --run-live 플래그 시 실행
"""
import copy
import json
import os
import pytest

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# fixture: 실제 Toss API 응답 구조를 미러한 더미 잔고
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_TOSS_FIXTURE = {
    "kr": {
        "005930": {"name": "삼성전자", "qty": 18,    "avg_price": 178100.0},
        "092780": {"name": "DYP",     "qty": 10247, "avg_price": 4544.0},
    },
    "us": {
        "NVDA": {"name": "NVIDIA",  "qty": 5,  "avg_price": 120.0},
        "AMZN": {"name": "Amazon",  "qty": 37, "avg_price": 262.58},
    },
    "account_seq": "12345",
    "raw_total":   {"krw": 99999999, "usd": 5000.0},
    "count":       4,
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _make_portfolio(tmp_path, data: dict) -> str:
    """tmp_path에 portfolio.json 생성 후 경로 반환."""
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 단위 테스트 — 파서
# ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestParser:
    """_parse_qty / _parse_price 단위 검증."""

    def test_qty_integer_string(self):
        from kis_api.toss import _parse_qty
        assert _parse_qty("18") == 18
        assert isinstance(_parse_qty("18"), int)

    def test_qty_fractional_string(self):
        from kis_api.toss import _parse_qty
        v = _parse_qty("10247")
        assert v == 10247
        assert isinstance(v, int)

    def test_qty_decimal(self):
        from kis_api.toss import _parse_qty
        v = _parse_qty("2.5")
        assert v == 2.5
        assert isinstance(v, float)

    def test_qty_bad_returns_none(self):
        from kis_api.toss import _parse_qty
        assert _parse_qty("") is None
        assert _parse_qty(None) is None
        assert _parse_qty("abc") is None

    def test_price_float(self):
        from kis_api.toss import _parse_price
        assert _parse_price("178100") == 178100.0
        assert _parse_price("4544.042158") == pytest.approx(4544.042158)

    def test_price_bad_returns_none(self):
        from kis_api.toss import _parse_price
        assert _parse_price(None) is None
        assert _parse_price("") is None
        assert _parse_price("n/a") is None

    # ── infinity / nan guard (IMPORTANT-1) ─────────────
    def test_qty_inf_returns_none(self):
        from kis_api.toss import _parse_qty
        assert _parse_qty("inf")  is None
        assert _parse_qty("-inf") is None
        assert _parse_qty("nan")  is None

    def test_price_inf_returns_none(self):
        from kis_api.toss import _parse_price
        assert _parse_price("inf")  is None
        assert _parse_price("-inf") is None
        assert _parse_price("nan")  is None

    def test_qty_valid_still_works_after_guard(self):
        from kis_api.toss import _parse_qty, _parse_price
        assert _parse_qty("18") == 18
        assert _parse_price("4544.04") == pytest.approx(4544.04)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 단위 테스트 — 동기화 로직
# ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSyncPortfolio:
    """sync_portfolio_from_toss 단위 검증 (monkeypatch)."""

    @pytest.fixture(autouse=True)
    def _patch_portfolio_file(self, tmp_path, monkeypatch):
        """conftest의 /tmp redirect 위에 추가로 PORTFOLIO_FILE을 tmp_path로 설정.
        fetch_toss_buying_power를 기본 None 반환으로 패치해 실API 호출 방지."""
        self._portfolio_path = str(tmp_path / "portfolio.json")
        monkeypatch.setattr("kis_api._config.PORTFOLIO_FILE", self._portfolio_path)
        monkeypatch.setattr("kis_api.toss.PORTFOLIO_FILE",    self._portfolio_path,
                            raising=False)
        # _config 내 상수도 패치 (toss.py가 from ._config import PORTFOLIO_FILE 사용)
        import kis_api._config as _cfg
        monkeypatch.setattr(_cfg, "PORTFOLIO_FILE", self._portfolio_path)
        # 기본: buying-power fetch → None (현금 보존; 개별 테스트에서 오버라이드 가능)
        async def _fake_buying_power_none(currency, account_seq=None):
            return None
        monkeypatch.setattr("kis_api.toss.fetch_toss_buying_power", _fake_buying_power_none)

    def _write_portfolio(self, data: dict):
        import json
        with open(self._portfolio_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def _read_portfolio(self) -> dict:
        import json
        with open(self._portfolio_path, encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture
    def _patch_holdings(self, monkeypatch):
        """fetch_toss_holdings를 Fixture 데이터 반환으로 교체."""
        fixture = copy.deepcopy(_TOSS_FIXTURE)
        async def _fake_holdings(account_seq=None):
            return copy.deepcopy(fixture)
        monkeypatch.setattr("kis_api.toss.fetch_toss_holdings", _fake_holdings)

    @pytest.fixture
    def _patch_holdings_none(self, monkeypatch):
        """fetch_toss_holdings가 None 반환 (API 실패 시뮬)."""
        async def _fake_holdings(account_seq=None):
            return None
        monkeypatch.setattr("kis_api.toss.fetch_toss_holdings", _fake_holdings)

    # ── 신규 삽입 ──────────────────────────────────────
    @pytest.mark.asyncio
    async def test_inserts_new_tickers(self, _patch_holdings):
        self._write_portfolio({"us_stocks": {}, "cash_krw": 1000000.0, "cash_usd": 0.0})
        from kis_api.toss import sync_portfolio_from_toss
        r = await sync_portfolio_from_toss()
        assert r["ok"] is True
        port = self._read_portfolio()
        # KR 종목 추가됨
        assert "005930" in port
        assert port["005930"]["qty"] == 18
        assert port["005930"]["avg_price"] == 178100.0
        # US 종목 추가됨
        assert "NVDA" in port["us_stocks"]
        assert port["us_stocks"]["NVDA"]["qty"] == 5

    # ── qty/avg_price 갱신, memo 필드 보존 ──────────────
    @pytest.mark.asyncio
    async def test_updates_and_preserves_memo(self, _patch_holdings):
        self._write_portfolio({
            "005930": {
                "name": "삼성전자", "qty": 10, "avg_price": 170000.0,
                "memo": "장기보유",  # bot-only 필드
                "grade": "A",
            },
            "us_stocks": {},
            "cash_krw": 1000000.0,
            "cash_usd": 0.0,
        })
        from kis_api.toss import sync_portfolio_from_toss
        r = await sync_portfolio_from_toss()
        assert r["ok"] is True
        port = self._read_portfolio()
        entry = port["005930"]
        # Toss 데이터로 갱신됨
        assert entry["qty"] == 18
        assert entry["avg_price"] == 178100.0
        # 기존 bot-only 필드 보존
        assert entry.get("memo") == "장기보유"
        assert entry.get("grade") == "A"
        # toss_missing 플래그 없음
        assert "toss_missing" not in entry

    # ── portfolio에 있지만 Toss에 없는 종목 → toss_missing 플래그 ──
    @pytest.mark.asyncio
    async def test_flags_missing_not_deletes(self, _patch_holdings):
        self._write_portfolio({
            "999999": {"name": "유령종목", "qty": 5, "avg_price": 10000.0},
            "us_stocks": {"GHOST": {"name": "Ghost Inc", "qty": 1, "avg_price": 50.0}},
            "cash_krw": 0.0,
            "cash_usd": 0.0,
        })
        from kis_api.toss import sync_portfolio_from_toss
        r = await sync_portfolio_from_toss()
        assert r["ok"] is True
        port = self._read_portfolio()
        # 삭제 안 되고 남아 있음
        assert "999999" in port
        assert port["999999"]["qty"] == 5
        # toss_missing 플래그가 붙음
        assert port["999999"].get("toss_missing") is True
        # US도 동일
        assert "GHOST" in port["us_stocks"]
        assert port["us_stocks"]["GHOST"].get("toss_missing") is True
        # flagged_missing 리스트에 포함
        assert "999999" in r["flagged_missing"]
        assert "GHOST" in r["flagged_missing"]

    # ── cash_krw/cash_usd 불변 ──────────────────────────
    @pytest.mark.asyncio
    async def test_cash_untouched(self, _patch_holdings):
        self._write_portfolio({
            "us_stocks": {}, "cash_krw": 2565614.0, "cash_usd": 1.08
        })
        from kis_api.toss import sync_portfolio_from_toss
        await sync_portfolio_from_toss()
        port = self._read_portfolio()
        assert port["cash_krw"] == 2565614.0
        assert port["cash_usd"] == 1.08

    # ── SAFETY GATE: fetch=None → portfolio.json 바이트 불변 ──
    @pytest.mark.asyncio
    async def test_safety_gate_no_write_on_none(self, _patch_holdings_none):
        initial = {"us_stocks": {}, "cash_krw": 1000.0, "cash_usd": 0.0}
        self._write_portfolio(initial)
        original_bytes = open(self._portfolio_path, "rb").read()

        from kis_api.toss import sync_portfolio_from_toss
        r = await sync_portfolio_from_toss()
        assert r["ok"] is False
        assert r["reason"] == "fetch_failed"
        # 파일 내용 바이트-동일
        assert open(self._portfolio_path, "rb").read() == original_bytes

    # ── toss_missing 재등장 시 플래그 제거 ─────────────
    @pytest.mark.asyncio
    async def test_missing_flag_cleared_on_reappear(self, _patch_holdings):
        # 005930이 이전 sync에서 toss_missing=True 였던 상태
        self._write_portfolio({
            "005930": {
                "name": "삼성전자", "qty": 10, "avg_price": 170000.0,
                "toss_missing": True,
            },
            "us_stocks": {},
            "cash_krw": 0.0, "cash_usd": 0.0,
        })
        from kis_api.toss import sync_portfolio_from_toss
        r = await sync_portfolio_from_toss()
        assert r["ok"] is True
        port = self._read_portfolio()
        # Toss fixture에 005930이 있으므로 플래그 제거
        assert "toss_missing" not in port["005930"]
        # flagged_missing에 없어야 함
        assert "005930" not in r["flagged_missing"]

    # ── 현금 동기화 — fetch 성공 시 갱신, 실패 시 보존 ──
    @pytest.mark.asyncio
    async def test_cash_synced_when_buying_power_available(self, _patch_holdings, monkeypatch):
        """fetch_toss_buying_power가 값을 반환하면 cash_krw/cash_usd가 갱신됨."""
        self._write_portfolio({
            "us_stocks": {}, "cash_krw": 0.0, "cash_usd": 0.0,
        })

        async def _fake_buying_power(currency, account_seq=None):
            return 2445478.0 if currency == "KRW" else 83.5

        monkeypatch.setattr("kis_api.toss.fetch_toss_buying_power", _fake_buying_power)

        from kis_api.toss import sync_portfolio_from_toss
        r = await sync_portfolio_from_toss()
        assert r["ok"] is True
        port = self._read_portfolio()
        assert port["cash_krw"] == 2445478.0
        assert port["cash_usd"] == 83.5
        # 반환 dict에도 포함
        assert r["cash_krw"] == 2445478.0
        assert r["cash_usd"] == 83.5

    @pytest.mark.asyncio
    async def test_cash_preserved_when_buying_power_fails(self, _patch_holdings):
        """fetch_toss_buying_power가 None 반환하면 기존 cash 값이 보존됨 (기본 autouse 패치)."""
        self._write_portfolio({
            "us_stocks": {}, "cash_krw": 2565614.0, "cash_usd": 1.08,
        })
        from kis_api.toss import sync_portfolio_from_toss
        r = await sync_portfolio_from_toss()
        assert r["ok"] is True
        port = self._read_portfolio()
        # None 반환이므로 기존 값 그대로
        assert port["cash_krw"] == 2565614.0
        assert port["cash_usd"] == 1.08
        # 반환 dict에는 None
        assert r["cash_krw"] is None
        assert r["cash_usd"] is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# NIT-3: raw HTTP shape 테스트 — _toss_get 레이어 mocking
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# 실제 Toss API wire 형식을 미러한 payload
_WIRE_PAYLOAD = {
    "result": {
        "items": [
            {
                "symbol": "005930",
                "name": "삼성전자",
                "marketCountry": "KR",
                "currency": "KRW",
                "quantity": "18",
                "averagePurchasePrice": "178100.0",
                "marketValue": "179100",
            },
            {
                "symbol": "NVDA",
                "name": "NVIDIA",
                "marketCountry": "US",
                "currency": "USD",
                "quantity": "5",
                "averagePurchasePrice": "120.0",
                "marketValue": "134.0",
            },
            # bad item: quantity="inf" — 이 종목은 스킵돼야 함
            {
                "symbol": "BADSTOCK",
                "name": "Bad Corp",
                "marketCountry": "US",
                "currency": "USD",
                "quantity": "inf",
                "averagePurchasePrice": "50.0",
                "marketValue": "55.0",
            },
            # unknown marketCountry — 스킵돼야 함
            {
                "symbol": "9988",
                "name": "Alibaba HK",
                "marketCountry": "HK",
                "currency": "HKD",
                "quantity": "10",
                "averagePurchasePrice": "88.0",
                "marketValue": "90.0",
            },
        ],
        "totalPurchaseAmount": {"krw": 3205800, "usd": 600.0},
    }
}


class TestRawHttpShape:
    """_toss_get HTTP 레이어를 직접 mock해 items[]→parse 경로 검증."""

    @pytest.mark.asyncio
    async def test_inf_item_skipped_valid_items_parsed(self, monkeypatch):
        """inf quantity 종목은 스킵, 정상 KR/US 종목은 파싱 성공."""
        import copy

        async def _fake_toss_get(path, account_seq=None):
            # /accounts → 계좌 목록, /holdings → 보유종목 wire payload
            if path == "/api/v1/accounts":
                return {"result": [{"accountSeq": "99999"}]}
            if path == "/api/v1/holdings":
                return copy.deepcopy(_WIRE_PAYLOAD)
            return None

        monkeypatch.setattr("kis_api.toss._toss_get", _fake_toss_get)

        from kis_api.toss import fetch_toss_holdings
        result = await fetch_toss_holdings()

        assert result is not None, "fetch_toss_holdings가 None 반환 — crash 의심"

        kr = result["kr"]
        us = result["us"]

        # 정상 KR 종목 파싱
        assert "005930" in kr
        assert kr["005930"]["qty"] == 18
        assert kr["005930"]["avg_price"] == pytest.approx(178100.0)

        # 정상 US 종목 파싱
        assert "NVDA" in us
        assert us["NVDA"]["qty"] == 5

        # inf quantity 종목은 결과에 없어야 함 (크래시 없이 스킵)
        assert "BADSTOCK" not in us, "inf quantity 종목이 us에 포함됨 — 침묵-0 규칙 위반"

        # unknown marketCountry("HK") 종목도 결과에 없어야 함
        assert "9988" not in kr and "9988" not in us, "HK 종목이 잘못 분류됨"

        # count는 정상 파싱된 종목수 (KR 1 + US 1 = 2)
        assert result["count"] == 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# account_seq 자동 해석 — fetch_toss_buying_power 단위 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_BUYING_POWER_PAYLOAD = {
    "result": {
        "cashBuyingPower": "2445478",
        "currency": "KRW",
    }
}


class TestBuyingPowerAutoResolve:
    """fetch_toss_buying_power — account_seq 자동 해석 경로 검증."""

    @pytest.mark.asyncio
    async def test_auto_resolves_account_seq_when_none(self, monkeypatch):
        """account_seq 미지정 시 fetch_toss_accounts()에서 첫 계좌 accountSeq를 취득해 API 호출."""
        import copy

        accounts_called = []
        toss_get_calls = []

        async def _fake_accounts():
            accounts_called.append(True)
            return [{"accountSeq": "99999", "accountType": "NORMAL"}]

        async def _fake_toss_get(path, account_seq=None):
            toss_get_calls.append((path, account_seq))
            if "buying-power" in path:
                return copy.deepcopy(_BUYING_POWER_PAYLOAD)
            return None

        monkeypatch.setattr("kis_api.toss.fetch_toss_accounts", _fake_accounts)
        monkeypatch.setattr("kis_api.toss._toss_get", _fake_toss_get)

        from kis_api.toss import fetch_toss_buying_power
        result = await fetch_toss_buying_power("KRW")

        # 계좌 자동 조회됐음
        assert accounts_called, "fetch_toss_accounts가 호출되지 않음 — 자동 해석 누락"
        # _toss_get 호출됐음 (buying-power 경로)
        assert any("buying-power" in p for p, _ in toss_get_calls), "_toss_get buying-power 호출 없음"
        # account_seq가 "99999"로 전달됐음
        bp_call = next((ac for p, ac in toss_get_calls if "buying-power" in p), None)
        assert bp_call == "99999", f"account_seq mismatch: {bp_call!r}"
        # 현금값이 올바르게 파싱됨
        assert result == pytest.approx(2445478.0), f"cash 파싱 실패: {result!r}"

    @pytest.mark.asyncio
    async def test_no_accounts_returns_none(self, monkeypatch):
        """계좌 목록이 빈 리스트이면 None 반환 (헤더 없는 요청 방지)."""
        async def _fake_accounts():
            return []

        toss_get_called = []
        async def _fake_toss_get(path, account_seq=None):
            toss_get_called.append(path)
            return None

        monkeypatch.setattr("kis_api.toss.fetch_toss_accounts", _fake_accounts)
        monkeypatch.setattr("kis_api.toss._toss_get", _fake_toss_get)

        from kis_api.toss import fetch_toss_buying_power
        result = await fetch_toss_buying_power("KRW")

        assert result is None
        # 계좌 없으면 buying-power GET을 시도하지 않아야 함
        assert not any("buying-power" in p for p in toss_get_called), \
            "계좌 없는데 buying-power GET 시도됨"

    @pytest.mark.asyncio
    async def test_provided_account_seq_skips_accounts_call(self, monkeypatch):
        """account_seq를 명시 전달하면 fetch_toss_accounts()를 호출하지 않음 (프로덕션 경로)."""
        import copy

        accounts_called = []
        async def _fake_accounts():
            accounts_called.append(True)
            return [{"accountSeq": "00000"}]

        async def _fake_toss_get(path, account_seq=None):
            if "buying-power" in path:
                return copy.deepcopy(_BUYING_POWER_PAYLOAD)
            return None

        monkeypatch.setattr("kis_api.toss.fetch_toss_accounts", _fake_accounts)
        monkeypatch.setattr("kis_api.toss._toss_get", _fake_toss_get)

        from kis_api.toss import fetch_toss_buying_power
        result = await fetch_toss_buying_power("KRW", account_seq="12345")

        # fetch_toss_accounts 호출 없어야 함 (account_seq 이미 있음)
        assert not accounts_called, "account_seq 제공됐는데 fetch_toss_accounts 호출됨"
        assert result == pytest.approx(2445478.0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 라이브 테스트 (--run-live 시 실행)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.live
@pytest.mark.asyncio
async def test_live_fetch_toss_holdings():
    """실계좌 잔고 조회: KR 보유 ≥1건, qty>0 확인."""
    from kis_api.toss import fetch_toss_holdings
    holdings = await fetch_toss_holdings()
    assert holdings is not None, "holdings가 None — API 실패 또는 크레덴셜 미설정"
    kr = holdings.get("kr", {})
    us = holdings.get("us", {})
    total = len(kr) + len(us)
    assert total >= 1, f"보유종목 0건 — kr={kr}, us={us}"
    # KR 종목 있으면 qty 확인
    for ticker, entry in kr.items():
        assert entry["qty"] > 0, f"{ticker} qty={entry['qty']} (non-positive)"
        assert isinstance(entry["avg_price"], (int, float))
