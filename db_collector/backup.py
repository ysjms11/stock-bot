"""iCloud Drive 백업.

P3-8 박리: backup_to_icloud
"""

import os
from datetime import datetime

from ._config import KST


def backup_to_icloud():
    """data/ → iCloud Drive 백업. 최근 2개 유지 (current / previous)."""
    import shutil

    ICLOUD_BASE = os.path.expanduser(
        "~/Library/Mobile Documents/com~apple~CloudDocs/stock-bot-backup"
    )
    CURRENT = os.path.join(ICLOUD_BASE, "current")
    PREVIOUS = os.path.join(ICLOUD_BASE, "previous")

    # 1. previous 삭제
    if os.path.exists(PREVIOUS):
        shutil.rmtree(PREVIOUS)

    # 2. current → previous 이동
    if os.path.exists(CURRENT):
        os.rename(CURRENT, PREVIOUS)

    # 3. 새 current 생성
    os.makedirs(CURRENT, exist_ok=True)

    # 4. 파일 복사
    data_dir = os.environ.get("DATA_DIR", "data")

    # stock.db
    db_src = os.path.join(data_dir, "stock.db")
    if os.path.exists(db_src):
        shutil.copy2(db_src, os.path.join(CURRENT, "stock.db"))

    # *.json, *.md, *.txt (최상위만, krx_db/ 제외)
    for f in os.listdir(data_dir):
        src = os.path.join(data_dir, f)
        if os.path.isfile(src) and (
            f.endswith(".json") or f.endswith(".md") or f.endswith(".txt")
        ):
            shutil.copy2(src, os.path.join(CURRENT, f))

    # research/ 폴더
    research_src = os.path.join(data_dir, "research")
    research_dst = os.path.join(CURRENT, "research")
    if os.path.isdir(research_src):
        shutil.copytree(research_src, research_dst, dirs_exist_ok=True)

    # 백업 타임스탬프
    with open(os.path.join(CURRENT, "_backup_time.txt"), "w") as f:
        f.write(datetime.now(KST).isoformat())

    print(f"[backup_to_icloud] 완료 → {CURRENT}")
    return {"ok": True, "path": CURRENT}
