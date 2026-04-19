# Astera Labs (ALAB) — 풀 딥서치 Thesis (v2, 리포트 원문 보강)

**딥서치 완료일**: 2026-04-19 (v2: 2026-04-19 리포트 원문 5종 완독)
**현재가**: $174.05
**확신등급**: **B+** (머스트 3/3 Pass, 3대 질문 2.5/3) — v2에서 유지
**감시가**: $130 (RR ≈ 1:2.7)
**손절**: $110 | **목표**: $205 (FY28E EPS × 45x 멀티플)
**레짐**: 🟢 탐욕 48일차 → B+ 신규 금지, 감시가 대기

---

## ■ 투자 테제

하이퍼스케일러 AI 인프라에서 PCIe Gen 6 리타이머·Scorpio 팹릭 스위치·CXL 메모리 컨트롤러·Ethernet SCM을 공급하는 first-mover 팹리스. 향후 3년간 매출 CAGR ~40%+ 가시성 (FY25 $852M → FY28E $2.41B).

**핵심 통찰 (v2 보강):** 시장이 ALAB을 "single-customer PCIe retimer 사업"으로 오인. 실제로는 하이퍼스케일러 custom NIC 생태계의 필수 연결성 공급자. Amazon Trainium 2/3/4, AMD Helios, AWS 커스텀 설계 다세대 공급. Scorpio X 2026 H2 ramp + UAL switch 2027 Trainium 4 = 새 product cycle 가시성.

## ■ 3대 핵심 질문 (v2 재검토)

### ① 해자 (2년 방어?): Weak Yes → **Yes 근접** (상향 조건부)

**Moat 강화 증거 (리포트 원문):**
- Scorpio P-Series는 custom NIC 사용 하이퍼스케일러의 **필수 PCIe 스위치** (SemiAnalysis 2024/7): "Since there will be no ConnectX-7/8 or Bluefield-3, which both have integrated PCIe switches, a **dedicated PCIe switch from Broadcom / Astera Labs will be required** to connect the backend NICs to both the CPU and GPU"
- **Amazon Trainium 2에 large amounts of Astera Labs retimer content** (SemiAnalysis 명시)
- **리타이머-to-GPU 비율 1.3-1.5x 고정 attach rate** (Citi Malik): AI 시스템 80%+ 사용
- Aries 6 업계 최저 전력 11W (PCIe 6.0 16-Lane, 경쟁사 13W+)
- COSMOS 소프트웨어 스택 (protocol 통합 lock-in)
- Cloud-Scale Interop Lab (50+ endpoint 검증)
- Amazon 워런트 3.26M주 @ $142.82, **$6.5B 누적 구매 2033년까지** = long-term commercial lock-in

**Moat 불완전 증거:**
- ROIC 6.08% vs WACC ~8% → **여전히 ROIC < WACC** (A등급 요건 미충족)
- CSP 커스텀 ASIC PCB 재설계 시 **retimer de-specification 가능** (LumenAlpha 2025/7)
- NVIDIA 자체 1.6T DSP 테이프아웃 완료 (단기 ramp 불가하나 2027+ 위협)
- 경쟁: MRVL Alaska P (2024/5), CRDO Toucan (2024/10), AVGO PCIe Gen 6 풀 포트폴리오 - Meta/Google ASIC 파트너

**판정:** 2년 방어 가능 (Amazon 워런트+Trainium 3 확정 공급). 3-5년은 불확실. **Weak Yes 유지**

### ② 구조적 수요: Strong Yes (v1 동일)

- FY25 매출 +115% YoY vs 반도체 산업 ~15-20%
- TAM $25B by 2030 (10x 확대). Scale-up switching 단독 $10-20B
- CXL 채택 2024 10% → 2025 50% in AI racks (Gartner: ALAB 60% 리더)
- 하이퍼스케일러 2026 CapEx $660-690B (Google+AWS만 $400B)
- Scorpio X 2026 H2 volume ramp (10+ customers). UAL switch 2027 Trainium 4+AMD MI500

### ③ 이익 상향: Yes (v1 동일)

- Q4/25 Beat: 매출 +8.5%, EPS +13.7%
- Q1/26 가이던스 $286-297M vs $259M 컨센 (+11~15%)
- FY26E EPS $2.48 (+103%), FY27E $3.51 (+41%), FY28E $4.56 (+30%)
- Jefferies 2025/7: 2027 EPS $2.32 → $2.81 상향 (Trainium 3/4 근거)
- 최근 컨센 revision: Loop $250 신규 (3/5), Citi 복잡 (4/2 $200 유지 또는 재상향)

