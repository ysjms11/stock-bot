"""tests/test_toss_ledger.py — toss_trades.json 전용 레저 단위 테스트.

단위 테스트 (live 마커 없음): 항상 실행.
라이브 테스트 (@pytest.mark.live): --run-live 플래그 시 실행.

핵심 보장:
1. FILLED 주문만 레저에 기록 (CANCELED/bad-price 스킵)
2. orderId dedup (재실행해도 추가 0건)
3. fetch None → toss_trades.json 미변경 (SAFETY GATE)
4. trade_log 완전 격리 (sync_toss_trade_ledger 호출 후 byte 동일)
5. get_toss_trade_summary — running-avg P&L 정확성, KRW/USD 분리
"""
import copy
import json
import os

import pytest


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 주문 픽스처 빌더
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _order(order_id, symbol, side, filled_qty, avg_price, currency="KRW",
           ordered_at="2026-06-01T09:30:00.000+09:00"):
    return {
        "orderId":   order_id,
        "symbol":    symbol,
        "side":      side,
        "status":    "CLOSED",
        "price":     str(avg_price),
        "quantity":  str(filled_qty),
        "currency":  currency,
        "orderedAt": ordered_at,
        "execution": {
            "filledQuantity":     str(filled_qty),
            "averageFilledPrice": str(avg_price),
        },
    }


def _canceled_order(order_id, symbol):
    """CANCELED — filledQuantity=0."""
    return {
        "orderId":   order_id,
        "symbol":    symbol,
        "side":      "BUY",
        "status":    "CLOSED",
        "price":     "1000",
        "quantity":  "10",
        "currency":  "KRW",
        "orderedAt": "2026-06-01T10:00:00.000+09:00",
        "execution": {
            "filledQuantity":     "0",
            "averageFilledPrice": "0",
        },
    }


def _bad_price_order(order_id, symbol):
    """filledQuantity > 0이지만 averageFilledPrice가 NaN (파싱 실패)."""
    return {
        "orderId":   order_id,
        "symbol":    symbol,
        "side":      "BUY",
        "status":    "CLOSED",
        "price":     "NaN",
        "quantity":  "5",
        "currency":  "KRW",
        "orderedAt": "2026-06-01T11:00:00.000+09:00",
        "execution": {
            "filledQuantity":     "5",
            "averageFilledPrice": "NaN",
        },
    }


