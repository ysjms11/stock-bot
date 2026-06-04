# INVESTMENT_RULES.md — 상세 매매 플레이북

> 프로젝트 지침(매 세션 로드)과 함께 쓰는 참조용 상세 규칙. `read_file("data/INVESTMENT_RULES.md")`로 필요 시 호출.
>
> 수치 범례: ✅ 원문 검증 / ⚠️ 부분확인·사용자결정 / ❌ 원문 없음·정성화
>
> **2026-06-04 세대 정렬**: 매도(§3)·현금(§5)을 KR_EXIT(2026-06-01)·US_EXIT(v2 2026-06-01)의 3차 반증검증 세대로 동기화. 기존 "매도 4 트리거(유포리아 부분정리)"·"VIX 분할매수"를 폐기하고 **3경로 + 전량 이진 + 능동 경로3 재배치 + 현금 dry-powder**로 통합. §11 변경 이력 참조.

---

## 0. 연구·인용 규율

- **데이터 없으면 데이터 불충분**. 수치 제시 시 반드시 출처(저자/연도/저널/URL).
- 2차 인용(블로그, 요약)은 신뢰도 낮음으로 표시.
- 확인 불가한 수치는 삭제 또는 정성화.
- LLM 자체 앵커링 편향 주의: Lou & Sun (2024) arXiv:2412.06593, CBEval (2024) arXiv:2412.03605 — round number, expert anchor에 취약.

---

## 1. 매수 10 Step 검증 (한국·미국 공통 코어)

### Step 1. 산업 흐름 확인 ✅
- 해당 산업 ETF 20/60일 이평 정배열 여부
- 외인·기관 순매수 최근 5/20일 누적 (KIS `get_sector flow` / `get_supply foreign_rank`)
- **기준**: 두 개 모두 양이어야 통과. 하나만 양이면 Starter 한정.

### Step 2. 리더십 포지션 ✅
- 시총 기준 업종 Top 3 이내, 또는 기술/점유율 우위 명확
- 분기 매출·영업이익 YoY 증가 (KIS `get_finance_rank sort=15`)
- **Piotroski F-Score ≥8** ✅ (원 논문: Piotroski 2000 JAR 38 Supp, **≥7이 아니라 ≥8이 공식**) — KIS `get_alpha_metrics`

### Step 3. 실적 모멘텀 ✅
- 연속 2분기 어닝 서프라이즈 양수 (원조 PEAD: **Bernard & Thomas 1989 JAR, 1990 JAE**) ✅ — Stickel 아님
- 가이던스 상향 또는 유지
- 컨센서스 예측 상향 트렌드 (단 Campbell-Sharpe 2007/2009 JFQA: 컨센은 전월 실측치 방향 **~30% 앵커링** 가정 ✅)

### Step 4. 밸류에이션 정합성 ⚠️
- 업종 peer median 대비 상대 PER/PBR
- **PEG**: <1 저평가 (Lynch 1989 *One Up on Wall Street* 원문) ✅ / **PEG > 1.5~2 경계는 실무 관행** (Lynch 직접 명시 아님) ⚠️
- 배당주: PEGY = P/E ÷ (성장률+배당률), <1 저평가

### Step 5. 수급 ✅
- 외인+기관 5일 연속 순매수 (KIS `get_scan preset=foreign_streak`)
- 거래량 20일 평균 대비 1.5x 이상
- 프로그램매매 동향 확인 (`get_market_signal program_trade`)

### Step 6. 기술적 ⚠️
- 20/60일 이평 정배열
- 52주 신고가 기준 괴리 10% 이내 (KIS `get_highlow`)
- 볼륨 프로파일 지지 구간 확인 (`get_stock_detail volume_profile`)

