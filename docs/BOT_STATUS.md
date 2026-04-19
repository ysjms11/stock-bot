# stock-bot 프로젝트 현황

> 최종 업데이트: 2026-04-20

## 아키텍처

```
맥미니 M4 로컬 서버 (192.168.0.36)
├── main.py          텔레그램 봇 + 자동알림 스케줄러 + 진입점
├── kis_api.py       KIS/DART/Yahoo API + 데이터 I/O + WebSocket + 매크로 + 백업
├── mcp_tools.py     MCP 도구 스키마 + 실행 로직 + SSE 서버
├── db_collector.py  KIS API + KRX OPEN API 풀수집 + SQLite DB + 기술지표 + 스캐너
├── krx_crawler.py   KRX DB 로드 & 스캐너 (레거시 JSON 파일 호환)
├── report_crawler.py 증권사 리포트 크롤링 + PDF 텍스트 추출
└── data/
    ├── stock.db     SQLite DB (~320MB)
    └── *.json       워치/포트/손절/알림 등 상태 파일
```

외부 접근: Cloudflare Tunnel → `https://bot.arcbot-server.org/mcp`

---

## MCP 도구 (38개)

| # | 도구명 | 파라미터 | 용도 |
|---|--------|---------|------|
| 1 | `get_rank` | type: price/us_price/volume/scan/after_hours/dividend | 등락률·체결강도·거래량·시간외·배당 순위 통합 |
| 2 | `get_portfolio` | mode: set/생략 | 포트폴리오 조회/수정 (KR+US, cash) |
| 3 | `get_stock_detail` | ticker, period, mode | 종목 상세 + 일봉 + 볼륨프로파일 + 호가 |
| 4 | `get_supply` | mode: daily/history/estimate/foreign_rank/combined_rank/broker_rank | 수급 분석 통합 |
| 5 | `get_dart` | mode: 생략/report/report_list/read/insider | DART 공시 + 내부자 거래 클러스터 |
| 6 | `get_macro` | mode: dashboard/sector_etf/convergence/op_growth 등 | 매크로 + 스크리너 통합 |
| 7 | `get_sector` | mode: flow/rotation | 업종별 수급 + 섹터 로테이션 |
| 8 | `manage_watch` | action: add/remove/list | 워치리스트 추가/제거/조회 (KR+US, 매수감시 포함) |
| 9 | `get_alerts` | brief | 손절가/매수감시 목록 + 현재가 대비 % |
| 10 | `get_market_signal` | mode: short_sale/vi/program_trade/credit/lending | 공매도·VI·프로그램매매·신용잔고·대차 통합 |
| 11 | `get_news` | ticker, sentiment | 뉴스 헤드라인 + 감성분석(긍정/부정/중립) |
| 12 | `get_consensus` | ticker, brief | FnGuide(KR) 컨센서스 |
| 13 | `set_alert` | log_type: stop/buy/decision/compare/trade/delete | 알림 등록 + 투자판단 + 매매기록 + 삭제 |
| 14 | `get_portfolio_history` | days | 스냅샷 히스토리 + 드로다운 + 투자규칙 경고 |
| 15 | `get_trade_stats` | period | 매매 성과 분석 (승률·손익·보유기간) |
| 16 | `backup_data` | action: backup/restore/status | GitHub Gist 백업·복원 |
| 17 | `simulate_trade` | sells, buys | 매매 시뮬레이션 (비중·섹터·RR 미리보기) |
| 18 | `get_backtest` | ticker, period, strategy | 백테스트 (5전략, D250/Y1~Y5, 비용반영) |
| 19 | `manage_report` | action: list/collect/tickers | 증권사 리포트 조회·수집·대상종목 |
| 20 | `get_regime` | — | 시장 국면 판단 (매크로 기반 레짐) |
| 21 | `get_scan` | preset, filters | KRX 전종목 스크리너 (6개 프리셋) |
| 22 | `get_finance_rank` | rank_type: 생략/fscore/mscore_safe/fcf_yield | 재무비율/F-Score/M-Score/FCF 순위 |
| 23 | `get_highlow` | — | 52주 신고가/신저가 근접 종목 |
| 24 | `get_broker` | ticker | 종목별 거래원 매수/매도 상위 5곳 |
| 25 | `read_file` | path | stock-bot 디렉토리 내 파일 읽기 |
| 26 | `write_file` | path, content | stock-bot 디렉토리 내 파일 쓰기 |
| 27 | `list_files` | path | 파일/폴더 목록 (depth 2) |
| 28 | `read_report_pdf` | report_id, page | 리포트 PDF 이미지 렌더링 |
| 29 | `get_change_scan` | preset (콤마 복합) | 변화 감지 스캔 (9개 프리셋) |
| 30 | `git_status` | — | Git 브랜치/변경파일 조회 |
| 31 | `git_diff` | path, staged | 변경내용 조회 |
| 32 | `git_log` | n | 최근 커밋 로그 |
| 33 | `git_commit` | files, message | 파일 지정 커밋 (.py/.env 차단) |
| 34 | `git_push` | — | origin/main push |
| 35 | `get_alpha_metrics` | ticker | F-Score/M-Score/FCF 메트릭 조회 |
| 36 | `get_us_ratings` | ticker, mode: events/trend/consensus | 미국 종목 애널 레이팅 조회 |
| 37 | `get_us_scan` | mode: watchlist/discovery/sector | 미국 애널 레이팅 스캔/발굴 |
| 38 | `get_us_analyst` | name, firm, sector, top | 미국 애널 개인/그룹 조회 |

