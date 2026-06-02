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

from kis_api import (
    load_json,
    load_stoploss,
    load_watchalert,
    load_dart_seen,
    load_events,
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
      const data = await this.api('/api/home');
      if (!data.error) {
        this.home = data;
        this.lastUpdated = new Date().toLocaleTimeString('ko-KR');
      }
    },

    setTab(t) {
      this.activeTab = t;
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
    + '\n'
    '    <!-- 포트폴리오 -->\n'
    '    <section x-show="activeTab===\'portfolio\'">\n'
    '      <div class="text-slate-400 text-center py-20">포트폴리오 (P2에서 구현)</div>\n'
    '    </section>\n'
    '\n'
    '    <!-- 워치·알림 -->\n'
    '    <section x-show="activeTab===\'watch\'">\n'
    '      <div class="text-slate-400 text-center py-20">워치·알림 (P2에서 구현)</div>\n'
    '    </section>\n'
    '\n'
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
    return await _api(execute_tool("get_alerts", {"brief": True}))


async def _handle_api_portfolio(request: web.Request) -> web.Response:
    # W2: 람다 팩토리 — 캐시 hit 시 코루틴 미생성으로 RuntimeWarning 방지
    return await _api(_cached("portfolio", 60.0, lambda: execute_tool("get_portfolio", {})))


async def _handle_api_home(request: web.Request) -> web.Response:
    # W2: 람다 팩토리 — 캐시 hit 시 코루틴 미생성으로 RuntimeWarning 방지
    return await _api(_cached("home", 60.0, lambda: build_home_payload()))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 라우트 등록
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def register_home_routes(app: web.Application) -> None:
    app.router.add_get("/home", _handle_home)
    app.router.add_get("/api/regime", _handle_api_regime)
    app.router.add_get("/api/alerts", _handle_api_alerts)
    app.router.add_get("/api/portfolio", _handle_api_portfolio)
    app.router.add_get("/api/home", _handle_api_home)
