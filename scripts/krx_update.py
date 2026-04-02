#!/usr/bin/env python3
"""
KRX 전종목 일별 데이터 크롤러 (GitHub Actions 전용, 독립 실행)
- KRX data.krx.co.kr에서 전종목 시세 + PER/PBR + 투자자별 수급 크롤링
- 비율 계산 후 Railway 서버 /api/krx_upload로 POST
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
KRX_JSON_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101",
}

BOT_URL = os.environ.get("BOT_URL", "https://chic-ambition-production-d764.up.railway.app")
BOT_API_KEY = os.environ.get("BOT_API_KEY", "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 파싱 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _pi(s) -> int:
    if not s or s == "-" or s == "":
        return 0
    return int(str(s).replace(",", "").replace("+", "").strip() or "0")


def _pf(s) -> float:
    if not s or s == "-" or s == "":
        return 0.0
    return float(str(s).replace(",", "").replace("+", "").strip() or "0")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KRX HTTP
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def krx_post(form: dict) -> dict:
    resp = requests.post(KRX_JSON_URL, data=form, headers=KRX_HEADERS, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"KRX HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1) 전종목 시세 — MDCSTAT01501
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_market_data(date: str, market: str = "STK") -> list[dict]:
    form = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
        "locale": "ko_KR",
        "mktId": market,
        "trdDd": date,
        "share": "1",
        "money": "1",
    }
    try:
        body = krx_post(form)
        records = body.get("OutBlock_1", [])
        if not records:
            raise RuntimeError("empty OutBlock_1")
    except Exception as e:
        print(f"[KRX] {market} 시세 직접호출 실패: {e} → pykrx fallback")
        return _market_data_pykrx(date, market)

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


def _market_data_pykrx(date: str, market: str) -> list[dict]:
    try:
        from pykrx import stock
        mkt = "KOSPI" if market == "STK" else "KOSDAQ"
        mkt_label = "kospi" if market == "STK" else "kosdaq"
        ohlcv = stock.get_market_ohlcv(date, market=mkt)
        cap = stock.get_market_cap(date, market=mkt)
        if ohlcv.empty:
            return []
        result = []
        for ticker in ohlcv.index:
            o = ohlcv.loc[ticker]
            c = cap.loc[ticker] if ticker in cap.index else None
            result.append({
                "ticker": ticker,
                "name": ticker,
                "market": mkt_label,
                "close": int(o.get("종가", 0)),
                "chg_pct": float(o.get("등락률", 0)),
                "volume": int(o.get("거래량", 0)),
                "trade_value": int(o.get("거래대금", 0)),
                "market_cap": int(c["시가총액"]) if c is not None else 0,
            })
        print(f"[KRX] {market} pykrx fallback: {len(result)}종목")
        return result
    except Exception as e:
        print(f"[KRX] pykrx fallback 실패: {e}")
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2) 전종목 PER/PBR — MDCSTAT03901
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_fundamental(date: str, market: str = "STK") -> dict:
    form = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT03901",
        "locale": "ko_KR",
        "mktId": market,
        "trdDd": date,
    }
    try:
        body = krx_post(form)
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
        print(f"[KRX] {market} PER/PBR 실패: {e} → pykrx fallback")
        return _fundamental_pykrx(date, market)


def _fundamental_pykrx(date: str, market: str) -> dict:
    try:
        from pykrx import stock
        mkt = "KOSPI" if market == "STK" else "KOSDAQ"
        fund = stock.get_market_fundamental(date, market=mkt)
        if fund.empty:
            return {}
        result = {}
        for ticker in fund.index:
            f = fund.loc[ticker]
            result[ticker] = {"per": float(f.get("PER", 0)), "pbr": float(f.get("PBR", 0))}
        return result
    except Exception as e:
        print(f"[KRX] pykrx fundamental fallback 실패: {e}")
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3) 투자자별 순매수 — MDCSTAT02401
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_investor_data(date: str, market: str = "STK") -> dict:
    result = {}
    inv_types = [("9000", "foreign"), ("7050", "inst"), ("8000", "indiv")]
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
            body = krx_post(form)
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
        time.sleep(1)
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def build_db(date: str) -> dict:
    """전종목 시세+수급 크롤링 후 DB dict 생성."""
    print(f"[KRX] 크롤링 시작: {date}")

    # 1) 시세
    stocks = {}
    for mkt in ["STK", "KSQ"]:
        for r in fetch_market_data(date, mkt):
            stocks[r["ticker"]] = r
        time.sleep(1)

    if not stocks:
        raise RuntimeError(f"KRX 데이터 없음 (date={date}). 휴장일이거나 접근 차단.")

    # 2) PER/PBR
    for mkt in ["STK", "KSQ"]:
        for ticker, vals in fetch_fundamental(date, mkt).items():
            if ticker in stocks:
                stocks[ticker].update(vals)
        time.sleep(1)

    for s in stocks.values():
        s.setdefault("per", 0.0)
        s.setdefault("pbr", 0.0)

    # 3) 투자자별 수급
    investor_data_available = False
    for mkt in ["STK", "KSQ"]:
        inv = fetch_investor_data(date, mkt)
        if inv:
            investor_data_available = True
        for ticker, vals in inv.items():
            if ticker in stocks:
                stocks[ticker].update(vals)
        time.sleep(1)

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

    # 시장 요약
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

    return {
        "date": date,
        "updated_at": datetime.now(KST).isoformat(),
        "investor_data_available": investor_data_available,
        "market_summary": market_summary,
        "count": len(stocks),
        "stocks": stocks,
    }


def upload_to_bot(db: dict) -> dict:
    """Railway 서버로 DB 업로드."""
    url = f"{BOT_URL.rstrip('/')}/api/krx_upload"
    headers = {"Content-Type": "application/json"}
    if BOT_API_KEY:
        headers["Authorization"] = f"Bearer {BOT_API_KEY}"

    print(f"[Upload] POST {url} ({db['count']}종목, {len(json.dumps(db)) // 1024}KB)")
    resp = requests.post(url, json=db, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Upload failed: HTTP {resp.status_code} {resp.text[:300]}")
    result = resp.json()
    print(f"[Upload] 완료: {result}")
    return result


def _last_trading_date() -> str:
    """KST 기준 최근 거래일 반환 (YYYYMMDD).
    - 평일 15:30 이후 → 오늘
    - 평일 15:30 이전 → 전 거래일
    - 주말 → 직전 금요일
    """
    now = datetime.now(KST)
    d = now

    # 15:30 이전이면 전날부터 탐색
    if d.hour < 15 or (d.hour == 15 and d.minute < 30):
        d -= timedelta(days=1)

    # 주말이면 금요일로
    while d.weekday() >= 5:  # 5=토, 6=일
        d -= timedelta(days=1)

    return d.strftime("%Y%m%d")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="KRX 전종목 크롤러")
    parser.add_argument("--date", type=str, default=None,
                        help="거래일 YYYYMMDD (생략 시 KST 기준 최근 거래일)")
    args = parser.parse_args()

    date = args.date or _last_trading_date()
    print(f"[KRX] 대상 날짜: {date} (KST now={datetime.now(KST).strftime('%Y-%m-%d %H:%M')})")

    try:
        db = build_db(date)
        print(f"[KRX] 크롤링 완료: {db['count']}종목")
        result = upload_to_bot(db)
        print(f"[OK] date={result.get('date')}, count={result.get('count')}, "
              f"size={result.get('file_size_kb')}KB")
    except Exception as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
