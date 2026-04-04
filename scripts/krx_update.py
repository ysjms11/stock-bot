#!/usr/bin/env python3
"""
KRX 전종목 일별 데이터 크롤러 (GitHub Actions 전용, 독립 실행)
- 전종목 시세: KRX OPEN API (primary) → data.krx.co.kr 스크래핑 (fallback) → pykrx (fallback2)
- PER/PBR: data.krx.co.kr 스크래핑 (OPEN API 미제공) → pykrx (fallback)
- 투자자별 수급: data.krx.co.kr 스크래핑 (OPEN API 미제공)
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
# KRX OPEN API (https://openapi.krx.co.kr)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
OPEN_API_BASE = "https://data-dbg.krx.co.kr/svc/apis"
# 전종목 시세 엔드포인트
OPEN_API_ENDPOINTS = {
    "STK": f"{OPEN_API_BASE}/sto/stk_bydd_trd",   # 유가증권(KOSPI) 일별매매정보
    "KSQ": f"{OPEN_API_BASE}/sto/ksq_bydd_trd",   # 코스닥 일별매매정보
}
KRX_API_KEY = os.environ.get("KRX_API_KEY", "")

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# data.krx.co.kr 스크래핑 (fallback용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
KRX_JSON_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_OTP_URL = "https://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
KRX_CSV_URL = "https://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"
KRX_PAGE_URL = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101"

KRX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": KRX_PAGE_URL,
    "Origin": "https://data.krx.co.kr",
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
# KRX OPEN API 호출 (Primary — 전종목 시세 전용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _open_api_get(endpoint: str, date: str) -> list[dict]:
    """KRX OPEN API GET 호출. AUTH_KEY는 쿼리 파라미터로 전달."""
    if not KRX_API_KEY:
        raise RuntimeError("KRX_API_KEY 환경변수 미설정")

    params = {"AUTH_KEY": KRX_API_KEY, "basDd": date}
    resp = requests.get(endpoint, params=params, timeout=30)

    print(f"[OPEN API] GET {endpoint.split('/')[-1]} basDd={date} → "
          f"HTTP {resp.status_code}, size={len(resp.text):,}")

    if resp.status_code == 401:
        raise RuntimeError("OPEN API 인증 실패 — AUTH_KEY 만료 또는 미승인")
    if resp.status_code == 429:
        raise RuntimeError("OPEN API 일일 요청 한도(10,000회) 초과")
    if resp.status_code != 200:
        raise RuntimeError(f"OPEN API HTTP {resp.status_code}: {resp.text[:200]}")

    body = resp.json()

    # 에러 응답 체크
    if isinstance(body, dict) and body.get("respCode"):
        code = body["respCode"]
        msg = body.get("respMsg", "")
        raise RuntimeError(f"OPEN API 에러 [{code}]: {msg}")

    records = body.get("OutBlock_1", [])
    if not records:
        raise RuntimeError(f"OPEN API 빈 응답 (OutBlock_1 없음)")

    print(f"[OPEN API] {len(records)}종목 수신")
    return records


def _extract_short_ticker(isu_cd: str) -> str:
    """ISIN 코드(KR7005930003)에서 6자리 종목코드(005930) 추출."""
    if not isu_cd:
        return ""
    # ISIN: KR + 1자리 + 6자리코드 + 3자리 = 12자리
    if len(isu_cd) == 12 and isu_cd.startswith("KR"):
        return isu_cd[3:9]
    # 이미 6자리면 그대로
    if len(isu_cd) == 6 and isu_cd.isdigit():
        return isu_cd
    return ""


def fetch_market_data_openapi(date: str, market: str = "STK") -> list[dict]:
    """KRX OPEN API로 전종목 시세 조회."""
    endpoint = OPEN_API_ENDPOINTS.get(market)
    if not endpoint:
        raise RuntimeError(f"지원하지 않는 마켓: {market}")

    records = _open_api_get(endpoint, date)
    mkt_label = "kospi" if market == "STK" else "kosdaq"
    result = []
    for r in records:
        ticker = _extract_short_ticker(r.get("ISU_CD", ""))
        if not ticker or len(ticker) != 6:
            continue
        result.append({
            "ticker": ticker,
            "name": r.get("ISU_NM", ""),
            "market": mkt_label,
            "close": _pi(r.get("TDD_CLSPRC")),
            "chg_pct": _pf(r.get("FLUC_RT")),
            "volume": _pi(r.get("ACC_TRDVOL")),
            "trade_value": _pi(r.get("ACC_TRDVAL")),
            "market_cap": _pi(r.get("MKTCAP")),
        })
    print(f"[OPEN API] {market} 시세: {len(result)}종목")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# data.krx.co.kr 스크래핑 (fallback)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _get_krx_session() -> requests.Session:
    """세션 생성 + KRX 전종목 시세 페이지 방문으로 JSESSIONID 쿠키 획득."""
    sess = requests.Session()
    sess.headers.update(KRX_HEADERS)
    try:
        resp = sess.get(KRX_PAGE_URL, timeout=15)
        cookies = dict(sess.cookies)
        cookie_names = list(cookies.keys())
        has_jsession = any("JSESSIONID" in k.upper() or "SESSION" in k.upper()
                          for k in cookie_names)
        print(f"[Session] 페이지 방문 HTTP {resp.status_code}, "
              f"쿠키={cookie_names}, JSESSIONID={'있음' if has_jsession else '없음'}")
    except Exception as e:
        print(f"[Session] 페이지 방문 실패: {e}")
    return sess


def _krx_json_post(sess: requests.Session, form: dict) -> dict:
    """세션 유지하면서 KRX JSON API 호출."""
    resp = sess.post(KRX_JSON_URL, data=form, timeout=30)
    print(f"[Scrape] POST {form.get('bld','?')} → HTTP {resp.status_code}, "
          f"body={resp.text[:100]}")
    if resp.status_code != 200:
        raise RuntimeError(f"KRX HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    if isinstance(body, dict) and body.get("RESULT") == "LOGOUT":
        raise RuntimeError("KRX LOGOUT 응답 — 세션 쿠키 미인식")
    return body


def _otp_download_csv(sess: requests.Session, otp_params: dict) -> pd.DataFrame:
    """OTP 2단계: OTP 생성 → CSV 다운로드 → DataFrame 반환."""
    resp = sess.post(KRX_OTP_URL, data=otp_params, timeout=15)
    print(f"[OTP] 생성 → HTTP {resp.status_code}, body={resp.text[:80]}")
    if resp.status_code != 200 or len(resp.text) < 10:
        raise RuntimeError(f"OTP 생성 실패: HTTP {resp.status_code}, body={resp.text[:100]}")
    otp = resp.text.strip()

    resp2 = sess.post(KRX_CSV_URL, data={"code": otp}, timeout=30)
    if resp2.status_code != 200:
        raise RuntimeError(f"CSV 다운로드 실패: HTTP {resp2.status_code}")

    raw = resp2.content
    print(f"[OTP] CSV 다운로드 ({len(raw):,}bytes)")

    for enc in ("cp949", "euc-kr", "utf-8"):
        try:
            df = pd.read_csv(io.BytesIO(raw), encoding=enc)
            if not df.empty:
                print(f"[OTP] CSV 파싱 ({enc}): {len(df)}행")
                return df
        except Exception:
            continue
    raise RuntimeError("CSV 인코딩 파싱 실패")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1) 전종목 시세 — OPEN API primary → 스크래핑 fallback → pykrx
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_market_data(date: str, market: str = "STK", sess: requests.Session = None) -> list[dict]:
    mkt_label = "kospi" if market == "STK" else "kosdaq"

    # ── Primary: KRX OPEN API ──
    try:
        result = fetch_market_data_openapi(date, market)
        if result:
            return result
        raise RuntimeError("OPEN API 0종목")
    except Exception as e:
        print(f"[OPEN API] {market} 시세 실패: {e} → 스크래핑 fallback")

    # ── Fallback 1: 세션 기반 JSON 스크래핑 ──
    if sess:
        try:
            form = {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
                "locale": "ko_KR",
                "mktId": market,
                "trdDd": date,
                "share": "1",
                "money": "1",
            }
            body = _krx_json_post(sess, form)
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
            print(f"[Scrape] {market} 시세: {len(result)}종목")
            if result:
                return result
        except Exception as e:
            print(f"[Scrape] {market} 시세 실패: {e} → pykrx fallback")

    # ── Fallback 2: pykrx ──
    return _market_data_pykrx(date, market)


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
        print(f"[pykrx] {market} fallback: {len(result)}종목")
        return result
    except Exception as e:
        print(f"[pykrx] fallback 실패: {e}")
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2) 전종목 PER/PBR — OPEN API 미제공, 스크래핑 only
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_fundamental(date: str, market: str = "STK", sess: requests.Session = None) -> dict:
    # ── Primary: 세션 기반 JSON 스크래핑 ──
    if sess:
        try:
            form = {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT03901",
                "locale": "ko_KR",
                "mktId": market,
                "trdDd": date,
            }
            body = _krx_json_post(sess, form)
            records = body.get("output", body.get("OutBlock_1", []))
            result = {}
            for r in records:
                ticker = r.get("ISU_SRT_CD", "")
                if ticker:
                    result[ticker] = {
                        "per": _pf(r.get("PER", "0")),
                        "pbr": _pf(r.get("PBR", "0")),
                    }
            print(f"[Scrape] {market} PER/PBR: {len(result)}종목")
            if result:
                return result
            raise RuntimeError("PER/PBR 파싱 결과 0종목")
        except Exception as e:
            print(f"[Scrape] {market} PER/PBR 실패: {e} → OTP CSV fallback")

        # ── Fallback: OTP CSV ──
        try:
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
        except Exception as e:
            print(f"[OTP] {market} PER/PBR 실패: {e} → pykrx fallback")

    # ── Fallback: pykrx ──
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
        print(f"[pykrx] fundamental fallback 실패: {e}")
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3) 투자자별 순매수 — OPEN API 미제공, 스크래핑 only
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_investor_data(date: str, market: str = "STK", sess: requests.Session = None) -> dict:
    result = {}
    inv_types = [("9000", "foreign"), ("7050", "inst"), ("8000", "indiv")]

    for inv_code, prefix in inv_types:
        got_data = False

        # ── Primary: 세션 기반 JSON 스크래핑 ──
        if sess:
            try:
                form = {
                    "bld": "dbms/MDC/STAT/standard/MDCSTAT02401",
                    "locale": "ko_KR",
                    "strtDd": date,
                    "endDd": date,
                    "mktId": market,
                    "invstTpCd": inv_code,
                }
                body = _krx_json_post(sess, form)
                records = body.get("output", body.get("OutBlock_1", []))
                for r in records:
                    ticker = r.get("ISU_SRT_CD", "")
                    if not ticker:
                        continue
                    if ticker not in result:
                        result[ticker] = {}
                    result[ticker][f"{prefix}_net_qty"] = _pi(r.get("NETBID_TRDVOL"))
                    result[ticker][f"{prefix}_net_amt"] = _pi(r.get("NETBID_TRDVAL"))
                print(f"[Scrape] {market} 투자자({prefix}): {len(records)}종목")
                if records:
                    got_data = True
            except Exception as e:
                print(f"[Scrape] {market} 투자자({prefix}) 실패: {e} → OTP CSV fallback")

        if got_data:
            time.sleep(1)
            continue

        # ── Fallback: OTP CSV ──
        if sess:
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
            except Exception as e:
                print(f"[OTP] {market} 투자자({prefix}) 실패: {e}")
        time.sleep(1)
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def build_db(date: str) -> dict:
    """전종목 시세+수급 크롤링 후 DB dict 생성."""
    print(f"[KRX] 크롤링 시작: {date}")
    print(f"[KRX] OPEN API KEY: {'설정됨' if KRX_API_KEY else '미설정 → 스크래핑만 사용'}")

    # 스크래핑 fallback용 세션 (OPEN API 실패 시 사용)
    sess = _get_krx_session()

    # 1) 시세 — OPEN API primary
    stocks = {}
    for mkt in ["STK", "KSQ"]:
        for r in fetch_market_data(date, mkt, sess=sess):
            stocks[r["ticker"]] = r
        time.sleep(1)

    if not stocks:
        raise RuntimeError(f"KRX 데이터 없음 (date={date}). 휴장일이거나 접근 차단.")

    # 2) PER/PBR — 스크래핑 only (OPEN API 미제공)
    for mkt in ["STK", "KSQ"]:
        for ticker, vals in fetch_fundamental(date, mkt, sess=sess).items():
            if ticker in stocks:
                stocks[ticker].update(vals)
        time.sleep(1)

    for s in stocks.values():
        s.setdefault("per", 0.0)
        s.setdefault("pbr", 0.0)

    # 3) 투자자별 수급 — 스크래핑 only (OPEN API 미제공)
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
