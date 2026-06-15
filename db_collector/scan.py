"""스캐너 / 히스토리 로더.

P3-3 박리: PRESETS, load_krx_db, _load_history, _get_foreign_streak_data_db,
           _summarize_filters, scan_stocks
"""

import numpy as np

from ._db import _get_db
from .technicals import _load_history_from_db


PRESETS = {
    "relative_strength": {
        "description": "시장평균 대비 등락률 +3% 이상 AND fi_ratio>0 (하락장에서 버틴 종목)",
        "sort": "fi_ratio",
    },
    "small_cap_buy": {
        "description": "시총 500~5000억 AND foreign_ratio>0.1% (소형주 외인매수)",
        "filters": {"market_cap_min": 500, "market_cap_max": 5000, "foreign_ratio_min": 0.1},
        "sort": "foreign_ratio",
    },
    "value": {
        "description": "PER>0 AND PER<10 AND PBR>0 AND PBR<1 AND 시총>1000억 (저평가)",
        "filters": {"per_min": 0.01, "per_max": 10, "pbr_min": 0.01, "pbr_max": 1,
                    "market_cap_min": 1000},
        "sort": "pbr",
    },
    "momentum": {
        "description": "chg_pct>3% AND turnover>1% (모멘텀)",
        "filters": {"chg_pct_min": 3, "turnover_min": 1},
        "sort": "chg_pct",
    },
    "oversold": {
        "description": "등락률 -7% 이하 (낙폭과대)",
        "filters": {"chg_pct_max": -7},
        "sort": "chg_pct",
    },
    "foreign_streak": {
        "description": "최근 5일 연속 외인 순매수, 시총 500억 이상 (multi-day)",
        "filters": {"market_cap_min": 500},
        "sort": "cum_foreign_ratio",
    },
}


def load_krx_db(date: str = None) -> dict | None:
    """기존 JSON 포맷과 호환되는 dict 반환. mcp_tools.py 하위호환.
    Returns: {date, stocks: {ticker: {...}}, count, market_summary}
    """
    conn = _get_db()
    try:
        if date is None:
            row = conn.execute(
                "SELECT MAX(trade_date) as d FROM daily_snapshot"
            ).fetchone()
            date = row["d"] if row and row["d"] else None
        if not date:
            return None

        rows = conn.execute("""
            SELECT d.*, m.name, m.market, m.sector as sector_name, m.sector_krx
            FROM daily_snapshot d
            LEFT JOIN stock_master m ON d.symbol = m.symbol
            WHERE d.trade_date = ?
        """, (date,)).fetchall()

        if not rows:
            return None

        stocks = {}
        for r in rows:
            d = dict(r)
            ticker = d.pop("symbol", None)
            if not ticker:
                continue
            d["ticker"] = ticker
            # 컬럼명 호환 매핑
            d["chg_pct"] = d.get("change_pct", 0) or 0
            # market_cap: SQLite는 억원 단위 → 원으로 변환 (기존 JSON 호환)
            mcap = d.get("market_cap", 0) or 0
            d["market_cap"] = mcap * 100_000_000
            # foreign_ratio / inst_ratio / fi_ratio
            if d.get("foreign_ratio") is None:
                d["foreign_ratio"] = d.get("foreign_own_pct", 0) or 0
            if d.get("inst_ratio") is None:
                d["inst_ratio"] = 0
            if d.get("fi_ratio") is None:
                fi_r = None
                fr_v = d.get("foreign_net_amt", 0) or 0
                ir_v = d.get("inst_net_amt", 0) or 0
                tv = d.get("trade_value", 0) or 0
                if tv > 0:
                    fi_r = round((fr_v + ir_v) / tv * 100, 4)
                d["fi_ratio"] = fi_r
            # 하위호환 vp 키 (250d 기준)
            d["vp_poc"] = d.get("vp_poc_250d")
            d["vp_va_high"] = d.get("vp_va_high_250d")
            d["vp_va_low"] = d.get("vp_va_low_250d")
            d["vp_position"] = d.get("vp_position_250d")
            # turnover 호환
            if d.get("turnover") is None:
                d["turnover"] = d.get("vol_tnrt", 0) or 0
            stocks[ticker] = d

        # market_summary 계산
        chg_list_kospi = [s.get("chg_pct", 0) or 0
                          for s in stocks.values() if s.get("market") == "kospi"]
        chg_list_kosdaq = [s.get("chg_pct", 0) or 0
                           for s in stocks.values() if s.get("market") == "kosdaq"]
        market_summary = {
            "kospi_avg_chg": round(float(np.mean(chg_list_kospi)), 4) if chg_list_kospi else 0,
            "kosdaq_avg_chg": round(float(np.mean(chg_list_kosdaq)), 4) if chg_list_kosdaq else 0,
        }

        return {
            "date": date,
            "stocks": stocks,
            "count": len(stocks),
            "market_summary": market_summary,
        }
    finally:
        conn.close()


