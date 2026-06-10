"""dashboard_home/whale.py — Whale 탭 데이터 빌더 (P3 박리).

_whale_home, _whale_kr_5pct, _whale_kr_full, _whale_us_13f,
_whale_pension, _whale_insider, build_whale_payload.
"""

import asyncio
from datetime import datetime, timedelta

from kis_api import (
    _DATA_DIR,
    fetch_nps_kr_full_holdings,
    fetch_nps_us_holdings,
    KST,
)

from ._helpers import _open_db

# 레거시 alias (코드 내 _open_whale_db() 호출)
_open_whale_db = _open_db

def _whale_home() -> dict:
    """home 프리셋 — 각 소스별 최신 날짜 + 건수 요약."""
    result: dict = {}
    try:
        conn = _open_whale_db()
        # kr_full
        r = conn.execute(
            "SELECT snapshot_date, COUNT(*) AS cnt FROM nps_kr_full_holdings"
            " GROUP BY snapshot_date ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        result["kr_full"] = {"snapshot_date": r["snapshot_date"], "count": r["cnt"]} if r else {}

        # us_13f
        r = conn.execute(
            "SELECT quarter, period_end, COUNT(*) AS cnt FROM nps_us_holdings"
            " GROUP BY quarter ORDER BY period_end DESC LIMIT 1"
        ).fetchone()
        result["us_13f"] = {"quarter": r["quarter"], "period_end": r["period_end"], "count": r["cnt"]} if r else {}

        # kr_5pct
        r = conn.execute(
            "SELECT quarter, COUNT(*) AS cnt FROM nps_holdings_disclosed"
            " WHERE quarter != '' GROUP BY quarter ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
        result["kr_5pct"] = {"quarter": r["quarter"], "count": r["cnt"]} if r else {}

        # pension
        r = conn.execute(
            "SELECT trade_date, COUNT(DISTINCT symbol) AS cnt"
            " FROM pension_flow_daily GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        result["pension"] = {"latest_date": r["trade_date"], "symbols": r["cnt"]} if r else {}

        # insider
        r = conn.execute(
            "SELECT COUNT(*) AS cnt, MAX(rcept_dt) AS latest FROM insider_transactions"
            " WHERE stock_irds_cnt != 0"
        ).fetchone()
        result["insider"] = {"latest_date": r["latest"] or "", "count": r["cnt"]} if r else {}

        conn.close()
    except Exception as exc:
        result["_error"] = str(exc)
    return result


def _whale_kr_5pct() -> list:
    """NPS 5%룰 최신 분기 전체 — 실제 컬럼만 사용."""
    try:
        conn = _open_whale_db()
        latest_q_row = conn.execute(
            "SELECT quarter FROM nps_holdings_disclosed WHERE quarter != ''"
            " ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
        if not latest_q_row:
            conn.close()
            return []
        latest_q = latest_q_row["quarter"]

        prev_q_row = conn.execute(
            "SELECT DISTINCT quarter FROM nps_holdings_disclosed"
            " WHERE quarter != '' AND quarter < ? ORDER BY quarter DESC LIMIT 1",
            (latest_q,),
        ).fetchone()
        prev_q = prev_q_row["quarter"] if prev_q_row else None

        prev_map: dict = {}
        if prev_q:
            for pr in conn.execute(
                "SELECT symbol, MAX(ratio_pct) AS max_r FROM nps_holdings_disclosed"
                " WHERE quarter = ? AND symbol != '' GROUP BY symbol",
                (prev_q,),
            ).fetchall():
                prev_map[pr["symbol"]] = float(pr["max_r"] or 0)

        rows = conn.execute(
            "SELECT report_date, company_name, symbol, ratio_pct"
            " FROM nps_holdings_disclosed WHERE quarter = ?"
            " ORDER BY ratio_pct DESC, report_date DESC",
            (latest_q,),
        ).fetchall()
        conn.close()

        out = []
        for r in rows:
            cur_r = float(r["ratio_pct"] or 0)
            sym = r["symbol"] or ""
            prev_r = prev_map.get(sym) if sym and prev_q else None
            if prev_q and sym:
                if prev_r is None:
                    change_label = "NEW"
                    change_val = None
                else:
                    change_val = round(cur_r - prev_r, 4)
                    change_label = "UP" if change_val > 0.05 else ("DOWN" if change_val < -0.05 else "FLAT")
            else:
                change_label = ""
                change_val = None
            out.append({
                "report_date": r["report_date"],
                "company_name": r["company_name"],
                "symbol": sym,
                "ratio_pct": cur_r,
                "prev_ratio": prev_map.get(sym),
                "change": change_val,
                "change_label": change_label,
                "is_new": change_label == "NEW",
                "quarter": latest_q,
                "prev_quarter": prev_q,
            })
        return out
    except Exception as exc:
        return [{"error": str(exc)}]


def _whale_kr_full() -> dict:
    """NPS KR 풀포트 — fetch_nps_kr_full_holdings 래핑."""
    try:
        from kis_api import fetch_nps_kr_full_holdings
        return fetch_nps_kr_full_holdings(top=200)
    except Exception as exc:
        return {"error": str(exc), "rows": []}


def _whale_us_13f() -> dict:
    """NPS US 13F — fetch_nps_us_holdings 래핑."""
    try:
        from kis_api import fetch_nps_us_holdings
        return fetch_nps_us_holdings(top=100, include_changes=True)
    except Exception as exc:
        return {"error": str(exc), "rows": []}


def _whale_pension() -> list:
    """연기금 5일 누적 순매매 — 직접 SQL (시총% 포함)."""
    try:
        conn = _open_whale_db()
        dates = [r["trade_date"] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM pension_flow_daily"
            " ORDER BY trade_date DESC LIMIT 5"
        ).fetchall()]
        if not dates:
            conn.close()
            return []
        ph = ",".join("?" for _ in dates)
        agg_rows = conn.execute(
            f"SELECT pf.symbol, pf.name, pf.market,"
            f" SUM(pf.net_amount_won) AS net_total"
            f" FROM pension_flow_daily pf"
            f" WHERE pf.trade_date IN ({ph})"
            f" GROUP BY pf.symbol HAVING net_total != 0",
            dates,
        ).fetchall()
        symbols = [r["symbol"] for r in agg_rows]
        cap_map: dict = {}
        if symbols:
            sph = ",".join("?" for _ in symbols)
            cap_rows = conn.execute(
                f"SELECT symbol, MAX(trade_date) AS d FROM daily_snapshot"
                f" WHERE symbol IN ({sph}) GROUP BY symbol",
                symbols,
            ).fetchall()
            for cr in cap_rows:
                cap = conn.execute(
                    "SELECT market_cap FROM daily_snapshot WHERE symbol=? AND trade_date=?",
                    (cr["symbol"], cr["d"]),
                ).fetchone()
                if cap and cap["market_cap"]:
                    cap_map[cr["symbol"]] = int(cap["market_cap"]) * 100_000_000
        conn.close()

        period = ""
        if dates:
            d0, d1 = dates[-1], dates[0]
            period = (f"{d0[:4]}-{d0[4:6]}-{d0[6:]} ~ {d1[:4]}-{d1[4:6]}-{d1[6:]}")

        out = []
        for r in agg_rows:
            cap = cap_map.get(r["symbol"], 0)
            pct = round(r["net_total"] * 100.0 / cap, 4) if cap > 0 else None
            out.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "market": r["market"],
                "net_won": r["net_total"],
                "net_eok": round(r["net_total"] / 100_000_000, 2),
                "cap_won": cap,
                "cap_pct": pct,
            })
        # 매수/매도 분리 정렬 후 재합산
        buy = sorted([e for e in out if e["net_won"] > 0],
                     key=lambda x: (-(x["cap_pct"] or 0) if x["cap_won"] else 0, -x["net_won"]))[:50]
        sell = sorted([e for e in out if e["net_won"] < 0],
                      key=lambda x: ((x["cap_pct"] or 0) if x["cap_won"] else 0, x["net_won"]))[:50]
        return {"period": period, "buy_top": buy, "sell_top": sell}
    except Exception as exc:
        return {"error": str(exc)}


def _whale_insider() -> list:
    """임원·5%↑ 주주 최근 90일 매매 — stock_master JOIN."""
    try:
        conn = _open_whale_db()
        cutoff = (datetime.now(KST) - timedelta(days=90)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT it.rcept_dt, it.symbol, sm.name AS company_name,"
            " it.repror, it.ofcps, it.main_shrholdr,"
            " it.stock_irds_cnt, it.stock_rate, it.stock_irds_rate"
            " FROM insider_transactions it"
            " LEFT JOIN stock_master sm ON sm.symbol = it.symbol"
            " WHERE it.rcept_dt >= ? AND it.stock_irds_cnt != 0 AND it.stock_rate >= 5"
            " ORDER BY it.rcept_dt DESC, ABS(it.stock_irds_rate) DESC",
            (cutoff,),
        ).fetchall()
        conn.close()
        out = []
        for r in rows:
            irds = r["stock_irds_cnt"] or 0
            role = (r["main_shrholdr"] or "") or (r["ofcps"] or "")
            out.append({
                "rcept_dt": r["rcept_dt"],
                "symbol": r["symbol"],
                "company_name": r["company_name"] or "",
                "repror": r["repror"] or "",
                "role": role,
                "irds_cnt": irds,
                "direction": "buy" if irds > 0 else "sell",
                "stock_rate": float(r["stock_rate"] or 0),
                "stock_irds_rate": float(r["stock_irds_rate"] or 0),
            })
        return out
    except Exception as exc:
        return [{"error": str(exc)}]


async def build_whale_payload(preset: str) -> dict | list:
    """preset ∈ home|kr_5pct|kr_full|us_13f|pension|insider — 구조화 데이터 반환."""
    loop = asyncio.get_running_loop()
    if preset == "home":
        return await loop.run_in_executor(None, _whale_home)
    elif preset == "kr_5pct":
        return await loop.run_in_executor(None, _whale_kr_5pct)
    elif preset == "kr_full":
        return await loop.run_in_executor(None, _whale_kr_full)
    elif preset == "us_13f":
        return await loop.run_in_executor(None, _whale_us_13f)
    elif preset == "pension":
        return await loop.run_in_executor(None, _whale_pension)
    elif preset == "insider":
        return await loop.run_in_executor(None, _whale_insider)
    else:
        return {"error": f"unknown preset: {preset}"}


