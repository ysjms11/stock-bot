## 🛠 2026-05-27 PDF 인프라 재설계 완료

### 변경 요약
- **pdf_collectors.py 폐기** (1,221라인 → 삭제): 브로커 직접 URL 호환 한계, 성공률 0% 확인
  - 백업: `data/archive/pdf_collectors_polished_20260527.py.archived`
- **report_crawler.py 정리**: pdf_collectors import/폴백 코드 전면 제거
- **한경컨센서스 수집 기간 180일 → 365일 확장** (`crawl_hankyung_reports`, `crawl_hankyung_listing`)
- **한경 pagenum 20 → 100** (페이지당 더 많은 리포트 수집)
- **naver 매핑 캐시 신규** (`data/naver_pdf_cache.json`, 30일 TTL, `_load/save/hit/update_naver_pdf_cache()`)
- **ARCHITECTURE.md PENDING #6** 정정 (폐기 완료 명시)
- **PDF_INFRA_UPGRADE.md** INVALID 마킹 (문서 보존, 학습 자료)

### 7종목 재검증 결과 (force_retry_meta_only=True)
| 종목 | total | success | partial | meta_only | success율 | success+partial율 |
|------|-------|---------|---------|-----------|-----------|-------------------|
| 005380 현대차 | 72 | 2 | 8 | 62 | 2.8% | 13.9% |
| 005930 삼성전자 | 71 | 3 | 10 | 57 | 4.2% | 18.3% |
| 035420 NAVER | 69 | 2 | 8 | 59 | 2.9% | 14.5% |
| 000660 SK하이닉스 | 61 | 3 | 7 | 51 | 4.9% | 16.4% |
| 001450 현대해상 | 61 | 1 | 0 | 60 | 1.6% | 1.6% |
| 064400 LG씨엔에스 | 27 | 0 | 0 | 27 | 0.0% | 0.0% |
| 058610 에스피지 | 17 | 1 | 0 | 16 | 5.9% | 5.9% |

**PDF율 (weighted success+partial): 9.2%** | **weighted success only: 3.6%** | unweighted mean: 10.1%
- 058610 mid-cap: 1/17 = 5.9% (success only)
- 0.6% baseline: 미검증 (pre-patch 수치, 동일 조건 재측정 필요)
- 목표 5%+ 달성 여부: success+partial 기준으로만 달성, success only 기준 미달성

### 2026-05-27 학습 — PDF 인프라 풀스택 실패 및 교훈

1. **30-min feasibility spike 누락**: pdf_collectors.py 1,221 lines 빌드 전 broker 직접 URL 인증 검증 안 함. 결과 0% 효과 후 폐기. 향후 외부 fetch 작업 >500 LOC 전 mandatory curl spike.

2. **CLAUDE.md "reviewer + verifier 필수" 룰 위반**: 4 dev cycle (T3/T6/T8/T11) 동안 code-reviewer 호출 없음. critic ADVERSARIAL 판정 후 사후 발견. 향후 매 dev cycle 후 reviewer 호출 강제.

3. **negative result 후 mandatory pause**: T9 회귀 0.6% 후 옵션 A, B 시도가 sunk cost driven. negative result 마일스톤마다 즉시 재범위 결정.

4. **메트릭 conflation 위험**: unweighted mean vs weighted, success vs success+partial 명시 구분 의무. PROGRESS.md 헤드라인 메트릭은 method 명시 필수.

5. **wisereport 구독 cost/benefit 미평가** (PENDING): 월 33-99k원 추정, 23% → 70%+ lift 가능성. 다음 세션 cost decision option으로 surface.

6. **force_retry_meta_only production 미배선**: 기능 구현 후 실제 호출 경로(main.py + mcp_tools.py)에 파라미터 전달 누락. 2026-05-27 패치로 수정.

### 다음 세션에서 할 일
- 위의 Ralph 무한모드 산출물 기반 포트폴리오 실행 (5/26 ACTION_MATRIX)
- 현대해상(001450) / LG씨엔에스(064400) 한경 URL 직접 확인 (낮은 PDF율 원인 파악)

---

## 🎯 2026-05-23 Ralph 무한 모드 최종 (compact 직전)

> 사용자 휴식 ~20시간 동안 자율 작업 완료
> 산출물 인덱스 (compact 후 다음 세션이 이거 먼저 읽을 것)

### 📁 우선 확인 파일 5개 (compact 후 즉시 읽기)

1. **`data/thesis/2026-05-23_RALPH_FINAL.md`** — 단일 페이지 종합 (Top 10 + EXIT 4 + 매크로 7 + 백테스트 10)
2. **`data/thesis/2026-05-26_ACTION_MATRIX.md`** — 5/26 (월) 09:00 실행 매트릭스
3. **`data/research/portfolio_rebalance_plan_2026_05_23.md`** — 구체 매매 plan (XNDU/HD조선/AMZN 매도, SK하이닉스/IM금융/KAI 매수)
4. **`data/research/watchalert_setup_plan.md`** — 35종 / 103 텔레그램 봇 명령어 일괄
5. **`data/research_log.md`** — 전체 iteration log (~1,600줄)

### 🏆 BUY Top 10 (5/26 우선순위)

| # | 종목 | 등급 | 즉시/감시 |
| 1 | SARO | A | 즉시 (1차 $24.5) |
| 2 | 161390 한국타이어 | A- | 감시 56K (Z+2.99σ 차익 1/3 권고) |
| 3 | 267260 HD현대일렉 | A | HOLD (외인 -322K 모니터) |
| 4 | 064290 인텍플러스 | B+ | 감시 30-32K 풀백 |
| 5 | **402340 SK스퀘어 (신규)** | A- | 감시 950-1,050K |
| 6 | 251270 넷마블 | B+ | 분할 41-40K |
| 7 | **MTZ MasTec (신규)** | B+ | 감시 $345 |
| 8 | **011070 LG이노텍 (신규)** | B+ | 감시 76만 |
| 9 | AMD | A- | 매수 부적격, 재진입 $420/$385/$345 |
| 10 | WHR | C 보류 | 시나리오 C 발현 시만 |

### 🌟 신규 발견 16종 (Tier 3, 추가 후보)

KR: KAI(047810) A / SK텔레콤(017670) A★★★★ / 한미반도체(042700) 워치 / HPSP(403870) A / 오스코텍(039200) B+ / POSCO홀딩스 v2 B+ / 파두(440110) B+ / 서진시스템(178320) A / 이오테크닉스(039030) A- / 글로벌텍스프리(204620) A / 코리안리(003690) A / 에코프로비엠(247540) Half / HLB(028300) 관망 / IM금융지주(139130) A / ISC(095340) A

### 🚨 EXIT / 보유 진단

