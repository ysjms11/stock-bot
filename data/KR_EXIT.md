# KR_EXIT.md — 한국 집중투자자용 매도 판단 프레임

> **적용 범위**: 한국 종목(KOSPI/KOSDAQ) **이득 영역(+10% 이상)** 매도 판단 전용.
> 손절(-7~10%)은 별도 체계, 평단 ±5% 박스권은 본 프레임 제외.
> **최우선 목표**: LS ELECTRIC식 LLM 조기매도 편향 차단(5/5 HOLD 조건에서 매도 추천 → +47~84% 추가 상승 놓침 재발 방지).
> **매수 프레임 정합**: KR_DEEPSEARCH.md 10-STEP 구조와 일관 — Pre-commitment·Devil's Advocate·PDF 게이트·F/G-Score·3-Gate 구조 동일 용어 사용.

---

## Section 1. LLM 편향 차단 10규칙 (매도 시 강제 적용)

매도 시점은 LLM의 가장 약한 영역이다. **사용자가 "팔까?"라고 물으면 LLM은 동조(sycophancy)하고, "확실해?"라고 압박하면 의견을 뒤집는다(FlipFlop)**. 다음 10규칙은 매도 직전 모든 단계에서 검증된다.

**규칙 1 — Sycophancy 금지.** Sharma et al. (2023, *Towards Understanding Sycophancy in Language Models*, arXiv:2310.13548, ICLR 2024)은 GPT-4·Claude 2·ChatGPT-3.5·LLaMA 2-70B 5개 모델 모두에서 RLHF가 "사용자 견해 동조 응답을 선호"하도록 학습됨을 입증했다. **사용자가 "팔까"라고 입력한 순간 LLM 출력의 50% 이상이 매도 동조로 편향**될 수 있으므로, 매도 질의는 항상 중립 프레이밍("매도/HOLD/추가매수 중 근거 기반 선택")으로 제출한다.

**규칙 2 — FlipFlop 차단.** Laban et al. (2023, arXiv:2311.08596) FlipFlop Experiment에서 10 LLM × 7 task × 67,640 시행 결과: "정말이야?"형 challenger 1회 후 평균 정확도 **-17%p, flip 비율 46%, Claude V2 -34%p**. **매도 판단 후 LLM에게 "확실해?"를 묻지 않는다**. 의심이 든다면 STEP 7 Devil's Advocate를 별도 사이클로 실행하되, 동일 세션에서 결정을 뒤집지 않는다.

**규칙 3 — Pre-commitment.** Ariely & Wertenbroch (2002, *Psychological Science* 13(3): 219–224)은 자기부과 deadline이 procrastination 통제에 유효함을 입증했다. 매도 의사결정은 **STEP 0에서 `set_alert(log_type='decision')`로 박제 후 진행** — 사후 합리화로 결심을 흐릴 수 없도록 한다.

**규칙 4 — Devil's Advocate 강제.** Kim, Kim & Yoon (2024, *Findings of ACL 2024*, pp. 1885–1897, DEBATE) 논문은 multi-agent NLG 평가에 반대측 critic을 강제 발화하면 SummEval/TopicalChat에서 G-Eval 대비 우수함을 보였다. 본 프레임에서는 **매도 판단 직전 "왜 지금 매도하면 안 되는가"를 별도 문단으로 강제 작성**한다.

**규칙 5 — Intrinsic reflection 금지.** Huang et al. (2023, *Large Language Models Cannot Self-Correct Reasoning Yet*, arXiv:2310.01798, ICLR 2024, Google DeepMind+UIUC)은 외부 피드백 없는 self-correction은 추론 정확도를 **저하**시킴을 입증했다. **LLM이 자기 결정을 자체 점검하는 것만으로 매도 편향을 교정할 수 없다** — 외부 게이트(룰·PDF·포트폴리오 데이터·봇 도구)가 필수다.

**규칙 6 — Bull-market 조기매도 편향 경고.** Li, Kim, Cucuringu & Ma (2025, arXiv:2505.07078, KDD 2026 accepted)은 20년 100+ 종목 백테스트에서 **LLM 전략이 상승장에서 과도하게 보수적이 되어 passive benchmark를 하회**함을 보였다. **3주 +30% 이상 급등 후 매도 권유는 이 편향의 직접 발현**일 가능성이 높으므로, 본 프레임은 O'Neil 8-week hold 룰(규칙 10 일부)을 강제 게이트로 둔다.

