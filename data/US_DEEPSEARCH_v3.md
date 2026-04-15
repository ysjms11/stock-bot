# 미국 종목 7단계 딥서치 프롬프트 템플릿 v3

> 학술·실무 검증 반영 최종본 (2026.04.15)
> [티커], [현재가], [등급] 등 대괄호만 바꿔서 복붙

---

## ① 새 종목 풀 딥서치 (~45분)

```
[티커] 미국 종목 7단계 딥서치. 현재가 약 $[현재가].

━━ STEP 1. 아이디어 소싱 근거 (2분) ━━
소싱 채널: 13F Best Ideas / 내부자 클러스터(10일 3명+) / 어닝 모멘텀 / 섹터 리더 / 스핀오프 / 자사주매입
2개+ 채널 중복 시 진행.

━━ STEP 2. 3대 핵심 질문 속판 (5분) ━━
웹서치: "[티커] earnings guidance" + stockanalysis.com/stocks/[티커]/forecast/
① 해자: GM 업종 상위사분위? ROIC > WACC?
② 구조적 수요: 매출성장 > 산업? NRR 110%+? 가격결정력?
③ 컨센 이익 상향: NTM EPS 3개월 변화 양수? 가이던스 방향에 2배 가중
→ 2/3+ Yes 진행, 1개 이하 종료

━━ STEP 3. 재무건전성 (7분) ★밸류에이션보다 먼저 ━━
웹서치: stockanalysis.com 재무제표 + "[티커] Altman Z-Score" + "[티커] Beneish M-Score"
□ Altman Z'' > 2.6  □ Beneish M < -1.78  □ D/E 산업 이하
□ ICR 3x+  □ FCF 4분기 중 3분기+ 양수  □ SBC 15% 이하 + 희석률 3% 미만
□ Current Ratio 1.5x+ (SaaS: 이연수익 제외)  □ 감사의견 적정
킬: Z < 1.1 or M > -1.78 or FCF 적자 지속 → 탈락

━━ STEP 4. 밸류에이션 삼각측량 (12분) ━━
웹서치: stockanalysis.com + "[티커] EV revenue EBITDA peers" + "[티커] FCF margin"
■ PEG 게이트: FwdPEG > 2.0 → 매수 보류

업종별 분기:
- SaaS: EV/NTM Rev(동종비교) + Rule of 40 + Reverse DCF
- 반도체: EV/NTM Rev + Through-Cycle P/E + Reverse DCF
- 바이오: Pipeline rNPV (상업화 전) / EV/NTM Rev (상업화)
- 방산/산업재: EV/EBITDA + 수주잔고 + FCF Yield
- 금융: P/E + P/TBV + ROE

Reverse DCF: 내재성장률 > 컨센 × 1.3 → 고평가 경고

■ 2년후 적정가 (A등급 + 이익가시성 2년+ 시 필수):
  웹서치: "[티커] EPS forecast 2028 consensus" (wallstreetzen.com)
  2028E EPS: $__ × 섹터멀티플 __x = 2년후 적정가 $__
  현재가 대비: __% [할인/프리미엄]
  → 20%+ 할인이면 현재가 1차 트랜치 진입 가능 (감시가 대기 불요)
  → 20% 미만이면 기존 감시가 체계 적용
  ※ 프리매출/적자 기업은 적용 불가 → 기존 감시가 체계
  ※ 검증근거: AVGO/MRVL/META 등록일 백테스트(2026.04 샘플5개, 적중3/3)
  ※ 샘플 부족. 향후 3개월 실전 추적으로 지속 검증 필요.

━━ STEP 5. 수급·내부자·기관 (5분) ━━
웹서치: "[티커] insider trading site:openinsider.com" + "[티커] short interest" + "[티커] unusual options activity"
내부자 필터: 코드 "P" only, $100K+, CEO/CFO/이사, 루틴 시점 제외
□ 기회주의적 매수  □ 클러스터(★★★★★)  □ 기관 신규진입
□ Short Float 5%+ 경고  □ UOA(비정상 옵션) 콜/풋 편향  □ 13D
긍정 2개+ → 확신+1, 내부자 매도 클러스터 → 확신-1

━━ STEP 6. 기술적 타이밍 (3분) ★5개→3개 ━━
봇: get_stock_detail(period=D250) + get_stock_detail(mode=volume_profile) [선택적]
필수 3개만:
□ EMA 정배열 (20>50>200)  □ 52주 신고가 거리 (5% 이내=돌파후보)
□ 어닝 갭 유지
VP는 선택적 참고. RSI 삭제(EMA와 중복).

━━ STEP 7. 킬질문 10개 + 등급 (8분) ★9→10개 ━━
머스트패스(실패 시 자동 하향):
  Q1.★ 50% 하락 시나리오? (프리모템, 조분류: unlikely/possible/likely)
  Q3.★ 최대 손실? (하방 계량화)
  Q7.★ 한 문장으로 설명 가능? (사업 이해)

나머지: Q2.시장이 아는 정보? Q4.6개월 유효? Q5.기회비용? Q6.최고보유종목 비교?
Q8.SBC 과다? 이익 본업?(스미스) Q9.과거 프레임?(막스) Q10.★★매도 조건은?

등급: A(10/10+머스트3통과+매력적+수급3+) / B+(8~9/10+머스트3통과)
      B(7/10) / B-(머스트1미달) / C(5~6) / D(머스트2+미달→종료)
감시가: A=RR1:2, B+=RR1:2.5, B=RR1:3, C=매수금지

출력 템플릿 사용. set_alert 등록. write_file로 thesis 저장.
반론 Bear Case 2개 필수.
```

