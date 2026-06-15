#!/bin/bash
# star_forensics.sh — GitHub 레포 5분 포렌식: 스타가 진짜 엔지니어링인지 판별
#
# 배경 (2026-06 조사 실증): AI 에이전트 생태계에서 스타 절대값은 품질 신호가 아님 —
# 최대 스타 2개(opencode 172k=시류, claude-flow 58.7k=기능 97% 스텁)가 품질과 가장 괴리.
# 신뢰 서열: 이슈 클로즈율 > 기여자 분포(1인 집중=적신호) > 릴리스 케이던스 > 스타.
#
# 사용법: ./scripts/star_forensics.sh owner/repo
# GITHUB_TOKEN 있으면 rate limit 5000/h (없어도 60/h로 동작).
set -euo pipefail

REPO="${1:?사용법: star_forensics.sh owner/repo}"
AUTH=()
[ -n "${GITHUB_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")

# macOS 기본 bash 3.2: set -u 에서 빈 배열 "${AUTH[@]}" 가 unbound variable — 방어적 확장
api() { curl -s ${AUTH[@]+"${AUTH[@]}"} "https://api.github.com/$1"; }

echo "═══ $REPO 포렌식 ═══"
R=$(api "repos/$REPO")
echo "$R" | python3 -c "
import json,sys
from datetime import datetime, timezone
r=json.load(sys.stdin)
if 'message' in r and 'stargazers_count' not in r:
    print('  API 오류:', r['message']); sys.exit(1)
pushed=r.get('pushed_at','')
days=(datetime.now(timezone.utc)-datetime.fromisoformat(pushed.replace('Z','+00:00'))).days if pushed else '?'
print(f\"  ⭐ stars: {r['stargazers_count']:,}   forks: {r['forks_count']:,}\")
print(f\"  📅 last push: {pushed} ({days}일 전)   archived: {r.get('archived')}\")
print(f\"  🐛 open issues(+PR): {r['open_issues_count']:,}\")
"
echo ""
echo "── 기여자 분포 (1인+봇 집중 = 적신호) ──"
api "repos/$REPO/contributors?per_page=5" | python3 -c "
import json,sys
cs=json.load(sys.stdin)
if isinstance(cs,list):
    total=sum(c['contributions'] for c in cs)
    for c in cs[:5]:
        pct=c['contributions']*100//max(total,1)
        print(f\"  {c['login']:25s} {c['contributions']:6,} commits ({pct}% of top5)\")
"
echo ""
echo "── 릴리스 케이던스 ──"
api "repos/$REPO/releases?per_page=3" | python3 -c "
import json,sys
rs=json.load(sys.stdin)
if isinstance(rs,list) and rs:
    for r in rs[:3]: print(f\"  {r.get('tag_name','?'):15s} {r.get('published_at','?')}\")
else: print('  (릴리스 없음 — 태그/케이던스 부재 자체가 신호)')
"
echo ""
echo "── 판정 가이드 ──"
echo "  · push 90일+ 전 = 동면 | 기여자 1인 90%+ = 1인 하이프 위험 | 릴리스 규칙적 = 건강"
echo "  · 곡선 모양은 https://star-history.com/#$REPO 에서: 감쇠 없는 복리=유기, 수직 점프=캠페인"
