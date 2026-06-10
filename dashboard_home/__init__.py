"""dashboard_home — 새 대시보드 P0/P1/P2/P3a/P3b.

/home 경로에 서빙. /dash(dashboard.py)는 무수정.
P0: HTML 쉘 + Alpine 탭 네비 + 빈 패널.
P1: JSON API (/api/home, /api/regime, /api/alerts, /api/portfolio) + 홈 화면 실데이터 바인딩.
P2: 포트폴리오 + 워치·알림 탭.
P3a: Whale 탭 — /api/whale?p=<preset> + Alpine 서브탭 5개.
P3b: 리포트 탭 — /api/reports + /api/reports/{ticker}, 기록 탭 — /api/decisions + /api/trades + /api/invest_todo.
"""
# dashboard_home 패키지 (2026-06 분해 P1→P4) — re-export 표면.
# P2: _assets.py (템플릿/JS 상수).
# P3: _helpers.py, reports.py, whale.py, payloads.py, routes.py.
# P4: core.py 소멸 — 모든 심볼이 owning 모듈에 직접 소유됨.

# ── 대형 문자열 상수 (characterization hash 잠금 13개, 소유: _assets) ────────
from ._assets import (
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
)

# ── 캐시/DB 헬퍼 (소유: _helpers) ─────────────────────────────────────────────
from ._helpers import (
    _open_db,
)

# ── 리포트 빌더 (소유: reports) ───────────────────────────────────────────────
from .reports import (
    _sync_reports_payload,
    _sync_reports_by_ticker,
    _reports_by_ticker,
    build_reports_payload,
)

# ── Whale 빌더 (소유: whale) ──────────────────────────────────────────────────
from .whale import (
    _whale_home,
    _whale_kr_5pct,
    _whale_kr_full,
    _whale_us_13f,
    _whale_pension,
    _whale_insider,
    build_whale_payload,
)

# ── 공개 표면 (main_pkg/_entry.py 등 외부 소비자, 소유: routes) ───────────────
from .routes import (
    register_home_routes,
    warm_caches,
)
