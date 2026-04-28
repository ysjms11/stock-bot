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
from dotenv import load_dotenv

load_dotenv()

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

_DATA_DIR = os.environ.get("DATA_DIR", "/data")
os.makedirs(_DATA_DIR, exist_ok=True)
_DB_PATH = f"{_DATA_DIR}/stock.db"

WATCHLIST_FILE    = f"{_DATA_DIR}/watchlist.json"
STOPLOSS_FILE     = f"{_DATA_DIR}/stoploss.json"
US_WATCHLIST_FILE = f"{_DATA_DIR}/us_watchlist.json"
DART_SEEN_FILE    = f"{_DATA_DIR}/dart_seen.json"
PORTFOLIO_FILE    = f"{_DATA_DIR}/portfolio.json"
WATCHALERT_FILE   = f"{_DATA_DIR}/watchalert.json"
WATCH_SENT_FILE      = f"{_DATA_DIR}/watch_sent.json"
STOPLOSS_SENT_FILE   = f"{_DATA_DIR}/stoploss_sent.json"
US_HOLDINGS_SENT_FILE = f"{_DATA_DIR}/us_holdings_sent.json"
DECISION_LOG_FILE = f"{_DATA_DIR}/decision_log.json"
COMPARE_LOG_FILE  = f"{_DATA_DIR}/compare_log.json"
WATCHLIST_LOG_FILE = f"{_DATA_DIR}/watchlist_log.json"
EVENTS_FILE       = f"{_DATA_DIR}/events.json"
WEEKLY_BASE_FILE      = f"{_DATA_DIR}/weekly_base.json"
UNIVERSE_FILE         = f"{_DATA_DIR}/stock_universe.json"
CONSENSUS_CACHE_FILE      = f"{_DATA_DIR}/consensus_cache.json"
PORTFOLIO_HISTORY_FILE    = f"{_DATA_DIR}/portfolio_history.json"
TRADE_LOG_FILE            = f"{_DATA_DIR}/trade_log.json"
SECTOR_FLOW_CACHE_FILE    = f"{_DATA_DIR}/sector_flow_cache.json"
SECTOR_ROTATION_FILE      = f"{_DATA_DIR}/sector_rotation.json"
SUPPLY_HISTORY_FILE       = f"{_DATA_DIR}/supply_history.json"
REPORTS_FILE              = f"{_DATA_DIR}/reports.json"
REGIME_STATE_FILE         = f"{_DATA_DIR}/regime_state.json"
MACRO_SENT_FILE           = f"{_DATA_DIR}/macro_sent.json"

GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
_BACKUP_GIST_ENV  = "BACKUP_GIST_ID"
_BACKUP_FILES_LIST = [
    STOPLOSS_FILE, PORTFOLIO_FILE,
    WATCHALERT_FILE, WATCHLIST_LOG_FILE, PORTFOLIO_HISTORY_FILE,
    TRADE_LOG_FILE, CONSENSUS_CACHE_FILE, DECISION_LOG_FILE,
    REGIME_STATE_FILE,
    # WATCHLIST_FILE / US_WATCHLIST_FILE 제외 — watchalert.json 단일 소스.
    # REPORTS_FILE 제외 — 1.4MB+ Gist 크기 초과. iCloud 백업으로 커버.
]

MACRO_SYMBOLS = {
    "VIX":    "^VIX",
    "WTI":    "CL=F",
    "GOLD":   "GC=F",
    "COPPER": "HG=F",
    "DXY":    "DX-Y.NYB",
    "US10Y":  "^TNX",
    "SP500":  "^GSPC",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 환경변수 기반 데이터 복원 (Railway Volume 미마운트 시 fallback)
# Railway Variables에 BACKUP_PORTFOLIO, BACKUP_STOPLOSS 등을 JSON 문자열로 설정하면
# /data/ 파일이 없을 때 자동 복원됨
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_BACKUP_MAP = {
    "BACKUP_PORTFOLIO":    PORTFOLIO_FILE,
    "BACKUP_STOPLOSS":     STOPLOSS_FILE,
    "BACKUP_WATCHALERT":   WATCHALERT_FILE,
    "BACKUP_DECISION_LOG": DECISION_LOG_FILE,
    "BACKUP_COMPARE_LOG":  COMPARE_LOG_FILE,
    "BACKUP_EVENTS":       EVENTS_FILE,
    "BACKUP_WEEKLY_BASE":  WEEKLY_BASE_FILE,
    # BACKUP_WATCHLIST / BACKUP_US_WATCHLIST 제거 — watchalert 단일 소스.
}
# 하위호환: 구 BACKUP_WATCHLIST/US_WATCHLIST 환경변수는 watchalert.json이 없을 때만 무시되지 않음.
# watchalert.json이 있으면 무조건 그것을 단일 소스로 사용 (레거시 env 무시).
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

# 레거시 환경변수 가드: watchalert.json이 존재하면 BACKUP_WATCHLIST/US_WATCHLIST 무시 로그만.
if os.path.exists(WATCHALERT_FILE):
    for _legacy_env in ("BACKUP_WATCHLIST", "BACKUP_US_WATCHLIST"):
        if os.environ.get(_legacy_env):
            print(f"[무시] {_legacy_env} (watchalert.json 단일 소스 사용)")

_token_cache = {"token": None, "expires": None}
TOKEN_CACHE_FILE = f"{_DATA_DIR}/token_cache.json"


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

# KNU 감성사전 메모리 캐시
_KNU_SENTI_CACHE: dict | None = None


def _load_knu_senti_lex() -> dict:
    """KNU 한국어 감성사전 로드 (최초 1회만 파일 읽기, 이후 메모리 캐싱)."""
    global _KNU_SENTI_CACHE
    if _KNU_SENTI_CACHE is not None:
        return _KNU_SENTI_CACHE
    path = os.path.join(_DATA_DIR, "knu_senti_lex.json")
    try:
        with open(path, encoding="utf-8") as f:
            _KNU_SENTI_CACHE = json.load(f)
    except Exception:
        _KNU_SENTI_CACHE = {}
    return _KNU_SENTI_CACHE


# 금융 특화 다단어 구문 점수 (KNU 개별 단어보다 우선 적용, 절댓값 클수록 강함)
# 양수=긍정, 음수=부정. 문자열 포함 여부로 매칭 (긴 구문 먼저 검사)
_FINANCE_PHRASE_SCORES: list[tuple[str, int]] = sorted([
    # 컨텍스트 반전: 감소/축소가 긍정인 경우
    ("대차잔고 감소", 4), ("대차잔고감소", 4),
    ("대차거래 잔고감소", 4), ("대차거래잔고감소", 4),
    ("공매도잔고 감소", 4), ("공매도잔고감소", 4),
    ("공매도 감소", 3), ("공매도감소", 3),
    ("공매도 축소", 3), ("공매도축소", 3),
    ("부채비율 감소", 2), ("부채비율감소", 2),
    ("적자 감소", 2), ("적자감소", 2),
    ("적자 축소", 2), ("적자축소", 2),
    # 컨텍스트 반전: 증가가 부정인 경우
    ("대차잔고 증가", -4), ("대차잔고증가", -4),
    ("공매도 증가", -3), ("공매도증가", -3),
    ("부채비율 증가", -2), ("부채비율증가", -2),
    # 강력 긍정
    ("흑자전환", 5), ("어닝서프라이즈", 5), ("어닝 서프라이즈", 5),
    ("깜짝실적", 4), ("깜짝 실적", 4), ("사상 최대", 4), ("사상최대", 4),
    ("최대 실적", 4), ("최대실적", 4), ("역대 최대", 4), ("역대최대", 4),
    ("목표가 상향", 4), ("목표가상향", 4),
    ("투자의견 상향", 4), ("투자의견상향", 4),
    ("통 큰 배당", 4), ("특별배당", 4),
    ("자사주 매입", 3), ("자사주매입", 3),
    ("배당 증가", 3), ("배당증가", 3), ("배당 확대", 3), ("배당확대", 3),
    ("영업이익 증가", 3), ("영업이익증가", 3),
    ("매출 증가", 2), ("매출증가", 2),
    ("실적 개선", 3), ("실적개선", 3),
    ("수주 확대", 3), ("수주확대", 3),
    ("계약 체결", 2), ("계약체결", 2),
    ("공급 계약", 2), ("공급계약", 2),
    ("수출 계약", 2), ("수출계약", 2),
    ("계약 성사", 3), ("계약성사", 3),
    ("허가 획득", 4), ("허가획득", 4),
    ("FDA 허가", 4), ("임상 허가", 3),
    ("기술 돌파", 3), ("돌파구", 3),
    ("수주잔고", 2),
    ("독점 공급", 3), ("독점공급", 3), ("독점 계약", 3),
    ("영업이익 흑자", 3),
    ("지지선", 2), ("저항선", 0),
    ("신고가 달성", 3), ("52주 신고가", 3), ("연고점 돌파", 3),
    ("수출 증가", 2), ("수출증가", 2),
    ("매수세 집중", 2), ("매수세 유입", 2),
    ("순매수 지속", 2), ("외인 순매수", 2),
    ("연속 상승", 2), ("연속 매수", 2),
    ("승소", 3),
    ("구조조정 효과", 2),  # 구조조정 자체는 -3이지만 "효과"와 결합 시 긍정
    ("부담 완화", 2), ("리스크 완화", 2),
    ("효과", 1),  # "효과로"에서 "과로(-1)" 오매칭 방지용 covered
    # KNU 오매칭 방지 — 양성 복합어 커버
    ("상한가", 3),           # "상한"(-2) KNU 오매칭 방지
    ("흑자", 2),             # 단독 흑자 (흑자전환은 이미 별도 +5)
    ("고성장", 2),
    ("판매 증가", 2), ("판매증가", 2),
    ("판매 급증", 3), ("판매급증", 3),
    ("수요 증가", 2), ("수요증가", 2),
    # KNU 오매칭 방지 — 음성 복합어 커버
    ("흥행 부진", -3), ("흥행부진", -3),
    ("배당 감소", -3), ("배당감소", -3),
    ("악화", -2),            # 악화됐다, 악화 우려 등 standalone
    ("침체", -2),            # 경기침체, 업황침체 등
    ("무산", -3),            # 계약 무산, 협상 무산 등
    ("규제 리스크", -3),
    ("원가 상승", -2), ("원가상승", -2),
    ("대손비용 증가", -3),
    ("부실", -2),            # 부실채권, 부실기업 등 (부실 확대는 이미 -3)
    # 강력 부정 — 맥락 반전 (긍정어가 부정 맥락에서 등장)
    ("허가 반려", -5), ("임상 실패", -4), ("허가 취소", -4),
    ("수익성 악화", -3), ("수익 악화", -3),
    ("수익성 압박", -3), ("수익성압박", -3),  # "수익"(KNU+1) 오매칭 방지
    ("영업적자", -4),  # "업적"(KNU+1) 오매칭 방지
    ("가치 하락", -3),  # "가치"(KNU+1) 오매칭 방지
    ("손실 확대", -3), ("손실확대", -3),
    ("연체율 상승", -3), ("부실 확대", -3),
    ("약세장", -3), ("연저점", -3),
    ("급락", -3), ("급감", -3),
    ("매도 폭탄", -4),
    ("공매도잔고 급증", -4),
    ("재고손실", -3),
    ("수출 감소", -2), ("수출감소", -2),
    ("실적 악화", -3), ("실적악화", -3),
    ("실적 쇼크", -5), ("실적쇼크", -5),  # "목표가 줄줄이 하향" 등 쇼크 표현
    ("수주 취소", -3), ("계약 취소", -3),
    ("유상증자", -3),   # 주식 희석 이슈
    # 강력 부정
    ("리스크 부각", -3), ("리스크 확대", -3),
    ("수급 악화", -2), ("수급악화", -2),
    ("적자전환", -5), ("어닝쇼크", -5), ("어닝 쇼크", -5),
    ("상장폐지", -5), ("상폐", -4),
    ("목표가 하향", -4), ("목표가하향", -4),
    ("투자의견 하향", -4), ("투자의견하향", -4),
    ("영업이익 급감", -4), ("영업이익급감", -4),  # "이익"(KNU+2) 오매칭 방지
    ("이익 급감", -3), ("이익급감", -3),
    ("영업이익 감소", -3), ("영업이익감소", -3),
    ("부채비율 급증", -3),  # "부채비율 증가"의 급증 변형
    ("적자 확대", -4), ("적자확대", -4),
    ("매출 감소", -2), ("매출감소", -2),
    ("이익 감소", -2), ("이익감소", -2),
    ("구조조정", -3), ("감자", -4),
    # 긍정 추가 (coverage 보강)
    ("양호", 2),             # 컨센서스 대비 실적 양호 등
    ("매출 성장", 3), ("매출성장", 3),
    ("수주 급증", 4), ("수주급증", 4),
], key=lambda x: -len(x[0]))  # 긴 구문 먼저 매칭

# 기계적 순위 기사 필터 패턴 (해당하면 neutral 즉시 반환)
_RANKING_PATTERNS = [
    r"순매수\s*상위",
    r"순매도\s*상위",
    r"체결강도\s*상위",
    r"등락률\s*상위",
    r"거래량\s*상위",
    r"상위\s*\d+\s*종목",
    r"상위\s*종목",
    r"시총\s*상위",
    r"배당수익률\s*상위",
    r"\d+종목\s*(집계|포함|선정)",
    r"상위에\s*(오른|든)\s*종목",
    r"상한가\s*종목",
    r"하한가\s*종목",
    r"종목\s*\d+\s*개",
]
_RANKING_RE = re.compile("|".join(_RANKING_PATTERNS))

# 미국 뉴스 영문 감성 키워드 사전 (기존 유지, 금융 특화 추가)
_US_POSITIVE_KEYWORDS = [
    "surge", "soar", "rally", "beat", "upgrade", "bullish", "growth",
    "record", "outperform", "buy", "strong", "raise", "profit", "gain",
    "upside", "breakout", "momentum", "dividend", "expand", "turnaround",
    "surprise", "exceeded", "record high", "beat estimates", "raised guidance",
]
_US_NEGATIVE_KEYWORDS = [
    "drop", "plunge", "crash", "miss", "downgrade", "bearish", "decline",
    "loss", "underperform", "sell", "weak", "cut", "warning", "risk",
    "layoff", "recall", "lawsuit", "investigation", "bankruptcy",
    "missed estimates", "lowered guidance", "earnings shock",
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


_DEFAULT_KR_WATCH = {
    "009540": "HD한국조선해양", "298040": "효성중공업",
    "010120": "LS ELECTRIC", "267260": "HD현대일렉트릭",
    "034020": "두산에너빌리티",
}


def load_watchlist():
    """하위호환 wrapper: watchalert 기반 {ticker: name}.
    watchalert.json 존재 시 그 내용을 그대로 반환 (빈 dict라도).
    파일 자체가 없으면 최초 실행이므로 기본 5종목 seed."""
    if os.path.exists(WATCHALERT_FILE):
        return load_kr_watch_dict()
    return dict(_DEFAULT_KR_WATCH)


def load_stoploss():
    return load_json(STOPLOSS_FILE, {})


_DEFAULT_US_WATCH = {
    "TSLA": {"name": "테슬라", "qty": 12},
    "CRSP": {"name": "크리스퍼", "qty": 70},
    "AMD": {"name": "AMD", "qty": 17},
    "LITE": {"name": "루멘텀", "qty": 4},
}


def load_us_watchlist():
    """하위호환 wrapper: watchalert 기반 {ticker: {name, qty}}.
    watchalert.json 존재 시 그 내용을 그대로 반환 (빈 dict라도).
    파일 자체가 없으면 최초 실행이므로 기본 US seed."""
    if os.path.exists(WATCHALERT_FILE):
        return load_us_watch_dict()
    return dict(_DEFAULT_US_WATCH)


def load_dart_seen():
    return load_json(DART_SEEN_FILE, {"ids": []})


def load_watchalert():
    return load_json(WATCHALERT_FILE, {})


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 워치리스트 단일화 헬퍼 (watchalert.json 기반)
# market 필드 없으면 _is_us_ticker()로 자동 추론
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _wa_market(ticker: str, entry: dict) -> str:
    m = (entry or {}).get("market")
    if m in ("KR", "US"):
        return m
    return "US" if _is_us_ticker(ticker) else "KR"


def load_kr_watch_tickers() -> list:
    """watchalert에서 market==KR 종목 코드 리스트."""
    wa = load_watchalert()
    return [t for t, v in wa.items() if _wa_market(t, v) == "KR"]


def load_us_watch_tickers() -> list:
    """watchalert에서 market==US 종목 코드 리스트."""
    wa = load_watchalert()
    return [t for t, v in wa.items() if _wa_market(t, v) == "US"]


def load_kr_watch_dict() -> dict:
    """구 watchlist.json 호환 형식 {ticker: name}."""
    wa = load_watchalert()
    return {t: (v.get("name") or t) for t, v in wa.items() if _wa_market(t, v) == "KR"}


def load_us_watch_dict() -> dict:
    """구 us_watchlist.json 호환 형식 {ticker: {name, qty}}."""
    wa = load_watchalert()
    return {
        t: {"name": v.get("name") or t, "qty": int(v.get("qty") or 0)}
        for t, v in wa.items()
        if _wa_market(t, v) == "US"
    }

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


def _insert_consensus_history(kr_data: dict, us_data: dict):
    """수집된 컨센서스를 consensus_history 테이블에 UPSERT."""
    today = datetime.now(KST).strftime("%Y%m%d")
    now_str = datetime.now(KST).isoformat()
    rows = []
    for symbol, entry in kr_data.items():
        avg = entry.get("avg")
        if not avg:
            continue
        rows.append((
            today, symbol,
            float(avg), float(entry.get("high", 0) or 0), float(entry.get("low", 0) or 0),
            int(entry.get("buy", 0) or 0), int(entry.get("hold", 0) or 0), int(entry.get("sell", 0) or 0),
            now_str,
        ))
    if not rows:
        return
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executemany("""
            INSERT INTO consensus_history
            (trade_date, symbol, target_avg, target_high, target_low, buy_count, hold_count, sell_count, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, symbol) DO UPDATE SET
                target_avg=excluded.target_avg, target_high=excluded.target_high, target_low=excluded.target_low,
                buy_count=excluded.buy_count, hold_count=excluded.hold_count, sell_count=excluded.sell_count,
                collected_at=excluded.collected_at
        """, rows)
        conn.commit()
        conn.close()
        print(f"[consensus_history] {len(rows)}건 저장 ({today})")
    except Exception as e:
        print(f"[consensus_history] DB 저장 실패: {e}")


async def update_consensus_cache(kr_tickers: dict | None = None) -> dict:
    """포트폴리오+워치리스트 전체 컨센서스를 배치 수집해 consensus_cache.json에 저장.
    기존 avg는 prev_avg로 보존해 주간 변동 추적 가능.
    실패 종목은 기존 캐시 유지.

    Args:
        kr_tickers: {ticker: name} 형태로 전달하면 해당 KR 종목만 수집 (부분 업데이트).
                    None이면 portfolio+watchlist 전체 자동 결정.
                    kr_tickers가 주어지면 US 섹션은 기존 캐시를 그대로 유지.
    """
    import asyncio as _aio
    old_cache = load_json(CONSENSUS_CACHE_FILE, {})
    old_kr = old_cache.get("kr", {})
    old_us = old_cache.get("us", {})

    partial_mode = kr_tickers is not None  # True면 kr만 갱신

    if not partial_mode:
        # 수집 대상 티커 자동 결정
        portfolio = load_json(PORTFOLIO_FILE, {})
        kr_tickers = {
            t: (v.get("name", t) if isinstance(v, dict) else t)
            for t, v in portfolio.items()
            if t != "us_stocks" and not _is_us_ticker(t)
        }
        us_tickers: dict = {
            t: (v.get("name", t) if isinstance(v, dict) else t)
            for t, v in portfolio.get("us_stocks", {}).items()
        }
        # 한국 워치리스트 추가
        for t, n in load_watchlist().items():
            if t not in kr_tickers and not _is_us_ticker(t):
                kr_tickers[t] = n
        # 미국 워치리스트 추가
        for t, v in load_us_watchlist().items():
            if t not in us_tickers:
                us_tickers[t] = v.get("name", t) if isinstance(v, dict) else str(v)
    else:
        us_tickers = {}  # 부분 업데이트 시 US 수집 건너뜀

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

    if partial_mode:
        # 부분 업데이트: kr 섹션만 덮어쓰고 us는 기존 캐시 유지
        merged_kr = {**old_kr, **new_kr}
        cache = {
            "updated": datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S%z"),
            "kr": merged_kr,
            "us": old_us,
        }
        save_json(CONSENSUS_CACHE_FILE, cache)
        _insert_consensus_history(new_kr, {})
        print(f"[consensus_cache] 부분 저장 완료: KR {len(new_kr)}종목 갱신 (전체 {len(merged_kr)})")
        return cache

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
    _insert_consensus_history(new_kr, new_us)
    print(f"[consensus_cache] 저장 완료: KR {len(new_kr)}종목, US {len(new_us)}종목")
    return cache


def detect_consensus_changes(old_kr: dict, new_kr: dict, target_pct: float = 5.0, detect_new_cover: bool = False) -> list:
    """컨센서스 변화 감지.
    Returns: [{"ticker", "name", "type", "detail"}, ...]
    type: "target_up" / "target_down" / "opinion_change" / "new_cover"
    """
    changes = []
    for ticker, new_data in new_kr.items():
        old_data = old_kr.get(ticker)
        new_avg = new_data.get("avg", 0) or 0

        if old_data is None:
            if detect_new_cover and new_avg > 0:
                changes.append({"ticker": ticker, "name": new_data.get("name", ticker),
                               "type": "new_cover", "detail": f"목표가 {new_avg:,.0f}"})
            continue

        old_avg = old_data.get("avg", 0) or 0
        if old_avg > 0 and new_avg > 0:
            pct = (new_avg - old_avg) / old_avg * 100
            if pct >= target_pct:
                changes.append({"ticker": ticker, "name": new_data.get("name", ticker),
                               "type": "target_up", "detail": f"{old_avg:,.0f}→{new_avg:,.0f} (+{pct:.1f}%)"})
            elif pct <= -target_pct:
                changes.append({"ticker": ticker, "name": new_data.get("name", ticker),
                               "type": "target_down", "detail": f"{old_avg:,.0f}→{new_avg:,.0f} ({pct:.1f}%)"})

        # 투자의견 변경
        def _dominant(d):
            b, h, s = d.get("buy", 0), d.get("hold", 0), d.get("sell", 0)
            if b >= h and b >= s and b > 0: return "매수"
            if s >= b and s >= h and s > 0: return "매도"
            return "중립"
        old_op = _dominant(old_data)
        new_op = _dominant(new_data)
        if old_op != new_op:
            changes.append({"ticker": ticker, "name": new_data.get("name", ticker),
                           "type": "opinion_change", "detail": f"{old_op}→{new_op}"})

    return changes


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
            cached = ws_manager.get_cached_price(sym)
            if cached is not None:
                price = float(cached)
            else:
                d = await _fetch_us_price_simple(sym, token)
                price = float(d.get("last", 0) or 0)
                await asyncio.sleep(0.2)
            qty   = info.get("qty", 0)
            eval_usd = round(price * qty, 2)
            us_eval_usd += eval_usd
            holdings[sym] = {"price": price, "qty": qty, "eval_usd": eval_usd}
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
    s = _get_session()
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
    # 1) 메모리 캐시 확인
    if _token_cache["token"] and _token_cache["expires"] and _token_cache["expires"] > now:
        return _token_cache["token"]
    # 2) 파일 캐시 확인 (재시작 후에도 23시간 재사용)
    try:
        if os.path.exists(TOKEN_CACHE_FILE):
            with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            exp = datetime.fromisoformat(cached.get("expires", "2000-01-01"))
            if cached.get("token") and exp > now:
                _token_cache["token"] = cached["token"]
                _token_cache["expires"] = exp
                return cached["token"]
    except Exception:
        pass
    # 3) 신규 발급
    url = f"{KIS_BASE_URL}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET}
    session = _get_session()
    async with session.post(url, headers={"content-type": "application/json"}, json=body) as resp:
        data = await resp.json()
        token = data.get("access_token")
        if token:
            expires = now + timedelta(hours=23)
            _token_cache["token"] = token
            _token_cache["expires"] = expires
            try:
                os.makedirs(os.path.dirname(TOKEN_CACHE_FILE), exist_ok=True)
                with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"token": token, "expires": expires.isoformat()}, f)
            except Exception:
                pass
        return token


