"""SQLite stock.db 공용 connect 헬퍼 — PRAGMA 일관 적용."""
import sqlite3
import os

_DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = f"{_DATA_DIR}/stock.db"
