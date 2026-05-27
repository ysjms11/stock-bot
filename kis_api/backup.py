"""GitHub Gist 백업/복원."""
import os
import json
import re
import asyncio
import aiohttp
import sqlite3
import xml.etree.ElementTree as ET
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from ._config import *
from ._config import (
    KIS_BASE_URL, KIS_APP_KEY, KIS_APP_SECRET, KST, ET, _DATA_DIR, _DB_PATH,
    WATCHLIST_FILE, STOPLOSS_FILE, US_WATCHLIST_FILE, DART_SEEN_FILE,
    PORTFOLIO_FILE, WATCHALERT_FILE, WATCH_SENT_FILE, STOPLOSS_SENT_FILE,
    US_HOLDINGS_SENT_FILE, DECISION_LOG_FILE, COMPARE_LOG_FILE,
    WATCHLIST_LOG_FILE, EVENTS_FILE, WEEKLY_BASE_FILE, UNIVERSE_FILE,
    CONSENSUS_CACHE_FILE, PORTFOLIO_HISTORY_FILE, TRADE_LOG_FILE,
    SECTOR_FLOW_CACHE_FILE, SECTOR_ROTATION_FILE, SUPPLY_HISTORY_FILE,
    REPORTS_FILE, REGIME_STATE_FILE, MACRO_SENT_FILE, TOKEN_CACHE_FILE,
    GITHUB_TOKEN, _BACKUP_GIST_ENV, _BACKUP_FILES_LIST, MACRO_SYMBOLS,
    DART_BASE_URL,
)
from ._session import _get_session, _kis_get, _kis_headers, get_kis_token, _token_cache
from ._helpers import (
    _is_us_ticker, _guess_excd, _is_us_market_hours_kst, _is_us_market_closed,
    DART_KEYWORDS, _load_knu_senti_lex, _FINANCE_PHRASE_SCORES, _RANKING_RE,
    _US_POSITIVE_KEYWORDS, _US_NEGATIVE_KEYWORDS, _NYSE_TICKERS, _AMEX_TICKERS,
)
from ._files import (
    load_json, save_json, load_watchlist, load_stoploss, load_us_watchlist,
    load_dart_seen, load_watchalert, _wa_market, load_kr_watch_tickers,
    load_us_watch_tickers, load_kr_watch_dict, load_us_watch_dict,
    load_decision_log, load_trade_log, save_trade_log, get_trade_stats,
    load_consensus_cache, load_sector_flow_cache, save_sector_flow_cache,
    load_compare_log, load_watchlist_log, append_watchlist_log, load_events,
)


def _load_us_holdings_sent() -> dict:
    """us_holdings_sent.json 로드 + 48h 초과 엔트리 자동 정리.
    스키마: {ticker_YYYY-MM-DD: {sent_at: ISO, events_count: int, downgrades: [str]}}
    cleanup: sent_at 이 48h 초과된 엔트리 제거.
    """
    data = load_json(US_HOLDINGS_SENT_FILE, {})
    cutoff = datetime.now() - timedelta(hours=48)
    cleaned = {}
    for k, v in data.items():
        try:
            sent_at = datetime.fromisoformat(v.get("sent_at", ""))
            if sent_at >= cutoff:
                cleaned[k] = v
        except (ValueError, TypeError, AttributeError):
            continue  # 파싱 실패 시 엔트리 drop
    if len(cleaned) != len(data):
        save_json(US_HOLDINGS_SENT_FILE, cleaned)  # 정리 반영
    return cleaned


