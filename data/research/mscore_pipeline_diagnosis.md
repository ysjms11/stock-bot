# M-Score 파이프라인 진단 + 복구 Plan (Ralph iter 13)

생성일: 2026-05-23
대상: `daily_snapshot.mscore` 100% NULL 문제 / Phase 1c "f_m_fcf_triple_top" fallback 원인

---

## 1. 현재 상태 (TL;DR)

| 항목 | 수치 |
|------|------|
| `daily_snapshot.mscore` (최신 trade_date 2864 rows) | **0건 filled** |
| `daily_snapshot.fscore` (비교) | 1517 / 2864 (53%) |
| `financial_quarterly` 전체 행 | 75,486 |
| `receivables` filled (전체) | 4,323 (5.7%) |
| `sga` filled (전체) | 4,027 (5.3%) |
| `depreciation` filled (전체) | 1,096 (1.5%) ← **최대 병목** |
| 2분기 연속 receivables 보유 종목 | 573 |
| 2분기 연속 sga 보유 종목 | 530 |
| 2분기 연속 depreciation 보유 종목 | 152 |
| **2분기 연속 3필드 ALL 보유 종목** | **118 / 2,742** (4.3%) |

> M-Score 8 컴포넌트 중 DSRI/SGAI/DEPI 가 `t`/`t-1` 두 분기 데이터 필요. 1개 분기라도 결손이면 `core_keys` 가드 (`db_collector.py:3171`) 통과 못해 `mscore=None`.

**최신 분기별 receivables/sga/depreciation 3필드 ALL 보유율:**
- 202603 (1Q26): 192 / 1,202 (16.0%)
- 202512 (4Q25): 121 / 2,695 (4.5%)
- 202509 (3Q25): 89 / 2,653 (3.4%)
- 202506 (2Q25): 86 / 2,680 (3.2%)

즉, **상장 종목 ~2,700개 중 4-16%만 M-Score 계산 가능 데이터셋 보유**. 이 중에서도 2분기 연속 가능한 종목은 더 줄어 ~118개 (4.3%).

---

## 2. 원인 분석

### 2.1. 계산 함수는 정상 작동 (코드 OK)
- `db_collector.py:3003` `_compute_mscore()` 함수 존재, Beneish 공식 정확히 구현
- TATA 결손 partial 7-variable fallback 포함 (5/9 fix)
- `update_all_alpha_metrics()` 가 daily_collect/dart_incremental 후 호출됨

### 2.2. 진짜 원인 = DART 파서 결손 (depreciation)

`kis_api.py:4040~` `dart_quarterly_full()` 파서는 **CF (현금흐름표) section** 에서 `'감가상각'` / `'무형자산상각'` 키워드로 감가상각 추출. 문제:

1. **분기보고서 CF 결손**: 한국 1·3분기 약식 보고서는 CF 간이공시. 많은 중소형주가 감가상각 항목 누락
2. **계정명 변종 미커버**: '감가상각비와무형자산상각비', '감가상각비-유형', '유무형감가상각' 등 변종 다수
3. **OFS(별도) 우선 우회 시 더 결손**: CFS(연결) 없으면 OFS(별도) 시도 — OFS는 CF 자체 미공시 종목 더 많음

`fs_source` 별 fill rate:
- `OFS` rows 3,048 → depreciation 753 (24.7%)
- `CFS` rows 1,637 → depreciation 343 (21.0%)
- `NULL` (구파서 시절) rows 70,801 → depreciation 0 (0%) ← **대다수**

### 2.3. 백필 지평선 (Backfill horizon)

`fs_source IS NULL` 인 70,801 rows = Phase 4 DART 파서 업그레이드 이전 수집된 데이터.
파서 업그레이드 후에도 2026-04~05 사이 수집된 ~5,200 rows만 새 파서 사용.
**202503 이전 분기는 백필 미실행 → 영구 결손 상태**.

### 2.4. `update_all_alpha_metrics()` 의 `per_ticker_mode` 가드

`db_collector.py:3401` 조회는 `fs_source IS NOT NULL` 조건만 필터. M-Score 계산은 추가로 **이전 분기에도 fs_source 필요** (없으면 prev 데이터 못 가져옴) → 함수 진입은 하지만 `_compute_mscore` 내부에서 None 반환.

---

## 3. 복구 Plan (3 Phase + 단순 우회)

### Phase A: DART 파서 키워드 보강 (예상 1-2시간, DART 콜 0)

목표: 신규 수집되는 행의 depreciation 매칭률을 22% → 50%+로 끌어올림.