**→ 2.5/3 → B+ 확정 유지**

## ■ 재무건전성 (v1 유지)

| 지표 | 값 | 판정 |
|------|-----|------|
| Altman Z-Score | 74-83 | ✅ 압도적 |
| D/E | 0.02 | ✅ |
| ICR | N/A (무부채) | ✅ |
| FCF | $281.8M (+175%) | ✅ |
| Current Ratio | 12.78 | ✅ |
| SBC/매출 | ~55% TTM | ❌ IPO 일회성, Q3 -36% YoY 정상화 중 |
| 현금 | $1.19B | ✅ |

## ■ 밸류에이션

- PEG 게이트 <2.0: FY26 1.16 / FY27 1.32 / TTM 1.93 → Pass
- FwdPE: FY26 70x / FY27 49x / FY28 38x
- **2년후 적정가**: FY28E EPS $4.56 × 45x = $205 (+18%, 20% 할인 미달)
- TIKR mid-case $408.87 by 2030 (30.5% CAGR, 36.9% net margin) - 장기 upside
- 애널 평균 $210 (최근 3개월 22명, 중앙값 $210)

## ■ 고객 집중도 (중요 재해석)

- **FY25 10-K: 엔드고객 Top 1 >70%, Top 3 = 86%**
- 직접고객: A 20%, B 20%, C 17%, D 16%, E 11% (Top 5 = 84%)
- **추정 Top 1 = Amazon** (SemiAnalysis: Trainium 2 대규모 retimer + 워런트 $6.5B 2033년까지)
- Amazon이 Top 1이라면 **single-point-of-failure 완화** (워런트로 법적 구속)
- 2026년 2개 US 하이퍼스케일러 Scorpio P 추가 예정 (Microsoft, Meta 추정)
- Customer F 2023 24% → 2024 36% → 2025 <10% 급감 전례 (Meta 추정, Broadcom 전환) ← 리스크 실재

## ■ 제품 구성 (FY25)

| 제품 | 비중/성장 | 상태 |
|------|----------|------|
| Aries PCIe Retimer | +70% YoY | 레거시 핵심, 1.3-1.5x/GPU attach |
| Taurus Ethernet SCM | +4x YoY | 400G → 800G 전환 |
| Scorpio P (PCIe Fabric) | **15% 매출 ($128M)** | Volume ramp 중, 2개 US CSP 추가 |
| Scorpio X (Scale-up) | Pre-production | 2026 H2 volume (Trainium 3) |
| Leo CXL Controller | 초기 | Microsoft Azure M-series 첫 공식 배포 |
| UAL Switch | 개발 중 | **2027 Trainium 4 + AMD MI500** |
| 광학 엔진 | 2028 로드맵 | aiXscale Photonics 인수 |
| PCIe 6.0 매출 | Q3/25 20%+ | 빠른 전환 |

## ■ 경쟁 구도

- **MRVL**: Alaska P PCIe Gen 6 (2024/5). ALAB IPO 전 인수 제안했다가 거부당한 역사
- **CRDO**: Toucan PCIe 6/CXL 3.x (2024/10). AEC (Active Electrical Cable) 강점. Forward P/S 44.68x vs MRVL 26.27x vs AVGO 23.03x → ALAB 최고 프리미엄
- **AVGO**: PCIe Gen 6 풀 포트폴리오, Meta/Google ASIC 파트너, Tomahawk 6 (102.4 Tbps) Ethernet 지배
- **ALAB 차별화**: 11W 최저전력 Aries 6, Cloud-Scale Interop Lab, COSMOS SW, Scorpio X 선점

## ■ 수급·내부자

- 기관 보유 72.6% (상승)
- **Short Float 6.60%** (5% 경고선 초과 ⚠️)
- **CEO 5개월 누적 매도 $76M+** (Rule 10b5-1 plan 12/1/25 adopted)
- COO·CFO·GC 2/22/26 동시 클러스터 ($14M+)
- Director Alba 12/2025 $24.9M
- Insider 매수: 0건
- 남은 CEO 보유: 5.86M주 (~$1.02B at $174) → 완전 엑시트 아님

## ■ 주요 리스크

1. **Top 1 엔드고객(추정 Amazon) 70% 집중** — Trainium 배포 속도 변동 직접 영향. 워런트로 일부 완화
2. **CSP 커스텀 ASIC PCB 재설계로 retimer de-specification** (LumenAlpha 장기 위협)
3. **NVIDIA 1.6T DSP 자체 개발** (2027+ 위협)
4. **Scorpio X 마진 압박** (TIKR 모델 36.9% net margin 가정의 취약점)
5. **프리미엄 밸류 + CEO 매도 클러스터** (FwdPE 70x, P/S 44x)
6. **Amazon 워런트 -2pp GM 헤드윈드** (Q2/26부터 비현금)