async def get_investor_trend(ticker, token):
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET, "tr_id": "FHKST01010900"
    }
    session = _get_session()
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
    session = _get_session()
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
    session = _get_session()
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


async def kis_stock_price(ticker, token, session=None):
    s = session or aiohttp.ClientSession()
    try:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        return d.get("output", {})
    finally:
        if session is None:
            await s.close()


async def kis_stock_info(ticker, token):
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/search-stock-info",
        "CTPF1002R", token,
        {"PRDT_TYPE_CD": "300", "PDNO": ticker})
    return d.get("output", {})


async def kis_investor_trend(ticker, token):
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-investor",
        "FHKST01010900", token,
        {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
    return d.get("output", [])


async def kis_credit_balance(ticker, token):
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-credit-by-company",
        "FHKST01010600", token,
        {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
    return d.get("output", {})


async def kis_short_selling(ticker, token):
    today = datetime.now().strftime("%Y%m%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-short-selling",
        "FHKST01010700", token,
        {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker,
         "fid_begin_dt": week_ago, "fid_end_dt": today})
    return d.get("output", [])


async def kis_volume_rank_api(token):
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/volume-rank",
        "FHPST01710000", token,
        {"fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20171",
         "fid_input_iscd": "0000", "fid_div_cls_code": "0", "fid_blng_cls_code": "0",
         "fid_trgt_cls_code": "111111111", "fid_trgt_exls_cls_code": "000000",
         "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "", "fid_input_date_1": ""})
    return d.get("output", [])


async def kis_foreigner_trend(token):
    today = datetime.now().strftime("%Y%m%d")
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-foreigner-trend",
        "FHPTJ04060100", token,
        {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": "0000", "fid_input_date_1": today})
    if not d:
        return []
    output = d.get("output") or []
    return [r for r in output if r is not None]


async def kis_sector_price(token):
    today = datetime.now().strftime("%Y%m%d")
    s = _get_session()
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


async def _fetch_market_investor_flow(token: str, market: str) -> dict:
    """시장별 투자자매매동향(일별) FHPTJ04040000.
    market: "KSP"(코스피) or "KSQ"(코스닥)
    Returns: {"frgn": 백만원, "orgn": 백만원, "prsn": 백만원}
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    params = {
        "fid_cond_mrkt_div_code": "U",
        "fid_input_iscd": "0001",
        "fid_input_date_1": today,
        "fid_input_iscd_1": market,
        "fid_input_date_2": today,
        "fid_input_iscd_2": "0001",
    }
    try:
        s = _get_session()
        _, d = await _kis_get(
            s,
            "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market",
            "FHPTJ04040000",
            token,
            params,
        )
        if not d or d.get("rt_cd") != "0":
            return {"frgn": 0, "orgn": 0, "prsn": 0}
        rows = d.get("output") or []
        if isinstance(rows, list) and rows:
            row = rows[0]
        elif isinstance(rows, dict):
            row = rows
        else:
            return {"frgn": 0, "orgn": 0, "prsn": 0}
        frgn = int(row.get("frgn_ntby_tr_pbmn", 0) or 0)
        orgn = int(row.get("orgn_ntby_tr_pbmn", 0) or 0)
        prsn = int(row.get("prsn_ntby_tr_pbmn", 0) or 0)
        return {"frgn": frgn, "orgn": orgn, "prsn": prsn}
    except Exception:
        return {"frgn": 0, "orgn": 0, "prsn": 0}


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
            s = _get_session()
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
    s = _get_session()
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
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price-detail",
        "HHDFS76200200", token,
        {"AUTH": "", "EXCD": excd, "SYMB": symbol})
    out = d.get("output", {})
    p = float(out.get("last", 0) or out.get("t_xprc", 0) or 0)
    if p > 0:
        return out
    fallback_codes = [c for c in ("NYS", "NAS", "AMS") if c != excd]
    for fb in fallback_codes:
        await asyncio.sleep(0.2)
        _, d2 = await _kis_get(s, "/uapi/overseas-price/v1/quotations/price-detail",
            "HHDFS76200200", token,
            {"AUTH": "", "EXCD": fb, "SYMB": symbol})
        out2 = d2.get("output", {})
        p2 = float(out2.get("last", 0) or out2.get("t_xprc", 0) or 0)
        if p2 > 0:
            print(f"[excd fallback detail] {symbol}: {excd}→{fb} 성공")
            return out2
    return out  # 모든 거래소에서 0이면 원래 결과 반환


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


def _previous_trading_day(date_str: str) -> str:
    """YYYYMMDD → 직전 영업일 YYYYMMDD (주말만 건너뜀, 공휴일 미반영).
    공휴일엔 KIS API가 빈응답 반환하므로 호출자가 추가 fallback 처리 권장."""
    dt = datetime.strptime(date_str, "%Y%m%d") - timedelta(days=1)
    while dt.weekday() >= 5:  # 5=토, 6=일
        dt -= timedelta(days=1)
    return dt.strftime("%Y%m%d")


async def kis_investor_trend_history(ticker: str, token: str, n_days: int = 5, session=None) -> list:
    """종목별 투자자 일별 수급 히스토리 (FHPTJ04160001).

    Returns: [{date, foreign_net, institution_net, individual_net,
               foreign_buy, foreign_sell}, ...] 최신순, 최대 n_days일

    Fallback: KIS API가 today 지정 호출에 빈 응답을 주는 경우(장중 미확정, 공휴일 등)
    직전 영업일로 한 번 재시도한 뒤에도 비면 빈 리스트 반환.
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    s = session or aiohttp.ClientSession()

    async def _call(base_date: str):
        _, d = await _kis_get(s,
            "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
            "FHPTJ04160001", token,
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         ticker,
                "FID_INPUT_DATE_1":       base_date,
                "FID_ORG_ADJ_PRC":        "",
                "FID_ETC_CLS_CODE":       "",
            })
        # output1=단일 현재가 dict, output2=일별 수급 list (최대 30일)
        return d.get("output2") if d else None

    try:
        rows = await _call(today)
        if not rows:  # 장중 빈 응답 → 직전 영업일로 1회 재시도
            rows = await _call(_previous_trading_day(today))
    finally:
        if session is None:
            await s.close()
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
    wl = load_watchlist()
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

    # ── 조건 6: 추정수급 외인+기관 동시 순매도 ──
    try:
        est = await kis_investor_trend_estimate(ticker, token)
        f_est = est.get("foreign_est_net", 0)
        i_est = est.get("institution_est_net", 0)
        both_est = f_est < 0 and i_est < 0
        conditions.append({"condition": "추정수급외인+기관동시매도", "triggered": both_est,
                            "detail": f"외인{f_est:+,} 기관{i_est:+,} (추정)"})
    except Exception as e:
        conditions.append({"condition": "추정수급외인+기관동시매도", "triggered": False, "detail": f"오류: {e}"})

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
    s = _get_session()
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
        s = _get_session()
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


async def kis_daily_short_sale(ticker: str, token: str, n: int = 10, session=None) -> list:
    """국내주식 공매도 일별추이 (FHPST04830000).

    Returns: [{date, short_vol, total_vol, short_ratio, close}, ...]
    날짜범위 파라미터로 조회 (페이징 없음, 범위 내 전체 반환).
    """
    try:
        today = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=int(n * 1.6))).strftime("%Y%m%d")
        s = session or aiohttp.ClientSession()
        try:
            _, d = await _kis_get(s,
                "/uapi/domestic-stock/v1/quotations/daily-short-sale",
                "FHPST04830000", token,
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD":         ticker,
                    "FID_INPUT_DATE_1":       start,
                    "FID_INPUT_DATE_2":       today,
                })
        finally:
            if session is None:
                await s.close()
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
        s = _get_session()
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
    """뉴스 헤드라인 감성 분석 (KNU 사전 + 금융 특화 규칙).

    알고리즘:
    1. 기계적 순위 기사 패턴 → 즉시 neutral
    2. FINANCE_PHRASE_SCORES (다단어, 우선 적용) → score 누적
    3. KNU 사전 단어 점수 (finance phrase 커버 범위 제외) → score 누적
    4. 부정어 반전 (않/없/못/안, 앞 키워드 3자 이내) → 부호 반전
    5. score > 0 → positive | score < 0 → negative | else → neutral

    Returns: {positive: [...], negative: [...], neutral: [...], summary: str}
    """
    knu = _load_knu_senti_lex()
    positive, negative, neutral = [], [], []

    for item in news_items:
        title = item.get("title", "")
        entry = {**item}

        # 1. 기계적 순위 기사 필터
        if _RANKING_RE.search(title):
            entry["sentiment"] = "neutral"
            entry["matched_keywords"] = ["[순위기사]"]
            entry["score"] = 0
            neutral.append(entry)
            continue

        score = 0
        matched = []
        covered = set()  # 이미 finance phrase가 커버한 문자 인덱스

        # 2. 금융 특화 구문 (다단어, 긴 것 먼저 — 이미 covered된 위치는 스킵)
        for phrase, phrase_score in _FINANCE_PHRASE_SCORES:
            start = 0
            while True:
                idx = title.find(phrase, start)
                if idx == -1:
                    break
                # 더 긴 구문이 이미 이 위치를 커버했으면 스킵 (중복 점수 방지)
                if not covered.isdisjoint(range(idx, idx + len(phrase))):
                    start = idx + len(phrase)
                    continue
                # 구문 직후 부정어 반전 확인
                suffix = title[idx + len(phrase): idx + len(phrase) + 10]
                actual_score = -phrase_score if re.search(r'않|없(?!지만|더라도)|못|안\s|아닌(?!지만|데)|아니(?!지만|더라도|라도)', suffix) else phrase_score
                score += actual_score
                matched.append(f"{phrase}({'+' if actual_score > 0 else ''}{actual_score})")
                for i in range(idx, idx + len(phrase)):
                    covered.add(i)
                start = idx + len(phrase)

        # 3. KNU 사전 단어 점수 (covered 범위 제외, 1자 단어는 오매칭 위험으로 제외)
        for word, word_score in knu.items():
            if not word_score or not word or len(word) < 2:
                continue
            start = 0
            while True:
                idx = title.find(word, start)
                if idx == -1:
                    break
                # covered 범위와 겹치면 스킵
                if covered.isdisjoint(range(idx, idx + len(word))):
                    # 4. 부정어 반전 확인 (키워드 직후 10자 이내)
                    suffix = title[idx + len(word): idx + len(word) + 10]
                    if re.search(r'않|없(?!지만|더라도)|못|안\s|아닌(?!지만|데)|아니(?!지만|더라도|라도)', suffix):
                        score -= word_score  # 부호 반전
                        matched.append(f"{word}(반전:{-word_score:+d})")
                    else:
                        score += word_score
                        if abs(word_score) >= 1:
                            matched.append(f"{word}({word_score:+d})")
                start = idx + len(word)

        # 5. 점수 → 감성 판정 (임계값 1)
        entry["matched_keywords"] = matched[:10]  # 상위 10개만 노출
        entry["score"] = score
        if score > 0:
            entry["sentiment"] = "positive"
            positive.append(entry)
        elif score < 0:
            entry["sentiment"] = "negative"
            negative.append(entry)
        else:
            entry["sentiment"] = "neutral"
            neutral.append(entry)

    summary = f"🟢긍정 {len(positive)} / 🔴부정 {len(negative)} / ⚪중립 {len(neutral)}"
    return {"positive": positive, "negative": negative, "neutral": neutral, "summary": summary}


