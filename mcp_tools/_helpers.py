# mcp_tools/_helpers.py — 내부 헬퍼 함수들 (DART 캐시, PDF 렌더링, 스캔 내부 함수)
import json
import os
import asyncio
from datetime import datetime

from kis_api import *
from kis_api import (
    _DATA_DIR, _is_us_ticker,
    kis_daily_closes, kis_estimate_perform,
    dart_quarterly_op,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP 인증
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")

def _check_mcp_auth(request) -> bool:
    if not _MCP_AUTH_TOKEN:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {_MCP_AUTH_TOKEN}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 스크리너 당일 결과 캐시
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_DART_CACHE_FILE = f"{_DATA_DIR}/dart_screener_cache.json"


def _load_dart_screener_cache(mode: str, cache_key: str) -> dict | None:
    """당일 mode+cache_key 에 해당하는 캐시 반환. 없으면 None."""
    today = datetime.now().strftime("%Y%m%d")
    try:
        if os.path.exists(_DART_CACHE_FILE):
            data = json.load(open(_DART_CACHE_FILE, encoding="utf-8"))
            day = data.get(today, {})
            entry = day.get(cache_key)
            if entry:
                print(f"[dart_cache] 캐시 히트: {cache_key}")
                return entry
    except Exception as e:
        print(f"[dart_cache] 로드 오류: {e}")
    return None


def _save_dart_screener_cache(cache_key: str, result: dict):
    """당일 캐시에 결과 저장. 오늘 날짜 외 항목은 자동 삭제."""
    today = datetime.now().strftime("%Y%m%d")
    try:
        data = {}
        if os.path.exists(_DART_CACHE_FILE):
            try:
                data = json.load(open(_DART_CACHE_FILE, encoding="utf-8"))
            except Exception:
                pass
        today_map = data.get(today, {})
        today_map[cache_key] = result
        with open(_DART_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({today: today_map}, f, ensure_ascii=False)
        print(f"[dart_cache] 저장: {cache_key} ({result.get('count', 0)}건)")
    except Exception as e:
        print(f"[dart_cache] 저장 오류: {e}")


# DART 공시 중요도 태그 키워드
_DART_TAGS = {
    "긴급": ["유상증자", "전환사채", "신주인수권부사채", "CB", "BW",
             "분할", "합병", "감자", "상장폐지", "회생", "공개매수"],
    "주의": ["수주", "계약", "대규모", "공급계약", "납품", "MOU", "투자",
             "소송", "제재", "과징금", "조회공시"],
    "참고": ["임원", "지분", "자기주식", "자사주", "배당",
             "주식매수선택권", "스톡옵션", "정관"],
}


def _dart_tag(title: str) -> str:
    for level, keywords in _DART_TAGS.items():
        if any(k in title for k in keywords):
            return level
    return "일반"


def _pf(val) -> float:
    """영업이익 등 재무 수치 문자열을 float으로 변환 (콤마 제거 포함)"""
    try:
        return float(str(val).replace(",", "").strip() or "0")
    except Exception:
        return 0.0


def _nf(val):
    """재무 수치 문자열 → float 변환, 빈값이면 None"""
    s = str(val).replace(",", "").strip()
    try:
        return float(s) if s else None
    except Exception:
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# PDF 렌더링 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_page_range(pages_str: str | None, total_pages: int) -> list[int] | None | str:
    """페이지 범위 문자열을 0-based 인덱스 리스트로 변환."""
    if not pages_str or not pages_str.strip():
        return None

    indices: list[int] = []
    parts = pages_str.strip().split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bounds = part.split("-", 1)
            try:
                start = int(bounds[0].strip())
                end   = int(bounds[1].strip())
            except ValueError:
                return f"페이지 범위 파싱 오류: '{part}'"
            if start < 1 or end < start:
                return f"잘못된 페이지 범위: '{part}'"
            if end > total_pages:
                end = total_pages
            indices.extend(range(start - 1, end))
        else:
            try:
                pg = int(part)
            except ValueError:
                return f"페이지 번호 파싱 오류: '{part}'"
            if pg < 1 or pg > total_pages:
                return f"페이지 번호 범위 초과: {pg} (총 {total_pages}p)"
            indices.append(pg - 1)

    if not indices:
        return "유효한 페이지 번호가 없습니다"
    return sorted(set(indices))


def _render_pdf_pages(pdf_path: str, page_indices: list[int] | None = None):
    """PDF 페이지를 PNG ImageContent 리스트로 변환."""
    import fitz as _fitz
    import base64 as _b64

    _MAX_BYTES = 5 * 1024 * 1024

    doc = _fitz.open(pdf_path)
    total_pages = len(doc)

    if page_indices is None:
        page_indices = list(range(total_pages))

    images: list[dict] = []
    cumulative = 0
    truncated = False
    rendered = 0

    for idx in page_indices:
        if idx < 0 or idx >= total_pages:
            continue
        page = doc[idx]
        pix  = page.get_pixmap(dpi=100)
        png_bytes = pix.tobytes("png")
        if cumulative + len(png_bytes) > _MAX_BYTES:
            truncated = True
            break
        b64 = _b64.b64encode(png_bytes).decode("ascii")
        images.append({
            "type":     "image",
            "data":     b64,
            "mimeType": "image/png",
        })
        cumulative += len(png_bytes)
        rendered   += 1

    doc.close()

    meta = {
        "total_pages":    total_pages,
        "rendered_pages": rendered,
        "requested_pages": len(page_indices),
        "size_kb":        cumulative // 1024,
        "truncated":      truncated,
    }
    return images, meta


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 스크리너 내부 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_TREND_PRIORITY = {"연속증가": 0, "흑자전환": 1, "감소": 2, "적자전환": 3, "적자지속": 4}


def _calc_qoq(quarterly: list) -> dict:
    r = {"qoq_growth": None, "recent_quarter_op": None, "prev_quarter_op": None, "op_trend": None}
    if len(quarterly) < 3:
        return r
    q_row = quarterly[2]
    rq = _nf(q_row.get("ebt"))
    pq = _nf(q_row.get("op"))
    if rq is None or pq is None:
        return r
    r["recent_quarter_op"] = round(rq)
    r["prev_quarter_op"]   = round(pq)
    if abs(pq) > 0:
        r["qoq_growth"] = round((rq - pq) / abs(pq) * 100, 1)
    if pq < 0 and rq > 0:
        r["op_trend"] = "흑자전환"
    elif pq > 0 and rq < 0:
        r["op_trend"] = "적자전환"
    elif pq <= 0 and rq <= 0:
        r["op_trend"] = "적자지속"
    elif pq > 0 and rq > pq:
        r["op_trend"] = "연속증가"
    else:
        r["op_trend"] = "감소"
    return r


async def _scan_conv_one(ticker: str, name: str, token: str, sem: asyncio.Semaphore, spread_threshold: float):
    async with sem:
        await asyncio.sleep(0.1)
        try:
            closes = await kis_daily_closes(ticker, token)
            valid = [c for c in closes[:60] if c > 0]
            if len(valid) < 60:
                return None
            ma5  = sum(valid[:5])  / 5
            ma20 = sum(valid[:20]) / 20
            ma60 = sum(valid[:60]) / 60
            cur  = valid[0]
            sp = (max(ma5, ma20, ma60) - min(ma5, ma20, ma60)) / cur * 100
            if sp <= spread_threshold:
                disp_20 = round((cur - ma20) / ma20 * 100, 2)
                disp_60 = round((cur - ma60) / ma60 * 100, 2)
                return {"ticker": ticker, "name": name, "price": cur,
                        "spread": round(sp, 2), "ma5": round(ma5),
                        "ma20": round(ma20), "ma60": round(ma60),
                        "disp_20": disp_20, "disp_60": disp_60}
        except Exception as e:
            print(f"[convergence] {ticker} 오류: {e}")
        return None


def _op_extra_fields(annual: list) -> dict:
    rev_recent = rev_prev = op_margin = rev_growth = None
    try:
        rev_recent = _pf(annual[0].get("ebt")) if len(annual) > 0 else None
        rev_prev   = _pf(annual[0].get("op"))  if len(annual) > 0 else None
    except Exception:
        pass
    try:
        if rev_recent is not None and rev_prev is not None and abs(rev_prev) > 0:
            rev_growth = round((rev_recent - rev_prev) / abs(rev_prev) * 100, 1)
    except Exception:
        pass
    try:
        op_recent_val = _pf(annual[2].get("ebt")) if len(annual) > 2 else None
        if op_recent_val is not None and rev_recent is not None and rev_recent > 0:
            op_margin = round(op_recent_val / rev_recent * 100, 1)
    except Exception:
        pass
    return {
        "op_margin":  op_margin,
        "rev_recent": round(rev_recent) if rev_recent is not None else None,
        "rev_prev":   round(rev_prev)   if rev_prev   is not None else None,
        "rev_growth": rev_growth,
        "period":     "최근연도 vs 전년도",
    }


async def _scan_op_one(ticker: str, name: str, token: str, sem: asyncio.Semaphore, min_growth: float):
    async with sem:
        await asyncio.sleep(0.07)
        try:
            raw = await kis_estimate_perform(ticker, token)
            annual = raw.get("annual", [])
            if len(annual) < 3:
                return None
            op_recent = _pf(annual[2].get("ebt"))
            op_prev   = _pf(annual[2].get("op"))
            if op_prev <= 0:
                return None
            growth_pct = (op_recent - op_prev) / abs(op_prev) * 100
            if growth_pct >= min_growth:
                return {"ticker": ticker, "name": name,
                        "op_recent": round(op_recent),
                        "op_prev":   round(op_prev),
                        "growth_pct": round(growth_pct, 1),
                        **_op_extra_fields(annual),
                        **_calc_qoq(raw.get("quarterly", []))}
        except Exception as e:
            print(f"[op_growth] {ticker} 오류: {e}")
        return None


async def _scan_turnaround_one(ticker: str, name: str, token: str, sem: asyncio.Semaphore):
    async with sem:
        await asyncio.sleep(0.07)
        try:
            raw = await kis_estimate_perform(ticker, token)
            annual = raw.get("annual", [])
            if len(annual) < 3:
                return None
            op_recent = _pf(annual[2].get("ebt"))
            op_prev   = _pf(annual[2].get("op"))
            if op_prev < 0 and op_recent > 0:
                return {"ticker": ticker, "name": name,
                        "op_recent": round(op_recent),
                        "op_prev":   round(op_prev),
                        **_op_extra_fields(annual),
                        **_calc_qoq(raw.get("quarterly", []))}
        except Exception as e:
            print(f"[op_turnaround] {ticker} 오류: {e}")
        return None


async def _scan_dart_op_one(ticker: str, name: str, corp_code: str, sem: asyncio.Semaphore, min_growth: float, recent_year: int, token: str = ""):
    try:
        async with sem:
            r_recent = await dart_quarterly_op(corp_code, recent_year, 4)
        async with sem:
            r_prev = await dart_quarterly_op(corp_code, recent_year - 1, 4)
        if not r_recent or not r_prev:
            return None
        op_recent = r_recent["op_profit"]
        op_prev   = r_prev["op_profit"]
        if op_recent is None or op_prev is None or op_prev <= 0:
            return None
        growth_pct = (op_recent - op_prev) / abs(op_prev) * 100
        if growth_pct < min_growth:
            return None
        rev_recent = r_recent.get("revenue")
        rev_prev   = r_prev.get("revenue")
        op_margin  = round(op_recent / rev_recent * 100, 1) if rev_recent and rev_recent > 0 else None
        rev_growth = round((rev_recent - rev_prev) / abs(rev_prev) * 100, 1) if rev_recent and rev_prev and rev_prev != 0 else None
        qoq_fields = {"qoq_growth": None, "recent_quarter_op": None, "prev_quarter_op": None, "op_trend": None}
        if token:
            try:
                raw_q = await kis_estimate_perform(ticker, token)
                qoq_fields = _calc_qoq(raw_q.get("quarterly", []))
            except Exception:
                pass
        return {"ticker": ticker, "name": name,
                "period": f"{recent_year}연간 vs {recent_year - 1}연간",
                "op_recent": op_recent, "op_prev": op_prev,
                "growth_pct": round(growth_pct, 1),
                "op_margin": op_margin, "rev_recent": rev_recent, "rev_growth": rev_growth,
                **qoq_fields}
    except Exception as e:
        print(f"[dart_op_growth] {ticker} 오류: {e}")
    return None


async def _scan_dart_turnaround_one(ticker: str, name: str, corp_code: str, sem: asyncio.Semaphore, recent_year: int, token: str = ""):
    try:
        async with sem:
            r_recent = await dart_quarterly_op(corp_code, recent_year, 4)
        async with sem:
            r_prev = await dart_quarterly_op(corp_code, recent_year - 1, 4)
        if not r_recent or not r_prev:
            return None
        op_recent = r_recent["op_profit"]
        op_prev   = r_prev["op_profit"]
        if op_recent is None or op_prev is None:
            return None
        if not (op_prev < 0 and op_recent > 0):
            return None
        rev_recent = r_recent.get("revenue")
        op_margin  = round(op_recent / rev_recent * 100, 1) if rev_recent and rev_recent > 0 else None
        qoq_fields = {"qoq_growth": None, "recent_quarter_op": None, "prev_quarter_op": None, "op_trend": None}
        if token:
            try:
                raw_q = await kis_estimate_perform(ticker, token)
                qoq_fields = _calc_qoq(raw_q.get("quarterly", []))
            except Exception:
                pass
        return {"ticker": ticker, "name": name,
                "period": f"{recent_year}연간 vs {recent_year - 1}연간",
                "op_recent": op_recent, "op_prev": op_prev,
                "op_margin": op_margin, "rev_recent": rev_recent,
                **qoq_fields}
    except Exception as e:
        print(f"[dart_turnaround] {ticker} 오류: {e}")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# US 애널 헬퍼 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def _exec_us_ratings(ticker: str = "", mode: str = "events",
                            days: int = 90, months: int = 6,
                            min_stars: float = 0.0, **_) -> dict:
    if not ticker:
        return {"error": "ticker는 필수입니다. 예: get_us_ratings(ticker='AMD', mode='consensus')",
                "modes": ["events", "trend", "consensus"]}
    from db_collector import _get_db
    ticker = ticker.upper()
    conn = _get_db()
    try:
        if mode == "consensus":
            row = conn.execute(
                "SELECT snapshot_date, analyst_count, consensus_rating, target_avg "
                "FROM us_consensus_snapshot WHERE ticker=? ORDER BY snapshot_date DESC LIMIT 1",
                (ticker,)).fetchone()
            if not row:
                return {"ticker": ticker, "mode": "consensus", "data": None,
                        "message": "데이터 없음 — 일일 스캔 대기"}
            return {"ticker": ticker, "mode": "consensus",
                    "data": {"snapshot_date": row[0], "analyst_count": row[1],
                             "consensus_rating": row[2], "target_avg": row[3]}}
        elif mode == "trend":
            rows = conn.execute(
                "SELECT substr(snapshot_date, 1, 7) AS ym, "
                "       AVG(analyst_count), AVG(target_avg) "
                "FROM us_consensus_snapshot WHERE ticker=? "
                "  AND snapshot_date >= date('now', ?) "
                "GROUP BY ym ORDER BY ym DESC",
                (ticker, f"-{months} months")).fetchall()
            return {"ticker": ticker, "mode": "trend", "months": months,
                    "data": [{"month": r[0], "avg_count": r[1], "avg_target": r[2]} for r in rows]}
        else:
            rows = conn.execute(
                "SELECT rating_date, rating_time, firm, analyst, action, "
                "       rating_new, rating_old, pt_now, pt_old, pt_change_pct, stars "
                "FROM us_analyst_ratings WHERE ticker=? "
                "  AND rating_date >= date('now', ?) "
                "  AND (stars IS NULL OR stars >= ?) "
                "ORDER BY rating_date DESC, rating_time DESC",
                (ticker, f"-{days} days", min_stars)).fetchall()
            return {"ticker": ticker, "mode": "events", "days": days, "min_stars": min_stars,
                    "count": len(rows),
                    "events": [{"date": r[0], "time": r[1], "firm": r[2], "analyst": r[3],
                                "action": r[4], "rating_new": r[5], "rating_old": r[6],
                                "pt_now": r[7], "pt_old": r[8], "pt_change_pct": r[9],
                                "stars": r[10]} for r in rows]}
    finally:
        conn.close()


async def _exec_us_scan(mode: str = "watchlist", days: int = 7,
                         min_upgrades: int = 3, sector: str = None, **_) -> dict:
    from kis_api import load_us_watchlist, PORTFOLIO_FILE, load_json
    from db_collector import _get_db

    if mode == "discovery":
        excluded = set()
        for t in load_us_watchlist().keys():
            excluded.add(t.upper())
        for t in load_json(PORTFOLIO_FILE, {}).get("us_stocks", {}).keys():
            excluded.add(t.upper())
        conn = _get_db()
        try:
            top_count = conn.execute(
                "SELECT COUNT(*) FROM us_analysts WHERE watched=1"
            ).fetchone()[0]
            if top_count == 0:
                return {"mode": "discovery", "days": days, "min_upgrades": min_upgrades,
                        "message": "톱 애널 확정 없음 — get_us_analyst(top=100) 로 후보 검토 후 watch_analyst 로 watched=1 설정 필요",
                        "top_analysts": 0, "data": []}
            rows = conn.execute(
                "SELECT r.ticker, COUNT(*) AS n_up, "
                "       AVG(r.pt_now) AS avg_target, "
                "       GROUP_CONCAT(r.firm, ', ') AS firms "
                "FROM us_analyst_ratings r "
                "JOIN us_analysts a ON r.analyst_slug = a.slug "
                "WHERE a.watched = 1 "
                "  AND r.action = 'Upgrades' "
                "  AND r.rating_date >= date('now', ?) "
                "GROUP BY r.ticker "
                "HAVING n_up >= ? "
                "ORDER BY n_up DESC, avg_target DESC",
                (f"-{days} days", min_upgrades)
            ).fetchall()
            filtered = [r for r in rows if r[0] not in excluded]
            return {"mode": "discovery", "days": days, "min_upgrades": min_upgrades,
                    "top_analysts": top_count, "excluded_tickers": sorted(excluded),
                    "data": [{"ticker": r[0], "upgrades": r[1],
                              "avg_target": r[2], "firms": r[3]} for r in filtered]}
        finally:
            conn.close()

    if mode == "sector":
        if not sector:
            return {"mode": "sector", "message": "sector 파라미터 필요", "data": []}
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT c.ticker, c.sector, "
                "       SUM(CASE WHEN r.action='Upgrades' THEN 1 ELSE 0 END) AS up_n, "
                "       SUM(CASE WHEN r.action='Downgrades' THEN 1 ELSE 0 END) AS down_n, "
                "       AVG(r.pt_now) AS avg_target "
                "FROM us_analyst_coverage c "
                "LEFT JOIN us_analyst_ratings r "
                "  ON c.ticker = r.ticker "
                "  AND r.rating_date >= date('now', ?) "
                "WHERE LOWER(c.sector) LIKE ? "
                "GROUP BY c.ticker, c.sector "
                "HAVING up_n > 0 OR down_n > 0 "
                "ORDER BY up_n DESC",
                (f"-{days} days", f"%{sector.lower()}%")
            ).fetchall()
            return {"mode": "sector", "sector": sector, "days": days,
                    "data": [{"ticker": r[0], "sector": r[1], "upgrades": r[2],
                              "downgrades": r[3], "avg_target": r[4]} for r in rows]}
        finally:
            conn.close()

    tickers = set()
    for t in load_us_watchlist().keys():
        tickers.add(t.upper())
    for t in load_json(PORTFOLIO_FILE, {}).get("us_stocks", {}).keys():
        tickers.add(t.upper())
    if not tickers:
        return {"mode": "watchlist", "days": days, "tickers": [], "data": []}
    conn = _get_db()
    try:
        out = []
        for ticker in sorted(tickers):
            rows = conn.execute(
                "SELECT rating_date, firm, analyst, action, rating_new, rating_old, "
                "       pt_now, pt_old, pt_change_pct "
                "FROM us_analyst_ratings WHERE ticker=? "
                "  AND rating_date >= date('now', ?) "
                "ORDER BY rating_date DESC, rating_time DESC",
                (ticker, f"-{days} days")).fetchall()
            if not rows:
                continue
            upgrades = sum(1 for r in rows if (r[3] or "").lower() == "upgrades")
            downgrades = sum(1 for r in rows if (r[3] or "").lower() == "downgrades")
            out.append({
                "ticker": ticker, "events": len(rows),
                "upgrades": upgrades, "downgrades": downgrades,
                "flag_upgrade": upgrades >= min_upgrades,
                "latest": [{"date": r[0], "firm": r[1], "action": r[3],
                            "rating_new": r[4], "pt_now": r[6], "pt_change_pct": r[8]}
                           for r in rows[:3]],
            })
        return {"mode": "watchlist", "days": days, "min_upgrades": min_upgrades,
                "tickers": sorted(tickers), "data": out}
    finally:
        conn.close()


