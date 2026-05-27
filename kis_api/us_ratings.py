"""미국 애널리스트 레이팅 수집 (StockAnalysis)."""
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


def _decode_sveltekit_ratings(raw: dict) -> dict | None:
    """SvelteKit __data.json 자기참조 배열을 구 JSON API 응답 구조로 변환.
    반환: {status:200, data:{widget:{all:{...}}, ratings:[...]}} 또는 None.
    """
    try:
        nodes = raw.get("nodes", [])
        node2 = next(
            (n for n in nodes
             if n.get("type") == "data"
             and isinstance(n.get("data", [None])[0], dict)
             and "ratings" in n.get("data", [{}])[0]),
            None,
        )
        if node2 is None:
            return None
        d = node2["data"]
        root = d[0]

        def resolve(idx):
            if not isinstance(idx, int) or idx < 0 or idx >= len(d):
                return None
            v = d[idx]
            if isinstance(v, dict):
                return {k: resolve(vi) for k, vi in v.items()}
            if isinstance(v, list):
                return [resolve(i) for i in v]
            return v

        return {
            "status": 200,
            "data": {
                "widget": resolve(root["widget"]),
                "ratings": resolve(root["ratings"]),
            },
        }
    except Exception:
        return None


async def _stockanalysis_ratings(ticker: str) -> dict | None:
    """StockAnalysis.com SvelteKit __data.json 엔드포인트. 반환: 정규화 dict 또는 None.
    주의: 2초 sleep은 호출자가 관리.
    구 api.stockanalysis.com/api/symbol/s/{ticker}/ratings 는 2026-05 이후 404.
    """
    url = f"https://stockanalysis.com/stocks/{ticker.lower()}/ratings/__data.json"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status in (429, 403):
                    print(f"[stockanalysis] {ticker} rate limited/blocked ({resp.status}), 30s 백오프")
                    await asyncio.sleep(30)
                    return None
                if resp.status != 200:
                    print(f"[stockanalysis] {ticker} HTTP {resp.status}")
                    return None
                raw = await resp.json(content_type=None)
                data = _decode_sveltekit_ratings(raw)
                if data is None:
                    return None
                return _normalize_stockanalysis_response(ticker, data)
    except Exception as e:
        print(f"[stockanalysis] {ticker} {type(e).__name__}: {e}")
        return None


def _normalize_stockanalysis_response(ticker: str, raw: dict) -> dict:
    """응답을 flat 구조로 정규화.
    pt_change_pct = (pt_now - pt_old) / pt_old * 100 (pt_old > 0 일 때만)
    """
    widget = raw.get("data", {}).get("widget", {}).get("all", {}) or {}
    ratings_raw = raw.get("data", {}).get("ratings", []) or []
    ratings = []
    for r in ratings_raw:
        pt_now = r.get("pt_now")
        pt_old = r.get("pt_old")
        pt_change_pct = None
        if pt_now and pt_old and pt_old > 0:
            pt_change_pct = (pt_now - pt_old) / pt_old * 100
        scores = r.get("scores") or {}
        ratings.append({
            "date": r.get("date"),
            "time": r.get("time"),
            "firm": r.get("firm"),
            "analyst": r.get("analyst"),
            "slug": r.get("slug"),
            "action": r.get("action_rt"),
            "rating_new": r.get("rating_new"),
            "rating_old": r.get("rating_old"),
            "pt_now": pt_now,
            "pt_old": pt_old,
            "pt_change_pct": pt_change_pct,
            "stars": scores.get("stars"),
            "success_rate": scores.get("success_rate"),
            "avg_return": scores.get("avg_return"),
            "total_ratings": scores.get("total"),
        })
    return {
        "ticker": ticker.upper(),
        "consensus": {
            "count": widget.get("count", 0),
            "rating": widget.get("consensus"),
            "target": widget.get("price_target"),
        },
        "ratings": ratings,
    }