### Step 7. 리스크 지표 — **미국 딥서치용** ⚠️
- **Altman Z''-Score > 2.6 Safe / 1.1-2.6 Gray / <1.1 Distress** ✅ (Altman 2000/2013 NYU Stern, EMS 모델)
- **Beneish M > -1.78 분식 의심** ✅ (Beneish 1999 FAJ 55(5); 오류율: FN 26%, FP 13.8%. **확률적 신호**로 해석)
- **Short Float 10%+ 경계, 20%+ 고위험, 30%+ 극단** ✅ (업계 표준; **기존 5% 기준은 오류** ❌ → 수정됨)
- **SBC: Net Dilution 3%+ 경고** ✅ (TDM Growth Partners 2024 벤치마크: 희석률 3%+ 기업 Nasdaq 아웃퍼폼 실패). SBC/매출 비율은 업종 peer 상대 비교 (고정 15% 기준 ❌)
- **Rule of 40 (SaaS: YoY 성장률 + EBITDA 마진 ≥ 40%)** ✅ (Brad Feld 2015 blog; VC 휴리스틱, 후기 SaaS 한정)

### Step 8. 셀사이드 편향 보정 ✅
- **한국 증권사 매수+적극매수 93.1%, 매도 0.1%** (자본시장연구원 김준석 2025.7.21) ✅
- **12개월 종료 시점 목표가 달성률 38%, 내재수익률이 실제 대비 평균 +15%p 상향편향** (Bradshaw, Brown & Huang 2013 Review of Accounting Studies) ✅
- **활용 규칙**: 리포트 목표가는 "상한"이 아니라 "편향 포함 참고값". **TP는 PDF로 분해 검증**(멀티플 타당성 × 브로커별 EPS × 피어 비교 — §3 경로3·KR/US_DEEPSEARCH PDF 게이트와 동일). 분해 검증 전에는 진입 RR 입력값으로 쓰지 않는다. 리포트 내러티브·시나리오·리스크는 유용하나 숫자는 편향 가정.
- `manage_report list brief=true`로 최근 7일 요약 확인, 전문 필요 시 `read_report_pdf`

### Step 9. Claude(LLM) 편향 자기검증 ✅
- **Anthropic 공식**: 매수 최종 판단 전 다음 체크리스트 (positive instruction 형식)
  - "내가 방금 round number로 끝나는 목표가를 쓰지 않았는가?" (CBEval 2024)
  - "첫 제시된 애널 목표가에 anchor 되지 않았는가?" (Lou & Sun 2024)
  - "매수 편향 리포트만 읽고 판단하지 않았는가?" (자본연 2025)
- XML 태그로 섹션 분리, `<evidence>` 태그 내에만 수치 기재

### Step 10. 사이즈 결정 & 기록
- 3-Gate 통과 수에 따라: 3개 통과 → Standard/Core 후보 / 2개 → Starter / 1개 이하 → 매수 금지
- `set_alert log_type=trade`로 매매 기록 + 매수 사유
- 분기 재검증 일자 캘린더 기록

---

## 2. 미국 딥서치 7단계 (미국 종목 전용)

1. **Altman Z''-Score > 2.6** ✅ (EMS 모델)
2. **Beneish M < -1.78** (비분식) ✅ — M > -1.78이면 분식 의심
3. **PEG < 1 저평가** (Lynch 원문) ✅
4. **Short Float < 20%** ✅ (5% 아님 ❌, 표준 20%+ 고위험)
5. **Net Dilution < 3%** (TDM 2024) ✅
6. **Rule of 40 (SaaS 한정)** ✅
7. **SEC 8-K·10-K 최근 이상 공시** — 내부자 거래 집중(2주+), litigation, going concern 등

**데이터 소스**: SEC EDGAR, yfinance, macrotrends, 미국 애널 레이팅은 `get_us_ratings events/consensus`

---

## 3. 매도 — 3경로 + 능동 재배치 (상세)

> **2026-06-04 정렬**: KR_EXIT(2026-06-01)·US_EXIT(v2 2026-06-01)의 3차 반증검증을 마스터로 동기화. 기존 "매도 4 트리거(유포리아 부분정리 포함)"를 폐기하고 **3경로 + 전량 이진**으로 통합. 정식 매도 절차·전체 인용은 한국 → `KR_EXIT.md` / 미국 → `US_EXIT.md`.

