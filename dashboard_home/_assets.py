"""dashboard_home/_assets.py — 템플릿/JS 자산 상수 (P2 박리).

11개 패널 상수 + _DASH_APP_JS + _HOME_SHELL 조립식.
이 파일은 완전히 순수 문자열 상수만 포함 — import 없음.
"""

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
    portHistory: null,
    portHistoryLoading: false,
    portChartPeriod: '3M',
    _portChart: null,
    _portSeries: null,
    _portResizeObs: null,
    _portChartRetry: false,

    /* market tab */
    market: null,
    marketMoverSeg: 'kr',
    marketStockQuery: '',
    marketStockResult: null,
    marketStockLoading: false,

    /* market: marketmap treemap */
    marketmap: {},
    marketmapMarket: 'kospi',
    marketmapLoading: false,
    _mmChart: null,

    /* P3b: report tab */
    report: null,
    reportSeg: 'kr',
    reportModal: null,
    reportModalList: null,
    reportModalLoading: false,

    /* P4: signal tab */
    signals: null,
    signalSeg: 'feed',

    /* P3b: record tab */
    record: null,
    recordSection: 'decisions',
    decisionsLimit: 20,
    decisionForm: { show: false, date: '', regime: '', memo: '' },
    recordToast: '',
    portSort: 'eval',
    portModal: null,
    portModalLoading: false,
    portModalCandlePeriod: '3M',
    _candleChart: null,
    _candleSeries: null,
    _volChart: null,
    _volSeries: null,
    _candleResizeObs: null,
    _candleChartRetry: false,

    /* P2: portfolio view toggle */
    portView: 'list',

    /* market: sector heatmap */
    sectorHeatmap: null,
    sectorHeatmapLoading: false,

    /* market: macro panel */
    macroPanel: null,
    macroPanelLoading: false,

    /* alpha screener (signal tab) */
    alphaSeg: 'change',
    alphaData: {},
    alphaLoading: false,

    /* supply panel (market tab) */
    supplySeg: 'foreign_rank',
    supplyData: {},
    supplyLoading: false,

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

    async loadPortfolioHistory() {
      this.portHistoryLoading = true;
      const data = await this.api('/api/portfolio_history');
      this.portHistoryLoading = false;
      if (!data.error) {
        this.portHistory = data;
        this.$nextTick(() => this._mountPortChart());
      }
    },

    _portChartData() {
      if (!this.portHistory || !this.portHistory.snapshots) return [];
      const snaps = this.portHistory.snapshots;
      const now = new Date();
      let cutoff = new Date(now);
      if (this.portChartPeriod === '1M') cutoff.setMonth(cutoff.getMonth() - 1);
      else if (this.portChartPeriod === '3M') cutoff.setMonth(cutoff.getMonth() - 3);
      else cutoff.setFullYear(cutoff.getFullYear() - 1);
      return snaps
        .filter(s => s.date && s.total_asset_krw > 0 && new Date(s.date) >= cutoff)
        .map(s => ({ time: s.date, value: s.total_asset_krw }));
    },

    _mountPortChart() {
      if (typeof LightweightCharts === 'undefined') return;
      const el = document.getElementById('port-chart-container');
      if (!el) return;
      const chartData = this._portChartData();
      // 빈 상태
      const emptyEl = document.getElementById('port-chart-empty');
      if (chartData.length < 2) {
        if (emptyEl) emptyEl.style.display = 'flex';
        el.style.display = 'none';
        return;
      }
      if (emptyEl) emptyEl.style.display = 'none';
      el.style.display = 'block';
      // 레이아웃 전(컨테이너 폭 0)이면 0-width 차트 생성 방지 → rAF 1회 재시도 후 return.
      // _portChartRetry 플래그로 무한루프 가드(1회만 재시도).
      if (el.clientWidth === 0) {
        if (!this._portChartRetry) {
          this._portChartRetry = true;
          requestAnimationFrame(() => this._mountPortChart());
        }
        return;
      }
      this._portChartRetry = false;
      // 이전 차트 제거
      if (this._portChart) {
        try { this._portChart.remove(); } catch(e) {}
        this._portChart = null;
        this._portSeries = null;
      }
      if (this._portResizeObs) { this._portResizeObs.disconnect(); this._portResizeObs = null; }
      const isMobile = window.innerWidth < 768;
      const h = isMobile ? 200 : 260;
      const chart = LightweightCharts.createChart(el, {
        width: el.clientWidth,
        height: h,
        layout: { background: { color: '#ffffff' }, textColor: '#94a3b8', fontSize: 11 },
        grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
        rightPriceScale: { borderColor: '#e2e8f0' },
        timeScale: { borderColor: '#e2e8f0', timeVisible: true, secondsVisible: false },
        handleScroll: !isMobile,
        handleScale: !isMobile,
      });
      const first = chartData[0].value;
      const last = chartData[chartData.length - 1].value;
      const isUp = last >= first;
      const lineColor = isUp ? '#16a34a' : '#dc2626';
      const topColor = isUp ? 'rgba(34,197,94,0.3)' : 'rgba(220,38,38,0.3)';
      const series = chart.addAreaSeries({
        lineColor,
        topColor,
        bottomColor: 'rgba(255,255,255,0)',
        lineWidth: 2,
        priceFormat: {
          type: 'custom',
          formatter: v => (v / 1e8).toFixed(1) + '억',
        },
      });
      series.setData(chartData);
      chart.timeScale().fitContent();
      this._portChart = chart;
      this._portSeries = series;
      // ResizeObserver
      const ro = new ResizeObserver(entries => {
        for (const entry of entries) {
          if (this._portChart) this._portChart.applyOptions({ width: entry.contentRect.width });
        }
      });
      ro.observe(el);
      this._portResizeObs = ro;
    },

    setPortChartPeriod(p) {
      this.portChartPeriod = p;
      this.$nextTick(() => {
        if (!this._portChart || !this._portSeries) { this._mountPortChart(); return; }
        const chartData = this._portChartData();
        if (chartData.length < 2) { this._mountPortChart(); return; }
        this._portSeries.setData(chartData);
        this._portChart.timeScale().fitContent();
      });
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
      this._destroyCandleChart();
      this.portModal = { ticker, loading: true };
      this.portModalLoading = true;
      this.portModalCandlePeriod = '3M';
      this.$nextTick(() => this.refreshIcons());
      const data = await this.api('/api/stock/' + ticker);
      this.portModal = data.error ? { ticker, error: data.error } : data;
      this.portModalLoading = false;
      /* 캔들 mount: 모달 DOM이 보인 뒤(x-if 렌더 + 레이아웃) mount.
         nextTick + rAF 한 번 더로 모달 폭 0-width 생성 방지(_mountCandleChart에도 가드 있음). */
      this.$nextTick(() => {
        this.refreshIcons();
        requestAnimationFrame(() => this._mountCandleChart());
      });
    },

    closeModal() {
      this._destroyCandleChart();
      this.portModal = null;
    },

    _destroyCandleChart() {
      if (this._candleResizeObs) { this._candleResizeObs.disconnect(); this._candleResizeObs = null; }
      if (this._candleChart) {
        try { this._candleChart.remove(); } catch(e) {}
        this._candleChart = null;
        this._candleSeries = null;
      }
      if (this._volChart) {
        try { this._volChart.remove(); } catch(e) {}
        this._volChart = null;
        this._volSeries = null;
      }
    },

    _candleChartData() {
      if (!this.portModal || !this.portModal.candles) return [];
      const now = new Date();
      let cutoff = new Date(now);
      if (this.portModalCandlePeriod === '1M') cutoff.setMonth(cutoff.getMonth() - 1);
      else if (this.portModalCandlePeriod === '3M') cutoff.setMonth(cutoff.getMonth() - 3);
      else cutoff.setMonth(cutoff.getMonth() - 6);
      return this.portModal.candles
        .filter(c => c.open > 0 && c.close > 0 && c.date)
        .filter(c => {
          const d = c.date.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');
          return new Date(d) >= cutoff;
        })
        .map(c => {
          const t = c.date.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');
          return { time: t, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume };
        });
    },

    _mountCandleChart() {
      if (typeof LightweightCharts === 'undefined') return;
      if (!this.portModal || !this.portModal.candles) return;
      const candleEl = document.getElementById('modal-candle-container');
      const volEl = document.getElementById('modal-vol-container');
      if (!candleEl) return;
      const chartData = this._candleChartData();
      if (chartData.length === 0) return;
      // 모달 레이아웃 전(컨테이너 폭 0)이면 0-width 생성 방지 → rAF 1회 재시도 후 return.
      // _candleChartRetry 플래그로 무한루프 가드(1회만 재시도).
      if (candleEl.clientWidth === 0) {
        if (!this._candleChartRetry) {
          this._candleChartRetry = true;
          requestAnimationFrame(() => this._mountCandleChart());
        }
        return;
      }
      this._candleChartRetry = false;
      this._destroyCandleChart();
      const isMobile = window.innerWidth < 768;
      const commonOpts = {
        layout: { background: { color: '#ffffff' }, textColor: '#94a3b8', fontSize: 11 },
        grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
        rightPriceScale: { borderColor: '#e2e8f0' },
        timeScale: { borderColor: '#e2e8f0', timeVisible: false, secondsVisible: false },
        handleScroll: !isMobile,
        handleScale: !isMobile,
      };
      const cChart = LightweightCharts.createChart(candleEl, {
        ...commonOpts,
        width: candleEl.clientWidth,
        height: isMobile ? 180 : 220,
      });
      const cSeries = cChart.addCandlestickSeries({
        upColor: '#16a34a',
        downColor: '#dc2626',
        borderUpColor: '#16a34a',
        borderDownColor: '#dc2626',
        wickUpColor: '#16a34a',
        wickDownColor: '#dc2626',
      });
      cSeries.setData(chartData.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
      cChart.timeScale().fitContent();
      this._candleChart = cChart;
      this._candleSeries = cSeries;
      // 거래량 히스토그램
      if (volEl) {
        const vChart = LightweightCharts.createChart(volEl, {
          ...commonOpts,
          width: volEl.clientWidth,
          height: 50,
          rightPriceScale: { visible: false },
          leftPriceScale: { visible: false },
        });
        const vSeries = vChart.addHistogramSeries({
          priceFormat: { type: 'volume' },
          priceScaleId: '',
        });
        vSeries.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0 } });
        vSeries.setData(chartData.map(c => ({
          time: c.time,
          value: c.volume,
          color: c.close >= c.open ? 'rgba(22,163,74,0.6)' : 'rgba(220,38,38,0.6)',
        })));
        vChart.timeScale().fitContent();
        this._volChart = vChart;
        this._volSeries = vSeries;
        // 두 차트 timeScale 동기화
        cChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
          if (range && this._volChart) this._volChart.timeScale().setVisibleLogicalRange(range);
        });
        vChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
          if (range && this._candleChart) this._candleChart.timeScale().setVisibleLogicalRange(range);
        });
      }
      // ResizeObserver: 기기 회전/뷰포트 변화 시 캔들+거래량 폭 동기화
      const cro = new ResizeObserver(entries => {
        for (const entry of entries) {
          const w = entry.contentRect.width;
          if (this._candleChart) this._candleChart.applyOptions({ width: w });
          if (this._volChart) this._volChart.applyOptions({ width: w });
        }
      });
      cro.observe(candleEl);
      this._candleResizeObs = cro;
    },

    setCandlePeriod(p) {
      this.portModalCandlePeriod = p;
      this.$nextTick(() => {
        if (!this._candleChart || !this._candleSeries) { this._mountCandleChart(); return; }
        const chartData = this._candleChartData();
        if (chartData.length === 0) return;
        this._candleSeries.setData(chartData.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
        this._candleChart.timeScale().fitContent();
        if (this._volSeries && this._volChart) {
          this._volSeries.setData(chartData.map(c => ({
            time: c.time,
            value: c.volume,
            color: c.close >= c.open ? 'rgba(22,163,74,0.6)' : 'rgba(220,38,38,0.6)',
          })));
          this._volChart.timeScale().fitContent();
        }
      });
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

    hmColor(v) {
      if (v == null) return 'bg-slate-100 text-slate-500';
      if (v >= 5)  return 'bg-green-700 text-white';
      if (v >= 3)  return 'bg-green-500 text-white';
      if (v >= 1)  return 'bg-green-400 text-white';
      if (v >= 0)  return 'bg-green-100 text-green-800';
      if (v >= -1) return 'bg-red-100 text-red-800';
      if (v >= -3) return 'bg-red-300 text-white';
      if (v >= -5) return 'bg-red-500 text-white';
      return 'bg-red-700 text-white';
    },

    hmPortItems() {
      if (!this.portfolio) return [];
      const krHoldings = (this.portfolio.kr && this.portfolio.kr.holdings) ? this.portfolio.kr.holdings : [];
      const usHoldings = (this.portfolio.us && this.portfolio.us.holdings) ? this.portfolio.us.holdings : [];
      const usdKrw = this.portfolio.usd_krw || 1400;
      const allItems = [];
      for (const h of krHoldings) {
        allItems.push({ ...h, eval_krw: h.eval_amt || 0, market: 'KR' });
      }
      for (const h of usHoldings) {
        allItems.push({ ...h, eval_krw: (h.eval_amt || 0) * usdKrw, market: 'US' });
      }
      const total = allItems.reduce((s, i) => s + i.eval_krw, 0);
      for (const item of allItems) {
        item.weight = total > 0 ? item.eval_krw / total * 100 : 0;
      }
      allItems.sort((a, b) => b.weight - a.weight);
      return allItems;
    },

    async loadSectorHeatmap() {
      /* SWR: keep stale data, never null during refresh */
      if (!this.sectorHeatmap) this.sectorHeatmapLoading = true;
      const data = await this.api('/api/sector_heatmap');
      this.sectorHeatmapLoading = false;
      if (!data.error) this.sectorHeatmap = data;
    },

    /* ── marketmap treemap ── */
    mmHeight() {
      return window.innerWidth >= 768 ? 480 : 320;
    },

    async loadMarketmap(market) {
      market = market || this.marketmapMarket;
      this.marketmapMarket = market;
      if (this.marketmap[market]) {
        this._renderMarketmap();
        this._bgRefreshMarketmap(market);
        return;
      }
      this.marketmapLoading = true;
      const data = await this.api('/api/marketmap?market=' + market);
      this.marketmapLoading = false;
      if (data && !data.error) {
        this.marketmap = Object.assign({}, this.marketmap, { [market]: data });
        this._renderMarketmap();
      }
    },

    async _bgRefreshMarketmap(market) {
      const data = await this.api('/api/marketmap?market=' + market);
      if (data && !data.error) {
        this.marketmap = Object.assign({}, this.marketmap, { [market]: data });
        this._renderMarketmap();
      }
    },

    mmColor(pct) {
      if (pct == null) return '#e2e8f0';
      const stops = [[-5,[185,28,28]],[-3,[239,68,68]],[-1,[252,165,165]],[0,[241,245,249]],[1,[134,239,172]],[3,[34,197,94]],[5,[21,128,61]]];
      let p = Math.max(-5, Math.min(5, pct));
      for (let i = 0; i < stops.length - 1; i++) {
        const a = stops[i][0], ca = stops[i][1], b = stops[i+1][0], cb = stops[i+1][1];
        if (p >= a && p <= b) {
          const t = (p - a) / (b - a);
          const c = ca.map((v, k) => Math.round(v + (cb[k] - v) * t));
          return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')';
        }
      }
      return '#f1f5f9';
    },
    mmEchartsData(raw) {
      if (!raw || !raw.data) return [];
      return raw.data.map(sector => {
        return {
          name: sector.name,
          value: [sector.value, sector.change_pct != null ? sector.change_pct : 0],
          change_pct: sector.change_pct,
          itemStyle: { color: this.mmColor(sector.change_pct) },
          children: (sector.children || []).map(child => {
            return {
              name: child.name,
              ticker: child.ticker,
              value: [child.value, child.change_pct != null ? child.change_pct : 0],
              change_pct: child.change_pct,
              itemStyle: { color: this.mmColor(child.change_pct) },
            };
          }),
        };
      });
    },

    _renderMarketmap() {
      const raw = this.marketmap[this.marketmapMarket];
      if (!raw || !raw.data || !raw.data.length) return;
      if (typeof echarts === 'undefined') {
        this._mmTries = (this._mmTries || 0) + 1;
        if (this._mmTries < 60) requestAnimationFrame(() => this._renderMarketmap());
        return;
      }
      const el = document.getElementById('marketmap-container');
      if (!el || el.offsetWidth === 0) {
        this._mmTries = (this._mmTries || 0) + 1;
        if (this._mmTries < 60) requestAnimationFrame(() => this._renderMarketmap());
        return;
      }
      this._mmTries = 0;
      el.style.height = this.mmHeight() + 'px';
      if (!this._mmChart) {
        this._mmChart = echarts.init(el, null, { renderer: 'svg' });
        const self = this;
        this._mmChart.on('click', function(p) {
          if (p.data && p.data.ticker) self.openStockModal(p.data.ticker);
        });
        if (window.ResizeObserver) {
          let _mmt;
          const ro = new ResizeObserver(() => {
            clearTimeout(_mmt);
            _mmt = setTimeout(() => {
              const e = document.getElementById('marketmap-container');
              if (self._mmChart && self._mmOpt && e && e.offsetWidth > 0 && e.offsetWidth !== self._mmLastW) { self._mmLastW = e.offsetWidth; self._mmChart.resize(); self._mmChart.setOption(self._mmOpt, true); }
            }, 120);
          });
          ro.observe(el);
          this._mmResizeObs = ro;
        }
      }
      const treeData = this.mmEchartsData(raw);
      const opt = {
        tooltip: {
          formatter: function(info) {
            const d = info.data;
            if (!d) return '';
            if (d.ticker) {
              const chgStr = d.change_pct != null ? ((d.change_pct >= 0 ? '+' : '') + d.change_pct.toFixed(2) + '%') : '-';
              const cap = Array.isArray(d.value) ? d.value[0] : d.value;
              const capStr = cap >= 10000 ? (cap / 10000).toFixed(1) + '조' : (cap.toFixed(0) + '억');
              return d.name + ' (' + d.ticker + ')<br/>' + chgStr + ' | 시총 ' + capStr;
            }
            return d.name || info.name;
          }
        },
        series: [{
          type: 'treemap',
          roam: false,
          nodeClick: 'zoomToNode',
          breadcrumb: { show: true, height: 28 },
          label: {
            show: true,
            formatter: function(p) {
              const d = p.data;
              if (!d || !d.ticker) return '';
              const chg = d.change_pct;
              const chgStr = chg != null ? ((chg >= 0 ? '+' : '') + chg.toFixed(2) + '%') : '';
              const rect = p.value;
              const area = Array.isArray(rect) ? rect[0] : rect;
              if (area > 30000) return d.name + '\n' + chgStr;
              return chgStr;
            },
            fontSize: 11,
            color: '#1e293b',
            overflow: 'truncate',
          },
          upperLabel: {
            show: true,
            height: 24,
            fontSize: 12,
            fontWeight: 'bold',
            color: '#1e293b',
            backgroundColor: 'rgba(255,255,255,0.7)',
            formatter: function(p) {
              const d = p.data;
              if (!d || d.ticker) return '';
              const chg = d.change_pct;
              const chgStr = chg != null ? (' ' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%') : '';
              return (d.name || '') + chgStr;
            }
          },
          levels: [
            { itemStyle: { borderWidth: 2, borderColor: '#e2e8f0', gapWidth: 2 } },
            { itemStyle: { borderWidth: 1, borderColor: '#cbd5e1', gapWidth: 1 } }
          ],
          data: treeData,
        }],
      };
      this._mmOpt = opt;
      this._mmChart.resize();
      this._mmChart.setOption(opt, true);
      this._mmLastW = el.offsetWidth;
    },

    async loadMacroPanel() {
      /* SWR: keep stale data, never null during refresh */
      if (!this.macroPanel) this.macroPanelLoading = true;
      const data = await this.api('/api/macro_panel');
      this.macroPanelLoading = false;
      if (!data.error) this.macroPanel = data;
    },

    setTab(t) {
      this.activeTab = t;
      if (t === 'portfolio') {
        this.loadPortfolio();
        /* 차트 A mount 트리거: 패널이 x-show로 보이게 된 뒤 mount.
           portHistory가 이미 있으면 즉시 재mount(이전 차트 remove 후 재생성 → 누수 없음),
           없으면 loadPortfolioHistory()가 fetch 후 mount.
           x-show 레이아웃 적용을 기다리려고 nextTick + rAF 한 번 더. */
        if (this.portHistory) {
          this.$nextTick(() => requestAnimationFrame(() => this._mountPortChart()));
        } else {
          this.loadPortfolioHistory();
        }
      }
      if (t === 'watch') this.loadWatch();
      if (t === 'signal') { this.loadSignal(); this.loadAlpha(this.alphaSeg); }
      if (t === 'report') this.loadReport();
      if (t === 'record') this.loadRecord();
      if (t === 'market') { this.loadMarket(); this.loadSectorHeatmap(); this.loadMacroPanel(); this.loadSupply(this.supplySeg); this.$nextTick(() => this.loadMarketmap()); }
      if (t === 'us') { this.loadUsCandidates(); this.loadUsScan(); }
      this.$nextTick(() => this.refreshIcons());
    },

    /* ── signal tab ── */
    async loadSignal() {
      const data = await this.api('/api/signals');
      if (!data.error) this.signals = data;
    },

    signalKindIcon(kind) {
      if (kind === 'supply_drain') return '🔵';
      if (kind === 'momentum_exit') return '🔴';
      return '⚡';
    },

    signalKindLabel(kind) {
      if (kind === 'supply_drain') return '수급이탈';
      if (kind === 'momentum_exit') return '모멘텀이탈';
      return '이상급등';
    },

    signalKindClass(kind) {
      if (kind === 'supply_drain') return 'bg-blue-100 text-blue-700';
      if (kind === 'momentum_exit') return 'bg-red-100 text-red-700';
      return 'bg-amber-100 text-amber-700';
    },

    dDayLabel(dday) {
      if (dday === 0) return 'D-day';
      if (dday < 0) return 'D' + dday;
      return 'D-' + dday;
    },

    dDayClass(dday) {
      if (dday === 0) return 'text-red-600 font-bold';
      if (dday <= 3) return 'text-orange-500 font-semibold';
      if (dday <= 7) return 'text-amber-600';
      return 'text-slate-500';
    },

    /* ── alpha screener (signal tab sub) ── */
    async loadAlpha(preset) {
      this.alphaSeg = preset;
      const cached = this.alphaData[preset];
      if (cached) {
        /* SWR: stale 즉시 반환 후 bg refresh */
        this._bgRefreshAlpha(preset);
        return;
      }
      this.alphaLoading = true;
      const data = await this.api('/api/alpha?preset=' + preset);
      this.alphaLoading = false;
      if (!data.error) {
        this.alphaData = Object.assign({}, this.alphaData, { [preset]: data });
      }
    },
    async _bgRefreshAlpha(preset) {
      const data = await this.api('/api/alpha?preset=' + preset);
      if (!data.error) {
        this.alphaData = Object.assign({}, this.alphaData, { [preset]: data });
      }
    },

    /* ── supply panel (market tab sub) ── */
    async loadSupply(mode) {
      this.supplySeg = mode;
      const cached = this.supplyData[mode];
      if (cached) {
        this._bgRefreshSupply(mode);
        return;
      }
      this.supplyLoading = true;
      const data = await this.api('/api/supply?mode=' + mode);
      this.supplyLoading = false;
      if (!data.error) {
        this.supplyData = Object.assign({}, this.supplyData, { [mode]: data });
      }
    },
    async _bgRefreshSupply(mode) {
      const data = await this.api('/api/supply?mode=' + mode);
      if (!data.error) {
        this.supplyData = Object.assign({}, this.supplyData, { [mode]: data });
      }
    },

    /* ── report tab ── */
    async loadReport() {
      if (this.report) return;
      const data = await this.api('/api/reports');
      if (!data.error) this.report = data;
    },

    async openReportModal(ticker) {
      this.reportModal = { ticker, loading: true };
      this.reportModalList = null;
      this.reportModalLoading = true;
      this.$nextTick(() => this.refreshIcons());
      const data = await this.api('/api/reports/' + ticker);
      this.reportModalLoading = false;
      if (data.error) {
        this.reportModal = { ticker, error: data.error };
      } else {
        this.reportModal = { ticker };
        this.reportModalList = Array.isArray(data) ? data : (data.list || []);
      }
      this.$nextTick(() => this.refreshIcons());
    },

    closeReportModal() {
      this.reportModal = null;
      this.reportModalList = null;
    },

    pdfUrl(ticker, basename) {
      if (!basename) return '';
      return '/dash/pdf/' + encodeURIComponent(ticker) + '/' + encodeURIComponent(basename);
    },

    /* ── record tab ── */
    async loadRecord() {
      if (this.record) return;
      const [decisions, trades, todo] = await Promise.all([
        this.api('/api/decisions'),
        this.api('/api/trades'),
        this.api('/api/invest_todo'),
      ]);
      this.record = {
        decisions: decisions.error ? [] : (decisions.items || []),
        trades: trades.error ? {} : trades,
        todo: todo.error ? '' : (todo.text || ''),
      };
    },

    regimeColor(regime) {
      if (!regime) return 'bg-slate-100 text-slate-600';
      const r = regime.toLowerCase();
      if (r.includes('공격') || r === 'offensive') return 'bg-green-100 text-green-700';
      if (r.includes('위기') || r === 'crisis') return 'bg-red-100 text-red-700';
      return 'bg-amber-100 text-amber-700';
    },

    async submitDecision() {
      const f = this.decisionForm;
      if (!f.regime) { this.showRecordToast('레짐을 선택하세요'); return; }
      const body = JSON.stringify({
        log_type: 'decision',
        date: f.date || new Date().toISOString().slice(0,10),
        regime: f.regime,
        notes: f.memo,
      });
      const r = await fetch('/api/decisions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
      const d = await r.json();
      if (d.error) { this.showRecordToast('오류: ' + d.error); return; }
      this.showRecordToast(d.message || '저장됨');
      this.decisionForm = { show: false, date: '', regime: '', memo: '' };
      this.record = null;
      await this.loadRecord();
    },

    showRecordToast(msg) {
      this.recordToast = msg;
      setTimeout(() => { this.recordToast = ''; }, 3000);
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

    /* ── market tab ── */
    async loadMarket() {
      /* stale-while-revalidate: 데이터 이미 있으면 null로 비우지 않고
         백그라운드로 fetch 후 도착 시 교체. 탭 최초 진입 시에만 로딩 표시. */
      const data = await this.api('/api/market');
      if (!data.error) this.market = data;
    },

    chgClass(v) {
      if (v == null || isNaN(Number(v))) return 'text-slate-500';
      return Number(v) > 0 ? 'text-green-600' : (Number(v) < 0 ? 'text-red-500' : 'text-slate-500');
    },

    chgStr(v) {
      if (v == null || isNaN(Number(v))) return '-';
      const n = Number(v);
      return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
    },

    async searchMarketStock() {
      const q = this.marketStockQuery.trim().toUpperCase();
      if (!q) return;
      this.marketStockLoading = true;
      this.marketStockResult = null;
      const data = await this.api('/api/stock/' + encodeURIComponent(q));
      this.marketStockLoading = false;
      this.marketStockResult = data;
      this.$nextTick(() => this.refreshIcons());
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
    },

    /* ── US 애널리스트 탭 ── */
    usSeg: 'candidates',
    usCandidates: null,
    usScan: null,
    usAnalysts: null,
    usAnalystsLoading: false,
    usCandidatesMinUpside: 20,
    usCandidatesTierSOnly: false,
    usModal: null,
    usModalRatings: null,
    usModalConsensus: null,
    usModalResearch: null,

    setUsSeg(s) {
      this.usSeg = s;
      if (s === 'analysts' && !this.usAnalysts) this.loadUsAnalysts();
      this.$nextTick(() => this.refreshIcons());
    },

    async loadUsCandidates() {
      const data = await this.api('/api/us/candidates');
      if (!data.error) this.usCandidates = data;
    },

    async loadUsScan() {
      const data = await this.api('/api/us/scan');
      if (!data.error) this.usScan = data;
    },

    async loadUsAnalysts() {
      this.usAnalystsLoading = true;
      const data = await this.api('/api/us/analysts');
      this.usAnalystsLoading = false;
      if (!data.error) this.usAnalysts = data;
    },

    filteredCandidates() {
      if (!this.usCandidates || !this.usCandidates.candidates) return [];
      let list = this.usCandidates.candidates.filter(c => {
        if (c.upside_pct < this.usCandidatesMinUpside) return false;
        if (this.usCandidatesTierSOnly && c.tier_s_count < 1) return false;
        return true;
      });
      return list.slice().sort((a, b) => b.upside_pct - a.upside_pct);
    },

    hmColorUs(upside) {
      if (upside >= 80) return 'bg-emerald-100 text-emerald-700';
      if (upside >= 50) return 'bg-green-100 text-green-700';
      if (upside >= 30) return 'bg-lime-100 text-lime-700';
      return 'bg-slate-100 text-slate-600';
    },

    usSuccessColor(rate) {
      const r = Number(rate);
      if (r >= 60) return 'text-emerald-600 font-semibold';
      if (r >= 45) return 'text-green-600';
      if (r >= 30) return 'text-amber-600';
      return 'text-slate-500';
    },

    usActionBadge(action) {
      if (!action) return 'bg-slate-100 text-slate-600';
      const a = action.toLowerCase();
      if (a === 'upgrades') return 'bg-green-100 text-green-700';
      if (a === 'downgrades') return 'bg-red-100 text-red-700';
      if (a === 'initiates') return 'bg-blue-100 text-blue-700';
      return 'bg-slate-100 text-slate-600';
    },

    async openUsModal(ticker) {
      this.usModal = { ticker, loading: true };
      this.usModalRatings = null;
      this.usModalConsensus = null;
      this.usModalResearch = null;
      this.$nextTick(() => this.refreshIcons());
      const [r, c, res] = await Promise.all([
        this.api('/api/us/ratings?ticker=' + encodeURIComponent(ticker)),
        this.api('/api/us/consensus?ticker=' + encodeURIComponent(ticker)),
        this.api('/api/us/analyst_research?ticker=' + encodeURIComponent(ticker)),
      ]);
      this.usModal = { ticker, loading: false };
      this.usModalRatings = r.error ? null : r;
      this.usModalConsensus = c.error ? null : c;
      this.usModalResearch = res;
      this.$nextTick(() => this.refreshIcons());
    },

    closeUsModal() {
      this.usModal = null;
      this.usModalRatings = null;
      this.usModalConsensus = null;
      this.usModalResearch = null;
    },

    usdCompact(n) {
      if (n == null || isNaN(Number(n))) return '-';
      const v = Number(n);
      if (v >= 1e12) return '$' + (v / 1e12).toFixed(1) + 'T';
      if (v >= 1e9) return '$' + (v / 1e9).toFixed(1) + 'B';
      if (v >= 1e6) return '$' + (v / 1e6).toFixed(1) + 'M';
      return '$' + v.toLocaleString('en-US', {maximumFractionDigits: 0});
    }
  };
}
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시세 탭 패널 HTML
# 지수 4카드 / 급등락(KR+US) / 거래량 / 종목 직접 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_MARKET_PANEL = (
    '    <!-- 시세 탭 패널 -->\n'
    '    <section x-show="activeTab===\'market\'" x-cloak>\n'
    '\n'
    '      <!-- 로딩 스켈레톤 (최초 1회만) -->\n'
    '      <template x-if="!market">\n'
    '        <div class="animate-pulse">\n'
    '          <!-- 지수 4카드 -->\n'
    '          <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">\n'
    '            <template x-for="i in [1,2,3,4]" :key="i">\n'
    '              <div class="bg-white rounded-xl border border-slate-100 p-4">\n'
    '                <div class="h-2.5 w-14 bg-slate-200 rounded mb-2"></div>\n'
    '                <div class="h-5 w-20 bg-slate-200 rounded mb-1"></div>\n'
    '                <div class="h-3 w-12 bg-slate-200 rounded"></div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '          <!-- 종목 조회 박스 -->\n'
    '          <div class="bg-white rounded-xl border border-slate-100 p-5 mb-6">\n'
    '            <div class="h-3 w-24 bg-slate-200 rounded mb-3"></div>\n'
    '            <div class="h-9 w-full bg-slate-200 rounded-lg"></div>\n'
    '          </div>\n'
    '          <!-- 급등락 리스트 -->\n'
    '          <div class="bg-white rounded-xl border border-slate-100 p-5">\n'
    '            <div class="flex gap-2 mb-4">\n'
    '              <template x-for="p in [1,2,3]" :key="p">\n'
    '                <div class="h-7 w-16 bg-slate-200 rounded-full"></div>\n'
    '              </template>\n'
    '            </div>\n'
    '            <template x-for="r in [1,2,3,4,5]" :key="r">\n'
    '              <div class="flex items-center gap-3 py-2.5 border-b border-slate-50 last:border-0">\n'
    '                <div class="flex-1">\n'
    '                  <div class="h-3 w-32 bg-slate-200 rounded mb-1"></div>\n'
    '                  <div class="h-3 w-20 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '                <div class="h-4 w-14 bg-slate-200 rounded ml-auto"></div>\n'
    '                <div class="h-4 w-12 bg-slate-200 rounded"></div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '      <template x-if="market">\n'
    '        <div>\n'
    '\n'
    '          <!-- 지수 4카드 -->\n'
    '          <template x-if="market.indices && market.indices.length">\n'
    '            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">\n'
    '              <template x-for="idx in market.indices" :key="idx.name">\n'
    '                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                  <div class="flex items-center justify-between mb-1">\n'
    '                    <span class="text-xs font-semibold text-slate-500 uppercase tracking-wide" x-text="idx.name"></span>\n'
    '                    <span class="text-xs px-1.5 py-0.5 rounded"\n'
    '                          :class="idx.market===\'US\' ? \'bg-blue-50 text-blue-500\' : \'bg-slate-100 text-slate-500\'"\n'
    '                          x-text="idx.market"></span>\n'
    '                  </div>\n'
    '                  <div class="text-lg font-bold text-slate-800"\n'
    '                       x-text="idx.price != null ? idx.price.toLocaleString(\'ko-KR\', {maximumFractionDigits: 2}) : \'-\'"></div>\n'
    '                  <div :class="chgClass(idx.change_pct)" class="text-sm font-semibold mt-0.5"\n'
    '                       x-text="chgStr(idx.change_pct)"></div>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- KR 섹터 히트맵 -->\n'
    '          <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-6">\n'
    '            <div class="flex items-center gap-3 mb-3">\n'
    '              <h2 class="text-sm font-semibold text-slate-700">KR 섹터 동향</h2>\n'
    '              <template x-if="sectorHeatmap && sectorHeatmap.date">\n'
    '                <span class="text-xs text-slate-400" x-text="sectorHeatmap.date.slice(0,4)+\'.\'+sectorHeatmap.date.slice(4,6)+\'.\'+sectorHeatmap.date.slice(6)"></span>\n'
    '              </template>\n'
    '            </div>\n'
    '            <!-- 로딩 스켈레톤 -->\n'
    '            <template x-if="sectorHeatmapLoading && !sectorHeatmap">\n'
    '              <div class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-7 gap-1.5 animate-pulse">\n'
    '                <template x-for="i in [1,2,3,4,5,6,7,8,9,10,11,12,13,14]" :key="i">\n'
    '                  <div class="bg-slate-100 rounded-lg h-16"></div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '            <!-- 데이터 -->\n'
    '            <template x-if="sectorHeatmap && sectorHeatmap.sectors && sectorHeatmap.sectors.length">\n'
    '              <div class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-7 gap-1.5">\n'
    '                <template x-for="s in sectorHeatmap.sectors" :key="s.sector">\n'
    '                  <div class="rounded-lg p-2 text-center h-16 md:h-20 flex flex-col items-center justify-center cursor-pointer hover:brightness-110 hover:ring-2 hover:ring-white/60 transition-all"\n'
    '                       :class="hmColor(s.avg_chg)">\n'
    '                    <div class="text-xs font-semibold truncate w-full text-center leading-tight" x-text="s.sector"></div>\n'
    '                    <div class="text-sm font-bold mt-0.5" x-text="(s.avg_chg >= 0 ? \'+\' : \'\') + s.avg_chg.toFixed(2) + \'%\'"></div>\n'
    '                    <div class="text-xs opacity-80" x-text="s.n_stocks + \'종목\'"></div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '            <!-- 빈 상태 -->\n'
    '            <template x-if="!sectorHeatmapLoading && (!sectorHeatmap || !sectorHeatmap.sectors || !sectorHeatmap.sectors.length)">\n'
    '              <div class="text-slate-400 text-sm py-4 text-center">섹터 데이터 없음 (장 마감 후 반영)</div>\n'
    '            </template>\n'
    '          </div>\n'
    '\n'
    '          <!-- 마켓맵 트리맵 -->\n'
    '          <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-6">\n'
    '            <!-- 헤더 -->\n'
    '            <div class="flex flex-wrap items-center gap-3 mb-3">\n'
    '              <h2 class="text-sm font-semibold text-slate-700">마켓맵</h2>\n'
    '              <template x-if="marketmap[marketmapMarket] && marketmap[marketmapMarket].as_of">\n'
    '                <span class="text-xs text-slate-400"\n'
    '                      x-text="marketmap[marketmapMarket] && marketmap[marketmapMarket].as_of ? marketmap[marketmapMarket].as_of.slice(0,4)+\'.\'+marketmap[marketmapMarket].as_of.slice(4,6)+\'.\'+marketmap[marketmapMarket].as_of.slice(6) : \'\'"></span>\n'
    '              </template>\n'
    '              <!-- KOSPI / KOSDAQ 토글 -->\n'
    '              <div class="flex gap-1 ml-auto">\n'
    '                <button @click="loadMarketmap(\'kospi\')"\n'
    '                        :class="marketmapMarket===\'kospi\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                        class="text-xs px-3 py-1.5 rounded-full transition-colors">KOSPI</button>\n'
    '                <button @click="loadMarketmap(\'kosdaq\')"\n'
    '                        :class="marketmapMarket===\'kosdaq\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                        class="text-xs px-3 py-1.5 rounded-full transition-colors">KOSDAQ</button>\n'
    '              </div>\n'
    '              <!-- 색 범례 (sm 이상에서만 표시) -->\n'
    '              <div class="hidden sm:flex items-center gap-1">\n'
    '                <span class="text-xs text-slate-400">-5%</span>\n'
    '                <div class="w-24 h-2 rounded-full" style="background:linear-gradient(to right,#b91c1c,#ef4444,#fca5a5,#f1f5f9,#86efac,#22c55e,#15803d)"></div>\n'
    '                <span class="text-xs text-slate-400">+5%</span>\n'
    '              </div>\n'
    '            </div>\n'
    '            <!-- 로딩 스켈레톤 -->\n'
    '            <template x-if="marketmapLoading && !marketmap[marketmapMarket]">\n'
    '              <div class="animate-pulse bg-slate-100 rounded-lg" :style="`height:${mmHeight()}px`"></div>\n'
    '            </template>\n'
    '            <!-- 트리맵 컨테이너 -->\n'
    '            <div id="marketmap-container"\n'
    '                 x-show="marketmap[marketmapMarket] && marketmap[marketmapMarket].data && marketmap[marketmapMarket].data.length"\n'
    '                 :style="`height:${mmHeight()}px`"\n'
    '                 class="w-full"></div>\n'
    '            <!-- 빈 상태 -->\n'
    '            <template x-if="!marketmapLoading && (!marketmap[marketmapMarket] || !marketmap[marketmapMarket].data || !marketmap[marketmapMarket].data.length)">\n'
    '              <div class="text-slate-400 text-sm py-8 text-center">마켓맵 데이터 없음 (장 마감 후 반영)</div>\n'
    '            </template>\n'
    '            <!-- 풋노트 -->\n'
    '            <template x-if="marketmap[marketmapMarket] && marketmap[marketmapMarket].as_of">\n'
    '              <div class="text-xs text-slate-400 mt-2 text-right"\n'
    '                   x-text="\'시총상위 \' + (marketmap[marketmapMarket].shown_stocks||0) + \'종목 표시 / 전체 \' + (marketmap[marketmapMarket].total_stocks||0) + \'종목\'"></div>\n'
    '            </template>\n'
    '          </div>\n'
    '\n'
    '          <!-- 종목 시세 직접 조회 -->\n'
    '          <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-6">\n'
    '            <h2 class="text-sm font-semibold text-slate-700 mb-3">종목 시세 조회</h2>\n'
    '            <div class="flex gap-2">\n'
    '              <input x-model="marketStockQuery"\n'
    '                     @keyup.enter="searchMarketStock()"\n'
    '                     placeholder="티커 입력 (예: 005930 / NVDA)"\n'
    '                     class="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">\n'
    '              <button @click="searchMarketStock()"\n'
    '                      class="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors">\n'
    '                조회\n'
    '              </button>\n'
    '            </div>\n'
    '            <!-- 조회 결과 -->\n'
    '            <template x-if="marketStockLoading">\n'
    '              <div class="animate-pulse mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">\n'
    '                <template x-for="s in [1,2,3,4]" :key="s">\n'
    '                  <div class="bg-slate-100 rounded-lg p-3">\n'
    '                    <div class="h-2.5 w-16 bg-slate-200 rounded mb-2"></div>\n'
    '                    <div class="h-5 w-20 bg-slate-200 rounded"></div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '            <template x-if="!marketStockLoading && marketStockResult && marketStockResult.error">\n'
    '              <div class="text-red-500 text-sm mt-3" x-text="\'오류: \' + marketStockResult.error"></div>\n'
    '            </template>\n'
    '            <template x-if="!marketStockLoading && marketStockResult && !marketStockResult.error && marketStockResult.ticker">\n'
    '              <div class="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">\n'
    '                <div class="col-span-2 md:col-span-4 flex items-baseline gap-2 mb-1">\n'
    '                  <span class="text-base font-bold text-slate-800" x-text="marketStockResult.name || marketStockResult.ticker"></span>\n'
    '                  <span class="text-xs text-slate-400" x-text="marketStockResult.ticker"></span>\n'
    '                  <span class="text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-500" x-text="marketStockResult.market || \'\'"></span>\n'
    '                </div>\n'
    '                <div class="bg-slate-50 rounded-lg p-3">\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">현재가</div>\n'
    '                  <div class="font-semibold text-slate-800"\n'
    '                       x-text="marketStockResult.cur_price != null ? (marketStockResult.market===\'US\' ? usd(marketStockResult.cur_price) : won(marketStockResult.cur_price)) : \'-\'"></div>\n'
    '                  <div :class="pnlClass(marketStockResult.chg_rate)" class="text-xs"\n'
    '                       x-text="marketStockResult.chg_rate != null ? chgStr(marketStockResult.chg_rate) : \'\'"></div>\n'
    '                </div>\n'
    '                <div class="bg-slate-50 rounded-lg p-3">\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">PER / PBR</div>\n'
    '                  <div class="font-semibold text-slate-800"\n'
    '                       x-text="(marketStockResult.per != null ? marketStockResult.per : \'-\') + \' / \' + (marketStockResult.pbr != null ? marketStockResult.pbr : \'-\')"></div>\n'
    '                </div>\n'
    '                <div class="bg-slate-50 rounded-lg p-3">\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">외인 순매수</div>\n'
    '                  <div :class="pnlClass(marketStockResult.foreign_net)" class="font-semibold"\n'
    '                       x-text="marketStockResult.foreign_net != null ? marketStockResult.foreign_net.toLocaleString(\'ko-KR\') : \'-\'"></div>\n'
    '                </div>\n'
    '                <div class="bg-slate-50 rounded-lg p-3">\n'
    '                  <div class="text-xs text-slate-400 mb-0.5">기관 순매수</div>\n'
    '                  <div :class="pnlClass(marketStockResult.inst_net)" class="font-semibold"\n'
    '                       x-text="marketStockResult.inst_net != null ? marketStockResult.inst_net.toLocaleString(\'ko-KR\') : \'-\'"></div>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '\n'
    '          <!-- 급등락 / 거래량 탭 -->\n'
    '          <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">\n'
    '            <div class="flex gap-2 mb-4">\n'
    '              <button @click="marketMoverSeg=\'kr\'"\n'
    '                :class="marketMoverSeg===\'kr\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">KR 등락</button>\n'
    '              <button @click="marketMoverSeg=\'us\'"\n'
    '                :class="marketMoverSeg===\'us\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">US 등락</button>\n'
    '              <button @click="marketMoverSeg=\'vol\'"\n'
    '                :class="marketMoverSeg===\'vol\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">거래량</button>\n'
    '              <button @click="marketMoverSeg=\'macro\'; loadMacroPanel()"\n'
    '                :class="marketMoverSeg===\'macro\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">매크로</button>\n'
    '              <button @click="marketMoverSeg=\'supply\'; loadSupply(supplySeg)"\n'
    '                :class="marketMoverSeg===\'supply\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">수급</button>\n'
    '            </div>\n'
    '\n'
    '            <!-- KR 등락 -->\n'
    '            <template x-if="marketMoverSeg===\'kr\'">\n'
    '              <div class="grid grid-cols-1 md:grid-cols-2 gap-6">\n'
    '                <!-- KR 상승 -->\n'
    '                <div>\n'
    '                  <div class="flex items-center gap-2 mb-2">\n'
    '                    <h3 class="text-xs font-semibold text-green-600 uppercase tracking-wider">KR 상승 TOP</h3>\n'
    '                    <template x-if="market.movers_kr_as_of">\n'
    '                      <span class="text-xs text-slate-400" x-text="market.movers_kr_as_of ? market.movers_kr_as_of.slice(0,4)+\'.\'+market.movers_kr_as_of.slice(4,6)+\'.\'+market.movers_kr_as_of.slice(6)+\' 종가 기준\' : \'\'"></span>\n'
    '                    </template>\n'
    '                  </div>\n'
    '                  <template x-if="!market.movers_kr_up || !market.movers_kr_up.length">\n'
    '                    <div class="text-slate-400 text-sm py-2">데이터 없음 (장 마감 시간 외)</div>\n'
    '                  </template>\n'
    '                  <template x-if="market.movers_kr_up && market.movers_kr_up.length">\n'
    '                    <table class="w-full text-sm">\n'
    '                      <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                        <th class="text-left py-1.5 font-medium">종목</th>\n'
    '                        <th class="text-right py-1.5 font-medium">현재가</th>\n'
    '                        <th class="text-right py-1.5 font-medium">등락</th>\n'
    '                      </tr></thead>\n'
    '                      <tbody>\n'
    '                        <template x-for="s in market.movers_kr_up" :key="s.ticker">\n'
    '                          <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                            <td class="py-1.5">\n'
    '                              <span class="font-medium text-slate-800 text-sm" x-text="s.name"></span>\n'
    '                              <span class="text-xs text-slate-400 ml-1" x-text="s.ticker"></span>\n'
    '                            </td>\n'
    '                            <td class="py-1.5 text-right text-slate-700 text-sm" x-text="s.price != null ? s.price.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                            <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.chg_pct)" x-text="chgStr(s.chg_pct)"></td>\n'
    '                          </tr>\n'
    '                        </template>\n'
    '                      </tbody>\n'
    '                    </table>\n'
    '                  </template>\n'
    '                </div>\n'
    '                <!-- KR 하락 -->\n'
    '                <div>\n'
    '                  <div class="flex items-center gap-2 mb-2">\n'
    '                    <h3 class="text-xs font-semibold text-red-500 uppercase tracking-wider">KR 하락 TOP</h3>\n'
    '                    <template x-if="market.movers_kr_as_of">\n'
    '                      <span class="text-xs text-slate-400" x-text="market.movers_kr_as_of ? market.movers_kr_as_of.slice(0,4)+\'.\'+market.movers_kr_as_of.slice(4,6)+\'.\'+market.movers_kr_as_of.slice(6)+\' 종가 기준\' : \'\'"></span>\n'
    '                    </template>\n'
    '                  </div>\n'
    '                  <template x-if="!market.movers_kr_down || !market.movers_kr_down.length">\n'
    '                    <div class="text-slate-400 text-sm py-2">데이터 없음 (장 마감 시간 외)</div>\n'
    '                  </template>\n'
    '                  <template x-if="market.movers_kr_down && market.movers_kr_down.length">\n'
    '                    <table class="w-full text-sm">\n'
    '                      <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                        <th class="text-left py-1.5 font-medium">종목</th>\n'
    '                        <th class="text-right py-1.5 font-medium">현재가</th>\n'
    '                        <th class="text-right py-1.5 font-medium">등락</th>\n'
    '                      </tr></thead>\n'
    '                      <tbody>\n'
    '                        <template x-for="s in market.movers_kr_down" :key="s.ticker">\n'
    '                          <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                            <td class="py-1.5">\n'
    '                              <span class="font-medium text-slate-800 text-sm" x-text="s.name"></span>\n'
    '                              <span class="text-xs text-slate-400 ml-1" x-text="s.ticker"></span>\n'
    '                            </td>\n'
    '                            <td class="py-1.5 text-right text-slate-700 text-sm" x-text="s.price != null ? s.price.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                            <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.chg_pct)" x-text="chgStr(s.chg_pct)"></td>\n'
    '                          </tr>\n'
    '                        </template>\n'
    '                      </tbody>\n'
    '                    </table>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- US 등락 -->\n'
    '            <template x-if="marketMoverSeg===\'us\'">\n'
    '              <div class="grid grid-cols-1 md:grid-cols-2 gap-6">\n'
    '                <!-- US 상승 -->\n'
    '                <div>\n'
    '                  <h3 class="text-xs font-semibold text-green-600 uppercase tracking-wider mb-2">US 상승 TOP (NAS)</h3>\n'
    '                  <template x-if="!market.movers_us_up || !market.movers_us_up.length">\n'
    '                    <div class="text-slate-400 text-sm py-2">데이터 없음 (미장 마감 시간 외)</div>\n'
    '                  </template>\n'
    '                  <template x-if="market.movers_us_up && market.movers_us_up.length">\n'
    '                    <table class="w-full text-sm">\n'
    '                      <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                        <th class="text-left py-1.5 font-medium">종목</th>\n'
    '                        <th class="text-right py-1.5 font-medium">가격</th>\n'
    '                        <th class="text-right py-1.5 font-medium">등락</th>\n'
    '                      </tr></thead>\n'
    '                      <tbody>\n'
    '                        <template x-for="s in market.movers_us_up" :key="s.ticker">\n'
    '                          <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                            <td class="py-1.5">\n'
    '                              <span class="font-medium text-slate-800 text-sm" x-text="s.name || s.ticker"></span>\n'
    '                              <span class="text-xs text-slate-400 ml-1" x-text="s.ticker"></span>\n'
    '                            </td>\n'
    '                            <td class="py-1.5 text-right text-slate-700 text-sm" x-text="s.price != null ? usd(s.price) : \'-\'"></td>\n'
    '                            <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.chg_pct)" x-text="chgStr(s.chg_pct)"></td>\n'
    '                          </tr>\n'
    '                        </template>\n'
    '                      </tbody>\n'
    '                    </table>\n'
    '                  </template>\n'
    '                </div>\n'
    '                <!-- US 하락 -->\n'
    '                <div>\n'
    '                  <h3 class="text-xs font-semibold text-red-500 uppercase tracking-wider mb-2">US 하락 TOP (NAS)</h3>\n'
    '                  <template x-if="!market.movers_us_down || !market.movers_us_down.length">\n'
    '                    <div class="text-slate-400 text-sm py-2">데이터 없음 (미장 마감 시간 외)</div>\n'
    '                  </template>\n'
    '                  <template x-if="market.movers_us_down && market.movers_us_down.length">\n'
    '                    <table class="w-full text-sm">\n'
    '                      <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                        <th class="text-left py-1.5 font-medium">종목</th>\n'
    '                        <th class="text-right py-1.5 font-medium">가격</th>\n'
    '                        <th class="text-right py-1.5 font-medium">등락</th>\n'
    '                      </tr></thead>\n'
    '                      <tbody>\n'
    '                        <template x-for="s in market.movers_us_down" :key="s.ticker">\n'
    '                          <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                            <td class="py-1.5">\n'
    '                              <span class="font-medium text-slate-800 text-sm" x-text="s.name || s.ticker"></span>\n'
    '                              <span class="text-xs text-slate-400 ml-1" x-text="s.ticker"></span>\n'
    '                            </td>\n'
    '                            <td class="py-1.5 text-right text-slate-700 text-sm" x-text="s.price != null ? usd(s.price) : \'-\'"></td>\n'
    '                            <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.chg_pct)" x-text="chgStr(s.chg_pct)"></td>\n'
    '                          </tr>\n'
    '                        </template>\n'
    '                      </tbody>\n'
    '                    </table>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '            <!-- 거래량 상위 -->\n'
    '            <template x-if="marketMoverSeg===\'vol\'">\n'
    '              <div>\n'
    '                <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">KR 체결강도/거래량 상위</h3>\n'
    '                <template x-if="!market.volume_top || !market.volume_top.length">\n'
    '                  <div class="text-slate-400 text-sm py-2">데이터 없음 (장 마감 시간 외)</div>\n'
    '                </template>\n'
    '                <template x-if="market.volume_top && market.volume_top.length">\n'
    '                  <table class="w-full text-sm">\n'
    '                    <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                      <th class="text-left py-1.5 font-medium">종목</th>\n'
    '                      <th class="text-right py-1.5 font-medium">현재가</th>\n'
    '                      <th class="text-right py-1.5 font-medium">등락</th>\n'
    '                      <th class="text-right py-1.5 font-medium">거래량</th>\n'
    '                    </tr></thead>\n'
    '                    <tbody>\n'
    '                      <template x-for="s in market.volume_top" :key="s.ticker">\n'
    '                        <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                          <td class="py-1.5">\n'
    '                            <span class="font-medium text-slate-800 text-sm" x-text="s.name"></span>\n'
    '                            <span class="text-xs text-slate-400 ml-1" x-text="s.ticker"></span>\n'
    '                          </td>\n'
    '                          <td class="py-1.5 text-right text-slate-700 text-sm" x-text="s.price != null ? s.price.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                          <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.chg_pct)" x-text="chgStr(s.chg_pct)"></td>\n'
    '                          <td class="py-1.5 text-right text-xs text-slate-500"\n'
    '                              x-text="s.volume != null ? Number(s.volume).toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                        </tr>\n'
    '                      </template>\n'
    '                    </tbody>\n'
    '                  </table>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '\n'
    '          </div><!-- /급등락·거래량 카드 -->\n'
    '\n'
    '          <!-- 매크로 패널 -->\n'
    '          <template x-if="marketMoverSeg===\'macro\'">\n'
    '            <div class="space-y-4">\n'
    '\n'
    '              <!-- 로딩 스켈레톤 -->\n'
    '              <template x-if="macroPanelLoading && !macroPanel">\n'
    '                <div class="space-y-4">\n'
    '                  <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 h-12 animate-pulse bg-slate-100"></div>\n'
    '                  <div class="grid grid-cols-2 md:grid-cols-4 gap-3">\n'
    '                    <template x-for="i in [1,2,3,4]" :key="i"><div class="bg-slate-100 animate-pulse rounded-xl h-16"></div></template>\n'
    '                  </div>\n'
    '                  <div class="grid grid-cols-1 md:grid-cols-2 gap-3">\n'
    '                    <div class="bg-slate-100 animate-pulse rounded-xl h-24"></div>\n'
    '                    <div class="bg-slate-100 animate-pulse rounded-xl h-24"></div>\n'
    '                  </div>\n'
    '                  <div class="bg-slate-100 animate-pulse rounded-xl h-32"></div>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 실데이터 -->\n'
    '              <template x-if="macroPanel">\n'
    '                <div class="space-y-4">\n'
    '\n'
    '                  <!-- A: 레짐 배너 -->\n'
    '                  <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4 flex items-center gap-3">\n'
    '                    <template x-if="macroPanel.regime">\n'
    '                      <div class="flex items-center gap-3 w-full">\n'
    '                        <span class="text-xs font-semibold px-2.5 py-1 rounded-full"\n'
    '                              :class="regimeBadgeClass(macroPanel.regime.color)"\n'
    '                              x-text="macroPanel.regime.label"></span>\n'
    '                        <span class="text-sm text-slate-600"\n'
    '                              x-text="macroPanel.regime.regime_en === \'offensive\' ? \'공격형\' : macroPanel.regime.regime_en === \'defensive\' ? \'수비형\' : macroPanel.regime.regime_en === \'crisis\' ? \'위기\' : macroPanel.regime.regime_en"></span>\n'
    '                        <template x-if="macroPanel.regime.days != null">\n'
    '                          <span class="text-xs text-slate-400" x-text="macroPanel.regime.days + \'일째\'"></span>\n'
    '                        </template>\n'
    '                      </div>\n'
    '                    </template>\n'
    '                    <template x-if="!macroPanel.regime">\n'
    '                      <span class="text-sm text-slate-400">레짐 데이터 없음</span>\n'
    '                    </template>\n'
    '                  </div>\n'
    '\n'
    '                  <!-- B: 핵심 지표 카드 -->\n'
    '                  <template x-if="macroPanel.indicators && macroPanel.indicators.length">\n'
    '                    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">\n'
    '                      <template x-for="ind in macroPanel.indicators" :key="ind.label">\n'
    '                        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                          <div class="text-xs text-slate-500 mb-1" x-text="ind.label"></div>\n'
    '                          <div class="text-lg font-bold text-slate-800"\n'
    '                               :class="ind.label===\'VIX\' && ind.value != null ? (Number(ind.value) >= 30 ? \'text-red-600\' : Number(ind.value) >= 20 ? \'text-amber-500\' : \'text-slate-800\') : \'text-slate-800\'"\n'
    '                               x-text="ind.value != null ? ind.value : \'-\'"></div>\n'
    '                          <div class="text-xs mt-1"\n'
    '                               :class="chgClass(ind.chg_pct != null ? ind.chg_pct : ind.chg)"\n'
    '                               x-text="(ind.chg_pct != null ? chgStr(ind.chg_pct) : (ind.chg != null ? (Number(ind.chg) > 0 ? \'+\' : \'\') + Number(ind.chg).toFixed(2) : \'-\'))"></div>\n'
    '                        </div>\n'
    '                      </template>\n'
    '                    </div>\n'
    '                  </template>\n'
    '\n'
    '                  <!-- C: 수익률 곡선 / 침체 시그널 -->\n'
    '                  <template x-if="macroPanel.curve || macroPanel.recession_signal">\n'
    '                    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">\n'
    '                      <!-- 수익률 곡선 -->\n'
    '                      <template x-if="macroPanel.curve">\n'
    '                        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                          <div class="flex items-center gap-2 mb-3">\n'
    '                            <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider">수익률 곡선</h3>\n'
    '                            <template x-if="macroPanel.curve.spread != null && macroPanel.curve.spread < 0">\n'
    '                              <span class="text-xs font-semibold bg-red-100 text-red-600 px-1.5 py-0.5 rounded">역전중</span>\n'
    '                            </template>\n'
    '                          </div>\n'
    '                          <div class="space-y-2">\n'
    '                            <div class="flex justify-between text-sm">\n'
    '                              <span class="text-slate-500">2Y</span>\n'
    '                              <span class="font-semibold text-slate-800" x-text="macroPanel.curve.y2 != null ? macroPanel.curve.y2.toFixed(2) + \'%\' : \'-\'"></span>\n'
    '                            </div>\n'
    '                            <div class="flex justify-between text-sm">\n'
    '                              <span class="text-slate-500">10Y</span>\n'
    '                              <span class="font-semibold text-slate-800" x-text="macroPanel.curve.y10 != null ? macroPanel.curve.y10.toFixed(2) + \'%\' : \'-\'"></span>\n'
    '                            </div>\n'
    '                            <div class="flex justify-between text-sm border-t border-slate-100 pt-2">\n'
    '                              <span class="text-slate-500">스프레드</span>\n'
    '                              <span class="font-semibold"\n'
    '                                    :class="macroPanel.curve.spread != null && macroPanel.curve.spread < 0 ? \'text-red-600\' : \'text-green-600\'"\n'
    '                                    x-text="macroPanel.curve.spread != null ? (macroPanel.curve.spread > 0 ? \'+\' : \'\') + macroPanel.curve.spread.toFixed(2) + \'%\' : \'-\'"></span>\n'
    '                            </div>\n'
    '                          </div>\n'
    '                        </div>\n'
    '                      </template>\n'
    '                      <!-- 침체 시그널 (Estrella-Mishkin 1998) -->\n'
    '                      <template x-if="macroPanel.recession_signal">\n'
    '                        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                          <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">침체 시그널 (Estrella-Mishkin)</h3>\n'
    '                          <div class="flex items-center gap-2 mb-2">\n'
    '                            <span class="text-lg font-bold"\n'
    '                                  :class="macroPanel.recession_signal.includes(\'역전\') ? \'text-red-600\' : macroPanel.recession_signal.includes(\'주의\') ? \'text-amber-500\' : \'text-green-600\'"\n'
    '                                  x-text="macroPanel.recession_signal"></span>\n'
    '                          </div>\n'
    '                          <div class="text-xs text-slate-400">10Y-2Y 스프레드 기반 선행지표</div>\n'
    '                        </div>\n'
    '                      </template>\n'
    '                      <!-- recession_prob (숫자 데이터 있을 때) -->\n'
    '                      <template x-if="macroPanel.recession_prob != null">\n'
    '                        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                          <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">침체확률 (12개월 선행)</h3>\n'
    '                          <div class="flex items-end gap-2 mb-1">\n'
    '                            <span class="text-3xl font-bold"\n'
    '                                  :class="macroPanel.recession_prob >= 40 ? \'text-red-600\' : macroPanel.recession_prob >= 20 ? \'text-amber-500\' : \'text-green-600\'"\n'
    '                                  x-text="macroPanel.recession_prob.toFixed(1) + \'%\'"></span>\n'
    '                          </div>\n'
    '                          <div class="text-xs text-slate-400">Estrella-Mishkin 1998</div>\n'
    '                        </div>\n'
    '                      </template>\n'
    '                    </div>\n'
    '                  </template>\n'
    '\n'
    '                  <!-- D: Polymarket Fed -->\n'
    '                  <template x-if="macroPanel.polymarket_fed && macroPanel.polymarket_fed.length">\n'
    '                    <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                      <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Polymarket — Fed</h3>\n'
    '                      <div class="space-y-2">\n'
    '                        <template x-for="m in macroPanel.polymarket_fed" :key="m.title">\n'
    '                          <div class="flex items-center justify-between gap-2 py-1.5 border-b border-slate-50">\n'
    '                            <div class="text-sm text-slate-700 flex-1 min-w-0 truncate" x-text="m.title"></div>\n'
    '                            <div class="flex items-center gap-2 flex-shrink-0">\n'
    '                              <span class="text-sm font-semibold"\n'
    '                                    :class="m.yes_pct >= 60 ? \'text-green-600\' : m.yes_pct <= 40 ? \'text-red-500\' : \'text-slate-700\'"\n'
    '                                    x-text="m.yes_pct + \'%\'"></span>\n'
    '                              <span class="text-xs text-slate-400" x-text="\'$\' + (m.volume_usd / 1e6).toFixed(1) + \'M\'"></span>\n'
    '                            </div>\n'
    '                          </div>\n'
    '                        </template>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '\n'
    '                  <!-- E: 섹터 로테이션 -->\n'
    '                  <template x-if="macroPanel.sector_rotation && macroPanel.sector_rotation.length">\n'
    '                    <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">\n'
    '                      <h3 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">섹터 로테이션 (외인+기관)</h3>\n'
    '                      <table class="w-full text-sm">\n'
    '                        <thead><tr class="text-xs text-slate-400 border-b border-slate-100">\n'
    '                          <th class="text-left py-1.5 font-medium">섹터</th>\n'
    '                          <th class="text-right py-1.5 font-medium">외인</th>\n'
    '                          <th class="text-right py-1.5 font-medium">기관</th>\n'
    '                          <th class="text-right py-1.5 font-medium">합산</th>\n'
    '                        </tr></thead>\n'
    '                        <tbody>\n'
    '                          <template x-for="s in macroPanel.sector_rotation" :key="s.sector">\n'
    '                            <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                              <td class="py-1.5 font-medium text-slate-800" x-text="s.sector"></td>\n'
    '                              <td class="py-1.5 text-right text-xs" :class="chgClass(s.foreign_net)" x-text="s.foreign_net != null ? Number(s.foreign_net).toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                              <td class="py-1.5 text-right text-xs" :class="chgClass(s.inst_net)" x-text="s.inst_net != null ? Number(s.inst_net).toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                              <td class="py-1.5 text-right text-sm font-semibold" :class="chgClass(s.combined)" x-text="s.combined != null ? Number(s.combined).toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                            </tr>\n'
    '                          </template>\n'
    '                        </tbody>\n'
    '                      </table>\n'
    '                    </div>\n'
    '                  </template>\n'
    '\n'
    '                  <!-- 오류 정보 -->\n'
    '                  <template x-if="macroPanel._errors && macroPanel._errors.length">\n'
    '                    <div class="text-xs text-slate-400 px-1">\n'
    '                      <span x-text="\'일부 소스 미수신: \' + macroPanel._errors.map(e => e.source).join(\', \')"></span>\n'
    '                    </div>\n'
    '                  </template>\n'
    '\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '            </div>\n'
    '          </template>\n'
    '          <!-- /매크로 패널 -->\n'
    '\n'
    '          <!-- ── 수급 패널 ── -->\n'
    '          <template x-if="marketMoverSeg===\'supply\'">\n'
    '            <div>\n'
    '              <!-- 수급유형 필터 pill (가로스크롤, teal-600 선택) -->\n'
    '              <div class="flex gap-2 mb-4 overflow-x-auto pb-1">\n'
    '                <button @click="loadSupply(\'foreign_rank\')"\n'
    '                  :class="supplySeg===\'foreign_rank\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">외인순매수TOP</button>\n'
    '                <button @click="loadSupply(\'combined_rank\')"\n'
    '                  :class="supplySeg===\'combined_rank\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">외인+기관합산</button>\n'
    '                <button @click="loadSupply(\'short_sale\')"\n'
    '                  :class="supplySeg===\'short_sale\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">공매도</button>\n'
    '                <button @click="loadSupply(\'credit\')"\n'
    '                  :class="supplySeg===\'credit\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">신용잔고</button>\n'
    '                <button @click="loadSupply(\'lending\')"\n'
    '                  :class="supplySeg===\'lending\' ? \'bg-teal-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">대차</button>\n'
    '              </div>\n'
    '\n'
    '              <!-- 로딩 스켈레톤 -->\n'
    '              <template x-if="supplyLoading && !supplyData[supplySeg]">\n'
    '                <div class="animate-pulse space-y-2">\n'
    '                  <template x-for="i in [1,2,3,4,5]" :key="i">\n'
    '                    <div class="bg-white rounded-xl border border-slate-100 p-4 h-14"></div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 외인순매수TOP (foreign_rank) -->\n'
    '              <template x-if="supplySeg===\'foreign_rank\' && supplyData[\'foreign_rank\']">\n'
    '                <div>\n'
    '                  <template x-if="supplyData[\'foreign_rank\'].error">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400"\n'
    '                      x-text="supplyData[\'foreign_rank\'].error"></div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'foreign_rank\'].error && supplyData[\'foreign_rank\'].items && supplyData[\'foreign_rank\'].items.length">\n'
    '                    <div>\n'
    '                      <div class="text-xs text-slate-400 mb-2"\n'
    '                        x-text="(supplyData[\'foreign_rank\'].as_of ? supplyData[\'foreign_rank\'].as_of.slice(0,4)+\'.\'+supplyData[\'foreign_rank\'].as_of.slice(4,6)+\'.\'+supplyData[\'foreign_rank\'].as_of.slice(6) : \'\') + \' 외인 순매수 상위\'"></div>\n'
    '                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">\n'
    '                        <table class="w-full text-sm">\n'
    '                          <thead><tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">#</th>\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">종목</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">순매수주</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">순매수금액</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">등락</th>\n'
    '                          </tr></thead>\n'
    '                          <tbody>\n'
    '                            <template x-for="(s, idx) in supplyData[\'foreign_rank\'].items" :key="idx">\n'
    '                              <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                                <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="idx+1"></td>\n'
    '                                <td class="px-4 py-2.5">\n'
    '                                  <div class="font-medium text-slate-800 text-sm" x-text="s.name"></div>\n'
    '                                  <div class="text-xs text-slate-400" x-text="s.ticker"></div>\n'
    '                                </td>\n'
    '                                <td class="px-4 py-2.5 text-right text-sm"\n'
    '                                  :class="s.foreign_net_qty >= 0 ? \'text-green-600\' : \'text-red-600\'"\n'
    '                                  x-text="s.foreign_net_qty != null ? s.foreign_net_qty.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-600 hidden sm:table-cell"\n'
    '                                  x-text="s.foreign_net_amt != null ? (s.foreign_net_amt/100000000).toFixed(0)+\'억\' : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-sm font-semibold"\n'
    '                                  :class="s.chg_pct >= 0 ? \'text-green-600\' : \'text-red-600\'"\n'
    '                                  x-text="s.chg_pct != null ? (s.chg_pct >= 0 ? \'+\' : \'\')+s.chg_pct.toFixed(1)+\'%\' : \'-\'"></td>\n'
    '                              </tr>\n'
    '                            </template>\n'
    '                          </tbody>\n'
    '                        </table>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'foreign_rank\'].error && (!supplyData[\'foreign_rank\'].items || !supplyData[\'foreign_rank\'].items.length)">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400">수급 데이터 없음 (장중 반영)\n'
    '                      <button @click="loadSupply(\'foreign_rank\')" class="ml-2 text-teal-600 underline text-xs">재시도</button>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 외인+기관합산 (combined_rank) -->\n'
    '              <template x-if="supplySeg===\'combined_rank\' && supplyData[\'combined_rank\']">\n'
    '                <div>\n'
    '                  <template x-if="supplyData[\'combined_rank\'].error">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400"\n'
    '                      x-text="supplyData[\'combined_rank\'].error"></div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'combined_rank\'].error && supplyData[\'combined_rank\'].items && supplyData[\'combined_rank\'].items.length">\n'
    '                    <div>\n'
    '                      <div class="text-xs text-slate-400 mb-2">외인+기관 합산 순매수 상위</div>\n'
    '                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">\n'
    '                        <table class="w-full text-sm">\n'
    '                          <thead><tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">#</th>\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">종목</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">외인+기관(주)</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">비율%</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">등락</th>\n'
    '                          </tr></thead>\n'
    '                          <tbody>\n'
    '                            <template x-for="(s, idx) in supplyData[\'combined_rank\'].items" :key="idx">\n'
    '                              <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                                <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="idx+1"></td>\n'
    '                                <td class="px-4 py-2.5">\n'
    '                                  <div class="font-medium text-slate-800 text-sm" x-text="s.name"></div>\n'
    '                                  <div class="text-xs text-slate-400" x-text="s.ticker"></div>\n'
    '                                </td>\n'
    '                                <td class="px-4 py-2.5 text-right text-sm"\n'
    '                                  :class="s.fi_total_net >= 0 ? \'text-green-600\' : \'text-red-600\'"\n'
    '                                  x-text="s.fi_total_net != null ? s.fi_total_net.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-500 hidden sm:table-cell"\n'
    '                                  x-text="s.fi_ratio_pct != null ? s.fi_ratio_pct.toFixed(2)+\'%\' : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-sm font-semibold"\n'
    '                                  :class="s.chg_pct >= 0 ? \'text-green-600\' : \'text-red-600\'"\n'
    '                                  x-text="s.chg_pct != null ? (s.chg_pct >= 0 ? \'+\' : \'\')+s.chg_pct.toFixed(1)+\'%\' : \'-\'"></td>\n'
    '                              </tr>\n'
    '                            </template>\n'
    '                          </tbody>\n'
    '                        </table>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'combined_rank\'].error && (!supplyData[\'combined_rank\'].items || !supplyData[\'combined_rank\'].items.length)">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400">수급 데이터 없음 (장중 반영)\n'
    '                      <button @click="loadSupply(\'combined_rank\')" class="ml-2 text-teal-600 underline text-xs">재시도</button>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 공매도 (short_sale) -->\n'
    '              <template x-if="supplySeg===\'short_sale\' && supplyData[\'short_sale\']">\n'
    '                <div>\n'
    '                  <template x-if="supplyData[\'short_sale\'].error">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400"\n'
    '                      x-text="supplyData[\'short_sale\'].error"></div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'short_sale\'].error && supplyData[\'short_sale\'].items && supplyData[\'short_sale\'].items.length">\n'
    '                    <div>\n'
    '                      <div class="text-xs text-slate-400 mb-2" x-text="(supplyData[\'short_sale\'].ticker || \'\') + \' 공매도 추이 (최근 \' + supplyData[\'short_sale\'].items.length + \'일)\'"></div>\n'
    '                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">\n'
    '                        <table class="w-full text-sm">\n'
    '                          <thead><tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">날짜</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">공매도량</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">비율%</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">종가</th>\n'
    '                          </tr></thead>\n'
    '                          <tbody>\n'
    '                            <template x-for="(s, idx) in supplyData[\'short_sale\'].items" :key="idx">\n'
    '                              <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                                <td class="px-4 py-2.5 text-xs text-slate-500"\n'
    '                                  x-text="s.date ? s.date.slice(0,4)+\'.\'+s.date.slice(4,6)+\'.\'+s.date.slice(6) : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-700"\n'
    '                                  x-text="s.short_vol != null ? s.short_vol.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs font-semibold"\n'
    '                                  :class="s.short_ratio >= 5 ? \'text-red-600\' : s.short_ratio >= 3 ? \'text-amber-600\' : \'text-slate-600\'"\n'
    '                                  x-text="s.short_ratio != null ? s.short_ratio.toFixed(2)+\'%\' : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-600 hidden sm:table-cell"\n'
    '                                  x-text="s.close != null ? s.close.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                              </tr>\n'
    '                            </template>\n'
    '                          </tbody>\n'
    '                        </table>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'short_sale\'].error && (!supplyData[\'short_sale\'].items || !supplyData[\'short_sale\'].items.length)">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400">공매도 데이터 없음\n'
    '                      <button @click="loadSupply(\'short_sale\')" class="ml-2 text-teal-600 underline text-xs">재시도</button>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 신용잔고 (credit) -->\n'
    '              <template x-if="supplySeg===\'credit\' && supplyData[\'credit\']">\n'
    '                <div>\n'
    '                  <template x-if="supplyData[\'credit\'].error">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400"\n'
    '                      x-text="supplyData[\'credit\'].error"></div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'credit\'].error && supplyData[\'credit\'].items && supplyData[\'credit\'].items.length">\n'
    '                    <div>\n'
    '                      <!-- 10% 초과 경고 배너 -->\n'
    '                      <template x-if="supplyData[\'credit\'].warning">\n'
    '                        <div class="mb-3 bg-red-50 border border-red-200 rounded-lg px-4 py-2 text-red-700 text-xs font-semibold"\n'
    '                          x-text="\'⚠️ 신용잔고 과열 경고: \' + supplyData[\'credit\'].warning"></div>\n'
    '                      </template>\n'
    '                      <div class="text-xs text-slate-400 mb-2" x-text="(supplyData[\'credit\'].ticker || \'\') + \' 신용잔고 추이\'"></div>\n'
    '                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">\n'
    '                        <table class="w-full text-sm">\n'
    '                          <thead><tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">날짜</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">신용잔고율%</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">신규</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">상환</th>\n'
    '                          </tr></thead>\n'
    '                          <tbody>\n'
    '                            <template x-for="(s, idx) in supplyData[\'credit\'].items" :key="idx">\n'
    '                              <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                                <td class="px-4 py-2.5 text-xs text-slate-500"\n'
    '                                  x-text="s.date ? s.date.slice(0,4)+\'.\'+s.date.slice(4,6)+\'.\'+s.date.slice(6) : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs font-semibold"\n'
    '                                  :class="s.credit_ratio >= 10 ? \'text-red-600\' : s.credit_ratio >= 5 ? \'text-amber-600\' : \'text-slate-600\'"\n'
    '                                  x-text="s.credit_ratio != null ? s.credit_ratio.toFixed(2)+\'%\' : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-600 hidden sm:table-cell"\n'
    '                                  x-text="s.credit_new != null ? s.credit_new.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-500 hidden sm:table-cell"\n'
    '                                  x-text="s.credit_repay != null ? s.credit_repay.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                              </tr>\n'
    '                            </template>\n'
    '                          </tbody>\n'
    '                        </table>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'credit\'].error && (!supplyData[\'credit\'].items || !supplyData[\'credit\'].items.length)">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400">신용잔고 데이터 없음\n'
    '                      <button @click="loadSupply(\'credit\')" class="ml-2 text-teal-600 underline text-xs">재시도</button>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '              <!-- 대차 (lending) -->\n'
    '              <template x-if="supplySeg===\'lending\' && supplyData[\'lending\']">\n'
    '                <div>\n'
    '                  <template x-if="supplyData[\'lending\'].error">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400"\n'
    '                      x-text="supplyData[\'lending\'].error"></div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'lending\'].error && supplyData[\'lending\'].items && supplyData[\'lending\'].items.length">\n'
    '                    <div>\n'
    '                      <div class="text-xs text-slate-400 mb-2" x-text="(supplyData[\'lending\'].ticker || \'\') + \' 대차잔고 추이\'"></div>\n'
    '                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">\n'
    '                        <table class="w-full text-sm">\n'
    '                          <thead><tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">\n'
    '                            <th class="text-left px-4 py-2.5 font-medium">날짜</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium">대차잔고(주)</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">전일대비</th>\n'
    '                            <th class="text-right px-4 py-2.5 font-medium hidden sm:table-cell">잔고금액(백만)</th>\n'
    '                          </tr></thead>\n'
    '                          <tbody>\n'
    '                            <template x-for="(s, idx) in supplyData[\'lending\'].items" :key="idx">\n'
    '                              <tr class="border-b border-slate-50 hover:bg-slate-50">\n'
    '                                <td class="px-4 py-2.5 text-xs text-slate-500"\n'
    '                                  x-text="s.date ? s.date.slice(0,4)+\'.\'+s.date.slice(4,6)+\'.\'+s.date.slice(6) : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-700"\n'
    '                                  x-text="s.loan_balance != null ? s.loan_balance.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs hidden sm:table-cell"\n'
    '                                  :class="s.change >= 0 ? \'text-red-500\' : \'text-green-500\'"\n'
    '                                  x-text="s.change != null ? (s.change >= 0 ? \'+\' : \'\')+s.change.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                                <td class="px-4 py-2.5 text-right text-xs text-slate-600 hidden sm:table-cell"\n'
    '                                  x-text="s.loan_balance_amt != null ? s.loan_balance_amt.toLocaleString(\'ko-KR\') : \'-\'"></td>\n'
    '                              </tr>\n'
    '                            </template>\n'
    '                          </tbody>\n'
    '                        </table>\n'
    '                      </div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                  <template x-if="!supplyData[\'lending\'].error && (!supplyData[\'lending\'].items || !supplyData[\'lending\'].items.length)">\n'
    '                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-6 text-center text-slate-400">대차 데이터 없음\n'
    '                      <button @click="loadSupply(\'lending\')" class="ml-2 text-teal-600 underline text-xs">재시도</button>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '\n'
    '            </div>\n'
    '          </template>\n'
    '          <!-- /수급 패널 -->\n'
    '\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '    </section>\n'
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 포트폴리오 패널 HTML (P2)
# 카드 클릭 → 종목 상세 모달 (GET /api/stock/{ticker})
# 정렬 pill: 평가금/수익률/손익금 — Alpine 클라이언트 정렬
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_PORTFOLIO_PANEL = (
    '    <!-- 포트폴리오 패널 -->\n'
    '    <section x-show="activeTab===\'portfolio\'" x-cloak>\n'
    '\n'
    '      <!-- 로딩 스켈레톤 (최초 1회만) -->\n'
    '      <template x-if="!portfolio">\n'
    '        <div class="animate-pulse">\n'
    '          <!-- 요약 바 셀 x4 -->\n'
    '          <div class="bg-white rounded-xl border border-slate-100 p-5 mb-5">\n'
    '            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">\n'
    '              <template x-for="i in [1,2,3,4]" :key="i">\n'
    '                <div>\n'
    '                  <div class="h-2.5 w-20 bg-slate-200 rounded mb-2"></div>\n'
    '                  <div class="h-6 w-28 bg-slate-200 rounded mb-1"></div>\n'
    '                  <div class="h-3 w-16 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </div>\n'
    '          <!-- 차트 박스 h-48 -->\n'
    '          <div class="bg-slate-100 rounded-xl h-48 mb-5"></div>\n'
    '          <!-- 종목 카드 grid x4 -->\n'
    '          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">\n'
    '            <template x-for="j in [1,2,3,4]" :key="j">\n'
    '              <div class="bg-white rounded-xl border border-slate-100 p-4">\n'
    '                <div class="flex items-start justify-between mb-3">\n'
    '                  <div>\n'
    '                    <div class="h-4 w-28 bg-slate-200 rounded mb-1"></div>\n'
    '                    <div class="h-3 w-16 bg-slate-200 rounded"></div>\n'
    '                  </div>\n'
    '                  <div class="h-4 w-14 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '                <div class="grid grid-cols-3 gap-2 mb-3">\n'
    '                  <template x-for="k in [1,2,3]" :key="k">\n'
    '                    <div>\n'
    '                      <div class="h-2.5 w-10 bg-slate-200 rounded mb-1"></div>\n'
    '                      <div class="h-3.5 w-16 bg-slate-200 rounded"></div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '                <div class="border-t border-slate-50 pt-2">\n'
    '                  <div class="h-3 w-full bg-slate-200 rounded mb-1"></div>\n'
    '                  <div class="h-3 w-3/4 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '        </div>\n'
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
    '          <!-- 차트 A: 자산 추이 -->\n'
    '          <div class="bg-white rounded-xl border border-slate-200 p-4 mb-5">\n'
    '            <div class="flex items-center justify-between mb-3">\n'
    '              <span class="text-sm font-semibold text-slate-700">자산 추이</span>\n'
    '              <div class="flex gap-1">\n'
    '                <template x-for="p in [\'1M\',\'3M\',\'1Y\']" :key="p">\n'
    '                  <button @click="setPortChartPeriod(p)"\n'
    '                    :class="portChartPeriod===p ? \'bg-blue-600 text-white\' : \'bg-slate-100 text-slate-600 hover:bg-slate-200\'"\n'
    '                    class="text-xs px-2.5 py-1 rounded font-medium transition-colors"\n'
    '                    x-text="p"></button>\n'
    '                </template>\n'
    '              </div>\n'
    '            </div>\n'
    '            <template x-if="portHistoryLoading && !portHistory">\n'
    '              <div class="bg-slate-100 rounded-lg animate-pulse h-48"></div>\n'
    '            </template>\n'
    '            <div id="port-chart-empty"\n'
    '                 style="display:none"\n'
    '                 class="h-48 flex flex-col items-center justify-center text-slate-400 text-sm gap-1">\n'
    '              <span>자산 스냅샷 없음</span>\n'
    '              <span class="text-xs text-slate-300">(매일 15:50 자동 수집)</span>\n'
    '            </div>\n'
    '            <div id="port-chart-container" style="display:none"></div>\n'
    '          </div>\n'
    '\n'
    '          <!-- 정렬 pill + 보기 토글 -->\n'
    '          <div class="flex items-center gap-2 mb-4 flex-wrap">\n'
    '            <button @click="portSort=\'eval\'"\n'
    '              :class="portSort===\'eval\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">평가금순</button>\n'
    '            <button @click="portSort=\'pnl_pct\'"\n'
    '              :class="portSort===\'pnl_pct\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">수익률순</button>\n'
    '            <button @click="portSort=\'pnl\'"\n'
    '              :class="portSort===\'pnl\' ? \'bg-blue-600 text-white\' : \'bg-white text-slate-600 border border-slate-200\'"\n'
    '              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">손익금순</button>\n'
    '            <div class="ml-auto flex gap-1">\n'
    '              <button @click="portView=\'list\'"\n'
    '                :class="portView===\'list\' ? \'bg-slate-700 text-white\' : \'bg-white text-slate-500 border border-slate-200\'"\n'
    '                class="p-1.5 rounded-lg transition-colors" title="리스트 보기">\n'
    '                <i data-lucide="layout-list" class="w-4 h-4"></i>\n'
    '              </button>\n'
    '              <button @click="portView=\'heatmap\'"\n'
    '                :class="portView===\'heatmap\' ? \'bg-slate-700 text-white\' : \'bg-white text-slate-500 border border-slate-200\'"\n'
    '                class="p-1.5 rounded-lg transition-colors" title="히트맵 보기">\n'
    '                <i data-lucide="grid-2x2" class="w-4 h-4"></i>\n'
    '              </button>\n'
    '            </div>\n'
    '          </div>\n'
    '\n'
    '          <!-- 히트맵 보기 -->\n'
    '          <template x-if="portView===\'heatmap\'">\n'
    '            <div>\n'
    '              <template x-if="hmPortItems().length === 0">\n'
    '                <div class="text-slate-400 text-center py-20">보유 종목이 없습니다</div>\n'
    '              </template>\n'
    '              <template x-if="hmPortItems().length > 0">\n'
    '                <div class="flex flex-wrap gap-1.5">\n'
    '                  <template x-for="item in hmPortItems()" :key="item.ticker">\n'
    '                    <div @click="openStockModal(item.ticker)"\n'
    '                         :style="\'flex-grow:\' + item.weight"\n'
    '                         class="min-w-[60px] md:min-w-[72px] h-14 md:h-20 rounded-lg flex flex-col items-center justify-center cursor-pointer hover:brightness-110 hover:ring-2 hover:ring-white/60 transition-all px-1"\n'
    '                         :class="hmColor(item.pnl_pct)">\n'
    '                      <div class="text-xs font-semibold truncate w-full text-center leading-tight" x-text="item.name && item.name.length <= 5 ? item.name : item.ticker"></div>\n'
    '                      <div class="text-sm font-bold" x-text="(item.pnl_pct >= 0 ? \'+\' : \'\') + (item.pnl_pct != null ? item.pnl_pct.toFixed(1) : \'-\') + \'%\'"></div>\n'
    '                      <div class="text-xs opacity-80 truncate w-full text-center" x-text="item.market===\'US\' ? (item.eval_amt != null ? \'$\' + Number(item.eval_amt).toLocaleString(\'en-US\', {maximumFractionDigits:0}) : \'-\') : (item.eval_amt != null ? Math.round(item.eval_amt/10000) + \'만\' : \'-\')"></div>\n'
    '                    </div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </template>\n'
    '\n'
    '          <!-- 리스트 보기 -->\n'
    '          <template x-if="portView===\'list\'">\n'
    '            <div>\n'
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
    '                      <div><div class="text-slate-400">현재가</div><div class="font-medium text-slate-700 flex items-center gap-1"><span x-text="won(h.cur_price)"></span><template x-if="h.price_stale"><span class="text-xs text-amber-500 font-normal">종가</span></template></div></div>\n'
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
    '            </div>\n'
    '          </template><!-- /portView list -->\n'
    '\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '      <!-- 종목 상세 모달 -->\n'
    '      <template x-if="portModal">\n'
    '        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="closeModal()">\n'
    '          <div class="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 p-6 relative max-h-[90vh] overflow-y-auto">\n'
    '            <button @click="closeModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-700">\n'
    '              <i data-lucide="x" class="w-5 h-5"></i>\n'
    '            </button>\n'
    '            <!-- 로딩 스켈레톤 -->\n'
    '            <template x-if="portModalLoading">\n'
    '              <div class="animate-pulse">\n'
    '                <div class="flex items-baseline gap-2 mb-3">\n'
    '                  <div class="h-5 w-32 bg-slate-200 rounded"></div>\n'
    '                  <div class="h-4 w-16 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '                <div class="h-7 w-24 bg-slate-200 rounded mb-4"></div>\n'
    '                <div class="bg-slate-100 rounded-lg h-40 mb-4"></div>\n'
    '                <div class="grid grid-cols-2 gap-3">\n'
    '                  <template x-for="n in [1,2,3,4,5,6]" :key="n">\n'
    '                    <div class="bg-slate-50 rounded-lg h-12"></div>\n'
    '                  </template>\n'
    '                </div>\n'
    '              </div>\n'
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
    '                <!-- 헤더: 종목명 + 현재가 -->\n'
    '                <div class="flex items-baseline gap-2 mb-3">\n'
    '                  <span class="text-lg font-bold text-slate-800" x-text="portModal.name || portModal.ticker"></span>\n'
    '                  <span class="text-xs text-slate-400" x-text="portModal.ticker"></span>\n'
    '                </div>\n'
    '                <div class="flex items-baseline gap-3 mb-4">\n'
    '                  <span class="text-xl font-bold text-slate-800" x-text="portModal.cur_price != null ? (portModal.market===\'US\' ? usd(portModal.cur_price) : won(portModal.cur_price)) : \'-\'"></span>\n'
    '                  <span :class="pnlClass(portModal.chg_rate)" class="text-sm font-semibold" x-text="portModal.chg_rate != null ? pct(portModal.chg_rate) : \'\'"></span>\n'
    '                </div>\n'
    '                <!-- 캔들 차트 B -->\n'
    '                <template x-if="portModal.candles && portModal.candles.length > 0">\n'
    '                  <div class="mb-3">\n'
    '                    <div id="modal-candle-container"></div>\n'
    '                    <div id="modal-vol-container" class="mt-1"></div>\n'
    '                    <div class="flex gap-1 mt-2">\n'
    '                      <template x-for="p in [\'1M\',\'3M\',\'6M\']" :key="p">\n'
    '                        <button @click="setCandlePeriod(p)"\n'
    '                          :class="portModalCandlePeriod===p ? \'bg-blue-600 text-white\' : \'bg-slate-100 text-slate-600 hover:bg-slate-200\'"\n'
    '                          class="text-xs px-2.5 py-1 rounded font-medium transition-colors"\n'
    '                          x-text="p"></button>\n'
    '                      </template>\n'
    '                    </div>\n'
    '                  </div>\n'
    '                </template>\n'
    '                <template x-if="portModal.candles && portModal.candles.length === 0">\n'
    '                  <div class="text-center text-slate-400 text-xs py-3 mb-3">\n'
    '                    <span x-text="portModal.market===\'US\' ? \'US 종목 캔들 미지원\' : \'캔들 데이터 없음\'"></span>\n'
    '                  </div>\n'
    '                </template>\n'
    '                <!-- 메타 그리드: PER/PBR/외인/기관 -->\n'
    '                <div class="grid grid-cols-2 gap-3 text-sm">\n'
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
    '      <!-- 로딩 스켈레톤 (최초 1회만, SWR) -->\n'
    '      <template x-if="!watch">\n'
    '        <div class="animate-pulse">\n'
    '          <!-- 헤더 행 -->\n'
    '          <div class="flex items-center justify-between mb-4">\n'
    '            <div class="h-5 w-32 bg-slate-200 rounded"></div>\n'
    '            <div class="h-7 w-16 bg-slate-200 rounded-lg"></div>\n'
    '          </div>\n'
    '          <!-- 섹션 카드 x2 -->\n'
    '          <template x-for="s in [1,2]" :key="s">\n'
    '            <div class="bg-white rounded-xl border border-slate-100 p-5 mb-4">\n'
    '              <div class="h-3 w-24 bg-slate-200 rounded mb-4"></div>\n'
    '              <template x-for="r in [1,2,3,4]" :key="r">\n'
    '                <div class="flex items-center gap-3 py-2.5 border-b border-slate-50 last:border-0">\n'
    '                  <div class="rounded-full h-8 w-8 bg-slate-200 shrink-0"></div>\n'
    '                  <div class="flex-1">\n'
    '                    <div class="h-3 w-32 bg-slate-200 rounded mb-1"></div>\n'
    '                    <div class="h-3 w-20 bg-slate-200 rounded"></div>\n'
    '                  </div>\n'
    '                  <div class="ml-auto h-4 w-14 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </template>\n'
    '        </div>\n'
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
    '                        <td class="text-right py-2 pr-3 text-slate-700"><span x-text="a.market===\'US\' ? usd(a.cur) : won(a.cur)"></span><template x-if="a.price_stale"><span class="text-xs text-amber-500 ml-1">종가</span></template></td>\n'
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
    '                        <div class="text-sm text-slate-700 flex items-center justify-end gap-1"><span x-text="bw.cur_price ? (bw.market===\'US\' ? usd(bw.cur_price) : won(bw.cur_price)) : \'가격없음\'"></span><template x-if="bw.price_stale"><span class="text-xs text-amber-500">종가</span></template></div>\n'
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
# P3b: 리포트 탭 패널 HTML
# JS 문자열 안 개행은 \\n, raw 문자열 사용.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_REPORT_PANEL = r"""
    <!-- 리포트 탭 -->
    <section x-show="activeTab==='report'" x-cloak>

      <!-- 로딩 스켈레톤 (최초 1회만) -->
      <template x-if="!report">
        <div class="animate-pulse">
          <!-- 세그먼트 pill x4 -->
          <div class="flex gap-1 mb-5 flex-wrap">
            <template x-for="i in [1,2,3,4]" :key="i">
              <div class="h-7 w-20 bg-slate-200 rounded-full"></div>
            </template>
          </div>
          <!-- 리포트 카드 x5 -->
          <div class="space-y-2">
            <template x-for="j in [1,2,3,4,5]" :key="j">
              <div class="bg-white rounded-xl border border-slate-100 p-4 flex items-start gap-3">
                <div class="rounded-full h-8 w-8 bg-slate-200 shrink-0"></div>
                <div class="flex-1">
                  <div class="h-3 w-28 bg-slate-200 rounded mb-2"></div>
                  <div class="h-3 w-full bg-slate-200 rounded mb-1"></div>
                  <div class="h-3 w-3/4 bg-slate-200 rounded"></div>
                </div>
                <div class="h-3 w-16 bg-slate-200 rounded"></div>
              </div>
            </template>
          </div>
        </div>
      </template>

      <template x-if="report">
        <div>

          <!-- 에러 -->
          <template x-if="report._error">
            <div class="bg-red-50 text-red-600 text-sm rounded-xl p-4 mb-4" x-text="report._error"></div>
          </template>

          <!-- 세그먼트 서브탭 -->
          <div class="flex gap-1 mb-5 flex-wrap">
            <button @click="reportSeg='kr'"
              :class="reportSeg==='kr' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              KR 한국 종목
              <span class="ml-1 text-[10px]" x-text="'(' + (report.kr_total || 0) + ')'"></span>
            </button>
            <button @click="reportSeg='us'"
              :class="reportSeg==='us' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              US 미국 종목
              <span class="ml-1 text-[10px]" x-text="'(' + (report.us_total || 0) + ')'"></span>
            </button>
            <button @click="reportSeg='industry'"
              :class="reportSeg==='industry' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              산업
              <span class="ml-1 text-[10px]" x-text="report.industry_total > 200 ? '최근200/' + report.industry_total : '(' + (report.industry_total || 0) + ')'"></span>
            </button>
            <button @click="reportSeg='macro'"
              :class="reportSeg==='macro' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              시황·전략
              <span class="ml-1 text-[10px]" x-text="report.macro_total > 200 ? '최근200/' + report.macro_total : '(' + (report.macro_total || 0) + ')'"></span>
            </button>
          </div>

          <!-- KR 종목 카드 그리드 -->
          <template x-if="reportSeg==='kr'">
            <div>
              <template x-if="!report.kr || !report.kr.length">
                <div class="text-slate-400 text-center py-16">리포트 없음</div>
              </template>
              <template x-if="report.kr && report.kr.length">
                <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                  <template x-for="c in report.kr" :key="c.ticker">
                    <div @click="openReportModal(c.ticker)"
                         class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 cursor-pointer hover:shadow-md hover:border-blue-300 transition-all">
                      <div class="text-sm font-semibold text-slate-800 truncate" x-text="c.name"></div>
                      <div class="text-xs text-slate-400 mb-2" x-text="c.ticker"></div>
                      <div class="flex items-center justify-between">
                        <span class="text-xs text-blue-600 font-bold" x-text="c.cnt + '건'"></span>
                        <span class="text-[10px] text-slate-400" x-text="c.latest"></span>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </template>

          <!-- US 종목 카드 그리드 -->
          <template x-if="reportSeg==='us'">
            <div>
              <template x-if="!report.us || !report.us.length">
                <div class="text-slate-400 text-center py-16">수집된 미국 종목 리포트 없음</div>
              </template>
              <template x-if="report.us && report.us.length">
                <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                  <template x-for="c in report.us" :key="c.ticker">
                    <div @click="openReportModal(c.ticker)"
                         class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 cursor-pointer hover:shadow-md hover:border-blue-300 transition-all">
                      <div class="text-sm font-semibold text-slate-800 truncate" x-text="c.name"></div>
                      <div class="text-xs text-slate-400 mb-2" x-text="c.ticker"></div>
                      <div class="flex items-center justify-between">
                        <span class="text-xs text-blue-600 font-bold" x-text="c.cnt + '건'"></span>
                        <span class="text-[10px] text-slate-400" x-text="c.latest"></span>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </template>

          <!-- 산업 리포트 리스트 -->
          <template x-if="reportSeg==='industry'">
            <div>
              <template x-if="!report.industry || !report.industry.length">
                <div class="text-slate-400 text-center py-16">산업 리포트 없음</div>
              </template>
              <template x-if="report.industry && report.industry.length">
                <div class="space-y-2">
                  <template x-for="(r, idx) in report.industry" :key="r.ticker + idx">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-start gap-3">
                      <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 mb-1 flex-wrap">
                          <span class="text-[10px] font-bold text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded-full border border-indigo-100" x-text="r.sector || '-'"></span>
                          <span class="text-[10px] text-slate-400" x-text="r.source"></span>
                          <span class="text-[10px] text-slate-300" x-text="r.date"></span>
                        </div>
                        <div class="text-sm text-slate-800 font-medium truncate" x-text="r.title"></div>
                      </div>
                      <template x-if="r.pdf_basename">
                        <a :href="pdfUrl(r.ticker, r.pdf_basename)" target="_blank"
                           class="flex-shrink-0 text-[10px] text-blue-600 font-semibold hover:underline flex items-center gap-0.5">
                          <i data-lucide="file-down" class="w-3 h-3"></i>PDF
                        </a>
                      </template>
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </template>

          <!-- 시황·전략·경제·채권 리스트 -->
          <template x-if="reportSeg==='macro'">
            <div>
              <template x-if="!report.macro || !report.macro.length">
                <div class="text-slate-400 text-center py-16">시황·전략 리포트 없음</div>
              </template>
              <template x-if="report.macro && report.macro.length">
                <div class="space-y-2">
                  <template x-for="(r, idx) in report.macro" :key="r.ticker + idx">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-start gap-3">
                      <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 mb-1 flex-wrap">
                          <span class="text-[10px] font-bold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-100" x-text="r.category"></span>
                          <span class="text-[10px] text-slate-400" x-text="r.source"></span>
                          <span class="text-[10px] text-slate-300" x-text="r.date"></span>
                        </div>
                        <div class="text-sm text-slate-800 font-medium truncate" x-text="r.title"></div>
                      </div>
                      <template x-if="r.pdf_basename">
                        <a :href="pdfUrl(r.ticker, r.pdf_basename)" target="_blank"
                           class="flex-shrink-0 text-[10px] text-blue-600 font-semibold hover:underline flex items-center gap-0.5">
                          <i data-lucide="file-down" class="w-3 h-3"></i>PDF
                        </a>
                      </template>
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </template>

        </div>
      </template>

      <!-- 종목 리포트 목록 모달 -->
      <template x-if="reportModal">
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="closeReportModal()">
          <div class="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 p-6 relative max-h-[80vh] flex flex-col">
            <button @click="closeReportModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-700">
              <i data-lucide="x" class="w-5 h-5"></i>
            </button>
            <div class="text-sm font-bold text-slate-700 mb-3" x-text="reportModal.ticker + ' 리포트 목록'"></div>
            <template x-if="reportModalLoading">
              <div class="animate-pulse">
                <div class="flex items-baseline gap-2 mb-3">
                  <div class="h-5 w-32 bg-slate-200 rounded"></div>
                  <div class="h-4 w-16 bg-slate-200 rounded"></div>
                </div>
                <div class="space-y-2">
                  <template x-for="n in [1,2,3,4]" :key="n">
                    <div class="border border-slate-100 rounded-lg p-3">
                      <div class="h-3 w-full bg-slate-200 rounded mb-2"></div>
                      <div class="h-3 w-3/4 bg-slate-200 rounded"></div>
                    </div>
                  </template>
                </div>
              </div>
            </template>
            <template x-if="!reportModalLoading && reportModal.error">
              <div class="text-red-500 text-sm" x-text="reportModal.error"></div>
            </template>
            <template x-if="!reportModalLoading && reportModalList">
              <div class="overflow-y-auto flex-1 space-y-2 pr-1">
                <template x-if="!reportModalList.length">
                  <div class="text-slate-400 text-sm py-4 text-center">리포트 없음</div>
                </template>
                <template x-for="(r, idx) in reportModalList" :key="idx">
                  <div class="border border-slate-100 rounded-lg p-3">
                    <div class="flex items-start justify-between gap-2">
                      <div class="flex-1 min-w-0">
                        <div class="text-sm text-slate-800 font-medium truncate" x-text="r.title"></div>
                        <div class="flex gap-2 mt-0.5 text-[10px] text-slate-400 flex-wrap">
                          <span x-text="r.date"></span>
                          <span x-text="r.source"></span>
                          <template x-if="r.analyst">
                            <span x-text="r.analyst"></span>
                          </template>
                          <template x-if="r.target_price">
                            <span class="text-blue-600 font-semibold" x-text="'TP ' + Number(r.target_price).toLocaleString('ko-KR') + '원'"></span>
                          </template>
                          <template x-if="r.opinion">
                            <span class="text-slate-600" x-text="r.opinion"></span>
                          </template>
                        </div>
                      </div>
                      <template x-if="r.pdf_basename">
                        <a :href="pdfUrl(reportModal.ticker, r.pdf_basename)" target="_blank"
                           class="flex-shrink-0 text-[10px] text-blue-600 font-semibold hover:underline flex items-center gap-0.5 mt-0.5">
                          <i data-lucide="file-down" class="w-3 h-3"></i>PDF
                        </a>
                      </template>
                    </div>
                  </div>
                </template>
              </div>
            </template>
          </div>
        </div>
      </template>

    </section>
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시그널 탭 패널 HTML
# 섹션: 임박이벤트 / 신호피드 / 발굴스캔 / DART / 컨센서스
# Alpine 서브탭(signalSeg) 으로 전환
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_SIGNAL_PANEL = r"""
    <!-- 시그널 탭 -->
    <section x-show="activeTab==='signal'" x-cloak>

      <!-- 로딩 스켈레톤 (최초 1회만) -->
      <template x-if="!signals">
        <div class="animate-pulse">
          <!-- 서브탭 pill x5 -->
          <div class="flex flex-wrap gap-2 mb-5">
            <template x-for="i in [1,2,3,4,5]" :key="i">
              <div class="h-7 w-20 bg-slate-200 rounded-full"></div>
            </template>
          </div>
          <!-- 피드 카드 x4 -->
          <div class="space-y-2">
            <template x-for="j in [1,2,3,4]" :key="j">
              <div class="bg-white rounded-xl border border-slate-100 p-4 flex items-start gap-3">
                <div class="rounded-full h-8 w-8 bg-slate-200 shrink-0"></div>
                <div class="flex-1">
                  <div class="flex items-center gap-2 mb-2">
                    <div class="h-4 w-16 bg-slate-200 rounded"></div>
                    <div class="h-4 w-20 bg-slate-200 rounded"></div>
                  </div>
                  <div class="h-3 w-full bg-slate-200 rounded mb-1"></div>
                  <div class="h-3 w-2/3 bg-slate-200 rounded"></div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </template>

      <template x-if="signals">
        <div>

          <!-- 서브탭 pill -->
          <div class="flex flex-wrap gap-2 mb-5">
            <button @click="signalSeg='feed'"
              :class="signalSeg==='feed' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              ⚡ 신호 피드
            </button>
            <button @click="signalSeg='events'"
              :class="signalSeg==='events' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              🚨 임박 이벤트
            </button>
            <button @click="signalSeg='scan'; loadAlpha(alphaSeg)"
              :class="signalSeg==='scan' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              🔍 알파스크리너
            </button>
            <button @click="signalSeg='dart'"
              :class="signalSeg==='dart' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              📑 DART
            </button>
            <button @click="signalSeg='consensus'"
              :class="signalSeg==='consensus' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              📈 컨센서스
            </button>
          </div>

          <!-- ── ⚡ 신호 피드 ── -->
          <template x-if="signalSeg==='feed'">
            <div>
              <template x-if="signals.feed && signals.feed.length">
                <div class="space-y-2">
                  <template x-for="(item, idx) in signals.feed" :key="idx">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-start gap-3">
                      <span class="text-lg mt-0.5" x-text="signalKindIcon(item.kind)"></span>
                      <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 flex-wrap mb-1">
                          <span class="text-xs px-1.5 py-0.5 rounded font-medium"
                            :class="signalKindClass(item.kind)"
                            x-text="signalKindLabel(item.kind)"></span>
                          <span class="text-sm font-semibold text-slate-800" x-text="item.name || item.ticker"></span>
                          <span class="text-xs text-slate-400" x-text="item.ticker"></span>
                        </div>
                        <div class="text-xs text-slate-600 truncate" x-text="item.detail"></div>
                        <div class="text-xs text-slate-400 mt-1" x-text="item.ts"></div>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
              <template x-if="!signals.feed || !signals.feed.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">최근 발화 신호 없음</div>
              </template>
            </div>
          </template>

          <!-- ── 🚨 임박 이벤트 ── -->
          <template x-if="signalSeg==='events'">
            <div>
              <template x-if="signals.events && signals.events.length">
                <div class="space-y-2">
                  <template x-for="(ev, idx) in signals.events" :key="idx">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-center gap-4">
                      <div class="text-center w-14 shrink-0">
                        <div class="text-lg font-bold" :class="dDayClass(ev.dday)" x-text="dDayLabel(ev.dday)"></div>
                        <div class="text-xs text-slate-400 mt-0.5" x-text="ev.date"></div>
                      </div>
                      <div class="flex-1 min-w-0">
                        <div class="text-sm font-medium text-slate-800 truncate">
                          <span x-text="ev.dday <= 3 ? '🚨 ' : ''"></span>
                          <span x-text="ev.name"></span>
                        </div>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
              <template x-if="!signals.events || !signals.events.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">임박 이벤트 없음</div>
              </template>
            </div>
          </template>

          <!-- ── 🔍 알파스크리너 ── -->
          <template x-if="signalSeg==='scan'">
            <div>
              <!-- 전략 필터 pill row (가로스크롤) -->
              <div class="flex gap-2 mb-4 overflow-x-auto pb-1">
                <button @click="loadAlpha('change')"
                  :class="alphaSeg==='change' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  변화감지
                </button>
                <button @click="loadAlpha('fscore')"
                  :class="alphaSeg==='fscore' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  F-Score 우량
                </button>
                <button @click="loadAlpha('mscore')"
                  :class="alphaSeg==='mscore' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  M-Score 안전
                </button>
                <button @click="loadAlpha('fcf')"
                  :class="alphaSeg==='fcf' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  FCF 수익률
                </button>
                <button @click="loadAlpha('high52')"
                  :class="alphaSeg==='high52' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  52주 신고가
                </button>
                <button @click="loadAlpha('low52')"
                  :class="alphaSeg==='low52' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
                  class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors whitespace-nowrap">
                  52주 신저가
                </button>
              </div>

              <!-- 로딩 스켈레톤 -->
              <template x-if="alphaLoading && !alphaData[alphaSeg]">
                <div class="animate-pulse space-y-2">
                  <template x-for="i in [1,2,3,4,5]" :key="i">
                    <div class="bg-white rounded-xl border border-slate-100 p-4 h-14"></div>
                  </template>
                </div>
              </template>

              <!-- 변화감지 (change) — 기존 카드 그리드 유지 -->
              <template x-if="alphaSeg==='change' && alphaData['change']">
                <div>
                  <template x-if="alphaData['change'].items && alphaData['change'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['change'].meta && alphaData['change'].meta.as_of ? alphaData['change'].meta.as_of : '') + ' · ' + (alphaData['change'].meta && alphaData['change'].meta.count != null ? alphaData['change'].meta.count + '종목' : '')">
                      </div>
                      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <template x-for="(s, idx) in alphaData['change'].items" :key="idx">
                          <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
                            <div class="flex items-start justify-between mb-2">
                              <div>
                                <div class="text-sm font-semibold text-slate-800" x-text="s.name || s.ticker"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker + (s.market ? ' · ' + s.market : '')"></div>
                              </div>
                              <div :class="s.chg_pct >= 0 ? 'text-green-600' : 'text-red-600'"
                                class="text-sm font-bold"
                                x-text="s.chg_pct != null ? (s.chg_pct >= 0 ? '+' : '') + s.chg_pct.toFixed(1) + '%' : '-'"></div>
                            </div>
                            <div class="flex flex-wrap gap-1.5 text-xs">
                              <template x-if="s.op_profit_delta != null">
                                <span class="bg-green-50 text-green-700 px-1.5 py-0.5 rounded">
                                  적자→흑자 Δ<span x-text="s.op_profit_delta.toFixed(0)"></span>억
                                </span>
                              </template>
                              <template x-if="s.fscore_delta != null">
                                <span class="bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded">
                                  F-Score +<span x-text="s.fscore_delta"></span>
                                </span>
                              </template>
                              <template x-if="s.insider_reprors != null">
                                <span class="bg-purple-50 text-purple-700 px-1.5 py-0.5 rounded">
                                  내부자 <span x-text="s.insider_reprors"></span>명 순매수
                                </span>
                              </template>
                            </div>
                          </div>
                        </template>
                      </div>
                    </div>
                  </template>
                  <template x-if="alphaData['change'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400" x-text="alphaData['change'].error"></div>
                  </template>
                  <template x-if="!alphaData['change'].error && (!alphaData['change'].items || !alphaData['change'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      발굴된 종목 없음
                      <button @click="loadAlpha('change')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

              <!-- F-Score 우량 (fscore) — 테이블 -->
              <template x-if="alphaSeg==='fscore' && alphaData['fscore']">
                <div>
                  <template x-if="alphaData['fscore'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400" x-text="alphaData['fscore'].error"></div>
                  </template>
                  <template x-if="!alphaData['fscore'].error && alphaData['fscore'].items && alphaData['fscore'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['fscore'].meta && alphaData['fscore'].meta.as_of ? alphaData['fscore'].meta.as_of + ' · ' : '') + (alphaData['fscore'].meta && alphaData['fscore'].meta.count != null ? alphaData['fscore'].meta.count + '종목' : '')">
                      </div>
                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                        <!-- 데스크탑 테이블 -->
                        <table class="w-full text-sm hidden sm:table">
                          <thead>
                            <tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">
                              <th class="text-left px-4 py-2.5 font-medium">#</th>
                              <th class="text-left px-4 py-2.5 font-medium">종목</th>
                              <th class="text-center px-4 py-2.5 font-medium">F-Score</th>
                              <th class="text-right px-4 py-2.5 font-medium hidden md:table-cell">시총(억)</th>
                            </tr>
                          </thead>
                          <tbody>
                            <template x-for="(s, idx) in alphaData['fscore'].items" :key="idx">
                              <tr class="border-b border-slate-50 hover:bg-slate-50">
                                <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="s.rank"></td>
                                <td class="px-4 py-2.5">
                                  <div class="font-medium text-slate-800" x-text="s.name"></div>
                                  <div class="text-xs text-slate-400" x-text="s.ticker + ' · ' + (s.market || '')"></div>
                                </td>
                                <td class="px-4 py-2.5 text-center">
                                  <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                    :class="s.fscore >= 8 ? 'bg-emerald-100 text-emerald-700' : 'bg-green-100 text-green-700'"
                                    x-text="s.fscore"></span>
                                </td>
                                <td class="px-4 py-2.5 text-right text-slate-600 text-xs hidden md:table-cell"
                                  x-text="s.market_cap != null ? s.market_cap.toLocaleString('ko-KR') : '-'"></td>
                              </tr>
                            </template>
                          </tbody>
                        </table>
                        <!-- 모바일 카드 -->
                        <div class="sm:hidden divide-y divide-slate-100">
                          <template x-for="(s, idx) in alphaData['fscore'].items" :key="idx">
                            <div class="p-3 flex items-center gap-3">
                              <span class="text-slate-400 text-xs w-5 text-right" x-text="s.rank"></span>
                              <div class="flex-1 min-w-0">
                                <div class="text-sm font-medium text-slate-800" x-text="s.name"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker"></div>
                              </div>
                              <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                :class="s.fscore >= 8 ? 'bg-emerald-100 text-emerald-700' : 'bg-green-100 text-green-700'"
                                x-text="'F' + s.fscore"></span>
                            </div>
                          </template>
                        </div>
                      </div>
                    </div>
                  </template>
                  <template x-if="!alphaData['fscore'].error && (!alphaData['fscore'].items || !alphaData['fscore'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      F-Score 데이터 없음
                      <button @click="loadAlpha('fscore')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

              <!-- M-Score 안전 (mscore) — 테이블 -->
              <template x-if="alphaSeg==='mscore' && alphaData['mscore']">
                <div>
                  <template x-if="alphaData['mscore'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-amber-600"
                      x-text="alphaData['mscore'].error || 'M-Score 데이터 수집 대기 중'"></div>
                  </template>
                  <template x-if="!alphaData['mscore'].error && alphaData['mscore'].items && alphaData['mscore'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['mscore'].meta && alphaData['mscore'].meta.as_of ? alphaData['mscore'].meta.as_of + ' · ' : '') + (alphaData['mscore'].meta && alphaData['mscore'].meta.count != null ? alphaData['mscore'].meta.count + '종목' : '')">
                      </div>
                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                        <table class="w-full text-sm hidden sm:table">
                          <thead>
                            <tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">
                              <th class="text-left px-4 py-2.5 font-medium">#</th>
                              <th class="text-left px-4 py-2.5 font-medium">종목</th>
                              <th class="text-center px-4 py-2.5 font-medium">M-Score</th>
                              <th class="text-right px-4 py-2.5 font-medium hidden md:table-cell">시총(억)</th>
                            </tr>
                          </thead>
                          <tbody>
                            <template x-for="(s, idx) in alphaData['mscore'].items" :key="idx">
                              <tr class="border-b border-slate-50 hover:bg-slate-50">
                                <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="s.rank"></td>
                                <td class="px-4 py-2.5">
                                  <div class="font-medium text-slate-800" x-text="s.name"></div>
                                  <div class="text-xs text-slate-400" x-text="s.ticker + ' · ' + (s.market || '')"></div>
                                </td>
                                <td class="px-4 py-2.5 text-center">
                                  <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                    :class="s.mscore <= -2.22 ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'"
                                    x-text="s.mscore != null ? s.mscore.toFixed(2) : '-'"></span>
                                  <span class="ml-1 text-xs"
                                    :class="s.mscore <= -2.22 ? 'text-green-600' : 'text-amber-600'"
                                    x-text="s.mscore <= -2.22 ? '안전' : '주의'"></span>
                                </td>
                                <td class="px-4 py-2.5 text-right text-slate-600 text-xs hidden md:table-cell"
                                  x-text="s.market_cap != null ? s.market_cap.toLocaleString('ko-KR') : '-'"></td>
                              </tr>
                            </template>
                          </tbody>
                        </table>
                        <div class="sm:hidden divide-y divide-slate-100">
                          <template x-for="(s, idx) in alphaData['mscore'].items" :key="idx">
                            <div class="p-3 flex items-center gap-3">
                              <span class="text-slate-400 text-xs w-5 text-right" x-text="s.rank"></span>
                              <div class="flex-1 min-w-0">
                                <div class="text-sm font-medium text-slate-800" x-text="s.name"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker"></div>
                              </div>
                              <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                :class="s.mscore <= -2.22 ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'"
                                x-text="s.mscore != null ? s.mscore.toFixed(2) : '-'"></span>
                            </div>
                          </template>
                        </div>
                      </div>
                    </div>
                  </template>
                  <template x-if="!alphaData['mscore'].error && (!alphaData['mscore'].items || !alphaData['mscore'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      M-Score 데이터 없음
                      <button @click="loadAlpha('mscore')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

              <!-- FCF 수익률 (fcf) — 테이블 -->
              <template x-if="alphaSeg==='fcf' && alphaData['fcf']">
                <div>
                  <template x-if="alphaData['fcf'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400" x-text="alphaData['fcf'].error"></div>
                  </template>
                  <template x-if="!alphaData['fcf'].error && alphaData['fcf'].items && alphaData['fcf'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['fcf'].meta && alphaData['fcf'].meta.as_of ? alphaData['fcf'].meta.as_of + ' · ' : '') + (alphaData['fcf'].meta && alphaData['fcf'].meta.count != null ? alphaData['fcf'].meta.count + '종목' : '')">
                      </div>
                      <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                        <table class="w-full text-sm hidden sm:table">
                          <thead>
                            <tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">
                              <th class="text-left px-4 py-2.5 font-medium">#</th>
                              <th class="text-left px-4 py-2.5 font-medium">종목</th>
                              <th class="text-right px-4 py-2.5 font-medium">FCF/EV(%)</th>
                              <th class="text-right px-4 py-2.5 font-medium hidden md:table-cell">시총(억)</th>
                            </tr>
                          </thead>
                          <tbody>
                            <template x-for="(s, idx) in alphaData['fcf'].items" :key="idx">
                              <tr class="border-b border-slate-50 hover:bg-slate-50">
                                <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="s.rank"></td>
                                <td class="px-4 py-2.5">
                                  <div class="font-medium text-slate-800" x-text="s.name"></div>
                                  <div class="text-xs text-slate-400" x-text="s.ticker + ' · ' + (s.market || '')"></div>
                                </td>
                                <td class="px-4 py-2.5 text-right">
                                  <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                    :class="s.fcf_yield >= 10 ? 'bg-emerald-100 text-emerald-700' : s.fcf_yield >= 5 ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-600'"
                                    x-text="s.fcf_yield != null ? s.fcf_yield.toFixed(1) + '%' : '-'"></span>
                                </td>
                                <td class="px-4 py-2.5 text-right text-slate-600 text-xs hidden md:table-cell"
                                  x-text="s.market_cap != null ? s.market_cap.toLocaleString('ko-KR') : '-'"></td>
                              </tr>
                            </template>
                          </tbody>
                        </table>
                        <div class="sm:hidden divide-y divide-slate-100">
                          <template x-for="(s, idx) in alphaData['fcf'].items" :key="idx">
                            <div class="p-3 flex items-center gap-3">
                              <span class="text-slate-400 text-xs w-5 text-right" x-text="s.rank"></span>
                              <div class="flex-1 min-w-0">
                                <div class="text-sm font-medium text-slate-800" x-text="s.name"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker"></div>
                              </div>
                              <span class="text-xs font-bold px-2 py-0.5 rounded-full"
                                :class="s.fcf_yield >= 10 ? 'bg-emerald-100 text-emerald-700' : s.fcf_yield >= 5 ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-600'"
                                x-text="s.fcf_yield != null ? s.fcf_yield.toFixed(1) + '%' : '-'"></span>
                            </div>
                          </template>
                        </div>
                      </div>
                    </div>
                  </template>
                  <template x-if="!alphaData['fcf'].error && (!alphaData['fcf'].items || !alphaData['fcf'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      FCF 데이터 없음
                      <button @click="loadAlpha('fcf')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

              <!-- 52주 신고가 근접 (high52) — 카드그리드 -->
              <template x-if="alphaSeg==='high52' && alphaData['high52']">
                <div>
                  <template x-if="alphaData['high52'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400" x-text="alphaData['high52'].error"></div>
                  </template>
                  <template x-if="!alphaData['high52'].error && alphaData['high52'].items && alphaData['high52'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['high52'].meta && alphaData['high52'].meta.as_of ? alphaData['high52'].meta.as_of + ' · ' : '') + (alphaData['high52'].meta && alphaData['high52'].meta.count != null ? alphaData['high52'].meta.count + '종목' : '')">
                      </div>
                      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <template x-for="(s, idx) in alphaData['high52'].items" :key="idx">
                          <div class="bg-white rounded-xl border border-emerald-200 shadow-sm p-4">
                            <div class="flex items-start justify-between mb-2">
                              <div>
                                <div class="text-sm font-semibold text-slate-800" x-text="s.name || s.ticker"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker"></div>
                              </div>
                              <div :class="s.chg_pct >= 0 ? 'text-green-600' : 'text-red-600'"
                                class="text-sm font-bold"
                                x-text="s.chg_pct != null ? (s.chg_pct >= 0 ? '+' : '') + s.chg_pct.toFixed(1) + '%' : '-'"></div>
                            </div>
                            <div class="flex gap-2 text-xs flex-wrap">
                              <span class="bg-emerald-50 text-emerald-700 px-1.5 py-0.5 rounded"
                                x-text="'52주고 ' + (s.new_high != null ? s.new_high.toLocaleString('ko-KR') : '-')"></span>
                              <span class="text-slate-500"
                                x-text="'현재 ' + (s.price != null ? s.price.toLocaleString('ko-KR') : '-')"></span>
                              <span class="font-semibold text-emerald-600"
                                x-text="'괴리 ' + (s.high_gap_pct != null ? s.high_gap_pct.toFixed(1) + '%' : '-')"></span>
                            </div>
                          </div>
                        </template>
                      </div>
                    </div>
                  </template>
                  <template x-if="!alphaData['high52'].error && (!alphaData['high52'].items || !alphaData['high52'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      신고가 근접 종목 없음
                      <button @click="loadAlpha('high52')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

              <!-- 52주 신저가 근접 (low52) — 카드그리드 -->
              <template x-if="alphaSeg==='low52' && alphaData['low52']">
                <div>
                  <template x-if="alphaData['low52'].error">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400" x-text="alphaData['low52'].error"></div>
                  </template>
                  <template x-if="!alphaData['low52'].error && alphaData['low52'].items && alphaData['low52'].items.length">
                    <div>
                      <div class="text-xs text-slate-400 mb-3"
                        x-text="(alphaData['low52'].meta && alphaData['low52'].meta.as_of ? alphaData['low52'].meta.as_of + ' · ' : '') + (alphaData['low52'].meta && alphaData['low52'].meta.count != null ? alphaData['low52'].meta.count + '종목' : '')">
                      </div>
                      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <template x-for="(s, idx) in alphaData['low52'].items" :key="idx">
                          <div class="bg-white rounded-xl border border-red-200 shadow-sm p-4">
                            <div class="flex items-start justify-between mb-2">
                              <div>
                                <div class="text-sm font-semibold text-slate-800" x-text="s.name || s.ticker"></div>
                                <div class="text-xs text-slate-400" x-text="s.ticker"></div>
                              </div>
                              <div :class="s.chg_pct >= 0 ? 'text-green-600' : 'text-red-600'"
                                class="text-sm font-bold"
                                x-text="s.chg_pct != null ? (s.chg_pct >= 0 ? '+' : '') + s.chg_pct.toFixed(1) + '%' : '-'"></div>
                            </div>
                            <div class="flex gap-2 text-xs flex-wrap">
                              <span class="bg-red-50 text-red-700 px-1.5 py-0.5 rounded"
                                x-text="'52주저 ' + (s.new_low != null ? s.new_low.toLocaleString('ko-KR') : '-')"></span>
                              <span class="text-slate-500"
                                x-text="'현재 ' + (s.price != null ? s.price.toLocaleString('ko-KR') : '-')"></span>
                              <span class="font-semibold text-red-600"
                                x-text="'괴리 ' + (s.low_gap_pct != null ? s.low_gap_pct.toFixed(1) + '%' : '-')"></span>
                            </div>
                          </div>
                        </template>
                      </div>
                    </div>
                  </template>
                  <template x-if="!alphaData['low52'].error && (!alphaData['low52'].items || !alphaData['low52'].items.length)">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
                      신저가 근접 종목 없음
                      <button @click="loadAlpha('low52')" class="ml-2 text-indigo-600 underline text-xs">재시도</button>
                    </div>
                  </template>
                </div>
              </template>

            </div>
          </template>

          <!-- ── 📑 DART ── -->
          <template x-if="signalSeg==='dart'">
            <div>
              <template x-if="signals.dart && signals.dart.length">
                <div class="space-y-2">
                  <template x-for="(d, idx) in signals.dart" :key="idx">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-start gap-3">
                      <div class="text-slate-400 text-xs w-20 shrink-0 mt-0.5" x-text="d.date"></div>
                      <div class="flex-1 min-w-0">
                        <div class="text-xs font-semibold text-slate-700 mb-0.5" x-text="d.corp"></div>
                        <div class="text-xs text-slate-600 leading-snug" x-text="d.title"></div>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
              <template x-if="!signals.dart || !signals.dart.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">최근 DART 공시 없음</div>
              </template>
            </div>
          </template>

          <!-- ── 📈 컨센서스 ── -->
          <template x-if="signalSeg==='consensus'">
            <div>
              <template x-if="signals.consensus && signals.consensus.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                  <table class="w-full text-sm">
                    <thead>
                      <tr class="text-xs text-slate-400 border-b border-slate-100 bg-slate-50">
                        <th class="text-left px-4 py-2.5 font-medium">종목</th>
                        <th class="text-right px-4 py-2.5 font-medium">현재 TP</th>
                        <th class="text-right px-4 py-2.5 font-medium">이전 TP</th>
                        <th class="text-right px-4 py-2.5 font-medium">변동</th>
                      </tr>
                    </thead>
                    <tbody>
                      <template x-for="(c, idx) in signals.consensus" :key="idx">
                        <tr class="border-b border-slate-50 hover:bg-slate-50">
                          <td class="px-4 py-2.5">
                            <div class="font-medium text-slate-800" x-text="c.name"></div>
                            <div class="text-xs text-slate-400" x-text="c.ticker"></div>
                          </td>
                          <td class="px-4 py-2.5 text-right text-slate-700" x-text="c.avg ? c.avg.toLocaleString('ko-KR') + '원' : '-'"></td>
                          <td class="px-4 py-2.5 text-right text-slate-500" x-text="c.prev_avg ? c.prev_avg.toLocaleString('ko-KR') + '원' : '-'"></td>
                          <td class="px-4 py-2.5 text-right font-semibold"
                            :class="c.chg_pct >= 0 ? 'text-green-600' : 'text-red-600'"
                            x-text="c.chg_pct != null ? (c.chg_pct >= 0 ? '+' : '') + c.chg_pct.toFixed(1) + '%' : '-'">
                          </td>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
              </template>
              <template x-if="!signals.consensus || !signals.consensus.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">컨센서스 변동 없음</div>
              </template>
            </div>
          </template>

        </div>
      </template>

    </section>
"""

# P3b: 기록 탭 패널 HTML
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_RECORD_PANEL = r"""
    <!-- 기록 탭 -->
    <section x-show="activeTab==='record'" x-cloak>

      <!-- 로딩 스켈레톤 (최초 1회만) -->
      <template x-if="!record">
        <div class="animate-pulse">
          <!-- 섹션 서브탭 pill x3 -->
          <div class="flex gap-1 mb-5">
            <template x-for="i in [1,2,3]" :key="i">
              <div class="h-7 w-20 bg-slate-200 rounded-full"></div>
            </template>
          </div>
          <!-- 기록 카드 x3 -->
          <div class="space-y-3">
            <template x-for="j in [1,2,3]" :key="j">
              <div class="bg-white rounded-xl border border-slate-100 p-4">
                <div class="flex items-start justify-between mb-3">
                  <div class="h-4 w-24 bg-slate-200 rounded"></div>
                  <div class="h-5 w-14 bg-slate-200 rounded-full"></div>
                </div>
                <div class="h-3 w-full bg-slate-200 rounded mb-1"></div>
                <div class="h-3 w-3/4 bg-slate-200 rounded"></div>
              </div>
            </template>
          </div>
        </div>
      </template>

      <template x-if="record">
        <div>

          <!-- 토스트 -->
          <template x-if="recordToast">
            <div class="fixed top-20 right-4 z-50 bg-slate-800 text-white text-sm px-4 py-2 rounded-lg shadow-lg" x-text="recordToast"></div>
          </template>

          <!-- 섹션 서브탭 -->
          <div class="flex gap-1 mb-5">
            <button @click="recordSection='decisions'"
              :class="recordSection==='decisions' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              투자판단
            </button>
            <button @click="recordSection='trades'"
              :class="recordSection==='trades' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              매매 성과
            </button>
            <button @click="recordSection='todo'"
              :class="recordSection==='todo' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
              class="text-xs px-3 py-1.5 rounded-full font-medium transition-colors">
              투자 TODO
            </button>
          </div>

          <!-- 투자판단 섹션 -->
          <template x-if="recordSection==='decisions'">
            <div>
              <!-- 새 투자판단 폼 토글 -->
              <div class="flex items-center justify-between mb-4">
                <h2 class="text-base font-semibold text-slate-700">투자판단 기록</h2>
                <button @click="decisionForm.show = !decisionForm.show"
                  :class="decisionForm.show ? 'bg-slate-600' : 'bg-blue-600'"
                  class="text-xs text-white px-3 py-1.5 rounded-lg font-medium">
                  <span x-text="decisionForm.show ? '닫기' : '+ 새 판단'"></span>
                </button>
              </div>

              <!-- 폼 -->
              <template x-if="decisionForm.show">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-5">
                  <div class="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
                    <div>
                      <label class="text-xs text-slate-500 block mb-1">날짜</label>
                      <input type="date" x-model="decisionForm.date"
                        class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
                    </div>
                    <div>
                      <label class="text-xs text-slate-500 block mb-1">레짐 *</label>
                      <select x-model="decisionForm.regime"
                        class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
                        <option value="">선택</option>
                        <option value="공격">공격</option>
                        <option value="경계">경계</option>
                        <option value="위기">위기</option>
                      </select>
                    </div>
                    <div class="col-span-2 md:col-span-1">
                      <label class="text-xs text-slate-500 block mb-1">메모</label>
                      <input x-model="decisionForm.memo" placeholder="간단 메모 (선택)"
                        class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
                    </div>
                  </div>
                  <button @click="submitDecision()"
                    class="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors">저장</button>
                </div>
              </template>

              <!-- 판단 카드 목록 -->
              <template x-if="record.decisions && record.decisions.length">
                <div class="space-y-3">
                  <template x-for="d in record.decisions.slice(0, decisionsLimit)" :key="d.date">
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
                      <div class="flex items-start justify-between gap-2 mb-2">
                        <div>
                          <span class="text-sm font-bold text-slate-800" x-text="d.date"></span>
                          <template x-if="d.saved_at">
                            <span class="text-[10px] text-slate-400 ml-2" x-text="'저장 ' + d.saved_at"></span>
                          </template>
                        </div>
                        <span :class="regimeColor(d.regime)"
                              class="text-xs font-bold px-2 py-0.5 rounded-full"
                              x-text="d.regime || '-'"></span>
                      </div>
                      <template x-if="d.notes">
                        <p class="text-sm text-slate-600" x-text="d.notes"></p>
                      </template>
                      <template x-if="d.actions && d.actions.length">
                        <ul class="mt-2 space-y-0.5">
                          <template x-for="(a, ai) in d.actions" :key="ai">
                            <li class="text-xs text-slate-500 flex gap-1">
                              <span class="text-slate-300">&#183;</span>
                              <span x-text="typeof a === 'string' ? a : JSON.stringify(a)"></span>
                            </li>
                          </template>
                        </ul>
                      </template>
                    </div>
                  </template>
                  <!-- 더보기 버튼 — 전체 건수보다 limit이 작을 때만 표시 -->
                  <template x-if="decisionsLimit < record.decisions.length">
                    <div class="text-center pt-1">
                      <button @click="decisionsLimit = record.decisions.length"
                        class="text-sm text-blue-600 hover:text-blue-700 px-4 py-2 rounded-lg border border-blue-200 hover:bg-blue-50 transition-colors"
                        x-text="'더보기 (' + (record.decisions.length - decisionsLimit) + '건 더)'">
                      </button>
                    </div>
                  </template>
                </div>
              </template>

              <template x-if="!record.decisions || !record.decisions.length">
                <div class="text-slate-400 text-center py-16">기록된 투자판단 없음</div>
              </template>
            </div>
          </template>

          <!-- 매매 성과 섹션 -->
          <template x-if="recordSection==='trades'">
            <div>
              <h2 class="text-base font-semibold text-slate-700 mb-4">매매 성과</h2>
              <template x-if="record.trades && record.trades.total_trades != null">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-5">
                  <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                      <div class="text-xs text-slate-400 mb-0.5">총 매매건수</div>
                      <div class="text-xl font-bold text-slate-800" x-text="record.trades.total_trades || 0"></div>
                    </div>
                    <div>
                      <div class="text-xs text-slate-400 mb-0.5">승률</div>
                      <div class="text-xl font-bold text-slate-800"
                           x-text="record.trades.win_rate_pct != null ? record.trades.win_rate_pct.toFixed(1) + '%' : '-'"></div>
                    </div>
                    <div>
                      <div class="text-xs text-slate-400 mb-0.5">평균 손익/건</div>
                      <div :class="pnlClass(record.trades.avg_pnl_per_trade)" class="text-xl font-bold"
                           x-text="record.trades.avg_pnl_per_trade != null ? (record.trades.avg_pnl_per_trade >= 0 ? '+' : '') + Number(record.trades.avg_pnl_per_trade).toLocaleString('ko-KR') : '-'"></div>
                    </div>
                    <div>
                      <div class="text-xs text-slate-400 mb-0.5">평균 보유</div>
                      <div class="text-xl font-bold text-slate-800"
                           x-text="record.trades.avg_holding_days != null ? Math.abs(record.trades.avg_holding_days).toFixed(0) + '일' : '-'"></div>
                    </div>
                  </div>
                </div>
              </template>
              <template x-if="record.trades && record.trades.trades && record.trades.trades.length">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                  <div class="px-4 py-3 border-b border-slate-100">
                    <h3 class="text-sm font-semibold text-slate-700">최근 매매 기록</h3>
                  </div>
                  <div class="overflow-x-auto">
                    <table class="w-full text-sm">
                      <thead>
                        <tr class="text-xs text-slate-400 border-b border-slate-100">
                          <th class="text-left py-2 px-4 font-medium">날짜</th>
                          <th class="text-left py-2 px-2 font-medium">종목</th>
                          <th class="text-center py-2 px-2 font-medium">구분</th>
                          <th class="text-right py-2 px-4 font-medium">이유</th>
                        </tr>
                      </thead>
                      <tbody>
                        <template x-for="(t, ti) in record.trades.trades.slice(0,20)" :key="ti">
                          <tr class="border-b border-slate-50 hover:bg-slate-50">
                            <td class="py-2 px-4 text-xs text-slate-500" x-text="t.date || '-'"></td>
                            <td class="py-2 px-2">
                              <span class="font-medium text-slate-800" x-text="t.name || t.ticker || '-'"></span>
                              <span class="text-xs text-slate-400 ml-1" x-text="t.ticker || ''"></span>
                            </td>
                            <td class="py-2 px-2 text-center">
                              <span :class="t.side === 'buy' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600'"
                                    class="text-[10px] font-bold px-1.5 py-0.5 rounded"
                                    x-text="t.side === 'buy' ? '매수' : '매도'"></span>
                            </td>
                            <td class="py-2 px-4 text-right text-xs text-slate-500 truncate max-w-[120px]"
                                x-text="t.reason || '-'"></td>
                          </tr>
                        </template>
                      </tbody>
                    </table>
                  </div>
                </div>
              </template>
              <template x-if="!record.trades || record.trades.total_trades == null">
                <div class="text-slate-400 text-center py-16">매매 기록 없음</div>
              </template>
            </div>
          </template>

          <!-- 투자 TODO 섹션 -->
          <template x-if="recordSection==='todo'">
            <div>
              <h2 class="text-base font-semibold text-slate-700 mb-4">투자 TODO</h2>
              <template x-if="record.todo">
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
                  <pre class="text-sm text-slate-700 whitespace-pre-wrap font-sans leading-relaxed" x-text="record.todo"></pre>
                </div>
              </template>
              <template x-if="!record.todo">
                <div class="text-slate-400 text-center py-16">TODO 파일 없음</div>
              </template>
            </div>
          </template>

        </div>
      </template>

    </section>
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# US 애널리스트 탭 패널 HTML
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_US_PANEL = r"""
    <!-- US 애널리스트 탭 -->
    <section x-show="activeTab==='us'" x-cloak>

      <!-- 서브탭 pill 바 -->
      <div class="flex gap-2 mb-5 overflow-x-auto">
        <button @click="setUsSeg('candidates')"
          :class="usSeg==='candidates' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
          class="px-4 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors">
          매수후보
        </button>
        <button @click="setUsSeg('scan')"
          :class="usSeg==='scan' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
          class="px-4 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors">
          레이팅변화
        </button>
        <button @click="setUsSeg('analysts')"
          :class="usSeg==='analysts' ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 border border-slate-200'"
          class="px-4 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors">
          톱애널
        </button>
      </div>

      <!-- ── 매수후보 서브탭 ── -->
      <template x-if="usSeg==='candidates'">
        <div>
          <!-- 스켈레톤 -->
          <template x-if="!usCandidates">
            <div class="animate-pulse">
              <div class="flex gap-3 mb-4">
                <div class="h-8 w-32 bg-slate-200 rounded-lg"></div>
                <div class="h-8 w-24 bg-slate-200 rounded-lg"></div>
              </div>
              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <template x-for="i in [1,2,3,4,5,6]" :key="i">
                  <div class="bg-white rounded-xl border border-slate-100 p-4">
                    <div class="h-4 w-16 bg-slate-200 rounded mb-2"></div>
                    <div class="h-6 w-24 bg-slate-200 rounded mb-3"></div>
                    <div class="h-3 w-full bg-slate-200 rounded mb-1"></div>
                    <div class="h-3 w-2/3 bg-slate-200 rounded"></div>
                  </div>
                </template>
              </div>
            </div>
          </template>

          <!-- 데이터 있음 -->
          <template x-if="usCandidates">
            <div>
              <!-- 필터 바 -->
              <div class="flex flex-wrap items-center gap-3 mb-4">
                <div class="flex items-center gap-2">
                  <label class="text-sm text-slate-600 whitespace-nowrap">최소 업사이드</label>
                  <select x-model.number="usCandidatesMinUpside"
                    class="text-sm border border-slate-200 rounded px-2 py-1">
                    <option value="20">20%+</option>
                    <option value="30">30%+</option>
                    <option value="50">50%+</option>
                  </select>
                </div>
                <label class="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer">
                  <input type="checkbox" x-model="usCandidatesTierSOnly" class="rounded">
                  Tier S 포함
                </label>
                <span class="text-xs text-slate-400" x-text="'총 ' + filteredCandidates().length + '건'"></span>
              </div>

              <!-- 카드 그리드 -->
              <template x-if="filteredCandidates().length === 0">
                <div class="text-center py-12 text-slate-400">
                  <i data-lucide="search-x" class="w-10 h-10 mx-auto mb-2 opacity-40"></i>
                  <p class="text-sm">필터 조건을 만족하는 매수후보가 없습니다.</p>
                </div>
              </template>

              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <template x-for="c in filteredCandidates()" :key="c.ticker">
                  <div @click="openUsModal(c.ticker)"
                    class="bg-white rounded-xl border border-slate-100 p-4 cursor-pointer hover:shadow-md transition-shadow">
                    <div class="flex items-center justify-between mb-2">
                      <span class="font-bold text-slate-800" x-text="c.ticker"></span>
                      <span class="text-xs px-2 py-0.5 rounded-full font-medium"
                        :class="hmColorUs(c.upside_pct)"
                        x-text="'+' + c.upside_pct.toFixed(1) + '%'"></span>
                    </div>
                    <div class="text-sm text-slate-600 mb-1">
                      <span x-text="usd(c.price)"></span>
                      <span class="text-slate-400 mx-1">→</span>
                      <span class="font-semibold text-slate-800" x-text="usd(c.avg_target)"></span>
                    </div>
                    <div class="flex items-center gap-2 mb-3">
                      <template x-if="c.tier_s_count > 0">
                        <span class="text-xs px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium"
                          x-text="'S×' + c.tier_s_count"></span>
                      </template>
                      <template x-if="c.tier_a_count > 0">
                        <span class="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 font-medium"
                          x-text="'A×' + c.tier_a_count"></span>
                      </template>
                      <span class="text-xs text-slate-400" x-text="'총 ' + c.total_advisors + '명'"></span>
                    </div>
                    <div class="text-xs text-slate-500 flex items-center gap-1">
                      <i data-lucide="clock" class="w-3 h-3 opacity-60"></i>
                      <span x-text="c.latest_call_days_ago + '일 전'"></span>
                      <template x-if="c.tier_s_analysts && c.tier_s_analysts.length > 0">
                        <span class="ml-1 text-amber-600 truncate" x-text="c.tier_s_analysts[0].name"></span>
                      </template>
                      <template x-if="(!c.tier_s_analysts || c.tier_s_analysts.length === 0) && c.tier_a_analysts && c.tier_a_analysts.length > 0">
                        <span class="ml-1 text-blue-600 truncate" x-text="c.tier_a_analysts[0].name"></span>
                      </template>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- ── 레이팅변화 서브탭 ── -->
      <template x-if="usSeg==='scan'">
        <div>
          <!-- 스켈레톤 -->
          <template x-if="!usScan">
            <div class="animate-pulse space-y-3">
              <template x-for="i in [1,2,3,4,5]" :key="i">
                <div class="bg-white rounded-xl border border-slate-100 p-4">
                  <div class="flex items-center gap-3">
                    <div class="h-4 w-12 bg-slate-200 rounded"></div>
                    <div class="h-4 w-8 bg-slate-200 rounded"></div>
                    <div class="h-4 w-8 bg-slate-200 rounded"></div>
                  </div>
                </div>
              </template>
            </div>
          </template>

          <template x-if="usScan">
            <div>
              <template x-if="!usScan.data || usScan.data.length === 0">
                <div class="text-center py-12 text-slate-400">
                  <i data-lucide="inbox" class="w-10 h-10 mx-auto mb-2 opacity-40"></i>
                  <p class="text-sm">최근 레이팅 변화 데이터가 없습니다.</p>
                </div>
              </template>
              <div class="space-y-3">
                <template x-for="item in (usScan.data || [])" :key="item.ticker">
                  <div class="bg-white rounded-xl border border-slate-100 p-4">
                    <div class="flex items-center gap-2 mb-2">
                      <span class="font-bold text-slate-800 text-sm" x-text="item.ticker"></span>
                      <template x-if="item.upgrades > 0">
                        <span class="text-xs px-1.5 py-0.5 rounded bg-green-100 text-green-700 font-medium"
                          x-text="'↑' + item.upgrades"></span>
                      </template>
                      <template x-if="item.downgrades > 0">
                        <span class="text-xs px-1.5 py-0.5 rounded bg-red-100 text-red-700 font-medium"
                          x-text="'↓' + item.downgrades"></span>
                      </template>
                      <span class="text-xs text-slate-400" x-text="item.events + '건'"></span>
                      <button @click="openUsModal(item.ticker)"
                        class="ml-auto text-xs text-blue-500 hover:underline">상세 ›</button>
                    </div>
                    <div class="space-y-1.5">
                      <template x-for="(ev, idx) in (item.latest || []).slice(0, 3)" :key="idx">
                        <div class="flex items-center gap-2 text-xs text-slate-600">
                          <span class="text-slate-400 w-12 shrink-0"
                            x-text="ev.date ? ev.date.slice(5) : ''"></span>
                          <span class="text-slate-600 truncate max-w-24 shrink-0" x-text="ev.firm"></span>
                          <span class="px-1.5 py-0.5 rounded text-xs font-medium shrink-0"
                            :class="usActionBadge(ev.action)"
                            x-text="ev.action"></span>
                          <span class="font-medium shrink-0" x-text="ev.rating_new"></span>
                          <template x-if="ev.pt_now">
                            <span class="text-slate-400 shrink-0" x-text="'TP $' + ev.pt_now"></span>
                          </template>
                        </div>
                      </template>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- ── 톱애널 서브탭 ── -->
      <template x-if="usSeg==='analysts'">
        <div>
          <!-- 스켈레톤 -->
          <template x-if="usAnalystsLoading || !usAnalysts">
            <div class="animate-pulse">
              <div class="hidden md:block bg-white rounded-xl border border-slate-100 overflow-hidden">
                <div class="h-10 bg-slate-100 w-full"></div>
                <template x-for="i in [1,2,3,4,5,6,7,8,9,10]" :key="i">
                  <div class="flex gap-4 p-3 border-b border-slate-50">
                    <div class="h-3 w-6 bg-slate-200 rounded"></div>
                    <div class="h-3 w-24 bg-slate-200 rounded"></div>
                    <div class="h-3 w-20 bg-slate-200 rounded"></div>
                    <div class="h-3 w-12 bg-slate-200 rounded"></div>
                  </div>
                </template>
              </div>
            </div>
          </template>

          <template x-if="!usAnalystsLoading && usAnalysts">
            <div>
              <template x-if="!usAnalysts.analysts || usAnalysts.analysts.length === 0">
                <div class="text-center py-12 text-slate-400">
                  <i data-lucide="user-x" class="w-10 h-10 mx-auto mb-2 opacity-40"></i>
                  <p class="text-sm">톱 애널리스트 데이터가 없습니다.</p>
                </div>
              </template>

              <!-- 데스크탑 테이블 -->
              <div class="hidden md:block bg-white rounded-xl border border-slate-100 overflow-hidden">
                <table class="w-full text-sm">
                  <thead>
                    <tr class="bg-slate-50 border-b border-slate-100">
                      <th class="text-left px-4 py-3 text-xs text-slate-500 font-medium w-10">#</th>
                      <th class="text-left px-4 py-3 text-xs text-slate-500 font-medium">이름</th>
                      <th class="text-left px-4 py-3 text-xs text-slate-500 font-medium">증권사</th>
                      <th class="text-right px-4 py-3 text-xs text-slate-500 font-medium">별점</th>
                      <th class="text-right px-4 py-3 text-xs text-slate-500 font-medium">적중률</th>
                      <th class="text-right px-4 py-3 text-xs text-slate-500 font-medium">콜수</th>
                    </tr>
                  </thead>
                  <tbody>
                    <template x-for="(a, idx) in (usAnalysts.analysts || [])" :key="a.slug || a.analyst">
                      <tr class="border-b border-slate-50 hover:bg-slate-50 transition-colors">
                        <td class="px-4 py-2.5 text-slate-400 text-xs" x-text="idx + 1"></td>
                        <td class="px-4 py-2.5 font-medium text-slate-800" x-text="a.analyst"></td>
                        <td class="px-4 py-2.5 text-slate-600 text-xs" x-text="a.firm"></td>
                        <td class="px-4 py-2.5 text-right">
                          <span class="text-amber-500 font-semibold text-xs">
                            <template x-for="s in Math.round(a.avg_stars)" :key="s">★</template>
                          </span>
                          <span class="text-xs text-slate-400 ml-1" x-text="Number(a.avg_stars).toFixed(1)"></span>
                        </td>
                        <td class="px-4 py-2.5 text-right text-xs"
                          :class="usSuccessColor(a.avg_success_rate)"
                          x-text="Number(a.avg_success_rate).toFixed(1) + '%'"></td>
                        <td class="px-4 py-2.5 text-right text-xs text-slate-600" x-text="a.call_count"></td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>

              <!-- 모바일 카드 -->
              <div class="md:hidden space-y-3">
                <template x-for="(a, idx) in (usAnalysts.analysts || [])" :key="a.slug || a.analyst">
                  <div class="bg-white rounded-xl border border-slate-100 p-4">
                    <div class="flex items-center gap-2 mb-1">
                      <span class="text-xs text-slate-400" x-text="idx + 1 + '.'"></span>
                      <span class="font-medium text-slate-800 text-sm" x-text="a.analyst"></span>
                    </div>
                    <div class="text-xs text-slate-500 mb-2" x-text="a.firm"></div>
                    <div class="flex items-center gap-4 text-xs">
                      <span class="text-amber-500 font-semibold" x-text="'★ ' + Number(a.avg_stars).toFixed(1)"></span>
                      <span :class="usSuccessColor(a.avg_success_rate)"
                        x-text="'적중 ' + Number(a.avg_success_rate).toFixed(1) + '%'"></span>
                      <span class="text-slate-400" x-text="a.call_count + '콜'"></span>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- ── US 종목 상세 모달 ── -->
      <template x-if="usModal">
        <div class="fixed inset-0 z-50 flex items-center justify-center p-4"
          @click.self="closeUsModal()">
          <div class="absolute inset-0 bg-black/40 backdrop-blur-sm"></div>
          <div class="relative bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div class="flex items-center justify-between px-6 py-4 border-b border-slate-100 sticky top-0 bg-white">
              <div class="flex items-center gap-2">
                <i data-lucide="star" class="w-5 h-5 text-amber-500"></i>
                <h2 class="font-bold text-slate-800 text-lg" x-text="usModal.ticker + ' 애널리스트 상세'"></h2>
              </div>
              <button @click="closeUsModal()"
                class="text-slate-400 hover:text-slate-600 text-xl leading-none">&times;</button>
            </div>

            <!-- 로딩 스켈레톤 -->
            <template x-if="usModal.loading">
              <div class="p-6 animate-pulse space-y-4">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <template x-for="i in [1,2,3,4]" :key="i">
                    <div class="h-16 bg-slate-100 rounded-lg"></div>
                  </template>
                </div>
                <div class="h-4 w-32 bg-slate-200 rounded mt-4"></div>
                <template x-for="i in [1,2,3,4,5]" :key="i">
                  <div class="h-10 bg-slate-100 rounded"></div>
                </template>
              </div>
            </template>

            <!-- 모달 컨텐츠 -->
            <template x-if="!usModal.loading">
              <div class="p-6 space-y-6">

                <!-- 컨센서스 요약 4그리드 -->
                <template x-if="usModalConsensus && usModalConsensus.data">
                  <div>
                    <h3 class="text-sm font-semibold text-slate-600 mb-3">컨센서스</h3>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-xs text-slate-400 mb-1">등급</div>
                        <div class="text-sm font-bold text-slate-800"
                          x-text="usModalConsensus.data.consensus_rating || '-'"></div>
                      </div>
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-xs text-slate-400 mb-1">평균 TP</div>
                        <div class="text-sm font-bold text-slate-800"
                          x-text="usModalConsensus.data.target_avg ? '$' + Number(usModalConsensus.data.target_avg).toFixed(0) : '-'"></div>
                      </div>
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-xs text-slate-400 mb-1">커버 수</div>
                        <div class="text-sm font-bold text-slate-800"
                          x-text="(usModalConsensus.data.analyst_count || '-') + '명'"></div>
                      </div>
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-xs text-slate-400 mb-1">기준일</div>
                        <div class="text-xs font-medium text-slate-600"
                          x-text="usModalConsensus.data.snapshot_date || '-'"></div>
                      </div>
                    </div>
                  </div>
                </template>

                <!-- 레이팅 이벤트 테이블 (최대 15) -->
                <template x-if="usModalRatings && usModalRatings.events && usModalRatings.events.length > 0">
                  <div>
                    <h3 class="text-sm font-semibold text-slate-600 mb-3">
                      레이팅 이력 <span class="text-slate-400 text-xs font-normal"
                        x-text="'(' + usModalRatings.count + '건 · 최근 15개)'"></span>
                    </h3>
                    <div class="overflow-x-auto">
                      <table class="w-full text-xs">
                        <thead>
                          <tr class="bg-slate-50 border-b border-slate-100">
                            <th class="text-left px-3 py-2 text-slate-500 font-medium">날짜</th>
                            <th class="text-left px-3 py-2 text-slate-500 font-medium">증권사</th>
                            <th class="text-left px-3 py-2 text-slate-500 font-medium">액션</th>
                            <th class="text-left px-3 py-2 text-slate-500 font-medium">등급</th>
                            <th class="text-right px-3 py-2 text-slate-500 font-medium">TP</th>
                          </tr>
                        </thead>
                        <tbody>
                          <template x-for="(ev, i) in usModalRatings.events.slice(0, 15)" :key="i">
                            <tr class="border-b border-slate-50">
                              <td class="px-3 py-2 text-slate-400" x-text="ev.date"></td>
                              <td class="px-3 py-2 text-slate-600 max-w-28 truncate" x-text="ev.firm"></td>
                              <td class="px-3 py-2">
                                <span class="px-1.5 py-0.5 rounded text-xs font-medium"
                                  :class="usActionBadge(ev.action)"
                                  x-text="ev.action"></span>
                              </td>
                              <td class="px-3 py-2 font-medium text-slate-800" x-text="ev.rating_new || '-'"></td>
                              <td class="px-3 py-2 text-right text-slate-600"
                                x-text="ev.pt_now ? '$' + ev.pt_now : '-'"></td>
                            </tr>
                          </template>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </template>

                <!-- FMP TP Summary (연구 데이터) -->
                <template x-if="usModalResearch && !usModalResearch.error && usModalResearch.price_target_summary">
                  <div>
                    <h3 class="text-sm font-semibold text-slate-600 mb-3">FMP Price Target</h3>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                      <div class="bg-blue-50 rounded-lg p-3 text-center">
                        <div class="text-slate-400 mb-1">최근 1개월</div>
                        <div class="font-bold text-slate-800"
                          x-text="usModalResearch.price_target_summary.last_month ? '$' + Number(usModalResearch.price_target_summary.last_month.avg_target).toFixed(0) : '-'"></div>
                        <div class="text-slate-400"
                          x-text="usModalResearch.price_target_summary.last_month ? usModalResearch.price_target_summary.last_month.count + '명' : ''"></div>
                      </div>
                      <div class="bg-blue-50 rounded-lg p-3 text-center">
                        <div class="text-slate-400 mb-1">최근 1분기</div>
                        <div class="font-bold text-slate-800"
                          x-text="usModalResearch.price_target_summary.last_quarter ? '$' + Number(usModalResearch.price_target_summary.last_quarter.avg_target).toFixed(0) : '-'"></div>
                        <div class="text-slate-400"
                          x-text="usModalResearch.price_target_summary.last_quarter ? usModalResearch.price_target_summary.last_quarter.count + '명' : ''"></div>
                      </div>
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-slate-400 mb-1">최근 1년</div>
                        <div class="font-bold text-slate-800"
                          x-text="usModalResearch.price_target_summary.last_year ? '$' + Number(usModalResearch.price_target_summary.last_year.avg_target).toFixed(0) : '-'"></div>
                        <div class="text-slate-400"
                          x-text="usModalResearch.price_target_summary.last_year ? usModalResearch.price_target_summary.last_year.count + '명' : ''"></div>
                      </div>
                      <div class="bg-slate-50 rounded-lg p-3 text-center">
                        <div class="text-slate-400 mb-1">전체</div>
                        <div class="font-bold text-slate-800"
                          x-text="usModalResearch.price_target_summary.all_time ? '$' + Number(usModalResearch.price_target_summary.all_time.avg_target).toFixed(0) : '-'"></div>
                        <div class="text-slate-400"
                          x-text="usModalResearch.price_target_summary.all_time ? usModalResearch.price_target_summary.all_time.count + '명' : ''"></div>
                      </div>
                    </div>
                  </div>
                </template>

                <!-- 빈 상태 -->
                <template x-if="!usModalRatings && !usModalConsensus && !usModalResearch">
                  <div class="text-center py-8 text-slate-400 text-sm">데이터를 불러오지 못했습니다.</div>
                </template>

              </div>
            </template>
          </div>
        </div>
      </template>

    </section>
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P3a: Whale 탭 패널 HTML (반드시 _HOME_SHELL 이전 정의)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# NOTE: 모든 JS 문자열 리터럴 안 개행은 \\n 사용. 표현식은 중괄호 이스케이프 불필요(raw 문자열).
_WHALE_PANEL = r"""
    <!-- Whale 탭 -->
    <section x-show="activeTab==='whale'" x-cloak
             x-data="{
               wTab: 'pension',
               wCache: {},
               wData: null,
               wLoading: false,
               async wLoad(p) {
                 if (this.wCache[p]) { this.wData = this.wCache[p]; return; }
                 this.wLoading = true;
                 this.wData = null;
                 const d = await (async path => {
                   try { const r = await fetch(path); return await r.json(); }
                   catch(e) { return {error: String(e)}; }
                 })('/api/whale?p=' + p);
                 this.wCache[p] = d;
                 this.wData = d;
                 this.wLoading = false;
                 this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
               },
               setWTab(p) {
                 this.wTab = p;
                 this.wLoad(p);
               }
             }"
             x-init="wLoad(wTab)">

      <!-- 서브탭 바 -->
      <div class="flex flex-wrap gap-2 mb-5">
        <template x-for="tab in [
          {key:'pension', label:'연기금 흐름'},
          {key:'kr_5pct', label:'KR 5%룰'},
          {key:'kr_full', label:'KR 풀포트'},
          {key:'us_13f',  label:'US 13F'},
          {key:'insider', label:'내부자'}
        ]" :key="tab.key">
          <button @click="setWTab(tab.key)"
                  :class="wTab===tab.key ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'"
                  class="px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                  x-text="tab.label">
          </button>
        </template>
      </div>

      <!-- 로딩 스켈레톤 -->
      <template x-if="wLoading">
        <div class="animate-pulse">
          <template x-for="i in [1,2,3,4,5,6,7,8]" :key="i">
            <div class="flex items-center gap-3 py-2.5 border-b border-slate-100 last:border-0">
              <div class="h-3 w-6 bg-slate-200 rounded shrink-0"></div>
              <div class="flex-1">
                <div class="h-3 w-32 bg-slate-200 rounded mb-1"></div>
                <div class="h-3 w-20 bg-slate-200 rounded"></div>
              </div>
              <div class="h-4 w-16 bg-slate-200 rounded ml-auto"></div>
              <div class="h-4 w-14 bg-slate-200 rounded"></div>
            </div>
          </template>
        </div>
      </template>

      <!-- 연기금 흐름 -->
      <template x-if="!wLoading && wTab==='pension' && wData">
        <div>
          <template x-if="wData.error">
            <div class="text-red-500 text-sm py-4" x-text="'오류: ' + wData.error"></div>
          </template>
          <template x-if="!wData.error">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="'기간: ' + (wData.period || '-') + ' | 5일 누적 순매매 시총% 정규화'"></p>
              <h3 class="text-sm font-semibold text-green-600 mb-2">매수 TOP 50</h3>
              <template x-if="!wData.buy_top || !wData.buy_top.length">
                <div class="text-slate-400 text-sm py-2">매수 없음</div>
              </template>
              <template x-if="wData.buy_top && wData.buy_top.length">
                <div class="overflow-x-auto mb-6">
                  <table class="w-full text-sm border-collapse">
                    <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                      <th class="text-left pb-2 font-medium">#</th>
                      <th class="text-left pb-2 font-medium">종목</th>
                      <th class="text-right pb-2 font-medium">순매수</th>
                      <th class="text-right pb-2 font-medium">시총%</th>
                    </tr></thead>
                    <tbody>
                      <template x-for="(e, idx) in wData.buy_top" :key="e.symbol + idx">
                        <tr class="border-b border-slate-100 hover:bg-slate-50">
                          <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                          <td class="py-1.5">
                            <span class="font-medium text-slate-800" x-text="e.name"></span>
                            <span class="text-xs text-slate-400 ml-1" x-text="e.symbol"></span>
                          </td>
                          <td class="py-1.5 text-right text-green-600 font-semibold"
                              x-text="e.net_eok != null ? '+' + e.net_eok.toFixed(0) + '억' : '-'"></td>
                          <td class="py-1.5 text-right text-green-600"
                              x-text="e.cap_pct != null ? '+' + e.cap_pct.toFixed(2) + '%' : '-'"></td>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
              </template>
              <h3 class="text-sm font-semibold text-red-600 mb-2">매도 TOP 50</h3>
              <template x-if="!wData.sell_top || !wData.sell_top.length">
                <div class="text-slate-400 text-sm py-2">매도 없음</div>
              </template>
              <template x-if="wData.sell_top && wData.sell_top.length">
                <div class="overflow-x-auto">
                  <table class="w-full text-sm border-collapse">
                    <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                      <th class="text-left pb-2 font-medium">#</th>
                      <th class="text-left pb-2 font-medium">종목</th>
                      <th class="text-right pb-2 font-medium">순매도</th>
                      <th class="text-right pb-2 font-medium">시총%</th>
                    </tr></thead>
                    <tbody>
                      <template x-for="(e, idx) in wData.sell_top" :key="e.symbol + idx">
                        <tr class="border-b border-slate-100 hover:bg-slate-50">
                          <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                          <td class="py-1.5">
                            <span class="font-medium text-slate-800" x-text="e.name"></span>
                            <span class="text-xs text-slate-400 ml-1" x-text="e.symbol"></span>
                          </td>
                          <td class="py-1.5 text-right text-red-600 font-semibold"
                              x-text="e.net_eok != null ? e.net_eok.toFixed(0) + '억' : '-'"></td>
                          <td class="py-1.5 text-right text-red-600"
                              x-text="e.cap_pct != null ? e.cap_pct.toFixed(2) + '%' : '-'"></td>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
              </template>
            </div>
          </template>
        </div>
      </template>

      <!-- KR 5%룰 -->
      <template x-if="!wLoading && wTab==='kr_5pct' && wData">
        <div>
          <template x-if="wData[0] && wData[0].error">
            <div class="text-red-500 text-sm" x-text="wData[0].error"></div>
          </template>
          <template x-if="wData.length && !(wData[0] && wData[0].error)">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="wData[0] ? wData[0].quarter + ' | 총 ' + wData.length + '건 | 10%+ 빨강' : ''"></p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                    <th class="text-left pb-2 font-medium">#</th>
                    <th class="text-left pb-2 font-medium">보고일</th>
                    <th class="text-left pb-2 font-medium">종목</th>
                    <th class="text-right pb-2 font-medium">지분%</th>
                    <th class="text-right pb-2 font-medium">전분기</th>
                  </tr></thead>
                  <tbody>
                    <template x-for="(r, idx) in wData" :key="r.symbol + r.report_date + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5 text-xs text-slate-500" x-text="r.report_date"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="r.company_name"></span>
                          <span class="text-xs text-slate-400 ml-1" x-text="r.symbol"></span>
                        </td>
                        <td class="py-1.5 text-right"
                            :class="r.ratio_pct >= 10 ? 'text-red-600 font-bold' : 'text-slate-700'"
                            x-text="r.ratio_pct != null ? r.ratio_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-xs">
                          <template x-if="r.change_label === 'NEW'">
                            <span class="text-green-600 font-semibold">NEW</span>
                          </template>
                          <template x-if="r.change_label === 'UP' && r.change != null">
                            <span class="text-green-600">+<span x-text="r.change.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="r.change_label === 'DOWN' && r.change != null">
                            <span class="text-red-500"><span x-text="r.change.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="r.change_label === 'FLAT' || r.change_label === ''">
                            <span class="text-slate-400">—</span>
                          </template>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
          <template x-if="!wData.length">
            <div class="text-slate-400 text-sm py-4">데이터 없음</div>
          </template>
        </div>
      </template>

      <!-- KR 풀포트 -->
      <template x-if="!wLoading && wTab==='kr_full' && wData">
        <div>
          <template x-if="wData.error">
            <div class="text-red-500 text-sm py-4" x-text="'오류: ' + wData.error"></div>
          </template>
          <template x-if="!wData.error">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="(wData.quarter_label || '-') + ' | 스냅샷 ' + (wData.snapshot_date || '-') + ' | 총 ' + (wData.total_holdings || 0) + '종목'"></p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                    <th class="text-left pb-2 font-medium">#</th>
                    <th class="text-left pb-2 font-medium">종목</th>
                    <th class="text-right pb-2 font-medium">비중%</th>
                    <th class="text-right pb-2 font-medium">평가액</th>
                    <th class="text-right pb-2 font-medium">지분%</th>
                    <th class="text-right pb-2 font-medium">전년대비</th>
                  </tr></thead>
                  <tbody>
                    <template x-for="(x, idx) in (wData.rows || [])" :key="(x.symbol || x.name) + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="x.name"></span>
                          <span class="text-xs text-slate-400 ml-1" x-text="x.symbol"></span>
                        </td>
                        <td class="py-1.5 text-right text-slate-700"
                            x-text="x.weight_pct != null ? x.weight_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-slate-600 text-xs"
                            x-text="x.valuation_eok != null ? x.valuation_eok.toLocaleString('ko-KR') + '억' : '-'"></td>
                        <td class="py-1.5 text-right"
                            :class="x.share_curr_pct >= 10 ? 'text-red-600 font-bold' : 'text-slate-600'"
                            x-text="x.share_curr_pct != null ? x.share_curr_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-xs">
                          <template x-if="x.data_missing || x.share_change_p == null">
                            <span class="text-slate-400">—</span>
                          </template>
                          <template x-if="!x.data_missing && x.share_change_p != null && x.share_change_p > 0.05">
                            <span class="text-green-600">+<span x-text="x.share_change_p.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="!x.data_missing && x.share_change_p != null && x.share_change_p < -0.05">
                            <span class="text-red-500"><span x-text="x.share_change_p.toFixed(2)"></span>p</span>
                          </template>
                          <template x-if="!x.data_missing && x.share_change_p != null && x.share_change_p >= -0.05 && x.share_change_p <= 0.05">
                            <span class="text-slate-400">—</span>
                          </template>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- US 13F -->
      <template x-if="!wLoading && wTab==='us_13f' && wData">
        <div>
          <template x-if="wData.error">
            <div class="text-red-500 text-sm py-4" x-text="'오류: ' + wData.error"></div>
          </template>
          <template x-if="!wData.error">
            <div>
              <p class="text-xs text-slate-400 mb-4"
                 x-text="(wData.quarter || '-') + ' | 분기말 ' + (wData.period_end || '-') + ' | TOP 100 / ' + (wData.total_holdings || 0) + '종목'"></p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                    <th class="text-left pb-2 font-medium">#</th>
                    <th class="text-left pb-2 font-medium">종목</th>
                    <th class="text-right pb-2 font-medium">가치</th>
                    <th class="text-right pb-2 font-medium">비중%</th>
                    <th class="text-right pb-2 font-medium">주식변화</th>
                  </tr></thead>
                  <tbody>
                    <template x-for="(x, idx) in (wData.rows || [])" :key="(x.cusip || x.name_of_issuer) + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="x.name_of_issuer || '-'"></span>
                          <template x-if="x.ticker">
                            <span class="text-xs text-slate-400 ml-1" x-text="x.ticker"></span>
                          </template>
                        </td>
                        <td class="py-1.5 text-right text-slate-700 text-xs"
                            x-text="x.value_usd != null ? (x.value_usd >= 1e9 ? '$' + (x.value_usd/1e9).toFixed(2) + 'B' : '$' + (x.value_usd/1e6).toFixed(0) + 'M') : '-'"></td>
                        <td class="py-1.5 text-right text-slate-600 text-xs"
                            x-text="x.weight_pct != null ? x.weight_pct.toFixed(2) + '%' : '-'"></td>
                        <td class="py-1.5 text-right text-xs">
                          <template x-if="x.status === 'NEW'">
                            <span class="text-green-600 font-semibold">NEW</span>
                          </template>
                          <template x-if="x.status === 'UP' && x.share_change_pct != null">
                            <span class="text-green-600">+<span x-text="x.share_change_pct.toFixed(1)"></span>%</span>
                          </template>
                          <template x-if="x.status === 'DOWN' && x.share_change_pct != null">
                            <span class="text-red-500"><span x-text="x.share_change_pct.toFixed(1)"></span>%</span>
                          </template>
                          <template x-if="!x.status || (x.status !== 'NEW' && x.status !== 'UP' && x.status !== 'DOWN')">
                            <span class="text-slate-400">—</span>
                          </template>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
        </div>
      </template>

      <!-- 내부자 -->
      <template x-if="!wLoading && wTab==='insider' && wData">
        <div>
          <template x-if="wData[0] && wData[0].error">
            <div class="text-red-500 text-sm py-4" x-text="wData[0].error"></div>
          </template>
          <template x-if="wData.length && !(wData[0] && wData[0].error)">
            <div>
              <p class="text-xs text-slate-400 mb-4">
                최근 90일 | 5%+ 주요주주·임원 | <span x-text="wData.length + '건'"></span>
              </p>
              <div class="overflow-x-auto">
                <table class="w-full text-sm border-collapse">
                  <thead><tr class="text-xs text-slate-400 border-b border-slate-200">
                    <th class="text-left pb-2 font-medium">#</th>
                    <th class="text-left pb-2 font-medium">보고일</th>
                    <th class="text-left pb-2 font-medium">종목</th>
                    <th class="text-left pb-2 font-medium">보고자</th>
                    <th class="text-right pb-2 font-medium">증감</th>
                    <th class="text-right pb-2 font-medium">지분%</th>
                  </tr></thead>
                  <tbody>
                    <template x-for="(r, idx) in wData" :key="(r.rcept_dt || '') + (r.symbol || '') + idx">
                      <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="py-1.5 text-slate-400 text-xs" x-text="idx+1"></td>
                        <td class="py-1.5 text-xs text-slate-500" x-text="r.rcept_dt"></td>
                        <td class="py-1.5">
                          <span class="font-medium text-slate-800" x-text="r.company_name || ''"></span>
                          <span class="text-xs text-slate-400 ml-1" x-text="r.symbol"></span>
                        </td>
                        <td class="py-1.5 text-xs">
                          <span class="text-slate-700" x-text="r.repror"></span>
                          <span class="text-slate-400 ml-1" x-text="r.role"></span>
                        </td>
                        <td class="py-1.5 text-right font-semibold"
                            :class="r.direction === 'buy' ? 'text-green-600' : 'text-red-500'"
                            x-text="r.irds_cnt != null ? (r.irds_cnt > 0 ? '+' : '') + r.irds_cnt.toLocaleString('ko-KR') : '-'"></td>
                        <td class="py-1.5 text-right text-xs"
                            :class="r.stock_rate >= 10 ? 'text-red-600 font-bold' : 'text-slate-600'"
                            x-text="r.stock_rate != null ? r.stock_rate.toFixed(2) + '%' : '-'"></td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
          <template x-if="!wData.length">
            <div class="text-slate-400 text-sm py-4">최근 90일 5%+ 보유자 매매 없음</div>
          </template>
        </div>
      </template>

    </section>
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 홈 패널 HTML (Alpine 템플릿)
# 완전히 별도 문자열로 분리 — JS 중괄호와 충돌 없음.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_HOME_PANEL = (
    '    <!-- 홈 패널 -->\n'
    '    <section x-show="activeTab===\'home\'">\n'
    '\n'
    '      <!-- 로딩 스켈레톤 (최초 1회만) -->\n'
    '      <template x-if="!home">\n'
    '        <div class="animate-pulse">\n'
    '          <!-- 지수 칩 x4 -->\n'
    '          <div class="flex gap-3 overflow-x-auto pb-1 mb-5">\n'
    '            <template x-for="i in [1,2,3,4]" :key="i">\n'
    '              <div class="flex-shrink-0 bg-white rounded-xl border border-slate-100 px-4 py-3 min-w-[110px] flex flex-col gap-2">\n'
    '                <div class="h-2.5 w-14 bg-slate-200 rounded"></div>\n'
    '                <div class="h-5 w-20 bg-slate-200 rounded"></div>\n'
    '                <div class="h-3 w-10 bg-slate-200 rounded"></div>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '          <!-- 자산 요약 카드 -->\n'
    '          <div class="bg-white rounded-xl border border-slate-100 p-5 mb-6">\n'
    '            <div class="h-3 w-24 bg-slate-200 rounded mb-4"></div>\n'
    '            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">\n'
    '              <template x-for="j in [1,2,3,4]" :key="j">\n'
    '                <div>\n'
    '                  <div class="h-2.5 w-16 bg-slate-200 rounded mb-2"></div>\n'
    '                  <div class="h-6 w-24 bg-slate-200 rounded mb-1"></div>\n'
    '                  <div class="h-3 w-20 bg-slate-200 rounded"></div>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </div>\n'
    '          <!-- 신호 카드 그리드 x3 -->\n'
    '          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">\n'
    '            <template x-for="k in [1,2,3]" :key="k">\n'
    '              <div class="bg-white rounded-xl border border-slate-100 p-4">\n'
    '                <div class="h-3 w-24 bg-slate-200 rounded mb-3"></div>\n'
    '                <template x-for="m in [1,2,3]" :key="m">\n'
    '                  <div class="flex items-center gap-3 py-2.5 border-b border-slate-50 last:border-0">\n'
    '                    <div class="rounded-full h-8 w-8 bg-slate-200 shrink-0"></div>\n'
    '                    <div class="flex-1">\n'
    '                      <div class="h-3 w-32 bg-slate-200 rounded mb-1"></div>\n'
    '                      <div class="h-3 w-20 bg-slate-200 rounded"></div>\n'
    '                    </div>\n'
    '                    <div class="ml-auto h-4 w-14 bg-slate-200 rounded"></div>\n'
    '                  </div>\n'
    '                </template>\n'
    '              </div>\n'
    '            </template>\n'
    '          </div>\n'
    '        </div>\n'
    '      </template>\n'
    '\n'
    '      <template x-if="home">\n'
    '        <div>\n'
    '\n'
    '          <!-- 지수 띠 -->\n'
    '          <template x-if="home.indices && home.indices.length">\n'
    '            <div class="flex gap-3 overflow-x-auto pb-1 mb-5 scrollbar-hide">\n'
    '              <template x-for="idx in home.indices" :key="idx.name">\n'
    '                <div class="flex-shrink-0 bg-white rounded-xl shadow-sm border border-slate-200 px-4 py-3 flex flex-col min-w-[110px]">\n'
    '                  <span class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-0.5" x-text="idx.name"></span>\n'
    '                  <span class="text-base font-bold text-slate-800"\n'
    '                        x-text="idx.price != null ? idx.price.toLocaleString(\'ko-KR\', {maximumFractionDigits:2}) : \'-\'"></span>\n'
    '                  <span :class="chgClass(idx.change_pct)" class="text-xs font-semibold mt-0.5"\n'
    '                        x-text="chgStr(idx.change_pct)"></span>\n'
    '                </div>\n'
    '              </template>\n'
    '            </div>\n'
    '          </template>\n'
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
    '                <div class="text-slate-700 mb-2" x-text="home.dart.label || (home.dart.count + \'건 누적 감지\')"></div>\n'
    '                <button @click="setTab(\'signal\'); signalSeg=\'dart\'"\n'
    '                  class="text-xs text-blue-600 hover:text-blue-700 hover:underline">\n'
    '                  시그널 탭에서 보기\n'
    '                </button>\n'
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
    '  <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>\n'
    '  <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>\n'
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
    '  <!-- 탭 네비 (9개) -->\n'
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
    '          <!-- 시세 -->\n'
    '          <button\n'
    '            @click="setTab(\'market\')"\n'
    '            :class="activeTab===\'market\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="trending-up" class="w-4 h-4"></i>\n'
    '            시세\n'
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
    '          <!-- US 애널 -->\n'
    '          <button\n'
    '            @click="setTab(\'us\')"\n'
    '            :class="activeTab===\'us\' ? \'bg-blue-600 text-white\' : \'text-slate-600 hover:bg-slate-100\'"\n'
    '            class="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors"\n'
    '          >\n'
    '            <i data-lucide="star" class="w-4 h-4"></i>\n'
    '            US 애널\n'
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
    + _MARKET_PANEL
    + _PORTFOLIO_PANEL
    + _WATCH_PANEL
    + _SIGNAL_PANEL
    + _RECORD_PANEL
    + _US_PANEL
    + _WHALE_PANEL
    + _REPORT_PANEL
    + '\n'
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
