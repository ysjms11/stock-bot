# 스케줄 타임라인 (자동 잡)

> `main.py` 후반부 `jq.run_daily` / `jq.run_repeating` 등록부의 전체 뷰.
> 새 잡 추가/삭제/시각 변경 시 **이 표도 함께 업데이트** (동일 커밋에 포함).

**표 읽는 법**
- `D` = 요일 필터: `평일`(월~금), `주말`, `토`, `일`, `월`, `화~토`, `전체`
- `담당 함수`는 `main.py` 내부
- `최근 변경`은 기능 변경된 날짜만 기록 (시간 미세 조정 제외)

---

## 반복 잡 (interval)

| 주기 | 잡 이름 | 담당 함수 | 핵심 동작 | 최근 변경 |
|------|---------|-----------|-----------|-----------|
| 5분 | `dart` | `check_dart_disclosure` | DART 공시 체크 (8~20시 내부 필터, 워치+포트만) | 4/18 (30분→5분) |
| 10분 | `stoploss` | `check_stoploss` | 손절/목표가 감시 (한국 장중 WebSocket, 미국 Yahoo 폴링) | — |
| 30분 | `anomaly` | `check_anomaly` | 이상 이벤트 감지 (급등/급락) | — |
| 60분 | `regime_transition` | `regime_transition_alert` | 시장 레짐 전환 알림 | — |

---

## 일일/주간 잡 (시간 순)

### 새벽 (02:00 ~ 06:59 KST)

| 시간 | D | 잡 이름 | 담당 함수 | 핵심 동작 | 최근 변경 |
|------|---|---------|-----------|-----------|-----------|
| 02:00 | 전체 | `dart_incremental` | `daily_dart_incremental` | DART 신규 정기공시 증분 수집 → 알파 재계산 | 4/16 신규 |
| 03:00 | 일 | `weekly_us_harvest` | `weekly_us_ratings_universe_scan` | S&P 500 ∪ Russell 1000 전체 레이팅 수집 (~1000종목, ~33분) | 4/23 Russell 확장 |
| 05:05 | 화~토 | `us_summary_dst` | `us_market_summary` | 미국 장 마감 요약 (DST, 내부 가드로 중복 방지) | — |
| 06:00 | 전체 | `macro_am` | `macro_dashboard` | 매크로 대시보드 (미국장 마감 후) | — |
| 06:05 | 화~토 | `us_summary_std` | `us_market_summary` | 미국 장 마감 요약 (표준시, 내부 가드로 중복 방지) | — |

### 오전 (07:00 ~ 08:59 KST)

| 시간 | D | 잡 이름 | 담당 함수 | 핵심 동작 | 최근 변경 |
|------|---|---------|-----------|-----------|-----------|
| 07:00 | 평일 | `earnings_cal` | `check_earnings_calendar` | KR 실적 캘린더 체크 | — |
| 07:00 | 평일 | `dividend_cal` | `check_dividend_calendar` | 배당 캘린더 체크 | — |
| 07:00 | 토 | `weekly` | `weekly_review` | 주간 리뷰 | — |
| 07:00 | 월 | `universe_update` | `weekly_universe_update` | KOSPI250+KOSDAQ350 유니버스 갱신 | — |
| 07:05 | 일 | `consensus_update` | `weekly_consensus_update` | FnGuide 컨센서스 주간 업데이트 | — |
| 07:05 | 일 | `weekly_sanity` | `weekly_sanity_check` | daily_snapshot 영업일 누락 감시 | 4/18 신규 |
| 07:10 | 평일 | `us_earnings_cal` | `check_us_earnings_calendar` | 미국 실적 캘린더 체크 | — |
| 07:15 | 일 | `weekly_financial` | `weekly_financial_job` | 주간 재무 수집 (DART) | — |
| 07:30 | 전체 | `us_ratings` | `daily_us_rating_scan` | 미국 애널 레이팅 스캔 | 4/18 신규 |
| 08:30 | 평일 | `report_collect` | `collect_reports_daily` | 증권사 리포트 수집 | — |

### 오후 (12:00 ~ 16:59)

