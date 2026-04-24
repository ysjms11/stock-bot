# 한국 종목 딥서치 프롬프트 v4 (3-Gate AND + 비중 3단계)

> 갱신일: 2026-04-24 · 원본: `INVESTMENT_RULES.md`
> [티커]·[종목명]·[현재가] 대괄호만 바꿔 복붙
> 각 STEP 헤더(`━━ STEP N. ...`) 반드시 출력. 생략·통합 금지.
> **확신등급(A/B+/B/C) 문자 사용 금지**. 비중 = Starter/Standard/Core, 카테고리 = 메인/가치/스윙.

---

## 🎯 절대 원칙

1. **Thesis 먼저, 숫자 나중** — 가격·컨센·목표가 thesis 형성 전 조회 금지.
   Campbell-Sharpe(2007) 30% 앵커 가중; Lou & Sun(2024) arXiv:2412.06593 LLM 동일 편향.
2. **재무 → 밸류 순서** — Piotroski(2000) F-Score 필터 연 +7.5% 초과.
3. **매크로 → 종목** — Brinson-Hood-Beebower(1986) 자산배분이 변동성 93.6% 설명.
4. **K/G 국면 먼저** — K(금리·유가·유동성) 주도 하락은 thesis intact 시 매수 기회, G(실적·가이던스) 주도는 매수 금지.
5. **Hard Kill 5개만** — 감사의견 비적정 / 자본잠식 / zombie(3년 연속 ICR<1 AND 업력 10년+) / 상폐 실질심사 / 분식·횡령. 나머지는 경고+비중 축소 50%로 전환.

---

## ① 라이트 체크 (5분, 기보유 모니터링)

5개 변수만:

```
[티커] 라이트 체크.
1. get_stock_detail(ticker="[티커]") — 200MA 위 유지? Stage 2 훼손?
2. get_consensus(ticker="[티커]") — 30일 EPS·TP 방향?
3. get_supply(mode="daily", ticker="[티커]") — 오늘 외인·기관 방향?
4. get_market_signal(mode="credit", ticker="[티커]") — 신용잔고율 10%+ 근접?
5. thesis 한 문장 — 아직 유효한가?

이상 1개+ → ②번 빠른 판정 에스컬레이션.
급락 시 K/G 분류 먼저: K 노이즈→홀드, G 훼손→thesis 재검증.
```

---

## ② 빠른 3-Gate 판정 (10분)

```
[티커] [종목명] 한국 빠른 3-Gate 스크린. 현재가 약 [현재가]원.

봇 병렬: get_regime + get_macro(mode="dashboard") + get_stock_detail(ticker="[티커]") + get_sector(mode="flow")
봇 직렬: get_consensus(ticker="[티커]") + get_alerts(brief=true)

━━ STEP 0. 레짐 + K/G (30초) ━━
□ 🟢/🟡/🔴 레짐
□ 최근 1주 주도요인: K(금리·환율·유동성) / G(실적·가이던스) / 혼합
→ K→thesis intact 시 1차 트랜치 / G→EPS 하향 멈춘 뒤 / 혼합→50% 축소

━━ STEP 1. Hard Kill 체크 (1분) ━━
5개 중 하나라도 해당 시 즉시 종료:
□ 감사의견 한정·의견거절·부적정
□ 자본잠식 (자기자본 음수)
□ zombie (3년 연속 ICR<1 AND 업력 ≥10년, 한은 2025.10 공식)
□ 상장폐지 실질심사 / 관리종목
□ 분식·횡령·배임 진행

━━ STEP 2. 3-Gate 속판 (6분) ━━
① 산업 흐름 (3중 2 통과)
  (a) 섹터 ETF > 200MA
  (b) 업종 외인 20일 누적 순매수 > 0
  (c) get_sector(mode="flow") 양수

② 리더 (3중 2 통과)
  (a) 시총 업종 Top 30%
  (b) F-Score ≥8 (Piotroski 2000 저PBR) OR G-Score ≥6 (Mohanram 2005 고PBR 성장)
  (c) ROE·OPM 업종 중위 이상

③ 근거 (3중 2 통과)
  (a) 30일 컨센 EPS·TP 상향
  (b) 외인+기관 10일 누적 순매수 > 0
  (c) 기술적 Stage 2 유지

━━ STEP 3. 비중·카테고리 배정 (2분) ━━
• 3게이트 통과 → Core 후보 (15~25%)
• 2게이트 통과 → Starter 한정 (3~5%)
• 1게이트 이하 → 워치리스트만, 매수 금지

카테고리:
• 메인 — 산업 사이클 리더, 장기 thesis
• 가치 — 배당·현금흐름 방어, 12~36M hold
• 스윙 — 단기 모멘텀, 손절 -7~10% (O'Neil/Minervini)

출력: 3-Gate 결과 + 비중 + 카테고리 + set_alert(buy_price) 등록.
```

