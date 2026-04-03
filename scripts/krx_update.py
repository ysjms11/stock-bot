#!/usr/bin/env python3
"""
KRX 전종목 일별 데이터 크롤러 (GitHub Actions 전용, 독립 실행)
- KRX data.krx.co.kr에서 전종목 시세 + PER/PBR + 투자자별 수급 크롤링
- OTP 기반 CSV 다운로드 (primary) → JSON 직접호출 (fallback) → pykrx (fallback2)
- 비율 계산 후 Railway 서버 /api/krx_upload로 POST
"""

import io
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

KST = ZoneInfo("Asia/Seoul")

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# URLs & 헤더
# ━━━━━━━━━━━━━━━━━━━━━━━━━
KRX_OTP_URL = "https://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
KRX_CSV_URL = "https://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"
KRX_JSON_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

KRX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiStat/mkd/shortSalesByStkDaily/main.cmd",
}

BOT_URL = os.environ.get("BOT_URL", "https://chic-ambition-production-d764.up.railway.app")
BOT_API_KEY = os.environ.get("BOT_API_KEY", "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 파싱 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _pi(s) -> int:
    if s is None or s == "-" or s == "":
        return 0
    try:
        if isinstance(s, float) and (s != s):  # NaN check
            return 0
        return int(str(s).replace(",", "").replace("+", "").strip() or "0")
    except (ValueError, TypeError):
        return 0


def _pf(s) -> float:
    if s is None or s == "-" or s == "":
        return 0.0
    try:
        if isinstance(s, float) and (s != s):  # NaN check
            return 0.0
        return float(str(s).replace(",", "").replace("+", "").strip() or "0")
    except (ValueError, TypeError):
        return 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# OTP 기반 CSV 다운로드 (Primary)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _get_krx_session() -> requests.Session:
    """세션 생성 + KRX 메인 페이지 방문으로 쿠키 획득."""
    sess = requests.Session()
    sess.headers.update(KRX_HEADERS)
    try:
        sess.get("https://data.krx.co.kr/contents/MDC/MDI/mdiStat/mkd/shortSalesByStkDaily/main.cmd",
                 timeout=10)
        print("[OTP] 세션 쿠키 획득 완료")
    except Exception as e:
        print(f"[OTP] 세션 쿠키 획득 실패 (무시): {e}")
    return sess


def _otp_download_csv(sess: requests.Session, otp_params: dict) -> pd.DataFrame:
    """OTP 2단계: OTP 생성 → CSV 다운로드 → DataFrame 반환."""
    # Step 1: OTP 생성
    resp = sess.post(KRX_OTP_URL, data=otp_params, timeout=15)
    if resp.status_code != 200 or len(resp.text) < 10:
        raise RuntimeError(f"OTP 생성 실패: HTTP {resp.status_code}, body={resp.text[:100]}")
    otp = resp.text.strip()
    print(f"[OTP] 토큰 생성 성공 (len={len(otp)})")

    # Step 2: CSV 다운로드
    resp2 = sess.post(KRX_CSV_URL, data={"code": otp}, timeout=30)
    if resp2.status_code != 200:
        raise RuntimeError(f"CSV 다운로드 실패: HTTP {resp2.status_code}")

    raw = resp2.content
    print(f"[OTP] CSV 다운로드 완료 ({len(raw):,}bytes)")

    # 인코딩 시도: cp949 → euc-kr → utf-8
    for enc in ("cp949", "euc-kr", "utf-8"):
        try:
            df = pd.read_csv(io.BytesIO(raw), encoding=enc)
            if not df.empty:
                print(f"[OTP] CSV 파싱 성공 ({enc}): {len(df)}행, 컬럼={list(df.columns[:5])}")
                return df
        except Exception:
            continue
    raise RuntimeError("CSV 인코딩 파싱 실패 (cp949/euc-kr/utf-8 모두 실패)")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# JSON 직접 호출 (Fallback 1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _krx_json_post(form: dict) -> dict:
    resp = requests.post(KRX_JSON_URL, data=form, headers=KRX_HEADERS, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"KRX HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    # "LOGOUT" 응답 체크
    if isinstance(body, dict) and body.get("RESULT") == "LOGOUT":
        raise RuntimeError("KRX LOGOUT 응답 — 세션/OTP 필요")
    return body


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1) 전종목 시세 — MDCSTAT01501
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_market_data(date: str, market: str = "STK", sess: requests.Session = None) -> list[dict]:
    mkt_label = "kospi" if market == "STK" else "kosdaq"

    # ── Primary: OTP CSV ──
    try:
        sess = sess or _get_krx_session()
        otp_params = {
            "locale": "ko_KR",
            "mktId": market,
            "trdDd": date,
            "share": "1",
            "money": "1",
            "csvxls_isNo": "false",
            "name": "fileDown",
            "url": "dbms/MDC/STAT/standard/MDCSTAT01501",
        }
        df = _otp_download_csv(sess, otp_params)
        result = []
        for _, row in df.iterrows():
            ticker = str(row.get("종목코드", row.iloc[0]) if "종목코드" in df.columns else row.iloc[0]).strip()
            if not ticker or len(ticker) != 6:
                continue
            name_col = "종목명" if "종목명" in df.columns else df.columns[1]
            close_col = "종가" if "종가" in df.columns else "TDD_CLSPRC"
            chg_col = "등락률" if "등락률" in df.columns else "FLUC_RT"
            vol_col = "거래량" if "거래량" in df.columns else "ACC_TRDVOL"
            tv_col = "거래대금" if "거래대금" in df.columns else "ACC_TRDVAL"
            cap_col = "시가총액" if "시가총액" in df.columns else "MKTCAP"
            result.append({
                "ticker": ticker,
                "name": str(row.get(name_col, "")),
                "market": mkt_label,
                "close": _pi(row.get(close_col, 0)),
                "chg_pct": _pf(row.get(chg_col, 0)),
                "volume": _pi(row.get(vol_col, 0)),
                "trade_value": _pi(row.get(tv_col, 0)),
                "market_cap": _pi(row.get(cap_col, 0)),
            })
        print(f"[OTP] {market} 시세: {len(result)}종목")
        if result:
            return result
        raise RuntimeError("OTP CSV 파싱 결과 0종목")
    except Exception as e:
        print(f"[OTP] {market} 시세 OTP 실패: {e} → JSON fallback")

    # ── Fallback 1: JSON 직접호출 ──
    try:
        form = {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
            "locale": "ko_KR",
            "mktId": market,
            "trdDd": date,
            "share": "1",
            "money": "1",
        }
        body = _krx_json_post(form)
        records = body.get("OutBlock_1", [])
        if not records:
            raise RuntimeError("empty OutBlock_1")
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
        print(f"[JSON] {market} 시세: {len(result)}종목")
        return result
    except Exception as e:
        print(f"[JSON] {market} 시세 실패: {e} → pykrx fallback")

    # ── Fallback 2: pykrx (세션 워밍업 포함) ──
    return _market_data_pykrx(date, market)


def _market_data_pykrx(date: str, market: str) -> list[dict]:
    try:
        # pykrx 전에 세션 워밍업
        _warmup_pykrx_session()
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
        print(f"[pykrx] {market} fallback: {len(result)}종목")
        return result
    except Exception as e:
        print(f"[pykrx] fallback 실패: {e}")
        return []


def _warmup_pykrx_session():
    """pykrx 호출 전 KRX 메인 페이지 방문으로 세션 쿠키 확보."""
    try:
        sess = requests.Session()
        sess.headers.update(KRX_HEADERS)
        sess.get("https://data.krx.co.kr/", timeout=10)
        # pykrx 내부 세션에 쿠키 주입 시도
        try:
            from pykrx.website.krx import krxio
            if hasattr(krxio, '_session'):
                for cookie in sess.cookies:
                    krxio._session.cookies.set(cookie.name, cookie.value)
                print("[pykrx] 세션 쿠키 주입 완료")
        except Exception:
            pass
    except Exception as e:
        print(f"[pykrx] 세션 워밍업 실패 (무시): {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2) 전종목 PER/PBR — MDCSTAT03901
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_fundamental(date: str, market: str = "STK", sess: requests.Session = None) -> dict:
    # ── Primary: OTP CSV ──
    try:
        sess = sess or _get_krx_session()
        otp_params = {
            "locale": "ko_KR",
            "mktId": market,
            "trdDd": date,
            "csvxls_isNo": "false",
            "name": "fileDown",
            "url": "dbms/MDC/STAT/standard/MDCSTAT03901",
        }
        df = _otp_download_csv(sess, otp_params)
        result = {}
        ticker_col = "종목코드" if "종목코드" in df.columns else df.columns[0]
        per_col = next((c for c in df.columns if "PER" in str(c)), "PER")
        pbr_col = next((c for c in df.columns if "PBR" in str(c)), "PBR")
        for _, row in df.iterrows():
            ticker = str(row.get(ticker_col, "")).strip()
            if ticker and len(ticker) == 6:
                result[ticker] = {
                    "per": _pf(row.get(per_col, 0)),
                    "pbr": _pf(row.get(pbr_col, 0)),
                }
        print(f"[OTP] {market} PER/PBR: {len(result)}종목")
        if result:
            return result
        raise RuntimeError("OTP CSV PER/PBR 파싱 결과 0종목")
    except Exception as e:
        print(f"[OTP] {market} PER/PBR OTP 실패: {e} → JSON fallback")

    # ── Fallback 1: JSON ──
    try:
        form = {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT03901",
            "locale": "ko_KR",
            "mktId": market,
            "trdDd": date,
        }
        body = _krx_json_post(form)
        records = body.get("output", body.get("OutBlock_1", []))
        result = {}
        for r in records:
            ticker = r.get("ISU_SRT_CD", "")
            if ticker:
                result[ticker] = {
                    "per": _pf(r.get("PER", "0")),
                    "pbr": _pf(r.get("PBR", "0")),
                }
        print(f"[JSON] {market} PER/PBR: {len(result)}종목")
        return result
    except Exception as e:
        print(f"[JSON] {market} PER/PBR 실패: {e} → pykrx fallback")

    # ── Fallback 2: pykrx ──
    return _fundamental_pykrx(date, market)


def _fundamental_pykrx(date: str, market: str) -> dict:
    try:
        _warmup_pykrx_session()
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
        print(f"[pykrx] fundamental fallback 실패: {e}")
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3) 투자자별 순매수 — MDCSTAT02401
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_investor_data(date: str, market: str = "STK", sess: requests.Session = None) -> dict:
    result = {}
    inv_types = [("9000", "foreign"), ("7050", "inst"), ("8000", "indiv")]

    sess = sess or _get_krx_session()
    for inv_code, prefix in inv_types:
        got_data = False
        # ── Primary: OTP CSV ──
        try:
            otp_params = {
                "locale": "ko_KR",
                "mktId": market,
                "strtDd": date,
                "endDd": date,
                "invstTpCd": inv_code,
                "csvxls_isNo": "false",
                "name": "fileDown",
                "url": "dbms/MDC/STAT/standard/MDCSTAT02401",
            }
            df = _otp_download_csv(sess, otp_params)
            ticker_col = "종목코드" if "종목코드" in df.columns else df.columns[0]
            qty_col = next((c for c in df.columns if "순매수량" in str(c) or "NETBID_TRDVOL" in str(c)), None)
            amt_col = next((c for c in df.columns if "순매수금액" in str(c) or "순매수대금" in str(c) or "NETBID_TRDVAL" in str(c)), None)
            count = 0
            for _, row in df.iterrows():
                ticker = str(row.get(ticker_col, "")).strip()
                if not ticker or len(ticker) != 6:
                    continue
                if ticker not in result:
                    result[ticker] = {}
                result[ticker][f"{prefix}_net_qty"] = _pi(row.get(qty_col, 0)) if qty_col else 0
                result[ticker][f"{prefix}_net_amt"] = _pi(row.get(amt_col, 0)) if amt_col else 0
                count += 1
            print(f"[OTP] {market} 투자자({prefix}): {count}종목")
            if count > 0:
                got_data = True
        except Exception as e:
            print(f"[OTP] {market} 투자자({prefix}) OTP 실패: {e} → JSON fallback")

        if got_data:
            time.sleep(1)
            continue

        # ── Fallback 1: JSON ──
        try:
            form = {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT02401",
                "locale": "ko_KR",
                "strtDd": date,
                "endDd": date,
                "mktId": market,
                "invstTpCd": inv_code,
            }
            body = _krx_json_post(form)
            records = body.get("output", body.get("OutBlock_1", []))
            for r in records:
                ticker = r.get("ISU_SRT_CD", "")
                if not ticker:
                    continue
                if ticker not in result:
                    result[ticker] = {}
                result[ticker][f"{prefix}_net_qty"] = _pi(r.get("NETBID_TRDVOL"))
                result[ticker][f"{prefix}_net_amt"] = _pi(r.get("NETBID_TRDVAL"))
            print(f"[JSON] {market} 투자자({prefix}): {len(records)}종목")
        except Exception as e:
            print(f"[JSON] {market} 투자자({prefix}) 실패: {e}")
        time.sleep(1)
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def build_db(date: str) -> dict:
    """전종목 시세+수급 크롤링 후 DB dict 생성."""
    print(f"[KRX] 크롤링 시작: {date}")

    # 세션 1개를 전체 크롤링에 재사용
    sess = _get_krx_session()

    # 1) 시세
    stocks = {}
    for mkt in ["STK", "KSQ"]:
        for r in fetch_market_data(date, mkt, sess=sess):
            stocks[r["ticker"]] = r
        time.sleep(1)

    if not stocks:
        raise RuntimeError(f"KRX 데이터 없음 (date={date}). 휴장일이거나 접근 차단.")

    # 2) PER/PBR
    for mkt in ["STK", "KSQ"]:
        for ticker, vals in fetch_fundamental(date, mkt, sess=sess).items():
            if ticker in stocks:
                stocks[ticker].update(vals)
        time.sleep(1)

    for s in stocks.values():
        s.setdefault("per", 0.0)
        s.setdefault("pbr", 0.0)

    # 3) 투자자별 수급
    investor_data_available = False
    for mkt in ["STK", "KSQ"]:
        inv = fetch_investor_data(date, mkt, sess=sess)
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
