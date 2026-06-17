"""토스증권 Open API — 읽기전용 잔고 동기화.

엔드포인트:
  POST /oauth2/token  → Bearer 토큰 (client_credentials)
  GET  /api/v1/accounts  → 계좌 목록
  GET  /api/v1/holdings  → 보유종목 (X-Tossinvest-Account 헤더 필요)
"""
import math
import os
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
    - cash_krw/cash_usd → 건드리지 않음

    반환: {"ok": True/False, ...}
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

    save_json(PORTFOLIO_FILE, portfolio)

    kr_count = len(toss_kr)
    us_count = len(toss_us)
    print(
        f"[toss-sync] 완료 — KR {kr_count}종목 (추가 {len(added_kr)}, 갱신 {len(updated_kr)}), "
        f"US {us_count}종목 (추가 {len(added_us)}, 갱신 {len(updated_us)}), "
        f"미관찰 플래그 {len(flagged)}종목"
    )
    return {
        "ok":               True,
        "added":            added_kr + added_us,
        "updated":          updated_kr + updated_us,
        "flagged_missing":  flagged,
        "kr_count":         kr_count,
        "us_count":         us_count,
    }