---

## ② 빠른 등급 판정 (10분)

```
[티커] 빠른 등급 판정. 현재가 약 $[현재가].
봇: get_stock_detail + get_alerts(brief=True)
웹서치: "[티커] earnings guidance 2026" + stockanalysis.com/stocks/[티커]/forecast/
3대 질문 → PEG 게이트(2.0) → 등급 → 감시가 → set_alert.
```

---

## ③ 실적 프리뷰 (어닝 전)

```
[티커] 실적 프리뷰. 발표일 [날짜]. 등급 [등급].
봇: get_stock_detail + VP(선택적)
웹서치: "[티커] Q[분기] earnings estimate" + whisper + preview + 내부자
분석: 가이던스 vs 컨센 위치, 관전 KPI 3개, Beat/Miss 시나리오, 포지션 콜.
```

---

## ④ 분기 재검증 (규칙 4)

```
[티커] 분기 재검증. 보유 [수량]주, 등급 [등급].
봇: get_stock_detail + get_portfolio + get_alerts(brief=True)
웹서치: "[티커] earnings guidance" + stockanalysis.com + "[티커] competition risk"
5개 질문: 1.thesis 유효? 2.등급 변경? 3.무효화 조건? 4.Fresh Money Test? 5.프리모템(조분류)
```

---

## ⑤ 종목 비교

```
[티커A] vs [티커B] 비교. 달러 현금 $[금액].
봇: get_stock_detail 양쪽 + get_portfolio
웹서치: stockanalysis.com 양쪽 + 내부자
각각: 3대 질문 + FwdPE/PEG(2.0) + 업종별 밸류에이션 + 내부자 + EMA/52주고가
비교표 + 콜. simulate_trade.
```

---

## ⑥ 워치 일괄 스캔 (월 1회)

```
미국 워치리스트 일괄 스캔.
봇: get_alerts(brief=True) + get_regime + get_macro(mode='us_sector')
감시가 10% 이내만: 웹서치 "[티커] earnings guidance" → 3대 질문 속판 → 등급/감시가 재조정.
PEG 게이트 2.0 재확인. 변화 없으면 스킵.
```

---

## ⑦ 아이디어 소싱 (주간/월간)

```
미국 신규 종목 소싱.
봇: get_macro(mode='us_sector') + get_rank(type='us_price', sort='rise', n=20)
웹서치 (주기별):
[일간] 내부자 클러스터(연9.8~21.6%) + 어닝 모멘텀(PEAD)
[주간] 섹터 리더 + 스핀오프(3년33.6%) + 자사주매입(4년12.1%)
[분기] 13F Best Ideas(연2.8~4.5%) + SA Quant(보조)
후보 3~5개 → 빠른 판정(②) → B+이상이면 풀 딥서치(①)
```