**규칙 7 — 한국 매수 편향 93% 환경 보정.** 자본시장연구원 김준석 (2025, *자본시장포커스* 2025-15호, 2025.07.21)에 따르면 한국 애널리스트 매수의견 비중은 2000년대 67% → 2010년대 89% → **2020년대 93%**까지 상승했다. 김준석 (2026, *자본시장포커스* 2026-02호)에서는 "투자의견·TP 컨센서스 초과수익률은 **2013년 이후 통계적으로 관찰되지 않으며**, 중소형주에서만 잔존"임을 표본 2000–2024 약 70만 건으로 입증했다. 따라서 **"매수 의견 일색 = 매도 신호"의 약한 증거**로 사용하되, 단독 트리거 금지. Womack (1996, *J. Finance* 51(1): 137–167)에서 미국 sell 추천의 post-event drift -9.1%/6개월 지속 결과는 한국 1989–1991 외삽이 아니므로 보조 근거로만 인용한다.

**규칙 8 — Category 분기.** 매수 프레임의 카테고리(메인/가치/스윙)별 매도 룰이 다르다. **메인은 8-week hold + 3경로 엄격, 스윙은 모멘텀 종료 즉시 매도, 가치는 F-Score 급락 시만**. 같은 신호도 카테고리에 따라 해석이 다르다.

**규칙 9 — 수치 임계 투명성.** 모든 매도 트리거의 수치는 **학술 근거 또는 "실무 관행" 라벨링**과 함께 명시. 학술 근거 없는 임계는 사용자 백테스트 영역으로 분리한다.

**규칙 10 — 3경로 룰: 매도 유일 정당화 경로.** Fisher (1958, *Common Stocks and Uncommon Profits*, Ch.6 "When to Sell — and When Not To")의 3가지 매도 사유 외에는 매도 정당화 금지. **단순 valuation 고평가는 Fisher가 명시적으로 부정**한 매도 사유다(원문: "trying to measure with greater preciseness than is possible"). Section 2 의사결정 트리에 구체 적용.

---

## Section 2. 3경로 의사결정 트리 (한국 적용)

매도는 **3경로 중 1개 이상이 명확히 발동**할 때만 정당화된다. 0/3이면 무조건 HOLD.

### 경로 1 — Thesis Invalidation (당초 매수 이유 소멸)

**학술 정의**: Fisher (1958, Ch.6) "(1) a mistake has been made in the original purchase, (2) the company no longer meets the 15 standards" — 매수 시점 thesis가 깨졌거나, 기업이 변질된 경우.

**한국 즉시 청산 트리거** (DART 공시 기반, 한국거래소 상장규정 §47·§48):

| 카테고리 | DART 키워드 | 행동 |
|---|---|---|
| 감사의견 비적정 (거절·부적정·한정) | "감사보고서" + "거절/부적정/한정" | **당일 청산** (정리매매) |
| 자본전액잠식 / 50% 이상 잠식 | "자본잠식" 정정 | **즉시 매도** |
| 횡령·배임 (자기자본 5% 또는 대규모 2.5% 이상) | "횡령" / "배임" | **즉시 매매정지 → 청산** |
| 분식회계 (감리지적 + 과징금) | "감리결과지적사항" | 즉시 매도 |
| 상장폐지·관리종목지정 | "상장폐지" / "관리종목" | 정리매매 7일 |

**봇 매핑**: `get_dart(mode='disclosure_list', ticker, days=30)`로 매도 판단 시점 직전 30일 공시를 자동 스캔. 위 키워드 1개라도 히트 시 STEP 1 즉시 EXIT.

