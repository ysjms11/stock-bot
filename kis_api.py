import os
import json
import re
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 공유 aiohttp 세션 (TCP 연결 풀 재사용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_shared_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    """공유 aiohttp 세션 반환. 없거나 닫혔으면 새로 생성."""
    global _shared_session
    if _shared_session is None or _shared_session.closed:
        connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=30)
        _shared_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _shared_session


async def close_session():
    """서버 종료 시 세션 정리."""
    global _shared_session
    if _shared_session and not _shared_session.closed:
        await _shared_session.close()
        _shared_session = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 환경변수 & 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
KIS_APP_KEY = os.environ.get("KIS_APP_KEY")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET")
DART_API_KEY = os.environ.get("DART_API_KEY", "")

KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
DART_BASE_URL = "https://opendart.fss.or.kr/api"
KST = timezone(timedelta(hours=9))
ET  = ZoneInfo('America/New_York')  # DST 자동 감지 (서머타임 EDT/표준시 EST)

os.makedirs("/data", exist_ok=True)

WATCHLIST_FILE    = "/data/watchlist.json"
STOPLOSS_FILE     = "/data/stoploss.json"
US_WATCHLIST_FILE = "/data/us_watchlist.json"
DART_SEEN_FILE    = "/data/dart_seen.json"
PORTFOLIO_FILE    = "/data/portfolio.json"
WATCHALERT_FILE   = "/data/watchalert.json"
WATCH_SENT_FILE      = "/data/watch_sent.json"
STOPLOSS_SENT_FILE   = "/data/stoploss_sent.json"
DECISION_LOG_FILE = "/data/decision_log.json"
COMPARE_LOG_FILE  = "/data/compare_log.json"
WATCHLIST_LOG_FILE = "/data/watchlist_log.json"
EVENTS_FILE       = "/data/events.json"
WEEKLY_BASE_FILE      = "/data/weekly_base.json"
UNIVERSE_FILE         = "/data/stock_universe.json"
CONSENSUS_CACHE_FILE      = "/data/consensus_cache.json"
PORTFOLIO_HISTORY_FILE    = "/data/portfolio_history.json"
TRADE_LOG_FILE            = "/data/trade_log.json"
SECTOR_FLOW_CACHE_FILE    = "/data/sector_flow_cache.json"
SECTOR_ROTATION_FILE      = "/data/sector_rotation.json"
SUPPLY_HISTORY_FILE       = "/data/supply_history.json"
REPORTS_FILE              = "/data/reports.json"
REGIME_STATE_FILE         = "/data/regime_state.json"

GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
_BACKUP_GIST_ENV  = "BACKUP_GIST_ID"
_BACKUP_FILES_LIST = [
    STOPLOSS_FILE, PORTFOLIO_FILE, WATCHLIST_FILE, US_WATCHLIST_FILE,
    WATCHALERT_FILE, WATCHLIST_LOG_FILE, PORTFOLIO_HISTORY_FILE,
    TRADE_LOG_FILE, CONSENSUS_CACHE_FILE, DECISION_LOG_FILE,
    REPORTS_FILE, REGIME_STATE_FILE,
]

MACRO_SYMBOLS = {
    "VIX":    "^VIX",
    "WTI":    "CL=F",
    "GOLD":   "GC=F",
    "COPPER": "HG=F",
    "DXY":    "DX-Y.NYB",
    "US10Y":  "^TNX",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 환경변수 기반 데이터 복원 (Railway Volume 미마운트 시 fallback)
# Railway Variables에 BACKUP_PORTFOLIO, BACKUP_STOPLOSS 등을 JSON 문자열로 설정하면
# /data/ 파일이 없을 때 자동 복원됨
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_BACKUP_MAP = {
    "BACKUP_PORTFOLIO":    PORTFOLIO_FILE,
    "BACKUP_STOPLOSS":     STOPLOSS_FILE,
    "BACKUP_WATCHLIST":    WATCHLIST_FILE,
    "BACKUP_US_WATCHLIST": US_WATCHLIST_FILE,
    "BACKUP_WATCHALERT":   WATCHALERT_FILE,
    "BACKUP_DECISION_LOG": DECISION_LOG_FILE,
    "BACKUP_COMPARE_LOG":  COMPARE_LOG_FILE,
    "BACKUP_EVENTS":       EVENTS_FILE,
    "BACKUP_WEEKLY_BASE":  WEEKLY_BASE_FILE,
}
for _env_key, _filepath in _BACKUP_MAP.items():
    if not os.path.exists(_filepath):
        _backup_val = os.environ.get(_env_key, "")
        if _backup_val:
            try:
                _data = json.loads(_backup_val)
                with open(_filepath, "w", encoding="utf-8") as _f:
                    json.dump(_data, _f, ensure_ascii=False, indent=2)
                print(f"[복원] {_filepath} ← 환경변수 {_env_key}")
            except Exception as _e:
                print(f"[복원 실패] {_env_key}: {_e}")

_token_cache = {"token": None, "expires": None}


def _is_us_ticker(ticker: str) -> bool:
    """영문 티커면 미국 종목으로 판별 (숫자 포함 없으면 US)"""
    return bool(ticker) and ticker.replace(".", "").replace("-", "").isalpha()


# NYSE 대표 종목 (나머지는 NASDAQ 기본)
_NYSE_TICKERS = {
    "BRK.A", "BRK.B", "JNJ", "V", "WMT", "PG", "MA", "HD", "DIS", "BA",
    "KO", "PFE", "MRK", "VZ", "T", "NKE", "MMM", "CAT", "GS", "JPM",
    "BAC", "C", "WFC", "UNH", "CVX", "XOM", "CRM", "ORCL", "IBM", "GE",
    "LMT", "RTX", "NOC", "PM", "MCD", "UPS", "FDX", "GM", "F",
    # 추가 NYSE 종목 (2026-04-05)
    "VRT", "ETN", "GLW", "MOD", "BWXT", "NVT", "STVN", "XYL",
    "HWM", "TDG", "GEV", "VST", "CEG", "CARR", "EMR", "ROK",
}
_AMEX_TICKERS = {
    "LEU", "HYMC", "BTG", "NGD", "USAS", "SAND",
}

def _guess_excd(symbol: str) -> str:
    """미국 종목 거래소코드 추정 (NYS/NAS/AMS)"""
    s = symbol.upper()
    if s in _NYSE_TICKERS:
        return "NYS"
    if s in _AMEX_TICKERS:
        return "AMS"
    return "NAS"


def _is_us_market_hours_kst() -> bool:
    """미국 장 시간 여부 (ET 09:30~16:00, DST 자동 감지)"""
    now_et = datetime.now(ET)
    wd = now_et.weekday()
    if wd >= 5:
        return False  # 토/일 ET → 미국 장 없음
    h, m = now_et.hour, now_et.minute
    if h < 9 or (h == 9 and m < 30):
        return False  # ET 09:30 이전
    if h >= 16:
        return False  # ET 16:00 이후
    return True


def _is_us_market_closed() -> bool:
    """미국 정규장 마감 후 30분 이내 여부 (DST 자동 감지)

    DST(UTC-4) 시: KST 05:00~05:30
    표준시(UTC-5) 시: KST 06:00~06:30
    """
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return False  # 토/일 ET → 미국 장 없음
    close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    diff_sec = (now_et - close_et).total_seconds()
    return 0 <= diff_sec <= 1800  # 마감 후 0~30분 이내

# DART 중요 공시 키워드
DART_KEYWORDS = [
    "수주", "계약", "공급계약", "납품", "유상증자", "무상증자",
    "전환사채", "신주인수권", "자기주식", "배당", "합병",
    "분할", "영업양수", "영업양도", "소송", "상장폐지",
    "실적", "매출", "영업이익", "감자", "대규모",
]

# 뉴스 감성 키워드 사전
_POSITIVE_KEYWORDS = [
    "상승", "급등", "신고가", "호실적", "수주", "계약", "흑자", "성장",
    "증가", "개선", "호재", "수혜", "기대", "목표가 상향", "매수",
    "반등", "강세", "돌파", "최고", "확대", "회복", "낙관",
    "상향", "호황", "투자확대", "수출증가", "영업이익 증가",
]

_NEGATIVE_KEYWORDS = [
    "하락", "급락", "신저가", "적자", "감소", "악화", "하향",
    "리스크", "우려", "경고", "매도", "약세", "손실", "부진",
    "위기", "제재", "소송", "감자", "상폐", "폭락", "둔화",
    "불확실", "위축", "수출감소", "영업이익 감소",
]

# 미국 뉴스 영문 감성 키워드 사전
_US_POSITIVE_KEYWORDS = [
    "surge", "soar", "rally", "beat", "upgrade", "bullish", "growth",
    "record", "outperform", "buy", "strong", "raise", "profit", "gain",
    "upside", "breakout", "momentum", "dividend", "expand",
]

_US_NEGATIVE_KEYWORDS = [
    "drop", "plunge", "crash", "miss", "downgrade", "bearish", "decline",
    "loss", "underperform", "sell", "weak", "cut", "warning", "risk",
    "layoff", "recall", "lawsuit", "investigation", "bankruptcy",
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 파일 저장/로드
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def load_json(filepath, default=None):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        if default is not None:
            save_json(filepath, default)
            return default
        return {}


def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_watchlist():
    return load_json(WATCHLIST_FILE, {
        "009540": "HD한국조선해양", "298040": "효성중공업",
        "010120": "LS ELECTRIC", "267260": "HD현대일렉트릭",
        "034020": "두산에너빌리티",
    })


def load_stoploss():
    return load_json(STOPLOSS_FILE, {})


def load_us_watchlist():
    return load_json(US_WATCHLIST_FILE, {
        "TSLA": {"name": "테슬라", "qty": 12},
        "CRSP": {"name": "크리스퍼", "qty": 70},
        "AMD": {"name": "AMD", "qty": 17},
        "LITE": {"name": "루멘텀", "qty": 4},
    })


def load_dart_seen():
    return load_json(DART_SEEN_FILE, {"ids": []})


def load_watchalert():
    return load_json(WATCHALERT_FILE, {})

def load_decision_log():
    return load_json(DECISION_LOG_FILE, {})

def load_trade_log() -> list:
    return load_json(TRADE_LOG_FILE, {"trades": []}).get("trades", [])

def save_trade_log(trades: list):
    if len(trades) > 1000:
        trades = trades[-1000:]
    save_json(TRADE_LOG_FILE, {"trades": trades})

def get_trade_stats(period: str = "month") -> dict:
    """매매 기록 성과 분석.
    period: 'month'=이번달, 'quarter'=이번분기, 'year'=올해, 'all'=전체"""
    from datetime import datetime as _dt
    now = _dt.now()

    if period == "month":
        cutoff = now.strftime("%Y-%m")
        label = now.strftime("%Y-%m")
    elif period == "quarter":
        q_start = ((now.month - 1) // 3) * 3 + 1
        cutoff = f"{now.year}-{q_start:02d}-01"
        label = f"{now.year}Q{(now.month - 1) // 3 + 1}"
    elif period == "year":
        cutoff = f"{now.year}-01-01"
        label = str(now.year)
    else:
        cutoff = "0000"
        label = "전체"

    all_trades = load_trade_log()

    if period == "month":
        sells = [t for t in all_trades if t.get("side") == "sell" and t.get("date", "").startswith(cutoff)]
    elif period == "all":
        sells = [t for t in all_trades if t.get("side") == "sell"]
    else:
        sells = [t for t in all_trades if t.get("side") == "sell" and t.get("date", "") >= cutoff]

    total  = len(sells)
    wins   = sum(1 for t in sells if t.get("result") == "win")
    losses = sum(1 for t in sells if t.get("result") == "loss")
    total_pnl = sum(t.get("pnl", 0) or 0 for t in sells)
    win_rate  = round(wins / total * 100, 1) if total > 0 else None
    avg_pnl   = round(total_pnl / total)     if total > 0 else None

    with_pnl = [t for t in sells if t.get("pnl_pct") is not None]
    best  = max(with_pnl, key=lambda x: x.get("pnl_pct", 0), default=None)
    worst = min(with_pnl, key=lambda x: x.get("pnl_pct", 0), default=None)

    def _brief(t):
        if not t:
            return None
        return {"id": t.get("id"), "ticker": t.get("ticker"), "name": t.get("name"),
                "pnl": t.get("pnl"), "pnl_pct": t.get("pnl_pct"),
                "holding_days": t.get("holding_days"), "date": t.get("date")}

    hold_days = [t.get("holding_days") for t in sells if t.get("holding_days") is not None]
    avg_hold  = round(sum(hold_days) / len(hold_days), 1) if hold_days else None

    # 등급별 정확도
    grade_acc: dict = {}
    for t in sells:
        g = (t.get("grade_at_trade") or "?").upper()
        if g not in grade_acc:
            grade_acc[g] = {"total": 0, "wins": 0, "win_rate": 0.0}
        grade_acc[g]["total"] += 1
        if t.get("result") == "win":
            grade_acc[g]["wins"] += 1
    for d in grade_acc.values():
        d["win_rate"] = round(d["wins"] / d["total"] * 100, 1) if d["total"] > 0 else 0.0

    # 연속 손실 (최근부터)
    consecutive_losses = 0
    for t in reversed(sells):
        if t.get("result") == "loss":
            consecutive_losses += 1
        else:
            break

    return {
        "period": label,
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "total_pnl": round(total_pnl),
        "avg_pnl_per_trade": avg_pnl,
        "best_trade": _brief(best),
        "worst_trade": _brief(worst),
        "avg_holding_days": avg_hold,
        "grade_accuracy": grade_acc,
        "consecutive_losses": consecutive_losses,
        "trades": sells,
    }

def load_consensus_cache() -> dict:
    """consensus_cache.json 로드. 없으면 {} 반환."""
    return load_json(CONSENSUS_CACHE_FILE, {})


def load_sector_flow_cache() -> dict:
    """sector_flow_cache.json 로드. 없으면 {} 반환."""
    return load_json(SECTOR_FLOW_CACHE_FILE, {})


def save_sector_flow_cache(data: dict):
    save_json(SECTOR_FLOW_CACHE_FILE, data)


def load_compare_log():
    return load_json(COMPARE_LOG_FILE, [])

def load_watchlist_log() -> list:
    return load_json(WATCHLIST_LOG_FILE, [])

def append_watchlist_log(entry: dict):
    log = load_watchlist_log()
    log.append(entry)
    if len(log) > 200:
        log = log[-200:]
    save_json(WATCHLIST_LOG_FILE, log)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# FnGuide 컨센서스
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _recom_label(code) -> str:
    """RECOM_CD 숫자 → 투자의견 한글"""
    try:
        v = float(str(code).strip())
        if v >= 4.0: return "매수"
        if v >= 3.0: return "중립매수"
        if v >= 2.0: return "중립"
        return "매도"
    except Exception:
        return str(code)

def fetch_fnguide_consensus(ticker: str) -> dict:
    """
    FnGuide 컨센서스 JSON API로 증권사 목표주가/투자의견 조회.
    ticker: 6자리 한국 종목코드 (예: '009540')
    반환: {ticker, name, consensus_target, opinion, reports, updated}
    실패 시 빈 결과 반환 (예외 없음).
    """
    import requests as _req
    import json as _json

    empty = {
        "ticker": ticker, "name": "", "error": "데이터 없음",
        "consensus_target": {"avg": 0, "high": 0, "low": 0},
        "opinion": {"buy": 0, "hold": 0, "sell": 0},
        "reports": [], "updated": "",
    }

    try:
        gicode = f"A{ticker}"
        base   = "https://comp.fnguide.com"
        referer = f"{base}/SVO2/ASP/SVD_Consensus.asp?pGB=1&gicode={gicode}"
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Referer": referer,
        }

        # 1. 세션 열기 (쿠키 획득)
        sess = _req.Session()
        sess.get(referer, headers=hdrs, timeout=10)

        # 2. 증권사별 목표주가 JSON (03_A{ticker}.json)
        r3 = sess.get(
            f"{base}/SVO2/json/data/01_06/03_{gicode}.json",
            headers=hdrs, timeout=10,
        )
        if r3.status_code != 200 or len(r3.content) < 50:
            return empty

        data3 = _json.loads(r3.content.decode("utf-8-sig"))
        rows = data3.get("comp", [])
        if not rows:
            return empty

        # 종목명 (04_ 파일에서 가져옴)
        stock_name = ""

        # 3. 최근 리포트 JSON (04_A{ticker}.json)
        reports = []
        r4 = sess.get(
            f"{base}/SVO2/json/data/01_06/04_{gicode}.json",
            headers=hdrs, timeout=10,
        )
        if r4.status_code == 200 and len(r4.content) > 50:
            data4 = _json.loads(r4.content.decode("utf-8-sig"))
            for item in data4.get("comp", []):
                stock_name = stock_name or item.get("CO_NM", "")
                tp_raw = item.get("TARGET_PRC", "").strip()
                try:
                    tp = int(tp_raw.replace(",", ""))
                except Exception:
                    tp = 0
                rec = item.get("RECOMMEND", "").upper()
                if rec in ("BUY", "STRONG BUY"):
                    opinion_str = "매수"
                elif rec in ("HOLD", "NEUTRAL", "OUTPERFORM"):
                    opinion_str = "중립"
                elif rec == "SELL":
                    opinion_str = "매도"
                else:
                    opinion_str = rec
                dt_raw = item.get("BULLET_DT", "")
                dt = f"{dt_raw[:4]}-{dt_raw[4:6]}-{dt_raw[6:]}" if len(dt_raw) == 8 else dt_raw
                reports.append({
                    "broker":  item.get("OFFER_INST_NM", ""),
                    "date":    dt,
                    "target":  tp,
                    "opinion": opinion_str,
                    "title":   item.get("TITLE", ""),
                })

        # 4. 증권사별 최신 목표주가 집계 (03_ 기반)
        inst_reports = []
        prices = []
        buy_cnt = hold_cnt = sell_cnt = 0
        avg_prc = 0
        updated = ""

        for row in rows:
            tp_raw = row.get("TARGET_PRC", "").strip()
            try:
                tp = int(tp_raw.replace(",", ""))
            except Exception:
                tp = 0
            if not avg_prc:
                try:
                    avg_prc = int(row.get("AVG_PRC", "0").replace(",", ""))
                except Exception:
                    pass
            recom = _recom_label(row.get("RECOM_CD", ""))
            if recom == "매수":       buy_cnt  += 1
            elif recom == "중립매수": hold_cnt += 1
            elif recom == "중립":     hold_cnt += 1
            else:                     sell_cnt += 1
            dt = row.get("EST_DT", "").replace("/", "-")
            if not updated or dt > updated:
                updated = dt
            if tp > 0:
                prices.append(tp)
            inst_reports.append({
                "broker":  row.get("INST_NM", ""),
                "date":    dt,
                "target":  tp,
                "opinion": recom,
            })

        high = max(prices) if prices else 0
        low  = min(prices) if prices else 0
        avg  = avg_prc or (sum(prices) // len(prices) if prices else 0)

        return {
            "ticker":           ticker,
            "name":             stock_name,
            "consensus_target": {"avg": avg, "high": high, "low": low},
            "opinion":          {"buy": buy_cnt, "hold": hold_cnt, "sell": sell_cnt},
            "reports":          reports,          # 04_: 최근 리포트 (제목+요약 포함)
            "broker_targets":   inst_reports,     # 03_: 증권사별 최신 목표가
            "updated":          updated,
        }

    except Exception as e:
        empty["error"] = str(e)
        return empty


def get_us_consensus(ticker: str) -> dict | None:
    """Nasdaq.com API로 미국 주식 애널리스트 1년 목표주가 조회.
    반환: {ticker, name, consensus_target:{avg}, recommendation}
    데이터 없거나 실패 시 None 반환.
    """
    import requests as _req, re as _re
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nasdaq.com/",
    }
    try:
        ticker = ticker.upper()
        # 1. summary: OneYrTarget (1년 목표주가)
        rs = _req.get(
            f"https://api.nasdaq.com/api/quote/{ticker}/summary?assetclass=stocks",
            headers=hdrs, timeout=8,
        )
        if rs.status_code != 200:
            return None
        summary = rs.json().get("data", {})
        target_raw = (summary.get("summaryData") or {}).get("OneYrTarget", {}).get("value", "")
        if not target_raw or target_raw == "N/A":
            return None
        avg = float(_re.sub(r"[^\d.]", "", target_raw))

        # 2. info: companyName
        ri = _req.get(
            f"https://api.nasdaq.com/api/quote/{ticker}/info?assetclass=stocks",
            headers=hdrs, timeout=8,
        )
        name = ticker
        if ri.status_code == 200:
            raw_name = (ri.json().get("data") or {}).get("companyName", ticker)
            # " Common Stock" 등 suffix 제거
            name = _re.sub(r"\s+(Common Stock|Common Shares?|Inc\.|Corp\.|Ltd\.?)\s*$", "", raw_name, flags=_re.I).strip() or raw_name

        return {
            "ticker":           ticker,
            "name":             name,
            "consensus_target": {"avg": avg, "high": 0, "low": 0},
            "recommendation":   "N/A",
        }
    except Exception:
        return None


async def update_consensus_cache() -> dict:
    """포트폴리오+워치리스트 전체 컨센서스를 배치 수집해 consensus_cache.json에 저장.
    기존 avg는 prev_avg로 보존해 주간 변동 추적 가능.
    실패 종목은 기존 캐시 유지."""
    import asyncio as _aio
    old_cache = load_json(CONSENSUS_CACHE_FILE, {})
    old_kr = old_cache.get("kr", {})
    old_us = old_cache.get("us", {})

    # 수집 대상 티커
    portfolio = load_json(PORTFOLIO_FILE, {})
    kr_tickers: dict = {
        t: (v.get("name", t) if isinstance(v, dict) else t)
        for t, v in portfolio.items()
        if t != "us_stocks" and not _is_us_ticker(t)
    }
    us_tickers: dict = {
        t: (v.get("name", t) if isinstance(v, dict) else t)
        for t, v in portfolio.get("us_stocks", {}).items()
    }
    # 한국 워치리스트 추가
    for t, n in load_json(WATCHLIST_FILE, {}).items():
        if t not in kr_tickers and not _is_us_ticker(t):
            kr_tickers[t] = n
    # 미국 워치리스트 추가
    for t, v in load_json(US_WATCHLIST_FILE, {}).items():
        if t not in us_tickers:
            us_tickers[t] = v.get("name", t) if isinstance(v, dict) else str(v)

    loop = _aio.get_event_loop()

    # 한국 컨센서스 (FnGuide, 동기 → executor)
    new_kr: dict = {}
    for ticker in kr_tickers:
        try:
            c = await _aio.wait_for(
                loop.run_in_executor(None, fetch_fnguide_consensus, ticker),
                timeout=10.0,
            )
            avg = int((c.get("consensus_target") or {}).get("avg", 0)) if c else 0
            if avg:
                old_entry = old_kr.get(ticker, {})
                old_avg   = old_entry.get("avg")
                entry = {
                    "name": c.get("name") or kr_tickers.get(ticker, ticker),
                    "avg":  avg,
                    "high": int((c.get("consensus_target") or {}).get("high", 0)),
                    "low":  int((c.get("consensus_target") or {}).get("low",  0)),
                    "buy":  int((c.get("opinion") or {}).get("buy",  0)),
                    "hold": int((c.get("opinion") or {}).get("hold", 0)),
                    "sell": int((c.get("opinion") or {}).get("sell", 0)),
                }
                if old_avg and int(old_avg) != avg:
                    entry["prev_avg"] = old_avg
                elif old_avg:
                    entry["prev_avg"] = old_entry.get("prev_avg")
                new_kr[ticker] = entry
            elif ticker in old_kr:
                new_kr[ticker] = old_kr[ticker]
        except Exception as _e:
            print(f"[consensus_cache] KR {ticker} 실패: {_e}")
            if ticker in old_kr:
                new_kr[ticker] = old_kr[ticker]
        await _aio.sleep(0.5)

    # 미국 컨센서스 (Nasdaq.com, 동기 → executor)
    new_us: dict = {}
    for ticker in us_tickers:
        try:
            c = await _aio.wait_for(
                loop.run_in_executor(None, get_us_consensus, ticker),
                timeout=10.0,
            )
            avg = float((c.get("consensus_target") or {}).get("avg", 0)) if c else 0.0
            if avg:
                old_entry = old_us.get(ticker, {})
                old_avg   = old_entry.get("avg")
                entry = {
                    "name": c.get("name", ticker),
                    "avg":  round(avg, 2),
                }
                if old_avg and round(float(old_avg), 2) != round(avg, 2):
                    entry["prev_avg"] = old_avg
                elif old_avg:
                    entry["prev_avg"] = old_entry.get("prev_avg")
                new_us[ticker] = entry
            elif ticker in old_us:
                new_us[ticker] = old_us[ticker]
        except Exception as _e:
            print(f"[consensus_cache] US {ticker} 실패: {_e}")
            if ticker in old_us:
                new_us[ticker] = old_us[ticker]
        await _aio.sleep(0.5)

    cache = {
        "updated": datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kr": new_kr,
        "us": new_us,
    }
    save_json(CONSENSUS_CACHE_FILE, cache)
    print(f"[consensus_cache] 저장 완료: KR {len(new_kr)}종목, US {len(new_us)}종목")
    return cache


async def save_portfolio_snapshot(token: str) -> dict:
    """장마감 후 포트폴리오 스냅샷 저장 (/data/portfolio_history.json).
    KR: KIS 배치조회 / US: KIS 해외현재가 / 현금: portfolio.json의 cash_krw, cash_usd"""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    portfolio = load_json(PORTFOLIO_FILE, {})
    kr_stocks = {k: v for k, v in portfolio.items()
                 if k != "us_stocks" and not _is_us_ticker(k) and isinstance(v, dict)}
    us_stocks  = portfolio.get("us_stocks", {})
    cash_krw   = float(portfolio.get("cash_krw", 0) or 0)
    cash_usd   = float(portfolio.get("cash_usd", 0) or 0)

    # USD/KRW 환율
    try:
        fx = await get_yahoo_quote("KRW=X")
        usd_krw = float(fx.get("price", 1300) or 1300) if fx else 1300.0
    except Exception:
        usd_krw = 1300.0

    # KR 평가 (배치 조회)
    kr_eval = 0.0
    holdings: dict = {}
    if kr_stocks:
        batch = await batch_stock_detail(list(kr_stocks.keys()), token, delay=0.2)
        for row in batch:
            ticker = row.get("ticker", "")
            if row.get("error") or not ticker:
                continue
            price = row.get("price", 0)
            qty   = kr_stocks.get(ticker, {}).get("qty", 0)
            eval_amt = price * qty
            kr_eval += eval_amt
            holdings[ticker] = {"price": price, "qty": qty, "eval": int(eval_amt)}

    # US 평가
    us_eval_usd = 0.0
    for sym, info in us_stocks.items():
        try:
            d = await _fetch_us_price_simple(sym, token)
            price = float(d.get("last", 0) or 0)
            qty   = info.get("qty", 0)
            eval_usd = round(price * qty, 2)
            us_eval_usd += eval_usd
            holdings[sym] = {"price": price, "qty": qty, "eval_usd": eval_usd}
            await asyncio.sleep(0.2)
        except Exception:
            pass

    us_eval_krw   = us_eval_usd * usd_krw
    cash_usd_krw  = cash_usd * usd_krw
    total_eval_krw  = int(kr_eval + us_eval_krw)
    total_asset_krw = int(kr_eval + us_eval_krw + cash_krw + cash_usd_krw)

    # 비중 계산
    for ticker, h in holdings.items():
        ev = h.get("eval", 0) or (h.get("eval_usd", 0) * usd_krw)
        h["weight_pct"] = round(ev / total_asset_krw * 100, 1) if total_asset_krw > 0 else 0.0

    cash_weight_pct = round((cash_krw + cash_usd_krw) / total_asset_krw * 100, 1) if total_asset_krw > 0 else 0.0

    snapshot = {
        "date": today,
        "total_eval_krw": total_eval_krw,
        "cash_krw": int(cash_krw),
        "cash_usd": round(cash_usd, 2),
        "usd_krw_rate": round(usd_krw, 1),
        "total_asset_krw": total_asset_krw,
        "kr_eval": int(kr_eval),
        "us_eval_krw": int(us_eval_krw),
        "holdings": holdings,
        "cash_weight_pct": cash_weight_pct,
    }

    history = load_json(PORTFOLIO_HISTORY_FILE, {"snapshots": []})
    snaps = [s for s in history.get("snapshots", []) if s.get("date") != today]
    snaps.append(snapshot)
    snaps = sorted(snaps, key=lambda x: x.get("date", ""))
    if len(snaps) > 365:
        snaps = snaps[-365:]
    save_json(PORTFOLIO_HISTORY_FILE, {"snapshots": snaps})
    print(f"[snapshot] 저장: {today}, 총자산 {total_asset_krw:,}원")
    return snapshot


async def _fetch_us_price_simple(sym: str, token: str) -> dict:
    """해외 현재가 단순 조회 (save_portfolio_snapshot 전용)"""
    async with aiohttp.ClientSession() as s:
        excd = _guess_excd(sym)
        _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price",
            "HHDFS00000300", token, {"AUTH": "", "EXCD": excd, "SYMB": sym})
        return d.get("output", {})


def check_drawdown() -> dict:
    """portfolio_history.json 기반 드로다운·주간/월간 수익률 분석 + 투자규칙 경고.
    스냅샷 부족 시 해당 지표는 None."""
    history = load_json(PORTFOLIO_HISTORY_FILE, {"snapshots": []})
    snaps = sorted(history.get("snapshots", []), key=lambda x: x.get("date", ""))

    def _total(s):
        return s.get("total_asset_krw") or s.get("total_eval_krw") or 0

    weekly_return = monthly_return = monthly_max_dd = None

    if len(snaps) >= 2:
        today_total = _total(snaps[-1])
        if len(snaps) >= 6:
            week_total = _total(snaps[-6])
            if week_total > 0:
                weekly_return = round((today_total - week_total) / week_total * 100, 2)
        if len(snaps) >= 21:
            month_total = _total(snaps[-21])
            if month_total > 0:
                monthly_return = round((today_total - month_total) / month_total * 100, 2)
            month_highs = [_total(s) for s in snaps[-21:] if _total(s) > 0]
            if month_highs:
                peak = max(month_highs)
                monthly_max_dd = round((today_total - peak) / peak * 100, 2) if peak > 0 else None
    else:
        today_total = 0

    alerts = []
    if weekly_return is not None and weekly_return <= -4:
        alerts.append({"level": "WARNING",
                        "message": f"주간 손실 {weekly_return:.1f}% > -4% 한도. 이번 주 신규매수 금지"})
    if monthly_max_dd is not None and monthly_max_dd <= -7:
        alerts.append({"level": "CRITICAL",
                        "message": f"월간 드로다운 {monthly_max_dd:.1f}% > -7% 한도. 신규매수 중단 + 포트 점검 필요"})
    elif monthly_return is not None and monthly_return <= -7:
        alerts.append({"level": "CRITICAL",
                        "message": f"월간 수익률 {monthly_return:.1f}% > -7% 한도. 신규매수 중단 + 포트 점검 필요"})

    # 연속 손절 카운트 (decision_log actions 에서 매도/정리/손절 키워드)
    consecutive_stops = 0
    try:
        dec_log = load_decision_log()
        entries = sorted(dec_log.values(), key=lambda x: x.get("date", ""), reverse=True)
        for entry in entries[:10]:
            actions_text = " ".join(entry.get("actions", []))
            if any(kw in actions_text for kw in ["매도", "정리", "손절"]):
                consecutive_stops += 1
            else:
                break
    except Exception:
        pass

    if consecutive_stops >= 3:
        alerts.append({"level": "CRITICAL",
                        "message": f"연속 손절 {consecutive_stops}회. 48시간 매매 중단 권고"})

    cash_weight = snaps[-1].get("cash_weight_pct") if snaps else None

    return {
        "snapshot_count": len(snaps),
        "weekly_return_pct": weekly_return,
        "monthly_return_pct": monthly_return,
        "monthly_max_drawdown_pct": monthly_max_dd,
        "consecutive_stops": consecutive_stops,
        "trading_suspended": consecutive_stops >= 3,
        "cash_weight_pct": cash_weight,
        "alerts": alerts,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KIS API
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def get_kis_token():
    now = datetime.now()
    if _token_cache["token"] and _token_cache["expires"] and _token_cache["expires"] > now:
        return _token_cache["token"]
    url = f"{KIS_BASE_URL}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers={"content-type": "application/json"}, json=body) as resp:
            data = await resp.json()
            token = data.get("access_token")
            if token:
                _token_cache["token"] = token
                _token_cache["expires"] = now + timedelta(hours=20)
            return token


async def get_stock_price(ticker, token):
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHKST01010100"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker}) as resp:
            return (await resp.json()).get("output", {})


async def get_investor_trend(ticker, token):
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHKST01010900"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}) as resp:
            return (await resp.json()).get("output", [])


async def get_volume_rank(token):
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHPST01710000"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20101",
        "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "0", "FID_INPUT_PRICE_2": "0",
        "FID_VOL_CNT": "0", "FID_INPUT_DATE_1": ""
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            return (await resp.json()).get("output", [])


async def get_kis_index(token, index_code="0001"):
    """KIS API로 KOSPI/KOSDAQ 지수 조회 (0001=KOSPI, 1001=KOSDAQ)"""
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHPUP02100000"
    }
    params = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": index_code}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            return (await resp.json()).get("output", {})


def _kis_headers(token, tr_id):
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
    }


