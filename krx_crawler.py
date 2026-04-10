"""
KRX 전종목 일별 데이터 크롤러
- 전종목 시세 (종가, 등락률, 거래량, 거래대금, 시총)
- 전종목 PER/PBR (기본정보)
- 투자자별 수급 (외인, 기관, 개인 순매수)
- 비율 계산 (foreign_ratio, inst_ratio, fi_ratio, turnover)
- DB: /data/krx_db/YYYYMMDD.json
- KRX OPEN API (openapi.krx.co.kr) 우선, 실패 시 크롤링 fallback
"""

import aiohttp
import asyncio
import json
import os
import subprocess
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
_DATA_DIR = os.environ.get("DATA_DIR", "/data")
KRX_DB_DIR = f"{_DATA_DIR}/krx_db"
KRX_JSON_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

KRX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101",
}

KRX_PROXY = os.environ.get("KRX_PROXY", "")

# KRX OPEN API
KRX_OPENAPI_BASE = "https://data-dbg.krx.co.kr/svc/apis"
KRX_API_KEY = os.environ.get("KRX_API_KEY", "")

# OPEN API 엔드포인트 매핑
_OPENAPI_ENDPOINTS = {
    "market_STK": ("sto", "stk_bydd_trd"),      # 유가증권 일별매매정보
    "market_KSQ": ("sto", "ksq_bydd_trd"),       # 코스닥 일별매매정보
    # 종목기본정보(stk_isu_base_info)에는 PER/PBR 없음 → PER/PBR은 크롤링 유지
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 파싱 헬퍼
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KRX OPEN API
# ━━━━━━━━━━━━━━━━━━━━━━━━━
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KRX 크롤링 (fallback)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def _krx_post(session: aiohttp.ClientSession, form: dict) -> dict:
    proxy = KRX_PROXY or None
    async with session.post(KRX_JSON_URL, data=form, headers=KRX_HEADERS,
                            proxy=proxy, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"KRX HTTP {resp.status}: {text[:200]}")
        return await resp.json(content_type=None)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1) 전종목 시세
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _parse_market_records(records: list, market: str) -> list[dict]:
    """시세 레코드 파싱 (OPEN API / 크롤링 공통).
    OPEN API: ISU_CD(6자리), ISU_NM
    크롤링:   ISU_SRT_CD(6자리), ISU_ABBRV
    """
    mkt_label = "kospi" if market == "STK" else "kosdaq"
    result = []
    for r in records:
        # 크롤링: ISU_SRT_CD(6자리), OPEN API: ISU_CD(6자리 또는 ISIN 12자리)
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
    # ── 1차: KRX OPEN API ──
    if KRX_API_KEY:
        ep = _OPENAPI_ENDPOINTS.get(f"market_{market}")
        if ep:
            try:
                async with aiohttp.ClientSession() as s:
                    records = await _krx_openapi_get(s, ep[0], ep[1], date)
                result = _parse_market_records(records, market)
                print(f"[KRX OPENAPI] {market} 시세: {len(result)}종목")
                return result
            except Exception as e:
                print(f"[KRX OPENAPI] {market} 시세 실패: {e} → 크롤링 fallback")

    # ── 2차: 크롤링 (data.krx.co.kr) ──
    form = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
        "locale": "ko_KR",
        "mktId": market,
        "trdDd": date,
        "share": "1",
        "money": "1",
    }
    try:
        async with aiohttp.ClientSession() as s:
            body = await _krx_post(s, form)
        records = body.get("OutBlock_1", [])
        if not records:
            raise RuntimeError("empty OutBlock_1")
        result = _parse_market_records(records, market)
        print(f"[KRX] {market} 시세: {len(result)}종목")
        return result
    except Exception as e:
        print(f"[KRX] {market} 시세 직접호출 실패: {e} → pykrx fallback")
        return await _market_data_pykrx(date, market)


async def _market_data_pykrx(date: str, market: str) -> list[dict]:
    """pykrx fallback — 시세+시총."""
    try:
        from pykrx import stock
        mkt = "KOSPI" if market == "STK" else "KOSDAQ"
        mkt_label = "kospi" if market == "STK" else "kosdaq"

        def _sync():
            ohlcv = stock.get_market_ohlcv(date, market=mkt)
            cap = stock.get_market_cap(date, market=mkt)
            return ohlcv, cap

        ohlcv, cap = await asyncio.to_thread(_sync)
        if ohlcv.empty:
            return []

        # 종목명은 stock_universe.json에서 보완
        names = _load_name_map()
        result = []
        for ticker in ohlcv.index:
            o = ohlcv.loc[ticker]
            c = cap.loc[ticker] if ticker in cap.index else None
            result.append({
                "ticker": ticker,
                "name": names.get(ticker, ticker),
                "market": mkt_label,
                "close": int(o.get("종가", 0)),
                "chg_pct": float(o.get("등락률", 0)),
                "volume": int(o.get("거래량", 0)),
                "trade_value": int(o.get("거래대금", 0)),
                "market_cap": int(c["시가총액"]) if c is not None else 0,
            })
        print(f"[KRX] {market} pykrx: {len(result)}종목")
        return result
    except Exception as e:
        print(f"[KRX] pykrx fallback 실패: {e}")
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2) 전종목 PER/PBR
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _parse_fundamental_records(records: list) -> dict:
    """PER/PBR 레코드 파싱 (OPEN API / 크롤링 공통)."""
    result = {}
    for r in records:
        ticker = str(r.get("ISU_SRT_CD", "")).strip()
        if ticker:
            result[ticker] = {
                "per": _pf(r.get("PER", "0")),
                "pbr": _pf(r.get("PBR", "0")),
            }
    return result


