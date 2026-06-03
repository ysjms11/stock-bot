# US_DEEPSEARCH.md (v5)

> 미국 주식 집중 포트폴리오 딥서치. Claude.ai + KIS MCP 봇 + 웹서치 조합.
> 갱신일: 2026-06-01 (v5, 통합 반증검증 반영) · 원본: `INVESTMENT_RULES.md` · 페어: `US_EXIT.md`
> 3-Gate / 비중 3단계 / 카테고리 3분류 / 등급 문자(A/B+) 완전 제거.

---

## ⚠️ v5 핵심 전환 (2026-06-01) — "단순화하되 비대칭으로"

매수 게이트 전체를 통합 반증검증(부분검증 4건 + 문서 통합검증)으로 재설계했다. 핵심 원리:

**1. 단순화의 비대칭 (가장 중요).** "항목 줄이기"가 아니라 **방향을 나눈다**:
- **우꼬리(대박) 차단 게이트 → 완화/삭제.** 극단밸류 veto, K/G 매크로 분류, z-score 정교 결합. 이유: Bessembinder(2018) 미국 net wealth의 100%가 상위 4.3% 종목에서 발생 — 이들은 사전에 PER·PBR 상단인 경우가 많다(Asness 1997: 모멘텀은 비싼 종목에서 가장 강함). veto는 NVDA 2023~24 같은 우꼬리를 사전 절단. 2010 Tesla·2015 NVDA는 밸류·모멘텀 게이트를 통과 못 했을 종목.
- **좌꼬리(부도·영구손실) 차단 게이트 → 강화·정량화.** 이유: 같은 Bessembinder 데이터에서 **57.4%가 평생 T-bill 미달**, 종목 lifetime 수익률 중위값 음수. 5~25% 비중에서 영구손실 1건의 자본 충격이 회생주 1건 놓치는 비용을 초과.

**2. 매수=매도 pre-commitment.** 매수 시점에 정량 Kill Switch 1~2개를 의무 작성 → US_EXIT의 전량 이진 매도와 한 변수로 묶음. Shefrin-Statman(1985)·Odean(1998)·He-Strub-Zariphopoulou(2019): disposition effect 차단의 표준 처방.

**3. 게이트는 좌꼬리 hygiene일 뿐 우꼬리를 만들지 않는다.** 통과 종목 중 대박은 여전히 ~4%. 우꼬리는 게이트가 아니라 (a) 질적 안목(혁신·해자·자본배분·optionality), (b) 5~20년 holding 준비(분기 -50% 변동성 견디는 비중), (c) thesis pre-commitment에서 나온다. 게이트 정밀화에 자원을 더 쓰지 말 것.

**삭제된 것**: K/G(STEP 0), 극단밸류 veto, Altman Z(기술주 무형자산 왜곡 — Merton DD/시총-부채로 대체), z-score 점수화, 부분 정리 매도(→전량 이진).
**강화된 것**: 좌꼬리 Tier 1(즉시 탈락)+Tier 2(정량 4개), 수급 신호 종류별 차등, Kill Switch 의무화, 임계 라벨링.

---

## 🧭 사전 규칙 (모든 프롬프트 공통)

### A. 매수 판정 — 5-Stage 구조 (좌꼬리 hygiene → 3-Gate → pre-commit)

**Stage 1 — Tier 1 즉시 탈락 (boolean, 하나라도 해당 시 매수 금지)**
- 8-K Item 4.02 (회계 Non-Reliance/restatement) | going concern opinion (PCAOB AS 2415) | SEC AAER 대상 | 상폐 통지(8-K 3.01)
- [규제 의무 1차 출처. 미국은 자동 상폐 룰이 약해 영구손실이 천천히·깊게 옴(WeWork 2019→2023 4년) → 매수 단계 좌꼬리 게이트가 미국에서 더 중요]

