# 파일별 함수 구조 상세

> ✅ **맵 갱신 완료 (2026-06-11)**: 2026-05 리팩터로 단일파일 `kis_api.py`·`mcp_tools.py`는 삭제되고 `main.py`는 7줄 shim만 남음 — 패키지 분리: `kis_api/`(23모듈), `mcp_tools/`(`__init__`·`_registry`·`_execute`·`_helpers`·`server`·`tools/*`), `main_pkg/`(`telegram_bot`·`_entry`·`_ctx`·`schedule`·`jobs/*`). `db_collector.py`도 2026-06 `db_collector/` 패키지(14모듈)로 분리됨. 아래는 실측 패키지 맵 (줄수는 2026-06-11 스냅샷 — ±드리프트 가능). **함수 위치 권위는 코드**: `grep -rn "def <name>" <pkg>/`.

## kis_api/ 패키지 구조 (2026-05 분해, 23모듈 11,077줄)

표면: `from kis_api import *` (`__init__.py` re-export). 새 함수는 도메인 서브모듈에 추가 후 `__init__.py`에 노출.

| 모듈 | 줄수 | 소유 |
|------|------|------|
| `__init__.py` | 217 | 패키지 표면 — 전 서브모듈 re-export (`from kis_api import *` 호환) |
| `_config.py` | 77 | 환경변수·경로 상수·타임존 (TELEGRAM_TOKEN, KIS_BASE_URL, KST/ET, DATA_DIR) |
| `_db.py` | 22 | stock.db 공용 connect 헬퍼 (PRAGMA 일관 적용) |
| `_files.py` | 361 | JSON 저장/로드 + 환경변수 기반 데이터 복원 (load_json/save_json, load_watchlist, load_stoploss 등) |
| `_helpers.py` | 237 | 티커 판별/거래소 추정/시장시간/감성 데이터 (_is_us_ticker, _guess_excd, _is_us_market_hours_kst) |
| `_session.py` | 107 | 공유 aiohttp 세션 + KIS 토큰 캐시 23h (get_kis_token) + `_kis_get` 래퍼 |
| `backup.py` | 246 | GitHub Gist 백업/복원 |
| `consensus.py` | 456 | FnGuide/Nasdaq 컨센서스 조회·캐시 (fetch_fnguide_consensus, get_us_consensus, update_consensus_cache) |
| `dart.py` | 1,653 | DART 공시/기업재무/사업보고서/내부자거래 (kis_elestock, upsert_insider_transactions, aggregate_insider_cluster) |
| `fmp.py` | 278 | FMP API (실적콜 transcript/애널 추정) + YouTube 자막 추출 |
| `kr_stock.py` | 1,590 | KIS 국내주식 31 TR_ID (kis_stock_price, kis_investor_trend, kis_daily_volumes, batch_stock_detail 등) |
| `macro.py` | 336 | 매크로 대시보드 수집·포맷 (collect_macro_data, format_macro_msg, judge_regime) |
| `news.py` | 558 | 뉴스 + 감성분석 + US 실적캘린더/섹터 ETF/공매도 잔량 (fetch_news, analyze_us_news_sentiment) |
| `pension.py` | 1,659 | NPS 연기금 수집: pension_flow / 13F (SEC EDGAR) / KR 풀포트 |
| `polymarket.py` | 609 | Polymarket 예측시장 + Treasury 수익률 곡선 |
| `portfolio.py` | 212 | 포트폴리오 스냅샷·드로다운 (save_portfolio_snapshot, check_drawdown) |
| `ranks.py` | 259 | 순위 API — 시간외/거래원/증권사/배당 (kis_overtime_fluctuation, kis_dividend_rate_rank, kis_us_updown_rate) |
| `regime.py` | 571 | 시장 국면 판단 — KR/US 분리 레짐 (calc_kr_regime, calc_us_regime, 디바운스, cmd_regime) |
| `sec_edgar.py` | 406 | SEC EDGAR 1차 공시 통합 (data.sec.gov, rate limit 10 req/s) |
| `universe.py` | 158 | 종목 유니버스 (get_stock_universe DB 기반 + fetch_universe_from_krx KIS fallback, kis_daily_closes) |
| `us_ratings.py` | 626 | 미국 애널리스트 레이팅 수집 (StockAnalysis) |
| `us_stock.py` | 166 | KIS 해외주식 + Yahoo Finance (get_yahoo_quote) + 볼륨 프로파일 |
| `websocket.py` | 273 | `KisRealtimeManager` — KIS WebSocket 실시간 체결가 (국내 전용) |

