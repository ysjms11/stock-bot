"""헬퍼 함수: 티커 판별, 거래소 추정, 시장 시간, 감성분석 데이터."""
import os
import re
from datetime import datetime

from ._config import ET, _DATA_DIR

# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 티커 판별 & 거래소 추정
# ━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_us_ticker(ticker: str) -> bool:
    """영문 티커면 미국 종목으로 판별 (숫자 포함 없으면 US)"""
    return bool(ticker) and ticker.replace(".", "").replace("-", "").isalpha()


# NYSE 대표 종목 (나머지는 NASDAQ 기본)
_NYSE_TICKERS = {
    "BRK.A", "BRK.B", "JNJ", "V", "WMT", "PG", "MA", "HD", "DIS", "BA",
    "KO", "PFE", "MRK", "VZ", "T", "NKE", "MMM", "CAT", "GS", "JPM",
    "BAC", "C", "WFC", "UNH", "CVX", "XOM", "CRM", "ORCL", "IBM", "GE",
    "LMT", "RTX", "NOC", "PM", "MCD", "UPS", "FDX", "GM", "F",
    # 추가 NYSE 종목 (2026-04-05)
    "VRT", "ETN", "GLW", "MOD", "BWXT", "NVT", "STVN", "XYL",
    "HWM", "TDG", "GEV", "VST", "CEG", "CARR", "EMR", "ROK",
}
_AMEX_TICKERS = {
    "LEU", "HYMC", "BTG", "NGD", "USAS", "SAND",
}


def _guess_excd(symbol: str) -> str:
    """미국 종목 거래소코드 추정 (NYS/NAS/AMS)"""
    s = symbol.upper()
    if s in _NYSE_TICKERS:
        return "NYS"
    if s in _AMEX_TICKERS:
        return "AMS"
    return "NAS"


def _is_us_market_hours_kst() -> bool:
    """미국 장 시간 여부 (ET 09:30~16:00, DST 자동 감지)"""
    now_et = datetime.now(ET)
    wd = now_et.weekday()
    if wd >= 5:
        return False  # 토/일 ET → 미국 장 없음
    h, m = now_et.hour, now_et.minute
    if h < 9 or (h == 9 and m < 30):
        return False  # ET 09:30 이전
    if h >= 16:
        return False  # ET 16:00 이후
    return True


def _is_us_market_closed() -> bool:
    """미국 정규장 마감 후 30분 이내 여부 (DST 자동 감지)

    DST(UTC-4) 시: KST 05:00~05:30
    표준시(UTC-5) 시: KST 06:00~06:30
    """
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return False  # 토/일 ET → 미국 장 없음
    close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    diff_sec = (now_et - close_et).total_seconds()
    return 0 <= diff_sec <= 1800  # 마감 후 0~30분 이내


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DART 키워드 & 감성분석
# ━━━━━━━━━━━━━━━━━━━━━━━━━

# DART 중요 공시 키워드
DART_KEYWORDS = [
    "수주", "계약", "공급계약", "납품", "유상증자", "무상증자",
    "전환사채", "신주인수권", "자기주식", "배당", "합병",
    "분할", "영업양수", "영업양도", "소송", "상장폐지",
    "실적", "매출", "영업이익", "감자", "대규모",
]

# KNU 감성사전 메모리 캐시
_KNU_SENTI_CACHE: dict | None = None


def _load_knu_senti_lex() -> dict:
    """KNU 한국어 감성사전 로드 (최초 1회만 파일 읽기, 이후 메모리 캐싱)."""
    global _KNU_SENTI_CACHE
    if _KNU_SENTI_CACHE is not None:
        return _KNU_SENTI_CACHE
    path = os.path.join(_DATA_DIR, "knu_senti_lex.json")
    try:
        with open(path, encoding="utf-8") as f:
            import json
            _KNU_SENTI_CACHE = json.load(f)
    except Exception:
        _KNU_SENTI_CACHE = {}
    return _KNU_SENTI_CACHE