---

## ③ 풀 딥서치 (~35분, 복붙용)

```
[티커] [종목명] 한국 풀 딥서치 10 Step. 현재가 약 [현재가]원.
각 STEP 헤더 반드시 출력. Hard Kill 적중 시 즉시 종료.
호출 순서 엄수: 병렬 가능 묶음 한 번에, 직렬 종속은 결과 후.

━━ STEP 0. 레짐 + K/G 국면 (30초) ━━
봇 병렬: get_regime + get_macro(mode="dashboard") + get_sector(mode="flow")

<regime>🟢 공격 / 🟡 중립 / 🔴 위기</regime>
<cash_floor>🟢 5~8% / 🟡 8~15% / 🔴 5%까지</cash_floor>
<kg_classification>
  K 주도: US10Y ±30bp/주 OR 한국 3Y ±15bp/주 OR VIX≥25 OR DXY ±2% OR 원달러 ±2% 중 3개+ 동시
  G 주도: KOSPI200 beat<50% OR 컨센 EPS 2주 변화 ≤-2%
  혼합
</kg_classification>
<entry_strategy>
  K → thesis intact 시 1차 트랜치 진입 가능
  G → EPS 궤적 재확인, 하향 멈춘 뒤 진입
  혼합 → 50% 축소 트랜치만
</entry_strategy>

━━ STEP 1. 트렌드·유동성 필터 (1분) ━━
봇 병렬: get_stock_detail(ticker="[티커]") + get_highlow

Minervini Stage 2 필수 5개 (3개 미만 통과 시 보류, 2주 후 재체크):
□ 주가 > 150MA AND 200MA
□ 150MA > 200MA
□ 200MA 최소 1개월 상승
□ 주가 > 50MA
□ 52주 저점 대비 +25% 이상

유동성 (20일 평균 거래대금 — 실무 관행 참고선):
□ 대형주(시총 5조+): 100억+ 권장
□ 일반: 50억+ 권장
□ 소형·가치: 30억+ 허용 (비중상한 1.5%)

━━ STEP 2. 사업 Thesis 형성 (10분) ★목표가·컨센 보기 전 ━━
봇 직렬: get_dart(mode="report", ticker="[티커]") → get_dart(mode="read", ticker="[티커]")
        + manage_report(action="collect", ticker="[티커]")
        + manage_report(action="list", ticker="[티커]", days=14)

※ 이 단계에서 투자의견·목표가·컨센 절대 조회 금지 (앵커링).
※ 리포트 본문은 산업 데이터·CAPEX·수주·경쟁 구도만 추출.

<thesis>
  한 문장: "[종목명]은 [무엇을] [어떻게] [왜 구조적으로 성장]"
  주력 제품/서비스 3개 + 매출 비중
  경쟁 구도: 국내·글로벌 M/S, 경쟁사 3개
  구조적 성장 드라이버 2개 (규제/수요/기술/밸류업)
</thesis>

→ 한 문장 thesis 작성 불가 시 이해 부족으로 탈락.

━━ STEP 3. 재무 검증 (5분) ★밸류에이션 이전 ━━
봇: get_finance_rank + get_dart(재무제표 추출)
※ F-Score/G-Score 봇 미지원 시 수동 계산

<financials>
  F-Score 또는 G-Score
  FCF Yield / CFO 최근 4분기 부호
  매출 3Y CAGR
  영업이익률 vs 업종 중앙값
  부채비율 vs 업종 중앙값 (제조 150%↑ 경계, 건설 250%↑ 정상, 금융 BIS 별도)
  ICR (3년 연속 <1 AND 업력 10년+ 만 Hard Kill)
</financials>

Hard Kill (5개):
□ 감사의견 비적정 / 자본잠식 / zombie / 상폐 실질심사 / 분식

경고 + 비중 축소 50% 전환:
□ 1회성 ICR<1 / 부채비율 업종 중앙값 1.5~2배 / CFO 1~2분기 음수

면책 트랙 (성장 사이클):
□ G-Score≥5 AND 매출 YoY>20%: 부채·FCF 경고 면제
□ CAPEX/매출>15% AND 업력<10년: ICR 단독 경고 면제
→ Physical AI·2차전지·바이오 자동 탈락 방지

━━ STEP 4. 팩트체크·반증 (3분) ━━
봇 병렬: get_news(ticker="[티커]", sentiment=true) + get_dart(mode="insider", ticker="[티커]", days=30)
웹서치: "[종목명] 리스크" + "[종목명] 악재"

<factcheck>
  최근 30일 공시 이벤트 (유상증자·자사주·감자·소송·내부자)
  뉴스 감성: 긍정/부정/중립
  내부자 거래: 3명+ 동시 매수 클러스터
  유상증자: 주주배정(중립) / 일반공모·제3자배정(악재)
  자사주: 매입만(약) vs 소각(강한 양+)
</factcheck>

반증 2개 필수:
  1. thesis가 틀리려면 무엇이 깨져야?
  2. Bear Case 시나리오 (-30% 경로)

Hard Kill: 감사의견 비적정 or 분식·횡령 진행 → 탈락

━━ STEP 5. 수급 확인 (3분) ━━
봇 병렬: get_supply(mode="history", ticker="[티커]", days=20)
        + get_broker(ticker="[티커]")
        + get_market_signal(mode="short_sale", ticker="[티커]")
        + get_market_signal(mode="credit", ticker="[티커]")
        + get_market_signal(mode="lending", ticker="[티커]")

<supply>
  외인 20일 누적 방향
  기관 20일 누적 방향
  공매도 잔고비율 추이
  대차잔고 5일 추이 (한국 실증상 공매도비중보다 예측력 강함)
  신용잔고율 (10%+ 과열 경고, 실무 관행)
  증권사 매수 상위 3곳 외국계 포함 여부
  참고: 외인-KOSPI 상관 0.54 (Choe-Kho-Stulz 1999)
</supply>

외인+기관 동반 5일+ 순매수 → 게이트 ③ 기여
외인 5일 연속 순매도 + 대차잔고 증가 → 진입 금지
신용잔고율 10%+ → 비중 상한 50% 축소

⛔━━ PDF 게이트 (STEP 6 진입 조건) ━━⛔
봇: read_report_pdf(ticker="[티커]") — 최소 2개 리포트 PDF 직접 읽기

□ PDF 2개+ 실행 완료?
□ TP 산출 방식(PER/PBR/EV_EBITDA/SOTP/DCF) 확인?
□ 브로커별 EPS 개별 수치 확인? (컨센 평균 사용 금지, 교훈 #9)
□ Forward FCF·부채비율 추정치 확인?

⛔ 미완료 시 STEP 6 진입 금지. manage_report list의 full_text는 meta_only/truncated 다수.

━━ STEP 6. 밸류에이션 (5분) ★이제 목표가 공개 ━━
봇 직렬: get_consensus(ticker="[티커]") + get_stock_detail(ticker="[티커]", mode="volume_profile")

<valuation>
  리포트 TP 분해 (PDF 기반):
    멀티플: PER/PBR/EV_EBITDA/SOTP
    적용 배수 vs 업종 피어
    목표 EPS/BPS + 브로커 편차 범위
  Forward PEG (참고 지표):
    <1 저평가 (Lynch 1989 원문)
    1~1.5 정상
    1.5~2 실무 경계
    >2 고평가 경고 (게이트 아닌 참고)
  컨센 gap:
    현재가 vs 평균 TP: __%
    30일 컨센 방향
    ※ 한국 매수 93.1%, 매도 0.1% (자본시장연구원 2025.7)
    ※ 목표가 달성률 38% (Bradshaw 2013)
    ※ 벤치마크로만 사용, 절대 기준 X
  VP 매물대 3개: 상단/중심/하단
</valuation>

경고 (Hard Kill 아님):
□ 업종 피어 대비 PER 50%+ 프리미엄 AND Forward PEG>2 → 관찰 라벨

━━ STEP 7. RR·과거 비교 (2분) ━━
봇 직렬: get_alerts(brief=true) + get_backtest(ticker="[티커]", strategy="ma_cross")

<rr>
  과거 동일 섹터·유사 종목 결과 (get_trade_stats 참조)
  진입가(감시가): __원
  손절가: __원 (-__%)
  목표가: __원 (+__%)
  RR = __:1

  최소 RR (3-Gate 통과 수 기반):
    3통과 (Core 후보): 1:2+
    2통과 (Starter 한정): 1:2.5+
    1통과: 매수 금지

  기회비용: 현 포트 최고 확신 종목 대비 더 매력적?
</rr>

━━ STEP 8. 포트 시뮬 + 비중·카테고리 확정 (2분) ━━
봇: simulate_trade(buys=[{ticker:"[티커]", qty:__, price:__}])

<position_sizing>
  3-Gate 통과 개수: __/3
  레짐 승수: 🟢 ×1.0 / 🟡 ×0.8 / 🔴 ×0.5

  기본 비중 (🟡 중립):
    3통과 → Core 15~25%
    2통과 → Starter 3~5%
    1 이하 → 금지
  예외 25~35%: 강한 사이클 리더, 사전 기록 필수
  절대 금지 >35%

  신규 진입 항상 Starter부터 (Barber-Odean 2000 과잉확신 경계)
  카테고리: 메인 / 가치(12~36M) / 스윙(1~6M, 손절 -7~10%)

  한도:
  □ 단일 종목 <35%
  □ 섹터 상한 없음 (집중 투자 원칙, 킬스위치 2~3개 대체)
  □ 현금 최소선 유지
</position_sizing>

━━ STEP 9. 결정·기록 (2분) ━━
봇: set_alert(ticker="[티커]", buy_price=__, memo="thesis + Kill Switch")
  + set_alert(log_type="decision", ...)
  + write_file("data/thesis/[티커]_[종목명].md", thesis 본문)

<decision>
  3-Gate 결과: ①__ / ②__ / ③__ (통과 __/3)
  비중 단계: Starter __% / Standard __% / Core __%
  카테고리: 메인 / 가치 / 스윙
  감시가 (RR 충족): __원
  손절가: __원 (모멘텀·스윙 -7~10% O'Neil/Minervini, Core는 thesis 정성)
  목표가 (2yr): __원
  Thesis 한 문장: ________________
  Kill Switch (thesis 무효화 조건 2~3개):
    1. ________________
    2. ________________
  Bear Case (2개 필수):
    1. ________________
    2. ________________
  승격 조건:
    Starter → Standard: ≥5거래일 + +5% & 20MA 위 + thesis 확증 1개 + 외인·기관 3일 중 2일 순매수
    Standard → Core: ≥10거래일 + 누적 +10% & 60MA 위 + 확증 2개 + 외인·기관 10일 중 6일
    ※ Jegadeesh 1990 단기 reversal: 매수 후 <3일 추가매수 금지
  다음 촉매: __
</decision>

XML 태그 준수. Bear Case 2개 필수. 확신등급 문자 사용 금지.
```

