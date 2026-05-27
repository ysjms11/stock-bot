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

# ── weekly_review, snapshot_and_drawdown ──

async def weekly_review(context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📋 *주간 리뷰 시간입니다*\n\n"
        "Claude에서 점검하세요:\n"
        "1️⃣ 보유 종목 Thesis 유효?\n"
        "2️⃣ 손절/익절 대상?\n"
        "3️⃣ 섹터 모멘텀 생존?\n"
        "4️⃣ 다음 주 매크로 이벤트?\n"
        "5️⃣ 현금 비중 적절?\n\n"
        "💡 스크린샷 + \"리뷰해줘\" 보내세요"
    )
    # ── 컨센서스 변동 (전주 vs 이번주) ──
    try:
        _cc = load_consensus_cache()
        changes = []
        for ticker, cd in _cc.get("kr", {}).items():
            prev_avg = cd.get("prev_avg")
            cur_avg  = cd.get("avg")
            if prev_avg and cur_avg and int(prev_avg) != int(cur_avg):
                diff_pct = (cur_avg - prev_avg) / prev_avg * 100
                name = cd.get("name", ticker)
                k_p = f"{int(prev_avg/1000)}K" if prev_avg >= 1000 else str(int(prev_avg))
                k_c = f"{int(cur_avg/1000)}K"  if cur_avg  >= 1000 else str(int(cur_avg))
                arrow = "↑" if diff_pct > 0 else "↓"
                changes.append(f"{name} {k_p}→{k_c} ({arrow}{abs(diff_pct):.1f}%)")
        for ticker, cd in _cc.get("us", {}).items():
            prev_avg = cd.get("prev_avg")
            cur_avg  = cd.get("avg")
            if prev_avg and cur_avg and round(float(prev_avg), 1) != round(float(cur_avg), 1):
                diff_pct = (cur_avg - prev_avg) / prev_avg * 100
                name = cd.get("name", ticker)
                arrow = "↑" if diff_pct > 0 else "↓"
                changes.append(f"{ticker}({name}) ${prev_avg:.0f}→${cur_avg:.0f} ({arrow}{abs(diff_pct):.1f}%)")
        if changes:
            msg += "\n\n📊 *컨센서스 변동*\n" + "\n".join(changes)
    except Exception:
        pass

    # ── 이번 주 매매 요약 ──
    try:
        stats = get_trade_stats("month")
        trades_this_week = [
            t for t in stats.get("trades", [])
            if t.get("side") == "sell" and t.get("date", "") >= (datetime.now(KST) - timedelta(days=7)).strftime("%Y-%m-%d")
        ]
        if trades_this_week:
            total_pnl_w = sum(t.get("pnl", 0) or 0 for t in trades_this_week)
            wins_w  = sum(1 for t in trades_this_week if t.get("result") == "win")
            lines_w = [f"\n\n💼 *이번 주 매매* ({len(trades_this_week)}건, 승률 {wins_w}/{len(trades_this_week)}, 손익 {total_pnl_w:+,.0f})"]
            for t in trades_this_week:
                pnl_str = f"{t.get('pnl', 0):+,.0f}" if t.get("pnl") is not None else "?"
                pnl_pct = f"{t.get('pnl_pct', 0):+.1f}%" if t.get("pnl_pct") is not None else ""
                icon = "✅" if t.get("result") == "win" else ("❌" if t.get("result") == "loss" else "⚪")
                lines_w.append(f"{icon} {t.get('name', t['ticker'])} {pnl_str}원 ({pnl_pct})")
            msg += "\n".join(lines_w)
        # 월말이면 이번 달 전체 성과 추가 (남은 날이 7일 이하)
        now_dt = datetime.now(KST)
        import calendar as _cal
        last_day = _cal.monthrange(now_dt.year, now_dt.month)[1]
        if now_dt.day >= last_day - 6:
            ms = get_trade_stats("month")
            if ms.get("total_trades", 0) > 0:
                wr = ms.get("win_rate_pct")
                msg += (
                    f"\n\n📅 *{ms['period']} 월간 성과*"
                    f"\n승률 {wr}% ({ms['wins']}승 {ms['losses']}패 / {ms['total_trades']}건)"
                    f"\n총손익 {ms['total_pnl']:+,.0f}원 | 평균보유 {ms.get('avg_holding_days') or '?'}일"
                )
                if ms.get("best_trade"):
                    b = ms["best_trade"]
                    msg += f"\n🏆 최고: {b.get('name', b['ticker'])} {b.get('pnl_pct', 0):+.1f}%"
                if ms.get("worst_trade"):
                    w = ms["worst_trade"]
                    msg += f"\n💀 최저: {w.get('name', w['ticker'])} {w.get('pnl_pct', 0):+.1f}%"
    except Exception:
        pass
    try:
        await _safe_send(context, msg)
    except Exception as e:
        print(f"주간 리뷰 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📸 포트 스냅샷 + 드로다운 감지 (15:50 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def snapshot_and_drawdown(context: ContextTypes.DEFAULT_TYPE):
    """장마감 후 포트 스냅샷 저장 + 드로다운 경고 (규칙 위반 시에만 텔레그램 발송)"""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    try:
        token = await get_kis_token()
        await save_portfolio_snapshot(token)
    except Exception as e:
        print(f"[snapshot] 스냅샷 저장 오류: {e}")
    try:
        dd = check_drawdown()
        alerts = dd.get("alerts", [])
        if not alerts:
            return
        lines = [f"⚠️ *리스크 한도 경고* ({now.strftime('%H:%M')})"]
        wr  = dd.get("weekly_return_pct")
        mdd = dd.get("monthly_max_drawdown_pct")
        mr  = dd.get("monthly_return_pct")
        cw  = dd.get("cash_weight_pct")
        if wr is not None:
            warn = " ⚠️ 한도 초과!" if wr <= -4 else ""
            lines.append(f"\n📉 주간 수익률: {wr:+.1f}%{warn}")
        if mdd is not None:
            warn = " 🚨 한도 초과!" if mdd <= -7 else ""
            lines.append(f"📉 월간 드로다운: {mdd:.1f}%{warn}")
        elif mr is not None:
            lines.append(f"📉 월간 수익률: {mr:+.1f}%")
        if cw is not None:
            lines.append(f"💰 현금비중: {cw:.1f}%")
        for a in alerts:
            lvl = "🚨" if a["level"] == "CRITICAL" else "⚠️"
            lines.append(f"{lvl} {a['message']}")
        await _safe_send(context, "\n".join(lines))
    except Exception as e:
        print(f"[drawdown] 드로다운 체크 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📋 컨센서스 배치 캐시 (매주 일요일 07:05 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
