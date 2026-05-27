"""main_pkg schedule — 40+ jq.run_daily / jq.run_repeating 등록.
auto-split from main.py main() 함수 스케줄 블록.
"""
from datetime import time as dtime
from kis_api import KST, ET

# ── jobs imports ──
from main_pkg.jobs.kr_summary import daily_kr_summary
from main_pkg.jobs.us_summary import us_market_summary
from main_pkg.jobs.stoploss import check_stoploss
from main_pkg.jobs.anomaly import check_anomaly
from main_pkg.jobs.momentum import check_supply_drain, momentum_exit_check
from main_pkg.jobs.weekly_review import weekly_review, snapshot_and_drawdown
from main_pkg.jobs.consensus import weekly_consensus_update, daily_consensus_check
from main_pkg.jobs.change_scan import daily_change_scan_alert, auto_backup
from main_pkg.jobs.universe import weekly_universe_update
from main_pkg.jobs.earnings import (
    check_earnings_calendar, check_us_earnings_calendar, check_dividend_calendar
)
from main_pkg.jobs.collect import daily_collect_job, daily_collect_sanity_check
from main_pkg.jobs.financial import weekly_financial_job
from main_pkg.jobs.dart_inc import daily_dart_incremental, daily_dart_disclosure_collect
from main_pkg.jobs.reports import collect_reports_daily
from main_pkg.jobs.macro_job import macro_dashboard
from main_pkg.jobs.dart_check import check_dart_disclosure
from main_pkg.jobs.insider import check_insider_cluster
from main_pkg.jobs.watch_change import watch_change_detect
from main_pkg.jobs.regime import regime_transition_alert
from main_pkg.jobs.sunday import sunday_30_reminder
from main_pkg.jobs.pension import (
    daily_pension_collect, daily_nps_dart_increment,
    weekly_nps_collect, daily_pension_alert,
)
from main_pkg.jobs.events import (
    daily_event_d1_alert,
    weekly_sat_port_check_notify, weekly_sun_discovery_notify,
    weekly_report_digest_notify,
)
# US ratings jobs are in telegram_bot.py (same scope as other weekly US jobs)
from main_pkg.telegram_bot import (
    daily_us_rating_scan, weekly_us_ratings_universe_scan,
    weekly_us_analyst_sync, hourly_us_holdings_check,
    weekly_us_analyst_report, weekly_sanity_check, weekly_log_rotate,
)


