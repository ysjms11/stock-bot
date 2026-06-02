"""dashboard_home — 새 대시보드 P0/P1.

/home 경로에 서빙. /dash(dashboard.py)는 무수정.
P0: HTML 쉘 + Alpine 탭 네비 + 빈 패널.
P1: JSON API (/api/home, /api/regime, /api/alerts, /api/portfolio) + 홈 화면 실데이터 바인딩.
"""

import re
import time
import asyncio
from datetime import datetime, timezone, timedelta

from aiohttp import web

import json

from kis_api import (
    load_json,
    load_stoploss,
    load_watchalert,
    load_dart_seen,
    load_events,
    get_yahoo_quote,
    CONSENSUS_CACHE_FILE,
    DART_SEEN_FILE,
    EVENTS_FILE,
    _DATA_DIR,
    KST,
)
from mcp_tools import execute_tool

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TTL 캐시 (단순 dict, asyncio 단일스레드 — lock 불필요)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_cache: dict = {}  # {key: (ts_monotonic, data)}


async def _cached(key: str, ttl: float, factory):
    """ttl초 내 캐시 hit이면 저장 데이터 반환, 아니면 factory() await 후 저장.

    W2: factory는 콜러블(람다 또는 함수). miss일 때만 await해 코루틴 누수 방지.
    캐시 hit 시 코루틴이 아예 생성되지 않으므로 RuntimeWarning 없음.
    """
    entry = _cache.get(key)
    if entry is not None:
        ts, data = entry
        if time.monotonic() - ts < ttl:
            return data
    data = await factory()
    _cache[key] = (time.monotonic(), data)
    return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 에러 dict 검사 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _tool_err(r) -> bool:
    """execute_tool이 raise 대신 {"error": ...}를 반환할 때 감지 (W1).

    execute_tool은 내부에서 예외를 잡아 {"error": msg, "tool": name}을 반환.
    이를 호출자 try/except가 못 잡으므로 명시적 검사 필요.
    """
    return isinstance(r, dict) and "error" in r


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통 API 래퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _api(coro) -> web.Response:
    try:
        return web.json_response(await coro)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=200)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# build_home_payload — 홈 집계 (부분 실패 허용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _regime_color(regime_en: str) -> str:
    """레짐 라벨을 Tailwind 색 클래스로 변환."""
    if regime_en == "offensive":
        return "green"
    if regime_en == "crisis":
        return "red"
    return "amber"


def _parse_events_upcoming(events: dict, max_items: int = 5) -> list:
    """events.json에서 오늘 이후 임박 이벤트 추출.

    W4: 이모지 접두사(🚨, ✅ 등)가 붙은 값도 처리.
    re.search로 값 어디든 박힌 ISO 날짜(YYYY-MM-DD)를 추출.
    매칭 없는 항목(---구분자, 2026-07-하순 등 비ISO)은 자연 제외.
    D-day 오름차순 정렬 후 max_items 반환.
    """
    today_date = datetime.now(KST).date()
    today_str = today_date.strftime("%Y-%m-%d")
    items = []
    for name, value in events.items():
        if not isinstance(value, str):
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", value)
        if not m:
            continue
        raw_date = m.group(1)
        if raw_date < today_str:
            continue
        try:
            event_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        dday = (event_date - today_date).days
        items.append({"name": name, "date": raw_date, "dday": dday})
    items.sort(key=lambda x: x["dday"])
    return items[:max_items]