# 금융 특화 다단어 구문 점수 (KNU 개별 단어보다 우선 적용, 절댓값 클수록 강함)
# 양수=긍정, 음수=부정. 문자열 포함 여부로 매칭 (긴 구문 먼저 검사)
_FINANCE_PHRASE_SCORES: list[tuple[str, int]] = sorted([
    # 컨텍스트 반전: 감소/축소가 긍정인 경우
    ("대차잔고 감소", 4), ("대차잔고감소", 4),
    ("대차거래 잔고감소", 4), ("대차거래잔고감소", 4),
    ("공매도잔고 감소", 4), ("공매도잔고감소", 4),
    ("공매도 감소", 3), ("공매도감소", 3),
    ("공매도 축소", 3), ("공매도축소", 3),
    ("부채비율 감소", 2), ("부채비율감소", 2),
    ("적자 감소", 2), ("적자감소", 2),
    ("적자 축소", 2), ("적자축소", 2),
    # 컨텍스트 반전: 증가가 부정인 경우
    ("대차잔고 증가", -4), ("대차잔고증가", -4),
    ("공매도 증가", -3), ("공매도증가", -3),
    ("부채비율 증가", -2), ("부채비율증가", -2),
    # 강력 긍정
    ("흑자전환", 5), ("어닝서프라이즈", 5), ("어닝 서프라이즈", 5),
    ("깜짝실적", 4), ("깜짝 실적", 4), ("사상 최대", 4), ("사상최대", 4),
    ("최대 실적", 4), ("최대실적", 4), ("역대 최대", 4), ("역대최대", 4),
    ("목표가 상향", 4), ("목표가상향", 4),
    ("투자의견 상향", 4), ("투자의견상향", 4),
    ("통 큰 배당", 4), ("특별배당", 4),
    ("자사주 매입", 3), ("자사주매입", 3),
    ("배당 증가", 3), ("배당증가", 3), ("배당 확대", 3), ("배당확대", 3),
    ("영업이익 증가", 3), ("영업이익증가", 3),
    ("매출 증가", 2), ("매출증가", 2),
    ("실적 개선", 3), ("실적개선", 3),
    ("수주 확대", 3), ("수주확대", 3),
    ("계약 체결", 2), ("계약체결", 2),
    ("공급 계약", 2), ("공급계약", 2),
    ("수출 계약", 2), ("수출계약", 2),
    ("계약 성사", 3), ("계약성사", 3),
    ("허가 획득", 4), ("허가획득", 4),
    ("FDA 허가", 4), ("임상 허가", 3),
    ("기술 돌파", 3), ("돌파구", 3),
    ("수주잔고", 2),
    ("독점 공급", 3), ("독점공급", 3), ("독점 계약", 3),
    ("영업이익 흑자", 3),
    ("지지선", 2), ("저항선", 0),
    ("신고가 달성", 3), ("52주 신고가", 3), ("연고점 돌파", 3),
    ("수출 증가", 2), ("수출증가", 2),
    ("매수세 집중", 2), ("매수세 유입", 2),
    ("순매수 지속", 2), ("외인 순매수", 2),
    ("연속 상승", 2), ("연속 매수", 2),
    ("승소", 3),
    ("구조조정 효과", 2),
    ("부담 완화", 2), ("리스크 완화", 2),
    ("효과", 1),
    # KNU 오매칭 방지 — 양성 복합어 커버
    ("상한가", 3),
    ("흑자", 2),
    ("고성장", 2),
    ("판매 증가", 2), ("판매증가", 2),
    ("판매 급증", 3), ("판매급증", 3),
    ("수요 증가", 2), ("수요증가", 2),
    # KNU 오매칭 방지 — 음성 복합어 커버
    ("흥행 부진", -3), ("흥행부진", -3),
    ("배당 감소", -3), ("배당감소", -3),
    ("악화", -2),
    ("침체", -2),
    ("무산", -3),
    ("규제 리스크", -3),
    ("원가 상승", -2), ("원가상승", -2),
    ("대손비용 증가", -3),
    ("부실", -2),
    # 강력 부정 — 맥락 반전
    ("허가 반려", -5), ("임상 실패", -4), ("허가 취소", -4),
    ("수익성 악화", -3), ("수익 악화", -3),
    ("수익성 압박", -3), ("수익성압박", -3),
    ("영업적자", -4),
    ("가치 하락", -3),
    ("손실 확대", -3), ("손실확대", -3),
    ("연체율 상승", -3), ("부실 확대", -3),
    ("약세장", -3), ("연저점", -3),
    ("급락", -3), ("급감", -3),
    ("매도 폭탄", -4),
    ("공매도잔고 급증", -4),
    ("재고손실", -3),
    ("수출 감소", -2), ("수출감소", -2),
    ("실적 악화", -3), ("실적악화", -3),
    ("실적 쇼크", -5), ("실적쇼크", -5),
    ("수주 취소", -3), ("계약 취소", -3),
    ("유상증자", -3),
    # 강력 부정
    ("리스크 부각", -3), ("리스크 확대", -3),
    ("수급 악화", -2), ("수급악화", -2),
    ("적자전환", -5), ("어닝쇼크", -5), ("어닝 쇼크", -5),
    ("상장폐지", -5), ("상폐", -4),
    ("목표가 하향", -4), ("목표가하향", -4),
    ("투자의견 하향", -4), ("투자의견하향", -4),
    ("영업이익 급감", -4), ("영업이익급감", -4),
    ("이익 급감", -3), ("이익급감", -3),
    ("영업이익 감소", -3), ("영업이익감소", -3),
    ("부채비율 급증", -3),
    ("적자 확대", -4), ("적자확대", -4),
    ("매출 감소", -2), ("매출감소", -2),
    ("이익 감소", -2), ("이익감소", -2),
    ("구조조정", -3), ("감자", -4),
    # 긍정 추가 (coverage 보강)
    ("양호", 2),
    ("매출 성장", 3), ("매출성장", 3),
    ("수주 급증", 4), ("수주급증", 4),
], key=lambda x: -len(x[0]))  # 긴 구문 먼저 매칭

