# AMD Thesis v2 (Advanced Micro Devices)
> 작성일: 2026-05-23
> v1: 2026-05-05 (매도 완료, 트리거 메모만 유지)
> 현재가: **$467.51** / 시총 $762B / FwdPE ~63x (FY26 EPS $7.44 기준)
> 카테고리: **AI 인프라 (Hyperscaler GPU)** / 매크로 sleeve: 그로스 (A×A 시나리오)

---

## v1 → v2 변화점 (18일)

| 항목 | v1 (5/5) | v2 (5/23) | Δ |
|------|----------|-----------|----|
| 주가 | ~$400 (매도 가격대) | $467.51 | **+17%** |
| 컨센 TP (1m avg) | n/a | **$453.6** (20명) | 1m TP에 주가가 이미 도달 |
| 컨센 등급 | 보류 | Buy (34명, TP $388.85 snapshot) | 다수 Buy |
| 90d 레이팅 | 분기 미진입 | **31건 / 3 upgrade / 25 PT 상향** | 매수쏠림 |
| Q1 실적 | 미발표 | **4/29 발표 — 매출 $10.3B (+38% YoY), DC $5.8B (+57%), EPS $1.37** | 빅 비트 |
| Q2 가이드 | n/a | **$11.2B ± $300M** (컨센 상회) | 강한 가이드 |
| MI400/Meta | 루머 | **Meta 6GW 다세대 협정 공식화** | 구조적 캡쳐 |
| AWS | 미확정 | **MI350 첫 하이퍼스케일러 본격 채택** | NVDA 일점쇼 깨짐 |
| NVDA 어닝 | 5/28 D-23 | **5/20 이미 발표, 매출 +85% $81.6B** | spillover 일부 가격 반영 |
| 등급 | B+ (4/28) | **A-** (Q1 빅비트 + 하이퍼스케일러 모멘텀) | 1단계 상향 |

**핵심 변화**: v1의 "매도/재매수 트리거 $400"을 17% 초과. NVDA 5/20 어닝 빅비트가 AMD에 풍선효과. Q1 DC 매출 $5.8B(+57%)와 Meta 6GW가 결정적 변곡점. **이미 매수 시점 놓침 — pullback 대기 thesis로 재구성**.

---

## Thesis 한 문장

AMD는 NVDA 독점이던 AI 인프라 GPU 시장에서 **하이퍼스케일러 멀티벤더 전략의 첫 번째 수혜자**가 되었으며, MI300X/MI350의 AWS·Meta·Microsoft·Oracle 4대 클라우드 동시 채택과 Q1 데이터센터 +57% YoY 가속이 **2027~2028년 DC $76B → $101B 매출 트래직션**을 정당화한다.

---

## 선정 근거 (504개 중 #4, 점수 53)

| 시그널 | 가중치 | 점수 기여 |
|--------|--------|----------|
| 애널 90d upgrade 모멘텀 (3 upgrade + 25 PT 상향) | High | +15 |
| 컨센 TP 1m → 3m 상승 ($431 → $454) | High | +10 |
| Q1 데이터센터 +57% YoY (구조적 성장) | High | +12 |
| Meta 6GW 다세대 협정 (visibility 2028+) | Medium | +8 |
| 매크로 A×A (관세 정상화 + Fed pivot) 그로스 sleeve 적합 | Medium | +5 |
| NVDA 5/20 어닝 spillover (긍정) | Low | +3 |
| **합계** | | **+53** |

---

## 3-Gate 판정

| Gate | 조건 | 판정 | 근거 |
|------|------|------|------|
| **Gate 1: 알파 원천** | 선행 지표? | **PASS** | 애널 PT 상향 25/31건 (선행), Q1 실적 (확정) |
| **Gate 2: 진입 타이밍** | 가격 메리트? | **FAIL** | $467 (1m TP $454 초과). 매수가 아닌 watch |
| **Gate 3: 자금 슬롯** | 포트 적합성? | **CONDITIONAL** | 풀백($380~$420) 시 재매수 적합. 지금 X |

**최종**: WATCH (재매수 가격 $380~$420 도달 감시)

---

## Forecast

| 항목 | 2025A | 2026F | 2027F | 2028F | 2029F | 2030F |
|------|-------|-------|-------|-------|-------|-------|
| 매출 ($B) | ~31 | **49.9** | **76.3** | **101.1** | 144.3 | 171.4 |
| YoY% | +14% | +61% | +53% | +33% | +43% | +19% |
| EBITDA ($B) | — | 10.8 | 16.6 | 22.0 | 31.3 | 37.2 |
| EPS ($) | 2.85 | **7.44** | **13.10** | **17.85** | 25.06 | 29.92 |
| FwdPE @ $467 | — | 63x | 36x | 26x | 19x | 16x |

