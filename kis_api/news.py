"""뉴스 + 감성분석 + 매크로 신호 계산."""
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def fetch_news(query="주식 시장 한국", max_items=8):
    """Google News RSS로 뉴스 헤드라인 가져오기"""
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"

    try:
        session = _get_session()
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status == 200:
                text = await resp.text()
                # 간단한 XML 파싱
                root = ET.fromstring(text)
                items = root.findall(".//item")
                results = []
                for item in items[:max_items]:
                    title = item.find("title").text if item.find("title") is not None else ""
                    pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                    source = item.find("source").text if item.find("source") is not None else ""
                    results.append({"title": title, "date": pub_date, "source": source})
                return results
    except Exception as e:
        print(f"뉴스 조회 오류: {e}")
    return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 미국 뉴스 / 감성분석 / 실적캘린더 / 섹터 ETF
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_us_news(ticker: str, n: int = 10) -> list:
    """yfinance로 미국 종목 뉴스 헤드라인 조회.
    Returns: [{"date": "YYYYMMDD", "time": "", "title": str, "source": str}, ...]
    yfinance 버전별 응답 구조 차이를 모두 처리 (구버전: flat dict, 신버전: content 중첩).
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        news = t.news or []
        result = []
        from datetime import datetime as _dt
        for item in news[:n]:
            # ── 신버전 yfinance (>=0.2.36): content 중첩 구조 ──
            content = item.get("content", {}) if isinstance(item.get("content"), dict) else {}
            title = content.get("title") or item.get("title", "")
            provider = content.get("provider", {})
            source = provider.get("displayName", "") if isinstance(provider, dict) else ""
            if not source:
                source = item.get("publisher", "")
            # 날짜 파싱: 신버전 pubDate (ISO string) → 구버전 providerPublishTime (unix ts)
            date_str, time_str = "", ""
            pub_date = content.get("pubDate", "")
            pub_ts = item.get("providerPublishTime", 0)
            if pub_date and isinstance(pub_date, str):
                try:
                    dt = _dt.fromisoformat(pub_date.replace("Z", "+00:00"))
                    date_str = dt.strftime("%Y%m%d")
                    time_str = dt.strftime("%H%M%S")
                except Exception:
                    pass
            elif pub_ts:
                try:
                    dt = _dt.fromtimestamp(pub_ts)
                    date_str = dt.strftime("%Y%m%d")
                    time_str = dt.strftime("%H%M%S")
                except Exception:
                    pass
            result.append({"date": date_str, "time": time_str, "title": title, "source": source})
        return result
    except Exception as e:
        print(f"[fetch_us_news] 오류 ({ticker}): {e}")
        return []


def analyze_us_news_sentiment(news_items: list) -> dict:
    """미국 뉴스 헤드라인 영문 감성 분석."""
    positive, negative, neutral = [], [], []
    for item in news_items:
        title = item.get("title", "").lower()
        pos_matches = [kw for kw in _US_POSITIVE_KEYWORDS if kw in title]
        neg_matches = [kw for kw in _US_NEGATIVE_KEYWORDS if kw in title]
        entry = {**item, "matched_keywords": pos_matches + neg_matches}
        if len(pos_matches) > len(neg_matches):
            entry["sentiment"] = "positive"
            positive.append(entry)
        elif len(neg_matches) > len(pos_matches):
            entry["sentiment"] = "negative"
            negative.append(entry)
        else:
            entry["sentiment"] = "neutral"
            neutral.append(entry)
    return {
        "positive": positive, "negative": negative, "neutral": neutral,
        "summary": f"🟢긍정 {len(positive)} / 🔴부정 {len(negative)} / ⚪중립 {len(neutral)}",
    }


def fetch_us_earnings_calendar(tickers: list) -> list:
    """yfinance로 미국 종목 실적 발표일 조회.
    Returns: [{"ticker": str, "name": str, "earnings_date": "YYYY-MM-DD", "days_until": int}, ...]
    t.calendar가 dict 또는 DataFrame 어느 형태든 처리.
    """
    try:
        import yfinance as yf
    except ImportError:
        return []
    from datetime import datetime as _dt, timedelta
    now = _dt.now()
    result = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None:
                continue
            # DataFrame → dict 변환 (일부 yfinance 버전에서 DataFrame 반환)
            if hasattr(cal, 'to_dict'):
                try:
                    # DataFrame 형태: columns = [0], index = ["Earnings Date", ...]
                    if hasattr(cal, 'iloc') and len(cal.columns) > 0:
                        cal = {idx: cal.iloc[i, 0] for i, idx in enumerate(cal.index)}
                    else:
                        cal = cal.to_dict()
                except Exception:
                    continue
            if hasattr(cal, 'empty') and cal.empty:
                continue
            if not isinstance(cal, dict):
                continue
            ed = cal.get("Earnings Date")
            if isinstance(ed, list) and ed:
                ed = ed[0]
            if not ed:
                continue
            if hasattr(ed, 'strftime'):
                date_str = ed.strftime("%Y-%m-%d")
            else:
                date_str = str(ed)[:10]
            try:
                ed_dt = _dt.strptime(date_str, "%Y-%m-%d")
                days_until = (ed_dt - now).days
                if -1 <= days_until <= 30:
                    # t.info 호출은 네트워크 요청이므로 방어적 처리
                    try:
                        name = t.info.get("shortName", ticker)
                    except Exception:
                        name = ticker
                    result.append({
                        "ticker": ticker,
                        "name": name,
                        "earnings_date": date_str,
                        "days_until": days_until,
                    })
            except Exception:
                pass
        except Exception as e:
            print(f"[us_earnings] {ticker} 오류: {e}")
            continue
    result.sort(key=lambda x: x.get("days_until", 999))
    return result


US_SECTOR_ETFS = [
    ("SPY", "S&P500"), ("QQQ", "나스닥100"),
    ("XLK", "기술"), ("XLF", "금융"), ("XLE", "에너지"),
    ("XLV", "헬스케어"), ("XLI", "산업재"), ("XLP", "필수소비"),
    ("XLY", "임의소비"), ("XLRE", "부동산"), ("XLU", "유틸리티"),
]


def fetch_us_sector_etf() -> list:
    """yfinance로 미국 섹터 ETF 등락률 조회.
    Returns: [{"ticker", "name", "price", "chg_1d", "chg_5d"}, ...]
    """
    try:
        import yfinance as yf
    except ImportError:
        return []
    result = []
    for sym, name in US_SECTOR_ETFS:
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="7d")
            if hist is None or hist.empty or len(hist) < 2:
                continue
            cur = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            chg_1d = round((cur - prev) / prev * 100, 2)
            if len(hist) >= 6:
                d5_ago = float(hist["Close"].iloc[-6])
                chg_5d = round((cur - d5_ago) / d5_ago * 100, 2)
            else:
                chg_5d = None
            result.append({
                "ticker": sym, "name": name,
                "price": round(cur, 2),
                "chg_1d": chg_1d,
                "chg_5d": chg_5d,
            })
        except Exception as e:
            print(f"[us_sector_etf] {sym} 오류: {e}")
            continue
    return result


def fetch_us_short_interest(ticker: str) -> dict:
    """yfinance에서 미국 종목 공매도 데이터 조회.
    Returns: {ticker, short_ratio, short_pct_float, days_to_cover, shares_short, ...}
    데이터 없으면 빈 dict. 동기 함수.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        shares_short = info.get("sharesShort")
        if shares_short is None:
            return {"ticker": ticker, "message": "공매도 데이터 없음"}
        return {
            "ticker": ticker,
            "name": info.get("shortName", ticker),
            "short_ratio": info.get("shortRatio"),
            "short_pct_float": info.get("shortPercentOfFloat"),
            "days_to_cover": info.get("shortRatio"),
            "shares_short": shares_short,
            "shares_short_prev": info.get("sharesShortPriorMonth"),
            "short_pct_shares_out": info.get("sharesPercentSharesOut"),
            "float_shares": info.get("floatShares"),
        }
    except Exception as e:
        print(f"[us_short_interest] {ticker} 오류: {e}")
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 시장 레짐 판정 (복합점수 기반)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _yf_history(symbol: str, period: str = "2y") -> list:
    """yfinance 종가 히스토리 → [float, ...] (오래된 순)."""
    try:
        import yfinance as yf
        df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return []
        col = df["Close"]
        # MultiIndex 대응 (yfinance >= 0.2.36 단일 티커도 MultiIndex 가능)
        if hasattr(col, "columns"):
            col = col.iloc[:, 0]
        return [float(v) for v in col.dropna().tolist()]
    except Exception as e:
        print(f"[_yf_history] {symbol}: {e}")
        return []


