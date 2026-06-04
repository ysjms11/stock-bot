# 한국 종목 딥서치 프롬프트 v5 (3-Gate + 비중 3단계)

> 갱신일: 2026-06-01 (v5, 통합 반증검증 반영) · 원본: `INVESTMENT_RULES.md` · 페어: `KR_EXIT.md`
> [티커]·[종목명]·[현재가] 대괄호만 바꿔 복붙
> 각 STEP 헤더 반드시 출력. 확신등급(A/B+/B/C) 문자 금지. 비중 = Starter/Standard/Core, 카테고리 = 메인/가치/스윙.

---

## ⚠️ v5 핵심 전환 (2026-06-01) — 미국 원리 + 한국 특수성

매수 게이트 전체를 통합 반증검증(US_DEEPSEARCH 통합검증 + 한국 통합검증)으로 재설계.

**미국과 같은 것 (옮김):**
1. **단순화 비대칭** — 우꼬리(대박) 차단 게이트(극단밸류 veto·K/G 매수사유·z-score)는 완화/삭제, 좌꼬리(부도·영구손실) 차단은 강화·정량화. Bessembinder(2018): 상위 4%가 net wealth 전부 + 57% 종목 T-bill 미달. 한국은 코스닥 부실 비중 높아 더 강하게 작용.
2. **가격 상승은 매도 사유 아님** — 목표가 도달·고수익률은 thesis 재산정 트리거. 전량 보유/전량 매도 이진.
3. **Kill Switch pre-commit** — 매수 시점 정량 무효화 조건 작성, KR_EXIT와 묶음.

**한국이 다른 것 (검증 결론):**
1. **K/G는 "체제 게이트"로 재배치** — 매수 사유로는 삭제(Cochrane 2011 실시간 분해 불가 + 인지편향). 단 한국은 미 통화정책 spillover 강함(Lastauskas-Nguyen 2024)·위기 시 외인 충격 영구화(Yang 2017) → **위기 진입 시 매수 동결 게이트로만** 신설. 임계는 평시 pre-commit.
2. **성장 트랙 면책 삭제** — "G-Score≥5 + 매출 YoY>20% → 부채·FCF 면제"는 OECD 좀비 정의(McGowan 2017 WP 1372)·Mohanram 원전과 충돌(Mohanram: "수익 대부분 short side"). 한국 바이오 상폐 사례가 위험 실증. → **삭제. Hard Kill은 절대 면제 불가.**
3. **임원 거래 매수신호 불가** — 한국 30일 사전공시제(2024.7)는 미국 Form 4(거래 후 2일)와 달라 cooling-off가 정보우위 소거. 매수 카운트 불가, 매도 회피만.
4. **12M 모멘텀 카운트 불가** — 한국은 전통 12개월 모멘텀이 음의 수익(Chae-Eom 2009). Value(BM, low investment)는 작동(Kang-Kang-Kim 2019). → 모멘텀 빼고 Value 우선.
5. **개인 순매수 = 역신호** — 개인 우세 종목 underperform 일관 실증(Park-Ok-Kim 2024, 김민기·김준석 2022).
6. **Kill Switch 변수 = raw 재무 + EPS revision** — 셀사이드 매도의견 0.1%/변경률 2.5%(김준석 2025.8)라 트리거 불가. 컨센 의견·TP는 2013 이후 알파 소멸. EPS estimate revision만 살아남음.

**삭제된 것**: K/G 매수사유(STEP 0), 성장 트랙 면책, 12M 모멘텀 근거, 부분축소 50%(→전량 이진).
**강화된 것**: 체제 게이트, 선행 좌꼬리(Altman-Eom-Kim), 수급 신호 차등, Kill Switch 정량화.

---

## 🎯 절대 원칙

