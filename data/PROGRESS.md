# 세션 인수인계 — stock-bot

> **매 세션 시작 시 가장 먼저 이 파일을 읽을 것.**
> 패턴 출처: Anthropic "Effective harnesses for long-running agents" (claude-progress.txt 구조)

---

## 🔴 다음 세션에서 바로 할 일

**우선순위 순:**

0. **4/21(화) 19:05 daily_change_scan_alert 첫 발송 확인** — 프리셋 3종(turnaround/fscore_jump/insider_cluster_buy) + 평일 19:05 텔레그램. 워치/포트 제외 + 7일 쿨다운. fscore_jump는 ~7/15 이후 정상(히스토리 90d 필요). `change_scan_sent.json` 생성 확인.

0b. **DART 5분 간격 체감 검증** — `check_dart_disclosure` 30분→5분 (4/18, c8c3da0). 장중 공시→알림 지연 체감. 과발송 없는지 모니터.

0c. **aiohttp 세션 풀링 성능 체감** — `_get_session()` 48곳 마이그레이션 (4/18, c8c3da0). 18:30 daily_collect 소요 시간 단축 여부. 이전 평균 대비 비교.

0d. **research/{TICKER}/ 계층 구조 배포 확인** — 22개 파일 이관 (4/18, dbc4076). 대시보드 /dash-v2 종목 리서치 섹션 22개 카드 렌더링. 새 딥서치 쓸 때 `data/research/{ticker}_{name}/{종류}_{YYYYMMDD}.md` 경로 사용.

1. **4/20(월) 18:30 daily_collect_job 정상동작 검증** — 4/17(금) 미실행 재발방지 안전장치 3종 배포 완료 (8806555). 월요일 수집 후 DB + `[retry]` 로그 확인.

2. **4/20(월) 9:00~15:30 WS 실시간 연결 검증** — `ws://` scheme fix (c0b6169). `[WS] 연결됨` 로그 + 장중 손절 감시 동작 확인.

3. **4/8~4/11 4일 row 공백 판단** — 주중 3일(4/8/9/11) daily_snapshot 자체 없음. 복구 필요하면 kis_stock_price 과거 조회로 close/volume부터 전체 row INSERT 필요 — 범위 큼. 일단 "과거 공백 그대로 두고 미래만 정상화" 정책이면 스킵. 결정 필요. **+ 4/17도 같은 정책 적용 (collect_daily 설계상 backfill 불가 확인)**.

3. **DART 증분 수집 Phase6 모니터링** — 매일 02:00 KST `daily_dart_incremental` 스케줄 배포 완료 (2026-04-16). 첫 공시 발생일(분기 마감 + 45일 근처) 이후 텔레그램 알림으로 쿼터/수집 건수 확인. 분기 피크일(5/15, 8/14, 11/14) 신규 ~800종목 수집 예상.

4. **프리셋 복구 데이터 누적 대기** — credit_unwind/foreign_accumulation 코드 배포 완료 (2026-04-16). ~7일 수집되면 실제 스캔 결과 확인. `short_squeeze`는 ~5/14 자동 작동.

5. **KR_DEEPSEARCH 실전 검증** — 10 Step 템플릿 + PDF 게이트 추가됨. 다음 한국 종목 딥서치 시 사용자가 직접 복붙하며 Step 누락 여부 / 킬 조건 체감 확인.

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

## 📌 미국 애널 레이팅 3단계 (예정)

