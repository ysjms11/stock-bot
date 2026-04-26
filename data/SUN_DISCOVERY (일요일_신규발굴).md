# SUN_DISCOVERY (일요일 신규 발굴)

> **탐색 모드**
> 평주 모드: 시간의 80% = 워치리스트 thesis 점검
> 트리거 모드: Gate 1 신규 시만 (분기 1~2회)

---

## 핵심 원칙 5 (절대 규칙)

1. 연 4~6건 cap — 초과 시 신규 매수 금지 (Buffett punch card)
2. anti-thesis 빈 칸 = 주문 금지
3. 발견과 매수는 다른 날 (24시간 룰 default, 신규 thesis는 1주 cooling)
4. 워치 12개월 무진전 → 자동 폐기 + 6개월 cooling
5. 손실 종목 averaging down 금지 (thesis 훼손이면 절반 손절이 default)

---

## 평주 모드 (분기의 80%)

### Phase 1: 매크로·Sector Flow 점검 (30~45분)

- [ ] `get_regime` → 레짐·VIX·트랜치 단계
- [ ] `get_macro(op_growth)` → 매크로 성장 신호
- [ ] `get_sector` → 섹터 로테이션
- [ ] `get_supply(foreign_rank)` → KR 외인 sector 흐름 (WI26 대용)
- [ ] **웹서치 1**: Yardeni S&P sector EPS revision breadth
- [ ] **웹서치 2**: 미 ISM·중국 PMI (한국 6개월 후행 인지)

**판정 1줄**: 현재 leadership sector __, __, __ / **Gate 1이 새로 열린 산업: 있음/없음**

---

### Phase 2: 워치리스트 Thesis Review ★ (60~90분)

> **이 단계가 일요일의 본진. 시간의 50~60%.**

- [ ] `manage_watch(list)` → 영구 워치 15±5개 전수
- [ ] `get_stock_detail(tickers="워치 전체")` (batch) → 현재가 vs entry price
- [ ] `get_alpha_metrics(tickers="워치 전체")` → F-Score 변화
- [ ] `get_news(tickers="워치 전체")` → thesis 영향 헤드라인
- [ ] (KR) `get_consensus` → 컨센·TP 변화 (외국계·DS·신영 가중)
- [ ] (US) `get_us_analyst_research` + `get_us_ratings` → TP·rating 변화

**각 워치 종목별 매트릭스:**

| 종목 | Gate 2 (리더) | Gate 3 (정량) | Anti-thesis 깨졌나 | 가격 vs entry | 판정 |
|---|---|---|---|---|---|
| 워치1 | 🟢/🟡/🔴 | 🟢/🟡/🔴 | Y/N | __% | 유지/트리거/폐기 |

**3가지 결과:**
- **트리거 종목 0~2개** → Phase 3로
- **폐기 대상** (12개월 무진전, thesis 깨짐, 펀더 훼손 2분기 연속) → `manage_watch(remove)`
- **유지** → `set_alert` 감시가 갱신만

---

### Phase 3: 트리거 종목 1차 자료 정독 (30~60분, 트리거 시만)

> **충동의 적은 시간. 1차 자료가 시간을 늘린다.**

**KR:**
- [ ] `manage_report(collect)` → `manage_report(list)`
- [ ] `read_report_pdf` **최소 1개 직접** (TP 산출방식·EPS 개별 수치)
- [ ] `get_dart(mode=report)` → `get_dart(mode=read)`
  - 핵심 섹션: II.사업의 내용(매출 가시성), III.재무 주석(매출인식·재고), IX.임원(스톡옵션·내부자), 수시공시(단일판매·공급계약)

**US:**
- [ ] `get_us_earnings_transcript` (직전 분기)
  - CEO/CFO의 "believe", "expect", "challenging" 빈도 체크
- [ ] **웹서치**: 10-K Item 1A Risk Factors (Y/Y 변화), Item 7 MD&A
- [ ] **웹서치**: DEF 14A (CEO 보수·옵션)

---

### Phase 4: 1페이지 Trade Plan (20~30분, 트리거 시만)

> **anti-thesis 칼럼이 빈 칸이면 주문 금지.**

**Two-Column Note (Pabrai·Klarman 양식):**

