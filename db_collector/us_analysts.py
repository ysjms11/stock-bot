"""미국 애널 마스터 관리 / 매수 후보 발굴.

P3-7 박리: sync_us_analyst_master, is_tier_s_analyst, find_us_buy_candidates
"""

import sqlite3
from datetime import datetime

from ._db import _get_db

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# us_analyst_ratings → us_analysts 마스터 자동 동기화
# (weekly_us_harvest 후 04:00 실행, ratings 1,902명 → 마스터 자동 인구)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def sync_us_analyst_master() -> dict:
    """ratings 테이블의 모든 애널을 us_analysts 마스터로 자동 동기화.
    + 3-Tier 자동 분류 (Tier A=watched=1, Tier S는 알림 시 런타임 분기).

    **Tier A (watched=1) 진입 조건 (OR)**:
      - 일반 톱: 별점≥4.0 AND 적중률≥60% AND 콜≥10
      - 잠수형 거장: 별점≥4.8 AND 적중률≥80% AND 콜≥7

    **Tier S (런타임 분기, watched=1 안에서)**:
      ① 활발 톱: 별점≥4.5 AND 적중률≥70% AND 콜≥20
      ② 잠수형 거장: 별점≥4.8 AND 적중률≥80% AND 콜≥7
      ③ 고수익 거장: 별점≥4.5 AND avg_return≥50% AND 콜≥10

    - 신규 애널: INSERT (avg_return 포함)
    - 기존 애널: stars/success_rate/total_ratings/avg_return 갱신
    - watched/curated_at 사용자 큐레이션 보존 (수동 watched=1만 자동 watched=0으로 안 됨)

    Returns: {inserted, updated, auto_watched_a, tier_s_count, total_master, total_watched}
    """
    conn = _get_db()
    try:
        before_master = conn.execute("SELECT COUNT(*) FROM us_analysts").fetchone()[0]

        # Step 1: ratings → 마스터 INSERT or UPDATE (avg_return 포함)
        conn.execute("""
            INSERT INTO us_analysts (slug, name, firm, stars, success_rate,
                                     total_ratings, avg_return, last_updated)
            SELECT analyst_slug, MAX(analyst), MAX(firm),
                   AVG(stars), AVG(success_rate), COUNT(*),
                   AVG(avg_return),
                   datetime('now')
            FROM us_analyst_ratings
            WHERE analyst_slug IS NOT NULL AND analyst_slug != ''
            GROUP BY analyst_slug
            ON CONFLICT(slug) DO UPDATE SET
              name          = excluded.name,
              firm          = excluded.firm,
              stars         = excluded.stars,
              success_rate  = excluded.success_rate,
              total_ratings = excluded.total_ratings,
              avg_return    = excluded.avg_return,
              last_updated  = excluded.last_updated
        """)
        conn.commit()

        # Step 2: Tier A 자동 watched=1 (OR 2 경로, 사용자 큐레이션 보존)
        cur = conn.execute("""
            UPDATE us_analysts SET
              watched = 1,
              curated_at = datetime('now')
            WHERE watched = 0 AND (
              -- 일반 톱
              (stars >= 4.0 AND success_rate >= 60 AND total_ratings >= 10)
              OR
              -- 잠수형 거장 (Wildcard)
              (stars >= 4.8 AND success_rate >= 80 AND total_ratings >= 7)
            )
        """)
        auto_watched_count = cur.rowcount
        conn.commit()

        # Step 3: Tier S 카운트 (런타임 분기지만 통계 목적)
        tier_s_count = conn.execute("""
            SELECT COUNT(*) FROM us_analysts WHERE watched = 1 AND (
              (stars >= 4.5 AND success_rate >= 70 AND total_ratings >= 20)
              OR (stars >= 4.8 AND success_rate >= 80 AND total_ratings >= 7)
              OR (stars >= 4.5 AND avg_return >= 50 AND total_ratings >= 10)
            )
        """).fetchone()[0]

        after_master = conn.execute("SELECT COUNT(*) FROM us_analysts").fetchone()[0]
        after_watched = conn.execute("SELECT COUNT(*) FROM us_analysts WHERE watched=1").fetchone()[0]

        return {
            "inserted": after_master - before_master,
            "updated": before_master,
            "auto_watched_a": auto_watched_count,
            "tier_s_count": tier_s_count,
            "total_master": after_master,
            "total_watched": after_watched,
            "criteria": "Tier A: 별점≥4.0 AND 적중률≥60% AND 콜≥10 OR (별점≥4.8 AND 적중률≥80% AND 콜≥7)",
        }
    finally:
        conn.close()


