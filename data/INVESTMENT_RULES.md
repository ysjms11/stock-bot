# INVESTMENT_RULES.md — 상세 매매 플레이북

> 프로젝트 지침(매 세션 로드)과 함께 쓰는 참조용 상세 규칙. `read_file("data/INVESTMENT_RULES.md")`로 필요 시 호출.
> 
> 수치 범례: ✅ 원문 검증 / ⚠️ 부분확인·사용자결정 / ❌ 원문 없음·정성화

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
- **활용 규칙**: 리포트 목표가는 "상한"이 아니라 "편향 포함 참고값". 실제 진입가 = 목표가 × 0.7 이하 고려. 리포트 내러티브·시나리오·리스크는 유용하나 숫자는 편향 가정.
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

## 3. 매도 4 트리거 (상세) — 2026-06-01 3차 반증검증 반영

> **⭐ 매도 대원칙 (v개정, KR/US_EXIT와 동기화)**:
> 1. **가격은 정보지 명령이 아니다.** 가격 상승폭(목표가 도달, 3주 +30%, 1년 +200% 등)은 **매도 사유가 아니라 thesis 재산정 트리거**. Bessembinder(2018, *JFE* 129(3)): 시장 net wealth 거의 전부가 상위 4% 종목 → 가격 상승으로 winner를 자르면 right-tail compounder를 놓침. Barberis-Xiong(2009, *JF* 64(2)): 오른 종목 파는 것(처분효과·realization utility)은 wealth 감소.
> 2. **전량 보유 / 전량 매도 이진.** thesis 살아있으면 전량 보유, 깨지면 전량 매도. 부분 정리(1/3·1/2 익절) 원칙 폐기 — Shiryaev-Xu-Zhou(2008, *Quant Finance* 8(8)): bang-bang 최적, 내부 해 없음. 회색 지대 "절반만"은 Elliott et al.(2024, *RAS* 29) 롱 보유자 상향 편향(동기화 추론) 위험. **예외: 포지션 구조(트리거4)·기회비용(자금 재배치)에 의한 비중 조절만.**
> 3. **매도는 thesis 붕괴(트리거1) 또는 명백한 기회비용으로만.** 트레일링 스톱·부분익절·고수익률·단순 고밸류는 매도 사유 아님.

### 트리거 1. Thesis 훼손 — **즉시 전량 청산** ★최우선
- 매수 시 기록한 핵심 논리가 근본적으로 무효
- 예: 경쟁사 기술 우위 역전, 주력 시장 규제 급변, 경영진 사기 의혹
- **Druckenmiller (The Hustle 2021)**: "I've never used stop loss... But I've also never hung onto a security if the reason I bought it has changed." ✅ (기계 스톱 아닌 thesis 기반 exit)
- **Rappaport-Mauboussin (2021) Expectation Gap**: 가격이 함의하는 기대(PIE)를 회사가 충족 못 하면 매도 — 단편 신호 아닌 thesis 붕괴, demanding hurdle.

**⭐ 공통 동인 무효화 (2026-06-01 신설, KR/US_EXIT 경로1과 동일)**:
개별 종목 thesis가 **산업 공통 동인**에 구조적으로 의존하면, 그 동인의 구조적 둔화도 thesis 무효화 신호. 종목별 지표(점유율·ASP·실적)보다 **선행** 가능한 뿌리 신호.

| 공통 동인 | 해당 종목(예시) | 무효화 신호(높은 문턱) |
|---|---|---|
| AI 데이터센터 capex | 반도체(메모리/HBM/GPU/ASIC), 전력기기(변압기), 클라우드 | 하이퍼스케일러(Amazon/Google/Meta/MS) 분기 capex 가이던스 **2분기 연속 하향** 또는 AI 투자 회수기 진입 공식화 |
| (기타 동인은 매수 thesis 작성 시 종목별 명시) | — | — |

