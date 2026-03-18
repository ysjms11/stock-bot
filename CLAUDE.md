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
```

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

> Railway는 `/data` 볼륨을 영구 마운트해야 재시작 후에도 데이터 보존됨.

---

## main.py 구조 (위→아래)

```
[1~11]    imports
          aiohttp, telegram, xml, datetime 등

[13~33]   환경변수 & 상수
          TELEGRAM_TOKEN, KIS_BASE_URL, KST, 데이터파일 경로 6개

[38~72]   헬퍼 함수 & 상수
          _is_us_ticker()       영문 티커 → 미국 종목 판별
          _NYSE_TICKERS         NYSE 대표 종목 세트
          _guess_excd()         NYS/NAS 거래소코드 추정
          _is_us_market_hours_kst()   미국 장시간 여부 (KST)
          DART_KEYWORDS         중요 공시 키워드 목록

[75~121]  파일 저장/로드
          load_json / save_json
          load_watchlist / load_stoploss / load_us_watchlist
          load_dart_seen / load_watchalert

[123~355] KIS API 함수
          get_kis_token()       OAuth 토큰 (20시간 캐시)
          get_stock_price()     국내 현재가 (구 방식, FHKST01010100)
          get_investor_trend()  국내 수급 (구 방식)
          get_volume_rank()     거래량 상위 (구 방식)
          get_kis_index()       KOSPI/KOSDAQ 지수
          _kis_headers()        공통 헤더 생성
          _kis_get()            GET 래퍼 (신 방식, aiohttp session 인자)
          kis_stock_price()     국내 현재가 (신 방식)
          kis_stock_info()      종목 기본정보
          kis_investor_trend()  국내 수급 (신 방식)
          kis_credit_balance()  신용잔고
          kis_short_selling()   공매도
          kis_volume_rank_api() 거래량 상위 (신 방식)
          kis_foreigner_trend() 외국인 순매수 상위
          kis_sector_price()    업종별 시세
          WI26_SECTORS          7개 업종 코드/이름
          _fetch_sector_flow()  업종 외국인+기관 순매수
          kis_us_stock_price()  해외 현재가 (HHDFS00000300)
          kis_us_stock_detail() 해외 현재가상세 (HHDFS76200200)

[358~373] Yahoo Finance
          get_yahoo_quote()     미국 지수/개별 시세 (fallback)

[376~432] DART API
          search_dart_disclosures()     최근 N일 공시 목록
          filter_important_disclosures() 워치리스트+키워드 필터

[435~480] 뉴스 (Google News RSS)
          fetch_news()

[480~615] 자동알림 1: daily_kr_summary
          매일 06:40 KST — 한국장 개장 전 요약 (지수·수급·손절·섹터·DART)

[617~730] 자동알림 2: daily_us_summary
          매일 22:00 KST — 미국장 마감 요약 (S&P500·나스닥·VIX·환율)

[733~855] 자동알림 3: check_stoploss
          10분마다 — 손절선 도달 + 매수희망가 도달 텔레그램 알림

[857~868] 자동알림 4: check_fx_alert
          1시간마다 — 환율 ±1% 이상 변동 알림

[871~942] 자동알림 5: check_anomaly
          30분마다 — 거래량+외국인 복합 이상 신호

[945~1007] 자동알림 6: check_dart_disclosure
           30분마다 — DART 중요 공시 (장중 08~16:30)

[925~942] 자동알림 7: weekly_review
          매주 일요일 01:00 KST — 주간 리뷰 리마인더

[1010~1540] 텔레그램 명령어 핸들러
            /start /analyze /scan /macro /news /dart /summary
            /watchlist /watch /unwatch
            /uslist /addus /remus
            /setstop /delstop /stops
            /setportfolio /setusportfolio /help
            post_init()

[1543~1638] MCP_TOOLS 배열 (15개)
            Claude MCP 도구 스키마 정의

[1641~2008] _execute_tool()
            MCP 도구 실행 로직 (if/elif 체인)

[2010~2114] MCP 서버
            _handle_jsonrpc()      JSON-RPC 2.0 처리
            _mcp_sessions          SSE 세션 관리
            mcp_sse_handler()      GET /mcp — SSE 스트림
            mcp_messages_handler() POST /mcp/messages

[2116~2170] 진입점
            main() + _run_all()
            스케줄 등록, aiohttp + telegram 동시 실행
```

---

## KIS API 호출 패턴

모든 신규 함수는 `_kis_get()` 래퍼를 사용:

```python
async def kis_some_api(ticker: str, token: str) -> dict:
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/...",
            "TR_ID_HERE", token,
            {"param1": "val1", "param2": "val2"})
        return d.get("output", {})
