# stock-bot 코드 리뷰 보고서

> 리뷰 일시: 2026-03-30

## 요약
- 전체 평가: **C** (동작은 하지만, 심각한 보안 이슈 존재)
- 심각 이슈: 3건
- 경고: 5건
- 정보: 4건

---

## 보안

### [심각] SEC-1: Git remote URL에 GitHub 토큰 평문 노출

```
origin  https://ysjms11:REDACTED@github.com/ysjms11/stock-bot.git
```

Git remote URL에 `ghp_` Personal Access Token이 평문으로 포함되어 있다. `.git/config`는 로컬 파일이지만, 누군가 레포를 clone하거나 디스크 접근 시 토큰이 유출된다. 또한 이 리뷰 보고서 자체가 커밋되면 토큰이 영구 기록된다.

**조치 필요:**
1. 해당 토큰 즉시 revoke (GitHub Settings > Developer settings > Personal access tokens)
2. `git remote set-url origin https://github.com/ysjms11/stock-bot.git` 실행
3. Git credential helper 또는 SSH 키 방식으로 전환

### [심각] SEC-2: KIS_APP_SECRET이 모든 API 요청 헤더에 포함

`_kis_headers()` (kis_api.py:873-880)와 구 방식 함수들이 **매 HTTP 요청 헤더에 `appsecret`을 포함**해서 전송한다. 이것은 KIS API의 요구사항이므로 전송 자체는 불가피하지만, 아래 위험이 존재한다:

- `_kis_get()` 반환값이나 에러 로그에 요청 헤더가 포함될 경우 시크릿 노출
- aiohttp 디버그 로깅 활성화 시 헤더 전체가 stdout에 출력됨

현재 코드에서 `print(f"...{token}...")`이나 헤더 직접 출력은 **발견되지 않았다** -- 이 부분은 양호.

### [심각] SEC-3: DART API 키가 URL에 평문 포함

kis_api.py:2427에서 `crtfc_key`가 URL 쿼리스트링에 포함되고, 해당 URL이 `print()`로 출력된다:

```python
url = f"{DART_BASE_URL}/corpCode.xml?crtfc_key={DART_API_KEY}"
print(f"[DART] corpCode.xml 다운로드 시작: {url[:60]}...")
```

`url[:60]`으로 잘리므로 실제 키가 노출될 확률은 낮지만, URL 길이가 변경되면 노출 가능하다. Railway 로그에 기록된다.

### [경고] SEC-4: traceback이 MCP 응답에 포함

mcp_tools.py에서 6개 이상의 위치에서 `traceback.format_exc()`를 result dict에 포함해 클라이언트에 반환한다 (예: mcp_tools.py:2346-2347). 스택 트레이스는 내부 파일 경로, 환경 정보 등을 노출할 수 있다.

```python
result = {"error": str(e), "traceback": tb}
```

**권장:** 프로덕션에서는 traceback을 서버 로그에만 기록하고, 클라이언트에는 에러 메시지만 반환할 것.

### [정보] SEC-5: 환경변수 복원 로그

kis_api.py:91에서 `_env_key` 이름만 출력하고 값은 출력하지 않는다. 양호.

---

## 메모리/디스크 관리

### [경고] DISK-1: decision_log.json -- 크기 제한 없음

`decision_log.json`은 dict(날짜 키) 구조이며, 어디에서도 오래된 항목을 삭제하지 않는다. 매일 투자판단을 기록하면 연간 365개 항목이 누적된다. 당장 문제는 아니지만 수년 운용 시 파일 크기가 커진다.

| 파일 | 제한 | 상태 |
|------|------|------|
| `reports.json` | 90일 + 종목당 5건 | report_crawler.py:276-286에서 정리됨. **양호** |
| `supply_history.json` | 종목당 180일 | kis_api.py:1214에서 `[-180:]` 슬라이싱. **양호** |
| `portfolio_history.json` | 365일 | kis_api.py:714에서 `snaps[-365:]`. **양호** |
| `watchlist_log.json` | 200건 | kis_api.py:334에서 `log[-200:]`. **양호** |
| `trade_log.json` | 1000건 | kis_api.py:222에서 `trades[-1000:]`. **양호** |
| `compare_log.json` | 50건 | mcp_tools.py:1477에서 `log[-50:]`. **양호** |
| `decision_log.json` | **제한 없음** | dict 구조, 삭제 로직 없음. **경고** |
| `consensus_cache.json` | 없음 (덮어쓰기) | 매주 전체 교체. 양호 |
| `dart_screener_cache.json` | 당일만 유지 | mcp_tools.py:67에서 오늘 외 삭제. 양호 |
| `sector_flow_cache.json` | 없음 (덮어쓰기) | 단일 스냅샷. 양호 |
| `sector_rotation.json` | 없음 (덮어쓰기) | 단일 스냅샷. 양호 |
| `dart_seen.json` | **제한 없음** | `{ids: [...]}` 리스트가 계속 누적됨. **경고** |
| `events.json` | 없음 (수동 관리) | 양호 |

