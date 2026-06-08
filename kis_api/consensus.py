"""FnGuide/Nasdaq 컨센서스 조회 및 캐시 관리."""
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# FnGuide 컨센서스
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _recom_label(code) -> str:
    """RECOM_CD 숫자 → 투자의견 한글"""
    try:
        v = float(str(code).strip())
        if v >= 4.0: return "매수"
        if v >= 3.0: return "중립매수"
        if v >= 2.0: return "중립"
        return "매도"
    except Exception:
        return str(code)

def fetch_fnguide_consensus(ticker: str) -> dict:
    """
    FnGuide 컨센서스 JSON API로 증권사 목표주가/투자의견 조회.
    ticker: 6자리 한국 종목코드 (예: '009540')
    반환: {ticker, name, consensus_target, opinion, reports, updated}
    실패 시 빈 결과 반환 (예외 없음).
    """
    import requests as _req
    import json as _json

    empty = {
        "ticker": ticker, "name": "", "error": "데이터 없음",
        "consensus_target": {"avg": 0, "high": 0, "low": 0},
        "opinion": {"buy": 0, "hold": 0, "sell": 0, "not_rated": 0},
        "reports": [], "updated": "",
    }

    try:
        gicode = f"A{ticker}"
        base   = "https://comp.fnguide.com"
        referer = f"{base}/SVO2/ASP/SVD_Consensus.asp?pGB=1&gicode={gicode}"
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Referer": referer,
        }

        # 1. 세션 열기 (쿠키 획득)
        sess = _req.Session()
        sess.get(referer, headers=hdrs, timeout=10)

        # 2. 증권사별 목표주가 JSON (03_A{ticker}.json)
        r3 = sess.get(
            f"{base}/SVO2/json/data/01_06/03_{gicode}.json",
            headers=hdrs, timeout=10,
        )
        if r3.status_code != 200 or len(r3.content) < 50:
            return empty

        data3 = _json.loads(r3.content.decode("utf-8-sig"))
        rows = data3.get("comp", [])
        if not rows:
            return empty

        # 종목명 (04_ 파일에서 가져옴)
        stock_name = ""

        # 3. 최근 리포트 JSON (04_A{ticker}.json)
        reports = []
        r4 = sess.get(
            f"{base}/SVO2/json/data/01_06/04_{gicode}.json",
            headers=hdrs, timeout=10,
        )
        if r4.status_code == 200 and len(r4.content) > 50:
            data4 = _json.loads(r4.content.decode("utf-8-sig"))
            for item in data4.get("comp", []):
                stock_name = stock_name or item.get("CO_NM", "")
                tp_raw = item.get("TARGET_PRC", "").strip()
                try:
                    tp = int(tp_raw.replace(",", ""))
                except Exception:
                    tp = 0
                rec = item.get("RECOMMEND", "").upper()
                if rec in ("BUY", "STRONG BUY"):
                    opinion_str = "매수"
                elif rec in ("HOLD", "NEUTRAL", "OUTPERFORM"):
                    opinion_str = "중립"
                elif rec == "SELL":
                    opinion_str = "매도"
                else:
                    opinion_str = rec
                dt_raw = item.get("BULLET_DT", "")
                dt = f"{dt_raw[:4]}-{dt_raw[4:6]}-{dt_raw[6:]}" if len(dt_raw) == 8 else dt_raw
                reports.append({
                    "broker":  item.get("OFFER_INST_NM", ""),
                    "date":    dt,
                    "target":  tp,
                    "opinion": opinion_str,
                    "title":   item.get("TITLE", ""),
                })

        # 4. 증권사별 최신 목표주가 집계 (03_ 기반)
        inst_reports = []
        prices = []
        buy_cnt = hold_cnt = sell_cnt = not_rated_cnt = 0
        avg_prc = 0
        updated = ""

        for row in rows:
            tp_raw = row.get("TARGET_PRC", "").strip()
            try:
                tp = int(tp_raw.replace(",", ""))
            except Exception:
                tp = 0
            if not avg_prc:
                try:
                    avg_prc = int(row.get("AVG_PRC", "0").replace(",", ""))
                except Exception:
                    pass
            recom_cd = str(row.get("RECOM_CD", "")).strip()
            # Not Rated 판정: TP=0 이거나 RECOM_CD 가 공란/0/"0.0"
            if tp <= 0 or recom_cd in ("", "0", "0.0"):
                not_rated_cnt += 1
                recom = "Not Rated"
            else:
                recom = _recom_label(recom_cd)
                if recom == "매수":       buy_cnt  += 1
                elif recom == "중립매수": hold_cnt += 1
                elif recom == "중립":     hold_cnt += 1
                else:                     sell_cnt += 1
            dt = row.get("EST_DT", "").replace("/", "-")
            if not updated or dt > updated:
                updated = dt
            if tp > 0:
                prices.append(tp)
            inst_reports.append({
                "broker":  row.get("INST_NM", ""),
                "date":    dt,
                "target":  tp,
                "opinion": recom,
            })

        high = max(prices) if prices else 0
        low  = min(prices) if prices else 0
        avg  = avg_prc or (sum(prices) // len(prices) if prices else 0)

        return {
            "ticker":           ticker,
            "name":             stock_name,
            "consensus_target": {"avg": avg, "high": high, "low": low},
            "opinion":          {"buy": buy_cnt, "hold": hold_cnt, "sell": sell_cnt, "not_rated": not_rated_cnt},
            "reports":          reports,          # 04_: 최근 리포트 (제목+요약 포함)
            "broker_targets":   inst_reports,     # 03_: 증권사별 최신 목표가
            "updated":          updated,
        }

    except Exception as e:
        empty["error"] = str(e)
        return empty


def get_us_consensus(ticker: str) -> dict | None:
    """Nasdaq.com API로 미국 주식 애널리스트 1년 목표주가 조회.
    반환: {ticker, name, consensus_target:{avg}, recommendation}
    데이터 없거나 실패 시 None 반환.
    """
    import requests as _req, re as _re
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nasdaq.com/",
    }
    try:
        ticker = ticker.upper()
        # 1. summary: OneYrTarget (1년 목표주가)
        rs = _req.get(
            f"https://api.nasdaq.com/api/quote/{ticker}/summary?assetclass=stocks",
            headers=hdrs, timeout=8,
        )
        if rs.status_code != 200:
            return None
        summary = rs.json().get("data", {})
        target_raw = (summary.get("summaryData") or {}).get("OneYrTarget", {}).get("value", "")
        if not target_raw or target_raw == "N/A":
            return None
        avg = float(_re.sub(r"[^\d.]", "", target_raw))

        # 2. info: companyName
        ri = _req.get(
            f"https://api.nasdaq.com/api/quote/{ticker}/info?assetclass=stocks",
            headers=hdrs, timeout=8,
        )
        name = ticker
        if ri.status_code == 200:
            raw_name = (ri.json().get("data") or {}).get("companyName", ticker)
            # " Common Stock" 등 suffix 제거
            name = _re.sub(r"\s+(Common Stock|Common Shares?|Inc\.|Corp\.|Ltd\.?)\s*$", "", raw_name, flags=_re.I).strip() or raw_name

        return {
            "ticker":           ticker,
            "name":             name,
            "consensus_target": {"avg": avg, "high": 0, "low": 0},
            "recommendation":   "N/A",
        }
    except Exception as e:
        print(f"[consensus] US 컨센서스 조회 실패 ({ticker}): {e}")
        return None


