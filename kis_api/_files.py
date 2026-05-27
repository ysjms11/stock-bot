"""파일 저장/로드 + 환경변수 기반 데이터 복원."""
import os
import json
import fcntl
import tempfile
from datetime import datetime

from ._config import (
    KST,
    WATCHLIST_FILE, STOPLOSS_FILE, US_WATCHLIST_FILE, DART_SEEN_FILE,
    PORTFOLIO_FILE, WATCHALERT_FILE, WATCH_SENT_FILE, STOPLOSS_SENT_FILE,
    US_HOLDINGS_SENT_FILE, DECISION_LOG_FILE, COMPARE_LOG_FILE,
    WATCHLIST_LOG_FILE, EVENTS_FILE, WEEKLY_BASE_FILE, UNIVERSE_FILE,
    CONSENSUS_CACHE_FILE, PORTFOLIO_HISTORY_FILE, TRADE_LOG_FILE,
    SECTOR_FLOW_CACHE_FILE, SECTOR_ROTATION_FILE, SUPPLY_HISTORY_FILE,
    REPORTS_FILE, REGIME_STATE_FILE, MACRO_SENT_FILE,
)
from ._helpers import _is_us_ticker

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 환경변수 기반 데이터 복원 (Railway Volume 미마운트 시 fallback)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_BACKUP_MAP = {
    "BACKUP_PORTFOLIO":    PORTFOLIO_FILE,
    "BACKUP_STOPLOSS":     STOPLOSS_FILE,
    "BACKUP_WATCHALERT":   WATCHALERT_FILE,
    "BACKUP_DECISION_LOG": DECISION_LOG_FILE,
    "BACKUP_COMPARE_LOG":  COMPARE_LOG_FILE,
    "BACKUP_EVENTS":       EVENTS_FILE,
    "BACKUP_WEEKLY_BASE":  WEEKLY_BASE_FILE,
}

for _env_key, _filepath in _BACKUP_MAP.items():
    if not os.path.exists(_filepath):
        _backup_val = os.environ.get(_env_key, "")
        if _backup_val:
            try:
                _data = json.loads(_backup_val)
                with open(_filepath, "w", encoding="utf-8") as _f:
                    json.dump(_data, _f, ensure_ascii=False, indent=2)
                print(f"[복원] {_filepath} ← 환경변수 {_env_key}")
            except Exception as _e:
                print(f"[복원 실패] {_env_key}: {_e}")

# 레거시 환경변수 가드
if os.path.exists(WATCHALERT_FILE):
    for _legacy_env in ("BACKUP_WATCHLIST", "BACKUP_US_WATCHLIST"):
        if os.environ.get(_legacy_env):
            print(f"[무시] {_legacy_env} (watchalert.json 단일 소스 사용)")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# JSON 저장/로드
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def load_json(filepath, default=None):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        if default is not None:
            save_json(filepath, default)
            return default
        return {}


def save_json(filepath, data):
    """Atomic write: temp file 생성 → fsync → atomic rename.
    동시 read/write 경합 방지 (read는 이전 상태 또는 새 상태만 봄).
    fcntl advisory lock으로 multi-writer race 제거."""
    filepath = str(filepath)
    dir_path = os.path.dirname(os.path.abspath(filepath))
    lock_path = filepath + ".lock"

    with open(lock_path, "w") as _lock:
        fcntl.flock(_lock.fileno(), fcntl.LOCK_EX)
        try:
            fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=dir_path)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, filepath)  # POSIX atomic rename
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        finally:
            fcntl.flock(_lock.fileno(), fcntl.LOCK_UN)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 워치리스트 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━

_DEFAULT_KR_WATCH = {
    "009540": "HD한국조선해양", "298040": "효성중공업",
    "010120": "LS ELECTRIC", "267260": "HD현대일렉트릭",
    "034020": "두산에너빌리티",
}


def load_watchlist():
    """하위호환 wrapper: watchalert 기반 {ticker: name}.
    watchalert.json 존재 시 그 내용을 그대로 반환 (빈 dict라도).
    파일 자체가 없으면 최초 실행이므로 기본 5종목 seed."""
    if os.path.exists(WATCHALERT_FILE):
        return load_kr_watch_dict()
    return dict(_DEFAULT_KR_WATCH)


def load_stoploss():
    return load_json(STOPLOSS_FILE, {})


_DEFAULT_US_WATCH = {
    "TSLA": {"name": "테슬라", "qty": 12},
    "CRSP": {"name": "크리스퍼", "qty": 70},
    "AMD": {"name": "AMD", "qty": 17},
    "LITE": {"name": "루멘텀", "qty": 4},
}


def load_us_watchlist():
    """하위호환 wrapper: watchalert 기반 {ticker: {name, qty}}.
    watchalert.json 존재 시 그 내용을 그대로 반환 (빈 dict라도).
    파일 자체가 없으면 최초 실행이므로 기본 US seed."""
    if os.path.exists(WATCHALERT_FILE):
        return load_us_watch_dict()
    return dict(_DEFAULT_US_WATCH)


def load_dart_seen():
    return load_json(DART_SEEN_FILE, {"ids": []})


def load_watchalert():
    return load_json(WATCHALERT_FILE, {})


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 워치리스트 단일화 헬퍼 (watchalert.json 기반)
# market 필드 없으면 _is_us_ticker()로 자동 추론
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _wa_market(ticker: str, entry: dict) -> str:
    m = (entry or {}).get("market")
    if m in ("KR", "US"):
        return m
    return "US" if _is_us_ticker(ticker) else "KR"


