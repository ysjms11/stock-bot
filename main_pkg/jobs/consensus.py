"""main_pkg jobs — auto-split from main.py. See main_pkg/__init__.py."""
import asyncio
import os
import json
import re
import calendar as _cal
from datetime import datetime, timedelta, time as dtime

from telegram.ext import ContextTypes

from main_pkg._ctx import (
    _KR_SECTORS, _SECTOR_LIMIT, _STOCK_LIMIT,
    _is_kr_trading_time, _read_regime, _safe_send,
    _track_silent_failure, _reset_silent_failure, _alert_silent_failure,
    _extract_grade, _grade_arrow,
)
from kis_api import *
from kis_api import (
    _DATA_DIR, _is_us_ticker, _is_us_market_hours_kst, _is_us_market_closed, _guess_excd,
    ws_manager, get_ws_tickers, close_session,
    fetch_us_earnings_calendar, fetch_us_sector_etf,
    fetch_and_cache_disclosure, parse_disclosure_summary,
)

try:
    from report_crawler import DB_PATH as REPORT_DB_PATH
except ImportError:
    REPORT_DB_PATH = os.path.join(os.environ.get("DATA_DIR", "data"), "stock.db")

# ── weekly_consensus_update, daily_consensus_check ──

async def weekly_consensus_update(context: ContextTypes.DEFAULT_TYPE):
    """매주 일요일 07:05 KST — 포트폴리오+워치리스트+유니버스 전체 컨센서스 배치 업데이트."""
    now = datetime.now(KST)
    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_consensus_weekly"
    if _sent.get("consensus_weekly") == _key:
        return

    try:
        from copy import deepcopy
        print("[consensus_update] 컨센서스 배치 업데이트 시작")
        old_cache = deepcopy(load_json(CONSENSUS_CACHE_FILE, {}))

        # stock_master 전종목 (일요일이니 시간 무관)
        all_kr = {}
        try:
            from db_collector import _get_db
            conn = _get_db()
            rows = conn.execute("SELECT symbol, name FROM stock_master").fetchall()
            all_kr = {r["symbol"]: r["name"] for r in rows}
            conn.close()
        except Exception as e:
            print(f"[consensus_update] stock_master 조회 실패 (watch/portfolio만 사용): {e}")
        # stock_master에 없는 감시 종목도 추가
        wa = load_watchalert()
        for t, v in wa.items():
            if not _is_us_ticker(t) and t not in all_kr:
                all_kr[t] = v.get("name", t) if isinstance(v, dict) else t
        wl = load_watchlist()
        for t, n in wl.items():
            if not _is_us_ticker(t) and t not in all_kr:
                all_kr[t] = n
        print(f"[consensus_update] 대상: {len(all_kr)}종목 (universe+portfolio+watch)")

        cache = await update_consensus_cache(kr_tickers=all_kr)
        kr_cnt = len(cache.get("kr", {}))
        us_cnt = len(cache.get("us", {}))
        print(f"[consensus_update] 완료: KR {kr_cnt}종목, US {us_cnt}종목")

        # 변화 감지 (10% 이상 목표가 변동 or 신규 커버리지)
        changes = detect_consensus_changes(
            old_cache.get("kr", {}), cache.get("kr", {}),
            target_pct=10.0, detect_new_cover=True
        )
        if changes:
            msg = f"📊 *주간 컨센서스 변화* ({len(changes)}건)\n\n"
            for c in changes[:15]:
                if c["type"] == "target_up":
                    msg += f"📈 *{c['name']}* — 목표가 상향 {c['detail']}\n"
                elif c["type"] == "target_down":
                    msg += f"📉 *{c['name']}* — 목표가 하향 {c['detail']}\n"
                elif c["type"] == "opinion_change":
                    msg += f"🔄 *{c['name']}* — 의견 변경 {c['detail']}\n"
                elif c["type"] == "new_cover":
                    msg += f"🆕 *{c['name']}* — 신규 커버리지 {c['detail']}\n"
            await _safe_send(context, msg)
            _sent["consensus_weekly"] = _key
            save_json(MACRO_SENT_FILE, _sent)
        _reset_silent_failure("consensus_update_error")
    except Exception as e:
        print(f"[consensus_update] 오류: {e}")
        cnt = _track_silent_failure("consensus_update_error", threshold=2)
        if cnt:
            await _alert_silent_failure(context, "consensus_update_error", cnt,
                f"weekly_consensus_update 연속 {cnt}회 실패\n오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📋 일간 컨센서스 변화 감지 (평일 19:30 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def daily_consensus_check(context: ContextTypes.DEFAULT_TYPE):
    """매일 19:30 평일 — 보유+감시 종목 컨센서스 변화 감지."""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return

    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_consensus_daily"
    if _sent.get("consensus_daily") == _key:
        return

    try:
        from copy import deepcopy
        old_cache = deepcopy(load_json(CONSENSUS_CACHE_FILE, {}))

        # 보유+감시 한국 종목
        kr_tickers = {}
        portfolio = load_json(PORTFOLIO_FILE, {})
        for t, v in portfolio.items():
            if t not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict) and not _is_us_ticker(t):
                kr_tickers[t] = v.get("name", t)
        wa = load_watchalert()
        for t, v in wa.items():
            if not _is_us_ticker(t) and t not in kr_tickers:
                kr_tickers[t] = v.get("name", t) if isinstance(v, dict) else t
        wl = load_watchlist()
        for t, n in wl.items():
            if not _is_us_ticker(t) and t not in kr_tickers:
                kr_tickers[t] = n

        if not kr_tickers:
            return

        await update_consensus_cache(kr_tickers=kr_tickers)
        new_cache = load_json(CONSENSUS_CACHE_FILE, {})

        changes = detect_consensus_changes(
            old_cache.get("kr", {}), new_cache.get("kr", {}),
            target_pct=5.0, detect_new_cover=False
        )

        # 🆕 누적 trend 감지 (점진 상향도 캐치, 단일 일 임계 미달분)
        trends = []
        changes_tickers = {c.get("ticker") for c in changes}  # 단일 일 알림 종목 제외
        try:
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(REPORT_DB_PATH, timeout=10)
            conn.execute("PRAGMA cache_size = -65536")
            conn.execute("PRAGMA temp_store = MEMORY")
            conn.execute("PRAGMA mmap_size = 268435456")
            conn.execute("PRAGMA busy_timeout = 30000")
            cutoff = (now - timedelta(days=15)).strftime("%Y%m%d")  # 2주치 점진 상향 캐치
            for ticker, name in kr_tickers.items():
                if ticker in changes_tickers:
                    continue  # 단일 일 changes에 이미 들어감
                rows = conn.execute(
                    "SELECT trade_date, target_avg FROM consensus_history "
                    "WHERE symbol=? AND trade_date >= ? "
                    "ORDER BY trade_date",
                    (ticker, cutoff),
                ).fetchall()
                if len(rows) < 2:
                    continue
                old_target = rows[0][1]
                new_target = rows[-1][1]
                if not (old_target and new_target) or old_target <= 0:
                    continue
                pct = (new_target - old_target) / old_target * 100
                # 30%+ 변화는 corporate action (액면분할/합병) 노이즈 → 제외
                if abs(pct) >= 30.0:
                    continue
                # 누적 3%+ 변화 (점진 상향/하향)
                if abs(pct) >= 3.0:
                    trends.append({
                        "ticker": ticker, "name": name,
                        "old_target": int(old_target), "new_target": int(new_target),
                        "pct": pct, "days": len(rows),
                    })
            conn.close()
        except Exception as _e:
            print(f"[daily_consensus] trend 감지 실패: {_e}")

        if changes or trends:
            msg = f"📊 *컨센서스 변화 감지* ({len(changes) + len(trends)}건)\n\n"
            # 단일 일 5%+ 변화 (급변)
            for c in changes[:8]:
                if c["type"] == "target_up":
                    msg += f"📈 *{c['name']}* — 목표가 상향 {c['detail']}\n"
                elif c["type"] == "target_down":
                    msg += f"📉 *{c['name']}* — 목표가 하향 {c['detail']}\n"
                elif c["type"] == "opinion_change":
                    msg += f"🔄 *{c['name']}* — 의견 변경 {c['detail']}\n"
            # 누적 trend (점진 상향/하향)
            if trends:
                if changes:
                    msg += "\n_누적 추세 (7~10일):_\n"
                for t in sorted(trends, key=lambda x: -abs(x["pct"]))[:8]:
                    arrow = "📈" if t["pct"] > 0 else "📉"
                    sign = "+" if t["pct"] > 0 else ""
                    msg += (f"{arrow} *{t['name']}* {t['old_target']:,}→{t['new_target']:,} "
                            f"({sign}{t['pct']:.1f}%, {t['days']}일)\n")
            await _safe_send(context, msg)
            _sent["consensus_daily"] = _key
            save_json(MACRO_SENT_FILE, _sent)

        print(f"[daily_consensus] {len(kr_tickers)}종목 수집, 단일변화 {len(changes)}건, 누적추세 {len(trends)}건")
    except Exception as e:
        print(f"[daily_consensus] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 일일 발굴 알림 (매일 19:05 KST, 평일)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHANGE_SCAN_SENT_FILE = f"{_DATA_DIR}/change_scan_sent.json"


