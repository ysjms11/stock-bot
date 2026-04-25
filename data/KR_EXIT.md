# KR_EXIT.md (v1)

> 한국 집중투자자용 매도 판단 프레임. KOSPI/KOSDAQ 종목 중 평단 대비 +10% 이상 이득 영역 전용.
> 작성일: 2026-04-25 · 페어: `KR_DEEPSEARCH.md` (매수 v4) · `INVESTMENT_RULES.md`
> 3-Gate / 비중 3단계(Starter·Standard·Core) / 카테고리(메인·가치·스윙) 매수 프레임 용어 그대로 승계.
> 손절(-7~10%)은 별도 체계, 평단 ±5% 박스권은 보유 유지 (KR_DEEPSEARCH 라이트 체크 영역).
> 최우선 목표: LS ELECTRIC식 LLM 조기매도 재발 방지.

---

## 0. 절대 원칙 (매도 시)

1. **매도는 매수보다 어렵다 (Akepanidtaworn 2023 JoF 78(6))** — 펀드매니저조차 매도 결정의 alpha가 무작위 이하. → **3경로 룰 외 매도는 자기 합리화로 간주**.
2. **LLM은 Bull-market에서 조기매도 편향 (Li et al. 2025 FINSABER, arXiv:2505.07078)** — LS ELECTRIC 케이스(+47~84% 놓침)의 학술적 원인.
3. **한국 PEAD 잔존 (이병주-김동철 2017 한국재무학회)** — 한국 대형주 PEAD 약 20일. 미국 2거래일과 정반대 → 한국은 어닝 발표 후 20일 drift 활용 가능, 즉시 매도 판단 자제.
4. **한국 애널 매수 편향 93.1% (KCMI 2025-07 김준석)** — Buy 93.1% 환경에서 upgrade 정보가치 약함, 단 **하향 클러스터는 매우 강한 매도 신호**.
5. **한국 컨센 TP 정보가치 소멸 (KCMI 2026 김준석)** — 2013 이후 대형주 TP 정보가치 통계적 소멸, **중소형주에서만 잔존**. 대형주(삼성전자/SK하이닉스)에 "TP 90% 도달" 단독 매도 트리거 사용 금지.
6. **한국 세제** — 상장주식 양도세 0 (대주주 50억+ 제외), 거래세 **2025년 0.15% / 2026년 0.20% (현재 적용)** (기획재정부 2025-12-01 시행령 개정). Munger 1994 USC compounding 논리는 한국 일반투자자에게 직접 적용 부적합 → **거래세 + 슬리피지 + 심리적 비용**으로 재구성.

---

## 1. LLM 편향 차단 10규칙 (매도 시 적용)

| # | 규칙 | 학술 근거 | 매도 적용 |
|---|---|---|---|
| 1 | **Sycophancy 금지** | Sharma et al. 2023 arXiv:2310.13548 (ICLR 2024) — RLHF가 사용자 영합 답을 강화. 5개 SOTA 모델 모두 sycophancy 노출 ✅ | 사용자가 "팔까?" 질문 자체가 매도 답변을 유도하는 leading question. **질문은 항상 "팔아야 할 학술 근거 3개 vs 보유할 근거 3개"** 양면 |
| 2 | **FlipFlop 방지** | Laban et al. 2023 arXiv:2311.08596 — 평균 정확도 **−17%p**, 평균 46% 답변 뒤집음. Claude V2는 단일 task에서 **−34%p** (단, 전체 평균 아님) ✅ | "정말 팔까?" 같은 challenger 발화에 흔들리지 말 것. **첫 판단을 XML로 기록 후 변경은 새 데이터 추가 시에만** |
| 3 | **Pre-commitment** | Ariely & Wertenbroch 2002 *Psychological Science* 13(3):219-224 ✅ | 매수 시점에 **Kill Switch 2~3개 + 손절가 + 목표가**를 `set_alert`로 외부화. 자가 deadline은 외부 deadline 대비 약함 → 봇 알림으로 강제 |
| 4 | **Devil's Advocate** | Liang et al. 2023 arXiv:2305.19118 multi-agent debate (Kim 2024 직접 매칭 [검증 불가] → Liang 대체) ⚠️ | 매도 판단 전 **반대 thesis 의도적 생성**: "이 종목을 지금 사겠다는 사람의 근거 3개" |
| 5 | **Intrinsic reflection 금지** | Huang et al. 2023 arXiv:2310.01798 (ICLR 2024) — "LLMs cannot self-correct reasoning yet" ✅ | "다시 생각해봐"는 정확도 저하. **외부 데이터(get_consensus, get_dart, read_report_pdf, get_supply 등)** 추가가 유일한 self-correction 경로 |
| 6 | **Bull-market 조기매도 편향** | Li et al. 2025 FINSABER ✅ — 가장 강력한 근거 | **상승장에서 LLM이 매도 추천하면 일단 의심**. LS ELECTRIC 케이스의 직접 원인. 매도 전 200MA 위 + 30일 컨센 상향 + thesis 유효 시 **HOLD 우선** |
| 7 | **애널 upgrade clustering = HOLD (한국 약하게 적용)** | Womack 1996 JoF 51(1):137-167 ✅ + KCMI 2025-07 김준석 ✅ | **한국(Buy 93.1%)에선 upgrade 신호 약함**. 다수 동시 upgrade는 HOLD/추가매수 시그널이나 강도 약함. 단 **하향 클러스터(2건+)는 매우 강한 매도 신호** |
| 8 | **Category 분기** [실무 관행] | 학술 근거 없음, Fisher/O'Neil/Graham 스타일 차이 반영 ⚠️ | 메인=Fisher 3경로 / 가치=intrinsic value 도달 / 스윙=O'Neil 50MA·8% 손절. 카테고리 변경은 **사전 기록 필수** |
| 9 | **수치 임계 투명성** | Anthropic Prompt Engineering Guide (positive instruction, XML tags) | 매도 임계는 항상 정량으로 기록. "왠지 비싸다"는 매도 사유 금지. "RSI(14) ≥80", "외국인 5일 누적 ≥-2σ" 등 봇 호출 결과 수치로만 |
| 10 | **3경로 룰 (매도 유일 정당화)** | Fisher 1958 *Common Stocks* Ch.6 ✅ | 매도는 ① Thesis Invalidation / ② Technical Exit / ③ 기회비용 — **이 3개 외 매도는 자기 합리화** |

