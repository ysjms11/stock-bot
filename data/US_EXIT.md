# US_EXIT.md (v2)

> 미국 집중투자자용 매도 판단 프레임. Claude.ai + KIS MCP 봇 + 웹 검증 게이트.
> 작성일: 2026-04-25 · **개정: 2026-06-01 (v2, 3차 반증검증 반영)** · 페어: `US_DEEPSEARCH.md` (매수 v4) · `INVESTMENT_RULES.md`
> 3-Gate / 비중 3단계(Starter·Standard·Core) / 카테고리(메인·가치·스윙) 매수 프레임 용어 그대로 승계.
> ⛔ 한국 전용 도구 사용 금지: `manage_report` / `get_dart` / `get_market_signal` / `read_report_pdf` (한국 종목 한정).

---

## ⚠️ 2026-06-01 v2 개정 요약 (3차 반증검증 반영 — KR_EXIT와 동기화)

매도 규칙 전반 3차 외부 반증검증(트레일링·부분익절·이동평균·thesis매도·winner매도)을 거쳐 4개 항목 개정. 상세 근거 Section 7.

1. **가격 상승폭은 매도 사유 아님 (절대원칙 0에 명문화).** 목표가 도달·고수익률은 매도 트리거가 아니라 재산정 트리거. Bessembinder(2018) right-tail 절단 비용 + Barberis-Xiong(2009) 처분효과 회피. (기존 AMD/LITE "익절 검토" 톤은 본 원칙으로 정리 — 가격이 아니라 thesis·밸류 극단으로만 판단.)
2. **부분 익절(부분 30%/50%) 원칙적 제거 → 전량 보유 / 전량 매도 이진.** Shiryaev-Xu-Zhou(2008): goodness index 기준 bang-bang 최적, 내부 해(부분매도) 없음. Barberis-Xiong(2009): 부분익절은 처분효과의 약화된 변형, wealth 감소. → thesis 살아있으면 전량 보유, 깨지면 전량 매도. **예외: 경로 3(더 우월한 기회로 자금 재배치)에 의한 비중 조절만 — 이는 익절이 아니라 재배치.**
3. **경로 1에 "공통 동인 무효화" 신설.** 하이퍼스케일러 AI capex처럼 다수 보유 종목을 관통하는 공통 동인의 구조적 둔화 → 동인에 묶인 종목 전체 재평가. 종목별로 흩어져 있던 capex 트리거를 상위 레이어로 격상. (미국 포트는 거의 전부 AI capex 베팅 — NVDA·AVGO·AMZN — 이라 한국보다 더 중요.)
4. **Section 7에 3차 검증 결과 정직 기록.**

**불변 원칙(검증 재확인, 유지)**: 매도는 3경로로만 / Fisher 3경로 / 단순 valuation 고평가는 매도 사유 아님 / 8주 hold 메인 적용 금지(이미 v1 반영) / 트레일링은 스윙 한정(이미 v1 반영) / round-trip 방어는 가격 매도룰이 아니라 thesis + 분산.

---

## 0. 절대 원칙 (매도 시)

0. **⭐ 가격 상승폭은 매도 사유가 아니다 (v2 신설).** 목표가 도달 = **매도가 아니라 thesis 재산정**. "+72% 올랐으니 익절", "충분히 올랐으니 정리"는 금지 — Bessembinder(2018, *JFE* 129(3)) 상위 4%가 시장 net wealth 전부 → 가격 상승을 이유로 winner를 자르면 right-tail compounder를 놓친다. Barberis-Xiong(2009, *JF* 64(2)) 처분효과(오른 거 팔면 기분 좋은 심리)는 wealth를 감소시킴. **매도는 오직 thesis 붕괴(경로1) 또는 명백한 기회비용(경로3)으로만. 가격이 얼마 올랐는지는 무관.**
1. **매도는 매수보다 어렵다 (Akepanidtaworn 2023 JoF 78(6))** — 펀드매니저조차 매도 결정의 alpha가 무작위 이하(단 "sell more thoughtfully"이지 "sell less"가 아님). → **3경로 룰 외 매도는 자기 합리화로 간주**.
2. **LLM은 Bull-market에서 조기매도 편향 (Li et al. 2025 FINSABER, arXiv:2505.07078)** — "LLM strategies are overly conservative in bull markets, underperforming passive benchmarks." LS ELECTRIC 케이스(+47~84% 놓침)의 학술적 원인.
3. **PEAD 대형주 소멸 (Martineau 2022 CFR 11(3-4))** — 미국 대형주는 어닝 발표 후 가격 반응이 **2거래일 내 완결**. 5~20일 추가 drift 기대 금지. (한국 PEAD 20일과 정반대)
4. **Sell rating의 비대칭 (Womack 1996 JoF 51(1):137-167)** — Downgrade post-event drift **−9.1% (6개월)** vs Upgrade +2.4%. 미국 S&P 500 Sell 의견 **4.8% (FactSet 2025-12)**, Buy 57.5% — Sell 발생 자체가 강한 시그널.
5. **세제 비대칭** — 미국 LTCG(1년+) **15~20%** vs STCG **최대 37%(+NIIT 3.8%)**. 1년 보유 + 1일 직전 매도는 세후 22%p 손해. **세제 절감이 매도 회피의 변명이 되어선 안 됨 (Munger 1994 USC)**.
6. **전량/전량 이진 (v2 신설).** thesis 살아있으면 **전량 보유**, thesis 깨지면 **전량 매도**. 부분 30%/50% 익절은 원칙 제거(Shiryaev 2008 bang-bang 최적). 회색 지대("일시적인지 구조적인지 모르겠는")에서 50%만 파는 것은 Elliott et al.(2024) 동기화 추론(롱 보유자의 상향 편향)에 의한 자기기만일 수 있음 → 50% 유보 대신 thesis 판정을 더 엄격히. **예외: 경로 3 자금 재배치만 비중 조절 허용.**

