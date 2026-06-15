"""KIS API 패키지 — 기존 `from kis_api import *` 호환.

분할 구조:
  _config.py    — 환경변수 & 경로 상수
  _session.py   — aiohttp 공유 세션 + KIS 토큰 캐시 + _kis_get 래퍼
  _helpers.py   — 티커 판별/거래소 추정/감성분석 데이터
  _files.py     — JSON I/O + 워치리스트/포트폴리오/로그 파일 함수
  consensus.py  — FnGuide/Nasdaq 컨센서스
  portfolio.py  — 포트폴리오 스냅샷 + 드로다운
  kr_stock.py   — KIS 국내 31 TR_ID
  us_stock.py   — KIS 해외 + Yahoo Finance + 볼륨 프로파일
  ranks.py      — 시간외/거래원/배당 순위
  universe.py   — 종목 유니버스
  websocket.py  — KisRealtimeManager + WebSocket
  macro.py      — 매크로 대시보드
  dart.py       — DART 공시/내부자거래
  us_ratings.py — 미국 애널 레이팅
  backup.py     — GitHub Gist 백업
  news.py       — 뉴스 + 감성분석 + 매크로 신호
  regime.py     — 시장 국면 판단
  fmp.py        — FMP API + YouTube 자막
  polymarket.py — Polymarket + Treasury
  pension.py    — 연기금(NPS)
  sec_edgar.py  — SEC EDGAR 1차 공시 (8-K/F-1/S-1/424B/EFFECT)
"""

# ━━ 설정/상수 ━━
from ._config import (
    TELEGRAM_TOKEN, CHAT_ID, KIS_APP_KEY, KIS_APP_SECRET, DART_API_KEY,
    KIS_BASE_URL, DART_BASE_URL, KST, ET, _DATA_DIR, _DB_PATH,
    WATCHLIST_FILE, STOPLOSS_FILE, US_WATCHLIST_FILE, DART_SEEN_FILE,
    PORTFOLIO_FILE, WATCHALERT_FILE, WATCH_SENT_FILE, STOPLOSS_SENT_FILE,
    US_HOLDINGS_SENT_FILE, DECISION_LOG_FILE, COMPARE_LOG_FILE,
    WATCHLIST_LOG_FILE, EVENTS_FILE, WEEKLY_BASE_FILE, UNIVERSE_FILE,
    CONSENSUS_CACHE_FILE, PORTFOLIO_HISTORY_FILE, TRADE_LOG_FILE,
    SECTOR_FLOW_CACHE_FILE, SECTOR_ROTATION_FILE, SUPPLY_HISTORY_FILE,
    REPORTS_FILE, REGIME_STATE_FILE, MACRO_SENT_FILE, TOKEN_CACHE_FILE,
    SIGNAL_FEED_FILE,
    GITHUB_TOKEN, _BACKUP_GIST_ENV, _BACKUP_FILES_LIST, MACRO_SYMBOLS,
    SILENT_FAILURE_LOG, DART_TELEGRAM_TOKEN, DART_CHAT_ID,
)

# ━━ 세션 + 토큰 ━━
from ._session import (
    _get_session, close_session, _token_cache,
    get_kis_token, _kis_headers, _kis_get,
)

# ━━ 헬퍼 ━━
from ._helpers import (
    _is_us_ticker, _guess_excd, _NYSE_TICKERS, _AMEX_TICKERS,
    _is_us_market_hours_kst, _is_us_market_closed,
    DART_KEYWORDS, _load_knu_senti_lex,
    _FINANCE_PHRASE_SCORES, _RANKING_RE,
    _US_POSITIVE_KEYWORDS, _US_NEGATIVE_KEYWORDS,
)

# ━━ 파일 I/O ━━
from ._files import (
    load_json, save_json, _BACKUP_MAP,
    load_watchlist, load_stoploss, load_us_watchlist,
    load_dart_seen, load_watchalert,
    _wa_market, load_kr_watch_tickers, load_us_watch_tickers,
    load_kr_watch_dict, load_us_watch_dict,
    load_decision_log, load_trade_log, save_trade_log, get_trade_stats,
    load_consensus_cache, load_sector_flow_cache, save_sector_flow_cache,
    load_compare_log, load_watchlist_log, append_watchlist_log, load_events,
    append_signal, load_signal_feed,
)

# ━━ 컨센서스 ━━
from .consensus import (
    _recom_label, fetch_fnguide_consensus, get_us_consensus,
    _insert_consensus_history, update_consensus_cache, detect_consensus_changes,
)

# ━━ 포트폴리오 스냅샷/드로다운 ━━
from .portfolio import (
    save_portfolio_snapshot, _fetch_us_price_simple, check_drawdown,
)

# ━━ 국내 KIS ━━
from .kr_stock import (
    get_investor_trend, get_volume_rank, get_kis_index,
    kis_stock_price, kis_stock_info, kis_investor_trend, kis_credit_balance,
    kis_short_selling, kis_volume_rank_api, kis_foreigner_trend, kis_sector_price,
    WI26_SECTORS, _TICKER_SECTOR,
    _fetch_market_investor_flow, _fetch_sector_flow, detect_sector_rotation,
    _previous_trading_day, kis_investor_trend_history, save_supply_snapshot,
    get_historical_ohlcv, compute_volume_profile, get_historical_supply,
    kis_daily_volumes, check_momentum_exit, batch_stock_detail,
    kis_program_trade_today, kis_investor_trend_estimate,
    kis_foreign_institution_total, kis_daily_short_sale, kis_news_title,
    analyze_news_sentiment, kis_vi_status, kis_volume_power_rank,
    kis_finance_ratio_rank, kis_near_new_highlow, kis_inquire_member,
    kis_daily_credit_balance, kis_daily_loan_trans, kis_overtime_price,
    kis_overtime_daily, kis_income_statement, kis_balance_sheet, kis_asking_price,
)

