"""F/M/FCF Phase2-4 알파 메트릭 계산 엔진.

P3-5 박리: _parse_period, _build_period, _compute_ttm, _prev_yoy_period,
           _fs_source, _pick_net_income, _safe_div, _compute_fscore,
           _compute_mscore, _compute_fcf_metrics, _ensure_alpha_columns,
           _update_alpha_metrics, update_all_alpha_metrics,
           collect_shares_historical
"""

import asyncio
import sqlite3
import time
from datetime import datetime

from kis_api import _get_session
from ._config import KST
from ._db import _get_db

# DART 분당 1000건 제한 → 안전 마진 900/분 = 0.067초/콜
# collect_shares_historical 에서 사용. 패치 투명성: _BACKING에 등록되므로
# monkeypatch.setattr(db_collector, "_DART_INTERVAL", 0.0) 가 이 모듈에도 전파됨.
_DART_INTERVAL = 0.067

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# TTM 계산 엔진 (F/M/FCF Phase2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# Flow 항목 (분기별 "누적" 값 → 차분으로 단분기 산출 후 4분기 합산)
_TTM_FLOW_FIELDS = (
    "revenue", "operating_profit", "net_income", "net_income_parent",
    "cfo", "capex", "fcf", "depreciation", "sga",
    "cost_of_sales", "gross_profit",
)
# Stock 항목 (대차대조표: end_period 시점 값 그대로)
_TTM_STOCK_FIELDS = (
    "total_assets", "current_assets", "total_liab", "current_liab",
    "total_equity", "equity_parent", "receivables", "inventory",
    "shares_out",
    "fixed_assets", "fixed_liab",  # F/M-Score 에서 AQI/DEPI 근사용
)


def _parse_period(period: str) -> tuple[int, int] | None:
    """YYYYMM (예: 202412) → (year, quarter). quarter: 1/2/3/4."""
    if not period or len(period) != 6 or not period.isdigit():
        return None
    y = int(period[:4])
    m = int(period[4:])
    q_map = {3: 1, 6: 2, 9: 3, 12: 4}
    q = q_map.get(m)
    if q is None:
        return None
    return (y, q)


def _build_period(year: int, quarter: int) -> str:
    """(year, quarter) → 'YYYYMM' (quarter 1→03, 2→06, 3→09, 4→12)."""
    return f"{year}{quarter * 3:02d}"


