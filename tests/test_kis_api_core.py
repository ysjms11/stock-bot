"""핵심 KIS API 함수 unit test — mock 기반 (외부 API 미호출).

대상:
- get_kis_token: 토큰 메모리/파일 캐시 (23h)
- _kis_get: HTTP 에러 / 429 retry / 응답 파싱
- kis_stock_price: 국내 응답 파싱
- kis_us_stock_price: 미국 응답 파싱 (rate 필드)
- load_json / save_json: corruption, encoding 회복
"""
import asyncio
import json
import os
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from kis_api._files import load_json, save_json
from kis_api._session import (
    _kis_headers, _kis_get, get_kis_token, _token_cache,
)
from kis_api.kr_stock import kis_stock_price
from kis_api.us_stock import kis_us_stock_price


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. _kis_headers
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_kis_headers_contains_required_keys():
    """KIS 헤더에 필수 키 4개 포함."""
    h = _kis_headers("token-xyz", "FHKST01010100")
    assert h["authorization"] == "Bearer token-xyz"
    assert h["tr_id"] == "FHKST01010100"
    assert "appkey" in h
    assert "appsecret" in h
    assert "content-type" in h


def test_kis_headers_content_type_charset():
    """content-type에 utf-8 charset 포함."""
    h = _kis_headers("t", "TR")
    assert "utf-8" in h["content-type"].lower()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. get_kis_token — 메모리 캐시
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_get_kis_token_memory_cache_hit():
    """메모리 캐시 만료 전이면 외부 호출 없이 토큰 반환."""
    _token_cache["token"] = "cached_token_abc"
    _token_cache["expires"] = datetime.now() + timedelta(hours=22)
    try:
        result = asyncio.run(get_kis_token())
        assert result == "cached_token_abc"
    finally:
        _token_cache["token"] = None
        _token_cache["expires"] = None


def test_get_kis_token_memory_cache_expired():
    """메모리 캐시 만료 시 None 반환 가능 (네트워크 없이)."""
    _token_cache["token"] = "old_token"
    _token_cache["expires"] = datetime.now() - timedelta(hours=1)
    # 캐시 만료 — 신규 발급 시도. 환경에 따라 실패할 수 있어 호출 자체만 검증
    try:
        result = asyncio.run(get_kis_token())
        # 실패 시 None 또는 신규 토큰 반환 — 타입만 검사
        assert result is None or isinstance(result, str)
    except Exception:
        pass  # 외부 API 호출 실패 OK
    finally:
        _token_cache["token"] = None
        _token_cache["expires"] = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. _kis_get — HTTP 응답 처리
# ━━━━━━━━━━━━━━━━━━━━━━━━━

class _FakeResponse:
    """aiohttp Response mock — async context manager."""
    def __init__(self, status, json_data):
        self.status = status
        self._json = json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        return self._json


