# KR_EXIT.md — 한국 집중투자자용 매도 판단 프레임

> **적용 범위**: 한국 종목(KOSPI/KOSDAQ) **이득 영역(+10% 이상)** 매도 판단 전용.
> 손절(-7~10%)은 별도 체계, 평단 ±5% 박스권은 본 프레임 제외.
> **최우선 목표**: LS ELECTRIC식 LLM 조기매도 편향 차단(5/5 HOLD 조건에서 매도 추천 → +47~84% 추가 상승 놓침 재발 방지).
> **매수 프레임 정합**: KR_DEEPSEARCH.md 10-STEP 구조와 일관 — Pre-commitment·Devil's Advocate·PDF 게이트·F/G-Score·3-Gate 구조 동일 용어 사용.

---

## ⚠️ 2026-06-01 대개정 요약 (3차 반증검증 반영)

본 프레임은 매도 규칙 전반에 대한 3차 외부 반증검증(트레일링 스톱·부분익절·이동평균·thesis매도·winner매도)을 거쳐 다음 3개 항목을 개정했다. 상세 근거는 Section 6.

1. **8-week hold 룰 폐기.** O'Neil "3주 +20% → 8주 강제보유"는 (a) 백테스트 1차 출처 부재(uncodified heuristic), (b) 가격 모멘텀 룰을 PEAD(어닝 표류)로 정당화한 범주 오류. → **삭제.** winner 조기매도 방지 목적은 "**3경로 0/3이면 무조건 HOLD**"가 그대로 수행한다(시간 게이트보다 thesis 게이트가 더 단단함).
2. **트레일링 스톱 강등.** 가격 트레일링은 평균수익을 깎고(Dai et al. 2021) right-tail 종목을 잘라낸다(Bessembinder 2018). 한국 ±30% 변동성에서는 whipsaw로 더 해롭다(Kaminski-Lo 2014: 평균회귀 레짐에서 stopping premium 음수). → **1차 매도 트리거에서 제거.** 단 **재앙적 갭다운(한 세션 -15%+) 발생 시 "재평가(매도 아님) 트리거"로만** 잔존(한국 air-pocket 한정).
3. **경로 1(Thesis Invalidation)에 "공통 동인 무효화" 추가.** 개별 종목 thesis가 산업 공통 동인(예: AI 데이터센터 capex)에 의존하는 경우, 그 동인의 구조적 둔화도 thesis 무효화 신호로 본다. 검증 steel-man("매도 트리거의 가치는 조기 감지가 아니라 판단의 질")에 따라 **경로 1(thesis)을 강화, 경로 2(기술)를 약화**하여 프레임 무게중심을 이동.

**불변 원칙(검증으로 재확인되어 유지)**: 목표가 도달 = 매도 아닌 재산정 / 3경로 0/3 = 무조건 HOLD / 단순 valuation 고평가는 매도 사유 아님(Fisher) / 본전 회귀(round-trip)는 가격 매도룰이 아니라 포지션 분산과 thesis로 관리.

---

## Section 1. LLM 편향 차단 10규칙 (매도 시 강제 적용)

매도 시점은 LLM의 가장 약한 영역이다. **사용자가 "팔까?"라고 물으면 LLM은 동조(sycophancy)하고, "확실해?"라고 압박하면 의견을 뒤집는다(FlipFlop)**. 다음 10규칙은 매도 직전 모든 단계에서 검증된다.

**규칙 1 — Sycophancy 금지.** Sharma et al. (2023, *Towards Understanding Sycophancy in Language Models*, arXiv:2310.13548, ICLR 2024)은 GPT-4·Claude 2·ChatGPT-3.5·LLaMA 2-70B 5개 모델 모두에서 RLHF가 "사용자 견해 동조 응답을 선호"하도록 학습됨을 입증했다. **사용자가 "팔까"라고 입력한 순간 LLM 출력의 50% 이상이 매도 동조로 편향**될 수 있으므로, 매도 질의는 항상 중립 프레이밍("매도/HOLD/추가매수 중 근거 기반 선택")으로 제출한다.

**규칙 2 — FlipFlop 차단.** Laban et al. (2023, arXiv:2311.08596) FlipFlop Experiment에서 10 LLM × 7 task × 67,640 시행 결과: "정말이야?"형 challenger 1회 후 평균 정확도 **-17%p, flip 비율 46%, Claude V2 -34%p**. **매도 판단 후 LLM에게 "확실해?"를 묻지 않는다**. 의심이 든다면 STEP 7 Devil's Advocate를 별도 사이클로 실행하되, 동일 세션에서 결정을 뒤집지 않는다.

**규칙 3 — Pre-commitment.** Ariely & Wertenbroch (2002, *Psychological Science* 13(3): 219–224)은 자기부과 deadline이 procrastination 통제에 유효함을 입증했다. 매도 의사결정은 **STEP 0에서 `set_alert(log_type='decision')`로 박제 후 진행** — 사후 합리화로 결심을 흐릴 수 없도록 한다.

**규칙 4 — Devil's Advocate 강제.** Kim, Kim & Yoon (2024, *Findings of ACL 2024*, pp. 1885–1897, DEBATE) 논문은 multi-agent NLG 평가에 반대측 critic을 강제 발화하면 SummEval/TopicalChat에서 G-Eval 대비 우수함을 보였다. 본 프레임에서는 **매도 판단 직전 "왜 지금 매도하면 안 되는가"를 별도 문단으로 강제 작성**한다.