async def kis_vi_status(token: str) -> list:
    """변동성완화장치(VI) 발동 종목 현황 (FHPST01390000).

    Returns: [{ticker, name, vi_type, vi_price, base_price, trigger_time, release_time, count}, ...]
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    try:
        s = _get_session()
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

    s = _get_session()
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
    s = _get_session()
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
    s = _get_session()
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
    today = datetime.now(KST).strftime("%Y%m%d")
    s = _get_session()
    _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/daily-credit-balance",
                          "FHPST04760000", token, {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20476",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": today,
    })
    items = d.get("output", d.get("output1", []))
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        result.append({
            "date": (item.get("deal_date") or item.get("bsop_date") or "").strip(),
            "credit_balance": int(item.get("whol_loan_rmnd_stcn", 0) or 0),
            "credit_ratio": float(item.get("whol_loan_rmnd_rate", 0) or 0),
            "credit_new": int(item.get("whol_loan_new_stcn", 0) or 0),
            "credit_repay": int(item.get("whol_loan_rdmp_stcn", 0) or 0),
            "close": int(item.get("stck_prpr", 0) or 0),
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
    s = _get_session()
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
            "date": (item.get("bsop_date") or "").strip(),
            "loan_balance": int(item.get("rmnd_stcn", 0) or 0),
            "loan_new": int(item.get("new_stcn", 0) or 0),
            "loan_repay": int(item.get("rdmp_stcn", 0) or 0),
            "loan_balance_amt": int(item.get("rmnd_amt", 0) or 0),
        })
    # 전일 대비 증감
    for i, row in enumerate(result):
        if i + 1 < len(result):
            row["change"] = row["loan_balance"] - result[i + 1]["loan_balance"]
        else:
            row["change"] = 0
    return result


async def kis_overtime_price(ticker: str, token: str, session=None) -> dict:
    """시간외 현재가 (FHPST02300000).

    Returns: {ticker, overtime_price, overtime_chg_rate, overtime_vol, ...}
    """
    s = session or aiohttp.ClientSession()
    try:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-overtime-price",
                              "FHPST02300000", token, {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        })
    finally:
        if session is None:
            await s.close()
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


async def kis_overtime_daily(ticker: str, token: str, session=None) -> dict:
    """시간외 일자별 주가 (FHPST02320000). 최근 30일."""
    s = session or aiohttp.ClientSession()
    try:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/quotations/inquire-daily-overtimeprice",
            "FHPST02320000", token,
            {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        rows = d.get("output2", [])
        if not rows:
            return {}
        r = rows[0]  # 최신 1일
        return {
            "ovtm_close": int(r.get("ovtm_untp_prpr", 0) or 0),
            "ovtm_change_pct": float(r.get("ovtm_untp_prdy_ctrt", 0) or 0),
            "ovtm_volume": int(r.get("ovtm_untp_vol", 0) or 0),
        }
    finally:
        if session is None:
            await s.close()


async def kis_income_statement(ticker: str, token: str, session=None) -> list:
    """손익계산서 분기별 (FHKST66430200). 최근 ~30분기."""
    s = session or aiohttp.ClientSession()
    try:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/finance/income-statement",
            "FHKST66430200", token,
            {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        rows = d.get("output", [])
        result = []
        for r in rows:
            period = str(r.get("stac_yymm", ""))
            if not period:
                continue
            def _pf(v):
                try:
                    return float(v)
                except Exception:
                    return 0.0
            result.append({
                "report_period":  period,
                "revenue":        _pf(r.get("sale_account")),
                "cost_of_sales":  _pf(r.get("sale_cost")),
                "gross_profit":   _pf(r.get("sale_totl_prfi")),
                "operating_profit": _pf(r.get("bsop_prti")),
                "op_prfi":        _pf(r.get("op_prfi")),
                "net_income":     _pf(r.get("thtr_ntin")),
            })
        return result
    finally:
        if session is None:
            await s.close()


async def kis_balance_sheet(ticker: str, token: str, session=None) -> list:
    """대차대조표 분기별 (FHKST66430100). 최근 ~30분기."""
    s = session or aiohttp.ClientSession()
    try:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/finance/balance-sheet",
            "FHKST66430100", token,
            {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker})
        rows = d.get("output", [])
        result = []
        for r in rows:
            period = str(r.get("stac_yymm", ""))
            if not period:
                continue
            def _pf(v):
                try:
                    return float(v)
                except Exception:
                    return 0.0
            result.append({
                "report_period":  period,
                "current_assets": _pf(r.get("cras")),
                "fixed_assets":   _pf(r.get("fxas")),
                "total_assets":   _pf(r.get("total_aset")),
                "current_liab":   _pf(r.get("flow_lblt")),
                "fixed_liab":     _pf(r.get("fix_lblt")),
                "total_liab":     _pf(r.get("total_lblt")),
                "capital":        _pf(r.get("cpfn")),
                "total_equity":   _pf(r.get("total_cptl")),
            })
        return result
    finally:
        if session is None:
            await s.close()


async def kis_asking_price(ticker: str, token: str) -> dict:
    """호가 잔량 (FHKST01010200).

    Returns: {ticker, asks: [{price, volume}], bids: [{price, volume}],
             total_ask_vol, total_bid_vol, bid_ask_ratio}
    """
    s = _get_session()
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
    s = _get_session()
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
    items = d.get("output2", d.get("output", []))
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items[:n]:
        ticker = (item.get("mksc_shrn_iscd") or item.get("stck_shrn_iscd") or "").strip()
        if not ticker:
            continue
        result.append({
            "rank": int(item.get("data_rank", 0) or 0),
            "ticker": ticker,
            "name": (item.get("hts_kor_isnm") or "").strip(),
            "overtime_price": int(item.get("ovtm_untp_prpr", 0) or item.get("stck_prpr", 0) or 0),
            "chg_pct": float(item.get("ovtm_untp_prdy_ctrt", 0) or item.get("prdy_ctrt", 0) or 0),
            "volume": int(item.get("ovtm_untp_vol", 0) or item.get("acml_vol", 0) or 0),
            "close": int(item.get("stck_prpr", 0) or 0),
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
    s = _get_session()
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
    s = _get_session()
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
        s = _get_session()
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
    s = _get_session()
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
    s = _get_session()
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

    kospi  = await _fetch_market("2001", 250)  # KOSPI 시총 상위 250
    await asyncio.sleep(0.3)
    kosdaq = await _fetch_market("1001", 350)  # KOSDAQ 시총 상위 350
    universe  = {**kospi, **kosdaq}
    print(f"[fetch_universe] KOSPI={len(kospi)}, KOSDAQ={len(kosdaq)}, 합계={len(universe)}")
    return universe


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
        s = _get_session()
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
    """KIS WebSocket 실시간 체결가 매니저
    - KR 통합체결가: H0UNCNT0 (KRX+NXT), 시간외: H0STOUP0 (16:00~18:00)
    - US 체결가: HDFSCNT0 (미국 장중)
    - 평일 상시 연결 (KR 시간외 + US 야간 대응). 끊김 시 30초 후 자동 재연결.
    """
    # KIS WebSocket은 plain ws:// 만 지원 (wss:// 시도하면 WRONG_VERSION_NUMBER)
    _WS_URL = "ws://ops.koreainvestment.com:21000"

    def __init__(self):
        self._subscribed: set = set()       # KR 종목 set
        self._subscribed_us: set = set()    # US 종목 set
        self._ws = None
        self._alert_cb = None
        self._running = False
        self._task = None
        self._fired: dict = {}  # {ticker: set(alert_types)} — 당일 발송 추적
        self._price_cache: dict = {}  # {ticker: int|float} — 최신 체결가 캐시

    async def start(self, alert_callback, tickers: set):
        self._alert_cb = alert_callback
        self._subscribed    = {t for t in tickers if not _is_us_ticker(t)}
        self._subscribed_us = {t for t in tickers if _is_us_ticker(t)}
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def update_tickers(self, new_tickers: set):
        """구독 종목 변경 (KR + US 모두 지원)"""
        kr_new = {t for t in new_tickers if not _is_us_ticker(t)}
        us_new = {t for t in new_tickers if _is_us_ticker(t)}
        kr_add    = kr_new - self._subscribed
        kr_remove = self._subscribed - kr_new
        us_add    = us_new - self._subscribed_us
        us_remove = self._subscribed_us - us_new
        self._subscribed    = kr_new
        self._subscribed_us = us_new
        if self._ws and not self._ws.closed:
            key = await get_kis_ws_approval_key()
            for t in kr_add:
                await self._send_sub_raw(self._ws, key, t, "1", "H0UNCNT0")
            for t in kr_remove:
                await self._send_sub_raw(self._ws, key, t, "0", "H0UNCNT0")
            for t in us_add:
                tr_key = f"D{_guess_excd(t)}{t}"
                await self._send_sub_raw(self._ws, key, tr_key, "1", "HDFSCNT0")
            for t in us_remove:
                tr_key = f"D{_guess_excd(t)}{t}"
                await self._send_sub_raw(self._ws, key, tr_key, "0", "HDFSCNT0")

    def reset_fired(self):
        self._fired = {}

    async def _run_loop(self):
        while self._running:
            now = datetime.now(KST)
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WS] 오류: {e}, 30초 후 재연결...")
            await asyncio.sleep(30)

    async def _connect_and_run(self):
        self.reset_fired()
        key = await get_kis_ws_approval_key()
        if not key:
            print("[WS] 접속키 없음, 스킵")
            return
        kr_count = len(self._subscribed)
        us_count = len(self._subscribed_us)
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self._WS_URL, heartbeat=30,
                timeout=aiohttp.ClientTimeout(total=None),
            ) as ws:
                self._ws = ws
                print(f"[WS] 연결됨 (KR {kr_count}개 + US {us_count}개 구독)")
                # KR 통합 체결가 구독 (H0UNCNT0)
                for t in list(self._subscribed):
                    await self._send_sub_raw(ws, key, t, "1", "H0UNCNT0")
                    await asyncio.sleep(0.05)
                # US 체결가 구독 (HDFSCNT0)
                for t in list(self._subscribed_us):
                    try:
                        tr_key = f"D{_guess_excd(t)}{t}"
                        await self._send_sub_raw(ws, key, tr_key, "1", "HDFSCNT0")
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        print(f"[WS] US 구독 오류 ({t}): {e}")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._on_text(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        print("[WS] 연결 종료됨")
                        break
        self._ws = None

    async def _send_sub_raw(self, ws, key, ticker, tr_type, tr_id="H0UNCNT0"):
        await ws.send_json({
            "header": {
                "approval_key": key, "custtype": "P",
                "tr_type": tr_type, "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": tr_id, "tr_key": ticker}},
        })

    async def _on_text(self, raw: str):
        # 포맷: "0|TR_ID|001|필드1^필드2^..."
        if raw.startswith("{"):
            return   # JSON ACK 무시
        parts = raw.split("|")
        if len(parts) < 4:
            return
        tr_id = parts[1]
        if tr_id not in ("H0UNCNT0", "H0STCNT0", "H0STOUP0", "HDFSCNT0"):
            return
        try:
            count = int(parts[2])
        except (ValueError, IndexError):
            return
        all_fields = parts[3].split("^")
        if count == 0 or not all_fields:
            return
        per_rec = len(all_fields) // max(count, 1)
        for i in range(count):
            f = all_fields[i * per_rec: (i + 1) * per_rec]
            try:
                if tr_id == "HDFSCNT0":
                    # US: SYMB=f[0], LAST=f[10]
                    if len(f) < 11:
                        continue
                    ticker = f[0]
                    price = float(f[10])
                else:
                    # KR (H0UNCNT0 / H0STCNT0 / H0STOUP0): ticker=f[0], price=f[2]
                    if len(f) < 3:
                        continue
                    ticker = f[0]
                    price = int(f[2])
                if price > 0:
                    self._price_cache[ticker] = price
                    if self._alert_cb:
                        await self._alert_cb(ticker, price)
            except Exception:
                continue

    def get_cached_price(self, ticker: str):
        """WebSocket 캐시에서 최신 체결가 반환. 없으면 None."""
        return self._price_cache.get(ticker)

    def set_cached_price(self, ticker: str, price):
        """외부에서 캐시에 가격 저장 (REST fallback 등)."""
        if price and price > 0:
            self._price_cache[ticker] = price


# KisRealtimeManager 싱글톤
ws_manager = KisRealtimeManager()


def get_ws_tickers() -> set:
    """WebSocket 구독 대상 종목 수집 (KR + US).
    단일 소스: 포트폴리오 + 손절 + watchalert (KR/US 통합).
    KIS WebSocket 41건 제한 → 포트폴리오/손절 우선, 초과 시 상위 40건만 반환.
    """
    # 우선순위 1: 포트폴리오 (실제 보유)
    priority: list = []
    seen: set = set()

    def _add(t: str):
        if t and t not in seen:
            seen.add(t)
            priority.append(t)

    pf = load_json(PORTFOLIO_FILE, {})
    for t in pf:
        if t not in ("us_stocks", "cash_krw", "cash_usd"):
            _add(t)
    for sym in pf.get("us_stocks", {}):
        _add(sym)
    # 우선순위 2: 손절/목표가 설정 종목
    sl = load_stoploss()
    for t in sl:
        if t != "us_stocks":
            _add(t)
    for sym in sl.get("us_stocks", {}):
        _add(sym)
    # 우선순위 3: watchalert (KR+US 단일 소스)
    for t in load_watchalert():
        _add(t)

    # KIS WebSocket 41건 제한 → 40건 안전 캡
    if len(priority) > 40:
        priority = priority[:40]
    return set(priority)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Yahoo Finance
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def get_yahoo_quote(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
        session = _get_session()
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

    # 1b. S&P 500 200일 이동평균 (judge_regime v6 기준)
    try:
        sp_hist = _yf_history("^GSPC", "1y")
        if sp_hist and len(sp_hist) >= 200:
            ma200 = sum(sp_hist[-200:]) / 200.0
            data.setdefault("SP500", {})["ma200"] = round(ma200, 2)
            # 현재가가 비어 있으면 히스토리 마지막 값으로 보강
            if data["SP500"].get("price") in (None, "?", 0):
                data["SP500"]["price"] = round(sp_hist[-1], 2)
        else:
            data.setdefault("SP500", {})["ma200"] = "?"
    except Exception:
        data.setdefault("SP500", {})["ma200"] = "?"

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

    # 4. 시장별 투자자매매동향 (KOSPI만, FHPTJ04040000)
    # KOSDAQ은 API 응답 전부 0 → 공식 문의 필요, 당분간 KOSPI만
    try:
        token = await get_kis_token()
        if token:
            kospi_flow = await _fetch_market_investor_flow(token, "KSP")
            data["MARKET_FLOW"] = {"kospi": kospi_flow}
            # judge_regime 호환: KOSPI 외인 순매수금(백만원 → 억원)
            data["FOREIGN_FLOW"] = {"amount_억": kospi_flow["frgn"] // 100}
        else:
            data["MARKET_FLOW"]  = {}
            data["FOREIGN_FLOW"] = {"amount_억": "?"}
    except Exception:
        data["MARKET_FLOW"]  = {}
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

    # 6. 시간외 급등락 (SQLite daily_snapshot, pm 슬롯용)
    try:
        from db_collector import _get_db
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        conn = _get_db()
        rows = conn.execute("""
            SELECT s.symbol, m.name_kr, s.ovtm_change_pct
            FROM daily_snapshot s
            LEFT JOIN stock_master m ON m.symbol = s.symbol
            WHERE s.trade_date = ?
              AND s.ovtm_change_pct IS NOT NULL
              AND s.ovtm_change_pct != 0
            ORDER BY s.ovtm_change_pct DESC
        """, (today_str,)).fetchall()
        conn.close()
        top    = [{"name": r["name_kr"] or r["symbol"], "pct": r["ovtm_change_pct"]}
                  for r in rows[:3]]
        bottom = [{"name": r["name_kr"] or r["symbol"], "pct": r["ovtm_change_pct"]}
                  for r in rows[-3:] if r["ovtm_change_pct"] < 0]
        data["OVERTIME_MOVERS"] = {"top": top, "bottom": bottom}
    except Exception:
        data["OVERTIME_MOVERS"] = {"top": [], "bottom": []}

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
    sp500 = data.get("SP500", {})
    msg += "[시장심리]\n"
    msg += f"VIX: {_p(vix)} ({_c(vix)}) | KOSPI: {_p(kospi)} ({_c(kospi)})\n"
    # S&P 500 + 200MA (레짐 판정 기준)
    sp_p = sp500.get("price", "?")
    sp_ma = sp500.get("ma200", "?")
    if sp_p != "?" and sp_ma != "?":
        try:
            diff_pct = (float(sp_p) / float(sp_ma) - 1) * 100
            msg += f"S&P500: {sp_p:,} (200MA {sp_ma:,}, {diff_pct:+.1f}%)\n"
        except Exception:
            msg += f"S&P500: {_p(sp500)} ({_c(sp500)}) | 200MA: {sp_ma}\n"
    else:
        msg += f"S&P500: {_p(sp500)} ({_c(sp500)})\n"
    msg += "\n"

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
    def _flow_str(flow_dict: dict, label: str) -> str:
        """시장별 투자자 흐름 → "외인 +1,064억 | 기관 -203억 | 개인 -1,228억" """
        frgn = flow_dict.get("frgn", 0)
        orgn = flow_dict.get("orgn", 0)
        prsn = flow_dict.get("prsn", 0)
        frgn_억 = frgn // 100
        orgn_억 = orgn // 100
        prsn_억 = prsn // 100
        return (f"{label}: 외인 {frgn_억:+,}억 | "
                f"기관 {orgn_억:+,}억 | 개인 {prsn_억:+,}억")

    mf = data.get("MARKET_FLOW", {})
    msg += "[수급]\n"
    if mf.get("kospi"):
        msg += _flow_str(mf["kospi"], "KOSPI") + "\n"
    if not mf:
        # fallback: FOREIGN_FLOW만 있을 때
        ff  = data.get("FOREIGN_FLOW", {})
        amt = ff.get("amount_억", "?")
        if isinstance(amt, (int, float)):
            msg += f"외인 KOSPI: {amt:+,}억\n"
        else:
            msg += f"외인 KOSPI: {amt}\n"
    msg += "\n"

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
    """매크로 데이터 기반 레짐 자동 판정 v6 (2026-04-23 개정, INVESTMENT_RULES v6)

    3단계 판정:
    - 🟢 공격: S&P 500 > 200MA (3% 버퍼) AND VIX < 20
    - 🔴 위기: S&P 500 < 200MA (-3% 버퍼) AND VIX > 30
    - 🟡 경계: 그 외 (둘 중 하나 이탈)

    USD/KRW / WTI / KOSPI 낙폭 / 외인 순매수는 판정에서 제외.
    (USD/KRW는 한국 종목 사이징 참고용으로만 사용)
    """
    def _sf(d, key="price"):
        v = d.get(key, "?")
        if v == "?" or v is None:
            return None
        try:
            return float(str(v).replace(",", ""))
        except Exception:
            return None

    vix         = _sf(data.get("VIX",   {}))
    sp500_price = _sf(data.get("SP500", {}), "price")
    sp500_ma200 = _sf(data.get("SP500", {}), "ma200")

    reasons = []

    # S&P 500 200MA (3% 버퍼) 판정
    sp_above_ma = None  # True=위, False=아래, "neutral"=버퍼존
    if sp500_price is not None and sp500_ma200 is not None and sp500_ma200 > 0:
        buffer = sp500_ma200 * 0.03
        if sp500_price > sp500_ma200 + buffer:
            sp_above_ma = True
            reasons.append(f"S&P {sp500_price:,.0f} > 200MA {sp500_ma200:,.0f}+3%")
        elif sp500_price < sp500_ma200 - buffer:
            sp_above_ma = False
            reasons.append(f"S&P {sp500_price:,.0f} < 200MA {sp500_ma200:,.0f}-3%")
        else:
            sp_above_ma = "neutral"
            reasons.append(f"S&P 200MA 버퍼존 ({sp500_price:,.0f}/{sp500_ma200:,.0f})")
    else:
        reasons.append("S&P/200MA 데이터 없음")

    # VIX 판정
    vix_zone = None  # "low"=<20, "mid"=20~30, "high"=>30
    if vix is not None:
        reasons.append(f"VIX {vix:.2f}")
        if vix < 20:
            vix_zone = "low"
        elif vix > 30:
            vix_zone = "high"
        else:
            vix_zone = "mid"
    else:
        reasons.append("VIX 데이터 없음")

    # 종합 판정
    if sp_above_ma is True and vix_zone == "low":
        return {"regime": "🟢", "label": "공격", "reasons": reasons}
    if sp_above_ma is False and vix_zone == "high":
        return {"regime": "🔴", "label": "위기", "reasons": reasons}
    return {"regime": "🟡", "label": "경계", "reasons": reasons}


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
        session = _get_session()
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "000":
                    return data.get("list", [])
    except Exception as e:
        print(f"DART API 오류: {e}")
    return []


def filter_important_disclosures(disclosures, watchlist_names):
    """관심 기업의 공시 전부 반환 (키워드 필터 제거, 나중에 필요시 추가)."""
    return [d for d in disclosures
            if any(name in d.get("corp_name", "") for name in watchlist_names if name)]


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART corp_code 매핑 & 재무 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━
DART_CORP_MAP_FILE = f"{_DATA_DIR}/dart_corp_map.json"


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
# DART 전체 재무제표 파서 (F-Score / M-Score / FCF 용)
# fnlttSinglAcntAll 1회 호출로 PL/BS/CF 전체 계정 파싱
# CFS(연결) 우선, 없으면 OFS(별도) fallback. 지배주주 귀속 순이익/자본도 파싱.
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# 계정명 매칭용 토큰 (account_nm에 대한 "in" 포함 검사 — 변종 대응)
# 주의: 더 구체적인 패턴을 먼저 배치 (예: "매출총이익" 먼저, "매출액"은 별도 처리)
_DART_ACCT_TOKENS = {
    # 손익 (sj_div 주로 'IS' 또는 'CIS')
    "gross_profit":     [("매출총이익",), ("매출총손실",)],
    "operating_profit": [("영업이익",), ("영업손실",)],
    "cost_of_sales":    [("매출원가",)],
    "sga":              [("판매비와관리비",), ("판매비와 관리비",), ("판관비",)],
    # 당기순이익: 지배/비지배 분리 필요 → 별도 처리
    # 대차 (sj_div 주로 'BS')
    "current_assets":   [("유동자산",)],
    "total_assets":     [("자산총계",)],
    "current_liab":     [("유동부채",)],
    "total_liab":       [("부채총계",)],
    "total_equity":     [("자본총계",)],
    "capital":          [("자본금",)],
    "receivables":      [("매출채권",)],
    "inventory":        [("재고자산",)],
    # 현금흐름 (sj_div 주로 'CF')
    "cfo":              [("영업활동",)],   # '영업활동현금흐름' / '영업활동으로 인한 현금흐름'
    # CapEx / 감가상각 / 무형자산상각 → 별도 처리 (sj='CF' 한정)
}


def _dart_amt_to_int(amt_str: str) -> int | None:
    """DART amount 문자열 → int (원 단위)."""
    if not amt_str:
        return None
    s = str(amt_str).replace(",", "").replace(" ", "").strip()
    if not s or s == "-":
        return None
    # 음수는 괄호로 오는 경우 처리
    neg = False
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
        neg = True
    try:
        v = int(s)
        return -v if neg else v
    except ValueError:
        return None


def _dart_acct_match(acct_nm: str, tokens_list) -> bool:
    """account_nm이 토큰 조합 중 하나에 모두 매치되면 True.
    tokens_list: [(tok1,), (tok1, tok2), ...] — 각 튜플은 AND, 튜플 간은 OR.
    """
    for tokens in tokens_list:
        if all(t in acct_nm for t in tokens):
            return True
    return False


async def dart_quarterly_full(corp_code: str, year: int, quarter: int,
                              session: aiohttp.ClientSession | None = None) -> dict | None:
    """DART fnlttSinglAcntAll 1회 호출로 PL/BS/CF 전체 파싱.

    quarter: 1=1분기, 2=반기, 3=3분기, 4=사업보고서(연간)
    CFS(연결) 우선, status!='000'이면 OFS(별도) fallback.

    반환 dict (값은 원 단위 int, 없으면 None):
      report_period (YYYYMM),
      revenue, cost_of_sales, gross_profit, operating_profit,
      net_income, net_income_parent,
      sga,
      current_assets, total_assets, current_liab, total_liab,
      capital, total_equity, equity_parent,
      receivables, inventory,
      cfo, capex, fcf, depreciation,
      shares_out,
      fs_source ('CFS' | 'OFS')
    """
    if not DART_API_KEY or not corp_code:
        return None
    reprt_map = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
    reprt_code = reprt_map.get(quarter)
    if not reprt_code:
        return None
    url = f"{DART_BASE_URL}/fnlttSinglAcntAll.json"

    async def _fetch(sess, fs_div: str):
        params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code,
                  "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": fs_div}
        async with sess.get(url, params=params,
                            timeout=aiohttp.ClientTimeout(total=15)) as resp:
            return await resp.json(content_type=None)

    own_session = session is None
    sess = session or aiohttp.ClientSession()
    try:
        data = await _fetch(sess, "CFS")
        fs_source = "CFS"
        if data.get("status") != "000" or not data.get("list"):
            data = await _fetch(sess, "OFS")
            fs_source = "OFS"
        if data.get("status") != "000" or not data.get("list"):
            return None

        items = data.get("list", [])
        out = {
            "report_period": f"{year}{quarter * 3:02d}",
            "fs_source": fs_source,
        }
        # 표준 계정 first-match 파싱
        fields = ["gross_profit", "operating_profit", "cost_of_sales", "sga",
                  "current_assets", "total_assets", "current_liab", "total_liab",
                  "total_equity", "capital", "receivables", "inventory", "cfo"]
        for f in fields:
            out[f] = None

        # 당기순이익 / 지배귀속 / CapEx / 감가상각은 별도 처리
        net_income = None
        net_income_parent = None
        equity_parent = None
        capex = None
        dep_pt = None
        dep_intan = None
        revenue = None

        for item in items:
            acct = (item.get("account_nm") or "").strip()
            sj = (item.get("sj_div") or "").strip()   # IS/CIS/BS/CF
            amt = _dart_amt_to_int(item.get("thstrm_amount"))
            if amt is None:
                continue

            # 매출액: 변종 대응 ("매출액" / "매출" / "영업수익" / "수익(매출액)")
            # 매출총이익/매출원가 제외 (포함어)
            if revenue is None and sj in ("IS", "CIS"):
                if acct in ("매출액", "매출", "수익(매출액)", "영업수익") \
                        or acct.startswith("매출액"):
                    revenue = amt
                    continue

            # 표준 계정 (first-match 보존)
            for key, tokens_list in _DART_ACCT_TOKENS.items():
                if out.get(key) is None and _dart_acct_match(acct, tokens_list):
                    # sj 보조 검증 — cfo는 CF, BS 계정은 BS, PL 계정은 IS/CIS
                    if key == "cfo" and sj != "CF":
                        continue
                    if key in ("current_assets", "total_assets", "current_liab",
                               "total_liab", "total_equity", "capital",
                               "receivables", "inventory") and sj != "BS":
                        continue
                    if key in ("gross_profit", "operating_profit", "cost_of_sales",
                               "sga") and sj not in ("IS", "CIS"):
                        continue
                    out[key] = amt
                    break

            # 감가상각비 / 무형자산상각비 (CF 간접법 조정항목)
            if sj == "CF":
                if dep_pt is None and "감가상각" in acct:
                    dep_pt = amt
                if dep_intan is None and ("무형자산상각" in acct or "무형자산 상각" in acct):
                    dep_intan = amt

            # 지배주주 귀속 순이익 — IS 만 (CIS는 포괄손익이라 제외)
            # 계정명 변종: "지배기업 소유주지분", "지배기업 소유지분", "지배기업소유주지분"
            if net_income_parent is None and sj == "IS":
                if "지배기업" in acct and "지분" in acct:
                    net_income_parent = amt
                    continue

            # 당기순이익 (전체, 지배+비지배 합산) — IS 우선, 없으면 CIS
            # 변종: "당기순이익", "연결당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"
            # CIS의 "총포괄이익/총포괄손익"은 제외 (별도 지표)
            if net_income is None and sj in ("IS", "CIS"):
                if ("당기순이익" in acct or "분기순이익" in acct or "반기순이익" in acct) \
                        and "지배" not in acct and "비지배" not in acct \
                        and "포괄" not in acct:
                    net_income = amt
                    continue

            # 지배주주 귀속 자본 — BS (계정명 변종 포함)
            if equity_parent is None and sj == "BS":
                if "지배기업" in acct and "지분" in acct:
                    equity_parent = amt
                    continue

            # CapEx — CF의 '유형자산 취득' or '유형자산의 증가'
            if capex is None and sj == "CF":
                if "유형자산" in acct and ("취득" in acct or "증가" in acct):
                    capex = abs(amt)
                    continue

        # 감가상각 합산
        depreciation = None
        if dep_pt is not None or dep_intan is not None:
            depreciation = (dep_pt or 0) + (dep_intan or 0)

        out["revenue"] = revenue
        out["net_income"] = net_income
        out["net_income_parent"] = net_income_parent
        out["equity_parent"] = equity_parent
        out["capex"] = capex
        out["depreciation"] = depreciation

        # FCF = CFO - abs(CapEx)
        if out.get("cfo") is not None:
            cx = capex if capex is not None else 0
            out["fcf"] = out["cfo"] - cx
        else:
            out["fcf"] = None

        # 발행주식수 — fnlttSinglAcntAll에는 없음 (별도 API 필요, Phase1 범위 밖)
        out["shares_out"] = None

        # 단위 변환: 원 → 억원 (기존 financial_quarterly 컬럼 단위와 통일)
        _MONEY_KEYS = ("revenue", "cost_of_sales", "gross_profit", "operating_profit",
                       "sga", "net_income", "net_income_parent",
                       "current_assets", "total_assets", "current_liab", "total_liab",
                       "capital", "total_equity", "equity_parent",
                       "receivables", "inventory",
                       "cfo", "capex", "fcf", "depreciation")
        for k in _MONEY_KEYS:
            v = out.get(k)
            if v is not None:
                out[k] = v // 100_000_000

        return out
    except Exception as e:
        print(f"[DART] dart_quarterly_full {corp_code} {year}Q{quarter} 오류: {e}")
        return None
    finally:
        if own_session:
            await sess.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 주식 총수 (stockTotqySttus: 보통주 발행주식수)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def dart_shares_outstanding(corp_code: str, year: int, quarter: int,
                                   session: aiohttp.ClientSession | None = None
                                   ) -> int | None:
    """DART 주식 총수 API. 보고서 기준 보통주 발행주식수(주) 반환.

    quarter: 1/2/3/4 (reprt_code 11013/11012/11014/11011)
    우선주/기타주 제외, 보통주만 반환.
    응답 필드: se='보통주' row의 istc_totqy(발행주식총수).

    DART 분당 1000콜 제한 → 호출자가 0.067초/콜 sleep 삽입.
    """
    if not DART_API_KEY or not corp_code:
        return None
    reprt_map = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
    reprt_code = reprt_map.get(quarter)
    if not reprt_code:
        return None

    url = f"{DART_BASE_URL}/stockTotqySttus.json"
    params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code,
              "bsns_year": str(year), "reprt_code": reprt_code}

    own_session = session is None
    sess = session or aiohttp.ClientSession()
    try:
        async with sess.get(url, params=params,
                            timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json(content_type=None)
        if data.get("status") != "000":
            return None
        items = data.get("list") or []
        for it in items:
            se = (it.get("se") or "").strip()
            # "보통주" 우선 매칭 (일부 회사는 "보통주식" 변종 가능성 대비)
            if se == "보통주" or se.startswith("보통주"):
                # istc_totqy: 발행주식총수, totqy: (구버전) 총수
                raw = it.get("istc_totqy") or it.get("totqy")
                v = _dart_amt_to_int(raw)
                if v is not None and v > 0:
                    return v
        return None
    except Exception as e:
        print(f"[DART] dart_shares_outstanding {corp_code} {year}Q{quarter} 오류: {e}")
        return None
    finally:
        if own_session:
            await sess.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 내부자 거래 (elestock.json: 임원·주요주주 특정증권등 소유상황보고서)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
DB_PATH_FOR_INSIDER = f"{_DATA_DIR}/stock.db"


async def kis_elestock(corp_code: str) -> list:
    """DART 임원·주요주주 소유보고서 조회.

    Returns: [{rcept_no, rcept_dt, repror, isu_exctv_ofcps,
               sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt, ...}, ...]
    """
    if not DART_API_KEY or not corp_code:
        return []
    url = f"{DART_BASE_URL}/elestock.json"
    params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if data.get("status") != "000":
                    return []
                return data.get("list", [])
    except Exception as e:
        print(f"[DART elestock] {corp_code} 오류: {e}")
        return []


def _to_int_safe(v) -> int:
    if v is None:
        return 0
    s = str(v).replace(",", "").replace("-", "-").strip()
    if not s or s == "-":
        return 0
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return 0


def _to_float_safe(v) -> float:
    if v is None:
        return 0.0
    s = str(v).replace(",", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def upsert_insider_transactions(symbol: str, corp_code: str, records: list) -> int:
    """elestock 응답을 insider_transactions 테이블에 UPSERT. 신규 row 수 반환."""
    import sqlite3
    if not records:
        return 0
    conn = sqlite3.connect(DB_PATH_FOR_INSIDER, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    try:
        for r in records:
            rcept_no = (r.get("rcept_no") or "").strip()
            repror = (r.get("repror") or "").strip()
            if not rcept_no or not repror:
                continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO insider_transactions "
                    "(rcept_no, symbol, corp_code, rcept_dt, repror, ofcps, rgist, "
                    " main_shrholdr, stock_cnt, stock_irds_cnt, stock_rate, stock_irds_rate, collected_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rcept_no, symbol, corp_code,
                        (r.get("rcept_dt") or "").strip(),
                        repror,
                        (r.get("isu_exctv_ofcps") or "").strip(),
                        (r.get("isu_exctv_rgist_at") or "").strip(),
                        (r.get("isu_main_shrholdr") or "").strip(),
                        _to_int_safe(r.get("sp_stock_lmp_cnt")),
                        _to_int_safe(r.get("sp_stock_lmp_irds_cnt")),
                        _to_float_safe(r.get("sp_stock_lmp_rate")),
                        _to_float_safe(r.get("sp_stock_lmp_irds_rate")),
                        now_str,
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
            except Exception as e:
                print(f"[insider upsert] {symbol} {rcept_no} {repror}: {e}")
        conn.commit()
    finally:
        conn.close()
    return inserted


def aggregate_insider_cluster(symbol: str, days: int = 30) -> dict:
    """최근 N일간 해당 종목 내부자 매수/매도 집계.

    Returns: {buy_names: set, sell_names: set, buy_qty, sell_qty, buyers, sellers, recent: [...]}
    """
    import sqlite3
    conn = sqlite3.connect(DB_PATH_FOR_INSIDER, timeout=30)
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM insider_transactions "
        "WHERE symbol=? AND rcept_dt>=? ORDER BY rcept_dt DESC",
        (symbol, cutoff),
    ).fetchall()
    conn.close()

    buy_names, sell_names = set(), set()
    buy_qty = sell_qty = 0
    recent = []
    for r in rows:
        name = r["repror"]
        delta = r["stock_irds_cnt"] or 0
        if delta > 0:
            buy_names.add(name)
            buy_qty += delta
        elif delta < 0:
            sell_names.add(name)
            sell_qty += abs(delta)
        recent.append({
            "date": r["rcept_dt"], "name": name, "ofcps": r["ofcps"],
            "delta": delta, "total": r["stock_cnt"], "rate": r["stock_rate"],
        })
    return {
        "symbol": symbol,
        "days": days,
        "buyers": len(buy_names),
        "sellers": len(sell_names),
        "buy_qty": buy_qty,
        "sell_qty": sell_qty,
        "buy_names": sorted(buy_names),
        "sell_names": sorted(sell_names),
        "recent": recent,
    }


async def collect_insider_for_tickers(tickers: list, corp_map: dict) -> dict:
    """워치리스트 종목들의 내부자 보고 수집 → DB 저장.
    Returns: {symbol: {new: int, total: int}}
    """
    import asyncio
    result = {}
    for sym in tickers:
        corp_code = corp_map.get(sym, "")
        if not corp_code:
            continue
        records = await kis_elestock(corp_code)
        new_cnt = upsert_insider_transactions(sym, corp_code, records)
        result[sym] = {"new": new_cnt, "total": len(records)}
        await asyncio.sleep(0.3)  # DART rate limit 여유
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 사업보고서 본문 저장
# ━━━━━━━━━━━━━━━━━━━━━━━━━
DART_REPORTS_DIR = f"{_DATA_DIR}/dart_reports"
CORP_CODES_FILE  = f"{_DATA_DIR}/corp_codes.json"


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


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 증분 수집용 — 최근 N일 정기공시(pblntf_ty=A) 전체 조회
# 기존 search_dart_reports는 특정 corp_code의 사업보고서(A001) 본문 수집용이라 분리.
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_RPT_PERIOD_RE = re.compile(r"(\d{4})\.(\d{2})")


def _parse_rpt_nm(rpt_nm: str) -> tuple[str | None, str | None]:
    """rpt_nm 문자열에서 (report_period YYYYMM, report_type) 파싱.

    규칙:
      - "[기재정정]" / "[첨부정정]" 접두가 붙어 있으면 (None, None) 반환 — 원본 공시만 수집.
      - "사업보고서 (2024.12)"  → ("202412", "annual")
      - "반기보고서 (2024.06)"  → ("202406", "semi")
      - "분기보고서 (2024.03)"  → ("202403", "quarterly")
      - "분기보고서 (2024.09)"  → ("202409", "quarterly")
      - 위 3유형이 아니면 (None, None).
    """
    nm = (rpt_nm or "").strip()
    if not nm:
        return None, None
    # 정정 공시는 skip (원본이 이미 DB에 있거나, 곧 원본 공시가 나올 것)
    if nm.startswith("[기재정정]") or nm.startswith("[첨부정정]") or nm.startswith("[정정]"):
        return None, None

    # 유형 분류 (첫 토큰만 본다; 일부는 "[첨부추가]" 등 변종이 뒤에 붙을 수 있음)
    if "사업보고서" in nm:
        rtype = "annual"
    elif "반기보고서" in nm:
        rtype = "semi"
    elif "분기보고서" in nm:
        rtype = "quarterly"
    else:
        return None, None

    m = _RPT_PERIOD_RE.search(nm)
    if not m:
        # 괄호 내 날짜 없음 — 비정형. 일단 스킵.
        return None, None
    year, month = m.group(1), m.group(2)
    # month 검증: 03/06/09/12만 유효
    if month not in ("03", "06", "09", "12"):
        return None, None
    # 유형-월 정합성 보조 검증 (사업=12, 반기=06)
    if rtype == "annual" and month != "12":
        return None, None
    if rtype == "semi" and month != "06":
        return None, None
    if rtype == "quarterly" and month not in ("03", "09"):
        return None, None
    return f"{year}{month}", rtype


async def search_dart_periodic_new(days: int = 7,
                                    session: aiohttp.ClientSession | None = None) -> list[dict]:
    """DART list.json 지난 N일 정기공시(pblntf_ty=A) 조회.

    기존 search_dart_reports(corp_code별 A001 본문 수집)와 분리 —
    전체 공시판에서 사업/반기/분기보고서 모두 긁어오는 증분 수집용.
    본문은 가져오지 않음 (corp_code + rcept_dt + rpt_nm만 필요).

    Args:
        days: 오늘 KST 기준 N일 전부터 조회 (기본 7).
        session: 재사용할 aiohttp 세션. None이면 내부에서 생성.

    Returns:
        [{"corp_code", "ticker" (stock_code, 없을 수 있음), "corp_name",
          "rcept_no", "rcept_dt", "rpt_nm",
          "report_period" (YYYYMM), "report_type" ("quarterly"|"semi"|"annual")}]
        정정공시([기재정정]/[첨부정정])는 제외.
    """
    if not DART_API_KEY:
        print("[DART-Incr] DART_API_KEY 미설정 — 빈 결과 반환")
        return []

    now = datetime.now(KST)
    end_de = now.strftime("%Y%m%d")
    bgn_de = (now - timedelta(days=max(days, 1))).strftime("%Y%m%d")
    url = f"{DART_BASE_URL}/list.json"

    own_session = session is None
    sess = session or aiohttp.ClientSession()

    results: list[dict] = []
    try:
        page_no = 1
        total_pages = 1
        while page_no <= total_pages:
            params = {
                "crtfc_key": DART_API_KEY,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "pblntf_ty": "A",
                "page_count": 100,
                "page_no": page_no,
                "sort": "date",
                "sort_mth": "desc",
            }
            try:
                async with sess.get(url, params=params,
                                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        print(f"[DART-Incr] list.json HTTP {resp.status} page={page_no}")
                        break
                    data = await resp.json(content_type=None)
            except Exception as e:
                print(f"[DART-Incr] list.json 호출 오류 page={page_no}: {e}")
                break

            status = data.get("status", "")
            if status != "000":
                # 013(조회된 데이터 없음) 포함 — 조용히 종료
                if status not in ("000", "013"):
                    print(f"[DART-Incr] list.json status={status} "
                          f"msg={data.get('message','')}")
                break

            page_list = data.get("list", []) or []
            total_pages = int(data.get("total_page", 1) or 1)

            for item in page_list:
                rpt_nm = (item.get("report_nm") or "").strip()
                period, rtype = _parse_rpt_nm(rpt_nm)
                if not period or not rtype:
                    continue
                corp_code = (item.get("corp_code") or "").strip()
                if not corp_code:
                    continue
                results.append({
                    "corp_code":     corp_code,
                    "ticker":        (item.get("stock_code") or "").strip(),
                    "corp_name":     (item.get("corp_name") or "").strip(),
                    "rcept_no":      (item.get("rcept_no") or "").strip(),
                    "rcept_dt":      (item.get("rcept_dt") or "").strip(),
                    "rpt_nm":        rpt_nm,
                    "report_period": period,
                    "report_type":   rtype,
                })

            # 과도한 페이징 방지 안전장치 (N일 × 100건/페이지 = 수천 페이지 이론상)
            if page_no >= 50:
                print(f"[DART-Incr] 50 페이지 상한 도달 — 중단 (page={page_no})")
                break
            page_no += 1

        print(f"[DART-Incr] list.json {bgn_de}~{end_de}: "
              f"원본 공시 {len(results)}건 (정정/비정기 제외 후)")
        return results
    finally:
        if own_session:
            await sess.close()


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


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 수시공시 본문 조회 + 요약 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━
DART_DISCLOSURE_CACHE_DIR = f"{_DATA_DIR}/dart_disclosures"


async def list_disclosures_for_ticker(ticker: str, days: int = 7) -> list[dict]:
    """특정 종목의 최근 N일 DART 공시 목록 조회.

    Args:
        ticker: 종목코드 (6자리)
        days: 조회 기간 (일)

    Returns:
        [{"rcept_no", "report_nm", "rcept_dt"}, ...] 또는 빈 리스트 (에러/키없음)
    """
    if not DART_API_KEY:
        return []
    if not ticker or not isinstance(ticker, str):
        return []

    try:
        corp_map = await get_dart_corp_map({})
        corp_code = corp_map.get(ticker, "")
        if not corp_code:
            return []

        now = datetime.now(KST)
        end_date = now.strftime("%Y%m%d")
        start_date = (now - timedelta(days=days)).strftime("%Y%m%d")

        url = f"{DART_BASE_URL}/list.json"
        params = {
            "crtfc_key": DART_API_KEY,
            "corp_code": corp_code,
            "bgn_de": start_date,
            "end_de": end_date,
            "page_count": 100,
            "sort": "date",
            "sort_mth": "desc",
        }

        session = _get_session()
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            if data.get("status") != "000":
                return []

            out = []
            for item in data.get("list", []):
                out.append({
                    "rcept_no": item.get("rcept_no", ""),
                    "report_nm": item.get("report_nm", ""),
                    "rcept_dt": item.get("rcept_dt", ""),
                })
            return out
    except Exception as e:
        print(f"[DART] list_disclosures_for_ticker({ticker}) 오류: {e}")
        return []


async def fetch_and_cache_disclosure(ticker: str, rcept_no: str) -> str:
    """공시 본문을 다운로드하여 data/dart_disclosures/ticker_rcept.txt 캐시.

    - 이미 있으면 파일 로드
    - 없으면 fetch_dart_document 호출 → 저장 → 반환
    - path traversal 방지
    """
    if not ticker or not rcept_no:
        return ""
    # path traversal 차단
    for bad in ("/", "\\", ".."):
        if bad in ticker or bad in rcept_no:
            return ""

    try:
        os.makedirs(DART_DISCLOSURE_CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(DART_DISCLOSURE_CACHE_DIR,
                                  f"{ticker}_{rcept_no}.txt")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                print(f"[DART] 캐시 읽기 실패 {cache_path}: {e}")

        body = await fetch_dart_document(rcept_no)
        if body:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(body)
            except Exception as e:
                print(f"[DART] 캐시 저장 실패 {cache_path}: {e}")
        return body or ""
    except Exception as e:
        print(f"[DART] fetch_and_cache_disclosure({ticker}, {rcept_no}) 오류: {e}")
        return ""


def _fmt_krw_amount(val: int) -> str:
    """원 단위 금액 → 한국어 요약 포맷."""
    if val is None:
        return "?"
    absv = abs(val)
    sign = "-" if val < 0 else ""
    if absv >= 1_0000_0000_0000:  # 1조 이상
        return f"{sign}{absv / 1_0000_0000_0000:.2f}조"
    elif absv >= 100_000_000:  # 1억 이상
        return f"{sign}{absv / 100_000_000:,.0f}억"
    elif absv >= 1_000_000:  # 백만 이상
        return f"{sign}{absv / 1_000_000:,.0f}백만"
    else:
        return f"{sign}{absv:,}원"


def _parse_pct(s: str) -> float | None:
    """'23.9%' / '+23.9' / '(23.9)' 등에서 % 값 추출."""
    if not s:
        return None
    m = re.search(r'([+\-]?\d+\.?\d*)\s*%?', s.replace(",", ""))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _detect_krw_unit(body_text: str) -> int:
    """본문에서 '(단위: 백만원)', '(단위: 원)', '(단위: 천원)' 감지 → 원 환산 곱수."""
    m = re.search(r'단위\s*[:\：]?\s*([가-힣]+원)', body_text[:3000])
    if not m:
        return 1  # 기본 원
    unit = m.group(1)
    if "백만" in unit:
        return 1_000_000
    if "천원" in unit or "천 원" in unit:
        return 1_000
    if "억" in unit:
        return 100_000_000
    return 1


def parse_disclosure_summary(report_nm: str, body_text: str) -> dict | None:
    """공시 본문 → 요약 dict. 5종 타입 분기. 매칭 안 되면 None.

    Returns:
        {"type": str, "summary": [str, ...]} 또는 None
    """
    if not report_nm or not body_text:
        return None
    try:
        title = report_nm
        # ━━ 타입 1: 잠정실적 ━━
        if "잠정실적" in title or "영업(잠정)실적" in title:
            return _parse_earnings_preview(body_text)

        # ━━ 타입 2: 자기주식 취득/소각 ━━
        if ("자기주식취득결정" in title or "자기주식 취득" in title
                or "주식소각" in title or "자기주식소각" in title):
            return _parse_buyback(title, body_text)

        # ━━ 타입 3: 배당결정 ━━
        if ("현금배당" in title or "현금·현물배당" in title
                or "현금ㆍ현물배당" in title or "배당결정" in title):
            return _parse_dividend(body_text)

        # ━━ 타입 4: 풍문·보도해명 ━━
        if "풍문" in title or "해명" in title:
            return _parse_rumor(body_text)

        # ━━ 타입 5: 매칭 안됨 ━━
        return None
    except Exception as e:
        print(f"[DART] parse_disclosure_summary 오류 ({report_nm[:40]}): {e}")
        return None


def _parse_earnings_preview(body: str) -> dict | None:
    """잠정실적 파싱. 매출/영업이익/순이익 + YoY%."""
    try:
        unit = _detect_krw_unit(body)
        lines_out = []

        # 계정별 키워드 (순서 중요 — 매출총이익/영업이익 등 구분)
        targets = [
            ("매출", "💰 매출", ["매출액", "매출 액"]),
            ("영업이익", "💰 영업익", ["영업이익", "영업 이익"]),
            ("순이익", "💰 순익",
             ["당기순이익", "당기 순이익", "반기순이익", "분기순이익", "순이익"]),
        ]

        for label, emoji, keywords in targets:
            amount_won = None
            yoy_pct = None

            for kw in keywords:
                # 라인 단위로 찾기 — 키워드가 포함된 라인 검색
                # 잠정실적 테이블은 세로 라인 구조: 항목\n당기실적\n전년동기\n증감률...
                idx = body.find(kw)
                if idx < 0:
                    continue

                # 키워드 이후 500자 윈도우에서 숫자들 수집
                window = body[idx:idx + 800]
                # 숫자 (콤마 포함, 괄호음수, 소수점) 추출
                # 1,462,345 또는 (1,462) 또는 1462345
                nums = re.findall(r'\(?([\-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?|\d+\.\d+)\)?', window)
                # 필터: 연도(19xx/20xx)와 너무 작은 숫자 제거
                filtered = []
                for n in nums:
                    clean = n.replace(",", "")
                    try:
                        v = float(clean)
                    except ValueError:
                        continue
                    # 연도 제외 (2015~2030)
                    if 2015 <= v <= 2030 and "." not in clean:
                        continue
                    filtered.append((v, clean))

                if not filtered:
                    continue

                # 첫 번째 큰 숫자 = 당기실적
                # 영업이익/순이익은 작을 수 있음 → min threshold 완화
                for v, clean in filtered:
                    if abs(v) >= 100:  # 단위가 원일 경우 매우 큰 값. 단위가 백만원이면 작을 수도.
                        amount_won = int(v) * unit
                        break

                if amount_won is None and filtered:
                    v, _ = filtered[0]
                    amount_won = int(v) * unit

                # YoY% 찾기 — 증감률/전년대비 키워드 근처
                # 윈도우 내에서 "증감률" 또는 "전년동기대비" 뒤의 소수
                pct_match = re.search(
                    r'(?:증감률|전년(?:동기)?(?:대비)?|대비)[^%\n]{0,100}?([\-]?\d+\.?\d*)\s*%?',
                    window)
                if pct_match:
                    try:
                        yoy_pct = float(pct_match.group(1))
                    except ValueError:
                        pass
                # 또는 단순히 소수점 있는 % 값 (% 부호 있는 경우 우선)
                if yoy_pct is None:
                    pct_m2 = re.findall(r'([\-]?\d+\.\d+)\s*%', window)
                    if pct_m2:
                        try:
                            yoy_pct = float(pct_m2[0])
                        except ValueError:
                            pass
                # 마지막 fallback: 필터링된 숫자 중 소수점 있고 합리적 범위(-99~999)인 값
                # 보통 잠정실적 테이블은 [당기, 전년동기, 증감률] 3열이므로
                # filtered[2]이 증감률 (소수점)
                if yoy_pct is None:
                    for v, clean in filtered:
                        if "." in clean and -99 <= v <= 999 and v != amount_won:
                            yoy_pct = v
                            break
                break  # 첫 매칭 키워드 사용

            if amount_won is not None:
                amt_str = _fmt_krw_amount(amount_won)
                if yoy_pct is not None:
                    sign = "+" if yoy_pct >= 0 else ""
                    lines_out.append(f"{emoji} {amt_str} ({sign}{yoy_pct:.1f}%)")
                else:
                    lines_out.append(f"{emoji} {amt_str}")

        if not lines_out:
            return None
        return {"type": "earnings_preview", "summary": lines_out}
    except Exception as e:
        print(f"[DART] _parse_earnings_preview 오류: {e}")
        return None


def _parse_buyback(title: str, body: str) -> dict | None:
    """자기주식 취득/소각. 규모(원) + 주수."""
    try:
        summary = []
        is_cancel = ("소각" in title)
        amount_won = None
        shares = None

        # 취득예정금액 / 소각예정금액 / 총액
        amt_keywords = ["취득예정금액", "소각예정금액", "취득금액", "소각금액", "취득 예정 금액"]
        for kw in amt_keywords:
            idx = body.find(kw)
            if idx < 0:
                continue
            window = body[idx:idx + 300]
            # 숫자 찾기
            nums = re.findall(r'([\-]?\d{1,3}(?:,\d{3})+|\d{4,})', window)
            for n in nums:
                try:
                    v = int(n.replace(",", ""))
                    if v >= 1_000_000:  # 1백만원 이상만 유효
                        amount_won = v
                        break
                except ValueError:
                    continue
            if amount_won:
                break

        # 취득예정주식수 / 소각예정주식수
        share_keywords = ["취득예정주식", "소각예정주식", "취득 예정 주식",
                          "소각 예정 주식", "취득주식수", "소각주식수"]
        for kw in share_keywords:
            idx = body.find(kw)
            if idx < 0:
                continue
            window = body[idx:idx + 300]
            nums = re.findall(r'([\-]?\d{1,3}(?:,\d{3})+|\d{4,})', window)
            for n in nums:
                try:
                    v = int(n.replace(",", ""))
                    if 100 <= v <= 10_000_000_000:  # 합리적 주수 범위
                        shares = v
                        break
                except ValueError:
                    continue
            if shares:
                break

        if amount_won:
            summary.append(f"💼 규모 {_fmt_krw_amount(amount_won)}")
        if shares:
            summary.append(f"💼 주수 {shares:,}주")

        if not summary:
            return None
        return {"type": "buyback_cancel" if is_cancel else "buyback",
                "summary": summary}
    except Exception as e:
        print(f"[DART] _parse_buyback 오류: {e}")
        return None


def _parse_dividend(body: str) -> dict | None:
    """현금·현물배당. DPS + 기준일."""
    try:
        summary = []
        dps = None
        base_date = None

        # 1주당 배당금 (주식배당은 제외하고 현금배당만)
        dps_keywords = ["1주당 현금배당금", "1주당 배당금", "주당 배당금",
                        "1주당현금배당금", "1주당배당금"]
        for kw in dps_keywords:
            idx = body.find(kw)
            if idx < 0:
                continue
            window = body[idx:idx + 300]
            # 보통주/우선주가 나올 수 있음 → 첫 매칭만
            nums = re.findall(r'([\-]?\d{1,3}(?:,\d{3})+|\d{2,})', window)
            for n in nums:
                try:
                    v = int(n.replace(",", ""))
                    if 10 <= v <= 1_000_000:  # 합리적 DPS 범위
                        dps = v
                        break
                except ValueError:
                    continue
            if dps:
                break

        # 배당기준일
        base_keywords = ["배당기준일", "기준일"]
        for kw in base_keywords:
            idx = body.find(kw)
            if idx < 0:
                continue
            window = body[idx:idx + 200]
            # YYYY-MM-DD or YYYY.MM.DD or YYYY년 MM월 DD일 or YYYYMMDD
            m = re.search(r'(20\d{2})[\-.\s년]+(\d{1,2})[\-.\s월]+(\d{1,2})', window)
            if m:
                y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
                base_date = f"{y}-{mo}-{d}"
                break
            m2 = re.search(r'(20\d{6})', window)
            if m2:
                s = m2.group(1)
                base_date = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                break

        if dps is not None:
            summary.append(f"💵 DPS {dps:,}원")
        if base_date:
            summary.append(f"📅 기준일 {base_date}")

        if not summary:
            return None
        return {"type": "dividend", "summary": summary}
    except Exception as e:
        print(f"[DART] _parse_dividend 오류: {e}")
        return None


def _parse_rumor(body: str) -> dict | None:
    """풍문·보도해명. 상태 + 재공시예정일."""
    try:
        summary = []
        status = None

        # 상태 판정 (우선순위: 사실무근 > 부인 > 미확정 > 확인)
        if "사실무근" in body or "사실이 아닙니다" in body or "사실이 아님" in body:
            status = "사실무근"
        elif "부인" in body:
            status = "부인"
        elif "미확정" in body or "확정된 바 없" in body or "확정되지 않" in body:
            status = "미확정"
        elif "사실" in body and ("확인" in body or "인정" in body):
            status = "사실확인"

        # 재공시예정일
        redate = None
        redate_keywords = ["재공시예정일", "향후 재공시", "향후재공시", "추후 재공시"]
        for kw in redate_keywords:
            idx = body.find(kw)
            if idx < 0:
                continue
            window = body[idx:idx + 300]
            m = re.search(r'(20\d{2})[\-.\s년]+(\d{1,2})[\-.\s월]+(\d{1,2})', window)
            if m:
                y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
                redate = f"{y}-{mo}-{d}"
                break
            m2 = re.search(r'(20\d{6})', window)
            if m2:
                s = m2.group(1)
                redate = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                break

        if status:
            summary.append(f"📰 상태: {status}")
        if redate:
            summary.append(f"📅 재공시 예정일: {redate}")

        if not summary:
            return None
        return {"type": "rumor_clarification", "summary": summary}
    except Exception as e:
        print(f"[DART] _parse_rumor 오류: {e}")
        return None


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
# StockAnalysis.com 애널 레이팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def _stockanalysis_ratings(ticker: str) -> dict | None:
    """StockAnalysis.com 비공식 JSON API. 반환: 정규화 dict 또는 None.
    주의: 2초 sleep은 호출자가 관리.
    """
    url = f"https://api.stockanalysis.com/api/symbol/s/{ticker.lower()}/ratings"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status in (429, 403):
                    print(f"[stockanalysis] {ticker} rate limited/blocked ({resp.status}), 30s 백오프")
                    await asyncio.sleep(30)
                    return None
                if resp.status != 200:
                    print(f"[stockanalysis] {ticker} HTTP {resp.status}")
                    return None
                data = await resp.json()
                if data.get("status") != 200:
                    return None
                return _normalize_stockanalysis_response(ticker, data)
    except Exception as e:
        print(f"[stockanalysis] {ticker} {type(e).__name__}: {e}")
        return None


def _normalize_stockanalysis_response(ticker: str, raw: dict) -> dict:
    """응답을 flat 구조로 정규화.
    pt_change_pct = (pt_now - pt_old) / pt_old * 100 (pt_old > 0 일 때만)
    """
    widget = raw.get("data", {}).get("widget", {}).get("all", {}) or {}
    ratings_raw = raw.get("data", {}).get("ratings", []) or []
    ratings = []
    for r in ratings_raw:
        pt_now = r.get("pt_now")
        pt_old = r.get("pt_old")
        pt_change_pct = None
        if pt_now and pt_old and pt_old > 0:
            pt_change_pct = (pt_now - pt_old) / pt_old * 100
        scores = r.get("scores") or {}
        ratings.append({
            "date": r.get("date"),
            "time": r.get("time"),
            "firm": r.get("firm"),
            "analyst": r.get("analyst"),
            "slug": r.get("slug"),
            "action": r.get("action_rt"),
            "rating_new": r.get("rating_new"),
            "rating_old": r.get("rating_old"),
            "pt_now": pt_now,
            "pt_old": pt_old,
            "pt_change_pct": pt_change_pct,
            "stars": scores.get("stars"),
            "success_rate": scores.get("success_rate"),
            "avg_return": scores.get("avg_return"),
            "total_ratings": scores.get("total"),
        })
    return {
        "ticker": ticker.upper(),
        "consensus": {
            "count": widget.get("count", 0),
            "rating": widget.get("consensus"),
            "target": widget.get("price_target"),
        },
        "ratings": ratings,
    }


def _save_us_ratings_to_db(data: dict) -> int:
    """INSERT OR IGNORE (UNIQUE 제약). 반환: 신규 insert 건수.
    db_collector._get_db() 로 연결. fetched_at = datetime.now().isoformat().
    """
    from db_collector import _get_db
    conn = _get_db()
    inserted = 0
    try:
        now_iso = datetime.now().isoformat()
        ticker = data["ticker"]
        for r in data.get("ratings", []):
            cur = conn.execute(
                "INSERT OR IGNORE INTO us_analyst_ratings "
                "(ticker, rating_date, rating_time, firm, analyst, analyst_slug, action, "
                " rating_new, rating_old, pt_now, pt_old, pt_change_pct, "
                " stars, success_rate, avg_return, total_ratings, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, r.get("date"), r.get("time"), r.get("firm"),
                 r.get("analyst"), r.get("slug"), r.get("action"),
                 r.get("rating_new"), r.get("rating_old"),
                 r.get("pt_now"), r.get("pt_old"), r.get("pt_change_pct"),
                 r.get("stars"), r.get("success_rate"),
                 r.get("avg_return"), r.get("total_ratings"), now_iso)
            )
            if cur.rowcount > 0:
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def _save_consensus_snapshot(data: dict) -> None:
    """일일 컨센 스냅샷 (INSERT OR REPLACE). snapshot_date = KST 오늘."""
    from db_collector import _get_db
    conn = _get_db()
    try:
        snap_date = datetime.now(KST).strftime("%Y-%m-%d")
        c = data.get("consensus", {}) or {}
        conn.execute(
            "INSERT OR REPLACE INTO us_consensus_snapshot "
            "(ticker, snapshot_date, analyst_count, consensus_rating, target_avg) "
            "VALUES (?, ?, ?, ?, ?)",
            (data["ticker"], snap_date, c.get("count"), c.get("rating"), c.get("target"))
        )
        conn.commit()
    finally:
        conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 미국 인덱스 유니버스 (S&P 500 / Russell 1000) — 주간 스캔용
# ━━━━━━━━━━━━━━━━━━━━━━━━━
US_SP500_FILE = f"{_DATA_DIR}/us_sp500.json"
US_RUSSELL1000_FILE = f"{_DATA_DIR}/us_russell1000.json"
_US_INDEX_MAX_AGE_DAYS = 30  # 한 달 이상 오래되면 자동 갱신
_SP500_MAX_AGE_DAYS = _US_INDEX_MAX_AGE_DAYS  # 하위 호환 별칭


def _fetch_index_tickers_from_wikipedia(
    url: str,
    *,
    ticker_col_idx: int,
    min_size: int,
    table_id: str | None,
    log_prefix: str,
) -> list[str] | None:
    """Wikipedia 인덱스 페이지 파싱 공통 헬퍼 (S&P 500 / Russell 1000 공용).

    Args:
        url: Wikipedia 페이지 URL.
        ticker_col_idx: 티커가 있는 td 컬럼 인덱스 (S&P 500 = 0, Russell 1000 = 1).
        min_size: 파싱 결과 최소 기대 종목 수. 미만이면 비정상으로 간주.
        table_id: `<table id="...">` 지정 시 우선 탐색. None 이면 첫 wikitable 사용.
        log_prefix: 로그 태그 (예: "sp500", "russell1000").

    Returns:
        티커 리스트 (대문자, BRK.B / BF.B 처럼 점(.) 포함 티커는 그대로 유지).
        파싱 실패 시 None.
    """
    import requests as _req
    from bs4 import BeautifulSoup
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        resp = _req.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"[{log_prefix}] wikipedia HTTP {resp.status_code}")
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        table = None
        if table_id:
            table = soup.find("table", {"id": table_id})
            if table is None:
                # 원래 S&P 500 로직 보존 — id 없으면 첫 wikitable
                table = soup.find("table", {"class": "wikitable"})
        else:
            # Russell 1000 처럼 id 없는 페이지는 구성종목 테이블이 첫 wikitable 이 아닐 수 있음
            # (보통 2번째). 티커 컬럼이 유효한 가장 큰 wikitable 자동 선택.
            wikitables = soup.find_all("table", {"class": "wikitable"})
            for candidate in wikitables:
                rows = candidate.find_all("tr")
                if len(rows) < max(min_size // 2, 50):
                    continue  # 너무 작은 표는 스킵
                first_data = rows[1] if len(rows) > 1 else None
                if first_data is None:
                    continue
                tds = first_data.find_all("td")
                if len(tds) > ticker_col_idx:
                    table = candidate
                    break
        if table is None:
            print(f"[{log_prefix}] wikipedia 구성종목 테이블을 찾을 수 없음")
            return None
        tickers: list[str] = []
        tbody = table.find("tbody") or table
        for tr in tbody.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) <= ticker_col_idx:
                continue
            t = tds[ticker_col_idx].get_text(strip=True)
            if t and len(t) <= 10:
                tickers.append(t.upper())
        if len(tickers) < min_size:
            print(f"[{log_prefix}] 파싱 결과 비정상 ({len(tickers)}개, 최소 {min_size})")
            return None
        return tickers
    except Exception as e:
        print(f"[{log_prefix}] wikipedia fetch 실패: {type(e).__name__}: {e}")
        return None


def _load_index_tickers(
    cache_file: str,
    *,
    fetcher,
    log_prefix: str,
    force_refresh: bool,
    max_age_days: int = _US_INDEX_MAX_AGE_DAYS,
) -> list[str]:
    """인덱스 티커 로더 공통 헬퍼 (캐시 + TTL + fallback 공용 로직).

    Args:
        cache_file: 로컬 JSON 캐시 경로.
        fetcher: 인자 없이 호출 시 티커 리스트(list[str]) 또는 None 반환하는 callable.
        log_prefix: 로그 태그.
        force_refresh: True 면 캐시 유효해도 강제 네트워크 재수집.
        max_age_days: 캐시 TTL (기본 30일).
    """
    try:
        need_refresh = force_refresh
        if not need_refresh:
            if not os.path.exists(cache_file):
                need_refresh = True
            else:
                age_days = (datetime.now().timestamp() - os.path.getmtime(cache_file)) / 86400
                if age_days > max_age_days:
                    need_refresh = True
        if need_refresh:
            tickers = fetcher()
            if tickers:
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump({"updated": datetime.now().isoformat(), "tickers": tickers}, f, ensure_ascii=False, indent=2)
                    print(f"[{log_prefix}] 캐시 갱신: {len(tickers)}개 → {cache_file}")
                except Exception as e:
                    print(f"[{log_prefix}] 캐시 저장 실패: {e}")
                return tickers
            else:
                print(f"[{log_prefix}] Wikipedia 갱신 실패, 기존 캐시 fallback")
        # 캐시 읽기
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return list(data.get("tickers", []))
    except Exception as e:
        print(f"[{log_prefix}] load 실패: {type(e).__name__}: {e}")
    return []


def _fetch_sp500_from_wikipedia() -> list[str] | None:
    """Wikipedia S&P 500 페이지 파싱 → 티커 리스트 반환.
    실패 시 None. BRK.B / BF.B 처럼 점(.)이 들어간 티커는 그대로 반환 (StockAnalysis.com 호환).
    """
    return _fetch_index_tickers_from_wikipedia(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        ticker_col_idx=0,
        min_size=400,
        table_id="constituents",
        log_prefix="sp500",
    )


def _fetch_russell1000_from_wikipedia() -> list[str] | None:
    """Wikipedia Russell 1000 페이지 파싱 → 티커 리스트 반환.
    실패 시 None. Russell 1000 위키 표는 2번째 컬럼이 티커 (index 1).
    """
    return _fetch_index_tickers_from_wikipedia(
        "https://en.wikipedia.org/wiki/Russell_1000_Index",
        ticker_col_idx=1,
        min_size=900,
        table_id=None,
        log_prefix="russell1000",
    )


def load_sp500_tickers(force_refresh: bool = False) -> list[str]:
    """S&P 500 티커 리스트 로더.
    - `data/us_sp500.json` 캐시 파일 사용.
    - 파일 없거나 mtime 이 30일 이상 오래되면 Wikipedia 에서 자동 갱신.
    - 네트워크 실패 시 기존 캐시(있으면) 반환, 없으면 빈 리스트.
    """
    return _load_index_tickers(
        US_SP500_FILE,
        fetcher=_fetch_sp500_from_wikipedia,
        log_prefix="sp500",
        force_refresh=force_refresh,
    )


def load_russell1000_tickers(force_refresh: bool = False) -> list[str]:
    """Russell 1000 (대형+중형주 1000개) 티커 리스트 로더.
    - `data/us_russell1000.json` 캐시 파일 사용.
    - 파일 없거나 mtime 이 30일 이상 오래되면 Wikipedia 에서 자동 갱신.
    - 네트워크 실패 시 기존 캐시(있으면) 반환, 없으면 빈 리스트.
    - 파싱 결과 900개 미만이면 비정상으로 간주 (Russell 1000 인덱스는 ~1000개 구성).
    """
    return _load_index_tickers(
        US_RUSSELL1000_FILE,
        fetcher=_fetch_russell1000_from_wikipedia,
        log_prefix="russell1000",
        force_refresh=force_refresh,
    )


def load_us_scan_universe() -> list[str]:
    """주간 US 레이팅 스캔 유니버스 = S&P 500 ∪ Russell 1000 합집합 (정렬된 리스트).
    - 둘 중 하나가 실패해도 나머지라도 반환 (방어적).
    - 중복 제거 + 정렬 후 반환.
    """
    merged: set[str] = set()
    try:
        sp = load_sp500_tickers()
        if sp:
            merged.update(sp)
    except Exception as e:
        print(f"[us_universe] S&P 500 로드 실패: {type(e).__name__}: {e}")
    try:
        rs = load_russell1000_tickers()
        if rs:
            merged.update(rs)
    except Exception as e:
        print(f"[us_universe] Russell 1000 로드 실패: {type(e).__name__}: {e}")
    return sorted(merged)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# StockAnalysis.com 애널 메타 + HTML 파싱 (3단계)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def _fetch_analyst_coverage_html(slug: str) -> dict | None:
    """StockAnalysis.com 애널 페이지 HTML 파싱.
    URL: https://stockanalysis.com/analysts/{slug}/
    반환: {
        "slug": str, "name": str, "firm": str,
        "stars": float, "success_rate": float, "total_ratings": int,
        "coverage": [{"ticker": str, "sector": str}]  # 애널이 커버하는 종목
    }
    실패 시 None.
    주의: 호출자가 2초 sleep 관리.
    """
    from bs4 import BeautifulSoup
    import re
    url = f"https://stockanalysis.com/analysts/{slug}/"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    print(f"[analyst_html] {slug} HTTP {resp.status}")
                    return None
                html = await resp.text()
        soup = BeautifulSoup(html, "lxml")

        # 이름 추출 — h1 또는 페이지 타이틀
        name_tag = soup.find("h1")
        name = name_tag.get_text(strip=True) if name_tag else slug.replace("-", " ").title()

        # 메타 정보 (firm, stars, success_rate, total_ratings)
        firm = None
        stars = None
        success_rate = None
        total_ratings = None

        # 1차: 페이지 내 embedded JSON payload (가장 안정적)
        #   예: data:{firm:"JP Morgan",name:"Mark Strouse",count:318,
        #           scores:{score:73.62,stars:4.59,total:227,...,success_rate:54.19}
        try:
            m_firm = re.search(r'firm:"([^"]{1,80})"', html)
            if m_firm:
                firm = m_firm.group(1)
            m_stars = re.search(r'stars:\s*([0-9]+\.?[0-9]*)', html)
            if m_stars:
                stars = float(m_stars.group(1))
            m_succ = re.search(r'success_rate:\s*([0-9]+\.?[0-9]*)', html)
            if m_succ:
                success_rate = float(m_succ.group(1))
            # JSON 내 count:N (페이지 "Total ratings" 표시값). scores.total 과 다름.
            m_total = re.search(r'\bcount:\s*(\d+)', html)
            if m_total:
                total_ratings = int(m_total.group(1))
        except Exception:
            pass

        # 2차 fallback: DOM 기반 ("Stock Analyst at {firm}", aria-label stars, "Total ratings")
        if not firm:
            for p in soup.find_all(["p", "span", "div"]):
                txt = p.get_text(" ", strip=True)
                m = re.search(r"Stock Analyst at\s+(.+)", txt)
                if m:
                    firm = m.group(1).strip()[:80]
                    break
        if stars is None:
            st = soup.find(attrs={"aria-label": re.compile(r"Rated\s+[\d.]+\s+out of 5 stars")})
            if st:
                m = re.search(r"Rated\s+([\d.]+)", st.get("aria-label", ""))
                if m:
                    stars = float(m.group(1))
        if total_ratings is None:
            # DOM: <div>318</div><div>Total ratings</div>
            for d in soup.find_all("div"):
                if d.get_text(strip=True).lower() == "total ratings":
                    prev = d.find_previous_sibling("div")
                    if prev:
                        m = re.search(r"(\d+)", prev.get_text(strip=True))
                        if m:
                            total_ratings = int(m.group(1))
                            break

        # Coverage — 레이팅 테이블의 unique 티커 추출 (2번째 셀 <a> 태그).
        # stockanalysis.com 애널 페이지는 섹터 컬럼이 없으므로 sector=None.
        coverage = []
        seen_tickers = set()
        for table in soup.find_all("table"):
            thead = table.find("thead")
            if not thead:
                continue
            htxt = thead.get_text(" ", strip=True).lower()
            if not any(kw in htxt for kw in ("symbol", "ticker", "stock")):
                continue
            tbody = table.find("tbody")
            if not tbody:
                continue
            for row in tbody.find_all("tr"):
                cells = row.find_all("td")
                ticker_val = None
                # 우선 각 셀의 첫 <a> 태그 텍스트가 유효 티커면 사용
                for c in cells:
                    a = c.find("a")
                    if a:
                        cand = a.get_text(strip=True).upper()
                        if 1 <= len(cand) <= 5 and cand.isalpha():
                            ticker_val = cand
                            break
                # fallback: 셀 full 텍스트 첫 토큰
                if not ticker_val:
                    for c in cells:
                        tokens = c.get_text(" ", strip=True).split()
                        if tokens:
                            cand = tokens[0].upper()
                            if 1 <= len(cand) <= 5 and cand.isalpha():
                                ticker_val = cand
                                break
                # XXXX = StockAnalysis.com 무료 페이월 마스킹 (10개 이후 티커 숨김). 스킵.
                if ticker_val and ticker_val != "XXXX" and ticker_val not in seen_tickers:
                    seen_tickers.add(ticker_val)
                    coverage.append({"ticker": ticker_val, "sector": None})
            break  # 첫 매칭 테이블만

        return {
            "slug": slug,
            "name": name,
            "firm": firm,
            "stars": stars,
            "success_rate": success_rate,
            "total_ratings": total_ratings,
            "coverage": coverage,
        }
    except Exception as e:
        print(f"[analyst_html] {slug} {type(e).__name__}: {e}")
        return None


def _upsert_analyst_meta(data: dict) -> None:
    """us_analysts 에 메타 UPSERT (기존 watched 플래그 보존)."""
    import json as _json
    from db_collector import _get_db
    conn = _get_db()
    try:
        slug = data["slug"]
        # sectors 는 coverage 의 sector 중 unique 를 JSON 배열로
        sectors = sorted({c.get("sector") for c in data.get("coverage", []) if c.get("sector")})
        sectors_json = _json.dumps(sectors, ensure_ascii=False)
        # watched 보존 UPSERT
        conn.execute(
            "INSERT INTO us_analysts (slug, name, firm, sectors, stars, success_rate, "
            " total_ratings, watched, curated_at, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?) "
            "ON CONFLICT(slug) DO UPDATE SET "
            " name=excluded.name, firm=excluded.firm, sectors=excluded.sectors, "
            " stars=excluded.stars, success_rate=excluded.success_rate, "
            " total_ratings=excluded.total_ratings, last_updated=excluded.last_updated",
            (slug, data.get("name"), data.get("firm"), sectors_json,
             data.get("stars"), data.get("success_rate"), data.get("total_ratings"),
             datetime.now().isoformat())
        )
        # coverage UPSERT
        for cov in data.get("coverage", []):
            conn.execute(
                "INSERT OR REPLACE INTO us_analyst_coverage "
                "(analyst_slug, ticker, sector, last_seen) VALUES (?, ?, ?, ?)",
                (slug, cov["ticker"], cov.get("sector"), datetime.now().strftime("%Y-%m-%d"))
            )
        conn.commit()
    finally:
        conn.close()


async def build_top_analysts_candidates(limit: int = 100, days: int = 180) -> list:
    """us_analyst_ratings 집계로 톱 N 후보 생성.
    stars * log(count) 가중치 정렬.
    watched=1 플래그 자동 설정 안 함 — 사용자 확정 대기.

    반환: [{slug, name, firm, avg_stars, avg_success_rate, call_count, score}, ...]
    """
    from db_collector import _get_db
    import math
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT analyst_slug, analyst, firm, "
            "       AVG(stars), AVG(success_rate), COUNT(*) "
            "FROM us_analyst_ratings "
            "WHERE analyst_slug IS NOT NULL AND stars IS NOT NULL "
            "  AND rating_date >= date('now', ?) "
            "GROUP BY analyst_slug HAVING COUNT(*) >= 5 AND AVG(stars) >= 3.5 "
            "ORDER BY AVG(stars) DESC",
            (f"-{days} days",)
        ).fetchall()
        candidates = []
        for r in rows:
            slug, name, firm, avg_s, avg_sr, cnt = r
            score = (avg_s or 0) * math.log((cnt or 0) + 1)
            candidates.append({
                "slug": slug, "name": name, "firm": firm,
                "avg_stars": avg_s, "avg_success_rate": avg_sr,
                "call_count": cnt, "score": round(score, 2)
            })
        # score 순 정렬
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:limit]
    finally:
        conn.close()


async def fetch_and_store_analyst_meta(slug: str) -> bool:
    """단일 애널 HTML 파싱 + us_analysts/coverage UPSERT. 성공 True."""
    data = await _fetch_analyst_coverage_html(slug)
    if not data:
        return False
    _upsert_analyst_meta(data)
    return True


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 미국 애널 레이팅 — 보유 감시 알림 중복 방지 저장소
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _load_us_holdings_sent() -> dict:
    """us_holdings_sent.json 로드 + 48h 초과 엔트리 자동 정리.
    스키마: {ticker_YYYY-MM-DD: {sent_at: ISO, events_count: int, downgrades: [str]}}
    cleanup: sent_at 이 48h 초과된 엔트리 제거.
    """
    data = load_json(US_HOLDINGS_SENT_FILE, {})
    cutoff = datetime.now() - timedelta(hours=48)
    cleaned = {}
    for k, v in data.items():
        try:
            sent_at = datetime.fromisoformat(v.get("sent_at", ""))
            if sent_at >= cutoff:
                cleaned[k] = v
        except (ValueError, TypeError, AttributeError):
            continue  # 파싱 실패 시 엔트리 drop
    if len(cleaned) != len(data):
        save_json(US_HOLDINGS_SENT_FILE, cleaned)  # 정리 반영
    return cleaned


def _save_us_holdings_sent(data: dict) -> None:
    save_json(US_HOLDINGS_SENT_FILE, data)


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
                # 빈 dict/list는 백업 스킵
                try:
                    parsed = json.loads(content)
                    if parsed == {} or parsed == []:
                        continue
                except Exception:
                    pass
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
        s = _get_session()
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
        s = _get_session()
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
        s = _get_session()
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
        session = _get_session()
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


def _calc_regime_v2() -> dict:
    """S&P 500 200MA + VIX 기반 레짐 판정 (조건부 로직)."""
    indicators = {}

    # 1. S&P 500 vs 200MA
    sp_signal = "🟡"
    sp_data = {"price": None, "sma200": None, "distance_pct": None,
               "sma200_slope": None, "signal": "🟡"}
    try:
        sp_hist = _yf_history("^GSPC", "1y")
        if len(sp_hist) >= 220:
            price = sp_hist[-1]
            sma200 = sum(sp_hist[-200:]) / 200
            sma200_20d_ago = sum(sp_hist[-220:-20]) / 200
            dist_pct = (price - sma200) / sma200 * 100
            slope_change = (sma200 - sma200_20d_ago) / sma200_20d_ago * 100
            slope = "rising" if slope_change > 0.3 else ("declining" if slope_change < -0.3 else "flat")
            sp_data = {
                "price": round(price, 2),
                "sma200": round(sma200, 2),
                "distance_pct": round(dist_pct, 2),
                "sma200_slope": slope,
                "signal": "🟢" if dist_pct > 3 else ("🔴" if dist_pct < -3 else "🟡"),
            }
    except Exception as e:
        print(f"[regime] S&P 조회 실패: {e}")
    indicators["sp500_vs_200ma"] = sp_data

    # 2. VIX + VIX 텀스트럭처
    vix_data = {"value": None, "vix3m": None, "term_ratio": None,
                "backwardation": False, "signal": "🟡"}
    try:
        vix_hist = _yf_history("^VIX", "1mo")
        vix_val = vix_hist[-1] if vix_hist else None

        vix3m_val = None
        try:
            v3m_hist = _yf_history("^VIX3M", "1mo")
            vix3m_val = v3m_hist[-1] if v3m_hist else None
        except Exception:
            pass
        if vix3m_val is None:
            try:
                v9d_hist = _yf_history("^VIX9D", "1mo")
                vix3m_val = v9d_hist[-1] if v9d_hist else None
            except Exception:
                pass

        if vix_val:
            term_ratio = round(vix_val / vix3m_val, 4) if vix3m_val and vix3m_val > 0 else None
            backwardation = bool(term_ratio and term_ratio > 1.0)
            sig = "🟢" if vix_val < 20 else ("🔴" if (vix_val > 30 or backwardation) else "🟡")
            vix_data = {
                "value": round(vix_val, 2),
                "vix3m": round(vix3m_val, 2) if vix3m_val else None,
                "term_ratio": term_ratio,
                "backwardation": backwardation,
                "signal": sig,
            }
    except Exception as e:
        print(f"[regime] VIX 조회 실패: {e}")
    indicators["vix"] = vix_data

    # 3. 레짐 판정 (조건부)
    sp_dist = sp_data.get("distance_pct")
    sp_slope = sp_data.get("sma200_slope")
    vix_val = vix_data.get("value")
    vix_back = vix_data.get("backwardation", False)

    regime_en = "neutral"
    logic_parts = []

    # 🟢 Offensive
    if (sp_dist is not None and sp_dist > 3 and
        vix_val is not None and vix_val < 20 and
        sp_slope == "rising"):
        regime_en = "offensive"
        logic_parts.append(f"S&P +{sp_dist:.2f}% above 200MA (🟢)")
        logic_parts.append(f"VIX {vix_val:.1f} < 20 (🟢)")
        logic_parts.append("SMA200 rising → 🟢 Offensive")
    # 🔴 Crisis
    elif (sp_dist is not None and sp_dist < -3 and
          vix_val is not None and (vix_val > 30 or vix_back)):
        regime_en = "crisis"
        logic_parts.append(f"S&P {sp_dist:.2f}% below 200MA (🔴)")
        if vix_val > 30:
            logic_parts.append(f"VIX {vix_val:.1f} > 30 (🔴) → 🔴 Crisis")
        else:
            logic_parts.append(f"VIX backwardation (term_ratio={vix_data['term_ratio']:.3f}) → 🔴 Crisis")
    else:
        if sp_dist is not None:
            logic_parts.append(f"S&P {sp_dist:+.2f}% from 200MA")
        if vix_val is not None:
            logic_parts.append(f"VIX {vix_val:.1f}")
        logic_parts.append("→ 🟡 Neutral")

    return {
        "regime_en": regime_en,
        "indicators": indicators,
        "logic": " AND ".join(logic_parts),
    }


def _regime_emoji(regime_en: str) -> str:
    return {"offensive": "🟢 탐욕", "neutral": "🟡 중립", "crisis": "🔴 공포"}.get(regime_en, "🟡 중립")


async def _fetch_usd_krw_value() -> dict:
    """USD/KRW 환율 (참고용, 레짐 판정에 미사용)."""
    usd_krw = None
    try:
        fx = await get_yahoo_quote("KRW=X")
        if fx:
            usd_krw = float(fx.get("price", 0) or 0)
    except Exception:
        pass
    return {
        "value": round(usd_krw, 1) if usd_krw else None,
        "note": "참고용 (레짐 판정에 미사용)",
    }


def _calc_tranche_level(vix_val: float | None) -> int | None:
    """VIX 트랜치 레벨 (🔴 내부 단계). VIX 30~40=1, 40~50=2, 50+=3."""
    if vix_val is None:
        return None
    if vix_val < 30:
        return None
    if vix_val < 40:
        return 1
    if vix_val < 50:
        return 2
    return 3


async def cmd_regime(mode: str = "current", days: int = 5,
                     regime: str = "", reason: str = "", **_kwargs) -> dict:
    """시장 레짐 판정 v2 — S&P 500 200MA + VIX 2개 지표 기반 조건부 로직."""
    state = load_json(REGIME_STATE_FILE, {"history": [], "current": {}})

    # ── override ──
    if mode == "override":
        if regime not in ("crisis", "neutral", "offensive"):
            return {"error": "regime must be one of: crisis, neutral, offensive"}
        today = datetime.now(KST).strftime("%Y-%m-%d")
        entry = {"date": today, "regime": regime, "override": True,
                 "reason": reason or "수동 강제"}
        state["current"] = {
            "current": regime,
            "days_in_regime": 1, "debounce_count": 99, "confirmed": True,
            "tranche_level": None, "last_updated": today,
            "override": True, "override_reason": reason or "수동 강제",
        }
        state.setdefault("history", []).append(entry)
        state["history"] = state["history"][-90:]
        save_json(REGIME_STATE_FILE, state)
        return {"regime": _regime_emoji(regime), "regime_en": regime,
                "mode": "override", "reason": reason, "date": today}

    # ── history ──
    if mode == "history":
        h = state.get("history", [])
        return {"history": h[-days:], "total_records": len(h)}

    # ── current ──
    today = datetime.now(KST).strftime("%Y-%m-%d")
    calc = _calc_regime_v2()
    new_regime = calc["regime_en"]
    indicators = calc["indicators"]
    vix_val = indicators["vix"]["value"]

    cur = state.get("current", {}) or {}
    prev_regime = cur.get("current", "neutral")
    debounce_count = int(cur.get("debounce_count", 0) or 0)
    days_in_regime = int(cur.get("days_in_regime", 0) or 0)

    # 디바운스 로직
    confirmed_regime = prev_regime
    if new_regime == prev_regime:
        # 같은 레짐 유지
        debounce_count += 1
        days_in_regime += 1
        confirmed_regime = prev_regime
    else:
        # 다른 레짐 감지 → 디바운스 카운트 시작/증가
        if cur.get("pending_regime") == new_regime:
            debounce_count += 1
        else:
            debounce_count = 1

        # 진입 조건
        threshold = 5 if new_regime == "offensive" else (3 if new_regime == "crisis" else 1)

        # 🟢→🟡, 🔴→🟡 즉시 가능 (Crisis exit는 별도 조건)
        if new_regime == "neutral":
            if prev_regime == "offensive":
                confirmed_regime = "neutral"
                debounce_count = 1
                days_in_regime = 1
            elif prev_regime == "crisis":
                # VIX < 25 OR S&P가 200MA -3% 이내
                sp_dist = indicators["sp500_vs_200ma"].get("distance_pct")
                if (vix_val is not None and vix_val < 25) or (sp_dist is not None and sp_dist > -3):
                    confirmed_regime = "neutral"
                    debounce_count = 1
                    days_in_regime = 1
                else:
                    confirmed_regime = prev_regime  # 유지
        elif debounce_count >= threshold:
            confirmed_regime = new_regime
            days_in_regime = 1

    pending = new_regime if confirmed_regime != new_regime else None
    tranche = _calc_tranche_level(vix_val) if confirmed_regime == "crisis" else None

    # USD/KRW (참고용, indicators에 포함)
    indicators["usd_krw"] = await _fetch_usd_krw_value()

    # state 저장
    new_state_cur = {
        "current": confirmed_regime,
        "days_in_regime": days_in_regime,
        "debounce_count": debounce_count,
        "confirmed": confirmed_regime == new_regime,
        "tranche_level": tranche,
        "pending_regime": pending,
        "last_updated": today,
        "indicators": indicators,
    }
    state["current"] = new_state_cur
    state["prev_regime"] = prev_regime  # 텔레그램 알림용

    # history 기록
    h_entry = {"date": today, "regime": confirmed_regime,
               "sp_distance_pct": indicators["sp500_vs_200ma"].get("distance_pct"),
               "vix": vix_val}
    hist = state.get("history", [])
    if hist and hist[-1].get("date") == today:
        hist[-1] = h_entry
    else:
        hist.append(h_entry)
    state["history"] = hist[-90:]
    save_json(REGIME_STATE_FILE, state)

    # 결과 조립
    debounce_msg = (
        f"{_regime_emoji(confirmed_regime)} {days_in_regime}일차 (확정)"
        if pending is None
        else f"→{_regime_emoji(pending)} 전환 대기 {debounce_count}일차"
    )

    return {
        "regime": _regime_emoji(confirmed_regime),
        "regime_en": confirmed_regime,
        "indicators": indicators,
        "tranche_level": tranche,
        "debounce": {
            "current": confirmed_regime,
            "days": days_in_regime,
            "confirmed": pending is None,
            "pending": pending,
            "text": debounce_msg,
        },
        "logic": calc["logic"],
        "date": today,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# YouTube 자막 추출
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_YT_URL_RE = re.compile(
    r"(?:v=|vi=|/v/|/vi/|/shorts/|/embed/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})"
)


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


async def fetch_polymarket(top: int = 10, min_volume: float = 500_000,
                            query: str = "") -> dict:
    """Polymarket 매크로/지정학/정치 prediction market 조회.

    24시간 거래량 정렬, sports/esports/pop culture 노이즈 자동 컷,
    min_volume 이하 제외 (저거래량 = 노이즈).

    Args:
        top: 반환 시장 수 (기본 10)
        min_volume: 최소 누적 거래량 USD (기본 500K)
        query: 키워드 (예: "Fed", "Iran", "Trump") 시 제목·설명 매칭 필터

    Returns:
        {"markets": [{title, prob_yes, prob_no, change_24h, change_7d, volume, vol_24h, end_date, tags}], "fetched_at": ...}
    """
    url = f"{POLYMARKET_API}/events"
    params = {
        "limit": "100",
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
    }
    # ssl=False — read-only public API, macOS aiohttp 인증서 이슈 우회
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        try:
            async with s.get(url, params=params,
                             timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return {"error": f"Polymarket HTTP {r.status}"}
                data = await r.json()
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    if not isinstance(data, list):
        return {"error": "unexpected response", "raw": str(data)[:200]}

    query_lower = (query or "").lower()
    results = []
    for ev in data:
        # 노이즈 태그 1개라도 있으면 제외
        tags = [t.get("label", "") for t in ev.get("tags", [])]
        if any(n in tags for n in _POLY_NOISE_TAGS):
            continue
        # 거래량 필터
        vol_total = float(ev.get("volume", 0) or 0)
        if vol_total < min_volume:
            continue
        # 키워드 필터
        title = ev.get("title", "")
        if query_lower:
            desc_blob = (title + " " + (ev.get("description") or "")[:300]).lower()
            if query_lower not in desc_blob:
                continue
        # 멀티 아웃컴 이벤트: 모든 sub-market의 (그룹제목, YES 확률, 변동) 추출
        markets = ev.get("markets", [])
        outcomes = []
        import json as _j
        for m in markets:
            try:
                op = m.get("outcomePrices", "[]")
                if isinstance(op, str):
                    op = _j.loads(op)
                if not op or len(op) < 1:
                    continue
                yes_prob = float(op[0])
                # group title (예: "Hold rates", "Cut 25bp")
                grp = (m.get("groupItemTitle") or m.get("question") or "").strip()
                chg_24h = float(m.get("oneDayPriceChange", 0) or 0) if m.get("oneDayPriceChange") is not None else None
                chg_7d = float(m.get("oneWeekPriceChange", 0) or 0) if m.get("oneWeekPriceChange") is not None else None
                outcomes.append({
                    "outcome": grp[:60],
                    "prob": round(yes_prob, 4),
                    "change_24h": round(chg_24h, 4) if chg_24h is not None else None,
                    "change_7d": round(chg_7d, 4) if chg_7d is not None else None,
                })
            except Exception:
                pass

        # 확률 내림차순 정렬, 가장 높은 거 우선
        outcomes.sort(key=lambda x: x.get("prob", 0), reverse=True)
        # binary 시장 (Yes/No 1개): outcomes[0]만 의미. 멀티: 상위 5개 표시
        is_binary = (len(outcomes) == 1)
        top_outcome = outcomes[0] if outcomes else None

        results.append({
            "title": title,
            "is_binary": is_binary,
            "top_outcome": top_outcome,  # 가장 가능성 큰 결과
            "outcomes": outcomes[:5],  # 멀티시 상위 5개
            "vol_total": vol_total,
            "vol_24h": float(ev.get("volume24hr", 0) or 0),
            "vol_1wk": float(ev.get("volume1wk", 0) or 0),
            "end_date": (ev.get("endDate", "") or "")[:10],
            "tags": tags[:4],
            "slug": ev.get("slug", ""),
        })
        if len(results) >= top:
            break

    return {
        "count": len(results),
        "min_volume": min_volume,
        "query": query,
        "fetched_at": datetime.now(KST).isoformat(),
        "markets": results,
    }


async def fetch_treasury_curve() -> dict:
    """미국 Treasury 수익률 곡선 — 침체 시그널 (10Y-2Y, 10Y-3M).

    FRED 공개 CSV (no API key) 사용. 최근 5거래일 데이터.
    역전 (10Y-2Y < 0) = Estrella-Mishkin 1998 NY Fed 침체 선행지표.

    Returns:
        {"yields": {"10y", "2y", "3m"}, "spreads": {"10y_2y", "10y_3m"},
         "spreads_1w_ago", "recession_signal": "정상/주의/역전"}
    """
    series = {"10y": "DGS10", "2y": "DGS2", "3m": "DGS3MO"}
    yields = {}
    yields_1w_ago = {}

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        for key, sid in series.items():
            url = f"{FRED_BASE}?id={sid}"
            try:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        continue
                    text = await r.text()
            except Exception:
                continue
            # CSV 파싱: 첫 줄 헤더, 마지막 N줄 = 최근 데이터
            lines = [ln for ln in text.strip().split("\n")[1:] if "," in ln]
            if not lines:
                continue
            # 최근 비결측 값
            for ln in reversed(lines):
                parts = ln.split(",")
                if len(parts) >= 2 and parts[1] not in (".", "", "NA"):
                    try:
                        yields[key] = float(parts[1])
                        break
                    except Exception:
                        pass
            # 1주 전 (5영업일 전 정도) 비결측
            if len(lines) >= 7:
                for ln in reversed(lines[:-5]):
                    parts = ln.split(",")
                    if len(parts) >= 2 and parts[1] not in (".", "", "NA"):
                        try:
                            yields_1w_ago[key] = float(parts[1])
                            break
                        except Exception:
                            pass

    spread_10y_2y = None
    spread_10y_3m = None
    spread_10y_2y_1w = None
    if "10y" in yields and "2y" in yields:
        spread_10y_2y = round(yields["10y"] - yields["2y"], 3)
    if "10y" in yields and "3m" in yields:
        spread_10y_3m = round(yields["10y"] - yields["3m"], 3)
    if "10y" in yields_1w_ago and "2y" in yields_1w_ago:
        spread_10y_2y_1w = round(yields_1w_ago["10y"] - yields_1w_ago["2y"], 3)

    # 침체 시그널 (Estrella-Mishkin 1998)
    if spread_10y_2y is None:
        signal = "데이터 부족"
    elif spread_10y_2y < 0:
        signal = "역전 (침체 선행)"
    elif spread_10y_2y < 0.25:
        signal = "주의 (역전 임박)"
    else:
        signal = "정상"

    return {
        "yields": yields,
        "yields_1w_ago": yields_1w_ago,
        "spread_10y_2y": spread_10y_2y,
        "spread_10y_3m": spread_10y_3m,
        "spread_10y_2y_1w_ago": spread_10y_2y_1w,
        "recession_signal": signal,
        "fetched_at": datetime.now(KST).isoformat(),
    }


def _ensure_pension_table(db_path: str):
    """pension_flow_daily 테이블 생성 (idempotent)."""
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pension_flow_daily (
            trade_date     TEXT NOT NULL,
            symbol         TEXT NOT NULL,
            market         TEXT DEFAULT '',
            name           TEXT DEFAULT '',
            net_amount_won INTEGER DEFAULT 0,
            net_qty        INTEGER DEFAULT 0,
            buy_amount_won INTEGER DEFAULT 0,
            sell_amount_won INTEGER DEFAULT 0,
            collected_at   TEXT DEFAULT '',
            PRIMARY KEY (trade_date, symbol)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pf_date ON pension_flow_daily(trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pf_symbol ON pension_flow_daily(symbol)")
    conn.commit()
    conn.close()


def collect_pension_flow_daily(date_str: str = None) -> dict:
    """매일 16:30 KST — 그날 종목별 연기금 매매 수집 → pension_flow_daily DB INSERT.

    Args:
        date_str: YYYYMMDD 형식. 생략 시 오늘.

    Returns:
        {"date": str, "kospi_count": int, "kosdaq_count": int, "saved": int}
    """
    try:
        from pykrx import stock as _krx
    except ImportError:
        return {"error": "pykrx 미설치"}

    if date_str is None:
        date_str = datetime.now(KST).strftime("%Y%m%d")

    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_pension_table(db_path)

    saved = 0
    counts = {}
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    now_iso = datetime.now(KST).isoformat()

    for m in ["KOSPI", "KOSDAQ"]:
        try:
            df = _krx.get_market_net_purchases_of_equities(date_str, date_str, m, "연기금")
        except Exception as e:
            print(f"[pension_flow] {m} {date_str} 실패: {e}")
            counts[m] = 0
            continue
        if df is None or len(df) == 0:
            counts[m] = 0
            continue
        cnt = 0
        for ticker, row in df.iterrows():
            net_amt = int(row.get("순매수거래대금", 0) or 0)
            # 매매가 0인 종목은 스킵 (DB 부피 절감)
            if net_amt == 0:
                continue
            net_qty = int(row.get("순매수거래량", 0) or 0)
            buy_amt = int(row.get("매수거래대금", 0) or 0)
            sell_amt = int(row.get("매도거래대금", 0) or 0)
            name = str(row.get("종목명", "") or "")
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO pension_flow_daily
                       (trade_date, symbol, market, name,
                        net_amount_won, net_qty, buy_amount_won, sell_amount_won,
                        collected_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (date_str, str(ticker), m, name,
                     net_amt, net_qty, buy_amt, sell_amt, now_iso),
                )
                cnt += 1
            except Exception:
                pass
        counts[m] = cnt
        saved += cnt
    conn.commit()
    conn.close()

    return {
        "date": date_str,
        "kospi_count": counts.get("KOSPI", 0),
        "kosdaq_count": counts.get("KOSDAQ", 0),
        "saved": saved,
    }


def fetch_pension_fund_flow(days: int = 5, market: str = "ALL", top: int = 30,
                              held_watch_only: bool = False) -> dict:
    """연기금 (NPS 우세) 종목별 누적 매매 — pykrx + KRX 로그인 활용.

    한국 시장 8개 투자자 분류 중 '연기금' 카테고리 단독.
    NPS가 한국 연기금 매매의 60~80% 비중이라 사실상 NPS 시그널 근사치.

    Args:
        days: 누적 일수 (기본 5)
        market: 'KOSPI' / 'KOSDAQ' / 'ALL' (기본 ALL)
        top: 매수 TOP / 매도 TOP 각각 N개 (기본 30)
        held_watch_only: True면 보유+워치만 필터 (포트 점검용)

    Returns:
        {
          "period": "YYYY-MM-DD ~ YYYY-MM-DD",
          "market": str,
          "buy_top": [{ticker, name, net_amount_won, net_qty}, ...],
          "sell_top": [...],
          "held_watch_flow": [...]   # 보유+워치 양방향
        }
    """
    try:
        from pykrx import stock as _krx
    except ImportError:
        return {"error": "pykrx 미설치"}

    today = datetime.now(KST)
    # 영업일 기준 days 일치 — KRX는 주말/공휴일 자동 스킵
    end_dd = today.strftime("%Y%m%d")
    start_dd = (today - timedelta(days=days * 2 + 3)).strftime("%Y%m%d")  # 여유 있게

    markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    all_rows = {}  # ticker → row dict

    for m in markets:
        try:
            df = _krx.get_market_net_purchases_of_equities(start_dd, end_dd, m, "연기금")
        except Exception as e:
            print(f"[pension_fund] {m} 실패: {e}")
            continue
        if df is None or len(df) == 0:
            continue
        for ticker, row in df.iterrows():
            net_amt = int(row.get("순매수거래대금", 0) or 0)
            net_qty = int(row.get("순매수거래량", 0) or 0)
            name = str(row.get("종목명", "") or "")
            all_rows[str(ticker)] = {
                "ticker": str(ticker),
                "name": name,
                "net_amount_won": net_amt,
                "net_qty": net_qty,
                "market": m,
            }

    # 기간 표시
    period = f"{start_dd[:4]}-{start_dd[4:6]}-{start_dd[6:]} ~ {end_dd[:4]}-{end_dd[4:6]}-{end_dd[6:]}"

    # 보유+워치 필터 (held_watch_only or held_watch_flow 추출용)
    held_watch_set = set()
    try:
        portfolio = load_json(PORTFOLIO_FILE, {})
        for k in portfolio.keys():
            if k not in ("us_stocks", "cash_krw", "cash_usd") and not _is_us_ticker(k):
                held_watch_set.add(k)
        for k in load_watchalert().keys():
            if not _is_us_ticker(k):
                held_watch_set.add(k)
    except Exception:
        pass

    # 정렬 분리
    buy_sorted = sorted(
        [r for r in all_rows.values() if r["net_amount_won"] > 0],
        key=lambda x: -x["net_amount_won"],
    )
    sell_sorted = sorted(
        [r for r in all_rows.values() if r["net_amount_won"] < 0],
        key=lambda x: x["net_amount_won"],
    )

    if held_watch_only:
        buy_sorted = [r for r in buy_sorted if r["ticker"] in held_watch_set]
        sell_sorted = [r for r in sell_sorted if r["ticker"] in held_watch_set]

    # 보유+워치 양방향
    held_watch_flow = sorted(
        [r for r in all_rows.values() if r["ticker"] in held_watch_set],
        key=lambda x: -abs(x["net_amount_won"]),
    )

    return {
        "period": period,
        "market": market,
        "days": days,
        "total_tracked": len(all_rows),
        "buy_top": buy_sorted[:top],
        "sell_top": sell_sorted[:top],
        "held_watch_flow": held_watch_flow,
        "fetched_at": datetime.now(KST).isoformat(),
    }


async def fetch_external_macro_signals(top_polymarket: int = 8) -> dict:
    """외부 매크로 시그널 통합 — Polymarket + Treasury curve + Fed Polymarket.

    한 번 호출로 매크로 전체 외부 베팅 컨센서스 + 금리 곡선 침체 시그널 조회.
    SAT_PORT_CHECK / SUN_DISCOVERY / 매크로 대시보드 자동 통합용.

    Returns:
        {"polymarket": [...], "fed": {...polymarket Fed decision...},
         "treasury": {...}, "summary": "1줄 요약"}
    """
    poly = await fetch_polymarket(top=top_polymarket, min_volume=500_000)
    fed = await fetch_polymarket(top=3, min_volume=100_000, query="Fed decision")
    curve = await fetch_treasury_curve()

    # 1줄 요약
    summary_parts = []
    if not fed.get("error") and fed.get("markets"):
        fed_top = fed["markets"][0]
        top_o = fed_top.get("top_outcome") or {}
        prob = top_o.get("prob")
        outcome_name = top_o.get("outcome", "")
        if prob is not None:
            summary_parts.append(
                f"Fed: {outcome_name} {prob*100:.0f}% ({fed_top['title'][:30]})"
            )
    if not curve.get("error") and curve.get("spread_10y_2y") is not None:
        summary_parts.append(
            f"10Y-2Y: {curve['spread_10y_2y']:+.2f}% ({curve['recession_signal']})"
        )

    return {
        "polymarket": poly,
        "fed": fed,
        "treasury": curve,
        "summary": " | ".join(summary_parts) if summary_parts else "데이터 부족",
        "fetched_at": datetime.now(KST).isoformat(),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NPS 5%룰 보고 (data.go.kr 공공데이터)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 출처: 국민연금공단_국민연금기금 5/10%이상 보유종목
#        publicDataPk=15106890
#        https://www.data.go.kr/data/15106890/fileData.do
# 형식: EUC-KR CSV, 컬럼 = 번호, 발행기관명, 보고서 작성기준일(YYYY-MM-DD), 지분율(퍼센트)
# 갱신 주기: 분기 (직전 분기 약 2개월 후 데이터 게시)
# 누적 전략: data.go.kr 측이 같은 atchFileId 덮어쓰기 → 우리 DB는 (report_date, name) 키로 누적
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NPS_DATA_GO_KR_PAGE = "https://www.data.go.kr/data/15106890/fileData.do"
NPS_FALLBACK_ATCH_FILE_ID = "FILE_000000003618528"  # 2025-12 시점 4Q25 분량


# 한글 → 영문약자 변환 (긴 매핑 우선 — startswith 충돌 방지)
# 예: "에이치디씨"가 "에이치디"보다 먼저 와야 "에이치디씨현대산업개발" → "HDC..."로 정상 변환
_KO_EN_GROUP_MAP = [
    # 4글자+
    ("비지에프", "BGF"),
    ("아이에스시", "ISC"),
    ("알에프에이치아이씨", "RFHIC"),
    ("에이치디현대", "HD현대"),
    ("에이치디씨", "HDC"),
    ("에이치엠엠", "HMM"),
    ("제이와이피", "JYP"),
    ("케이씨씨", "KCC"),
    ("케이티앤지", "KT&G"),
    ("엘아이지", "LIG"),
    ("오씨아이", "OCI"),
    # 3글자
    ("씨제이", "CJ"),
    ("에이치디", "HD"),
    ("케이지", "KG"),
    ("케이티", "KT"),
    ("엘에스", "LS"),
    ("엘엑스", "LX"),
    ("에스케이", "SK"),
    ("와이지", "YG"),
    # 2글자
    ("디비", "DB"),
    ("디엘", "DL"),
    ("지에스", "GS"),
    ("엘지", "LG"),
    ("에스엠", "SM"),
]


def _normalize_company_name(name: str) -> str:
    """발행기관명 → stock_master 매칭용 표준화.

    - "(주)" prefix/suffix 제거
    - 한글표기 그룹명 → 영문약자 (엘지→LG 등)
    - 공백 제거 + 영문 대문자 통일
    """
    if not name:
        return ""
    s = name.strip()
    # 양쪽 (주) 제거
    s = re.sub(r"^\(주\)\s*", "", s)
    s = re.sub(r"\s*\(주\)$", "", s)
    s = s.replace("주식회사", "")
    # 공백 제거
    s = re.sub(r"\s+", "", s)
    # 한글 → 영문약자 prefix 변환
    for ko, en in _KO_EN_GROUP_MAP:
        if s.startswith(ko):
            s = en + s[len(ko):]
            break
    # 자주 쓰는 한글 단어 → 영문약자 (어디 위치하든)
    s = s.replace("엔터테인먼트", "ENT.")
    s = s.replace("에프엔에프", "F&F")
    s = s.replace("일렉트릭", "ELECTRIC")
    # 잘 알려진 동의어 prefix 후처리
    if s.startswith("현대자동차") and len(s) <= 6:
        s = "현대차" + s[5:]
    return s.upper()


def _ensure_nps_holdings_table(db_path: str):
    """nps_holdings_disclosed 테이블 생성 (idempotent)."""
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nps_holdings_disclosed (
            report_date    TEXT NOT NULL,    -- 보고서 작성기준일 (YYYY-MM-DD)
            company_name   TEXT NOT NULL,    -- 발행기관명 (원본 그대로)
            symbol         TEXT DEFAULT '',  -- stock_master 매칭 ticker (없으면 빈 문자열)
            ratio_pct      REAL DEFAULT 0,   -- 지분율(%)
            quarter        TEXT DEFAULT '',  -- '2025Q4' 등
            source_file    TEXT DEFAULT '',  -- atchFileId
            collected_at   TEXT DEFAULT '',
            PRIMARY KEY (report_date, company_name)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nps_date ON nps_holdings_disclosed(report_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nps_symbol ON nps_holdings_disclosed(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nps_quarter ON nps_holdings_disclosed(quarter)")
    conn.commit()
    conn.close()


async def _discover_nps_atch_file_id() -> str:
    """data.go.kr 메타페이지에서 최신 atchFileId 추출.
    실패 시 fallback 상수 반환.
    """
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as s:
            async with s.get(NPS_DATA_GO_KR_PAGE) as r:
                if r.status != 200:
                    return NPS_FALLBACK_ATCH_FILE_ID
                html = await r.text()
        m = re.findall(r"atchFileId=(FILE_\d+)", html)
        # 의미없는 'FILE_null' 같은 패턴 필터, 가장 흔한 ID 채택
        ids = [x for x in m if x.startswith("FILE_") and x != "FILE_null"]
        if ids:
            # 같은 ID가 여러 번 나오면 그게 정답
            from collections import Counter
            top = Counter(ids).most_common(1)[0][0]
            return top
    except Exception as e:
        print(f"[nps_5pct] atchFileId 자동 추출 실패: {e}")
    return NPS_FALLBACK_ATCH_FILE_ID


async def _download_nps_5pct_csv(atch_file_id: str = None) -> tuple:
    """NPS 5%룰 CSV 다운로드 → (rows, atch_file_id) 반환.

    Returns:
        (rows, atch_file_id) — rows = [{"name": str, "report_date": str, "ratio_pct": float}, ...]
    """
    if atch_file_id is None:
        atch_file_id = await _discover_nps_atch_file_id()

    url = (
        f"https://www.data.go.kr/cmm/cmm/fileDownload.do"
        f"?atchFileId={atch_file_id}&fileDetailSn=1&insertDataPrcus=N"
    )
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)
    raw_bytes = b""
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return ([], atch_file_id)
                raw_bytes = await r.read()
    except Exception as e:
        print(f"[nps_5pct] CSV 다운로드 실패: {e}")
        return ([], atch_file_id)

    # EUC-KR / CP949 디코딩 시도
    text = ""
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            text = raw_bytes.decode(enc)
            break
        except Exception:
            continue
    if not text:
        return ([], atch_file_id)

    rows = []
    import csv as _csv
    import io as _io
    reader = _csv.reader(_io.StringIO(text))
    header = None
    for line in reader:
        if not line or all(not c.strip() for c in line):
            continue
        if header is None:
            header = [c.strip() for c in line]
            continue
        if len(line) < 4:
            continue
        # 컬럼: 번호, 발행기관명, 보고서 작성기준일, 지분율(퍼센트)
        try:
            name = line[1].strip()
            report_date = line[2].strip()
            ratio_str = line[3].strip().replace(",", "")
            if not name or not report_date:
                continue
            try:
                ratio = float(ratio_str)
            except Exception:
                ratio = 0.0
            rows.append({
                "name": name,
                "report_date": report_date,
                "ratio_pct": ratio,
            })
        except Exception:
            continue
    return (rows, atch_file_id)


def _date_to_quarter(date_str: str) -> str:
    """'2025-10-14' → '2025Q4'."""
    try:
        y, mo, _ = date_str.split("-")
        m = int(mo)
        q = (m - 1) // 3 + 1
        return f"{y}Q{q}"
    except Exception:
        return ""


def _build_name_to_symbol_map(db_path: str) -> tuple:
    """stock_master 전체에서 (정규화명 → symbol) 매핑 + (정규화명, symbol) 리스트 반환.

    리스트는 substring fallback 매칭용.
    """
    import sqlite3 as _s
    direct = {}
    name_list = []  # [(normalized_name, symbol)]
    try:
        conn = _s.connect(db_path, timeout=30)
        cur = conn.execute("SELECT symbol, name FROM stock_master WHERE name IS NOT NULL AND name != ''")
        for sym, nm in cur.fetchall():
            key = _normalize_company_name(nm)
            if key and key not in direct:
                direct[key] = sym
            if key:
                name_list.append((key, sym))
        conn.close()
    except Exception as e:
        print(f"[nps_5pct] name→symbol map 빌드 실패: {e}")
    return (direct, name_list)


def _match_company_to_symbol(name_csv: str, direct: dict, name_list: list) -> str:
    """발행기관명 → symbol 매칭.

    1차: 직접 정규화 비교
    2차: stock_master 이름이 csv명의 prefix (예: "한국단자" ↔ "한국단자공업")
    3차: csv명이 stock_master 이름의 prefix (예: "코오롱인더스트리" → "코오롱인더")
    길이차 ≤ 7 제한 (오매칭 방지)
    """
    key = _normalize_company_name(name_csv)
    if not key:
        return ""
    if key in direct:
        return direct[key]
    # 2차/3차 fallback
    best_sym = ""
    best_diff = 999
    for nm, sym in name_list:
        if not nm or len(nm) < 3:
            continue
        # case 2: stock_master 이름이 csv 키의 prefix
        if key.startswith(nm) and len(key) - len(nm) <= 7:
            diff = len(key) - len(nm)
            if diff < best_diff:
                best_diff = diff
                best_sym = sym
        # case 3: csv 키가 stock_master 이름의 prefix
        elif nm.startswith(key) and len(nm) - len(key) <= 7:
            diff = len(nm) - len(key)
            if diff < best_diff:
                best_diff = diff
                best_sym = sym
    return best_sym


async def collect_nps_5percent_disclosed() -> dict:
    """NPS 5%룰 CSV 다운로드 + DB 저장 (분기 갱신 시 신규 row만 누적).

    Returns:
        {"total_csv": int, "matched": int, "unmatched": int,
         "inserted_new": int, "atch_file_id": str, "quarters": [...]}
    """
    rows, atch_id = await _download_nps_5pct_csv()
    if not rows:
        return {"error": "CSV 다운로드 실패", "atch_file_id": atch_id}

    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_holdings_table(db_path)
    direct_map, name_list = _build_name_to_symbol_map(db_path)

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    now_iso = datetime.now(KST).isoformat()

    matched = 0
    unmatched_names = []
    inserted_new = 0
    quarters = set()

    for r in rows:
        name = r["name"]
        report_date = r["report_date"]
        ratio = r["ratio_pct"]
        symbol = _match_company_to_symbol(name, direct_map, name_list)
        if symbol:
            matched += 1
        else:
            unmatched_names.append(name)
        quarter = _date_to_quarter(report_date)
        if quarter:
            quarters.add(quarter)

        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO nps_holdings_disclosed
                   (report_date, company_name, symbol, ratio_pct, quarter, source_file, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (report_date, name, symbol, ratio, quarter, atch_id, now_iso),
            )
            if cur.rowcount > 0:
                inserted_new += 1
        except Exception as e:
            print(f"[nps_5pct] insert 실패 {name}: {e}")

    conn.commit()
    conn.close()

    return {
        "total_csv": len(rows),
        "matched": matched,
        "unmatched": len(unmatched_names),
        "unmatched_names": unmatched_names[:20],  # 디버그용 샘플
        "inserted_new": inserted_new,
        "atch_file_id": atch_id,
        "quarters": sorted(quarters),
        "fetched_at": now_iso,
    }


def fetch_nps_holdings(quarter: str = None, days: int = 90, ratio_min: float = 0.0,
                        held_watch_only: bool = False, limit: int = 100) -> dict:
    """NPS 5%룰 보유종목 조회.

    Args:
        quarter: '2025Q4' 등 분기 필터. None이면 days 기간으로.
        days: report_date 기준 최근 N일 (quarter=None일 때만)
        ratio_min: 최소 지분율(%) 필터
        held_watch_only: True면 보유+워치만
        limit: 최대 반환 건수

    Returns:
        {"period": str, "total": int, "rows": [{report_date, company_name, symbol, ratio_pct, ...}]}
    """
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_holdings_table(db_path)

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.row_factory = _s.Row

    where = ["ratio_pct >= ?"]
    params = [ratio_min]

    if quarter:
        where.append("quarter = ?")
        params.append(quarter)
        period_str = quarter
    else:
        cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
        where.append("report_date >= ?")
        params.append(cutoff)
        period_str = f"최근 {days}일"

    sql = f"""
        SELECT report_date, company_name, symbol, ratio_pct, quarter
        FROM nps_holdings_disclosed
        WHERE {' AND '.join(where)}
        ORDER BY report_date DESC, ratio_pct DESC
    """
    rows = []
    for r in conn.execute(sql, params).fetchall():
        rows.append({
            "report_date": r["report_date"],
            "company_name": r["company_name"],
            "symbol": r["symbol"] or "",
            "ratio_pct": float(r["ratio_pct"] or 0),
            "quarter": r["quarter"] or "",
        })
    conn.close()

    # 보유+워치 필터
    if held_watch_only:
        held_watch = set()
        try:
            portfolio = load_json(PORTFOLIO_FILE, {})
            for k in portfolio.keys():
                if k not in ("us_stocks", "cash_krw", "cash_usd") and not _is_us_ticker(k):
                    held_watch.add(k)
            for k in load_watchalert().keys():
                if not _is_us_ticker(k):
                    held_watch.add(k)
        except Exception:
            pass
        rows = [r for r in rows if r["symbol"] in held_watch]

    return {
        "period": period_str,
        "ratio_min": ratio_min,
        "total": len(rows),
        "rows": rows[:limit],
        "fetched_at": datetime.now(KST).isoformat(),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NPS 미국 보유 — SEC EDGAR Form 13F-HR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CIK 0001608046 (National Pension Service)
# 13F-HR: 분기 끝 후 ~45일 내 제출 (예: 4Q25 → 2026-02-10)
# 컬럼: nameOfIssuer, cusip, value(천달러), sshPrnamt(주식수)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NPS_US_CIK = "0001608046"
SEC_USER_AGENT = "stock-bot research arcturusnd@gmail.com"
SEC_BASE = "https://www.sec.gov"
SEC_DATA_BASE = "https://data.sec.gov"


def _ensure_nps_us_table(db_path: str):
    """nps_us_holdings 테이블 생성 (idempotent)."""
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nps_us_holdings (
            accession      TEXT NOT NULL,    -- 13F 파일 ID (예: 0001608046-26-000001)
            filing_date    TEXT,             -- 파일 제출일 (YYYY-MM-DD)
            period_end     TEXT,             -- 분기말일 (YYYY-MM-DD)
            quarter        TEXT,             -- '2025Q4' 등
            cusip          TEXT NOT NULL,
            name_of_issuer TEXT,
            value_usd      INTEGER DEFAULT 0,  -- 13F의 value × 1000 (실제 달러)
            shares         INTEGER DEFAULT 0,
            ticker         TEXT DEFAULT '',
            collected_at   TEXT DEFAULT '',
            PRIMARY KEY (accession, cusip)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npsus_quarter ON nps_us_holdings(quarter)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npsus_period ON nps_us_holdings(period_end)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npsus_ticker ON nps_us_holdings(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npsus_cusip ON nps_us_holdings(cusip)")
    conn.commit()
    conn.close()


def _period_to_quarter(period_end: str) -> str:
    """'2025-12-31' → '2025Q4'."""
    try:
        y, m, _ = period_end.split("-")
        mi = int(m)
        q = (mi - 1) // 3 + 1
        return f"{y}Q{q}"
    except Exception:
        return ""


async def _sec_fetch_text(url: str) -> str:
    """SEC EDGAR/data.sec.gov 호출 — UA 필수, ssl 우회 안전."""
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as s:
        async with s.get(url) as r:
            if r.status != 200:
                raise RuntimeError(f"SEC HTTP {r.status} {url}")
            return await r.text()


async def _sec_list_nps_13f_filings(max_quarters: int = 8) -> list:
    """NPS submissions JSON에서 최근 13F-HR 목록 추출.

    Returns:
        [{"accession", "accession_nodash", "filing_date", "period_end", "quarter"}, ...]
    """
    url = f"{SEC_DATA_BASE}/submissions/CIK{NPS_US_CIK}.json"
    txt = await _sec_fetch_text(url)
    import json as _json
    d = _json.loads(txt)
    recent = d.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    fdates = recent.get("filingDate", [])
    rdates = recent.get("reportDate", [])
    accs = recent.get("accessionNumber", [])
    out = []
    for i, f in enumerate(forms):
        if "13F" not in f:
            continue
        acc = accs[i]
        fd = fdates[i] if i < len(fdates) else ""
        rd = rdates[i] if i < len(rdates) else ""
        out.append({
            "accession": acc,
            "accession_nodash": acc.replace("-", ""),
            "filing_date": fd,
            "period_end": rd,
            "quarter": _period_to_quarter(rd),
        })
        if len(out) >= max_quarters:
            break
    return out


async def _sec_locate_holdings_xml(accession_nodash: str) -> str:
    """13F 파일링 폴더에서 holdings xml 파일명 식별.

    /Archives/edgar/data/{cik}/{acc_nodash}/index.json 에 파일 목록 있음.
    primary_doc.xml 외의 .xml 이 holdings (보통 분기명_v2.xml).
    """
    url = (f"{SEC_BASE}/Archives/edgar/data/"
           f"{int(NPS_US_CIK)}/{accession_nodash}/index.json")
    txt = await _sec_fetch_text(url)
    import json as _json
    d = _json.loads(txt)
    items = d.get("directory", {}).get("item", [])
    for it in items:
        name = it.get("name", "")
        if name.endswith(".xml") and name != "primary_doc.xml":
            return name
    raise RuntimeError(f"holdings xml not found in {accession_nodash}")


async def _sec_fetch_holdings(accession_nodash: str, holdings_filename: str) -> list:
    """holdings xml 다운로드 + 파싱.

    Returns:
        [{cusip, name_of_issuer, value_usd, shares}, ...]
    """
    url = (f"{SEC_BASE}/Archives/edgar/data/"
           f"{int(NPS_US_CIK)}/{accession_nodash}/{holdings_filename}")
    txt = await _sec_fetch_text(url)
    import xml.etree.ElementTree as _ET
    # ns 무시: tag 끝부분만 비교
    root = _ET.fromstring(txt)
    rows = []
    for info in root.iter():
        tag = info.tag.split("}")[-1] if "}" in info.tag else info.tag
        if tag != "infoTable":
            continue
        rec = {}
        for child in info.iter():
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag == "nameOfIssuer":
                rec["name_of_issuer"] = (child.text or "").strip()
            elif ctag == "cusip":
                rec["cusip"] = (child.text or "").strip()
            elif ctag == "value":
                # 2023 SEC 개정 후 actual dollars (이전 thousands였음).
                # NPS 13F는 2025년 분기만 사용 → actual dollars 그대로.
                try:
                    rec["value_usd"] = int(float(child.text or 0))
                except Exception:
                    rec["value_usd"] = 0
            elif ctag == "sshPrnamt":
                try:
                    rec["shares"] = int(float(child.text or 0))
                except Exception:
                    rec["shares"] = 0
        if rec.get("cusip"):
            rec.setdefault("name_of_issuer", "")
            rec.setdefault("value_usd", 0)
            rec.setdefault("shares", 0)
            rows.append(rec)
    return rows


async def collect_nps_us_13f(max_quarters: int = 4) -> dict:
    """NPS 미국 13F-HR 분기 holdings 자동 수집 → DB 누적.

    Args:
        max_quarters: 직전 N개 분기까지 수집 (기본 4 = 최근 1년)

    Returns:
        {"quarters_processed": N, "total_rows_inserted": int, "filings": [...]}
    """
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_us_table(db_path)

    try:
        filings = await _sec_list_nps_13f_filings(max_quarters=max_quarters)
    except Exception as e:
        return {"error": f"submissions JSON 실패: {e}"}

    if not filings:
        return {"error": "13F-HR 파일링 없음"}

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    now_iso = datetime.now(KST).isoformat()

    total_inserted = 0
    processed = []

    for fl in filings:
        acc = fl["accession"]
        acc_nd = fl["accession_nodash"]
        # 이미 수집된 accession은 스킵 (속도 최적화)
        existing = conn.execute(
            "SELECT COUNT(*) FROM nps_us_holdings WHERE accession=?",
            (acc,),
        ).fetchone()[0]
        if existing > 0:
            processed.append({**fl, "status": "skip-existing", "rows": existing})
            continue

        try:
            holdings_fn = await _sec_locate_holdings_xml(acc_nd)
            # SEC fair-use rate limit (10 req/sec). 보수적으로 0.2초.
            await asyncio.sleep(0.2)
            rows = await _sec_fetch_holdings(acc_nd, holdings_fn)
            await asyncio.sleep(0.2)
        except Exception as e:
            processed.append({**fl, "status": "fetch-fail", "error": str(e)})
            continue

        ins = 0
        for r in rows:
            try:
                cur = conn.execute(
                    """INSERT OR REPLACE INTO nps_us_holdings
                       (accession, filing_date, period_end, quarter,
                        cusip, name_of_issuer, value_usd, shares, ticker, collected_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (acc, fl["filing_date"], fl["period_end"], fl["quarter"],
                     r["cusip"], r.get("name_of_issuer", ""),
                     r.get("value_usd", 0), r.get("shares", 0),
                     "", now_iso),
                )
                if cur.rowcount > 0:
                    ins += 1
            except Exception:
                pass
        total_inserted += ins
        processed.append({**fl, "status": "inserted", "rows": ins})

    conn.commit()
    conn.close()

    return {
        "quarters_processed": len(processed),
        "total_rows_inserted": total_inserted,
        "filings": processed,
        "fetched_at": now_iso,
    }


