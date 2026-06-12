"""SEC EDGAR 1차 공시 조회 도구.

handle_get_sec_filings(arguments) → dict
  - ticker 지정: 해당 종목 공시 조회
  - tickers 지정: 여러 종목 배치 조회
  - 없으면 DB 캐시에서 최근 공시 목록 반환
"""

import asyncio
import logging
from datetime import datetime

from db_collector import db_write_lock

logger = logging.getLogger(__name__)


async def handle_get_sec_filings(arguments: dict) -> dict:
    """SEC EDGAR 1차 공시 조회.

    arguments:
        ticker  (str):       단일 티커 (예: 'XNDU', 'NVDA')
        tickers (str|list):  복수 티커 콤마 구분 (예: 'NVDA,AMZN') 또는 리스트
        forms   (list[str]): 필터할 폼 종류 (기본: 8-K/F-1/S-1/424B3/424B4/424B5/424B1/424B2/EFFECT/6-K/SC 13D/SC 13G/4)
        days    (int):       최근 N일 (기본 30, 최대 180)
        db_only (bool):      True면 SEC API 호출 없이 DB 캐시만 반환 (기본 False)
        save_db (bool):      True면 결과를 DB에 저장 (기본 True)
        limit   (int):       반환 최대 건수 (기본 50, 최대 200)

    Returns:
        dict: {ticker/tickers, cik_map, days, form_filter,
               total, filings: [...], db_saved, errors: [...]}
    """
    from kis_api.sec_edgar import (
        ticker_to_cik, bulk_fetch_cik_map, get_company_filings,
        upsert_sec_filings, query_sec_filings,
        FILING_FORMS_DEFAULT,
    )

    # ── 파라미터 파싱 ──
    raw_ticker  = arguments.get("ticker", "").strip().upper()
    raw_tickers = arguments.get("tickers", "")
    forms_arg   = arguments.get("forms", None)
    days        = min(int(arguments.get("days", 30)), 180)
    db_only     = bool(arguments.get("db_only", False))
    save_db     = bool(arguments.get("save_db", True))
    limit       = min(int(arguments.get("limit", 50)), 200)

    # 폼 목록 결정
    if forms_arg:
        if isinstance(forms_arg, list):
            forms_set = set(forms_arg)
        else:
            forms_set = {f.strip() for f in str(forms_arg).split(",") if f.strip()}
    else:
        forms_set = FILING_FORMS_DEFAULT

    # 티커 목록 수집
    tickers: list[str] = []
    if raw_ticker:
        tickers.append(raw_ticker)
    if raw_tickers:
        if isinstance(raw_tickers, list):
            tickers.extend([t.strip().upper() for t in raw_tickers if t.strip()])
        else:
            tickers.extend([t.strip().upper() for t in str(raw_tickers).split(",") if t.strip()])
    # 중복 제거, 순서 유지
    seen = set()
    unique_tickers = []
    for t in tickers:
        if t and t not in seen:
            seen.add(t)
            unique_tickers.append(t)
    tickers = unique_tickers

    errors: list[str] = []
    all_filings: list[dict] = []
    cik_map: dict[str, str] = {}

    # ── DB-only 모드 ──
    if db_only:
        if tickers:
            for t in tickers:
                rows = query_sec_filings(ticker=t, forms=list(forms_set), days=days, limit=limit)
                all_filings.extend(rows)
        else:
            all_filings = query_sec_filings(forms=list(forms_set), days=days, limit=limit)
        all_filings = all_filings[:limit]
        return {
            "source":      "db_cache",
            "tickers":     tickers or ["(all)"],
            "days":        days,
            "form_filter": sorted(forms_set),
            "total":       len(all_filings),
            "filings":     all_filings,
        }

    # ── 티커 미지정: DB 캐시 반환 ──
    if not tickers:
        rows = query_sec_filings(forms=list(forms_set), days=days, limit=limit)
        return {
            "source":      "db_cache",
            "note":        "ticker 미지정 → DB 캐시 반환. 특정 종목 조회 시 ticker 파라미터 전달.",
            "days":        days,
            "form_filter": sorted(forms_set),
            "total":       len(rows),
            "filings":     rows,
        }

    # ── CIK 조회 ──
    cik_map = await bulk_fetch_cik_map(tickers)
    missing_cik = [t for t in tickers if t not in cik_map]
    # bulk_fetch_cik_map 에서 못 찾은 것은 개별 시도
    for t in missing_cik:
        cik = await ticker_to_cik(t)
        if cik:
            cik_map[t] = cik
        else:
            errors.append(f"{t}: CIK not found in SEC EDGAR")

    # ── 공시 조회 ──
    db_saved_total = 0
    for t in tickers:
        cik = cik_map.get(t)
        if not cik:
            continue
        try:
            filings = await get_company_filings(cik, types=forms_set, days=days)
            # ticker 필드 명시적 세팅 (EDGAR ticker 필드와 다를 수 있음)
            for f in filings:
                f["ticker"] = t
            all_filings.extend(filings)
            if save_db and filings:
                async with db_write_lock:
                    saved = upsert_sec_filings(filings)
                db_saved_total += saved
        except Exception as exc:
            logger.error("get_company_filings(%s, %s) 실패: %s", t, cik, exc)
            errors.append(f"{t}: {exc}")
        # 복수 종목 연속 호출 시 rate limit 방어
        if len(tickers) > 1:
            await asyncio.sleep(0.12)

    # 날짜 내림차순 정렬
    all_filings.sort(key=lambda x: x.get("filing_date", ""), reverse=True)
    all_filings = all_filings[:limit]

    result = {
        "source":      "sec_edgar_api",
        "tickers":     tickers,
        "cik_map":     cik_map,
        "days":        days,
        "form_filter": sorted(forms_set),
        "total":       len(all_filings),
        "filings":     all_filings,
    }
    if save_db:
        result["db_saved"] = db_saved_total
    if errors:
        result["errors"] = errors

    return result
