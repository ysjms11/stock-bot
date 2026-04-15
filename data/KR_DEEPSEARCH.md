# 한국 종목 딥서치 — 10 Step 프레임워크

> 확정일: 2026-04-16 · 근거: INVESTMENT_RULES.md (원본 유지)
> 이 문서는 한국 종목 매수 검증 전용. 미국은 `US_DEEPSEARCH_v3.md` 참조.

---

## 🎯 핵심 원칙

1. **Thesis 먼저, 목표가 나중** — 가격 먼저 보면 30% 앵커링 편향 (Campbell & Sharpe 2007)
2. **컨센서스는 벤치마크, 입력 아님** — 한국 매수 의견 93.7%, 목표가 알파 2013년 이후 0 (KCMI 2026)
3. **5개 변수면 충분** — 5개·40개 정확도 동일 17%, 정보 늘리면 확신만 2배 부풀음 (Slovic 1973)

---

## ✅ 매수 검증 10 Step

**도구 표기**: 🇰🇷 = 한국 전용 · 🇺🇸 = 미국 전용 · 🌐 = 공통

| Step | 이름 | 도구 | 시간 |
|------|------|------|------|
| 0 | 레짐 게이트 | 🌐 `get_regime` | 10초 |
| 1 | 트렌드 & 유동성 필터 | 🌐 `get_stock_detail`(가격+이평), 🇰🇷 `get_highlow` / 🇺🇸 웹서치(52w range) | 30초 |
| 2 | 사업 thesis 형성 | 🇰🇷 `read_report_pdf`(산업분석만) + `get_dart`(사업보고서) / 🇺🇸 웹서치(10-K, 경쟁사) | 10분 |
| 3 | 재무 검증 | 🇰🇷 `get_dart`(재무제표) + `get_finance_rank` / 🇺🇸 웹서치(SEC filing) | 5분 |
| 4 | 팩트체크 & 반증 | 🌐 `get_news`(sentiment) + 웹서치, 🇰🇷 `get_dart`(공시) / 🇺🇸 웹서치(SEC 8-K) | 3분 |
| 5 | 수급 확인 | 🇰🇷 `get_supply` + `get_broker` + `get_market_signal` / 🇺🇸 웹서치(13F, short interest) | 2분 |
| 6 | 밸류에이션 | 🇰🇷 리포트 목표가 분해 + `get_consensus` / 🇺🇸 웹서치(analyst estimates). 🌐 `get_stock_detail`(VP) | 5분 |
| 7 | RR & 과거 비교 | 🌐 `get_alerts`(과거 판단) + `get_backtest` | 2분 |
| 8 | 포트 시뮬 | 🌐 `simulate_trade` | 1분 |
| 9 | 결정 & 기록 | 🌐 `set_alert`(등급 + 손절/목표 + decision) | 2분 |

---

## 📌 Step별 상세

- **Step 0 — 레짐 게이트**: 🟢/🟡/🔴 확인 → 현금 제약 설정. 🔴 시에도 매수 가능 (개인 구조적 우위).
- **Step 1 — 트렌드 필터**: Stage 2인가? 유동성 충분한가? 아니면 Kill. Minervini SEPA 기준.
- **Step 2 — 사업 thesis**: "이 회사가 어떻게 돈 버는지" 이해. **목표가 보기 전**에 한 문장 thesis 작성. 리포트에서 산업 데이터만 추출, 투자의견·목표가는 이 단계에서 무시.
- **Step 3 — 재무 검증**: 매출·마진·FCF·부채 검증. Pabrai 1순위: 레버리지. earnings quality 체크.
- **Step 4 — 반증**: thesis 반증 찾기. "이 thesis가 틀리려면 뭐가 필요한가?"
- **Step 5 — 수급**: 스마트 머니가 thesis와 같은 방향인가?
- **Step 6 — 밸류에이션**: 이제 목표가 본다. 산출 근거 뜯기. 피어 멀티플 크로스체크. VP로 진입가. 컨센은 기대치 갭 확인용만.
- **Step 7 — 비교**: 내 기존 판단 히스토리와 비교. RR 계산. 기회비용 체크.
- **Step 8 — 시뮬**: 비중·섹터·현금 변화. **35% 종목한도 / 50% 섹터한도** 위반 여부.
- **Step 9 — 기록**: 확신등급 부여. 감시가 등록. thesis 한 문장 + Kill Switch 기록. **한 문장으로 못 쓰면 이해가 부족한 것**.

---

## 🔍 라이트 체크 (5분, 기보유 모니터링)

**5개 변수만** (Slovic: 5개 넘으면 정확도 안 올라감):

1. 🌐 `get_stock_detail` — 트렌드 유지? (200MA 위?)
2. 🇰🇷 `get_consensus` or `manage_report` / 🇺🇸 웹서치 — 추정치 상향/하향?
3. 🌐 `get_stock_detail` — PER/PBR 섹터 대비 이상?
4. 🇰🇷 `get_supply`(daily) / 🇺🇸 웹서치 — 오늘 수급 방향?
5. thesis 한 문장 — 아직 유효?

**이상 감지 시 → 풀 DD 에스컬레이션**

---

## 🚨 풀 DD 트리거 (하나라도 해당 시)

- 신규 매수 진입
- A / B+ 확신등급 부여
- 처음 분석하는 섹터
- 기보유 종목 thesis 붕괴 의심
- `get_change_scan`에서 구조적 변화 시그널

---

## 🔗 관련 문서

- `INVESTMENT_RULES.md` — 전체 투자 규칙 (원본 유지)
- `US_DEEPSEARCH_v3.md` — 미국 종목 7단계
- `bot_guide.md` — MCP 도구 용도·타이밍
- `bot_scenarios.md` — 상황별 도구 조합 워크플로우
