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
from mcp_tools._helpers import (
    _parse_page_range, _render_pdf_pages, _extract_pdf_text, _embed_pdf_resource,
    _is_sensitive_path,
)

try:
    from report_crawler import (
        collect_reports, get_collection_tickers,
        DB_PATH as REPORT_DB_PATH,
    )
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False
    REPORT_DB_PATH = ""

# stock-bot 레포 루트 (mcp_tools/tools/files.py에서 3단계 상위). read_file/write_file/
# list_files/read_report_pdf 4곳의 경로탈출 가드가 공유하는 base — 모듈 속성으로 뽑아둬야
# 테스트가 실제 레포 대신 tmp 디렉토리를 가리키도록 monkeypatch할 수 있다
# (2026-09 리뷰: 이전엔 함수마다 인라인 재계산이라 patch 지점이 없어 테스트가 실제
# data/ 밑에 파일을 쓰고 지우는 부작용이 있었음).
_BOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))


async def handle_read_file(arguments: dict) -> dict | list:
    result = None
    rel = arguments.get("path", "").strip()
    if not rel:
        result = {"error": "path는 필수입니다"}
    elif ".." in rel or rel.startswith("/"):
        result = {"error": "상위 디렉토리 접근 불가 (../ 및 절대경로 차단)"}
    elif _is_sensitive_path(rel):
        result = {"error": "거부: 보안상 보호된 경로입니다"}
    else:
        _allowed_ext = (".md", ".py", ".json", ".txt", ".pdf")
        if not any(rel.endswith(ext) for ext in _allowed_ext):
            result = {"error": f"허용 확장자: {', '.join(_allowed_ext)}"}
        else:
            _base = _BOT_ROOT
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
            else:
                _fsize = os.path.getsize(_fpath)
                _raw_lines = arguments.get("lines")
                _raw_offset = arguments.get("offset")
                try:
                    _lines_limit = None if _raw_lines in (None, "") else int(_raw_lines)
                    _offset = 0 if _raw_offset in (None, "") else int(_raw_offset)
                    _int_error = False
                except (TypeError, ValueError):
                    _int_error = True

                if _int_error:
                    result = {"error": "lines/offset은 정수여야 합니다"}
                else:
                    _offset = max(0, _offset)
                    if _fsize <= 100 * 1024 and _lines_limit is None:
                        # 100KB 이하 + lines 미지정 → 기존 전체읽기 동작 무변경
                        with open(_fpath, "r", encoding="utf-8") as _rf:
                            result = {"path": rel, "content": _rf.read()}
                    else:
                        # 100KB 초과이거나 lines 지정 → 청크 단위 읽기.
                        # lines 미지정(자동 청크 모드)이면 offset부터 파일 끝까지를 요청 범위로 삼되
                        # 아래 바이트 버짓으로 자연히 첫 청크만 반환됨 — 클라이언트는 path(+offset)만으로 순회 가능.
                        _budget = 100 * 1024
                        with open(_fpath, "r", encoding="utf-8") as _rf:
                            _all = _rf.readlines()
                        _total_lines = len(_all)
                        _sliced = _all[_offset: _offset + _lines_limit] if _lines_limit is not None else _all[_offset:]
                        _kept = []
                        _kept_bytes = 0
                        _truncated = False
                        _partial_line = False
                        for _ln in _sliced:
                            _lb = len(_ln.encode("utf-8"))
                            if _kept_bytes + _lb > _budget:
                                _truncated = True
                                break
                            _kept.append(_ln)
                            _kept_bytes += _lb
                        if _truncated and not _kept and _sliced:
                            # 단일 라인 자체가 버짓을 넘는 극단 케이스(예: corp_codes.json류) —
                            # 바이트 단위로 잘라 반환. 잔여 바이트는 영구 접근 불가이므로
                            # next_offset은 이 라인 "다음"으로 넘겨 무한 재요청을 방지한다.
                            _kept = [_sliced[0].encode("utf-8")[:_budget].decode("utf-8", errors="ignore")]
                            _partial_line = True
                        _lines_returned = len(_kept)
                        _next_offset = _offset + _lines_returned
                        result = {
                            "path": rel,
                            "content": "".join(_kept),
                            "lines_returned": _lines_returned,
                            "total_lines": _total_lines,
                            "offset": _offset,
                        }
                        if _truncated:
                            result["truncated"] = True
                        if _partial_line:
                            result["partial_line"] = True
                        if _next_offset < _total_lines:
                            # 남은 내용 있음 — next_offset 제공(이게 순회 계속 신호)
                            result["next_offset"] = _next_offset
                            result["note"] = (
                                f"라인 {_offset} 바이트절단, 잔여 접근불가. next_offset로 다음 줄부터 재호출"
                                if _partial_line else
                                "next_offset로 재호출해 이어읽기 — next_offset이 없으면 끝"
                            )
                        elif _partial_line:
                            # 마지막 줄이 통째로 잘린 경우 — next_offset은 없지만(더 읽을 줄이 없음) 절단 사실은 안내
                            result["note"] = f"라인 {_offset} 바이트절단, 잔여 접근불가"

    return result


