"""
KRX 전종목 일별 데이터 크롤러
- 전종목 시세 (종가, 등락률, 거래량, 거래대금, 시총)
- 전종목 PER/PBR (기본정보)
- 투자자별 수급 (외인, 기관, 개인 순매수)
- 비율 계산 (foreign_ratio, inst_ratio, fi_ratio, turnover)
- DB: /data/krx_db/YYYYMMDD.json
"""

import aiohttp
import asyncio
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
KRX_DB_DIR = "/data/krx_db"
KRX_JSON_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

KRX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101",
}

KRX_PROXY = os.environ.get("KRX_PROXY", "")


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
# KRX HTTP
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
# 1) 전종목 시세 — MDCSTAT01501
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def fetch_krx_market_data(date: str, market: str = "STK") -> list[dict]:
    """전종목 시세 크롤링. market: STK(코스피), KSQ(코스닥)"""
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
    except Exception as e:
        print(f"[KRX] {market} 시세 직접호출 실패: {e} → pykrx fallback")
        return await _market_data_pykrx(date, market)

    mkt_label = "kospi" if market == "STK" else "kosdaq"
    result = []
    for r in records:
        ticker = r.get("ISU_SRT_CD", "")
        if not ticker or len(ticker) != 6:
            continue
        result.append({
            "ticker": ticker,
            "name": r.get("ISU_ABBRV", ""),
            "market": mkt_label,
            "close": _pi(r.get("TDD_CLSPRC")),
            "chg_pct": _pf(r.get("FLUC_RT")),
            "volume": _pi(r.get("ACC_TRDVOL")),
            "trade_value": _pi(r.get("ACC_TRDVAL")),
            "market_cap": _pi(r.get("MKTCAP")),
        })
    print(f"[KRX] {market} 시세: {len(result)}종목")
    return result


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
# 2) 전종목 PER/PBR — MDCSTAT03901
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def fetch_krx_fundamental(date: str, market: str = "STK") -> dict:
    """전종목 PER/PBR. Returns {ticker: {per, pbr}}"""
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
        result = {}
        for r in records:
            ticker = r.get("ISU_SRT_CD", "")
            if ticker:
                result[ticker] = {
                    "per": _pf(r.get("PER", "0")),
                    "pbr": _pf(r.get("PBR", "0")),
                }
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
    path = "/data/stock_universe.json"
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


async def update_daily_db(date: str = None) -> dict:
    """전종목 시세+수급 크롤링 후 DB 저장."""
    if date is None:
        date = datetime.now(KST).strftime("%Y%m%d")
    os.makedirs(KRX_DB_DIR, exist_ok=True)
    print(f"[KRX] DB 갱신 시작: {date}")

    # ── 1) 시세 데이터 (코스피 + 코스닥) ──
    stocks = {}
    for mkt in ["STK", "KSQ"]:
        records = await fetch_krx_market_data(date, mkt)
        for r in records:
            stocks[r["ticker"]] = r
        await asyncio.sleep(1)

    if not stocks:
        msg = f"KRX 데이터 없음 (date={date}). 휴장일이거나 접근 차단."
        print(f"[KRX] {msg}")
        return {"error": msg}

    # ── 2) PER/PBR ──
    for mkt in ["STK", "KSQ"]:
        fund = await fetch_krx_fundamental(date, mkt)
        for ticker, vals in fund.items():
            if ticker in stocks:
                stocks[ticker]["per"] = vals["per"]
                stocks[ticker]["pbr"] = vals["pbr"]
        await asyncio.sleep(1)

    # PER/PBR 기본값
    for s in stocks.values():
        s.setdefault("per", 0.0)
        s.setdefault("pbr", 0.0)

    # ── 3) 투자자별 수급 ──
    investor_data_available = False
    for mkt in ["STK", "KSQ"]:
        inv = await fetch_krx_investor_data(date, mkt)
        if inv:
            investor_data_available = True
        for ticker, vals in inv.items():
            if ticker in stocks:
                stocks[ticker].update(vals)
        await asyncio.sleep(1)

    # 수급 기본값 + 비율 계산
    for s in stocks.values():
        for key in ["foreign_net_qty", "foreign_net_amt",
                     "inst_net_qty", "inst_net_amt",
                     "indiv_net_qty", "indiv_net_amt"]:
            s.setdefault(key, 0)

        mcap = s.get("market_cap", 0)
        f_amt = s["foreign_net_amt"]
        i_amt = s["inst_net_amt"]
        tv = s.get("trade_value", 0)

        if mcap > 0:
            s["foreign_ratio"] = round(f_amt / mcap * 100, 4)
            s["inst_ratio"] = round(i_amt / mcap * 100, 4)
            s["fi_ratio"] = round((f_amt + i_amt) / mcap * 100, 4)
            s["turnover"] = round(tv / mcap * 100, 4)
        else:
            s["foreign_ratio"] = 0.0
            s["inst_ratio"] = 0.0
            s["fi_ratio"] = 0.0
            s["turnover"] = 0.0

    # ── 4) 시장 요약 ──
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

    # ── 5) 저장 (atomic write) ──
    db = {
        "date": date,
        "updated_at": datetime.now(KST).isoformat(),
        "investor_data_available": investor_data_available,
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
    print(f"[KRX] DB 저장 완료: {filepath} ({size_kb:.0f}KB, {len(stocks)}종목)")

    _cleanup_old_db(30)

    return {
        "date": date,
        "count": len(stocks),
        "market_summary": market_summary,
        "file_size_kb": round(size_kb, 1),
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
        "description": "PER<10 AND PBR<1 AND 시총>1000억 (저평가)",
        "filters": {"per_min": 0.01, "per_max": 10, "pbr_max": 1, "market_cap_min": 1000},
        "sort": "fi_ratio",
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
        "description": "최근 5일 연속 외인 순매수 (multi-day)",
        "sort": "foreign_ratio",
    },
}


