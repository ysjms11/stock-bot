"""KIS WebSocket 실시간 체결가 매니저."""
import os
import json
import re
import asyncio
import aiohttp
import sqlite3
import xml.etree.ElementTree as ET
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from ._config import *
from ._config import (
    KIS_BASE_URL, KIS_APP_KEY, KIS_APP_SECRET, KST, ET, _DATA_DIR, _DB_PATH,
    WATCHLIST_FILE, STOPLOSS_FILE, US_WATCHLIST_FILE, DART_SEEN_FILE,
    PORTFOLIO_FILE, WATCHALERT_FILE, WATCH_SENT_FILE, STOPLOSS_SENT_FILE,
    US_HOLDINGS_SENT_FILE, DECISION_LOG_FILE, COMPARE_LOG_FILE,
    WATCHLIST_LOG_FILE, EVENTS_FILE, WEEKLY_BASE_FILE, UNIVERSE_FILE,
    CONSENSUS_CACHE_FILE, PORTFOLIO_HISTORY_FILE, TRADE_LOG_FILE,
    SECTOR_FLOW_CACHE_FILE, SECTOR_ROTATION_FILE, SUPPLY_HISTORY_FILE,
    REPORTS_FILE, REGIME_STATE_FILE, MACRO_SENT_FILE, TOKEN_CACHE_FILE,
    GITHUB_TOKEN, _BACKUP_GIST_ENV, _BACKUP_FILES_LIST, MACRO_SYMBOLS,
    DART_BASE_URL,
)
from ._session import _get_session, _kis_get, _kis_headers, get_kis_token, _token_cache
from ._helpers import (
    _is_us_ticker, _guess_excd, _is_us_market_hours_kst, _is_us_market_closed,
    DART_KEYWORDS, _load_knu_senti_lex, _FINANCE_PHRASE_SCORES, _RANKING_RE,
    _US_POSITIVE_KEYWORDS, _US_NEGATIVE_KEYWORDS, _NYSE_TICKERS, _AMEX_TICKERS,
)
from ._files import (
    load_json, save_json, load_watchlist, load_stoploss, load_us_watchlist,
    load_dart_seen, load_watchalert, _wa_market, load_kr_watch_tickers,
    load_us_watch_tickers, load_kr_watch_dict, load_us_watch_dict,
    load_decision_log, load_trade_log, save_trade_log, get_trade_stats,
    load_consensus_cache, load_sector_flow_cache, save_sector_flow_cache,
    load_compare_log, load_watchlist_log, append_watchlist_log, load_events,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KIS WebSocket 실시간 체결가 (국내주식 전용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_ws_key_cache: dict = {"key": None, "expires": 0.0}


async def get_kis_ws_approval_key() -> str:
    """WebSocket 접속키 발급 (23시간 캐시)"""
    import time as _t
    now = _t.time()
    if _ws_key_cache["key"] and now < _ws_key_cache["expires"]:
        return _ws_key_cache["key"]
    url = f"{KIS_BASE_URL}/oauth2/Approval"
    body = {"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "secretkey": KIS_APP_SECRET}
    try:
        s = _get_session()
        async with s.post(url, json=body) as r:
            d = await r.json(content_type=None)
            key = d.get("approval_key", "")
            if key:
                _ws_key_cache["key"] = key
                _ws_key_cache["expires"] = now + 82800
            return _ws_key_cache.get("key") or ""
    except Exception as e:
        print(f"[WS] 접속키 발급 오류: {e}")
        return ""


class KisRealtimeManager:
    """KIS WebSocket 실시간 체결가 매니저
    - KR 통합체결가: H0UNCNT0 (KRX+NXT), 시간외: H0STOUP0 (16:00~18:00)
    - US 체결가: HDFSCNT0 (미국 장중)
    - 평일 상시 연결 (KR 시간외 + US 야간 대응). 끊김 시 30초 후 자동 재연결.
    """
    # KIS WebSocket은 plain ws:// 만 지원 (wss:// 시도하면 WRONG_VERSION_NUMBER)
    _WS_URL = "ws://ops.koreainvestment.com:21000"

    def __init__(self):
        self._subscribed: set = set()       # KR 종목 set
        self._subscribed_us: set = set()    # US 종목 set
        self._ws = None
        self._alert_cb = None
        self._running = False
        self._task = None
        self._fired: dict = {}  # {ticker: set(alert_types)} — 당일 발송 추적
        self._price_cache: dict = {}  # {ticker: int|float} — 최신 체결가 캐시

    async def start(self, alert_callback, tickers: set):
        self._alert_cb = alert_callback
        self._subscribed    = {t for t in tickers if not _is_us_ticker(t)}
        self._subscribed_us = {t for t in tickers if _is_us_ticker(t)}
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def update_tickers(self, new_tickers: set):
        """구독 종목 변경 (KR + US 모두 지원)"""
        kr_new = {t for t in new_tickers if not _is_us_ticker(t)}
        us_new = {t for t in new_tickers if _is_us_ticker(t)}
        kr_add    = kr_new - self._subscribed
        kr_remove = self._subscribed - kr_new
        us_add    = us_new - self._subscribed_us
        us_remove = self._subscribed_us - us_new
        self._subscribed    = kr_new
        self._subscribed_us = us_new
        if self._ws and not self._ws.closed:
            key = await get_kis_ws_approval_key()
            for t in kr_add:
                await self._send_sub_raw(self._ws, key, t, "1", "H0UNCNT0")
            for t in kr_remove:
                await self._send_sub_raw(self._ws, key, t, "0", "H0UNCNT0")
            for t in us_add:
                tr_key = f"D{_guess_excd(t)}{t}"
                await self._send_sub_raw(self._ws, key, tr_key, "1", "HDFSCNT0")
            for t in us_remove:
                tr_key = f"D{_guess_excd(t)}{t}"
                await self._send_sub_raw(self._ws, key, tr_key, "0", "HDFSCNT0")

    def reset_fired(self):
        self._fired = {}

    async def _run_loop(self):
        while self._running:
            now = datetime.now(KST)
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WS] 오류: {e}, 30초 후 재연결...")
            await asyncio.sleep(30)

    async def _connect_and_run(self):
        # 재연결 시 _fired 보존 — 당일 알림 중복 방지
        # (일별 reset 은 외부 daily 잡 또는 자정 자동)
        key = await get_kis_ws_approval_key()
        if not key:
            print("[WS] 접속키 없음, 스킵")
            return
        kr_count = len(self._subscribed)
        us_count = len(self._subscribed_us)
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self._WS_URL, heartbeat=30,
                timeout=aiohttp.ClientTimeout(total=None),
            ) as ws:
                self._ws = ws
                print(f"[WS] 연결됨 (KR {kr_count}개 + US {us_count}개 구독)")
                # KR 통합 체결가 구독 (H0UNCNT0)
                for t in list(self._subscribed):
                    await self._send_sub_raw(ws, key, t, "1", "H0UNCNT0")
                    await asyncio.sleep(0.05)
                # US 체결가 구독 (HDFSCNT0)
                for t in list(self._subscribed_us):
                    try:
                        tr_key = f"D{_guess_excd(t)}{t}"
                        await self._send_sub_raw(ws, key, tr_key, "1", "HDFSCNT0")
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        print(f"[WS] US 구독 오류 ({t}): {e}")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._on_text(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        print("[WS] 연결 종료됨")
                        break
        self._ws = None

    async def _send_sub_raw(self, ws, key, ticker, tr_type, tr_id="H0UNCNT0"):
        await ws.send_json({
            "header": {
                "approval_key": key, "custtype": "P",
                "tr_type": tr_type, "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": tr_id, "tr_key": ticker}},
        })

    async def _on_text(self, raw: str):
        # 포맷: "0|TR_ID|001|필드1^필드2^..."
        if raw.startswith("{"):
            return   # JSON ACK 무시
        parts = raw.split("|")
        if len(parts) < 4:
            return
        tr_id = parts[1]
        if tr_id not in ("H0UNCNT0", "H0STCNT0", "H0STOUP0", "HDFSCNT0"):
            return
        try:
            count = int(parts[2])
        except (ValueError, IndexError):
            return
        all_fields = parts[3].split("^")
        if count == 0 or not all_fields:
            return
        per_rec = len(all_fields) // max(count, 1)
        for i in range(count):
            f = all_fields[i * per_rec: (i + 1) * per_rec]
            try:
                if tr_id == "HDFSCNT0":
                    # US: SYMB=f[0], LAST=f[10]
                    if len(f) < 11:
                        continue
                    ticker = f[0]
                    price = float(f[10])
                else:
                    # KR (H0UNCNT0 / H0STCNT0 / H0STOUP0): ticker=f[0], price=f[2]
                    if len(f) < 3:
                        continue
                    ticker = f[0]
                    price = int(f[2])
                if price > 0:
                    self._price_cache[ticker] = price
                    if self._alert_cb:
                        await self._alert_cb(ticker, price)
            except Exception:
                continue

    def get_cached_price(self, ticker: str):
        """WebSocket 캐시에서 최신 체결가 반환. 없으면 None."""
        return self._price_cache.get(ticker)

    def set_cached_price(self, ticker: str, price):
        """외부에서 캐시에 가격 저장 (REST fallback 등)."""
        if price and price > 0:
            self._price_cache[ticker] = price


# KisRealtimeManager 싱글톤
ws_manager = KisRealtimeManager()


def get_ws_tickers() -> set:
    """WebSocket 구독 대상 종목 수집 (KR + US).
    단일 소스: 포트폴리오 + 손절 + watchalert (KR/US 통합).
    KIS WebSocket 41건 제한 → 포트폴리오/손절 우선, 초과 시 상위 40건만 반환.
    """
    # 우선순위 1: 포트폴리오 (실제 보유)
    priority: list = []
    seen: set = set()

    def _add(t: str):
        if t and t not in seen:
            seen.add(t)
            priority.append(t)

    pf = load_json(PORTFOLIO_FILE, {})
    for t in pf:
        if t not in ("us_stocks", "cash_krw", "cash_usd"):
            _add(t)
    for sym in pf.get("us_stocks", {}):
        _add(sym)
    # 우선순위 2: 손절/목표가 설정 종목
    sl = load_stoploss()
    for t in sl:
        if t != "us_stocks":
            _add(t)
    for sym in sl.get("us_stocks", {}):
        _add(sym)
    # 우선순위 3: watchalert (KR+US 단일 소스)
    for t in load_watchalert():
        _add(t)

    # KIS WebSocket 41건 제한 → 40건 안전 캡
    if len(priority) > 40:
        priority = priority[:40]
    return set(priority)


