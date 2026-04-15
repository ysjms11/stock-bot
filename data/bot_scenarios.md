# Stock-Bot 활용 시나리오 가이드
> 업데이트: 2026-04-08

---

## 시나리오 1: "새로운 종목 찾아줘"

### 1-1. 실적 좋은데 주가 안 오른 종목
```
니: "영업이익 늘었는데 주가 덜 오른 종목 찾아줘"
나: get_change_scan(preset="earnings_disconnect", gap_min=30)
→ EPS +30% 이상인데 YTD 수익률 보합/하락 종목 리스트
→ 결과 중 시총 3000억+ 필터: get_scan(per_min=0.01, per_max=15, market_cap_min=3000)
→ 관심 종목 1~2개 → 7단계 딥서치 진행
```

### 1-2. 세력이 모으고 있는 종목
```
니: "외인이 꾸준히 사고 있는데 아직 안 오른 종목?"
나: get_change_scan(preset="foreign_accumulation") + get_change_scan(preset="vp_support")
→ 외인 보유비율 5일 +1%p 증가 + VA 하단 근접 종목
→ "조용히 매집 중인데 아직 가격은 저점 근처"
→ 컨센서스 확인: get_consensus(ticker)
→ 공매도 추이 확인: get_market_signal(mode=short_sale, ticker)
```

### 1-3. 곧 터질 것 같은 종목
```
니: "이평선 모이고 거래량 늘어나는 종목?"
나: get_change_scan(preset="ma_convergence", spread_max=3)
→ MA5/20/60 수렴 + 30일간 수렴 가속
→ 결과에서 volume_spike 교차:
   get_change_scan(preset="volume_spike", ratio_min=2.0)
→ 이평선 수렴 + 거래량 폭발 = "방향 결정 임박"
→ 외인 방향 확인: get_supply(mode=history, ticker, days=10)
```

### 1-4. 저평가 가치주 발굴
```
니: "PER 낮고 배당 높은 안전한 종목?"
나: get_scan(preset="value") — PER<10, PBR<1, 시총>1000억
→ 결과에서 consensus_gap 높은 것 교차:
   get_change_scan(preset="consensus_undervalued", gap_min=40)
→ "밸류에이션 싼데 증권사 목표가까지 40% 이상 남은 종목"
→ 재무 확인: get_finance_rank(mode="roe", n=30)
```

### 1-5. 숏커버 + 반등 종목
```
니: "공매도 줄고 있는 종목 중 과매도인 거?"
나: get_change_scan(preset="short_squeeze", short_change_min=-30)
→ 공매도 10일 -30% 이상 감소
→ 결과에서 oversold_bounce 교차:
   get_change_scan(preset="oversold_bounce", rsi_max=35)
→ "숏커버 진행 중 + RSI 과매도 → 반등 임박"
```

---

## 시나리오 2: "이 종목 사도 돼?"

### 2-1. 빠른 체크 (1분)
```
니: "삼성전자 지금 어때?"
나: get_stock_detail(ticker="005930") — 현재가/PER/수급
→ "193,100원, PER 29.4, 외인+119만 기관+167만 동시 매수"
→ get_alerts(brief=true) — 니 감시가/손절 확인
→ "감시가 175K 대비 +10%. RR 안 나와서 대기."
```

### 2-2. 딥체크 (10분, 7단계)
```
니: "에스티팜 진입할까?"
나:
1. get_stock_detail(ticker="237690") — 현재가 137,000원
2. get_consensus(ticker="237690") — 10곳 매수 목표 189K
3. manage_report(action=collect, ticker="237690") → list — 리포트 읽기
4. get_supply(mode=history, ticker="237690", days=10) — 외인 10일 추세
5. get_market_signal(mode=short_sale, ticker="237690") — 공매도 6.5%
6. get_stock_detail(mode=volume_profile, ticker="237690") — VA 120~145K
7. get_dart(mode=report, ticker="237690") — 사업보고서 확인
→ 종합 판단 + 킬질문 9개 + simulate_trade
```

