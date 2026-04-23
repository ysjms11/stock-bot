"""관세청 data.go.kr 수출입 데이터 래퍼 + 시계열 계산.

개인 투자 선행지표 용도.
- Itemtrade:       HS 품목 전체 (국가 합산)
- nitemtrade:      HS × 국가 조합
- nationtrade:     국가 전체 (품목 합산)

응답은 XML. 간헐적으로 HTML/비정상 응답이 섞여서 3회 지수백오프 재시도.
"""

from __future__ import annotations

import os
import time
import sqlite3
import xml.etree.ElementTree as ET
import urllib.parse
from datetime import datetime
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 환경변수
# ━━━━━━━━━━━━━━━━━━━━━━━━━
_DATAGO_API_KEY = os.environ.get("DATAGO_API_KEY", "").strip()

_BASE_URL = "https://apis.data.go.kr/1220000/"

_ENDPOINTS = {
    "item":         "Itemtrade/getItemtradeList",
    "item_country": "nitemtrade/getNitemtradeList",
    "country":      "nationtrade/getNationtradeList",
}

_MAX_RETRIES = 3
_RATE_SLEEP = 0.3  # 호출 간 0.3초 sleep (초당 3건 이내)


def _require_key() -> str:
    if not _DATAGO_API_KEY:
        raise RuntimeError("DATAGO_API_KEY 환경변수가 설정되지 않음 (.env 확인)")
    return _DATAGO_API_KEY


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# HTTP 호출 + 재시도
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _fetch_xml(endpoint_key: str, params: dict) -> Optional[ET.Element]:
    """data.go.kr XML API 호출 + 3회 지수백오프 재시도.

    성공: ET.Element (root) 반환
    3회 실패 또는 XML 파싱 불가: None 반환
    """
    key = _require_key()
    url = _BASE_URL + _ENDPOINTS[endpoint_key]

    # serviceKey 는 이미 인코딩된 상태일 수 있음 → requests 가 이중 인코딩하지 않도록
    # 쿼리스트링으로 직접 조립
    all_params = {"serviceKey": key, **params}
    qs = "&".join(
        f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in all_params.items()
    )
    full_url = f"{url}?{qs}"

    last_err: Optional[str] = None
    for attempt in range(_MAX_RETRIES):
        try:
            r = requests.get(full_url, timeout=30)
            text = r.text or ""
            # 간혹 HTML 에러 페이지를 200으로 반환 — <OpenAPI_ServiceResponse> / <response> 확인
            if not text.lstrip().startswith("<"):
                raise ValueError(f"non-XML body: {text[:120]}")
            root = ET.fromstring(text)
            # 에러 래퍼 (<OpenAPI_ServiceResponse><cmmMsgHeader>...) 확인
            if root.tag.endswith("OpenAPI_ServiceResponse"):
                msg = root.findtext(".//returnReasonCode") or ""
                auth = root.findtext(".//returnAuthMsg") or ""
                raise ValueError(f"service error: {auth} ({msg})")
            return root
        except (requests.RequestException, ET.ParseError, ValueError) as e:
            last_err = str(e)
            if attempt < _MAX_RETRIES - 1:
                backoff = 2 ** attempt  # 1s, 2s, 4s
                time.sleep(backoff)
                continue
            break

    print(f"[trade_api] {endpoint_key} {params} failed 3x ({last_err})")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _yymm_from_year(year_str: str) -> str:
    """'2026.02' → '202602'. 이상값이면 원본 유지."""
    if not year_str:
        return ""
    return year_str.replace(".", "").strip()