---

## 1. LLM 편향 차단 10규칙 (매도 시 적용)

| # | 규칙 | 학술 근거 | 매도 적용 |
|---|---|---|---|
| 1 | **Sycophancy 금지** | Sharma et al. 2023 arXiv:2310.13548 (ICLR 2024) — RLHF가 사용자 영합 답을 강화. 5개 SOTA 모델 모두 sycophancy 노출 ✅ | 사용자가 "팔까?"라고 물으면 매도 쪽으로 기우는 통계적 편향 존재 → **질문은 항상 "팔아야 할 학술 근거 3개 vs 보유할 근거 3개"** 양면 |
| 2 | **FlipFlop 방지** | Laban et al. 2023 arXiv:2311.08596 — 평균 정확도 **−17%p**, 평균 46% 답변 뒤집음. Claude V2는 단일 task에서 **−34%p** (단, 전체 평균 아님) ✅ | "정말 팔까?" 같은 challenger 발화에 흔들리지 말 것. **첫 판단을 XML로 기록 후 변경은 새 데이터 추가 시에만** |
| 3 | **Pre-commitment** | Ariely & Wertenbroch 2002 *Psychological Science* 13(3):219-224 ✅ | 매수 시점에 **Kill Switch 2~3개 + 손절가 + 목표가**를 `set_alert`로 외부화. 자가 deadline은 외부 deadline 대비 약함 → 봇 알림으로 강제 |
| 4 | **Devil's Advocate** | Liang et al. 2023 arXiv:2305.19118 multi-agent debate (Kim 2024 직접 매칭 [검증 불가] → Liang 대체) ⚠️ | 매도 판단 전 **반대 thesis 의도적 생성**: "이 종목을 지금 사겠다는 사람의 근거 3개" + "이게 right-tail 대박일 가능성을 가격 때문에 자르는 건 아닌가" |
| 5 | **Intrinsic reflection 금지** | Huang et al. 2023 arXiv:2310.01798 (ICLR 2024) — "LLMs cannot self-correct reasoning yet" ✅ | "다시 생각해봐"는 정확도 저하. **외부 데이터(get_us_ratings, openinsider, finviz, 8-K)** 추가가 유일한 self-correction 경로 |
| 6 | **Bull-market 조기매도 편향** | Li et al. 2025 FINSABER ✅ — 가장 강력한 근거 | **상승장에서 LLM이 매도 추천하면 일단 의심**. LS ELECTRIC 케이스의 직접 원인. 매도 전 50MA 위 + 3M 컨센 상향 + 백로그 증가 시 **HOLD 우선** |
| 7 | **애널 upgrade clustering = HOLD (미국은 약하게 적용)** | Womack 1996 ✅ + FactSet 2025-12 ✅ | 한국(Buy 93.1%)에선 upgrade 신호 약하지만 **미국(Buy 57.5%)에선 정보가치 큼**. 다수 동시 upgrade는 **HOLD/추가매수 시그널**. 단 Barber 2001 net alpha≈0 → 단독 회전 트리거 금지 |
| 8 | **Category 분기** [실무 관행] | 학술 근거 없음, Fisher/O'Neil/Graham 스타일 차이 반영 ⚠️ | 메인=Fisher 3경로(전량/전량) / 가치=intrinsic value 도달 / 스윙=O'Neil 50MA·8% 손절. 카테고리 변경은 **사전 기록 필수** |
| 9 | **수치 임계 투명성** | Anthropic Prompt Engineering Guide (positive instruction, XML tags) | 매도 임계는 항상 정량으로 기록. "왠지 비싸다"는 매도 사유 금지 |
| 10 | **3경로 룰 (매도 유일 정당화)** | Fisher 1958 *Common Stocks* Ch.6 ✅ | 매도는 ① 사실 오판 / ② 펀더멘털 변화 / ③ 더 매력적 기회 — **이 3개 외 매도는 자기 합리화** |

**LLM 조기매도 4중 결합 경고**: Sharma(sycophancy) + Laban(FlipFlop) + Huang(self-correction 실패) + Li(bull-market 조기매도) — LS ELECTRIC 케이스가 이 4가지가 동시 작동한 표본. 매도 추천 LLM 출력은 4가지 편향 체크 후에만 채택.

---

## 2. 3경로 의사결정 트리 (Fisher 3경로, 미국 적용)

> **검증 무게중심 (v2)**: 경로 1(Thesis)이 최우선·강화. 경로 2(Technical)는 스윙 한정·약화. 가격 기반 매도(트레일링·이평·고수익률)는 메인/가치 종목에서 단독 매도 트리거 아님. 매도 액션은 원칙적으로 전량/전량(경로3 재배치만 예외).

### 경로 1. Thesis Invalidation (전량 청산) ★최우선

**근거**: Fisher 1958 Ch.6 #1 (factual mistake) + #2 (no longer fits criteria); Druckenmiller 2021 "if the reason I bought it has changed"; Rappaport-Mauboussin 2021 Expectations Investing Ch.7 "Expectation Gap"(가격이 함의하는 기대를 회사가 못 채우면 매도 — 단편 신호 아닌 thesis 붕괴, demanding hurdle).

