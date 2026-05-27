# 감시가 / 손절가 / TP 등록 plan — 2026-05-23

> **Ralph 무한 모드 iter 60 산출물**
> 사용자 5/26 (월) 09:00 즉시 실행용
> 텔레그램 봇 (`set_alert` MCP 도구) 또는 KIS 앱에서 일괄 등록
> 환율: 1 USD = 1,380 KRW (5/22 기준)
> 출처: iter 39 portfolio_rebalance_plan + iter 51/52 페어 + Top 18 thesis + ETF 6 + EXIT 2

---

## 사용법 (텔레그램 봇 명령 형식)

세 가지 알림 종류:

| 종류 | 명령 형식 | 설명 |
|------|----------|------|
| **매수감시** | `/watchalert 종목코드 가격 메모` | 지정가 도달 시 알림 |
| **손절가** | `/stoploss 종목코드 가격` | 손절선 이탈 시 알림 |
| **목표가** | `/target 종목코드 가격` | TP 도달 시 알림 |

MCP 도구: `set_alert(mode='watch'|'stop'|'target', ticker, price, memo)`

---

## A. 신규 매수 (즉시 실행 5/26 09:00)

### 1. 047810 한국항공우주 (KAI) — A등급, 알파섹터 #1 방산
- 1차 진입: 170,000원 (30% 비중, 5/22 종가 168,400 도달 임박)
- 2차 진입: 160,000원 (40%, -5.5% 조정 시)
- 3차 진입: 150,000원 (30%, -11.7% 큰 조정)
- 손절: 148,000원 (-13% from 1차)
- 12M TP: 220,000원 (컨센 평균, +29.4%)
- 24M TP: 250,000원 (최고가)
```
/watchalert 047810 170000 KAI 1차 30% 방산 A등급
/watchalert 047810 160000 KAI 2차 40% 조정진입
/watchalert 047810 150000 KAI 3차 30% 큰조정
/stoploss 047810 148000
/target 047810 220000
```

### 2. 139130 IM금융지주 — A등급, NPS +6.6pp, 자사주+감액배당
- 1차 진입: 18,920원 (30% 비중)
- 2차 진입: 17,500원 (50%, MA60 지지)
- 3차 진입: 16,000원 (20%, 큰 조정)
- 손절: 15,500원 (-18%)
- 12M TP: 24,500원 (+29.5%)
```
/watchalert 139130 18920 IM금융 1차 30% NPS+자사주
/watchalert 139130 17500 IM금융 2차 50% MA60지지
/watchalert 139130 16000 IM금융 3차 20% 큰조정
/stoploss 139130 15500
/target 139130 24500
```

### 3. 003690 코리안리 — A워치, 재보험 매크로 robust
- 1차 진입: 13,800원 (50%, 5/22 종가 14,520에서 -5% 대기)
- 2차 진입: 13,200원 (50%, MA120 근접)
- 손절: 12,200원 (-11.6%, MA120 하향 + 외인+기관 5d 순매도 동시)
- 12M TP: 16,500원 (+19.6%, 보수 PBR 0.85 리레이팅)
- Bull TP: 17,000원 (+23.2%)
```
/watchalert 003690 13800 코리안리 1차 50% 재보험
/watchalert 003690 13200 코리안리 2차 50% MA120
/stoploss 003690 12200
/target 003690 16500
```

### 4. 161390 한국타이어 — A워치, 1Q26 OP +43% 비트 + 한온 OP +361%
- 1차 진입: 56,000원 (50%, 지지대 매물대 하단)
- 2차 진입: 60,000원 (50%, 시나리오 A 트리거 시 56K 미도달 정상 진입)
- 손절: 50,000원 (-10.7%)
- 12M TP (보수): 80,000원 (+25.8%)
- 12M TP (컨센): 85,067원 (+33.7%)
- Bull TP: 93,000원 (LS증권 최고가, +46.2%)
```
/watchalert 161390 56000 한국타이어 1차 매물대하단
/watchalert 161390 60000 한국타이어 2차 시나A 트리거
/stoploss 161390 50000
/target 161390 85000
```