### 2-3. 매수 전 최종 확인
```
니: "NVDA $171에 12주 사려고. 시뮬레이션 해줘"
나: simulate_trade(buys=[{ticker:"NVDA", qty:12, price:171}])
→ 포트 비중 변화: 현금 $7,696→$5,644, NVDA 비중 0→X%
→ 섹터 집중도 확인
→ RR비 확인: (목표$270-$171) / ($171-손절$140) = 3.2 ✅
→ 킬질문 9개 자동 답변
→ set_alert(log_type="trade", side="buy", ticker="NVDA", qty=12, price=171)
```

---

## 시나리오 3: "포트 상태 점검"

### 3-1. 일일 점검 (1분)
```
나: get_regime → 🔴 위기 25.6점
   get_alerts(brief=true) → triggered 없음
→ "변화 없음. 할 일 없음." 끝.
```

### 3-2. 주간 Sunday 30 (30분)
```
나:
1. get_regime → 레짐 변화? (🔴→🟡 전환?)
2. get_alerts → 손절 근접? triggered?
3. get_supply(mode=combined_rank) → 스마트머니 어디로?
4. get_macro(mode=op_turnaround) → 적자→흑자 전환 종목
5. get_change_scan(preset="foreign_reversal") → 외인 전환 종목
6. get_change_scan(preset="earnings_disconnect") → 실적 괴리 종목
7. 관심 1종목 딥체크
8. set_alert(log_type="decision") → 판단 기록
```

### 3-3. 제로베이스 테스트 (월간)
```
니: "지금 현금 100%면 현재 포트를 다시 만들겠어?"
나:
1. get_portfolio → 현재 보유 전부 확인
2. 종목별 get_stock_detail + get_consensus → thesis 재점검
3. get_change_scan(preset="consensus_undervalued") → 더 좋은 기회 있나?
4. simulate_trade(sells=[전부매도]) → 현금 100% 시뮬
5. simulate_trade(buys=[다시구성]) → 최적 포트 비교
→ "HD조선 51.8%는 다시 안 만든다. NVDA 12주+LITE 4주가 낫다."
```

---

## 시나리오 4: "시장이 급변했어"

### 4-1. 급락장 대응
```
니: "코스피 -5% 났어. 어떡해?"
나:
1. get_regime → 🔴 확인 (쇼크 오버라이드?)
2. get_alerts → CRSP -29.6% 강제재평가! 
3. get_portfolio_history(days=30) → 드로다운 -10% 넘었나?
4. get_change_scan(preset="oversold_bounce") → 과매도 반등 후보
5. get_scan(preset="oversold") → 낙폭과대 종목 중 펀더멘털 양호한 것
→ "드로다운 -12%. C/D등급 전수 점검. 신규 1종목/주 제한."
```

### 4-2. 전쟁/이벤트 발생
```
니: "이란 전쟁 확전됐어"
나:
1. get_regime(mode=override, regime="crisis", reason="이란 확전") → 즉시 🔴
2. get_macro(mode=dashboard) → WTI/VIX/환율 확인
3. get_alerts → 전 보유종목 손절 거리 확인
4. get_sector(mode=flow) → 방산/에너지 유입? 반도체 유출?
→ "🔴 강제. 신규매수 동결. 현금 확보 우선."
```

### 4-3. 어닝 서프라이즈 대응
```
니: "삼성전자 57조 어닝서프라이즈!"
나:
1. get_stock_detail(ticker="005930") → 210,000원 +8.8%
2. get_consensus(ticker="005930") → 목표가 상향 중?
3. get_supply(mode=daily, ticker="005930") → 외인+기관 동시 매수?
4. get_sector(mode=flow) → 반도체 섹터 전체 유입?
5. get_change_scan(preset="sector_leader", market="kospi") → 반도체 내 최강 종목
→ "삼전 보유 18주 유지. 셀온더뉴스 가능성 → 추매 대기."
```

---

## 시나리오 5: "섹터 분석"

### 5-1. 어떤 섹터가 돌고 있나?
```
니: "요즘 돈이 어디로 가고 있어?"
나:
1. get_sector(mode=flow) → 업종별 외인+기관 순매수
2. get_sector(mode=rotation) → 전일 대비 자금 이동
3. get_macro(mode=sector_etf) → 섹터 ETF 등락률
→ "반도체 +7.69%, 조선 +3.71% 유입. 바이오 유출."
→ get_change_scan(preset="sector_leader") → 각 섹터 1등 종목
```

