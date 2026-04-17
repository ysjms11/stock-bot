# 봇 데이터 파일 설명서

## 📋 내가 봐야 할 파일

| 파일 | 내용 | 예시 |
|------|------|------|
| **PROGRESS.md** | 세션 인계 + 다음 할 일 (매 세션 시작 시 가장 먼저 읽는 문서) | "shares_out 재시도 필요" / "Phase6 증분 수집 배포됨" |
| **TODO_dev.md** | 봇 개발 TODO (체크박스, 4/15부터 분리) | - [x] F-Score/M-Score/FCF 메트릭 |
| **TODO_invest.md** | 투자 TODO (체크박스) | - [ ] SKH 10%룰 진입 |
| **bot_guide.md** | MCP 도구를 용도별 분류 | 일일점검용, 딥서치용, 발굴검증용 등 |
| **bot_scenarios.md** | 실전 활용 시나리오 | "HD조선 수급 점검 시 이 순서로 호출" |
| **bot_reference.txt** | MCP 도구 파라미터 상세 | get_supply(mode=history, ticker=005930, days=20) |
| **krx_db_design.md** | KRX DB 필드 설계서 | 시세/수급/공매도/외인/기술지표 전체 |
| **INVESTMENT_RULES.md** | 투자 규칙 & 운영 매뉴얼 (등급체계/포트규칙/레짐) | A등급 최대 20%, D등급 즉시 손절 등 |
| ~~HANDOVER.md~~ | **[폐기 2026-04-16]** PROGRESS.md로 역할 일원화 | — |

## 🐍 핵심 소스 파일

| 파일 | 내용 |
|------|------|
| **kis_api.py** | KIS/DART/Yahoo API + 파일 I/O + WebSocket + 매크로 (~4200줄) |
| **main.py** | 텔레그램 봇 + 자동알림 스케줄 + 진입점 (~3500줄) |
| **mcp_tools.py** | MCP 도구 스키마 35개 + 실행 로직 + SSE 서버 (~3800줄) |
| **db_collector.py** | KIS + DART 수집 + SQLite DB + 기술지표 + 스캐너 + **F/M/FCF 계산** (~2900줄) |
| **krx_crawler.py** | KRX DB 로드 (레거시 JSON 호환) (~400줄) |
| **data/stock.db** | SQLite DB (310MB, 7개 테이블: stock_master/daily_snapshot/financial_quarterly/consensus_history/reports/insider_transactions/sqlite_sequence) |
| **data/db_schema.sql** | SQLite DB 스키마 정의 (테이블/인덱스/뷰 DDL) |

## 💰 투자 현황 파일

| 파일 | 내용 | 예시 |
|------|------|------|
| **portfolio.json** | 보유종목 + 수량 + 평단가 + 현금 | HD조선 50주 평단 207,800원 |
| **watchalert.json** | **워치리스트 단일 소스** (KR+US 통합). 스키마: `{ticker: {name, market: "KR"\|"US", buy_price, qty, memo, grade, created_at, updated_at}}`. `buy_price>0` = 매수감시 활성, `buy_price==0` = 단순 워치 | NVDA $171 "AI GPU 1위" / 풍산 buy=0 "관심만" |
| ~~watchlist.json~~ | **[레거시 2026-04-16]** watchalert.json으로 통합 | — |
| ~~us_watchlist.json~~ | **[레거시 2026-04-16]** watchalert.json으로 통합 | — |
| **stoploss.json** | 손절가/목표가 설정된 보유종목 | HD조선 손절 175K, 목표 280K |
| **decision_log.json** | 투자 판단 기록 (날짜별) | "4/9: 레짐 11→3개 확정, NVDA A등급" |
| **trade_log.json** | 실제 매매 기록 (매수/매도) | "4/3 HD조선 6주 매도 @205K" |
| **portfolio_history.json** | 포트 일별 스냅샷 (총자산/손익) | "4/8: 총 6,420만원, 일간 +3.2%" |
| **events.json** | 매크로 이벤트 + 실적/배당 캘린더 | "4/10 CPI, 4/23 POSCO 실적" |
| **compare_log.json** | 종목 비교 기록 | "AMD vs NVDA: NVDA 92점, AMD 70점" |
| **watchlist_log.json** | 워치리스트 변경 이력 | "4/6: STVN 삭제 (D등급, thesis 붕괴)" |

## 📊 데이터/캐시 (안 봐도 됨)

| 파일 | 내용 |
|------|------|
| consensus_cache.json | FnGuide 컨센서스 캐시 (자동 갱신) |
| dart_screener_cache.json | DART 스크리너 당일 캐시 |
| corp_codes.json | DART 고유번호↔종목코드 매핑 (3,959 법인) |
| dart_corp_map.json | DART 고유번호 간이 캐시 (레거시) |
| sector_rotation.json | 섹터 로테이션 감지 데이터 |
| regime_state.json | 레짐 디바운스 상태 추적 |
| std_sector_map.json | 표준산업분류 전종목 캐시 (1회 수집) |