### 절대원칙 0 — 가격 상승폭은 매도 사유가 아니다 ✅
- 목표가 도달 = **매도가 아니라 thesis 재산정**. "충분히 올랐으니 익절"은 금지.
- 근거: Bessembinder (2018 JFE 129(3)) 상위 4%가 시장 net wealth 전부 → 가격 상승으로 winner를 자르면 right-tail compounder를 절단. Barberis-Xiong (2009 JF 64(2)) 부분익절 = 처분효과의 약화된 변형, wealth 감소.
- **전량/전량 이진**: thesis 살아있으면 전량 보유, 깨지면 전량 매도. 부분 30/50% 익절 폐기 (Shiryaev-Xu-Zhou 2008 *Quant Finance* 8(8): goodness index 기준 bang-bang 최적, 내부 해 없음). **예외: 경로 3(자금 재배치)만 부분 비중 조절 허용** — 이는 익절이 아니라 재배치.
- 가격 하락폭도 단독 매도 사유 아님 → 경로 1(thesis) 재점검 알람.
- 학습표본 #1: LS ELECTRIC 4/17 조기매도 → +47~84% 추가상승 미스. "thesis 무결 + 만장일치 컨센 상향 = 상승률 무관 강제 HOLD."

### 경로 1 — Thesis Invalidation (전량 청산) ★최우선
- 매수 시 기록한 핵심 논리가 근본적으로 무효 → **즉시 전량 청산**.
- 근거: Fisher (1958 Ch.6) #1 factual mistake / #2 no longer meets criteria. **Druckenmiller**: "never used stop loss... never hung onto a security if the reason I bought it has changed." Rappaport-Mauboussin (2021) Expectation Gap (높은 문턱).
- **즉시 청산 트리거**: (한국) DART 감사의견 비적정·자본잠식·횡령·분식·상폐. (미국) 8-K Item 4.02 restatement·2.06 impairment·SEC AAER·FDA거부.
- **thesis 손상 (3거래일 쿨링 후 전량 재평가 — 부분50 폐지)**: CEO/CFO 사임(8-K 5.02), 2분기 연속 어닝 미스 + 가이던스 하향. **회사 직접 가이드 하향 > 셀사이드 컨센**(한국 컨센은 매수 93.1%·변경률 2.5%라 후행). 회색지대 "50%만 유보"는 Elliott et al.(2024 RAS 29) 동기화 추론(롱 보유자 상향 편향) 위험 → thesis 판정을 엄격히 하여 이진 결정.
- **⭐ 공통 동인 무효화**: 개별 thesis가 산업 공통 동인(AI 데이터센터 capex 등)에 구조적 의존 시, 그 동인의 구조적 둔화도 thesis 무효화 신호(종목별 지표보다 선행 가능). 임계: 하이퍼스케일러(Amazon/Google/Meta/MS) capex 가이던스 **2분기 연속 하향** 또는 AI 투자 회수기 공식화. 발동 시 자동매도 아님 → **동인에 묶인 보유 종목 전체 재평가**(개별 thesis 재점검). ⚠️ 가격 선반영 가능(효율적 시장; 반도체는 주가가 동인 가격 선행) → 조기경보 아닌 재점검 트리거.