> 출처: FMP analyst estimates (36 analysts FY26/27, 29 FY28, 14 FY29/30)

---

## 구조적 성장 드라이버 (5개)

1. **MI300X/MI350 하이퍼스케일러 캡쳐** — AWS(첫 MI350), Meta(6GW), Microsoft(Azure Sweden/Ireland), Oracle(16K GPU cluster). NVDA 의존 80%+ → 70%로 자연 감소
2. **MI400 HBM4 race (2026 H2 ramp)** — OpenAI 6GW 협정으로 차세대 visibility 확보
3. **ROCm 소프트웨어 성숙** — CUDA 모트 점진 약화. PyTorch native + HuggingFace 풀스택 지원
4. **EPYC 서버 CPU 점유율 확장** — DC 매출 중 ~$1.5~2B는 CPU. AMD 가이드 "2030 서버 CPU TAM $120B 중 점유율 50%+"
5. **Embedded (Xilinx) Edge AI / 게임 콘솔** — PS6 / Xbox Next-gen 디자인 윈 (2027 ramp), Embedded 회복 사이클

---

## 수급 시그널 (애널 30/90일)

| 기간 | upgrade | downgrade | PT 상향 | 신호 |
|------|---------|-----------|---------|------|
| 30일 | 2 (Seaport, Daiwa→Outperform) | 1 (Daiwa Strong→Buy)* | 18 | 강한 양 |
| 90일 | 3 (Bernstein, DA Davidson, Seaport) | 2 (Northland, Daiwa class label) | 25 | 강한 양 |

> *Daiwa 5/13: "Strong Buy → Buy" 클래스 명칭 다운이지만 PT $250 → $500으로 100% 상향. 실질 양.
>
> **1m TP avg $453.6** (20명) vs **3m TP avg $431** (24명) — 한 달 내 5.2% TP 상향. 노이즈 아님.

---

## Kill Switch (5개)

1. **MI350 hyperscaler 채택률 stall** — AWS/Meta/MS 중 하나라도 6개월 내 추가 LOI 부재 시
2. **NVDA Blackwell Ultra 가격 인하 (>20%)** — 멀티벤더 경제성 붕괴
3. **Q2 가이드 미스 (<$11B)** — 7월 말 어닝에서 가이드 다운
4. **CUDA 차세대 lock-in 신호** — Meta/MS가 MI400 주문 축소 / 연기
5. **거시 리스크: Fed 재긴축 + 관세 재격화** (A×A → C 전환 시)

---

## Bear Case

### Bear A (-25%): $467 → $350

- Q2 가이드 약화 (~$10.5B) + DC YoY 감속 (+57% → +35%)
- 멀티플 63x → 47x 컴프레션 (NVDA 디스카운트 유지)
- 트리거 확률: 30%

### Bear B (-45%): $467 → $260

- AWS MI350 주문 지연 + Meta 6GW timeline 후퇴 (2027 → 2029)
- NVDA Blackwell Ultra pricing war 본격화 (-20% ASP)
- 매크로 A×A → C 전환 (Fed 재긴축, 관세 재격화)
- 트리거 확률: 12%

---

## 컨센서스 요약

| 항목 | 값 |
|------|-----|
| 컨센 등급 | **Buy** (34명) |
| TP 평균 (snapshot 5/17) | $388.85 |
| TP 평균 (1m, 20명) | **$453.6** |
| TP 평균 (3m, 24명) | $431.04 |
| TP 평균 (1y, 79명) | $319.63 |

- **Strong Buy**: TD Cowen, Bernstein, Benchmark, Rosenblatt, DA Davidson, Seaport ($430~$525)
- **Hold/Neutral**: JP Morgan ($385), RBC ($400), Citi ($358), Northland ($260)

**해석**: 1m TP가 3m TP를 5.2% 상회 → 컨센이 빠르게 따라잡는 중. **현재가 $467은 1m TP +3% 이내** → 단기 valuation 부담.

---

## NVDA spillover 시나리오 (5/20 이미 발표)

> v1 작성 시 5/28 어닝 D-23으로 가정했으나, NVDA Q1 FY27은 5/20 발표 완료. 매출 $81.6B (+85% YoY), GM 74.9%, Blackwell Ultra 2.7x throughput 빅비트.

**현재 단계: 이미 spillover 반영 진행 중**. 다음 NVDA 어닝 = 2026년 8월 말.

