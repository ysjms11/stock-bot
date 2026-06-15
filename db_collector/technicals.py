"""
db_collector 기술지표 계산 (2026-06 분해 P2b-2).
순수 지표 함수 + SQLite 히스토리 로더 + 지표 적용기.
의존: numpy, sqlite3 (conn은 인자로 전달), stdlib만.
"""

import sqlite3
import numpy as np


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 기술지표 헬퍼 (순수 함수)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _ma(arr, n):
    """Simple MA. Returns None if insufficient data."""
    if len(arr) < n:
        return None
    return round(float(np.mean(arr[:n])), 2)


def _rsi(closes, period=14):
    """RSI calculation. Returns None if insufficient data."""
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i + 1] for i in range(min(len(closes) - 1, period * 3))]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]
    if len(gains) < period:
        return None
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _calc_vp(closes: list, volumes: list, n: int, n_bins: int = 20) -> dict:
    """매물대 계산. Returns {poc, va_high, va_low, position} or all None."""
    null = {"poc": None, "va_high": None, "va_low": None, "position": None}
    actual = min(n, len(closes))
    if actual < 30 or len(volumes) < actual:
        return null
    cs = closes[:actual]
    vs = volumes[:actual]
    p_min, p_max = min(cs), max(cs)
    if p_max <= p_min:
        return null
    bin_size = (p_max - p_min) / n_bins
    bins = [0.0] * n_bins
    for c, v in zip(cs, vs):
        idx = min(int((c - p_min) / bin_size), n_bins - 1)
        bins[idx] += v
    poc_idx = int(np.argmax(bins))
    total_vol = sum(bins)
    if total_vol == 0:
        return null
    target_vol = total_vol * 0.7
    sorted_bins = sorted(range(n_bins), key=lambda i: bins[i], reverse=True)
    va_vol = 0
    va_indices = []
    for bi in sorted_bins:
        va_vol += bins[bi]
        va_indices.append(bi)
        if va_vol >= target_vol:
            break
    va_low_idx = min(va_indices)
    va_high_idx = max(va_indices)
    va_high = round(p_min + (va_high_idx + 1) * bin_size)
    va_low = round(p_min + va_low_idx * bin_size)
    cur = closes[0] if closes else 0
    rng = va_high - va_low
    return {
        "poc": round(p_min + (poc_idx + 0.5) * bin_size),
        "va_high": va_high,
        "va_low": va_low,
        "position": round((cur - va_low) / rng, 4) if rng > 0 else None,
    }


def _volume_ratio(volumes: list, recent: int, prev_offset: int):
    """최근 recent일 평균 / 그 이전 recent일 평균."""
    total = recent + prev_offset
    if len(volumes) < total:
        return None
    r = np.mean(volumes[:recent]) if any(v > 0 for v in volumes[:recent]) else 0
    p = np.mean(volumes[prev_offset:total]) if any(v > 0 for v in volumes[prev_offset:total]) else 0
    return round(r / p, 2) if p > 0 else None


def _spread_at(closes: list, offset: int):
    """offset일 전 시점의 MA spread (MA5-MA60)/MA60."""
    if len(closes) < offset + 60:
        return None
    ma5 = _ma(closes[offset:], 5)
    ma60 = _ma(closes[offset:], 60)
    if ma5 and ma60 and ma60 > 0:
        return (ma5 - ma60) / ma60 * 100
    return None


def _rsi_at(closes: list, offset: int, period: int = 14):
    """offset일 전 시점의 RSI."""
    if len(closes) < offset + period + 1:
        return None
    return _rsi(closes[offset:], period)


