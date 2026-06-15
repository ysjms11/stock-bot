# CLAUDE.md — stock-bot 프로젝트 가이드

## 🚨 세션 시작 루틴 (필수)

매 세션 시작 시 **반드시** `data/PROGRESS.md`를 가장 먼저 읽을 것. 다음 세션이 바로 이어갈 수 있도록 설계된 인수인계 문서임. Anthropic "Effective harnesses for long-running agents" 패턴.

```
1. pwd
2. git log --oneline -10
3. cat data/PROGRESS.md      ← 가장 중요
4. cat data/TODO_dev.md      (봇 개발)
5. cat data/TODO_invest.md   (투자, 필요시)
```

세션 종료 시 `PROGRESS.md`의 "다음 세션에서 할 일" 갱신 필수.

---

## KIS API 참조

`kis-api-ref/` 폴더에 한투 공식 API 샘플 (data.csv 6326행, examples_llm/). TR_ID/파라미터 확인 시 참조.
상세 TR_ID 테이블 → `.claude/rules/kis-api-reference.md`

## 인프라

| 항목 | 내용 |
|------|------|
| 레포 | https://github.com/ysjms11/stock-bot |
| 배포 | 맥미니 M4 (192.168.0.36), launchd 자동시작 |
| MCP URL | `https://bot.arcbot-server.org/mcp` (SSE) |
| MCP messages | `https://bot.arcbot-server.org/mcp/messages?sessionId=<id>` (POST) |
| Health check | `https://bot.arcbot-server.org/health` |
| 웹 대시보드 | `https://bot.arcbot-server.org/home` (구 `/dash`·`/dash-v2`→/home 302 리다이렉트, 옛 버전=`/dash-classic`) (Cloudflare Access Gmail PIN 인증 · TODO 토글/추가 + 투자판단 폼) |
| Cloudflare Tunnel | `com.stock-bot.cloudflared` (launchd) |
| 도메인 | `arcbot-server.org` |
| 포트 | 환경변수 `PORT` (기본 8080) |

**필수 환경변수**

```
TELEGRAM_TOKEN   텔레그램 봇 토큰
CHAT_ID          텔레그램 채팅 ID
KIS_APP_KEY      KIS Open API 앱키
KIS_APP_SECRET   KIS Open API 시크릿
DART_API_KEY     전자공시 API 키 (선택)
KRX_API_KEY      KRX OPEN API 인증키 (db_collector가 18:30 사용)
GITHUB_TOKEN     GitHub Gist 백업용 토큰 (선택)
BACKUP_GIST_ID   백업 Gist ID (선택)
DATA_DIR         데이터 디렉토리 경로 (/Users/kreuzer/stock-bot/data)
```

---

## 파일 구조

⚠️ **2026-05 리팩터: 단일 `kis_api.py`/`mcp_tools.py`/`main.py` → 패키지로 분리됨.** 옛 단일파일을 직접 참조(`open("mcp_tools.py")`, `from main import X` 등)하는 코드·테스트·문서는 stale — **파일시스템을 믿을 것**(`ls kis_api/ mcp_tools/ main_pkg/`). (참고: 이 표/아래 `.claude/rules/*`도 구조 기술이 갱신 중일 수 있음.)

| 모듈 | 형태 | 역할 |
|------|------|------|
| `kis_api/` | 패키지(23) | KIS/DART/Yahoo API, 데이터 I/O, WebSocket, 매크로, 백업, 미국 애널, NPS/13F. 기반: `_config`·`_session`·`_files`·`_helpers`·`_db`. 도메인: `kr_stock`·`us_stock`·`consensus`·`regime`·`news`·`macro`·`dart`·`fmp`·`polymarket`·`pension`·`portfolio`·`ranks`·`sec_edgar`·`universe`·`us_ratings`·`backup`·`websocket`. `from kis_api import *`로 공개 API 노출 |
| `mcp_tools/` | 패키지 | `__init__`=`MCP_TOOLS` 스키마 배열(47개), `_registry`=`TOOL_HANDLERS` dict + `execute_tool`(elif 체인 폐기), `_execute`=`_execute_tool` 래퍼, `server`=JSON-RPC/SSE, `tools/*.py`=도구별 핸들러(20). 각 핸들러는 `from kis_api import *` |
| `main.py` + `main_pkg/` | shim + 패키지 | `main.py`(~7줄)=진입점 shim. 로직은 `main_pkg/`: `telegram_bot`·`_entry`·`_ctx`·`schedule` + `jobs/`(25 잡파일, US애널/sanity 포함). 텔레그램 봇 + 자동알림 스케줄 |
| `db_collector/` | 패키지(14) | KIS+KRX 풀수집·SQLite·기술지표·스캐너·F/M/FCF·US애널 (2026-06 분해). `_db`=`db_write_lock`·`_get_db`, `collect`=일일 파이프라인, `scan`·`technicals`·`financial`·`alpha` 등. `__init__` 프록시가 `setattr(db_collector, X)`를 백킹 모듈 전체로 전파(monkeypatch 투명성 — 서브모듈 직접 패치 금지). 상세 → `.claude/rules/file-structure.md` |
| `krx_crawler.py` | 단일파일(~1500) | db_collector 호환 wrapper (레거시 fallback) |
| `dashboard.py` | 단일파일(~3700) | 구 `/dash` 웹 대시보드 (HTML 렌더링) |
| `dashboard_home/` | 패키지(7) | 신 `/home` 대시보드 (2026-06 재구축→분해). `_assets`=템플릿/JS 상수+`_HOME_SHELL` 조립(sha256 골든 동결), `payloads`=빌더, `routes`=핸들러+`register_home_routes`+`warm_caches`, `reports`/`whale`/`_helpers`(SWR 캐시). 표면=`register_home_routes`·`warm_caches` 2심볼 |
| `report_crawler.py` | 단일파일(~1400) | 증권사 리포트 수집 (한경컨센서스→네이버리서치→와이즈 메타 우선순위 통합) + reports DB. 08:30 `report_collect` 잡 본체 + `read_report_pdf` 소스 |