### 5. 402340 SK스퀘어 — 감시가 분할 진입 (조정 진입)
- 1차 진입: 1,050,000원 (40%)
- 2차 진입: 950,000원 (60%)
- 손절: 880,000원
- 12M TP: 1,400,000원
```
/watchalert 402340 1050000 SK스퀘어 1차 40%
/watchalert 402340 950000 SK스퀘어 2차 60%
/stoploss 402340 880000
/target 402340 1400000
```

### 6. 403870 HPSP — A등급, 반도체장비 알파 #1, 하반기 V회복
- 1차 진입: 54,700원 (30%, 5/22 종가, 즉시)
- 2차 진입: 49,000원 (40%, 추가 조정)
- 3차 진입: 44,000원 (30%, MA200 근접)
- 손절: 40,000원 (-27%, 4/13 저점 41,400 하향이탈)
- 1차 TP: 65,000원 (50%, +18.8%, 컨센 평균)
- 2차 TP: 85,000원 (잔여, LS 최고가, +55%)
```
/watchalert 403870 54700 HPSP 1차 30%
/watchalert 403870 49000 HPSP 2차 40% MA60
/watchalert 403870 44000 HPSP 3차 30% MA200
/stoploss 403870 40000
/target 403870 65000
```

---

## B. 보유 종목 손절/TP 갱신 (iter 39/51/52 반영)

### 1. 000660 SK하이닉스 — TRAIL 상향 (iter 51 D3 + iter 52 Z=+2.19σ 정점)
- 평단 851,000원, 현재가 1,941,000원, +128.08%
- **트레일 손절**: 1,400,000원 (이전 800,000 → 상향, 평단 +64% 락인)
- 1차 익절: 25% 분할 (1주 @ 시장가, iter 52 페어 권고)
- 12M TP: 2,500,000원 (HBM Rubin 70% 점유 + supercycle, 유지)
```
/stoploss 000660 1400000
/target 000660 2500000
```

### 2. 298040 효성중공업 — 평단 +14.3% 락인 강화
- 평단 2,800,000원, 현재가 4,000,000원, +42.86%
- 트레일 손절: 3,200,000원 (이전 2,500,000 → 상향, 평단 +14.3% 락인)
- 12M TP: 4,623,000원 (유지)
```
/stoploss 298040 3200000
/target 298040 4623000
```

### 3. 010120 LS ELECTRIC — 평단 락인 (트레일 유지)
- 평단 126,600원, 현재가 280,500원, +121.56%
- 트레일 손절: 239,275원 (현재 유지, 락인 +89%)
- 12M TP: 350,000원 (신규 설정)
```
/stoploss 010120 239275
/target 010120 350000
```

### 4. 267260 HD현대일렉트릭 — Trail 상향
- 평단 1,069,666원, 현재가 1,171,000원, +9.47%
- 트레일 손절: 900,000원 (이전 800,000 → 상향, 평단 -15.9% 보호)
- 12M TP: 1,500,000원 (컨센 평균 1,468K → 라운드 상향)
```
/stoploss 267260 900000
/target 267260 1500000
```

### 5. 005930 삼성전자 — 25% 분할 익절 (iter 51 D2 시그널)
- 평단 178,100원, 현재가 293,000원, +64.51%
- 트레일 손절: 220,000원 (이전 150,000 → 상향, 평단 +23.5% 락인)
- 1차 익절: 25% 분할 (4-5주, iter 51 D2)
- 12M TP: 370,000원 (유지)
```
/stoploss 005930 220000
/target 005930 370000
```

### 6. 009540 HD한국조선해양 — 비중축소 (iter 39, 50주→34주)
- 평단 413,590원, 현재가 419,000원, +1.31%
- 트레일 손절: 350,000원 (유지)
- 12M TP: 550,000원 (신규)
```
/stoploss 009540 350000
/target 009540 550000
```

### 7. NVDA — Trail 상향
- 평단 $188.30, 현재가 $220, +16.85%
- 트레일 손절: $170 (이전 $140 → 상향, 평단 -9.7% 락인)
- 12M TP: $330 (유지)
```
/stoploss NVDA 170
/target NVDA 330
```