**즉시 전량 청산 트리거 (쿨링 예외, 8-K 기반)**:
- **8-K Item 4.02** Non-Reliance on Previously Issued FS (10-K restatement) ← **Fisher #1 정확히 부합 (Lerman-Livnat 2010 RAS 15(4))**
- **8-K Item 2.06** Material Impairments — HIGH 훼손
- **SEC AAER** (Accounting and Auditing Enforcement Release)
- **FDA 거부 / 임상 중단** (바이오 한정, 8-K Item 8.01)
- **핵심 고객 이탈 / 라이선스 패소** (예: NVDA CUDA 분쟁)

**thesis 손상 시 (3거래일 쿨링 후 전량 재평가 — v2: 부분 50% 폐기)**:
- **8-K Item 5.02** CEO/CFO 사임 (Fisher #2: 경영진 악화)
- **2분기 연속 어닝 미스 + 가이던스 하향** — 대형주는 발표 **2거래일 내 판단** (Martineau 2022). → **thesis 재평가: 깨졌으면 전량 매도, 아니면 전량 보유.** (v2: "50% 축소" 폐지 — 회색 지대 50% 유보는 Elliott 2024 동기화 추론 위험. thesis 판정을 엄격히 하여 이진 결정.)
- 주력 시장 규제 급변, 경쟁사 기술 우위 역전

**⭐ 공통 동인 무효화 (v2 신설)**: 개별 종목 thesis가 **산업 공통 동인**에 구조적으로 의존하면, 그 동인의 구조적 둔화도 thesis 무효화 신호. 종목별 지표보다 **선행** 가능한 뿌리 신호.

| 공통 동인 | 해당 종목(현 보유) | 무효화 신호(높은 문턱) |
|---|---|---|
| AI 데이터센터 capex | NVDA(GPU), AVGO(ASIC), (AMZN 클라우드+capex 집행 주체) | 하이퍼스케일러(Amazon/Google/Meta/MS) 분기 capex 가이던스 **2분기 연속 하향** 또는 AI 투자 회수기 진입 공식화 |

  - 발동 시 **자동 매도 아님 → AI 동인에 묶인 보유 종목 전체(NVDA·AVGO·AMZN 등) 재평가.** 단일 분기 잡음 배제 위해 "2분기 연속" 또는 "공식 회수기 선언" 수준 높은 문턱.
  - ⚠️ 주의: capex 신호는 가격에 선반영될 수 있음(효율적 시장; 반도체는 주가가 동인 가격을 선행). **"조기경보"로 과신 금지 — Section 7.** thesis 재점검 트리거이지 시점 예측 도구 아님.
  - 기존 종목별로 흩어진 capex 트리거(NVDA T2·AVGO Yellow·LITE Yellow 등)는 유지하되, 본 공통 동인이 상위에서 "전체 재평가"를 촉발.

### 경로 2. Technical Exit (스윙·모멘텀 카테고리만)

**근거**: O'Neil *How to Make Money in Stocks* — "cut every single loss when it is 7% or 8% below your purchase price" (8% 하드 스톱); "A sharp, high-volume drop below the 50-day moving average after a long run was a major sell signal."

**적용 한계**:
- **메인·가치 카테고리에는 적용 금지** — O'Neil은 모멘텀 매매 전제. Core thesis intact 시 50MA 이탈만으로 매도 ❌ (트레일링 스톱도 메인 적용 금지 — Dai 2021 평균수익 감소, Bessembinder 2018 right-tail 절단)
- **VP(매물대) 대형주 무력화** — Steidlmayer 1984는 CBOT 선물 일중 데이터 기원. 미국 대형주는 ETF arbitrage·옵션 헷지·passive flow로 매물대 정보가치 희석 ⚠️ [학술 근거 약함]
- **AAII CAN-SLIM 10년 6.3-23.5% 변동성, FFTY ETF 인셉션 후 4.38% vs S&P 500 13.78%** (FFTY Factsheet 2025-09-30) — **O'Neil 룰의 미국 대형주 underperform 실증**

**스윙 매도 임계 (스윙 카테고리만, 손절은 고정 스톱이지 트레일링 아님)**:
- 매수가 **−7~8%** 하드 스톱 (O'Neil) / **−6~10%** (Minervini 2013)
- 50MA 고볼륨 이탈 + 5일 내 반등 실패
- O'Neil 8-week hold rule — **메인 카테고리 적용 금지** [학술 근거 약함, FFTY 실증 반증]

**메인/가치 카테고리 기술 신호 = 매도 아닌 경로1 재점검 알람**:
- 50MA 결정적 이탈 또는 재앙적 갭다운(한 세션 -15%+) → **매도 아님. thesis(경로1) 재점검 트리거.** thesis 무결 시 전량 보유.

### 경로 3. Opportunity Cost (기회비용) — v2: 유일하게 부분 비중 조절 허용

> **INVESTMENT_RULES §3 경로3 능동 발동 기준이 우선** (2026-06-04): 검증 신규후보(3-Gate 3/3) 보유 + 풀투자 시 능동 재배치 스캔 발동, 양쪽 PDF 분해 forward-RR 비교, 재원=최저RR/최약thesis, 20거래일 빈도제한, 세제게이트.

**근거**: Fisher 1958 Ch.6 #3 (the fundamentals of another) — Fisher 본인이 가장 신중한 사유라 명시. **이것은 익절이 아니라 자금 재배치이므로 부분 조절 허용** (전량/전량 이진의 예외).

**임계 (모두 충족 시)**:
- **Implied perpetual g ≥ 10Y T-yield** (Damodaran "Stable Growth Rate" cap, 수학적 불가) → **이는 자동 매도가 아니라 thesis 재산정 사유**. 재산정 결과 thesis 약화 확인 시에만 자금 이동.
- **Fwd PE > 5y avg × 2** OR 업종 Through-Cycle PER 2배 초과 (재산정 트리거)
- **새 후보 종목이 3-Gate 3/3 통과 + Starter→Standard 승격 조건 충족**

**미국 세제 게이트 (강제)**:
- **보유 < 1년**: STCG 최대 37% + NIIT 3.8% → **+22%p** 세후 손해. 자금 이동 보류, holding 만기 대기
- **보유 ≥ 1년**: LTCG 15~20%. 정상 가능
- 단, **세제 절감이 매도 회피 변명이 되어선 안 됨** (Munger 1994 USC)

---

## 3. 미국 시장 특화 매도 시그널 표 (학술 강도순)

> ※ v2: 매도 액션의 "부분 30~50%"는 경로1 thesis 손상 시 **전량 재평가**로, 경로3 재배치 시에만 부분 조절. 가격 상승 기반(Implied g 등)은 자동 부분매도가 아니라 재산정 트리거.

| 시그널 | 임계 | 학술 근거 | 강도 | 매도 액션(v2) |
|---|---|---|---|---|
| **8-K Item 4.02 (FS restatement)** | 발생 즉시 | Lerman-Livnat 2010 RAS 15(4) ✅ | ✅ 강 | **즉시 전량** |
| **FDA 거부 / 임상 중단** (바이오) | 8-K Item 8.01 | DiMasi 2016 JHE 47:20-33 ✅ | ✅ 강 | **즉시 전량** |
| **Sell rating 신규 발행** | 4.8% 빈도 | Womack 1996 drift −9.1%/6M ✅ | ✅ 강 | thesis 재평가 → 전량/보유 |
| **2분기 연속 어닝 미스 + 가이드 하향** | YoY EPS 미달 + guide ↓ | Bernard-Thomas 1989; Martineau 2022 (대형주 2일) ✅ | ✅ 강 | **thesis 재평가 → 전량/보유** (v2: 50% 폐지) |
| **Opportunistic insider 클러스터 매도** | 과거 패턴 부재 + 다중 매도 | Cohen-Malloy-Pomorski 2012 −82bp/월 ✅ | ✅ 강 | thesis 재평가 |
| **Implied perpetual g ≥ 10Y T-yield** | Reverse DCF | Damodaran *Investment Valuation* Ch.12 ✅ | ✅ 강 | **재산정 트리거** (자동매도 아님), 경로3 자금이동 시 부분 |
| **공통 동인(AI capex) 2분기 연속 하향** | 하이퍼스케일러 가이던스 | 신설 — 동인 묶인 종목 전체 재평가 | ✅ 강 | **전체 재평가 트리거** |
| **Confidential 13F 청산 (11A 수정공시)** | hedge fund 매도 | Agarwal-Jiang-Tang-Yang 2013 ✅ | ⚠️ 중 | 가격·기본면 결합 후 재평가 |
| **Heavy short + 낮은 기관보유율** | percentile 결합 | Asquith 2005 EW −215bp/월; Boehmer 2008 ✅ | ⚠️ 중 | 대형주는 기관보유율 높아 신호 약함 |
| **PE ratio 단독** | — | ❌ 근거 없음 | ❌ 약 | 단독 매도 금지 |
| **고수익률 단독 (예: +72%)** | — | ❌ Bessembinder right-tail 절단 | ❌ 약 | **매도 금지 — 재산정 트리거** (v2) |
| **Short Float 단독 10/20/30%** | — | ❌ 업계 관행 | ❌ 약 | 단독 매도 금지 |
| **UOA Call/Put > 2 단독** | — | ❌ Pan-Poteshman 방향성만 | ❌ 약 | 단독 매도 금지 |
| **O'Neil 8-week 만료 후 매도** | 대형주 | ❌ FFTY 실증 반증 | ❌ 약 | 메인 적용 금지 |
| **VP(매물대) 저항 도달** | 대형주 | ⚠️ Steidlmayer 선물 기원 | ⚠️ 약 | 보조지표만 |

**중요 주의**: 가격 상승폭(+N%) 자체는 어떤 강도로도 매도 트리거가 아니다(절대원칙 0). 밸류 극단(Implied g ≥ 10Y)은 매도가 아니라 thesis 재산정 사유이며, 재산정 결과 thesis 약화 시에만 경로3로 자금 이동.

---

## 4. 매도 판단 STEP 0~8 (미국 봇 도구 + 웹 매핑)

### 0. 라이트 체크 (5분, 일일 모니터링)
```
[티커] 미국 라이트 체크.
1. get_stock_detail(ticker) — 50MA·150MA·200MA, 52주 고가 괴리 (위치 확인용, 매도 아님)
2. get_us_ratings(ticker, mode="trend", months=3) — 3M 컨센 방향
3. get_us_ratings(ticker, mode="events", days=30) — 30일 upgrade/downgrade 빈도
4. get_news(ticker, sentiment=true) — 최근 헤드라인 감성
5. thesis 한 문장 — 아직 유효한가? + 공통 동인(AI capex) 둔화 신호?

이상 1개+ → STEP 1 빠른 판정.
LLM 편향 4중 경고: Sharma + Laban + Huang + Li 동시 작동 의심 시 매도 추천 보류.
가격이 많이 올랐다는 이유로는 STEP 1 에스컬레이션 금지(절대원칙 0).
```

### 1. 빠른 매도 판정 (10분)
```
[티커] 미국 빠른 매도 스크린. 현재가 약 $[현재가]. 보유 평단 $[평단], 수익률 [%].

봇 병렬: get_regime + get_macro(mode="dashboard") + get_us_ratings(consensus) + get_news(sentiment=true)
웹 병렬: stockanalysis.com/stocks/[ticker]/financials/ratios/ + finviz.com/quote.ashx?t=[ticker]
봇: get_stock_detail(period="D60") — 50MA 위치(스윙만 매도 신호, 메인은 알람)

3경로 속판:
경로 1 (Thesis): 최근 30일 8-K Item 4.02 / 2.06 / 5.02 발생? FDA·SEC AAER? 공통 동인(capex) 2분기 연속 하향?
경로 2 (Technical, 스윙만): 50MA 이탈 + 5일 반등 실패? 매수가 −7~8%?
경로 3 (Opportunity): 새 후보 3-Gate 3/3 + thesis 재산정상 현 보유 약화?

세제 게이트: 보유 ≥ 1년?
LLM 편향 게이트: Bull market 조기매도 4중 경고
절대원칙 0: 고수익률은 매도 사유 아님 — 재산정만

출력: 3경로 결과, 세제 상태, 액션 (전량 보유 / 전량 매도 / 경로3 재배치 / 쿨링3일)
```

### 2. 풀 매도 판정 (30분)
```
[티커] 미국 풀 매도 판정. 8 STEP. 봇·웹 병렬.

━━ STEP 0. 레짐 + LLM 편향 + 절대원칙 0 사전 체크 (3분) ━━
봇: get_regime + get_macro(dashboard)
사전 기록: <pre_commit>매수 시 Kill Switch + 손절가 + 목표가 재확인</pre_commit>
LLM 4중 편향 체크 + "가격 상승은 매도 사유 아님" 자가 확인

━━ STEP 1. Thesis Invalidation 점검 (5분) — 경로 1 최우선 ━━
웹 병렬: sec.gov EDGAR 8-K(30일)/10-K/10-Q + stockanalysis.com news
체크: 8-K Item 4.02/2.06/5.02? 가이던스 하향? 핵심 고객·계약 이탈? 바이오: FDA CRL·임상중단?
⭐ 공통 동인: 하이퍼스케일러 capex 가이던스 2분기 연속 하향? → AI 묶인 종목 전체 재평가
→ thesis 붕괴 확인 시 전량 청산 (Fisher #1)

━━ STEP 2. 컨센·애널리스트 (5분) ━━
봇: get_us_ratings(trend 3M / events 30d / consensus)
- Sell rating 신규(4.8% 희소) → thesis 재평가 / 다수 Downgrade → Womack drift −9.1%
- 다수 Upgrade clustering → HOLD

━━ STEP 3. 수급 (5분) ━━
웹: openinsider / whalewisdom / finviz(Short Float, Inst Own) / barchart UOA
- Opportunistic insider 클러스터(Cohen 2012) / Confidential 13F(Agarwal 2013) / Heavy short+Inst↓(Asquith 2005)
⚠️ 단독 금지: Short Float, UOA

━━ STEP 4. 밸류에이션 (5분) — 경로 3 재산정 게이트 ━━
웹: stockanalysis.com ratios / statistics
업종 프레임(매수 v4 동일): SaaS Rule40<20+FCF<0(2Q) / 반도체 Fwd PE>35x / 바이오 PoS·runway / 광모듈 EV/Sales>6x
Reverse DCF: Implied g ≥ 10Y T-yield → **재산정 트리거(자동매도 아님)**. 재산정 후 thesis 약화 시 경로3
※ 고밸류 자체는 매도 아님 — thesis 재점검 사유 (절대원칙 0)

━━ STEP 5. 기술적 (3분) — 경로 2 (스윙만) ━━
봇: get_stock_detail(D60/D250/orderbook)
스윙만: 매수가 −7~8% 하드스톱 / 50MA 고볼륨 이탈+5일 반등실패
⚠️ 메인·가치: thesis intact 시 기술 단독 매도 금지 → 경로1 재점검 알람
⚠️ 트레일링 스톱: 메인 적용 금지(Dai 2021). VP 대형주 무력화

━━ STEP 6. 세제 게이트 (3분) — 경로 3 강제 ━━
보유<1년 STCG 37%+NIIT 3.8% → 자금이동 보류 / 보유≥1년 LTCG 15-20%
"세제 절감 변명" 금지(Munger 1994)

━━ STEP 7. 시뮬레이션 + Devil's Advocate (3분) ━━
봇: simulate_trade(sells=[...])
Devil's Advocate: "지금 이걸 사겠다는 사람 근거 3개" + "right-tail 대박을 가격 때문에 자르는 건 아닌가"(Bessembinder)

━━ STEP 8. 결정·기록 (3분) ━━
봇: set_alert(decision/trade) + write_file(research/[ticker]/exit_[date].md)

XML 출력:
<exit_judgment>
  <regime>공격/중립/위기 + VIX</regime>
  <ticker>[ticker]</ticker>
  <principle_0>가격 상승폭 매도 사유 아님 확인 ✓</principle_0>
  <pre_commit>매수 시 Kill Switch + 손절 + 목표 재확인</pre_commit>
  <holding>평단 / 현재가 / 수익률 / 보유기간</holding>
  <path1_thesis>8-K / FDA / SEC AAER / 가이드 (T1 발현 여부)</path1_thesis>
  <common_driver>AI capex 2분기 연속 하향 여부 → 동인 묶인 종목 전체 재평가</common_driver>
  <path2_technical>50MA / 매수가 −7~8% (스윙만, 메인은 알람)</path2_technical>
  <path3_opportunity>새 후보 3-Gate + 현보유 재산정 약화 / 세제</path3_opportunity>
  <tax_gate>보유 1년+ / LTCG vs STCG</tax_gate>
  <devil_advocate>반대 근거 3개 + right-tail 절단 점검</devil_advocate>
  <llm_bias_check>Sharma/Laban/Huang/Li 4중 점검</llm_bias_check>
  <decision>전량 보유 / 전량 매도 / 경로3 재배치 / 쿨링3일</decision>
  <action>set_alert / simulate_trade / thesis 업데이트</action>
  <sources>학술 / 웹 / data_gaps</sources>
</exit_judgment>
```

---

## 5. 미국 업종별 매도 시그널

### SaaS (Rule of 40, NRR)
- **thesis 재평가**: Rule of 40 **< 20** AND FCF margin **< 0** (2분기 연속) → 전량/보유 재평가
- **NRR < 100%** (Bessemer / SEG 2025: <90% 1.2x, 100~110% 6.0x, >120% 11.7x) → 강한 약화
- **즉시 전량**: 8-K Item 4.02

### 반도체 로직/AI (NVDA, AMD, AVGO)
- **Through-Cycle PER 17.5x × 2 = 35x 초과** (Regions IM 2019 [⚠️ 1차 검증 실패], Damodaran 22-25x 병용) → 재산정 트리거
- **⭐ 공통 동인(경로1)**: 하이퍼스케일러 capex 2분기 연속 −10%+ 컷 → AI 종목 전체 재평가. 현재(2026-04) AMZN $200B / GOOGL $175-185B / META $115-135B / MSFT $110-120B 합산 $600-700B (+36~67% YoY)
- **Book-to-Bill < 1.0** (SEMI BB 2017 폐지, ASML/AMAT/LRCX/KLA 평균 대안)
- **PEAD 대형주 소멸 (Martineau 2022)**: 2거래일 내 판단

### 바이오 (CRSP)
- **Pipeline rNPV × PoS** (DiMasi 2016 P1→승인 11.83%; Wong-Siah-Lo 2019 종양 3.4%)
- **Phase 결과 / FDA CRL / 임상 중단** (8-K Item 8.01): 즉시 전량
- **Cash runway < 18개월**

### 광모듈 (LITE)
- **EV/Sales peer 평균 6x 초과** → 재산정 트리거
- **공통 동인**: 하이퍼스케일러 광 capex 하향 (경로1)
- **Pluggable→CPO 전환** (단 2026-03 NVIDIA $2B LITE 투자로 supply 통합 강화 해석)

---

## 6. 미국 케이스 (NVDA / AMD / CRSP / AVGO / LITE)

> 보유 현황(2026-04): NVDA 17주 / AMD 17주 / CRSP 70.12주 / AVGO 5주 / LITE 1주.
> 각 thesis 무효화 조건 = 매수 시 Kill Switch. **v2: 트리거 발동 = 전량 재평가(부분 30/50 폐지). 가격 상승폭은 매도 사유 아님.**

### NVDA (평단 $188.30)
**Bull thesis**: DC 폭발적 성장 + AI infra 독점 + CUDA moat (공통 동인: AI 데이터센터 capex)
| 트리거 | 임계 | 액션(v2) |
|---|---|---|
| T1 즉시 | 10-K restatement (8-K 4.02), CUDA 라이선스/특허 패소 | 전량 |
| T2 thesis재평가 | DC Capex 가이드 **2분기 연속** 하향 OR DC YoY < 30% | thesis 재평가 → 전량/보유 |
| ⭐공통동인 | 하이퍼스케일러 합산 capex 2분기 연속 하향 | AI 종목 전체 재평가 |
| 재산정 | Implied g ≥ 10Y T-yield OR Fwd PE > 35x | 재산정 트리거(자동매도 아님) |
**현재**: DC $62.3B (+75% YoY), $1T orders through 2027 → **트리거 미발현. 고수익률은 매도 사유 아님.**

### AMD (평단 $201.67)
**Bull thesis**: Zen 서버 점유 + MI300/MI325 NVIDIA 대안
| 트리거 | 임계 | 액션(v2) |
|---|---|---|
| T1 즉시 | 회계 restatement | 전량 |
| T2 thesis재평가 | MI300/MI325 가이드 미스 2분기 연속 OR DC YoY < 50% | thesis 재평가 → 전량/보유 |
| 재산정 | SOTP 가중 20x 하회 OR Implied g ≥ 10Y | 재산정 트리거 |
| 공통동인 | 서버 CPU 점유 vs ARM 역전 / 하이퍼스케일러 capex 둔화 | 재평가 |
**현재 (v2 정정)**: thesis(서버 점유·MI300) 유효 시 **전량 보유**. **고수익률(+72%) 자체는 매도 사유 아님 — 절대원칙 0.** Implied g가 10Y T-yield 초과하는 수학적 과열이면 경로3 재산정만. ("익절 검토" 톤 폐기.)

### CRSP (평단 $55.03)
**Bull thesis**: Casgevy 상업화 + zugo-cel/in vivo 파이프라인
| 트리거 | 임계 | 액션(v2) |
|---|---|---|
| T1 즉시 | FDA 거부, 임상 중단(8-K), 회계 restatement | 전량 |
| T2 thesis재평가 | Casgevy 분기 매출 컨센 미스 2분기 연속 | thesis 재평가 → 전량/보유 |
| 파이프라인 | zugo-cel H2 2026 부정적 (LBCL ORR<70%, SLE 미달) | thesis 재평가 |
| Cash | Runway < 18M (현재 ~15분기) | 미발현 |
**현재**: 손절 영역 별도(이득 프레임 아님). PoS 종양 3.4%/비종양 13.8% 적용.

### AVGO (평단 $400.32)
**Bull thesis**: AI ASIC(Google TPU/Meta MTIA/OpenAI/Anthropic) + VMware SW 마진 (공통 동인: AI capex)
| 트리거 | 임계 | 액션(v2) |
|---|---|---|
| T1 즉시 | 회계 restatement | 전량 |
| T2 thesis재평가 | AI revenue YoY < 50% 2분기 연속 OR ASIC 백로그 QoQ 감소 | thesis 재평가 → 전량/보유 |
| ⭐공통동인 | 하이퍼스케일러 capex 2분기 연속 하향 | AI 종목 전체 재평가 |
| 재산정 | VMware EBITDA margin 분기 −200bp+ / SOTP 22x 하회 / Implied g ≥ 10Y | 재산정 트리거 |
**현재**: Q1 FY26 AI $8.4B (+106%), 백로그 $73B (78% TPU = 집중 리스크 명시) → **트리거 미발현.**

### LITE (평단 $684.50)
**Bull thesis**: 800G/1.6T + Blackwell + NVIDIA $2B 투자
| 트리거 | 임계 | 액션(v2) |
|---|---|---|
| T1 즉시 | 회계 restatement | 전량 |
| T2 thesis재평가 | 800G/1.6T ASP 분기 −15%+ 하락 | thesis 재평가 → 전량/보유 |
| 재산정 | EV/Sales > peer 6x / NVIDIA $2B 약정 단축·취소 | 재산정 트리거 |
| 공통동인 | 하이퍼스케일러 capex 2분기 연속 하향 | AI 종목 전체 재평가 |
**현재 (v2 정정)**: thesis 유효 시 **전량 보유**. 고수익률(+28%) 자체는 매도 사유 아님. NVIDIA $2B 투자·$400M+ OCS 백로그·FY26 +77% 컨센 → 트리거 미발현.

---

## 7. 확신 없는 항목 + 3차 검증 결과 (정직한 명시)

### 3차 반증검증 확정 결과 (2026-06-01, KR_EXIT와 동기화)

**[기각/강등] 부분 익절 (부분 30%/50%)** — Shiryaev-Xu-Zhou(2008, *Quant Finance* 8(8)): goodness index 기준 bang-bang(전량/전량) 최적, 내부 해 없음. Barberis-Xiong(2009, *JF* 64(2)): 부분익절은 처분효과의 약화된 변형, wealth 감소. Elliott et al.(2024, *RAS* 29): 롱 보유자는 펀더멘털을 상향 편향 해석 → 회색 지대 "50%만" 유보가 자기기만 위험. → **원칙적 제거. thesis 살아있으면 전량 보유, 깨지면 전량 매도. 경로3 재배치만 부분 조절 예외.**

**[기각] 고수익률 기반 매도** — Bessembinder(2018, *JFE* 129(3)): 상위 4%가 시장 net wealth 전부 → 가격 상승을 이유로 winner 자르면 right-tail 절단. → **"+N% 올랐으니 익절" 폐기. 가격 상승은 재산정 트리거.** (기존 AMD +72%·LITE +28% "익절 검토" 톤 정정.)

**[유지·강화] 경로 1 thesis 기반 매도** — 검증 steel-man: 트리거의 가치는 '조기 감지'가 아니라 '판단의 질'. Faugère et al.(2004, *JPM* 30): fundamental break 기반 매도가 가격 룰보다 정당. → 경로 1 최우선·demanding hurdle.

**[유지·강화] 공통 동인(AI capex) → 경로1 격상** — 종목별로 흩어진 capex 트리거를 상위 레이어로. 미국 포트가 거의 전부 AI capex 베팅이라 한국보다 중요. 단 capex는 가격에 선반영될 수 있어(반도체는 주가가 동인 선행) "조기경보 아님, 재점검 트리거".

**[이미 반영, 유지] 8주 hold 메인 금지 / 트레일링 스윙 한정** — v1에서 이미 FFTY 실증·O'Neil 모멘텀 전제로 메인 적용 금지. Dai(2021)·Bessembinder(2018) 추가 확인. 변경 없음.

### [학술 근거 약함] (v1 유지)
- Short Float 10/20/30% 단독, UOA Call/Put>2 임계, O'Neil 8-week 미국 대형주, AAII CAN-SLIM 변동성, VP 대형주, Through-Cycle PER 17.5x, 광모듈 EV/Sales 4~6x, DC YoY 50% 임계, SaaS Rule40<20 — 모두 [실무 관행] 또는 1차 출처 미확인.

### [검증 불가] (v1 유지)
- Devil's Advocate Kim 2024 → Liang 2023 대체. Subrahmanyam 2025 워킹페이퍼. Claude V2 −34%p 단일 task. O'Neil 50MA ETF arbitrage 영향. Munger "3%p" 보수 인용.

### [데이터 불충분] (v1 유지)
- 보유 종목 현재 Reverse DCF Implied g 정확값(종목별 계산 필요). LITE 800G/1.6T ASP. AMD MI300/MI325 매출 분해.

### 세제 (v1 유지)
- 미국 LTCG 15~20% / STCG 37%+NIIT 3.8% (IRS Topic 409). Munger 1994 "compounding의 적 = 양도세" 미국 적용 가능(한국 일반투자자와 달리).

---

## 8. 학술·실무 근거 (검증 가능 출처)

| 원칙 | 출처 | 강도 |
|---|---|---|
| 가격 상승 ≠ 매도 / right-tail | Bessembinder 2018 *JFE* 129(3):440-457 | ✅ |
| 부분익절 = 처분효과 변형 | Barberis-Xiong 2009 *JF* 64(2):751-784 | ✅ |
| bang-bang 최적(전량/전량) | Shiryaev-Xu-Zhou 2008 *Quant Finance* 8(8):765-776 | ✅ |
| 롱 보유자 상향 편향 | Elliott-Hobson-Van Landuyt-White 2024 *RAS* 29:3534-3563 | ✅ |
| 매도는 매수보다 어렵다 | Akepanidtaworn et al. 2023 *JoF* 78(6):3055-3098 | ✅ |
| Bull-market LLM 조기매도 | Li, Kim, Cucuringu, Ma 2025 FINSABER arXiv:2505.07078 | ✅ |
| Sycophancy | Sharma et al. 2023 arXiv:2310.13548 | ✅ |
| FlipFlop | Laban et al. 2023 arXiv:2311.08596 | ✅ |
| Self-correction 실패 | Huang et al. 2023 arXiv:2310.01798 | ✅ |
| Multi-agent debate | Liang et al. 2023 arXiv:2305.19118 | ✅ |
| Pre-commitment | Ariely-Wertenbroch 2002 *Psych Sci* 13(3):219-224 | ✅ |
| 매도 3경로 | Fisher 1958 *Common Stocks* Ch.6 | ✅ |
| Expectation Gap | Rappaport-Mauboussin 2021 *Expectations Investing* Ch.7 | ✅ |
| 트레일링 스톱 평균수익 감소 | Dai-Marshall-Nguyen-Visaltanachoti 2021 *IRF* 21(4):1334-1352 | ✅ |
| 트레일링 무가치/추세전환 | Clare-Seaton-Smith-Thomas 2013 *J Asset Mgmt* 14:182-194 | ✅ |
| stopping premium | Kaminski-Lo 2014 *JFM* 18:234-254 | ✅ |
| Thesis-based exit | Druckenmiller 2021 The Hustle | ✅ |
| O'Neil 7-8% 스톱, 50MA | O'Neil *How to Make Money in Stocks* 4th ed. Ch.10 | ✅(자기계발서) |
| Minervini 6-10% | Minervini 2013 | ✅(자기계발서) |
| PEAD 대형주 소멸 | Martineau 2022 *Critical Finance Review* 11(3-4) | ✅ |
| 한국 PEAD 잔존 | 이병주-김동철 2017 한국재무학회 | ✅ |
| Womack drift | Womack 1996 *JoF* 51(1):137-167 | ✅ |
| 애널 net alpha | Barber-Lehavy-McNichols-Trueman 2001 *JoF* 56(2) | ✅ |
| 미국 Sell 4.8% | FactSet Butters 2025-12; 2026-03 Buy 58.2% | ✅ |
| Insider opportunistic | Cohen-Malloy-Pomorski 2012 *JoF* 67(3) | ✅ |
| 13F confidential | Agarwal-Jiang-Tang-Yang 2013 *JoF* 68(2) | ✅ |
| Short interest + 기관 | Asquith-Pathak-Ritter 2005 *JFE* 78(2) | ✅ |
| Informed shorts | Boehmer-Jones-Zhang 2008 *JoF* 63(2) | ✅ |
| Option volume | Pan-Poteshman 2006 *RFS* 19(3) | ✅ |
| 8-K abnormal return | Lerman-Livnat 2010 *RAS* 15(4) | ✅ |
| 단기 reversal (3일 쿨링) | Jegadeesh 1990 *JoF* 45(3) | ✅ |
| Reverse DCF cap | Damodaran *Investment Valuation* Ch.12 | ✅ |
| Rule of 40 / NRR | Feld 2015; Bessemer / SEG / SaaS Capital 2025 | ✅ |
| SEMI BB 폐지 | semi.org (2017-12) | ✅ |
| 바이오 PoS | DiMasi 2016 *JHE* 47:20-33; Wong-Siah-Lo 2019 *Biostatistics* 20(2) | ✅ |
| O'Neil 미국 대형주 underperform | FFTY Factsheet 2025-09-30 (4.38% vs 13.78%) | ✅ |
| 미국 LTCG/STCG | IRS Topic 409 | ✅ |
| 세금 compounding | Munger 1994 USC "Worldly Wisdom" | ✅ |

---

## 변경 이력
- **2026-04-25 v1**: 신규 작성. US_DEEPSEARCH v4 일관. PEAD 대형주 소멸·미국 Sell 4.8%·LTCG 세제·LLM 4중 편향. 8주 hold 메인 금지·트레일링 스윙 한정 반영.
- **2026-06-01 v2**: 3차 반증검증 반영(KR_EXIT 동기화). (1) 절대원칙 0 "가격 상승≠매도" 명문화 (2) 부분 30/50 익절 제거 → 전량/전량 이진, 경로3 재배치만 예외 (3) 경로1에 공통 동인(AI capex) 격상 — 종목별 흩어진 capex 트리거 상위 레이어화 (4) AMD/LITE "익절 검토" 톤 정정 (5) Section 7 검증결과 기록. 근거: Bessembinder 2018, Shiryaev 2008, Barberis-Xiong 2009, Elliott 2024, Dai 2021.
