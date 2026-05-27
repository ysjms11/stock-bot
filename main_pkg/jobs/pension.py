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

# ── daily_pension_* ──

async def daily_pension_collect(context: ContextTypes.DEFAULT_TYPE):
    """매일 16:30 KST (평일) — 종목별 연기금 매매 수집 → DB 저장.

    학습 #27 적용 (5/8): saved=0 (수집 0건) 평일 3일 연속 시 텔레그램 escalate.
    pykrx KRX_ID/KRX_PW 만료 의심 알림 — 침묵 fallback 방지.
    """
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    try:
        from kis_api import collect_pension_flow_daily
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, collect_pension_flow_daily, None)
        print(f"[pension_collect] {result}")

        # silent failure 추적: 평일에 saved=0이면 카운트, 정상이면 리셋
        saved = int(result.get("saved", 0)) if isinstance(result, dict) else 0
        if saved == 0:
            cnt = _track_silent_failure("pension_collect_zero", threshold=3)
            if cnt:
                await _alert_silent_failure(
                    context, "pension_collect_zero", cnt,
                    "*연기금 수급 데이터 0건 (평일)*\n"
                    "→ pykrx 자동로그인 실패 의심.\n"
                    "확인: `.env` 의 KRX_ID / KRX_PW 만료 여부 (KRX 정보데이터시스템 비번 갱신 주기 90일).\n"
                    "복구 후엔 다음 평일 16:30 자동 재수집."
                )
        else:
            _reset_silent_failure("pension_collect_zero")
    except Exception as e:
        print(f"[pension_collect] 오류: {e}")


async def daily_nps_dart_increment(context: ContextTypes.DEFAULT_TYPE):
    """매일 04:00 KST — NPS 5%룰 DART 증분 수집.

    NPS는 분기 단위 일제 보고 + 분기 사이 변동시 5일 내 보고 의무.
    매일 D001 검색 → repror=='국민연금공단' 만 nps_holdings_disclosed에 살붙이기.
    신규 NPS 보고 발생 시 텔레그램 알림.
    """
    try:
        from kis_api import collect_nps_dart_increments
        r = await collect_nps_dart_increments(days=7)  # 최근 7일 안전 마진
        print(f"[nps_dart_inc] {r}")
        new_n = r.get("nps_inserted", 0)
        if new_n <= 0:
            return  # 신규 보고 없으면 silent
        import sqlite3 as _s
        db_path = f"{_DATA_DIR}/stock.db"
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        cutoff_iso = (datetime.now(KST) - timedelta(hours=24)).isoformat()
        recent = conn.execute(
            """SELECT report_date, company_name, ratio_pct, stkqy_irds, report_resn
               FROM nps_holdings_disclosed
               WHERE source = 'dart' AND collected_at >= ?
               ORDER BY ABS(stkqy_irds) DESC LIMIT 10""",
            (cutoff_iso,),
        ).fetchall()
        conn.close()
        if not recent:
            return
        lines = [f"🏛 *NPS 5%룰 DART 신규 보고 {len(recent)}건*"]
        for x in recent:
            qty = x["stkqy_irds"] or 0
            arrow = "▲" if qty > 0 else "▼"
            lines.append(
                f"  {arrow} *{x['company_name']}* {qty:+,}주 "
                f"(지분 {x['ratio_pct']:.2f}%) — {x['report_date']}"
            )
        await _safe_send(context, "\n".join(lines))
    except Exception as e:
        print(f"[nps_dart_inc] 오류: {e}")