def _safe_float(x: Optional[str]) -> Optional[float]:
    if x is None:
        return None
    s = str(x).strip().replace(",", "")
    if not s or s in ("-", "N/A"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_items(root: ET.Element) -> list[dict]:
    """`<item>` 엘리먼트 배열을 정규화된 dict 리스트로 변환.

    스킵 규칙:
    - year == '총계' (집계 소계, DB 중복 유발)
    - hsCd == '-' (총계 rows)
    """
    out: list[dict] = []
    for it in root.findall(".//item"):
        year = (it.findtext("year") or "").strip()
        hs_cd = (it.findtext("hsCd") or "").strip()

        if year == "총계" or hs_cd == "-":
            continue

        row = {
            "yymm":         _yymm_from_year(year),
            "hs_code":      hs_cd,
            "hs_name":      (it.findtext("statKor") or "").strip(),
            "country_cd":   (it.findtext("statCd") or "").strip(),
            "country_name": (it.findtext("statCdCntnKor1") or "").strip(),
            "exp_usd":      _safe_float(it.findtext("expDlr")),
            "imp_usd":      _safe_float(it.findtext("impDlr")),
            "exp_wgt_kg":   _safe_float(it.findtext("expWgt")),
            "imp_wgt_kg":   _safe_float(it.findtext("impWgt")),
            "balance_usd":  _safe_float(it.findtext("balPayments")),
        }
        out.append(row)
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 3개 엔드포인트 래퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def trade_item(
    hs_sgn: str,
    strt_yymm: str,
    end_yymm: str,
    num_rows: int = 100,
) -> list[dict]:
    """Itemtrade/getItemtradeList — HS 품목 전체 (국가 합산).

    hs_sgn : HS 2/4/6/10자리
    strt_yymm / end_yymm : YYYYMM
    """
    params = {
        "strtYymm": strt_yymm,
        "endYymm":  end_yymm,
        "hsSgn":    hs_sgn,
        "numOfRows": num_rows,
        "pageNo":   1,
    }
    root = _fetch_xml("item", params)
    time.sleep(_RATE_SLEEP)
    if root is None:
        return []
    return _parse_items(root)


def trade_item_country(
    hs_sgn: str,
    strt_yymm: str,
    end_yymm: str,
    num_rows: int = 300,
) -> list[dict]:
    """nitemtrade/getNitemtradeList — HS × 국가."""
    params = {
        "strtYymm": strt_yymm,
        "endYymm":  end_yymm,
        "hsSgn":    hs_sgn,
        "numOfRows": num_rows,
        "pageNo":   1,
    }
    root = _fetch_xml("item_country", params)
    time.sleep(_RATE_SLEEP)
    if root is None:
        return []
    return _parse_items(root)


def trade_country(
    country_cd: str,
    strt_yymm: str,
    end_yymm: str,
    num_rows: int = 100,
) -> list[dict]:
    """nationtrade/getNationtradeList — 국가 전체 (품목 합산)."""
    params = {
        "strtYymm":  strt_yymm,
        "endYymm":   end_yymm,
        "statCd":    country_cd,
        "numOfRows": num_rows,
        "pageNo":    1,
    }
    root = _fetch_xml("country", params)
    time.sleep(_RATE_SLEEP)
    if root is None:
        return []
    return _parse_items(root)


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# DB UPSERT
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _open_db() -> sqlite3.Connection:
    """db_collector._get_db() 재사용. circular import 방지 위해 늦은 import."""
    try:
        from db_collector import _get_db
        return _get_db()
    except Exception:
        # 테스트/fallback — 직접 연결
        data_dir = os.environ.get("DATA_DIR", "/data")
        db_path = f"{data_dir}/stock.db"
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn


def save_trade_monthly(rows: list[dict], source: str = "datago") -> int:
    """trade_monthly 에 INSERT OR REPLACE. 반환: 저장된 row 수.

    row 필수 키: yymm, hs_code. country_cd 는 없으면 '' 로 처리.
    hs_level = len(hs_code) (2/4/6/10)
    """
    if not rows:
        return 0

    fetched_at = datetime.now().isoformat()
    conn = _open_db()
    try:
        cur = conn.cursor()
        sql = """
            INSERT OR REPLACE INTO trade_monthly
            (yymm, hs_code, hs_level, country_cd, country_name, hs_name,
             exp_usd, imp_usd, exp_wgt_kg, imp_wgt_kg, balance_usd,
             source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        saved = 0
        for r in rows:
            yymm = r.get("yymm", "")
            hs_code = r.get("hs_code", "")
            if not yymm or not hs_code:
                continue
            hs_level = len(hs_code)
            cur.execute(sql, (
                yymm,
                hs_code,
                hs_level,
                r.get("country_cd", "") or "",
                r.get("country_name", "") or "",
                r.get("hs_name", "") or "",
                r.get("exp_usd"),
                r.get("imp_usd"),
                r.get("exp_wgt_kg"),
                r.get("imp_wgt_kg"),
                r.get("balance_usd"),
                source,
                fetched_at,
            ))
            saved += 1
        conn.commit()
        return saved
    finally:
        conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━
# 시계열 계산
# ━━━━━━━━━━━━━━━━━━━━━━━━━
def _pct(curr: Optional[float], base: Optional[float]) -> Optional[float]:
    """(curr - base) / base * 100. 0/None 방어."""
    if curr is None or base is None:
        return None
    try:
        if base == 0:
            return None
        return (curr - base) / base * 100.0
    except (TypeError, ZeroDivisionError):
        return None


def compute_yoy_mom_qoq(series: list[dict], value_key: str = "exp_usd") -> dict:
    """월별 오름차순 series 에서 YoY / MoM / QoQ 계산.

    series: [{"yymm": "202502", "exp_usd": ...}, ...]
    반환:
        {"yoy_pct": 15.7, "mom_pct": 8.1, "qoq_pct": 3.4,
         "current": ..., "peak_12m": ...}
    데이터 부족 시 개별 값 None.
    """
    # yymm 오름차순 정렬
    s = sorted(
        [x for x in series if x.get(value_key) is not None and x.get("yymm")],
        key=lambda x: x["yymm"],
    )
    out = {
        "yoy_pct": None,
        "mom_pct": None,
        "qoq_pct": None,
        "current": None,
        "peak_12m": None,
    }
    if not s:
        return out

    current = s[-1].get(value_key)
    out["current"] = current

    # MoM: 직전 월
    if len(s) >= 2:
        out["mom_pct"] = _pct(current, s[-2].get(value_key))

    # QoQ: 3개월 전
    if len(s) >= 4:
        out["qoq_pct"] = _pct(current, s[-4].get(value_key))

    # YoY: 12개월 전
    if len(s) >= 13:
        out["yoy_pct"] = _pct(current, s[-13].get(value_key))

    # 최근 12개월 최대
    last12 = [x.get(value_key) for x in s[-12:] if x.get(value_key) is not None]
    if last12:
        out["peak_12m"] = max(last12)

    return out


def compute_peakout_signal(series: list[dict], value_key: str = "exp_usd") -> dict:
    """피크아웃 3지표 분류.

    1. YoY 증가율 2차 차분 < 0 (가속→감속)
    2. 3개월 이동평균 기울기 꺾임 (최근 3M MA < 직전 3M MA)
    3. ASP (exp_usd / exp_wgt_kg) 감소 + 물량 증가 디버전스 (wgt 있을 때만)

    반환: {"peakout": bool, "score": 0~3, "reasons": [...]}
    """
    reasons: list[str] = []
    score = 0

    s = sorted(
        [x for x in series if x.get(value_key) is not None and x.get("yymm")],
        key=lambda x: x["yymm"],
    )

    # ── 1. YoY 2차 차분 ──
    # 최소 14개월 필요 (현재/12M전 + 직전월/13M전)
    if len(s) >= 14:
        yoy_curr = _pct(s[-1].get(value_key), s[-13].get(value_key))
        yoy_prev = _pct(s[-2].get(value_key), s[-14].get(value_key))
        if yoy_curr is not None and yoy_prev is not None:
            diff2 = yoy_curr - yoy_prev
            if diff2 < 0:
                score += 1
                reasons.append(f"YoY 감속 ({yoy_prev:.1f}% → {yoy_curr:.1f}%)")

    # ── 2. 3M MA 기울기 꺾임 ──
    # 최소 6개월 필요 (최근 3M MA vs 직전 3M MA)
    def _mean(xs: list[float]) -> Optional[float]:
        xs = [v for v in xs if v is not None]
        return sum(xs) / len(xs) if xs else None

    if len(s) >= 6:
        recent3 = _mean([x.get(value_key) for x in s[-3:]])
        prev3 = _mean([x.get(value_key) for x in s[-6:-3]])
        if recent3 is not None and prev3 is not None and recent3 < prev3:
            score += 1
            reasons.append(f"3M MA 하향 ({prev3:,.0f} → {recent3:,.0f})")

    # ── 3. ASP 감소 + 물량 증가 디버전스 ──
    # exp_usd 모드일 때만 의미 있음 (wgt 기반 ASP)
    if value_key == "exp_usd" and len(s) >= 2:
        curr = s[-1]
        prev = s[-2]
        asp_curr = compute_asp(curr)
        asp_prev = compute_asp(prev)
        wgt_curr = curr.get("exp_wgt_kg")
        wgt_prev = prev.get("exp_wgt_kg")
        if (
            asp_curr is not None and asp_prev is not None
            and wgt_curr is not None and wgt_prev is not None
            and asp_curr < asp_prev and wgt_curr > wgt_prev
        ):
            score += 1
            reasons.append(
                f"ASP↓ 물량↑ 디버전스 ({asp_prev:.2f} → {asp_curr:.2f}$/kg)"
            )

    return {
        "peakout": score >= 2,
        "score": score,
        "reasons": reasons,
    }


def compute_asp(row: dict) -> Optional[float]:
    """판가(ASP) 역산 = exp_usd / exp_wgt_kg. 중량 0/None 이면 None."""
    if not row:
        return None
    usd = row.get("exp_usd")
    wgt = row.get("exp_wgt_kg")
    if usd is None or wgt is None:
        return None
    try:
        if wgt == 0:
            return None
        return float(usd) / float(wgt)
    except (TypeError, ZeroDivisionError):
        return None
