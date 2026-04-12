# TODO — 2026-04-12 최종
> 레포: ysjms11/stock-bot | 서버: 맥미니 M4 + Cloudflare Tunnel (arcbot-server.org)

---

## 🔴 즉시 (다음 세션)

### 봇 — 월요일 확인
- [ ] **첫 전종목 수집 확인** — 4/14(월) 18:30 자동 실행 결과 (텔레그램 알림)
- [ ] **주간 재무 수집 확인** — 4/13(일) 07:15 결과
- [ ] **DART 공시 알림 확인** — 장중 관심 57종목 공시 알림 오는지
- [ ] **감시종목 US 현재가** — 장중 대시보드에서 달러 표시되는지
- [ ] **포트폴리오 현재가 정확성** — 장중 KIS API 가격이 증권앱과 일치하는지 (HD한국조선해양 394K vs 396K 차이 확인)
- [ ] **기존 코드 정리** — Safari keepalive/KRX 크롤링 관련 제거 (수집 테스트 확인 후)
- [x] ~~대시보드 v2 → v1 교체 완료~~

### 투자 판단
- [ ] **전종목 등급 재평가** (새 3질문 기준: 해자/수요/이익가속)
- [ ] **전종목 감시가 재설정** (등급별 RR 역산 기반)
- [ ] 삼성전자 ICMS + NAND + HBM 통합 분석

---

## 🟡 진행중 / PENDING

### 봇 개발 — 신규 기능
- [ ] **컨센 상향 알람 (매일)** (P1) — 보유+감시 54종목 FnGuide EPS 변화율 + 목표가 추적, 상향 시 즉시 텔레그램 알림. 확신등급 질문③ 자동화
- [ ] **전종목 컨센 변화 스캔 (주말)** (P1) — 일요일 07:05 전종목 컨센 갱신 후: ①목표가 대폭 상향 종목 ②신규 커버 시작 소형주 감지 → 텔레그램 알림 (Sunday 30 참고용)
- [ ] **신규 리포트 텔레그램 알람** (P1) — 07:00 리포트 수집 시 이전 대비 신규 건만 필터링해서 텔레그램 발송
- [ ] **컨센서스 히스토리 SQLite 저장** (P2) — 매일 수집 시 consensus_history 테이블에 누적, 목표가/의견 추이 분석용
- [ ] **DB 변화 감지 스캔** (P2) — SQLite 기반 이평선 수렴/적자→흑자 전환/수급 전환/거래량 폭발 등 프리셋. get_change_scan 확장
- [ ] **실적/배당 일정 자동 수집** (P1) — 보유+워치 종목 실적 발표일/배당일 자동 등록 (현재 수동 events.json)
- [ ] **공시 자동 알람 개선** (P2) — 보유+워치 종목 DART 공시 발생 시 텔레그램 알림 + 내용 자동 저장 (현재 30분 체크 → 실시간화 검토)
- [ ] **자료 종목별 자동 분류** (P3) — 리포트/사업보고서/공시를 data/research/{TICKER}/ 폴더에 자동 저장. Claude 참조 편의

### 봇 개발 — 기존
- [ ] **대시보드 인증** — Cloudflare Access 설정 후 TODO 수정 + 투자판단 메모 추가 기능
- [ ] Railway 완전 삭제 (사용자 로그인)
- [ ] Oracle Cloud VM 해지 (사용자 로그인)
- [ ] aiohttp 세션 풀링 — 50+개 함수 세션 공유 (성능 최적화, P3)
- [ ] bot_architecture.md 생성 (P2)
- [ ] 시장/섹터 전략 레포트 자동 수집 (P2)

### 투자 PENDING
- [ ] LITE 5/6 FQ3 실적 전후 추매 판단
- [ ] AMD 4/28 Q1 실적 후 등급 재평가 (B+→A 검토 조건: Q3 DC $6B+)
- [ ] NVDA 5/28 Q1 실적 후 확인 (가이던스 $78B)
- [ ] 촉매 발생 시 감시가 재설정 룰 확정 (RR 역산 기반)
- [ ] 레짐 🟡 전환 시 A등급 감시가 재평가

---

## ✅ 완료 (4/12)

