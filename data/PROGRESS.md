# 세션 인수인계 — stock-bot

> **매 세션 시작 시 가장 먼저 이 파일을 읽을 것.**
> 패턴 출처: Anthropic "Effective harnesses for long-running agents" (claude-progress.txt 구조)

---

## 🔴 다음 세션에서 바로 할 일

**우선순위 순:**

1. **DART 증분 수집 Phase6 모니터링** — 매일 02:00 KST `daily_dart_incremental` 스케줄 배포 완료 (2026-04-16). 첫 공시 발생일(분기 마감 + 45일 근처) 이후 텔레그램 알림으로 쿼터/수집 건수 확인. 평일 대다수 "공시 없음" → 조용히 skip. 분기별 피크일(5/15, 8/14, 11/14) 신규 ~800종목 수집 예상.

2. **프리셋 복구 데이터 누적 대기** — credit_unwind/foreign_accumulation 코드 배포 완료 (2026-04-16). ~7일 수집되면 실제 스캔 결과 확인. `short_squeeze`는 ~5/14 자동 작동.

3. **KR_DEEPSEARCH 실전 검증** — 10 Step 템플릿 + PDF 게이트 추가됨. 다음 한국 종목 딥서치 시 사용자가 직접 복붙하며 Step 누락 여부 / 킬 조건 체감 확인.

---

## 🟢 중장기 TODO (TODO_dev.md 참조)

- **P2 Tier 1 알파**: F-Score/M-Score, FCF 메트릭
- **P2.5 Tier 2**: 관세청 10일 수출, 거버넌스/밸류업
- **P3**: 뉴스 감성 개선, DB 변화 감지, 공시 실시간화

---

## 📌 주요 아키텍처 결정 (최근)

| 날짜 | 결정 | 이유 |
|------|------|------|
| 2026-04-15 | Railway 완전 삭제 | 중복 발송 원인 (매크로/DART 2회씩) |
| 2026-04-15 | 내부자 거래 `get_dart(mode='insider')` 추가 | 30일 3명+ 매수 알파 신호 |
| 2026-04-15 | 에이전트 3개 추가 (critic/verifier/debugger) | OMC 프롬프트 패턴 차용 |
| 2026-04-15 | KRX 레거시 대청소 (-2,357줄) | krx_update.py 좀비, Safari keepalive 좀비 |
| 2026-04-15 | CLAUDE.md 다이어트 275→146줄 | 매 세션 토큰 절약 |
| 2026-04-16 | Oracle VM Stop | 중복 발송 추가 원인 의심 |
| 2026-04-16 | Oracle VM Terminate | Stop 후 중복 알림 없음 확인, 완전 삭제 |
| 2026-04-16 | 워치리스트 단일화 (watchalert.json) | 3파일 파편화 26종목 불일치 해결, save/load 단일 경로 |
| 2026-04-16 | 배포 플로우: main 직행 | 1인 운영 봇, 브랜치/PR 생략 |
| 2026-04-16 | HANDOVER.md 폐기 | 1인 운영 + AI 페어, PROGRESS.md로 역할 일원화 |
| 2026-04-16 | 대시보드 thesis/ 폴더 노출 | 18개 Thesis 딥서치 문서 접근성 |
| 2026-04-16 | KR_DEEPSEARCH.md 신설 (10 Step + PDF 게이트) | US_DEEPSEARCH_v3와 대칭, Claude의 Step 생략 방지 |
| 2026-04-16 | F/M/FCF 알파 메트릭 4-Phase 구축 | TTM 기반 F-Score/M-Score/FCF, 12분기 DART 소급 26,584행, MCP get_alpha_metrics + 3 rank 추가 (커밋 9702a68→2ffa724) |
| 2026-04-17 | F/M/FCF 완전 가동 | shares_out 12분기 소급 24,310건 완료 + F-Score #7 보강 재계산. 전종목 F-Score 분포 정규(피크 4-5점), 우량 7+ 552종목(22%). 자동화 스케줄(ed5aa72). 다음: DART 증분 수집. |
| 2026-04-16 | F/M/FCF Phase6 DART 증분 자동화 | `search_dart_periodic_new` (list.json pblntf_ty=A, 정정공시 skip) + `collect_financial_on_disclosure` (중복체크, max_calls=1000 안전장치, _DART_INTERVAL 0.067) + main 02:00 daily 스케줄. 13 pytest 모두 pass (mock only, 실호출 0). |
| 2026-04-17 | Step 5 딥서치 수급 파이프라인 복구 | `kis_investor_trend_history` output1(현재가 dict)→output2(일별 수급 list) 근본 버그 수정 + today 빈응답 시 yesterday fallback. 부가 효과: `collect_daily` Phase3이 같은 함수 쓰므로 daily_snapshot의 4/8 이후 수급값 0 버그도 자동 복구. 4/13~4/16 11,444건 백필 + foreign_trend 캐시 재계산 (커밋 5014239). |

