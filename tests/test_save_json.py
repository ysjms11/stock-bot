"""save_json atomic write 회귀 테스트."""
import json
import os
import tempfile
import threading

import pytest

from kis_api._files import load_json, save_json


def test_save_json_basic(tmp_path):
    """기본 save/load 라운드트립."""
    path = tmp_path / "test.json"
    data = {"key": "value", "num": 42, "list": [1, 2, 3]}
    save_json(str(path), data)
    loaded = load_json(str(path))
    assert loaded == data


def test_save_json_overwrite(tmp_path):
    """덮어쓰기 — 이전 데이터가 새 데이터로 완전 교체됨."""
    path = tmp_path / "test.json"
    save_json(str(path), {"v": 1})
    save_json(str(path), {"v": 2, "extra": True})
    loaded = load_json(str(path))
    assert loaded == {"v": 2, "extra": True}


def test_save_json_no_tmp_leftover(tmp_path):
    """save 완료 후 .tmp_ 임시 파일이 남지 않음."""
    path = tmp_path / "test.json"
    save_json(str(path), {"x": 1})
    tmp_files = [f for f in os.listdir(tmp_path) if f.startswith(".tmp_")]
    assert tmp_files == [], f"임시 파일 잔존: {tmp_files}"


def test_save_json_lock_file_exists(tmp_path):
    """save 완료 후 .lock 파일은 남아 있어도 무방 (advisory lock, 데이터 무결성 영향 없음)."""
    path = tmp_path / "test.json"
    save_json(str(path), {"y": 2})
    # .lock 파일 존재 여부 무관하게 데이터는 정상
    loaded = load_json(str(path))
    assert loaded == {"y": 2}


def test_save_json_concurrent(tmp_path):
    """N concurrent writers — 최종 state는 어느 한 writer의 유효한 JSON 결과."""
    path = tmp_path / "concurrent.json"
    errors = []
    results = []

    def writer(i):
        try:
            save_json(str(path), {"writer": i, "payload": list(range(50))})
            results.append(i)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent write 오류: {errors}"

    # 최종 파일은 유효한 JSON이어야 함
    with open(str(path), encoding="utf-8") as f:
        final = json.load(f)
    assert "writer" in final
    assert final["writer"] in results


def test_save_json_unicode(tmp_path):
    """한글/이모지 포함 데이터 저장 후 복원."""
    path = tmp_path / "unicode.json"
    data = {"종목명": "삼성전자", "메모": "매수 검토 중 📈"}
    save_json(str(path), data)
    loaded = load_json(str(path))
    assert loaded == data


def test_save_json_large_data(tmp_path):
    """10만 항목 dict — 대용량에서도 atomic 유지."""
    path = tmp_path / "large.json"
    data = {str(i): i * 1.5 for i in range(100_000)}
    save_json(str(path), data)
    loaded = load_json(str(path))
    assert len(loaded) == 100_000
    assert loaded["99999"] == pytest.approx(99999 * 1.5)
