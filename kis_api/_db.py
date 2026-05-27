"""SQLite stock.db 공용 connect 헬퍼 — PRAGMA 일관 적용."""
import sqlite3
import os

_DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = f"{_DATA_DIR}/stock.db"


def get_db_conn(path: str = DB_PATH, timeout: int = 30) -> sqlite3.Connection:
    """공용 SQLite 연결 — PRAGMA 자동 적용.

    - cache_size = -65536  (64MB, 기본 2MB)
    - temp_store = MEMORY  (임시 테이블 RAM 처리)
    - mmap_size = 268435456 (256MB memory-mapped I/O)
    - busy_timeout = 30000ms (WAL 경합 대기)
    """
    con = sqlite3.connect(path, timeout=timeout)
    con.execute("PRAGMA cache_size = -65536;")
    con.execute("PRAGMA temp_store = MEMORY;")
    con.execute("PRAGMA mmap_size = 268435456;")
    con.execute("PRAGMA busy_timeout = 30000;")
    return con
