# stock-bot 프로젝트 현황

> 최종 업데이트: 2026-03-30

## 아키텍처

```
Railway (main push → 자동 배포)
├── main.py          텔레그램 봇 + 자동알림 스케줄러 + 진입점
├── kis_api.py       KIS/DART/Yahoo/FDR/yfinance API + 데이터 I/O
├── mcp_tools.py     MCP 도구 스키마 + 실행 로직 + SSE 서버
├── report_crawler.py 증권사 리포트 크롤링 + PDF 텍스트 추출
└── /data/*.json     영구 데이터 (Railway Volume 마운트)
```

---

## MCP 도구 (19개)

32개 → 18개 통합 (2026-03-29) → 19개 (+manage_report)

| # | 도구명 | 파라미터 | 용도 |
|---|--------|---------|------|
| 1 | `get_rank` | type: price/us_price/volume/scan | 등락률·체결강도·거래량 순위 통합 |
| 2 | `get_portfolio` | mode: set/생략 | 포트폴리오 조회/수정 (KR+US, cash) |
| 3 | `get_stock_detail` | ticker, period, tickers | 종목 상세 + 일봉 + 다종목 일괄조회 |
| 4 | `get_supply` | mode: daily/history/estimate/foreign_rank/combined_rank | 수급 분석 통합 (5개 흡수) |
| 5 | `get_dart` | — | 워치리스트 최근 3일 DART 공시 |
| 6 | `get_macro` | mode: dashboard/sector_etf/convergence/op_growth 등 | 매크로 + 스크리너 통합 |
| 7 | `get_sector` | mode: flow/rotation | 업종별 수급 + 섹터 로테이션 |
| 8 | `manage_watch` | action: add/remove | 워치리스트 추가/제거 |
| 9 | `get_alerts` | — | 손절가/매수감시 목록 + 현재가 대비 % |
| 10 | `get_market_signal` | mode: short_sale/vi/program_trade | 공매도·VI·프로그램매매 통합 |
| 11 | `get_news` | ticker, sentiment | 뉴스 헤드라인 + 감성분석(긍정/부정/중립) |
| 12 | `get_consensus` | ticker | FnGuide(KR) / yfinance(US) 컨센서스 |
| 13 | `set_alert` | log_type: stop/buy/decision/compare/trade/delete | 알림 등록 + 투자판단 + 매매기록 + 삭제 |
| 14 | `get_portfolio_history` | days | 스냅샷 히스토리 + 드로다운 + 투자규칙 경고 |
| 15 | `get_trade_stats` | period | 매매 성과 분석 (승률·손익·보유기간) |
| 16 | `backup_data` | action: backup/restore/status | GitHub Gist 백업·복원 |
| 17 | `simulate_trade` | sells, buys | 매매 시뮬레이션 (비중·섹터·RR 미리보기) |
| 18 | `get_backtest` | ticker, period, strategy | 백테스트 (5전략, D250/Y1~Y5, 비용반영) |
| 19 | `manage_report` | action: list/collect/tickers | 증권사 리포트 조회·수집·대상종목 |

---

## 자동 스케줄러

| 시간 (KST) | 주기 | 기능 | 함수 |
|------------|------|------|------|
| 10분마다 | 반복 | 손절선/매수감시 도달 알림 | `check_stoploss` |
| 30분마다 | 반복 | 거래량+외국인 이상 신호 | `check_anomaly` |
| 30분마다 | 반복 | DART 중요 공시 알림 | `check_dart_disclosure` |
| 07:00 | 평일 | 실적 캘린더 알림 | `check_earnings_calendar` |
| 07:00 | 평일 | 배당 캘린더 알림 | `check_dividend_calendar` |
| 07:00 | 평일 | 증권사 리포트 수집 | `collect_reports_daily` |
| 07:00 | 월 | 종목 유니버스 갱신 (KRX 시총) | `weekly_universe_update` |
| 07:00 | 토 | 주간 리뷰 리마인더 | `weekly_review` |
| 07:05 | 월 | 컨센서스 캐시 갱신 | `weekly_consensus_update` |
| 05:05 | 화~토 | 미국 장 마감 요약 (서머타임) | `us_market_summary` |
| 06:00 | 매일 | 매크로 대시보드 (AM) | `macro_dashboard` |
| 06:05 | 화~토 | 미국 장 마감 요약 (표준시) | `us_market_summary` |
| 15:40 | 평일 | 한국 장 마감 요약 + 수급 축적 | `daily_kr_summary` |
| 15:40 | 평일 | 수급 이탈 감지 | `check_supply_drain` |
| 15:45 | 평일 | 모멘텀 이탈 체크 | `momentum_exit_check` |
| 15:50 | 평일 | 포트폴리오 스냅샷 + 드로다운 | `snapshot_and_drawdown` |
| 18:00 | 매일 | 매크로 대시보드 (PM) + 섹터 로테이션 | `macro_dashboard` |
| 22:00 | 매일 | GitHub Gist 자동 백업 | `auto_backup` |

