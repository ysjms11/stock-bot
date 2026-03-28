# stock-bot

Railway 배포 Python MCP 서버 + 텔레그램 봇.
KIS Open Trading API 기반 한국/미국 주식 조회, 손절/목표가 알림, 매크로 대시보드, 스크리너 등을 Claude MCP 도구로 제공한다.

---

## 인프라

| 항목 | 내용 |
|------|------|
| 레포 | https://github.com/ysjms11/stock-bot |
| 배포 | Railway (main 브랜치 push → 자동 배포) |
| MCP URL | `https://<railway-domain>/mcp` (SSE) |
| MCP messages | `https://<railway-domain>/mcp/messages?sessionId=<id>` (POST) |
| Health check | `https://<railway-domain>/health` |
| 데이터 저장 | Railway `/data` 볼륨 (영구 마운트) |

---

## 주요 기능 요약

- **한국/미국 주식 실시간 조회** — KIS Open API 기반 현재가, PER/PBR, 수급, 일봉 차트
- **포트폴리오 관리** — 보유 종목 손익 추적, 히스토리 스냅샷, 드로다운 분석
- **손절/목표가 자동 알림** — 10분마다 가격 도달 시 텔레그램 알림
- **매크로 대시보드** — VIX, WTI, 금, DXY, US10Y, 환율 + 레짐 자동판정 (위기/경계/중립/공격)
- **스크리너** — 이평선 수렴, 영업이익 성장/턴어라운드, DART 기반 재무 스크리닝
- **수급 분석** — 외국인/기관 순매수, 프로그램매매, 투자자 추정 수급
- **DART 공시 모니터링** — 중요 공시 자동 알림 + 중요도 태그 (긴급/주의/참고/일반)
- **컨센서스** — FnGuide 기반 증권사 목표주가/투자의견
- **매매기록 + 성과추적** — 매매 로그, 승률/손익 분석, 확신등급 정확도
- **자동 백업/복원** — GitHub Gist 기반 데이터 백업, 시작 시 자동복원

---

## MCP 도구 (28개)

