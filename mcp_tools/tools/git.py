# mcp_tools/tools/git.py — git_status, git_diff, git_log, git_commit, git_push
import asyncio
import json
import os
import re
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from kis_api import *
from kis_api import (
    _DATA_DIR, _is_us_ticker, _guess_excd, _kis_get, _get_session,
    _fetch_sector_flow, _TICKER_SECTOR,
    ws_manager, get_ws_tickers,
    collect_macro_data, format_macro_msg,
    check_drawdown, PORTFOLIO_HISTORY_FILE,
    load_trade_log, save_trade_log, get_trade_stats as _get_trade_stats_fn, TRADE_LOG_FILE,
    backup_data_files, restore_data_files, get_backup_status,
    SUPPLY_HISTORY_FILE,
    get_historical_ohlcv, get_historical_supply, compute_volume_profile,
    fetch_us_news, analyze_us_news_sentiment,
    fetch_us_earnings_calendar, fetch_us_sector_etf,
    fetch_us_short_interest,
    cmd_regime,
    kis_finance_ratio_rank, kis_near_new_highlow, kis_inquire_member,
    kis_daily_credit_balance, kis_daily_loan_trans, kis_overtime_price, kis_asking_price,
    kis_overtime_fluctuation, kis_traded_by_company, kis_dividend_rate_rank,
    load_corp_codes, search_dart_reports, save_dart_report,
    list_dart_reports, read_dart_report, DART_REPORTS_DIR,
    list_disclosures_for_ticker, fetch_and_cache_disclosure,
    fetch_youtube_transcript,
    fmp_earnings_transcript, fmp_price_target_summary,
    fmp_analyst_estimates, fmp_stock_grades,
    fetch_polymarket, fetch_treasury_curve, fetch_external_macro_signals,
    fetch_pension_fund_flow,
    WI26_SECTORS, detect_sector_rotation,
    load_sector_flow_cache, save_sector_flow_cache,
    load_decision_log, load_compare_log, load_compare_log,
    append_watchlist_log,
    DECISION_LOG_FILE, COMPARE_LOG_FILE, WATCHALERT_FILE,
)
from db_collector import load_krx_db, scan_stocks, _load_history
from mcp_tools._helpers import _run_git, _validate_git_path

try:
    from report_crawler import (
        collect_reports, get_collection_tickers,
        DB_PATH as REPORT_DB_PATH,
    )
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False
    REPORT_DB_PATH = ""


async def handle_git_status(arguments: dict) -> dict | list:
    result = None
    rc, stdout, stderr = await asyncio.to_thread(
        _run_git, ["status", "--porcelain"]
    )
    if rc != 0:
        result = {"error": f"git status 실패: {stderr.strip()}"}
    else:
        _, branch_out, _ = await asyncio.to_thread(
            _run_git, ["branch", "--show-current"]
        )
        branch = branch_out.strip()
        staged, modified, untracked = [], [], []
        for line in stdout.splitlines():
            if len(line) < 2:
                continue
            xy, fpath = line[:2], line[3:]
            if xy[0] in ("A", "M", "D", "R", "C"):
                staged.append(fpath.strip())
            if xy[1] in ("M", "D"):
                modified.append(fpath.strip())
            if xy == "??":
                untracked.append(fpath.strip())
        result = {
            "branch":    branch,
            "clean":     stdout.strip() == "",
            "staged":    staged,
            "modified":  modified,
            "untracked": untracked,
        }

    return result


async def handle_git_diff(arguments: dict) -> dict | list:
    result = None
    path_arg   = arguments.get("path", "").strip()
    staged_arg = bool(arguments.get("staged", False))
    git_args   = ["diff"]
    if staged_arg:
        git_args.append("--cached")
    if path_arg:
        validated = _validate_git_path(path_arg)
        git_args += ["--", validated]
    rc, stdout, stderr = await asyncio.to_thread(_run_git, git_args)
    if rc != 0:
        result = {"error": f"git diff 실패: {stderr.strip()}"}
    else:
        _MAX_DIFF = 50 * 1024  # 50KB
        truncated = False
        if len(stdout.encode()) > _MAX_DIFF:
            stdout = stdout.encode()[:_MAX_DIFF].decode(errors="replace")
            truncated = True
        result = {
            "staged":    staged_arg,
            "path":      path_arg or None,
            "diff":      stdout,
            "truncated": truncated,
        }

    return result


async def handle_git_log(arguments: dict) -> dict | list:
    result = None
    n        = min(int(arguments.get("n", 10) or 10), 50)
    path_arg = arguments.get("path", "").strip()
    git_args = ["log", "--oneline", "--no-decorate", f"-{n}"]
    if path_arg:
        validated = _validate_git_path(path_arg)
        git_args += ["--", validated]
    rc, stdout, stderr = await asyncio.to_thread(_run_git, git_args)
    if rc != 0:
        result = {"error": f"git log 실패: {stderr.strip()}"}
    else:
        commits = []
        for line in stdout.splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2:
                commits.append({"hash": parts[0], "message": parts[1]})
        result = {
            "n":       n,
            "path":    path_arg or None,
            "commits": commits,
        }

    return result


async def handle_git_commit(arguments: dict) -> dict | list:
    result = None
    message = arguments.get("message", "").strip()
    files   = arguments.get("files", [])
    if not message:
        result = {"error": "message가 비어 있습니다"}
    elif len(message) > 500:
        result = {"error": f"message가 500자 초과({len(message)}자)"}
    elif not files:
        result = {"error": "files 목록이 비어 있습니다"}
    else:
        # .py/.env 차단
        blocked = [f for f in files if f.endswith(".py") or f.endswith(".env")]
        if blocked:
            result = {"error": f".py/.env 파일 커밋 불가: {blocked}"}
        else:
            try:
                validated_files = [_validate_git_path(f) for f in files]
            except ValueError as ve:
                result = {"error": str(ve)}
            else:
                rc_add, out_add, err_add = await asyncio.to_thread(
                    _run_git, ["add"] + validated_files
                )
                if rc_add != 0:
                    result = {"error": f"git add 실패: {err_add.strip()}"}
                else:
                    rc_cm, out_cm, err_cm = await asyncio.to_thread(
                        _run_git, ["commit", "-m", message]
                    )
                    if rc_cm != 0:
                        result = {"error": f"git commit 실패: {err_cm.strip()}"}
                    else:
                        result = {
                            "ok":     True,
                            "files":  validated_files,
                            "output": out_cm.strip(),
                        }

    return result


async def handle_git_push(arguments: dict) -> dict | list:
    result = None
    # 현재 브랜치 확인 — main만 허용
    _, branch_out, _ = await asyncio.to_thread(
        _run_git, ["branch", "--show-current"]
    )
    branch = branch_out.strip()
    if branch != "main":
        result = {"error": f"main 브랜치만 push 허용 (현재: {branch!r})"}
    else:
        rc, stdout, stderr = await asyncio.to_thread(
            _run_git, ["push", "origin", "main"]
        )
        # stderr에서 ghp_ 토큰 마스킹
        masked_err = re.sub(r"ghp_[A-Za-z0-9]+", "***", stderr)
        masked_out = re.sub(r"ghp_[A-Za-z0-9]+", "***", stdout)
        if rc != 0:
            result = {"error": f"git push 실패: {masked_err.strip()}"}
        else:
            result = {
                "ok":     True,
                "branch": branch,
                "output": (masked_out + masked_err).strip(),
            }

    return result


