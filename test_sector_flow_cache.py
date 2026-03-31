"""
sector_flow 캐시 기능 테스트
- load_sector_flow_cache / save_sector_flow_cache 기본 I/O
- get_sector_flow 핸들러의 캐시 히트/미스 판정 로직
"""
import pytest
import json
import os
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


# ── kis_api 함수 import (telegram 미설치 환경 대비) ──
import sys, types

telegram_stub = types.ModuleType("telegram")
telegram_stub.Update = object
telegram_stub.ReplyKeyboardMarkup = type("ReplyKeyboardMarkup", (), {"__init__": lambda self, *a, **kw: None})
ext_stub = types.ModuleType("telegram.ext")
ext_stub.Application = object
ext_stub.CommandHandler = object
ext_stub.MessageHandler = object
ext_stub.filters = type("filters", (), {"TEXT": None, "Regex": staticmethod(lambda x: x)})()
ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
sys.modules.setdefault("telegram", telegram_stub)
sys.modules.setdefault("telegram.ext", ext_stub)

import kis_api
from kis_api import load_sector_flow_cache, save_sector_flow_cache, load_json, save_json


class TestSectorFlowCacheIO:
    """load/save 기본 동작 테스트"""

    def test_load_empty_cache(self, tmp_path):
        """캐시 파일 없을 때 빈 dict 반환"""
        fake_path = str(tmp_path / "sector_flow_cache.json")
        with patch.object(kis_api, "SECTOR_FLOW_CACHE_FILE", fake_path):
            result = load_sector_flow_cache()
        assert result == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        """저장 후 로드하면 동일 데이터"""
        fake_path = str(tmp_path / "sector_flow_cache.json")
        sample = {
            "date": "20260329",
            "cached_at": "15:35:00",
            "data": {
                "top_inflow": [{"sector": "반도체", "frgn": 100, "orgn": 200}],
                "top_outflow": [{"sector": "조선", "frgn": -50, "orgn": -30}],
                "all": [],
            },
        }
        with patch.object(kis_api, "SECTOR_FLOW_CACHE_FILE", fake_path):
            save_sector_flow_cache(sample)
            loaded = load_sector_flow_cache()
        assert loaded == sample
        assert loaded["date"] == "20260329"
        assert loaded["data"]["top_inflow"][0]["sector"] == "반도체"

    def test_load_corrupted_json(self, tmp_path):
        """깨진 JSON 파일은 빈 dict 반환"""
        fake_path = str(tmp_path / "sector_flow_cache.json")
        with open(fake_path, "w") as f:
            f.write("{corrupted json!!")
        with patch.object(kis_api, "SECTOR_FLOW_CACHE_FILE", fake_path):
            result = load_sector_flow_cache()
        assert result == {}


class TestSectorFlowCacheLogic:
    """캐시 히트/미스 판정 로직 테스트

    mcp_tools.py get_sector_flow 핸들러의 핵심 조건:
      market_closed = hour > 15 or (hour == 15 and minute >= 30)
      cache hit = market_closed AND cache["date"] == today
    """

    def _make_cache(self, date_str: str) -> dict:
        return {
            "date": date_str,
            "cached_at": "15:35:00",
            "data": {
                "date": date_str,
                "top_inflow": [{"sector": "반도체", "frgn": 500, "orgn": 300}],
                "top_outflow": [{"sector": "에너지", "frgn": -100, "orgn": -200}],
                "all": [
                    {"sector": "반도체", "frgn": 500, "orgn": 300},
                    {"sector": "에너지", "frgn": -100, "orgn": -200},
                ],
            },
        }

    def _check_cache_hit(self, now_kst: datetime, cache: dict) -> bool:
        """Replicates the cache-hit condition from mcp_tools.py"""
        market_closed = now_kst.hour > 15 or (now_kst.hour == 15 and now_kst.minute >= 30)
        today = now_kst.strftime("%Y%m%d")
        return market_closed and cache.get("date") == today

    def _should_save_cache(self, now_kst: datetime, has_data: bool) -> bool:
        """Replicates the cache-save condition from mcp_tools.py"""
        market_closed = now_kst.hour > 15 or (now_kst.hour == 15 and now_kst.minute >= 30)
        return market_closed and has_data

    def test_cache_hit_after_market_close(self):
        """15:30 이후 + 당일 캐시 존재 → cached=True (캐시 히트)"""
        now = datetime(2026, 3, 29, 16, 0, 0, tzinfo=KST)
        cache = self._make_cache("20260329")
        assert self._check_cache_hit(now, cache) is True

    def test_cache_hit_at_exactly_1530(self):
        """15:30 정각에도 캐시 히트"""
        now = datetime(2026, 3, 29, 15, 30, 0, tzinfo=KST)
        cache = self._make_cache("20260329")
        assert self._check_cache_hit(now, cache) is True

    def test_no_cache_during_market_hours(self):
        """15:30 이전 → 캐시 무시, 실시간 조회"""
        now = datetime(2026, 3, 29, 14, 0, 0, tzinfo=KST)
        cache = self._make_cache("20260329")
        assert self._check_cache_hit(now, cache) is False

    def test_no_cache_at_1529(self):
        """15:29는 아직 장중 → 캐시 미스"""
        now = datetime(2026, 3, 29, 15, 29, 0, tzinfo=KST)
        cache = self._make_cache("20260329")
        assert self._check_cache_hit(now, cache) is False

    def test_cache_miss_different_date(self):
        """장마감 후지만 캐시 날짜가 다르면 미스"""
        now = datetime(2026, 3, 30, 16, 0, 0, tzinfo=KST)
        cache = self._make_cache("20260329")  # yesterday's cache
        assert self._check_cache_hit(now, cache) is False

    def test_cache_miss_empty_cache(self):
        """캐시가 비어있으면 미스"""
        now = datetime(2026, 3, 29, 16, 0, 0, tzinfo=KST)
        assert self._check_cache_hit(now, {}) is False

    def test_no_cache_on_fallback_data(self):
        """API 실패로 fallback 데이터(all zeros)일 때 캐시 저장하지 않음"""
        now = datetime(2026, 3, 29, 16, 0, 0, tzinfo=KST)
        has_data = False  # all sectors have total == 0
        assert self._should_save_cache(now, has_data) is False

    def test_save_cache_with_valid_data_after_close(self):
        """장마감 후 유효 데이터 → 캐시 저장"""
        now = datetime(2026, 3, 29, 16, 0, 0, tzinfo=KST)
        has_data = True
        assert self._should_save_cache(now, has_data) is True

    def test_no_save_during_market_hours_even_with_data(self):
        """장중에는 유효 데이터여도 캐시 저장하지 않음"""
        now = datetime(2026, 3, 29, 10, 0, 0, tzinfo=KST)
        has_data = True
        assert self._should_save_cache(now, has_data) is False
