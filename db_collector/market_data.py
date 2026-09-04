"""매크로 일봉 시계열(#2) + 시장 투자자 flow 시계열(#3) 저장.

배경: 레짐(`kis_api/regime.py`)의 `foreign_5d` 지표와 SAT_PORT_CHECK 매크로 8변수
임계판정(`kis_api/polymarket.py fetch_external_macro_signals`)은 그동안 라이브 조회만
하고 시계열을 저장하지 않아 각각 "미수집"/"확인불가" 상태였다. 이 모듈이 일봉 시계열을
stock.db에 적재하고, 소비자는 point-in-time 헬퍼(`latest_asof`/`series_asof_window`)로
읽어간다. 판정 로직 자체는 변경하지 않는다 (지표 채움·표시 전용).

⚠️ 정렬 원칙 (룩어헤드 방지, 반드시 지킬 것):
각 시리즈는 "자기 시장의 거래일"로 저장한다. 예) KOSPI는 KST 기준 그날 종가일,
S&P500/VIX/DXY 등 미국 시리즈는 미국 동부시간 세션 종가일 — 이 세션은 KST로 보면
당일 밤~익일 05:00경에야 확정된다. 따라서 "KST 시점에 이미 완료된 최신 값"만
쓰려면 미국 시리즈는 최소 하루를 늦춰 조회해야 한다 (당일 새벽 잡이 "어제 KST 저녁에
아직 진행 중이던 미국장" 데이터를 미래값처럼 끌어쓰는 사고 방지).
`latest_asof()` / `series_asof_window()`가 이 컷오프 규칙을 강제한다:
  - US 시리즈 (nasdaq/sp500/vix/us10y/us2y/dxy/wti/gold): date <= kst_date - 1일
  - KR/FX 시리즈 (kospi/kosdaq/usdkrw): date <= kst_date

FDR 버전 메모: 설치된 FinanceDataReader(0.9.110) DataReader()는 `fill_method` 인자를
받지 않는다(TypeError) — 상위 지시문의 "fill_method=None 주의"는 이 설치 버전에는
해당하지 않아 미적용. 대신 FDR은 실제 거래일에만 행을 반환하므로(주말/휴장일 forward-fill
없음) 별도 조치 없이 "값=종가" 원칙이 지켜진다 (2026-09-03 실측 확인).
"""
import sqlite3
import warnings
from datetime import datetime, timedelta

from kis_api import KST
# ⚠️ kis_api.kr_stock는 함수 내부(collect_market_flow_daily)에서 lazy import한다 —
# db_collector/__init__.py의 _BACKING에 이 모듈이 등록되면서(2026-09 W7) 패키지
# 로드 시점 순환 import 위험을 없애기 위함 (db_collector 패키지 초기화 중 kis_api 전체
# 로드를 트리거하지 않도록).

from ._db import _get_db, db_write_lock