async def fetch_krx_fundamental(date: str, market: str = "STK") -> dict:
    """전종목 PER/PBR. 크롤링 우선, 실패 시 pykrx fallback.
    (KRX OPEN API 종목기본정보에는 PER/PBR 필드 없음)"""
    # ── 크롤링 (data.krx.co.kr) ──
    form = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT03901",
        "locale": "ko_KR",
        "mktId": market,
        "trdDd": date,
    }
    try:
        async with aiohttp.ClientSession() as s:
            body = await _krx_post(s, form)
        records = body.get("output", body.get("OutBlock_1", []))
        result = _parse_fundamental_records(records)
        print(f"[KRX] {market} PER/PBR: {len(result)}종목")
        return result
    except Exception as e:
        print(f"[KRX] {market} PER/PBR 직접호출 실패: {e} → pykrx fallback")
        return await _fundamental_pykrx(date, market)


async def _fundamental_pykrx(date: str, market: str) -> dict:
    try:
        from pykrx import stock
        mkt = "KOSPI" if market == "STK" else "KOSDAQ"

        def _sync():
            return stock.get_market_fundamental(date, market=mkt)

        fund = await asyncio.to_thread(_sync)
        if fund.empty:
            return {}
        result = {}
        for ticker in fund.index:
            f = fund.loc[ticker]
            result[ticker] = {
                "per": float(f.get("PER", 0)),
                "pbr": float(f.get("PBR", 0)),
            }
        return result
    except Exception as e:
        print(f"[KRX] pykrx fundamental fallback 실패: {e}")
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3) 투자자별 순매수 — MDCSTAT02401
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def fetch_krx_investor_data(date: str, market: str = "STK") -> dict:
    """전종목 투자자별 순매수. Returns {ticker: {foreign_net_qty, foreign_net_amt, ...}}"""
    result = {}
    inv_types = [("9000", "foreign"), ("7050", "inst"), ("8000", "indiv")]

    async with aiohttp.ClientSession() as s:
        for inv_code, prefix in inv_types:
            try:
                form = {
                    "bld": "dbms/MDC/STAT/standard/MDCSTAT02401",
                    "locale": "ko_KR",
                    "strtDd": date,
                    "endDd": date,
                    "mktId": market,
                    "invstTpCd": inv_code,
                }
                body = await _krx_post(s, form)
                records = body.get("output", body.get("OutBlock_1", []))
                for r in records:
                    ticker = r.get("ISU_SRT_CD", "")
                    if not ticker:
                        continue
                    if ticker not in result:
                        result[ticker] = {}
                    result[ticker][f"{prefix}_net_qty"] = _pi(r.get("NETBID_TRDVOL"))
                    result[ticker][f"{prefix}_net_amt"] = _pi(r.get("NETBID_TRDVAL"))
                print(f"[KRX] {market} 투자자({prefix}): {len(records)}종목")
            except Exception as e:
                print(f"[KRX] {market} 투자자({prefix}) 실패: {e}")
            await asyncio.sleep(1)

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DB 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _load_name_map() -> dict:
    """stock_universe.json에서 {ticker: name} 매핑 로드."""
    path = f"{_DATA_DIR}/stock_universe.json"
    if not os.path.exists(path):
        path = "stock_universe.json"
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            uni = json.load(f)
        return {k: v.get("name", k) if isinstance(v, dict) else v for k, v in uni.items()}
    except Exception:
        return {}


def load_krx_db(date: str = None) -> dict | None:
    """DB 파일 로드. date=None이면 최신 파일."""
    if not os.path.exists(KRX_DB_DIR):
        return None
    if date:
        fp = os.path.join(KRX_DB_DIR, f"{date}.json")
        if not os.path.exists(fp):
            return None
        with open(fp, encoding="utf-8") as f:
            return json.load(f)

    files = sorted([f for f in os.listdir(KRX_DB_DIR) if f.endswith(".json")], reverse=True)
    if not files:
        return None
    with open(os.path.join(KRX_DB_DIR, files[0]), encoding="utf-8") as f:
        return json.load(f)


def _cleanup_old_db(keep_days: int = 30):
    """keep_days 이전 DB 파일 삭제."""
    if not os.path.exists(KRX_DB_DIR):
        return
    cutoff = (datetime.now(KST) - timedelta(days=keep_days)).strftime("%Y%m%d")
    removed = 0
    for fname in os.listdir(KRX_DB_DIR):
        if fname.endswith(".json") and fname[:8] < cutoff:
            os.remove(os.path.join(KRX_DB_DIR, fname))
            removed += 1
    if removed:
        print(f"[KRX] {removed}개 오래된 DB 파일 삭제")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 기술적 지표 계산
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _load_history(target_date: str, n_days: int = 250) -> dict:
    """과거 N일 DB에서 종목별 {close: [...], volume: [...], eps: [...]} 시계열 로드.
    최신(target_date)이 index 0. 날짜 목록도 반환.
    Returns: ({ticker: {close: [], volume: [], eps: []}}, [dates])
    """
    if not os.path.exists(KRX_DB_DIR):
        return {}, []
    files = sorted([f for f in os.listdir(KRX_DB_DIR)
                     if f.endswith(".json") and f[:8] <= target_date], reverse=True)[:n_days]
    if not files:
        return {}, []

    history = {}
    dates = []
    for fname in files:
        d = fname[:8]
        dates.append(d)
        try:
            with open(os.path.join(KRX_DB_DIR, fname), encoding="utf-8") as f:
                db = json.load(f)
            for ticker, s in db.get("stocks", {}).items():
                if ticker not in history:
                    history[ticker] = {"close": [], "volume": [], "eps": [],
                                       "foreign_net_amt": [], "short_balance": [],
                                       "credit_balance": [], "foreign_hold_ratio": []}
                h = history[ticker]
                h["close"].append(s.get("close", 0))
                h["volume"].append(s.get("volume", 0))
                h["eps"].append(s.get("eps", 0))
                h["foreign_net_amt"].append(s.get("foreign_net_amt", 0))
                h["short_balance"].append(s.get("short_balance", 0))
                h["credit_balance"].append(s.get("credit_balance", 0))
                h["foreign_hold_ratio"].append(s.get("foreign_hold_ratio", 0))
        except Exception:
            pass
    return history, dates


