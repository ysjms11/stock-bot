# Stock-Bot MCP 도구 가이드
> 업데이트: 2026-04-11 | 총 33개 도구

---

## 📊 일일 점검 (1분 컷)

| 도구 | 명령 | 용도 |
|---|---|---|
| **get_regime** | `get_regime` | 🔴🟡🟢 레짐 판정 (복합점수 0~100) |
| **get_alerts** | `get_alerts(brief=true)` | 손절/목표 근접 + 매수감시 + 최근 decision |

→ 변화 없으면 끝. KOSPI ±3% or 보유 ±5% 시 풀 점검.

---

## 📈 주간 Sunday 30 (30분)

| 순서 | 도구 | 명령 | 용도 |
|---|---|---|---|
| 1 | get_regime | `get_regime` | 레짐 변화 확인 |
| 2 | get_alerts | `get_alerts` | triggered/손절 근접 |
| 3 | get_supply | `get_supply(mode=combined_rank)` | 외인+기관 합산 순매수 TOP |
| 4 | get_macro | `get_macro(mode=op_turnaround)` | 적자→흑자 전환 종목 |
| 5 | get_macro | `get_macro(mode=op_growth)` | 영업이익 급증 종목 |
| 6 | 웹서치 | 매크로 thesis 스캔 | 산업 트렌드 확인 |
| 7 | 딥체크 | 아래 7단계 참조 | 관심 종목 1~2개 |
| 8 | set_alert | `set_alert(log_type=decision)` | 판단 기록 |

---

## 🔍 종목 분석

### 빠른 조회
| 도구 | 명령 | 용도 |
|---|---|---|
| **get_stock_detail** | `get_stock_detail(ticker="005930")` | 현재가/PER/PBR/수급 |
| | `get_stock_detail(tickers="005930,000660")` | 다종목 일괄 (최대 20개) |
| | `get_stock_detail(ticker="NVDA")` | 미국 종목도 자동 판별 |
| | `get_stock_detail(ticker="005930", period="D60")` | 일봉 60일 |

### 상세 분석
| 도구 | 명령 | 용도 |
|---|---|---|
| get_stock_detail | `mode=volume_profile` | 매물대 분석 (POC/VA) |
| | `mode=orderbook` | 매수·매도 10호가 + 잔량 |
| | `mode=after_hours` | 시간외 현재가 |
| get_consensus | `get_consensus(ticker="005930")` | 증권사 목표가/투자의견 |
| get_news | `get_news(ticker="005930")` | 종목 뉴스 헤드라인 + 감성분석 |
| get_backtest | `get_backtest(ticker="005930")` | 백테스트 (5가지 전략) |

### 한국 종목 7단계 딥서치
```
1. get_stock_detail(ticker) — 현재가/PER/PBR/수급
2. get_consensus(ticker) — 컨센서스
3. manage_report(action=collect, ticker) → manage_report(action=list, ticker) — 리포트 수집+열람
4. get_supply(mode=history, ticker) — N일 수급 추세
5. get_market_signal(mode=short_sale, ticker) — 공매도 추이
6. get_stock_detail(mode=volume_profile, ticker) — 매물대
7. get_dart(mode=report, ticker) → get_dart(mode=read, ticker) — DART 사업보고서
```

---

## 🔎 전종목 스캔 (종목 발굴)

### get_scan — 스냅샷 필터링 (krx_db 기반)
| 프리셋 | 설명 | 사용 예 |
|---|---|---|
| `value` | PER<10 + PBR<1 + 시총>1000억 | 저평가 가치주 |
| `momentum` | 등락률>3% + 회전율>1% | 모멘텀 종목 |
| `oversold` | 등락률 -7% 이하 | 낙폭과대 |
| `relative_strength` | 시장평균+3% + 외인기관 매수 | 하락장 버틴 종목 |
| `small_cap_buy` | 시총 500~5000억 + 외인매수 | 소형주 외인 유입 |
| `foreign_streak` | 5일 연속 외인 순매수 | 외인 지속 매수 |
| 커스텀 | `per_max=15, market_cap_min=5000, fi_ratio_min=0.1` | 자유 조합 |

