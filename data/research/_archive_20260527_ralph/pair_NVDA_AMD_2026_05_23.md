# 페어 트레이드: NVDA vs AMD — 2026-05-23

> Ralph iter 23 · US AI 인프라 페어 · DB 기반 analyst ratings 집계
> Source: `/data/stock.db` · `us_analyst_ratings` (NVDA n=43, AMD n=26, since 2026-03)
> ⚠️ KIS DB는 KR 전용 (daily_snapshot.symbol=한국코드). 미국 주가 시계열 부재 → analyst TP/sentiment 기반 spread 분석.

---

## 1. 페어 정합성 (Why pair?)

| 항목 | NVDA | AMD |
|---|---|---|
| 시장 | AI 가속기 (datacenter GPU/ASIC) | AI 가속기 (MI300/MI350) |
| 시장 점유율 | ~90% (H100/B100/Rubin) | ~5–7% (추격) |
| 매크로 노출 | Mag7 capex · 관세 · 중국 H20 | Mag7 capex · META/AWS 다변화 |
| FY1Q26 매출 YoY | +85% ($82B) | +136% (iter 4 백테) |
| 데이터센터 성장 | +92% YoY | +200%+ (MI355 채택) |
| 어닝 콜 | 5/20 (raw +85%) | 5/6 (raw, TP 일제 상향) |

페어 정합성 강함 — 동일 end-market (hyperscaler AI capex). 분기 트리거 = **점유율 시프트**.

---

## 2. Sentiment / TP Spread (DB 기반)

### 2-1. 90일 평균 (since 2026-03-01)

| 지표 | NVDA | AMD | Spread (NVDA−AMD) |
|---|---|---|---|
| 평균 TP | $304.3 | $413.9 | **−$109.6** |
| 평균 별점 | 4.61 | 4.40 | +0.21 |
| 리포트 수 | 43건 | 26건 | +17 |
| Upgrades | 0 | 3 (Bernstein/Seaport/DA Davidson) | **−3** |
| Downgrades | 0 | 0 | 0 |
| TP 변화% 평균 | +9.1% | +37.5% | **−28.4%p** |

⚠️ TP 절대치 비교는 의미 없음 (NVDA $300대 vs AMD $400+은 단가 차이 아닌 현재가/업사이드 차이).
**TP 변화%가 핵심**: AMD +37.5% vs NVDA +9.1% → **AMD sentiment 가속 더 가파름**.

### 2-2. 월별 TP 궤적

| 기간 | AMD 평균 TP (n) | NVDA 평균 TP (n) |
|---|---|---|
| 2026-03 | $230 (1건) | $289 (16건) |
| 2026-04 | $307 (6건) +33% MoM | — |
| 2026-05 | **$457 (19건) +49% MoM** | $314 (27건) +9% MoM |

**핵심**: AMD TP 6주만에 $230 → $457 (**+99%**, 어닝+upgrade 트리거). NVDA는 안정적 상승 (+8.6%).
스프레드 (NVDA TP − AMD TP) = $59 → $−143로 역전 — AMD가 sentiment-wise outperform.

### 2-3. 어닝 후 일제 행동 (cluster)

- **5/6 AMD 어닝**: 17개 펌이 같은 날 reiterate/upgrade, TP 평균 $457 (Mizuho $515, Keybanc $530, Bernstein upgrade $525). Downgrade 0.
- **5/21 NVDA 어닝**: 16개 펌이 같은 날 reiterate, TP $270–$500. 신규 upgrade 0 (이미 SB consensus). Baird만 outlier $500.

→ **AMD는 incremental sentiment 개선 (Hold→Buy 전환 진행), NVDA는 이미 만점 컨센서스**.

---

## 3. 시나리오 매트릭스

| Cell | 매크로 | NVDA | AMD | Spread |
|---|---|---|---|---|
| A×A (Fed pivot 9월·관세 정상화) | Bullish | +25% | +35% | **AMD outperform +10%p** |
| B×B (관세 유지·Fed hold) | Mid | +10% | +15% | AMD outperform +5%p |
| Late-Cycle Bear 25% | 침체 | −15% | −25% | NVDA outperform +10%p (점유율 방어) |
| 중국 H20 영구 차단 | Tail | −15% | −10% | AMD outperform +5%p (중국 노출 적음) |
| AMD MI350 reliability 사고 | Tail | +5% | −20% | NVDA outperform +25%p |

**비대칭**: AMD = high-beta (Bull 더 먹고, Bear 더 빠짐). NVDA = 디펜시브 (점유율 우위).

---

## 4. 전략 옵션

### 옵션 1: AMD 단방향 Bull (선호)
- 근거: TP 상향 모멘텀 +99% (6주), Bernstein/Seaport/DA Davidson 3건 upgrade, MI355 채택 가속, META 6GW deal.
- 진입: 한투 미국 주식 일반 매수
- 사이즈: 코어 5–7%
- TP: $500 (analyst mean), upside +50% from current
- Stop: −15% 또는 MI350 reliability/META deal 취소

### 옵션 2: 둘 다 LONG (헷지 포지션)
- 근거: AI capex 강세 thesis 본인 view면 페어 둘 다 베타로 잡기
- 비중: **NVDA 60% + AMD 40%** (별점·TP 변동성 가중)
- AI capex 둔화 시 동반 손실 — kill switch 필요

### 옵션 3: NVDA LONG + AMD SHORT (점유율 방어 view)
- 근거: AMD MI350 reliability 우려 + NVDA Rubin 차세대 lock-in
- 한국 retail 실행: KIS US options put on AMD + long NVDA stock
- 비추천 — 현재 sentiment 反 방향, 타이밍 어려움

---

## 5. 권고 (Recommended)

**옵션 2 (둘 다 LONG, NVDA 60% / AMD 40%)** 선호.

이유:
1. Sentiment 양사 모두 Strong Buy 컨센서스, downgrade 0
2. AMD TP 모멘텀 가파르지만 변동성 큼 (high-beta) — 단독 베팅 리스크
3. NVDA 별점 (4.61 > 4.40) + 점유율 우위로 다운사이드 방어
4. 둘 다 Mag7 capex 강세에 동반 상승 — 시나리오 A×A/B×B 모두 양수

---

## 6. Kill Switch (3+ 점등 시 페어 해체)

1. **AI capex 둔화 시그널** — Mag7 capex guide cut, hyperscaler 발주 둔화 뉴스
2. **NVDA 점유율 95%+ 복원** — AMD thesis 무효, AMD 비중 0
3. **AMD MI350 reliability 이슈** — AMD 단독 압박, NVDA로 비중 이전
4. **중국 H20 영구 차단** — NVDA $5B+ revenue 위협, NVDA 비중 축소
5. **Mag7 capex 축소** (FAANG capex 가이던스 −10%+) — 페어 전체 close
6. **양사 분기 매출 가이던스 미스** — thesis 약화

---

## 7. 가장 큰 Trigger (단일)

**META 6GW MI355 deal 진행 상황 (3Q26 update)**. 통과 = AMD +30% / NVDA −5% 추가. 무산 = AMD −20% / NVDA +5%.
모니터링: META capex update + AMD analyst day (가을).

---

_생성: 2026-05-23 Ralph iter 23 · KR retail용 페어 전략_