def _ma(arr, n):
    """Simple MA. Returns None if insufficient data."""
    if len(arr) < n:
        return None
    return round(float(np.mean(arr[:n])), 2)


def _rsi(closes, period=14):
    """RSI calculation. Returns None if insufficient data."""
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i + 1] for i in range(min(len(closes) - 1, period * 3))]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]
    if len(gains) < period:
        return None
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _calc_vp(closes: list, volumes: list, n: int, n_bins: int = 20) -> dict:
    """매물대 계산. Returns {poc, va_high, va_low, position} or all None."""
    null = {"poc": None, "va_high": None, "va_low": None, "position": None}
    actual = min(n, len(closes))
    if actual < 30 or len(volumes) < actual:
        return null
    cs = closes[:actual]
    vs = volumes[:actual]
    p_min, p_max = min(cs), max(cs)
    if p_max <= p_min:
        return null
    bin_size = (p_max - p_min) / n_bins
    bins = [0.0] * n_bins
    for c, v in zip(cs, vs):
        idx = min(int((c - p_min) / bin_size), n_bins - 1)
        bins[idx] += v
    poc_idx = int(np.argmax(bins))
    total_vol = sum(bins)
    if total_vol == 0:
        return null
    target_vol = total_vol * 0.7
    sorted_bins = sorted(range(n_bins), key=lambda i: bins[i], reverse=True)
    va_vol = 0
    va_indices = []
    for bi in sorted_bins:
        va_vol += bins[bi]
        va_indices.append(bi)
        if va_vol >= target_vol:
            break
    va_low_idx = min(va_indices)
    va_high_idx = max(va_indices)
    va_high = round(p_min + (va_high_idx + 1) * bin_size)
    va_low = round(p_min + va_low_idx * bin_size)
    cur = closes[0] if closes else 0
    rng = va_high - va_low
    return {
        "poc": round(p_min + (poc_idx + 0.5) * bin_size),
        "va_high": va_high,
        "va_low": va_low,
        "position": round((cur - va_low) / rng, 4) if rng > 0 else None,
    }


def _volume_ratio(volumes: list, recent: int, prev_offset: int) -> float | None:
    """최근 recent일 평균 / 그 이전 recent일 평균."""
    total = recent + prev_offset
    if len(volumes) < total:
        return None
    r = np.mean(volumes[:recent]) if any(v > 0 for v in volumes[:recent]) else 0
    p = np.mean(volumes[prev_offset:total]) if any(v > 0 for v in volumes[prev_offset:total]) else 0
    return round(r / p, 2) if p > 0 else None


def _spread_at(closes: list, offset: int) -> float | None:
    """offset일 전 시점의 MA spread (MA5-MA60)/MA60."""
    if len(closes) < offset + 60:
        return None
    ma5 = _ma(closes[offset:], 5)
    ma60 = _ma(closes[offset:], 60)
    if ma5 and ma60 and ma60 > 0:
        return (ma5 - ma60) / ma60 * 100
    return None


def _rsi_at(closes: list, offset: int, period: int = 14) -> float | None:
    """offset일 전 시점의 RSI."""
    if len(closes) < offset + period + 1:
        return None
    return _rsi(closes[offset:], period)


