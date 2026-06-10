# 패키지 셸 (2026-06 분해 P1) — core.py는 구 단일파일 verbatim,
# 이후 phase에서 도메인 모듈로 분리 예정.
# 외부 인터페이스(import 표면)는 이 __init__이 동결.
#
# monkeypatch 투명성: tests가 `import db_collector as _dbc` 후
# `monkeypatch.setattr(_dbc, "DB_PATH", ...)` 등으로 core.py의 globals를
# 패치하던 동작을 그대로 보존하기 위해, setattr을 core 모듈로 전달한다.

import sys
import types

from . import core  # noqa: F401 — submodule must be importable
from .core import *  # noqa: F401, F403


# 외부 코드·테스트가 직접 참조하는 private/dunder 심볼 명시 재수출.
# (grep 기반: from db_collector import X / db_collector.X / @patch("db_collector.X"))
from .core import (  # noqa: F401
    # 상수 / 설정
    DB_PATH,
    KRX_DB_DIR,
    _KR_MARKET_HOLIDAYS,
    _DART_INTERVAL,

    # 비동기 락
    db_write_lock,

    # DB 접근
    _get_db,

    # KRX 파싱 헬퍼
    _pi,
    _pf,
    _parse_market_records,

    # 기술지표 헬퍼
    _ma,
    _rsi,
    _macd,
    _atr,
    _volatility_20d,
    _calc_vp,
    _volume_ratio,
    _spread_at,
    _rsi_at,

    # 섹터 분류
    _classify_sector,

    # 스캐너 / 히스토리
    _load_history,
    _summarize_filters,
    PRESETS,

    # 날짜 / 기간 헬퍼
    _is_kr_trading_day,
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
# monkeypatch 투명성 — setattr을 core 모듈로 전달하는 모듈 서브클래스
# tests가 `monkeypatch.setattr(_dbc, "DB_PATH", ...)` 식으로 패치할 때
# db_collector.core 의 globals도 동시에 갱신하여 core.py 내부 참조가
# 새 값을 읽도록 보장한다 (Python 3.7+ ModuleType 서브클래스 패턴).
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class _PackageModule(types.ModuleType):
    """db_collector 패키지 모듈 — setattr을 core로 전파."""

    def __setattr__(self, name: str, value):
        super().__setattr__(name, value)
        # core 모듈에도 동일하게 적용 (테스트 monkeypatch 투명성)
        if hasattr(core, name):
            setattr(core, name, value)

    def __delattr__(self, name: str):
        super().__delattr__(name)
        if hasattr(core, name):
            delattr(core, name)


# 현재 패키지 모듈 객체를 서브클래스 인스턴스로 교체
_current = sys.modules[__name__]
_proxy = _PackageModule(__name__)
_proxy.__dict__.update(_current.__dict__)
sys.modules[__name__] = _proxy
