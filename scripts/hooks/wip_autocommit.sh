#!/bin/bash
# wip_autocommit.sh — PostToolUse(Edit|Write) 훅: 대형 리팩토링 세션용 WIP 자동 체크포인트
#
# 설계 (Aider의 'LLM 편집 즉시 git 영구 기록' 규율 백포트, 2026-06):
# - /rewind 체크포인트는 bash 가 변경한 파일(rm/mv/sed, DB 마이그레이션)을 추적 못함 →
#   git 커밋이 이중 안전망.
# - ⚠️ 브랜치 게이트: 브랜치명이 wip/* 또는 refactor/* 일 때만 작동. main·fix/* 등
#   일반 작업에서는 완전 no-op (main 직행 배포 운영이라 상시 자동커밋은 금지).
# - git add -u (추적 파일만): untracked 시크릿(.env.bak* 등) 절대 커밋 안 됨.
# - 사용법: 대형 리팩토링 시작 시 `git checkout -b refactor/<작업명>` 만 하면 켜짐.
#   끝나면 squash-merge 권장.
set -u

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
[ -n "$ROOT" ] && cd "$ROOT" || exit 0

BRANCH=$(git branch --show-current 2>/dev/null)
case "$BRANCH" in
  wip/*|refactor/*) ;;   # 게이트 통과
  *) exit 0 ;;           # 그 외 브랜치 = no-op
esac

# 추적 파일 변경 있을 때만 (untracked 제외 — 시크릿 보호)
git diff --quiet HEAD 2>/dev/null && exit 0

git add -u :/ 2>/dev/null
git commit -q -m "wip(claude): auto-checkpoint $(date '+%m/%d %H:%M:%S')" 2>/dev/null || true
exit 0
