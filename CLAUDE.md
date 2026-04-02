# CLAUDE.md — stock-bot 프로젝트 가이드

## KIS API 참조

**`kis-api-ref/` 폴더에 한투 공식 API 샘플이 있음. API 엔드포인트, TR_ID, 파라미터 확인 시 이 폴더 참조할 것.**

| 파일/폴더 | 내용 |
|-----------|------|
| `kis-api-ref/examples_llm/domestic_stock/` | 국내주식 API 예제 (TR_ID, params, response 포함) |
| `kis-api-ref/examples_llm/overseas_stock/` | 해외주식 API 예제 |
| `kis-api-ref/examples_llm/etfetn/` | ETF/ETN API 예제 |
| `kis-api-ref/data.csv` | KIS REST API 전체 목록 (6326행, category/TR_ID/URL/params/response) |
| `kis-api-ref/data2.csv` | KIS API 확장 목록 (12437행) |

새 API 엔드포인트 찾을 때: `grep "TR_ID명" kis-api-ref/data.csv` 또는 `kis-api-ref/examples_llm/` 서브폴더 참조.

## 인프라

| 항목 | 내용 |
|------|------|
| 레포 | https://github.com/ysjms11/stock-bot |
| 배포 | Railway (main 브랜치 push → 자동 배포) |
| MCP URL | `https://<railway-domain>/mcp` (SSE) |
| MCP messages | `https://<railway-domain>/mcp/messages?sessionId=<id>` (POST) |
| Health check | `https://<railway-domain>/health` |
| 포트 | 환경변수 `PORT` (Railway 자동 주입, 기본 8080) |

**필수 환경변수 (Railway Variables)**

```
TELEGRAM_TOKEN   텔레그램 봇 토큰
CHAT_ID          텔레그램 채팅 ID
KIS_APP_KEY      KIS Open API 앱키
KIS_APP_SECRET   KIS Open API 시크릿
DART_API_KEY     전자공시 API 키 (선택)
GITHUB_TOKEN     GitHub Gist 백업용 토큰 (선택)
BACKUP_GIST_ID   백업 Gist ID (선택)
KRX_PROXY        KRX 크롤러 프록시 URL (선택, 데이터센터 IP 차단 우회용)
```

---

## 파일 구조

프로젝트는 4개 주요 Python 파일로 분리되어 있음:

| 파일 | 줄 수 | 역할 |
|------|-------|------|
| `kis_api.py` | ~2400 | KIS/DART/Yahoo API 함수, 데이터 파일 I/O, WebSocket, 매크로, 백업 |
| `main.py` | ~1950 | 텔레그램 봇 + 자동알림 스케줄 + 진입점 |
| `mcp_tools.py` | ~1760 | MCP 도구 스키마 + 실행 로직 + SSE 서버 |
| `krx_crawler.py` | ~400 | KRX 전종목 크롤러, DB 관리, 스캐너 |

기타 파일:

| 파일 | 내용 |
|------|------|
| `stock_universe.json` | 종목 유니버스 (시총 상위 코스피+코스닥) |
| `dart_corp_map.json` | DART 고유번호 ↔ 종목코드 매핑 |
| `test_consensus_ci.py` | CI 테스트 (컨센서스 기능) |
| `requirements.txt` | Python 의존성 |
| `Procfile` | Railway 실행 명령 |

---

## 데이터 파일 경로 (`/data/*.json`)

