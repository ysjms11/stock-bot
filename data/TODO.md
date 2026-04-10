# TODO — 2026-04-10 최종
> 레포: ysjms11/stock-bot | 서버: 맥미니 M4 + Cloudflare Tunnel (arcbot-server.org)

---

## 🔴 즉시 (다음 세션)

### 투자 판단
- [ ] **CPI 결과 확인 (4/10 21:30 KST)** → NVDA/AMD 단기 방향성
- [ ] 보유 5종목 딥리서치 + 리서치 파일 (HD조선해양/효성중공업/LS ELECTRIC/HD현대일렉트릭/CRSP)
- [ ] 보유 7종목 손절/목표 메모(thesis) 추가 (현재 NVDA만 있음)
- [ ] LITE 리서치 파일 생성
- [ ] 시스템 프롬프트 v4 프로젝트 적용 (다운로드 완료, 교체만)

### 봇 기능 추가
- [ ] `/summary`에 events.json 기반 "이번 주/다음 주 일정" 섹션 추가
- [ ] 어닝 D-3 텔레그램 알림 기능 추가

---

## 🟡 진행중 / PENDING

### 봇 개발
- [ ] Railway 완전 삭제 (이전 완료, 프로젝트만 삭제)
- [ ] Oracle Cloud VM 해지 (더 이상 KRX 크롤링 안 함)
- [ ] GitHub API 연동 — Claude MCP로 직접 커밋/push (P2)
- [ ] 시장/섹터 전략 레포트 자동 수집 (P2)
- [ ] bot_architecture.md 생성 (P2)

### 투자 PENDING
- [ ] CRSP -5.2% → thesis 재검증 (딥리서치)
- [ ] HD조선 51% → **섹터한도 50% 초과** 대응 (비중축소 or 한도상향)
- [ ] 포트 등급-비중 반비례 문제 구조조정
- [ ] LITE 5/6 FQ3 실적 전후 추매 판단
- [ ] 삼성전자 ICMS + NAND + HBM 통합 분석
- [ ] AMD 4/28 Q1 실적 후 등급 재평가 (B+→A 검토 조건: Q3 DC $6B+)
- [ ] NVDA 5/28 Q1 실적 후 확인 (가이던스 $78B)
- [ ] 워치 44개 → 20개 이하 축소
- [ ] 촉매 발생 시 감시가 재설정 룰 확정 (RR 역산 기반)
- [ ] 레짐 🟡 전환 시 A등급 감시가 재평가

---

## ✅ 완료 (4/10)

### 봇 버그/기능
- [x] **웹 대시보드 /dash** (다크모드, 모바일 반응형)
  - markdown→HTML 변환, portfolio/watchalert/decision/trade/events 테이블
  - /dash/file/{filename} (.md/.txt/.json 렌더링, .py/.env 차단)
- [x] **get_regime v2** (11지표 → S&P 200MA + VIX 2지표 조건부)
  - 디바운스 + VIX 트랜치 + USD/KRW 참고용
- [x] **리포트 크롤러 통합** (한경 + 네이버 + 와이즈리포트)
  - HD한국조선해양 26년 3~4월 리포트 7건 모두 수집
  - 와이즈 perPage=100 추가 → 1년치 86건
  - 중복 제거 키 단순화 (date, source, ticker)
- [x] **공매도/신용/외인보유 수집 조사 → 보류**
  - KRX 정보데이터시스템: 공매도 → 금융투자협회 redirect, 외인/신용 종목별만
  - 공공데이터포털/네이버: 불가
  - KIS API: 가능하나 1.5초/호출 부담
  - 결정: 딥서치 시점에 get_market_signal(short_sale) 개별 조회
- [x] **백필 232거래일 완료** (2025-04-23 ~ 2026-04-07)
  - foreign_trend_5d/20d/60d 활성화 (105종목 매칭)
- [x] **KRX Safari keepalive 멀티탭 순회 강화**
- [x] **GitHub Actions krx_update.yml / krx_backfill.yml 삭제**
  - scripts collect_supplement/upload_supplement 제거
  - main.py /api/krx_supplement 엔드포인트 제거
- [x] **mcp_tools.py load_krx_db import 에러 수정** (로컬 import가 상위 가림)