### KIS API 풀수집 + SQLite DB 전환 (크롤러 v2)
- [x] **SQLite DB 스키마** — 3테이블(stock_master 8컬럼 + daily_snapshot 112컬럼 + financial_quarterly 17컬럼) + v_daily_scan 뷰
- [x] **db_collector.py 신규** (1650줄) — KIS API 배치 수집 + SQLite + 기술지표 + 스캐너
  - collect_daily(): 4 Phase (시세→시간외→수급→공매도) + 기술지표 계산
  - collect_financial_weekly(): 손익계산서 + 대차대조표 (주 1회)
  - Rate limiter: Semaphore(8) + sleep(0.13) = 초당 8건
  - KRX 실패 시 stock_master fallback (KRX 의존 제거)
- [x] **kis_api.py 확장** — 신규 함수 3개 (overtime_daily/income_statement/balance_sheet) + session 파라미터 4개
- [x] **JSON→SQLite 마이그레이션** — 232일 641,839행 이관 완료
- [x] **mcp_tools.py → db_collector 직접 import** — krx_crawler 경유 제거
- [x] **krx_crawler.py 호환 래퍼** — 기존 import 유지 + db_collector re-export
- [x] **스케줄 등록** — daily_collect_job(18:30 평일) + weekly_financial_job(07:15 일요일)
- [x] **iCloud 백업** — stock.db + data/ 전체, 최근 2개 보관 (Gist 병행 유지)
- [x] **섹터 자동 갱신** — Phase 1에서 bstp_kor_isnm → sector_krx 자동 저장, 신규 상장 fallback
- [x] **10종목 수집 테스트** — 4 Phase 전부 성공

### 코드 점검 7건
- [x] **XSS v1 대시보드** — 30여 곳 _html.escape 적용
- [x] **MCP 인증** — MCP_AUTH_TOKEN Bearer 인증 (SSE+messages)
- [x] **setstop 키 불일치** — entry_price와 함께 target_price 저장
- [x] **레짐 상태 통일** — _read_regime() 헬퍼로 4곳 통일
- [x] **KIS 토큰 분기** — read_file/git_* 등 9개 도구 토큰 없이 동작
- [x] **Safari 수급 fallback** — fetch_krx_investor_data KRX OPEN API 연결

### WebSocket 확장
- [x] **KR 통합 체결가** — H0STCNT0→H0UNCNT0 (KRX+NXT 통합)
- [x] **US 체결가** — HDFSCNT0 추가 (미국 0분 지연)
- [x] **24시간 상시 연결**
- [x] **전체 가격 조회 WS 캐시 통일** — portfolio_cmd/get_portfolio/check_stoploss/snapshot
- [x] **kis_us_stock_price 파라미터 순서 버그** 3곳 수정

### 대시보드 v2
- [x] **상단 고정 탭 + 이벤트 D-day + 감시종목 전체/검색/필터**
- [x] **KR+US 실시간 현재가 + 환율 + 손익**
- [x] **문서 카드 그리드 + 자동 새로고침 + TODO 접힘**
- [x] **포트폴리오 증권사 스타일** — 총자산/총손익 요약 + 종목별 평가금/라벨 + 정렬(평가금/수익률/손익금)
- [x] **감시종목 현재가+괴리율** — WS 캐시 우선 + SQLite fallback, US 달러 표시
- [x] **투자판단 카드형** — notes/grades 표시 + 전체보기 /dash/decisions
- [x] **매매일지 카드형** — reason/grade/target/stop 표시 + 전체보기 /dash/trades
- [x] **DART 공시 개선** — 키워드 필터 제거 + 대상 57종목 확대
- [x] **check_stoploss WS 캐시 저장** — REST 가격을 set_cached_price()로 공유
- [x] **감시종목 섹터별 그룹핑** — SQLite+수동 매핑, 섹터 검색 가능
- [x] **섹터 오버라이드 8종목** — 하이록/DYP/일진파워/두산에너빌/한화시스템/이노/인텔리안/코웨이
- [x] **TODO 독립 탭** — 문서에서 분리, 탭 네비게이션에 추가
- [x] **대시보드 v2 → /dash 교체 확정**
- [x] **매매일지 가격 원화/달러 표시**
- [x] **포트폴리오 라벨** — 현재가/평가/손익/평단/매입

### 문서/기타
- [x] **MCP 도구 33개 입출력 샘플 + 데이터 파일 구조 13개** (bot_samples.md)
- [x] **문서 4개 전체 업데이트** — bot_guide/bot_reference/FILES.md/CLAUDE.md
- [x] **섹터명 보강** — KRX 29개→92개 실용 섹터
- [x] **Git MCP 도구 5개** — git_status/diff/log/commit/push

