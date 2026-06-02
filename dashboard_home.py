"""dashboard_home — 새 대시보드 (P0 골격).

/home 경로에 서빙. /dash(dashboard.py)는 무수정.
P0: HTML 쉘 + Alpine 탭 네비 + 빈 패널. 데이터 없음.
P1~: /api/* JSON 엔드포인트 추가 예정.
"""

from aiohttp import web

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Alpine dashApp JS (인라인 <script> 본문)
# Python 문자열 안에 들어가므로 JS 문자열 리터럴 내
# 제어문자는 쓰지 않음 — \n 버그 방지.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_DASH_APP_JS = """
function dashApp() {
  return {
    activeTab: 'home',
    loading: false,
    lastUpdated: '',
    regimeLabel: '',

    init() {
      this.refreshIcons();
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
      return '$' + Number(n).toFixed(2);
    }
  };
}
"""

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
    '  <!-- ━━ 상단 sticky 바 ━━ -->\n'
    '  <header class="sticky top-0 z-50 bg-white border-b border-slate-200 shadow-sm">\n'
    '    <div class="max-w-6xl mx-auto px-4 flex items-center justify-between h-12">\n'
    '      <div class="flex items-center gap-2">\n'
    '        <span class="text-lg font-bold text-slate-800">\U0001f4ca Stock Bot</span>\n'
    '        <span x-text="regimeLabel" class="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-500"></span>\n'
    '      </div>\n'
    '      <div class="flex items-center gap-3">\n'
    '        <span x-text="lastUpdated" class="text-xs text-slate-400"></span>\n'
    '        <!-- 자동갱신 토글 placeholder (P1에서 구현) -->\n'
    '        <button class="text-xs px-2 py-1 rounded border border-slate-200 text-slate-500 hover:bg-slate-50">자동갱신</button>\n'
    '      </div>\n'
    '    </div>\n'
    '  </header>\n'
    '\n'
    '  <!-- ━━ 탭 네비 (7개) ━━ -->\n'
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
    '  <!-- ━━ 탭 패널 ━━ -->\n'
    '  <main class="max-w-6xl mx-auto px-4 py-6">\n'
    '\n'
    '    <!-- 홈 -->\n'
    '    <section x-show="activeTab===\'home\'">\n'
    '      <div class="text-slate-400 text-center py-20">홈 (P1에서 구현)</div>\n'
    '    </section>\n'
    '\n'
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
# 라우트 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _handle_home(request: web.Request) -> web.Response:
    return web.Response(text=_HOME_SHELL, content_type="text/html")


def register_home_routes(app: web.Application) -> None:
    app.router.add_get("/home", _handle_home)