1. **Thesis 먼저, 숫자 나중** — 가격·컨센·목표가 thesis 형성 전 조회 금지. Campbell-Sharpe(2009, JFQA 44(2)) 컨센 30% 앵커 가중.
2. **재무 → 밸류 순서** — Piotroski(2000) F-Score 필터 연 +7.5% 초과.
3. **가격 상승 ≠ 매도 사유** — 목표가 도달은 재산정(매도 아님). Bessembinder(2018) 우꼬리 절단 회피. 매도는 thesis 붕괴(Kill Switch)로만, 전량 이진.
4. **K/G는 매수 사유 아님, 체제 게이트로만** — 실시간 K/G 분해 불가(Cochrane 2011). 단 위기 진입 신호(원/달러 급변·외인 대량유출·VIX 동반상승) 2개+ 시 신규 매수 동결.
5. **Hard Kill 5개 — 면책 불가.** 감사의견 비적정 / 자본잠식률 50% 2년 or 완전잠식 / 한계기업(ICR<1 3년 연속) / 상폐 실질심사 / 분식·횡령. **성장 트랙 면책 적용 불가.**

---

## ① 라이트 체크 (5분, 기보유 모니터링)

```
[티커] 라이트 체크.
1. get_stock_detail(ticker="[티커]") — 200MA 위 유지? Stage 2 훼손?
2. get_consensus(ticker="[티커]") — 30일 EPS estimate revision 방향? (TP·의견 level 무시, EPS만)
3. get_supply(mode="daily", ticker="[티커]") — 오늘 외인·기관 방향? (개인 순매수 급증은 역신호)
4. get_market_signal(mode="credit", ticker="[티커]") — 신용잔고율 10%+ 근접?
5. thesis 한 문장 + Kill Switch — 아직 유효한가? raw 분기재무 트리거 발동?

이상 1개+ → ②번 빠른 판정.
※ 가격이 많이 올랐다는 이유로는 에스컬레이션 금지(절대원칙 3). thesis·좌꼬리만.
```

---

## ② 빠른 3-Gate 판정 (10분)

```
[티커] [종목명] 한국 빠른 3-Gate 스크린. 현재가 약 [현재가]원.

봇 병렬: get_regime + get_macro(mode="dashboard") + get_stock_detail(ticker="[티커]") + get_sector(mode="flow")
봇 직렬: get_consensus(ticker="[티커]") + get_alerts(brief=true)

━━ STEP 0. 체제 게이트 (30초) — 매수 동결 여부만 ━━
□ 🟢/🟡/🔴 레짐 (현금 비중 도구)
□ 위기 진입 신호: 원/달러 일일 급변 OR 외인 대규모 패시브 유출 OR VIX·MOVE 동반 상승 — 2개+ 동시?
→ 2개+ → 신규 매수 동결 (K/G 분류로 "사라/마라" 판단 안 함 — 위기면 멈춤)
→ 미발동 → 진행

━━ STEP 1. Hard Kill 체크 (1분) — 면책 불가 ━━
5개 중 하나라도 해당 시 즉시 종료:
□ 감사의견 한정·의견거절·부적정
□ 자본잠식률 50% 2년 or 완전자본잠식
□ 한계기업 (ICR<1 3년 연속 — 한국은행/OECD McGowan 2017 정의)
□ 상장폐지 실질심사 / 관리종목
□ 분식·횡령·배임 (자기자본 5%+) 진행
※ 성장 트랙 면책 적용 불가

━━ STEP 2. 3-Gate 속판 (6분) ━━
① 산업 흐름 (3중 2 통과)
  (a) 섹터 ETF > 200MA
  (b) 업종 외인 20일 누적 순매수 > 0
  (c) get_sector(mode="flow") 양수

② 리더 (3중 2 통과)
  (a) 시총 업종 Top 30%
  (b) F-Score ≥8 (Piotroski 2000 저PBR, 주) OR G-Score ≥6 (Mohanram 2005 고PBR, 산업중앙값 상대화, 보조)
  (c) ROE·OPM 업종 중위 이상

③ 근거 (3중 2 통과) — 신호 차등
  (a) 30일 컨센 EPS estimate 상향 revision (TP·의견 level 아님)
  (b) Value 신호: 저PBR or low investment (한국 작동, Kang-Kang-Kim 2019)
  (c) 외인+기관 동시 10일 누적 순매수 > 0 + 가격 미반영
  ※ 12M 가격 모멘텀 제외(한국 음의 수익, Chae-Eom 2009)
  ※ 외인 단독 일간·셀사이드 매수의견·임원 매수 카운트 불가

━━ STEP 3. 비중·카테고리 (2분) ━━
• 3게이트 → Core 후보 (15~25%)
• 2게이트 → Starter 한정 (3~5%)
• 1 이하 → 워치리스트만

카테고리: 메인(장기 thesis) / 가치(배당·현금흐름 12~36M) / 스윙(단기 모멘텀, 손절 -7~10% O'Neil)

출력: 3-Gate 결과 + 비중 + 카테고리 + Kill Switch + set_alert(buy_price).
```

