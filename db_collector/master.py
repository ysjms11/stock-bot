"""종목 마스터 UPSERT 헬퍼.

P3-2 박리: _sync_stock_master, _update_master_from_basic
"""

import sqlite3

from .sector import _classify_sector, _load_std_sector_map


def _sync_stock_master(conn: sqlite3.Connection, market_data: list[dict]):
    """시세 데이터에서 종목 마스터 UPSERT."""
    std_map = _load_std_sector_map()
    for item in market_data:
        ticker = item["ticker"]
        name = item.get("name", "")
        market = item.get("market", "")
        info = std_map.get(ticker, {})
        sector = _classify_sector(ticker, name, info.get("std_code", ""))
        conn.execute("""
            INSERT INTO stock_master (symbol, name, market, sector, std_code, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name, market=excluded.market,
                sector=excluded.sector, updated_at=excluded.updated_at
        """, (ticker, name, market, sector, info.get("std_code", "")))
    conn.commit()


def _update_master_from_basic(conn: sqlite3.Connection, phase1_results: dict):
    """Phase 1 기본시세 응답에서 sector_krx + 신규 종목 섹터 갱신."""
    std_map = _load_std_sector_map()
    for ticker, data in phase1_results.items():
        sector_krx = data.get("bstp_kor_isnm", "")
        if not sector_krx:
            continue
        # sector_krx 저장
        conn.execute("""
            UPDATE stock_master SET sector_krx = ? WHERE symbol = ?
        """, (sector_krx, ticker))
        # 정밀 섹터가 비어있으면 KRX 업종으로 fallback
        row = conn.execute("SELECT sector FROM stock_master WHERE symbol = ?", (ticker,)).fetchone()
        if row and not row["sector"]:
            conn.execute("UPDATE stock_master SET sector = ? WHERE symbol = ?", (sector_krx, ticker))
    conn.commit()
