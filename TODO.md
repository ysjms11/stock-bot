# 주식봇 개선 TODO
> 업데이트: 2026-04-10 | 레포: ysjms11/stock-bot | 서버: 맥미니 M4 + Cloudflare Tunnel (arcbot-server.org)

---

## 🔄 진행중 / PENDING

### 봇 개발
- [ ] 과거 1년 백필 진행 상황 확인 (PID 19553, 백그라운드 실행 중)
- [ ] Railway 완전 삭제
- [ ] Oracle Cloud VM 해지 검토
- [ ] GitHub API 연동 — Claude MCP로 직접 커밋/push (P2)
- [ ] 와이즈리포트 메타데이터만 추가 (PDF 유료, JSON API는 무료) — 보류

### 투자 PENDING
- [ ] CRSP -29.6% → 25% 강제재평가 (딥서치 필요)
- [ ] HD조선 51.8% 비중 축소 검토 (🟡 전환 후)
- [ ] 촉매 발생 시 감시가 재설정 룰 확정 (RR 역산 기반)
- [ ] 레짐 🟡 전환 시 A등급 감시가 재평가 (NVDA $180 2.3%, LITE $750)
- [ ] LITE 5/5 Q3 실적 전후 추매 판단
- [ ] 삼성전자 셀온더뉴스 여부 모니터링
- [ ] 워치 44개 → 20개 이하 축소 검토
- [ ] 포트 등급-비중 반비례 문제 구조조정
- [ ] 이란 2주 휴전 → 레짐 디바운스 3거래일 확인 (4/8=1일차)

---

## ✅ 완료 (2026-04-09 ~ 04-10)

### 봇 도구/기능
- [x] **웹 대시보드 추가** — `/dash` + `/dash/file/{filename}` (다크모드, 모바일 반응형)
  - 포트폴리오/감시종목/투자판단/매매기록/이벤트 테이블 렌더링
  - data/ 폴더 .md/.txt/.json 파일 렌더링 (보안: .py/.env 차단, ../ 차단)
  - markdown→HTML 변환 (체크박스/테이블/코드블록/볼드/링크)

### get_regime 전면 재설계 (11지표 → 2지표)
- [x] **S&P 500 200MA + VIX 2개 지표 조건부 로직**
  - 🟢 Offensive: S&P > 200MA +3% AND VIX < 20 AND SMA200 rising
  - 🔴 Crisis: S&P < 200MA -3% AND (VIX > 30 OR VIX 백워데이션)
  - 🟡 Neutral: 그 외
- [x] 디바운스 (🟢 5일, 🔴 3일, 🟢→🟡 즉시, 🔴→🟡 VIX<25 또는 S&P>200MA-3%)
- [x] VIX 트랜치 레벨 (🔴 내부 1~3 단계)
- [x] USD/KRW 참고용 indicator (레짐 판정 미사용)
- [x] 점수 시스템 / KR 지표 / Turbulence 폐기
- [x] regime_state.json 새 포맷

### 리포트 크롤러 개선
- [x] HD한국조선해양(009540) 미수집 버그 수정
- [x] 한경컨센서스 크롤링 추가 (네이버 + 한경 통합)
- [x] _MAX_PER_TICKER 5 → 10
- [x] 중복 제거 키 단순화 (date, source, ticker) — 한경 제목 공백 누락 대응
- [x] 한경 우선 정렬 (메타데이터 풍부)
- [x] 와이즈리포트 분석 (PDF 유료, JSON 메타데이터만 가능 → 보류)

### 봇 버그 수정
- [x] mcp_tools.py load_krx_db import 에러 수정 (로컬 import가 상위 가림)
- [x] 백필 스크립트 맥미니 로컬 실행 (Safari KRX + KRX OPEN API)
- [x] launchd com.stock-bot.krx-keepalive (25분마다 KRX 세션 연장 자동 클릭)

---

## ✅ 완료 (2026-04-08)

