# 데이터 파일 경로 (`/data/*.json`)

| 파일 | 내용 | 기본값 |
|------|------|--------|
| `/data/watchlist.json` | 한국 워치리스트 `{ticker: name}` | 5개 기본 종목 |
| `/data/us_watchlist.json` | 미국 워치리스트 `{ticker: {name, qty}}` | TSLA 등 4개 |
| `/data/stoploss.json` | 손절/목표가 `{ticker: {name, stop_price, ...}, us_stocks: {...}}` | `{}` |
| `/data/portfolio.json` | 보유 포트폴리오 `{ticker: {name, qty, avg_price}, us_stocks: {...}}` | `{}` |
| `/data/dart_seen.json` | DART 알림 전송된 공시 ID 목록 `{ids: [...]}` | `{ids: []}` |
| `/data/insider_sent.json` | 내부자 클러스터 알림 최근 발송 `{ticker: "YYYY-MM-DD"}` (7일 쿨다운) | `{}` |
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
| `/data/krx_db/YYYYMMDD.json` | KRX 전종목 일별 DB (시세+수급+비율, 보관 무제한) | — |
| `/data/std_sector_map.json` | 표준산업분류코드 캐시 `{ticker: {std_code, std_name}}` (1회 수집) | `{}` |
| `/data/stock.db` | SQLite DB (stock_master + daily_snapshot + financial_snapshot + 뷰, ~277MB) | — |
| `/data/db_schema.sql` | SQLite DB 스키마 정의 (테이블/인덱스/뷰 DDL) | — |

> 맥미니 로컬 `data/` 디렉토리 사용 (`DATA_DIR` 환경변수).
> 환경변수 기반 자동복원 fallback 있음 (`BACKUP_PORTFOLIO`, `BACKUP_STOPLOSS` 등).

> **상세 참조**: 파일별 함수 구조 → `.claude/rules/file-structure.md`, KIS API TR_ID 테이블 → `.claude/rules/kis-api-reference.md`
