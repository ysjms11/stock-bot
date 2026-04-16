"""
DB 수집기 — KIS API 풀수집 + SQLite DB
- 매일: 기본시세 + 시간외 + 수급 + 공매도 → daily_snapshot
- 주 1회: 손익계산서 + 대차대조표 → financial_quarterly
- 기술지표 계산 → daily_snapshot UPDATE
- FnGuide 컨센서스 → daily_snapshot UPDATE

파일 구조:
  [1~70]    imports + 상수
  [71~130]  SQLite 연결 / 스키마 초기화
  [131~160] Rate Limiter
  [161~240] KRX OPEN API 함수 (krx_crawler.py에서 복사)
  [241~330] 섹터 분류 (krx_crawler.py에서 복사)
  [331~390] 종목 마스터 UPSERT
  [391~430] _collect_phase — 전종목 배치 수집
  [431~530] _store_daily_snapshot — daily_snapshot INSERT
  [531~560] collect_daily — 메인 수집 함수
  [561+]    하위호환 심볼 (main.py import용)
"""

import sqlite3
import asyncio
import aiohttp
import os
import json
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field

KST = ZoneInfo("Asia/Seoul")
_DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = f"{_DATA_DIR}/stock.db"

# 하위호환 (main.py import용)
KRX_DB_DIR = f"{_DATA_DIR}/krx_db"

# KRX OPEN API 상수 (krx_crawler.py와 동일)
KRX_OPENAPI_BASE = "https://data-dbg.krx.co.kr/svc/apis"
KRX_API_KEY = os.environ.get("KRX_API_KEY", "")

_OPENAPI_ENDPOINTS = {
    "market_STK": ("sto", "stk_bydd_trd"),
    "market_KSQ": ("sto", "ksq_bydd_trd"),
}

KRX_JSON_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101",
}

_STD_SECTOR_MAP_PATH = f"{_DATA_DIR}/std_sector_map.json"

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Rate limiter 전역 세마포어
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_RATE_SEM = None  # collect_daily 시작 시 초기화