async def build_home_payload() -> dict:
    """홈 화면용 집계 payload. 각 소스 개별 try/except로 부분 실패 허용."""
    payload: dict = {}
    errors: list = []

    # 1. regime — W1: 에러 dict 반환 시 가짜 neutral 만들지 않고 키 omit
    try:
        rdata = await execute_tool("get_regime", {"mode": "current"})
        if _tool_err(rdata):
            errors.append({"source": "regime", "msg": rdata["error"]})
        else:
            regime_en = rdata.get("regime_en", "neutral")
            payload["regime"] = {
                "label": rdata.get("regime", regime_en),
                "regime_en": regime_en,
                "color": _regime_color(regime_en),
                "days_in_regime": rdata.get("debounce", {}).get("days"),
            }
    except Exception as e:
        errors.append({"source": "regime", "msg": str(e)})

    # 2. portfolio summary — W1: 에러 dict 감지
    try:
        pdata = await execute_tool("get_portfolio", {})
        if _tool_err(pdata):
            errors.append({"source": "portfolio", "msg": pdata["error"]})
        elif "kr" in pdata or "us" in pdata:
            kr_sum = pdata.get("kr", {}).get("summary", {})
            us_sum = pdata.get("us", {}).get("summary", {})
            payload["portfolio"] = {
                "kr_eval": kr_sum.get("total_eval", 0),
                "kr_pnl": kr_sum.get("total_pnl", 0),
                "kr_pnl_pct": kr_sum.get("total_pnl_pct", 0),
                "us_eval": us_sum.get("total_eval", 0),
                "us_pnl": us_sum.get("total_pnl", 0),
                "us_pnl_pct": us_sum.get("total_pnl_pct", 0),
                "cash_krw": pdata.get("cash_krw", 0),
                "cash_usd": pdata.get("cash_usd", 0),
            }
        else:
            payload["portfolio"] = {"empty": True}
    except Exception as e:
        errors.append({"source": "portfolio", "msg": str(e)})

    # 3. alerts — W1: 에러 dict 감지 / I1: 손절 근접 필터+정렬 교정
    # gap_pct 부호 규약: (stop_price - cur) / cur * 100
    #   양수  = 현재가가 손절가 아래(이탈)          → 가장 위험
    #   0 근처= 손절가에 근접                       → 위험
    #   큰 음수= 손절가가 현재가에서 멀리 아래(안전) → 제외
    # 손절 근접 조건: gap_pct >= -10 (손절가 10% 이내 또는 이탈만 표시)
    # 정렬: 내림차순(양수/큰 값 = 가장 위험이 맨 위)
    try:
        adata = await execute_tool("get_alerts", {"brief": True})
        if _tool_err(adata):
            errors.append({"source": "alerts", "msg": adata["error"]})
        else:
            raw_stops = adata.get("alerts", [])
            raw_watch = adata.get("watch_alerts", [])
            # 손절: gap_pct >= -10 (손절가 10% 이내 근접 or 이미 이탈), 내림차순(가장 위험 먼저)
            # gap_pct < -10 인 안전 종목(SK하이닉스 -66% 등)은 제외
            stoploss_near = sorted(
                [a for a in raw_stops if a.get("gap_pct") is not None and a["gap_pct"] >= -10],
                key=lambda x: x["gap_pct"],
                reverse=True,
            )[:5]
            # 워치: triggered 또는 gap_pct 0~5% (희망가 5% 이내), triggered 먼저 → gap_pct 오름차순
            watch_near = sorted(
                [
                    w for w in raw_watch
                    if w.get("triggered")
                    or (w.get("gap_pct") is not None and 0 <= w["gap_pct"] <= 5)
                ],
                key=lambda x: (not x.get("triggered", False), x.get("gap_pct") if x.get("gap_pct") is not None else float("inf")),
            )[:5]
            payload["alerts"] = {
                "stoploss": stoploss_near,
                "watch": watch_near,
            }
    except Exception as e:
        errors.append({"source": "alerts", "msg": str(e)})

    # 4. events (오늘 이후 임박)
    try:
        events = load_events()
        payload["events"] = _parse_events_upcoming(events, max_items=5)
    except Exception as e:
        errors.append({"source": "events", "msg": str(e)})

    # 5. consensus (prev_avg 대비 변동 상위 N)
    # W3: abs(chg_pct) > 30 제외 — 액면분할/TP base 리셋 노이즈 차단
    try:
        cc = load_json(CONSENSUS_CACHE_FILE, {})
        kr = cc.get("kr", {})
        changed = []
        for ticker, info in kr.items():
            avg = info.get("avg", 0) or 0
            prev = info.get("prev_avg", 0) or 0
            if prev > 0 and avg > 0 and avg != prev:
                chg_pct = round((avg - prev) / prev * 100, 1)
                if abs(chg_pct) >= 1.0 and abs(chg_pct) <= 30:
                    changed.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "avg": avg,
                        "prev_avg": prev,
                        "chg_pct": chg_pct,
                    })
        changed.sort(key=lambda x: abs(x["chg_pct"]), reverse=True)
        if changed:
            payload["consensus"] = changed[:5]
    except Exception as e:
        errors.append({"source": "consensus", "msg": str(e)})

    # 6. scan — change_scan_sent.json 최근 날짜 + 건수
    # I2: os 직접 사용 불필요 — _DATA_DIR이 모듈 상단에서 이미 import됨
    try:
        scan_file = f"{_DATA_DIR}/change_scan_sent.json"
        scan_data = load_json(scan_file, {})
        if scan_data:
            dates = [v for v in scan_data.values() if isinstance(v, str)]
            latest_date = max(dates) if dates else None
            payload["scan"] = {"date": latest_date, "count": len(scan_data)}
        else:
            payload["scan"] = {"date": None, "count": 0}
    except Exception as e:
        errors.append({"source": "scan", "msg": str(e)})

    # 7. dart — dart_seen.json 건수
    try:
        dart_data = load_json(DART_SEEN_FILE, {"ids": []})
        ids = dart_data.get("ids", [])
        payload["dart"] = {"count": len(ids)}
    except Exception as e:
        errors.append({"source": "dart", "msg": str(e)})

    payload["_errors"] = errors
    return payload


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Alpine dashApp JS (인라인 <script> 본문)
# Python 문자열 안에 들어가므로 JS 문자열 리터럴 내
# 제어문자는 쓰지 않음 — \n 버그 방지.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_DASH_APP_JS = r"""
function dashApp() {
  return {
    activeTab: 'home',
    loading: false,
    lastUpdated: '',
    autoRefresh: true,
    home: null,
    _refreshTimer: null,

    /* P2: portfolio tab */
    portfolio: null,
    portSort: 'eval',
    portModal: null,
    portModalLoading: false,

    /* P2: watch/alert tab */
    watch: null,
    watchForm: { show: false, ticker: '', name: '', stop: '', target: '', buy: '' },
    watchToast: '',

    async init() {
      await this.loadHome();
      this.refreshIcons();
      this._startAutoRefresh();
    },

    _startAutoRefresh() {
      if (this._refreshTimer) clearInterval(this._refreshTimer);
      this._refreshTimer = setInterval(async () => {
        if (this.autoRefresh) {
          await this.loadHome();
          this.refreshIcons();
        }
      }, 60000);
    },

    toggleAutoRefresh() {
      this.autoRefresh = !this.autoRefresh;
    },

    async loadHome() {
      /* stale-while-revalidate: 데이터 이미 있으면 loading 화면 안 띄움.
         fetch 중 기존 데이터 유지 → 도착 시 교체. */
      if (!this.home) this.loading = true;
      const data = await this.api('/api/home');
      this.loading = false;
      if (!data.error) {
        this.home = data;
        this.lastUpdated = new Date().toLocaleTimeString('ko-KR');
      }
    },

    /* ── portfolio tab ── */
    async loadPortfolio() {
      if (this.portfolio) return;
      const data = await this.api('/api/portfolio');
      if (!data.error) this.portfolio = data;
    },

    portSorted(holdings) {
      if (!holdings || !holdings.length) return [];
      const arr = [...holdings];
      if (this.portSort === 'eval') arr.sort((a, b) => b.eval_amt - a.eval_amt);
      else if (this.portSort === 'pnl_pct') arr.sort((a, b) => b.pnl_pct - a.pnl_pct);
      else if (this.portSort === 'pnl') arr.sort((a, b) => b.pnl - a.pnl);
      return arr;
    },

    async openStockModal(ticker) {
      this.portModal = { ticker, loading: true };
      this.portModalLoading = true;
      this.$nextTick(() => this.refreshIcons());
      const data = await this.api('/api/stock/' + ticker);
      this.portModal = data.error ? { ticker, error: data.error } : data;
      this.portModalLoading = false;
      this.$nextTick(() => this.refreshIcons());
    },

    closeModal() {
      this.portModal = null;
    },

    /* ── watch/alert tab ── */
    async loadWatch() {
      /* stale-while-revalidate: 데이터 이미 있으면 null로 비우지 않고
         백그라운드로 fetch 후 도착 시 교체. 탭 최초 진입 시에만 로딩 표시. */
      const data = await this.api('/api/watch');
      if (!data.error) this.watch = data;
    },

    async removeWatch(ticker, alertType) {
      const body = JSON.stringify({ action: 'remove', ticker, alert_type: alertType || 'watchlist' });
      const r = await fetch('/api/watch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
      const d = await r.json();
      if (d.error) { this.showToast('오류: ' + d.error); return; }
      this.showToast('삭제됨: ' + ticker);
      this.watch = null;
      await this.loadWatch();
      this.$nextTick(() => this.refreshIcons());
    },

    async submitWatchForm() {
      const f = this.watchForm;
      if (!f.ticker) { this.showToast('티커를 입력하세요'); return; }
      let body;
      if (f.buy) {
        body = JSON.stringify({ action: 'set_alert', log_type: 'watch', ticker: f.ticker.toUpperCase(), name: f.name || f.ticker.toUpperCase(), buy_price: parseFloat(f.buy), stop_price: parseFloat(f.stop || 0), target_price: parseFloat(f.target || 0) });
      } else {
        body = JSON.stringify({ action: 'add', ticker: f.ticker.toUpperCase(), name: f.name || f.ticker.toUpperCase() });
      }
      const r = await fetch('/api/watch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
      const d = await r.json();
      if (d.error) { this.showToast('오류: ' + d.error); return; }
      this.showToast(d.message || '저장됨');
      this.watchForm = { show: false, ticker: '', name: '', stop: '', target: '', buy: '' };
      this.watch = null;
      await this.loadWatch();
      this.$nextTick(() => this.refreshIcons());
    },

    showToast(msg) {
      this.watchToast = msg;
      setTimeout(() => { this.watchToast = ''; }, 3000);
    },

    setTab(t) {
      this.activeTab = t;
      if (t === 'portfolio') this.loadPortfolio();
      if (t === 'watch') this.loadWatch();
      this.$nextTick(() => this.refreshIcons());
    },

    refreshIcons() {
      if (window.lucide) lucide.createIcons();
    },

    async api(path) {
      try {
        const r = await fetch(path);
        if (!r.ok) throw new Error(r.status);
        return await r.json();
      } catch (e) {
        console.error('api', path, e);
        return { error: String(e) };
      }
    },

    won(n) {
      if (n == null || isNaN(Number(n))) return '-';
      return Number(n).toLocaleString('ko-KR') + '원';
    },

    pct(n) {
      if (n == null || isNaN(Number(n))) return '-';
      const v = Number(n);
      return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
    },

    usd(n) {
      if (n == null || isNaN(Number(n))) return '-';
      return '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    },

    regimeBadgeClass(color) {
      if (color === 'green') return 'bg-green-100 text-green-700';
      if (color === 'red') return 'bg-red-100 text-red-700';
      return 'bg-amber-100 text-amber-700';
    },

    gapClass(gap) {
      if (gap === null || gap === undefined) return 'text-slate-500';
      if (gap >= 0) return 'text-red-600 font-bold';
      if (gap >= -5) return 'text-orange-500 font-semibold';
      return 'text-slate-600';
    },

    pnlClass(v) {
      if (v == null || isNaN(Number(v))) return 'text-slate-500';
      return Number(v) >= 0 ? 'text-green-600' : 'text-red-600';
    },

    consBadgeClass(chg) {
      if (chg == null) return 'text-slate-500';
      return Number(chg) >= 0 ? 'text-green-600' : 'text-red-600';
    }
  };
}
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 포트폴리오 패널 HTML (P2)
# 카드 클릭 → 종목 상세 모달 (GET /api/stock/{ticker})
# 정렬 pill: 평가금/수익률/손익금 — Alpine 클라이언트 정렬
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_PORTFOLIO_PANEL = (
    '    <!-- 포트폴리오 패널 -->\n'
    '    <section x-show="activeTab===\'portfolio\'" x-cloak>\n'
    '\n'
    '      <!-- 로딩 -->\n'
    '      <template x-if="!portfolio">\n'
    '        <div class="text-slate-400 text-center py-20">데이터 로딩 중...</div>\n'
    '      </template>\n'
    '\n'
    '      <template x-if="portfolio">\n'
    '        <div>\n'
    '\n'
    '          <!-- grand 요약 바 -->\n'
    '          <template x-if="portfolio.grand_eval_krw != null">\n'
    '            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-5">\n'
    '              <div class="grid grid-cols-2 md:grid-cols-4 gap-4">\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">총 자산 (원화환산)</div>\n'
    '                  <div class="text-xl font-bold text-slate-800" x-text="won(portfolio.grand_eval_krw)"></div>\n'
    '                </div>\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">총 손익</div>\n'
    '                  <div :class="pnlClass(portfolio.grand_pnl_krw)" class="text-xl font-bold"\n'
    '                       x-text="won(portfolio.grand_pnl_krw)"></div>\n'
    '                  <div :class="pnlClass(portfolio.grand_pnl_pct)" class="text-sm"\n'
    '                       x-text="pct(portfolio.grand_pnl_pct)"></div>\n'
    '                </div>\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">현금 (원)</div>\n'
    '                  <div class="text-lg font-semibold text-slate-700" x-text="won(portfolio.cash_krw)"></div>\n'
    '                </div>\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">현금 ($) / 환율</div>\n'
    '                  <div class="text-lg font-semibold text-slate-700" x-text="usd(portfolio.cash_usd)"></div>\n'
    '                  <div class="text-xs text-slate-400" x-text="portfolio.usd_krw ? \'1$=\' + Math.round(portfolio.usd_krw).toLocaleString(\'ko-KR\') + \'원\' : \'\'"></div>\n'
    '                </div>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 정렬 pill -->\n'
    '          <div class="flex gap-2 mb-4">\n'
    '            <button @click="portSort=\'eval\'"\n'
    '              :class="portSort===\'eval\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">평가금순</button>\n'
    '            <button @click="portSort=\'pnl_pct\'"\n'
    '              :class="portSort===\'pnl_pct\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">수익률순</button>\n'
    '            <button @click="portSort=\'pnl\'"\n'
    '              :class="portSort===\'pnl\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">손익금순</button>\n'
    '          </div>\n'
    '\n'
    '          <!-- KR 종목 -->\n'
    '          <template x-if="portfolio.kr && portfolio.kr.holdings && portfolio.kr.holdings.length">\n'
    '            <div class="mb-6">\n'
    '              <h3 class="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">국내 (KR)</h3>\n'
    '              <div class="grid grid-cols-1 md:grid-cols-2 gap-3">\n'
    '                <template x-for="h in portSorted(portfolio.kr.holdings)" :key="h.ticker">\n'
    '                  <div @click="openStockModal(h.ticker)"\n'
    '                       class="bg-white rounded-xl shadow-sm border border-slate-200 p-4 cursor-pointer hover:shadow-md hover:border-blue-300 transition-all">\n'
    '                    <div class="flex items-start justify-between mb-2">\n'
    '                      <div>\n'
    '                        <div class="text-sm font-semibold text-slate-800" x-text="h.name"></div>\n'
    '                        <div class="text-xs text-slate-400" x-text="h.ticker"></div>\n'
    '                      </div>\n'
    '                      <div :class="pnlClass(h.pnl_pct)" class="text-sm font-bold" x-text="pct(h.pnl_pct)"></div>\n'
    '                    </div>\n'
    '                    <div class="grid grid-cols-3 gap-2 text-xs text-slate-500">\n'
    '                      <div><div class="text-slate-400">수량</div><div class="font-medium text-slate-700" x-text="h.qty.toLocaleString(\'ko-KR\')"></div></div>\n'
    '                      <div><div class="text-slate-400">평단</div><div class="font-medium text-slate-700" x-text="won(h.avg_price)"></div></div>\n'
    '                      <div><div class="text-slate-400">현재가</div><div class="font-medium text-slate-700" x-text="won(h.cur_price)"></div></div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center justify-between mt-2 pt-2 border-t border-slate-50">\n'
    '                      <div class="text-xs text-slate-400">평가금액</div>\n'
    '                      <div class="text-sm font-semibold text-slate-800" x-text="won(h.eval_amt)"></div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center justify-between">\n'
    '                      <div class="text-xs text-slate-400">손익</div>\n'
    '                      <div :class="pnlClass(h.pnl)" class="text-sm font-medium" x-text="won(h.pnl)"></div>\n'
    '                    </div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- US 종목 -->\n'
    '          <template x-if="portfolio.us && portfolio.us.holdings && portfolio.us.holdings.length">\n'
    '            <div class="mb-6">\n'
    '              <h3 class="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">해외 (US)</h3>\n'
    '              <div class="grid grid-cols-1 md:grid-cols-2 gap-3">\n'
    '                <template x-for="h in portSorted(portfolio.us.holdings)" :key="h.ticker">\n'
    '                  <div @click="openStockModal(h.ticker)"\n'
    '                       class="bg-white rounded-xl shadow-sm border border-slate-200 p-4 cursor-pointer hover:shadow-md hover:border-blue-300 transition-all">\n'
    '                    <div class="flex items-start justify-between mb-2">\n'
    '                      <div>\n'
    '                        <div class="text-sm font-semibold text-slate-800" x-text="h.name"></div>\n'
    '                        <div class="text-xs text-slate-400" x-text="h.ticker"></div>\n'
    '                      </div>\n'
    '                      <div :class="pnlClass(h.pnl_pct)" class="text-sm font-bold" x-text="pct(h.pnl_pct)"></div>\n'
    '                    </div>\n'
    '                    <div class="grid grid-cols-3 gap-2 text-xs text-slate-500">\n'
    '                      <div><div class="text-slate-400">수량</div><div class="font-medium text-slate-700" x-text="h.qty"></div></div>\n'
    '                      <div><div class="text-slate-400">평단</div><div class="font-medium text-slate-700" x-text="usd(h.avg_price)"></div></div>\n'
    '                      <div><div class="text-slate-400">현재가</div><div class="font-medium text-slate-700" x-text="usd(h.cur_price)"></div></div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center justify-between mt-2 pt-2 border-t border-slate-50">\n'
    '                      <div class="text-xs text-slate-400">평가금액</div>\n'
    '                      <div class="text-sm font-semibold text-slate-800" x-text="usd(h.eval_amt)"></div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center justify-between">\n'
    '                      <div class="text-xs text-slate-400">손익</div>\n'
    '                      <div :class="pnlClass(h.pnl)" class="text-sm font-medium" x-text="usd(h.pnl)"></div>\n'
    '                    </div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 빈 상태 -->\n'
    '          <template x-if="(!portfolio.kr || !portfolio.kr.holdings || !portfolio.kr.holdings.length) && (!portfolio.us || !portfolio.us.holdings || !portfolio.us.holdings.length)">\n'
    '            <div class="text-slate-400 text-center py-20">보유 종목이 없습니다</div>\n'
    '          </template>\n'
    '\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '      <!-- 종목 상세 모달 -->\n'
    '      <template x-if="portModal">\n'
    '        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="closeModal()">\n'
    '          <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 p-6 relative">\n'
    '            <button @click="closeModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-700">\n'
    '              <i data-lucide="x" class="w-5 h-5"></i>\n'
    '            </button>\n'
    '            <!-- 로딩 상태 -->\n'
    '            <template x-if="portModalLoading">\n'
    '              <div class="text-slate-400 text-center py-10">조회 중...</div>\n'
    '            </template>\n'
    '            <!-- 에러 -->\n'
    '            <template x-if="!portModalLoading && portModal.error">\n'
    '              <div>\n'
    '                <div class="text-sm font-bold text-slate-700 mb-2" x-text="portModal.ticker"></div>\n'
    '                <div class="text-red-500 text-sm" x-text="portModal.error"></div>\n'
    '              </div>\n'
    '            </template>\n'
    '            <!-- 데이터 -->\n'
    '            <template x-if="!portModalLoading && !portModal.error && portModal.ticker">\n'
    '              <div>\n'
    '                <div class="flex items-baseline gap-2 mb-4">\n'
    '                  <span class="text-lg font-bold text-slate-800" x-text="portModal.name || portModal.ticker"></span>\n'
    '                  <span class="text-xs text-slate-400" x-text="portModal.ticker"></span>\n'
    '                </div>\n'
    '                <div class="grid grid-cols-2 gap-3 text-sm">\n'
    '                  <div class="bg-slate-50 rounded-lg p-3">\n'
    '                    <div class="text-xs text-slate-400 mb-0.5">현재가</div>\n'
    '                    <div class="font-semibold text-slate-800" x-text="portModal.cur_price != null ? (portModal.market===\'US\' ? usd(portModal.cur_price) : won(portModal.cur_price)) : \'-\'"></div>\n'
    '                    <div :class="pnlClass(portModal.chg_rate)" class="text-xs" x-text="portModal.chg_rate != null ? pct(portModal.chg_rate) : \'\'"></div>\n'
    '                  </div>\n'
    '                  <div class="bg-slate-50 rounded-lg p-3">\n'
    '                    <div class="text-xs text-slate-400 mb-0.5">PER / PBR</div>\n'
    '                    <div class="font-semibold text-slate-800" x-text="(portModal.per != null ? portModal.per : \'-\') + \' / \' + (portModal.pbr != null ? portModal.pbr : \'-\')"></div>\n'
    '                  </div>\n'
    '                  <template x-if="portModal.foreign_net != null">\n'
    '                    <div class="bg-slate-50 rounded-lg p-3">\n'
    '                      <div class="text-xs text-slate-400 mb-0.5">외인 순매수</div>\n'
    '                      <div :class="pnlClass(portModal.foreign_net)" class="font-semibold" x-text="portModal.foreign_net != null ? portModal.foreign_net.toLocaleString(\'ko-KR\') : \'-\'"></div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="portModal.inst_net != null">\n'
    '                    <div class="bg-slate-50 rounded-lg p-3">\n'
    '                      <div class="text-xs text-slate-400 mb-0.5">기관 순매수</div>\n'
    '                      <div :class="pnlClass(portModal.inst_net)" class="font-semibold" x-text="portModal.inst_net != null ? portModal.inst_net.toLocaleString(\'ko-KR\') : \'-\'"></div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '    </section>\n'
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 워치·알림 패널 HTML (P2)
# 섹션: 손절/목표 알림 | 매수감시 | 감시종목 목록 | 추가 폼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_WATCH_PANEL = (
    '    <!-- 워치·알림 패널 -->\n'
    '    <section x-show="activeTab===\'watch\'" x-cloak>\n'
    '\n'
    '      <!-- 로딩: 초기 1회만 표시, 이후 재fetch 시에는 기존 데이터 유지(stale-while-revalidate) -->\n'
    '      <template x-if="!watch">\n'
    '        <div class="text-slate-400 text-center py-20">데이터 로딩 중...</div>\n'
    '      </template>\n'
    '\n'
    '      <template x-if="watch">\n'
    '        <div>\n'
    '\n'
    '          <!-- 토스트 -->\n'
    '          <template x-if="watchToast">\n'
    '            <div class="fixed top-20 right-4 z-50 bg-slate-800 text-white text-sm px-4 py-2 rounded-lg shadow-lg" x-text="watchToast"></div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 추가 폼 토글 버튼 -->\n'
    '          <div class="flex items-center justify-between mb-4">\n'
    '            <h2 class="text-base font-semibold text-slate-700">워치 &amp; 알림 관리</h2>\n'
    '            <button @click="watchForm.show = !watchForm.show"\n'
    '              :class="watchForm.show ? \'bg-slate-600\' : \'bg-blue-600\'"\n'
    '              class="text-xs text-white px-3 py-1.5 rounded-lg font-medium">\n'
    '              <span x-text="watchForm.show ? \'닫기\' : \'+ 추가\'"></span>\n'
    '            </button>\n'
    '          </div>\n'
    '\n'
    '          <!-- 추가 폼 (슬라이드) -->\n'
    '          <template x-if="watchForm.show">\n'
    '            <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-5">\n'
    '              <h3 class="text-sm font-semibold text-slate-700 mb-3">워치 / 손절·목표 / 매수감시 추가</h3>\n'
    '              <div class="grid grid-cols-2 md:grid-cols-3 gap-3">\n'
    '                <div>\n'
    '                  <label class="text-xs text-slate-500 block mb-1">티커 *</label>\n'
    '                  <input x-model="watchForm.ticker" placeholder="005930 / NVDA"\n'
    '                    class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '                </div>\n'
    '                <div>\n'
    '                  <label class="text-xs text-slate-500 block mb-1">종목명</label>\n'
    '                  <input x-model="watchForm.name" placeholder="이름 (선택)"\n'
    '                    class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '                </div>\n'
    '                <div>\n'
    '                  <label class="text-xs text-slate-500 block mb-1">매수감시가</label>\n'
    '                  <input x-model="watchForm.buy" placeholder="0 = 순수 워치"\n'
    '                    class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '                </div>\n'
    '                <div>\n'
    '                  <label class="text-xs text-slate-500 block mb-1">손절가</label>\n'
    '                  <input x-model="watchForm.stop" placeholder="선택"\n'
    '                    class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '                </div>\n'
    '                <div>\n'
    '                  <label class="text-xs text-slate-500 block mb-1">목표가</label>\n'
    '                  <input x-model="watchForm.target" placeholder="선택"\n'
    '                    class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '                </div>\n'
    '              </div>\n'
    '              <button @click="submitWatchForm()"\n'
    '                class="mt-3 bg-blue-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors">저장</button>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 손절/목표 알림 섹션 — watch.stoploss_alerts (cur/stop_price/target_price 실값) -->\n'
    '          <template x-if="watch && watch.stoploss_alerts && watch.stoploss_alerts.length">\n'
    '            <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-4">\n'
    '              <div class="flex items-center gap-2 mb-3">\n'
    '                <i data-lucide="alert-triangle" class="w-4 h-4 text-red-500"></i>\n'
    '                <h3 class="text-sm font-semibold text-slate-700">손절·목표 알림</h3>\n'
    '              </div>\n'
    '              <div class="overflow-x-auto">\n'
    '                <table class="w-full text-sm">\n'
    '                  <thead>\n'
    '                    <tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                      <th class="text-left py-2 pr-3 font-medium">종목</th>\n'
    '                      <th class="text-right py-2 pr-3 font-medium">현재가</th>\n'
    '                      <th class="text-right py-2 pr-3 font-medium">손절가</th>\n'
    '                      <th class="text-right py-2 pr-3 font-medium">목표가</th>\n'
    '                      <th class="text-right py-2 font-medium">손절 gap</th>\n'
    '                      <th class="py-2 pl-3"></th>\n'
    '                    </tr>\n'
    '                  </thead>\n'
    '                  <tbody>\n'
    '                    <template x-for="a in watch.stoploss_alerts" :key="a.ticker">\n'
    '                      <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                        <td class="py-2 pr-3">\n'
    '                          <div class="font-medium text-slate-800" x-text="a.name"></div>\n'
    '                          <div class="text-xs text-slate-400" x-text="a.ticker"></div>\n'
    '                        </td>\n'
    '                        <td class="text-right py-2 pr-3 text-slate-700" x-text="a.market===\'US\' ? usd(a.cur) : won(a.cur)"></td>\n'
    '                        <td class="text-right py-2 pr-3 text-slate-600" x-text="a.stop_price ? (a.market===\'US\' ? usd(a.stop_price) : won(a.stop_price)) : \'-\'"></td>\n'
    '                        <td class="text-right py-2 pr-3 text-slate-600" x-text="a.target_price ? (a.market===\'US\' ? usd(a.target_price) : won(a.target_price)) : \'-\'"></td>\n'
    '                        <td class="text-right py-2">\n'
    '                          <span :class="gapClass(a.gap_pct)" x-text="a.gap_pct != null ? (a.gap_pct > 0 ? \'+\' : \'\') + a.gap_pct.toFixed(1) + \'%\' : \'-\'"></span>\n'
    '                        </td>\n'
    '                        <td class="pl-3 py-2">\n'
    '                          <button @click="removeWatch(a.ticker, \'alert\')" class="text-xs text-slate-300 hover:text-red-500 transition-colors">\n'
    '                            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>\n'
    '                          </button>\n'
    '                        </td>\n'
    '                      </tr>\n'
    '                    </template>\n'
    '                  </tbody>\n'
    '                </table>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 매수감시 섹션 — watch.buy_watch (cur_price=0이면 gap 표시 안 함) -->\n'
    '          <template x-if="watch && watch.buy_watch && watch.buy_watch.length">\n'
    '            <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-4">\n'
    '              <div class="flex items-center gap-2 mb-3">\n'
    '                <i data-lucide="target" class="w-4 h-4 text-blue-500"></i>\n'
    '                <h3 class="text-sm font-semibold text-slate-700">매수 감시</h3>\n'
    '              </div>\n'
    '              <div class="space-y-2">\n'
    '                <template x-for="bw in watch.buy_watch" :key="bw.ticker">\n'
    '                  <div class="flex items-center justify-between py-2 border-b border-slate-50 last:border-0">\n'
    '                    <div class="flex items-center gap-2">\n'
    '                      <div>\n'
    '                        <span class="text-sm font-medium text-slate-800" x-text="bw.name"></span>\n'
    '                        <span class="text-xs text-slate-400 ml-1" x-text="bw.ticker"></span>\n'
    '                        <template x-if="bw.triggered">\n'
    '                          <span class="ml-1 text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 font-medium">도달!</span>\n'
    '                        </template>\n'
    '                        <template x-if="bw.grade">\n'
    '                          <span class="ml-1 text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-600" x-text="bw.grade"></span>\n'
    '                        </template>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center gap-4">\n'
    '                      <div class="text-right">\n'
    '                        <div class="text-xs text-slate-400">희망가</div>\n'
    '                        <div class="text-sm text-slate-700" x-text="bw.market===\'US\' ? usd(bw.buy_price) : won(bw.buy_price)"></div>\n'
    '                      </div>\n'
    '                      <div class="text-right">\n'
    '                        <div class="text-xs text-slate-400">현재가</div>\n'
    '                        <div class="text-sm text-slate-700" x-text="bw.cur_price ? (bw.market===\'US\' ? usd(bw.cur_price) : won(bw.cur_price)) : \'가격없음\'"></div>\n'
    '                      </div>\n'
    '                      <div class="text-right w-16">\n'
    '                        <div class="text-xs text-slate-400">gap</div>\n'
    '                        <div class="text-sm" :class="bw.gap_pct != null && bw.gap_pct <= 0 ? \'text-green-600 font-semibold\' : \'text-slate-600\'"\n'
    '                             x-text="bw.gap_pct != null ? (bw.gap_pct > 0 ? \'+\' : \'\') + bw.gap_pct.toFixed(1) + \'%\' : \'—\'"></div>\n'
    '                      </div>\n'
    '                      <button @click="removeWatch(bw.ticker, \'buy_alert\')" class="text-xs text-slate-300 hover:text-red-500 transition-colors">\n'
    '                        <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>\n'
    '                      </button>\n'
    '                    </div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 감시종목 목록 -->\n'
    '          <template x-if="watch && watch.watchlist && watch.watchlist.length">\n'
    '            <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-4">\n'
    '              <div class="flex items-center gap-2 mb-3">\n'
    '                <i data-lucide="bookmark" class="w-4 h-4 text-indigo-500"></i>\n'
    '                <h3 class="text-sm font-semibold text-slate-700">감시 종목</h3>\n'
    '                <span class="text-xs text-slate-400" x-text="\'(\' + watch.watchlist.length + \'종목)\'"></span>\n'
    '              </div>\n'
    '              <div class="grid grid-cols-1 md:grid-cols-2 gap-2">\n'
    '                <template x-for="w in watch.watchlist" :key="w.ticker">\n'
    '                  <div class="flex items-center justify-between py-2 px-3 bg-slate-50 rounded-lg">\n'
    '                    <div class="flex items-center gap-2">\n'
    '                      <div>\n'
    '                        <span class="text-sm font-medium text-slate-800" x-text="w.name"></span>\n'
    '                        <span class="text-xs text-slate-400 ml-1" x-text="w.ticker"></span>\n'
    '                        <template x-if="w.grade">\n'
    '                          <span class="ml-1 text-xs px-1.5 py-0.5 rounded bg-slate-200 text-slate-600" x-text="w.grade"></span>\n'
    '                        </template>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                    <div class="flex items-center gap-2">\n'
    '                      <span class="text-xs text-slate-400 bg-white px-1.5 py-0.5 rounded" x-text="w.market || \'KR\'"></span>\n'
    '                      <button @click="removeWatch(w.ticker, \'watchlist\')" class="text-slate-300 hover:text-red-500 transition-colors">\n'
    '                        <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>\n'
    '                      </button>\n'
    '                    </div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 빈 상태 -->\n'
    '          <template x-if="watch && (!watch.watchlist || !watch.watchlist.length) && (!watch.stoploss_alerts || !watch.stoploss_alerts.length) && (!watch.buy_watch || !watch.buy_watch.length)">\n'
    '            <div class="text-slate-400 text-center py-20">워치·알림이 없습니다</div>\n'
    '          </template>\n'
    '\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '    </section>\n'
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 홈 패널 HTML (Alpine 템플릿)
# 완전히 별도 문자열로 분리 — JS 중괄호와 충돌 없음.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_HOME_PANEL = (
    '    <!-- 홈 패널 -->\n'
    '    <section x-show="activeTab===\'home\'">\n'
    '\n'
    '      <!-- 로딩 중 -->\n'
    '      <template x-if="!home">\n'
    '        <div class="text-slate-400 text-center py-20">데이터 로딩 중...</div>\n'
    '      </template>\n'
    '\n'
    '      <template x-if="home">\n'
    '        <div>\n'
    '\n'
    '          <!-- 자산 요약 카드 -->\n'
    '          <template x-if="home.portfolio && !home.portfolio.empty">\n'
    '            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-6">\n'
    '              <h2 class="text-sm font-semibold text-slate-500 mb-3">자산 요약</h2>\n'
    '              <div class="grid grid-cols-2 md:grid-cols-4 gap-4">\n'
    '                <!-- KR 평가 -->\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">국내 평가</div>\n'
    '                  <div class="text-lg font-bold text-slate-800" x-text="won(home.portfolio.kr_eval)"></div>\n'
    '                  <div :class="pnlClass(home.portfolio.kr_pnl)" class="text-sm"\n'
    '                       x-text="won(home.portfolio.kr_pnl) + \' (\' + pct(home.portfolio.kr_pnl_pct) + \')\'"></div>\n'
    '                </div>\n'
    '                <!-- US 평가 -->\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">해외 평가</div>\n'
    '                  <div class="text-lg font-bold text-slate-800" x-text="usd(home.portfolio.us_eval)"></div>\n'
    '                  <div :class="pnlClass(home.portfolio.us_pnl)" class="text-sm"\n'
    '                       x-text="usd(home.portfolio.us_pnl) + \' (\' + pct(home.portfolio.us_pnl_pct) + \')\'"></div>\n'
    '                </div>\n'
    '                <!-- 현금 KRW -->\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">현금 (원)</div>\n'
    '                  <div class="text-lg font-bold text-slate-700" x-text="won(home.portfolio.cash_krw)"></div>\n'
    '                </div>\n'
    '                <!-- 현금 USD -->\n'
    '                <div>\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">현금 ($)</div>\n'
    '                  <div class="text-lg font-bold text-slate-700" x-text="usd(home.portfolio.cash_usd)"></div>\n'
    '                </div>\n'
    '              </div>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 신호 카드 그리드 -->\n'
    '          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">\n'
    '\n'
    '            <!-- 손절 근접 카드 -->\n'
    '            <template x-if="home.alerts && home.alerts.stoploss && home.alerts.stoploss.length">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="alert-triangle" class="w-4 h-4 text-red-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">손절 근접</span>\n'
    '                </div>\n'
    '                <template x-for="a in home.alerts.stoploss" :key="a.ticker">\n'
    '                  <div class="flex items-center justify-between py-1.5 border-b border-slate-50 last:border-0">\n'
    '                    <div>\n'
    '                      <span class="text-sm font-medium text-slate-800" x-text="a.name"></span>\n'
    '                      <span class="text-xs text-slate-400 ml-1" x-text="a.ticker"></span>\n'
    '                    </div>\n'
    '                    <div :class="gapClass(a.gap_pct)" class="text-sm"\n'
    '                         x-text="a.gap_pct != null ? (a.gap_pct > 0 ? \'+\' : \'\') + a.gap_pct.toFixed(1) + \'%\' : \'-\'"></div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- 워치 근접 카드 -->\n'
    '            <template x-if="home.alerts && home.alerts.watch && home.alerts.watch.length">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="target" class="w-4 h-4 text-blue-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">매수 근접</span>\n'
    '                </div>\n'
    '                <template x-for="w in home.alerts.watch" :key="w.ticker">\n'
    '                  <div class="flex items-center justify-between py-1.5 border-b border-slate-50 last:border-0">\n'
    '                    <div class="flex items-center gap-1.5">\n'
    '                      <span class="text-sm font-medium text-slate-800" x-text="w.name"></span>\n'
    '                      <template x-if="w.triggered">\n'
    '                        <span class="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">도달</span>\n'
    '                      </template>\n'
    '                    </div>\n'
    '                    <div class="text-sm text-slate-600"\n'
    '                         x-text="w.gap_pct != null ? (w.gap_pct > 0 ? \'+\' : \'\') + w.gap_pct.toFixed(1) + \'%\' : \'-\'"></div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- 임박 이벤트 카드 -->\n'
    '            <template x-if="home.events && home.events.length">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="calendar" class="w-4 h-4 text-purple-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">임박 이벤트</span>\n'
    '                </div>\n'
    '                <template x-for="ev in home.events" :key="ev.name">\n'
    '                  <div class="flex items-center justify-between py-1.5 border-b border-slate-50 last:border-0">\n'
    '                    <span class="text-sm text-slate-700 truncate max-w-[160px]" x-text="ev.name"></span>\n'
    '                    <span class="text-xs text-slate-500 whitespace-nowrap ml-2"\n'
    '                          x-text="\'D-\' + ev.dday + \' (\' + ev.date + \')\'"></span>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- 발굴 스캔 카드 -->\n'
    '            <template x-if="home.scan && home.scan.count > 0">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="search" class="w-4 h-4 text-teal-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">변화감지 스캔</span>\n'
    '                </div>\n'
    '                <div class="text-slate-700">\n'
    '                  <span class="text-2xl font-bold" x-text="home.scan.count"></span>\n'
    '                  <span class="text-sm text-slate-400 ml-1">건</span>\n'
    '                </div>\n'
    '                <div class="text-xs text-slate-400 mt-1" x-text="home.scan.date ? \'최근: \' + home.scan.date : \'\'"></div>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- 컨센서스 변동 카드 -->\n'
    '            <template x-if="home.consensus && home.consensus.length">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="trending-up" class="w-4 h-4 text-indigo-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">컨센서스 변동</span>\n'
    '                </div>\n'
    '                <template x-for="c in home.consensus" :key="c.ticker">\n'
    '                  <div class="flex items-center justify-between py-1.5 border-b border-slate-50 last:border-0">\n'
    '                    <span class="text-sm text-slate-700" x-text="c.name"></span>\n'
    '                    <span :class="consBadgeClass(c.chg_pct)" class="text-sm"\n'
    '                          x-text="(c.chg_pct >= 0 ? \'+\' : \'\') + c.chg_pct + \'%\'"></span>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- DART 카드 -->\n'
    '            <template x-if="home.dart && home.dart.count > 0">\n'
    '              <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                <div class="flex items-center gap-2 mb-3">\n'
    '                  <i data-lucide="file-text" class="w-4 h-4 text-orange-500"></i>\n'
    '                  <span class="text-sm font-semibold text-slate-700">DART 공시</span>\n'
    '                </div>\n'
    '                <div class="text-slate-700">\n'
    '                  <span class="text-2xl font-bold" x-text="home.dart.count"></span>\n'
    '                  <span class="text-sm text-slate-400 ml-1">건 처리됨</span>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '          </div><!-- /신호 카드 그리드 -->\n'
    '\n'
    '          <!-- 에러 디버그 (있을 때만) -->\n'
    '          <template x-if="home._errors && home._errors.length">\n'
    '            <div class="mt-4 text-xs text-slate-400">\n'
    '              <template x-for="err in home._errors" :key="err.source">\n'
    '                <div x-text="err.source + \': \' + err.msg"></div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '    </section>\n'
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 완성된 HTML 문서 (일반 문자열 — f-string 아님)
# JS 중괄호와 충돌 없음. Alpine 속성은 HTML 어트리뷰트라 OK.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_HOME_SHELL = (
    "<!DOCTYPE html>\n"
    '<html lang="ko">\n'
    "<head>\n"
    '  <meta charset="utf-8">\n'
    '  <meta name="viewport" content="width=device-width,initial-scale=1">\n'
    "  <title>\U0001f4ca Stock Bot</title>\n"
    '  <script src="https://cdn.tailwindcss.com"></script>\n'
    '  <script src="https://unpkg.com/lucide@latest"></script>\n'
    '  <script defer src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js"></script>\n'
    "  <style>\n"
    "    @import url('https://fonts.googleapis.com/css2?family=Pretendard:wght@400;600;800&display=swap');\n"
    "    body { font-family: 'Pretendard', sans-serif; background-color: #f8fafc; }\n"
    "    [x-cloak] { display: none !important; }\n"
    "  </style>\n"
    "</head>\n"
    '<body class="min-h-screen">\n'
    '\n'
    '<!-- Alpine 루트 -->\n'
    '<div x-data="dashApp()" x-init="init()">\n'
    '\n'
    '  <!-- 상단 sticky 바 -->\n'
    '  <header class="sticky top-0 z-50 bg-white border-b border-slate-200 shadow-sm">\n'
    '    <div class="max-w-6xl mx-auto px-4 flex items-center justify-between h-12">\n'
    '      <div class="flex items-center gap-2">\n'
    '        <span class="text-lg font-bold text-slate-800">\U0001f4ca Stock Bot</span>\n'
    '        <template x-if="home && home.regime">\n'
    '          <span\n'
    '            :class="[\'text-xs px-2 py-0.5 rounded-full\', regimeBadgeClass(home.regime.color)]"\n'
    '            x-text="home.regime.label"\n'
    '          ></span>\n'
    '        </template>\n'
    '        <template x-if="!home || !home.regime">\n'
    '          <span class="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-400">로딩...</span>\n'
    '        </template>\n'
    '      </div>\n'
    '      <div class="flex items-center gap-3">\n'
    '        <span x-text="lastUpdated" class="text-xs text-slate-400"></span>\n'
    '        <button\n'
    '          @click="toggleAutoRefresh()"\n'
    '          :class="autoRefresh ? \'bg-blue-50 border-blue-200 text-blue-600\' : \'border-slate-200 text-slate-500\'"\n'
    '          class="text-xs px-2 py-1 rounded border hover:opacity-80 transition-opacity"\n'
    '          x-text="autoRefresh ? \'자동갱신 ON\' : \'자동갱신 OFF\'"\n'
    '        ></button>\n'
    '      </div>\n'
    '    </div>\n'
    '  </header>\n'
    '\n'
    '  <!-- 탭 네비 (7개) -->\n'
    '  <nav class="bg-white border-b border-slate-200 sticky top-12 z-40">\n'
    '    <div class="max-w-6xl mx-auto px-4">\n'
    '      <div class="overflow-x-auto">\n'
    '        <div class="flex gap-1 py-2 whitespace-nowrap">\n'
    '\n'
    '          <!-- 홈 -->\n'
    '          <button\n'
    '            @click="setTab(\'home\')"\n'
    '            :class="activeTab===\'home\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="home" class="w-4 h-4"></i>\n'
    '            홈\n'
    '          </button>\n'
    '\n'
    '          <!-- 포트폴리오 -->\n'
    '          <button\n'
    '            @click="setTab(\'portfolio\')"\n'
    '            :class="activeTab===\'portfolio\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="bar-chart-2" class="w-4 h-4"></i>\n'
    '            포트폴리오\n'
    '          </button>\n'
    '\n'
    '          <!-- 워치·알림 -->\n'
    '          <button\n'
    '            @click="setTab(\'watch\')"\n'
    '            :class="activeTab===\'watch\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="bell" class="w-4 h-4"></i>\n'
    '            워치·알림\n'
    '          </button>\n'
    '\n'
    '          <!-- 시그널 -->\n'
    '          <button\n'
    '            @click="setTab(\'signal\')"\n'
    '            :class="activeTab===\'signal\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="zap" class="w-4 h-4"></i>\n'
    '            시그널\n'
    '          </button>\n'
    '\n'
    '          <!-- 기록 -->\n'
    '          <button\n'
    '            @click="setTab(\'record\')"\n'
    '            :class="activeTab===\'record\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="clipboard-list" class="w-4 h-4"></i>\n'
    '            기록\n'
    '          </button>\n'
    '\n'
    '          <!-- Whale -->\n'
    '          <button\n'
    '            @click="setTab(\'whale\')"\n'
    '            :class="activeTab===\'whale\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="fish" class="w-4 h-4"></i>\n'
    '            Whale\n'
    '          </button>\n'
    '\n'
    '          <!-- 리포트 -->\n'
    '          <button\n'
    '            @click="setTab(\'report\')"\n'
    '            :class="activeTab===\'report\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="file-text" class="w-4 h-4"></i>\n'
    '            리포트\n'
    '          </button>\n'
    '\n'
    '        </div>\n'
    '      </div>\n'
    '    </div>\n'
    '  </nav>\n'
    '\n'
    '  <!-- 탭 패널 -->\n'
    '  <main class="max-w-6xl mx-auto px-4 py-6">\n'
    '\n'
    + _HOME_PANEL
    + _PORTFOLIO_PANEL
    + _WATCH_PANEL
    + '\n'
    '    <!-- 시그널 -->\n'
    '    <section x-show="activeTab===\'signal\'">\n'
    '      <div class="text-slate-400 text-center py-20">시그널 (P1에서 구현)</div>\n'
    '    </section>\n'
    '\n'
    '    <!-- 기록 -->\n'
    '    <section x-show="activeTab===\'record\'">\n'
    '      <div class="text-slate-400 text-center py-20">기록 (P3에서 구현)</div>\n'
    '    </section>\n'
    '\n'
    '    <!-- Whale -->\n'
    '    <section x-show="activeTab===\'whale\'">\n'
    '      <div class="text-slate-400 text-center py-20">Whale (P3에서 구현)</div>\n'
    '    </section>\n'
    '\n'
    '    <!-- 리포트 -->\n'
    '    <section x-show="activeTab===\'report\'">\n'
    '      <div class="text-slate-400 text-center py-20">리포트 (P3에서 구현)</div>\n'
    '    </section>\n'
    '\n'
    '  </main>\n'
    '\n'
    '</div><!-- /Alpine 루트 -->\n'
    '\n'
    "<script>\n"
    + _DASH_APP_JS
    + "\n</script>\n"
    "\n"
    "</body>\n"
    "</html>\n"
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _handle_home(request: web.Request) -> web.Response:
    return web.Response(text=_HOME_SHELL, content_type="text/html")


async def _handle_api_regime(request: web.Request) -> web.Response:
    return await _api(execute_tool("get_regime", {"mode": "current"}))


async def _handle_api_alerts(request: web.Request) -> web.Response:
    # TTL 240s: /api/watch가 stoploss_alerts를 직접 포함하므로 이 엔드포인트는 brief 요약용
    return await _api(_cached("alerts", 240.0, lambda: execute_tool("get_alerts", {"brief": True})))


async def _build_portfolio_with_grand() -> dict:
    """get_portfolio 결과에 원화환산 grand 합계를 추가.

    grand_eval_krw = kr_eval + us_eval * usd_krw
    grand_pnl_krw  = kr_pnl + us_pnl * usd_krw
    USDKRW 환율 실패 시 fallback 1400 사용.
    """
    pdata = await execute_tool("get_portfolio", {})
    if _tool_err(pdata):
        return pdata
    # USDKRW 환율 조회 (실패 허용)
    usd_krw = 1400.0
    try:
        fx = await get_yahoo_quote("USDKRW=X")
        if fx and fx.get("price"):
            usd_krw = float(fx["price"])
    except Exception:
        pass
    kr_sum = pdata.get("kr", {}).get("summary", {})
    us_sum = pdata.get("us", {}).get("summary", {})
    kr_eval = float(kr_sum.get("total_eval", 0) or 0)
    kr_pnl  = float(kr_sum.get("total_pnl", 0) or 0)
    kr_cost = float(kr_sum.get("total_cost", 0) or 0)
    us_eval = float(us_sum.get("total_eval", 0) or 0)
    us_pnl  = float(us_sum.get("total_pnl", 0) or 0)
    us_cost = float(us_sum.get("total_cost", 0) or 0)
    grand_eval_krw = kr_eval + us_eval * usd_krw
    grand_pnl_krw  = kr_pnl  + us_pnl  * usd_krw
    grand_cost_krw = kr_cost + us_cost * usd_krw
    grand_pnl_pct  = round(grand_pnl_krw / grand_cost_krw * 100, 2) if grand_cost_krw else 0
    pdata["usd_krw"]        = round(usd_krw, 2)
    pdata["grand_eval_krw"] = round(grand_eval_krw, 0)
    pdata["grand_pnl_krw"]  = round(grand_pnl_krw, 0)
    pdata["grand_pnl_pct"]  = grand_pnl_pct
    return pdata


async def _handle_api_portfolio(request: web.Request) -> web.Response:
    # W2: 람다 팩토리 — 캐시 hit 시 코루틴 미생성으로 RuntimeWarning 방지
    # TTL 240s: 장 마감 후 가격 변동 작고 글랜스 대시보드라 4분 staleness 무방
    return await _api(_cached("portfolio", 240.0, _build_portfolio_with_grand))


async def _handle_api_home(request: web.Request) -> web.Response:
    # W2: 람다 팩토리 — 캐시 hit 시 코루틴 미생성으로 RuntimeWarning 방지
    # TTL 240s: 프론트 자동갱신 60초 유지, 대부분 캐시 히트 → 4분마다 1회 콜드
    return await _api(_cached("home", 240.0, lambda: build_home_payload()))


def _is_us_ticker_simple(ticker: str) -> bool:
    """숫자로만 구성 = KR, 알파벳 포함 = US (간단 판별)."""
    return bool(ticker) and not ticker.isdigit()


async def _build_watch_payload() -> dict:
    """GET /api/watch — load_watchalert() + execute_tool get_alerts(full) 병합.

    반환:
        watchlist: [감시종목 목록] — buy_price=0 포함 전체
        buy_watch: watch_alerts (현재가 포함)  ← execute_tool에서 실시간 현재가 반영
        stoploss_alerts: 손절/목표가 알림 — cur·stop_price·target_price·gap_pct 실값
    """
    wa = load_watchalert()
    # watchlist: watchalert 전체 항목 (순수 감시 + 매수감시 모두)
    watchlist = [
        {
            "ticker": ticker,
            "name": info.get("name", ticker),
            "market": info.get("market", ""),
            "grade": info.get("grade", ""),
            "buy_price": info.get("buy_price", 0),
            "memo": info.get("memo", ""),
            "created_at": info.get("created_at", ""),
        }
        for ticker, info in wa.items()
    ]

    # get_alerts full 호출 — cur/gap_pct/target_pct 확보
    adata: dict = {}
    try:
        adata = await execute_tool("get_alerts", {})
        if _tool_err(adata):
            adata = {}
    except Exception:
        adata = {}

    # buy_watch: watch_alerts (현재가 포함)
    buy_watch = adata.get("watch_alerts", [])
    # cur_price=0인 항목의 gap "-100%" → null로 교체 (오해 방지)
    for bw in buy_watch:
        if bw.get("cur_price") == 0 or bw.get("cur_price") is None:
            bw["gap_pct"] = None

    # stoploss_alerts: get_alerts.alerts + load_stoploss() 절대가 병합
    raw_alerts = adata.get("alerts", [])
    sl_data = {}
    try:
        sl_raw = load_stoploss()
        # stoploss.json 구조: {ticker: {stop_price, target_price, name}, us_stocks: {ticker: ...}}
        for ticker, info in sl_raw.items():
            if ticker == "us_stocks":
                for us_ticker, us_info in (info or {}).items():
                    sl_data[us_ticker] = us_info
            elif isinstance(info, dict):
                sl_data[ticker] = info
    except Exception:
        pass

    stoploss_alerts = []
    for alert in raw_alerts:
        ticker = alert.get("ticker", "")
        sl_info = sl_data.get(ticker, {})
        is_us = _is_us_ticker_simple(ticker)
        stop_price_raw = sl_info.get("stop_price") or sl_info.get("stop")
        target_price_raw = sl_info.get("target_price") or sl_info.get("target")
        # 0 또는 0.0 = 미설정 → None으로 정규화 (템플릿에서 '-' 표시)
        stop_price = stop_price_raw if stop_price_raw else None
        target_price = target_price_raw if target_price_raw else None
        cur_val = alert.get("cur")
        gap_val = alert.get("gap_pct")
        # cur=0이면 프라이싱 실패 → gap_pct도 무의미, None으로 정규화
        if not cur_val:
            cur_val = None
            gap_val = None
        stoploss_alerts.append({
            "ticker": ticker,
            "name": alert.get("name", sl_info.get("name", ticker)),
            "market": "US" if is_us else "KR",
            "cur": cur_val,
            "stop_price": stop_price,
            "target_price": target_price,
            "gap_pct": gap_val,
            "target_pct": alert.get("target_pct"),
        })

    return {"watchlist": watchlist, "buy_watch": buy_watch, "stoploss_alerts": stoploss_alerts}


async def _handle_api_watch_get(request: web.Request) -> web.Response:
    # TTL 240s: home/portfolio와 동일 (4분 staleness 무방)
    return await _api(_cached("watch", 240.0, _build_watch_payload))


async def _handle_api_stock_detail(request: web.Request) -> web.Response:
    ticker = request.match_info.get("ticker", "").strip().upper()
    if not ticker:
        return web.json_response({"error": "ticker required"}, status=200)

    async def _fetch():
        raw = await execute_tool("get_stock_detail", {"ticker": ticker})
        if _tool_err(raw):
            return raw
        # 핵심 필드만 추려서 모달용으로 반환
        return {
            "ticker": ticker,
            "name": raw.get("name") or raw.get("hts_kor_isnm") or ticker,
            "market": raw.get("market", ""),
            "cur_price": raw.get("cur_price") or raw.get("stck_prpr"),
            "chg_rate": raw.get("chg_rate") or raw.get("prdy_ctrt"),
            "per": raw.get("per"),
            "pbr": raw.get("pbr"),
            "foreign_net": raw.get("foreign_net") or raw.get("frgnr_ntby_qty"),
            "inst_net": raw.get("inst_net") or raw.get("orgn_ntby_qty"),
        }

    return await _api(_cached(f"stock_{ticker}", 60.0, _fetch))


async def _handle_api_watch_post(request: web.Request) -> web.Response:
    """POST /api/watch — action에 따라 manage_watch 또는 set_alert 호출.

    body 예시:
        {"action":"add","ticker":"NVDA","name":"NVIDIA"}
        {"action":"remove","ticker":"NVDA","alert_type":"watchlist"}
        {"action":"set_alert","log_type":"watch","ticker":"005930","name":"삼성전자","buy_price":60000}
    에러는 200 + {"error":"..."} 로 반환 (Alpine이 d.error로 감지).
    실제 상태 변경 발생 — 읽기 전용 경로 아님.
    캐시 무효화: watch/alerts 60s TTL 캐시 제거 (다음 fetch가 fresh 호출).
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=200)
    action = body.get("action", "").strip().lower()
    if action in ("add", "remove"):
        result = await execute_tool("manage_watch", body)
    elif action == "set_alert":
        result = await execute_tool("set_alert", body)
    else:
        return web.json_response({"error": f"unknown action: {action}"}, status=200)
    # 캐시 무효화 (watch + alerts — 다음 GET이 fresh 데이터 반환)
    _cache.pop("watch", None)
    _cache.pop("alerts", None)
    if _tool_err(result):
        return web.json_response(result, status=200)
    return web.json_response(result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 라우트 등록
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def register_home_routes(app: web.Application) -> None:
    app.router.add_get("/home", _handle_home)
    app.router.add_get("/api/regime", _handle_api_regime)
    app.router.add_get("/api/alerts", _handle_api_alerts)
    app.router.add_get("/api/portfolio", _handle_api_portfolio)
    app.router.add_get("/api/home", _handle_api_home)
    # P2 추가
    app.router.add_get("/api/watch", _handle_api_watch_get)
    app.router.add_get("/api/stock/{ticker}", _handle_api_stock_detail)
    app.router.add_post("/api/watch", _handle_api_watch_post)
