# MCP 도구 전체 목록 (43개)

> `mcp_tools.py`의 도구 스키마 요약. 새 도구 추가 시 → `.claude/rules/add-mcp-tool.md` 참조

| # | 이름 | mode/type | 설명 |
|---|------|-----------|------|
| 1 | `get_rank` | type=price | 한국 등락률 상위/하위 (rise/fall, kospi/kosdaq) |
| | | type=us_price | 미국 등락률 상위/하위 (NAS/NYS/AMS) |
| | | type=volume | 체결강도 상위 (120%이상=매수우위) |
| | | type=scan | 거래량 상위 종목 |
| | | type=after_hours | 시간외 등락률 순위 (장 마감 후 급등/급락) |
| | | type=dividend | 배당수익률 순위 (배당금·배당률·PER) |
| 2 | `get_portfolio` | | 포트폴리오 조회/수정 (한국+미국 손익, cash_krw/cash_usd) |
| 3 | `get_stock_detail` | (기본) | 현재가·PER·PBR·수급, 한국/미국 자동 판별, period로 일봉 |
| | | mode=volume_profile | 볼륨 프로파일(매물대) 분석 (Y1/Y2/Y3) |
| | | mode=after_hours | 시간외 현재가·등락률·거래량 |
| | | mode=orderbook | 매수·매도 10호가 + 잔량 + 비율 |
| 4 | `get_supply` | mode=daily | 당일확정수급 (외인/기관/개인) |
| | | mode=history | N일 수급추세 (연속매수/매도) |
| | | mode=estimate | 장중추정수급 (가집계) |
| | | mode=foreign_rank | 외국인 순매수 상위 |
| | | mode=combined_rank | 외인+기관 합산 순매수 상위 |
| | | mode=broker_rank | 증권사별 매매종목 상위 (매수/매도) |
| 5 | `get_dart` | | DART 공시 (워치 3일, report/report_list/read/insider 모드). insider: 임원·주요주주 N일 매수/매도 집계 + cluster_flag(3명+매수 AND 순매수>0) |
| 6 | `get_macro` | | 매크로 지표 (dashboard/sector_etf/convergence/op_growth 등) |
| 7 | `get_sector` | | 업종별 외인+기관 순매수, 업종 로테이션 분석 |
| 8 | `manage_watch` | | 워치리스트 조회/추가/제거 (한국+미국, 매수감시 포함) |
| 9 | `get_alerts` | | 손절가/목표가 목록 + 현재가 대비 % + 매수감시 |
| 10 | `get_market_signal` | mode=short_sale | 공매도 일별추이 |
| | | mode=vi | VI 발동 종목 현황 |
| | | mode=program_trade | 프로그램매매 투자자별 동향 |
| | | mode=credit | 신용잔고 일별추이 (10% 과열 경고) |
| | | mode=lending | 대차거래 일별추이 |
| 11 | `get_news` | | 종목 뉴스 헤드라인 (한국/미국, sentiment 감성분석) |
| 12 | `get_consensus` | | 증권사 컨센서스 목표주가/투자의견 (FnGuide) |
| 13 | `set_alert` | | 손절가/목표가, 매수감시, 투자판단, 종목비교, 매매기록 |
| 14 | `get_portfolio_history` | | 포트폴리오 스냅샷 히스토리 + 드로다운 + 투자규칙 경고 |
| 15 | `get_trade_stats` | | 매매 기록 성과 분석 (승률·손익·평균보유기간) |
| 16 | `backup_data` | | /data/*.json GitHub Gist 백업·복원·상태 조회 |
| 17 | `simulate_trade` | | 가상 매매 시뮬레이션 |
| 18 | `get_backtest` | | 백테스트 (ma_cross/momentum_exit/supply_follow/bollinger/hybrid) |
| 19 | `manage_report` | action=list/collect/tickers | 리포트 관리. `category=company/industry/market/strategy/economy/bond` 필터 (4/26 신규). collect 시 비종목 카테고리 자동 수집. |
| 20 | `get_regime` | | 시장 국면 판단 (매크로 기반) |
| 21 | `get_scan` | | KRX 전종목 스크리너 (시총/PER/PBR/수급/회전율, 6개 프리셋) |
| 22 | `get_finance_rank` | (기본) | 전종목 재무비율 순위 (PER/PBR/ROE/영업이익률/부채비율/매출성장률) |
| | | rank_type=fscore | F-Score >=7 우량 순위 (daily_snapshot, F/M/FCF Phase4) |
| | | rank_type=mscore_safe | M-Score <=-2.22 안전 순위 (오름차순, F/M/FCF Phase4) |
| | | rank_type=fcf_yield | FCF/EV 내림차순 순위 (F/M/FCF Phase4) |
| 23 | `get_highlow` | | 52주 신고가/신저가 근접 종목 순위 (괴리율 필터) |
| 24 | `get_broker` | | 종목별 거래원(증권사) 매수/매도 상위 5곳 |
| 25 | `read_file` | | stock-bot 디렉토리 내 파일 읽기 (.md/.py/.json/.txt, 100KB, ../ 차단) |
| 26 | `write_file` | | stock-bot 디렉토리 내 파일 쓰기 (.md/.json/.txt, .py/.env 불가, 200KB, ../ 차단) |
| 27 | `list_files` | | stock-bot 디렉토리 내 파일/폴더 목록 (이름·크기·수정일, depth 2, ../ 차단) |
| 28 | `read_report_pdf` | | 리포트 PDF 페이지 이미지 렌더링 (report_crawler DB 기반) |
| 29 | `get_change_scan` | preset= | 변화 감지 스캔 (ma_convergence/volume_spike/earnings_disconnect/consensus_undervalued/oversold_bounce/vp_support/golden_cross/sector_leader/w52_breakout, 복합 콤마 구분) |
| 30 | `git_status` | | Git 브랜치/변경파일 조회 |
| 31 | `git_diff` | | 변경내용 조회 (path, staged 옵션) |
| 32 | `git_log` | | 최근 커밋 로그 |
| 33 | `git_commit` | | 파일 지정 커밋 (.py/.env 차단) |
| 34 | `git_push` | | origin/main push |
| 35 | `get_alpha_metrics` | | 종목별 F-Score/M-Score/FCF 메트릭 조회 (daily_snapshot 최신 기준, F/M/FCF Phase4) |
| 36 | `get_us_ratings` | mode=events | 미국 종목 애널 레이팅 이벤트 조회 (days 기간, min_stars 별점 하한) |
| | | mode=trend | 월별 레이팅 추세 (months 기간) |
| | | mode=consensus | 현재 컨센서스 요약 |
| 37 | `get_us_scan` | mode=watchlist | 감시+보유 종목 최근 레이팅 스캔 |
| | | mode=discovery | 감시 밖 관심 종목 발굴 (min_upgrades 상향 임계값) |
| | | mode=sector | 섹터별 레이팅 모멘텀 |
| 38 | `get_us_analyst` | | 미국 애널 개인/그룹 조회 (name=개별, 없으면 top 리스트, firm/sector 필터) |
| 39 | `get_youtube_transcript` | | 유튜브 자막 추출 (URL/ID, ko 우선 en fallback, 자막 없으면 에러) |
| 40 | `get_us_buy_candidates` | | 톱 애널 추천 + TP 업사이드 충족 미국 매수 후보 (raw 데이터, watched=1 풀 254명, Tier S/A 카운트 포함, 기본 180일/1명+/+20%/limit 50) |
| 41 | `get_us_earnings_transcript` | | FMP 실적 컨퍼런스콜 본문 (CEO/CFO 발언 + 톱애널 Q&A, 분기당 5만자, max_chars 절삭 옵션) |
| 42 | `get_us_analyst_research` | | FMP 분석가 통합: Price Target Summary + Estimates(향후 5년 매출/EBITDA/순이익) + Grades(등급 변경 이력) |
| 43 | `get_polymarket` | | Polymarket prediction market — 매크로/지정학/정치/Fed/이란/관세/대선 베팅 컨센서스. 24h 거래량 정렬, sports/esports/pop culture 자동 컷, $500K 미만 노이즈 제외. (4/27 신규) |
| 44 | `get_macro_external` | | 외부 매크로 시그널 통합 — Polymarket Fed decision + Treasury 수익률 곡선 (Estrella-Mishkin 1998 침체 선행지표). 매크로 대시보드/SAT-SUN/D-1 자동 호출. (4/27 신규) |
