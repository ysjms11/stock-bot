"""매크로 일봉 + 시장 투자자 flow 시계열 수집 잡 (평일 19:08 KST). See main_pkg/__init__.py.

레짐 foreign_5d(kis_api/regime.py)와 SAT_PORT_CHECK 매크로 8변수 임계판정
(kis_api/polymarket.py fetch_external_macro_signals)의 데이터 원천 — db_collector/market_data.py.

⚠️ 16:05 → 19:08 이동(2026-09): KRX 확정 수급은 ~18:00 이후 공개되므로 16:05엔 flow가
잠정치/공백이었다. 19:08이면 당일 확정치를 수거한다 (19:05 daily_change_scan ·
19:15 collect_sanity_1 사이 빈 슬롯).
"""
import asyncio
from datetime import datetime

from main_pkg._ctx import (
    _track_silent_failure, _reset_silent_failure, _alert_silent_failure,
)
from kis_api import KST, get_kis_token

try:
    from db_collector.market_data import collect_macro_daily, collect_market_flow_daily
    from db_collector import _is_kr_trading_day
    _HAS_MARKET_DATA = True
except Exception:
    _HAS_MARKET_DATA = False


async def daily_market_data_collect(context):
    """평일 19:08 KST — 매크로 일봉(VIX·10Y·DXY·WTI·FX·지수) + KOSPI/KOSDAQ 투자자
    flow 시계열 저장. 성공 시 조용(텔레그램 미발송, print 요약만), 실패(예외) 또는
    양쪽 0건 지속 시 silent failure escalate (main_pkg/jobs/collect.py 패턴 복제).

    W8: 주말은 macro/flow 둘 다 스킵(전체 return). KR 휴장일(예 추석·설날 등 평일
    공휴일)은 flow만 스킵하고 macro는 수행 — US/FX 시리즈는 KR 휴장과 무관하고, KOSPI도
    전일 종가가 이미 반영돼 있어 매일 실행해도 무해하다. ⚠️ `_is_kr_trading_day`는
    "%Y%m%d" 문자열만 받는다 — date 객체나 대시(-) 포맷을 넘기면 strptime 실패 시
    fail-open으로 True를 반환해 가드가 무력화되므로 반드시 strftime("%Y%m%d")로 호출.

    부팅 직후 백필은 이 잡을 통하지 않고 collect_macro_daily()/collect_market_flow_daily(token)
    을 수동 1회 직접 호출한다 (2026-09 둘 다 async로 전환됨 — `venv/bin/python3 -c
    "import asyncio; from db_collector.market_data import collect_macro_daily; \
    print(asyncio.run(collect_macro_daily()))"` 형태로 asyncio.run() 필요).
    """
    if not _HAS_MARKET_DATA:
        return

    now = datetime.now(KST)
    if now.weekday() >= 5:
        return  # 주말: macro/flow 둘 다 스킵

    is_trading_day = _is_kr_trading_day(now.strftime("%Y%m%d"))

    try:
        macro_result = await asyncio.wait_for(collect_macro_daily(), timeout=300)

        flow_result = {}
        if is_trading_day:
            token = await get_kis_token()
            flow_result = await asyncio.wait_for(collect_market_flow_daily(token), timeout=300)

        print(f"[market_data] macro={macro_result} flow={flow_result}")

        macro_errors = [k for k, v in macro_result.items() if isinstance(v, str) and v.startswith("error")]
        flow_errors = [
            k for k, v in flow_result.items()
            if (isinstance(v, str) and v.startswith("error"))
            or (isinstance(v, dict) and v.get("error"))
        ]
        macro_total = sum(v for v in macro_result.values() if isinstance(v, int))
        flow_total = sum(v for v in flow_result.values() if isinstance(v, int))

        if macro_errors or flow_errors:
            cnt = _track_silent_failure("market_data_error", threshold=2)
            if cnt:
                await _alert_silent_failure(context, "market_data_error", cnt,
                    f"daily_market_data_collect 부분 실패\nmacro_errors={macro_errors} flow_errors={flow_errors}")
            return

        if macro_total == 0 and flow_total == 0:
            cnt = _track_silent_failure("market_data_error", threshold=2)
            if cnt:
                await _alert_silent_failure(context, "market_data_error", cnt,
                    "daily_market_data_collect 연속 0건 (매크로+flow 모두 신규 행 없음)")
            return

        _reset_silent_failure("market_data_error")
    except Exception as e:
        print(f"[market_data] 오류: {e}")
        cnt = _track_silent_failure("market_data_error", threshold=2)
        if cnt:
            await _alert_silent_failure(context, "market_data_error", cnt,
                f"daily_market_data_collect 연속 {cnt}회 실패\n오류: {e}")