def _load_history(target_date: str = None, n_days: int = 250):
    """mcp_tools.py 호환. 과거 N일 데이터 SQLite에서 로드.
    Returns: ({ticker: {close: [], volume: [], ...}}, [날짜리스트])
    """
    conn = _get_db()
    try:
        if target_date is None:
            row = conn.execute(
                "SELECT MAX(trade_date) as d FROM daily_snapshot"
            ).fetchone()
            target_date = row["d"] if row and row["d"] else None
        if not target_date:
            return {}, []
        return _load_history_from_db(conn, target_date, n_days)
    finally:
        conn.close()


def _get_foreign_streak_data_db(target_date: str, days: int = 5):
    """SQLite에서 최근 N일 연속 외인 순매수 종목 + 누적 foreign_own_pct.
    Returns: ({ticker: cum_foreign_ratio}, days_available)
    """
    conn = _get_db()
    try:
        date_rows = conn.execute("""
            SELECT DISTINCT trade_date FROM daily_snapshot
            WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?
        """, (target_date, days)).fetchall()
        if not date_rows:
            return {}, 0
        dates_avail = [r[0] for r in date_rows]
        days_available = len(dates_avail)

        cum_ratio = {}
        candidates = None
        for d in dates_avail:
            rows = conn.execute("""
                SELECT symbol, foreign_net_amt, foreign_own_pct
                FROM daily_snapshot WHERE trade_date = ?
            """, (d,)).fetchall()
            daily_positive = set()
            for r in rows:
                if (r["foreign_net_amt"] or 0) > 0:
                    daily_positive.add(r["symbol"])
                    cum_ratio[r["symbol"]] = (
                        cum_ratio.get(r["symbol"], 0) + (r["foreign_own_pct"] or 0)
                    )
            if candidates is None:
                candidates = daily_positive
            else:
                candidates &= daily_positive

        result = {t: round(cum_ratio.get(t, 0), 4) for t in (candidates or set())}
        return result, days_available
    finally:
        conn.close()


def _summarize_filters(filters: dict) -> dict:
    """필터 요약 (내부 표시용)."""
    summary = {}
    keys = ["market_cap_min", "market_cap_max", "chg_pct_min", "chg_pct_max",
            "foreign_ratio_min", "fi_ratio_min", "per_min", "per_max",
            "pbr_min", "pbr_max", "turnover_min", "sort", "n", "market"]
    for k in keys:
        v = filters.get(k)
        if v is not None:
            summary[k] = v
    return summary