---

## ✅ 완료 (4/11 이전)

### 투자 시스템
- [x] 시스템 프롬프트 v4 + 보유 10종목 손절/목표/thesis + 5종목 딥리서치
- [x] 등급체계 + 감시가 공식 확정 + HD현대일렉트릭 B→B+

### 인프라/봇 (4/9 이전)
- [x] 맥미니 서버 이전 + launchd + KRX OPEN API + Safari keepalive
- [x] KRX DB v2 (62필드) + MCP 28개 도구 + 텔레그램 알림
- [x] get_regime v2 + 리포트 크롤러 + 백필 232일 + 웹 대시보드

---

## 알림 스케줄

| 주기 | 시간 | 알림 |
|------|------|------|
| 10분 | 수시 | 손절/감시가 체크 + 브리핑 |
| 30분 | 수시 | 이상 신호 + DART 공시 |
| 1시간 | 수시 | 레짐 전환 체크 |
| 매일 | 07:00 | 실적/배당 캘린더 + 리포트 수집 |
| 매일 | 15:40 | 장마감 요약 + 포트 건강 |
| 매일 | 16:30 | 모멘텀 경고 |
| 매일 | 18:00 | 매크로 대시보드 |
| 매일 | **18:30** | **KIS API 풀수집 (SQLite)** |
| 매일 | 19:00 | 워치 변화 감지 |
| 매일 | 22:00 | Gist 백업 |
| 매일 | 06:00 | 미국 장마감 요약 + 매크로 |
| 일요일 | 07:05 | 컨센서스 갱신 |
| 일요일 | **07:15** | **주간 재무 수집 (손익+대차)** |
| 일요일 | 19:00 | Sunday 30 리마인더 |

---

## 데이터 수집 현황

| 데이터 | 소스 | 수집 | 비고 |
|--------|------|------|------|
| 시세 (OHLCV/시총) | KIS API (FHKST01010100) | ✅ 매일 | Phase 1 |
| PER/PBR/EPS/BPS | KIS API (FHKST01010100) | ✅ 매일 | Phase 1 |
| 외인/기관/개인 수급 | KIS API (FHPTJ04160001) | ✅ 매일 | Phase 3 |
| 공매도 | KIS API (FHPST04830000) | ✅ 매일 | Phase 4 |
| 시간외 | KIS API (FHPST02320000) | ✅ 매일 | Phase 2 |
| 손익계산서 | KIS API (FHKST66430200) | ✅ 주 1회 | 일요일 |
| 대차대조표 | KIS API (FHKST66430100) | ✅ 주 1회 | 일요일 |
| 컨센서스 목표가 | FnGuide | ✅ 주 1회 | 일요일 |
| 기술 지표 (47+5개) | 자체 계산 | ✅ 매일 | MA/RSI/MACD/ATR/VP 등 |
| 섹터 분류 | KIS API + std_sector_map | ✅ 자동 | Phase 1에서 갱신 |

---

## DB 구조

| 테이블 | 컬럼 | 용도 |
|--------|------|------|
| stock_master | 8 | 종목 메타 (코드/이름/시장/섹터) |
| daily_snapshot | 112 | 스캔용 메인 (시세+수급+공매도+시간외+기술지표+재무파생) |
| financial_quarterly | 17 | 재무 원본 (손익+대차, 주 1회) |
| v_daily_scan (뷰) | 34 | master JOIN + 스캔 핵심 컬럼 |

DB: SQLite `data/stock.db` (277MB, 232일 641K행)
백업: iCloud (최근 2개) + Gist (소형 JSON)

---

## 구현 시 주의사항
1. 맥미니 로컬 data/ (DATA_DIR 환경변수)
2. KIS API 초당 10건 제한 (배치: Semaphore(8) + sleep(0.13))
3. WebSocket 41건 합산 제한 (현재 KR6 + US4 = 10건)
4. MCP 도구 추가 시 mcp_tools.py 스키마 등록 필수
5. Agent Team: architect(Opus) → dev(Sonnet) → kis-specialist(Sonnet) → test(Sonnet) → reviewer(Codex)
6. 5파일 구조: kis_api.py / main.py / mcp_tools.py / db_collector.py / krx_crawler.py(래퍼)