## ■ Thesis 무효화 조건 (A→D/C 하향 트리거)

1. Top 1 엔드고객 분기 기여도 **>10pp 급락** (Customer F 전례)
2. Non-GAAP GM **75% 하회 3분기 연속** (Amazon warrant 2pp 제외)
3. **Scorpio X 2026 H2 volume ramp 공식 연기**
4. **Trainium 3 공급 중단 or 2개 CSP 추가 디자인 윈 취소** (신규 보강)
5. NVIDIA 자체 DSP ramp 공식 성공 (2027+)

## ■ 매도 조건

1. 목표가 $205 도달 → 50% 축소
2. 무효화 조건 중 1개 이상 → 즉시 재평가
3. FwdPE 100x 초과 → 과열, 전량 재평가

## ■ 감시가 체계

| 가격대 | 수식어 | 액션 |
|--------|--------|------|
| $130 이하 | Ready | B+ 1차 트랜치 (🟢 해제 조건부) |
| $130-150 | Extended | 50% 축소 |
| **$150-180** | **Stretched** | **매수 보류 (현재 $174)** |
| $180+ | — | 매수 금지 |

## ■ 다음 촉매

- **2026-05-12 Q1/26 어닝** (Scorpio X 초기 volume, Amazon 워런트 vesting)
- **Q2/26 실적**: TIKR "첫 confirmation point" = Scorpio X 마진 확인
- 2027 컨센 EPS revision 방향
- Trainium 3 ramp 데이터 (Jefferies 핵심 촉매)
- Trainium 4 플랫폼 발표 (2027 UAL switch 탑재)

## ■ Bear Case 반론

1. **"PCIe retimer 시장은 NVLink/Optical 전환으로 구조적 하락"** — 일부 타당하나 SemiAnalysis가 반박: 35% short float가 피상적 이해에서 나옴. 커스텀 NIC 시장은 여전히 PCIe 스위치/리타이머 필요. Scorpio X는 scale-up fabric 신시장
2. **"Top 1 엔드고객 70% = Single Point of Failure"** — 매우 타당. 추정 Amazon이면 $6.5B 워런트로 법적 구속, 다세대 공급 (Trainium 2/3/4). 2026년 3개 CSP 다변화 진행

## ■ Claude 편향 체크 (v2)

MS Top Pick + Strong Buy + 리포트 원문 5종의 긍정적 세부사항(SemiAnalysis, TIKR $408 target, Jefferies Trainium 3/4, Citi 80% attach rate, Morgan Stanley Top Pick)에 반사적 A등급 상향 유혹 있었으나:
- **판정 번복 금지 규율** 적용 (새 1차 데이터 아님, 기존 컨센 재구성 중심)
- ROIC < WACC는 구조적 → A 조건 미충족
- 리포트 원문이 기존 B+ 판정의 근거를 강화할 뿐 번복 아님
- TIKR $408 target은 5년 CAGR 가정, 2년 기준 $205 목표 변경 없음
- 감시가 $130 유지, 현재가 $174 Stretched 매수 보류 동일

**→ 등급/감시가/액션 모두 v1 유지. v2는 근거 보강만 수행**

---

**소스 (v2 원문 완독):**
- SEC EDGAR: 10-K FY25, 10-Q Q1·Q2·Q3/25, 8-K Q4/25 (Form 4 전수)
- **SemiAnalysis**: GB200 Hardware Architecture (2024/7, 유료 헤드 섹션)
- **LumenAlpha Substack**: B300/CX-8 retimer 분석 (2025/7)
- **TIKR blog**: 224% bull case $408 mid-case (2026/3)
- **Morgan Stanley**: 2026 Top Chip Pick (Joseph Moore, 12/2025) - TipRanks 인용 + 3/3 TMT 컨퍼런스 트랜스크립트 요약
- **Jefferies**: Blayne Curtis $95→$130 Trainium 3/4 논거 (2025/7)
- **Citi**: Atif Malik 리타이머-to-GPU 1.3-1.5x attach rate
- **Needham**: N. Quinn Bolton $220
- **Stifel**: Tore Svanberg $200
- **Loop Capital**: Ananda Baruah $250 신규 (2026/3/5)
- stockanalysis.com (25 애널 EPS forecast)
- Benzinga (22 애널 ratings, $181.52 평균)
- TipRanks/MarketBeat/WallStreetZen 컨센 교차검증
