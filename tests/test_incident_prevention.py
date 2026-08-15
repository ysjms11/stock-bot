"""tests/test_incident_prevention.py — 8/7~8/14 침묵장애 재발방지 3건 + 2026-08-16 리뷰 반영 회귀 테스트.

배경: 봇 프로세스의 아웃바운드 네트워크만 죽은 채 8일 무알림 운행
(수집 30연속 실패·스냅샷 동결·텔레그램 발송까지 전멸, 인바운드만 생존).
로그는 /tmp라 재부팅에 소실돼 원인 추적이 불가능했음.

1. /health 신선도 확장 (main_pkg/_entry.py) — last_snapshot/last_collect 노출,
   어떤 예외에도 200 {"status":"ok"} 보장, 정상 300초/필드결손 30초 캐시,
   읽기전용 URI 연결(DB 파일 생성 부작용 차단).
2. weekly_log_rotate 로그 경로 이동 (main_pkg/jobs/sanity.py) — /tmp → ~/Library/Logs
   (재부팅에도 보존).
3. weekly_sanity_check 백필 윈도우 확대 (main_pkg/jobs/sanity.py) — 5영업일→10영업일,
   안전상한 14일→21일, SQL 조회창 14일→25일, 백필완료 임계 1500→500, 백필 캡 5일+30분 타임아웃.
"""
import inspect
import json
import os
import sqlite3
import time as _time

import main_pkg._entry as entry
import main_pkg.jobs.sanity as sanity


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. /health 신선도 확장
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _reset_health_cache():
    entry._health_cache["ts"] = 0.0
    entry._health_cache["payload"] = None
    entry._health_cache["ttl"] = 300


def _make_db(tmp_path, trade_date, name="stock.db"):
    db_path = tmp_path / name
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE daily_snapshot (trade_date TEXT)")
    con.execute("INSERT INTO daily_snapshot (trade_date) VALUES (?)", (trade_date,))
    con.commit()
    con.close()
    return str(db_path)


async def test_health_returns_snapshot_and_collect_fields(tmp_path, monkeypatch):
    _reset_health_cache()
    hist_file = tmp_path / "portfolio_history.json"
    hist_file.write_text("{}")
    monkeypatch.setattr(entry, "PORTFOLIO_HISTORY_FILE", str(hist_file))
    monkeypatch.setattr(entry, "DB_PATH", _make_db(tmp_path, "20260814"))

    resp = await entry._handle_health(None)
    assert resp.status == 200
    payload = json.loads(resp.body)
    assert payload["status"] == "ok"
    assert "last_snapshot" in payload
    assert payload["last_collect"] == "20260814"
    assert entry._health_cache["ttl"] == 300  # 필드 완비 → 정상 TTL
    _reset_health_cache()


async def test_health_always_ok_even_when_files_missing(tmp_path, monkeypatch):
    """파일/DB 부재 등 어떤 예외에도 status:ok는 유지, 신선도 필드만 생략."""
    _reset_health_cache()
    monkeypatch.setattr(entry, "PORTFOLIO_HISTORY_FILE", str(tmp_path / "does_not_exist.json"))
    monkeypatch.setattr(entry, "DB_PATH", str(tmp_path / "does_not_exist.db"))

    resp = await entry._handle_health(None)
    assert resp.status == 200
    payload = json.loads(resp.body)
    assert payload == {"status": "ok"}
    assert entry._health_cache["ttl"] == 30  # 필드 결손 → 단축 TTL
    _reset_health_cache()


async def test_health_caches_for_300_seconds(tmp_path, monkeypatch):
    _reset_health_cache()
    hist_file = tmp_path / "portfolio_history.json"
    hist_file.write_text("{}")
    monkeypatch.setattr(entry, "PORTFOLIO_HISTORY_FILE", str(hist_file))
    monkeypatch.setattr(entry, "DB_PATH", _make_db(tmp_path, "20260814", "a.db"))

    resp1 = await entry._handle_health(None)
    payload1 = json.loads(resp1.body)
    assert payload1["last_collect"] == "20260814"

    # DB 내용이 바뀌어도 300초 이내면 캐시된 결과를 그대로 반환해야 함 (2회째 호출 = 재조회 없음)
    monkeypatch.setattr(entry, "DB_PATH", _make_db(tmp_path, "20260815", "b.db"))
    resp2 = await entry._handle_health(None)
    payload2 = json.loads(resp2.body)
    assert payload2 == payload1

    # 캐시 만료 시각을 강제로 지나가게 하면 새 값을 반영해야 함
    entry._health_cache["ts"] = _time.time() - 301
    resp3 = await entry._handle_health(None)
    payload3 = json.loads(resp3.body)
    assert payload3["last_collect"] == "20260815"
    _reset_health_cache()


