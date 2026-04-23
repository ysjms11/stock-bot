# 한국 종목 10 Step 딥서치 프롬프트 템플릿

> 확정일: 2026-04-17 · 원본: `INVESTMENT_RULES.md` (유지)
> [티커], [종목명], [현재가] 등 대괄호만 바꿔서 복붙
> **각 STEP 헤더(`━━ STEP N. ...`)를 반드시 출력**. 생략·통합 금지. 킬 조건 적중 시 즉시 중단.

---

## 🎯 핵심 원칙 (매 실행 전 자각)

1. **Thesis 먼저, 목표가 나중** — 가격 먼저 보면 30% 앵커링 편향 (Campbell & Sharpe 2007)
2. **컨센서스 = 벤치마크, 입력 아님** — 한국 매수 의견 93.7%, 목표가 알파 2013년 이후 0 (KCMI 2026)
3. **5개 변수면 충분** — 5개·40개 정확도 동일 17%, 정보 늘리면 확신만 2배 (Slovic 1973)
4. **K/G 국면 먼저 분류** — 주가 변동이 K(금리·유가·유동성) 주도인지 G(실적·이익) 주도인지 먼저 판별. K 노이즈에 thesis 흔들리지 않기 (Gordon 2026-04-17 추가)

---

## ① 새 종목 풀 딥서치 (~35분, 복붙용)