### get_change_scan — 변화 감지 (D-N 비교, 🆕)
| 프리셋 | 설명 | 핵심 조건 |
|---|---|---|
| `ma_convergence` | 이평선 수렴 (곧 터짐) | MA spread <3%, 30일간 수렴 |
| `volume_spike` | 거래량 폭발 | 10일 평균 대비 2배+ |
| `earnings_disconnect` | 실적 좋은데 주가 안 오름 | EPS +30%인데 주가 보합 |
| `consensus_undervalued` | 컨센서스 대비 저평가 | 목표가 괴리 40%+ |
| `oversold_bounce` | 과매도 반등 시작 | RSI <30에서 상승 전환 |
| `vp_support` | 매물대 지지 구간 | VP position <0.2 |
| `golden_cross` | 골든크로스 발생 | MA5>MA20 전환 |
| `sector_leader` | 섹터 내 최강 | 업종 대비 +5% 초과 |
| `w52_breakout` | 52주 신고가 근접 | 52주 위치 >95% |
| `short_squeeze` | 숏커버 진행 | 공매도 10일 -30% |
| `credit_unwind` | 신용 정리 중 | 신용잔고 5일 연속 감소 |
| `foreign_reversal` | 외인 매수 전환 | 5일 매도→매수 |
| `foreign_accumulation` | 외인 보유비율 급증 | 5일 +1%p |
| 파라미터화 | 모든 임계값 변경 가능 | `spread_max=5, ratio_min=3.0` |

---

## 📋 수급 분석

| 도구 | 명령 | 용도 |
|---|---|---|
| **get_supply** | `mode=daily, ticker` | 당일 확정 수급 (외인/기관/개인) |
| | `mode=history, ticker, days=10` | N일 수급 추세 |
| | `mode=estimate, ticker` | 장중 추정 수급 |
| | `mode=foreign_rank` | 외인 순매수 TOP |
| | `mode=combined_rank` | 외인+기관 합산 TOP |
| | `mode=broker_rank` | 증권사별 매매 종목 TOP |
| **get_broker** | `get_broker(ticker)` | 종목별 거래원(증권사) 매수/매도 5곳 |

---

## 🏭 섹터/매크로

| 도구 | 명령 | 용도 |
|---|---|---|
| **get_macro** | (생략) | KOSPI/KOSDAQ/환율 기본 |
| | `mode=dashboard` | VIX/WTI/금/구리/DXY/US10Y/이벤트 |
| | `mode=sector_etf` | 섹터 ETF 시세 |
| | `mode=us_sector` | 미국 섹터 ETF (SPY/QQQ/XLK) |
| | `mode=convergence` | 이평선 수렴 스크리너 |
| | `mode=op_growth` | KIS 영업이익 증가율 스크리너 |
| | `mode=op_turnaround` | 적자→흑자 전환 |
| | `mode=dart_op_growth` | DART 기반 연간 OP 성장률 |
| | `mode=dart_turnaround` | DART 기반 적자→흑자 |
| **get_sector** | `mode=flow` | 업종별 외인+기관 순매수 (92개 실용 섹터: 반도체/조선/전력기기 등) |
| | `mode=rotation` | 섹터 로테이션 감지 |
| **get_finance_rank** | | 전종목 재무비율 순위 (PER/PBR/ROE 등) |
| **get_highlow** | | 52주 신고가/신저가 근접 종목 |

---

## 📊 시장 시그널

| 도구 | 명령 | 용도 |
|---|---|---|
| **get_market_signal** | `mode=short_sale, ticker` | 공매도 일별 추이 |
| | `mode=credit, ticker` | 신용잔고 일별 추이 |
| | `mode=lending, ticker` | 대차거래 일별 추이 |
| | `mode=vi` | VI 발동 종목 현황 |
| | `mode=program_trade` | 프로그램매매 투자자별 |

---

## 💼 포트폴리오 관리

| 도구 | 명령 | 용도 |
|---|---|---|
| **get_portfolio** | `get_portfolio` | 현재 보유 + 손익 |
| | `mode=set, market=KR, holdings={...}` | 포트폴리오 설정 |
| **get_portfolio_history** | `get_portfolio_history(days=30)` | 일별 스냅샷 + 드로다운 |
| **get_trade_stats** | `get_trade_stats` | 매매 성과 분석 (승률/손익/보유기간) |
| **simulate_trade** | `simulate_trade(buys=[...], sells=[...])` | 가상 매매 시뮬레이션 |

---

## ⚙️ 알림/기록 관리

