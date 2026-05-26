# mcp_tools/_registry.py — 45 tool handlers dispatch dict
"""
각 tool 핸들러를 import하여 TOOL_HANDLERS dict에 등록.
handle_xxx(arguments, token=None) -> dict | list 시그니처 통일.
"""

from .tools.price import handle_get_rank, handle_get_stock_detail
from .tools.portfolio import (
    handle_get_portfolio, handle_get_portfolio_history,
    handle_get_trade_stats, handle_simulate_trade,
)
from .tools.alerts import handle_get_alerts, handle_set_alert, handle_manage_watch
from .tools.supply import handle_get_supply, handle_get_pension_flow
from .tools.dart import handle_get_dart
from .tools.macro import handle_get_macro, handle_get_polymarket, handle_get_macro_external
from .tools.sector import handle_get_sector
from .tools.consensus import handle_get_consensus
from .tools.market_signal import handle_get_market_signal, handle_get_alpha_metrics
from .tools.news import handle_get_news
from .tools.backtest import handle_get_backtest, handle_backup_data
from .tools.regime import handle_get_regime
from .tools.scan import (
    handle_get_scan, handle_get_change_scan,
    handle_get_finance_rank, handle_get_highlow, handle_get_broker,
)
from .tools.files import (
    handle_read_file, handle_write_file, handle_list_files, handle_read_report_pdf,
)
from .tools.git import (
    handle_git_status, handle_git_diff, handle_git_log,
    handle_git_commit, handle_git_push,
)
from .tools.us import (
    handle_get_us_ratings, handle_get_us_scan, handle_get_us_analyst,
    handle_watch_analyst, handle_get_us_buy_candidates,
    handle_get_us_earnings_transcript, handle_get_us_analyst_research,
)
from .tools.youtube import handle_get_youtube_transcript
from .tools.manage_report import handle_manage_report

TOOL_HANDLERS: dict = {
    "get_rank":                 handle_get_rank,
    "get_portfolio":            handle_get_portfolio,
    "get_stock_detail":         handle_get_stock_detail,
    "get_supply":               handle_get_supply,
    "get_dart":                 handle_get_dart,
    "get_macro":                handle_get_macro,
    "get_sector":               handle_get_sector,
    "get_alerts":               handle_get_alerts,
    "set_alert":                handle_set_alert,
    "manage_watch":             handle_manage_watch,
    "get_market_signal":        handle_get_market_signal,
    "get_news":                 handle_get_news,
    "get_consensus":            handle_get_consensus,
    "get_portfolio_history":    handle_get_portfolio_history,
    "get_trade_stats":          handle_get_trade_stats,
    "backup_data":              handle_backup_data,
    "simulate_trade":           handle_simulate_trade,
    "get_backtest":             handle_get_backtest,
    "manage_report":            handle_manage_report,
    "get_regime":               handle_get_regime,
    "get_scan":                 handle_get_scan,
    "get_finance_rank":         handle_get_finance_rank,
    "get_highlow":              handle_get_highlow,
    "get_broker":               handle_get_broker,
    "get_change_scan":          handle_get_change_scan,
    "read_file":                handle_read_file,
    "write_file":               handle_write_file,
    "list_files":               handle_list_files,
    "git_status":               handle_git_status,
    "git_diff":                 handle_git_diff,
    "git_log":                  handle_git_log,
    "git_commit":               handle_git_commit,
    "git_push":                 handle_git_push,
    "read_report_pdf":          handle_read_report_pdf,
    "get_alpha_metrics":        handle_get_alpha_metrics,
    "get_us_ratings":           handle_get_us_ratings,
    "get_us_scan":              handle_get_us_scan,
    "get_us_analyst":           handle_get_us_analyst,
    "watch_analyst":            handle_watch_analyst,
    "get_us_earnings_transcript": handle_get_us_earnings_transcript,
    "get_us_analyst_research":  handle_get_us_analyst_research,
    "get_polymarket":           handle_get_polymarket,
    "get_macro_external":       handle_get_macro_external,
    "get_pension_flow":         handle_get_pension_flow,
    "get_us_buy_candidates":    handle_get_us_buy_candidates,
    "get_youtube_transcript":   handle_get_youtube_transcript,
}


import inspect as _inspect

# 핸들러 시그니처 캐시 (id → bool)
_HANDLER_SIG_CACHE: dict[int, bool] = {}


def _needs_token(handler) -> bool:
    """핸들러가 token 인자를 받는지 inspect로 자동 판별 + 캐시.

    token=None 기본값이 있더라도 2번째 파라미터가 존재하면 token 전달.
    단, 실제로 token=None 기본값을 가진 핸들러는 KIS 호출 없이 token 없이도 동작하므로
    len(params) >= 2 이면서 token 파라미터가 있을 때만 True.
    """
    h_id = id(handler)
    if h_id in _HANDLER_SIG_CACHE:
        return _HANDLER_SIG_CACHE[h_id]
    sig = _inspect.signature(handler)
    params = list(sig.parameters.keys())
    needs = len(params) >= 2 and params[1] in ("token", "token_")
    _HANDLER_SIG_CACHE[h_id] = needs
    return needs


async def execute_tool(name: str, arguments: dict, token: str = "") -> dict | list:
    """dispatch dict 기반 tool 실행. 핸들러 없으면 error dict 반환."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        if _needs_token(handler):
            if not token:
                from kis_api import get_kis_token
                token = await get_kis_token()
            if not token:
                return {"error": "KIS 토큰 발급 실패"}
            return await handler(arguments, token)
        else:
            return await handler(arguments)
    except TypeError as e:
        return {"error": f"TypeError: {e}", "tool": name}
    except Exception as e:
        return {"error": str(e), "tool": name}