def _macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD(fast, slow, signal). Returns (macd, signal_line, histogram) or (None, None, None)."""
    if len(closes) < slow + signal:
        return None, None, None
    # EMA 계산 (closes는 최신→과거 순 → 역순으로)
    rev = list(reversed(closes[:slow + signal + 10]))

    def _ema(arr, period):
        k = 2.0 / (period + 1)
        ema = arr[0]
        for v in arr[1:]:
            ema = v * k + ema * (1 - k)
        return ema

    # 전체 시계열에 대한 EMA 계산
    def _ema_series(arr, period):
        k = 2.0 / (period + 1)
        result = [arr[0]]
        for v in arr[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    rev_all = list(reversed(closes[:slow + signal + 20]))
    if len(rev_all) < slow:
        return None, None, None
    ema_fast_s = _ema_series(rev_all, fast)
    ema_slow_s = _ema_series(rev_all, slow)
    if len(ema_fast_s) < slow or len(ema_slow_s) < slow:
        return None, None, None
    macd_line = [f - s for f, s in zip(ema_fast_s[slow - 1:], ema_slow_s[slow - 1:])]
    if len(macd_line) < signal:
        return None, None, None
    signal_line = _ema_series(macd_line, signal)[-1]
    macd_val = macd_line[-1]
    hist = round(macd_val - signal_line, 4)
    return round(macd_val, 4), round(signal_line, 4), hist


def _atr(closes: list, highs: list, lows: list, period: int = 14):
    """ATR(period). closes/highs/lows는 최신→과거 순. Returns None if insufficient."""
    # db_collector에서는 high/low가 daily_snapshot에 있으나
    # history에는 close만 있으므로 close 기반 근사 ATR 계산
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(period):
        c_prev = closes[i + 1] if i + 1 < len(closes) else closes[i]
        h = highs[i] if i < len(highs) else closes[i]
        l = lows[i] if i < len(lows) else closes[i]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    return round(float(np.mean(trs)), 2) if trs else None


def _volatility_20d(closes: list):
    """20일 종가 표준편차 / 평균 (변동성). Returns None if insufficient."""
    if len(closes) < 20:
        return None
    c = closes[:20]
    mean = float(np.mean(c))
    if mean == 0:
        return None
    return round(float(np.std(c, ddof=0)) / mean * 100, 4)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# SQLite 히스토리 로더
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _load_history_from_db(conn: sqlite3.Connection, target_date: str, n_days: int = 260):
    """SQLite daily_snapshot에서 과거 N일 시계열 로드.
    Returns: ({ticker: {close: [], volume: [], eps: [], foreign_net_amt: [], short_volume: [],
                         high: [], low: []}}, [날짜리스트(최신→과거)])
    """
    date_rows = conn.execute("""
        SELECT DISTINCT trade_date FROM daily_snapshot
        WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?
    """, (target_date, n_days + 1)).fetchall()
    dates = [r[0] for r in date_rows]

    if len(dates) < 2:
        return {}, dates

    oldest = dates[-1]
    rows = conn.execute("""
        SELECT symbol, trade_date, close, high, low, volume, eps,
               foreign_net_amt, short_volume, foreign_own_pct, loan_balance_rate
        FROM daily_snapshot
        WHERE trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date ASC
    """, (oldest, target_date)).fetchall()

    # 종목별 그룹핑 (ASC 순서 → 나중에 reverse해서 최신→과거 순으로)
    tmp = {}
    for r in rows:
        sym = r["symbol"]
        if sym not in tmp:
            tmp[sym] = {"close": [], "volume": [], "eps": [],
                        "foreign_net_amt": [], "short_volume": [],
                        "high": [], "low": [], "foreign_own_pct": [],
                        "loan_balance_rate": []}
        h = tmp[sym]
        h["close"].append(r["close"] or 0)
        h["volume"].append(r["volume"] or 0)
        h["eps"].append(r["eps"] or 0)
        h["foreign_net_amt"].append(r["foreign_net_amt"] or 0)
        h["short_volume"].append(r["short_volume"] or 0)
        h["high"].append(r["high"] or 0)
        h["low"].append(r["low"] or 0)
        h["foreign_own_pct"].append(r["foreign_own_pct"] or 0)
        h["loan_balance_rate"].append(r["loan_balance_rate"] or 0)

    # 최신→과거 순으로 역순
    history = {}
    for sym, h in tmp.items():
        history[sym] = {k: list(reversed(v)) for k, v in h.items()}

    return history, dates  # dates는 이미 DESC(최신→과거)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 기술지표 적용기 (stocks dict in-place 업데이트)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _compute_technicals_sqlite(date: str, stocks: dict, history: dict, dates: list):
    """기술지표 + 추세 점수 + 매물대를 stocks dict에 in-place 추가.
    krx_crawler._compute_technicals 로직 기반, SQLite 입출력.
    추가 지표: MACD(12,26,9), ATR(14), volatility_20d, bb_width.
    """
    n_days = len(dates)
    print(f"[Tech/SQLite] 과거 {n_days}일 로드, 지표 계산 시작")

    # 연초 날짜 (YTD 계산용)
    year = date[:4]
    ytd_idx = None
    for i, d in enumerate(dates):
        if d[:4] < year:
            ytd_idx = i
            break

    # 섹터 평균 등락률 계산
    sector_chg = {}
    for s in stocks.values():
        sec = s.get("sector_name", "")
        if sec:
            sector_chg.setdefault(sec, []).append(s.get("chg_pct") or s.get("change_pct", 0) or 0)
    sector_avg = {sec: round(float(np.mean(vals)), 4) for sec, vals in sector_chg.items() if vals}

    for ticker, s in stocks.items():
        h = history.get(ticker, {})
        closes = h.get("close", [])
        volumes = h.get("volume", [])
        highs = h.get("high", [])
        lows = h.get("low", [])
        eps_hist = h.get("eps", [])
        cur = s.get("close", 0)

        # ── 이평선 ──
        s["ma5"] = _ma(closes, 5)
        s["ma10"] = _ma(closes, 10)
        s["ma20"] = _ma(closes, 20)
        s["ma60"] = _ma(closes, 60)
        s["ma120"] = _ma(closes, 120)
        s["ma200"] = _ma(closes, 200)

        # ── RSI(14) ──
        s["rsi14"] = _rsi(closes, 14)

        # ── 볼린저밴드 (MA20 ± 2σ) + bb_width ──
        if len(closes) >= 20:
            m20 = float(np.mean(closes[:20]))
            std20 = float(np.std(closes[:20], ddof=0))
            s["bb_upper"] = round(m20 + 2 * std20, 0)
            s["bb_lower"] = round(m20 - 2 * std20, 0)
            s["bb_width"] = round((s["bb_upper"] - s["bb_lower"]) / m20 * 100, 4) if m20 > 0 else None
        else:
            s["bb_upper"] = s["bb_lower"] = s["bb_width"] = None

        # ── MA spread ──
        ma5v = s["ma5"]
        ma60v = s["ma60"]
        s["ma_spread"] = round((ma5v - ma60v) / ma60v * 100, 2) if ma5v and ma60v and ma60v > 0 else None

        # ── 52주 고/저/position ──
        if len(closes) >= 60:
            w52_slice = closes[:min(250, len(closes))]
            w52h = max(w52_slice)
            w52l = min(w52_slice)
            s["w52_position"] = round((cur - w52l) / (w52h - w52l), 4) if w52h > w52l else None
        else:
            s["w52_position"] = None

        # ── YTD 수익률 ──
        if ytd_idx is not None and ytd_idx < len(closes) and closes[ytd_idx] > 0:
            s["ytd_return"] = round((cur - closes[ytd_idx]) / closes[ytd_idx] * 100, 2)
        else:
            s["ytd_return"] = None

        # ── 섹터 상대강도 ──
        sec = s.get("sector_name", "")
        chg_pct = s.get("chg_pct") or s.get("change_pct", 0) or 0
        s["sector_rel_strength"] = round(chg_pct - sector_avg[sec], 2) if sec and sec in sector_avg else None

        # ── 추세: volume_ratio 5d/10d/20d ──
        s["volume_ratio_5d"] = _volume_ratio(volumes, 5, 5)
        s["volume_ratio_10d"] = _volume_ratio(volumes, 10, 10)
        s["volume_ratio_20d"] = _volume_ratio(volumes, 20, 20)

        # ── 추세: ma_spread_change 10d/30d ──
        cur_spread = s["ma_spread"]
        for nd in (10, 30):
            key = f"ma_spread_change_{nd}d"
            prev = _spread_at(closes, nd)
            s[key] = round(cur_spread - prev, 2) if cur_spread is not None and prev is not None else None

        # ── 추세: rsi_change 5d/20d ──
        rsi_now = s["rsi14"]
        for nd in (5, 20):
            key = f"rsi_change_{nd}d"
            prev_rsi = _rsi_at(closes, nd)
            s[key] = round(rsi_now - prev_rsi, 2) if rsi_now is not None and prev_rsi is not None else None

        # ── 추세: eps_change_90d + earnings_gap ──
        ep_idx = min(89, len(eps_hist) - 1) if len(eps_hist) >= 2 else -1
        if ep_idx >= 1 and eps_hist[0] != 0 and eps_hist[ep_idx] != 0:
            s["eps_change_90d"] = round((eps_hist[0] - eps_hist[ep_idx]) / abs(eps_hist[ep_idx]) * 100, 2)
            ytd = s.get("ytd_return")
            s["earnings_gap"] = round(s["eps_change_90d"] - ytd, 2) if ytd is not None else None
        else:
            s["eps_change_90d"] = s["earnings_gap"] = None

        # ── 수급 추세: foreign_trend Nd ──
        frgn_hist = h.get("foreign_net_amt", [])
        for nd in (5, 20, 60):
            key = f"foreign_trend_{nd}d"
            if len(frgn_hist) >= nd:
                buy_days = sum(1 for x in frgn_hist[:nd] if x > 0)
                s[key] = round(buy_days / nd, 4)
            else:
                s[key] = None

        # ── 수급 비율: foreign_ratio / inst_ratio / fi_ratio ──
        fown = s.get("foreign_own_pct") or h.get("foreign_own_pct", [None])[0]
        s["foreign_ratio"] = fown
        s["inst_ratio"] = s.get("inst_ratio")
        fi_r = None
        fr_v = s.get("foreign_net_amt") or 0
        ir_v = s.get("inst_net_amt") or 0
        vol = s.get("trade_value") or 0
        if vol and vol > 0:
            fi_r = round((fr_v + ir_v) / vol * 100, 4)
        s["fi_ratio"] = fi_r

        # ── 수급 추세: short_change Nd (SQLite: short_volume 기반) ──
        short_hist = h.get("short_volume", [])
        for nd in (5, 20):
            key = f"short_change_{nd}d"
            if len(short_hist) >= nd + 1 and short_hist[nd] > 0:
                s[key] = round((short_hist[0] - short_hist[nd]) / short_hist[nd] * 100, 2)
            else:
                s[key] = None

        # ── 매물대 60d / 250d ──
        for period, suffix in [(60, "_60d"), (250, "_250d")]:
            vp = _calc_vp(closes, volumes, period)
            s[f"vp_poc{suffix}"] = vp["poc"]
            s[f"vp_va_high{suffix}"] = vp["va_high"]
            s[f"vp_va_low{suffix}"] = vp["va_low"]
            s[f"vp_position{suffix}"] = vp["position"]

        # ── MACD(12, 26, 9) ──
        macd_val, macd_sig, macd_hist = _macd(closes)
        s["macd"] = macd_val
        s["macd_signal"] = macd_sig
        s["macd_hist"] = macd_hist

        # ── ATR(14) ──
        s["atr14"] = _atr(closes, highs, lows, 14)

        # ── volatility_20d ──
        s["volatility_20d"] = _volatility_20d(closes)

    # ── 섹터 내 순위 계산 ──
    sector_stocks = {}
    for ticker, s in stocks.items():
        sec = s.get("sector_name", "")
        if sec:
            sector_stocks.setdefault(sec, []).append((ticker, s.get("chg_pct") or s.get("change_pct", 0) or 0))
    for sec, members in sector_stocks.items():
        members.sort(key=lambda x: x[1], reverse=True)
        for rank, (ticker, _) in enumerate(members, 1):
            stocks[ticker]["sector_rank"] = rank
    for s in stocks.values():
        s.setdefault("sector_rank", None)

    print(f"[Tech/SQLite] 지표 계산 완료: {len(stocks)}종목")