## mcp_tools/ 패키지 구조 (2026-05 분해, 코어 5 + tools/ 20파일)

| 모듈 | 줄수 | 소유 |
|------|------|------|
| `__init__.py` | 567 | `MCP_TOOLS` 스키마 배열 47개 + re-export — **도구 개수 권위 값은 `len(MCP_TOOLS)`** |
| `_registry.py` | 134 | `TOOL_HANDLERS` dict + `execute_tool()` 디스패치 (구 elif 체인 폐기; 헤더 주석 "45"는 stale) |
| `_execute.py` | 28 | `_execute_tool()` 래퍼 (token 인자 핸들러에 KIS 토큰 자동 발급) |
| `_helpers.py` | 863 | 내부 헬퍼 — DART 스크리너 캐시, PDF 렌더링, 스캔 내부 함수 |
| `server.py` | 214 | MCP 서버 — SSE + JSON-RPC 핸들러 |

tools/ — 19 핸들러 모듈 + `__init__.py`. 핸들러 47개 = 도구 1:1 (`test_mcp_schema.py`가 자동 검증):

| 모듈 | 핸들러(도구) |
|------|--------------|
| `alerts.py` | get_alerts · set_alert · manage_watch |
| `backtest.py` | get_backtest · backup_data |
| `consensus.py` | get_consensus |
| `dart.py` | get_dart |
| `files.py` | read_file · write_file · list_files · read_report_pdf |
| `git.py` | git_status · git_diff · git_log · git_commit · git_push |
| `macro.py` | get_macro · get_polymarket · get_macro_external |
| `manage_report.py` | manage_report |
| `market_signal.py` | get_market_signal · get_alpha_metrics |
| `news.py` | get_news |
| `portfolio.py` | get_portfolio · get_portfolio_history · get_trade_stats · simulate_trade |
| `price.py` | get_rank · get_stock_detail |
| `regime.py` | get_regime |
| `scan.py` | get_scan · get_change_scan · get_finance_rank · get_highlow · get_broker |
| `sec.py` | get_sec_filings |
| `sector.py` | get_sector |
| `supply.py` | get_supply · get_pension_flow |
| `us.py` | get_us_ratings · get_us_scan · get_us_analyst · watch_analyst · get_us_buy_candidates · get_us_earnings_transcript · get_us_analyst_research |
| `youtube.py` | get_youtube_transcript |

## main_pkg/ 패키지 구조 (2026-05 분해; `main.py` 7줄 = shim)

`main.py` = `from main_pkg import main` shim (launchd plist가 `python main.py` 실행하므로 유지).

| 모듈 | 줄수 | 소유 |
|------|------|------|
| `__init__.py` | 5 | `main` re-export |
| `_ctx.py` | 153 | 공유 상수·공통 헬퍼 — 전 main_pkg 모듈의 import 원천 (_is_kr_trading_time, _extract_grade, _refresh_ws) |
| `_entry.py` | 343 | post_init() · main() · _run_all() — MCP+텔레그램+WebSocket 기동, dashboard_home 라우트 등록(:257) |
| `schedule.py` | 114 | `register_all_schedules(jq)` — 잡 50건 등록 (run_daily 46 + run_repeating 4) + PTB days= 매핑 가드 |
| `telegram_bot.py` | ~1,082 | 텔레그램 명령어 핸들러 (미국 애널/sanity 잡 7종은 2026-06-12 `jobs/us_analyst.py`·`jobs/sanity.py`로 분리 — telegram_bot이 15심볼 하위호환 re-export) |

