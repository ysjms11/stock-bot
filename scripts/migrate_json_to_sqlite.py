#!/usr/bin/env python3
"""JSON KRX DB → SQLite 마이그레이션.

data/krx_db/*.json 232개 파일을 읽어 stock.db에 INSERT.

실행:
    python3 scripts/migrate_json_to_sqlite.py
    python3 scripts/migrate_json_to_sqlite.py --dry-run   # 파일 목록만 출력
"""

import sqlite3
import json
import os
import glob
import sys
import ast
import argparse
from datetime import datetime

# 프로젝트 루트를 sys.path에 추가 (db_collector import용)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_collector import (
    _get_db,
    _load_std_sector_map,
    _classify_sector,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# JSON 필드 → SQLite 컬럼 매핑
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# (json_field, sqlite_col, type_fn)
# type_fn: int / float / None(=as-is, nullable)
_COL_MAP: list[tuple[str, str, object]] = [
    # ── 기본시세 ──
    ("close",                "close",                int),
    ("chg_pct",              "change_pct",           float),
    ("volume",               "volume",               int),
    ("trade_value",          "trade_value",          int),
    # market_cap: JSON은 원 단위 → 억원 변환 (_safe_mcap 함수 사용)
    ("per",                  "per",                  float),
    ("pbr",                  "pbr",                  float),
    ("eps",                  "eps",                  float),
    ("bps",                  "bps",                  float),
    ("div_yield",            "div_yield",            float),
    ("w52_high",             "w52_high",             int),
    ("w52_low",              "w52_low",              int),
    ("foreign_hold_ratio",   "foreign_own_pct",      float),
    ("list_shares",          "listing_shares",       int),
    ("turnover",             "turnover",             float),

    # ── 수급 ──
    ("foreign_net_qty",      "foreign_net_qty",      int),
    ("foreign_net_amt",      "foreign_net_amt",      int),
    ("inst_net_qty",         "inst_net_qty",         int),
    ("inst_net_amt",         "inst_net_amt",         int),
    ("indiv_net_qty",        "indiv_net_qty",        int),
    ("indiv_net_amt",        "indiv_net_amt",        int),

    # ── 공매도 ──
    ("short_balance",        "short_volume",         int),   # JSON: short_balance
    ("short_ratio",          "short_ratio",          float),

    # ── 컨센서스 (없으면 NULL) ──
    ("consensus_target",     "consensus_target",     int),
    ("consensus_count",      "consensus_count",      int),
    ("consensus_gap",        "consensus_gap",        float),

    # ── 이평선 ──
    ("ma5",                  "ma5",                  float),
    ("ma10",                 "ma10",                 float),
    ("ma20",                 "ma20",                 float),
    ("ma60",                 "ma60",                 float),
    ("ma120",                "ma120",                float),
    ("ma200",                "ma200",                float),
    ("ma_spread",            "ma_spread",            float),

    # ── RSI / 볼린저 ──
    ("rsi14",                "rsi14",                float),
    ("bb_upper",             "bb_upper",             float),
    ("bb_lower",             "bb_lower",             float),

    # ── 52주 / YTD ──
    ("w52_position",         "w52_position",         float),
    ("ytd_return",           "ytd_return",           float),

    # ── 매물대 (Volume Profile) ──
    ("vp_poc_60d",           "vp_poc_60d",           float),
    ("vp_va_high_60d",       "vp_va_high_60d",       float),
    ("vp_va_low_60d",        "vp_va_low_60d",        float),
    ("vp_position_60d",      "vp_position_60d",      float),
    ("vp_poc_250d",          "vp_poc_250d",          float),
    ("vp_va_high_250d",      "vp_va_high_250d",      float),
    ("vp_va_low_250d",       "vp_va_low_250d",       float),
    ("vp_position_250d",     "vp_position_250d",     float),

    # ── 거래량 추세 ──
    ("volume_ratio_5d",      "volume_ratio_5d",      float),
    ("volume_ratio_10d",     "volume_ratio_10d",     float),
    ("volume_ratio_20d",     "volume_ratio_20d",     float),

    # ── MA 스프레드 변화 ──
    ("ma_spread_change_10d", "ma_spread_change_10d", float),
    ("ma_spread_change_30d", "ma_spread_change_30d", float),

    # ── RSI 변화 ──
    ("rsi_change_5d",        "rsi_change_5d",        float),
    ("rsi_change_20d",       "rsi_change_20d",       float),

    # ── 어닝 ──
    ("eps_change_90d",       "eps_change_90d",       float),
    ("earnings_gap",         "earnings_gap",         float),

    # ── 수급 추세 ──
    ("foreign_trend_5d",     "foreign_trend_5d",     float),
    ("foreign_trend_20d",    "foreign_trend_20d",    float),
    ("foreign_trend_60d",    "foreign_trend_60d",    float),
    ("foreign_ratio",        "foreign_ratio",        float),
    ("inst_ratio",           "inst_ratio",           float),
    ("fi_ratio",             "fi_ratio",             float),

    # ── 공매도 변화 ──
    ("short_change_5d",      "short_change_5d",      float),
    ("short_change_20d",     "short_change_20d",     float),

    # ── 섹터 ──
    ("sector_rel_strength",  "sector_rel_strength",  float),
    ("sector_rank",          "sector_rank",          int),
]