| 도구 | 명령 | 용도 |
|---|---|---|
| **set_alert** | `ticker, stop_price, target_price` | 손절/목표가 등록 |
| | `ticker, buy_price, watch_grade` | 매수 감시가 등록 |
| | `log_type=decision` | 투자 판단 기록 |
| | `log_type=trade` | 매매 기록 (프로세스 점수) |
| | `log_type=compare` | 종목 비교 기록 |
| | `log_type=delete, ticker` | 알림 완전 삭제 |
| **manage_watch** | `action=add/remove, ticker` | 워치리스트 추가/제거 |
| **manage_report** | `action=collect/list, ticker` | 브로커 리포트 수집/열람 |

---

## 🔧 공시/파일 관리

| 도구 | 명령 | 용도 |
|---|---|---|
| **get_dart** | (생략) | 워치리스트 최근 3일 공시 |
| | `mode=report, ticker` | 사업보고서 txt 저장 |
| | `mode=read, ticker` | 저장된 사업보고서 읽기 |
| **backup_data** | | Gist 백업/복원 |
| **read_file** | `path="CLAUDE.md"` | 봇 디렉토리 파일 읽기 |
| **write_file** | `path="TODO.md", content="..."` | 파일 쓰기 (.md/.json/.txt) |
| **list_files** | `path="data"` | 디렉토리 목록 조회 |

---

## 🗄️ 데이터 수집 (KRX DB v2)

매일 15:55 KST 자동 수집 → data/krx_db/YYYYMMDD.json

| 카테고리 | 필드 | 소스 |
|---|---|---|
| 시세 | 종가/시고저/등락률/거래량/시총 | KRX OPEN API |
| 종목정보 | 업종/상장주식수 | KRX OPEN API |
| 밸류 | PER/PBR/EPS/BPS/배당수익률 | KRX 크롤링 (Safari) |
| 수급 | 외인/기관/개인 순매수 | KRX 크롤링 |
| 공매도 | 잔고/비중 | KRX 크롤링 |
| 외인보유 | 보유비율/한도소진율 | KRX 크롤링 |
| 신용/대차 | 신용잔고/대차잔고 | KRX 크롤링 |
| 컨센서스 | 목표가/괴리율 | FnGuide |
| 이평선 | MA5/10/20/60/120/200 | 계산 |
| 기술지표 | RSI(14)/볼린저/MA spread | 계산 |
| 추세 점수 | 수급추세/공매도변화/거래량비율 (5d/20d/60d) | 계산 |
| 매물대 | POC/VA 상하단/위치 (60d/250d) | 계산 |
| 실적괴리 | EPS변화/YTD수익률/earnings_gap | 계산 |
| 섹터강도 | 업종 내 순위/상대강도 | 계산 |
| 52주 위치 | 고가/저가/position | 계산 |

보관: 무제한 | 일별 ~4.6MB | 연간 ~1.1GB

---

## 🤖 텔레그램 자동 알림

| 시간 | 알림 |
|---|---|
| 07:00 | 실적 캘린더 + 배당 캘린더 + 리포트 수집 (평일) |
| 07:10 | 미국 실적 캘린더 (평일) |
| 15:40 | 한국 장마감 요약 (포트변동/섹터ETF/감시접근) |
| 15:50 | 포트폴리오 스냅샷 + 드로다운 체크 (평일) |
| 16:30 | 모멘텀 경고 (5가지 조건 중 2개+) |
| 19:00 | 워치 변화 감지 (평일) |
| 06:00 | 미국 장마감 요약 (S&P/나스닥/보유종목/손절경고) |
| 18:00 | 매크로 대시보드 (매일) |
| 22:00 | Gist 자동 백업 (매일) |
| 일요일 07:00 | 유니버스 + 컨센서스 갱신 |
| 일요일 19:00 | Sunday 30 리마인더 |
| 수시 | 손절가/목표가/매수감시 도달 알림 |
| 수시 | DART 공시 알림 ([긴급]/[주의]/[참고]) |
| 수시 | 수급 이탈 경고 (외인 3일 연속 순매도) |
| 수시 | 주간 손실 한도 경고 (-3%/-4%) |

---

## 🗂️ Git 도구

| 도구 | 명령 | 용도 |
|---|---|---|
| **git_status** | `git_status` | 브랜치/staged/modified/untracked 조회 |
| **git_diff** | `git_diff(path="kis_api.py")` | 변경 내용 확인 (staged 옵션 지원) |
| **git_log** | `git_log(n=10)` | 최근 커밋 히스토리 (path 옵션 지원) |
| **git_commit** | `git_commit(files=["main.py"], message="fix: ...")` | 파일 지정 커밋 (.py/.env 차단) |
| **git_push** | `git_push` | origin/main push |