def _save_us_ratings_to_db(data: dict) -> int:
    """INSERT OR IGNORE (UNIQUE 제약). 반환: 신규 insert 건수.
    db_collector._get_db() 로 연결. fetched_at = datetime.now().isoformat().
    """
    from db_collector import _get_db
    conn = _get_db()
    inserted = 0
    try:
        now_iso = datetime.now().isoformat()
        ticker = data["ticker"]
        for r in data.get("ratings", []):
            cur = conn.execute(
                "INSERT OR IGNORE INTO us_analyst_ratings "
                "(ticker, rating_date, rating_time, firm, analyst, analyst_slug, action, "
                " rating_new, rating_old, pt_now, pt_old, pt_change_pct, "
                " stars, success_rate, avg_return, total_ratings, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, r.get("date"), r.get("time"), r.get("firm"),
                 r.get("analyst"), r.get("slug"), r.get("action"),
                 r.get("rating_new"), r.get("rating_old"),
                 r.get("pt_now"), r.get("pt_old"), r.get("pt_change_pct"),
                 r.get("stars"), r.get("success_rate"),
                 r.get("avg_return"), r.get("total_ratings"), now_iso)
            )
            if cur.rowcount > 0:
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def _save_consensus_snapshot(data: dict) -> None:
    """일일 컨센 스냅샷 (INSERT OR REPLACE). snapshot_date = KST 오늘."""
    from db_collector import _get_db
    conn = _get_db()
    try:
        snap_date = datetime.now(KST).strftime("%Y-%m-%d")
        c = data.get("consensus", {}) or {}
        conn.execute(
            "INSERT OR REPLACE INTO us_consensus_snapshot "
            "(ticker, snapshot_date, analyst_count, consensus_rating, target_avg) "
            "VALUES (?, ?, ?, ?, ?)",
            (data["ticker"], snap_date, c.get("count"), c.get("rating"), c.get("target"))
        )
        conn.commit()
    finally:
        conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 미국 인덱스 유니버스 (S&P 500 / Russell 1000) — 주간 스캔용
# ━━━━━━━━━━━━━━━━━━━━━━━━━
US_SP500_FILE = f"{_DATA_DIR}/us_sp500.json"
US_RUSSELL1000_FILE = f"{_DATA_DIR}/us_russell1000.json"
_US_INDEX_MAX_AGE_DAYS = 30  # 한 달 이상 오래되면 자동 갱신
_SP500_MAX_AGE_DAYS = _US_INDEX_MAX_AGE_DAYS  # 하위 호환 별칭


def _fetch_index_tickers_from_wikipedia(
    url: str,
    *,
    ticker_col_idx: int,
    min_size: int,
    table_id: str | None,
    log_prefix: str,
) -> list[str] | None:
    """Wikipedia 인덱스 페이지 파싱 공통 헬퍼 (S&P 500 / Russell 1000 공용).

    Args:
        url: Wikipedia 페이지 URL.
        ticker_col_idx: 티커가 있는 td 컬럼 인덱스 (S&P 500 = 0, Russell 1000 = 1).
        min_size: 파싱 결과 최소 기대 종목 수. 미만이면 비정상으로 간주.
        table_id: `<table id="...">` 지정 시 우선 탐색. None 이면 첫 wikitable 사용.
        log_prefix: 로그 태그 (예: "sp500", "russell1000").

    Returns:
        티커 리스트 (대문자, BRK.B / BF.B 처럼 점(.) 포함 티커는 그대로 유지).
        파싱 실패 시 None.
    """
    import requests as _req
    from bs4 import BeautifulSoup
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        resp = _req.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"[{log_prefix}] wikipedia HTTP {resp.status_code}")
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        table = None
        if table_id:
            table = soup.find("table", {"id": table_id})
            if table is None:
                # 원래 S&P 500 로직 보존 — id 없으면 첫 wikitable
                table = soup.find("table", {"class": "wikitable"})
        else:
            # Russell 1000 처럼 id 없는 페이지는 구성종목 테이블이 첫 wikitable 이 아닐 수 있음
            # (보통 2번째). 티커 컬럼이 유효한 가장 큰 wikitable 자동 선택.
            wikitables = soup.find_all("table", {"class": "wikitable"})
            for candidate in wikitables:
                rows = candidate.find_all("tr")
                if len(rows) < max(min_size // 2, 50):
                    continue  # 너무 작은 표는 스킵
                first_data = rows[1] if len(rows) > 1 else None
                if first_data is None:
                    continue
                tds = first_data.find_all("td")
                if len(tds) > ticker_col_idx:
                    table = candidate
                    break
        if table is None:
            print(f"[{log_prefix}] wikipedia 구성종목 테이블을 찾을 수 없음")
            return None
        tickers: list[str] = []
        tbody = table.find("tbody") or table
        for tr in tbody.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) <= ticker_col_idx:
                continue
            t = tds[ticker_col_idx].get_text(strip=True)
            if t and len(t) <= 10:
                tickers.append(t.upper())
        if len(tickers) < min_size:
            print(f"[{log_prefix}] 파싱 결과 비정상 ({len(tickers)}개, 최소 {min_size})")
            return None
        return tickers
    except Exception as e:
        print(f"[{log_prefix}] wikipedia fetch 실패: {type(e).__name__}: {e}")
        return None