import asyncio


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 매크로 시리즈 정의 — source(fdr/yf) + symbol + group(룩어헤드 컷오프 분류)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MACRO_SERIES = {
    "kospi":  {"source": "fdr", "symbol": "KS11",     "group": "KR"},
    "kosdaq": {"source": "fdr", "symbol": "KQ11",     "group": "KR"},
    "usdkrw": {"source": "fdr", "symbol": "USD/KRW",  "group": "FX"},
    "nasdaq": {"source": "fdr", "symbol": "IXIC",     "group": "US"},
    "sp500":  {"source": "yf",  "symbol": "^GSPC",    "group": "US"},
    "vix":    {"source": "yf",  "symbol": "^VIX",     "group": "US"},
    "us10y":  {"source": "yf",  "symbol": "^TNX",     "group": "US"},
    # 2Y 국채 선물 — 야후 "^IRX"는 13주(3M)물이라 2Y가 아님. "2YY=F"(2-Year T-Note
    # futures)로 대체 — 2026-09-03 실측 정상 응답 확인 (지시문의 "없으면 skip"은 불필요).
    "us2y":   {"source": "yf",  "symbol": "2YY=F",    "group": "US"},
    "dxy":    {"source": "yf",  "symbol": "DX-Y.NYB", "group": "US"},
    "wti":    {"source": "yf",  "symbol": "CL=F",     "group": "US"},
    "gold":   {"source": "yf",  "symbol": "GC=F",     "group": "US"},
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 스키마 (idempotent — kis_api/polymarket.py _ensure_pension_table 패턴 참고)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _ensure_market_data_tables(conn: sqlite3.Connection) -> None:
    """macro_daily / market_flow_daily 테이블 생성 (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_daily (
            series     TEXT NOT NULL,
            date       TEXT NOT NULL,
            value      REAL,
            source     TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            PRIMARY KEY (series, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_macro_daily_series ON macro_daily(series)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_flow_daily (
            date       TEXT NOT NULL,
            market     TEXT NOT NULL,
            frgn_net   INTEGER,
            orgn_net   INTEGER,
            prsn_net   INTEGER,
            updated_at TEXT DEFAULT '',
            PRIMARY KEY (date, market)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_flow_date ON market_flow_daily(date)")
    conn.commit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# #2 매크로 일봉 시계열
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _fetch_series_closes(meta: dict, start: str, end: str) -> list:
    """(date "YYYY-MM-DD", value) 리스트. FDR/yfinance 종가만. 실패 시 []."""
    symbol = meta["symbol"]
    if meta["source"] == "fdr":
        import FinanceDataReader as fdr
        df = fdr.DataReader(symbol, start, end)
        if df is None or df.empty or "Close" not in df.columns:
            return []
        return [(idx.strftime("%Y-%m-%d"), float(v)) for idx, v in df["Close"].dropna().items()]
    else:
        import yfinance as yf
        end_excl = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.Ticker(symbol).history(start=start, end=end_excl, auto_adjust=True)
        if df is None or df.empty or "Close" not in df.columns:
            return []
        return [(idx.strftime("%Y-%m-%d"), float(v)) for idx, v in df["Close"].dropna().items()]


def _fetch_macro_rows(latest_map: dict, today_str: str, backfill_from: str) -> dict:
    """동기 함수 (FDR/yfinance 모두 blocking I/O) — series별 신규 (date, value) 행을 조회.

    DB에 접근하지 않는다 — latest_map은 호출부(collect_macro_daily)가 db_write_lock
    안에서 미리 읽어 전달한다. 호출부가 asyncio.to_thread로 이 함수를 오프로드해
    blocking I/O가 이벤트루프를 막지 않게 한다 (CLAUDE.md 비동기 규칙).

    시리즈 하나 실패해도 나머지는 진행(per-series try/except, 침묵-0 금지 원칙에 따라
    실패는 "error:..." 문자열로 구분).

    ⚠️ W9(2026-09-04): "latest >= today_str면 조기 종료"(rows=[])를 제거했다 — 그
    분기 때문에 같은 날 두 번째 이후 실행(수동 백필/장애 복구 재실행 포함)이 통째로
    no-op이 되어, 예를 들어 장중에 넣은 잠정 수급 행이 저녁 정기 잡에서 갱신되지
    못하고 그대로 고정되는 결함이 있었다. INSERT OR REPLACE라 같은 날 재조회는
    안전하게 멱등이므로 항상 latest-5일(backfill_from보다 이르면 backfill_from로
    clamp)부터 오늘까지 재조회한다.

    Returns: {series: [(date_str, value), ...] | "error:..."}
    """
    results = {}
    for series, meta in MACRO_SERIES.items():
        try:
            latest = latest_map.get(series)
            if latest:
                # latest+1일이 아니라 latest-5일부터 재조회(W1+B1). INSERT OR REPLACE라
                # 진행중 봉(당일 16시 KST 시점의 WTI/금/DXY/10Y 등 아직 마감 전 종가)이
                # 다음 실행에서 확정치로 자동 덮인다. backfill_from보다 이르게 내려가지
                # 않도록 max로 clamp.
                start_dt = datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=5)
                start = max(backfill_from, start_dt.strftime("%Y-%m-%d"))
            else:
                start = backfill_from

            results[series] = _fetch_series_closes(meta, start, today_str)
        except Exception as e:
            results[series] = f"error:{e}"
    return results


async def collect_macro_daily(backfill_from: str = "2024-01-01") -> dict:
    """시리즈별 macro_daily max(latest date-5일, backfill_from)(latest 없으면 backfill_from)
    ~오늘까지 수집 → INSERT OR REPLACE. 이미 오늘 행이 있어도 조기 종료하지 않는다(W9,
    아래 `_fetch_macro_rows` 참고) — INSERT OR REPLACE라 재실행은 항상 멱등.

    fetch(FDR/yfinance, blocking I/O)와 write(INSERT~commit)를 분리한다: fetch는
    asyncio.to_thread로 이벤트루프 밖에서 수행하고, write는 db_write_lock 안에서
    connect~commit 구간 전체를 한 트랜잭션으로 처리한다 (CLAUDE.md DB 쓰기 불변식 —
    네트워크 fetch/sleep은 락 밖, 락 안에서는 await 없이 커밋까지 완료).
    `_ensure_market_data_tables` DDL과 최신 date 조회도 별도 락 블록에서 먼저 수행해
    (동시에 도는 flow 수집기와의) DDL 경합을 피한다.

    Returns: {series: rows_written(int) | "error:..."}
    """
    conn = _get_db()
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    now_iso = datetime.now(KST).isoformat()

    latest_map = {}
    async with db_write_lock:
        _ensure_market_data_tables(conn)
        for series in MACRO_SERIES:
            row = conn.execute(
                "SELECT MAX(date) AS d FROM macro_daily WHERE series=?", (series,)
            ).fetchone()
            latest_map[series] = row["d"] if row and row["d"] else None

    fetched = await asyncio.to_thread(_fetch_macro_rows, latest_map, today_str, backfill_from)

    results = {}
    async with db_write_lock:
        for series, meta in MACRO_SERIES.items():
            rows_or_err = fetched.get(series)
            if isinstance(rows_or_err, str):  # "error:..."
                results[series] = rows_or_err
                continue
            written = 0
            for d_str, val in rows_or_err:
                if val is None:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO macro_daily "
                    "(series, date, value, source, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (series, d_str, float(val), meta["source"], now_iso),
                )
                written += 1
            results[series] = written
        conn.commit()

    conn.close()
    return results


def series_asof_window(series: str, kst_date: str, n: int = 1, conn: sqlite3.Connection = None) -> list:
    """룩어헤드 컷오프 적용 후 최근 n행 [{date, value}, ...] (최신→과거 DESC).

    US 시리즈는 date <= kst_date - 1일, KR/FX 시리즈는 date <= kst_date 까지만 조회
    (모듈 docstring "정렬 원칙" 참조). 알 수 없는 series는 KR과 동일 취급(보수적으로
    당일까지만 — 미확인 시리즈를 US처럼 하루 당겨 잘못 룩어헤드-차단하지 않도록).
    """
    meta = MACRO_SERIES.get(series)
    group = meta["group"] if meta else "KR"
    cutoff = kst_date
    if group == "US":
        cutoff_dt = datetime.strptime(kst_date, "%Y-%m-%d") - timedelta(days=1)
        cutoff = cutoff_dt.strftime("%Y-%m-%d")

    close_here = conn is None
    if conn is None:
        conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT date, value FROM macro_daily WHERE series=? AND date<=? "
            "ORDER BY date DESC LIMIT ?",
            (series, cutoff, n),
        ).fetchall()
    finally:
        if close_here:
            conn.close()
    return [{"date": r["date"], "value": r["value"]} for r in rows]


def latest_asof(series: str, kst_date: str, conn: sqlite3.Connection = None) -> dict | None:
    """룩어헤드 방지 point-in-time 최신값 1건. {"date", "value"} 또는 None."""
    w = series_asof_window(series, kst_date, n=1, conn=conn)
    return w[0] if w else None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# #3 시장 투자자 flow 시계열 (KOSPI/KOSDAQ)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def collect_market_flow_daily(token: str, backfill_from: str = "2024-01-01") -> dict:
    """KSP/KSQ 시장 투자자 flow 시계열 DB max(최신 date-5일, backfill_from) ~ 오늘 수집
    (최신 없으면 backfill_from부터, macro와 동일 시그니처).

    네트워크 fetch(+청크 간 sleep)는 락 밖에서 수행하고, INSERT~commit 트랜잭션
    전체를 db_write_lock 안에서 한 블록으로 처리한다 (CLAUDE.md DB 쓰기 불변식).
    `_ensure_market_data_tables` DDL과 최신 date 조회도 별도 락 블록에서 먼저 수행해
    (동시에 도는 macro 수집기와의) DDL 경합을 피한다.

    ⚠️ 폴백 없음(2026-09 수정, B1): 범위 조회(`_fetch_market_investor_flow_range`)가
    빈 결과를 반환해도 예전처럼 벽시계 당일 1건 조회(`_fetch_market_investor_flow`)로
    폴백하지 않는다 — 실제 데이터가 없는 휴장일에도 "오늘 날짜"로 행을 만들어 저장하는
    침묵 오염이었다. 빈 응답은 그대로 0건으로 기록(다음 실행이 latest-5일부터 자동
    재조회해 거둔다).

    ⚠️ 침묵-0 방어(W4): 조회된 rows가 있는데 전부 frgn=orgn=prsn=0이면(KIS iscd 불일치
    등 침묵-0 함정 재발 신호, kis_api/kr_stock.py `_MARKET_IDX_CODE` 주석 참고) 저장하지
    않고 {"error": "all_zero", "market": ...}로 표시해 잡의 silent-failure 카운트를 유도한다.

    ⚠️ W9(2026-09-04): "latest >= today면 조기 종료"(rows_written=0)를 제거했다 — 오늘
    이미 (장중 잠정치 등으로) 행이 있어도, 같은 날 재실행이 재조회 없이 no-op이 되어
    당일 행이 잠정치로 고정되는 결함이었다. `_fetch_market_investor_flow_range`가
    `date > end_yyyymmdd`(=오늘) 행을 이미 걸러내므로(kis_api/kr_stock.py) 룩어헤드
    걱정 없이 항상 재조회한다 — INSERT OR REPLACE라 멱등, 비용은 시장당 API 1~2콜.

    Returns: {"KSP": rows_written(int) | {"error": "all_zero", ...} | "error:...", "KSQ": ...}
    """
    conn = _get_db()

    today_dt = datetime.now(KST)
    today_yyyymmdd = today_dt.strftime("%Y%m%d")
    backfill_yyyymmdd = backfill_from.replace("-", "")

    latest_map = {}
    async with db_write_lock:
        _ensure_market_data_tables(conn)
        for market in ("KSP", "KSQ"):
            row = conn.execute(
                "SELECT MAX(date) AS d FROM market_flow_daily WHERE market=?", (market,)
            ).fetchone()
            latest_map[market] = row["d"] if row and row["d"] else None

    # W7: kis_api.kr_stock는 함수 내부에서 lazy import (db_collector._BACKING 등록 후
    # 패키지 로드 시점 순환 import 회피 — 모듈 상단 참고).
    from kis_api.kr_stock import _fetch_market_investor_flow_range

    results = {}
    for market in ("KSP", "KSQ"):
        try:
            latest = latest_map.get(market)
            if latest:
                start_dt = datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=5)
                start_yyyymmdd = max(backfill_yyyymmdd, start_dt.strftime("%Y%m%d"))
            else:
                start_yyyymmdd = backfill_yyyymmdd

            rows = await _fetch_market_investor_flow_range(token, market, start_yyyymmdd, today_yyyymmdd)

            if rows and all(
                r.get("frgn", 0) == 0 and r.get("orgn", 0) == 0 and r.get("prsn", 0) == 0
                for r in rows
            ):
                results[market] = {"error": "all_zero", "market": market}
                await asyncio.sleep(0.3)
                continue

            now_iso = datetime.now(KST).isoformat()
            written = 0
            async with db_write_lock:
                for r in rows:
                    date_raw = r.get("date")
                    if not date_raw or len(date_raw) != 8:
                        continue
                    date_str = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
                    conn.execute(
                        "INSERT OR REPLACE INTO market_flow_daily "
                        "(date, market, frgn_net, orgn_net, prsn_net, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (date_str, market, r.get("frgn", 0), r.get("orgn", 0), r.get("prsn", 0), now_iso),
                    )
                    written += 1
                conn.commit()
            results[market] = written
            await asyncio.sleep(0.3)
        except Exception as e:
            results[market] = f"error:{e}"

    conn.close()
    return results