---

## 자동 스케줄러 (30+ 잡)

| 시간 (KST) | 주기/요일 | 기능 | 함수 |
|------------|-----------|------|------|
| 5분마다 | 반복 | DART 공시 체크 (8~20시 내부 필터) | `check_dart_disclosure` |
| 10분마다 | 반복 | 손절선/매수감시 도달 알림 | `check_stoploss` |
| 30분마다 | 반복 | 거래량+외국인 이상 신호 | `check_anomaly` |
| 60분마다 | 반복 | 시장 레짐 전환 알림 | `regime_transition_alert` |
| 02:00 | 매일 | DART 신규 정기공시 증분 수집 | `daily_dart_incremental` |
| 05:05 | 화~토 | 미국 장 마감 요약 (DST) | `us_market_summary` |
| 06:00 | 매일 | 매크로 대시보드 (AM) | `macro_dashboard` |
| 06:05 | 화~토 | 미국 장 마감 요약 (표준시) | `us_market_summary` |
| 07:00 | 평일 | 한국 실적/배당 캘린더 | `check_earnings_calendar` |
| 07:00 | 토 | 주간 리뷰 리마인더 | `weekly_review` |
| 07:00 | 월 | KOSPI250+KOSDAQ350 유니버스 갱신 | `weekly_universe_update` |
| 07:05 | 일 | FnGuide 컨센서스 주간 업데이트 | `weekly_consensus_update` |
| 07:05 | 일 | daily_snapshot 영업일 누락 감시 | `weekly_sanity_check` |
| 07:10 | 평일 | 미국 실적 캘린더 | `check_us_earnings_calendar` |
| 07:15 | 일 | 주간 재무 수집 (DART) | `weekly_financial_job` |
| 07:30 | 매일 | 미국 애널 레이팅 스캔 | `daily_us_rating_scan` |
| 08:30 | 평일 | 증권사 리포트 수집 | `collect_reports_daily` |
| 12:00 (ET) | 평일 | 미국 보유종목 애널 레이팅 감시 | `hourly_us_holdings_check` |
| 15:40 | 평일 | 한국 장 마감 요약 | `daily_kr_summary` |
| 15:40 | 평일 | 수급 이탈 감지 | `check_supply_drain` |
| 15:50 | 평일 | 포트폴리오 스냅샷 + 드로다운 | `snapshot_and_drawdown` |
| 16:30 | 평일 | 모멘텀 이탈 체크 | `momentum_exit_check` |
| 16:30 (ET) | 평일 | 미국 장 마감 애널 레이팅 감시 | `hourly_us_holdings_check` |
| 18:30 | 평일 | KRX 전종목 DB 수집 (SQLite) | `daily_collect_job` |
| 18:55 | 매일 | 매크로 대시보드 (PM) | `macro_dashboard` |
| 19:00 | 평일 | 워치리스트 변경 감지 | `watch_change_detect` |
| 19:00 | 일 | Sunday 30 리마인더 | `sunday_30_reminder` |
| 19:05 | 평일 | 발굴 알림 (turnaround/fscore_jump/insider_cluster_buy) | `daily_change_scan_alert` |
| 19:30 | 평일 | 컨센서스 상향 체크 | `daily_consensus_check` |
| 20:00 | 평일 | 내부자 군집 감지 (워치종목) | `check_insider_cluster` |
| 22:00 | 매일 | GitHub Gist 자동 백업 | `auto_backup` |