| 파일 | 내용 | 기본값 |
|------|------|--------|
| `/data/watchlist.json` | 한국 워치리스트 `{ticker: name}` | 5개 기본 종목 |
| `/data/us_watchlist.json` | 미국 워치리스트 `{ticker: {name, qty}}` | TSLA 등 4개 |
| `/data/stoploss.json` | 손절/목표가 `{ticker: {name, stop_price, ...}, us_stocks: {...}}` | `{}` |
| `/data/portfolio.json` | 보유 포트폴리오 `{ticker: {name, qty, avg_price}, us_stocks: {...}}` | `{}` |
| `/data/dart_seen.json` | DART 알림 전송된 공시 ID 목록 `{ids: [...]}` | `{ids: []}` |
| `/data/watchalert.json` | 매수 희망가 감시 `{ticker: {name, buy_price, memo, created}}` | `{}` |
| `/data/watch_sent.json` | 매수감시 알림 당일 발송 기록 `{ticker: "YYYY-MM-DD"}` | `{}` |
| `/data/stoploss_sent.json` | 손절 알림 당일 발송 횟수 기록 | `{}` |
| `/data/decision_log.json` | 투자판단 기록 (날짜별 regime/grades/actions) | `[]` |
| `/data/compare_log.json` | 종목 비교 기록 | `[]` |
| `/data/watchlist_log.json` | 워치리스트 변경 이력 | `[]` |
| `/data/events.json` | 매크로 이벤트 캘린더 | `{}` |
| `/data/weekly_base.json` | 주간 리뷰 기준 스냅샷 | `{}` |
| `/data/stock_universe.json` | 종목 유니버스 (시총 상위) | `{}` |
| `/data/consensus_cache.json` | 컨센서스 캐시 (FnGuide) | `{}` |
| `/data/portfolio_history.json` | 포트폴리오 일별 스냅샷 | `[]` |
| `/data/trade_log.json` | 매매 기록 | `[]` |
| `/data/dart_corp_map.json` | DART 고유번호 매핑 | `{}` |
| `/data/dart_screener_cache.json` | DART 스크리너 당일 캐시 | `{}` |
| `/data/corp_codes.json` | OpenDART corp_code 매핑 캐시 (1일 1회 갱신) | `{}` |
| `/data/dart_reports/*.txt` | DART 사업보고서 본문 txt 파일 | — |
| `/data/krx_db/YYYYMMDD.json` | KRX 전종목 일별 DB (시세+수급+비율, 30일 보관) | — |

> Railway는 `/data` 볼륨을 영구 마운트해야 재시작 후에도 데이터 보존됨.
> 볼륨 미마운트 시 환경변수 기반 자동복원 fallback 있음 (`BACKUP_PORTFOLIO`, `BACKUP_STOPLOSS` 등).

---

## kis_api.py 구조 (위→아래)