| # | 이름 | 설명 |
|---|------|------|
| 1 | `scan_market` | 거래량 상위 종목 스캔 |
| 2 | `get_portfolio` | 포트폴리오 조회 또는 수정. mode='set' 시 저장, 생략 시 현재가/손익 조회. cash_krw/cash_usd로 현금 잔고 업데이트 |
| 3 | `get_stock_detail` | 개별 종목 상세 (현재가/PER/PBR/수급). 한국/미국 자동 판별. period 지정 시 일봉 반환 |
| 4 | `get_foreign_rank` | 외국인 순매수 상위 종목 |
| 5 | `get_dart` | 워치리스트 최근 3일 DART 공시 |
| 6 | `get_macro` | KOSPI/KOSDAQ 지수 + 환율. mode별: dashboard(레짐판정), sector_etf, convergence(이평수렴), op_growth/op_turnaround/dart_op_growth/dart_turnaround(재무스크리너) |
| 7 | `get_sector_flow` | WI26 주요 업종별 외국인+기관 순매수금액 상위/하위 3개 |
| 8 | `add_watch` | 한국 워치리스트에 종목 추가 (changelog 자동 기록) |
| 9 | `remove_watch` | 한국 워치리스트에서 종목 제거. alert_type='buy_alert' 시 매수감시 제거 |
| 10 | `get_alerts` | 손절가 목록 + 현재가 대비 % + 매수감시 목록 + 최근 변동 이력 20건 |
| 11 | `get_investor_flow` | 개별 종목 투자자별 수급 (외국인/기관/개인 매수/매도/순매수) |
| 12 | `get_price_rank` | 등락률 상위/하위 종목 순위 (sort='rise'/'fall', market='all'/'kospi'/'kosdaq') |
| 13 | `get_investor_trend_history` | 종목별 투자자 일별 수급 히스토리 (최대 N일) |
| 14 | `get_program_trade` | 프로그램매매 투자자별 당일 동향 |
| 15 | `get_investor_estimate` | 장중 투자자 추정 순매수 가집계 |
| 16 | `get_foreign_institution` | 외국인+기관 합산 순매수 상위 종목 (가집계) |
| 17 | `get_short_sale` | 국내주식 공매도 일별추이 |
| 18 | `get_news` | KIS 종목 관련 뉴스 헤드라인 최신순 |
| 19 | `get_vi_status` | 변동성완화장치(VI) 발동 종목 현황 |
| 20 | `get_volume_power` | 체결강도 상위 종목 순위 (매수/매도 체결 비율) |
| 21 | `get_us_price_rank` | 미국 주식 등락률 상위/하위 순위 (NAS/NYS/AMS) |
| 22 | `get_consensus` | 종목별 증권사 컨센서스 목표주가/투자의견 (FnGuide 기반) |
| 23 | `set_alert` | 손절가/목표가 등록, 매수감시, 투자판단 기록(decision), 종목비교(compare), 매매기록(trade) |
| 24 | `delete_alert` | 매도 후 stoploss.json에서 알림 완전 삭제 |
| 25 | `get_portfolio_history` | 포트폴리오 스냅샷 히스토리 + 드로다운 분석, 투자규칙 경고 |
| 26 | `get_trade_stats` | 매매 기록 성과 분석 (승률/손익/평균보유기간/확신등급 정확도) |
| 27 | `get_batch_detail` | 여러 한국 종목 일괄 조회 (최대 20종목, 현재가/등락률/PER/PBR/외인기관수급) |
| 28 | `backup_data` | /data/*.json GitHub Gist 백업/복원/상태조회 (backup/restore/restore_force/status) |

> **참고**: `set_watch_alert`, `get_watch_alerts`, `remove_watch_alert` 기능은 `set_alert`(buy_price), `get_alerts`, `remove_watch`(alert_type='buy_alert')로 통합됨.

---

## 텔레그램 명령어 (18개)

| 명령어 | 설명 |
|--------|------|
| `/start` | 봇 시작 인사 |
| `/analyze <종목코드>` | 종목 분석 (수급 포함) |
| `/scan` | 거래량 급등 TOP10 |
| `/macro` | VIX/환율/유가/금리/KOSPI/KOSDAQ 매크로 대시보드 |
| `/news [키워드]` | 뉴스 헤드라인 |
| `/dart` | 워치리스트 DART 공시 |
| `/summary` | 한국 장마감 요약 (수동 실행) |
| `/watchlist` | 한국 워치리스트 조회 |
| `/watch <코드> <이름>` | 한국 워치리스트 종목 추가 |
| `/unwatch <코드>` | 한국 워치리스트 종목 제거 |
| `/uslist` | 미국 워치리스트 조회 |
| `/addus <심볼> <이름> <수량>` | 미국 워치리스트 종목 추가 |
| `/remus <심볼>` | 미국 워치리스트 종목 제거 |
| `/setstop <코드> <이름> <손절가> [진입가]` | 손절/목표가 등록 |
| `/delstop <코드>` | 손절가 삭제 |
| `/stops` | 손절가 목록 조회 |
| `/setportfolio <코드> <이름> <수량> <평단가>` | 한국 포트폴리오 등록 |
| `/setusportfolio <심볼> <수량> <평단가>` | 미국 포트폴리오 등록 |
| `/help` | 도움말 |

---

## 자동 알림 스케줄

| 주기 | 시각 (KST) | 이름 | 설명 |
|------|-----------|------|------|
| 10분마다 | 장중 | `check_stoploss` | 손절선/매수희망가 도달 시 텔레그램 알림 |
| 30분마다 | 장중 | `check_anomaly` | 거래량+외국인 복합 이상 신호 감지 |
| 30분마다 | 08:00~16:30 | `check_dart_disclosure` | DART 중요 공시 알림 |
| 평일 15:40 | 15:40 | `daily_kr_summary` | 한국장 마감 요약 (지수/수급/손절/섹터/DART) |
| 평일 15:40 | 15:40 | `check_supply_drain` | 외국인/기관 수급 급감 감지 |
| 평일 15:45 | 15:45 | `momentum_exit_check` | 모멘텀 종료 감지 |
| 평일 15:50 | 15:50 | `snapshot_and_drawdown` | 포트폴리오 스냅샷 저장 + 드로다운 분석 |
| 평일 05:05/06:05 | 05:05 / 06:05 | `us_market_summary` | 미국장 마감 요약 (서머타임/표준시 이중 등록) |
| 매일 06:00 | 06:00 | `macro_dashboard` | 매크로 대시보드 (새벽) |
| 매일 18:00 | 18:00 | `macro_dashboard` | 매크로 대시보드 (한국장 마감 후) |
| 매일 22:00 | 22:00 | `auto_backup` | /data/*.json GitHub Gist 자동 백업 |
| 일요일 01:00 | 01:00 | `weekly_review` | 주간 리뷰 리마인더 |
| 월요일 07:00 | 07:00 | `weekly_universe_update` | 주간 유니버스 업데이트 |
| 월요일 07:05 | 07:05 | `weekly_consensus_update` | 주간 컨센서스 자동 업데이트 |

> **비활성화**: `check_fx_alert` (환율 ±1% 알림) — 매크로 대시보드로 통합 예정

---

## 매크로 레짐 자동판정

`get_macro(mode='dashboard')` 및 텔레그램 매크로 알림에 포함.

| 레짐 | 조건 |
|------|------|
| 🔴 위기 | VIX >= 30, WTI >= $100, KOSPI <= -5% 중 하나 |
| 🟠 경계 | VIX >= 25, WTI >= $90, KOSPI <= -3%, USD/KRW >= 1500 중 하나 |
| 🟢 공격 | VIX < 20 AND KOSPI > 0 AND 외인순매수 > 0 AND USD/KRW < 1400 모두 충족 |
| 🟡 중립 | 위 조건 미해당 |

---

## 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `TELEGRAM_TOKEN` | O | 텔레그램 봇 토큰 |
| `CHAT_ID` | O | 텔레그램 채팅 ID |
| `KIS_APP_KEY` | O | KIS Open API 앱키 |
| `KIS_APP_SECRET` | O | KIS Open API 시크릿 |
| `DART_API_KEY` | - | 전자공시 API 키 (DART 공시 기능 활성화) |
| `GITHUB_TOKEN` | - | GitHub Personal Access Token (Gist 백업/복원) |
| `BACKUP_GIST_ID` | - | 백업용 Gist ID (자동 생성 가능) |
| `PORT` | - | 서버 포트 (Railway 자동 주입, 기본 8080) |

---

## 데이터 파일 (`/data/*.json`)

| 파일 | 내용 |
|------|------|
| `watchlist.json` | 한국 워치리스트 `{ticker: name}` |
| `us_watchlist.json` | 미국 워치리스트 `{ticker: {name, qty}}` |
| `stoploss.json` | 손절/목표가 `{ticker: {name, stop_price, ...}, us_stocks: {...}}` |
| `portfolio.json` | 보유 포트폴리오 `{ticker: {name, qty, avg_price}, us_stocks: {...}}` |
| `dart_seen.json` | DART 알림 전송된 공시 ID |
| `watchalert.json` | 매수 희망가 감시 목록 |
| `watchlist_log.json` | 워치리스트 변동 이력 (최대 200건) |
| `portfolio_history.json` | 포트폴리오 일별 스냅샷 + 드로다운 |
| `trade_log.json` | 매매 기록 로그 |
| `dart_screener_cache.json` | DART 스크리너 당일 캐시 |

---

## 배포 (Railway)

1. GitHub 레포 `main` 브랜치에 push
2. Railway가 자동 감지하여 빌드/배포
3. `/data` 볼륨을 영구 마운트 설정 (재배포 후 데이터 보존)
4. 환경변수를 Railway Variables에 등록
5. Health check: `GET /health` → `{"status": "ok"}`

```
main push → Railway 자동 빌드 → 배포 → MCP + 텔레그램 봇 동시 실행
```

---

## 워치리스트 변동 이력

- `add_watch` / `remove_watch` / `set_alert`(update) / `delete_alert` 실행 시 `/data/watchlist_log.json`에 자동 기록
- `get_alerts` 응답에 `recent_changelog` (최근 20건) 포함
- 최대 200건 보관
