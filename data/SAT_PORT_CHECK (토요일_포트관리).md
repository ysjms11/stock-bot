# SAT_PORT_CHECK (토요일 포트폴리오 관리)

> **방어 모드**
> 디폴트 HOLD — 점검 = 트리거 발견까지만
> 결정-실행 분리 — 토요일 결정, 월요일 VWAP 실행

---

## 핵심 원칙 5

1. 디폴트 HOLD — 점검 = 트리거 발견까지만
2. 결정과 실행 분리 — 토요일 결정 작성, 월요일 시초가 ±30분 또는 VWAP 실행. 호가창 보면서 결정 변경 금지
3. Core cooling 3거래일 — Thesis-breaking 악재 외엔 즉시 매도 금지
4. 스윙은 신속 손절 — -7~10% 또는 -2×ATR, 쿨링 없음
5. 35% 초과 = 즉시 조정, 단일 25% Hard cap

---

## Phase 1: 시스템·매크로 임계 점검 (5~7분)

- [ ] `get_regime` → 레짐·VIX·트랜치 단계
- [ ] `get_portfolio` → 현금 비중, 35% 초과 종목, NAV 주간 변동
- [ ] `get_alerts` → triggered 손절·목표·매수감시
- [ ] `get_macro` → KR/US 통합
- [ ] `get_macro_external` → **외부 시그널** (Polymarket TOP 8 + Fed decision + 10Y-2Y 침체)

**매크로 8변수 임계값 돌파 체크:**

| 변수 | 임계값 | 상태 |
|---|---|---|
| VIX | 20↑ / 30↑ / 40↑ | __ |
| USDKRW | ±2% | __ |
| US10Y | ±20bp | __ |
| WTI | ±5% | __ |
| DXY | ±1% | __ |
| KOSPI/SPY 60일 MA | 이탈 | __ |
| **Fed 인하 확률 (Polymarket)** | **±10pp 1주** | __ |
| **10Y-2Y 스프레드** | **역전 / 0.25 미만** | __ |

→ 2개 이상 돌파 시 Phase 5에서 카테고리별 대응 차등 적용
→ Polymarket 변동 큰 시장 (이란·관세·AI 규제) 별도 점검

---

## Phase 2: 종목 루프 (보유 전체, 종목당 60~90초) ★

> **이게 본진. 디폴트 HOLD를 강제하는 최소 점검.**

- [ ] `get_alpha_metrics(tickers="보유 전체")` (batch) → F-Score 변화
- [ ] `get_news(tickers="보유 전체")` (batch) → thesis 영향 헤드라인
- [ ] (KR) `get_supply` → 5일 외인+기관 net, 거래원 외국계 매도
- [ ] (KR) `get_pension_flow(held_watch_only=true)` → **연기금 양방향** (NPS 60~80% 비중, 너 포트 매수/매도 직격)
- [ ] (US) `get_us_ratings` → 다운그레이드

**종목별 매트릭스 (각 60~90초):**

| 종목 | 카테고리 | 현재가/손절% | Pillar 신호등 | Kill 위반 | 어닝 D- | 비중% | Action |
|---|---|---|---|---|---|---|---|
| 보유1 | Core/방어/스윙 | __ | P1🟢 P2🟡 P3🟢 | N | D-15 | 12% | HOLD |

**Pillar 정의 (3-Gate 그대로):**
- P1 = 산업흐름 (Gate 1)
- P2 = 리더 (Gate 2)
- P3 = 정량 근거 (Gate 3)

**Action 결정 규칙:**
- 1개 🔴 → **WATCH** (감시만, 다음주 재점검)
- 2개 🔴 → **DEEP DIVE 큐** (일요일 또는 평일 1차 자료 정독)
- 3개 🔴 또는 핵심 단일 pillar 🔴 → **TRADE** (Phase 5로)
- 디폴트 → **HOLD**

---

## Phase 3: 4 Sell Triggers 점검 (5~10분)

| Trigger | 점검 도구 | 카테고리별 대응 |
|---|---|---|
| ① Thesis 훼손 | Pillar 🔴 핵심 + 뉴스 | Core: 3거래일 쿨링 후 / 스윙: 즉시 |
| ② 리더십 상실 | 직전+직전직전 분기 어닝 미스+가이던스 하향 | Core: TRIM 1/3 → EXIT / 방어: TRIM |
| ③ 밸류 극단+유포리아 | PER/PBR + 3주 30%+ 급등 + 컨센 연속 상향 | TRIM 1/3~1/2 우선 (전량 X) |
| ④ 구조 위반 | 35% 초과 + Core가 3-Gate 탈락 | 즉시 비중 조정 |

**Drawdown 단계별 프로토콜:**