def _insert_consensus_history(kr_data: dict, us_data: dict):
    """수집된 컨센서스를 consensus_history 테이블에 UPSERT."""
    today = datetime.now(KST).strftime("%Y%m%d")
    now_str = datetime.now(KST).isoformat()
    rows = []
    for symbol, entry in kr_data.items():
        avg = entry.get("avg")
        if not avg:
            continue
        rows.append((
            today, symbol,
            float(avg), float(entry.get("high", 0) or 0), float(entry.get("low", 0) or 0),
            int(entry.get("buy", 0) or 0), int(entry.get("hold", 0) or 0), int(entry.get("sell", 0) or 0),
            now_str,
        ))
    if not rows:
        return
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA cache_size = -65536")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA mmap_size = 268435456")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.executemany("""
            INSERT INTO consensus_history
            (trade_date, symbol, target_avg, target_high, target_low, buy_count, hold_count, sell_count, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, symbol) DO UPDATE SET
                target_avg=excluded.target_avg, target_high=excluded.target_high, target_low=excluded.target_low,
                buy_count=excluded.buy_count, hold_count=excluded.hold_count, sell_count=excluded.sell_count,
                collected_at=excluded.collected_at
        """, rows)
        conn.commit()
        conn.close()
        print(f"[consensus_history] {len(rows)}건 저장 ({today})")
    except Exception as e:
        print(f"[consensus_history] DB 저장 실패: {e}")