---

## ③ 풀 딥서치 (~35분, 복붙용)

```
[티커] [종목명] 한국 풀 딥서치 10 Step. 현재가 약 [현재가]원.
각 STEP 헤더 반드시 출력. Hard Kill 적중 시 즉시 종료.
대원칙: 가격 상승은 매수회피/매도 사유 아님. 좌꼬리(부도) 게이트 엄격, 우꼬리(밸류) veto 없음.

━━ STEP 0. 체제 게이트 (30초) — 매수 동결 여부 ━━
봇 병렬: get_regime + get_macro(mode="dashboard") + get_sector(mode="flow")

<regime>🟢 공격 / 🟡 중립 / 🔴 위기 (현금 비중 도구)</regime>
<cash_floor>🟢 5~8% / 🟡 8~15% / 🔴 5%까지</cash_floor>
<crisis_gate>
  위기 진입 신호 (평시 pre-commit 임계):
  □ 원/달러 일일 변동성 임계 초과
  □ 외인 대규모 패시브 유출
  □ VIX/MOVE 동반 상승
  → 2개+ 동시 = 신규 매수 동결 (임계 미달까지 유지)
  ※ K/G로 "이 하락은 사도 돼/안 돼" 판단 금지 — Cochrane 2011 실시간 분해 불가. 위기면 멈출 뿐.
</crisis_gate>

━━ STEP 1. 트렌드·유동성 필터 (1분) ━━
봇 병렬: get_stock_detail(ticker="[티커]") + get_highlow

Minervini Stage 2 (3개 미만 시 보류, 2주 후 재체크):
□ 주가 > 150MA AND 200MA  □ 150MA > 200MA  □ 200MA 1개월 상승
□ 주가 > 50MA  □ 52주 저점 +25%+

유동성 (실무 관행 참고선):
□ 대형주(5조+): 100억+  □ 일반: 50억+  □ 소형·가치: 30억+ (비중 1.5%)

━━ STEP 2. 사업 Thesis 형성 (10분) ★목표가·컨센 보기 전 ━━
봇 직렬: get_dart(mode="report", ticker="[티커]") → get_dart(mode="read", ticker="[티커]")
        + manage_report(action="collect", ticker="[티커]") + manage_report(action="list", ticker="[티커]", days=14)
※ 투자의견·목표가·컨센 조회 금지 (앵커링). 산업 데이터·CAPEX·수주·경쟁만 추출.

<thesis>
  한 문장: "[종목명]은 [무엇을] [어떻게] [왜 구조적 성장]"
  주력 제품 3개 + 매출 비중 / 경쟁 구도 M/S, 경쟁사 3개 / 구조적 드라이버 2개
</thesis>
→ 한 문장 thesis 불가 시 이해 부족 탈락.

━━ STEP 3. 좌꼬리 게이트 (5분) ★최우선, 밸류 이전 ━━
봇: get_finance_rank + get_dart(재무제표 추출)

Hard Kill (5개, 면책 불가):
□ 감사의견 비적정 □ 자본잠식률 50% 2년/완전잠식 □ 한계기업(ICR<1 3년) □ 상폐 실질심사 □ 분식·횡령

선행 좌꼬리 (Altman-Eom-Kim 1995 한국 재추정 변수 — 부도 2년 전 식별):
□ Working capital/TA, Retained earnings/TA, EBIT/TA, Equity/Liab, Sales/TA 종합
□ ICR 추세 (1회성 <1 경고)
□ CFO 최근 4분기 부호
□ 잦은 CB·BW 발행 (연 2회+ 경고 가산 — 영구 희석)
□ 부채비율 vs 업종 (제조 150%↑ 경계, 건설 250%↑ 정상, 금융 BIS 별도)

<financials>F/G-Score / FCF Yield / 매출 3Y CAGR / OPM vs 업종 / 부채비율 / ICR / CB·BW 발행 이력</financials>

⛔ 성장 트랙 면책 삭제됨 (2026-06-01) — 적자 성장주도 Hard Kill·선행 좌꼬리 통과 필수.
   OECD 좀비 정의·Mohanram 원전 충돌. 적자 성장주 중 좀비 회피.

━━ STEP 4. 팩트체크·반증 (3분) ━━
봇 병렬: get_news(ticker="[티커]", sentiment=true) + get_dart(mode="insider", ticker="[티커]", days=30)
웹서치: "[종목명] 리스크" + "[종목명] 악재"

<factcheck>
  30일 공시 (유상증자·자사주·감자·소송·내부자)
  뉴스 감성
  유상증자: 주주배정(중립) / 일반공모·제3자(악재)
  자사주: 매입만(약) vs 소각(강한 양+)
  ※ 임원 거래: 30일 사전공시제(2024.7)라 매수 신호 불가(정보우위 소거). 매도 사전공시는 회피 신호.
</factcheck>

반증 2개 필수: ① thesis가 틀리려면? ② Bear Case (-30% 경로)

━━ STEP 5. 수급 확인 (3분) — 신호 차등 ━━
봇 병렬: get_supply(mode="history", days=20) + get_broker + get_market_signal(short_sale/credit/lending)

<supply>
  매수 카운트 가능:
    외인+기관 동시 5일+ 누적 순매수 + 가격 미반영 + 거래량 동반 (Park-Ok-Kim 2024)
  역신호 (매도 회피):
    개인 순매수 급증 (강한 역신호 — 개인 우세 종목 underperform)
    외인 공매도 급증 (Wang-Lee 2015, t+2 예측)
    대차잔고 급증 (실무 통념, 학술 근거 약함)
    신용잔고율 10%+ (과열 경고, 비중 50% 축소)
  카운트 불가:
    외인 단독 일간 순매수 (추세추종, Choe-Kho-Stulz 1999)
    임원 사전공시 매수
  증권사 매수 상위 3곳 외국계 포함 여부
</supply>

외인 5일 연속 순매도 + 대차잔고 증가 → 진입 금지

⛔━━ PDF 게이트 (STEP 6 진입 조건) ━━⛔
봇: read_report_pdf(ticker="[티커]") — 최소 2개 PDF 직접 읽기
□ PDF 2개+ 완료? □ TP 산출 방식? □ 브로커별 EPS 개별 수치(컨센 평균 금지, 교훈 #9)?
□ Forward FCF·부채비율 추정치?
⛔ 미완료 시 STEP 6 진입 금지. ※ 셀사이드 의견 level은 무시, EPS 추정치만 추출.

━━ STEP 6. 밸류에이션 (5분) ━━
봇 직렬: get_consensus(ticker="[티커]") + get_stock_detail(mode="volume_profile")

<valuation>
  리포트 TP 분해 (PDF): 멀티플 / 적용 배수 vs 피어 / 목표 EPS + 브로커 편차
  Forward PEG (참고): <1 저평가 / 1~1.5 정상 / >2 경고 (게이트 아님)
  컨센 gap: 현재가 vs 평균 TP, 30일 EPS revision 방향
    ※ 한국 매수 92.9%, 매도 0.1% (김준석 2025.8). 의견·TP는 2013 이후 알파 소멸. EPS revision만 유효.
    ※ 목표가 달성률 38% (Bradshaw 2013). 벤치마크로만.
  VP 매물대 3개
</valuation>
⚠️ 단순 고밸류(고PER)는 매도/회피 사유 아님 — Damodaran sanity check(Implied g ≥ 경제성장률)만 경고.

━━ STEP 7. RR·과거 비교 (2분) ━━
봇 직렬: get_alerts(brief=true) + get_backtest(ticker="[티커]", strategy="ma_cross")

<rr>
  진입가: __원 / 손절가: __원(-__%) / 목표가: __원(+__%) / RR = __:1
  최소 RR: 3통과 1:2+ / 2통과 1:2.5+ / 1통과 금지
  ※ 목표가 = 재산정 기준(매도 트리거 아님)
  기회비용: 현 포트 최고 확신 종목 대비?
</rr>

━━ STEP 8. 포트 시뮬 + 비중·카테고리 (2분) ━━
봇: simulate_trade(buys=[{ticker:"[티커]", qty:__, price:__}])

<position_sizing>
  3-Gate 통과: __/3 · 레짐 승수 🟢×1.0 / 🟡×0.8 / 🔴×0.5
  기본(🟡): 3통과 Core 15~25% / 2통과 Starter 3~5% / 1 이하 금지
  단일 하드캡 25%: 절대 한도(시총 증가 포함). 초과 시 비중관리 축소(경로3 재배치) — 가격 익절 아님
  클러스터(섹터) 35% 소프트: 의도적 매크로 베팅은 사전 기록 시 예외, 우발적 집중만 차단
  신규 항상 Starter부터 (Barber-Odean 2000)
  □ 단일 ≤25% 하드 □ 클러스터(섹터) 35% 소프트 □ 현금 최소선 (INVESTMENT_RULES §3 정합)
</position_sizing>

━━ STEP 9. 결정·기록 (2분) ★Kill Switch pre-commit 의무 ━━
봇: set_alert(buy_price, memo="thesis+Kill Switch") + set_alert(log_type="decision") + write_file("data/thesis/[티커]_[종목명].md")

<decision>
  3-Gate: ①__/②__/③__ (통과 __/3)
  비중: Starter/Standard/Core __% · 카테고리: 메인/가치/스윙
  감시가: __원 / 손절가: __원(스윙 -7~10%, Core는 thesis 정성) / 목표가 2yr: __원(재산정 기준)
  Thesis 한 문장: ____
  Kill Switch (정량, 매수=매도 묶음 — KR_EXIT 트리거):
    1차 raw 분기재무: 매출 YoY < __% OR OPM < __% OR CFO 음전환
    2차 EPS revision: 컨센 EPS 누적 하향 __%+ (셀사이드 매도의견 제외)
    3차 공통동인/매크로: AI capex 2분기 연속 하향 OR 원/달러 임계 돌파(수출주 영구손실 채널)
  Bear Case 2개: 1.____ 2.____
  Pre-mortem 1문장: "1년 후 실패했다면 가장 큰 이유는?"
  승격: Starter→Standard ≥5일+5%&20MA위+확증1+외인기관 3중2 / Standard→Core ≥10일+10%&60MA위+확증2+외인기관 10중6
    ※ Jegadeesh 1990: 매수 후 <3일 추가매수 금지
  다음 촉매: __
</decision>

XML 준수. Bear Case 2개 필수. 등급 문자 금지.
```

