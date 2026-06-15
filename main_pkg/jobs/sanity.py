"""main_pkg/jobs/sanity.py — KRX 무결성 체크 + 로그 로테이션 잡.

Phase B (2026-06-13): main_pkg/telegram_bot.py 에서 verbatim 추출.
원본 telegram_bot.py 에 re-export 래퍼 유지 (backward-compat).
"""
import os
from datetime import datetime, timedelta

from kis_api import *           # KST, CHAT_ID, UNIVERSE_FILE 등 star-import
from kis_api import _DATA_DIR   # explicit private import
from main_pkg._ctx import _safe_send
from db_collector import _KR_MARKET_HOLIDAYS as _KRX_HOLIDAYS


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 주간 무결성 체크 (일 07:05 KST)
# 최근 영업일 5일 daily_snapshot 누락 시 텔레그램 경고
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_krx_business_day(d) -> bool:
    """KRX 영업일 판정. d: datetime.date 또는 datetime."""
    if hasattr(d, "date"):
        d = d.date()
    if d.weekday() >= 5:  # 토(5)/일(6)
        return False
    return d.strftime("%Y%m%d") not in _KRX_HOLIDAYS


async def weekly_sanity_check(context):
    """매주 일요일 07:05: 최근 영업일 5일 daily_snapshot 존재 확인.
    KRX 공휴일(근로자의 날·신정·설·추석·임시공휴일 등)은 영업일에서 제외.
    당해 _KRX_HOLIDAYS 등록 카운트 부족 시 갱신 알림 (매주 발송 → 잊지 않게).
    """
    try:
        from db_collector import _get_db
        conn = _get_db()
        cur = conn.execute(
            "SELECT trade_date, COUNT(*) FROM daily_snapshot "
            "WHERE trade_date >= ? GROUP BY trade_date ORDER BY trade_date DESC",
            ((datetime.now(KST) - timedelta(days=14)).strftime("%Y%m%d"),)
        )
        rows = cur.fetchall()
        conn.close()
        # 지난 5 영업일 역산 — KRX 공휴일 제외
        bizdays = []
        d = datetime.now(KST).date() - timedelta(days=1)
        # 안전 상한: 14일 뒤로까지 (장기 연휴 대비)
        for _ in range(14):
            if len(bizdays) >= 5:
                break
            if _is_krx_business_day(d):
                bizdays.append(d.strftime("%Y%m%d"))
            d -= timedelta(days=1)
        have = {r[0] for r in rows if r[1] > 1500}
        missing = [b for b in bizdays if b not in have]
        if missing:
            msg = f"⚠️ daily_snapshot 누락 영업일: {', '.join(missing)}"
            await _safe_send(context, msg)

            # 누락 영업일 감지 후 자동 백필 (학습 #28 영구 대응)
            try:
                import json as _json
                from db_collector import backfill_day_via_chart
                universe_data = (_json.load(open(UNIVERSE_FILE))
                                 if os.path.exists(UNIVERSE_FILE) else {})
                tickers = list(universe_data.get("codes", {}).keys())
                if not tickers:
                    print("[catchup] universe 비어있음, 스킵", flush=True)
                else:
                    for d in missing:
                        try:
                            r = await backfill_day_via_chart(d, tickers)
                            print(f"[catchup] {d}: ok={r['ok']} fail={r['fail']}",
                                  flush=True)
                        except Exception as e:
                            print(f"[catchup] {d} 오류: {e}", flush=True)
            except Exception as e:
                print(f"[catchup] 오류: {e}", flush=True)

        # KRX 공휴일 list 연 1회 갱신 알림
        # 정상 한 해 13~16건. 8건 미만이면 list 미갱신/누락으로 간주
        this_year_str = str(datetime.now(KST).year)
        krx_cnt = sum(1 for d in _KRX_HOLIDAYS if d.startswith(this_year_str))
        if krx_cnt < 8:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=(f"📅 KRX 공휴일 list 갱신 필요\n"
                      f"{this_year_str}년 등록: {krx_cnt}건 (정상 13~16건)\n"
                      f"db_collector/_config.py `_KR_MARKET_HOLIDAYS` frozenset 갱신 (단일 소스)\n"
                      f"https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp")
            )

        # 5/9 추가: derived 컬럼 / 별도 테이블 stale 감지 (학습 #28 후속)
        # daily_snapshot row count 만으로는 컬럼/테이블 침묵 영구 0 미감지
        sanity_warnings = []
        try:
            import sqlite3 as _s
            db_path = f"{_DATA_DIR}/stock.db"
            with _s.connect(db_path, timeout=10) as conn:
                conn.execute("PRAGMA cache_size = -65536;")
                conn.execute("PRAGMA temp_store = MEMORY;")
                conn.execute("PRAGMA mmap_size = 268435456;")
                conn.execute("PRAGMA busy_timeout = 30000;")
                # 최신 영업일 종목 총카운트 (mscore/fscore 비율 기준)
                total = conn.execute(
                    "SELECT COUNT(*) FROM daily_snapshot "
                    "WHERE trade_date=(SELECT MAX(trade_date) FROM daily_snapshot)"
                ).fetchone()[0]
                # mscore non-null count (Phase 4 alpha)
                # 비율 기반: 데이터 미수집 (m=0) 시 silent skip,
                # 부분 채워진 (0 < m < 30%) 경우만 경고. critic 5/10 권장.
                m = conn.execute(
                    "SELECT COUNT(*) FROM daily_snapshot "
                    "WHERE trade_date=(SELECT MAX(trade_date) FROM daily_snapshot) "
                    "AND mscore IS NOT NULL"
                ).fetchone()[0]
                if total > 0 and 0 < m < total * 0.3:
                    sanity_warnings.append(
                        f"⚠️ mscore 신선도 낮음: {m}/{total} (30% 임계 미달)"
                    )
                # fscore non-null count — 비율 기반 (20% 임계)
                # 자연 한계: DART 재무제표 있는 종목만 27% (마이크로/우선주/SPAC 제외)
                # 5/10 사용자 알림 후 50% → 20% 조정 (false alarm 방지)
                f = conn.execute(
                    "SELECT COUNT(*) FROM daily_snapshot "
                    "WHERE trade_date=(SELECT MAX(trade_date) FROM daily_snapshot) "
                    "AND fscore IS NOT NULL"
                ).fetchone()[0]
                if total > 0 and 0 < f < total * 0.2:
                    sanity_warnings.append(
                        f"⚠️ fscore 신선도 낮음: {f}/{total} (20% 임계 미달)"
                    )
                # wi_5pct_changes 14일 이내 (분기 보고이므로 여유)
                wi = conn.execute(
                    "SELECT julianday('now') - julianday(MAX(report_date)) "
                    "FROM wi_5pct_changes"
                ).fetchone()[0]
                if wi and wi > 14:
                    sanity_warnings.append(f"⚠️ wi_5pct_changes {wi:.0f}일 stale (기대 <14일)")
                # pension_flow_daily 7일 이내 (평일 매일 갱신)
                pf = conn.execute(
                    "SELECT julianday('now') - julianday(MAX(trade_date)) "
                    "FROM pension_flow_daily"
                ).fetchone()[0]
                if pf and pf > 7:
                    sanity_warnings.append(f"⚠️ pension_flow_daily {pf:.0f}일 stale (기대 <7일)")
                # dart_5pct_changes 7일 이내 (정상이면 매일 갱신)
                dart5 = conn.execute(
                    "SELECT julianday('now') - julianday(MAX(rcept_dt)) "
                    "FROM dart_5pct_changes WHERE rcept_dt IS NOT NULL"
                ).fetchone()[0]
                if dart5 and dart5 > 7:
                    sanity_warnings.append(f"⚠️ dart_5pct_changes {dart5:.0f}일 stale")
        except Exception as e:
            sanity_warnings.append(f"sanity 확장 검증 오류: {e}")

        if sanity_warnings:
            warn_msg = "🔍 *데이터 품질 경고*\n\n" + "\n".join(sanity_warnings)
            await _safe_send(context, warn_msg)
    except Exception as e:
        print(f"[weekly_sanity] 실패: {e}")


