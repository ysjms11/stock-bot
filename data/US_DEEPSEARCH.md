# US_DEEPSEARCH.md (v4)

> 미국 주식 집중 포트폴리오 딥서치. Claude.ai + KIS MCP 봇 + 웹서치 조합.
> 갱신일: 2026-04-24 · 원본: `INVESTMENT_RULES.md`
> 3-Gate AND / 비중 3단계 / 카테고리 3분류 / 등급 문자(A/B+) 완전 제거.

---

## 🧭 사전 규칙 (모든 프롬프트 공통)

### A. 매수 판정: 3-Gate AND
세 게이트 중 2/3 통과 → Starter / 3/3 통과 → Standard·Core

1. **산업 흐름** — 섹터 S&P500 대비 상대강도 우위 또는 구조적 수요 입증
2. **리더 지위** — 산업 내 점유율·해자·마진 상위 1~3위
3. **근거** — 밸류/모멘텀/수급 중 최소 1개 정량 근거 (정성 불가)
   - 밸류: EV/Sales peer 하위 30%, Fwd PE < 5y avg, Reverse DCF 내재g < 컨센
   - 모멘텀: 3M 상대강도 상위 20%, 52주고가 -15% 이내
   - 수급: 내부자 P코드 클러스터 3인+, 13F 신규 10펀드+, UOA Call/Put > 2

### B. 비중 3단계

| 카테고리 | 비중 | 조건 |
|---|---|---|
| Starter | 3~5% | Gate 2/3 + safety filter (Half-position) |
| Standard | 7~12% | 3-Gate 3/3 초기 진입 |
| Core | 15~25% | 3-Gate + 1년 thesis + 분기 재검증 통과 |
| 예외 | 25~35% | 극단 컨빅션 + 사전 기록 |
| 금지 | > 35% | 단일 종목 절대 한도 |

