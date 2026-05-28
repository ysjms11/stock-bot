"""FMP API + YouTube 자막 추출."""
import os
import json
import re
import asyncio
import aiohttp
import sqlite3
import xml.etree.ElementTree as ET
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from ._config import *
from ._config import (
    KIS_BASE_URL, KIS_APP_KEY, KIS_APP_SECRET, KST, ET, _DATA_DIR, _DB_PATH,
    WATCHLIST_FILE, STOPLOSS_FILE, US_WATCHLIST_FILE, DART_SEEN_FILE,
    PORTFOLIO_FILE, WATCHALERT_FILE, WATCH_SENT_FILE, STOPLOSS_SENT_FILE,
    US_HOLDINGS_SENT_FILE, DECISION_LOG_FILE, COMPARE_LOG_FILE,
    WATCHLIST_LOG_FILE, EVENTS_FILE, WEEKLY_BASE_FILE, UNIVERSE_FILE,
    CONSENSUS_CACHE_FILE, PORTFOLIO_HISTORY_FILE, TRADE_LOG_FILE,
    SECTOR_FLOW_CACHE_FILE, SECTOR_ROTATION_FILE, SUPPLY_HISTORY_FILE,
    REPORTS_FILE, REGIME_STATE_FILE, MACRO_SENT_FILE, TOKEN_CACHE_FILE,
    GITHUB_TOKEN, _BACKUP_GIST_ENV, _BACKUP_FILES_LIST, MACRO_SYMBOLS,
    DART_BASE_URL,
)
from ._session import _get_session, _kis_get, _kis_headers, get_kis_token, _token_cache
from ._helpers import (
    _is_us_ticker, _guess_excd, _is_us_market_hours_kst, _is_us_market_closed,
    DART_KEYWORDS, _load_knu_senti_lex, _FINANCE_PHRASE_SCORES, _RANKING_RE,
    _US_POSITIVE_KEYWORDS, _US_NEGATIVE_KEYWORDS, _NYSE_TICKERS, _AMEX_TICKERS,
)
from ._files import (
    load_json, save_json, load_watchlist, load_stoploss, load_us_watchlist,
    load_dart_seen, load_watchalert, _wa_market, load_kr_watch_tickers,
    load_us_watch_tickers, load_kr_watch_dict, load_us_watch_dict,
    load_decision_log, load_trade_log, save_trade_log, get_trade_stats,
    load_consensus_cache, load_sector_flow_cache, save_sector_flow_cache,
    load_compare_log, load_watchlist_log, append_watchlist_log, load_events,
)
from .regime import _YT_URL_RE


def _extract_youtube_id(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    if not s:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = _YT_URL_RE.search(s)
    return m.group(1) if m else ""


def fetch_youtube_transcript(url_or_id: str, languages: list | None = None,
                             max_chars: int = 0) -> dict:
    import youtube_transcript_api as _yta

    vid = _extract_youtube_id(url_or_id)
    if not vid:
        return {"error": "유효한 유튜브 URL/ID 아님", "input": url_or_id}

    langs = languages or ["ko", "en"]
    try:
        api = _yta.YouTubeTranscriptApi()
        t = api.fetch(vid, languages=langs)
    except _yta.TranscriptsDisabled:
        return {"error": "자막 비활성화된 영상", "video_id": vid}
    except _yta.NoTranscriptFound:
        return {"error": f"요청 언어({','.join(langs)}) 자막 없음", "video_id": vid}
    except _yta.VideoUnavailable:
        return {"error": "영상 접근 불가 (삭제/비공개)", "video_id": vid}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "video_id": vid}

    snippets = list(t)
    text = "\n".join(s.text for s in snippets if s.text)
    total_sec = int(snippets[-1].start + snippets[-1].duration) if snippets else 0
    mm, ss = divmod(total_sec, 60)
    hh, mm = divmod(mm, 60)
    dur = f"{hh:d}:{mm:02d}:{ss:02d}" if hh else f"{mm:d}:{ss:02d}"

    char_count = len(text)
    truncated = False
    if max_chars and char_count > max_chars:
        text = text[:max_chars] + f"\n...[TRUNCATED: {char_count - max_chars} chars omitted]"
        truncated = True

    return {
        "video_id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "language": getattr(t, "language", None) or langs[0],
        "language_code": getattr(t, "language_code", None) or langs[0],
        "duration": dur,
        "line_count": len(snippets),
        "char_count": char_count,
        "truncated": truncated,
        "transcript": text,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Financial Modeling Prep (FMP) — 미국 종목 본문 데이터
# 무료 250 calls/day, .env FMP_API_KEY 필요
# ━━━━━━━━━━━━━━━━━━━━━━━━━

FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FMP_BASE_URL = "https://financialmodelingprep.com/stable"


async def fmp_earnings_transcript(ticker: str, year: int, quarter: int,
                                    max_chars: int = 0) -> dict:
    """FMP earnings call transcript 본문 조회.

    Returns: {"symbol", "period", "year", "date", "char_count", "transcript", "truncated"}
    """
    if not FMP_API_KEY:
        return {"error": "FMP_API_KEY 미설정 (.env 추가 필요)"}
    ticker = ticker.upper()
    url = f"{FMP_BASE_URL}/earning-call-transcript"
    params = {"symbol": ticker, "year": year, "quarter": quarter, "apikey": FMP_API_KEY}
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    return {"error": f"FMP HTTP {r.status}", "ticker": ticker}
                data = await r.json()
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}", "ticker": ticker}

    if not data or not isinstance(data, list):
        return {"error": "transcript 없음 (분기 미발표 또는 미커버)",
                "ticker": ticker, "year": year, "quarter": quarter}

    rec = data[0]
    text = rec.get("content", "") or ""
    char_count = len(text)
    truncated = False
    if max_chars and char_count > max_chars:
        text = text[:max_chars] + f"\n...[TRUNCATED: {char_count - max_chars} chars omitted]"
        truncated = True

    return {
        "symbol": rec.get("symbol", ticker),
        "period": rec.get("period"),
        "year": rec.get("year"),
        "date": rec.get("date"),
        "char_count": char_count,
        "truncated": truncated,
        "transcript": text,
    }


