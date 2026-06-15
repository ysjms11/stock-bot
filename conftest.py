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
