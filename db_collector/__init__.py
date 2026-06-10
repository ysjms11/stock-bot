# 패키지 셸 (2026-06 분해 P4b) — core.py 소멸, 모든 심볼은 도메인 모듈 실소유.
# 외부 인터페이스(import 표면)는 이 __init__이 동결.
#
# monkeypatch 투명성:
# 다중 모듈 포워딩 — monkeypatch.setattr(db_collector, X)가 X를 정의한
# 모든 백킹 모듈에 전파, 콜사이트가 어느 모듈에 있든 패치 보임.
# _BACKING 순서: collect, _config, _db, krx, sector, master, technicals,
#                scan, dividends, alpha, financial, us_analysts, backup

import sys
import types

from . import collect     # noqa: F401 — P4a 박리: 수집 파이프라인
from . import _config     # noqa: F401 — P2b-1 박리
from . import _db         # noqa: F401 — P3-1 박리
from . import krx         # noqa: F401 — P2b-4 박리
from . import sector      # noqa: F401 — P2b-3 박리
from . import master      # noqa: F401 — P3-2 박리
from . import technicals  # noqa: F401 — P2b-2 박리
from . import scan        # noqa: F401 — P3-3 박리
from . import dividends   # noqa: F401 — P3-4 박리
from . import alpha       # noqa: F401 — P3-5 박리
from . import financial   # noqa: F401 — P3-6 박리
from . import us_analysts # noqa: F401 — P3-7 박리
from . import backup      # noqa: F401 — P3-8 박리

# 현재 백킹 모듈 목록.  _BACKING 순서 = 속성 탐색 우선순위.
_BACKING: list = [collect, _config, _db, krx, sector, master, technicals,
                  scan, dividends, alpha, financial, us_analysts, backup]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 명시 re-export — 외부 코드·테스트가 직접 참조하는 심볼
# (grep 기반: from db_collector import X / db_collector.X / @patch("db_collector.X"))
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 상수 / 설정 — _config.py 가 실소유자 (P2b-1)
from ._config import (  # noqa: F401
    KST,
    _DATA_DIR,
    DB_PATH,
    KRX_DB_DIR,
    KRX_OPENAPI_BASE,
    KRX_API_KEY,
    _OPENAPI_ENDPOINTS,
    KRX_JSON_URL,
    KRX_HEADERS,
    _STD_SECTOR_MAP_PATH,
    _PHASE_TIMEOUT,
    _KR_MARKET_HOLIDAYS,
    _is_kr_trading_day,
)

# DB 연결 / 스키마 초기화 / 쓰기 락 — _db.py 가 실소유자 (P3-1)
from ._db import (  # noqa: F401
    db_write_lock,
    _get_db,
    _init_schema,
)

# KRX OPEN API 파서 + fetch 함수 — krx.py 가 실소유자 (P2b-4)
from .krx import (  # noqa: F401
    _pi,
    _pf,
    _krx_openapi_get,
    _krx_post,
    _parse_market_records,
    fetch_krx_market_data,
)

# 섹터 분류 — sector.py 가 실소유자 (P2b-3)
from .sector import (  # noqa: F401
    _STD_CODE_TO_SECTOR,
    _SECTOR_KEYWORD_RULES,
    _SECTOR_CODE_DEFAULTS,
    _SECTOR_OVERRIDES,
    _classify_sector,
    _load_std_sector_map,
)

# 종목 마스터 UPSERT — master.py 가 실소유자 (P3-2)
from .master import (  # noqa: F401
    _sync_stock_master,
    _update_master_from_basic,
)

# 기술지표 헬퍼 — technicals.py 가 실소유자 (P2b-2)
from .technicals import (  # noqa: F401
    _ma,
    _rsi,
    _macd,
    _atr,
    _volatility_20d,
    _calc_vp,
    _volume_ratio,
    _spread_at,
    _rsi_at,
    _load_history_from_db,
    _compute_technicals_sqlite,
)