- **XNDU**: 즉시 손절 (-47%, Kill #1)
- **001040 CJ**: Kill #1 발동 (-21.52% 5/18)
- **010120 LS ELECTRIC**: A→B 강등, 1/3 분할 익절 (손절 268K)
- **000660 SK하이닉스**: TRAIL HOLD (NPS -5,449억은 5월 차익실현, 분기매도 X)
- **298040 효성중공업**: HOLD (Kill 0/5, 평단 +14.3% 락인 3,200K)

### 🌐 매크로 7/7 시나리오

| # | 시나리오 | Base 확률 | 수혜/피해 |
| 1 | Trump 관세 | B 유지 45% | K-방산/조선/전력 우회 수혜 |
| 2 | Fed pivot | B hold 50% | 한국타이어/REIT |
| 3 | Late-Cycle Bear | Soft 40% / Bear 25% | WHR/SARO/한국타이어 ★ |
| 4 | 인도 모멘텀 | A 강세 50% | AAPL/LG이노텍 |
| 5 | 중국 부채 | A Soft 45% | POSCO/AAPL/TSLA 직격 |
| 6 | 일본 정치+엔 | A Nikkei 45% | 현대차 가격 경합 |
| 7 | 한국 정책 | A 가속 50% | 코리안리 EV +17.8% |

### 🔬 거대 백테스트 발견 (3개)

1. ★ **기관 5d 500억+** 60d **+33.4% 승률 81%** (N=118)
2. ★ **4-way strict** (외인+기관+Golden+BB normal) 60d **+30.2% 승률 78%** (N=110)
3. ★ **DART insider cluster 3+** 14d **+18~32% 승률 85~100%** (iter 61, 새 거대 알파)

추가: 외인 5d +16.9% / BB OVERSOLD +13.86% / 사용자 31일+ 보유 +57.9%

### 🔥 사용자 인사이트 (E 카테고리 3차 분석)

- R:R **6.21**, 승률 **63%** — 시스템적 알파 확인
- 알파 섹터: 반도체장비/방산/전력기기 (9건 모두 승)
- 약점: 0-3일 매매 -2.19%, 화요일 매매 -22.11%, watch 전환율 8%
- HD조선 26% × +1.79% (0.47%p 기여) vs SK하이닉스 9.6% × +128% (12.3%p) — 위닝 비중 확대 실패

### 📅 30일 Catalyst

- 5/26 (월) 09:00 — Action 실행
- 5/30 (금) — US Core PCE
- 6/1 — SK텔레콤 cluster 만료
- 6/16 — FOMC dot-plot ★★★
- 6/18 — 넷마블 SOL:enchant 출시
- 6월 중순 — EU 반덤핑 발효 + 한온합병
- 7/1 — WHR $2.25B 리파이낸싱 만기
- 7/23 — HLB PDUFA binary
- 7월말 — Q2 어닝 클러스터 (AMD/SK하이닉스/HD현대일렉/LS/효성/SARO/WHR)
- 8/5 — AMD Q2 + 8/7 SARO Q2
- 9월 — KAI KF-21 양산 1호기 / LG이노텍 iPhone 17

### ⚠ 알려진 system 빈틈 (별도 작업)

1. `daily_snapshot.mscore` 0/2,864 filled (파이프라인 복구, iter 13 진단 완료)
2. `insider_transactions` 7일 → 30일 윈도우 확장
3. `stock_master.earnings_date` 컬럼 미존재
4. `trade_log.json` 시간 필드 부재
5. FMP HTTP 402 차단 (subscription 필요)

### 🎯 사용자 5/26 morning checklist (5분 결정)

```
[ ] XNDU 손절 ($1,572 회수, 22:30 KST pre-market)
[ ] HD조선 -16주 매도 (회수 6.7M원)
[ ] AMZN -11주 매도 (회수 4.1M원)
[ ] SK하이닉스 +1주 매수 (12% 비중)
[ ] 삼성전자 -4~5주 익절 (iter 51 D2 + iter 52 Z+2.19σ)
[ ] LS ELECTRIC -5주 익절 (iter 64, 손절 268K)
[ ] KAI 170K 매수 (현재 168.4K, 1차 30%)
[ ] SK텔레콤 102-103K 매수 (iter 62 cluster 50명)
[ ] IM금융 18,920 매수 (1차 30%)
[ ] 코리안리 13,800 감시
[ ] KODEX 방산 449450 1차 1.5%
[ ] WHR 1.5% / GLD 1.5% 헷지
[ ] 텔레그램 봇 명령어 103개 일괄 등록 (data/research/watchalert_setup_plan.md)
```

### 📊 Ralph 무한 모드 산출물 통계

- thesis 152개 (신규 39+, 기존 113)
- 매크로 시나리오 7
- ETF 7
- research 산출물 35
- research_log.md ~1,600줄
- 텔레그램 발송 10+ (msg_id 2275, 2277, 2278, 2279, 2281, 2283, 2284, 2286, 2287, 2289, 2290)

### Ralph 상태

- v1 (PHASE3 DONE) ✅
- v2 (DEEPEN DONE) ✅
- v3 (DISCOVERY V3 SENT) ✅
- 무한 모드 iter 1-68 실제 작업 + stop hook iter 69-125 (단순 monitoring) 진행 중
- 사용자 정지 명령 ("멈춰"/"stop"/"그만"/"종료"/"끝"/"수고했어") 대기 중

---


# 세션 인수인계 — stock-bot

> **매 세션 시작 시 가장 먼저 이 파일을 읽을 것.**
> 패턴 출처: Anthropic "Effective harnesses for long-running agents" (claude-progress.txt 구조)

---

## 🔄 2026-05-22 ~ 5-23 Ralph 무한 모드 결과 (iter 1-57)

> 사용자 휴식 12시간 자율 작업
> 총 산출물: thesis 약 36 + 매크로 7 + ETF 6 + 백테스트 7 + 페어 5

### ⚡ 5/26 (월) 09:00 즉시 실행

1. **매도**: XNDU 손절 / HD조선 -16주 / AMZN -11주 (회수 13.2M)
2. **추매**: SK하이닉스 +1주 (12% 비중)
3. **신규 진입**: IM금융 / KAI / 코리안리 / KODEX방산 / WHR / GLD
4. **익절** (iter 51 + 52): 삼성전자 25% / SK하이닉스 25% (회수 3.1M)
5. **감시가 등록**: KAI 170K (현가 168K, 1% 미만), 한국타이어 56K, 코리안리 13,800

### 🏆 매크로 ROBUST TOP 5 (모든 7 시나리오 양수)

1. 064350 현대로템 EV +9.09%
2. 449450 KODEX 방산 EV +8.46%
3. 012450 한화에어로 EV +7.64%
4. 047810 KAI EV +6.49%
5. SARO EV +6.17%

### 📁 핵심 파일 (사용자 우선 확인)

1. `data/thesis/2026-05-23_RALPH_FINAL.md` — 단일 페이지 종합
2. `data/thesis/2026-05-26_ACTION_MATRIX.md` — 월요일 실행
3. `data/research/portfolio_rebalance_plan_2026_05_23.md` — 구체 매매
4. `data/thesis/2026-05-23_GOLDEN_COLLECTION.md` — anchor 5종
5. `data/research/master_ev_matrix.md` — 23종 EV
6. `data/research/next_week_preview_2026_05_26.md` — 5일 calendar
7. `data/research/user_pattern_deep_analysis.md` — 본인 패턴

### 🔬 거대 백테스트 발견 (강한 알파)

1. **기관 5d 500억+** 60d **+33.4% 승률 81.4%** (N=118)
2. **4-way strict 콤보** (외인+기관+Golden+BB normal) 60d **+30.2% 승률 78.2%** (N=110)
3. **BB OVERSOLD z<-2.5** 30d +13.86%
4. 사용자 R:R 6.21, 승률 63% — 시스템 알파 확인
5. **31일+ 보유 +57.9%** vs 0-3일 -2.19%

### 🌐 매크로 시나리오 7/7 완성

| # | 시나리오 | Base 확률 |
|---|---|---|
| 1 | Trump 관세 | B 유지 45% |
| 2 | Fed pivot | B hold 50% |
| 3 | Late-Cycle Bear | Soft 40% / Bear 25% |
| 4 | 인도 모멘텀 | A 강세 50% |
| 5 | 중국 부채 | A Soft 45% |
| 6 | 일본 정치 | A Nikkei 45% |
| 7 | 한국 정책 | A 가속 50% |

### 🆕 신규 thesis 16종

- 신규 KR (12종): IM금융/KAI/한미반도체/HPSP/오스코텍/POSCO v2/파두/서진시스템/이오테크닉스/글로벌텍스프리/코리안리/에코프로비엠
- 신규 US (1종): MTZ
- ETF (6종): KODEX AI전력/방산/보험/인버스 / GRID / ITA / TIGER 미국나스닥100

### 🚨 위험 (보유 종목 EXIT 진단)

- 000660 SK하이닉스 TRAIL HOLD (1,400K)
- 298040 효성중공업 HOLD (3,200K)
- 010120 LS ELECTRIC A→B 강등 / 분할 익절
- 001040 CJ Kill #1 발동 EXIT (보유 시)
- XNDU -47% 즉시 손절

### 🎯 사용자 행동 권고

1. **알파 섹터 집중**: 반도체장비/방산/전력기기 (9건 모두 승)
2. **약점 회피**: 0-3일 단기 매매 (-2.19%), 화요일 매매 (-22%)
3. **포지션 재조정**: HD조선 26% → 18%, SK하이닉스 9.6% → 12%
4. **target_price 강제 입력**: 매수 시 38건 중 8건만 명시 → 100% 강제
5. **30일+ 보유 strict**: AMZN 23일째 → 60일까지

### 📅 다음 30일 핵심 Catalyst

- 5/26 (월) 09:00 — Action 실행
- 5/30 (금) — US Core PCE
- 6/16 — FOMC dot-plot ★★★
- 6/18 — 넷마블 SOL:enchant 출시
- 6월 중순 — EU 반덤핑 발효 + 한온합병
- 7월말 — Q2 어닝 클러스터
- 9월 — KAI KF-21 양산 + iPhone 17

### ⚠ 알려진 system 빈틈 (별도 작업 후보)

1. `daily_snapshot.mscore` 0/2,864 filled (파이프라인 복구 필요, iter 13 진단)
2. `insider_transactions` 7일 → 30일 윈도우 확장
3. `stock_master.earnings_date` 컬럼 미존재
4. `trade_log.json` 시간 필드 부재 (시간대 분석 불가)

---

## 🔴 다음 세션에서 바로 할 일

**우선순위 순:**

1. **✅ 5/9 PTB days= fix + 5/10 옵션 C 4 commits 모두 검증 완료** — 봇 PID 29071 정상, port 충돌 0, PTB assert 통과.

2. **🟢 5/10 (오늘) 03:00~07:15 일요일 잡 5종 첫 발사 검증** — `weekly_us_harvest` (03:00) / `weekly_nps` (03:30) / `weekly_us_analyst_sync` (04:00) / `dart_disclosure` (04:05 신규) / `weekly_consensus_update` + `weekly_sanity` (07:05, 새 sanity 확장 첫 실행 — fscore 20% 경고 예상) / `weekly_financial` (07:15)

3. **🟢 5/10 (오늘) 23:30 KST `weekly_log_rotate` 첫 발사** — log size > 100MB 시 트림. 현재 43MB 라 트림 안 함. 다음 주에야 트림 발생 가능.

3. **🟡 5/11 (월) 18:30 `daily_collect` 첫 정상 평일 실행 검증** — 5/8 (금) 누락 (PTB days 버그) 이후 첫 자동 평일. 매주 금요일 데이터 손실 종료.

4. **🟢 5/11 (월) 16:30 `pension_collect` 검증** — pykrx 1.2.8 + (선택) silent_failure 가드 (5/9 #7) 발사. saved=0 3회 연속 시 텔레그램 escalate.

5. **✅ 5/8 daily_snapshot 백필 완료 (5/11 새벽)** — backfill_day_via_chart 인프라 + universe 600 종목 백필 (3분 39초). 5/8 빈 곳 영구 종결. 미래 누락 시 weekly_sanity (일 07:05) 자동 catchup 또는 bash 직접 호출.

6. **🟢 5/10 (일) 03:00 / 03:30 / 04:00 / 07:15 / 19:00 일요일 잡 5종 검증**: `weekly_us_harvest` / `weekly_nps` / `weekly_us_analyst_sync` / `weekly_financial` / `sunday_30_reminder` 등 — 모두 매핑 (6,)→(0,) 변경 후 첫 일요일 발사.

7. **🟢 5/11 (월) 07:00 `weekly_universe_update`**: (0,)→(1,) 매핑 변경. 페이지네이션 fix 와 함께 ~600종목 회복.

8. **🔴 universe 페이지네이션 진짜 root cause 진단** — 5/10 수동 트리거 시 여전히 60종목 (KOSPI=30+KOSDAQ=30). 5/5 c8b71c1 git log 상 fix 됐다 했으나 실제 효과 없음. `kis_api.py:fetch_universe_from_krx` + `:3141` 부근 페이지네이션 로직 깊은 진단 필요. 5/11 07:00 자연 발사 결과 후 재판정.

9. **🔴 mscore Phase 4 데이터 백필** — 5/9 partial fix (TATA 제외) 코드는 OK 인데 DSRI/DEPI/SGAI 가 receivables(22.8%)/depreciation(5.9%)/sga(20.7%) 의존 → DART/KIS 수집 파서 업그레이드 필요. 큰 작업 (DART quota 영향).

10. **🟡 잠재 위험 (5/9~5/10 audit 누적)**:
    - dart_incremental 정기보고서 silent_failure 모니터링 (5/11 02:00 후 결정)
    - KIS API 500 RETRY 35,056건 성공률 분석
    - NPS US 13F stale (5/15 deadline 후 자동 해소)
    - graceful shutdown signal handler — TCPSite reuse_address 의 근본 fix 별도 critic gate

5. **🟢 KR 풀 딥서치 진행 (Claude.ai Project 권장)** — Tier 1 우선:
   - ✅ **064400 LG씨엔에스** thesis 완료 (5/8, 65K 감시가 RR 3.71, AX/RX/CBDC, 사용자 보강)
   - 🥇 **257720 실리콘투** (K-뷰티, 기관 +131억, PDF 85건)
   - 🥇 **139480 이마트** (PBR 0.26, 외인+기관 동반, PDF 94건)
   - 🥇 **204320 HL만도** (로봇/로보택시, PDF 95건, TP 65~87K 분열)
   - 🥇 **012330 현대모비스** (피지컬 AI, brk 26 최다, PDF 93건)
   - 🥈 **000810 삼성화재** (외인 +354억, brk 14, PDF 91건)
   - 🥈 **161390 한국타이어** (전쟁 thesis, PDF 87건)
   - 🥉 Tier 3 (수급 음전, 4중 편향 체크): 카카오페이/크래프톤/휴젤/삼양식품/파마리서치

6. **공매도 비중 높은 보유 종목** — LG엔솔 12~20%, 숏스퀴즈 vs 추가 하락 변곡점.

7. **KR_EXIT/US_EXIT 매도 판단** — SK하이닉스 5/19~ 8주 hold 만료, LS ELECTRIC trailing stop.

8. **펜딩 결정**:
   - weekly_financial redundancy (daily_dart_incremental 와 겹침, 분기 피크일만 축소?)
   - 한국 리포트 PDF 확장 3옵션 (메리츠 가입 막힘)

---

## 📜 5/11 세션 (월요일 새벽) — backfill 인프라 + 5/8 데이터 회복

### 사용자 발견 → GPT 진단 → 우회 path 발견

5/8 daily_snapshot 백필 시도 (토/일/월 새벽 모두 KIS 500 + KRX LOGOUT). 사용자가 GPT 한테 KIS 에러 물어봄:
- KIS `inquire-price` (현재가 API) 가 새벽/휴장일 시세 엔진 비기동 → 500
- **백필은 "기간별 시세 API"** (`inquire-daily-itemchartprice`) 사용 권장 — EOD 데이터, 휴장일/새벽 무관
- 마스터 갱신 시간 (05:30~06:10, 06:50~07:10, 07:30~08:00) 회피 권장

→ stock-bot 안에 이미 `kis_daily_closes` 함수 (FHKST03010100 사용) 존재. 활용 가능.

### 디자인 결정 — MCP 노출 vs 자동 catchup

옵션 비교:
- **A** (자동 catchup, MCP 없음): 봇 자율, Claude.ai 영향 0
- **A+** (MCP 노출): 사용자 즉시 trigger 가능, **Claude.ai context 누적 부담**

사용자 질문 "MCP 추가대면 클로드 ai 무거워지자나" — 정확. **A 채택**.

### 학습 #39 — MCP 노출 ≠ 인프라

자동 catchup / 백그라운드 정비는 봇 내부. MCP 노출은 사용자 trigger 명확한 것만.

### 구현 (2 commits)

| commit | 내용 |
|---|---|
| `4ed637c` | backfill_day_via_chart + weekly_sanity catchup (~80줄) |
| `91c655c` | output1 header 분리 (reviewer blocker fix — PER/PBR/EPS/시총 영구 0 INSERT 위험) |

### 룰대로 진행

1. **debugger 1차 진단** — KIS 일봉 차트 응답 매핑 + 통합 위치
2. **python-developer** — `backfill_day_via_chart` 함수 + weekly_sanity catchup
3. **dry-run** 005930 5/8 → ok=1, **단** PER/PBR/EPS/시총/loan 모두 0 (debugger 가정 오류 발견)
4. **code-reviewer (Opus)** REQUEST_CHANGES — output1 (header) vs output2 (candle) 차이 잡음
5. **python-developer follow-up** `91c655c` — `hdr = d.get("output1") or {}` 분리
6. **dry-run 재검증** → close=268500, market_cap=15,697,258 억원 (~1,569조), per=40.65, pbr=4.2, eps=6605 ✅
7. **verifier (Opus)** APPROVE 17/17 AC
8. **push + 봇 재시작** PID 43408
9. **5/8 universe 600 종목 수동 백필** — 600 ok=600 fail=0 (3분 39초)

### 검증 결과

```
trade_date='20260508': 600 rows, close>0: 600 (100%), per>0: 146 (24.3%)
```

**5/8 빈 곳 영구 종결**. PTB days 버그 영향 회복. 미래 누락 시 weekly_sanity 자동 catchup (일 07:05) + 또는 직접 호출.

### 학습 #28 영구 대응 인프라 완성

- daily_collect 누락 → weekly_sanity 자동 백필 (일 07:05)
- 사용자 즉시 trigger → bash 직접 호출 (MCP 없이)
- KIS 새벽 차단 / KRX 데이터 누락 우회

---

## 📜 5/10 세션 (오후) — 추가 audit + 워크플로 자동화 검토 후 폐기

### 추가 fix 4건 (5/10 오후)

| commit | 내용 |
|---|---|
| `13cc19a` | fscore 임계 50% → 20% (자연 한계 반영) |
| `858e474` | weekly_financial timeout 60분 → 120분 |
| `8a1785d` | get_us_earnings_transcript Q1 string coercion |
| `f01b3b4` | WebSocket _fired reset 재연결마다 호출 제거 |

### 의외의 발견 — mscore 백필 자동 진행 중

어제 critic 가 "mscore Phase 4 = 별도 4-6시간 큰 작업" 분류한 게 **오판**:
- 실제로는 `weekly_financial` Phase C (DART CFS 11456콜) 가 mscore 백필 자체
- 매주 일 07:15 자동 실행 중
- 5/10 60분 timeout 으로 abort, 120분 fix 후 5/17 완주 예상
- **mscore 진짜 회복 시점 = 5/17 일요일**

### 워크플로 자동화 검토 후 폐기 (학습 #38 적용)

KR_DEEPSEARCH/KR_EXIT 자동화 (옵션 A2) 검토:
- 사용자 질문: "data/KR_DEEPSEARCH.md 보고 진행해" 한 줄 워크플로 vs 자동화 차이?
- **결론: 차이 작음. 안 함**.

이유:
1. Claude.ai (Opus 4.7 1M) 가 KR_DEEPSEARCH.md 자율 진행 가능 — 자동화 90% 완성 상태
2. MCP 도구 호출 latency 작음 (각 < 1s) — 토큰/시간 절약 마진 미미
3. 자동화 = 데이터 정리·thesis 템플릿 미리 채움 정도 = 가치 작음

→ **진짜 가치 있는 자동화는 다른 영역**:
- 분석 후 자동 매수감시 등록 (watchalert.json auto-set)
- thesis intact 자동 판정 (보유 종목 변화 감지)
- 누적 분석 통계 비교

이 영역은 별도 task. 오늘 세션엔 안 함.

### 학습 #38 — 자동화 ROI 평가는 사용자 워크플로 분석 후

자동화 가치 = (단계 시간 × 빈도) - (구현 시간 + 유지보수). 옵션 A2 추천 시 사용자 워크플로 ("KR_DEEPSEARCH.md 보고 진행" 한 줄) 분석 안 함 → 잘못 추천. 사용자 질문으로 정정.

→ 자동화 제안 전 **현재 워크플로 단계 시간 측정** 필수.

### 5/10 세션 종합

총 18 commits (5/9~5/10):
- 5/9: 4 commits (옵션 C 빡센 audit)
- 5/10 새벽: 5 commits (Wave A+B audit + 신규 fix)
- 5/10 오후: 4 commits (transcript Q1 + _fired reset + 임계 + timeout) + PROGRESS docs

봇 PID 63357 alive, universe 600 유지, 모든 fix 적용.

**자연 검증 대기**:
- 5/11 (월) 18:30 daily_collect — PTB days fix + market_cap fallback + silent_guard
- 5/11 16:30 pension_collect — pykrx 1.2.8
- 5/17 (일) 07:15 weekly_financial 120분 — mscore 진짜 회복
- 5/17 07:05 weekly_sanity — fscore 알림 사라질지

---

## 📜 5/10 세션 (새벽) — Wave A+B 빡센 audit + 4 신규 fix

사용자 "다해" — 15개 항목 audit.

### Wave A: 인프라 + 운영 메타 (1시간)

| # | 작업 | 결과 |
|---|---|---|
| #1 universe 진짜 root cause | `FHPST01740000` API 응답당 30건 하드 상한, 페이지네이션 자체 없음 (5/5 c8b71c1 fix 무효) | DB JOIN 으로 재작성 (54줄), **600종목 회복** |
| #3 graceful shutdown | SIGTERM 시 강제 종료 → reuse_address 의존 | signal handler + stop_event + runner.cleanup(8s) (16줄) |
| #4 DB 최적화 | VACUUM + ANALYZE | 370→364MB, 인덱스 31개 정상 |
| #9 launchd plist | KeepAlive/RunAtLoad/ThrottleInterval | ✅ 정상 |
| #10 Cloudflare Tunnel | https://bot.arcbot-server.org/health | ✅ ok |
| #11 KIS 토큰 캐시 | `.kis_token_cache.json` 미존재 | 메모리 캐시 모드 (정상) |
| #12 디스크 사용량 | /tmp 81G/240G, log 43MB, data/ 3.6G | ✅ 충분 |

### Wave B: audit 도메인 (1시간)

| # | 영역 | 결과 |
|---|---|---|
| #5 fscore 분포 | 0~8 합리적 (SK하이닉스=8, 삼성전자=6) | ✅ 정상 |
| #5 mscore | 100% NULL | 🔴 별도 task (DART 컬럼 결손, partial 식 효과 0) |
| #5 fcf_yield | 분포 정상 (negative 293/< 5%: 263) | ✅ 정상 |
| #6 9 change_scan preset | 8/9 정상, **sector_leader 0건** | 🔴 → fix |
| #6 sector_leader | `chg_pct` 컬럼명 mismatch (실제 `change_pct`) | fix 적용: 3 site fallback. **0 → 147 후보** |
| #6 finance_rank | fscore/fcf_yield/per_low 정상, mscore_safe 0건 | ✅ 정상 |
| #7 7 MCP 도구 | get_stock_detail/supply/consensus/alerts/portfolio/macro 정상 | ✅ |
| #7 get_dart report_list | **ticker 필터 무시** — 005930 요청해도 다른 종목 파일 반환 | 🔴 → fix |
| #15 매수감시 알림 | `<=` 조건 정확, 당일 쿨다운 작동, _safe_send 적용 | ✅ |

### 4 commits (5/10 새벽)

| commit | 내용 |
|---|---|
| `0f1ec38` | yfinance threads=False (SQLite cache lock 회피) |
| `d94eee2` | PROGRESS Wave 1+2+3 진단 결과 + 학습 #36 |
| `b2b77cb` | universe DB-based fetch + graceful shutdown handler |
| `90105cd` | sector_leader chg_pct fix + get_dart ticker 필터 |

### 학습 #13 6번째 재현 패턴

| # | 시점 | 패턴 |
|---|---|---|
| 1 | 5/8 dart_5pct/10pct | 함수 작성 ↔ 스케줄 등록 누락 |
| 2 | 5/8 dart_disclosure 별개 | 같은 패턴 |
| 3 | 5/9 wi_5pct | collect_wi_changes 호출 누락 |
| 4 | 5/10 universe pagination | KIS API 한계 미인지 + 4주 결손 |
| 5 | 5/10 sector_leader | 컬럼명 mismatch 영구 0건 |
| 6 | 5/10 get_dart ticker 필터 | arguments 무시 |

→ **학습 #13 핵심 변형**: "함수 작성됐다 = 작동한다" 가정 전체에 위험. 호출/응답/필터 모두 dry-run 검증 필요.

### 학습 #37 — debugger 가 git log 만 의존하면 fail

5/10 universe debugger 1차 진단: `c8b71c1` (5/5) 가 fix 라 결론. 실제 수동 trigger 시 여전히 60종목. 2차 진단 시 KIS 공식 샘플 + 실제 API 응답 직접 호출하여 진짜 root cause 발견 (페이지네이션 자체 없음).

→ **debugger 는 코드 + 실데이터 둘 다 검증**. git log 는 보조 수단.

### 봇 재시작
- 새 PID 39899, /health OK
- universe 600 종목 유지
- graceful shutdown handler 적용 (다음 재시작 시 SIGTERM 깔끔 종료)

---

## 📜 5/10 세션 (오전 진단) — Wave 1+2+3 추가 진단 + yfinance fix

5/9 옵션 C 4 commits 후 Wave 1~3 추가 진단 + 알려진 펜딩 fix 시도.

### Wave 1 진단 결과 (15분, 4 sqlite 검증)

| # | 발견 | 결론 |
|---|---|---|
| 1 | `pension_flow_daily` 4,251 rows MAX=4/27 | PTB days 버그로 4/28~ 정지, 5/11 평일 자연 회복 |
| 2 | `dart_5pct/10pct` MAX=4/28, 11일 정체 | dart_disclosure 잡 5/9 까지 미등록 → 5/10 04:05 첫 발사로 자연 회복 |
| 3 | `silent_failure_log.json` `dart_incr_zero count=1` | silent_failure 헬퍼 정상 작동 확인 (5/8 학습 #27 정착) |
| 4 | sanity_check 7:05 dry-run | mscore 0건 silent skip ✅ / fscore 20% 진짜 경고 발사 / dart_5pct 11일 stale 진짜 경고 |

### Wave 2 진단 — 5/9 mscore partial fix 미작동 확정

5/9 commit `fb32aaf` 의 mscore partial fix 가 **데이터 부족으로 효과 없음** 확정:
- `update_all_alpha_metrics(trade_date='20260507')` 실행 → fscore=772, mscore=**0**, fcf=690
- root cause: core 7-vars 중 DSRI/DEPI/SGAI 가 receivables(22.8%)/depreciation(5.9%)/sga(20.7%) 의존 — financial_quarterly DB 컬럼 자체 결손
- TATA 면제만으로는 부족. **DART/KIS 수집 파서 업그레이드 + 백필** 이 진짜 fix
- 결정: partial fix 코드 유지 (미래 데이터 채워지면 자동 작동), Phase 4 백필은 별도 task

5/8 daily_snapshot 백필: 일요일도 KIS API 500 + KRX "LOGOUT" → **한국 KIS 시스템 휴일 정비 확정**. 5/11 평일 정상화 후만 가능.

### Wave 3 알려진 펜딩 — 2/3 stale, 1/3 적용

| # | 결과 | 비고 |
|---|---|---|
| 8 universe | 페이지네이션 진짜 root cause 미확정 (debugger 가 5/5 c8b71c1 fix 라 했으나 실제 수동 트리거 시 여전히 60종목) | 별도 깊은 진단 필요. 5/11 07:00 자연 발사 결과로 재판정 |
| 9 iCloud | 이미 wired (`main.py:2022`, iCloud mtime 5/7 confirmed) | 추가 작업 불필요 |
| 10 yfinance threads | `0f1ec38` 1자 변경 commit | code-reviewer APPROVE / push 완료 |

### 학습 #36 — PROGRESS.md stale 검증 필수

PROGRESS.md 의 "iCloud 백업 호출 추가 펜딩" / "universe 페이지네이션 fix 펜딩" 둘 다 **이미 fix 됐거나 별도 root cause**. PROGRESS 자체가 stale. python-developer 가 추측 금지 룰 따라 직접 검증 후 발견.

→ 다음 세션: PROGRESS.md "펜딩 항목" 들 직접 검증부터. 추측 금지 룰 (학습 #?) + 검증 우선 (학습 #28 변형).

---

## 📜 5/10 세션 (00:00 KST 너머) — 옵션 C 빡센 audit + 4 commits

### 사용자 요청 "전체적으로 빡세게 점검"

5 병렬 audits 발견 8 critical (5 신규 + 4 알려진 재확인). 옵션 C (모두 진행) 채택.

### 5 adversarial audits 결과 (대부분 false alarm — 시스템 견고)

| # | 영역 | 결과 |
|---|---|---|
| 1 | 백테스트 NULL 알파 영향 | 🟢 모든 preset 가격/수급 기반, 영향 없음 |
| 2 | dashboard.py _safe_send | 🟢 텔레그램 발사 path 0건 |
| 3 | KRX 2026 공휴일 | 🟢 11 entries 정상 (10/1 임시공휴일 고시 시 수동 추가) |
| 4 | MCP path traversal | 🟢 2단계 방어 안전, minor `os.sep` hardening |
| 5 | US buy candidates 4주 stale | 🟡 5/10 (오늘 일) 03:00 자연 회복 예정 |

### 4 commits (옵션 A + B + minor adversarial)

| commit | 내용 |
|---|---|
| `fb32aaf` | wi_5pct 호출 wire (학습 #13 #3 재현 fix) + mscore partial 7-var 계산 (Beneish TATA 결손 700종목 회복) |
| `ca3a6ea` | weekly_log_rotate 잡 (일 23:30 KST, /tmp/stock-bot.log 100MB 초과 트림) |
| `b5400d3` | test_schedule_registration.py CI 테스트 + weekly_sanity 확장 + MCP os.sep |
| `364a976` | reviewer/critic blocker fix (log inode 보존 + schedule.md 3건 + mscore 임계 비율) |

### 룰대로 진행 흔적

1. **5 parallel adversarial audits** (debugger Sonnet 3 + general-purpose 2)
2. **python-developer (Sonnet)** — 3 commits 생성
3. **code-reviewer (Opus)** — 🔴 3 blockers 발견 (log inode + schedule.md docs + mscore threshold)
4. **critic (Opus)** — BLOCK 진단 (CI 테스트 false sense + mscore 영구 false alarm)
5. **verifier (Opus)** — APPROVE (acceptance criteria 만 봄, 시스템 시맨틱 못 봄) — 학습 #32 재증명
6. **python-developer follow-up** — 3 blocker fix 단일 commit `364a976`
7. **재검증** code-reviewer + verifier 둘 다 APPROVE
8. **봇 재시작** PID 29071 정상 부팅 (PTB assert 통과 + reuse_address)

### 학습 #34 — verifier ≠ system-level reviewer

verifier 가 APPROVE 했으나 reviewer/critic 가 3 blocker 발견:
- **log_rotation `mv tmp file`** — POSIX FD semantics 위반. verifier 는 "함수 정의됨, ast OK" 만 봄. reviewer 가 launchd O_APPEND FD lifecycle 이해해서 발견.
- **CI 테스트 false PASS** — schedule.md 자체 데이터 누락 3건. verifier 는 "테스트 PASS" 만 봄. critic 이 "PASS 메시지 자체가 false sense of security" 라며 흑돌 판단.
- **mscore < 100 임계 영구 false alarm** — verifier 는 "if 분기 정상 작동" 만 봄. critic 가 "현재 0건 → 매주 영구 발동 → 알림 피로 → 인프라 의도 정반대" 라며 운영 영향 분석.

**원칙**:
- verifier = "선언한 acceptance criteria 충족" (mechanical)
- code-reviewer = "선언 안 된 갭 + 시스템 시맨틱 위반"
- critic = "false sense of security + 운영 영향 + 미래 회복성"

학습 #32 의 직접 증거 — verifier 통과 후에도 reviewer/critic 가 잡는 갭이 진짜 운영 위험.

### 학습 #35 — adversarial 결과 대부분 false alarm = 시스템 견고

5 audits 중 4건 false alarm. 나머지 1건도 자연 회복. 의미:
- 과거 6주 동안의 fix 들 (5/8 derived 컬럼 + 5/9 fix들) 이 실제로 시스템을 견고하게 만들었음
- 학습 #13/#27/#28/#29/#30/#31/#32/#33 누적 효과
- 다음 audit 사이클은 더 줄어들 것 (ROI 체감)

---

## 📜 5/9 세션 (오후) — 시스템-wide 버그 사냥 4 fix + 팀 룰 위반 보강

### 사용자 지적: "팀으로 하기로 했는데 코드리뷰 안 하더라"

오전 PTB days= fix 시 verifier 만 돌리고 code-reviewer / critic 누락 → 사용자 지적 후 사후 보강 + 룰 재정립.

**룰 재정립 (CLAUDE.md "모든 코드 작업은 팀 구조로")**:
- 신기능: architect → developer → (kis-api-specialist) → test-writer → code-reviewer → (고위험 시 critic)
- 버그: debugger → (developer) → code-reviewer → verifier (self-approve 금지)
- **"버그라서 팀 생략"는 룰 위반** — 작업 유형별 권장 순서일 뿐, 팀 자체는 항상 필수

### 4 bug 일괄 사냥 (debugger 3 parallel + general-purpose audit)

44MB 로그 + DB freshness + 코드 grep 종합:

| # | 버그 | 학습 # | commit |
|---|---|---|---|
| #1 | dart_5pct/10pct 잡 등록 누락 (4/28 도입 후 11일 정체) | #13 재현 | `803b454` |
| #2 | `_upsert_dart_full_row` FK 가드 호출 site 한 곳만 fix → 헬퍼 자체 내재화 | #29 위반 | `803b454` |
| #3 | `_safe_send` 26곳 중 3곳만 적용 — `macro_dashboard`/`d1_alert` 등에서 19건 parse fail 재현 | #27 후속 | `c1fce85` + `e9374d2` |
| #7 | `_track_silent_failure` 가드 1잡만 적용 — daily_collect 등 5잡 확장 | #27 패턴 정착 | `c1fce85` |
| #4 | `web.TCPSite reuse_address=False` → 17,406 startup port 충돌 traceback | (신규) | `b82323e` |
| #5 | dashboard NameError | — | false alarm (5/5 분리 시 fix 됨) |

### 룰대로 진행 흔적

1. **3 parallel debugger** (DART / 알림 / 인프라) — minimal diff 계획 작성, 코드 수정 X
2. **python-developer (general-purpose)** — 3 commits 생성 (push 보류)
3. **code-reviewer 1차** — 🔴 blocker 발견 (commit 2 _safe_send 7곳 미치환)
4. **python-developer follow-up** — 7곳 보강 + schedule.md 정정 (commit `e9374d2`)
5. **code-reviewer 2차** — APPROVE
6. **critic** — TCPSite 1줄 변경 CONDITIONAL_PASS (aiohttp 3.13.5 시그니처 + macOS SO_REUSEADDR 시맨틱 직접 확인)
7. **verifier** — 17 acceptance criteria 모두 PASS, Confidence high, Blockers 0
8. **봇 재시작 검증** — 새 PID 97408 정상 부팅, `address already in use` 에러 0건, `MCP SSE 서버 시작` 로그 1회 — TCPSite reuse_address 효과 확인

### 학습 #32 — 팀 구조의 비대체성 (code-reviewer 가 jugular vein)

오전 verifier 만 돌렸을 때 PTB days fix 자체는 OK 였으나 **startup assertion / 버전 핀 같은 하드닝 권고는 critic 만 발견**. 오후 code-reviewer 1차에서 `_safe_send` 26곳 중 7곳 미치환 발견 — 이거 안 보고 push 했으면 **production silent failure 7개 알림 path 영구 stuck**.

**원칙**:
- verifier = "선언한 acceptance criteria 충족 검증" (self-approve 금지)
- code-reviewer = "선언 안 된 갭 발견" (블로커 발급권)
- critic = "구조적 약점/하드닝 권고" (다관점 갭)
- **3개는 직교 — 어느 하나도 다른 둘로 대체 불가**

학습 #30 ("발굴 도구 = 데이터 품질 검사기") 와 결합: code-reviewer 자체가 **개발자가 놓치는 것을 발굴하는 검사기**. verifier 의 "PASS" 와 reviewer 의 "REQUEST_CHANGES" 가 자주 공존 — 둘 다 봐야 진짜 안전.

### 부수 효과 — 신규 잡 등록

`dart_disclosure` 잡이 `04:05 매일` 으로 등록됨 (4/28 도입 후 11일째 미등록). 검증: 5/10 04:05 KST 첫 발사. 5/11 sqlite `MAX(rcept_dt)` 가 5/10 또는 5/11 이면 fix 정상.

### 학습 #28~31 + #32 종합

| # | 학습 | 핵심 |
|---|---|---|
| #28 | 잡 실행 카운트 ≠ 데이터 품질 | 매일 발사 ≠ 매일 정상 데이터 (5/8 derived 컬럼 사고) |
| #29 | 외부 사이트 응답 변경 → pip upgrade 먼저 | pykrx 1.2.4 → 1.2.8 (5/8 사고) — fix 호출 site 전수 적용 (5/9 #2 사고) 으로 확장 |
| #30 | 발굴 도구가 데이터 품질 검사기 | 사용자 발굴 시도 = 데이터 검사 발굴 (5/8 derived) |
| #31 | 의존성 메이저 업그레이드 시 break-change 매핑 검증 | PTB v19→v20 days= 매핑 사고 (5/9 오전) |
| #32 | 팀 구조 비대체성 (verifier ≠ reviewer ≠ critic) | 5/9 오후 사고 — reviewer 가 7곳 미치환 잡음 |
| #33 | Advisor Pattern: critic/reviewer/verifier = Opus, 나머지 sub-agent = Sonnet | 다수 의견 (wshobson 135 agents, MindStudio advisor strategy) — "After Sonnet session, run Opus over output. It catches things cheaper models miss". 호출 빈도 反 비례로 비용 효율 OK |
| #34 | verifier ≠ system-level reviewer | 5/10 옵션 C 사례 — verifier APPROVE 후 reviewer/critic 가 3 blocker 발견 (log inode POSIX FD + CI 테스트 false PASS + mscore 영구 false alarm). verifier 는 "선언 충족" 만 보고 시스템 시맨틱 / false sense of security / 운영 영향은 reviewer/critic 영역 |
| #35 | Adversarial audit 결과 false alarm 비율 = 시스템 성숙 지표 | 5/10 5 audits 중 4건 false alarm — 학습 #13~#33 누적 효과로 시스템 견고. 다음 audit 사이클 ROI 체감 예상 |
| #36 | PROGRESS.md "펜딩" 항목 직접 검증 필수 | 5/10 Wave 3 시도 — iCloud 펜딩 (이미 wired) / universe 페이지네이션 펜딩 (5/5 c8b71c1 라고 git log 에 적혔으나 실제 수동 트리거 시 여전히 60종목) — PROGRESS 자체 stale 가능. 추측 금지 + 직접 grep/sqlite/실행 검증 |
| #37 | debugger git log 신뢰 X — 실데이터 검증 | 5/10 universe debugger 1차: c8b71c1 fix 결론. 실제: KIS API 30건 한계 + 페이지네이션 자체 없음. 2차: 공식 샘플 + 실제 API 호출로 진짜 root cause 발견 |
| #38 | 자동화 ROI 평가는 워크플로 분석 후 | 5/10 옵션 A2 (KR_DEEPSEARCH 자동화) 추천 시 현재 워크플로 ("KR_DEEPSEARCH.md 보고 진행" 한 줄) 분석 안 함 → 잘못 추천. 사용자 질문 "장점이 있어?" 로 정정. 자동화 제안 전 현재 단계 시간 측정 필수 |
| #39 | MCP 노출 ≠ 인프라 — context 부담 누적 | 5/11 backfill 인프라 디자인 시 사용자 질문 "MCP 추가하면 Claude.ai 무거워지자나" 로 자동 catchup 우월성 발견. 자동화 = 봇 자율 운영 + Claude.ai context 0 영향. MCP 노출은 명확한 사용자 trigger 필요한 도구만 (set_alert, manage_watch 등). 백필/정비/모니터링은 봇 내부 |

---

## 📜 5/9 세션 (오전) — PTB v20+ days= 매핑 시스템 버그 일괄 fix

### 🚨 사용자 신고 + 즉시 진단

사용자: 텔레그램 SAT_PORT_CHECK 알림 사진 + "이거 금요일에 자꾸 날아오는데"

**근본원인 (5분 진단)**:
- `python-telegram-bot >= 20.0` 부터 `JobQueue.run_daily(days=...)` 매핑이 변경됨:
  - **이전 (v19 이하)**: `0=mon, 1=tue, ..., 6=sun`
  - **이후 (v20+)**: `0=sun, 1=mon, ..., 5=fri, 6=sat`
- 검증: `JobQueue._CRON_MAPPING == ('sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat')`
- 코드는 v19 매핑으로 작성. v21.10 사용 중 → **모든 잡이 1일 일찍 발사**.

**증거 데이터**:
1. SAT_PORT_CHECK = `days=(5,)` → v20에서 'fri' = 금요일 발사 (사용자 사진)
2. `daily_collect_job = days=(0,1,2,3,4)` → 'sun-thu' = **금요일 데이터 누락**. `daily_snapshot` 5/8 (Fri) 0건 / 4/24 (Fri) 일부.
3. `weekly_us_harvest = days=(6,)` → 'sat' = 토 03:00 (의도: 일 03:00) — 1일 빠름.
4. 36개 `run_daily` 잡 중 33개 영향 (3개는 `days=` 없음 또는 전체일).

### 일괄 fix (commit 미정)

`main.py` 5216~5276 영역 6단계 replace_all (충돌 회피 순서):

| Step | Before | After | Count | 의도 |
|------|--------|-------|-------|------|
| 1 | `(1,2,3,4,5)` | `(2,3,4,5,6)` | 2 | 화-토 (us_summary) |
| 2 | `(0,1,2,3,4)` | `(1,2,3,4,5)` | 19 | 평일 |
| 3 | `(0, 1, 2, 3, 4)` | `(1, 2, 3, 4, 5)` | 2 | 평일 (pension) |
| 4 | `(0,)` | `(1,)` | 1 | 월 (universe_update) |
| 5 | `(6,)` | `(0,)` | 10 | 일 (weekly 잡 9종 + sunday_30) |
| 6 | `(5,)` | `(6,)` | 2 | 토 (weekly_review, sat_port_check) |

미변경: `(0,1,2,3,4,5,6)` 2건 (us_ratings + event_d1, "전체" 의도).

**검증**:
- `python3 ast.parse` ✅
- verifier 독립 검증: APPROVE / Confidence high / Blockers 0
- venv PTB v21.10 + `_CRON_MAPPING` 매핑 직접 확인
- 봇 재시작 (launchctl kickstart -k) → 새 PID 정상 boot, /health OK

**5/8 백필**: 토요일 KIS API `inquire-price` 500 무한 retry → 보류. 월요일 정상화 후 재시도.

### 학습 #31 — 의존성 메이저 버전 업그레이드 시 break-change 매핑 검증 필수

PTB 19→21 메이저 업그레이드 시 `days=` 매핑 컨벤션 변경. requirements.txt `>=21.10` 만 보고는 모름. 핵심 시그니처가 바뀌면:
- 라이브러리 release notes 정독 (특히 `versionchanged` 마커)
- `_CRON_MAPPING` 같은 내부 상수 직접 import 후 sanity 검증
- 실데이터로 1일치라도 비교 (`daily_snapshot` 영업일 누락 = 시스템적 day-shift 신호)

학습 #28 (잡 실행 카운트 ≠ 데이터 품질)의 변형: **잡이 매일 실행되는 것처럼 보여도 매핑이 1일 밀리면 영구 결함**. 사용자 알림 이상 (사진 첨부) 같은 외부 신호가 가장 먼저 감지함 — 코드 검증보다 빠름.

---

## 📜 5/8 세션 — 봇 점검 7+1 fix + 발굴 + 딥서치

### ⓪ 사용자 점검 요청 → 사고 7건 발견

`/dash` 발굴 시도하다 **봇 데이터 파이프라인 큰 사고 발견**. 이전 점검은 잡 실행 카운트만 보고 데이터 품질 검증 누락 (학습 #13 재현).

### ① pykrx 1.2.4 → 1.2.8 (8e7fbdc)
- pension_collect 5/6/5/7 평일 saved=0 침묵 (pykrx 1.2.4 KRX 응답 컬럼 변경 호환 깨짐)
- `pip install --upgrade pykrx` + requirements.txt `>=1.2.8`
- KRX 인증은 정상 (가설 오진), pykrx 라이브러리 버전 문제

### ② _safe_send 헬퍼 + Markdown parse fallback (6b47c28)
- 매크로 대시보드 14건+ "Can't parse entities" 발송 실패 (1주일 사용자 알림 누락)
- 매수감시 / d1_alert 동일 패턴
- `_safe_send(context, text, parse_mode="Markdown")` — 1차 Markdown / 2차 plain text fallback
- 3곳 적용 (다른 40+ send_message 호출은 보존)

### ③ DART FK + wise NoneType (d662b69)
- 090740 코오롱생명과학 stock_master 미등록 → FK 위반
- wise 인텔리안테크 매일 None.strip() 에러
- INSERT 전 stock_master 존재 확인 + `(item.get(...) or "").strip()` 가드

### ④ _exec_us_ratings friendly error (0ab8ee1)
- ticker 누락 시 traceback → friendly `{"error": "..."}` 응답
- ticker 시그니처 default = ""

### ⑤ Silent failure escalation 헬퍼 (a35b691, 학습 #27 첫 실증)
- `_track_silent_failure / _reset_silent_failure / _alert_silent_failure` (24h cooldown)
- daily_pension_collect 평일 saved=0 3회 연속 시 텔레그램 escalate
- pension_collect 침묵 사고 재발 방지

### ⑥ 🚨 daily_snapshot derived 컬럼 영구 결손 fix (6fee418) — **이 세션 최대 사고**
사용자 "기능 다 정상이라며 전부확인해" 지적 정확. 발굴 시도 중 발견:
- **fscore 14, fcf 2, mscore 0, consensus_target 0, foreign/inst_net_amt 0** (4/15부터 약 한 달 영구 0)
- **원인 3건**:
  1. update_all_alpha_metrics — count >= 500 임계값. 1Q26 분기 분산 (202603=19, 202512=485) → 둘 다 미통과 → MAX(202603) 19종목만
  2. _update_consensus — db_collector.py:749 주석 처리, **함수 미구현**
  3. KIS FHPTJ04160001 종목별 금액 0 응답 (PROGRESS 4/15 메모 알려진 한계)
- **fix 3건**:
  1. update_all_alpha_metrics per-ticker mode (종목별 가용 최신 분기 자동 선택)
  2. `_update_consensus_in_snapshot` 신규 (consensus_history → daily_snapshot)
  3. `_update_supply_in_snapshot` 신규 (pykrx 1.2.8 종목별 외인/기관 매매)
- **복구**: fscore 14→507, fcf 2→463, consensus 0→509, foreign 0→2,497, inst 0→2,311
- verifier APPROVE (Confidence high, Blockers 0)

### ⑦ KR 발굴 + 풀 딥서치 1건
- daily_snapshot 정상화 후 RR 매트릭스 발굴 → Tier 1 종목 선별
- **064400 LG씨엔에스 풀 딥서치** (thesis v1 작성 후 사용자 보강):
  - 3-Gate 3/3 통과
  - 1Q26 OP +19.4%, NI +41.8% (시장 컨센 부합/상회)
  - AX/RX/CBDC 3축 신성장 (Palantir/OpenAI, 피지컬웍스, Stable Coin)
  - 캡티브 50.9% (LG전자 25.1%, LG화학 20%) + 외부 비계열 회복
  - PE 13.5 vs Peer 평균 23.9 (-43%), ROIC 32.6%
  - 신용등급 AA (2025 상향)
  - 감시가 65,000원 RR 3.71 (Starter 3~5%), 60K 도달 시 RR 4.14 (2차 트랜치)
  - Bear case: LG지주 1Q -37% 미달 → 그룹 IT 투자 둔화
  - Kill Switch: 클라우드&AI 60% 이하 / 캡티브 두 자리수 (-) / 컨센 미스 2분기 연속

### ⑧ Tier 1 후보 9종 PDF 일괄 수집 (604건)
- 삼성화재(91)/HL만도(95)/한국타이어(87)/현대모비스(93)/카카오페이(54)/휴젤(92)/크래프톤(92) 신규
- 삼양식품/파마리서치는 DB 보유 (dedup 0건)
- Claude.ai Project 진행 준비 완료

### 🎯 5/8 커밋 6건 (8e7fbdc → 6fee418), data/thesis/064400_LG씨엔에스.md 신규

---

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
| 2026-05-05 | Dashboard 분리 (f93abb6) + Silent failure 헬퍼 (a35b691) | main.py 9197→5279줄, dashboard.py 3966줄 신규 (35함수 + 4상수 + register_routes). paste only 회귀 0. verifier APPROVE. silent_failure_log + _track/_reset/_alert 헬퍼, pension_collect 적용. |
| 2026-05-08 | 봇 점검 7건 fix + daily_snapshot derived 영구 결손 fix (8e7fbdc~6fee418, 6커밋) | 사용자 발굴 요청 → derived 컬럼 4종 한 달 영구 0 발견. update_all_alpha_metrics per-ticker mode + _update_consensus_in_snapshot/_update_supply_in_snapshot 신규. fscore 14→507, consensus 0→509, foreign 0→2497, inst 0→2311. 부수 fix: pykrx 1.2.4→1.2.8, _safe_send Markdown fallback, DART FK 가드, wise NoneType, _exec_us_ratings friendly error. KR 발굴 + LG씨엔에스 풀 딥서치 v1 (3-Gate 3/3, 65K 감시가 RR 3.71). Tier 1 후보 9종 PDF 604건 수집. |

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
28. **🆕 잡 실행 카운트 ≠ 데이터 품질** (5/8 derived 영구 결손, 학습 #13 진화): 5/5 점검에서 "기능 다 정상" 결론냈는데 사용자 "기능 다 정상이라며 전부확인해" 지적으로 daily_snapshot 4컬럼 (fscore/fcf/consensus/외인기관 수급) **한 달 영구 0** 발견. 잡 실행 로그 (`[Finance] Phase A 완료`) 만 보면 정상이지만 실제 채움률 검사 안 함. → 봇 점검 시 **잡 실행 카운트 + 데이터 채움률 (NULL/0/positive 분포) 둘 다** 검사 필수. 학습 #13 "수집 성공 but 0값" 재현. 정기 헬스체크 SQL 쿼리 자동화 가치 있음 (별도 작업).
29. **🆕 외부 사이트 응답 변경 → pip upgrade 먼저 시도** (5/8 pykrx): KRX 응답 컬럼 변경으로 pykrx 1.2.4 KeyError. "KRX 인증 만료" 가설 오진 → 환경변수 정상 확인 → `pip install --upgrade` 1회로 1.2.4→1.2.8 해결. 외부 라이브러리 깨질 때 **인증/네트워크/코드 의심 전에 라이브러리 버전 업그레이드 우선** 시도가 빠른 fix. requirements.txt에 minimum 버전 박아 재발 방지.
30. **🆕 발굴 도구 자동화에 데이터 품질 의존** (5/8 발굴): 발굴 시도가 derived 컬럼 결손 사고를 강제 노출시킴. **새 기능/도구 사용 = 기존 데이터 품질 검사기 역할**. 봇 운영자가 자기 도구 안 쓰면 silent 결손 못 잡음. 정기적 발굴 / 분석 / 백테스트 호출이 데이터 무결성 검사기로 작동.

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
