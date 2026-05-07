# 세션 인수인계 — stock-bot

> **매 세션 시작 시 가장 먼저 이 파일을 읽을 것.**
> 패턴 출처: Anthropic "Effective harnesses for long-running agents" (claude-progress.txt 구조)

---

## 🔴 다음 세션에서 바로 할 일

**우선순위 순:**

1. **🟢 5/6 (수) 19:00 KST `watch_change_detect` 검증** — 5/5 임계값 강화 + shadow 버그 수정 후 첫 평일 알림. 워치 50종 알림 18개 → 5~7개 압축 + SQLite 5/5 데이터 기반 정확성 둘 다 검증.

2. **🟢 5/10 (일) 04:00 KST `weekly_us_analyst_sync` 정상 발송 검증** — 4/27 첫 자동 실행 KeyError 실패 → 5/5 fix 후 첫 정상 발송. 텔레그램 메시지 도착 확인.

3. **🟢 5/10 (일) 07:15 KST `weekly_financial` 검증** — 30→60분 상향 + per-ticker wait_for(10s) + Phase별 elapsed 로그 후 첫 실행. Phase A/B/C 정상 완료 + dict 알림 도착 확인. 펜딩 결정: daily_dart_incremental 와 redundancy 검토 (분기 피크일만 실행으로 축소?).

3b. **🟢 5/11 (월) 07:00 KST `weekly_universe_update` 검증** — 페이지네이션 fix (M → F or M) 후 첫 실행. KOSPI 250 + KOSDAQ 350 정상 회복 (60종목 → ~600종목).

4. **워치리스트 한국 11종 + 미국 17종 딥서치 (~28종목)** — 워치 50개 → 매수 가능 검증된 종목으로 압축 목표.

5. **DART 증분 수집 Phase6 모니터링** — 매일 02:00 KST. 분기 피크일(5/15, 8/14, 11/14) 신규 ~800종목 예상.

6. **공매도 비중 높은 보유 종목 모니터링** — LG엔솔(딥서치 결과) 12~20% 공매도 비중. 숏스퀴즈 vs 추가 하락 변곡점.

7. **🆕 KR_EXIT/US_EXIT 활용 매도 판단** — SK하이닉스 5/19~ (8-week hold 만료) STEP 재실행. LS ELECTRIC trailing stop 모니터링 (정점 -15% 도달 시 부분 매도 검토). 프로젝트 지침에 "한국 매도 → KR_EXIT.md / 미국 매도 → US_EXIT.md 로드" 1줄 추가 필요.

---

## 📜 5/5 세션 — 운영 안정화 + Shadow 버그 사고

### ⓪ 자동 잡 5건 검증 + 버그 발견
- 4/27 첫 자동 실행 시 weekly_us_analyst_sync `KeyError: 'auto_watched'` 실패
- 5/2 텔레그램 false positive 2건: "5/1 누락" + "재무 30분 타임아웃"
- 5/5 사용자 지적: 워치 변화 알림 18건 + SK하이닉스 "이평선 수렴 -0.2%"

### ① weekly_us_analyst_sync 키 미스매치 (5c88061)
- db_collector 반환 `auto_watched_a`, `tier_s_count`, `criteria` vs main.py 참조 `auto_watched`, `min_stars`, `min_calls`
- main.py 메시지 포맷 정정 + Tier S 카운트 + criteria 노출 추가

### ② weekly_sanity 휴장일 + weekly_financial 타임아웃 (309dbd9)
- `_KRX_HOLIDAYS` frozenset (2026 13개 공휴일) + `_is_krx_business_day()` 헬퍼
- weekly_financial 30분 → 60분, 결과 dict 분해 (IS/BS/DART 카운트)

### ③ KRX 공휴일 갱신 알림 자동화 (5f009b8)
- 매주 일요일 weekly_sanity 안에서 당해 등록 < 8건 시 텔레그램 알림
- 2027년 1월 첫 일요일부터 자동 발동

