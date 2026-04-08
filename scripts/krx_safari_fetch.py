#!/usr/bin/env python3
"""
Safari 로그인 세션을 이용한 KRX data.krx.co.kr 데이터 수집.
- Safari에서 KRX 로그인(카카오)이 되어 있어야 함
- osascript로 Safari에서 fetch 실행 → localStorage → Python으로 추출
- 맥미니 cron에서 실행 (launchd)
"""

import json
import os
import subprocess
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

KST = ZoneInfo("Asia/Seoul")
_DATA_DIR = os.environ.get("DATA_DIR", "/data")
KRX_DB_DIR = os.path.join(_DATA_DIR, "krx_db")


def _pi(s) -> int:
    if not s or s == "-":
        return 0
    return int(str(s).replace(",", "").replace("+", "").strip() or "0")


def _pf(s) -> float:
    if not s or s == "-":
        return 0.0
    return float(str(s).replace(",", "").replace("+", "").strip() or "0")


def safari_fetch(bld: str, params: dict, key: str = "krx_tmp") -> list:
    """Safari fetch로 KRX JSON API 호출. Returns output records."""
    body_parts = [f"{k}={v}" for k, v in params.items()]
    body_str = "&".join(body_parts)

    js = f"""
        fetch('/comm/bldAttendant/getJsonData.cmd', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
            body: '{body_str}'
        }})
        .then(r => r.text())
        .then(t => {{ localStorage.setItem('{key}', t); document.title = 'OK_' + t.length; }})
        .catch(e => document.title = 'ERR:' + e.message);
    """

    # Execute fetch
    cmd1 = f'''tell application "Safari" to do JavaScript "{js.replace('"', '\\"').strip()}" in document 1'''
    subprocess.run(["osascript", "-e", cmd1], capture_output=True, timeout=15)
    time.sleep(3)

    # Check result
    cmd2 = 'tell application "Safari" to get name of document 1'
    r = subprocess.run(["osascript", "-e", cmd2], capture_output=True, text=True, timeout=5)
    title = r.stdout.strip()

    if not title.startswith("OK_"):
        print(f"  [Safari] 실패: {title}")
        return []

    # Extract from localStorage (chunked for large data)
    cmd3 = f'tell application "Safari" to do JavaScript "localStorage.getItem(\'{key}\')" in document 1'
    r2 = subprocess.run(["osascript", "-e", cmd3], capture_output=True, text=True, timeout=30)
    raw = r2.stdout.strip()
    # Clean localStorage after read
    subprocess.run(["osascript", "-e",
        f'tell application "Safari" to do JavaScript "localStorage.removeItem(\'{key}\')" in document 1'],
        capture_output=True, timeout=5)

    try:
        data = json.loads(raw)
        records = data.get("output", data.get("block1", data.get("OutBlock_1", [])))
        return records if isinstance(records, list) else []
    except Exception as e:
        print(f"  [Safari] JSON 파싱 실패: {e}")
        return []


