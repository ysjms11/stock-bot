#!/usr/bin/env python3
"""backfill_gaps.py — daily_snapshot 과거 공백 복구

4/8, 4/9, 4/10, 4/17 (4 영업일) × 전종목 (~2,861) 기본 시세 복구.
KIS FHKST03010100 일봉 API 사용 (종목당 1콜, 30일치 한꺼번에).
INSERT OR IGNORE 로 기존 데이터 안 건드림.

실행:
    python3 backfill_gaps.py             # 실제 backfill
    python3 backfill_gaps.py --dry-run   # DB 쓰기 없이 시뮬

실행 시간: 약 15분 (2,861 × 0.3초 + 오버헤드).
봇 rate limit 공유: 초당 3건만 쓰므로 여유 있음 (봇 REST 호출 < 5건/초 추정).
"""
import asyncio, os, sys, argparse
from datetime import datetime

sys.path.insert(0, "/Users/kreuzer/stock-bot")
os.chdir("/Users/kreuzer/stock-bot")
from dotenv import load_dotenv
load_dotenv("/Users/kreuzer/stock-bot/.env")

import aiohttp
from kis_api import get_kis_token, _kis_get, KST
from db_collector import _get_db

TARGET_DATES = ["20260408", "20260409", "20260410", "20260417"]
RATE_LIMIT_SLEEP = 0.3
LOG_INTERVAL = 100  # 종목 N 개마다 진행 로그
START_DATE = "20260401"  # API 조회 범위 시작
END_DATE = "20260422"    # API 조회 범위 끝


async def fetch_ohlcv_for_ticker(ticker: str, token: str, session) -> dict:
    """종목 1개 일봉 OHLCV 조회. 반환: {date: {close, open, ...}, ...}.
    TARGET_DATES 에 해당하는 날짜만 포함.
    """
    try:
        _, d = await _kis_get(session,
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100", token,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
             "FID_INPUT_DATE_1": START_DATE, "FID_INPUT_DATE_2": END_DATE,
             "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
        out = {}
        for c in d.get("output2") or []:
            date = c.get("stck_bsop_date")
            if date in TARGET_DATES:
                out[date] = {
                    "close":       int(c.get("stck_clpr", 0) or 0),
                    "open":        int(c.get("stck_oprc", 0) or 0),
                    "high":        int(c.get("stck_hgpr", 0) or 0),
                    "low":         int(c.get("stck_lwpr", 0) or 0),
                    "volume":      int(c.get("acml_vol", 0) or 0),
                    "trade_value": int(c.get("acml_tr_pbmn", 0) or 0),
                    "change_pct":  float(c.get("prdy_ctrt", 0) or 0),
                }
        return out
    except Exception as e:
        return {"__error__": str(e)}


def insert_ohlcv(conn, ticker: str, date: str, ohlcv: dict, dry_run: bool) -> bool:
    """반환: True = 신규 insert 성공, False = 이미 있음/에러."""
    if dry_run:
        return True
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO daily_snapshot "
            "(trade_date, symbol, close, open, high, low, change_pct, volume, trade_value, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (date, ticker, ohlcv["close"], ohlcv["open"], ohlcv["high"], ohlcv["low"],
             ohlcv["change_pct"], ohlcv["volume"], ohlcv["trade_value"],
             datetime.now().isoformat())
        )
        return cur.rowcount > 0
    except Exception as e:
        print(f"[INSERT] {ticker} {date} 에러: {e}")
        return False


async def main(dry_run: bool = False):
    print(f"[backfill] 시작 — target: {TARGET_DATES} / dry_run={dry_run}")
    token = await get_kis_token()
    if not token:
        print("[backfill] 토큰 발급 실패")
        return

    # stock_master 에서 전체 심볼 로드
    conn = _get_db()
    try:
        symbols = [r[0] for r in conn.execute("SELECT symbol FROM stock_master").fetchall()]
    finally:
        conn.close()
    print(f"[backfill] 대상 {len(symbols)}종목")

    # API 호출 루프
    stats = {d: 0 for d in TARGET_DATES}
    stats["skipped"] = 0
    stats["failed"] = 0
    failed_tickers = []

    conn = _get_db()
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for i, ticker in enumerate(symbols, 1):
                result = await fetch_ohlcv_for_ticker(ticker, token, session)
                if "__error__" in result:
                    stats["failed"] += 1
                    failed_tickers.append((ticker, result["__error__"]))
                else:
                    for date, ohlcv in result.items():
                        if insert_ohlcv(conn, ticker, date, ohlcv, dry_run):
                            stats[date] += 1
                        else:
                            stats["skipped"] += 1
                if i % LOG_INTERVAL == 0:
                    conn.commit()
                    print(f"[backfill] {i}/{len(symbols)} ({i/len(symbols)*100:.1f}%) — "
                          f"{sum(stats[d] for d in TARGET_DATES)} rows inserted")
                await asyncio.sleep(RATE_LIMIT_SLEEP)
        conn.commit()
    finally:
        conn.close()

    print("\n[backfill] 완료")
    print(f"  4/8:  {stats['20260408']} rows")
    print(f"  4/9:  {stats['20260409']} rows")
    print(f"  4/10: {stats['20260410']} rows")
    print(f"  4/17: {stats['20260417']} rows")
    print(f"  이미 있음 (skip): {stats['skipped']}")
    print(f"  실패: {stats['failed']}")
    if failed_tickers[:5]:
        print(f"  실패 샘플: {failed_tickers[:5]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
