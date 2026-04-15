#!/usr/bin/env python3
"""워치리스트 단일화 마이그레이션 (1단계).

watchlist.json + us_watchlist.json → watchalert.json 통합.

통합 스키마:
  {
    "<ticker>": {
      "name": str, "market": "KR"|"US",
      "buy_price": float (0=단순워치, >0=매수감시),
      "qty": int, "memo": str, "grade": str|None,
      "created_at": "YYYY-MM-DD", "updated_at": "YYYY-MM-DD"
    }
  }

충돌 규칙: watchalert 기존 엔트리 우선(보존). 없으면 추가(buy_price=0, grade=null).
원본은 `.bak` 리네임.

Usage:
  python3 scripts/migrate_watchlist.py --dry-run   # 검증
  python3 scripts/migrate_watchlist.py             # 실행
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

# kis_api 전체 import는 dotenv 등 무거운 의존성 때문에 피함.
# 파일 경로/헬퍼 최소 복제.
_DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
WATCHLIST_FILE    = f"{_DATA_DIR}/watchlist.json"
US_WATCHLIST_FILE = f"{_DATA_DIR}/us_watchlist.json"
WATCHALERT_FILE   = f"{_DATA_DIR}/watchalert.json"


def _is_us_ticker(ticker: str) -> bool:
    return bool(ticker) and ticker.replace(".", "").replace("-", "").isalpha()


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _market_of(ticker: str, entry: dict | None = None) -> str:
    if entry and entry.get("market") in ("KR", "US"):
        return entry["market"]
    return "US" if _is_us_ticker(ticker) else "KR"


def migrate(dry_run: bool = False) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    wa = _load(WATCHALERT_FILE)
    kr = _load(WATCHLIST_FILE)       # {ticker: name}
    us = _load(US_WATCHLIST_FILE)    # {ticker: {name, qty}}

    before = len(wa)
    added_kr, added_us = [], []
    skipped_conflict = []
    enriched = []  # 기존 엔트리에 market 누락 보강

    # 기존 watchalert 엔트리: market 필드 없으면 보강
    for t, v in wa.items():
        if not isinstance(v, dict):
            continue
        if not v.get("market"):
            v["market"] = _market_of(t, v)
            enriched.append(t)

    # KR 병합
    for t, name in (kr or {}).items():
        if t in wa:
            skipped_conflict.append(t)
            continue
        mk = _market_of(t)
        entry = {
            "name": name if isinstance(name, str) else str(name),
            "market": mk,
            "buy_price": 0.0,
            "qty": 0,
            "memo": "",
            "grade": None,
            "created_at": today,
            "updated_at": today,
        }
        wa[t] = entry
        (added_us if mk == "US" else added_kr).append(t)

    # US 병합 ({ticker: {name, qty}})
    for t, meta in (us or {}).items():
        if t in wa:
            skipped_conflict.append(t)
            continue
        if isinstance(meta, dict):
            nm = meta.get("name") or t
            qt = int(meta.get("qty") or 0)
        else:
            nm, qt = str(meta), 0
        entry = {
            "name": nm, "market": "US",
            "buy_price": 0.0, "qty": qt,
            "memo": "", "grade": None,
            "created_at": today, "updated_at": today,
        }
        wa[t] = entry
        added_us.append(t)

    report = {
        "before": before,
        "after": len(wa),
        "added_kr": added_kr,
        "added_us": added_us,
        "skipped_conflict": skipped_conflict,
        "enriched_market": enriched,
        "legacy_kr_count": len(kr or {}),
        "legacy_us_count": len(us or {}),
    }

    if dry_run:
        return report

    # 실제 저장
    with open(WATCHALERT_FILE, "w", encoding="utf-8") as f:
        json.dump(wa, f, ensure_ascii=False, indent=2)

    # 원본 .bak
    for p in (WATCHLIST_FILE, US_WATCHLIST_FILE):
        if os.path.exists(p):
            bak = p + ".bak"
            shutil.move(p, bak)
            report.setdefault("backed_up", []).append(bak)

    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="변경 없이 검증만")
    args = ap.parse_args()

    r = migrate(dry_run=args.dry_run)
    print("━" * 50)
    print(f"모드        : {'DRY-RUN' if args.dry_run else 'EXECUTE'}")
    print(f"watchalert  : {r['before']} → {r['after']}")
    print(f"legacy KR   : {r['legacy_kr_count']}건")
    print(f"legacy US   : {r['legacy_us_count']}건")
    print(f"추가 KR     : {len(r['added_kr'])}건  {r['added_kr'][:10]}")
    print(f"추가 US     : {len(r['added_us'])}건  {r['added_us'][:10]}")
    print(f"충돌 스킵   : {len(r['skipped_conflict'])}건  {r['skipped_conflict'][:10]}")
    print(f"market 보강 : {len(r['enriched_market'])}건")
    if "backed_up" in r:
        print(f"백업        : {r['backed_up']}")
    print("━" * 50)


if __name__ == "__main__":
    main()
