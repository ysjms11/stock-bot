# stock-bot

맥미니 M4 로컬 서버 기반 Python MCP 서버 + 텔레그램 봇.
KIS Open Trading API 기반 한국/미국 주식 조회, 손절/목표가 알림, 매크로 대시보드, 스크리너 등을 Claude MCP 도구로 제공한다.

---

## 인프라

| 항목 | 내용 |
|------|------|
| 레포 | https://github.com/ysjms11/stock-bot |
| 배포 | 맥미니 M4 (192.168.0.36), launchd 자동시작 |
| MCP URL | `https://bot.arcbot-server.org/mcp` (SSE) |
| MCP messages | `https://bot.arcbot-server.org/mcp/messages?sessionId=<id>` (POST) |
| Health check | `https://bot.arcbot-server.org/health` |
| Cloudflare Tunnel | `com.stock-bot.cloudflared` (launchd) |
| 도메인 | `arcbot-server.org` |
| 포트 | 환경변수 `PORT` (기본 8080) |

---

## 파일 구조

| 파일 | 줄 수 | 역할 |
|------|-------|------|
| `main.py` | ~1950 | 텔레그램 봇 + 자동알림 스케줄 + 진입점 |
| `kis_api.py` | ~2400 | KIS/DART/Yahoo API 함수, 데이터 파일 I/O, WebSocket, 매크로, 백업 |
| `mcp_tools.py` | ~1760 | MCP 도구 스키마 + 실행 로직 + SSE 서버 |
| `db_collector.py` | ~1700 | KIS API + KRX OPEN API 풀수집 + SQLite DB + 기술지표 + 스캐너 |
| `krx_crawler.py` | ~400 | KRX DB 로드 & 스캐너 (레거시 JSON 파일 호환) |
| `report_crawler.py` | — | 증권사 리포트 크롤링 + PDF 추출 |

---

## 주요 기능 요약

- **한국/미국 주식 실시간 조회** — KIS Open API 기반 현재가, PER/PBR, 수급, 일봉 차트
- **포트폴리오 관리** — 보유 종목 손익 추적, 히스토리 스냅샷, 드로다운 분석
- **손절/목표가 자동 알림** — 10분마다 가격 도달 시 텔레그램 알림
- **매크로 대시보드** — VIX, WTI, 금, DXY, US10Y, 환율 + 레짐 자동판정 (위기/경계/중립/공격)
- **스크리너** — KRX 전종목 SQLite 기반, 이평선 수렴, F-Score, M-Score, FCF/EV
- **수급 분석** — 외국인/기관 순매수, 프로그램매매, 투자자 추정 수급
- **DART 공시 모니터링** — 중요 공시 자동 알림 + 중요도 태그 (긴급/주의/참고/일반)
- **컨센서스** — FnGuide 기반 증권사 목표주가/투자의견
- **미국 애널 레이팅** — 종목별 이벤트/추세/컨센서스, 발굴 스캔, 애널 개인 조회
- **매매기록 + 성과추적** — 매매 로그, 승률/손익 분석, 확신등급 정확도
- **자동 백업** — GitHub Gist 백업 (코드=GitHub, DB/data=iCloud)

---

## MCP 도구 (38개)