### 8. AMZN — 비중축소 (iter 39, 37주→26주)
- 평단 $262.58, 현재가 $269.10, +2.48%
- 트레일 손절: $235 (이전 $223 → 평단 -10.5% 락인)
- 12M TP: $310 (이전 $303 약간 상향)
```
/stoploss AMZN 235
/target AMZN 310
```

### 9. AVGO — 트레일 상향
- 평단 $400.34, 현재가 $428.50, +7.07%
- 트레일 손절: $360 (이전 $340 → 상향)
- 12M TP: $510 (유지)
```
/stoploss AVGO 360
/target AVGO 510
```

### 10. 021240 코웨이 — Trail 상향
- 평단 84,485원, 현재가 93,300원, +10.43%
- 트레일 손절: 78,000원 (평단 -7.7% 락인)
- 12M TP: 110,000원
```
/stoploss 021240 78000
/target 021240 110000
```

---

## C. 감시가 대기 (조정 진입, 이미 thesis 등록된 종목)

### 1. 064290 인텍플러스 — A워치
```
/watchalert 064290 18000 인텍플러스 1차 30%
/watchalert 064290 16500 인텍플러스 2차 50%
/stoploss 064290 14500
/target 064290 26000
```

### 2. 218410 RFHIC — A워치
```
/watchalert 218410 26500 RFHIC 1차 표준진입
/watchalert 218410 24000 RFHIC 2차 안전진입
/stoploss 218410 22000
/target 218410 35000
```

### 3. 251270 넷마블 — A워치, FCC catalyst D-day
```
/watchalert 251270 92000 넷마블 1차
/stoploss 251270 82000
/target 251270 127000
```

### 4. 257720 실리콘투 — A워치
```
/watchalert 257720 31000 실리콘투 1차
/stoploss 257720 27500
/target 257720 41000
```

### 5. 204320 HL만도 — A워치
```
/watchalert 204320 39000 HL만도 1차 52w저점 근접
/stoploss 204320 36000
/target 204320 52000
```

### 6. 278470 에이피알 — A워치 유지 (5/22 v4 보류)
```
/watchalert 278470 90000 에이피알 1차 보류
/stoploss 278470 80000
/target 278470 130000
```

### 7. 139480 이마트 — 신규
```
/watchalert 139480 78000 이마트 1차
/stoploss 139480 70000
/target 139480 105000
```

### 8. SARO StandardAero — A페어 (KODEX방산 페어)
```
/watchalert SARO 38 SARO 1차 30%
/watchalert SARO 35 SARO 2차 50%
/stoploss SARO 31
/target SARO 52
```

---

## D. ETF 진입

### 1. 449450 KODEX K-방산 — 알파섹터 #1, 즉시 진입
- 1차 진입: 24,000원 (50%, 5/22 follow-through)
- 2차 진입: 22,000원 (30%, -8% 조정)
- 3차 진입: 20,000원 (20%, 큰 조정)
- 손절: 18,500원
- 12M TP: 32,000원
```
/watchalert 449450 24000 KODEX방산 1차 50%
/watchalert 449450 22000 KODEX방산 2차 30%
/watchalert 449450 20000 KODEX방산 3차 20%
/stoploss 449450 18500
/target 449450 32000
```

### 2. 140700 KODEX 보험 — A워치, 매크로 robust
```
/watchalert 140700 18895 KODEX보험 1차 50%
/watchalert 140700 17500 KODEX보험 2차 50%
/stoploss 140700 16200
/target 140700 23000
```

### 3. 487240 KODEX AI전력핵심설비 — B워치, 분할 진입
```
/watchalert 487240 13900 KODEX AI전력 1차 33%
/watchalert 487240 13000 KODEX AI전력 2차 33%
/watchalert 487240 11500 KODEX AI전력 3차 34%
/stoploss 487240 10500
/target 487240 18000
```

### 4. 133690 TIGER 미국나스닥100 — A등급 ETF
```
/watchalert 133690 95000 TIGER나스닥100 1차
/stoploss 133690 87000
/target 133690 110000
```

### 5. GRID FirstTrust SmartGrid — US ETF 페어
```
/watchalert GRID 145 GRID 1차 페어
/stoploss GRID 128
/target GRID 180
```