def _krx_kospi_history(days: int = 600) -> list:
    """pykrx KOSPI 종가 히스토리. 실패 시 yfinance ^KS11 fallback."""
    try:
        from pykrx import stock as krx
        end = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=days)).strftime("%Y%m%d")
        df = krx.get_index_ohlcv(start, end, "1001")
        if df is not None and not df.empty:
            return [float(c) for c in df["종가"].dropna().tolist()]
    except Exception as e:
        print(f"[_krx_kospi_history] pykrx 실패, yfinance fallback: {e}")
    return _yf_history("^KS11", "2y")


def _krx_foreign_net(days: int = 280) -> list:
    """pykrx 외국인 KOSPI 순매수 금액 히스토리. 실패 시 빈 리스트."""
    try:
        from pykrx import stock as krx
        end = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=days)).strftime("%Y%m%d")
        df = krx.get_market_net_purchases_of_equities(start, end, "KOSPI", "외국인")
        if df is not None and not df.empty:
            col = "순매수거래대금" if "순매수거래대금" in df.columns else df.columns[-1]
            return [float(v) for v in df[col].dropna().tolist()]
    except Exception as e:
        print(f"[_krx_foreign_net] pykrx 실패: {e}")
    return []


def _calc_zscore(values: list, lookback: int = 252, min_data: int = 60):
    """롤링 z-score. Returns {"value","z","mean","std"} or None."""
    if len(values) < min_data:
        return None
    import numpy as np
    window = values[-lookback:] if len(values) >= lookback else values
    current = window[-1]
    arr = np.array(window, dtype=float)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    if std < 1e-10:
        return {"value": current, "z": 0.0, "mean": mean, "std": std}
    return {"value": current, "z": float((current - mean) / std), "mean": mean, "std": std}


