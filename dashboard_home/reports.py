"""dashboard_home/reports.py — 리포트 탭 빌더 (P3 박리).

_sync_reports_payload(동기 SQLite), build_reports_payload(async 래퍼),
_sync_reports_by_ticker, _reports_by_ticker.
"""

import asyncio
import os
import sqlite3 as _sqlite3

from kis_api import _DATA_DIR

from ._helpers import _open_db

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P3b: 리포트 탭 — build_reports_payload
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _sync_reports_payload() -> dict:
    """reports 테이블에서 4세그먼트 집계 (동기 본체).

    kr: category='company' AND ticker GLOB '[0-9]*'  — 종목 카드 그리드
    us: category='company' AND ticker GLOB '[A-Za-z]*' — 종목 카드 그리드
    industry: category='industry' — 날짜 내림차순 LIMIT 200
    macro: category IN ('market','strategy','economy','bond') — 날짜 내림차순 LIMIT 200

    stock_master 조인으로 종목명 보강 (symbol 컬럼).
    """
    result: dict = {
        "kr": [], "us": [], "industry": [], "macro": [],
        "kr_total": 0, "us_total": 0,
        "industry_total": 0, "macro_total": 0,
    }
    try:
        conn = _open_db()

        # KR 종목 — 티커별 집계 + stock_master 이름 보강
        rows = conn.execute(
            "SELECT r.ticker,"
            " COALESCE(NULLIF(sm.name,''), NULLIF(r.name,''), r.ticker) AS rname,"
            " COUNT(*) AS cnt, MAX(r.date) AS latest"
            " FROM reports r"
            " LEFT JOIN stock_master sm ON sm.symbol = r.ticker"
            " WHERE r.category = 'company' AND r.ticker GLOB '[0-9]*'"
            " GROUP BY r.ticker ORDER BY cnt DESC"
        ).fetchall()
        result["kr_total"] = len(rows)
        result["kr"] = [
            {"ticker": r["ticker"], "name": r["rname"], "cnt": r["cnt"], "latest": r["latest"]}
            for r in rows
        ]

        # US 종목
        rows = conn.execute(
            "SELECT r.ticker,"
            " COALESCE(NULLIF(sm.name,''), NULLIF(r.name,''), r.ticker) AS rname,"
            " COUNT(*) AS cnt, MAX(r.date) AS latest"
            " FROM reports r"
            " LEFT JOIN stock_master sm ON sm.symbol = r.ticker"
            " WHERE r.category = 'company' AND r.ticker GLOB '[A-Za-z]*'"
            " GROUP BY r.ticker ORDER BY cnt DESC"
        ).fetchall()
        result["us_total"] = len(rows)
        result["us"] = [
            {"ticker": r["ticker"], "name": r["rname"], "cnt": r["cnt"], "latest": r["latest"]}
            for r in rows
        ]

        # 산업 리포트
        rows = conn.execute(
            "SELECT date, name AS sector, title, source, ticker, pdf_path"
            " FROM reports WHERE category = 'industry'"
            " ORDER BY date DESC LIMIT 200"
        ).fetchall()
        cnt_q = conn.execute(
            "SELECT COUNT(*) AS n FROM reports WHERE category = 'industry'"
        ).fetchone()
        result["industry_total"] = cnt_q["n"] if cnt_q else 0
        result["industry"] = [
            {
                "date": r["date"], "sector": r["sector"],
                "title": r["title"], "source": r["source"],
                "ticker": r["ticker"],
                "pdf_basename": os.path.basename(r["pdf_path"]) if r["pdf_path"] else "",
            }
            for r in rows
        ]

        # 시황·전략·경제·채권
        rows = conn.execute(
            "SELECT date, category, name AS label, title, source, ticker, pdf_path"
            " FROM reports"
            " WHERE category IN ('market','strategy','economy','bond')"
            " ORDER BY date DESC LIMIT 200"
        ).fetchall()
        cnt_q = conn.execute(
            "SELECT COUNT(*) AS n FROM reports"
            " WHERE category IN ('market','strategy','economy','bond')"
        ).fetchone()
        result["macro_total"] = cnt_q["n"] if cnt_q else 0
        result["macro"] = [
            {
                "date": r["date"], "category": r["category"],
                "label": r["label"],
                "title": r["title"], "source": r["source"],
                "ticker": r["ticker"],
                "pdf_basename": os.path.basename(r["pdf_path"]) if r["pdf_path"] else "",
            }
            for r in rows
        ]

        conn.close()
    except Exception as exc:
        result["_error"] = str(exc)
    return result


async def build_reports_payload() -> dict:
    """_sync_reports_payload를 executor에서 실행 (whale과 동일 패턴)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_reports_payload)


def _sync_reports_by_ticker(ticker: str) -> list:
    """종목별 리포트 목록 — 날짜 내림차순."""
    try:
        conn = _open_db()
        rows = conn.execute(
            "SELECT date, source, analyst, title, target_price, opinion, pdf_path"
            " FROM reports WHERE ticker = ? ORDER BY date DESC",
            (ticker,),
        ).fetchall()
        conn.close()
        return [
            {
                "date": r["date"], "source": r["source"],
                "analyst": r["analyst"], "title": r["title"],
                "target_price": r["target_price"], "opinion": r["opinion"],
                "pdf_basename": os.path.basename(r["pdf_path"]) if r["pdf_path"] else "",
            }
            for r in rows
        ]
    except Exception as exc:
        return [{"error": str(exc)}]


async def _reports_by_ticker(ticker: str) -> list:
    """_sync_reports_by_ticker를 executor에서 실행 (whale과 동일 패턴)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_reports_by_ticker, ticker)