async def handle_write_file(arguments: dict) -> dict | list:
    result = None
    rel = arguments.get("path", "").strip()
    content = arguments.get("content", "")
    if not rel:
        result = {"error": "path는 필수입니다"}
    elif ".." in rel or rel.startswith("/"):
        result = {"error": "상위 디렉토리 접근 불가 (../ 및 절대경로 차단)"}
    elif _is_sensitive_path(rel):
        result = {"error": "거부: 보안상 보호된 경로입니다"}
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
            _base = _BOT_ROOT
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
        _base = _BOT_ROOT
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
        _mode      = (arguments.get("mode") or "image").strip().lower()

        if _mode not in ("image", "text", "pdf"):
            return {"error": "mode는 image|text|pdf 중 하나입니다"}

        if not _ticker:
            result = {"error": "ticker는 필수입니다"}
        elif not REPORT_DB_PATH or not os.path.exists(REPORT_DB_PATH):
            result = {"error": f"리포트 DB 없음: {REPORT_DB_PATH}"}
        else:
            try:
                _conn = _sqlite3.connect(REPORT_DB_PATH, timeout=10)
                _conn.execute("PRAGMA cache_size = -65536")
                _conn.execute("PRAGMA temp_store = MEMORY")
                _conn.execute("PRAGMA mmap_size = 268435456")
                _conn.execute("PRAGMA busy_timeout = 30000")
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
            elif _report_id and _ticker and _row["ticker"] != _ticker:
                # ticker mismatch guard: report_id로 조회한 리포트가 다른 종목 소속
                result = {
                    "error": "ticker mismatch: report_id가 요청한 ticker와 다른 종목에 속합니다",
                    "report_id": _report_id,
                    "requested_ticker": _ticker,
                    "actual_ticker": _row["ticker"],
                }
            elif not _row["pdf_path"]:
                result = {"error": "PDF 경로 없음 (pdf_path가 비어 있음)"}
            else:
                # 보안: path traversal 차단 (세 모드 공통)
                _pdf_path = os.path.realpath(_row["pdf_path"])
                _data_base = os.path.realpath(_DATA_DIR) if _DATA_DIR else ""
                _bot_base  = _BOT_ROOT
                # Fix: explicit parens + os.sep boundary so that neither branch
                # is dead and a sibling directory (e.g. /…/stock-bot-evil) cannot
                # pass via a prefix match without the separator boundary.
                _in_data = bool(_data_base) and (
                    _pdf_path == _data_base or _pdf_path.startswith(_data_base + os.sep)
                )
                _in_bot = (
                    _pdf_path == _bot_base or _pdf_path.startswith(_bot_base + os.sep)
                )
                _allowed = _in_data or _in_bot
                if not _allowed:
                    result = {"error": "PDF 경로가 허용 디렉토리 밖입니다"}
                elif not os.path.isfile(_pdf_path):
                    result = {"error": f"PDF 파일 없음: {_pdf_path}"}
                else:
                    # pdf 모드: 페이지 파싱 불필요, 원본 통째 임베드
                    if _mode == "pdf":
                        try:
                            _blocks, _meta = _embed_pdf_resource(_pdf_path)
                            _meta_text = {
                                "mode":           "pdf",
                                "ticker":         _ticker,
                                "report_id":      _row["id"] if "id" in _row.keys() else _report_id,
                                "title":          _row["title"],
                                "source":         _row["source"],
                                "date":           _row["date"],
                                "pdf_size_kb":    os.path.getsize(_pdf_path) // 1024,
                                **_meta,
                            }
                            result = _blocks + [
                                {"type": "text", "text": json.dumps(_meta_text, ensure_ascii=False)}
                            ]
                        except Exception as _pe:
                            result = {"error": f"PDF 임베드 실패: {_pe}"}
                    else:
                        # image/text 모드: fitz로 페이지 수 파악 후 범위 파싱
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
                                result = {"error": _page_indices}
                            elif _mode == "text":
                                try:
                                    _blocks, _meta = _extract_pdf_text(_pdf_path, _page_indices)
                                except Exception as _te:
                                    result = {"error": f"PDF 텍스트 추출 실패: {_te}"}
                                    _blocks = None
                                if _blocks is not None:
                                    _meta_text = {
                                        "mode":            "text",
                                        "ticker":          _ticker,
                                        "report_id":       _row["id"] if "id" in _row.keys() else _report_id,
                                        "title":           _row["title"],
                                        "source":          _row["source"],
                                        "date":            _row["date"],
                                        "pdf_size_kb":     os.path.getsize(_pdf_path) // 1024,
                                        "pages_requested": _pages_str or "전체",
                                        **_meta,
                                    }
                                    result = _blocks + [
                                        {"type": "text", "text": json.dumps(_meta_text, ensure_ascii=False)}
                                    ]
                            else:
                                # image 모드 (기존 동작 그대로)
                                try:
                                    _images, _meta = _render_pdf_pages(_pdf_path, _page_indices)
                                except Exception as _re:
                                    result = {"error": f"PDF 렌더링 실패: {_re}"}
                                    _images = None

                                if _images is not None:
                                    _meta_text = {
                                        "mode":            "image",
                                        "ticker":          _ticker,
                                        "report_id":       _row["id"] if "id" in _row.keys() else _report_id,
                                        "title":           _row["title"],
                                        "source":          _row["source"],
                                        "date":            _row["date"],
                                        "pdf_size_kb":     os.path.getsize(_pdf_path) // 1024,
                                        "pages_requested": _pages_str or "전체",
                                        **_meta,
                                    }
                                    result = _images + [
                                        {"type": "text", "text": json.dumps(_meta_text, ensure_ascii=False)}
                                    ]

    return result


