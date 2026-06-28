"""토스증권 Open API — 읽기전용 잔고 동기화 + 체결이력 + 장 캘린더 + 종목 경고.

엔드포인트:
  POST /oauth2/token                        → Bearer 토큰 (client_credentials)
  GET  /api/v1/accounts                     → 계좌 목록
  GET  /api/v1/holdings                     → 보유종목 (X-Tossinvest-Account 헤더 필요)
  GET  /api/v1/buying-power?currency=KRW|USD → 현금 매수가능금액 (예수금)
  GET  /api/v1/orders?status=CLOSED         → 체결이력 (페이지네이션)
  GET  /api/v1/market-calendar/{country}    → 장 운영 캘린더 (KR/US)
  GET  /api/v1/stocks/{symbol}/warnings     → 종목 경고 목록
"""
import asyncio
import math
import os
from datetime import date as _date
from datetime import datetime, timedelta

import aiohttp
from dotenv import load_dotenv

load_dotenv(override=True)

TOSS_BASE = "https://openapi.tossinvest.com"
_TOSS_CLIENT_ID     = os.environ.get("TOSS_CLIENT_ID", "")
_TOSS_CLIENT_SECRET = os.environ.get("TOSS_CLIENT_SECRET", "")

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 토큰 메모리 캐시 (expires_in 기반 — 60s 안전 마진)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_toss_token_cache: dict = {"token": None, "expires": None}