### 맥미니 서버 이전
- [x] 맥미니 M4 서버 이전 + Cloudflare Tunnel + 도메인 (arcbot-server.org)
- [x] 데이터 44개 워치 복원
- [x] 봇 자동시작 launchd 등록
- [x] MCP 커넥터 변경

### 봇 도구/플러그인
- [x] Codex 플러그인 + Agent Team 구조
- [x] read_file / write_file / list_files MCP 도구 (27→30개)
- [x] get_change_scan MCP 도구 (12개 프리셋, 임계값 파라미터화)

### 봇 버그 수정
- [x] 미국 $0.00 버그 (NYS/NAS/AMS fallback)
- [x] 모멘텀경고 16:30 이동 + 추정수급
- [x] credit/lending/after_hours 필드 매핑
- [x] 토큰 캐싱 + Gist 백업 버그
- [x] DART API 스크리너 정상 확인
- [x] 코드 최적화 audit 반영 확인

### KRX DB v2 전종목 데이터 수집 시스템
- [x] 설계서 (data/krx_db_design.md, 62개 필드)
- [x] KRX OPEN API 시세 + Safari 세션 돌파 (PER/PBR/수급/공매도/외인보유/신용/대차)
- [x] FnGuide 컨센서스 전종목 509종목
- [x] 기술적 지표 (MA5/10/20/60/120/200, RSI14, 볼린저)
- [x] 추세 점수 다구간 (5d/20d/60d)
- [x] 매물대 2구간 (VP 60d/250d)
- [x] 섹터 상대강도 + 52주 위치 + YTD + earnings_gap
- [x] 보관 무제한, 백필 스크립트 작성

### 텔레그램 알림 강화 🆕
- [x] 포트 건강 체크 (15:40 장마감 요약에 규칙 위반 추가)
- [x] 워치 변화 감지 (19:00 평일, 53개 종목만)
- [x] 레짐 전환 가이드 (전환 확정 시 행동 안내 1회)
- [x] 감시가 터치 브리핑 (가격 트리거 + 레짐/현금/이벤트 포함)
- [x] Sunday 30 리마인더 (일요일 19:00, 체크리스트 포함)

### 문서화
- [x] data/bot_guide.md — 30개 도구 용도별 분류
- [x] data/bot_scenarios.md — 7가지 활용 시나리오
- [x] data/bot_reference.txt — 30개 도구 상세 파라미터 레퍼런스
- [x] data/krx_db_design.md — KRX DB 설계서
- [x] CLAUDE.md 맥미니 환경 업데이트
- [x] TODO.md 전면 업데이트

### 기타
- [x] KRX OPEN API 승인 확인 (8개 서비스)
- [x] GitHub Token repo 권한 추가 + push
- [x] TSLA/LITE 딥서치
- [x] 일일점검 + 종합 리서치

---

## 알림 스케줄 전체

| 주기 | 시간 | 알림 |
|------|------|------|
| 10분 | 수시 | 손절/감시가 체크 + 브리핑 |
| 30분 | 수시 | 이상 신호 + DART 공시 |
| 1시간 | 수시 | 레짐 전환 체크 |
| 매일 | 15:40 | 장마감 요약 + 포트 건강 |
| 매일 | 16:30 | 모멘텀 경고 |
| 매일 | 19:00 | 워치 변화 감지 |
| 매일 | 06:00 | 미국 장마감 요약 |
| 일요일 | 19:00 | Sunday 30 리마인더 |

---

## 구현 시 주의사항
1. 맥미니 로컬 data/ (DATA_DIR 환경변수)
2. KIS API 초당 20회 제한 (sleep 0.3)
3. KRX 크롤링: Safari 세션 유지 필수
4. MCP 도구 추가 시 mcp_tools.py 스키마 등록 필수
5. Agent Team: architect(Opus) → dev(Sonnet) → reviewer(Codex)
6. 데이터 보관 무제한
