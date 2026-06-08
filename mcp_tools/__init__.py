# mcp_tools/__init__.py — 패키지 진입점
# MCP_TOOLS 스키마 배열 + _execute_tool + server re-export

from .server import (
    mcp_sse_handler,
    mcp_messages_handler,
    mcp_streamable_post_handler,
    mcp_streamable_delete_handler,
    mcp_streamable_options_handler,
    _handle_jsonrpc,
)
from ._execute import _execute_tool
from ._registry import TOOL_HANDLERS, execute_tool

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP_TOOLS 스키마 배열 (기존 순서/내용 유지)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
MCP_TOOLS = [
    # 1. get_rank ← scan_market + get_price_rank + get_us_price_rank + get_volume_power
    {"name": "get_rank",
     "description": "순위 조회 통합. type별: price=등락률, us_price=미국등락률, volume=체결강도, scan=거래량, after_hours=시간외등락률, dividend=배당수익률",
     "inputSchema": {"type": "object",
                     "properties": {
                         "type": {"type": "string", "enum": ["price", "us_price", "volume", "scan", "after_hours", "dividend"], "description": "순위 조회 유형"},
                         "sort": {"type": "string", "description": "price/us_price/after_hours용 (rise/fall, 기본 rise)"},
                         "market": {"type": "string", "description": "price용 (all/kospi/kosdaq), dividend용 (0=전체/1=코스피/3=코스닥)"},
                         "exchange": {"type": "string", "description": "us_price용 (NAS/NYS/AMS, 기본 NAS)"},
                         "n": {"type": "integer", "description": "결과 수 (기본 20)"},
                     },
                     "required": ["type"]}},
    # 2. get_portfolio (유지)
    {"name": "get_portfolio",
     "description": "포트폴리오 조회 또는 수정. mode 생략 시 현재가·손익 조회. mode='set' 시 포트폴리오 저장. cash_krw/cash_usd로 현금 잔고 업데이트 가능.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode":     {"type": "string", "description": "'set' 이면 저장 모드. 생략 시 조회."},
                         "market":   {"type": "string", "description": "[set] 'KR' 또는 'US'"},
                         "holdings": {"type": "object", "description": "[set] KR: {종목코드: {name, qty, avg_price}}, US: {심볼: {name, qty, avg_price}}"},
                         "cash_krw": {"type": "number", "description": "[set] 원화 현금 잔고 (원)"},
                         "cash_usd": {"type": "number", "description": "[set] 달러 현금 잔고 (USD)"},
                     },
                     "required": []}},
    # 3. get_stock_detail (확장 ← + get_batch_detail)
    {"name": "get_stock_detail",
     "description": "개별 종목 상세: 현재가·PER·PBR·수급 또는 일봉 조회. 한국/미국 자동 판별. period 지정 시 일봉. mode별: volume_profile=매물대분석, after_hours=시간외현재가, orderbook=호가잔량",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드(예: 005930) 또는 미국 티커(예: TSLA, AAPL)"},
                         "mode": {"type": "string", "description": "volume_profile/after_hours/orderbook", "enum": ["volume_profile", "after_hours", "orderbook"]},
                         "period": {"type": "string", "description": "일봉 조회: D60/D30/W20 등. volume_profile 시: Y1=1년, Y2=2년, Y3=3년 (기본 Y1)"},
                         "bins": {"type": "integer", "description": "볼륨 프로파일 가격 구간 수 (기본 20, 최대 50)"},
                         "tickers": {"type": "string", "description": "콤마 구분 종목코드로 다종목 일괄 조회 (예: '005930,000660' 또는 'AAPL,TSLA'). 최대 20종목."},
                         "delay": {"type": "number", "description": "일괄조회 시 종목간 딜레이 (기본 0.3초)"},
                     },
                     "required": []}},
    # 4. get_supply ← get_investor_flow + get_investor_trend_history + get_investor_estimate + get_foreign_rank + get_foreign_institution
    {"name": "get_supply",
     "description": "수급 분석 통합. mode별: daily=당일확정수급, history=N일수급추세, estimate=장중추정수급, foreign_rank=외국인순매수상위, combined_rank=외인+기관합산, broker_rank=증권사별매매종목상위",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode": {"type": "string", "enum": ["daily", "history", "estimate", "foreign_rank", "combined_rank", "broker_rank"], "description": "수급 조회 모드"},
                         "ticker": {"type": "string", "description": "종목코드 (daily/history/estimate 시 필수)"},
                         "days": {"type": "integer", "description": "history 시 조회 일수 (기본 5, 최대 30)"},
                         "sort": {"type": "string", "description": "combined_rank/broker_rank 시 정렬 (buy/sell, 기본 buy)"},
                         "broker": {"type": "string", "description": "broker_rank 시 증권사코드 (생략 시 전체)"},
                         "n": {"type": "integer", "description": "foreign_rank/combined_rank/broker_rank 결과 수"},
                     },
                     "required": ["mode"]}},
    # 5. get_dart (유지 + report/report_list 모드 추가)
    {"name": "get_dart",
     "description": "DART 공시 조회. mode 생략: 워치리스트 최근 3일 공시. mode='report': 보유+워치 종목 사업보고서 본문을 txt 저장 (ticker 지정 가능). mode='report_list': 저장된 txt 파일 목록. mode='read': 저장된 사업보고서 txt 내용 반환 (ticker 필수). mode='disclosure_list': 종목별 최근 N일 수시공시 목록 (ticker 필수, days 기본 7). mode='disclosure_read': 특정 rcept_no 공시 본문 다운로드·캐시 (ticker+rcept_no 필수). mode='insider': 종목별 임원·주요주주 내부자 거래 집계 (ticker 필수, days 지정 가능).",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode":     {"type": "string", "description": "'report'=사업보고서 저장, 'report_list'=저장 파일 목록, 'read'=저장된 보고서 읽기(ticker 필수), 'disclosure_list'=수시공시 목록(ticker 필수), 'disclosure_read'=공시본문 다운로드(ticker+rcept_no 필수), 'insider'=내부자거래집계(ticker 필수), 생략=기존 공시"},
                         "ticker":   {"type": "string", "description": "[report/read/disclosure_list/disclosure_read/insider] 종목코드"},
                         "rcept_no": {"type": "string", "description": "[disclosure_read] DART 접수번호 (필수)"},
                         "days":     {"type": "integer", "description": "[insider] 집계 기간 (기본 30일), [disclosure_list] 조회 기간 (기본 7일)"},
                     },
                     "required": []}},
    # 6. get_macro (유지)
    {"name": "get_macro",
     "description": "매크로 지표 조회. mode 생략 시 KOSPI·KOSDAQ·환율. mode='dashboard': VIX·WTI·금·구리·DXY·US10Y 등 전체. mode='sector_etf': 섹터 ETF 시세. mode='us_sector': 미국 섹터 ETF 등락률 (SPY/QQQ/XLK 등). mode='convergence': 이평선 수렴 스크리너 (disp_20/disp_60 이격도 포함, market/sort 지원). mode='convergence2': 코스닥 위주 하위호환. mode='op_growth': KIS 영업이익 증가율 스크리너. mode='op_turnaround': KIS 적자→흑자 전환. mode='dart_op_growth': DART 기반 연간 영업이익 성장률 스크리너. mode='dart_turnaround': DART 기반 적자→흑자 전환.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode":       {"type": "string", "description": "'dashboard'|'sector_etf'|'us_sector'|'convergence'|'convergence2'|'op_growth'|'op_turnaround'|'dart_op_growth'|'dart_turnaround'|생략"},
                         "spread":     {"type": "number", "description": "[convergence] 이평 수렴 기준 % (기본 5.0)"},
                         "market":     {"type": "string", "description": "[convergence] 'all'=코스피+코스닥(기본), 'kospi'=코스피위주, 'kosdaq'=코스닥위주"},
                         "sort":       {"type": "string", "description": "[convergence] 'spread'=수렴도순(기본), 'disp_20'=20일이격도순, 'disp_60'=60일이격도순. [op_growth/op_turnaround/dart_op_growth/dart_turnaround] 'yoy'=연간증가율순(기본), 'qoq'=분기증가율순, 'trend'=분기추세순(연속증가>흑자전환>감소>적자전환>적자지속)"},
                         "min_growth": {"type": "number", "description": "[op_growth/dart_op_growth] 영업이익 최소 증가율 % (기본 50)"},
                     },
                     "required": []}},
    # 7. get_sector ← get_sector_flow + get_sector_rotation
    {"name": "get_sector",
     "description": "섹터 분석 통합. mode별: flow=WI26업종별외인+기관순매수(기본), rotation=섹터로테이션감지(전일대비자금이동)",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode": {"type": "string", "description": "'flow'(기본) 또는 'rotation'"},
                     },
                     "required": []}},
    # 8. manage_watch ← add_watch + remove_watch
    {"name": "manage_watch",
     "description": "워치리스트 관리. action별: add=종목추가(변동이력자동기록), remove=종목제거(변동이력자동기록)",
     "inputSchema": {"type": "object",
                     "properties": {
                         "action": {"type": "string", "enum": ["add", "remove"], "description": "추가 또는 제거"},
                         "ticker": {"type": "string", "description": "종목코드 (예: 005930) 또는 미국 티커"},
                         "name": {"type": "string", "description": "종목명 (add 시 필수)"},
                         "alert_type": {"type": "string", "description": "remove 시 삭제 대상: 'watchlist'(기본) 또는 'buy_alert'"},
                     },
                     "required": ["action", "ticker"]}},
    # 9. get_alerts (유지)
    {"name": "get_alerts",     "description": "손절가 목록 + 현재가 대비 손절까지 남은 % + 매수감시 목록. brief=true 시 핵심 필드만 반환.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "boolean", "description": "true 시 핵심 필드만 (memo/changelog/compares 제거)"},
     }, "required": []}},
    # 10. get_market_signal ← get_short_sale + get_vi_status + get_program_trade
    {"name": "get_market_signal",
     "description": "시장 시그널 통합. mode별: short_sale=공매도일별추이, vi=VI발동종목현황, program_trade=프로그램매매투자자별동향, credit=신용잔고일별추이, lending=대차거래일별추이",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode": {"type": "string", "enum": ["short_sale", "vi", "program_trade", "credit", "lending"], "description": "시그널 조회 모드"},
                         "ticker": {"type": "string", "description": "종목코드 (short_sale/credit/lending 시 필수)"},
                         "days": {"type": "integer", "description": "short_sale/credit/lending 조회 일수 (기본 20, 최대 60)"},
                         "market": {"type": "string", "description": "program_trade 시 시장 (kospi/kosdaq, 기본 kospi)"},
                     },
                     "required": ["mode"]}},
    # 11. get_news (확장 ← + get_news_sentiment)
    {"name": "get_news",
     "description": "종목 뉴스 헤드라인. 한국(KIS)/미국(yfinance) 자동 판별. sentiment=true 시 헤드라인 감성분석(긍정/부정/중립) 포함",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드 (감성분석 전체조회 시 생략 가능)"},
                         "n": {"type": "integer", "description": "뉴스 개수 (기본 10)"},
                         "sentiment": {"type": "boolean", "description": "true 시 감성분석 포함 (기본 false)"},
                     },
                     "required": []}},
    # 12. get_consensus (유지)
    {"name": "get_consensus",  "description": "종목별 증권사 컨센서스 목표주가/투자의견 조회 (FnGuide 기반). brief=true 시 reports/broker_targets 최근 5건만.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "한국 종목코드 6자리 (예: 009540)"},
                         "brief": {"type": "boolean", "description": "true 시 reports/broker_targets 최근 5건만"},
                     },
                     "required": ["ticker"]}},
    # 13. set_alert (확장 ← + delete_alert)
    {"name": "set_alert",      "description": "손절가/목표가 등록, 매수감시, 투자판단 기록, 매매기록, 알림삭제. log_type으로 모드 선택: 생략→stop/buy, decision→투자판단, compare→종목비교, trade→매매기록, delete→매도 후 알림 완전 삭제 (ticker, market 필요)",
     "inputSchema": {"type": "object",
                     "properties": {
                         "log_type":          {"type": "string", "description": "모드: 생략=stop/buy, 'decision'=투자판단, 'compare'=종목비교, 'trade'=매매기록, 'delete'=알림삭제"},
                         "ticker":            {"type": "string", "description": "종목코드 또는 미국 티커"},
                         "name":              {"type": "string", "description": "종목명"},
                         "stop_price":        {"type": "number", "description": "손절가"},
                         "target_price":      {"type": "number", "description": "목표가 [trade:매수 시 목표가]"},
                         "buy_price":         {"type": "number", "description": "매수 희망가 (이 가격 이하 시 텔레그램 알림)"},
                         "memo":              {"type": "string", "description": "메모"},
                         "date":              {"type": "string", "description": "[decision/trade] YYYY-MM-DD (생략시 오늘)"},
                         "regime":            {"type": "string", "description": "[decision] 시장 국면 (예: 경계, 공격, 방어)"},
                         "grades":            {"type": "object", "description": "[decision] 종목별 확신등급. 값은 문자열(\"A\") 또는 객체({\"grade\":\"B\",\"change\":\"A→B\",\"reason\":\"사유\"})"},
                         "actions":           {"type": "array",  "description": "[decision] 액션 목록 (예: [\"HD조선 6주 매도\"])"},
                         "watchlist":         {"type": "array",  "description": "[decision] 관심 종목 목록 (예: [\"한화에어로 130만원대\"])"},
                         "notes":             {"type": "string", "description": "[decision] 메모 (예: 이란전쟁 리스크)"},
                         "held_ticker":       {"type": "string", "description": "[compare] 보유 종목코드"},
                         "candidate_ticker":  {"type": "string", "description": "[compare] 교체 후보 종목코드"},
                         "held_score":        {"type": "number", "description": "[compare] 보유 종목 점수"},
                         "candidate_score":   {"type": "number", "description": "[compare] 후보 종목 점수"},
                         "reasoning":         {"type": "string", "description": "[compare] 비교 근거"},
                         "side":              {"type": "string", "description": "[trade] 'buy' 또는 'sell'"},
                         "qty":               {"type": "integer","description": "[trade] 매매 수량"},
                         "price":             {"type": "number", "description": "[trade] 매매 단가"},
                         "grade":             {"type": "string", "description": "[trade] 매매 시점 확신등급 (A/B/C/D)"},
                         "reason":            {"type": "string", "description": "[trade] 매매 사유"},
                         "market":            {"type": "string", "description": "[delete] 'KR'=한국(기본), 'US'=미국. [buy_price 등록 시 자동 감지]"},
                         "watch_grade":       {"type": "string", "description": "[buy_price] 매수감시 확신등급 (A/B+/B/B-/C+/C/D). 생략 가능."},
                     },
                     "required": []}},
    # 14. get_portfolio_history (유지)
    {"name": "get_portfolio_history",
     "description": "포트폴리오 스냅샷 히스토리 + 드로다운 분석. brief=true 시 스냅샷 핵심 필드만 (holdings 제거).",
     "inputSchema": {"type": "object",
                     "properties": {
                         "days": {"type": "integer", "description": "최근 N일 스냅샷 반환 (기본 30, 최대 365)"},
                         "brief": {"type": "boolean", "description": "true 시 스냅샷에서 holdings 제거, 핵심 집계만"},
                     },
                     "required": []}},
    # 15. get_trade_stats (유지)
    {"name": "get_trade_stats",
     "description": "매매 기록 성과 분석. 승률·손익·평균보유기간·확신등급 정확도 등 반환. 월간 복기 시 사용.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "period": {"type": "string", "description": "'month'=이번달(기본), 'quarter'=이번분기, 'year'=올해, 'all'=전체"},
                     },
                     "required": []}},
    # 16. backup_data (유지)
    {"name": "backup_data",
     "description": "/data/*.json 파일 GitHub Gist 백업·복원·상태 조회. action='backup': Gist에 백업, 'restore': Gist에서 복원(기존 파일 보존), 'restore_force': 강제 덮어쓰기 복원, 'status': 최근 백업 정보 조회.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "action": {"type": "string", "description": "'backup' | 'restore' | 'restore_force' | 'status'"},
                     },
                     "required": ["action"]}},
    # 17. simulate_trade (유지)
    {"name": "simulate_trade",
     "description": "포트폴리오 매매 시뮬레이션. 매도/매수 후 비중·섹터·현금·RR비율 변화를 미리보기.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "sells": {"type": "array", "description": "매도 목록 [{ticker, qty, price(선택)}]",
                                   "items": {"type": "object", "properties": {
                                       "ticker": {"type": "string"}, "qty": {"type": "integer"},
                                       "price": {"type": "number", "description": "매도가 (생략 시 현재가)"}}}},
                         "buys": {"type": "array", "description": "매수 목록 [{ticker, qty, price(선택)}]",
                                  "items": {"type": "object", "properties": {
                                      "ticker": {"type": "string"}, "qty": {"type": "integer"},
                                      "price": {"type": "number", "description": "매수가 (생략 시 현재가)"}}}},
                     },
                     "required": []}},
    # 18. get_backtest (유지)
    {"name": "get_backtest",
     "description": "종목 백테스트. 52주 일봉 데이터로 전략별 시뮬레이션. 수익률·승률·MDD·매매내역 반환. Buy&Hold 벤치마크 비교 포함.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker":   {"type": "string", "description": "종목코드(예: 005930) 또는 미국 티커(예: AAPL)"},
                         "period":   {"type": "string", "description": "일봉 기간. D250=52주(KIS API), D120=6개월, D60=3개월, Y1=1년/Y2=2년/Y3=3년(FDR/yfinance 사용)"},
                         "strategy": {"type": "string", "description": "전략: 'ma_cross'(이평교차, 기본), 'momentum_exit'(모멘텀종료), 'supply_follow'(수급추종, 10일제한), 'bollinger'(볼린저밴드), 'hybrid'(복합)"},
                     },
                     "required": ["ticker"]}},
    {"name": "manage_report",
     "description": "증권사 리포트 관리. category 필터로 종목/산업/시황/전략/경제 구분. action별: list=수집된 리포트 조회, collect=수동 수집 트리거(비종목 카테고리도 지원), tickers=수집 대상 종목 목록",
     "inputSchema": {"type": "object",
                     "properties": {
                         "action": {"type": "string", "enum": ["list", "collect", "tickers"], "description": "list=조회, collect=수집, tickers=대상종목"},
                         "days": {"type": "integer", "description": "list 시 최근 N일 (기본 7)"},
                         "ticker": {"type": "string", "description": "list/collect 시 특정 종목 필터 (종목 분석만)"},
                         "category": {"type": "string", "enum": ["company", "industry", "market", "strategy", "economy", "bond"], "description": "list/collect 시 카테고리 필터. company=종목분석(기본), industry=산업, market=시황, strategy=투자전략, economy=경제, bond=채권"},
                         "brief": {"type": "boolean", "description": "list 시 true면 제목+증권사만 (full_text 제외)"},
                     },
                     "required": ["action"]}},
    # 20. get_regime
    {"name": "get_regime",
     "description": "시장 레짐 — 통화별 현금 다이얼. KR=KOSPI 실현변동성 252일 퍼센타일+200MA거리, US=S&P 200MA+VIX 252일 퍼센타일. KR/US 독립, 🟢평상(현금5~8%)/🟡경계(8~15%비축)/🔴발사(풀투자). 디바운스 잡음방지.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode": {"type": "string", "enum": ["current", "history", "override"],
                                  "description": "current=오늘 레짐, history=최근 N일, override=수동 강제", "default": "current"},
                         "days": {"type": "integer", "description": "history 모드 조회 일수", "default": 5},
                         "regime": {"type": "string", "enum": ["crisis", "neutral", "offensive"],
                                    "description": "override 모드에서 강제할 레짐"},
                         "reason": {"type": "string", "description": "override 사유"},
                         "market": {"type": "string", "enum": ["kr", "us", "both"],
                                    "description": "override 적용 시장 (기본 both)", "default": "both"},
                     },
                     "required": []}},
    # 21. get_scan — KRX 전종목 스크리너
    {"name": "get_scan",
     "description": "KRX 전종목 스크리너. 시총/등락률/PER/PBR/외인수급비율/회전율 등으로 필터링. preset 지원: relative_strength(하락장 버틴), small_cap_buy(소형주 외인매수), value(저평가), momentum(모멘텀), oversold(낙폭과대), foreign_streak(5일연속 외인매수).",
     "inputSchema": {"type": "object",
                     "properties": {
                         "preset": {"type": "string", "enum": ["relative_strength", "small_cap_buy", "value", "momentum", "oversold", "foreign_streak"],
                                    "description": "프리셋 스크리너 (개별 파라미터로 오버라이드 가능)"},
                         "market_cap_min": {"type": "number", "description": "시총 최소 (억원, 기본 0)"},
                         "market_cap_max": {"type": "number", "description": "시총 최대 (억원, 기본 9999999)"},
                         "chg_pct_min": {"type": "number", "description": "등락률 최소 (%)"},
                         "chg_pct_max": {"type": "number", "description": "등락률 최대 (%)"},
                         "foreign_ratio_min": {"type": "number", "description": "외인 수급비율 최소"},
                         "fi_ratio_min": {"type": "number", "description": "외인+기관 합산 수급비율 최소"},
                         "per_min": {"type": "number", "description": "PER 최소 (기본 0)"},
                         "per_max": {"type": "number", "description": "PER 최대 (기본 9999)"},
                         "pbr_max": {"type": "number", "description": "PBR 최대 (기본 9999)"},
                         "turnover_min": {"type": "number", "description": "회전율 최소 (%)"},
                         "sort": {"type": "string", "description": "정렬: foreign_ratio/fi_ratio/chg_pct/turnover/market_cap (기본 fi_ratio)"},
                         "n": {"type": "integer", "description": "결과 수 (기본 30, 최대 100)"},
                         "date": {"type": "string", "description": "날짜 YYYYMMDD (생략 시 최신 DB)"},
                         "market": {"type": "string", "description": "kospi/kosdaq/all (기본 all)"},
                     },
                     "required": []}},
    # 22. get_finance_rank — 재무비율 순위 + 알파 메트릭 순위 (F/M/FCF Phase4)
    {"name": "get_finance_rank",
     "description": "전종목 재무비율 순위. rank_type 생략 시 KIS FHPST01750000 (PER/PBR/ROE/영업이익률/순이익률/부채비율/매출성장률). rank_type=fscore|mscore_safe|fcf_yield 시 daily_snapshot 알파 메트릭 순위.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "rank_type": {"type": "string", "enum": ["fscore", "mscore_safe", "fcf_yield"], "description": "알파 메트릭 순위 모드. fscore=F-Score 내림차순(>=7 필터), mscore_safe=M-Score 오름차순(<=-2.22 필터), fcf_yield=FCF/EV 내림차순"},
                         "market": {"type": "string", "description": "0000=전체(기본), 0001=거래소, 1001=코스닥, 2001=코스피200. rank_type 지정 시 kospi/kosdaq/all."},
                         "year": {"type": "string", "description": "회계연도 (기본: 전년도). rank_type 미지정 시 사용."},
                         "quarter": {"type": "string", "description": "0=1Q, 1=반기, 2=3Q, 3=결산(기본). rank_type 미지정 시 사용."},
                         "sort": {"type": "string", "description": "7=수익성(기본), 11=안정성, 15=성장성, 20=활동성. rank_type 미지정 시 사용."},
                         "n": {"type": "integer", "description": "결과 수 (기본 30, 최대 100)"},
                     },
                     "required": []}},
    # 23. get_highlow — 52주 신고가/신저가 근접
    {"name": "get_highlow",
     "description": "52주 신고가/신저가 근접 종목 순위. 괴리율 범위 필터. KIS FHPST01870000.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "mode": {"type": "string", "enum": ["high", "low"], "description": "high=신고가 근접(기본), low=신저가 근접"},
                         "market": {"type": "string", "description": "0000=전체(기본), 0001=거래소, 1001=코스닥"},
                         "gap_min": {"type": "integer", "description": "괴리율 최소 %(기본 0)"},
                         "gap_max": {"type": "integer", "description": "괴리율 최대 %(기본 10)"},
                         "n": {"type": "integer", "description": "결과 수 (기본 30)"},
                     },
                     "required": []}},
    # 24. get_broker — 거래원(증권사) 매매 정보
    {"name": "get_broker",
     "description": "종목별 거래원(증권사) 매수/매도 상위 5곳. 외국계 증권사 동향 파악.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드 (예: 005930)"},
                     },
                     "required": ["ticker"]}},

    {"name": "read_file",
     "description": "stock-bot 디렉토리 내 파일 읽기. 허용 확장자: .md/.py/.json/.txt, 최대 100KB. ../ 경로 차단. PDF 읽기는 read_report_pdf 사용.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "stock-bot 디렉토리 기준 상대경로 (예: CLAUDE.md, kis_api.py)"},
                         "lines": {"type": "integer", "description": "최대 N줄만 읽기 (생략 시 전체)"},
                         "offset": {"type": "integer", "description": "시작 줄 번호 0-indexed (기본값 0, lines와 함께 사용)"},
                     },
                     "required": ["path"]}},
    {"name": "write_file",
     "description": "stock-bot 디렉토리 내 파일 쓰기. 허용 확장자: .md/.json/.txt (.py/.env 불가), 최대 200KB. ../ 경로 차단.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "stock-bot 디렉토리 기준 상대경로 (예: TODO.md, data/events.json)"},
                         "content": {"type": "string", "description": "파일에 쓸 내용"},
                     },
                     "required": ["path", "content"]}},
    {"name": "list_files",
     "description": "stock-bot 디렉토리 내 파일/폴더 목록 조회. 최대 depth 2. ../ 경로 차단.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "stock-bot 디렉토리 기준 상대경로 (기본값: .)"},
                     },
                     "required": []}},
    {"name": "read_report_pdf",
     "description": "리포트 PDF 읽기. mode=image(기본, 자동 합치기: ≤50p 1p/img, 51p+ 2p/img, 최대 50장=100p, 초과 시 next_pages 안내), text(텍스트 전페이지 추출·차트는 글자만), pdf(PDF 원본 임베드·실험적). pages 파라미터로 범위 지정 가능(예 '1-3' 또는 '1-3,10,20-25' 비연속). 종목코드만으로 최신 리포트 자동 조회.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker":    {"type": "string",  "description": "종목코드"},
                         "report_id": {"type": "integer", "description": "리포트 ID (manage_report에서 확인, 생략 시 해당 종목 최신 PDF)"},
                         "pages":     {"type": "string",  "description": "페이지 범위 (예: '1-3' 또는 '1-3,10,20-25' 비연속, 생략 시 전체). pdf 모드에서는 무시됨"},
                         "mode":      {"type": "string",  "description": "image(기본)|text|pdf", "enum": ["image", "text", "pdf"]},
                     },
                     "required": ["ticker"]}},
    {"name": "get_change_scan",
     "description": "변화 감지 스캔. 기술적 지표+수급 기반 종목 발굴. preset: ma_convergence/volume_spike/earnings_disconnect/consensus_undervalued/oversold_bounce/vp_support/golden_cross/sector_leader/w52_breakout/short_squeeze/credit_unwind/foreign_reversal/foreign_accumulation/turnaround/fscore_jump/insider_cluster_buy. 복합: 콤마 구분.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "preset": {"type": "string", "description": "프리셋명 (콤마로 복합 가능, 예: 'earnings_disconnect,vp_support')"},
                         "n": {"type": "integer", "description": "결과 수 (기본 30, 최대 100)"},
                         "market": {"type": "string", "description": "kospi/kosdaq/all (기본 all)"},
                         "sort": {"type": "string", "description": "정렬 기준 필드명 (기본: 프리셋별 자동)"},
                     },
                     "required": []}},

    # ── Git 도구 ──────────────────────────────────────────────
    {"name": "git_status",
     "description": "현재 git 저장소 상태 조회. 브랜치명, staged/modified/untracked 파일 목록 반환.",
     "inputSchema": {"type": "object",
                     "properties": {},
                     "required": []}},

    {"name": "git_diff",
     "description": "git diff 결과 반환. staged=true 이면 --cached(스테이징 영역) diff. path 지정 시 해당 경로만. 50KB 초과 시 truncate.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path":   {"type": "string",  "description": "특정 파일/디렉토리 경로 (선택)"},
                         "staged": {"type": "boolean", "description": "true이면 --cached diff (기본 false)"},
                     },
                     "required": []}},

    {"name": "git_log",
     "description": "git 커밋 로그 조회. n개(기본 10, 최대 50) 반환. path 지정 시 해당 경로의 커밋만.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "n":    {"type": "integer", "description": "조회할 커밋 수 (기본 10, 최대 50)"},
                         "path": {"type": "string",  "description": "특정 파일/디렉토리 경로 (선택)"},
                     },
                     "required": []}},

    {"name": "git_commit",
     "description": "지정한 파일을 staging하고 커밋. .py/.env 파일은 커밋 불가. message는 최대 500자.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "message": {"type": "string", "description": "커밋 메시지 (최대 500자)"},
                         "files":   {"type": "array",  "items": {"type": "string"},
                                     "description": "staging할 파일 경로 목록 (.py/.env 불가)"},
                     },
                     "required": ["message", "files"]}},

    {"name": "git_push",
     "description": "origin main 브랜치에 push. main 브랜치일 때만 허용.",
     "inputSchema": {"type": "object",
                     "properties": {},
                     "required": []}},

    # 34. get_alpha_metrics — F-Score/M-Score/FCF 메트릭 조회 (F/M/FCF Phase4)
    {"name": "get_alpha_metrics",
     "description": "종목별 F-Score/M-Score/FCF 메트릭 조회 (daily_snapshot 최신 trade_date 기준). 데이터 수집 전이면 error 반환.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "ticker": {"type": "string", "description": "종목코드 (예: 005930)"},
                     },
                     "required": ["ticker"]}},

    {"name": "get_us_ratings",
     "description": "미국 종목 애널 레이팅 조회 (이벤트/추세/컨센). mode별 출력 다름.",
     "inputSchema": {"type": "object",
      "properties": {
        "ticker": {"type": "string", "description": "미국 종목 티커 (필수, 예: GEV)"},
        "mode": {"type": "string", "enum": ["events", "trend", "consensus"], "default": "events",
                 "description": "events=개별 이벤트, trend=월별 추세, consensus=현재 컨센 요약"},
        "days": {"type": "integer", "default": 90, "description": "events 조회 기간 (일)"},
        "months": {"type": "integer", "default": 6, "description": "trend 조회 기간 (월)"},
        "min_stars": {"type": "number", "default": 0.0, "description": "별점 하한 (0=전체)"}
      },
      "required": ["ticker"]}},

    {"name": "get_us_scan",
     "description": "미국 애널 레이팅 스캔/발굴. watchlist=감시+보유, discovery=감시 밖 관심, sector=섹터 모멘텀.",
     "inputSchema": {"type": "object",
      "properties": {
        "mode": {"type": "string", "enum": ["watchlist", "discovery", "sector"], "default": "watchlist"},
        "days": {"type": "integer", "default": 7},
        "min_upgrades": {"type": "integer", "default": 3, "description": "discovery 상향 임계값"},
        "sector": {"type": "string", "description": "sector 모드 필터"}
      }}},

    {"name": "get_us_analyst",
     "description": "미국 애널 개인/그룹 조회. name 지정 시 개별, firm/sector 지정 시 메타 필터, 없으면 top 레이팅 리스트.",
     "inputSchema": {"type": "object",
      "properties": {
        "name": {"type": "string", "description": "애널 slug 또는 full name"},
        "firm": {"type": "string", "description": "증권사 필터 (us_analysts.firm LIKE)"},
        "sector": {"type": "string", "description": "섹터 필터 (us_analysts.sectors JSON LIKE)"},
        "top": {"type": "integer", "default": 10},
        "min_stars": {"type": "number", "default": 4.0},
        "days": {"type": "integer", "default": 14}
      }}},

    {"name": "watch_analyst",
     "description": "미국 톱 애널 확정/해제. slug 지정 + watched=true/false. discovery 모드에서 사용됨.",
     "inputSchema": {"type": "object",
      "properties": {
        "slug": {"type": "string", "description": "애널 slug (예: mark-strouse). 필수."},
        "watched": {"type": "boolean", "default": True, "description": "true=톱 애널 확정, false=해제"}
      },
      "required": ["slug"]}},

    {"name": "get_us_earnings_transcript",
     "description": "FMP 미국 종목 실적 발표 컨퍼런스콜 본문(transcript) 조회. CEO/CFO 가이던스 발언 + 톱애널 Q&A 직접 확인 가능. 분기당 4~6만자. 본문 큰 만큼 max_chars 절삭 옵션 제공.",
     "inputSchema": {"type": "object",
      "properties": {
        "ticker": {"type": "string", "description": "미국 티커 (예: AMD, NVDA)"},
        "year": {"type": "integer", "description": "회계연도 (예: 2025, 2026)"},
        "quarter": {"type": "integer", "description": "분기 1~4"},
        "max_chars": {"type": "integer", "default": 0,
                      "description": "본문 최대 문자수. 0=무제한 (기본). 50000 등 지정 시 절삭."}
      },
      "required": ["ticker", "year", "quarter"]}},

    {"name": "get_us_analyst_research",
     "description": "FMP 미국 종목 분석가 데이터 통합: 1) Price Target Summary (1m/3m/1y 평균 TP, 카운트) 2) Analyst Estimates (매출/EBITDA/순이익/EPS Low/High/Avg, 향후 5년) 3) Stock Grades (증권사 등급 변경 이력, 최근 N건). 한 번 호출로 'TP 근거 + 추정치 + 등급 흐름' 파악.",
     "inputSchema": {"type": "object",
      "properties": {
        "ticker": {"type": "string", "description": "미국 티커"},
        "estimates_period": {"type": "string", "enum": ["annual", "quarter"], "default": "annual"},
        "estimates_limit": {"type": "integer", "default": 5, "description": "추정치 N년/분기"},
        "grades_limit": {"type": "integer", "default": 20, "description": "등급 변경 이력 N건"}
      },
      "required": ["ticker"]}},

    {"name": "get_polymarket",
     "description": "Polymarket prediction market — 매크로/지정학/정치/Fed/이란/관세/대선 등 '돈 걸린 베팅 컨센서스' 조회. Susquehanna·Jump Trading·Bloomberg·CNBC가 활용. 24h 거래량 정렬, sports/esports/pop culture 자동 컷, $500K 미만 노이즈 제외. 매크로/이벤트 점검 시, FOMC/Fed/이란/관세/대선 키워드 언급 시, SAT_PORT_CHECK / SUN_DISCOVERY Phase 1 시 자동 호출.",
     "inputSchema": {"type": "object",
      "properties": {
        "top": {"type": "integer", "default": 10, "description": "반환 시장 수 (기본 10)"},
        "min_volume": {"type": "number", "default": 500000, "description": "최소 누적 거래량 USD (기본 500K, 노이즈 컷)"},
        "query": {"type": "string", "description": "키워드 (예: 'Fed', 'Iran', 'Trump tariff'). 제목·설명에서 매칭 필터."}
      },
      "required": []}},

    {"name": "get_macro_external",
     "description": "외부 매크로 시그널 통합 — Polymarket Fed decision 베팅 + Treasury 수익률 곡선 침체 시그널 (Estrella-Mishkin 1998). 한 번 호출로 'Fed 금리 결정 확률 + 10Y-2Y 스프레드 + 지정학·정치 매크로 베팅' 파악. 매크로 점검·SAT/SUN Phase 1·이벤트 D-1 시 자동 호출.",
     "inputSchema": {"type": "object",
      "properties": {
        "top_polymarket": {"type": "integer", "default": 8, "description": "Polymarket TOP N 시장 (기본 8)"}
      },
      "required": []}},

    {"name": "get_pension_flow",
     "description": "연기금(NPS 60~80% 비중) 종목별 N일 누적 매수/매도 — pykrx + KRX 인증. NPS 단독 시그널 근사치. 양방향 (매수 TOP + 매도 TOP + 보유/워치 양방향). 연기금/NPS 매매 점검 시, SAT_PORT_CHECK Phase 1·SUN_DISCOVERY Phase 1 시 자동 호출. '연기금', '국민연금', '기관 매수' 키워드 언급 시 자동 호출.",
     "inputSchema": {"type": "object",
      "properties": {
        "days": {"type": "integer", "default": 5, "description": "누적 일수 (기본 5)"},
        "market": {"type": "string", "enum": ["KOSPI", "KOSDAQ", "ALL"], "default": "ALL"},
        "top": {"type": "integer", "default": 30, "description": "매수/매도 각각 TOP N (기본 30)"},
        "held_watch_only": {"type": "boolean", "default": False, "description": "True면 보유+워치만 (포트 점검). False면 전체 (발굴)"}
      },
      "required": []}},

    {"name": "get_us_buy_candidates",
     "description": "톱 애널 추천 + TP 대비 업사이드 충족 미국 매수 후보 발굴. raw 데이터 반환 (정렬·필터·해석은 사용 측). watched=1 (Tier A 254명) 애널의 최근 N일 Upgrades/Initiates만 검색. 보유/워치 제외 기본. min_upside=20%면 ~50종목, 30%면 ~23종목, 10%면 ~100종목.",
     "inputSchema": {"type": "object",
      "properties": {
        "days": {"type": "integer", "default": 180,
                 "description": "최근 N일 추천만 (기본 180, 최대 365)"},
        "min_advisors": {"type": "integer", "default": 1,
                          "description": "최소 추천 톱 애널 수 (기본 1 — Tier S 거장 단독도 OK)"},
        "min_upside": {"type": "number", "default": 20.0,
                        "description": "TP 대비 최소 업사이드 % (기본 20). 음수 가능 (TP 초과도 보려면)"},
        "exclude_held_and_watch": {"type": "boolean", "default": True,
                                     "description": "보유/워치 종목 제외 (기본 true)"},
        "limit": {"type": "integer", "default": 50,
                   "description": "반환 최대 종목 수 (기본 50, 최대 200)"}
      },
      "required": []}},

    {"name": "get_youtube_transcript",
     "description": "유튜브 영상 자막 추출. URL 또는 11자 video ID 입력. 한국어 우선, 영어 fallback. 자막 없으면 에러. 기본 무제한 (Claude 1M 컨텍스트).",
     "inputSchema": {"type": "object",
      "properties": {
        "url": {"type": "string", "description": "유튜브 URL 또는 video ID (watch/youtu.be/shorts/embed/live 지원)"},
        "languages": {"type": "array", "items": {"type": "string"},
                      "description": "언어 우선순위 (기본 ['ko','en'])",
                      "default": ["ko", "en"]},
        "max_chars": {"type": "integer",
                      "description": "자막 최대 문자수. 0=무제한(기본). 극단적 방어 목적으로만 사용.",
                      "default": 0}
      },
      "required": ["url"]}},

    # 47. get_sec_filings — SEC EDGAR 1차 공시 (2026-05-27 Phase 1)
    {"name": "get_sec_filings",
     "description": "SEC EDGAR 1차 공시 조회. de-SPAC/IPO 희석 위험 탐지용. 8-K/F-1/S-1/424B3/EFFECT/6-K 등. ticker 지정 시 SEC API 실시간 조회 + DB 저장. db_only=true 시 DB 캐시만 반환.",
     "inputSchema": {"type": "object",
      "properties": {
        "ticker":  {"type": "string",
                    "description": "단일 미국 티커 (예: 'XNDU', 'NVDA', 'AMZN')"},
        "tickers": {"type": "string",
                    "description": "복수 티커, 콤마 구분 (예: 'NVDA,AMZN,XNDU'). ticker와 함께 사용 가능."},
        "forms":   {"type": "array", "items": {"type": "string"},
                    "description": "필터할 폼 종류. 기본: ['8-K','F-1','F-1/A','S-1','S-1/A','424B3','424B4','424B5','424B1','424B2','EFFECT','6-K','6-K/A','SC 13D','SC 13G','4']"},
        "days":    {"type": "integer", "default": 30,
                    "description": "최근 N일 이내 공시 (기본 30, 최대 180)"},
        "db_only": {"type": "boolean", "default": False,
                    "description": "True면 SEC API 호출 없이 DB 캐시만 반환"},
        "save_db": {"type": "boolean", "default": True,
                    "description": "True면 결과를 stock.db sec_filings 테이블에 저장"},
        "limit":   {"type": "integer", "default": 50,
                    "description": "반환 최대 건수 (기본 50, 최대 200)"}
      },
      "required": []}},
]



__all__ = [
    "MCP_TOOLS",
    "_execute_tool",
    "execute_tool",
    "TOOL_HANDLERS",
    "mcp_sse_handler",
    "mcp_messages_handler",
    "mcp_streamable_post_handler",
    "mcp_streamable_delete_handler",
    "mcp_streamable_options_handler",
    "_handle_jsonrpc",
]
