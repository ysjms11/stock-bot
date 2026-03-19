# 주식봇 개선 TODO
> 업데이트: 2026-03-19 | 레포: ysjms11/stock-bot | Railway: chic-ambition


---

## ✅ 완료된 항목
- [x] 미국 주식 현재가 조회 (get_stock_detail 통합)
- [x] 미국 포트폴리오 등락률 반영
- [x] 미보유 종목 가격 감시 (set_alert buy_price)
- [x] 일봉 데이터 조회 (get_stock_detail period 파라미터)
- [x] decision_log 저장/조회 (set_alert log_type=decision)
- [x] compare_snapshot (set_alert log_type=compare)
- [x] #11 복합 신호 분류 개선 — 보유종목 익절/손절/추세 분류 + 일일 중복 방지 (2026-03-19)
- [x] #14 매크로 대시보드 — VIX/WTI/금/구리/DXY/US10Y/외인수급/이벤트 수집, 매일 18:00+06:00 자동발송, get_macro(mode=dashboard) MCP 지원 (2026-03-19)
- [x] #4 외국인 순매수 + scan_market 통합 — scan 결과에 frgn_ntby_qty + [외인매수] 태그 (2026-03-19)
- [x] #7 DART 공시 중요도 태깅 — [긴급]/[주의]/[참고] 키워드 확장 + 제목 앞 태그 표시 (2026-03-19)
- [x] #6 섹터 ETF 시세 조회 — get_macro(mode='sector_etf') 8개 ETF 현재가·등락률 (2026-03-19)
- [x] #12 이평선 수렴 스크리너 — convergence/convergence2 분할 스캔 (110/111종목) (2026-03-19)
- [x] #13 영업이익 증가율 스크리너 — op_growth + op_turnaround + 매출/이익률 필드 (2026-03-19)
- [x] #16 배치 스캔 인프라 — get_stock_universe() + batch_fetch() + kis_daily_closes(), stock_universe.json 221종목 (2026-03-19)
- [x] 한국 장마감 요약 개선 — 섹터ETF/포트변동/목표갭/감시접근 추가 (15:40) (2026-03-19)
- [x] 미국 장마감 요약 신규 — S&P/나스닥/보유종목/손절경고/감시접근 (06:00) (2026-03-19)
- [x] 수급 이탈 경고 — 외인 3일 연속 순매도 시 자동 경고 (2026-03-19)
- [x] 주간 손실 한도 경고 — -3%/-4% 자동 경고 (weekly_base.json 기준) (2026-03-19)
- [x] 감시 알림 파일 기반 중복 방지 — watch_sent.json (배포 후에도 유지) (2026-03-19)
- [x] 전체 알림 주말/장외 시간 체크 추가 — _is_kr_trading_time() 헬퍼 (2026-03-19)
- [x] 환율 알림 비활성화 — 매크로 대시보드로 통합 (2026-03-19)
- [x] 매도 규칙 전면 개정 — 평단 기준 금지 (2026-03-19)

---

## 🔄 진행중 / 디버깅 필요

- [ ] **DART API 분기별 영업이익 스크리너** — dart_corp_map.json 생성 완료 (211종목), dart_op_growth/dart_turnaround 구현 완료. MCP 레벨 에러 디버깅 중.
- [ ] **#18 GitHub API 연동** (P2) — Claude가 MCP 도구로 직접 파일 수정+커밋+push

---

## 🟢 P2 — 다음 달

### 17. 모멘텀 종료 자동 감지
**영향도:** ★★★★★ | **난이도:** ★★★

```
보유 종목 섹터별 5가지 조건 자동 체크:
1. 외인/기관 3일 연속 순매도
2. 대장주 고점 대비 -10%
3. 거래량 20일 평균 대비 50% 이하
4. 동일 섹터 신고가 종목 수 감소
5. (뉴스 기반은 수동)

→ 2개 이상 해당: "⚠️ 모멘텀 종료 의심" 알림
```

### 15. get_consensus — 증권사 컨센서스 자동 수집
**영향도:** ★★★★☆ | **난이도:** ★★★★

```
데이터 소스: 네이버증권 또는 FnGuide 크롤링
기능: 감시/보유 종목의 증권사 목표가 자동 수집
알림: 목표가 상향/하향 시 텔레그램 알림
```

### 10. 텔레그램 /summary 고도화
- 확신등급 변동 표시 (이전 점검 대비)
- 포트 비중 변화
- 당일 중요 공시 하이라이트

### 18. GitHub API 연동
**영향도:** ★★★★☆ | **난이도:** ★★★

```
목표: Claude가 MCP 도구로 직접 TODO.md/코드 파일 수정 + 커밋 + push 가능하게
- Railway 환경변수에 GitHub PAT (GITHUB_PAT) 저장
- update_file MCP 도구 추가: path/content/message 인자로 파일 수정+커밋
- Claude가 코드 변경 후 바로 배포까지 자율 완결 가능
```

---

## 구현 우선순위 요약

```
진행중:
  DART API 스크리너 디버깅

다음 달 (P2):
  17. 모멘텀 종료 감지
  15. get_consensus
  10. summary 고도화
  18. GitHub API 연동 (Claude 자율 커밋/push)
```

---

## 구현 시 주의사항

1. **Railway Volume:** JSON 파일이 재배포 시 리셋됨.
   GitHub Gist 백업 또는 환경변수 저장 검토.

2. **KIS API 호출 제한:** 초당 20회.
   배치 스캔 시 sleep(0.05) 필요. 200종목 = 최소 10초.

3. **미국 시장 시간대:** 한국시간 밤 11:30~새벽 6:00.
   미국 종목 실시간 데이터는 장중에만 유효.

4. **MCP 도구 등록:** 새 함수 추가 시 mcp_tools.py에
   스키마 등록 필수. Claude가 호출하려면 정확해야 함.

5. **DART API:** dart_corp_map.json은 레포에 커밋되어 있음 (211종목).
   업데이트 필요 시 로컬에서 build_dart_corp_map() 실행 후 재커밋.