def _load_index_tickers(
    cache_file: str,
    *,
    fetcher,
    log_prefix: str,
    force_refresh: bool,
    max_age_days: int = _US_INDEX_MAX_AGE_DAYS,
) -> list[str]:
    """인덱스 티커 로더 공통 헬퍼 (캐시 + TTL + fallback 공용 로직).

    Args:
        cache_file: 로컬 JSON 캐시 경로.
        fetcher: 인자 없이 호출 시 티커 리스트(list[str]) 또는 None 반환하는 callable.
        log_prefix: 로그 태그.
        force_refresh: True 면 캐시 유효해도 강제 네트워크 재수집.
        max_age_days: 캐시 TTL (기본 30일).
    """
    try:
        need_refresh = force_refresh
        if not need_refresh:
            if not os.path.exists(cache_file):
                need_refresh = True
            else:
                age_days = (datetime.now().timestamp() - os.path.getmtime(cache_file)) / 86400
                if age_days > max_age_days:
                    need_refresh = True
        if need_refresh:
            tickers = fetcher()
            if tickers:
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump({"updated": datetime.now().isoformat(), "tickers": tickers}, f, ensure_ascii=False, indent=2)
                    print(f"[{log_prefix}] 캐시 갱신: {len(tickers)}개 → {cache_file}")
                except Exception as e:
                    print(f"[{log_prefix}] 캐시 저장 실패: {e}")
                return tickers
            else:
                print(f"[{log_prefix}] Wikipedia 갱신 실패, 기존 캐시 fallback")
        # 캐시 읽기
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return list(data.get("tickers", []))
    except Exception as e:
        print(f"[{log_prefix}] load 실패: {type(e).__name__}: {e}")
    return []


def _fetch_sp500_from_wikipedia() -> list[str] | None:
    """Wikipedia S&P 500 페이지 파싱 → 티커 리스트 반환.
    실패 시 None. BRK.B / BF.B 처럼 점(.)이 들어간 티커는 그대로 반환 (StockAnalysis.com 호환).
    """
    return _fetch_index_tickers_from_wikipedia(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        ticker_col_idx=0,
        min_size=400,
        table_id="constituents",
        log_prefix="sp500",
    )


def _fetch_russell1000_from_wikipedia() -> list[str] | None:
    """Wikipedia Russell 1000 페이지 파싱 → 티커 리스트 반환.
    실패 시 None. Russell 1000 위키 표는 2번째 컬럼이 티커 (index 1).
    """
    return _fetch_index_tickers_from_wikipedia(
        "https://en.wikipedia.org/wiki/Russell_1000_Index",
        ticker_col_idx=1,
        min_size=900,
        table_id=None,
        log_prefix="russell1000",
    )


def load_sp500_tickers(force_refresh: bool = False) -> list[str]:
    """S&P 500 티커 리스트 로더.
    - `data/us_sp500.json` 캐시 파일 사용.
    - 파일 없거나 mtime 이 30일 이상 오래되면 Wikipedia 에서 자동 갱신.
    - 네트워크 실패 시 기존 캐시(있으면) 반환, 없으면 빈 리스트.
    """
    return _load_index_tickers(
        US_SP500_FILE,
        fetcher=_fetch_sp500_from_wikipedia,
        log_prefix="sp500",
        force_refresh=force_refresh,
    )


def load_russell1000_tickers(force_refresh: bool = False) -> list[str]:
    """Russell 1000 (대형+중형주 1000개) 티커 리스트 로더.
    - `data/us_russell1000.json` 캐시 파일 사용.
    - 파일 없거나 mtime 이 30일 이상 오래되면 Wikipedia 에서 자동 갱신.
    - 네트워크 실패 시 기존 캐시(있으면) 반환, 없으면 빈 리스트.
    - 파싱 결과 900개 미만이면 비정상으로 간주 (Russell 1000 인덱스는 ~1000개 구성).
    """
    return _load_index_tickers(
        US_RUSSELL1000_FILE,
        fetcher=_fetch_russell1000_from_wikipedia,
        log_prefix="russell1000",
        force_refresh=force_refresh,
    )