---

## 🚨 풀 DD 트리거

- 신규 매수 / Core 승격 검토 / 처음 분석 섹터 / thesis 붕괴 의심 / get_change_scan 시그널 / 라이트 체크 1개+ / 체제 게이트 발동 후 해제 시점

> ⚠️ **도구 레퍼런스 주석 (2026-06-04)**: 본 문서의 `get_sector(mode="flow")`·`get_change_scan`·`get_scan preset=` 등 표기는 현행 MCP 스키마와 불일치 가능(죽은/변경된 호출명). 실제 호출 전 스키마 확인 필요 — INVESTMENT_RULES §6 동일 과제.

---

## 🔗 관련 문서
- `INVESTMENT_RULES.md` · `US_DEEPSEARCH.md` · `KR_EXIT.md` · `bot_guide.md`

---

## 📚 학술·실무 근거

| 원칙 | 핵심 출처 |
|---|---|
| Thesis 먼저 (앵커링) | Tversky-Kahneman(1974) Science 185; Campbell-Sharpe(2009) JFQA 44(2) |
| 재무→밸류 | Piotroski(2000) JAR 38 |
| 가격≠매도 / 우꼬리 | Bessembinder(2018) JFE 129(3) |
| K/G 실시간 분해 불가 | Cochrane(2011) JoF 66(4) |
| 한국 매크로 spillover | Lastauskas-Nguyen(2024) IMF WP; Yang(2017) APJFS 46 (위기 per-trade 충격) |
| 인지편향 (K/G 누수) | Nisbett-Wilson(1977) Psych Review 84(3); Wilson-Brekke(1994) Psych Bulletin 116(1); Kunda(1990) Psych Bulletin 108(3) |
| F-Score | Piotroski(2000) ≥8, Walkshäusl(2020) J.Asset Mgmt 21 신흥국 재검증, Jeong-Kim(2019) 한국 |
| G-Score | Mohanram(2005) RAS 10 (보조, 산업 상대화; 한국 단독 출처 [확인 불가]) |
| 한국 부도예측 | Altman-Eom-Kim(1995) JIFMA 6(3); Nam-Jinn(2000) |
| Zombie 정의 | OECD McGowan-Andrews-Millot(2017) WP No.1372; Economic Policy 33(96); 한국은행 한계기업 보고서 (정확 발간물 보완 필요) |
| distress 신흥국 부재 | Eisdorfer-Goyal-Zhdanov(2018) Financial Management 47(3):553-581 |
| 한국 외인 (양면) | Choe-Kho-Stulz(1999) JFE 54(2) 추세추종·비-destabilizing; (2005) RFS 18(3) 정보우위 부분반증; Bae-Min-Jung(2011) APJFS 40(2) 장기 우위 |
| 외인 공매도 | Wang-Lee(2015) PBFJ 32 (t+2 예측) |
| 개인 역신호 | Park-Ok-Kim(2024) PBFJ (개인 알파 부재); 김민기·김준석(2022) KCMI 22-02 (회전율 1600%) |
| 한국 모멘텀 음수 | Chae-Eom(2009) APJFS 38; Chui-Titman-Wei(2010) JoF (collectivist 약함) |
| 한국 Value 작동 | Kang-Kang-Kim(2019) APJFS 48; Kim-Ok-Park(2020) Sustainability 12(4) |
| 임원 사전공시제 | 자본시장법 개정 2024.7.24 (30일 사전공시+cooling-off); 정준혁(2024) 금융법연구 21(2) |
| 한국 컨센 편향 | 김준석(2025.8.5) KCMI "Optimism Bias" 매수 92.9% 매도 0.1%; (2026.02) 의견·TP 2013후 알파소멸, EPS revision만 유효 |
| 목표가 달성률 | Bradshaw-Brown-Huang(2013) RAS 18(4) 38%; Bradshaw-Huang-Tan(2019) JAR 57 약한 인프라국 낙관편향 |
| 단기 Reversal | Jegadeesh(1990) JoF 45(3) (1개월, 장기 모멘텀 아님) |
| 개인 과매매 | Barber-Odean(2000) JoF 55(2) |
| Damodaran cap | Damodaran *Investment Valuation* Ch.12 (Implied g < 경제성장률) |
| 매수=매도 / disposition | Shefrin-Statman(1985) JoF 40; Odean(1998) JoF 53 |
| 자산배분 우선 | Brinson-Hood-Beebower(1986) FAJ 42(4) |
| 3-Gate 멀티팩터 | Asness-Moskowitz-Pedersen(2013) JoF 68(3) (일본·한국 모멘텀 약함 시사) |
| 공매도 재개 | FSC 2024.6.27 발표 / 2025.3.31 전면 재개 |