```
[티커] [종목명] 한국 종목 10 Step 딥서치. 현재가 약 [현재가]원.
각 STEP 헤더를 반드시 출력하고 순서대로 진행. 킬 적중 시 즉시 종료.

━━ STEP 0. 레짐 + K/G 국면 (20초) ━━
봇: get_regime + get_macro(mode="dashboard")
□ 🟢 탐욕 / 🟡 중립 / 🔴 공포 확인
□ 현금 제약 반영 (🔴=방어, 🟢=공격, 🟡=균형)
□ 최근 시장 변동 원인 분류 (Gordon: P=D(1+g)/(K-g)):
  - K(금리·유가·환율·유동성) 주도? → PE 수축 구간, thesis intact 시 진입 기회
  - G(실적·가이던스 하향 연쇄) 주도? → 이 종목 실적 하향 리스크 사전 가중
  - 혼합 → 1차 트랜치만, 2차는 다음 CPI/어닝 후
※ 🔴에도 매수 가능 (개인 구조적 우위). 단 G 쇼크면 A등급도 thesis 재검증 후 진입.

━━ STEP 1. 트렌드 & 유동성 필터 (30초) ━━
봇: get_stock_detail(ticker="[티커]") + get_highlow(ticker="[티커]")
□ Minervini Stage 2 (200MA 위 + 200MA 상승)
□ 20거래일 평균 거래대금 100억원+ (유동성)
□ 52주 고가 대비 -25% 이내 (고가권)
킬: Stage 2 아님 OR 거래대금 <50억 → 탈락

━━ STEP 2. 사업 thesis 형성 (10분) ★목표가 보기 전 ━━
봇: get_dart(mode="report", ticker="[티커]") → get_dart(mode="read", ticker="[티커]")
    + manage_report(action="collect", ticker="[티커]")
    + manage_report(action="list", ticker="[티커]", days=14)
    + get_export_trend(ticker="[티커]") ← 2026-04-23 추가 (수출 의존 업종만)
※ 리포트에서 산업 데이터만 추출. 투자의견·목표가 이 단계에서 완전 무시.

출력 필수:
■ Thesis 한 문장: "[종목명]은 [무엇을] [어떻게] [왜 구조적으로 성장]"
■ 주력 제품/서비스 3개 + 각 매출 비중
■ 경쟁 구도: 국내/글로벌 M/S, 경쟁사 3개
■ 구조적 성장 드라이버 2개 (규제/수요/기술/밸류업)
■ **수출 데이터 교차검증** (수출 의존 업종만, 2026-04-23 추가):
  - get_export_trend 매핑 있으면 → 최근 YoY + 업종 신뢰도 참고
  - 🟢 고신뢰 (반도체 DRAM/HBM): 동행 지표 — "이미 반영 중" 확인용만
  - 🟡 중신뢰 (양극재/2차전지): lag 1~6개월 선행 가능
  - 🔴 저신뢰 (변압기/조선/방산/원전): **수주공시 우선**, 수출 무시
  - 매핑 없음 (내수·금융·IT서비스): skip
  - ※ 수출 시그널은 **thesis 보강**용. 단독 매수 근거로 사용 금지.

킬: 한 문장 thesis 못 쓰면 → 이해 부족, 탈락

━━ STEP 3. 재무 검증 (5분) ★밸류에이션보다 먼저 ━━
봇: get_dart(mode="report", ticker="[티커]") 재무제표 항목 + get_finance_rank(metric="roe|per|pbr|debt_ratio")
□ 매출 성장 3개년 CAGR 양수
□ 영업이익률 업종 평균 이상 (get_finance_rank로 확인)
□ 부채비율 200% 이하 (금융/건설 예외)
□ ICR(이자보상배율) 3x+
□ FCF 최근 4분기 중 3분기+ 양수
□ ROE 10%+ (성장기업은 8%+도 허용)
킬: 부채비율 300%+ OR ICR <1 OR FCF 연속적자 → 탈락

━━ STEP 4. 팩트체크 & 반증 (3분) ━━
봇: get_news(ticker="[티커]", sentiment=true) + get_dart(mode="report", ticker="[티커]")
    + 웹서치: "[종목명] 리스크" "[종목명] 악재"

■ 최근 30일 공시 이벤트: ________
■ 뉴스 감성: 긍정/부정/중립 비율
■ 반증 2개 필수:
  1. 이 thesis가 틀리려면 무엇이 깨져야 하나?
  2. Bear Case 시나리오 (-30% 경로)

킬: 회계/감사 이슈 or 대규모 소송 진행 → 탈락

━━ STEP 5. 수급 확인 (2분) ━━
봇: get_supply(mode="history", ticker="[티커]", days=20)
    + get_broker(ticker="[티커]") + get_market_signal(mode="short_sale", ticker="[티커]")
    + get_market_signal(mode="credit", ticker="[티커]")
□ 외국인 20일 누적 순매수 방향
□ 기관 20일 누적 순매수 방향
□ 공매도 잔고비율 추이 (증가=경고)
□ 신용잔고율 <10% (과열 기준)
□ 증권사 매수 상위 3곳에 외국계 포함 여부
긍정 3개+ → 확신+1, 외국인+기관 동반매도 → 확신-1

⛔━━ PDF 게이트 (STEP 6 진입 조건) ━━⛔
봇: read_report_pdf(ticker="[티커]") — 최소 2개 리포트 PDF 읽기 필수
□ read_report_pdf 최소 2개 리포트 실행 완료?
□ TP 산출 방식(PER/PBR/EV_EBITDA/SOTP/DCF) 확인?
□ 브로커별 EPS 개별 수치 확인? (컨센 avg 사용 금지 — 교훈 #9)
□ FCF·부채비율 Forward 추정치 확인? (DART 과거만으로 부족)
⛔ 미완료 시 STEP 6 진입 금지. manage_report list의 full_text는 대부분 meta_only/truncated → PDF 직접 읽기만 유효.

━━ STEP 6. 밸류에이션 (5분) ★이제 목표가 본다 ━━
봇: get_consensus(ticker="[티커]")
    + get_stock_detail(ticker="[티커]", mode="volume_profile")

■ 리포트 목표가 분해 (PDF에서 확인한 데이터 사용):
  - 사용 멀티플: PER/PBR/EV_EBITDA/SOTP 중 무엇?
  - 적용 배수 vs 업종 피어 배수 (과도한가?)
  - 목표 EPS/BPS 근거 (컨센 대비 +/-)
  - 브로커별 EPS 편차 범위 (최소~최대)
■ 컨센서스 gap:
  - 현재가 vs 컨센 평균 목표가: __% 할인/프리미엄
  - 최근 3개월 컨센 방향: 상향/하향/정체
  - ※ 컨센은 입력 아님, 기대치 벤치마크로만 사용
■ VP 주요 매물대 3개:
  - 상단 저항 ____원 / 중심 ____원 / 하단 지지 ____원

킬: 업종 피어 대비 PER 50%+ 프리미엄 + 성장률 차이로 설명 안 됨 → 보류

━━ STEP 7. RR & 과거 비교 (2분) ━━
봇: get_alerts(brief=true) + get_backtest(ticker="[티커]", strategy="ma_cross")
■ 내 과거 동일 섹터/유사 종목 판단 결과: ________
■ RR 계산:
  - 진입가(감시가): ____원
  - 손절가: ____원 (-__%)
  - 목표가: ____원 (+__%)
  - RR = 목표 상승률 / 손절 하락률 = __:1
  - 등급별 최소 RR: A=1:2, B+=1:2.5, B=1:3, C=매수금지
■ 기회비용: 현 포트 내 최고 확신 종목 대비 더 매력적인가?

━━ STEP 8. 포트 시뮬 (1분) ━━
봇: simulate_trade(ticker="[티커]", action="buy", qty=__, price=__)
□ 단일 종목 비중 <35%
□ 섹터 합계 비중 <50%
□ 현금 최소선 유지 (레짐별)
킬: 35% OR 50% 한도 초과 → 비중 축소 or 탈락

━━ STEP 9. 결정 & 기록 (2분) ━━
봇: set_alert(ticker="[티커]", buy_price=__, grade="__", memo="thesis 한 문장 + Kill Switch")
    + write_file("data/thesis/[티커]_[종목명].md", thesis 본문)

■ 확신등급: A / B+ / B / B- / C (C는 매수 금지)
■ 감시가 (RR 기준): ____원
■ 손절가: ____원
■ 목표가 (2yr): ____원
■ Thesis 한 문장: ________________
■ Kill Switch (thesis 무효화 조건 2개):
  1. ________________
  2. ________________
■ 다음 촉매 (실적 발표일 or 공시 타이밍): ____

출력 템플릿 준수 필수. Bear Case 2개 필수.
```