**규칙 5 — Intrinsic reflection 금지.** Huang et al. (2023, *Large Language Models Cannot Self-Correct Reasoning Yet*, arXiv:2310.01798, ICLR 2024, Google DeepMind+UIUC)은 외부 피드백 없는 self-correction은 추론 정확도를 **저하**시킴을 입증했다. **LLM이 자기 결정을 자체 점검하는 것만으로 매도 편향을 교정할 수 없다** — 외부 게이트(룰·PDF·포트폴리오 데이터·봇 도구)가 필수다.

**규칙 6 — Bull-market 조기매도 편향 경고.** Li, Kim, Cucuringu & Ma (2025, arXiv:2505.07078, KDD 2026 accepted)은 20년 100+ 종목 백테스트에서 **LLM 전략이 상승장에서 과도하게 보수적이 되어 passive benchmark를 하회**함을 보였다. **3주 +30% 이상 급등 후 매도 권유는 이 편향의 직접 발현**일 가능성이 높다. (※ 2026-06-01 개정: 본 편향의 차단 장치였던 O'Neil 8-week hold 룰은 폐기되었다 — Section 6 참조. 대체 장치는 "3경로 0/3 = 무조건 HOLD"이며, 급등 자체는 매도 사유가 될 수 없다.)

**규칙 7 — 한국 매수 편향 93% 환경 보정.** 자본시장연구원 김준석 (2025, *자본시장포커스* 2025-15호, 2025.07.21)에 따르면 한국 애널리스트 매수의견 비중은 2000년대 67% → 2010년대 89% → **2020년대 93%**까지 상승했다. 김준석 (2026, *자본시장포커스* 2026-02호)에서는 "투자의견·TP 컨센서스 초과수익률은 **2013년 이후 통계적으로 관찰되지 않으며**, 중소형주에서만 잔존"임을 표본 2000–2024 약 70만 건으로 입증했다. 따라서 **"매수 의견 일색 = 매도 신호"의 약한 증거**로 사용하되, 단독 트리거 금지. (※ 매도의견 비중 0.1%, 의견변경률 2.5% — KCMI 2026. 한국에서는 애널 다운그레이드·TP하향이 거의 발생하지 않으므로 이를 조기경보로 쓸 수 없음. Section 6.)

**규칙 8 — Category 분기.** 매수 프레임의 카테고리(메인/가치/스윙)별 매도 룰이 다르다. **메인은 3경로 엄격 적용, 스윙은 모멘텀 종료 즉시 매도, 가치는 F-Score 급락 시만**. 같은 신호도 카테고리에 따라 해석이 다르다. (※ 2026-06-01 개정: 메인 카테고리의 "8-week hold" 조항은 폐기. 메인은 "3경로 0/3이면 HOLD"가 적용된다.)

**규칙 9 — 수치 임계 투명성.** 모든 매도 트리거의 수치는 **학술 근거 또는 "실무 관행" 라벨링**과 함께 명시. 학술 근거 없는 임계는 사용자 백테스트 영역으로 분리한다.

**규칙 10 — 3경로 룰: 매도 유일 정당화 경로.** Fisher (1958, *Common Stocks and Uncommon Profits*, Ch.6 "When to Sell — and When Not To")의 3가지 매도 사유 외에는 매도 정당화 금지. **단순 valuation 고평가는 Fisher가 명시적으로 부정**한 매도 사유다(원문: "trying to measure with greater preciseness than is possible"). Section 2 의사결정 트리에 구체 적용.

---

## Section 2. 3경로 의사결정 트리 (한국 적용)

매도는 **3경로 중 1개 이상이 명확히 발동**할 때만 정당화된다. 0/3이면 무조건 HOLD.

> **검증 무게중심 (2026-06-01)**: 3차 반증검증 steel-man — "가격 트레일링은 *왜* 파는지 모른 채 노이즈에 right-tail을 잘라내지만, 펀더멘털 트리거는 *진짜 fundamental break일 때만* 발화하므로 false positive가 적다. 트리거의 가치는 '조기 감지'가 아니라 '판단의 질'이다." → **경로 1(Thesis)을 최우선·강화, 경로 2(Technical)는 보조·약화.**

### 경로 1 — Thesis Invalidation (당초 매수 이유 소멸) ★최우선

**학술 정의**: Fisher (1958, Ch.6) "(1) a mistake has been made in the original purchase, (2) the company no longer meets the 15 standards" — 매수 시점 thesis가 깨졌거나, 기업이 변질된 경우. 추가로 Rappaport-Mauboussin (2021, *Expectations Investing* Ch.7) "Expectation Gap": **가격이 함의하는 기대(PIE)를 회사가 충족시킬 수 없다고 판단되면 매도** — 단편 신호가 아니라 thesis 자체의 붕괴를 높은 문턱(demanding hurdle)으로 본다.

**한국 즉시 청산 트리거** (DART 공시 기반, 한국거래소 상장규정 §47·§48):

| 카테고리 | DART 키워드 | 행동 |
|---|---|---|
| 감사의견 비적정 (거절·부적정·한정) | "감사보고서" + "거절/부적정/한정" | **당일 청산** (정리매매) |
| 자본전액잠식 / 50% 이상 잠식 | "자본잠식" 정정 | **즉시 매도** |
| 횡령·배임 (자기자본 5% 또는 대규모 2.5% 이상) | "횡령" / "배임" | **즉시 매매정지 → 청산** |
| 분식회계 (감리지적 + 과징금) | "감리결과지적사항" | 즉시 매도 |
| 상장폐지·관리종목지정 | "상장폐지" / "관리종목" | 정리매매 7일 |

**봇 매핑**: `get_dart(mode='disclosure_list', ticker, days=30)`로 매도 판단 시점 직전 30일 공시를 자동 스캔. 위 키워드 1개라도 히트 시 STEP 1 즉시 EXIT.

**⭐ 공통 동인 무효화 (2026-06-01 신설)**: 개별 종목 thesis가 **산업 공통 동인**에 구조적으로 의존하는 경우, 그 동인의 구조적 둔화도 thesis 무효화 신호로 본다. 종목별 지표(점유율·ASP)보다 **선행**할 수 있는 뿌리 신호다.

| 공통 동인 | 해당 종목군(예시) | 무효화 신호(높은 문턱) |
|---|---|---|
| AI 데이터센터 capex | 반도체(메모리/HBM), 전력기기(변압기), 클라우드 | 하이퍼스케일러(Amazon/Google/Meta/MS) 분기 capex 가이던스 **2분기 연속 하향** 또는 AI 투자 회수기 진입 공식화 |
| (기타 동인은 매수 thesis 작성 시 종목별 명시) | — | — |

  - 발동 시 **자동 매도 아님 → 해당 동인에 묶인 보유 종목 전체 재평가**(개별 thesis 재점검). 단일 분기 잡음 배제 위해 "2분기 연속" 또는 "공식 회수기 선언" 수준의 높은 문턱 유지.
  - ⚠️ 주의: 공통 동인 신호는 가격에 선반영될 수 있음(효율적 시장). "조기경보"로 과신 금지 — Section 6. 어디까지나 thesis 재점검 트리거이지 시점 예측 도구가 아니다.

**완화된 thesis 손상**: EPS 컨센서스 30일 -10% 이상 하향 + TP 하향 리포트 비중 >50% → 부분 비중 축소(50%) 검토. 단독으로 전량 청산 불가(Devil's Advocate 통과 필수). (※ 한국은 애널 다운그레이드 자체가 드물어(변경률 2.5%) 이 신호의 발생 빈도가 낮음 — 보조로만.) (※ 정합 명확화: thesis **완전 무효(Hard Kill)** = 전량 청산 / **부분 손상(스코어 기반)** = 축소 검토 — 단 이 축소는 가격 상승 기반 익절이 아니므로 절대원칙 0과 무관하며, INVESTMENT_RULES §3 전량 이진과 정합(손상-스코어 기반 축소는 가격 익절이 아님).)

### 경로 2 — Technical Exit (기술적 매도 룰) ※2026-06-01 약화

**개정 사유**: 가격 기반 매도룰(트레일링·이동평균 이탈)은 3차 검증에서 평균수익 감소(Dai et al. 2021 "inferior mean returns")·right-tail 절단(Bessembinder 2018)·한국 변동성 whipsaw(Kaminski-Lo 2014)로 약화 판정. **경로 2는 단독 매도 트리거가 아니라, 경로 1 점검을 촉발하는 "알람" 역할로 강등한다.**

- **50일 이평선 대량거래 동반 결정적 이탈 + 3거래일 내 반등 실패** → **매도 아님. 경로 1(thesis) 재점검 트리거.** thesis 무결 시 HOLD 유지.
- **재앙적 갭다운 (한 세션 -15% 이상, 한국 ±30% air-pocket 한정)** → **매도 아님. 즉시 thesis 재평가 트리거.** 단일 갭다운이 thesis 붕괴(경로 1)를 동반하는지 확인. 동반하지 않으면 HOLD.
- **트레일링 스톱 / 부분 익절 / "3주 +20% → 8주 보유" → 모두 폐기.** 가격 상승폭·하락폭 자체는 매도 사유가 아니다(목표가 도달 = 재산정). 본전 회귀(round-trip) 위험은 경로 1(thesis 붕괴 시 매도) + 포지션 분산으로 관리하며, 가격 트레일링으로 방어하지 않는다(검증: 가격 매도룰은 right-tail truncation 비용을 매번 지불).

**한국 가격제한폭 ±30% (2015.6.15 시행) 참고**: 종목별 일일 ATR이 미국 대비 1.5~2배여서, 미국식 고정-% 또는 ATR 트레일링을 그대로 이식하면 whipsaw가 미국보다 잦다(Kaminski-Lo: 한국 단기 reversal 우세 환경에서 stopping premium 음수 가능). 이것이 트레일링을 1차 트리거에서 제거한 시장 구조적 근거다.

### 경로 3 — 기회비용 (한국 세제 반영)

> **INVESTMENT_RULES §3 경로3 능동 발동 기준이 우선** (2026-06-04): 검증 신규후보(3-Gate 3/3) 보유 + 풀투자 시 능동 재배치 스캔 발동, 양쪽 PDF 분해 forward-RR 비교, 재원=최저RR/최약thesis, 20거래일 빈도제한, 세제게이트.

**학술 정의**: Munger (1994, *A Lesson on Elementary, Worldly Wisdom*, USC Marshall Guest Lecture)는 "30년 15% 복리, 매년 35% 자본이득세 vs 마지막 1회 35% → 약 3.5%p/yr 손실"을 제시했다. **이 모델은 미국 35% 자본이득세 가정**이다.

**한국 일반투자자(비대주주) 적용 — 정정**: 한국 상장주식 양도세는 **일반투자자 비과세** (대주주 50억 이상 별도). 거래세는 **2025년 0.15% (코스피 농특세 0.15% + 거래세 0.00%, 코스닥 거래세 0.15%), 2026년 0.20% (코스피 0.05%+0.15%, 코스닥 0.20%)** — 기획재정부 2025-12-01 시행령 개정안 기준.

**결론**: Munger의 "compounding의 적 = 자본이득세" 직접 인용은 한국 일반투자자에 부적합. **한국 기회비용 경로는 거래세(0.15~0.20%) + 슬리피지 + 재진입 타이밍 리스크 + 심리적 회전율 비용**으로 재구성한다.

**기회비용 매도 합리적 임계** (실무 기준):
- 보유 종목 **연 기대수익률** vs **명백히 우월한 대체 종목 기대수익률** 격차 ≥ **5%p/yr 이상 + 12개월 이상 지속 예상** 시에만 발동.
- 단순 "다른 게 더 올랐다"는 sycophancy → 매도 금지(규칙 1).

---

## Section 3. 한국 시장 특화 매도 시그널 표

> ※ 2026-06-01: 시그널 #11·#12(트레일링·8주 hold)는 폐기. 가격 기반 시그널은 매도 트리거가 아니라 경로 1 재점검 알람으로만 해석.

| # | 시그널 | 임계 | 학술 근거 | 가중 |
|---|---|---|---|---|
| 1 | DART 감사의견 비적정/횡령/자본잠식 | 발생 즉시 | 상장규정 §47·§48 | **즉시 청산** |
| 2 | 컨센서스 EPS 30일 하향 | -5% 이상 | 김준석 (2026) KCMI 2026-02호 | 강 |
| 3 | TP 도달율 (대형주) | 90% 도달 | 김준석 (2026) — 2013 이후 정보가치 약화 / **매도 아님, 재산정 트리거** | 약 |
| 4 | TP 도달율 (중소형주) | 90% 도달 | 김준석 (2026) — 중소형주 정보가치 잔존 / **매도 아님, 재산정 트리거** | 약~중 |
| 5 | TP 컨센서스 분산도(StdDev/Mean) | >25% | Diether, Malloy, Scherbina (2002) JF 57(5): 2113–2141 | 중 |
| 6 | 외국인 5일 연속 순매도 + 52주 고가 ±5% | 동시 발생 | 52주 고가 anchoring: Goh & Jeon (2017) PBFJ 44: 150–159; 5일 룰은 **실무 관행** | 중 (조합 시) |
| 7 | 신용잔고율 급증 | 30일 +50% | **실무 관행** (학술 임계 미확인) | 중 |
| 8 | 대차잔고 비율 | 시총 대비 >10% & 30일 +50% | 공매도 빌드업 패턴 — **실무 관행** | 중 |
| 9 | 임원·최대주주 매도 클러스터 | 30일 내 2건 이상 (사전공시제 1%/50억 이상) | 자본시장법 §173조의3 (2024.7.24 시행); 미국 Cohen, Malloy & Pomorski (2012) JF 67(3): 1009–1043 외삽 | 강 |
| 10 | PEAD 강도 (한국 대형주) | 호재 후 60일 내 약화 신호 | 강형구 외 (2022) 한국증권학회지 51(3): 309–334 — 대형주 신속 반응, 소형주만 잔존 | 약 (대형주) |
| 11 | ~~트레일링/이평 이탈~~ → **재앙 갭다운(-15%+/세션)** | 한 세션 -15% 이상 | **재평가 트리거(매도 아님)**, 한국 air-pocket 한정 | 알람 only |
| 12 | ~~200MA +50% + 3주 +30% (8주 hold)~~ | **폐기** | uncodified + 범주오류 (Section 6) | — |
| ⭐13 | 공통 동인 둔화 (capex 등) | 하이퍼스케일러 capex 2분기 연속 하향 | 신설 — 동인 묶인 종목군 전체 재평가 트리거 | 강 (조합 시) |

**중요 주의**: ⚠️ **시그널 6의 "외국인 5일 연속 순매도"는 학술 직접 근거 없음**. Choe, Kho & Stulz (1999, *J. Financial Economics* 54(2): 227–264)는 한국 외국인의 **순매수 시점 positive feedback trading** 결론이며, 매도 시점이 시장 destabilize 한다는 결론은 명시적으로 **부정**된다(원문: "no evidence that trades by foreign investors had a destabilizing effect"). 따라서 "외국인 매도 = 추가 하락"은 약한 가설로만 사용.

---

## Section 4. 매도 판단 STEP 0~8 (한국 봇 도구 매핑)

라이트 체크 5분 / 빠른 판정 10분 / 풀 매도 판정 30분 — 시간 배분은 **한국 거래시간(09:00-15:30) 내 단계적 실행** 가정.

### STEP 0 — Pre-commitment 박제 (1분)
```
set_alert(log_type='decision', date=YYYY-MM-DD, regime='경계/공격/방어',
  grades={ticker: {grade:'B', change:'A→B', reason:'thesis손상/공통동인둔화/...'}},
  actions=['종목 X% 비중 축소' or 'HOLD 유지'], notes='시장 국면+이유')
```
규칙 3 적용. 매도 결심은 **봇 데이터 조회 전**에 박제 → 데이터 사후 합리화 차단.

### STEP 1 — Thesis 재검증 (5분) ★최우선
- `get_dart(mode='disclosure_list', ticker, days=30)` — Section 2 경로 1 키워드 자동 스캔. 1건이라도 히트 시 즉시 EXIT.
- `get_news(ticker, n=20, sentiment=true)` — **부정 비율 >30% 시 매도 가중**.
- **공통 동인 점검(신설)**: 해당 종목이 AI capex 등 공통 동인에 의존하면, 동인 둔화 신호(하이퍼스케일러 capex 가이던스 추세) 확인. 둔화 시 동일 동인 묶인 보유 종목 전체 재평가.
- `get_dart(mode='read', ticker)` — 사업보고서 본문 (필요 시).

### STEP 2 — 기술 (3분) ※약화: 매도 아닌 재점검 알람
- `get_stock_detail(ticker, period='W52')` — 주봉 52주: 52주 고저·추세 위치. (KR 일봉은 KIS API 최대 ~100건이라 `D250` 불가 — 200MA는 주봉(≈40주선)·봇 산출값(daily_snapshot `ma200`)으로 대체.)
- `get_stock_detail(ticker, mode='volume_profile', period='Y1')` — 매물대 확인.
- `get_market_signal(mode='vi')` — VI 발동 빈도 (변동성 급증 신호).

판정: **50MA 결정적 이탈 또는 한 세션 -15%+ 갭다운** 시 → **매도 아님. 경로 1(thesis) 재점검 트리거.** thesis 무결 시 HOLD. (트레일링·8주 hold 판정 없음 — 폐기.)

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
- 경로 1 (Thesis): STEP 1+3 합산 — **공통 동인 둔화 포함. 최우선 가중.**
- 경로 2 (Technical): STEP 2 — **단독 매도 불가. 경로 1 재점검 알람으로만.**
- 경로 3 (기회비용): 별도 평가 (보통 +0)

| 합계 | 액션 |
|---|---|
| ≤ -10 | 즉시 매도 (경로 1 thesis 붕괴 확인 시) |
| -5 ~ -9 | 50% 비중 축소 (경로 1 손상 시) |
| -4 ~ +4 | **HOLD** |
| ≥ +5 | 추가매수 검토 (별도 매수 프레임) |

> ※ "50% 비중 축소" 한정: thesis **완전 무효(Hard Kill)** = 전량 청산(≤ -10 행) / **부분 손상(스코어 기반)** = 축소 검토(본 행). 이 축소는 가격 상승 기반 익절이 아님 — 절대원칙 0과 무관하며, INVESTMENT_RULES §3 전량 이진과 정합(손상-스코어 기반 축소는 가격 익절이 아님).

**0/3 발동 시 무조건 HOLD** (LS ELECTRIC 케이스 핵심 + winner 조기매도 방지 — 폐기된 8주 hold의 목적을 이 규칙이 대체).

### STEP 7 — Devil's Advocate (5분)
규칙 4 적용. 별도 문단으로 다음 질문에 답한다:
- "왜 지금 매도하면 안 되는가?"
- "재진입 비용(슬리피지+세금+심리)은?"
- "슈퍼사이클 한가운데 sell sentiment인가?" (FINSABER bull-market bias 자가 점검)
- "이 종목이 right-tail 대박일 가능성을 가격 하락 때문에 잘라내는 것은 아닌가?" (Bessembinder)
- `simulate_trade(sells=[{ticker, qty, price}])`로 비중 변화·RR 사전 확인.

### STEP 8 — 기록 (2분)
```
set_alert(log_type='trade', side='sell', ticker, qty, price, grade='B',
  reason='thesis손상/공통동인둔화', date=YYYY-MM-DD)
write_file('thesis/{ticker}.md', updated_content)  # thesis 갱신
set_alert(log_type='delete', ticker, market='KR')   # 매도 시 알림 정리
```

---

## Section 5. 한국 케이스 (LS ELECTRIC + SK하이닉스 재검증)

### Case A — LS ELECTRIC (010120): LLM 조기매도 추천 실패

**조건** (사용자 보고 시점 기준): 5/5 HOLD 충족 — Thesis 유효(HVDC·데이터센터 수주), 목표가 미도달, 과열 없음, 손절 미터치, 200MA 위.

**3경로 판정**: 0/3 발동.
- 경로 1: thesis 유효 — HVDC 수주 모멘텀 지속, EPS 추정 상향 (유안타 2026.4.14 TP 260,000원 상향, KB 2026.4.14 TP 240,000원 상향, +79.1%).
- 경로 2: 50일선 위, 200MA 위 — 기술적 매도 알람 없음.
- 경로 3: 명백히 우월한 대체 종목 미확인.

**LLM 진단**: 조기매도 추천은 다음 편향의 **합성 발현**:
1. **Sycophancy** (규칙 1): 사용자 "팔까?" 입력에 동조.
2. **Intrinsic reflection 오류** (규칙 5): "충분히 올랐으니 팔아야"라는 자기 추론으로 외부 데이터 무시.
3. **Bull-market 조기매도 편향** (규칙 6, FINSABER Li et al. 2025): 상승장에서 보수 편향.

**차단 룰**: **3경로 0/3 = 무조건 HOLD**. 이후 +47~84% 상승 미스 사례를 본 프레임의 핵심 교정 데이터로 등록. (※ 2026-06-01: 과거 본 케이스에서 "200MA +60% + 3주 +26%로 과열" 운운하며 부분익절을 검토했던 항목은 폐기. 가격 상승폭은 매도 사유가 아님 — Bessembinder right-tail 절단 비용. thesis 무결 시 HOLD.)

### Case B — SK하이닉스 (000660): 2026-04-24 HOLD 판정 (재검증)

**검증된 데이터** (KIS API 일봉):
- 2026-03-31 종가 807,000원 → 2026-04-24 종가 **1,222,000원**, **3주 +51.4%**
- 1Q26 매출 52.6조원 (+198% YoY), 영업이익 37.6조원 (+405% YoY) — 어닝 비트.
- HBM3E 점유율 ~62%, HBM4 70% 예상 (UBS).

**3경로 판정**: 0/3.
- 경로 1: thesis 3/3 유효 — HBM 슈퍼사이클(공통 동인: AI 데이터센터 capex 확대), 1Q 어닝 비트. **공통 동인 점검: 하이퍼스케일러 capex 둔화 신호 없음 → thesis 뿌리 무결.**
- 경로 2: 50일선 위, 200MA 위 — 기술적 알람 없음. (※ 과거 판정의 "8-week hold 강제 발동"·"trailing stop 1,100K 상향"은 2026-06-01 폐기.)
- 경로 3: 슈퍼사이클 한가운데 — 기회비용 경로 비활성.

**SK하이닉스 thesis 무효화 조건 (Kill Switch, 공통 동인 반영)**:
1. **③ 1Q26 영업이익 컨센 34조 미만** (개별 실적)
2. **② 삼성 HBM3e 12H NVIDIA Blackwell Ultra 공식 퀄 통과 + SK HBM 점유율 50% 이하** (개별 경쟁 — SK "1등 수혜주" thesis 직접 붕괴, 핵심 트리거)
3. **① 3Q26 HBM ASP 2분기 연속 QoQ 마이너스** (개별 사이클 — 단일 분기 잡음 배제 위해 2분기로 문턱 상향)
4. **⭐ 하이퍼스케일러(Amazon/Google/Meta/MS) capex 가이던스 2분기 연속 하향** (공통 동인 — thesis 뿌리. ①③보다 선행 가능)
- **발동 기준**: ②(점유율) 또는 ⭐(capex)는 thesis 뿌리/핵심이라 단독 발동 시 전체 재평가. ①③은 2개 동시 또는 ②⭐와 조합 시 재평가. 모두 **자동 매도 아니라 재평가**.

**판정**: **HOLD.** thesis 3/3 + 공통 동인 무결. 목표가 도달은 재산정(매도 아님). 본전 회귀 위험은 트레일링이 아니라 thesis(Kill Switch 4종) + 포지션 비중으로 관리.

---

## Section 6. 확신 없는 항목 + 3차 검증 결과 (정직 명시)

본 프레임은 학술 근거 강도를 라벨링한다. 다음은 사용자 직접 백테스트 또는 추가 검증이 필요하거나, 3차 외부검증으로 확정/기각된 항목이다.

### 3차 반증검증 확정 결과 (2026-06-01)

**[기각] 8-week hold 룰** — O'Neil "3주 +20% → 8주 강제보유". (a) 백테스트 1차 출처 부재(uncodified heuristic), (b) **범주 오류**: 8주 룰은 가격 breakout 후 momentum continuation 룰인데 이를 PEAD(SUE/CAR로 정의되는 어닝 정보 표류)로 정당화했음 — 두 현상은 별개. (c) JT(1993) 모멘텀 형성기간 3~12개월 하단보다 짧음. FFTY ETF(CAN SLIM 전체) underperform은 8주 룰 단독 검증이 아님. → **폐기. winner 조기매도 방지는 "3경로 0/3 = HOLD"로 대체.**

**[기각/강등] 트레일링 스톱 (가격 기반)** — Dai et al.(2021, *IRF* 21(4)): 개별주에서 "inferior mean returns to a mean–variance optimal benchmark." Clare et al.(2013): "there is no value in stop loss rules; a change of trend is simply the best stop-loss rule." Kaminski-Lo(2014): i.i.d./평균회귀 레짐에서 stopping premium 음수 — 한국 단기 reversal 우세 환경에 해당. Bessembinder(2018): 상위 4%가 시장 net wealth 전부 → 가격 룰은 right-tail compounder를 거의 확실히 절단. → **1차 매도 트리거에서 제거. 재앙 갭다운(-15%+/세션) 재평가 트리거로만 잔존.**

**[기각] 부분 익절 (+N% 트리거, 1/3·1/2 비율)** — Shiryaev-Xu-Zhou(2008, *Quant Finance* 8(8)): goodness index 기준 bang-bang(전량 보유 또는 전량 매도) 최적해, 내부 해(부분 매도) 없음. Barberis-Xiong(2009, *JF* 64(2)): 부분 익절은 disposition effect의 약화된 변형(realization utility), wealth는 감소. → **폐기.**

**[기각] 펀더멘털 "조기경보" 가설** — 사용자 가설("가이던스 컷·제품가격·점유율이 가격보다 먼저 경고")은 conditional reject. 5개 신호 중 4개가 가격에 동시·후행(Bulkley-Herrerias 2005 profit warning D-day -13~22%; Martineau 2022 대형주 PEAD 사망; 반도체는 주가가 DRAM가격 1~6개월 선행). 애널 컨센서스 하향(Womack 1996)만 1~6개월 lead 있으나 **한국은 매도의견 0.1%·변경률 2.5%(KCMI 2026)로 사실상 미발생**. 또한 Elliott et al.(2024, *RAS* 29): **롱 보유자는 펀더멘털 정보를 체계적으로 상향 편향 해석** → "이 가이던스 컷은 일시적"이라는 자기기만이 학술적으로 예측됨. → **공통 동인(capex) 신호는 "조기경보"가 아니라 "thesis 재점검 트리거"로만 사용. 시점 예측 도구로 과신 금지.**

**[유지·강화] 경로 1 thesis 기반 매도** — 3차 검증 steel-man: 트리거의 가치는 '조기 감지'가 아니라 '판단의 질'. Faugère et al.(2004, *JPM* 30): 하락장에서 "Valuation Level"·"Target Price" 매도룰 상위, fundamental break 기반 매도가 가격 룰보다 정당. Rappaport-Mauboussin Expectation Gap이 학술 근거 있는 유일한 펀더 매도룰. → **경로 1 최우선·demanding hurdle 유지.**

**[유지] round-trip(본전 회귀) 방어 = 포지션 + thesis** — 라운드트립 드로다운 = 0.9·w₀/(1+0.9·w₀). 비중 w₀가 작으면 무시 가능(5%→-4.3%), 클수록 실손(25%→-18.4%). **사용자 포트는 다종목 분산으로 단일 종목 비중이 낮아(SK하이닉스 전체 ~12%) 한 종목 round-trip이 포트에 치명적이지 않음.** 따라서 가격 트레일링 없이 thesis(경로1) + 분산으로 관리. (단 향후 단일 종목이 포트 20% 초과로 커지면 비중 관리 별도 검토 — 현재는 미해당.)

### 기존 미확인 항목 (유지)
- Eom, Hahn & Sohn (2019, *PBFJ* 53: 379–398) "한국 호재 60일 / 악재 20일 비대칭" 정확 수치 본문 미확인. 일반 한국 PEAD는 60-day CAR 표준.
- 강형구·전진규 (2022, 한국증권학회지 51(3)) 공저자 매칭 불확실하나 결과(대형주 PEAD 약화/소형주 잔존) 신뢰.
- 외국인 5일 순매도, 신용잔고 10%+, 대차잔고 30일 +50% — 모두 **실무 관행**, 학술 임계 미확인.
- 한국어/한국 종목 LLM Sycophancy·FlipFlop 직접 측정 — 미발견. 영어 SOTA 측정치 외삽.

### 세제 정정 (유지)
- Munger (1994) "compounding의 적 = 양도세"는 미국 35% 가정. 한국 일반투자자 양도세 비과세 + 거래세 0.15%(2025)/0.20%(2026, 기획재정부 2025-12-01). 미국 주식 보유 한국 거주자(해외주식 22% 분리과세)에는 적용 가능.

---

## Section 7. 학술·실무 근거 인용

**LLM 편향 (10규칙)**:
1. Sharma, M., et al. (2023). *Towards Understanding Sycophancy in Language Models*. arXiv:2310.13548. ICLR 2024.
2. Laban, P., et al. (2023). *Are You Sure? Challenging LLMs Leads to Performance Drops in The FlipFlop Experiment*. arXiv:2311.08596. (Claude V2 -34%p)
3. Ariely, D., & Wertenbroch, K. (2002). *Psychological Science* 13(3): 219–224.
4. Kim, A., Kim, K., & Yoon, S. (2024). DEBATE. *Findings of ACL 2024*: 1885–1897.
5. Huang, J., et al. (2023). *LLMs Cannot Self-Correct Reasoning Yet*. arXiv:2310.01798. ICLR 2024.
6. Li, W. W., Kim, H., Cucuringu, M., & Ma, T. (2025). *Can LLM-based Financial Investing Strategies Outperform the Market?* arXiv:2505.07078. KDD 2026. (FINSABER)
7. Womack, K. L. (1996). *Journal of Finance* 51(1): 137–167. (Sell rec -9.1%/6M drift)

**3경로 의사결정 트리**:
8. Fisher, P. A. (1958). *Common Stocks and Uncommon Profits*. Ch.6.
9. Rappaport, A., & Mauboussin, M. J. (2021). *Expectations Investing* (rev. ed.). Ch.7 "Buy, Sell, or Hold" — Expectation Gap 매도룰.
10. Munger, C. T. (1994). USC Marshall Guest Lecture.
11. Piotroski, J. D. (2000). *Journal of Accounting Research* 38(Suppl.): 1–41. (F-Score)
12. Mohanram, P. S. (2005). *Review of Accounting Studies* 10(2-3): 133–170. (G-Score)

**3차 매도룰 검증 (2026-06-01 신규)**:
13. Dai, M., Marshall, B. R., Nguyen, N. H., & Visaltanachoti, N. (2021). Stop-loss rules. *International Review of Finance* 21(4): 1334–1352. ("inferior mean returns")
14. Clare, A., Seaton, J., Smith, P. N., & Thomas, S. (2013). *Journal of Asset Management* 14: 182–194. ("no value in stop loss rules")
15. Kaminski, K. M., & Lo, A. W. (2014). *Journal of Financial Markets* 18: 234–254. (stopping premium)
16. Bessembinder, H. (2018). Do stocks outperform Treasury bills? *Journal of Financial Economics* 129(3): 440–457. (상위 4% = net wealth 전부)
17. Shiryaev, A., Xu, Z., & Zhou, X. Y. (2008). Thou shalt buy and hold. *Quantitative Finance* 8(8): 765–776. (bang-bang 최적)
18. Barberis, N., & Xiong, W. (2009). What drives the disposition effect? *Journal of Finance* 64(2): 751–784. (realization utility)
19. Bulkley, G., & Herrerias, R. (2005). Stock returns following profit warnings. (D-day -13~22%)
20. Martineau, C. (2022). Rest in Peace Post-Earnings Announcement Drift. *Critical Finance Review* 11(3-4): 613–646.
21. Elliott, W. B., Hobson, J. L., Van Landuyt, B. W., & White, B. J. (2024). Asymmetric motivated reasoning in investor judgment. *Review of Accounting Studies* 29: 3534–3563. (롱 보유자 상향 편향)
22. Faugère, C., Shawky, H. A., & Smith, D. M. (2004). Sell discipline and institutional money management. *Journal of Portfolio Management* 30: 95–105.
23. Akepanidtaworn, K., Di Mascio, R., Imas, A., & Schmidt, L. (2023). Selling fast and buying slow. *Journal of Finance* 78(6): 3055–3098. (매도 -100bp/yr, "sell more thoughtfully")

**한국 시장 특화**:
24. 김준석 (2026). *자본시장포커스* 2026-02호. 표본 2000–2024 약 70만 건. 매도의견 0.1%, 변경률 2.5%, 2013 이후 컨센 초과수익 소멸.
25. 김준석 (2025). *자본시장포커스* 2025-15호. 매수의견 93%.
26. 이승희·주소현·박광수 (2013). *Journal of The Korean Data Analysis Society*. 목표가 80.8% 단일방향 후행.
27. 강형구·전진규 (2022). *한국증권학회지* 51(3): 309–334. 대형주 PEAD 약화.
28. Eom, Y., Hahn, J., & Sohn, W. (2019). *Pacific-Basin Finance Journal* 53: 379–398.
29. Goh, J., & Jeon, B. (2017). *Pacific-Basin Finance Journal* 44: 150–159.
30. Choe, H., Kho, B.-C., & Stulz, R. M. (1999). *JFE* 54(2): 227–264. 외국인 destabilize 가설 기각.
31. Diether, K. B., Malloy, C. J., & Scherbina, A. (2002). *Journal of Finance* 57(5): 2113–2141.
32. Cohen, L., Malloy, C., & Pomorski, L. (2012). Decoding Inside Information. *JF* 67(3): 1009–1043.

**한국 법령·제도**:
33. 자본시장법 §173조의3 (사전공시제). 2024.7.24 시행. 1%/50억 30일 사전공시.
34. 한국거래소 상장규정 §47/§48, 코스닥 §53/§56.
35. 가격제한폭 ±30% (2015.6.15), 정적 VI ±10%.
36. KRX NSDS 공매도 — 2025.3.31 전 종목 재개.
37. 증권거래세 2025년 0.15% / 2026년 0.20% (기획재정부 2025-12-01).
38. 양도세 일반투자자 비과세, 대주주 50억 (기획재정부 2025-09-15).

---

## 부록 — 매도 판단 XML 출력 템플릿

매수 KR_DEEPSEARCH 10-STEP과 일관된 형식. 모든 매도 판단은 다음 XML로 출력 후 `set_alert(log_type='decision')`에 박제.

```xml
<exit_decision date="YYYY-MM-DD" ticker="000000" name="종목명">
  <pre_commitment>STEP 0 박제 — 액션 X% / HOLD / 매도 사유 1줄</pre_commitment>
  <thesis_check>
    <invalidation>경로 1 점수 (-10~+10) + 근거 (DART 키워드/EPS 변화)</invalidation>
    <common_driver>공통 동인(AI capex 등) 둔화 여부 — 2분기 연속 하향 시 동인 묶인 종목 전체 재평가</common_driver>
  </thesis_check>
  <technical_check>
    <ma_status>50MA·200MA·52주 고가 위치 (매도 아님, 경로1 재점검 알람)</ma_status>
    <crash_gap>한 세션 -15%+ 갭다운 여부 (재평가 트리거, 매도 아님)</crash_gap>
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
  <three_paths_score>경로1(최우선) / 경로2(알람) / 경로3 / 합계</three_paths_score>
  <devils_advocate>왜 매도하면 안 되는가 + right-tail 절단 위험 점검 — 3문장 이상 강제</devils_advocate>
  <final_action>HOLD / 부분매도 X% / 전량매도</final_action>
  <reasoning>최종 근거 (학술/실무 인용 포함)</reasoning>
  <records>
    set_alert(log_type='decision') ✓
    set_alert(log_type='trade') ✓ (실행 시)
    write_file('thesis/{ticker}.md') ✓
  </records>
</exit_decision>
```

**최종 원칙 — LS ELECTRIC 재발 방지 (2026-06-01 개정)**:
> 3경로 0/3 발동 + Devil's Advocate 통과 → **HOLD가 유일한 정답**.
> 가격 상승폭(목표가 도달 포함)은 매도 사유가 아니다 — 재산정 트리거일 뿐. 가격 하락폭도 단독 매도 사유가 아니다(경로 1 thesis 재점검 알람). 매도는 오직 thesis 붕괴(개별 + 공통 동인) 또는 명백한 기회비용으로만.
> LLM이 "충분히 올랐으니 익절" 또는 "많이 빠졌으니 손절"만으로 매도를 권유하면 규칙 1·5·6의 합성 편향 발현 — 즉시 차단.