### 경로 2 — Technical Exit (스윙·모멘텀 카테고리만)
- **스윙/모멘텀**: 매수가 **-7~8%** 하드 스톱 (O'Neil *How to Make Money in Stocks*) / **-6~10%** (Minervini 2013). 50MA 고볼륨 이탈 + 5일 내 반등 실패.
- **메인·가치 카테고리: 기술 신호 단독 매도 금지** → 경로 1(thesis) 재점검 알람으로만. **트레일링 스톱 메인 적용 금지** (Dai et al. 2021 평균수익 감소 / Bessembinder 2018 right-tail 절단 / Kaminski-Lo 2014 한국 평균회귀 레짐 stopping premium 음수, ±30% whipsaw).
- **재앙적 갭다운**(한 세션 -15%+, 한국 air-pocket 한정) → 매도 아닌 thesis 재평가 트리거. thesis 무결 시 전량 보유.

### 경로 3 — Opportunity Cost / 능동 재배치 (유일하게 부분 비중 조절 허용) ★활성화
> 경로3는 EXIT 문서엔 있었으나 (a) 마스터에 누락, (b) 반응형으로 묻혀 실제 발동 0건(decision_log)이었다. 이를 **능동 발동 + 검증 기반**으로 활성화한다. 근거: Fisher (1958 Ch.6) #3 — Fisher 본인이 가장 신중한 매도 사유로 명시. **이것은 익절이 아니라 자금 재배치이므로 전량 이진의 예외로 부분 조절 허용.**

- **능동 발동 조건**: 검증된 신규 후보(3-Gate 3/3)가 있는데 **풀투자라 못 사는 상태**면, 자동으로 경로3 재배치 스캔을 발동한다. (반응형 "팔까?" 아님 — 능동. 이것이 "갈아탈 용기"의 시스템화.)
- **RR 비교 (검증 필수)**: 신규 후보와 기존 보유를 **둘 다 PDF로 TP를 분해**(멀티플 타당성 × 브로커별 EPS × 피어 비교; 컨센 평균 금지, 교훈 #9)한 **검증된 forward-RR**로 비교. 셀사이드 목표가 받아쓰기 금지(한국 달성률 38%, Bradshaw 2013). 후보가 **명백히 우위**일 때만 발동.
- **재원 선정 (우꼬리 함정 차단)**: 줄이는 대상 = **"가장 많이 오른 것"이 아니라 "검증된 forward-RR이 가장 낮은 것 / thesis가 가장 약한 것(G 신호 보유)"**. "올랐으니 여력 없다"는 폐기된 "고밸류=비싸다" 논리와 동일 → 금지(Bessembinder right-tail).
- **빈도 제한 (churn·매매중독 차단)**: 종목 전환 최소 **20거래일 대기** 룰 연계. 잦은 갈아타기는 학습표본(테마점핑·하루뒤엎기) 재발. Akepanidtaworn et al.(2023 JoF 78(6)) "sell more thoughtfully."
- **세제 게이트**: 한국 일반투자자 양도세 비과세(거래세 0.15%(2025)/0.20%(2026)만) → 회전 비용 낮음. 미국: 보유<1년 STCG 최대 37%+NIIT 3.8% → 자금이동 보류, 보유≥1년 LTCG 15~20%. 단 세제 절감이 매도 회피 변명이 되어선 안 됨(Munger 1994).
- **실행**: `simulate_trade` 필수 → `set_alert(log_type=compare)` 비교 기록 → `set_alert(decision/trade)`.

### 경로 외 — 포지션 구조 위반 (위험 규칙)
- 단일 종목 **35% 초과**(시총 증가 포함) → 예외 기록 없으면 축소. (※ **사용자 기억상 단일 25% 하드캡** — 본 문서 35%와 불일치, **확인 필요**. HD조선 현재 ~24% = 25% 기준 정합.)
- Core 포지션이 3-Gate 탈락 → Standard 다운그레이드 or 청산.
- 이 축소는 가격 매도가 아니라 비중 관리이며, 그 자금은 경로3 후보로 재배치 가능.

### Core 3거래일 쿨링 (경로1 thesis 손상·경로2에만)
- **근거**: Jegadeesh (1990 JoF) 단기 reversal — 유동성·sentiment 충격은 5일 내 평균회귀 ✅
- **Odean (1998) 인용 금지** — 손절 지연 편향 비판 논문이며 3일 쿨링을 지지하지 않음. ❌
- Thesis-breaking 악재(경로1 즉시청산 트리거)는 쿨링 없이 즉시.

---

## 4. 분기 재검증 5질문 (Core 포지션)

1. **처음 매수 이유가 지금도 유효한가?** (Thesis check)
2. **3-Gate (산업흐름·리더·근거) 여전히 통과하는가?**
3. **펀더멘털 추이**: YoY 매출·영업이익·F-Score 유지 또는 개선?
4. **리더십**: 업종 내 순위, 점유율, 기술 우위 유지?
5. **리스크 지표**: Altman Z'' 악화? Beneish M 악화? Short Float 급증?

5질문 중 2개 이상 실패 → Standard로 다운그레이드 검토 (매도는 §3 경로1 기준).

---

## 5. 현금·USD 관리 (dry-powder)

### 현금 = 발사 가능한 dry-powder (역할 분담) ⚠️ 사용자 결정
현금은 두 역할을 **분리**한다 — 섞으면 둘 다 못 쓴다.

| 레짐 | 현금 | 역할 |
|---|---|---|
| 🟢 평상 | **5~8% (하단 5% 권장)** | 급락 대비 실탄 상비 |
| 🟡 경계 | 8~15% | 실탄 증강 |
| 🔴 위기 | 가능한 풀투자 (실탄 발사) | 급락 시 발사 |

- **급락 발사 (🔴)**: 상비 현금을 **보유 중 thesis 살아있는 최고확신 A급에 추가**. 급락에 새 종목 탐색 아님 — 아는 강한 놈을 싸게 더. (2008·2020 급락에 현금 보유자가 우량주 30~50% 할인 매수.)
- **신규 기회 (평소)**: 감시가 도달한 검증 A급은 **예비금이 아니라 경로3 회전**으로 조달(§3 경로3). 예비금과 분리 — 예비금은 급락 전용, 회전은 평소 기회 전용.
- **비용 인지**: 현금 5%는 평소 연 약 0.5~0.75% drag. 집중 포트의 급락 보험료로 정당. 단 dry powder는 실제로 쓸 때만 값어치 — **인내와 미루기 혼동 금지**(0.5%로 死문화됐던 이력).

### USD 비중 ⚠️
- 현금의 50-70% USD 지향 (개인 가이드, 공식 표준 아님)
- 레퍼런스 앵커: 한국은행 외환보유액 USD 71.9% (2024 말), 국민연금 해외주식 37% — 맥락 다르므로 참고만

### 매크로는 현금·동결 도구 한정 (VIX 분할매수 삭제) ✅
- **기존 "VIX 30-40 / 40-50 / 50+ 분할 매수" 삭제.** 매크로는 **현금 비중 결정에만** 쓴다. VIX로 매수 타이밍/분할 트랜치를 잡지 않는다.
- 근거: VIX 트랜치 임계값은 arbitrary (Faber식 binary; 기존 "75%/100%/66.7% 확률"은 FXEmpire 단일 블로그 샘플 2-4건 — 이미 삭제됨 ❌). 레짐·종목 분리 원칙(매크로는 종목 매수/매도 직접 결정 금지). 현금 단계는 위 표(레짐 키)로 충분.
- 하락장 방어 = 비중 상한 + 좌꼬리 게이트(딥서치 Hard Kill) + thesis 매도(경로1)로, 매크로 타이밍이 아님.
- 에스컬레이션 일수(7/14/30)는 내부 휴리스틱, 학술 근거 없음 ❌ — 가변.

---

## 6. 도구 매핑 (KIS MCP)

| 단계 | 도구 | 용도 |
|---|---|---|
| Step 1 산업 흐름 | `get_sector flow`, `get_macro sector_etf` | 섹터 외인·기관 |
| Step 2 리더 | `get_finance_rank`, `get_alpha_metrics` | F-Score, 재무비율 |
| Step 3 실적 | `get_consensus`, `get_dart mode=report` | 컨센서스, 사업보고서 |
| Step 4 밸류 | `get_stock_detail`, `get_finance_rank sort=15` | PER/PBR/PEG |
| Step 5 수급 | `get_supply history`, `get_supply combined_rank` | 5일 연속 매수 |
| Step 6 기술 | `get_stock_detail mode=volume_profile`, `get_highlow` | 이평·매물대 |
| Step 7 리스크 | `get_alpha_metrics`, `get_us_ratings` | Z''/M/애널 |
| Step 8 리포트 | `manage_report list`, `read_report_pdf` | 셀사이드 |
| Step 9 LLM 편향 | 내부 체크 | 자기검증 |
| Step 10 기록 | `set_alert log_type=trade/decision` | 매매·판단 |
| 매도 비교 | `simulate_trade`, `set_alert log_type=compare` | 경로3 재배치 |
| 재검증 | `get_portfolio_history`, `get_trade_stats` | 성과 복기 |

> ⚠️ 도구 레퍼런스 점검 과제: `get_sector flow`, `get_highlow`, `get_scan preset=` 표기는 현행 스키마와 불일치 가능 — 실제 호출명 검증 후 갱신 필요(`get_supply combined_rank/foreign_rank`, `get_macro convergence` 등으로 대체된 항목 확인).

---

## 7. 셀사이드 리포트 활용 규칙

- **수치는 편향 포함 참고값**: 한국 매수 93.1%, 매도 0.1% (자본연 2025.7), 목표가 달성률 38% (Bradshaw 2013)
- **내러티브·시나리오·리스크**는 유용 → 추출 후 Claude가 재해석
- **목표가 그대로 쓰지 말 것**: **PDF로 TP 분해 검증**(멀티플 × 브로커별 EPS × 피어) 후 진입가·RR 입력값으로 사용 (§3 경로3·딥서치 PDF 게이트와 동일). 분해 없는 단순 "×0.7 할인"은 임시방편 — 분해 검증 우선.
- **한국 컨센 TP 정보가치 (대형주 vs 중소형주)**: 김준석 2026 자본시장연구원 — 대형주 2013 이후 통계적 소멸, 중소형주 잔존. 대형주 TP 90% 도달 단독 매도 트리거 금지.
- **하향 클러스터는 강한 신호 (rarity premium)**: 매수 93.1% 환경에서 하향이 나오면 그 자체가 강한 신호. 매수 의견 변경 없는 보유 의견 추가도 사실상 하향과 동등 해석.
- **삭제된 인용**: "리포트 가치의 41%" — 실제 논문(Lv 2025 arXiv:2502.20489)의 수치는 **10.41%** (1σ 예측 증가 시 1년 수익률 +10.41%p). 41% 오기 ❌

---

## 8. 산업별 리더십 상실 신호

- **산업마다 고유 신호 존재, 일반 규칙 없음**
- 기존 "반도체 DRAM -15%" 류 예시는 Claude 창작 판명 → **완전 제거** ❌
- 각 산업의 핵심 KPI(반도체 재고·ASP, SW ARR·NRR, 방산 백로그 등) 별도 연구 후 기록 필요
- 현재 워치리스트 종목별 개별 문서화 과제 (TODO)

---

## 9. 연구 규율 자가 체크리스트

매 지침 수정 시:
1. 모든 수치에 (저자·연도·저널·URL) 태깅됐는가?
2. 2차 인용은 그렇게 표시됐는가?
3. 확인 불가 항목은 삭제 또는 정성화됐는가?
4. "Odean ↔ Core 쿨링" 같은 오인용 없는가?
5. 원저자 이름(O'Neil/Minervini) 차용 시 그들의 실제 기준과 일치하는가?
6. **문서 정렬**: 마스터 수정 후 KR/US_EXIT·KR/US_DEEPSEARCH와 모순 없는지 diff 대조했는가? (세대 분기 재발 방지)

**Anthropic 공식 권장 (platform.claude.com/docs/en/build-with-claude/prompt-engineering)**:
- Positive instruction > negative
- XML 태그로 구조화
- Long context at top, query at end
- Liu et al. (2023) arXiv:2307.03172 "Lost in the Middle" (TACL) — 중간 섹션 주의력 저하 실재

---

## 10. 참고 문헌 (검증된 핵심 인용)

- **Odean (1998)** "Are Investors Reluctant to Realize Their Losses?" JoF 53(5):1775-1798
- **Barber & Odean (2000)** "Trading Is Hazardous to Your Wealth" JoF 55(2):773-806
- **Bernard & Thomas (1989)** "Post-Earnings-Announcement Drift" JAR 27 Supp:1-36
- **Ivković, Sialm, Weisbenner (2008)** "Portfolio Concentration..." JFQA 43(3):613-655
- **Akepanidtaworn et al. (2023)** "Selling Fast and Buying Slow" JoF 78(6):3055-3098
- **Campbell & Sharpe (2007/2009)** "Anchoring Bias in Consensus Forecasts" JFQA 44(2)
- **Piotroski (2000)** "Value Investing..." JAR 38 Supp:1-41 (F-Score ≥8)
- **Kacperczyk, Sialm, Zheng (2005)** "On the Industry Concentration..." JoF 60(4)
- **Tversky & Kahneman (1974)** Science 185:1124-1131
- **Dawes (1979)** American Psychologist 34(7):571-582
- **Haynes et al. (2009)** NEJM 360:491-499 (체크리스트 사망 47%↓, 합병증 36%↓)
- **Altman (2000/2013)** NYU Stern Z''-Score
- **Beneish (1999)** FAJ 55(5) M-Score
- **Lynch (1989)** *One Up on Wall Street* (PEG)
- **O'Neil** *How to Make Money in Stocks* (7-8% stop)
- **Minervini (2013)** *Trade Like a Stock Market Wizard* (6-10% stop)
- **Bradshaw, Brown & Huang (2013)** RAS 목표가 달성률 38%
- **김준석 (2025.7)** 자본시장연구원 "애널리스트의 낙관적 편향" (한국 매수 93.1%)
- **김준석 (2026)** 자본시장연구원 — 한국 대형주 TP 정보가치 2013 이후 소멸
- **Choe-Kho-Stulz (1999)** JFE 54(2):227-264 — 외국인 매도 destabilize 가설 부정 (한국 시장)
- **Schwab** "Does Market Timing Work?" (즉시투자 = 완벽타이머 92.4%)
- **Jegadeesh (1990)** JoF 단기 reversal
- **Liu et al. (2023)** arXiv:2307.03172 "Lost in the Middle" TACL
- **Lou & Sun (2024)** arXiv:2412.06593 LLM anchoring
- **CBEval (Shaikh et al. 2024)** arXiv:2412.03605 round number bias
- **Zheng et al. (2023)** arXiv:2306.05685 LLM-as-Judge
- **Druckenmiller** The Hustle 2021 interview (thesis-based exit)
- **매도 세대 (2026-06-01 EXIT 동기화)**:
  - **Bessembinder (2018)** JFE 129(3):440-457 — 상위 4% = 시장 net wealth 전부 (가격 상승 ≠ 매도, right-tail)
  - **Shiryaev-Xu-Zhou (2008)** Quant Finance 8(8):765-776 — bang-bang 최적(전량/전량)
  - **Barberis-Xiong (2009)** JF 64(2):751-784 — 부분익절 = 처분효과 변형
  - **Dai-Marshall-Nguyen-Visaltanachoti (2021)** IRF 21(4):1334-1352 — 트레일링 평균수익 감소
  - **Kaminski-Lo (2014)** JFM 18:234-254 — stopping premium 음수
  - **Elliott et al. (2024)** RAS 29:3534-3563 — 롱 보유자 상향 편향
  - **Fisher (1958)** *Common Stocks and Uncommon Profits* Ch.6 — 매도 3경로
  - **Rappaport-Mauboussin (2021)** *Expectations Investing* Ch.7 — Expectation Gap
- **Anthropic Prompt Engineering Docs** platform.claude.com/docs/en/build-with-claude/prompt-engineering

---

## 11. 삭제·수정 변경 이력

### 2026-06-04 6/1 세대 정렬 (EXIT 동기화)

| 항목 | 기존 | 변경 |
|---|---|---|
| §3 매도 프레임 | 4 트리거(thesis/리더십/유포리아/구조) | **3경로(thesis/technical/opportunity) + 전량 이진** |
| 절대원칙 0 | 없음 | **"가격 상승폭 ≠ 매도 사유" 명문화** (Bessembinder/Barberis-Xiong/Shiryaev) |
| 유포리아 부분정리 (1/3·1/2, v1/v2) | 사용 | **폐기** → 재산정 트리거 |
| 부분 익절 | 허용 | **폐기**(bang-bang), 경로3 재배치만 부분 예외 |
| 공통 동인(AI capex) | 없음 | **경로1로 격상**(2분기 연속 하향 → 동인 종목 전체 재평가) |
| 경로3 기회비용/재배치 | 마스터 누락 | **신설·능동 발동**(풀투자+검증 A급후보 시 자동 스캔, PDF분해 RR 비교, 재원=최저RR/최약thesis, 20일 빈도제한, 세제게이트) |
| §5 VIX 분할매수 | 30-40/40-50/50+ | **삭제**(매크로=현금·동결 한정) |
| §5 현금 | 단순 범위 | **dry-powder 역할분담**(🟢상비 5% / 🔴발사=보유 A급 추가 / 신규=경로3 회전) |
| TP 활용(§7·§8) | "×0.7 할인" | **PDF 분해 검증 우선**(딥서치 PDF 게이트 정합) |
| Core 손절·모멘텀 손절 | 별도 항목 | 경로2로 통합 |

### 2026-04-23 전면 개정

| 항목 | 기존 | 변경 |
|---|---|---|
| 확신등급 A/B+/B/B-/C/D | 사용 | 폐기, 3-Gate + 비중 3단계로 교체 |
| Stickel 1991 PEAD | 사용 | Bernard & Thomas 1989/1990으로 교체 |
| Core 3일 쿨링 Odean 근거 | 사용 | Jegadeesh 1990 reversal로 교체 |
| Hard Stop -15~30% | 사용 | 모멘텀 7-10%(O'Neil/Minervini), Core는 thesis 정성화 |
| Short Float 5% | 사용 | 10/20/30% 업계 표준으로 교체 |
| SBC/매출 15% | 사용 | Net Dilution 3% 주지표, SBC는 peer 상대 |
| 현금 20% → 31% 감소 | 사용 | 삭제 |
| 리포트 가치 41% | 사용 | 삭제 또는 10.41% (Lv 2025 정확치)로 수정 |
| McKinsey 200bp | 사용 | 삭제 (컨텍스트 불명) |
| Minervini 3일 쿨링 | 사용 | 삭제 (공식 근거 없음) |
| 한국 매도 0.07% | 사용 | 0.1% (자본연 2025.7 정확치)로 수정 |
| 반도체 DRAM -15% 예시 | 사용 | 완전 제거, 산업별 개별 연구 TODO |
| VIX 75%/100%/66.7% 확률 | 사용 | 삭제, 정성 "극단 영역 = 분할 진입 참고선" |
| 7/14/30일 에스컬레이션 | 사용 | 내부 휴리스틱 표기, 학술 근거 없음 명시 |
| F-Score ≥7 | 사용 | F-Score ≥8 (Piotroski 2000 공식) |
| PEG > 2 고평가 (Lynch) | 사용 | "실무 관행, Lynch 직접 명시 아님" 표기 |
| PEG 게이트 컷 | 사용 | 삭제 (참고 지표로만 활용) |
| 감시가 괴리 상한 규칙 | 사용 | 삭제 |
| RR 역산 공식 | 사용 | 삭제 (단 경로3 재배치는 검증 forward-RR 비교 사용 — 2026-06-04) |
| Process Loss 추적 | 사용 | 삭제 (등급 기반이라 폐기) |
| 섹터 상한 50% | 사용 | 폐지 (집중 투자 원칙, 킬스위치 대체) |
| 시간출구 일수 (B+=45일 등) | 사용 | 삭제 (등급 기반이라 폐기) |
| "🟢 신규 자제" 조항 | 사용 | 삭제 (6주 매수 0건 원인) |
| 현금 🟢 15-20% | 사용 | 🟢 5-8%로 공격적 조정 |
| VCP (Virtual Cash Pool) | 사용 | 삭제 (등급 기반) |

### 2026-05-14 한국 시장 특수성 보강

| 항목 | 기존 | 변경 |
|---|---|---|
| T3 유포리아 조건 2 | "3주 +30%" 단독 | "3주 +30% OR 1년 +200%" 누적 보강 (v2) — ※ 2026-06-04 유포리아 트리거 자체 폐기 |
| T2 가이드 하향 우선 | 컨센·가이드 동등 | **회사 직접 가이드 하향 > 셀사이드 컨센** (KCMI 2026) — 경로1로 승계 |
| 셀사이드 리포트 활용 7 | 일반 보정 | 대형주 TP 정보가치 소멸 + 하향 클러스터 = rarity premium 강한 신호 명시 |
| 김준석 2026 / Choe-Kho-Stulz 1999 | 미인용 | 참고 문헌 추가 |