| THESIS | ANTI-THESIS |
|---|---|
| 변화: __ | 깨지는 fact 1: __ |
| 숫자: 매출 +X%, 마진 +Yp, 멀티플 Z배 → IRR W% | 깨지는 fact 2: __ |
| 12개월 IRR Base/Bull: __ / __ | 매도 트리거: __ |
| Catalyst (≤24개월): __ | Bear 시나리오 -30% 합리적인가? __ |

- [ ] **20문항 checklist** (Pabrai 단축본): 부채/EBITDA≤3, 이자보상≥5, 매출 상위1고객≤15%, 영업CF/영업이익 0.8~1.2, CEO보수/영업이익≤2%, 감사인 변경 3년 없음, 내부자 클러스터 매도 없음 등 → **모두 PASS만 매수**
- [ ] **Pre-mortem 5분** (Klein HBR 2007): "1년 후 -50%" 가정 → 실패 원인 5~10개 즉시 작성
- [ ] **5 Kill Questions:**
  1. -50% 가는 가장 그럴듯한 시나리오 3개?
  2. 5년 안에 망하는 가장 흔한 경로?
  3. 내가 틀렸다는 가장 빠른 증거 (다음 분기 KPI 1~2개)?
  4. 같은 자본을 다른 후보·현금에 묻으면 기회비용?
  5. 5년 후 "뻔한 실수"로 회자될 가능성?

---

### Phase 5: 외부 시각 강제 + 비교 노트 (15~20분, 트리거 시만)

- [ ] **반대 의견 의도적 검색**: 매도/Hold 리포트 1건 (외국계·DS·신영 우선)
- [ ] **TipRanks 별점 분산** 확인
- [ ] **컨센과 정반대인 구체 변수** 식별 (= mispricing)
- [ ] **비교 노트**: 보유 종목 중 가장 약한 thesis vs 신규 후보 → "교체할 만한 우월성?" 모호하면 보류

---

### Phase 6: 결정 + 기록 (10~15분)

- [ ] `simulate_trade` 자동 실행
- [ ] **9 킬질문** 통과 확인
- [ ] **FOMO 3단계 자가 stop:**
  1. 물리적: 매수 버튼 손 떼고 알람 30분
  2. Lap test: anti-thesis 3분 내 3개 못 쓰면 REJECT
  3. 기회비용: 신규 IRR > 기존 약체 IRR 명확한가?
- [ ] **결정**: 1차 사이즈만 (목표의 30~50%, **월요일 9:30 이후**)
- [ ] `set_alert(log_type='decision')` → 행동 태그 + thesis 파일 저장
- [ ] **Punches remaining**: __ / 6 갱신

---

## 트리거 모드 (Gate 1 신규 시만, 분기 1~2회)

> **평주 Phase 1에서 "Gate 1이 새로 열린 산업: 있음" 일 때만 작동.**

### Phase 7: 신규 발굴

- [ ] `get_scan` → 알파팩터 (F-Score≥7 + Magic Formula 상위)
- [ ] `get_highlow` → 52주 신고가 (Trend Template 보조)
- [ ] (US) `get_us_buy_candidates` → 톱 애널 + TP 업사이드
- [ ] (US) `get_us_scan` → 레이팅 업그레이드
- [ ] **L1(50개) → L2(워치 진입 후보 0~2개)** 압축
- [ ] L2 진입 기준: **(1) Industry/Thesis Gate, (2) Business Quality Gate (ROIC≥WACC+5%p, moat 1개+)** 통과 + **(3) Price Gate 미달** (= 가격이 안 와서 못 사는 상태)
- [ ] `manage_watch(add)` + "왜 안 사는가" 메타데이터 + 24개월 내 catalyst 명시

---

## 운영 규칙 Sticky

```
Punches remaining this year: __ / 6
워치리스트 영구 슬롯: __ / 15±5
이번 분기 신규 발굴 trigger: 발생 / 미발생
```

---

## 함정 회피 5

| 함정 | 대응 |
|---|---|
| 매주 신규 발굴해서 매수 | Punches __ / 6 시각화 |
| Thematic ETF launch된 트렌드 진입 | ETF launch = 끝물 가설 default |
| 한국 컨센 그대로 신뢰 (매수 92.9%) | "매수→Hold = 매도 신호" + 외국계 대조 |
| Story만 보고 Gate 3 면제 | thesis는 매번 IRR W%로 환산 |
| 손실 종목 averaging down | 추매 trigger 사전 명시만 |
