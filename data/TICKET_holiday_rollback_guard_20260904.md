# TICKET — 미등록 휴장일 자가롤백 가드 완성 (7/19 WIP 인수) + 10Y-2Y 공식값 + 테스트 격리

> 2026-09-04 | 7/19 다른 세션 미커밋 WIP(`db_collector/_config.py`·`collect.py`, 재시작들로 이미 라이브 실행 중)를 인수해 첫 리뷰·보강 후 커밋

## A. 배경
- 7/17 제헌절(2026 재지정)이 하드코딩 휴장일 목록에 없어 수집이 돌았고 KIS가 전일 시세를 반환 → daily_snapshot 복제 2,864행 유입(7/18 발견). 다른 세션이 목록에 7/17 추가 + "직전 거래일과 종가 98%+ 동일 → 당일 행 DELETE" 자가롤백 가드를 작성했으나 미커밋·미리뷰 상태로 방치, 라이브에서는 실행 중.
- market_data 리뷰(9/3)에서 발견된 위험: 롤백이 성공하면 sanity 잡(19:15/20:15/21:15/22:15 "당일 0건→재수집+알림")이 4회 재수집(~21분·수천 KIS 콜)+4회 경고 루프. 미등록 후보 10/5(개천절 대체)·12/31(연말휴장).

## B. 리뷰 반영 (Opus 1라운드, 첫 리뷰)
| # | 지적 | 조치 |
|---|------|------|
| B1 | 롤백 DELETE가 `db_write_lock` 밖 + sync라 busy_timeout 30초 이벤트루프 블록 | 판정 read(`_detect_holiday_duplicate`, `to_thread`) / DELETE(락 안) / 마커(락 해제 후) 분리 |
| W1 | 마커가 sanity 자가치유를 영구 차단하는데 휴장일 확증 없음(KRX 폴백 실패 이틀이면 0=0 매칭) | KIS 005930 일봉(`FHKST03010100`)에 당일 캔들 있으면 거래일 → 롤백·마커 금지 + 의심 알림. close=0 매칭 >50%면 롤백은 하되 마커 미기록 |
| W2 | 직전일이 부분수집(예 8/14 596행)이면 98% 도달 불가 → 가드 무력화 | prev = 행수 ≥ 0.9×tot인 최근 거래일(10일 소급), 없으면 `skipped_reason=prev_partial` |
| W3 | 알림 `_KR_MARKET_HOLIDAYS`의 `_`로 Markdown 파싱 오류 | plain `send_message` (모듈 관례) |
| W4 | Treasury 공식값은 실시간이라 과거 날짜 호출 시 룩어헤드 | `kst_date`가 오늘일 때만 공식값, 아니면 DB 프록시 |
| 사소 | `is_rolled_back_today`가 빈 마커+.lock 생성 / weekly_sanity가 롤백일을 누락 영업일로 오판 / `20270717`(토) 무의미 / stale 주석 / change 단위 | 전부 반영 |
- 실측 근거: 정상 연속거래일 동일종가 비율 9~14%(43쌍) vs 임계 98% → 오탐 마진 7배. `tot ≥ 100` 최소행 가드.

## C. 최종 설계
- `collect_daily` 말미: `_detect_holiday_duplicate(date)`(read, thread) → 복제면 `_kis_confirms_trading_day(date)` → 캔들 없으면 `_rollback_holiday_duplicate`(락 안 DELETE) → 마커 `data/holiday_rollback.json` `{YYYYMMDD: {deleted, same_pct, at}}` → 보고서 `skipped=True, reason="holiday_duplicate_rollback"`.
- `daily_collect_job`: 롤백 시 1회 "📅 복제 감지 — 휴장일 추정, n행 롤백. 휴장일이면 목록 등록" / 캔들-거래일 시 "⚠️ 복제 감지지만 거래일 — 수집 이상 의심".
- `daily_collect_sanity_check`: `_is_kr_trading_day("YYYYMMDD")` False 또는 `is_rolled_back_today` True면 스킵(재수집·알림 0). `weekly_sanity_check`: 마커 날짜 제외.
- 휴장일 목록: +20261005, +20261231, −20270717(토, 대체휴일 여부 확인 후 20270719 검토).
- `get_macro_external.thresholds` US10Y-2Y: Treasury 공식 스프레드(1주 변화 %p) 우선, 폴백 2YY=F 프록시.
- `test_regime.py`: autouse fixture로 `kis_api.regime.REGIME_STATE_FILE` 강제 tmp 격리(미데코 테스트가 프로덕션 상태파일에 쓰는 함정 차단).

## D. 검증·배포 (2026-09-04)
- **게이트**: dev → Opus 리뷰(blocker 1·중요 4·사소 7) → dev 2라운드 → verifier **APPROVE 12/12**(독립 프로브: 판정 5분기·캔들 확증 3분기·마커/sanity/weekly 스킵·알림 plain·`collect_daily`↔잡 e2e 실경로·PIT 가드·격리). 전체 **1024 passed / 25 skipped**, 신규 `tests/test_holiday_rollback_guard.py` 31.
- **휴장일 델타(HEAD 대비)**: +20260717(사고일, WIP분) +20261005 +20261231 / −0 (`20270717`은 HEAD에 없던 WIP 항목이라 제거로 상쇄).
- **커밋**: 가드 본체 + 스프레드/격리 2건(아래 로그). 재시작 후 라이브.
- **후속(비차단, 별도 처리)**: ① `tests/test_mcp_dispatch.py::test_tool_invokable[get_regime]`가 실제 핸들러를 호출해 **전체 pytest가 프로덕션 `data/regime_state.json`을 씀**(기존 결함, 바이섹트 확정) → `tests/conftest.py` autouse로 `kis_api.regime.REGIME_STATE_FILE` tmp 강제. ② 롤백 마커 영구·미정리 — KIS 장애로 캔들 확증 실패 시 실거래일이 조용히 영구 결손될 수 있음 → 마커 N일 만료 또는 weekly_sanity 메시지에 마커 날짜 표기. ③ `stock_master`는 롤백 대상 아님(가격·날짜 컬럼 없어 무해, 주석만).
- **후속 커밋(같은 날 저녁)**: ① 루트 `conftest.py` autouse로 `REGIME_STATE_FILE` 값 복사 소비자 7곳(kis_api.regime·main_pkg._ctx·jobs.regime·jobs.watch_change·telegram_bot·jobs.sunday·jobs.kr_summary) tmp 강제 — 전체 스위트 실행 중 `data/*.json` 41개 sha256 불변 실증(toss_sync 봇 쓰기만 귀속). `tests/test_read_file_chunked.py`가 프로덕션 `data/`에 임시파일 생성하던 것도 `files.py` `_BOT_ROOT` 상수(값 동일, 경로탈출 가드 5케이스 무회귀) 패치로 제거. ② 마커 쓰기측 30일 prune + `isinstance(dict)` 가드. ③ weekly_sanity 📅 라인에 등록 안내 + `parse_mode=None`, 새 테스트는 백필 AsyncMock·UNIVERSE_FILE tmp로 KIS 노출 차단. 전체 1032 passed / 25 skipped, verifier 재검증 통과.
