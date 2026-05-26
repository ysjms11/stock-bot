"""tests/ 패키지용 conftest — live 마커 등록 + .env 로드 + 부모 conftest 상속."""

# 부모 conftest.py(/data 권한 패치)는 pytest가 자동으로 함께 로드.
# .env의 DATA_DIR를 명시적으로 로드해야 report_crawler.DB_PATH가 실제 stock.db 경로를 가리킴.

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _ROOT / ".env"

if _ENV_FILE.is_file() and not os.environ.get("DATA_DIR"):
    try:
        for line in _ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            # .env 전체를 상속하지 말고 DATA_DIR만 필요. 다른 키는 기존 환경 유지.
            if k == "DATA_DIR" and not os.environ.get(k):
                os.environ[k] = v
    except Exception:
        pass


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: external network call required (skip in CI with -m 'not live')",
    )
