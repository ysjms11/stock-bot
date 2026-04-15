# 봇 데이터 파일 설명서

## 📋 내가 봐야 할 파일

| 파일 | 내용 | 예시 |
|------|------|------|
| **TODO.md** | 할 일 목록 (체크박스) | - [ ] get_regime 3개 지표로 수정 |
| **bot_guide.md** | 30개 MCP 도구를 용도별 분류 | 일일점검용, 딥서치용, 발굴검증용 등 |
| **bot_scenarios.md** | 7가지 실전 활용 시나리오 | "HD조선 수급 점검 시 이 순서로 호출" |
| **bot_reference.txt** | 30개 도구 파라미터 상세 | get_supply(mode=history, ticker=005930, days=20) |
| **krx_db_design.md** | KRX DB 62개 필드 설계서 | 시세/수급/공매도/외인/기술지표 전체 |
| **INVESTMENT_RULES.md** | 투자 규칙 & 운영 매뉴얼 (등급체계/포트규칙/레짐) | A등급 최대 20%, D등급 즉시 손절 등 |
| **HANDOVER.md** | 세션 인계 가이드 | 새 세션 시작 시 컨텍스트 복원 절차 |

## 🐍 핵심 소스 파일

| 파일 | 내용 |
|------|------|
| **db_collector.py** | KIS API 풀수집 + SQLite DB 저장 + 기술지표 계산 + 스캐너 (~1700줄) |
| **data/stock.db** | SQLite DB (277MB, stock_master + daily_snapshot + financial_snapshot + 뷰) |
| **data/db_schema.sql** | SQLite DB 스키마 정의 (테이블/인덱스/뷰 DDL) |

## 💰 투자 현황 파일

| 파일 | 내용 | 예시 |
|------|------|------|
| **portfolio.json** | 보유종목 + 수량 + 평단가 + 현금 | HD조선 50주 평단 207,800원, 현금 411만원 |
| **watchalert.json** | **워치리스트 단일 소스** (KR+US 통합). 스키마: `{ticker: {name, market: "KR"\|"US", buy_price, qty, memo, grade, created_at, updated_at}}`. `buy_price>0` = 매수감시 활성, `buy_price==0` = 단순 워치 | NVDA $171 "AI GPU 1위" / 풍산 buy=0 "관심만" |
| ~~watchlist.json~~ | **[레거시]** 2026-04-16 `.bak`으로 리네임, watchalert.json으로 통합 | — |
| ~~us_watchlist.json~~ | **[레거시]** 2026-04-16 `.bak`으로 리네임, watchalert.json으로 통합 | — |
| **stoploss.json** | 손절가/목표가 설정된 보유종목 | HD조선 손절 175K, 목표 280K |
| **decision_log.json** | 투자 판단 기록 (날짜별) | "4/9: 레짐 11→3개 확정, NVDA A등급" |
| **trade_log.json** | 실제 매매 기록 (매수/매도) | "4/3 HD조선 6주 매도 @205K, grade:B" |
| **portfolio_history.json** | 포트 일별 스냅샷 (총자산/손익) | "4/8: 총 6,420만원, 일간 +3.2%" |
| **events.json** | 매크로 이벤트 캘린더 | "4/10 CPI, 4/23 POSCO 실적" |
| **compare_log.json** | 종목 비교 기록 | "AMD vs NVDA: NVDA 92점, AMD 70점" |
| **watchlist_log.json** | 워치리스트 변경 이력 | "4/6: STVN 삭제 (D등급, thesis 붕괴)" |

## 📊 데이터/캐시 (안 봐도 됨)

| 파일 | 내용 |
|------|------|
| consensus_cache.json | FnGuide 컨센서스 캐시 (자동 갱신) |
| dart_screener_cache.json | DART 스크리너 당일 캐시 |
| corp_codes.json | DART 고유번호↔종목코드 매핑 (27만건) |
| reports.json | 수집된 증권사 리포트 전문 (1.4MB) |
| sector_rotation.json | 섹터 로테이션 감지 데이터 |
| regime_state.json | 레짐 디바운스 상태 추적 |
| std_sector_map.json | 표준산업분류 전종목 캐시 (265KB, 1회 수집) |

## 🔧 시스템 (절대 건들지 마)

| 파일 | 내용 |
|------|------|
| token_cache.json | KIS API 인증 토큰 (23시간 캐시) |
| krx_cookies.json | KRX 크롤링 Safari 세션 쿠키 |
| stoploss_sent.json | 손절 알림 당일 발송 기록 |
| watch_sent.json | 감시가 알림 당일 발송 기록 |
| supply_history.json | 수급 히스토리 캐시 |
| weekly_base.json | 주간 리뷰 기준 스냅샷 |
| sector_flow_cache.json | 섹터 수급 캐시 |
| code_audit.json | 코드 최적화 감사 결과 |
| kis_api_audit.json | KIS API 감사 결과 |
| regime_transition_sent.json | 레짐 전환 알림 발송 기록 |
| dart_seen.json | DART 알림 전송된 공시 ID 목록 |

## 📁 폴더

| 폴더 | 내용 |
|------|------|
| **krx_db/** | KRX 전종목 일별 DB (2,773종목 × 62필드, 232일 백필) |
| **dart_reports/** | DART 사업보고서 본문 txt |
| **research/** | 종목별 딥리서치 파일 (NVDA.md, AMD.md 등 10개) |
