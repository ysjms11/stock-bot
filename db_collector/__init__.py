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
from .core import *  # noqa: F401, F403

# 현재 백킹 모듈 목록.  박리된 모듈은 여기에 append하고 명시 re-export도 갱신.
_BACKING: list = [core, _config, technicals, sector, krx]


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

from .core import (  # noqa: F401
    # DART 간격 (core에 남아 있음)
    _DART_INTERVAL,

    # 비동기 락
    db_write_lock,

    # DB 접근
    _get_db,

    # 섹터 분류 — sector.py 가 실소유자, core re-import로 이 블록에서 가져옴
    _classify_sector,

    # 스캐너 / 히스토리
    _load_history,
    _summarize_filters,
    PRESETS,

    # 날짜 / 기간 헬퍼
    _parse_period,
    _build_period,
    _prev_yoy_period,

    # 재무 계산 헬퍼
    _safe_div,
    _pick_net_income,
    _div_num,
    _compute_ttm,

    # 알파 메트릭
    _compute_fscore,
    _compute_mscore,
    _compute_fcf_metrics,
    _update_alpha_metrics,
    _ensure_alpha_columns,
    update_all_alpha_metrics,

    # DART 내부 배치
    _collect_dart_full_batch,

    # 공개 함수 (외부 직접 import)
    load_krx_db,
    scan_stocks,
    collect_daily,
    collect_financial_weekly,
    collect_dividends,
    collect_financial_on_disclosure,
    backup_to_icloud,
    backfill_day_via_chart,
    sync_us_analyst_master,
    is_tier_s_analyst,
    find_us_buy_candidates,
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
