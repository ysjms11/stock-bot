# 세션 인수인계 — stock-bot

> **매 세션 시작 시 가장 먼저 이 파일을 읽을 것.**
> 패턴 출처: Anthropic "Effective harnesses for long-running agents" (claude-progress.txt 구조)

---

## 🔴 다음 세션에서 바로 할 일

**우선순위 순:**

0. **다음 일요일 03:00 KST 주간 US harvest 첫 실행 확인** — 1,010종목 × ~33분 스캔. 애널 풀 확장 트래킹.

2. **DART 증분 수집 Phase6 모니터링** — 매일 02:00 KST. 분기 피크일(5/15, 8/14, 11/14) 신규 ~800종목 예상.

3. **4/17/4/8~4/11 공백 정책** — 미래만 정상화 확정. collect_daily backfill 불가 (현재가 API만 사용). 예외 케이스만 `backfill_gaps.py` 로 처리.

4. **KR_DEEPSEARCH 실전 검증** — 다음 한국 종목 딥서치 시 10 Step 누락 여부 / 킬 조건 체감 확인.

5. **맥미니 배포** — `get_youtube_transcript` MCP 도구 추가됨(2026-04-24). 맥미니 pull 후 `pip install youtube-transcript-api` + 봇 재시작 필요. Claude Desktop/Code에서 유튜브 URL 넣으면 자동 자막 추출.

---

## 📌 미국 애널 레이팅 — 추가 발견 엔드포인트 (메모, 2026-04-18)

StockAnalysis.com 1단계 구축 중 탐색으로 발견. **1단계 스코프 포함 안 함, 2~3단계 참고용 기록.**

**✅ 작동 확인**
- `/api/symbol/s/{ticker}/overview` — 시총/PE/FwdPE/EPS/컨센타겟/어닝일
- `/api/symbol/s/{ticker}/statistics` — 밸류에이션/shares
- `/api/symbol/s/{ticker}/dividend` — 배당 이력
- `stockanalysis.com/analysts/{slug}/` HTML — 애널 커버 종목 리스트

**❌ 작동 안 함 (다른 경로 있을 수도)**
- /financials, /forecast, /insider, /institutional, /options, /short-interest

**활용 계획**
- 2단계: `/overview` → `get_us_ratings(mode="overview")` 통합. 딥서치 Step 1/6 자동화
- 3단계: 애널 HTML 파서로 톱 100명 커버 종목 리스트 구축

---

## ✅ 미국 애널 레이팅 3단계 완료 (2026-04-23)

커밋 75e0498 / 56a4bcc / 47cb16a — 5 Unit 전부 완료.

**완료 사항:**
- **Unit 1 (75e0498)**: DB 스키마 us_analysts + us_analyst_coverage (10+4 컬럼, 인덱스 3)
- **Unit 2 (56a4bcc)**: HTML 파서 4함수. mark-strouse 실측 OK (11종목 coverage)
- **Unit 3 (47cb16a)**: discovery 본 구현 (watched=1 톱 애널 상향 3건+ 종목)
- **Unit 4 (47cb16a)**: firm/sector 필터 (기존 stub 교체)
- **Unit 5 (47cb16a)**: weekly_us_analyst_report 일요일 19:00 KST
- **신규 MCP 도구 watch_analyst**: 톱 애널 확정/해제 (38→39개)

**다음 운영 단계:**
1. `build_top_analysts_candidates()` 호출로 톱 100 후보 리스트 생성
2. HTML 파서로 각 애널 메타 수집 (약 30~60분)
3. 운영자가 `watch_analyst(slug, watched=True)` 로 70~100명 확정
4. 이후 discovery 자동 가동

---

## 📌 미국 애널 레이팅 3단계 레거시 스펙 (참조용)

이전 계획 스펙:

- **톱 100 애널 리스트 구축**
  - `us_analysts` 테이블 신설 (slug, name, firm, sectors, stars, success_rate, watched)
  - StockAnalysis.com 애널 페이지 HTML 파싱 (2단계에서 미발견 경로 탐색)
  - 봇이 후보 추천 → 월이가 확정 (월간 재평가)