# INSERT 컬럼 리스트 (market_cap은 별도 처리)
_INSERT_COLS = (
    ["trade_date", "symbol", "market_cap"]
    + [sqlite_col for (_, sqlite_col, _) in _COL_MAP]
    + ["collected_at"]
)

# SQL placeholders
_INSERT_SQL = (
    "INSERT OR IGNORE INTO daily_snapshot ("
    + ", ".join(_INSERT_COLS)
    + ") VALUES ("
    + ", ".join(["?"] * len(_INSERT_COLS))
    + ")"
)


def _safe_cast(val, type_fn):
    """None-safe 형변환. type_fn이 None이면 as-is."""
    if val is None:
        return None
    if type_fn is None:
        return val
    try:
        return type_fn(val)
    except (TypeError, ValueError):
        return None


def _safe_mcap(val) -> int | None:
    """시총: JSON 원 단위 → SQLite 억원."""
    if val is None:
        return None
    try:
        return int(int(val) // 100_000_000)
    except (TypeError, ValueError):
        return None


def _build_row(date: str, ticker: str, rec: dict, collected_at: str) -> tuple:
    """JSON 레코드 → INSERT 바인딩 튜플."""
    market_cap = _safe_mcap(rec.get("market_cap"))

    values = [date, ticker, market_cap]
    for json_field, _, type_fn in _COL_MAP:
        values.append(_safe_cast(rec.get(json_field), type_fn))
    values.append(collected_at)
    return tuple(values)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# stock_master 채우기
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def migrate_stock_master(conn: sqlite3.Connection, stocks: dict):
    """최신 JSON의 stocks dict → stock_master UPSERT."""
    std_map = _load_std_sector_map()

    rows = []
    for ticker, rec in stocks.items():
        name = rec.get("name", "")
        market = rec.get("market", "")
        sector_krx = rec.get("sector_krx", "")
        info = std_map.get(ticker, {})
        std_code = info.get("std_code", "")
        sector = _classify_sector(ticker, name, std_code)
        # list_shares: JSON 필드 (없으면 0)
        listing_shares = _safe_cast(rec.get("list_shares"), int) or 0
        rows.append((ticker, name, market, sector, sector_krx, std_code,
                     listing_shares))

    conn.executemany("""
        INSERT INTO stock_master (symbol, name, market, sector, sector_krx,
                                  std_code, listing_shares, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(symbol) DO UPDATE SET
            name=excluded.name,
            market=excluded.market,
            sector=excluded.sector,
            sector_krx=excluded.sector_krx,
            std_code=excluded.std_code,
            listing_shares=excluded.listing_shares,
            updated_at=excluded.updated_at
    """, rows)
    conn.commit()
    return len(rows)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 마이그레이션
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def migrate(dry_run: bool = False):
    data_dir = os.environ.get("DATA_DIR", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    ))
    db_dir = os.path.join(data_dir, "krx_db")
    files = sorted(glob.glob(os.path.join(db_dir, "*.json")))

    if not files:
        print(f"[migrate] krx_db 파일 없음: {db_dir}")
        sys.exit(1)

    print(f"[migrate] 마이그레이션 대상: {len(files)}파일  ({files[0][-13:-5]} ~ {files[-1][-13:-5]})")

    if dry_run:
        print("[migrate] --dry-run 모드: DB 기록 생략")
        for f in files:
            print(f"  {os.path.basename(f)}")
        return

    # ── DB 연결 + 스키마 생성 ──
    conn = _get_db()

    # ── 1. stock_master: 전체 파일에서 누적 구축 ──
    # 과거 파일에는 현재 상장폐지된 종목이 있을 수 있으므로
    # 모든 파일을 순회해 종목 메타를 수집한다. 최신 파일이 나중에
    # UPSERT되어 name/sector가 최신값으로 덮어씌워진다.
    print(f"\n[1/2] stock_master 구축 — 전체 {len(files)}파일에서 누적 수집")
    all_stocks_meta: dict[str, dict] = {}
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for ticker, rec in data.get("stocks", {}).items():
            # 최신 파일일수록 나중에 덮어쓰기 → 최신값 유지
            all_stocks_meta[ticker] = rec

    n_master = migrate_stock_master(conn, all_stocks_meta)
    print(f"      → {n_master}개 종목 UPSERT 완료")

    # ── 2. daily_snapshot: 전체 파일 순회 ──
    print(f"\n[2/2] daily_snapshot INSERT — {len(files)}파일")

    total_rows = 0
    total_skipped = 0
    start_ts = datetime.now()

    for i, filepath in enumerate(files, 1):
        filename = os.path.basename(filepath)
        date_str = filename.replace(".json", "")  # YYYYMMDD

        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  [WARN] {filename} 읽기 실패: {e}")
            continue

        stocks: dict = data.get("stocks", {})
        if not stocks:
            print(f"  [WARN] {filename} stocks 없음, 스킵")
            continue

        collected_at = data.get("updated_at", "")

        rows = []
        for ticker, rec in stocks.items():
            try:
                row = _build_row(date_str, ticker, rec, collected_at)
                rows.append(row)
            except Exception as e:
                total_skipped += 1
                print(f"  [WARN] {filename}/{ticker} 행 구성 실패: {e}")

        if rows:
            try:
                conn.executemany(_INSERT_SQL, rows)
                conn.commit()
                total_rows += len(rows)
            except Exception as e:
                conn.rollback()
                print(f"  [ERROR] {filename} INSERT 실패: {e}")

        if i % 50 == 0 or i == len(files):
            elapsed = (datetime.now() - start_ts).total_seconds()
            print(f"  [{i:3d}/{len(files)}] {filename}  누적 {total_rows:,}행  "
                  f"({elapsed:.0f}s 경과)")

    # ── 완료 ──
    elapsed_total = (datetime.now() - start_ts).total_seconds()
    print(f"\n[migrate] 완료!")
    print(f"  파일: {len(files)}개")
    print(f"  INSERT: {total_rows:,}행")
    print(f"  스킵:   {total_skipped}행")
    print(f"  소요:   {elapsed_total:.1f}s")

    # ── 검증 쿼리 ──
    row = conn.execute("SELECT COUNT(*) FROM daily_snapshot").fetchone()
    print(f"  DB 최종 daily_snapshot: {row[0]:,}행")
    row = conn.execute("SELECT COUNT(*) FROM stock_master").fetchone()
    print(f"  DB 최종 stock_master:   {row[0]:,}개 종목")

    conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# AST 검증 (import 시 자동 실행)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _self_ast_check():
    """자기 파일을 AST 파싱해서 구문 오류 없는지 확인."""
    src = open(__file__, encoding="utf-8").read()
    ast.parse(src)  # SyntaxError 없으면 통과


_self_ast_check()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JSON KRX DB → SQLite 마이그레이션")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB에 쓰지 않고 파일 목록만 출력")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)
