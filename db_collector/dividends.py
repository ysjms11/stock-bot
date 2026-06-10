"""배당 수집 / div_yield 재계산.

P3-4 박리: _div_num, _recompute_div_yield_from_events, collect_dividends
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta

from ._config import KST
from ._db import _get_db, db_write_lock


def _div_num(x) -> float:
    try:
        return float(str(x).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


def _recompute_div_yield_from_events(conn: sqlite3.Connection, dates: list = None) -> dict:
    """dividend_events(KIS 예탁원 DPS)로 div_yield 재계산. KRX 불필요.

    div_yield[date][sym] = (record_date ∈ (date-365, date] 현금 DPS 합) / close × 100.
    종목별 실배당이라 재구성(낡은 앵커)보다 정확. 배당 종목 셀만 갱신(무배당/미보유 불변).
    dates=None이면 전 거래일 재계산(소급 포함).
    """
    evrows = conn.execute(
        "SELECT symbol, record_date, dps_cash FROM dividend_events WHERE dps_cash > 0").fetchall()
    if not evrows:
        return {"updated": 0, "note": "dividend_events 비어있음 (collect_dividends 먼저)"}
    ev = {}  # symbol -> [(record_date_int, dps), ...]
    for r in evrows:
        try:
            ev.setdefault(r["symbol"], []).append((int(r["record_date"]), float(r["dps_cash"])))
        except (ValueError, TypeError):
            continue
    payers = set(ev.keys())

    # payer들의 (date, close) 일괄 로드
    closes = {}  # symbol -> {date: close}
    for r in conn.execute("SELECT trade_date, symbol, close FROM daily_snapshot WHERE close > 0").fetchall():
        if r["symbol"] in payers:
            closes.setdefault(r["symbol"], {})[r["trade_date"]] = r["close"]

    want = set(dates) if dates else None
    # 날짜별 직전-12M 하한 1회 precompute
    all_dates = {d for cm in closes.values() for d in cm if (want is None or d in want)}
    date_lo = {d: int((datetime.strptime(d, "%Y%m%d") - timedelta(days=365)).strftime("%Y%m%d"))
               for d in all_dates}

    updates = []  # (div_yield, date, symbol)
    for sym, evlist in ev.items():
        cmap = closes.get(sym)
        if not cmap:
            continue
        for d, c in cmap.items():
            if c <= 0 or (want is not None and d not in want):
                continue
            di = int(d); lo = date_lo[d]
            ttm = sum(dps for (rd, dps) in evlist if lo < rd <= di)
            if ttm > 0:
                updates.append((round(ttm / c * 100.0, 4), d, sym))
    if updates:
        # 비파괴: 기존 실값(pre-04-08 KRX DVD_YLD 등)은 보존하고 0/NULL(미수집)만 채운다.
        conn.executemany(
            "UPDATE daily_snapshot SET div_yield=? WHERE trade_date=? AND symbol=? "
            "AND (div_yield IS NULL OR div_yield=0)", updates)
        conn.commit()
    return {"candidates": len(updates), "payers": len(ev)}


async def collect_dividends(tickers: list = None, lookback_days: int = 430) -> dict:
    """[KRX 불필요] KIS 예탁원(HHKDB669102C0)으로 종목별 현금배당 DPS 수집 → dividend_events 저장
    → div_yield 재계산. DPS는 sticky(연 1회)라 주 1회 수집 권장. div_yield = DPS÷종가.
    """
    try:
        from kis_api import get_kis_token, kis_dividend_schedule
    except ImportError as e:
        return {"error": f"kis_api import 실패: {e}"}
    tok = await get_kis_token()
    if not tok:
        return {"error": "KIS 토큰 발급 실패"}

    conn = _get_db()
    if tickers is None:
        cutoff = (datetime.now(KST) - timedelta(days=14)).strftime("%Y%m%d")
        tickers = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM daily_snapshot WHERE trade_date >= ?", (cutoff,)).fetchall()]
    today = datetime.now(KST).strftime("%Y%m%d")
    from_dt = (datetime.now(KST) - timedelta(days=lookback_days)).strftime("%Y%m%d")

    sem = asyncio.Semaphore(6)
    events = []
    stat = {"ok": 0, "fail": 0, "payers": 0}

    async def _one(t):
        async with sem:
            try:
                rows = await kis_dividend_schedule(tok, from_dt=from_dt, to_dt=today, ticker=t, gb1="0")
                stat["ok"] += 1
            except Exception:
                stat["fail"] += 1
                return
            got = False
            for r in (rows or []):
                amt = _div_num(r.get("per_sto_divi_amt"))   # 현금 DPS (주식배당은 0)
                rd = (r.get("record_date") or "").strip()
                if amt > 0 and len(rd) == 8 and rd.isdigit():
                    events.append((t, rd, amt, (r.get("divi_kind") or "").strip(),
                                   (r.get("divi_pay_dt") or "").strip()))
                    got = True
            if got:
                stat["payers"] += 1
            await asyncio.sleep(0.03)

    # circuit breaker: 첫 50종목 프로브
    probe = tickers[:50]
    await asyncio.gather(*[_one(t) for t in probe])
    if probe and stat["fail"] >= len(probe) * 0.8:
        conn.close()
        print(f"[Dividends] KIS 배당 조회 프로브 대량 실패 ({stat['fail']}/{len(probe)}) → 중단")
        return {"error": "KIS 배당 조회 대량 실패", **stat}
    await asyncio.gather(*[_one(t) for t in tickers[50:]])

    now = datetime.now(KST).isoformat()
    # 쓰기 직렬화 — asyncio.gather fetch 완료 후 sync 쓰기 구간이므로 한 번만 잠근다.
    async with db_write_lock:
        conn.executemany(
            "INSERT OR REPLACE INTO dividend_events(symbol,record_date,dps_cash,divi_kind,pay_date,fetched_at) "
            "VALUES (?,?,?,?,?,?)",
            [(s, rd, amt, k, p, now) for (s, rd, amt, k, p) in events])
        conn.commit()
        rc = _recompute_div_yield_from_events(conn)
    conn.close()
    print(f"[Dividends] KIS 예탁원: 조회 {stat['ok']}, payers {stat['payers']}, "
          f"events {len(events)}, 실패 {stat['fail']} → div_yield {rc}")
    return {"tickers": stat["ok"], "payers": stat["payers"], "events": len(events),
            "fail": stat["fail"], "recompute": rc}