# 기계적 순위 기사 필터 패턴 (해당하면 neutral 즉시 반환)
_RANKING_PATTERNS = [
    r"순매수\s*상위",
    r"순매도\s*상위",
    r"체결강도\s*상위",
    r"등락률\s*상위",
    r"거래량\s*상위",
    r"상위\s*\d+\s*종목",
    r"상위\s*종목",
    r"시총\s*상위",
    r"배당수익률\s*상위",
    r"\d+종목\s*(집계|포함|선정)",
    r"상위에\s*(오른|든)\s*종목",
    r"상한가\s*종목",
    r"하한가\s*종목",
    r"종목\s*\d+\s*개",
]
_RANKING_RE = re.compile("|".join(_RANKING_PATTERNS))

# 미국 뉴스 영문 감성 키워드 사전
_US_POSITIVE_KEYWORDS = [
    "surge", "soar", "rally", "beat", "upgrade", "bullish", "growth",
    "record", "outperform", "buy", "strong", "raise", "profit", "gain",
    "upside", "breakout", "momentum", "dividend", "expand", "turnaround",
    "surprise", "exceeded", "record high", "beat estimates", "raised guidance",
]
_US_NEGATIVE_KEYWORDS = [
    "drop", "plunge", "crash", "miss", "downgrade", "bearish", "decline",
    "loss", "underperform", "sell", "weak", "cut", "warning", "risk",
    "layoff", "recall", "lawsuit", "investigation", "bankruptcy",
    "missed estimates", "lowered guidance", "earnings shock",
]
