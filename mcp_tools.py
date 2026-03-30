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
    backup_data_files, restore_data_files, get_backup_status,
    SUPPLY_HISTORY_FILE,
    get_historical_ohlcv, get_historical_supply,
    fetch_us_news, analyze_us_news_sentiment,
    fetch_us_earnings_calendar, fetch_us_sector_etf,
)

try:
    from report_crawler import (
        load_reports, collect_reports, get_collection_tickers,
    )
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False
    print("[mcp] report_crawler 미설치 — manage_report 비활성")

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
    # 1. get_rank ← scan_market + get_price_rank + get_us_price_rank + get_volume_power
    {"name": "get_rank",
     "description": "순위 조회 통합. type별: price=한국등락률상위/하위, us_price=미국등락률상위/하위, volume=체결강도상위(120%이상=매수우위), scan=거래량상위종목",
     "inputSchema": {"type": "object",
                     "properties": {
                         "type": {"type": "string", "enum": ["price", "us_price", "volume", "scan"], "description": "순위 조회 유형"},
                         "sort": {"type": "string", "description": "price/us_price용 (rise/fall, 기본 rise)"},
                         "market": {"type": "string", "description": "price용 (all/kospi/kosdaq, 기본 all)"},
                         "exchange": {"type": "string", "description": "us_price용 (NAS/NYS/AMS, 기본 NAS)"},
                         "n": {"type": "integer", "description": "결과 수 (기본 20)"},
                     },
                     "required": ["type"]}},
    # 2. get_portfolio (유지)
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
    # 3. get_stock_detail (확장 ← + get_batch_detail)
    {"name": "get_stock_detail",
     "description": "개별 종목 상세: 현재가·PER·PBR·수급 또는 일봉 조회. 한국/미국 자동 판별. period 지정 시 일봉 반환. tickers 전달 시 여러 종목 일괄 조회 (최대 20종목).",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드(예: 005930) 또는 미국 티커(예: TSLA, AAPL)"},
                         "period": {"type": "string", "description": "일봉 조회 시 지정 (예: D60=최근 60일, D30=30일, W20=20주). 생략 시 현재가 상세 반환"},
                         "tickers": {"type": "string", "description": "콤마 구분 종목코드로 다종목 일괄 조회 (예: '005930,000660'). 최대 20종목."},
                         "delay": {"type": "number", "description": "일괄조회 시 종목간 딜레이 (기본 0.3초)"},
                     },
                     "required": []}},
    # 4. get_supply ← get_investor_flow + get_investor_trend_history + get_investor_estimate + get_foreign_rank + get_foreign_institution
    {"name": "get_supply",
     "description": "수급 분석 통합. mode별: daily=당일확정수급(외인/기관/개인), history=N일수급추세(연속매수매도), estimate=장중추정수급(가집계), foreign_rank=외국인순매수상위, combined_rank=외인+기관합산순매수상위",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode": {"type": "string", "enum": ["daily", "history", "estimate", "foreign_rank", "combined_rank"], "description": "수급 조회 모드"},
                         "ticker": {"type": "string", "description": "종목코드 (daily/history/estimate 시 필수)"},
                         "days": {"type": "integer", "description": "history 시 조회 일수 (기본 5, 최대 10)"},
                         "sort": {"type": "string", "description": "combined_rank 시 정렬 (buy/sell, 기본 buy)"},
                         "n": {"type": "integer", "description": "foreign_rank/combined_rank 결과 수"},
                     },
                     "required": ["mode"]}},
    # 5. get_dart (유지)
    {"name": "get_dart",       "description": "워치리스트 최근 3일 DART 공시",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    # 6. get_macro (유지)
    {"name": "get_macro",
     "description": "매크로 지표 조회. mode 생략 시 KOSPI·KOSDAQ·환율. mode='dashboard': VIX·WTI·금·구리·DXY·US10Y 등 전체. mode='sector_etf': 섹터 ETF 시세. mode='us_sector': 미국 섹터 ETF 등락률 (SPY/QQQ/XLK 등). mode='convergence': 이평선 수렴 스크리너 (disp_20/disp_60 이격도 포함, market/sort 지원). mode='convergence2': 코스닥 위주 하위호환. mode='op_growth': KIS 영업이익 증가율 스크리너. mode='op_turnaround': KIS 적자→흑자 전환. mode='dart_op_growth': DART 기반 연간 영업이익 성장률 스크리너. mode='dart_turnaround': DART 기반 적자→흑자 전환.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode":       {"type": "string", "description": "'dashboard'|'sector_etf'|'us_sector'|'convergence'|'convergence2'|'op_growth'|'op_turnaround'|'dart_op_growth'|'dart_turnaround'|생략"},
                         "spread":     {"type": "number", "description": "[convergence] 이평 수렴 기준 % (기본 5.0)"},
                         "market":     {"type": "string", "description": "[convergence] 'all'=코스피+코스닥(기본), 'kospi'=코스피위주, 'kosdaq'=코스닥위주"},
                         "sort":       {"type": "string", "description": "[convergence] 'spread'=수렴도순(기본), 'disp_20'=20일이격도순, 'disp_60'=60일이격도순. [op_growth/op_turnaround/dart_op_growth/dart_turnaround] 'yoy'=연간증가율순(기본), 'qoq'=분기증가율순, 'trend'=분기추세순(연속증가>흑자전환>감소>적자전환>적자지속)"},
                         "min_growth": {"type": "number", "description": "[op_growth/dart_op_growth] 영업이익 최소 증가율 % (기본 50)"},
                     },
                     "required": []}},
    # 7. get_sector ← get_sector_flow + get_sector_rotation
    {"name": "get_sector",
     "description": "섹터 분석 통합. mode별: flow=WI26업종별외인+기관순매수(기본), rotation=섹터로테이션감지(전일대비자금이동)",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode": {"type": "string", "description": "'flow'(기본) 또는 'rotation'"},
                     },
                     "required": []}},
    # 8. manage_watch ← add_watch + remove_watch
    {"name": "manage_watch",
     "description": "워치리스트 관리. action별: add=종목추가(변동이력자동기록), remove=종목제거(변동이력자동기록)",
     "inputSchema": {"type": "object",
                     "properties": {
                         "action": {"type": "string", "enum": ["add", "remove"], "description": "추가 또는 제거"},
                         "ticker": {"type": "string", "description": "종목코드 (예: 005930) 또는 미국 티커"},
                         "name": {"type": "string", "description": "종목명 (add 시 필수)"},
                         "alert_type": {"type": "string", "description": "remove 시 삭제 대상: 'watchlist'(기본) 또는 'buy_alert'"},
                     },
                     "required": ["action", "ticker"]}},
    # 9. get_alerts (유지)
    {"name": "get_alerts",     "description": "손절가 목록 + 현재가 대비 손절까지 남은 % + 매수감시 목록",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    # 10. get_market_signal ← get_short_sale + get_vi_status + get_program_trade
    {"name": "get_market_signal",
     "description": "시장 시그널 통합. mode별: short_sale=공매도일별추이, vi=VI발동종목현황, program_trade=프로그램매매투자자별동향",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode": {"type": "string", "enum": ["short_sale", "vi", "program_trade"], "description": "시그널 조회 모드"},
                         "ticker": {"type": "string", "description": "종목코드 (short_sale 시 필수)"},
                         "days": {"type": "integer", "description": "short_sale 조회 일수 (기본 10)"},
                         "market": {"type": "string", "description": "program_trade 시 시장 (kospi/kosdaq, 기본 kospi)"},
                     },
                     "required": ["mode"]}},
    # 11. get_news (확장 ← + get_news_sentiment)
    {"name": "get_news",
     "description": "종목 뉴스 헤드라인. 한국(KIS)/미국(yfinance) 자동 판별. sentiment=true 시 헤드라인 감성분석(긍정/부정/중립) 포함",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드 (감성분석 전체조회 시 생략 가능)"},
                         "n": {"type": "integer", "description": "뉴스 개수 (기본 10)"},
                         "sentiment": {"type": "boolean", "description": "true 시 감성분석 포함 (기본 false)"},
                     },
                     "required": []}},
    # 12. get_consensus (유지)
    {"name": "get_consensus",  "description": "종목별 증권사 컨센서스 목표주가/투자의견 조회 (FnGuide 기반). 평균·최고·최저 목표주가, 매수/중립/매도 건수, 증권사별 최신 목표가 반환.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드 6자리 (예: 009540)"},
                     },
                     "required": ["ticker"]}},
    # 13. set_alert (확장 ← + delete_alert)
    {"name": "set_alert",      "description": "손절가/목표가 등록, 매수감시, 투자판단 기록, 매매기록, 알림삭제. log_type으로 모드 선택: 생략→stop/buy, decision→투자판단, compare→종목비교, trade→매매기록, delete→매도 후 알림 완전 삭제 (ticker, market 필요)",
     "inputSchema": {"type": "object",
                     "properties": {
                         "log_type":          {"type": "string", "description": "모드: 생략=stop/buy, 'decision'=투자판단, 'compare'=종목비교, 'trade'=매매기록, 'delete'=알림삭제"},
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
                         "market":            {"type": "string", "description": "[delete] 'KR'=한국(기본), 'US'=미국"},
                     },
                     "required": []}},
    # 14. get_portfolio_history (유지)
    {"name": "get_portfolio_history",
     "description": "포트폴리오 스냅샷 히스토리 + 드로다운 분석. 주간/월간 수익률, 월간 최대 드로다운, 투자규칙 경고(주간-4%/월간-7%/연속손절3회) 포함.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "days": {"type": "integer", "description": "최근 N일 스냅샷 반환 (기본 30, 최대 365)"},
                     },
                     "required": []}},
    # 15. get_trade_stats (유지)
    {"name": "get_trade_stats",
     "description": "매매 기록 성과 분석. 승률·손익·평균보유기간·확신등급 정확도 등 반환. 월간 복기 시 사용.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "period": {"type": "string", "description": "'month'=이번달(기본), 'quarter'=이번분기, 'year'=올해, 'all'=전체"},
                     },
                     "required": []}},
    # 16. backup_data (유지)
    {"name": "backup_data",
     "description": "/data/*.json 파일 GitHub Gist 백업·복원·상태 조회. action='backup': Gist에 백업, 'restore': Gist에서 복원(기존 파일 보존), 'restore_force': 강제 덮어쓰기 복원, 'status': 최근 백업 정보 조회.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "action": {"type": "string", "description": "'backup' | 'restore' | 'restore_force' | 'status'"},
                     },
                     "required": ["action"]}},
    # 17. simulate_trade (유지)
    {"name": "simulate_trade",
     "description": "포트폴리오 매매 시뮬레이션. 매도/매수 후 비중·섹터·현금·RR비율 변화를 미리보기.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "sells": {"type": "array", "description": "매도 목록 [{ticker, qty, price(선택)}]",
                                   "items": {"type": "object", "properties": {
                                       "ticker": {"type": "string"}, "qty": {"type": "integer"},
                                       "price": {"type": "number", "description": "매도가 (생략 시 현재가)"}}}},
                         "buys": {"type": "array", "description": "매수 목록 [{ticker, qty, price(선택)}]",
                                  "items": {"type": "object", "properties": {
                                      "ticker": {"type": "string"}, "qty": {"type": "integer"},
                                      "price": {"type": "number", "description": "매수가 (생략 시 현재가)"}}}},
                     },
                     "required": []}},
    # 18. get_backtest (유지)
    {"name": "get_backtest",
     "description": "종목 백테스트. 52주 일봉 데이터로 전략별 시뮬레이션. 수익률·승률·MDD·매매내역 반환. Buy&Hold 벤치마크 비교 포함.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker":   {"type": "string", "description": "종목코드(예: 005930) 또는 미국 티커(예: AAPL)"},
                         "period":   {"type": "string", "description": "일봉 기간. D250=52주(KIS API), D120=6개월, D60=3개월, Y1=1년/Y2=2년/Y3=3년(FDR/yfinance 사용)"},
                         "strategy": {"type": "string", "description": "전략: 'ma_cross'(이평교차, 기본), 'momentum_exit'(모멘텀종료), 'supply_follow'(수급추종, 10일제한), 'bollinger'(볼린저밴드), 'hybrid'(복합)"},
                     },
                     "required": ["ticker"]}},
    {"name": "manage_report",
     "description": "증권사 리포트 관리. action별: list=수집된 리포트 조회(days/ticker 필터), collect=수동 수집 트리거, tickers=수집 대상 종목 목록",
     "inputSchema": {"type": "object",
                     "properties": {
                         "action": {"type": "string", "enum": ["list", "collect", "tickers"], "description": "list=조회, collect=수집, tickers=대상종목"},
                         "days": {"type": "integer", "description": "list 시 최근 N일 (기본 7)"},
                         "ticker": {"type": "string", "description": "list/collect 시 특정 종목 필터"},
                         "brief": {"type": "boolean", "description": "list 시 true면 제목+증권사만 (full_text 제외)"},
                     },
                     "required": ["action"]}},
]