def _compute_technicals(date: str, stocks: dict):
    """기술적 지표 + 추세 점수 + 매물대를 stocks dict에 in-place 추가."""
    history, dates = _load_history(date, 260)
    n_days = len(dates)
    print(f"[Tech] 과거 {n_days}일 DB 로드, 지표 계산 시작")

    # 연초 날짜 (YTD 계산용)
    year = date[:4]
    ytd_idx = None
    for i, d in enumerate(dates):
        if d[:4] < year:
            ytd_idx = i
            break

    # 섹터 평균 등락률 계산
    sector_chg = {}
    for s in stocks.values():
        sec = s.get("sector_name", "")
        if sec:
            sector_chg.setdefault(sec, []).append(s.get("chg_pct", 0))
    sector_avg = {sec: round(float(np.mean(vals)), 4) for sec, vals in sector_chg.items() if vals}

    for ticker, s in stocks.items():
        h = history.get(ticker, {})
        closes = h.get("close", [])
        volumes = h.get("volume", [])
        eps_hist = h.get("eps", [])
        cur = s.get("close", 0)

        # ── 이평선 ──
        s["ma5"] = _ma(closes, 5)
        s["ma10"] = _ma(closes, 10)
        s["ma20"] = _ma(closes, 20)
        s["ma60"] = _ma(closes, 60)
        s["ma120"] = _ma(closes, 120)
        s["ma200"] = _ma(closes, 200)

        # ── RSI(14) ──
        s["rsi14"] = _rsi(closes, 14)

        # ── 볼린저밴드 (MA20 ± 2σ) ──
        if len(closes) >= 20:
            m20 = float(np.mean(closes[:20]))
            std20 = float(np.std(closes[:20], ddof=0))
            s["bb_upper"] = round(m20 + 2 * std20, 0)
            s["bb_lower"] = round(m20 - 2 * std20, 0)
        else:
            s["bb_upper"] = s["bb_lower"] = None

        # ── MA spread ──
        ma5v = s["ma5"]
        ma60v = s["ma60"]
        s["ma_spread"] = round((ma5v - ma60v) / ma60v * 100, 2) if ma5v and ma60v and ma60v > 0 else None

        # ── 52주 고/저/position ──
        if len(closes) >= 60:
            w52_slice = closes[:min(250, len(closes))]
            w52h = max(w52_slice)
            w52l = min(w52_slice)
            s["w52_high"] = w52h
            s["w52_low"] = w52l
            s["w52_position"] = round((cur - w52l) / (w52h - w52l), 4) if w52h > w52l else None
        else:
            s["w52_high"] = s["w52_low"] = s["w52_position"] = None

        # ── YTD 수익률 ──
        if ytd_idx is not None and ytd_idx < len(closes) and closes[ytd_idx] > 0:
            s["ytd_return"] = round((cur - closes[ytd_idx]) / closes[ytd_idx] * 100, 2)
        else:
            s["ytd_return"] = None

        # ── 섹터 상대강도 ──
        sec = s.get("sector_name", "")
        s["sector_rel_strength"] = round(s.get("chg_pct", 0) - sector_avg[sec], 2) if sec and sec in sector_avg else None

        # ── 추세: volume_ratio 5d/20d ──
        s["volume_ratio_5d"] = _volume_ratio(volumes, 5, 5)
        s["volume_ratio_20d"] = _volume_ratio(volumes, 20, 20)
        s["volume_ratio_10d"] = _volume_ratio(volumes, 10, 10)  # 하위호환

        # ── 추세: ma_spread_change 10d/30d ──
        cur_spread = s["ma_spread"]
        for nd in (10, 30):
            key = f"ma_spread_change_{nd}d"
            prev = _spread_at(closes, nd)
            s[key] = round(cur_spread - prev, 2) if cur_spread is not None and prev is not None else None

        # ── 추세: rsi_change 5d/20d ──
        rsi_now = s["rsi14"]
        for nd in (5, 20):
            key = f"rsi_change_{nd}d"
            prev_rsi = _rsi_at(closes, nd)
            s[key] = round(rsi_now - prev_rsi, 2) if rsi_now is not None and prev_rsi is not None else None

        # ── 추세: eps_change_90d + earnings_gap ──
        ep_idx = min(89, len(eps_hist) - 1) if len(eps_hist) >= 2 else -1
        if ep_idx >= 1 and eps_hist[0] != 0 and eps_hist[ep_idx] != 0:
            s["eps_change_90d"] = round((eps_hist[0] - eps_hist[ep_idx]) / abs(eps_hist[ep_idx]) * 100, 2)
            ytd = s.get("ytd_return")
            s["earnings_gap"] = round(s["eps_change_90d"] - ytd, 2) if ytd is not None else None
        else:
            s["eps_change_90d"] = s["earnings_gap"] = None

        # ── 수급 추세: foreign_trend Nd ──
        frgn_hist = h.get("foreign_net_amt", [])
        for nd in (5, 20, 60):
            key = f"foreign_trend_{nd}d"
            if len(frgn_hist) >= nd:
                buy_days = sum(1 for x in frgn_hist[:nd] if x > 0)
                s[key] = round(buy_days / nd, 4)
            else:
                s[key] = None

        # ── 수급 추세: short_change Nd ──
        short_hist = h.get("short_balance", [])
        for nd in (5, 20):
            key = f"short_change_{nd}d"
            if len(short_hist) >= nd + 1 and short_hist[nd] > 0:
                s[key] = round((short_hist[0] - short_hist[nd]) / short_hist[nd] * 100, 2)
            else:
                s[key] = None

        # ── 수급 추세: credit_change Nd ──
        credit_hist = h.get("credit_balance", [])
        for nd in (5, 20):
            key = f"credit_change_{nd}d"
            if len(credit_hist) >= nd + 1 and credit_hist[nd] > 0:
                s[key] = round((credit_hist[0] - credit_hist[nd]) / credit_hist[nd] * 100, 2)
            else:
                s[key] = None

        # ── 수급 추세: foreign_hold_change_5d ──
        fh_hist = h.get("foreign_hold_ratio", [])
        if len(fh_hist) >= 6 and fh_hist[5] > 0:
            s["foreign_hold_change_5d"] = round(fh_hist[0] - fh_hist[5], 4)
        else:
            s["foreign_hold_change_5d"] = None

        # ── 매물대 60d / 250d ──
        for period, suffix in [(60, "_60d"), (250, "_250d")]:
            vp = _calc_vp(closes, volumes, period)
            s[f"vp_poc{suffix}"] = vp["poc"]
            s[f"vp_va_high{suffix}"] = vp["va_high"]
            s[f"vp_va_low{suffix}"] = vp["va_low"]
            s[f"vp_position{suffix}"] = vp["position"]
        # 하위호환: vp_poc/va_high/va_low/vp_position = 250d 기준
        s["vp_poc"] = s["vp_poc_250d"]
        s["vp_va_high"] = s["vp_va_high_250d"]
        s["vp_va_low"] = s["vp_va_low_250d"]
        s["vp_position"] = s["vp_position_250d"]

    # ── 섹터 내 순위 계산 ──
    sector_stocks = {}
    for ticker, s in stocks.items():
        sec = s.get("sector_name", "")
        if sec:
            sector_stocks.setdefault(sec, []).append((ticker, s.get("chg_pct", 0)))
    for sec, members in sector_stocks.items():
        members.sort(key=lambda x: x[1], reverse=True)
        for rank, (ticker, _) in enumerate(members, 1):
            stocks[ticker]["sector_rank"] = rank

    for s in stocks.values():
        s.setdefault("sector_rank", None)

    print(f"[Tech] 지표 계산 완료: {len(stocks)}종목")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Safari KRX 크롤링 (로그인 세션)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _safari_fetch(bld: str, params: dict, key: str = "krx_tmp") -> list:
    """Safari fetch로 KRX JSON API 호출 (동기). Returns output records."""
    body_parts = [f"{k}={v}" for k, v in params.items()]
    body_str = "&".join(body_parts)

    js = (f"fetch('/comm/bldAttendant/getJsonData.cmd',{{"
          f"method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},"
          f"body:'{body_str}'}}).then(r=>r.text()).then(t=>"
          f"{{localStorage.setItem('{key}',t);document.title='OK_'+t.length;}})"
          f".catch(e=>document.title='ERR:'+e.message);")

    try:
        subprocess.run(["osascript", "-e",
            f'tell application "Safari" to do JavaScript "{js}" in document 1'],
            capture_output=True, timeout=15)
        import time; time.sleep(3)

        r = subprocess.run(["osascript", "-e",
            'tell application "Safari" to get name of document 1'],
            capture_output=True, text=True, timeout=5)
        title = r.stdout.strip()
        if not title.startswith("OK_"):
            return []

        r2 = subprocess.run(["osascript", "-e",
            f'tell application "Safari" to do JavaScript "localStorage.getItem(\'{key}\')" in document 1'],
            capture_output=True, text=True, timeout=30)
        raw = r2.stdout.strip()

        # Clean localStorage
        subprocess.run(["osascript", "-e",
            f'tell application "Safari" to do JavaScript "localStorage.removeItem(\'{key}\')" in document 1'],
            capture_output=True, timeout=5)

        data = json.loads(raw)
        records = data.get("output", data.get("block1", data.get("OutBlock_1", [])))
        return records if isinstance(records, list) else []
    except Exception as e:
        print(f"  [Safari] 에러: {e}")
        return []


