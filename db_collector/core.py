"""
DB 수집기 — KIS API 풀수집 + SQLite DB
- 매일: 기본시세 + 시간외 + 수급 + 공매도 → daily_snapshot
- 주 1회: 손익계산서 + 대차대조표 → financial_quarterly
- 기술지표 계산 → daily_snapshot UPDATE
- FnGuide 컨센서스 → daily_snapshot UPDATE

파일 구조:
  [1~70]    imports + 상수
  [71~130]  SQLite 연결 / 스키마 초기화
  [131~160] Rate Limiter
  [161~240] KRX OPEN API 함수 (krx_crawler.py에서 복사)
  [241~330] 섹터 분류 (krx_crawler.py에서 복사)
  [331~390] 종목 마스터 UPSERT
  [391~430] _collect_phase — 전종목 배치 수집
  [431~530] _store_daily_snapshot — daily_snapshot INSERT
  [531~560] collect_daily — 메인 수집 함수
  [561+]    하위호환 심볼 (main.py import용)
"""

import sqlite3
import asyncio
import aiohttp
import os
import json
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field

from kis_api import _get_session

# 설정 상수 및 거래일 판정 (_config.py 로 박리됨)
from ._config import (
    KST,
    _DATA_DIR,
    DB_PATH,
    KRX_DB_DIR,
    KRX_OPENAPI_BASE,
    KRX_API_KEY,
    _OPENAPI_ENDPOINTS,
    KRX_JSON_URL,
    KRX_HEADERS,
    _STD_SECTOR_MAP_PATH,
    _PHASE_TIMEOUT,
    _KR_MARKET_HOLIDAYS,
    _is_kr_trading_day,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Rate limiter 전역 세마포어
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_RATE_SEM = None  # collect_daily 시작 시 초기화


# DB 연결 / 스키마 초기화 / 쓰기 락 — _db.py 가 실소유자 (P3-1)
from ._db import db_write_lock, _get_db, _init_schema


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Rate Limiter
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def _rate_limited(coro):
    """초당 8건 제한 (세마포어 + jitter 슬립)."""
    import random
    async with _RATE_SEM:
        result = await coro
        await asyncio.sleep(0.10 + random.random() * 0.06)  # 0.10~0.16초
        return result


# KRX OPEN API 파서 + fetch 함수 — krx.py 가 실소유자 (P2b-4)
from .krx import (
    _pi,
    _pf,
    _krx_openapi_get,
    _krx_post,
    _parse_market_records,
    fetch_krx_market_data,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 섹터 분류 (krx_crawler.py에서 복사)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# 6자리 표준산업분류코드 → 섹터명
# 섹터 분류 데이터/함수 — sector.py 가 실소유자 (P2b-3)
from .sector import (
    _STD_CODE_TO_SECTOR,
    _SECTOR_KEYWORD_RULES,
    _SECTOR_CODE_DEFAULTS,
    _SECTOR_OVERRIDES,
    _classify_sector,
    _load_std_sector_map,
)


# 종목 마스터 UPSERT — master.py 가 실소유자 (P3-2)
from .master import _sync_stock_master, _update_master_from_basic


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase별 배치 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def _collect_phase(name: str, tickers: list, token: str,
                          session: aiohttp.ClientSession, fetch_fn) -> dict:
    """한 Phase 전종목 수집. Circuit breaker 내장.
    Returns {"results": {ticker: data}, "success": N, "failed": N[, "aborted": True]}
    """
    results = {}
    failed = 0

    async def _fetch_one(ticker):
        try:
            return ticker, await _rate_limited(fetch_fn(ticker, token, session))
        except Exception:
            return ticker, None

    # Circuit breaker: 첫 50종목 테스트
    probe_size = min(50, len(tickers))
    probe = tickers[:probe_size]
    probe_results = await asyncio.gather(*[_fetch_one(t) for t in probe])

    probe_fail = sum(1 for _, data in probe_results if data is None)
    for ticker, data in probe_results:
        if data is not None:
            results[ticker] = data
        else:
            failed += 1

    # 실패율 80% 이상이면 나머지 중단
    if probe_size > 0 and probe_fail / probe_size >= 0.8:
        remaining_count = len(tickers) - probe_size
        print(f"[{name}] Circuit breaker: {probe_fail}/{probe_size} 실패 → 나머지 {remaining_count}종목 스킵")
        return {
            "results": results,
            "success": len(results),
            "failed": failed + remaining_count,
            "aborted": True,
        }

    # 나머지 종목 실행
    remaining = tickers[probe_size:]
    if remaining:
        rem_results = await asyncio.gather(*[_fetch_one(t) for t in remaining])
        for ticker, data in rem_results:
            if data is not None:
                results[ticker] = data
            else:
                failed += 1

    return {"results": results, "success": len(results), "failed": failed}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# daily_snapshot INSERT
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _store_daily_snapshot(conn: sqlite3.Connection, date: str,
                           krx_data: dict, p1: dict, p2: dict, p3: dict, p4: dict):
    """4개 Phase 결과를 daily_snapshot에 INSERT OR REPLACE."""
    p1r = p1["results"]
    p2r = p2["results"]
    p3r = p3["results"]
    p4r = p4["results"]

    for ticker, krx in krx_data.items():
        try:
            basic = p1r.get(ticker, {})
            overtime = p2r.get(ticker, {})
            supply_raw = p3r.get(ticker, [])
            short_raw = p4r.get(ticker, [])

            # supply / short 는 리스트 반환 → 최신 1행
            supply = supply_raw[0] if isinstance(supply_raw, list) and supply_raw else {}
            short = short_raw[0] if isinstance(short_raw, list) and short_raw else {}

            # 수급(Phase 3) 데이터 부재 → 0이 아니라 NULL로 기록해 실패를 가시화한다.
            # (종전: 모두 0 → fetch 실패와 "실제 0" 구분 불가, 침묵 회귀의 원인)
            _has_supply = bool(supply)
            f_qty = int(supply.get("foreign_net",         0) or 0) if _has_supply else None
            f_amt = int(supply.get("foreign_net_amt",     0) or 0) if _has_supply else None
            i_qty = int(supply.get("institution_net",     0) or 0) if _has_supply else None
            i_amt = int(supply.get("institution_net_amt", 0) or 0) if _has_supply else None
            d_qty = int(supply.get("individual_net",      0) or 0) if _has_supply else None
            d_amt = int(supply.get("individual_net_amt",  0) or 0) if _has_supply else None

            # KIS 기본시세 필드 → KRX fallback
            close = int(basic.get("stck_prpr", 0) or 0)
            if close == 0:
                close = krx.get("close", 0)

            # market_cap fallback: KIS hts_avls 가 우선주/SPAC 등에 빈 응답 → listing_shares × close 로 계산
            # 단위: 억원 (KIS hts_avls 기준)
            mcap = int(basic.get("hts_avls", 0) or 0)
            if mcap == 0 and close > 0:
                listing = int(basic.get("lstn_stcn", 0) or 0)
                if listing > 0:
                    mcap = listing * close // 100000000  # 억원

            conn.execute("""
                INSERT OR REPLACE INTO daily_snapshot (
                    trade_date, symbol,
                    close, open, high, low, change_pct,
                    volume, trade_value, market_cap,
                    per, pbr, eps, bps, div_yield,
                    w52_high, w52_low, foreign_own_pct, listing_shares, turnover,
                    loan_balance_rate,
                    foreign_net_qty, foreign_net_amt, inst_net_qty, inst_net_amt,
                    indiv_net_qty, indiv_net_amt,
                    short_volume, short_ratio,
                    ovtm_close, ovtm_change_pct, ovtm_volume,
                    collected_at
                ) VALUES (
                    ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    datetime('now')
                )
            """, (
                date, ticker,
                close,
                int(basic.get("stck_oprc", 0) or 0) or krx.get("open", 0),
                int(basic.get("stck_hgpr", 0) or 0) or krx.get("high", 0),
                int(basic.get("stck_lwpr", 0) or 0) or krx.get("low", 0),
                float(basic.get("prdy_ctrt", 0) or 0) or krx.get("chg_pct", 0),
                int(basic.get("acml_vol", 0) or 0) or krx.get("volume", 0),
                int(basic.get("acml_tr_pbmn", 0) or 0) or krx.get("trade_value", 0),
                mcap,  # 억원 (hts_avls → listing_shares × close fallback)
                float(basic.get("per", 0) or 0),
                float(basic.get("pbr", 0) or 0),
                float(basic.get("eps", 0) or 0),
                float(basic.get("bps", 0) or 0),
                None,  # div_yield — 6c _recompute_div_yield_from_events(KIS-DPS÷종가)가 채움. NULL=미수집
                int(basic.get("w52_hgpr", 0) or 0),
                int(basic.get("w52_lwpr", 0) or 0),
                float(basic.get("hts_frgn_ehrt", 0) or 0),
                int(basic.get("lstn_stcn", 0) or 0),
                float(basic.get("vol_tnrt", 0) or 0),
                float(basic.get("whol_loan_rmnd_rate", 0) or 0),  # 신용잔고비율
                # 수급 (kis_investor_trend_history 변환 키). 금액은 KIS output2 `*_ntby_tr_pbmn`(원).
                # 부재 시 NULL(위에서 계산). pykrx refiner(_update_supply_in_snapshot)가 가용 시 정밀화.
                f_qty,
                f_amt,
                i_qty,
                i_amt,
                d_qty,
                d_amt,
                # 공매도 (FHPST04830000 응답 필드)
                int(short.get("short_vol", 0) or 0),
                float(short.get("short_ratio", 0) or 0),
                # 시간외 (kis_overtime_daily 반환 필드)
                int(overtime.get("ovtm_close", 0) or 0),
                float(overtime.get("ovtm_change_pct", 0) or 0),
                int(overtime.get("ovtm_volume", 0) or 0),
            ))
        except Exception as e:
            print(f"[DB] {ticker} snapshot INSERT 실패: {e}")

    conn.commit()


# _KR_MARKET_HOLIDAYS / _is_kr_trading_day — _config.py로 박리, 위 from ._config import 로 re-import됨.

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 수집 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def collect_daily(date: str = None) -> dict:
    """매일 장후 전종목 수집.
    Phase: KRX시세 → stock_master UPSERT → KIS기본시세 → 시간외 → 수급 → 공매도
           → daily_snapshot INSERT → 기술지표 계산 (→ Part 2)

    Returns:
        {"date": str, "phases": {...}, "total": int, "duration": float}
    """
    global _RATE_SEM
    _RATE_SEM = asyncio.Semaphore(8)

    if date is None:
        date = datetime.now(KST).strftime("%Y%m%d")

    # 주말 가드
    dt = datetime.strptime(date, "%Y%m%d")
    if dt.weekday() >= 5:  # 토(5), 일(6)
        print(f"[collect_daily] {date} 주말 → 스킵")
        return {"skipped": True, "reason": "weekend", "date": date}

    # 휴장일 가드 — 평일 공휴일에 KIS가 직전 영업일 시세를 반환해 spurious 행이 쌓이는 것을 차단
    if not _is_kr_trading_day(date):
        print(f"[collect_daily] {date} 휴장일 → 스킵")
        return {"skipped": True, "reason": "holiday", "date": date}

    report: dict = {"date": date, "phases": {}, "total": 0, "duration": 0.0}
    start = datetime.now()

    # 1. KRX OPEN API → 전종목 시세 (STK + KSQ, 각 2콜)
    all_stocks: dict = {}
    for mkt in ["STK", "KSQ"]:
        try:
            records = await fetch_krx_market_data(date, mkt)
            for r in records:
                all_stocks[r["ticker"]] = r
        except Exception as e:
            print(f"[collect_daily] KRX {mkt} 실패: {e}")
        await asyncio.sleep(0.5)

    # KRX 실패 시 stock_master에서 종목 리스트 fallback
    if not all_stocks:
        print("[collect_daily] KRX 데이터 없음 → stock_master fallback")
        conn = _get_db()
        rows = conn.execute("SELECT symbol, name, market FROM stock_master").fetchall()
        if not rows:
            conn.close()
            return {"error": "KRX 데이터 없음 + stock_master 비어있음", "date": date}
        for r in rows:
            all_stocks[r["symbol"]] = {"ticker": r["symbol"], "name": r["name"], "market": r["market"]}
        conn.close()
        print(f"[collect_daily] stock_master에서 {len(all_stocks)}종목 로드")

    tickers = list(all_stocks.keys())
    report["total"] = len(tickers)
    print(f"[collect_daily] {date} — 전종목 {len(tickers)}개 수집 시작")

    # 2. stock_master UPSERT
    conn = _get_db()
    try:
        _sync_stock_master(conn, list(all_stocks.values()))
    except Exception as e:
        print(f"[collect_daily] stock_master UPSERT 실패: {e}")

    # 3. KIS API Phase별 배치 수집
    from kis_api import (
        get_kis_token,
        kis_stock_price,
        kis_overtime_daily,
        kis_investor_trend_history,
        kis_daily_short_sale,
    )

    token = await get_kis_token()
    if not token:
        conn.close()
        return {"error": "KIS 토큰 발급 실패", "date": date}

    session = _get_session()
    # Phase 1: KIS 기본시세 + 밸류에이션 (FHKST01010100)
    print(f"[collect_daily] Phase 1/4 — 기본시세 {len(tickers)}종목")
    try:
        p1 = await asyncio.wait_for(
            _collect_phase("basic", tickers, token, session,
                           lambda t, tok, s: kis_stock_price(t, tok, session=s)),
            timeout=_PHASE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[collect_daily] Phase basic 타임아웃 ({_PHASE_TIMEOUT}초)")
        p1 = {"results": {}, "success": 0, "failed": len(tickers), "timeout": True}
    report["phases"]["basic"] = {
        "success": p1["success"], "failed": p1["failed"],
    }

    # Phase 1 후: sector_krx 자동 갱신 (신규 상장 종목 섹터 fallback)
    try:
        _update_master_from_basic(conn, p1["results"])
    except Exception as e:
        print(f"[collect_daily] sector_krx 갱신 실패: {e}")

    # Phase 2: 시간외 (FHPST02320000)
    print(f"[collect_daily] Phase 2/4 — 시간외")
    try:
        p2 = await asyncio.wait_for(
            _collect_phase("overtime", tickers, token, session,
                           lambda t, tok, s: kis_overtime_daily(t, tok, session=s)),
            timeout=_PHASE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[collect_daily] Phase overtime 타임아웃 ({_PHASE_TIMEOUT}초)")
        p2 = {"results": {}, "success": 0, "failed": len(tickers), "timeout": True}
    report["phases"]["overtime"] = {
        "success": p2["success"], "failed": p2["failed"],
    }

    # Phase 3: 투자자 수급 1일 (FHPTJ04160001)
    print(f"[collect_daily] Phase 3/4 — 수급")
    try:
        p3 = await asyncio.wait_for(
            _collect_phase("supply", tickers, token, session,
                           lambda t, tok, s: kis_investor_trend_history(t, tok, n_days=1, session=s)),
            timeout=_PHASE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[collect_daily] Phase supply 타임아웃 ({_PHASE_TIMEOUT}초)")
        p3 = {"results": {}, "success": 0, "failed": len(tickers), "timeout": True}
    report["phases"]["supply"] = {
        "success": p3["success"], "failed": p3["failed"],
    }

    # Phase 4: 공매도 1일 (FHPST04830000)
    print(f"[collect_daily] Phase 4/4 — 공매도")
    try:
        p4 = await asyncio.wait_for(
            _collect_phase("short", tickers, token, session,
                           lambda t, tok, s: kis_daily_short_sale(t, tok, n=1, session=s)),
            timeout=_PHASE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[collect_daily] Phase short 타임아웃 ({_PHASE_TIMEOUT}초)")
        p4 = {"results": {}, "success": 0, "failed": len(tickers), "timeout": True}
    report["phases"]["short"] = {
        "success": p4["success"], "failed": p4["failed"],
    }

    # 4-6, 6c. DB 쓰기 직렬화 — 네트워크 fetch(Phase 1-4) 완료 후 순수 sync 쓰기 구간.
    # 6b의 _update_supply_in_snapshot은 내부에 pykrx 네트워크+sleep이 있으므로
    # lock 밖에서 fetch하고 lock 안에서 write+commit한다. 나머지는 순수 sync.
    async with db_write_lock:
        # 4. daily_snapshot INSERT
        print(f"[collect_daily] daily_snapshot INSERT")
        _store_daily_snapshot(conn, date, all_stocks, p1, p2, p3, p4)

        # 5. 기술지표 계산 + UPDATE
        try:
            _compute_and_update(conn, date)
        except Exception as e:
            print(f"[collect_daily] 기술지표 계산 실패: {e}")

        # 6. FnGuide 컨센서스 UPDATE — daily_snapshot.consensus_target/count/gap
        # 5/8 fix: 미구현 상태였음 (학습 #13 "수집 성공 but 0값 함정" 재현)
        try:
            c_res = _update_consensus_in_snapshot(conn, date)
            report["consensus"] = c_res
        except Exception as e:
            print(f"[collect_daily] 컨센서스 갱신 실패: {e}")
            report["consensus"] = {"error": str(e)}

        # 6c. div_yield 재계산 — KIS 예탁원 DPS(dividend_events) ÷ 종가. ★KRX 불필요★.
        # events는 주간 collect_dividends가 갱신(DPS는 sticky). 여기선 당일 셀만 재계산(무 API, 저비용).
        # 종전: v2 수집기 div_yield=0.0 하드코딩 → 04-08~ 영구 0. 이제 KIS-DPS로 항상 산출.
        try:
            dv_res = _recompute_div_yield_from_events(conn, dates=[date])
            report["dividend"] = dv_res
        except Exception as e:
            print(f"[collect_daily] div_yield 재계산 실패: {e}")
            report["dividend"] = {"error": str(e)}

        conn.commit()

    # 6b. 외인/기관 수급 금액 정밀화 (pykrx refiner) — lock 밖에서 fetch, lock 안에서 write.
    # 1차값은 _store_daily_snapshot이 KIS FHPTJ04160001(`*_ntby_tr_pbmn`)로 이미 기록.
    # 여기서는 KRX 가용 시 정확한 원(KRW)으로 덮어쓰기만 한다(실패해도 1차값 보존).
    # ⚠️ _fetch_supply_data는 blocking pykrx HTTP+sleep → 반드시 lock 밖에서 실행.
    try:
        supply_fetched = _fetch_supply_data(date)
        async with db_write_lock:
            s_res = _write_supply_to_snapshot(conn, date, supply_fetched)
            conn.commit()
        report["supply"] = s_res
    except Exception as e:
        print(f"[collect_daily] 수급 정밀화 실패: {e}")
        report["supply"] = {"error": str(e)}

    conn.close()

    # 7. F/M/FCF 알파 메트릭 일괄 업데이트 (실패해도 collect_daily는 성공 취급)
    try:
        async with db_write_lock:
            alpha_res = update_all_alpha_metrics(trade_date=date)
        report["alpha"] = alpha_res
    except Exception as e:
        print(f"[collect_daily] 알파 메트릭 계산 실패: {e}")
        report["alpha"] = {"error": str(e)}

    report["duration"] = (datetime.now() - start).total_seconds()
    print(
        f"[collect_daily] 완료 — {len(tickers)}종목 "
        f"({report['duration']:.1f}s)"
    )
    return report


async def backfill_day_via_chart(date: str, tickers: list) -> dict:
    """FHKST03010100 일봉 차트로 단일 날짜 백필.

    KIS inquire-price (현재가) 새벽/휴장일 500 차단 우회.
    EOD 데이터라 안정적. OHLCV + PER/PBR/EPS/시총/신용잔고비율 채움.
    수급/공매도/시간외/52주고저는 NULL (별도 호출 필요).

    학습 #28 (잡 실행 ≠ 데이터 품질) 영구 대응 인프라.
    INSERT OR IGNORE: 기존 정상 행 보호.
    """
    from kis_api import get_kis_token, _kis_get
    token = await get_kis_token()
    conn = _get_db()
    ok, fail = 0, 0
    end_dt = (datetime.strptime(date, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    start_dt = (datetime.strptime(date, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d")
    timeout = aiohttp.ClientTimeout(total=8)
    for ticker in tickers:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as s:
                _, d = await _kis_get(s,
                    "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                    "FHKST03010100", token,
                    {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
                     "FID_INPUT_DATE_1": start_dt, "FID_INPUT_DATE_2": end_dt,
                     "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
            # KIS FHKST03010100: output1 = header (PER/PBR/EPS/시총/신용잔고비율),
            # output2 = candle 리스트 (OHLCV). 5/11 reviewer blocker — output2 에서
            # PER/PBR 등 읽으면 영구 0 INSERT (silent corruption).
            hdr = d.get("output1") or {}
            row = next((c for c in (d.get("output2") or [])
                        if c.get("stck_bsop_date") == date), None)
            if not row:
                fail += 1
                continue
            # write+commit 원자: 락 안에서, 네트워크 fetch는 위에서 락 밖에 끝남
            async with db_write_lock:
                conn.execute("""
                    INSERT OR IGNORE INTO daily_snapshot
                    (trade_date, symbol, close, open, high, low,
                     volume, trade_value, market_cap, per, pbr, eps,
                     loan_balance_rate, collected_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                """, (
                    date, ticker,
                    int(row.get("stck_clpr", 0) or 0),
                    int(row.get("stck_oprc", 0) or 0),
                    int(row.get("stck_hgpr", 0) or 0),
                    int(row.get("stck_lwpr", 0) or 0),
                    int(row.get("acml_vol", 0) or 0),
                    int(row.get("acml_tr_pbmn", 0) or 0),
                    int(hdr.get("hts_avls", 0) or 0),  # 억원 (KIS hts_avls 단위)
                    float(hdr.get("per", 0) or 0),
                    float(hdr.get("pbr", 0) or 0),
                    float(hdr.get("eps", 0) or 0),
                    float(hdr.get("itewhol_loan_rmnd_ratem", 0) or 0),
                ))
                conn.commit()
            ok += 1
            await asyncio.sleep(0.3)
        except Exception:
            fail += 1
    conn.close()
    return {"date": date, "ok": ok, "fail": fail}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 과거 날짜 안전 백필 (KIS 현재가 API 미사용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def collect_daily_backfill(date_str: str, *, _limit: int = 0, kis_history: bool = True) -> dict:
    """과거 영업일 daily_snapshot 백필.

    안전 원칙 (절대 위반 금지):
      - KIS 현재가 API (FHKST01010100 / kis_stock_price) 호출 금지.
        → 오늘 가격이 과거 날짜 행에 박히는 데이터 오염 방지.
      - PER/PBR/EPS/BPS/w52/div_yield/foreign_own_pct 등 시점 특정 불가 컬럼 → 0.
      - 소스별 시점 정합: KRX 데이터는 date_str 당일, KIS 히스토리는 date_str 행 필터.

    Sources:
      Phase A — KRX OPEN API: close, chg_pct, volume, trade_value, market_cap
      Phase B — KIS FHKST03010100 일봉 차트: open, high, low (OHLCV 히스토리, 안전)
      Phase C — KIS FHPTJ04160001 히스토리: foreign_net, institution_net, individual_net
      Phase D — KIS FHPST04830000 히스토리: short_vol, short_ratio
      Phase E — KIS FHPST04760000 히스토리: loan_balance_rate (credit_ratio)

    Args:
        date_str:    "YYYYMMDD" 형식 (예: "20260527")
        _limit:      0 = 전종목, N>0 = N종목만 처리 (dry-run용)
        kis_history: False 이면 Phase C/D/E (수급/공매도/신용잔고) 스킵.
                     KIS 서버 500 오류 등 장애 시 Phase A+B만 빠르게 백필할 때 사용.
                     스킵된 컬럼은 모두 0으로 저장됨.

    Returns:
        {"date": str, "inserted": N, "skipped": M, "errors": E, "elapsed_sec": float}
    """
    import time as _time
    t0 = _time.monotonic()

    # 주말 가드
    dt = datetime.strptime(date_str, "%Y%m%d")
    if dt.weekday() >= 5:
        return {"date": date_str, "skipped": 0, "inserted": 0, "errors": 0,
                "elapsed_sec": 0.0, "reason": "weekend"}

    # 휴장일 가드 — 공휴일 백필 시 spurious 행 방지
    if not _is_kr_trading_day(date_str):
        return {"date": date_str, "skipped": 0, "inserted": 0, "errors": 0,
                "elapsed_sec": 0.0, "reason": "holiday"}

    # ── Phase A: KRX 전종목 시세 ──────────────────────────────────────
    krx_by_ticker: dict = {}
    for mkt in ("STK", "KSQ"):
        try:
            rows = await fetch_krx_market_data(date_str, mkt)
            for r in rows:
                krx_by_ticker[r["ticker"]] = r
        except Exception as e:
            print(f"[backfill] KRX {mkt} 실패: {e}")
        await asyncio.sleep(0.5)

    if not krx_by_ticker:
        # fallback: stock_master
        conn = _get_db()
        ms_rows = conn.execute(
            "SELECT symbol, name, market FROM stock_master"
        ).fetchall()
        conn.close()
        for r in ms_rows:
            krx_by_ticker[r["symbol"]] = {
                "ticker": r["symbol"], "name": r["name"], "market": r["market"],
                "close": 0, "chg_pct": 0.0, "volume": 0,
                "trade_value": 0, "market_cap": 0,
            }
        if not krx_by_ticker:
            return {"date": date_str, "inserted": 0, "skipped": 0, "errors": 1,
                    "elapsed_sec": _time.monotonic() - t0,
                    "reason": "KRX 응답 없음 + stock_master 비어있음"}
        print(f"[backfill] KRX 빈 응답 → stock_master fallback ({len(krx_by_ticker)}종목)")

    tickers = list(krx_by_ticker.keys())
    if _limit > 0:
        tickers = tickers[:_limit]
        print(f"[backfill] dry-run: {_limit}종목만 처리")
    print(f"[backfill] {date_str} — {len(tickers)}종목 백필 시작")

    # ── Phase B: KIS 일봉 차트 → open/high/low ───────────────────────
    # FHKST03010100 output2 — 역사적 OHLCV. 현재가 API 아님 → 안전.
    from kis_api import get_kis_token, _kis_get
    token = await get_kis_token()

    chart_map: dict = {}  # ticker → {open, high, low}
    start_dt = (dt - timedelta(days=5)).strftime("%Y%m%d")
    end_dt   = (dt + timedelta(days=1)).strftime("%Y%m%d")

    # ── 청크 헬퍼: N개씩 나눠 gather (KIS 429/500 방지) ──────────────
    _BF_CHUNK = 50  # 한 번에 최대 50 동시 요청 (KIS 초당 10건 × 5초)

    async def _chunked_gather(coros):
        """코루틴 리스트를 _BF_CHUNK 개씩 묶어 순차 gather."""
        results = []
        coro_list = list(coros)
        for i in range(0, len(coro_list), _BF_CHUNK):
            chunk = coro_list[i:i + _BF_CHUNK]
            chunk_results = await asyncio.gather(*chunk)
            results.extend(chunk_results)
            if i + _BF_CHUNK < len(coro_list):
                await asyncio.sleep(0.5)  # chunk 간 0.5초 쉬어서 burst 완화
        return results

    async def _fetch_chart(ticker: str):
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as s:
                _, d = await _kis_get(
                    s,
                    "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                    "FHKST03010100", token,
                    {
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_INPUT_ISCD":         ticker,
                        "FID_INPUT_DATE_1":       start_dt,
                        "FID_INPUT_DATE_2":       end_dt,
                        "FID_PERIOD_DIV_CODE":    "D",
                        "FID_ORG_ADJ_PRC":        "0",
                    },
                )
            row = next(
                (c for c in (d.get("output2") or [])
                 if c.get("stck_bsop_date") == date_str),
                None,
            )
            if row:
                return ticker, {
                    "open":  int(row.get("stck_oprc", 0) or 0),
                    "high":  int(row.get("stck_hgpr", 0) or 0),
                    "low":   int(row.get("stck_lwpr", 0) or 0),
                }
        except Exception:
            pass
        await asyncio.sleep(0.1)
        return ticker, {}

    print(f"[backfill] Phase B — 일봉 차트 {len(tickers)}종목 (chunk={_BF_CHUNK})")
    chart_results = await _chunked_gather(_fetch_chart(t) for t in tickers)
    chart_ok = sum(1 for _, d in chart_results if d)
    for tk, data in chart_results:
        chart_map[tk] = data
    print(f"[backfill] Phase B 완료 — open/high/low 확보 {chart_ok}/{len(tickers)}종목")

    # ── Phases C/D/E: KIS 히스토리 (수급/공매도/신용잔고) ─────────────
    supply_map:  dict = {}  # ticker → {foreign_net, institution_net, individual_net}
    short_map:   dict = {}  # ticker → {short_vol, short_ratio}
    credit_map:  dict = {}  # ticker → {credit_ratio}

    if not kis_history:
        print(f"[backfill] Phase C/D/E 스킵 (supply/short/credit = 0)")
    else:
        from kis_api.kr_stock import (
            kis_investor_trend_history,
            kis_daily_short_sale,
            kis_daily_credit_balance,
        )
        from kis_api import _get_session as _ks

        async def _fetch_supply(ticker: str):
            try:
                sess = _ks()
                rows = await kis_investor_trend_history(ticker, token, n_days=10, session=sess)
                row = next((r for r in rows if r.get("date") == date_str), None)
                if row:
                    return ticker, {
                        "foreign_net":     int(row.get("foreign_net",     0) or 0),
                        "institution_net": int(row.get("institution_net", 0) or 0),
                        "individual_net":  int(row.get("individual_net",  0) or 0),
                    }
            except Exception:
                pass
            await asyncio.sleep(0.1)
            return ticker, {}

        async def _fetch_short(ticker: str):
            try:
                sess = _ks()
                rows = await kis_daily_short_sale(ticker, token, n=10, session=sess)
                row = next((r for r in rows if r.get("date") == date_str), None)
                if row:
                    return ticker, {
                        "short_vol":   int(row.get("short_vol",   0) or 0),
                        "short_ratio": float(row.get("short_ratio", 0) or 0),
                    }
            except Exception:
                pass
            await asyncio.sleep(0.1)
            return ticker, {}

        async def _fetch_credit(ticker: str):
            try:
                rows = await kis_daily_credit_balance(ticker, token, n=10)
                row = next((r for r in rows if r.get("date") == date_str), None)
                if row:
                    return ticker, {
                        "credit_ratio": float(row.get("credit_ratio", 0) or 0),
                    }
            except Exception:
                pass
            await asyncio.sleep(0.1)
            return ticker, {}

        print(f"[backfill] Phase C — 수급 히스토리 {len(tickers)}종목")
        supply_results = await _chunked_gather(_fetch_supply(t) for t in tickers)
        supply_ok = sum(1 for _, d in supply_results if d)
        for tk, data in supply_results:
            supply_map[tk] = data
        print(f"[backfill] Phase C 완료 — 수급 확보 {supply_ok}/{len(tickers)}종목")

        print(f"[backfill] Phase D — 공매도 히스토리 {len(tickers)}종목")
        short_results = await _chunked_gather(_fetch_short(t) for t in tickers)
        short_ok = sum(1 for _, d in short_results if d)
        for tk, data in short_results:
            short_map[tk] = data
        print(f"[backfill] Phase D 완료 — 공매도 확보 {short_ok}/{len(tickers)}종목")

        print(f"[backfill] Phase E — 신용잔고 히스토리 {len(tickers)}종목")
        credit_results = await _chunked_gather(_fetch_credit(t) for t in tickers)
        credit_ok = sum(1 for _, d in credit_results if d)
        for tk, data in credit_results:
            credit_map[tk] = data
        print(f"[backfill] Phase E 완료 — 신용잔고 확보 {credit_ok}/{len(tickers)}종목")

    # ── INSERT OR REPLACE ────────────────────────────────────────────
    conn = _get_db()
    inserted = 0
    errors = 0

    # 모든 fetch는 위에서 완료됨. 순수 sync INSERT 루프 + 후속 쓰기를 한 lock 블록으로.
    async with db_write_lock:
        for ticker in tickers:
            try:
                krx  = krx_by_ticker.get(ticker, {})
                ch   = chart_map.get(ticker, {})
                sup  = supply_map.get(ticker, {})
                sht  = short_map.get(ticker, {})
                crd  = credit_map.get(ticker, {})

                close = int(krx.get("close", 0) or 0)

                conn.execute("""
                    INSERT OR REPLACE INTO daily_snapshot (
                        trade_date, symbol,
                        close, open, high, low, change_pct,
                        volume, trade_value, market_cap,
                        per, pbr, eps, bps, div_yield,
                        w52_high, w52_low, foreign_own_pct, listing_shares, turnover,
                        loan_balance_rate,
                        foreign_net_qty, foreign_net_amt, inst_net_qty, inst_net_amt,
                        indiv_net_qty, indiv_net_amt,
                        short_volume, short_ratio,
                        ovtm_close, ovtm_change_pct, ovtm_volume,
                        collected_at
                    ) VALUES (
                        ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?,
                        ?, ?, ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        datetime('now')
                    )
                """, (
                    date_str, ticker,
                    # OHLCV — KRX close, KIS chart open/high/low
                    close,
                    int(ch.get("open", 0) or 0),
                    int(ch.get("high", 0) or 0),
                    int(ch.get("low",  0) or 0),
                    float(krx.get("chg_pct", 0) or 0),
                    int(krx.get("volume",     0) or 0),
                    int(krx.get("trade_value", 0) or 0),
                    int(krx.get("market_cap",  0) or 0) // 100_000_000,  # KRW → 억원
                    # 시점 특정 불가 컬럼 → 0 (KIS 현재가 API 미사용 원칙)
                    0.0,  # per
                    0.0,  # pbr
                    0.0,  # eps
                    0.0,  # bps
                    0.0,  # div_yield
                    0,    # w52_high
                    0,    # w52_low
                    0.0,  # foreign_own_pct
                    0,    # listing_shares
                    0.0,  # turnover
                    # 신용잔고비율 (당일 시점)
                    float(crd.get("credit_ratio", 0) or 0),
                    # 수급 (KIS FHPTJ04160001 히스토리 — 당일 행 필터). 금액은 `*_ntby_tr_pbmn`(원).
                    int(sup.get("foreign_net",         0) or 0),
                    int(sup.get("foreign_net_amt",     0) or 0),
                    int(sup.get("institution_net",     0) or 0),
                    int(sup.get("institution_net_amt", 0) or 0),
                    int(sup.get("individual_net",      0) or 0),
                    int(sup.get("individual_net_amt",  0) or 0),
                    # 공매도 (KIS FHPST04830000 히스토리 — 당일 행 필터)
                    int(sht.get("short_vol",   0) or 0),
                    float(sht.get("short_ratio", 0) or 0),
                    # 시간외 — 히스토리 없음 → 0
                    0, 0.0, 0,
                ))
                inserted += 1
            except Exception as e:
                print(f"[backfill] {ticker} INSERT 실패: {e}")
                errors += 1

        conn.commit()

        # 기술지표 계산 (collect_daily와 동일)
        try:
            _compute_and_update(conn, date_str)
        except Exception as e:
            print(f"[backfill] 기술지표 계산 실패: {e}")

        # 컨센서스 UPDATE (consensus_history → daily_snapshot.consensus_target/count/gap)
        try:
            _update_consensus_in_snapshot(conn, date_str)
        except Exception as e:
            print(f"[backfill] 컨센서스 갱신 실패: {e}")

    conn.close()

    # F/M/FCF 알파 메트릭
    try:
        async with db_write_lock:
            update_all_alpha_metrics(trade_date=date_str)
    except Exception as e:
        print(f"[backfill] 알파 메트릭 실패: {e}")

    elapsed = _time.monotonic() - t0
    print(
        f"[backfill] {date_str} 완료 — inserted={inserted}, "
        f"errors={errors}, elapsed={elapsed:.1f}s"
    )
    return {
        "date":        date_str,
        "inserted":    inserted,
        "skipped":     len(krx_by_ticker) - len(tickers),
        "errors":      errors,
        "elapsed_sec": round(elapsed, 1),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Part 2 — 기술지표 계산 (technicals.py 로 박리됨) + 하위호환 심볼 + 재무
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# 순수 지표 + 히스토리 로더 + 지표 적용기 — technicals.py 가 실소유자 (P2b-2)
from .technicals import (
    _ma,
    _rsi,
    _calc_vp,
    _volume_ratio,
    _spread_at,
    _rsi_at,
    _macd,
    _atr,
    _volatility_20d,
    _load_history_from_db,
    _compute_technicals_sqlite,
)


# _calc_vp / _volume_ratio / _spread_at / _rsi_at / _macd / _atr / _volatility_20d
# _load_history_from_db / _compute_technicals_sqlite — technicals.py로 박리,
# 위 from .technicals import 로 re-import됨.


def _compute_and_update(conn: sqlite3.Connection, date: str):
    """기술지표 계산 후 daily_snapshot UPDATE."""
    # 1. 과거 데이터 로드
    history, dates = _load_history_from_db(conn, date, 260)

    # 2. 당일 종목 데이터 + 섹터명 조인
    rows = conn.execute("""
        SELECT d.*, m.name, m.market, m.sector as sector_name
        FROM daily_snapshot d
        LEFT JOIN stock_master m ON d.symbol = m.symbol
        WHERE d.trade_date = ?
    """, (date,)).fetchall()
    stocks = {r["symbol"]: dict(r) for r in rows}

    if not stocks:
        print(f"[Tech/SQLite] {date} 데이터 없음, 지표 계산 스킵")
        return

    # 3. 기술지표 계산
    _compute_technicals_sqlite(date, stocks, history, dates)

    # 4. UPDATE
    for ticker, s in stocks.items():
        try:
            conn.execute("""
                UPDATE daily_snapshot SET
                    ma5=?, ma10=?, ma20=?, ma60=?, ma120=?, ma200=?, ma_spread=?,
                    rsi14=?, bb_upper=?, bb_lower=?, bb_width=?,
                    macd=?, macd_signal=?, macd_hist=?,
                    atr14=?, volatility_20d=?,
                    w52_position=?, ytd_return=?,
                    vp_poc_60d=?, vp_va_high_60d=?, vp_va_low_60d=?, vp_position_60d=?,
                    vp_poc_250d=?, vp_va_high_250d=?, vp_va_low_250d=?, vp_position_250d=?,
                    volume_ratio_5d=?, volume_ratio_10d=?, volume_ratio_20d=?,
                    ma_spread_change_10d=?, ma_spread_change_30d=?,
                    rsi_change_5d=?, rsi_change_20d=?,
                    eps_change_90d=?, earnings_gap=?,
                    foreign_trend_5d=?, foreign_trend_20d=?, foreign_trend_60d=?,
                    foreign_ratio=?, inst_ratio=?, fi_ratio=?,
                    short_change_5d=?, short_change_20d=?,
                    sector_rel_strength=?, sector_rank=?
                WHERE trade_date=? AND symbol=?
            """, (
                s.get("ma5"), s.get("ma10"), s.get("ma20"),
                s.get("ma60"), s.get("ma120"), s.get("ma200"), s.get("ma_spread"),
                s.get("rsi14"), s.get("bb_upper"), s.get("bb_lower"), s.get("bb_width"),
                s.get("macd"), s.get("macd_signal"), s.get("macd_hist"),
                s.get("atr14"), s.get("volatility_20d"),
                s.get("w52_position"), s.get("ytd_return"),
                s.get("vp_poc_60d"), s.get("vp_va_high_60d"),
                s.get("vp_va_low_60d"), s.get("vp_position_60d"),
                s.get("vp_poc_250d"), s.get("vp_va_high_250d"),
                s.get("vp_va_low_250d"), s.get("vp_position_250d"),
                s.get("volume_ratio_5d"), s.get("volume_ratio_10d"), s.get("volume_ratio_20d"),
                s.get("ma_spread_change_10d"), s.get("ma_spread_change_30d"),
                s.get("rsi_change_5d"), s.get("rsi_change_20d"),
                s.get("eps_change_90d"), s.get("earnings_gap"),
                s.get("foreign_trend_5d"), s.get("foreign_trend_20d"), s.get("foreign_trend_60d"),
                s.get("foreign_ratio"), s.get("inst_ratio"), s.get("fi_ratio"),
                s.get("short_change_5d"), s.get("short_change_20d"),
                s.get("sector_rel_strength"), s.get("sector_rank"),
                date, ticker,
            ))
        except Exception as e:
            print(f"[Tech/SQLite] {ticker} UPDATE 실패: {e}")
    conn.commit()
    print(f"[Tech/SQLite] {date} UPDATE 완료: {len(stocks)}종목")


# 스캐너/히스토리/load_krx_db — scan.py 가 실소유자 (P3-3)
from .scan import (
    PRESETS,
    load_krx_db,
    _load_history,
    _get_foreign_streak_data_db,
    _summarize_filters,
    scan_stocks,
)


# 재무 수집 함수들 — financial.py 가 실소유자 (P3-6)
from .financial import (
    _DART_INTERVAL,
    _upsert_dart_full_row,
    _collect_dart_full_batch,
    collect_financial_weekly,
    collect_financial_historical,
    collect_financial_on_disclosure,
    _fetch_supply_data,
    _write_supply_to_snapshot,
    _update_supply_in_snapshot,
    _update_consensus_in_snapshot,
    _update_financial_derived,
)


# iCloud Drive 백업 — backup.py 가 실소유자 (P3-8)
from .backup import backup_to_icloud

# 알파 메트릭 엔진 (F/M/FCF Phase2-4) — alpha.py 가 실소유자 (P3-5)
from .alpha import (
    _TTM_FLOW_FIELDS,
    _TTM_STOCK_FIELDS,
    _parse_period,
    _build_period,
    _compute_ttm,
    _prev_yoy_period,
    _fs_source,
    _pick_net_income,
    _safe_div,
    _compute_fscore,
    _compute_mscore,
    _compute_fcf_metrics,
    _ensure_alpha_columns,
    _update_alpha_metrics,
    update_all_alpha_metrics,
    collect_shares_historical,
)

# 미국 애널 마스터 / 매수 후보 — us_analysts.py 가 실소유자 (P3-7)
from .us_analysts import sync_us_analyst_master, is_tier_s_analyst, find_us_buy_candidates