_PHASE_TIMEOUT = 600   # Phase별 타임아웃 10분


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# SQLite 연결 / 스키마 초기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _get_db() -> sqlite3.Connection:
    """SQLite 연결. WAL 모드, FK 활성화. 스키마 자동 생성."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row  # dict-like 접근
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    """data/db_schema.sql 실행."""
    schema_path = os.path.join(os.path.dirname(__file__), "data", "db_schema.sql")
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    # 기존 DB 마이그레이션: 누락 컬럼 추가 (SQLite ADD COLUMN IF NOT EXISTS 미지원 → try/except)
    for alter_sql in (
        "ALTER TABLE daily_snapshot ADD COLUMN loan_balance_rate REAL DEFAULT 0",
        # v1.4: F/M/FCF Phase1 — financial_quarterly 확장 (DART fnlttSinglAcntAll)
        "ALTER TABLE financial_quarterly ADD COLUMN cfo INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN capex INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN fcf INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN depreciation INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN sga INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN receivables INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN inventory INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN shares_out INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN net_income_parent INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN equity_parent INTEGER",
        "ALTER TABLE financial_quarterly ADD COLUMN fs_source TEXT",
        # v1.5: F/M/FCF Phase3 — daily_snapshot 알파 메트릭
        "ALTER TABLE daily_snapshot ADD COLUMN fscore INTEGER",
        "ALTER TABLE daily_snapshot ADD COLUMN mscore REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_to_assets REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_yield_ev REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_conversion REAL",
    ):
        try:
            conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass  # 이미 존재


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Rate Limiter
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def _rate_limited(coro):
    """초당 8건 제한 (세마포어 + jitter 슬립)."""
    import random
    async with _RATE_SEM:
        result = await coro
        await asyncio.sleep(0.10 + random.random() * 0.06)  # 0.10~0.16초
        return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# KRX OPEN API (krx_crawler.py에서 복사)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _pi(s) -> int:
    """KRX comma-formatted string → int"""
    if not s or s == "-" or s == "":
        return 0
    return int(str(s).replace(",", "").replace("+", "").strip() or "0")


def _pf(s) -> float:
    """KRX string → float"""
    if not s or s == "-" or s == "":
        return 0.0
    return float(str(s).replace(",", "").replace("+", "").strip() or "0")


async def _krx_openapi_get(session: aiohttp.ClientSession, category: str,
                            endpoint: str, date: str) -> list:
    """KRX OPEN API GET 요청. Returns OutBlock_1 리스트."""
    url = f"{KRX_OPENAPI_BASE}/{category}/{endpoint}"
    params = {"AUTH_KEY": KRX_API_KEY, "basDd": date}
    async with session.get(url, params=params,
                           timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status == 401:
            raise RuntimeError("KRX OPEN API 인증 실패 (401)")
        if resp.status == 429:
            raise RuntimeError("KRX OPEN API 호출 한도 초과 (429)")
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"KRX OPEN API HTTP {resp.status}: {text[:200]}")
        data = await resp.json(content_type=None)
        records = data.get("OutBlock_1", [])
        if not records:
            raise RuntimeError(f"KRX OPEN API 빈 응답 ({endpoint})")
        return records


async def _krx_post(session: aiohttp.ClientSession, form: dict) -> dict:
    """KRX 크롤링 POST 요청."""
    async with session.post(KRX_JSON_URL, data=form, headers=KRX_HEADERS,
                            timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"KRX HTTP {resp.status}: {text[:200]}")
        return await resp.json(content_type=None)


def _parse_market_records(records: list, market: str) -> list[dict]:
    """시세 레코드 파싱 (OPEN API / 크롤링 공통).
    OPEN API: ISU_CD(6자리), ISU_NM
    크롤링:   ISU_SRT_CD(6자리), ISU_ABBRV
    """
    mkt_label = "kospi" if market == "STK" else "kosdaq"
    result = []
    for r in records:
        raw = str(r.get("ISU_SRT_CD") or r.get("ISU_CD", "")).strip()
        # ISIN(KR7XXXXXX000) → 6자리 추출
        if len(raw) == 12 and raw.startswith("KR"):
            ticker = raw[3:9]
        else:
            ticker = raw
        if not ticker or len(ticker) != 6:
            continue
        name = str(r.get("ISU_ABBRV") or r.get("ISU_NM", "")).strip()
        result.append({
            "ticker": ticker,
            "name": name,
            "market": mkt_label,
            "close": _pi(r.get("TDD_CLSPRC")),
            "chg_pct": _pf(r.get("FLUC_RT")),
            "volume": _pi(r.get("ACC_TRDVOL")),
            "trade_value": _pi(r.get("ACC_TRDVAL")),
            "market_cap": _pi(r.get("MKTCAP")),
        })
    return result


async def fetch_krx_market_data(date: str, market: str = "STK") -> list[dict]:
    """전종목 시세. KRX OPEN API 우선, 실패 시 크롤링 fallback."""
    # 1차: KRX OPEN API
    if KRX_API_KEY:
        ep = _OPENAPI_ENDPOINTS.get(f"market_{market}")
        if ep:
            try:
                async with aiohttp.ClientSession() as s:
                    records = await _krx_openapi_get(s, ep[0], ep[1], date)
                result = _parse_market_records(records, market)
                print(f"[KRX OPENAPI] {market} 시세: {len(result)}종목")
                return result
            except Exception as e:
                print(f"[KRX OPENAPI] {market} 시세 실패: {e} → 크롤링 fallback")

    # 2차: 크롤링 (data.krx.co.kr)
    form = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
        "locale": "ko_KR",
        "mktId": market,
        "trdDd": date,
        "share": "1",
        "money": "1",
    }
    try:
        async with aiohttp.ClientSession() as s:
            body = await _krx_post(s, form)
        records = body.get("OutBlock_1", [])
        if not records:
            raise RuntimeError("empty OutBlock_1")
        result = _parse_market_records(records, market)
        print(f"[KRX] {market} 시세: {len(result)}종목")
        return result
    except Exception as e:
        print(f"[KRX] {market} 시세 직접호출 실패: {e}")
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 섹터 분류 (krx_crawler.py에서 복사)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# 6자리 표준산업분류코드 → 섹터명
_STD_CODE_TO_SECTOR = {
    # 반도체/전자
    "032601": "반도체", "032602": "전자부품", "032603": "IT하드웨어",
    "032604": "통신장비/가전", "032605": "영상/음향", "032606": "전자부품",
    # 전력/에너지
    "032801": "전력기기", "032802": "2차전지", "032803": "전선/케이블",
    "032804": "전기장비", "032805": "가전", "032809": "전기장비",
    # 자동차/운송장비
    "033001": "자동차", "033002": "자동차부품", "033003": "자동차부품",
    "033004": "자동차부품", "033101": "조선", "033102": "철도",
    "033103": "항공우주/방산", "033109": "기타운송",
    # 방산
    "032502": "방산",
    # 바이오/제약/의료
    "032101": "바이오", "032102": "제약", "032103": "의료용품",
    "032701": "의료기기", "032702": "정밀기기", "032703": "정밀기기",
    # 화학
    "032001": "화학", "032002": "화학", "032003": "화학",
    "032004": "화학", "032005": "화학섬유",
    # 금속/소재
    "032401": "철강", "032402": "비철금속", "032403": "금속가공",
    "032501": "금속가공", "032509": "금속가공",
    "032201": "고무", "032202": "플라스틱",
    "032301": "유리/세라믹", "032302": "세라믹", "032303": "시멘트/비금속",
    "032309": "비금속",
    # 기계
    "032901": "일반기계",
    # 식품/음료
    "031001": "식품", "031002": "식품", "031003": "식품", "031004": "식품",
    "031005": "식품", "031006": "식품", "031007": "식품", "031008": "식품",
    "031009": "식품", "031101": "음료", "031102": "음료", "031201": "담배",
    # 섬유/의류
    "031301": "섬유", "031302": "섬유", "031303": "섬유", "031304": "섬유",
    "031309": "섬유", "031401": "패션/의류", "031403": "패션/의류",
    "031404": "패션/의류", "031501": "패션/의류", "031502": "패션/의류",
    # 목재/종이/인쇄
    "031601": "목재/종이", "031602": "목재/종이",
    "031701": "목재/종이", "031702": "목재/종이", "031709": "목재/종이",
    "031801": "인쇄", "031802": "인쇄",
    # 정유/에너지
    "031902": "정유",
    # 전기/가스/환경
    "043501": "전기/가스", "043502": "가스", "043503": "전기/가스",
    "053802": "환경", "053803": "환경",
    # 건설
    "064101": "건설", "064102": "건설",
    "064201": "건설", "064202": "건설", "064203": "건설", "064204": "건설",
    # 유통/도매/소매
    "074501": "유통", "074502": "유통",
    "074601": "무역/상사", "074602": "무역/상사", "074603": "식품유통",
    "074604": "유통", "074605": "유통", "074606": "유통",
    "074607": "무역/상사", "074608": "무역/상사",
    "074701": "유통/소매", "074702": "유통/소매", "074703": "유통/소매",
    "074704": "유통/소매", "074705": "유통/소매", "074707": "유통/소매",
    "074708": "유통/소매", "074709": "유통/소매",
    # 운송/물류
    "084902": "운송", "084903": "물류",
    "085001": "해운", "085101": "항공", "085209": "물류",
    # 호텔/외식
    "095501": "레저/호텔", "095601": "외식",
    # SW/게임/미디어
    "105801": "출판/교육", "105802": "소프트웨어",
    "105901": "엔터/미디어", "105902": "엔터/미디어",
    "106002": "엔터/미디어", "106003": "엔터/미디어",
    # IT/통신
    "106102": "통신", "106201": "IT서비스",
    "106301": "인터넷/플랫폼", "106309": "IT서비스",
    # 금융
    "116401": "은행", "116402": "투자",
    "116501": "보험", "116502": "보험", "116601": "증권", "116602": "보험",
    # 부동산
    "126801": "리츠/부동산", "126802": "리츠/부동산",
    # 전문서비스
    "137103": "광고", "137104": "광고",
    "137105": "지주", "137106": "지주",
    "137201": "엔지니어링", "137209": "엔지니어링",
    "137302": "디자인", "137309": "기타서비스",
    # 생활서비스
    "147401": "시설관리", "147502": "여행", "147503": "보안", "147509": "기타서비스",
    "147601": "기타서비스", "147602": "기타서비스", "147603": "기타서비스",
    # 교육
    "168501": "교육", "168505": "교육", "168506": "교육", "168507": "교육",
    # 농림어업
    "010101": "농업", "010301": "수산",
    # 엔터/레저
    "189001": "엔터/미디어", "189101": "스포츠", "189102": "엔터/미디어",
    # 기타
    "033201": "가구/생활", "033301": "귀금속", "033302": "기타제조",
    "033303": "스포츠용품", "033309": "기타제조",
    "199503": "기타서비스", "199609": "기타서비스",
}

# 애매한 코드 → 이름 키워드로 세분화
_SECTOR_KEYWORD_RULES = [
    # 116409 기타금융 → 금융 vs 지주
    ("116409", ["금융", "카드", "캐피탈"], "금융"),
    ("116409", ["페이", "핀테크"], "핀테크"),
    # 032902 특수기계 → 반도체장비 vs 로봇 vs 건설기계
    ("032902", ["로봇", "로보틱스", "로보티즈"], "로봇"),
    ("032902", ["건설기계", "밥캣"], "건설기계"),
    # 032004 기타화학 → 화장품 vs 소재
    ("032004", ["아모레", "코스맥스", "코스메카", "콜마", "에이피알",
                "LG생활", "달바", "뷰티"], "화장품/뷰티"),
    ("032004", ["솔브레인", "나노신소재", "코스모신소재", "레이크머티리얼즈"], "전자소재"),
    # 105802 SW → 게임 vs AI
    ("105802", ["게임", "크래프톤", "넷마블", "엔씨소프트", "펄어비스",
                "시프트업", "넥슨"], "게임"),
    ("105802", ["루닛", "노타", "클로봇"], "AI"),
]

# 애매한 코드의 기본값 (키워드 매칭 안 될 때)
_SECTOR_CODE_DEFAULTS = {
    "116409": "지주", "032902": "반도체장비", "137001": "바이오",
    "032004": "화학", "105802": "소프트웨어",
}

# 개별 종목 오버라이드 (매출비중 기준)
_SECTOR_OVERRIDES = {
    "005930": "반도체",      # 삼성전자
    "005935": "반도체",      # 삼성전자우
    "009540": "조선",        # HD한국조선해양
    "267250": "조선",        # HD현대
    "086520": "2차전지",     # 에코프로
    "003670": "2차전지",     # 포스코퓨처엠
    "013030": "산업부품",    # 하이록코리아 (피팅/밸브)
    "092780": "자동차부품",  # DYP (피스톤+EV냉각)
    "094820": "원전", # 일진파워
    "272210": "방산/IT",     # 한화시스템
    "034020": "원전", # 두산에너빌리티
    "073490": "5G/통신",     # 이노와이어리스
    "189300": "위성통신",    # 인텔리안테크
    "021240": "가전/렌탈",   # 코웨이
}


def _classify_sector(ticker: str, name: str, std_code: str) -> str:
    """표준산업분류코드 + 이름 키워드 → 실용 섹터명."""
    if ticker in _SECTOR_OVERRIDES:
        return _SECTOR_OVERRIDES[ticker]
    if std_code in _STD_CODE_TO_SECTOR and std_code not in _SECTOR_CODE_DEFAULTS:
        return _STD_CODE_TO_SECTOR[std_code]
    for code, keywords, sector in _SECTOR_KEYWORD_RULES:
        if std_code == code:
            for kw in keywords:
                if kw in name:
                    return sector
    if std_code in _SECTOR_CODE_DEFAULTS:
        return _SECTOR_CODE_DEFAULTS[std_code]
    return _STD_CODE_TO_SECTOR.get(std_code, "")


def _load_std_sector_map() -> dict:
    """std_sector_map.json 로드 → {ticker: {std_code, std_name}}."""
    try:
        with open(_STD_SECTOR_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 종목 마스터 UPSERT
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _sync_stock_master(conn: sqlite3.Connection, market_data: list[dict]):
    """시세 데이터에서 종목 마스터 UPSERT."""
    std_map = _load_std_sector_map()
    for item in market_data:
        ticker = item["ticker"]
        name = item.get("name", "")
        market = item.get("market", "")
        info = std_map.get(ticker, {})
        sector = _classify_sector(ticker, name, info.get("std_code", ""))
        conn.execute("""
            INSERT INTO stock_master (symbol, name, market, sector, std_code, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name, market=excluded.market,
                sector=excluded.sector, updated_at=excluded.updated_at
        """, (ticker, name, market, sector, info.get("std_code", "")))
    conn.commit()


def _update_master_from_basic(conn: sqlite3.Connection, phase1_results: dict):
    """Phase 1 기본시세 응답에서 sector_krx + 신규 종목 섹터 갱신."""
    std_map = _load_std_sector_map()
    for ticker, data in phase1_results.items():
        sector_krx = data.get("bstp_kor_isnm", "")
        if not sector_krx:
            continue
        # sector_krx 저장
        conn.execute("""
            UPDATE stock_master SET sector_krx = ? WHERE symbol = ?
        """, (sector_krx, ticker))
        # 정밀 섹터가 비어있으면 KRX 업종으로 fallback
        row = conn.execute("SELECT sector FROM stock_master WHERE symbol = ?", (ticker,)).fetchone()
        if row and not row["sector"]:
            conn.execute("UPDATE stock_master SET sector = ? WHERE symbol = ?", (sector_krx, ticker))
    conn.commit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase별 배치 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def _collect_phase(name: str, tickers: list, token: str,
                          session: aiohttp.ClientSession, fetch_fn) -> dict:
    """한 Phase 전종목 수집. Circuit breaker 내장.
    Returns {"results": {ticker: data}, "success": N, "failed": N[, "aborted": True]}
    """
    results = {}
    failed = 0

    async def _fetch_one(ticker):
        try:
            return ticker, await _rate_limited(fetch_fn(ticker, token, session))
        except Exception:
            return ticker, None

    # Circuit breaker: 첫 50종목 테스트
    probe_size = min(50, len(tickers))
    probe = tickers[:probe_size]
    probe_results = await asyncio.gather(*[_fetch_one(t) for t in probe])

    probe_fail = sum(1 for _, data in probe_results if data is None)
    for ticker, data in probe_results:
        if data is not None:
            results[ticker] = data
        else:
            failed += 1

    # 실패율 80% 이상이면 나머지 중단
    if probe_size > 0 and probe_fail / probe_size >= 0.8:
        remaining_count = len(tickers) - probe_size
        print(f"[{name}] Circuit breaker: {probe_fail}/{probe_size} 실패 → 나머지 {remaining_count}종목 스킵")
        return {
            "results": results,
            "success": len(results),
            "failed": failed + remaining_count,
            "aborted": True,
        }

    # 나머지 종목 실행
    remaining = tickers[probe_size:]
    if remaining:
        rem_results = await asyncio.gather(*[_fetch_one(t) for t in remaining])
        for ticker, data in rem_results:
            if data is not None:
                results[ticker] = data
            else:
                failed += 1

    return {"results": results, "success": len(results), "failed": failed}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# daily_snapshot INSERT
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _store_daily_snapshot(conn: sqlite3.Connection, date: str,
                           krx_data: dict, p1: dict, p2: dict, p3: dict, p4: dict):
    """4개 Phase 결과를 daily_snapshot에 INSERT OR REPLACE."""
    p1r = p1["results"]
    p2r = p2["results"]
    p3r = p3["results"]
    p4r = p4["results"]

    for ticker, krx in krx_data.items():
        try:
            basic = p1r.get(ticker, {})
            overtime = p2r.get(ticker, {})
            supply_raw = p3r.get(ticker, [])
            short_raw = p4r.get(ticker, [])

            # supply / short 는 리스트 반환 → 최신 1행
            supply = supply_raw[0] if isinstance(supply_raw, list) and supply_raw else {}
            short = short_raw[0] if isinstance(short_raw, list) and short_raw else {}

            # KIS 기본시세 필드 → KRX fallback
            close = int(basic.get("stck_prpr", 0) or 0)
            if close == 0:
                close = krx.get("close", 0)

            conn.execute("""
                INSERT OR REPLACE INTO daily_snapshot (
                    trade_date, symbol,
                    close, open, high, low, change_pct,
                    volume, trade_value, market_cap,
                    per, pbr, eps, bps, div_yield,
                    w52_high, w52_low, foreign_own_pct, listing_shares, turnover,
                    loan_balance_rate,
                    foreign_net_qty, foreign_net_amt, inst_net_qty, inst_net_amt,
                    indiv_net_qty, indiv_net_amt,
                    short_volume, short_ratio,
                    ovtm_close, ovtm_change_pct, ovtm_volume,
                    collected_at
                ) VALUES (
                    ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    datetime('now')
                )
            """, (
                date, ticker,
                close,
                int(basic.get("stck_oprc", 0) or 0) or krx.get("open", 0),
                int(basic.get("stck_hgpr", 0) or 0) or krx.get("high", 0),
                int(basic.get("stck_lwpr", 0) or 0) or krx.get("low", 0),
                float(basic.get("prdy_ctrt", 0) or 0) or krx.get("chg_pct", 0),
                int(basic.get("acml_vol", 0) or 0) or krx.get("volume", 0),
                int(basic.get("acml_tr_pbmn", 0) or 0) or krx.get("trade_value", 0),
                int(basic.get("hts_avls", 0) or 0),  # 억원
                float(basic.get("per", 0) or 0),
                float(basic.get("pbr", 0) or 0),
                float(basic.get("eps", 0) or 0),
                float(basic.get("bps", 0) or 0),
                0.0,  # div_yield — 별도 계산
                int(basic.get("w52_hgpr", 0) or 0),
                int(basic.get("w52_lwpr", 0) or 0),
                float(basic.get("hts_frgn_ehrt", 0) or 0),
                int(basic.get("lstn_stcn", 0) or 0),
                float(basic.get("vol_tnrt", 0) or 0),
                float(basic.get("whol_loan_rmnd_rate", 0) or 0),  # 신용잔고비율
                # 수급 (kis_investor_trend_history 변환 키)
                int(supply.get("foreign_net", 0) or 0),
                0,  # foreign_net_amt — 히스토리 API에 금액 없음
                int(supply.get("institution_net", 0) or 0),
                0,  # inst_net_amt
                int(supply.get("individual_net", 0) or 0),
                0,  # indiv_net_amt
                # 공매도 (FHPST04830000 응답 필드)
                int(short.get("short_vol", 0) or 0),
                float(short.get("short_ratio", 0) or 0),
                # 시간외 (kis_overtime_daily 반환 필드)
                int(overtime.get("ovtm_close", 0) or 0),
                float(overtime.get("ovtm_change_pct", 0) or 0),
                int(overtime.get("ovtm_volume", 0) or 0),
            ))
        except Exception as e:
            print(f"[DB] {ticker} snapshot INSERT 실패: {e}")

    conn.commit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 수집 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def collect_daily(date: str = None) -> dict:
    """매일 장후 전종목 수집.
    Phase: KRX시세 → stock_master UPSERT → KIS기본시세 → 시간외 → 수급 → 공매도
           → daily_snapshot INSERT → 기술지표 계산 (→ Part 2)

    Returns:
        {"date": str, "phases": {...}, "total": int, "duration": float}
    """
    global _RATE_SEM
    _RATE_SEM = asyncio.Semaphore(8)

    if date is None:
        date = datetime.now(KST).strftime("%Y%m%d")

    # 주말 가드
    dt = datetime.strptime(date, "%Y%m%d")
    if dt.weekday() >= 5:  # 토(5), 일(6)
        print(f"[collect_daily] {date} 주말 → 스킵")
        return {"skipped": True, "reason": "weekend", "date": date}

    report: dict = {"date": date, "phases": {}, "total": 0, "duration": 0.0}
    start = datetime.now()

    # 1. KRX OPEN API → 전종목 시세 (STK + KSQ, 각 2콜)
    all_stocks: dict = {}
    for mkt in ["STK", "KSQ"]:
        try:
            records = await fetch_krx_market_data(date, mkt)
            for r in records:
                all_stocks[r["ticker"]] = r
        except Exception as e:
            print(f"[collect_daily] KRX {mkt} 실패: {e}")
        await asyncio.sleep(0.5)

    # KRX 실패 시 stock_master에서 종목 리스트 fallback
    if not all_stocks:
        print("[collect_daily] KRX 데이터 없음 → stock_master fallback")
        conn = _get_db()
        rows = conn.execute("SELECT symbol, name, market FROM stock_master").fetchall()
        if not rows:
            conn.close()
            return {"error": "KRX 데이터 없음 + stock_master 비어있음", "date": date}
        for r in rows:
            all_stocks[r["symbol"]] = {"ticker": r["symbol"], "name": r["name"], "market": r["market"]}
        conn.close()
        print(f"[collect_daily] stock_master에서 {len(all_stocks)}종목 로드")

    tickers = list(all_stocks.keys())
    report["total"] = len(tickers)
    print(f"[collect_daily] {date} — 전종목 {len(tickers)}개 수집 시작")

    # 2. stock_master UPSERT
    conn = _get_db()
    try:
        _sync_stock_master(conn, list(all_stocks.values()))
    except Exception as e:
        print(f"[collect_daily] stock_master UPSERT 실패: {e}")

    # 3. KIS API Phase별 배치 수집
    from kis_api import (
        get_kis_token,
        kis_stock_price,
        kis_overtime_daily,
        kis_investor_trend_history,
        kis_daily_short_sale,
    )

    token = await get_kis_token()
    if not token:
        conn.close()
        return {"error": "KIS 토큰 발급 실패", "date": date}

    async with aiohttp.ClientSession() as session:
        # Phase 1: KIS 기본시세 + 밸류에이션 (FHKST01010100)
        print(f"[collect_daily] Phase 1/4 — 기본시세 {len(tickers)}종목")
        try:
            p1 = await asyncio.wait_for(
                _collect_phase("basic", tickers, token, session,
                               lambda t, tok, s: kis_stock_price(t, tok, session=s)),
                timeout=_PHASE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            print(f"[collect_daily] Phase basic 타임아웃 ({_PHASE_TIMEOUT}초)")
            p1 = {"results": {}, "success": 0, "failed": len(tickers), "timeout": True}
        report["phases"]["basic"] = {
            "success": p1["success"], "failed": p1["failed"],
        }

        # Phase 1 후: sector_krx 자동 갱신 (신규 상장 종목 섹터 fallback)
        try:
            _update_master_from_basic(conn, p1["results"])
        except Exception as e:
            print(f"[collect_daily] sector_krx 갱신 실패: {e}")

        # Phase 2: 시간외 (FHPST02320000)
        print(f"[collect_daily] Phase 2/4 — 시간외")
        try:
            p2 = await asyncio.wait_for(
                _collect_phase("overtime", tickers, token, session,
                               lambda t, tok, s: kis_overtime_daily(t, tok, session=s)),
                timeout=_PHASE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            print(f"[collect_daily] Phase overtime 타임아웃 ({_PHASE_TIMEOUT}초)")
            p2 = {"results": {}, "success": 0, "failed": len(tickers), "timeout": True}
        report["phases"]["overtime"] = {
            "success": p2["success"], "failed": p2["failed"],
        }

        # Phase 3: 투자자 수급 1일 (FHPTJ04160001)
        print(f"[collect_daily] Phase 3/4 — 수급")
        try:
            p3 = await asyncio.wait_for(
                _collect_phase("supply", tickers, token, session,
                               lambda t, tok, s: kis_investor_trend_history(t, tok, n_days=1, session=s)),
                timeout=_PHASE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            print(f"[collect_daily] Phase supply 타임아웃 ({_PHASE_TIMEOUT}초)")
            p3 = {"results": {}, "success": 0, "failed": len(tickers), "timeout": True}
        report["phases"]["supply"] = {
            "success": p3["success"], "failed": p3["failed"],
        }

        # Phase 4: 공매도 1일 (FHPST04830000)
        print(f"[collect_daily] Phase 4/4 — 공매도")
        try:
            p4 = await asyncio.wait_for(
                _collect_phase("short", tickers, token, session,
                               lambda t, tok, s: kis_daily_short_sale(t, tok, n=1, session=s)),
                timeout=_PHASE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            print(f"[collect_daily] Phase short 타임아웃 ({_PHASE_TIMEOUT}초)")
            p4 = {"results": {}, "success": 0, "failed": len(tickers), "timeout": True}
        report["phases"]["short"] = {
            "success": p4["success"], "failed": p4["failed"],
        }

    # 4. daily_snapshot INSERT
    print(f"[collect_daily] daily_snapshot INSERT")
    _store_daily_snapshot(conn, date, all_stocks, p1, p2, p3, p4)

    # 5. 기술지표 계산 + UPDATE
    try:
        _compute_and_update(conn, date)
    except Exception as e:
        print(f"[collect_daily] 기술지표 계산 실패: {e}")

    # 6. FnGuide 컨센서스 UPDATE (Part 2에서 구현)
    # _update_consensus(conn, date, tickers)

    conn.close()

    # 7. F/M/FCF 알파 메트릭 일괄 업데이트 (실패해도 collect_daily는 성공 취급)
    try:
        alpha_res = update_all_alpha_metrics(trade_date=date)
        report["alpha"] = alpha_res
    except Exception as e:
        print(f"[collect_daily] 알파 메트릭 계산 실패: {e}")
        report["alpha"] = {"error": str(e)}

    report["duration"] = (datetime.now() - start).total_seconds()
    print(
        f"[collect_daily] 완료 — {len(tickers)}종목 "
        f"({report['duration']:.1f}s)"
    )
    return report


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# Part 2 — 기술지표 계산 + 하위호환 심볼 + 재무
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _ma(arr, n):
    """Simple MA. Returns None if insufficient data."""
    if len(arr) < n:
        return None
    return round(float(np.mean(arr[:n])), 2)


def _rsi(closes, period=14):
    """RSI calculation. Returns None if insufficient data."""
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i + 1] for i in range(min(len(closes) - 1, period * 3))]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]
    if len(gains) < period:
        return None
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _calc_vp(closes: list, volumes: list, n: int, n_bins: int = 20) -> dict:
    """매물대 계산. Returns {poc, va_high, va_low, position} or all None."""
    null = {"poc": None, "va_high": None, "va_low": None, "position": None}
    actual = min(n, len(closes))
    if actual < 30 or len(volumes) < actual:
        return null
    cs = closes[:actual]
    vs = volumes[:actual]
    p_min, p_max = min(cs), max(cs)
    if p_max <= p_min:
        return null
    bin_size = (p_max - p_min) / n_bins
    bins = [0.0] * n_bins
    for c, v in zip(cs, vs):
        idx = min(int((c - p_min) / bin_size), n_bins - 1)
        bins[idx] += v
    poc_idx = int(np.argmax(bins))
    total_vol = sum(bins)
    if total_vol == 0:
        return null
    target_vol = total_vol * 0.7
    sorted_bins = sorted(range(n_bins), key=lambda i: bins[i], reverse=True)
    va_vol = 0
    va_indices = []
    for bi in sorted_bins:
        va_vol += bins[bi]
        va_indices.append(bi)
        if va_vol >= target_vol:
            break
    va_low_idx = min(va_indices)
    va_high_idx = max(va_indices)
    va_high = round(p_min + (va_high_idx + 1) * bin_size)
    va_low = round(p_min + va_low_idx * bin_size)
    cur = closes[0] if closes else 0
    rng = va_high - va_low
    return {
        "poc": round(p_min + (poc_idx + 0.5) * bin_size),
        "va_high": va_high,
        "va_low": va_low,
        "position": round((cur - va_low) / rng, 4) if rng > 0 else None,
    }


def _volume_ratio(volumes: list, recent: int, prev_offset: int):
    """최근 recent일 평균 / 그 이전 recent일 평균."""
    total = recent + prev_offset
    if len(volumes) < total:
        return None
    r = np.mean(volumes[:recent]) if any(v > 0 for v in volumes[:recent]) else 0
    p = np.mean(volumes[prev_offset:total]) if any(v > 0 for v in volumes[prev_offset:total]) else 0
    return round(r / p, 2) if p > 0 else None


def _spread_at(closes: list, offset: int):
    """offset일 전 시점의 MA spread (MA5-MA60)/MA60."""
    if len(closes) < offset + 60:
        return None
    ma5 = _ma(closes[offset:], 5)
    ma60 = _ma(closes[offset:], 60)
    if ma5 and ma60 and ma60 > 0:
        return (ma5 - ma60) / ma60 * 100
    return None


def _rsi_at(closes: list, offset: int, period: int = 14):
    """offset일 전 시점의 RSI."""
    if len(closes) < offset + period + 1:
        return None
    return _rsi(closes[offset:], period)


def _macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD(fast, slow, signal). Returns (macd, signal_line, histogram) or (None, None, None)."""
    if len(closes) < slow + signal:
        return None, None, None
    # EMA 계산 (closes는 최신→과거 순 → 역순으로)
    rev = list(reversed(closes[:slow + signal + 10]))

    def _ema(arr, period):
        k = 2.0 / (period + 1)
        ema = arr[0]
        for v in arr[1:]:
            ema = v * k + ema * (1 - k)
        return ema

    # 전체 시계열에 대한 EMA 계산
    def _ema_series(arr, period):
        k = 2.0 / (period + 1)
        result = [arr[0]]
        for v in arr[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    rev_all = list(reversed(closes[:slow + signal + 20]))
    if len(rev_all) < slow:
        return None, None, None
    ema_fast_s = _ema_series(rev_all, fast)
    ema_slow_s = _ema_series(rev_all, slow)
    if len(ema_fast_s) < slow or len(ema_slow_s) < slow:
        return None, None, None
    macd_line = [f - s for f, s in zip(ema_fast_s[slow - 1:], ema_slow_s[slow - 1:])]
    if len(macd_line) < signal:
        return None, None, None
    signal_line = _ema_series(macd_line, signal)[-1]
    macd_val = macd_line[-1]
    hist = round(macd_val - signal_line, 4)
    return round(macd_val, 4), round(signal_line, 4), hist


def _atr(closes: list, highs: list, lows: list, period: int = 14):
    """ATR(period). closes/highs/lows는 최신→과거 순. Returns None if insufficient."""
    # db_collector에서는 high/low가 daily_snapshot에 있으나
    # history에는 close만 있으므로 close 기반 근사 ATR 계산
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(period):
        c_prev = closes[i + 1] if i + 1 < len(closes) else closes[i]
        h = highs[i] if i < len(highs) else closes[i]
        l = lows[i] if i < len(lows) else closes[i]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    return round(float(np.mean(trs)), 2) if trs else None


def _volatility_20d(closes: list):
    """20일 종가 표준편차 / 평균 (변동성). Returns None if insufficient."""
    if len(closes) < 20:
        return None
    c = closes[:20]
    mean = float(np.mean(c))
    if mean == 0:
        return None
    return round(float(np.std(c, ddof=0)) / mean * 100, 4)


def _load_history_from_db(conn: sqlite3.Connection, target_date: str, n_days: int = 260):
    """SQLite daily_snapshot에서 과거 N일 시계열 로드.
    Returns: ({ticker: {close: [], volume: [], eps: [], foreign_net_amt: [], short_volume: [],
                         high: [], low: []}}, [날짜리스트(최신→과거)])
    """
    date_rows = conn.execute("""
        SELECT DISTINCT trade_date FROM daily_snapshot
        WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?
    """, (target_date, n_days + 1)).fetchall()
    dates = [r[0] for r in date_rows]

    if len(dates) < 2:
        return {}, dates

    oldest = dates[-1]
    rows = conn.execute("""
        SELECT symbol, trade_date, close, high, low, volume, eps,
               foreign_net_amt, short_volume, foreign_own_pct, loan_balance_rate
        FROM daily_snapshot
        WHERE trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date ASC
    """, (oldest, target_date)).fetchall()

    # 종목별 그룹핑 (ASC 순서 → 나중에 reverse해서 최신→과거 순으로)
    tmp = {}
    for r in rows:
        sym = r["symbol"]
        if sym not in tmp:
            tmp[sym] = {"close": [], "volume": [], "eps": [],
                        "foreign_net_amt": [], "short_volume": [],
                        "high": [], "low": [], "foreign_own_pct": [],
                        "loan_balance_rate": []}
        h = tmp[sym]
        h["close"].append(r["close"] or 0)
        h["volume"].append(r["volume"] or 0)
        h["eps"].append(r["eps"] or 0)
        h["foreign_net_amt"].append(r["foreign_net_amt"] or 0)
        h["short_volume"].append(r["short_volume"] or 0)
        h["high"].append(r["high"] or 0)
        h["low"].append(r["low"] or 0)
        h["foreign_own_pct"].append(r["foreign_own_pct"] or 0)
        h["loan_balance_rate"].append(r["loan_balance_rate"] or 0)

    # 최신→과거 순으로 역순
    history = {}
    for sym, h in tmp.items():
        history[sym] = {k: list(reversed(v)) for k, v in h.items()}

    return history, dates  # dates는 이미 DESC(최신→과거)


def _compute_technicals_sqlite(date: str, stocks: dict, history: dict, dates: list):
    """기술지표 + 추세 점수 + 매물대를 stocks dict에 in-place 추가.
    krx_crawler._compute_technicals 로직 기반, SQLite 입출력.
    추가 지표: MACD(12,26,9), ATR(14), volatility_20d, bb_width.
    """
    n_days = len(dates)
    print(f"[Tech/SQLite] 과거 {n_days}일 로드, 지표 계산 시작")

    # 연초 날짜 (YTD 계산용)
    year = date[:4]
    ytd_idx = None
    for i, d in enumerate(dates):
        if d[:4] < year:
            ytd_idx = i
            break

    # 섹터 평균 등락률 계산
    sector_chg = {}
    for s in stocks.values():
        sec = s.get("sector_name", "")
        if sec:
            sector_chg.setdefault(sec, []).append(s.get("chg_pct", 0) or 0)
    sector_avg = {sec: round(float(np.mean(vals)), 4) for sec, vals in sector_chg.items() if vals}

    for ticker, s in stocks.items():
        h = history.get(ticker, {})
        closes = h.get("close", [])
        volumes = h.get("volume", [])
        highs = h.get("high", [])
        lows = h.get("low", [])
        eps_hist = h.get("eps", [])
        cur = s.get("close", 0)

        # ── 이평선 ──
        s["ma5"] = _ma(closes, 5)
        s["ma10"] = _ma(closes, 10)
        s["ma20"] = _ma(closes, 20)
        s["ma60"] = _ma(closes, 60)
        s["ma120"] = _ma(closes, 120)
        s["ma200"] = _ma(closes, 200)

        # ── RSI(14) ──
        s["rsi14"] = _rsi(closes, 14)

        # ── 볼린저밴드 (MA20 ± 2σ) + bb_width ──
        if len(closes) >= 20:
            m20 = float(np.mean(closes[:20]))
            std20 = float(np.std(closes[:20], ddof=0))
            s["bb_upper"] = round(m20 + 2 * std20, 0)
            s["bb_lower"] = round(m20 - 2 * std20, 0)
            s["bb_width"] = round((s["bb_upper"] - s["bb_lower"]) / m20 * 100, 4) if m20 > 0 else None
        else:
            s["bb_upper"] = s["bb_lower"] = s["bb_width"] = None

        # ── MA spread ──
        ma5v = s["ma5"]
        ma60v = s["ma60"]
        s["ma_spread"] = round((ma5v - ma60v) / ma60v * 100, 2) if ma5v and ma60v and ma60v > 0 else None

        # ── 52주 고/저/position ──
        if len(closes) >= 60:
            w52_slice = closes[:min(250, len(closes))]
            w52h = max(w52_slice)
            w52l = min(w52_slice)
            s["w52_position"] = round((cur - w52l) / (w52h - w52l), 4) if w52h > w52l else None
        else:
            s["w52_position"] = None

        # ── YTD 수익률 ──
        if ytd_idx is not None and ytd_idx < len(closes) and closes[ytd_idx] > 0:
            s["ytd_return"] = round((cur - closes[ytd_idx]) / closes[ytd_idx] * 100, 2)
        else:
            s["ytd_return"] = None

        # ── 섹터 상대강도 ──
        sec = s.get("sector_name", "")
        chg_pct = s.get("chg_pct", 0) or 0
        s["sector_rel_strength"] = round(chg_pct - sector_avg[sec], 2) if sec and sec in sector_avg else None

        # ── 추세: volume_ratio 5d/10d/20d ──
        s["volume_ratio_5d"] = _volume_ratio(volumes, 5, 5)
        s["volume_ratio_10d"] = _volume_ratio(volumes, 10, 10)
        s["volume_ratio_20d"] = _volume_ratio(volumes, 20, 20)

        # ── 추세: ma_spread_change 10d/30d ──
        cur_spread = s["ma_spread"]
        for nd in (10, 30):
            key = f"ma_spread_change_{nd}d"
            prev = _spread_at(closes, nd)
            s[key] = round(cur_spread - prev, 2) if cur_spread is not None and prev is not None else None

        # ── 추세: rsi_change 5d/20d ──
        rsi_now = s["rsi14"]
        for nd in (5, 20):
            key = f"rsi_change_{nd}d"
            prev_rsi = _rsi_at(closes, nd)
            s[key] = round(rsi_now - prev_rsi, 2) if rsi_now is not None and prev_rsi is not None else None

        # ── 추세: eps_change_90d + earnings_gap ──
        ep_idx = min(89, len(eps_hist) - 1) if len(eps_hist) >= 2 else -1
        if ep_idx >= 1 and eps_hist[0] != 0 and eps_hist[ep_idx] != 0:
            s["eps_change_90d"] = round((eps_hist[0] - eps_hist[ep_idx]) / abs(eps_hist[ep_idx]) * 100, 2)
            ytd = s.get("ytd_return")
            s["earnings_gap"] = round(s["eps_change_90d"] - ytd, 2) if ytd is not None else None
        else:
            s["eps_change_90d"] = s["earnings_gap"] = None

        # ── 수급 추세: foreign_trend Nd ──
        frgn_hist = h.get("foreign_net_amt", [])
        for nd in (5, 20, 60):
            key = f"foreign_trend_{nd}d"
            if len(frgn_hist) >= nd:
                buy_days = sum(1 for x in frgn_hist[:nd] if x > 0)
                s[key] = round(buy_days / nd, 4)
            else:
                s[key] = None

        # ── 수급 비율: foreign_ratio / inst_ratio / fi_ratio ──
        fown = s.get("foreign_own_pct") or h.get("foreign_own_pct", [None])[0]
        s["foreign_ratio"] = fown
        s["inst_ratio"] = s.get("inst_ratio")
        fi_r = None
        fr_v = s.get("foreign_net_amt") or 0
        ir_v = s.get("inst_net_amt") or 0
        vol = s.get("trade_value") or 0
        if vol and vol > 0:
            fi_r = round((fr_v + ir_v) / vol * 100, 4)
        s["fi_ratio"] = fi_r

        # ── 수급 추세: short_change Nd (SQLite: short_volume 기반) ──
        short_hist = h.get("short_volume", [])
        for nd in (5, 20):
            key = f"short_change_{nd}d"
            if len(short_hist) >= nd + 1 and short_hist[nd] > 0:
                s[key] = round((short_hist[0] - short_hist[nd]) / short_hist[nd] * 100, 2)
            else:
                s[key] = None

        # ── 매물대 60d / 250d ──
        for period, suffix in [(60, "_60d"), (250, "_250d")]:
            vp = _calc_vp(closes, volumes, period)
            s[f"vp_poc{suffix}"] = vp["poc"]
            s[f"vp_va_high{suffix}"] = vp["va_high"]
            s[f"vp_va_low{suffix}"] = vp["va_low"]
            s[f"vp_position{suffix}"] = vp["position"]

        # ── MACD(12, 26, 9) ──
        macd_val, macd_sig, macd_hist = _macd(closes)
        s["macd"] = macd_val
        s["macd_signal"] = macd_sig
        s["macd_hist"] = macd_hist

        # ── ATR(14) ──
        s["atr14"] = _atr(closes, highs, lows, 14)

        # ── volatility_20d ──
        s["volatility_20d"] = _volatility_20d(closes)

    # ── 섹터 내 순위 계산 ──
    sector_stocks = {}
    for ticker, s in stocks.items():
        sec = s.get("sector_name", "")
        if sec:
            sector_stocks.setdefault(sec, []).append((ticker, s.get("chg_pct", 0) or 0))
    for sec, members in sector_stocks.items():
        members.sort(key=lambda x: x[1], reverse=True)
        for rank, (ticker, _) in enumerate(members, 1):
            stocks[ticker]["sector_rank"] = rank
    for s in stocks.values():
        s.setdefault("sector_rank", None)

    print(f"[Tech/SQLite] 지표 계산 완료: {len(stocks)}종목")


def _compute_and_update(conn: sqlite3.Connection, date: str):
    """기술지표 계산 후 daily_snapshot UPDATE."""
    # 1. 과거 데이터 로드
    history, dates = _load_history_from_db(conn, date, 260)

    # 2. 당일 종목 데이터 + 섹터명 조인
    rows = conn.execute("""
        SELECT d.*, m.name, m.market, m.sector as sector_name
        FROM daily_snapshot d
        LEFT JOIN stock_master m ON d.symbol = m.symbol
        WHERE d.trade_date = ?
    """, (date,)).fetchall()
    stocks = {r["symbol"]: dict(r) for r in rows}

    if not stocks:
        print(f"[Tech/SQLite] {date} 데이터 없음, 지표 계산 스킵")
        return

    # 3. 기술지표 계산
    _compute_technicals_sqlite(date, stocks, history, dates)

    # 4. UPDATE
    for ticker, s in stocks.items():
        try:
            conn.execute("""
                UPDATE daily_snapshot SET
                    ma5=?, ma10=?, ma20=?, ma60=?, ma120=?, ma200=?, ma_spread=?,
                    rsi14=?, bb_upper=?, bb_lower=?, bb_width=?,
                    macd=?, macd_signal=?, macd_hist=?,
                    atr14=?, volatility_20d=?,
                    w52_position=?, ytd_return=?,
                    vp_poc_60d=?, vp_va_high_60d=?, vp_va_low_60d=?, vp_position_60d=?,
                    vp_poc_250d=?, vp_va_high_250d=?, vp_va_low_250d=?, vp_position_250d=?,
                    volume_ratio_5d=?, volume_ratio_10d=?, volume_ratio_20d=?,
                    ma_spread_change_10d=?, ma_spread_change_30d=?,
                    rsi_change_5d=?, rsi_change_20d=?,
                    eps_change_90d=?, earnings_gap=?,
                    foreign_trend_5d=?, foreign_trend_20d=?, foreign_trend_60d=?,
                    foreign_ratio=?, inst_ratio=?, fi_ratio=?,
                    short_change_5d=?, short_change_20d=?,
                    sector_rel_strength=?, sector_rank=?
                WHERE trade_date=? AND symbol=?
            """, (
                s.get("ma5"), s.get("ma10"), s.get("ma20"),
                s.get("ma60"), s.get("ma120"), s.get("ma200"), s.get("ma_spread"),
                s.get("rsi14"), s.get("bb_upper"), s.get("bb_lower"), s.get("bb_width"),
                s.get("macd"), s.get("macd_signal"), s.get("macd_hist"),
                s.get("atr14"), s.get("volatility_20d"),
                s.get("w52_position"), s.get("ytd_return"),
                s.get("vp_poc_60d"), s.get("vp_va_high_60d"),
                s.get("vp_va_low_60d"), s.get("vp_position_60d"),
                s.get("vp_poc_250d"), s.get("vp_va_high_250d"),
                s.get("vp_va_low_250d"), s.get("vp_position_250d"),
                s.get("volume_ratio_5d"), s.get("volume_ratio_10d"), s.get("volume_ratio_20d"),
                s.get("ma_spread_change_10d"), s.get("ma_spread_change_30d"),
                s.get("rsi_change_5d"), s.get("rsi_change_20d"),
                s.get("eps_change_90d"), s.get("earnings_gap"),
                s.get("foreign_trend_5d"), s.get("foreign_trend_20d"), s.get("foreign_trend_60d"),
                s.get("foreign_ratio"), s.get("inst_ratio"), s.get("fi_ratio"),
                s.get("short_change_5d"), s.get("short_change_20d"),
                s.get("sector_rel_strength"), s.get("sector_rank"),
                date, ticker,
            ))
        except Exception as e:
            print(f"[Tech/SQLite] {ticker} UPDATE 실패: {e}")
    conn.commit()
    print(f"[Tech/SQLite] {date} UPDATE 완료: {len(stocks)}종목")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 하위호환 함수 — mcp_tools.py / main.py 호환
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def load_krx_db(date: str = None) -> dict | None:
    """기존 JSON 포맷과 호환되는 dict 반환. mcp_tools.py 하위호환.
    Returns: {date, stocks: {ticker: {...}}, count, market_summary}
    """
    conn = _get_db()
    try:
        if date is None:
            row = conn.execute(
                "SELECT MAX(trade_date) as d FROM daily_snapshot"
            ).fetchone()
            date = row["d"] if row and row["d"] else None
        if not date:
            return None

        rows = conn.execute("""
            SELECT d.*, m.name, m.market, m.sector as sector_name, m.sector_krx
            FROM daily_snapshot d
            LEFT JOIN stock_master m ON d.symbol = m.symbol
            WHERE d.trade_date = ?
        """, (date,)).fetchall()

        if not rows:
            return None

        stocks = {}
        for r in rows:
            d = dict(r)
            ticker = d.pop("symbol", None)
            if not ticker:
                continue
            d["ticker"] = ticker
            # 컬럼명 호환 매핑
            d["chg_pct"] = d.get("change_pct", 0) or 0
            # market_cap: SQLite는 억원 단위 → 원으로 변환 (기존 JSON 호환)
            mcap = d.get("market_cap", 0) or 0
            d["market_cap"] = mcap * 100_000_000
            # foreign_ratio / inst_ratio / fi_ratio
            if d.get("foreign_ratio") is None:
                d["foreign_ratio"] = d.get("foreign_own_pct", 0) or 0
            if d.get("inst_ratio") is None:
                d["inst_ratio"] = 0
            if d.get("fi_ratio") is None:
                fi_r = None
                fr_v = d.get("foreign_net_amt", 0) or 0
                ir_v = d.get("inst_net_amt", 0) or 0
                tv = d.get("trade_value", 0) or 0
                if tv > 0:
                    fi_r = round((fr_v + ir_v) / tv * 100, 4)
                d["fi_ratio"] = fi_r
            # 하위호환 vp 키 (250d 기준)
            d["vp_poc"] = d.get("vp_poc_250d")
            d["vp_va_high"] = d.get("vp_va_high_250d")
            d["vp_va_low"] = d.get("vp_va_low_250d")
            d["vp_position"] = d.get("vp_position_250d")
            # turnover 호환
            if d.get("turnover") is None:
                d["turnover"] = d.get("vol_tnrt", 0) or 0
            stocks[ticker] = d

        # market_summary 계산
        chg_list_kospi = [s.get("chg_pct", 0) or 0
                          for s in stocks.values() if s.get("market") == "kospi"]
        chg_list_kosdaq = [s.get("chg_pct", 0) or 0
                           for s in stocks.values() if s.get("market") == "kosdaq"]
        market_summary = {
            "kospi_avg_chg": round(float(np.mean(chg_list_kospi)), 4) if chg_list_kospi else 0,
            "kosdaq_avg_chg": round(float(np.mean(chg_list_kosdaq)), 4) if chg_list_kosdaq else 0,
        }

        return {
            "date": date,
            "stocks": stocks,
            "count": len(stocks),
            "market_summary": market_summary,
        }
    finally:
        conn.close()


def _load_history(target_date: str = None, n_days: int = 250):
    """mcp_tools.py 호환. 과거 N일 데이터 SQLite에서 로드.
    Returns: ({ticker: {close: [], volume: [], ...}}, [날짜리스트])
    """
    conn = _get_db()
    try:
        if target_date is None:
            row = conn.execute(
                "SELECT MAX(trade_date) as d FROM daily_snapshot"
            ).fetchone()
            target_date = row["d"] if row and row["d"] else None
        if not target_date:
            return {}, []
        return _load_history_from_db(conn, target_date, n_days)
    finally:
        conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# scan_stocks — krx_crawler.py에서 복사 (load_krx_db 포맷 호환)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

PRESETS = {
    "relative_strength": {
        "description": "시장평균 대비 등락률 +3% 이상 AND fi_ratio>0 (하락장에서 버틴 종목)",
        "sort": "fi_ratio",
    },
    "small_cap_buy": {
        "description": "시총 500~5000억 AND foreign_ratio>0.1% (소형주 외인매수)",
        "filters": {"market_cap_min": 500, "market_cap_max": 5000, "foreign_ratio_min": 0.1},
        "sort": "foreign_ratio",
    },
    "value": {
        "description": "PER>0 AND PER<10 AND PBR>0 AND PBR<1 AND 시총>1000억 (저평가)",
        "filters": {"per_min": 0.01, "per_max": 10, "pbr_min": 0.01, "pbr_max": 1,
                    "market_cap_min": 1000},
        "sort": "pbr",
    },
    "momentum": {
        "description": "chg_pct>3% AND turnover>1% (모멘텀)",
        "filters": {"chg_pct_min": 3, "turnover_min": 1},
        "sort": "chg_pct",
    },
    "oversold": {
        "description": "등락률 -7% 이하 (낙폭과대)",
        "filters": {"chg_pct_max": -7},
        "sort": "chg_pct",
    },
    "foreign_streak": {
        "description": "최근 5일 연속 외인 순매수, 시총 500억 이상 (multi-day)",
        "filters": {"market_cap_min": 500},
        "sort": "cum_foreign_ratio",
    },
}


def _get_foreign_streak_data_db(target_date: str, days: int = 5):
    """SQLite에서 최근 N일 연속 외인 순매수 종목 + 누적 foreign_own_pct.
    Returns: ({ticker: cum_foreign_ratio}, days_available)
    """
    conn = _get_db()
    try:
        date_rows = conn.execute("""
            SELECT DISTINCT trade_date FROM daily_snapshot
            WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?
        """, (target_date, days)).fetchall()
        if not date_rows:
            return {}, 0
        dates_avail = [r[0] for r in date_rows]
        days_available = len(dates_avail)

        cum_ratio = {}
        candidates = None
        for d in dates_avail:
            rows = conn.execute("""
                SELECT symbol, foreign_net_amt, foreign_own_pct
                FROM daily_snapshot WHERE trade_date = ?
            """, (d,)).fetchall()
            daily_positive = set()
            for r in rows:
                if (r["foreign_net_amt"] or 0) > 0:
                    daily_positive.add(r["symbol"])
                    cum_ratio[r["symbol"]] = (
                        cum_ratio.get(r["symbol"], 0) + (r["foreign_own_pct"] or 0)
                    )
            if candidates is None:
                candidates = daily_positive
            else:
                candidates &= daily_positive

        result = {t: round(cum_ratio.get(t, 0), 4) for t in (candidates or set())}
        return result, days_available
    finally:
        conn.close()


def _summarize_filters(filters: dict) -> dict:
    """필터 요약 (내부 표시용)."""
    summary = {}
    keys = ["market_cap_min", "market_cap_max", "chg_pct_min", "chg_pct_max",
            "foreign_ratio_min", "fi_ratio_min", "per_min", "per_max",
            "pbr_min", "pbr_max", "turnover_min", "sort", "n", "market"]
    for k in keys:
        v = filters.get(k)
        if v is not None:
            summary[k] = v
    return summary


def scan_stocks(db: dict, filters: dict, preset: str = None) -> dict:
    """필터 조건으로 종목 스캔.

    filters keys:
        market_cap_min/max (억원), chg_pct_min/max (%), foreign_ratio_min,
        fi_ratio_min, per_min/max, pbr_max, turnover_min,
        sort (str), n (int), market (kospi/kosdaq/all)

    Returns: {date, preset, filters, count, results: [...]}
    """
    stocks = db.get("stocks", {})
    date = db.get("date", "")

    # ── 프리셋 적용 ──
    preset_desc = None
    if preset and preset in PRESETS:
        p = PRESETS[preset]
        preset_desc = p.get("description", "")
        pf = p.get("filters", {})
        merged = {**pf}
        for k, v in filters.items():
            if v is not None:
                merged[k] = v
        filters = merged
        if "sort" not in filters or filters.get("sort") is None:
            filters["sort"] = p.get("sort", "fi_ratio")

    # 필터 파라미터
    mcap_min = float(filters.get("market_cap_min", 0)) * 100_000_000    # 억원 → 원
    mcap_max = float(filters.get("market_cap_max", 9999999)) * 100_000_000
    chg_min = float(filters.get("chg_pct_min", -30))
    chg_max = float(filters.get("chg_pct_max", 30))
    fr_min = float(filters.get("foreign_ratio_min", -999))
    fi_min = float(filters.get("fi_ratio_min", -999))
    per_min = float(filters.get("per_min", 0))
    per_max = float(filters.get("per_max", 9999))
    pbr_min = float(filters.get("pbr_min", 0))
    pbr_max = float(filters.get("pbr_max", 9999))
    turn_min = float(filters.get("turnover_min", 0))
    sort_by = filters.get("sort", "fi_ratio")
    n = int(filters.get("n", 30))
    n = max(1, min(n, 100))
    market_filter = filters.get("market", "all")

    # 시장 평균 등락률
    summary = db.get("market_summary", {})
    market_avg_chg = round(
        (summary.get("kospi_avg_chg", 0) + summary.get("kosdaq_avg_chg", 0)) / 2, 2)

    # relative_strength: 동적 chg_pct_min
    if preset == "relative_strength":
        if "chg_pct_min" not in filters or filters["chg_pct_min"] == chg_min:
            chg_min = market_avg_chg + 3.0
        fi_min = max(fi_min, 0)

    # foreign_streak: 연속 매수 종목 + 누적 비율
    streak_data = None
    days_available = 0
    if preset == "foreign_streak":
        streak_days = max(2, int(filters.get("streak_days", 5)))
        streak_data, days_available = _get_foreign_streak_data_db(date, streak_days)
        if days_available < streak_days:
            preset_desc = f"최근 {days_available}/{streak_days}일 연속 외인 순매수 (DB 부족)"
        if not streak_data:
            return {
                "date": date,
                "preset": preset,
                "preset_description": preset_desc,
                "filters": _summarize_filters(filters),
                "market_avg_chg": market_avg_chg,
                "days_available": days_available,
                "total_matched": 0,
                "count": 0,
                "results": [],
                "note": f"연속 매수 종목 없음 (가용 DB: {days_available}/{streak_days}일)",
            }

    # ── 필터링 ──
    results = []
    for ticker, s in stocks.items():
        mcap = s.get("market_cap", 0) or 0
        if mcap < mcap_min or mcap > mcap_max:
            continue
        chg = s.get("chg_pct", 0) or 0
        if chg < chg_min or chg > chg_max:
            continue
        fr = s.get("foreign_ratio", 0) or 0
        if fr < fr_min:
            continue
        fi = s.get("fi_ratio") or 0
        if fi < fi_min:
            continue
        per = s.get("per", 0) or 0
        if per_min > 0 and (per < per_min or per > per_max):
            continue
        if per_max < 9999 and per > per_max:
            continue
        pbr = s.get("pbr", 0) or 0
        if pbr_min > 0 and pbr < pbr_min:
            continue
        if pbr_max < 9999 and pbr > pbr_max:
            continue
        turn = s.get("turnover", 0) or 0
        if turn < turn_min:
            continue
        if market_filter != "all":
            if s.get("market", "") != market_filter:
                continue
        if streak_data is not None and ticker not in streak_data:
            continue

        item = {
            "ticker": ticker,
            "name": s.get("name", ticker),
            "market": s.get("market", ""),
            "close": s.get("close", 0),
            "chg_pct": chg,
            "market_cap": round(mcap / 100_000_000),  # 원 → 억원
            "per": per,
            "pbr": pbr,
            "foreign_ratio": fr,
            "inst_ratio": s.get("inst_ratio", 0) or 0,
            "fi_ratio": fi,
            "turnover": turn,
        }
        if streak_data is not None:
            item["cum_foreign_ratio"] = streak_data.get(ticker, 0)
        results.append(item)

    # ── 정렬 ──
    reverse = True
    if sort_by in ("per", "pbr"):
        reverse = False
    if sort_by == "chg_pct" and preset == "oversold":
        reverse = False
    results.sort(key=lambda x: x.get(sort_by, 0) or 0, reverse=reverse)
    total_matched = len(results)
    results = results[:n]

    out = {
        "date": date,
        "preset": preset,
        "preset_description": preset_desc,
        "filters": _summarize_filters(filters),
        "market_avg_chg": market_avg_chg,
        "total_matched": total_matched,
        "count": len(results),
        "results": results,
    }
    if preset == "foreign_streak":
        out["days_available"] = days_available
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 재무 수집 (주 1회)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

async def collect_financial_weekly(date: str = None) -> dict:
    """손익계산서 + 대차대조표 수집 → financial_quarterly UPSERT.
    주 1회 실행. KIS API kis_income_statement / kis_balance_sheet 사용.
    """
    global _RATE_SEM
    _RATE_SEM = asyncio.Semaphore(8)

    conn = _get_db()
    tickers = [r["symbol"] for r in conn.execute(
        "SELECT symbol FROM stock_master"
    ).fetchall()]

    if not tickers:
        conn.close()
        return {"error": "stock_master 비어 있음"}

    from kis_api import get_kis_token

    token = await get_kis_token()
    if not token:
        conn.close()
        return {"error": "KIS 토큰 발급 실패"}

    success_is = 0
    success_bs = 0
    success_dart = 0

    async with aiohttp.ClientSession() as session:
        # Phase A: 손익계산서
        print(f"[Finance] Phase A — 손익계산서 {len(tickers)}종목")
        for i, ticker in enumerate(tickers):
            try:
                from kis_api import kis_income_statement
                rows_is = await _rate_limited(
                    kis_income_statement(ticker, token, session=session)
                )
                for r in (rows_is or []):
                    rp = r.get("report_period", "")
                    if not rp:
                        continue
                    conn.execute("""
                        INSERT OR REPLACE INTO financial_quarterly (
                            symbol, report_period, revenue, cost_of_sales, gross_profit,
                            operating_profit, op_profit, net_income, collected_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """, (
                        ticker, rp,
                        r.get("revenue"), r.get("cost_of_sales"), r.get("gross_profit"),
                        r.get("operating_profit"), r.get("op_profit"), r.get("net_income"),
                    ))
                success_is += 1
            except Exception:
                pass
            if (i + 1) % 200 == 0:
                print(f"[Finance] 손익계산서: {i+1}/{len(tickers)}")
                conn.commit()

        conn.commit()

        # Phase B: 대차대조표
        print(f"[Finance] Phase B — 대차대조표 {len(tickers)}종목")
        for i, ticker in enumerate(tickers):
            try:
                from kis_api import kis_balance_sheet
                rows_bs = await _rate_limited(
                    kis_balance_sheet(ticker, token, session=session)
                )
                for r in (rows_bs or []):
                    rp = r.get("report_period", "")
                    if not rp:
                        continue
                    conn.execute("""
                        UPDATE financial_quarterly SET
                            current_assets=?, fixed_assets=?, total_assets=?,
                            current_liab=?, fixed_liab=?, total_liab=?,
                            capital=?, total_equity=?,
                            collected_at=datetime('now')
                        WHERE symbol=? AND report_period=?
                    """, (
                        r.get("current_assets"), r.get("fixed_assets"), r.get("total_assets"),
                        r.get("current_liab"), r.get("fixed_liab"), r.get("total_liab"),
                        r.get("capital"), r.get("total_equity"),
                        ticker, rp,
                    ))
                success_bs += 1
            except Exception:
                pass
            if (i + 1) % 200 == 0:
                print(f"[Finance] 대차대조표: {i+1}/{len(tickers)}")
                conn.commit()

        conn.commit()

        # Phase C: DART 현금흐름표 + 지배귀속 + 판관비/매출채권/재고 (최신 4분기)
        # F/M/FCF Phase1 — dart_quarterly_full 1콜로 PL/BS/CF 전체
        try:
            from kis_api import get_dart_corp_map, dart_quarterly_full
            corp_map = await get_dart_corp_map({})
        except Exception as e:
            print(f"[Finance] Phase C skip — corp_map 로드 실패: {e}")
            corp_map = {}

        if corp_map:
            # 최신 4분기 (현재연도 Q1 ~ 전년도 Q2) 기준 — TTM 1회분 확보
            from datetime import datetime as _dt
            now = _dt.now(KST)
            # DART 공시 지연(~45일) 감안: 직전 확정 분기부터 과거로 4개
            current_q = (now.month - 1) // 3 + 1
            targets = []  # (year, quarter)
            y, q = now.year, max(current_q - 1, 1) if current_q > 1 else 4
            if current_q == 1:
                y = now.year - 1
            for _ in range(4):
                targets.append((y, q))
                q -= 1
                if q < 1:
                    q = 4
                    y -= 1

            print(f"[Finance] Phase C — DART 현금흐름표 {len(tickers)}종목 × "
                  f"{len(targets)}분기 = {len(tickers) * len(targets)}콜")
            success_dart = await _collect_dart_full_batch(
                conn, tickers, corp_map, targets
            )

    # 재무 파생값 → daily_snapshot UPDATE
    _update_financial_derived(conn, date)
    conn.close()

    print(f"[Finance] 완료 — IS:{success_is}/{len(tickers)}, "
          f"BS:{success_bs}/{len(tickers)}, DART:{success_dart}")
    return {
        "tickers": len(tickers),
        "income_statement": success_is,
        "balance_sheet": success_bs,
        "dart_full": success_dart,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 전체 재무제표 배치 (F/M/FCF Phase1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# DART 분당 1000건 제한 → 안전 마진 900/분 = 0.067초/콜
_DART_INTERVAL = 0.067


def _upsert_dart_full_row(conn: sqlite3.Connection, ticker: str, r: dict):
    """dart_quarterly_full 결과 1분기를 financial_quarterly에 UPSERT.
    기존 KIS IS/BS 데이터를 덮어쓰지 않기 위해 COALESCE 패턴 사용.
    10개 신규 컬럼 + (없을 때) revenue/operating_profit/net_income/… 보강.
    """
    rp = r.get("report_period", "")
    if not rp:
        return False

    # 먼저 row 존재 보장 (INSERT OR IGNORE로 PK만 채움)
    conn.execute(
        "INSERT OR IGNORE INTO financial_quarterly (symbol, report_period, collected_at) "
        "VALUES (?, ?, datetime('now'))",
        (ticker, rp),
    )
    # 신규 컬럼은 항상 DART 값으로 덮어쓰기 (KIS에는 없음)
    # 기존 컬럼은 COALESCE로 기존값 유지
    conn.execute("""
        UPDATE financial_quarterly SET
            revenue          = COALESCE(revenue, ?),
            cost_of_sales    = COALESCE(cost_of_sales, ?),
            gross_profit    = COALESCE(gross_profit, ?),
            operating_profit = COALESCE(operating_profit, ?),
            net_income       = COALESCE(net_income, ?),
            current_assets   = COALESCE(current_assets, ?),
            total_assets     = COALESCE(total_assets, ?),
            current_liab     = COALESCE(current_liab, ?),
            total_liab       = COALESCE(total_liab, ?),
            capital          = COALESCE(capital, ?),
            total_equity     = COALESCE(total_equity, ?),
            cfo              = ?,
            capex            = ?,
            fcf              = ?,
            depreciation     = ?,
            sga              = ?,
            receivables      = ?,
            inventory        = ?,
            shares_out       = ?,
            net_income_parent = ?,
            equity_parent    = ?,
            fs_source        = ?,
            collected_at     = datetime('now')
        WHERE symbol=? AND report_period=?
    """, (
        r.get("revenue"), r.get("cost_of_sales"), r.get("gross_profit"),
        r.get("operating_profit"), r.get("net_income"),
        r.get("current_assets"), r.get("total_assets"),
        r.get("current_liab"), r.get("total_liab"),
        r.get("capital"), r.get("total_equity"),
        r.get("cfo"), r.get("capex"), r.get("fcf"),
        r.get("depreciation"), r.get("sga"),
        r.get("receivables"), r.get("inventory"),
        r.get("shares_out"), r.get("net_income_parent"),
        r.get("equity_parent"), r.get("fs_source"),
        ticker, rp,
    ))
    return True


async def _collect_dart_full_batch(conn: sqlite3.Connection, tickers: list,
                                    corp_map: dict,
                                    targets: list) -> int:
    """DART fnlttSinglAcntAll 배치 수집 (tickers × targets).

    tickers: [symbol, ...]
    corp_map: {symbol: corp_code}
    targets: [(year, quarter), ...]
    반환: 성공 콜 수 (종목·분기 단위)
    """
    from kis_api import dart_quarterly_full

    success = 0
    total = len(tickers) * len(targets)
    done = 0
    skipped_no_corp = 0

    async with aiohttp.ClientSession() as session:
        for ticker in tickers:
            corp_code = corp_map.get(ticker)
            if not corp_code:
                skipped_no_corp += 1
                done += len(targets)
                continue
            for (y, q) in targets:
                try:
                    r = await dart_quarterly_full(corp_code, y, q, session=session)
                    if r:
                        _upsert_dart_full_row(conn, ticker, r)
                        success += 1
                except Exception:
                    pass
                done += 1
                await asyncio.sleep(_DART_INTERVAL)
                if done % 500 == 0:
                    conn.commit()
                    print(f"[DART-Full] 진행: {done}/{total} (성공 {success})")
        conn.commit()

    print(f"[DART-Full] 완료 — 성공 {success}/{total}, corp_map 미등록 스킵 {skipped_no_corp}")
    return success


async def collect_financial_historical(quarters_back: int = 12,
                                       tickers_limit: int | None = None) -> dict:
    """최근 N분기 DART 전체 재무제표 소급 수집 (F/M/FCF Phase1 1회용).

    유니버스 3200종목 × 12분기 = ~38,400콜.
    DART 분당 1000콜 제한 → 0.067초/콜 = 약 43분 소요.

    Args:
        quarters_back: 과거 몇 분기 수집할지 (기본 12 = 3년)
        tickers_limit: 테스트용 종목 수 제한 (None=전종목)

    Returns:
        {"tickers": N, "quarters": Q, "calls_made": N*Q, "success": S,
         "duration_sec": T}
    """
    from datetime import datetime as _dt
    import time

    conn = _get_db()
    tickers = [r["symbol"] for r in conn.execute(
        "SELECT symbol FROM stock_master"
    ).fetchall()]
    if tickers_limit:
        tickers = tickers[:tickers_limit]

    if not tickers:
        conn.close()
        return {"error": "stock_master 비어 있음"}

    # corp_codes.json (3959종목) 우선, fallback으로 dart_corp_map.json (211종목)
    try:
        from kis_api import load_corp_codes, get_dart_corp_map
        full_map = await load_corp_codes()  # {ticker: {corp_code, corp_name}}
        corp_map = {tk: v["corp_code"] for tk, v in full_map.items() if v.get("corp_code")}
        if not corp_map:
            legacy = await get_dart_corp_map({})
            corp_map = legacy if isinstance(legacy, dict) else {}
    except Exception as e:
        conn.close()
        return {"error": f"corp_map 로드 실패: {e}"}

    if not corp_map:
        conn.close()
        return {"error": "corp_map 비어 있음 — corp_codes.json / dart_corp_map.json 확인"}

    print(f"[Historical] corp_map 엔트리: {len(corp_map)}종목")

    # 타겟 분기 리스트 생성 (DART 공시 지연 ~45일 고려 → 직전 확정 분기부터)
    now = _dt.now(KST)
    current_q = (now.month - 1) // 3 + 1
    y, q = now.year, current_q - 1
    if q < 1:
        q = 4
        y -= 1
    targets = []
    for _ in range(quarters_back):
        targets.append((y, q))
        q -= 1
        if q < 1:
            q = 4
            y -= 1

    total_calls = len(tickers) * len(targets)
    print(f"[Historical] 대상: {len(tickers)}종목 × {len(targets)}분기 = {total_calls}콜")
    print(f"[Historical] 예상 소요: 약 {total_calls * _DART_INTERVAL / 60:.1f}분")
    print(f"[Historical] 타겟 분기: {targets[0]} ~ {targets[-1]}")

    start = time.time()
    success = await _collect_dart_full_batch(conn, tickers, corp_map, targets)
    duration = time.time() - start

    conn.close()

    return {
        "tickers": len(tickers),
        "quarters": len(targets),
        "calls_made": total_calls,
        "success": success,
        "duration_sec": round(duration, 1),
        "target_range": f"{targets[-1]} ~ {targets[0]}",
    }


def _update_financial_derived(conn: sqlite3.Connection, date: str = None):
    """financial_quarterly 최신 분기 → daily_snapshot 재무 파생 컬럼 UPDATE."""
    if date is None:
        row = conn.execute("SELECT MAX(trade_date) as d FROM daily_snapshot").fetchone()
        date = row["d"] if row and row["d"] else None
    if not date:
        return

    # 각 종목의 최신 분기 재무
    financials = conn.execute("""
        SELECT f.* FROM financial_quarterly f
        INNER JOIN (
            SELECT symbol, MAX(report_period) as max_period
            FROM financial_quarterly
            GROUP BY symbol
        ) latest ON f.symbol = latest.symbol AND f.report_period = latest.max_period
    """).fetchall()

    updated = 0
    for f in financials:
        sym = f["symbol"]
        rev = f["revenue"] or 0
        op = f["operating_profit"] or 0
        ni = f["net_income"] or 0
        ta = f["total_assets"] or 0
        tl = f["total_liab"] or 0
        te = f["total_equity"] or 0

        op_margin = round(op / rev * 100, 4) if rev else None
        net_margin = round(ni / rev * 100, 4) if rev else None
        debt_ratio = round(tl / te * 100, 4) if te else None
        roe = round(ni / te * 100, 4) if te else None

        # 전분기 대비 성장률
        prev = conn.execute("""
            SELECT revenue, operating_profit FROM financial_quarterly
            WHERE symbol=? AND report_period < ?
            ORDER BY report_period DESC LIMIT 1
        """, (sym, f["report_period"])).fetchone()

        rev_growth = None
        op_growth = None
        if prev:
            prev_rev = prev["revenue"] or 0
            prev_op = prev["operating_profit"] or 0
            if prev_rev and abs(prev_rev) > 0:
                rev_growth = round((rev - prev_rev) / abs(prev_rev) * 100, 4)
            if prev_op and abs(prev_op) > 0:
                op_growth = round((op - prev_op) / abs(prev_op) * 100, 4)

        try:
            conn.execute("""
                UPDATE daily_snapshot SET
                    revenue=?, operating_profit=?, net_income=?,
                    total_assets=?, total_liabilities=?, total_equity=?,
                    operating_margin=?, net_margin=?, debt_ratio=?, roe=?,
                    revenue_growth=?, op_growth=?
                WHERE trade_date=? AND symbol=?
            """, (
                rev, op, ni, ta, tl, te,
                op_margin, net_margin, debt_ratio, roe,
                rev_growth, op_growth,
                date, sym,
            ))
            updated += 1
        except Exception as e:
            print(f"[Finance] {sym} 재무파생 UPDATE 실패: {e}")

    conn.commit()
    print(f"[Finance] 재무 파생값 UPDATE 완료: {updated}종목 ({date})")


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# iCloud Drive 백업
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def backup_to_icloud():
    """data/ → iCloud Drive 백업. 최근 2개 유지 (current / previous)."""
    import shutil

    ICLOUD_BASE = os.path.expanduser(
        "~/Library/Mobile Documents/com~apple~CloudDocs/stock-bot-backup"
    )
    CURRENT = os.path.join(ICLOUD_BASE, "current")
    PREVIOUS = os.path.join(ICLOUD_BASE, "previous")

    # 1. previous 삭제
    if os.path.exists(PREVIOUS):
        shutil.rmtree(PREVIOUS)

    # 2. current → previous 이동
    if os.path.exists(CURRENT):
        os.rename(CURRENT, PREVIOUS)

    # 3. 새 current 생성
    os.makedirs(CURRENT, exist_ok=True)

    # 4. 파일 복사
    data_dir = os.environ.get("DATA_DIR", "data")

    # stock.db
    db_src = os.path.join(data_dir, "stock.db")
    if os.path.exists(db_src):
        shutil.copy2(db_src, os.path.join(CURRENT, "stock.db"))

    # *.json, *.md, *.txt (최상위만, krx_db/ 제외)
    for f in os.listdir(data_dir):
        src = os.path.join(data_dir, f)
        if os.path.isfile(src) and (
            f.endswith(".json") or f.endswith(".md") or f.endswith(".txt")
        ):
            shutil.copy2(src, os.path.join(CURRENT, f))

    # research/ 폴더
    research_src = os.path.join(data_dir, "research")
    research_dst = os.path.join(CURRENT, "research")
    if os.path.isdir(research_src):
        shutil.copytree(research_src, research_dst, dirs_exist_ok=True)

    # 백업 타임스탬프
    with open(os.path.join(CURRENT, "_backup_time.txt"), "w") as f:
        f.write(datetime.now(KST).isoformat())

    print(f"[backup_to_icloud] 완료 → {CURRENT}")
    return {"ok": True, "path": CURRENT}


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# TTM 계산 엔진 (F/M/FCF Phase2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# Flow 항목 (분기별 "누적" 값 → 차분으로 단분기 산출 후 4분기 합산)
_TTM_FLOW_FIELDS = (
    "revenue", "operating_profit", "net_income", "net_income_parent",
    "cfo", "capex", "fcf", "depreciation", "sga",
    "cost_of_sales", "gross_profit",
)
# Stock 항목 (대차대조표: end_period 시점 값 그대로)
_TTM_STOCK_FIELDS = (
    "total_assets", "current_assets", "total_liab", "current_liab",
    "total_equity", "equity_parent", "receivables", "inventory",
    "shares_out",
    "fixed_assets", "fixed_liab",  # F/M-Score 에서 AQI/DEPI 근사용
)


def _parse_period(period: str) -> tuple[int, int] | None:
    """YYYYMM (예: 202412) → (year, quarter). quarter: 1/2/3/4."""
    if not period or len(period) != 6 or not period.isdigit():
        return None
    y = int(period[:4])
    m = int(period[4:])
    q_map = {3: 1, 6: 2, 9: 3, 12: 4}
    q = q_map.get(m)
    if q is None:
        return None
    return (y, q)


def _build_period(year: int, quarter: int) -> str:
    """(year, quarter) → 'YYYYMM' (quarter 1→03, 2→06, 3→09, 4→12)."""
    return f"{year}{quarter * 3:02d}"


def _compute_ttm(conn: sqlite3.Connection, ticker: str, end_period: str) -> dict:
    """TTM (Trailing Twelve Months) 재무 지표 계산.

    한국 DART 분기 보고서는 "당해 연도 누적" 값을 반환함:
      1분기(YYYY03) = 3개월 누적
      반기  (YYYY06) = 6개월 누적
      3분기(YYYY09) = 9개월 누적
      사업  (YYYY12) = 12개월 누적 (= 연간)

    따라서 단순 4분기 합산은 중복 계상됨.
    TTM 공식:
        Qn of year Y (n<4): cumulative(Qn,Y) + annual(Y-1) - cumulative(Qn,Y-1)
        Q4 of year Y      : annual(Y)  (그대로)

    Args:
        conn: SQLite 연결
        ticker: 종목코드
        end_period: 'YYYYMM' (기준 분기 말)

    Returns:
        dict {
          revenue, operating_profit, net_income, ...(flow),
          total_assets, current_assets, ..., shares_out (stock, end_period 시점 값),
          period_end: end_period,
          periods_used: [리스트],
          is_ttm_complete: bool  (True = flow 계산에 필요한 모든 분기 데이터 보유),
        }
        실패 시 {"period_end": end_period, "is_ttm_complete": False, 필드는 모두 None}.
    """
    parsed = _parse_period(end_period)
    flow_fields = list(_TTM_FLOW_FIELDS)
    stock_fields = list(_TTM_STOCK_FIELDS)

    # 기본 반환 템플릿 (전 필드 None)
    out: dict = {f: None for f in (*flow_fields, *stock_fields)}
    out["period_end"] = end_period
    out["periods_used"] = []
    out["is_ttm_complete"] = False

    if parsed is None:
        return out
    year, quarter = parsed

    # end_period row (Stock 필드 + flow 누적값)
    end_row = conn.execute(
        "SELECT * FROM financial_quarterly WHERE symbol=? AND report_period=?",
        (ticker, end_period),
    ).fetchone()
    if end_row is None:
        return out

    # Stock 필드: end_period 시점 값 그대로
    for f in stock_fields:
        try:
            out[f] = end_row[f]
        except (IndexError, KeyError):
            out[f] = None

    # TTM flow 계산
    if quarter == 4:
        # Q4 = 연간 (12개월 누적) = 그대로
        periods_used = [end_period]
        for f in flow_fields:
            try:
                out[f] = end_row[f]
            except (IndexError, KeyError):
                out[f] = None
        # 필수 핵심 필드가 하나라도 있으면 complete로 간주
        out["is_ttm_complete"] = any(out[f] is not None for f in flow_fields)
    else:
        # n<4: TTM = cum(Qn,Y) + annual(Y-1) - cum(Qn,Y-1)
        prev_annual_period = _build_period(year - 1, 4)
        prev_same_q_period = _build_period(year - 1, quarter)
        periods_used = [end_period, prev_annual_period, prev_same_q_period]

        prev_annual = conn.execute(
            "SELECT * FROM financial_quarterly WHERE symbol=? AND report_period=?",
            (ticker, prev_annual_period),
        ).fetchone()
        prev_same_q = conn.execute(
            "SELECT * FROM financial_quarterly WHERE symbol=? AND report_period=?",
            (ticker, prev_same_q_period),
        ).fetchone()

        all_present = prev_annual is not None and prev_same_q is not None
        out["is_ttm_complete"] = all_present

        if all_present:
            for f in flow_fields:
                try:
                    cur = end_row[f]
                    ann = prev_annual[f]
                    prev_q = prev_same_q[f]
                except (IndexError, KeyError):
                    out[f] = None
                    continue
                # 보수적: 3개 값 중 하나라도 NULL이면 TTM도 NULL
                if cur is None or ann is None or prev_q is None:
                    out[f] = None
                    continue
                out[f] = cur + ann - prev_q
        else:
            # 불완전: 그래도 end_row 값만이라도 채워둠 (참고용, is_ttm_complete=False 표시)
            for f in flow_fields:
                try:
                    out[f] = end_row[f]
                except (IndexError, KeyError):
                    out[f] = None

    out["periods_used"] = periods_used
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 발행주식수 소급 수집 (F/M/FCF Phase2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
async def collect_shares_historical(quarters_back: int = 12,
                                     tickers_limit: int | None = None) -> dict:
    """DART stockTotqySttus API로 보통주 발행주식수 N분기 소급.

    financial_quarterly.shares_out (주 단위) UPDATE.
    이미 값이 있어도 덮어씀 (최신 API 결과 우선).

    Args:
        quarters_back: 과거 몇 분기 수집 (기본 12 = 3년)
        tickers_limit: 테스트용 상한 (None=전종목)

    Returns:
        {"tickers", "quarters", "calls_made", "success", "updated", "duration_sec"}
    """
    from datetime import datetime as _dt
    import time

    conn = _get_db()
    tickers = [r["symbol"] for r in conn.execute(
        "SELECT symbol FROM stock_master"
    ).fetchall()]
    if tickers_limit:
        tickers = tickers[:tickers_limit]
    if not tickers:
        conn.close()
        return {"error": "stock_master 비어 있음"}

    # corp_codes.json(3959) 우선, fallback dart_corp_map.json(211)
    try:
        from kis_api import load_corp_codes, get_dart_corp_map
        full_map = await load_corp_codes()
        corp_map = {tk: v["corp_code"] for tk, v in full_map.items()
                    if v.get("corp_code")}
        if not corp_map:
            legacy = await get_dart_corp_map({})
            corp_map = legacy if isinstance(legacy, dict) else {}
    except Exception as e:
        conn.close()
        return {"error": f"corp_map 로드 실패: {e}"}
    if not corp_map:
        conn.close()
        return {"error": "corp_map 비어 있음"}

    # 타겟 분기 (DART 공시 지연 ~45일)
    now = _dt.now(KST)
    current_q = (now.month - 1) // 3 + 1
    y, q = now.year, current_q - 1
    if q < 1:
        q = 4
        y -= 1
    targets = []
    for _ in range(quarters_back):
        targets.append((y, q))
        q -= 1
        if q < 1:
            q = 4
            y -= 1

    total_calls = len(tickers) * len(targets)
    print(f"[SharesHist] corp_map {len(corp_map)}종목, 대상 {len(tickers)}×{len(targets)}={total_calls}콜")
    print(f"[SharesHist] 예상 {total_calls * _DART_INTERVAL / 60:.1f}분, "
          f"타겟 {targets[-1]}~{targets[0]}")

    from kis_api import dart_shares_outstanding

    success = 0
    updated = 0
    done = 0
    skipped_no_corp = 0
    start = time.time()

    async with aiohttp.ClientSession() as session:
        for ticker in tickers:
            corp_code = corp_map.get(ticker)
            if not corp_code:
                skipped_no_corp += 1
                done += len(targets)
                continue
            for (ty, tq) in targets:
                rp = _build_period(ty, tq)
                try:
                    shares = await dart_shares_outstanding(
                        corp_code, ty, tq, session=session
                    )
                    if shares is not None and shares > 0:
                        success += 1
                        # row 없으면 생성 (collect_financial_historical 이후 shares만 채우는 케이스도 대응)
                        conn.execute(
                            "INSERT OR IGNORE INTO financial_quarterly "
                            "(symbol, report_period, collected_at) "
                            "VALUES (?, ?, datetime('now'))",
                            (ticker, rp),
                        )
                        cur = conn.execute(
                            "UPDATE financial_quarterly SET shares_out=? "
                            "WHERE symbol=? AND report_period=?",
                            (shares, ticker, rp),
                        )
                        if cur.rowcount > 0:
                            updated += 1
                except Exception:
                    pass
                done += 1
                await asyncio.sleep(_DART_INTERVAL)
                if done % 500 == 0:
                    conn.commit()
                    print(f"[SharesHist] {done}/{total_calls} (성공 {success}, UPDATE {updated})")
        conn.commit()

    conn.close()
    duration = time.time() - start
    print(f"[SharesHist] 완료 — 성공 {success}/{total_calls}, "
          f"UPDATE {updated}, corp_map 스킵 {skipped_no_corp}, {duration:.1f}초")
    return {
        "tickers": len(tickers),
        "quarters": len(targets),
        "calls_made": total_calls,
        "success": success,
        "updated": updated,
        "skipped_no_corp": skipped_no_corp,
        "duration_sec": round(duration, 1),
        "target_range": f"{targets[-1]} ~ {targets[0]}",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# F/M/FCF Phase3 — 메트릭 계산 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━
#
# 공통 원칙:
#   * TTM 엔진(_compute_ttm) 기반. 현재 TTM vs 4분기 전 TTM (YoY).
#   * net_income_parent 우선, None일 시 net_income으로 fallback (IS 없는 KR 기업 대응).
#   * fs_source == 'OFS_HOLDCO' (순수 지주사)는 F/M-Score 스킵 (영업활동 없음).
#   * 개별 지표 계산 불가(NULL) 시 False 취급 금지 → None으로 표시. score는 True만 카운트.
#   * ZeroDivisionError / None 연산은 명시적 체크.
#
# 단위:
#   * money 필드: 억원 (financial_quarterly에서 수집 시 이미 억원)
#   * shares_out: 주
#   * market_cap: 억원 (daily_snapshot)
# ━━━━━━━━━━━━━━━━━━━━━━━━━


def _prev_yoy_period(end_period: str) -> str | None:
    """end_period 기준 4분기 전(전년 동분기) period 반환. 'YYYYMM' → 'YYYYMM'."""
    parsed = _parse_period(end_period)
    if parsed is None:
        return None
    y, q = parsed
    return _build_period(y - 1, q)


def _fs_source(conn: sqlite3.Connection, ticker: str, end_period: str) -> str | None:
    """financial_quarterly.fs_source 조회."""
    row = conn.execute(
        "SELECT fs_source FROM financial_quarterly "
        "WHERE symbol=? AND report_period=?",
        (ticker, end_period),
    ).fetchone()
    if row is None:
        return None
    try:
        return row["fs_source"]
    except (IndexError, KeyError):
        return None


def _pick_net_income(ttm: dict) -> float | None:
    """지배주주 귀속 우선, 없으면 전체 순이익 fallback."""
    v = ttm.get("net_income_parent")
    if v is not None:
        return v
    return ttm.get("net_income")


def _safe_div(num, den):
    """None/0 안전 나눗셈. 둘 중 하나라도 None이거나 den=0 → None."""
    if num is None or den is None:
        return None
    try:
        if den == 0:
            return None
        return num / den
    except (TypeError, ZeroDivisionError):
        return None


def _compute_fscore(conn: sqlite3.Connection, ticker: str, end_period: str) -> dict:
    """Piotroski F-Score (0~9점) TTM YoY 기반 계산.

    9개 이분법 지표:
      1. ROA > 0               (TTM 순이익 / 평균 총자산)
      2. CFO > 0               (TTM CFO)
      3. ΔROA > 0              (현재 TTM ROA vs 전년 TTM ROA)
      4. CFO > NI              (이익 품질)
      5. Δ장기부채비율 < 0       (장기부채/총자산 감소, 장기부채=total_liab-current_liab)
      6. Δ유동비율 > 0          (유동자산/유동부채 증가)
      7. 주식수 증가 없음        (shares_out 전년 이하)
      8. ΔGPM > 0              (매출총이익률 증가)
      9. Δ자산회전율 > 0        (TTM 매출/평균 총자산 증가)

    각 지표 데이터 부족 시 None (False 아님). score는 True만 카운트.
    is_complete=True 조건: 9개 모두 True/False.
    순수 지주사(OFS_HOLDCO)는 빈 결과 반환.

    Returns:
      {
        "score": 0~9 | None,
        "details": {지표명: True/False/None},
        "period": end_period,
        "yoy_period": 전년 동분기,
        "is_complete": bool,
        "skipped": None | "holdco" | "no_data",
      }
    """
    yoy_period = _prev_yoy_period(end_period)
    details = {
        "roa_pos": None,
        "cfo_pos": None,
        "delta_roa_pos": None,
        "cfo_gt_ni": None,
        "delta_ltdebt_neg": None,
        "delta_current_ratio_pos": None,
        "shares_not_increased": None,
        "delta_gpm_pos": None,
        "delta_asset_turnover_pos": None,
    }
    result = {
        "score": None,
        "details": details,
        "period": end_period,
        "yoy_period": yoy_period,
        "is_complete": False,
        "skipped": None,
    }

    # 지주사 스킵
    src = _fs_source(conn, ticker, end_period)
    if src == "OFS_HOLDCO":
        result["skipped"] = "holdco"
        return result

    if yoy_period is None:
        result["skipped"] = "no_data"
        return result

    cur = _compute_ttm(conn, ticker, end_period)
    prev = _compute_ttm(conn, ticker, yoy_period)

    # 최소 현재 분기 row는 존재해야 진행 (prev는 일부만 있어도 계산 가능)
    if not cur.get("period_end") or cur.get("total_assets") is None:
        result["skipped"] = "no_data"
        return result

    ni_cur = _pick_net_income(cur)
    ni_prev = _pick_net_income(prev)
    ta_cur = cur.get("total_assets")
    ta_prev = prev.get("total_assets")
    ca_cur = cur.get("current_assets")
    ca_prev = prev.get("current_assets")
    cl_cur = cur.get("current_liab")
    cl_prev = prev.get("current_liab")
    tl_cur = cur.get("total_liab")
    tl_prev = prev.get("total_liab")
    cfo_cur = cur.get("cfo")
    rev_cur = cur.get("revenue")
    rev_prev = prev.get("revenue")
    gp_cur = cur.get("gross_profit")
    gp_prev = prev.get("gross_profit")
    cos_cur = cur.get("cost_of_sales")
    cos_prev = prev.get("cost_of_sales")
    sh_cur = cur.get("shares_out")
    sh_prev = prev.get("shares_out")

    # 평균 자산 (prev 없으면 current 단일 사용)
    if ta_cur is not None and ta_prev is not None:
        avg_ta_cur = (ta_cur + ta_prev) / 2
    else:
        avg_ta_cur = ta_cur

    # 전년 ROA 계산용 평균 자산: prev + 2기전 자산이 이상적이나 없음 → prev 단일
    avg_ta_prev = ta_prev

    # 1. ROA > 0
    roa_cur = _safe_div(ni_cur, avg_ta_cur)
    if roa_cur is not None:
        details["roa_pos"] = roa_cur > 0

    # 2. CFO > 0
    if cfo_cur is not None:
        details["cfo_pos"] = cfo_cur > 0

    # 3. ΔROA > 0
    roa_prev = _safe_div(ni_prev, avg_ta_prev)
    if roa_cur is not None and roa_prev is not None:
        details["delta_roa_pos"] = roa_cur > roa_prev

    # 4. CFO > NI
    # 단위 일관성: DART 파서(kis_api.dart_quarterly_full)에서 모든 money 필드를
    # 수집 시점에 //1e8 처리 → 전부 "억원" 단위. net_income도 억원.
    if cfo_cur is not None and ni_cur is not None:
        details["cfo_gt_ni"] = cfo_cur > ni_cur

    # 5. Δ장기부채비율 < 0 (장기부채/총자산)
    #    장기부채 = total_liab - current_liab
    def _ltdebt_ratio(tl, cl, ta):
        if tl is None or cl is None or ta is None or ta == 0:
            return None
        return (tl - cl) / ta
    ltd_cur = _ltdebt_ratio(tl_cur, cl_cur, ta_cur)
    ltd_prev = _ltdebt_ratio(tl_prev, cl_prev, ta_prev)
    if ltd_cur is not None and ltd_prev is not None:
        details["delta_ltdebt_neg"] = ltd_cur < ltd_prev

    # 6. Δ유동비율 > 0
    curr_cur = _safe_div(ca_cur, cl_cur)
    curr_prev = _safe_div(ca_prev, cl_prev)
    if curr_cur is not None and curr_prev is not None:
        details["delta_current_ratio_pos"] = curr_cur > curr_prev

    # 7. 주식수 증가 없음
    if sh_cur is not None and sh_prev is not None:
        details["shares_not_increased"] = sh_cur <= sh_prev

    # 8. ΔGPM > 0  — GPM = gross_profit / revenue. 없으면 (revenue - cost_of_sales)/revenue
    def _gpm(gp, cos, rev):
        if rev is None or rev == 0:
            return None
        if gp is not None:
            return gp / rev
        if cos is not None:
            return (rev - cos) / rev
        return None
    gpm_cur = _gpm(gp_cur, cos_cur, rev_cur)
    gpm_prev = _gpm(gp_prev, cos_prev, rev_prev)
    if gpm_cur is not None and gpm_prev is not None:
        details["delta_gpm_pos"] = gpm_cur > gpm_prev

    # 9. Δ자산회전율 > 0
    at_cur = _safe_div(rev_cur, avg_ta_cur)
    at_prev = _safe_div(rev_prev, avg_ta_prev)
    if at_cur is not None and at_prev is not None:
        details["delta_asset_turnover_pos"] = at_cur > at_prev

    # 집계
    score = sum(1 for v in details.values() if v is True)
    is_complete = all(v is not None for v in details.values())
    result["score"] = score
    result["is_complete"] = is_complete
    return result


def _compute_mscore(conn: sqlite3.Connection, ticker: str, end_period: str) -> dict:
    """Beneish M-Score (earnings manipulation detection).

    공식: M = -4.84 + 0.92·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI
              + 0.115·DEPI - 0.172·SGAI + 4.679·TATA - 0.327·LVGI

    임계값:
      M > -1.78        → "high"
      -2.22 < M ≤ -1.78 → "moderate"
      M ≤ -2.22        → "low"

    주의 — 컬럼 부재에 따른 근사:
      * PPE (유형자산) 컬럼 없음 → PPE ≈ total_assets - current_assets (비유동자산).
        단, 이 근사로는 AQI가 항상 0이 되어버림 (1 - (CA+(TA-CA))/TA = 0).
        → AQI 는 fixed_assets(고정자산, 비유동자산) 대신 inventory 기반 근사.
        여기선 AQI 변수를 제외 대신 TA 대비 receivables 변화 비율로 대체 근사.
      * Cash, CurrDebt 컬럼 없음 → TATA = (operating_profit - cfo) / total_assets 근사
        (오퍼레이팅 발생액 = OI - CFO의 전통적 근사식)

    Returns:
      {
        "mscore": float | None,
        "manipulation_risk": "high"/"moderate"/"low"/None,
        "variables": {DSRI, GMI, AQI, SGI, DEPI, SGAI, LVGI, TATA},
        "period": end_period,
        "yoy_period": 전년 동분기,
        "is_complete": bool,
        "skipped": None | "holdco" | "no_data",
      }
    """
    yoy_period = _prev_yoy_period(end_period)
    variables = {
        "DSRI": None, "GMI": None, "AQI": None, "SGI": None,
        "DEPI": None, "SGAI": None, "LVGI": None, "TATA": None,
    }
    result = {
        "mscore": None,
        "manipulation_risk": None,
        "variables": variables,
        "period": end_period,
        "yoy_period": yoy_period,
        "is_complete": False,
        "skipped": None,
    }

    src = _fs_source(conn, ticker, end_period)
    if src == "OFS_HOLDCO":
        result["skipped"] = "holdco"
        return result

    if yoy_period is None:
        result["skipped"] = "no_data"
        return result

    cur = _compute_ttm(conn, ticker, end_period)
    prev = _compute_ttm(conn, ticker, yoy_period)

    if cur.get("total_assets") is None:
        result["skipped"] = "no_data"
        return result

    # 필드 추출
    rev_c = cur.get("revenue")
    rev_p = prev.get("revenue")
    ar_c = cur.get("receivables")
    ar_p = prev.get("receivables")
    gp_c = cur.get("gross_profit")
    gp_p = prev.get("gross_profit")
    cos_c = cur.get("cost_of_sales")
    cos_p = prev.get("cost_of_sales")
    ca_c = cur.get("current_assets")
    ca_p = prev.get("current_assets")
    ta_c = cur.get("total_assets")
    ta_p = prev.get("total_assets")
    cl_c = cur.get("current_liab")
    cl_p = prev.get("current_liab")
    tl_c = cur.get("total_liab")
    tl_p = prev.get("total_liab")
    sga_c = cur.get("sga")  # 원 단위
    sga_p = prev.get("sga")
    dep_c = cur.get("depreciation")  # 원 단위
    dep_p = prev.get("depreciation")
    cfo_c = cur.get("cfo")  # 원 단위
    op_c = cur.get("operating_profit")  # 억원 단위

    # PPE 근사 (비유동자산 = total_assets - current_assets)
    def _ppe(ta, ca):
        if ta is None or ca is None:
            return None
        return ta - ca
    ppe_c = _ppe(ta_c, ca_c)
    ppe_p = _ppe(ta_p, ca_p)

    # 1. DSRI = (AR_t/Rev_t) / (AR_t-1/Rev_t-1)
    arr_c = _safe_div(ar_c, rev_c)
    arr_p = _safe_div(ar_p, rev_p)
    variables["DSRI"] = _safe_div(arr_c, arr_p)

    # 2. GMI = GM_t-1 / GM_t   (GM = gross_profit / revenue)
    def _gm(gp, cos, rev):
        if rev is None or rev == 0:
            return None
        if gp is not None:
            return gp / rev
        if cos is not None:
            return (rev - cos) / rev
        return None
    gm_c = _gm(gp_c, cos_c, rev_c)
    gm_p = _gm(gp_p, cos_p, rev_p)
    variables["GMI"] = _safe_div(gm_p, gm_c)

    # 3. AQI — 원공식 = (1 - (CA+PPE)/TA)_t / (...)_t-1
    # 우리 DB에는 PPE 컬럼 없음. total_assets - current_assets는 비유동자산
    # 전체(=PPE+무형+투자자산)이므로 "1 - (CA+비유동)/TA = 0" 으로 항상 0이 됨.
    # → 실용적 근사: fixed_assets(비유동자산) 있으면 PPE ≈ 0.5 * fixed_assets
    #   (제조업 평균: 유형자산이 비유동의 ~50%). 더 정밀한 대체는 Phase 후속에서.
    fa_c = cur.get("fixed_assets")
    fa_p = prev.get("fixed_assets")
    def _aqi_ratio(ca, fa, ta):
        if ca is None or fa is None or ta is None or ta == 0:
            return None
        ppe_approx = fa * 0.5  # 제조업 평균 가정
        return 1 - (ca + ppe_approx) / ta
    aqi_c = _aqi_ratio(ca_c, fa_c, ta_c)
    aqi_p = _aqi_ratio(ca_p, fa_p, ta_p)
    variables["AQI"] = _safe_div(aqi_c, aqi_p)

    # 4. SGI = Rev_t / Rev_t-1
    variables["SGI"] = _safe_div(rev_c, rev_p)

    # 5. DEPI = (Dep_t-1/(Dep_t-1+PPE_t-1)) / (Dep_t/(Dep_t+PPE_t))
    # 단위 일관성: DART 파서에서 모든 money 필드 //1e8 처리됐으므로
    # dep/depreciation 도 억원. PPE 근사 = fixed_assets * 0.5 (AQI와 동일).
    def _depi_ratio(dep, fa):
        if dep is None or fa is None:
            return None
        ppe_approx = fa * 0.5
        total = dep + ppe_approx
        if total == 0:
            return None
        return dep / total
    depi_c = _depi_ratio(dep_c, fa_c)
    depi_p = _depi_ratio(dep_p, fa_p)
    variables["DEPI"] = _safe_div(depi_p, depi_c)

    # 6. SGAI = (SGA/Rev)_t / (SGA/Rev)_t-1   — sga, rev 모두 억원
    def _sga_ratio(sga, rev):
        if sga is None or rev is None or rev == 0:
            return None
        return sga / rev
    sgar_c = _sga_ratio(sga_c, rev_c)
    sgar_p = _sga_ratio(sga_p, rev_p)
    variables["SGAI"] = _safe_div(sgar_c, sgar_p)

    # 7. LVGI = ((CL+LTD)/TA)_t / (...)_t-1
    # CL+LTD = total_liab (CL + (TL-CL) = TL) 로 근사
    lvgi_c = _safe_div(tl_c, ta_c)
    lvgi_p = _safe_div(tl_p, ta_p)
    variables["LVGI"] = _safe_div(lvgi_c, lvgi_p)

    # 8. TATA ≈ (operating_profit - CFO) / total_assets  — 발생액 근사
    # op/cfo/ta 모두 억원
    if op_c is not None and cfo_c is not None and ta_c is not None and ta_c != 0:
        variables["TATA"] = (op_c - cfo_c) / ta_c

    # 최종 M-Score
    if all(variables[k] is not None for k in
           ("DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "LVGI", "TATA")):
        m = (-4.84
             + 0.92 * variables["DSRI"]
             + 0.528 * variables["GMI"]
             + 0.404 * variables["AQI"]
             + 0.892 * variables["SGI"]
             + 0.115 * variables["DEPI"]
             - 0.172 * variables["SGAI"]
             + 4.679 * variables["TATA"]
             - 0.327 * variables["LVGI"])
        result["mscore"] = m
        result["is_complete"] = True
        if m > -1.78:
            result["manipulation_risk"] = "high"
        elif m > -2.22:
            result["manipulation_risk"] = "moderate"
        else:
            result["manipulation_risk"] = "low"
    return result


def _compute_fcf_metrics(conn: sqlite3.Connection, ticker: str, end_period: str,
                         market_cap: float | None = None) -> dict:
    """FCF 기반 3종 지표 TTM 기반.

    반환 단위:
      * fcf_ttm: 억원
      * fcf_to_assets, fcf_yield_ev, fcf_conversion: % (예: 5.3 = 5.3%)

    주의 — 단순화:
      * EV ≈ market_cap + total_liab (현금 컬럼 없어 cash 차감 생략)
      * FCF 전환율 = fcf / net_income. 순이익 ≤ 0 이면 None (의미 없음)
      * 단위: DART 파서가 수집 시 모든 money 필드를 //1e8 처리하므로
        financial_quarterly 의 fcf/cfo/depreciation 모두 "억원". market_cap도 억원.

    Returns:
      {
        "fcf_ttm": float | None (억원),
        "fcf_to_assets": float | None (%),
        "fcf_yield_ev": float | None (%),
        "fcf_conversion": float | None (%),
        "period": end_period,
        "is_complete": bool,
      }
    """
    result = {
        "fcf_ttm": None,
        "fcf_to_assets": None,
        "fcf_yield_ev": None,
        "fcf_conversion": None,
        "period": end_period,
        "is_complete": False,
    }

    ttm = _compute_ttm(conn, ticker, end_period)
    if ttm.get("period_end") is None:
        return result

    fcf = ttm.get("fcf")
    ta = ttm.get("total_assets")
    tl = ttm.get("total_liab")
    ni = _pick_net_income(ttm)

    # fcf 는 억원 단위 (DART 파서에서 //1e8 처리됨)
    if fcf is None:
        return result
    result["fcf_ttm"] = float(fcf)

    # FCF / 총자산
    if ta is not None and ta > 0:
        result["fcf_to_assets"] = (fcf / ta) * 100

    # FCF / EV
    if market_cap is not None and market_cap > 0 and tl is not None:
        ev = market_cap + tl
        if ev > 0:
            result["fcf_yield_ev"] = (fcf / ev) * 100

    # FCF / 순이익 (순이익>0 일 때만)
    if ni is not None and ni > 0:
        result["fcf_conversion"] = (fcf / ni) * 100

    # is_complete: 3개 모두 계산됐을 때
    core = (result["fcf_to_assets"], result["fcf_yield_ev"], result["fcf_conversion"])
    result["is_complete"] = all(v is not None for v in core)
    return result


def _ensure_alpha_columns(conn: sqlite3.Connection):
    """daily_snapshot 에 F/M/FCF 5컬럼 존재 보장. 없으면 ALTER ADD."""
    for sql in (
        "ALTER TABLE daily_snapshot ADD COLUMN fscore INTEGER",
        "ALTER TABLE daily_snapshot ADD COLUMN mscore REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_to_assets REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_yield_ev REAL",
        "ALTER TABLE daily_snapshot ADD COLUMN fcf_conversion REAL",
    ):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass


def _update_alpha_metrics(conn: sqlite3.Connection, ticker: str, end_period: str,
                          market_cap: float | None = None,
                          trade_date: str | None = None) -> bool:
    """F-Score + M-Score + FCF 계산 후 daily_snapshot(trade_date, symbol)에 UPDATE.

    Args:
        conn: SQLite 연결
        ticker: 종목코드
        end_period: 'YYYYMM' 기준 분기말 (재무 데이터 기준)
        market_cap: 억원 단위. 없으면 daily_snapshot 에서 조회 시도.
        trade_date: 'YYYYMMDD'. 없으면 daily_snapshot 최신 row 사용.

    Returns:
        True = UPDATE 발생, False = 해당 row 없음 or 데이터 없음.
    """
    _ensure_alpha_columns(conn)

    # trade_date 결정
    if trade_date is None:
        row = conn.execute(
            "SELECT trade_date, market_cap FROM daily_snapshot "
            "WHERE symbol=? ORDER BY trade_date DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        if row is None:
            return False
        trade_date = row["trade_date"]
        if market_cap is None:
            market_cap = row["market_cap"] if row["market_cap"] else None

    # market_cap 인자 없으면 해당 날짜 row에서 조회
    if market_cap is None:
        row = conn.execute(
            "SELECT market_cap FROM daily_snapshot WHERE trade_date=? AND symbol=?",
            (trade_date, ticker),
        ).fetchone()
        if row and row["market_cap"]:
            market_cap = row["market_cap"]

    fs = _compute_fscore(conn, ticker, end_period)
    ms = _compute_mscore(conn, ticker, end_period)
    fcf = _compute_fcf_metrics(conn, ticker, end_period, market_cap=market_cap)

    cur = conn.execute(
        "UPDATE daily_snapshot SET fscore=?, mscore=?, "
        "fcf_to_assets=?, fcf_yield_ev=?, fcf_conversion=? "
        "WHERE trade_date=? AND symbol=?",
        (
            fs.get("score"),
            ms.get("mscore"),
            fcf.get("fcf_to_assets"),
            fcf.get("fcf_yield_ev"),
            fcf.get("fcf_conversion"),
            trade_date, ticker,
        ),
    )
    return cur.rowcount > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# F/M/FCF 전종목 일괄 업데이트 (collect_daily 훅)
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def update_all_alpha_metrics(end_period: str | None = None,
                              trade_date: str | None = None) -> dict:
    """전종목 F-Score/M-Score/FCF 계산 후 daily_snapshot 5컬럼 UPDATE.

    Args:
        end_period: 재무 기준 분기 (YYYYMM). None이면 financial_quarterly에서
                    fs_source IS NOT NULL 중 최신 report_period 자동 선택.
        trade_date: daily_snapshot 대상 일자 (YYYYMMDD). None이면 MAX(trade_date).

    Returns:
        {"tickers": N, "success": S, "fscore_filled": F, "mscore_filled": M,
         "fcf_filled": FC, "duration_sec": T, "end_period": ..., "trade_date": ...}
    """
    start = datetime.now()
    conn = _get_db()
    try:
        _ensure_alpha_columns(conn)

        # end_period 자동 결정
        # 단순 MAX(report_period) 는 극소수 선행공시 분기(1~10건)를 골라버림 →
        # "커버리지가 충분한 최신 분기" 를 선택: count>=500 중 최신.
        # (기준치 500은 유니버스 2400종목의 ~20% 이상 — 신뢰할 수준)
        if end_period is None:
            row = conn.execute(
                "SELECT report_period FROM financial_quarterly "
                "WHERE fs_source IS NOT NULL "
                "GROUP BY report_period HAVING COUNT(*) >= 500 "
                "ORDER BY report_period DESC LIMIT 1"
            ).fetchone()
            end_period = row["report_period"] if row else None
            # fallback: 아무리 적어도 최신 분기 하나 사용
            if end_period is None:
                row = conn.execute(
                    "SELECT MAX(report_period) AS p FROM financial_quarterly "
                    "WHERE fs_source IS NOT NULL"
                ).fetchone()
                end_period = row["p"] if row and row["p"] else None
        if not end_period:
            conn.close()
            return {"error": "end_period 확보 실패 (financial_quarterly 비어있음)",
                    "tickers": 0, "success": 0}

        # trade_date 자동 결정
        if trade_date is None:
            row = conn.execute(
                "SELECT MAX(trade_date) AS t FROM daily_snapshot"
            ).fetchone()
            trade_date = row["t"] if row and row["t"] else None
        if not trade_date:
            conn.close()
            return {"error": "trade_date 확보 실패 (daily_snapshot 비어있음)",
                    "tickers": 0, "success": 0}

        # 대상 종목: fs_source 있는 재무 + 해당 trade_date에 daily_snapshot row 존재
        rows = conn.execute(
            "SELECT fq.symbol AS ticker, ds.market_cap AS market_cap "
            "FROM financial_quarterly fq "
            "JOIN daily_snapshot ds ON ds.symbol=fq.symbol "
            "WHERE fq.report_period=? AND fq.fs_source IS NOT NULL "
            "AND ds.trade_date=?",
            (end_period, trade_date),
        ).fetchall()

        tickers_total = len(rows)
        success = 0
        fscore_filled = 0
        mscore_filled = 0
        fcf_filled = 0
        errors = 0

        print(f"[AlphaMetrics] 시작 — end_period={end_period} "
              f"trade_date={trade_date} 대상 {tickers_total}종목")

        for r in rows:
            ticker = r["ticker"]
            mcap = r["market_cap"] if r["market_cap"] else None
            # database is locked 대비 최대 3회 재시도 (0.5s 간격)
            attempt = 0
            while True:
                try:
                    ok = _update_alpha_metrics(
                        conn, ticker, end_period,
                        market_cap=mcap, trade_date=trade_date,
                    )
                    if ok:
                        success += 1
                    break
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < 2:
                        attempt += 1
                        import time as _t
                        _t.sleep(0.5)
                        continue
                    errors += 1
                    if errors <= 5:
                        print(f"[AlphaMetrics] {ticker} 실패(lock): {e}")
                    break
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        print(f"[AlphaMetrics] {ticker} 실패: {e}")
                    break

        conn.commit()

        # 채움 수 집계 (WHERE IS NOT NULL count)
        fscore_filled = conn.execute(
            "SELECT COUNT(*) AS c FROM daily_snapshot "
            "WHERE trade_date=? AND fscore IS NOT NULL",
            (trade_date,),
        ).fetchone()["c"]
        mscore_filled = conn.execute(
            "SELECT COUNT(*) AS c FROM daily_snapshot "
            "WHERE trade_date=? AND mscore IS NOT NULL",
            (trade_date,),
        ).fetchone()["c"]
        fcf_filled = conn.execute(
            "SELECT COUNT(*) AS c FROM daily_snapshot "
            "WHERE trade_date=? AND fcf_to_assets IS NOT NULL",
            (trade_date,),
        ).fetchone()["c"]

        duration = (datetime.now() - start).total_seconds()
        print(f"[AlphaMetrics] 완료 — success={success}/{tickers_total} "
              f"fscore={fscore_filled} mscore={mscore_filled} "
              f"fcf={fcf_filled} ({duration:.1f}s)")

        return {
            "tickers": tickers_total,
            "success": success,
            "fscore_filled": fscore_filled,
            "mscore_filled": mscore_filled,
            "fcf_filled": fcf_filled,
            "duration_sec": round(duration, 1),
            "end_period": end_period,
            "trade_date": trade_date,
            "errors": errors,
        }
    finally:
        conn.close()


