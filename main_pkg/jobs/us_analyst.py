"""main_pkg/jobs/us_analyst.py — 미국 애널 레이팅 잡 모음.

Phase B (2026-06-13): main_pkg/telegram_bot.py 에서 verbatim 추출.
원본 telegram_bot.py 에 re-export 래퍼 유지 (backward-compat).
"""
import asyncio
from datetime import datetime, timedelta

from kis_api import *           # KST, ET, CHAT_ID, UNIVERSE_FILE 등 star-import
from kis_api import _DATA_DIR   # explicit private import
from main_pkg._ctx import _safe_send
from db_collector import db_write_lock


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 미국 애널 레이팅 — 실시간 감시 (2단계)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_US_SELL_RATINGS = frozenset({"Sell", "Strong Sell"})
_US_DOWNGRADE_PT_THRESHOLD = -15.0  # 타겟 15% 이상 하향 = 다운그레이드 간주


def _detect_new_downgrades(ticker: str, events_48h: list) -> list:
    """48h 이내 이벤트 중 다운그레이드 감지.
    조건 (OR):
      A) action == "Downgrades"
      B) rating_new ∈ _US_SELL_RATINGS 이고 rating_old ∉ _US_SELL_RATINGS
      C) pt_change_pct < _US_DOWNGRADE_PT_THRESHOLD (-15%)
    events_48h: list of dict with keys date, firm, action, rating_new, rating_old, pt_now, pt_old, pt_change_pct.
    반환: 다운그레이드 해당 이벤트 dict list.
    """
    out = []
    for e in events_48h:
        action = (e.get("action") or "").lower()
        new_r = e.get("rating_new") or ""
        old_r = e.get("rating_old") or ""
        pt_chg = e.get("pt_change_pct")
        if action == "downgrades":
            out.append(e)
            continue
        if new_r in _US_SELL_RATINGS and old_r not in _US_SELL_RATINGS:
            out.append(e)
            continue
        if pt_chg is not None and pt_chg < _US_DOWNGRADE_PT_THRESHOLD:
            out.append(e)
            continue
    return out


async def daily_us_rating_scan(context):
    """매일 KST 07:30 (UTC 22:30) — 감시+보유 미국 종목 애널 레이팅 수집 + 텔레그램 요약.
    60종목 × 2초 ≈ 2분 예상.
    """
    try:
        from kis_api import (_stockanalysis_ratings, _save_us_ratings_to_db,
                              _save_consensus_snapshot, load_us_watchlist,
                              PORTFOLIO_FILE, load_json, _load_us_holdings_sent)
        tickers = set()
        for t in load_us_watchlist().keys():
            tickers.add(t.upper())
        portfolio = load_json(PORTFOLIO_FILE, {})
        for t in portfolio.get("us_stocks", {}).keys():
            tickers.add(t.upper())
        if not tickers:
            print("[us_ratings] 대상 종목 없음")
            return
        print(f"[us_ratings] 일일 스캔 시작 ({len(tickers)}종목)")
        inserted = 0
        failed = []
        for ticker in sorted(tickers):
            try:
                result = await _stockanalysis_ratings(ticker)
                if result:
                    async with db_write_lock:
                        inserted += _save_us_ratings_to_db(result)
                        _save_consensus_snapshot(result)
                else:
                    failed.append(ticker)
            except Exception as e:
                print(f"[us_ratings] {ticker} 실패: {e}")
                failed.append(ticker)
            await asyncio.sleep(2.0)
        print(f"[us_ratings] 완료: 신규 {inserted}건, 실패 {len(failed)}종목")

        # ━━━━━━ 신규: 텔레그램 요약 발송 ━━━━━━
        try:
            urgent_sent = _load_us_holdings_sent()
            urgent_sent_tickers = {k.split("_")[0] for k in urgent_sent.keys()}
            msg = _format_daily_rating_summary(
                tickers=sorted(tickers),
                inserted=inserted,
                failed=failed,
                urgent_sent_tickers=urgent_sent_tickers,
            )
            if msg:
                await _safe_send(context, msg)
        except Exception as e:
            print(f"[us_ratings] 텔레그램 요약 전송 실패: {e}")

    except Exception as e:
        print(f"[us_ratings] 스캔 전체 실패: {e}")


