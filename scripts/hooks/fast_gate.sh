#!/bin/bash
# fast_gate.sh — SubagentStop 훅: 서브에이전트 종료 시 변경된 .py 빠른 문법 게이트
#
# 설계 (2026-06, 고스타 레포 스틸맨 조사에서 채택):
# - pytest 풀스위트(~2분)를 훅에 걸면 모든 에이전트 종료마다 2분 비용 → 의도적으로
#   py_compile(서브초)로 다운그레이드. 풀 회귀는 verifier 에이전트가 담당 (CLAUDE.md 팀 룰).
# - exit 2 = 서브에이전트 종료 차단 + stderr 를 에이전트에게 피드백 → 자가수정 루프.
# - stop_hook_active 가드: 무한 루프 방지 (Claude Code 가 재진입 시 플래그 셋).
set -u

# stdin 훅 입력에서 재진입 가드 (간단 substring 매치로 충분)
INPUT=$(cat 2>/dev/null || true)
case "$INPUT" in
  *'"stop_hook_active":true'*|*'"stop_hook_active": true'*) exit 0 ;;
esac

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
[ -n "$ROOT" ] && cd "$ROOT" || exit 0

# 작업트리에서 변경된 .py (추적 파일 한정, 최대 50개 — 폭주 방지)
FILES=$(git diff --name-only HEAD -- '*.py' 2>/dev/null | head -50)
[ -z "$FILES" ] && exit 0

PY="$ROOT/venv/bin/python3"
[ -x "$PY" ] || PY=python3

FAIL=0
ERRS=""
while IFS= read -r f; do
  [ -f "$f" ] || continue
  OUT=$("$PY" -m py_compile "$f" 2>&1) || { FAIL=1; ERRS="${ERRS}
${f}: ${OUT}"; }
done <<< "$FILES"

if [ "$FAIL" -eq 1 ]; then
  echo "[fast-gate] py_compile 실패 — 종료 전 수정 필요:${ERRS}" >&2
  exit 2
fi
exit 0
