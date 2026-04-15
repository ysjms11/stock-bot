# 세션 인수인계 — stock-bot

> **매 세션 시작 시 가장 먼저 이 파일을 읽을 것.**
> 패턴 출처: Anthropic "Effective harnesses for long-running agents" (claude-progress.txt 구조)

---

## 🔴 다음 세션에서 바로 할 일

**우선순위 순:**

1. **맥미니 서버 반영** — 오늘 커밋 7개 적용 필요
   ```bash
   cd ~/stock-bot && git pull
   # 좀비 launchd가 원격에 있으면 제거:
   launchctl unload ~/Library/LaunchAgents/com.stock-bot.krx-update.plist 2>/dev/null
   launchctl unload ~/Library/LaunchAgents/com.stock-bot.krx-keepalive.plist 2>/dev/null
   rm -f ~/Library/LaunchAgents/com.stock-bot.krx-{update,keepalive}.plist
   launchctl kickstart -k gui/501/com.stock-bot.main  # 봇 재시작
   ```

2. **프리셋 복구 후속 검증** — foreign_accumulation + credit_unwind 코드 반영 완료 (2026-04-16). 맥미니 배포 후 ~7일 수집 쌓이면 실제 스캔 결과 확인 필요. `short_squeeze`는 ~5/14 자동 작동.

3. **내부자 클러스터 첫 실행 결과 확인** — 4/16 20:00 KST 이후 텔레그램 알림 체크

---

## 🟡 이번 주 할 일 (우선순위 중)

- **워치리스트 단일화** (TODO_dev.md P1): `watchlist.json` + `us_watchlist.json` + `watchalert.json` → `watchalert.json` 단일 소스. 현재 26종목 불일치. 반나절 작업.
- **Oracle Cloud VM 처리**: 4/15 Stop 완료. 4/16 이후 중복 알림 없으면 Terminate.

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

---

## 🧠 최근 세션 학습 (Lessons learned)

1. **API 응답 필드는 전수 검토할 것** — `whol_loan_rmnd_rate` 이미 Phase 1에 있었는데 모르고 Safari fetch 만듦. 오판이 구조적 결정까지 끌고 감.
2. **"죽은 코드" 판단 전 데이터 성숙도 체크** — short_squeeze는 코드 정상, 과거 데이터 0이라 일시적으로 빈 결과일 뿐이었음.
3. **사용자 지적 신뢰** — "KRX Safari 대체됐던 거 같은데"라는 기억이 정확했고, 재검증으로 2,357줄 청소로 이어짐.
4. **팀 구조 원칙 지키기** — Opus가 직접 구현 안 하고 Sonnet 에이전트에 위임. 코드 수정은 python-developer.

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