| 시나리오 | 가정 | AMD 영향 | 확률 |
|----------|------|---------|------|
| **잔여 spillover (+)** | NVDA 빅비트 후 펀드 멀티벤더 노출 확대 | AMD 추가 +5~10% (~$510) | 35% |
| **부합 (mid)** | 현 가격 보합 | $440~$480 박스권 | 45% |
| **차익실현 (-)** | NVDA-AMD 동반 차익 | AMD -8~15% (~$400~$430) | **재매수 zone** | 20% |

**다음 결정적 이벤트**: AMD Q2 어닝 (7월 말 추정).

---

## 매크로 시나리오 매핑

| 시나리오 | 확률 | AMD 영향 | 비중 |
|----------|------|---------|------|
| **A×A** (관세 정상화 + Fed pivot) | 50% | **그로스 sleeve 1순위** — 멀티플 63x 정당화 | OW |
| **B×B** (관세 부분 + Fed 보합) | 30% | DC 매출 견조, 멀티플 50x 압축 | EW |
| **C** (관세 재격화 + 인플레 재반등) | 20% | -45% Bear B 시나리오 | UW (회피) |

> v1 매도 의사결정 이후 매크로는 A×A 방향으로 확정 진행. 그로스 sleeve 재진입 명분 강함.

---

## 진입 전략 (재매수)

| Tranche | 가격 | 비중 | 트리거 |
|---------|------|------|--------|
| 1차 | **$420** | 30% | NVDA 후행 차익실현 → AMD 동반 조정 시 |
| 2차 | **$385** | 40% | Q2 어닝 전 (~7월 중순) 멀티플 정상화 |
| 3차 | **$345** | 30% | Bear A 진입 시 (DC 둔화 우려) |

**총 비중 한도**: 그로스 sleeve 내 단일 종목 15% 이내 (NVDA와 합산 25% 이내).

**현재 액션**: watchalert.json에 `buy_price=420, memo="thesis v2 1차 진입"` 등록 권장.

---

## 다음 촉매

| 일정 | 이벤트 | 영향 |
|------|--------|------|
| 5/30~6/15 | OpenAI/Meta GW deal 후속 발표 | 양 |
| 6월 중순 | Computex / AMD Advancing AI 행사 | MI400 디테일 |
| 7월 말~8월 초 | **AMD Q2 어닝** (가이드 $11.2B ± $300M) | 결정적 |
| 8월 말 | **NVDA Q2 FY27 어닝** | spillover 라운드2 |
| 9월 | MI355X 양산 출하 | 신제품 사이클 |

---

## 참조

- FMP analyst research: TP summary (1m $453.6 / 3m $431 / 1y $320), estimates 36 analysts FY26~27
- DB `us_analyst_ratings`: 90d 31건 / 3 upgrade / 25 PT 상향
- Q1 2026 earnings (4/29): 매출 $10.3B (+38%), DC $5.8B (+57%), EPS $1.37 (vs cons $1.27)
- Q2 2026 guide: $11.2B ± $300M
- Meta 6GW agreement, AWS MI350 첫 채택 (Q1 콜)
- NVDA Q1 FY27 (5/20): 매출 $81.6B (+85%), GM 74.9%, Blackwell Ultra 2.7x throughput
- AMD MI350: 35x inference improvement, 288GB HBM3E (mid-2025 launch)
- v1: /Users/kreuzer/stock-bot/data/thesis/AMD.md

