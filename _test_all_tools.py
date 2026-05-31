"""전 MCP 도구 실호출 점검 — stub(null) / 크래시 / 빈응답 한 번에 검출.

youtube 같은 'handler stub' 회귀를 모든 도구에 대해 잡는다. 실제 디스패처
_execute_tool(name, args, token) 를 통해 호출하므로 핸들러 본문 누락이 그대로
드러난다. (MCP SSE 전송계층 위·아래로 동일 핸들러 체인)

분류:
  NULL_STUB  — result 가 None → 핸들러 stub (youtube 류, 치명)
  EXCEPTION  — 호출 중 예외 (핸들러 크래시)
  ERROR      — {"error": ...} 반환 (정상 방어일 수도, 내용 봐야)
  OK         — dict/list 정상 반환
"""
import asyncio
import inspect
import json

import mcp_tools
from mcp_tools import MCP_TOOLS
from mcp_tools._execute import _execute_tool

# 도구별 테스트 인자 (필수 파라미터 충족용). 없으면 {} 로 호출.
KR = "005930"   # 삼성전자
US = "NVDA"
ARGS = {
    "get_stock_detail": {"ticker": KR},
    "get_supply": {"ticker": KR, "mode": "daily"},
    "get_consensus": {"ticker": KR},
    "get_news": {"ticker": KR},
    "get_dart": {"ticker": KR},
    "get_alpha_metrics": {"ticker": KR},
    "get_broker": {"ticker": KR},
    "get_market_signal": {"ticker": KR, "mode": "credit"},
    "get_change_scan": {"preset": "golden_cross"},
    "get_scan": {"preset": "value"},
    "get_finance_rank": {},
    "get_highlow": {},
    "get_rank": {"type": "price"},
    "get_sector": {},
    "get_macro": {},
    "get_macro_external": {},
    "get_polymarket": {},
    "get_regime": {},
    "get_portfolio": {},
    "get_portfolio_history": {},
    "get_trade_stats": {},
    "get_alerts": {},
    "get_backtest": {"ticker": KR, "strategy": "ma_cross"},
    "get_pension_flow": {"ticker": KR},
    "get_sec_filings": {"ticker": US},
    "get_us_ratings": {"ticker": US, "mode": "consensus"},
    "get_us_scan": {"mode": "watchlist"},
    "get_us_analyst": {},
    "get_us_buy_candidates": {},
    "get_us_analyst_research": {"ticker": US},
    "get_us_earnings_transcript": {"ticker": US, "year": 2025, "quarter": 1},
    "get_youtube_transcript": {"url": "ec61o-JTEnU", "max_chars": 200},
    "manage_watch": {},
    "manage_report": {"action": "list", "ticker": KR},
    "list_files": {"path": "."},
    "read_file": {"path": "requirements.txt"},
    "read_report_pdf": {},               # 인자부족 → ERROR 기대(정상)
    "git_status": {},
    "git_log": {},
    "git_diff": {},
    "backup_data": {"action": "status"},
    "simulate_trade": {},                # 인자부족 → ERROR 기대(정상)
    "get_watch": {},
    # 변이형(mutating)은 안전 모드/무인자로 ERROR 유도해 stub 여부만 확인:
    "set_alert": {},                     # 무인자 → ERROR 기대(정상, stub면 NULL)
    "watch_analyst": {},                 # 무인자 → ERROR 기대
    "write_file": {},                    # 무인자 → ERROR 기대
    "git_commit": {},                    # 무인자 → ERROR 기대
    "git_push": {},                      # 실제 push? → 아래 SKIP 처리
}

# 실제 부작용 있어 라이브 호출 제외 (코드 점검으로 대체)
SKIP_LIVE = {"git_push"}


async def main():
    names = [t["name"] for t in MCP_TOOLS]
    print(f"총 MCP_TOOLS 스키마: {len(names)}개\n")

    # 토큰 1회 발급 (token 필요한 핸들러용)
    token = None
    try:
        from kis_api import get_kis_token
        token = await get_kis_token()
    except Exception as e:
        print(f"[warn] 토큰 발급 실패: {e}\n")

    results = {"NULL_STUB": [], "EXCEPTION": [], "ERROR": [], "OK": [], "SKIP": []}
    for name in names:
        if name in SKIP_LIVE:
            results["SKIP"].append(name)
            print(f"  SKIP   {name} (mutating — 코드점검 대체)")
            continue
        args = ARGS.get(name, {})
        try:
            r = await _execute_tool(name, args)
        except Exception as e:
            results["EXCEPTION"].append((name, f"{type(e).__name__}: {e}"))
            print(f"  ☠ EXC  {name}: {type(e).__name__}: {str(e)[:90]}")
            continue
        if r is None:
            results["NULL_STUB"].append(name)
            print(f"  🔴NULL {name} ← STUB 핸들러!")
        elif isinstance(r, dict) and "error" in r and len(r) <= 3:
            results["ERROR"].append((name, str(r.get("error"))[:80]))
            print(f"  ⚠ ERR  {name}: {str(r.get('error'))[:70]}")
        else:
            sz = len(r) if isinstance(r, (list, dict)) else 0
            results["OK"].append(name)
            print(f"  ✅ OK   {name} (keys/items={sz})")

    print("\n" + "=" * 50)
    print(f"OK={len(results['OK'])}  ERROR={len(results['ERROR'])}  "
          f"NULL_STUB={len(results['NULL_STUB'])}  EXCEPTION={len(results['EXCEPTION'])}  "
          f"SKIP={len(results['SKIP'])}")
    if results["NULL_STUB"]:
        print(f"\n🔴 STUB 핸들러 (youtube 류 치명): {results['NULL_STUB']}")
    if results["EXCEPTION"]:
        print(f"\n☠ 크래시 핸들러:")
        for n, e in results["EXCEPTION"]:
            print(f"    {n}: {e}")
    if results["ERROR"]:
        print(f"\n⚠ ERROR 반환 (정상 방어인지 확인 필요):")
        for n, e in results["ERROR"]:
            print(f"    {n}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