def _safari_available() -> bool:
    """Safari에 KRX 로그인 세션이 있는지 확인."""
    try:
        r = subprocess.run(["osascript", "-e",
            'tell application "Safari" to get URL of document 1'],
            capture_output=True, text=True, timeout=5)
        return "krx.co.kr" in r.stdout
    except Exception:
        return False


def _fetch_safari_krx(date: str) -> dict:
    """Safari 로그인 세션으로 KRX 전종목 데이터 일괄 수집 (동기).
    Returns: {ticker: {per, pbr, eps, bps, div_yield, sector_name,
                       foreign_net_amt, inst_net_amt, indiv_net_amt,
                       short_balance, short_ratio, foreign_hold_ratio,
                       foreign_exhaust_rate, credit_balance, lending_balance}}
    """
    import time
    result = {}

    # 1) PER/PBR/EPS/BPS/배당/업종 (MDCSTAT03501)
    for mkt_id, label in [("STK", "KOSPI"), ("KSQ", "KOSDAQ")]:
        records = _safari_fetch("", {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT03501",
            "locale": "ko_KR", "mktId": mkt_id, "trdDd": date,
        }, key=f"krx_fund_{mkt_id}")
        for r in records:
            t = r.get("ISU_SRT_CD", "")
            if not t: continue
            result.setdefault(t, {})
            result[t]["per"] = _pf(r.get("PER"))
            result[t]["pbr"] = _pf(r.get("PBR"))
            result[t]["eps"] = _pf(r.get("EPS"))
            result[t]["bps"] = _pf(r.get("BPS"))
            result[t]["div_yield"] = _pf(r.get("DVD_YLD"))
            idn = r.get("IDX_IND_NM", "")
            if idn:
                result[t]["sector_name"] = idn
        print(f"  [Safari] {label} PER/PBR: {len(records)}종목")
        time.sleep(1)

    # 2) 투자자별 순매수 (MDCSTAT02401)
    for inv_code, prefix in [("9000", "foreign"), ("7050", "inst"), ("8000", "indiv")]:
        for mkt_id, label in [("STK", "KOSPI"), ("KSQ", "KOSDAQ")]:
            records = _safari_fetch("", {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT02401",
                "locale": "ko_KR", "strtDd": date, "endDd": date,
                "mktId": mkt_id, "invstTpCd": inv_code,
            }, key=f"krx_{prefix}_{mkt_id}")
            for r in records:
                t = r.get("ISU_SRT_CD", "")
                if not t: continue
                result.setdefault(t, {})
                result[t][f"{prefix}_net_qty"] = _pi(r.get("NETBID_TRDVOL"))
                result[t][f"{prefix}_net_amt"] = _pi(r.get("NETBID_TRDVAL"))
            print(f"  [Safari] {label} {prefix}: {len(records)}종목")
            time.sleep(1)

    # 3) 공매도 / 외인보유 / 신용잔고 / 대차잔고 — KRX 전종목 일괄 수집 불가
    # - 공매도: KRX → 금융투자협회로 redirect, 별도 세션 필요
    # - 외인보유: MDCSTAT03701은 종목별 시계열만 제공 (전종목 일괄 메뉴 없음)
    # - 신용잔고: KRX 정보데이터시스템에 메뉴 없음
    # - 대차잔고: 종목별 시계열만 제공
    # → 필요 시 KIS API 종목별 호출로 보강 (kis_daily_short_sale 등)

    print(f"  [Safari] 총 {len(result)}종목 수집")
    return result


async def _fetch_sector_info(date: str) -> dict:
    """KRX OPEN API 종목기본정보 → {ticker: {sector_name, list_shares}}"""
    if not KRX_API_KEY:
        return {}
    result = {}
    for cat, ep in [("sto", "stk_isu_base_info"), ("sto", "ksq_isu_base_info")]:
        try:
            async with aiohttp.ClientSession() as s:
                records = await _krx_openapi_get(s, cat, ep, date)
            for r in records:
                ticker = str(r.get("ISU_SRT_CD", "")).strip()
                if not ticker or len(ticker) != 6:
                    continue
                result[ticker] = {
                    "sector_name": str(r.get("SECT_TP_NM", "")).strip(),
                    "list_shares": _pi(r.get("LIST_SHRS")),
                }
            print(f"[KRX OPENAPI] {ep}: {len(records)}종목 섹터정보")
        except Exception as e:
            print(f"[KRX OPENAPI] {ep} 섹터정보 실패: {e}")
    return result