2단계 완료 (4/18, 커밋 예정). 3단계 스펙:

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
| 2026-04-15 | Railway 완전 삭제 | 중복 발송 원인 (매크로/DART 2회씩) |
| 2026-04-15 | 내부자 거래 `get_dart(mode='insider')` 추가 | 30일 3명+ 매수 알파 신호 |
| 2026-04-15 | 에이전트 3개 추가 (critic/verifier/debugger) | OMC 프롬프트 패턴 차용 |
| 2026-04-15 | KRX 레거시 대청소 (-2,357줄) | krx_update.py 좀비, Safari keepalive 좀비 |
| 2026-04-15 | CLAUDE.md 다이어트 275→146줄 | 매 세션 토큰 절약 |
| 2026-04-16 | Oracle VM Stop | 중복 발송 추가 원인 의심 |
| 2026-04-16 | Oracle VM Terminate | Stop 후 중복 알림 없음 확인, 완전 삭제 |
| 2026-04-16 | 워치리스트 단일화 (watchalert.json) | 3파일 파편화 26종목 불일치 해결, save/load 단일 경로 |
| 2026-04-16 | 배포 플로우: main 직행 | 1인 운영 봇, 브랜치/PR 생략 |
| 2026-04-16 | HANDOVER.md 폐기 | 1인 운영 + AI 페어, PROGRESS.md로 역할 일원화 |
| 2026-04-16 | 대시보드 thesis/ 폴더 노출 | 18개 Thesis 딥서치 문서 접근성 |
| 2026-04-16 | KR_DEEPSEARCH.md 신설 (10 Step + PDF 게이트) | US_DEEPSEARCH_v3와 대칭, Claude의 Step 생략 방지 |
| 2026-04-16 | F/M/FCF 알파 메트릭 4-Phase 구축 | TTM 기반 F-Score/M-Score/FCF, 12분기 DART 소급 26,584행, MCP get_alpha_metrics + 3 rank 추가 (커밋 9702a68→2ffa724) |
| 2026-04-17 | F/M/FCF 완전 가동 | shares_out 12분기 소급 24,310건 완료 + F-Score #7 보강 재계산. 전종목 F-Score 분포 정규(피크 4-5점), 우량 7+ 552종목(22%). 자동화 스케줄(ed5aa72). 다음: DART 증분 수집. |
| 2026-04-16 | F/M/FCF Phase6 DART 증분 자동화 | `search_dart_periodic_new` (list.json pblntf_ty=A, 정정공시 skip) + `collect_financial_on_disclosure` (중복체크, max_calls=1000 안전장치, _DART_INTERVAL 0.067) + main 02:00 daily 스케줄. 13 pytest 모두 pass (mock only, 실호출 0). |
| 2026-04-17 | Step 5 딥서치 수급 파이프라인 복구 | `kis_investor_trend_history` output1(현재가 dict)→output2(일별 수급 list) 근본 버그 수정 + today 빈응답 시 yesterday fallback. 부가 효과: `collect_daily` Phase3이 같은 함수 쓰므로 daily_snapshot의 4/8 이후 수급값 0 버그도 자동 복구. 4/13~4/16 11,444건 백필 + foreign_trend 캐시 재계산 (커밋 5014239). |
| 2026-04-18 | 뉴스 감성분석: KNU 사전+금융 규칙 교체 | 단순 키워드 카운트 오탐 → 점수 기반+컨텍스트 반전+순위기사 필터 |
| 2026-04-18 | 감성분석 2차 개선: 양보절+구문보강 (97%) | 없지만/아니지만 concessive 제외 negative lookahead + finance phrase covered 체크 + 구문 17개 추가 (192케이스 66%→97%) |
| 2026-04-18 | WS scheme `wss://` → `ws://` (c0b6169) | KIS 서버가 plain ws만 지원. TLS 1.2 fix (ea61753)는 오진. openssl/직접테스트로 진짜 원인 확정 |
| 2026-04-18 | daily_collect 안전장치 3종 (8806555) | 4/17 미실행 사건 재발방지: 포트 probe / post_init 당일 retry / 주간 무결성체크 |
| 2026-04-18 | US 애널 레이팅 MCP 3종 1단계 | StockAnalysis.com 기반 `get_us_ratings`/`get_us_scan`/`get_us_analyst`. daily_us_rating_scan KST 07:30 스케줄. 테이블 2 + 인덱스 3. 팀 워크플로 (architect→developer→test→specialist→reviewer→verifier) 병렬 실행으로 완성, 5/5 테스트. 2~3단계(overview 통합/discovery/sector) 후속. |
| 2026-04-18 | US 애널 레이팅 2단계 (실시간 감시) | `hourly_us_holdings_check` ET 12:00 / 16:30 평일 스케줄. 보유종목 48h 2건+다운그레이드 1건+ 조건 시 긴급 🚨 텔레그램. `daily_us_rating_scan` 에 📊 일일 요약 추가. `us_holdings_sent.json` ET 기반 키 + 48h auto-cleanup. 13/13 테스트 (기존 5 + 신규 8). Markdown escape + len 버그 수정 배포전 반영. |
| 2026-04-18 | KR 발굴 알림 (`daily_change_scan_alert`) | `get_change_scan` 3개 프리셋 추가(turnaround/fscore_jump/insider_cluster_buy) + 매일 19:05 KST 평일 자동 푸시. 워치/포트 제외, 7일 쿨다운(`change_scan_sent.json`), 각 상위 5개씩 최대 15건. 스모크 테스트: turnaround 113건(S-Oil Q4 +2356 from -1363 등), fscore_jump 0건(데이터 성숙 대기 ~7/15), insider_cluster_buy 3건(SK하이닉스 8명 +6267주). 데이터 품질 교훈: financial_quarterly.operating_profit `=0` 이 4,817건(누락 마커) → 엄격한 `<0` 필터 필수. |
| 2026-04-18 | aiohttp 세션 풀링 48곳 마이그레이션 | `_get_session()` TCP 연결 풀 재사용(이미 kis_api.py에 존재하나 미사용). kis_api 35/49, mcp_tools 5/5, db_collector 8/8. 14곳 제외(explicit timeout override / WebSocket ws_connect). 핫루프(종목별 수집/스캔) DNS 캐시 재사용으로 응답 속도 개선. 스모크: 삼성전자 216,000 + turnaround 113건 OK. |
| 2026-04-18 | DART 공시 체크 30분 → 5분 | `check_dart_disclosure` interval 1800→300. 장중 공시→알림 지연 평균 15분 → 2.5분. 기존 8~20시 필터 + 중복제거 유지. |
| 2026-04-18 | `data/research/{TICKER}/` 계층 구조화 | 22개 flat 파일 → 21개 TICKER 디렉토리. `AMD.md` → `AMD/main.md`, `009540_HD조선해양_딥체크_20260413.md` → `009540_HD한국조선해양/딥체크_20260413.md` 등. HD조선해양 중복 2파일 통합. 대시보드 `_handle_dash_research_file`: 2단계 경로 허용(max_slashes=1, research 한정) + `realpath` 검증 보안 강화. 라우트 `{filename:.+}`. thesis/ (42개)는 flat 유지(별도 섹션/용도). MCP read_file/write_file/list_files 는 기존 realpath 검증이 이미 서브디렉토리 지원 — 수정 불필요. |

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