async def _kis_get(session, path, tr_id, token, params):
    """KIS API GET 호출 (429/5xx 자동 재시도, 공유 세션 fallback)."""
    s = session if session and not getattr(session, 'closed', False) else _get_session()
    url = f"{KIS_BASE_URL}{path}"
    headers = _kis_headers(token, tr_id)
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        async with s.get(url, headers=headers, params=params) as r:
            if r.status == 429 and attempt < max_retries:
                print(f"[RETRY] {path} → 429, attempt {attempt}/{max_retries}")
                await asyncio.sleep(1.0 * attempt)
                continue
            if r.status in (500, 502, 503) and attempt < max_retries:
                print(f"[RETRY] {path} → {r.status}, attempt {attempt}/{max_retries}")
                await asyncio.sleep(2.0)
                continue
            data = await r.json(content_type=None)
            return r.status, data
    return 500, {}


async def kis_stock_price(ticker, token):
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        return d.get("output", {})


async def kis_stock_info(ticker, token):
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/search-stock-info",
            "CTPF1002R", token,
            {"PRDT_TYPE_CD": "300", "PDNO": ticker})
        return d.get("output", {})


async def kis_investor_trend(ticker, token):
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        return d.get("output", [])


async def kis_credit_balance(ticker, token):
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-credit-by-company",
            "FHKST01010600", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        return d.get("output", {})


async def kis_short_selling(ticker, token):
    today = datetime.now().strftime("%Y%m%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-short-selling",
            "FHKST01010700", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker,
             "fid_begin_dt": week_ago, "fid_end_dt": today})
        return d.get("output", [])


async def kis_volume_rank_api(token):
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/volume-rank",
            "FHPST01710000", token,
            {"fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20171",
             "fid_input_iscd": "0000", "fid_div_cls_code": "0", "fid_blng_cls_code": "0",
             "fid_trgt_cls_code": "111111111", "fid_trgt_exls_cls_code": "000000",
             "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "", "fid_input_date_1": ""})
        return d.get("output", [])


async def kis_foreigner_trend(token):
    today = datetime.now().strftime("%Y%m%d")
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-foreigner-trend",
            "FHPTJ04060100", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": "0000", "fid_input_date_1": today})
        if not d:
            return []
        output = d.get("output") or []
        return [r for r in output if r is not None]


async def kis_sector_price(token):
    today = datetime.now().strftime("%Y%m%d")
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-daily-sector-price",
            "FHKUP03500100", token,
            {"fid_cond_mrkt_div_code": "U", "fid_input_iscd": "0001",
             "fid_input_date_1": today, "fid_period_div_code": "D"})
        return d.get("output", [])


WI26_SECTORS = [
    ("001", "반도체"), ("004", "조선"),   ("006", "전력기기"),
    ("007", "방산"),   ("010", "2차전지"), ("012", "건설"),
    ("021", "바이오"),
]

# 외국인 순매수 상위 fallback용 티커→업종 매핑
_TICKER_SECTOR = {
    "005930": "반도체", "000660": "반도체", "012510": "반도체", "042700": "반도체",
    "009540": "조선",   "042660": "조선",   "010140": "조선",   "267250": "조선",
    "012510": "전력기기","028260": "전력기기","267260": "전력기기","298040": "전력기기",
    "012450": "방산",   "047810": "방산",   "329180": "방산",   "272210": "방산",
    "006400": "2차전지","051910": "2차전지","373220": "2차전지","247540": "2차전지",
    "000720": "건설",   "097950": "건설",   "047040": "건설",   "028260": "건설",
    "207940": "바이오", "068270": "바이오", "196170": "바이오", "091990": "바이오",
}


async def _fetch_sector_flow(token: str, sector_code: str) -> tuple:
    """업종 외국인+기관 순매수금액(백만원) 반환. 실패 시 (0, 0)."""
    today = datetime.now().strftime("%Y%m%d")
    params = {
        "fid_cond_mrkt_div_code": "U",
        "fid_input_iscd": sector_code,
        "fid_input_date_1": today,
        "fid_period_div_code": "D",
    }
    for path in [
        "/uapi/domestic-stock/v1/quotations/inquire-member-daily-by-group",
        "/uapi/domestic-stock/v1/quotations/inquire-daily-sector-price",
    ]:
        try:
            async with aiohttp.ClientSession() as s:
                _, d = await _kis_get(s, path, "FHKUP03500100", token, params)
            if not d or d.get("rt_cd") != "0":
                continue
            out = d.get("output2") or d.get("output") or {}
            if isinstance(out, list):
                out = out[0] if out else {}
            frgn = int(out.get("frgn_ntby_tr_pbmn", 0) or 0)
            orgn = int(out.get("orgn_ntby_tr_pbmn", 0) or 0)
            if frgn != 0 or orgn != 0:
                return frgn, orgn
        except Exception:
            continue
    return 0, 0


async def detect_sector_rotation(token: str) -> dict:
    """WI26 업종별 외인+기관 순매수 수집 → 전일 대비 자금 이동 감지.
    Returns: {sectors: [{name, frgn, orgn, total, prev_total, change}],
             rotations: ["반도체→전력기기", ...], date: str}
    """
    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 오늘 업종별 수급 수집
    today_data = {}
    for code, name in WI26_SECTORS:
        try:
            frgn, orgn = await _fetch_sector_flow(token, code)
            today_data[name] = {"frgn": frgn, "orgn": orgn, "total": frgn + orgn}
            await asyncio.sleep(0.3)
        except Exception:
            today_data[name] = {"frgn": 0, "orgn": 0, "total": 0}

    # 전일 데이터 로드
    prev = load_json(SECTOR_ROTATION_FILE, {})
    prev_data = prev.get("sectors", {})
    prev_date = prev.get("date", "")

    # 변화량 계산
    sectors = []
    for name, cur in today_data.items():
        prev_total = prev_data.get(name, {}).get("total", 0)
        change = cur["total"] - prev_total if prev_date and prev_date != today else 0
        sectors.append({
            "name": name,
            "frgn": cur["frgn"],
            "orgn": cur["orgn"],
            "total": cur["total"],
            "prev_total": prev_total,
            "change": change,
        })

    # 유입/유출 상위 감지 → 로테이션 패턴
    sectors.sort(key=lambda x: x["change"], reverse=True)
    inflow = [s for s in sectors if s["change"] > 0]
    outflow = [s for s in sectors if s["change"] < 0]

    rotations = []
    for out_s in outflow[:2]:
        for in_s in inflow[:2]:
            if abs(out_s["change"]) > 100 and abs(in_s["change"]) > 100:
                rotations.append(f"{out_s['name']}→{in_s['name']}")

    # 오늘 데이터 저장 (내일 비교용)
    save_json(SECTOR_ROTATION_FILE, {"date": today, "sectors": today_data})

    return {
        "date": today,
        "prev_date": prev_date,
        "sectors": sectors,
        "rotations": rotations,
        "top_inflow": inflow[:3] if inflow else [],
        "top_outflow": outflow[:3] if outflow else [],
    }


