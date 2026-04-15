# 파일별 함수 구조 상세

## kis_api.py 구조 (위→아래)

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

## db_collector.py 구조

```
[1~55]      imports, 상수 (DB_PATH, KST, _DATA_DIR)
[56~82]     SQLite 연결 (_get_db, _init_schema)
[83~210]    KRX OPEN API + 파싱 (_krx_openapi_get, _krx_post, _parse_market_records, fetch_krx_market_data)
[210~362]   섹터 매핑 (_classify_sector, _load_std_sector_map, KIS 기본정보 수집 헬퍼)
[363~402]   stock_master 관리 (_sync_stock_master, _update_master_from_basic)
[403~523]   배치 수집 (_collect_phase, _store_daily_snapshot)
[524~661]   collect_daily() 메인 함수
[662~827]   기술지표 헬퍼 (_ma, _rsi, _calc_vp, _volume_ratio, _spread_at, _rsi_at, _macd, _atr, _volatility_20d)
[829~1051]  히스토리 로드 & 기술지표 계산 (_load_history_from_db, _compute_technicals_sqlite)
[1052~1120] 기술지표 DB 저장 (_compute_and_update)
[1121~1221] load_krx_db() 하위호환 함수
[1222~1312] 스캐너 헬퍼 (_load_history, _get_foreign_streak_data_db, _summarize_filters)
[1313~1466] scan_stocks() 스캐너 (15개 프리셋 + 복합 필터)
[1467~1646] collect_financial_weekly() + _update_financial_derived()
[1647~1699] backup_to_icloud()
```