작업:
1. `kis_api.py:4040-4045` 의 감가상각 매칭 토큰 확장
   - 추가: `'감가상각비와무형자산상각비'`, `'유형자산감가상각'`, `'무형자산상각비'`, `'감가상각 및 무형자산상각'`
2. `sga` 키워드 변종 추가: `'판매관리비'`, `'매출원가외이외비용'` 일부
3. 단위 테스트 추가 (`test_dart_incremental.py`에 mock account 5종 추가)

**효과**: 신규 수집되는 ~5,200 rows의 fill rate 개선. 백필은 별도 필요.

### Phase B: 백필 (예상 DART 콜 비용 검토 필수)

대상: `receivables IS NULL OR depreciation IS NULL OR sga IS NULL AND fs_source IS NULL` 인 70,801 rows 중 유의미한 분기(t-1 필요)만.

권장 범위 축소:
- **2분기 연속 백필 (최신 2개 분기)**: 2,700 종목 × 2 분기 × 2 fs_div(CFS+OFS) = **~10,800 DART 콜**
- DART 일일 한도: 40,000콜. **27% 점유** — 1일 1회 야간 실행 가능
- 4분기 연속 (M-Score TTM 용도): 5,400 × 2 = 21,600 콜 (54% 점유)

**3분기 이상 과거 백필 비추**: 한도 압박 + 4Q26 들어가면 어차피 일부 종목 폐기

### Phase C: M-Score 재계산 + daily_snapshot UPDATE (예상 10분)

코드 변경 0건. Phase A/B 완료 후:
```
python -c "from db_collector import update_all_alpha_metrics; \
           print(update_all_alpha_metrics())"
```

**예상 결과**:
- Phase A만: 종목 ~200~300 mscore 계산 (7-10%)
- Phase A+B: ~600~800 mscore 계산 (22-30%)
- 전 종목 80%+ 도달은 6개월 자연 누적 필요 (분기마다 신규 1,000건씩 쌓임)

---

## 4. 단순 우회 옵션 (코드/콜 비용 최소)

### 옵션 A: 부분 컴포넌트 M-Score (추천)
Beneish 8개 중 **SGI(매출성장률) + LVGI(레버리지) 만으로 약식 점수** 계산.
- `revenue`, `total_assets`, `total_liab` 만 필요 → 이미 90%+ filled
- 정확도 낮으나 "조작 의심 여부" 시그널은 유의미

**구현**: `_compute_mscore` 에 `result["mscore_lite"]` 추가 (2-variable approximate). 30분 작업.

### 옵션 B: FnGuide 컨센서스 백업
`consensus_cache.json`에 일부 종목 receivables/depreciation 있음 → 보강 데이터 source.
- 단점: 100~200종목 한정, 분기 1회 갱신

### 옵션 C: KIS `kis_estimate_perform()` (HHKST668300C0)
종목추정실적 API. 매출/영업이익만 가능, depreciation 없음 → **부적합**.

### 옵션 D: 그대로 두고 시그널만 강화
"f_m_fcf_triple_top" 대신 **"f_fcf_double_top"** 으로 시그널 이름 변경 (M 빠진 걸 명시). Phase 1c sql_signals 분류만 수정.
- 장점: 0 작업, 즉시 정직
- 단점: M-Score 영구히 dormant

---

## 5. 권장 의사결정

### 즉시 (이번 세션 가능, 코드 변경 X):
- **옵션 D 채택**: SQL 시그널에서 mscore 의존성 제거, "f_fcf_double" 로 rename
- `data/PROGRESS.md` 9번 task 상태 = "BLOCKED: DART 파서 + 백필 필요, DART 콜 27% 점유 (사용자 동의 필요)"

### 단기 (1-2주, 별도 세션):
- **Phase A** (파서 키워드 보강) → 위험 낮음, 즉시 신규 수집 개선
- 옵션 A (mscore_lite) 병행 도입 → 200+ 종목 즉시 시그널 생성

### 중기 (1개월+):
- **Phase B 백필** 사용자 명시 동의 후 실행
- DART 한도 27% 점유 1회 → 야간 03:00 실행 (다른 잡과 충돌 X 확인 필요)

---

## 6. 작업 안 함 — 제약 준수

본 진단은 **plan 작성 only**. 코드 수정 / DART 콜 / DB UPDATE 일체 없음.
CLAUDE.md 에이전트 팀 구조 준수: 실행은 architect → developer → reviewer → verifier 순으로 별도 세션에서.