async def kis_us_stock_price(symbol: str, token: str, excd: str = "") -> dict:
    """KIS API 해외주식 현재가 (HHDFS00000300). 거래소 코드 자동 fallback."""
    if not excd:
        excd = _guess_excd(symbol)
    # 1차 시도
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price",
            "HHDFS00000300", token,
            {"AUTH": "", "EXCD": excd, "SYMB": symbol})
        out = d.get("output", {})
        price = float(out.get("last", 0) or 0)
        if price > 0:
            return out
        # 2차: 다른 거래소로 fallback
        fallback_codes = [c for c in ("NYS", "NAS", "AMS") if c != excd]
        for fb in fallback_codes:
            await asyncio.sleep(0.2)
            _, d2 = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price",
                "HHDFS00000300", token,
                {"AUTH": "", "EXCD": fb, "SYMB": symbol})
            out2 = d2.get("output", {})
            p2 = float(out2.get("last", 0) or 0)
            if p2 > 0:
                print(f"[excd fallback] {symbol}: {excd}→{fb} 성공")
                return out2
        return out  # 모든 거래소에서 0이면 원래 결과 반환


async def kis_us_stock_detail(symbol: str, token: str, excd: str = "") -> dict:
    """KIS API 해외주식 현재가상세 (HHDFS76200200) — PER/PBR/시총/52주 등"""
    if not excd:
        excd = _guess_excd(symbol)
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price-detail",
            "HHDFS76200200", token,
            {"AUTH": "", "EXCD": excd, "SYMB": symbol})
        return d.get("output", {})


async def kis_fluctuation_rank(token: str, market: str = "0000",
                              sort: str = "rise", n: int = 20) -> list:
    """등락률 순위 조회 (FHPST01700000).

    market: "0000"=전체, "0001"=KOSPI, "1001"=KOSDAQ
    sort: "rise"=상승률 상위, "fall"=하락률 상위
    Returns: [{ticker, name, price, chg_pct, volume}, ...]
    """
    # 등락 필터: rise=양수 구간, fall=음수 구간
    rate1, rate2 = ("0", "") if sort == "rise" else ("", "0")
    hdrs = _kis_headers(token, "FHPST01700000")
    params = {
        "fid_rsfl_rate2":         rate2,
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code":  "20170",
        "fid_input_iscd":         market,
        "fid_rank_sort_cls_code": "0000",
        "fid_input_cnt_1":        str(min(n, 30)),
        "fid_prc_cls_code":       "0",
        "fid_input_price_1":      "1000",
        "fid_input_price_2":      "",
        "fid_vol_cnt":            "",
        "fid_trgt_cls_code":      "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code":       "0",
        "fid_rsfl_rate1":         rate1,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/ranking/fluctuation",
                             headers=hdrs, params=params) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        print(f"[kis_fluctuation_rank] 오류: {e}")
        return []

    result = []
    for item in data.get("output", [])[:n]:
        ticker = (item.get("mksc_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "ticker":  ticker,
            "name":    (item.get("hts_kor_isnm") or "").strip(),
            "price":   int(item.get("stck_prpr", 0) or 0),
            "chg_pct": float(item.get("prdy_ctrt", 0) or 0),
            "volume":  int(item.get("acml_vol", 0) or 0),
        })
    # fall 모드: 하락률 큰 순(음수 방향) 정렬
    if sort == "fall":
        result.sort(key=lambda x: x["chg_pct"])
    return result


async def kis_investor_trend_history(ticker: str, token: str, n_days: int = 5) -> list:
    """종목별 투자자 일별 수급 히스토리 (FHPTJ04160001).

    Returns: [{date, foreign_net, institution_net, individual_net,
               foreign_buy, foreign_sell}, ...] 최신순, 최대 n_days일
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
            "FHPTJ04160001", token,
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         ticker,
                "FID_INPUT_DATE_1":       today,
                "FID_ORG_ADJ_PRC":        "",
                "FID_ETC_CLS_CODE":       "",
            })
    rows = d.get("output1") if d else None
    if not isinstance(rows, list):
        rows = []
    result = []
    for row in rows[:n_days]:
        result.append({
            "date":            row.get("stck_bsop_date", ""),
            "foreign_net":     int(row.get("frgn_ntby_qty",  0) or 0),
            "institution_net": int(row.get("orgn_ntby_qty",  0) or 0),
            "individual_net":  int(row.get("prsn_ntby_qty",  0) or 0),
            "foreign_buy":     int(row.get("frgn_shnu_vol",  0) or 0),
            "foreign_sell":    int(row.get("frgn_seln_vol",  0) or 0),
        })
    return result


async def save_supply_snapshot(token: str):
    """보유+감시 종목의 외인/기관 수급을 /data/supply_history.json에 일별 저장.
    구조: {ticker: [{date, foreign_net, institution_net}, ...]}
    3개월 후 수급 기반 백테스트 정밀화 가능."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    history = load_json(SUPPLY_HISTORY_FILE, {})

    portfolio = load_json(PORTFOLIO_FILE, {})
    wl = load_json(WATCHLIST_FILE, {})
    tickers = {}
    for t, v in portfolio.items():
        if t not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict):
            tickers[t] = True
    for t in wl:
        tickers[t] = True

    for ticker_code in tickers:
        if _is_us_ticker(ticker_code):
            continue  # 국내만
        try:
            hist = await kis_investor_trend_history(ticker_code, token, n_days=1)
            if hist:
                entry = {"date": today, "foreign_net": hist[0]["foreign_net"],
                         "institution_net": hist[0]["institution_net"]}
                if ticker_code not in history:
                    history[ticker_code] = []
                # 중복 방지
                if not history[ticker_code] or history[ticker_code][-1].get("date") != today:
                    history[ticker_code].append(entry)
                    # 최대 180일 보관
                    history[ticker_code] = history[ticker_code][-180:]
            await asyncio.sleep(0.3)
        except Exception:
            pass

    save_json(SUPPLY_HISTORY_FILE, history)
    print(f"[supply_snapshot] {len(tickers)}종목 수급 저장 완료")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 장기 일봉 / 수급 데이터 (FDR · yfinance · KRX)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def get_historical_ohlcv(ticker: str, years: int = 3) -> list:
    """FinanceDataReader(한국) / yfinance(미국)로 장기 일봉 OHLCV 조회.
    Returns: [{"date": "YYYYMMDD", "open": ..., "high": ..., "low": ..., "close": ..., "vol": int}, ...]
    시간순(오래된→최신) 정렬. 동기 함수 — run_in_executor로 호출할 것.
    """
    end_dt = datetime.now(KST)
    start_dt = end_dt - timedelta(days=years * 365)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    is_us = _is_us_ticker(ticker)

    if is_us:
        try:
            import yfinance as yf
            df = yf.download(ticker, start=start_str, end=end_str, progress=False, auto_adjust=True)
            if df is None or df.empty:
                return []
            # yfinance >=1.2 returns MultiIndex columns for single ticker
            if isinstance(df.columns, __import__('pandas').MultiIndex):
                df.columns = df.columns.droplevel("Ticker")
            result = []
            for idx, row in df.iterrows():
                dt_str = idx.strftime("%Y%m%d")
                result.append({
                    "date": dt_str,
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "vol": int(row["Volume"]),
                })
            return result
        except Exception as e:
            print(f"[get_historical_ohlcv] yfinance 오류 ({ticker}): {e}")
            return []
    else:
        try:
            import FinanceDataReader as fdr
            df = fdr.DataReader(ticker, start_str, end_str)
            if df is None or df.empty:
                return []
            result = []
            for idx, row in df.iterrows():
                dt_str = idx.strftime("%Y%m%d")
                result.append({
                    "date": dt_str,
                    "open": int(row.get("Open", 0) or 0),
                    "high": int(row.get("High", 0) or 0),
                    "low": int(row.get("Low", 0) or 0),
                    "close": int(row.get("Close", 0) or 0),
                    "vol": int(row.get("Volume", 0) or 0),
                })
            return result
        except Exception as e:
            print(f"[get_historical_ohlcv] FDR 오류 ({ticker}): {e}")
            return []


def compute_volume_profile(candles: list, current_price: float, bins: int = 20) -> dict:
    """일봉 데이터에서 볼륨 프로파일(매물대) 계산.
    candles: get_historical_ohlcv() 반환값 [{"close":..., "vol":...}, ...]
    """
    if not candles:
        return {"error": "일봉 데이터 없음"}

    valid = [c for c in candles if c.get("close") and c.get("vol")]
    if not valid:
        return {"error": "종가 데이터 없음"}

    all_lows = [c.get("low", c["close"]) for c in valid]
    all_highs = [c.get("high", c["close"]) for c in valid]
    closes = [c["close"] for c in valid]
    volumes = [c["vol"] for c in valid]

    price_low = min(all_lows)
    price_high = max(all_highs)
    if price_high == price_low:
        price_high = price_low * 1.01 if price_low else 1  # avoid zero-division

    bin_size = (price_high - price_low) / bins
    total_volume = sum(volumes)

    # Build bins
    bin_list = []
    for i in range(bins):
        b_low = price_low + i * bin_size
        b_high = price_low + (i + 1) * bin_size
        b_mid = (b_low + b_high) / 2
        bin_list.append({
            "price_low": round(b_low, 2),
            "price_high": round(b_high, 2),
            "price_mid": round(b_mid, 2),
            "volume": 0,
        })

    # Assign volumes to bins (distribute across low~high range)
    for c in valid:
        c_low = c.get("low", c["close"])
        c_high = c.get("high", c["close"])
        vol = c["vol"]
        idx_lo = max(0, min(int((c_low - price_low) / bin_size), bins - 1))
        idx_hi = max(0, min(int((c_high - price_low) / bin_size), bins - 1))
        span = idx_hi - idx_lo + 1
        per_bin = vol / span
        for i in range(idx_lo, idx_hi + 1):
            bin_list[i]["volume"] += int(per_bin)

    # Calculate volume_pct and bar
    max_vol = max(b["volume"] for b in bin_list) or 1
    for b in bin_list:
        b["volume_pct"] = round(b["volume"] / total_volume * 100, 2) if total_volume else 0
        filled = int(round(b["volume"] / max_vol * 10))
        b["bar"] = "\u2588" * filled + "\u2591" * (10 - filled)

    # POC (Point of Control)
    poc_idx = max(range(bins), key=lambda i: bin_list[i]["volume"])
    poc = bin_list[poc_idx]["price_mid"]
    poc_volume_pct = bin_list[poc_idx]["volume_pct"]

    # Value Area (70% of total volume, expand from POC)
    va_volume = bin_list[poc_idx]["volume"]
    va_low_idx = poc_idx
    va_high_idx = poc_idx
    target = total_volume * 0.70

    while va_volume < target:
        expand_down = bin_list[va_low_idx - 1]["volume"] if va_low_idx > 0 else -1
        expand_up = bin_list[va_high_idx + 1]["volume"] if va_high_idx < bins - 1 else -1
        if expand_down < 0 and expand_up < 0:
            break
        if expand_down >= expand_up:
            va_low_idx -= 1
            va_volume += bin_list[va_low_idx]["volume"]
        else:
            va_high_idx += 1
            va_volume += bin_list[va_high_idx]["volume"]

    value_area_low = bin_list[va_low_idx]["price_low"]
    value_area_high = bin_list[va_high_idx]["price_high"]

    # Support / Resistance levels
    support_bins = [b for b in bin_list if b["price_mid"] < current_price]
    resistance_bins = [b for b in bin_list if b["price_mid"] > current_price]
    support_levels = sorted(support_bins, key=lambda b: b["volume"], reverse=True)[:3]
    resistance_levels = sorted(resistance_bins, key=lambda b: b["volume"], reverse=True)[:3]

    # Format for output
    is_decimal = any(isinstance(c["close"], float) and c["close"] != int(c["close"]) for c in valid[:5])
    def _fmt_level(b):
        if is_decimal:
            return {"price_range": f"{b['price_low']:.2f}~{b['price_high']:.2f}",
                    "price_mid": b["price_mid"], "volume_pct": b["volume_pct"]}
        return {"price_range": f"{b['price_low']:,.0f}~{b['price_high']:,.0f}",
                "price_mid": b["price_mid"], "volume_pct": b["volume_pct"]}

    support_out = [_fmt_level(b) for b in support_levels]
    resistance_out = [_fmt_level(b) for b in resistance_levels]

    # Interpretation
    cp = current_price
    _pf = ".2f" if is_decimal else ",.0f"
    poc_diff_pct = (cp - poc) / poc * 100 if poc else 0
    interp_parts = []
    if abs(poc_diff_pct) < 2:
        interp_parts.append(f"현재가가 POC({poc:{_pf}}) 부근 → 매물대 중심에서 거래 중")
    elif poc_diff_pct > 0:
        interp_parts.append(f"현재가가 POC({poc:{_pf}}) 위 {poc_diff_pct:.1f}% → 매물 소화 후 상승 구간")
    else:
        interp_parts.append(f"현재가가 POC({poc:{_pf}}) 아래 {abs(poc_diff_pct):.1f}% → 매물대 저항 가능")

    if value_area_low <= cp <= value_area_high:
        interp_parts.append(f"Value Area({value_area_low:{_pf}}~{value_area_high:{_pf}}) 내부 위치")
    elif cp > value_area_high:
        interp_parts.append(f"Value Area({value_area_low:{_pf}}~{value_area_high:{_pf}}) 상단 돌파 → 강세")
    else:
        interp_parts.append(f"Value Area({value_area_low:{_pf}}~{value_area_high:{_pf}}) 하단 이탈 → 약세 주의")

    if support_out:
        interp_parts.append(f"주요 지지대: {support_out[0]['price_range']}")
    if resistance_out:
        interp_parts.append(f"주요 저항대: {resistance_out[0]['price_range']}")

    return {
        "total_candles": len(candles),
        "total_volume": total_volume,
        "current_price": current_price,
        "price_range": {"low": round(price_low, 2), "high": round(price_high, 2)},
        "poc": round(poc, 2),
        "poc_volume_pct": poc_volume_pct,
        "value_area": {"low": round(value_area_low, 2), "high": round(value_area_high, 2)},
        "bins": bin_list,
        "support_levels": support_out,
        "resistance_levels": resistance_out,
        "interpretation": ". ".join(interp_parts),
    }


def get_historical_supply(ticker: str, days: int = 365) -> list:
    """KRX 크롤링으로 종목별 투자자 매매동향 (외인/기관) 조회.
    Returns: [{"date": "YYYYMMDD", "foreign_net": int, "institution_net": int}, ...]
    시간순 정렬. 국내 전용 — 미국 종목은 빈 리스트. 동기 함수.
    """
    if _is_us_ticker(ticker):
        return []

    import requests as _req
    end_dt = datetime.now(KST)
    start_dt = end_dt - timedelta(days=days)

    url = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
    }
    # KRX isuCd는 'A005930' 형식 (시장구분 접두사 + 6자리)
    isu_cd = f"A{ticker}" if len(ticker) == 6 and ticker.isdigit() else ticker
    payload = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT02303",
        "locale": "ko_KR",
        "isuCd": isu_cd,
        "isuCd2": isu_cd,
        "strtDd": start_dt.strftime("%Y%m%d"),
        "endDd": end_dt.strftime("%Y%m%d"),
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
    }

    try:
        resp = _req.post(url, data=payload, headers=headers, timeout=30)
        data = resp.json()
        rows = data.get("output", [])
        result = []
        for row in rows:
            dt = row.get("TRD_DD", "").replace("/", "").replace("-", "")
            if len(dt) != 8:
                continue
            frgn = int(str(row.get("FORN_PURE_QTY", row.get("foreignNetBuy", 0)) or 0).replace(",", "") or 0)
            inst = int(str(row.get("ORGN_PURE_QTY", row.get("organNetBuy", 0)) or 0).replace(",", "") or 0)
            result.append({
                "date": dt,
                "foreign_net": frgn,
                "institution_net": inst,
            })
        result.sort(key=lambda x: x["date"])
        return result
    except Exception as e:
        print(f"[get_historical_supply] KRX 크롤링 오류 ({ticker}): {e}")
        return []


async def kis_daily_volumes(ticker: str, token: str, n: int = 21) -> list:
    """최근 n거래일 거래량 리스트 반환 (최신이 [0]). FHKST03010100 일봉 API."""
    today_str = datetime.now(KST).strftime("%Y%m%d")
    start_dt = (datetime.now(KST) - timedelta(days=n * 2)).strftime("%Y%m%d")
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100", token,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
             "FID_INPUT_DATE_1": start_dt, "FID_INPUT_DATE_2": today_str,
             "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
    candles = d.get("output2") or []
    return [int(c.get("acml_vol", 0) or 0) for c in candles[:n]]


async def check_momentum_exit(ticker: str, token: str) -> dict:
    """모멘텀 종료 복합 신호 체크 (5개 조건, 2개 이상 해당 시 warning=True).

    Returns:
        {"ticker", "conditions": [{"condition", "triggered", "detail"}],
         "triggered": [...triggered conditions...], "count": int, "warning": bool}
    """
    conditions = []

    # ── 조건 1·2·5: 수급 히스토리 ──
    try:
        hist = await kis_investor_trend_history(ticker, token, n_days=5)
        await asyncio.sleep(0.3)

        frgn_vals = [h["foreign_net"] for h in hist]
        inst_vals  = [h["institution_net"] for h in hist]

        # 조건 1: 외국인 3일 연속 순매도
        f3 = frgn_vals[:3]
        frgn_consec = len(f3) == 3 and all(x < 0 for x in f3)
        frgn_detail = "/".join(f"{x:+,}" for x in frgn_vals[:5]) if frgn_vals else "-"
        conditions.append({"condition": "외인3일연속매도", "triggered": frgn_consec, "detail": frgn_detail})

        # 조건 2: 기관 3일 연속 순매도
        i3 = inst_vals[:3]
        inst_consec = len(i3) == 3 and all(x < 0 for x in i3)
        inst_detail = "/".join(f"{x:+,}" for x in inst_vals[:5]) if inst_vals else "-"
        conditions.append({"condition": "기관3일연속매도", "triggered": inst_consec, "detail": inst_detail})

        # 조건 5: 당일 외인+기관 동시 순매도
        if hist:
            t = hist[0]
            both = t["foreign_net"] < 0 and t["institution_net"] < 0
            conditions.append({"condition": "당일외인+기관동시매도", "triggered": both,
                                "detail": f"외인{t['foreign_net']:+,} 기관{t['institution_net']:+,}"})
        else:
            conditions.append({"condition": "당일외인+기관동시매도", "triggered": False, "detail": "데이터 없음"})
    except Exception as e:
        for cond in ["외인3일연속매도", "기관3일연속매도", "당일외인+기관동시매도"]:
            conditions.append({"condition": cond, "triggered": False, "detail": f"오류: {e}"})

    # ── 조건 3: 거래량 20일 평균 대비 50% 이하 ──
    try:
        vols = await kis_daily_volumes(ticker, token, n=21)
        await asyncio.sleep(0.3)
        if len(vols) >= 21:
            today_vol = vols[0]
            avg20 = sum(vols[1:21]) / 20
            ratio = today_vol / avg20 * 100 if avg20 > 0 else 100
            conditions.append({"condition": "거래량감소(20일평균50%이하)", "triggered": ratio <= 50,
                                "detail": f"오늘{today_vol:,} 20일평균{int(avg20):,} ({ratio:.0f}%)"})
        else:
            conditions.append({"condition": "거래량감소(20일평균50%이하)", "triggered": False, "detail": "데이터 부족"})
    except Exception as e:
        conditions.append({"condition": "거래량감소(20일평균50%이하)", "triggered": False, "detail": f"오류: {e}"})

    # ── 조건 4: 52주 고점 대비 -10% 이상 하락 ──
    try:
        p = await kis_stock_price(ticker, token)
        await asyncio.sleep(0.3)
        cur = int(p.get("stck_prpr", 0) or 0)
        h52 = int(p.get("w52_hgpr", 0) or 0)
        if cur > 0 and h52 > 0:
            drop = (cur - h52) / h52 * 100
            conditions.append({"condition": "52주고점대비-10%이상", "triggered": drop <= -10,
                                "detail": f"현재{cur:,} 52주고{h52:,} ({drop:.1f}%)"})
        else:
            conditions.append({"condition": "52주고점대비-10%이상", "triggered": False, "detail": "데이터 없음"})
    except Exception as e:
        conditions.append({"condition": "52주고점대비-10%이상", "triggered": False, "detail": f"오류: {e}"})

    triggered = [c for c in conditions if c["triggered"]]
    return {
        "ticker": ticker,
        "conditions": conditions,
        "triggered": triggered,
        "count": len(triggered),
        "warning": len(triggered) >= 2,
    }


