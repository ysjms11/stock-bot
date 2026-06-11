# 데이터 파일 경로 (`/data/*.json`)

| 파일 | 내용 | 기본값 |
|------|------|--------|
| `/data/watchlist.json` · `/data/us_watchlist.json` | **폐기** — 2026-04-16 `watchalert.json` 단일 소스로 통합 (KR=`.bak`만 잔존, `scripts/migrate_watchlist.py`로 병합. US=빈 `{}` 잔재). `load_watchlist()`/`load_us_watchlist()`는 watchalert 파생 하위호환 wrapper(`load_kr_watch_dict`/`load_us_watch_dict`), watchalert.json 자체가 없을 때만 기본 종목 seed. ⚠️ `main_pkg/jobs/earnings.py:48`에 us_watchlist 직접 read 1곳 잔존(항상 빈 결과) | — |
| `/data/stoploss.json` | 손절/목표가 `{ticker: {name, stop_price, ...}, us_stocks: {...}}` | `{}` |
| `/data/portfolio.json` | 보유 포트폴리오 `{ticker: {name, qty, avg_price}, us_stocks: {...}}` | `{}` |
| `/data/dart_seen.json` | DART 알림 전송된 공시 ID 목록 `{ids: [...]}` | `{ids: []}` |
| `/data/watchalert.json` | **KR+US 통합 워치리스트 단일 소스** (매수감시 겸용) `{ticker: {name, market(KR/US), buy_price, memo, grade, created_at, updated_at, qty(선택·US 수량)}}`. `load_watchlist`/`load_us_watchlist`는 이 파일 기반 하위호환 wrapper (`kis_api/_files.py`). 레거시 `created` 필드 다수 잔존 | `{}` |
| `/data/*_sent.json` (묶음) | 알림 중복발송 방지 기록 8종 — `watch_sent`(매수감시 당일 `{ticker: "YYYY-MM-DD"}`) · `stoploss_sent`(손절 당일 횟수) · `insider_sent`(내부자 클러스터, 7일 쿨다운) · `us_holdings_sent` · `watch_change_sent` · `regime_transition_sent` · `change_scan_sent` · `macro_sent` | `{}` |
| `/data/decision_log.json` | 투자판단 기록 (날짜별 regime/grades/actions) | `{}` |
| `/data/compare_log.json` | 종목 비교 기록 | `[]` |
| `/data/watchlist_log.json` | 워치리스트 변경 이력 | `[]` |
| `/data/events.json` | 매크로 이벤트 캘린더 | `{}` |
| `/data/weekly_base.json` | 주간 리뷰 기준 스냅샷 | `{}` |
| `/data/stock_universe.json` | 종목 유니버스 (시총 상위) | `{}` |
| `/data/consensus_cache.json` | 컨센서스 캐시 (FnGuide) | `{}` |
| `/data/portfolio_history.json` | 포트폴리오 일별 스냅샷 | `[]` |
| `/data/trade_log.json` | 매매 기록 `{trades: [...]}` (최대 1000건 보관) | `{"trades": []}` |
| `/data/dart_corp_map.json` | DART 고유번호 매핑 — 1차 조회/저장 경로이나 현재 미생성. 실제 커밋 파일은 레포 루트 `dart_corp_map.json`(4642B). ⚠️ 로더 fallback이 패키지화로 `kis_api/`를 가리켜 깨짐(`kis_api/dart.py:135`) → `get_dart_corp_map()`=`{}` — dart_op_growth·insider 실시간 수집 비활성 (라이브 로그 확인됨) | `{}` |
| `/data/dart_screener_cache.json` | DART 스크리너 당일 캐시 | `{}` |
| `/data/corp_codes.json` | OpenDART corp_code 매핑 캐시 (1일 1회 갱신) | `{}` |
| `/data/regime_state.json` | 레짐 상태 (KR/US 분리 + 전환 히스토리) — Gist 백업 대상(`_BACKUP_FILES_LIST`) | `{}` |
| `/data/token_cache.json` | KIS 토큰 파일 캐시 `{token, expires}` (23시간) | `{}` |
| `/data/signal_feed.json` | 시그널 피드 (알림/대시보드 공용 이벤트 로그) | `[]` |
| `/data/silent_failure_log.json` | 무음 실패 감시 로그 (잡별 0건 결과 추적, `main_pkg/_ctx.py`) | `{}` |
| `/data/sector_flow_cache.json` · `sector_rotation.json` | 섹터 수급 당일 캐시 + 섹터 로테이션 상태 | `{}` |
| `/data/supply_history.json` | 종목별 수급 히스토리 (외인 보유율 등 교차확인용) | `{}` |
| `/data/us_sp500.json` · `us_russell1000.json` | 미국 유니버스 `{updated, tickers}` (weekly_us_harvest 사용) | `{}` |
| `/data/sec_cik_map.json` | SEC CIK ↔ 티커 매핑 캐시 | `{}` |
| `/data/naver_pdf_cache.json` + `report_pdfs/` | 증권사 리포트 크롤러 캐시 + PDF 저장소 (report_crawler) | `{}` / — |
| `/data/knu_senti_lex.json` | KNU 한국어 감성사전 (뉴스 sentiment) | — |
| `/data/dart_reports/*.txt` | DART 사업보고서 본문 txt 파일 | — |
| `/data/dart_disclosures/` | DART 공시 본문 txt 캐시 (`kis_api/dart.py` `DART_DISCLOSURE_CACHE_DIR`) | — |
| `/data/deepdive/` · `decisions/` · `thesis/` · `research/` | 투자 워크플로 md 문서 디렉토리 (딥다이브/판단기록/thesis/리서치) | — |
| `/data/std_sector_map.json` | 표준산업분류코드 캐시 `{ticker: {std_code, std_name}}` (1회 수집) | `{}` |
| `/data/stock.db` | SQLite DB **20테이블+1뷰** (권위=`sqlite_master`): stock_master·daily_snapshot·financial_quarterly·consensus_history·dividend_events·reports·insider_transactions·sec_filings + 미국 애널 4종(us_analysts·us_analyst_ratings·us_analyst_coverage·us_consensus_snapshot) + 5%/10%룰 4종(dart_5pct_changes·dart_10pct_insiders·wi_5pct_changes·wi_10pct_insiders) + pension_flow_daily + nps_* 3종(nps_holdings_disclosed·nps_us_holdings·nps_kr_full_holdings) + v_daily_scan 뷰, ~480MB | — |
| `/data/db_schema.sql` | SQLite DB 스키마 정의 (테이블/인덱스/뷰 DDL) — 단 pension/nps/dart·wi 5%·10%룰 테이블 8종은 런타임 생성이라 미수록 (12/20 테이블만 수록) | — |

> 맥미니 로컬 `data/` 디렉토리 사용 (`DATA_DIR` 환경변수).
> 환경변수 기반 자동복원 fallback 있음 (`BACKUP_PORTFOLIO`·`BACKUP_STOPLOSS`·`BACKUP_WATCHALERT` 등 7종, `kis_api/_files.py` `_BACKUP_MAP`. 구 `BACKUP_WATCHLIST`/`BACKUP_US_WATCHLIST`는 미사용 — watchalert.json 존재 시 무시 로그만 출력).
> 구 `/data/krx_db/YYYYMMDD.json` 일별 JSON DB는 **폐기** — 일별 데이터는 `stock.db` daily_snapshot으로 일원화. `load_krx_db()`는 SQLite를 읽어 구 JSON 포맷으로 변환하는 호환 wrapper.

> **상세 참조**: 파일별 함수 구조 → `.claude/rules/file-structure.md`, KIS API TR_ID 테이블 → `.claude/rules/kis-api-reference.md`