def is_tier_s_analyst(stars: float, success_rate: float, total_ratings: int,
                       avg_return: float = 0.0) -> bool:
    """Tier S 엘리트 판정 — 알림 시 런타임 분기용.
    3 경로 OR — 자주 정확 / 잠수형 거장 / 고수익 거장."""
    if stars is None:
        return False
    s = stars
    sr = success_rate or 0.0
    n = total_ratings or 0
    ret = avg_return or 0.0
    return (
        (s >= 4.5 and sr >= 70 and n >= 20) or       # ① 활발 톱
        (s >= 4.8 and sr >= 80 and n >= 7) or        # ② 잠수형 거장
        (s >= 4.5 and ret >= 50 and n >= 10)         # ③ 고수익 거장
    )


def find_us_buy_candidates(
    days: int = 180,
    min_advisors: int = 1,
    min_upside: float = 20.0,
    exclude_held_and_watch: bool = True,
    limit: int = 50,
) -> dict:
    """톱 애널 추천 + 가격 적정 미국 매수 후보 발굴.

    원시 데이터 반환. 정렬·필터·해석은 LLM/사용자가 동적으로.

    조건:
    - watched=1 (Tier A, 254명) 애널의 Upgrades or Initiates
    - 최근 N일 (기본 180)
    - 종목별 추천 애널 N명+ (기본 1)
    - TP 대비 현재가 업사이드 N%+ (기본 20%, TP 초과 자동 컷)
    - 보유/워치 제외 (기본)

    Returns: {
      "criteria": {...},
      "total_pool": int,           # 풀 크기
      "after_upside_filter": int,  # 업사이드 필터 후
      "candidates": [
        {ticker, price, avg_target, upside_pct,
         tier_s_count, tier_a_count, others_count, total_advisors,
         latest_call_days_ago, tier_s_analysts, tier_a_analysts}
      ]
    }
    """
    import yfinance as yf
    from datetime import datetime, timezone

    conn = _get_db()
    try:
        # Step 1: 종목별 watched 애널 추천 집계
        rows = conn.execute("""
            SELECT r.ticker,
                   AVG(r.pt_now) AS avg_tp,
                   COUNT(*) AS total_advisors,
                   MAX(r.rating_date) AS latest_rating,
                   GROUP_CONCAT(DISTINCT r.action) AS actions
            FROM us_analyst_ratings r
            JOIN us_analysts a ON r.analyst_slug = a.slug
            WHERE a.watched = 1
              AND r.action IN ('Upgrades', 'Initiates')
              AND r.rating_date >= date('now', ?)
            GROUP BY r.ticker
            HAVING COUNT(*) >= ?
        """, (f"-{days} days", min_advisors)).fetchall()

        if not rows:
            return {"criteria": {"days": days, "min_advisors": min_advisors,
                                  "min_upside": min_upside},
                    "total_pool": 0, "after_upside_filter": 0, "candidates": []}

        # Step 2: 보유/워치 제외 처리
        excluded = set()
        if exclude_held_and_watch:
            try:
                from kis_api import load_us_watchlist, PORTFOLIO_FILE, load_json
                for t in load_us_watchlist().keys():
                    excluded.add(t.upper())
                for t in load_json(PORTFOLIO_FILE, {}).get("us_stocks", {}).keys():
                    excluded.add(t.upper())
            except Exception:
                pass

        candidate_rows = [r for r in rows if r["ticker"].upper() not in excluded]
        total_pool = len(candidate_rows)
        if not candidate_rows:
            return {"criteria": {"days": days, "min_advisors": min_advisors,
                                  "min_upside": min_upside},
                    "total_pool": 0, "after_upside_filter": 0, "candidates": []}

        # Step 3: 종목별 Tier S/A/일반 카운트 + 톱 애널 정보 수집
        ticker_details = {}
        for r in candidate_rows:
            ticker = r["ticker"]
            advisor_rows = conn.execute("""
                SELECT r.firm, r.analyst, r.action, r.rating_new, r.pt_now, r.rating_date,
                       a.stars, a.success_rate, a.total_ratings, a.avg_return, a.watched
                FROM us_analyst_ratings r
                JOIN us_analysts a ON r.analyst_slug = a.slug
                WHERE r.ticker = ? AND a.watched = 1
                  AND r.action IN ('Upgrades', 'Initiates')
                  AND r.rating_date >= date('now', ?)
                ORDER BY r.rating_date DESC, a.stars DESC
            """, (ticker, f"-{days} days")).fetchall()

            tier_s_list, tier_a_list = [], []
            for ar in advisor_rows:
                meta = {
                    "name": ar["analyst"], "firm": ar["firm"],
                    "stars": ar["stars"], "success_rate": ar["success_rate"],
                    "total_calls": ar["total_ratings"], "avg_return": ar["avg_return"],
                    "action": ar["action"], "rating": ar["rating_new"],
                    "pt": ar["pt_now"], "rated_at": ar["rating_date"],
                }
                if is_tier_s_analyst(ar["stars"], ar["success_rate"],
                                       ar["total_ratings"], ar["avg_return"]):
                    tier_s_list.append(meta)
                else:
                    tier_a_list.append(meta)

            ticker_details[ticker] = {
                "tier_s_count": len(tier_s_list),
                "tier_a_count": len(tier_a_list),
                "tier_s_analysts": tier_s_list[:3],  # 상위 3명만
                "tier_a_analysts": tier_a_list[:3],
                "actions": (r["actions"] or "").split(","),
                "latest_rating": r["latest_rating"],
            }

        # Step 4: yfinance 배치 다운로드 (현재가)
        tickers_list = [r["ticker"] for r in candidate_rows]
        prices = {}
        try:
            data = yf.download(tickers=tickers_list, period="1d",
                                progress=False, auto_adjust=True, threads=False)
            if not data.empty:
                close = data["Close"]
                if hasattr(close, "iloc"):
                    last = close.iloc[-1]
                    if hasattr(last, "to_dict"):
                        prices = last.to_dict()
                    else:
                        # 단일 종목 케이스
                        prices = {tickers_list[0]: float(last)}
        except Exception as e:
            print(f"[buy_candidates] yfinance 실패: {e}")

        # Step 5: 업사이드 계산 + 필터
        today = datetime.now(timezone.utc).date()
        candidates = []
        for r in candidate_rows:
            ticker = r["ticker"]
            avg_tp = r["avg_tp"]
            price = prices.get(ticker)
            if price is None or price != price or not avg_tp or avg_tp <= 0:
                continue
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            upside = (avg_tp - price) / price * 100.0
            if upside < min_upside:
                continue

            details = ticker_details.get(ticker, {})
            # 최근 콜 days ago
            latest = details.get("latest_rating") or r["latest_rating"]
            try:
                from datetime import date as _date
                ld = _date.fromisoformat(latest) if latest else today
                days_ago = (today - ld).days
            except Exception:
                days_ago = None

            candidates.append({
                "ticker": ticker,
                "price": round(price, 2),
                "avg_target": round(avg_tp, 2),
                "upside_pct": round(upside, 2),
                "total_advisors": r["total_advisors"],
                "tier_s_count": details.get("tier_s_count", 0),
                "tier_a_count": details.get("tier_a_count", 0),
                "others_count": 0,  # watched=1만 보므로 일반은 0
                "latest_call_days_ago": days_ago,
                "actions": details.get("actions", []),
                "tier_s_analysts": details.get("tier_s_analysts", []),
                "tier_a_analysts": details.get("tier_a_analysts", []),
            })

        # Step 6: 정렬 (업사이드 내림차순) + limit
        candidates.sort(key=lambda c: -c["upside_pct"])
        after_filter = len(candidates)
        candidates = candidates[:limit]

        return {
            "criteria": {
                "days": days, "min_advisors": min_advisors,
                "min_upside": min_upside, "limit": limit,
                "exclude_held_and_watch": exclude_held_and_watch,
            },
            "total_pool": total_pool,
            "after_upside_filter": after_filter,
            "candidates": candidates,
        }
    finally:
        conn.close()

