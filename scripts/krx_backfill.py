#!/usr/bin/env python3
"""
KRX 과거 1년 백필 스크립트 (GitHub Actions 전용)
- pykrx로 과거 250거래일 데이터 수집
- 시세 + PER/PBR + 수급 + 공매도 + 외인보유
- 날짜별 data/krx_db/YYYYMMDD.json 생성 → 맥미니 업로드
- 예상 소요: 2~4시간 (250일 × ~30초/일)
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

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

KST = ZoneInfo("Asia/Seoul")
BOT_URL = os.environ.get("BOT_URL", "https://bot.arcbot-server.org")
BOT_API_KEY = os.environ.get("BOT_API_KEY", "")


def _pi(s) -> int:
    if s is None or s == "-" or s == "":
        return 0
    try:
        if isinstance(s, float) and (s != s):
            return 0
        return int(float(str(s).replace(",", "").replace("+", "").strip() or "0"))
    except (ValueError, TypeError):
        return 0


def _pf(s) -> float:
    if s is None or s == "-" or s == "":
        return 0.0
    try:
        if isinstance(s, float) and (s != s):
            return 0.0
        return float(str(s).replace(",", "").replace("+", "").strip() or "0")
    except (ValueError, TypeError):
        return 0.0


def get_trading_dates(n_days: int = 250) -> list[str]:
    """최근 N거래일 날짜 목록 (YYYYMMDD, 최신순)."""
    from pykrx import stock
    end = datetime.now(KST).strftime("%Y%m%d")
    start = (datetime.now(KST) - timedelta(days=n_days * 2)).strftime("%Y%m%d")
    dates = stock.get_previous_business_days(fromdate=start, todate=end)
    return [d.strftime("%Y%m%d") for d in dates][-n_days:][::-1]


def build_daily(date: str) -> dict | None:
    """하루치 전종목 데이터 수집."""
    from pykrx import stock
    stocks = {}

    # 1) 시세 (OHLCV + 시총)
    for mkt in ["KOSPI", "KOSDAQ"]:
        mkt_label = mkt.lower()
        try:
            ohlcv = stock.get_market_ohlcv(date, market=mkt)
            cap = stock.get_market_cap(date, market=mkt)
            if ohlcv.empty:
                continue
            for ticker in ohlcv.index:
                o = ohlcv.loc[ticker]
                c = cap.loc[ticker] if ticker in cap.index else None
                close = int(o.get("종가", 0))
                prev = int(o.get("시가", 0))  # approximate
                stocks[ticker] = {
                    "ticker": ticker,
                    "name": ticker,
                    "market": mkt_label,
                    "close": close,
                    "open": int(o.get("시가", 0)),
                    "high": int(o.get("고가", 0)),
                    "low": int(o.get("저가", 0)),
                    "chg_pct": float(o.get("등락률", 0)),
                    "volume": int(o.get("거래량", 0)),
                    "trade_value": int(o.get("거래대금", 0)),
                    "market_cap": int(c["시가총액"]) if c is not None else 0,
                }
            print(f"  {mkt} 시세: {len(ohlcv)}종목")
        except Exception as e:
            print(f"  {mkt} 시세 실패: {e}")
        time.sleep(1)

    if not stocks:
        return None

    # 2) PER/PBR
    for mkt in ["KOSPI", "KOSDAQ"]:
        try:
            fund = stock.get_market_fundamental(date, market=mkt)
            if not fund.empty:
                for ticker in fund.index:
                    if ticker in stocks:
                        f = fund.loc[ticker]
                        stocks[ticker]["per"] = float(f.get("PER", 0))
                        stocks[ticker]["pbr"] = float(f.get("PBR", 0))
                        stocks[ticker]["eps"] = float(f.get("EPS", 0))
                        stocks[ticker]["bps"] = float(f.get("BPS", 0))
                        stocks[ticker]["div_yield"] = float(f.get("DIV", 0))
        except Exception as e:
            print(f"  {mkt} PER/PBR 실패: {e}")
        time.sleep(1)

    # 3) 수급
    for mkt in ["KOSPI", "KOSDAQ"]:
        try:
            df = stock.get_market_net_purchases_of_equities_by_ticker(date, date, market=mkt)
            if not df.empty:
                for ticker in df.index:
                    if ticker in stocks:
                        row = df.loc[ticker]
                        stocks[ticker]["foreign_net_amt"] = int(row.get("외국인합계", 0) or 0)
                        stocks[ticker]["inst_net_amt"] = int(row.get("기관합계", 0) or 0)
                        stocks[ticker]["indiv_net_amt"] = int(row.get("개인", 0) or 0)
        except Exception as e:
            print(f"  {mkt} 수급 실패: {e}")
        time.sleep(1)

    # 4) 공매도
    for mkt in ["KOSPI", "KOSDAQ"]:
        try:
            df = stock.get_shorting_balance_by_ticker(date, market=mkt)
            if not df.empty:
                for ticker in df.index:
                    if ticker in stocks:
                        row = df.loc[ticker]
                        stocks[ticker]["short_balance"] = int(row.get("공매도잔고", row.get("잔고수량", 0)) or 0)
                        stocks[ticker]["short_ratio"] = float(row.get("공매도비중", row.get("비중", 0)) or 0)
        except Exception as e:
            print(f"  {mkt} 공매도 실패: {e}")
        time.sleep(1)

    # 5) 외인 보유
    for mkt in ["KOSPI", "KOSDAQ"]:
        try:
            df = stock.get_exhaustion_rates_of_foreign_investment(date, market=mkt)
            if not df.empty:
                for ticker in df.index:
                    if ticker in stocks:
                        row = df.loc[ticker]
                        stocks[ticker]["foreign_hold_ratio"] = float(row.get("지분율", row.get("보유비율", 0)) or 0)
                        stocks[ticker]["foreign_exhaust_rate"] = float(row.get("한도소진율", 0) or 0)
        except Exception as e:
            print(f"  {mkt} 외인보유 실패: {e}")
        time.sleep(1)

    # 기본값
    for s in stocks.values():
        for key in ["per", "pbr", "eps", "bps", "div_yield"]:
            s.setdefault(key, 0.0)
        for key in ["foreign_net_amt", "inst_net_amt", "indiv_net_amt",
                     "short_balance", "short_ratio", "foreign_hold_ratio", "foreign_exhaust_rate"]:
            s.setdefault(key, 0)
        # 비율 계산
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
    return {
        "date": date,
        "updated_at": datetime.now(KST).isoformat(),
        "source": {"price": "pykrx", "valuation": "pykrx", "supply": "pykrx"},
        "market_summary": {
            "kospi_count": len(kospi),
            "kosdaq_count": len(kosdaq),
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


def upload_db(db: dict) -> dict:
    """맥미니 서버로 DB 업로드."""
    url = f"{BOT_URL.rstrip('/')}/api/krx_upload"
    headers = {"Content-Type": "application/json"}
    if BOT_API_KEY:
        headers["Authorization"] = f"Bearer {BOT_API_KEY}"
    resp = requests.post(url, json=db, headers=headers, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Upload failed: HTTP {resp.status_code} {resp.text[:300]}")
    return resp.json()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="KRX 과거 1년 백필")
    parser.add_argument("--days", type=int, default=250, help="백필 일수 (기본 250)")
    parser.add_argument("--start-from", type=int, default=0, help="시작 인덱스 (재시작용)")
    parser.add_argument("--local", action="store_true", help="로컬 저장 (업로드 안 함)")
    args = parser.parse_args()

    print(f"[Backfill] {args.days}일 백필 시작")
    dates = get_trading_dates(args.days)
    # 오래된 순서로 (시간순 저장)
    dates = dates[::-1]
    total = len(dates)
    print(f"[Backfill] {total}거래일 대상")

    success = 0
    fail = 0
    for i, date in enumerate(dates):
        if i < args.start_from:
            continue
        print(f"\n[{i+1}/{total}] {date}")
        try:
            db = build_daily(date)
            if not db:
                print(f"  → 데이터 없음 (휴장일?)")
                continue

            if args.local:
                data_dir = os.environ.get("DATA_DIR", "/data")
                db_dir = os.path.join(data_dir, "krx_db")
                os.makedirs(db_dir, exist_ok=True)
                filepath = os.path.join(db_dir, f"{date}.json")
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(db, f, ensure_ascii=False)
                print(f"  → 로컬 저장: {db['count']}종목")
            else:
                result = upload_db(db)
                print(f"  → 업로드: {result.get('count')}종목, {result.get('file_size_kb')}KB")

            success += 1
        except Exception as e:
            print(f"  → 실패: {e}")
            fail += 1
        time.sleep(2)  # rate limit

    print(f"\n[Backfill] 완료: 성공={success}, 실패={fail}, 총={total}")


if __name__ == "__main__":
    main()