def register_all_schedules(jq):
    """app.job_queue(jq)에 40+ 잡을 등록한다.

    PTB days= 매핑 가드 (학습 #31):
    PTB v19→v20 에서 JobQueue.run_daily(days=...) 매핑이 (0=mon~6=sun)에서
    (0=sun~6=sat)로 변경됨. 향후 v22 등 메이저 업그레이드 시 재발 방지를
    위해 startup 에 assert. 매핑이 바뀌면 즉시 크래시 → 즉시 발견.
    """
    from telegram.ext import JobQueue as _JQ_AssertGuard
    _PTB_EXPECTED = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
    assert _JQ_AssertGuard._CRON_MAPPING == _PTB_EXPECTED, (
        f"[CRITICAL] PTB days= 매핑 변경 감지. 기대: {_PTB_EXPECTED}, "
        f"실제: {_JQ_AssertGuard._CRON_MAPPING}. schedule.py days= 튜플 전체 audit 필요."
    )

    # ── 반복 잡 ──
    jq.run_repeating(check_stoploss, interval=600, first=60, name="stoploss")
    jq.run_repeating(check_anomaly, interval=1800, first=120, name="anomaly")
    jq.run_repeating(check_dart_disclosure, interval=300, first=180, name="dart")
    jq.run_repeating(regime_transition_alert, interval=3600, first=300, name="regime_transition")

    # ── 일일/주간 잡 ──
    jq.run_daily(daily_kr_summary, time=dtime(15, 40, tzinfo=KST), days=(1,2,3,4,5), name="kr_summary")
    jq.run_daily(us_market_summary, time=dtime(5,  5, tzinfo=KST), days=(2,3,4,5,6), name="us_summary_dst")
    jq.run_daily(us_market_summary, time=dtime(6,  5, tzinfo=KST), days=(2,3,4,5,6), name="us_summary_std")
    jq.run_daily(check_supply_drain,   time=dtime(15, 42, tzinfo=KST), days=(1,2,3,4,5), name="supply_drain")  # +2m (rate limit stagger)
    jq.run_daily(momentum_exit_check,  time=dtime(16, 30, tzinfo=KST), days=(1,2,3,4,5), name="momentum_check")
    jq.run_daily(snapshot_and_drawdown, time=dtime(15, 50, tzinfo=KST), days=(1,2,3,4,5), name="snapshot_dd")
    jq.run_daily(weekly_review,           time=dtime(7,  0, tzinfo=KST), days=(6,), name="weekly")
    jq.run_daily(weekly_universe_update,  time=dtime(7,  1, tzinfo=KST), days=(1,), name="universe_update")   # +1m (rate limit stagger)
    jq.run_daily(weekly_consensus_update, time=dtime(7,  5, tzinfo=KST), days=(0,), name="consensus_update")
    jq.run_daily(daily_consensus_check,  time=dtime(19, 30, tzinfo=KST), days=(1,2,3,4,5), name="daily_consensus")
    jq.run_daily(daily_change_scan_alert, time=dtime(19,  5, tzinfo=KST), days=(1,2,3,4,5), name="daily_change_scan")
    jq.run_daily(auto_backup,            time=dtime(22, 0, tzinfo=KST), name="auto_backup")
    jq.run_daily(macro_dashboard, time=dtime(18, 55, tzinfo=KST), name="macro_pm")
    jq.run_daily(macro_dashboard, time=dtime(6,  0, tzinfo=KST), name="macro_am")
    jq.run_daily(check_earnings_calendar,  time=dtime(7,  2, tzinfo=KST), days=(1,2,3,4,5), name="earnings_cal")  # +2m (rate limit stagger)
    jq.run_daily(check_dividend_calendar,  time=dtime(7,  3, tzinfo=KST), days=(1,2,3,4,5), name="dividend_cal")  # +3m (rate limit stagger)
    jq.run_daily(check_us_earnings_calendar, time=dtime(7, 10, tzinfo=KST), days=(1,2,3,4,5), name="us_earnings_cal")
    jq.run_daily(collect_reports_daily,    time=dtime(8, 30, tzinfo=KST), days=(1,2,3,4,5), name="report_collect")
    jq.run_daily(daily_collect_job,       time=dtime(18, 30, tzinfo=KST), days=(1,2,3,4,5), name="daily_collect")
    jq.run_daily(daily_collect_sanity_check, time=dtime(19, 15, tzinfo=KST), days=(1,2,3,4,5), name="collect_sanity_1")
    jq.run_daily(daily_collect_sanity_check, time=dtime(20, 15, tzinfo=KST), days=(1,2,3,4,5), name="collect_sanity_2")
    jq.run_daily(daily_collect_sanity_check, time=dtime(21, 15, tzinfo=KST), days=(1,2,3,4,5), name="collect_sanity_3")
    jq.run_daily(daily_collect_sanity_check, time=dtime(22, 15, tzinfo=KST), days=(1,2,3,4,5), name="collect_sanity_4")
    jq.run_daily(daily_us_rating_scan,    time=dtime(7, 30, tzinfo=KST), days=(0,1,2,3,4,5,6), name="us_ratings")
    jq.run_daily(weekly_us_ratings_universe_scan, time=dtime(3, 0, tzinfo=KST), days=(0,), name="weekly_us_harvest")
    jq.run_daily(weekly_us_analyst_sync,        time=dtime(4, 0, tzinfo=KST), days=(0,), name="weekly_us_analyst_sync")
    jq.run_daily(weekly_nps_collect,            time=dtime(3, 30, tzinfo=KST), days=(0,), name="weekly_nps")
    jq.run_daily(daily_nps_dart_increment, time=dtime(4, 0, tzinfo=KST), name="nps_dart_inc")
    jq.run_daily(daily_dart_disclosure_collect, time=dtime(4, 5, tzinfo=KST), days=(0,1,2,3,4,5,6), name="dart_disclosure")
    jq.run_daily(hourly_us_holdings_check, time=dtime(12, 0, tzinfo=ET), days=(1,2,3,4,5), name="us_holdings_noon")
    jq.run_daily(hourly_us_holdings_check, time=dtime(16, 30, tzinfo=ET), days=(1,2,3,4,5), name="us_holdings_close")
    jq.run_daily(weekly_us_analyst_report, time=dtime(19, 0, tzinfo=KST), days=(0,), name="weekly_us_analyst")
    jq.run_daily(weekly_financial_job,    time=dtime(7,  15, tzinfo=KST), days=(0,),         name="weekly_financial")
    jq.run_daily(daily_dart_incremental,  time=dtime(2,  0, tzinfo=KST),                     name="dart_incremental")
    jq.run_daily(watch_change_detect,     time=dtime(19, 0, tzinfo=KST), days=(1,2,3,4,5), name="watch_change")
    jq.run_daily(check_insider_cluster,   time=dtime(20, 0, tzinfo=KST), days=(1,2,3,4,5), name="insider_cluster")
    jq.run_daily(sunday_30_reminder,      time=dtime(19, 0, tzinfo=KST), days=(0,), name="sunday_30")
    jq.run_daily(weekly_sat_port_check_notify, time=dtime(9, 0, tzinfo=KST), days=(6,), name="weekly_sat_port_check")
    jq.run_daily(weekly_sun_discovery_notify,  time=dtime(9, 0, tzinfo=KST), days=(0,), name="weekly_sun_discovery")
    jq.run_daily(daily_event_d1_alert, time=dtime(19, 30, tzinfo=KST), days=(0, 1, 2, 3, 4, 5, 6), name="event_d1")
    jq.run_daily(daily_pension_collect, time=dtime(16, 32, tzinfo=KST), days=(1, 2, 3, 4, 5), name="pension_collect")  # +2m (rate limit stagger)
    jq.run_daily(daily_pension_alert,   time=dtime(19,  0, tzinfo=KST), days=(1, 2, 3, 4, 5), name="pension_alert")
    jq.run_daily(weekly_report_digest_notify, time=dtime(19, 7, tzinfo=KST), days=(0,), name="weekly_report_digest")
    jq.run_daily(weekly_sanity_check,     time=dtime(7,  5, tzinfo=KST), days=(0,), name="weekly_sanity")
    jq.run_daily(weekly_log_rotate,       time=dtime(23, 30, tzinfo=KST), days=(0,), name="weekly_log_rotate")
