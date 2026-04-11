# 인수인계 — 2026-04-10 세션 종료
> 다음 세션 AI가 여기만 읽으면 바로 이어서 작업 가능

---

## ✅ 이번 세션 (4/10) 완료

### 봇 개발
1. **웹 대시보드 /dash 추가** — markdown 렌더링, 다크모드, 모바일 반응형
   - URL: https://bot.arcbot-server.org/dash
   - 파일 렌더러: `/dash/file/{filename}` (.md/.txt/.json)
2. **리포트 크롤러 3개 사이트 통합**
   - 한경컨센서스 + 네이버 + **와이즈리포트 메타데이터** (JSON API, perPage=100)
   - HD한국조선해양 26년 3~4월 리포트 7건 모두 수집 확인
   - 중복 제거 키 단순화 (date+source+ticker)
3. **get_regime v2 재설계** — 11지표 → S&P 200MA + VIX 2지표 조건부 로직
4. **백필 232거래일 완료** — foreign_trend 5d/20d/60d 활성화
5. **공매도/신용/외인보유 조사 → 보류 결정**
   - KRX 정보데이터시스템 한계, 공공데이터포털 없음, KIS API는 1.5초/호출 부담
   - **결정**: 딥서치 시 `get_market_signal(short_sale, ticker=...)` 개별 조회
6. **GitHub Actions krx_update.yml / krx_backfill.yml 삭제**
   - scripts/krx_update.py collect_supplement 제거
   - main.py /api/krx_supplement 엔드포인트 제거
7. **KRX Safari keepalive 멀티탭 순회 강화**
8. **mcp_tools.py load_krx_db import 에러 수정**

### 아키텍처 변경 (이 세션 이후)
- **KRX DB: JSON → SQLite 전환** (`data/krx.db`, 3테이블 112컬럼)
  - `daily_prices`: 시세/밸류에이션/수급 (기존 JSON 호환)
  - `financials`: 손익계산서 + 재무상태표 (KIS API `kis_income_statement` / `kis_balance_sheet`)
  - `overtime_prices`: 시간외 종가/등락률/거래량 (`kis_overtime_daily`)
- **db_collector.py 신규** (~1700줄): KIS API 풀수집 + SQLite 저장 + 지표 계산 통합
- **WebSocket 통합**: KR 실시간 + US 폴링 + 24시간 연속 관리
- **대시보드 v2**: 포트폴리오 현재가/손익 증권사 스타일 렌더링
- **파일 구조 5파일로 확장**: `kis_api.py` / `main.py` / `mcp_tools.py` / `krx_crawler.py` / `db_collector.py`

### 투자
- NVDA 12주 매수 @ $183.68, 등급 A (trade T004 + 손절 $140/목표 $274)
- AMD 딥서치 → 목표 $235→$280, 등급 B→B+, 전량홀드
- data/research/ 폴더: NVDA.md, AMD.md

---

## 🔴 다음 세션 즉시 할 일

### 1. CPI 결과 확인 (4/10 21:30 KST)
- NVDA/AMD 단기 방향성 판단

### 2. 보유 종목 딥리서치 + 리서치 파일
- HD조선해양 / 효성중공업 / LS ELECTRIC / HD현대일렉트릭 / CRSP
- 각 종목 data/research/{TICKER}.md 생성

### 3. 보유 7종목 손절/목표 thesis 추가
- 현재 NVDA만 있음

### 4. LITE 리서치 파일 생성
- 5/6 FQ3 실적 전 점검

### 5. 시스템 프롬프트 v4 프로젝트 적용
- system_prompt_v4_simple.md 다운로드 완료, 프로젝트 지식 교체만

---

## 🟡 봇 개발 (우선순위)

### 즉시 가능
- [ ] `/summary`에 events.json 기반 "이번 주/다음 주 일정" 섹션 추가
- [ ] 어닝 D-3 텔레그램 알림 기능 추가

### 정리 (10분이면 끝)
- [ ] Railway 프로젝트 삭제 (웹에서)
- [ ] Oracle Cloud VM 해지 (웹에서)

### P2
- [ ] GitHub API 연동 (Claude가 MCP로 직접 commit/push)
- [ ] 시장/섹터 전략 레포트 자동 수집
- [ ] bot_architecture.md 생성

---

## 📋 투자 PENDING