async def batch_stock_detail(tickers: list, token: str, delay: float = 0.3) -> list:
    """여러 종목을 순차 조회해 간소화된 상세 정보 리스트 반환.

    각 종목: {ticker, name, price, chg_pct, vol, w52h, w52l, per, pbr, frgn_net, inst_net}
    실패 종목: {ticker, error: "..."}
    """
    results = []
    for ticker in tickers:
        row = {"ticker": ticker}
        try:
            p = await kis_stock_price(ticker, token)
            await asyncio.sleep(delay * 0.6)
            inv = await kis_investor_trend(ticker, token)
            await asyncio.sleep(delay * 0.4)
            row.update({
                "name":     p.get("hts_kor_isnm", ticker),
                "price":    int(p.get("stck_prpr", 0) or 0),
                "chg_pct":  float(p.get("prdy_ctrt", 0) or 0),
                "vol":      int(p.get("acml_vol", 0) or 0),
                "w52h":     int(p.get("w52_hgpr", 0) or 0),
                "w52l":     int(p.get("w52_lwpr", 0) or 0),
                "per":      p.get("per"),
                "pbr":      p.get("pbr"),
                "frgn_net": int(inv[0].get("frgn_ntby_qty", 0) or 0) if inv else 0,
                "inst_net": int(inv[0].get("orgn_ntby_qty", 0) or 0) if inv else 0,
            })
        except Exception as e:
            row["error"] = str(e)
        results.append(row)
    return results


async def kis_program_trade_today(token: str, market: str = "kospi") -> list:
    """프로그램매매 투자자별 당일 동향 (HHPPG046600C1).

    market: "kospi"(1) or "kosdaq"(4)
    Returns: [{investor, total_net_qty, total_net_amt, arb_net_qty, non_arb_net_qty}, ...]
    """
    mrkt_code = "1" if market.lower() == "kospi" else "4"
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/investor-program-trade-today",
            "HHPPG046600C1", token,
            {"MRKT_DIV_CLS_CODE": mrkt_code})
    result = []
    for row in d.get("output1", []):
        name = (row.get("invr_cls_name") or "").strip()
        if not name:
            continue
        result.append({
            "investor":        name,
            "total_net_qty":   int(row.get("all_ntby_qty",  0) or 0),
            "total_net_amt":   int(row.get("all_ntby_amt",  0) or 0),
            "arb_net_qty":     int(row.get("arbt_ntby_qty", 0) or 0),
            "non_arb_net_qty": int(row.get("nabt_ntby_qty", 0) or 0),
        })
    return result


async def kis_investor_trend_estimate(ticker: str, token: str) -> dict:
    """장중 투자자 추정 수급 가집계 (HHPTJ04160200).
    외국인·기관 추정 순매수 수량 (확정치 아님, 장중 업데이트).
    Returns: {ticker, foreign_est_net, institution_est_net, sum_est_net, is_estimate: True}
    """
    try:
        async with aiohttp.ClientSession() as s:
            _, d = await _kis_get(s,
                "/uapi/domestic-stock/v1/quotations/investor-trend-estimate",
                "HHPTJ04160200", token,
                {"MKSC_SHRN_ISCD": ticker})
        rows = d.get("output2", [])
        row = rows[-1] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})
        return {
            "ticker":              ticker,
            "foreign_est_net":     int(row.get("frgn_fake_ntby_qty", 0) or 0),
            "institution_est_net": int(row.get("orgn_fake_ntby_qty", 0) or 0),
            "sum_est_net":         int(row.get("sum_fake_ntby_qty",  0) or 0),
            "is_estimate":         True,
        }
    except Exception as e:
        print(f"[kis_investor_trend_estimate] 오류: {e}")
        return {"ticker": ticker, "error": str(e)}


async def kis_foreign_institution_total(token: str, sort: str = "buy", n: int = 20) -> list:
    """외국인+기관 합산 순매수 상위 종목 가집계 (FHPTJ04400000).

    sort: "buy"=순매수 상위, "sell"=순매도 상위
    Returns: [{ticker, name, price, chg_pct, foreign_net, institution_net, fi_total_net}, ...]
    """
    rank_code = "0" if sort == "buy" else "1"
    hdrs = _kis_headers(token, "FHPTJ04400000")
    params = {
        "FID_COND_MRKT_DIV_CODE": "V",
        "FID_COND_SCR_DIV_CODE":  "16449",
        "FID_INPUT_ISCD":         "0000",
        "FID_DIV_CLS_CODE":       "0",
        "FID_RANK_SORT_CLS_CODE": rank_code,
        "FID_ETC_CLS_CODE":       "0",
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/foreign-institution-total",
                             headers=hdrs, params=params) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        print(f"[kis_foreign_institution_total] 오류: {e}")
        return []

    result = []
    for item in data.get("output", [])[:n]:
        ticker = (item.get("mksc_shrn_iscd") or "").strip()
        if not ticker:
            continue
        frgn = int(item.get("frgn_ntby_qty", 0) or 0)
        orgn = int(item.get("orgn_ntby_qty", 0) or 0)
        result.append({
            "ticker":          ticker,
            "name":            (item.get("hts_kor_isnm") or "").strip(),
            "price":           int(item.get("stck_prpr", 0) or 0),
            "chg_pct":         float(item.get("prdy_ctrt", 0) or 0),
            "foreign_net":     frgn,
            "institution_net": orgn,
            "fi_total_net":    frgn + orgn,
        })
    return result


async def kis_daily_short_sale(ticker: str, token: str, n: int = 10) -> list:
    """국내주식 공매도 일별추이 (FHPST04830000).

    Returns: [{date, short_vol, total_vol, short_ratio, close}, ...]
    날짜범위 파라미터로 조회 (페이징 없음, 범위 내 전체 반환).
    """
    try:
        today = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=int(n * 1.6))).strftime("%Y%m%d")
        async with aiohttp.ClientSession() as s:
            _, d = await _kis_get(s,
                "/uapi/domestic-stock/v1/quotations/daily-short-sale",
                "FHPST04830000", token,
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD":         ticker,
                    "FID_INPUT_DATE_1":       start,
                    "FID_INPUT_DATE_2":       today,
                })
        result = []
        for row in d.get("output2", [])[:n]:
            result.append({
                "date":        row.get("stck_bsop_date", ""),
                "short_vol":   int(row.get("ssts_cntg_qty",  0) or 0),
                "total_vol":   int(row.get("acml_vol",        0) or 0),
                "short_ratio": float(row.get("ssts_vol_rlim", 0) or 0),
                "close":       int(row.get("stck_clpr",       0) or 0),
            })
        return result
    except Exception as e:
        print(f"[kis_daily_short_sale] 오류: {e}")
        return []


async def kis_news_title(ticker: str, token: str, n: int = 10) -> list:
    """종목 관련 뉴스 제목 조회 (FHKST01011800).

    Returns: [{date, time, title, source}, ...]
    """
    try:
        async with aiohttp.ClientSession() as s:
            _, d = await _kis_get(s,
                "/uapi/domestic-stock/v1/quotations/news-title",
                "FHKST01011800", token,
                {
                    "FID_NEWS_OFER_ENTP_CODE": "",
                    "FID_COND_MRKT_CLS_CODE":  "",
                    "FID_INPUT_ISCD":          ticker,
                    "FID_TITL_CNTT":           "",
                    "FID_INPUT_DATE_1":        "",
                    "FID_INPUT_HOUR_1":        "",
                    "FID_RANK_SORT_CLS_CODE":  "0",
                    "FID_INPUT_SRNO":          "",
                })
        result = []
        for row in d.get("output", [])[:n]:
            title = (row.get("hts_pbnt_titl_cntt") or "").strip()
            if not title:
                continue
            result.append({
                "date":   row.get("data_dt", ""),
                "time":   row.get("data_tm", ""),
                "title":  title,
                "source": (row.get("dorg") or "").strip(),
            })
        return result
    except Exception as e:
        print(f"[kis_news_title] 오류: {e}")
        return []


def analyze_news_sentiment(news_items: list) -> dict:
    """뉴스 헤드라인 감성 분석.

    Returns: {positive: [...], negative: [...], neutral: [...], summary: str}
    """
    positive, negative, neutral = [], [], []
    for item in news_items:
        title = item.get("title", "")
        pos_matched = [kw for kw in _POSITIVE_KEYWORDS if kw in title]
        neg_matched = [kw for kw in _NEGATIVE_KEYWORDS if kw in title]
        entry = {**item}
        if len(pos_matched) > len(neg_matched):
            entry["sentiment"] = "positive"
            entry["matched_keywords"] = pos_matched
            positive.append(entry)
        elif len(neg_matched) > len(pos_matched):
            entry["sentiment"] = "negative"
            entry["matched_keywords"] = neg_matched
            negative.append(entry)
        else:
            entry["sentiment"] = "neutral"
            entry["matched_keywords"] = pos_matched + neg_matched
            neutral.append(entry)
    summary = f"🟢긍정 {len(positive)} / 🔴부정 {len(negative)} / ⚪중립 {len(neutral)}"
    return {"positive": positive, "negative": negative, "neutral": neutral, "summary": summary}


async def kis_vi_status(token: str) -> list:
    """변동성완화장치(VI) 발동 종목 현황 (FHPST01390000).

    Returns: [{ticker, name, vi_type, vi_price, base_price, trigger_time, release_time, count}, ...]
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    try:
        async with aiohttp.ClientSession() as s:
            _, d = await _kis_get(s,
                "/uapi/domestic-stock/v1/quotations/inquire-vi-status",
                "FHPST01390000", token,
                {
                    "FID_DIV_CLS_CODE":       "0",
                    "FID_COND_SCR_DIV_CODE":  "20139",
                    "FID_MRKT_CLS_CODE":      "0",
                    "FID_INPUT_ISCD":         "",
                    "FID_RANK_SORT_CLS_CODE": "0",
                    "FID_INPUT_DATE_1":       today,
                    "FID_TRGT_CLS_CODE":      "",
                    "FID_TRGT_EXLS_CLS_CODE": "",
                })
        result = []
        for row in d.get("output", []):
            ticker = (row.get("mksc_shrn_iscd") or "").strip()
            if not ticker:
                continue
            vi_kind = row.get("vi_kind_code", "")
            vi_type = {"1": "정적VI", "2": "동적VI", "3": "정적+동적VI"}.get(vi_kind, vi_kind)
            result.append({
                "ticker":       ticker,
                "name":         (row.get("hts_kor_isnm") or "").strip(),
                "vi_type":      vi_type,
                "vi_price":     int(row.get("vi_prc",      0) or 0),
                "base_price":   int(row.get("vi_stnd_prc", 0) or 0),
                "trigger_time": row.get("cntg_vi_hour", ""),
                "release_time": row.get("vi_cncl_hour", ""),
                "count":        int(row.get("vi_count",    0) or 0),
            })
        return result
    except Exception as e:
        print(f"[kis_vi_status] 오류: {e}")
        return []


async def kis_volume_power_rank(token: str, market: str = "all", n: int = 20) -> list:
    """체결강도 상위 종목 순위 (FHPST01680000).

    market: "all"=전체, "kospi"=코스피, "kosdaq"=코스닥
    Returns: [{ticker, name, price, chg_pct, volume_power_pct, buy_vol, sell_vol}, ...]
    """
    market_code = {"all": "0000", "kospi": "0001", "kosdaq": "1001"}.get(market.lower(), "0000")
    hdrs = _kis_headers(token, "FHPST01680000")
    params = {
        "fid_trgt_exls_cls_code": "0",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code":  "20168",
        "fid_input_iscd":         market_code,
        "fid_div_cls_code":       "0",
        "fid_input_price_1":      "",
        "fid_input_price_2":      "",
        "fid_vol_cnt":            "",
        "fid_trgt_cls_code":      "0",
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/ranking/volume-power",
                             headers=hdrs, params=params) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        print(f"[kis_volume_power_rank] 오류: {e}")
        return []

    result = []
    for item in data.get("output", [])[:n]:
        ticker = (item.get("stck_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "ticker":           ticker,
            "name":             (item.get("hts_kor_isnm") or "").strip(),
            "price":            int(item.get("stck_prpr",      0) or 0),
            "chg_pct":          float(item.get("prdy_ctrt",    0) or 0),
            "volume_power_pct": float(item.get("tday_rltv",    0) or 0),
            "buy_vol":          int(item.get("shnu_cnqn_smtn", 0) or 0),
            "sell_vol":         int(item.get("seln_cnqn_smtn", 0) or 0),
        })
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 재무비율 순위 / 52주 신고가·신저가 / 거래원
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def kis_finance_ratio_rank(token: str, market: str = "0000",
                                  year: str = "", quarter: str = "3",
                                  sort: str = "7", n: int = 30) -> list:
    """전종목 재무비율 순위 (FHPST01750000).

    market: 0000=전체, 0001=거래소, 1001=코스닥, 2001=코스피200
    year: 회계연도 (기본=전년도)
    quarter: 0=1Q, 1=반기, 2=3Q, 3=결산
    sort: 7=수익성, 11=안정성, 15=성장성, 20=활동성
    """
    if not year:
        year = str(datetime.now(KST).year - 1)

    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ranking/finance-ratio",
                              "FHPST01750000", token, {
            "fid_trgt_cls_code": "0",
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20175",
            "fid_input_iscd": market,
            "fid_div_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_input_option_1": year,
            "fid_input_option_2": quarter,
            "fid_rank_sort_cls_code": sort,
            "fid_blng_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
        })
    items = d.get("output", [])
    if os.environ.get("DEBUG") and items:
        print(f"[DEBUG] finance_ratio keys: {list(items[0].keys())}")
    result = []
    for item in items[:n]:
        ticker = (item.get("stck_shrn_iscd") or item.get("mksc_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "rank": int(item.get("data_rank", 0) or 0),
            "ticker": ticker,
            "name": (item.get("hts_kor_isnm") or "").strip(),
            "price": int(item.get("stck_prpr", 0) or 0),
            "chg_pct": float(item.get("prdy_ctrt", 0) or 0),
            # 수익성 (sort=7)
            "capital_profit_rate": float(item.get("cptl_op_prfi", 0) or 0),    # 총자본경상이익률
            "capital_net_rate": float(item.get("cptl_ntin_rate", 0) or 0),     # 총자본순이익률
            "sales_gross_rate": float(item.get("sale_totl_rate", 0) or 0),     # 매출액총이익률
            "sales_net_rate": float(item.get("sale_ntin_rate", 0) or 0),       # 매출액순이익률
            # 안정성 (sort=11)
            "equity_ratio": float(item.get("bis", 0) or 0),                    # 자기자본비율
            "debt_ratio": float(item.get("lblt_rate", 0) or 0),               # 부채비율
            "borrowing_dep": float(item.get("bram_depn", 0) or 0),            # 차입금의존도
            "reserve_rate": float(item.get("rsrv_rate", 0) or 0),             # 유보비율
            # 성장성 (sort=15)
            "revenue_growth": float(item.get("grs", 0) or 0),                 # 매출액증가율
            "op_profit_growth": float(item.get("bsop_prfi_inrt", 0) or 0),    # 영업이익증가율
            "net_profit_growth": float(item.get("ntin_inrt", 0) or 0),        # 순이익증가율
            "equity_growth": float(item.get("equt_inrt", 0) or 0),            # 자기자본증가율
            "total_asset_growth": float(item.get("totl_aset_inrt", 0) or 0),  # 총자산증가율
            # 활동성 (sort=20)
            "capital_turnover": float(item.get("cptl_tnrt", 0) or 0),         # 총자본회전율
            "volume": int(item.get("acml_vol", 0) or 0),
        })
    return result


async def kis_near_new_highlow(token: str, mode: str = "high",
                                market: str = "0000", gap_min: int = 0,
                                gap_max: int = 10, n: int = 30) -> list:
    """52주 신고가/신저가 근접 종목 (FHPST01870000).

    mode: "high"=신고가 근접, "low"=신저가 근접
    market: 0000=전체, 0001=거래소, 1001=코스닥
    gap_min/gap_max: 괴리율 범위 (%)
    """
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ranking/near-new-highlow",
                              "FHPST01870000", token, {
            "fid_aply_rang_vol": "0",
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20187",
            "fid_div_cls_code": "0",
            "fid_input_cnt_1": str(gap_min),
            "fid_input_cnt_2": str(gap_max),
            "fid_prc_cls_code": "0" if mode == "high" else "1",
            "fid_input_iscd": market,
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_aply_rang_prc_1": "0",
            "fid_aply_rang_prc_2": "10000000",
        })
    items = d.get("output", [])
    if os.environ.get("DEBUG") and items:
        print(f"[DEBUG] near_new_highlow keys: {list(items[0].keys())}")
    result = []
    for i, item in enumerate(items[:n]):
        ticker = (item.get("stck_shrn_iscd") or item.get("mksc_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "rank": i + 1,
            "ticker": ticker,
            "name": (item.get("hts_kor_isnm") or "").strip(),
            "price": int(item.get("stck_prpr", 0) or 0),
            "chg_pct": float(item.get("prdy_ctrt", 0) or 0),
            "base_price": int(item.get("stck_sdpr", 0) or 0),
            "new_high": int(item.get("new_hgpr", 0) or 0),
            "high_gap_pct": float(item.get("hprc_near_rate", 0) or 0),
            "new_low": int(item.get("new_lwpr", 0) or 0),
            "low_gap_pct": float(item.get("lwpr_near_rate", 0) or 0),
            "volume": int(item.get("acml_vol", 0) or 0),
        })
    return result


async def kis_inquire_member(ticker: str, token: str) -> dict:
    """종목별 거래원(증권사) 매매 정보 (FHKST01010600, inquire-member).

    Returns: {ticker, name, buy_members: [...], sell_members: [...]}
    """
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-member",
                              "FHKST01010600", token, {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        })
    output = d.get("output", {})
    if os.environ.get("DEBUG") and output:
        keys = list(output.keys()) if isinstance(output, dict) else list(output[0].keys()) if output else []
        print(f"[DEBUG] inquire_member keys: {keys}")
    # output은 단일 dict, 필드가 seln_mbcr_name1~5, total_seln_qty1~5 등 번호 접미사
    if isinstance(output, list):
        output = output[0] if output else {}
    sell_members = []
    buy_members = []
    for i in range(1, 6):
        sname = (output.get(f"seln_mbcr_name{i}") or "").strip()
        sqty = int(output.get(f"total_seln_qty{i}", 0) or 0)
        srlim = float(output.get(f"seln_mbcr_rlim{i}", 0) or 0)
        if sname:
            sell_members.append({"name": sname, "volume": sqty, "ratio": srlim})
        bname = (output.get(f"shnu_mbcr_name{i}") or "").strip()
        bqty = int(output.get(f"total_shnu_qty{i}", 0) or 0)
        brlim = float(output.get(f"shnu_mbcr_rlim{i}", 0) or 0)
        if bname:
            buy_members.append({"name": bname, "volume": bqty, "ratio": brlim})
    note = None
    if not sell_members and not buy_members:
        note = "거래원 데이터 없음 (휴장일이거나 장중 미제공)"
    result = {
        "ticker": ticker,
        "buy_members": buy_members,
        "sell_members": sell_members,
    }
    if note:
        result["note"] = note
    return result


async def kis_daily_credit_balance(ticker: str, token: str, n: int = 20) -> list:
    """신용잔고 일별추이 (FHPST04760000).

    Returns: [{date, credit_balance, credit_ratio, change, ...}, ...]
    """
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/daily-credit-balance",
                              "FHPST04760000", token, {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20476",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_DATE_1": "",
        })
    items = d.get("output", d.get("output1", []))
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        result.append({
            "date": (item.get("bsop_date") or item.get("stck_bsop_date") or "").strip(),
            "credit_balance": int(item.get("crdt_ldng_remn", 0) or 0),
            "credit_ratio": float(item.get("crdt_ldng_remn_rate", 0) or 0),
            "credit_new": int(item.get("crdt_ldng_new_qty", 0) or 0),
            "credit_repay": int(item.get("crdt_ldng_repy_qty", 0) or 0),
            "close": int(item.get("stck_prpr", 0) or item.get("stck_clpr", 0) or 0),
        })
    # 전일 대비 증감 계산
    for i, row in enumerate(result):
        if i + 1 < len(result):
            row["change"] = row["credit_balance"] - result[i + 1]["credit_balance"]
        else:
            row["change"] = 0
    return result


async def kis_daily_loan_trans(ticker: str, token: str, n: int = 20) -> list:
    """대차거래 일별추이 (HHPST074500C0).

    Returns: [{date, loan_balance, loan_new, loan_repay, ...}, ...]
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    start = (datetime.now(KST) - timedelta(days=n * 2)).strftime("%Y%m%d")
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/daily-loan-trans",
                              "HHPST074500C0", token, {
            "MRKT_DIV_CLS_CODE": "3",
            "MKSC_SHRN_ISCD": ticker,
            "START_DATE": start,
            "END_DATE": today,
            "CTS": "",
        })
    items = d.get("output1", d.get("output", []))
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        result.append({
            "date": (item.get("trns_date") or item.get("bsop_date") or "").strip(),
            "loan_balance": int(item.get("loan_remn", 0) or item.get("stln_remn", 0) or 0),
            "loan_new": int(item.get("loan_new_qty", 0) or item.get("stln_new_qty", 0) or 0),
            "loan_repay": int(item.get("loan_repy_qty", 0) or item.get("stln_repy_qty", 0) or 0),
            "loan_balance_amt": int(item.get("loan_remn_amt", 0) or 0),
        })
    # 전일 대비 증감
    for i, row in enumerate(result):
        if i + 1 < len(result):
            row["change"] = row["loan_balance"] - result[i + 1]["loan_balance"]
        else:
            row["change"] = 0
    return result