async def weekly_nps_collect(context: ContextTypes.DEFAULT_TYPE):
    """매주 일요일 03:30 KST — NPS 5%룰 (KR) + 13F-HR (US) 통합 수집.

    KR: data.go.kr CSV (분기 갱신 시 신규분 누적)
    US: SEC EDGAR 13F-HR XML (분기말 +45일 ~ 제출, 4분기 백필)
    """
    msg_lines = []

    # ── 1) KR 5%룰 ──
    try:
        from kis_api import collect_nps_5percent_disclosed
        kr_result = await collect_nps_5percent_disclosed()
        print(f"[nps_kr_5pct] {kr_result}")
        new_kr = kr_result.get("inserted_new", 0)
        if new_kr > 0 and not kr_result.get("error"):
            quarters = kr_result.get("quarters", [])
            qstr = ", ".join(quarters) if quarters else ""
            msg_lines.append(
                f"📊 *NPS KR 5%룰 신규 {new_kr}건* ({qstr})\n"
                f"매칭: {kr_result.get('matched',0)}/{kr_result.get('total_csv',0)}"
            )
    except Exception as e:
        print(f"[nps_kr_5pct] 오류: {e}")

    # ── 2) US 13F-HR ──
    try:
        from kis_api import collect_nps_us_13f
        us_result = await collect_nps_us_13f(max_quarters=2)  # 매주 직전 2분기 점검
        print(f"[nps_us_13f] {us_result}")
        new_us = us_result.get("total_rows_inserted", 0)
        if new_us > 0 and not us_result.get("error"):
            new_filings = [
                f for f in us_result.get("filings", [])
                if f.get("status") == "inserted"
            ]
            qs = ", ".join(f"{x['quarter']}({x['rows']})" for x in new_filings)
            msg_lines.append(
                f"🇺🇸 *NPS US 13F 신규 {new_us}건* — {qs}"
            )
    except Exception as e:
        print(f"[nps_us_13f] 오류: {e}")

    # ── 2.5) NPS 5%룰 DART 90일 풀 백필 (분기 사이 누적 보고 보완) ──
    try:
        from kis_api import collect_nps_dart_increments
        ni = await collect_nps_dart_increments(days=90)
        print(f"[nps_dart_inc weekly 90d] {ni}")
    except Exception as e:
        print(f"[nps_dart_inc weekly] 오류: {e}")

    # ── 3) KR 풀 포트 (whale-insight 미러) ──
    # 매주 silent 갱신. 분기 라벨 변경 시에만 알림.
    try:
        from kis_api import collect_nps_kr_full_from_whale_insight
        # 직전 snapshot의 quarter_label
        import sqlite3 as _s
        prev_label = ""
        try:
            _conn = _s.connect(f"{_DATA_DIR}/stock.db", timeout=10)
            _conn.execute("PRAGMA cache_size = -65536;")
            _conn.execute("PRAGMA temp_store = MEMORY;")
            _conn.execute("PRAGMA mmap_size = 268435456;")
            _conn.execute("PRAGMA busy_timeout = 30000;")
            row = _conn.execute(
                "SELECT quarter_label FROM nps_kr_full_holdings "
                "ORDER BY snapshot_date DESC LIMIT 1"
            ).fetchone()
            if row:
                prev_label = row[0] or ""
            _conn.close()
        except Exception:
            pass
        kr_result = await collect_nps_kr_full_from_whale_insight()
        print(f"[nps_kr_full] {kr_result}")
        if not kr_result.get("error"):
            new_label = kr_result.get("quarter_label", "")
            if new_label and new_label != prev_label:
                msg_lines.append(
                    f"🇰🇷 *NPS KR 풀포트 갱신* — {prev_label or '신규'} → {new_label}\n"
                    f"{kr_result.get('total_rows', 0)}종목, "
                    f"매칭 {kr_result.get('matched', 0)}/{kr_result.get('total_rows', 0)}"
                )
    except Exception as e:
        print(f"[nps_kr_full] 오류: {e}")

    # ── 4) 와이즈리포트 5%룰 변동 수집 (학습 #13 3번째 재현 fix - 5/9) ──
    # 함수 정의만 있고 호출 site 0건 - 4/1 이후 38일 정체
    try:
        from kis_api import collect_wi_changes
        r5pct = await collect_wi_changes()
        print(f"[wi_5pct] {r5pct}", flush=True)
        wi_inserted = r5pct.get("major_inserted", 0) + r5pct.get("ele_inserted", 0)
        if wi_inserted == 0:
            cnt = _track_silent_failure("wi_5pct_zero", threshold=2)
            if cnt:
                await _alert_silent_failure(
                    context, "wi_5pct_zero", cnt,
                    f"weekly_nps step4 (wi_5pct) {cnt}주 연속 0건"
                )
        else:
            _reset_silent_failure("wi_5pct_zero")
    except Exception as e:
        print(f"[wi_5pct] 오류: {e}", flush=True)

    # 텔레그램 알림 (신규 있을 때만)
    if msg_lines:
        try:
            await _safe_send(context, "\n\n".join(msg_lines))
        except Exception as e:
            print(f"[nps_collect] 텔레그램 발송 오류: {e}")