| # | 이름 | 설명 |
|---|------|------|
| 1 | `get_rank` | 한국 등락률/체결강도/거래량/시간외/배당 순위. type=price/us_price/volume/scan/after_hours/dividend |
| 2 | `get_portfolio` | 포트폴리오 조회/수정 (한국+미국 손익, cash_krw/cash_usd) |
| 3 | `get_stock_detail` | 개별 종목 상세 (현재가·PER·PBR·수급). mode=volume_profile/after_hours/orderbook |
| 4 | `get_supply` | 수급 분석. mode=daily/history/estimate/foreign_rank/combined_rank/broker_rank |
| 5 | `get_dart` | DART 공시 (워치 3일, report/report_list/read/insider 모드) |
| 6 | `get_macro` | 매크로 지표. mode=dashboard/sector_etf/convergence/op_growth 등 |
| 7 | `get_sector` | 업종별 외인+기관 순매수, 업종 로테이션 분석 |
| 8 | `manage_watch` | 워치리스트 조회/추가/제거 (한국+미국, 매수감시 포함) |
| 9 | `get_alerts` | 손절가/목표가 목록 + 현재가 대비 % + 매수감시 |
| 10 | `get_market_signal` | 공매도/VI/프로그램매매/신용잔고/대차. mode=short_sale/vi/program_trade/credit/lending |
| 11 | `get_news` | 종목 뉴스 헤드라인 (한국/미국, sentiment 감성분석) |
| 12 | `get_consensus` | 증권사 컨센서스 목표주가/투자의견 (FnGuide) |
| 13 | `set_alert` | 손절가/목표가, 매수감시, 투자판단, 종목비교, 매매기록 |
| 14 | `get_portfolio_history` | 포트폴리오 스냅샷 히스토리 + 드로다운 + 투자규칙 경고 |
| 15 | `get_trade_stats` | 매매 기록 성과 분석 (승률·손익·평균보유기간) |
| 16 | `backup_data` | /data/*.json GitHub Gist 백업·복원·상태 조회 |
| 17 | `simulate_trade` | 가상 매매 시뮬레이션 |
| 18 | `get_backtest` | 백테스트 (ma_cross/momentum_exit/supply_follow/bollinger/hybrid) |
| 19 | `manage_report` | 리포트 관리 (`category=company/industry/market/strategy/economy/bond` 필터, 비종목 카테고리 자동 수집) |
| 20 | `get_regime` | 시장 국면 판단 (매크로 기반) |
| 21 | `get_scan` | KRX 전종목 스크리너 (시총/PER/PBR/수급/회전율, 6개 프리셋) |
| 22 | `get_finance_rank` | 전종목 재무비율/F-Score/M-Score/FCF 순위 |
| 23 | `get_highlow` | 52주 신고가/신저가 근접 종목 순위 |
| 24 | `get_broker` | 종목별 거래원(증권사) 매수/매도 상위 5곳 |
| 25 | `read_file` | stock-bot 디렉토리 내 파일 읽기 |
| 26 | `write_file` | stock-bot 디렉토리 내 파일 쓰기 |
| 27 | `list_files` | stock-bot 디렉토리 내 파일/폴더 목록 |
| 28 | `read_report_pdf` | 리포트 PDF 페이지 이미지 렌더링 |
| 29 | `get_change_scan` | 변화 감지 스캔 (ma_convergence/volume_spike/earnings_disconnect 등 9개 프리셋) |
| 30 | `git_status` | Git 브랜치/변경파일 조회 |
| 31 | `git_diff` | 변경내용 조회 |
| 32 | `git_log` | 최근 커밋 로그 |
| 33 | `git_commit` | 파일 지정 커밋 (.py/.env 차단) |
| 34 | `git_push` | origin/main push |
| 35 | `get_alpha_metrics` | 종목별 F-Score/M-Score/FCF 메트릭 조회 |
| 36 | `get_us_ratings` | 미국 종목 애널 레이팅 조회. mode=events/trend/consensus |
| 37 | `get_us_scan` | 미국 애널 레이팅 스캔/발굴. mode=watchlist/discovery/sector |
| 38 | `get_us_analyst` | 미국 애널 개인/그룹 조회 (name=개별, top 리스트, firm/sector 필터) |

---

## 자동 알림 스케줄 (30+ 잡)

### 반복 잡

| 주기 | 잡 | 핵심 동작 |
|------|----|-----------|
| 5분 | `dart` | DART 공시 체크 (8~20시 내부 필터) |
| 10분 | `stoploss` | 손절/목표가 감시 (한국 WebSocket, 미국 Yahoo 폴링) |
| 30분 | `anomaly` | 거래량+외국인 이상 신호 감지 |
| 60분 | `regime_transition` | 시장 레짐 전환 알림 |

### 일일 잡 (주요)

| 시간 (KST) | 요일 | 잡 | 핵심 동작 |
|-----------|------|-----|-----------|
| 02:00 | 전체 | `dart_incremental` | DART 신규 정기공시 증분 수집 |
| 05:05 / 06:05 | 화~토 | `us_summary` | 미국 장 마감 요약 (DST/표준시 이중 등록) |
| 06:00 | 전체 | `macro_am` | 매크로 대시보드 (AM) |
| 07:00 | 평일 | `earnings_cal` | 한국 실적/배당 캘린더 |
| 07:00 | 토 | `weekly` | 주간 리뷰 |
| 07:00 | 월 | `universe_update` | KOSPI250+KOSDAQ350 유니버스 갱신 |
| 07:05 | 일 | `consensus_update` | FnGuide 컨센서스 주간 업데이트 |
| 07:30 | 전체 | `us_ratings` | 미국 애널 레이팅 스캔 |
| 08:30 | 평일 | `report_collect` | 증권사 리포트 수집 (종목+산업/시황/전략/경제 4 카테고리) |
| 15:40 | 평일 | `kr_summary` | 한국 장 마감 요약 + 수급 이탈 감지 |
| 15:50 | 평일 | `snapshot_dd` | 포트폴리오 스냅샷 + 드로다운 체크 |
| 16:30 | 평일 | `momentum_check` | 모멘텀 이탈 체크 |
| 18:30 | 평일 | `daily_collect` | KRX 전종목 DB 수집 (SQLite) |
| 18:55 | 전체 | `macro_pm` | 매크로 대시보드 (PM) |
| 19:05 | 평일 | `daily_change_scan` | 발굴 알림 (turnaround/fscore_jump/insider_cluster_buy) |
| 19:30 | 평일 | `daily_consensus` | 컨센서스 상향 체크 |
| 20:00 | 평일 | `insider_cluster` | 내부자 군집 감지 (3명+ 매수 AND 순매수>0) |
| 19:07 | 일 | `weekly_report_digest` | 비종목 리포트 분석 시간 알림 (통계 + Claude.ai 프롬프트 템플릿) |
| 22:00 | 전체 | `auto_backup` | GitHub Gist 자동 백업 |

---

## 매크로 레짐 자동판정

| 레짐 | 조건 |
|------|------|
| 위기 | VIX >= 30, WTI >= $100, KOSPI <= -5% 중 하나 |
| 경계 | VIX >= 25, WTI >= $90, KOSPI <= -3%, USD/KRW >= 1500 중 하나 |
| 공격 | VIX < 20 AND KOSPI > 0 AND 외인순매수 > 0 AND USD/KRW < 1400 모두 충족 |
| 중립 | 위 조건 미해당 |

---

## 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `TELEGRAM_TOKEN` | O | 텔레그램 봇 토큰 |
| `CHAT_ID` | O | 텔레그램 채팅 ID |
| `KIS_APP_KEY` | O | KIS Open API 앱키 |
| `KIS_APP_SECRET` | O | KIS Open API 시크릿 |
| `DART_API_KEY` | — | 전자공시 API 키 |
| `KRX_API_KEY` | — | KRX OPEN API 인증키 (db_collector가 18:30 사용) |
| `GITHUB_TOKEN` | — | GitHub Personal Access Token (Gist 백업) |
| `BACKUP_GIST_ID` | — | 백업 Gist ID |
| `DATA_DIR` | — | 데이터 디렉토리 경로 (기본 /Users/kreuzer/stock-bot/data) |
| `PORT` | — | 서버 포트 (기본 8080) |

---

## 데이터 저장

| 저장소 | 내용 |
|--------|------|
| `data/stock.db` | SQLite DB (~320MB) — stock_master / daily_snapshot / financial_quarterly / consensus_history / reports / insider_transactions |
| `data/*.json` | 워치/포트/손절/알림 등 상태 파일 (전체 목록 → `.claude/rules/data-files.md`) |
| GitHub Gist | `data/*.json` 자동 백업 (매일 22:00) |
| iCloud | `stock.db` 및 대용량 데이터 백업 (`backup_to_icloud`) |

---

## 배포

```
main push → 맥미니 git pull → launchd 재시작 → MCP + 텔레그램 봇 동시 실행
```

맥미니 배포 절차:
1. GitHub `main` 브랜치에 push
2. 맥미니에서 `git pull` 후 launchd 서비스 재시작
3. Health check: `GET https://bot.arcbot-server.org/health` → `{"status": "ok"}`
4. Cloudflare Tunnel이 외부 → 로컬 8080 포트로 라우팅
