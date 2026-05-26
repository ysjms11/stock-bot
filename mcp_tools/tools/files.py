# mcp_tools/tools/files.py — read_file, write_file, list_files, read_report_pdf
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
from mcp_tools._helpers import _parse_page_range, _render_pdf_pages

try:
    from report_crawler import (
        collect_reports, get_collection_tickers,
        DB_PATH as REPORT_DB_PATH,
    )
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False
    REPORT_DB_PATH = ""


async def handle_read_file(arguments: dict) -> dict | list:
    result = None
    rel = arguments.get("path", "").strip()
    if not rel:
        result = {"error": "path는 필수입니다"}
    elif ".." in rel or rel.startswith("/"):
        result = {"error": "상위 디렉토리 접근 불가 (../ 및 절대경로 차단)"}
    else:
        _allowed_ext = (".md", ".py", ".json", ".txt", ".pdf")
        if not any(rel.endswith(ext) for ext in _allowed_ext):
            result = {"error": f"허용 확장자: {', '.join(_allowed_ext)}"}
        else:
            _base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
            _fpath = os.path.realpath(os.path.join(_base, rel))
            # 5/9 hardening: prefix collision 차단 (os.sep 경계 검사)
            if _fpath != _base and not _fpath.startswith(_base + os.sep):
                result = {"error": "stock-bot 디렉토리 밖 접근 불가"}
            elif not os.path.isfile(_fpath):
                result = {"error": f"파일 없음: {rel}"}
            elif rel.endswith(".pdf"):
                _pdf_size = os.path.getsize(_fpath)
                if _pdf_size > 2 * 1024 * 1024:
                    result = {"error": f"PDF 크기 초과 (최대 2MB, 실제 {_pdf_size // 1024}KB)"}
                else:
                    result = {
                        "path": rel,
                        "full_path": _fpath,
                        "size_kb": _pdf_size // 1024,
                        "note": "PDF 파일입니다. Claude Code의 Read 도구로 직접 읽으세요.",
                    }
            elif os.path.getsize(_fpath) > 100 * 1024:
                result = {"error": f"파일 크기 초과 (최대 100KB, 실제 {os.path.getsize(_fpath) // 1024}KB)"}
            else:
                with open(_fpath, "r", encoding="utf-8") as _rf:
                    result = {"path": rel, "content": _rf.read()}

    return result


async def handle_write_file(arguments: dict) -> dict | list:
    result = None
    rel = arguments.get("path", "").strip()
    content = arguments.get("content", "")
    if not rel:
        result = {"error": "path는 필수입니다"}
    elif ".." in rel or rel.startswith("/"):
        result = {"error": "상위 디렉토리 접근 불가 (../ 및 절대경로 차단)"}
    else:
        _write_allowed = (".md", ".json", ".txt")
        _write_blocked = (".py", ".env")
        if any(rel.endswith(ext) for ext in _write_blocked):
            result = {"error": f".py/.env 파일은 쓰기 불가"}
        elif not any(rel.endswith(ext) for ext in _write_allowed):
            result = {"error": f"허용 확장자: {', '.join(_write_allowed)}"}
        elif len(content.encode("utf-8")) > 200 * 1024:
            result = {"error": f"내용 크기 초과 (최대 200KB)"}
        else:
            _base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
            _fpath = os.path.realpath(os.path.join(_base, rel))
            # 5/9 hardening: prefix collision 차단 (os.sep 경계 검사)
            if _fpath != _base and not _fpath.startswith(_base + os.sep):
                result = {"error": "stock-bot 디렉토리 밖 접근 불가"}
            else:
                os.makedirs(os.path.dirname(_fpath), exist_ok=True)
                with open(_fpath, "w", encoding="utf-8") as _wf:
                    _wf.write(content)
                result = {"ok": True, "path": rel, "bytes": len(content.encode("utf-8"))}

    return result


