# 봇 개발 TODO — 2026-04-23
> 레포: ysjms11/stock-bot | 서버: 맥미니 M4 + Cloudflare Tunnel (arcbot-server.org)

---

## 🔴 확인 (매일)
- [ ] 전종목 수집 결과 — 18:30 자동 실행
- [ ] 감시종목 US 현재가
- [ ] DART 공시 알림

---

## 🟡 P1 — 다음 개발

### 신규 기능
- [x] **컨센 상향 알람 (매일 19:30)** — 구현+테스트 완료
- [x] **전종목 컨센 변화 스캔 (일요일 07:05)** — 구현 완료
- [x] **신규 리포트 텔레그램 알람** — SQLite 전환, 종목별 표시
- [x] **MCP PDF 리포트 읽기** — 100DPI PNG ImageContent, 200개 전수 테스트 완료
- [x] **실적/배당 일정 자동 수집** — events.json v2 구조, 매일 07:00 자동수집 (KIS+DART+yfinance), 매크로일정 포함, 텔레그램 알림

### 인프라
- [x] **MCP Streamable HTTP** — Claude.ai 연결 안정화, SSE 병행
- [x] **Gist 백업 409 수정** — 손상 Gist 교체, 신규 생성
- [x] **KRX OPEN API** — 승인됨. KIS API + SQLite로 대체되어 전환 불필요. GitHub Actions KRX 크롤링은 보조 데이터로 유지.
- [ ] **대시보드 인증** — Cloudflare Access + TODO 수정 + 투자판단 메모 기능
- [x] **워치리스트 단일화 (3파일 → 1파일)** — 2026-04-16 완료. watchalert.json 단일 소스, load_watchlist/us_watchlist는 wrapper. /watch/unwatch/addus/remus 통합, market 필드, buy_price>0 삭제 보호, WebSocket 40건 캡. 레거시 2파일 .bak 리네임.

---

## 🟢 P2 — 알파 도구 (Tier 1, 즉시 구현)

- [x] **F-Score / M-Score / FCF 메트릭** — TTM 기반, 12분기 DART 소급 26,584행 수집 (4/16). MCP `get_alpha_metrics` + `get_finance_rank(fscore/mscore_safe/fcf_yield)`. **남은 작업**: shares_out 재시도(자정 KST 후), main.py 일일 자동화 스케줄 1줄.
- [x] **내부자 거래 추적** — DART elestock.json, insider_transactions 테이블, 매일 20:00 체크, 30일 3명+ 매수 + 순매수 시 텔레그램 플래그. MCP `get_dart(mode='insider')` 추가 (4/15)
- [x] **FCF 메트릭** — F-Score/M-Score와 일괄 구축 완료 (위 항목 참조)

## 🔵 P2.5 — 알파 도구 (Tier 2, 다음 분기)

- [-] ~~**관세청 10일 수출**~~ — 2026-04-23 Phase 1~2 구축 후 같은날 전체 롤백. 발굴 도구 부적합(대기업 위주), 대부분 동행/후행 — 매수 시그널 X. MCP 도구 존재 = 어거지 호출 유혹(Slovic 1973). 4/19 거버넌스 롤백과 동일 패턴.
- [-] ~~**거버넌스/밸류업**~~ — 2026-04-19 3단계(자사주/배당/밸류업 계획) 구현 후 전체 롤백. 후행지표 비중 큼 + 진짜 알파(배당 증액/KRX 지수 편입)는 미구현 상태라 "간판만 비슷한 도구"로 판단. 재도전 시 배당 증액 비교 + KRX 편입 스크래핑 등 **핵심 알파 재설계** 필요.

## 🟢 P3 — 나중에

- [x] 뉴스 감성 분석 개선 — 오탐 줄이기 (4/18, KNU 사전+구문 보강 97%)
- [x] DB 변화 감지 스캔 — turnaround/fscore_jump/insider_cluster_buy 3종 추가 + 매일 19:05 알림 (4/18)
- [x] 공시 자동 알람 실시간화 — 30분 → 5분 (4/18)
- [x] 자료 종목별 자동 분류 — data/research/{TICKER}_{NAME}/ 2단계 계층 (4/18, dbc4076)
- [x] aiohttp 세션 풀링 — 48곳 마이그레이션, 14곳 제외(per-call timeout/WebSocket) (4/18)
- [x] ~~bot_architecture.md 생성~~ — 대신 `.claude/rules/schedule.md` 작성 (4/19). 전체 아키텍처 문서는 시스템 안정화 후(US 데이터 수집 등) 재고

### 정리
- [x] **Railway 완전 삭제** — 중복 발송 원인 (매크로/DART 2회), 4/15 삭제
- [x] **Oracle Cloud VM Terminate** — 4/15 Stop → 4/16 중복 알림 없음 확인 후 완전 Terminate