```
[1~9]       imports
            aiohttp, json, re, xml, datetime, zoneinfo 등

[11~61]     환경변수 & 상수
            TELEGRAM_TOKEN, KIS_BASE_URL, KST, ET(미국 동부시간)
            데이터파일 경로 17개, MACRO_SYMBOLS

[62~89]     환경변수 기반 데이터 복원
            _BACKUP_MAP — Railway Volume 미마운트 시 fallback

[90~144]    헬퍼 함수 & 상수
            _token_cache              토큰 캐시
            _is_us_ticker()           영문 티커 → 미국 종목 판별
            _NYSE_TICKERS             NYSE 대표 종목 세트
            _guess_excd()             NYS/NAS 거래소코드 추정
            _is_us_market_hours_kst() 미국 장시간 여부 (ET 기반, DST 자동 감지)
            _is_us_market_closed()    미국 정규장 마감 후 30분 이내 여부
            DART_KEYWORDS             중요 공시 키워드 목록

[147~308]   파일 저장/로드
            load_json / save_json
            load_watchlist / load_stoploss / load_us_watchlist
            load_dart_seen / load_watchalert / load_decision_log
            load_trade_log / save_trade_log / get_trade_stats
            load_consensus_cache / load_compare_log
            load_watchlist_log / append_watchlist_log

[309~607]   컨센서스 & 스크리너
            _recom_label()            투자의견 코드→라벨
            fetch_fnguide_consensus() FnGuide 컨센서스 크롤링
            get_us_consensus()        미국 종목 컨센서스
            update_consensus_cache()  캐시 일괄 업데이트

[608~770]   포트폴리오 히스토리 & 드로다운
            save_portfolio_snapshot() 일별 스냅샷 저장
            _fetch_us_price_simple()  미국 가격 간이 조회
            check_drawdown()          드로다운 분석

[771~1005]  KIS API 함수 (국내)
            get_kis_token()           OAuth 토큰 (20시간 캐시)
            get_stock_price()         국내 현재가 (구 방식)
            get_investor_trend()      국내 수급 (구 방식)
            get_volume_rank()         거래량 상위 (구 방식)
            get_kis_index()           KOSPI/KOSDAQ 지수
            _kis_headers()            공통 헤더 생성
            _kis_get()                GET 래퍼 (신 방식)
            kis_stock_price()         국내 현재가 (신 방식)
            kis_stock_info()          종목 기본정보
            kis_investor_trend()      국내 수급 (신 방식)
            kis_credit_balance()      신용잔고
            kis_short_selling()       공매도
            kis_volume_rank_api()     거래량 상위 (신 방식)
            kis_foreigner_trend()     외국인 순매수 상위
            kis_sector_price()        업종별 시세
            WI26_SECTORS              7개 업종 코드/이름
            _fetch_sector_flow()      업종 외국인+기관 순매수
            kis_us_stock_price()      해외 현재가
            kis_us_stock_detail()     해외 현재가상세

[1006~1060] kis_fluctuation_rank()    등락률 순위

[1060~1190] 투자자 수급 확장
            kis_investor_trend_history() 투자자별 일별 수급 히스토리
            kis_daily_volumes()       최근 N일 거래량
            check_momentum_exit()     모멘텀 이탈 체크

[1191~1222] batch_stock_detail()      다종목 일괄 조회

[1223~1476] 추가 KIS API 함수
            kis_program_trade_today() 프로그램매매 당일 동향
            kis_investor_trend_estimate() 장중 투자자 추정 수급
            kis_foreign_institution_total() 외국인+기관 합산 순매수
            kis_daily_short_sale()    공매도 일별추이
            kis_news_title()          종목 뉴스 헤드라인
            kis_vi_status()           VI 발동 현황
            kis_volume_power_rank()   체결강도 상위

[1476~1544] 해외 확장
            kis_us_updown_rate()      해외 등락률 상위/하위
            kis_estimate_perform()    국내 종목추정실적

[1545~1660] 유니버스 & 일봉
            get_stock_universe()      종목 유니버스 로드
            fetch_universe_from_krx() KRX 시총 상위 종목 갱신
            batch_fetch()             일괄 API 호출
            kis_daily_closes()        최근 N일 종가

[1662~1831] KIS WebSocket 실시간 체결가
            get_kis_ws_approval_key() WebSocket 접속키 발급
            KisRealtimeManager        실시간 체결가 매니저 (국내주식 전용)
            get_ws_tickers()          구독 대상 티커 목록

[1831~1848] Yahoo Finance
            get_yahoo_quote()         미국 지수/개별 시세 (fallback)

[1849~2052] 매크로 대시보드
            load_events()             이벤트 캘린더 로드
            collect_macro_data()      매크로 데이터 수집 (VIX·WTI·금·구리·DXY·US10Y)
            format_macro_msg()        매크로 메시지 포맷
            judge_regime()            시장 국면 판단

[2052~2111] DART API
            search_dart_disclosures()      최근 N일 공시 목록
            filter_important_disclosures() 워치리스트+키워드 필터

[2111~2230] DART 기업재무
            build_dart_corp_map()     DART 고유번호 매핑 구축
            get_dart_corp_map()       매핑 로드/캐시
            dart_quarterly_op()       분기별 영업이익 조회

[2230~2400] DART 사업보고서 본문 저장
            load_corp_codes()         corp_code 매핑 캐시 (1일 1회)
            _download_corp_codes()    corpCode.xml zip → 매핑 생성
            search_dart_reports()     사업보고서(A001) 검색
            fetch_dart_document()     document.xml → 텍스트 추출
            _report_file_exists()     접수번호 중복 체크
            save_dart_report()        사업보고서 txt 저장
            list_dart_reports()       저장된 txt 목록 반환

[2400~2570] GitHub Gist 백업
            backup_data_files()       Gist에 백업
            restore_data_files()      Gist에서 복원
            get_backup_status()       백업 상태 조회

[2372~2397] 뉴스 (Google News RSS)
            fetch_news()
```

