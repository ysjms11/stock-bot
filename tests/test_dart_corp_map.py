"""get_dart_corp_map fallback 경로 회귀 테스트.

2026-05 패키지화로 kis_api/dart.py 의 __file__ 기준이 바뀌면서
레포 루트의 dart_corp_map.json 을 못 찾던 버그를 재발 방지.

1. get_dart_corp_map({}) 가 비어있지 않은 dict 를 반환하고 키/값이 str
2. fallback 후보 2번이 실제 레포 루트를 가리키며 파일이 존재
"""
import os
import pytest
import kis_api.dart as dart_module
from kis_api.dart import get_dart_corp_map


async def test_corp_map_nonempty():
    """운영 DATA_DIR 이나 레포 루트에서 corp_map 을 정상 로드."""
    result = await get_dart_corp_map({})
    assert isinstance(result, dict), "반환값이 dict 여야 함"
    assert len(result) > 0, "dart_corp_map.json 이 비어있거나 미발견 — fallback 경로 확인 필요"
    # 모든 키와 값이 문자열이어야 함 (ticker: corp_code 형식)
    for k, v in result.items():
        assert isinstance(k, str), f"키가 str 이어야 함: {k!r}"
        assert isinstance(v, str), f"값이 str 이어야 함: {v!r}"


def test_fallback_candidate_points_to_repo_root():
    """fallback 후보 2 가 레포 루트의 dart_corp_map.json 을 가리키며 파일이 존재."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(dart_module.__file__)))
    expected_path = os.path.join(repo_root, "dart_corp_map.json")
    assert os.path.exists(expected_path), (
        f"레포 루트에 dart_corp_map.json 이 없음: {expected_path}"
    )
