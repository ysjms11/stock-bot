"""브레이커 입력 픽스(2026-07-20) 회귀 테스트.

대상: kis_api/portfolio.py
  - save_portfolio_snapshot(): US 가격 경로 재구성 (WS 캐시 제거,
    REST → Yahoo → carry-forward(age<=2)) + NAV 체인(flow-adjusted, additive
    nav_r/nav_index/holdings.price_ext/holdings.price_age_days)
  - check_drawdown(): nav_index 우선 사용, 윈도우 내 결측 시 구방식(_total) 폴백

핵심 불변식 검증:
  1. flow-neutral: 입출금(현금 변화)·qty 변화가 nav_r을 바꾸지 않음
     (nav_r은 prev 스냅샷의 qty·현금만 사용, "오늘" 실제 qty/현금은 미사용)
  2. USD 현금의 환차가 nav_r에 정확히 반영됨
  3. carry-forward: REST+Yahoo 모두 실패 시 age<=2까지 직전가 사용,
     age>2는 스냅샷에서 제외 + DQ ERROR 로그
  4. check_drawdown(): 윈도우 내 nav_index 결측 시 구방식(total_asset_krw) 폴백
  5. nav 기반일 때 알림 메시지에 "(flow-adjusted)" 표기 + 임계값(-4/-7) 동작 불변
"""
import json

import pytest

import kis_api.portfolio as portfolio


PAST_DATE = "2020-01-01"  # 항상 "오늘"보다 과거 — prev 스냅샷 고정 날짜로 사용


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _setup_files(tmp_path, monkeypatch, portfolio_data, history_data):
    pf = tmp_path / "portfolio.json"
    ph = tmp_path / "portfolio_history.json"
    _write(pf, portfolio_data)
    _write(ph, history_data)
    monkeypatch.setattr(portfolio, "PORTFOLIO_FILE", str(pf))
    monkeypatch.setattr(portfolio, "PORTFOLIO_HISTORY_FILE", str(ph))
    return pf, ph


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. flow-neutral 속성 — 입출금·qty 변화가 nav_r을 바꾸지 않음
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def _run_snapshot(tmp_path, monkeypatch, portfolio_data, fx_price=1300.0, sym_price=110.0):
    history = {
        "snapshots": [{
            "date": PAST_DATE,
            "total_asset_krw": 1_000_000,
            "cash_krw": 0, "cash_usd": 0, "usd_krw_rate": 1300.0,
            "holdings": {
                "AAA": {"price": 100, "price_ext": 100, "price_age_days": 0,
                        "qty": 10, "eval_usd": 1000},
            },
            "nav_index": 100.0,
        }]
    }
    _setup_files(tmp_path, monkeypatch, portfolio_data, history)

    async def fake_fetch_us_price_simple(sym, token):
        return {"last": str(sym_price)}

    async def fake_yahoo_quote(sym):
        if sym == "KRW=X":
            return {"price": fx_price}
        return {"price": sym_price}

    monkeypatch.setattr(portfolio, "_fetch_us_price_simple", fake_fetch_us_price_simple)
    monkeypatch.setattr(portfolio, "get_yahoo_quote", fake_yahoo_quote)

    return await portfolio.save_portfolio_snapshot("dummy-token")


async def test_flow_neutral_cash_and_qty_change_do_not_move_nav_r(tmp_path, monkeypatch):
    """오늘 실제 qty/현금이 prev와 크게 달라도(입출금·추가매수) nav_r은 prev의
    qty·현금만 쓰므로 변하지 않는다."""
    base_portfolio = {"us_stocks": {"AAA": {"qty": 10}}, "cash_krw": 0, "cash_usd": 0}
    snap_a = await _run_snapshot(tmp_path, monkeypatch, base_portfolio)

    flow_portfolio = {"us_stocks": {"AAA": {"qty": 999}},
                       "cash_krw": 50_000_000, "cash_usd": 5000}
    snap_b = await _run_snapshot(tmp_path, monkeypatch, flow_portfolio)

    assert "nav_r" in snap_a and "nav_r" in snap_b
    assert snap_a["nav_r"] == pytest.approx(snap_b["nav_r"], abs=1e-9)
    # 가격 100→110, fx 불변이므로 순수 가격 수익률 10%와 일치해야 함
    assert snap_a["nav_r"] == pytest.approx(0.1, abs=1e-6)


