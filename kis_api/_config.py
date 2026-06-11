"""환경변수, 경로 상수, 타임존 설정."""
import os
from datetime import timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 환경변수 & 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━
TELEGRAM_TOKEN      = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID             = os.environ.get("CHAT_ID")
DART_TELEGRAM_TOKEN = os.environ.get("DART_TELEGRAM_TOKEN", "")  # DART 전용 봇 (미설정 시 메인 봇)
DART_CHAT_ID        = os.environ.get("DART_CHAT_ID", "")          # DART 전용 채팅 (미설정 시 CHAT_ID)
KIS_APP_KEY    = os.environ.get("KIS_APP_KEY")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET")
DART_API_KEY   = os.environ.get("DART_API_KEY", "")

KIS_BASE_URL  = "https://openapi.koreainvestment.com:9443"
DART_BASE_URL = "https://opendart.fss.or.kr/api"
KST = timezone(timedelta(hours=9))
ET  = ZoneInfo('America/New_York')  # DST 자동 감지 (서머타임 EDT/표준시 EST)

_DATA_DIR = os.environ.get("DATA_DIR", "/data")
os.makedirs(_DATA_DIR, exist_ok=True)
_DB_PATH  = f"{_DATA_DIR}/stock.db"

# ━━ 파일 경로 (17개) ━━
WATCHLIST_FILE         = f"{_DATA_DIR}/watchlist.json"
STOPLOSS_FILE          = f"{_DATA_DIR}/stoploss.json"
US_WATCHLIST_FILE      = f"{_DATA_DIR}/us_watchlist.json"
DART_SEEN_FILE         = f"{_DATA_DIR}/dart_seen.json"
PORTFOLIO_FILE         = f"{_DATA_DIR}/portfolio.json"
WATCHALERT_FILE        = f"{_DATA_DIR}/watchalert.json"
WATCH_SENT_FILE        = f"{_DATA_DIR}/watch_sent.json"
STOPLOSS_SENT_FILE     = f"{_DATA_DIR}/stoploss_sent.json"
US_HOLDINGS_SENT_FILE  = f"{_DATA_DIR}/us_holdings_sent.json"
DECISION_LOG_FILE      = f"{_DATA_DIR}/decision_log.json"
COMPARE_LOG_FILE       = f"{_DATA_DIR}/compare_log.json"
WATCHLIST_LOG_FILE     = f"{_DATA_DIR}/watchlist_log.json"
EVENTS_FILE            = f"{_DATA_DIR}/events.json"
WEEKLY_BASE_FILE       = f"{_DATA_DIR}/weekly_base.json"
UNIVERSE_FILE          = f"{_DATA_DIR}/stock_universe.json"
CONSENSUS_CACHE_FILE   = f"{_DATA_DIR}/consensus_cache.json"
PORTFOLIO_HISTORY_FILE = f"{_DATA_DIR}/portfolio_history.json"
TRADE_LOG_FILE         = f"{_DATA_DIR}/trade_log.json"
SECTOR_FLOW_CACHE_FILE = f"{_DATA_DIR}/sector_flow_cache.json"
SECTOR_ROTATION_FILE   = f"{_DATA_DIR}/sector_rotation.json"
SUPPLY_HISTORY_FILE    = f"{_DATA_DIR}/supply_history.json"
REPORTS_FILE           = f"{_DATA_DIR}/reports.json"
REGIME_STATE_FILE      = f"{_DATA_DIR}/regime_state.json"
MACRO_SENT_FILE        = f"{_DATA_DIR}/macro_sent.json"
SILENT_FAILURE_LOG     = f"{_DATA_DIR}/silent_failure_log.json"
TOKEN_CACHE_FILE       = f"{_DATA_DIR}/token_cache.json"

# ━━ GitHub Gist 백업 ━━
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
_BACKUP_GIST_ENV  = "BACKUP_GIST_ID"


_BACKUP_FILES_LIST = [
    STOPLOSS_FILE, PORTFOLIO_FILE,
    WATCHALERT_FILE, WATCHLIST_LOG_FILE, PORTFOLIO_HISTORY_FILE,
    TRADE_LOG_FILE, CONSENSUS_CACHE_FILE, DECISION_LOG_FILE,
    REGIME_STATE_FILE,
]

# ━━ 매크로 Yahoo Finance 심볼 ━━
MACRO_SYMBOLS = {
    "VIX":    "^VIX",
    "WTI":    "CL=F",
    "GOLD":   "GC=F",
    "COPPER": "HG=F",
    "DXY":    "DX-Y.NYB",
    "US10Y":  "^TNX",
    "SP500":  "^GSPC",
}
