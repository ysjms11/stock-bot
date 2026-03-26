import json
import os
import asyncio
import uuid
import aiohttp
import traceback
from datetime import datetime, timedelta
from aiohttp import web

from kis_api import *
from kis_api import (
    _is_us_ticker, _guess_excd, _kis_get,
    _fetch_sector_flow, _TICKER_SECTOR,
    ws_manager, get_ws_tickers,
    collect_macro_data, format_macro_msg,
    check_drawdown, PORTFOLIO_HISTORY_FILE,
    load_trade_log, save_trade_log, get_trade_stats, TRADE_LOG_FILE,
)

_mcp_sessions: dict = {}   # session_id → asyncio.Queue

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 스크리너 당일 결과 캐시
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_DART_CACHE_FILE = "/data/dart_screener_cache.json"

def _load_dart_screener_cache(mode: str, cache_key: str) -> dict | None:
    """당일 mode+cache_key 에 해당하는 캐시 반환. 없으면 None."""
    today = datetime.now().strftime("%Y%m%d")
    try:
        if os.path.exists(_DART_CACHE_FILE):
            data = json.load(open(_DART_CACHE_FILE, encoding="utf-8"))
            day = data.get(today, {})
            entry = day.get(cache_key)
            if entry:
                print(f"[dart_cache] 캐시 히트: {cache_key}")
                return entry
    except Exception as e:
        print(f"[dart_cache] 로드 오류: {e}")
    return None