async def test_flow_neutral_new_position_and_full_exit_ignored(tmp_path, monkeypatch):
    """오늘 신규 편입/전량 매도된 종목은 공통보유가 아니므로 nav_r 계산에서
    제외되고, 기존 공통종목 가격 수익률만 반영되어야 한다."""
    portfolio_data = {
        "us_stocks": {"AAA": {"qty": 10}, "CCC": {"qty": 5}},  # CCC는 신규 편입(공통 아님)
        "cash_krw": 0, "cash_usd": 0,
    }
    history = {
        "snapshots": [{
            "date": PAST_DATE,
            "total_asset_krw": 1_000_000,
            "cash_krw": 0, "cash_usd": 0, "usd_krw_rate": 1300.0,
            "holdings": {
                "AAA": {"price": 100, "price_ext": 100, "price_age_days": 0,
                        "qty": 10, "eval_usd": 1000},
            },
            "nav_index": 100.0,
        }]
    }
    _setup_files(tmp_path, monkeypatch, portfolio_data, history)

    async def fake_fetch(sym, token):
        return {"last": "110" if sym == "AAA" else "50"}

    async def fake_yahoo(sym):
        return {"price": 1300.0}

    monkeypatch.setattr(portfolio, "_fetch_us_price_simple", fake_fetch)
    monkeypatch.setattr(portfolio, "get_yahoo_quote", fake_yahoo)

    snap = await portfolio.save_portfolio_snapshot("dummy-token")
    assert snap["nav_r"] == pytest.approx(0.1, abs=1e-6)  # CCC 신규편입은 미반영


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. USD 현금 환차 반영
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def test_usd_cash_fx_change_reflected_in_nav_r(tmp_path, monkeypatch):
    """보유종목 없이 USD 현금만 있는 포트 — cash_usd는 그대로, 환율만 변할 때
    그 변화가 정확히 nav_r에 반영되어야 한다."""
    history = {
        "snapshots": [{
            "date": PAST_DATE,
            "total_asset_krw": 1_300_000,
            "cash_krw": 0, "cash_usd": 1000, "usd_krw_rate": 1300.0,
            "holdings": {},
            "nav_index": 100.0,
        }]
    }
    portfolio_data = {"us_stocks": {}, "cash_krw": 0, "cash_usd": 1000}
    _setup_files(tmp_path, monkeypatch, portfolio_data, history)

    async def fake_yahoo(sym):
        assert sym == "KRW=X"
        return {"price": 1400.0}

    async def fail_fetch(sym, token):
        raise AssertionError("US 보유종목이 없으므로 호출되면 안 됨")

    monkeypatch.setattr(portfolio, "get_yahoo_quote", fake_yahoo)
    monkeypatch.setattr(portfolio, "_fetch_us_price_simple", fail_fetch)

    snap = await portfolio.save_portfolio_snapshot("dummy-token")
    expected_r = 1400.0 / 1300.0 - 1
    assert snap["nav_r"] == pytest.approx(expected_r, rel=1e-6)


