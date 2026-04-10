# CLAUDE.md — stock-bot 프로젝트 가이드

## KIS API 참조

**`kis-api-ref/` 폴더에 한투 공식 API 샘플이 있음. API 엔드포인트, TR_ID, 파라미터 확인 시 이 폴더 참조할 것.**

| 파일/폴더 | 내용 |
|-----------|------|
| `kis-api-ref/examples_llm/domestic_stock/` | 국내주식 API 예제 (TR_ID, params, response 포함) |
| `kis-api-ref/examples_llm/overseas_stock/` | 해외주식 API 예제 |
| `kis-api-ref/examples_llm/etfetn/` | ETF/ETN API 예제 |
| `kis-api-ref/data.csv` | KIS REST API 전체 목록 (6326행, category/TR_ID/URL/params/response) |
| `kis-api-ref/data2.csv` | KIS API 확장 목록 (12437행) |

새 API 엔드포인트 찾을 때: `grep "TR_ID명" kis-api-ref/data.csv` 또는 `kis-api-ref/examples_llm/` 서브폴더 참조.

## 인프라

| 항목 | 내용 |
|------|------|
| 레포 | https://github.com/ysjms11/stock-bot |
| 배포 | 맥미니 M4 (192.168.0.36), launchd 자동시작 |
| MCP URL | `https://bot.arcbot-server.org/mcp` (SSE) |
| MCP messages | `https://bot.arcbot-server.org/mcp/messages?sessionId=<id>` (POST) |
| Health check | `https://bot.arcbot-server.org/health` |
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
GITHUB_TOKEN     GitHub Gist 백업용 토큰 (선택)
BACKUP_GIST_ID   백업 Gist ID (선택)
KRX_UPLOAD_KEY   KRX DB 업로드 인증 키 (GitHub Actions)
DATA_DIR         데이터 디렉토리 경로 (/Users/kreuzer/stock-bot/data)
```

**GitHub Actions Secrets** (KRX 크롤러용)

```
BOT_URL          서버 URL (https://bot.arcbot-server.org)
BOT_API_KEY      KRX_UPLOAD_KEY와 동일한 값
```

---

## 파일 구조

프로젝트는 4개 주요 Python 파일로 분리되어 있음:

| 파일 | 줄 수 | 역할 |
|------|-------|------|
| `kis_api.py` | ~2400 | KIS/DART/Yahoo API 함수, 데이터 파일 I/O, WebSocket, 매크로, 백업 |
| `main.py` | ~1950 | 텔레그램 봇 + 자동알림 스케줄 + 진입점 |
| `mcp_tools.py` | ~1760 | MCP 도구 스키마 + 실행 로직 + SSE 서버 |
| `krx_crawler.py` | ~400 | KRX DB 로드, 스캐너 (크롤링은 GitHub Actions) |

기타 파일:

| 파일 | 내용 |
|------|------|
| `scripts/krx_update.py` | GitHub Actions용 KRX 크롤러 (독립 실행) |
| `scripts/requirements_actions.txt` | GitHub Actions 의존성 |
| `.github/workflows/krx_update.yml` | KRX 크롤링 워크플로우 (평일 15:55 KST) |
| `stock_universe.json` | 종목 유니버스 (시총 상위 코스피+코스닥) |
| `dart_corp_map.json` | DART 고유번호 ↔ 종목코드 매핑 |
| `test_consensus_ci.py` | CI 테스트 (컨센서스 기능) |
| `requirements.txt` | Python 의존성 |
| `Procfile` | Railway 실행 명령 |

---

## 데이터 파일 경로 (`/data/*.json`)

| 파일 | 내용 | 기본값 |
|------|------|--------|
| `/data/watchlist.json` | 한국 워치리스트 `{ticker: name}` | 5개 기본 종목 |
| `/data/us_watchlist.json` | 미국 워치리스트 `{ticker: {name, qty}}` | TSLA 등 4개 |
| `/data/stoploss.json` | 손절/목표가 `{ticker: {name, stop_price, ...}, us_stocks: {...}}` | `{}` |
| `/data/portfolio.json` | 보유 포트폴리오 `{ticker: {name, qty, avg_price}, us_stocks: {...}}` | `{}` |
| `/data/dart_seen.json` | DART 알림 전송된 공시 ID 목록 `{ids: [...]}` | `{ids: []}` |
| `/data/watchalert.json` | 매수 희망가 감시 `{ticker: {name, buy_price, memo, created}}` | `{}` |
| `/data/watch_sent.json` | 매수감시 알림 당일 발송 기록 `{ticker: "YYYY-MM-DD"}` | `{}` |
| `/data/stoploss_sent.json` | 손절 알림 당일 발송 횟수 기록 | `{}` |
| `/data/decision_log.json` | 투자판단 기록 (날짜별 regime/grades/actions) | `[]` |
| `/data/compare_log.json` | 종목 비교 기록 | `[]` |
| `/data/watchlist_log.json` | 워치리스트 변경 이력 | `[]` |
| `/data/events.json` | 매크로 이벤트 캘린더 | `{}` |
| `/data/weekly_base.json` | 주간 리뷰 기준 스냅샷 | `{}` |
| `/data/stock_universe.json` | 종목 유니버스 (시총 상위) | `{}` |
| `/data/consensus_cache.json` | 컨센서스 캐시 (FnGuide) | `{}` |
| `/data/portfolio_history.json` | 포트폴리오 일별 스냅샷 | `[]` |
| `/data/trade_log.json` | 매매 기록 | `[]` |
| `/data/dart_corp_map.json` | DART 고유번호 매핑 | `{}` |
| `/data/dart_screener_cache.json` | DART 스크리너 당일 캐시 | `{}` |
| `/data/corp_codes.json` | OpenDART corp_code 매핑 캐시 (1일 1회 갱신) | `{}` |
| `/data/dart_reports/*.txt` | DART 사업보고서 본문 txt 파일 | — |
| `/data/krx_db/YYYYMMDD.json` | KRX 전종목 일별 DB (시세+수급+비율, 30일 보관) | — |

> 맥미니 로컬 `data/` 디렉토리 사용 (`DATA_DIR` 환경변수).
> 환경변수 기반 자동복원 fallback 있음 (`BACKUP_PORTFOLIO`, `BACKUP_STOPLOSS` 등).

---

> **상세 참조**: 파일별 함수 구조 → `.claude/rules/file-structure.md`, KIS API TR_ID 테이블 → `.claude/rules/kis-api-reference.md`

---

## MCP 도구 목록 (28개)

| # | 이름 | mode/type | 설명 |
|---|------|-----------|------|
| 1 | `get_rank` | type=price | 한국 등락률 상위/하위 (rise/fall, kospi/kosdaq) |
| | | type=us_price | 미국 등락률 상위/하위 (NAS/NYS/AMS) |
| | | type=volume | 체결강도 상위 (120%이상=매수우위) |
| | | type=scan | 거래량 상위 종목 |
| | | type=after_hours | 시간외 등락률 순위 (장 마감 후 급등/급락) |
| | | type=dividend | 배당수익률 순위 (배당금·배당률·PER) |
| 2 | `get_portfolio` | | 포트폴리오 조회/수정 (한국+미국 손익, cash_krw/cash_usd) |
| 3 | `get_stock_detail` | (기본) | 현재가·PER·PBR·수급, 한국/미국 자동 판별, period로 일봉 |
| | | mode=volume_profile | 볼륨 프로파일(매물대) 분석 (Y1/Y2/Y3) |
| | | mode=after_hours | 시간외 현재가·등락률·거래량 |
| | | mode=orderbook | 매수·매도 10호가 + 잔량 + 비율 |
| 4 | `get_supply` | mode=daily | 당일확정수급 (외인/기관/개인) |
| | | mode=history | N일 수급추세 (연속매수/매도) |
| | | mode=estimate | 장중추정수급 (가집계) |
| | | mode=foreign_rank | 외국인 순매수 상위 |
| | | mode=combined_rank | 외인+기관 합산 순매수 상위 |
| | | mode=broker_rank | 증권사별 매매종목 상위 (매수/매도) |
| 5 | `get_dart` | | DART 공시 (워치 3일, report/report_list/read 모드) |
| 6 | `get_macro` | | 매크로 지표 (dashboard/sector_etf/convergence/op_growth 등) |
| 7 | `get_sector` | | 업종별 외인+기관 순매수, 업종 로테이션 분석 |
| 8 | `manage_watch` | | 워치리스트 조회/추가/제거 (한국+미국, 매수감시 포함) |
| 9 | `get_alerts` | | 손절가/목표가 목록 + 현재가 대비 % + 매수감시 |
| 10 | `get_market_signal` | mode=short_sale | 공매도 일별추이 |
| | | mode=vi | VI 발동 종목 현황 |
| | | mode=program_trade | 프로그램매매 투자자별 동향 |
| | | mode=credit | 신용잔고 일별추이 (10% 과열 경고) |
| | | mode=lending | 대차거래 일별추이 |
| 11 | `get_news` | | 종목 뉴스 헤드라인 (한국/미국, sentiment 감성분석) |
| 12 | `get_consensus` | | 증권사 컨센서스 목표주가/투자의견 (FnGuide) |
| 13 | `set_alert` | | 손절가/목표가, 매수감시, 투자판단, 종목비교, 매매기록 |
| 14 | `get_portfolio_history` | | 포트폴리오 스냅샷 히스토리 + 드로다운 + 투자규칙 경고 |
| 15 | `get_trade_stats` | | 매매 기록 성과 분석 (승률·손익·평균보유기간) |
| 16 | `backup_data` | | /data/*.json GitHub Gist 백업·복원·상태 조회 |
| 17 | `simulate_trade` | | 가상 매매 시뮬레이션 |
| 18 | `get_backtest` | | 백테스트 (ma_cross/momentum_exit/supply_follow/bollinger/hybrid) |
| 19 | `manage_report` | | 투자 리포트 관리 |
| 20 | `get_regime` | | 시장 국면 판단 (매크로 기반) |
| 21 | `get_scan` | | KRX 전종목 스크리너 (시총/PER/PBR/수급/회전율, 6개 프리셋) |
| 22 | `get_finance_rank` | | 전종목 재무비율 순위 (PER/PBR/ROE/영업이익률/부채비율/매출성장률) |
| 23 | `get_highlow` | | 52주 신고가/신저가 근접 종목 순위 (괴리율 필터) |
| 24 | `get_broker` | | 종목별 거래원(증권사) 매수/매도 상위 5곳 |
| 25 | `read_file` | | stock-bot 디렉토리 내 파일 읽기 (.md/.py/.json/.txt, 100KB, ../ 차단) |
| 26 | `write_file` | | stock-bot 디렉토리 내 파일 쓰기 (.md/.json/.txt, .py/.env 불가, 200KB, ../ 차단) |
| 27 | `list_files` | | stock-bot 디렉토리 내 파일/폴더 목록 (이름·크기·수정일, depth 2, ../ 차단) |
| 28 | `get_change_scan` | preset= | 변화 감지 스캔 (ma_convergence/volume_spike/earnings_disconnect/consensus_undervalued/oversold_bounce/vp_support/golden_cross/sector_leader/w52_breakout, 복합 콤마 구분) |

---

## 새 MCP 도구 추가하는 방법

**Step 1 — API 함수 작성** (`kis_api.py`에 추가)

```python
async def kis_new_api(ticker: str, token: str) -> dict:
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/...", "TR_ID", token, {"param": ticker})
        return d.get("output", {})
```

**Step 2 — MCP_TOOLS 배열에 스키마 추가** (`mcp_tools.py`의 `MCP_TOOLS` 배열 끝)

```python
{"name": "new_tool_name", "description": "도구 설명",
 "inputSchema": {"type": "object",
                 "properties": {"ticker": {"type": "string", "description": "종목코드"}},
                 "required": ["ticker"]}},
```

**Step 3 — `_execute_tool` 함수에 elif 핸들러 추가** (`mcp_tools.py`의 `else: result = {"error": ...}` 바로 위)

```python
elif name == "new_tool_name":
    ticker = arguments.get("ticker", "").strip()
    d = await kis_new_api(ticker, token)
    result = {"ticker": ticker, "field": d.get("field_name")}
```

**Step 4** — 커밋 & push → 맥미니 서버에서 git pull 후 재시작

---

## 알려진 이슈

- **해외 현재가 `rate` 필드**: 응답 필드는 `rate` (등락률%). `diff_rate`는 존재하지 않음 → None 반환됨. `get_portfolio` 미국 섹션은 `d.get("rate")` 사용.
- **거래소 코드 자동판별**: `_guess_excd()`는 `_NYSE_TICKERS` 세트 기반으로 NYS/NAS만 구분. AMEX(`AMS`) 종목은 NAS로 fallback됨.
- **로컬 데이터**: 맥미니 로컬 `data/` 디렉토리 사용. 환경변수 기반 fallback 복원 + Gist 백업 있음.
- **KIS 토큰 캐시**: `data/token_cache.json`에 파일 캐싱 (24시간 유효, 23시간 재사용). 재시작 시에도 캐시된 토큰 즉시 사용.
- **Yahoo Finance fallback**: 미국 장 요약(`us_market_summary`)과 손절 체크(`check_stoploss` US)는 Yahoo Finance 사용. KIS 해외 API와 혼용 주의.
- **check_fx_alert 비활성화**: 환율 알림은 매크로 대시보드로 통합 예정, 스케줄에서 주석 처리됨.
- **WebSocket 국내 전용**: `KisRealtimeManager`는 국내주식만 지원. 미국주식은 폴링 방식(`check_stoploss`).
- **DST 자동 감지**: 미국 장 시간 판별은 `zoneinfo.ZoneInfo('America/New_York')` 사용으로 서머타임/표준시 자동 전환.
- **KRX 크롤링 → GitHub Actions**: GitHub Actions에서 크롤링 후 `/api/krx_upload`로 업로드하는 구조. 설정: GitHub Secrets(`BOT_URL`, `BOT_API_KEY`) + 환경변수(`KRX_UPLOAD_KEY`).
- **공매도/신용잔고 전종목 미수집**: KRX 정보데이터시스템(공매도→금융투자협회 redirect, 외인/신용은 종목별만), 공공데이터포털, 네이버(페이지 폐쇄) 모두 부적합. KIS API는 1.5초/호출이라 전종목 60분 부담. 결정: **딥서치 시점에 `get_market_signal(mode=short_sale, ticker=...)` 개별 조회**. `short_squeeze`/`credit_unwind`/`foreign_accumulation` 프리셋은 비활성 상태 유지.
- **KRX Safari 세션 의존**: PER/PBR/수급은 Safari 카카오 로그인 필수. 30분 자동로그아웃 → `com.stock-bot.krx-keepalive` launchd가 25분마다 "연장" 버튼 클릭. 모든 윈도우/탭 순회.

---

## 코딩 규칙

- **4파일 구조**: API/데이터 → `kis_api.py`, 텔레그램+스케줄 → `main.py`, MCP → `mcp_tools.py`, KRX 크롤러 → `krx_crawler.py`.
- **KIS API 신 방식**: 새 함수는 반드시 `_kis_get()` 래퍼 사용 (구 방식 `get_stock_price()` 패턴 사용 금지).
- **에러 처리**: 개별 종목 루프 내부는 `try/except Exception: pass` 패턴으로 한 종목 오류가 전체 중단 방지.
- **asyncio.sleep(0.3~0.4)**: KIS API 연속 호출 시 rate limit 방지를 위해 `await asyncio.sleep(0.3)` 삽입.
- **섹션 구분**: `# ━━━━━━━━━━━━━━━━━━━━━━━━━` 주석으로 논리적 섹션 구분 유지.
- **한국어 변수명**: 텔레그램 메시지 문자열 외에는 영문 변수명 사용.
- **MCP 도구 순서**: `MCP_TOOLS` 배열과 `_execute_tool` elif 체인의 순서를 일치시킬 것.
- **import 패턴**: `kis_api.py`에서 `from kis_api import *` + 명시적 private 함수 import. `mcp_tools.py`도 동일.

---

## Token Optimization Rules

1. Trust skills/memory — skip re-reading files already in context
2. No speculative tool calls — only call tools when result is needed
3. Parallelize independent tool calls when possible
4. Route output > 20 lines to subagents
5. Never restate what the user already said

---

## Agent Team

모든 코드 작업은 아래 팀 구조를 따른다:

### Teammate 1: architect (Opus)
- 역할: 설계/계획만. 코드 작성 안 함.
- "어떤 파일을 어떻게 수정할지" 계획을 세우고 python-developer에게 넘김.

### Teammate 2: python-developer (Sonnet)
- 역할: architect 계획에 따라 실제 코드 작성.
- 모든 수정은 이 에이전트가 실행.

### Teammate 3: kis-api-specialist (Sonnet)
- 역할: KIS Open API 관련 로직 검토. API 호출 순서, 파라미터, 에러 처리 확인.
- KIS API 관련 없는 작업이면 스킵.

### Teammate 4: test-writer (Sonnet)
- 역할: 테스트 작성 및 실행. 수정된 기능의 정상 동작 확인.

### Teammate 5: code-reviewer (Codex)
- 역할: 최종 코드 리뷰. /codex:review --base main 실행.
- 모든 작업의 마지막 단계.

### 작업 순서
architect → python-developer → kis-api-specialist(해당시) → test-writer → code-reviewer
