# TICKET — US 레짐 오전환 사고 (yfinance 스레드 race) + 하드닝

> 2026-09-04 | 상태 복원 10:13 (수동, Gist 백업 기준) | 코드 하드닝: dev → Opus 리뷰 → verifier → 배포 (아래 D절)

## A. 사고
- **10:06** 봇 재시작(10:05:42, market_data 배포) 직후 캐시 워밍 + `get_regime` 호출 → US 지표가 `vix_val=76.46`(타 심볼 가격) · `vix3m=14.32`(=실제 ^VIX 값) · `sp_dist=None`으로 계산·저장.
- 결과: US 레짐 **🟢 offensive 5일차 → 🟡 neutral 즉시 확정**(🟡 디바운스 1일). 🟢 복귀는 8일 디바운스라 방치 시 **8일 고착**. 히스토리 09-04 row가 `us_vix_pct=None, us_sp_dist=None`으로 덮임.
- **10:10:42** `regime_transition` 잡(기동 후 first=300s)이 `regime_transition_sent.json`(08-29 저장 `us: offensive`) vs 오염 상태를 비교 → **가짜 "🇺🇸 🟢→🟡" 알림 발송 추정**(파일 mtime 10:10, 발송 텍스트는 로그 미기록). 복원 후 11:10 잡이 "🟡→🟢" 정정 알림을 자동 발송.
- 재발성: 히스토리 90일 중 **2026-07-13**에도 동일 None 패턴 1회.

## B. 근본원인
- `kis_api/regime.py` `cmd_regime` current 경로: `asyncio.gather(to_thread(calc_kr_regime), to_thread(calc_us_regime))` — 두 스레드가 동시에 `kis_api/news.py:280 _yf_history()` 호출. KR: `KRW=X`(+`^KS11` 폴백) / US: `^VIX`·`^VIX3M`·`^GSPC`·`HYG`·`LQD`·`^TNX`·`^IRX`.
- `_yf_history`가 **`yf.download()`** 사용 — yfinance `download`는 모듈 전역 공유 dict(`yfinance.shared._DFS`/`_ERRORS`)에 결과를 모았다가 꺼내는 구조라 **스레드 동시호출 시 심볼 간 DataFrame이 뒤섞임**(비스레드안전, 공지된 함정). 재시작 직후엔 대시보드 워밍(macro_panel·us_candidates)까지 겹쳐 충돌 확률 급등.
- 2차 결함: 지표가 전부 None이어도 `calc_us_regime`이 🟡 neutral을 반환하고 `cmd_regime`이 그대로 디바운스·상태·히스토리를 덮어씀 — **결측 = 중립**으로 오해석. 🟡 임계 1일이라 한 번의 오염이 즉시 확정됨.
- 격리 순차 재계산은 정상(offensive, S&P +8.56%, VIX 2.8%ile, VIX3M 17.42) → 데이터 소스 문제 아님.
- **메커니즘 확증 (Opus 리뷰)**: `yfinance/multi.py:141` `shared._DFS = {}` 전역 rebind → 각 스레드가 여기 기록 → `:157 while len(shared._DFS) < len(tickers)` → `:209 concat`. 동시 `download()` 2건이면 뒤 호출의 리셋이 앞 호출 결과를 지우고 **다른 심볼 DF로 조립**. 반면 `scrapers/history.py`(`Ticker.history`)는 `shared._DFS`를 읽지 않음 → 전환이 유효한 차단.
- **상시 race 경로 (재시작과 무관)**: `dashboard_home/payloads.py:1120-1124`가 `get_regime`+`get_macro`를 `asyncio.gather` → 각각 `calc_us_regime`(to_thread)·`kis_api/macro.py:84`(to_thread)에서 **동시에 `^GSPC` download**. 즉 대시보드 열 때마다 창이 열림. 잔존 `yf.download`: `kis_api/kr_stock.py:531`(볼륨프로파일·백테스트 캔들 — MCP 병렬 툴콜 2건이면 타 종목 캔들 반환 가능), `db_collector/us_analysts.py:236`(buy_candidates 현재가) → 공용 락으로 직렬화(D절).