def load_us_scan_universe() -> list[str]:
    """주간 US 레이팅 스캔 유니버스 = S&P 500 ∪ Russell 1000 합집합 (정렬된 리스트).
    - 둘 중 하나가 실패해도 나머지라도 반환 (방어적).
    - 중복 제거 + 정렬 후 반환.
    """
    merged: set[str] = set()
    try:
        sp = load_sp500_tickers()
        if sp:
            merged.update(sp)
    except Exception as e:
        print(f"[us_universe] S&P 500 로드 실패: {type(e).__name__}: {e}")
    try:
        rs = load_russell1000_tickers()
        if rs:
            merged.update(rs)
    except Exception as e:
        print(f"[us_universe] Russell 1000 로드 실패: {type(e).__name__}: {e}")
    return sorted(merged)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# StockAnalysis.com 애널 메타 + HTML 파싱 (3단계)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def _fetch_analyst_coverage_html(slug: str) -> dict | None:
    """StockAnalysis.com 애널 페이지 HTML 파싱.
    URL: https://stockanalysis.com/analysts/{slug}/
    반환: {
        "slug": str, "name": str, "firm": str,
        "stars": float, "success_rate": float, "total_ratings": int,
        "coverage": [{"ticker": str, "sector": str}]  # 애널이 커버하는 종목
    }
    실패 시 None.
    주의: 호출자가 2초 sleep 관리.
    """
    from bs4 import BeautifulSoup
    import re
    url = f"https://stockanalysis.com/analysts/{slug}/"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    print(f"[analyst_html] {slug} HTTP {resp.status}")
                    return None
                html = await resp.text()
        soup = BeautifulSoup(html, "lxml")

        # 이름 추출 — h1 또는 페이지 타이틀
        name_tag = soup.find("h1")
        name = name_tag.get_text(strip=True) if name_tag else slug.replace("-", " ").title()

        # 메타 정보 (firm, stars, success_rate, total_ratings)
        firm = None
        stars = None
        success_rate = None
        total_ratings = None

        # 1차: 페이지 내 embedded JSON payload (가장 안정적)
        #   예: data:{firm:"JP Morgan",name:"Mark Strouse",count:318,
        #           scores:{score:73.62,stars:4.59,total:227,...,success_rate:54.19}
        try:
            m_firm = re.search(r'firm:"([^"]{1,80})"', html)
            if m_firm:
                firm = m_firm.group(1)
            m_stars = re.search(r'stars:\s*([0-9]+\.?[0-9]*)', html)
            if m_stars:
                stars = float(m_stars.group(1))
            m_succ = re.search(r'success_rate:\s*([0-9]+\.?[0-9]*)', html)
            if m_succ:
                success_rate = float(m_succ.group(1))
            # JSON 내 count:N (페이지 "Total ratings" 표시값). scores.total 과 다름.
            m_total = re.search(r'\bcount:\s*(\d+)', html)
            if m_total:
                total_ratings = int(m_total.group(1))
        except Exception:
            pass

        # 2차 fallback: DOM 기반 ("Stock Analyst at {firm}", aria-label stars, "Total ratings")
        if not firm:
            for p in soup.find_all(["p", "span", "div"]):
                txt = p.get_text(" ", strip=True)
                m = re.search(r"Stock Analyst at\s+(.+)", txt)
                if m:
                    firm = m.group(1).strip()[:80]
                    break
        if stars is None:
            st = soup.find(attrs={"aria-label": re.compile(r"Rated\s+[\d.]+\s+out of 5 stars")})
            if st:
                m = re.search(r"Rated\s+([\d.]+)", st.get("aria-label", ""))
                if m:
                    stars = float(m.group(1))
        if total_ratings is None:
            # DOM: <div>318</div><div>Total ratings</div>
            for d in soup.find_all("div"):
                if d.get_text(strip=True).lower() == "total ratings":
                    prev = d.find_previous_sibling("div")
                    if prev:
                        m = re.search(r"(\d+)", prev.get_text(strip=True))
                        if m:
                            total_ratings = int(m.group(1))
                            break

        # Coverage — 레이팅 테이블의 unique 티커 추출 (2번째 셀 <a> 태그).
        # stockanalysis.com 애널 페이지는 섹터 컬럼이 없으므로 sector=None.
        coverage = []
        seen_tickers = set()
        for table in soup.find_all("table"):
            thead = table.find("thead")
            if not thead:
                continue
            htxt = thead.get_text(" ", strip=True).lower()
            if not any(kw in htxt for kw in ("symbol", "ticker", "stock")):
                continue
            tbody = table.find("tbody")
            if not tbody:
                continue
            for row in tbody.find_all("tr"):
                cells = row.find_all("td")
                ticker_val = None
                # 우선 각 셀의 첫 <a> 태그 텍스트가 유효 티커면 사용
                for c in cells:
                    a = c.find("a")
                    if a:
                        cand = a.get_text(strip=True).upper()
                        if 1 <= len(cand) <= 5 and cand.isalpha():
                            ticker_val = cand
                            break
                # fallback: 셀 full 텍스트 첫 토큰
                if not ticker_val:
                    for c in cells:
                        tokens = c.get_text(" ", strip=True).split()
                        if tokens:
                            cand = tokens[0].upper()
                            if 1 <= len(cand) <= 5 and cand.isalpha():
                                ticker_val = cand
                                break
                # XXXX = StockAnalysis.com 무료 페이월 마스킹 (10개 이후 티커 숨김). 스킵.
                if ticker_val and ticker_val != "XXXX" and ticker_val not in seen_tickers:
                    seen_tickers.add(ticker_val)
                    coverage.append({"ticker": ticker_val, "sector": None})
            break  # 첫 매칭 테이블만

        return {
            "slug": slug,
            "name": name,
            "firm": firm,
            "stars": stars,
            "success_rate": success_rate,
            "total_ratings": total_ratings,
            "coverage": coverage,
        }
    except Exception as e:
        print(f"[analyst_html] {slug} {type(e).__name__}: {e}")
        return None