## 🔧 시스템 (절대 건들지 마)

| 파일 | 내용 |
|------|------|
| token_cache.json | KIS API 인증 토큰 (23시간 캐시) |
| stoploss_sent.json | 손절 알림 당일 발송 기록 |
| watch_sent.json | 감시가 알림 당일 발송 기록 |
| supply_history.json | 수급 히스토리 캐시 |
| weekly_base.json | 주간 리뷰 기준 스냅샷 |
| sector_flow_cache.json | 섹터 수급 캐시 |
| regime_transition_sent.json | 레짐 전환 알림 발송 기록 |
| dart_seen.json | DART 알림 전송된 공시 ID 목록 |
| insider_sent.json | 내부자 클러스터 알림 발송 기록 (7일 쿨다운) |

## 📁 폴더

| 폴더 | 내용 |
|------|------|
| **krx_db/** | KRX 전종목 일별 DB (레거시 JSON, SQLite로 대체) |
| **dart_reports/** | DART 사업보고서 본문 txt (수동 수집, 현재 16건) |
| **report_pdfs/** | 증권사 애널리스트 리포트 PDF (현재 39건) |
| **thesis/** | 종목별 딥서치 thesis 문서 (현재 30건, KR/US 공용) |
| **research/** | 종목별 딥리서치 참고자료 |

---

## 🆕 F/M/FCF 알파 메트릭 인프라 (2026-04-16~17 구축)

### `financial_quarterly` 테이블 — 28컬럼

| 카테고리 | 컬럼 |
|----------|------|
| **기본** | symbol, report_period (YYYYMM), collected_at |
| **손익** | revenue, cost_of_sales, gross_profit, operating_profit, op_profit, net_income, **net_income_parent**, **sga** |
| **대차** | current_assets, fixed_assets, total_assets, current_liab, fixed_liab, total_liab, capital, total_equity, **equity_parent** |
| **현금흐름** | **cfo**, **capex**, **fcf**, **depreciation** |
| **기타** | **receivables**, **inventory**, **shares_out**, **fs_source** (CFS/OFS) |

- 단위: money=**억원**, shares_out=**주**
- 12분기 소급 완료 (2023.Q2 ~ 2026.Q1, 26,584행)
- DART 체계: 연초 누적값 저장 (TTM 계산은 `_compute_ttm()` 호출 시 자동 차분)

### `daily_snapshot` 테이블 — 118컬럼 (기존 113 + 5 신규)

알파 메트릭 5컬럼 신규 추가:
- `fscore INTEGER` (0~9점, Piotroski F-Score)
- `mscore REAL` (Beneish M-Score, M ≤ -2.22 안전)
- `fcf_to_assets REAL` (FCF/총자산 %)
- `fcf_yield_ev REAL` (FCF/EV %)
- `fcf_conversion REAL` (FCF/순이익 % = 이익 현금화율)

### 자동화 스케줄

| 시간 (KST) | 작업 | DART 쿼터 |
|-----------|------|-----------|
| **매일 02:00** | `daily_dart_incremental` — 신규 정기공시 증분 수집 (list.json → 필요 건만) | 5~500콜 |
| 매일 18:30 | `collect_daily` + `update_all_alpha_metrics` (훅) | 0 |

### MCP 도구 확장 (Phase 4)

- `get_alpha_metrics(ticker)` — 종목별 F/M/FCF 상세 + 해석 (우량/조작 위험/현금창출)
- `get_finance_rank(rank_type='fscore')` — F-Score ≥7 우량 순위
- `get_finance_rank(rank_type='mscore_safe')` — M-Score ≤-2.22 안전 순위 (오름차순)
- `get_finance_rank(rank_type='fcf_yield')` — FCF/EV 내림차순 순위

---

## 🚨 알려진 구조적 한계

- **직접법 현금흐름표 회사**: 삼성전자/SK하이닉스/현대차 등 대형주는 DART fnlttSinglAcntAll에 감가상각 노출 X → **M-Score 22%만 계산 가능** (DEPI 변수 누락)
- **지배주주 귀속 순이익**: IS(별도 손익계산서) 없는 회사(CIS만)는 파싱 어려움 → 3%만 수집, 나머지는 net_income fallback
- **DART 일일 40,000콜 한도**: 12분기 소급 1회 = 한도 95% 점유 → 재소급 금지, 증분 수집만 사용
- **stockTotqySttus vs fnlttSinglAcntAll**: 같은 날 양쪽 대량 호출 시 쿼터 분리 충돌 가능 → 스케줄 분리 필요