- **애널 커버 종목 리스트 DB 저장**
- **`get_us_scan(mode="discovery")` 본격 구현**
  - 감시 밖 종목에 톱 애널 상향 3건+ 감지
- **`get_us_analyst` 확장** (firm/sector 필터 활성화)
- **주간 리포트** (일요일 저녁, Sunday 30 준비용)
- **`/overview` 엔드포인트 통합** → `get_us_ratings(mode="overview")` (딥서치 Step 1/6 자동화)

예상 공수: 3~4시간

---

## 🟢 중장기 TODO (TODO_dev.md 참조)

- **P2 Tier 1 알파**: F-Score/M-Score, FCF 메트릭
- **P2.5 Tier 2**: 관세청 10일 수출, 거버넌스/밸류업
- **P3**: ~~뉴스 감성 개선~~ ✅, DB 변화 감지, 공시 실시간화

---

## 📌 주요 아키텍처 결정 (최근)

| 날짜 | 결정 | 이유 |
|------|------|------|
| 2026-04-15 | Railway 삭제 + main 직행 배포 + HANDOVER 폐기 | 1인 운영 봇 구조 단순화. 중복 발송 원인 제거. |
| 2026-04-15 | 에이전트 3개 추가 (critic/verifier/debugger) | OMC 프롬프트 패턴 차용 |
| 2026-04-15 | KRX 레거시 대청소 (-2,357줄) + CLAUDE.md 다이어트 | 좀비 코드 제거, 매 세션 토큰 절약 |
| 2026-04-16 | 워치리스트 단일화 (watchalert.json) | 3파일 파편화 26종목 불일치 해결 |
| 2026-04-16 | KR_DEEPSEARCH.md 신설 (10 Step + PDF 게이트) | US_DEEPSEARCH와 대칭, Step 생략 방지 |
| 2026-04-16 | F/M/FCF 알파 메트릭 4-Phase (9702a68→2ffa724) | TTM F-Score/M-Score/FCF, 12분기 DART 26,584행, MCP `get_alpha_metrics` |
| 2026-04-17 | F/M/FCF 완전 가동 + DART 증분 자동화 | shares_out 24,310건. 우량 7+ 552종목(22%). 02:00 daily 스케줄. |
| 2026-04-17 | Step 5 수급 파이프라인 복구 (5014239) | `kis_investor_trend_history` output1/output2 파싱 버그. collect_daily Phase3도 동시 복구. 11,444건 백필. |
| 2026-04-18 | 뉴스 감성분석 KNU 사전+구문보강 (97%) | 단순 키워드 → 점수 기반+양보절 제외. 192케이스 66%→97%. |
| 2026-04-18 | WS scheme `ws://` 수정 (c0b6169) | KIS는 plain ws만 지원. TLS 오진 재조사. |
| 2026-04-18 | daily_collect 안전장치 3종 + aiohttp 풀링 48곳 + DART 5분 | 4/17 미실행 방지, 연결 풀 재사용, 공시 지연 감소 |
| 2026-04-18 | US 애널 레이팅 MCP 3종 1+2단계 | StockAnalysis.com. 실시간 감시 ET 12:00/16:30. 13/13 테스트. |
| 2026-04-18 | KR 발굴 알림 (daily_change_scan_alert) | turnaround/fscore_jump/insider_cluster_buy 3종 프리셋, 매일 19:05 |
| 2026-04-18 | `data/research/{TICKER}/` 계층 구조화 | 22개 flat → 21개 디렉토리 |
| 2026-04-19 | 거버넌스/밸류업 전체 롤백 | 후행지표 판단. "간판만 비슷" 알파 없음. TODO 착수 전 선행/후행 판단 교훈. |
| 2026-04-19 | `.claude/rules/schedule.md` 신규 | 반복잡 4종 + 일일잡 26종 타임라인 |
| 2026-04-21 | US 레이팅 오탐 근본 수정 (d1b2c1d) | `fetched_at` → `rating_date` 필터. 첫 수집 수개월치 오탐 방지. |
| 2026-04-23 | 주간 US 유니버스 수집 잡 (12cf948/975ef5d) | S&P 500 + Russell 1000 합집합 1,010종목 × 주 1회 일요일 03:00 KST. Wikipedia 파싱 + 30일 TTL 캐시. 애널 풀 자연 확장 목적 (공식 톱 100 랭킹은 무료 7명 제한). 라이브 검증: HBM 중국 6개월 MoM +47%, QoQ +124% 확인. |
| 2026-04-23 | INVESTMENT_RULES v6 레짐 개정 (6e2c6f9) | 레짐 = 현금 관리 도구로 역할 재정의(매수 허가 필터 아님). 🟢 신규자제 조항 삭제 (6주 0매수 교훈). 현금 🟢 15~20% → **5~8%** (공격적). "현금은 비용" 원칙. Step 0 레짐게이트 → 레짐체크(현금 가이드용). 🟡에서 축적→🔴에서 VIX 단계별 3거래일 분할 투입. |
| 2026-04-23 | Claude Code Stop 훅 (전역 ~/.claude/settings.json) | 응답 완료 시 `afplay -v 255 Blow.aiff && say '작업 완료'`. PC+맥 인접 환경에 맞춤. 매 턴 알림. |
| 2026-04-23 | judge_regime v6 동기화 (a5cf996) | 4단계(🟢/🟠/🟡/🔴) → 3단계(🟢/🟡/🔴). 판정 지표 KOSPI/WTI/USDKRW/외인 제거, **S&P 200MA + VIX 2개만**. 🟠 참조 4곳 제거. TestJudgeRegimeV6 10/10 pass. |
| 2026-04-23 | 치명 KST 스코프 버그 수정 (42f3a14) | 604d775 도입 버그 — mcp_tools.py:4479 `KST = _ZoneInfo(...)` 로컬 재할당 → `_execute_tool()` 전체 KST 스코프 파괴 → 91건 UnboundLocalError. 로컬 할당 2줄 제거. Python 로컬 스코프 교훈. |
| 2026-04-23 | 대시보드 인증 + 편집 기능 (2d0ae78) | Cloudflare Access Gmail PIN 적용(`/dash/*`), `/health`·`/mcp` 무인증 유지. TODO 체크박스 토글(해시 충돌감지) + 항목 추가 + 투자판단 입력 폼. 신규 함수 4 + POST 라우터 3 + `_atomic_write`. TODO_dev.md P1 완료. |
| 2026-04-23 | critic 리뷰 hotfix (8f58f8c) | MAJOR 2: `_inline()` href XSS 차단(`_sanitize_url` 스킴 화이트리스트+속성 이스케이프, 8개 공격 페이로드 유닛테스트 통과) + 서버측 코드블록 검사(curl 우회 방지). MINOR 2: dead code 제거 + 409 UX 개선(confirm→reload). |
| 2026-04-23 | E2E 검증 6/6 PASS | TODO 토글 왕복 + 해시 충돌(409) + 코드블록 우회 차단(400) + XSS 7케이스 차단 + 투자판단 병합(regime/notes/actions/grades) + `/dash` 10섹션 렌더. critic 지적 "HTTP 요청 실증 갭" 완전 해소. |
| 2026-04-24 | DART 수시공시 본문 조회 + 알림 요약 (f1969d5) | get_dart MCP 2종 추가(disclosure_list/disclosure_read), 캐시 `data/dart_disclosures/{ticker}_{rcept}.txt` 50KB truncate + path traversal 차단. 텔레그램 알림 5종 요약(잠정실적/자사주/배당/풍문/기타). 단위테스트 6/6 + 라이브테스트 3/3 PASS(현대로템·한화에어로·하나금융). 파싱 실패 try/except fallback. |
| 2026-04-24 | INVESTMENT_RULES v6 전면 개정 + 정합성 동기화 | `ac1f049` 전면 교체: 확신등급(A/B+/B/B-/C/D) 폐기 → 3-Gate + 비중 3단계(Core/Standard/Starter), F-Score ≥7→≥8(Piotroski 공식), Short Float 5%→10/20/30%, VIX 확률값/현금 31% 감소/리포트 41% 등 환각 수치 삭제, O'Neil/Minervini 7-10% 손절 정확화, Odean 오인용 제거. tag `rules-v5-before-overhaul` 롤백점 보존. 후속 `7639ec3`: KR/US_DEEPSEARCH + CLAUDE.md 싹다 동기화. thesis/research 폴더는 과거 스냅샷이라 제외. |
| 2026-04-23 | 관세청 수출 모듈 완전 롤백 | 2일 공수 구축 후 사용자 판단으로 제거. 이유: (1) 발굴 도구 부적합 (대기업 위주, 중소형 발굴 X), (2) 대부분 동행/후행 — 매수 시그널 X, (3) MCP 도구 존재 = 어거지 호출 유혹 (Slovic 1973). 4/19 거버넌스 롤백과 동일 패턴. US 애널 3단계 + Russell 1000은 별개 유지. 제거: trade_api.py(432줄), hs_ticker_map.json(108종목), MCP `get_export_trend`, 월 3회 잡(trade_decade/trade_monthly), trade_monthly/trade_preliminary_decade DB 테이블, INVESTMENT_RULES §5/KR_DEEPSEARCH Step2/schedule.md 월간잡/bot_samples.md §40, CLAUDE.md 6→5파일. |