jobs/ — 24 잡모듈 + `__init__.py` (25파일). 파일 ↔ 등록 잡이름 (`schedule.py` 기준):

| 파일 | 함수 → 잡이름 |
|------|---------------|
| `anomaly.py` | check_anomaly → `anomaly` (30분) |
| `change_scan.py` | daily_change_scan_alert → `daily_change_scan` · auto_backup → `auto_backup` |
| `collect.py` | daily_collect_job → `daily_collect` · daily_collect_sanity_check → `collect_sanity_1~4` · weekly_dividend_job → `weekly_dividend` |
| `consensus.py` | weekly_consensus_update → `consensus_update` · daily_consensus_check → `daily_consensus` |
| `dart_check.py` | check_dart_disclosure → `dart` (5분) |
| `dart_inc.py` | daily_dart_incremental → `dart_incremental` · daily_dart_disclosure_collect → `dart_disclosure` |
| `earnings.py` | check_earnings_calendar → `earnings_cal` · check_us_earnings_calendar → `us_earnings_cal` · check_dividend_calendar → `dividend_cal` |
| `events.py` | daily_event_d1_alert → `event_d1` · weekly_sat_port_check_notify → `weekly_sat_port_check` · weekly_sun_discovery_notify → `weekly_sun_discovery` · weekly_report_digest_notify → `weekly_report_digest` |
| `financial.py` | weekly_financial_job → `weekly_financial` |
| `insider.py` | check_insider_cluster → `insider_cluster` |
| `kr_summary.py` | daily_kr_summary → `kr_summary` |
| `macro_job.py` | macro_dashboard → `macro_am` · `macro_pm` |
| `momentum.py` | check_supply_drain → `supply_drain` · momentum_exit_check → `momentum_check` |
| `pension.py` | daily_pension_collect → `pension_collect` · daily_nps_dart_increment → `nps_dart_inc` · weekly_nps_collect → `weekly_nps` · daily_pension_alert → `pension_alert` |
| `regime.py` | regime_transition_alert → `regime_transition` (60분) |
| `reports.py` | collect_reports_daily → `report_collect` (정본; `dart_inc.py`의 데드 사본은 2026-06-12 삭제) |
| `stoploss.py` | check_stoploss → `stoploss` (10분) |
| `sunday.py` | sunday_30_reminder → `sunday_30` |
| `universe.py` | weekly_universe_update → `universe_update` |
| `us_summary.py` | us_market_summary → `us_summary_dst` · `us_summary_std` |
| `watch_change.py` | watch_change_detect → `watch_change` |
| `weekly_review.py` | weekly_review → `weekly` · snapshot_and_drawdown → `snapshot_dd` |
| `us_analyst.py` | daily_us_rating_scan → `us_ratings` · weekly_us_ratings_universe_scan → `weekly_us_harvest` · weekly_us_analyst_sync → `weekly_us_analyst_sync` · hourly_us_holdings_check → `us_holdings_noon`·`us_holdings_close` · weekly_us_analyst_report → `weekly_us_analyst` (+헬퍼5·상수2, 2026-06-12 telegram_bot서 분리) |
| `sanity.py` | weekly_sanity_check → `weekly_sanity` · weekly_log_rotate → `weekly_log_rotate` (+_is_krx_business_day, 2026-06-12 telegram_bot서 분리) |

## db_collector/ 패키지 구조 (2026-06 분해, 단일파일 4,439줄 → 14모듈)