# 표준 픽스처: BUY + SELL + CANCELED + bad-price
_FIXTURE_ORDERS = [
    _order("ORD-001", "005930", "BUY",  10, 70000, "KRW", "2026-05-01T09:30:00.000+09:00"),
    _order("ORD-002", "005930", "SELL",  5, 80000, "KRW", "2026-05-10T09:30:00.000+09:00"),
    _order("ORD-003", "NVDA",   "BUY",   3,   900, "USD", "2026-05-15T10:00:00.000+09:00"),
    _canceled_order("ORD-004", "005930"),
    _bad_price_order("ORD-005", "005935"),
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 픽스처: 임시 toss_trades.json + trade_log.json 경로
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def toss_ledger_paths(tmp_path, monkeypatch):
    """tmp_path에 toss_trades.json / trade_log.json 경로를 격리 설정."""
    toss_path = str(tmp_path / "toss_trades.json")
    trade_log_path = str(tmp_path / "trade_log.json")

    import kis_api._config as _cfg
    monkeypatch.setattr(_cfg, "TOSS_TRADES_FILE", toss_path)
    # toss.py 가 from ._config import TOSS_TRADES_FILE 로 가져오므로
    # 모듈 네임스페이스에도 반영
    import kis_api.toss as _toss
    monkeypatch.setattr(_toss, "TOSS_TRADES_FILE", toss_path, raising=False)

    return toss_path, trade_log_path


@pytest.fixture
def mock_fetch_orders(monkeypatch):
    """fetch_toss_orders를 교체 가능한 인터페이스 반환."""
    import kis_api.toss as _toss

    def _set(orders):
        async def _fake(status="CLOSED", account_seq=None):
            return orders
        monkeypatch.setattr(_toss, "fetch_toss_orders", _fake)

    return _set


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 1: FILLED만 레저에, CANCELED/bad-price 스킵
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_ledger_filled_only(toss_ledger_paths, mock_fetch_orders):
    """FILLED 주문(ORD-001,002,003)만 레저에 저장. CANCELED + bad-price 스킵."""
    toss_path, _ = toss_ledger_paths
    mock_fetch_orders(_FIXTURE_ORDERS)

    from kis_api.toss import sync_toss_trade_ledger
    result = await sync_toss_trade_ledger()

    assert result["ok"] is True
    assert result["added"] == 3      # ORD-001, 002, 003
    assert result["skipped_existing"] == 0

    data = json.loads(open(toss_path).read())
    trades = data["trades"]
    assert len(trades) == 3
    ids = {t["id"] for t in trades}
    assert "ORD-001" in ids
    assert "ORD-002" in ids
    assert "ORD-003" in ids
    assert "ORD-004" not in ids   # CANCELED
    assert "ORD-005" not in ids   # bad price


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 2: orderId dedup (재실행 = 추가 0건)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_ledger_dedup(toss_ledger_paths, mock_fetch_orders):
    """같은 주문 목록으로 두 번 실행 → 두 번째는 added=0."""
    toss_path, _ = toss_ledger_paths
    mock_fetch_orders(_FIXTURE_ORDERS)

    from kis_api.toss import sync_toss_trade_ledger

    r1 = await sync_toss_trade_ledger()
    assert r1["ok"] is True
    assert r1["added"] == 3

    r2 = await sync_toss_trade_ledger()
    assert r2["ok"] is True
    assert r2["added"] == 0
    assert r2["skipped_existing"] == 3  # 3 already in ledger

    data = json.loads(open(toss_path).read())
    assert len(data["trades"]) == 3


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 3: fetch None → toss_trades.json 미변경 (SAFETY GATE)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_ledger_fetch_none_no_write(toss_ledger_paths, mock_fetch_orders):
    """fetch_toss_orders가 None 반환 시 toss_trades.json을 생성/변경하지 않음."""
    toss_path, _ = toss_ledger_paths

    # 기존 레저에 1건 미리 기록
    pre_data = {"trades": [{"id": "OLD-001", "ticker": "005930", "side": "buy",
                             "qty": 10.0, "price": 70000.0, "date": "2026-01-01",
                             "ordered_at": "2026-01-01T09:30:00.000+09:00",
                             "currency": "KRW", "source": "toss", "name": "삼성전자"}]}
    with open(toss_path, "w", encoding="utf-8") as f:
        json.dump(pre_data, f)
    pre_bytes = open(toss_path, "rb").read()

    mock_fetch_orders(None)  # None = fetch 실패

    from kis_api.toss import sync_toss_trade_ledger
    result = await sync_toss_trade_ledger()

    assert result["ok"] is False
    assert result.get("reason") == "fetch_failed"

    # 파일 byte 동일 확인
    post_bytes = open(toss_path, "rb").read()
    assert post_bytes == pre_bytes, "fetch None 인데도 toss_trades.json이 변경됨!"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 4: trade_log 완전 격리 (sync 후 byte 동일)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_ledger_trade_log_untouched(toss_ledger_paths, mock_fetch_orders):
    """sync_toss_trade_ledger 실행 후 trade_log.json은 byte 동일."""
    toss_path, trade_log_path = toss_ledger_paths
    mock_fetch_orders(_FIXTURE_ORDERS)

    # 사전 기록: trade_log에 수동 거래 (64건 대표 1건)
    known_trade_log = {
        "trades": [
            {
                "id":     "MANUAL-001",
                "ticker": "005935",
                "name":   "삼성전자우",
                "side":   "buy",
                "qty":    5.0,
                "price":  65000.0,
                "date":   "2026-06-01",
                "grade":  "A",
                "memo":   "수동입력",
                "source": "manual",
            }
        ]
    }
    with open(trade_log_path, "w", encoding="utf-8") as f:
        json.dump(known_trade_log, f, ensure_ascii=False)
    pre_bytes = open(trade_log_path, "rb").read()

    from kis_api.toss import sync_toss_trade_ledger
    result = await sync_toss_trade_ledger()
    assert result["ok"] is True

    # trade_log.json byte 동일 확인
    post_bytes = open(trade_log_path, "rb").read()
    assert post_bytes == pre_bytes, (
        "sync_toss_trade_ledger이 trade_log.json을 변경했음 — 격리 위반!"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 5: get_toss_trade_summary — running-avg P&L
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def summary_ledger(toss_ledger_paths):
    """테스트용 레저 파일 기록 후 toss_path 반환."""
    toss_path, _ = toss_ledger_paths

    def _write(data):
        with open(toss_path, "w", encoding="utf-8") as f:
            json.dump({"trades": data}, f)
        return toss_path

    return _write


def test_summary_pnl_calculation(summary_ledger):
    """KRW 종목: BUY 10@70000 → SELL 5@80000 → 실현손익 +50000원."""
    summary_ledger([
        {"id": "ORD-001", "ticker": "005930", "side": "buy",  "qty": 10.0, "price": 70000.0,
         "date": "2026-05-01", "ordered_at": "2026-05-01T09:30:00.000+09:00",
         "currency": "KRW", "source": "toss", "name": "삼성전자"},
        {"id": "ORD-002", "ticker": "005930", "side": "sell", "qty": 5.0,  "price": 80000.0,
         "date": "2026-05-10", "ordered_at": "2026-05-10T09:30:00.000+09:00",
         "currency": "KRW", "source": "toss", "name": "삼성전자"},
        {"id": "ORD-003", "ticker": "NVDA",   "side": "buy",  "qty": 3.0,  "price": 900.0,
         "date": "2026-05-15", "ordered_at": "2026-05-15T10:00:00.000+09:00",
         "currency": "USD", "source": "toss", "name": "NVIDIA"},
    ])

    from kis_api.toss import get_toss_trade_summary
    summary = get_toss_trade_summary()

    assert summary["total_trades"] == 3

    # KRW 실현손익
    krw = summary["by_currency"].get("KRW", {})
    assert krw.get("win") == 1
    assert krw.get("loss") == 0
    assert abs(krw.get("realized_pnl", 0) - 50000.0) < 1.0

    # USD: NVDA 매도 없음
    usd = summary["by_currency"].get("USD", {})
    assert usd.get("win", 0) == 0

    # by_ticker KRW/005930
    krw_005930 = summary["by_ticker"].get("KRW/005930", {})
    assert krw_005930.get("realized_pnl") is not None
    assert abs(krw_005930["realized_pnl"] - 50000.0) < 1.0
    pnl_pct = krw_005930.get("pnl_pct")
    assert pnl_pct is not None
    assert abs(pnl_pct - 14.29) < 0.1   # (80000/70000-1)*100

    # USD/NVDA: open_qty=3, 매도 없어 pnl=None
    usd_nvda = summary["by_ticker"].get("USD/NVDA", {})
    assert usd_nvda.get("open_qty") == pytest.approx(3.0)
    assert usd_nvda.get("sell_count", 0) == 0


def test_summary_sell_before_buy_no_pnl(summary_ledger):
    """매수 기록 없이 매도만 → pnl=None (fabricate 금지)."""
    summary_ledger([
        {"id": "S-ONLY", "ticker": "AAPL", "side": "sell", "qty": 5.0, "price": 200.0,
         "date": "2026-05-01", "ordered_at": "2026-05-01T09:30:00.000+09:00",
         "currency": "USD", "source": "toss", "name": "Apple"},
    ])

    from kis_api.toss import get_toss_trade_summary
    summary = get_toss_trade_summary()

    aapl = summary["by_ticker"].get("USD/AAPL", {})
    assert aapl.get("realized_pnl") is None


def test_summary_krw_usd_separated(summary_ledger):
    """같은 ticker 'ABC'가 KRW / USD에 각각 존재 → 혼산 없이 분리 집계."""
    summary_ledger([
        {"id": "KR-BUY",  "ticker": "ABC", "side": "buy",  "qty": 10.0, "price": 1000.0,
         "date": "2026-05-01", "ordered_at": "2026-05-01T09:30:00.000+09:00",
         "currency": "KRW", "source": "toss", "name": "ABC-KR"},
        {"id": "KR-SELL", "ticker": "ABC", "side": "sell", "qty": 10.0, "price": 1200.0,
         "date": "2026-05-10", "ordered_at": "2026-05-10T09:30:00.000+09:00",
         "currency": "KRW", "source": "toss", "name": "ABC-KR"},
        {"id": "US-BUY",  "ticker": "ABC", "side": "buy",  "qty": 2.0,  "price": 50.0,
         "date": "2026-05-02", "ordered_at": "2026-05-02T10:00:00.000+09:00",
         "currency": "USD", "source": "toss", "name": "ABC-US"},
        {"id": "US-SELL", "ticker": "ABC", "side": "sell", "qty": 2.0,  "price": 40.0,
         "date": "2026-05-11", "ordered_at": "2026-05-11T10:00:00.000+09:00",
         "currency": "USD", "source": "toss", "name": "ABC-US"},
    ])

    from kis_api.toss import get_toss_trade_summary
    summary = get_toss_trade_summary()

    krw_abc = summary["by_ticker"].get("KRW/ABC", {})
    usd_abc = summary["by_ticker"].get("USD/ABC", {})

    # KRW: (1200-1000)*10 = +2000
    assert krw_abc.get("realized_pnl") is not None
    assert abs(krw_abc["realized_pnl"] - 2000.0) < 1.0

    # USD: (40-50)*2 = -20
    assert usd_abc.get("realized_pnl") is not None
    assert abs(usd_abc["realized_pnl"] - (-20.0)) < 0.1

    # 통화별 분리
    assert summary["by_currency"]["KRW"]["realized_pnl"] > 0
    assert summary["by_currency"]["USD"]["realized_pnl"] < 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 6: trade_log 심볼 미참조 — 소스 수준 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_toss_ledger_no_trade_log_imports():
    """sync_toss_trade_ledger / get_toss_trade_summary의 코드 본문(docstring 제외)에
    TRADE_LOG_FILE / load_trade_log / save_trade_log 가 없음을 검증.

    docstring에 설명으로 언급될 수 있으므로 ast 기반으로 실제 코드 노드만 검사.
    """
    import ast, textwrap, inspect
    from kis_api import toss as toss_mod

    def _code_without_docstring(fn) -> str:
        """함수 소스에서 첫 Expr(Constant) docstring 노드를 제거한 코드 반환."""
        src = textwrap.dedent(inspect.getsource(fn))
        tree = ast.parse(src)
        fn_node = tree.body[0]  # AsyncFunctionDef or FunctionDef
        body = fn_node.body
        # docstring = 첫 statement가 Expr(Constant str)이면 제거
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        # 남은 body를 unparse
        mod = ast.Module(body=body, type_ignores=[])
        return ast.unparse(mod)

    code_sync    = _code_without_docstring(toss_mod.sync_toss_trade_ledger)
    code_summary = _code_without_docstring(toss_mod.get_toss_trade_summary)
    combined = code_sync + code_summary

    forbidden = ["TRADE_LOG_FILE", "load_trade_log", "save_trade_log"]
    for sym in forbidden:
        assert sym not in combined, (
            f"sync_toss_trade_ledger 또는 get_toss_trade_summary 코드 본문이 {sym!r}을 참조함 — 격리 위반!"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 라이브 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.live
@pytest.mark.asyncio
async def test_live_sync_toss_trade_ledger():
    """실제 Toss API 호출 — 레저에 ~1900건 이상 기록되는지 확인."""
    from kis_api import sync_toss_trade_ledger, get_toss_trade_summary
    result = await sync_toss_trade_ledger()
    assert result.get("ok") is True, f"sync 실패: {result}"
    total = result.get("total", 0)
    assert total >= 100, f"레저 총 건수가 예상보다 적음: {total}"
    print(f"[live] toss_trades.json 총 {total}건 (추가 {result.get('added')}건)")

    summary = get_toss_trade_summary()
    assert isinstance(summary, dict)
    assert "by_currency" in summary
    assert "by_ticker" in summary
    assert summary["total_trades"] >= 100
    print(f"[live] 통화별 집계: {summary['by_currency']}")