---

## KIS API TR_ID (24개)

### 국내 (19개)

| TR_ID | 용도 | 함수 |
|-------|------|------|
| `FHKST01010100` | 현재가 | `kis_stock_price` |
| `FHKST01010600` | 신용잔고 | `kis_credit_balance` |
| `FHKST01010700` | 공매도 | `kis_short_selling` |
| `FHKST01010900` | 투자자 수급 | `kis_investor_trend` |
| `FHKST01011800` | 종목 뉴스 | `kis_news_title` |
| `FHKST03010100` | 일봉 차트 | `kis_daily_volumes` / `kis_daily_closes` |
| `FHKUP03500100` | 업종별 시세 | `kis_sector_price` / `_fetch_sector_flow` |
| `FHPST01390000` | VI 발동 현황 | `kis_vi_status` |
| `FHPST01680000` | 체결강도 상위 | `kis_volume_power_rank` |
| `FHPST01700000` | 등락률 순위 | `kis_fluctuation_rank` |
| `FHPST01710000` | 거래량 상위 | `kis_volume_rank_api` |
| `FHPST01740000` | 시가총액 상위 | `fetch_universe_from_krx` |
| `FHPST04830000` | 공매도 일별 | `kis_daily_short_sale` |
| `FHPTJ04060100` | 외국인 순매수 상위 | `kis_foreigner_trend` |
| `FHPTJ04160001` | 투자자 일별 수급 | `kis_investor_trend_history` |
| `FHPTJ04400000` | 외인+기관 합산 | `kis_foreign_institution_total` |
| `FHPUP02100000` | KOSPI/KOSDAQ 지수 | `get_kis_index` |
| `HHKST668300C0` | 종목 추정실적 | `kis_estimate_perform` |
| `HHPPG046600C1` | 프로그램매매 | `kis_program_trade_today` |
| `HHPTJ04160200` | 장중 추정 수급 | `kis_investor_trend_estimate` |
| `CTPF1002R` | 종목 기본정보 | `kis_stock_info` |

### 해외 (3개)

| TR_ID | 용도 | 함수 |
|-------|------|------|
| `HHDFS00000300` | 해외 현재가 | `kis_us_stock_price` |
| `HHDFS76200200` | 해외 현재가상세 (PER/PBR) | `kis_us_stock_detail` |
| `HHDFS76290000` | 해외 등락률 상위/하위 | `kis_us_updown_rate` |

### 외부 데이터 소스

| 소스 | 용도 | 파일 |
|------|------|------|
| FnGuide | 컨센서스 목표주가/투자의견 | `kis_api.py` |
| Yahoo Finance | 매크로 지표 (VIX/WTI/금/환율), US 컨센서스 | `kis_api.py` |
| DART | 전자공시 검색/기업재무 | `kis_api.py` |
| FinanceDataReader | 한국 주식 장기 일봉 (3년) | `kis_api.py` |
| yfinance | 미국 주식 장기 일봉 (3년) | `kis_api.py` |
| KRX | 투자자 매매동향 크롤링 | `kis_api.py` |
| 네이버증권 리서치 | 증권사 리포트 크롤링 | `report_crawler.py` |

---

## 데이터 파일 (/data/*.json)