def _save_dart_screener_cache(cache_key: str, result: dict):
    """당일 캐시에 결과 저장. 오늘 날짜 외 항목은 자동 삭제."""
    today = datetime.now().strftime("%Y%m%d")
    try:
        data = {}
        if os.path.exists(_DART_CACHE_FILE):
            try:
                data = json.load(open(_DART_CACHE_FILE, encoding="utf-8"))
            except Exception:
                pass
        today_map = data.get(today, {})
        today_map[cache_key] = result
        # 오늘 날짜 외 항목 제거 (캐시 파일 비대화 방지)
        with open(_DART_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({today: today_map}, f, ensure_ascii=False)
        print(f"[dart_cache] 저장: {cache_key} ({result.get('count', 0)}건)")
    except Exception as e:
        print(f"[dart_cache] 저장 오류: {e}")

# DART 공시 중요도 태그 키워드
_DART_TAGS = {
    "긴급": ["유상증자", "전환사채", "신주인수권부사채", "CB", "BW",
             "분할", "합병", "감자", "상장폐지", "회생", "공개매수"],
    "주의": ["수주", "계약", "대규모", "공급계약", "납품", "MOU", "투자",
             "소송", "제재", "과징금", "조회공시"],
    "참고": ["임원", "지분", "자기주식", "자사주", "배당",
             "주식매수선택권", "스톡옵션", "정관"],
}


def _dart_tag(title: str) -> str:
    for level, keywords in _DART_TAGS.items():
        if any(k in title for k in keywords):
            return level
    return "일반"


def _pf(val) -> float:
    """영업이익 등 재무 수치 문자열을 float으로 변환 (콤마 제거 포함)"""
    try:
        return float(str(val).replace(",", "").strip() or "0")
    except Exception:
        return 0.0


def _nf(val):
    """재무 수치 문자열 → float 변환, 빈값이면 None"""
    s = str(val).replace(",", "").strip()
    try:
        return float(s) if s else None
    except Exception:
        return None


_TREND_PRIORITY = {"연속증가": 0, "흑자전환": 1, "감소": 2, "적자전환": 3, "적자지속": 4}


def _calc_qoq(quarterly: list) -> dict:
    """quarterly 데이터에서 QoQ 필드 계산.
    quarterly[2] = 영업이익 분기 행, .op=전분기, .ebt=최근분기 (annual과 동일 패턴)"""
    r = {"qoq_growth": None, "recent_quarter_op": None, "prev_quarter_op": None, "op_trend": None}
    if len(quarterly) < 3:
        return r
    q_row = quarterly[2]
    rq = _nf(q_row.get("ebt"))
    pq = _nf(q_row.get("op"))
    if rq is None or pq is None:
        return r
    r["recent_quarter_op"] = round(rq)
    r["prev_quarter_op"]   = round(pq)
    if abs(pq) > 0:
        r["qoq_growth"] = round((rq - pq) / abs(pq) * 100, 1)
    if pq < 0 and rq > 0:
        r["op_trend"] = "흑자전환"
    elif pq > 0 and rq < 0:
        r["op_trend"] = "적자전환"
    elif pq <= 0 and rq <= 0:
        r["op_trend"] = "적자지속"
    elif pq > 0 and rq > pq:
        r["op_trend"] = "연속증가"
    else:
        r["op_trend"] = "감소"
    return r


async def _scan_conv_one(ticker: str, name: str, token: str, sem: asyncio.Semaphore, spread_threshold: float):
    """convergence 스캔 단위 함수 (모듈 레벨 — closure 없이 파라미터 명시적 전달)"""
    async with sem:
        await asyncio.sleep(0.1)
        try:
            closes = await kis_daily_closes(ticker, token)
            valid = [c for c in closes[:60] if c > 0]
            if len(valid) < 60:
                return None
            ma5  = sum(valid[:5])  / 5
            ma20 = sum(valid[:20]) / 20
            ma60 = sum(valid[:60]) / 60
            cur  = valid[0]
            sp = (max(ma5, ma20, ma60) - min(ma5, ma20, ma60)) / cur * 100
            if sp <= spread_threshold:
                disp_20 = round((cur - ma20) / ma20 * 100, 2)
                disp_60 = round((cur - ma60) / ma60 * 100, 2)
                return {"ticker": ticker, "name": name, "price": cur,
                        "spread": round(sp, 2), "ma5": round(ma5),
                        "ma20": round(ma20), "ma60": round(ma60),
                        "disp_20": disp_20, "disp_60": disp_60}
        except Exception as e:
            print(f"[convergence] {ticker} 오류: {e}")
        return None


def _op_extra_fields(annual: list) -> dict:
    """annual 데이터에서 매출/영업이익률 보조 필드 계산. 실패 시 null 반환."""
    rev_recent = rev_prev = op_margin = rev_growth = None
    try:
        rev_recent = _pf(annual[0].get("ebt")) if len(annual) > 0 else None
        rev_prev   = _pf(annual[0].get("op"))  if len(annual) > 0 else None
    except Exception:
        pass
    try:
        if rev_recent is not None and rev_prev is not None and abs(rev_prev) > 0:
            rev_growth = round((rev_recent - rev_prev) / abs(rev_prev) * 100, 1)
    except Exception:
        pass
    try:
        op_recent_val = _pf(annual[2].get("ebt")) if len(annual) > 2 else None
        if op_recent_val is not None and rev_recent is not None and rev_recent > 0:
            op_margin = round(op_recent_val / rev_recent * 100, 1)
    except Exception:
        pass
    return {
        "op_margin":  op_margin,
        "rev_recent": round(rev_recent) if rev_recent is not None else None,
        "rev_prev":   round(rev_prev)   if rev_prev   is not None else None,
        "rev_growth": rev_growth,
        "period":     "최근연도 vs 전년도",
    }


async def _scan_op_one(ticker: str, name: str, token: str, sem: asyncio.Semaphore, min_growth: float):
    """op_growth 스캔 단위 함수 (모듈 레벨 — closure 없이 파라미터 명시적 전달)"""
    async with sem:
        await asyncio.sleep(0.07)
        try:
            raw = await kis_estimate_perform(ticker, token)
            annual = raw.get("annual", [])
            # annual[2] = 영업이익 행 (행=지표, 열=연도)
            # .op = 전년도, .ebt = 최근 연도 (열 매핑: rev<op<ebt<np<eps 순)
            if len(annual) < 3:
                return None
            op_recent = _pf(annual[2].get("ebt"))
            op_prev   = _pf(annual[2].get("op"))
            if op_prev <= 0:
                return None
            growth_pct = (op_recent - op_prev) / abs(op_prev) * 100
            if growth_pct >= min_growth:
                return {"ticker": ticker, "name": name,
                        "op_recent": round(op_recent),
                        "op_prev":   round(op_prev),
                        "growth_pct": round(growth_pct, 1),
                        **_op_extra_fields(annual),
                        **_calc_qoq(raw.get("quarterly", []))}
        except Exception as e:
            print(f"[op_growth] {ticker} 오류: {e}")
        return None


async def _scan_turnaround_one(ticker: str, name: str, token: str, sem: asyncio.Semaphore):
    """op_turnaround 스캔 단위 함수 — 영업이익 적자→흑자 전환 종목 필터"""
    async with sem:
        await asyncio.sleep(0.07)
        try:
            raw = await kis_estimate_perform(ticker, token)
            annual = raw.get("annual", [])
            if len(annual) < 3:
                return None
            op_recent = _pf(annual[2].get("ebt"))
            op_prev   = _pf(annual[2].get("op"))
            if op_prev < 0 and op_recent > 0:
                return {"ticker": ticker, "name": name,
                        "op_recent": round(op_recent),
                        "op_prev":   round(op_prev),
                        **_op_extra_fields(annual),
                        **_calc_qoq(raw.get("quarterly", []))}
        except Exception as e:
            print(f"[op_turnaround] {ticker} 오류: {e}")
        return None


async def _scan_dart_op_one(ticker: str, name: str, corp_code: str, sem: asyncio.Semaphore, min_growth: float, recent_year: int, token: str = ""):
    """dart_op_growth 스캔 단위 — 연간 영업이익 YoY 비교 + QoQ (KIS 분기 fallback)"""
    try:
        async with sem:
            r_recent = await dart_quarterly_op(corp_code, recent_year, 4)
        async with sem:
            r_prev = await dart_quarterly_op(corp_code, recent_year - 1, 4)
        if not r_recent or not r_prev:
            return None
        op_recent = r_recent["op_profit"]
        op_prev   = r_prev["op_profit"]
        if op_recent is None or op_prev is None or op_prev <= 0:
            return None
        growth_pct = (op_recent - op_prev) / abs(op_prev) * 100
        if growth_pct < min_growth:
            return None
        rev_recent = r_recent.get("revenue")
        rev_prev   = r_prev.get("revenue")
        op_margin  = round(op_recent / rev_recent * 100, 1) if rev_recent and rev_recent > 0 else None
        rev_growth = round((rev_recent - rev_prev) / abs(rev_prev) * 100, 1) if rev_recent and rev_prev and rev_prev != 0 else None
        # QoQ: KIS 분기 추정실적 활용 (DART 분기보고서 대신)
        qoq_fields = {"qoq_growth": None, "recent_quarter_op": None, "prev_quarter_op": None, "op_trend": None}
        if token:
            try:
                raw_q = await kis_estimate_perform(ticker, token)
                qoq_fields = _calc_qoq(raw_q.get("quarterly", []))
            except Exception:
                pass
        return {"ticker": ticker, "name": name,
                "period": f"{recent_year}연간 vs {recent_year - 1}연간",
                "op_recent": op_recent, "op_prev": op_prev,
                "growth_pct": round(growth_pct, 1),
                "op_margin": op_margin, "rev_recent": rev_recent, "rev_growth": rev_growth,
                **qoq_fields}
    except Exception as e:
        print(f"[dart_op_growth] {ticker} 오류: {e}")
    return None


async def _scan_dart_turnaround_one(ticker: str, name: str, corp_code: str, sem: asyncio.Semaphore, recent_year: int, token: str = ""):
    """dart_turnaround 스캔 단위 — 영업이익 적자→흑자 전환 + QoQ (KIS 분기 fallback)"""
    try:
        async with sem:
            r_recent = await dart_quarterly_op(corp_code, recent_year, 4)
        async with sem:
            r_prev = await dart_quarterly_op(corp_code, recent_year - 1, 4)
        if not r_recent or not r_prev:
            return None
        op_recent = r_recent["op_profit"]
        op_prev   = r_prev["op_profit"]
        if op_recent is None or op_prev is None:
            return None
        if not (op_prev < 0 and op_recent > 0):
            return None
        rev_recent = r_recent.get("revenue")
        op_margin  = round(op_recent / rev_recent * 100, 1) if rev_recent and rev_recent > 0 else None
        # QoQ: KIS 분기 추정실적 활용
        qoq_fields = {"qoq_growth": None, "recent_quarter_op": None, "prev_quarter_op": None, "op_trend": None}
        if token:
            try:
                raw_q = await kis_estimate_perform(ticker, token)
                qoq_fields = _calc_qoq(raw_q.get("quarterly", []))
            except Exception:
                pass
        return {"ticker": ticker, "name": name,
                "period": f"{recent_year}연간 vs {recent_year - 1}연간",
                "op_recent": op_recent, "op_prev": op_prev,
                "op_margin": op_margin, "rev_recent": rev_recent,
                **qoq_fields}
    except Exception as e:
        print(f"[dart_turnaround] {ticker} 오류: {e}")
    return None


MCP_TOOLS = [
    {"name": "scan_market",    "description": "거래량 상위 종목 스캔",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_portfolio",
     "description": "포트폴리오 조회 또는 수정. mode 생략 시 현재가·손익 조회. mode='set' 시 포트폴리오 저장. cash_krw/cash_usd로 현금 잔고 업데이트 가능.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode":     {"type": "string", "description": "'set' 이면 저장 모드. 생략 시 조회."},
                         "market":   {"type": "string", "description": "[set] 'KR' 또는 'US'"},
                         "holdings": {"type": "object", "description": "[set] KR: {종목코드: {name, qty, avg_price}}, US: {심볼: {name, qty, avg_price}}"},
                         "cash_krw": {"type": "number", "description": "[set] 원화 현금 잔고 (원)"},
                         "cash_usd": {"type": "number", "description": "[set] 달러 현금 잔고 (USD)"},
                     },
                     "required": []}},
    {"name": "get_stock_detail","description": "개별 종목 상세: 현재가·PER·PBR·수급 또는 일봉 조회. 한국/미국 자동 판별. period 지정 시 일봉 반환.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드(예: 005930) 또는 미국 티커(예: TSLA, AAPL)"},
                         "period": {"type": "string", "description": "일봉 조회 시 지정 (예: D60=최근 60일, D30=30일, W20=20주). 생략 시 현재가 상세 반환"},
                     },
                     "required": ["ticker"]}},
    {"name": "get_foreign_rank","description": "외국인 순매수 상위 종목",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_dart",       "description": "워치리스트 최근 3일 DART 공시",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_macro",
     "description": "매크로 지표 조회. mode 생략 시 KOSPI·KOSDAQ·환율. mode='dashboard': VIX·WTI·금·구리·DXY·US10Y 등 전체. mode='sector_etf': 섹터 ETF 시세. mode='convergence': 이평선 수렴 스크리너 (disp_20/disp_60 이격도 포함, market/sort 지원). mode='convergence2': 코스닥 위주 하위호환. mode='op_growth': KIS 영업이익 증가율 스크리너. mode='op_turnaround': KIS 적자→흑자 전환. mode='dart_op_growth': DART 기반 연간 영업이익 성장률 스크리너. mode='dart_turnaround': DART 기반 적자→흑자 전환.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode":       {"type": "string", "description": "'dashboard'|'sector_etf'|'convergence'|'convergence2'|'op_growth'|'op_turnaround'|'dart_op_growth'|'dart_turnaround'|생략"},
                         "spread":     {"type": "number", "description": "[convergence] 이평 수렴 기준 % (기본 5.0)"},
                         "market":     {"type": "string", "description": "[convergence] 'all'=코스피+코스닥(기본), 'kospi'=코스피위주, 'kosdaq'=코스닥위주"},
                         "sort":       {"type": "string", "description": "[convergence] 'spread'=수렴도순(기본), 'disp_20'=20일이격도순, 'disp_60'=60일이격도순. [op_growth/op_turnaround/dart_op_growth/dart_turnaround] 'yoy'=연간증가율순(기본), 'qoq'=분기증가율순, 'trend'=분기추세순(연속증가>흑자전환>감소>적자전환>적자지속)"},
                         "min_growth": {"type": "number", "description": "[op_growth/dart_op_growth] 영업이익 최소 증가율 % (기본 50)"},
                     },
                     "required": []}},
    {"name": "get_sector_flow","description": "WI26 주요 업종별 외국인+기관 순매수금액 상위/하위 3개",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "add_watch",      "description": "한국 워치리스트에 종목 추가",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드 (예: 005930)"},
                         "name":   {"type": "string", "description": "종목명 (예: 삼성전자)"},
                     },
                     "required": ["ticker", "name"]}},
    {"name": "remove_watch",   "description": "한국 워치리스트에서 종목 제거. alert_type='buy_alert' 시 매수감시 제거",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드 (예: 005930) 또는 미국 티커"},
                         "alert_type": {"type": "string", "description": "삭제 대상: 'watchlist'(기본) 또는 'buy_alert'(매수감시 제거)"},
                     },
                     "required": ["ticker"]}},
    {"name": "get_alerts",     "description": "손절가 목록 + 현재가 대비 손절까지 남은 % + 매수감시 목록",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_investor_flow", "description": "개별 종목 투자자별 수급: 외국인·기관·개인 매수/매도/순매수 수량. 장중이면 당일 누적, 장후면 최근 영업일 확정 데이터.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드 (예: 009540)"},
                     },
                     "required": ["ticker"]}},
    {"name": "get_price_rank",
     "description": "등락률 상위/하위 종목 순위. '오늘 상승률 상위 종목', '하락률 상위 코스닥' 등에 사용.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "sort":   {"type": "string", "description": "'rise'=상승률 상위(기본), 'fall'=하락률 상위"},
                         "market": {"type": "string", "description": "'all'=전체(기본), 'kospi', 'kosdaq'"},
                         "n":      {"type": "integer", "description": "조회 종목 수 (기본 20, 최대 30)"},
                     },
                     "required": []}},
    {"name": "get_investor_trend_history",
     "description": "개별 종목의 투자자별 수급 일별 히스토리. 외국인·기관·개인 순매수 추이 (최근 N일). 'HD조선 외인 수급 5일 흐름' 등에 사용.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드 (예: 009540)"},
                         "days":   {"type": "integer", "description": "조회 일수 (기본 5, 최대 10)"},
                     },
                     "required": ["ticker"]}},
    {"name": "get_program_trade",
     "description": "프로그램매매 투자자별 당일 동향. 외국인·기관·개인의 차익/비차익 프로그램매매 현황.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "market": {"type": "string", "description": "'kospi'(기본) 또는 'kosdaq'"},
                     },
                     "required": []}},
    {"name": "get_investor_estimate",
     "description": "장중 투자자 추정 수급 가집계. 외국인·기관 추정 순매수 수량 (확정치 아님). '지금 삼성전자 외인 추정 수급' 등에 사용.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드 (예: 005930)"},
                     },
                     "required": ["ticker"]}},
    {"name": "get_foreign_institution",
     "description": "외국인+기관 합산 순매수 상위 종목 (가집계). 외인과 기관이 동시에 매수하는 종목 파악에 사용.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "sort": {"type": "string", "description": "'buy'=순매수 상위(기본), 'sell'=순매도 상위"},
                         "n":    {"type": "integer", "description": "조회 종목 수 (기본 20)"},
                     },
                     "required": []}},
    {"name": "get_short_sale",
     "description": "국내주식 공매도 일별추이. 공매도 비율·수량 확인. 하락 원인 파악 시 사용.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드 (예: 005930)"},
                         "n":      {"type": "integer", "description": "조회 일수 (기본 10)"},
                     },
                     "required": ["ticker"]}},
    {"name": "get_news",
     "description": "KIS 종목 관련 뉴스 헤드라인 목록. 종목명 언급 뉴스 최신순 조회.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드 (예: 005930)"},
                         "n":      {"type": "integer", "description": "뉴스 개수 (기본 10)"},
                     },
                     "required": ["ticker"]}},
    {"name": "get_vi_status",
     "description": "변동성완화장치(VI) 발동 종목 현황. 오늘 VI 발동된 전 종목 목록.",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_volume_power",
     "description": "체결강도 상위 종목 순위. 매수/매도 체결 비율. 120% 이상=매수 우위. '지금 체결강도 높은 종목' 등에 사용.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "market": {"type": "string", "description": "'all'=전체(기본), 'kospi', 'kosdaq'"},
                         "n":      {"type": "integer", "description": "조회 종목 수 (기본 20)"},
                     },
                     "required": []}},
    {"name": "get_us_price_rank",
     "description": "미국 주식 등락률 상위/하위 종목 순위. '나스닥 오늘 상승률 상위' 등에 사용.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "sort":     {"type": "string", "description": "'rise'=상승률 상위(기본), 'fall'=하락률 상위"},
                         "exchange": {"type": "string", "description": "'NAS'=나스닥(기본), 'NYS'=뉴욕, 'AMS'=아멕스"},
                         "n":        {"type": "integer", "description": "조회 종목 수 (기본 20)"},
                     },
                     "required": []}},
    {"name": "get_consensus",  "description": "종목별 증권사 컨센서스 목표주가/투자의견 조회 (FnGuide 기반). 평균·최고·최저 목표주가, 매수/중립/매도 건수, 증권사별 최신 목표가 반환.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드 6자리 (예: 009540)"},
                     },
                     "required": ["ticker"]}},
    {"name": "set_alert",      "description": "손절가/목표가 등록, 매수감시, 투자판단 기록, 매매기록. log_type으로 모드 선택: 생략→stop/buy, decision→투자판단, compare→종목비교, trade→매매기록",
     "inputSchema": {"type": "object",
                     "properties": {
                         "log_type":          {"type": "string", "description": "모드: 생략=stop/buy, 'decision'=투자판단, 'compare'=종목비교, 'trade'=매매기록"},
                         "ticker":            {"type": "string", "description": "종목코드 또는 미국 티커"},
                         "name":              {"type": "string", "description": "종목명"},
                         "stop_price":        {"type": "number", "description": "손절가"},
                         "target_price":      {"type": "number", "description": "목표가 [trade:매수 시 목표가]"},
                         "buy_price":         {"type": "number", "description": "매수 희망가 (이 가격 이하 시 텔레그램 알림)"},
                         "memo":              {"type": "string", "description": "메모"},
                         "date":              {"type": "string", "description": "[decision/trade] YYYY-MM-DD (생략시 오늘)"},
                         "regime":            {"type": "string", "description": "[decision] 시장 국면 (예: 경계, 공격, 방어)"},
                         "grades":            {"type": "object", "description": "[decision] 종목별 확신등급. 값은 문자열(\"A\") 또는 객체({\"grade\":\"B\",\"change\":\"A→B\",\"reason\":\"사유\"})"},
                         "actions":           {"type": "array",  "description": "[decision] 액션 목록 (예: [\"HD조선 6주 매도\"])"},
                         "watchlist":         {"type": "array",  "description": "[decision] 관심 종목 목록 (예: [\"한화에어로 130만원대\"])"},
                         "notes":             {"type": "string", "description": "[decision] 메모 (예: 이란전쟁 리스크)"},
                         "held_ticker":       {"type": "string", "description": "[compare] 보유 종목코드"},
                         "candidate_ticker":  {"type": "string", "description": "[compare] 교체 후보 종목코드"},
                         "held_score":        {"type": "number", "description": "[compare] 보유 종목 점수"},
                         "candidate_score":   {"type": "number", "description": "[compare] 후보 종목 점수"},
                         "reasoning":         {"type": "string", "description": "[compare] 비교 근거"},
                         "side":              {"type": "string", "description": "[trade] 'buy' 또는 'sell'"},
                         "qty":               {"type": "integer","description": "[trade] 매매 수량"},
                         "price":             {"type": "number", "description": "[trade] 매매 단가"},
                         "grade":             {"type": "string", "description": "[trade] 매매 시점 확신등급 (A/B/C/D)"},
                         "reason":            {"type": "string", "description": "[trade] 매매 사유"},
                     },
                     "required": []}},
    {"name": "delete_alert", "description": "매도 후 stoploss.json에서 해당 종목의 손절/목표가 알림을 완전히 삭제. watchlist_log에 delete_alert 기록.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드 또는 미국 티커 (예: 034020, TSLA)"},
                         "market": {"type": "string", "description": "'KR'=한국(기본), 'US'=미국"},
                     },
                     "required": ["ticker"]}},
    {"name": "get_portfolio_history",
     "description": "포트폴리오 스냅샷 히스토리 + 드로다운 분석. 주간/월간 수익률, 월간 최대 드로다운, 투자규칙 경고(주간-4%/월간-7%/연속손절3회) 포함.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "days": {"type": "integer", "description": "최근 N일 스냅샷 반환 (기본 30, 최대 365)"},
                     },
                     "required": []}},
    {"name": "get_trade_stats",
     "description": "매매 기록 성과 분석. 승률·손익·평균보유기간·확신등급 정확도 등 반환. 월간 복기 시 사용.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "period": {"type": "string", "description": "'month'=이번달(기본), 'quarter'=이번분기, 'year'=올해, 'all'=전체"},
                     },
                     "required": []}},
    {"name": "get_batch_detail", "description": "여러 한국 종목을 한 번에 조회. 현재가·등락률·거래량·52주고저·PER·PBR·당일 외인/기관 순매수 반환. 최대 20종목.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "tickers": {"type": "string", "description": "콤마 구분 종목코드 (예: '009540,298040,010120')"},
                         "delay":   {"type": "number",  "description": "종목간 API 딜레이 초 (기본 0.3)"},
                     },
                     "required": ["tickers"]}},
]


