"""SQLite stock.db 쿼리 벤치마크 — 회귀 측정 자동화.

사용법:
    python tests/bench_db_query.py [--cold|--warm]

cold: macOS sync && purge로 OS cache flush 후 측정 (sudo 필요)
warm: 기본, 운영 환경 (OS cache hot) 시뮬레이션

측정 메트릭:
- 단종목 다일자 조회 (10 symbols × 250 days)
- 다종목 단일자 조회 (2864 symbols × 1 day)
- 인덱스 사용 여부 (EXPLAIN QUERY PLAN)
"""

import argparse
import os
import sqlite3
import sys
import time

DB_PATH = "/Users/kreuzer/stock-bot/data/stock.db"

SYMBOLS_TEST = ['005930', '000660', '035720', 'AMZN', 'NVDA',
                '005380', '042700', '012450', '079550', '047810']


def benchmark_query_1_single_symbol(con: sqlite3.Connection, iterations: int = 3) -> float:
    """단종목 다일자 조회 (10 symbols × 250 days)."""
    # warmup
    for sym in SYMBOLS_TEST:
        con.execute(
            "SELECT * FROM daily_snapshot WHERE symbol = ? ORDER BY trade_date DESC LIMIT 250",
            (sym,)
        ).fetchall()

    start = time.time()
    for _ in range(iterations):
        for sym in SYMBOLS_TEST:
            con.execute(
                "SELECT * FROM daily_snapshot WHERE symbol = ? ORDER BY trade_date DESC LIMIT 250",
                (sym,)
            ).fetchall()
    elapsed = (time.time() - start) * 1000 / iterations
    return elapsed


def benchmark_query_2_multi_symbol_single_date(con: sqlite3.Connection, iterations: int = 3) -> float:
    """전종목 단일일 조회 (2864 × 1 day)."""
    # find the latest trade_date first
    row = con.execute(
        "SELECT trade_date FROM daily_snapshot ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    latest_date = row[0] if row else "20260527"

    # warmup
    con.execute(
        "SELECT trade_date, symbol, close FROM daily_snapshot WHERE trade_date = ?",
        (latest_date,)
    ).fetchall()

    start = time.time()
    for _ in range(iterations):
        con.execute(
            "SELECT trade_date, symbol, close FROM daily_snapshot WHERE trade_date = ?",
            (latest_date,)
        ).fetchall()
    elapsed = (time.time() - start) * 1000 / iterations
    return elapsed


def explain_query_plan(con: sqlite3.Connection, query: str, params: tuple = ()) -> str:
    """EXPLAIN QUERY PLAN 결과."""
    rows = con.execute(f"EXPLAIN QUERY PLAN {query}", params).fetchall()
    return "\n".join("  " + str(row) for row in rows)


def run_bench(cold: bool = False):
    if not os.path.exists(DB_PATH):
        print(f"ERROR: DB not found at {DB_PATH}")
        sys.exit(1)

    if cold:
        print("Cold cache mode — sudo로 OS cache flush 시도 (실패 가능)")
        os.system("sudo sync && sudo purge 2>/dev/null || echo '  cache flush 권한 부족 — warm 측정으로 진행'")

    print(f"\n=== Benchmark mode: {'cold' if cold else 'warm'} cache ===\n")

    # Default cache connection
    con1 = sqlite3.connect(DB_PATH)

    # Optimized cache connection
    con2 = sqlite3.connect(DB_PATH)
    con2.execute("PRAGMA cache_size = -65536;")   # 64MB
    con2.execute("PRAGMA temp_store = MEMORY;")
    con2.execute("PRAGMA mmap_size = 268435456;")  # 256MB

    # EXPLAIN QUERY PLAN
    print("Query 1 (single symbol, multi-date) EXPLAIN QUERY PLAN:")
    print(explain_query_plan(
        con1,
        "SELECT * FROM daily_snapshot WHERE symbol = ? ORDER BY trade_date DESC LIMIT 250",
        ('005930',)
    ))
    print()

    # Benchmark Q1
    print("Query 1: 10 symbols × 250 days")
    e1 = benchmark_query_1_single_symbol(con1)
    e2 = benchmark_query_1_single_symbol(con2)
    print(f"  Default cache: {e1:.1f}ms/iter")
    print(f"  64MB cache:    {e2:.1f}ms/iter")
    speedup1 = e1 / e2 if e2 > 0 else float('inf')
    print(f"  Speedup:       {speedup1:.2f}x")
    print()

    # Benchmark Q2
    print("Query 2: all symbols × 1 day (latest trade_date)")
    e3 = benchmark_query_2_multi_symbol_single_date(con1)
    e4 = benchmark_query_2_multi_symbol_single_date(con2)
    print(f"  Default cache: {e3:.1f}ms/iter")
    print(f"  64MB cache:    {e4:.1f}ms/iter")
    speedup2 = e3 / e4 if e4 > 0 else float('inf')
    print(f"  Speedup:       {speedup2:.2f}x")
    print()

    total_default = e1 + e3
    total_opt = e2 + e4
    total_speedup = total_default / total_opt if total_opt > 0 else float('inf')
    print(f"=== Total speedup: {total_speedup:.2f}x ===")

    con1.close()
    con2.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SQLite stock.db 쿼리 벤치마크")
    parser.add_argument("--cold", action="store_true", help="Cold OS cache mode (sudo 필요)")
    args = parser.parse_args()
    run_bench(cold=args.cold)