---

## ✅ 완료 (최근)
- [x] **레짐 일수 카운터 버그 수정** — days_in_regime 호출마다 +1 → 히스토리 날짜 기반 계산으로 변경 (4/23)
- [x] **US 레이팅 날짜 필터 근본 수정** — fetched_at→rating_date 필터, Hold→Hold 무변화 제외, 알림에 경과일수 표시 (4/23)
- [x] **US 레이팅 알림 이벤트 분류** — downgrade/target_cut/downgrade+target_cut 3분류 (4/23)
- [x] **문서 전체 업데이트** — CLAUDE.md(38개 도구), README.md(맥미니 전면 재작성), BOT_STATUS.md(최신 반영) (4/23)
- [x] **INVESTMENT_RULES v5** — 4개 핵심 규칙, 킬질문, 보유관리, VCP, 현금 에스컬레이션, AI 지침 (4/23)
- [x] **대시보드 이벤트 UI 수정** — D-day 정렬 고정 + 언더스코어→공백 (4/23)
- [x] **실적/배당 일정 자동 수집 (캘린더)** — kis_api.py 14개 함수, main.py 07:00 스케줄, events.json v2 구조 (4/23)
- [x] **프리셋 복구 (credit_unwind + foreign_accumulation)** — loan_balance_rate 컬럼 + foreign_hold_change_5d 계산 추가, ~7일 데이터 누적 대기 (4/16)
- [x] **KR_DEEPSEARCH.md 신설 (10 Step + PDF 게이트)** — US 대칭, 킬 조건 + 체크박스로 Step 생략 방지 (4/16)
- [x] **대시보드 thesis/ 폴더 노출** — 💡 투자 테제 섹션, 18개 딥서치 문서 (4/16)
- [x] **HANDOVER.md 폐기** — PROGRESS.md로 인수인계 일원화 (4/16)
- [x] **문서 stale 정리** — FILES/krx_db_design/bot_reference 업데이트, regime_update_notes 삭제, 네비 주석 추가 (4/16)
- [x] **MCP PDF 리포트 시각 분석** — 100DPI PNG, 836개 PDF 대응, 차트/도표 인식 확인
- [x] **MCP Streamable HTTP 트랜스포트** — POST/DELETE/OPTIONS /mcp, 세션30분, CORS
- [x] **Gist 백업 409 해결** — reports.json(1.5MB) 포함 Gist 손상 → 신규 Gist 생성
- [x] **read_file/read_report_pdf 우선순위** — PDF 읽기는 read_report_pdf 우선
- [x] **종목 분석 규칙에 PDF 추가** — INVESTMENT_RULES.md 4번째 단계로 등록
- [x] **전체 알림 중복 방지 16개** — macro_dashboard 덮어쓰기 버그 수정 (근본 원인)
- [x] **US 감시종목 장외 가격 수집** — is_us 가드 제거, 장외에도 전일 종가 캐시
- [x] **MCP read_file PDF 지원** — 경로+크기 반환 (2MB 제한)
- [x] **대시보드 📄 리포트 탭** — 종목별 카드 + /dash/reports/{ticker} 목록 + PDF 브라우저 서빙
- [x] **리포트 PDF 로컬 저장** — data/report_pdfs/{ticker}/{date}_{증권사}_{애널}.pdf (109건 다운로드)
- [x] **리포트 목표가/투자의견 자동 추출** — 정규식 + 와이즈 API 소급 (39%→88%)
- [x] **_save_pdf_local path traversal 방지** — 5단계 보안 (코드리뷰 지적)
- [x] **대시보드 📈투자/🔧봇개발 탭 분리** — TODO 3파일 체계
- [x] **리포트 SQLite 전환** — reports.json→SQLite, analyst 수집, 영구보관, 상한 제거
- [x] **리포트 수집 08:30** — 모닝 리포트 반영 위해 07:00→08:30
- [x] **리포트 알림 종목별 표시** — 한 종목 독식 방지, 종목별 최신 1건
- [x] **장마감/US 요약 중복 방지** — MACRO_SENT_FILE 날짜 체크
- [x] **DART 공시 중복 방지** — seen_ids 발송 전 저장
- [x] **KOSDAQ 수급 제거** — API 응답 전부 0 (공식 문의 필요)
- [x] 매크로 대시보드 수급 개선 (FHPTJ04040000)
- [x] 매크로 중복 발송 방지
- [x] 시간외 급등락 TOP 3
- [x] DB 수집 알림 한글화
- [x] Gist 백업 409 수정 (reports.json 제외)
- [x] 유니버스 확대 (KOSPI250+KOSDAQ350)
- [x] 컨센서스 히스토리 SQLite
- [x] collect_daily 안전장치 4개
- [x] rate limiter jitter
- [x] 매크로 스케줄 18:55