**LLM 조기매도 4중 결합 경고**: Sharma(sycophancy) + Laban(FlipFlop) + Huang(self-correction 실패) + Li(bull-market 조기매도) — LS ELECTRIC 케이스가 이 4가지가 동시 작동한 표본. 매도 추천 LLM 출력은 4가지 편향 체크 후에만 채택.

---

## 2. 3경로 의사결정 트리 (Fisher 3경로, 한국 적용)

### 경로 1. Thesis Invalidation (즉시 청산 또는 50% 부분)

**근거**: Fisher 1958 Ch.6 #1 (factual mistake) + #2 (no longer fits criteria); Druckenmiller 2021 The Hustle "if the reason I bought it has changed".

**한국 거래소 즉시 청산 트리거 (쿨링 예외, KR_DEEPSEARCH Hard Kill 5개와 매핑)**:

| 트리거 | 봇 도구 | 근거 |
|---|---|---|
| **감사의견 비적정** (한정/부적정/의견거절) | `get_dart` 사업보고서 | 거래소 상장규정 §48 |
| **자본잠식 50% 이상** | `get_dart` 재무제표 | 거래소 상장규정 §48 |
| **분식회계 확정** | `get_dart` 정정공시 | 자본시장법 §178 |
| **횡령·배임 확정** (CEO/CFO/Chair) | `get_dart` 내부자 | 거래소 상장규정 §48 |
| **거래소 실질심사 대상 지정** | `get_news sentiment=true` | 거래소 상장규정 §47 |

