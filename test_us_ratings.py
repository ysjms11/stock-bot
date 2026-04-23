"""미국 애널 레이팅 주간 유니버스 스캔 테스트.

- Russell 1000 로더 (Wikipedia 파싱 + 캐시 + fallback)
- S&P 500 ∪ Russell 1000 합집합 유니버스 (load_us_scan_universe)

네트워크는 항상 모킹. 실제 Wikipedia 호출하지 않음.
"""
import sys, types, json, os, unittest, tempfile, shutil
from unittest.mock import patch, MagicMock
from datetime import datetime

# ── telegram stub (kis_api 임포트 체인이 끌어올 수 있음) ──
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
from kis_api import (
    _fetch_sp500_from_wikipedia,
    _fetch_russell1000_from_wikipedia,
    load_sp500_tickers,
    load_russell1000_tickers,
    load_us_scan_universe,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Wikipedia HTML 샘플 (파싱 검증용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _russell1000_html_fixture(n: int = 1000) -> str:
    """Russell 1000 위키 스타일 HTML 픽스처 생성.
    실제 페이지는 (Company, Ticker, Sector) 3컬럼. 티커는 index=1.
    """
    rows = ['<tr><th>Company</th><th>Ticker</th><th>Sector</th></tr>']
    rows.append('<tr><td>3M</td><td>MMM</td><td>Industrials</td></tr>')
    rows.append('<tr><td>A. O. Smith</td><td>AOS</td><td>Industrials</td></tr>')
    rows.append('<tr><td>Berkshire Hathaway</td><td>BRK.B</td><td>Financials</td></tr>')
    rows.append('<tr><td>Brown-Forman</td><td>BF.B</td><td>Consumer Staples</td></tr>')
    # 최소 크기 요건 (900+) 충족용 더미 행
    for i in range(n - 4):
        rows.append(f'<tr><td>Company{i}</td><td>SYM{i:04d}</td><td>Sector</td></tr>')
    inner = "".join(rows)
    # Russell 1000 위키는 첫 wikitable 이 summary, 2번째 wikitable 이 구성종목 (현실 반영).
    return (
        "<html><body>"
        '<table class="wikitable"><tr><th>Summary</th></tr><tr><td>~1000 companies</td></tr></table>'
        f'<table class="wikitable">{inner}</table>'
        "</body></html>"
    )


def _sp500_html_fixture(n: int = 503) -> str:
    """S&P 500 위키 스타일 HTML 픽스처. 티커는 첫 td (index=0)."""
    rows = ['<tr><th>Symbol</th><th>Security</th><th>Sector</th></tr>']
    rows.append('<tr><td>AAPL</td><td>Apple Inc.</td><td>IT</td></tr>')
    rows.append('<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td></tr>')
    for i in range(n - 2):
        rows.append(f'<tr><td>TCK{i:04d}</td><td>Company{i}</td><td>Sector</td></tr>')
    inner = "".join(rows)
    return (
        "<html><body>"
        f'<table id="constituents" class="wikitable">{inner}</table>'
        "</body></html>"
    )


def _mock_requests_get(html_body: str, status: int = 200):
    """requests.get 을 HTML 바디 반환 Mock 으로 교체."""
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.text = html_body
    return MagicMock(return_value=mock_resp)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. TestFetchRussell1000Wikipedia
# ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFetchRussell1000Wikipedia(unittest.TestCase):
    """_fetch_russell1000_from_wikipedia — HTML 파싱."""

    def test_parses_ticker_column_index_1(self):
        """Russell 1000 위키 표는 2번째 컬럼(index 1)이 티커. 파싱 성공."""
        html = _russell1000_html_fixture(1000)
        with patch("requests.get", _mock_requests_get(html)):
            tickers = _fetch_russell1000_from_wikipedia()
        self.assertIsNotNone(tickers)
        self.assertGreaterEqual(len(tickers), 900)
        self.assertIn("MMM", tickers)
        self.assertIn("AOS", tickers)
        self.assertIn("BRK.B", tickers)  # 점 포함 티커 보존
        self.assertIn("BF.B", tickers)

    def test_returns_none_when_below_min_size(self):
        """900개 미만이면 비정상 간주 → None."""
        html = _russell1000_html_fixture(500)  # 500개만 (부족)
        with patch("requests.get", _mock_requests_get(html)):
            tickers = _fetch_russell1000_from_wikipedia()
        self.assertIsNone(tickers)

    def test_returns_none_on_http_error(self):
        """HTTP 404 → None."""
        with patch("requests.get", _mock_requests_get("", status=404)):
            tickers = _fetch_russell1000_from_wikipedia()
        self.assertIsNone(tickers)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. TestLoadRussell1000Tickers
# ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestLoadRussell1000Tickers(unittest.TestCase):
    """load_russell1000_tickers — 캐시/TTL/fallback."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="stock-bot-rs1000-")
        self.cache_path = os.path.join(self.tmpdir, "us_russell1000.json")
        self._patcher = patch.object(kis_api, "US_RUSSELL1000_FILE", self.cache_path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_fetch_and_cache_when_missing(self):
        """캐시 없으면 Wikipedia 수집 + 파일 저장 → 리스트 반환."""
        html = _russell1000_html_fixture(1000)
        with patch("requests.get", _mock_requests_get(html)):
            tickers = load_russell1000_tickers()
        self.assertGreaterEqual(len(tickers), 900)
        self.assertIn("MMM", tickers)
        self.assertIn("AOS", tickers)
        # 캐시 파일이 쓰였는지
        self.assertTrue(os.path.exists(self.cache_path))
        with open(self.cache_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertIn("tickers", saved)
        self.assertIn("updated", saved)

    def test_use_fresh_cache_without_network(self):
        """30일 이내 캐시 있으면 네트워크 호출 없음."""
        sample = {"updated": datetime.now().isoformat(), "tickers": ["MMM", "AOS", "ZZZ"]}
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(sample, f)
        with patch("requests.get") as mock_get:
            tickers = load_russell1000_tickers()
        self.assertEqual(set(tickers), {"MMM", "AOS", "ZZZ"})
        mock_get.assert_not_called()

    def test_fallback_to_stale_cache_on_network_failure(self):
        """네트워크 실패 + 기존 캐시 있음 → 캐시 반환 (TTL 초과해도)."""
        sample = {"updated": "2020-01-01T00:00:00", "tickers": ["OLD1", "OLD2"]}
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(sample, f)
        # mtime 을 강제로 오래되게
        old_ts = datetime(2020, 1, 1).timestamp()
        os.utime(self.cache_path, (old_ts, old_ts))
        # requests.get 이 예외 — fetcher None 반환
        with patch("requests.get", side_effect=RuntimeError("network down")):
            tickers = load_russell1000_tickers()
        self.assertEqual(set(tickers), {"OLD1", "OLD2"})

    def test_force_refresh_hits_network_even_with_fresh_cache(self):
        """force_refresh=True → 캐시 유효해도 네트워크 호출."""
        sample = {"updated": datetime.now().isoformat(), "tickers": ["STALE"]}
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(sample, f)
        html = _russell1000_html_fixture(1000)
        with patch("requests.get", _mock_requests_get(html)) as mock_get:
            tickers = load_russell1000_tickers(force_refresh=True)
        mock_get.assert_called_once()
        self.assertGreaterEqual(len(tickers), 900)
        self.assertIn("MMM", tickers)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. TestLoadUsScanUniverse
# ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestLoadUsScanUniverse(unittest.TestCase):
    """load_us_scan_universe — S&P 500 ∪ Russell 1000 합집합."""

    def test_union_is_sorted_and_deduped(self):
        """두 로더 결과 합집합 → 중복 제거 + 정렬."""
        with patch("kis_api.load_sp500_tickers", return_value=["AAPL", "MSFT", "BRK.B"]), \
             patch("kis_api.load_russell1000_tickers", return_value=["MMM", "AOS", "BRK.B", "MSFT"]):
            u = load_us_scan_universe()
        self.assertEqual(u, sorted({"AAPL", "MSFT", "BRK.B", "MMM", "AOS"}))

    def test_union_size_between_sp500_and_1100(self):
        """실제 규모 시뮬레이션: S&P 500 + Russell 1000 합집합은 S&P 500 단독보다 크고
        Russell 1000 ± 100 정도 범위 (중복 많음)."""
        sp500 = [f"SP{i:03d}" for i in range(503)]
        # Russell 1000: S&P 500 중 많은 수가 포함됨 (대부분의 대형주 겹침) + 중형주 추가
        rs1000 = sp500[:450] + [f"MID{i:03d}" for i in range(550)]  # 총 1000
        with patch("kis_api.load_sp500_tickers", return_value=sp500), \
             patch("kis_api.load_russell1000_tickers", return_value=rs1000):
            u = load_us_scan_universe()
        self.assertGreater(len(u), len(sp500))  # S&P 500 단독보다 커야
        self.assertGreaterEqual(len(u), 1000)
        self.assertLessEqual(len(u), 1100)  # 중복 덕에 1000 ~ 1100 사이

    def test_returns_sp500_when_russell_fails(self):
        """Russell 로드 실패해도 S&P 500 는 반환 (방어적)."""
        with patch("kis_api.load_sp500_tickers", return_value=["AAPL", "MSFT"]), \
             patch("kis_api.load_russell1000_tickers", return_value=[]):
            u = load_us_scan_universe()
        self.assertEqual(u, ["AAPL", "MSFT"])

    def test_returns_russell_when_sp500_fails(self):
        """S&P 500 로드 실패해도 Russell 는 반환 (방어적)."""
        with patch("kis_api.load_sp500_tickers", return_value=[]), \
             patch("kis_api.load_russell1000_tickers", return_value=["MMM", "AOS"]):
            u = load_us_scan_universe()
        self.assertEqual(u, ["AOS", "MMM"])  # 정렬됨

    def test_both_fail_returns_empty(self):
        """둘 다 실패/예외 → 빈 리스트."""
        with patch("kis_api.load_sp500_tickers", side_effect=RuntimeError("sp500 down")), \
             patch("kis_api.load_russell1000_tickers", side_effect=RuntimeError("rs down")):
            u = load_us_scan_universe()
        self.assertEqual(u, [])


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. TestLoadSp500TickersRegression
# ━━━━━━━━━━━━━━━━━━━━━━━━━

class TestLoadSp500TickersRegression(unittest.TestCase):
    """리팩토링으로 기존 load_sp500_tickers 동작이 깨지지 않는지 회귀 테스트."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="stock-bot-sp500-")
        self.cache_path = os.path.join(self.tmpdir, "us_sp500.json")
        self._patcher = patch.object(kis_api, "US_SP500_FILE", self.cache_path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_sp500_fetch_and_cache(self):
        """S&P 500 파싱 + 캐싱 (id='constituents' 테이블, 티커는 첫 td)."""
        html = _sp500_html_fixture(503)
        with patch("requests.get", _mock_requests_get(html)):
            tickers = load_sp500_tickers()
        self.assertGreaterEqual(len(tickers), 400)
        self.assertIn("AAPL", tickers)
        self.assertIn("BRK.B", tickers)
        self.assertTrue(os.path.exists(self.cache_path))


if __name__ == "__main__":
    unittest.main()