async def _fetch_kis_valuations(tickers: list) -> dict:
    """KIS API로 전종목 PER/PBR/EPS/BPS 일괄 수집.
    Returns: {ticker: {per, pbr, eps, bps}}
    """
    try:
        from kis_api import get_kis_token, kis_stock_price
    except ImportError:
        print("[KIS] kis_api import 실패")
        return {}

    token = await get_kis_token()
    if not token:
        print("[KIS] 토큰 발급 실패")
        return {}

    result = {}
    total = len(tickers)
    for i, ticker in enumerate(tickers):
        try:
            d = await kis_stock_price(ticker, token)
            result[ticker] = {
                "per": float(d.get("per", 0) or 0),
                "pbr": float(d.get("pbr", 0) or 0),
                "eps": float(d.get("eps", 0) or 0),
                "bps": float(d.get("bps", 0) or 0),
                "div_yield": float(d.get("stck_divi", 0) or 0),
            }
        except Exception:
            pass
        if (i + 1) % 100 == 0:
            print(f"[KIS] PER/PBR 수집: {i+1}/{total}")
        await asyncio.sleep(0.3)

    print(f"[KIS] PER/PBR 수집 완료: {len(result)}/{total}종목")
    return result


async def _fetch_consensus_batch(tickers: list) -> dict:
    """FnGuide 컨센서스 직렬 수집.
    Returns: {ticker: {consensus_target, consensus_count}}
    """
    try:
        from kis_api import fetch_fnguide_consensus
    except ImportError:
        print("[Consensus] kis_api import 실패")
        return {}

    result = {}
    total = len(tickers)
    loop = asyncio.get_running_loop()
    for i, ticker in enumerate(tickers):
        try:
            c = await loop.run_in_executor(None, fetch_fnguide_consensus, ticker)
            if c:
                ct = c.get("consensus_target", {})
                avg = int(ct.get("avg", 0) or 0) if isinstance(ct, dict) else int(ct or 0)
                if avg > 0:
                    bt = c.get("broker_targets", [])
                    result[ticker] = {
                        "consensus_target": avg,
                        "consensus_count": len(bt) if bt else 0,
                    }
        except Exception:
            pass
        if (i + 1) % 100 == 0:
            print(f"[Consensus] 수집: {i+1}/{total}")

    print(f"[Consensus] 수집 완료: {len(result)}/{total}종목")
    return result


