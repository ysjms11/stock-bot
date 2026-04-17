"""F/M/FCF Phase2 — TTM 엔진 간이 검증 (실행 선택적).

실행 방법:
    python test_ttm.py

주의: DART 12분기 소급 수집(collect_financial_historical)이 완료된 후 실행.
수집 전이면 "데이터 없음"으로 출력됨. DB 경로는 DATA_DIR/stock.db 사용.

기대 결과 (삼성전자 005930):
  1) TTM(202412): Q4=사업보고서 = 연간값 그대로. is_ttm_complete=True.
     revenue ~ 300조원 (2024년 기준). operating_profit, net_income도 연간 일치.
  2) TTM(202509): 202509(9M) + 202412(12M) - 202409(9M).
     연간값에 가까운 규모. is_ttm_complete=True (세 분기 다 있다면).
  3) shares_out: end_period(여기선 202412) 시점 값 ≈ 5,919,637,922주 (보통주).
"""
import os
import sys
import sqlite3

# 프로젝트 루트 = 이 파일 위치
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from db_collector import _compute_ttm, _get_db  # noqa: E402


def _fmt_money(v, unit="억원"):
    """억원 단위 값을 읽기 쉽게 포맷팅."""
    if v is None:
        return "None"
    try:
        if abs(v) >= 10_000:
            return f"{v/10_000:,.1f}조{unit[-2:]}"  # "조원"
        return f"{v:,} {unit}"
    except Exception:
        return str(v)


def _fmt_shares(v):
    if v is None:
        return "None"
    try:
        return f"{v:,}주"
    except Exception:
        return str(v)


def _dump(title: str, ttm: dict):
    print(f"\n── {title} ──")
    print(f"  period_end       : {ttm.get('period_end')}")
    print(f"  periods_used     : {ttm.get('periods_used')}")
    print(f"  is_ttm_complete  : {ttm.get('is_ttm_complete')}")
    print(f"  [Flow]")
    for k in ("revenue", "operating_profit", "net_income",
              "net_income_parent", "cfo", "capex", "fcf",
              "depreciation", "sga", "cost_of_sales", "gross_profit"):
        print(f"    {k:<18}: {_fmt_money(ttm.get(k))}")
    print(f"  [Stock]")
    for k in ("total_assets", "current_assets", "total_liab",
              "current_liab", "total_equity", "equity_parent",
              "receivables", "inventory"):
        print(f"    {k:<18}: {_fmt_money(ttm.get(k))}")
    print(f"    shares_out        : {_fmt_shares(ttm.get('shares_out'))}")


def main():
    conn = _get_db()
    ticker = "005930"  # 삼성전자

    # DB 내 데이터 확인
    rows = conn.execute(
        "SELECT report_period FROM financial_quarterly "
        "WHERE symbol=? ORDER BY report_period DESC",
        (ticker,),
    ).fetchall()
    periods = [r["report_period"] for r in rows]
    print(f"[test_ttm] {ticker} DB 보유 분기 ({len(periods)}): {periods[:12]}")

    if not periods:
        print("[test_ttm] financial_quarterly 데이터 없음. "
              "collect_financial_historical 완료 후 재실행.")
        conn.close()
        return

    # 케이스 1: 연간 (Q4)
    ttm_2024 = _compute_ttm(conn, ticker, "202412")
    _dump("TTM(202412) = 2024 연간 (Q4=그대로)", ttm_2024)

    # 케이스 2: 3분기 (TTM)
    ttm_202509 = _compute_ttm(conn, ticker, "202509")
    _dump("TTM(202509) = 202509 + 202412 - 202409", ttm_202509)

    # 케이스 3: 잘못된 입력
    ttm_bad = _compute_ttm(conn, ticker, "202413")
    print(f"\n── 잘못된 end_period('202413') ──")
    print(f"  is_ttm_complete: {ttm_bad.get('is_ttm_complete')} (False 예상)")
    print(f"  revenue: {ttm_bad.get('revenue')} (None 예상)")

    # 검증 힌트: Q4 TTM과 연간 일치?
    print(f"\n[검증] TTM(Q4)의 revenue가 financial_quarterly.revenue(202412)와 같아야 함.")
    row = conn.execute(
        "SELECT revenue FROM financial_quarterly WHERE symbol=? AND report_period=?",
        (ticker, "202412"),
    ).fetchone()
    if row:
        raw = row["revenue"]
        print(f"  DB raw(202412).revenue = {_fmt_money(raw)}")
        print(f"  TTM(202412).revenue    = {_fmt_money(ttm_2024.get('revenue'))}")
        match = (raw == ttm_2024.get("revenue"))
        print(f"  일치 여부: {'OK' if match else 'MISMATCH'}")

    conn.close()


if __name__ == "__main__":
    main()