### ④ watch_change_detect 임계값 강화 (99016ba)
- 감시가 근접: 5% → 2%
- 이평선 수렴: `abs<3` → `abs<1.5 AND change_10d<0` (실제 수렴 중인 종목만)
- 외인 매수 전환: 5d≥60% → 5d≥70%
- 5/4 SQLite 검증: 전종목 2756 중 812(29%) → 168(6%) 통과

### ⑤ 🚨 load_krx_db shadow 버그 (5165971) — 한 달 stale 데이터 사고
- **사용자 지적이 정확** ("데이터 이상한 거 같다")
- krx_crawler.py L17: `from db_collector import load_krx_db` (SQLite)
- krx_crawler.py L511: `def load_krx_db(...)` ← 무조건 재정의 (레거시 JSON, 4/7 마지막)
- `from krx_crawler import load_krx_db` 가 final namespace의 L511 정의를 받음
- main.py 3곳 (`watch_change_detect` 등)이 4/7 데이터 보고 알림 발송
- 수정: L511 def를 `if not _USE_SQLITE:` 가드 안에 배치
- 검증: SK하이닉스 ma_spread 알림값 -0.2% (4/7 stale) → 실제 +33.83% (5/4)

### ⑥ AMD watchalert 정리 + wording (ea5d8b7)
- 매도 후 AMD watchalert 잔존 → 노이즈. 제거.
- 매도 트리거 메모는 `data/thesis/AMD.md` 보존 (재매수 후 stoploss/target 시스템 사용)
- "도달!" → "≤ ${buy_price} ({gap:+.1f}%)" + 헤더 "진입!" + 부제 "(현재가가 매수희망가 이하로 진입)"

### ⑦ shadow 가드 + 레거시 정리 (c9b6004)
- `_load_history` / `scan_stocks` 동일 shadow 패턴 (prod 영향 X, 잠재 trap)
- 모듈 끝 `if _USE_SQLITE: from db_collector import ... as ...` export alias
- 검증: `krx_crawler.{load_krx_db, _load_history, scan_stocks} is db_collector.{...}` 모두 True
- `data/krx_db/` 디렉토리 삭제 (232 JSON, 1GB 회수, prod 사용 X)

### ⑧ Silent failure 전수조사 + 2건 발견/수정 (c8b71c1)
사용자 "할 거 더 있어?" 질문에서 디스크 분석 → 다음 발견:
- **weekly_universe_update 60종목 (3주째 stale)**: KIS market-cap API tr_cont 응답값이 어느 시점 "M" → "F" 변경. 코드 `!= "M"` → 첫 페이지 30종목만 받고 break. 가드(<100) 발동으로 silent. 수정: `not in ("F", "M")`.
- **weekly_financial Phase A hang (60분 또 타임아웃)**: shared session 30s/콜 + 한 종목 hang 시 전체 막힘. 진행 로그 200건마다 + 버퍼링 = 가시성 0. 수정: per-ticker `wait_for(10s)` + 진행 로그 50건마다 + flush=True + Phase 시작/완료 elapsed 노출.

### ⑨ Stale 파일 정리
- `reports.json` 1.5MB (SQLite 전환 후 dead, read/write 호출 0건)
- `sector_sample_350.json` 80KB (코드 참조 0건)
- `krx_cookies.json` 68B (Safari 시절 legacy)
- 합 ~1.6MB. 빈 *.json 4개 (compare_log/sector_flow_cache/us_watchlist/regime_transition_sent)는 활성 사용 중이라 보존.

### 🎯 5/5 커밋: 9건 (5c88061 → c8b71c1) + 로컬 정리 2건 (krx_db 1GB + stale 1.6MB)

---

## 📜 이번 세션 (4/24~4/26) 큰 작업 종합

### ① daily_collect 자가진단 (4/25) ✅
- 4/24 18:30 미실행 사건(원인 미확정) 대응
- 평일 19:15/20:15/21:15/22:15 네 번 자가진단 → 0건 시 재실행
- `daily_collect_sanity_check` 함수 (main.py)