async def _exec_us_analyst(name: str = None, firm: str = None, sector: str = None,
                            top: int = 10, min_stars: float = 4.0, days: int = 14, **_) -> dict:
    from db_collector import _get_db
    conn = _get_db()
    try:
        if name:
            slug = name.lower().replace(" ", "-")
            rows = conn.execute(
                "SELECT ticker, rating_date, firm, action, rating_new, pt_now, "
                "       pt_change_pct, stars, success_rate "
                "FROM us_analyst_ratings "
                "WHERE (analyst_slug=? OR LOWER(analyst)=?) "
                "  AND rating_date >= date('now', ?) "
                "ORDER BY rating_date DESC LIMIT 50",
                (slug, name.lower(), f"-{days} days")).fetchall()
            return {"name": name, "days": days, "count": len(rows),
                    "calls": [{"ticker": r[0], "date": r[1], "firm": r[2], "action": r[3],
                               "rating_new": r[4], "pt_now": r[5], "pt_change_pct": r[6],
                               "stars": r[7], "success_rate": r[8]} for r in rows]}
        if firm or sector:
            where_parts = []
            params = []
            if firm:
                where_parts.append("LOWER(firm) LIKE ?")
                params.append(f"%{firm.lower()}%")
            if sector:
                where_parts.append("LOWER(sectors) LIKE ?")
                params.append(f'%"{sector.lower()}"%')
            where_parts.append("stars >= ?")
            params.append(min_stars)
            params.append(top)
            rows = conn.execute(
                "SELECT slug, name, firm, sectors, stars, success_rate, total_ratings, watched "
                "FROM us_analysts "
                f"WHERE {' AND '.join(where_parts)} "
                "ORDER BY stars DESC "
                "LIMIT ?",
                params
            ).fetchall()
            import json as _json
            return {"mode": "filter", "firm": firm, "sector": sector,
                    "min_stars": min_stars, "top": top, "count": len(rows),
                    "analysts": [{"slug": r[0], "name": r[1], "firm": r[2],
                                  "sectors": _json.loads(r[3]) if r[3] else [],
                                  "stars": r[4], "success_rate": r[5],
                                  "total_ratings": r[6], "watched": bool(r[7])}
                                 for r in rows]}
        rows = conn.execute(
            "SELECT analyst_slug, analyst, firm, AVG(stars), AVG(success_rate), COUNT(*) "
            "FROM us_analyst_ratings WHERE stars >= ? "
            "  AND rating_date >= date('now', ?) "
            "GROUP BY analyst_slug HAVING COUNT(*) > 0 "
            "ORDER BY AVG(stars) DESC LIMIT ?",
            (min_stars, f"-{days} days", top)).fetchall()
        return {"mode": "top", "top": top, "min_stars": min_stars, "days": days,
                "analysts": [{"slug": r[0], "analyst": r[1], "firm": r[2],
                              "avg_stars": r[3], "avg_success_rate": r[4], "call_count": r[5]}
                             for r in rows]}
    finally:
        conn.close()


