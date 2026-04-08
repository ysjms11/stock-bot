# 주식봇 개선 TODO
> 업데이트: 2026-04-08 | 레포: ysjms11/stock-bot | 서버: 맥미니 M4 + Cloudflare Tunnel (arcbot-server.org)

---

## 🔄 진행중 / PENDING

### 봇 개발
- [ ] KRX OPEN API 전환 (8개 서비스 승인완료 4/6, 코드 전환 필요)
- [ ] Railway 완전 삭제
- [ ] GitHub API 연동 — Claude MCP로 직접 커밋/push (P2)

### 투자 PENDING
- [ ] CRSP -29.6% → 25% 강제재평가 (thesis 재평가 필수)
- [ ] HD조선 51.8% 비중 축소 검토
- [ ] 촉매 발생 시 감시가 재설정 룰 확정 (RR 역산 기반)
- [ ] 레짐 🟡 전환 시 A등급 감시가 재평가 (NVDA/LITE)
- [ ] LITE 5/5 Q3 실적 전후 추매 판단
- [ ] 삼성전자 셀온더뉴스 여부 모니터링
- [ ] 워치 44개 → 20개 이하 축소 검토
- [ ] 포트 등급-비중 반비례 문제 구조조정

---

## ✅ 완료 (2026-04-08)
- [x] 맥미니 M4 서버 이전 + Cloudflare Tunnel + 도메인 (arcbot-server.org)
- [x] 데이터 44개 워치 복원 (Railway → 맥미니)
- [x] Codex 플러그인 설치 + Agent Team 구조 확립
- [x] read_file / write_file / list_files MCP 도구 추가
- [x] CLAUDE.md 맥미니 환경 업데이트
- [x] 미국 $0.00 버그 수정 (NYS/NAS/AMS fallback)
- [x] 모멘텀경고 16:30 KST 이동 + 추정수급 포함
- [x] 토큰 캐싱 파일 저장 (token_cache.json)
- [x] Gist 백업 빈 데이터 스킵 로직
- [x] 봇 자동시작 launchd 등록
- [x] get_regime 10Y-3M 금리차 확인
- [x] TSLA/LITE 딥서치
- [x] credit/lending/after_hours 필드 매핑 수정
- [x] DART API 스크리너 정상 동작 확인
- [x] 코드 최적화 audit 반영 확인

## ✅ 완료 (2026-03-19 이전)
- [x] 미국 주식 현재가 조회 + 포트폴리오 등락률
- [x] 미보유 종목 가격 감시 + 일봉 데이터
- [x] decision_log / compare_snapshot 저장
- [x] 매크로 대시보드 + 섹터 ETF
- [x] 이평선 수렴/영업이익 스크리너
- [x] 장마감 요약 (한국 15:40 / 미국 06:00)
- [x] 수급 이탈 경고 + 주간 손실 한도
- [x] 모멘텀 종료 자동 감지 + get_consensus

---

## 구현 시 주의사항
1. 맥미니 로컬 data/ (DATA_DIR 환경변수)
2. KIS API 초당 20회 제한 (sleep 0.3)
3. 미국 장시간: 한국 23:30~06:00
4. MCP 도구 추가 시 mcp_tools.py 스키마 등록 필수
5. Agent Team: architect(Opus) → python-developer(Sonnet) → kis-api-specialist(Sonnet) → test-writer(Sonnet) → code-reviewer(Codex)