async def weekly_us_ratings_universe_scan(context):
    """매주 일요일 03:00 KST — S&P 500 ∪ Russell 1000 전체 유니버스 레이팅 수집 (애널 풀 축적용).
    ~1000종목 × 2초 ≈ 33분 예상. 진행 50종목마다 로그.
    알림은 완료 요약 1건만 (개별 이벤트 알림 없음).
    """
    import time as _time
    try:
        from kis_api import (
            _stockanalysis_ratings, _save_us_ratings_to_db, _save_consensus_snapshot,
            load_sp500_tickers, load_russell1000_tickers, load_us_scan_universe,
        )
        tickers = load_us_scan_universe()
        if not tickers:
            print("[weekly_harvest] US 유니버스 로드 실패 — 스캔 건너뜀")
            return
        sp500_n = len(load_sp500_tickers())
        russell_n = len(load_russell1000_tickers())
        total = len(tickers)
        print(f"[weekly_harvest] 시작 — {total}종목")
        start_ts = _time.monotonic()
        inserted_total = 0
        failed_count = 0
        for idx, ticker in enumerate(sorted(tickers), start=1):
            try:
                result = await _stockanalysis_ratings(ticker)
                if result:
                    async with db_write_lock:
                        new_n = _save_us_ratings_to_db(result)
                        try:
                            _save_consensus_snapshot(result)
                        except Exception:
                            pass
                    inserted_total += new_n
                    if idx % 50 == 0 or idx == total:
                        print(f"[weekly_harvest] {idx}/{total} — {ticker} {new_n}건 신규 (누적 {inserted_total})")
                else:
                    failed_count += 1
                    if idx % 50 == 0 or idx == total:
                        print(f"[weekly_harvest] {idx}/{total} — {ticker} 응답 없음 (누적 실패 {failed_count})")
            except Exception as e:
                failed_count += 1
                print(f"[weekly_harvest] {ticker} 실패: {type(e).__name__}: {e}")
            await asyncio.sleep(2.0)
        elapsed_min = (_time.monotonic() - start_ts) / 60
        print(f"[weekly_harvest] 완료: {total}종목, 신규 {inserted_total}건, 실패 {failed_count}, {elapsed_min:.1f}분")

        # 완료 알림 (1건만)
        try:
            msg = (
                "📊 주간 US 레이팅 수집 완료\n"
                f"• 스캔: {total:,}종목 (S&P500 {sp500_n} ∪ Russell1000 {russell_n})\n"
                f"• 신규 레이팅: {inserted_total}건\n"
                f"• 실패: {failed_count}종목\n"
                f"• 소요: {elapsed_min:.1f}분"
            )
            await context.bot.send_message(chat_id=CHAT_ID, text=msg)
        except Exception as e:
            print(f"[weekly_harvest] 완료 알림 실패: {e}")
    except Exception as e:
        print(f"[weekly_harvest] 전체 실패: {type(e).__name__}: {e}")


async def weekly_us_analyst_sync(context):
    """주간 US 애널 마스터 자동 동기화 (일요일 04:00 KST, harvest 끝난 직후).

    us_analyst_ratings 1,902명 → us_analysts 마스터 자동 인구 + 별점 4.5+ 콜 5+ 자동 watched=1.
    discovery 시그널 풀 확장이 목적.
    """
    try:
        from db_collector import sync_us_analyst_master
        async with db_write_lock:
            result = await asyncio.to_thread(sync_us_analyst_master)
        msg = (
            "🔄 US 애널 마스터 동기화 완료\n"
            f"• 신규 애널: {result['inserted']}명\n"
            f"• 자동 watched=1 (Tier A): {result['auto_watched_a']}명\n"
            f"• Tier S 엘리트: {result['tier_s_count']}명\n"
            f"• 마스터 총: {result['total_master']}명 / watched: {result['total_watched']}명\n"
            f"• 기준: {result['criteria']}"
        )
        print(f"[us_analyst_sync] {result}")
        await context.bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        print(f"[us_analyst_sync] 실패: {type(e).__name__}: {e}")