### ② US 애널 마스터 자동 sync (4/25) ✅
- 1,902명 ratings 데이터 vs 마스터 13명 갭 복구
- `sync_us_analyst_master` (db_collector) + 일요일 04:00 자동
- 결과: 마스터 13→1,902명, watched 12→254명

### ③ 3-Tier 시스템 (4/25) ✅
- avg_return 컬럼 추가 + `is_tier_s_analyst()` 런타임 분류
- **Tier A** (watched=1): 별점≥4.0 AND 적중률≥60% AND 콜≥10 OR 잠수형 거장 (4.8/80/7)
- **Tier S** (런타임 31명): ① 활발 톱 ② 잠수형 거장 ③ 고수익 거장(Goldsmith UBS +265%)
- 차등 알림 (🚨🚨🚨 / 🚨🚨 / 🚨 / ⚠️)

### ④ get_us_buy_candidates (4/25) ✅
- 톱애널 추천 + TP 업사이드 충족 미국 매수 후보 raw 데이터
- 기본 180일/1명+/+20%/limit 50 → ~50종목 sweet spot
- 정렬·필터·해석은 LLM이 동적 (점수제 박지 않음)
- 검증: SARO +36% / WWD +22% / BIIB +25% (Tier S+A 강함)

### ⑤ FMP 통합 (4/26) ✅
- "왜 그 TP인가" 본문 답
- `fmp_earnings_transcript`: 분기 5만자 (CEO 가이던스 + 톱애널 Q&A)
- `fmp_price_target_summary`: 1m/3m/1y 평균 TP + 카운트
- `fmp_analyst_estimates`: 매출/EBITDA/순이익 향후 5년
- `fmp_stock_grades`: 증권사 등급 변경 이력
- MCP 도구 2개 추가 (`get_us_earnings_transcript`, `get_us_analyst_research`)
- 무료 250 calls/day (보유/워치 충분)
- `.env FMP_API_KEY` 설정 완료

### ⑧ 외부 시그널 + 연기금 (NPS) 자동 추적 (4/27 저녁) ✅
- **Polymarket + Treasury Curve** (4/27 오후):
  - `fetch_polymarket()`: 매크로/지정학/정치 prediction market (24h $500K+ 노이즈 컷)
  - `fetch_treasury_curve()`: FRED API 10Y/2Y/3M (Estrella-Mishkin 1998 침체 시그널)
  - `fetch_external_macro_signals()`: 통합
  - MCP `get_polymarket` + `get_macro_external` 도구
  - 매크로 대시보드 (06:00, 18:55) `_format_external_signals` 자동 첨부
  - SAT_PORT_CHECK Phase 1 매크로 8변수 (Fed 인하 확률, 10Y-2Y 추가)
  - SUN_DISCOVERY Phase 1 mispricing 후보 (컨센 vs Polymarket 차이)
  - `daily_event_d1_alert` 19:30 평일 (FOMC/어닝/매크로 매칭 시 Polymarket+Treasury 첨부)
- **연기금 (NPS) 종목별 양방향 매매 추적** (4/27 저녁):
  - KRX 정보데이터시스템 인증 (KRX_ID/KRX_PW .env 설정 완료)
  - pykrx auto-login → 연기금 단독 매매 데이터 fetch
  - `pension_flow_daily` 테이블 (영구), 4/17~4/27 백필 완료
  - `daily_pension_collect` 16:30 평일 + `daily_pension_alert` 19:00 평일
  - 알림: 시총 대비 % 기준 정렬, 절대금액 보조 표시
  - 4 섹션: 보유 양방향 / 워치 양방향 / 발굴 매수 TOP10 (시총%) / 발굴 매수 TOP10 (절대금액)
  - 너 포트/워치 외 = **매수 시그널만** (매도는 무의미)
  - MCP `get_pension_flow(days, market, top, held_watch_only)`
  - SAT_PORT_CHECK / SUN_DISCOVERY Phase 1 명시
