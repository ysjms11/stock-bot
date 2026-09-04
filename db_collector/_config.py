"""
db_collector 설정 상수 및 거래일 판정 (2026-06 분해 P2b-1).
순수 함수 / 상수만 — 외부 패키지 의존 없음 (stdlib만).
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 타임존
# ━━━━━━━━━━━━━━━━━━━━━━━━━
KST = ZoneInfo("Asia/Seoul")

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 디렉토리 / DB 경로
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = f"{_DATA_DIR}/stock.db"
KRX_DB_DIR = f"{_DATA_DIR}/krx_db"
_STD_SECTOR_MAP_PATH = f"{_DATA_DIR}/std_sector_map.json"

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KRX OPEN API 상수
# ━━━━━━━━━━━━━━━━━━━━━━━━━
KRX_OPENAPI_BASE = "https://data-dbg.krx.co.kr/svc/apis"
KRX_API_KEY = os.environ.get("KRX_API_KEY", "")

_OPENAPI_ENDPOINTS = {
    "market_STK": ("sto", "stk_bydd_trd"),
    "market_KSQ": ("sto", "ksq_bydd_trd"),
}

KRX_JSON_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 타임아웃
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_PHASE_TIMEOUT = 600   # Phase별 타임아웃 10분

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KRX 휴장일 집합
# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 출처: KRX 휴장일(매년 1월 갱신 — main_pkg/telegram_bot.weekly_sanity_check가 갱신 알림).
_KR_MARKET_HOLIDAYS = frozenset({
    # 2026
    "20260101", "20260216", "20260217", "20260218", "20260302", "20260501",
    "20260505", "20260525", "20260603", "20260717", "20260817", "20260924",
    "20260925", "20261005", "20261009", "20261225", "20261231",
    # 20261005 = 개천절(10/3 토) 대체휴일(월). 20261231 = KRX 연말휴장(공휴일 아닌 개장휴무).
    # 20260717 = 제헌절. 2026 공휴일 재지정분 — 미등록 상태로 2026-07-17에 수집이 돌아
    # KIS가 전일(7/16) 시세를 반환 → daily_snapshot에 복제 행 2,864개 유입(2026-07-18 발견).
    # 하드코딩 목록의 구조적 한계이므로 collect_daily에 복제-감지 자가롤백 가드를 병설했다.
    # 2027 (1월 갱신 시 보강)
    "20270101",
    # 2027 제헌절(7/17)은 토요일 — 주말 가드로 이미 커버되어 등록 불필요. 대체휴일 적용
    # 여부 확인 후(제헌절은 관례상 대체공휴일 미적용이나 매년 확정분 재확인 필요) 지정되면
    # 20270719(월) 등록.
})


def _is_kr_trading_day(date: str) -> bool:
    """KR 거래일(개장일) 판정. 주말 또는 KRX 휴장일이면 False. 오프라인·즉시·결정적.

    하드코딩 집합 기반이라 네트워크 불필요(KRX/pykrx 다운에도 견고). 미등록 신규 휴장일은
    weekly_sanity_check 갱신 알림으로 보강한다.
    """
    try:
        dt = datetime.strptime(date, "%Y%m%d")
    except (ValueError, TypeError):
        return True  # 형식 불명 → 보수적으로 수집 허용
    if dt.weekday() >= 5:  # 토(5)/일(6)
        return False
    return date not in _KR_MARKET_HOLIDAYS