---

## ② 빠른 등급 판정 (10분, 복붙용)

```
[티커] [종목명] 한국 빠른 등급 판정. 현재가 약 [현재가]원.
봇: get_stock_detail(ticker="[티커]") + get_consensus(ticker="[티커]") + get_alerts(brief=true)
웹서치: "[종목명] 2026 실적 전망" + "[종목명] 목표주가"

0. 레짐 + K/G 국면 분류 (Step 0 축약)
1. Stage 2 & 거래대금 확인 (Step 1)
2. Thesis 한 문장 작성 (Step 2)
3. 부채비율·ICR·FCF 확인 (Step 3)
4. 수급 방향 (Step 5)
5. 컨센 gap + RR (Step 6-7)
→ 등급 / 감시가 / set_alert 기록
```

---

## ③ 라이트 체크 (5분, 기보유 모니터링)

**5개 변수만** — Slovic 1973 기반 (5개 넘으면 정확도 안 올라감):

```
[티커] 라이트 체크.
1. get_stock_detail(ticker="[티커]") — 200MA 위 유지?
2. get_consensus(ticker="[티커]") — 추정치 상향/하향?
3. get_stock_detail — PER/PBR 섹터 대비 이상 없음?
4. get_supply(mode="daily", ticker="[티커]") — 오늘 수급 방향?
5. thesis 한 문장 — 아직 유효?

이상 1개+ → 풀 DD 에스컬레이션
※ 급락 시 K/G 분류 먼저: K 노이즈면 홀드, G 훼손이면 thesis 재검증
```

---

## 🚨 풀 DD 트리거 (하나라도 해당 시)

- 신규 매수 진입
- A / B+ 확신등급 부여
- 처음 분석하는 섹터
- 기보유 종목 thesis 붕괴 의심
- `get_change_scan` 구조적 변화 시그널
- 라이트 체크 5개 중 1개+ 이상

---

## 🔗 관련 문서

- `INVESTMENT_RULES.md` — 전체 투자 규칙 (등급·한도·프로세스)
- `US_DEEPSEARCH_v3.md` — 미국 7단계 (대칭 구조)
- `bot_guide.md` — MCP 도구 용도
- `bot_scenarios.md` — 상황별 도구 조합
