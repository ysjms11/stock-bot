# FIX — KR 레짐 극단 우회(92%ile)에 방향 게이트 추가 (E 하이브리드 v2)

> 작성: 2026-07-17 Cowork 시스템 점검. 대상: `kis_api/regime.py` `calc_kr_regime()`.
> 심각도: **높음** — 실전 오판 발생 중.

## 1. 문제

`crisis_condition`의 92%ile 극단 우회가 **방향(추세)을 안 봄**:

```python
crisis_condition = (
    (vol_pct is not None and vol_pct > 80 and ma_dist is not None and ma_dist < -3)
    or (vol_pct is not None and vol_pct > 92)   # ← 방향 조건 없음
)
```

🔴의 설계 의도는 "폭락 공포에 실탄 발사"(REGIME_DESIGN §2)인데, 상방 멜트업의 극단 변동성도 🔴로 판정됨.

**실사례 (2026-07-16)**: vol_pct=96.8%ile, ma_dist=**+30.84%** (KOSPI 7284, 200MA 위 30%)
→ `🔴 공포 4일차 (확정) — 발사·풀투자 지향(현금 최소)`
→ 유포리아 고점에서 풀투자 지시. 같은 날 드로다운 모듈은 월간 -14.85% CRITICAL로 신규매수 중단 지시 — 정면 충돌.

**부수 버그**: neutral 분기가 `50 <= vol_pct <= 80`으로 상한이 막혀 있어, vol 80~92%ile & ma_dist -3~0% 구간이 crisis도 neutral도 아니어서 **🟢 Offensive로 낙하**함 (예: vol 85%ile, ma -1% → 🟢).

## 2. 수정 — 교체 코드 (paste-ready)

`calc_kr_regime()` 내 `# E 하이브리드 (백테스트 확정)...` 주석부터 `elif ... 50 <= vol_pct <= 80 ...` neutral 분기 끝까지를 아래로 교체:

```python
    # E 하이브리드 v2 (2026-07-17): 극단 우회에도 방향 게이트 추가.
    #   v1 결함: vol_pct>92 우회가 방향 무시 → 상방 멜트업(가격≫200MA)의 극단 변동성을
    #   폭락 공포로 오판, 유포리아 고점에서 🔴 발사(풀투자) 지시.
    #   실사례: 2026-07-16 vol_pct=96.8%ile & ma_dist=+30.84% → 🔴 오판 (4일 확정).
    #   원칙: 🔴 = "폭락 공포에 실탄 발사"(REGIME_DESIGN §2) → 하락 맥락(ma_dist<0) 필수.
    #   상방 극단 변동성(vol>80 & ma_dist>=0)은 과열 경계 → 🟡 (비축).
    #   부수 수정: neutral 분기 상한(<=80) 제거 → vol 80~92 & ma -3~0 이 🟢로 새던 갭 봉합.
    crisis_condition = (
        (vol_pct is not None and vol_pct > 80 and ma_dist is not None and ma_dist < -3)
        or (vol_pct is not None and vol_pct > 92
            and ma_dist is not None and ma_dist < 0)
    )
    overheat = (
        not crisis_condition
        and vol_pct is not None and vol_pct > 80
        and ma_dist is not None and ma_dist >= 0
    )
    if crisis_condition:
        regime_en = "crisis"
        if vol_pct is not None and vol_pct > 92:
            logic_parts.append(f"{pct_str} > 92%ile & ma_dist={ma_dist:+.2f}% < 0 (극단 우회·하락 확인) → 🔴 Crisis (발사)")
        else:
            logic_parts.append(f"{pct_str} > 80%ile & 200MA {ma_dist:.2f}% < -3% (추세게이트) → 🔴 Crisis (발사)")
        if confirmations:
            logic_parts.append(f"확인: {confirmations}")
    elif overheat:
        regime_en = "neutral"
        logic_parts.append(f"{pct_str} > 80%ile & ma_dist={ma_dist:+.2f}% >= 0 (상방 멜트업 과열) → 🟡 Neutral (경계·비축)")
    elif (vol_pct is not None and vol_pct >= 50) or (ma_dist is not None and ma_dist < -5):
        regime_en = "neutral"
        logic_parts.append(f"{pct_str} >= 50%ile 또는 ma_dist={ma_dist}")
        logic_parts.append("→ 🟡 Neutral")
```