---

## main.py 구조 (위→아래)

```
[1~13]      imports
            kis_api에서 전체 import + 특정 함수 import

[15~67]     헬퍼 함수
            _refresh_ws()             WebSocket 구독 갱신
            _is_kr_trading_time()     한국 장시간 여부
            _extract_grade()          확신등급 추출
            _grade_arrow()            등급 변경 화살표

[68~327]    자동알림 1: daily_kr_summary
            매일 15:40 KST — 한국장 마감 요약 (지수·수급·손절·섹터·DART)

[328~444]   자동알림 2: daily_us_summary
            미사용 (us_market_summary로 대체)

[445~560]   자동알림 3: us_market_summary
            미국 장 마감 요약 (서머타임/표준시 이중 등록, 마감 30분 이내 가드)

[561~739]   자동알림 4: check_stoploss
            10분마다 — 손절선 도달 + 매수희망가 도달 텔레그램 알림
            _get_stoploss_sent_count / _increment_stoploss_sent (일일 발송 제한)

[740~756]   자동알림 5: check_fx_alert
            비활성화 — 매크로 대시보드로 통합 예정

[757~848]   자동알림 6: check_anomaly
            30분마다 — 거래량+외국인 복합 이상 신호

[849~903]   자동알림 7: check_supply_drain
            매일 15:40 KST — 수급 이탈 감지

[904~954]   자동알림 8: momentum_exit_check
            매일 15:45 KST — 모멘텀 이탈 체크

[955~1039]  자동알림 9: weekly_review
            매주 일요일 01:00 KST — 주간 리뷰 리마인더

[1040~1082] 자동알림 10: snapshot_and_drawdown
            매일 15:50 KST — 포트폴리오 스냅샷 + 드로다운 경고

[1083~1097] 자동알림 11: weekly_consensus_update
            매주 월요일 07:05 KST — 컨센서스 캐시 갱신

[1098~1122] 자동알림 12: auto_backup
            매일 22:00 KST — GitHub Gist 자동 백업

[1123~1166] 자동알림 13: weekly_universe_update
            매주 월요일 07:00 KST — 종목 유니버스 갱신

[1167~1184] 자동알림 14: macro_dashboard
            매일 18:00 + 06:00 KST — 매크로 대시보드

[1185~1248] 자동알림 15: check_dart_disclosure
            30분마다 — DART 중요 공시 (장중 08~16:30)

[1249~1763] 텔레그램 명령어 핸들러
            /start /analyze /scan /macro /news /dart /summary
            /watchlist /watch /unwatch
            /uslist /addus /remus
            /setstop /delstop /stops
            /setportfolio /setusportfolio /help
            post_init()  (시작 시 Gist 복원 + WebSocket 시작 + 유니버스 로드)

[1764~1831] post_init()
            시작 시 초기화: Gist 복원, WebSocket, 유니버스, 컨센서스

[1832~1877] main()
            텔레그램 봇 빌드, 명령어 등록, 자동알림 스케줄 등록

[1879~1949] _run_all()
            MCP aiohttp 서버 시작 + WebSocket 실시간 알림 콜백 + 텔레그램 폴링
```

---

## mcp_tools.py 구조 (위→아래)

