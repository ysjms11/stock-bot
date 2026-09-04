# TICKET — 매크로 일봉 + 시장 투자자 flow 시계열 저장 (v4 검증 후속 #2/#3)

> 2026-09-03 | 배포: 커밋 직후 재시작 + 1회 백필(2024-01~) | 게이트: dev → Opus 리뷰(조건부 통과, blocker 2·중요 8 반영) → verifier

## A. 배경
- "KOSPI 방향성 예측 v4" 프롬프트 풀테스트(2026-09-02)에서 정확도 주장은 **캘린더 정렬 룩어헤드**로 기각. 다만 우리 DB에 없던 데이터 4종을 식별: ① VKOSPI(KRX 계정 필요, 보류) ② 매크로 일봉 시계열 ③ 시장별 투자자 flow 시계열 ④ 베이시스/P-C/프로그램(별도).
- 이 티켓 = ②·③. 기존엔 `get_macro_external`이 매 호출 라이브 조회만 하고 **히스토리를 저장하지 않아** SAT_PORT_CHECK "8변수 임계 돌파" 비교(±2%·±20bp·60일 MA 이탈)와 레짐 `foreign_5d`(항상 None)가 수치화 불가였음.

## B. 설계
- **테이블 2개** (`db_collector/market_data.py`, 런타임 `CREATE IF NOT EXISTS`, 기존 테이블 무변경):
  - `macro_daily(series, date, value, source, updated_at)` long format — kospi·kosdaq·usdkrw(FDR) / nasdaq·sp500·vix·us10y·us2y·dxy·wti·gold(yfinance)
  - `market_flow_daily(date, market, frgn_net, orgn_net, prsn_net)` 백만원 — KIS `FHPTJ04040000` 범위조회(KSP `0001` / KSQ `1001`)
- **Point-in-time 정렬**: `series_asof_window()`가 US 시리즈는 `kst_date-1`까지만 반환(미국 세션 D의 종가는 KST D+1 05:00 확정). KR/FX는 당일까지. 소비자는 이 함수만 사용 → 룩어헤드 구조적 차단.
- **증분 규칙**: 시작점 = `latest - 5일` + INSERT OR REPLACE. 16시대 진행중 봉(WTI·금·DXY·10Y)·잠정 수급이 다음 실행에서 확정치로 자동 덮임. 첫 실행은 2024-01-01부터.
- **잡** `market_data` 평일 **19:08 KST** (`main_pkg/jobs/market_data.py`): KRX 확정 수급(~18:00 공개) 당일 수거. 주말 스킵, 미등록 휴장 평일은 flow만 스킵(`_is_kr_trading_day("YYYYMMDD")` — **문자열 전용, 타입 틀리면 fail-open**). `wait_for 300s`, 성공 무발송, 실패는 `_track_silent_failure("market_data_error")`.
- **소비자 2곳(additive)**: ① `kis_api/regime.py` `indicators.foreign_5d`(억원) + `confirmations.foreign_outflow_5d`(≤ -2조, **비게이팅** — crisis/overheat/디바운스 로직 diff 0) + 신선도 가드(최신행 7일 초과 시 None). ② `get_macro_external.thresholds` 8항목: VIX(20/30/40) · USDKRW ±2% · US10Y ±20bp · WTI ±5% · DXY ±1% · KOSPI vs MA60 · SP500 vs MA60 · US10Y-2Y(<0.25). 각 항목 `breached: True/False/None`(데이터 부족).
- **KIS 기존 버그 수정 동봉**: `_fetch_market_investor_flow`가 KOSDAQ에 KOSPI 지수코드(`0001`)를 넘겨 **rt_cd=0 + 전부 0**을 조용히 반환하던 것 → `_MARKET_IDX_CODE` 맵. KSP 파라미터는 바이트 동일(매크로 대시보드 무회귀). 방어: 청크 전체 0이면 미저장 + `error:all_zero`.

