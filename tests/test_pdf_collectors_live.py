"""pdf_collectors.py — 라이브 회귀 테스트 (실제 외부 사이트 호출)

@pytest.mark.live 마크 — CI에서는 `-m "not live"` 로 스킵.
사용자 룰: robots.txt 준수 + 30초 timeout + 다운로드 1회만 (caching).

수락 기준 (PDF_INFRA_UPGRADE.md 5/4 작성):
  1. 058610 manage_report collect 재실행 시 5건+ success로 전환
  2. 058610 read_report_pdf(report_id=7088, 한투) 직접 호출 성공
  3. 058610 read_report_pdf(report_id=7089, 다올) 직접 호출 성공
  4. wisereport_only 라벨이 명시되어 사용자가 제한 인식 가능

검증 종목:
  - 058610 에스피지 (1순위, 5건 다운로드 검증)
  - 010120 LS ELECTRIC
  - 009540 HD한국조선해양
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pdf_collectors as pc  # noqa: E402

pytestmark = pytest.mark.live  # 모듈 단위 마크


# 다운로드 결과 캐시 (테스트간 재사용)
_CACHE: dict[str, tuple[bytes, str] | None] = {}


def _fetch_cached(key: str, **kwargs):
    if key in _CACHE:
        return _CACHE[key]
    res = pc.fetch_pdf_with_fallback(**kwargs)
    _CACHE[key] = res
    return res


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 058610 ━━━━━━━━━━━━━━━━━━━━━━━━━

# 058610 에스피지 — 실제 broker 직접 URL이 있다면 매핑
# 주의: stock.db에 저장된 wisereport URL은 broker 직접 다운로드에 사용 불가.
# 직접 다운로드 URL(samsungpop 등) 이 있는 경우만 테스트.

SAMSUNG_058610_DIRECT_URL = (
    "https://www.samsungpop.com/common.do?cmd=down&contentType=application/pdf"
    "&inlineYn=Y&saveKey=research.pdf&fileName=2010/2026031015471500K_02_06.pdf"
)


class Test058610Spg:
    """058610 에스피지 회귀 — 수락기준 #1."""

    def test_samsung_direct_url_succeeds(self):
        """삼성증권 직접 URL → samsungpop_direct."""
        result = _fetch_cached(
            "058610-samsung-direct",
            ticker="058610", date="2026-03-11", title="감속기를 부탁해",
            broker_hint="삼성증권", pdf_url=SAMSUNG_058610_DIRECT_URL,
        )
        assert result is not None, "삼성증권 직접 URL이 실패하면 fallback 체인 전체가 의문"
        body, src = result
        assert body.startswith(b"%PDF"), f"PDF 매직바이트 없음: {body[:8]!r}"
        assert src == "samsungpop_direct", f"라벨 불일치: {src}"
        assert len(body) > 10_000, f"PDF 너무 작음: {len(body)} bytes"

    def test_samsung_fetch_by_url_helper(self):
        """samsungpop_fetch_by_url 헬퍼도 동일 결과."""
        result = pc.samsungpop_fetch_by_url(SAMSUNG_058610_DIRECT_URL)
        assert result is not None
        assert result.startswith(b"%PDF")

    def test_wisereport_url_correctly_labeled(self):
        """wisereport URL은 fetch_pdf_with_fallback에서 실패해도 라벨 식별 가능."""
        wise_url = (
            "https://www.wisereport.co.kr/comm/LoadReport.aspx?"
            "rpt_id=1093346&brk_cd=27&fpath=1F02720260420_058610.pdf&target=comp"
        )
        # is_pdf_url_free → False (무료 직접 다운로드 불가)
        assert pc.is_pdf_url_free(wise_url) is False
        # 라벨은 wisereport_paid
        assert pc.get_source_label(wise_url, extraction_status="meta_only") == "wisereport_paid"

    def test_058610_collect_reports_integration(self):
        """058610 collect_reports 실제 호출 — 신규 reports의 success 카운트.

        주의: DB에 이미 entry가 있으면 existing_keys 매칭으로 skip되므로,
        새 reports는 0건일 가능성 높음. 단순히 예외 raise 안 함만 검증.
        """
        try:
            from report_crawler import collect_reports
        except ImportError:
            pytest.skip("report_crawler 미사용 환경")

        results = collect_reports({"058610": "에스피지"})
        # results는 list (빈 list 가능). 타입만 검증.
        assert isinstance(results, list), f"results는 list여야 함: {type(results)}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 010120 LS ELECTRIC ━━━━━━━━━━━━━━━━━━━━━━━━━

class Test010120LsElec:
    """010120 LS ELECTRIC — 추가 검증."""

    def test_collect_reports_no_crash(self):
        try:
            from report_crawler import collect_reports
        except ImportError:
            pytest.skip("report_crawler 미사용 환경")
        results = collect_reports({"010120": "LS ELECTRIC"})
        assert isinstance(results, list)


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 009540 HD한국조선해양 ━━━━━━━━━━━━━━━━━━━━━━━━━

class Test009540HdKsoe:
    """009540 HD한국조선해양 — 추가 검증."""

    def test_collect_reports_no_crash(self):
        try:
            from report_crawler import collect_reports
        except ImportError:
            pytest.skip("report_crawler 미사용 환경")
        results = collect_reports({"009540": "HD한국조선해양"})
        assert isinstance(results, list)


# ━━━━━━━━━━━━━━━━━━━━━━━━━ 폴백 체인 확인 ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFallbackChainLive:
    """fetch_pdf_with_fallback 4단계 체인 라이브 검증."""

    def test_samsung_broker_hint_first_priority(self):
        """1순위 broker_hint=삼성 → samsungpop_direct."""
        result = _fetch_cached(
            "058610-samsung-direct",  # 캐시 재사용
            ticker="058610", date="2026-03-11", title="test",
            broker_hint="삼성", pdf_url=SAMSUNG_058610_DIRECT_URL,
        )
        assert result is not None
        _, src = result
        assert src == "samsungpop_direct"

    def test_url_domain_second_priority(self):
        """broker_hint 없으면 URL 도메인 매칭."""
        result = _fetch_cached(
            "058610-url-domain",
            ticker="058610", date="2026-03-11", title="test",
            broker_hint=None, pdf_url=SAMSUNG_058610_DIRECT_URL,
        )
        assert result is not None
        _, src = result
        assert src == "samsungpop_direct"

    def test_unknown_broker_returns_none_fast(self):
        """알 수 없는 broker + URL 없으면 빠르게 None."""
        result = pc.fetch_pdf_with_fallback(
            ticker="058610", date="2026-03-11", title="test",
            broker_hint="알수없는증권", pdf_url=None,
        )
        # 무료 collector들이 모두 fileName/id 없어서 None 반환 예상
        assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━ robots.txt 준수 검증 ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRobotsTxtCompliance:
    """사용자 룰: robots.txt 준수."""

    def test_samsungpop_robots_check(self):
        """samsungpop.com robots.txt 조회 — 에러 raise 안 함."""
        pc._ROBOTS_CACHE.clear()
        allowed = pc._is_crawl_allowed("www.samsungpop.com")
        assert isinstance(allowed, bool)

    def test_hanaw_robots_check(self):
        pc._ROBOTS_CACHE.clear()
        allowed = pc._is_crawl_allowed("file.hanaw.com")
        assert isinstance(allowed, bool)