async def _execute_tool(name: str, arguments: dict) -> dict | list:
    """툴 실행 → 결과 반환 (에러 시 {"error": ...})"""
    arguments = arguments or {}
    print(f"툴 호출: {name} {arguments}")
    try:
        token = await get_kis_token()
        if not token:
            raise RuntimeError("KIS 토큰 발급 실패")

        if name == "scan_market":
            rows = await kis_volume_rank_api(token)
            await asyncio.sleep(0.05)
            frgn_rows = await kis_foreigner_trend(token)
            # 외국인 순매수량 dict (ticker → qty)
            frgn_dict = {r.get("mksc_shrn_iscd", ""): int(r.get("frgn_ntby_qty", 0) or 0)
                         for r in frgn_rows}
            result = []
            for r in rows[:15]:
                ticker = r.get("mksc_shrn_iscd")
                frgn_qty = frgn_dict.get(ticker, 0)
                item = {
                    "ticker": ticker, "name": r.get("hts_kor_isnm"),
                    "vol": r.get("acml_vol"), "chg": r.get("prdy_ctrt"),
                    "frgn_ntby_qty": frgn_qty,
                    "frgn_buy": frgn_qty > 0,
                }
                if frgn_qty > 0:
                    item["tag"] = "외인매수"
                result.append(item)

        elif name == "get_portfolio":
            mode = arguments.get("mode", "").strip().lower()

            if mode == "set":
                # ── 포트폴리오 저장 모드 ──
                market   = arguments.get("market", "KR").strip().upper()
                holdings = arguments.get("holdings") or {}
                cash_krw = arguments.get("cash_krw")
                cash_usd = arguments.get("cash_usd")
                portfolio = load_json(PORTFOLIO_FILE, {})
                # 현금 잔고 업데이트
                if cash_krw is not None:
                    portfolio["cash_krw"] = float(cash_krw)
                if cash_usd is not None:
                    portfolio["cash_usd"] = float(cash_usd)
                if not holdings and cash_krw is None and cash_usd is None:
                    result = {"error": "holdings, cash_krw, cash_usd 중 하나는 필요합니다"}
                else:
                    if market == "US" and holdings:
                        us = portfolio.get("us_stocks", {})
                        us.update(holdings)
                        portfolio["us_stocks"] = us
                    elif holdings:
                        for ticker, info in holdings.items():
                            portfolio[ticker] = info
                    save_json(PORTFOLIO_FILE, portfolio)
                    asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
                    kr_count = sum(1 for k in portfolio if k not in ("us_stocks", "cash_krw", "cash_usd"))
                    us_count = len(portfolio.get("us_stocks", {}))
                    result = {"ok": True,
                              "message": f"포트폴리오 저장됨 (KR {kr_count}종목, US {us_count}종목)",
                              "cash_krw": portfolio.get("cash_krw"),
                              "cash_usd": portfolio.get("cash_usd")}

            else:
                # ── 조회 모드 (기존) ──
                portfolio = load_json(PORTFOLIO_FILE, {})
                kr_stocks = {k: v for k, v in portfolio.items() if k != "us_stocks"}
                us_stocks = portfolio.get("us_stocks", {})
                if not kr_stocks and not us_stocks:
                    result = {"message": "포트폴리오가 비어있습니다. /setportfolio 또는 /setusportfolio 로 등록하세요."}
                else:
                    kr_holdings, us_holdings = [], []
                    kr_eval = kr_cost = us_eval = us_cost = 0

                    for ticker, info in kr_stocks.items():
                        qty = info.get("qty", 0)
                        avg = info.get("avg_price", 0)
                        d = await kis_stock_price(ticker, token)
                        cur = int(d.get("stck_prpr", 0) or 0)
                        eval_amt = cur * qty
                        cost_amt = int(avg) * qty
                        pnl = eval_amt - cost_amt
                        pnl_pct = round((cur - avg) / avg * 100, 2) if avg else 0
                        kr_eval += eval_amt
                        kr_cost += cost_amt
                        kr_holdings.append({
                            "ticker": ticker, "name": info.get("name", ticker),
                            "qty": qty, "avg_price": avg, "cur_price": cur,
                            "eval_amt": eval_amt, "pnl": pnl, "pnl_pct": pnl_pct,
                            "chg_today": d.get("prdy_ctrt"),
                        })

                    for symbol, info in us_stocks.items():
                        qty = info.get("qty", 0)
                        avg = info.get("avg_price", 0)
                        d = await kis_us_stock_price(symbol, token)
                        cur = float(d.get("last", 0) or d.get("stck_prpr", 0) or 0)
                        eval_amt = round(cur * qty, 2)
                        cost_amt = round(avg * qty, 2)
                        pnl = round(eval_amt - cost_amt, 2)
                        pnl_pct = round((cur - avg) / avg * 100, 2) if avg else 0
                        us_eval += eval_amt
                        us_cost += cost_amt
                        us_holdings.append({
                            "ticker": symbol, "name": info.get("name", symbol),
                            "qty": qty, "avg_price": avg, "cur_price": cur,
                            "eval_amt": eval_amt, "pnl": pnl, "pnl_pct": pnl_pct,
                            "chg_today": d.get("rate"),
                        })

                    result = {
                        "kr": {
                            "holdings": kr_holdings,
                            "summary": {
                                "total_eval": kr_eval, "total_cost": kr_cost,
                                "total_pnl": kr_eval - kr_cost,
                                "total_pnl_pct": round((kr_eval - kr_cost) / kr_cost * 100, 2) if kr_cost else 0,
                            },
                        },
                        "us": {
                            "holdings": us_holdings,
                            "summary": {
                                "total_eval": round(us_eval, 2), "total_cost": round(us_cost, 2),
                                "total_pnl": round(us_eval - us_cost, 2),
                                "total_pnl_pct": round((us_eval - us_cost) / us_cost * 100, 2) if us_cost else 0,
                            },
                        },
                    }

        elif name == "get_stock_detail":
            ticker = arguments.get("ticker", "005930").strip().upper()
            period = arguments.get("period", "").strip().upper()  # e.g. "D60", "W20"

            if period:
                # ── 일봉/주봉 조회 모드 ──
                period_type = period[0] if period else "D"  # D/W/M
                try:
                    n = int(period[1:])
                except ValueError:
                    n = 60
                today_str = datetime.now(KST).strftime("%Y%m%d")
                buffer = {"D": 2, "W": 8, "M": 40}.get(period_type, 2)
                start_dt = (datetime.now(KST) - timedelta(days=n * buffer)).strftime("%Y%m%d")

                if _is_us_ticker(ticker):
                    excd = _guess_excd(ticker)
                    async with aiohttp.ClientSession() as s:
                        _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/dailyprice",
                            "HHDFS76240000", token,
                            {"AUTH": "", "EXCD": excd, "SYMB": ticker,
                             "GUBN": "0", "BYMD": today_str, "MODP": "0"})
                    candles = d.get("output2", [])
                    result = {
                        "ticker": ticker, "market": "US", "period": period,
                        "candles": [{"date": c.get("xymd"), "open": c.get("open"),
                                     "high": c.get("high"), "low": c.get("low"),
                                     "close": c.get("clos"), "vol": c.get("tvol")}
                                    for c in candles[:n]],
                    }
                else:
                    async with aiohttp.ClientSession() as s:
                        _, d = await _kis_get(s,
                            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                            "FHKST03010100", token,
                            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
                             "FID_INPUT_DATE_1": start_dt, "FID_INPUT_DATE_2": today_str,
                             "FID_PERIOD_DIV_CODE": period_type, "FID_ORG_ADJ_PRC": "0"})
                    candles = d.get("output2", [])
                    result = {
                        "ticker": ticker, "market": "KR", "period": period,
                        "candles": [{"date": c.get("stck_bsop_date"),
                                     "open": c.get("stck_oprc"), "high": c.get("stck_hgpr"),
                                     "low": c.get("stck_lwpr"), "close": c.get("stck_clpr"),
                                     "vol": c.get("acml_vol")}
                                    for c in candles[:n]],
                    }

            elif _is_us_ticker(ticker):
                # ── 미국 주식 ──
                excd = _guess_excd(ticker)
                price_d = await kis_us_stock_price(ticker, token, excd)
                detail_d = await kis_us_stock_detail(ticker, token, excd)
                cur = float(price_d.get("last", 0) or 0)
                base = float(price_d.get("base", 0) or 0)
                result = {
                    "ticker": ticker, "market": "US",
                    "price": cur,
                    "chg_pct": float(price_d.get("rate", 0) or 0),
                    "volume": int(price_d.get("tvol", 0) or 0),
                    "open": float(detail_d.get("open", 0) or 0),
                    "high": float(detail_d.get("high", 0) or 0),
                    "low": float(detail_d.get("low", 0) or 0),
                    "prev_close": base,
                    "w52h": float(detail_d.get("h52p", 0) or 0),
                    "w52l": float(detail_d.get("l52p", 0) or 0),
                    "per": float(detail_d.get("perx", 0) or 0) or None,
                    "pbr": float(detail_d.get("pbrx", 0) or 0) or None,
                    "eps": float(detail_d.get("epsx", 0) or 0) or None,
                    "market_cap": detail_d.get("tomv", ""),
                    "sector": detail_d.get("e_icod", ""),
                }
            else:
                # ── 한국 주식 ──
                price = await kis_stock_price(ticker, token)
                inv   = await kis_investor_trend(ticker, token)
                result = {
                    "ticker": ticker, "market": "KR",
                    "price": price.get("stck_prpr"), "chg": price.get("prdy_ctrt"),
                    "vol": price.get("acml_vol"),
                    "w52h": price.get("w52_hgpr"), "w52l": price.get("w52_lwpr"),
                    "per": price.get("per"), "pbr": price.get("pbr"), "eps": price.get("eps"),
                    "bps": price.get("bps"),
                    "investor": inv[:3] if isinstance(inv, list) else inv,
                }
                # 추정실적 (period 없을 때만)
                try:
                    result["earnings"] = await kis_estimate_perform(ticker, token)
                except Exception:
                    pass

        elif name == "get_foreign_rank":
            try:
                rows = await kis_foreigner_trend(token)
                if not rows:
                    result = {"error": "데이터 없음", "items": []}
                else:
                    result = [
                        {
                            "ticker": r.get("mksc_shrn_iscd", ""),
                            "name": r.get("hts_kor_isnm", ""),
                            "net_buy": r.get("frgn_ntby_qty", "0"),
                        }
                        for r in rows[:15]
                    ]
            except Exception as e:
                result = {"error": str(e), "items": []}

        elif name == "get_dart":
            disclosures = await search_dart_disclosures(days_back=3)
            wl = load_watchlist()
            important = filter_important_disclosures(disclosures, list(wl.values()))
            result = []
            for d in important[:10]:
                title = d.get("report_nm", "") or ""
                tag = _dart_tag(title)
                tagged_title = f"[{tag}] {title}" if tag != "일반" else title
                result.append({
                    "corp": d.get("corp_name", ""),
                    "title": tagged_title,
                    "date": d.get("rcept_dt", ""),
                    "importance": tag,
                })

        elif name == "get_macro":
            mode = arguments.get("mode", "").strip().lower()
            print(f"[get_macro] mode={repr(mode)}")
            if mode == "dashboard":
                # ── 전체 매크로 대시보드 ──
                try:
                    data = await collect_macro_data()
                    result = {
                        "data":    data,
                        "message": format_macro_msg(data),
                        "regime":  judge_regime(data),
                    }
                except Exception as _me:
                    _tb = traceback.format_exc()
                    print(f"[get_macro/dashboard] 에러: {_me}\n{_tb}")
                    result = {"error": str(_me), "mode": "dashboard", "traceback": _tb}
            elif mode == "sector_etf":
                # ── 섹터 ETF 시세 ── (kis_stock_price 사용: ETF 코드도 일반 주식 API로 조회 가능)
                SECTOR_ETFS = [
                    ("140710", "KODEX 조선"),
                    ("464520", "TIGER 방산"),
                    ("305720", "KODEX 2차전지"),
                    ("469150", "TIGER AI반도체"),
                    ("244580", "KODEX 바이오"),
                    ("261070", "KODEX 전력에너지"),
                    ("069500", "KODEX 200"),
                    ("252670", "KODEX 200선물인버스2X"),
                ]
                etf_results = []
                for etf_code, etf_name in SECTOR_ETFS:
                    try:
                        d = await kis_stock_price(etf_code, token)
                        etf_results.append({
                            "code": etf_code, "name": etf_name,
                            "price": d.get("stck_prpr"),
                            "chg_pct": d.get("prdy_ctrt"),
                            "volume": d.get("acml_vol"),
                        })
                        await asyncio.sleep(0.05)
                    except Exception:
                        pass
                result = {"etfs": etf_results}
            elif mode in ("convergence", "convergence2"):
                # ── 이평선 수렴 스크리너 ──
                try:
                    spread_threshold = float(arguments.get("spread", 5.0))
                    sort_by = arguments.get("sort", "spread").strip().lower()
                    if sort_by not in ("spread", "disp_20", "disp_60"):
                        sort_by = "spread"
                    # market 파라미터: convergence2는 'kosdaq'으로 고정
                    if mode == "convergence2":
                        market = "kosdaq"
                    else:
                        market = arguments.get("market", "all").strip().lower()
                        if market not in ("kospi", "kosdaq", "all"):
                            market = "all"
                    universe = get_stock_universe()
                    if not universe:
                        result = {"error": "stock_universe.json 로드 실패 — 파일 없음"}
                    else:
                        all_codes = list(universe.items())
                        half = len(all_codes) // 2 + len(all_codes) % 2  # 110 for 221
                        kospi_codes  = all_codes[:half]
                        kosdaq_codes = all_codes[half:]
                        if market == "kospi":
                            codes = kospi_codes
                        elif market == "kosdaq":
                            codes = kosdaq_codes
                        else:  # all
                            codes = all_codes
                        print(f"[{mode}] {len(codes)}종목 병렬 스캔 시작 (market={market}, spread≤{spread_threshold}%, sort={sort_by})")
                        sem_c = asyncio.Semaphore(10)
                        items = await asyncio.gather(
                            *[_scan_conv_one(t, n, token, sem_c, spread_threshold) for t, n in codes]
                        )
                        conv_results = [x for x in items if x]
                        if sort_by == "disp_20":
                            conv_results.sort(key=lambda x: abs(x["disp_20"]))
                        elif sort_by == "disp_60":
                            conv_results.sort(key=lambda x: abs(x["disp_60"]))
                        else:
                            conv_results.sort(key=lambda x: x["spread"])
                        print(f"[{mode}] 완료: {len(conv_results)}개 수렴 종목")
                        result = {
                            "mode": mode,
                            "market": market,
                            "spread_threshold": spread_threshold,
                            "sort": sort_by,
                            "count": len(conv_results),
                            "results": conv_results,
                        }
                except Exception as _ce:
                    _tb = traceback.format_exc()
                    print(f"[get_macro/{mode}] 에러: {_ce}\n{_tb}")
                    result = {"error": str(_ce), "mode": mode, "traceback": _tb}

            elif mode == "op_growth":
                # ── 영업이익 증가율 스크리너 (병렬 스캔) ──
                try:
                    min_growth = float(arguments.get("min_growth", 50))
                    sort_by    = arguments.get("sort", "yoy")
                    universe = get_stock_universe()
                    if not universe:
                        result = {"error": "stock_universe.json 로드 실패 — 파일 없음"}
                    else:
                        codes = list(universe.items())
                        print(f"[op_growth] {len(codes)}종목 병렬 스캔 시작 (최소 증가율: {min_growth}%)")
                        sem_o = asyncio.Semaphore(5)
                        items = await asyncio.gather(
                            *[_scan_op_one(t, n, token, sem_o, min_growth) for t, n in codes]
                        )
                        filtered = [x for x in items if x]
                        if sort_by == "qoq":
                            op_results = sorted(filtered, key=lambda x: x.get("qoq_growth") if x.get("qoq_growth") is not None else -9999, reverse=True)
                        elif sort_by == "trend":
                            op_results = sorted(filtered, key=lambda x: _TREND_PRIORITY.get(x.get("op_trend", ""), 9))
                        else:  # yoy (default)
                            op_results = sorted(filtered, key=lambda x: x["growth_pct"], reverse=True)
                        print(f"[op_growth] 완료: {len(op_results)}개 기준충족 종목")
                        result = {
                            "mode": "op_growth",
                            "min_growth": min_growth,
                            "sort": sort_by,
                            "count": len(op_results),
                            "results": op_results,
                        }
                except Exception as _oe:
                    _tb = traceback.format_exc()
                    print(f"[get_macro/op_growth] 에러: {_oe}\n{_tb}")
                    result = {"error": str(_oe), "mode": "op_growth", "traceback": _tb}

            elif mode == "op_turnaround":
                # ── 영업이익 적자→흑자 전환 스크리너 ──
                try:
                    sort_by = arguments.get("sort", "yoy")
                    universe = get_stock_universe()
                    if not universe:
                        result = {"error": "stock_universe.json 로드 실패 — 파일 없음"}
                    else:
                        codes = list(universe.items())
                        print(f"[op_turnaround] {len(codes)}종목 병렬 스캔 시작")
                        sem_t = asyncio.Semaphore(5)
                        items = await asyncio.gather(
                            *[_scan_turnaround_one(t, n, token, sem_t) for t, n in codes]
                        )
                        filtered = [x for x in items if x]
                        if sort_by == "qoq":
                            ta_results = sorted(filtered, key=lambda x: x.get("qoq_growth") if x.get("qoq_growth") is not None else -9999, reverse=True)
                        elif sort_by == "trend":
                            ta_results = sorted(filtered, key=lambda x: _TREND_PRIORITY.get(x.get("op_trend", ""), 9))
                        else:  # yoy / default: 흑자전환이라 모두 op_recent 기준
                            ta_results = sorted(filtered, key=lambda x: x["op_recent"], reverse=True)
                        print(f"[op_turnaround] 완료: {len(ta_results)}개 전환 종목")
                        result = {
                            "mode": "op_turnaround",
                            "sort": sort_by,
                            "count": len(ta_results),
                            "results": ta_results,
                        }
                except Exception as _te:
                    _tb = traceback.format_exc()
                    print(f"[get_macro/op_turnaround] 에러: {_te}\n{_tb}")
                    result = {"error": str(_te), "mode": "op_turnaround", "traceback": _tb}

            elif mode in ("dart_op_growth", "dart_turnaround"):
                # ── DART 기반 연간 영업이익 스크리너 ──
                try:
                    universe = get_stock_universe()
                    if not universe:
                        result = {"error": "stock_universe.json 로드 실패"}
                    else:
                        from kis_api import DART_API_KEY as _DART_KEY
                        print(f"[{mode}] DART_API_KEY 설정 여부: {bool(_DART_KEY)}")
                        corp_map = await get_dart_corp_map(universe)
                        print(f"[{mode}] corp_map size: {len(corp_map)}")
                        if not corp_map:
                            result = {"error": "dart_corp_map 로드 실패",
                                      "hint": "DART_API_KEY 미설정 또는 corpCode.xml 다운로드 실패. Railway 로그 확인."}
                        else:
                            now = datetime.now()
                            # 사업보고서 제출 마감: 3월 말. 4월 이후부터 전년도 데이터 안정적.
                            if now.month <= 3:
                                recent_year = now.year - 2  # 3월 이전: 2년 전 사업보고서 비교
                            else:
                                recent_year = now.year - 1  # 4월~: 전년도 사업보고서 비교
                            print(f"[{mode}] recent_year={recent_year} (month={now.month})")
                            codes = [(t, n, corp_map[t]) for t, n in universe.items() if t in corp_map]
                            # semaphore(15): 5→15로 확대, sleep 제거 → 첫 실행 속도 3배 향상
                            sem_d = asyncio.Semaphore(15)
                            sort_by = arguments.get("sort", "yoy")
                            if mode == "dart_op_growth":
                                min_growth = float(arguments.get("min_growth", 50))
                                # 당일 캐시 확인 (min_growth 포함해서 캐시 키 구성)
                                _ckey = f"dart_op_growth_{int(min_growth)}_{recent_year}"
                                cached = _load_dart_screener_cache(mode, _ckey)
                                if cached:
                                    _raw_results = cached.get("results", [])
                                else:
                                    print(f"[dart_op_growth] {len(codes)}종목 스캔 (최소 성장률: {min_growth}%)")
                                    items = await asyncio.gather(
                                        *[_scan_dart_op_one(t, n, c, sem_d, min_growth, recent_year, token) for t, n, c in codes]
                                    )
                                    _raw_results = [x for x in items if x]
                                    _cache_result = {"mode": "dart_op_growth", "count": len(_raw_results), "results": sorted(_raw_results, key=lambda x: x["growth_pct"], reverse=True)}
                                    _save_dart_screener_cache(_ckey, _cache_result)
                                # sort 적용 (캐시 히트 후에도 적용)
                                if sort_by == "qoq":
                                    _sorted = sorted(_raw_results, key=lambda x: x.get("qoq_growth") if x.get("qoq_growth") is not None else -9999, reverse=True)
                                elif sort_by == "trend":
                                    _sorted = sorted(_raw_results, key=lambda x: _TREND_PRIORITY.get(x.get("op_trend", ""), 9))
                                else:
                                    _sorted = sorted(_raw_results, key=lambda x: x["growth_pct"], reverse=True)
                                result = {"mode": "dart_op_growth", "sort": sort_by, "count": len(_sorted), "results": _sorted}
                            else:  # dart_turnaround
                                _ckey = f"dart_turnaround_{recent_year}"
                                cached = _load_dart_screener_cache(mode, _ckey)
                                if cached:
                                    _raw_results = cached.get("results", [])
                                else:
                                    print(f"[dart_turnaround] {len(codes)}종목 스캔")
                                    items = await asyncio.gather(
                                        *[_scan_dart_turnaround_one(t, n, c, sem_d, recent_year, token) for t, n, c in codes]
                                    )
                                    _raw_results = [x for x in items if x]
                                    _cache_result = {"mode": "dart_turnaround", "count": len(_raw_results), "results": sorted(_raw_results, key=lambda x: x["op_recent"], reverse=True)}
                                    _save_dart_screener_cache(_ckey, _cache_result)
                                # sort 적용
                                if sort_by == "qoq":
                                    _sorted = sorted(_raw_results, key=lambda x: x.get("qoq_growth") if x.get("qoq_growth") is not None else -9999, reverse=True)
                                elif sort_by == "trend":
                                    _sorted = sorted(_raw_results, key=lambda x: _TREND_PRIORITY.get(x.get("op_trend", ""), 9))
                                else:
                                    _sorted = sorted(_raw_results, key=lambda x: x["op_recent"], reverse=True)
                                result = {"mode": "dart_turnaround", "sort": sort_by, "count": len(_sorted), "results": _sorted}
                except Exception as _de:
                    _tb = traceback.format_exc()
                    print(f"[get_macro/{mode}] 에러: {_de}\n{_tb}")
                    result = {"error": str(_de), "mode": mode, "traceback": _tb}

            else:
                # ── 기본 모드: KOSPI/KOSDAQ/환율 ──
                kospi  = await get_kis_index(token, "0001")
                kosdaq = await get_kis_index(token, "1001")
                usd    = await get_yahoo_quote("USDKRW=X")
                result = {
                    "kospi":  {"index": kospi.get("bstp_nmix_prpr"),  "chg": kospi.get("bstp_nmix_prdy_ctrt")},
                    "kosdaq": {"index": kosdaq.get("bstp_nmix_prpr"), "chg": kosdaq.get("bstp_nmix_prdy_ctrt")},
                    "usd_krw": {"price": usd.get("price") if usd else None,
                                "chg_pct": usd.get("change_pct") if usd else None},
                }

        elif name == "get_sector_flow":
            today = datetime.now().strftime("%Y%m%d")
            sectors = []
            for code, label in WI26_SECTORS:
                frgn, orgn = await _fetch_sector_flow(token, code)
                sectors.append({
                    "sector": label, "code": code,
                    "frgn": frgn, "orgn": orgn,
                    "total": frgn + orgn,
                })

            has_data = any(s["total"] != 0 for s in sectors)
            note = None

            if not has_data:
                # Fallback: 외국인 순매수 상위 기반 업종 근사치 (수량 기준)
                frgn_rows = await kis_foreigner_trend(token)
                sector_frgn = {label: 0 for _, label in WI26_SECTORS}
                for r in frgn_rows:
                    sect = _TICKER_SECTOR.get(r.get("mksc_shrn_iscd", ""))
                    if sect:
                        sector_frgn[sect] += int(r.get("frgn_ntby_qty", 0) or 0)
                sectors = [
                    {"sector": label, "code": code,
                     "frgn": sector_frgn.get(label, 0), "orgn": 0,
                     "total": sector_frgn.get(label, 0)}
                    for code, label in WI26_SECTORS
                ]
                note = "업종별 투자자 API 미지원 — 외국인 순매수 상위 기반 근사치(수량)"

            sorted_s = sorted(sectors, key=lambda x: x["total"], reverse=True)
            result = {
                "date": today,
                "top_inflow":  [{"sector": s["sector"], "frgn": s["frgn"], "orgn": s["orgn"]}
                                 for s in sorted_s[:3]],
                "top_outflow": [{"sector": s["sector"], "frgn": s["frgn"], "orgn": s["orgn"]}
                                 for s in sorted_s[-3:][::-1]],
                "all": [{"sector": s["sector"], "frgn": s["frgn"], "orgn": s["orgn"]}
                        for s in sorted_s],
            }
            if note:
                result["note"] = note

            # ── 섹터 ETF 시세 ──
            SECTOR_ETFS = [
                ("140710", "KODEX 조선"),
                ("464520", "TIGER 방산"),
                ("305720", "KODEX 2차전지"),
                ("469150", "TIGER AI반도체"),
                ("244580", "KODEX 바이오"),
                ("261070", "KODEX 전력에너지"),
            ]
            etf_prices = []
            for etf_code, etf_name in SECTOR_ETFS:
                try:
                    async with aiohttp.ClientSession() as s:
                        _, ed = await _kis_get(s, "/uapi/etfetn/v1/quotations/inquire-price",
                            "FHPST02400000", token,
                            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": etf_code})
                    out = ed.get("output", {})
                    etf_prices.append({
                        "code": etf_code, "name": etf_name,
                        "price": out.get("stck_prpr"), "chg": out.get("prdy_ctrt"),
                    })
                    await asyncio.sleep(0.05)
                except Exception:
                    pass
            result["etf_prices"] = etf_prices

        elif name == "get_alerts":
            stops = load_stoploss()
            kr_stops = {k: v for k, v in stops.items() if k != "us_stocks"}
            us_stops = stops.get("us_stocks", {})
            alerts = []
            for ticker, info in kr_stops.items():
                stop   = info.get("stop_price", 0)
                entry  = info.get("entry_price", 0)
                target = info.get("target_price", 0)
                cur = 0
                try:
                    d = await kis_stock_price(ticker, token)
                    cur = int(d.get("stck_prpr", 0) or 0)
                except Exception:
                    pass
                gap_pct = round((stop - cur) / cur * 100, 2) if cur else None
                item = {
                    "ticker": ticker, "name": info.get("name", ticker),
                    "market": "KR", "stop": stop, "entry": entry,
                    "cur": cur, "gap_pct": gap_pct,
                }
                if target:
                    item["target"] = target
                    item["target_pct"] = round((target - cur) / cur * 100, 2) if cur else None
                alerts.append(item)
            for sym, info in us_stops.items():
                stop   = info.get("stop_price", 0)
                target = info.get("target_price", 0)
                cur = 0.0
                try:
                    d = await get_yahoo_quote(sym)
                    cur = float(d.get("price", 0) or 0) if d else 0.0
                except Exception:
                    pass
                gap_pct = round((stop - cur) / cur * 100, 2) if cur else None
                item = {
                    "ticker": sym, "name": info.get("name", sym),
                    "market": "US", "stop": stop,
                    "cur": cur, "gap_pct": gap_pct,
                }
                if target:
                    item["target"] = target
                    item["target_pct"] = round((target - cur) / cur * 100, 2) if cur else None
                alerts.append(item)

            # ── 매수감시 목록 통합 ──
            wa = load_watchalert()
            watch_alerts = []
            for wa_ticker, wa_info in wa.items():
                buy_price = wa_info.get("buy_price", 0)
                cur = 0.0
                try:
                    if _is_us_ticker(wa_ticker):
                        d = await kis_us_stock_price(wa_ticker, token)
                        cur = float(d.get("last", 0) or 0)
                    else:
                        d = await kis_stock_price(wa_ticker, token)
                        cur = int(d.get("stck_prpr", 0) or 0)
                except Exception:
                    pass
                gap_pct = round((cur - buy_price) / buy_price * 100, 2) if buy_price else None
                watch_alerts.append({
                    "ticker": wa_ticker,
                    "name": wa_info.get("name", wa_ticker),
                    "buy_price": buy_price,
                    "cur_price": cur,
                    "gap_pct": gap_pct,
                    "triggered": cur > 0 and cur <= buy_price,
                    "memo": wa_info.get("memo", ""),
                    "created": wa_info.get("created", ""),
                })
            # ── 투자판단/비교 최근 기록 ──
            dec_log = load_decision_log()
            recent_decisions = sorted(dec_log.values(), key=lambda x: x.get("date", ""), reverse=True)[:3]
            cmp_log = load_compare_log()
            if not isinstance(cmp_log, list):
                cmp_log = []
            recent_compares = cmp_log[-3:][::-1]
            result = {
                "alerts": alerts,
                "watch_alerts": watch_alerts,
                "recent_decisions": recent_decisions,
                "recent_compares": recent_compares,
                "recent_changelog": load_watchlist_log()[-20:],
            }

        elif name == "set_alert":
            log_type     = arguments.get("log_type", "").strip().lower()
            ticker       = arguments.get("ticker", "").strip().upper()
            aname        = arguments.get("name", ticker).strip()
            stop_price   = float(arguments.get("stop_price", 0) or 0)
            target_price = float(arguments.get("target_price", 0) or 0)
            buy_price    = float(arguments.get("buy_price", 0) or 0)
            memo         = arguments.get("memo", "").strip() if arguments.get("memo") else ""

            if log_type == "decision":
                # ── 투자판단 기록 모드 ──
                date   = (arguments.get("date") or datetime.now(KST).strftime("%Y-%m-%d")).strip()
                regime = arguments.get("regime", "").strip()
                grades_raw = arguments.get("grades") or {}
                grades = {}
                for gk, gv in grades_raw.items():
                    if isinstance(gv, str):
                        grades[gk] = gv
                    elif isinstance(gv, dict):
                        obj = {"grade": gv.get("grade", "")}
                        if gv.get("change"):
                            obj["change"] = gv["change"]
                        if gv.get("reason"):
                            obj["reason"] = gv["reason"]
                        grades[gk] = obj
                    else:
                        grades[gk] = gv
                actions  = arguments.get("actions") or []
                watchlist_dec = arguments.get("watchlist") or []
                notes  = arguments.get("notes", "").strip()
                log = load_decision_log()
                entry = {
                    "date": date, "regime": regime,
                    "grades": grades, "actions": actions,
                    "watchlist": watchlist_dec, "notes": notes,
                    "saved_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                }
                log[date] = entry
                save_json(DECISION_LOG_FILE, log)
                result = {"ok": True, "message": f"{date} 투자판단 저장됨", "date": date}

            elif log_type == "trade":
                # ── 매매 기록 모드 ──
                side  = arguments.get("side", "").strip().lower()
                qty   = int(arguments.get("qty", 0) or 0)
                price = float(arguments.get("price", 0) or 0)
                grade = arguments.get("grade", "").strip().upper()
                reason = arguments.get("reason", "").strip()
                date  = (arguments.get("date") or datetime.now(KST).strftime("%Y-%m-%d")).strip()
                tgt_t = float(arguments.get("target_price", 0) or 0)
                stp_t = float(arguments.get("stop_price", 0) or 0)
                if not ticker or not side or qty <= 0 or price <= 0:
                    result = {"error": "ticker, side, qty, price는 필수입니다"}
                elif side not in ("buy", "sell"):
                    result = {"error": "side는 'buy' 또는 'sell' 이어야 합니다"}
                else:
                    trades = load_trade_log()
                    trade_id = f"T{len(trades) + 1:03d}"
                    market = "US" if _is_us_ticker(ticker) else "KR"
                    entry = {
                        "id": trade_id, "ticker": ticker, "name": aname,
                        "market": market, "side": side, "qty": qty,
                        "price": price, "date": date,
                        "grade_at_trade": grade, "reason": reason,
                    }
                    if side == "buy":
                        if tgt_t: entry["target_price"] = tgt_t
                        if stp_t: entry["stop_price"]   = stp_t
                        entry["linked_buy_id"] = None
                    else:  # sell
                        linked_buy = next(
                            (t for t in reversed(trades) if t["ticker"] == ticker and t["side"] == "buy"),
                            None,
                        )
                        entry["linked_buy_id"] = linked_buy["id"] if linked_buy else None
                        if linked_buy:
                            buy_p = float(linked_buy["price"])
                            calc_qty = min(qty, int(linked_buy.get("qty", qty)))
                            pnl = round((price - buy_p) * calc_qty, 2)
                            pnl_pct = round((price - buy_p) / buy_p * 100, 2) if buy_p else 0
                            entry["pnl"]     = pnl
                            entry["pnl_pct"] = pnl_pct
                            entry["result"]  = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")
                            try:
                                from datetime import datetime as _ddt
                                bd = linked_buy.get("date", "")
                                if bd:
                                    entry["holding_days"] = (_ddt.strptime(date, "%Y-%m-%d") - _ddt.strptime(bd, "%Y-%m-%d")).days
                            except Exception:
                                pass
                    trades.append(entry)
                    save_trade_log(trades)
                    pnl_str = f" | 손익 {entry.get('pnl', 0):+,.0f}" if "pnl" in entry else ""
                    fmt_p = f"${price:,.2f}" if market == "US" else f"{price:,.0f}원"
                    result = {"ok": True,
                              "message": f"{aname}({ticker}) {side} {qty}주 @{fmt_p} 기록됨{pnl_str}",
                              "trade_id": trade_id}

            elif log_type == "compare":
                # ── 종목비교 스냅샷 모드 ──
                held_ticker      = arguments.get("held_ticker", "").strip().upper()
                candidate_ticker = arguments.get("candidate_ticker", "").strip().upper()
                held_score       = float(arguments.get("held_score", 0) or 0)
                candidate_score  = float(arguments.get("candidate_score", 0) or 0)
                reasoning        = arguments.get("reasoning", "").strip()
                compare_memo     = arguments.get("memo", "").strip() if arguments.get("memo") else ""
                log = load_compare_log()
                if not isinstance(log, list):
                    log = []
                entry = {
                    "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                    "held": held_ticker, "candidate": candidate_ticker,
                    "held_score": held_score, "candidate_score": candidate_score,
                    "reasoning": reasoning, "memo": compare_memo,
                }
                log.append(entry)
                log = log[-50:]   # 최대 50건 보관
                save_json(COMPARE_LOG_FILE, log)
                verdict = "교체 권장" if candidate_score > held_score else "보유 유지"
                result = {"ok": True, "message": f"{held_ticker} vs {candidate_ticker} 비교 저장됨 ({verdict})", "verdict": verdict}

            elif not ticker or not aname:
                result = {"error": "ticker와 name은 필수입니다"}
            elif buy_price > 0:
                # ── 매수감시 모드 ──
                wa = load_watchalert()
                old_price = wa.get(ticker, {}).get("buy_price", None)
                log_action = "update" if old_price else "add"
                wa[ticker] = {
                    "name": aname,
                    "buy_price": buy_price,
                    "memo": memo,
                    "created": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                }
                save_json(WATCHALERT_FILE, wa)
                asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
                append_watchlist_log({
                    "date": datetime.now(KST).strftime("%Y-%m-%d"),
                    "action": log_action,
                    "ticker": ticker, "name": aname,
                    "buy_price": buy_price, "old_price": old_price, "reason": memo,
                })
                if _is_us_ticker(ticker):
                    msg = f"{aname}({ticker}) 매수감시 ${buy_price:,.2f} 등록됨"
                else:
                    msg = f"{aname}({ticker}) 매수감시 {buy_price:,.0f}원 등록됨"
                if memo:
                    msg += f" | 메모: {memo}"
                result = {"ok": True, "message": msg, "total_watch": len(wa)}
            elif stop_price > 0:
                # ── 손절가 등록 모드 ──
                stops = load_stoploss()
                if _is_us_ticker(ticker):
                    us = stops.get("us_stocks", {})
                    us[ticker] = {"name": aname, "stop_price": stop_price, "target_price": target_price}
                    stops["us_stocks"] = us
                    save_json(STOPLOSS_FILE, stops)
                    result = {
                        "ok": True,
                        "message": f"{aname}({ticker}) 손절가 ${stop_price:,.2f} 저장됨"
                                   + (f", 목표가 ${target_price:,.2f}" if target_price else ""),
                    }
                else:
                    stops[ticker] = {
                        "name":         aname,
                        "stop_price":   stop_price,
                        "entry_price":  stops.get(ticker, {}).get("entry_price", 0),
                        "target_price": target_price,
                    }
                    save_json(STOPLOSS_FILE, stops)
                    asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
                    result = {
                        "ok": True,
                        "message": f"{aname}({ticker}) 손절가 {stop_price:,.0f}원 저장됨"
                                   + (f", 목표가 {target_price:,.0f}원" if target_price else ""),
                    }
            else:
                result = {"error": "stop_price 또는 buy_price 중 하나는 필수입니다"}

        elif name == "add_watch":
            ticker = arguments.get("ticker", "").strip()
            wname  = arguments.get("name", "").strip()
            if not ticker or not wname:
                result = {"error": "ticker와 name은 필수입니다"}
            else:
                wl = load_watchlist()
                wl[ticker] = wname
                save_json(WATCHLIST_FILE, wl)
                asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
                append_watchlist_log({
                    "date": datetime.now(KST).strftime("%Y-%m-%d"),
                    "action": "add",
                    "ticker": ticker, "name": wname,
                    "buy_price": None, "old_price": None, "reason": "",
                })
                result = {"ok": True, "message": f"{wname}({ticker}) 워치리스트 추가됨", "total": len(wl)}

        elif name == "remove_watch":
            ticker = arguments.get("ticker", "").strip().upper()
            alert_type = arguments.get("alert_type", "watchlist").strip().lower()
            if not ticker:
                result = {"error": "ticker는 필수입니다"}
            elif alert_type == "buy_alert":
                # ── 매수감시 제거 ──
                wa = load_watchalert()
                if ticker in wa:
                    removed = wa.pop(ticker)
                    save_json(WATCHALERT_FILE, wa)
                    asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
                    result = {"ok": True, "message": f"{removed['name']}({ticker}) 매수감시 제거됨", "total_watch": len(wa)}
                else:
                    result = {"error": f"{ticker} 매수감시 목록에 없음"}
            else:
                # ── 워치리스트 제거 ──
                wl = load_watchlist()
                if ticker in wl:
                    removed = wl.pop(ticker)
                    save_json(WATCHLIST_FILE, wl)
                    asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
                    append_watchlist_log({
                        "date": datetime.now(KST).strftime("%Y-%m-%d"),
                        "action": "remove",
                        "ticker": ticker, "name": removed,
                        "buy_price": None, "old_price": None, "reason": "",
                    })
                    result = {"ok": True, "message": f"{removed}({ticker}) 워치리스트 제거됨", "total": len(wl)}
                else:
                    result = {"error": f"{ticker} 워치리스트에 없음"}

        elif name == "get_investor_flow":
            ticker = arguments.get("ticker", "").strip()
            if not ticker:
                result = {"error": "ticker는 필수입니다"}
            else:
                inv = await kis_investor_trend(ticker, token)
                if not inv:
                    result = {"error": f"{ticker} 수급 데이터 없음"}
                else:
                    row = inv[0]  # 가장 최근 영업일 (장중이면 당일 누적)
                    # 장중 여부: 평일 09:00~15:30 KST
                    now_kst = datetime.now(KST)
                    wd = now_kst.weekday()
                    tot_min = now_kst.hour * 60 + now_kst.minute
                    is_live = (wd < 5 and 9 * 60 <= tot_min <= 15 * 60 + 30)
                    result = {
                        "ticker": ticker,
                        "date": row.get("stck_bsop_date", ""),
                        "is_live": is_live,
                        "foreign":     {
                            "buy":  int(row.get("frgn_shnu_vol", 0) or 0),
                            "sell": int(row.get("frgn_seln_vol", 0) or 0),
                            "net":  int(row.get("frgn_ntby_qty", 0) or 0),
                        },
                        "institution": {
                            "buy":  int(row.get("orgn_shnu_vol", 0) or 0),
                            "sell": int(row.get("orgn_seln_vol", 0) or 0),
                            "net":  int(row.get("orgn_ntby_qty", 0) or 0),
                        },
                        "individual":  {
                            "buy":  int(row.get("prsn_shnu_vol", 0) or 0),
                            "sell": int(row.get("prsn_seln_vol", 0) or 0),
                            "net":  int(row.get("prsn_ntby_qty", 0) or 0),
                        },
                    }

        elif name == "get_price_rank":
            sort   = arguments.get("sort", "rise").strip().lower()
            market = arguments.get("market", "all").strip().lower()
            n      = int(arguments.get("n", 20) or 20)
            n      = max(1, min(n, 30))
            market_code = {"all": "0000", "kospi": "0001", "kosdaq": "1001"}.get(market, "0000")
            items = await kis_fluctuation_rank(token, market=market_code, sort=sort, n=n)
            result = {
                "sort":   sort,
                "market": market,
                "count":  len(items),
                "items":  items,
            }

        elif name == "get_investor_trend_history":
            ticker = arguments.get("ticker", "").strip()
            if not ticker:
                result = {"error": "ticker는 필수입니다"}
            else:
                days  = int(arguments.get("days", 5) or 5)
                days  = max(1, min(days, 10))
                rows  = await kis_investor_trend_history(ticker, token, n_days=days)
                result = {
                    "ticker": ticker,
                    "days":   days,
                    "history": rows,
                }

        elif name == "get_program_trade":
            market = arguments.get("market", "kospi").strip().lower()
            rows   = await kis_program_trade_today(token, market=market)
            result = {
                "market": market,
                "count":  len(rows),
                "items":  rows,
            }

        elif name == "get_investor_estimate":
            ticker = arguments.get("ticker", "").strip()
            if not ticker:
                result = {"error": "ticker는 필수입니다"}
            else:
                result = await kis_investor_trend_estimate(ticker, token)

        elif name == "get_foreign_institution":
            sort = arguments.get("sort", "buy").strip().lower()
            n    = int(arguments.get("n", 20) or 20)
            n    = max(1, min(n, 50))
            items = await kis_foreign_institution_total(token, sort=sort, n=n)
            result = {
                "sort":  sort,
                "count": len(items),
                "items": items,
            }

        elif name == "get_short_sale":
            ticker = arguments.get("ticker", "").strip()
            if not ticker:
                result = {"error": "ticker는 필수입니다"}
            else:
                n     = int(arguments.get("n", 10) or 10)
                n     = max(1, min(n, 30))
                rows  = await kis_daily_short_sale(ticker, token, n=n)
                result = {
                    "ticker": ticker,
                    "count":  len(rows),
                    "items":  rows,
                }

        elif name == "get_news":
            ticker = arguments.get("ticker", "").strip()
            if not ticker:
                result = {"error": "ticker는 필수입니다"}
            else:
                n    = int(arguments.get("n", 10) or 10)
                n    = max(1, min(n, 30))
                rows = await kis_news_title(ticker, token, n=n)
                result = {
                    "ticker": ticker,
                    "count":  len(rows),
                    "items":  rows,
                }

        elif name == "get_vi_status":
            rows   = await kis_vi_status(token)
            result = {
                "count": len(rows),
                "items": rows,
            }

        elif name == "get_volume_power":
            market = arguments.get("market", "all").strip().lower()
            n      = int(arguments.get("n", 20) or 20)
            n      = max(1, min(n, 50))
            items  = await kis_volume_power_rank(token, market=market, n=n)
            result = {
                "market": market,
                "count":  len(items),
                "items":  items,
            }

        elif name == "get_us_price_rank":
            sort     = arguments.get("sort", "rise").strip().lower()
            exchange = arguments.get("exchange", "NAS").strip().upper()
            n        = int(arguments.get("n", 20) or 20)
            n        = max(1, min(n, 50))
            items    = await kis_us_updown_rate(token, sort=sort, exchange=exchange, n=n)
            result   = {
                "sort":     sort,
                "exchange": exchange,
                "count":    len(items),
                "items":    items,
            }

        elif name == "get_consensus":
            ticker = arguments.get("ticker", "").strip().upper()
            if not ticker:
                result = {"error": "ticker는 필수입니다"}
            elif ticker.isdigit():
                # 한국 종목 (6자리 숫자) → FnGuide
                result = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_fnguide_consensus, ticker
                )
            else:
                # 미국 종목 (영문 티커) → yfinance
                r = await asyncio.get_event_loop().run_in_executor(
                    None, get_us_consensus, ticker
                )
                result = r if r else {"error": f"{ticker} 컨센서스 데이터 없음"}

        elif name == "delete_alert":
            ticker = arguments.get("ticker", "").strip().upper()
            if not ticker:
                result = {"error": "ticker는 필수입니다"}
            else:
                stops = load_stoploss()
                if _is_us_ticker(ticker):
                    us = stops.get("us_stocks", {})
                    if ticker not in us:
                        result = {"ok": False, "message": "해당 종목 알림이 없습니다"}
                    else:
                        entry = us.pop(ticker)
                        stops["us_stocks"] = us
                        save_json(STOPLOSS_FILE, stops)
                        append_watchlist_log({
                            "date": datetime.now(KST).strftime("%Y-%m-%d"),
                            "action": "delete_alert",
                            "ticker": ticker,
                            "name": entry.get("name", ticker),
                            "stop_price": entry.get("stop_price"),
                            "target_price": entry.get("target_price"),
                        })
                        result = {"ok": True, "message": f"{entry.get('name', ticker)}({ticker}) 알림 삭제됨"}
                else:
                    if ticker not in stops:
                        result = {"ok": False, "message": "해당 종목 알림이 없습니다"}
                    else:
                        entry = stops.pop(ticker)
                        save_json(STOPLOSS_FILE, stops)
                        asyncio.create_task(ws_manager.update_tickers(get_ws_tickers()))
                        append_watchlist_log({
                            "date": datetime.now(KST).strftime("%Y-%m-%d"),
                            "action": "delete_alert",
                            "ticker": ticker,
                            "name": entry.get("name", ticker),
                            "stop_price": entry.get("stop_price"),
                            "target_price": entry.get("target_price"),
                        })
                        result = {"ok": True, "message": f"{entry.get('name', ticker)}({ticker}) 알림 삭제됨"}

        elif name == "get_portfolio_history":
            days = min(int(arguments.get("days", 30) or 30), 365)
            history = load_json(PORTFOLIO_HISTORY_FILE, {"snapshots": []})
            snaps = sorted(history.get("snapshots", []), key=lambda x: x.get("date", ""))
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            recent = [s for s in snaps if s.get("date", "") >= cutoff]
            dd = check_drawdown()
            result = {
                "days": days,
                "snapshot_count": len(recent),
                "snapshots": recent,
                "drawdown": dd,
            }

        elif name == "get_trade_stats":
            period = arguments.get("period", "month").strip().lower()
            result = get_trade_stats(period)

        elif name == "get_batch_detail":
            raw = arguments.get("tickers", "")
            delay = float(arguments.get("delay", 0.3) or 0.3)
            tickers = [t.strip().upper() for t in raw.split(",") if t.strip()][:20]
            if not tickers:
                result = {"error": "tickers는 필수입니다 (콤마 구분 종목코드)"}
            else:
                result = await batch_stock_detail(tickers, token, delay=delay)

        else:
            result = {"error": f"unknown tool: {name}"}

    except Exception as e:
        tb = traceback.format_exc()
        result = {"error": str(e), "traceback": tb}
        print(f"에러: {name} → {e}\n{tb}")

    print(f"툴 결과: {name} → {json.dumps(result, ensure_ascii=False)[:200]}")
    return result


async def _handle_jsonrpc(body: dict) -> dict | None:
    """JSON-RPC 요청 처리 → 응답 dict (notification이면 None)"""
    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "kis-stock-bot", "version": "1.0.0"},
        }}

    if method.startswith("notifications/"):
        return None  # notification은 응답 없음

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS, "nextCursor": None}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        result = await _execute_tool(tool_name, tool_args)
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
        }}

    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