def collect_all(date: str) -> dict:
    """Safari 세션으로 전종목 수급/PER·PBR/공매도/외인보유 수집."""
    result = {}

    # 1) PER/PBR/EPS/BPS/배당 (MDCSTAT03501)
    for mkt_id, mkt_label in [("STK", "KOSPI"), ("KSQ", "KOSDAQ")]:
        print(f"[{mkt_label}] PER/PBR 수집...")
        records = safari_fetch("dbms/MDC/STAT/standard/MDCSTAT03501", {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT03501",
            "locale": "ko_KR", "mktId": mkt_id, "trdDd": date,
        }, key=f"krx_fund_{mkt_id}")

        for r in records:
            ticker = r.get("ISU_SRT_CD", "")
            if not ticker:
                continue
            if ticker not in result:
                result[ticker] = {}
            result[ticker]["per"] = _pf(r.get("PER"))
            result[ticker]["pbr"] = _pf(r.get("PBR"))
            result[ticker]["eps"] = _pf(r.get("EPS"))
            result[ticker]["bps"] = _pf(r.get("BPS"))
            result[ticker]["div_yield"] = _pf(r.get("DVD_YLD"))
            result[ticker]["sector_name"] = r.get("IDX_IND_NM", "") if "IDX_IND_NM" in r else ""
        print(f"  {mkt_label} PER/PBR: {len(records)}종목")
        time.sleep(1)

    # 2) 투자자별 순매수 (MDCSTAT02401)
    for inv_code, prefix in [("9000", "foreign"), ("7050", "inst"), ("8000", "indiv")]:
        for mkt_id, mkt_label in [("STK", "KOSPI"), ("KSQ", "KOSDAQ")]:
            print(f"[{mkt_label}] 수급({prefix}) 수집...")
            records = safari_fetch("dbms/MDC/STAT/standard/MDCSTAT02401", {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT02401",
                "locale": "ko_KR", "strtDd": date, "endDd": date,
                "mktId": mkt_id, "invstTpCd": inv_code,
            }, key=f"krx_inv_{prefix}_{mkt_id}")

            for r in records:
                ticker = r.get("ISU_SRT_CD", "")
                if not ticker:
                    continue
                if ticker not in result:
                    result[ticker] = {}
                result[ticker][f"{prefix}_net_qty"] = _pi(r.get("NETBID_TRDVOL"))
                result[ticker][f"{prefix}_net_amt"] = _pi(r.get("NETBID_TRDVAL"))
            print(f"  {mkt_label} {prefix}: {len(records)}종목")
            time.sleep(1)

    # 3) 신용잔고 (MDCSTAT02501)
    for mkt_id, mkt_label in [("STK", "KOSPI"), ("KSQ", "KOSDAQ")]:
        print(f"[{mkt_label}] 신용잔고 수집...")
        records = safari_fetch("dbms/MDC/STAT/standard/MDCSTAT02501", {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT02501",
            "locale": "ko_KR", "mktId": mkt_id, "trdDd": date,
        }, key=f"krx_credit_{mkt_id}")

        for r in records:
            ticker = r.get("ISU_SRT_CD", "")
            if not ticker:
                continue
            if ticker not in result:
                result[ticker] = {}
            result[ticker]["credit_balance"] = _pi(r.get("CRED_REMN_MARG_AMT", r.get("TOTL_REMN_QTY", 0)))
        print(f"  {mkt_label} 신용잔고: {len(records)}종목")
        time.sleep(1)

    print(f"\n[총계] {len(result)}종목 수집 완료")
    return result


def merge_to_db(date: str, supplement: dict):
    """기존 daily JSON에 수급 데이터 merge."""
    filepath = os.path.join(KRX_DB_DIR, f"{date}.json")
    if not os.path.exists(filepath):
        print(f"[Merge] DB 파일 없음: {filepath}")
        return

    with open(filepath, encoding="utf-8") as f:
        db = json.load(f)

    merged = 0
    for ticker, vals in supplement.items():
        if ticker in db.get("stocks", {}):
            db["stocks"][ticker].update(vals)
            merged += 1

    # 비율 재계산
    for s in db.get("stocks", {}).values():
        mcap = s.get("market_cap", 0)
        f_amt = s.get("foreign_net_amt", 0)
        i_amt = s.get("inst_net_amt", 0)
        if mcap > 0:
            s["foreign_ratio"] = round(f_amt / mcap * 100, 4)
            s["inst_ratio"] = round(i_amt / mcap * 100, 4)
            s["fi_ratio"] = round((f_amt + i_amt) / mcap * 100, 4)

    db["supplement_at"] = datetime.now(KST).isoformat()
    db["source"]["supply"] = f"safari_krx({merged})"

    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)
    os.replace(tmp, filepath)

    size_kb = round(os.path.getsize(filepath) / 1024, 1)
    print(f"[Merge] {date}: {merged}종목 merge, {size_kb}KB")


def _last_trading_date() -> str:
    now = datetime.now(KST)
    d = now
    if d.hour < 16:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Safari KRX 데이터 수집")
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()

    date = args.date or _last_trading_date()
    print(f"[Safari KRX] 날짜: {date}")

    # Safari가 KRX에 로그인되어 있는지 확인
    cmd = 'tell application "Safari" to get URL of document 1'
    r = subprocess.run(["osascript", "-e", cmd], capture_output=True, text=True, timeout=5)
    url = r.stdout.strip()
    if "krx.co.kr" not in url:
        print("[Safari] KRX 페이지가 열려있지 않음 → 열기")
        subprocess.run(["open", "https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd"])
        time.sleep(5)

    supplement = collect_all(date)
    if supplement:
        merge_to_db(date, supplement)
    else:
        print("[WARN] 수집 데이터 없음")


if __name__ == "__main__":
    main()