### C. 카테고리 3분류
- **메인**: 3-Gate 전부 통과. 손절 -15%, 목표가 2Y Fwd EPS × Sector Multiple
- **가치**: FCF yield > 5% + Net Cash + 해자. 손절 -12%
- **스윙**: 기술적 돌파 + 수급. 손절 -7~10% (O'Neil/Minervini), 보유 ≤ 3M, 비중 ≤ Starter

### D. 매도 4트리거
1. **Thesis 훼손** → 즉시 청산 (Bull 핵심가정 파괴)
2. **리더십 상실** → 연속 2분기 EPS 미스 + 가이던스 하향
   - 대형주: PEAD 소멸 (Martineau 2021 CFR). 발표 후 2거래일 내 판단
   - 소형주 <$2B: PEAD 잔존, 60일 드리프트 기대
3. **밸류 극단 + 유포리아** → Implied perpetual g ≥ 10Y T-yield (Damodaran cap 위반) → 부분 정리 30~50%
4. **포지션 구조 위반** → 단일 종목 > 35% → 자동 리밸런싱
   - **섹터 상한 없음** (집중 투자 원칙, 킬스위치 2~3개 대체)

### E. Core 3일 쿨링
Core 악재 → 당일 매도 금지, 3거래일 후 thesis 재평가.
근거: Jegadeesh(1990 JoF) 단기 reversal.
예외: Thesis 훼손 확정 (10-K restatement, FDA 거부, SEC AAER) → 즉시.

### F. Claude 금지 5개 (Anthropic positive instruction)
1. ❌ PER/PBR 단독 컷 → ✅ 업종별 프레임 (SaaS=Rule40, 반도체=Through-Cycle, 바이오=rNPV, 광모듈=EV/Sales)
2. ❌ A/B+/B 등급 문자 → ✅ 카테고리 + 비중단계
3. ❌ 셀사이드 목표가 맹신 → ✅ Barber(2001) net alpha≈0. Bold analyst 상위 5명 trimmed mean (Clement-Tse 2005 herding)
4. ❌ VIX로 종목 매매 결정 → ✅ VIX는 레짐·현금 조정용
5. ❌ 근거 없는 수치 → ✅ 학술·실무서·업계 표준 명시. 확인 불가 시 "데이터 불충분"

### G. Kill 조건 (학술 검증 완료, 3개)

| 지표 | Kill | 근거 |
|---|---|---|
| Altman Z'' | < 1.1 AND (IC < 1.5× OR Net Debt/EBITDA > 5×) | Altman 1968/2013 |
| Implied perpetual growth | ≥ 10Y T-yield (수학적 불가) | Damodaran "Stable Growth Rate" NYU |
| 회계 적신호 | Beneish M > -1.78 AND (감사의견 변경 OR 10-K restatement OR SEC AAER) | Beneish 1999, FP 17.5% |

### H. Yellow 조건 (조사 심화)
- Altman Z'' 1.1~2.6 Grey zone
- Beneish M > -1.78 단독
- 3-Gate 2/3 → Half-position (Starter)
- 기술·SaaS·바이오: Altman 편향 (MDPI 2025 무형자산) → Runway < 12M + Rule of 40 < 0로 대체

### I. ❌ v3에서 삭제

- **Reverse DCF 컨센×1.3배** — Damodaran·Rosenbaum·McKinsey·CFA 근거 전무 (완전 창작)
- **A/B+/B/B-/C/D 6단계** — 전면 폐기
- **등급별 RR 공식 (A=1:2, B+=1:2.5, B=1:3)**
- **머스트패스 3개 질문** — 3-Gate로 통합
- **Short Float 5%+** — 업계 표준 10/20/30%로 교체
- **SBC 15%** — Net Dilution 3% 주지표로 교체
- **VIX 75%/100% 확률**
- **샘플 3개 100% 적중** — n=3 통계적 무의미

---

## 📋 프롬프트 ① — 풀 딥서치 (45분)

```
[티커] 미국 풀 딥서치 v4. 현재가 약 $[현재가].
7 Step 순서대로. 각 Step 내 호출 최대 병렬.
모든 수치 출처 명시. 확인 불가 시 "데이터 불충분".
등급 문자(A/B+) 금지. 카테고리·비중단계만 사용.

━━ STEP 0. 레짐 + K/G (3분) ━━
봇 병렬: get_regime + get_macro(mode="dashboard") + get_macro(mode="us_sector")
웹 조건부: VIX>25 or US10Y>5% 시 "S&P 500 weekly drivers"

<regime>공격/중립/위기</regime>
<kg>K주도 / G주도 / 혼합</kg>
<entry>K→1차 트랜치 OK / G→보류 / 혼합→50% 축소</entry>

━━ STEP 1. 아이디어 소싱 (5분) ━━
봇 병렬:
- get_rank(type="us_price", sort="rise"/"fall", n=20)
- get_us_scan(mode="watchlist"/"discovery")
웹 옵션: 분기말+45일 시 "13F filings Q[x] top buys"

━━ STEP 2. 3대 질문 속판 (7분) ━━
봇 병렬: get_us_ratings(consensus/trend, months=6) + get_news(sentiment=true)
웹 병렬: stockanalysis.com/stocks/[ticker]/forecast/ + "market share" + "moat"

<thesis>Bull 3줄 / Bear 3줄 / 컨센 6M delta</thesis>
※ Clement-Tse 2005 herding: bold analyst 상위 5명 trimmed mean. 단순 평균 금지.

━━ STEP 3. 재무건전성 (10분) ★밸류 이전 ━━
봇: 없음 (KIS 미국 PER/PBR 부정확 → stockanalysis 교차검증)
웹 병렬:
- stockanalysis.com/stocks/[ticker]/financials/
- stockanalysis.com/stocks/[ticker]/financials/balance-sheet/
- stockanalysis.com/stocks/[ticker]/financials/cash-flow-statement/
- (의심 시) sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=[ticker]&type=10-K

수동 계산:
- Altman Z'' (Safe>2.6 / Grey 1.1~2.6 / Distress<1.1)
- Beneish M (>-1.78 manipulator 의심)
- Runway = Cash/|Quarterly OpCashFlow| (적자기업)
- Rule of 40 = Rev Growth% + FCF Margin% (SaaS, GM≥70%)

Kill:
- Z''<1.1 AND (IC<1.5× OR ND/EBITDA>5×)
- M>-1.78 AND (감사변경 OR restatement OR AAER)

Yellow:
- Z'' 1.1~2.6 or M>-1.78 단독 → 조사 심화
- 기술·SaaS·바이오: Altman 제외, Runway<12M + Rule40<0 → Yellow

━━ STEP 4. 밸류에이션 (6분) ━━
봇 병렬: get_stock_detail + get_backtest(period=Y3)
웹 병렬:
- stockanalysis.com/stocks/[ticker]/financials/ratios/
- stockanalysis.com/stocks/[ticker]/statistics/
- "[ticker] vs [peer] EV/Revenue forward PE"

업종별 프레임:
- SaaS (GM≥70%, 구독≥80%): Rule of 40 + NRR
- 반도체 메모리: PBR + cycle-adjusted PER + Book-to-bill 1.0 경계
- 반도체 로직/AI (NVDA/AMD/TSM): Through-Cycle PER (10Y SOX 평균 17.5x)
  * NVDA: DC segment 30~45x fwd PE
  * AMD: SOTP (DC 30~35x + Client 15~20x + Embedded 15x)
- 바이오: Pipeline rNPV × PoS (DiMasi 2016: P1→승인 11.83%)
  할인율 10~15%, PoS + 고할인율 이중차감 금지
- 광모듈 (LITE/COHR): EV/Sales 4~6x + EV/EBITDA 15~25x
  P/E 부적합: SBC·감가상각, M&A GAAP 왜곡

Reverse DCF (Damodaran):
- Kill: Implied perpetual g ≥ 10Y T-yield
- Yellow: Implied 10Y CAGR > GDP ~5% 10년 지속
- ❌ "컨센×1.3배" 삭제 (Damodaran·Rosenbaum·McKinsey·CFA 근거 전무)

━━ STEP 5. 수급 (7분) ━━
봇: get_us_ratings(events, days=30)
웹 병렬:
- openinsider.com/screener?s=[ticker]
- whalewisdom.com/stock/[ticker]
- finviz.com/quote.ashx?t=[ticker]
- barchart.com/stocks/quotes/[ticker]/unusual-options-activity

신호:
- 내부자 (Cohen-Malloy-Pomorski 2012): opportunistic P-code 3인+ + CEO/CFO/Chair = 1M +2~3.8%
- 13F (Agarwal 2013): 45일 stale, 10+펀드 신규 + ±10% 매집가 근처
- Short Float (업계 표준):
  10% 경계 / 20% 고위험 / 30% 극단
  >20% + 내부자 매도 → Pass
  >20% + 내부자 매수 → squeeze 후보
- UOA: Call/Put>2 + 만기 30D+ = Bull

━━ STEP 6. 기술적 (4분) ━━
봇 병렬:
- get_stock_detail(period="D250")
- get_stock_detail(mode="volume_profile", period="Y1")
- get_stock_detail(mode="orderbook")
- get_backtest(strategy="ma_cross")

주의:
- VP 미국 한계: 어닝갭·ETF arb·옵션헷지 무력화 → 1년 대신 3~6개월 VP, 대형주는 보조지표만
- PEAD 대형주 소멸 (Martineau 2021 CFR): 2006년 이후. 소형주<$2B만 잔존

━━ STEP 7. 3-Gate + 비중 결정 (3분) ━━
<gate_check>
  Gate 1 (산업 흐름): YES/NO + 정량
  Gate 2 (리더): YES/NO + 점유율·해자
  Gate 3 (근거): YES/NO + 밸류/모멘텀/수급, 수치
  결론: 3/3→Standard~Core / 2/3→Starter / <2/3→Pass
</gate_check>

봇: simulate_trade + set_alert + manage_watch

<position>
  Starter 3~5% / Standard 7~12% / Core 15~25% / 예외 25~35% / 금지 >35%
  섹터 상한 없음 (킬스위치 대체)
  신규 항상 Starter부터
</position>

<stop_target>
  손절: 메인 -15% / 가치 -12% / 스윙 -7~10%
  목표: 2Y Fwd EPS × Sector Multiple
  EPS 보수 조정: 성장주 -20~30%, 일반 -10~20% (Bradshaw 2011)
  1차 진입: FV -20~30% 할인 (바이오 30%+, Graham MOS)
</stop_target>

출력 템플릿:
<judgment>
  <regime>공격/중립/위기 + VIX + K/G</regime>
  <ticker>[ticker]</ticker>
  <gate>①__ ②__ ③__ (__/3)</gate>
  <safety>PASS/YELLOW/KILL</safety>
  <valuation>섹터프레임 / Reverse DCF / FV 2Y</valuation>
  <supply>내부자 / 13F / Short / UOA</supply>
  <decision>카테고리 / 비중단계 / 비중% / action</decision>
  <stop_target>손절 / 목표 2Y / 1차진입</stop_target>
  <thesis>Bull / Bear / Invalidation</thesis>
  <sources>학술 / 웹사이트 / data_gaps</sources>
</judgment>
```

---

## ⚡ 프롬프트 ② — 빠른 판정 (10분)

```
[티커] 미국 빠른 3-Gate 스크린. 현재가 약 $[현재가].
봇 병렬: get_regime + get_macro(dashboard) + get_us_ratings(consensus/trend) + get_news
웹: stockanalysis.com/stocks/[ticker]/financials/ratios/ + finviz.com/quote.ashx?t=[ticker] + openinsider.com/screener?s=[ticker]
봇: get_stock_detail(D120) + simulate_trade

3-Gate 속판:
Gate 1: 섹터 ETF 1M 상대강도 S&P > 0
Gate 2: 컨센 Buy 비중 > 60% + 상향 추세
Gate 3: FCF margin>0 OR 내부자 P-code 클러스터 OR Short squeeze 조건

Quick Kill:
- Rule40<20 AND FCF margin<0 → Pass
- Short Float>20% AND 내부자 매도 우세 → Pass
- 컨센 6M 하향 + 52주고가 -30% 이탈 → 재검토

출력: Gate X/3, Safety, 비중단계, action
```

---

## 🔁 프롬프트 ③ — 분기 재검증 (Core 전용)

```
[티커] 미국 분기 재검증. 보유 Core. 종목당 8분.
트리거: 어닝 발표 후 2주 이내 OR Core 악재 3거래일 쿨링 후

봇: get_us_ratings(trend, months=3)
웹:
- stockanalysis.com/stocks/[ticker]/financials/?p=quarterly
- sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=[ticker]&type=10-Q
- whalewisdom.com/stock/[ticker]
- openinsider.com/screener?s=[ticker]

확인:
1. 컨센 3M 방향 + 목표가 평균 Δ%
2. Revenue YoY / FCF / GM 분기 방향 / 가이던스 Δ
3. 13F top holder 증감 + 내부자 매도 클러스터
4. 현재 EV/Rev vs 3M 전, Reverse DCF 내재 g% vs 당시

매도 4트리거:
T1. Thesis 훼손 → 즉시 청산 (쿨링 예외)
T2. 리더십 상실 (2분기 연속 미스+가이던스 하향) → 부분 매도 30~50%
  * 대형주: 발표 후 2거래일 내 판단 (PEAD 소멸)
  * 소형주 <$2B: 60일 드리프트 기대
T3. 밸류 극단 (Implied g ≥ 10Y yield OR Fwd PE > 5y avg×2) → 부분 정리 30~50%
T4. 단일 >35% → 리밸런싱 (섹터 상한 없음)

쿨링: Core 악재 당일 매도 금지, 3일 후 재평가 (T1 확정 예외)

출력: thesis status / trigger check / action / next review
```

---

## 📚 학술·실무 근거

| 원칙 | 출처 |
|---|---|
| 3-Gate | Buffett 1995 AGM; Mauboussin-Callahan 2024 |
| 비중 3단계 | Kelly(1956) Half Kelly; Ivković-Sialm-Weisbenner(2008) JFQA |
| 집중 투자 | Cohen-Polk-Silli 2010 "Best Ideas"; Cremers-Petajisto 2009 RFS |
| Altman Z'' | Altman 1968/2013; MDPI 2025 무형자산 편향 |
| Beneish M | Beneish 1999 FAJ 55(5); FP 17.5% |
| Reverse DCF | Damodaran *Investment Valuation* Ch.12 |
| PEAD | Bernard-Thomas 1989 JAR; Martineau 2021 CFR (대형주 소멸) |
| 내부자 | Cohen-Malloy-Pomorski 2012 JoF 67(3); Kang-Kim-Wang 2018 |
| 13F | Agarwal-Jiang-Tang-Yang 2013 JoF 68(2) |
| Core 쿨링 | Jegadeesh 1990 JoF 45(3) |
| SaaS | Brad Feld 2015 "Rule of 40" |
| 반도체 | Regions IM 2019 (SOX 17.5x) |
| 바이오 | DiMasi-Grabowski-Hansen 2016 JHE 47 |
| 애널 bias | Barber 2001 JoF 56; Clement-Tse 2005 JoF 60(1) |
| Graham MOS | Graham-Dodd *Security Analysis* 1934 |
| Anthropic | platform.claude.com/docs/en/build-with-claude/prompt-engineering |
| LLM anchoring | Shaikh 2024 CBEval arXiv:2412.03605; Lou & Sun 2024 arXiv:2412.06593 |

## ⚠️ 실무 관행 라벨

- Short Float 10/20/30%: 업계 관행
- Rule of 40 SaaS 한정: VC 휴리스틱
- EV/Sales 4~6x, EV/EBITDA 15~25x: 광모듈 업계 관행
- 컨센 Buy 비중 >60%: 실무 관행
- -7~10% 스윙 손절: O'Neil 7-8%, Minervini 6-10%

---

## 변경 이력

- **2026-04-24 v4**: 전면 개정
  - 파일명: US_DEEPSEARCH_v3.md → US_DEEPSEARCH.md
  - Reverse DCF 1.3배 → Damodaran perpetuity cap 교체
  - Beneish M 단독 Kill → 복합 조건 강등
  - A/B+ 등급 → 3-Gate + 비중 3단계 + 카테고리
  - Short Float 5% → 10/20/30% 업계 표준
  - SBC 15% → Net Dilution 3% 주지표
  - 섹터 상한 폐지 (킬스위치 대체)
  - PEAD 대형주 소멸 반영
  - Altman Z'' 기술·SaaS·바이오 제외
  - VP 미국 한계 명시
  - Clement-Tse herding bias
  - 샘플 3개 case study 표시
  - Anthropic positive instruction + XML
- 2026-04-17 v3: K/G 국면 STEP 0 추가
- 2026-04-15 v3: 재무→밸류 순서 (Piotroski)
