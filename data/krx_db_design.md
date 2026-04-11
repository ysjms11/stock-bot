# KRX DB 설계서 v2.0
> 확정일: 2026-04-08
> 목적: 전종목 일별 데이터 수집 → 변화 감지 스캔 → 종목 발굴

> **⚠️ 이 문서는 기존 JSON DB 설계서입니다. 현재는 SQLite DB (data/krx.db) 사용 중.**
> **최신 스키마는 `data/db_schema.sql` 참조. JSON 방식은 SQLite 전환으로 폐기.**

---

## 수집 구조

```
맥미니 cron 15:55 KST (asyncio 병렬):
├── Task 1: KRX OPEN API → 시세/시총/종목정보 (30초)
├── Task 2: pykrx → PER/PBR/EPS/BPS/배당수익률 (30초)
├── Task 3: pykrx → 외인/기관/개인 순매수금액 (30초)
├── Task 4: pykrx → 공매도 잔고/비중 (30초)
├── Task 5: pykrx → 외인 보유비율/한도소진율 (30초)
├── Task 6: data.krx.co.kr 크롤링 → 신용잔고/대차잔고 (30초)
├── Task 7: FnGuide → 컨센서스 목표가 전종목 (~22분)
├── merge → 기술적 지표 계산 → 추세 점수 계산
└── 저장 → data/krx_db/YYYYMMDD.json
```

차단 시 GitHub Actions fallback 추가.
보관: 무제한 (cleanup 삭제).
초기 백필: pykrx로 과거 1년치 (GitHub Actions에서 1회).

---

## daily JSON 필드 전체 (~60개)

### 1. 시세 (KRX OPEN API)
| # | 필드 | 설명 |
|---|---|---|
| 1 | close | 종가 |
| 2 | open | 시가 |
| 3 | high | 고가 |
| 4 | low | 저가 |
| 5 | chg_pct | 등락률(%) |
| 6 | volume | 거래량 |
| 7 | trade_value | 거래대금 |
| 8 | market_cap | 시가총액 |

### 2. 종목정보 (KRX OPEN API)
| # | 필드 | 설명 |
|---|---|---|
| 9 | sector_code | 업종코드 |
| 10 | sector_name | 업종명 |
| 11 | list_shares | 상장주식수 |
| 12 | market | kospi/kosdaq |

### 3. 밸류에이션 (pykrx)
| # | 필드 | 설명 |
|---|---|---|
| 13 | per | PER |
| 14 | pbr | PBR |
| 15 | eps | EPS |
| 16 | bps | BPS |
| 17 | div_yield | 배당수익률 |

### 4. 수급 (pykrx)
| # | 필드 | 설명 |
|---|---|---|
| 18 | foreign_net_amt | 외인 순매수금액 |
| 19 | inst_net_amt | 기관 순매수금액 |
| 20 | indiv_net_amt | 개인 순매수금액 |

### 5. 공매도 (pykrx)
| # | 필드 | 설명 |
|---|---|---|
| 21 | short_balance | 공매도 잔고 |
| 22 | short_ratio | 공매도 비중(%) |

### 6. 외인 보유 (pykrx)
| # | 필드 | 설명 |
|---|---|---|
| 23 | foreign_hold_ratio | 외인 보유비율(%) |
| 24 | foreign_exhaust_rate | 한도소진율(%) |

### 7. 신용/대차 (data.krx.co.kr 크롤링)
| # | 필드 | 설명 |
|---|---|---|
| 25 | credit_balance | 신용잔고 |
| 26 | lending_balance | 대차잔고 |

### 8. 컨센서스 (FnGuide)
| # | 필드 | 설명 |
|---|---|---|
| 27 | consensus_target | 컨센서스 목표가 |
| 28 | consensus_count | 커버 증권사 수 |
| 29 | consensus_gap | (목표가-현재가)/현재가 % |

### 9. 이평선 (종가 기반 계산)
| # | 필드 | 설명 |
|---|---|---|
| 30 | ma5 | 5일 이평선 |
| 31 | ma10 | 10일 이평선 |
| 32 | ma20 | 20일 이평선 |
| 33 | ma60 | 60일 이평선 |
| 34 | ma120 | 120일 이평선 |
| 35 | ma200 | 200일 이평선 |

### 10. 기술적 지표 (계산)
| # | 필드 | 설명 |
|---|---|---|
| 36 | rsi14 | RSI(14) |
| 37 | bb_upper | 볼린저 상단 (MA20+2σ) |
| 38 | bb_lower | 볼린저 하단 (MA20-2σ) |
| 39 | ma_spread | (MA5-MA60)/MA60 % |