---

## 🚨 풀 DD 트리거

- 신규 종목 매수 진입
- 3-Gate 3개 통과 후 Core 승격 검토
- 처음 분석하는 섹터
- 기보유 종목 thesis 붕괴 의심
- get_change_scan 구조적 변화 시그널
- 라이트 체크 1개+ 이상
- 급락 + K/G 분류에서 G 훼손

---

## 🔗 관련 문서

- `INVESTMENT_RULES.md` — 전체 투자 규칙
- `US_DEEPSEARCH.md` — 미국 7단계
- `bot_guide.md` — MCP 도구 용도

---

## 📚 학술·실무 근거

| 원칙 | 핵심 출처 |
|---|---|
| Thesis 먼저 (앵커링) | Tversky-Kahneman(1974); Campbell-Sharpe(2007) JFQA 44(2); Lou & Sun(2024) arXiv:2412.06593 |
| 재무→밸류 | Piotroski(2000) JAR 38 |
| 자산배분 우선 | Brinson-Hood-Beebower(1986) FAJ 42(4) |
| F-Score | Piotroski(2000) ≥8 원문, 저PBR 가치주 |
| G-Score | Mohanram(2005) Rev Acc Studies 10, ≥6 고PBR 성장 |
| Zombie 정의 | OECD Adalet McGowan(2017); 한국은행 2025.10 |
| 3-Gate 멀티팩터 | Asness-Moskowitz-Pedersen(2013) JoF 68(3) |
| 한국 외인 정보 | Choe-Kho-Stulz(1999) JFE 54(2) |
| 한국 컨센 편향 | 자본시장연구원 김준석(2025.7) 매수 93.1%, 매도 0.1% |
| 목표가 달성률 | Bradshaw-Brown-Huang(2013) RAS 38% |
| 한국 PEAD | 이병주-김동철(2017) 한국재무학회 |
| 공매도 재개 | FSC 2025.03.31 전면 재개 |
| 단기 Reversal | Jegadeesh(1990) JoF 45(3) |
| 개인 과매매 | Barber-Odean(2000) JoF 55(2) |