**Stage 2 — Tier 2 정량 좌꼬리 게이트 (전부 통과)**
- **Cash runway ≥ 12개월** (적자기업) 또는 FCF margin > 0 [VC 실무 통용; 거품의 본질은 "비싸다"가 아니라 "비싼데 현금 소진" — Pets.com/WeWork/2021 SPAC]
- **Net Debt/EBITDA ≤ 5~6x** 또는 무차입 [Moody's 신용평가 1차]
- **시총/총부채 > 2x** (또는 Merton DD > 2σ — retail 접근 시) [Merton 1974, Hillegeist 2004 학술 강함]
- **10-K Risk Factors 직전 분기 대비 비정상 추가 없음** [Cohen-Malloy-Nguyen 2020 JoF: 10-K 텍스트 변경 모니터링 월 188bp]
- Beneish M > -1.78은 **단독 금지**, 보조 신호로만 (Beneish 1999, FP 17.5%)
- ⚠️ **Altman Z 폐기** — 기술주·SaaS·바이오에서 무형자산 왜곡(R&D 비용처리 → X2/X4 distress zone 오인). Braunsberger-Aschauer 2025 JRFM. 시장 기반(Merton/시총-부채)으로 대체.

**Stage 3 — 3-Gate AND (산업 × 리더, binary)**
1. **산업 흐름** — 섹터 S&P500 대비 상대강도 우위 또는 구조적 수요 입증
2. **리더 지위** — 산업 내 점유율·해자·마진 상위 1~3위

**Stage 4 — Gate ③ 근거 (단독 불가, 2개 이상 — 신호 종류 차등 필수)**

| 신호 | 알파 (1차 출처) | Gate③ 카운트 |
|---|---|---|
| **Value × Momentum 결합** | Asness-Moskowitz-Pedersen 2013 JoF 68 (V·M 음상관, 결합 Sharpe 우월) | ✅ 핵심 |
| **Opportunistic insider buy 클러스터** | Cohen-Malloy-Pomorski 2012 JoF 67: VW +82bp/월(≈9.8%/yr) | ✅ 핵심 |
| **Confidential 13F amendment** | Agarwal-Jiang-Tang-Yang 2013 JoF 68: +5.2~7.5%/yr | ✅ |
| Damodaran sanity check (implied g < 10Y T-yield) | Damodaran *Investment Valuation* Ch.12 | ✅ 통과 조건 |
| ~~일반 셀사이드 등급~~ | Barber 2001: 거래비용 후 net ≈ 0; Womack 대형주 무의미 | ❌ 제외 |
| ~~일반 13F (45일 지연)~~ | 거래비용 후 미미 | ❌ 제외 |
| ~~Routine insider buy~~ | CMP 2012: -20bp/월 (≈0) | ❌ 제외 |
| ~~옵션 UOA / P/C~~ | Pan-Poteshman 2006: horizon 일~주 (집중투자 부적합) | ❌ 제외 |

  - "정확히 2개" 임계는 arbitrary지만 **"단독 불가, 2개 이상"은 유지 가능한 bright-line** (단독 신호 알파 ≈0, AQR 2016). 수급은 **opportunistic insider + confidential 13F만** value/momentum과 동급 카운트.

**Stage 5 — Pre-commit (매수 직전, READ-DO 의무)**
- **Kill Switch 1~2개 정량 작성** — "Stage 4 신호 X·Y가 무효화되거나 [정량 지표]가 [임계] 이하로 무너지면 thesis 무효 → 전량 매도." US_EXIT와 동일 변수로 묶음.
- **공통 동인 한 줄 기록** (거름 아님, 모니터링용) — 예: "AI capex sensitivity HIGH". 포트 전체 동일 factor 중복노출(NVDA+AVGO+AMZN) 추적 + 매도 트리거 정의용.
- **Pre-mortem 1문장** (Klein 2007) — "1년 후 이 매수가 실패했다면, 가장 가능성 높은 이유는?"

**판정 결과**:
- Stage 1 해당 OR Stage 2 미달 → **매수 금지** (좌꼬리)
- Stage 3 (3-Gate) 3/3 + Stage 4 2개+ + Stage 5 완료 → **Standard/Core 후보**
- Stage 3 2/3 → **Starter 한정** (half-position)
- Stage 4 1개 이하 → Pass

### B. 비중 3단계 (35% 진입 상한 유지 — 사용자 결정 2026-06-01)

| 카테고리 | 비중 | 조건 |
|---|---|---|
| Starter | 3~5% | Stage 3 2/3 + Stage 1·2 통과 (Half-position) |
| Standard | 7~12% | 5-Stage 전부 통과 초기 진입 |
| Core | 15~25% | 5-Stage + 1년 thesis + 분기 재검증 통과 |
| 예외 | 25~35% | 극단 컨빅션 + 사전 기록 + **Stage 2 좌꼬리 게이트 특히 엄격** |
| 금지 | > 35% | 단일 종목 절대 한도 |

- **35%는 "진입 상한"이지 "트림 기준"이 아니다.** 새로 살 때 한 종목 35% 초과 금지. 단 **이미 산 게 올라서 35% 초과 시 자르지 않음**(트림 ❌) — 우꼬리가 일하는 것, 끝까지 보유(Bessembinder). 가격 상승은 매도 사유 아님(US_EXIT 절대원칙 0).
- **큰 비중일수록 좌꼬리 게이트 엄격**: 영구손실 1건 충격이 비중에 비례(25% 종목 부도 시 포트 -25%, 회복 +33%/3~4년). 따라서 비중 20%+ 진입 시 Stage 1·2를 특히 깐깐하게. (참고: 검증은 fractional Kelly 정신상 Core 15~20% 권고했으나, 사용자가 35% 진입 상한 유지 결정 — 대신 좌꼬리 게이트 강화로 보완.)

### C. 카테고리 3분류
- **메인**: 5-Stage 전부 통과. 손절 -15%(스윙 아님, thesis 기반). 목표가 2Y Fwd EPS × Sector Multiple = **재산정 기준**(매도 트리거 아님)
- **가치**: FCF yield > 5% + Net Cash + 해자. 손절 -12%
- **스윙**: 기술적 돌파 + 수급. 손절 -7~10% 고정 스톱(O'Neil/Minervini), 보유 ≤ 3M, 비중 ≤ Starter

### D. 매도 4트리거 (US_EXIT와 동기화 — 전량 이진, 가격≠매도)

> 상세는 `US_EXIT.md`. 요약:
> **대원칙: 가격 상승은 매도 사유 아님(재산정 트리거). 전량 보유/전량 매도 이진. thesis 붕괴로만 매도.**

1. **Thesis 훼손** → **즉시 전량 청산**. Stage 5에서 적은 Kill Switch 발동. **공통 동인(AI capex 등) 2분기 연속 둔화 시 동인 묶인 종목 전체 재평가** 포함.
2. **리더십 상실** → 연속 2분기 EPS 미스 + 가이던스 하향 → **thesis 재평가 → 전량/보유 이진** (부분 50% 폐기)
   - 대형주: PEAD 소멸(Martineau 2022). 발표 후 2거래일 내 판단
   - 소형주 <$2B: PEAD 잔존, 60일 드리프트
3. **밸류 sanity 붕괴** → Implied perpetual g ≥ 10Y T-yield (Damodaran cap 위반) → **재산정 트리거(자동매도 아님)**. 재산정 후 thesis 약화 시 기회비용(트리거4) 검토
4. **포지션/기회비용** → 단일 > 35% 진입 금지(올라서 초과는 보유) / 명백히 우월한 기회로 자금 재배치 시에만 비중 조절 (전량 이진의 예외)
   - **섹터 상한 없음** (집중 투자 원칙)

### E. Core 3거래일 쿨링 (트리거 2에만)
Core 악재 → 당일 매도 금지, 3거래일 후 thesis 재평가(전량/보유). 근거: Jegadeesh(1990) 단기 reversal.
예외: Thesis 훼손 확정(10-K restatement, FDA 거부, SEC AAER) → 즉시 전량.
※ 트리거 3(밸류)는 매도 아니므로 쿨링 무관.

### F. Claude 금지 6개 (Anthropic positive instruction)
1. ❌ PER/PBR 단독 컷 → ✅ 업종별 프레임 (SaaS=Rule40, 반도체=Through-Cycle, 바이오=rNPV, 광모듈=EV/Sales)
2. ❌ A/B+/B 등급 문자 → ✅ 카테고리 + 비중단계
3. ❌ 셀사이드 목표가 맹신 → ✅ Barber(2001) net alpha≈0. Bold analyst 상위 5명 trimmed mean (Clement-Tse 2005)
4. ❌ VIX/거시로 종목 매매 결정 → ✅ VIX·레짐은 현금 조정용. **K/G 매크로 분류 삭제**(실시간 불가 + 매수 결정 누수: Nisbett 1981 dilution, Wilson 1996 anchoring, Kunda 1990 motivated reasoning)
5. ❌ 근거 없는 수치 → ✅ 학술·실무서·업계 표준 명시. 확인 불가 시 "데이터 불충분"
6. ❌ **가격 상승(고수익률·목표가 도달)을 매도/회피 사유로 → ✅ 재산정 트리거** (Bessembinder right-tail). ❌ 극단밸류 veto로 우꼬리 절단 → ✅ Damodaran sanity check(수학적 불가만)으로 한정

### G. Kill 조건 (좌꼬리 hygiene, 정량)

| 지표 | Kill | 근거 | 라벨 |
|---|---|---|---|
| 회계 Non-Reliance | 8-K Item 4.02 발생 | 17 CFR 240 | 규제 의무 |
| Going concern | PCAOB AS 2415 opinion | FASB ASC 205-40 | 규제 의무 |
| Cash runway | < 12개월 (적자기업) | a16z/SVB 실무, Barron's 2000.3 | 실무 통용 |
| Net Debt/EBITDA | > 6x | Moody's 2014 | 신용평가 1차 |
| 시총/총부채 | < 2x (또는 Merton DD < 2σ) | Merton 1974, Hillegeist 2004 | 학술 강함 |
| Implied perpetual g | ≥ 10Y T-yield (수학적 불가 sanity check) | Damodaran NYU | 학술/실무 권위 |
| 회계 적신호 | Beneish M > -1.78 AND (감사변경 OR restatement OR AAER) | Beneish 1999 | 학술 (단독 금지) |

### H. Yellow 조건 (조사 심화)
- 시총/총부채 2~3x or Cash runway 12~18M → 조사 심화
- Beneish M > -1.78 단독 → 조사 심화
- 3-Gate 2/3 → Half-position (Starter)
- 10-K Risk Factors 변경 감지 → MD&A 정독

### I. ❌ v4→v5에서 삭제·전환

- **K/G 국면 분류 (STEP 0)** — 실시간 불가(Cochrane 2011), 라벨로도 결정 누수(행동재무) → 완전 삭제, 거래일지 사후 회고로만
- **극단밸류 veto** — 우꼬리(고밸류+고품질) 절단(Asness 1997) → 삭제, Damodaran sanity check로 대체
- **Altman Z'' Kill 조건** — 기술주 무형자산 왜곡 → Merton DD/시총-부채로 교체
- **z-score 정교 결합** — estimation error가 신호 압도(McLean-Pontiff 2016 alpha decay 26~58%) → 미도입
- **부분 정리 매도 30~50%** — Shiryaev 2008 bang-bang → 전량 이진 (US_EXIT)
- (v3→v4 기삭제) Reverse DCF ×1.3배, A/B+ 등급, Short Float 5%, SBC 15%, VIX 확률, n=3 샘플

---

## 📋 프롬프트 ① — 풀 딥서치 (45분)

```
[티커] 미국 풀 딥서치 v5. 현재가 약 $[현재가].
5-Stage 순서대로. 각 Stage 내 호출 최대 병렬.
모든 수치 출처 명시. 확인 불가 시 "데이터 불충분". 등급 문자 금지.
대원칙: 가격 상승은 매수회피/매도 사유 아님. 좌꼬리(부도) 게이트는 엄격, 우꼬리(밸류) veto 없음.

━━ STEP 0. 레짐 (현금 도구로만, 종목 판단 개입 금지) (2분) ━━
봇 병렬: get_regime + get_macro(mode="dashboard") + get_macro(mode="us_sector")
<regime>공격/중립/위기 (현금 비중 참고용. K/G 분류 안 함 — 삭제됨)</regime>

━━ STEP 1. 아이디어 소싱 (5분) ━━
봇 병렬: get_rank(type="us_price", sort="rise"/"fall", n=20) + get_us_scan(mode="watchlist"/"discovery")
웹 옵션: 분기말+45일 시 "confidential 13F amendment" 우선

━━ STAGE 1+2. 좌꼬리 게이트 (10분) ★최우선, 밸류보다 먼저 ━━
웹 병렬:
- stockanalysis.com/stocks/[ticker]/financials/ (+ balance-sheet, cash-flow)
- sec.gov/cgi-bin/browse-edgar?CIK=[ticker]&type=8-K (Item 4.02/3.01 확인)
- sec.gov ...&type=10-K (going concern, Risk Factors)

Tier 1 (즉시 탈락 boolean):
□ 8-K Item 4.02 (회계 정정) □ going concern opinion □ SEC AAER □ 상폐 통지
→ 하나라도 YES = 매수 금지, 분석 중단

Tier 2 (전부 통과):
□ Cash runway ≥ 12M (적자기업) or FCF margin > 0
□ Net Debt/EBITDA ≤ 6x or 무차입
□ 시총/총부채 > 2x
□ 10-K Risk Factors 비정상 추가 없음
→ 미달 시 매수 금지 (Yellow는 조사 심화)
※ Altman Z 계산 안 함(기술주 왜곡). Beneish M은 보조만.

━━ STAGE 3. 3-Gate (산업 × 리더) (5분) ━━
봇: get_us_ratings(consensus/trend, months=6) + get_news(sentiment=true)
웹: stockanalysis.com/stocks/[ticker]/forecast/ + "market share" + "moat"
<gate1_industry>섹터 상대강도 / 구조적 수요 — YES/NO</gate1_industry>
<gate2_leader>점유율·해자·마진 상위 1~3위 — YES/NO</gate2_leader>
※ Clement-Tse 2005: bold analyst 상위 5명 trimmed mean. 단순 평균 금지.

━━ STAGE 4. Gate③ 근거 (단독 불가, 2개+, 신호 차등) (8분) ━━
밸류 (업종별 프레임):
- SaaS(GM≥70%): Rule of 40 + NRR
- 반도체 메모리: PBR + cycle-adjusted PER + Book-to-bill
- 반도체 로직/AI(NVDA/AMD): Through-Cycle PER (SOX 10Y 17.5x), DC 30~45x
- 바이오: rNPV × PoS (DiMasi 2016: P1→승인 11.83%), 할인율 10~15%
- 광모듈: EV/Sales 4~6x
- Damodaran sanity check: Implied perpetual g < 10Y T-yield (위반 시 Kill)

모멘텀: 3M 상대강도 상위 20%, 52주고가 -15% 이내

수급 (종류 차등 — 유효한 것만 카운트):
웹: openinsider.com/screener?s=[ticker] (opportunistic vs routine 구분!)
    whalewisdom.com/stock/[ticker] (confidential 13F amendment 확인)
✅ 카운트: opportunistic insider buy 클러스터(P-code, CEO/CFO/Chair), confidential 13F
❌ 제외: 일반 셀사이드 등급, 일반 13F(45일), routine insider, 옵션 UOA

<gate3_evidence>유효 신호 몇 개? (밸류/모멘텀/opportunistic수급 중 2개+ 필요)</gate3_evidence>

━━ STAGE 5. Pre-commit (READ-DO 의무) (5분) ━━
<kill_switch>정량 1~2개: "신호 X 무효화 or [지표] < [임계] → thesis 무효, 전량 매도"</kill_switch>
<common_driver>공통 동인 한 줄 (예: AI capex HIGH) — 모니터링용, 거름 아님</common_driver>
<pre_mortem>1년 후 실패했다면 가장 큰 이유 1문장 (Klein)</pre_mortem>

봇: simulate_trade + set_alert + manage_watch

출력 템플릿:
<judgment>
  <regime>공격/중립/위기 (현금 도구. K/G 없음)</regime>
  <ticker>[ticker]</ticker>
  <tier1>즉시탈락 4종 — PASS/KILL</tier1>
  <tier2>Cash runway / ND-EBITDA / 시총-부채 / Risk Factors — PASS/미달</tier2>
  <gate>①산업 __ ②리더 __ (3-Gate)</gate>
  <gate3>유효 신호 __개 (opportunistic수급/밸류/모멘텀)</gate3>
  <valuation>업종프레임 / Damodaran sanity / FV 2Y(재산정 기준)</valuation>
  <kill_switch>정량 1~2개 (매수=매도 묶음)</kill_switch>
  <common_driver>공통 동인 (모니터링)</common_driver>
  <decision>카테고리 / 비중단계 / 비중% / action</decision>
  <stop_target>손절 / 목표 2Y(재산정) / 1차진입</stop_target>
  <thesis>Bull / Bear / pre-mortem</thesis>
  <sources>학술 / 웹 / data_gaps</sources>
</judgment>
```

---

## ⚡ 프롬프트 ② — 빠른 판정 (10분)

```
[티커] 미국 빠른 5-Stage 스크린. 현재가 약 $[현재가].
봇 병렬: get_regime + get_macro(dashboard) + get_us_ratings(consensus/trend) + get_news
웹: stockanalysis.com/stocks/[ticker]/financials/ + finviz.com/quote.ashx?t=[ticker] + openinsider.com/screener?s=[ticker]
봇: get_stock_detail(D120) + simulate_trade

좌꼬리 속판 (Stage 1+2):
- 8-K 4.02 / going concern / AAER 있나? → 있으면 즉시 Pass
- Cash runway ≥12M or FCF>0? / Net Debt/EBITDA ≤6x? / 시총-부채 >2x?

3-Gate 속판 (Stage 3+4):
Gate1: 섹터 1M 상대강도 > S&P
Gate2: 점유율·해자 상위
Gate3: opportunistic insider buy OR confidential 13F OR (밸류 + 모멘텀) 중 2개+
  ※ 일반 셀사이드 등급·옵션 UOA 카운트 안 함

Pre-commit: Kill Switch 1개 + 공통동인 + pre-mortem 1줄

Quick Kill: Tier 1 해당 / Cash runway<12M / Implied g ≥ 10Y yield
출력: Stage 통과 여부, 비중단계, Kill Switch, action
```

---

## 🔁 프롬프트 ③ — 분기 재검증 (Core 전용)

```
[티커] 미국 분기 재검증. 보유 Core. 종목당 8분.
트리거: 어닝 발표 후 2주 이내 OR Core 악재 3거래일 쿨링 후
대원칙: 가격이 얼마 올랐는지/빠졌는지는 재검증 사유 아님. thesis·좌꼬리만.

봇: get_us_ratings(trend, months=3)
웹: stockanalysis.com financials ?p=quarterly + sec.gov 10-Q/8-K + whalewisdom + openinsider

확인:
1. Stage 5 Kill Switch 발동했나? (매수 시 적은 정량 조건)
2. 좌꼬리 재점검: Cash runway / Net Debt/EBITDA / 8-K 4.02·going concern 신규?
3. Revenue YoY / FCF / GM 분기 방향 / 가이던스 Δ
4. 공통 동인(AI capex 등) 2분기 연속 둔화? → 동인 묶인 종목 전체 재평가
5. opportunistic insider / confidential 13F 변화 (일반 13F·셀사이드 무시)

매도 4트리거 (US_EXIT 동기화):
T1. Thesis 훼손 (Kill Switch 발동) → 즉시 전량 (쿨링 예외)
T2. 리더십 상실 (2분기 미스+가이드 하향) → thesis 재평가 → 전량/보유
  * 대형주 2거래일 / 소형주 60일
T3. Damodaran sanity 붕괴 (Implied g ≥ 10Y) → 재산정 트리거(매도 아님)
T4. 단일 >35% 진입 금지(올라서 초과는 보유) / 기회비용 재배치만 부분

쿨링: Core 악재 당일 매도 금지, 3일 후 재평가 (T1 확정 예외)
출력: Kill Switch 상태 / 좌꼬리 / trigger / action / next review
```

---

## 📚 학술·실무 근거

| 원칙 | 출처 |
|---|---|
| 단순화 비대칭 (1/N 가중) | DeMiguel-Garlappi-Uppal 2009 RFS 22; Platanakis 2020 EJOR (가중≠종목선택) |
| 우꼬리 (right-tail) | Bessembinder 2018 JFE 129:440-457 (상위 4.3%); 2023 FAJ 79 (글로벌 2.4%) |
| Best ideas (집중) | Cohen-Polk-Silli 2010; Antón-Cohen-Polk 2021 (연 2.8~4.5%) |
| 단순 결합 성공 | Piotroski 2000 JAR (F-score 등가중) |
| less-is-more | Gigerenzer-Brighton 2009 TopiCS 1 |
| 극단밸류 veto 반증 | Asness 1997 FAJ 53 (모멘텀은 비싼 종목서 강함); Asness 2016 JPM (factor timing 약함) |
| 좌꼬리 (부도 회피) | Campbell-Hilscher-Szilagyi 2008 JoF 63 (distress 음수익); Dichev 1998 JoF 53 |
| Merton DD | Merton 1974 JoF 29; Hillegeist 2004 RAS 9 (BSM-Prob > Z·O); Bharath-Shumway 2008 RFS 21 |
| Altman 한계 | Altman 1968 JoF 23; Braunsberger-Aschauer 2025 JRFM 18(8) (기술주 부적합) |
| 10-K 텍스트 | Cohen-Malloy-Nguyen 2020 JoF 75 (월 188bp) |
| Cash runway 거품 | Barron's 2000.3 "Burning Up"; De-SPAC 2021-22 (Russell/VRC) |
| V×M 결합 | Asness-Moskowitz-Pedersen 2013 JoF 68 |
| Opportunistic insider | Cohen-Malloy-Pomorski 2012 JoF 67 (VW +82bp/월) |
| Confidential 13F | Agarwal-Jiang-Tang-Yang 2013 JoF 68 (+5.2~7.5%/yr) |
| 일반 수급 무효 | Barber 2001 JoF 56 (net≈0); Pan-Poteshman 2006 RFS 19 (horizon 단기) |
| PEAD 대형주 소멸 | Martineau 2022 Critical Finance Review 11 |
| 매수=매도 / disposition | Shefrin-Statman 1985 JoF 40; Odean 1998 JoF 53; He-Strub-Zariphopoulou 2019 JET |
| Pre-mortem | Klein 2007 HBR; Mitchell-Russo-Pennington 1989 JBDM (+30%) |
| Reverse DCF cap | Damodaran *Investment Valuation* Ch.12; Musings 2016-11 |
| 다중검정 함정 | Bailey-López de Prado 2014 JPM 40(5); Harvey-Liu-Zhu 2016 RFS 29 (t>3.0); Hou-Xue-Zhang 2020 |
| 체크리스트 | Gawande 2009 *Checklist Manifesto* (5~9개, killer items); Miller 1956 (7±2) |
| 비중/Kelly | Kelly 1956; Ivković-Sialm-Weisbenner 2008 JFQA |
| Anthropic | platform.claude.com/docs/en/build-with-claude/prompt-engineering |

## ⚠️ 임계 라벨

- **규제 의무**: 8-K 4.02, going concern, AAER, 상폐 통지
- **학술 강함**: Merton DD 2σ, 시총/총부채 2x, V×M 결합, opportunistic insider, confidential 13F, 10-K 텍스트 변경
- **신용평가 1차**: Net Debt/EBITDA 6x, EBIT/이자 1.5x
- **학술/실무 권위**: Damodaran perpetuity cap (implied g < 10Y)
- **실무 통용 (학술 임계 없음)**: Cash runway 12M, 비중단계 %, Short Float 10/20/30%, Rule of 40, EV/Sales 4~6x
- **arbitrary (명시)**: Gate③ "2개"라는 정수, 비중 상한 35% 숫자 자체
- **삭제됨**: K/G 분류, 극단밸류 veto, Altman Z, z-score, 부분정리

---

## 변경 이력

- **2026-06-01 v5**: 통합 반증검증 반영 (부분검증 4건 + 문서 통합검증)
  - **단순화 비대칭 원리** 도입: 우꼬리 차단(밸류 veto·K/G·z-score) 완화/삭제, 좌꼬리 차단(부도·회계·희석) 강화·정량화
  - K/G(STEP 0) 완전 삭제 — 실시간 불가 + 결정 누수
  - 극단밸류 veto 삭제 — 우꼬리 절단(Asness 1997), Damodaran sanity check로 대체
  - Altman Z 폐기 — 기술주 무형자산 왜곡, Merton DD/시총-부채로 교체
  - 좌꼬리 게이트 Tier 1(즉시탈락) + Tier 2(Cash runway/ND-EBITDA/시총-부채/10-K) 신설
  - Gate③ 신호 종류별 차등 — opportunistic insider·confidential 13F만 유효, 셀사이드·일반13F·UOA 제외
  - Stage 5 Pre-commit 의무화 — Kill Switch 정량 + 공통동인 + pre-mortem (매수=매도 묶음)
  - 매도 트리거 US_EXIT 동기화 — 전량 이진, 가격≠매도
  - 비중 35% 진입 상한 유지(사용자 결정) + "트림 아닌 진입 상한" + 큰 비중일수록 좌꼬리 엄격
  - z-score 미도입 (estimation error), 임계 라벨링 전면 적용
- 2026-04-24 v4: Reverse DCF 1.3배→Damodaran cap, A/B+ 등급→3-Gate, Short Float 10/20/30%, 섹터상한 폐지, PEAD 대형주 소멸
- 2026-04-17 v3: K/G STEP 0 추가 (→ v5에서 삭제)
- 2026-04-15 v3: 재무→밸류 순서