**부분 50% 매도 트리거 (3거래일 쿨링 후, Jegadeesh 1990)**:
- CEO/CFO 사임 (Fisher #2: 경영진 악화)
- **2분기 연속 어닝 미스 + 가이던스 하향** — 한국 PEAD 20일 활용 (이병주-김동철 2017)
- 주력 시장 규제 급변, 경쟁사 기술 우위 역전
- **내부자 -3인 이상 매도 클러스터** (30일 내 CEO/CFO/Chair, 2024.7.24 시행 자본시장법 §173조의3 사전공시 활용)

### 경로 2. Technical Exit (스윙·모멘텀 카테고리만)

**근거**: O'Neil *How to Make Money in Stocks* — "cut every single loss when it is 7% or 8% below your purchase price with absolutely no exception" (8% 하드 스톱); "A sharp, high-volume drop below the 50-day moving average after a long run was a major sell signal."

**적용 한계**:
- **메인·가치 카테고리에는 적용 금지** — O'Neil은 모멘텀 매매 전제. Core thesis intact 시 50MA 이탈만으로 매도 ❌
- **한국 가격제한폭 ±30% 보정** — 일중 trailing stop 비효율. 일종가 기준만 사용
- **한국 변동성 보정** — KOSPI/KOSDAQ 일중 변동성은 미국 S&P 500 대비 약 1.3-1.5배. ATR 임계 보정 필요
- **O'Neil 8-week hold rule 한국 재현 실증 부재** [학술 근거 약함] — 단, 매수 후 +20% 이상 8주 이내 도달 시 강제 적용 (실무 관행)

**스윙 매도 임계 (KR_DEEPSEARCH 일관)**:
- 매수가 **−7~8%** 하드 스톱 (O'Neil) / **−6~10%** (Minervini 2013)
- 50MA 고볼륨 이탈 + 5일 내 반등 실패
- 200일선 이탈 (Core 종목, Faber 2007 trend-following [한국 재현 미확인])

⚠️ **8-week hold 활성 종목은 위 트리거 모두 무효**. 8주 경과 후에만 발동.

### 경로 3. Opportunity Cost (기회비용, 부분 30~50%)

**근거**: Fisher 1958 Ch.6 #3 (the fundamentals of another) — Fisher 본인이 가장 신중한 사유라 명시.

**임계 (모두 충족 시)**:
- 새 후보 종목이 **KR_DEEPSEARCH 3-Gate 3/3 통과 + F-Score ≥8 OR G-Score ≥6**
- 기존 종목 forward 1Y 기대수익률이 신규 종목 대비 **-10%p 이상 열위**
- **Implied perpetual g ≥ 10Y T-yield (Damodaran "Stable Growth Rate" cap, 수학적 불가)**
- **Fwd PE > 5y avg × 2** OR 업종 Through-Cycle PER 2배 초과

**한국 세제 보정 (Munger 1994 USC 재구성)**:
- 한국 일반투자자 양도세 0 → **Munger compounding 직접 적용 부적합**
- 한국 기회비용 = **거래세 + 슬리피지 + 심리적 비용**으로 재구성
- 거래세 **2025년 0.15% / 2026년 0.20% (현재 적용)** (기획재정부 2025-12-01 시행령 개정)
- 회전율 10회/년 시 거래세 부담: 2025 1.5%p / 2026 2.0%p
- 거래세 + 슬리피지 합계(약 0.5%p 추가) 차감 후에도 우위 유지 시에만 발동

---

## 3. 한국 시장 특화 매도 시그널 표 (학술 강도순)

| # | 시그널 | 임계 | 봇 도구 | 학술 근거 | 강도 | 매도 액션 |
|---|---|---|---|---|---|---|
| 1 | **회계 restatement / 거래소 실질심사** | DART 정정공시 / KRX 공시 | `get_dart` / `get_news` | 자본시장법 §178; 상장규정 §47-48 ✅ | ✅ 강 | **즉시 전량** |
| 2 | **30일 컨센 TP 하향 클러스터** | 하향 ≥2건 | `get_consensus` | Womack 1996 JoF 51(1) drift −9.1%/6M ✅; KCMI 2025-07 김준석 (한국 Buy 93.1% 환경에서 하향은 강한 신호) ✅ | ✅ 강 | **부분 30~50%** |
| 3 | **30일 EPS 하향 클러스터** | 하향 ≥3건 | `get_consensus` | Womack 1996; 한국 PEAD 20일 (이병주-김동철 2017) ✅ | ✅ 강 | **부분 30~50%** |
| 4 | **2분기 연속 어닝 미스 + 가이드 하향** | YoY EPS 미달 + guide ↓ | `get_consensus` + `get_dart` | Bernard-Thomas 1989 JAR 27; 한국 PEAD 20일 활용 ✅ | ✅ 강 | **부분 50% (한국은 발표 후 20일 drift 활용 가능)** |
| 5 | **내부자 -3인 이상 매도 클러스터** | 30일 CEO/CFO/Chair 3인+ | `get_dart mode=insider` | 자본시장법 §173조의3 (2024.7.24) 사전공시 ✅ | ✅ 강 | **부분 30~50%** |
| 6 | **Implied perpetual g ≥ 10Y T-yield** | Reverse DCF | (계산) | Damodaran *Investment Valuation* Ch.12 ✅ | ✅ 강 | **부분 30~50%** |
| 7 | **컨센 TP 90% 도달 (중소형주)** | 가격/TP ≥0.9 | `get_consensus` | KCMI 2026 김준석 (중소형주에서만 잔존) ✅ | ⚠️ 중 | 보조 트리거 |
| 8 | **컨센 TP 90% 도달 (대형주)** | 가격/TP ≥0.9 | `get_consensus` | KCMI 2026 김준석 (한국 대형주 TP 정보가치 2013 이후 통계적 소멸) ❌ | ❌ 약 | **단독 매도 트리거 금지** (삼성/SK하이닉스 등) |
| 9 | **50일선 결정적 이탈 + 반등 실패** | 5일 하회 + 재이탈 | `get_stock_detail D60` | O'Neil sell rule (실무 관행, 한국 직접 검증 부족) ⚠️ | ⚠️ 중 | 스윙·모멘텀만 |
| 10 | **200일선 이탈 (Core 종목)** | 종가 기준 | `get_stock_detail D250` | Faber 2007 trend-following (한국 재현 미확인) ⚠️ | ⚠️ 중 | 메인 카테고리도 신호 |
| 11 | **외국인 5일 누적 매도 + 52주 고가 ±5%** | -2σ 이탈 + 52w 고가 근접 | `get_supply history` + `get_stock_detail` | Choe-Kho-Stulz 1999 JFE 54(2): destabilize 가설 **부정** ❌ | ❌ 약 | **단독 사용 금지, 보조만** |
| 12 | **신용잔고율 10%+ 급증** | 5일 누적 +50% | `get_market_signal credit` | 학술 근거 부재, 실무 관행 ❌ | ❌ 약 | 단독 매도 금지 |
| 13 | **대차잔고 +50% (5일)** | KRX NSDS 데이터 | `get_market_signal lending` | KRX NSDS 2025.3.31 공매도 재개 후 데이터 부족 ❌ | ❌ 약 | 단독 매도 금지 |

**해석 룰**:
- ✅ 강 시그널 1개 발동 → 즉시 매도 검토
- ⚠️ 중 시그널 2개 이상 동시 발동 → 매도 검토
- ❌ 약 시그널만으로는 매도 불가, 강/중 시그널 보조용으로만

---

## 4. 매도 판단 STEP 0~8 (한국 봇 도구 + PDF 게이트)

### 0. 라이트 체크 (5분, 일일 모니터링)

```
[티커] 한국 라이트 체크.
1. get_stock_detail(ticker="[티커]") — 50MA·150MA·200MA, 52주 고가 괴리
2. get_consensus(ticker="[티커]") — 30일 EPS·TP 방향
3. get_news(ticker="[티커]", sentiment=true) — 최근 헤드라인 감성
4. thesis 한 문장 — 아직 유효한가?
5. 8-week hold 활성 여부 — 매수일 + 56일 vs 현재일

이상 1개+ → STEP 1 빠른 판정 에스컬레이션.
LLM 편향 4중 경고: Sharma + Laban + Huang + Li 동시 작동 의심 시 매도 추천 보류.
8-week hold 활성 시 STEP 즉시 종료, HOLD 강제.
```

### 1. 빠른 매도 판정 (10분)

```
[티커] 한국 빠른 매도 스크린. 현재가 약 [현재가]원. 보유 평단 [평단]원, 수익률 [%].

봇 병렬: get_regime + get_macro(mode="dashboard") + get_consensus + get_news(sentiment=true)
봇 병렬: get_stock_detail(period="D60") + get_supply(mode="history") + get_dart(mode="insider")
봇 병렬: get_market_signal(mode="credit") + get_market_signal(mode="short_sale") + get_market_signal(mode="lending")

3경로 속판:
경로 1 (Thesis): 최근 30일 DART 공시 트리거 5개 발동? 내부자 매도 클러스터?
경로 2 (Technical, 스윙만): 50MA 이탈 + 5일 반등 실패? 8-week hold 미활성?
경로 3 (Opportunity): 신규 종목 KR_DEEPSEARCH 3-Gate 통과 + 기존 종목 -10%p 열위?

세제: 한국 양도세 0 (Munger 직접 적용 X), 거래세 2026 0.20% + 슬리피지
LLM 편향 게이트: Bull market에서 조기매도 추천 시 4중 경고 적용

출력: 3경로 결과, 액션 (HOLD/부분30/부분50/전량/쿨링3일)
```

### 2. 풀 매도 판정 (30분)

```
[티커] 한국 풀 매도 판정. 8 STEP. 봇 도구 + PDF 게이트.

━━ STEP 0. Pre-commitment + LLM 편향 사전 체크 (3분) ━━
봇: get_regime + get_macro(dashboard)
사전 기록: set_alert(log_type="decision", notes="매도 검토 트리거 X, HOLD 조건 Y, SELL 조건 Z")
LLM 4중 편향 체크: Sharma/Laban/Huang/Li 동시 작동 가능성?
8-week hold 활성 여부 우선 확인 → 활성 시 STEP 즉시 종료, HOLD 강제

━━ STEP 1. Thesis Invalidation 점검 (5분) — 경로 1 ━━
봇 병렬:
- get_dart(mode="disclosure_list", days=30) — 사업보고서/임시공시
- get_dart(mode="insider", days=30) — 내부자 매도 클러스터
- get_news(sentiment=true, days=14) — 부정 뉴스

체크:
- 한국 거래소 즉시 청산 트리거 5개 발동? (감사/자본잠식/분식/횡령/실질심사)
- CEO/CFO 사임? 핵심 고객·계약 이탈?
- 자본시장법 §173조의3 사전공시 매도 발생?

→ 발견 시 즉시 청산 (쿨링 예외, Fisher #1)

━━ STEP 2. 컨센·애널리스트 (5분) ━━
봇:
- get_consensus(ticker="[티커]") — 30일 EPS·TP 방향

신호:
- 30일 컨센 TP 하향 클러스터 (≥2건) → drift −9.1%/6M (Womack 1996)
- 30일 EPS 하향 클러스터 (≥3건) → 부분 30-50%
- 다수 동시 Upgrade clustering → HOLD (한국 Buy 93.1% 환경에서 신호 약함)
- 컨센 TP 90% 도달 → 중소형주만 보조 트리거, 대형주는 사용 금지 (KCMI 2026)

━━ STEP 3. 수급 (5분) ━━
봇 병렬:
- get_supply(mode="history", days=20) — 외인·기관 20일 누적
- get_market_signal(mode="credit") — 신용잔고율
- get_market_signal(mode="short_sale") — 공매도 추이
- get_market_signal(mode="lending") — 대차잔고
- get_market_signal(mode="program") — 프로그램 매매

⚠️ 단독 트리거 금지:
- 외국인 5일 연속 매도 (Choe-Kho-Stulz 1999 destabilize 부정)
- 신용잔고율 10%+ (학술 근거 부재)
- 대차잔고 +50% (NSDS 2025.3.31 후 데이터 부족)
- 강/중 시그널 보조용으로만

━━ STEP 4. PDF 게이트 (10분) — 매도 시에도 강제 ━━
봇:
- manage_report(action="collect", ticker="[티커]")
- manage_report(action="list", ticker="[티커]", days=30)

⛔━━ PDF 게이트 (필수) ━━⛔

**절대 원칙: txt 요약 금지, PDF 직접 읽기 강제**

read_report_pdf 최소 2건 + 가능하면 다른 증권사 (편차 확인):
- 1번째: 가장 최신 리포트
- 2번째: 다른 증권사 (편차 확인)

추출 항목:
1. TP 산출 방식 변화 (PER/PBR/EV_EBITDA/SOTP/DCF)
2. EPS 추정 변화 (직전 리포트 대비)
3. 가이던스 변화 (회사 vs 증권사)
4. 사이클 위치 판단 (초·중·후기·고점)
5. 리스크 섹션 신규 항목 (thesis invalidation 후보)
6. Bull thesis 변화 (매수 시점 근거 3개 유지?)

근거: Diether-Malloy-Scherbina 2002 JoF 57(5) (analyst dispersion)

PDF 게이트 미통과 시: STEP 5 진입 금지, 매도 결정 금지

━━ STEP 5. 밸류에이션 (3분) — 경로 3 게이트 ━━
업종 프레임 (KR_DEEPSEARCH 동일):
- 반도체: Forward PER · Forward P/B 사이클 위치
- 전력기기: Forward PER · 백로그 YoY
- 바이오: rNPV × PoS (P1→승인 11.83% / 종양 3.4%)
- 가치주: PBR · ROE · 배당수익률

Reverse DCF (Damodaran):
- Implied perpetual g ≥ 10Y 한국 국채 수익률 → 부분 30-50% (수학적 불가)
- Implied 10Y CAGR > GDP ~3% 10년 지속 → Yellow

━━ STEP 6. 기술적 (3분) — 경로 2 (스윙만) ━━
봇:
- get_stock_detail(period="D250") — 200MA + Stage
- get_stock_detail(mode="orderbook")

스윙·모멘텀 카테고리 한정:
- 매수가 −7~8% 하드 스톱 (O'Neil)
- 50MA 고볼륨 이탈 + 5일 반등 실패
- 8-week hold 활성 시 무효

⚠️ 메인·가치 카테고리: thesis intact 시 기술 단독 매도 금지

━━ STEP 7. 시뮬레이션 + Devil's Advocate (3분) ━━
봇: simulate_trade(sells=[{ticker, qty, price}])

Devil's Advocate (Liang 2023 multi-agent debate):
- "이 종목을 지금 사겠다는 사람의 근거 3개"
- 보유 시나리오 + 매도 시나리오 양방향 시뮬레이션
- 양방향 시뮬레이션 결과 보유 우위 시 → HOLD 회귀

━━ STEP 8. 결정·기록 (3분) ━━
봇:
- set_alert(log_type="decision", actions=[...], grades={...}, regime, notes)
- set_alert(log_type="trade", side="sell", price, qty, reason, grade)
- write_file("data/thesis/[티커]_[종목명].md", thesis 무효화 사유)

XML 출력 (매수 KR_DEEPSEARCH 일관, 부록 참조)
```

---

## 5. 한국 케이스 (LS ELECTRIC + SK하이닉스)

> 사용자 한국 보유: 효성중공업 / 삼성전자 / SK하이닉스 / HD현대일렉 / LS ELECTRIC / HD한국조선해양 / 코웨이 등 7종.

### 5-1. LS ELECTRIC (2026-04-17 LLM 조기매도 추천 실패 케이스)

**상황** (5/5 HOLD 조건):
- Thesis 유효 (국내 초고압변압기 + 미국 AI 데이터센터 전력)
- 목표가 미도달
- 과열 시그널 없음 (RSI <70)
- 손절 미터치
- 200MA +60% 상회

**3경로 판정**:
| 경로 | 발동 | 근거 |
|---|---|---|
| 경로 1 (Thesis) | ❌ | Thesis 3/3 유효, 거래소 트리거 없음 |
| 경로 2 (Technical) | ❌ | 50MA 위, 200MA +60% 상회 |
| 경로 3 (기회비용) | ❌ | 동급 대안 종목 부재 |

→ **0/3 발동 → HOLD 강제**

**LLM 편향 진단**:
- 규칙 1 (Sycophancy): 사용자 "팔까?" 질문에 동조
- 규칙 5 (Intrinsic reflection): 외부 데이터 없이 "충분히 올랐다" 자기검토
- 규칙 6 (Bull-market 조기매도): 상승장 +50% 도달만으로 매도 추천

**결과**: LLM 조기매도 권유 → 이후 +47~84% 추가 상승 놓침

**교훈**: 3경로 0/3 + 200MA 상회 + 8-week hold 미경과 시 LLM은 매도 권유 자격 자체 없음

⚠️ **현재 시점 보정 (2026-04-25)**: 200MA +60% 상회 상태에서는 trailing stop 검토 영역. 정점 -15% 도달 시 부분 매도(30-50%) 검토 가능. 단, 이는 **경로 2 부분 발동**으로 분류, LLM 단독 판단 금지.

### 5-2. SK하이닉스 (2026-04-24 HOLD 판단 검증)

**상황** (KIS API 일봉 검증):
- 3주 +51.4% 급등 (807,000원 → 1,222,000원)
- 52주 고가 -3.5% 근접
- 내 목표 93% 도달 / 컨센 TP 80% 막 통과
- Forward PEG 0.016
- HBM 메모리 슈퍼사이클 진행 중 (2026 시장 전망)
- 1Q26 영업이익 +405% 컨센 상회
- HBM3E 점유율 62%
- 4월 일제 상향 (KB 190만 / SK 200만)

**경영진 클러스터 매수**: 3명 17일 전 (CEO/CFO/Chair) → thesis 강화 신호 (자본시장법 §173조의3 사전공시 활용)

**외국인 수급**: 5일 중 4일 순매도 → 약 시그널 (Section 3 #11), 단독 트리거 불가 (Choe-Kho-Stulz 1999 destabilize 부정)

**3경로 판정**:
| 경로 | 발동 | 근거 |
|---|---|---|
| 경로 1 (Thesis) | ❌ | Thesis 3/3 유효, 1Q26 실적 +405%, 내부자 +3명 매수 |
| 경로 2 (Technical) | ❌ | **8-week hold 강제 활성** (매수 후 3주, +51% 도달) |
| 경로 3 (기회비용) | ❌ | 동급 대안 부재, Forward PEG 0.016 |

→ **0/3 발동 + 8-week hold 활성 → HOLD 강제**

**컨센 TP 80% 통과의 함의**: 한국 애널 TP는 상승기에 후행 (이승희 외 2013 KDAS 15(5)). 4월 일제 상향 진행 중 → 규칙 7 (한국 upgrade 신호 약함, 단 하향이 아니라 상향 클러스터로 HOLD 정당화).

**KCMI 2026 김준석 적용**: 대형주 컨센 TP 정보가치 2013 이후 소멸 → "TP 80% 통과" 단독 매도 트리거 금지.

**Trailing stop 설정**: 정점 1,222,000원 -15% = 1,038,700원 → **1,100,000원으로 상향 보수적 설정** (한국 변동성 반영)

**최종 판단**: HOLD + Trailing stop 1,100,000원 + 8주 경과 후 (2026-05 중순) STEP 재실행

---

## 6. 확신 없는 항목 (정직한 명시)

### [학술 근거 약함]
- **외국인 5일 연속 매도**: Choe-Kho-Stulz 1999 JFE 54(2)가 destabilize 가설 **부정**. 단독 트리거 사용 금지, 보조 시그널만
- **신용잔고율 10%+ 단독 임계**: 학술 직접 근거 부재, 실무 관행
- **대차잔고 +50% (5일)**: KRX NSDS 2025.3.31 공매도 재개 후 데이터 부족
- **O'Neil 8-week hold rule 한국 대형주 적용**: 한국 직접 학술 검증 부재 (실무 관행)
- **한국 가격제한폭 ±30% 보정 ATR 임계**: 학술 직접 근거 부재
- **컨센 TP 90% 도달 (대형주)**: KCMI 2026 김준석 한국 대형주 TP 정보가치 2013 이후 소멸 → 단독 매도 트리거 금지

### [검증 불가]
- **Devil's Advocate "Kim et al. 2024 ACL"** — 단일 매칭 논문 미확인 → Liang et al. 2023 arXiv:2305.19118 multi-agent debate로 대체
- **Claude V2 −34%p (FlipFlop)** — Laban 2023 Figure 1 단일 task example, 전체 평균 −17%p. 인용 시 맥락 명시
- **강형구·전진규 2022 한국증권학회지 51(3)**: 공동저자 매칭 확인 필요 (검증 불충분)
- **Eom-Hahn-Sohn 2019 PBFJ 53 "60일/20일 비대칭" 정확 수치**: 본문 미확인 → 인용 자제

### [데이터 불충분]
- **한국어 LLM 편향 측정 부재**: Sharma 2023, Laban 2023 등 모두 영어 환경 측정. 한국어/한국 종목 맥락에서 sycophancy/FlipFlop 직접 측정 연구 부재. 본 프레임은 영어 측정 결과를 외삽한 것임을 명시. 사용자 직접 검증: 동일 종목 동일 상황에 대해 LLM 응답 5회 반복 → 일관성 측정
- **2020-2026 구간 한국 PEAD 재현/소멸 연구 부족**: 사용자 백테스트 영역
- **한국 CEO/CFO/Chair 3인 동시 매수/매도 클러스터링 abnormal return 직접 실증 부족**: Cohen-Malloy-Pomorski 2012 미국 +82bp/월을 한국에 외삽 부적합. 2024.7.24 시행 사전공시제 이후 데이터로 신규 연구 필요

### [실무 관행 / 사용자 매수 프레임 정정]
- **거래세 정정**: 사용자 KR_DEEPSEARCH 거래세 "0.18%" → **2025년 0.15% / 2026년 0.20% (현재 적용)** (기획재정부 2025-12-01 시행령 개정). 회전율 10회/년 시 거래세 부담: 2025 1.5%p / 2026 2.0%p
- **이승희 외 2013 학회명 정정**: KDISS (Journal of the Korean Data and Information Science Society) → **KDAS** (Journal of the Korean Data Analysis Society) 15(5)
- **Munger 1994 한국 적용 한계**: USC Business School Speech의 "compounding의 적은 매년 양도세 실현" 논리는 미국 환경. 한국 일반투자자 양도세 0이므로 직접 적용 부적합. 한국 기회비용 = 거래세 + 슬리피지 + 심리적 비용으로 재구성 필요
- **컨센 TP 정보가치 (대형주 vs 중소형주 차등)**: KCMI 2026 김준석 - 한국 대형주 TP 정보가치 2013 이후 소멸, 중소형주에서만 잔존 → 대형주(삼성전자/SK하이닉스 등) TP 가중치 < 중소형주 TP 가중치

---

## 7. 학술·실무 근거 (검증 가능 출처)

| 원칙 | 출처 | 강도 |
|---|---|---|
| 매도는 매수보다 어렵다 | Akepanidtaworn et al. 2023 *JoF* 78(6):3055-3098 | ✅ |
| Bull-market LLM 조기매도 | Li, Kim, Cucuringu, Ma 2025 "FINSABER" arXiv:2505.07078 | ✅ |
| Sycophancy | Sharma et al. 2023 arXiv:2310.13548 (ICLR 2024) | ✅ |
| FlipFlop | Laban et al. 2023 arXiv:2311.08596 (avg −17%p) | ✅ |
| Self-correction 실패 | Huang et al. 2023 arXiv:2310.01798 (ICLR 2024) | ✅ |
| Multi-agent debate (Devil's Advocate) | Liang et al. 2023 arXiv:2305.19118 (Kim 2024 직접 매칭 검증 불가로 대체) | ✅ |
| Pre-commitment | Ariely & Wertenbroch 2002 *Psychological Science* 13(3):219-224 | ✅ |
| Single-task FlipFlop 도식 | Laban 2023 Figure 1 (Claude V2 −34%p, 전체 평균 아님) | ⚠️ |
| 매도 3경로 룰 | Fisher 1958 *Common Stocks and Uncommon Profits* Ch.6 (Wiley 1996/2003판 p.105-106) | ✅ |
| Thesis-based exit | Druckenmiller, The Hustle 2021-05-11 인터뷰 | ✅ |
| O'Neil 7-8% 스톱, 50MA 룰, 8-week hold | O'Neil *How to Make Money in Stocks* 4th ed. (2009) Ch.10 | ✅ (자기계발서) |
| Minervini 6-10% 스톱 | Minervini 2013 *Trade Like a Stock Market Wizard* | ✅ (자기계발서) |
| 한국 PEAD 잔존 | 이병주-김동철 2017 한국재무학회 (한국 20일 vs 미국 0~2일 비대칭 정당화) | ✅ |
| Womack drift | Womack 1996 *JoF* 51(1):137-167 (Sell drift −9.1%/6M) | ✅ |
| 애널 net alpha | Barber-Lehavy-McNichols-Trueman 2001 *JoF* 56(2):531-563 (net ≈ 0) | ✅ |
| Recommendation drift 국제 비교 | Jegadeesh-Kim 2006 *JFM* 9(3):274-309 | ✅ |
| 한국 매수 93.1% | 자본시장연구원 김준석 2025-07 "애널리스트의 낙관적 편향" 2025-15호 | ✅ |
| 한국 컨센 TP 정보가치 소멸 | 자본시장연구원 김준석 2026 (대형주 2013 이후 소멸, 중소형주 잔존) | ✅ |
| 외국인 매도 destabilize 부정 | Choe-Kho-Stulz 1999 *JFE* 54(2):227-264 | ✅ |
| 한국 애널 TP 상승기 후행 | 이승희·주소현·박광수 2013 *Journal of the Korean Data Analysis Society* 15(5) (KDISS 아님) | ✅ |
| Insider opportunistic | Cohen-Malloy-Pomorski 2012 *JoF* 67(3):1009-1043 (월 +82bp VW / +180bp EW, **한국 재현 미확인**) | ✅ (미국) |
| Insider 보조 | Lakonishok-Lee 2001 *RFS* 14(1):79-111 | ⚠️ |
| 8-K abnormal return | Lerman-Livnat 2010 *RAS* 15(4):752-778 (미국 사례, 한국은 DART 공시) | ✅ |
| 단기 reversal (3일 쿨링) | Jegadeesh 1990 *JoF* 45(3) | ✅ |
| Reverse DCF cap | Damodaran *Investment Valuation* Ch.12 "Stable Growth Rate" NYU Stern | ✅ |
| Altman Z'' | Altman 1968/2013 NYU Stern | ✅ |
| Beneish M | Beneish 1999 *FAJ* 55(5) | ✅ |
| Analyst dispersion | Diether-Malloy-Scherbina 2002 *JoF* 57(5):2113-2141 (PDF 게이트 근거) | ✅ |
| 손실회피 | Odean 1998 *JoF* 53(5) | ✅ |
| PEAD 원전 | Bernard-Thomas 1989/1990 JAR/JAE | ✅ |
| Piotroski F-Score | Piotroski 2000 *JAR* 38 | ✅ |
| Faber trend-following | Faber 2007 *Journal of Wealth Management* (한국 재현 미확인) | ⚠️ |
| 자본시장법 §173조의3 | 2024.7.24 시행 (내부자거래 사전공시제) | ✅ |
| 자본시장법 §178 | 분식회계 처벌 | ✅ |
| 거래소 상장규정 §47 | 한국거래소 유가증권시장 (실질심사) | ✅ |
| 거래소 상장규정 §48 | 한국거래소 유가증권시장 (상장폐지 사유) | ✅ |
| 한국 거래세 정정 | 기획재정부 시행령 개정 2025-12-01 (2025 0.15% / 2026 0.20%) | ✅ |
| KRX NSDS 운영 개시 | 2025.3.31 공매도 재개 | ✅ |
| 가격제한폭 ±30% | 2015.6.15 시행 | ✅ |
| 세금 compounding | Munger 1994 USC "Worldly Wisdom" — 미국 환경, 한국 양도세 0이므로 직접 적용 부적합 | ✅ (미국) |
| 한국 양도세 비교 | 국내주식 비과세(대주주 50억+ 제외), 해외주식 22% (지방세 포함) | ✅ |
| Anthropic 가이드 | platform.claude.com/docs/en/build-with-claude/prompt-engineering | ✅ |

---

## 부록. XML 출력 템플릿

매도 판단 결과는 다음 XML 형식으로 출력. 매수 KR_DEEPSEARCH 10-STEP과 일관.

```xml
<sell_decision ticker="" date="">
  <pre_commitment>
    <trigger>매도 검토 트리거 (예: 컨센 TP 90% 도달)</trigger>
    <hold_condition>HOLD 조건 사전 선언</hold_condition>
    <sell_condition>SELL 조건 사전 선언</sell_condition>
    <category>메인 / 가치 / 스윙</category>
    <position_weight>현재 비중 %</position_weight>
    <gain_pct>평단 대비 +X.X%</gain_pct>
  </pre_commitment>

  <thesis_check>
    <original_thesis>당초 매수 근거 3개</original_thesis>
    <validity_3of3>3/3 유효 / 2/3 / 1/3 / 0/3</validity_3of3>
    <dart_disclosures>최근 30일 공시 요약</dart_disclosures>
    <hard_kill_5>감사/자본잠식/zombie/상폐/분식 점검 결과</hard_kill_5>
    <path1_triggered>Y / N</path1_triggered>
  </thesis_check>

  <technical_check>
    <ma50>50MA 위/아래 (이탈 일수)</ma50>
    <ma200>200MA 위/아래 (이격도)</ma200>
    <oneill_8week>활성 / 비활성 (매수일 + 56일 vs 현재일)</oneill_8week>
    <trailing_stop>정점 -15% 가격 (한국 변동성 보정)</trailing_stop>
    <volume_spike>Y / N</volume_spike>
    <path2_triggered>Y / N (8-week 미활성 시만)</path2_triggered>
  </technical_check>

  <pdf_gate>
    <reports_read>증권사명 + 날짜 (최소 2건)</reports_read>
    <tp_change_30d>TP 상향 X건 / 하향 Y건</tp_change_30d>
    <eps_change_30d>EPS 상향 X건 / 하향 Y건</eps_change_30d>
    <risk_new>신규 리스크 항목</risk_new>
    <gate_pass>Y / N</gate_pass>
  </pdf_gate>

  <supply_check>
    <foreign_5d>외국인 5일 누적 (σ)</foreign_5d>
    <institution_5d>기관 5일 누적</institution_5d>
    <credit_balance>신용잔고율 + 5일 변화</credit_balance>
    <lending_balance>대차잔고 + 5일 변화</lending_balance>
    <short_sale>공매도 추이</short_sale>
    <note>단독 매도 트리거 사용 금지 (학술 근거 약함)</note>
  </supply_check>

  <insider_check>
    <buy_cluster_30d>매수 클러스터 (인원/금액)</buy_cluster_30d>
    <sell_cluster_30d>매도 클러스터 (인원/금액)</sell_cluster_30d>
    <pre_disclosure>2024.7.24 시행 사전공시 발생 여부</pre_disclosure>
  </insider_check>

  <three_paths_score>
    <path1>Y / N (Thesis Invalidation)</path1>
    <path2>Y / N (Technical Exit, 스윙만)</path2>
    <path3>Y / N (기회비용, 한국 세제 보정)</path3>
    <total>X / 3</total>
  </three_paths_score>

  <devils_advocate>
    <hold_argument>보유 정당화 논리 (Liang 2023)</hold_argument>
    <simulate_sell>매도 시나리오 시뮬레이션 결과</simulate_sell>
    <simulate_hold>보유 시나리오 시뮬레이션 결과</simulate_hold>
    <verdict>HOLD 회귀 / 매도 유지</verdict>
  </devils_advocate>

  <llm_bias_check>
    <sycophancy>Sharma 2023 통과</sycophancy>
    <flipflop>Laban 2023 통과</flipflop>
    <self_correction>Huang 2023 통과 (외부 데이터 grounding)</self_correction>
    <bull_market>Li 2025 통과 (조기매도 편향 4중 체크)</bull_market>
  </llm_bias_check>

  <final_action>
    <decision>HOLD / 부분 매도 X% / 전량 매도</decision>
    <rationale>3경로 X/3 발동 + Devil's Advocate 결과</rationale>
    <next_review>다음 재검토 시점 (예: 8주 경과 후)</next_review>
  </final_action>

  <records>
    <set_alert_decision>판단 근거 기록 (set_alert log_type=decision)</set_alert_decision>
    <set_alert_trade>매매 실행 시 (set_alert log_type=trade)</set_alert_trade>
    <write_file_thesis>data/thesis/[티커]_[종목명].md 업데이트</write_file_thesis>
  </records>
</sell_decision>
```

---

## 변경 이력

- **2026-04-25 v1**: 신규 작성. KR_DEEPSEARCH v4와 일관 (3-Gate / 비중 3단계 / 카테고리). 한국 PEAD 20일 잔존·한국 매수 93.1%·컨센 TP 정보가치 소멸·LLM 4중 편향 반영. 미국 도구(get_us_ratings, SEC EDGAR 웹) 사용 금지 명시. 학술 근거 약한 항목은 [실무 관행] / [학술 근거 약함] / [검증 불가] / [데이터 불충분] 4단계 라벨 분리.

**핵심 정정 사항** (사용자 KR_DEEPSEARCH 매수 프레임 대비):
1. 거래세 0.18% → **2025년 0.15% / 2026년 0.20% (현재 적용)** (기획재정부 2025-12-01 시행령 개정)
2. 이승희 외 2013 학회명: KDISS → **KDAS** (Korean Data Analysis Society)
3. 외국인 5일 연속 매도 트리거 약화: Choe-Kho-Stulz 1999가 destabilize 가설 부정 → 단독 트리거 금지
4. Munger compounding 한국 재구성: 양도세 0이므로 거래세+슬리피지+심리비용으로
5. 컨센 TP 90% 도달: 대형주(삼성/하이닉스)에서는 단독 트리거 금지, 중소형주만

**최종 원칙**: 3경로 0/3 + 8-week hold 활성 + Devil's Advocate 통과 → **HOLD가 유일한 정답**

LLM이 "충분히 올랐으니 익절"만으로 매도 권유 시 → 규칙 1 (Sycophancy) + 규칙 5 (Intrinsic reflection) + 규칙 6 (Bull-market 조기매도) 합성 편향. 즉시 차단.