| 종목 DD | Thesis 멀쩡 | Thesis 약화 | Thesis 무너짐 |
|---|---|---|---|
| -10% | 무대응 | 재검토 | TRIM 1/3 |
| -20% | 트랜치 1차 추매 | TRIM 1/3 | 전량 매도 |
| -30% | 2차 추매 (25% cap) | TRIM 1/2 | (이미 매도) |
| -50% | 추매 중단·시그널 의심 | 전량 매도 | — |

---

## Phase 4: 집중도 + 의도하지 않은 노출 (5분)

- [ ] `get_portfolio_history` → 비중 drift
- [ ] **Top 1 / Top 3 / Top 5 비중**
  - Soft cap 15% / Hard cap 25% / Top 5 ≤ 70%
- [ ] **카테고리별 비중**: 메인 / 가치방어 / 스윙
- [ ] **동일 매크로 드라이버 합계** (의도하지 않은 단일 베팅)
  - 예: SK하이닉스 + Micron + NVDA = HBM/AI capex 단일 베팅 → 합계 ≤ 35%
- [ ] **Compounder 자가진단**: 비중 자연 증식 종목 → "오늘 0에서 시작한다면 이 비중으로 살까?"

---

## Phase 5: 결정 작성 + 자가진단 (5~10분)

> **결정만. 실행은 월요일.**

- [ ] **매도 결정 워크시트** (TRADE Action 종목):
  - KR → `data/KR_EXIT.md` 8 STEP 진입
  - US → `data/US_EXIT.md` 8 STEP 진입
- [ ] **추매 결정** (감시가 도달 + Pillar 모두 🟢):
  - `simulate_trade` 자동 실행
  - 9 킬질문 통과 확인
  - 카테고리별 사이즈 (Core 1차 5%, 스윙 3%)

- [ ] **Goalpost moving 자가진단** (1줄):
  - "이번 주 어느 종목의 KPI 임계값을 임의로 완화했나?" Y/N
  - Y → 해당 종목 비중 절반 자동 축소 약속

- [ ] **Inner Scorecard 자문** (1줄):
  - "이 결정을 SNS에 공개 못 한다고 가정해도 같은 결정인가?"

---

## Phase 6: 기록 + 큐 (5분)

- [ ] `set_alert(log_type='decision')` → 결정 + 행동 태그
  - 진입 사유: catalyst/valuation/momentum/contrarian/quality/event
  - 청산 사유: thesis-broken/stop/time-stop/target/opportunity-cost/panic/rebalance/tax
  - 감정: FOMO/fear/conviction-A/boredom/revenge
- [ ] **다음 주 큐**:
  - DEEP DIVE 종목 (일요일 1차 자료 정독)
  - 어닝 임박 D-7 이내 종목
  - Pre-mortem 1종목 (분기 rotation)

---

## 월간/분기 추가 작업

### 월 1회 (토요일 루틴 끝에 +15분)

- [ ] `get_trade_stats` → KPI 6종 갱신
  - Hit Rate, Win/Loss Ratio, Expectancy, Profit Factor, Kelly, R-multiple
- [ ] **행동 태그 분포 분석**
  - 어떤 진입 사유의 expectancy가 음수?
  - FOMO·panic 태그 거래의 평균 R-multiple?
- [ ] **Factor drift 점검**: 가치/성장/모멘텀/퀄리티 노출 변화

### 분기 1회 (+30분)

- [ ] **Pre-mortem 1종목** (보유 전체를 4분기 rotation)
  - "1년 후 -50%로 끝났다면 사유 5개" + Klein double-barreled ("청산했더니 +200% 갔다면")
- [ ] **Anti-thesis 갱신** (Munger inversion)
- [ ] **Original Thesis Lock + Diff View**: 매수 시점 thesis vs 현재 보유 사유 lexical overlap
- [ ] **Omission 평가**: 워치에서 안 산 종목 사후 가격 (Buffett "many billions cost")
- [ ] **Post-mortem 4분면**: 매도한 종목 → Deserved Success / Bad Break / Dumb Luck / Poetic Justice

### 12월 말 (한국 양도세)

- [ ] 단일 종목 50억 직전 → 12.30 정규장 마감 전 부분 매도 필수
- [ ] 해외주식 250만 원 공제 활용 (익절-재매수)
- [ ] 손익통산 (한국 거주자 wash sale 면제 → 12.30 손절 + 12.31 재매수 합법)
- [ ] 미국 T+1 결제 인지 (12.31 매도는 1월 결제로 밀림)

---

## 운영 규칙 Sticky

```
이번 주 매크로 임계 돌파: __ / 6
이번 주 TRADE 종목: __ 개
이번 주 Goalpost moving 자가진단: Y / N
디폴트는 HOLD. TRADE는 사유 메모 필수.
```