async def hourly_us_holdings_check(context):
    """보유 미국 종목 다운그레이드 실시간 감시. ET 12:00 / 16:30 두 번 실행.
    발송 조건 (AND):
      - 보유 종목 (portfolio.us_stocks)
      - 최근 48h 신규 이벤트 2건 이상
      - 그 중 다운그레이드 1건 이상
    중복 방지: us_holdings_sent.json 키 'TICKER_YYYY-MM-DD' 로 하루 1회만.
    """
    try:
        from kis_api import (
            _stockanalysis_ratings, _save_us_ratings_to_db, _save_consensus_snapshot,
            _load_us_holdings_sent, _save_us_holdings_sent,
            PORTFOLIO_FILE, load_json
        )
        from db_collector import _get_db

        portfolio = load_json(PORTFOLIO_FILE, {})
        tickers = sorted({t.upper() for t in portfolio.get("us_stocks", {}).keys()})
        if not tickers:
            print("[us_holdings] 보유 미국 종목 없음")
            return

        # 1. 신규 데이터 fetch (incremental)
        print(f"[us_holdings] 보유 {len(tickers)}종목 감시 시작")
        for ticker in tickers:
            try:
                result = await _stockanalysis_ratings(ticker)
                if result:
                    async with db_write_lock:
                        _save_us_ratings_to_db(result)
                        _save_consensus_snapshot(result)
            except Exception as e:
                print(f"[us_holdings] {ticker} fetch 실패: {e}")
            await asyncio.sleep(2.0)

        # 2. 다운그레이드 감지 + 알림
        sent = _load_us_holdings_sent()
        conn = _get_db()
        # ET 기준 날짜로 중복키 — 12:00/16:30 ET 이 KST 기준 날짜 경계 넘어도 같은 키
        today_str = datetime.now(ET).strftime("%Y-%m-%d")
        try:
            for ticker in tickers:
                sent_key = f"{ticker}_{today_str}"
                if sent_key in sent:
                    continue  # 오늘 이미 발송
                rows = conn.execute(
                    "SELECT r.rating_date, r.rating_time, r.firm, r.analyst, r.action, "
                    "       r.rating_new, r.rating_old, r.pt_now, r.pt_old, r.pt_change_pct, "
                    "       COALESCE(a.stars, r.stars) AS stars, "
                    "       COALESCE(a.watched, 0) AS watched, "
                    "       COALESCE(a.success_rate, r.success_rate) AS sr, "
                    "       COALESCE(a.total_ratings, r.total_ratings) AS calls, "
                    "       COALESCE(a.avg_return, r.avg_return) AS ret "
                    "FROM us_analyst_ratings r "
                    "LEFT JOIN us_analysts a ON r.analyst_slug = a.slug "
                    "WHERE r.ticker=? "
                    "  AND r.rating_date >= date('now', '-2 days') "
                    "ORDER BY r.rating_date DESC, r.rating_time DESC",
                    (ticker,)
                ).fetchall()
                if len(rows) < 2:
                    continue  # 48h 내 신규 2건 미만
                from db_collector import is_tier_s_analyst
                events = [
                    {"date": r[0], "time": r[1], "firm": r[2], "analyst": r[3],
                     "action": r[4], "rating_new": r[5], "rating_old": r[6],
                     "pt_now": r[7], "pt_old": r[8], "pt_change_pct": r[9],
                     "stars": r[10], "watched": bool(r[11]),
                     "tier_s": is_tier_s_analyst(r[10], r[12], r[13], r[14])}
                    for r in rows
                ]
                downgrades = _detect_new_downgrades(ticker, events)
                if not downgrades:
                    continue
                # 조건 충족 → 긴급 알림
                msg = _format_urgent_downgrade_alert(ticker, events, downgrades)
                try:
                    await _safe_send(context, msg)
                    sent[sent_key] = {
                        "sent_at": datetime.now().isoformat(),
                        "events_count": len(events),
                        "downgrades": [f"{d.get('firm')} {d.get('rating_old')}→{d.get('rating_new')}" for d in downgrades],
                    }
                    print(f"[us_holdings] 🚨 {ticker} 긴급 발송 ({len(downgrades)} downgrades)")
                except Exception as e:
                    print(f"[us_holdings] {ticker} 텔레그램 발송 실패: {e}")
        finally:
            conn.close()
        _save_us_holdings_sent(sent)
    except Exception as e:
        print(f"[us_holdings] 감시 전체 실패: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 주간 미국 애널 리포트 (일요일 19:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def weekly_us_analyst_report(context):
    """매주 일요일 19:00 KST — 주간 미국 애널 활동 요약.
    내용:
    1. 톱 애널 (watched=1) 이번주 활동
    2. Discovery TOP 10 (감시 밖 + 상향 집중 종목)
    3. 보유/감시 종목 컨센서스 변화 요약
    """
    try:
        from kis_api import load_us_watchlist, PORTFOLIO_FILE, load_json
        from db_collector import _get_db
        conn = _get_db()
        try:
            today_kst = datetime.now(KST)
            week_label = f"{(today_kst - timedelta(days=6)).strftime('%m/%d')}~{today_kst.strftime('%m/%d')}"

            lines = [f"📊 *Weekly Analyst Digest* ({week_label})", ""]

            # 1. 톱 애널 활동 (최근 7일)
            top_activity = conn.execute(
                "SELECT a.name, a.firm, "
                "       SUM(CASE WHEN r.action='Upgrades' THEN 1 ELSE 0 END) AS up_n, "
                "       SUM(CASE WHEN r.action='Downgrades' THEN 1 ELSE 0 END) AS down_n, "
                "       COUNT(*) AS total "
                "FROM us_analysts a "
                "LEFT JOIN us_analyst_ratings r ON a.slug = r.analyst_slug "
                "  AND r.rating_date >= date('now', '-7 days') "
                "WHERE a.watched = 1 "
                "GROUP BY a.slug "
                "HAVING total > 0 "
                "ORDER BY total DESC LIMIT 10"
            ).fetchall()
            if top_activity:
                lines.append("━━ *톱 애널 활동* ━━")
                for name, firm, up_n, down_n, total in top_activity[:10]:
                    lines.append(f"- {_md_escape(name)} ({_md_escape(firm)}): ↑{up_n} ↓{down_n} (총 {total})")
                lines.append("")
            else:
                # watched=1 없음 or 활동 없음
                top_count = conn.execute("SELECT COUNT(*) FROM us_analysts WHERE watched=1").fetchone()[0]
                if top_count == 0:
                    lines.append("_톱 애널 확정 없음 — `watch_analyst` 로 후보 확정 필요_")
                    lines.append("")

            # 2. Discovery TOP 10
            excluded = set()
            for t in load_us_watchlist().keys():
                excluded.add(t.upper())
            for t in load_json(PORTFOLIO_FILE, {}).get("us_stocks", {}).keys():
                excluded.add(t.upper())

            discovery_rows = conn.execute(
                "SELECT r.ticker, COUNT(*) AS n_up, AVG(r.pt_now) AS avg_target "
                "FROM us_analyst_ratings r "
                "JOIN us_analysts a ON r.analyst_slug = a.slug "
                "WHERE a.watched = 1 AND r.action = 'Upgrades' "
                "  AND r.rating_date >= date('now', '-7 days') "
                "GROUP BY r.ticker HAVING n_up >= 2 "
                "ORDER BY n_up DESC LIMIT 15"
            ).fetchall()
            discovery_filtered = [r for r in discovery_rows if r[0] not in excluded][:10]
            if discovery_filtered:
                lines.append("━━ *🚀 Discovery (감시 밖 신규)* ━━")
                for t, n, target in discovery_filtered:
                    target_s = f"${target:.0f}" if target else "—"
                    lines.append(f"- *{_md_escape(t)}*: {n}건 상향, avg {target_s}")
                lines.append("")

            # 3. 보유/감시 종목 컨센 변화 (최근 7일 이벤트 요약)
            tickers_union = sorted(excluded)
            if tickers_union:
                placeholders = ",".join("?" * len(tickers_union))
                portfolio_rows = conn.execute(
                    f"SELECT r.ticker, "
                    f"       SUM(CASE WHEN r.action='Upgrades' THEN 1 ELSE 0 END) AS up_n, "
                    f"       SUM(CASE WHEN r.action='Downgrades' THEN 1 ELSE 0 END) AS down_n, "
                    f"       COUNT(*) AS total "
                    f"FROM us_analyst_ratings r "
                    f"WHERE r.ticker IN ({placeholders}) "
                    f"  AND r.rating_date >= date('now', '-7 days') "
                    f"GROUP BY r.ticker HAVING total > 0 "
                    f"ORDER BY (up_n - down_n) DESC, total DESC",
                    tickers_union
                ).fetchall()
                if portfolio_rows:
                    lines.append("━━ *💼 내 종목 이번주 이벤트* ━━")
                    for t, up_n, down_n, total in portfolio_rows[:15]:
                        if up_n > 0 and down_n == 0:
                            lines.append(f"- {_md_escape(t)}: ↑{up_n}건")
                        elif down_n > 0 and up_n == 0:
                            lines.append(f"- {_md_escape(t)}: ↓{down_n}건 ⚠️")
                        else:
                            lines.append(f"- {_md_escape(t)}: ↑{up_n} ↓{down_n}")
                    lines.append("")

            # 이벤트 전무
            if len(lines) <= 3:
                lines.append("_이번주 활동 없음_")

            msg = "\n".join(lines)
            if len(msg) > 4000:
                msg = msg[:3900] + "\n\n_... 4000자 제한으로 일부 생략_"

            try:
                await _safe_send(context, msg)
                print(f"[weekly_us_report] 발송 완료 ({len(msg)}자)")
            except Exception as e:
                print(f"[weekly_us_report] 텔레그램 발송 실패: {e}")

        finally:
            conn.close()
    except Exception as e:
        print(f"[weekly_us_report] 전체 실패: {e}")


def _md_escape(s) -> str:
    """텔레그램 Markdown V1 특수문자 이스케이프 (_ * [ `). None → —."""
    if not s:
        return "—"
    s = str(s)
    for c in ("\\", "_", "*", "[", "`"):
        s = s.replace(c, "\\" + c)
    return s


def _rating_elapsed(rdate: str) -> str:
    """rating_date → ' (YYYY-MM-DD, N일 전)'. 날짜 없으면 ''."""
    if not rdate:
        return ""
    try:
        d = datetime.strptime(rdate[:10], "%Y-%m-%d").date()
        days = (datetime.now(KST).date() - d).days
        return f" ({rdate[:10]}, {days}일 전)"
    except Exception:
        return ""


def _format_urgent_downgrade_alert(ticker: str, all_events: list, downgrades: list) -> str:
    """긴급 다운그레이드 메시지 포맷. 4096자 미만.
    Tier S(엘리트) → Tier A(톱) → 일반 3단계 차등 강조 + 별점 표시.
    """
    tier_s_dgs = [d for d in downgrades if d.get("tier_s")]
    tier_a_dgs = [d for d in downgrades if d.get("watched") and not d.get("tier_s")]
    other_dgs = [d for d in downgrades if not d.get("watched")]

    # 헤더: Tier S 우선 (가장 강한 시그널)
    if len(tier_s_dgs) >= 2:
        header = f"🚨🚨🚨 *{_md_escape(ticker)}* 엘리트 애널 {len(tier_s_dgs)}명 동시 다운"
    elif len(tier_s_dgs) == 1 and len(tier_a_dgs) >= 1:
        header = f"🚨🚨 *{_md_escape(ticker)}* 엘리트+톱 다운그레이드"
    elif len(tier_s_dgs) == 1:
        header = f"🚨🚨 *{_md_escape(ticker)}* 엘리트 애널 다운그레이드"
    elif len(tier_a_dgs) >= 2:
        header = f"🚨 *{_md_escape(ticker)}* 톱 애널 {len(tier_a_dgs)}명 동시 다운"
    elif len(tier_a_dgs) == 1:
        header = f"🚨 *{_md_escape(ticker)}* 톱 애널 다운그레이드"
    else:
        header = f"⚠️ *{_md_escape(ticker)}* 다운그레이드 경고 (일반)"

    lines = [header, ""]
    lines.append(f"최근 48h: *{len(all_events)}건* 이벤트, *{len(downgrades)}건* 다운")
    if tier_s_dgs:
        lines.append(f"  └ 🥇 엘리트 (Tier S): *{len(tier_s_dgs)}명*")
    if tier_a_dgs:
        lines.append(f"  └ 🥈 톱 (Tier A): *{len(tier_a_dgs)}명*")
    lines.append("")

    def _fmt_dg(d):
        firm = _md_escape(d.get("firm"))
        old_r = _md_escape(d.get("rating_old") or "—")
        new_r = _md_escape(d.get("rating_new") or "—")
        pt_now = d.get("pt_now")
        pt_chg = d.get("pt_change_pct")
        pt_str = f"${pt_now:.0f}" if pt_now else "—"
        chg_str = f" ({pt_chg:+.1f}%)" if pt_chg is not None else ""
        elapsed_str = _rating_elapsed(d.get("date", ""))
        stars = d.get("stars")
        star_str = f" ⭐{stars:.1f}" if stars is not None else ""
        return f"- {firm}{star_str}: {old_r}→{new_r} {pt_str}{chg_str}{elapsed_str}"

    if tier_s_dgs:
        lines.append("*🥇 엘리트 다운그레이드:*")
        for d in tier_s_dgs[:5]:
            lines.append(_fmt_dg(d))
        if len(tier_s_dgs) > 5:
            lines.append(f"... +{len(tier_s_dgs) - 5}건 더")
        lines.append("")

    if tier_a_dgs:
        lines.append("*🥈 톱 다운그레이드:*")
        for d in tier_a_dgs[:5]:
            lines.append(_fmt_dg(d))
        if len(tier_a_dgs) > 5:
            lines.append(f"... +{len(tier_a_dgs) - 5}건 더")
        lines.append("")

    if other_dgs:
        lines.append(f"*일반 다운그레이드:* {len(other_dgs)}건")
        for d in other_dgs[:2]:
            lines.append(_fmt_dg(d))
        if len(other_dgs) > 2:
            lines.append(f"... +{len(other_dgs) - 2}건 더")

    # 비중 조정 권고 (강도 차등)
    if len(tier_s_dgs) >= 2:
        lines.append("")
        lines.append("→ *⚠️ 즉시 비중 축소 검토 (엘리트 동시)*")
    elif len(tier_s_dgs) >= 1 or len(tier_a_dgs) >= 2:
        lines.append("")
        lines.append("→ *비중 축소 검토 권장*")

    return "\n".join(lines)


def _format_daily_rating_summary(tickers: list, inserted: int, failed: list,
                                  urgent_sent_tickers: set) -> str:
    """일일 스캔 텔레그램 요약. 긴급 이미 발송된 종목은 '이미 알림' 마크.
    축약: 내 종목 10개 초과 시 '... N more'.
    """
    from db_collector import _get_db
    conn = _get_db()
    kst_now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    try:
        lines = [f"📊 *미국 애널 스캔* ({kst_now})", ""]

        # 내 종목 섹션 (최근 4일 이벤트, rating_date 기준)
        # us_analysts JOIN — 별점/적중률/콜수/평균수익률 모두 가져옴 → Tier S 판정
        from db_collector import is_tier_s_analyst
        my_section = []
        downgrade_section = []         # 일반 다운그레이드
        tier_a_downgrade_section = []  # Tier A 다운그레이드
        tier_s_downgrade_section = []  # Tier S 엘리트 다운그레이드 (최강조)
        for ticker in tickers:
            rows = conn.execute(
                "SELECT r.firm, r.action, r.rating_new, r.rating_old, "
                "       r.pt_now, r.pt_change_pct, r.rating_date, "
                "       COALESCE(a.stars, r.stars) AS stars, "
                "       COALESCE(a.watched, 0) AS watched, "
                "       COALESCE(a.success_rate, r.success_rate) AS sr, "
                "       COALESCE(a.total_ratings, r.total_ratings) AS calls, "
                "       COALESCE(a.avg_return, r.avg_return) AS ret "
                "FROM us_analyst_ratings r "
                "LEFT JOIN us_analysts a ON r.analyst_slug = a.slug "
                "WHERE r.ticker=? "
                "  AND r.rating_date >= date('now', '-4 days') "
                "ORDER BY r.rating_date DESC, r.rating_time DESC",
                (ticker,)
            ).fetchall()
            # Hold→Hold 무변화 제외 (Maintains/Reiterates + target 미변동)
            rows = [r for r in rows
                    if not ((r[1] or "").lower() in ("maintains", "reiterates") and not r[5])]
            if not rows:
                continue
            already_sent = "⚠️ 이미 알림" if ticker in urgent_sent_tickers else ""
            # 다운그레이드 분리 (Tier S / Tier A / 일반)
            dgs = [r for r in rows if (r[1] or "").lower() == "downgrades"]
            tier_s_dgs = [r for r in dgs if is_tier_s_analyst(r[7], r[9], r[10], r[11])]
            tier_a_dgs = [r for r in dgs if r[8] and not is_tier_s_analyst(r[7], r[9], r[10], r[11])]
            other_dgs = [r for r in dgs if not r[8]]

            def _fmt_row(r, prefix=""):
                firm, act, new_r, old_r, pt, pt_chg, rdate, stars = r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]
                pt_str = f"${pt:.0f}" if pt else "—"
                star_str = f" ⭐{stars:.1f}" if stars is not None else ""
                return f"{prefix}*{_md_escape(ticker)}*: {_md_escape(firm)}{star_str} {_md_escape(new_r)} {pt_str}{_rating_elapsed(rdate)} {already_sent}"

            if tier_s_dgs:
                for r in tier_s_dgs[:2]:
                    tier_s_downgrade_section.append(_fmt_row(r, "- 🥇 "))
                if len(tier_s_dgs) >= 2:
                    tier_s_downgrade_section.append(f"  ⚠️⚠️ {_md_escape(ticker)} 엘리트 {len(tier_s_dgs)}명 동시 다운 → 즉시 비중 축소")

            if tier_a_dgs and not tier_s_dgs:
                for r in tier_a_dgs[:2]:
                    tier_a_downgrade_section.append(_fmt_row(r, "- 🥈 "))
                if len(tier_a_dgs) >= 2:
                    tier_a_downgrade_section.append(f"  ⚠️ {_md_escape(ticker)} 톱 {len(tier_a_dgs)}명 동시 다운 → 비중 축소 검토")

            if other_dgs and not tier_s_dgs and not tier_a_dgs:
                # 엘리트/톱 다운 없을 때만 일반 표시
                for r in other_dgs[:2]:
                    downgrade_section.append(_fmt_row(r, "- "))

            if not dgs:
                # 상향/유지 표시 (날짜 + 별점 + Tier 마크)
                def _firm_str(r):
                    firm = _md_escape(r[0])
                    stars = r[7]
                    is_s = is_tier_s_analyst(r[7], r[9], r[10], r[11])
                    tier_mark = "🥇" if is_s else ("🥈" if r[8] else "")
                    star_mark = f"⭐{stars:.1f}" if stars is not None else ""
                    pt_str = f" ${r[4]:.0f}" if r[4] else ""
                    return f"{tier_mark}{firm}{star_mark}{pt_str}{_rating_elapsed(r[6])}"
                firms = ", ".join(_firm_str(r) for r in rows[:2])
                my_section.append(f"- {_md_escape(ticker)}: {len(rows)}건 ({firms}) {already_sent}")

        orig_my_count = len(my_section)  # 축약 전 원본 카운트 (폴백 메시지용)

        # 1. Tier S 엘리트 다운그레이드 (최우선, 최강 시그널)
        if tier_s_downgrade_section:
            lines.append("━━ 🥇 *엘리트 다운그레이드 (Tier S)* ━━")
            lines.extend(tier_s_downgrade_section[:10])
            if len(tier_s_downgrade_section) > 10:
                lines.append(f"... +{len(tier_s_downgrade_section) - 10}건 더")
            lines.append("")

        # 2. Tier A 톱 다운그레이드
        if tier_a_downgrade_section:
            lines.append("━━ 🥈 *톱 다운그레이드 (Tier A)* ━━")
            lines.extend(tier_a_downgrade_section[:10])
            if len(tier_a_downgrade_section) > 10:
                lines.append(f"... +{len(tier_a_downgrade_section) - 10}건 더")
            lines.append("")

        if my_section:
            # 축약 전략: 10개 초과면 잘라내기
            if len(my_section) > 10:
                cut = my_section[:10]
                cut.append(f"... +{len(my_section) - 10}종목 더")
                my_section = cut
            lines.append("━━ *내 종목* ━━")
            lines.extend(my_section)
            lines.append("")

        # 3. 일반 다운그레이드 (엘리트/톱 없을 때만 표시)
        if downgrade_section:
            lines.append("━━ *다운그레이드 (일반)* ━━")
            lines.extend(downgrade_section[:10])
            if len(downgrade_section) > 10:
                lines.append(f"... +{len(downgrade_section) - 10}건 더")
            lines.append("")

        # 통계
        lines.append("━━ *통계* ━━")
        lines.append(f"스캔 {len(tickers)}종목 / 신규 이벤트 {inserted}건 / 실패 {len(failed)}")

        msg = "\n".join(lines)
        # 4096자 체크 (안전 마진)
        if len(msg) > 4000:
            # 압축 — 내 종목 상세 생략, Tier S/A 보존
            lines = [f"📊 *미국 애널 스캔* ({kst_now})", ""]
            if tier_s_downgrade_section:
                lines.append("━━ 🥇 *엘리트 다운그레이드* ━━")
                lines.extend(tier_s_downgrade_section[:5])
                lines.append("")
            if tier_a_downgrade_section:
                lines.append("━━ 🥈 *톱 다운그레이드* ━━")
                lines.extend(tier_a_downgrade_section[:5])
                lines.append("")
            if downgrade_section:
                lines.append("━━ *다운그레이드 (일반)* ━━")
                lines.extend(downgrade_section[:3])
                lines.append("")
            lines.append(f"내 종목 이벤트: {orig_my_count}종목 (상세 생략)")
            lines.append(f"스캔 {len(tickers)}종목 / 신규 {inserted}건 / 실패 {len(failed)}")
            msg = "\n".join(lines)
        any_section = (my_section or downgrade_section or tier_a_downgrade_section or tier_s_downgrade_section)
        return msg if any_section else ""  # 이벤트 없으면 빈 문자열 → 발송 안 함
    finally:
        conn.close()
