#!/usr/bin/env python3
"""reports 테이블 name 오염 정정 스크립트.

사용:
    python3 scripts/fix_report_name_pollution.py              # dry-run (기본)
    python3 scripts/fix_report_name_pollution.py --apply      # 실제 커밋
    python3 scripts/fix_report_name_pollution.py --fix-mismatch          # full_text 불일치 7건 적용 dry-run
    python3 scripts/fix_report_name_pollution.py --fix-mismatch --apply  # full_text 불일치 7건 실제 커밋
    python3 scripts/fix_report_name_pollution.py --scan       # 잔여 불일치 스캔 (정정 없음)

작업 순서:
  1. 변경 대상 백업 → data/archive/reports_fix_backup_{label}_{YYYYMMDD}[-N].json
     (같은 날 동일 label로 재실행해도 파일 덮어쓰기 없음 — -2, -3 서픽스 부여)
  2. company 행 중 name != stock_master 정규명 UPDATE  (label='name_norm')
  3. id=15103 ticker/name 재배정 (042700→053690, 한화투자증권 오매핑)  (label='id15103')
  4. --fix-mismatch: full_text 불일치 7건 판정 결과 반영  (label='mismatch')
     - 2026-06-13 1차 적용 완료 → 현재 DB 기준 "변경 0건"이 정상(멱등)
     - iCloud 백업 DB에 pre-fix 원본 존재
  5. --scan 으로 잔여 확인

백업 소실 경위 (2026-06-13):
  1차 --apply 실행 시 _backup() label 파라미터가 없어 name_norm(7110행)과
  id15103(1행)이 동일 파일명을 공유, id15103 백업이 name_norm 백업을 덮어씀.
  결과적으로 남은 1차 실행 백업은 아래 두 파일뿐이며 _post_ 파일은 존재하지 않음:
    - data/archive/reports_name_fix_backup_20260613.json       (1행, id15103 pre-fix)
    - data/archive/reports_mismatch_fix_backup_20260613.json   (7행, mismatch pre-fix)
  name_norm 원본(7110행)은 1차 실행 덮어쓰기로 소실 — iCloud pre-fix stock.db로 복구 가능.
  신규 _backup() 스킴(reports_fix_backup_{label}_{stamp}.json)은 향후 재실행분부터 적용.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime

DATA_DIR = os.environ.get("DATA_DIR", "/Users/kreuzer/stock-bot/data")
DB_PATH = os.path.join(DATA_DIR, "stock.db")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
PDF_DIR = os.path.join(DATA_DIR, "report_pdfs")

# ─────────────────────────────────────────────────────────────────────────────
# full_text 불일치 7건 판정 결과 (2026-06-13 1차 적용 완료, 멱등)
# 재실행 시 현재 DB 상태와 비교해 변경 없으면 skip
# ─────────────────────────────────────────────────────────────────────────────
ADJUDICATED_MISMATCH_FIXES = [
    # (id, action, new_ticker, new_name, reason)
    (2208,  "DELETE", None,     None,       "full_text가 다른 종목 코드 혼재, 중복 존재 확인됨"),
    (6182,  "DELETE", None,     None,       "full_text가 다른 종목 코드 혼재, 중복 존재 확인됨"),
    (6797,  "DELETE", None,     None,       "full_text가 다른 종목 코드 혼재, 중복 존재 확인됨"),
    (4373,  "UPDATE", "004020", "현대제철", "full_text 내 004020이 실 대상"),
    (6104,  "UPDATE", "138930", "BNK금융지주", "full_text 내 138930이 실 대상"),
    (6691,  "UPDATE", "004020", "현대제철", "full_text 내 004020이 실 대상"),
    (6801,  "UPDATE", "112610", "씨에스윈드", "full_text 내 112610이 실 대상"),
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _load_stock_master(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT symbol, name FROM stock_master WHERE name IS NOT NULL AND name != ''"
    ).fetchall()
    return dict(rows)


def _backup(rows: list[tuple], label: str) -> str:
    """변경 대상 행을 JSON으로 백업.

    rows = list of (id, ticker, name, title, pdf_url, pdf_path)
    label = 백업 종류 식별자 (예: 'name_norm', 'id15103', 'mismatch')
    파일명: reports_fix_backup_{label}_{YYYYMMDD}[-N].json
    같은 날 동일 label로 재호출해도 덮어쓰지 않음 — -2, -3 서픽스 부여.
    """
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    base = f"reports_fix_backup_{label}_{stamp}"
    path = os.path.join(ARCHIVE_DIR, f"{base}.json")
    # 충돌 회피: 같은 이름 이미 존재하면 -2, -3 … 서픽스
    n = 2
    while os.path.exists(path):
        path = os.path.join(ARCHIVE_DIR, f"{base}-{n}.json")
        n += 1

    data = [
        {"id": r[0], "ticker": r[1], "name": r[2], "title": r[3],
         "pdf_url": r[4], "pdf_path": r[5]}
        for r in rows
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _collect_dirty_rows(conn: sqlite3.Connection, master: dict[str, str]) -> list[tuple]:
    """company 행 중 name이 stock_master 정규명과 다른 행 목록 반환."""
    rows = conn.execute(
        """SELECT id, ticker, name, title, pdf_url, pdf_path
           FROM reports
           WHERE category = 'company'"""
    ).fetchall()
    dirty = []
    for row in rows:
        rid, ticker, name, title, pdf_url, pdf_path = row
        if ticker in master and name != master[ticker]:
            dirty.append(row)
    return dirty


def _fix_name_pollution(conn: sqlite3.Connection, master: dict[str, str], apply: bool) -> int:
    """company 행 name 정규화. 적용 행수 반환."""
    dirty = _collect_dirty_rows(conn, master)
    if not dirty:
        print("  name 오염 대상 없음.")
        return 0

    print(f"  name 오염 대상: {len(dirty)}행")
    for row in dirty[:5]:
        print(f"    id={row[0]} ticker={row[1]} name={row[2]!r} → {master[row[1]]!r}")
    if len(dirty) > 5:
        print(f"    ... (처음 5건만 표시)")

    if not apply:
        return len(dirty)

    # 백업 (label 구분)
    backup_path = _backup(dirty, label="name_norm")
    print(f"  백업 완료: {backup_path}")

    # UPDATE (트랜잭션은 호출자에서 관리)
    for row in dirty:
        rid, ticker = row[0], row[1]
        conn.execute("UPDATE reports SET name=? WHERE id=?", (master[ticker], rid))

    print(f"  UPDATE 완료: {len(dirty)}행")
    return len(dirty)


def _fix_id_15103(conn: sqlite3.Connection, apply: bool) -> str:
    """id=15103 ticker 재배정 (042700 → 053690)."""
    row = conn.execute(
        "SELECT id, ticker, name, title, pdf_url, pdf_path FROM reports WHERE id=15103"
    ).fetchone()
    if row is None:
        print("  id=15103 행 없음 (이미 수정됨 또는 미존재).")
        return "not_found"

    rid, ticker, name, title, pdf_url, pdf_path = row
    print(f"  id=15103: ticker={ticker!r} name={name!r} title={title!r}")

    if ticker == "053690":
        print("  id=15103 이미 053690으로 설정됨.")
        return "already_fixed"

    # 053690 기존 중복 체크 (같은 date·source·ticker)
    dup = conn.execute(
        """SELECT id FROM reports
           WHERE date=(SELECT date FROM reports WHERE id=15103)
             AND source=(SELECT source FROM reports WHERE id=15103)
             AND ticker='053690'
           LIMIT 1"""
    ).fetchone()

    if not apply:
        if dup:
            print(f"  → dry-run: UNIQUE 충돌(id={dup[0]}) → DELETE 15103 예정")
            return "dry_run_delete"
        else:
            print(f"  → dry-run: UPDATE ticker=053690, name=한미글로벌 예정")
            # pdf_path 이동 여부 표시
            if pdf_path and "/042700/" in pdf_path:
                new_path = pdf_path.replace("/042700/", "/053690/")
                print(f"  → dry-run: pdf_path 이동 예정: {pdf_path} → {new_path}")
            return "dry_run_update"

    # 백업 (label 구분)
    backup_path = _backup([row], label="id15103")
    print(f"  백업 완료: {backup_path}")

    if dup:
        conn.execute("DELETE FROM reports WHERE id=15103")
        print(f"  UNIQUE 충돌(id={dup[0]}) → id=15103 DELETE 완료")
        return "deleted"
    else:
        # pdf_path 파일 이동
        new_pdf_path = pdf_path
        if pdf_path and "/042700/" in pdf_path:
            new_pdf_path = pdf_path.replace("/042700/", "/053690/")
            src = os.path.join(DATA_DIR, pdf_path.lstrip("/")) if not os.path.isabs(pdf_path) else pdf_path
            dst = os.path.join(DATA_DIR, new_pdf_path.lstrip("/")) if not os.path.isabs(new_pdf_path) else new_pdf_path
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
                print(f"  PDF 이동: {src} → {dst}")
            else:
                print(f"  PDF 파일 미존재(이동 건너뜀): {src}")

        conn.execute(
            "UPDATE reports SET ticker='053690', name='한미글로벌', pdf_path=? WHERE id=15103",
            (new_pdf_path,)
        )
        print(f"  id=15103 UPDATE 완료: ticker=053690, name=한미글로벌")
        return "updated"


def _fix_mismatch(conn: sqlite3.Connection, master: dict[str, str], apply: bool) -> int:
    """full_text 불일치 7건 판정 결과 적용 (ADJUDICATED_MISMATCH_FIXES).

    2026-06-13 1차 적용 완료 — 현재 DB 기준 "변경 0건"이 정상(멱등).
    iCloud 백업 DB에 pre-fix 원본 존재.
    """
    pending = []
    for fix_id, action, new_ticker, new_name, reason in ADJUDICATED_MISMATCH_FIXES:
        row = conn.execute(
            "SELECT id, ticker, name, title, pdf_url, pdf_path FROM reports WHERE id=?",
            (fix_id,)
        ).fetchone()

        if action == "DELETE":
            if row is None:
                print(f"  id={fix_id} DELETE: 이미 없음 (멱등)")
                continue
            # 중복 존재 확인 (DELETE 사전 조건)
            dup = conn.execute(
                """SELECT id FROM reports
                   WHERE date=(SELECT date FROM reports WHERE id=?)
                     AND source=(SELECT source FROM reports WHERE id=?)
                     AND ticker=(SELECT ticker FROM reports WHERE id=?)
                     AND id != ?
                   LIMIT 1""",
                (fix_id, fix_id, fix_id, fix_id)
            ).fetchone()
            pending.append((row, action, new_ticker, new_name, reason, dup))
            print(f"  id={fix_id} DELETE 예정 (이유: {reason}){' ← 중복id=' + str(dup[0]) if dup else ' ← 주의: 중복행 미확인'}")

        elif action == "UPDATE":
            if row is None:
                print(f"  id={fix_id} UPDATE: 행 없음 (이미 삭제됐거나 미존재)")
                continue
            cur_ticker = row[1]
            if cur_ticker == new_ticker:
                print(f"  id={fix_id} UPDATE: 이미 ticker={new_ticker} (멱등)")
                continue
            pending.append((row, action, new_ticker, new_name, reason, None))
            print(f"  id={fix_id} UPDATE 예정: ticker={cur_ticker!r} → {new_ticker!r} name={new_name!r}")

    if not pending:
        print("  full_text 불일치 정정 대상 없음 (멱등 — 이미 적용됨).")
        return 0

    print(f"  전체 적용 대상: {len(pending)}건")

    if not apply:
        return len(pending)

    # 백업
    rows_to_backup = [p[0] for p in pending]
    backup_path = _backup(rows_to_backup, label="mismatch")
    print(f"  백업 완료: {backup_path}")

    applied = 0
    for row, action, new_ticker, new_name, reason, dup in pending:
        fix_id = row[0]
        if action == "DELETE":
            conn.execute("DELETE FROM reports WHERE id=?", (fix_id,))
            print(f"  id={fix_id} DELETE 완료")
            applied += 1
        elif action == "UPDATE":
            conn.execute(
                "UPDATE reports SET ticker=?, name=? WHERE id=?",
                (new_ticker, new_name, fix_id)
            )
            print(f"  id={fix_id} UPDATE 완료: ticker={new_ticker}, name={new_name}")
            applied += 1

    print(f"  full_text 불일치 정정 완료: {applied}건")
    return applied


def _scan(conn: sqlite3.Connection, master: dict[str, str]):
    """잔여 name 불일치 + full_text 종목코드 불일치 스캔."""
    print("\n[SCAN] name vs stock_master 잔여 불일치:")
    dirty = _collect_dirty_rows(conn, master)
    if dirty:
        print(f"  잔여 {len(dirty)}건:")
        for row in dirty[:20]:
            print(f"    id={row[0]} ticker={row[1]} name={row[2]!r} → {master.get(row[1], '?')!r}")
    else:
        print("  없음 (정상)")

    print("\n[SCAN] full_text 종목코드 불일치 의심 (처음 50건):")
    pat = re.compile(r"\b(\d{6})\b")
    rows = conn.execute(
        """SELECT id, ticker, name, title, full_text
           FROM reports
           WHERE full_text IS NOT NULL AND full_text != ''
             AND category = 'company'
           LIMIT 5000"""
    ).fetchall()
    mismatches = []
    for rid, ticker, name, title, full_text in rows:
        snippet = (full_text or "")[:1000]
        codes = set(pat.findall(snippet))
        # 합성 티커(_IND_ 등) 제외
        if not re.match(r"^\d{6}$", ticker):
            continue
        if ticker not in codes and codes:
            # 날짜 패턴(YYYYMMDD) 제외 후에도 이질 코드가 있는지
            non_date = {c for c in codes if not re.match(r"^(19|20)\d{6}$", c)}
            if non_date:
                mismatches.append((rid, ticker, name, title, sorted(non_date)[:3]))

    if mismatches:
        for m in mismatches[:50]:
            print(f"    id={m[0]} ticker={m[1]} name={m[2]!r} title={repr(m[3])[:60]} codes={m[4]}")
        print(f"  총 {len(mismatches)}건 (처음 50건 표시)")
    else:
        print("  없음")


def main():
    parser = argparse.ArgumentParser(description="reports name 오염 정정")
    parser.add_argument("--apply", action="store_true", help="실제 커밋 (기본=dry-run)")
    parser.add_argument("--scan", action="store_true", help="잔여 불일치 스캔만")
    parser.add_argument("--fix-mismatch", action="store_true",
                        help="full_text 불일치 7건 판정 결과 적용 (2026-06-13 1차 적용 완료, 멱등)")
    args = parser.parse_args()

    print(f"DB: {DB_PATH}")
    conn = _connect()
    master = _load_stock_master(conn)
    print(f"stock_master 로드: {len(master)}종목")

    if args.scan:
        _scan(conn, master)
        conn.close()
        return

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode} ===\n")

    if args.fix_mismatch:
        print("[fix-mismatch] full_text 불일치 7건 판정 결과:")
        applied = _fix_mismatch(conn, master, apply=args.apply)
        if args.apply:
            conn.commit()
            print("\n=== COMMIT 완료 ===")
        else:
            print(f"\n=== DRY-RUN 완료 — --apply 로 실제 적용 ===")
        print(f"\n요약: full_text 불일치 정정={applied}건")
        conn.close()
        if args.apply:
            print("\n[SCAN] 사후 잔여 확인:")
            conn2 = _connect()
            master2 = _load_stock_master(conn2)
            _scan(conn2, master2)
            conn2.close()
        return

    print("[1] name 오염 정규화:")
    updated_count = _fix_name_pollution(conn, master, apply=args.apply)

    print("\n[2] id=15103 재배정:")
    result_15103 = _fix_id_15103(conn, apply=args.apply)

    if args.apply:
        conn.commit()
        print("\n=== COMMIT 완료 ===")
    else:
        print(f"\n=== DRY-RUN 완료 — --apply 로 실제 적용 ===")

    print(f"\n요약: name UPDATE 대상={updated_count}행, id=15103={result_15103}")

    conn.close()

    if args.apply:
        print("\n[SCAN] 사후 잔여 확인:")
        conn2 = _connect()
        master2 = _load_stock_master(conn2)
        _scan(conn2, master2)
        conn2.close()


if __name__ == "__main__":
    main()
