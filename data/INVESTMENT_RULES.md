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

## 3. 매도 4 트리거 (상세)

### 트리거 1. Thesis 훼손 — **즉시 청산**
- 매수 시 기록한 핵심 논리가 근본적으로 무효
- 예: 경쟁사 기술 우위 역전, 주력 시장 규제 급변, 경영진 사기 의혹
- **Druckenmiller (The Hustle 2021)**: "I've never used stop loss... But I've also never hung onto a security if the reason I bought it has changed." ✅ (기계 스톱 아닌 thesis 기반 exit)

### 트리거 2. 리더십 상실 — 3거래일 쿨링 후 판단
- **연속 2분기 어닝 미스 + 가이던스 하향**
- 근거: Bernard & Thomas (1989 JAR) PEAD 하방 드리프트 60일+; 연속 SUE 확인 시 드리프트 강화
- 조합 레이블은 실무 휴리스틱 (단일 학술 논문 아님) ⚠️

### 트리거 3. 밸류 극단 + 유포리아 — 부분 정리
- 업종 역사 PER 상단 근접
- 3주 누적 +30% 이상 급등
- 컨센서스 연속 상향 + 거래량 이상 폭증
- 근거: De Bondt-Thaler (1985, 1987) overreaction + Jegadeesh-Titman (1993) momentum reversal. 조합은 실무 합성 ⚠️
- **부분 정리**: 1/3 또는 1/2, 나머지는 thesis 유지 조건부

### 트리거 4. 포지션 구조 위반
- 단일 35% 초과 (시총 증가 포함) — 예외 기록 없으면 축소
- Core 포지션이 3-Gate 탈락 → Standard로 다운그레이드 or 청산

### Core 3거래일 쿨링 룰 (트리거 2·3에만 적용)
- **근거**: Jegadeesh (1990 JoF) 단기 reversal — 유동성·sentiment 충격은 5일 내 평균회귀 ✅
- **Odean (1998) 인용 금지** — 이 논문은 "손절 지연 편향"을 비판하는 연구이며 3일 쿨링을 지지하지 않음. 오히려 모멘텀·스윙은 신속 손절(O'Neil/Minervini) 원칙. ❌ 수정됨
- Thesis-breaking 악재(트리거 1)는 쿨링 없이 즉시.

### 모멘텀/스윙 손절 기준 ✅
- **William O'Neil CAN SLIM: 매수가 -7~8% 하드 스톱** (*How to Make Money in Stocks*) ✅
- **Mark Minervini: 평균 -6~7%, 최대 -10%** (*Trade Like a Stock Market Wizard* 2013) ✅
- 기존 "-15~30%" 기준은 원저자 권장과 큰 괴리 → 삭제 ❌

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

5질문 중 2개 이상 실패 → Standard로 다운그레이드 검토.

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
- **김준석 (2025.7)** 자본시장연구원 "애널리스트의 낙관적 편향"
- **Schwab** "Does Market Timing Work?" (즉시투자 = 완벽타이머 92.4%)
- **Jegadeesh (1990)** JoF 단기 reversal
- **Liu et al. (2023)** arXiv:2307.03172 "Lost in the Middle" TACL
- **Lou & Sun (2024)** arXiv:2412.06593 LLM anchoring
- **CBEval (Shaikh et al. 2024)** arXiv:2412.03605 round number bias
- **Zheng et al. (2023)** arXiv:2306.05685 LLM-as-Judge
- **Druckenmiller** The Hustle 2021 interview (thesis-based exit)
- **Anthropic Prompt Engineering Docs** platform.claude.com/docs/en/build-with-claude/prompt-engineering

---

## 11. 삭제·수정 변경 이력 (2026-04-23 전면 개정)

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
| "🟢 신규 자제" 조항 | 사용 | 삭제 (6주 매수 0건 원인) |
| 현금 🟢 15-20% | 사용 | 🟢 5-8%로 공격적 조정 |
| VCP (Virtual Cash Pool) | 사용 | 삭제 (등급 기반) |
