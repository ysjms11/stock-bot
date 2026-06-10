"""재무 수집 함수들 (주 1회 + 증분 + 공급 + 컨센서스).

P3-6 박리: collect_financial_weekly, _upsert_dart_full_row, _collect_dart_full_batch,
           collect_financial_historical, collect_financial_on_disclosure,
           _fetch_supply_data, _write_supply_to_snapshot, _update_supply_in_snapshot,
           _update_consensus_in_snapshot, _update_financial_derived

late-binding 결정:
- update_all_alpha_metrics: top-level import from .alpha. _BACKING 등록 후
  monkeypatch.setattr(db_collector, ...) 가 financial.update_all_alpha_metrics를
  직접 갱신 → collect_financial_on_disclosure 내 globals() 조회 시 패치 값 보임.
- _DART_INTERVAL: 이 파일에 module-level 정의 (canonical). 패치 투명성 동일.
"""

import asyncio
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from kis_api import _get_session
from ._config import KST
from ._db import _get_db, db_write_lock
from .alpha import update_all_alpha_metrics

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Rate limiter (collect.py 와 독립 사본 — 순환 import 방지)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_RATE_SEM = None  # collect_financial_weekly 시작 시 초기화


async def _rate_limited(coro):
    """초당 8건 제한 (세마포어 + jitter 슬립)."""
    import random
    async with _RATE_SEM:
        result = await coro
        await asyncio.sleep(0.10 + random.random() * 0.06)
        return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 재무 수집 (주 1회)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def collect_financial_weekly(date: str = None) -> dict:
    """손익계산서 + 대차대조표 수집 → financial_quarterly UPSERT.
    주 1회 실행. KIS API kis_income_statement / kis_balance_sheet 사용.
    """
    global _RATE_SEM
    _RATE_SEM = asyncio.Semaphore(8)

    conn = _get_db()
    tickers = [r["symbol"] for r in conn.execute(
        "SELECT symbol FROM stock_master"
    ).fetchall()]

    if not tickers:
        conn.close()
        return {"error": "stock_master 비어 있음"}

    from kis_api import get_kis_token

    token = await get_kis_token()
    if not token:
        conn.close()
        return {"error": "KIS 토큰 발급 실패"}

    success_is = 0
    success_bs = 0
    success_dart = 0

    session = _get_session()
    # Per-ticker timeout 10초 (5/5 사고 후) — 한 종목 hang으로 전체 60분 타임아웃 방지
    _PER_TICKER_TIMEOUT = 10.0
    _timeout_count = {"is": 0, "bs": 0}
    # 진행 로그 50건마다 (이전 200 → 50, buffering 가시성 ↑)
    _PROGRESS_EVERY = 50

    # Phase A: 손익계산서
    print(f"[Finance] Phase A — 손익계산서 {len(tickers)}종목 시작", flush=True)
    _phase_start = asyncio.get_running_loop().time()
    for i, ticker in enumerate(tickers):
        try:
            from kis_api import kis_income_statement
            rows_is = await asyncio.wait_for(
                _rate_limited(kis_income_statement(ticker, token, session=session)),
                timeout=_PER_TICKER_TIMEOUT
            )
            # fetch 완료 후 쓰기+커밋을 동일 lock 블록 안에서 수행
            async with db_write_lock:
                for r in (rows_is or []):
                    rp = r.get("report_period", "")
                    if not rp:
                        continue
                    conn.execute("""
                        INSERT OR REPLACE INTO financial_quarterly (
                            symbol, report_period, revenue, cost_of_sales, gross_profit,
                            operating_profit, op_profit, net_income, collected_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """, (
                        ticker, rp,
                        r.get("revenue"), r.get("cost_of_sales"), r.get("gross_profit"),
                        r.get("operating_profit"), r.get("op_profit"), r.get("net_income"),
                    ))
                conn.commit()
            success_is += 1
        except asyncio.TimeoutError:
            _timeout_count["is"] += 1
        except Exception as e:
            print(f"[Finance] {ticker} 손익계산서 수집 실패 (무시): {e}")
        if (i + 1) % _PROGRESS_EVERY == 0:
            elapsed = asyncio.get_running_loop().time() - _phase_start
            print(f"[Finance] 손익: {i+1}/{len(tickers)} (성공 {success_is}, 타임아웃 {_timeout_count['is']}, {elapsed:.0f}s)", flush=True)
    elapsed_a = asyncio.get_running_loop().time() - _phase_start
    print(f"[Finance] Phase A 완료 — 성공 {success_is}/{len(tickers)}, 타임아웃 {_timeout_count['is']}, {elapsed_a:.0f}s", flush=True)

    # Phase B: 대차대조표
    print(f"[Finance] Phase B — 대차대조표 {len(tickers)}종목 시작", flush=True)
    _phase_start = asyncio.get_running_loop().time()
    for i, ticker in enumerate(tickers):
        try:
            from kis_api import kis_balance_sheet
            rows_bs = await asyncio.wait_for(
                _rate_limited(kis_balance_sheet(ticker, token, session=session)),
                timeout=_PER_TICKER_TIMEOUT
            )
            # fetch 완료 후 쓰기+커밋을 동일 lock 블록 안에서 수행
            async with db_write_lock:
                for r in (rows_bs or []):
                    rp = r.get("report_period", "")
                    if not rp:
                        continue
                    conn.execute("""
                        UPDATE financial_quarterly SET
                            current_assets=?, fixed_assets=?, total_assets=?,
                            current_liab=?, fixed_liab=?, total_liab=?,
                            capital=?, total_equity=?,
                            collected_at=datetime('now')
                        WHERE symbol=? AND report_period=?
                    """, (
                        r.get("current_assets"), r.get("fixed_assets"), r.get("total_assets"),
                        r.get("current_liab"), r.get("fixed_liab"), r.get("total_liab"),
                        r.get("capital"), r.get("total_equity"),
                        ticker, rp,
                    ))
                conn.commit()
            success_bs += 1
        except asyncio.TimeoutError:
            _timeout_count["bs"] += 1
        except Exception:
            pass
        if (i + 1) % _PROGRESS_EVERY == 0:
            elapsed = asyncio.get_running_loop().time() - _phase_start
            print(f"[Finance] 대차: {i+1}/{len(tickers)} (성공 {success_bs}, 타임아웃 {_timeout_count['bs']}, {elapsed:.0f}s)", flush=True)
    elapsed_b = asyncio.get_running_loop().time() - _phase_start
    print(f"[Finance] Phase B 완료 — 성공 {success_bs}/{len(tickers)}, 타임아웃 {_timeout_count['bs']}, {elapsed_b:.0f}s", flush=True)

    # Phase C: DART 현금흐름표 + 지배귀속 + 판관비/매출채권/재고 (최신 4분기)
    # F/M/FCF Phase1 — dart_quarterly_full 1콜로 PL/BS/CF 전체
    try:
        from kis_api import get_dart_corp_map, dart_quarterly_full
        corp_map = await get_dart_corp_map({})
    except Exception as e:
        print(f"[Finance] Phase C skip — corp_map 로드 실패: {e}")
        corp_map = {}

    if corp_map:
        # 최신 4분기 (현재연도 Q1 ~ 전년도 Q2) 기준 — TTM 1회분 확보
        from datetime import datetime as _dt
        now = _dt.now(KST)
        # DART 공시 지연(~45일) 감안: 직전 확정 분기부터 과거로 4개
        current_q = (now.month - 1) // 3 + 1
        targets = []  # (year, quarter)
        y, q = now.year, max(current_q - 1, 1) if current_q > 1 else 4
        if current_q == 1:
            y = now.year - 1
        for _ in range(4):
            targets.append((y, q))
            q -= 1
            if q < 1:
                q = 4
                y -= 1

        print(f"[Finance] Phase C — DART 현금흐름표 {len(tickers)}종목 × "
              f"{len(targets)}분기 = {len(tickers) * len(targets)}콜")
        success_dart = await _collect_dart_full_batch(
            conn, tickers, corp_map, targets
        )

    # 재무 파생값 → daily_snapshot UPDATE
    async with db_write_lock:
        _update_financial_derived(conn, date)
    conn.close()

    print(f"[Finance] 완료 — IS:{success_is}/{len(tickers)}, "
          f"BS:{success_bs}/{len(tickers)}, DART:{success_dart}")
    return {
        "tickers": len(tickers),
        "income_statement": success_is,
        "balance_sheet": success_bs,
        "dart_full": success_dart,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 전체 재무제표 배치 (F/M/FCF Phase1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# DART 분당 1000건 제한 → 안전 마진 900/분 = 0.067초/콜
_DART_INTERVAL = 0.067


def _upsert_dart_full_row(conn: sqlite3.Connection, ticker: str, r: dict):
    """dart_quarterly_full 결과 1분기를 financial_quarterly에 UPSERT.
    기존 KIS IS/BS 데이터를 덮어쓰지 않기 위해 COALESCE 패턴 사용.
    10개 신규 컬럼 + (없을 때) revenue/operating_profit/net_income/… 보강.
    """
    # FK guard 내재화 (학습 #29): stock_master 미등록 ticker 는 silent skip
    # 5/8 d662b69 fix 가 호출 site 한 곳만 커버 → 헬퍼 안으로 push (방어 위치 통일)
    if not conn.execute("SELECT 1 FROM stock_master WHERE symbol=? LIMIT 1", (ticker,)).fetchone():
        return False

    rp = r.get("report_period", "")
    if not rp:
        return False

    # 먼저 row 존재 보장 (INSERT OR IGNORE로 PK만 채움)
    conn.execute(
        "INSERT OR IGNORE INTO financial_quarterly (symbol, report_period, collected_at) "
        "VALUES (?, ?, datetime('now'))",
        (ticker, rp),
    )
    # 신규 컬럼은 항상 DART 값으로 덮어쓰기 (KIS에는 없음)
    # 기존 컬럼은 COALESCE로 기존값 유지
    conn.execute("""
        UPDATE financial_quarterly SET
            revenue          = COALESCE(revenue, ?),
            cost_of_sales    = COALESCE(cost_of_sales, ?),
            gross_profit    = COALESCE(gross_profit, ?),
            operating_profit = COALESCE(operating_profit, ?),
            net_income       = COALESCE(net_income, ?),
            current_assets   = COALESCE(current_assets, ?),
            total_assets     = COALESCE(total_assets, ?),
            current_liab     = COALESCE(current_liab, ?),
            total_liab       = COALESCE(total_liab, ?),
            capital          = COALESCE(capital, ?),
            total_equity     = COALESCE(total_equity, ?),
            cfo              = ?,
            capex            = ?,
            fcf              = ?,
            depreciation     = ?,
            sga              = ?,
            receivables      = ?,
            inventory        = ?,
            shares_out       = ?,
            net_income_parent = ?,
            equity_parent    = ?,
            fs_source        = ?,
            collected_at     = datetime('now')
        WHERE symbol=? AND report_period=?
    """, (
        r.get("revenue"), r.get("cost_of_sales"), r.get("gross_profit"),
        r.get("operating_profit"), r.get("net_income"),
        r.get("current_assets"), r.get("total_assets"),
        r.get("current_liab"), r.get("total_liab"),
        r.get("capital"), r.get("total_equity"),
        r.get("cfo"), r.get("capex"), r.get("fcf"),
        r.get("depreciation"), r.get("sga"),
        r.get("receivables"), r.get("inventory"),
        r.get("shares_out"), r.get("net_income_parent"),
        r.get("equity_parent"), r.get("fs_source"),
        ticker, rp,
    ))
    return True


async def _collect_dart_full_batch(conn: sqlite3.Connection, tickers: list,
                                    corp_map: dict,
                                    targets: list) -> int:
    """DART fnlttSinglAcntAll 배치 수집 (tickers × targets).

    tickers: [symbol, ...]
    corp_map: {symbol: corp_code}
    targets: [(year, quarter), ...]
    반환: 성공 콜 수 (종목·분기 단위)
    """
    from kis_api import dart_quarterly_full

    success = 0
    total = len(tickers) * len(targets)
    done = 0
    skipped_no_corp = 0

    session = _get_session()
    for ticker in tickers:
        corp_code = corp_map.get(ticker)
        if not corp_code:
            skipped_no_corp += 1
            done += len(targets)
            continue
        for (y, q) in targets:
            try:
                r = await dart_quarterly_full(corp_code, y, q, session=session)
                if r:
                    # fetch 완료 후 쓰기+커밋을 동일 lock 블록 안에서 수행
                    async with db_write_lock:
                        _upsert_dart_full_row(conn, ticker, r)
                        conn.commit()
                    success += 1
            except Exception:
                pass
            done += 1
            await asyncio.sleep(_DART_INTERVAL)
            if done % 500 == 0:
                print(f"[DART-Full] 진행: {done}/{total} (성공 {success})")

    print(f"[DART-Full] 완료 — 성공 {success}/{total}, corp_map 미등록 스킵 {skipped_no_corp}")
    return success


async def collect_financial_historical(quarters_back: int = 12,
                                       tickers_limit: int | None = None) -> dict:
    """최근 N분기 DART 전체 재무제표 소급 수집 (F/M/FCF Phase1 1회용).

    유니버스 3200종목 × 12분기 = ~38,400콜.
    DART 분당 1000콜 제한 → 0.067초/콜 = 약 43분 소요.

    Args:
        quarters_back: 과거 몇 분기 수집할지 (기본 12 = 3년)
        tickers_limit: 테스트용 종목 수 제한 (None=전종목)

    Returns:
        {"tickers": N, "quarters": Q, "calls_made": N*Q, "success": S,
         "duration_sec": T}
    """
    from datetime import datetime as _dt
    import time

    conn = _get_db()
    tickers = [r["symbol"] for r in conn.execute(
        "SELECT symbol FROM stock_master"
    ).fetchall()]
    if tickers_limit:
        tickers = tickers[:tickers_limit]

    if not tickers:
        conn.close()
        return {"error": "stock_master 비어 있음"}

    # corp_codes.json (3959종목) 우선, fallback으로 dart_corp_map.json (211종목)
    try:
        from kis_api import load_corp_codes, get_dart_corp_map
        full_map = await load_corp_codes()  # {ticker: {corp_code, corp_name}}
        corp_map = {tk: v["corp_code"] for tk, v in full_map.items() if v.get("corp_code")}
        if not corp_map:
            legacy = await get_dart_corp_map({})
            corp_map = legacy if isinstance(legacy, dict) else {}
    except Exception as e:
        conn.close()
        return {"error": f"corp_map 로드 실패: {e}"}

    if not corp_map:
        conn.close()
        return {"error": "corp_map 비어 있음 — corp_codes.json / dart_corp_map.json 확인"}

    print(f"[Historical] corp_map 엔트리: {len(corp_map)}종목")

    # 타겟 분기 리스트 생성 (DART 공시 지연 ~45일 고려 → 직전 확정 분기부터)
    now = _dt.now(KST)
    current_q = (now.month - 1) // 3 + 1
    y, q = now.year, current_q - 1
    if q < 1:
        q = 4
        y -= 1
    targets = []
    for _ in range(quarters_back):
        targets.append((y, q))
        q -= 1
        if q < 1:
            q = 4
            y -= 1

    total_calls = len(tickers) * len(targets)
    print(f"[Historical] 대상: {len(tickers)}종목 × {len(targets)}분기 = {total_calls}콜")
    print(f"[Historical] 예상 소요: 약 {total_calls * _DART_INTERVAL / 60:.1f}분")
    print(f"[Historical] 타겟 분기: {targets[0]} ~ {targets[-1]}")

    start = time.time()
    success = await _collect_dart_full_batch(conn, tickers, corp_map, targets)
    duration = time.time() - start

    conn.close()

    return {
        "tickers": len(tickers),
        "quarters": len(targets),
        "calls_made": total_calls,
        "success": success,
        "duration_sec": round(duration, 1),
        "target_range": f"{targets[-1]} ~ {targets[0]}",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 증분 수집 (신규 정기공시 기반)
# - 매일 02:00 KST에 main.py 스케줄러가 호출
# - search_dart_periodic_new(days=2)로 최근 2일 정기공시 목록
# - (corp_code, report_period) 쌍 기준 DB 미존재 + cfo IS NULL 만 신규 수집
# - fnlttSinglAcntAll + stockTotqySttus 두 API 사용, 0.067초/콜 간격
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def collect_financial_on_disclosure(days: int = 2,
                                           max_calls: int = 1000) -> dict:
    """신규 정기공시 기반 증분 수집 + 알파 재계산 트리거.

    흐름:
      1) search_dart_periodic_new(days) → 최근 N일 원본 정기공시 목록.
      2) 각 (corp_code, report_period) 쌍 DB 중복 체크.
         - financial_quarterly에 해당 row가 있고 cfo IS NOT NULL 이면 skip.
         - corp_code → ticker 매핑은 corp_codes.json 역방향 (dict{ticker: {corp_code,corp_name}}
           → {corp_code: ticker}). stock_code가 list.json에도 있으니 이를 fallback.
      3) 신규 건만 dart_quarterly_full + dart_shares_outstanding 호출 후 upsert.
      4) 0.067초/콜 rate limit 준수. max_calls 도달 시 중단.
      5) 수집 >0건이면 update_all_alpha_metrics(end_period=최신 period) 호출.

    Args:
        days: 조회 기간 (기본 2일 — 일 1회 스케줄 여유 포함).
        max_calls: DART API 호출 상한 (fnlttSinglAcntAll + stockTotqySttus 합산).
                   기본 1000 = DART 분당 1000콜 상한 고려한 1분치 여유.

    Returns:
        {"disclosures_found", "already_in_db", "skipped_no_ticker",
         "newly_collected", "fnltt_calls", "shares_calls", "alpha_recalc" (dict|None),
         "duration_sec", "quota_used_estimate"}
    """
    from datetime import datetime as _dt
    import time as _time

    from kis_api import (
        search_dart_periodic_new,
        dart_quarterly_full,
        dart_shares_outstanding,
        load_corp_codes,
    )

    start = _time.time()
    result = {
        "disclosures_found": 0,
        "already_in_db": 0,
        "skipped_no_ticker": 0,
        "newly_collected": 0,
        "fnltt_calls": 0,
        "shares_calls": 0,
        "alpha_recalc": None,
        "duration_sec": 0.0,
        "quota_used_estimate": 0,
        "errors": 0,
    }

    # Step 1: 신규 공시 목록
    try:
        scan_sess = _get_session()
        disclosures = await search_dart_periodic_new(days=days, session=scan_sess)
    except Exception as e:
        print(f"[DART-Incr] search_dart_periodic_new 오류: {e}")
        result["errors"] += 1
        result["duration_sec"] = round(_time.time() - start, 1)
        return result

    result["disclosures_found"] = len(disclosures)
    if not disclosures:
        result["duration_sec"] = round(_time.time() - start, 1)
        return result

    # Step 2: corp_code → ticker 역매핑 (list.json에 stock_code가 포함되어 있지만
    # 보험용으로 corp_codes.json 역매핑도 준비)
    try:
        full_map = await load_corp_codes()  # {ticker: {corp_code, corp_name}}
    except Exception as e:
        print(f"[DART-Incr] load_corp_codes 실패: {e}")
        full_map = {}
    corp_to_ticker = {
        v.get("corp_code"): tk for tk, v in full_map.items() if v.get("corp_code")
    }

    conn = _get_db()

    # Step 3: (corp_code, report_period) 중복 + ticker 해결 후 수집 대상 필터링
    # 동일 corp_code+period가 여러 row로 올 수 있으니 dedup.
    to_collect: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    for d in disclosures:
        corp_code = d["corp_code"]
        period    = d["report_period"]
        if (corp_code, period) in seen_pairs:
            continue
        seen_pairs.add((corp_code, period))

        # ticker 해석: 1순위 list.json stock_code, 2순위 corp_codes 역매핑
        ticker = d.get("ticker") or corp_to_ticker.get(corp_code, "")
        if not ticker or len(ticker) != 6:
            # 비상장/지주사 하위 등 — corp_code만 있고 ticker 없는 케이스 skip
            result["skipped_no_ticker"] += 1
            continue

        # DB 중복 체크 — cfo NOT NULL이면 이미 완전 수집된 row (덮어쓰지 않음)
        row = conn.execute(
            "SELECT 1 FROM financial_quarterly "
            "WHERE symbol=? AND report_period=? AND cfo IS NOT NULL",
            (ticker, period),
        ).fetchone()
        if row:
            result["already_in_db"] += 1
            continue

        to_collect.append({**d, "ticker": ticker})

    if not to_collect:
        conn.close()
        result["duration_sec"] = round(_time.time() - start, 1)
        return result

    # Step 4: 수집 (fnlttSinglAcntAll + stockTotqySttus) — 종목당 2콜
    # max_calls 안전장치: 2콜/종목 기준 허용 종목 수 계산
    max_pairs = max_calls // 2
    if len(to_collect) > max_pairs:
        print(f"[DART-Incr] 공시 {len(to_collect)}건 > 허용 {max_pairs}건 — 앞에서만 수집")
        to_collect = to_collect[:max_pairs]

    latest_period = ""
    sess = _get_session()
    for item in to_collect:
        ticker    = item["ticker"]
        corp_code = item["corp_code"]
        period    = item["report_period"]

        # period "YYYYMM" → (year, quarter)
        try:
            year  = int(period[:4])
            month = int(period[4:])
            q_map = {3: 1, 6: 2, 9: 3, 12: 4}
            quarter = q_map.get(month)
        except (ValueError, TypeError, IndexError):
            continue
        if not quarter:
            continue

        # 4a. fnlttSinglAcntAll
        # 5/8 fix: stock_master에 없는 종목은 FK constraint 위반 → skip
        # (DART 등록되었으나 KIS 미커버 또는 stock_master 미갱신 케이스)
        master_exists = conn.execute(
            "SELECT 1 FROM stock_master WHERE symbol = ? LIMIT 1", (ticker,)
        ).fetchone()
        if not master_exists:
            print(f"[DART-Incr] skip {ticker}({corp_code}) — stock_master 미등록")
            continue
        # 4a. fnlttSinglAcntAll — fetch 먼저, 쓰기는 lock 안에서
        _dart_row = None
        try:
            r = await dart_quarterly_full(corp_code, year, quarter, session=sess)
            result["fnltt_calls"] += 1
            if r:
                _dart_row = r
        except Exception as e:
            print(f"[DART-Incr] full {ticker}({corp_code}) {year}Q{quarter} 오류: {e}")
            result["errors"] += 1
            await asyncio.sleep(_DART_INTERVAL)
            continue
        if _dart_row:
            async with db_write_lock:
                _upsert_dart_full_row(conn, ticker, _dart_row)
                conn.commit()
            if period > latest_period:
                latest_period = period
        await asyncio.sleep(_DART_INTERVAL)

        if (result["fnltt_calls"] + result["shares_calls"]) >= max_calls:
            print(f"[DART-Incr] max_calls({max_calls}) 도달 — 수집 중단")
            break

        # 4b. stockTotqySttus — fetch 먼저, 쓰기는 lock 안에서
        try:
            shares = await dart_shares_outstanding(
                corp_code, year, quarter, session=sess,
            )
            result["shares_calls"] += 1
            if shares:
                async with db_write_lock:
                    conn.execute(
                        "UPDATE financial_quarterly SET shares_out=? "
                        "WHERE symbol=? AND report_period=?",
                        (shares, ticker, period),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[DART-Incr] shares {ticker}({corp_code}) {year}Q{quarter} 오류: {e}")
            result["errors"] += 1
        await asyncio.sleep(_DART_INTERVAL)

        result["newly_collected"] += 1

        if (result["fnltt_calls"] + result["shares_calls"]) >= max_calls:
            print(f"[DART-Incr] max_calls({max_calls}) 도달 — 수집 중단")
            break

    conn.close()

    # Step 5: 알파 메트릭 재계산 (최신 period 기준)
    if result["newly_collected"] > 0 and latest_period:
        try:
            async with db_write_lock:
                result["alpha_recalc"] = update_all_alpha_metrics(end_period=latest_period)
        except Exception as e:
            print(f"[DART-Incr] update_all_alpha_metrics 오류: {e}")
            result["alpha_recalc"] = {"error": str(e)}

    result["quota_used_estimate"] = result["fnltt_calls"] + result["shares_calls"]
    result["duration_sec"] = round(_time.time() - start, 1)
    print(f"[DART-Incr] 완료 — 공시 {result['disclosures_found']}건 → "
          f"신규 수집 {result['newly_collected']}건 "
          f"(중복 {result['already_in_db']}, 무ticker {result['skipped_no_ticker']}) "
          f"쿼터 {result['quota_used_estimate']}콜, "
          f"{result['duration_sec']:.0f}초")
    return result


def _fetch_supply_data(date: str) -> dict:
    """pykrx 종목별 외인/기관 순매수 데이터를 네트워크에서 가져온다 (blocking HTTP+sleep 포함).

    ⚠️ 이 함수는 동기 네트워크 호출 + sleep이 있으므로 db_write_lock 밖에서 호출해야 한다.

    Returns: {"rows": [(col_qty, col_amt, date, ticker, qty, amt), ...],
              "empty": N, "errors": [...]}
    """
    if not date:
        return {"rows": [], "empty": 0, "errors": ["date 누락"]}

    try:
        from pykrx import stock
    except ImportError:
        return {"rows": [], "empty": 0, "errors": ["pykrx 미설치"]}

    import time as _time

    def _net_purchases_retry(mkt: str, inv_name: str, tries: int = 3):
        """pykrx 호출 + retry. 성공 시 비어있지 않은 df, 실패/빈응답 시 None."""
        last = None
        for attempt in range(1, tries + 1):
            try:
                df = stock.get_market_net_purchases_of_equities(date, date, mkt, inv_name)
                if df is not None and len(df) > 0:
                    return df, None
                last = "빈 응답(KRX soft-block 가능)"
            except Exception as e:
                last = f"{type(e).__name__}: {str(e)[:80]}"
            if attempt < tries:
                _time.sleep(1.5 * attempt)  # backoff
        return None, last

    rows = []  # (col_qty, col_amt, qty, amt, ticker)
    empty = 0
    errors = []

    # 외국인 (foreign_net_qty/amt) + 기관합계 (inst_net_qty/amt)
    for inv_name, col_qty, col_amt in [
        ("외국인", "foreign_net_qty", "foreign_net_amt"),
        ("기관합계", "inst_net_qty", "inst_net_amt"),
    ]:
        for mkt in ("KOSPI", "KOSDAQ"):
            df, err = _net_purchases_retry(mkt, inv_name)
            if df is None:
                empty += 1
                errors.append(f"{mkt} {inv_name}: {err}")
                continue  # 실패: 덮어쓰지 않음 → KIS 1차값 보존
            for ticker, row in df.iterrows():
                try:
                    qty = int(row.get("순매수거래량", 0) or 0)
                    amt = int(row.get("순매수거래대금", 0) or 0)
                    if qty == 0 and amt == 0:
                        continue  # 0은 절대 기록하지 않음 (refiner 원칙)
                    rows.append((col_qty, col_amt, qty, amt, ticker))
                except Exception:
                    continue

    return {"rows": rows, "empty": empty, "errors": errors}


def _write_supply_to_snapshot(conn: sqlite3.Connection, date: str, supply_data: dict) -> dict:
    """_fetch_supply_data()의 결과를 DB에 기록한다 (순수 sync 쓰기, lock 안에서 호출).

    commit()은 호출자(async with db_write_lock 블록)가 직접 수행한다.
    Returns: {foreign_count, inst_count, empty, errors, ok}
    """
    rows = supply_data.get("rows", [])
    empty = supply_data.get("empty", 0)
    errors = list(supply_data.get("errors", []))

    foreign_n = 0
    inst_n = 0

    for col_qty, col_amt, qty, amt, ticker in rows:
        try:
            cur = conn.execute(
                f"UPDATE daily_snapshot SET {col_qty}=?, {col_amt}=? "
                f"WHERE trade_date=? AND symbol=?",
                (qty, amt, date, ticker)
            )
            if cur.rowcount > 0:
                if col_qty == "foreign_net_qty":
                    foreign_n += 1
                else:
                    inst_n += 1
        except Exception:
            continue

    ok = foreign_n > 0
    print(f"[Supply] pykrx refine: 외인 {foreign_n}, 기관 {inst_n}, 빈응답/실패 {empty}/4")
    if errors:
        for e in errors:
            print(f"  - {e}")
    if not ok:
        # KIS 1차값은 보존되므로 데이터 손실은 아니지만, KRX 경로가 죽었음을 가시화.
        print(f"⚠️ [Supply] pykrx 정밀화 전부 실패 ({date}) — KIS 1차 수급값 유지 (원 단위 근사).")
    return {
        "foreign_count": foreign_n,
        "inst_count": inst_n,
        "empty": empty,
        "errors": errors,
        "ok": ok,
    }


def _update_supply_in_snapshot(conn: sqlite3.Connection, date: str) -> dict:
    """pykrx 종목별 외인/기관 매매로 daily_snapshot 수급 금액을 '정밀화(refine)'한다.

    ⚠️ 더 이상 유일 소스가 아니다. 1차 소스는 KIS FHPTJ04160001(`*_ntby_tr_pbmn`,
    `_store_daily_snapshot`에서 기록). 이 함수는 KRX가 가용할 때만 정확한 원(KRW) 값으로
    덮어쓰는 보강(refiner)이다. KRX/pykrx는 간헐적으로 빈 응답(soft-block)을 주므로:
      - 호출은 retry+backoff 한다.
      - 빈 응답/실패 시 절대 0/NULL로 덮어쓰지 않는다 (KIS 1차값 보존).
      - 전부 실패하면 ⚠️ 경보를 출력해 가시화한다 (침묵 금지).

    ⚠️ 내부적으로 네트워크(pykrx)+sleep 후 DB 쓰기를 수행하므로 db_write_lock 밖에서
    호출해야 한다. (fetch → _write_supply_to_snapshot → conn.commit() 패턴)

    Returns: {foreign_count, inst_count, empty, errors, ok}
    """
    supply_data = _fetch_supply_data(date)
    result = _write_supply_to_snapshot(conn, date, supply_data)
    conn.commit()
    return result


# 배당 수집 / div_yield 재계산 — dividends.py 가 실소유자 (P3-4)
from .dividends import _div_num, _recompute_div_yield_from_events, collect_dividends


def _update_consensus_in_snapshot(conn: sqlite3.Connection, date: str) -> dict:
    """consensus_history 최신 → daily_snapshot.consensus_target/count/gap UPDATE.

    5/8 신규 (학습 #13 "수집 성공 but 0값 함정" 재현 사고 fix).
    이전: db_collector.py:749 주석 처리 → daily_snapshot 컨센 컬럼 영구 0.

    consensus_history 스키마: trade_date, symbol, target_avg/high/low, buy/hold/sell_count
    daily_snapshot 채울 컬럼: consensus_target (avg), consensus_count (buy+hold+sell), consensus_gap (%)

    Returns: {tickers, updated, latest_consensus_date}
    """
    if not date:
        return {"error": "date 누락"}

    # 종목별 가용 최신 컨센 (snapshot_date <= 현재 trade_date)
    # consensus_history는 매주 일요일 collect라 최근 7일 이내 데이터 사용
    rows = conn.execute("""
        SELECT ch.symbol, ch.target_avg, ch.buy_count, ch.hold_count, ch.sell_count, ch.trade_date
        FROM consensus_history ch
        INNER JOIN (
            SELECT symbol, MAX(trade_date) AS latest
            FROM consensus_history
            WHERE trade_date <= ?
            GROUP BY symbol
        ) latest ON ch.symbol = latest.symbol AND ch.trade_date = latest.latest
        WHERE ch.target_avg > 0
    """, (date,)).fetchall()

    if not rows:
        return {"error": "consensus_history 데이터 없음", "tickers": 0, "updated": 0}

    updated = 0
    latest_dates = set()
    for r in rows:
        sym = r["symbol"]
        target = r["target_avg"] or 0
        b = r["buy_count"] or 0
        h = r["hold_count"] or 0
        s = r["sell_count"] or 0
        n_brk = b + h + s
        latest_dates.add(r["trade_date"])

        # 현재가 + gap 계산
        cur_row = conn.execute(
            "SELECT close FROM daily_snapshot WHERE trade_date=? AND symbol=?",
            (date, sym)
        ).fetchone()
        if not cur_row or not cur_row["close"]:
            continue
        close = cur_row["close"]
        gap = round((target - close) / close * 100, 2) if close > 0 else None

        cur = conn.execute(
            "UPDATE daily_snapshot SET consensus_target=?, consensus_count=?, consensus_gap=? "
            "WHERE trade_date=? AND symbol=?",
            (int(target), n_brk, gap, date, sym)
        )
        if cur.rowcount > 0:
            updated += 1
    conn.commit()

    print(f"[Consensus] daily_snapshot UPDATE 완료: {updated}/{len(rows)}종목 "
          f"(latest={sorted(latest_dates)[-1] if latest_dates else None})")
    return {
        "tickers": len(rows),
        "updated": updated,
        "latest_consensus_date": sorted(latest_dates)[-1] if latest_dates else None,
    }


def _update_financial_derived(conn: sqlite3.Connection, date: str = None):
    """financial_quarterly 최신 분기 → daily_snapshot 재무 파생 컬럼 UPDATE."""
    if date is None:
        row = conn.execute("SELECT MAX(trade_date) as d FROM daily_snapshot").fetchone()
        date = row["d"] if row and row["d"] else None
    if not date:
        return

    # 각 종목의 최신 분기 재무
    financials = conn.execute("""
        SELECT f.* FROM financial_quarterly f
        INNER JOIN (
            SELECT symbol, MAX(report_period) as max_period
            FROM financial_quarterly
            GROUP BY symbol
        ) latest ON f.symbol = latest.symbol AND f.report_period = latest.max_period
    """).fetchall()

    updated = 0
    for f in financials:
        sym = f["symbol"]
        rev = f["revenue"] or 0
        op = f["operating_profit"] or 0
        ni = f["net_income"] or 0
        ta = f["total_assets"] or 0
        tl = f["total_liab"] or 0
        te = f["total_equity"] or 0

        op_margin = round(op / rev * 100, 4) if rev else None
        net_margin = round(ni / rev * 100, 4) if rev else None
        debt_ratio = round(tl / te * 100, 4) if te else None
        roe = round(ni / te * 100, 4) if te else None

        # 전분기 대비 성장률
        prev = conn.execute("""
            SELECT revenue, operating_profit FROM financial_quarterly
            WHERE symbol=? AND report_period < ?
            ORDER BY report_period DESC LIMIT 1
        """, (sym, f["report_period"])).fetchone()

        rev_growth = None
        op_growth = None
        if prev:
            prev_rev = prev["revenue"] or 0
            prev_op = prev["operating_profit"] or 0
            if prev_rev and abs(prev_rev) > 0:
                rev_growth = round((rev - prev_rev) / abs(prev_rev) * 100, 4)
            if prev_op and abs(prev_op) > 0:
                op_growth = round((op - prev_op) / abs(prev_op) * 100, 4)

        try:
            conn.execute("""
                UPDATE daily_snapshot SET
                    revenue=?, operating_profit=?, net_income=?,
                    total_assets=?, total_liabilities=?, total_equity=?,
                    operating_margin=?, net_margin=?, debt_ratio=?, roe=?,
                    revenue_growth=?, op_growth=?
                WHERE trade_date=? AND symbol=?
            """, (
                rev, op, ni, ta, tl, te,
                op_margin, net_margin, debt_ratio, roe,
                rev_growth, op_growth,
                date, sym,
            ))
            updated += 1
        except Exception as e:
            print(f"[Finance] {sym} 재무파생 UPDATE 실패: {e}")

    conn.commit()
    print(f"[Finance] 재무 파생값 UPDATE 완료: {updated}종목 ({date})")