- **컨센 누적 trend 감지 보강** (4/27):
  - 단일 일 5% 임계 → + 15일 누적 3% 추가 (점진 상향 캐치)
  - 효성중공업 +3.0%/2주 같은 경우 잡힘 (이전엔 누락)
  - 30%+ 변화 = corporate action 노이즈 컷
  - 단일 changes 중복 제거
- **주말 루틴 v2 텔레그램 알림** (4/27):
  - SAT_PORT_CHECK / SUN_DISCOVERY 파일 신설 (data/)
  - 토 09:00 / 일 09:00 알림 (Claude.ai 프롬프트 템플릿)
- **커밋**: 245d094 / 56867cc / a022d4f / df5ecfb / c43e451 / a9a54e8 / 1b86d93 / 0a0c4b3

### ⑦ 비종목 리포트 카테고리 풀구축 + 노이즈 필터 (4/26 저녁) ✅
- **DB 스키마**: `reports.category` 컬럼 + 인덱스. 기존 3,356건 = 'company'
- **신규 4 카테고리**: industry / market / strategy / economy (네이버+한경 무로그인)
  - 한경 페이지네이션 (5페이지, 100건 cap 해제) — industry 419건, market 234건 누적
  - `_IND_/_MKT_/_STR_/_ECO_<sha1[:10]>` 합성 ticker (UNIQUE 충돌 회피)
- **실측 1주일 정독 (4/20~4/26 산업+전략 37건)** 후 정밀 노이즈 필터:
  - `_NOISE_RULES`: 시장 모닝브리프 + 유진투자증권 News Comment + 키움 시황/FICC Daily + 대신 퀀틴전시 플랜
  - `_is_noise()` 헬퍼 — 수집 단계 SKIP
  - 한경 EC 파싱 버그 수정 (td[1] 카테고리 라벨 cell 감지)
  - dedup (date+source+title) — 35건 중복 제거
- **결과**: 1주 168 → 107건 (876K 토큰, Claude.ai 1M 안전)
- **MCP 확장**: `manage_report(category=, days=, ...)` 다중 카테고리 + 카테고리별 collect
- **위클리 알림 (4/26 추가)**: 매주 일요일 19:07 `weekly_report_digest_notify` 잡 — 통계 + Claude.ai 프롬프트 템플릿 텔레그램 push. 봇 판단 X (사용자 직접 Claude.ai 호출). 첫 자동 발송 4/27(일) 19:07.
- 커밋: f401d9d / 8d7112e / 7aaae48 / e5f0746 / f26e01c

### ⑥ KR_EXIT.md + US_EXIT.md 매도 프레임 신설 (4/25~4/26) ✅
- **US_EXIT.md** (4/25, 30.7KB): 미국 매도 판단 프레임 (Martineau 2022 PEAD 대형주 소멸, FactSet Sell 4.8% 희소성, IRS LTCG/STCG 22%p 격차, Munger 1994 USC 정확 인용)
- **KR_EXIT.md** (4/26, 33.8KB): 한국 매도 판단 프레임 (KCMI 2026 김준석 한국 TP 정보가치 소멸, Choe-Kho-Stulz 1999 외국인 destabilize 부정, 거래세 2025 0.15%/2026 0.20% 정정)
- 공통: LLM 편향 차단 10규칙 (Sharma+Laban+Huang+Li 4중 차단), 3경로 의사결정 트리 (Fisher 1958 Ch.6), 학술 강도 4단계 라벨
- **SK하이닉스 EXIT 1호 실전 적용** (4/24): 3주 +51% 상황에서 3경로 0/3 + O'Neil 8-week hold 강제 발동 → HOLD 전량. 목표가 1,310K→1,700K 상향, Trailing Stop 912K 신설
- **LS ELECTRIC 케이스 진단**: LLM 4중 편향(Sharma+Laban+Huang+Li) 합성으로 4/17 조기매도 추천 → +47~84% 추가 상승 놓침 사례 학술 진단