def fetch_nps_us_holdings(quarter: str = None, top: int = 30,
                            include_changes: bool = False) -> dict:
    """NPS 미국 보유 종목 조회 (가치 정렬).

    Args:
        quarter: '2025Q4' 등. None이면 가장 최신 분기 자동.
        top: TOP N (기본 30)
        include_changes: True면 직전 분기 대비 ▲/▼ 정보 포함

    Returns:
        {"quarter", "period_end", "total_holdings", "total_value_usd",
         "rows": [{name, cusip, ticker, value_usd, shares, weight_pct,
                   change_pct (전 분기 대비), status (NEW/UP/DOWN/EXIT/HELD)}]}
    """
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_us_table(db_path)

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.row_factory = _s.Row

    if not quarter:
        row = conn.execute(
            "SELECT quarter FROM nps_us_holdings ORDER BY period_end DESC LIMIT 1"
        ).fetchone()
        if not row:
            conn.close()
            return {"error": "데이터 없음. collect_nps_us_13f 호출 필요"}
        quarter = row["quarter"]

    cur_rows = conn.execute(
        """SELECT period_end, cusip, name_of_issuer, value_usd, shares, ticker
           FROM nps_us_holdings WHERE quarter = ? ORDER BY value_usd DESC""",
        (quarter,),
    ).fetchall()

    if not cur_rows:
        conn.close()
        return {"error": f"분기 {quarter} 데이터 없음"}

    period_end = cur_rows[0]["period_end"]
    total_value = sum(r["value_usd"] for r in cur_rows)

    # 직전 분기 비교용 데이터
    prev_map = {}  # cusip → {value_usd, shares}
    if include_changes:
        prev_q_row = conn.execute(
            "SELECT DISTINCT quarter FROM nps_us_holdings "
            "WHERE quarter < ? ORDER BY quarter DESC LIMIT 1",
            (quarter,),
        ).fetchone()
        if prev_q_row:
            prev_q = prev_q_row["quarter"]
            for pr in conn.execute(
                """SELECT cusip, value_usd, shares
                   FROM nps_us_holdings WHERE quarter = ?""",
                (prev_q,),
            ).fetchall():
                prev_map[pr["cusip"]] = {
                    "value_usd": pr["value_usd"], "shares": pr["shares"]
                }
    conn.close()

    # EXIT 판정용: 현재 분기 *전체* cusip 집합
    cur_cusips_all = set(r["cusip"] for r in cur_rows)

    rows = []
    for r in cur_rows[:top]:
        d = {
            "cusip": r["cusip"],
            "name_of_issuer": r["name_of_issuer"],
            "ticker": r["ticker"] or "",
            "value_usd": r["value_usd"],
            "shares": r["shares"],
            "weight_pct": (r["value_usd"] * 100.0 / total_value) if total_value > 0 else 0,
        }
        if include_changes:
            prev = prev_map.get(r["cusip"])
            if not prev:
                d["status"] = "NEW"
                d["share_change_pct"] = None
                d["value_change_pct"] = None
            else:
                prev_sh = prev["shares"] or 0
                prev_v = prev["value_usd"] or 0
                d["share_change_pct"] = ((r["shares"] - prev_sh) * 100.0 / prev_sh) if prev_sh > 0 else None
                d["value_change_pct"] = ((r["value_usd"] - prev_v) * 100.0 / prev_v) if prev_v > 0 else None
                if d["share_change_pct"] is None:
                    d["status"] = "HELD"
                elif d["share_change_pct"] > 1:
                    d["status"] = "UP"
                elif d["share_change_pct"] < -1:
                    d["status"] = "DOWN"
                else:
                    d["status"] = "HELD"
        rows.append(d)

    # EXIT 종목 (직전 분기에만 있음)
    exits = []
    if include_changes and prev_map:
        for cusip, prev in prev_map.items():
            if cusip in cur_cusips_all:
                continue
            # 이름은 직전 분기에서 가져와야 함
            conn = _s.connect(db_path, timeout=30)
            conn.row_factory = _s.Row
            nm_row = conn.execute(
                "SELECT name_of_issuer FROM nps_us_holdings WHERE cusip=? LIMIT 1",
                (cusip,),
            ).fetchone()
            conn.close()
            exits.append({
                "cusip": cusip,
                "name_of_issuer": nm_row["name_of_issuer"] if nm_row else "?",
                "prev_value_usd": prev["value_usd"],
                "prev_shares": prev["shares"],
                "status": "EXIT",
            })
        exits.sort(key=lambda x: -x.get("prev_value_usd", 0))

    return {
        "quarter": quarter,
        "period_end": period_end,
        "total_holdings": len(cur_rows),
        "total_value_usd": total_value,
        "rows": rows,
        "exits_top10": exits[:10] if include_changes else [],
        "fetched_at": datetime.now(KST).isoformat(),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NPS 한국 풀 포트 200종목 — whale-insight.com 데이터
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# data.js의 NPS_KR.krTimeline (200종목, 분기 갱신)
# 5%룰 보고 + NPS 사업보고서를 수동 큐레이션한 데이터.
# 컬럼: name, weight%, valuation(억), change%, share24%, share25Q4%
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHALE_INSIGHT_DATA_JS = "https://whale-insight.com/assets/js/data.js"


def _ensure_nps_kr_full_table(db_path: str):
    """nps_kr_full_holdings 테이블 — whale-insight 분기 풀포트 미러."""
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nps_kr_full_holdings (
            snapshot_date  TEXT NOT NULL,    -- 우리 수집 시점 (YYYY-MM-DD)
            quarter_label  TEXT,             -- '2025Q4' (data.js의 share25Q4 참조)
            name           TEXT NOT NULL,    -- whale-insight 원본 종목명
            symbol         TEXT DEFAULT '',  -- stock_master 매칭 ticker
            weight_pct     REAL DEFAULT 0,   -- 포트 비중%
            valuation_eok  INTEGER DEFAULT 0, -- 평가액 억원
            change_pct     REAL DEFAULT 0,   -- 전 분기 대비 (whale-insight 원본)
            share_prev_pct REAL DEFAULT 0,   -- share24 (전 연도 지분율)
            share_curr_pct REAL DEFAULT 0,   -- share25Q4 (현 분기 지분율)
            data_missing   INTEGER DEFAULT 0,
            source_version TEXT,             -- data.js v 쿼리값
            collected_at   TEXT,
            PRIMARY KEY (snapshot_date, name)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npskr_full_date ON nps_kr_full_holdings(snapshot_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_npskr_full_symbol ON nps_kr_full_holdings(symbol)")
    conn.commit()
    conn.close()


def _parse_pct(s) -> tuple:
    """'+0.5%' / '-1.2%' / '데이터 부족' → (float, missing_flag)."""
    if s is None:
        return (0.0, 1)
    txt = str(s).strip()
    if "부족" in txt or not txt or txt == "—":
        return (0.0, 1)
    try:
        return (float(txt.replace("%", "").replace("+", "").strip()), 0)
    except Exception:
        return (0.0, 1)


def _parse_eok(s) -> int:
    """'230,421억' → 230421 (억원)."""
    if not s:
        return 0
    try:
        v = str(s).replace(",", "").replace("억", "").strip()
        return int(v)
    except Exception:
        return 0


async def collect_nps_kr_full_from_whale_insight() -> dict:
    """whale-insight.com data.js 다운로드 → NPS_KR.krTimeline 추출 → DB 저장.

    출처 표기 의무: 카드에 '출처: whale-insight.com' 명시.
    """
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_kr_full_table(db_path)

    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as s:
            async with s.get(WHALE_INSIGHT_DATA_JS) as r:
                if r.status != 200:
                    return {"error": f"HTTP {r.status}"}
                txt = await r.text()
    except Exception as e:
        return {"error": f"download failed: {e}"}

    # 버전 추출 (URL에 ?v=20260427_0 형태로 노출되는 게 있다면 변경 시점 식별)
    src_version = ""
    vm = re.search(r"data\.js\?v=([^\"'>\s]+)", txt)
    if vm:
        src_version = vm.group(1)

    # NPS_KR.krTimeline 블록만 추출
    # id: 'NPS_KR' ... krTimeline: [ ... ] 다음 } 닫힘 전까지
    block_match = re.search(
        r"id:\s*['\"]NPS_KR['\"].*?krTimeline:\s*\[(.*?)\]",
        txt, re.DOTALL,
    )
    if not block_match:
        return {"error": "NPS_KR.krTimeline 블록 못 찾음"}
    block = block_match.group(1)

    # 각 row: { name: '...', weight: '...%', valuation: '...억', change: '...', share24: '...%', share25Q4: '...%' },
    row_pattern = re.compile(
        r"\{\s*"
        r"name:\s*['\"]([^'\"]+)['\"],\s*"
        r"weight:\s*['\"]([^'\"]+)['\"],\s*"
        r"valuation:\s*['\"]([^'\"]+)['\"],\s*"
        r"change:\s*['\"]([^'\"]+)['\"],\s*"
        r"share24:\s*['\"]([^'\"]+)['\"],\s*"
        r"share25Q4:\s*['\"]([^'\"]+)['\"]\s*"
        r"\}"
    )
    matches = row_pattern.findall(block)
    if not matches:
        return {"error": "row 파싱 실패"}

    # 추출한 quarter 라벨 (share25Q4 → 2025Q4)
    quarter_label = "2025Q4"  # 데이터 컬럼명에서 추정. 차후 분기 바뀌면 갱신 필요.
    snap_date = datetime.now(KST).strftime("%Y-%m-%d")

    direct_map, name_list = _build_name_to_symbol_map(db_path)

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    now_iso = datetime.now(KST).isoformat()

    inserted = 0
    matched = 0
    unmatched_names = []
    for (name, weight, valuation, change, share24, share25q4) in matches:
        symbol = _match_company_to_symbol(name, direct_map, name_list)
        if symbol:
            matched += 1
        else:
            unmatched_names.append(name)
        weight_v, weight_miss = _parse_pct(weight)
        chg_v, chg_miss = _parse_pct(change)
        s_prev, sp_miss = _parse_pct(share24)
        s_curr, sc_miss = _parse_pct(share25q4)
        eok = _parse_eok(valuation)
        miss = 1 if (weight_miss or chg_miss or sp_miss or sc_miss) else 0
        try:
            conn.execute(
                """INSERT OR REPLACE INTO nps_kr_full_holdings
                   (snapshot_date, quarter_label, name, symbol,
                    weight_pct, valuation_eok, change_pct,
                    share_prev_pct, share_curr_pct,
                    data_missing, source_version, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (snap_date, quarter_label, name, symbol,
                 weight_v, eok, chg_v, s_prev, s_curr,
                 miss, src_version, now_iso),
            )
            inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()

    return {
        "snapshot_date": snap_date,
        "quarter_label": quarter_label,
        "total_rows": len(matches),
        "inserted": inserted,
        "matched": matched,
        "unmatched": len(unmatched_names),
        "unmatched_sample": unmatched_names[:10],
        "source_version": src_version,
        "fetched_at": now_iso,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# whale-insight 5%룰/10%룰 변동 데이터 미러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# major_stock.js (5%↑ 변동) + elestock.js (10%↑ 보유자 매매)
# 우리 자체 수집보다 더 풍부한 데이터 (DART 직파싱 추정)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WI_MAJOR_JS = "https://whale-insight.com/assets/js/nps_major_stock.js"
WI_ELE_JS = "https://whale-insight.com/assets/js/nps_elestock.js"


def _ensure_wi_change_tables(db_path: str):
    """whale-insight 변동 데이터 테이블."""
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wi_5pct_changes (
            report_date    TEXT NOT NULL,
            company        TEXT NOT NULL,
            symbol         TEXT DEFAULT '',
            stkqy          INTEGER DEFAULT 0,
            stkqy_irds     INTEGER DEFAULT 0,
            stkrt          REAL DEFAULT 0,
            stkrt_irds     REAL DEFAULT 0,
            report_resn    TEXT DEFAULT '',
            source_version TEXT DEFAULT '',
            collected_at   TEXT DEFAULT '',
            PRIMARY KEY (report_date, company, stkrt_irds)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wi5_date ON wi_5pct_changes(report_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wi5_symbol ON wi_5pct_changes(symbol)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wi_10pct_insiders (
            report_date    TEXT NOT NULL,
            company        TEXT NOT NULL,
            symbol         TEXT DEFAULT '',
            stkqy          INTEGER DEFAULT 0,
            stkqy_irds     INTEGER DEFAULT 0,
            stkrt          REAL DEFAULT 0,
            stkrt_irds     REAL DEFAULT 0,
            shrholdr_role  TEXT DEFAULT '',
            source_version TEXT DEFAULT '',
            collected_at   TEXT DEFAULT '',
            PRIMARY KEY (report_date, company, stkrt_irds)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wi10_date ON wi_10pct_insiders(report_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wi10_symbol ON wi_10pct_insiders(symbol)")
    conn.commit()
    conn.close()


def _parse_int_with_sign(s) -> int:
    """'+193731' / '-25,733,062' / '0' → int."""
    if s is None:
        return 0
    txt = str(s).replace(",", "").strip()
    try:
        return int(float(txt))
    except Exception:
        return 0


def _parse_float_with_sign(s) -> float:
    """'-13.63' / '+0.60' / '0' → float."""
    if s is None:
        return 0.0
    txt = str(s).replace(",", "").replace("%", "").strip()
    try:
        return float(txt)
    except Exception:
        return 0.0


async def _fetch_wi_js_array(url: str, var_name: str) -> list:
    """whale-insight JS 파일 다운로드 → const VAR_NAME = [...] 배열 추출."""
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return []
                txt = await r.text()
    except Exception as e:
        print(f"[wi_fetch] {url} 실패: {e}")
        return []

    # const VAR_NAME = [ ... ];
    m = re.search(rf"{var_name}\s*=\s*\[(.*?)\];", txt, re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    # 객체 단위로 파싱 — { key: 'value', ... }
    obj_pattern = re.compile(r"\{([^}]+)\}", re.DOTALL)
    field_pattern = re.compile(r"(\w+):\s*'([^']*)'")
    out = []
    for obj_match in obj_pattern.finditer(block):
        body = obj_match.group(1)
        rec = {}
        for fm in field_pattern.finditer(body):
            rec[fm.group(1)] = fm.group(2)
        if rec:
            out.append(rec)
    return out


async def collect_wi_changes() -> dict:
    """whale-insight 5%룰 변동 + 10%↑ 보유자 매매 데이터 미러링."""
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_wi_change_tables(db_path)

    direct_map, name_list = _build_name_to_symbol_map(db_path)

    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    now_iso = datetime.now(KST).isoformat()

    # ── 5%룰 변동 (major_stock) ──
    major = await _fetch_wi_js_array(WI_MAJOR_JS, "MAJOR_DATA")
    major_inserted = 0
    for rec in major:
        try:
            sym = _match_company_to_symbol(rec.get("company", ""), direct_map, name_list)
            cur = conn.execute(
                """INSERT OR REPLACE INTO wi_5pct_changes
                   (report_date, company, symbol, stkqy, stkqy_irds,
                    stkrt, stkrt_irds, report_resn, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.get("date", ""),
                    rec.get("company", ""),
                    sym,
                    _parse_int_with_sign(rec.get("stkqy")),
                    _parse_int_with_sign(rec.get("stkqy_irds")),
                    _parse_float_with_sign(rec.get("stkrt")),
                    _parse_float_with_sign(rec.get("stkrt_irds")),
                    rec.get("report_resn", ""),
                    now_iso,
                ),
            )
            if cur.rowcount > 0:
                major_inserted += 1
        except Exception:
            pass

    # ── 10%↑ 보유자 매매 (elestock) ──
    ele = await _fetch_wi_js_array(WI_ELE_JS, "ELESTOCK_DATA")
    ele_inserted = 0
    for rec in ele:
        try:
            sym = _match_company_to_symbol(rec.get("company", ""), direct_map, name_list)
            cur = conn.execute(
                """INSERT OR REPLACE INTO wi_10pct_insiders
                   (report_date, company, symbol, stkqy, stkqy_irds,
                    stkrt, stkrt_irds, shrholdr_role, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.get("date", ""),
                    rec.get("company", ""),
                    sym,
                    _parse_int_with_sign(rec.get("stkqy")),
                    _parse_int_with_sign(rec.get("stkqy_irds")),
                    _parse_float_with_sign(rec.get("stkrt")),
                    _parse_float_with_sign(rec.get("stkrt_irds")),
                    rec.get("isu_main_shrholdr", ""),
                    now_iso,
                ),
            )
            if cur.rowcount > 0:
                ele_inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()

    return {
        "major_total": len(major),
        "major_inserted": major_inserted,
        "ele_total": len(ele),
        "ele_inserted": ele_inserted,
        "fetched_at": now_iso,
    }


def fetch_nps_kr_full_holdings(top: int = 30) -> dict:
    """NPS KR 풀 포트 조회 (가장 최근 snapshot, 비중 내림차순)."""
    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_nps_kr_full_table(db_path)
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.row_factory = _s.Row
    snap_row = conn.execute(
        "SELECT snapshot_date FROM nps_kr_full_holdings ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    if not snap_row:
        conn.close()
        return {"error": "데이터 없음. collect_nps_kr_full_from_whale_insight() 호출 필요"}
    snap = snap_row["snapshot_date"]
    rows = conn.execute(
        """SELECT name, symbol, weight_pct, valuation_eok, change_pct,
                  share_prev_pct, share_curr_pct, data_missing, quarter_label
           FROM nps_kr_full_holdings
           WHERE snapshot_date = ?
           ORDER BY weight_pct DESC""",
        (snap,),
    ).fetchall()
    total_eok = sum(r["valuation_eok"] for r in rows)
    conn.close()
    out = []
    for r in rows[:top]:
        out.append({
            "name": r["name"],
            "symbol": r["symbol"] or "",
            "weight_pct": float(r["weight_pct"] or 0),
            "valuation_eok": int(r["valuation_eok"] or 0),
            "change_pct": float(r["change_pct"] or 0),
            "share_prev_pct": float(r["share_prev_pct"] or 0),
            "share_curr_pct": float(r["share_curr_pct"] or 0),
            "data_missing": bool(r["data_missing"]),
            "share_change_p": (
                float(r["share_curr_pct"]) - float(r["share_prev_pct"])
                if not r["data_missing"] else None
            ),
        })
    return {
        "snapshot_date": snap,
        "quarter_label": rows[0]["quarter_label"] if rows else "",
        "total_holdings": len(rows),
        "total_valuation_eok": total_eok,
        "rows": out,
        "fetched_at": datetime.now(KST).isoformat(),
    }