### 11. 비율 (계산)
| # | 필드 | 설명 |
|---|---|---|
| 40 | turnover | 거래대금/시총 (%) |
| 41 | foreign_ratio | 외인순매수/시총 (%) |
| 42 | inst_ratio | 기관순매수/시총 (%) |
| 43 | fi_ratio | (외인+기관)/시총 (%) |

### 12. 추세 점수 (N일 비교 계산)
| # | 필드 | 설명 |
|---|---|---|
| 44 | foreign_trend_20d | 20일 중 외인 순매수 비율 (0~1) |
| 45 | short_change_10d | 공매도 잔고 10일 변화율(%) |
| 46 | credit_change_5d | 신용잔고 5일 변화율(%) |
| 47 | volume_ratio_10d | 10일 평균 거래량 / 20일전 대비 |
| 48 | ma_spread_change_30d | MA spread 30일간 변화 |
| 49 | rsi_change_20d | RSI 20일간 변화 |
| 50 | foreign_hold_change_5d | 외인 보유비율 5일 변화(%p) |

### 13. 매물대 (1년 종가+거래량 기반 계산, 백필 후 활성화)
| # | 필드 | 설명 |
|---|---|---|
| 51 | vp_poc | 최다 거래 가격 (POC) |
| 52 | vp_va_high | VA 상단 (70%) |
| 53 | vp_va_low | VA 하단 (70%) |
| 54 | vp_position | (현재가-VA_low)/(VA_high-VA_low) |

### 14. 실적 괴리 (계산)
| # | 필드 | 설명 |
|---|---|---|
| 55 | eps_change_90d | EPS 90일 변화율(%) |
| 56 | ytd_return | 연초 대비 수익률(%) |
| 57 | earnings_gap | eps_change - ytd_return (실적 대비 주가 괴리) |

### 15. 섹터 상대강도 (계산)
| # | 필드 | 설명 |
|---|---|---|
| 58 | sector_rank | 같은 업종 내 등락률 순위 |
| 59 | sector_rel_strength | 종목 등락률 - 업종 평균(%) |

### 16. 52주 위치 (계산)
| # | 필드 | 설명 |
|---|---|---|
| 60 | w52_high | 250거래일 최고가 |
| 61 | w52_low | 250거래일 최저가 |
| 62 | w52_position | (현재가-저가)/(고가-저가) (0=바닥, 1=고점) |

---

## 변화 감지 스캔 프리셋 (get_change_scan)

| 프리셋 | 로직 | 핵심 필드 |
|---|---|---|
| short_squeeze | 공매도 10일 -30% 이상 감소 | short_change_10d |
| credit_unwind | 신용잔고 5일 연속 감소 | credit_change_5d |
| ma_convergence | MA spread 3% 이내 + 30일간 수렴 가속 | ma_spread + ma_spread_change_30d |
| foreign_reversal | 외인 5일 매도 → 매수 전환 | foreign_trend_20d |
| volume_spike | 10일 평균 거래량 20일전 대비 3배 이상 | volume_ratio_10d |
| earnings_disconnect | OP/EPS +30% 이상 but 주가 YTD 보합/하락 | earnings_gap |
| foreign_accumulation | 외인 보유비율 5일 +1%p 이상 증가 | foreign_hold_change_5d |
| oversold_bounce | RSI 30 이하에서 반등 시작 (RSI 상승 전환) | rsi14 + rsi_change_20d |
| consensus_undervalued | 컨센서스 괴리율 40% 이상 | consensus_gap |
| sector_leader | 섹터 상대강도 +5% 이상 (업종 내 최강) | sector_rel_strength |
| vp_support | 현재가가 VA 하단 근접 (vp_position < 0.2) | vp_position |
| golden_cross | MA5 > MA20 전환 (전일 MA5 < MA20) | ma5 + ma20 |

복합 스캔 예시:
- "earnings_disconnect + foreign_reversal + vp_support"
  = 실적 좋은데 주가 안 올랐고, 외인이 사기 시작했고, VP 지지대에 있는 종목

---

## 예상 용량
- daily JSON: ~2MB/일 (62필드 × 2,700종목)
- 연간: ~500MB
- 맥미니 256GB: 500년치 저장 가능

## 구현 순서
1. pykrx 전종목 수집 (시세/밸류/수급/공매도/외인보유)
2. 신용잔고/대차잔고 크롤링
3. 컨센서스 전종목 수집 (FnGuide)
4. 기술적 지표 계산 (이평선/RSI/볼린저)
5. 추세 점수 계산 (N일 비교)
6. 매물대 계산 (백필 후)
7. 실적괴리/섹터상대강도/52주위치 계산
8. get_change_scan MCP 도구 추가
9. 과거 1년 백필 (GitHub Actions)
10. 보관 무제한 (cleanup 삭제)