- 발동 시 **자동 매도 아님 → 동인에 묶인 보유 종목 전체 재평가**(개별 thesis 재점검). "2분기 연속" 또는 "공식 회수기 선언" 수준 높은 문턱.
- ⚠️ 주의: 공통 동인 신호는 가격에 선반영될 수 있음(효율적 시장; 반도체는 주가가 동인 가격 선행). **"조기경보"로 과신 금지 — thesis 재점검 트리거이지 시점 예측 도구 아님.**

### 트리거 2. 리더십 상실 — 3거래일 쿨링 후 thesis 재평가(전량/보유)
- **연속 2분기 어닝 미스 + 가이던스 하향**
- 근거: Bernard & Thomas (1989 JAR) PEAD 하방 드리프트 60일+; 연속 SUE 확인 시 드리프트 강화
- 조합 레이블은 실무 휴리스틱 (단일 학술 논문 아님) ⚠️
- **한국 시장 특수성**: 셀사이드 컨센 하향은 후행성·매수 편향 93.1% → 컨센 하향 < 회사 가이드 하향 우선 (KCMI 2026, 자본시장연구원 김준석)
- **진짜 트리거 = 회사 직접 가이드 하향** (셀사이드 컨센 무시 가능)
- **액션 (v개정)**: 발동 시 **thesis 재평가 → 깨졌으면 전량 매도, 아니면 전량 보유.** (기존 "부분 50% 축소" 폐기 — 회색 지대 50% 유보는 Elliott 2024 동기화 추론 위험. thesis 판정을 엄격히 하여 이진 결정.)

### 트리거 3. 가격 급등·고밸류 — **매도 아님, thesis 재산정 트리거** (v개정: 부분 정리 폐기)

**개정 사유 (2026-06-01, 3차 검증)**: 기존 v1/v2의 "가격 급등 + 유포리아 → 부분 정리 1/3~1/2"는 다음으로 폐기:
- **Bessembinder(2018)**: 가격 급등으로 winner를 자르면 right-tail compounder 절단. 시장 부의 거의 전부가 그런 종목에서 발생.
- **Barberis-Xiong(2009)**: 부분 익절은 처분효과의 약화된 변형 — 기분은 좋으나 wealth 감소.
- **Chan-Jegadeesh-Lakonishok(1996, *JF* 51)**: 컨센서스 EPS 상향은 **매수 신호**(up-revision이 down-revision을 6개월 7.7% 초과). 기존 v2의 "컨센 상향 클러스터 = 매도 조건"은 **방향이 거꾸로**였음.
- **Shiryaev-Xu-Zhou(2008)**: 부분 매도(내부 해)는 수학적 최적이 아님.

**v개정 규칙**:
- 업종 역사 PER 상단 근접, 3주 +30%·1년 +200% 급등, 외인 보유 비중 급증 — **이 모든 가격·수급 신호는 매도 사유가 아니라 thesis 재산정 트리거.**
- 발동 시: 부분 정리 ❌ → **thesis 재점검.** thesis 무결 시 **전량 보유**(목표가 도달 = 재산정). thesis 손상(트리거1) 또는 명백한 기회비용(트리거3 아래 기회비용) 확인 시에만 매도.
- ⚠️ **단순 고밸류(고PER) 단독 매도 금지** (Fisher 1958 Ch.6 명시적 부정). CAPE류 밸류 지표는 단기 타이밍 무력(Campbell-Shiller: 1년 R²<4%).

  **참고 — 과거 v2 학습(2026-05-14)의 재해석**: 효성중공업/HD현대일렉/LS ELECTRIC 1년 +194~490% 폭등 시점에 "1년 +200% 누적 = 부분 정리" 조건을 추가했으나, 3차 검증 결과 이는 winner 자르기(Bessembinder 절단)였음. 그 종목들이 right-tail이었다면 부분 정리가 손해. → **가격 폭등은 "이 종목이 잘 작동 중"이라는 신호이지 매도 신호가 아님.** 한국 외인 리밸런싱 압력(Choe-Kho-Stulz 1999는 destabilize 부정)도 단독 매도 트리거 아님 — thesis 재점검 시 참고만.

