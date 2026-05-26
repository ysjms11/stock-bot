"""Polymarket 예측 시장 + Treasury 수익률 곡선."""
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


async def fetch_polymarket(top: int = 10, min_volume: float = 500_000,
                            query: str = "") -> dict:
    """Polymarket 매크로/지정학/정치 prediction market 조회.

    24시간 거래량 정렬, sports/esports/pop culture 노이즈 자동 컷,
    min_volume 이하 제외 (저거래량 = 노이즈).

    Args:
        top: 반환 시장 수 (기본 10)
        min_volume: 최소 누적 거래량 USD (기본 500K)
        query: 키워드 (예: "Fed", "Iran", "Trump") 시 제목·설명 매칭 필터

    Returns:
        {"markets": [{title, prob_yes, prob_no, change_24h, change_7d, volume, vol_24h, end_date, tags}], "fetched_at": ...}
    """
    url = f"{POLYMARKET_API}/events"
    params = {
        "limit": "100",
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
    }
    # ssl=False — read-only public API, macOS aiohttp 인증서 이슈 우회
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        try:
            async with s.get(url, params=params,
                             timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return {"error": f"Polymarket HTTP {r.status}"}
                data = await r.json()
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    if not isinstance(data, list):
        return {"error": "unexpected response", "raw": str(data)[:200]}

    query_lower = (query or "").lower()
    results = []
    for ev in data:
        # 노이즈 태그 1개라도 있으면 제외
        tags = [t.get("label", "") for t in ev.get("tags", [])]
        if any(n in tags for n in _POLY_NOISE_TAGS):
            continue
        # 거래량 필터
        vol_total = float(ev.get("volume", 0) or 0)
        if vol_total < min_volume:
            continue
        # 키워드 필터
        title = ev.get("title", "")
        if query_lower:
            desc_blob = (title + " " + (ev.get("description") or "")[:300]).lower()
            if query_lower not in desc_blob:
                continue
        # 멀티 아웃컴 이벤트: 모든 sub-market의 (그룹제목, YES 확률, 변동) 추출
        markets = ev.get("markets", [])
        outcomes = []
        import json as _j
        for m in markets:
            try:
                op = m.get("outcomePrices", "[]")
                if isinstance(op, str):
                    op = _j.loads(op)
                if not op or len(op) < 1:
                    continue
                yes_prob = float(op[0])
                # group title (예: "Hold rates", "Cut 25bp")
                grp = (m.get("groupItemTitle") or m.get("question") or "").strip()
                chg_24h = float(m.get("oneDayPriceChange", 0) or 0) if m.get("oneDayPriceChange") is not None else None
                chg_7d = float(m.get("oneWeekPriceChange", 0) or 0) if m.get("oneWeekPriceChange") is not None else None
                outcomes.append({
                    "outcome": grp[:60],
                    "prob": round(yes_prob, 4),
                    "change_24h": round(chg_24h, 4) if chg_24h is not None else None,
                    "change_7d": round(chg_7d, 4) if chg_7d is not None else None,
                })
            except Exception:
                pass

        # 확률 내림차순 정렬, 가장 높은 거 우선
        outcomes.sort(key=lambda x: x.get("prob", 0), reverse=True)
        # binary 시장 (Yes/No 1개): outcomes[0]만 의미. 멀티: 상위 5개 표시
        is_binary = (len(outcomes) == 1)
        top_outcome = outcomes[0] if outcomes else None

        results.append({
            "title": title,
            "is_binary": is_binary,
            "top_outcome": top_outcome,  # 가장 가능성 큰 결과
            "outcomes": outcomes[:5],  # 멀티시 상위 5개
            "vol_total": vol_total,
            "vol_24h": float(ev.get("volume24hr", 0) or 0),
            "vol_1wk": float(ev.get("volume1wk", 0) or 0),
            "end_date": (ev.get("endDate", "") or "")[:10],
            "tags": tags[:4],
            "slug": ev.get("slug", ""),
        })
        if len(results) >= top:
            break

    return {
        "count": len(results),
        "min_volume": min_volume,
        "query": query,
        "fetched_at": datetime.now(KST).isoformat(),
        "markets": results,
    }


async def fetch_treasury_curve() -> dict:
    """미국 Treasury 수익률 곡선 — 침체 시그널 (10Y-2Y, 10Y-3M).

    FRED 공개 CSV (no API key) 사용. 최근 5거래일 데이터.
    역전 (10Y-2Y < 0) = Estrella-Mishkin 1998 NY Fed 침체 선행지표.

    Returns:
        {"yields": {"10y", "2y", "3m"}, "spreads": {"10y_2y", "10y_3m"},
         "spreads_1w_ago", "recession_signal": "정상/주의/역전"}
    """
    series = {"10y": "DGS10", "2y": "DGS2", "3m": "DGS3MO"}
    yields = {}
    yields_1w_ago = {}

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        for key, sid in series.items():
            url = f"{FRED_BASE}?id={sid}"
            try:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        continue
                    text = await r.text()
            except Exception:
                continue
            # CSV 파싱: 첫 줄 헤더, 마지막 N줄 = 최근 데이터
            lines = [ln for ln in text.strip().split("\n")[1:] if "," in ln]
            if not lines:
                continue
            # 최근 비결측 값
            for ln in reversed(lines):
                parts = ln.split(",")
                if len(parts) >= 2 and parts[1] not in (".", "", "NA"):
                    try:
                        yields[key] = float(parts[1])
                        break
                    except Exception:
                        pass
            # 1주 전 (5영업일 전 정도) 비결측
            if len(lines) >= 7:
                for ln in reversed(lines[:-5]):
                    parts = ln.split(",")
                    if len(parts) >= 2 and parts[1] not in (".", "", "NA"):
                        try:
                            yields_1w_ago[key] = float(parts[1])
                            break
                        except Exception:
                            pass

    spread_10y_2y = None
    spread_10y_3m = None
    spread_10y_2y_1w = None
    if "10y" in yields and "2y" in yields:
        spread_10y_2y = round(yields["10y"] - yields["2y"], 3)
    if "10y" in yields and "3m" in yields:
        spread_10y_3m = round(yields["10y"] - yields["3m"], 3)
    if "10y" in yields_1w_ago and "2y" in yields_1w_ago:
        spread_10y_2y_1w = round(yields_1w_ago["10y"] - yields_1w_ago["2y"], 3)

    # 침체 시그널 (Estrella-Mishkin 1998)
    if spread_10y_2y is None:
        signal = "데이터 부족"
    elif spread_10y_2y < 0:
        signal = "역전 (침체 선행)"
    elif spread_10y_2y < 0.25:
        signal = "주의 (역전 임박)"
    else:
        signal = "정상"

    return {
        "yields": yields,
        "yields_1w_ago": yields_1w_ago,
        "spread_10y_2y": spread_10y_2y,
        "spread_10y_3m": spread_10y_3m,
        "spread_10y_2y_1w_ago": spread_10y_2y_1w,
        "recession_signal": signal,
        "fetched_at": datetime.now(KST).isoformat(),
    }


def _ensure_pension_table(db_path: str):
    """pension_flow_daily 테이블 생성 (idempotent)."""
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pension_flow_daily (
            trade_date     TEXT NOT NULL,
            symbol         TEXT NOT NULL,
            market         TEXT DEFAULT '',
            name           TEXT DEFAULT '',
            net_amount_won INTEGER DEFAULT 0,
            net_qty        INTEGER DEFAULT 0,
            buy_amount_won INTEGER DEFAULT 0,
            sell_amount_won INTEGER DEFAULT 0,
            collected_at   TEXT DEFAULT '',
            PRIMARY KEY (trade_date, symbol)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pf_date ON pension_flow_daily(trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pf_symbol ON pension_flow_daily(symbol)")
    conn.commit()
    conn.close()


def collect_pension_flow_daily(date_str: str = None) -> dict:
    """매일 16:30 KST — 그날 종목별 연기금 매매 수집 → pension_flow_daily DB INSERT.

    Args:
        date_str: YYYYMMDD 형식. 생략 시 오늘.

    Returns:
        {"date": str, "kospi_count": int, "kosdaq_count": int, "saved": int}
    """
    try:
        from pykrx import stock as _krx
    except ImportError:
        return {"error": "pykrx 미설치"}

    if date_str is None:
        date_str = datetime.now(KST).strftime("%Y%m%d")

    db_path = f"{_DATA_DIR}/stock.db"
    _ensure_pension_table(db_path)

    saved = 0
    counts = {}
    import sqlite3 as _s
    conn = _s.connect(db_path, timeout=30)
    now_iso = datetime.now(KST).isoformat()

    for m in ["KOSPI", "KOSDAQ"]:
        try:
            df = _krx.get_market_net_purchases_of_equities(date_str, date_str, m, "연기금")
        except Exception as e:
            print(f"[pension_flow] {m} {date_str} 실패: {e}")
            counts[m] = 0
            continue
        if df is None or len(df) == 0:
            counts[m] = 0
            continue
        cnt = 0
        for ticker, row in df.iterrows():
            net_amt = int(row.get("순매수거래대금", 0) or 0)
            # 매매가 0인 종목은 스킵 (DB 부피 절감)
            if net_amt == 0:
                continue
            net_qty = int(row.get("순매수거래량", 0) or 0)
            buy_amt = int(row.get("매수거래대금", 0) or 0)
            sell_amt = int(row.get("매도거래대금", 0) or 0)
            name = str(row.get("종목명", "") or "")
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO pension_flow_daily
                       (trade_date, symbol, market, name,
                        net_amount_won, net_qty, buy_amount_won, sell_amount_won,
                        collected_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (date_str, str(ticker), m, name,
                     net_amt, net_qty, buy_amt, sell_amt, now_iso),
                )
                cnt += 1
            except Exception:
                pass
        counts[m] = cnt
        saved += cnt
    conn.commit()
    conn.close()

    return {
        "date": date_str,
        "kospi_count": counts.get("KOSPI", 0),
        "kosdaq_count": counts.get("KOSDAQ", 0),
        "saved": saved,
    }


def fetch_pension_fund_flow(days: int = 5, market: str = "ALL", top: int = 30,
                              held_watch_only: bool = False) -> dict:
    """연기금 (NPS 우세) 종목별 누적 매매 — pykrx + KRX 로그인 활용.

    한국 시장 8개 투자자 분류 중 '연기금' 카테고리 단독.
    NPS가 한국 연기금 매매의 60~80% 비중이라 사실상 NPS 시그널 근사치.

    Args:
        days: 누적 일수 (기본 5)
        market: 'KOSPI' / 'KOSDAQ' / 'ALL' (기본 ALL)
        top: 매수 TOP / 매도 TOP 각각 N개 (기본 30)
        held_watch_only: True면 보유+워치만 필터 (포트 점검용)

    Returns:
        {
          "period": "YYYY-MM-DD ~ YYYY-MM-DD",
          "market": str,
          "buy_top": [{ticker, name, net_amount_won, net_qty}, ...],
          "sell_top": [...],
          "held_watch_flow": [...]   # 보유+워치 양방향
        }
    """
    try:
        from pykrx import stock as _krx
    except ImportError:
        return {"error": "pykrx 미설치"}

    today = datetime.now(KST)
    # 영업일 기준 days 일치 — KRX는 주말/공휴일 자동 스킵
    end_dd = today.strftime("%Y%m%d")
    start_dd = (today - timedelta(days=days * 2 + 3)).strftime("%Y%m%d")  # 여유 있게

    markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    all_rows = {}  # ticker → row dict

    for m in markets:
        try:
            df = _krx.get_market_net_purchases_of_equities(start_dd, end_dd, m, "연기금")
        except Exception as e:
            print(f"[pension_fund] {m} 실패: {e}")
            continue
        if df is None or len(df) == 0:
            continue
        for ticker, row in df.iterrows():
            net_amt = int(row.get("순매수거래대금", 0) or 0)
            net_qty = int(row.get("순매수거래량", 0) or 0)
            name = str(row.get("종목명", "") or "")
            all_rows[str(ticker)] = {
                "ticker": str(ticker),
                "name": name,
                "net_amount_won": net_amt,
                "net_qty": net_qty,
                "market": m,
            }

    # 기간 표시
    period = f"{start_dd[:4]}-{start_dd[4:6]}-{start_dd[6:]} ~ {end_dd[:4]}-{end_dd[4:6]}-{end_dd[6:]}"

    # 보유+워치 필터 (held_watch_only or held_watch_flow 추출용)
    held_watch_set = set()
    try:
        portfolio = load_json(PORTFOLIO_FILE, {})
        for k in portfolio.keys():
            if k not in ("us_stocks", "cash_krw", "cash_usd") and not _is_us_ticker(k):
                held_watch_set.add(k)
        for k in load_watchalert().keys():
            if not _is_us_ticker(k):
                held_watch_set.add(k)
    except Exception:
        pass

    # 정렬 분리
    buy_sorted = sorted(
        [r for r in all_rows.values() if r["net_amount_won"] > 0],
        key=lambda x: -x["net_amount_won"],
    )
    sell_sorted = sorted(
        [r for r in all_rows.values() if r["net_amount_won"] < 0],
        key=lambda x: x["net_amount_won"],
    )

    if held_watch_only:
        buy_sorted = [r for r in buy_sorted if r["ticker"] in held_watch_set]
        sell_sorted = [r for r in sell_sorted if r["ticker"] in held_watch_set]

    # 보유+워치 양방향
    held_watch_flow = sorted(
        [r for r in all_rows.values() if r["ticker"] in held_watch_set],
        key=lambda x: -abs(x["net_amount_won"]),
    )

    return {
        "period": period,
        "market": market,
        "days": days,
        "total_tracked": len(all_rows),
        "buy_top": buy_sorted[:top],
        "sell_top": sell_sorted[:top],
        "held_watch_flow": held_watch_flow,
        "fetched_at": datetime.now(KST).isoformat(),
    }


async def fetch_external_macro_signals(top_polymarket: int = 8) -> dict:
    """외부 매크로 시그널 통합 — Polymarket + Treasury curve + Fed Polymarket.

    한 번 호출로 매크로 전체 외부 베팅 컨센서스 + 금리 곡선 침체 시그널 조회.
    SAT_PORT_CHECK / SUN_DISCOVERY / 매크로 대시보드 자동 통합용.

    Returns:
        {"polymarket": [...], "fed": {...polymarket Fed decision...},
         "treasury": {...}, "summary": "1줄 요약"}
    """
    poly = await fetch_polymarket(top=top_polymarket, min_volume=500_000)
    fed = await fetch_polymarket(top=3, min_volume=100_000, query="Fed decision")
    curve = await fetch_treasury_curve()

    # 1줄 요약
    summary_parts = []
    if not fed.get("error") and fed.get("markets"):
        fed_top = fed["markets"][0]
        top_o = fed_top.get("top_outcome") or {}
        prob = top_o.get("prob")
        outcome_name = top_o.get("outcome", "")
        if prob is not None:
            summary_parts.append(
                f"Fed: {outcome_name} {prob*100:.0f}% ({fed_top['title'][:30]})"
            )
    if not curve.get("error") and curve.get("spread_10y_2y") is not None:
        summary_parts.append(
            f"10Y-2Y: {curve['spread_10y_2y']:+.2f}% ({curve['recession_signal']})"
        )

    return {
        "polymarket": poly,
        "fed": fed,
        "treasury": curve,
        "summary": " | ".join(summary_parts) if summary_parts else "데이터 부족",
        "fetched_at": datetime.now(KST).isoformat(),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NPS 5%룰 보고 (data.go.kr 공공데이터)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 출처: 국민연금공단_국민연금기금 5/10%이상 보유종목
#        publicDataPk=15106890
#        https://www.data.go.kr/data/15106890/fileData.do
# 형식: EUC-KR CSV, 컬럼 = 번호, 발행기관명, 보고서 작성기준일(YYYY-MM-DD), 지분율(퍼센트)
# 갱신 주기: 분기 (직전 분기 약 2개월 후 데이터 게시)
# 누적 전략: data.go.kr 측이 같은 atchFileId 덮어쓰기 → 우리 DB는 (report_date, name) 키로 누적
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NPS_DATA_GO_KR_PAGE = "https://www.data.go.kr/data/15106890/fileData.do"
NPS_FALLBACK_ATCH_FILE_ID = "FILE_000000003618528"  # 2025-12 시점 4Q25 분량


# 한글 → 영문약자 변환 (긴 매핑 우선 — startswith 충돌 방지)
# 예: "에이치디씨"가 "에이치디"보다 먼저 와야 "에이치디씨현대산업개발" → "HDC..."로 정상 변환
_KO_EN_GROUP_MAP = [
    # 4글자+
    ("비지에프", "BGF"),
    ("아이에스시", "ISC"),
    ("알에프에이치아이씨", "RFHIC"),
    ("에이치디현대", "HD현대"),
    ("에이치디씨", "HDC"),
    ("에이치엠엠", "HMM"),
    ("제이와이피", "JYP"),
    ("케이씨씨", "KCC"),
    ("케이티앤지", "KT&G"),
    ("엘아이지", "LIG"),
    ("오씨아이", "OCI"),
    # 3글자
    ("씨제이", "CJ"),
    ("에이치디", "HD"),
    ("케이지", "KG"),
    ("케이티", "KT"),
    ("엘에스", "LS"),
    ("엘엑스", "LX"),
    ("에스케이", "SK"),
    ("와이지", "YG"),
    # 2글자
    ("디비", "DB"),
    ("디엘", "DL"),
    ("지에스", "GS"),
    ("엘지", "LG"),
    ("에스엠", "SM"),
]