---

## 🧠 최근 세션 학습 (Lessons learned)

1. **API 응답 필드는 전수 검토할 것** — `whol_loan_rmnd_rate` 이미 Phase 1에 있었는데 모르고 Safari fetch 만듦. 오판이 구조적 결정까지 끌고 감.
2. **"죽은 코드" 판단 전 데이터 성숙도 체크** — short_squeeze는 코드 정상, 과거 데이터 0이라 일시적으로 빈 결과일 뿐이었음.
3. **사용자 지적 신뢰** — "KRX Safari 대체됐던 거 같은데"라는 기억이 정확했고, 재검증으로 2,357줄 청소로 이어짐.
4. **팀 구조 원칙 지키기** — Opus가 직접 구현 안 하고 Sonnet 에이전트에 위임. 코드 수정은 python-developer.
5. **"맥미니 = 다른 서버" 편향 주의** — 워크트리가 `/Users/kreuzer/stock-bot/.claude/worktrees/` 아래라 본체가 맥미니 자체임을 잊고 "배포 필요"라 말함. 사용자가 "니가 맥미니야"로 교정.
6. **문서는 복붙 템플릿 + 킬 조건 없으면 Step 생략됨** — KR_DEEPSEARCH 초판은 설명문만 → Claude가 건너뜀. US 패턴(━━ STEP N ━━ 헤더 강제, 킬 조건, 체크박스) 차용으로 해결.
7. **리뷰 2중 체제의 가치** — code-reviewer + critic 병렬로 워치리스트 단일화 치명 6건(wrapper fallback, 직접참조, WebSocket 41건 초과) 캐치. 커밋 전에 막음.
8. **DART API 한도는 stockTotqySttus가 더 빡빡** — 4/16 fnlttSinglAcntAll 34k콜은 통과, 직후 stockTotqySttus 1k콜에서 status=020 (한도초과). 두 API가 다른 쿼터 풀을 쓰거나 stockTotqySttus가 별도 제한. 다음에 같은 일정으로 두 API 모두 돌리면 실패하니 분리.
9. **DART CF 직접법 회사는 감가상각 노출 안 됨** — 삼성/SK하이닉스/현대차 등 대형 직접법 채택사는 fnlttSinglAcntAll의 sj=CF에 "감가상각" 계정 없음. 결과: 22%만 채워짐 → M-Score DEPI 계산 불가. 별도 데이터 소스(FnGuide/주석) 없으면 구조적 한계.
10. **Python stdout 버퍼링 함정** — nohup + python3 -u 했는데도 print line buffering이 일정 시점 후 끊김. 장기 실행 모니터링은 DB 카운트 기반 polling이 더 신뢰. (Phase 1.5 백그라운드 80분 진행 중 로그 stuck 경험)
11. **한 함수 버그가 여러 경로 파급** — `kis_investor_trend_history` 의 output1/output2 파싱 버그 1곳이 MCP `get_supply(history)` + `collect_daily` Phase3 양쪽 동시 고장의 원인이었음. 수정 1줄로 둘 다 복구. 버그 진단 시 "이 함수 누가 쓰는지" 사전 grep 필수 — 영향 범위 과소평가 금지.
12. **KIS API 응답 스키마가 조용히 바뀜** — 공식 공지 없이 `output1: list → dict + output2: list` 변경 발생. 상담원도 "공식 자료에서 근거 확인 불가"로 회피. 주기적 응답 구조 스모크 테스트 필요 (특히 장기 운영 TR_ID).
13. **"수집 성공 but 0값" 함정** — NULL/0 구분 필수. 4/12~4/16 "수급 100% 충원"으로 보였지만 실은 전부 0. `COUNT(col)` 뿐 아니라 `SUM(CASE WHEN col=0 ...)` 도 모니터링에 포함해야.
14. **KIS 공개 채널은 정책/공지 담당 — 개별 TR 장애는 고객의소리** — 상담원이 "TR_ID 확인 불가"라고 말해도 TR이 없다는 뜻 아님. 개별 로그 분석은 별도 경로 필요. TR 유효성은 우리 쪽 live test가 가장 빠름.
15. **에이전트 합의 ≠ 정답** — WS WRONG_VERSION_NUMBER를 TLS 버전 이슈로 단정, debugger + code-reviewer 에이전트 둘 다 동의. 사용자 "서버 불안정? 그럴리가" 반문으로 재조사. openssl s_client + 직접 ws 연결 테스트로 `wss://`→`ws://` scheme 이 진짜 원인 확정. **"그럴듯한 진단"은 증거(네트워크/직접 테스트)로 반드시 검증**. 에이전트 여러 명이 동의해도 틀릴 수 있음.
16. **collect_daily() 과거 backfill 불가** — 내부 `kis_stock_price` (FHKST01010100) 가 현재가 API라 date 파라미터 무시. 과거 일자 복원은 `kis_daily_closes()` (FHKST03010100, 일봉) 기반 별도 스크립트 필요. 기본시세만 복원 가능, 수급/공매도/loan은 별도 일별 API 필요. "재수집 가능" 이라는 전제 깨짐.
17. **데이터 품질 검증: 0 vs NULL 구분 필수** — turnaround 프리셋 개발 중 financial_quarterly.operating_profit=0.0 이 4,817건(누락 마커) 발견. 첫 구현에서 `<=0` 필터로 211건 매치 → 카카오뱅크처럼 이전 분기 전부 0.0인 종목이 "턴어라운드" 오탐. `<0` 엄격 필터로 113건 정상화. 교훈: 스크리너 작성 전 해당 칼럼의 0/NULL 분포를 `SELECT SUM(CASE WHEN col=0 THEN 1 END), SUM(CASE WHEN col IS NULL THEN 1 END)` 로 사전 검증.
18. **기존 헬퍼의 미사용 탐지** — `_get_session()` aiohttp 풀러가 kis_api.py에 존재했으나 63개 호출 지점이 여전히 `async with aiohttp.ClientSession() as s:` 로 매 호출 새 세션 생성 중이었음. "이미 있는 인프라를 실제로 쓰는지"는 grep 검증 필요. 새 헬퍼 추가보다 기존 헬퍼 보급률 확인이 우선.
19. **스모크 테스트의 첫 번째 결과를 의심하라** — turnaround 구현 첫 테스트에서 211건이 나왔을 때 "많다" 직감이 있었으나 기능 자체는 작동했음. 샘플 데이터(카카오뱅크 prev=0) 실제로 열어본 뒤에야 필터 오류 확인. 카운트만 보고 넘어가지 말 것. 최소 1건이라도 `tail -n 3` 으로 실제 내용 확인.
20. **TODO에 있다고 구현 금지 — 알파 원천 먼저 판단** (4/19 거버넌스 롤백): 원래 TODO "거버넌스/밸류업 3~6%/년" 보고 1/2/3단계 구현 → 배포 후 사용자 지적으로 전체 롤백. 공시 기반은 본질적으로 동행/후행 시그널 + 진짜 알파(배당 증액 비교/KRX 지수 편입)는 미구현이라 "간판만 비슷"했음. 교훈: **TODO 항목 착수 전 "이게 선행 알파인가, 후행 관찰인가"** 먼저 판단. 기존 선행 시그널(fscore_jump/insider_cluster/foreign_accumulation/short_squeeze)과의 **가치 중복** 체크 필수.
21. **시스템 안정화 전 아키텍처 문서 금지** (4/19 bot_architecture.md 결론): US 데이터 수집 등 기반 작업 미완 상태에선 시스템 전반 문서는 3~6개월 내 stale. 스코프 좁은 문서(schedule.md = 스케줄만)만 만들고, 전체 아키텍처는 기반 안정화 후 재고. CLAUDE.md + rules/ 분산 구조가 현 단계에선 더 유효.
22. **유튜버/블로거 인용 주장은 실증 없으면 믿지 마라** (4/23 관세청 수출 매핑 딥서치): "라면 r=0.98" "미용의료기기 r=0.97" 주장 실측해보니 각각 +0.41(무효), -0.57(역상관). 에이전트A 리서치도 마찬가지 — 2차 정보 그대로 인용하면 위험. **구현 전 피어슨 상관 n≥18 통계 검증 필수**. HS 매핑 30종목 고신뢰 8개 → 실증 통과 3개로 급감 (삼성전자/SK하이닉스/삼성전기만). 4/19 거버넌스 롤백과 같은 패턴. 교훈: **알파 주장은 숫자로 증명 전에는 가설**.
23. **"선행"과 "동행" 구분 필수** (4/23 실증): 반도체 DRAM/HBM r=0.93~0.96이지만 lag=0 (동행). 즉 수출 폭발 시점엔 이미 주가 상승 중 → "수출 좋음 = 매수" 전략 X, **확인 지표로만**. 진짜 선행은 DRAM 스팟가 등 더 빠른 지표. 알림 메시지 v2는 "동행/선행 N개월/후행" 태그 필수.
24. **"통관 수출"과 "수주 기반 업종" 구분** (4/23 실증): 변압기/조선/방산은 수출 통관 데이터가 r<0.35 무상관. 이유: 통관 시점 = 납품 시점 = 수주 후 2~12개월 후행. 이런 업종은 **수주 공시**가 진짜 선행 알파. 매핑에서 confidence 0.35 미만으로 낮추고 "수주 우선" 플래그 필수.
25. **Python 로컬 스코프 버그 — 함수 내 할당 있으면 전체가 로컬** (4/23 KST 버그): `mcp_tools.py:4479` 에 `KST = _ZoneInfo("Asia/Seoul")` 추가 시 `_execute_tool()` 전체에서 `KST`가 로컬 변수로 간주 → 4479 이전 참조 23곳 전부 UnboundLocalError. Phase 3 pytest에서 91건 failure로 발각. 교훈: **모듈 스코프 전역과 동일 이름 로컬 할당 금지**. 필요 시 `_kst` 등 다른 이름 사용. `from module import *` 로 들어온 심볼 재할당 시 특히 위험.
26. **실증 vs 시장 통찰 우선순위** (4/23 400종목 딥서치): Agent 2 시장 통찰이 K-POP 음반(HS 852349) 을 "숨은 알파"로 제시 → 실증 결과 하이브 p=0.075 미달, 스튜디오드래곤 역상관. 이유: HS 852349 = 음반+영상+게임 혼합 노이즈. 교훈: **가설(시장 통찰) → 실증(수치 검증) → 매핑** 순서 필수. 시장 통찰이 틀릴 수 있음을 전제해야.
27. **화학 섹터 역상관 = 스프레드 프록시 발견** (4/23 400종목): HS 390110 폴리에틸렌 수출 ↑ → LG화학/롯데케미칼/금호석유 등 12종목 r ≤ -0.6 (lag=6m). 해석: 범용 플라스틱 수출 급증 = 공급과잉 = 스프레드 악화. **역상관도 실전 알파**가 될 수 있음. 매핑에 "역상관 경고" 플래그 추가 필요.
28. **섹터 기반 자동 HS 매핑의 한계** (4/23 B 에이전트): Phase 2에서 전자소재 섹터가 무조건 HS 854121(트랜지스터)에 매핑됨. 결과 솔브레인/코스모신소재/나노신소재 전부 오매핑(실제로는 반도체 에천트/양극재/특수소재 생산). 역상관 62종목 분석 결과 **50%가 섹터→HS 자동 매핑 결함**. 교훈: 섹터 레벨 자동화는 1차 후보 생성용만. **Phase 3 필수**: DART 사업보고서 매출 구성 파싱 → 종목별 정확한 HS 결정 + 지주사 자동 제외 필터.
29. **지주회사 부호 반전 = 자동 제외 시그널** (4/23 B 에이전트): 아모레퍼시픽홀딩스 r=-0.77 vs 아모레퍼시픽 본사 r=+0.61 동일 HS에서 **부호 반전**. 지주사 구조(자회사 지분+현금성자산+투자손익)가 본사 영업실적과 다른 주가 반응. 지주사 자동 제외 필요 — 한화/한화3우B/DL/HD현대 모두 동일 패턴.
30. **MCP 도구 존재 = 어거지 호출 유혹** (4/23 관세청 수출 롤백): 2일 공수로 구축 후 사용자 지적으로 전체 제거. 이유: (1) "MCP에 도구 있으니 참고삼아 돌림" → thesis 안 흔들 자신 없음, (2) Slovic 1973: 5개 변수 정확도 17% = 40개 정확도 17%, 정보 늘리면 확신만 2배, (3) 4/19 거버넌스 롤백과 동일 패턴 — "간판만 비슷한 도구". 교훈: **구현 전 "이 도구 있으면 내가 쓸까, 쓴다면 어느 시나리오에?" 자문 필수**. 활용 시나리오 구체 예상 안 되면 구현 금지.
31. **UI 렌더링 함수 = 잠재적 Stored XSS 벡터** (4/23 `_inline()` 수정): 기존 `_md_to_html` 은 `<`/`>` 만 escape 했으나 마크다운 링크의 URL 부분(`\[...\]\(URL\)` 의 `URL`)은 무검증. 파일 쓰기 엔드포인트 추가 순간 **공격 면적 급증** — TODO/decision 내용이 사용자 입력으로 변질. Cloudflare Access 가 외부는 막지만 내부망(192.168.0.x:8080) 직접 접근은 막지 않음. 교훈: **파일 쓰기 엔드포인트 추가 시 "이 파일이 다시 렌더되는 경로 전체" 조사 필수**. 렌더 파이프라인의 과거 "읽기 전용 전제" 가 순식간에 깨짐. `_sanitize_url` 같은 스킴 화이트리스트를 선제적으로 적용. critic 에이전트가 이 결함을 잡음 — **독립 리뷰의 구체적 가치 사례**.

---

## 🛠 세션 시작 루틴

매 세션 시작 시 순서대로:

```bash
pwd                          # 1. 작업 디렉토리 확인
git log --oneline -10        # 2. 최근 커밋 훑기
cat data/PROGRESS.md         # 3. 이 파일 (다음 할 일)
cat data/TODO_dev.md         # 4. 봇 개발 TODO
cat data/TODO_invest.md      # 5. 투자 TODO (필요시)
```

이 루틴 후 사용자 요청 처리 시작.

---

## 📝 업데이트 규칙

- **세션 종료 직전**: "다음 세션에서 바로 할 일" 섹션 갱신
- **중요 결정 시**: "주요 아키텍처 결정" 표에 한 줄 추가
- **실수/교훈 발견 시**: "최근 세션 학습"에 한 줄 추가
- **작업 완료 시**: TODO_dev.md 체크 + 필요시 PROGRESS.md "다음 할 일"에서 제거
- **150줄 이하로 유지** — 오래된 결정/학습은 주기적으로 쳐낼 것