async def update_consensus_cache(kr_tickers: dict | None = None) -> dict:
    """포트폴리오+워치리스트 전체 컨센서스를 배치 수집해 consensus_cache.json에 저장.
    기존 avg는 prev_avg로 보존해 주간 변동 추적 가능.
    실패 종목은 기존 캐시 유지.

    Args:
        kr_tickers: {ticker: name} 형태로 전달하면 해당 KR 종목만 수집 (부분 업데이트).
                    None이면 portfolio+watchlist 전체 자동 결정.
                    kr_tickers가 주어지면 US 섹션은 기존 캐시를 그대로 유지.
    """
    import asyncio as _aio
    old_cache = load_json(CONSENSUS_CACHE_FILE, {})
    old_kr = old_cache.get("kr", {})
    old_us = old_cache.get("us", {})

    partial_mode = kr_tickers is not None  # True면 kr만 갱신

    if not partial_mode:
        # 수집 대상 티커 자동 결정
        portfolio = load_json(PORTFOLIO_FILE, {})
        kr_tickers = {
            t: (v.get("name", t) if isinstance(v, dict) else t)
            for t, v in portfolio.items()
            if t != "us_stocks" and not _is_us_ticker(t)
        }
        us_tickers: dict = {
            t: (v.get("name", t) if isinstance(v, dict) else t)
            for t, v in portfolio.get("us_stocks", {}).items()
        }
        # 한국 워치리스트 추가
        for t, n in load_watchlist().items():
            if t not in kr_tickers and not _is_us_ticker(t):
                kr_tickers[t] = n
        # 미국 워치리스트 추가
        for t, v in load_us_watchlist().items():
            if t not in us_tickers:
                us_tickers[t] = v.get("name", t) if isinstance(v, dict) else str(v)
    else:
        us_tickers = {}  # 부분 업데이트 시 US 수집 건너뜀

    loop = _aio.get_event_loop()

    # 한국 컨센서스 (FnGuide, 동기 → executor)
    new_kr: dict = {}
    for ticker in kr_tickers:
        try:
            c = await _aio.wait_for(
                loop.run_in_executor(None, fetch_fnguide_consensus, ticker),
                timeout=10.0,
            )
            avg = int((c.get("consensus_target") or {}).get("avg", 0)) if c else 0
            if avg:
                old_entry = old_kr.get(ticker, {})
                old_avg   = old_entry.get("avg")
                entry = {
                    "name": c.get("name") or kr_tickers.get(ticker, ticker),
                    "avg":  avg,
                    "high": int((c.get("consensus_target") or {}).get("high", 0)),
                    "low":  int((c.get("consensus_target") or {}).get("low",  0)),
                    "buy":  int((c.get("opinion") or {}).get("buy",  0)),
                    "hold": int((c.get("opinion") or {}).get("hold", 0)),
                    "sell": int((c.get("opinion") or {}).get("sell", 0)),
                }
                if old_avg and int(old_avg) != avg:
                    entry["prev_avg"] = old_avg
                elif old_avg:
                    entry["prev_avg"] = old_entry.get("prev_avg")
                new_kr[ticker] = entry
            elif ticker in old_kr:
                new_kr[ticker] = old_kr[ticker]
        except Exception as _e:
            print(f"[consensus_cache] KR {ticker} 실패: {_e}")
            if ticker in old_kr:
                new_kr[ticker] = old_kr[ticker]
        await _aio.sleep(0.5)

    if partial_mode:
        # 부분 업데이트: kr 섹션만 덮어쓰고 us는 기존 캐시 유지
        merged_kr = {**old_kr, **new_kr}
        cache = {
            "updated": datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S%z"),
            "kr": merged_kr,
            "us": old_us,
        }
        save_json(CONSENSUS_CACHE_FILE, cache)
        from db_collector import db_write_lock
        async with db_write_lock:
            _insert_consensus_history(new_kr, {})
        print(f"[consensus_cache] 부분 저장 완료: KR {len(new_kr)}종목 갱신 (전체 {len(merged_kr)})")
        return cache

    # 미국 컨센서스 (Nasdaq.com, 동기 → executor)
    new_us: dict = {}
    for ticker in us_tickers:
        try:
            c = await _aio.wait_for(
                loop.run_in_executor(None, get_us_consensus, ticker),
                timeout=10.0,
            )
            avg = float((c.get("consensus_target") or {}).get("avg", 0)) if c else 0.0
            if avg:
                old_entry = old_us.get(ticker, {})
                old_avg   = old_entry.get("avg")
                entry = {
                    "name": c.get("name", ticker),
                    "avg":  round(avg, 2),
                }
                if old_avg and round(float(old_avg), 2) != round(avg, 2):
                    entry["prev_avg"] = old_avg
                elif old_avg:
                    entry["prev_avg"] = old_entry.get("prev_avg")
                new_us[ticker] = entry
            elif ticker in old_us:
                new_us[ticker] = old_us[ticker]
        except Exception as _e:
            print(f"[consensus_cache] US {ticker} 실패: {_e}")
            if ticker in old_us:
                new_us[ticker] = old_us[ticker]
        await _aio.sleep(0.5)

    cache = {
        "updated": datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kr": new_kr,
        "us": new_us,
    }
    save_json(CONSENSUS_CACHE_FILE, cache)
    from db_collector import db_write_lock
    async with db_write_lock:
        _insert_consensus_history(new_kr, new_us)
    print(f"[consensus_cache] 저장 완료: KR {len(new_kr)}종목, US {len(new_us)}종목")
    return cache