### [경고] DISK-2: dart_seen.json -- 무한 축적

`dart_seen.json`의 `ids` 배열은 한번 추가되면 영구 보관된다. 하루 수십 건씩 30분마다 체크하므로, 수년 운용 시 수만 건이 될 수 있다. 최근 30일분만 유지하는 정리 로직이 필요하다.

---

## 코드 품질

### [경고] QUAL-1: 하드코딩된 매직 넘버

심각한 것만 정리:

| 위치 | 값 | 설명 | 권장 |
|------|-----|------|------|
| kis_api.py 다수 | `0.3`, `0.2`, `0.4` | API rate limit sleep | `_KIS_API_DELAY = 0.3` 상수화 |
| kis_api.py:1058 | `100` | 섹터 로테이션 최소 변화량(백만원) | 상수화 필요 |
| main.py:46 | `50`, `35` | 섹터/종목 비중 한도 % | 이미 `_SECTOR_LIMIT`, `_STOCK_LIMIT` 상수 존재. 양호 |
| kis_api.py:714 | `365` | 포트폴리오 히스토리 최대 일수 | `_MAX_SNAPSHOT_DAYS = 365` 상수화 권장 |
| report_crawler.py | `90`, `5`, `50`, `10000` | 보관/제한 상수 | 이미 `_RETAIN_DAYS` 등으로 상수화됨. **양호** |

URL 하드코딩은 `KIS_BASE_URL`, `DART_BASE_URL` 등으로 잘 상수화되어 있다. Yahoo Finance URL(`query1.finance.yahoo.com`)과 FnGuide URL(`comp.fnguide.com`)은 단일 사용처이므로 현 상태 허용 범위.

### [정보] QUAL-2: 구 방식 / 신 방식 API 함수 이중 존재

`get_stock_price()` (구)와 `kis_stock_price()` (신)이 공존한다. `batch_stock_detail()`은 구 방식 `kis_stock_price()`를 호출하지만, 이것도 내부적으로 동일한 TR_ID를 사용하므로 동작에는 문제 없다. 다만 코드 정리 시 신 방식으로 통일하는 것이 좋다.

### [정보] QUAL-3: `from kis_api import *` 패턴

main.py와 mcp_tools.py 모두 `from kis_api import *`를 사용한다. kis_api.py에 `__all__`이 정의되어 있지 않아, 모든 모듈 레벨 이름이 임포트된다. 의도치 않은 이름 충돌 가능성이 있다.

### [정보] QUAL-4: aiohttp ClientSession 남용

거의 모든 KIS API 함수가 내부에서 `async with aiohttp.ClientSession() as s:`를 생성한다. 함수 호출마다 새 TCP 연결을 맺으므로 비효율적이다. 장기적으로 공유 세션 풀 도입이 권장되나, 현재 호출 빈도(초당 2-3회)에서는 실질적 문제는 아니다.

---

## requirements.txt

### [경고] REQ-1: 버전 핀닝 부재

```
python-telegram-bot[job-queue]>=21.10
aiohttp>=3.10.0
beautifulsoup4
lxml
requests
finance-datareader>=0.9.50
yfinance>=0.2.0
pdfplumber>=0.10.0
tzdata
```

- 대부분 최소 버전(`>=`)만 지정하거나 버전 미지정. Railway에서 `pip install` 시 최신 버전이 설치되며, 호환성 깨질 위험이 있다.
- `beautifulsoup4`, `lxml`, `requests`는 버전 지정 없음.
- 알려진 보안 취약 패키지: **현재 시점에서 특별히 심각한 CVE는 확인되지 않음**. 다만 `requests`와 `aiohttp`는 정기적 업데이트 대상.
- **권장:** `pip freeze > requirements.txt`로 정확한 버전 핀닝. 또는 `requirements.lock` 별도 관리.

---

## 개선 제안 (우선순위순)

| 우선순위 | 항목 | 분류 | 설명 |
|---------|------|------|------|
| **P0** | SEC-1 | 보안 | Git remote URL에서 GitHub 토큰 제거 + 토큰 revoke. **즉시 조치 필요** |
| **P1** | SEC-3 | 보안 | DART API URL에서 키를 로그에 출력하지 않도록 수정 |
| **P1** | SEC-4 | 보안 | traceback을 MCP 클라이언트 응답에서 제거, 서버 로그에만 기록 |
| **P2** | DISK-1 | 디스크 | decision_log.json에 최대 항목 수 또는 보관 기간 제한 추가 |
| **P2** | DISK-2 | 디스크 | dart_seen.json에 최근 30일분만 유지하는 정리 로직 추가 |
| **P2** | REQ-1 | 의존성 | requirements.txt 버전 핀닝 (최소한 major.minor 수준) |
| **P3** | QUAL-1 | 코드 품질 | API sleep 딜레이 등 매직 넘버 상수화 |
| **P3** | QUAL-4 | 성능 | aiohttp 세션 재사용 (공유 세션 풀) |