def _compute_ttm(conn: sqlite3.Connection, ticker: str, end_period: str) -> dict:
    """TTM (Trailing Twelve Months) 재무 지표 계산.

    한국 DART 분기 보고서는 "당해 연도 누적" 값을 반환함:
      1분기(YYYY03) = 3개월 누적
      반기  (YYYY06) = 6개월 누적
      3분기(YYYY09) = 9개월 누적
      사업  (YYYY12) = 12개월 누적 (= 연간)

    따라서 단순 4분기 합산은 중복 계상됨.
    TTM 공식:
        Qn of year Y (n<4): cumulative(Qn,Y) + annual(Y-1) - cumulative(Qn,Y-1)
        Q4 of year Y      : annual(Y)  (그대로)

    Args:
        conn: SQLite 연결
        ticker: 종목코드
        end_period: 'YYYYMM' (기준 분기 말)

    Returns:
        dict {
          revenue, operating_profit, net_income, ...(flow),
          total_assets, current_assets, ..., shares_out (stock, end_period 시점 값),
          period_end: end_period,
          periods_used: [리스트],
          is_ttm_complete: bool  (True = flow 계산에 필요한 모든 분기 데이터 보유),
        }
        실패 시 {"period_end": end_period, "is_ttm_complete": False, 필드는 모두 None}.
    """
    parsed = _parse_period(end_period)
    flow_fields = list(_TTM_FLOW_FIELDS)
    stock_fields = list(_TTM_STOCK_FIELDS)

    # 기본 반환 템플릿 (전 필드 None)
    out: dict = {f: None for f in (*flow_fields, *stock_fields)}
    out["period_end"] = end_period
    out["periods_used"] = []
    out["is_ttm_complete"] = False

    if parsed is None:
        return out
    year, quarter = parsed

    # end_period row (Stock 필드 + flow 누적값)
    end_row = conn.execute(
        "SELECT * FROM financial_quarterly WHERE symbol=? AND report_period=?",
        (ticker, end_period),
    ).fetchone()
    if end_row is None:
        return out

    # Stock 필드: end_period 시점 값 그대로
    for f in stock_fields:
        try:
            out[f] = end_row[f]
        except (IndexError, KeyError):
            out[f] = None

    # TTM flow 계산
    if quarter == 4:
        # Q4 = 연간 (12개월 누적) = 그대로
        periods_used = [end_period]
        for f in flow_fields:
            try:
                out[f] = end_row[f]
            except (IndexError, KeyError):
                out[f] = None
        # 필수 핵심 필드가 하나라도 있으면 complete로 간주
        out["is_ttm_complete"] = any(out[f] is not None for f in flow_fields)
    else:
        # n<4: TTM = cum(Qn,Y) + annual(Y-1) - cum(Qn,Y-1)
        prev_annual_period = _build_period(year - 1, 4)
        prev_same_q_period = _build_period(year - 1, quarter)
        periods_used = [end_period, prev_annual_period, prev_same_q_period]

        prev_annual = conn.execute(
            "SELECT * FROM financial_quarterly WHERE symbol=? AND report_period=?",
            (ticker, prev_annual_period),
        ).fetchone()
        prev_same_q = conn.execute(
            "SELECT * FROM financial_quarterly WHERE symbol=? AND report_period=?",
            (ticker, prev_same_q_period),
        ).fetchone()

        all_present = prev_annual is not None and prev_same_q is not None
        out["is_ttm_complete"] = all_present

        if all_present:
            for f in flow_fields:
                try:
                    cur = end_row[f]
                    ann = prev_annual[f]
                    prev_q = prev_same_q[f]
                except (IndexError, KeyError):
                    out[f] = None
                    continue
                # 보수적: 3개 값 중 하나라도 NULL이면 TTM도 NULL
                if cur is None or ann is None or prev_q is None:
                    out[f] = None
                    continue
                out[f] = cur + ann - prev_q
        else:
            # 불완전: 그래도 end_row 값만이라도 채워둠 (참고용, is_ttm_complete=False 표시)
            for f in flow_fields:
                try:
                    out[f] = end_row[f]
                except (IndexError, KeyError):
                    out[f] = None

    out["periods_used"] = periods_used
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 발행주식수 소급 수집 (F/M/FCF Phase2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def collect_shares_historical(quarters_back: int = 12,
                                     tickers_limit: int | None = None) -> dict:
    """DART stockTotqySttus API로 보통주 발행주식수 N분기 소급.

    financial_quarterly.shares_out (주 단위) UPDATE.
    이미 값이 있어도 덮어씀 (최신 API 결과 우선).

    Args:
        quarters_back: 과거 몇 분기 수집 (기본 12 = 3년)
        tickers_limit: 테스트용 상한 (None=전종목)

    Returns:
        {"tickers", "quarters", "calls_made", "success", "updated", "duration_sec"}
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

    # corp_codes.json(3959) 우선, fallback dart_corp_map.json(211)
    try:
        from kis_api import load_corp_codes, get_dart_corp_map
        full_map = await load_corp_codes()
        corp_map = {tk: v["corp_code"] for tk, v in full_map.items()
                    if v.get("corp_code")}
        if not corp_map:
            legacy = await get_dart_corp_map({})
            corp_map = legacy if isinstance(legacy, dict) else {}
    except Exception as e:
        conn.close()
        return {"error": f"corp_map 로드 실패: {e}"}
    if not corp_map:
        conn.close()
        return {"error": "corp_map 비어 있음"}

    # 타겟 분기 (DART 공시 지연 ~45일)
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
    print(f"[SharesHist] corp_map {len(corp_map)}종목, 대상 {len(tickers)}×{len(targets)}={total_calls}콜")
    print(f"[SharesHist] 예상 {total_calls * _DART_INTERVAL / 60:.1f}분, "
          f"타겟 {targets[-1]}~{targets[0]}")

    from kis_api import dart_shares_outstanding

    success = 0
    updated = 0
    done = 0
    skipped_no_corp = 0
    start = time.time()

    session = _get_session()
    for ticker in tickers:
        corp_code = corp_map.get(ticker)
        if not corp_code:
            skipped_no_corp += 1
            done += len(targets)
            continue
        for (ty, tq) in targets:
            rp = _build_period(ty, tq)
            try:
                shares = await dart_shares_outstanding(
                    corp_code, ty, tq, session=session
                )
                if shares is not None and shares > 0:
                    success += 1
                    # row 없으면 생성 (collect_financial_historical 이후 shares만 채우는 케이스도 대응)
                    conn.execute(
                        "INSERT OR IGNORE INTO financial_quarterly "
                        "(symbol, report_period, collected_at) "
                        "VALUES (?, ?, datetime('now'))",
                        (ticker, rp),
                    )
                    cur = conn.execute(
                        "UPDATE financial_quarterly SET shares_out=? "
                        "WHERE symbol=? AND report_period=?",
                        (shares, ticker, rp),
                    )
                    if cur.rowcount > 0:
                        updated += 1
            except Exception:
                pass
            done += 1
            await asyncio.sleep(_DART_INTERVAL)
            if done % 500 == 0:
                conn.commit()
                print(f"[SharesHist] {done}/{total_calls} (성공 {success}, UPDATE {updated})")
    conn.commit()

    conn.close()
    duration = time.time() - start
    print(f"[SharesHist] 완료 — 성공 {success}/{total_calls}, "
          f"UPDATE {updated}, corp_map 스킵 {skipped_no_corp}, {duration:.1f}초")
    return {
        "tickers": len(tickers),
        "quarters": len(targets),
        "calls_made": total_calls,
        "success": success,
        "updated": updated,
        "skipped_no_corp": skipped_no_corp,
        "duration_sec": round(duration, 1),
        "target_range": f"{targets[-1]} ~ {targets[0]}",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# F/M/FCF Phase3 — 메트릭 계산 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━
#
# 공통 원칙:
#   * TTM 엔진(_compute_ttm) 기반. 현재 TTM vs 4분기 전 TTM (YoY).
#   * net_income_parent 우선, None일 시 net_income으로 fallback (IS 없는 KR 기업 대응).
#   * fs_source == 'OFS_HOLDCO' (순수 지주사)는 F/M-Score 스킵 (영업활동 없음).
#   * 개별 지표 계산 불가(NULL) 시 False 취급 금지 → None으로 표시. score는 True만 카운트.
#   * ZeroDivisionError / None 연산은 명시적 체크.
#
# 단위:
#   * money 필드: 억원 (financial_quarterly에서 수집 시 이미 억원)
#   * shares_out: 주
#   * market_cap: 억원 (daily_snapshot)
# ━━━━━━━━━━━━━━━━━━━━━━━━━


def _prev_yoy_period(end_period: str) -> str | None:
    """end_period 기준 4분기 전(전년 동분기) period 반환. 'YYYYMM' → 'YYYYMM'."""
    parsed = _parse_period(end_period)
    if parsed is None:
        return None
    y, q = parsed
    return _build_period(y - 1, q)


def _fs_source(conn: sqlite3.Connection, ticker: str, end_period: str) -> str | None:
    """financial_quarterly.fs_source 조회."""
    row = conn.execute(
        "SELECT fs_source FROM financial_quarterly "
        "WHERE symbol=? AND report_period=?",
        (ticker, end_period),
    ).fetchone()
    if row is None:
        return None
    try:
        return row["fs_source"]
    except (IndexError, KeyError):
        return None


def _pick_net_income(ttm: dict) -> float | None:
    """지배주주 귀속 우선, 없으면 전체 순이익 fallback."""
    v = ttm.get("net_income_parent")
    if v is not None:
        return v
    return ttm.get("net_income")


def _safe_div(num, den):
    """None/0 안전 나눗셈. 둘 중 하나라도 None이거나 den=0 → None."""
    if num is None or den is None:
        return None
    try:
        if den == 0:
            return None
        return num / den
    except (TypeError, ZeroDivisionError):
        return None


def _compute_fscore(conn: sqlite3.Connection, ticker: str, end_period: str) -> dict:
    """Piotroski F-Score (0~9점) TTM YoY 기반 계산.

    9개 이분법 지표:
      1. ROA > 0               (TTM 순이익 / 평균 총자산)
      2. CFO > 0               (TTM CFO)
      3. ΔROA > 0              (현재 TTM ROA vs 전년 TTM ROA)
      4. CFO > NI              (이익 품질)
      5. Δ장기부채비율 < 0       (장기부채/총자산 감소, 장기부채=total_liab-current_liab)
      6. Δ유동비율 > 0          (유동자산/유동부채 증가)
      7. 주식수 증가 없음        (shares_out 전년 이하)
      8. ΔGPM > 0              (매출총이익률 증가)
      9. Δ자산회전율 > 0        (TTM 매출/평균 총자산 증가)

    각 지표 데이터 부족 시 None (False 아님). score는 True만 카운트.
    is_complete=True 조건: 9개 모두 True/False.
    순수 지주사(OFS_HOLDCO)는 빈 결과 반환.

    Returns:
      {
        "score": 0~9 | None,
        "details": {지표명: True/False/None},
        "period": end_period,
        "yoy_period": 전년 동분기,
        "is_complete": bool,
        "skipped": None | "holdco" | "no_data",
      }
    """
    yoy_period = _prev_yoy_period(end_period)
    details = {
        "roa_pos": None,
        "cfo_pos": None,
        "delta_roa_pos": None,
        "cfo_gt_ni": None,
        "delta_ltdebt_neg": None,
        "delta_current_ratio_pos": None,
        "shares_not_increased": None,
        "delta_gpm_pos": None,
        "delta_asset_turnover_pos": None,
    }
    result = {
        "score": None,
        "details": details,
        "period": end_period,
        "yoy_period": yoy_period,
        "is_complete": False,
        "skipped": None,
    }

    # 지주사 스킵
    src = _fs_source(conn, ticker, end_period)
    if src == "OFS_HOLDCO":
        result["skipped"] = "holdco"
        return result

    if yoy_period is None:
        result["skipped"] = "no_data"
        return result

    cur = _compute_ttm(conn, ticker, end_period)
    prev = _compute_ttm(conn, ticker, yoy_period)

    # 최소 현재 분기 row는 존재해야 진행 (prev는 일부만 있어도 계산 가능)
    if not cur.get("period_end") or cur.get("total_assets") is None:
        result["skipped"] = "no_data"
        return result

    ni_cur = _pick_net_income(cur)
    ni_prev = _pick_net_income(prev)
    ta_cur = cur.get("total_assets")
    ta_prev = prev.get("total_assets")
    ca_cur = cur.get("current_assets")
    ca_prev = prev.get("current_assets")
    cl_cur = cur.get("current_liab")
    cl_prev = prev.get("current_liab")
    tl_cur = cur.get("total_liab")
    tl_prev = prev.get("total_liab")
    cfo_cur = cur.get("cfo")
    rev_cur = cur.get("revenue")
    rev_prev = prev.get("revenue")
    gp_cur = cur.get("gross_profit")
    gp_prev = prev.get("gross_profit")
    cos_cur = cur.get("cost_of_sales")
    cos_prev = prev.get("cost_of_sales")
    sh_cur = cur.get("shares_out")
    sh_prev = prev.get("shares_out")

    # 평균 자산 (prev 없으면 current 단일 사용)
    if ta_cur is not None and ta_prev is not None:
        avg_ta_cur = (ta_cur + ta_prev) / 2
    else:
        avg_ta_cur = ta_cur

    # 전년 ROA 계산용 평균 자산: prev + 2기전 자산이 이상적이나 없음 → prev 단일
    avg_ta_prev = ta_prev

    # 1. ROA > 0
    roa_cur = _safe_div(ni_cur, avg_ta_cur)
    if roa_cur is not None:
        details["roa_pos"] = roa_cur > 0

    # 2. CFO > 0
    if cfo_cur is not None:
        details["cfo_pos"] = cfo_cur > 0

    # 3. ΔROA > 0
    roa_prev = _safe_div(ni_prev, avg_ta_prev)
    if roa_cur is not None and roa_prev is not None:
        details["delta_roa_pos"] = roa_cur > roa_prev

    # 4. CFO > NI
    # 단위 일관성: DART 파서(kis_api.dart_quarterly_full)에서 모든 money 필드를
    # 수집 시점에 //1e8 처리 → 전부 "억원" 단위. net_income도 억원.
    if cfo_cur is not None and ni_cur is not None:
        details["cfo_gt_ni"] = cfo_cur > ni_cur

    # 5. Δ장기부채비율 < 0 (장기부채/총자산)
    #    장기부채 = total_liab - current_liab
    def _ltdebt_ratio(tl, cl, ta):
        if tl is None or cl is None or ta is None or ta == 0:
            return None
        return (tl - cl) / ta
    ltd_cur = _ltdebt_ratio(tl_cur, cl_cur, ta_cur)
    ltd_prev = _ltdebt_ratio(tl_prev, cl_prev, ta_prev)
    if ltd_cur is not None and ltd_prev is not None:
        details["delta_ltdebt_neg"] = ltd_cur < ltd_prev

    # 6. Δ유동비율 > 0
    curr_cur = _safe_div(ca_cur, cl_cur)
    curr_prev = _safe_div(ca_prev, cl_prev)
    if curr_cur is not None and curr_prev is not None:
        details["delta_current_ratio_pos"] = curr_cur > curr_prev

    # 7. 주식수 증가 없음
    if sh_cur is not None and sh_prev is not None:
        details["shares_not_increased"] = sh_cur <= sh_prev

    # 8. ΔGPM > 0  — GPM = gross_profit / revenue. 없으면 (revenue - cost_of_sales)/revenue
    def _gpm(gp, cos, rev):
        if rev is None or rev == 0:
            return None
        if gp is not None:
            return gp / rev
        if cos is not None:
            return (rev - cos) / rev
        return None
    gpm_cur = _gpm(gp_cur, cos_cur, rev_cur)
    gpm_prev = _gpm(gp_prev, cos_prev, rev_prev)
    if gpm_cur is not None and gpm_prev is not None:
        details["delta_gpm_pos"] = gpm_cur > gpm_prev

    # 9. Δ자산회전율 > 0
    at_cur = _safe_div(rev_cur, avg_ta_cur)
    at_prev = _safe_div(rev_prev, avg_ta_prev)
    if at_cur is not None and at_prev is not None:
        details["delta_asset_turnover_pos"] = at_cur > at_prev

    # 집계
    score = sum(1 for v in details.values() if v is True)
    is_complete = all(v is not None for v in details.values())
    result["score"] = score
    result["is_complete"] = is_complete
    return result


def _compute_mscore(conn: sqlite3.Connection, ticker: str, end_period: str) -> dict:
    """Beneish M-Score (earnings manipulation detection).

    공식: M = -4.84 + 0.92·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI
              + 0.115·DEPI - 0.172·SGAI + 4.679·TATA - 0.327·LVGI

    임계값:
      M > -1.78        → "high"
      -2.22 < M ≤ -1.78 → "moderate"
      M ≤ -2.22        → "low"

    주의 — 컬럼 부재에 따른 근사:
      * PPE (유형자산) 컬럼 없음 → PPE ≈ total_assets - current_assets (비유동자산).
        단, 이 근사로는 AQI가 항상 0이 되어버림 (1 - (CA+(TA-CA))/TA = 0).
        → AQI 는 fixed_assets(고정자산, 비유동자산) 대신 inventory 기반 근사.
        여기선 AQI 변수를 제외 대신 TA 대비 receivables 변화 비율로 대체 근사.
      * Cash, CurrDebt 컬럼 없음 → TATA = (operating_profit - cfo) / total_assets 근사
        (오퍼레이팅 발생액 = OI - CFO의 전통적 근사식)

    Returns:
      {
        "mscore": float | None,
        "manipulation_risk": "high"/"moderate"/"low"/None,
        "variables": {DSRI, GMI, AQI, SGI, DEPI, SGAI, LVGI, TATA},
        "period": end_period,
        "yoy_period": 전년 동분기,
        "is_complete": bool,
        "skipped": None | "holdco" | "no_data",
      }
    """
    yoy_period = _prev_yoy_period(end_period)
    variables = {
        "DSRI": None, "GMI": None, "AQI": None, "SGI": None,
        "DEPI": None, "SGAI": None, "LVGI": None, "TATA": None,
    }
    result = {
        "mscore": None,
        "manipulation_risk": None,
        "variables": variables,
        "period": end_period,
        "yoy_period": yoy_period,
        "is_complete": False,
        "skipped": None,
    }

    src = _fs_source(conn, ticker, end_period)
    if src == "OFS_HOLDCO":
        result["skipped"] = "holdco"
        return result

    if yoy_period is None:
        result["skipped"] = "no_data"
        return result

    cur = _compute_ttm(conn, ticker, end_period)
    prev = _compute_ttm(conn, ticker, yoy_period)

    if cur.get("total_assets") is None:
        result["skipped"] = "no_data"
        return result

    # 필드 추출
    rev_c = cur.get("revenue")
    rev_p = prev.get("revenue")
    ar_c = cur.get("receivables")
    ar_p = prev.get("receivables")
    gp_c = cur.get("gross_profit")
    gp_p = prev.get("gross_profit")
    cos_c = cur.get("cost_of_sales")
    cos_p = prev.get("cost_of_sales")
    ca_c = cur.get("current_assets")
    ca_p = prev.get("current_assets")
    ta_c = cur.get("total_assets")
    ta_p = prev.get("total_assets")
    cl_c = cur.get("current_liab")
    cl_p = prev.get("current_liab")
    tl_c = cur.get("total_liab")
    tl_p = prev.get("total_liab")
    sga_c = cur.get("sga")  # 원 단위
    sga_p = prev.get("sga")
    dep_c = cur.get("depreciation")  # 원 단위
    dep_p = prev.get("depreciation")
    cfo_c = cur.get("cfo")  # 원 단위
    op_c = cur.get("operating_profit")  # 억원 단위

    # PPE 근사 (비유동자산 = total_assets - current_assets)
    def _ppe(ta, ca):
        if ta is None or ca is None:
            return None
        return ta - ca
    ppe_c = _ppe(ta_c, ca_c)
    ppe_p = _ppe(ta_p, ca_p)

    # 1. DSRI = (AR_t/Rev_t) / (AR_t-1/Rev_t-1)
    arr_c = _safe_div(ar_c, rev_c)
    arr_p = _safe_div(ar_p, rev_p)
    variables["DSRI"] = _safe_div(arr_c, arr_p)

    # 2. GMI = GM_t-1 / GM_t   (GM = gross_profit / revenue)
    def _gm(gp, cos, rev):
        if rev is None or rev == 0:
            return None
        if gp is not None:
            return gp / rev
        if cos is not None:
            return (rev - cos) / rev
        return None
    gm_c = _gm(gp_c, cos_c, rev_c)
    gm_p = _gm(gp_p, cos_p, rev_p)
    variables["GMI"] = _safe_div(gm_p, gm_c)

    # 3. AQI — 원공식 = (1 - (CA+PPE)/TA)_t / (...)_t-1
    # 우리 DB에는 PPE 컬럼 없음. total_assets - current_assets는 비유동자산
    # 전체(=PPE+무형+투자자산)이므로 "1 - (CA+비유동)/TA = 0" 으로 항상 0이 됨.
    # → 실용적 근사: fixed_assets(비유동자산) 있으면 PPE ≈ 0.5 * fixed_assets
    #   (제조업 평균: 유형자산이 비유동의 ~50%). 더 정밀한 대체는 Phase 후속에서.
    fa_c = cur.get("fixed_assets")
    fa_p = prev.get("fixed_assets")
    def _aqi_ratio(ca, fa, ta):
        if ca is None or fa is None or ta is None or ta == 0:
            return None
        ppe_approx = fa * 0.5  # 제조업 평균 가정
        return 1 - (ca + ppe_approx) / ta
    aqi_c = _aqi_ratio(ca_c, fa_c, ta_c)
    aqi_p = _aqi_ratio(ca_p, fa_p, ta_p)
    variables["AQI"] = _safe_div(aqi_c, aqi_p)

    # 4. SGI = Rev_t / Rev_t-1
    variables["SGI"] = _safe_div(rev_c, rev_p)

    # 5. DEPI = (Dep_t-1/(Dep_t-1+PPE_t-1)) / (Dep_t/(Dep_t+PPE_t))
    # 단위 일관성: DART 파서에서 모든 money 필드 //1e8 처리됐으므로
    # dep/depreciation 도 억원. PPE 근사 = fixed_assets * 0.5 (AQI와 동일).
    def _depi_ratio(dep, fa):
        if dep is None or fa is None:
            return None
        ppe_approx = fa * 0.5
        total = dep + ppe_approx
        if total == 0:
            return None
        return dep / total
    depi_c = _depi_ratio(dep_c, fa_c)
    depi_p = _depi_ratio(dep_p, fa_p)
    variables["DEPI"] = _safe_div(depi_p, depi_c)

    # 6. SGAI = (SGA/Rev)_t / (SGA/Rev)_t-1   — sga, rev 모두 억원
    def _sga_ratio(sga, rev):
        if sga is None or rev is None or rev == 0:
            return None
        return sga / rev
    sgar_c = _sga_ratio(sga_c, rev_c)
    sgar_p = _sga_ratio(sga_p, rev_p)
    variables["SGAI"] = _safe_div(sgar_c, sgar_p)

    # 7. LVGI = ((CL+LTD)/TA)_t / (...)_t-1
    # CL+LTD = total_liab (CL + (TL-CL) = TL) 로 근사
    lvgi_c = _safe_div(tl_c, ta_c)
    lvgi_p = _safe_div(tl_p, ta_p)
    variables["LVGI"] = _safe_div(lvgi_c, lvgi_p)

    # 8. TATA ≈ (operating_profit - CFO) / total_assets  — 발생액 근사
    # op/cfo/ta 모두 억원
    if op_c is not None and cfo_c is not None and ta_c is not None and ta_c != 0:
        variables["TATA"] = (op_c - cfo_c) / ta_c

    # 최종 M-Score
    # 5/9 fix: TATA 만 None 허용 partial 7-variable 계산 (CFS 결손 종목 660+)
    # 학습 #28 후속 — Phase 4 launch 이후 mscore 100% NULL 원인
    core_keys = ("DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "LVGI")
    if all(variables[k] is not None for k in core_keys):
        if variables["TATA"] is not None:
            m = (-4.84
                 + 0.92 * variables["DSRI"]
                 + 0.528 * variables["GMI"]
                 + 0.404 * variables["AQI"]
                 + 0.892 * variables["SGI"]
                 + 0.115 * variables["DEPI"]
                 - 0.172 * variables["SGAI"]
                 + 4.679 * variables["TATA"]
                 - 0.327 * variables["LVGI"])
            result["is_complete"] = True
        else:
            # TATA 결손 partial 계산 - 4.679*TATA term 제외 (정확도 약간 낮으나 sufficient signal)
            m = (-4.84
                 + 0.92 * variables["DSRI"]
                 + 0.528 * variables["GMI"]
                 + 0.404 * variables["AQI"]
                 + 0.892 * variables["SGI"]
                 + 0.115 * variables["DEPI"]
                 - 0.172 * variables["SGAI"]
                 - 0.327 * variables["LVGI"])
            result["is_complete"] = False
            result["partial_reason"] = "TATA_missing"
        result["mscore"] = m
        if m > -1.78:
            result["manipulation_risk"] = "high"
        elif m > -2.22:
            result["manipulation_risk"] = "moderate"
        else:
            result["manipulation_risk"] = "low"
    return result


def _compute_fcf_metrics(conn: sqlite3.Connection, ticker: str, end_period: str,
                         market_cap: float | None = None) -> dict:
    """FCF 기반 3종 지표 TTM 기반.

    반환 단위:
      * fcf_ttm: 억원
      * fcf_to_assets, fcf_yield_ev, fcf_conversion: % (예: 5.3 = 5.3%)

    주의 — 단순화:
      * EV ≈ market_cap + total_liab (현금 컬럼 없어 cash 차감 생략)
      * FCF 전환율 = fcf / net_income. 순이익 ≤ 0 이면 None (의미 없음)
      * 단위: DART 파서가 수집 시 모든 money 필드를 //1e8 처리하므로
        financial_quarterly 의 fcf/cfo/depreciation 모두 "억원". market_cap도 억원.

    Returns:
      {
        "fcf_ttm": float | None (억원),
        "fcf_to_assets": float | None (%),
        "fcf_yield_ev": float | None (%),
        "fcf_conversion": float | None (%),
        "period": end_period,
        "is_complete": bool,
      }
    """
    result = {
        "fcf_ttm": None,
        "fcf_to_assets": None,
        "fcf_yield_ev": None,
        "fcf_conversion": None,
        "period": end_period,
        "is_complete": False,
    }

    ttm = _compute_ttm(conn, ticker, end_period)
    if ttm.get("period_end") is None:
        return result

    fcf = ttm.get("fcf")
    ta = ttm.get("total_assets")
    tl = ttm.get("total_liab")
    ni = _pick_net_income(ttm)

    # fcf 는 억원 단위 (DART 파서에서 //1e8 처리됨)
    if fcf is None:
        return result
    result["fcf_ttm"] = float(fcf)

    # FCF / 총자산
    if ta is not None and ta > 0:
        result["fcf_to_assets"] = (fcf / ta) * 100

    # FCF / EV
    if market_cap is not None and market_cap > 0 and tl is not None:
        ev = market_cap + tl
        if ev > 0:
            result["fcf_yield_ev"] = (fcf / ev) * 100

    # FCF / 순이익 (순이익>0 일 때만)
    if ni is not None and ni > 0:
        result["fcf_conversion"] = (fcf / ni) * 100

    # is_complete: 3개 모두 계산됐을 때
    core = (result["fcf_to_assets"], result["fcf_yield_ev"], result["fcf_conversion"])
    result["is_complete"] = all(v is not None for v in core)
    return result


def _ensure_alpha_columns(conn: sqlite3.Connection):
    """daily_snapshot 에 F/M/FCF 5컬럼 존재 보장. 없으면 ALTER ADD."""
    for sql in (
        "ALTER TABLE daily_snapshot ADD COLUMN fscore INTEGER",
        "ALTER TABLE daily_snapshot ADD COLUMN mscore REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_to_assets REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_yield_ev REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_conversion REAL",
    ):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass


def _update_alpha_metrics(conn: sqlite3.Connection, ticker: str, end_period: str,
                          market_cap: float | None = None,
                          trade_date: str | None = None) -> bool:
    """F-Score + M-Score + FCF 계산 후 daily_snapshot(trade_date, symbol)에 UPDATE.

    Args:
        conn: SQLite 연결
        ticker: 종목코드
        end_period: 'YYYYMM' 기준 분기말 (재무 데이터 기준)
        market_cap: 억원 단위. 없으면 daily_snapshot 에서 조회 시도.
        trade_date: 'YYYYMMDD'. 없으면 daily_snapshot 최신 row 사용.

    Returns:
        True = UPDATE 발생, False = 해당 row 없음 or 데이터 없음.
    """
    _ensure_alpha_columns(conn)

    # trade_date 결정
    if trade_date is None:
        row = conn.execute(
            "SELECT trade_date, market_cap FROM daily_snapshot "
            "WHERE symbol=? ORDER BY trade_date DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        if row is None:
            return False
        trade_date = row["trade_date"]
        if market_cap is None:
            market_cap = row["market_cap"] if row["market_cap"] else None

    # market_cap 인자 없으면 해당 날짜 row에서 조회
    if market_cap is None:
        row = conn.execute(
            "SELECT market_cap FROM daily_snapshot WHERE trade_date=? AND symbol=?",
            (trade_date, ticker),
        ).fetchone()
        if row and row["market_cap"]:
            market_cap = row["market_cap"]

    fs = _compute_fscore(conn, ticker, end_period)
    ms = _compute_mscore(conn, ticker, end_period)
    fcf = _compute_fcf_metrics(conn, ticker, end_period, market_cap=market_cap)

    cur = conn.execute(
        "UPDATE daily_snapshot SET fscore=?, mscore=?, "
        "fcf_to_assets=?, fcf_yield_ev=?, fcf_conversion=? "
        "WHERE trade_date=? AND symbol=?",
        (
            fs.get("score"),
            ms.get("mscore"),
            fcf.get("fcf_to_assets"),
            fcf.get("fcf_yield_ev"),
            fcf.get("fcf_conversion"),
            trade_date, ticker,
        ),
    )
    return cur.rowcount > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# F/M/FCF 전종목 일괄 업데이트 (collect_daily 훅)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def update_all_alpha_metrics(end_period: str | None = None,
                              trade_date: str | None = None) -> dict:
    """전종목 F-Score/M-Score/FCF 계산 후 daily_snapshot 5컬럼 UPDATE.

    Args:
        end_period: 재무 기준 분기 (YYYYMM). None이면 financial_quarterly에서
                    fs_source IS NOT NULL 중 최신 report_period 자동 선택.
        trade_date: daily_snapshot 대상 일자 (YYYYMMDD). None이면 MAX(trade_date).

    Returns:
        {"tickers": N, "success": S, "fscore_filled": F, "mscore_filled": M,
         "fcf_filled": FC, "duration_sec": T, "end_period": ..., "trade_date": ...}
    """
    start = datetime.now()
    conn = _get_db()
    try:
        _ensure_alpha_columns(conn)

        # end_period 자동 결정 (5/8 fix — 분기 피크 시즌 분산 대응):
        # 이전: count>=500 단일 분기. 1Q26 공시 시즌엔 202512(485) + 202603(19) 분산 → 둘 다 미통과 → MAX(202603, 19종목)만 채움
        # 변경: end_period=None 이면 종목별 가용 최신 분기 자동 선택 (per-ticker mode)
        # end_period 명시 호출은 기존 동작 그대로 (단일 분기 모드).
        per_ticker_mode = (end_period is None)
        if end_period is None:
            # per-ticker: 일단 stub. 실제 분기는 SELECT 쿼리에서 각 ticker별 MAX 사용
            # 기존 코드 호환을 위해 표시용으로 fs_source 있는 분기 중 가장 종목 많은 것 반영
            row = conn.execute(
                "SELECT report_period, COUNT(*) c FROM financial_quarterly "
                "WHERE fs_source IS NOT NULL "
                "GROUP BY report_period ORDER BY c DESC LIMIT 1"
            ).fetchone()
            end_period = row["report_period"] if row else None
        if not end_period:
            conn.close()
            return {"error": "end_period 확보 실패 (financial_quarterly 비어있음)",
                    "tickers": 0, "success": 0}

        # trade_date 자동 결정
        if trade_date is None:
            row = conn.execute(
                "SELECT MAX(trade_date) AS t FROM daily_snapshot"
            ).fetchone()
            trade_date = row["t"] if row and row["t"] else None
        if not trade_date:
            conn.close()
            return {"error": "trade_date 확보 실패 (daily_snapshot 비어있음)",
                    "tickers": 0, "success": 0}

        # 대상 종목: fs_source 있는 재무 + 해당 trade_date에 daily_snapshot row 존재
        # 5/8 fix: per_ticker_mode=True 면 종목별 가용 최신 분기 사용 (분기 피크 시즌 분산 대응)
        if per_ticker_mode:
            rows = conn.execute(
                "SELECT fq.symbol AS ticker, fq.report_period AS period, "
                "       ds.market_cap AS market_cap "
                "FROM financial_quarterly fq "
                "JOIN daily_snapshot ds ON ds.symbol=fq.symbol "
                "INNER JOIN ("
                "  SELECT symbol, MAX(report_period) AS max_p "
                "  FROM financial_quarterly WHERE fs_source IS NOT NULL "
                "  GROUP BY symbol"
                ") latest ON fq.symbol=latest.symbol AND fq.report_period=latest.max_p "
                "WHERE fq.fs_source IS NOT NULL AND ds.trade_date=?",
                (trade_date,),
            ).fetchall()
        else:
            # 명시 분기 호출 (기존 동작 그대로)
            rows = conn.execute(
                "SELECT fq.symbol AS ticker, fq.report_period AS period, "
                "       ds.market_cap AS market_cap "
                "FROM financial_quarterly fq "
                "JOIN daily_snapshot ds ON ds.symbol=fq.symbol "
                "WHERE fq.report_period=? AND fq.fs_source IS NOT NULL "
                "AND ds.trade_date=?",
                (end_period, trade_date),
            ).fetchall()

        tickers_total = len(rows)
        success = 0
        fscore_filled = 0
        mscore_filled = 0
        fcf_filled = 0
        errors = 0

        print(f"[AlphaMetrics] 시작 — end_period={end_period} "
              f"trade_date={trade_date} 대상 {tickers_total}종목")

        for r in rows:
            ticker = r["ticker"]
            mcap = r["market_cap"] if r["market_cap"] else None
            # 5/8 fix: per_ticker_mode 면 종목별 최신 분기 사용
            ticker_period = r["period"] if per_ticker_mode else end_period
            # database is locked 대비 최대 3회 재시도 (0.5s 간격)
            attempt = 0
            while True:
                try:
                    ok = _update_alpha_metrics(
                        conn, ticker, ticker_period,
                        market_cap=mcap, trade_date=trade_date,
                    )
                    if ok:
                        success += 1
                    break
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < 2:
                        attempt += 1
                        import time as _t
                        _t.sleep(0.5)
                        continue
                    errors += 1
                    if errors <= 5:
                        print(f"[AlphaMetrics] {ticker} 실패(lock): {e}")
                    break
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        print(f"[AlphaMetrics] {ticker} 실패: {e}")
                    break

        conn.commit()

        # 채움 수 집계 (WHERE IS NOT NULL count)
        fscore_filled = conn.execute(
            "SELECT COUNT(*) AS c FROM daily_snapshot "
            "WHERE trade_date=? AND fscore IS NOT NULL",
            (trade_date,),
        ).fetchone()["c"]
        mscore_filled = conn.execute(
            "SELECT COUNT(*) AS c FROM daily_snapshot "
            "WHERE trade_date=? AND mscore IS NOT NULL",
            (trade_date,),
        ).fetchone()["c"]
        fcf_filled = conn.execute(
            "SELECT COUNT(*) AS c FROM daily_snapshot "
            "WHERE trade_date=? AND fcf_to_assets IS NOT NULL",
            (trade_date,),
        ).fetchone()["c"]

        duration = (datetime.now() - start).total_seconds()
        print(f"[AlphaMetrics] 완료 — success={success}/{tickers_total} "
              f"fscore={fscore_filled} mscore={mscore_filled} "
              f"fcf={fcf_filled} ({duration:.1f}s)")

        return {
            "tickers": tickers_total,
            "success": success,
            "fscore_filled": fscore_filled,
            "mscore_filled": mscore_filled,
            "fcf_filled": fcf_filled,
            "duration_sec": round(duration, 1),
            "end_period": end_period,
            "trade_date": trade_date,
            "errors": errors,
        }
    finally:
        conn.close()