### 5-2. 특정 섹터 딥다이브
```
니: "반도체 섹터에서 가장 좋은 종목은?"
나:
1. get_scan(market_cap_min=3000, sector="반도체") → 반도체 시총 3000억+
2. get_change_scan(preset="foreign_accumulation") → 외인 집중 매수
3. get_change_scan(preset="earnings_disconnect") → 실적 대비 저평가
4. get_finance_rank(mode="roe") → ROE 높은 순
→ "SK하이닉스: 외인 매수 + ROE 상위. 삼성전자: 실적 서프라이즈."
```

---

## 시나리오 6: "복합 스캔 조합"

### 6-1. 완벽한 매수 후보 (다중 필터)
```
니: "모든 조건이 다 좋은 종목 있어?"
나: 순차 필터링
1. get_change_scan(preset="earnings_disconnect") → 실적 괴리 30종목
2. 그 중 get_change_scan(preset="foreign_reversal") 교차 → 10종목
3. 그 중 get_change_scan(preset="vp_support") 교차 → 3종목
4. 그 중 get_change_scan(preset="short_squeeze") 교차 → 1종목!
→ "실적 +40%인데 주가 안 오르고, 외인 전환, VA 하단, 숏커버 중 = 매수 1순위"
→ 7단계 딥서치 진입
```

### 6-2. 위험 종목 걸러내기
```
니: "내 워치리스트 중 위험한 거 알려줘"
나:
1. get_alerts → 44개 워치 목록
2. 각 종목 krx_db에서: credit_change_5d > +20% (신용 급증 = 과열)
3. short_ratio > 10% (공매도 과열)
4. rsi14 > 70 (과매수)
5. vp_position > 1.0 (VA 돌파 = 차익실현 구간)
→ "파마리서치: 공매도 29% + VA 이탈 = 위험. 워치에서 제거 검토."
```

---

## 시나리오 7: "매도 타이밍"

### 7-1. 언제 팔아야 해?
```
니: "HD조선 51.8%인데 줄여야 할까?"
나:
1. get_stock_detail(ticker="329180") → 현재가/수급
2. get_supply(mode=history, ticker="329180", days=10) → 외인 3일 연속 매도?
3. get_market_signal(mode=short_sale, ticker="329180") → 공매도 추이
4. get_consensus(ticker="329180") → 목표가 도달?
5. get_stock_detail(mode=volume_profile, ticker="329180") → VA 상단 돌파?
→ "외인 아직 매수 중 + 목표가 미도달 → B등급 유지하되 35% 한도 초과분만 트림"
→ simulate_trade(sells=[{ticker:"329180", qty:10}]) → 비중 변화 확인
```

### 7-2. 모멘텀 종료 체크
```
니: "HD일렉트릭 모멘텀 끝난 거 아냐?"
나:
1. get_alerts → 모멘텀 경고 확인 (5가지 조건 중 2개+?)
2. get_supply(mode=history, ticker="267260") → 외인/기관 3일 매도?
3. get_change_scan(preset="short_squeeze", ticker 필터) → 공매도 감소? 증가?
4. get_stock_detail → 거래량 급감?
→ "2/5 조건 해당 (거래량 감소 + 52주고 -20%). 아직 모멘텀 종료 아님. B유지."
```

---

## 도구 조합 빠른 참조

| 니가 원하는 것 | 1차 도구 | 2차 도구 | 3차 도구 |
|---|---|---|---|
| 새 종목 발굴 | get_change_scan | get_scan (교차) | 딥서치 7단계 |
| 매수 판단 | get_stock_detail | get_consensus + VP | simulate_trade |
| 포트 점검 | get_regime + get_alerts | get_portfolio_history | 제로베이스 테스트 |
| 급변 대응 | get_regime | get_macro(dashboard) | get_sector(flow) |
| 섹터 분석 | get_sector | get_change_scan | get_finance_rank |
| 매도 판단 | get_supply(history) | get_market_signal | simulate_trade |
| 위험 점검 | get_change_scan | get_market_signal(credit) | get_alerts |