def _rolling_ma_pct(closes: list, ma_len: int) -> list:
    """각 시점에서 (종가-MA)/MA*100 시리즈 생성."""
    out = []
    for i in range(ma_len, len(closes)):
        ma = sum(closes[i - ma_len + 1:i + 1]) / ma_len
        out.append((closes[i] - ma) / ma * 100 if ma else 0)
    return out


def _rolling_momentum(closes: list, lag: int) -> list:
    """(현재/lag일전 - 1)*100 시리즈."""
    return [(closes[i] / closes[i - lag] - 1) * 100
            for i in range(lag, len(closes))]


def _realized_vol(closes: list, window: int = 20):
    """최근 window일 실현변동성 (연율화 %). None if 데이터 부족."""
    if len(closes) < window + 1:
        return None
    import numpy as np
    recent = closes[-(window + 1):]
    rets = np.diff(np.log(np.array(recent, dtype=float)))
    return float(np.std(rets, ddof=1) * (252 ** 0.5) * 100)


def _rolling_realized_vol(closes: list, window: int = 20) -> list:
    """실현변동성 시계열."""
    import numpy as np
    out = []
    for i in range(window + 1, len(closes)):
        seg = closes[i - window:i + 1]
        rets = np.diff(np.log(np.array(seg, dtype=float)))
        out.append(float(np.std(rets, ddof=1) * (252 ** 0.5) * 100))
    return out


def _sig_entry(value, z, label="", invert=False):
    """신호 dict 생성 헬퍼."""
    zz = round(-z if invert else z, 2)
    return {"value": value, "z": zz, "raw_z": round(z, 2), "label": label}


async def compute_us_signals() -> dict:
    """미국 6개 신호 z-score → {"signals":{}, "score":float, "failed":[]}"""
    import numpy as np
    from scipy.stats import norm

    signals, failed = {}, []

    # 1. VIX (역수)
    vix_data = _yf_history("^VIX", "2y")
    zs = _calc_zscore(vix_data)
    if zs:
        signals["VIX"] = _sig_entry(round(zs["value"], 1), zs["z"], "역수", invert=True)
    else:
        failed.append("VIX")
    await asyncio.sleep(0.3)

    # 2. HY 스프레드 프록시 (HYG/LQD)
    hyg = _yf_history("HYG", "2y")
    await asyncio.sleep(0.3)
    lqd = _yf_history("LQD", "2y")
    if hyg and lqd:
        ml = min(len(hyg), len(lqd))
        ratio = [h / l if l > 0 else 0 for h, l in zip(hyg[-ml:], lqd[-ml:])]
        zs = _calc_zscore(ratio)
        if zs:
            signals["HY스프레드"] = _sig_entry(round(zs["value"], 4), zs["z"], "HYG/LQD")
        else:
            failed.append("HY스프레드")
    else:
        failed.append("HY스프레드")
    await asyncio.sleep(0.3)

    # 3. S&P vs 200MA
    sp = _yf_history("^GSPC", "2y")
    if sp and len(sp) >= 200:
        pct_series = _rolling_ma_pct(sp, 200)
        zs = _calc_zscore(pct_series)
        if zs:
            signals["S&P/200MA"] = _sig_entry(round(zs["value"], 1), zs["z"], "%")
        else:
            failed.append("S&P/200MA")
    else:
        failed.append("S&P/200MA")

    # 4. S&P 50일 모멘텀
    if sp and len(sp) > 50:
        mom = _rolling_momentum(sp, 50)
        zs = _calc_zscore(mom)
        if zs:
            signals["50d모멘텀"] = _sig_entry(round(zs["value"], 1), zs["z"], "%")
        else:
            failed.append("50d모멘텀")
    else:
        failed.append("50d모멘텀")
    await asyncio.sleep(0.3)

    # 5. VIX 텀스트럭처 (^VIX3M / ^VIX)
    vix3m = _yf_history("^VIX3M", "2y")
    if vix3m and vix_data:
        ml = min(len(vix3m), len(vix_data))
        term = [v3 / v if v > 0 else 1.0 for v3, v in zip(vix3m[-ml:], vix_data[-ml:])]
        zs = _calc_zscore(term)
        if zs:
            signals["VIX텀"] = _sig_entry(round(zs["value"], 3), zs["z"], "비율")
        else:
            failed.append("VIX텀")
    else:
        failed.append("VIX텀")
    await asyncio.sleep(0.3)

    # 6. 금리차 (10Y-3M 스프레드, ^TNX - ^IRX)
    #    Bauer & Mertens(2018, SF Fed): 10Y-3M이 10Y-2Y보다 경기침체 예측력 우수
    tnx = _yf_history("^TNX", "2y")
    irx = _yf_history("^IRX", "2y")
    if tnx and irx:
        ml = min(len(tnx), len(irx))
        spread = [t - i for t, i in zip(tnx[-ml:], irx[-ml:])]
        zs = _calc_zscore(spread)
        if zs:
            signals["10Y-3M금리차"] = _sig_entry(round(zs["value"], 2), zs["z"], "%p")
        else:
            failed.append("10Y-3M금리차")
    else:
        failed.append("10Y-3M금리차")

    # 점수
    z_vals = [s["z"] for s in signals.values()]
    if z_vals:
        avg_z = float(np.mean(z_vals))
        score = float(norm.cdf(avg_z) * 100)
    else:
        avg_z, score = 0.0, 50.0

    return {"signals": signals, "score": round(score, 1),
            "avg_z": round(avg_z, 2), "failed": failed,
            "n_signals": len(signals)}