- [ ] CRSP -5.2% → thesis 재검증
- [ ] HD조선 51% → **섹터한도 50% 초과** 대응
- [ ] 포트 등급-비중 반비례 문제 구조조정
- [ ] LITE 5/6 FQ3 실적 전후 추매 판단
- [ ] 삼성전자 ICMS + NAND + HBM 통합 분석
- [ ] AMD 4/28 Q1 실적 후 등급 재평가 (B+→A 조건: Q3 DC $6B+)
- [ ] NVDA 5/28 Q1 실적 후 확인 (가이던스 $78B)
- [ ] 워치 44개 → 20개 이하 축소
- [ ] 촉매 발생 시 감시가 재설정 룰 확정

---

## 핵심 시스템 상태

### 투자 규칙 (4개 핵심)
1. 탐욕에 팔고, 공포에 사라
2. thesis 있으면 사고, 없으면 안 산다
3. A등급은 조기에 팔지 않는다
4. 분기마다 모든 포지션의 thesis를 재검증한다

### 레짐 시스템 (v2)
- **2개 지표**: S&P 200MA (±3% 버퍼) + VIX (20/30 + 백워데이션)
- **조건부 로직**: 🟢=둘다양호, 🔴=둘다위험, 🟡=나머지
- **현재**: 🟡 중립 (S&P -0.51% from 200MA, VIX 21.4)
- **USD/KRW**: 레짐 아님. 참고값만 표시 (현재 1,481원)
- **현금**: 🟢 15~20% / 🟡 10~15% / 🔴 5%까지 투입
- **디바운스**: 🟢 5일 / 🔴 3일 / 🟢→🟡 즉시 / 🔴→🟡 VIX<25 OR S&P>-3%

### 삭제된 것들 (충돌로 제거)
- 비중 제한 (규칙3과 충돌)
- 기계적 -25% 손절 (앵커링 → thesis 무효화 조건으로 대체)
- 드로다운 관리 규칙 (규칙1과 충돌)
- 이벤트 대응 상세 (4개 규칙이 커버)
- AI 안전편향 방지 상세 (콜+반론2개 구조로 대체)

---

## 데이터 수집 인프라

| 데이터 | 소스 | 수집 주기 | 저장 |
|--------|------|----------|------|
| 시세 (OHLCV/시총) | KIS API (`db_collector.py`) | 매일 15:55 (launchd) | SQLite daily_prices |
| PER/PBR/EPS/BPS | KIS API 풀수집 | 매일 15:55 | SQLite daily_prices |
| 외인/기관/개인 수급 | KIS API 풀수집 | 매일 15:55 | SQLite daily_prices |
| 손익계산서/재무상태표 | KIS API (`kis_income_statement` / `kis_balance_sheet`) | 매일 15:55 | SQLite financials |
| 시간외 데이터 | KIS API (`kis_overtime_daily`) | 매일 15:55 | SQLite overtime_prices |
| 컨센서스 목표가 | FnGuide 크롤링 | 매일 15:55 | SQLite daily_prices |
| 공매도/신용/외인보유 | KIS API | 딥서치 시 개별 조회 | — |
| 증권사 리포트 | 한경+네이버+와이즈 | 매일 수집 | data/reports/ |
| KRX DB 축적 | 232일 (2025-04-23 ~) | 보관 무제한 | data/krx.db |

> **이전 방식 (JSON)**: `data/krx_db/YYYYMMDD.json` → SQLite 전환으로 폐기. 설계서는 `data/krx_db_design.md` (레거시 참조용 유지).

---

## 서버 상태

- **맥미니 M4**: 192.168.0.36
- **도메인**: arcbot-server.org (Cloudflare Tunnel)
- **launchd 실행 중**:
  - com.stock-bot.main (텔레그램 봇 + MCP)
  - com.stock-bot.krx-update (매일 15:55 KRX DB 갱신 — `db_collector.py` 실행)
  - com.stock-bot.krx-keepalive (25분마다 KRX Safari 세션 연장)
  - com.stock-bot.cloudflared (터널)
- **DB 파일**: `data/krx.db` (SQLite, 3테이블 112컬럼)
- **GitHub**: ysjms11/stock-bot, main 브랜치
- **최근 커밋**: `f400095` chore: GitHub Actions KRX workflows 삭제

---

## 세션 이어받기 체크리스트

다음 세션 시작 시:
1. [ ] data/HANDOVER.md (이 파일) 읽기
2. [ ] data/TODO.md 읽기 (즉시 할 일 확인)
3. [ ] `get_regime()` 호출해서 현재 레짐 확인
4. [ ] `get_portfolio()` 호출해서 현재 포트 확인
5. [ ] 텔레그램 알림 로그 확인 (어제밤 ~ 오늘 새벽)
</content>
</invoke>