## ⚠️ 실무 관행 라벨 (학술 엄밀 근거 부재)
- 거래대금 50억/100억, Forward PEG 1.5~2(Lynch 원문 <1만), 신용잔고율 10%+, 대차잔고 단독 우월성, 부채비율 업종 기준(건설만 대한건설협회 공식)
- get_backtest, get_trade_stats로 보완 교정.

## ❌ v4→v5 삭제·전환
- **K/G 매수사유 (STEP 0)** → 체제 게이트(위기 매수동결)로 재배치
- **성장 트랙 면책** (G≥5+매출>20% 부채·FCF 면제) → 삭제 (OECD 좀비·Mohanram 충돌)
- **12M 가격 모멘텀 근거** → 삭제 (한국 음의 수익), Value로 대체
- **부분축소 50%** → 전량 이진 (KR_EXIT)
- (v3 기삭제) 확신등급 6단계, RR 공식, 섹터 상한 50%

## 🔧 v5 출처 정정 (검증 반영)
- "코스닥 4년 영업손실=관리/5년=상폐" → **2022.12 폐지** (현행 5년 연속 영업손실=투자주의 환기종목만). 매출 30억 기준 → 30→40→50/75→100억 단계 상향
- "Campbell-Sharpe 2007" → **2009** JFQA 44(2) (2007은 FEDS WP)
- "Lou & Sun 2024 arXiv" → **삭제** (LLM 논문, 금융 앵커링 아님), Campbell-Sharpe 2009로 대체
- "Nisbett-Wilson 1981" → **1977**, "Wilson-Brekke 1996" → **1994**
- "이병주-김동철 2017 한국재무학회" → **[확인 불가]** (검색 미발견), 인용 보류
- "한국은행 2025.10 zombie" → 정확 발간물·페이지 보완 필요 (일부 한은 보고서는 업력 조건 없이 ICR<1 3년만 사용)
- "김준석 2025.7" → **2025.8.5** KCMI