---

## 🧠 최근 세션 학습 (Lessons learned)

1. **API 응답 필드는 전수 검토할 것** — `whol_loan_rmnd_rate` 이미 Phase 1에 있었는데 모르고 Safari fetch 만듦. 오판이 구조적 결정까지 끌고 감.
2. **"죽은 코드" 판단 전 데이터 성숙도 체크** — short_squeeze는 코드 정상, 과거 데이터 0이라 일시적으로 빈 결과일 뿐이었음.
3. **사용자 지적 신뢰** — "KRX Safari 대체됐던 거 같은데"라는 기억이 정확했고, 재검증으로 2,357줄 청소로 이어짐.
4. **팀 구조 원칙 지키기** — Opus가 직접 구현 안 하고 Sonnet 에이전트에 위임. 코드 수정은 python-developer.
5. **"맥미니 = 다른 서버" 편향 주의** — 워크트리가 `/Users/kreuzer/stock-bot/.claude/worktrees/` 아래라 본체가 맥미니 자체임을 잊고 "배포 필요"라 말함. 사용자가 "니가 맥미니야"로 교정.
6. **문서는 복붙 템플릿 + 킬 조건 없으면 Step 생략됨** — KR_DEEPSEARCH 초판은 설명문만 → Claude가 건너뜀. US 패턴(━━ STEP N ━━ 헤더 강제, 킬 조건, 체크박스) 차용으로 해결.
7. **리뷰 2중 체제의 가치** — code-reviewer + critic 병렬로 워치리스트 단일화 치명 6건(wrapper fallback, 직접참조, WebSocket 41건 초과) 캐치. 커밋 전에 막음.
8. **DART API 한도는 stockTotqySttus가 더 빡빡** — 4/16 fnlttSinglAcntAll 34k콜은 통과, 직후 stockTotqySttus 1k콜에서 status=020 (한도초과). 두 API가 다른 쿼터 풀을 쓰거나 stockTotqySttus가 별도 제한. 다음에 같은 일정으로 두 API 모두 돌리면 실패하니 분리.
9. **DART CF 직접법 회사는 감가상각 노출 안 됨** — 삼성/SK하이닉스/현대차 등 대형 직접법 채택사는 fnlttSinglAcntAll의 sj=CF에 "감가상각" 계정 없음. 결과: 22%만 채워짐 → M-Score DEPI 계산 불가. 별도 데이터 소스(FnGuide/주석) 없으면 구조적 한계.
10. **Python stdout 버퍼링 함정** — nohup + python3 -u 했는데도 print line buffering이 일정 시점 후 끊김. 장기 실행 모니터링은 DB 카운트 기반 polling이 더 신뢰. (Phase 1.5 백그라운드 80분 진행 중 로그 stuck 경험)

---

## 🛠 세션 시작 루틴

매 세션 시작 시 순서대로:

```bash
pwd                          # 1. 작업 디렉토리 확인
git log --oneline -10        # 2. 최근 커밋 훑기
cat data/PROGRESS.md         # 3. 이 파일 (다음 할 일)
cat data/TODO_dev.md         # 4. 봇 개발 TODO
cat data/TODO_invest.md      # 5. 투자 TODO (필요시)
```

이 루틴 후 사용자 요청 처리 시작.

---

## 📝 업데이트 규칙

- **세션 종료 직전**: "다음 세션에서 바로 할 일" 섹션 갱신
- **중요 결정 시**: "주요 아키텍처 결정" 표에 한 줄 추가
- **실수/교훈 발견 시**: "최근 세션 학습"에 한 줄 추가
- **작업 완료 시**: TODO_dev.md 체크 + 필요시 PROGRESS.md "다음 할 일"에서 제거
- **150줄 이하로 유지** — 오래된 결정/학습은 주기적으로 쳐낼 것
