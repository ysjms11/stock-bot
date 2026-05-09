"""schedule.md ↔ main.py jq.run_daily 등록 cross-check 테스트.

학습 #13 (함수 작성 ↔ 스케줄 등록 분리) 3번 재현 후 5/9 도입.
CI 에서 fail 하면 신규 잡 등록 누락 또는 schedule.md 미갱신.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def collect_main_jobs() -> set:
    """main.py 의 jq.run_daily(... name="X") 에서 X 수집.

    한 라인 단위로 jq.run_daily/run_repeating 호출 식별 후 name= 추출.
    (regex `[^)]+` 는 dtime(...) 내부 `)` 에서 멈추므로 라인 기반이 견고).
    """
    src = (ROOT / "main.py").read_text()
    jobs = set()
    for line in src.splitlines():
        if "jq.run_daily" in line or "jq.run_repeating" in line:
            m = re.search(r'name="([^"]+)"', line)
            if m:
                jobs.add(m.group(1))
    return jobs


def collect_schedule_md_jobs() -> set:
    """.claude/rules/schedule.md 의 표에서 잡 이름 (백틱 wrap) 수집.

    표 컬럼 패턴: | 시간 | D | `잡_이름` | `함수명` | 설명 | 변경 |
    잡 이름은 3번째 컬럼 (시간/D 다음)에 위치.
    """
    md = (ROOT / ".claude" / "rules" / "schedule.md").read_text()
    jobs = set()
    for line in md.splitlines():
        # 표 행만 (| 로 시작)
        if not line.lstrip().startswith("|"):
            continue
        cols = [c.strip() for c in line.split("|")]
        # 백틱 잡 이름은 보통 3번째 컬럼 (반복 잡: cols[2], 일일 잡: cols[3])
        # 안전하게 모든 컬럼 스캔
        for col in cols:
            # re.search 로 데코(★ 등) 허용 — 잡 이름은 백틱 wrap 만 보장
            m = re.search(r"`([a-z_0-9]+)`", col)
            if m:
                name = m.group(1)
                # 함수명/잡명 둘 다 추출되는데 main.py 등록값과 일치하는 것만 카운트
                jobs.add(name)
    return jobs


def test_schedule_registration_consistency():
    """등록된 잡 ↔ 문서화된 잡 일치 검증.

    schedule.md 의 잡 이름은 함수명/잡명 모두 포함될 수 있어
    schedule.md ⊆ main.py 인지 검증 (md 에 있는 이름이 main 에도 있어야 함).
    """
    main_jobs = collect_main_jobs()
    md_jobs = collect_schedule_md_jobs()

    # main.py 의 함수명도 부분 추출 시 흔들리므로,
    # schedule.md 의 잡 이름들 중 main.py 등록 name 과 매칭 안 되는 것 검증
    # → 학습 #13 패턴 (md 문서화는 됐는데 main 등록 누락) 차단

    # 함수 이름은 schedule.md 의 4번째 컬럼에 별도 백틱으로 들어가는데
    # 동일 추출되므로 main.py 의 async def 함수명도 모아서 화이트리스트 처리
    src = (ROOT / "main.py").read_text()
    func_names = set(re.findall(r"^async def (\w+)\(", src, re.MULTILINE))
    func_names |= set(re.findall(r"^def (\w+)\(", src, re.MULTILINE))

    # md_jobs 에서 함수명에 매칭되는 것은 함수 컬럼 (잡 이름 X) 이므로 제외
    md_job_names_only = md_jobs - func_names

    # 1. main.py 등록인데 md 미문서화 (함수명 또는 잡명 어느쪽에도 없음)
    undocumented = main_jobs - md_jobs
    # 2. md 문서화인데 main.py 미등록 (학습 #13 패턴)
    unregistered = md_job_names_only - main_jobs

    errors = []
    if unregistered:
        errors.append(
            f"schedule.md 문서화됐으나 main.py 등록 누락 (학습 #13): "
            f"{sorted(unregistered)}"
        )
    if undocumented:
        # warning 만 (함수명 컬럼과의 추출 모호성 때문)
        print(
            f"WARN: main.py 등록됐으나 schedule.md 문서화 누락: "
            f"{sorted(undocumented)}"
        )

    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)
    print(
        f"PASS: main.py {len(main_jobs)} 잡 등록 + schedule.md "
        f"{len(md_job_names_only)} 잡 문서화 일치"
    )


if __name__ == "__main__":
    test_schedule_registration_consistency()