async def fmp_price_target_summary(ticker: str) -> dict:
    """FMP analyst price target 평균/추세."""
    if not FMP_API_KEY:
        return {"error": "FMP_API_KEY 미설정"}
    ticker = ticker.upper()
    url = f"{FMP_BASE_URL}/price-target-summary"
    params = {"symbol": ticker, "apikey": FMP_API_KEY}
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return {"error": f"FMP HTTP {r.status}"}
                data = await r.json()
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    if not data or not isinstance(data, list):
        return {"ticker": ticker, "data": None, "message": "price target 없음"}

    rec = data[0]
    return {
        "symbol": rec.get("symbol", ticker),
        "last_month": {"count": rec.get("lastMonthCount"),
                        "avg_target": rec.get("lastMonthAvgPriceTarget")},
        "last_quarter": {"count": rec.get("lastQuarterCount"),
                          "avg_target": rec.get("lastQuarterAvgPriceTarget")},
        "last_year": {"count": rec.get("lastYearCount"),
                       "avg_target": rec.get("lastYearAvgPriceTarget")},
        "all_time": {"count": rec.get("allTimeCount"),
                      "avg_target": rec.get("allTimeAvgPriceTarget")},
    }


async def fmp_analyst_estimates(ticker: str, period: str = "annual",
                                  limit: int = 5) -> dict:
    """FMP analyst estimates — 매출/EBITDA/순이익 Low/High/Avg 추정.

    period: 'annual' or 'quarter'
    """
    if not FMP_API_KEY:
        return {"error": "FMP_API_KEY 미설정"}
    ticker = ticker.upper()
    url = f"{FMP_BASE_URL}/analyst-estimates"
    params = {"symbol": ticker, "period": period, "apikey": FMP_API_KEY}
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return {"error": f"FMP HTTP {r.status}"}
                data = await r.json()
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    if not data or not isinstance(data, list):
        return {"ticker": ticker, "estimates": []}

    estimates = []
    for rec in data[:limit]:
        estimates.append({
            "date": rec.get("date"),
            "revenue": {"low": rec.get("revenueLow"),
                         "avg": rec.get("revenueAvg"),
                         "high": rec.get("revenueHigh")},
            "ebitda": {"low": rec.get("ebitdaLow"),
                        "avg": rec.get("ebitdaAvg"),
                        "high": rec.get("ebitdaHigh")},
            "ebit": {"low": rec.get("ebitLow"),
                      "avg": rec.get("ebitAvg"),
                      "high": rec.get("ebitHigh")},
            "net_income": {"low": rec.get("netIncomeLow"),
                            "avg": rec.get("netIncomeAvg"),
                            "high": rec.get("netIncomeHigh")},
            "eps": {"low": rec.get("epsLow"),
                     "avg": rec.get("epsAvg"),
                     "high": rec.get("epsHigh")},
            "analyst_count": rec.get("numAnalystsRevenue") or rec.get("numAnalystsEps"),
        })
    return {"symbol": ticker, "period": period, "estimates": estimates}


async def fmp_stock_grades(ticker: str, limit: int = 20) -> dict:
    """FMP stock grades — 증권사 등급 변경 이력.

    Returns: 최근 N건의 등급 변경 (action: upgrade/downgrade/maintain)
    """
    if not FMP_API_KEY:
        return {"error": "FMP_API_KEY 미설정"}
    ticker = ticker.upper()
    url = f"{FMP_BASE_URL}/grades"
    params = {"symbol": ticker, "apikey": FMP_API_KEY}
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return {"error": f"FMP HTTP {r.status}"}
                data = await r.json()
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    if not data or not isinstance(data, list):
        return {"ticker": ticker, "grades": []}

    grades = [{
        "date": r.get("date"),
        "firm": r.get("gradingCompany"),
        "previous": r.get("previousGrade"),
        "new": r.get("newGrade"),
        "action": r.get("action"),
    } for r in data[:limit]]
    return {"symbol": ticker, "count": len(grades), "grades": grades}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🌐 외부 시그널 (Polymarket + Treasury Curve + Fed)
# 투자에 직접 영향: 매크로/지정학/정치 베팅 + 금리 곡선
# Susquehanna·Jump Trading·Bloomberg·CNBC 다 활용
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

POLYMARKET_API = "https://gamma-api.polymarket.com"
FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"  # 무료, API key 불필요

# 노이즈 카테고리 (sports/esports/pop culture 자동 컷)
_POLY_NOISE_TAGS = {"Sports", "Esports", "NBA", "NHL", "NFL", "MLB", "Soccer",
                    "Football", "Tennis", "Cricket", "Pop Culture", "Music",
                    "Film", "TV", "Award", "Celebrity", "Hide From New", "Game",
                    "Games", "Olympics", "Boxing", "MMA", "Golf"}