### 트리거 4. 포지션 구조 위반 (유일하게 비중 조절 허용)
- 단일 35% 초과 (시총 증가 포함) — 예외 기록 없으면 축소
- Core 포지션이 3-Gate 탈락 → Standard로 다운그레이드 or 청산
- **주의**: 이는 가격 매도룰이 아니라 비중 관리룰. 전량/전량 이진의 예외(비중 조절 허용). round-trip(본전 회귀) 방어는 가격 트레일링이 아니라 thesis(트리거1) + 포지션 분산으로 관리(검증: 가격 매도룰은 right-tail 절단 비용을 매번 지불). ※ 단일 비중 상한 수치(35%)는 거시·포지션 관리 작업에서 별도 재검토 예정.

### Core 3거래일 쿨링 룰 (트리거 2에만 적용 — v개정: 트리거3은 매도 아니므로 제외)
- **근거**: Jegadeesh (1990 JoF) 단기 reversal — 유동성·sentiment 충격은 5일 내 평균회귀 ✅
- **Odean (1998) 인용 금지** — 이 논문은 "손절 지연 편향"을 비판하는 연구이며 3일 쿨링을 지지하지 않음. 오히려 모멘텀·스윙은 신속 손절(O'Neil/Minervini) 원칙. ❌ 수정됨
- Thesis-breaking 악재(트리거 1)는 쿨링 없이 즉시.

### 모멘텀/스윙 손절 기준 ✅ (스윙 카테고리 한정 — 고정 스톱이지 트레일링 아님)
- **William O'Neil CAN SLIM: 매수가 -7~8% 하드 스톱** (*How to Make Money in Stocks*) ✅
- **Mark Minervini: 평균 -6~7%, 최대 -10%** (*Trade Like a Stock Market Wizard* 2013) ✅
- 기존 "-15~30%" 기준은 원저자 권장과 큰 괴리 → 삭제 ❌
- ⚠️ **트레일링 스톱(고점 추적 손절)은 메인/가치 카테고리 적용 금지** — Dai et al.(2021, *IRF* 21(4)) 평균수익 감소, Bessembinder(2018) right-tail 절단, Kaminski-Lo(2014) 한국 변동성 whipsaw. 스윙의 -7~8%는 고정 진입가 기준 손절이지 트레일링이 아님.

### Core 손절
- 정량 기준 아닌 **thesis 훼손 + 분기 재검증 실패** 정성 판단
- 원저자 이름(O'Neil/Minervini) 차용 불가 — 그들은 모멘텀 매매 전제

---

## 4. 분기 재검증 5질문 (Core 포지션)

1. **처음 매수 이유가 지금도 유효한가?** (Thesis check)
2. **3-Gate (산업흐름·리더·근거) 여전히 통과하는가?**
3. **펀더멘털 추이**: YoY 매출·영업이익·F-Score 유지 또는 개선?
4. **리더십**: 업종 내 순위, 점유율, 기술 우위 유지?
5. **리스크 지표**: Altman Z'' 악화? Beneish M 악화? Short Float 급증?
6. **(신설) 공통 동인 점검**: 이 종목이 의존하는 공통 동인(예: AI capex)이 구조적으로 둔화되고 있는가? (2분기 연속 하향 추세 여부)

질문 중 2개 이상 실패 → Standard로 다운그레이드 검토. (가격이 얼마 올랐는지/빠졌는지는 재검증 사유 아님 — 매도 대원칙 1.)

---

## 5. 현금·USD 관리

### 현금 범위 (사용자 결정)
- 🟢 평상: 5-8%
- 🟡 경계: 8-15%
- 🔴 위기: 가능한 풀투자

### USD 비중 ⚠️
- 현금의 50-70% USD 지향 (개인 가이드, 공식 표준 아님)
- 레퍼런스 앵커: 한국은행 외환보유액 USD 71.9% (2024 말), 국민연금 해외주식 37% — 맥락 다르므로 참고만

### VIX 기반 분할 진입 ⚠️
- **임계값은 관행적 참고선, 고정 룰 아님** — Whaley (2000 JPM) mean-reversion 원리 기반
- VIX 30-40: 스트레스 영역, 분할 매수 시작 검토
- VIX 40-50: 위기 영역, 분할 단계 심화
- VIX >50: 극단 공포 (역사적으로 시장 저점 근접, 단 샘플 극소)
- **삭제**: "75%/100%/66.7%" 구체 확률 (FXEmpire 단일 블로그, 샘플 2-4건) ❌
- **삭제**: "현금 20% → 20년 31% 감소" (출처 확인 불가) ❌
- ⚠️ **레짐/VIX는 현금 비중 도구로만. 개별 종목 매수·매도 결정에 개입 금지** (거시→종목 누수 차단; 종목은 3-Gate·매도 트리거가 단독 판정).

### 에스컬레이션 일수
- 7/14/30일은 **내부 샘플 휴리스틱 (조정 가능)**, 학술 근거 없음 ❌
- 원칙: "공포 지속 시 분할 심화", 정확 일수는 가변

---

## 6. 도구 매핑 (KIS MCP)

| 단계 | 도구 | 용도 |
|---|---|---|
| Step 1 산업 흐름 | `get_sector flow`, `get_macro sector_etf` | 섹터 외인·기관 |
| Step 2 리더 | `get_finance_rank`, `get_alpha_metrics` | F-Score, 재무비율 |
| Step 3 실적 | `get_consensus`, `get_dart mode=report` | 컨센서스, 사업보고서 |
| Step 4 밸류 | `get_stock_detail`, `get_finance_rank sort=15` | PER/PBR/PEG |
| Step 5 수급 | `get_supply history`, `get_scan preset=foreign_streak` | 5일 연속 매수 |
| Step 6 기술 | `get_stock_detail mode=volume_profile`, `get_highlow` | 이평·매물대 |
| Step 7 리스크 | `get_alpha_metrics`, `get_us_ratings` | Z''/M/애널 |
| Step 8 리포트 | `manage_report list`, `read_report_pdf` | 셀사이드 |
| Step 9 LLM 편향 | 내부 체크 | 자기검증 |
| Step 10 기록 | `set_alert log_type=trade/decision` | 매매·판단 |
| 재검증 | `get_portfolio_history`, `get_trade_stats` | 성과 복기 |

---

## 7. 셀사이드 리포트 활용 규칙

- **수치는 편향 포함 참고값**: 한국 매수 93.1%, 매도 0.1% (자본연 2025.7), 목표가 달성률 38% (Bradshaw 2013)
- **내러티브·시나리오·리스크**는 유용 → 추출 후 Claude가 재해석
- **목표가 그대로 쓰지 말 것**: 최소 ×0.7 보정 후 진입가 검토
- **한국 컨센 TP 정보가치 (대형주 vs 중소형주)**: 김준석 2026 자본시장연구원 — 대형주 2013 이후 통계적 소멸, 중소형주 잔존. 대형주 TP 90% 도달 단독 매도 트리거 금지.
- **하향 클러스터는 강한 신호 (rarity premium)**: 매수 93.1% 환경에서 하향이 나오면 그 자체가 강한 신호. 매수 의견 변경 없는 보유 의견 추가도 사실상 하향과 동등 해석. (단 이는 thesis 재점검 트리거 — 컨센 상향은 반대로 매수 신호이지 매도 신호 아님, CJL 1996.)
- **삭제된 인용**: "리포트 가치의 41%" — 실제 논문(Lv 2025 arXiv:2502.20489)의 수치는 **10.41%** (1σ 예측 증가 시 1년 수익률 +10.41%p). 41% 오기 ❌

---

## 8. 공통 동인 & 산업별 리더십 상실 신호 (2026-06-01 개정)

> 기존 "산업별 신호 TODO"를 공통 동인 프레임으로 채움. 매도 트리거 1(thesis 훼손)의 핵심 도구.

### 공통 동인 (다수 종목 관통)
- **AI 데이터센터 capex** — 현 포트 다수 종목(반도체 메모리/GPU/ASIC, 전력기기, 클라우드)의 공통 뿌리. 무효화 신호: 하이퍼스케일러(Amazon/Google/Meta/MS) 분기 capex 가이던스 2분기 연속 하향. 발동 시 동인 묶인 종목 전체 재평가.
  - 현재(2026-04 기준) 합산 capex 추정: AMZN $200B / GOOGL $175-185B / META $115-135B / MSFT $110-120B → $600-700B (+36~67% YoY). 2분기 연속 −10%+ 컷이 트리거.
  - ⚠️ capex는 가격에 선반영될 수 있음 — 조기경보 아닌 재점검 트리거.

### 산업별 고유 KPI (종목 thesis 작성 시 정량 명시)
- **반도체 메모리(SK하이닉스 등)**: HBM 점유율(50% 이하 = thesis 손상), HBM ASP 2분기 연속 QoQ 마이너스, 영업이익 컨센 하회
- **반도체 로직/AI(NVDA/AVGO)**: DC revenue YoY, ASIC 백로그 QoQ, Fwd PE vs Through-Cycle ×2
- **전력기기(효성중공업/HD현대일렉/LS)**: 미국 데이터센터 전력 수주잔고, 변압기 리드타임, 미국 그리드 capex
- **조선(HD한국조선해양)**: LNG선 발주 사이클, IMO 환경규제, 선박 교체 주기 — **AI capex와 독립 동인**(천연가스·환경규제). AI 둔화와 무관하게 따로 평가.
- **SW/SaaS**: ARR 성장률, NRR(<100% = 약화), Rule of 40
- 각 종목 thesis 파일(`data/thesis/[ticker].md`)에 Kill Switch로 정량 기록.

---

## 9. 연구 규율 자가 체크리스트

매 지침 수정 시:
1. 모든 수치에 (저자·연도·저널·URL) 태깅됐는가?
2. 2차 인용은 그렇게 표시됐는가?
3. 확인 불가 항목은 삭제 또는 정성화됐는가?
4. "Odean ↔ Core 쿨링" 같은 오인용 없는가?
5. 원저자 이름(O'Neil/Minervini) 차용 시 그들의 실제 기준과 일치하는가?
6. **(신설) 가격 상승/하락을 매도 사유로 쓰지 않았는가?** (매도 대원칙 1 — 가격은 재산정 트리거)

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
- **Akepanidtaworn et al. (2023)** "Selling Fast and Buying Slow" JoF 78(6):3055-3098 (매도는 매수보다 어렵다; "sell more thoughtfully")
- **Campbell & Sharpe (2007/2009)** "Anchoring Bias in Consensus Forecasts" JFQA 44(2)
- **Piotroski (2000)** "Value Investing..." JAR 38 Supp:1-41 (F-Score ≥8)
- **Kacperczyk, Sialm, Zheng (2005)** "On the Industry Concentration..." JoF 60(4)
- **Tversky & Kahneman (1974)** Science 185:1124-1131
- **Dawes (1979)** American Psychologist 34(7):571-582 (improper linear model — 임의 가중도 임상판단 능가)
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
- **Bessembinder (2018)** "Do Stocks Outperform Treasury Bills?" JFE 129(3):440-457 (상위 4% = net wealth 전부; right-tail)
- **Barberis & Xiong (2009)** "What Drives the Disposition Effect?" JoF 64(2):751-784 (realization utility)
- **Shiryaev, Xu, Zhou (2008)** "Thou Shalt Buy and Hold" Quantitative Finance 8(8):765-776 (bang-bang 최적)
- **Chan, Jegadeesh, Lakonishok (1996)** "Momentum Strategies" JoF 51 (컨센 상향 = 매수 신호)
- **Dai, Marshall, Nguyen, Visaltanachoti (2021)** IRF 21(4):1334-1352 (트레일링 평균수익 감소)
- **Clare, Seaton, Smith, Thomas (2013)** J Asset Mgmt 14:182-194 (추세전환이 최선의 손절)
- **Kaminski & Lo (2014)** JFM 18:234-254 (stopping premium)
- **Elliott, Hobson, Van Landuyt, White (2024)** RAS 29:3534-3563 (롱 보유자 상향 편향)
- **Rappaport & Mauboussin (2021)** *Expectations Investing* (Expectation Gap)
- **Anthropic Prompt Engineering Docs** platform.claude.com/docs/en/build-with-claude/prompt-engineering

---

## 11. 삭제·수정 변경 이력

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
| RR 역산 공식 | 사용 | 삭제 |
| Process Loss 추적 | 사용 | 삭제 (등급 기반이라 폐기) |
| 섹터 상한 50% | 사용 | 폐지 (집중 투자 원칙, 킬스위치 대체) |
| 시간출구 일수 (B+=45일 등) | 사용 | 삭제 (등급 기반이라 폐기) |
| "🟢 신규 자제" 조항 | 사용 | 삭제 |
| 현금 🟢 15-20% | 사용 | 🟢 5-8%로 공격적 조정 |
| VCP (Virtual Cash Pool) | 사용 | 삭제 (등급 기반) |

### 2026-05-14 한국 시장 특수성 보강 (※ 2026-06-01 트리거3 부분 검증으로 폐기됨)

| 항목 | 기존 | 변경 |
|---|---|---|
| T3 유포리아 조건 2 | "3주 +30%" 단독 | "3주 +30% OR 1년 +200%" 누적 보강 (v2) → **2026-06-01 폐기** |
| T3 v2 보조 조건 추가 | 없음 | 외인 보유 5년 평균 +5%p 초과 → **2026-06-01 재산정 참고로 격하** |
| T2 가이드 하향 우선 | 컨센·가이드 동등 | **회사 직접 가이드 하향 > 셀사이드 컨센** (유지) |

### 2026-06-01 3차 반증검증 반영 (KR/US_EXIT 동기화)

| 항목 | 기존 | 변경 | 근거 |
|---|---|---|---|
| 매도 대원칙 | 없음 | "가격은 명령 아닌 정보 / 전량·전량 이진 / thesis로만" 신설 | Bessembinder 2018, Shiryaev 2008, Barberis-Xiong 2009 |
| 트리거 3 (유포리아 부분 정리) | v1+v2 부분 정리 1/3~1/2 | **폐기 → thesis 재산정 트리거.** 가격 급등은 매도 사유 아님 | Bessembinder, Barberis-Xiong, CJL 1996(컨센 상향=매수), Shiryaev |
| 트리거 2 액션 | 부분 50% 축소 | **thesis 재평가 → 전량/보유 이진** | Elliott 2024 회색지대 자기기만 |
| 트리거 1 | 개별 종목 thesis | **공통 동인(AI capex) 무효화 추가** | 검증 steel-man(판단의 질) + 사용자 통찰 |
| 8절 (산업별 신호 TODO) | 비어있음 | **공통 동인 + 산업별 KPI로 채움** | — |
| 모멘텀 손절 | 7-8% | **트레일링 스톱 메인 적용 금지 명시** (스윙 -7~8%는 고정 손절) | Dai 2021, Kaminski-Lo 2014 |
| 재검증 5질문 | 5개 | **공통 동인 점검 6번째 추가** | — |
| 트리거 4 (35%) | 35% | 유지(비중 조절은 이진 예외) + 수치는 거시·포지션 작업서 재검토 표기 | — |
