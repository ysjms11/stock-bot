# AMD — 매도/재매수 트리거 메모

> **상태**: 매도 완료 (포트 빠짐)
> **마지막 watchalert 메모**: 2026-04-28 갱신 후 5/5 watchalert 제거

---

## 부분매도 트리거 (재매수 후 적용)

- **$400 도달 시 1/3 정리 검토**
- 트리거 조건:
  - FwdPE 51x → 58x 멀티플 확장 (밸류에이션 부담)
  - "유포리아 4트리거" 확정 시 (시장 과열 신호)

## 재매수 후 시스템 등록 워크플로우

AMD 재매수 시:
1. portfolio.json `us_stocks.AMD` 추가
2. stoploss.json `us_stocks.AMD.target_price = 400` 등록 (목표가 = 부분매도 가격)
3. **watchalert에 buy_price로 등록 X** — buy_price는 매수 감시용. 매도 감시 의도면 stoploss/target 시스템 사용.

## 등급 기록

- 2026-04-28 시점 등급: B+
- 4/28 Q1 실적 후 재평가 대상 (Q3 DC $6B+ 시 A 검토)