def load_kr_watch_tickers() -> list:
    """watchalert에서 market==KR 종목 코드 리스트."""
    wa = load_watchalert()
    return [t for t, v in wa.items() if _wa_market(t, v) == "KR"]


def load_us_watch_tickers() -> list:
    """watchalert에서 market==US 종목 코드 리스트."""
    wa = load_watchalert()
    return [t for t, v in wa.items() if _wa_market(t, v) == "US"]


def load_kr_watch_dict() -> dict:
    """구 watchlist.json 호환 형식 {ticker: name}."""
    wa = load_watchalert()
    return {t: (v.get("name") or t) for t, v in wa.items() if _wa_market(t, v) == "KR"}


def load_us_watch_dict() -> dict:
    """구 us_watchlist.json 호환 형식 {ticker: {name, qty}}."""
    wa = load_watchalert()
    return {
        t: {"name": v.get("name") or t, "qty": int(v.get("qty") or 0)}
        for t, v in wa.items()
        if _wa_market(t, v) == "US"
    }


def load_decision_log():
    return load_json(DECISION_LOG_FILE, {})


def load_trade_log() -> list:
    return load_json(TRADE_LOG_FILE, {"trades": []}).get("trades", [])


def save_trade_log(trades: list):
    if len(trades) > 1000:
        trades = trades[-1000:]
    save_json(TRADE_LOG_FILE, {"trades": trades})


def get_trade_stats(period: str = "month") -> dict:
    """매매 기록 성과 분석.
    period: 'month'=이번달, 'quarter'=이번분기, 'year'=올해, 'all'=전체"""
    from datetime import datetime as _dt
    now = _dt.now()

    if period == "month":
        cutoff = now.strftime("%Y-%m")
        label = now.strftime("%Y-%m")
    elif period == "quarter":
        q_start = ((now.month - 1) // 3) * 3 + 1
        cutoff = f"{now.year}-{q_start:02d}-01"
        label = f"{now.year}Q{(now.month - 1) // 3 + 1}"
    elif period == "year":
        cutoff = f"{now.year}-01-01"
        label = str(now.year)
    else:
        cutoff = "0000"
        label = "전체"

    all_trades = load_trade_log()

    if period == "month":
        sells = [t for t in all_trades if t.get("side") == "sell" and t.get("date", "").startswith(cutoff)]
    elif period == "all":
        sells = [t for t in all_trades if t.get("side") == "sell"]
    else:
        sells = [t for t in all_trades if t.get("side") == "sell" and t.get("date", "") >= cutoff]

    total  = len(sells)
    wins   = sum(1 for t in sells if t.get("result") == "win")
    losses = sum(1 for t in sells if t.get("result") == "loss")
    total_pnl = sum(t.get("pnl", 0) or 0 for t in sells)
    win_rate  = round(wins / total * 100, 1) if total > 0 else None
    avg_pnl   = round(total_pnl / total)     if total > 0 else None

    with_pnl = [t for t in sells if t.get("pnl_pct") is not None]
    best  = max(with_pnl, key=lambda x: x.get("pnl_pct", 0), default=None)
    worst = min(with_pnl, key=lambda x: x.get("pnl_pct", 0), default=None)

    def _brief(t):
        if not t:
            return None
        return {"id": t.get("id"), "ticker": t.get("ticker"), "name": t.get("name"),
                "pnl": t.get("pnl"), "pnl_pct": t.get("pnl_pct"),
                "holding_days": t.get("holding_days"), "date": t.get("date")}

    hold_days = [t.get("holding_days") for t in sells if t.get("holding_days") is not None]
    avg_hold  = round(sum(hold_days) / len(hold_days), 1) if hold_days else None

    # 등급별 정확도
    grade_acc: dict = {}
    for t in sells:
        g = (t.get("grade_at_trade") or "?").upper()
        if g not in grade_acc:
            grade_acc[g] = {"total": 0, "wins": 0, "win_rate": 0.0}
        grade_acc[g]["total"] += 1
        if t.get("result") == "win":
            grade_acc[g]["wins"] += 1
    for d in grade_acc.values():
        d["win_rate"] = round(d["wins"] / d["total"] * 100, 1) if d["total"] > 0 else 0.0

    # 연속 손실 (최근부터)
    consecutive_losses = 0
    for t in reversed(sells):
        if t.get("result") == "loss":
            consecutive_losses += 1
        else:
            break

    return {
        "period": label,
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "total_pnl": round(total_pnl),
        "avg_pnl_per_trade": avg_pnl,
        "best_trade": _brief(best),
        "worst_trade": _brief(worst),
        "avg_holding_days": avg_hold,
        "grade_accuracy": grade_acc,
        "consecutive_losses": consecutive_losses,
        "trades": sells,
    }


def load_consensus_cache() -> dict:
    """consensus_cache.json 로드. 없으면 {} 반환."""
    return load_json(CONSENSUS_CACHE_FILE, {})


def load_sector_flow_cache() -> dict:
    """sector_flow_cache.json 로드. 없으면 {} 반환."""
    return load_json(SECTOR_FLOW_CACHE_FILE, {})


def save_sector_flow_cache(data: dict):
    save_json(SECTOR_FLOW_CACHE_FILE, data)


def load_compare_log():
    return load_json(COMPARE_LOG_FILE, [])


def load_watchlist_log() -> list:
    return load_json(WATCHLIST_LOG_FILE, [])


def append_watchlist_log(entry: dict):
    log = load_watchlist_log()
    log.append(entry)
    if len(log) > 200:
        log = log[-200:]
    save_json(WATCHLIST_LOG_FILE, log)


def load_events() -> dict:
    return load_json(EVENTS_FILE, {})