### 6. ITA iShares Defense — US ETF, KODEX방산 페어
```
/watchalert ITA 168 ITA 1차 페어
/stoploss ITA 152
/target ITA 200
```

---

## E. 헷지 (Conditional — 트리거 발효 후 진입)

### 1. 252670 KODEX 인버스 2X — Bear 트리거 발효 후
- 트리거: VIX>25 5d+ AND KOSPI -7% 동시 (2개+ 동시 발효 시)
- 1차 진입: 시장가 (조건부)
- 손절: -3% portfolio drag
- 비중 한도: 5% (헷지 전용)
```
/watchalert 252670 5800 인버스2X Bear트리거 2개+ 후 진입
/stoploss 252670 5200
```

### 2. WHR Whirlpool — 보류, Q2 어닝 (7/30) 이후 재검토
- 트리거: 7/30 Q2 어닝 + 7/1 리파이낸싱 이중 통과 후
```
/watchalert WHR 40 WHR 1차 보류 Q2 어닝 후
/watchalert WHR 38 WHR 2차 조건부
/stoploss WHR 32
/target WHR 58
```

### 3. GLD — 매크로 위기 헷지 (선택)
```
/watchalert GLD 305 GLD 1차 1.5%
/stoploss GLD 280
/target GLD 360
```

---

## F. EXIT 종목 (Kill 발동 — 즉시 실행)

### 1. XNDU Xanadu Quantum — 즉시 전량 손절
- 평단 $27.85, 현재가 $14.75, -47%
- 22일 보유, 손절선 부재 (iter 38 발견)
- 행동: **5/26 (월) 사전장 22:30 KST 전량 시장가 매도** (120주)
```
# 알람 불요 (즉시 매도)
```

### 2. 001040 CJ — EXIT 발동 (스캔에서 발견 시)
- 행동: 보유 중이면 손절, 아니면 워치 제거
```
# 보유 시: /stoploss 001040 (현재가 -7%)
```

---

## G. 텔레그램 봇 일괄 명령어 list (복사용)

```
/watchalert 047810 170000 KAI 1차 30% 방산 A등급
/watchalert 047810 160000 KAI 2차 40% 조정진입
/watchalert 047810 150000 KAI 3차 30% 큰조정
/stoploss 047810 148000
/target 047810 220000
/watchalert 139130 18920 IM금융 1차 30% NPS+자사주
/watchalert 139130 17500 IM금융 2차 50% MA60지지
/watchalert 139130 16000 IM금융 3차 20% 큰조정
/stoploss 139130 15500
/target 139130 24500
/watchalert 003690 13800 코리안리 1차 50% 재보험
/watchalert 003690 13200 코리안리 2차 50% MA120
/stoploss 003690 12200
/target 003690 16500
/watchalert 161390 56000 한국타이어 1차 매물대하단
/watchalert 161390 60000 한국타이어 2차 시나A 트리거
/stoploss 161390 50000
/target 161390 85000
/watchalert 402340 1050000 SK스퀘어 1차 40%
/watchalert 402340 950000 SK스퀘어 2차 60%
/stoploss 402340 880000
/target 402340 1400000
/watchalert 403870 54700 HPSP 1차 30%
/watchalert 403870 49000 HPSP 2차 40% MA60
/watchalert 403870 44000 HPSP 3차 30% MA200
/stoploss 403870 40000
/target 403870 65000
/stoploss 000660 1400000
/target 000660 2500000
/stoploss 298040 3200000
/target 298040 4623000
/stoploss 010120 239275
/target 010120 350000
/stoploss 267260 900000
/target 267260 1500000
/stoploss 005930 220000
/target 005930 370000
/stoploss 009540 350000
/target 009540 550000
/stoploss NVDA 170
/target NVDA 330
/stoploss AMZN 235
/target AMZN 310
/stoploss AVGO 360
/target AVGO 510
/stoploss 021240 78000
/target 021240 110000
/watchalert 064290 18000 인텍플러스 1차 30%
/watchalert 064290 16500 인텍플러스 2차 50%
/stoploss 064290 14500
/target 064290 26000
/watchalert 218410 26500 RFHIC 1차 표준
/stoploss 218410 22000
/target 218410 35000
/watchalert 251270 92000 넷마블 FCC catalyst
/stoploss 251270 82000
/target 251270 127000
/watchalert 257720 31000 실리콘투 1차
/stoploss 257720 27500
/target 257720 41000
/watchalert 204320 39000 HL만도 52w저점
/stoploss 204320 36000
/target 204320 52000
/watchalert 278470 90000 에이피알 보류
/stoploss 278470 80000
/target 278470 130000
/watchalert 139480 78000 이마트 1차
/stoploss 139480 70000
/target 139480 105000
/watchalert SARO 38 SARO 1차 30%
/stoploss SARO 31
/target SARO 52
/watchalert 449450 24000 KODEX방산 1차 50%
/watchalert 449450 22000 KODEX방산 2차 30%
/stoploss 449450 18500
/target 449450 32000
/watchalert 140700 18895 KODEX보험 1차 50%
/stoploss 140700 16200
/target 140700 23000
/watchalert 487240 13900 KODEX AI전력 1차 33%
/stoploss 487240 10500
/target 487240 18000
/watchalert 133690 95000 TIGER나스닥100 1차
/stoploss 133690 87000
/target 133690 110000
/watchalert GRID 145 GRID 페어
/stoploss GRID 128
/target GRID 180
/watchalert ITA 168 ITA 페어
/stoploss ITA 152
/target ITA 200
/watchalert 252670 5800 인버스2X Bear트리거+ 후
/stoploss 252670 5200
/watchalert WHR 40 WHR 보류 Q2어닝후
/stoploss WHR 32
/target WHR 58
/watchalert GLD 305 GLD 헷지 1.5%
/stoploss GLD 280
/target GLD 360
```