async def kis_overtime_price(ticker: str, token: str) -> dict:
    """시간외 현재가 (FHPST02300000).

    Returns: {ticker, overtime_price, overtime_chg_rate, overtime_vol, ...}
    """
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-overtime-price",
                              "FHPST02300000", token, {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        })
    out = d.get("output", {})
    if isinstance(out, list):
        out = out[0] if out else {}
    return {
        "ticker": ticker,
        "overtime_price": int(out.get("ovtm_untp_prpr", 0) or 0),
        "overtime_chg_rate": float(out.get("ovtm_untp_prdy_ctrt", 0) or 0),
        "overtime_vol": int(out.get("ovtm_untp_vol", 0) or 0),
        "overtime_tr_pbmn": int(out.get("ovtm_untp_tr_pbmn", 0) or 0),
        "close": int(out.get("stck_prpr", 0) or 0),
        "base_price": int(out.get("stck_sdpr", 0) or 0),
        "chg_pct": float(out.get("prdy_ctrt", 0) or 0),
    }


async def kis_asking_price(ticker: str, token: str) -> dict:
    """호가 잔량 (FHKST01010200).

    Returns: {ticker, asks: [{price, volume}], bids: [{price, volume}],
             total_ask_vol, total_bid_vol, bid_ask_ratio}
    """
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                              "FHKST01010200", token, {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        })
    out1 = d.get("output1", {})
    out2 = d.get("output2", {})
    if isinstance(out1, list):
        out1 = out1[0] if out1 else {}
    if isinstance(out2, list):
        out2 = out2[0] if out2 else {}

    asks = []  # 매도호가 (낮은 가격부터)
    bids = []  # 매수호가 (높은 가격부터)
    for i in range(1, 11):
        ask_p = int(out1.get(f"askp{i}", 0) or 0)
        ask_v = int(out1.get(f"askp_rsqn{i}", 0) or 0)
        bid_p = int(out1.get(f"bidp{i}", 0) or 0)
        bid_v = int(out1.get(f"bidp_rsqn{i}", 0) or 0)
        if ask_p:
            asks.append({"price": ask_p, "volume": ask_v})
        if bid_p:
            bids.append({"price": bid_p, "volume": bid_v})

    total_ask = int(out1.get("total_askp_rsqn", 0) or 0)
    total_bid = int(out1.get("total_bidp_rsqn", 0) or 0)
    ratio = round(total_bid / total_ask * 100, 1) if total_ask > 0 else 0

    return {
        "ticker": ticker,
        "asks": asks,
        "bids": bids,
        "total_ask_vol": total_ask,
        "total_bid_vol": total_bid,
        "bid_ask_ratio": ratio,
        "price": int(out2.get("stck_prpr", 0) or 0),
        "chg_pct": float(out2.get("prdy_ctrt", 0) or 0),
    }


async def kis_overtime_fluctuation(token: str, sort: str = "rise",
                                    market: str = "0000", n: int = 20) -> list:
    """시간외 등락률 순위 (FHPST02340000).

    sort: "rise"=상승률 상위, "fall"=하락률 상위
    market: 0000=전체, 0001=코스피, 1001=코스닥
    """
    div_code = "2" if sort == "rise" else "5"  # 2=상승률, 5=하락률
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ranking/overtime-fluctuation",
                              "FHPST02340000", token, {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_MRKT_CLS_CODE": "",
            "FID_COND_SCR_DIV_CODE": "20234",
            "FID_INPUT_ISCD": market,
            "FID_DIV_CLS_CODE": div_code,
            "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "",
            "FID_VOL_CNT": "",
            "FID_TRGT_CLS_CODE": "",
            "FID_TRGT_EXLS_CLS_CODE": "",
        })
    items = d.get("output", d.get("output1", []))
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        ticker = (item.get("stck_shrn_iscd") or item.get("mksc_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "rank": int(item.get("data_rank", 0) or 0),
            "ticker": ticker,
            "name": (item.get("hts_kor_isnm") or "").strip(),
            "overtime_price": int(item.get("stck_prpr", 0) or item.get("ovtm_untp_prpr", 0) or 0),
            "chg_pct": float(item.get("prdy_ctrt", 0) or item.get("ovtm_untp_prdy_ctrt", 0) or 0),
            "volume": int(item.get("acml_vol", 0) or item.get("ovtm_untp_vol", 0) or 0),
            "prev_close": int(item.get("stck_sdpr", 0) or 0),
        })
    return result