(else → offensive 분기는 기존 그대로.)

## 3. 판정 변화 매트릭스

| vol_pct | ma_dist | 구(v1) | 신(v2) |
|---|---|---|---|
| 96.8 | +30.8 (현재) | 🔴 발사 | 🟡 과열 경계 |
| 95 | -5 (폭락) | 🔴 | 🔴 (유지) |
| 85 | -4 | 🔴 | 🔴 (유지) |
| 85 | -1 | 🟢 (갭 버그) | 🟡 |
| 93 | ma_dist None | 🔴 | 🟡 (보수 폴백) |
| 30 | +5 | 🟢 | 🟢 (유지) |

- 2008/2020형 지속 폭락(가격 < 200MA)은 전부 v1과 동일하게 🔴 유지 — 백테스트 적중 케이스 훼손 없음. 바뀌는 것은 "상방" 케이스뿐.
- 적용 후 KR: 다음 스케줄 계산에서 neutral 산출 → 디바운스 임계 1일이라 즉시 🟡 확정.

## 4. 테스트 추가 (test_regime.py 에 클래스 추가)

기존 테스트는 crisis_condition을 직접 검증하지 않음(사각지대). 아래 추가:

```python
class TestKrCrisisDirectionGate(unittest.TestCase):
    """calc_kr_regime — 극단 vol 방향 게이트 (E 하이브리드 v2)."""

    def _run(self, vol_pct, ma_dist):
        import kis_api.regime as rg
        with patch.object(rg, "_fdr_closes", return_value=[100.0] * 300), \
             patch.object(rg, "_realized_vol_series", return_value=[10.0] * 260), \
             patch.object(rg, "_pct_rank", return_value=vol_pct), \
             patch.object(rg, "_dist_from_ma", return_value=ma_dist):
            return rg.calc_kr_regime()

    def test_meltup_extreme_vol_not_crisis(self):
        r = self._run(96.8, 30.84)          # 2026-07-16 실사례
        self.assertEqual(r["regime_en"], "neutral")
        self.assertIn("과열", r["logic"])

    def test_downside_extreme_vol_is_crisis(self):
        r = self._run(95.0, -5.0)
        self.assertEqual(r["regime_en"], "crisis")

    def test_trend_gate_crisis(self):
        r = self._run(85.0, -4.0)
        self.assertEqual(r["regime_en"], "crisis")

    def test_mid_vol_shallow_dip_is_neutral_not_offensive(self):
        r = self._run(85.0, -1.0)           # v1 갭 버그: 🟢로 새던 구간
        self.assertEqual(r["regime_en"], "neutral")

    def test_calm_uptrend_offensive(self):
        r = self._run(30.0, 5.0)
        self.assertEqual(r["regime_en"], "offensive")
```

주의: `_pct_rank`는 USD/KRW 확인지표 경로에서도 안 불림(그쪽은 산술 계산). `_fdr_closes` mock이 USD/KRW 호출에도 같은 300개 리스트를 반환하므로 usdkrw_chg60=0.0으로 무해.

## 5. 적용 절차

1. `kis_api/regime.py` §2 교체 적용
2. `test_regime.py` §4 클래스 추가
3. `pytest test_regime.py -q` — 신규 5개 포함 전체 green 확인
4. 봇 재시작 → `get_regime` 호출 → KR이 `🟡 (상방 멜트업 과열)` 로직 문구로 나오는지 확인
5. REGIME_DESIGN.md §2 판정 로직에 한 줄 추가: "극단 우회(>92%ile)는 ma_dist<0 동반 시에만 🔴. vol>80 & ma_dist>=0 은 과열 🟡."

## 6. 임시 조치 (코드 적용 전)

2026-07-17 Cowork에서 `get_regime mode=override market=kr regime=neutral` 실행됨.
override는 다음 current 계산에서 덮여 사라지고, v1 코드가 남아있으면 디바운스 3일 후 🔴 재확정됨 → **코드 수정이 근본 해결.**