# ━━ 해외 KIS + Yahoo Finance ━━
from .us_stock import (
    kis_us_stock_price, kis_us_stock_detail, kis_fluctuation_rank,
    get_yahoo_quote,
)

# ━━ 순위 ━━
from .ranks import (
    kis_overtime_fluctuation, kis_traded_by_company, kis_dividend_rate_rank,
    kis_us_updown_rate, kis_estimate_perform, kis_dividend_schedule,
)

# ━━ 유니버스 ━━
from .universe import (
    get_stock_universe, fetch_universe_from_krx, kis_daily_closes,
)

# ━━ WebSocket ━━
from .websocket import (
    _ws_key_cache, get_kis_ws_approval_key, KisRealtimeManager, ws_manager,
    get_ws_tickers,
)

# ━━ 매크로 ━━
from .macro import (
    _DEFAULT_EVENTS, collect_macro_data, format_macro_msg, judge_regime,
)

# ━━ DART ━━
from .dart import (
    DART_REPORTS_DIR,
    search_dart_disclosures, filter_important_disclosures,
    build_dart_corp_map, get_dart_corp_map,
    dart_quarterly_op, _dart_amt_to_int, _dart_acct_match, dart_quarterly_full,
    dart_shares_outstanding, kis_elestock, _to_int_safe, _to_float_safe,
    upsert_insider_transactions, aggregate_insider_cluster,
    collect_insider_for_tickers, load_corp_codes, _download_corp_codes,
    _report_name_priority, search_dart_reports, _parse_rpt_nm,
    search_dart_periodic_new, fetch_dart_document, list_disclosures_for_ticker,
    fetch_and_cache_disclosure, _fmt_krw_amount, _parse_pct as _dart_parse_pct,
    _detect_krw_unit, parse_disclosure_summary,
    _parse_earnings_preview, _parse_buyback, _parse_dividend, _parse_rumor,
    _report_file_exists, save_dart_report, read_dart_report, list_dart_reports,
)

# ━━ 미국 애널 레이팅 ━━
from .us_ratings import (
    _stockanalysis_ratings, _normalize_stockanalysis_response,
    _save_us_ratings_to_db, _save_consensus_snapshot,
    _fetch_index_tickers_from_wikipedia, _load_index_tickers,
    _fetch_sp500_from_wikipedia, _fetch_russell1000_from_wikipedia,
    load_sp500_tickers, load_russell1000_tickers, load_us_scan_universe,
    _fetch_analyst_coverage_html, _upsert_analyst_meta,
    build_top_analysts_candidates, fetch_and_store_analyst_meta,
)

# ━━ 백업 ━━
from .backup import (
    _load_us_holdings_sent, _save_us_holdings_sent,
    _gist_patch_with_retry, backup_data_files, restore_data_files, get_backup_status,
)

# ━━ 뉴스 + 감성분석 + 매크로 신호 ━━
from .news import (
    fetch_news, fetch_us_news, analyze_us_news_sentiment,
    fetch_us_earnings_calendar, fetch_us_sector_etf, fetch_us_short_interest,
    _yf_history, _krx_kospi_history, _krx_foreign_net,
    _calc_zscore, _rolling_ma_pct, _rolling_momentum, _realized_vol,
    _rolling_realized_vol, _sig_entry, compute_us_signals, compute_kr_signals,
)

# ━━ 시장 국면 ━━
from .regime import (
    _calc_regime_v2, _regime_emoji, cmd_regime,
    calc_kr_regime, calc_us_regime, _apply_regime_debounce,
)

# ━━ FMP + YouTube ━━
from .fmp import (
    _extract_youtube_id, fetch_youtube_transcript,
    fmp_earnings_transcript, fmp_price_target_summary,
    fmp_analyst_estimates, fmp_stock_grades,
)

# ━━ Polymarket + Treasury + pension_flow ━━
from .polymarket import (
    fetch_polymarket, fetch_treasury_curve,
    _ensure_pension_table, collect_pension_flow_daily, fetch_pension_fund_flow,
    fetch_external_macro_signals,
)

# ━━ 연기금(NPS) ━━
from .pension import (
    _normalize_company_name, _ensure_nps_holdings_table,
    _discover_nps_atch_file_id, _download_nps_5pct_csv,
    _date_to_quarter, _build_name_to_symbol_map, _match_company_to_symbol,
    collect_nps_5percent_disclosed, fetch_nps_holdings,
    _ensure_nps_us_table, _period_to_quarter, _sec_fetch_text,
    _sec_list_nps_13f_filings, _sec_locate_holdings_xml, _sec_fetch_holdings,
    collect_nps_us_13f, fetch_nps_us_holdings,
    _ensure_nps_kr_full_table,
    _parse_eok, collect_nps_kr_full_from_whale_insight,
    _ensure_wi_change_tables, _parse_int_with_sign, _parse_float_with_sign,
    _fetch_wi_js_array, _ensure_dart_change_tables,
    _dart_get, _dart_list_disclosures,
    collect_nps_dart_increments, collect_dart_5pct_changes,
    collect_dart_10pct_insiders, collect_wi_changes, fetch_nps_kr_full_holdings,
)

# ━━ SEC EDGAR 1차 공시 ━━
from .sec_edgar import (
    ensure_cik_map_loaded, ticker_to_cik, bulk_fetch_cik_map,
    get_company_filings,
    upsert_sec_filings, query_sec_filings,
    FILING_FORMS_CRITICAL, FILING_FORMS_WATCH, FILING_FORMS_DEFAULT,
)