def detect_consensus_changes(old_kr: dict, new_kr: dict, target_pct: float = 5.0, detect_new_cover: bool = False) -> list:
    """컨센서스 변화 감지.
    Returns: [{"ticker", "name", "type", "detail"}, ...]
    type: "target_up" / "target_down" / "opinion_change" / "new_cover"
    """
    changes = []
    for ticker, new_data in new_kr.items():
        old_data = old_kr.get(ticker)
        new_avg = new_data.get("avg", 0) or 0

        if old_data is None:
            if detect_new_cover and new_avg > 0:
                changes.append({"ticker": ticker, "name": new_data.get("name", ticker),
                               "type": "new_cover", "detail": f"목표가 {new_avg:,.0f}"})
            continue

        old_avg = old_data.get("avg", 0) or 0
        if old_avg > 0 and new_avg > 0:
            pct = (new_avg - old_avg) / old_avg * 100
            if pct >= target_pct:
                changes.append({"ticker": ticker, "name": new_data.get("name", ticker),
                               "type": "target_up", "detail": f"{old_avg:,.0f}→{new_avg:,.0f} (+{pct:.1f}%)"})
            elif pct <= -target_pct:
                changes.append({"ticker": ticker, "name": new_data.get("name", ticker),
                               "type": "target_down", "detail": f"{old_avg:,.0f}→{new_avg:,.0f} ({pct:.1f}%)"})

        # 투자의견 변경
        def _dominant(d):
            b, h, s = d.get("buy", 0), d.get("hold", 0), d.get("sell", 0)
            if b >= h and b >= s and b > 0: return "매수"
            if s >= b and s >= h and s > 0: return "매도"
            return "중립"
        old_op = _dominant(old_data)
        new_op = _dominant(new_data)
        if old_op != new_op:
            changes.append({"ticker": ticker, "name": new_data.get("name", ticker),
                           "type": "opinion_change", "detail": f"{old_op}→{new_op}"})

    return changes