### 🎯 MCP 도구 카운트: 39 → **46개**
- get_youtube_transcript (40, 4/24)
- get_us_buy_candidates (41, 4/25)
- get_us_earnings_transcript (42, 4/26)
- get_us_analyst_research (43, 4/26)
- get_polymarket (44, 4/27) — Polymarket 매크로/지정학 베팅
- get_macro_external (45, 4/27) — Polymarket + Treasury 통합
- get_pension_flow (46, 4/27) — 연기금 종목별 양방향 매매

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
| 2026-04-16 | 워치리스트 단일화 (watchalert.json) | 3파일 파편화 26종목 불일치 해결 |
| 2026-04-16 | KR_DEEPSEARCH.md 신설 (10 Step + PDF 게이트) | US_DEEPSEARCH와 대칭, Step 생략 방지 |
| 2026-04-17 | F/M/FCF 완전 가동 + DART 증분 자동화 | shares_out 24,310건. 우량 7+ 552종목(22%). 02:00 daily 스케줄. |
| 2026-04-18 | 뉴스 감성분석 KNU 사전+구문보강 (97%) | 단순 키워드 → 점수 기반+양보절 제외. 192케이스 66%→97%. |
| 2026-04-18 | US 애널 레이팅 MCP 3종 1+2단계 | StockAnalysis.com. 실시간 감시 ET 12:00/16:30. 13/13 테스트. |
| 2026-04-19 | 거버넌스/밸류업 전체 롤백 | 후행지표 판단. "간판만 비슷" 알파 없음. TODO 착수 전 선행/후행 판단 교훈. |
| 2026-04-21 | US 레이팅 오탐 근본 수정 (d1b2c1d) | `fetched_at` → `rating_date` 필터. 첫 수집 수개월치 오탐 방지. |
| 2026-04-23 | 주간 US 유니버스 수집 잡 (12cf948/975ef5d) | S&P 500 + Russell 1000 합집합 1,010종목 × 주 1회 일요일 03:00 KST. |
| 2026-04-23 | INVESTMENT_RULES v6 레짐 개정 (6e2c6f9) | 레짐 = 현금 관리 도구로 역할 재정의. 🟢 신규자제 조항 삭제. 현금 🟢 5~8%. "현금은 비용" 원칙. |
| 2026-04-23 | judge_regime v6 동기화 (a5cf996) | 4단계→3단계(🟢/🟡/🔴). 판정 지표 **S&P 200MA + VIX 2개만**. |
| 2026-04-23 | 치명 KST 스코프 버그 수정 (42f3a14) | 604d775 도입 버그. Python 로컬 스코프 교훈. |
| 2026-04-23 | 대시보드 인증 + 편집 기능 (2d0ae78) | Cloudflare Access Gmail PIN, TODO 토글 + 투자판단 입력. TODO_dev.md P1 완료. |
| 2026-04-23 | critic hotfix XSS 차단 (8f58f8c) | `_inline()` href XSS 차단(스킴 화이트리스트), 코드블록 검사. E2E 6/6 PASS. |
| 2026-04-23 | 관세청 수출 모듈 완전 롤백 | 2일 공수 구축 후 제거. 발굴 부적합, 동행/후행, 어거지 호출 유혹. 4/19 거버넌스 패턴. |
| 2026-04-24 | DART 수시공시 본문 조회 + 알림 요약 (f1969d5) | get_dart MCP 2종 추가. 캐시 + path traversal 차단. 단위 6/6 + 라이브 3/3 PASS. |
| 2026-04-24 | INVESTMENT_RULES v6 전면 개정 + 정합성 동기화 (ac1f049/7639ec3) | 확신등급 폐기 → 3-Gate + 비중 3단계. F-Score ≥8, 환각 수치 삭제. KR/US_DEEPSEARCH 동기화. |
| 2026-04-25 | US_EXIT.md v1 신설 (30.7KB) | 미국 매도 프레임. PEAD 대형주 소멸(Martineau 2022), Sell 4.8% 희소성, LTCG/STCG 22%p, LLM 4중 편향. 보유 5종목 Kill Switch. |
| 2026-04-26 | KR_EXIT.md v1 신설 (33.8KB, commit 20d781d) | 한국 매도 프레임. KCMI 2026 김준석 TP 소멸, Choe-Kho-Stulz 1999 외국인 destabilize 부정, 거래세 2025 0.15%/2026 0.20% 정정, 이승희 KDISS→KDAS 정정, Munger 한국 부적합. SK하이닉스 EXIT 1호 적용 → HOLD 전량 + 목표가 1700K + Trailing 912K. |
| 2026-05-05 | load_krx_db shadow 버그 + 운영 안정화 + silent 전수조사 (5c88061~c8b71c1, 9커밋) | krx_crawler.py L17 try-import 후 L511 def 재정의 → main.py 3곳이 4/7 JSON 보던 사고. _USE_SQLITE 가드로 fix. 동일 패턴 _load_history/scan_stocks 도 export alias 가드. **Silent 전수조사**: weekly_universe_update KIS 페이지네이션 헤더 (M→F 변경) 3주 stale + weekly_financial 60분 또 타임아웃 (per-ticker wait_for 추가). 부수: weekly_us_analyst_sync KeyError, weekly_sanity 휴장일, watch_change 임계값 강화, AMD watchalert 정리. legacy 정리: krx_db 1GB + stale 3 files 1.6MB. |

