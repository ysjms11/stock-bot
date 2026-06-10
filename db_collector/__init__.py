# 패키지 셸 (2026-06 분해 P2) — core.py는 구 단일파일 verbatim,
# 이후 phase에서 도메인 모듈로 분리 예정.
# 외부 인터페이스(import 표면)는 이 __init__이 동결.
#
# monkeypatch 투명성 (P2a):
# 다중 모듈 포워딩 — monkeypatch.setattr(db_collector, X)가 X를 정의한
# 모든 백킹 모듈에 전파, 콜사이트가 어느 모듈에 있든 패치 보임.
# _BACKING = [core, ...] — core를 항상 첫 원소로; 모듈 박리 시 append.

import sys
import types

from . import core  # noqa: F401 — submodule must be importable
from . import _config  # noqa: F401 — P2b-1 박리
from . import technicals  # noqa: F401 — P2b-2 박리
from . import sector  # noqa: F401 — P2b-3 박리
from . import krx  # noqa: F401 — P2b-4 박리
from . import _db  # noqa: F401 — P3-1 박리
from . import master  # noqa: F401 — P3-2 박리
from . import scan  # noqa: F401 — P3-3 박리
from . import dividends  # noqa: F401 — P3-4 박리
from . import alpha  # noqa: F401 — P3-5 박리
from . import financial  # noqa: F401 — P3-6 박리
from . import us_analysts  # noqa: F401 — P3-7 박리
from . import backup  # noqa: F401 — P3-8 박리
from .core import *  # noqa: F401, F403

# 현재 백킹 모듈 목록.  박리된 모듈은 여기에 append하고 명시 re-export도 갱신.
_BACKING: list = [core, _config, technicals, sector, krx, _db,
                  master, scan, dividends, alpha, financial, us_analysts, backup]


# 외부 코드·테스트가 직접 참조하는 private/dunder 심볼 명시 재수출.
# (grep 기반: from db_collector import X / db_collector.X / @patch("db_collector.X"))

# 상수 / 설정 — _config.py 가 실소유자 (P2b-1)
from ._config import (  # noqa: F401
    DB_PATH,
    KRX_DB_DIR,
    _KR_MARKET_HOLIDAYS,
    _is_kr_trading_day,
)

# KRX 파싱 헬퍼 — krx.py 가 실소유자 (P2b-4)
from .krx import (  # noqa: F401
    _pi,
    _pf,
    _parse_market_records,
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
)

# 비동기 락 / DB 접근 — _db.py 가 실소유자 (P3-1)
from ._db import (  # noqa: F401
    db_write_lock,
    _get_db,
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
    _parse_period,
    _build_period,
    _prev_yoy_period,
    _safe_div,
    _pick_net_income,
    _compute_ttm,
    _compute_fscore,
    _compute_mscore,
    _compute_fcf_metrics,
    _update_alpha_metrics,
    _ensure_alpha_columns,
    update_all_alpha_metrics,
)

# 재무 수집 — financial.py 가 실소유자 (P3-6)
from .financial import (  # noqa: F401
    _DART_INTERVAL,
    _collect_dart_full_batch,
    collect_financial_weekly,
    collect_financial_on_disclosure,
)

# iCloud 백업 — backup.py 가 실소유자 (P3-8)
from .backup import backup_to_icloud  # noqa: F401

# 미국 애널 마스터 / 매수 후보 — us_analysts.py 가 실소유자 (P3-7)
from .us_analysts import (  # noqa: F401
    sync_us_analyst_master,
    is_tier_s_analyst,
    find_us_buy_candidates,
)

from .core import (  # noqa: F401
    # 섹터 분류 — sector.py 가 실소유자, core re-import로 이 블록에서 가져옴
    _classify_sector,

    # 공개 함수 (외부 직접 import)
    collect_daily,
    backfill_day_via_chart,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# monkeypatch 투명성 — 다중 모듈 포워딩
# tests가 `monkeypatch.setattr(_dbc, "DB_PATH", ...)` 식으로 패치할 때
# _BACKING 리스트의 모든 모듈에 이름이 있으면 동시에 갱신한다.
# 이로써 코어 내부 참조든, 박리된 서브모듈 내부 참조든 패치가 동일하게 보인다.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class _PackageModule(types.ModuleType):
    """db_collector 패키지 모듈 — setattr을 _BACKING 전 모듈로 전파.

    다중 모듈 포워딩 — monkeypatch.setattr(db_collector, X)가 X를 정의한
    모든 백킹 모듈에 전파, 콜사이트가 어느 모듈에 있든 패치 보임.
    """

    def __setattr__(self, name: str, value):
        super().__setattr__(name, value)
        # _BACKING 전체에 전파 (이름이 존재하는 모든 모듈 대상)
        for mod in _BACKING:
            if hasattr(mod, name):
                setattr(mod, name, value)

    def __delattr__(self, name: str):
        super().__delattr__(name)
        for mod in _BACKING:
            if hasattr(mod, name):
                delattr(mod, name)

    def __getattr__(self, name: str):
        # 명시 re-export 또는 from .core import * 로 이미 바인딩된 이름은
        # 여기까지 오지 않는다 (ModuleType.__getattribute__가 먼저 처리).
        # 폴스루 안전망: 박리 후 re-export를 누락했을 때도 _BACKING 순서대로 탐색.
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