async def weekly_log_rotate(context):
    """매주 일요일 23:30 KST - /tmp/stock-bot.log 트림 (100MB 초과 시 마지막 10MB).

    학습 #?? (5/9): mac /tmp 는 RAM-backed (APFS), 무한 성장 시 launchd
    stdout 드롭 + working set eviction. launchd plist StandardOutPath
    직접 쏟음 → 자동 트림 필요.

    inode 보존 (POSIX append FD 호환): launchd 가 시작 시 O_APPEND 로 연
    FD 를 보유함. `mv tmp file` 패턴은 path 가 새 inode 를 가리키게 만들지만
    launchd 의 기존 FD 는 unlinked old inode 에 계속 write → 트림 효과 무효화.
    `cat tmp > file` 은 file 의 기존 내용을 truncate 후 새 내용 write 하여
    inode 를 유지함 → launchd FD valid, 다음 append write 가 truncated file
    끝에 정상 추가됨.
    """
    import os as _os
    import subprocess as _sp
    log_path = "/tmp/stock-bot.log"
    try:
        size = _os.path.getsize(log_path)
        if size > 100 * 1024 * 1024:
            _sp.run(
                f"tail -c 10485760 {log_path} > {log_path}.tmp && cat {log_path}.tmp > {log_path} && rm {log_path}.tmp",
                shell=True, check=True
            )
            print(f"[log_rotate] {size/1e6:.1f}MB -> 10MB 트림 (inode 보존)", flush=True)
    except FileNotFoundError:
        # 로그 파일 부재 (개발/테스트 환경)
        pass
    except Exception as e:
        print(f"[log_rotate] 오류: {e}", flush=True)