async def update_daily_db(date: str = None) -> dict:
    """전종목 데이터 수집 후 DB 저장 (설계서 v2.0 기반 병렬 수집)."""
    if date is None:
        date = datetime.now(KST).strftime("%Y%m%d")
    os.makedirs(KRX_DB_DIR, exist_ok=True)
    print(f"[KRX] DB 갱신 시작: {date}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    # Task 1: KRX OPEN API → 시세/시총
    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    stocks = {}
    for mkt in ["STK", "KSQ"]:
        records = await fetch_krx_market_data(date, mkt)
        for r in records:
            stocks[r["ticker"]] = r
        await asyncio.sleep(0.5)

    if not stocks:
        msg = f"KRX 데이터 없음 (date={date}). 휴장일이거나 접근 차단."
        print(f"[KRX] {msg}")
        return {"error": msg}

    print(f"[KRX] 시세 수집 완료: {len(stocks)}종목")
    all_tickers = list(stocks.keys())

    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    # Task 2: Safari KRX 크롤링 (PER/PBR/수급/공매도/외인보유/신용/대차/업종)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    safari_data = {}
    safari_ok = False
    if _safari_available():
        print("[KRX] Safari 세션 감지 → KRX 크롤링 시작")
        safari_data = await asyncio.to_thread(_fetch_safari_krx, date)
        safari_ok = len(safari_data) > 0
        for ticker, vals in safari_data.items():
            if ticker in stocks:
                stocks[ticker].update(vals)
        print(f"[KRX] Safari 수집: {len(safari_data)}종목")
    else:
        print("[KRX] Safari 세션 없음 → KIS API fallback")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    # Task 3: KIS API PER/PBR fallback (Safari 실패 시)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    kis_count = 0
    if not safari_ok:
        kis_data = await _fetch_kis_valuations(all_tickers)
        kis_count = len(kis_data)
        for ticker, vals in kis_data.items():
            if ticker in stocks:
                stocks[ticker].update(vals)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    # Task 4: 컨센서스 (FnGuide, 병렬 sem=5 → 전종목 ~2분)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    consensus_data = await _fetch_consensus_batch(all_tickers)
    for ticker, vals in consensus_data.items():
        if ticker in stocks:
            close = stocks[ticker].get("close", 0)
            target = vals["consensus_target"]
            stocks[ticker]["consensus_target"] = target
            stocks[ticker]["consensus_count"] = vals["consensus_count"]
            stocks[ticker]["consensus_gap"] = round((target - close) / close * 100, 1) if close > 0 else 0

    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    # 기본값 + 비율 계산
    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    for s in stocks.values():
        for key in ["per", "pbr", "eps", "bps", "div_yield"]:
            s.setdefault(key, 0.0)
        for key in ["foreign_net_amt", "inst_net_amt", "indiv_net_amt",
                     "short_balance", "short_ratio", "foreign_hold_ratio",
                     "foreign_exhaust_rate", "credit_balance", "lending_balance"]:
            s.setdefault(key, 0)
        s.setdefault("sector_name", "")
        s.setdefault("list_shares", 0)
        s.setdefault("consensus_target", 0)
        s.setdefault("consensus_count", 0)
        s.setdefault("consensus_gap", 0)

        mcap = s.get("market_cap", 0)
        tv = s.get("trade_value", 0)
        f_amt = s.get("foreign_net_amt", 0)
        i_amt = s.get("inst_net_amt", 0)

        if mcap > 0:
            s["turnover"] = round(tv / mcap * 100, 4)
            s["foreign_ratio"] = round(f_amt / mcap * 100, 4)
            s["inst_ratio"] = round(i_amt / mcap * 100, 4)
            s["fi_ratio"] = round((f_amt + i_amt) / mcap * 100, 4)
        else:
            s["turnover"] = s["foreign_ratio"] = s["inst_ratio"] = s["fi_ratio"] = 0.0

    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    # 시장 요약
    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    kospi = [s for s in stocks.values() if s["market"] == "kospi"]
    kosdaq = [s for s in stocks.values() if s["market"] == "kosdaq"]
    market_summary = {
        "kospi_count": len(kospi),
        "kosdaq_count": len(kosdaq),
        "kospi_up": sum(1 for s in kospi if s["chg_pct"] > 0),
        "kospi_down": sum(1 for s in kospi if s["chg_pct"] < 0),
        "kosdaq_up": sum(1 for s in kosdaq if s["chg_pct"] > 0),
        "kosdaq_down": sum(1 for s in kosdaq if s["chg_pct"] < 0),
        "kospi_avg_chg": round(sum(s["chg_pct"] for s in kospi) / len(kospi), 2) if kospi else 0,
        "kosdaq_avg_chg": round(sum(s["chg_pct"] for s in kosdaq) / len(kosdaq), 2) if kosdaq else 0,
    }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    # 기술적 지표 + 추세 + 매물대 계산
    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    _compute_technicals(date, stocks)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    # 저장 (atomic write, 보관 무제한)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━
    cons_count = len(consensus_data)
    safari_count = len(safari_data)
    val_src = f"safari_krx({safari_count})" if safari_ok else f"KIS_API({kis_count})"
    supply_src = f"safari_krx({safari_count})" if safari_ok else "unavailable"
    db = {
        "date": date,
        "updated_at": datetime.now(KST).isoformat(),
        "source": {"price": "KRX_OPENAPI", "valuation": val_src,
                    "consensus": f"FnGuide({cons_count})", "supply": supply_src},
        "market_summary": market_summary,
        "count": len(stocks),
        "stocks": stocks,
    }
    filepath = os.path.join(KRX_DB_DIR, f"{date}.json")
    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)
    os.replace(tmp_path, filepath)

    size_kb = os.path.getsize(filepath) / 1024
    print(f"[KRX] DB 저장 완료: {filepath} ({size_kb:.0f}KB, {len(stocks)}종목, "
          f"PER/PBR={kis_count}, 컨센서스={cons_count})")

    return {
        "date": date,
        "count": len(stocks),
        "market_summary": market_summary,
        "file_size_kb": round(size_kb, 1),
        "kis_valuation_count": kis_count,
        "consensus_count": cons_count,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 스캐너
# ━━━━━━━━━━━━━━━━━━━━━━━━━
PRESETS = {
    "relative_strength": {
        "description": "시장평균 대비 등락률 +3% 이상 AND fi_ratio>0 (하락장에서 버틴 종목)",
        "sort": "fi_ratio",
    },
    "small_cap_buy": {
        "description": "시총 500~5000억 AND foreign_ratio>0.1% (소형주 외인매수)",
        "filters": {"market_cap_min": 500, "market_cap_max": 5000, "foreign_ratio_min": 0.1},
        "sort": "foreign_ratio",
    },
    "value": {
        "description": "PER>0 AND PER<10 AND PBR>0 AND PBR<1 AND 시총>1000억 (저평가)",
        "filters": {"per_min": 0.01, "per_max": 10, "pbr_min": 0.01, "pbr_max": 1, "market_cap_min": 1000},
        "sort": "pbr",
    },
    "momentum": {
        "description": "chg_pct>3% AND turnover>1% (모멘텀)",
        "filters": {"chg_pct_min": 3, "turnover_min": 1},
        "sort": "chg_pct",
    },
    "oversold": {
        "description": "등락률 -7% 이하 (낙폭과대)",
        "filters": {"chg_pct_max": -7},
        "sort": "chg_pct",
    },
    "foreign_streak": {
        "description": "최근 5일 연속 외인 순매수, 시총 500억 이상 (multi-day)",
        "filters": {"market_cap_min": 500},
        "sort": "cum_foreign_ratio",
    },
}


def _get_foreign_streak_data(target_date: str, days: int = 5) -> tuple[dict, int]:
    """최근 N일 연속 외인 순매수 종목 + 누적 foreign_ratio.

    Returns: ({ticker: cum_foreign_ratio}, days_available)
    가용 DB가 days보다 적으면 있는 만큼 사용.
    """
    if not os.path.exists(KRX_DB_DIR):
        return {}, 0
    files = sorted([f for f in os.listdir(KRX_DB_DIR) if f.endswith(".json")
                     and f[:8] <= target_date], reverse=True)[:days]
    if not files:
        return {}, 0

    days_available = len(files)

    # 각 날짜별 외인 순매수 양수인 종목 + foreign_ratio 누적
    candidates = None
    cum_ratio = {}  # ticker → 누적 foreign_ratio
    for fname in files:
        with open(os.path.join(KRX_DB_DIR, fname), encoding="utf-8") as f:
            db = json.load(f)
        daily_positive = set()
        for t, s in db.get("stocks", {}).items():
            if s.get("foreign_net_amt", 0) > 0:
                daily_positive.add(t)
                cum_ratio[t] = cum_ratio.get(t, 0) + s.get("foreign_ratio", 0)
        if candidates is None:
            candidates = daily_positive
        else:
            candidates &= daily_positive

    # 연속 매수 종목만 남기기
    result = {t: round(cum_ratio.get(t, 0), 4) for t in (candidates or set())}
    return result, days_available


def scan_stocks(db: dict, filters: dict, preset: str = None) -> dict:
    """필터 조건으로 종목 스캔.

    filters keys:
        market_cap_min/max (억원), chg_pct_min/max (%), foreign_ratio_min,
        fi_ratio_min, per_min/max, pbr_max, turnover_min,
        sort (str), n (int), market (kospi/kosdaq/all)

    Returns: {date, preset, filters, count, results: [...]}
    """
    stocks = db.get("stocks", {})
    date = db.get("date", "")

    # ── 프리셋 적용 ──
    preset_desc = None
    if preset and preset in PRESETS:
        p = PRESETS[preset]
        preset_desc = p.get("description", "")
        pf = p.get("filters", {})
        # 프리셋 필터를 기본값으로, 개별 파라미터로 오버라이드 가능
        merged = {**pf}
        for k, v in filters.items():
            if v is not None:
                merged[k] = v
        filters = merged
        if "sort" not in filters or filters.get("sort") is None:
            filters["sort"] = p.get("sort", "fi_ratio")

    # 필터 파라미터
    mcap_min = float(filters.get("market_cap_min", 0)) * 1_0000_0000       # 억원 → 원
    mcap_max = float(filters.get("market_cap_max", 9999999)) * 1_0000_0000
    chg_min = float(filters.get("chg_pct_min", -30))
    chg_max = float(filters.get("chg_pct_max", 30))
    fr_min = float(filters.get("foreign_ratio_min", -999))
    fi_min = float(filters.get("fi_ratio_min", -999))
    per_min = float(filters.get("per_min", 0))
    per_max = float(filters.get("per_max", 9999))
    pbr_min = float(filters.get("pbr_min", 0))
    pbr_max = float(filters.get("pbr_max", 9999))
    turn_min = float(filters.get("turnover_min", 0))
    sort_by = filters.get("sort", "fi_ratio")
    n = int(filters.get("n", 30))
    n = max(1, min(n, 100))
    market_filter = filters.get("market", "all")

    # 시장 평균 등락률
    summary = db.get("market_summary", {})
    market_avg_chg = round(
        (summary.get("kospi_avg_chg", 0) + summary.get("kosdaq_avg_chg", 0)) / 2, 2)

    # relative_strength: 동적 chg_pct_min
    if preset == "relative_strength":
        if "chg_pct_min" not in filters or filters["chg_pct_min"] == chg_min:
            chg_min = market_avg_chg + 3.0
        fi_min = max(fi_min, 0)

    # foreign_streak: 연속 매수 종목 + 누적 비율
    streak_data = None   # {ticker: cum_foreign_ratio}
    days_available = 0
    if preset == "foreign_streak":
        streak_days = max(2, int(filters.get("streak_days", 5)))
        streak_data, days_available = _get_foreign_streak_data(date, streak_days)
        if days_available < streak_days:
            preset_desc = f"최근 {days_available}/{streak_days}일 연속 외인 순매수 (DB 부족)"
        if not streak_data:
            return {
                "date": date,
                "preset": preset,
                "preset_description": preset_desc,
                "filters": _summarize_filters(filters),
                "market_avg_chg": market_avg_chg,
                "days_available": days_available,
                "total_matched": 0,
                "count": 0,
                "results": [],
                "note": f"연속 매수 종목 없음 (가용 DB: {days_available}/{streak_days}일)",
            }

    # ── 필터링 ──
    results = []
    for ticker, s in stocks.items():
        mcap = s.get("market_cap", 0)
        if mcap < mcap_min or mcap > mcap_max:
            continue
        chg = s.get("chg_pct", 0)
        if chg < chg_min or chg > chg_max:
            continue
        fr = s.get("foreign_ratio", 0)
        if fr < fr_min:
            continue
        fi = s.get("fi_ratio", 0)
        if fi < fi_min:
            continue
        per = s.get("per", 0)
        if per_min > 0 and (per < per_min or per > per_max):
            continue
        if per_max < 9999 and per > per_max:
            continue
        pbr = s.get("pbr", 0)
        if pbr_min > 0 and pbr < pbr_min:
            continue
        if pbr_max < 9999 and pbr > pbr_max:
            continue
        turn = s.get("turnover", 0)
        if turn < turn_min:
            continue
        if market_filter != "all":
            if s.get("market", "") != market_filter:
                continue
        if streak_data is not None and ticker not in streak_data:
            continue

        item = {
            "ticker": ticker,
            "name": s.get("name", ticker),
            "market": s.get("market", ""),
            "close": s.get("close", 0),
            "chg_pct": chg,
            "market_cap": round(mcap / 1_0000_0000),   # 억원
            "per": per,
            "pbr": pbr,
            "foreign_ratio": fr,
            "inst_ratio": s.get("inst_ratio", 0),
            "fi_ratio": fi,
            "turnover": turn,
        }
        if streak_data is not None:
            item["cum_foreign_ratio"] = streak_data.get(ticker, 0)
        results.append(item)

    # ── 정렬 ──
    reverse = True
    if sort_by in ("per", "pbr"):
        reverse = False  # PER/PBR은 낮은순
    if sort_by == "chg_pct" and preset == "oversold":
        reverse = False  # 낙폭 큰 순
    results.sort(key=lambda x: x.get(sort_by, 0), reverse=reverse)
    total_matched = len(results)
    results = results[:n]

    out = {
        "date": date,
        "preset": preset,
        "preset_description": preset_desc,
        "filters": _summarize_filters(filters),
        "market_avg_chg": market_avg_chg,
        "total_matched": total_matched,
        "count": len(results),
        "results": results,
    }
    if preset == "foreign_streak":
        out["days_available"] = days_available
    return out


def _summarize_filters(filters: dict) -> dict:
    """필터 요약 (내부 표시용)."""
    summary = {}
    keys = ["market_cap_min", "market_cap_max", "chg_pct_min", "chg_pct_max",
            "foreign_ratio_min", "fi_ratio_min", "per_min", "per_max",
            "pbr_min", "pbr_max", "turnover_min", "sort", "n", "market"]
    for k in keys:
        v = filters.get(k)
        if v is not None:
            summary[k] = v
    return summary
