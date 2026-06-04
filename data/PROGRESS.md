## 📄 2026-06-04 db_collector div_yield/foreign 침묵-0 회귀 + div_yield KIS-DPS 전환 (KRX 독립화)

**발단**: 알파 연구 중 `daily_snapshot`의 두 필드 회귀 발견 — **div_yield** 2026-04-08부터 전종목 0, **foreign_net_amt** 05-07부터 간헐 0. 둘 다 upstream 실패를 **0으로 침묵 기록**(fetch 실패 ≠ 실제 0 구분 불가).

**근본 원인**:
- div_yield: v1(JSON)→v2(SQLite) 전환 때 "별도 계산" 보강 미포팅. `_store_daily_snapshot`가 0.0 하드코딩 + 갱신 코드 부재 → **애초에 수집 안 함**. (04-07까지 1282는 마이그된 v1 데이터)
- foreign_net_amt: ① insert가 amt=0 하드코딩(주석 "히스토리 API에 금액 없음"은 **오류** — Phase-3 KIS `FHPTJ04160001`이 `frgn_ntby_tr_pbmn`(금액) 이미 반환, 매핑만 누락) ② 실 amt 소스 `_update_supply_in_snapshot`(pykrx)가 KRX soft-block 실패를 침묵(retry 무). KRX 포털 스크래핑은 세션 내내 다운(간헐적).

**수정 (브랜치 `fix/collector-div-yield-foreign-amt`, 커밋 8개 push)**:
- **foreign**: KIS `*_ntby_tr_pbmn×1e6`(원, 기존 pykrx값과 0.3~1.1% 일치)을 1차 소스로. 누락 시 **NULL(0 아님)**. pykrx는 retry+never-zero refiner로 강등. 전 구간(04-08~06-02)+05-08 데이터 KIS 복구(~92k행).
- **div_yield → ★KIS 예탁원 DPS 기반 전환 (KRX 완전 독립)★**: 신규 `dividend_events` 테이블 + `collect_dividends()`(예탁원 HHKDB669102C0 종목별 현금 DPS, 일요일 07:20 주간잡) + `_recompute_div_yield_from_events()`(`div_yield = 직전12M DPS÷종가`, collect_daily 6c 매일 재계산, 무 API, 비파괴). 종전 pykrx div 경로 3함수 제거. **전종목 2864→payers 1373, events 1845, 04-08~06-02 ~1360종/일 채움. 신호 z=+1.80**(random 98% 상회).
- **휴장일 가드**: collect_daily/backfill에 `_is_kr_trading_day` 추가(휴장일 평일에 KIS 직전영업일 시세로 spurious 행 생기던 것 차단). 기존 spurious 8,586행 삭제(04-12일·05-05·05-25, KIS chk-holiday 권위 확인). 휴장일 list telegram_bot→db_collector 단일화.
- **부수**: 05-08 부분일(600→2768행 완성) · 백필 insert 금액0버그 fix · **pyarrow 설치**(누락으로 `divyield_reconstruct.py` sidecar/probe 침묵 실패하던 것 복구, requirements 등록) · 토큰 `.git/config`평문→`~/.git-credentials`(600) 이동.

**핵심 교훈**:
- **div_yield는 KRX 불필요** — KIS 예탁원 DPS÷종가로 매일 산출(KRX feed 죽어도 무관). 사용자 `RALPH/divyield_reconstruct.py`(DPS 역산, no-API)도 같은 원리, 검증됨(+0.34, deployable). [[KRX 스크래핑 의존 금지]]
- "히스토리 API에 금액 없음" 같은 **주석 가정 검증 필수** — 실제로 KIS output2에 `*_ntby_tr_pbmn` 존재했음.
- 침묵-0 안티패턴: **fetch 실패는 NULL+retry+가시화**, 실제 0과 구분.

**브랜치 주의**: `fix/collector-div-yield-foreign-amt`는 dashboard 재구축·test 스위트 작업과 **공유 중**(동시 세션). main 머지 전 내용 확인 필수.

**남은 것 (사용자 스킵 결정)**: 🔐 토큰 키체인 암호화(헤드리스 차단 확정)·교체(gh 미로그인 대화형) — 둘 다 선택, 봇 정상. 메모리 [[DB research]] "div_yield/foreign 배포 불가" 차단 **해제됨**.

---

## 📄 2026-06-04 테스트 스위트 분할 회귀 소탕 (사용자 "test_mcp_schema 7개 실패" → 옵션1 확장)

**발단**: `test_mcp_schema.py` 7개 실패 — 헬퍼가 `open("mcp_tools.py")` 하는데 5/27 분할로 그 flat 파일이 사라짐. collection abort(6 error)에 가려져 **조용히 stale-fail 중**이던 분할 회귀. ([[package-refactor-stale-docs]])

**요청 범위 (reviewer+verifier Opus 통과)**:
- `test_mcp_schema.py` **7/7** — flat 파일 regex 스크래핑 → `MCP_TOOLS`(__init__) / `TOOL_HANDLERS`(_registry) **직접 import** + 핸들러 서브모드는 `mcp_tools/tools/*.py` **AST 파싱**으로 추출. elif 체인→dispatch dict 구조 반영. mutation-test로 non-vacuous 확인(팬텀 enum→FAIL).
- `test_mcp_consolidation.py` **41/41** — stale `@patch("mcp_tools.X")` → `kis_api.get_kis_token`(토큰은 _registry 지역 import) + `mcp_tools.tools.<mod>.X`(핸들러가 `from kis_api import *`로 바인딩).
- **collection error 6→0** (610 collect): test_backtest/regime/us_features/phase_b import 복구(심볼이 kis_api 서브모듈로 이동: SUPPLY_HISTORY_FILE/_REGIME_ORDER/US_SECTOR_ETFS, KR 감성 키워드 미사용 삭제).
- **obsolete 2개 → module-level skip**(되돌리기 쉬움): `test_krx_otp`(krx_update 모듈 삭제·db_collector로 이동·OTP 방식 폐기), `test_consensus_ci`(get_hankyung_consensus 제거, 한경→FnGuide; def test_ 없는 라이브 텔레그램 스크립트).

**옵션1 확장 — stale-path 소탕 (사용자 "니추천대로 진행"; 2 병렬 dev + reviewer + verifier)**:
- 완전 green: test_backtest **18**, test_us_features **24**, test_us_ratings **13**, test_data_extension **14**, test_sector_flow_cache **12**, test_schedule_registration **1**(← `main.py` shim 아닌 `main_pkg/schedule.py` 읽도록 경로 교정, 등록 라인 제거 시 FAIL 확인=non-vacuous).
- stale-path 수정·잔여는 behavioral: test_regime, test_edge_cases(23/3, reviewer 지적 토큰타깃 `kis_api.get_kis_token` 교정으로 +1), test_phase_b(SimulateTrade), test_scan_presets, test_report(dead `report_crawler.load_reports` patch 제거).
- 패턴: `mcp_tools.X`→`mcp_tools.tools.<mod>.X`, `kis_api.X`(이동분 aiohttp/US_*_FILE)→서브모듈, `from main import X`→`main_pkg.*`(telegram_bot·_entry·_ctx·jobs.stoploss).

**결과**: 175 failed → **111 failed / 497 passed / 2 skipped** (64 해소). 회귀 0(test_undefined_names 단독 통과 확인).

옵션1 후 **175→111 fail**. 사용자 "니추천대로 다해" → 옵션2까지 전부 진행:

**옵션2 — async 인프라 + behavioral + 격리 (Wave A/B, 4 병렬 dev + reviewer + verifier)**:
- **Wave A: `pytest-asyncio` 1.4.0 venv 설치 + `pytest.ini asyncio_mode=auto`** → "async def not supported" ~82개 해제. test_tool_fallbacks 5·test_api_limits 12·test_phase_a 28 완전 green. 나머지 async 파일은 동일 stale-`@patch`로 드러나 레시피 적용(get_kis_token→kis_api, mcp_tools.X→tools.<mod>, kis_api.X→서브모듈, from main→main_pkg).
- **라이브 마커 인프라**: `conftest.py`에 `--run-live` 옵션 + 기본 skip(`pytest_collection_modifyitems`), `pytest.ini` 마커 등록. pytest-asyncio가 라이브 KRX/DB 통합 테스트를 실제 실행시켜 hang 유발 → **15개 `@pytest.mark.live`** 마킹(KRX 네트워크 5·report DB 7·scanner DB 2 등; 기본 skip, `pytest --run-live`로 실행). bot 기존 관례.
- **Wave B 잔여 stale-namespace/behavioral/격리**: test_dart_report 17(DART_REPORTS_DIR→`kis_api.dart`)·test_phase_b rotation 2(→`kis_api.kr_stock`)·test_regime 47(라벨 위기→공포·공격→탐욕 코드 검증 후 갱신, REGIME_STATE_FILE 격리, _yf_history→kis_api.news)·test_edge_cases 26(토큰 파일캐시 격리)·test_keyboard 29(cmd 출력 신규동작 갱신 + telegram stub cross-file 오염 = main_pkg purge+reimport로 해결)·test_report 24(extract_pdf_text 3-tuple). reviewer가 동작-갱신 단언을 실제 구현과 전수 대조(rubber-stamp 회귀 0).

**최종 결과 (full suite, 기본 live-skip)**: **595 passed / 17 skipped / 0 failed**. 회귀 0. (시작: collection 불가·175 숨은 실패 → 0). 마지막 3건도 해결:
- `test_mixed_sentiment`: 코드 갭 아님 — `_FINANCE_PHRASE_SCORES`는 의도적 큐레이션(컨텍스트 반전 "공매도 감소"=+3, bare "상승/급등" 모호하므로 제외). 옛 naive-keyword 기대가 stale → 입력을 큐레이션 긍정구문("사상 최대 어닝서프라이즈…하지만 우려도", +9)으로 교체해 "mixed→net positive" 의도 보존. **알고리즘 미수정**.
- supply 2개: 사용자 foreign_rank DB-first에 맞춰 `@patch("sqlite3.connect", side_effect=Exception)`로 DB 비활성→KIS-live 폴백 경로 테스트(SQL 비커플링 → 사용자 추가 편집에 강건). dict shape(`source:"KIS live"`) 단언.

**⚠️ 동시 세션 충돌 감지**: 작업 막바지(22:43) 사용자가 같은 브랜치에서 `mcp_tools/tools/supply.py`(foreign_rank → **DB-first** daily_snapshot, KIS는 폴백)·`kis_api/polymarket.py`(+163)·`dashboard_home.py` 동시 편집(이 PROGRESS 상단 db_collector 항목도 사용자 추가). **내 에이전트는 프로덕션 미수정 — revert 안 함**. 단 사용자의 foreign_rank DB-first 변경이 내가 고친 테스트 2개를 깸: `test_mcp_consolidation::TestGetSupply::test_foreign_rank`(mock_frgn.assert_called_once — DB경로가 KIS API 우회), `test_tool_fallbacks::test_get_supply_foreign_rank_empty_returns_note`("note" 미반환). → **해결**: 두 테스트를 DB-first 폴백 경로로 갱신(위 참조). SQL에 커플링 안 했으므로 사용자가 foreign_rank 쿼리 더 바꿔도 대체로 견딤. **supply.py/polymarket.py/dashboard_home.py는 사용자 작업 — 손대지 않음.**

**git**: 전부 **미커밋**(test_*.py 22개 + conftest.py + pytest.ini + PROGRESS.md = 내 작업; supply.py/polymarket.py/dashboard_home.py = 사용자). pytest-asyncio는 venv 설치만(requirements.txt에 pytest 자체도 없는 기존 관례 따름). .claude/rules·CLAUDE.md 미수정(stale flat 경로 잔존하나 보호 파일 — 플래그).

**docs 갱신 완료** (사용자 "다 해" 승인): CLAUDE.md(파일구조표 flat→패키지·MCP 47개·dispatch dict), `.claude/rules/add-mcp-tool.md`(절차 재작성: __init__ MCP_TOOLS→tools/<mod> 핸들러→_registry dict), `mcp-tools.md`(47), `file-structure.md`(stale 경고 헤더). 정책/워크플로 섹션은 미수정.

**git**: 전부 **미커밋**. 내 작업(test 22개+conftest+pytest.ini+CLAUDE.md+rules 3개+PROGRESS) 과 사용자 WIP(supply/polymarket/dashboard_home)가 같은 브랜치 작업트리에 혼재 → 커밋 경계는 사용자 판단(자동 커밋 안 함). pytest-asyncio는 venv 설치만.

**다음 세션**: 본 테스트 작업 완료. 잔여 = 전부 사용자 영역(① main 머지 타이밍 ② 사용자 collector/dashboard WIP 마무리). 새 MCP 도구 추가 시 갱신된 add-mcp-tool.md 따를 것.

---

## 📄 2026-06-03 대시보드 바닥부터 재구축 (사용자 "데쉬보드 너무 별로지 않냐")

기존 `/dash`(dashboard.py 3700줄 string-concat 누적물, 10섹션·11탭 오버플로·데스크탑 여백·조용히 썩음)를 비판 → **새 `/home` 대시보드를 나란히 신축**. **브랜치 `fix/collector-div-yield-foreign-amt`** 에서 작업(세션 중 사용자가 collector/US_EXIT 작업으로 이 브랜치 사용 중인 것 발견 — [[deploy-main-직행]] 참고, 커밋 전 git branch 확인 교훈).

**스택**: 무빌드 — Tailwind+Alpine+Lucide+Pretendard CDN, 서버는 JSON API만(`dashboard_home.py`), 표현은 Alpine 클라 렌더. **핵심 축**: MCP 핸들러(`execute_tool`)가 곧 데이터 계층(90% 래핑). `dashboard.py` **0줄 수정**(회귀 안전장치, /dash 그대로 fallback).

**완성 (P0~P4 + 시그널, 전부 라이브 검증·커밋·브랜치 푸시):**
- P0 골격(aa4318b): 셸+탭7+Alpine. P1 홈(4518ba7): /api/home 집계(부분실패 허용·TTL), 레짐배지+자산요약+신호카드. reviewer 지적 반영(W1 가짜 neutral 금지/W4 이벤트 regex/I1 손절 부호/W2 캐시누수/W3 컨센서스 노이즈).
- P2 포트·워치(6e6f4d2): /api/portfolio(원화환산 grand)·/api/watch(stoploss_alerts 실값)·POST. 정렬 pill·종목 모달. TTL240s+stale-while-revalidate.
- P3 Whale·리포트·기록(ec25f74): build_whale_payload(5서브탭)·build_reports_payload(4세그 KR101/US0/산업898/시황934)·기록(투자판단 레짐배지/매매/투자TODO). reports async await 버그 수정.
- P4 신호 영속화(59468d0): kis_api append_signal/load_signal_feed(SIGNAL_FEED_FILE), momentum.py/anomaly.py에 **추가-only 훅**(dedup·텔레그램 발송 불변, try/except 래핑). /api/signals.
- 시그널 탭(7dc4ff0): 5서브탭(신호피드/임박이벤트/발굴/DART/컨센서스). 라이브 이상급등 3건 영속화 end-to-end 확인.

**7탭 전부 라이브 OK**: 홈/포트폴리오/워치·알림/시그널/기록/Whale/리포트. 콘솔 0.

**시세 차원 추가 (4e53b72/69251f2, "현재가 관련이 하나도 없냐" 지적)**: 홈 지수 띠(KOSPI/KOSDAQ/S&P/나스닥) + 📈시세 탭(지수·등락률 상하위·거래량·종목 시세조회). KR 등락은 KIS 장중 미제공이라 daily_snapshot 종가 기준. 현재가 "-" 시 종가 폴백+"종가" 뱃지. **탭 8개**(홈/시세/포트/워치/시그널/기록/Whale/리포트).

