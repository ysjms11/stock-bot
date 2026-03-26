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
| 데이터 저장 | Railway `/data` 볼륨 (영구 마운트) |

---

## MCP 도구 (24개)

| # | 이름 | 설명 |
|---|------|------|
| 1 | `scan_market` | 거래량 상위 15개 종목 + 외국인 순매수 |
| 2 | `get_portfolio` | 보유 포트폴리오 (한국+미국) 손익 |
| 3 | `get_stock_detail` | 국내 개별 종목 상세 (현재가·PER·PBR·수급) |
| 4 | `get_foreign_rank` | 외국인 순매수 상위 종목 |
| 5 | `get_dart` | 워치리스트 최근 3일 DART 공시 |
| 6 | `get_macro` | KOSPI·KOSDAQ 지수 + 환율. mode='dashboard': VIX/WTI/금/DXY/US10Y + **레짐 자동판정**. mode='convergence': 이평선 수렴 스크리너 (disp_20/disp_60 이격도, market/sort 옵션). mode='op_growth'/'op_turnaround'/'dart_op_growth'/'dart_turnaround': 재무 스크리너 |
| 7 | `get_sector_flow` | WI26 업종별 외국인+기관 순매수 상위/하위 3개 |
| 8 | `add_watch` | 한국 워치리스트 종목 추가 (changelog 자동 기록) |
| 9 | `remove_watch` | 한국 워치리스트 종목 제거 (changelog 자동 기록) |
| 10 | `get_alerts` | 손절가 목록 + 현재가 대비 % + 최근 변동 이력 20건 |
| 11 | `set_alert` | 손절가/목표가 등록·수정, 매수감시, 투자판단/종목비교 기록 |
| 12 | `get_us_stock_detail` | 미국 개별 종목 상세 (현재가·등락률·PER·PBR·시총·52주) |
| 13 | `set_watch_alert` | 매수 희망가 감시 등록 (가격 도달 시 텔레그램 알림) |
| 14 | `get_watch_alerts` | 매수 희망가 감시 목록 조회 |
| 15 | `remove_watch_alert` | 매수 희망가 감시 제거 |
| 16 | `get_investor_flow` | 국내 종목별 투자자 수급 (외국인·기관·개인) |
| 17 | `get_price_rank` | 국내 등락률 상위/하위 종목 순위 |
| 18 | `get_investor_trend_history` | 종목별 투자자 일별 수급 히스토리 (최대 N일) |
| 19 | `get_program_trade` | 프로그램 매매 동향 |
| 20 | `get_investor_estimate` | 투자자별 추정 순매수 |
| 21 | `get_foreign_institution` | 외국인·기관 동시 순매수 종목 |
| 22 | `get_short_sale` | 공매도 현황 |
| 23 | `get_consensus` | 종목별 증권사 컨센서스 목표주가/투자의견 (FnGuide JSON API). 평균·최고·최저 목표주가, 매수/중립/매도 건수, 증권사별 최신 목표가 |
| 24 | `delete_alert` | 매도 후 stoploss.json에서 손절/목표가 알림 완전 삭제. watchlist_log에 delete_alert 기록 |

---

## 자동 스케줄러

| 시각 (KST) | 내용 |
|------------|------|
| 06:40 | 한국장 개장 전 요약 (지수·수급·손절·섹터·DART) |
| 18:00 | 매크로 대시보드 (VIX·WTI·금·DXY·환율·외인수급·이벤트 + **레짐 자동판정**) |
| 06:00 | 매크로 대시보드 (동일, 새벽 추가 발송) |
| 22:00 | 미국장 마감 요약 (S&P500·나스닥·VIX·환율) |
| 10분마다 | 손절선/매수희망가 도달 알림 |
| 30분마다 | 거래량·외국인 이상 신호 감지, DART 중요 공시 |
| 1시간마다 | 환율 ±1% 이상 변동 알림 |
| 매주 일요일 01:00 | 주간 리뷰 리마인더 |

---

## 워치리스트 변동 이력

- `add_watch` / `remove_watch` / `set_alert`(update) 실행 시 `/data/watchlist_log.json`에 자동 기록
- `get_alerts` 응답에 `recent_changelog` (최근 20건) 포함
- 최대 200건 보관

---

## 매크로 레짐 자동판정

`get_macro(mode='dashboard')` 및 텔레그램 매크로 알림에 포함.

| 레짐 | 조건 |
|------|------|
| 🔴 위기 | VIX ≥ 30, WTI ≥ $100, KOSPI ≤ −5% 중 하나 |
| 🟠 경계 | VIX ≥ 25, WTI ≥ $90, KOSPI ≤ −3%, USD/KRW ≥ 1500 중 하나 |
| 🟢 공격 | VIX < 20 AND KOSPI > 0 AND 외인순매수 > 0 AND USD/KRW < 1400 모두 충족 |
| 🟡 중립 | 위 조건 미해당 |

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

---

## TODO

### P1 — 완료

| # | 항목 | 완료일 | 비고 |
|---|------|--------|------|
| 18 | 워치리스트 변동 이력 | 2026-03-26 | add/remove/update 시 watchlist_log.json 자동 저장, get_alerts에 recent_changelog |
| 15 | 컨센서스 조회 | 2026-03-26 | FnGuide 내부 JSON API 발굴. get_consensus 도구 추가 |
| 12 | 이평선 수렴 스크리너 고도화 | 2026-03-26 | disp_20/disp_60 이격도 추가, market='all' 코스피+코스닥 통합, sort 옵션 |
| 14 | 매크로 대시보드 판정 자동화 | 2026-03-26 | VIX/WTI/KOSPI/환율 기반 RED/ORANGE/YELLOW/GREEN 4단계, 텔레그램 알림 포함 |
| — | delete_alert 도구 | 2026-03-26 | 매도 후 알림 완전 삭제 |
| — | get_investor_trend_history 버그 수정 | 2026-03-26 | rows 타입 체크로 KeyError: slice 해결 |

### P1 — 잔여

| # | 항목 | 비고 |
|---|------|------|
| 16 | KOSPI200 배치 인프라 | 전종목 일봉 데이터 배치 수집 |

### P2 — 다음 달

| # | 항목 |
|---|------|
| 17 | 모멘텀 종료 감지 |
| 13 | 영업이익 스크리너 고도화 |
| 10 | summary 추가 고도화 |
| — | 텔레그램 알림에 컨센서스 비교 포함 |
| — | 컨센서스 자동 배치 (주 1회 전종목 업데이트) |