async def test_kr_us_mixed_fx_applies_only_to_us_leg(tmp_path, monkeypatch):
    """KR 6자리 티커(fx=1) + US 티커 혼합 포트폴리오 — 환율이 오직 US 레그에만
    적용되고 KR 레그에는 미적용됨을 손계산 기대값으로 증명한다. 기존 테스트는
    전부 US 단일 종목(가짜 티커도 _is_us_ticker=True)이라 fx가 KR에 잘못
    적용되거나 US에서 누락돼도 걸러내지 못했다."""
    kr_ticker = "005930"
    us_ticker = "AAA"
    portfolio_data = {
        kr_ticker: {"qty": 10},
        "us_stocks": {us_ticker: {"qty": 10}},
        "cash_krw": 0, "cash_usd": 0,
    }
    history = {
        "snapshots": [{
            "date": PAST_DATE,
            "total_asset_krw": 1_000_000,
            "cash_krw": 0, "cash_usd": 0, "usd_krw_rate": 1300.0,
            "holdings": {
                kr_ticker: {"price": 1000, "price_ext": 1000, "qty": 10, "eval": 10000},
                us_ticker: {"price": 100, "price_ext": 100, "price_age_days": 0,
                            "qty": 10, "eval_usd": 1000},
            },
            "nav_index": 100.0,
        }]
    }
    _setup_files(tmp_path, monkeypatch, portfolio_data, history)

    async def fake_batch_stock_detail(tickers, token, delay=0.2):
        return [{"ticker": t, "price": 1100, "error": None} for t in tickers]  # KR 가격 +10%

    async def fake_fetch_us_price_simple(sym, token):
        return {"last": "100"}  # US 가격(USD)은 불변

    async def fake_yahoo_quote(sym):
        if sym == "KRW=X":
            return {"price": 1400.0}  # 환율만 1300 → 1400
        return {"price": 100.0}

    monkeypatch.setattr(portfolio, "batch_stock_detail", fake_batch_stock_detail)
    monkeypatch.setattr(portfolio, "_fetch_us_price_simple", fake_fetch_us_price_simple)
    monkeypatch.setattr(portfolio, "get_yahoo_quote", fake_yahoo_quote)

    snap = await portfolio.save_portfolio_snapshot("dummy-token")

    # 손계산: KR 레그는 fx=1 고정, US 레그만 fx_prev/fx_now 적용
    numerator = 10 * 1100 * 1.0 + 10 * 100 * 1400.0
    denominator = 10 * 1000 * 1.0 + 10 * 100 * 1300.0
    expected_r = numerator / denominator - 1

    # 프로덕션 코드가 nav_r을 round(nav_r, 6)으로 저장하므로 그에 맞춰 비교
    assert snap["nav_r"] == pytest.approx(round(expected_r, 6), abs=1e-9)
    # KR 단독 가격수익률(10%)과는 달라야 함 — US 레그에 fx가 실제로 반영됐다는 증거.
    # fx가 (버그처럼) KR에도 적용되거나 US에서 누락되면 이 부등식이 깨진다.
    assert snap["nav_r"] != pytest.approx(0.10, abs=1e-6)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. carry-forward: age 증가 · age<=2 사용 · age>2 제외 + DQ 로그
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def test_carry_forward_age_progression_then_exclusion(tmp_path, monkeypatch, capsys):
    """REST+Yahoo 모두 실패 시 직전 스냅샷 가격을 carry-forward.
    age 0→1→2까지는 포함, age 3(한도초과)에서 제외 + DQ ERROR 로그."""
    portfolio_data = {"us_stocks": {"BBB": {"qty": 10}}, "cash_krw": 0, "cash_usd": 0}

    async def fail_rest(sym, token):
        raise RuntimeError("REST 실패")

    async def fail_or_fx_yahoo(sym):
        if sym == "KRW=X":
            return {"price": 1300.0}
        return None  # 개별 종목 yahoo 폴백도 실패

    monkeypatch.setattr(portfolio, "_fetch_us_price_simple", fail_rest)
    monkeypatch.setattr(portfolio, "get_yahoo_quote", fail_or_fx_yahoo)

    # age 0 → 1
    history1 = {"snapshots": [{
        "date": PAST_DATE, "total_asset_krw": 1_000_000,
        "cash_krw": 0, "cash_usd": 0, "usd_krw_rate": 1300.0,
        "holdings": {"BBB": {"price": 50, "price_ext": 50, "price_age_days": 0,
                              "qty": 10, "eval_usd": 500}},
        "nav_index": 100.0,
    }]}
    _setup_files(tmp_path, monkeypatch, portfolio_data, history1)
    snap1 = await portfolio.save_portfolio_snapshot("dummy-token")
    assert snap1["holdings"]["BBB"]["price_age_days"] == 1
    assert snap1["holdings"]["BBB"]["price"] == 50
    assert snap1["holdings"]["BBB"]["price_ext"] == 50

    # age 1 → 2
    history2 = {"snapshots": [dict(snap1, date=PAST_DATE)]}
    _setup_files(tmp_path, monkeypatch, portfolio_data, history2)
    snap2 = await portfolio.save_portfolio_snapshot("dummy-token")
    assert snap2["holdings"]["BBB"]["price_age_days"] == 2
    assert snap2["holdings"]["BBB"]["price"] == 50

    # age 2 → 3: 한도 초과 → 제외 + DQ ERROR 로그
    history3 = {"snapshots": [dict(snap2, date=PAST_DATE)]}
    _setup_files(tmp_path, monkeypatch, portfolio_data, history3)
    capsys.readouterr()  # 이전 캡처 비우기
    snap3 = await portfolio.save_portfolio_snapshot("dummy-token")
    assert "BBB" not in snap3["holdings"]
    out = capsys.readouterr().out
    assert "DQ ERROR" in out
    assert "BBB" in out


