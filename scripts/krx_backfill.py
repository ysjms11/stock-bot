#!/usr/bin/env python3
"""
KRX 과거 1년 백필 (맥미니 로컬 실행)
- KRX OPEN API → 시세/시총
- Safari KRX 세션 → PER/PBR/수급/공매도/외인보유/신용
- 날짜별 data/krx_db/YYYYMMDD.json 로컬 저장
- 전체 저장 후 기술적 지표 일괄 재계산
- 예상 소요: 250일 × 5초 = ~20분
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# .env 로드
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

# krx_crawler import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from krx_crawler import (
    KRX_DB_DIR, KST, _pi, _pf,
    fetch_krx_market_data, _parse_market_records,
    _safari_available, _fetch_safari_krx,
    _compute_technicals, load_krx_db,
)

_DATA_DIR = os.environ.get("DATA_DIR", "/data")


def _get_trading_dates(n_days: int = 250) -> list[str]:
    """KRX OPEN API로 거래일 목록 추출. 최신순 → 오래된순 반환."""
    # data/krx_db에 이미 있는 날짜 + KRX OPEN API로 확인
    # 단순 접근: 과거 n_days*1.5일 범위에서 평일만
    dates = []
    d = datetime.now(KST) - timedelta(days=1)  # 어제부터
    while len(dates) < n_days:
        if d.weekday() < 5:  # 평일
            dates.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return list(reversed(dates))  # 오래된순


def _build_daily_sync(date: str) -> dict | None:
    """하루치 데이터 수집 (KRX OPEN API + Safari KRX, 동기)."""
    # 1) 시세 (KRX OPEN API)
    stocks = {}
    for mkt in ["STK", "KSQ"]:
        try:
            records = asyncio.run(_fetch_market_async(date, mkt))
            for r in records:
                stocks[r["ticker"]] = r
        except Exception as e:
            print(f"    시세({mkt}) 실패: {e}")
        time.sleep(0.5)

    if not stocks:
        return None

    # 2) Safari KRX (PER/PBR/수급)
    safari_data = _fetch_safari_krx(date)
    for ticker, vals in safari_data.items():
        if ticker in stocks:
            stocks[ticker].update(vals)

    # 기본값 + 비율 계산
    for s in stocks.values():
        for key in ["per", "pbr", "eps", "bps", "div_yield"]:
            s.setdefault(key, 0.0)
        for key in ["foreign_net_amt", "inst_net_amt", "indiv_net_amt",
                     "short_balance", "short_ratio", "foreign_hold_ratio",
                     "foreign_exhaust_rate", "credit_balance", "lending_balance"]:
            s.setdefault(key, 0)
        s.setdefault("sector_name", "")

        mcap = s.get("market_cap", 0)
        f_amt = s.get("foreign_net_amt", 0)
        i_amt = s.get("inst_net_amt", 0)
        tv = s.get("trade_value", 0)
        if mcap > 0:
            s["turnover"] = round(tv / mcap * 100, 4)
            s["foreign_ratio"] = round(f_amt / mcap * 100, 4)
            s["inst_ratio"] = round(i_amt / mcap * 100, 4)
            s["fi_ratio"] = round((f_amt + i_amt) / mcap * 100, 4)
        else:
            s["turnover"] = s["foreign_ratio"] = s["inst_ratio"] = s["fi_ratio"] = 0.0

    # 시장 요약
    kospi = [s for s in stocks.values() if s["market"] == "kospi"]
    kosdaq = [s for s in stocks.values() if s["market"] == "kosdaq"]
    safari_count = len(safari_data)

    return {
        "date": date,
        "updated_at": datetime.now(KST).isoformat(),
        "source": {"price": "KRX_OPENAPI", "valuation": f"safari_krx({safari_count})",
                    "supply": f"safari_krx({safari_count})"},
        "market_summary": {
            "kospi_count": len(kospi), "kosdaq_count": len(kosdaq),
            "kospi_up": sum(1 for s in kospi if s["chg_pct"] > 0),
            "kospi_down": sum(1 for s in kospi if s["chg_pct"] < 0),
            "kosdaq_up": sum(1 for s in kosdaq if s["chg_pct"] > 0),
            "kosdaq_down": sum(1 for s in kosdaq if s["chg_pct"] < 0),
            "kospi_avg_chg": round(sum(s["chg_pct"] for s in kospi) / len(kospi), 2) if kospi else 0,
            "kosdaq_avg_chg": round(sum(s["chg_pct"] for s in kosdaq) / len(kosdaq), 2) if kosdaq else 0,
        },
        "count": len(stocks),
        "stocks": stocks,
    }


async def _fetch_market_async(date: str, mkt: str) -> list:
    """fetch_krx_market_data async wrapper."""
    return await fetch_krx_market_data(date, mkt)


def _save_db(db: dict):
    """로컬 저장 (atomic write)."""
    os.makedirs(KRX_DB_DIR, exist_ok=True)
    filepath = os.path.join(KRX_DB_DIR, f"{db['date']}.json")
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)
    os.replace(tmp, filepath)
    return round(os.path.getsize(filepath) / 1024, 1)


def _recompute_all_technicals():
    """저장된 모든 DB에 대해 기술적 지표 재계산 (최신부터)."""
    if not os.path.exists(KRX_DB_DIR):
        return
    files = sorted([f for f in os.listdir(KRX_DB_DIR) if f.endswith(".json")])
    print(f"\n[Tech] {len(files)}일 기술적 지표 재계산 시작")

    for i, fname in enumerate(files):
        date = fname[:8]
        filepath = os.path.join(KRX_DB_DIR, fname)
        try:
            with open(filepath, encoding="utf-8") as f:
                db = json.load(f)
            stocks = db.get("stocks", {})
            _compute_technicals(date, stocks)
            db["stocks"] = stocks

            tmp = filepath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(db, f, ensure_ascii=False)
            os.replace(tmp, filepath)

            if (i + 1) % 20 == 0:
                print(f"  [{i+1}/{len(files)}] {date} 완료")
        except Exception as e:
            print(f"  [{i+1}/{len(files)}] {date} 실패: {e}")

    print(f"[Tech] 재계산 완료: {len(files)}일")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="KRX 백필 (맥미니 로컬)")
    parser.add_argument("--days", type=int, default=250, help="백필 일수 (기본 250)")
    parser.add_argument("--start-from", type=int, default=0, help="시작 인덱스 (재시작용)")
    parser.add_argument("--skip-existing", action="store_true", help="이미 있는 날짜 건너뛰기")
    parser.add_argument("--no-tech", action="store_true", help="기술적 지표 재계산 건너뛰기")
    parser.add_argument("--sleep", type=float, default=5.0, help="날짜당 sleep초 (기본 5)")
    args = parser.parse_args()

    # Safari 세션 확인
    if not _safari_available():
        print("[ERROR] Safari에 KRX 로그인 세션 없음. Safari에서 data.krx.co.kr 로그인 필요.")
        sys.exit(1)

    dates = _get_trading_dates(args.days)
    total = len(dates)
    print(f"[Backfill] {total}거래일 백필 시작 (sleep={args.sleep}초)")

    # 이미 존재하는 날짜 확인
    existing = set()
    if os.path.exists(KRX_DB_DIR):
        existing = {f[:8] for f in os.listdir(KRX_DB_DIR) if f.endswith(".json")}
    print(f"[Backfill] 기존 DB: {len(existing)}일")

    success = 0
    skip = 0
    fail = 0

    for i, date in enumerate(dates):
        if i < args.start_from:
            continue
        if args.skip_existing and date in existing:
            skip += 1
            continue

        print(f"\n[{i+1}/{total}] {date}", end=" ")
        try:
            db = _build_daily_sync(date)
            if not db or db.get("count", 0) == 0:
                print("→ 데이터 없음 (휴장일?)")
                continue

            size = _save_db(db)
            safari_n = len([t for t, s in db["stocks"].items() if s.get("per", 0) != 0])
            supply_n = len([t for t, s in db["stocks"].items() if s.get("foreign_net_amt", 0) != 0])
            print(f"→ {db['count']}종목, PER={safari_n}, 수급={supply_n}, {size}KB")
            success += 1
        except Exception as e:
            print(f"→ 실패: {e}")
            fail += 1

        time.sleep(args.sleep)

    print(f"\n[Backfill] 데이터 수집 완료: 성공={success}, 건너뜀={skip}, 실패={fail}")

    # 기술적 지표 재계산
    if not args.no_tech and success > 0:
        _recompute_all_technicals()

    print(f"[Backfill] 전체 완료")


if __name__ == "__main__":
    main()