## ⚠️ 실무 관행 라벨 (학술 엄밀 근거 부재)

- 거래대금 50억/100억 임계값
- Forward PEG 1.5~2 경계 (Lynch 원문은 <1만)
- 신용잔고율 10%+ 과열
- 부채비율 업종별 기준 (건설업만 대한건설협회 공식)

이 수치들은 "참고치"로만. 봇 `get_backtest`, `get_trade_stats`로 보완 교정.

## ❌ v3에서 완전 삭제

- 확신등급 A/B+/B/B-/C/D 6단계
- 등급별 RR 공식
- 섹터 상한 50% (킬스위치 대체)
- "한국 매수 93.7%" (93.1%로 수정)
- "Slovic 17% 정확도" 구체 수치 (원문 확인 불가 → 정성화)
- "5개 변수 40개 정확도 동일" (Slovic 1973 미출판 WP)

---

## 변경 이력

- **2026-04-24 v4**: 전면 개정
  - 확신등급 6단계 → 비중 3단계 (Starter/Standard/Core) + 카테고리
  - Hard Kill 5개로 축소 (감사·자본잠식·zombie·상폐·분식)
  - 경고 + 비중 축소 50% 전환 신설
  - 성장 트랙 면책 (G-Score≥5 + 매출 YoY>20%)
  - 3-Gate 내부 2/3 OR + 게이트 간 부분 AND
  - F-Score ≥8 (Piotroski) OR G-Score ≥6 (Mohanram) 병기
  - PEG 게이트 폐지 → 참고 지표
  - 섹터 상한 폐지 (킬스위치 2~3개 대체)
  - 승격 조건 (Starter→Standard→Core) 신설
  - 한국 매수 93.1% 정확 수치로 수정 (자본연 2025.7)
- 2026-04-17: K/G 국면 STEP 0 추가 (Gordon)
- 2026-04-15: PDF 게이트 신설 (교훈 #9)