**완화된 thesis 손상**:  EPS 컨센서스 30일 -10% 이상 하향 + TP 하향 리포트 비중 >50% → 부분 비중 축소(50%) 검토. 단독으로 전량 청산 불가(Devil's Advocate 통과 필수).

### 경로 2 — Technical Exit (기술적 매도 룰)

**학술 정의**: O'Neil (2009, *How to Make Money in Stocks*, 4th ed., Ch.10 "When You Must Sell and Cut Every Loss... Without Exception"). 한국 시장 적용 룰:

- **50일 이평선 대량거래 동반 결정적 이탈 + 3거래일 내 반등 실패** → 매도 검토 (이득 영역에서 trailing stop 발동).
- **8-week hold 룰**: "3주 안에 +20% 이상 급등한 종목은 최소 8주 보유" — 사용자 케이스 SK하이닉스 3주 +51%(807K → 1,222K, 2026-04-24 검증)에 직접 발동, **HOLD 강제**.
- **부분 익절 +20~25%**: 1차 매도 표준. 단 8-week hold 발동 시 예외.

**한국 가격제한폭 ±30% (2015.6.15 시행) 보정**: 종목별 일일 ATR이 미국 대비 1.5~2배. 미국식 8% trailing stop 직접 이식 시 whipsaw 위험. 권장:
- 대형주: ATR(14) × 2.5~3.0
- 중소형주: ATR(14) × 3.0~3.5 + VI 발동 빈도 가중
- *(임계는 학술 직접 근거 없는 **실무 관행**, 사용자 종목별 백테스트 권장)*

### 경로 3 — 기회비용 (한국 세제 반영)

**학술 정의**: Munger (1994, *A Lesson on Elementary, Worldly Wisdom*, USC Marshall Guest Lecture)는 "30년 15% 복리, 매년 35% 자본이득세 vs 마지막 1회 35% → 약 3.5%p/yr 손실"을 제시했다. **이 모델은 미국 35% 자본이득세 가정**이다.

**한국 일반투자자(비대주주) 적용 — 정정**: 한국 상장주식 양도세는 **일반투자자 비과세** (대주주 50억 이상 별도). 거래세는 **2025년 0.15% (코스피 농특세 0.15% + 거래세 0.00%, 코스닥 거래세 0.15%), 2026년 0.20% (코스피 0.05%+0.15%, 코스닥 0.20%)** — 기획재정부 2025-12-01 시행령 개정안 기준. 사용자 매수 프레임 표기 "0.18%"는 **2024년 구세율**이므로 본 프레임에서는 **0.15%(2025) / 0.20%(2026)** 사용.

**결론**: Munger의 "compounding의 적 = 자본이득세" 직접 인용은 한국 일반투자자에 부적합. **한국 기회비용 경로는 거래세(0.15~0.20%) + 슬리피지 + 재진입 타이밍 리스크 + 심리적 회전율 비용**으로 재구성한다.

**기회비용 매도 합리적 임계** (실무 기준):
- 보유 종목 **연 기대수익률** vs **명백히 우월한 대체 종목 기대수익률** 격차 ≥ **5%p/yr 이상 + 12개월 이상 지속 예상** 시에만 발동.
- 단순 "다른 게 더 올랐다"는 sycophancy → 매도 금지(규칙 1).

---

## Section 3. 한국 시장 특화 매도 시그널 표

| # | 시그널 | 임계 | 학술 근거 | 가중 |
|---|---|---|---|---|
| 1 | DART 감사의견 비적정/횡령/자본잠식 | 발생 즉시 | 상장규정 §47·§48 | **즉시 청산** |
| 2 | 컨센서스 EPS 30일 하향 | -5% 이상 | 김준석 (2026) KCMI 2026-02호 | 강 |
| 3 | TP 도달율 (대형주) | 90% 도달 | 김준석 (2026) — 단 2013 이후 정보가치 약화 | 약~중 |
| 4 | TP 도달율 (중소형주) | 90% 도달 | 김준석 (2026) — 중소형주 정보가치 잔존 | 중 |
| 5 | TP 컨센서스 분산도(StdDev/Mean) | >25% | Diether, Malloy, Scherbina (2002) JF 57(5): 2113–2141 | 중 |
| 6 | 외국인 5일 연속 순매도 + 52주 고가 ±5% | 동시 발생 | 52주 고가 anchoring: Goh & Jeon (2017) PBFJ 44: 150–159; 5일 룰은 **실무 관행** | 중 (조합 시) |
| 7 | 신용잔고율 급증 | 30일 +50% | **실무 관행** (학술 임계 미확인) | 중 |
| 8 | 대차잔고 비율 | 시총 대비 >10% & 30일 +50% | 공매도 빌드업 패턴 — **실무 관행** | 중 |
| 9 | 임원·최대주주 매도 클러스터 | 30일 내 2건 이상 (사전공시제 1%/50억 이상) | 자본시장법 §173조의3 (2024.7.24 시행); 미국 Cohen, Malloy & Pomorski (2012) JF 67(3): 1009–1043 외삽 | 강 |
| 10 | PEAD 강도 (한국 대형주) | 호재 후 60일 내 약화 신호 | 강형구 외 (2022) 한국증권학회지 51(3): 309–334 — 대형주 신속 반응, 소형주만 잔존 | 약 (대형주) |
| 11 | 50일선 대량거래 동반 이탈 | 결정적 이탈 + 반등 실패 | O'Neil (2009) Ch.10 | 강 |
| 12 | 200MA 대비 +50% 이상 + 3주 +30% | 동시 | O'Neil 8-week hold rule 예외 검토 | 약 (단독 매도 금지) |

**중요 주의**: ⚠️ **시그널 6의 "외국인 5일 연속 순매도"는 학술 직접 근거 없음**. Choe, Kho & Stulz (1999, *J. Financial Economics* 54(2): 227–264)는 한국 외국인의 **순매수 시점 positive feedback trading** 결론이며, 매도 시점이 시장 destabilize 한다는 결론은 명시적으로 **부정**된다(원문: "no evidence that trades by foreign investors had a destabilizing effect"). 따라서 "외국인 매도 = 추가 하락"은 약한 가설로만 사용.

---

## Section 4. 매도 판단 STEP 0~8 (한국 봇 도구 매핑)

라이트 체크 5분 / 빠른 판정 10분 / 풀 매도 판정 30분 — 시간 배분은 **한국 거래시간(09:00-15:30) 내 단계적 실행** 가정.

### STEP 0 — Pre-commitment 박제 (1분)
```
set_alert(log_type='decision', date=YYYY-MM-DD, regime='경계/공격/방어',
  grades={ticker: {grade:'B', change:'A→B', reason:'과열/EPS둔화/...'}},
  actions=['종목 X% 비중 축소' or 'HOLD 유지'], notes='시장 국면+이유')
```
규칙 3 적용. 매도 결심은 **봇 데이터 조회 전**에 박제 → 데이터 사후 합리화 차단.

### STEP 1 — Thesis 재검증 (5분)
- `get_dart(mode='disclosure_list', ticker, days=30)` — Section 2 경로 1 키워드 자동 스캔. 1건이라도 히트 시 즉시 EXIT.
- `get_news(ticker, n=20, sentiment=true)` — **부정 비율 >30% 시 매도 가중**.
- `get_dart(mode='read', ticker)` — 사업보고서 본문 (필요 시).

### STEP 2 — 기술 (3분)
- `get_stock_detail(ticker, period='D250')` — 50MA/200MA/52주 고가 위치.
- `get_stock_detail(ticker, mode='volume_profile', period='Y1')` — 매물대 확인.
- `get_market_signal(mode='vi')` — VI 발동 빈도 (변동성 급증 신호).

판정: **52주 고가 ±5% + 200MA 대비 +50% 이상 + 3주 +30%** 동시 발동 시 **8-week hold 룰 강제** (HOLD 가산).

### STEP 3 — 컨센서스 (PDF 게이트, 8분)
- `get_consensus(ticker)` — TP/EPS 30일 변화.
- `manage_report(action='list', ticker, days=30)` — 최근 리포트 목록.
- `read_report_pdf(ticker)` × **최소 2건, 다른 증권사 우선** — TP 산출 방식, EPS 추정, 리스크 섹션 비교.

**PDF 게이트 통과 조건** (매도 시에도 매수와 동일 강제):
- 최소 2건 PDF 직접 읽기 — Diether, Malloy & Scherbina (2002) 근거.
- 다른 증권사 혼합 — group-think 회피.
- 한국 IB 관계 매도의견 회피 경향 보정 → 외국계 + 국내 증권사 혼합 권장.

### STEP 4 — 수급 (5분)
- `get_supply(mode='history', ticker, days=20)` — 외인·기관 20일 누적.
- `get_market_signal(mode='credit', ticker, days=20)` — 신용잔고 추이.
- `get_market_signal(mode='lending', ticker, days=20)` — 대차잔고.
- `get_market_signal(mode='short_sale', ticker, days=20)` — KRX NSDS 공매도 (2025.3.31 재개 이후 데이터).

### STEP 5 — 내부자 (3분)
- `get_dart(mode='insider', ticker, days=60)` — 임원·주요주주 매도 클러스터.

**한국 사전공시제 30일 룰 주의**: 자본시장법 §173조의3 (2024.7.24 시행)에 따라 1%/50억 이상 거래는 **30일 사전공시** — 미국 Form 4 익일 보고와 달리 사전 정보가 시장에 선반영될 수 있다. 미국식 Cohen-Malloy-Pomorski (2012) "익일 시그널" 직접 이식 불가.

### STEP 6 — 3경로 판정 (3분)
세 경로 점수 합산 (-10 ~ +10):
- 경로 1 (Thesis): STEP 1+3 합산
- 경로 2 (Technical): STEP 2 합산
- 경로 3 (기회비용): 별도 평가 (보통 +0)

| 합계 | 액션 |
|---|---|
| ≤ -10 | 즉시 매도 |
| -5 ~ -9 | 50% 비중 축소 |
| -4 ~ +4 | **HOLD** |
| ≥ +5 | 추가매수 검토 (별도 매수 프레임) |

**0/3 발동 시 무조건 HOLD** (LS ELECTRIC 케이스 핵심).

### STEP 7 — Devil's Advocate (5분)
규칙 4 적용. 별도 문단으로 다음 질문에 답한다:
- "왜 지금 매도하면 안 되는가?"
- "재진입 비용(슬리피지+세금+심리)은?"
- "슈퍼사이클 한가운데 sell sentiment인가?" (FINSABER bull-market bias 자가 점검)
- `simulate_trade(sells=[{ticker, qty, price}])`로 비중 변화·RR 사전 확인.

### STEP 8 — 기록 (2분)
```
set_alert(log_type='trade', side='sell', ticker, qty, price, grade='B',
  reason='과열+EPS둔화+TP90%', date=YYYY-MM-DD)
write_file('thesis/{ticker}.md', updated_content)  # thesis 갱신
set_alert(log_type='delete', ticker, market='KR')   # 매도 시 알림 정리
```

---

## Section 5. 한국 케이스 (LS ELECTRIC + SK하이닉스 재검증)

### Case A — LS ELECTRIC (010120): LLM 조기매도 추천 실패

**조건** (사용자 보고 시점 기준): 5/5 HOLD 충족 — Thesis 유효(HVDC·데이터센터 수주), 목표가 미도달, 과열 없음, 손절 미터치, 200MA 위.

**3경로 판정**: 0/3 발동.
- 경로 1: thesis 유효 — HVDC 수주 모멘텀 지속, EPS 추정 상향 (유안타 2026.4.14 TP 260,000원 상향, KB 2026.4.14 TP 240,000원 상향, +79.1%).
- 경로 2: 50일선 위, 200MA 위, 손절 미터치.
- 경로 3: 명백히 우월한 대체 종목 미확인.

**LLM 진단**: 조기매도 추천은 다음 편향의 **합성 발현**:
1. **Sycophancy** (규칙 1): 사용자 "팔까?" 입력에 동조.
2. **Intrinsic reflection 오류** (규칙 5): "충분히 올랐으니 팔아야"라는 자기 추론으로 외부 데이터 무시.
3. **Bull-market 조기매도 편향** (규칙 6, FINSABER Li et al. 2025): 상승장에서 보수 편향.

**차단 룰**: **3경로 0/3 = 무조건 HOLD**. 이후 +47~84% 상승 미스 사례를 본 프레임의 핵심 교정 데이터로 등록. (단, 봇 일봉 데이터 검증 결과 2026-04-24 시점에서는 200MA 대비 +60% 이상 상회 + 3주 +26% 단기 급등으로 **과열 항목 재평가 필요**할 수 있음 — 부분 익절 검토 가능 영역으로 진입. 단 8-week hold 룰 발동 중이라면 HOLD 우선.)

### Case B — SK하이닉스 (000660): 2026-04-24 HOLD 판정

**검증된 데이터** (KIS API 일봉):
- 2026-03-31 종가 807,000원 → 2026-04-24 종가 **1,222,000원**, **3주 +51.4%** ✅
- 52주 고가 1,267,000원 (2026-04-23 장중) — 신고가 -3.5%
- 1Q26 매출 52.6조원 (+198% YoY), 영업이익 37.6조원 (+405% YoY) — 어닝 비트.
- 컨센서스 TP: KB 190만, SK 200만, 메리츠 170만, 삼성 180만, IBK 110→180만 (4월 일제 상향).
- HBM3E 점유율 ~62%, HBM4 70% 예상 (UBS).

**3경로 판정**: 0/3.
- 경로 1: thesis 3/3 유효 — HBM 슈퍼사이클, 1Q 어닝 비트, NVDA Vera Rubin SOCAMM2 양산.
- 경로 2: 50일선 위, 200MA 위, **8-week hold 룰 강제 발동** (3주 +51%).
- 경로 3: 반도체 슈퍼사이클 한가운데 — 기회비용 경로 비활성.

**보조 데이터**:
- 사용자 목표 93% 도달 / 컨센 TP 80% 막 통과 → 김준석 (2026)에 따라 **대형주 TP 정보가치 약함**, 단독 매도 트리거 불가.
- 경영진 3명 클러스터 매수 17일 전 — 매수 신호 (사전공시제 §173조의3에 따른 사전공시일이 매수 신호로 기능).
- 외국인 5일 중 4일 순매도 — Section 3 시그널 6 (실무 관행), **단독 매도 트리거 불가** (Choe-Kho-Stulz 1999 결과 부정).
- Forward PEG 0.016 → 극단적 저PEG, 기회비용 경로 활성 불가.

**판정**: **HOLD + 8-week hold 룰 강제 + Trailing stop 1,100K 수준 상향**. 부분 익절은 8주 경과 후(2026-06 중순 이후) 재평가.

---

## Section 6. 확신 없는 항목 (정직 명시)

본 프레임은 학술 근거 강도를 5단계로 라벨링한다. 다음 항목은 **사용자 직접 백테스트 또는 추가 검증**이 필요하다.

**검증 부분 실패 / 1차 출처 본문 미확인**:
- Eom, Hahn & Sohn (2019, *Pacific-Basin Finance Journal* 53: 379–398) "한국 호재 60일 / 악재 20일 비대칭" 정확 수치는 abstract에서 미확인. 본문 PDF 직접 확인 전까지 **수치 인용 자제**. 일반적 한국 PEAD 측정은 60-day CAR 표준 (Shin et al. 2019, *Sustainability* 11(18): 5137).
- 강형구·전진규 (2022, 한국증권학회지 51(3): 309–334) 공동저자 매칭 부분 불확실 (한양대 repo 파일명에 강형구 명시, 교신저자 경희대 j.b.w 이메일 — 전진규 동국대 소속과 매칭 추가 확인 필요). 단 **연구 결과 자체는 신뢰**: 2003 Q1–2019 Q4, 한국 대형주 PEAD 약화/소형주 잔존.
- O'Neil (2009) 8-week hold 룰의 정확한 페이지/판본 출처 미확인. 서적 + Investor's Business Daily 공식 해설 간접 인용.

**검증 실패 / 실무 관행으로 라벨링**:
- 외국인 5일 연속 순매도 + 52주 고가 ±5% 조합의 매도 트리거 학술 근거 — 미확인. **실무 관행**.
- 신용잔고율 10%+ 임계 학술 근거 — 미확인. **실무 관행**.
- 대차잔고 30일 +50% 임계 — **실무 관행**, 한국 공매도 재개 1년 데이터로 백테스트 표본 부족.
- 한국 내부자 매수 클러스터 (CEO/CFO/Chair 3인+) 학술 재현 논문 — 미확인. Cohen-Malloy-Pomorski (2012) 미국 결과 외삽.
- 한국 반도체 대형주 급등 후 1M/3M/6M 경로 학술 분석 — 미확인. KRX 일봉 + 증권사 사이클 리포트 보강 권장.
- 한국어/한국 종목 LLM Sycophancy 또는 FlipFlop 직접 측정 연구 — 미발견. 영어 SOTA LLM(Claude V2/GPT-4 시점) 측정치 외삽.

**Munger (1994) 정정**: 사용자 매수 프레임의 "compounding의 적 = 양도세" 인용은 미국 35% 자본이득세 가정. **한국 일반투자자(비대주주)는 양도세 비과세 + 거래세 0.15%(2025)/0.20%(2026)** 이므로 직접 적용 부적합. 미국 주식 보유 한국 거주자(해외주식 22% 분리과세)에는 적용 가능.

**거래세 정정**: 매수 프레임 "0.18%"는 2024년 구세율. **2025년 0.15%, 2026년 0.20%** (기획재정부 2025-12-01 시행령).

---

## Section 7. 학술·실무 근거 인용

**LLM 편향 (10규칙)**:
1. Sharma, M., Tong, M., Korbak, T., et al. (2023). *Towards Understanding Sycophancy in Language Models*. arXiv:2310.13548. ICLR 2024.
2. Laban, P., Murakhovs'ka, L., Xiong, C., & Wu, C.-S. (2023). *Are You Sure? Challenging LLMs Leads to Performance Drops in The FlipFlop Experiment*. arXiv:2311.08596. (Claude V2 -34%p, 평균 -17%p, flip 46%)
3. Ariely, D., & Wertenbroch, K. (2002). Procrastination, Deadlines, and Performance: Self-Control by Precommitment. *Psychological Science* 13(3): 219–224.
4. Kim, A., Kim, K., & Yoon, S. (2024). DEBATE: Devil's Advocate-Based Assessment and Text Evaluation. *Findings of ACL 2024*: 1885–1897. arXiv:2405.09935.
5. Huang, J., Chen, X., Mishra, S., et al. (2023). *Large Language Models Cannot Self-Correct Reasoning Yet*. arXiv:2310.01798. ICLR 2024.
6. Li, W. W., Kim, H., Cucuringu, M., & Ma, T. (2025). *Can LLM-based Financial Investing Strategies Outperform the Market in Long Run?* arXiv:2505.07078. KDD 2026 accepted. (FINSABER 프레임워크)
7. Womack, K. L. (1996). Do Brokerage Analysts' Recommendations Have Investment Value? *Journal of Finance* 51(1): 137–167.

**3경로 의사결정 트리**:
8. Fisher, P. A. (1958). *Common Stocks and Uncommon Profits*. Harper & Brothers. Ch.6 "When to Sell — and When Not To."
9. O'Neil, W. J. (2009). *How to Make Money in Stocks*. 4th ed., McGraw-Hill. Ch.10 (Sell Rules).
10. Munger, C. T. (1994, April 14). *A Lesson on Elementary, Worldly Wisdom As It Relates To Investment Management & Business*. USC Marshall School of Business Guest Lecture.
11. Piotroski, J. D. (2000). Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers. *Journal of Accounting Research* 38(Supplement): 1–41. (F-Score)
12. Mohanram, P. S. (2005). Separating Winners from Losers among Low Book-to-Market Stocks Using Financial Statement Analysis. *Review of Accounting Studies* 10(2-3): 133–170. (G-Score)

**한국 시장 특화**:
13. 김준석 (2026). 애널리스트 투자의견과 목표주가의 투자가치. *자본시장포커스* 2026-02호 (자본시장연구원, 2026.01.19 발간). 표본 2000–2024, 약 70만 건. **2013년 이후 컨센서스 초과수익률 통계적 소멸, 중소형주에서만 잔존**.
14. 김준석 (2025). 애널리스트의 낙관적 편향. *자본시장포커스* 2025-15호 (자본시장연구원, 2025.07.21). **매수의견 비중 2020년대 93%**.
15. 이승희, 주소현, 박광수 (2013). 애널리스트 목표주가와 실제주가의 선도-지연 관계. *Journal of The Korean Data Analysis Society* (한국자료분석학회). 표본 2000–2010, 상승기 +19.2% / 하락기 +45.6% 괴리도. (※ 사용자 매수 프레임의 학회명 *Journal of the Korean Data and Information Science Society* 표기는 정정 필요)
16. 강형구·전진규 (2022). 한국 PEAD 후속 연구. *한국증권학회지* 51(3): 309–334. 표본 2003 Q1–2019 Q4. **대형주/관심 중소기업 신속 반응, 저관심 중소기업만 PEAD 잔존**.
17. Eom, Y., Hahn, J., & Sohn, W. (2019). Individual investors and post-earnings-announcement drift: Evidence from Korea. *Pacific-Basin Finance Journal* 53: 379–398. DOI:10.1016/j.pacfin.2018.12.002.
18. Goh, J., & Jeon, B. (2017). Post-earnings-announcement-drift and 52-week high: Evidence from Korea. *Pacific-Basin Finance Journal* 44: 150–159.
19. Martineau, C. (2022). Rest in Peace Post-Earnings Announcement Drift. *Critical Finance Review* 11(3-4): 613–646.
20. Choe, H., Kho, B.-C., & Stulz, R. M. (1999). Do foreign investors destabilize stock markets? The Korean experience in 1997. *Journal of Financial Economics* 54(2): 227–264. **외국인 매도 destabilize 가설 기각** — 인용 시 주의.
21. Cohen, L., Malloy, C., & Pomorski, L. (2012). Decoding Inside Information. *Journal of Finance* 67(3): 1009–1043. (미국, 한국 직접 재현 미확인)
22. Diether, K. B., Malloy, C. J., & Scherbina, A. (2002). Differences of Opinion and the Cross-Section of Stock Returns. *Journal of Finance* 57(5): 2113–2141. (PDF 2건 비교 게이트 근거 — 단 2건은 약한 dispersion proxy)

**한국 법령·제도**:
23. 자본시장법 §173조의3 (내부자거래 사전공시제). 2024.1.23 공포, **2024.7.24 시행**. 1% 또는 50억원 이상 거래 30일 사전공시.
24. 한국거래소 유가증권시장 상장규정 §47(관리종목) / §48(상장폐지·실질심사). 코스닥시장 상장규정 §53/§56.
25. 가격제한폭 ±30% — 2015.6.15 시행 (이전 ±15%). 동시 정적 VI ±10% 도입.
26. KRX NSDS (공매도 중앙점검시스템) — **2025.3.31 가동, 전 종목 공매도 재개**. nsdst.krx.co.kr.
27. 증권거래세: 2025년 코스피 0.15% (농특세 0.15% + 거래세 0.00%) / 코스닥 0.15%. **2026년 0.20%로 상향 (기획재정부 2025-12-01 시행령)**.
28. 양도소득세: 일반투자자 비과세, 대주주 요건 종목당 50억원 이상 유지 (기획재정부 2025-09-15 확정). 코스피 1% / 코스닥 2% / 코넥스 4% 또는 50억원.

---

## 부록 — 매도 판단 XML 출력 템플릿

매수 KR_DEEPSEARCH 10-STEP과 일관된 형식. 모든 매도 판단은 다음 XML로 출력 후 `set_alert(log_type='decision')`에 박제.

```xml
<exit_decision date="YYYY-MM-DD" ticker="000000" name="종목명">
  <pre_commitment>STEP 0 박제 — 액션 X% / HOLD / 매도 사유 1줄</pre_commitment>
  <thesis_check>
    <invalidation>경로 1 점수 (-10~+10) + 근거 (DART 키워드/EPS 변화)</invalidation>
  </thesis_check>
  <technical_check>
    <ma_status>50MA·200MA·52주 고가 위치</ma_status>
    <oneill_8week>발동/미발동 — 3주 +20% 이상이면 발동, HOLD 강제</oneill_8week>
    <atr_trailing_stop>현재 ATR 기반 trailing stop 가격</atr_trailing_stop>
  </technical_check>
  <pdf_gate>
    <reports_read>리포트 N건 (증권사 명단)</reports_read>
    <tp_dispersion>StdDev/Mean %</tp_dispersion>
    <eps_30d_change>+/- %</eps_30d_change>
  </pdf_gate>
  <supply_check>
    <foreign_5d>외인 5일 누적</foreign_5d>
    <institution_20d>기관 20일 누적</institution_20d>
    <credit_balance>신용잔고 30일 변화</credit_balance>
    <short_balance>공매도/대차 잔고 변화</short_balance>
  </supply_check>
  <insider_check>30일 클러스터 N건 (사전공시 §173조의3)</insider_check>
  <three_paths_score>경로1 / 경로2 / 경로3 / 합계</three_paths_score>
  <devils_advocate>왜 매도하면 안 되는가 — 3문장 이상 강제 발화</devils_advocate>
  <final_action>HOLD / 부분매도 X% / 전량매도</final_action>
  <reasoning>최종 근거 (학술/실무 인용 포함)</reasoning>
  <records>
    set_alert(log_type='decision') ✓
    set_alert(log_type='trade') ✓ (실행 시)
    write_file('thesis/{ticker}.md') ✓
  </records>
</exit_decision>
```

**최종 원칙 — LS ELECTRIC 재발 방지**:
> 3경로 0/3 발동 + 8-week hold 룰 활성 + Devil's Advocate 통과 → **HOLD가 유일한 정답**.
> LLM이 "충분히 올랐으니 익절" 만으로 매도를 권유하면 규칙 1·5·6의 합성 편향 발현 — 즉시 차단.