async def test_health_short_ttl_on_partial_failure(tmp_path, monkeypatch):
    """last_snapshot/last_collect 중 하나라도 빠지면 캐시 TTL이 30초로 단축돼야 함
    (300초 그대로면 일시적 실패가 5분간 고착 — 2026-08-16 리뷰)."""
    _reset_health_cache()
    monkeypatch.setattr(entry, "PORTFOLIO_HISTORY_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(entry, "DB_PATH", _make_db(tmp_path, "20260814", "partial.db"))

    resp1 = await entry._handle_health(None)
    payload1 = json.loads(resp1.body)
    assert "last_snapshot" not in payload1
    assert payload1["last_collect"] == "20260814"
    assert entry._health_cache["ttl"] == 30

    # 300초 TTL이었다면 여전히 캐시 hit일 31초 뒤 — 30초 단축 TTL이므로 재조회돼야 함
    entry._health_cache["ts"] = _time.time() - 31
    monkeypatch.setattr(entry, "DB_PATH", _make_db(tmp_path, "20260815", "partial2.db"))
    resp2 = await entry._handle_health(None)
    payload2 = json.loads(resp2.body)
    assert payload2["last_collect"] == "20260815"
    _reset_health_cache()


async def test_health_ok_when_db_path_undefined(tmp_path, monkeypatch):
    """DB_PATH 참조 자체가 NameError(속성 부재)여도 200 status:ok는 유지되고
    last_collect만 생략돼야 함 (2026-08-16 리뷰 (a))."""
    _reset_health_cache()
    hist_file = tmp_path / "portfolio_history.json"
    hist_file.write_text("{}")
    monkeypatch.setattr(entry, "PORTFOLIO_HISTORY_FILE", str(hist_file))
    monkeypatch.delattr(entry, "DB_PATH", raising=False)

    resp = await entry._handle_health(None)
    assert resp.status == 200
    payload = json.loads(resp.body)
    assert payload["status"] == "ok"
    assert "last_snapshot" in payload
    assert "last_collect" not in payload
    assert entry._health_cache["ttl"] == 30
    _reset_health_cache()


async def test_health_readonly_connect_does_not_create_db_file(tmp_path, monkeypatch):
    """mode=ro 읽기전용 연결이므로 경로 부재 시 빈 DB 파일을 생성하면 안 됨
    (구 sqlite3.connect(DB_PATH) 부작용 차단 — 2026-08-16 리뷰 (b), 재현 확인됨)."""
    _reset_health_cache()
    fake_db = tmp_path / "does_not_exist.db"
    monkeypatch.setattr(entry, "PORTFOLIO_HISTORY_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(entry, "DB_PATH", str(fake_db))

    resp = await entry._handle_health(None)
    assert resp.status == 200
    payload = json.loads(resp.body)
    assert "last_collect" not in payload
    assert not fake_db.exists(), "읽기전용 연결이 빈 DB 파일을 생성함 (mode=ro 미적용)"
    _reset_health_cache()


def test_health_uses_readonly_uri_in_source():
    src = inspect.getsource(entry._handle_health)
    assert 'mode=ro' in src
    assert 'uri=True' in src


def test_health_route_registered_on_run_all():
    """_run_all 소스에 /health 라우트가 구 lambda가 아닌 _handle_health로 등록돼 있는지 검사."""
    src = inspect.getsource(entry._run_all)
    assert '"/health", _handle_health' in src
    assert 'lambda r: web.json_response({"status": "ok"})' not in src


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 로그 경로 이동 (/tmp → ~/Library/Logs, 재부팅 소실 방지)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_log_rotate_path_moved_to_library_logs():
    src = inspect.getsource(sanity.weekly_log_rotate)
    assert "~/Library/Logs/stock-bot.log" in src
    assert '"/tmp/stock-bot.log"' not in src


async def test_log_rotate_uses_expanduser_path(monkeypatch):
    """weekly_log_rotate가 실제로 os.path.expanduser(~/Library/Logs/...) 경로를 사용하는지 실행 검증."""
    calls = {}

    def _fake_getsize(path):
        calls["path"] = path
        raise FileNotFoundError(path)

    monkeypatch.setattr(os.path, "getsize", _fake_getsize)
    await sanity.weekly_log_rotate(context=None)

    assert calls["path"] == os.path.expanduser("~/Library/Logs/stock-bot.log")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. sanity 백필 윈도우/조회창/임계/가드 (2026-08-16 리뷰 반영)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def test_sanity_window_expanded_in_source():
    src = inspect.getsource(sanity.weekly_sanity_check)
    # 백필 역산 윈도우: 5영업일→10영업일, 안전상한 14일→21일
    assert "range(21)" in src
    assert "len(bizdays) >= 10" in src
    assert "range(14)" not in src
    assert "len(bizdays) >= 5" not in src
    # SQL 조회창: bizday 역산 최대(21일)보다 넓게 25일 (14일이면 공휴일 낀 주에 영구 오탐)
    assert "timedelta(days=25)" in src
    assert "timedelta(days=14)" not in src
    # 백필완료 판정 임계: 1500(전종목 기준, 백필산출물 600행을 영구 결손 오판) → 500
    assert "r[1] >= 500" in src
    assert "r[1] > 1500" not in src
    # 백필 가드: 회당 최대 5일 캡(오래된 날짜 우선) + 30분 전체 타임아웃
    assert "sorted(missing)" in src
    assert "missing_sorted[:5]" in src
    assert "asyncio.wait_for" in src
    assert "timeout=1800" in src