```
[1~22]      imports
            kis_api에서 전체 import + 특정 함수 import

[23~100]    DART 스크리너 캐시 & 헬퍼
            _load_dart_screener_cache / _save_dart_screener_cache
            _dart_tag()               공시 태그 분류
            _pf() / _nf()            숫자 파싱 헬퍼
            _calc_qoq()              분기 QoQ 계산

[100~305]   스크리너 내부 함수
            _scan_conv_one()          이평 수렴 스크리너 (종목 1개)
            _op_extra_fields()        영업이익 부가정보
            _scan_op_one()            KIS 영업이익 성장 스크리너 (종목 1개)
            _scan_turnaround_one()    KIS 적자→흑자 전환 (종목 1개)
            _scan_dart_op_one()       DART 영업이익 성장 스크리너 (종목 1개)
            _scan_dart_turnaround_one() DART 적자→흑자 전환 (종목 1개)

[306~510]   MCP_TOOLS 배열 (28개)
            Claude MCP 도구 스키마 정의

[513~1663]  _execute_tool()
            MCP 도구 실행 로직 (if/elif 체인)

[1666~1758] MCP 서버
            _handle_jsonrpc()      JSON-RPC 2.0 처리
            _mcp_sessions          SSE 세션 관리
            mcp_sse_handler()      GET /mcp — SSE 스트림
            mcp_messages_handler() POST /mcp/messages
```

---

## KIS API 호출 패턴

모든 신규 함수는 `_kis_get()` 래퍼를 사용 (kis_api.py에 정의):

```python
async def kis_some_api(ticker: str, token: str) -> dict:
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/...",
            "TR_ID_HERE", token,
            {"param1": "val1", "param2": "val2"})
        return d.get("output", {})
```

**국내 주요 TR_ID**

| TR_ID | 용도 | 함수 |
|-------|------|------|
| `FHKST01010100` | 국내 현재가 | `kis_stock_price()` |
| `FHKST01010900` | 국내 수급(외국인/기관) | `kis_investor_trend()` |
| `FHPST01710000` | 거래량 상위 | `kis_volume_rank_api()` |
| `FHKUP03500100` | 업종별 시세 | `kis_sector_price()` |
| `FHPTJ04060100` | 외국인 순매수 상위 | `kis_foreigner_trend()` |
| `FHPUP02100000` | KOSPI/KOSDAQ 지수 | `get_kis_index()` |
| `CTPF1002R` | 종목 기본정보 | `kis_stock_info()` |
| `FHKST01010600` | 신용잔고 | `kis_credit_balance()` |
| `FHKST01010700` | 공매도 | `kis_short_selling()` |
| `FHPST01700000` | 등락률 순위 | `kis_fluctuation_rank()` |
| `FHPTJ04160001` | 투자자별 일별 수급 히스토리 | `kis_investor_trend_history()` |
| `FHKST03010100` | 일봉 차트 (거래량/종가) | `kis_daily_volumes()` / `kis_daily_closes()` |
| `HHPPG046600C1` | 프로그램매매 당일 동향 | `kis_program_trade_today()` |
| `HHPTJ04160200` | 장중 투자자 추정 수급 | `kis_investor_trend_estimate()` |
| `FHPTJ04400000` | 외국인+기관 합산 순매수 | `kis_foreign_institution_total()` |
| `FHPST04830000` | 공매도 일별추이 | `kis_daily_short_sale()` |
| `FHKST01011800` | 종목 뉴스 헤드라인 | `kis_news_title()` |
| `FHPST01390000` | VI 발동 현황 | `kis_vi_status()` |
| `FHPST01680000` | 체결강도 상위 | `kis_volume_power_rank()` |
| `FHPST01740000` | 시가총액 상위 (유니버스) | `fetch_universe_from_krx()` |
| `HHKST668300C0` | 종목추정실적 | `kis_estimate_perform()` |

**해외 주요 TR_ID**

| TR_ID | 용도 | 경로 |
|-------|------|------|
| `HHDFS00000300` | 해외 현재가 | `/uapi/overseas-price/v1/quotations/price` |
| `HHDFS76200200` | 해외 현재가상세 (PER/PBR/시총/52주) | `/uapi/overseas-price/v1/quotations/price-detail` |
| `HHDFS76290000` | 해외 등락률 상위/하위 | `/uapi/overseas-stock/v1/ranking/updown-rate` |

**해외 현재가 응답 주요 필드**

| 필드 | 설명 |
|------|------|
| `last` | 현재가 |
| `rate` | 등락률 (%) ← `diff_rate` 아님 주의 |
| `tvol` | 거래량 |
| `base` | 전일 종가 |

