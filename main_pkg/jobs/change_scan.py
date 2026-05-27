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

# ── daily_change_scan_alert, auto_backup ──

async def daily_change_scan_alert(context: ContextTypes.DEFAULT_TYPE):
    """매일 19:05 평일 — turnaround/fscore_jump/insider_cluster_buy 스캔 → 워치/포트 제외 → 텔레그램 푸시."""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return

    try:
        from mcp_tools import _execute_tool

        # 워치+포트 제외 집합 (한국만)
        excluded = set()
        try:
            wl = load_watchlist()
            for t in wl.keys():
                if not _is_us_ticker(t):
                    excluded.add(t)
            pf = load_json(PORTFOLIO_FILE, {})
            for t, v in pf.items():
                if t in ("us_stocks", "cash_krw", "cash_usd"):
                    continue
                if isinstance(v, dict) and not _is_us_ticker(t):
                    excluded.add(t)
        except Exception as e:
            print(f"[change_scan] 워치/포트 로드 실패: {e}")

        # 쿨다운 기록 (7일)
        sent = load_json(CHANGE_SCAN_SENT_FILE, {})
        today_str = now.strftime("%Y-%m-%d")
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        cooldown_set = {t for t, d in sent.items() if isinstance(d, str) and d >= cutoff}

        # 프리셋별 스캔
        presets_config = [
            ("turnaround",           "📈 적자→흑자 전환"),
            ("fscore_jump",          "🚀 F-Score 도약"),
            ("insider_cluster_buy",  "👥 내부자 군집매수"),
        ]

        sections = []  # [(label, [item, ...])]
        new_sent_symbols = []

        for preset_name, label in presets_config:
            try:
                res = await _execute_tool("get_change_scan", {"preset": preset_name, "n": 10, "market": "all"})
            except Exception as e:
                print(f"[change_scan] {preset_name} 실행 오류: {e}")
                continue
            if not isinstance(res, dict):
                continue
            rows = res.get("results", []) or []
            filtered = []
            for r in rows:
                tk = r.get("ticker", "")
                if not tk or tk in excluded or tk in cooldown_set:
                    continue
                filtered.append(r)
                if len(filtered) >= 5:
                    break
            if filtered:
                sections.append((preset_name, label, filtered))
                for r in filtered:
                    new_sent_symbols.append(r.get("ticker", ""))

        total_hits = sum(len(items) for _, _, items in sections)
        if total_hits == 0:
            print("[change_scan] 결과 0건 — 발송 스킵")
            cnt = _track_silent_failure("change_scan_zero", threshold=3)
            if cnt:
                await _alert_silent_failure(context, "change_scan_zero", cnt,
                    f"발굴 0건 연속 {cnt}일 — daily_change_scan 점검 필요")
            return
        _reset_silent_failure("change_scan_zero")

        msg = "🔔 *오늘의 발굴* (워치/보유 제외)\n"
        for preset_name, label, items in sections:
            msg += f"\n*{label}* ({len(items)}건)\n"
            for r in items:
                tk = r.get("ticker", "")
                nm = r.get("name", tk)
                if preset_name == "turnaround":
                    delta = r.get("op_profit_delta")
                    latest = r.get("op_profit_latest")
                    prev = r.get("op_profit_prev")
                    msg += f" • `{tk}` {nm} 영업이익 {latest:+.1f} (전: {prev:+.1f})\n" if (latest is not None and prev is not None) else f" • `{tk}` {nm}\n"
                elif preset_name == "fscore_jump":
                    fn = r.get("fscore_now")
                    fp = r.get("fscore_past")
                    fd = r.get("fscore_delta")
                    msg += f" • `{tk}` {nm} F {fp}→{fn} (ΔF={fd})\n" if (fn is not None and fp is not None) else f" • `{tk}` {nm}\n"
                elif preset_name == "insider_cluster_buy":
                    nrep = r.get("insider_reprors")
                    nq = r.get("insider_net_qty")
                    msg += f" • `{tk}` {nm} 30일 {nrep}명 {nq:+,}주\n" if (nrep is not None and nq is not None) else f" • `{tk}` {nm}\n"
                else:
                    msg += f" • `{tk}` {nm}\n"

        try:
            await _safe_send(context, msg)
        except Exception as e:
            print(f"[change_scan] 텔레그램 발송 실패: {e}")
            return

        # 쿨다운 업데이트 (발송 성공 후)
        for tk in new_sent_symbols:
            if tk:
                sent[tk] = today_str
        # 만료된 항목 정리
        sent = {t: d for t, d in sent.items() if isinstance(d, str) and d >= cutoff}
        save_json(CHANGE_SCAN_SENT_FILE, sent)
        print(f"[change_scan] {total_hits}건 발송 완료")
    except Exception as e:
        print(f"[change_scan] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 💾 /data/ 자동 백업 (매일 22:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def auto_backup(context: ContextTypes.DEFAULT_TYPE):
    """매일 22:00 KST — /data/*.json GitHub Gist 자동 백업"""
    try:
        result = await backup_data_files()
        if result.get("ok"):
            files = result.get("files", [])
            print(f"[backup] 완료: {len(files)}개 파일 — {result.get('action', '')}")
        else:
            err = result.get("error", "알 수 없는 오류")
            print(f"[backup] 실패: {err}")
            if GITHUB_TOKEN:  # 설정은 됐는데 오류면 텔레그램 알림
                try:
                    await context.bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"⚠️ 자동 백업 실패: {err}"
                    )
                except Exception:
                    pass
    except Exception as e:
        print(f"[backup] 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📋 유니버스 자동 갱신 (매주 월요일 07:00 KST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