## C. 리뷰 반영 (Opus, 2026-09-03)
| # | 지적 | 조치 |
|---|------|------|
| B1 | flow 폴백이 응답 날짜 대신 벽시계 날짜 스탬프 → 휴장일 유령행, 영구 미보정 | 폴백 삭제. 저장은 `stck_bsop_date`만 |
| W1 | `latest+1` 증분이라 진행중 봉이 영구 박제 | `latest-5일` 재조회 + upsert |
| W2 | flow 백필 250일 → 2024-01 미달·영구 결손 | `backfill_from="2024-01-01"` |
| W3 | `db_write_lock` 우회(executor 스레드) | fetch=`to_thread` / DDL·write=락 안 분리 |
| W4 | KSQ 침묵-0 재발 시 0 적재·테스트 미단정 | all_zero 가드 + `fid_input_iscd` 단정 테스트 |
| W5 | "8변수"인데 7항목 | US10Y-2Y 스프레드 추가(us2y 소비처) |
| W6 | foreign_5d 신선도 가드 없음 | 7일 초과 None |
| W7 | `_BACKING` 미등록(monkeypatch 투명성) | 등록 + kr_stock lazy import(순환 제거) |
| W8 | 잡 거래일 가드 없음 | 위 B |
| — | 잡 16:05는 수급 잠정치 시각 | 19:08로 이동(19:05 change_scan·19:15 sanity 사이 빈 슬롯) |

### C-2. 리뷰가 발견한 **타 세션 WIP 위험** (이 티켓 범위 밖, 미수정 — 운영자 판단)
`db_collector/collect.py`·`_config.py`(7/19 미커밋, 브랜치 `fix/collector-div-yield-foreign-amt`, **재시작들로 이미 라이브 실행 중**)의 "미등록 휴장일 자가롤백" 가드: 직전 거래일과 종가 98%+ 동일 → 당일 snapshot 전부 DELETE + `skipped`. 그런데 `daily_collect_sanity_check`(19:15/20:15/21:15/22:15)는 "당일 0건 → collect_daily 재실행"이라 **롤백이 성공할수록 4회 재수집(~21분·수천 KIS 콜) + 4회 ⚠️ 알림**. 등록된 휴장일(8/17·9/24·9/25)은 수집 자체가 스킵돼 무관. **미등록 후보: 2026-10-05(개천절 대체휴일)·2026-12-31(연말휴장)**. 권고: 롤백 마커 영속화 + sanity 스킵, 또는 두 날짜 목록 등록.

## D. 배포·검증 (2026-09-04)
- **커밋** `0212334` feat(data) — 12파일(+1377/-31). 타 세션 WIP(`collect.py`·`_config.py`·`PROGRESS.md`·`events.json`·thesis) 미포함.
- **재시작** 10:05:42 (`launchctl kickstart -k`), /health ok · last_snapshot 2026-09-03 15:50. 기동 중 KIS 500 재시도 다수(KIS 측 일시장애, 재시도 성공).
- **1회 백필** (별도 프로세스, `backfill_from="2024-01-01"`): macro 11시리즈 651~694행/시리즈 (2024-01-02~2026-09-03, 3초) · flow KSP/KSQ 각 652행 (2024-01-02~2026-09-04, 5초, KIS 500 1회 재시도) · (date,market) 중복 0 · **KSQ 비영(0) 확인 = 지수코드 수정 실효**.
  - 주의: 장중(10:05) 백필이라 09-04 flow 행은 **잠정치**(당일 누적). 19:08 잡의 5일 재조회가 확정치로 덮음(설계대로).
- **라이브** `get_regime`: `kr.indicators.foreign_5d = -27784`(억원) + `confirmations.foreign_outflow_5d = true` — 레짐 🟡 중립 19일차 **무변동(비게이팅 확인)**. `get_macro_external.thresholds`: 8항목 반환, breaches 2 (WTI 5d +9.61% · KOSPI vs MA60 -9.15%), VIX는 US 컷오프대로 09-03 값(14.32).
- **잔여/후속**
  - `US10Y-2Y` 항목은 `2YY=F`(CBOT 수익률 선물) 프록시 + us2y가 하루 지연(09-02) → 0.562 vs Treasury 공식 0.40(같은 응답 `treasury` 블록). 경계(0.25) 판정엔 공식 곡선이 권위 — 항목을 treasury 블록 값으로 교체하는 후속 권장(소규모).
  - `usdkrw`(FDR)는 실측 1~2일 지연. KR 시리즈 신선도 가드는 없음(레짐 foreign_5d만 7일 가드).
  - **첫 스케줄 실행 실측(09-04 19:08:04)**: `macro={kospi:5, …}`(09-04 봉 6행 기록) 정상, **`flow={'KSP':0,'KSQ':0}`** — 10:05 수동 백필이 09-04 행을 먼저 넣어 `latest >= today` 조기종료가 걸림. 정상 운영(하루 1회)에선 무해하나 수동/복구 재실행이 조용히 no-op이 되는 결함 → 조기종료 제거(항상 `latest-5일`부터 재조회, upsert 멱등) 후속 커밋. 09-04 flow 잠정치는 그 배포 후 수동 재실행으로 확정치 덮어씀.