| 파일 | 용도 | 백업 |
|------|------|------|
| `watchlist.json` | 한국 워치리스트 `{ticker: name}` | O |
| `us_watchlist.json` | 미국 워치리스트 `{ticker: {name, qty}}` | O |
| `portfolio.json` | 보유 포트폴리오 `{ticker: {name, qty, avg_price}}` | O |
| `stoploss.json` | 손절/목표가 알림 | O |
| `watchalert.json` | 매수 희망가 감시 | O |
| `portfolio_history.json` | 포트폴리오 일별 스냅샷 | O |
| `trade_log.json` | 매매 기록 | O |
| `decision_log.json` | 투자판단 기록 | O |
| `watchlist_log.json` | 워치리스트 변경 이력 | O |
| `consensus_cache.json` | FnGuide 컨센서스 캐시 | O |
| `reports.json` | 증권사 리포트 (90일 보관) | O |
| `dart_seen.json` | DART 알림 발송 ID | X |
| `watch_sent.json` | 매수감시 당일 발송 기록 | X |
| `stoploss_sent.json` | 손절 알림 당일 발송 횟수 | X |
| `events.json` | 매크로 이벤트 캘린더 | X |
| `weekly_base.json` | 주간 리뷰 기준 스냅샷 | X |
| `stock_universe.json` | 종목 유니버스 (시총 상위) | X |
| `sector_flow_cache.json` | 섹터 수급 캐시 (16:30 이후) | X |
| `sector_rotation.json` | 섹터 로테이션 전일 데이터 | X |
| `supply_history.json` | 수급 히스토리 (180일 축적) | X |
| `dart_screener_cache.json` | DART 스크리너 당일 캐시 | X |
| `dart_corp_map.json` | DART 고유번호 매핑 | X |

---

## 환경변수

| 변수 | 필수 | 용도 |
|------|------|------|
| `TELEGRAM_TOKEN` | O | 텔레그램 봇 토큰 |
| `CHAT_ID` | O | 텔레그램 채팅 ID |
| `KIS_APP_KEY` | O | KIS Open API 앱키 |
| `KIS_APP_SECRET` | O | KIS Open API 시크릿 |
| `DART_API_KEY` | — | DART 전자공시 API 키 |
| `GITHUB_TOKEN` | — | Gist 백업용 토큰 |
| `BACKUP_GIST_ID` | — | 백업 Gist ID |
| `PORT` | — | Railway 자동 주입 (기본 8080) |

---

## 패키지 (requirements.txt)

| 패키지 | 용도 |
|--------|------|
| `python-telegram-bot[job-queue]` | 텔레그램 봇 + 스케줄러 |
| `aiohttp` | 비동기 HTTP (KIS API + MCP SSE) |
| `tzdata` | 타임존 데이터 |
| `beautifulsoup4` | HTML 파싱 (크롤링) |
| `lxml` | XML/HTML 파서 |
| `requests` | 동기 HTTP (크롤링, KRX) |
| `finance-datareader` | 한국 주식 장기 일봉 |
| `yfinance` | 미국 주식 장기 일봉 |
| `pdfplumber` | PDF 텍스트 추출 |

---

## 테스트 현황

| 파일 | 테스트 수 | 대상 |
|------|----------|------|
| `test_phase_b.py` | 13 | 감성분석, 섹터로테이션, 시뮬레이션 |
| `test_backtest.py` | 18 | 5전략, look-ahead bias, 비용, 엣지케이스 |
| `test_data_extension.py` | 14 | FDR, yfinance, KRX, Y모드 |
| `test_mcp_consolidation.py` | 41 | 19개 통합도구 라우팅 검증 |
| `test_report.py` | 31 | 크롤링, PDF추출, MCP도구, 감성분석 |
| **합계** | **117** | |

---

## Claude Code 에이전트

`.claude/agents/` 에 4개 커스텀 에이전트 설정:

| 에이전트 | 모델 | 역할 |
|---------|------|------|
| python-developer | opus | 3개 파일 코드 수정, API 연동 |
| kis-api-specialist | sonnet | KIS API 조사, TR_ID 분석 |
| test-writer | sonnet | pytest 테스트 작성 |
| code-reviewer | sonnet | 읽기 전용 리뷰, 버그 헌팅 |