def scan_stocks(db: dict, filters: dict, preset: str = None) -> dict:
    """필터 조건으로 종목 스캔.

    filters keys:
        market_cap_min/max (억원), chg_pct_min/max (%), foreign_ratio_min,
        fi_ratio_min, per_min/max, pbr_max, turnover_min,
        sort (str), n (int), market (kospi/kosdaq/all)

    Returns: {date, preset, filters, count, results: [...]}
    """
    stocks = db.get("stocks", {})
    date = db.get("date", "")

    # ── 프리셋 적용 ──
    preset_desc = None
    if preset and preset in PRESETS:
        p = PRESETS[preset]
        preset_desc = p.get("description", "")
        pf = p.get("filters", {})
        merged = {**pf}
        for k, v in filters.items():
            if v is not None:
                merged[k] = v
        filters = merged
        if "sort" not in filters or filters.get("sort") is None:
            filters["sort"] = p.get("sort", "fi_ratio")

    # 필터 파라미터
    mcap_min = float(filters.get("market_cap_min", 0)) * 100_000_000    # 억원 → 원
    mcap_max = float(filters.get("market_cap_max", 9999999)) * 100_000_000
    chg_min = float(filters.get("chg_pct_min", -30))
    chg_max = float(filters.get("chg_pct_max", 30))
    fr_min = float(filters.get("foreign_ratio_min", -999))
    fi_min = float(filters.get("fi_ratio_min", -999))
    per_min = float(filters.get("per_min", 0))
    per_max = float(filters.get("per_max", 9999))
    pbr_min = float(filters.get("pbr_min", 0))
    pbr_max = float(filters.get("pbr_max", 9999))
    turn_min = float(filters.get("turnover_min", 0))
    sort_by = filters.get("sort", "fi_ratio")
    n = int(filters.get("n", 30))
    n = max(1, min(n, 100))
    market_filter = filters.get("market", "all")

    # 시장 평균 등락률
    summary = db.get("market_summary", {})
    market_avg_chg = round(
        (summary.get("kospi_avg_chg", 0) + summary.get("kosdaq_avg_chg", 0)) / 2, 2)

    # relative_strength: 동적 chg_pct_min
    if preset == "relative_strength":
        if "chg_pct_min" not in filters or filters["chg_pct_min"] == chg_min:
            chg_min = market_avg_chg + 3.0
        fi_min = max(fi_min, 0)

    # foreign_streak: 연속 매수 종목 + 누적 비율
    streak_data = None
    days_available = 0
    if preset == "foreign_streak":
        streak_days = max(2, int(filters.get("streak_days", 5)))
        streak_data, days_available = _get_foreign_streak_data_db(date, streak_days)
        if days_available < streak_days:
            preset_desc = f"최근 {days_available}/{streak_days}일 연속 외인 순매수 (DB 부족)"
        if not streak_data:
            return {
                "date": date,
                "preset": preset,
                "preset_description": preset_desc,
                "filters": _summarize_filters(filters),
                "market_avg_chg": market_avg_chg,
                "days_available": days_available,
                "total_matched": 0,
                "count": 0,
                "results": [],
                "note": f"연속 매수 종목 없음 (가용 DB: {days_available}/{streak_days}일)",
            }

    # ── 필터링 ──
    results = []
    for ticker, s in stocks.items():
        mcap = s.get("market_cap", 0) or 0
        if mcap < mcap_min or mcap > mcap_max:
            continue
        chg = s.get("chg_pct", 0) or 0
        if chg < chg_min or chg > chg_max:
            continue
        fr = s.get("foreign_ratio", 0) or 0
        if fr < fr_min:
            continue
        fi = s.get("fi_ratio") or 0
        if fi < fi_min:
            continue
        per = s.get("per", 0) or 0
        if per_min > 0 and (per < per_min or per > per_max):
            continue
        if per_max < 9999 and per > per_max:
            continue
        pbr = s.get("pbr", 0) or 0
        if pbr_min > 0 and pbr < pbr_min:
            continue
        if pbr_max < 9999 and pbr > pbr_max:
            continue
        turn = s.get("turnover", 0) or 0
        if turn < turn_min:
            continue
        if market_filter != "all":
            if s.get("market", "") != market_filter:
                continue
        if streak_data is not None and ticker not in streak_data:
            continue

        item = {
            "ticker": ticker,
            "name": s.get("name", ticker),
            "market": s.get("market", ""),
            "close": s.get("close", 0),
            "chg_pct": chg,
            "market_cap": round(mcap / 100_000_000),  # 원 → 억원
            "per": per,
            "pbr": pbr,
            "foreign_ratio": fr,
            "inst_ratio": s.get("inst_ratio", 0) or 0,
            "fi_ratio": fi,
            "turnover": turn,
        }
        if streak_data is not None:
            item["cum_foreign_ratio"] = streak_data.get(ticker, 0)
        results.append(item)

    # ── 정렬 ──
    reverse = True
    if sort_by in ("per", "pbr"):
        reverse = False
    if sort_by == "chg_pct" and preset == "oversold":
        reverse = False
    results.sort(key=lambda x: x.get(sort_by, 0) or 0, reverse=reverse)
    total_matched = len(results)
    results = results[:n]

    out = {
        "date": date,
        "preset": preset,
        "preset_description": preset_desc,
        "filters": _summarize_filters(filters),
        "market_avg_chg": market_avg_chg,
        "total_matched": total_matched,
        "count": len(results),
        "results": results,
    }
    if preset == "foreign_streak":
        out["days_available"] = days_available
    return out