**해외 현재가상세 응답 주요 필드**

| 필드 | 설명 |
|------|------|
| `perx` | PER |
| `pbrx` | PBR |
| `epsx` | EPS |
| `tomv` | 시가총액 |
| `h52p` | 52주 최고가 |
| `l52p` | 52주 최저가 |
| `e_icod` | 업종 코드 |
| `open` / `high` / `low` | 시가/고가/저가 |

---

## MCP 도구 목록 (29개)

| # | 이름 | 설명 |
|---|------|------|
| 1 | `scan_market` | 거래량 상위 종목 스캔 |
| 2 | `get_portfolio` | 포트폴리오 조회/수정 (한국+미국 손익, cash_krw/cash_usd) |
| 3 | `get_stock_detail` | 개별 종목 상세 (현재가·PER·PBR·수급, 한국/미국 자동 판별, period로 일봉 조회) |
| 4 | `get_foreign_rank` | 외국인 순매수 상위 종목 |
| 5 | `get_dart` | DART 공시 (기본: 워치 3일 공시, mode='report': 사업보고서 txt 저장, mode='report_list': 저장 파일 목록, mode='read': 저장된 보고서 읽기) |
| 6 | `get_macro` | 매크로 지표 (기본: 지수+환율, dashboard/sector_etf/convergence/op_growth/dart_op_growth 등 모드) |
| 7 | `get_sector_flow` | WI26 업종별 외국인+기관 순매수 상위/하위 3개 |
| 8 | `add_watch` | 한국 워치리스트 종목 추가 |
| 9 | `remove_watch` | 한국 워치리스트 종목 제거 (alert_type='buy_alert'로 매수감시도 제거 가능) |
| 10 | `get_alerts` | 손절가 목록 + 현재가 대비 % + 매수감시 목록 |
| 11 | `get_investor_flow` | 개별 종목 투자자별 수급 (외국인·기관·개인) |
| 12 | `get_price_rank` | 등락률 상위/하위 종목 (rise/fall, kospi/kosdaq) |
| 13 | `get_investor_trend_history` | 투자자별 수급 일별 히스토리 (최근 N일) |
| 14 | `get_program_trade` | 프로그램매매 투자자별 당일 동향 |
| 15 | `get_investor_estimate` | 장중 투자자 추정 수급 가집계 |
| 16 | `get_foreign_institution` | 외국인+기관 합산 순매수 상위 (가집계) |
| 17 | `get_short_sale` | 공매도 일별추이 |
| 18 | `get_news` | KIS 종목 뉴스 헤드라인 |
| 19 | `get_vi_status` | VI 발동 종목 현황 |
| 20 | `get_volume_power` | 체결강도 상위 종목 순위 |
| 21 | `get_us_price_rank` | 미국 주식 등락률 상위/하위 (NAS/NYS/AMS) |
| 22 | `get_consensus` | 증권사 컨센서스 목표주가/투자의견 (FnGuide) |
| 23 | `set_alert` | 손절가/목표가, 매수감시, 투자판단, 종목비교, 매매기록 (log_type으로 모드 선택) |
| 24 | `delete_alert` | stoploss.json에서 종목 알림 삭제 |
| 25 | `get_portfolio_history` | 포트폴리오 스냅샷 히스토리 + 드로다운 + 투자규칙 경고 |
| 26 | `get_trade_stats` | 매매 기록 성과 분석 (승률·손익·평균보유기간) |
| 27 | `get_batch_detail` | 다종목 일괄 조회 (최대 20종목, 현재가·PER·PBR·수급) |
| 28 | `backup_data` | /data/*.json GitHub Gist 백업·복원·상태 조회 |
| 29 | `get_scan` | KRX 전종목 스크리너 (시총/PER/PBR/수급비율/회전율 필터, 6개 프리셋) |

---

## 새 MCP 도구 추가하는 방법

**Step 1 — API 함수 작성** (`kis_api.py`에 추가)

```python
async def kis_new_api(ticker: str, token: str) -> dict:
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/...", "TR_ID", token, {"param": ticker})
        return d.get("output", {})
```

**Step 2 — MCP_TOOLS 배열에 스키마 추가** (`mcp_tools.py`의 `MCP_TOOLS` 배열 끝)

```python
{"name": "new_tool_name", "description": "도구 설명",
 "inputSchema": {"type": "object",
                 "properties": {"ticker": {"type": "string", "description": "종목코드"}},
                 "required": ["ticker"]}},
```

**Step 3 — `_execute_tool` 함수에 elif 핸들러 추가** (`mcp_tools.py`의 `else: result = {"error": ...}` 바로 위)

```python
elif name == "new_tool_name":
    ticker = arguments.get("ticker", "").strip()
    d = await kis_new_api(ticker, token)
    result = {"ticker": ticker, "field": d.get("field_name")}
```

**Step 4** — 커밋 & push → Railway 자동 배포

---

## 알려진 이슈

- **해외 현재가 `rate` 필드**: 응답 필드는 `rate` (등락률%). `diff_rate`는 존재하지 않음 → None 반환됨. `get_portfolio` 미국 섹션은 `d.get("rate")` 사용.
- **거래소 코드 자동판별**: `_guess_excd()`는 `_NYSE_TICKERS` 세트 기반으로 NYS/NAS만 구분. AMEX(`AMS`) 종목은 NAS로 fallback됨.
- **`/data` 볼륨**: Railway에서 볼륨 마운트 안 하면 재배포 시 데이터 초기화됨. 환경변수 기반 fallback 복원 + Gist 백업 있음.
- **KIS 토큰 캐시**: `_token_cache`는 메모리에만 존재. 재시작 시 재발급 필요 (20초 내외 소요).
- **Yahoo Finance fallback**: 미국 장 요약(`us_market_summary`)과 손절 체크(`check_stoploss` US)는 Yahoo Finance 사용. KIS 해외 API와 혼용 주의.
- **check_fx_alert 비활성화**: 환율 알림은 매크로 대시보드로 통합 예정, 스케줄에서 주석 처리됨.
- **WebSocket 국내 전용**: `KisRealtimeManager`는 국내주식만 지원. 미국주식은 폴링 방식(`check_stoploss`).
- **DST 자동 감지**: 미국 장 시간 판별은 `zoneinfo.ZoneInfo('America/New_York')` 사용으로 서머타임/표준시 자동 전환.
- **KRX 데이터센터 IP 차단**: `data.krx.co.kr`은 Railway 등 데이터센터 IP를 Akamai WAF + 앱 레벨에서 차단. `KRX_PROXY` 환경변수로 프록시 설정 필요. 프록시 없으면 pykrx fallback 시도 (동일 IP 차단일 수 있음).

---

## 코딩 규칙

- **4파일 구조**: API/데이터 → `kis_api.py`, 텔레그램+스케줄 → `main.py`, MCP → `mcp_tools.py`, KRX 크롤러 → `krx_crawler.py`.
- **KIS API 신 방식**: 새 함수는 반드시 `_kis_get()` 래퍼 사용 (구 방식 `get_stock_price()` 패턴 사용 금지).
- **에러 처리**: 개별 종목 루프 내부는 `try/except Exception: pass` 패턴으로 한 종목 오류가 전체 중단 방지.
- **asyncio.sleep(0.3~0.4)**: KIS API 연속 호출 시 rate limit 방지를 위해 `await asyncio.sleep(0.3)` 삽입.
- **섹션 구분**: `# ━━━━━━━━━━━━━━━━━━━━━━━━━` 주석으로 논리적 섹션 구분 유지.
- **한국어 변수명**: 텔레그램 메시지 문자열 외에는 영문 변수명 사용.
- **MCP 도구 순서**: `MCP_TOOLS` 배열과 `_execute_tool` elif 체인의 순서를 일치시킬 것.
- **import 패턴**: `kis_api.py`에서 `from kis_api import *` + 명시적 private 함수 import. `mcp_tools.py`도 동일.