async def kis_traded_by_company(token: str, broker: str = "", sort: str = "buy",
                                 market: str = "0000", n: int = 20) -> list:
    """증권사별 매매종목 순위 (FHPST01860000).

    broker: 증권사코드 (빈 문자열이면 자사)
    sort: "buy"=매수상위, "sell"=매도상위
    market: 0000=전체, 0001=거래소, 1001=코스닥
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    sort_code = "1" if sort == "buy" else "0"
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ranking/traded-by-company",
                              "FHPST01860000", token, {
            "fid_trgt_exls_cls_code": "0",
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20186",
            "fid_div_cls_code": "0",
            "fid_rank_sort_cls_code": sort_code,
            "fid_input_date_1": today,
            "fid_input_date_2": today,
            "fid_input_iscd": broker if broker else market,
            "fid_trgt_cls_code": "0",
            "fid_aply_rang_vol": "0",
            "fid_aply_rang_prc_1": "",
            "fid_aply_rang_prc_2": "",
        })
    items = d.get("output", [])
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        ticker = (item.get("stck_shrn_iscd") or item.get("mksc_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "rank": int(item.get("data_rank", 0) or 0),
            "ticker": ticker,
            "name": (item.get("hts_kor_isnm") or "").strip(),
            "price": int(item.get("stck_prpr", 0) or 0),
            "chg_pct": float(item.get("prdy_ctrt", 0) or 0),
            "trade_amt": int(item.get("trad_pbmn", 0) or item.get("acml_tr_pbmn", 0) or 0),
            "trade_vol": int(item.get("trad_vol", 0) or item.get("acml_vol", 0) or 0),
            "broker_name": (item.get("mbcr_nm") or "").strip(),
        })
    return result


async def kis_dividend_rate_rank(token: str, market: str = "0",
                                  n: int = 30) -> list:
    """배당수익률 순위 (HHKDB13470100).

    market: 0=전체, 1=코스피, 3=코스닥
    """
    today = datetime.now(KST)
    year = str(today.year - 1)
    f_dt = f"{year}0101"
    t_dt = f"{year}1231"
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ranking/dividend-rate",
                              "HHKDB13470100", token, {
            "CTS_AREA": " ",
            "GB1": market,
            "UPJONG": "",
            "GB2": "0",
            "GB3": "2",
            "F_DT": f_dt,
            "T_DT": t_dt,
            "GB4": "0",
        })
    items = d.get("output", [])
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        ticker = (item.get("stck_shrn_iscd") or item.get("rank_iscd") or "").strip()
        if not ticker or len(ticker) != 6:
            continue
        result.append({
            "rank": int(item.get("data_rank", 0) or len(result) + 1),
            "ticker": ticker,
            "name": (item.get("hts_kor_isnm") or item.get("rank_isnm") or "").strip(),
            "price": int(item.get("stck_prpr", 0) or 0),
            "dividend": int(item.get("per_sto_divi_amt", 0) or item.get("dvdn_amt", 0) or 0),
            "dividend_yield": float(item.get("divi_rate", 0) or item.get("dvdn_rate", 0) or 0),
            "per": float(item.get("per", 0) or 0),
            "market_cap": int(item.get("lstg_stcn", 0) or 0),
        })
    return result


async def kis_us_updown_rate(token: str, sort: str = "rise",
                             exchange: str = "NAS", n: int = 20) -> list:
    """해외주식 등락률 상위/하위 종목 순위 (HHDFS76290000).

    sort: "rise"=상승률 상위, "fall"=하락률 상위
    exchange: "NYS", "NAS", "AMS"
    Returns: [{ticker, name, price, chg_pct, volume}, ...]
    """
    gubn = "1" if sort == "rise" else "0"
    try:
        async with aiohttp.ClientSession() as s:
            _, d = await _kis_get(s,
                "/uapi/overseas-stock/v1/ranking/updown-rate",
                "HHDFS76290000", token,
                {
                    "AUTH":     "",
                    "EXCD":     exchange.upper(),
                    "NDAY":     "0",
                    "GUBN":     gubn,
                    "VOL_RANG": "0",
                    "KEYB":     "",
                })
        result = []
        for item in d.get("output2", [])[:n]:
            symb = (item.get("symb") or "").strip()
            if not symb:
                continue
            result.append({
                "ticker":  symb,
                "name":    (item.get("name") or item.get("ename") or "").strip(),
                "price":   float(item.get("last", 0) or 0),
                "chg_pct": float(item.get("rate", 0) or 0),
                "volume":  int(item.get("tvol",  0) or 0),
            })
        if sort == "fall":
            result.sort(key=lambda x: x["chg_pct"])
        return result
    except Exception as e:
        print(f"[kis_us_updown_rate] 오류: {e}")
        return []


async def kis_estimate_perform(ticker: str, token: str) -> dict:
    """국내주식 종목추정실적 (HHKST668300C0)
    output2: 연간 추정실적 / output3: 분기 추정실적
    필드: dt(결산년월) data1(매출액) data2(영업이익) data3(세전이익) data4(순이익) data5(EPS)
    """
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/estimate-perform",
            "HHKST668300C0", token, {"SHT_CD": ticker})

    def _row(r):
        return {
            "dt":  r.get("dt", ""),
            "rev": r.get("data1", ""),
            "op":  r.get("data2", ""),
            "ebt": r.get("data3", ""),
            "np":  r.get("data4", ""),
            "eps": r.get("data5", ""),
        }

    annual = d.get("output2") or []
    qtly   = d.get("output3") or []
    return {
        "annual":    [_row(r) for r in (annual if isinstance(annual, list) else [annual])],
        "quarterly": [_row(r) for r in (qtly   if isinstance(qtly,   list) else [qtly])],
    }


async def kis_dividend_schedule(token: str, from_dt: str = "", to_dt: str = "",
                                ticker: str = "", gb1: str = "0") -> list:
    """예탁원정보 배당일정 (HHKDB669102C0)
    gb1: 0=전체, 1=결산배당, 2=중간배당
    반환: [{sht_cd, record_date, per_sto_divi_amt, divi_rate, divi_pay_dt, ...}, ...]
    """
    if not from_dt:
        from_dt = datetime.now(KST).strftime("%Y%m%d")
    if not to_dt:
        to_dt = (datetime.now(KST) + timedelta(days=90)).strftime("%Y%m%d")
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/ksdinfo/dividend",
            "HHKDB669102C0", token,
            {"CTS": " ", "GB1": gb1, "F_DT": from_dt, "T_DT": to_dt,
             "SHT_CD": ticker or " ", "HIGH_GB": " "})
    return d.get("output1") or d.get("output") or []


def get_stock_universe() -> dict:
    """stock_universe.json에서 종목 유니버스 로드. {ticker: name} 반환.
    /data/stock_universe.json 없으면 kis_api.py 위치 기준 절대경로로 시도.
    """
    _repo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_universe.json")
    for path in [UNIVERSE_FILE, _repo_path, "stock_universe.json"]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                codes = data.get("codes", {})
                if codes:
                    print(f"[universe] {len(codes)}종목 로드 ({path})")
                    return codes
        except Exception:
            pass
    print("[universe] stock_universe.json 로드 실패 — 빈 유니버스 반환")
    return {}


async def fetch_universe_from_krx(token: str) -> dict:
    """KIS 시가총액 상위 API로 유니버스 자동 조회.

    - KOSPI200 구성종목 전체 (fid_input_iscd="2001")
    - KOSDAQ 시총 상위 150종목 (fid_input_iscd="1001")
    페이지네이션: 응답 헤더 tr_cont="M" 이면 다음 페이지 요청.

    Returns: {ticker: name}
    """
    BASE_PATH = "/uapi/domestic-stock/v1/ranking/market-cap"
    TR_ID     = "FHPST01740000"

    async def _fetch_market(iscd: str, max_count: int) -> dict:
        collected: dict = {}
        tr_cont = ""
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            while len(collected) < max_count:
                hdrs = {**_kis_headers(token, TR_ID), "tr_cont": tr_cont}
                params = {
                    "fid_input_price_2":       "",
                    "fid_cond_mrkt_div_code":  "J",
                    "fid_cond_scr_div_code":   "20174",
                    "fid_div_cls_code":        "1",   # 보통주만 (우선주·ETF 제외)
                    "fid_input_iscd":          iscd,
                    "fid_trgt_cls_code":       "0",
                    "fid_trgt_exls_cls_code":  "0",
                    "fid_input_price_1":       "",
                    "fid_vol_cnt":             "",
                }
                try:
                    async with s.get(f"{KIS_BASE_URL}{BASE_PATH}",
                                     headers=hdrs, params=params) as r:
                        data           = await r.json(content_type=None)
                        resp_tr_cont   = r.headers.get("tr_cont", "D")
                except Exception as e:
                    print(f"[fetch_universe] iscd={iscd} 요청 오류: {e}")
                    break

                items = data.get("output", [])
                if not items:
                    break
                for item in items:
                    ticker = (item.get("mksc_shrn_iscd") or "").strip()
                    name   = (item.get("hts_kor_isnm")   or "").strip()
                    if ticker and name:
                        collected[ticker] = name
                        if len(collected) >= max_count:
                            break

                if resp_tr_cont != "M":
                    break
                tr_cont = "N"
                await asyncio.sleep(0.15)

        return collected

    kospi200  = await _fetch_market("2001", 250)  # KOSPI200 전체
    await asyncio.sleep(0.3)
    kosdaq150 = await _fetch_market("1001", 150)  # KOSDAQ 시총 상위 150
    universe  = {**kospi200, **kosdaq150}
    print(f"[fetch_universe] KOSPI200={len(kospi200)}, KOSDAQ={len(kosdaq150)}, 합계={len(universe)}")
    return universe


async def batch_fetch(codes: list, fetch_fn, token: str, delay: float = 0.06) -> dict:
    """종목 리스트에 대해 rate limit 지키면서 배치 조회.
    codes: list of tickers
    fetch_fn(ticker, token) → result
    returns: {ticker: result}
    """
    results = {}
    for code in codes:
        try:
            results[code] = await fetch_fn(code, token)
        except Exception:
            pass
        await asyncio.sleep(delay)
    return results


async def kis_daily_closes(ticker: str, token: str, n: int = 65) -> list:
    """최근 n거래일 종가 리스트 반환 (최신이 [0])
    FHKST03010100 일봉 API 사용. 8초 timeout으로 hang 방지.
    """
    today_str = datetime.now(KST).strftime("%Y%m%d")
    start_dt = (datetime.now(KST) - timedelta(days=n * 2)).strftime("%Y%m%d")
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100", token,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
             "FID_INPUT_DATE_1": start_dt, "FID_INPUT_DATE_2": today_str,
             "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
    candles = d.get("output2") or []
    return [int(c.get("stck_clpr", 0) or 0) for c in candles[:n]]


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KIS WebSocket 실시간 체결가 (국내주식 전용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_ws_key_cache: dict = {"key": None, "expires": 0.0}


async def get_kis_ws_approval_key() -> str:
    """WebSocket 접속키 발급 (23시간 캐시)"""
    import time as _t
    now = _t.time()
    if _ws_key_cache["key"] and now < _ws_key_cache["expires"]:
        return _ws_key_cache["key"]
    url = f"{KIS_BASE_URL}/oauth2/Approval"
    body = {"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "secretkey": KIS_APP_SECRET}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body) as r:
                d = await r.json(content_type=None)
                key = d.get("approval_key", "")
                if key:
                    _ws_key_cache["key"] = key
                    _ws_key_cache["expires"] = now + 82800
                return _ws_key_cache.get("key") or ""
    except Exception as e:
        print(f"[WS] 접속키 발급 오류: {e}")
        return ""


class KisRealtimeManager:
    """KIS WebSocket 국내주식 실시간 체결가 매니저
    평일 09:00~16:00 KST에만 연결. 끊김 시 30초 후 자동 재연결.
    미국 주식은 _is_us_ticker() 로 걸러서 구독하지 않음.
    """
    _WS_URL = "wss://ops.koreainvestment.com:21000"

    def __init__(self):
        self._subscribed: set = set()
        self._ws = None
        self._alert_cb = None
        self._running = False
        self._task = None
        self._fired: dict = {}  # {ticker: set(alert_types)} — 당일 발송 추적

    async def start(self, alert_callback, tickers: set):
        self._alert_cb = alert_callback
        self._subscribed = {t for t in tickers if not _is_us_ticker(t)}
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def update_tickers(self, new_tickers: set):
        """구독 종목 변경 (KR만 필터링)"""
        kr_new    = {t for t in new_tickers if not _is_us_ticker(t)}
        to_add    = kr_new - self._subscribed
        to_remove = self._subscribed - kr_new
        self._subscribed = kr_new
        if self._ws and not self._ws.closed:
            for t in to_add:
                await self._send_sub(t, "1")
            for t in to_remove:
                await self._send_sub(t, "0")

    def reset_fired(self):
        self._fired = {}

    async def _run_loop(self):
        while self._running:
            now = datetime.now(KST)
            if now.weekday() < 5 and 9 <= now.hour < 16:
                try:
                    await self._connect_and_run()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"[WS] 오류: {e}, 30초 후 재연결...")
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(60)   # 장외: 1분마다 체크

    async def _connect_and_run(self):
        self.reset_fired()
        key = await get_kis_ws_approval_key()
        if not key:
            print("[WS] 접속키 없음, 스킵")
            return
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self._WS_URL, heartbeat=30,
                timeout=aiohttp.ClientTimeout(total=None)
            ) as ws:
                self._ws = ws
                print(f"[WS] 연결됨 ({len(self._subscribed)}개 구독)")
                for t in list(self._subscribed):
                    await self._send_sub_raw(ws, key, t, "1")
                    await asyncio.sleep(0.05)
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._on_text(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        print("[WS] 연결 종료됨")
                        break
        self._ws = None

    async def _send_sub_raw(self, ws, key, ticker, tr_type):
        await ws.send_json({
            "header": {
                "approval_key": key, "custtype": "P",
                "tr_type": tr_type, "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": "H0STCNT0", "tr_key": ticker}},
        })

    async def _send_sub(self, ticker, tr_type):
        if self._ws and not self._ws.closed:
            key = await get_kis_ws_approval_key()
            await self._send_sub_raw(self._ws, key, ticker, tr_type)

    async def _on_text(self, raw: str):
        # 포맷: "0|H0STCNT0|001|종목코드^체결시간^현재가^..."
        if raw.startswith("{"):
            return   # JSON ACK 무시
        parts = raw.split("|")
        if len(parts) < 4 or parts[1] != "H0STCNT0":
            return
        count = int(parts[2])
        all_fields = parts[3].split("^")
        if count == 0 or not all_fields:
            return
        per_rec = len(all_fields) // count
        for i in range(count):
            f = all_fields[i * per_rec: (i + 1) * per_rec]
            if len(f) < 3:
                continue
            ticker = f[0]
            try:
                price = int(f[2])
            except (ValueError, IndexError):
                continue
            if price > 0 and self._alert_cb:
                await self._alert_cb(ticker, price)


# KisRealtimeManager 싱글톤
ws_manager = KisRealtimeManager()


def get_ws_tickers() -> set:
    """WebSocket 구독 대상 KR 종목 수집 (포트폴리오 + 손절 + 워치알러트 + 워치리스트)"""
    tickers = set()
    for t in load_json(PORTFOLIO_FILE, {}):
        if t != "us_stocks" and not _is_us_ticker(t):
            tickers.add(t)
    for t in load_stoploss():
        if t != "us_stocks" and not _is_us_ticker(t):
            tickers.add(t)
    for t in load_watchalert():
        if not _is_us_ticker(t):
            tickers.add(t)
    for t in load_watchlist():
        if not _is_us_ticker(t):
            tickers.add(t)
    return tickers


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Yahoo Finance
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def get_yahoo_quote(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status == 200:
                    meta = (await resp.json()).get("chart", {}).get("result", [{}])[0].get("meta", {})
                    price = meta.get("regularMarketPrice", 0)
                    prev = meta.get("chartPreviousClose", 0)
                    return {"price": price, "prev": prev, "change_pct": ((price - prev) / prev * 100) if prev else 0}
    except Exception:
        pass
    return {"price": 0, "prev": 0, "change_pct": 0}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 매크로 대시보드
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_DEFAULT_EVENTS = {
    "FOMC":    "2026-04-28",
    "CPI":     "2026-04-10",
    "PPI":     "2026-04-11",
    "고용보고서": "2026-04-03",
    "다음FOMC": "2026-06-16",
    "이란":     "진행중",
}


def load_events() -> dict:
    """이벤트 캘린더 로드 (/data/events.json, 없으면 기본값으로 초기화)"""
    return load_json(EVENTS_FILE, _DEFAULT_EVENTS)


async def collect_macro_data() -> dict:
    """매크로 지표 전체 수집 — 텔레그램 자동발송 + MCP 공용"""
    data = {}

    # 1. Yahoo Finance 매크로 심볼
    for key, symbol in MACRO_SYMBOLS.items():
        try:
            q = await get_yahoo_quote(symbol)
            p = q.get("price", 0)
            c = q.get("change_pct", 0)
            data[key] = {
                "price":      round(float(p), 2) if p else "?",
                "change_pct": round(float(c), 2) if c is not None else "?",
            }
        except Exception:
            data[key] = {"price": "?", "change_pct": "?"}
        await asyncio.sleep(0.3)

    # 2. KOSPI
    try:
        q = await get_yahoo_quote("^KS11")
        data["KOSPI"] = {
            "price":      round(float(q.get("price", 0)), 2),
            "change_pct": round(float(q.get("change_pct", 0)), 2),
        }
    except Exception:
        data["KOSPI"] = {"price": "?", "change_pct": "?"}

    # 3. USD/KRW
    try:
        q = await get_yahoo_quote("KRW=X")
        krw = float(q.get("price", 0) or 0)
        data["USDKRW"] = {
            "price":      f"{krw:.1f}" if krw else "?",
            "change_pct": round(float(q.get("change_pct", 0)), 2),
        }
    except Exception:
        data["USDKRW"] = {"price": "?", "change_pct": "?"}

    # 4. 외국인 KOSPI 수급 (업종별 합산)
    try:
        token = await get_kis_token()
        if token:
            total_frgn = 0
            for code, _ in WI26_SECTORS:
                frgn, _ = await _fetch_sector_flow(token, code)
                total_frgn += frgn
                await asyncio.sleep(0.1)
            data["FOREIGN_FLOW"] = {"amount_억": total_frgn}
        else:
            data["FOREIGN_FLOW"] = {"amount_억": "?"}
    except Exception:
        data["FOREIGN_FLOW"] = {"amount_억": "?"}

    # 5. 이벤트 캘린더 (날짜 미래 항목만 포함)
    events = load_events()
    now = datetime.now(KST)
    upcoming = {}
    for key, val in events.items():
        try:
            evt = datetime.strptime(val, "%Y-%m-%d")
            if evt.date() >= now.date():
                upcoming[key] = val
        except Exception:
            upcoming[key] = val   # "진행중" 같은 비날짜 값도 포함
    data["EVENTS"] = upcoming

    return data


def format_macro_msg(data: dict) -> str:
    """매크로 데이터 → 텔레그램 메시지 포맷"""
    def _p(d, prefix="", suffix=""):
        v = d.get("price", "?")
        return f"{prefix}{v}{suffix}" if v != "?" else "?"

    def _c(d):
        c = d.get("change_pct", "?")
        if c == "?":
            return "?"
        try:
            return f"{float(c):+.2f}%"
        except Exception:
            return str(c)

    now = datetime.now(KST)
    msg = f"📊 *매크로 대시보드* ({now.strftime('%m/%d %H:%M')} KST)\n\n"

    # [시장심리]
    vix   = data.get("VIX",   {})
    kospi = data.get("KOSPI", {})
    msg += "[시장심리]\n"
    msg += f"VIX: {_p(vix)} ({_c(vix)}) | KOSPI: {_p(kospi)} ({_c(kospi)})\n\n"

    # [가격지표]
    wti    = data.get("WTI",    {})
    gold   = data.get("GOLD",   {})
    copper = data.get("COPPER", {})
    dxy    = data.get("DXY",    {})
    usdkrw = data.get("USDKRW",{})
    us10y  = data.get("US10Y",  {})
    msg += "[가격지표]\n"
    msg += f"WTI: ${_p(wti)} ({_c(wti)}) | 금: ${_p(gold)} ({_c(gold)})\n"
    msg += f"구리: ${_p(copper)} ({_c(copper)}) | DXY: {_p(dxy)} ({_c(dxy)})\n"
    # 환율 변동률 ±0.5% 이상 시 경고 이모지
    _fx_chg = usdkrw.get("change_pct", "?")
    _fx_warn = ""
    try:
        _fx_val = float(_fx_chg)
        if _fx_val >= 0.5:
            _fx_warn = " ⚠️📈"
        elif _fx_val <= -0.5:
            _fx_warn = " ⚠️📉"
    except (TypeError, ValueError):
        pass
    msg += f"USD/KRW: {_p(usdkrw)} ({_c(usdkrw)}){_fx_warn} | US10Y: {_p(us10y)}% ({_c(us10y)})\n\n"

    # [수급]
    ff  = data.get("FOREIGN_FLOW", {})
    amt = ff.get("amount_억", "?")
    msg += "[수급]\n"
    if isinstance(amt, (int, float)):
        msg += f"외인 KOSPI: {amt:+,}억\n\n"
    else:
        msg += f"외인 KOSPI: {amt}\n\n"

    # [이벤트]
    events = data.get("EVENTS", {})
    if events:
        msg += "[이벤트]\n"
        for k, v in list(events.items())[:5]:
            msg += f"{k}: {v}\n"
        msg += "\n"

    regime = judge_regime(data)
    msg += f"→ 자동판정: {regime['regime']} {regime['label']} ({', '.join(regime['reasons'])})"
    return msg


def judge_regime(data: dict) -> dict:
    """매크로 데이터 기반 레짐 자동 판정 (RED > ORANGE > YELLOW > GREEN)"""
    def _sf(d, key="price"):
        v = d.get(key, "?")
        if v == "?":
            return None
        try:
            return float(str(v).replace(",", ""))
        except Exception:
            return None

    vix       = _sf(data.get("VIX",   {}))
    wti       = _sf(data.get("WTI",   {}))
    kospi_chg = _sf(data.get("KOSPI", {}), "change_pct")
    usdkrw    = _sf(data.get("USDKRW",{}))
    ff_amt    = data.get("FOREIGN_FLOW", {}).get("amount_억", "?")
    frgn_net  = ff_amt if isinstance(ff_amt, (int, float)) else None

    # RED
    red = []
    if vix       is not None and vix       >= 30:  red.append(f"VIX {vix:.2f}")
    if wti       is not None and wti       >= 100: red.append(f"WTI ${wti:.2f}")
    if kospi_chg is not None and kospi_chg <= -5:  red.append(f"KOSPI {kospi_chg:+.2f}%")
    if red:
        return {"regime": "🔴", "label": "위기", "reasons": red}

    # ORANGE
    orange = []
    if vix       is not None and vix       >= 25:   orange.append(f"VIX {vix:.2f}")
    if wti       is not None and wti       >= 90:   orange.append(f"WTI ${wti:.2f}")
    if kospi_chg is not None and kospi_chg <= -3:   orange.append(f"KOSPI {kospi_chg:+.2f}%")
    if usdkrw    is not None and usdkrw    >= 1500: orange.append(f"USD/KRW {usdkrw:.1f}")
    if orange:
        return {"regime": "🟠", "label": "경계", "reasons": orange}

    # GREEN (모든 조건 충족 시)
    if (vix       is not None and vix       < 20 and
        kospi_chg is not None and kospi_chg > 0  and
        frgn_net  is not None and frgn_net  > 0  and
        usdkrw    is not None and usdkrw    < 1400):
        return {"regime": "🟢", "label": "공격", "reasons": [
            f"VIX {vix:.2f}",
            f"KOSPI {kospi_chg:+.2f}%",
            f"외인 {frgn_net:+,}억",
            f"USD/KRW {usdkrw:.1f}",
        ]}

    # YELLOW (기본)
    yellow = []
    if vix is not None and 20 <= vix < 25:
        yellow.append(f"VIX {vix:.2f}")
    if not yellow:
        yellow.append("특이 신호 없음")
    return {"regime": "🟡", "label": "중립", "reasons": yellow}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART API - 공시 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def search_dart_disclosures(days_back=1):
    """최근 N일 공시 검색 (전체 기업)"""
    if not DART_API_KEY:
        return []

    now = datetime.now(KST)
    end_date = now.strftime("%Y%m%d")
    start_date = (now - timedelta(days=days_back)).strftime("%Y%m%d")

    url = f"{DART_BASE_URL}/list.json"
    params = {
        "crtfc_key": DART_API_KEY,
        "bgn_de": start_date,
        "end_de": end_date,
        "page_count": 100,
        "sort": "date",
        "sort_mth": "desc",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "000":
                        return data.get("list", [])
    except Exception as e:
        print(f"DART API 오류: {e}")
    return []


def filter_important_disclosures(disclosures, watchlist_names):
    """워치리스트 기업의 중요 공시만 필터링"""
    important = []
    for d in disclosures:
        corp_name = d.get("corp_name", "")
        report_nm = d.get("report_nm", "")

        # 워치리스트 기업인지 확인
        is_watched = any(name in corp_name for name in watchlist_names)
        if not is_watched:
            continue

        # 중요 키워드 매칭
        is_important = any(kw in report_nm for kw in DART_KEYWORDS)
        # 주요사항보고서(B), 발행공시(C)는 항상 중요
        pblntf_ty = d.get("pblntf_ty", "")
        if pblntf_ty in ("B", "C"):
            is_important = True

        if is_important:
            important.append(d)

    return important


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART corp_code 매핑 & 재무 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━
DART_CORP_MAP_FILE = "/data/dart_corp_map.json"


async def build_dart_corp_map(universe: dict) -> dict:
    """corpCode.xml zip 다운로드 → stock_code ↔ corp_code 매핑 생성 후 저장."""
    import zipfile, io, traceback as _tb
    from xml.etree import ElementTree as ET

    if not DART_API_KEY:
        print("[DART] build_dart_corp_map: DART_API_KEY 미설정")
        return {}
    url = f"{DART_BASE_URL}/corpCode.xml?crtfc_key={DART_API_KEY}"
    print(f"[DART] corpCode.xml 다운로드 시작: {url[:60]}...")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(url) as resp:
                print(f"[DART] corpCode.xml HTTP {resp.status}")
                raw = await resp.read()
        print(f"[DART] 다운로드 완료: {len(raw)} bytes")
        zf = zipfile.ZipFile(io.BytesIO(raw))
        print(f"[DART] zip 파일 목록: {zf.namelist()}")
        xml_data = zf.read("CORPCODE.xml")
        root = ET.fromstring(xml_data)

        mapping = {}
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code  = (item.findtext("corp_code")  or "").strip()
            if stock_code and stock_code in universe:
                mapping[stock_code] = corp_code

        try:
            with open(DART_CORP_MAP_FILE, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False)
            print(f"[DART] corp_map 저장 완료: {DART_CORP_MAP_FILE}")
        except Exception as save_e:
            print(f"[DART] corp_map 저장 실패 (메모리에서 계속): {save_e}")
        print(f"[DART] corp_map 생성: {len(mapping)}개 종목")
        return mapping
    except Exception as e:
        print(f"[DART] corp_map 생성 실패: {e}\n{_tb.format_exc()}")
        return {}


async def get_dart_corp_map(universe: dict) -> dict:
    """dart_corp_map.json 로드. 파일 없으면 빈 dict 반환 (다운로드 시도 안 함).

    파일 탐색 순서:
      1. /data/dart_corp_map.json  (Railway Volume)
      2. <kis_api.py 디렉토리>/dart_corp_map.json  (레포 커밋 파일)
    """
    import os
    candidates = [
        DART_CORP_MAP_FILE,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "dart_corp_map.json"),
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[DART] corp_map 로드: {path} ({len(data)}종목)")
                return data
        except Exception as e:
            print(f"[DART] corp_map 로드 실패 ({path}): {e}")
    print("[DART] dart_corp_map.json 없음 — dart_op_growth 사용 불가")
    return {}


async def dart_quarterly_op(corp_code: str, year: int, quarter: int) -> dict | None:
    """DART fnlttSinglAcntAll로 연간/분기 영업이익·매출 조회.

    quarter: 1=1분기, 2=반기, 3=3분기, 4=사업보고서(연간)
    반환: {"year", "quarter", "op_profit"(억원), "revenue"(억원)} 또는 None
    """
    reprt_map = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
    reprt_code = reprt_map.get(quarter, "11011")
    url = f"{DART_BASE_URL}/fnlttSinglAcntAll.json"

    async def _fetch(fs_div: str):
        params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code,
                  "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": fs_div}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(url, params=params) as resp:
                return await resp.json(content_type=None)

    try:
        data = await _fetch("CFS")
        if data.get("status") != "000":
            data = await _fetch("OFS")
        if data.get("status") != "000":
            return None

        op_profit = revenue = None
        for item in data.get("list", []):
            acct    = (item.get("account_nm") or "").strip()
            amt_str = (item.get("thstrm_amount") or "").replace(",", "").replace(" ", "")
            if not amt_str:
                continue
            try:
                amt = int(amt_str) // 100_000_000  # 원 → 억원
            except Exception:
                continue
            if acct in ("영업이익", "영업이익(손실)") and op_profit is None:
                op_profit = amt
            elif acct in ("매출액", "수익(매출액)") and revenue is None:
                revenue = amt

        if op_profit is None:
            return None
        return {"year": year, "quarter": quarter, "op_profit": op_profit, "revenue": revenue}
    except Exception as e:
        print(f"[DART] dart_quarterly_op {corp_code} {year}Q{quarter} 오류: {e}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 사업보고서 본문 저장
# ━━━━━━━━━━━━━━━━━━━━━━━━━
DART_REPORTS_DIR = "/data/dart_reports"
CORP_CODES_FILE  = "/data/corp_codes.json"


async def load_corp_codes() -> dict:
    """corp_codes.json 로드. 1일 1회 갱신 (캐시)."""
    if os.path.exists(CORP_CODES_FILE):
        try:
            mtime = os.path.getmtime(CORP_CODES_FILE)
            age_hours = (datetime.now(KST).timestamp() - mtime) / 3600
            if age_hours < 24:
                with open(CORP_CODES_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                if data:
                    print(f"[DART] corp_codes 캐시 사용 ({len(data)}종목, {age_hours:.1f}h)")
                    return data
        except Exception as e:
            print(f"[DART] corp_codes 캐시 로드 실패: {e}")

    # 캐시 만료 또는 없음 → corpCode.xml 다운로드
    return await _download_corp_codes()


async def _download_corp_codes() -> dict:
    """OpenDART corpCode.xml zip → ticker↔corp_code 매핑 생성."""
    import zipfile, io
    if not DART_API_KEY:
        print("[DART] corp_codes: DART_API_KEY 미설정")
        return {}
    url = f"{DART_BASE_URL}/corpCode.xml?crtfc_key={DART_API_KEY}"
    print(f"[DART] corpCode.xml 다운로드 시작...")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(url) as resp:
                if resp.status != 200:
                    print(f"[DART] corpCode.xml HTTP {resp.status}")
                    return {}
                raw = await resp.read()
        from xml.etree import ElementTree as ET
        zf = zipfile.ZipFile(io.BytesIO(raw))
        xml_data = zf.read("CORPCODE.xml")
        root = ET.fromstring(xml_data)

        mapping = {}
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code  = (item.findtext("corp_code")  or "").strip()
            corp_name  = (item.findtext("corp_name")  or "").strip()
            if stock_code and corp_code:
                mapping[stock_code] = {"corp_code": corp_code, "corp_name": corp_name}

        os.makedirs(os.path.dirname(CORP_CODES_FILE), exist_ok=True)
        with open(CORP_CODES_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False)
        print(f"[DART] corp_codes 저장: {len(mapping)}종목")
        return mapping
    except Exception as e:
        print(f"[DART] corp_codes 다운로드 실패: {e}")
        return {}


def _report_name_priority(report_nm: str) -> int:
    """보고서명 우선순위. 낮을수록 우선. 원본 > 정정 > 첨부정정."""
    nm = (report_nm or "").strip()
    if nm == "사업보고서":
        return 0
    if nm.startswith("[정정]"):
        return 1
    if nm.startswith("[첨부정정]"):
        return 2
    return 3


async def search_dart_reports(corp_code: str, days_back: int = 365) -> list:
    """OpenDART list.json으로 사업보고서(A001) 검색.

    결과를 보고서명 우선순위로 정렬: 사업보고서 > [정정] > [첨부정정].
    """
    if not DART_API_KEY:
        return []
    now = datetime.now(KST)
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bgn_de": (now - timedelta(days=days_back)).strftime("%Y%m%d"),
        "end_de": now.strftime("%Y%m%d"),
        "pblntf_ty": "A",
        "pblntf_detail_ty": "A001",
        "page_count": 10,
        "sort": "date",
        "sort_mth": "desc",
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(f"{DART_BASE_URL}/list.json", params=params) as resp:
                if resp.status != 200:
                    print(f"[DART] list.json HTTP {resp.status} for {corp_code}")
                    return []
                data = await resp.json(content_type=None)
                status = data.get("status", "")
                if status == "000":
                    results = data.get("list", [])
                    # 원본 사업보고서 우선, 정정/첨부정정 후순위
                    results.sort(key=lambda r: _report_name_priority(r.get("report_nm", "")))
                    names = [r.get("report_nm", "") for r in results[:5]]
                    print(f"[DART] list.json {corp_code}: {len(results)}건 → {names}")
                    return results
                else:
                    print(f"[DART] list.json {corp_code}: status={status} msg={data.get('message','')}")
    except Exception as e:
        print(f"[DART] report search error ({corp_code}): {e}")
    return []


async def fetch_dart_document(rcept_no: str) -> str:
    """OpenDART document.xml → ZIP 내 HTML 파일들 → 순수 텍스트.

    document.xml 응답은 ZIP 파일 (다수 HTML 조각) 또는 XML wrapper.
    ZIP인 경우 내부 모든 텍스트 파일을 합쳐 추출.
    """
    import zipfile, io
    if not DART_API_KEY:
        return ""
    url = f"{DART_BASE_URL}/document.xml"
    params = {"crtfc_key": DART_API_KEY, "rcept_no": rcept_no}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
            async with s.get(url, params=params) as resp:
                status = resp.status
                ct = resp.headers.get("Content-Type", "")
                if status != 200:
                    print(f"[DART] document.xml HTTP {status} ct={ct} for {rcept_no}")
                    return ""
                raw = await resp.read()

        size_kb = len(raw) / 1024
        magic = raw[:4].hex() if len(raw) >= 4 else "empty"
        print(f"[DART] document.xml 응답: rcept={rcept_no} size={size_kb:.1f}KB "
              f"ct={ct} magic={magic}")

        # OpenDART 에러 감지 — JSON 형태
        if b'"status"' in raw[:200] and b'"message"' in raw[:500]:
            try:
                err = json.loads(raw)
                print(f"[DART] document.xml JSON 에러: {err.get('status')} {err.get('message')}")
                return ""
            except Exception:
                pass

        # OpenDART 에러 감지 — XML 형태 (<result><status>...)
        if raw[:50].lstrip().startswith(b'<?xml') or b'<result>' in raw[:200]:
            try:
                from xml.etree import ElementTree as _ET
                _root = _ET.fromstring(raw)
                _status = _root.findtext("status") or _root.findtext(".//status") or ""
                _msg = _root.findtext("message") or _root.findtext(".//message") or ""
                if _status and _status != "000":
                    print(f"[DART] document.xml XML 에러: status={_status} msg={_msg}")
                    return ""
            except Exception:
                pass  # XML 파싱 실패 → 본문일 수 있음

        from bs4 import BeautifulSoup
        import re

        # ZIP 파일인지 확인 (PK 매직넘버)
        if raw[:2] == b'PK':
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
                all_names = zf.namelist()
                print(f"[DART] ZIP 내부 파일({len(all_names)}): "
                      f"{[n for n in all_names[:10]]}")

                html_parts = []
                # 이미지/CSS/폰트 제외, 나머지 텍스트 파일 모두 처리
                skip_ext = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg',
                            '.css', '.js', '.ttf', '.woff', '.woff2', '.eot',
                            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt',
                            '.zip', '.hwp', '.mp3', '.mp4', '.avi', '.ico'}
                for name in sorted(all_names):
                    ext = os.path.splitext(name)[1].lower()
                    if ext in skip_ext:
                        continue
                    try:
                        part_raw = zf.read(name)
                        part = part_raw.decode("utf-8", errors="replace")
                        soup = BeautifulSoup(part, "html.parser")
                        text = soup.get_text(separator="\n")
                        text = re.sub(r'\n{3,}', '\n\n', text).strip()
                        if len(text) > 20:
                            html_parts.append(text)
                    except Exception as ze:
                        print(f"[DART] ZIP 내 파일 처리 실패 ({name}): {ze}")

                full_text = "\n\n".join(html_parts)
                if len(full_text) < 100:
                    print(f"[DART] ZIP 본문 너무 짧음 ({len(full_text)}자, "
                          f"파일{len(html_parts)}개): {rcept_no}")
                    return ""
                print(f"[DART] ZIP 문서 추출 성공: {rcept_no} "
                      f"({len(full_text)}자, {len(html_parts)}파일)")
                return full_text
            except zipfile.BadZipFile:
                print(f"[DART] ZIP 파일 손상 (BadZipFile): {rcept_no} "
                      f"raw[:20]={raw[:20]}")
                return ""

        # ZIP이 아닌 경우 — XML/HTML 직접 응답
        html = raw.decode("utf-8", errors="replace")
        print(f"[DART] non-ZIP 응답 처리: {rcept_no} 앞100자={html[:100]!r}")
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n")
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        if len(text) < 100:
            print(f"[DART] non-ZIP 본문 너무 짧음 ({len(text)}자): {rcept_no}")
            return ""
        return text
    except Exception as e:
        print(f"[DART] document fetch error ({rcept_no}): {e}")
        return ""


def _report_file_exists(rcept_no: str) -> str | None:
    """접수번호로 기존 파일 검색. 있으면 파일경로, 없으면 None."""
    if not rcept_no or not os.path.exists(DART_REPORTS_DIR):
        return None
    for fname in os.listdir(DART_REPORTS_DIR):
        if rcept_no in fname:
            return os.path.join(DART_REPORTS_DIR, fname)
    return None


async def save_dart_report(ticker: str, name: str, rcept_no: str,
                           report_date: str) -> dict | None:
    """사업보고서 본문을 txt로 저장. 이미 존재하면 스킵."""
    if not rcept_no:
        return None
    existing = _report_file_exists(rcept_no)
    if existing:
        size_kb = os.path.getsize(existing) / 1024
        print(f"[DART] 이미 존재: {existing}")
        return {"ticker": ticker, "name": name, "report_date": report_date,
                "file_path": existing, "file_size_kb": round(size_kb, 1),
                "skipped": True}

    text = await fetch_dart_document(rcept_no)
    if not text:
        print(f"[DART] 본문 없음: {ticker} {rcept_no}")
        return None

    os.makedirs(DART_REPORTS_DIR, exist_ok=True)
    date_str = report_date.replace("-", "").replace(".", "")[:8]
    # path traversal 방지: 파일명에서 위험 문자 제거
    import re as _re
    safe_ticker = _re.sub(r'[^a-zA-Z0-9]', '', ticker)
    safe_name = _re.sub(r'[^\w]', '', name)
    safe_rcept = _re.sub(r'[^0-9]', '', rcept_no)
    safe_date = _re.sub(r'[^0-9]', '', date_str)
    if not safe_ticker or not safe_rcept:
        print(f"[DART] 잘못된 ticker/rcept_no: {ticker}/{rcept_no}")
        return None
    filename = f"{safe_ticker}_{safe_name}_{safe_date}_{safe_rcept}.txt"
    filepath = os.path.join(DART_REPORTS_DIR, filename)
    # 최종 경로가 DART_REPORTS_DIR 내인지 확인
    if not os.path.abspath(filepath).startswith(os.path.abspath(DART_REPORTS_DIR)):
        print(f"[DART] 경로 이탈 감지: {filepath}")
        return None

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    header = (
        f"===== DART 사업보고서 =====\n"
        f"종목: {name} ({ticker})\n"
        f"보고서일: {report_date}\n"
        f"접수번호: {rcept_no}\n"
        f"저장일시: {now_str}\n"
        f"{'=' * 30}\n\n"
    )
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(header + text)

    size_kb = os.path.getsize(filepath) / 1024
    print(f"[DART] 저장: {filepath} ({size_kb:.1f}KB)")
    return {"ticker": ticker, "name": name, "report_date": report_date,
            "file_path": filepath, "file_size_kb": round(size_kb, 1),
            "skipped": False}


def read_dart_report(ticker: str, max_chars: int = 50_000) -> dict:
    """저장된 사업보고서 txt 파일 내용 반환. 여러 개면 최신 날짜."""
    if not os.path.exists(DART_REPORTS_DIR):
        return {"error": f"사업보고서 없음. get_dart(mode='report', ticker='{ticker}')으로 먼저 저장하세요."}

    matches = []
    for fname in os.listdir(DART_REPORTS_DIR):
        if not fname.endswith(".txt"):
            continue
        parts = fname.replace(".txt", "").split("_")
        if parts[0] == ticker:
            matches.append(fname)

    if not matches:
        return {"error": f"사업보고서 없음. get_dart(mode='report', ticker='{ticker}')으로 먼저 저장하세요."}

    # 파일명: {ticker}_{name}_{date}_{rcept}.txt — name에 _가 포함될 수 있으므로 뒤에서 파싱
    def _parse_fname(f):
        stem = f.replace(".txt", "")
        parts = stem.split("_")
        # 뒤에서 rcept(숫자), date(8자리 숫자), 나머지가 ticker_name
        if len(parts) >= 4:
            rcept = parts[-1]
            date_str = parts[-2]
            name = "_".join(parts[1:-2])
        elif len(parts) >= 3:
            rcept = ""
            date_str = parts[-1]
            name = "_".join(parts[1:-1])
        else:
            rcept = ""
            date_str = ""
            name = parts[1] if len(parts) >= 2 else ""
        return name, date_str, rcept

    matches.sort(key=lambda f: (_parse_fname(f)[1], _parse_fname(f)[2]), reverse=True)
    fname = matches[0]
    filepath = os.path.join(DART_REPORTS_DIR, fname)

    name, date_str, _ = _parse_fname(fname)
    if len(date_str) == 8:
        report_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    else:
        report_date = date_str

    size_kb = os.path.getsize(filepath) / 1024

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    truncated = False
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True

    return {
        "ticker": ticker,
        "name": name,
        "report_date": report_date,
        "file_path": filepath,
        "file_size_kb": round(size_kb, 1),
        "content": content,
        "truncated": truncated,
    }


def list_dart_reports() -> dict:
    """저장된 사업보고서 txt 파일 목록 반환."""
    files = []
    if os.path.exists(DART_REPORTS_DIR):
        for fname in sorted(os.listdir(DART_REPORTS_DIR)):
            if not fname.endswith(".txt"):
                continue
            filepath = os.path.join(DART_REPORTS_DIR, fname)
            parts = fname.replace(".txt", "").split("_")
            ticker = parts[0] if len(parts) >= 1 else ""
            # 뒤에서 파싱: rcept(마지막), date(뒤에서 둘째), 나머지가 name
            if len(parts) >= 4:
                date_str = parts[-2]
                name = "_".join(parts[1:-2])
            elif len(parts) >= 3:
                date_str = parts[-1]
                name = "_".join(parts[1:-1])
            else:
                date_str = ""
                name = parts[1] if len(parts) >= 2 else ""
            if len(date_str) == 8:
                report_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            else:
                report_date = date_str
            size_kb = os.path.getsize(filepath) / 1024
            files.append({
                "ticker": ticker, "name": name,
                "report_date": report_date,
                "file_path": filepath,
                "file_size_kb": round(size_kb, 1),
            })
    return {"files": files, "total": len(files)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# GitHub Gist 백업/복원
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def backup_data_files() -> dict:
    """GitHub Gist에 /data/*.json 백업 (PATCH 기존 Gist 또는 POST 신규 생성)"""
    if not GITHUB_TOKEN:
        return {"ok": False, "error": "GITHUB_TOKEN 미설정"}

    gist_id = os.environ.get(_BACKUP_GIST_ENV, "")
    files: dict = {}
    backed_up: list = []

    for fpath in _BACKUP_FILES_LIST:
        fname = os.path.basename(fpath)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read().strip() or "{}"
                files[fname] = {"content": content}
                backed_up.append(fname)
            except Exception as e:
                print(f"[backup] {fname} 읽기 실패: {e}")

    if not files:
        return {"ok": False, "error": "백업할 파일 없음"}

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    try:
        async with aiohttp.ClientSession() as s:
            if gist_id:
                url = f"https://api.github.com/gists/{gist_id}"
                payload = {"description": f"stock-bot /data/ backup {ts}", "files": files}
                async with s.patch(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        d = await resp.json()
                        return {"ok": True, "action": "updated", "gist_id": d["id"],
                                "files": backed_up, "updated_at": d.get("updated_at", "")}
                    text = await resp.text()
                    return {"ok": False, "error": f"PATCH {resp.status}: {text[:200]}"}
            else:
                url = "https://api.github.com/gists"
                payload = {"description": f"stock-bot /data/ backup {ts}", "public": False, "files": files}
                async with s.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 201:
                        d = await resp.json()
                        new_id = d["id"]
                        print(f"[backup] 신규 Gist 생성: {new_id} — BACKUP_GIST_ID 환경변수 설정 필요")
                        return {"ok": True, "action": "created", "gist_id": new_id,
                                "files": backed_up, "note": f"BACKUP_GIST_ID={new_id} 환경변수 설정 필요"}
                    text = await resp.text()
                    return {"ok": False, "error": f"POST {resp.status}: {text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def restore_data_files(force: bool = False) -> dict:
    """GitHub Gist에서 /data/*.json 복원. force=False이면 기존 파일 보존."""
    if not GITHUB_TOKEN:
        return {"ok": False, "error": "GITHUB_TOKEN 미설정"}

    gist_id = os.environ.get(_BACKUP_GIST_ENV, "")
    if not gist_id:
        return {"ok": False, "error": "BACKUP_GIST_ID 환경변수 미설정"}

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.github.com/gists/{gist_id}", headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return {"ok": False, "error": f"GET {resp.status}: {text[:200]}"}
                data = await resp.json()

        gist_files = data.get("files", {})
        restored: list = []
        skipped: list = []

        for fpath in _BACKUP_FILES_LIST:
            fname = os.path.basename(fpath)
            if fname not in gist_files:
                continue
            if not force and os.path.exists(fpath):
                skipped.append(fname)
                continue
            try:
                content = gist_files[fname].get("content", "{}")
                json.loads(content)  # 유효성 검사
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
                restored.append(fname)
            except Exception as e:
                print(f"[restore] {fname} 복원 실패: {e}")

        return {"ok": True, "restored": restored, "skipped": skipped,
                "gist_id": gist_id, "updated_at": data.get("updated_at", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def get_backup_status() -> dict:
    """백업 Gist 상태 조회 (최근 백업 시각, 파일 목록)"""
    if not GITHUB_TOKEN:
        return {"ok": False, "error": "GITHUB_TOKEN 미설정"}

    gist_id = os.environ.get(_BACKUP_GIST_ENV, "")
    if not gist_id:
        return {"ok": False, "gist_id": None, "note": "BACKUP_GIST_ID 미설정 — 첫 백업 실행 후 자동 생성"}

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.github.com/gists/{gist_id}", headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return {"ok": False, "error": f"GET {resp.status}: {text[:100]}"}
                data = await resp.json()

        return {
            "ok": True,
            "gist_id": gist_id,
            "updated_at": data.get("updated_at", ""),
            "description": data.get("description", ""),
            "files": list(data.get("files", {}).keys()),
            "file_count": len(data.get("files", {})),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 뉴스 조회 (Google News RSS)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def fetch_news(query="주식 시장 한국", max_items=8):
    """Google News RSS로 뉴스 헤드라인 가져오기"""
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # 간단한 XML 파싱
                    root = ET.fromstring(text)
                    items = root.findall(".//item")
                    results = []
                    for item in items[:max_items]:
                        title = item.find("title").text if item.find("title") is not None else ""
                        pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                        source = item.find("source").text if item.find("source") is not None else ""
                        results.append({"title": title, "date": pub_date, "source": source})
                    return results
    except Exception as e:
        print(f"뉴스 조회 오류: {e}")
    return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 미국 뉴스 / 감성분석 / 실적캘린더 / 섹터 ETF
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_us_news(ticker: str, n: int = 10) -> list:
    """yfinance로 미국 종목 뉴스 헤드라인 조회.
    Returns: [{"date": "YYYYMMDD", "time": "", "title": str, "source": str}, ...]
    yfinance 버전별 응답 구조 차이를 모두 처리 (구버전: flat dict, 신버전: content 중첩).
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        news = t.news or []
        result = []
        from datetime import datetime as _dt
        for item in news[:n]:
            # ── 신버전 yfinance (>=0.2.36): content 중첩 구조 ──
            content = item.get("content", {}) if isinstance(item.get("content"), dict) else {}
            title = content.get("title") or item.get("title", "")
            provider = content.get("provider", {})
            source = provider.get("displayName", "") if isinstance(provider, dict) else ""
            if not source:
                source = item.get("publisher", "")
            # 날짜 파싱: 신버전 pubDate (ISO string) → 구버전 providerPublishTime (unix ts)
            date_str, time_str = "", ""
            pub_date = content.get("pubDate", "")
            pub_ts = item.get("providerPublishTime", 0)
            if pub_date and isinstance(pub_date, str):
                try:
                    dt = _dt.fromisoformat(pub_date.replace("Z", "+00:00"))
                    date_str = dt.strftime("%Y%m%d")
                    time_str = dt.strftime("%H%M%S")
                except Exception:
                    pass
            elif pub_ts:
                try:
                    dt = _dt.fromtimestamp(pub_ts)
                    date_str = dt.strftime("%Y%m%d")
                    time_str = dt.strftime("%H%M%S")
                except Exception:
                    pass
            result.append({"date": date_str, "time": time_str, "title": title, "source": source})
        return result
    except Exception as e:
        print(f"[fetch_us_news] 오류 ({ticker}): {e}")
        return []


def analyze_us_news_sentiment(news_items: list) -> dict:
    """미국 뉴스 헤드라인 영문 감성 분석."""
    positive, negative, neutral = [], [], []
    for item in news_items:
        title = item.get("title", "").lower()
        pos_matches = [kw for kw in _US_POSITIVE_KEYWORDS if kw in title]
        neg_matches = [kw for kw in _US_NEGATIVE_KEYWORDS if kw in title]
        entry = {**item, "matched_keywords": pos_matches + neg_matches}
        if len(pos_matches) > len(neg_matches):
            entry["sentiment"] = "positive"
            positive.append(entry)
        elif len(neg_matches) > len(pos_matches):
            entry["sentiment"] = "negative"
            negative.append(entry)
        else:
            entry["sentiment"] = "neutral"
            neutral.append(entry)
    return {
        "positive": positive, "negative": negative, "neutral": neutral,
        "summary": f"🟢긍정 {len(positive)} / 🔴부정 {len(negative)} / ⚪중립 {len(neutral)}",
    }


def fetch_us_earnings_calendar(tickers: list) -> list:
    """yfinance로 미국 종목 실적 발표일 조회.
    Returns: [{"ticker": str, "name": str, "earnings_date": "YYYY-MM-DD", "days_until": int}, ...]
    t.calendar가 dict 또는 DataFrame 어느 형태든 처리.
    """
    try:
        import yfinance as yf
    except ImportError:
        return []
    from datetime import datetime as _dt, timedelta
    now = _dt.now()
    result = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None:
                continue
            # DataFrame → dict 변환 (일부 yfinance 버전에서 DataFrame 반환)
            if hasattr(cal, 'to_dict'):
                try:
                    # DataFrame 형태: columns = [0], index = ["Earnings Date", ...]
                    if hasattr(cal, 'iloc') and len(cal.columns) > 0:
                        cal = {idx: cal.iloc[i, 0] for i, idx in enumerate(cal.index)}
                    else:
                        cal = cal.to_dict()
                except Exception:
                    continue
            if hasattr(cal, 'empty') and cal.empty:
                continue
            if not isinstance(cal, dict):
                continue
            ed = cal.get("Earnings Date")
            if isinstance(ed, list) and ed:
                ed = ed[0]
            if not ed:
                continue
            if hasattr(ed, 'strftime'):
                date_str = ed.strftime("%Y-%m-%d")
            else:
                date_str = str(ed)[:10]
            try:
                ed_dt = _dt.strptime(date_str, "%Y-%m-%d")
                days_until = (ed_dt - now).days
                if -1 <= days_until <= 30:
                    # t.info 호출은 네트워크 요청이므로 방어적 처리
                    try:
                        name = t.info.get("shortName", ticker)
                    except Exception:
                        name = ticker
                    result.append({
                        "ticker": ticker,
                        "name": name,
                        "earnings_date": date_str,
                        "days_until": days_until,
                    })
            except Exception:
                pass
        except Exception as e:
            print(f"[us_earnings] {ticker} 오류: {e}")
            continue
    result.sort(key=lambda x: x.get("days_until", 999))
    return result


US_SECTOR_ETFS = [
    ("SPY", "S&P500"), ("QQQ", "나스닥100"),
    ("XLK", "기술"), ("XLF", "금융"), ("XLE", "에너지"),
    ("XLV", "헬스케어"), ("XLI", "산업재"), ("XLP", "필수소비"),
    ("XLY", "임의소비"), ("XLRE", "부동산"), ("XLU", "유틸리티"),
]


def fetch_us_sector_etf() -> list:
    """yfinance로 미국 섹터 ETF 등락률 조회.
    Returns: [{"ticker", "name", "price", "chg_1d", "chg_5d"}, ...]
    """
    try:
        import yfinance as yf
    except ImportError:
        return []
    result = []
    for sym, name in US_SECTOR_ETFS:
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="7d")
            if hist is None or hist.empty or len(hist) < 2:
                continue
            cur = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            chg_1d = round((cur - prev) / prev * 100, 2)
            if len(hist) >= 6:
                d5_ago = float(hist["Close"].iloc[-6])
                chg_5d = round((cur - d5_ago) / d5_ago * 100, 2)
            else:
                chg_5d = None
            result.append({
                "ticker": sym, "name": name,
                "price": round(cur, 2),
                "chg_1d": chg_1d,
                "chg_5d": chg_5d,
            })
        except Exception as e:
            print(f"[us_sector_etf] {sym} 오류: {e}")
            continue
    return result


def fetch_us_short_interest(ticker: str) -> dict:
    """yfinance에서 미국 종목 공매도 데이터 조회.
    Returns: {ticker, short_ratio, short_pct_float, days_to_cover, shares_short, ...}
    데이터 없으면 빈 dict. 동기 함수.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        shares_short = info.get("sharesShort")
        if shares_short is None:
            return {"ticker": ticker, "message": "공매도 데이터 없음"}
        return {
            "ticker": ticker,
            "name": info.get("shortName", ticker),
            "short_ratio": info.get("shortRatio"),
            "short_pct_float": info.get("shortPercentOfFloat"),
            "days_to_cover": info.get("shortRatio"),
            "shares_short": shares_short,
            "shares_short_prev": info.get("sharesShortPriorMonth"),
            "short_pct_shares_out": info.get("sharesPercentSharesOut"),
            "float_shares": info.get("floatShares"),
        }
    except Exception as e:
        print(f"[us_short_interest] {ticker} 오류: {e}")
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 시장 레짐 판정 (복합점수 기반)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _yf_history(symbol: str, period: str = "2y") -> list:
    """yfinance 종가 히스토리 → [float, ...] (오래된 순)."""
    try:
        import yfinance as yf
        df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return []
        col = df["Close"]
        # MultiIndex 대응 (yfinance >= 0.2.36 단일 티커도 MultiIndex 가능)
        if hasattr(col, "columns"):
            col = col.iloc[:, 0]
        return [float(v) for v in col.dropna().tolist()]
    except Exception as e:
        print(f"[_yf_history] {symbol}: {e}")
        return []


def _krx_kospi_history(days: int = 600) -> list:
    """pykrx KOSPI 종가 히스토리. 실패 시 yfinance ^KS11 fallback."""
    try:
        from pykrx import stock as krx
        end = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=days)).strftime("%Y%m%d")
        df = krx.get_index_ohlcv(start, end, "1001")
        if df is not None and not df.empty:
            return [float(c) for c in df["종가"].dropna().tolist()]
    except Exception as e:
        print(f"[_krx_kospi_history] pykrx 실패, yfinance fallback: {e}")
    return _yf_history("^KS11", "2y")


def _krx_foreign_net(days: int = 280) -> list:
    """pykrx 외국인 KOSPI 순매수 금액 히스토리. 실패 시 빈 리스트."""
    try:
        from pykrx import stock as krx
        end = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=days)).strftime("%Y%m%d")
        df = krx.get_market_net_purchases_of_equities(start, end, "KOSPI", "외국인")
        if df is not None and not df.empty:
            col = "순매수거래대금" if "순매수거래대금" in df.columns else df.columns[-1]
            return [float(v) for v in df[col].dropna().tolist()]
    except Exception as e:
        print(f"[_krx_foreign_net] pykrx 실패: {e}")
    return []


def _calc_zscore(values: list, lookback: int = 252, min_data: int = 60):
    """롤링 z-score. Returns {"value","z","mean","std"} or None."""
    if len(values) < min_data:
        return None
    import numpy as np
    window = values[-lookback:] if len(values) >= lookback else values
    current = window[-1]
    arr = np.array(window, dtype=float)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    if std < 1e-10:
        return {"value": current, "z": 0.0, "mean": mean, "std": std}
    return {"value": current, "z": float((current - mean) / std), "mean": mean, "std": std}


def _rolling_ma_pct(closes: list, ma_len: int) -> list:
    """각 시점에서 (종가-MA)/MA*100 시리즈 생성."""
    out = []
    for i in range(ma_len, len(closes)):
        ma = sum(closes[i - ma_len + 1:i + 1]) / ma_len
        out.append((closes[i] - ma) / ma * 100 if ma else 0)
    return out


def _rolling_momentum(closes: list, lag: int) -> list:
    """(현재/lag일전 - 1)*100 시리즈."""
    return [(closes[i] / closes[i - lag] - 1) * 100
            for i in range(lag, len(closes))]


def _realized_vol(closes: list, window: int = 20):
    """최근 window일 실현변동성 (연율화 %). None if 데이터 부족."""
    if len(closes) < window + 1:
        return None
    import numpy as np
    recent = closes[-(window + 1):]
    rets = np.diff(np.log(np.array(recent, dtype=float)))
    return float(np.std(rets, ddof=1) * (252 ** 0.5) * 100)


def _rolling_realized_vol(closes: list, window: int = 20) -> list:
    """실현변동성 시계열."""
    import numpy as np
    out = []
    for i in range(window + 1, len(closes)):
        seg = closes[i - window:i + 1]
        rets = np.diff(np.log(np.array(seg, dtype=float)))
        out.append(float(np.std(rets, ddof=1) * (252 ** 0.5) * 100))
    return out


def _sig_entry(value, z, label="", invert=False):
    """신호 dict 생성 헬퍼."""
    zz = round(-z if invert else z, 2)
    return {"value": value, "z": zz, "raw_z": round(z, 2), "label": label}


async def compute_us_signals() -> dict:
    """미국 6개 신호 z-score → {"signals":{}, "score":float, "failed":[]}"""
    import numpy as np
    from scipy.stats import norm

    signals, failed = {}, []

    # 1. VIX (역수)
    vix_data = _yf_history("^VIX", "2y")
    zs = _calc_zscore(vix_data)
    if zs:
        signals["VIX"] = _sig_entry(round(zs["value"], 1), zs["z"], "역수", invert=True)
    else:
        failed.append("VIX")
    await asyncio.sleep(0.3)

    # 2. HY 스프레드 프록시 (HYG/LQD)
    hyg = _yf_history("HYG", "2y")
    await asyncio.sleep(0.3)
    lqd = _yf_history("LQD", "2y")
    if hyg and lqd:
        ml = min(len(hyg), len(lqd))
        ratio = [h / l if l > 0 else 0 for h, l in zip(hyg[-ml:], lqd[-ml:])]
        zs = _calc_zscore(ratio)
        if zs:
            signals["HY스프레드"] = _sig_entry(round(zs["value"], 4), zs["z"], "HYG/LQD")
        else:
            failed.append("HY스프레드")
    else:
        failed.append("HY스프레드")
    await asyncio.sleep(0.3)

    # 3. S&P vs 200MA
    sp = _yf_history("^GSPC", "2y")
    if sp and len(sp) >= 200:
        pct_series = _rolling_ma_pct(sp, 200)
        zs = _calc_zscore(pct_series)
        if zs:
            signals["S&P/200MA"] = _sig_entry(round(zs["value"], 1), zs["z"], "%")
        else:
            failed.append("S&P/200MA")
    else:
        failed.append("S&P/200MA")

    # 4. S&P 50일 모멘텀
    if sp and len(sp) > 50:
        mom = _rolling_momentum(sp, 50)
        zs = _calc_zscore(mom)
        if zs:
            signals["50d모멘텀"] = _sig_entry(round(zs["value"], 1), zs["z"], "%")
        else:
            failed.append("50d모멘텀")
    else:
        failed.append("50d모멘텀")
    await asyncio.sleep(0.3)

    # 5. VIX 텀스트럭처 (^VIX3M / ^VIX)
    vix3m = _yf_history("^VIX3M", "2y")
    if vix3m and vix_data:
        ml = min(len(vix3m), len(vix_data))
        term = [v3 / v if v > 0 else 1.0 for v3, v in zip(vix3m[-ml:], vix_data[-ml:])]
        zs = _calc_zscore(term)
        if zs:
            signals["VIX텀"] = _sig_entry(round(zs["value"], 3), zs["z"], "비율")
        else:
            failed.append("VIX텀")
    else:
        failed.append("VIX텀")
    await asyncio.sleep(0.3)

    # 6. 금리차 (10Y-3M 스프레드, ^TNX - ^IRX)
    #    Bauer & Mertens(2018, SF Fed): 10Y-3M이 10Y-2Y보다 경기침체 예측력 우수
    tnx = _yf_history("^TNX", "2y")
    irx = _yf_history("^IRX", "2y")
    if tnx and irx:
        ml = min(len(tnx), len(irx))
        spread = [t - i for t, i in zip(tnx[-ml:], irx[-ml:])]
        zs = _calc_zscore(spread)
        if zs:
            signals["10Y-3M금리차"] = _sig_entry(round(zs["value"], 2), zs["z"], "%p")
        else:
            failed.append("10Y-3M금리차")
    else:
        failed.append("10Y-3M금리차")

    # 점수
    z_vals = [s["z"] for s in signals.values()]
    if z_vals:
        avg_z = float(np.mean(z_vals))
        score = float(norm.cdf(avg_z) * 100)
    else:
        avg_z, score = 0.0, 50.0

    return {"signals": signals, "score": round(score, 1),
            "avg_z": round(avg_z, 2), "failed": failed,
            "n_signals": len(signals)}


async def compute_kr_signals() -> dict:
    """한국 5개 신호 z-score → {"signals":{}, "score":float, "failed":[]}"""
    import numpy as np
    from scipy.stats import norm

    signals, failed = {}, []

    # 1. KOSPI vs 200MA
    kospi = _krx_kospi_history(days=600)
    if kospi and len(kospi) >= 200:
        pct_series = _rolling_ma_pct(kospi, 200)
        zs = _calc_zscore(pct_series)
        if zs:
            signals["KOSPI/200MA"] = _sig_entry(round(zs["value"], 1), zs["z"], "%")
        else:
            failed.append("KOSPI/200MA")
    else:
        failed.append("KOSPI/200MA")

    # 2. KOSPI 50일 모멘텀
    if kospi and len(kospi) > 50:
        mom = _rolling_momentum(kospi, 50)
        zs = _calc_zscore(mom)
        if zs:
            signals["50d모멘텀"] = _sig_entry(round(zs["value"], 1), zs["z"], "%")
        else:
            failed.append("50d모멘텀")
    else:
        failed.append("50d모멘텀")

    # 3. 외인 순매수 5일합
    frgn = _krx_foreign_net(days=400)
    if frgn and len(frgn) >= 60:
        rolling5 = [sum(frgn[i - 4:i + 1]) for i in range(4, len(frgn))]
        zs = _calc_zscore(rolling5)
        if zs:
            val_억 = round(zs["value"] / 1e8, 0)
            signals["외인5일"] = _sig_entry(val_억, zs["z"], "억")
        else:
            failed.append("외인5일")
    else:
        failed.append("외인5일")

    # 4. USD/KRW (역수)
    usdkrw = _yf_history("KRW=X", "2y")
    if usdkrw:
        zs = _calc_zscore(usdkrw)
        if zs:
            signals["USD/KRW"] = _sig_entry(round(zs["value"], 0), zs["z"], "역수", invert=True)
        else:
            failed.append("USD/KRW")
    else:
        failed.append("USD/KRW")
    await asyncio.sleep(0.3)

    # 5. KOSPI 20일 실현변동성 (역수)
    if kospi and len(kospi) >= 80:
        vol_series = _rolling_realized_vol(kospi, 20)
        zs = _calc_zscore(vol_series)
        if zs:
            signals["실현변동성"] = _sig_entry(round(zs["value"], 1), zs["z"], "역수,%", invert=True)
        else:
            failed.append("실현변동성")
    else:
        failed.append("실현변동성")

    z_vals = [s["z"] for s in signals.values()]
    if z_vals:
        avg_z = float(np.mean(z_vals))
        score = float(norm.cdf(avg_z) * 100)
    else:
        avg_z, score = 0.0, 50.0

    return {"signals": signals, "score": round(score, 1),
            "avg_z": round(avg_z, 2), "failed": failed,
            "n_signals": len(signals)}


def compute_turbulence(sp: list, kospi: list,
                       usdkrw: list, wti: list,
                       window: int = 60):
    """Turbulence Index (마할라노비스 거리). Returns dict or None."""
    import numpy as np
    ml = min(len(sp), len(kospi), len(usdkrw), len(wti))
    if ml < window + 2:
        return None

    def _ret(arr):
        return np.diff(np.log(np.array(arr[-ml:], dtype=float)))

    R = np.column_stack([_ret(sp), _ret(kospi), _ret(usdkrw), _ret(wti)])
    n = len(R)
    if n < window + 1:
        return None

    cov_win = R[-(window + 1):-1]
    cov_mat = np.cov(cov_win, rowvar=False)
    try:
        cov_inv = np.linalg.inv(cov_mat)
    except np.linalg.LinAlgError:
        cov_inv = np.linalg.pinv(cov_mat)

    mean_v = np.mean(cov_win, axis=0)
    diff = R[-1] - mean_v
    turb = float(diff @ cov_inv @ diff)

    # 히스토리 95퍼센타일
    turb_hist = []
    for i in range(window + 1, n):
        cw = R[i - window:i]
        cm = np.cov(cw, rowvar=False)
        try:
            ci = np.linalg.inv(cm)
        except np.linalg.LinAlgError:
            ci = np.linalg.pinv(cm)
        mv = np.mean(cw, axis=0)
        d = R[i] - mv
        turb_hist.append(float(d @ ci @ d))

    p95 = float(np.percentile(turb_hist, 95)) if turb_hist else turb * 2
    return {"value": round(turb, 2), "threshold_95": round(p95, 2),
            "alert": turb > p95}


def _regime_label(score: float) -> tuple:
    """점수 → (emoji, 한글, 영문)"""
    if score >= 70:
        return ("🟢", "공격", "offensive")
    elif score >= 40:
        return ("🟡", "중립", "neutral")
    else:
        return ("🔴", "위기", "defensive")


_REGIME_ORDER = {"offensive": 2, "neutral": 1, "defensive": 0}


def apply_debounce(new_score: float, state: dict) -> dict:
    """디바운스 적용 → state 업데이트 반환."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    _, _, new_regime = _regime_label(new_score)
    prev_regime = state.get("regime", new_regime)
    prev_pending = state.get("pending_regime", "")

    if new_regime == prev_regime:
        state["regime"] = new_regime
        state["consecutive_days"] = state.get("consecutive_days", 0) + 1
        state["pending_regime"] = ""
        state["pending_days"] = 0
    elif new_regime == prev_pending:
        pd = state.get("pending_days", 0) + 1
        state["pending_days"] = pd
        is_worse = _REGIME_ORDER.get(new_regime, 1) < _REGIME_ORDER.get(prev_regime, 1)
        threshold = 2 if is_worse else 3
        if pd >= threshold:
            state["regime"] = new_regime
            state["consecutive_days"] = pd
            state["pending_regime"] = ""
            state["pending_days"] = 0
    else:
        state.setdefault("regime", prev_regime)
        state["pending_regime"] = new_regime
        state["pending_days"] = 1

    state["date"] = today
    return state


async def cmd_regime(mode: str = "current", days: int = 5,
                     regime: str = "", reason: str = "",
                     kr_weight: float = 0.6, us_weight: float = 0.4) -> dict:
    """시장 레짐 판정 메인 함수."""
    state = load_json(REGIME_STATE_FILE, {"history": [], "current": {}})

    # ── override ──
    if mode == "override":
        if regime not in ("crisis", "neutral", "offensive"):
            return {"error": "regime must be one of: crisis, neutral, offensive"}
        mapped = {"crisis": "defensive", "neutral": "neutral", "offensive": "offensive"}[regime]
        today = datetime.now(KST).strftime("%Y-%m-%d")
        cur = state.get("current", {})
        entry = {"date": today, "regime": mapped, "override": True,
                 "reason": reason or "수동 강제",
                 "kr_score": cur.get("kr_score", 0),
                 "us_score": cur.get("us_score", 0),
                 "combined_score": cur.get("combined_score", 0)}
        state["current"].update({"regime": mapped, "override": True,
                                 "override_reason": reason, "date": today,
                                 "pending_regime": "", "pending_days": 0})
        state["history"].append(entry)
        state["history"] = state["history"][-90:]
        save_json(REGIME_STATE_FILE, state)
        e, k, _ = _regime_label({"offensive": 80, "neutral": 55, "defensive": 20}[mapped])
        return {"regime": f"{e} {k}", "mode": "override", "reason": reason, "date": today}

    # ── history ──
    if mode == "history":
        h = state.get("history", [])
        return {"history": h[-days:], "total_records": len(h)}

    # ── current ──
    # 비중 정규화
    tw = kr_weight + us_weight
    if tw > 0:
        kr_weight, us_weight = kr_weight / tw, us_weight / tw

    # 포트폴리오 기반 동적 비중
    try:
        pf = load_json(PORTFOLIO_FILE, {})
        kr_v, us_v = 0.0, 0.0
        for t, info in pf.items():
            if t == "us_stocks":
                for _, ui in info.items():
                    us_v += float(ui.get("qty", 0)) * float(ui.get("avg_price", 0))
            elif t not in ("cash_krw", "cash_usd"):
                kr_v += float(info.get("qty", 0)) * float(info.get("avg_price", 0))
        us_v_krw = us_v * 1400
        total_v = kr_v + us_v_krw
        if total_v > 0:
            kr_weight = kr_v / total_v
            us_weight = us_v_krw / total_v
    except Exception:
        pass

    # 신호 계산
    us_r = await compute_us_signals()
    kr_r = await compute_kr_signals()

    us_score = us_r["score"]
    kr_score = kr_r["score"]
    combined = round(kr_weight * kr_score + us_weight * us_score, 1)

    # Turbulence
    turbulence = None
    try:
        sp = _yf_history("^GSPC", "1y")
        kospi = _krx_kospi_history(400)
        usdkrw = _yf_history("KRW=X", "1y")
        wti = _yf_history("CL=F", "1y")
        turbulence = compute_turbulence(sp, kospi, usdkrw, wti)
    except Exception as e:
        print(f"[cmd_regime] turbulence 오류: {e}")

    # 디바운스
    cur = state.get("current", {})
    cur["kr_score"] = kr_score
    cur["us_score"] = us_score
    cur["combined_score"] = combined
    cur.pop("override", None)
    cur.pop("override_reason", None)
    cur = apply_debounce(combined, cur)
    state["current"] = cur

    # 히스토리 기록
    today = datetime.now(KST).strftime("%Y-%m-%d")
    h_entry = {"date": today, "kr_score": kr_score, "us_score": us_score,
               "combined_score": combined, "regime": cur["regime"]}
    hist = state.get("history", [])
    if hist and hist[-1].get("date") == today:
        hist[-1] = h_entry
    else:
        hist.append(h_entry)
    state["history"] = hist[-90:]
    save_json(REGIME_STATE_FILE, state)

    # 결과 조립
    e_c, k_c, en_c = _regime_label(combined)
    e_us, k_us, _ = _regime_label(us_score)
    e_kr, k_kr, _ = _regime_label(kr_score)

    # 디바운스 메시지
    if cur.get("pending_regime"):
        pe, pk, _ = _regime_label(
            {"offensive": 80, "neutral": 55, "defensive": 20}.get(cur["pending_regime"], 55))
        db_msg = f"→{pe}{pk} 전환 대기 {cur.get('pending_days', 0)}일차"
    else:
        db_msg = f"{e_c} {cur.get('consecutive_days', 1)}일차 (확정)"

    def _fmt_sigs(sigs):
        return " | ".join(f"{k}: {v['value']} (z={v['z']:+.1f})" for k, v in sigs.items()) or "데이터 없음"

    result = {
        "regime": f"{e_c} {k_c}",
        "regime_en": en_c,
        "combined_score": combined,
        "kr": {"score": kr_score, "emoji": e_kr, "label": k_kr,
               "signals": kr_r["signals"], "text": _fmt_sigs(kr_r["signals"]),
               "failed": kr_r["failed"], "weight": round(kr_weight, 2)},
        "us": {"score": us_score, "emoji": e_us, "label": k_us,
               "signals": us_r["signals"], "text": _fmt_sigs(us_r["signals"]),
               "failed": us_r["failed"], "weight": round(us_weight, 2)},
        "debounce": db_msg,
        "date": today,
    }
    if turbulence:
        t_status = "⚠️ 경고" if turbulence["alert"] else "정상"
        result["turbulence"] = {
            "value": turbulence["value"],
            "threshold_95": turbulence["threshold_95"],
            "alert": turbulence["alert"],
            "text": f"{turbulence['value']} ({t_status}, 95p={turbulence['threshold_95']})",
        }

    all_failed = us_r["failed"] + kr_r["failed"]
    if all_failed:
        result["warnings"] = f"데이터 실패: {', '.join(all_failed)}"

    return result
