"""
pytest conftest — /data 디렉토리 권한 문제 우회.
kis_api.py는 모듈 임포트 시 os.makedirs("/data") 를 호출하는데,
테스트 환경에서는 /data 쓰기 권한이 없으므로 /tmp/stock-bot-test 로 redirect.
"""
import os

_orig_makedirs = os.makedirs

def _patched_makedirs(path, *args, **kwargs):
    if str(path) == "/data":
        path = "/tmp/stock-bot-test"
    return _orig_makedirs(path, *args, **kwargs)

os.makedirs = _patched_makedirs


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# live 마커: 실제 네트워크/DB/프로덕션 데이터를 치는 통합 테스트는 기본 실행에서 제외.
# pytest-asyncio 도입(2026-06-04)으로 async 통합 테스트가 실제 실행되게 되면서,
# KRX/DART/KIS 실호출 테스트가 기본 `pytest`를 느리게/비결정적으로 만듦.
# `pytest --run-live` 로 명시 실행(맥미니 등 실데이터 환경). 관례: tests/conftest.py 의 live 마커.
# ━━━━━━━━━━━━━━━━━━━━━━━━━
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-live", action="store_true", default=False,
        help="run @pytest.mark.live tests (real network/DB/production data)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="live test (real network/DB) — pass --run-live to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# REGIME_STATE_FILE 격리: 루트 conftest이므로 tests/ 하위뿐 아니라 루트 test_*.py 25개도
# 함께 커버한다 (2026-09 리뷰 — 기존엔 tests/conftest.py에만 있어 루트 테스트는 무방비).
# ━━━━━━━━━━━━━━━━━━━━━━━━━
import importlib

# REGIME_STATE_FILE을 자기 네임스페이스로 복사해 가는 값 복사 소비자 7곳(grep -rn REGIME_STATE_FILE 기준)(전수 확인:
# grep -rn "REGIME_STATE_FILE" --include=*.py .). kis_api.regime이 유일한 writer이지만,
# `from kis_api import *`로 값을 복사해 가는 main_pkg 쪽 read/derive 소비자도 같은 값을
# 들고 있어야 격리가 실효성이 있다 (그중 main_pkg.jobs.regime/watch_change는 파생 경로
# — regime_transition_sent.json / watch_change_sent.json — 에 직접 save_json도 한다).
_REGIME_STATE_FILE_CONSUMERS = (
    "kis_api.regime",
    "main_pkg._ctx",
    "main_pkg.jobs.regime",
    "main_pkg.jobs.watch_change",
    # 아래 3곳은 read-only 소비(load_json)지만 load_json은 파일 부재/손상 시 default를 save_json으로
    # 써 버리므로(kis_api/_files.py) 동일하게 격리 (2026-09-04 verifier 지적)
    "main_pkg.telegram_bot",
    "main_pkg.jobs.sunday",
    "main_pkg.jobs.kr_summary",
)


@pytest.fixture(autouse=True)
def _isolate_regime_state_file(tmp_path, monkeypatch):
    """test_mcp_dispatch.py::test_tool_invokable[get_regime] 등이 실제
    data/regime_state.json을 쓰는 결함 차단 (바이섹트로 확인: get_regime → handle_get_regime
    → cmd_regime → save_json(REGIME_STATE_FILE, state)).

    kis_api/regime.py는 `from ._config import (..., REGIME_STATE_FILE, ...)`로 이름을 모듈
    네임스페이스에 바인딩하므로 save_json(REGIME_STATE_FILE, state) 호출은 kis_api.regime
    모듈의 REGIME_STATE_FILE 속성을 그대로 읽는다 — 이 속성을 직접 monkeypatch해야 확실히
    격리된다. 이 conftest의 /data→/tmp os.makedirs 리다이렉트만으로는 부족하다:
    kis_api._config가 `load_dotenv(override=True)`로 .env의 실 DATA_DIR을 다시 로드해
    env 기반 리다이렉트가 무효화될 수 있기 때문(실측: 패치 전 REGIME_STATE_FILE은 항상
    실제 data/regime_state.json 절대경로였음).

    같은 패턴으로 `main_pkg/_ctx.py`(`_read_regime()`), `main_pkg/jobs/regime.py`
    (`regime_transition_alert` — REGIME_STATE_FILE.rsplit('/', 1)[0]에서 regime_transition_sent.json
    경로도 파생), `main_pkg/jobs/watch_change.py`(`watch_change_detect` — 같은 방식으로
    watch_change_sent.json 경로 파생)도 `from kis_api import *`로 REGIME_STATE_FILE 값을
    각자의 모듈 네임스페이스에 독립 복사해 간다 — kis_api.regime 하나만 patch하면 이들은
    여전히 실제 절대경로를 들고 있으므로 함께 patch해야 한다.
    (참고: main_pkg.jobs.regime.regime_transition_alert는 test_regime.py에서 실제로
    호출된다 — 그 테스트는 자체적으로 별도 tmp 경로를 patch.object로 국소 오버라이드하므로
    이 fixture와 충돌하지 않는다.)

    import 실패(선택적 의존성 미설치 등) 시 조용히 스킵 — 무관한 모듈 하나의 import
    실패로 전체 스위트가 막히지 않게.
    """
    target = str(tmp_path / "regime_state.json")
    for _modname in _REGIME_STATE_FILE_CONSUMERS:
        try:
            _mod = importlib.import_module(_modname)
        except Exception:
            continue
        if hasattr(_mod, "REGIME_STATE_FILE"):
            monkeypatch.setattr(_mod, "REGIME_STATE_FILE", target)