| 시간 | TZ | D | 잡 이름 | 담당 함수 | 핵심 동작 | 최근 변경 |
|------|----|---|---------|-----------|-----------|-----------|
| 12:00 | ET | 평일 | `us_holdings_noon` | `hourly_us_holdings_check` | 미국 보유 종목 애널 레이팅 감시 (DST 자동) | 4/18 신규 |
| 15:40 | KST | 평일 | `kr_summary` | `daily_kr_summary` | 한국 장 마감 요약 | — |
| 15:40 | KST | 평일 | `supply_drain` | `check_supply_drain` | 수급 고갈 체크 | — |
| 15:50 | KST | 평일 | `snapshot_dd` | `snapshot_and_drawdown` | 포트폴리오 스냅샷 + 드로다운 체크 | — |
| 16:30 | KST | 평일 | `momentum_check` | `momentum_exit_check` | 모멘텀 이탈 체크 | — |
| 16:30 | ET | 평일 | `us_holdings_close` | `hourly_us_holdings_check` | 미국 장 마감 애널 레이팅 감시 | 4/18 신규 |

### 저녁 (18:30 ~ 22:00 KST) ★ 피크 타임

| 시간 | D | 잡 이름 | 담당 함수 | 핵심 동작 | 최근 변경 |
|------|---|---------|-----------|-----------|-----------|
| 18:30 | 평일 | `daily_collect` ★ | `daily_collect_job` | **KRX 전종목 DB 수집** (18:30 + post_init retry + 주간 무결성) | 4/18 안전장치 3종 |
| 18:55 | 전체 | `macro_pm` | `macro_dashboard` | 매크로 대시보드 저녁 (수집 완료 후) | — |
| 19:00 | 평일 | `watch_change` | `watch_change_detect` | 워치리스트 변경 감지 | — |
| 19:00 | 일 | `sunday_30` | `sunday_30_reminder` | Sunday 30 리마인더 | — |
| 19:05 | 평일 | `daily_change_scan` | `daily_change_scan_alert` | 발굴 알림 (turnaround/fscore_jump/insider_cluster_buy) | 4/18 신규 |
| 19:30 | 평일 | `daily_consensus` | `daily_consensus_check` | 컨센서스 상향 체크 | — |
| 20:00 | 평일 | `insider_cluster` | `check_insider_cluster` | 내부자 군집 감지 (워치종목) | 4/15 신규 |
| 22:00 | 전체 | `auto_backup` | `auto_backup` | `/data/` Gist 백업 | — |

---

## 저녁 피크 타임 충돌/의존성

```
18:30  daily_collect (수집) ─┐
                             ├─ daily_collect 완료에 의존 (~21분 소요)
18:55  macro_pm (매크로) ────┘

19:00  watch_change, sunday_30 (일)
19:05  daily_change_scan  ─┐
19:30  daily_consensus    │ daily_collect 결과 또는 DB 의존
20:00  insider_cluster    ┘

22:00  auto_backup (전체 일괄)
```

**5분 간격 분리**로 텔레그램 rate limit 회피. 신규 잡 추가 시 **19:10~19:25 구간** 비어 있음.

## 주말 활동

- **토 07:00**: `weekly_review`
- **일 03:00**: `weekly_us_harvest` (S&P 500 ∪ Russell 1000 유니버스 애널 레이팅 수집, ~1000종목, ~33분)
- **일 07:05**: `consensus_update` + `weekly_sanity`
- **일 07:15**: `weekly_financial`
- **일 19:00**: `sunday_30`
- 매일 돌아가는 것: `dart_incremental` (02:00), `us_ratings` (07:30), `macro_am` (06:00), `macro_pm` (18:55), `auto_backup` (22:00), 반복잡 4종

## 타임존 메모

- 기본 KST (`tzinfo=KST`). Railway(UTC) 서버에서도 정확한 시각 실행 (2026-04-15 이후 Railway 삭제 후엔 맥미니만 사용, KST 직접)
- `hourly_us_holdings_check` 만 **ET** (`tzinfo=ET`). `zoneinfo`가 DST 자동 전환

## 신규 잡 추가 절차

1. `main.py` 함수 작성 (`async def daily_XXX(context): ...`)
2. `main.py` `main()` 의 `jq.run_daily(...)` 등록 (시간대/요일/name)
3. **이 표에 1줄 추가** (같은 커밋)
4. `data/PROGRESS.md` "주요 아키텍처 결정" 표에 신규 스케줄 기록 (변경일, 이유)
