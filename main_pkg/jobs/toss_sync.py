"""main_pkg jobs — 토스증권 잔고 → portfolio.json 1분 주기 자동 동기화."""
from telegram.ext import ContextTypes

from kis_api import sync_portfolio_from_toss, load_json
from kis_api import _DATA_DIR as _KIS_DATA_DIR
from kis_api.toss import _TOSS_CLIENT_ID, _TOSS_CLIENT_SECRET
from main_pkg._ctx import _refresh_ws

import os as _os

# ── W3: 연속 실패 쿨다운 ────────────────────────────────────────
_fail_streak: int = 0  # 연속 실패 횟수 (성공 시 0으로 리셋)
_FAIL_LOG_INTERVAL: int = 30  # 연속 실패 시 30회(30분)마다 1회 출력

_PORTFOLIO_FILE = _os.path.join(_KIS_DATA_DIR, "portfolio.json")
_META_KEYS = {"us_stocks", "cash_krw", "cash_usd"}


def _holding_sig(portfolio: dict) -> frozenset:
    """portfolio dict 전체 보유 시그니처 반환 — KR + US 종목 집합.

    KR: 최상위 키에서 _META_KEYS 제외.
    US: us_stocks 딕셔너리의 키에 'US:' 접두사 (KR 코드 충돌 방지).
    수량이 아닌 종목 집합 변동만 감지하므로 add/remove 시에만 WS 재구독.
    """
    kr_keys = frozenset(k for k in portfolio if k not in _META_KEYS)
    us_raw = portfolio.get("us_stocks", {})
    us_keys = frozenset(f"US:{t}" for t in us_raw) if isinstance(us_raw, dict) else frozenset()
    return kr_keys | us_keys


async def toss_sync_job(context: ContextTypes.DEFAULT_TYPE):
    """1분 주기로 토스증권 잔고를 portfolio.json에 동기화한다.

    - 토스 키 미설정 시 즉시 return (로그 스팸 방지).
    - 텔레그램 메시지 전송 금지 (1분 주기이므로 무음).
    - 실패 또는 예외 시에만 print 로깅 (W3: 연속 실패 30분 쿨다운).
    - KR/US 보유 집합 변동 시 WebSocket 손절구독 갱신 (W2).
    """
    global _fail_streak

    if not _TOSS_CLIENT_ID or not _TOSS_CLIENT_SECRET:
        return

    # W2: sync 전 전체 보유 종목 시그니처 스냅샷 (KR + US)
    before_sig = _holding_sig(load_json(_PORTFOLIO_FILE, {}))

    try:
        result = await sync_portfolio_from_toss()
        if not result.get("ok"):
            _fail_streak += 1
            reason = result.get("reason", "unknown")
            if _fail_streak == 1 or _fail_streak % _FAIL_LOG_INTERVAL == 0:
                print(f"[toss_sync] 동기화 실패 (연속 {_fail_streak}회): {reason}")
            return

        # 성공
        if _fail_streak > 0:
            print(f"[toss_sync] 동기화 회복 (연속 {_fail_streak}회 실패 후)")
        _fail_streak = 0

        # W2: KR/US 보유 집합이 바뀐 경우에만 WS 손절구독 갱신
        after_sig = _holding_sig(load_json(_PORTFOLIO_FILE, {}))
        if after_sig != before_sig:
            try:
                await _refresh_ws()
            except Exception as e:
                print(f"[toss_sync] WS 갱신 오류: {e}")

    except Exception as e:
        _fail_streak += 1
        if _fail_streak == 1 or _fail_streak % _FAIL_LOG_INTERVAL == 0:
            print(f"[toss_sync] 예외 (연속 {_fail_streak}회): {e}")