## C. 복원 (10:13)
- 원본 백업 `data/regime_state.json.bak_20260904_yfglitch`.
- Gist 22:00 백업(09-03, us offensive 5일차·debounce 12) 기준으로 당일 갱신 재적용: `us` = offensive **6일차**(debounce 13, last_updated 09-04, 지표=격리 재계산값), 상위 `current` 미러·`prev_regime=offensive`, 히스토리 09-04 row 정정(`us_vix_pct 2.8, us_sp_dist 8.56`). **KR 블록(🟡 19일차, foreign_5d -27,784억) 무수정.** 원자적 쓰기(tmp+replace). 봇 프로세스 history 모드로 반영 확인.

## D. 하드닝 (코드, 본 티켓과 동일 커밋)
- **`kis_api/news.py` `_yf_history`**: `yf.download()` → `yf.Ticker(symbol).history()` (조립에 `shared._DFS` 미사용) + 공용 `kis_api/_helpers.py` `YF_LOCK`으로 직렬화. 라이브 대조: ^VIX/^GSPC/KRW=X 2y 길이·마지막값 download와 오차 0, 10심볼 직렬 1.9초.
- **잔존 `yf.download` 2곳 락**: `kis_api/kr_stock.py get_historical_ohlcv`(볼륨프로파일·백테스트) · `db_collector/us_analysts.py find_us_buy_candidates` — 동작 무변경, 같은 락 객체(실측 4스레드 max_concurrency=1). 락 밖 download 0건.
- **결측 = 이전 상태 유지** (`kis_api/regime.py`): `calc_kr/us_regime`이 1차 입력 **하나라도** 결측이면(`OR`, 리뷰 blocker — ^GSPC 단독 실패도 커버) `data_unavailable=True`. `cmd_regime`은 그 시장의 디바운스·`state[mkt]`·상위 미러·`prev_regime`을 건너뛰고 보존; 반환 서브딕트 `regime_en/regime/cash_posture`도 보존값 + `data_unavailable: true` + logic "지표 조회 실패 — 이전 상태 유지"; history 당일 row는 이전 지표 + `"data_unavailable": ["us"]` 마커(정상 row는 키 부재로 바이트 불변). `indicators=None`·빈 state 등 기형 5종 무예외.
- **무음 동결 방지**: 결측 시 print + `state[mkt].unavailable_streak/unavailable_date`(하루 1회 증가, 정상 시 키 제거) → `regime_transition` 잡이 하루 1회 "⚠️ 레짐 지표 조회 실패 N일째 — 이전 상태 유지 중" 발송. 기존 전환 비교 로직 6케이스 HEAD와 동일.
- **정상 경로 무변경 증명**: HEAD `regime.py`와 동일 목 입력 4시나리오(+calc 6) payload·state JSON 완전 동일(리뷰 12시나리오 1682줄 일치 + verifier 재현).
- **게이트**: dev(616) → Opus 리뷰(blocker 1·중요 5 → 2라운드 반영) → verifier APPROVE — 전체 **988 passed / 25 skipped**, `test_regime.py` 83(신규 14: 단일레그 결측·보존·기형 state·streak·락 공유·잡 경고).
- **후속(비차단)**: ① `test_regime.py:16`의 `kis_api.REGIME_STATE_FILE` 모듈 패치는 서브모듈에 미전파 — `cmd_regime` 호출 테스트 17개가 전부 개별 `@patch`라 현재 안전하나, 미데코 테스트 추가 시 **프로덕션 regime_state.json에 쓰기** 위험 → autouse fixture로 강제 패치 권장. ② 결측 시 `res[mkt]["indicators"]`는 당일 None(보존값 아님) — `data_unavailable`이 설명하나 `indicators_stale` 분리 고려. ③ 뉴스/ETF/market_data의 `Ticker` 직접 호출 5곳 무락(조립에 `_DFS` 안 써 실질 무해).
- **배포**: 커밋 후 재시작(아래 로그). 복원 상태(🟢 6일차) 유지 확인 → 11:10 전환잡의 "🟡→🟢" 정정 알림 발송 확인.