async def handle_list_files(arguments: dict) -> dict | list:
    result = None
    rel = (arguments.get("path") or ".").strip()
    if ".." in rel or rel.startswith("/"):
        result = {"error": "상위 디렉토리 접근 불가 (../ 및 절대경로 차단)"}
    else:
        _base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
        _dpath = os.path.realpath(os.path.join(_base, rel))
        # 5/9 hardening: prefix collision 차단 (os.sep 경계 검사)
        if _dpath != _base and not _dpath.startswith(_base + os.sep):
            result = {"error": "stock-bot 디렉토리 밖 접근 불가"}
        elif not os.path.isdir(_dpath):
            result = {"error": f"디렉토리 없음: {rel}"}
        else:
            entries = []
            for item in sorted(os.listdir(_dpath)):
                item_path = os.path.join(_dpath, item)
                if item.startswith("."):
                    continue
                try:
                    st = os.stat(item_path)
                    entry = {"name": item, "size": st.st_size,
                             "modified": datetime.fromtimestamp(st.st_mtime, tz=KST).strftime("%Y-%m-%d %H:%M")}
                    if os.path.isdir(item_path):
                        entry["type"] = "dir"
                        sub = []
                        for sub_item in sorted(os.listdir(item_path)):
                            if sub_item.startswith("."):
                                continue
                            sub_path = os.path.join(item_path, sub_item)
                            try:
                                ss = os.stat(sub_path)
                                sub.append({"name": sub_item, "size": ss.st_size,
                                             "modified": datetime.fromtimestamp(ss.st_mtime, tz=KST).strftime("%Y-%m-%d %H:%M"),
                                             "type": "dir" if os.path.isdir(sub_path) else "file"})
                            except Exception:
                                pass
                        entry["children"] = sub
                    else:
                        entry["type"] = "file"
                    entries.append(entry)
                except Exception:
                    pass
            result = {"path": rel, "entries": entries}

    # ── Git 도구 ──────────────────────────────────────────────
    return result


async def handle_read_report_pdf(arguments: dict) -> dict | list:
    result = None
    if not _REPORT_AVAILABLE:
        result = {"error": "report_crawler 모듈 미설치 — REPORT_DB_PATH 없음"}
    else:
        import sqlite3 as _sqlite3
        _ticker    = arguments.get("ticker", "").strip()
        _report_id = arguments.get("report_id")
        _pages_str = arguments.get("pages", "").strip() or None

        if not _ticker:
            result = {"error": "ticker는 필수입니다"}
        elif not REPORT_DB_PATH or not os.path.exists(REPORT_DB_PATH):
            result = {"error": f"리포트 DB 없음: {REPORT_DB_PATH}"}
        else:
            try:
                _conn = _sqlite3.connect(REPORT_DB_PATH, timeout=10)
                _conn.row_factory = _sqlite3.Row
                if _report_id:
                    _row = _conn.execute(
                        "SELECT * FROM reports WHERE id=?", (_report_id,)
                    ).fetchone()
                else:
                    _row = _conn.execute(
                        "SELECT * FROM reports WHERE ticker=? AND pdf_path IS NOT NULL AND pdf_path != '' ORDER BY date DESC LIMIT 1",
                        (_ticker,)
                    ).fetchone()
                _conn.close()
            except Exception as _e:
                _row = None
                print(f"[read_report_pdf] SQLite 오류: {_e}")

            if not _row:
                result = {"error": f"리포트 없음 (ticker={_ticker}, report_id={_report_id})"}
            elif not _row["pdf_path"]:
                result = {"error": "PDF 경로 없음 (pdf_path가 비어 있음)"}
            else:
                # 보안: path traversal 차단
                _pdf_path = os.path.realpath(_row["pdf_path"])
                _data_base = os.path.realpath(_DATA_DIR) if _DATA_DIR else ""
                _bot_base  = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
                _allowed   = _data_base and _pdf_path.startswith(_data_base) or _pdf_path.startswith(_bot_base)
                if not _allowed:
                    result = {"error": "PDF 경로가 허용 디렉토리 밖입니다"}
                elif not os.path.isfile(_pdf_path):
                    result = {"error": f"PDF 파일 없음: {_pdf_path}"}
                else:
                    # fitz로 페이지 수 파악 후 범위 파싱
                    try:
                        import fitz as _fitz_tmp
                        _doc_tmp = _fitz_tmp.open(_pdf_path)
                        _total_pages = len(_doc_tmp)
                        _doc_tmp.close()
                    except Exception as _fe:
                        result = {"error": f"PDF 열기 실패: {_fe}"}
                        _total_pages = -1

                    if _total_pages >= 0:
                        _page_indices = _parse_page_range(_pages_str, _total_pages)
                        if isinstance(_page_indices, str):
                            # 오류 문자열 반환
                            result = {"error": _page_indices}
                        else:
                            # PNG 렌더링
                            try:
                                _images, _meta = _render_pdf_pages(_pdf_path, _page_indices)
                            except Exception as _re:
                                result = {"error": f"PDF 렌더링 실패: {_re}"}
                                _images = None

                            if _images is not None:
                                # 메타 텍스트 content
                                _meta_text = {
                                    "ticker":         _ticker,
                                    "report_id":      _row["id"] if "id" in _row.keys() else _report_id,
                                    "title":          _row["title"],
                                    "source":         _row["source"],
                                    "date":           _row["date"],
                                    "pdf_size_kb":    os.path.getsize(_pdf_path) // 1024,
                                    "pages_requested": _pages_str or "전체",
                                    **_meta,
                                }
                                # result를 list로 반환 → _handle_jsonrpc에서 직접 content로 사용
                                result = _images + [
                                    {"type": "text", "text": json.dumps(_meta_text, ensure_ascii=False)}
                                ]

    return result