async def get_toss_token() -> str:
    """OAuth2 client_credentials 토큰 반환 (메모리 캐시, 만료 60s 전 갱신)."""
    now = datetime.now()
    if (_toss_token_cache["token"]
            and _toss_token_cache["expires"]
            and _toss_token_cache["expires"] > now):
        return _toss_token_cache["token"]

    if not _TOSS_CLIENT_ID or not _TOSS_CLIENT_SECRET:
        print("[toss] TOSS_CLIENT_ID/SECRET 미설정 — 토큰 발급 불가")
        return ""

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(
            f"{TOSS_BASE}/oauth2/token",
            data={
                "grant_type":    "client_credentials",
                "client_id":     _TOSS_CLIENT_ID,
                "client_secret": _TOSS_CLIENT_SECRET,
            },
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                print(f"[toss] 토큰 발급 실패 HTTP {resp.status}: {body[:200]}")
                return ""
            data = await resp.json(content_type=None)

    token = data.get("access_token", "")
    if not token:
        print(f"[toss] 토큰 필드 없음: {list(data.keys())}")
        return ""

    expires_in = int(data.get("expires_in", 86399))
    _toss_token_cache["token"] = token
    _toss_token_cache["expires"] = now + timedelta(seconds=expires_in - 60)
    return token


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 내부 GET 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def _toss_get(path: str, account_seq=None) -> dict | None:
    """Toss API GET. 성공 시 JSON dict 반환, 실패 시 None (절대 raise 안 함)."""
    token = await get_toss_token()
    if not token:
        return None
    headers: dict = {"Authorization": f"Bearer {token}"}
    if account_seq is not None:
        headers["X-Tossinvest-Account"] = str(account_seq)
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(f"{TOSS_BASE}{path}", headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"[toss] GET {path} HTTP {resp.status}: {body[:200]}")
                    return None
                return await resp.json(content_type=None)
    except Exception as e:
        print(f"[toss] GET {path} 오류: {type(e).__name__}: {e}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 계좌 목록
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def fetch_toss_accounts() -> list:
    """GET /api/v1/accounts → result 리스트 반환 (실패 시 [])."""
    data = await _toss_get("/api/v1/accounts")
    if data is None:
        return []
    result = data.get("result", [])
    if not isinstance(result, list):
        print(f"[toss] accounts result 비정상 타입: {type(result)}")
        return []
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 현금 매수가능금액 (예수금)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def fetch_toss_buying_power(currency: str, account_seq=None) -> float | None:
    """GET /api/v1/buying-power?currency={KRW|USD} → cashBuyingPower float.

    반환값은 현금 매수가능금액(예수금)으로, D+2 미수금이 포함된 값이라 출금가능액과
    미세 차이가 있을 수 있음.

    account_seq 미지정 시 fetch_toss_accounts()에서 result[0].accountSeq 자동 해석.
    계좌 조회 실패 또는 accountSeq 없으면 None 반환 (X-Tossinvest-Account 없이 호출하면 HTTP 400).
    응답 None / 키 없음 / 파싱 실패 → None 반환 (침묵-0 금지; 호출측이 None 가드).
    """
    if account_seq is None:
        accounts = await fetch_toss_accounts()
        if not accounts:
            print("[toss-bp] 계좌 목록 조회 실패 — buying-power 중단")
            return None
        account_seq = accounts[0].get("accountSeq")
        if not account_seq:
            print("[toss-bp] accountSeq 없음 — buying-power 중단")
            return None

    currency = currency.upper()
    data = await _toss_get(f"/api/v1/buying-power?currency={currency}", account_seq=account_seq)
    if data is None:
        return None
    result = data.get("result")
    if not isinstance(result, dict):
        print(f"[toss-bp] buying-power result 비정상 타입: {type(result)}")
        return None
    raw = result.get("cashBuyingPower")
    if raw is None:
        print(f"[toss-bp] cashBuyingPower 필드 없음 (currency={currency}): {list(result.keys())}")
        return None
    parsed = _parse_price(raw)
    if parsed is None:
        print(f"[toss-bp] cashBuyingPower 파싱 실패 (raw={raw!r}) — 침묵-0 금지")
    return parsed


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 보유종목 조회 + 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_qty(raw) -> int | float | None:
    """수량 문자열을 int(정수) 또는 float(소수) 로 파싱. 실패 시 None."""
    try:
        f = float(str(raw).strip())
        if not math.isfinite(f):  # NaN / inf / -inf guard
            return None
        return int(f) if f == int(f) else f
    except (ValueError, TypeError, OverflowError):
        return None


def _parse_price(raw) -> float | None:
    """가격 문자열을 float로 파싱. 실패 시 None."""
    try:
        f = float(str(raw).strip())
        if not math.isfinite(f):  # NaN / inf / -inf guard
            return None
        return f
    except (ValueError, TypeError, OverflowError):
        return None


async def fetch_toss_holdings(account_seq=None) -> dict | None:
    """Toss 보유종목 조회.

    account_seq 미지정 시 fetch_toss_accounts()에서 result[0].accountSeq 사용.

    반환: {"kr": {ticker: {name, qty, avg_price}},
            "us": {ticker: {name, qty, avg_price}},
            "account_seq": ...,
            "raw_total": {...},
            "count": N}
    실패(API 오류) 시 None.
    """
    if account_seq is None:
        accounts = await fetch_toss_accounts()
        if not accounts:
            print("[toss] 계좌 목록 조회 실패 — holdings 중단")
            return None
        account_seq = accounts[0].get("accountSeq")
        if not account_seq:
            print("[toss] accountSeq 없음 — holdings 중단")
            return None

    data = await _toss_get("/api/v1/holdings", account_seq=account_seq)
    if data is None:
        return None

    result = data.get("result")
    if not isinstance(result, dict):
        print(f"[toss] holdings result 비정상 타입: {type(result)}")
        return None

    raw_items = result.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []

    kr: dict = {}
    us: dict = {}
    skipped = 0

    for item in raw_items:
        symbol = str(item.get("symbol", "")).strip()
        name   = str(item.get("name", symbol)).strip()
        market = str(item.get("marketCountry", "")).upper()

        qty = _parse_qty(item.get("quantity"))
        avg = _parse_price(item.get("averagePurchasePrice"))

        if not symbol:
            skipped += 1
            continue
        if qty is None:
            print(f"[toss] {symbol} qty 파싱 실패 (raw={item.get('quantity')!r}) — 스킵 (침묵-0 금지)")
            skipped += 1
            continue
        if avg is None:
            print(f"[toss] {symbol} avg_price 파싱 실패 (raw={item.get('averagePurchasePrice')!r}) — 스킵 (침묵-0 금지)")
            skipped += 1
            continue

        entry = {"name": name, "qty": qty, "avg_price": avg}
        if market == "KR":
            kr[symbol] = entry
        elif market == "US":
            us[symbol] = entry
        else:
            print(f"[toss] {symbol} 알 수 없는 marketCountry={market!r} — 스킵")
            skipped += 1
            continue

    total = len(kr) + len(us)
    if skipped:
        print(f"[toss] 스킵된 종목: {skipped}건")

    return {
        "kr":          kr,
        "us":          us,
        "account_seq": account_seq,
        "raw_total":   result.get("totalPurchaseAmount"),
        "count":       total,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 포트폴리오 동기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def sync_portfolio_from_toss() -> dict:
    """Toss 잔고 → portfolio.json 머지.

    머지 정책:
    - Toss KR/US 보유 → update/insert (name, qty, avg_price 갱신; 기존 bot-only 필드 보존)
    - portfolio.json에 있지만 Toss에 없는 종목 → toss_missing=True 플래그만 (삭제 안 함)
    - 재등장 시 toss_missing 플래그 제거
    - cash_krw/cash_usd → buying-power API(예수금)로 동기화; fetch 실패 시 기존 값 보존

    반환: {"ok": True/False, "cash_krw": float|None, "cash_usd": float|None, ...}
    """
    from ._config import PORTFOLIO_FILE
    from ._files import load_json, save_json

    holdings = await fetch_toss_holdings()

    # ── SAFETY GATE ──────────────────────────────────────
    if holdings is None:
        print("[toss-sync] fetch 실패 — 포트폴리오 미변경")
        return {"ok": False, "reason": "fetch_failed"}

    portfolio = load_json(PORTFOLIO_FILE, {})

    _META = {"us_stocks", "cash_krw", "cash_usd"}
    toss_kr: dict = holdings["kr"]
    toss_us: dict = holdings["us"]

    added_kr:   list = []
    updated_kr: list = []
    added_us:   list = []
    updated_us: list = []
    flagged:    list = []

    # ── KR 머지 ──────────────────────────────────────────
    for ticker, toss_entry in toss_kr.items():
        existing = portfolio.get(ticker)
        if existing is None or ticker in _META:
            # 신규
            portfolio[ticker] = dict(toss_entry)
            portfolio[ticker].pop("toss_missing", None)
            added_kr.append(ticker)
        else:
            # 기존 bot-only 필드 보존하며 갱신
            existing["name"]      = toss_entry["name"]
            existing["qty"]       = toss_entry["qty"]
            existing["avg_price"] = toss_entry["avg_price"]
            existing.pop("toss_missing", None)
            updated_kr.append(ticker)

    # ── US 머지 ──────────────────────────────────────────
    us_section = portfolio.setdefault("us_stocks", {})
    for ticker, toss_entry in toss_us.items():
        existing = us_section.get(ticker)
        if existing is None:
            us_section[ticker] = dict(toss_entry)
            us_section[ticker].pop("toss_missing", None)
            added_us.append(ticker)
        else:
            existing["name"]      = toss_entry["name"]
            existing["qty"]       = toss_entry["qty"]
            existing["avg_price"] = toss_entry["avg_price"]
            existing.pop("toss_missing", None)
            updated_us.append(ticker)

    # ── toss_missing 플래그 — portfolio에 있지만 Toss에 없는 종목 ──
    for ticker in list(portfolio.keys()):
        if ticker in _META:
            continue
        if ticker not in toss_kr:
            portfolio[ticker]["toss_missing"] = True
            flagged.append(ticker)

    for ticker in list(us_section.keys()):
        if ticker not in toss_us:
            us_section[ticker]["toss_missing"] = True
            if ticker not in flagged:
                flagged.append(ticker)

    # ── 현금 동기화 (buying-power API) ───────────────────
    account_seq = holdings.get("account_seq")
    bp_krw = await fetch_toss_buying_power("KRW", account_seq=account_seq)
    await asyncio.sleep(0.3)  # rate limit — 429 on rapid calls
    bp_usd = await fetch_toss_buying_power("USD", account_seq=account_seq)
    if bp_krw is not None:
        portfolio["cash_krw"] = bp_krw
    if bp_usd is not None:
        portfolio["cash_usd"] = bp_usd

    save_json(PORTFOLIO_FILE, portfolio)

    kr_count = len(toss_kr)
    us_count = len(toss_us)
    cash_log = (
        f", KRW현금={bp_krw} USD현금={bp_usd}"
        if (bp_krw is not None or bp_usd is not None) else " (현금 fetch 실패 — 기존 보존)"
    )
    print(
        f"[toss-sync] 완료 — KR {kr_count}종목 (추가 {len(added_kr)}, 갱신 {len(updated_kr)}), "
        f"US {us_count}종목 (추가 {len(added_us)}, 갱신 {len(updated_us)}), "
        f"미관찰 플래그 {len(flagged)}종목{cash_log}"
    )
    return {
        "ok":               True,
        "added":            added_kr + added_us,
        "updated":          updated_kr + updated_us,
        "flagged_missing":  flagged,
        "kr_count":         kr_count,
        "us_count":         us_count,
        "cash_krw":         bp_krw,
        "cash_usd":         bp_usd,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# FEATURE ① — 체결이력 조회 + trade_log 자동화
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def fetch_toss_orders(status: str = "CLOSED", account_seq=None) -> list | None:
    """GET /api/v1/orders?status={status} (페이지네이션) — 전체 주문 목록 반환.

    status: "OPEN" 또는 "CLOSED" (CLOSED = 종료된 주문)
    account_seq: 미지정 시 fetch_toss_accounts()에서 첫 번째 계좌 사용.
    반환: list of order dicts. 실패(API 오류) 시 None (빈 리스트와 구분).
    페이지네이션: response.result.hasNext==True 이면 nextCursor를 cursor= 파라미터로 전달.
    """
    if account_seq is None:
        accounts = await fetch_toss_accounts()
        if not accounts:
            print("[toss-orders] 계좌 목록 조회 실패")
            return None
        account_seq = accounts[0].get("accountSeq")
        if not account_seq:
            print("[toss-orders] accountSeq 없음")
            return None

    token = await get_toss_token()
    if not token:
        return None

    headers = {
        "Authorization":       f"Bearer {token}",
        "X-Tossinvest-Account": str(account_seq),
    }

    all_orders: list = []
    cursor: str | None = None
    page = 0

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            while True:
                params: dict = {"status": status}
                if cursor:
                    params["cursor"] = cursor

                async with s.get(
                    f"{TOSS_BASE}/api/v1/orders",
                    headers=headers,
                    params=params,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        print(f"[toss-orders] HTTP {resp.status} (page={page}): {body[:200]}")
                        return None
                    data = await resp.json(content_type=None)

                result = data.get("result", {})
                if not isinstance(result, dict):
                    print(f"[toss-orders] result 비정상 타입: {type(result)}")
                    return None

                orders = result.get("orders", [])
                if isinstance(orders, list):
                    all_orders.extend(orders)

                has_next  = result.get("hasNext", False)
                next_cur  = result.get("nextCursor")
                page += 1

                if not has_next or not next_cur:
                    break

                cursor = next_cur
                await asyncio.sleep(0.3)  # rate limit — 429 on rapid calls

    except Exception as e:
        print(f"[toss-orders] 오류: {type(e).__name__}: {e}")
        return None

    print(f"[toss-orders] status={status} 총 {len(all_orders)}건 (pages={page})")
    return all_orders


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# FEATURE ① — 별도 toss_trades.json 레저 (trade_log 완전 분리)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def sync_toss_trade_ledger() -> dict:
    """Toss CLOSED 주문 → data/toss_trades.json 전용 레저 (trade_log 불간섭).

    정책:
    - status=CLOSED 주문 조회; 조회 실패(None) → toss_trades.json 미변경
    - execution.filledQuantity > 0인 것만 (CANCELED 스킵)
    - orderId로 dedup (append-only, 기존 항목 보존)
    - 최대 5000건 보관 (ordered_at 정렬)
    - trade_log / TRADE_LOG_FILE 를 절대 건드리지 않음

    반환: {"ok": bool, "added": int, "total": int, "skipped_existing": int}
    """
    from ._config import TOSS_TRADES_FILE
    from ._files import load_json, save_json

    orders = await fetch_toss_orders(status="CLOSED")

    # SAFETY GATE — fetch 실패 시 toss_trades.json 미변경
    if orders is None:
        return {"ok": False, "reason": "fetch_failed"}

    # 기존 레저 로드 (default: {"trades": []})
    existing_data = load_json(TOSS_TRADES_FILE, {"trades": []})
    if not isinstance(existing_data, dict):
        existing_data = {"trades": []}
    existing_trades: list = existing_data.get("trades", [])
    if not isinstance(existing_trades, list):
        existing_trades = []

    existing_ids: set = {str(t.get("id", "")) for t in existing_trades if t.get("id")}
    skipped_existing = 0
    new_entries: list = []

    for order in orders:
        execution = order.get("execution") or {}
        filled_qty_raw = execution.get("filledQuantity")
        avg_price_raw  = execution.get("averageFilledPrice")

        filled_qty = _parse_qty(filled_qty_raw)
        if filled_qty is None or filled_qty <= 0:
            continue  # CANCELED 또는 미체결

        avg_price = _parse_price(avg_price_raw)
        if avg_price is None:
            print(f"[toss-ledger] {order.get('orderId')} avg_price 파싱 실패 — 스킵 (침묵-0 금지)")
            continue

        order_id = str(order.get("orderId", "")).strip()
        if not order_id:
            continue

        if order_id in existing_ids:
            skipped_existing += 1
            continue

        symbol    = str(order.get("symbol", "")).strip()
        side_raw  = str(order.get("side", "")).upper()
        side      = "buy" if side_raw == "BUY" else "sell"
        currency  = str(order.get("currency", "KRW")).upper()
        ordered_at = str(order.get("orderedAt", ""))
        trade_date = ordered_at[:10] if len(ordered_at) >= 10 else ""

        entry: dict = {
            "id":         order_id,
            "ticker":     symbol,
            "name":       symbol,   # symbol fallback (no external lookup to avoid rate-limit)
            "side":       side,
            "qty":        float(filled_qty),
            "price":      float(avg_price),
            "date":       trade_date,
            "ordered_at": ordered_at,
            "currency":   currency,
            "source":     "toss",
        }
        new_entries.append(entry)
        existing_ids.add(order_id)

    # 병합 + ordered_at 정렬 + 5000건 캡
    merged = existing_trades + new_entries
    merged.sort(key=lambda t: (t.get("ordered_at") or t.get("date") or "", t.get("id") or ""))
    if len(merged) > 5000:
        merged = merged[-5000:]

    save_json(TOSS_TRADES_FILE, {"trades": merged})

    print(
        f"[toss-ledger] 완료 — 신규 {len(new_entries)}건, "
        f"기존중복 {skipped_existing}건, 총 {len(merged)}건"
    )
    return {
        "ok":               True,
        "added":            len(new_entries),
        "total":            len(merged),
        "skipped_existing": skipped_existing,
    }


def get_toss_trade_summary() -> dict:
    """toss_trades.json 레저 읽기전용 분석 — 통화별 실현손익 + 승률.

    - KRW / USD 완전 분리 (혼산 절대 금지)
    - 운용평균원가(running weighted-average cost) 기준 실현손익
    - 매도 전에 매수 기록이 없으면 pnl=None (침묵-0 금지 / fabricate 금지)
    - pnl_pct: 퍼센트 단위 (e.g. 28.57 = +28.57%)

    반환 구조:
    {
      "total_trades": int,
      "by_currency": {
        "KRW": {"realized_pnl": float, "win": int, "loss": int, "win_rate_pct": float},
        "USD": {...},
      },
      "by_ticker": {
        "<currency>/<ticker>": {
          "currency": str, "ticker": str,
          "realized_pnl": float|None, "pnl_pct": float|None,
          "open_qty": float,
          "sell_count": int, "win_count": int,
        }, ...
      },
      "recent_10": [trade_dict, ...],
    }
    """
    from ._config import TOSS_TRADES_FILE
    from ._files import load_json

    data = load_json(TOSS_TRADES_FILE, {"trades": []})
    trades: list = data.get("trades", []) if isinstance(data, dict) else []
    if not isinstance(trades, list):
        trades = []

    # trades는 ordered_at 정렬 상태(sync_toss_trade_ledger 보장) — 순서 재정렬 보강
    trades_sorted = sorted(
        trades,
        key=lambda t: (t.get("ordered_at") or t.get("date") or "", t.get("id") or "")
    )

    # 통화+티커별 running-avg 상태
    # key = (currency, ticker)
    _avg_state: dict[tuple, dict] = {}  # {"total_qty": float, "total_cost": float}
    _ticker_stats: dict[tuple, dict] = {}

    for trade in trades_sorted:
        currency = str(trade.get("currency", "KRW")).upper()
        ticker   = str(trade.get("ticker", "")).strip()
        side     = str(trade.get("side", "")).lower()
        qty      = trade.get("qty")
        price    = trade.get("price")

        if not ticker or not currency or not side:
            continue
        try:
            qty   = float(qty)
            price = float(price)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(qty) and math.isfinite(price)):
            continue
        if qty <= 0 or price <= 0:
            continue

        key = (currency, ticker)
        if key not in _avg_state:
            _avg_state[key] = {"total_qty": 0.0, "total_cost": 0.0}
        if key not in _ticker_stats:
            _ticker_stats[key] = {
                "currency":     currency,
                "ticker":       ticker,
                "open_qty":     0.0,
                "realized_pnl": None,
                "pnl_pct":      None,
                "sell_count":   0,
                "win_count":    0,
            }

        state = _avg_state[key]
        stats = _ticker_stats[key]

        if side == "buy":
            state["total_qty"]  += qty
            state["total_cost"] += qty * price
            stats["open_qty"]   += qty

        elif side == "sell":
            stats["sell_count"] += 1
            stats["open_qty"]   = max(0.0, stats["open_qty"] - qty)

            avg_cost = (
                state["total_cost"] / state["total_qty"]
                if state["total_qty"] > 0 else None
            )
            if avg_cost is not None and avg_cost > 0:
                pnl = (price - avg_cost) * qty
                pnl_pct = (price / avg_cost - 1) * 100

                # 누산
                prev_pnl = stats["realized_pnl"] if stats["realized_pnl"] is not None else 0.0
                stats["realized_pnl"] = prev_pnl + pnl
                stats["pnl_pct"] = round(pnl_pct, 2)  # 최신 매도 기준 (참고용)
                if pnl > 0:
                    stats["win_count"] += 1
            # no prior buy → leave pnl None (침묵-0 금지)

            # FIFO 방식으로 cost-basis 차감 (running avg 보전)
            if state["total_qty"] >= qty:
                ratio = qty / state["total_qty"]
                state["total_cost"] -= state["total_cost"] * ratio
                state["total_qty"]  -= qty
                state["total_cost"] = max(0.0, state["total_cost"])
                state["total_qty"]  = max(0.0, state["total_qty"])
            else:
                state["total_qty"]  = 0.0
                state["total_cost"] = 0.0

    # 통화별 집계
    by_currency: dict[str, dict] = {}
    for (currency, ticker), stats in _ticker_stats.items():
        if currency not in by_currency:
            by_currency[currency] = {"realized_pnl": 0.0, "win": 0, "loss": 0}
        cur = by_currency[currency]
        if stats["realized_pnl"] is not None:
            cur["realized_pnl"] += stats["realized_pnl"]
        cur["win"]  += stats["win_count"]
        sell_losses  = stats["sell_count"] - stats["win_count"]
        cur["loss"] += max(0, sell_losses)

    for currency, cur in by_currency.items():
        total_sells = cur["win"] + cur["loss"]
        cur["win_rate_pct"] = round(cur["win"] / total_sells * 100, 1) if total_sells > 0 else None
        cur["realized_pnl"] = round(cur["realized_pnl"], 2)

    # by_ticker: key = "KRW/005930" 형태
    by_ticker = {}
    for (currency, ticker), stats in _ticker_stats.items():
        k = f"{currency}/{ticker}"
        by_ticker[k] = dict(stats)
        if stats["realized_pnl"] is not None:
            by_ticker[k]["realized_pnl"] = round(stats["realized_pnl"], 2)

    recent_10 = trades_sorted[-10:] if len(trades_sorted) >= 10 else trades_sorted[:]

    return {
        "total_trades": len(trades_sorted),
        "by_currency":  by_currency,
        "by_ticker":    by_ticker,
        "recent_10":    recent_10,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# FEATURE ② — 장 운영 캘린더 (US 신규 + KR drift 감지)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def fetch_toss_market_calendar(country: str) -> dict | None:
    """GET /api/v1/market-calendar/{country} → result dict (None on failure).

    country: "KR" 또는 "US"
    응답: {today:{date,…}, previousBusinessDay:{date}, nextBusinessDay:{date}}
    """
    country = country.upper()
    data = await _toss_get(f"/api/v1/market-calendar/{country}")
    if data is None:
        return None
    result = data.get("result")
    if not isinstance(result, dict):
        print(f"[toss-cal] {country} calendar result 비정상: {type(result)}")
        return None
    return result


async def is_kr_business_day_toss(date_str: str | None = None) -> bool | None:
    """Toss KR 캘린더로 오늘(또는 date_str)이 영업일인지 확인.

    반환: True=영업일, False=휴장/주말, None=API 실패 (caller falls back).
    date_str: "YYYY-MM-DD" 형식 (None = 오늘).
    판정 로직: result.today.date 가 date_str과 일치 + today 안에 정규장/통합장 시간 정보 있으면 영업일.
    주말/휴일이면 today.date가 요청일과 다를 수 있음 — previousBusinessDay / nextBusinessDay 브라케팅으로 확인.
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    cal = await fetch_toss_market_calendar("KR")
    if cal is None:
        return None

    today_info = cal.get("today") or {}
    today_date = today_info.get("date", "")[:10]

    if today_date == date_str:
        # today가 요청일: 정규장 또는 통합(+시장) 세션 정보 있으면 영업일
        has_session = bool(
            today_info.get("regularMarket")
            or today_info.get("integrated")
            or today_info.get("sessions")
            or today_info.get("openTime")
            or today_info.get("closeTime")
        )
        # 추가 방어: 명시적 holiday 마커
        is_holiday = bool(today_info.get("holiday") or today_info.get("isHoliday"))
        if is_holiday:
            return False
        # today가 요청일이고 세션 정보 없으면 휴장으로 취급
        return has_session

    # today 날짜가 요청일과 다름 — bracketing으로 판정
    prev_date = (cal.get("previousBusinessDay") or {}).get("date", "")[:10]
    next_date = (cal.get("nextBusinessDay") or {}).get("date", "")[:10]

    if prev_date and next_date:
        # date_str이 prev와 next 사이에 없으면 non-business day
        return bool(prev_date <= date_str <= next_date and date_str != today_date)

    # fallback: today가 요청일 아니면 영업일 아님
    return False


async def is_us_market_open_now() -> bool:
    """현재 시각이 미국 정규장 운영 시간 안에 있는지 확인 (Toss US 캘린더 기반).

    반환: True=장 중, False=장 외 또는 API 실패 (보수적).
    """
    cal = await fetch_toss_market_calendar("US")
    if cal is None:
        return False

    today_info = cal.get("today") or {}

    # 명시적 holiday/non-trading 마커
    if today_info.get("holiday") or today_info.get("isHoliday"):
        return False

    # 세션 시간 파싱 시도 (openTime/closeTime or regularMarket.open/close)
    open_str  = (
        today_info.get("openTime")
        or (today_info.get("regularMarket") or {}).get("open")
        or (today_info.get("regularMarket") or {}).get("openTime")
    )
    close_str = (
        today_info.get("closeTime")
        or (today_info.get("regularMarket") or {}).get("close")
        or (today_info.get("regularMarket") or {}).get("closeTime")
    )

    if not open_str or not close_str:
        # 세션 정보 없으면 장 없음(휴장)으로 보수적 처리
        return False

    try:
        # ISO 형식 파싱 (e.g. "2026-06-18T09:30:00-04:00")
        from datetime import timezone as _tz
        now_utc = datetime.now(_tz.utc)

        def _parse_iso(s: str) -> datetime:
            # Python 3.11+ fromisoformat handles +HH:MM; 3.9/3.10도 지원
            try:
                return datetime.fromisoformat(s)
            except ValueError:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))

        open_dt  = _parse_iso(open_str)
        close_dt = _parse_iso(close_str)
        return open_dt <= now_utc <= close_dt
    except Exception as e:
        print(f"[toss-cal] US 시간 파싱 실패: {e} (open={open_str!r}, close={close_str!r})")
        return False


async def check_kr_holiday_drift() -> dict:
    """하드코딩된 _KR_MARKET_HOLIDAYS vs Toss KR 캘린더 드리프트 감지.

    향후 30일 범위에서 Toss가 휴장이라고 알리는 날이 하드코딩 집합에 없거나,
    반대로 하드코딩에는 있지만 Toss는 영업일로 볼 경우 불일치를 반환.

    반환: {
        "ok": bool,
        "toss_holidays_not_in_hardcoded": ["YYYYMMDD", ...],
        "hardcoded_not_in_toss":          ["YYYYMMDD", ...],
        "checked_days": N,
    }
    API 실패 시 ok=False.
    """
    try:
        from db_collector._config import _KR_MARKET_HOLIDAYS
    except ImportError:
        try:
            from db_collector import _KR_MARKET_HOLIDAYS  # type: ignore[attr-defined]
        except Exception as e:
            return {"ok": False, "reason": f"_KR_MARKET_HOLIDAYS import 실패: {e}"}

    today = _date.today()
    dates_to_check: list[str] = []  # YYYY-MM-DD for API, YYYYMMDD for hardcoded
    for offset in range(31):
        d = today + timedelta(days=offset)
        if d.weekday() < 5:  # 평일만 (주말은 양측 모두 공통 휴장)
            dates_to_check.append(d.strftime("%Y-%m-%d"))

    toss_not_in_hard: list = []
    hard_not_in_toss: list = []

    for date_str in dates_to_check:
        date_compact = date_str.replace("-", "")
        hardcoded_is_holiday = date_compact in _KR_MARKET_HOLIDAYS

        toss_result = await is_kr_business_day_toss(date_str)
        if toss_result is None:
            # API 실패 — 중단
            return {"ok": False, "reason": f"Toss API 실패 ({date_str})"}

        toss_is_holiday = not toss_result

        if toss_is_holiday and not hardcoded_is_holiday:
            toss_not_in_hard.append(date_compact)
        elif hardcoded_is_holiday and not toss_is_holiday:
            hard_not_in_toss.append(date_compact)

        await asyncio.sleep(0.3)  # rate limit

    return {
        "ok":                             True,
        "toss_holidays_not_in_hardcoded": toss_not_in_hard,
        "hardcoded_not_in_toss":          hard_not_in_toss,
        "checked_days":                   len(dates_to_check),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# FEATURE ③ — 종목 경고 점검
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def fetch_toss_stock_warnings(symbol: str) -> list:
    """GET /api/v1/stocks/{symbol}/warnings → 경고 객체 리스트 반환.

    빈 리스트 = 경고 없음 또는 API 실패 (실패는 로그만, 빈 list 반환 — crash 없음).
    경고 필드는 실제 warned 종목을 보기 전까지 불명 → raw 그대로 반환.
    """
    symbol = symbol.strip().upper()
    data = await _toss_get(f"/api/v1/stocks/{symbol}/warnings")
    if data is None:
        # _toss_get 이미 HTTP 오류 로그 출력 — 여기선 빈 list만 반환
        return []
    result = data.get("result", [])
    if not isinstance(result, list):
        print(f"[toss-warn] {symbol} warnings result 비정상 타입: {type(result)} — 스킵")
        return []
    return result


async def check_watch_warnings() -> list:
    """포트폴리오 보유 + 워치리스트 KR 종목에 대해 Toss 경고 일괄 점검.

    반환: [{"ticker": str, "name": str, "warnings": list}, ...]  — 경고 있는 종목만.
    US 종목은 Toss 경고가 KR 시장 개념이므로 스킵.
    """
    from ._config import PORTFOLIO_FILE
    from ._files import load_json, load_kr_watch_dict

    portfolio = load_json(PORTFOLIO_FILE, {})
    _meta_keys = {"us_stocks", "cash_krw", "cash_usd"}

    # KR 보유 종목 수집
    ticker_name: dict[str, str] = {}
    for ticker, info in portfolio.items():
        if ticker in _meta_keys:
            continue
        if isinstance(info, dict):
            ticker_name[ticker] = info.get("name", ticker)

    # KR 워치리스트 추가
    for ticker, name in load_kr_watch_dict().items():
        ticker_name.setdefault(ticker, name)

    warned: list = []
    for ticker, name in ticker_name.items():
        try:
            warnings = await fetch_toss_stock_warnings(ticker)
            if warnings:
                warned.append({"ticker": ticker, "name": name, "warnings": warnings})
        except Exception as e:
            print(f"[toss-warn] {ticker} 경고 조회 오류: {e} — 스킵")
        await asyncio.sleep(0.3)  # rate limit

    print(f"[toss-warn] 점검 완료 — {len(ticker_name)}종목 점검, 경고 {len(warned)}종목")
    return warned
