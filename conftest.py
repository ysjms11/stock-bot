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