기타 파일:

| 파일 | 내용 |
|------|------|
| `stock_universe.json` | 종목 유니버스 (시총 상위 코스피+코스닥) |
| `dart_corp_map.json` | DART 고유번호 ↔ 종목코드 매핑 |
| `tests/`(14) + 루트 `test_*.py`(25) + `conftest.py` + `pytest.ini` | pytest 스위트. asyncio_mode=auto, live 마커는 기본 skip(`--run-live`로 실행), 루트 conftest가 /data→/tmp redirect, characterization 골든(dashboard_home·db_collector) 포함 |
| `requirements.txt` | Python 의존성 |

---

## 데이터 파일 경로

핵심 파일만 본체에 기록:
- `data/stock.db` — SQLite DB (~450MB, 성장 중. 주요 테이블: stock_master + daily_snapshot + financial_quarterly + consensus_history + reports + insider_transactions 외 — 전체 20테이블, 권위 값은 sqlite_master)
- `data/*.json` — 워치/포트/손절/알림 등 상태 파일 (전체 목록 → `.claude/rules/data-files.md`)
- `data/db_schema.sql` — SQLite 스키마 정의

---

## MCP 도구 (47개)

스키마 배열 `MCP_TOOLS` → `mcp_tools/__init__.py`. 디스패치 `TOOL_HANDLERS` dict + `execute_tool()` → `mcp_tools/_registry.py` (구 `_execute_tool` elif 체인 폐기). 도구별 핸들러 → `mcp_tools/tools/*.py`.
전체 도구 목록/모드/파라미터 → `.claude/rules/mcp-tools.md`

---

## 새 MCP 도구 추가

절차 (API 함수 작성 → `MCP_TOOLS` 스키마 추가 → `tools/<mod>.py` 핸들러 작성 → `_registry.TOOL_HANDLERS`에 등록 → 커밋):
→ `.claude/rules/add-mcp-tool.md` 참조

---

## 자동 스케줄 (50건 등록)

전체 타임라인/의존성/신규 잡 추가 절차 → `.claude/rules/schedule.md` (권위 값은 `main_pkg/schedule.py` 등록부 — `jq.run_daily` 46 + `jq.run_repeating` 4, 표기는 갱신 지연 가능)

---

## 알려진 이슈

**🔴 버그 함정**
- **미국 현재가 `rate` 필드**: KIS 해외 응답은 `rate`. `diff_rate` 없음. 전 코드 `rate` 통일됨.
- **WebSocket 국내 전용**: `KisRealtimeManager`는 국내만. 미국은 Yahoo Finance 폴링 (`check_stoploss`).
- **KRX OPEN API 간헐 장애**: 자주 빈 응답. `db_collector`가 `stock_master` fallback으로 KIS API 직접 호출.

**🟢 해결됨 (2026-06 확인)**
- `get_change_scan` 프리셋 16종 전부 구현(`mcp_tools/tools/scan.py`)·데이터 성숙. 공매도/외인보유 필드 2026-04-13~, 신용잔고율(`loan_balance_rate`) 2026-04-16~ 수집. `short_squeeze`/`credit_unwind`/`foreign_accumulation` 모두 라이브 결과 반환 (2026-06-10 기준 matched 1098/1114/82).

**🟡 아키텍처 노트**
- 로컬 DATA_DIR + Gist 백업
- KIS 토큰 캐시 23시간 (메모리+파일)
- Yahoo Finance fallback (미국 장 요약 + `check_stoploss`)
- DST 자동 감지 (`zoneinfo`)
- 섹터 2중 구조 (`sector`=실용 분류, `sector_krx`=KRX 원본, `std_sector_map.json` 캐시)

