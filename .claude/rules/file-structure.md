# 파일별 함수 구조 상세

> ⚠️ **STALE (2026-05 리팩터)**: 아래 `kis_api.py`·`main.py`·`mcp_tools.py` 단일파일 구조/라인범위 맵은 **더 이상 유효하지 않음** — 패키지로 분리됨(`kis_api/` 23파일, `mcp_tools/` = `__init__`·`_registry`·`_execute`·`server`·`tools/*`, `main_pkg/` = `telegram_bot`·`_entry`·`_ctx`·`schedule`·`jobs/*`; `main.py`는 7줄 shim). **함수 위치는 `grep -rn "def <name>" <pkg>/`로 확인할 것.** `db_collector.py`도 2026-06 `db_collector/` 패키지(14모듈)로 분리됨 — 아래 패키지 맵 참조 (구 단일파일 라인맵 폐기).

## kis_api.py 구조 (위→아래) — ⚠️ STALE, kis_api/ 패키지로 분리됨

```
[1~9]       imports (aiohttp, json, re, xml, datetime, zoneinfo 등)
[11~61]     환경변수 & 상수 (TELEGRAM_TOKEN, KIS_BASE_URL, KST, ET, 데이터파일 경로 17개)
[62~89]     환경변수 기반 데이터 복원 (_BACKUP_MAP)
[90~144]    헬퍼 (_token_cache, _is_us_ticker, _NYSE_TICKERS, _guess_excd, _is_us_market_hours_kst, DART_KEYWORDS)
[147~308]   파일 저장/로드 (load_json/save_json, load_watchlist, load_stoploss 등)
[309~607]   컨센서스 & 스크리너 (fetch_fnguide_consensus, get_us_consensus, update_consensus_cache)
[608~770]   포트폴리오 히스토리 & 드로다운 (save_portfolio_snapshot, check_drawdown)
[771~1005]  KIS API 국내 (get_kis_token, _kis_get, kis_stock_price, kis_investor_trend 등)
[1006~1060] kis_fluctuation_rank (등락률 순위)
[1060~1190] 투자자 수급 확장 (kis_investor_trend_history, kis_daily_volumes, check_momentum_exit)
[1191~1222] batch_stock_detail (다종목 일괄)
[1223~1476] 추가 KIS API (프로그램매매, 추정수급, 공매도추이, 뉴스, VI, 체결강도)
[1476~1544] 해외 확장 (kis_us_updown_rate, kis_estimate_perform)
[1545~1660] 유니버스 & 일봉 (fetch_universe_from_krx, kis_daily_closes)
[1662~1831] WebSocket 실시간 (KisRealtimeManager)
[1831~1848] Yahoo Finance (get_yahoo_quote)
[1849~2052] 매크로 대시보드 (collect_macro_data, judge_regime)
[2052~2230] DART API (공시, 기업재무, 사업보고서)
[2230~2400] GitHub Gist 백업/복원
[2400+]     뉴스, 재무비율순위, 52주신고저, 거래원, 신용잔고, 대차, 시간외, 호가
[3547+]     DART 내부자 거래 (kis_elestock, upsert_insider_transactions, aggregate_insider_cluster, collect_insider_for_tickers)
```

## main.py 구조

```
[1~67]      imports + 헬퍼 (_refresh_ws, _is_kr_trading_time, _extract_grade)
[68~327]    자동알림 1: daily_kr_summary (15:40 KST)
[328~560]   자동알림 2-3: US 요약 (서머타임/표준시)
[561~739]   자동알림 4: check_stoploss (10분마다)
[740~1248]  자동알림 5-15: anomaly, supply_drain, momentum_exit, weekly_review, snapshot, consensus, backup, universe, macro, DART
[1249~1763] 텔레그램 명령어 핸들러
[1764~1831] post_init() (Gist복원, WebSocket, 유니버스, 컨센서스)
[1832~1949] main() + _run_all() (MCP+텔레그램+WebSocket)
```

## mcp_tools.py 구조

```
[1~22]      imports
[23~100]    DART 스크리너 캐시 & 헬퍼
[100~305]   스크리너 내부 함수 (_scan_conv_one, _scan_op_one 등)
[306~600]   MCP_TOOLS 배열 (24개 도구 스키마)
[600+]      _execute_tool() (if/elif 체인)
[끝]        MCP 서버 (_handle_jsonrpc, SSE, messages)
```

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

## dashboard_home/ 패키지 구조 (2026-06 분해, 단일파일 ~6,700줄 → 7모듈)

| 모듈 | 줄수 | 소유 |
|------|------|------|
| `__init__.py` | 59 | 표면 동결 (외부 소비자는 `register_home_routes`·`warm_caches` 2심볼 — main_pkg/_entry.py) |
| `_assets.py` | 5,133 | 템플릿/JS 상수 11종 + `_HOME_SHELL` 조립 — **sha256 characterization 골든으로 byte 동결** (r-string 재이스케이프 절대 금지) |
| `_helpers.py` | ~105 | SWR 캐시 인프라 (`_cache`/`_refreshing`/`_cached`) + `_open_db`. 다른 서브모듈 import 금지 (cycle) |
| `payloads.py` | ~1,495 | 네트워크/DB payload 빌더 전부 (home/market/macro/portfolio/watch/signals/US/records) |
| `reports.py` | 150 | 리포트 payload (SQLite) |
| `whale.py` | 276 | Whale 6종 + build_whale_payload |
| `routes.py` | 431 | `_handle_*` 29개 + `register_home_routes` + `warm_caches` 정본 |

> characterization: `tests/test_dashboard_home_characterization.py` (템플릿 해시 13·라우트 57·payload 키셋). 템플릿 수정 시 해시 골든도 함께 갱신해야 함 (의도된 변경만).
