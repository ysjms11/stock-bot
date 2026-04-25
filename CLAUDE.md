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
| 웹 대시보드 | `https://bot.arcbot-server.org/dash` (Cloudflare Access Gmail PIN 인증 · TODO 토글/추가 + 투자판단 폼) |
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

프로젝트는 5개 주요 Python 파일로 분리되어 있음:

| 파일 | 줄 수 | 역할 |
|------|-------|------|
| `kis_api.py` | ~6400 | KIS/DART/Yahoo API 함수, 데이터 파일 I/O, WebSocket, 매크로, 백업, 미국 애널 레이팅 (StockAnalysis) |
| `main.py` | ~5800 | 텔레그램 봇 + 자동알림 스케줄 (40+잡) + 진입점 |
| `mcp_tools.py` | ~4600 | MCP 도구 (41개) 스키마 + 실행 로직 + SSE 서버 |
| `krx_crawler.py` | ~1500 | KRX DB 로드 & 스캐너 (레거시 JSON 파일 호환) |
| `db_collector.py` | ~3200 | KIS API + KRX OPEN API 풀수집 + SQLite DB + 기술지표 + 스캐너 |

기타 파일:

| 파일 | 내용 |
|------|------|
| `stock_universe.json` | 종목 유니버스 (시총 상위 코스피+코스닥) |
| `dart_corp_map.json` | DART 고유번호 ↔ 종목코드 매핑 |
| `test_consensus_ci.py` | CI 테스트 (컨센서스 기능) |
| `requirements.txt` | Python 의존성 |

---

## 데이터 파일 경로

핵심 파일만 본체에 기록:
- `data/stock.db` — SQLite DB (~320MB, stock_master + daily_snapshot + financial_quarterly + consensus_history + reports + insider_transactions)
- `data/*.json` — 워치/포트/손절/알림 등 상태 파일 (전체 목록 → `.claude/rules/data-files.md`)
- `data/db_schema.sql` — SQLite 스키마 정의

---

## MCP 도구 (41개)

실행 로직: `mcp_tools.py`의 `_execute_tool()` 함수.
전체 도구 목록/모드/파라미터 → `.claude/rules/mcp-tools.md`

---

## 새 MCP 도구 추가

절차 (API 함수 작성 → 스키마 추가 → elif 핸들러 → 커밋):
→ `.claude/rules/add-mcp-tool.md` 참조

---

## 자동 스케줄 (30+ 잡)

전체 타임라인/의존성/신규 잡 추가 절차 → `.claude/rules/schedule.md`

---

## 알려진 이슈

**🔴 버그 함정**
- **미국 현재가 `rate` 필드**: KIS 해외 응답은 `rate`. `diff_rate` 없음. 전 코드 `rate` 통일됨.
- **WebSocket 국내 전용**: `KisRealtimeManager`는 국내만. 미국은 Yahoo Finance 폴링 (`check_stoploss`).
- **KRX OPEN API 간헐 장애**: 자주 빈 응답. `db_collector`가 `stock_master` fallback으로 KIS API 직접 호출.

**🟠 데이터 성숙 대기 중 (4/12부터 수집)**
- `get_change_scan` 프리셋 3개는 과거 데이터 0으로 현재 빈 결과. 시간 지나면 자동 작동:
  - `short_squeeze`: ~5/14 (20d 데이터 필요)
  - `foreign_accumulation`: ~4/19 (5d). 계산 로직 5줄 추가 필요
  - `credit_unwind`: `whol_loan_rmnd_rate` 저장 + 계산 필요 (추가 API 호출 없음)

**🟡 아키텍처 노트**
- 로컬 DATA_DIR + Gist 백업
- KIS 토큰 캐시 23시간 (메모리+파일)
- Yahoo Finance fallback (미국 장 요약 + `check_stoploss`)
- DST 자동 감지 (`zoneinfo`)
- 섹터 2중 구조 (`sector`=실용 분류, `sector_krx`=KRX 원본, `std_sector_map.json` 캐시)

---

## 코딩 규칙

- **5파일 구조**: API/데이터 → `kis_api.py`, 텔레그램+스케줄 → `main.py`, MCP → `mcp_tools.py`, KRX 크롤러 → `krx_crawler.py`, KIS API 배치 수집+SQLite → `db_collector.py`.
- **KIS API 신 방식**: 새 함수는 반드시 `_kis_get()` 래퍼 사용 (구 방식 `get_stock_price()` 패턴 사용 금지).
- **에러 처리**: 개별 종목 루프 내부는 `try/except Exception: pass` 패턴으로 한 종목 오류가 전체 중단 방지.
- **KIS API rate limit**: 초당 10건 제한. 연속 호출 시 `await asyncio.sleep(0.3)` 삽입 (실사용 초당 ~3.3건).
- **섹션 구분**: `# ━━━━━━━━━━━━━━━━━━━━━━━━━` 주석으로 논리적 섹션 구분 유지.
- **한국어 변수명**: 텔레그램 메시지 문자열 외에는 영문 변수명 사용.
- **MCP 도구 순서**: `MCP_TOOLS` 배열과 `_execute_tool` elif 체인의 순서를 일치시킬 것.
- **import 패턴**: `kis_api.py`에서 `from kis_api import *` + 명시적 private 함수 import. `mcp_tools.py`도 동일.

---

## 🚨 매수 관련 질문 처리 모드 (필수)

사용자 발화에 다음 키워드가 있으면 **단순 정보 모드** 적용 — 풀딥서치(KR/US_DEEPSEARCH 10 Step) 발동 금지:
- "리스트", "리스트만", "후보만", "종목만", "추천 알려줘", "raw", "그냥", "분석 빼고", "간단히"
- 매수 검토 어휘 없이 단순 발굴 요청 (예: "톱애널 추천 미국주 리스트", "살만한 종목 5개")

### 단순 정보 모드 동작
1. **도구 1번 호출로 끝** (`get_us_buy_candidates`, `get_us_scan`, `get_finance_rank` 등)
2. **표 + 한 줄 코멘트만**, 50줄 이내
3. **금지**: 매크로 레짐, 자금 매칭, 3-Gate, RR, Kill Switch, Bear Case, 보유 재평가, 다른 종목 끌어들이기

### 풀딥서치 발동은 명시 시에만
- "딥서치", "검증해줘", "이거 사도 돼?", "1차 진입 권고", "RR 분석", "포트 편입"
- 위 어휘가 없으면 **무조건 단순 정보 모드 유지**.

상세 룰: `data/INVESTMENT_RULES.md` 0-A 섹션.

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

| 역할 | 모델 | 언제 |
|------|------|-----|
| architect (Opus, 기본 계정) | - | 설계/계획 (코드 작성 X) |
| python-developer | Sonnet | 실제 코드 수정. 모든 edit은 여기서 |
| kis-api-specialist | Sonnet | KIS API 호출/파라미터/에러 처리 검토 |
| test-writer | Sonnet | 테스트 작성+실행 |
| code-reviewer | Sonnet | 일반 코드 리뷰 |
| critic | Sonnet | 고위험 변경 최종 게이트 (다관점 갭분석, file:line 증거) |
| verifier | Sonnet | 증거 기반 완료 검증. self-approve 금지 |
| debugger | Sonnet | 버그 리포트 시. 근본원인+minimal diff. 3-failure circuit breaker |

**작업 순서:**
- 신기능: architect → developer → (kis-api-specialist) → test-writer → reviewer → (고위험이면 critic)
- 버그: debugger 단독 → verifier 별도 검증
- 재검증 필요 시: verifier 단독 (self-approve 금지)