def _get_foreign_streak_tickers(target_date: str, days: int = 5) -> set:
    """최근 N일 연속 외인 순매수 종목 집합."""
    if not os.path.exists(KRX_DB_DIR):
        return set()
    files = sorted([f for f in os.listdir(KRX_DB_DIR) if f.endswith(".json")
                     and f[:8] <= target_date], reverse=True)[:days]
    if len(files) < days:
        return set()

    # 첫 번째(최신) 파일의 전 종목을 후보로
    candidates = None
    for fname in files:
        with open(os.path.join(KRX_DB_DIR, fname), encoding="utf-8") as f:
            db = json.load(f)
        positive = {t for t, s in db.get("stocks", {}).items()
                     if s.get("foreign_net_amt", 0) > 0}
        if candidates is None:
            candidates = positive
        else:
            candidates &= positive
    return candidates or set()


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
    pbr_max = float(filters.get("pbr_max", 9999))
    turn_min = float(filters.get("turnover_min", 0))
    sort_by = filters.get("sort", "fi_ratio")
    n = int(filters.get("n", 30))
    n = max(1, min(n, 100))
    market_filter = filters.get("market", "all")

    # relative_strength: 동적 chg_pct_min
    if preset == "relative_strength":
        summary = db.get("market_summary", {})
        avg_chg = (summary.get("kospi_avg_chg", 0) + summary.get("kosdaq_avg_chg", 0)) / 2
        if "chg_pct_min" not in filters or filters["chg_pct_min"] == chg_min:
            chg_min = avg_chg + 3.0
        fi_min = max(fi_min, 0)

    # foreign_streak: 연속 매수 종목 필터
    streak_tickers = None
    if preset == "foreign_streak":
        streak_tickers = _get_foreign_streak_tickers(date)
        if not streak_tickers:
            return {
                "date": date,
                "preset": preset,
                "preset_description": preset_desc,
                "filters": _summarize_filters(filters),
                "count": 0,
                "results": [],
                "note": f"최근 5일 DB 파일 부족 또는 연속 매수 종목 없음",
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
        if pbr_max < 9999 and pbr > pbr_max:
            continue
        turn = s.get("turnover", 0)
        if turn < turn_min:
            continue
        if market_filter != "all":
            if s.get("market", "") != market_filter:
                continue
        if streak_tickers is not None and ticker not in streak_tickers:
            continue

        results.append({
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
        })

    # ── 정렬 ──
    reverse = True
    if sort_by in ("per", "pbr"):
        reverse = False  # PER/PBR은 낮은순이 유용
    if sort_by == "chg_pct" and preset == "oversold":
        reverse = False  # 낙폭 큰 순
    results.sort(key=lambda x: x.get(sort_by, 0), reverse=reverse)
    total_matched = len(results)
    results = results[:n]

    return {
        "date": date,
        "preset": preset,
        "preset_description": preset_desc,
        "filters": _summarize_filters(filters),
        "total_matched": total_matched,
        "count": len(results),
        "results": results,
    }


def _summarize_filters(filters: dict) -> dict:
    """필터 요약 (내부 표시용)."""
    summary = {}
    keys = ["market_cap_min", "market_cap_max", "chg_pct_min", "chg_pct_max",
            "foreign_ratio_min", "fi_ratio_min", "per_min", "per_max",
            "pbr_max", "turnover_min", "sort", "n", "market"]
    for k in keys:
        v = filters.get(k)
        if v is not None:
            summary[k] = v
    return summary