async def compute_kr_signals() -> dict:
    """한국 5개 신호 z-score → {"signals":{}, "score":float, "failed":[]}"""
    import numpy as np
    from scipy.stats import norm

    signals, failed = {}, []

    # 1. KOSPI vs 200MA
    kospi = _krx_kospi_history(days=600)
    if kospi and len(kospi) >= 200:
        pct_series = _rolling_ma_pct(kospi, 200)
        zs = _calc_zscore(pct_series)
        if zs:
            signals["KOSPI/200MA"] = _sig_entry(round(zs["value"], 1), zs["z"], "%")
        else:
            failed.append("KOSPI/200MA")
    else:
        failed.append("KOSPI/200MA")

    # 2. KOSPI 50일 모멘텀
    if kospi and len(kospi) > 50:
        mom = _rolling_momentum(kospi, 50)
        zs = _calc_zscore(mom)
        if zs:
            signals["50d모멘텀"] = _sig_entry(round(zs["value"], 1), zs["z"], "%")
        else:
            failed.append("50d모멘텀")
    else:
        failed.append("50d모멘텀")

    # 3. 외인 순매수 5일합
    frgn = _krx_foreign_net(days=400)
    if frgn and len(frgn) >= 60:
        rolling5 = [sum(frgn[i - 4:i + 1]) for i in range(4, len(frgn))]
        zs = _calc_zscore(rolling5)
        if zs:
            val_억 = round(zs["value"] / 1e8, 0)
            signals["외인5일"] = _sig_entry(val_억, zs["z"], "억")
        else:
            failed.append("외인5일")
    else:
        failed.append("외인5일")

    # 4. USD/KRW (역수)
    usdkrw = _yf_history("KRW=X", "2y")
    if usdkrw:
        zs = _calc_zscore(usdkrw)
        if zs:
            signals["USD/KRW"] = _sig_entry(round(zs["value"], 0), zs["z"], "역수", invert=True)
        else:
            failed.append("USD/KRW")
    else:
        failed.append("USD/KRW")
    await asyncio.sleep(0.3)

    # 5. KOSPI 20일 실현변동성 (역수)
    if kospi and len(kospi) >= 80:
        vol_series = _rolling_realized_vol(kospi, 20)
        zs = _calc_zscore(vol_series)
        if zs:
            signals["실현변동성"] = _sig_entry(round(zs["value"], 1), zs["z"], "역수,%", invert=True)
        else:
            failed.append("실현변동성")
    else:
        failed.append("실현변동성")

    z_vals = [s["z"] for s in signals.values()]
    if z_vals:
        avg_z = float(np.mean(z_vals))
        score = float(norm.cdf(avg_z) * 100)
    else:
        avg_z, score = 0.0, 50.0

    return {"signals": signals, "score": round(score, 1),
            "avg_z": round(avg_z, 2), "failed": failed,
            "n_signals": len(signals)}