async def _exec_watch_analyst(slug: str, watched: bool = True, **_) -> dict:
    from db_collector import _get_db
    from datetime import datetime
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT slug, name, firm, stars FROM us_analysts WHERE slug=?", (slug,)
        ).fetchone()
        if not row:
            return {"status": "error", "slug": slug,
                    "message": "애널 메타 없음 — 먼저 fetch_and_store_analyst_meta 로 수집 필요"}
        conn.execute(
            "UPDATE us_analysts SET watched=?, curated_at=? WHERE slug=?",
            (1 if watched else 0, datetime.now().isoformat(), slug)
        )
        conn.commit()
        return {"status": "ok", "slug": slug, "name": row[1], "firm": row[2],
                "stars": row[3], "watched": watched}
    finally:
        conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Git 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━
import subprocess
from pathlib import Path

_GIT_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GIT_ALLOWED_SUBCMDS = {"status", "diff", "log", "add", "commit", "push", "branch", "rev-parse"}
_GIT_BLOCKED_FLAGS = {"--force", "-f", "--hard", "--reset", "--delete", "-D", "--force-with-lease"}


def _run_git(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    if not args:
        raise ValueError("git 인자가 없습니다")
    subcmd = args[0]
    if subcmd not in _GIT_ALLOWED_SUBCMDS:
        raise ValueError(f"허용되지 않은 git 서브커맨드: {subcmd}")
    for flag in args:
        if flag in _GIT_BLOCKED_FLAGS:
            raise ValueError(f"차단된 git 플래그: {flag}")
    proc = subprocess.run(
        ["git"] + args,
        cwd=_GIT_REPO_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _validate_git_path(raw: str) -> str:
    if ".." in raw.split("/") or ".." in raw.split(os.sep):
        raise ValueError(f"경로 traversal 차단: {raw!r}")
    abs_path = Path(_GIT_REPO_DIR) / raw
    try:
        resolved = abs_path.resolve()
    except Exception:
        raise ValueError(f"경로 정규화 실패: {raw!r}")
    repo_resolved = Path(_GIT_REPO_DIR).resolve()
    if not str(resolved).startswith(str(repo_resolved)):
        raise ValueError(f"저장소 외부 경로 차단: {raw!r}")
    return raw


# _NO_TOKEN_TOOLS: token 불필요 도구 목록
_NO_TOKEN_TOOLS = frozenset({
    "read_file", "write_file", "list_files", "read_report_pdf",
    "git_status", "git_diff", "git_log", "git_commit", "git_push",
    "backup_data",
    "get_us_ratings", "get_us_scan", "get_us_analyst", "watch_analyst",
})
