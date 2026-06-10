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


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 재무 수집 (주 1회)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def collect_financial_weekly(date: str = None) -> dict:
    """손익계산서 + 대차대조표 수집 → financial_quarterly UPSERT.
    주 1회 실행. KIS API kis_income_statement / kis_balance_sheet 사용.
    """
    global _RATE_SEM
    _RATE_SEM = asyncio.Semaphore(8)

    conn = _get_db()
    tickers = [r["symbol"] for r in conn.execute(
        "SELECT symbol FROM stock_master"
    ).fetchall()]

    if not tickers:
        conn.close()
        return {"error": "stock_master 비어 있음"}

    from kis_api import get_kis_token

    token = await get_kis_token()
    if not token:
        conn.close()
        return {"error": "KIS 토큰 발급 실패"}

    success_is = 0
    success_bs = 0
    success_dart = 0

    session = _get_session()
    # Per-ticker timeout 10초 (5/5 사고 후) — 한 종목 hang으로 전체 60분 타임아웃 방지
    _PER_TICKER_TIMEOUT = 10.0
    _timeout_count = {"is": 0, "bs": 0}
    # 진행 로그 50건마다 (이전 200 → 50, buffering 가시성 ↑)
    _PROGRESS_EVERY = 50

    # Phase A: 손익계산서
    print(f"[Finance] Phase A — 손익계산서 {len(tickers)}종목 시작", flush=True)
    _phase_start = asyncio.get_running_loop().time()
    for i, ticker in enumerate(tickers):
        try:
            from kis_api import kis_income_statement
            rows_is = await asyncio.wait_for(
                _rate_limited(kis_income_statement(ticker, token, session=session)),
                timeout=_PER_TICKER_TIMEOUT
            )
            # fetch 완료 후 쓰기+커밋을 동일 lock 블록 안에서 수행
            async with db_write_lock:
                for r in (rows_is or []):
                    rp = r.get("report_period", "")
                    if not rp:
                        continue
                    conn.execute("""
                        INSERT OR REPLACE INTO financial_quarterly (
                            symbol, report_period, revenue, cost_of_sales, gross_profit,
                            operating_profit, op_profit, net_income, collected_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """, (
                        ticker, rp,
                        r.get("revenue"), r.get("cost_of_sales"), r.get("gross_profit"),
                        r.get("operating_profit"), r.get("op_profit"), r.get("net_income"),
                    ))
                conn.commit()
            success_is += 1
        except asyncio.TimeoutError:
            _timeout_count["is"] += 1
        except Exception as e:
            print(f"[Finance] {ticker} 손익계산서 수집 실패 (무시): {e}")
        if (i + 1) % _PROGRESS_EVERY == 0:
            elapsed = asyncio.get_running_loop().time() - _phase_start
            print(f"[Finance] 손익: {i+1}/{len(tickers)} (성공 {success_is}, 타임아웃 {_timeout_count['is']}, {elapsed:.0f}s)", flush=True)
    elapsed_a = asyncio.get_running_loop().time() - _phase_start
    print(f"[Finance] Phase A 완료 — 성공 {success_is}/{len(tickers)}, 타임아웃 {_timeout_count['is']}, {elapsed_a:.0f}s", flush=True)

    # Phase B: 대차대조표
    print(f"[Finance] Phase B — 대차대조표 {len(tickers)}종목 시작", flush=True)
    _phase_start = asyncio.get_running_loop().time()
    for i, ticker in enumerate(tickers):
        try:
            from kis_api import kis_balance_sheet
            rows_bs = await asyncio.wait_for(
                _rate_limited(kis_balance_sheet(ticker, token, session=session)),
                timeout=_PER_TICKER_TIMEOUT
            )
            # fetch 완료 후 쓰기+커밋을 동일 lock 블록 안에서 수행
            async with db_write_lock:
                for r in (rows_bs or []):
                    rp = r.get("report_period", "")
                    if not rp:
                        continue
                    conn.execute("""
                        UPDATE financial_quarterly SET
                            current_assets=?, fixed_assets=?, total_assets=?,
                            current_liab=?, fixed_liab=?, total_liab=?,
                            capital=?, total_equity=?,
                            collected_at=datetime('now')
                        WHERE symbol=? AND report_period=?
                    """, (
                        r.get("current_assets"), r.get("fixed_assets"), r.get("total_assets"),
                        r.get("current_liab"), r.get("fixed_liab"), r.get("total_liab"),
                        r.get("capital"), r.get("total_equity"),
                        ticker, rp,
                    ))
                conn.commit()
            success_bs += 1
        except asyncio.TimeoutError:
            _timeout_count["bs"] += 1
        except Exception:
            pass
        if (i + 1) % _PROGRESS_EVERY == 0:
            elapsed = asyncio.get_running_loop().time() - _phase_start
            print(f"[Finance] 대차: {i+1}/{len(tickers)} (성공 {success_bs}, 타임아웃 {_timeout_count['bs']}, {elapsed:.0f}s)", flush=True)
    elapsed_b = asyncio.get_running_loop().time() - _phase_start
    print(f"[Finance] Phase B 완료 — 성공 {success_bs}/{len(tickers)}, 타임아웃 {_timeout_count['bs']}, {elapsed_b:.0f}s", flush=True)

    # Phase C: DART 현금흐름표 + 지배귀속 + 판관비/매출채권/재고 (최신 4분기)
    # F/M/FCF Phase1 — dart_quarterly_full 1콜로 PL/BS/CF 전체
    try:
        from kis_api import get_dart_corp_map, dart_quarterly_full
        corp_map = await get_dart_corp_map({})
    except Exception as e:
        print(f"[Finance] Phase C skip — corp_map 로드 실패: {e}")
        corp_map = {}

    if corp_map:
        # 최신 4분기 (현재연도 Q1 ~ 전년도 Q2) 기준 — TTM 1회분 확보
        from datetime import datetime as _dt
        now = _dt.now(KST)
        # DART 공시 지연(~45일) 감안: 직전 확정 분기부터 과거로 4개
        current_q = (now.month - 1) // 3 + 1
        targets = []  # (year, quarter)
        y, q = now.year, max(current_q - 1, 1) if current_q > 1 else 4
        if current_q == 1:
            y = now.year - 1
        for _ in range(4):
            targets.append((y, q))
            q -= 1
            if q < 1:
                q = 4
                y -= 1

        print(f"[Finance] Phase C — DART 현금흐름표 {len(tickers)}종목 × "
              f"{len(targets)}분기 = {len(tickers) * len(targets)}콜")
        success_dart = await _collect_dart_full_batch(
            conn, tickers, corp_map, targets
        )

    # 재무 파생값 → daily_snapshot UPDATE
    async with db_write_lock:
        _update_financial_derived(conn, date)
    conn.close()

    print(f"[Finance] 완료 — IS:{success_is}/{len(tickers)}, "
          f"BS:{success_bs}/{len(tickers)}, DART:{success_dart}")
    return {
        "tickers": len(tickers),
        "income_statement": success_is,
        "balance_sheet": success_bs,
        "dart_full": success_dart,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 전체 재무제표 배치 (F/M/FCF Phase1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# DART 분당 1000건 제한 → 안전 마진 900/분 = 0.067초/콜
_DART_INTERVAL = 0.067


def _upsert_dart_full_row(conn: sqlite3.Connection, ticker: str, r: dict):
    """dart_quarterly_full 결과 1분기를 financial_quarterly에 UPSERT.
    기존 KIS IS/BS 데이터를 덮어쓰지 않기 위해 COALESCE 패턴 사용.
    10개 신규 컬럼 + (없을 때) revenue/operating_profit/net_income/… 보강.
    """
    # FK guard 내재화 (학습 #29): stock_master 미등록 ticker 는 silent skip
    # 5/8 d662b69 fix 가 호출 site 한 곳만 커버 → 헬퍼 안으로 push (방어 위치 통일)
    if not conn.execute("SELECT 1 FROM stock_master WHERE symbol=? LIMIT 1", (ticker,)).fetchone():
        return False

    rp = r.get("report_period", "")
    if not rp:
        return False

    # 먼저 row 존재 보장 (INSERT OR IGNORE로 PK만 채움)
    conn.execute(
        "INSERT OR IGNORE INTO financial_quarterly (symbol, report_period, collected_at) "
        "VALUES (?, ?, datetime('now'))",
        (ticker, rp),
    )
    # 신규 컬럼은 항상 DART 값으로 덮어쓰기 (KIS에는 없음)
    # 기존 컬럼은 COALESCE로 기존값 유지
    conn.execute("""
        UPDATE financial_quarterly SET
            revenue          = COALESCE(revenue, ?),
            cost_of_sales    = COALESCE(cost_of_sales, ?),
            gross_profit    = COALESCE(gross_profit, ?),
            operating_profit = COALESCE(operating_profit, ?),
            net_income       = COALESCE(net_income, ?),
            current_assets   = COALESCE(current_assets, ?),
            total_assets     = COALESCE(total_assets, ?),
            current_liab     = COALESCE(current_liab, ?),
            total_liab       = COALESCE(total_liab, ?),
            capital          = COALESCE(capital, ?),
            total_equity     = COALESCE(total_equity, ?),
            cfo              = ?,
            capex            = ?,
            fcf              = ?,
            depreciation     = ?,
            sga              = ?,
            receivables      = ?,
            inventory        = ?,
            shares_out       = ?,
            net_income_parent = ?,
            equity_parent    = ?,
            fs_source        = ?,
            collected_at     = datetime('now')
        WHERE symbol=? AND report_period=?
    """, (
        r.get("revenue"), r.get("cost_of_sales"), r.get("gross_profit"),
        r.get("operating_profit"), r.get("net_income"),
        r.get("current_assets"), r.get("total_assets"),
        r.get("current_liab"), r.get("total_liab"),
        r.get("capital"), r.get("total_equity"),
        r.get("cfo"), r.get("capex"), r.get("fcf"),
        r.get("depreciation"), r.get("sga"),
        r.get("receivables"), r.get("inventory"),
        r.get("shares_out"), r.get("net_income_parent"),
        r.get("equity_parent"), r.get("fs_source"),
        ticker, rp,
    ))
    return True


async def _collect_dart_full_batch(conn: sqlite3.Connection, tickers: list,
                                    corp_map: dict,
                                    targets: list) -> int:
    """DART fnlttSinglAcntAll 배치 수집 (tickers × targets).

    tickers: [symbol, ...]
    corp_map: {symbol: corp_code}
    targets: [(year, quarter), ...]
    반환: 성공 콜 수 (종목·분기 단위)
    """
    from kis_api import dart_quarterly_full

    success = 0
    total = len(tickers) * len(targets)
    done = 0
    skipped_no_corp = 0

    session = _get_session()
    for ticker in tickers:
        corp_code = corp_map.get(ticker)
        if not corp_code:
            skipped_no_corp += 1
            done += len(targets)
            continue
        for (y, q) in targets:
            try:
                r = await dart_quarterly_full(corp_code, y, q, session=session)
                if r:
                    # fetch 완료 후 쓰기+커밋을 동일 lock 블록 안에서 수행
                    async with db_write_lock:
                        _upsert_dart_full_row(conn, ticker, r)
                        conn.commit()
                    success += 1
            except Exception:
                pass
            done += 1
            await asyncio.sleep(_DART_INTERVAL)
            if done % 500 == 0:
                print(f"[DART-Full] 진행: {done}/{total} (성공 {success})")

    print(f"[DART-Full] 완료 — 성공 {success}/{total}, corp_map 미등록 스킵 {skipped_no_corp}")
    return success


async def collect_financial_historical(quarters_back: int = 12,
                                       tickers_limit: int | None = None) -> dict:
    """최근 N분기 DART 전체 재무제표 소급 수집 (F/M/FCF Phase1 1회용).

    유니버스 3200종목 × 12분기 = ~38,400콜.
    DART 분당 1000콜 제한 → 0.067초/콜 = 약 43분 소요.

    Args:
        quarters_back: 과거 몇 분기 수집할지 (기본 12 = 3년)
        tickers_limit: 테스트용 종목 수 제한 (None=전종목)

    Returns:
        {"tickers": N, "quarters": Q, "calls_made": N*Q, "success": S,
         "duration_sec": T}
    """
    from datetime import datetime as _dt
    import time

    conn = _get_db()
    tickers = [r["symbol"] for r in conn.execute(
        "SELECT symbol FROM stock_master"
    ).fetchall()]
    if tickers_limit:
        tickers = tickers[:tickers_limit]

    if not tickers:
        conn.close()
        return {"error": "stock_master 비어 있음"}

    # corp_codes.json (3959종목) 우선, fallback으로 dart_corp_map.json (211종목)
    try:
        from kis_api import load_corp_codes, get_dart_corp_map
        full_map = await load_corp_codes()  # {ticker: {corp_code, corp_name}}
        corp_map = {tk: v["corp_code"] for tk, v in full_map.items() if v.get("corp_code")}
        if not corp_map:
            legacy = await get_dart_corp_map({})
            corp_map = legacy if isinstance(legacy, dict) else {}
    except Exception as e:
        conn.close()
        return {"error": f"corp_map 로드 실패: {e}"}

    if not corp_map:
        conn.close()
        return {"error": "corp_map 비어 있음 — corp_codes.json / dart_corp_map.json 확인"}

    print(f"[Historical] corp_map 엔트리: {len(corp_map)}종목")

    # 타겟 분기 리스트 생성 (DART 공시 지연 ~45일 고려 → 직전 확정 분기부터)
    now = _dt.now(KST)
    current_q = (now.month - 1) // 3 + 1
    y, q = now.year, current_q - 1
    if q < 1:
        q = 4
        y -= 1
    targets = []
    for _ in range(quarters_back):
        targets.append((y, q))
        q -= 1
        if q < 1:
            q = 4
            y -= 1

    total_calls = len(tickers) * len(targets)
    print(f"[Historical] 대상: {len(tickers)}종목 × {len(targets)}분기 = {total_calls}콜")
    print(f"[Historical] 예상 소요: 약 {total_calls * _DART_INTERVAL / 60:.1f}분")
    print(f"[Historical] 타겟 분기: {targets[0]} ~ {targets[-1]}")

    start = time.time()
    success = await _collect_dart_full_batch(conn, tickers, corp_map, targets)
    duration = time.time() - start

    conn.close()

    return {
        "tickers": len(tickers),
        "quarters": len(targets),
        "calls_made": total_calls,
        "success": success,
        "duration_sec": round(duration, 1),
        "target_range": f"{targets[-1]} ~ {targets[0]}",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 증분 수집 (신규 정기공시 기반)
# - 매일 02:00 KST에 main.py 스케줄러가 호출
# - search_dart_periodic_new(days=2)로 최근 2일 정기공시 목록
# - (corp_code, report_period) 쌍 기준 DB 미존재 + cfo IS NULL 만 신규 수집
# - fnlttSinglAcntAll + stockTotqySttus 두 API 사용, 0.067초/콜 간격
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def collect_financial_on_disclosure(days: int = 2,
                                           max_calls: int = 1000) -> dict:
    """신규 정기공시 기반 증분 수집 + 알파 재계산 트리거.

    흐름:
      1) search_dart_periodic_new(days) → 최근 N일 원본 정기공시 목록.
      2) 각 (corp_code, report_period) 쌍 DB 중복 체크.
         - financial_quarterly에 해당 row가 있고 cfo IS NOT NULL 이면 skip.
         - corp_code → ticker 매핑은 corp_codes.json 역방향 (dict{ticker: {corp_code,corp_name}}
           → {corp_code: ticker}). stock_code가 list.json에도 있으니 이를 fallback.
      3) 신규 건만 dart_quarterly_full + dart_shares_outstanding 호출 후 upsert.
      4) 0.067초/콜 rate limit 준수. max_calls 도달 시 중단.
      5) 수집 >0건이면 update_all_alpha_metrics(end_period=최신 period) 호출.

    Args:
        days: 조회 기간 (기본 2일 — 일 1회 스케줄 여유 포함).
        max_calls: DART API 호출 상한 (fnlttSinglAcntAll + stockTotqySttus 합산).
                   기본 1000 = DART 분당 1000콜 상한 고려한 1분치 여유.

    Returns:
        {"disclosures_found", "already_in_db", "skipped_no_ticker",
         "newly_collected", "fnltt_calls", "shares_calls", "alpha_recalc" (dict|None),
         "duration_sec", "quota_used_estimate"}
    """
    from datetime import datetime as _dt
    import time as _time

    from kis_api import (
        search_dart_periodic_new,
        dart_quarterly_full,
        dart_shares_outstanding,
        load_corp_codes,
    )

    start = _time.time()
    result = {
        "disclosures_found": 0,
        "already_in_db": 0,
        "skipped_no_ticker": 0,
        "newly_collected": 0,
        "fnltt_calls": 0,
        "shares_calls": 0,
        "alpha_recalc": None,
        "duration_sec": 0.0,
        "quota_used_estimate": 0,
        "errors": 0,
    }

    # Step 1: 신규 공시 목록
    try:
        scan_sess = _get_session()
        disclosures = await search_dart_periodic_new(days=days, session=scan_sess)
    except Exception as e:
        print(f"[DART-Incr] search_dart_periodic_new 오류: {e}")
        result["errors"] += 1
        result["duration_sec"] = round(_time.time() - start, 1)
        return result

    result["disclosures_found"] = len(disclosures)
    if not disclosures:
        result["duration_sec"] = round(_time.time() - start, 1)
        return result

    # Step 2: corp_code → ticker 역매핑 (list.json에 stock_code가 포함되어 있지만
    # 보험용으로 corp_codes.json 역매핑도 준비)
    try:
        full_map = await load_corp_codes()  # {ticker: {corp_code, corp_name}}
    except Exception as e:
        print(f"[DART-Incr] load_corp_codes 실패: {e}")
        full_map = {}
    corp_to_ticker = {
        v.get("corp_code"): tk for tk, v in full_map.items() if v.get("corp_code")
    }

    conn = _get_db()

    # Step 3: (corp_code, report_period) 중복 + ticker 해결 후 수집 대상 필터링
    # 동일 corp_code+period가 여러 row로 올 수 있으니 dedup.
    to_collect: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    for d in disclosures:
        corp_code = d["corp_code"]
        period    = d["report_period"]
        if (corp_code, period) in seen_pairs:
            continue
        seen_pairs.add((corp_code, period))

        # ticker 해석: 1순위 list.json stock_code, 2순위 corp_codes 역매핑
        ticker = d.get("ticker") or corp_to_ticker.get(corp_code, "")
        if not ticker or len(ticker) != 6:
            # 비상장/지주사 하위 등 — corp_code만 있고 ticker 없는 케이스 skip
            result["skipped_no_ticker"] += 1
            continue

        # DB 중복 체크 — cfo NOT NULL이면 이미 완전 수집된 row (덮어쓰지 않음)
        row = conn.execute(
            "SELECT 1 FROM financial_quarterly "
            "WHERE symbol=? AND report_period=? AND cfo IS NOT NULL",
            (ticker, period),
        ).fetchone()
        if row:
            result["already_in_db"] += 1
            continue

        to_collect.append({**d, "ticker": ticker})

    if not to_collect:
        conn.close()
        result["duration_sec"] = round(_time.time() - start, 1)
        return result

    # Step 4: 수집 (fnlttSinglAcntAll + stockTotqySttus) — 종목당 2콜
    # max_calls 안전장치: 2콜/종목 기준 허용 종목 수 계산
    max_pairs = max_calls // 2
    if len(to_collect) > max_pairs:
        print(f"[DART-Incr] 공시 {len(to_collect)}건 > 허용 {max_pairs}건 — 앞에서만 수집")
        to_collect = to_collect[:max_pairs]

    latest_period = ""
    sess = _get_session()
    for item in to_collect:
        ticker    = item["ticker"]
        corp_code = item["corp_code"]
        period    = item["report_period"]

        # period "YYYYMM" → (year, quarter)
        try:
            year  = int(period[:4])
            month = int(period[4:])
            q_map = {3: 1, 6: 2, 9: 3, 12: 4}
            quarter = q_map.get(month)
        except (ValueError, TypeError, IndexError):
            continue
        if not quarter:
            continue

        # 4a. fnlttSinglAcntAll
        # 5/8 fix: stock_master에 없는 종목은 FK constraint 위반 → skip
        # (DART 등록되었으나 KIS 미커버 또는 stock_master 미갱신 케이스)
        master_exists = conn.execute(
            "SELECT 1 FROM stock_master WHERE symbol = ? LIMIT 1", (ticker,)
        ).fetchone()
        if not master_exists:
            print(f"[DART-Incr] skip {ticker}({corp_code}) — stock_master 미등록")
            continue
        # 4a. fnlttSinglAcntAll — fetch 먼저, 쓰기는 lock 안에서
        _dart_row = None
        try:
            r = await dart_quarterly_full(corp_code, year, quarter, session=sess)
            result["fnltt_calls"] += 1
            if r:
                _dart_row = r
        except Exception as e:
            print(f"[DART-Incr] full {ticker}({corp_code}) {year}Q{quarter} 오류: {e}")
            result["errors"] += 1
            await asyncio.sleep(_DART_INTERVAL)
            continue
        if _dart_row:
            async with db_write_lock:
                _upsert_dart_full_row(conn, ticker, _dart_row)
                conn.commit()
            if period > latest_period:
                latest_period = period
        await asyncio.sleep(_DART_INTERVAL)

        if (result["fnltt_calls"] + result["shares_calls"]) >= max_calls:
            print(f"[DART-Incr] max_calls({max_calls}) 도달 — 수집 중단")
            break

        # 4b. stockTotqySttus — fetch 먼저, 쓰기는 lock 안에서
        try:
            shares = await dart_shares_outstanding(
                corp_code, year, quarter, session=sess,
            )
            result["shares_calls"] += 1
            if shares:
                async with db_write_lock:
                    conn.execute(
                        "UPDATE financial_quarterly SET shares_out=? "
                        "WHERE symbol=? AND report_period=?",
                        (shares, ticker, period),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[DART-Incr] shares {ticker}({corp_code}) {year}Q{quarter} 오류: {e}")
            result["errors"] += 1
        await asyncio.sleep(_DART_INTERVAL)

        result["newly_collected"] += 1

        if (result["fnltt_calls"] + result["shares_calls"]) >= max_calls:
            print(f"[DART-Incr] max_calls({max_calls}) 도달 — 수집 중단")
            break

    conn.close()

    # Step 5: 알파 메트릭 재계산 (최신 period 기준)
    if result["newly_collected"] > 0 and latest_period:
        try:
            async with db_write_lock:
                result["alpha_recalc"] = update_all_alpha_metrics(end_period=latest_period)
        except Exception as e:
            print(f"[DART-Incr] update_all_alpha_metrics 오류: {e}")
            result["alpha_recalc"] = {"error": str(e)}

    result["quota_used_estimate"] = result["fnltt_calls"] + result["shares_calls"]
    result["duration_sec"] = round(_time.time() - start, 1)
    print(f"[DART-Incr] 완료 — 공시 {result['disclosures_found']}건 → "
          f"신규 수집 {result['newly_collected']}건 "
          f"(중복 {result['already_in_db']}, 무ticker {result['skipped_no_ticker']}) "
          f"쿼터 {result['quota_used_estimate']}콜, "
          f"{result['duration_sec']:.0f}초")
    return result


def _fetch_supply_data(date: str) -> dict:
    """pykrx 종목별 외인/기관 순매수 데이터를 네트워크에서 가져온다 (blocking HTTP+sleep 포함).

    ⚠️ 이 함수는 동기 네트워크 호출 + sleep이 있으므로 db_write_lock 밖에서 호출해야 한다.

    Returns: {"rows": [(col_qty, col_amt, date, ticker, qty, amt), ...],
              "empty": N, "errors": [...]}
    """
    if not date:
        return {"rows": [], "empty": 0, "errors": ["date 누락"]}

    try:
        from pykrx import stock
    except ImportError:
        return {"rows": [], "empty": 0, "errors": ["pykrx 미설치"]}

    import time as _time

    def _net_purchases_retry(mkt: str, inv_name: str, tries: int = 3):
        """pykrx 호출 + retry. 성공 시 비어있지 않은 df, 실패/빈응답 시 None."""
        last = None
        for attempt in range(1, tries + 1):
            try:
                df = stock.get_market_net_purchases_of_equities(date, date, mkt, inv_name)
                if df is not None and len(df) > 0:
                    return df, None
                last = "빈 응답(KRX soft-block 가능)"
            except Exception as e:
                last = f"{type(e).__name__}: {str(e)[:80]}"
            if attempt < tries:
                _time.sleep(1.5 * attempt)  # backoff
        return None, last

    rows = []  # (col_qty, col_amt, qty, amt, ticker)
    empty = 0
    errors = []

    # 외국인 (foreign_net_qty/amt) + 기관합계 (inst_net_qty/amt)
    for inv_name, col_qty, col_amt in [
        ("외국인", "foreign_net_qty", "foreign_net_amt"),
        ("기관합계", "inst_net_qty", "inst_net_amt"),
    ]:
        for mkt in ("KOSPI", "KOSDAQ"):
            df, err = _net_purchases_retry(mkt, inv_name)
            if df is None:
                empty += 1
                errors.append(f"{mkt} {inv_name}: {err}")
                continue  # 실패: 덮어쓰지 않음 → KIS 1차값 보존
            for ticker, row in df.iterrows():
                try:
                    qty = int(row.get("순매수거래량", 0) or 0)
                    amt = int(row.get("순매수거래대금", 0) or 0)
                    if qty == 0 and amt == 0:
                        continue  # 0은 절대 기록하지 않음 (refiner 원칙)
                    rows.append((col_qty, col_amt, qty, amt, ticker))
                except Exception:
                    continue

    return {"rows": rows, "empty": empty, "errors": errors}


def _write_supply_to_snapshot(conn: sqlite3.Connection, date: str, supply_data: dict) -> dict:
    """_fetch_supply_data()의 결과를 DB에 기록한다 (순수 sync 쓰기, lock 안에서 호출).

    commit()은 호출자(async with db_write_lock 블록)가 직접 수행한다.
    Returns: {foreign_count, inst_count, empty, errors, ok}
    """
    rows = supply_data.get("rows", [])
    empty = supply_data.get("empty", 0)
    errors = list(supply_data.get("errors", []))

    foreign_n = 0
    inst_n = 0

    for col_qty, col_amt, qty, amt, ticker in rows:
        try:
            cur = conn.execute(
                f"UPDATE daily_snapshot SET {col_qty}=?, {col_amt}=? "
                f"WHERE trade_date=? AND symbol=?",
                (qty, amt, date, ticker)
            )
            if cur.rowcount > 0:
                if col_qty == "foreign_net_qty":
                    foreign_n += 1
                else:
                    inst_n += 1
        except Exception:
            continue

    ok = foreign_n > 0
    print(f"[Supply] pykrx refine: 외인 {foreign_n}, 기관 {inst_n}, 빈응답/실패 {empty}/4")
    if errors:
        for e in errors:
            print(f"  - {e}")
    if not ok:
        # KIS 1차값은 보존되므로 데이터 손실은 아니지만, KRX 경로가 죽었음을 가시화.
        print(f"⚠️ [Supply] pykrx 정밀화 전부 실패 ({date}) — KIS 1차 수급값 유지 (원 단위 근사).")
    return {
        "foreign_count": foreign_n,
        "inst_count": inst_n,
        "empty": empty,
        "errors": errors,
        "ok": ok,
    }


def _update_supply_in_snapshot(conn: sqlite3.Connection, date: str) -> dict:
    """pykrx 종목별 외인/기관 매매로 daily_snapshot 수급 금액을 '정밀화(refine)'한다.

    ⚠️ 더 이상 유일 소스가 아니다. 1차 소스는 KIS FHPTJ04160001(`*_ntby_tr_pbmn`,
    `_store_daily_snapshot`에서 기록). 이 함수는 KRX가 가용할 때만 정확한 원(KRW) 값으로
    덮어쓰는 보강(refiner)이다. KRX/pykrx는 간헐적으로 빈 응답(soft-block)을 주므로:
      - 호출은 retry+backoff 한다.
      - 빈 응답/실패 시 절대 0/NULL로 덮어쓰지 않는다 (KIS 1차값 보존).
      - 전부 실패하면 ⚠️ 경보를 출력해 가시화한다 (침묵 금지).

    ⚠️ 내부적으로 네트워크(pykrx)+sleep 후 DB 쓰기를 수행하므로 db_write_lock 밖에서
    호출해야 한다. (fetch → _write_supply_to_snapshot → conn.commit() 패턴)

    Returns: {foreign_count, inst_count, empty, errors, ok}
    """
    supply_data = _fetch_supply_data(date)
    result = _write_supply_to_snapshot(conn, date, supply_data)
    conn.commit()
    return result


# 배당 수집 / div_yield 재계산 — dividends.py 가 실소유자 (P3-4)
from .dividends import _div_num, _recompute_div_yield_from_events, collect_dividends


def _update_consensus_in_snapshot(conn: sqlite3.Connection, date: str) -> dict:
    """consensus_history 최신 → daily_snapshot.consensus_target/count/gap UPDATE.

    5/8 신규 (학습 #13 "수집 성공 but 0값 함정" 재현 사고 fix).
    이전: db_collector.py:749 주석 처리 → daily_snapshot 컨센 컬럼 영구 0.

    consensus_history 스키마: trade_date, symbol, target_avg/high/low, buy/hold/sell_count
    daily_snapshot 채울 컬럼: consensus_target (avg), consensus_count (buy+hold+sell), consensus_gap (%)

    Returns: {tickers, updated, latest_consensus_date}
    """
    if not date:
        return {"error": "date 누락"}

    # 종목별 가용 최신 컨센 (snapshot_date <= 현재 trade_date)
    # consensus_history는 매주 일요일 collect라 최근 7일 이내 데이터 사용
    rows = conn.execute("""
        SELECT ch.symbol, ch.target_avg, ch.buy_count, ch.hold_count, ch.sell_count, ch.trade_date
        FROM consensus_history ch
        INNER JOIN (
            SELECT symbol, MAX(trade_date) AS latest
            FROM consensus_history
            WHERE trade_date <= ?
            GROUP BY symbol
        ) latest ON ch.symbol = latest.symbol AND ch.trade_date = latest.latest
        WHERE ch.target_avg > 0
    """, (date,)).fetchall()

    if not rows:
        return {"error": "consensus_history 데이터 없음", "tickers": 0, "updated": 0}

    updated = 0
    latest_dates = set()
    for r in rows:
        sym = r["symbol"]
        target = r["target_avg"] or 0
        b = r["buy_count"] or 0
        h = r["hold_count"] or 0
        s = r["sell_count"] or 0
        n_brk = b + h + s
        latest_dates.add(r["trade_date"])

        # 현재가 + gap 계산
        cur_row = conn.execute(
            "SELECT close FROM daily_snapshot WHERE trade_date=? AND symbol=?",
            (date, sym)
        ).fetchone()
        if not cur_row or not cur_row["close"]:
            continue
        close = cur_row["close"]
        gap = round((target - close) / close * 100, 2) if close > 0 else None

        cur = conn.execute(
            "UPDATE daily_snapshot SET consensus_target=?, consensus_count=?, consensus_gap=? "
            "WHERE trade_date=? AND symbol=?",
            (int(target), n_brk, gap, date, sym)
        )
        if cur.rowcount > 0:
            updated += 1
    conn.commit()

    print(f"[Consensus] daily_snapshot UPDATE 완료: {updated}/{len(rows)}종목 "
          f"(latest={sorted(latest_dates)[-1] if latest_dates else None})")
    return {
        "tickers": len(rows),
        "updated": updated,
        "latest_consensus_date": sorted(latest_dates)[-1] if latest_dates else None,
    }


def _update_financial_derived(conn: sqlite3.Connection, date: str = None):
    """financial_quarterly 최신 분기 → daily_snapshot 재무 파생 컬럼 UPDATE."""
    if date is None:
        row = conn.execute("SELECT MAX(trade_date) as d FROM daily_snapshot").fetchone()
        date = row["d"] if row and row["d"] else None
    if not date:
        return

    # 각 종목의 최신 분기 재무
    financials = conn.execute("""
        SELECT f.* FROM financial_quarterly f
        INNER JOIN (
            SELECT symbol, MAX(report_period) as max_period
            FROM financial_quarterly
            GROUP BY symbol
        ) latest ON f.symbol = latest.symbol AND f.report_period = latest.max_period
    """).fetchall()

    updated = 0
    for f in financials:
        sym = f["symbol"]
        rev = f["revenue"] or 0
        op = f["operating_profit"] or 0
        ni = f["net_income"] or 0
        ta = f["total_assets"] or 0
        tl = f["total_liab"] or 0
        te = f["total_equity"] or 0

        op_margin = round(op / rev * 100, 4) if rev else None
        net_margin = round(ni / rev * 100, 4) if rev else None
        debt_ratio = round(tl / te * 100, 4) if te else None
        roe = round(ni / te * 100, 4) if te else None

        # 전분기 대비 성장률
        prev = conn.execute("""
            SELECT revenue, operating_profit FROM financial_quarterly
            WHERE symbol=? AND report_period < ?
            ORDER BY report_period DESC LIMIT 1
        """, (sym, f["report_period"])).fetchone()

        rev_growth = None
        op_growth = None
        if prev:
            prev_rev = prev["revenue"] or 0
            prev_op = prev["operating_profit"] or 0
            if prev_rev and abs(prev_rev) > 0:
                rev_growth = round((rev - prev_rev) / abs(prev_rev) * 100, 4)
            if prev_op and abs(prev_op) > 0:
                op_growth = round((op - prev_op) / abs(prev_op) * 100, 4)

        try:
            conn.execute("""
                UPDATE daily_snapshot SET
                    revenue=?, operating_profit=?, net_income=?,
                    total_assets=?, total_liabilities=?, total_equity=?,
                    operating_margin=?, net_margin=?, debt_ratio=?, roe=?,
                    revenue_growth=?, op_growth=?
                WHERE trade_date=? AND symbol=?
            """, (
                rev, op, ni, ta, tl, te,
                op_margin, net_margin, debt_ratio, roe,
                rev_growth, op_growth,
                date, sym,
            ))
            updated += 1
        except Exception as e:
            print(f"[Finance] {sym} 재무파생 UPDATE 실패: {e}")

    conn.commit()
    print(f"[Finance] 재무 파생값 UPDATE 완료: {updated}종목 ({date})")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# iCloud Drive 백업
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def backup_to_icloud():
    """data/ → iCloud Drive 백업. 최근 2개 유지 (current / previous)."""
    import shutil

    ICLOUD_BASE = os.path.expanduser(
        "~/Library/Mobile Documents/com~apple~CloudDocs/stock-bot-backup"
    )
    CURRENT = os.path.join(ICLOUD_BASE, "current")
    PREVIOUS = os.path.join(ICLOUD_BASE, "previous")

    # 1. previous 삭제
    if os.path.exists(PREVIOUS):
        shutil.rmtree(PREVIOUS)

    # 2. current → previous 이동
    if os.path.exists(CURRENT):
        os.rename(CURRENT, PREVIOUS)

    # 3. 새 current 생성
    os.makedirs(CURRENT, exist_ok=True)

    # 4. 파일 복사
    data_dir = os.environ.get("DATA_DIR", "data")

    # stock.db
    db_src = os.path.join(data_dir, "stock.db")
    if os.path.exists(db_src):
        shutil.copy2(db_src, os.path.join(CURRENT, "stock.db"))

    # *.json, *.md, *.txt (최상위만, krx_db/ 제외)
    for f in os.listdir(data_dir):
        src = os.path.join(data_dir, f)
        if os.path.isfile(src) and (
            f.endswith(".json") or f.endswith(".md") or f.endswith(".txt")
        ):
            shutil.copy2(src, os.path.join(CURRENT, f))

    # research/ 폴더
    research_src = os.path.join(data_dir, "research")
    research_dst = os.path.join(CURRENT, "research")
    if os.path.isdir(research_src):
        shutil.copytree(research_src, research_dst, dirs_exist_ok=True)

    # 백업 타임스탬프
    with open(os.path.join(CURRENT, "_backup_time.txt"), "w") as f:
        f.write(datetime.now(KST).isoformat())

    print(f"[backup_to_icloud] 완료 → {CURRENT}")
    return {"ok": True, "path": CURRENT}


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

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# us_analyst_ratings → us_analysts 마스터 자동 동기화
# (weekly_us_harvest 후 04:00 실행, ratings 1,902명 → 마스터 자동 인구)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def sync_us_analyst_master() -> dict:
    """ratings 테이블의 모든 애널을 us_analysts 마스터로 자동 동기화.
    + 3-Tier 자동 분류 (Tier A=watched=1, Tier S는 알림 시 런타임 분기).

    **Tier A (watched=1) 진입 조건 (OR)**:
      - 일반 톱: 별점≥4.0 AND 적중률≥60% AND 콜≥10
      - 잠수형 거장: 별점≥4.8 AND 적중률≥80% AND 콜≥7

    **Tier S (런타임 분기, watched=1 안에서)**:
      ① 활발 톱: 별점≥4.5 AND 적중률≥70% AND 콜≥20
      ② 잠수형 거장: 별점≥4.8 AND 적중률≥80% AND 콜≥7
      ③ 고수익 거장: 별점≥4.5 AND avg_return≥50% AND 콜≥10

    - 신규 애널: INSERT (avg_return 포함)
    - 기존 애널: stars/success_rate/total_ratings/avg_return 갱신
    - watched/curated_at 사용자 큐레이션 보존 (수동 watched=1만 자동 watched=0으로 안 됨)

    Returns: {inserted, updated, auto_watched_a, tier_s_count, total_master, total_watched}
    """
    conn = _get_db()
    try:
        before_master = conn.execute("SELECT COUNT(*) FROM us_analysts").fetchone()[0]

        # Step 1: ratings → 마스터 INSERT or UPDATE (avg_return 포함)
        conn.execute("""
            INSERT INTO us_analysts (slug, name, firm, stars, success_rate,
                                     total_ratings, avg_return, last_updated)
            SELECT analyst_slug, MAX(analyst), MAX(firm),
                   AVG(stars), AVG(success_rate), COUNT(*),
                   AVG(avg_return),
                   datetime('now')
            FROM us_analyst_ratings
            WHERE analyst_slug IS NOT NULL AND analyst_slug != ''
            GROUP BY analyst_slug
            ON CONFLICT(slug) DO UPDATE SET
              name          = excluded.name,
              firm          = excluded.firm,
              stars         = excluded.stars,
              success_rate  = excluded.success_rate,
              total_ratings = excluded.total_ratings,
              avg_return    = excluded.avg_return,
              last_updated  = excluded.last_updated
        """)
        conn.commit()

        # Step 2: Tier A 자동 watched=1 (OR 2 경로, 사용자 큐레이션 보존)
        cur = conn.execute("""
            UPDATE us_analysts SET
              watched = 1,
              curated_at = datetime('now')
            WHERE watched = 0 AND (
              -- 일반 톱
              (stars >= 4.0 AND success_rate >= 60 AND total_ratings >= 10)
              OR
              -- 잠수형 거장 (Wildcard)
              (stars >= 4.8 AND success_rate >= 80 AND total_ratings >= 7)
            )
        """)
        auto_watched_count = cur.rowcount
        conn.commit()

        # Step 3: Tier S 카운트 (런타임 분기지만 통계 목적)
        tier_s_count = conn.execute("""
            SELECT COUNT(*) FROM us_analysts WHERE watched = 1 AND (
              (stars >= 4.5 AND success_rate >= 70 AND total_ratings >= 20)
              OR (stars >= 4.8 AND success_rate >= 80 AND total_ratings >= 7)
              OR (stars >= 4.5 AND avg_return >= 50 AND total_ratings >= 10)
            )
        """).fetchone()[0]

        after_master = conn.execute("SELECT COUNT(*) FROM us_analysts").fetchone()[0]
        after_watched = conn.execute("SELECT COUNT(*) FROM us_analysts WHERE watched=1").fetchone()[0]

        return {
            "inserted": after_master - before_master,
            "updated": before_master,
            "auto_watched_a": auto_watched_count,
            "tier_s_count": tier_s_count,
            "total_master": after_master,
            "total_watched": after_watched,
            "criteria": "Tier A: 별점≥4.0 AND 적중률≥60% AND 콜≥10 OR (별점≥4.8 AND 적중률≥80% AND 콜≥7)",
        }
    finally:
        conn.close()


def is_tier_s_analyst(stars: float, success_rate: float, total_ratings: int,
                       avg_return: float = 0.0) -> bool:
    """Tier S 엘리트 판정 — 알림 시 런타임 분기용.
    3 경로 OR — 자주 정확 / 잠수형 거장 / 고수익 거장."""
    if stars is None:
        return False
    s = stars
    sr = success_rate or 0.0
    n = total_ratings or 0
    ret = avg_return or 0.0
    return (
        (s >= 4.5 and sr >= 70 and n >= 20) or       # ① 활발 톱
        (s >= 4.8 and sr >= 80 and n >= 7) or        # ② 잠수형 거장
        (s >= 4.5 and ret >= 50 and n >= 10)         # ③ 고수익 거장
    )


def find_us_buy_candidates(
    days: int = 180,
    min_advisors: int = 1,
    min_upside: float = 20.0,
    exclude_held_and_watch: bool = True,
    limit: int = 50,
) -> dict:
    """톱 애널 추천 + 가격 적정 미국 매수 후보 발굴.

    원시 데이터 반환. 정렬·필터·해석은 LLM/사용자가 동적으로.

    조건:
    - watched=1 (Tier A, 254명) 애널의 Upgrades or Initiates
    - 최근 N일 (기본 180)
    - 종목별 추천 애널 N명+ (기본 1)
    - TP 대비 현재가 업사이드 N%+ (기본 20%, TP 초과 자동 컷)
    - 보유/워치 제외 (기본)

    Returns: {
      "criteria": {...},
      "total_pool": int,           # 풀 크기
      "after_upside_filter": int,  # 업사이드 필터 후
      "candidates": [
        {ticker, price, avg_target, upside_pct,
         tier_s_count, tier_a_count, others_count, total_advisors,
         latest_call_days_ago, tier_s_analysts, tier_a_analysts}
      ]
    }
    """
    import yfinance as yf
    from datetime import datetime, timezone

    conn = _get_db()
    try:
        # Step 1: 종목별 watched 애널 추천 집계
        rows = conn.execute("""
            SELECT r.ticker,
                   AVG(r.pt_now) AS avg_tp,
                   COUNT(*) AS total_advisors,
                   MAX(r.rating_date) AS latest_rating,
                   GROUP_CONCAT(DISTINCT r.action) AS actions
            FROM us_analyst_ratings r
            JOIN us_analysts a ON r.analyst_slug = a.slug
            WHERE a.watched = 1
              AND r.action IN ('Upgrades', 'Initiates')
              AND r.rating_date >= date('now', ?)
            GROUP BY r.ticker
            HAVING COUNT(*) >= ?
        """, (f"-{days} days", min_advisors)).fetchall()

        if not rows:
            return {"criteria": {"days": days, "min_advisors": min_advisors,
                                  "min_upside": min_upside},
                    "total_pool": 0, "after_upside_filter": 0, "candidates": []}

        # Step 2: 보유/워치 제외 처리
        excluded = set()
        if exclude_held_and_watch:
            try:
                from kis_api import load_us_watchlist, PORTFOLIO_FILE, load_json
                for t in load_us_watchlist().keys():
                    excluded.add(t.upper())
                for t in load_json(PORTFOLIO_FILE, {}).get("us_stocks", {}).keys():
                    excluded.add(t.upper())
            except Exception:
                pass

        candidate_rows = [r for r in rows if r["ticker"].upper() not in excluded]
        total_pool = len(candidate_rows)
        if not candidate_rows:
            return {"criteria": {"days": days, "min_advisors": min_advisors,
                                  "min_upside": min_upside},
                    "total_pool": 0, "after_upside_filter": 0, "candidates": []}

        # Step 3: 종목별 Tier S/A/일반 카운트 + 톱 애널 정보 수집
        ticker_details = {}
        for r in candidate_rows:
            ticker = r["ticker"]
            advisor_rows = conn.execute("""
                SELECT r.firm, r.analyst, r.action, r.rating_new, r.pt_now, r.rating_date,
                       a.stars, a.success_rate, a.total_ratings, a.avg_return, a.watched
                FROM us_analyst_ratings r
                JOIN us_analysts a ON r.analyst_slug = a.slug
                WHERE r.ticker = ? AND a.watched = 1
                  AND r.action IN ('Upgrades', 'Initiates')
                  AND r.rating_date >= date('now', ?)
                ORDER BY r.rating_date DESC, a.stars DESC
            """, (ticker, f"-{days} days")).fetchall()

            tier_s_list, tier_a_list = [], []
            for ar in advisor_rows:
                meta = {
                    "name": ar["analyst"], "firm": ar["firm"],
                    "stars": ar["stars"], "success_rate": ar["success_rate"],
                    "total_calls": ar["total_ratings"], "avg_return": ar["avg_return"],
                    "action": ar["action"], "rating": ar["rating_new"],
                    "pt": ar["pt_now"], "rated_at": ar["rating_date"],
                }
                if is_tier_s_analyst(ar["stars"], ar["success_rate"],
                                       ar["total_ratings"], ar["avg_return"]):
                    tier_s_list.append(meta)
                else:
                    tier_a_list.append(meta)

            ticker_details[ticker] = {
                "tier_s_count": len(tier_s_list),
                "tier_a_count": len(tier_a_list),
                "tier_s_analysts": tier_s_list[:3],  # 상위 3명만
                "tier_a_analysts": tier_a_list[:3],
                "actions": (r["actions"] or "").split(","),
                "latest_rating": r["latest_rating"],
            }

        # Step 4: yfinance 배치 다운로드 (현재가)
        tickers_list = [r["ticker"] for r in candidate_rows]
        prices = {}
        try:
            data = yf.download(tickers=tickers_list, period="1d",
                                progress=False, auto_adjust=True, threads=False)
            if not data.empty:
                close = data["Close"]
                if hasattr(close, "iloc"):
                    last = close.iloc[-1]
                    if hasattr(last, "to_dict"):
                        prices = last.to_dict()
                    else:
                        # 단일 종목 케이스
                        prices = {tickers_list[0]: float(last)}
        except Exception as e:
            print(f"[buy_candidates] yfinance 실패: {e}")

        # Step 5: 업사이드 계산 + 필터
        today = datetime.now(timezone.utc).date()
        candidates = []
        for r in candidate_rows:
            ticker = r["ticker"]
            avg_tp = r["avg_tp"]
            price = prices.get(ticker)
            if price is None or price != price or not avg_tp or avg_tp <= 0:
                continue
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            upside = (avg_tp - price) / price * 100.0
            if upside < min_upside:
                continue

            details = ticker_details.get(ticker, {})
            # 최근 콜 days ago
            latest = details.get("latest_rating") or r["latest_rating"]
            try:
                from datetime import date as _date
                ld = _date.fromisoformat(latest) if latest else today
                days_ago = (today - ld).days
            except Exception:
                days_ago = None

            candidates.append({
                "ticker": ticker,
                "price": round(price, 2),
                "avg_target": round(avg_tp, 2),
                "upside_pct": round(upside, 2),
                "total_advisors": r["total_advisors"],
                "tier_s_count": details.get("tier_s_count", 0),
                "tier_a_count": details.get("tier_a_count", 0),
                "others_count": 0,  # watched=1만 보므로 일반은 0
                "latest_call_days_ago": days_ago,
                "actions": details.get("actions", []),
                "tier_s_analysts": details.get("tier_s_analysts", []),
                "tier_a_analysts": details.get("tier_a_analysts", []),
            })

        # Step 6: 정렬 (업사이드 내림차순) + limit
        candidates.sort(key=lambda c: -c["upside_pct"])
        after_filter = len(candidates)
        candidates = candidates[:limit]

        return {
            "criteria": {
                "days": days, "min_advisors": min_advisors,
                "min_upside": min_upside, "limit": limit,
                "exclude_held_and_watch": exclude_held_and_watch,
            },
            "total_pool": total_pool,
            "after_upside_filter": after_filter,
            "candidates": candidates,
        }
    finally:
        conn.close()