def _save_us_holdings_sent(data: dict) -> None:
    save_json(US_HOLDINGS_SENT_FILE, data)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# GitHub Gist 백업/복원
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def _gist_patch_with_retry(s, gist_id: str, files: dict, headers: dict,
                                 description: str, max_retries: int = 3) -> dict:
    """Gist PATCH + 409/429 시 exponential backoff retry.

    GitHub Gist API는 If-Match conditional request 미지원 (400 반환).
    단순 PATCH (last-write-wins) + 409/429 시 backoff."""
    url = f"https://api.github.com/gists/{gist_id}"
    payload = {"description": description, "files": files}

    for attempt in range(max_retries):
        try:
            async with s.patch(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    d = await resp.json()
                    return {"ok": True, "action": "updated", "gist_id": d["id"],
                            "updated_at": d.get("updated_at", ""), "attempts": attempt + 1}
                if resp.status == 409:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    print(f"[backup] PATCH 409 Conflict, {wait}s 후 재시도 ({attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait)
                    continue
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    print(f"[backup] PATCH 429 Rate limit, {retry_after}s 후 재시도")
                    await asyncio.sleep(retry_after)
                    continue
                text = await resp.text()
                return {"ok": False, "error": f"PATCH {resp.status}: {text[:200]}"}
        except Exception as e:
            return {"ok": False, "error": f"PATCH 예외: {e}"}

    return {"ok": False, "error": f"PATCH — {max_retries}회 재시도 후 실패"}


async def backup_data_files() -> dict:
    """GitHub Gist에 /data/*.json 백업 (PATCH 기존 Gist 또는 POST 신규 생성).
    PATCH 409/429 시 최대 3회 exponential backoff 재시도."""
    if not GITHUB_TOKEN:
        return {"ok": False, "error": "GITHUB_TOKEN 미설정"}

    gist_id = os.environ.get(_BACKUP_GIST_ENV, "")
    files: dict = {}
    backed_up: list = []

    for fpath in _BACKUP_FILES_LIST:
        fname = os.path.basename(fpath)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read().strip() or "{}"
                # 빈 dict/list는 백업 스킵
                try:
                    parsed = json.loads(content)
                    if parsed == {} or parsed == []:
                        continue
                except json.JSONDecodeError:
                    pass  # JSON 파싱 실패 시 그대로 백업 포함
                files[fname] = {"content": content}
                backed_up.append(fname)
            except Exception as e:
                print(f"[backup] {fname} 읽기 실패: {e}")

    if not files:
        return {"ok": False, "error": "백업할 파일 없음"}

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    description = f"stock-bot /data/ backup {ts}"

    try:
        s = _get_session()
        if gist_id:
            result = await _gist_patch_with_retry(s, gist_id, files, headers, description)
            if result.get("ok"):
                result["files"] = backed_up
            return result
        else:
            url = "https://api.github.com/gists"
            payload = {"description": description, "public": False, "files": files}
            async with s.post(url, json=payload, headers=headers) as resp:
                if resp.status == 201:
                    d = await resp.json()
                    new_id = d["id"]
                    print(f"[backup] 신규 Gist 생성: {new_id} — BACKUP_GIST_ID 환경변수 설정 필요")
                    return {"ok": True, "action": "created", "gist_id": new_id,
                            "files": backed_up, "note": f"BACKUP_GIST_ID={new_id} 환경변수 설정 필요"}
                text = await resp.text()
                return {"ok": False, "error": f"POST {resp.status}: {text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def restore_data_files(force: bool = False) -> dict:
    """GitHub Gist에서 /data/*.json 복원. force=False이면 기존 파일 보존."""
    if not GITHUB_TOKEN:
        return {"ok": False, "error": "GITHUB_TOKEN 미설정"}

    gist_id = os.environ.get(_BACKUP_GIST_ENV, "")
    if not gist_id:
        return {"ok": False, "error": "BACKUP_GIST_ID 환경변수 미설정"}

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        s = _get_session()
        async with s.get(f"https://api.github.com/gists/{gist_id}", headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                return {"ok": False, "error": f"GET {resp.status}: {text[:200]}"}
            data = await resp.json()

        gist_files = data.get("files", {})
        restored: list = []
        skipped: list = []

        for fpath in _BACKUP_FILES_LIST:
            fname = os.path.basename(fpath)
            if fname not in gist_files:
                continue
            if not force and os.path.exists(fpath):
                skipped.append(fname)
                continue
            try:
                content = gist_files[fname].get("content", "{}")
                json.loads(content)  # 유효성 검사
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
                restored.append(fname)
            except Exception as e:
                print(f"[restore] {fname} 복원 실패: {e}")

        return {"ok": True, "restored": restored, "skipped": skipped,
                "gist_id": gist_id, "updated_at": data.get("updated_at", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def get_backup_status() -> dict:
    """백업 Gist 상태 조회 (최근 백업 시각, 파일 목록)"""
    if not GITHUB_TOKEN:
        return {"ok": False, "error": "GITHUB_TOKEN 미설정"}

    gist_id = os.environ.get(_BACKUP_GIST_ENV, "")
    if not gist_id:
        return {"ok": False, "gist_id": None, "note": "BACKUP_GIST_ID 미설정 — 첫 백업 실행 후 자동 생성"}

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        s = _get_session()
        async with s.get(f"https://api.github.com/gists/{gist_id}", headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                return {"ok": False, "error": f"GET {resp.status}: {text[:100]}"}
            data = await resp.json()

        return {
            "ok": True,
            "gist_id": gist_id,
            "updated_at": data.get("updated_at", ""),
            "description": data.get("description", ""),
            "files": list(data.get("files", {}).keys()),
            "file_count": len(data.get("files", {})),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 뉴스 조회 (Google News RSS)