---

## 변경 이력

- **2026-06-01 v5**: 통합 반증검증 반영 (US 통합검증 + 한국 통합검증)
  - 미국 원리 옮김: 단순화 비대칭 / 가격≠매도 / Kill Switch pre-commit / 전량 이진
  - K/G 매수사유 삭제 → **체제 게이트(위기 매수동결)** 재배치 (Cochrane + Lastauskas-Nguyen + Yang)
  - **성장 트랙 면책 삭제** (OECD WP1372 좀비·Mohanram 원전 충돌)
  - 임원 거래 매수신호 불가 (30일 사전공시제)
  - 12M 모멘텀 카운트 불가 (Chae-Eom 음수), Value 우선
  - 개인 순매수 역신호 명시
  - Kill Switch = raw 분기재무 + EPS revision (셀사이드 매도의견 제외) + 원/달러 임계
  - 선행 좌꼬리 게이트 (Altman-Eom-Kim) + CB·BW 발행 경고
  - 수급 신호 차등 (외인+기관 동시 누적만 매수, 개인·공매도·대차 역신호)
  - 출처 7건 정정 (4년 영업손실 룰 폐지, Campbell-Sharpe 2009, Nisbett 1977, Wilson 1994, Lou&Sun 삭제, 이병주-김동철 확인불가, 김준석 2025.8)
- 2026-04-24 v4: 확신등급→비중 3단계, Hard Kill 5개, 성장 면책(→v5 삭제), 3-Gate 2/3
- 2026-04-17 v3: K/G STEP 0 (→v5 체제게이트 전환)
- 2026-04-15 v3: 재무→밸류 순서
