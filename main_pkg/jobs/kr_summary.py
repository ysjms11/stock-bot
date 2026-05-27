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

# ── daily_kr_summary ──

async def daily_kr_summary(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return
    # 중복 발송 방지
    _kr_sent = load_json(MACRO_SENT_FILE, {})
    _kr_key = f"{now.strftime('%Y-%m-%d')}_kr_summary"
    if _kr_sent.get("kr_summary") == _kr_key:
        return
    try:
        token = await get_kis_token()

        # ── [시장] KOSPI + 환율 ──
        macro = await get_yahoo_quote("^KS11") or {}
        kospi_p = macro.get("price", "?")
        kospi_c = macro.get("change_pct", "?")

        fx = await get_yahoo_quote("KRW=X") or {}
        krw = int(float(fx.get("price", 0) or 0))
        kospi_c_f = round(float(kospi_c or 0), 2)
        kospi_e = "🔴" if kospi_c_f < 0 else "🟢"
        msg = f"📊 *한국 장 마감* ({now.strftime('%m/%d %H:%M')})\n\n"
        msg += f"[시장] {kospi_e} KOSPI {kospi_p} ({kospi_c_f:+.2f}%) | 💱 {krw:,}원\n"

        # ── [섹터] ETF 4개 ──
        SECTOR_ETF_4 = [
            ("140710", "조선"), ("261070", "전력"),
            ("464520", "방산"), ("469150", "AI반도체"),
        ]
        sector_parts = []
        for code, label in SECTOR_ETF_4:
            try:
                d = await kis_stock_price(code, token)
                chg = float(d.get("prdy_ctrt", 0) or 0)
                sector_parts.append(f"{label}{chg:+.2f}%")
                await asyncio.sleep(0.05)
            except Exception:
                pass
        if sector_parts:
            msg += f"[섹터] {' | '.join(sector_parts)}\n"

        # ── 포트폴리오 데이터 수집 (배치 조회) ──
        portfolio = load_json(PORTFOLIO_FILE, {})
        kr_stocks = {k: v for k, v in portfolio.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)}
        stops = load_stoploss()
        kr_stops = {k: v for k, v in stops.items() if k != "us_stocks" and isinstance(v, dict)}
        port_rows = []
        total_eval = 0.0
        total_prev_eval = 0.0
        batch = await batch_stock_detail(list(kr_stocks.keys()), token, delay=0.3)
        batch_map = {r["ticker"]: r for r in batch}
        for ticker, info in kr_stocks.items():
            d = batch_map.get(ticker, {})
            if d.get("error"):
                port_rows.append({
                    "ticker": ticker, "info": info,
                    "price": 0, "chg": 0.0,
                    "frgn_qty": 0, "tgt": 0, "tgt_pct": None,
                    "error": d["error"],
                })
                continue
            try:
                price = d.get("price", 0)
                chg   = d.get("chg_pct", 0.0)
                qty   = info.get("qty", 0)
                eval_amt   = price * qty
                prev_price = price / (1 + chg / 100) if chg != -100 else price
                total_eval      += eval_amt
                total_prev_eval += prev_price * qty
                stop_info = kr_stops.get(ticker, {})
                tgt = float(stop_info.get("target_price") or stop_info.get("target") or 0)
                tgt_pct = (tgt - price) / price * 100 if tgt > 0 and price > 0 else None
                port_rows.append({
                    "ticker": ticker, "info": info,
                    "price": price, "chg": chg,
                    "frgn_qty": d.get("frgn_net", 0), "tgt": tgt, "tgt_pct": tgt_pct,
                })
            except Exception:
                pass

        # ── [포트] 오늘 변동 + 주간 수익률 ──
        today_delta = int(total_eval - total_prev_eval)
        weekly_base = load_json(WEEKLY_BASE_FILE, {})
        base_amt = float(weekly_base.get("base_amt", 0))
        week_pct = (total_eval - base_amt) / base_amt * 100 if base_amt > 0 else 0.0
        # 월요일이면 weekly_base 갱신
        if now.weekday() == 0 and total_eval > 0:
            this_monday = now.strftime("%Y-%m-%d")
            if weekly_base.get("date") != this_monday:
                save_json(WEEKLY_BASE_FILE, {"date": this_monday, "base_amt": int(total_eval)})
        today_str = f"+{today_delta:,}" if today_delta >= 0 else f"{today_delta:,}"
        msg += f"[포트] 오늘 {today_str}원 | 이번 주 {week_pct:+.1f}%\n"
        if week_pct <= -4:
            msg += f"🔴 주간 {week_pct:.1f}% — 신규매수 금지 규칙 발동!\n"
        elif week_pct <= -3:
            msg += f"⚠️ 주간 {week_pct:.1f}% — 신규매수 주의\n"

        # ── 컨센서스 목표가 수집 (캐시 우선, 7일 초과 시 실시간 폴백) ──
        consensus_map = {}
        try:
            _cc = load_consensus_cache()
            if _cc:
                _upd = _cc.get("updated", "")
                _cache_ok = False
                if _upd:
                    try:
                        _age = (datetime.now(KST) - datetime.fromisoformat(_upd)).total_seconds() / 86400
                        _cache_ok = _age < 7
                    except (ValueError, TypeError) as e:
                        print(f"[kr_summary] 컨센서스 캐시 날짜 파싱 실패 (실시간 조회): {e}")
                if _cache_ok:
                    for row in port_rows:
                        _cd = _cc.get("kr", {}).get(row["ticker"])
                        if _cd and _cd.get("avg"):
                            consensus_map[row["ticker"]] = {
                                "avg": float(_cd["avg"]),
                                "buy": int(_cd.get("buy", 0)),
                            }
        except Exception:
            pass
        # 캐시 미스 종목만 실시간 조회
        loop = asyncio.get_event_loop()
        for row in port_rows:
            if row["ticker"] in consensus_map:
                continue
            try:
                c = await asyncio.wait_for(
                    loop.run_in_executor(None, fetch_fnguide_consensus, row["ticker"]),
                    timeout=5.0,
                )
                avg = c.get("consensus_target", {}).get("avg") if c else None
                if avg:
                    consensus_map[row["ticker"]] = {
                        "avg": float(avg),
                        "buy": (c.get("opinion") or {}).get("buy", 0),
                    }
                await asyncio.sleep(0.5)
            except Exception:
                pass

        # ── 확신등급 변동 수집 ──
        grade_change_map: dict = {}  # ticker → grade_arrow string
        try:
            dec_log = load_decision_log()
            dec_entries = sorted(dec_log.values(), key=lambda x: x.get("date", ""), reverse=True)
            if len(dec_entries) >= 2:
                cur_entry  = dec_entries[0]
                prev_entry = dec_entries[1]
                for row in port_rows:
                    t = row["ticker"]
                    n = row["info"].get("name", t)
                    cur_g  = _extract_grade(cur_entry,  t, n)
                    prev_g = _extract_grade(prev_entry, t, n)
                    grade_change_map[t] = _grade_arrow(prev_g, cur_g)
        except Exception:
            pass

        # ── [보유] 종목별 ──
        if port_rows:
            msg += "\n[보유]\n"
            for row in port_rows:
                name = row["info"].get("name", row["ticker"])
                if row.get("error"):
                    msg += f"{name} ({row['ticker']}) — 조회실패\n"
                    continue
                price = row["price"]
                chg = row["chg"]
                qty   = row["info"].get("qty", 0)
                eval_amt = price * qty
                w_pct = round(eval_amt / total_eval * 100) if total_eval > 0 else 0
                limit_warn = f" ⚠️>{_STOCK_LIMIT}%한도" if w_pct > _STOCK_LIMIT else ""
                grade_str = grade_change_map.get(row["ticker"], "")
                frgn_qty = row["frgn_qty"]
                fire = " 🔥" if chg >= 5 else (" ⚠️" if chg <= -3 else "")
                frgn_abs = abs(frgn_qty)
                frgn_k = frgn_abs // 1000
                frgn_disp = (f"+{frgn_k}K" if frgn_qty >= 0 else f"-{frgn_k}K") if frgn_k > 0 else f"{frgn_qty:+}"
                frgn_ok = " ✅" if frgn_qty > 0 else ""
                tgt_str = f" | 목표{row['tgt']:,.0f} {row['tgt_pct']:+.1f}%" if row["tgt_pct"] is not None else ""
                # 컨센서스 비교
                cons_str = ""
                try:
                    cdata = consensus_map.get(row["ticker"])
                    if cdata:
                        cavg = cdata["avg"]
                        buy_cnt = cdata["buy"]
                        our_tgt = row["tgt"]
                        if our_tgt > 0 and cavg > 0:
                            ratio = our_tgt / cavg
                            diff_pct = (our_tgt - cavg) / cavg * 100
                            if ratio < 0.8:
                                cons_str = f" ⚠️목표{our_tgt:,.0f} vs 컨센{cavg:,.0f} (↑{abs(diff_pct):.0f}%)"
                            elif ratio > 1.2:
                                cons_str = f" ⚠️목표{our_tgt:,.0f} vs 컨센{cavg:,.0f} (↓{abs(diff_pct):.0f}%)"
                            else:
                                cons_str = f" 📊컨센{cavg:,.0f}(매수{buy_cnt})"
                        elif cavg > 0:
                            cons_str = f" 📊컨센{cavg:,.0f}(매수{buy_cnt})"
                except Exception:
                    pass
                msg += f"{name} {price:,} ({chg:+.2f}%){fire} 비중{w_pct}%{limit_warn} | 외인{frgn_disp}{frgn_ok}{tgt_str}{cons_str}{grade_str}\n"

            # ── 섹터 비중 경고 ──
            try:
                sector_lines = []
                for sector_name, sector_tickers in _KR_SECTORS.items():
                    sector_eval = sum(
                        row["price"] * row["info"].get("qty", 0)
                        for row in port_rows
                        if row["ticker"] in sector_tickers and not row.get("error")
                    )
                    if sector_eval <= 0 or total_eval <= 0:
                        continue
                    s_pct = round(sector_eval / total_eval * 100)
                    if s_pct > _SECTOR_LIMIT:
                        sector_lines.append(f"⚠️ {sector_name} 섹터 {s_pct}% (한도{_SECTOR_LIMIT}% 초과)")
                    elif s_pct >= 30:
                        sector_lines.append(f"📊 {sector_name} 섹터 {s_pct}% (한도{_SECTOR_LIMIT}% OK)")
                if sector_lines:
                    msg += "\n".join(sector_lines) + "\n"
            except Exception:
                pass

        # ── [뉴스 감성] ──
        try:
            all_tickers = list(kr_stocks.keys())
            wl = load_watchlist()
            for t in wl:
                if t not in all_tickers:
                    all_tickers.append(t)
            neg_alerts = []
            for t in all_tickers:
                try:
                    news = await kis_news_title(t, token, n=5)
                    sa = analyze_news_sentiment(news)
                    neg_count = len(sa.get("negative", []))
                    if neg_count >= 2:
                        name = kr_stocks.get(t, {}).get("name") or wl.get(t, t)
                        top_neg = sa["negative"][0]["title"] if sa["negative"] else ""
                        neg_alerts.append(f"🔴 {name}: 부정 {neg_count}건 — {top_neg[:20]}")
                    await asyncio.sleep(0.2)
                except Exception:
                    pass
            if neg_alerts:
                msg += "\n[뉴스 감성]\n" + "\n".join(neg_alerts[:5]) + "\n"
        except Exception:
            pass

        # ── [감시 접근] gap_pct <= 5% ──
        try:
            wa = load_watchalert()
            near = []
            for wa_ticker, wa_info in wa.items():
                buy_p = float(wa_info.get("buy_price", 0) or 0)
                if buy_p <= 0:
                    continue
                try:
                    if _is_us_ticker(wa_ticker):
                        wd = await kis_us_stock_price(wa_ticker, token)
                        cur_p = float(wd.get("last", 0) or 0)
                    else:
                        wd = await kis_stock_price(wa_ticker, token)
                        cur_p = int(wd.get("stck_prpr", 0) or 0)
                    await asyncio.sleep(0.2)
                    if cur_p > 0 and (cur_p - buy_p) / buy_p * 100 <= 5:
                        near.append((wa_info.get("name", wa_ticker), cur_p, buy_p,
                                     (cur_p - buy_p) / buy_p * 100, _is_us_ticker(wa_ticker)))
                except Exception:
                    pass
            if near:
                msg += "\n[감시 접근]\n"
                for name, cur, buy, gap, is_us in near:
                    sign = "🟢" if cur <= buy else "·"
                    if is_us:
                        msg += f"{sign} {name}: ${cur:,.2f} ← 감시 ${buy:,.2f} ({gap:+.1f}%)\n"
                    else:
                        msg += f"{sign} {name}: {cur:,}원 ← 감시 {buy:,.0f}원 ({gap:+.1f}%)\n"
        except Exception:
            pass

        # ── [포트 건강] 규칙 위반 체크 ──
        try:
            pf = load_json(PORTFOLIO_FILE, {})
            kr_pf = {k: v for k, v in pf.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)}
            us_pf = pf.get("us_stocks", {})
            cash_krw = float(pf.get("cash_krw", 0) or 0)
            cash_usd = float(pf.get("cash_usd", 0) or 0)

            # 총 자산 계산 (간이)
            total_kr = sum(float(v.get("avg_price", 0)) * float(v.get("qty", 0)) for v in kr_pf.values())
            total_us = sum(float(v.get("avg_price", 0)) * float(v.get("qty", 0)) for v in us_pf.values())
            total_asset = total_kr + (total_us * krw) + cash_krw + (cash_usd * krw)

            health_warnings = []
            if total_asset > 0:
                # 단일종목 비중 35% 초과
                for t, v in {**kr_pf, **us_pf}.items():
                    val = float(v.get("avg_price", 0)) * float(v.get("qty", 0))
                    if t in us_pf:
                        val *= krw
                    pct = val / total_asset * 100
                    if pct > 35:
                        health_warnings.append(f"⚠️ {v.get('name', t)} {pct:.0f}% → 한도 35% 초과")

                # 현금 비중
                cash_total = cash_krw + cash_usd * krw
                cash_pct = cash_total / total_asset * 100
                if cash_pct < 10:
                    health_warnings.append(f"⚠️ 현금 {cash_pct:.1f}% → 최소 10% 미달")

                # 레짐 체크
                regime_en, regime_e = _read_regime()
                if regime_en == "crisis" and cash_pct < 25:
                    health_warnings.append(f"⚠️ {regime_e} 레짐 현금 {cash_pct:.1f}% → 25% 권장")

            if health_warnings:
                msg += "\n[포트 건강]\n" + "\n".join(health_warnings) + "\n"
            else:
                msg += "\n✅ 포트 건강: 이상 없음\n"
        except Exception as e:
            print(f"포트 건강 체크 오류: {e}")

        # ── [이벤트] 7일 내 일정 (events.json) ──
        try:
            events = load_json(f"{_DATA_DIR}/events.json", {})
            today = now.date()
            upcoming = []
            for key, date_str in events.items():
                if not isinstance(date_str, str) or len(date_str) != 10:
                    continue
                try:
                    ev_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue
                diff = (ev_date - today).days
                if 0 <= diff <= 7:
                    label = key.replace("_", " ")
                    if diff == 0:
                        upcoming.append(f"• {label} (오늘)")
                    elif diff == 1:
                        upcoming.append(f"• {label} (내일)")
                    else:
                        upcoming.append(f"• {label} (D-{diff}, {ev_date.strftime('%m/%d')})")
            if upcoming:
                msg += "\n[이벤트] 7일 내\n" + "\n".join(upcoming) + "\n"
        except Exception as e:
            print(f"이벤트 섹션 오류: {e}")

        msg += "\n→ Claude에서 점검하세요"
        await _safe_send(context, msg)

        # 발송 기록
        _kr_sent["kr_summary"] = _kr_key
        save_json(MACRO_SENT_FILE, _kr_sent)

        # ── 수급 히스토리 축적 (백테스트용) ──
        try:
            await save_supply_snapshot(token)
        except Exception as e:
            print(f"[kr_summary] 수급 히스토리 저장 실패 (무시): {e}")

    except Exception as e:
        print(f"daily_kr_summary 오류: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔔 자동알림 2: 미국 장 마감 요약
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