async def mcp_sse_handler(request: web.Request) -> web.StreamResponse:
    """GET /mcp  → SSE 스트림 수립, endpoint 이벤트 전송"""
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _mcp_sessions[session_id] = queue
    print(f"SSE 연결됨: {session_id}")

    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
    })
    await resp.prepare(request)

    # 클라이언트에 메시지 POST URL 전달
    await resp.write(
        ("event: endpoint\n"
         f"data: /mcp/messages?sessionId={session_id}\n\n").encode()
    )

    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30)
                if msg is None:
                    break
                data = json.dumps(msg, ensure_ascii=False)
                await resp.write(
                    ("event: message\n" + f"data: {data}\n\n").encode()
                )
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception as e:
        print(f"에러: SSE [{session_id}] {e}")
    finally:
        _mcp_sessions.pop(session_id, None)
        print(f"SSE 종료: {session_id}")

    return resp


async def mcp_messages_handler(request: web.Request) -> web.Response:
    """POST /mcp/messages?sessionId=UUID  → JSON-RPC 수신 후 SSE로 응답"""
    session_id = request.rel_url.query.get("sessionId")
    queue = _mcp_sessions.get(session_id)
    if not queue:
        return web.json_response({"error": "session not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    response = await _handle_jsonrpc(body)
    if response is not None:
        await queue.put(response)

    return web.Response(status=202, text="Accepted")