async def test_carry_forward_no_prior_snapshot_excludes_with_dq_log(tmp_path, monkeypatch, capsys):
    """REST+Yahoo 실패 + 직전 스냅샷 자체가 없으면(신규 종목 최초 조회 실패)
    carry-forward 불가 → 제외 + DQ ERROR 로그."""
    portfolio_data = {"us_stocks": {"ZZZ": {"qty": 1}}, "cash_krw": 0, "cash_usd": 0}
    history = {"snapshots": []}
    _setup_files(tmp_path, monkeypatch, portfolio_data, history)

    async def fail_rest(sym, token):
        raise RuntimeError("REST 실패")

    async def fail_or_fx_yahoo(sym):
        if sym == "KRW=X":
            return {"price": 1300.0}
        return None

    monkeypatch.setattr(portfolio, "_fetch_us_price_simple", fail_rest)
    monkeypatch.setattr(portfolio, "get_yahoo_quote", fail_or_fx_yahoo)

    capsys.readouterr()
    snap = await portfolio.save_portfolio_snapshot("dummy-token")
    assert "ZZZ" not in snap["holdings"]
    out = capsys.readouterr().out
    assert "DQ ERROR" in out and "ZZZ" in out
    # 최초 스냅샷이므로 nav_index는 기준점 100.0
    assert snap["nav_index"] == 100.0
    assert "nav_r" not in snap


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 4/5. check_drawdown(): nav_index 우선 + 결측 시 폴백 + "(flow-adjusted)" 표기
#      + 임계값(-4/-7)·레벨 불변
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _make_snaps_nav_based():
    """21개 스냅샷 — total_asset_krw는 완만히 "상승"(구방식이면 무경보),
    nav_index는 급락(-10%, CRITICAL+WARNING 유발). nav 기반이면 하락 경보가
    떠야 하고, 메시지에 (flow-adjusted)가 붙어야 한다."""
    snaps = []
    for i in range(21):
        nav = 100.0 if i < 19 else (95.0 if i == 19 else 90.0)
        snaps.append({
            "date": f"2024-01-{i + 1:02d}",
            "total_asset_krw": 1_000_000 + i * 1000,  # 완만한 상승 (구방식 기준 무경보)
            "nav_index": nav,
        })
    return snaps


def _make_snaps_fallback(missing_index=18):
    """21개 스냅샷 — total_asset_krw는 급락(-8%, CRITICAL 유발), nav_index는
    완만 상승(있었다면 무경보)이지만 윈도우 내 한 스냅샷에 nav_index가 없어
    구방식(_total)으로 폴백해야 한다."""
    snaps = []
    for i in range(21):
        snaps.append({
            "date": f"2024-02-{i + 1:02d}",
            "total_asset_krw": 1_000_000 - i * 4000,  # i=20 → 920,000 (-8.0%)
            "nav_index": 100.0 + i * 0.1,              # 있었다면 무경보
        })
    del snaps[missing_index]["nav_index"]
    return snaps


def test_check_drawdown_uses_nav_index_when_window_complete(tmp_path, monkeypatch):
    """윈도우(6/21) 전체에 nav_index가 있으면 nav_index 기반으로 계산하고,
    메시지에 (flow-adjusted)가 붙는다. 구방식(total_asset, 완만 상승)이었다면
    나오지 않았을 하락 경보가 떠야 함 — 소스가 실제로 nav_index임을 증명."""
    ph = tmp_path / "portfolio_history.json"
    _write(ph, {"snapshots": _make_snaps_nav_based()})
    monkeypatch.setattr(portfolio, "PORTFOLIO_HISTORY_FILE", str(ph))

    dd = portfolio.check_drawdown()

    assert dd["monthly_max_drawdown_pct"] == pytest.approx(-10.0, abs=0.01)
    assert dd["monthly_return_pct"] == pytest.approx(-10.0, abs=0.01)
    assert dd["weekly_return_pct"] == pytest.approx(-10.0, abs=0.01)
    # 임계값(-4/-7)·레벨은 무변경 — CRITICAL(월간 -7% 한도)·WARNING(주간 -4% 한도) 둘 다 발동
    levels = {a["level"] for a in dd["alerts"]}
    assert "CRITICAL" in levels
    assert "WARNING" in levels
    assert all("(flow-adjusted)" in a["message"] for a in dd["alerts"]
               if "월간" in a["message"] or "주간" in a["message"])


def test_check_drawdown_falls_back_to_total_when_nav_missing_in_window(tmp_path, monkeypatch):
    """윈도우 내 스냅샷 하나라도 nav_index가 없으면 구방식(total_asset_krw)으로
    폴백 — 값이 total_asset_krw 기준(-8%)과 일치하고 (flow-adjusted) 표기가
    없어야 한다."""
    ph = tmp_path / "portfolio_history.json"
    _write(ph, {"snapshots": _make_snaps_fallback()})
    monkeypatch.setattr(portfolio, "PORTFOLIO_HISTORY_FILE", str(ph))

    dd = portfolio.check_drawdown()

    assert dd["monthly_max_drawdown_pct"] == pytest.approx(-8.0, abs=0.01)
    assert dd["alerts"], "CRITICAL 경보가 떠야 함 (구방식 -8% <= -7% 한도)"
    assert any(a["level"] == "CRITICAL" for a in dd["alerts"])
    assert all("(flow-adjusted)" not in a["message"] for a in dd["alerts"])


def test_check_drawdown_thresholds_and_levels_unchanged_shape():
    """알림 dict 구조(level/message 키)와 임계값 상수(-4/-7)는 리팩터 전과
    동일해야 한다 — 회귀 방지용 얕은 계약 테스트."""
    import inspect
    src = inspect.getsource(portfolio.check_drawdown)
    assert "<= -4" in src
    assert "<= -7" in src
    assert '"level": "WARNING"' in src
    assert '"level": "CRITICAL"' in src