---

## 🧠 최근 세션 학습 (Lessons learned)

1. **API 응답 필드는 전수 검토할 것** — `whol_loan_rmnd_rate` 이미 Phase 1에 있었는데 모르고 Safari fetch 만듦.
2. **"죽은 코드" 판단 전 데이터 성숙도 체크** — short_squeeze는 코드 정상, 과거 데이터 0이라 일시적 빈 결과.
3. **사용자 지적 신뢰** — "KRX Safari 대체됐던 거 같은데" 기억이 정확. 재검증으로 2,357줄 청소.
4. **팀 구조 원칙 지키기** — Opus가 직접 구현 안 하고 Sonnet 에이전트에 위임.
5. **"맥미니 = 다른 서버" 편향 주의** — 워크트리가 본체임을 잊고 "배포 필요"라 오판.
6. **문서는 복붙 템플릿 + 킬 조건 없으면 Step 생략됨** — KR_DEEPSEARCH 초판은 설명문만 → 건너뜀. US 패턴 차용.
7. **리뷰 2중 체제의 가치** — code-reviewer + critic 병렬로 치명 6건 캐치.
8. **DART API 한도는 stockTotqySttus가 더 빡빡** — 4/16 status=020 한도초과 발견.
9. **DART CF 직접법 회사는 감가상각 노출 안 됨** — 삼성/SK하이닉스/현대차. M-Score DEPI 계산 불가.
10. **Python stdout 버퍼링 함정** — nohup -u 했는데도 line buffering 끊김. DB 카운트 polling이 더 신뢰.
11. **한 함수 버그가 여러 경로 파급** — `kis_investor_trend_history` 1곳 수정으로 둘 다 복구. grep 필수.
12. **KIS API 응답 스키마가 조용히 바뀜** — 공지 없이 변경. 주기적 스모크 테스트 필요.
13. **"수집 성공 but 0값" 함정** — NULL/0 구분 필수. `SUM(CASE WHEN col=0)` 모니터링 포함.
14. **에이전트 합의 ≠ 정답** — WS WRONG_VERSION_NUMBER 오진. 사용자 반문으로 재조사. 직접 테스트로 검증.
15. **collect_daily() 과거 backfill 불가** — 현재가 API라 date 무시. 일봉 API 별도 필요.
16. **0 vs NULL 구분 필수** — turnaround 211→113건 정상화. 사전 분포 검증.
17. **기존 헬퍼의 미사용 탐지** — `_get_session()` 63개 호출 미사용. grep 검증 우선.
18. **TODO에 있다고 구현 금지 — 알파 원천 먼저 판단** (4/19 거버넌스 롤백). 선행/후행 판단 + 가치 중복 체크.
19. **유튜버/블로거 인용 주장은 실증 없으면 믿지 마라** (4/23 관세청). 구현 전 피어슨 n≥18 통계 검증.
20. **"선행"과 "동행" 구분 필수** (4/23 실증). DRAM/HBM r=0.93 lag=0 동행. 선행 알파 X.
21. **Python 로컬 스코프 버그** (4/23 KST). 모듈 전역과 동일 이름 로컬 할당 금지.
22. **MCP 도구 존재 = 어거지 호출 유혹** (4/23 관세청). Slovic 1973: 정보 늘리면 확신만 2배.
23. **UI 렌더링 함수 = 잠재적 Stored XSS 벡터** (4/23 `_inline()`). 파일 쓰기 추가 시 렌더 경로 전수 조사.
24. **🆕 LLM 매도 판단 4중 편향 합성 효과** (4/26 KR_EXIT 학술 진단): LS ELECTRIC 4/17 조기매도 추천 = Sharma 2023(sycophancy) + Laban 2023(FlipFlop -17%p) + Huang 2023(intrinsic reflection) + Li 2025 FINSABER(bull-market 조기매도) **4중 편향 동시 작동** 표본. 결과 +47~84% 추가 상승 놓침. 교훈: **매도 판단 LLM 출력은 4중 편향 체크 후에만 채택**. 본 세션 SK하이닉스 판단 중간 FlipFlop 1회 발생, 사용자 지적으로 첫 판단 HOLD 복귀 — 학술 진단대로 발현. KR_EXIT/US_EXIT의 Section 1 LLM 10규칙은 4중 편향 차단 장치.
25. **🆕 Import shadow trap — try-block import 후 같은 모듈 def는 final namespace 점유** (5/5 load_krx_db 사고): `from X import f` (try-block) + `def f(...)` (모듈 본문) → 외부에서 `from this_module import f` 했을 때 두 번째 def가 받아짐. krx_crawler.py L17 SQLite import + L511 legacy JSON def → main.py 3곳이 한 달 stale 4/7 데이터 사용. 검출 신호: 사용자 "데이터 이상한 거 같다" 지적. 방어: ① re-export하는 모듈은 def를 `if not _<flag>:` 가드 안에 배치, ② 또는 모듈 끝에 export alias 강제 (`if _flag: from X import f as f`). 신규 외부 의존 함수 추가 시 같은 모듈에 동명 def 없는지 grep 필수.
26. **🆕 사용자 지적 즉시 신뢰 #2** (5/5): "데이터 이상한 거 같다" 한 마디로 한 달 stale 발견. 학습 #3 (KRX Safari 대체 기억) 재확인. 패턴: 직관적 이상 + 구체적 수치(SK하이닉스 -0.2%) → 즉시 검증 우선.
27. **🆕 Silent failure 가드 자체가 새 silent failure 만든다** (5/5 universe + financial): "60종목 < 100 → 기존 유지" 가드는 정상 작동했지만 **알림 없이 stale 3주 방치**. "타임아웃" 메시지는 떴지만 반복돼서 둔감화. 가드/타임아웃 추가 시 **N회 반복 시 텔레그램 알림** 또는 **stale 일수 표시** 같은 visible escalation 필요. 단순 print/silent skip은 "알림 인플레이션"으로 시그널 묻힘. 학습 #10 (stdout 버퍼링) 재현 — print 200건마다 + 버퍼링이면 사실상 invisible. → 진행 로그는 50건마다 + flush=True 고정. **5/8 첫 실증 (a35b691)**: pension_collect 5/6/5/7 평일 saved=0 침묵 → 헬퍼 3종 (`_track_silent_failure` / `_reset_silent_failure` / `_alert_silent_failure`) + 24h cooldown + `silent_failure_log.json`. 3회 연속 시 텔레그램 escalate 패턴 정립.

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