```

**국내 주요 TR_ID**

| TR_ID | 용도 |
|-------|------|
| `FHKST01010100` | 국내 현재가 |
| `FHKST01010900` | 국내 수급(외국인/기관) |
| `FHPST01710000` | 거래량 상위 |
| `FHKUP03500100` | 업종별 시세 |
| `FHPTJ04060100` | 외국인 순매수 상위 |
| `FHPUP02100000` | KOSPI/KOSDAQ 지수 |
| `CTPF1002R` | 종목 기본정보 |
| `FHKST01010600` | 신용잔고 |
| `FHKST01010700` | 공매도 |

**해외 주요 TR_ID**

| TR_ID | 용도 | 경로 |
|-------|------|------|
| `HHDFS00000300` | 해외 현재가 | `/uapi/overseas-price/v1/quotations/price` |
| `HHDFS76200200` | 해외 현재가상세 (PER/PBR/시총/52주) | `/uapi/overseas-price/v1/quotations/price-detail` |

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

## MCP 도구 목록 (15개)

| # | 이름 | 설명 |
|---|------|------|
| 1 | `scan_market` | 거래량 상위 15개 종목 |
| 2 | `get_portfolio` | 보유 포트폴리오 (한국+미국) 손익 |
| 3 | `get_stock_detail` | 국내 개별 종목 상세 (현재가·PER·PBR·수급) |
| 4 | `get_foreign_rank` | 외국인 순매수 상위 종목 |
| 5 | `get_dart` | 워치리스트 최근 3일 DART 공시 |
| 6 | `get_macro` | KOSPI·KOSDAQ 지수 + USD/KRW 환율 |
| 7 | `get_sector_flow` | WI26 업종별 외국인+기관 순매수 상위/하위 3개 |
| 8 | `add_watch` | 한국 워치리스트 종목 추가 |
| 9 | `remove_watch` | 한국 워치리스트 종목 제거 |
| 10 | `get_alerts` | 손절가 목록 + 현재가 대비 손절까지 남은 % |
| 11 | `set_alert` | 손절가/목표가 등록 및 수정 |
| 12 | `get_us_stock_detail` | 미국 개별 종목 상세 (현재가·등락률·PER·PBR·시총·52주) |
| 13 | `set_watch_alert` | 매수 희망가 감시 등록 (미보유 종목, 가격 도달 시 텔레그램 알림) |
| 14 | `get_watch_alerts` | 매수 희망가 감시 목록 조회 |
| 15 | `remove_watch_alert` | 매수 희망가 감시 제거 |

---

## 새 MCP 도구 추가하는 방법

**Step 1 — API 함수 작성**

```python
async def kis_new_api(ticker: str, token: str) -> dict:
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/...", "TR_ID", token, {"param": ticker})
        return d.get("output", {})
```

**Step 2 — MCP_TOOLS 배열에 스키마 추가** (`_execute_tool` 위)

```python
{"name": "new_tool_name", "description": "도구 설명",
 "inputSchema": {"type": "object",
                 "properties": {"ticker": {"type": "string", "description": "종목코드"}},
                 "required": ["ticker"]}},
```

**Step 3 — `_execute_tool` 함수에 elif 핸들러 추가** (`else: result = {"error": ...}` 바로 위)

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
- **`/data` 볼륨**: Railway에서 볼륨 마운트 안 하면 재배포 시 데이터 초기화됨.
- **KIS 토큰 캐시**: `_token_cache`는 메모리에만 존재. 재시작 시 재발급 필요 (20초 내외 소요).
- **Yahoo Finance fallback**: 미국 장 요약(`daily_us_summary`)과 손절 체크(`check_stoploss` US)는 Yahoo Finance 사용. KIS 해외 API와 혼용 주의.
- **`check_stoploss` US**: Yahoo Finance로 가격 확인, KIS API 아님.

---

## 코딩 규칙

- **단일 파일**: 모든 로직은 `main.py` 한 파일에 유지.
- **KIS API 신 방식**: 새 함수는 반드시 `_kis_get()` 래퍼 사용 (구 방식 `get_stock_price()` 패턴 사용 금지).
- **에러 처리**: 개별 종목 루프 내부는 `try/except Exception: pass` 패턴으로 한 종목 오류가 전체 중단 방지.
- **asyncio.sleep(0.3~0.4)**: KIS API 연속 호출 시 rate limit 방지를 위해 `await asyncio.sleep(0.3)` 삽입.
- **섹션 구분**: `# ━━━━━━━━━━━━━━━━━━━━━━━━━` 주석으로 논리적 섹션 구분 유지.
- **한국어 변수명**: 텔레그램 메시지 문자열 외에는 영문 변수명 사용.
- **MCP 도구 순서**: `MCP_TOOLS` 배열과 `_execute_tool` elif 체인의 순서를 일치시킬 것.