def _upsert_analyst_meta(data: dict) -> None:
    """us_analysts 에 메타 UPSERT (기존 watched 플래그 보존)."""
    import json as _json
    from db_collector import _get_db
    conn = _get_db()
    try:
        slug = data["slug"]
        # sectors 는 coverage 의 sector 중 unique 를 JSON 배열로
        sectors = sorted({c.get("sector") for c in data.get("coverage", []) if c.get("sector")})
        sectors_json = _json.dumps(sectors, ensure_ascii=False)
        # watched 보존 UPSERT
        conn.execute(
            "INSERT INTO us_analysts (slug, name, firm, sectors, stars, success_rate, "
            " total_ratings, watched, curated_at, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?) "
            "ON CONFLICT(slug) DO UPDATE SET "
            " name=excluded.name, firm=excluded.firm, sectors=excluded.sectors, "
            " stars=excluded.stars, success_rate=excluded.success_rate, "
            " total_ratings=excluded.total_ratings, last_updated=excluded.last_updated",
            (slug, data.get("name"), data.get("firm"), sectors_json,
             data.get("stars"), data.get("success_rate"), data.get("total_ratings"),
             datetime.now().isoformat())
        )
        # coverage UPSERT
        for cov in data.get("coverage", []):
            conn.execute(
                "INSERT OR REPLACE INTO us_analyst_coverage "
                "(analyst_slug, ticker, sector, last_seen) VALUES (?, ?, ?, ?)",
                (slug, cov["ticker"], cov.get("sector"), datetime.now().strftime("%Y-%m-%d"))
            )
        conn.commit()
    finally:
        conn.close()


async def build_top_analysts_candidates(limit: int = 100, days: int = 180) -> list:
    """us_analyst_ratings 집계로 톱 N 후보 생성.
    stars * log(count) 가중치 정렬.
    watched=1 플래그 자동 설정 안 함 — 사용자 확정 대기.

    반환: [{slug, name, firm, avg_stars, avg_success_rate, call_count, score}, ...]
    """
    from db_collector import _get_db
    import math
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT analyst_slug, analyst, firm, "
            "       AVG(stars), AVG(success_rate), COUNT(*) "
            "FROM us_analyst_ratings "
            "WHERE analyst_slug IS NOT NULL AND stars IS NOT NULL "
            "  AND rating_date >= date('now', ?) "
            "GROUP BY analyst_slug HAVING COUNT(*) >= 5 AND AVG(stars) >= 3.5 "
            "ORDER BY AVG(stars) DESC",
            (f"-{days} days",)
        ).fetchall()
        candidates = []
        for r in rows:
            slug, name, firm, avg_s, avg_sr, cnt = r
            score = (avg_s or 0) * math.log((cnt or 0) + 1)
            candidates.append({
                "slug": slug, "name": name, "firm": firm,
                "avg_stars": avg_s, "avg_success_rate": avg_sr,
                "call_count": cnt, "score": round(score, 2)
            })
        # score 순 정렬
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:limit]
    finally:
        conn.close()


async def fetch_and_store_analyst_meta(slug: str) -> bool:
    """단일 애널 HTML 파싱 + us_analysts/coverage UPSERT. 성공 True."""
    data = await _fetch_analyst_coverage_html(slug)
    if not data:
        return False
    _upsert_analyst_meta(data)
    return True


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 미국 애널 레이팅 — 보유 감시 알림 중복 방지 저장소
# ━━━━━━━━━━━━━━━━━━━━━━━━━
