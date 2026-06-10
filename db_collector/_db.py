"""SQLite 연결 / 스키마 초기화 / 쓰기 직렬화 락.

P3-1 박리: db_write_lock, _get_db, _init_schema
외부 임포트 표면 (db_collector.db_write_lock, db_collector._get_db) 유지됨.
"""

import asyncio
import os
import sqlite3

from ._config import DB_PATH


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 무거운 DB 쓰기 트랜잭션 직렬화 (SQLite WAL = 동시 writer 1개).
# 여러 async 잡이 interleave하며 쓰기 → busy_timeout race → 'database is locked'.
# 이 락으로 쓰기를 큐잉(대기)시켜 경합 제거. ⚠️ 락은 connect→write→commit 구간만 잡고,
# 네트워크 fetch/sleep 등 await-heavy 작업은 락 밖에서 한다 (안 그러면 불필요하게 직렬화).
db_write_lock = asyncio.Lock()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# SQLite 연결 / 스키마 초기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _get_db() -> sqlite3.Connection:
    """SQLite 연결. WAL 모드, FK 활성화. 스키마 자동 생성."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA cache_size = -65536")   # 64MB (negative = KB)
    conn.execute("PRAGMA temp_store = MEMORY")   # temp 테이블 메모리 처리
    conn.execute("PRAGMA mmap_size = 268435456") # 256MB memory-mapped I/O
    conn.row_factory = sqlite3.Row  # dict-like 접근
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    """data/db_schema.sql 실행."""
    schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "db_schema.sql")
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    # 기존 DB 마이그레이션: 누락 컬럼 추가 (SQLite ADD COLUMN IF NOT EXISTS 미지원 → try/except)
    for alter_sql in (
        "ALTER TABLE daily_snapshot ADD COLUMN loan_balance_rate REAL DEFAULT 0",
        # v1.4: F/M/FCF Phase1 — financial_quarterly 확장 (DART fnlttSinglAcntAll)
        "ALTER TABLE financial_quarterly ADD COLUMN cfo INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN capex INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN fcf INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN depreciation INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN sga INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN receivables INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN inventory INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN shares_out INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN net_income_parent INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN equity_parent INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN fs_source TEXT",
        # v1.5: F/M/FCF Phase3 — daily_snapshot 알파 메트릭
        "ALTER TABLE daily_snapshot ADD COLUMN fscore INTEGER",
        "ALTER TABLE daily_snapshot ADD COLUMN mscore REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_to_assets REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_yield_ev REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_conversion REAL",
        # v1.6: 톱 애널 평가용 — 콜 후 평균 수익률 (Tier S 경로 ③ 핵심, 2026-04-25)
        "ALTER TABLE us_analysts ADD COLUMN avg_return REAL",
    ):
        try:
            conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass  # 이미 존재