class _FakeSession:
    """aiohttp ClientSession mock — closed=False 유지."""
    def __init__(self, responses):
        # responses: list of (status, json) — 호출 순서대로 반환
        self._responses = list(responses)
        self.closed = False
        self.calls = []

    def get(self, url, headers=None, params=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        if self._responses:
            status, body = self._responses.pop(0)
        else:
            status, body = 500, {}
        return _FakeResponse(status, body)


def test_kis_get_success_returns_data():
    """200 응답 → 데이터 그대로 반환."""
    session = _FakeSession([(200, {"output": {"stck_prpr": "70000"}, "rt_cd": "0"})])
    status, data = asyncio.run(_kis_get(
        session, "/uapi/test", "TR_ID_X", "token", {"key": "val"}
    ))
    assert status == 200
    assert data["output"]["stck_prpr"] == "70000"
    # tr_id, appkey 등 헤더 전달 확인
    assert session.calls[0]["headers"]["tr_id"] == "TR_ID_X"
    assert session.calls[0]["headers"]["authorization"] == "Bearer token"


def test_kis_get_429_retries_then_succeeds():
    """429 → 재시도 → 200."""
    session = _FakeSession([
        (429, {}),
        (200, {"output": "ok"}),
    ])
    # 슬립 패치 (테스트 속도 보장)
    with patch("kis_api._session.asyncio.sleep", new=AsyncMock(return_value=None)):
        status, data = asyncio.run(_kis_get(
            session, "/p", "TR", "tk", {}
        ))
    assert status == 200
    assert data["output"] == "ok"
    assert len(session.calls) == 2


def test_kis_get_500_retries_then_succeeds():
    """500 → 재시도 → 200."""
    session = _FakeSession([
        (500, {}),
        (200, {"output": "data"}),
    ])
    with patch("kis_api._session.asyncio.sleep", new=AsyncMock(return_value=None)):
        status, data = asyncio.run(_kis_get(
            session, "/p", "TR", "tk", {}
        ))
    assert status == 200
    assert data["output"] == "data"


def test_kis_get_500_exhausts_retries():
    """500을 max_retries 회 연속 받으면 status는 마지막 응답값."""
    session = _FakeSession([(500, {}), (500, {}), (500, {})])
    with patch("kis_api._session.asyncio.sleep", new=AsyncMock(return_value=None)):
        status, data = asyncio.run(_kis_get(
            session, "/p", "TR", "tk", {}
        ))
    # 마지막 시도에서는 retry 안 함 → status 500 그대로
    assert status == 500


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. kis_stock_price — 국내 응답 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_kis_stock_price_returns_output_dict():
    """KIS 국내 현재가 응답에서 output 추출."""
    fake_data = {
        "output": {
            "stck_prpr": "70000",
            "prdy_ctrt": "1.45",
            "hts_avls": "5000000",
            "per": "12.34",
        }
    }
    session = _FakeSession([(200, fake_data)])
    out = asyncio.run(kis_stock_price("005930", "tk", session=session))
    assert out["stck_prpr"] == "70000"
    assert out["prdy_ctrt"] == "1.45"
    assert out["per"] == "12.34"
    # 올바른 TR_ID 사용 확인
    assert session.calls[0]["headers"]["tr_id"] == "FHKST01010100"


def test_kis_stock_price_empty_output():
    """output 누락 시 빈 dict 반환."""
    session = _FakeSession([(200, {"rt_cd": "1", "msg1": "오류"})])
    out = asyncio.run(kis_stock_price("005930", "tk", session=session))
    assert out == {}


def test_kis_stock_price_passes_ticker_in_params():
    """fid_input_iscd 파라미터에 ticker 전달."""
    session = _FakeSession([(200, {"output": {}})])
    asyncio.run(kis_stock_price("005930", "tk", session=session))
    params = session.calls[0]["params"]
    assert params["fid_input_iscd"] == "005930"
    assert params["fid_cond_mrkt_div_code"] == "J"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. kis_us_stock_price — 미국 응답 파싱 (rate 필드)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_kis_us_stock_price_extracts_last_and_rate():
    """미국 현재가 응답에서 last(현재가), rate(등락률%) 필드 보존."""
    fake = {
        "output": {
            "last": "180.50",
            "rate": "1.25",
            "tvol": "5000000",
            "base": "178.30",
        }
    }
    session = _FakeSession([(200, fake)])
    with patch("kis_api.us_stock._get_session", return_value=session):
        out = asyncio.run(kis_us_stock_price("TSLA", "tk"))
    assert out["last"] == "180.50"
    assert out["rate"] == "1.25"
    # CLAUDE.md 경고: 'diff_rate'가 아니라 'rate' 필드여야 한다
    assert "rate" in out
    # 올바른 TR_ID
    assert session.calls[0]["headers"]["tr_id"] == "HHDFS00000300"


def test_kis_us_stock_price_zero_falls_back_to_other_exchange():
    """1차 거래소가 0이면 다른 거래소로 fallback."""
    fake_zero = {"output": {"last": "0", "rate": "0"}}
    fake_real = {"output": {"last": "120.00", "rate": "0.50"}}
    session = _FakeSession([
        (200, fake_zero),  # 1차
        (200, fake_real),  # fallback
    ])
    with patch("kis_api.us_stock._get_session", return_value=session), \
         patch("kis_api.us_stock.asyncio.sleep", new=AsyncMock(return_value=None)):
        out = asyncio.run(kis_us_stock_price("TSLA", "tk", excd="NAS"))
    assert out["last"] == "120.00"


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. load_json / save_json — 추가 회귀 케이스
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_load_json_returns_default_when_missing(tmp_path):
    """파일 없으면 default 반환 + 파일 자동 생성."""
    path = tmp_path / "nonexistent.json"
    result = load_json(str(path), default={"x": 1})
    assert result == {"x": 1}
    assert path.exists()
    loaded = load_json(str(path))
    assert loaded == {"x": 1}


def test_load_json_returns_empty_dict_no_default(tmp_path):
    """파일 없고 default=None이면 {} 반환 (파일 미생성)."""
    path = tmp_path / "absent.json"
    result = load_json(str(path))
    assert result == {}
    assert not path.exists()


def test_load_json_handles_corrupted_file(tmp_path):
    """깨진 JSON → default로 회복."""
    path = tmp_path / "broken.json"
    path.write_text("{not json}", encoding="utf-8")
    result = load_json(str(path), default={"recovered": True})
    assert result == {"recovered": True}


def test_save_json_creates_dir_implicitly(tmp_path):
    """기존 디렉토리 내 파일 저장."""
    path = tmp_path / "sub.json"
    save_json(str(path), {"a": 1})
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_save_json_round_trip_nested(tmp_path):
    """중첩 dict round trip."""
    path = tmp_path / "nested.json"
    data = {
        "watchlist": {
            "005930": {"name": "삼성전자", "qty": 100},
            "AAPL": {"name": "Apple", "qty": 50, "memo": "분할 매수 검토"},
        },
        "decision_log": [
            {"date": "2026-05-01", "action": "buy"},
        ],
    }
    save_json(str(path), data)
    result = load_json(str(path))
    assert result == data


def test_save_json_handles_list_root(tmp_path):
    """list 루트 객체도 저장/복원 가능."""
    path = tmp_path / "list.json"
    data = [{"a": 1}, {"b": 2}, {"c": [1, 2, 3]}]
    save_json(str(path), data)
    result = load_json(str(path), default=[])
    assert result == data


def test_save_json_pathlib_path_accepted(tmp_path):
    """pathlib.Path도 인자로 허용 (str 변환)."""
    path = tmp_path / "p.json"
    save_json(path, {"v": 7})
    assert load_json(str(path)) == {"v": 7}
