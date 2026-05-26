# PDF 다운로드 인프라 폴백 시스템 구축

> 작성일: 2026-05-04
> 작성자: Claude (058610 에스피지 딥서치 중 발견)
> 우선순위: 중 (분석 정확도 영향)
> **상태: INVALID — 2026-05-27 폐기 결정**
>
> **폐기 사유**: pdf_collectors.py 1,221라인 구현 완료 후 실제 성공률 0% 확인.
> 외부 broker 사이트(삼성증권/유진투자/미래에셋 등) 직접 URL이 로그인 세션 필요·
> IP 차단·CORS 등 호환성 한계로 실질적 수집 불가.
>
> **대안 (5/27 적용)**: 한경컨센서스 수집 범위 180일→365일 확장 + naver pstatic
> 매핑 캐시(data/naver_pdf_cache.json, 30일 TTL) 강화. 작동하는 경로에 집중.
>
> 이 문서는 학습 자료로 보존됨. 새 구현 시 참고 금지.

## 배경

058610 에스피지 풀 딥서치 KR_DEEPSEARCH v4 진행 중 PDF 게이트 단계에서 인프라 한계 발견.

### 현재 문제

- `manage_report` collect 결과: 24개 리포트 수집했으나 **23개 meta_only**, 1개만 success
- pdf_url은 거의 모두 `wisereport.co.kr`(유료) 단일 경로
- 실제 PDF 다운로드 성공률: **1/24 = 4.2%**
- 058610 핵심 리포트 5건 (한투 4/20, 다올 3/16, 하나 3/16, 삼성 3/11, DB 2/23) 모두 pdf_path="" 빈 값

### 영향

- KR_DEEPSEARCH 풀 딥서치 PDF 게이트 ("최소 2개+ PDF 직접 읽기" 강제) 종종 미충족
- 컨센서스 EPS·TP 산출 방식·Forward FCF 직접 검증 불가
- 교훈 #9 "컨센 avg 사용 금지 — 브로커별 EPS 개별 수치 필수" 강제 어려움

### 워크어라운드 (현재)

- Claude가 web_fetch로 직접 무료 사이트 PDF 다운로드 (samsungpop.com 등)
- 봇 자동화에서 빠짐, 매번 수동

## 확인된 무료 PDF 경로 (2026-05-04 검증)

| 증권사 | URL 패턴 | 검증 상태 |
|---|---|---|
| 삼성증권 | `samsungpop.com/common.do?cmd=down&fileName={path}` | ✅ web_fetch 성공 (058610 3/11) |
| 유진투자증권 | `eugenefn.com/common/files/amail/{date}_{ticker}_{analyst}_{id}.pdf` | ✅ 검색 결과 확인 |
| 미래에셋증권 | `securities.miraeasset.com/bbs/download/{attachmentId}.pdf` | ✅ 검색 결과 확인 |
| 하나증권 | `file.hanaw.com/download/research/FileServer/WEB/{path}` | ✅ 검색 결과 확인 (시황/전략) |
| DB금융투자 | `ssl.pstatic.net/imgstock/upload/research/company/{id}.pdf` | ✅ 네이버 호스팅 확인 |
| 네이버 증권 (다증권사) | `stock.pstatic.net/stock-research/company/{brk_cd}/{date}_company_{id}.pdf` | ⚠️ id 추측 어려움 |
| 와이즈리포트 (유료) | `wisereport.co.kr/comm/LoadReport.aspx?rpt_id={id}` | ❌ 인증 필요 |

## Claude Code 작업 프롬프트 (복붙용)

```
Stock-bot PDF 다운로드 인프라 개선 — 무료 폴백 시스템 구축

배경 파일: data/PDF_INFRA_UPGRADE.md 참조

3명 팀 구성:

Teammate 1 (Senior Backend):
- 신규 파일: data/pdf_collectors.py
- 증권사별 collector 모듈 분리. 각 모듈 인터페이스: fetch_pdf(ticker, date, title) -> bytes | None
- 5개 무료 경로 우선 구현 (samsungpop, eugenefn, miraeasset, hanaw, dbfi)
- manage_report.py 수정: 1순위 무료 → 2순위 네이버 증권 → 3순위 와이즈리포트 폴백
- 첫 성공 시 종료, source_used 필드 기록

Teammate 2 (Test Engineer):
- 신규 파일: tests/test_pdf_collectors.py
- 058610 에스피지 회귀 테스트:
  * 한투 4/20, 다올 3/16, 하나 3/16, 삼성 3/11, DB 2/23 (5건)
  * 목표 성공률: 기존 1/24 (4.2%) → 5/24 (20.8%) 이상
- 010120 LS ELECTRIC, 009540 HD조선해양 추가 검증
- pytest 기반 자동화

Teammate 3 (Architect):
- DB schema 변경: extraction_status에 source_used 필드 추가
  * "samsungpop_direct", "eugenefn_direct", "wisereport_paid", "naver_research" 등
- get_consensus 통합: 가용한 모든 PDF 경로 시도 후 broker별 TP·EPS 산출
- bot_guide.md (또는 CLAUDE.md) 업데이트: 신규 폴백 사용법

테스트 케이스 (수락 기준):
1. 058610 manage_report collect 재실행 시 5건 이상 success로 전환
2. 058610 read_report_pdf(report_id=7088, 한투) 직접 호출 성공 (현재 "PDF 경로 없음" 에러)
3. 058610 read_report_pdf(report_id=7089, 다올) 직접 호출 성공
4. wisereport_only 라벨이 명시적으로 표시되어 사용자가 제한 인식 가능

비기능 요건:
- 각 collector 30초 타임아웃
- 실패 시 다음 폴백으로 즉시 전환 (재시도 X)
- requests 세션 재사용 (성능)
- robots.txt 존중

브랜치: feat/pdf-collectors-fallback
PR 메시지에 058610 회귀 테스트 결과 첨부 (before/after 성공률)
```

## 검증된 URL 샘플 (참고용)

```
삼성증권 058610 2026-03-11:
https://www.samsungpop.com/common.do?cmd=down&contentType=application/pdf&inlineYn=Y&saveKey=research.pdf&fileName=2010/2026031015471500K_02_06.pdf

유진투자증권 005930 2026-01-30:
https://www.eugenefn.com/common/files/amail//20260130_005930_sophie.yim_114.pdf

하나증권 시황 2026-04-27:
https://file.hanaw.com/download/research/FileServer/WEB/strategy/market/2026/04/24/lee_260427.pdf
```

## 추정 효과

- 058610 PDF 게이트 정식 충족: 부분(1건) → 정식(5건+)
- 전체 종목 PDF 자동화 성공률: 4.2% → 20%+ (보수적), 50%+ (목표)
- 매주 풀 딥서치 1-2건 진행 시 PDF 게이트 통과율 대폭 개선
- KR_DEEPSEARCH 교훈 #9 (컨센 avg 금지, 브로커별 개별 EPS) 강제 가능

## 관련 문서

- `data/KR_DEEPSEARCH.md` — PDF 게이트 강제 규칙
- `data/thesis/058610_에스피지.md` — PDF 게이트 부분충족 v1 → 정식충족 v2 사례
- `bot_guide.md` — manage_report·read_report_pdf 사용법

## 변경 이력

- 2026-05-04: 058610 에스피지 딥서치 중 PDF 게이트 한계 발견 → 폴백 시스템 PENDING 등록