**P5 컷오버 완료 (242a2c7)**: `/dash`·`/dash-v2`→302 `/home`, `/dash-classic`→옛 `_handle_dash_v2` 보존. **미들웨어 아닌 라우트 교체**(register_routes 국소, 전역영향 회피). /dash 서브패스(whale/reports/pdf/file/decisions/trades/todo)·/api/*·/mcp·/health 전부 보존 라이브 확인. 롤백=3줄 revert.

**폴리시 완료 (b98dba8)**: 기록 탭 페이지네이션(최근20+더보기) + 홈 DART 카드 누적라벨 정리. 탭키 단/복수 버그(signals→signal) 픽스.

### 후속 빌드 (06-03~04, 사용자 "남들 사례 찾아 추가/보강/최적화" → "쭉쭉 진행")
연구(OpenStock/Tremor/AddyOsmani 멀티에이전트/VoltAgent 등) → 우리 적용. **전부 라이브 검증·커밋·브랜치 푸시.**
- **웹디자인 팀 신설** (`.claude/agents/`: ui-ux-designer/frontend-developer/design-reviewer, CLAUDE.md Agent Team 등록 b7e889c). 워크플로 architect→designer→frontend-dev→design-reviewer→verifier→메인 라이브확인. **실제 가동**(설계 spec→구현→리뷰), mount 타이밍 버그는 메인세션 라이브에서 포착·수정("검증이 병목" 입증).
- **차트** (d36cd8f/60025e6): TradingView lightweight-charts(무빌드 CDN). 자산추이(area)·종목 캔들+거래량. mount 타이밍 fix(탭/모달 가시화 후 + rAF 0-width 가드).
- **콜드로드 최적화** (0cb46b8): `_cached` 서버측 SWR(stale 즉시+백그라운드 갱신) + `warm_caches()` 시작 프리워밍. **홈/포트/워치 30s→0.002s.** (US후보·매크로 등 무거운 lazy 엔드포인트는 미프리워밍 → 첫 로드 ~40s, 백로그.)
- **스켈레톤** (60f1e03): 11표면 "로딩중"텍스트→animate-pulse.
- **히트맵** (214b2c0): 포트폴리오 손익(flex-grow=평가액,색=손익%) + KR 섹터(78개, /api/sector_heatmap, daily_snapshot 집계).
- **US 애널 탭** (1a38513): 9번째 탭. 매수후보(get_us_buy_candidates)·레이팅변화(get_us_scan)·톱애널·종목 리서치 모달. 26k행 자산 표면화.
- **매크로** (5c3fc11): 시세 탭 pill. /api/macro_panel(get_macro+external+polymarket+sector gather). 레짐배너·지표·Fed·섹터로테이션. (curve/침체확률은 get_macro_external 키 불일치로 비어있음 — 백로그.)
- **알파스크리너+수급** (7a766f1): 시그널 탭 알파스크리너(F/M-Score·FCF·52주, /api/alpha) + 시세 탭 수급(외인/공매도/신용, /api/supply). 둘 다 pill로 흡수(탭 9개 유지). M-Score는 데이터 수집 전(graceful).

**현재 = 9탭 풀 리서치/시그널 콕핏.** 무빌드 Tailwind+Alpine+Lucide+lightweight-charts. dashboard.py(옛 /dash-classic) 전 과정 무수정.

**🟡 남은 것 (사용자 결정 필요 — 내가 안 함)**:
1. **브랜치 → main 머지**: 대시보드+collector 전부 `fix/collector-div-yield-foreign-amt`에 있고 라이브, main(`8c9f70b`)엔 없음. 사용자 판단.
2. **CLAUDE.md 인프라표 URL `/dash`→`/home`** (파일구조/팀은 사용자가 이미 갱신함, 인프라표 URL만 남음): 보호 파일, 명시 동의 후.

**🔧 폴리시 백로그 (선택)**:
- 무거운 엔드포인트(US후보·macro_panel) 프리워밍 추가 → 첫 로드 즉시화.
- 매크로 국채곡선/침체확률(get_macro_external 반환 키 파싱) + M-Score(데이터 수집 전) 보강.
- 접근성(aria/명암대비/키보드).

---

## 📋 2026-06-01 세션 종료 핸드오프 (컴팩트 직전)

### 이번 세션(5/29~6/1) 성과 — 분할 회귀 소탕 + PDF 가독성
1. **분할 회귀 25건 수정** (main.py→main_pkg, kis_api 5/27 분할 잔재): 1차 NameError 14건(a072451) + 2차 11건(7da09f9). `tests/test_undefined_names.py` 바이트코드 LOAD_GLOBAL 가드 신설(83e754d).
2. **MCP 도구 전수검사 하니스** `_test_all_tools.py` (tracked): 47도구 실호출 → NULL_STUB=0, EXCEPTION=0 확정. youtube stub 발견·수정(aca69cc).
3. **pension/sec 2버그** — 오진이었음(하니스 시그니처 착오), reviewer가 차단. 코드 멀쩡, 가짜 수정 미커밋.
4. **5/27·28 daily_snapshot 백필** — `collect_daily_backfill()` (d5354f2). KRX historical+KIS history-with-date. 2766/2766행. KIS 현재가 미사용으로 corruption 회피.
5. **consensus not_rated** 분류(9963258): TP=0 Not Rated를 sell 오분류 → not_rated 신설.
6. **read_report_pdf 대수술** (핵심):
   - mode=text/pdf 추가(ce0c3f1). PDF 원본 전송은 claude.ai 미지원 확정(실측 -32602 거부 + GitHub csharp-sdk#1261).
   - image 모드 적응형 합치기 → 최종 **최대 2합치기**(2aa99c2): ≤50p=1p/img, 51p+=2p/img, dpi150, _MAX_EDGE=3000, _MAX_IMAGES=50(=100p 커버), _MAX_BYTES=33MB. 페이지당 550→1075px(2배). **육안+Claude 판독으로 표 숫자 읽힘 확인**(4합치기는 못 읽었음).

### git 상태
- 코드 전부 커밋·push 완료. 최신 c6924e8(KR_EXIT 리서치) / 봇 코드 최신 2aa99c2.
- untracked = 런타임 .lock/아티팩트 + 리서치 .md(US_EXIT/research_log) + `_split_kis_api.py`(1회용)뿐. **커밋 불필요.**
- `data/reports.db`(0바이트 고아) 삭제 + .gitignore에 `data/*.db` 패턴 추가.
- 봇 health 200, launchd 정상.

### 다음 세션 할 일 (우선순위)
1. **SEC EDGAR Phase 2** — sec_polling 잡(10분) + 8-K/EFFECT 텔레그램 알림 (Phase 1만 완료됨)
2. `_split_kis_api.py` 삭제 검토(리팩토링 끝난 1회용)
3. backtest.py US 캔들 동일 버그(#6, out-of-scope로 남음)
4. read_report_pdf: 100p+ 리포트는 next_pages로 2회 호출 필요 — 자주 쓰면 UX 개선 검토
5. KR_EXIT/US_EXIT 리서치 산출물 기반 실행은 **사용자 직접 결정 영역**

---

## 📄 2026-06-02 리포트 탭 재구조화 (사용자 "이러면 뭐가뭔지 어케알아")

### 문제
`/dash#reports` 가 `GROUP BY ticker` 단일 doc-card 그리드 → **카드 1,863개(빈 카드 1,455개)**. 원인 3겹:
1. 비종목 리포트(industry/market/strategy/economy 1,762건)가 PDF URL당 `_IND_<sha1>`/`_MKT_`/`_STR_`/`_ECO_` 합성 ticker라 PDF 1건=카드 1개. market/strategy/economy는 name='' → 빈 카드.
2. company KR 101 ticker가 GROUP BY 비결정성으로 메모성 name(`삼성전자 추매`, `LS ELECTRIC Bear Case`)이 라벨로 샐 위험.
3. 미국 종목 리포트는 0건(수집 안 됨).

### 구현 (dashboard.py만 수정, 새 라우트/스케줄/MCP 없음)
`#reports` 섹션을 **세그먼트 4개**로 분리(JS show/hide 토글, `location.reload` 전체 리로드라 바인딩 재부착 안전):
- 🇰🇷 한국 종목: `category='company' AND ticker GLOB '[0-9]*'`, GROUP BY ticker, doc-card. 카드명 `COALESCE(sm.name, NULLIF(r.name,''), r.ticker)` (stock_master LEFT JOIN) → 정식 종목명 결정화.
- 🇺🇸 미국 종목: `ticker GLOB '[A-Za-z]*'`, 동일. 현재 0건 → 안내문.
- 🏭 산업: `category='industry'`, 날짜그룹 리스트(섹터태그+제목+증권사+PDF), `ORDER BY date DESC, id DESC LIMIT 200`.
- 🌐 시황·전략: `category IN ('market','strategy','economy','bond')`, 동일 날짜 리스트(카테고리태그).
- PDF: `pdf_path` 있을 때 기존 `/dash/pdf/{ticker}/{basename}` 재사용(합성 ticker도 `report_pdfs/{ticker}/` 실존 확인, 표본 20/20).
- CSS 11줄(`.rpt-seg*`/`.rpt-list*`), JS 11줄(세그 토글) 추가.

### reviewer(Opus)/verifier(Opus)
- reviewer: blocker 0. 권고 A(카드명 COALESCE)+B(tiebreak)+C(try/finally close 보장)+D(LIMIT ? 바인딩) 전부 반영.
- verifier: 8/8 기준 증거 기반 PASS. ast OK, venv import OK, 4쿼리 실측(KR101/US0/IND861→200/MSE901→200), 카드명 정식명 확정, PDF 20/20 실존, escape 전수, `/dash/reports`·`/dash/pdf` 핸들러·라우트 무회귀 확인. **배포 가능.**

### 비고
- 산업 861건 중 554건이 섹터 빈칸 → "섹터 폴더"보다 날짜별이 실제로 나음(사용자 직관 적중).
- 후속 후보: 미국 종목 리포트 수집 파이프라인(현재 0건), 날짜 리스트 페이지네이션(현재 최근 200건 캡).

### 후속 — 4주 잠복 JS SyntaxError 발견·수정 (사용자 "글릭이 안대")
세그먼트 클릭이 안 된다는 제보 → 라이브 콘솔 진단 결과 `dash:613 SyntaxError: Invalid or unexpected token`. 근본원인: `_dash_v2_js()`가 파이썬 삼중따옴표 문자열인데 951행 `confirm('...했습니다.\n페이지...')`의 `\n`이 **실제 개행**으로 렌더 → JS 작은따옴표 문자열이 줄바꿈을 넘겨 `<script>` 블록 **전체 파싱 실패**. **5/5 분리 커밋(f93abb6)부터 ~4주간 대시보드 v2 JS 전부 사망**(탭 하이라이트/자동갱신("갱신: -" 고정)/워치필터/포트정렬/TODO토글/투자판단폼/리포트 세그먼트토글 전부). 수정: `\n`→`\\n` 1글자(630b40a). `node --check`로 렌더 JS 전수 검증 에러 0, 라이브 재확인(토글 동작 + refresh-time 갱신 + 콘솔 0). **교훈: 파이썬 삼중따옴표로 JS 생성 시 JS 문자열 리터럴 안 `\n`/`\t` 등은 `\\n`으로 이스케이프 필수.**

### 후속 — 대시보드 전체 점검 (사용자 "점검 빡세게") → Pass1+2
라이브 진단(브라우저 JS 프로브) + 정적감사(코드, 백그라운드) + 라우트 헬스 병렬 수행.

**Pass1 — 기능 버그 7건 (커밋 a9bd7cf):**
1. 감시종목 섹션 사망 — `_render_row`의 `escape(info.get("grade",""))`가 watchalert.json의 `grade:null`(달바글로벌 483650·LG 003550)에 `escape(None)` → AttributeError → 섹션 전체 "로드 실패". `info.get("grade") or ""` 가드. → **99종목 부활**.
2. Whale kr_5pct 카드 NaN/undefined — JS가 dict에 없는 필드 4개(stkqy/stkqy_irds/repror/report_resn) 참조 → UI 줄 제거.
3. 레짐 뱃지 항상 neutral — 판별이 강세/약세만 봐서 폼값 공격/경계/위기 미매칭. 공격→bull, 위기→bear 매핑(메인 1941 + decisions 3856).
4. items_json `</script>` breakout(whale 2곳) — `json.dumps`가 `/` 미이스케이프 → `<>&`→유니코드. (951행 \n버그와 동일 클래스 잠복폭탄.)
5. reports 섹션 예외 시 `<div>` 미닫힘 → 본문을 지역변수 조립 후 무조건 균형 div emit.
6. decision/trade/invest/dev 섹션 except에 "로드 실패" placeholder 통일.
7. research 파일뷰 `<title>/<h1>` filename XSS escape.

**Pass2 — 구조 리팩토링 555줄↓ (커밋 a986464, 동작불변 byte 증명):**
- 죽은 함수 `_build_whale_section_html`(371줄, 호출 0) 삭제, 미사용 `lang` 변수 제거.
- `_md_to_html`/`_md_to_html_editable` 통합(`file_key=None`, shim 유지). SQLite 보일러플레이트→`_open_dash_db()` 헬퍼로 whale 9곳 dedup. (4197→3701줄)

**오탐 정정 2건 (코드만 본 감사의 한계):**
- `/v40` 탭은 404 아님 — cloudflared가 `^/v40`을 **localhost:8765(forward_bot=v40/v988 자동투자봇)**로 라우팅. dashboard.py 라우트에 없을 뿐 공개 URL 정상.
- "죽은 import 3개"는 substring grep이 `_sqlite3`/`_sqlite3_rpt` 매칭한 오탐 — 전부 사용 중.

**미적용(판단):** whale 26곳 `로드 실패: {e}` 예외 노출은 Cloudflare Access 뒤 1인봇이라 본인 디버깅에 유용 → 유지.

각 Pass reviewer(Opus)+verifier(Opus) APPROVE(blocker 0). md SHA 일치·whale byte 동일·node --check 전수·강제예외 div균형 검증. 라이브 재확인: 10섹션 전부 정상, watch 12296자/99행, 4세그먼트, 콘솔 0.

---

## 📄 2026-05-31 read_report_pdf — text/pdf 모드 추가 (사용자 "PDF 100장 제한·원본 전송 가능?")

### 문제
claude.ai 클라이언트가 **채팅당 이미지 100개 하드 제한**. read_report_pdf 가 PDF를 페이지마다 PNG로 변환 → 100p+ 리포트는 "이미지 100개 도달" 에러.

### 조사 결론 (1차 출처)
- MCP 스펙: tool result content = text/image/audio/resource(embedded)/resource_link. PDF는 EmbeddedResource(application/pdf base64)로 담을 수 있음.
- **그러나 claude.ai 가 PDF EmbeddedResource 를 문서로 렌더 안 함** (GitHub modelcontextprotocol/csharp-sdk#1261 "BAD IMAGE", 미해결). MCP→claude.ai 로 PDF 원본 직접 전송은 현재 불가.
- arxiv-mcp/pdf-mcp 등 **잘 되는 실전 패턴은 전부 "텍스트 추출 후 text 블록 반환"** (read_paper=markdown). → text 모드가 현실적 정답.

### 구현 (commit ce0c3f1)
read_report_pdf 에 mode 파라미터:
- `mode=image` (기본, 하위호환 100%): 기존 페이지→PNG. meta.mode 키만 추가.
- `mode=text` (신규·권장): fitz 텍스트 전페이지 추출 → text 블록. **이미지 0, 페이지 무제한.** 라이브 확인: 005930 11p 텍스트 정상.
- `mode=pdf` (신규·실험): PDF 원본 EmbeddedResource(application/pdf). 25MB 상한 가드. claude.ai 렌더 여부 사용자 검증 필요(미지원 가능성 높음).
- 신규 헬퍼 `_extract_pdf_text`/`_embed_pdf_resource` (_helpers.py). 스키마 mode enum 추가.

### ⚠️ text 모드 한계 (중요)
삼성전자류 리포트는 **차트·표를 이미지로 박아** 텍스트 추출 시 페이지당 80~110자뿐(11217: 총1062자). 반면 산업/전략 리포트는 텍스트 풍부(9천~1만7천자, 샘플30개중 28개 1500자+). meta.char_count 로 "이 PDF는 text로 안 잡힘(차트형)" 판별 가능 → 그런 경우만 image 모드 폴백. **종목별 리포트 성격에 따라 mode 선택 필요.**

### reviewer/verifier
verifier 8/8 APPROVE, reviewer 권고(pdf 25MB 가드) 반영 후 커밋. 봇 재시작 health 200, 라이브 MCP text 모드 작동 확인.

---

## 🐛 2026-05-30~31 MCP 도구 점검 — youtube stub 수정 + 47도구 전수검사

### youtube stub (실버그, commit aca69cc)
`handle_get_youtube_transcript` 가 mcp_tools.py→패키지 분할(904fe12, 5/27) 때 URL 파싱만 하고 `fetch_youtube_transcript()` 호출 `else:` 분기가 통째로 누락 → 항상 `null` 반환. 클라이언트에서 "유튜브 요약 안 됨". 원본 mcp_tools.py:4667 `asyncio.to_thread` 로직 복구. 라이브 MCP 재호출로 721줄/13042자 자막 정상 확인.

### 47도구 전수검사 결과: 다른 stub 없음
`_test_all_tools.py` (repo루트, tracked) — 47개 도구를 실제 `_execute_tool(name, args)` 로 전수 호출. **OK=38 / ERROR=8 / NULL_STUB=0 / EXCEPTION=0 / SKIP=1(git_push)**.
- NULL_STUB=0 → youtube 같은 stub 핸들러 더 없음 (사용자 "다른 애들도 되는거 맞아?" 답: 전부 정상)
- ERROR=8 전부 정상 방어: 무인자 호출(set_alert/write_file/git_commit/manage_watch/simulate_trade/read_report_pdf/watch_analyst) + FMP 402(FMP_API_KEY 미설정, 코드버그 아님)

### ⚠️ 내 오진 1건 (기록 — 같은 실수 방지)
처음에 하니스가 `_execute_tool(name, args, **token**)` 3인자로 호출 → 47개 전부 TypeError → "pension/sec 버그"로 오진하고 팀 호출. **reviewer 가 차단**: "_fetch_watchlist_flows/SEC_DB_PATH 심볼 자체가 코드에 없음, 변경 대상 부재". 실제 원인은 `_execute_tool` 가 2인자(token 자동감지, 어제 수정됨)인데 하니스가 구 시그니처 사용. 하니스 token 인자 제거로 해결. **pension/sec 코드는 멀쩡, 커밋된 가짜 수정 없음.** → 교훈: 전수검사 도구 자체의 시그니처부터 1개 도구로 검증 후 전체 돌릴 것.

---

## 🚨 2026-05-29 전체 회귀 테스트 — 분할 회귀 25건 발견·수정 (사용자 "전체 테스트" 지시)

### 발견 경위
사용자 "전체적으로 문제없는지 테스트" → 봇 로그 스캔에서 NameError 다수 발견. main.py→main_pkg(5/27) + kis_api(5/27) 분할 시 **모듈전역이 타 모듈로 안 넘어가 런타임 NameError**. 모듈 import는 통과해 기존 AST import 테스트(test_module_imports)가 못 잡음. 다수는 try/except에 삼켜져 로그에도 안 떠 숨어있었음.

### 데이터 영향 (실측)
- DB daily_snapshot 최신 **5/26** — daily_collect_job이 `_HAS_DB_COLLECTOR` NameError로 즉사 → **5/27·5/28 시장데이터(~2864종목/일) 미수집**. 자가진단 잡도 동일 버그라 자동복구 실패.
- `fetch_polymarket()` → `NameError: POLYMARKET_API` 실증. get_macro_external/get_polymarket 응답 `{"error":...}` 확인.

### 수정 — 2 wave, 25건
**Wave1 (14건, commit a072451)** main_pkg 분할 회귀:
- `_HAS_DB_COLLECTOR`+collect_daily/collect_financial_weekly (collect/financial/dart_inc)
- `_REPORT_AVAILABLE`+collect_reports/get_collection_tickers/collect_market_reports/REPORT_DB_PATH (reports/dart_inc/events/consensus/pension)
- load_krx_db (sunday/watch_change), CHANGE_SCAN_SENT_FILE (change_scan), INSIDER_* 3종 (insider)
- SILENT_FAILURE_LOG (_ctx), import sys (_entry), _refresh_ws+regime_cur+manual_summary import (telegram_bot)

**Wave2 (11건, commit 7da09f9)** 바이트코드 LOAD_GLOBAL 검출기로 발견:
- GroupA(3): anomaly/momentum `global` dict 모듈레벨 미초기화 → `={}` 추가
- GroupB(7): kis_api 상수 분산 cross-import — polymarket←fmp(POLYMARKET_API/FRED_BASE/_POLY_NOISE_TAGS), pension←polymarket(NPS 3종), fmp←regime(_YT_URL_RE)
- dashboard _GRADE_ORDER 로컬 정의

### 재발 방지 (commit 83e754d)
- **tests/test_undefined_names.py** 신규 — 바이트코드 LOAD_GLOBAL을 모듈 실제 namespace+builtins에 대조. import-only 테스트가 못 잡던 NameError 클래스 영구 차단. comprehension/lambda FP 없음.
- 폐기된 pdf_collectors 테스트 2개 제거(ModuleNotFoundError로 suite 수집 차단하던 stale). 나머지 suite **161 passed**.
- 전 작업 developer→reviewer→critic→verifier. 검출기 6패키지 0건.

### ⚠️ 미해결 — 사용자 결정/후속 필요
1. **5/27·28 daily_snapshot 백필** — ✅ **완료(5/30, 옵션 A→B 재결정)**. 사용자가 "그냥 채우면 안대?" 재질문 → `db_collector.collect_daily_backfill(date_str, kis_history=False)` 신규 구축(commit d5354f2). KRX historical OHLCV + KIS 일봉차트로 안전 채움(KIS 현재가 미사용). 5/27·28 각 2766행, close/market_cap 100%, open/high/low ~60%(KIS 일봉차트 500 부분장애), 수급/공매도/신용/PER 등은 의도적 0. _compute_and_update 5/27~5/30 4일 재계산으로 MA 정합 복구. 027410/006840 잔존 raw원 mcap도 자동 갱신. KIS 수급 엔드포인트(`investor-trade-by-stock-daily`) 5xx 회복 시 `kis_history=True` 재실행 가능(INSERT OR REPLACE 멱등).
2. **2개 1회성 로그 에러** — `_exec_us_ratings() ticker`(현 코드엔 default 있어 stale 추정), `KeyError auto_watched`(data-edge). 현 코드 재현 안 됨 → 비수정.
3. **코딩규칙 권고** — kis_api submodule 역방향 의존 금지(fmp/regime → polymarket/pension). 현재 선형 무순환이나 명문화 시 순환 회귀 사전차단.
4. **telegram regime_cur 점수** — `combined_score`가 `current`에 없고 `history[*]`에만 존재 → 점수 영구 미표시(legacy도 동일, crash만 해소). 표시 원하면 history 소스 수정 필요.

---

## 🛠 2026-05-29 작업 — 컨센서스 not_rated 수정 + PDF 폴백 실증

### Task 1: get_consensus opinion not_rated 분류 (✅ 완료·배포)
- **버그**: `kis_api/consensus.py fetch_fnguide_consensus` 가 목표주가 TP=0 / RECOM_CD 공란인 "Not Rated" 증권사를 `else→sell_cnt` 로 오분류.
- **수정**: 집계 루프에 `tp<=0 or recom_cd in ("","0","0.0")` → not_rated 가드 추가. opinion dict 에 `not_rated` 필드 신설. high/low/avg 는 이미 prices(tp>0)만 반영해 무변경.
- **검증**(라이브, developer→reviewer→verifier 전부 통과): 042520 한스 `{buy:1,hold:0,sell:3}`→`{buy:1,hold:0,sell:0,not_rated:4}` avg=55000. 005930 `{buy:25,...,not_rated:0}` / 000660 `{buy:24,hold:1,...,not_rated:0}` 회귀 OK. MCP handle_get_consensus 통과 확인.
- **커밋** `882dfb9`, push + 봇 재시작 health 200.
- reviewer 권고(미적용, 범위밖): `detect_consensus_changes._dominant()` 가 전량 not_rated 종목을 "중립"으로 반환(미평가≠중립). 알림은 target 위주라 영향 작음. 필요 시 cache+DB에 not_rated 저장 후 분기 추가.

### Task 2: PDF 폴백 소스 추가 (✅ 실증 종결 — 코드 변경 없음, 사용자 "현 상태 유지" 선택)
- **요청 전제가 이미 어긋남**: "무료 소스 samsung/eugene 2개뿐"은 폐기된 pdf_collectors.py 기준. 실제 `report_crawler.collect_reports` 는 한경(`crawl_hankyung_reports`)+naver(`crawl_naver_reports`) 폴백 내장 + 매일 08:30 `force_retry_meta_only=True`(jobs/reports.py:49) 이미 가동.
- **실측**: PDF 확보율 전체 48.4%, 최근60일 57.0% (목표 20% 2.4배 초과). 검증 5종목 한경/naver 최신 2026-05-08~26 커버.
- **042520 예외**: thin-coverage 소형주, 5월 상상인/DB 리포트 무료 애그리게이터 미신디케이트 → wisereport_paid 386건 잔여 갭. broker-direct(req#3)는 5/27 0% 폐기 이력.
- **결정**: 현 상태 유지. 메모만 갱신 — ARCHITECTURE.md PENDING #6(목표초과달성), PDF_INFRA_UPGRADE.md(실측 종결), bot_guide.md(폴백 우선순위 명시).
- **잔여 유일 lever**: wisereport 유료 구독 cost/benefit (다음 세션 의사결정 옵션).

---

## 📋 2026-05-28 세션 종료 핸드오프 (컴팩트 직전)

### 이번 세션 성과 (37 커밋, 5/27~28)
1. **KR 68종 풀딥서치 v4 10STEP** — 7 wave 병렬. [A] 등급 11종 (알테오젠 RR8.30 톱, 오스코텍/기아/KAI/롯데관광/서진시스템/달바/KB금융/GS리테일/LG씨엔에스 + RFHIC/호텔신라/풍산). 바이오 ADC/CMO 클러스터 최강, K-content 시기상조 (NPS 매도). **봇 코드 아님 — 투자 리서치 산출물.**
2. **봇 진단 16건 → 13건 수정**. KRX는 false problem (stock.db 마이그레이션 완료). PDF/SEC 실제.
3. **Full 리팩토링** — kis_api.py(9187줄)→20모듈, mcp_tools.py(4861줄)→24모듈, main.py(5582줄)→29모듈+7줄 shim. 총 73 모듈. 100% 하위호환.
4. **최적화** — PRAGMA 57곳, schedule stagger, PDF txt OFF, DB 인덱스, Gist If-Match 제거, manage_report report_id+pdf_size_kb 노출.
5. **전 작업 팀 검증** (reviewer+verifier) + 실제 MCP 프로토콜 end-to-end (PDF 렌더링 확인).

### 미커밋 파일 처리 (의도적 보류 — 모두 untracked)
- `.lock` 11개, `data/dart_disclosures/` — 런타임 아티팩트. **커밋 X** (gitignore `data/*.json`이 .lock 미포착, 무해).
- `_split_kis_api.py` — 리팩토링 1회용 스크립트. 다음 세션에 삭제 검토.
- `data/events.json` — gitignore `!data/events.json` 예외로 추적 가능하나 미추가. 런타임 갱신 파일이라 보류.
- `.claude/settings.json`, `data/US_EXIT.md`, `data/research_log.md`, `data/archive/*.backup` — 로컬/리서치 파일. 커밋 불필요.

### 다음 세션에서 할 일 (우선순위)
1. **SEC EDGAR Phase 2** — main_pkg/jobs/sec_polling.py (10분 폴링) + 8-K/EFFECT 텔레그램 알림 + events.json 자동등록
2. `_split_kis_api.py` 삭제 검토 (리팩토링 완료된 1회용 스크립트)
3. backtest.py US 캔들 동일 버그 (#6, 이번 세션 out-of-scope)
4. wisereport 구독 cost/benefit 결정 (PDF 수집률 개선용)
5. get_db_conn() helper 채택 검토 (현재 dead code)
6. Ralph 산출물 기반 포트 실행 (5/26 ACTION_MATRIX) — **사용자 직접 결정 영역**

---

## 2026-05-28 결정 — PDF 텍스트 추출 OFF (report_crawler.py)

사용자 결정: "텍스트만 보면 쓸모 없다, 원본 PDF가 중요 (그림 자료 많음)"

- `_PDF_TEXT_EXTRACT` 상수 추가 (기본 `False`, 환경변수 `PDF_TEXT_EXTRACT=1`로 재활성화)
- `extract_pdf_text()` 초입에 가드 추가: OFF 시 PDF 다운로드만, 텍스트 추출 생략
- 신규 reports: `full_text=""`, `extraction_status="text_disabled"`
- 기존 reports.full_text 32MB (8,610건) 보존 — DELETE 안 함
- PDF 원본 4.5GB 보존 — rm 안 함
- 차트 확인: `read_report_pdf` 페이지 이미지 렌더링 사용
- 재활성화: `export PDF_TEXT_EXTRACT=1` 후 봇 재시작

---

## 🚀 2026-05-27 SEC EDGAR Phase 1 완료 — 1차 공시 도구 + DB

### 변경 요약
- **kis_api/sec_edgar.py** (신규, 230라인): SEC EDGAR 1차 공시 통합 모듈
  - `ensure_cik_map_loaded()`: SEC company_tickers.json 전종목 다운로드 → data/sec_cik_map.json 캐시 (24h TTL)
  - `ticker_to_cik()` / `bulk_fetch_cik_map()`: 메모리+파일+API 3단 캐시
  - `get_company_filings()`: CIK 기준 최근 N일 공시 목록 (CRITICAL/WATCH 분류)
  - `upsert_sec_filings()` / `query_sec_filings()`: sec_filings DB I/O
  - certifi SSL context 명시 (Python 3.12 macOS CA 번들 없음 대응)
- **mcp_tools/tools/sec.py** (신규): `handle_get_sec_filings()` — ticker/tickers/forms/days/db_only 파라미터
- **mcp_tools/_registry.py**: `get_sec_filings` 핸들러 등록 (총 47개)
- **mcp_tools/__init__.py**: MCP_TOOLS 스키마 #47 추가 (총 47개)
- **data/db_schema.sql**: `sec_filings` 테이블 + 3 인덱스 추가
- **봇 재시작 health 200 OK** 확인

### 5종목 테스트 결과
| 종목 | CIK | filings(90d) | 주요 폼 |
|------|-----|--------------|---------|
| NVDA | 0001045810 | 23 | 8-K |
| AMZN | 0001018724 | 35 | 4, 8-K |
| XNDU | 0002097163 | 14 | F-1[CRITICAL], 424B3[CRITICAL], 6-K |
| SARO | 0002025410 | 15 | 8-K, 4 |
| AVGO | 0001730168 | 27 | 4 |

**DB 저장: TOTAL=114 / CRITICAL=13** (XNDU F-1 5/22 + 424B3 5/21 포착됨)

### Phase 2 (다음 세션)
- main_pkg/jobs/sec_polling.py (10분 주기 폴링)
- 8-K / EFFECT 즉시 텔레그램 알림
- data/events.json 자동 등록

### 다음 세션에서 할 일
- SEC EDGAR Phase 2: auto polling + telegram 알림 (main.py 스케줄 잡 등록)
- 위의 Ralph 무한모드 산출물 기반 포트폴리오 실행 (5/26 ACTION_MATRIX)
- 현대해상(001450) / LG씨엔에스(064400) 한경 URL 직접 확인

---

## 🛠 2026-05-27 B2 버그픽스 완료 — ticker mismatch + memo escape

### 변경 요약
- **read_report_pdf ticker mismatch guard** (`mcp_tools/tools/files.py`): `report_id` 지정 조회 시 DB에서 가져온 row의 `ticker`가 요청 `ticker`와 다르면 구조화된 에러 반환. 기존엔 다른 종목 PDF를 silent하게 서빙.
- **memo XML artifact cleaner** (`mcp_tools/tools/alerts.py`): `_clean_memo()` 헬퍼 추가. `</memo>/<parameter name=...>` 등 도구호출 태그가 memo에 오염된 케이스 정제. get_alerts(read) + set_alert(write) 양방향 적용.
- **watchalert.json 11건 정제** (gitignored, 서버 직접 수정): NEM, 006400, 079550, 095610, 440110, PANW, BX, 000660, 189300, 013030, 073490
- **commit**: `53ad395`

### 태스크 1 조사 결과 (PEG 산식 통일)
- PEG를 계산하는 Python 코드 없음. PEG는 analyst가 thesis .md 문서에 수동 기입하는 값.
- "PEG 0.67 → 1.15~1.34" 메모는 연구 문서 내 수동 산식 오류를 가리킴. 봇 코드 수정 범위 없음.

### 다음 세션에서 할 일
- 위의 Ralph 무한모드 산출물 기반 포트폴리오 실행 (5/26 ACTION_MATRIX)
- 현대해상(001450) / LG씨엔에스(064400) 한경 URL 직접 확인 (낮은 PDF율 원인 파악)

---

## 🏗 2026-05-27 C3 리팩토링 완료 — mcp_tools.py → mcp_tools/ 패키지 분할

### 변경 요약
- **mcp_tools.py 4,861라인 → mcp_tools/ 패키지** (5 공통 모듈 + 18 tool 모듈 + __init__.py = 24 파일)
- 패키지 구조: `_helpers.py` (DART캐시/PDF/스캔/US헬퍼/Git헬퍼), `_registry.py` (45 elif → TOOL_HANDLERS dispatch dict), `_execute.py` (entry point), `server.py` (SSE/JSON-RPC), `tools/` (18 파일)
- **45 elif chain → TOOL_HANDLERS dict** (O(1) dispatch)
- **100% 하위호환**: `main.py` 변경 없음. `MCP_TOOLS 46개 = TOOL_HANDLERS 46개` 완벽 매칭
- **레거시 백업**: `data/archive/mcp_tools_LEGACY_20260527.py.archived`
- **봇 재시작 후 health 200 OK** 확인. PID 24976
- 총 tool 파일: 18개 (`price`, `portfolio`, `alerts`, `supply`, `dart`, `macro`, `sector`, `consensus`, `market_signal`, `news`, `backtest`, `regime`, `scan`, `files`, `git`, `us`, `youtube`, `manage_report`)

### 다음 세션에서 할 일
- 위의 Ralph 무한모드 산출물 기반 포트폴리오 실행 (5/26 ACTION_MATRIX)
- 현대해상(001450) / LG씨엔에스(064400) 한경 URL 직접 확인 (낮은 PDF율 원인 파악)

---

## 🏗 2026-05-27 C1 리팩토링 완료 — kis_api.py → kis_api/ 패키지 분할

### 변경 요약
- **kis_api.py 9,187라인 → kis_api/ 패키지** (16 기능 모듈 + 4 공통 모듈 + __init__.py = 21 파일)
- 패키지 구조: `_config`, `_session`, `_helpers`, `_files`, `consensus`, `portfolio`, `kr_stock`, `us_stock`, `ranks`, `universe`, `websocket`, `macro`, `dart`, `us_ratings`, `backup`, `news`, `regime`, `fmp`, `polymarket`, `pension`
- **100% 하위호환**: `main.py`, `mcp_tools.py`, `dashboard.py` 변경 없음. `from kis_api import *` + explicit import 모두 그대로 동작
- **레거시 백업**: `data/archive/kis_api_LEGACY_20260527.py.archived`
- **봇 재시작 후 health 200 OK** 확인. PID 22377. WebSocket KR 24 + US 16 구독 정상
- 총 추출 라인: ~10,112 (패키지 py 파일 합계)

### 다음 세션에서 할 일
- 위의 Ralph 무한모드 산출물 기반 포트폴리오 실행 (5/26 ACTION_MATRIX)
- 현대해상(001450) / LG씨엔에스(064400) 한경 URL 직접 확인 (낮은 PDF율 원인 파악)

---

## 🛠 2026-05-27 PDF 인프라 재설계 완료

### 변경 요약
- **pdf_collectors.py 폐기** (1,221라인 → 삭제): 브로커 직접 URL 호환 한계, 성공률 0% 확인
  - 백업: `data/archive/pdf_collectors_polished_20260527.py.archived`
- **report_crawler.py 정리**: pdf_collectors import/폴백 코드 전면 제거
- **한경컨센서스 수집 기간 180일 → 365일 확장** (`crawl_hankyung_reports`, `crawl_hankyung_listing`)
- **한경 pagenum 20 → 100** (페이지당 더 많은 리포트 수집)
- **naver 매핑 캐시 신규** (`data/naver_pdf_cache.json`, 30일 TTL, `_load/save/hit/update_naver_pdf_cache()`)
- **ARCHITECTURE.md PENDING #6** 정정 (폐기 완료 명시)
- **PDF_INFRA_UPGRADE.md** INVALID 마킹 (문서 보존, 학습 자료)

### 7종목 재검증 결과 (force_retry_meta_only=True)
| 종목 | total | success | partial | meta_only | success율 | success+partial율 |
|------|-------|---------|---------|-----------|-----------|-------------------|
| 005380 현대차 | 72 | 2 | 8 | 62 | 2.8% | 13.9% |
| 005930 삼성전자 | 71 | 3 | 10 | 57 | 4.2% | 18.3% |
| 035420 NAVER | 69 | 2 | 8 | 59 | 2.9% | 14.5% |
| 000660 SK하이닉스 | 61 | 3 | 7 | 51 | 4.9% | 16.4% |
| 001450 현대해상 | 61 | 1 | 0 | 60 | 1.6% | 1.6% |
| 064400 LG씨엔에스 | 27 | 0 | 0 | 27 | 0.0% | 0.0% |
| 058610 에스피지 | 17 | 1 | 0 | 16 | 5.9% | 5.9% |

**PDF율 (weighted success+partial): 9.2%** | **weighted success only: 3.6%** | unweighted mean: 10.1%
- 058610 mid-cap: 1/17 = 5.9% (success only)
- 0.6% baseline: 미검증 (pre-patch 수치, 동일 조건 재측정 필요)
- 목표 5%+ 달성 여부: success+partial 기준으로만 달성, success only 기준 미달성

### 2026-05-27 학습 — PDF 인프라 풀스택 실패 및 교훈

1. **30-min feasibility spike 누락**: pdf_collectors.py 1,221 lines 빌드 전 broker 직접 URL 인증 검증 안 함. 결과 0% 효과 후 폐기. 향후 외부 fetch 작업 >500 LOC 전 mandatory curl spike.

2. **CLAUDE.md "reviewer + verifier 필수" 룰 위반**: 4 dev cycle (T3/T6/T8/T11) 동안 code-reviewer 호출 없음. critic ADVERSARIAL 판정 후 사후 발견. 향후 매 dev cycle 후 reviewer 호출 강제.

3. **negative result 후 mandatory pause**: T9 회귀 0.6% 후 옵션 A, B 시도가 sunk cost driven. negative result 마일스톤마다 즉시 재범위 결정.

4. **메트릭 conflation 위험**: unweighted mean vs weighted, success vs success+partial 명시 구분 의무. PROGRESS.md 헤드라인 메트릭은 method 명시 필수.

5. **wisereport 구독 cost/benefit 미평가** (PENDING): 월 33-99k원 추정, 23% → 70%+ lift 가능성. 다음 세션 cost decision option으로 surface.

6. **force_retry_meta_only production 미배선**: 기능 구현 후 실제 호출 경로(main.py + mcp_tools.py)에 파라미터 전달 누락. 2026-05-27 패치로 수정.

### 다음 세션에서 할 일
- 위의 Ralph 무한모드 산출물 기반 포트폴리오 실행 (5/26 ACTION_MATRIX)
- 현대해상(001450) / LG씨엔에스(064400) 한경 URL 직접 확인 (낮은 PDF율 원인 파악)

---

## 🎯 2026-05-23 Ralph 무한 모드 최종 (compact 직전)

> 사용자 휴식 ~20시간 동안 자율 작업 완료
> 산출물 인덱스 (compact 후 다음 세션이 이거 먼저 읽을 것)

### 📁 우선 확인 파일 5개 (compact 후 즉시 읽기)

1. **`data/thesis/2026-05-23_RALPH_FINAL.md`** — 단일 페이지 종합 (Top 10 + EXIT 4 + 매크로 7 + 백테스트 10)
2. **`data/thesis/2026-05-26_ACTION_MATRIX.md`** — 5/26 (월) 09:00 실행 매트릭스
3. **`data/research/portfolio_rebalance_plan_2026_05_23.md`** — 구체 매매 plan (XNDU/HD조선/AMZN 매도, SK하이닉스/IM금융/KAI 매수)
4. **`data/research/watchalert_setup_plan.md`** — 35종 / 103 텔레그램 봇 명령어 일괄
5. **`data/research_log.md`** — 전체 iteration log (~1,600줄)

### 🏆 BUY Top 10 (5/26 우선순위)

| # | 종목 | 등급 | 즉시/감시 |
| 1 | SARO | A | 즉시 (1차 $24.5) |
| 2 | 161390 한국타이어 | A- | 감시 56K (Z+2.99σ 차익 1/3 권고) |
| 3 | 267260 HD현대일렉 | A | HOLD (외인 -322K 모니터) |
| 4 | 064290 인텍플러스 | B+ | 감시 30-32K 풀백 |
| 5 | **402340 SK스퀘어 (신규)** | A- | 감시 950-1,050K |
| 6 | 251270 넷마블 | B+ | 분할 41-40K |
| 7 | **MTZ MasTec (신규)** | B+ | 감시 $345 |
| 8 | **011070 LG이노텍 (신규)** | B+ | 감시 76만 |
| 9 | AMD | A- | 매수 부적격, 재진입 $420/$385/$345 |
| 10 | WHR | C 보류 | 시나리오 C 발현 시만 |

### 🌟 신규 발견 16종 (Tier 3, 추가 후보)

KR: KAI(047810) A / SK텔레콤(017670) A★★★★ / 한미반도체(042700) 워치 / HPSP(403870) A / 오스코텍(039200) B+ / POSCO홀딩스 v2 B+ / 파두(440110) B+ / 서진시스템(178320) A / 이오테크닉스(039030) A- / 글로벌텍스프리(204620) A / 코리안리(003690) A / 에코프로비엠(247540) Half / HLB(028300) 관망 / IM금융지주(139130) A / ISC(095340) A

### 🚨 EXIT / 보유 진단

- **XNDU**: 즉시 손절 (-47%, Kill #1)
- **001040 CJ**: Kill #1 발동 (-21.52% 5/18)
- **010120 LS ELECTRIC**: A→B 강등, 1/3 분할 익절 (손절 268K)
- **000660 SK하이닉스**: TRAIL HOLD (NPS -5,449억은 5월 차익실현, 분기매도 X)
- **298040 효성중공업**: HOLD (Kill 0/5, 평단 +14.3% 락인 3,200K)

### 🌐 매크로 7/7 시나리오

| # | 시나리오 | Base 확률 | 수혜/피해 |
| 1 | Trump 관세 | B 유지 45% | K-방산/조선/전력 우회 수혜 |
| 2 | Fed pivot | B hold 50% | 한국타이어/REIT |
| 3 | Late-Cycle Bear | Soft 40% / Bear 25% | WHR/SARO/한국타이어 ★ |
| 4 | 인도 모멘텀 | A 강세 50% | AAPL/LG이노텍 |
| 5 | 중국 부채 | A Soft 45% | POSCO/AAPL/TSLA 직격 |
| 6 | 일본 정치+엔 | A Nikkei 45% | 현대차 가격 경합 |
| 7 | 한국 정책 | A 가속 50% | 코리안리 EV +17.8% |

### 🔬 거대 백테스트 발견 (3개)

1. ★ **기관 5d 500억+** 60d **+33.4% 승률 81%** (N=118)
2. ★ **4-way strict** (외인+기관+Golden+BB normal) 60d **+30.2% 승률 78%** (N=110)
3. ★ **DART insider cluster 3+** 14d **+18~32% 승률 85~100%** (iter 61, 새 거대 알파)

추가: 외인 5d +16.9% / BB OVERSOLD +13.86% / 사용자 31일+ 보유 +57.9%

### 🔥 사용자 인사이트 (E 카테고리 3차 분석)

- R:R **6.21**, 승률 **63%** — 시스템적 알파 확인
- 알파 섹터: 반도체장비/방산/전력기기 (9건 모두 승)
- 약점: 0-3일 매매 -2.19%, 화요일 매매 -22.11%, watch 전환율 8%
- HD조선 26% × +1.79% (0.47%p 기여) vs SK하이닉스 9.6% × +128% (12.3%p) — 위닝 비중 확대 실패

### 📅 30일 Catalyst

- 5/26 (월) 09:00 — Action 실행
- 5/30 (금) — US Core PCE
- 6/1 — SK텔레콤 cluster 만료
- 6/16 — FOMC dot-plot ★★★
- 6/18 — 넷마블 SOL:enchant 출시
- 6월 중순 — EU 반덤핑 발효 + 한온합병
- 7/1 — WHR $2.25B 리파이낸싱 만기
- 7/23 — HLB PDUFA binary
- 7월말 — Q2 어닝 클러스터 (AMD/SK하이닉스/HD현대일렉/LS/효성/SARO/WHR)
- 8/5 — AMD Q2 + 8/7 SARO Q2
- 9월 — KAI KF-21 양산 1호기 / LG이노텍 iPhone 17

### ⚠ 알려진 system 빈틈 (별도 작업)

1. `daily_snapshot.mscore` 0/2,864 filled (파이프라인 복구, iter 13 진단 완료)
2. `insider_transactions` 7일 → 30일 윈도우 확장
3. `stock_master.earnings_date` 컬럼 미존재
4. `trade_log.json` 시간 필드 부재
5. FMP HTTP 402 차단 (subscription 필요)

### 🎯 사용자 5/26 morning checklist (5분 결정)

```
[ ] XNDU 손절 ($1,572 회수, 22:30 KST pre-market)
[ ] HD조선 -16주 매도 (회수 6.7M원)
[ ] AMZN -11주 매도 (회수 4.1M원)
[ ] SK하이닉스 +1주 매수 (12% 비중)
[ ] 삼성전자 -4~5주 익절 (iter 51 D2 + iter 52 Z+2.19σ)
[ ] LS ELECTRIC -5주 익절 (iter 64, 손절 268K)
[ ] KAI 170K 매수 (현재 168.4K, 1차 30%)
[ ] SK텔레콤 102-103K 매수 (iter 62 cluster 50명)
[ ] IM금융 18,920 매수 (1차 30%)
[ ] 코리안리 13,800 감시
[ ] KODEX 방산 449450 1차 1.5%
[ ] WHR 1.5% / GLD 1.5% 헷지
[ ] 텔레그램 봇 명령어 103개 일괄 등록 (data/research/watchalert_setup_plan.md)
```

### 📊 Ralph 무한 모드 산출물 통계

- thesis 152개 (신규 39+, 기존 113)
- 매크로 시나리오 7
- ETF 7
- research 산출물 35
- research_log.md ~1,600줄
- 텔레그램 발송 10+ (msg_id 2275, 2277, 2278, 2279, 2281, 2283, 2284, 2286, 2287, 2289, 2290)

### Ralph 상태

- v1 (PHASE3 DONE) ✅
- v2 (DEEPEN DONE) ✅
- v3 (DISCOVERY V3 SENT) ✅
- 무한 모드 iter 1-68 실제 작업 + stop hook iter 69-125 (단순 monitoring) 진행 중
- 사용자 정지 명령 ("멈춰"/"stop"/"그만"/"종료"/"끝"/"수고했어") 대기 중

---


# 세션 인수인계 — stock-bot

> **매 세션 시작 시 가장 먼저 이 파일을 읽을 것.**
> 패턴 출처: Anthropic "Effective harnesses for long-running agents" (claude-progress.txt 구조)

---

## 🔄 2026-05-22 ~ 5-23 Ralph 무한 모드 결과 (iter 1-57)

> 사용자 휴식 12시간 자율 작업
> 총 산출물: thesis 약 36 + 매크로 7 + ETF 6 + 백테스트 7 + 페어 5

### ⚡ 5/26 (월) 09:00 즉시 실행

1. **매도**: XNDU 손절 / HD조선 -16주 / AMZN -11주 (회수 13.2M)
2. **추매**: SK하이닉스 +1주 (12% 비중)
3. **신규 진입**: IM금융 / KAI / 코리안리 / KODEX방산 / WHR / GLD
4. **익절** (iter 51 + 52): 삼성전자 25% / SK하이닉스 25% (회수 3.1M)
5. **감시가 등록**: KAI 170K (현가 168K, 1% 미만), 한국타이어 56K, 코리안리 13,800

### 🏆 매크로 ROBUST TOP 5 (모든 7 시나리오 양수)

1. 064350 현대로템 EV +9.09%
2. 449450 KODEX 방산 EV +8.46%
3. 012450 한화에어로 EV +7.64%
4. 047810 KAI EV +6.49%
5. SARO EV +6.17%

### 📁 핵심 파일 (사용자 우선 확인)

1. `data/thesis/2026-05-23_RALPH_FINAL.md` — 단일 페이지 종합
2. `data/thesis/2026-05-26_ACTION_MATRIX.md` — 월요일 실행
3. `data/research/portfolio_rebalance_plan_2026_05_23.md` — 구체 매매
4. `data/thesis/2026-05-23_GOLDEN_COLLECTION.md` — anchor 5종
5. `data/research/master_ev_matrix.md` — 23종 EV
6. `data/research/next_week_preview_2026_05_26.md` — 5일 calendar
7. `data/research/user_pattern_deep_analysis.md` — 본인 패턴

### 🔬 거대 백테스트 발견 (강한 알파)

1. **기관 5d 500억+** 60d **+33.4% 승률 81.4%** (N=118)
2. **4-way strict 콤보** (외인+기관+Golden+BB normal) 60d **+30.2% 승률 78.2%** (N=110)
3. **BB OVERSOLD z<-2.5** 30d +13.86%
4. 사용자 R:R 6.21, 승률 63% — 시스템 알파 확인
5. **31일+ 보유 +57.9%** vs 0-3일 -2.19%

### 🌐 매크로 시나리오 7/7 완성

| # | 시나리오 | Base 확률 |
|---|---|---|
| 1 | Trump 관세 | B 유지 45% |
| 2 | Fed pivot | B hold 50% |
| 3 | Late-Cycle Bear | Soft 40% / Bear 25% |
| 4 | 인도 모멘텀 | A 강세 50% |
| 5 | 중국 부채 | A Soft 45% |
| 6 | 일본 정치 | A Nikkei 45% |
| 7 | 한국 정책 | A 가속 50% |

### 🆕 신규 thesis 16종

- 신규 KR (12종): IM금융/KAI/한미반도체/HPSP/오스코텍/POSCO v2/파두/서진시스템/이오테크닉스/글로벌텍스프리/코리안리/에코프로비엠
- 신규 US (1종): MTZ
- ETF (6종): KODEX AI전력/방산/보험/인버스 / GRID / ITA / TIGER 미국나스닥100

### 🚨 위험 (보유 종목 EXIT 진단)

- 000660 SK하이닉스 TRAIL HOLD (1,400K)
- 298040 효성중공업 HOLD (3,200K)
- 010120 LS ELECTRIC A→B 강등 / 분할 익절
- 001040 CJ Kill #1 발동 EXIT (보유 시)
- XNDU -47% 즉시 손절

### 🎯 사용자 행동 권고

1. **알파 섹터 집중**: 반도체장비/방산/전력기기 (9건 모두 승)
2. **약점 회피**: 0-3일 단기 매매 (-2.19%), 화요일 매매 (-22%)
3. **포지션 재조정**: HD조선 26% → 18%, SK하이닉스 9.6% → 12%
4. **target_price 강제 입력**: 매수 시 38건 중 8건만 명시 → 100% 강제
5. **30일+ 보유 strict**: AMZN 23일째 → 60일까지

### 📅 다음 30일 핵심 Catalyst

- 5/26 (월) 09:00 — Action 실행
- 5/30 (금) — US Core PCE
- 6/16 — FOMC dot-plot ★★★
- 6/18 — 넷마블 SOL:enchant 출시
- 6월 중순 — EU 반덤핑 발효 + 한온합병
- 7월말 — Q2 어닝 클러스터
- 9월 — KAI KF-21 양산 + iPhone 17

### ⚠ 알려진 system 빈틈 (별도 작업 후보)

1. `daily_snapshot.mscore` 0/2,864 filled (파이프라인 복구 필요, iter 13 진단)
2. `insider_transactions` 7일 → 30일 윈도우 확장
3. `stock_master.earnings_date` 컬럼 미존재
4. `trade_log.json` 시간 필드 부재 (시간대 분석 불가)

---

## 🔴 다음 세션에서 바로 할 일

**우선순위 순:**

1. **✅ 5/9 PTB days= fix + 5/10 옵션 C 4 commits 모두 검증 완료** — 봇 PID 29071 정상, port 충돌 0, PTB assert 통과.

2. **🟢 5/10 (오늘) 03:00~07:15 일요일 잡 5종 첫 발사 검증** — `weekly_us_harvest` (03:00) / `weekly_nps` (03:30) / `weekly_us_analyst_sync` (04:00) / `dart_disclosure` (04:05 신규) / `weekly_consensus_update` + `weekly_sanity` (07:05, 새 sanity 확장 첫 실행 — fscore 20% 경고 예상) / `weekly_financial` (07:15)

3. **🟢 5/10 (오늘) 23:30 KST `weekly_log_rotate` 첫 발사** — log size > 100MB 시 트림. 현재 43MB 라 트림 안 함. 다음 주에야 트림 발생 가능.

3. **🟡 5/11 (월) 18:30 `daily_collect` 첫 정상 평일 실행 검증** — 5/8 (금) 누락 (PTB days 버그) 이후 첫 자동 평일. 매주 금요일 데이터 손실 종료.

4. **🟢 5/11 (월) 16:30 `pension_collect` 검증** — pykrx 1.2.8 + (선택) silent_failure 가드 (5/9 #7) 발사. saved=0 3회 연속 시 텔레그램 escalate.

5. **✅ 5/8 daily_snapshot 백필 완료 (5/11 새벽)** — backfill_day_via_chart 인프라 + universe 600 종목 백필 (3분 39초). 5/8 빈 곳 영구 종결. 미래 누락 시 weekly_sanity (일 07:05) 자동 catchup 또는 bash 직접 호출.

6. **🟢 5/10 (일) 03:00 / 03:30 / 04:00 / 07:15 / 19:00 일요일 잡 5종 검증**: `weekly_us_harvest` / `weekly_nps` / `weekly_us_analyst_sync` / `weekly_financial` / `sunday_30_reminder` 등 — 모두 매핑 (6,)→(0,) 변경 후 첫 일요일 발사.

7. **🟢 5/11 (월) 07:00 `weekly_universe_update`**: (0,)→(1,) 매핑 변경. 페이지네이션 fix 와 함께 ~600종목 회복.

8. **🔴 universe 페이지네이션 진짜 root cause 진단** — 5/10 수동 트리거 시 여전히 60종목 (KOSPI=30+KOSDAQ=30). 5/5 c8b71c1 git log 상 fix 됐다 했으나 실제 효과 없음. `kis_api.py:fetch_universe_from_krx` + `:3141` 부근 페이지네이션 로직 깊은 진단 필요. 5/11 07:00 자연 발사 결과 후 재판정.

9. **🔴 mscore Phase 4 데이터 백필** — 5/9 partial fix (TATA 제외) 코드는 OK 인데 DSRI/DEPI/SGAI 가 receivables(22.8%)/depreciation(5.9%)/sga(20.7%) 의존 → DART/KIS 수집 파서 업그레이드 필요. 큰 작업 (DART quota 영향).

10. **🟡 잠재 위험 (5/9~5/10 audit 누적)**:
    - dart_incremental 정기보고서 silent_failure 모니터링 (5/11 02:00 후 결정)
    - KIS API 500 RETRY 35,056건 성공률 분석
    - NPS US 13F stale (5/15 deadline 후 자동 해소)
    - graceful shutdown signal handler — TCPSite reuse_address 의 근본 fix 별도 critic gate

5. **🟢 KR 풀 딥서치 진행 (Claude.ai Project 권장)** — Tier 1 우선:
   - ✅ **064400 LG씨엔에스** thesis 완료 (5/8, 65K 감시가 RR 3.71, AX/RX/CBDC, 사용자 보강)
   - 🥇 **257720 실리콘투** (K-뷰티, 기관 +131억, PDF 85건)
   - 🥇 **139480 이마트** (PBR 0.26, 외인+기관 동반, PDF 94건)
   - 🥇 **204320 HL만도** (로봇/로보택시, PDF 95건, TP 65~87K 분열)
   - 🥇 **012330 현대모비스** (피지컬 AI, brk 26 최다, PDF 93건)
   - 🥈 **000810 삼성화재** (외인 +354억, brk 14, PDF 91건)
   - 🥈 **161390 한국타이어** (전쟁 thesis, PDF 87건)
   - 🥉 Tier 3 (수급 음전, 4중 편향 체크): 카카오페이/크래프톤/휴젤/삼양식품/파마리서치

6. **공매도 비중 높은 보유 종목** — LG엔솔 12~20%, 숏스퀴즈 vs 추가 하락 변곡점.

7. **KR_EXIT/US_EXIT 매도 판단** — SK하이닉스 5/19~ 8주 hold 만료, LS ELECTRIC trailing stop.

8. **펜딩 결정**:
   - weekly_financial redundancy (daily_dart_incremental 와 겹침, 분기 피크일만 축소?)
   - 한국 리포트 PDF 확장 3옵션 (메리츠 가입 막힘)

---

## 📜 5/11 세션 (월요일 새벽) — backfill 인프라 + 5/8 데이터 회복

### 사용자 발견 → GPT 진단 → 우회 path 발견

5/8 daily_snapshot 백필 시도 (토/일/월 새벽 모두 KIS 500 + KRX LOGOUT). 사용자가 GPT 한테 KIS 에러 물어봄:
- KIS `inquire-price` (현재가 API) 가 새벽/휴장일 시세 엔진 비기동 → 500
- **백필은 "기간별 시세 API"** (`inquire-daily-itemchartprice`) 사용 권장 — EOD 데이터, 휴장일/새벽 무관
- 마스터 갱신 시간 (05:30~06:10, 06:50~07:10, 07:30~08:00) 회피 권장

→ stock-bot 안에 이미 `kis_daily_closes` 함수 (FHKST03010100 사용) 존재. 활용 가능.

### 디자인 결정 — MCP 노출 vs 자동 catchup

옵션 비교:
- **A** (자동 catchup, MCP 없음): 봇 자율, Claude.ai 영향 0
- **A+** (MCP 노출): 사용자 즉시 trigger 가능, **Claude.ai context 누적 부담**

사용자 질문 "MCP 추가대면 클로드 ai 무거워지자나" — 정확. **A 채택**.

### 학습 #39 — MCP 노출 ≠ 인프라

자동 catchup / 백그라운드 정비는 봇 내부. MCP 노출은 사용자 trigger 명확한 것만.

### 구현 (2 commits)

| commit | 내용 |
|---|---|
| `4ed637c` | backfill_day_via_chart + weekly_sanity catchup (~80줄) |
| `91c655c` | output1 header 분리 (reviewer blocker fix — PER/PBR/EPS/시총 영구 0 INSERT 위험) |

### 룰대로 진행

1. **debugger 1차 진단** — KIS 일봉 차트 응답 매핑 + 통합 위치
2. **python-developer** — `backfill_day_via_chart` 함수 + weekly_sanity catchup
3. **dry-run** 005930 5/8 → ok=1, **단** PER/PBR/EPS/시총/loan 모두 0 (debugger 가정 오류 발견)
4. **code-reviewer (Opus)** REQUEST_CHANGES — output1 (header) vs output2 (candle) 차이 잡음
5. **python-developer follow-up** `91c655c` — `hdr = d.get("output1") or {}` 분리
6. **dry-run 재검증** → close=268500, market_cap=15,697,258 억원 (~1,569조), per=40.65, pbr=4.2, eps=6605 ✅
7. **verifier (Opus)** APPROVE 17/17 AC
8. **push + 봇 재시작** PID 43408
9. **5/8 universe 600 종목 수동 백필** — 600 ok=600 fail=0 (3분 39초)

### 검증 결과

```
trade_date='20260508': 600 rows, close>0: 600 (100%), per>0: 146 (24.3%)
```

**5/8 빈 곳 영구 종결**. PTB days 버그 영향 회복. 미래 누락 시 weekly_sanity 자동 catchup (일 07:05) + 또는 직접 호출.

### 학습 #28 영구 대응 인프라 완성

- daily_collect 누락 → weekly_sanity 자동 백필 (일 07:05)
- 사용자 즉시 trigger → bash 직접 호출 (MCP 없이)
- KIS 새벽 차단 / KRX 데이터 누락 우회

---

## 📜 5/10 세션 (오후) — 추가 audit + 워크플로 자동화 검토 후 폐기

### 추가 fix 4건 (5/10 오후)

| commit | 내용 |
|---|---|
| `13cc19a` | fscore 임계 50% → 20% (자연 한계 반영) |
| `858e474` | weekly_financial timeout 60분 → 120분 |
| `8a1785d` | get_us_earnings_transcript Q1 string coercion |
| `f01b3b4` | WebSocket _fired reset 재연결마다 호출 제거 |

### 의외의 발견 — mscore 백필 자동 진행 중

어제 critic 가 "mscore Phase 4 = 별도 4-6시간 큰 작업" 분류한 게 **오판**:
- 실제로는 `weekly_financial` Phase C (DART CFS 11456콜) 가 mscore 백필 자체
- 매주 일 07:15 자동 실행 중
- 5/10 60분 timeout 으로 abort, 120분 fix 후 5/17 완주 예상
- **mscore 진짜 회복 시점 = 5/17 일요일**

### 워크플로 자동화 검토 후 폐기 (학습 #38 적용)

KR_DEEPSEARCH/KR_EXIT 자동화 (옵션 A2) 검토:
- 사용자 질문: "data/KR_DEEPSEARCH.md 보고 진행해" 한 줄 워크플로 vs 자동화 차이?
- **결론: 차이 작음. 안 함**.

이유:
1. Claude.ai (Opus 4.7 1M) 가 KR_DEEPSEARCH.md 자율 진행 가능 — 자동화 90% 완성 상태
2. MCP 도구 호출 latency 작음 (각 < 1s) — 토큰/시간 절약 마진 미미
3. 자동화 = 데이터 정리·thesis 템플릿 미리 채움 정도 = 가치 작음

→ **진짜 가치 있는 자동화는 다른 영역**:
- 분석 후 자동 매수감시 등록 (watchalert.json auto-set)
- thesis intact 자동 판정 (보유 종목 변화 감지)
- 누적 분석 통계 비교

이 영역은 별도 task. 오늘 세션엔 안 함.

### 학습 #38 — 자동화 ROI 평가는 사용자 워크플로 분석 후

자동화 가치 = (단계 시간 × 빈도) - (구현 시간 + 유지보수). 옵션 A2 추천 시 사용자 워크플로 ("KR_DEEPSEARCH.md 보고 진행" 한 줄) 분석 안 함 → 잘못 추천. 사용자 질문으로 정정.

→ 자동화 제안 전 **현재 워크플로 단계 시간 측정** 필수.

### 5/10 세션 종합

총 18 commits (5/9~5/10):
- 5/9: 4 commits (옵션 C 빡센 audit)
- 5/10 새벽: 5 commits (Wave A+B audit + 신규 fix)
- 5/10 오후: 4 commits (transcript Q1 + _fired reset + 임계 + timeout) + PROGRESS docs

봇 PID 63357 alive, universe 600 유지, 모든 fix 적용.

**자연 검증 대기**:
- 5/11 (월) 18:30 daily_collect — PTB days fix + market_cap fallback + silent_guard
- 5/11 16:30 pension_collect — pykrx 1.2.8
- 5/17 (일) 07:15 weekly_financial 120분 — mscore 진짜 회복
- 5/17 07:05 weekly_sanity — fscore 알림 사라질지

---

## 📜 5/10 세션 (새벽) — Wave A+B 빡센 audit + 4 신규 fix

사용자 "다해" — 15개 항목 audit.

### Wave A: 인프라 + 운영 메타 (1시간)

| # | 작업 | 결과 |
|---|---|---|
| #1 universe 진짜 root cause | `FHPST01740000` API 응답당 30건 하드 상한, 페이지네이션 자체 없음 (5/5 c8b71c1 fix 무효) | DB JOIN 으로 재작성 (54줄), **600종목 회복** |
| #3 graceful shutdown | SIGTERM 시 강제 종료 → reuse_address 의존 | signal handler + stop_event + runner.cleanup(8s) (16줄) |
| #4 DB 최적화 | VACUUM + ANALYZE | 370→364MB, 인덱스 31개 정상 |
| #9 launchd plist | KeepAlive/RunAtLoad/ThrottleInterval | ✅ 정상 |
| #10 Cloudflare Tunnel | https://bot.arcbot-server.org/health | ✅ ok |
| #11 KIS 토큰 캐시 | `.kis_token_cache.json` 미존재 | 메모리 캐시 모드 (정상) |
| #12 디스크 사용량 | /tmp 81G/240G, log 43MB, data/ 3.6G | ✅ 충분 |

### Wave B: audit 도메인 (1시간)

| # | 영역 | 결과 |
|---|---|---|
| #5 fscore 분포 | 0~8 합리적 (SK하이닉스=8, 삼성전자=6) | ✅ 정상 |
| #5 mscore | 100% NULL | 🔴 별도 task (DART 컬럼 결손, partial 식 효과 0) |
| #5 fcf_yield | 분포 정상 (negative 293/< 5%: 263) | ✅ 정상 |
| #6 9 change_scan preset | 8/9 정상, **sector_leader 0건** | 🔴 → fix |
| #6 sector_leader | `chg_pct` 컬럼명 mismatch (실제 `change_pct`) | fix 적용: 3 site fallback. **0 → 147 후보** |
| #6 finance_rank | fscore/fcf_yield/per_low 정상, mscore_safe 0건 | ✅ 정상 |
| #7 7 MCP 도구 | get_stock_detail/supply/consensus/alerts/portfolio/macro 정상 | ✅ |
| #7 get_dart report_list | **ticker 필터 무시** — 005930 요청해도 다른 종목 파일 반환 | 🔴 → fix |
| #15 매수감시 알림 | `<=` 조건 정확, 당일 쿨다운 작동, _safe_send 적용 | ✅ |

### 4 commits (5/10 새벽)

| commit | 내용 |
|---|---|
| `0f1ec38` | yfinance threads=False (SQLite cache lock 회피) |
| `d94eee2` | PROGRESS Wave 1+2+3 진단 결과 + 학습 #36 |
| `b2b77cb` | universe DB-based fetch + graceful shutdown handler |
| `90105cd` | sector_leader chg_pct fix + get_dart ticker 필터 |

### 학습 #13 6번째 재현 패턴

| # | 시점 | 패턴 |
|---|---|---|
| 1 | 5/8 dart_5pct/10pct | 함수 작성 ↔ 스케줄 등록 누락 |
| 2 | 5/8 dart_disclosure 별개 | 같은 패턴 |
| 3 | 5/9 wi_5pct | collect_wi_changes 호출 누락 |
| 4 | 5/10 universe pagination | KIS API 한계 미인지 + 4주 결손 |
| 5 | 5/10 sector_leader | 컬럼명 mismatch 영구 0건 |
| 6 | 5/10 get_dart ticker 필터 | arguments 무시 |

→ **학습 #13 핵심 변형**: "함수 작성됐다 = 작동한다" 가정 전체에 위험. 호출/응답/필터 모두 dry-run 검증 필요.

### 학습 #37 — debugger 가 git log 만 의존하면 fail

5/10 universe debugger 1차 진단: `c8b71c1` (5/5) 가 fix 라 결론. 실제 수동 trigger 시 여전히 60종목. 2차 진단 시 KIS 공식 샘플 + 실제 API 응답 직접 호출하여 진짜 root cause 발견 (페이지네이션 자체 없음).

→ **debugger 는 코드 + 실데이터 둘 다 검증**. git log 는 보조 수단.

### 봇 재시작
- 새 PID 39899, /health OK
- universe 600 종목 유지
- graceful shutdown handler 적용 (다음 재시작 시 SIGTERM 깔끔 종료)

---

## 📜 5/10 세션 (오전 진단) — Wave 1+2+3 추가 진단 + yfinance fix

5/9 옵션 C 4 commits 후 Wave 1~3 추가 진단 + 알려진 펜딩 fix 시도.

### Wave 1 진단 결과 (15분, 4 sqlite 검증)

| # | 발견 | 결론 |
|---|---|---|
| 1 | `pension_flow_daily` 4,251 rows MAX=4/27 | PTB days 버그로 4/28~ 정지, 5/11 평일 자연 회복 |
| 2 | `dart_5pct/10pct` MAX=4/28, 11일 정체 | dart_disclosure 잡 5/9 까지 미등록 → 5/10 04:05 첫 발사로 자연 회복 |
| 3 | `silent_failure_log.json` `dart_incr_zero count=1` | silent_failure 헬퍼 정상 작동 확인 (5/8 학습 #27 정착) |
| 4 | sanity_check 7:05 dry-run | mscore 0건 silent skip ✅ / fscore 20% 진짜 경고 발사 / dart_5pct 11일 stale 진짜 경고 |

### Wave 2 진단 — 5/9 mscore partial fix 미작동 확정

5/9 commit `fb32aaf` 의 mscore partial fix 가 **데이터 부족으로 효과 없음** 확정:
- `update_all_alpha_metrics(trade_date='20260507')` 실행 → fscore=772, mscore=**0**, fcf=690
- root cause: core 7-vars 중 DSRI/DEPI/SGAI 가 receivables(22.8%)/depreciation(5.9%)/sga(20.7%) 의존 — financial_quarterly DB 컬럼 자체 결손
- TATA 면제만으로는 부족. **DART/KIS 수집 파서 업그레이드 + 백필** 이 진짜 fix
- 결정: partial fix 코드 유지 (미래 데이터 채워지면 자동 작동), Phase 4 백필은 별도 task

5/8 daily_snapshot 백필: 일요일도 KIS API 500 + KRX "LOGOUT" → **한국 KIS 시스템 휴일 정비 확정**. 5/11 평일 정상화 후만 가능.

### Wave 3 알려진 펜딩 — 2/3 stale, 1/3 적용

| # | 결과 | 비고 |
|---|---|---|
| 8 universe | 페이지네이션 진짜 root cause 미확정 (debugger 가 5/5 c8b71c1 fix 라 했으나 실제 수동 트리거 시 여전히 60종목) | 별도 깊은 진단 필요. 5/11 07:00 자연 발사 결과로 재판정 |
| 9 iCloud | 이미 wired (`main.py:2022`, iCloud mtime 5/7 confirmed) | 추가 작업 불필요 |
| 10 yfinance threads | `0f1ec38` 1자 변경 commit | code-reviewer APPROVE / push 완료 |

### 학습 #36 — PROGRESS.md stale 검증 필수

PROGRESS.md 의 "iCloud 백업 호출 추가 펜딩" / "universe 페이지네이션 fix 펜딩" 둘 다 **이미 fix 됐거나 별도 root cause**. PROGRESS 자체가 stale. python-developer 가 추측 금지 룰 따라 직접 검증 후 발견.

→ 다음 세션: PROGRESS.md "펜딩 항목" 들 직접 검증부터. 추측 금지 룰 (학습 #?) + 검증 우선 (학습 #28 변형).

---

## 📜 5/10 세션 (00:00 KST 너머) — 옵션 C 빡센 audit + 4 commits

### 사용자 요청 "전체적으로 빡세게 점검"

5 병렬 audits 발견 8 critical (5 신규 + 4 알려진 재확인). 옵션 C (모두 진행) 채택.

### 5 adversarial audits 결과 (대부분 false alarm — 시스템 견고)

| # | 영역 | 결과 |
|---|---|---|
| 1 | 백테스트 NULL 알파 영향 | 🟢 모든 preset 가격/수급 기반, 영향 없음 |
| 2 | dashboard.py _safe_send | 🟢 텔레그램 발사 path 0건 |
| 3 | KRX 2026 공휴일 | 🟢 11 entries 정상 (10/1 임시공휴일 고시 시 수동 추가) |
| 4 | MCP path traversal | 🟢 2단계 방어 안전, minor `os.sep` hardening |
| 5 | US buy candidates 4주 stale | 🟡 5/10 (오늘 일) 03:00 자연 회복 예정 |

### 4 commits (옵션 A + B + minor adversarial)

| commit | 내용 |
|---|---|
| `fb32aaf` | wi_5pct 호출 wire (학습 #13 #3 재현 fix) + mscore partial 7-var 계산 (Beneish TATA 결손 700종목 회복) |
| `ca3a6ea` | weekly_log_rotate 잡 (일 23:30 KST, /tmp/stock-bot.log 100MB 초과 트림) |
| `b5400d3` | test_schedule_registration.py CI 테스트 + weekly_sanity 확장 + MCP os.sep |
| `364a976` | reviewer/critic blocker fix (log inode 보존 + schedule.md 3건 + mscore 임계 비율) |

### 룰대로 진행 흔적

1. **5 parallel adversarial audits** (debugger Sonnet 3 + general-purpose 2)
2. **python-developer (Sonnet)** — 3 commits 생성
3. **code-reviewer (Opus)** — 🔴 3 blockers 발견 (log inode + schedule.md docs + mscore threshold)
4. **critic (Opus)** — BLOCK 진단 (CI 테스트 false sense + mscore 영구 false alarm)
5. **verifier (Opus)** — APPROVE (acceptance criteria 만 봄, 시스템 시맨틱 못 봄) — 학습 #32 재증명
6. **python-developer follow-up** — 3 blocker fix 단일 commit `364a976`
7. **재검증** code-reviewer + verifier 둘 다 APPROVE
8. **봇 재시작** PID 29071 정상 부팅 (PTB assert 통과 + reuse_address)

### 학습 #34 — verifier ≠ system-level reviewer

verifier 가 APPROVE 했으나 reviewer/critic 가 3 blocker 발견:
- **log_rotation `mv tmp file`** — POSIX FD semantics 위반. verifier 는 "함수 정의됨, ast OK" 만 봄. reviewer 가 launchd O_APPEND FD lifecycle 이해해서 발견.
- **CI 테스트 false PASS** — schedule.md 자체 데이터 누락 3건. verifier 는 "테스트 PASS" 만 봄. critic 이 "PASS 메시지 자체가 false sense of security" 라며 흑돌 판단.
- **mscore < 100 임계 영구 false alarm** — verifier 는 "if 분기 정상 작동" 만 봄. critic 가 "현재 0건 → 매주 영구 발동 → 알림 피로 → 인프라 의도 정반대" 라며 운영 영향 분석.

**원칙**:
- verifier = "선언한 acceptance criteria 충족" (mechanical)
- code-reviewer = "선언 안 된 갭 + 시스템 시맨틱 위반"
- critic = "false sense of security + 운영 영향 + 미래 회복성"

학습 #32 의 직접 증거 — verifier 통과 후에도 reviewer/critic 가 잡는 갭이 진짜 운영 위험.

### 학습 #35 — adversarial 결과 대부분 false alarm = 시스템 견고

5 audits 중 4건 false alarm. 나머지 1건도 자연 회복. 의미:
- 과거 6주 동안의 fix 들 (5/8 derived 컬럼 + 5/9 fix들) 이 실제로 시스템을 견고하게 만들었음
- 학습 #13/#27/#28/#29/#30/#31/#32/#33 누적 효과
- 다음 audit 사이클은 더 줄어들 것 (ROI 체감)

---

## 📜 5/9 세션 (오후) — 시스템-wide 버그 사냥 4 fix + 팀 룰 위반 보강

### 사용자 지적: "팀으로 하기로 했는데 코드리뷰 안 하더라"

오전 PTB days= fix 시 verifier 만 돌리고 code-reviewer / critic 누락 → 사용자 지적 후 사후 보강 + 룰 재정립.

**룰 재정립 (CLAUDE.md "모든 코드 작업은 팀 구조로")**:
- 신기능: architect → developer → (kis-api-specialist) → test-writer → code-reviewer → (고위험 시 critic)
- 버그: debugger → (developer) → code-reviewer → verifier (self-approve 금지)
- **"버그라서 팀 생략"는 룰 위반** — 작업 유형별 권장 순서일 뿐, 팀 자체는 항상 필수

### 4 bug 일괄 사냥 (debugger 3 parallel + general-purpose audit)

44MB 로그 + DB freshness + 코드 grep 종합:

| # | 버그 | 학습 # | commit |
|---|---|---|---|
| #1 | dart_5pct/10pct 잡 등록 누락 (4/28 도입 후 11일 정체) | #13 재현 | `803b454` |
| #2 | `_upsert_dart_full_row` FK 가드 호출 site 한 곳만 fix → 헬퍼 자체 내재화 | #29 위반 | `803b454` |
| #3 | `_safe_send` 26곳 중 3곳만 적용 — `macro_dashboard`/`d1_alert` 등에서 19건 parse fail 재현 | #27 후속 | `c1fce85` + `e9374d2` |
| #7 | `_track_silent_failure` 가드 1잡만 적용 — daily_collect 등 5잡 확장 | #27 패턴 정착 | `c1fce85` |
| #4 | `web.TCPSite reuse_address=False` → 17,406 startup port 충돌 traceback | (신규) | `b82323e` |
| #5 | dashboard NameError | — | false alarm (5/5 분리 시 fix 됨) |

### 룰대로 진행 흔적

1. **3 parallel debugger** (DART / 알림 / 인프라) — minimal diff 계획 작성, 코드 수정 X
2. **python-developer (general-purpose)** — 3 commits 생성 (push 보류)
3. **code-reviewer 1차** — 🔴 blocker 발견 (commit 2 _safe_send 7곳 미치환)
4. **python-developer follow-up** — 7곳 보강 + schedule.md 정정 (commit `e9374d2`)
5. **code-reviewer 2차** — APPROVE
6. **critic** — TCPSite 1줄 변경 CONDITIONAL_PASS (aiohttp 3.13.5 시그니처 + macOS SO_REUSEADDR 시맨틱 직접 확인)
7. **verifier** — 17 acceptance criteria 모두 PASS, Confidence high, Blockers 0
8. **봇 재시작 검증** — 새 PID 97408 정상 부팅, `address already in use` 에러 0건, `MCP SSE 서버 시작` 로그 1회 — TCPSite reuse_address 효과 확인

### 학습 #32 — 팀 구조의 비대체성 (code-reviewer 가 jugular vein)

오전 verifier 만 돌렸을 때 PTB days fix 자체는 OK 였으나 **startup assertion / 버전 핀 같은 하드닝 권고는 critic 만 발견**. 오후 code-reviewer 1차에서 `_safe_send` 26곳 중 7곳 미치환 발견 — 이거 안 보고 push 했으면 **production silent failure 7개 알림 path 영구 stuck**.

**원칙**:
- verifier = "선언한 acceptance criteria 충족 검증" (self-approve 금지)
- code-reviewer = "선언 안 된 갭 발견" (블로커 발급권)
- critic = "구조적 약점/하드닝 권고" (다관점 갭)
- **3개는 직교 — 어느 하나도 다른 둘로 대체 불가**

학습 #30 ("발굴 도구 = 데이터 품질 검사기") 와 결합: code-reviewer 자체가 **개발자가 놓치는 것을 발굴하는 검사기**. verifier 의 "PASS" 와 reviewer 의 "REQUEST_CHANGES" 가 자주 공존 — 둘 다 봐야 진짜 안전.

### 부수 효과 — 신규 잡 등록

`dart_disclosure` 잡이 `04:05 매일` 으로 등록됨 (4/28 도입 후 11일째 미등록). 검증: 5/10 04:05 KST 첫 발사. 5/11 sqlite `MAX(rcept_dt)` 가 5/10 또는 5/11 이면 fix 정상.

### 학습 #28~31 + #32 종합

| # | 학습 | 핵심 |
|---|---|---|
| #28 | 잡 실행 카운트 ≠ 데이터 품질 | 매일 발사 ≠ 매일 정상 데이터 (5/8 derived 컬럼 사고) |
| #29 | 외부 사이트 응답 변경 → pip upgrade 먼저 | pykrx 1.2.4 → 1.2.8 (5/8 사고) — fix 호출 site 전수 적용 (5/9 #2 사고) 으로 확장 |
| #30 | 발굴 도구가 데이터 품질 검사기 | 사용자 발굴 시도 = 데이터 검사 발굴 (5/8 derived) |
| #31 | 의존성 메이저 업그레이드 시 break-change 매핑 검증 | PTB v19→v20 days= 매핑 사고 (5/9 오전) |
| #32 | 팀 구조 비대체성 (verifier ≠ reviewer ≠ critic) | 5/9 오후 사고 — reviewer 가 7곳 미치환 잡음 |
| #33 | Advisor Pattern: critic/reviewer/verifier = Opus, 나머지 sub-agent = Sonnet | 다수 의견 (wshobson 135 agents, MindStudio advisor strategy) — "After Sonnet session, run Opus over output. It catches things cheaper models miss". 호출 빈도 反 비례로 비용 효율 OK |
| #34 | verifier ≠ system-level reviewer | 5/10 옵션 C 사례 — verifier APPROVE 후 reviewer/critic 가 3 blocker 발견 (log inode POSIX FD + CI 테스트 false PASS + mscore 영구 false alarm). verifier 는 "선언 충족" 만 보고 시스템 시맨틱 / false sense of security / 운영 영향은 reviewer/critic 영역 |
| #35 | Adversarial audit 결과 false alarm 비율 = 시스템 성숙 지표 | 5/10 5 audits 중 4건 false alarm — 학습 #13~#33 누적 효과로 시스템 견고. 다음 audit 사이클 ROI 체감 예상 |
| #36 | PROGRESS.md "펜딩" 항목 직접 검증 필수 | 5/10 Wave 3 시도 — iCloud 펜딩 (이미 wired) / universe 페이지네이션 펜딩 (5/5 c8b71c1 라고 git log 에 적혔으나 실제 수동 트리거 시 여전히 60종목) — PROGRESS 자체 stale 가능. 추측 금지 + 직접 grep/sqlite/실행 검증 |
| #37 | debugger git log 신뢰 X — 실데이터 검증 | 5/10 universe debugger 1차: c8b71c1 fix 결론. 실제: KIS API 30건 한계 + 페이지네이션 자체 없음. 2차: 공식 샘플 + 실제 API 호출로 진짜 root cause 발견 |
| #38 | 자동화 ROI 평가는 워크플로 분석 후 | 5/10 옵션 A2 (KR_DEEPSEARCH 자동화) 추천 시 현재 워크플로 ("KR_DEEPSEARCH.md 보고 진행" 한 줄) 분석 안 함 → 잘못 추천. 사용자 질문 "장점이 있어?" 로 정정. 자동화 제안 전 현재 단계 시간 측정 필수 |
| #39 | MCP 노출 ≠ 인프라 — context 부담 누적 | 5/11 backfill 인프라 디자인 시 사용자 질문 "MCP 추가하면 Claude.ai 무거워지자나" 로 자동 catchup 우월성 발견. 자동화 = 봇 자율 운영 + Claude.ai context 0 영향. MCP 노출은 명확한 사용자 trigger 필요한 도구만 (set_alert, manage_watch 등). 백필/정비/모니터링은 봇 내부 |

---

## 📜 5/9 세션 (오전) — PTB v20+ days= 매핑 시스템 버그 일괄 fix

### 🚨 사용자 신고 + 즉시 진단

사용자: 텔레그램 SAT_PORT_CHECK 알림 사진 + "이거 금요일에 자꾸 날아오는데"

**근본원인 (5분 진단)**:
- `python-telegram-bot >= 20.0` 부터 `JobQueue.run_daily(days=...)` 매핑이 변경됨:
  - **이전 (v19 이하)**: `0=mon, 1=tue, ..., 6=sun`
  - **이후 (v20+)**: `0=sun, 1=mon, ..., 5=fri, 6=sat`
- 검증: `JobQueue._CRON_MAPPING == ('sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat')`
- 코드는 v19 매핑으로 작성. v21.10 사용 중 → **모든 잡이 1일 일찍 발사**.

**증거 데이터**:
1. SAT_PORT_CHECK = `days=(5,)` → v20에서 'fri' = 금요일 발사 (사용자 사진)
2. `daily_collect_job = days=(0,1,2,3,4)` → 'sun-thu' = **금요일 데이터 누락**. `daily_snapshot` 5/8 (Fri) 0건 / 4/24 (Fri) 일부.
3. `weekly_us_harvest = days=(6,)` → 'sat' = 토 03:00 (의도: 일 03:00) — 1일 빠름.
4. 36개 `run_daily` 잡 중 33개 영향 (3개는 `days=` 없음 또는 전체일).

### 일괄 fix (commit 미정)

`main.py` 5216~5276 영역 6단계 replace_all (충돌 회피 순서):

| Step | Before | After | Count | 의도 |
|------|--------|-------|-------|------|
| 1 | `(1,2,3,4,5)` | `(2,3,4,5,6)` | 2 | 화-토 (us_summary) |
| 2 | `(0,1,2,3,4)` | `(1,2,3,4,5)` | 19 | 평일 |
| 3 | `(0, 1, 2, 3, 4)` | `(1, 2, 3, 4, 5)` | 2 | 평일 (pension) |
| 4 | `(0,)` | `(1,)` | 1 | 월 (universe_update) |
| 5 | `(6,)` | `(0,)` | 10 | 일 (weekly 잡 9종 + sunday_30) |
| 6 | `(5,)` | `(6,)` | 2 | 토 (weekly_review, sat_port_check) |

미변경: `(0,1,2,3,4,5,6)` 2건 (us_ratings + event_d1, "전체" 의도).

**검증**:
- `python3 ast.parse` ✅
- verifier 독립 검증: APPROVE / Confidence high / Blockers 0
- venv PTB v21.10 + `_CRON_MAPPING` 매핑 직접 확인
- 봇 재시작 (launchctl kickstart -k) → 새 PID 정상 boot, /health OK

**5/8 백필**: 토요일 KIS API `inquire-price` 500 무한 retry → 보류. 월요일 정상화 후 재시도.

### 학습 #31 — 의존성 메이저 버전 업그레이드 시 break-change 매핑 검증 필수

PTB 19→21 메이저 업그레이드 시 `days=` 매핑 컨벤션 변경. requirements.txt `>=21.10` 만 보고는 모름. 핵심 시그니처가 바뀌면:
- 라이브러리 release notes 정독 (특히 `versionchanged` 마커)
- `_CRON_MAPPING` 같은 내부 상수 직접 import 후 sanity 검증
- 실데이터로 1일치라도 비교 (`daily_snapshot` 영업일 누락 = 시스템적 day-shift 신호)

학습 #28 (잡 실행 카운트 ≠ 데이터 품질)의 변형: **잡이 매일 실행되는 것처럼 보여도 매핑이 1일 밀리면 영구 결함**. 사용자 알림 이상 (사진 첨부) 같은 외부 신호가 가장 먼저 감지함 — 코드 검증보다 빠름.

---

## 📜 5/8 세션 — 봇 점검 7+1 fix + 발굴 + 딥서치

### ⓪ 사용자 점검 요청 → 사고 7건 발견

`/dash` 발굴 시도하다 **봇 데이터 파이프라인 큰 사고 발견**. 이전 점검은 잡 실행 카운트만 보고 데이터 품질 검증 누락 (학습 #13 재현).

### ① pykrx 1.2.4 → 1.2.8 (8e7fbdc)
- pension_collect 5/6/5/7 평일 saved=0 침묵 (pykrx 1.2.4 KRX 응답 컬럼 변경 호환 깨짐)
- `pip install --upgrade pykrx` + requirements.txt `>=1.2.8`
- KRX 인증은 정상 (가설 오진), pykrx 라이브러리 버전 문제

### ② _safe_send 헬퍼 + Markdown parse fallback (6b47c28)
- 매크로 대시보드 14건+ "Can't parse entities" 발송 실패 (1주일 사용자 알림 누락)
- 매수감시 / d1_alert 동일 패턴
- `_safe_send(context, text, parse_mode="Markdown")` — 1차 Markdown / 2차 plain text fallback
- 3곳 적용 (다른 40+ send_message 호출은 보존)

### ③ DART FK + wise NoneType (d662b69)
- 090740 코오롱생명과학 stock_master 미등록 → FK 위반
- wise 인텔리안테크 매일 None.strip() 에러
- INSERT 전 stock_master 존재 확인 + `(item.get(...) or "").strip()` 가드

### ④ _exec_us_ratings friendly error (0ab8ee1)
- ticker 누락 시 traceback → friendly `{"error": "..."}` 응답
- ticker 시그니처 default = ""

### ⑤ Silent failure escalation 헬퍼 (a35b691, 학습 #27 첫 실증)
- `_track_silent_failure / _reset_silent_failure / _alert_silent_failure` (24h cooldown)
- daily_pension_collect 평일 saved=0 3회 연속 시 텔레그램 escalate
- pension_collect 침묵 사고 재발 방지

### ⑥ 🚨 daily_snapshot derived 컬럼 영구 결손 fix (6fee418) — **이 세션 최대 사고**
사용자 "기능 다 정상이라며 전부확인해" 지적 정확. 발굴 시도 중 발견:
- **fscore 14, fcf 2, mscore 0, consensus_target 0, foreign/inst_net_amt 0** (4/15부터 약 한 달 영구 0)
- **원인 3건**:
  1. update_all_alpha_metrics — count >= 500 임계값. 1Q26 분기 분산 (202603=19, 202512=485) → 둘 다 미통과 → MAX(202603) 19종목만
  2. _update_consensus — db_collector.py:749 주석 처리, **함수 미구현**
  3. KIS FHPTJ04160001 종목별 금액 0 응답 (PROGRESS 4/15 메모 알려진 한계)
- **fix 3건**:
  1. update_all_alpha_metrics per-ticker mode (종목별 가용 최신 분기 자동 선택)
  2. `_update_consensus_in_snapshot` 신규 (consensus_history → daily_snapshot)
  3. `_update_supply_in_snapshot` 신규 (pykrx 1.2.8 종목별 외인/기관 매매)
- **복구**: fscore 14→507, fcf 2→463, consensus 0→509, foreign 0→2,497, inst 0→2,311
- verifier APPROVE (Confidence high, Blockers 0)

### ⑦ KR 발굴 + 풀 딥서치 1건
- daily_snapshot 정상화 후 RR 매트릭스 발굴 → Tier 1 종목 선별
- **064400 LG씨엔에스 풀 딥서치** (thesis v1 작성 후 사용자 보강):
  - 3-Gate 3/3 통과
  - 1Q26 OP +19.4%, NI +41.8% (시장 컨센 부합/상회)
  - AX/RX/CBDC 3축 신성장 (Palantir/OpenAI, 피지컬웍스, Stable Coin)
  - 캡티브 50.9% (LG전자 25.1%, LG화학 20%) + 외부 비계열 회복
  - PE 13.5 vs Peer 평균 23.9 (-43%), ROIC 32.6%
  - 신용등급 AA (2025 상향)
  - 감시가 65,000원 RR 3.71 (Starter 3~5%), 60K 도달 시 RR 4.14 (2차 트랜치)
  - Bear case: LG지주 1Q -37% 미달 → 그룹 IT 투자 둔화
  - Kill Switch: 클라우드&AI 60% 이하 / 캡티브 두 자리수 (-) / 컨센 미스 2분기 연속

### ⑧ Tier 1 후보 9종 PDF 일괄 수집 (604건)
- 삼성화재(91)/HL만도(95)/한국타이어(87)/현대모비스(93)/카카오페이(54)/휴젤(92)/크래프톤(92) 신규
- 삼양식품/파마리서치는 DB 보유 (dedup 0건)
- Claude.ai Project 진행 준비 완료

### 🎯 5/8 커밋 6건 (8e7fbdc → 6fee418), data/thesis/064400_LG씨엔에스.md 신규

---

---

## 📜 5/5 세션 — 운영 안정화 + Shadow 버그 사고

### ⓪ 자동 잡 5건 검증 + 버그 발견
- 4/27 첫 자동 실행 시 weekly_us_analyst_sync `KeyError: 'auto_watched'` 실패
- 5/2 텔레그램 false positive 2건: "5/1 누락" + "재무 30분 타임아웃"
- 5/5 사용자 지적: 워치 변화 알림 18건 + SK하이닉스 "이평선 수렴 -0.2%"

### ① weekly_us_analyst_sync 키 미스매치 (5c88061)
- db_collector 반환 `auto_watched_a`, `tier_s_count`, `criteria` vs main.py 참조 `auto_watched`, `min_stars`, `min_calls`
- main.py 메시지 포맷 정정 + Tier S 카운트 + criteria 노출 추가

### ② weekly_sanity 휴장일 + weekly_financial 타임아웃 (309dbd9)
- `_KRX_HOLIDAYS` frozenset (2026 13개 공휴일) + `_is_krx_business_day()` 헬퍼
- weekly_financial 30분 → 60분, 결과 dict 분해 (IS/BS/DART 카운트)

### ③ KRX 공휴일 갱신 알림 자동화 (5f009b8)
- 매주 일요일 weekly_sanity 안에서 당해 등록 < 8건 시 텔레그램 알림
- 2027년 1월 첫 일요일부터 자동 발동

### ④ watch_change_detect 임계값 강화 (99016ba)
- 감시가 근접: 5% → 2%
- 이평선 수렴: `abs<3` → `abs<1.5 AND change_10d<0` (실제 수렴 중인 종목만)
- 외인 매수 전환: 5d≥60% → 5d≥70%
- 5/4 SQLite 검증: 전종목 2756 중 812(29%) → 168(6%) 통과

### ⑤ 🚨 load_krx_db shadow 버그 (5165971) — 한 달 stale 데이터 사고
- **사용자 지적이 정확** ("데이터 이상한 거 같다")
- krx_crawler.py L17: `from db_collector import load_krx_db` (SQLite)
- krx_crawler.py L511: `def load_krx_db(...)` ← 무조건 재정의 (레거시 JSON, 4/7 마지막)
- `from krx_crawler import load_krx_db` 가 final namespace의 L511 정의를 받음
- main.py 3곳 (`watch_change_detect` 등)이 4/7 데이터 보고 알림 발송
- 수정: L511 def를 `if not _USE_SQLITE:` 가드 안에 배치
- 검증: SK하이닉스 ma_spread 알림값 -0.2% (4/7 stale) → 실제 +33.83% (5/4)

### ⑥ AMD watchalert 정리 + wording (ea5d8b7)
- 매도 후 AMD watchalert 잔존 → 노이즈. 제거.
- 매도 트리거 메모는 `data/thesis/AMD.md` 보존 (재매수 후 stoploss/target 시스템 사용)
- "도달!" → "≤ ${buy_price} ({gap:+.1f}%)" + 헤더 "진입!" + 부제 "(현재가가 매수희망가 이하로 진입)"

### ⑦ shadow 가드 + 레거시 정리 (c9b6004)
- `_load_history` / `scan_stocks` 동일 shadow 패턴 (prod 영향 X, 잠재 trap)
- 모듈 끝 `if _USE_SQLITE: from db_collector import ... as ...` export alias
- 검증: `krx_crawler.{load_krx_db, _load_history, scan_stocks} is db_collector.{...}` 모두 True
- `data/krx_db/` 디렉토리 삭제 (232 JSON, 1GB 회수, prod 사용 X)

### ⑧ Silent failure 전수조사 + 2건 발견/수정 (c8b71c1)
사용자 "할 거 더 있어?" 질문에서 디스크 분석 → 다음 발견:
- **weekly_universe_update 60종목 (3주째 stale)**: KIS market-cap API tr_cont 응답값이 어느 시점 "M" → "F" 변경. 코드 `!= "M"` → 첫 페이지 30종목만 받고 break. 가드(<100) 발동으로 silent. 수정: `not in ("F", "M")`.
- **weekly_financial Phase A hang (60분 또 타임아웃)**: shared session 30s/콜 + 한 종목 hang 시 전체 막힘. 진행 로그 200건마다 + 버퍼링 = 가시성 0. 수정: per-ticker `wait_for(10s)` + 진행 로그 50건마다 + flush=True + Phase 시작/완료 elapsed 노출.

### ⑨ Stale 파일 정리
- `reports.json` 1.5MB (SQLite 전환 후 dead, read/write 호출 0건)
- `sector_sample_350.json` 80KB (코드 참조 0건)
- `krx_cookies.json` 68B (Safari 시절 legacy)
- 합 ~1.6MB. 빈 *.json 4개 (compare_log/sector_flow_cache/us_watchlist/regime_transition_sent)는 활성 사용 중이라 보존.

### 🎯 5/5 커밋: 9건 (5c88061 → c8b71c1) + 로컬 정리 2건 (krx_db 1GB + stale 1.6MB)

---

## 📜 이번 세션 (4/24~4/26) 큰 작업 종합

### ① daily_collect 자가진단 (4/25) ✅
- 4/24 18:30 미실행 사건(원인 미확정) 대응
- 평일 19:15/20:15/21:15/22:15 네 번 자가진단 → 0건 시 재실행
- `daily_collect_sanity_check` 함수 (main.py)

### ② US 애널 마스터 자동 sync (4/25) ✅
- 1,902명 ratings 데이터 vs 마스터 13명 갭 복구
- `sync_us_analyst_master` (db_collector) + 일요일 04:00 자동
- 결과: 마스터 13→1,902명, watched 12→254명

### ③ 3-Tier 시스템 (4/25) ✅
- avg_return 컬럼 추가 + `is_tier_s_analyst()` 런타임 분류
- **Tier A** (watched=1): 별점≥4.0 AND 적중률≥60% AND 콜≥10 OR 잠수형 거장 (4.8/80/7)
- **Tier S** (런타임 31명): ① 활발 톱 ② 잠수형 거장 ③ 고수익 거장(Goldsmith UBS +265%)
- 차등 알림 (🚨🚨🚨 / 🚨🚨 / 🚨 / ⚠️)

### ④ get_us_buy_candidates (4/25) ✅
- 톱애널 추천 + TP 업사이드 충족 미국 매수 후보 raw 데이터
- 기본 180일/1명+/+20%/limit 50 → ~50종목 sweet spot
- 정렬·필터·해석은 LLM이 동적 (점수제 박지 않음)
- 검증: SARO +36% / WWD +22% / BIIB +25% (Tier S+A 강함)

### ⑤ FMP 통합 (4/26) ✅
- "왜 그 TP인가" 본문 답
- `fmp_earnings_transcript`: 분기 5만자 (CEO 가이던스 + 톱애널 Q&A)
- `fmp_price_target_summary`: 1m/3m/1y 평균 TP + 카운트
- `fmp_analyst_estimates`: 매출/EBITDA/순이익 향후 5년
- `fmp_stock_grades`: 증권사 등급 변경 이력
- MCP 도구 2개 추가 (`get_us_earnings_transcript`, `get_us_analyst_research`)
- 무료 250 calls/day (보유/워치 충분)
- `.env FMP_API_KEY` 설정 완료

### ⑧ 외부 시그널 + 연기금 (NPS) 자동 추적 (4/27 저녁) ✅
- **Polymarket + Treasury Curve** (4/27 오후):
  - `fetch_polymarket()`: 매크로/지정학/정치 prediction market (24h $500K+ 노이즈 컷)
  - `fetch_treasury_curve()`: FRED API 10Y/2Y/3M (Estrella-Mishkin 1998 침체 시그널)
  - `fetch_external_macro_signals()`: 통합
  - MCP `get_polymarket` + `get_macro_external` 도구
  - 매크로 대시보드 (06:00, 18:55) `_format_external_signals` 자동 첨부
  - SAT_PORT_CHECK Phase 1 매크로 8변수 (Fed 인하 확률, 10Y-2Y 추가)
  - SUN_DISCOVERY Phase 1 mispricing 후보 (컨센 vs Polymarket 차이)
  - `daily_event_d1_alert` 19:30 평일 (FOMC/어닝/매크로 매칭 시 Polymarket+Treasury 첨부)
- **연기금 (NPS) 종목별 양방향 매매 추적** (4/27 저녁):
  - KRX 정보데이터시스템 인증 (KRX_ID/KRX_PW .env 설정 완료)
  - pykrx auto-login → 연기금 단독 매매 데이터 fetch
  - `pension_flow_daily` 테이블 (영구), 4/17~4/27 백필 완료
  - `daily_pension_collect` 16:30 평일 + `daily_pension_alert` 19:00 평일
  - 알림: 시총 대비 % 기준 정렬, 절대금액 보조 표시
  - 4 섹션: 보유 양방향 / 워치 양방향 / 발굴 매수 TOP10 (시총%) / 발굴 매수 TOP10 (절대금액)
  - 너 포트/워치 외 = **매수 시그널만** (매도는 무의미)
  - MCP `get_pension_flow(days, market, top, held_watch_only)`
  - SAT_PORT_CHECK / SUN_DISCOVERY Phase 1 명시
- **컨센 누적 trend 감지 보강** (4/27):
  - 단일 일 5% 임계 → + 15일 누적 3% 추가 (점진 상향 캐치)
  - 효성중공업 +3.0%/2주 같은 경우 잡힘 (이전엔 누락)
  - 30%+ 변화 = corporate action 노이즈 컷
  - 단일 changes 중복 제거
- **주말 루틴 v2 텔레그램 알림** (4/27):
  - SAT_PORT_CHECK / SUN_DISCOVERY 파일 신설 (data/)
  - 토 09:00 / 일 09:00 알림 (Claude.ai 프롬프트 템플릿)
- **커밋**: 245d094 / 56867cc / a022d4f / df5ecfb / c43e451 / a9a54e8 / 1b86d93 / 0a0c4b3

### ⑦ 비종목 리포트 카테고리 풀구축 + 노이즈 필터 (4/26 저녁) ✅
- **DB 스키마**: `reports.category` 컬럼 + 인덱스. 기존 3,356건 = 'company'
- **신규 4 카테고리**: industry / market / strategy / economy (네이버+한경 무로그인)
  - 한경 페이지네이션 (5페이지, 100건 cap 해제) — industry 419건, market 234건 누적
  - `_IND_/_MKT_/_STR_/_ECO_<sha1[:10]>` 합성 ticker (UNIQUE 충돌 회피)
- **실측 1주일 정독 (4/20~4/26 산업+전략 37건)** 후 정밀 노이즈 필터:
  - `_NOISE_RULES`: 시장 모닝브리프 + 유진투자증권 News Comment + 키움 시황/FICC Daily + 대신 퀀틴전시 플랜
  - `_is_noise()` 헬퍼 — 수집 단계 SKIP
  - 한경 EC 파싱 버그 수정 (td[1] 카테고리 라벨 cell 감지)
  - dedup (date+source+title) — 35건 중복 제거
- **결과**: 1주 168 → 107건 (876K 토큰, Claude.ai 1M 안전)
- **MCP 확장**: `manage_report(category=, days=, ...)` 다중 카테고리 + 카테고리별 collect
- **위클리 알림 (4/26 추가)**: 매주 일요일 19:07 `weekly_report_digest_notify` 잡 — 통계 + Claude.ai 프롬프트 템플릿 텔레그램 push. 봇 판단 X (사용자 직접 Claude.ai 호출). 첫 자동 발송 4/27(일) 19:07.
- 커밋: f401d9d / 8d7112e / 7aaae48 / e5f0746 / f26e01c

### ⑥ KR_EXIT.md + US_EXIT.md 매도 프레임 신설 (4/25~4/26) ✅
- **US_EXIT.md** (4/25, 30.7KB): 미국 매도 판단 프레임 (Martineau 2022 PEAD 대형주 소멸, FactSet Sell 4.8% 희소성, IRS LTCG/STCG 22%p 격차, Munger 1994 USC 정확 인용)
- **KR_EXIT.md** (4/26, 33.8KB): 한국 매도 판단 프레임 (KCMI 2026 김준석 한국 TP 정보가치 소멸, Choe-Kho-Stulz 1999 외국인 destabilize 부정, 거래세 2025 0.15%/2026 0.20% 정정)
- 공통: LLM 편향 차단 10규칙 (Sharma+Laban+Huang+Li 4중 차단), 3경로 의사결정 트리 (Fisher 1958 Ch.6), 학술 강도 4단계 라벨
- **SK하이닉스 EXIT 1호 실전 적용** (4/24): 3주 +51% 상황에서 3경로 0/3 + O'Neil 8-week hold 강제 발동 → HOLD 전량. 목표가 1,310K→1,700K 상향, Trailing Stop 912K 신설
- **LS ELECTRIC 케이스 진단**: LLM 4중 편향(Sharma+Laban+Huang+Li) 합성으로 4/17 조기매도 추천 → +47~84% 추가 상승 놓침 사례 학술 진단

### 🎯 MCP 도구 카운트: 39 → **46개**
- get_youtube_transcript (40, 4/24)
- get_us_buy_candidates (41, 4/25)
- get_us_earnings_transcript (42, 4/26)
- get_us_analyst_research (43, 4/26)
- get_polymarket (44, 4/27) — Polymarket 매크로/지정학 베팅
- get_macro_external (45, 4/27) — Polymarket + Treasury 통합
- get_pension_flow (46, 4/27) — 연기금 종목별 양방향 매매

---

## 📌 미국 애널 레이팅 — 추가 발견 엔드포인트 (메모, 2026-04-18)

StockAnalysis.com 1단계 구축 중 탐색으로 발견. **1단계 스코프 포함 안 함, 2~3단계 참고용 기록.**

**✅ 작동 확인**
- `/api/symbol/s/{ticker}/overview` — 시총/PE/FwdPE/EPS/컨센타겟/어닝일
- `/api/symbol/s/{ticker}/statistics` — 밸류에이션/shares
- `/api/symbol/s/{ticker}/dividend` — 배당 이력
- `stockanalysis.com/analysts/{slug}/` HTML — 애널 커버 종목 리스트

**❌ 작동 안 함 (다른 경로 있을 수도)**
- /financials, /forecast, /insider, /institutional, /options, /short-interest

**활용 계획**
- 2단계: `/overview` → `get_us_ratings(mode="overview")` 통합. 딥서치 Step 1/6 자동화
- 3단계: 애널 HTML 파서로 톱 100명 커버 종목 리스트 구축

---

## ✅ 미국 애널 레이팅 3단계 완료 (2026-04-23)

커밋 75e0498 / 56a4bcc / 47cb16a — 5 Unit 전부 완료.

**완료 사항:**
- **Unit 1 (75e0498)**: DB 스키마 us_analysts + us_analyst_coverage (10+4 컬럼, 인덱스 3)
- **Unit 2 (56a4bcc)**: HTML 파서 4함수. mark-strouse 실측 OK (11종목 coverage)
- **Unit 3 (47cb16a)**: discovery 본 구현 (watched=1 톱 애널 상향 3건+ 종목)
- **Unit 4 (47cb16a)**: firm/sector 필터 (기존 stub 교체)
- **Unit 5 (47cb16a)**: weekly_us_analyst_report 일요일 19:00 KST
- **신규 MCP 도구 watch_analyst**: 톱 애널 확정/해제 (38→39개)

**다음 운영 단계:**
1. `build_top_analysts_candidates()` 호출로 톱 100 후보 리스트 생성
2. HTML 파서로 각 애널 메타 수집 (약 30~60분)
3. 운영자가 `watch_analyst(slug, watched=True)` 로 70~100명 확정
4. 이후 discovery 자동 가동

---

## 🟢 중장기 TODO (TODO_dev.md 참조)

- **P2 Tier 1 알파**: F-Score/M-Score, FCF 메트릭
- **P2.5 Tier 2**: 관세청 10일 수출, 거버넌스/밸류업
- **P3**: ~~뉴스 감성 개선~~ ✅, DB 변화 감지, 공시 실시간화

---

## 📌 주요 아키텍처 결정 (최근)

| 날짜 | 결정 | 이유 |
|------|------|------|
| 2026-04-15 | Railway 삭제 + main 직행 배포 + HANDOVER 폐기 | 1인 운영 봇 구조 단순화. 중복 발송 원인 제거. |
| 2026-04-15 | 에이전트 3개 추가 (critic/verifier/debugger) | OMC 프롬프트 패턴 차용 |
| 2026-04-16 | 워치리스트 단일화 (watchalert.json) | 3파일 파편화 26종목 불일치 해결 |
| 2026-04-16 | KR_DEEPSEARCH.md 신설 (10 Step + PDF 게이트) | US_DEEPSEARCH와 대칭, Step 생략 방지 |
| 2026-04-17 | F/M/FCF 완전 가동 + DART 증분 자동화 | shares_out 24,310건. 우량 7+ 552종목(22%). 02:00 daily 스케줄. |
| 2026-04-18 | 뉴스 감성분석 KNU 사전+구문보강 (97%) | 단순 키워드 → 점수 기반+양보절 제외. 192케이스 66%→97%. |
| 2026-04-18 | US 애널 레이팅 MCP 3종 1+2단계 | StockAnalysis.com. 실시간 감시 ET 12:00/16:30. 13/13 테스트. |
| 2026-04-19 | 거버넌스/밸류업 전체 롤백 | 후행지표 판단. "간판만 비슷" 알파 없음. TODO 착수 전 선행/후행 판단 교훈. |
| 2026-04-21 | US 레이팅 오탐 근본 수정 (d1b2c1d) | `fetched_at` → `rating_date` 필터. 첫 수집 수개월치 오탐 방지. |
| 2026-04-23 | 주간 US 유니버스 수집 잡 (12cf948/975ef5d) | S&P 500 + Russell 1000 합집합 1,010종목 × 주 1회 일요일 03:00 KST. |
| 2026-04-23 | INVESTMENT_RULES v6 레짐 개정 (6e2c6f9) | 레짐 = 현금 관리 도구로 역할 재정의. 🟢 신규자제 조항 삭제. 현금 🟢 5~8%. "현금은 비용" 원칙. |
| 2026-04-23 | judge_regime v6 동기화 (a5cf996) | 4단계→3단계(🟢/🟡/🔴). 판정 지표 **S&P 200MA + VIX 2개만**. |
| 2026-04-23 | 치명 KST 스코프 버그 수정 (42f3a14) | 604d775 도입 버그. Python 로컬 스코프 교훈. |
| 2026-04-23 | 대시보드 인증 + 편집 기능 (2d0ae78) | Cloudflare Access Gmail PIN, TODO 토글 + 투자판단 입력. TODO_dev.md P1 완료. |
| 2026-04-23 | critic hotfix XSS 차단 (8f58f8c) | `_inline()` href XSS 차단(스킴 화이트리스트), 코드블록 검사. E2E 6/6 PASS. |
| 2026-04-23 | 관세청 수출 모듈 완전 롤백 | 2일 공수 구축 후 제거. 발굴 부적합, 동행/후행, 어거지 호출 유혹. 4/19 거버넌스 패턴. |
| 2026-04-24 | DART 수시공시 본문 조회 + 알림 요약 (f1969d5) | get_dart MCP 2종 추가. 캐시 + path traversal 차단. 단위 6/6 + 라이브 3/3 PASS. |
| 2026-04-24 | INVESTMENT_RULES v6 전면 개정 + 정합성 동기화 (ac1f049/7639ec3) | 확신등급 폐기 → 3-Gate + 비중 3단계. F-Score ≥8, 환각 수치 삭제. KR/US_DEEPSEARCH 동기화. |
| 2026-04-25 | US_EXIT.md v1 신설 (30.7KB) | 미국 매도 프레임. PEAD 대형주 소멸(Martineau 2022), Sell 4.8% 희소성, LTCG/STCG 22%p, LLM 4중 편향. 보유 5종목 Kill Switch. |
| 2026-04-26 | KR_EXIT.md v1 신설 (33.8KB, commit 20d781d) | 한국 매도 프레임. KCMI 2026 김준석 TP 소멸, Choe-Kho-Stulz 1999 외국인 destabilize 부정, 거래세 2025 0.15%/2026 0.20% 정정, 이승희 KDISS→KDAS 정정, Munger 한국 부적합. SK하이닉스 EXIT 1호 적용 → HOLD 전량 + 목표가 1700K + Trailing 912K. |
| 2026-05-05 | load_krx_db shadow 버그 + 운영 안정화 + silent 전수조사 (5c88061~c8b71c1, 9커밋) | krx_crawler.py L17 try-import 후 L511 def 재정의 → main.py 3곳이 4/7 JSON 보던 사고. _USE_SQLITE 가드로 fix. 동일 패턴 _load_history/scan_stocks 도 export alias 가드. **Silent 전수조사**: weekly_universe_update KIS 페이지네이션 헤더 (M→F 변경) 3주 stale + weekly_financial 60분 또 타임아웃 (per-ticker wait_for 추가). 부수: weekly_us_analyst_sync KeyError, weekly_sanity 휴장일, watch_change 임계값 강화, AMD watchalert 정리. legacy 정리: krx_db 1GB + stale 3 files 1.6MB. |
| 2026-05-05 | Dashboard 분리 (f93abb6) + Silent failure 헬퍼 (a35b691) | main.py 9197→5279줄, dashboard.py 3966줄 신규 (35함수 + 4상수 + register_routes). paste only 회귀 0. verifier APPROVE. silent_failure_log + _track/_reset/_alert 헬퍼, pension_collect 적용. |
| 2026-05-08 | 봇 점검 7건 fix + daily_snapshot derived 영구 결손 fix (8e7fbdc~6fee418, 6커밋) | 사용자 발굴 요청 → derived 컬럼 4종 한 달 영구 0 발견. update_all_alpha_metrics per-ticker mode + _update_consensus_in_snapshot/_update_supply_in_snapshot 신규. fscore 14→507, consensus 0→509, foreign 0→2497, inst 0→2311. 부수 fix: pykrx 1.2.4→1.2.8, _safe_send Markdown fallback, DART FK 가드, wise NoneType, _exec_us_ratings friendly error. KR 발굴 + LG씨엔에스 풀 딥서치 v1 (3-Gate 3/3, 65K 감시가 RR 3.71). Tier 1 후보 9종 PDF 604건 수집. |

---

## 🧠 최근 세션 학습 (Lessons learned)

1. **API 응답 필드는 전수 검토할 것** — `whol_loan_rmnd_rate` 이미 Phase 1에 있었는데 모르고 Safari fetch 만듦.
2. **"죽은 코드" 판단 전 데이터 성숙도 체크** — short_squeeze는 코드 정상, 과거 데이터 0이라 일시적 빈 결과.
3. **사용자 지적 신뢰** — "KRX Safari 대체됐던 거 같은데" 기억이 정확. 재검증으로 2,357줄 청소.
4. **팀 구조 원칙 지키기** — Opus가 직접 구현 안 하고 Sonnet 에이전트에 위임.
5. **"맥미니 = 다른 서버" 편향 주의** — 워크트리가 본체임을 잊고 "배포 필요"라 오판.
6. **문서는 복붙 템플릿 + 킬 조건 없으면 Step 생략됨** — KR_DEEPSEARCH 초판은 설명문만 → 건너뜀. US 패턴 차용.
7. **리뷰 2중 체제의 가치** — code-reviewer + critic 병렬로 치명 6건 캐치.
8. **DART API 한도는 stockTotqySttus가 더 빡빡** — 4/16 status=020 한도초과 발견.
9. **DART CF 직접법 회사는 감가상각 노출 안 됨** — 삼성/SK하이닉스/현대차. M-Score DEPI 계산 불가.
10. **Python stdout 버퍼링 함정** — nohup -u 했는데도 line buffering 끊김. DB 카운트 polling이 더 신뢰.
11. **한 함수 버그가 여러 경로 파급** — `kis_investor_trend_history` 1곳 수정으로 둘 다 복구. grep 필수.
12. **KIS API 응답 스키마가 조용히 바뀜** — 공지 없이 변경. 주기적 스모크 테스트 필요.
13. **"수집 성공 but 0값" 함정** — NULL/0 구분 필수. `SUM(CASE WHEN col=0)` 모니터링 포함.
14. **에이전트 합의 ≠ 정답** — WS WRONG_VERSION_NUMBER 오진. 사용자 반문으로 재조사. 직접 테스트로 검증.
15. **collect_daily() 과거 backfill 불가** — 현재가 API라 date 무시. 일봉 API 별도 필요.
16. **0 vs NULL 구분 필수** — turnaround 211→113건 정상화. 사전 분포 검증.
17. **기존 헬퍼의 미사용 탐지** — `_get_session()` 63개 호출 미사용. grep 검증 우선.
18. **TODO에 있다고 구현 금지 — 알파 원천 먼저 판단** (4/19 거버넌스 롤백). 선행/후행 판단 + 가치 중복 체크.
19. **유튜버/블로거 인용 주장은 실증 없으면 믿지 마라** (4/23 관세청). 구현 전 피어슨 n≥18 통계 검증.
20. **"선행"과 "동행" 구분 필수** (4/23 실증). DRAM/HBM r=0.93 lag=0 동행. 선행 알파 X.
21. **Python 로컬 스코프 버그** (4/23 KST). 모듈 전역과 동일 이름 로컬 할당 금지.
22. **MCP 도구 존재 = 어거지 호출 유혹** (4/23 관세청). Slovic 1973: 정보 늘리면 확신만 2배.
23. **UI 렌더링 함수 = 잠재적 Stored XSS 벡터** (4/23 `_inline()`). 파일 쓰기 추가 시 렌더 경로 전수 조사.
24. **🆕 LLM 매도 판단 4중 편향 합성 효과** (4/26 KR_EXIT 학술 진단): LS ELECTRIC 4/17 조기매도 추천 = Sharma 2023(sycophancy) + Laban 2023(FlipFlop -17%p) + Huang 2023(intrinsic reflection) + Li 2025 FINSABER(bull-market 조기매도) **4중 편향 동시 작동** 표본. 결과 +47~84% 추가 상승 놓침. 교훈: **매도 판단 LLM 출력은 4중 편향 체크 후에만 채택**. 본 세션 SK하이닉스 판단 중간 FlipFlop 1회 발생, 사용자 지적으로 첫 판단 HOLD 복귀 — 학술 진단대로 발현. KR_EXIT/US_EXIT의 Section 1 LLM 10규칙은 4중 편향 차단 장치.
25. **🆕 Import shadow trap — try-block import 후 같은 모듈 def는 final namespace 점유** (5/5 load_krx_db 사고): `from X import f` (try-block) + `def f(...)` (모듈 본문) → 외부에서 `from this_module import f` 했을 때 두 번째 def가 받아짐. krx_crawler.py L17 SQLite import + L511 legacy JSON def → main.py 3곳이 한 달 stale 4/7 데이터 사용. 검출 신호: 사용자 "데이터 이상한 거 같다" 지적. 방어: ① re-export하는 모듈은 def를 `if not _<flag>:` 가드 안에 배치, ② 또는 모듈 끝에 export alias 강제 (`if _flag: from X import f as f`). 신규 외부 의존 함수 추가 시 같은 모듈에 동명 def 없는지 grep 필수.
26. **🆕 사용자 지적 즉시 신뢰 #2** (5/5): "데이터 이상한 거 같다" 한 마디로 한 달 stale 발견. 학습 #3 (KRX Safari 대체 기억) 재확인. 패턴: 직관적 이상 + 구체적 수치(SK하이닉스 -0.2%) → 즉시 검증 우선.
27. **🆕 Silent failure 가드 자체가 새 silent failure 만든다** (5/5 universe + financial): "60종목 < 100 → 기존 유지" 가드는 정상 작동했지만 **알림 없이 stale 3주 방치**. "타임아웃" 메시지는 떴지만 반복돼서 둔감화. 가드/타임아웃 추가 시 **N회 반복 시 텔레그램 알림** 또는 **stale 일수 표시** 같은 visible escalation 필요. 단순 print/silent skip은 "알림 인플레이션"으로 시그널 묻힘. 학습 #10 (stdout 버퍼링) 재현 — print 200건마다 + 버퍼링이면 사실상 invisible. → 진행 로그는 50건마다 + flush=True 고정. **5/8 첫 실증 (a35b691)**: pension_collect 5/6/5/7 평일 saved=0 침묵 → 헬퍼 3종 (`_track_silent_failure` / `_reset_silent_failure` / `_alert_silent_failure`) + 24h cooldown + `silent_failure_log.json`. 3회 연속 시 텔레그램 escalate 패턴 정립.
28. **🆕 잡 실행 카운트 ≠ 데이터 품질** (5/8 derived 영구 결손, 학습 #13 진화): 5/5 점검에서 "기능 다 정상" 결론냈는데 사용자 "기능 다 정상이라며 전부확인해" 지적으로 daily_snapshot 4컬럼 (fscore/fcf/consensus/외인기관 수급) **한 달 영구 0** 발견. 잡 실행 로그 (`[Finance] Phase A 완료`) 만 보면 정상이지만 실제 채움률 검사 안 함. → 봇 점검 시 **잡 실행 카운트 + 데이터 채움률 (NULL/0/positive 분포) 둘 다** 검사 필수. 학습 #13 "수집 성공 but 0값" 재현. 정기 헬스체크 SQL 쿼리 자동화 가치 있음 (별도 작업).
29. **🆕 외부 사이트 응답 변경 → pip upgrade 먼저 시도** (5/8 pykrx): KRX 응답 컬럼 변경으로 pykrx 1.2.4 KeyError. "KRX 인증 만료" 가설 오진 → 환경변수 정상 확인 → `pip install --upgrade` 1회로 1.2.4→1.2.8 해결. 외부 라이브러리 깨질 때 **인증/네트워크/코드 의심 전에 라이브러리 버전 업그레이드 우선** 시도가 빠른 fix. requirements.txt에 minimum 버전 박아 재발 방지.
30. **🆕 발굴 도구 자동화에 데이터 품질 의존** (5/8 발굴): 발굴 시도가 derived 컬럼 결손 사고를 강제 노출시킴. **새 기능/도구 사용 = 기존 데이터 품질 검사기 역할**. 봇 운영자가 자기 도구 안 쓰면 silent 결손 못 잡음. 정기적 발굴 / 분석 / 백테스트 호출이 데이터 무결성 검사기로 작동.
31. **🆕 외부 API 통합 시 spec 검증 의무** (5/28 Gist If-Match 400): 5/27 commit 42808d6에서 GitHub Gist PATCH에 `If-Match: <ETag>` 헤더 추가. Gist API는 conditional PATCH 미지원 → 5/28 22:00 백업 400 Bad Request 발생. 사용자 텔레그램 알림으로 발견. **외부 API 헤더/파라미터 추가 시 GitHub docs 등 공식 reference 30분 spike 후 구현** 원칙 정립. "표준 HTTP 헤더라도 API별 지원 여부 다름".
32. **🆕 외부 API 호출 함수는 mock-based regression test 의무** (5/28): `tests/test_backup.py` 부재로 If-Match 400 사고가 production까지 도달. tests/test_backup.py 5개 mock test 추가 (If-Match 헤더 부재 검증 / 200 ok / 409 retry 3회 / 429 Retry-After sleep 값 / token 누락 에러). 외부 API 호출 함수 신규/수정 시 **assertion: 헤더 검증 + status code 분기 + retry semantics** mock test 필수.
33. **🆕 fix 후에도 reviewer/verifier 생략 금지** (5/28 팀 강화): 5/28 Gist fix commit 시 debugger 단독 종료 시도. 사용자 "팀으로 진행해서 확인 다 한 거야?" 질문으로 reviewer + verifier 추가 거침. **단순 버그픽스라도 CLAUDE.md "모든 코드 작업에 reviewer + verifier 필수" 룰 예외 없음**. 코드 한 줄 fix도 reviewer(코드 리뷰) + verifier(증거 기반 완료 검증) 필수.
34. **🆕 성공/실패 메트릭 정밀화** (5/28 PDF infra 학습 계승): weighted vs unweighted, success vs partial 명시 구분 의무. "5/5 PASS" 표기 시 총 테스트 수 + 커버 케이스 명시. 모호한 "완료" 보고는 verifier가 거부.
35. **🆕 벤치마크 메트릭은 운영 조건으로 측정** (5/28 DB 인덱스+cache A commit 633e31f): commit msg "196ms → 17ms (11.5x faster)" 주장 → verifier 실측 warm cache 1.28x로 미재현. 원인: developer benchmark는 cold OS-cache 조건 (sync && purge 후) 측정. 실 운영 (봇 24/7 가동 + macOS APFS page cache 항상 warm)에서는 SQLite 자체 cache 효과가 OS cache에 흡수. 학습 #4 "메트릭 conflation 회피"의 3번째 재발 (PDF 9.2% / Gist If-Match / DB 11.5x). 새 룰: ① 벤치마크는 운영 조건 시뮬레이션 (warm cache, 부하 평균)에서 측정. ② cold cache 측정 시 명시 ("cold OS cache 조건, 운영 환경에서는 1.28x 예상"). ③ commit msg에 측정 조건 1줄 포함. 또한: PRAGMA cache_size per-connection 설정은 `_get_db()` 통과 호출자에만 적용. dashboard.py / mcp_tools/tools/* 등 16+ 직접 sqlite3.connect 호출자는 미적용 → F2에서 보편화.

---

## 🛠 세션 시작 루틴

매 세션 시작 시 순서대로:

```bash
pwd                          # 1. 작업 디렉토리 확인
git log --oneline -10        # 2. 최근 커밋 훑기
cat data/PROGRESS.md         # 3. 이 파일 (다음 할 일)
cat data/TODO_dev.md         # 4. 봇 개발 TODO
cat data/TODO_invest.md      # 5. 투자 TODO (필요시)
```

이 루틴 후 사용자 요청 처리 시작.

---

## 📝 업데이트 규칙

- **세션 종료 직전**: "다음 세션에서 바로 할 일" 섹션 갱신
- **중요 결정 시**: "주요 아키텍처 결정" 표에 한 줄 추가
- **실수/교훈 발견 시**: "최근 세션 학습"에 한 줄 추가
- **작업 완료 시**: TODO_dev.md 체크 + 필요시 PROGRESS.md "다음 할 일"에서 제거
- **150줄 이하로 유지** — 오래된 결정/학습은 주기적으로 쳐낼 것