---

## SQLite DB 스키마 (stock.db)

| 테이블 | 내용 |
|--------|------|
| `stock_master` | 종목 기본정보 (코드, 이름, 시장, 섹터, PER/PBR) |
| `daily_snapshot` | 일별 시세+수급+기술지표 (기준가, 외인/기관 순매수, MA, RSI, MACD 등) |
| `financial_quarterly` | 분기별 재무데이터 (매출/영업이익/순이익/부채비율) |
| `consensus_history` | FnGuide 컨센서스 이력 |
| `reports` | 증권사 리포트 메타데이터 |
| `insider_transactions` | 임원·주요주주 내부자 거래 |

---

## 환경변수

| 변수 | 필수 | 용도 |
|------|------|------|
| `TELEGRAM_TOKEN` | O | 텔레그램 봇 토큰 |
| `CHAT_ID` | O | 텔레그램 채팅 ID |
| `KIS_APP_KEY` | O | KIS Open API 앱키 |
| `KIS_APP_SECRET` | O | KIS Open API 시크릿 |
| `DART_API_KEY` | — | DART 전자공시 API 키 |
| `KRX_API_KEY` | — | KRX OPEN API 인증키 (db_collector 18:30 사용) |
| `GITHUB_TOKEN` | — | Gist 백업용 토큰 |
| `BACKUP_GIST_ID` | — | 백업 Gist ID |
| `DATA_DIR` | — | 데이터 디렉토리 경로 |
| `PORT` | — | 서버 포트 (기본 8080) |

---

## 최근 주요 변경사항

| 날짜 | 변경 내용 |
|------|-----------|
| 2026-04-18 | `get_us_ratings` / `get_us_scan` / `get_us_analyst` 도구 3개 추가 (미국 애널 레이팅) |
| 2026-04-18 | `daily_us_rating_scan` 스케줄 추가 (07:30 매일) |
| 2026-04-18 | `weekly_sanity_check` 추가 (daily_snapshot 영업일 누락 감시) |
| 2026-04-18 | `daily_collect_job` 안전장치 3종 (post_init retry, 주간 무결성 검사) |
| 2026-04-18 | `daily_change_scan_alert` 추가 (19:05 평일, 발굴 알림) |
| 2026-04-18 | DART 체크 주기 30분 → 5분으로 단축 |
| 2026-04-18 | `hourly_us_holdings_check` 추가 (미국 장중/마감 ET 시각 기반) |
| 2026-04-16 | `dart_incremental` 추가 (02:00 신규 정기공시 증분 수집) |
| 2026-04-15 | 내부자 거래 클러스터 감지 (`check_insider_cluster`, 20:00 평일) |
| 2026-04-12 | SQLite 기반 daily_snapshot 수집 시작 (db_collector.py) |
| 2026-03-29 | 거버넌스 지표 롤백 (후행지표 판단으로 제거) |

---

## 알려진 이슈

**버그 함정**
- 미국 현재가 `rate` 필드: KIS 해외 응답은 `rate`. `diff_rate` 없음. 전 코드 `rate` 통일됨.
- WebSocket 국내 전용: `KisRealtimeManager`는 국내만. 미국은 Yahoo Finance 폴링.
- KRX OPEN API 간헐 장애: 자주 빈 응답. `db_collector`가 `stock_master` fallback으로 KIS API 직접 호출.

**데이터 성숙 대기 중 (4/12부터 수집)**
- `short_squeeze`: ~5/14 (20d 데이터 필요)
- `foreign_accumulation`: ~4/19 (5d). 계산 로직 5줄 추가 필요
- `credit_unwind`: `whol_loan_rmnd_rate` 저장 + 계산 필요