async def daily_pension_alert(context: ContextTypes.DEFAULT_TYPE):
    """매일 19:00 KST (평일) — 5일 누적 연기금 매매 시그널 텔레그램 알림.

    시총 대비 % 기준 정렬. 절대금액 보조 표시.
    임계값: 0.3%+ 매수/매도 (소형주도 포함, 절대금액 무관).
    너 포트/워치 종목은 무조건 양방향 표시.
    """
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    _sent = load_json(MACRO_SENT_FILE, {})
    _key = f"{now.strftime('%Y-%m-%d')}_pension_alert"
    if _sent.get("pension_alert") == _key:
        return

    try:
        import sqlite3 as _s
        conn = _s.connect(REPORT_DB_PATH, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        # 5일 누적 (영업일 기준 추정 — 수집 공백 대비 14일 cal day cutoff)
        cutoff_dt = (now - timedelta(days=14)).strftime("%Y%m%d")

        # 5일 누적 매매 + 시총 join (daily_snapshot)
        rows = conn.execute("""
            SELECT pf.symbol, pf.name,
                   SUM(pf.net_amount_won) as net_5d,
                   ds.market_cap, ds.close
            FROM pension_flow_daily pf
            LEFT JOIN daily_snapshot ds ON ds.symbol = pf.symbol
                AND ds.trade_date = (
                    SELECT MAX(trade_date) FROM daily_snapshot
                    WHERE symbol = pf.symbol
                )
            WHERE pf.trade_date >= ?
            GROUP BY pf.symbol
            HAVING ABS(net_5d) > 0
        """, (cutoff_dt,)).fetchall()
        conn.close()

        if not rows:
            print("[pension_alert] 데이터 없음")
            return

        # 시총 대비 % 계산 (market_cap 단위 = 억원)
        # net_5d 단위 = 원
        items = []
        for symbol, name, net_5d, mcap_eok, close in rows:
            if not mcap_eok or mcap_eok <= 0:
                continue
            mcap_won = mcap_eok * 100_000_000  # 억 → 원
            pct = (net_5d / mcap_won) * 100
            items.append({
                "ticker": symbol, "name": name or symbol,
                "net_5d": int(net_5d), "mcap_eok": int(mcap_eok),
                "pct": pct,
            })

        if not items:
            print("[pension_alert] 시총 매핑 0")
            return

        # 보유/워치 분리
        held_set = set()
        watch_set = set()
        try:
            pf = load_json(PORTFOLIO_FILE, {})
            for k in pf.keys():
                if k not in ("us_stocks", "cash_krw", "cash_usd") and not _is_us_ticker(k):
                    held_set.add(k)
            for k in load_watchalert().keys():
                if not _is_us_ticker(k) and k not in held_set:
                    watch_set.add(k)
        except Exception:
            pass
        held_watch_set = held_set | watch_set

        # 분류 + 정렬 (% 기준)
        held_flow = sorted(
            [x for x in items if x["ticker"] in held_set],
            key=lambda x: -abs(x["pct"]),
        )
        watch_flow = sorted(
            [x for x in items if x["ticker"] in watch_set],
            key=lambda x: -abs(x["pct"]),
        )
        external_buys = [x for x in items if x["ticker"] not in held_watch_set
                          and x["net_5d"] > 0]
        # TOP 10 by 시총 대비 % (강도 기준)
        buy_top_pct = sorted(
            [x for x in external_buys if x["pct"] >= 0.3],
            key=lambda x: -x["pct"],
        )[:10]
        # TOP 10 by 절대금액 (시장 임팩트 기준)
        buy_top_amount = sorted(external_buys, key=lambda x: -x["net_5d"])[:10]

        if not (buy_top_pct or buy_top_amount or held_flow or watch_flow):
            return

        def _fmt_amt(won: int) -> str:
            eok = won / 100_000_000
            sign = "+" if won > 0 else ""
            return f"{sign}{eok:,.0f}억"

        msg = f"📊 *연기금 5일 매매* ({now.strftime('%m/%d')})\n"
        msg += "_시총 대비 %_\n\n"

        def _flow_line(x: dict) -> str:
            emoji = "📈" if x["pct"] > 0 else "📉"
            ap = abs(x["pct"])
            star = ""
            if ap >= 1.0:
                star = emoji + emoji + " "
            elif ap >= 0.5:
                star = emoji + " "
            sign = "+" if x["pct"] > 0 else ""
            return f"{emoji} {star}*{x['name']}* {sign}{x['pct']:.2f}% ({_fmt_amt(x['net_5d'])})\n"

        # 1) 보유 종목 양방향
        if held_flow:
            msg += "📍 *보유 종목 양방향*\n"
            for x in held_flow[:15]:
                msg += _flow_line(x)
            msg += "\n"

        # 2) 워치 종목 양방향
        if watch_flow:
            msg += "👀 *워치 종목 양방향*\n"
            for x in watch_flow[:15]:
                msg += _flow_line(x)
            msg += "\n"

        # 2) 발굴 매수 — 시총% 기준 TOP 10 (강도 시그널)
        if buy_top_pct:
            msg += "📈 *발굴 매수 TOP 10* (시총% 기준)\n"
            for i, x in enumerate(buy_top_pct, 1):
                star = "📈📈📈 " if x["pct"] >= 1.0 else ("📈📈 " if x["pct"] >= 0.5 else "")
                msg += f"{i}. {star}*{x['name']}* +{x['pct']:.2f}% ({_fmt_amt(x['net_5d'])})\n"
            msg += "\n"

        # 3) 발굴 매수 — 절대금액 기준 TOP 10 (시장 임팩트)
        if buy_top_amount:
            msg += "💰 *발굴 매수 TOP 10* (절대금액 기준)\n"
            for i, x in enumerate(buy_top_amount, 1):
                msg += f"{i}. *{x['name']}* {_fmt_amt(x['net_5d'])} ({x['pct']:+.2f}%)\n"

        await _safe_send(context, msg, disable_web_page_preview=True)
        _sent["pension_alert"] = _key
        save_json(MACRO_SENT_FILE, _sent)
    except Exception as e:
        print(f"daily_pension_alert 오류: {e}")