### 투자 판단
- [x] NVDA 딥서치 → 12주 매수 @ $183.68, 확신등급 A
- [x] NVDA 봇 기록 (trade T004 + 손절$140/목표$274 + decision + 리서치파일)
- [x] AMD 딥서치 → 목표 $235→$280, 등급 B→B+, 전량홀드
- [x] AMD 봇 기록 (메모 + decision + 리서치파일)
- [x] data/research/ 폴더 (NVDA.md, AMD.md)
- [x] events.json 어닝 일정 (AMD 4/28, NVDA 5/28, LITE 5/6, CRSP 5/7)
- [x] NVDA 워치리스트 매수감시 → 보유종목 손절/목표 전환

---

## ✅ 완료 (4/9)

- [x] 딥리서치 12건 (진입전략/사이징/레짐시스템/지표최적화/프롬프트검증/레짐점수설계)
- [x] 투자 철학 근본 재검토 → 4개 핵심 규칙 확정
- [x] 시스템 프롬프트 v4 완성 (7번 재작성, 충돌 수정, 단순화)
- [x] 봇 get_regime 전면 재설계 (11개→2개 지표, 조건부 로직)
- [x] FILES.md / HANDOVER.md / regime_update_notes.md 저장
- [x] decision_log 3건 기록

---

## ✅ 완료 (4/8 이전)

### 인프라
- [x] 맥미니 M4 서버 이전 (Railway → 맥미니 + Cloudflare Tunnel + arcbot-server.org)
- [x] launchd 자동시작 (com.stock-bot.main, krx-update, krx-keepalive, cloudflared)
- [x] KRX OPEN API 8개 서비스 승인 + 시세 자동 수집
- [x] Safari 카카오 로그인 + keepalive 25분 주기 연장

### KRX DB v2
- [x] 설계서 data/krx_db_design.md (62개 필드)
- [x] KRX OPEN API 시세 + Safari 세션 (PER/PBR/수급)
- [x] FnGuide 컨센서스 전종목 509종목
- [x] 기술적 지표 (MA5~200, RSI14, 볼린저, 52주, YTD, VP 60d/250d)
- [x] 추세 점수 다구간 (volume_ratio, ma_spread_change, rsi_change, eps_change)
- [x] 섹터 상대강도 + earnings_gap

### MCP 도구 (28개)
- [x] get_change_scan (12개 프리셋, 임계값 파라미터화)
- [x] read_file / write_file / list_files
- [x] watch_grade / 모멘텀경고 16:30 이동 + 추정수급
- [x] credit/lending/after_hours 필드 매핑

### 텔레그램 알림
- [x] 포트 건강 체크 (15:40 장마감 요약에 추가)
- [x] 워치 변화 감지 (19:00 평일)
- [x] 레짐 전환 가이드 (전환 확정 시)
- [x] 감시가 터치 브리핑 (레짐/현금/이벤트 포함)
- [x] Sunday 30 리마인더 (일요일 19:00)

---

## 알림 스케줄

| 주기 | 시간 | 알림 |
|------|------|------|
| 10분 | 수시 | 손절/감시가 체크 + 브리핑 |
| 30분 | 수시 | 이상 신호 + DART 공시 |
| 1시간 | 수시 | 레짐 전환 체크 |
| 매일 | 15:40 | 장마감 요약 + 포트 건강 |
| 매일 | 15:55 | KRX DB 갱신 (launchd) |
| 매일 | 16:30 | 모멘텀 경고 |
| 매일 | 19:00 | 워치 변화 감지 |
| 매일 | 06:00 | 미국 장마감 요약 |
| 일요일 | 19:00 | Sunday 30 리마인더 |

---

## 데이터 수집 현황

| 데이터 | 소스 | 수집 |
|--------|------|------|
| 시세 (OHLCV/시총) | KRX OPEN API | ✅ 매일 전종목 |
| PER/PBR/EPS/BPS | Safari KRX | ✅ 매일 전종목 |
| 외인/기관/개인 수급 | Safari KRX | ✅ 매일 전종목 |
| 컨센서스 목표가 | FnGuide | ✅ 매일 전종목 |
| 기술 지표 | 자체 계산 | ✅ |
| 공매도/신용/외인보유 | KIS API | ⏸️ 딥서치 시 개별 조회 |

---

## 구현 시 주의사항
1. 맥미니 로컬 data/ (DATA_DIR 환경변수)
2. KIS API 초당 20회 제한 (sleep 0.3)
3. KRX Safari 카카오 로그인 필수 — keepalive 25분 연장
4. MCP 도구 추가 시 mcp_tools.py 스키마 등록 필수
5. Agent Team: architect(Opus) → dev(Sonnet) → reviewer(Codex)
6. 데이터 보관 무제한
</content>
</invoke>