---

## 코딩 규칙

- **모듈 경계**: API/데이터 → `kis_api/`, 텔레그램+스케줄 → `main_pkg/`, 웹 대시보드 → `dashboard.py`(/dash-classic)·`dashboard_home/`(/home 패키지), MCP → `mcp_tools/`, KIS 배치 수집+SQLite → `db_collector/`, krx 호환 wrapper → `krx_crawler.py`. **dashboard.py 는 main 만 import** (단방향, shadow trap 방지).
- **KIS API 신 방식**: 새 함수는 반드시 `_kis_get()` 래퍼 사용 (구 방식 `get_stock_price()` 패턴 사용 금지).
- **에러 처리**: 개별 종목 루프 내부는 `try/except Exception: pass` 패턴으로 한 종목 오류가 전체 중단 방지.
- **KIS API rate limit**: 초당 10건 제한. 연속 호출 시 `await asyncio.sleep(0.3)` 삽입 (실사용 초당 ~3.3건).
- **섹션 구분**: `# ━━━━━━━━━━━━━━━━━━━━━━━━━` 주석으로 논리적 섹션 구분 유지.
- **한국어 변수명**: 텔레그램 메시지 문자열 외에는 영문 변수명 사용.
- **MCP 도구 등록**: `MCP_TOOLS` 배열과 `_registry.TOOL_HANDLERS` dict 1:1 매칭 유지 (`test_mcp_schema.py`가 자동 검증).
- **import 패턴**: `kis_api` 패키지 표면에서 `from kis_api import *` + 명시적 private 함수 import (예: `from kis_api import (_kis_get, _DATA_DIR, ...)`). `mcp_tools/tools/*` 핸들러·`mcp_tools/_helpers`·`main_pkg/` 모듈 동일 (예외: `tools/sec.py`는 `kis_api.sec_edgar` 서브모듈 직접 import).

---

## Token Optimization Rules

1. Trust skills/memory — skip re-reading files already in context
2. No speculative tool calls — only call tools when result is needed
3. Parallelize independent tool calls when possible
4. Route output > 20 lines to subagents
5. Never restate what the user already said

---

## Agent Team

모든 코드 작업은 팀 구조로. 상세 프롬프트는 `.claude/agents/*.md` 참조.

**Advisor Pattern** (5/9 학습 #33 — 다수 의견 채택):
- **Opus** = critic / code-reviewer / verifier (깊은 갭 분석, "cheaper models miss" 잡기)
- **Sonnet** = developer / debugger / test-writer / api-specialist (mechanical 작업, 도메인 lookup)
- **호출 빈도** 反 비례로 비용 효율: Opus 승급 3개는 모두 호출 빈도 낮음 (commit 직전 / 시스템-wide 변경 시)

| 역할 | 모델 | 언제 |
|------|------|-----|
| architect (메인 세션) | 메인 세션 모델 (1M context) | 설계/계획/메타-인식 (코드 작성 X) |
| python-developer | Sonnet | 실제 코드 수정. 모든 edit은 여기서 |
| kis-api-specialist | Sonnet | KIS API 호출/파라미터/에러 처리 검토 |
| test-writer | Sonnet | 테스트 작성+실행 |
| debugger | Sonnet | 버그 리포트 시. 근본원인+minimal diff. 3-failure circuit breaker |
| **code-reviewer** | **Opus** | 일반 코드 리뷰 — 선언 안 된 갭 발견 (jugular vein, 학습 #32) |
| **critic** | **Opus** | 고위험 최종 게이트. 실수 비용 10-100x. 다관점 갭 분석 |
| **verifier** | **Opus** | 증거 기반 완료 검증. self-approve 금지. 17+ acceptance criteria 추적 |
| **ui-ux-designer** | Sonnet | 대시보드 UI/UX 설계 (IA/레이아웃/반응형/비주얼 spec, 코드 X). 웹디자인 팀 |
| **frontend-developer** | Sonnet | `dashboard_home/` UI(Alpine/Tailwind/Lucide)+JSON API 구현. 무빌드 스택 전담 |
| **design-reviewer** | **Opus** | UI/UX·반응형·접근성·스택함정(JS \n·탭키·payload정합) 리뷰 게이트 |

**작업 순서:**
- 신기능: architect → developer → (kis-api-specialist) → test-writer → reviewer → (고위험이면 critic) → verifier
- 버그: debugger → developer → reviewer → (고위험이면 critic) → verifier
- **웹/대시보드** (`dashboard_home/`): architect → ui-ux-designer(설계) → frontend-developer(구현) → design-reviewer → verifier → 메인세션 라이브확인(브라우저). 옛 `dashboard.py`(=/dash-classic)는 무수정.
- 재검증 필요 시: verifier 단독 (self-approve 금지)
- **공통**: 모든 코드 작업에 reviewer + verifier 필수. 인라인 진단/편집 금지.