---

## 딥서치 출력 템플릿

```
═══════════════════════════════════════════
[종목명] ([티커]) — 딥서치 완료일: YYYY-MM-DD
═══════════════════════════════════════════

■ 확신등급: [ A / B+ / B / B- / C / D ]

■ 투자 테제:
  "[종목]은 [구조적 수요]에서 [해자]로 향후 N년간 연 __% 이익 성장 가능"

■ 3대 핵심 질문:
  ① 해자:       [Strong Yes/Yes/Weak/No] — GM 업종 __사분위, ROIC vs WACC
  ② 구조적 수요: [Strong Yes/Yes/Weak/No] — 매출성장 vs 산업, NRR __%, 가격결정력
  ③ 이익 상향:   [Strong Yes/Yes/Weak/No] — NTM EPS 3개월 __%, 가이던스 ↑/→/↓

■ 재무건전성:
  Z'': __ (>2.6) | M-Score: __ (<-1.78) | D/E: __ | ICR: __x | FCF: __ | SBC: __%

■ 밸류에이션 ([업종]):
  FwdPE: __ (동종 __) | PEG: __ | PEG게이트(2.0): Pass/Fail
  [SaaS] EV/NTM Rev: __x | Rule of 40: __
  [반도체] TC-P/E: __
  Reverse DCF 내재성장: __% (컨센 __%)\n  판단: [매력적/합리적/부담/과대평가]

■ 2년후 적정가 (A등급+이익가시성2년+시 필수):
  2028E EPS $__ × 섹터멀티플 __x = $__
  현재가 대비 __% → 진입가능: Y/N (20%+할인=Y)

■ 수급:
  내부자 매수: ○/× ($__K) | 클러스터: ○/× | 기관 신규진입: ○/×
  Short Float: __% | UOA: ○/× | 13D: ○/×

■ 기술적 (3개):
  EMA 정배열: ○/× | 52주고가 거리: __% | 어닝갭 유지: ○/×

■ 킬질문: __/10 (머스트패스 Q1/Q3/Q7: __/3)

■ 리스크: 1.___ 2.___ 3.___
■ Thesis 무효화: 1.___ 2.___
■ 매도 조건: 1.___ 2.___
■ 감시가: $__ (RR 1:__, 목표 $__, 손절 $__)
■ 다음 촉매: ___ (날짜)
■ 반론: 1.___ 2.___
═══════════════════════════════════════════
```

---

## v2→v3 핵심 변경 (학술 검증 기반)

| 변경 | 이전 | 이후 | 근거 |
|------|------|------|------|
| 순서 | 밸류→재무 | **재무→밸류** | 부실기업에 DCF 불필요 (Piotroski) |
| GM 기준 | 40% 고정 | **업종 상위사분위** | SaaS 70%+ vs 제조 25%+ |
| TAM | 10%+ CAGR | **매출성장>산업+NRR+가격결정력** | TAM→개별수익 학술근거 없음 |
| PEG 게이트 | 3.0 | **2.0** | Lynch 원래 기준 |
| Rule of 40 | 전업종 | **SaaS 전용** | 반도체/바이오 부적합 |
| Short Float | 10% | **5%** | Desai et al. 2002 |
| 옵션 | 없음 | **UOA 추가** | Pan & Poteshman 2006 |
| 기술적 | 5개 | **3개** | RSI↔EMA 중복, VP 학술약 |
| 킬질문 | 9개 | **10개(+매도조건)** | 처분효과 방지 핵심 |
| 킬 구조 | 균등 | **머스트패스 3개 게이트** | Piotroski F-Score 구조 |
| 소싱 | 5채널 | **7채널(+스핀오프,자사주)** | 각각 33.6%, 12.1% 초과 |
| 적정가 | 올해FwdPE만 | **2년후EPS×섹터멀티플** | 워치50개 백테스트: 감시가 2%적중, 2yr FV 100%적중(3/3) |
