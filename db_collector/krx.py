"""
db_collector KRX OPEN API + 파싱 헬퍼 (2026-06 분해 P2b-4).
순수 파서 (_pi, _pf, _parse_market_records) + async fetch 함수들.
의존: aiohttp, kis_api._get_session, ._config 상수 (stdlib + aiohttp만).
"""

import aiohttp

from kis_api import _get_session
from ._config import (
    KRX_OPENAPI_BASE,
    KRX_API_KEY,
    _OPENAPI_ENDPOINTS,
    KRX_JSON_URL,
    KRX_HEADERS,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KRX OPEN API (krx_crawler.py에서 복사)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _pi(s) -> int:
    """KRX comma-formatted string → int"""
    if not s or s == "-" or s == "":
        return 0
    return int(str(s).replace(",", "").replace("+", "").strip() or "0")


def _pf(s) -> float:
    """KRX string → float"""
    if not s or s == "-" or s == "":
        return 0.0
    return float(str(s).replace(",", "").replace("+", "").strip() or "0")


async def _krx_openapi_get(session: aiohttp.ClientSession, category: str,
                            endpoint: str, date: str) -> list:
    """KRX OPEN API GET 요청. Returns OutBlock_1 리스트."""
    url = f"{KRX_OPENAPI_BASE}/{category}/{endpoint}"
    params = {"AUTH_KEY": KRX_API_KEY, "basDd": date}
    async with session.get(url, params=params,
                           timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status == 401:
            raise RuntimeError("KRX OPEN API 인증 실패 (401)")
        if resp.status == 429:
            raise RuntimeError("KRX OPEN API 호출 한도 초과 (429)")
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"KRX OPEN API HTTP {resp.status}: {text[:200]}")
        data = await resp.json(content_type=None)
        records = data.get("OutBlock_1", [])
        if not records:
            raise RuntimeError(f"KRX OPEN API 빈 응답 ({endpoint})")
        return records


async def _krx_post(session: aiohttp.ClientSession, form: dict) -> dict:
    """KRX 크롤링 POST 요청."""
    async with session.post(KRX_JSON_URL, data=form, headers=KRX_HEADERS,
                            timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"KRX HTTP {resp.status}: {text[:200]}")
        return await resp.json(content_type=None)


def _parse_market_records(records: list, market: str) -> list[dict]:
    """시세 레코드 파싱 (OPEN API / 크롤링 공통).
    OPEN API: ISU_CD(6자리), ISU_NM
    크롤링:   ISU_SRT_CD(6자리), ISU_ABBRV
    """
    mkt_label = "kospi" if market == "STK" else "kosdaq"
    result = []
    for r in records:
        raw = str(r.get("ISU_SRT_CD") or r.get("ISU_CD", "")).strip()
        # ISIN(KR7XXXXXX000) → 6자리 추출
        if len(raw) == 12 and raw.startswith("KR"):
            ticker = raw[3:9]
        else:
            ticker = raw
        if not ticker or len(ticker) != 6:
            continue
        name = str(r.get("ISU_ABBRV") or r.get("ISU_NM", "")).strip()
        result.append({
            "ticker": ticker,
            "name": name,
            "market": mkt_label,
            "close": _pi(r.get("TDD_CLSPRC")),
            "chg_pct": _pf(r.get("FLUC_RT")),
            "volume": _pi(r.get("ACC_TRDVOL")),
            "trade_value": _pi(r.get("ACC_TRDVAL")),
            "market_cap": _pi(r.get("MKTCAP")),
        })
    return result


async def fetch_krx_market_data(date: str, market: str = "STK") -> list[dict]:
    """전종목 시세. KRX OPEN API 우선, 실패 시 크롤링 fallback."""
    # 1차: KRX OPEN API
    if KRX_API_KEY:
        ep = _OPENAPI_ENDPOINTS.get(f"market_{market}")
        if ep:
            try:
                s = _get_session()
                records = await _krx_openapi_get(s, ep[0], ep[1], date)
                result = _parse_market_records(records, market)
                print(f"[KRX OPENAPI] {market} 시세: {len(result)}종목")
                return result
            except Exception as e:
                print(f"[KRX OPENAPI] {market} 시세 실패: {e} → 크롤링 fallback")

    # 2차: 크롤링 (data.krx.co.kr)
    form = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
        "locale": "ko_KR",
        "mktId": market,
        "trdDd": date,
        "share": "1",
        "money": "1",
    }
    try:
        s = _get_session()
        body = await _krx_post(s, form)
        records = body.get("OutBlock_1", [])
        if not records:
            raise RuntimeError("empty OutBlock_1")
        result = _parse_market_records(records, market)
        print(f"[KRX] {market} 시세: {len(result)}종목")
        return result
    except Exception as e:
        print(f"[KRX] {market} 시세 직접호출 실패: {e}")
        return []