async def _execute_tool(name: str, arguments: dict) -> dict | list:
    """툴 실행 → 결과 반환 (에러 시 {"error": ...})"""
    arguments = arguments or {}
    print(f"툴 호출: {name} {arguments}")
    try:
        token = await get_kis_token()
        if not token:
            raise RuntimeError("KIS 토큰 발급 실패")

        if name == "get_rank":
            rank_type = arguments.get("type", "scan").strip().lower()

            if rank_type == "price":
                # ← 기존 get_price_rank 핸들러
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

            elif rank_type == "us_price":
                # ← 기존 get_us_price_rank 핸들러
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

            elif rank_type == "volume":
                # ← 기존 get_volume_power 핸들러
                market = arguments.get("market", "all").strip().lower()
                n      = int(arguments.get("n", 20) or 20)
                n      = max(1, min(n, 50))
                items  = await kis_volume_power_rank(token, market=market, n=n)
                result = {
                    "market": market,
                    "count":  len(items),
                    "items":  items,
                }

            elif rank_type == "scan":
                # ← 기존 scan_market 핸들러
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

            else:
                result = {"error": f"알 수 없는 type: {rank_type}. price/us_price/volume/scan 중 하나"}

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
                        portfolio["us_stocks"] = holdings
                    elif holdings:
                        # 기존 KR 종목 제거 후 새로 설정
                        _meta_keys = {"us_stocks", "cash_krw", "cash_usd"}
                        old_kr = [k for k in portfolio if k not in _meta_keys]
                        for k in old_kr:
                            del portfolio[k]
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
                _meta_keys = {"us_stocks", "cash_krw", "cash_usd"}
                kr_stocks = {k: v for k, v in portfolio.items() if k not in _meta_keys}
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
                        "cash_krw": portfolio.get("cash_krw", 0),
                        "cash_usd": portfolio.get("cash_usd", 0),
                    }

        elif name == "get_stock_detail":
            # ── 다종목 일괄 조회 (tickers 파라미터) ──
            batch_tickers_raw = arguments.get("tickers", "")
            if batch_tickers_raw:
                # ← 기존 get_batch_detail 핸들러
                raw = batch_tickers_raw
                delay = float(arguments.get("delay", 0.3) or 0.3)
                tickers = [t.strip().upper() for t in raw.split(",") if t.strip()][:20]
                if not tickers:
                    result = {"error": "tickers는 필수입니다 (콤마 구분 종목코드)"}
                else:
                    result = await batch_stock_detail(tickers, token, delay=delay)
            else:
                # ── 단일 종목 조회 (기존 로직) ──
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

        elif name == "get_supply":
            supply_mode = arguments.get("mode", "daily").strip().lower()

            if supply_mode == "daily":
                # ← 기존 get_investor_flow 핸들러
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

            elif supply_mode == "history":
                # ← 기존 get_investor_trend_history 핸들러
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

            elif supply_mode == "estimate":
                # ← 기존 get_investor_estimate 핸들러
                ticker = arguments.get("ticker", "").strip()
                if not ticker:
                    result = {"error": "ticker는 필수입니다"}
                else:
                    result = await kis_investor_trend_estimate(ticker, token)

            elif supply_mode == "foreign_rank":
                # ← 기존 get_foreign_rank 핸들러
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

            elif supply_mode == "combined_rank":
                # ← 기존 get_foreign_institution 핸들러
                sort = arguments.get("sort", "buy").strip().lower()
                n    = int(arguments.get("n", 20) or 20)
                n    = max(1, min(n, 50))
                items = await kis_foreign_institution_total(token, sort=sort, n=n)
                result = {
                    "sort":  sort,
                    "count": len(items),
                    "items": items,
                }

            else:
                result = {"error": f"알 수 없는 mode: {supply_mode}. daily/history/estimate/foreign_rank/combined_rank 중 하나"}

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

            elif mode == "us_sector":
                # ── 미국 섹터 ETF 등락률 ──
                loop = asyncio.get_running_loop()
                etfs = await loop.run_in_executor(None, fetch_us_sector_etf)
                if not etfs:
                    result = {"error": "미국 섹터 ETF 데이터 조회 실패 (yfinance)"}
                else:
                    sorted_etfs = sorted(etfs, key=lambda x: x["chg_1d"], reverse=True)
                    result = {
                        "mode": "us_sector",
                        "count": len(sorted_etfs),
                        "top3": sorted_etfs[:3],
                        "bottom3": sorted_etfs[-3:][::-1],
                        "all": sorted_etfs,
                    }

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

        elif name == "get_sector":
            sector_mode = arguments.get("mode", "flow").strip().lower()
            if not sector_mode:
                sector_mode = "flow"

            if sector_mode == "rotation":
                # ← 기존 get_sector_rotation 핸들러
                rot = await detect_sector_rotation(token)
                result = rot
            elif sector_mode == "flow":
                # ← 기존 get_sector_flow 핸들러 (mode="flow" 기본)
                now_kst = datetime.now(KST)
                today = now_kst.strftime("%Y%m%d")
                market_closed = now_kst.hour > 15 or (now_kst.hour == 15 and now_kst.minute >= 30)
                data_finalized = now_kst.hour >= 17 or (now_kst.hour == 16 and now_kst.minute >= 30)

                # ── 캐시 확인: 16:30 이후 확정 데이터 캐시만 사용 ──
                cache = load_sector_flow_cache()
                if data_finalized and cache.get("date") == today and "data" in cache:
                    result = dict(cache["data"])
                    result["cached"] = True
                    result["cached_at"] = cache.get("cached_at", "")
                else:
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

                    # ── 장마감 후 캐시 저장 (fallback 데이터는 캐시하지 않음) ──
                    if data_finalized and has_data:
                        save_sector_flow_cache({
                            "date": today,
                            "cached_at": now_kst.strftime("%H:%M:%S"),
                            "data": result,
                        })
                    result["cached"] = False

            else:
                result = {"error": f"알 수 없는 mode: {sector_mode}. flow/rotation 중 하나"}

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

            elif log_type == "delete":
                # ← 기존 delete_alert 핸들러
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

        elif name == "manage_watch":
            watch_action = arguments.get("action", "").strip().lower()

            if watch_action == "add":
                # ← 기존 add_watch 핸들러
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

            elif watch_action == "remove":
                # ← 기존 remove_watch 핸들러
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

            else:
                result = {"error": "action은 'add' 또는 'remove' 이어야 합니다"}

        elif name == "get_market_signal":
            signal_mode = arguments.get("mode", "").strip().lower()

            if signal_mode == "short_sale":
                # ← 기존 get_short_sale 핸들러
                ticker = arguments.get("ticker", "").strip()
                if not ticker:
                    result = {"error": "ticker는 필수입니다"}
                else:
                    n     = int(arguments.get("days", 10) or 10)
                    n     = max(1, min(n, 30))
                    rows  = await kis_daily_short_sale(ticker, token, n=n)
                    result = {
                        "ticker": ticker,
                        "count":  len(rows),
                        "items":  rows,
                    }

            elif signal_mode == "vi":
                # ← 기존 get_vi_status 핸들러
                rows   = await kis_vi_status(token)
                result = {
                    "count": len(rows),
                    "items": rows,
                }

            elif signal_mode == "program_trade":
                # ← 기존 get_program_trade 핸들러
                market = arguments.get("market", "kospi").strip().lower()
                rows   = await kis_program_trade_today(token, market=market)
                result = {
                    "market": market,
                    "count":  len(rows),
                    "items":  rows,
                }

            else:
                result = {"error": f"알 수 없는 mode: {signal_mode}. short_sale/vi/program_trade 중 하나"}

        elif name == "get_news":
            sentiment = arguments.get("sentiment", False)
            if isinstance(sentiment, str):
                sentiment = sentiment.lower() in ("true", "1", "yes")

            if sentiment:
                # ← 감성분석 모드
                ticker = arguments.get("ticker", "").strip()
                if ticker and _is_us_ticker(ticker):
                    # 미국 종목 감성분석
                    loop = asyncio.get_running_loop()
                    news = await loop.run_in_executor(None, fetch_us_news, ticker, 15)
                    analysis = analyze_us_news_sentiment(news)
                    result = {"ticker": ticker, "market": "US", **analysis}
                elif ticker:
                    # 한국 종목 감성분석
                    news = await kis_news_title(ticker, token, n=15)
                    analysis = analyze_news_sentiment(news)
                    result = {"ticker": ticker, **analysis}
                else:
                    portfolio = load_json(PORTFOLIO_FILE, {})
                    watchlist = load_watchlist()
                    tickers = {}
                    for t, v in portfolio.items():
                        if t not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict):
                            tickers[t] = v.get("name", t)
                    for t, n in watchlist.items():
                        if t not in tickers:
                            tickers[t] = n
                    all_results = []
                    for t, nm in tickers.items():
                        try:
                            news = await kis_news_title(t, token, n=10)
                            analysis = analyze_news_sentiment(news)
                            all_results.append({"ticker": t, "name": nm, **analysis})
                            await asyncio.sleep(0.3)
                        except Exception:
                            pass
                    all_results.sort(key=lambda x: len(x.get("negative", [])), reverse=True)
                    total_pos = sum(len(r.get("positive", [])) for r in all_results)
                    total_neg = sum(len(r.get("negative", [])) for r in all_results)
                    total_neu = sum(len(r.get("neutral", [])) for r in all_results)
                    result = {
                        "stocks": all_results,
                        "total_summary": f"긍정 {total_pos} / 부정 {total_neg} / 중립 {total_neu}",
                    }
            else:
                # ← 뉴스 헤드라인 모드
                ticker = arguments.get("ticker", "").strip()
                if not ticker:
                    result = {"error": "ticker는 필수입니다"}
                elif _is_us_ticker(ticker):
                    # 미국 종목 뉴스
                    n    = int(arguments.get("n", 10) or 10)
                    n    = max(1, min(n, 30))
                    loop = asyncio.get_running_loop()
                    rows = await loop.run_in_executor(None, fetch_us_news, ticker, n)
                    result = {
                        "ticker": ticker,
                        "market": "US",
                        "count":  len(rows),
                        "items":  rows,
                    }
                else:
                    # 한국 종목 뉴스
                    n    = int(arguments.get("n", 10) or 10)
                    n    = max(1, min(n, 30))
                    rows = await kis_news_title(ticker, token, n=n)
                    result = {
                        "ticker": ticker,
                        "count":  len(rows),
                        "items":  rows,
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

        elif name == "backup_data":
            action = arguments.get("action", "status").strip().lower()
            if action == "backup":
                result = await backup_data_files()
            elif action == "restore":
                result = await restore_data_files(force=False)
            elif action == "restore_force":
                result = await restore_data_files(force=True)
            elif action == "status":
                result = await get_backup_status()
            else:
                result = {"error": f"알 수 없는 action: {action}. 'backup'|'restore'|'restore_force'|'status' 중 하나"}

        elif name == "simulate_trade":
            sells = arguments.get("sells") or []
            buys = arguments.get("buys") or []
            if not sells and not buys:
                result = {"error": "sells 또는 buys 중 하나는 필요합니다"}
            else:
                portfolio = load_json(PORTFOLIO_FILE, {})
                _meta_keys = {"us_stocks", "cash_krw", "cash_usd"}
                kr_stocks = {k: dict(v) for k, v in portfolio.items() if k not in _meta_keys and isinstance(v, dict)}
                us_stocks = {k: dict(v) for k, v in portfolio.get("us_stocks", {}).items()}
                cash_krw = float(portfolio.get("cash_krw", 0) or 0)
                cash_usd = float(portfolio.get("cash_usd", 0) or 0)
                stops = load_stoploss()

                # 환율 조회 (fallback 1400)
                _fx = await get_yahoo_quote("USDKRW=X")
                _usd_krw = float(_fx.get("price", 1400)) if _fx else 1400

                # 현재가 캐시
                price_cache = {}

                async def get_cur_price(ticker):
                    if ticker in price_cache:
                        return price_cache[ticker]
                    if _is_us_ticker(ticker):
                        d = await kis_us_stock_price(ticker, token)
                        p = float(d.get("last", 0) or 0)
                    else:
                        d = await kis_stock_price(ticker, token)
                        p = int(d.get("stck_prpr", 0) or 0)
                    price_cache[ticker] = p
                    await asyncio.sleep(0.3)
                    return p

                # 시뮬레이션: 매도 적용
                sim_kr = {k: dict(v) for k, v in kr_stocks.items()}
                sim_us = {k: dict(v) for k, v in us_stocks.items()}
                sim_cash_krw = cash_krw
                sim_cash_usd = cash_usd
                trade_log = []

                for s in sells:
                    t = s.get("ticker", "").strip().upper()
                    q = int(s.get("qty", 0))
                    p = s.get("price")
                    if not t or q <= 0:
                        continue
                    if p is None or p <= 0:
                        p = await get_cur_price(t)
                    if _is_us_ticker(t):
                        if t in sim_us:
                            sim_us[t]["qty"] = max(0, sim_us[t].get("qty", 0) - q)
                            if sim_us[t]["qty"] == 0:
                                del sim_us[t]
                            sim_cash_usd += p * q
                            trade_log.append(f"매도 {t} {q}주 @${p:,.2f}")
                    else:
                        if t in sim_kr:
                            sim_kr[t]["qty"] = max(0, sim_kr[t].get("qty", 0) - q)
                            if sim_kr[t]["qty"] == 0:
                                del sim_kr[t]
                            sim_cash_krw += p * q
                            trade_log.append(f"매도 {t} {q}주 @{p:,.0f}원")

                # 시뮬레이션: 매수 적용
                for b in buys:
                    t = b.get("ticker", "").strip().upper()
                    q = int(b.get("qty", 0))
                    p = b.get("price")
                    if not t or q <= 0:
                        continue
                    if p is None or p <= 0:
                        p = await get_cur_price(t)
                    if _is_us_ticker(t):
                        cost = p * q
                        sim_cash_usd -= cost
                        if t in sim_us:
                            old_qty = sim_us[t].get("qty", 0)
                            old_avg = sim_us[t].get("avg_price", 0)
                            new_qty = old_qty + q
                            sim_us[t]["qty"] = new_qty
                            sim_us[t]["avg_price"] = round((old_avg * old_qty + p * q) / new_qty, 2)
                        else:
                            sim_us[t] = {"name": t, "qty": q, "avg_price": round(p, 2)}
                        trade_log.append(f"매수 {t} {q}주 @${p:,.2f}")
                    else:
                        cost = p * q
                        sim_cash_krw -= cost
                        if t in sim_kr:
                            old_qty = sim_kr[t].get("qty", 0)
                            old_avg = sim_kr[t].get("avg_price", 0)
                            new_qty = old_qty + q
                            sim_kr[t]["qty"] = new_qty
                            sim_kr[t]["avg_price"] = round((old_avg * old_qty + p * q) / new_qty)
                        else:
                            sim_kr[t] = {"name": t, "qty": q, "avg_price": round(p)}
                        trade_log.append(f"매수 {t} {q}주 @{p:,.0f}원")

                # 시뮬레이션 결과 계산
                # 1) 종목별 비중
                sim_eval_kr = 0
                sim_holdings_kr = []
                for t, info in sim_kr.items():
                    p = await get_cur_price(t)
                    ev = p * info.get("qty", 0)
                    sim_eval_kr += ev
                    sim_holdings_kr.append({"ticker": t, "name": info.get("name", t), "qty": info["qty"], "eval": ev})

                sim_eval_us = 0
                sim_holdings_us = []
                for t, info in sim_us.items():
                    p = await get_cur_price(t)
                    ev = p * info.get("qty", 0)
                    sim_eval_us += ev
                    sim_holdings_us.append({"ticker": t, "name": info.get("name", t), "qty": info["qty"], "eval": ev})

                total_eval = sim_eval_kr + sim_eval_us * _usd_krw + sim_cash_krw + sim_cash_usd * _usd_krw

                # 2) 비중 계산
                for h in sim_holdings_kr:
                    h["weight_pct"] = round(h["eval"] / total_eval * 100, 1) if total_eval > 0 else 0
                for h in sim_holdings_us:
                    h["weight_pct"] = round(h["eval"] * _usd_krw / total_eval * 100, 1) if total_eval > 0 else 0

                # 3) 섹터 비중 (국내만, _TICKER_SECTOR 사용)
                sector_eval = {}
                for h in sim_holdings_kr:
                    sec = _TICKER_SECTOR.get(h["ticker"], "기타")
                    sector_eval[sec] = sector_eval.get(sec, 0) + h["eval"]
                sector_weights = {s: round(v / total_eval * 100, 1) for s, v in sector_eval.items() if total_eval > 0}

                # 4) 현금 비중
                cash_total_krw = sim_cash_krw + sim_cash_usd * _usd_krw
                cash_pct = round(cash_total_krw / total_eval * 100, 1) if total_eval > 0 else 0

                # 5) RR 비율 (목표수익/손절손실)
                rr_items = []
                for h in sim_holdings_kr:
                    t = h["ticker"]
                    stop_info = stops.get(t, {})
                    stop_p = float(stop_info.get("stop_price", 0) or 0)
                    target_p = float(stop_info.get("target_price") or stop_info.get("target", 0) or 0)
                    cur_p = await get_cur_price(t)
                    if stop_p > 0 and target_p > 0 and cur_p > 0:
                        risk = (cur_p - stop_p) / cur_p * 100
                        reward = (target_p - cur_p) / cur_p * 100
                        rr = round(reward / risk, 2) if risk > 0 else 0
                        rr_items.append({"ticker": t, "risk_pct": round(risk, 1), "reward_pct": round(reward, 1), "rr": rr})

                result = {
                    "trades": trade_log,
                    "kr_holdings": sorted(sim_holdings_kr, key=lambda x: x["eval"], reverse=True),
                    "us_holdings": sorted(sim_holdings_us, key=lambda x: x["eval"], reverse=True),
                    "sector_weights": dict(sorted(sector_weights.items(), key=lambda x: x[1], reverse=True)),
                    "cash": {"krw": round(sim_cash_krw), "usd": round(sim_cash_usd, 2), "pct": cash_pct},
                    "total_eval_krw": round(total_eval),
                    "rr_ratios": rr_items,
                }

        elif name == "get_backtest":
            ticker = arguments.get("ticker", "").strip().upper()
            period = arguments.get("period", "D250").strip().upper()
            strategy = arguments.get("strategy", "ma_cross").strip().lower()

            if not ticker:
                result = {"error": "ticker는 필수입니다"}
            elif strategy not in ("ma_cross", "momentum_exit", "supply_follow", "bollinger", "hybrid"):
                result = {"error": f"지원 전략: ma_cross, momentum_exit, supply_follow, bollinger, hybrid"}
            else:
                is_us = _is_us_ticker(ticker)

                # ── 일봉 데이터 조회 ──
                period_type = period[0] if period else "D"
                try:
                    n = int(period[1:])
                except ValueError:
                    n = 250

                _krx_supply_map = {}   # Y모드 supply_follow용
                _data_error = None     # 데이터 조회 실패 시 에러 메시지

                if period_type == "Y":
                    # ── 장기 데이터: FDR/yfinance ──
                    years = max(1, min(n, 5))  # 1~5년 제한
                    loop = asyncio.get_running_loop()
                    candles = await loop.run_in_executor(None, get_historical_ohlcv, ticker, years)
                    if not candles:
                        _data_error = f"장기 데이터 조회 실패 ({ticker}, {years}년). FDR/yfinance 설치 확인: pip install finance-datareader yfinance"
                    else:
                        # supply_follow 전략 시 KRX 수급도 로드
                        if strategy == "supply_follow" and not is_us:
                            krx_supply = await loop.run_in_executor(None, get_historical_supply, ticker, years * 365)
                            if krx_supply:
                                _krx_supply_map = {s["date"]: s for s in krx_supply}
                else:
                    # ── 기존: KIS API 일봉 ──
                    today_str = datetime.now(KST).strftime("%Y%m%d")
                    buf = {"D": 2, "W": 8, "M": 40}.get(period_type, 2)
                    start_dt = (datetime.now(KST) - timedelta(days=n * buf)).strftime("%Y%m%d")

                    if is_us:
                        excd = _guess_excd(ticker)
                        async with aiohttp.ClientSession() as s:
                            _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/dailyprice",
                                "HHDFS76240000", token,
                                {"AUTH": "", "EXCD": excd, "SYMB": ticker,
                                 "GUBN": "0", "BYMD": today_str, "MODP": "0"})
                        raw_candles = d.get("output2", [])
                        candles = []
                        for c in raw_candles[:n]:
                            candles.append({
                                "date": c.get("xymd", ""),
                                "open": float(c.get("open", 0) or 0),
                                "high": float(c.get("high", 0) or 0),
                                "low": float(c.get("low", 0) or 0),
                                "close": float(c.get("clos", 0) or 0),
                                "vol": int(c.get("tvol", 0) or 0),
                            })
                    else:
                        # 국내 일봉 API 1회 최대 100건 → 분할 호출
                        candles = []
                        _chunk = 100
                        _end = today_str
                        _remaining = n
                        _seen_dates = set()
                        async with aiohttp.ClientSession() as s:
                            while _remaining > 0:
                                _start = (datetime.strptime(_end, "%Y%m%d") - timedelta(days=_chunk * 2)).strftime("%Y%m%d")
                                _, d = await _kis_get(s,
                                    "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                                    "FHKST03010100", token,
                                    {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
                                     "FID_INPUT_DATE_1": _start, "FID_INPUT_DATE_2": _end,
                                     "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
                                batch = d.get("output2", [])
                                if not batch:
                                    break
                                added = 0
                                for c in batch:
                                    dt = c.get("stck_bsop_date", "")
                                    if not dt or dt in _seen_dates:
                                        continue
                                    _seen_dates.add(dt)
                                    candles.append({
                                        "date": dt,
                                        "open": int(c.get("stck_oprc", 0) or 0),
                                        "high": int(c.get("stck_hgpr", 0) or 0),
                                        "low": int(c.get("stck_lwpr", 0) or 0),
                                        "close": int(c.get("stck_clpr", 0) or 0),
                                        "vol": int(c.get("acml_vol", 0) or 0),
                                    })
                                    added += 1
                                _remaining -= added
                                if added < 10:
                                    break  # 더 이상 데이터 없음
                                # 다음 구간: 가장 오래된 날짜 전일부터
                                oldest = min(_seen_dates)
                                _end = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
                                await asyncio.sleep(0.3)

                # 시간순 정렬 (API는 최신순, Y모드는 이미 정렬)
                if candles:
                    candles.sort(key=lambda x: x["date"])

                if _data_error:
                    result = {"error": _data_error}
                elif len(candles) < 20:
                    result = {"error": f"일봉 데이터 부족 ({len(candles)}개). 최소 20개 필요."}
                else:
                    # ── 비용 설정 ──
                    if is_us:
                        buy_cost_pct = 0.15 + 0.1    # 환전 0.15% + 슬리피지 0.1%
                        sell_cost_pct = 0.15 + 0.1
                    else:
                        buy_cost_pct = 0.015 + 0.1   # 수수료 0.015% + 슬리피지 0.1%
                        sell_cost_pct = 0.015 + 0.18 + 0.1  # +거래세 0.18%

                    # ── 이동평균 / 표준편차 헬퍼 ──
                    closes = [c["close"] for c in candles]
                    volumes = [c["vol"] for c in candles]

                    def _ma(arr, period_len, idx):
                        if idx < period_len - 1:
                            return None
                        return sum(arr[idx - period_len + 1:idx + 1]) / period_len

                    def _std(arr, period_len, idx):
                        if idx < period_len - 1:
                            return None
                        subset = arr[idx - period_len + 1:idx + 1]
                        avg = sum(subset) / period_len
                        return (sum((x - avg) ** 2 for x in subset) / period_len) ** 0.5

                    # ── 신호 생성 (look-ahead bias 방지: i일 종가 신호 → i+1일 시가 체결) ──
                    signals = [None] * len(candles)

                    if strategy == "ma_cross":
                        for i in range(20, len(candles)):
                            ma5p = _ma(closes, 5, i - 1)
                            ma20p = _ma(closes, 20, i - 1)
                            ma5c = _ma(closes, 5, i)
                            ma20c = _ma(closes, 20, i)
                            if ma5p and ma20p and ma5c and ma20c:
                                if ma5p <= ma20p and ma5c > ma20c:
                                    signals[i] = "buy"
                                elif ma5p >= ma20p and ma5c < ma20c:
                                    signals[i] = "sell"

                    elif strategy == "momentum_exit":
                        for i in range(20, len(candles)):
                            lookback = min(i, 250)
                            high_max = max(c["high"] for c in candles[i - lookback:i])
                            if candles[i]["close"] > high_max:
                                signals[i] = "buy"
                            recent_high = max(c["high"] for c in candles[max(0, i - 20):i + 1])
                            drop_pct = (recent_high - candles[i]["close"]) / recent_high * 100 if recent_high > 0 else 0
                            vol_ma20 = _ma(volumes, 20, i)
                            vol_ratio = candles[i]["vol"] / vol_ma20 if vol_ma20 and vol_ma20 > 0 else 1
                            if drop_pct >= 10 and vol_ratio <= 0.5:
                                signals[i] = "sell"

                    elif strategy == "supply_follow":
                        supply_by_date = {}

                        # 1순위: KRX 크롤링 데이터 (Y 모드에서 조회했으면)
                        if _krx_supply_map:
                            supply_by_date = _krx_supply_map

                        # 2순위: 기존 축적 데이터
                        if not supply_by_date:
                            supply_hist = load_json(SUPPLY_HISTORY_FILE, {})
                            ticker_supply = supply_hist.get(ticker, [])
                            supply_by_date = {s["date"].replace("-", ""): s for s in ticker_supply}

                        # 3순위: KIS API 10일
                        if not supply_by_date:
                            try:
                                api_hist = await kis_investor_trend_history(ticker, token, n_days=10)
                                api_hist.reverse()
                                ticker_supply = [{"date": h["date"][:4]+"-"+h["date"][4:6]+"-"+h["date"][6:],
                                                  "foreign_net": h["foreign_net"],
                                                  "institution_net": h["institution_net"]} for h in api_hist]
                                supply_by_date = {s["date"].replace("-", ""): s for s in ticker_supply}
                            except Exception:
                                pass
                        for i in range(2, len(candles)):
                            dates_3 = [candles[j]["date"] for j in range(i - 2, i + 1)]
                            frgn_3 = []
                            for dt in dates_3:
                                s_data = supply_by_date.get(dt)
                                if s_data:
                                    frgn_3.append(s_data.get("foreign_net", 0))
                            if len(frgn_3) == 3:
                                if all(f > 0 for f in frgn_3):
                                    signals[i] = "buy"
                                elif all(f < 0 for f in frgn_3):
                                    signals[i] = "sell"

                    elif strategy == "bollinger":
                        for i in range(19, len(candles)):
                            ma20 = _ma(closes, 20, i)
                            sd = _std(closes, 20, i)
                            if ma20 is not None and sd is not None:
                                upper = ma20 + 2 * sd
                                lower = ma20 - 2 * sd
                                if candles[i]["close"] <= lower:
                                    signals[i] = "buy"
                                elif candles[i]["close"] >= upper:
                                    signals[i] = "sell"

                    elif strategy == "hybrid":
                        for i in range(60, len(candles)):
                            ma5 = _ma(closes, 5, i)
                            ma20 = _ma(closes, 20, i)
                            ma60 = _ma(closes, 60, i)
                            vol_ma20 = _ma(volumes, 20, i)
                            if ma5 and ma20 and ma60 and vol_ma20:
                                aligned = ma5 > ma20 > ma60
                                vol_up = candles[i]["vol"] > vol_ma20
                                above_ma5 = candles[i]["close"] > ma5
                                if aligned and vol_up and above_ma5:
                                    signals[i] = "buy"
                                if ma5 < ma20:
                                    signals[i] = "sell"
                            recent_high = max(c["high"] for c in candles[max(0, i - 20):i + 1])
                            drop_pct = (recent_high - candles[i]["close"]) / recent_high * 100 if recent_high > 0 else 0
                            if drop_pct >= 10:
                                signals[i] = "sell"

                    # ── 매매 시뮬레이션 (익일 시가 체결) ──
                    trades = []
                    position = None

                    for i in range(len(candles) - 1):
                        sig = signals[i]
                        next_open = candles[i + 1]["open"]
                        next_date = candles[i + 1]["date"]

                        if next_open <= 0:
                            continue

                        if sig == "buy" and position is None:
                            entry_price = next_open * (1 + buy_cost_pct / 100)
                            position = {"entry_date": next_date, "entry_price": entry_price, "entry_idx": i + 1}

                        elif sig == "sell" and position is not None:
                            exit_price = next_open * (1 - sell_cost_pct / 100)
                            pnl_pct = (exit_price - position["entry_price"]) / position["entry_price"] * 100
                            hold_days = i + 1 - position["entry_idx"]
                            trades.append({
                                "entry_date": position["entry_date"],
                                "entry_price": round(position["entry_price"], 2),
                                "exit_date": next_date,
                                "exit_price": round(exit_price, 2),
                                "pnl_pct": round(pnl_pct, 2),
                                "hold_days": hold_days,
                            })
                            position = None

                    # 미청산 포지션 (마지막 종가로 평가)
                    if position is not None:
                        last = candles[-1]
                        exit_price = last["close"] * (1 - sell_cost_pct / 100)
                        pnl_pct = (exit_price - position["entry_price"]) / position["entry_price"] * 100
                        hold_days = len(candles) - 1 - position["entry_idx"]
                        trades.append({
                            "entry_date": position["entry_date"],
                            "entry_price": round(position["entry_price"], 2),
                            "exit_date": last["date"] + "(미청산)",
                            "exit_price": round(exit_price, 2),
                            "pnl_pct": round(pnl_pct, 2),
                            "hold_days": hold_days,
                            "open_position": True,
                        })

                    # ── 성과 계산 ──
                    wins = [t for t in trades if t["pnl_pct"] > 0]
                    losses = [t for t in trades if t["pnl_pct"] <= 0]
                    total_return = 1.0
                    for t in trades:
                        total_return *= (1 + t["pnl_pct"] / 100)
                    total_return_pct = round((total_return - 1) * 100, 2)

                    # MDD
                    peak = 1.0
                    mdd = 0.0
                    cumulative = 1.0
                    for t in trades:
                        cumulative *= (1 + t["pnl_pct"] / 100)
                        if cumulative > peak:
                            peak = cumulative
                        dd = (peak - cumulative) / peak * 100
                        if dd > mdd:
                            mdd = dd

                    # Buy & Hold 벤치마크
                    bh_entry = candles[0]["close"]
                    bh_exit = candles[-1]["close"]
                    bh_cost = buy_cost_pct + sell_cost_pct
                    bh_return = (bh_exit - bh_entry) / bh_entry * 100 - bh_cost if bh_entry > 0 else 0

                    avg_hold = round(sum(t["hold_days"] for t in trades) / len(trades), 1) if trades else 0

                    # supply_follow 경고
                    supply_warning = None
                    if strategy == "supply_follow":
                        if _krx_supply_map:
                            krx_days = len(_krx_supply_map)
                            if krx_days < 60:
                                supply_warning = f"KRX 수급 데이터 {krx_days}일분 조회됨. 데이터가 적어 신호 정밀도가 낮을 수 있음."
                        else:
                            supply_hist_data = load_json(SUPPLY_HISTORY_FILE, {})
                            ticker_days = len(supply_hist_data.get(ticker, []))
                            if ticker_days < 60:
                                supply_warning = f"수급 데이터 {ticker_days}일분만 축적됨 (KIS API 최대 10일). Y모드(FDR+KRX) 또는 3개월 축적 후 정밀화 가능."

                    result = {
                        "ticker": ticker,
                        "market": "US" if is_us else "KR",
                        "strategy": strategy,
                        "period": period,
                        "candle_count": len(candles),
                        "date_range": f"{candles[0]['date']}~{candles[-1]['date']}",
                        "total_return_pct": total_return_pct,
                        "benchmark_bh_pct": round(bh_return, 2),
                        "alpha_pct": round(total_return_pct - bh_return, 2),
                        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
                        "trade_count": len(trades),
                        "wins": len(wins),
                        "losses": len(losses),
                        "max_drawdown_pct": round(mdd, 2),
                        "avg_hold_days": avg_hold,
                        "costs": {"buy_pct": buy_cost_pct, "sell_pct": sell_cost_pct,
                                  "note": "한국: 수수료+거래세+슬리피지" if not is_us else "미국: 환전스프레드+슬리피지"},
                        "trades": trades,
                    }
                    if supply_warning:
                        result["supply_warning"] = supply_warning

        elif name == "manage_report":
            if not _REPORT_AVAILABLE:
                result = {"error": "report_crawler 모듈 미설치"}
            else:
                action = arguments.get("action", "list").strip().lower()

                if action == "list":
                    days = int(arguments.get("days", 7) or 7)
                    ticker_filter = arguments.get("ticker", "").strip()
                    brief = arguments.get("brief", False)

                    data = load_reports()
                    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
                    reports = [r for r in data.get("reports", []) if r.get("date", "") >= cutoff]

                    if ticker_filter:
                        reports = [r for r in reports if r.get("ticker") == ticker_filter]

                    if brief:
                        reports = [{"date": r.get("date"), "ticker": r.get("ticker"),
                                    "name": r.get("name"), "source": r.get("source"),
                                    "title": r.get("title"),
                                    "extraction_status": r.get("extraction_status", "unknown")} for r in reports]
                    else:
                        # full_text 3000자 제한 + extraction_status 하위호환
                        for r in reports:
                            if "extraction_status" not in r:
                                r["extraction_status"] = "unknown"
                            if r.get("full_text") and len(r["full_text"]) > 3000:
                                r["full_text"] = r["full_text"][:3000] + "...(truncated)"

                    result = {
                        "count": len(reports),
                        "days": days,
                        "reports": reports,
                        "last_collected": data.get("last_collected", ""),
                    }

                elif action == "collect":
                    ticker_filter = arguments.get("ticker", "").strip()
                    tickers = get_collection_tickers()
                    if ticker_filter:
                        name_for_ticker = tickers.get(ticker_filter, ticker_filter)
                        tickers = {ticker_filter: name_for_ticker}

                    loop = asyncio.get_running_loop()
                    new_reports = await loop.run_in_executor(None, collect_reports, tickers)
                    result = {
                        "collected": len(new_reports),
                        "reports": [{"date": r.get("date"), "ticker": r.get("ticker"),
                                     "name": r.get("name"), "source": r.get("source"),
                                     "title": r.get("title"),
                                     "extraction_status": r.get("extraction_status", "unknown")} for r in new_reports],
                    }

                elif action == "tickers":
                    tickers = get_collection_tickers()
                    result = {
                        "count": len(tickers),
                        "tickers": [{"ticker": t, "name": n} for t, n in tickers.items()],
                    }

                else:
                    result = {"error": f"알 수 없는 action: {action}. list|collect|tickers"}

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