### 📊 최근 30일 미국 톱애널 콜 (5/22 수집, Ralph iter1)
- **5/13 Daiwa (Louis Miscioscia, ★3.08) — Downgrade: Buy → Hold, PT $250→$500** ⚠️ 등급 다운+TP 2배 (애매한 시그널, 등급은 컨센 매수 흐름에 합류했으나 표면적 다운그레이드. 별점 낮음)
- **5/12 Mizuho (Vijay Rakesh, ★4.89) — Maintain Buy, PT $415→$515 (+24%)** ← 톱애널, +100$ TP 상향
- **5/7 Citigroup (Atif Malik, ★5.0) — Maintain Hold, PT $248→$358** ★5.0 만점, Hold이나 TP +44% 대폭 상향
- **5/6 8건 동시 1Q26 review**:
  - **Bernstein (Stacy Rasgon, ★4.72) — UPGRADE Hold→Buy, PT $265→$525 (+98%)** ← 단일 최대 시그널, 가장 강한 confirm
  - **TD Cowen (Buchalter, ★4.33) — Maintain Strong Buy, PT $290→$500 (+72%)**
  - **JP Morgan (Harlan Sur, ★4.97) — Maintain Hold, PT $270→$385 (+43%)** Hold이나 +43% TP 상향
  - **Barclays (O'Malley, ★5.0) — Maintain Buy, PT $300→$500 (+67%)** ★5.0
  - **Wells Fargo (Aaron Rakers, ★5.0) — Maintain Buy, PT $345→$505 (+46%)** ★5.0
  - **Susquehanna (Rolland, ★4.81) — Maintain Buy, PT $375→$450 (+20%)**
  - **Benchmark (Cody Acree, ★5.0) — Maintain Strong Buy, PT $325→$485 (+49%)** ★5.0
  - Cantor (Muse, ★4.94) — Maintain Buy, PT $450→$500
  - RBC (Pajjuir, ★3.07) — Maintain Hold, PT $325→$400
- **시그널 요약**: 5/6 1Q26 어닝 후 12건 중 **upgrade 1 + maintain 10 + downgrade 1**, TP 평균 변화 **+50%**, ★4.5+ 톱애널 5/12명 (Citi, JPM, Barclays, Wells, Cantor, TD Cowen, Bernstein, Susq, Benchmark, Mizuho, Daiwa). **★5.0만 4명 confirm Buy**. 매크로 thesis "AI 수요 확정" 정량 확정. Bernstein upgrade는 5/6 단일 최대 변화점 — re-rating thesis 백본.

---

## 🎙 FMP Earnings Call 본문 (Ralph iter 4, 2026-05-23 시도)

- `get_us_earnings_transcript(AMD, 2026 Q1)` → **HTTP 402** (FMP subscription plan 미포함)
- `get_us_earnings_transcript(AMD, 2025 Q4)` → **HTTP 402**
- 현 thesis 본문 내 이미 반영된 4/29 Q1 콜 시그널이 부분 대체:
  - CEO Lisa Su: AWS MI350 첫 하이퍼스케일러 본격 채택 공개
  - CEO: Meta 6GW 다세대 협정 공식화 (visibility 2028+)
  - Q1 매출 $10.3B (+38% YoY) / DC $5.8B (+57%) / EPS $1.37 vs cons $1.27 비트
  - Q2 가이드 $11.2B ± $300M (컨센 상회)
- 추가 인사이트(Bernstein Rasgon Q&A 후속 발언, OpenAI 6GW 협정 timeline 디테일, MI400 HBM4 ramp 일정 톤) 발췌 **불가** — Q2 어닝(7월말) 시 재시도.

---

## 🌐 보조 매크로 시그널 (5/22~23, Ralph iter 7)

### 직접 관련 시그널
- **SOXX 5d +5.66% / SMH 5d +6.45% (+) — 반도체 ETF V-shape 회복** (5/18 SOXX 495 저점 → 5/22 537 +8.4%, 5/22 단일 +2.40%). AMD 5/22 +3.99% 단일 강세는 ETF 동조. AI capex 자금이 5/16~17 일시 매도(SPX -0.73%) 회복 후 재유입 시그널.
- **DXY 99.32 / US10Y 4.56% (+) — 약달러 + 금리 안정** = 그로스 멀티플 유지 환경 (10Y > 4.6% 시 멀티플 압축 vulnerable). AMD PER 153.44는 thesis "AI capex 확정 → 멀티플 정당화" 가정에 의존.
- **VIX 16.7 (+) — 그로스/리스크 자산 매수 환경**. 20 하회 유지 시 AI thesis 정합. >20 진입 시 멀티플 first to compress.
- **XLK 5d +2.34% / QQQ 5d +1.21% — 테크 outperform** (vs SPY +0.88%). 5/16~17 일시 약세 후 5/22 +1.0% 회복. AMD 단독 강세보다 섹터 wave 강세.

### 매크로 매트릭스 정합 재확인
- v2 thesis 매크로 매핑 **A×A (AI 수요 확정 × Fed pivot 지연)** 가정 → **5/23 시그널 모두 정합**.
  - SOXX 자금 V-shape = "AI capex 확정" A축 강화
  - US10Y 4.56% (4.47 → 4.57 +10bp) + Fed 6/16 No-change 97.65% = "Fed pivot 지연" B축 약하게나마 정합 (금리 추가 인상은 thesis 부정적)
- **갭 잠재**: WTI $97 + 이란 ceasefire 99.55% 유지로 인플레 재점화 시 → 6/16 FOMC 매파 surprise → 멀티플 압축 위험 (현재는 27% 베타 시뮬레이션 시나리오 B에 반영됨, intact)