# 스캐너 / 히스토리 / load_krx_db — scan.py 가 실소유자 (P3-3)
from .scan import (  # noqa: F401
    PRESETS,
    load_krx_db,
    _load_history,
    _get_foreign_streak_data_db,
    _summarize_filters,
    scan_stocks,
)

# 배당 — dividends.py 가 실소유자 (P3-4)
from .dividends import (  # noqa: F401
    _div_num,
    _recompute_div_yield_from_events,
    collect_dividends,
)

# 알파 메트릭 엔진 — alpha.py 가 실소유자 (P3-5)
from .alpha import (  # noqa: F401
    _TTM_FLOW_FIELDS,
    _TTM_STOCK_FIELDS,
    _parse_period,
    _build_period,
    _compute_ttm,
    _prev_yoy_period,
    _fs_source,
    _pick_net_income,
    _safe_div,
    _compute_fscore,
    _compute_mscore,
    _compute_fcf_metrics,
    _ensure_alpha_columns,
    _update_alpha_metrics,
    update_all_alpha_metrics,
    collect_shares_historical,
)

# 재무 수집 — financial.py 가 실소유자 (P3-6)
from .financial import (  # noqa: F401
    _DART_INTERVAL,
    _upsert_dart_full_row,
    _collect_dart_full_batch,
    collect_financial_weekly,
    collect_financial_historical,
    collect_financial_on_disclosure,
    _fetch_supply_data,
    _write_supply_to_snapshot,
    _update_supply_in_snapshot,
    _update_consensus_in_snapshot,
    _update_financial_derived,
)

# iCloud 백업 — backup.py 가 실소유자 (P3-8)
from .backup import backup_to_icloud  # noqa: F401

# 미국 애널 마스터 / 매수 후보 — us_analysts.py 가 실소유자 (P3-7)
from .us_analysts import (  # noqa: F401
    sync_us_analyst_master,
    is_tier_s_analyst,
    find_us_buy_candidates,
)

# 수집 파이프라인 — collect.py 가 실소유자 (P4a)
from .collect import (  # noqa: F401
    _RATE_SEM,
    _rate_limited,
    _collect_phase,
    _store_daily_snapshot,
    _compute_and_update,
    collect_daily,
    backfill_day_via_chart,
    collect_daily_backfill,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# monkeypatch 투명성 — 다중 모듈 포워딩
# tests가 `monkeypatch.setattr(_dbc, "DB_PATH", ...)` 식으로 패치할 때
# _BACKING 리스트의 모든 모듈에 이름이 있으면 동시에 갱신한다.
# 이로써 어느 서브모듈 내부에서 참조하든 패치가 동일하게 보인다.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class _PackageModule(types.ModuleType):
    """db_collector 패키지 모듈 — setattr을 _BACKING 전 모듈로 전파."""

    def __setattr__(self, name: str, value):
        super().__setattr__(name, value)
        for mod in _BACKING:
            if hasattr(mod, name):
                setattr(mod, name, value)

    def __delattr__(self, name: str):
        super().__delattr__(name)
        for mod in _BACKING:
            if hasattr(mod, name):
                delattr(mod, name)

    def __getattr__(self, name: str):
        # 명시 re-export로 이미 바인딩된 이름은 여기까지 오지 않는다.
        # 폴스루 안전망: re-export를 누락했을 때도 _BACKING 순서대로 탐색.
        for mod in _BACKING:
            try:
                return getattr(mod, name)
            except AttributeError:
                continue
        raise AttributeError(f"module 'db_collector' has no attribute '{name!r}'")


# 현재 패키지 모듈 객체를 서브클래스 인스턴스로 교체
_current = sys.modules[__name__]
_proxy = _PackageModule(__name__)
_proxy.__dict__.update(_current.__dict__)
sys.modules[__name__] = _proxy