---

## H. 카테고리별 분포 요약

| 카테고리 | 종목 수 | 명령어 수 | 비중 권고 |
|---------|--------|----------|----------|
| A. 신규 매수 (즉시) | 6 | 25 | 16.5% |
| B. 보유 종목 손절/TP 갱신 | 10 | 20 | (기존) |
| C. 감시가 대기 (조정) | 8 | 28 | 16% (충족 시) |
| D. ETF 진입 | 6 | 21 | 18% |
| E. 헷지 (Conditional) | 3 | 9 | 5% |
| F. EXIT (즉시 매도) | 2 | 0 | -3.1% |
| **합계** | **35** | **103** | **52.4%** |

---

## I. 실행 순서 (5/26 월요일)

**09:00 KST 개장 직후 (한국):**
1. XNDU 22:30 KST 시장가 매도 (Step F1)
2. HD조선 16주 시장가 매도 (iter 39 Step B)
3. SK하이닉스 1주 25% 익절 분할 (iter 51/52)
4. 삼성전자 4-5주 25% 익절 분할 (iter 51 D2)

**09:30 ~ 10:00:**
5. 신규 매수 즉시 진입: KAI 1차 (5/22 종가 168,400, 진입가 170K 근접)
6. HPSP 1차 즉시 (54,700 5/22 종가)
7. KODEX 방산 1차 즉시

**10:00 ~ 11:00:**
8. IM금융 1차, 코리안리 1차, KODEX 보험 1차

**11:00 이후 — 알람 등록:**
9. 위 G 섹션 일괄 명령 텔레그램 봇 입력 (~103개)

**22:30 KST (US 개장):**
10. AMZN 11주 매도 (iter 39 Step B)
11. NVDA 손절 상향, AVGO 손절 상향

---

## J. Kill 조건 (plan 보류)

다음 조건 발동 시 plan 보류 + 사용자 재판단:
- KOSPI -3% 1일 하락
- VIX 25+ 진입 (Bear 트리거)
- 매크로 Bear C 발효 (regime_transition_alert)
- 보유 종목 1개라도 -5% 갭다운 개장
- 매크로 7/7 시나리오 robust 종목 EV +9.09% (현대로템) 우선

---

> **이 plan은 권고이며, 최종 실행은 사용자 판단에 따름.**
> iter 39 portfolio rebalance + iter 51 D2/D3 시그널 + iter 52 페어 Z=+2.19σ + Top 18 thesis + ETF 6 + EXIT 2 통합 반영.