| 모듈 | 소유 |
|------|------|
| `__init__.py` | 패키지 표면 동결 + `_PackageModule` 프록시 — `setattr(db_collector, X)`가 `_BACKING` 전 모듈로 전파(monkeypatch 투명성). **테스트는 패키지 네임스페이스를 패치할 것(서브모듈 직접 패치 금지)** |
| `_config.py` | 상수 (DB_PATH, KST, KRX_OPENAPI_*, `_KR_MARKET_HOLIDAYS`, `_is_kr_trading_day`) |
| `_db.py` | `_get_db`, `_init_schema`, **`db_write_lock`** (쓰기 직렬화 싱글톤) |
| `krx.py` | KRX OPEN API (_pi/_pf/_krx_openapi_get/_krx_post/_parse_market_records/fetch_krx_market_data) |
| `sector.py` | 섹터 분류 (dicts + _classify_sector/_load_std_sector_map) |
| `master.py` | stock_master (_sync_stock_master/_update_master_from_basic) |
| `collect.py` | 수집 파이프라인 (collect_daily/_collect_phase/_store_daily_snapshot/backfill 2종/_compute_and_update + `_RATE_SEM`) |
| `technicals.py` | 기술지표 (_ma/_rsi/_macd/_atr/_calc_vp 등 + _load_history_from_db/_compute_technicals_sqlite) |
| `scan.py` | 스캐너 (PRESETS/scan_stocks/load_krx_db/_load_history/_summarize_filters) |
| `financial.py` | 재무 수집 (collect_financial_weekly/on_disclosure/DART batch/수급·컨센 writer + 독립 `_RATE_SEM` 사본) |
| `dividends.py` | 배당 (collect_dividends/_recompute_div_yield_from_events) |
| `alpha.py` | F/M/FCF 알파 엔진 (TTM/update_all_alpha_metrics/collect_shares_historical) |
| `us_analysts.py` | 미국 애널 (sync_us_analyst_master/is_tier_s_analyst/find_us_buy_candidates) |
| `backup.py` | backup_to_icloud |

> 함수 위치: `grep -rn "def <name>" db_collector/`. 박리 모듈은 core 미존재 — 외부는 항상 `from db_collector import X` (패키지 표면).

## dashboard_home/ 패키지 구조 (2026-06 분해, 단일파일 7,506줄 → 7모듈 7,272줄)

| 모듈 | 줄수 | 소유 |
|------|------|------|
| `__init__.py` | 58 | 표면 동결 (외부 소비자는 `register_home_routes`·`warm_caches` 2심볼 — main_pkg/_entry.py) |
| `_assets.py` | 4,757 | 템플릿/JS 상수 10종 + `_HOME_SHELL` 조립 — **sha256 characterization 골든으로 byte 동결** (r-string 재이스케이프 절대 금지) |
| `_helpers.py` | 107 | SWR 캐시 인프라 (`_cache`/`_refreshing`/`_cached`) + `_open_db`. 다른 서브모듈 import 금지 (cycle) |
| `payloads.py` | 1,495 | 네트워크/DB payload 빌더 전부 (home/market/macro/portfolio/watch/signals/US/records) |
| `reports.py` | 149 | 리포트 payload (SQLite) |
| `whale.py` | 275 | Whale 6종 + build_whale_payload |
| `routes.py` | 431 | `_handle_*` 29개 + `register_home_routes` + `warm_caches` 정본 |

> characterization: `tests/test_dashboard_home_characterization.py` (템플릿 sha256·라우트·payload 키셋 골든 — **카운트는 테스트 파일이 정본**). 템플릿 수정 시 해시 골든도 함께 갱신해야 함 (의도된 변경만).

## 단일파일 4종

| 파일 | 줄수 | 역할 |
|------|------|------|
| `main.py` | 7 | shim — `from main_pkg import main` (launchd 진입점, 위 main_pkg/ 참조) |
| `dashboard.py` | 3,707 | 구 `/dash-classic` HTML 렌더링 (main 미import, 단방향) — 무수정 영역 |
| `krx_crawler.py` | 1,487 | db_collector 호환 wrapper (레거시 fallback) |
| `report_crawler.py` | 1,392 | 증권사 리포트 수집 (한경컨센서스+네이버리서치+와이즈 우선순위 병합) + reports DB — `report_collect` 잡·`read_report_pdf` 도구의 소스 |
