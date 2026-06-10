# dashboard_home 패키지 셸 (2026-06 분해 P1) — core.py verbatim
# 외부 표면은 main_pkg/_entry.py가 쓰는 2심볼 + characterization test 전체.
# 로직 변경 없음 — 순수 re-export.

from .core import (
    # ── 공개 표면 (main_pkg/_entry.py 등 외부 소비자) ─────────────────────────
    register_home_routes,
    warm_caches,

    # ── 대형 문자열 상수 (characterization hash 잠금 13개) ─────────────────────
    # __doc__ 은 아래 별도 처리
    _DASH_APP_JS,
    _HOME_PANEL,
    _HOME_SHELL,
    _MARKET_PANEL,
    _PORTFOLIO_PANEL,
    _RECORD_PANEL,
    _REPORT_PANEL,
    _SIGNAL_PANEL,
    _US_PANEL,
    _WATCH_PANEL,
    _WHALE_PANEL,
    _WHALE_PANEL_REMOVED,

    # ── DB 헬퍼 (characterization test 직접 호출) ──────────────────────────────
    _open_db,
    _sync_reports_payload,
    _sync_reports_by_ticker,
    _reports_by_ticker,

    # ── Whale 빌더 (characterization test 직접 호출) ───────────────────────────
    _whale_home,
    _whale_kr_5pct,
    _whale_kr_full,
    _whale_us_13f,
    _whale_pension,
    _whale_insider,
    build_whale_payload,

    # ── 추가 공개 빌더 (상위 소비자용) ────────────────────────────────────────
    build_reports_payload,
)

# __doc__: core 모듈 독스트링을 패키지 네임스페이스에 그대로 노출
# → characterization test의 __doc__ sha256 검증 통과
from .core import __doc__
