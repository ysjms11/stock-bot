"""웹 대시보드 (HTTP HTML 렌더링) — main.py 에서 분리 (5/5 리팩토링).

- 라우트는 register_routes(app) 로 외부 노출.
- main.py 의 함수를 import 하지 않음 (단방향 의존: main → dashboard).
- 내부 함수는 모두 _ 접두사 유지 (외부 호출자 없음 = private).
- shadow trap 방지 (학습 #25): from kis_api import * 금지, 명시 import만.
"""

import os
import json
import re
import hashlib
import html as _html
import asyncio
from datetime import datetime, timedelta
from aiohttp import web

from kis_api import (
    _DATA_DIR, KST,
    load_json, save_json,
    load_watchalert, load_stoploss,
    PORTFOLIO_FILE, WATCHALERT_FILE,
    ws_manager, get_kis_token,
    kis_stock_price, kis_us_stock_price,
    get_yahoo_quote,
    _is_us_ticker,
)
from krx_crawler import load_krx_db

try:
    from report_crawler import DB_PATH as REPORT_DB_PATH
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False
    REPORT_DB_PATH = os.path.join(os.environ.get("DATA_DIR", "data"), "stock.db")

# telegram_bot._GRADE_ORDER 와 동일 (단방향 규칙: dashboard는 main_pkg import 금지)
_GRADE_ORDER = {"A": 0, "B+": 1, "B": 2, "B-": 3, "C+": 4, "C": 5, "D": 6, "": 7}

# 웹 대시보드 (/dash)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_DASH_CSS = """
<style>
:root{--bg:#1a1a2e;--bg2:#16213e;--fg:#e0e0e0;--fg2:#a0a0b0;--accent:#4fc3f7;--red:#ef5350;--green:#66bb6a;--border:#2a2a4a}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,sans-serif;padding:16px;max-width:860px;margin:0 auto;line-height:1.6}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:1.6em;margin:16px 0 8px}h2{font-size:1.3em;margin:24px 0 8px;border-bottom:1px solid var(--border);padding-bottom:4px}
h3{font-size:1.1em;margin:16px 0 4px;color:var(--accent)}
p{margin:8px 0}
code{background:var(--bg2);padding:2px 6px;border-radius:3px;font-family:'SF Mono',monospace;font-size:0.9em}
pre{background:var(--bg2);padding:12px;border-radius:6px;overflow-x:auto;margin:8px 0;font-size:0.85em;border:1px solid var(--border)}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:0.9em}
th{background:var(--bg2);padding:8px;text-align:left;border:1px solid var(--border);font-weight:600}
td{padding:6px 8px;border:1px solid var(--border)}
tr:nth-child(even){background:rgba(255,255,255,0.03)}
ul,ol{margin:8px 0 8px 20px}li{margin:2px 0}
.check{display:flex;align-items:center;gap:6px;margin:2px 0}
.check input{width:16px;height:16px;accent-color:var(--accent)}
.section{margin:24px 0;padding:16px;background:var(--bg2);border-radius:8px;border:1px solid var(--border)}
.nav{display:flex;gap:12px;padding:8px 0;border-bottom:1px solid var(--border);margin-bottom:16px;flex-wrap:wrap}
.nav a{padding:4px 10px;border-radius:4px;background:var(--bg2);font-size:0.9em}
.pos{color:var(--green)}.neg{color:var(--red)}
@media(max-width:600px){body{padding:8px}table{font-size:0.8em}th,td{padding:4px}}
</style>
"""


def _md_to_html(md: str) -> str:
    """Markdown → HTML (정규식 기반 경량 변환)."""
    lines = md.split("\n")
    html_lines = []
    in_code = False
    in_table = False
    in_list = False

    for line in lines:
        # code block
        if line.strip().startswith("```"):
            if in_code:
                html_lines.append("</code></pre>")
                in_code = False
            else:
                lang = line.strip()[3:].strip()
                html_lines.append(f"<pre><code>")
                in_code = True
            continue
        if in_code:
            html_lines.append(line.replace("<", "&lt;").replace(">", "&gt;"))
            continue

        stripped = line.strip()

        # close table
        if in_table and not stripped.startswith("|"):
            html_lines.append("</tbody></table>")
            in_table = False

        # close list
        if in_list and not stripped.startswith("- ") and not stripped.startswith("* ") and stripped:
            html_lines.append("</ul>")
            in_list = False

        # empty line
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("")
            continue

        # headers
        if stripped.startswith("### "):
            html_lines.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif stripped.startswith("> "):
            html_lines.append(f"<blockquote style='border-left:3px solid var(--accent);padding-left:12px;color:var(--fg2)'>{_inline(stripped[2:])}</blockquote>")
        # checkbox
        elif stripped.startswith("- [x] ") or stripped.startswith("- [X] "):
            html_lines.append(f"<div class='check'><input type='checkbox' checked disabled><span style='text-decoration:line-through;color:var(--fg2)'>{_inline(stripped[6:])}</span></div>")
        elif stripped.startswith("- [ ] "):
            html_lines.append(f"<div class='check'><input type='checkbox' disabled><span>{_inline(stripped[6:])}</span></div>")
        # table
        elif stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue  # separator row
            if not in_table:
                html_lines.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                html_lines.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
        # list
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline(stripped[2:])}</li>")
        # hr
        elif stripped.startswith("---"):
            html_lines.append("<hr style='border-color:var(--border);margin:16px 0'>")
        else:
            html_lines.append(f"<p>{_inline(stripped)}</p>")

    if in_code:
        html_lines.append("</code></pre>")
    if in_table:
        html_lines.append("</tbody></table>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def _md_to_html_editable(md: str, file_key: str) -> str:
    """Markdown → HTML (체크박스 클릭 가능 버전, data-* 속성 추가).

    file_key: "dev" | "invest" | "todo" — POST /dash/todo/toggle 에서 파일 식별용.
    각 체크박스 라인에 data-todo-file/line/hash 속성 부여.
    라인 번호는 원본 md 의 1-indexed (요청 시 그대로 수정).
    """
    lines = md.split("\n")
    html_lines = []
    in_code = False
    in_table = False
    in_list = False

    for idx, line in enumerate(lines):
        line_num = idx + 1  # 1-indexed

        # code block
        if line.strip().startswith("```"):
            if in_code:
                html_lines.append("</code></pre>")
                in_code = False
            else:
                lang = line.strip()[3:].strip()
                html_lines.append(f"<pre><code>")
                in_code = True
            continue
        if in_code:
            html_lines.append(line.replace("<", "&lt;").replace(">", "&gt;"))
            continue

        stripped = line.strip()

        # close table
        if in_table and not stripped.startswith("|"):
            html_lines.append("</tbody></table>")
            in_table = False

        # close list
        if in_list and not stripped.startswith("- ") and not stripped.startswith("* ") and stripped:
            html_lines.append("</ul>")
            in_list = False

        # empty line
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("")
            continue

        # headers
        if stripped.startswith("### "):
            html_lines.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif stripped.startswith("> "):
            html_lines.append(f"<blockquote style='border-left:3px solid var(--accent);padding-left:12px;color:var(--fg2)'>{_inline(stripped[2:])}</blockquote>")
        # checkbox (editable)
        elif stripped.startswith("- [x] ") or stripped.startswith("- [X] "):
            line_hash = hashlib.sha1(line.encode("utf-8")).hexdigest()[:12]
            html_lines.append(
                f"<div class='check'>"
                f"<input type='checkbox' checked "
                f"data-todo-file='{_html.escape(file_key)}' "
                f"data-todo-line='{line_num}' "
                f"data-todo-hash='{line_hash}'>"
                f"<span style='text-decoration:line-through;color:var(--fg2)'>{_inline(stripped[6:])}</span>"
                f"</div>"
            )
        elif stripped.startswith("- [ ] "):
            line_hash = hashlib.sha1(line.encode("utf-8")).hexdigest()[:12]
            html_lines.append(
                f"<div class='check'>"
                f"<input type='checkbox' "
                f"data-todo-file='{_html.escape(file_key)}' "
                f"data-todo-line='{line_num}' "
                f"data-todo-hash='{line_hash}'>"
                f"<span>{_inline(stripped[6:])}</span>"
                f"</div>"
            )
        # table
        elif stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue  # separator row
            if not in_table:
                html_lines.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                html_lines.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
        # list
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline(stripped[2:])}</li>")
        # hr
        elif stripped.startswith("---"):
            html_lines.append("<hr style='border-color:var(--border);margin:16px 0'>")
        else:
            html_lines.append(f"<p>{_inline(stripped)}</p>")

    if in_code:
        html_lines.append("</code></pre>")
    if in_table:
        html_lines.append("</tbody></table>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def _atomic_write(filepath: str, content: str) -> None:
    """파일 쓰기 전 임시 파일에 쓰고 os.replace 로 교체 (전원 나가도 안전)."""
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, filepath)


# 편집 대상 TODO 파일 화이트리스트 (경로 조작 방어)
_TODO_FILE_MAP = {
    "dev": "TODO_dev.md",
    "invest": "TODO_invest.md",
    "todo": "TODO.md",
}


_SAFE_URL_SCHEMES = ("http://", "https://", "/", "#", "mailto:")


def _sanitize_url(url: str) -> str:
    """href URL 화이트리스트 — javascript:/data: 등 XSS 벡터 차단 + 속성 탈출 방지.

    허용: http://, https://, 절대경로(/), 앵커(#), mailto:
    그 외 (javascript:, data:, vbscript: 등) → "#" 으로 치환.
    쌍따옴표 이스케이프로 href="" 속성 탈출 방어.
    """
    u = url.strip()
    u_lower = u.lower()
    if not any(u_lower.startswith(s) for s in _SAFE_URL_SCHEMES):
        return "#"
    # href 속성값 탈출 방지
    return u.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(text: str) -> str:
    """인라인 마크다운 (bold, code, link)."""
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # 링크: URL 은 _sanitize_url 로 스킴 화이트리스트 + 속성 이스케이프
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        lambda m: f'<a href="{_sanitize_url(m.group(2))}">{m.group(1)}</a>',
        text,
    )
    return text


def _json_to_table(data, title: str = "") -> str:
    """JSON 데이터를 HTML 테이블로."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        keys = list(data[0].keys())
        rows = "".join(
            "<tr>" + "".join(f"<td>{_html.escape(str(r.get(k, '')))}</td>" for k in keys) + "</tr>"
            for r in data[:50]
        )
        header = "".join(f"<th>{_html.escape(str(k))}</th>" for k in keys)
        return f"<table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>"
    elif isinstance(data, dict):
        rows = "".join(
            f"<tr><td><strong>{_html.escape(str(k))}</strong></td><td>{_format_val(v)}</td></tr>"
            for k, v in list(data.items())[:100]
        )
        return f"<table>{rows}</table>"
    return f"<pre>{_html.escape(json.dumps(data, ensure_ascii=False, indent=2)[:5000])}</pre>"


def _format_val(v):
    if isinstance(v, dict):
        return "<code>" + _html.escape(json.dumps(v, ensure_ascii=False)[:200]) + "</code>"
    if isinstance(v, list):
        return f"[{len(v)} items]"
    if isinstance(v, (int, float)) and abs(v) >= 10000:
        return f"{v:,.0f}"
    return _html.escape(str(v))


def _build_portfolio_html() -> str:
    """portfolio.json + KRX DB 현재가 → 포트폴리오 테이블."""
    pf = load_json(PORTFOLIO_FILE, {})
    kr = {k: v for k, v in pf.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)}
    us = pf.get("us_stocks", {})

    # KRX DB에서 현재가
    db = load_krx_db()
    db_stocks = db.get("stocks", {}) if db else {}
    db_date = db.get("date", "") if db else ""

    html = ""
    kr_total_cost = kr_total_eval = 0
    if kr:
        html += f"<h3>🇰🇷 한국</h3><table><thead><tr><th>종목</th><th>수량</th><th>평단가</th><th>현재가</th><th>손익</th></tr></thead><tbody>"
        for t, v in kr.items():
            qty = int(v.get("qty", 0))
            avg = int(v.get("avg_price", 0))
            cur = db_stocks.get(t, {}).get("close", 0)
            cost = qty * avg
            ev = qty * cur if cur else 0
            kr_total_cost += cost
            kr_total_eval += ev
            if cur and avg:
                pnl_pct = (cur - avg) / avg * 100
                cls = "pos" if pnl_pct >= 0 else "neg"
                pnl_str = f"<span class='{cls}'>{pnl_pct:+.1f}%</span>"
                cur_str = f"{cur:,}원"
            else:
                pnl_str = "-"
                cur_str = "-"
            html += f"<tr><td>{_html.escape(v.get('name', t))}</td><td>{qty:,}</td><td>{avg:,}원</td><td>{cur_str}</td><td>{pnl_str}</td></tr>"
        html += "</tbody></table>"
        if kr_total_cost > 0:
            kr_pnl = (kr_total_eval - kr_total_cost) / kr_total_cost * 100
            cls = "pos" if kr_pnl >= 0 else "neg"
            html += f"<p>KR 합계: 평가 {kr_total_eval:,.0f}원 / 매입 {kr_total_cost:,.0f}원 = <span class='{cls}'>{kr_pnl:+.1f}%</span></p>"

    if us:
        html += "<h3>🇺🇸 미국</h3><table><thead><tr><th>종목</th><th>수량</th><th>평단가</th></tr></thead><tbody>"
        for t, v in us.items():
            html += f"<tr><td>{_html.escape(v.get('name', t))} ({_html.escape(t)})</td><td>{int(v.get('qty', 0)):,}</td><td>${float(v.get('avg_price', 0)):,.2f}</td></tr>"
        html += "</tbody></table>"

    cash_k = float(pf.get("cash_krw", 0) or 0)
    cash_u = float(pf.get("cash_usd", 0) or 0)
    if cash_k or cash_u:
        html += f"<p>💰 현금: {cash_k:,.0f}원 / ${cash_u:,.2f}</p>"
    if db_date:
        html += f"<p style='color:var(--fg2);font-size:0.85em'>현재가 기준: {db_date}</p>"
    return html or "<p>포트폴리오 비어있음</p>"


async def _build_portfolio_v2_html() -> str:
    """portfolio.json + KRX DB(KR) + KIS API(US) 현재가 → 증권사 앱 스타일 포트폴리오 (v2 전용)."""
    pf = load_json(PORTFOLIO_FILE, {})
    kr = {k: v for k, v in pf.items() if k not in ("us_stocks", "cash_krw", "cash_usd") and isinstance(v, dict)}
    us = pf.get("us_stocks", {})

    # ── KR 현재가: WebSocket 캐시 우선 → REST fallback ──
    kr_prices: dict[str, int] = {}
    rest_needed = []
    for t in kr.keys():
        cached = ws_manager.get_cached_price(t)
        if cached is not None:
            kr_prices[t] = cached
        else:
            rest_needed.append(t)
    if rest_needed:
        try:
            token = await asyncio.wait_for(get_kis_token(), timeout=5)
            for t in rest_needed:
                try:
                    data = await asyncio.wait_for(kis_stock_price(t, token), timeout=5)
                    price = int(data.get("stck_prpr", 0) or 0)
                    if price:
                        kr_prices[t] = price
                except Exception:
                    pass
                await asyncio.sleep(0.3)
        except Exception:
            pass

    # ── US 현재가: WebSocket 캐시 우선 → REST fallback ──
    us_prices: dict[str, float] = {}
    usd_krw = 0.0
    if us:
        us_rest_needed = []
        for sym in us.keys():
            cached = ws_manager.get_cached_price(sym)
            if cached is not None:
                us_prices[sym] = float(cached)
            else:
                us_rest_needed.append(sym)
        if us_rest_needed:
            try:
                token = await asyncio.wait_for(get_kis_token(), timeout=5)
                for sym in us_rest_needed:
                    try:
                        data = await asyncio.wait_for(kis_us_stock_price(sym, token), timeout=5)
                        price = float(data.get("last", 0) or 0)
                        if price:
                            us_prices[sym] = price
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)
            except Exception:
                pass

        # 환율 조회 (Yahoo Finance KRW=X)
        try:
            fx = await asyncio.wait_for(get_yahoo_quote("KRW=X"), timeout=5)
            usd_krw = float(fx.get("price", 0) or 0) if fx else 0.0
        except Exception:
            pass

    # ── 합계 계산 ──
    cash_k = float(pf.get("cash_krw", 0) or 0)
    cash_u = float(pf.get("cash_usd", 0) or 0)

    kr_total_cost = kr_total_eval = 0
    for t, v in kr.items():
        qty = int(v.get("qty", 0))
        avg = int(v.get("avg_price", 0))
        cur = kr_prices.get(t, 0)
        kr_total_cost += qty * avg
        kr_total_eval += qty * cur if cur else 0

    us_total_cost_usd = us_total_eval_usd = 0.0
    for sym, info in us.items():
        qty = float(info.get("qty", 0) or 0)
        avg = float(info.get("avg_price", 0) or 0)
        cur = us_prices.get(sym, 0.0)
        us_total_cost_usd += qty * avg
        us_total_eval_usd += qty * cur if cur else 0.0

    us_eval_krw = us_total_eval_usd * usd_krw if usd_krw else 0.0
    us_cost_krw = us_total_cost_usd * usd_krw if usd_krw else 0.0
    cash_total_krw = cash_k + (cash_u * usd_krw if usd_krw else 0.0)

    grand_eval = kr_total_eval + us_eval_krw + cash_total_krw
    grand_cost = kr_total_cost + us_cost_krw
    grand_pnl = grand_eval - grand_cost - cash_total_krw   # 현금은 손익 계산 제외
    grand_pnl_pct = grand_pnl / grand_cost * 100 if grand_cost else 0.0

    def _pc(val: float) -> str:
        return "pos" if val >= 0 else "neg"

    def _sign(val: float) -> str:
        return "+" if val >= 0 else ""

    # ── 상단 요약 카드 ──
    pnl_cls = _pc(grand_pnl)
    html = '<div class="pf-summary">'
    html += f'<div class="pf-total">{grand_eval:,.0f}원</div>'
    html += f'<div class="pf-pnl {pnl_cls}">{_sign(grand_pnl)}{grand_pnl:,.0f}원 ({_sign(grand_pnl_pct)}{grand_pnl_pct:.1f}%)</div>'

    cash_parts = []
    if cash_k:
        cash_parts.append(f"KRW {cash_k:,.0f}원")
    if cash_u:
        cash_parts.append(f"USD ${cash_u:,.2f}")
    if cash_parts:
        html += f'<div class="pf-cash">현금 {" | ".join(cash_parts)}</div>'
    html += '</div>'

    # ── 🇰🇷 한국 섹션 ──
    if kr:
        kr_pnl = kr_total_eval - kr_total_cost
        kr_pnl_pct = kr_pnl / kr_total_cost * 100 if kr_total_cost else 0.0
        kr_pnl_cls = _pc(kr_pnl)
        html += '<div class="pf-section-header">'
        html += '<span class="pf-section-title">🇰🇷 한국 주식</span>'
        html += (f'<span class="pf-section-summary">'
                 f'평가 {kr_total_eval:,.0f}원'
                 f' &nbsp; <span class="{kr_pnl_cls}">{_sign(kr_pnl)}{kr_pnl:,.0f}원 ({_sign(kr_pnl_pct)}{kr_pnl_pct:.1f}%)</span>'
                 f'</span>')
        html += '</div>'

        # 정렬 버튼
        html += ('<div class="pf-sort-bar">'
                 '<button class="pf-sort-btn active" data-section="kr" data-sort="eval">평가금순</button>'
                 '<button class="pf-sort-btn" data-section="kr" data-sort="pnl-pct">수익률순</button>'
                 '<button class="pf-sort-btn" data-section="kr" data-sort="pnl-amt">손익금순</button>'
                 '</div>')

        # 평가금 내림차순 정렬
        kr_items = []
        for t, v in kr.items():
            qty = int(v.get("qty", 0))
            avg = int(v.get("avg_price", 0))
            cur = kr_prices.get(t, 0)
            ev = qty * cur if cur else qty * avg  # 현재가 없으면 매입금
            pnl_amt = (qty * cur - qty * avg) if cur else 0
            pnl_pct = (cur - avg) / avg * 100 if (cur and avg) else 0.0
            kr_items.append((t, v, qty, avg, cur, ev, pnl_amt, pnl_pct))
        kr_items.sort(key=lambda x: x[5], reverse=True)

        html += '<div id="pf-kr-list">'
        for t, v, qty, avg, cur, ev, pnl_amt, pnl_pct in kr_items:
            name = _html.escape(v.get("name", t))
            pc = _pc(pnl_amt)
            cost = qty * avg

            cur_str = (f'<span class="pf-label">현재가</span>{cur:,}원' if cur else "-")
            ev_str = f'<span class="pf-label">평가</span>{ev:,.0f}원'
            pnl_str = (f'<span class="pf-label">손익</span><span class="{pc}">{_sign(pnl_amt)}{pnl_amt:,.0f}원 {_sign(pnl_pct)}{pnl_pct:.1f}%</span>'
                       if cur else "-")
            detail = (f'{qty:,}주 · <span class="pf-label">평단</span>{avg:,}원 · '
                      f'<span class="pf-label">매입</span>{cost:,.0f}원')

            html += (f'<div class="pf-card" data-eval="{ev}" data-pnl-pct="{pnl_pct:.4f}" data-pnl-amt="{pnl_amt}">'
                     f'<div class="pf-left"><div class="pf-name">{name}</div><div class="pf-detail">{detail}</div></div>'
                     f'<div class="pf-right"><div class="pf-price">{cur_str}</div>'
                     f'<div class="pf-eval">{ev_str}</div>'
                     f'<div class="pf-pnl-row">{pnl_str}</div></div>'
                     f'</div>')
        html += '</div>'

    # ── 🇺🇸 미국 섹션 ──
    if us:
        us_pnl_usd = us_total_eval_usd - us_total_cost_usd
        us_pnl_pct = us_pnl_usd / us_total_cost_usd * 100 if us_total_cost_usd else 0.0
        us_pnl_cls = _pc(us_pnl_usd)
        fx_str = f" (USD/KRW {usd_krw:,.1f})" if usd_krw else ""
        eval_krw_str = f" = {us_eval_krw:,.0f}원" if usd_krw else ""

        html += '<div class="pf-section-header" style="margin-top:16px">'
        html += f'<span class="pf-section-title">🇺🇸 미국 주식{fx_str}</span>'
        html += (f'<span class="pf-section-summary">'
                 f'평가 ${us_total_eval_usd:,.2f}{eval_krw_str}'
                 f' &nbsp; <span class="{us_pnl_cls}">{_sign(us_pnl_usd)}${us_pnl_usd:,.2f} ({_sign(us_pnl_pct)}{us_pnl_pct:.1f}%)</span>'
                 f'</span>')
        html += '</div>'

        # 정렬 버튼
        html += ('<div class="pf-sort-bar">'
                 '<button class="pf-sort-btn active" data-section="us" data-sort="eval">평가금순</button>'
                 '<button class="pf-sort-btn" data-section="us" data-sort="pnl-pct">수익률순</button>'
                 '<button class="pf-sort-btn" data-section="us" data-sort="pnl-amt">손익금순</button>'
                 '</div>')

        # 평가금(USD) 내림차순 정렬
        us_items = []
        for sym, info in us.items():
            qty = float(info.get("qty", 0) or 0)
            avg = float(info.get("avg_price", 0) or 0)
            cur = us_prices.get(sym, 0.0)
            ev_usd = qty * cur if cur else qty * avg  # 현재가 없으면 매입금
            pnl_usd = (qty * cur - qty * avg) if cur else 0.0
            pnl_pct = (cur - avg) / avg * 100 if (cur and avg) else 0.0
            # 정렬용 평가금은 원화 환산값 우선, 없으면 USD 그대로
            ev_sort = ev_usd * usd_krw if usd_krw else ev_usd
            us_items.append((sym, info, qty, avg, cur, ev_usd, pnl_usd, pnl_pct, ev_sort))
        us_items.sort(key=lambda x: x[8], reverse=True)

        html += '<div id="pf-us-list">'
        for sym, info, qty, avg, cur, ev_usd, pnl_usd, pnl_pct, ev_sort in us_items:
            name = _html.escape(info.get("name", sym))
            pc = _pc(pnl_usd)
            cost_usd = qty * avg

            cur_str = (f'<span class="pf-label">현재가</span>${cur:,.2f}' if cur else "-")
            ev_usd_str = f'<span class="pf-label">평가</span>${ev_usd:,.2f}'
            ev_krw_str = f" ({ev_usd * usd_krw:,.0f}원)" if (ev_usd and usd_krw) else ""
            pnl_str = (f'<span class="pf-label">손익</span><span class="{pc}">{_sign(pnl_usd)}${pnl_usd:,.2f} {_sign(pnl_pct)}{pnl_pct:.1f}%</span>'
                       if cur else "-")
            detail = (f'{qty:,.0f}주 · <span class="pf-label">평단</span>${avg:,.2f} · '
                      f'<span class="pf-label">매입</span>${cost_usd:,.2f}')

            html += (f'<div class="pf-card" data-eval="{ev_sort:.2f}" data-pnl-pct="{pnl_pct:.4f}" data-pnl-amt="{pnl_usd:.4f}">'
                     f'<div class="pf-left"><div class="pf-name">{name} <span style="color:var(--fg2);font-size:0.8em">({_html.escape(sym)})</span></div>'
                     f'<div class="pf-detail">{detail}</div></div>'
                     f'<div class="pf-right"><div class="pf-price">{cur_str}</div>'
                     f'<div class="pf-eval">{ev_usd_str}{ev_krw_str}</div>'
                     f'<div class="pf-pnl-row">{pnl_str}</div></div>'
                     f'</div>')
        html += '</div>'

    if not kr and not us:
        return "<p>포트폴리오 비어있음</p>"
    return html


def _build_watchalert_html() -> str:
    """watchalert.json → 감시가 테이블."""
    wa = load_watchalert()
    if not wa:
        return "<p>감시 종목 없음</p>"
    items = []
    for t, v in wa.items():
        bp = float(v.get("buy_price", 0) or 0)
        items.append({"name": v.get("name", t), "ticker": t, "buy_price": bp,
                       "grade": v.get("grade", ""), "memo": v.get("memo", "")[:40]})
    items.sort(key=lambda x: x["buy_price"], reverse=True)
    html = "<table><thead><tr><th>종목</th><th>코드</th><th>감시가</th><th>등급</th><th>메모</th></tr></thead><tbody>"
    for i in items[:30]:
        bp = f"${i['buy_price']:,.2f}" if _is_us_ticker(i["ticker"]) else f"{i['buy_price']:,.0f}원"
        html += (f"<tr><td>{_html.escape(i['name'])}</td><td>{_html.escape(i['ticker'])}</td>"
                 f"<td>{bp}</td><td>{_html.escape(i['grade'])}</td><td>{_html.escape(i['memo'])}</td></tr>")
    html += "</tbody></table>"
    if len(items) > 30:
        html += f"<p>... 외 {len(items) - 30}종목</p>"
    return html


async def _handle_dash_file(request: web.Request) -> web.Response:
    """GET /dash/file/{filename} — data/ 파일 렌더링."""
    try:
        filename = request.match_info.get("filename", "")

        # 보안
        if ".." in filename or "/" in filename or "\\" in filename:
            return web.Response(text="Forbidden", status=403)
        if filename.endswith((".py", ".env", ".sh")):
            return web.Response(text="Forbidden", status=403)

        filepath = os.path.join(_DATA_DIR, filename)
        if not os.path.isfile(filepath):
            return web.Response(text="Not Found", status=404)
        if os.path.getsize(filepath) > 500 * 1024:
            return web.Response(text="File too large", status=413)

        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        safe_filename = _html.escape(filename)
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_filename}</title>{_DASH_CSS}</head><body>
<div class="nav"><a href="/dash">← 대시보드</a></div>
<h1>{safe_filename}</h1>"""

        if filename.endswith(".md") or filename.endswith(".txt"):
            html += _md_to_html(content)
        elif filename.endswith(".json"):
            try:
                data = json.loads(content)
                if filename == "portfolio.json":
                    html += _build_portfolio_html()
                elif filename == "watchalert.json":
                    html += _build_watchalert_html()
                else:
                    html += _json_to_table(data)
            except Exception:
                html += f"<pre>{_html.escape(content[:10000])}</pre>"
        else:
            html += f"<pre>{_html.escape(content[:10000])}</pre>"

        html += "</body></html>"
        return web.Response(text=html, content_type="text/html")
    except Exception as e:
        import traceback
        print(f"[Dash] file 오류: {e}\n{traceback.format_exc()}")
        return web.Response(text=f"Error: {e}", status=500)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 웹 대시보드 v2 (/dash-v2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_DASH_V2_CSS = """
<style>
:root{--bg:#1a1a2e;--bg2:#16213e;--fg:#e0e0e0;--fg2:#a0a0b0;--accent:#4fc3f7;--red:#ef5350;--green:#66bb6a;--border:#2a2a4a}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,sans-serif;padding:16px;padding-top:72px;max-width:900px;margin:0 auto;line-height:1.6}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:1.5em;margin:16px 0 8px}
h2{font-size:1.2em;margin:0 0 12px;color:var(--fg)}
h3{font-size:1.0em;margin:12px 0 4px;color:var(--accent)}
p{margin:8px 0}
code{background:var(--bg2);padding:2px 6px;border-radius:3px;font-family:'SF Mono',monospace;font-size:0.9em}
pre{background:var(--bg2);padding:12px;border-radius:6px;overflow-x:auto;margin:8px 0;font-size:0.85em;border:1px solid var(--border)}
pre code{background:none;padding:0}
ul,ol{margin:8px 0 8px 20px}li{margin:2px 0}
.check{display:flex;align-items:center;gap:6px;margin:2px 0}
.check input{width:16px;height:16px;accent-color:var(--accent)}
.tab-nav{position:sticky;top:0;z-index:100;background:var(--bg);display:flex;gap:4px;padding:8px 0;border-bottom:2px solid var(--border);overflow-x:auto;margin-bottom:0}
.tab-nav a{padding:6px 14px;border-radius:16px;white-space:nowrap;font-size:0.85em;color:var(--fg2);text-decoration:none;transition:background 0.2s,color 0.2s}
.tab-nav a:hover{background:var(--bg2);color:var(--fg)}
.tab-nav a.active{background:var(--accent);color:#000;font-weight:600}
.section{background:var(--bg2);border-radius:8px;padding:16px;margin:16px 0;border:1px solid var(--border);scroll-margin-top:60px;transition:border-color 0.2s}
.section:hover{border-color:rgba(79,195,247,0.3)}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:0.9em}
thead th{color:var(--accent);font-weight:600;font-size:0.85em;padding:8px 10px;text-align:left;border-bottom:1px solid var(--border)}
tbody td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--border)}
tbody tr:hover{background:rgba(255,255,255,0.03)}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:0.75em;font-weight:600}
.badge-A{background:rgba(239,83,80,0.2);color:#ef5350}
.badge-Bp{background:rgba(255,167,38,0.2);color:#ffa726}
.badge-B{background:rgba(102,187,106,0.2);color:#66bb6a}
.badge-Bm{background:rgba(120,144,156,0.2);color:#78909c}
.badge-C{background:rgba(120,144,156,0.2);color:#78909c}
.badge-buy{background:rgba(102,187,106,0.15);color:var(--green)}
.badge-sell{background:rgba(239,83,80,0.15);color:var(--red)}
.pos{color:var(--green)}.neg{color:var(--red)}
.pf-summary{background:var(--bg);border-radius:12px;padding:16px;margin-bottom:16px;text-align:center}
.pf-total{font-size:1.8em;font-weight:700;margin:4px 0}
.pf-pnl{font-size:1.2em;font-weight:600}
.pf-cash{font-size:0.85em;color:var(--fg2);margin-top:8px}
.pf-section-header{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);margin-bottom:8px}
.pf-section-title{font-weight:600}
.pf-section-summary{font-size:0.85em;color:var(--fg2);text-align:right}
.pf-card{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.05)}
.pf-card:last-child{border-bottom:none}
.pf-left{}
.pf-name{font-weight:600;font-size:0.95em}
.pf-detail{font-size:0.8em;color:var(--fg2);margin-top:2px}
.pf-right{text-align:right}
.pf-price{font-weight:600}
.pf-eval{font-size:0.85em;color:var(--fg2);margin-top:1px}
.pf-pnl-row{font-size:0.85em;margin-top:2px}
.pf-label{font-size:0.7em;color:var(--fg2);margin-right:2px}
.pf-sort-bar{display:flex;gap:4px;margin-bottom:8px}
.pf-sort-btn{padding:4px 10px;border-radius:12px;border:1px solid var(--border);background:transparent;color:var(--fg2);cursor:pointer;font-size:0.75em;transition:background 0.2s,color 0.2s}
.pf-sort-btn.active{background:var(--accent);color:#000;border-color:var(--accent)}
.dday{font-weight:700;color:var(--accent);white-space:nowrap;text-align:center}
.dday-0{font-weight:700;color:var(--red);animation:pulse 1s infinite;white-space:nowrap;text-align:center}
@keyframes pulse{50%{opacity:0.6}}
.doc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
.doc-card{background:var(--bg);border-radius:8px;padding:12px;border:1px solid var(--border);text-decoration:none;color:var(--fg);transition:border-color 0.2s,transform 0.2s;display:block}
.doc-card:hover{border-color:var(--accent);transform:translateY(-2px);text-decoration:none}
.doc-icon{font-size:1.5em;margin-bottom:4px}
.doc-name{font-size:0.85em;font-weight:600}
.doc-desc{font-size:0.75em;color:var(--fg2)}
.search-box{width:100%;padding:8px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--fg);font-size:0.9em;margin-bottom:8px}
.search-box:focus{outline:none;border-color:var(--accent)}
.filter-bar{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px;align-items:center}
.filter-btn{padding:4px 10px;border-radius:12px;border:1px solid var(--border);background:transparent;color:var(--fg2);cursor:pointer;font-size:0.75em;transition:background 0.2s,color 0.2s}
.filter-btn.active{background:var(--accent);color:#000;border-color:var(--accent)}
.refresh-bar{display:flex;justify-content:space-between;align-items:center;padding:4px 0;font-size:0.75em;color:var(--fg2);margin-bottom:8px}
.toggle{cursor:pointer;user-select:none}
details summary{cursor:pointer;user-select:none}
details summary h2{display:inline}
.sector-group{margin-bottom:8px;border:1px solid var(--border);border-radius:8px;overflow:hidden}
.sector-group[open]{border-color:rgba(79,195,247,0.3)}
.sector-header{padding:8px 12px;cursor:pointer;font-weight:600;font-size:0.9em;background:var(--bg);list-style:none;display:flex;align-items:center;gap:6px}
.sector-header::-webkit-details-marker{display:none}
.watch-sector-table{margin:0}
.decision-card{background:var(--bg);border-radius:8px;padding:8px 12px;margin-bottom:8px;border:1px solid var(--border)}
.decision-card[open]{border-color:var(--accent)}
.decision-card summary{cursor:pointer;display:flex;align-items:center;gap:8px;flex-wrap:wrap;list-style:none}
.decision-card summary::-webkit-details-marker{display:none}
.decision-date{font-weight:600;font-size:0.9em;min-width:90px}
.decision-preview{color:var(--fg2);font-size:0.8em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.decision-body{margin-top:10px;padding-top:10px;border-top:1px solid var(--border)}
.decision-actions{margin-bottom:8px}
.decision-actions li{font-size:0.85em;margin:2px 0;list-style:none;padding-left:12px}
.decision-actions li::before{content:"→ ";color:var(--accent)}
.decision-notes{font-size:0.82em;color:var(--fg2);background:rgba(255,255,255,0.02);padding:8px;border-radius:4px;margin-bottom:8px}
.decision-grades{font-size:0.82em}
.badge-neutral{background:rgba(255,193,7,0.15);color:#ffc107}
.badge-bull{background:rgba(102,187,106,0.15);color:var(--green)}
.badge-bear{background:rgba(239,83,80,0.15);color:var(--red)}
@media(max-width:600px){body{padding:8px;padding-top:72px}.tab-nav{font-size:0.8em}table{font-size:0.8em}.doc-grid{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}}
</style>
"""


def _dash_v2_js() -> str:
    """대시보드 v2 JS (탭 하이라이트 + 자동새로고침 + 감시종목 검색/필터)."""
    return """<script>
// 1. 탭 하이라이트 (IntersectionObserver)
const sections = document.querySelectorAll('.section[id]');
const tabs = document.querySelectorAll('.tab-nav a');
const obs = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      tabs.forEach(t => t.classList.remove('active'));
      const tab = document.querySelector('.tab-nav a[href="#' + e.target.id + '"]');
      if (tab) tab.classList.add('active');
    }
  });
}, { rootMargin: '-60px 0px -70% 0px' });
sections.forEach(s => obs.observe(s));

// 2. 자동 새로고침
let refreshInterval = null;
const REFRESH_MS = 300000;
const refreshToggle = document.getElementById('refresh-toggle');
const refreshTime = document.getElementById('refresh-time');

function startRefresh() {
  refreshInterval = setInterval(() => location.reload(), REFRESH_MS);
  localStorage.setItem('autoRefresh', 'on');
  if (refreshToggle) refreshToggle.textContent = '⏸ 자동갱신 끄기';
}
function stopRefresh() {
  clearInterval(refreshInterval);
  localStorage.setItem('autoRefresh', 'off');
  if (refreshToggle) refreshToggle.textContent = '▶ 자동갱신 켜기';
}
if (refreshToggle) {
  refreshToggle.addEventListener('click', () => {
    if (localStorage.getItem('autoRefresh') === 'off') startRefresh();
    else stopRefresh();
  });
}
if (localStorage.getItem('autoRefresh') !== 'off') startRefresh();
else stopRefresh();
if (refreshTime) refreshTime.textContent = new Date().toLocaleTimeString('ko-KR');

// 3. 감시종목 검색/필터
const searchInput = document.getElementById('watch-search');
const filterBtns = document.querySelectorAll('.filter-btn');
const watchCount = document.getElementById('watch-count');
let currentFilter = 'all';

function filterWatch() {
  const tables = document.querySelectorAll('.watch-sector-table');
  if (!tables.length) return;
  const q = (searchInput ? searchInput.value : '').toLowerCase();
  let visible = 0, total = 0;
  tables.forEach(tbl => {
    const rows = tbl.querySelectorAll('tbody tr');
    let groupVisible = 0;
    rows.forEach(r => {
      const name = (r.dataset.name || '').toLowerCase();
      const ticker = (r.dataset.ticker || '').toLowerCase();
      const grade = r.dataset.grade || '';
      const market = r.dataset.market || '';
      const sectorName = (tbl.closest('.sector-group')?.dataset.sector || '').toLowerCase();
      const matchSearch = !q || name.includes(q) || ticker.includes(q) || sectorName.includes(q);
      const matchFilter = currentFilter === 'all'
        || (currentFilter === 'kr' && market === 'kr')
        || (currentFilter === 'us' && market === 'us')
        || grade.startsWith(currentFilter);
      const show = matchSearch && matchFilter;
      r.style.display = show ? '' : 'none';
      if (show) { visible++; groupVisible++; }
      total++;
    });
    // 그룹 내 visible 종목이 없으면 details 자체를 숨김
    const details = tbl.closest('.sector-group');
    if (details) details.style.display = groupVisible === 0 ? 'none' : '';
  });
  if (watchCount) watchCount.textContent = visible + '/' + total + '종목';
}

if (searchInput) searchInput.addEventListener('input', filterWatch);
filterBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    filterBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentFilter = btn.dataset.filter;
    filterWatch();
  });
});

// 4. 포트폴리오 정렬
document.querySelectorAll('.pf-sort-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const section = btn.dataset.section; // 'kr' or 'us'
    const sortKey = btn.dataset.sort;    // 'eval', 'pnl-pct', 'pnl-amt'
    const container = document.getElementById('pf-' + section + '-list');
    if (!container) return;
    const cards = [...container.querySelectorAll('.pf-card')];
    const attr = sortKey === 'eval' ? 'eval'
               : sortKey === 'pnl-pct' ? 'pnlPct'
               : 'pnlAmt';
    cards.sort((a, b) => {
      const av = parseFloat(a.dataset[attr] || 0);
      const bv = parseFloat(b.dataset[attr] || 0);
      return bv - av;
    });
    cards.forEach(c => container.appendChild(c));
    // 같은 section의 버튼만 토글
    btn.closest('.pf-sort-bar').querySelectorAll('.pf-sort-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

// 5. TODO 체크박스 토글 (클릭 → 서버에 [ ] ↔ [x] 반영)
document.addEventListener('change', async (e) => {
  const cb = e.target;
  if (cb.type !== 'checkbox' || !cb.dataset.todoFile) return;
  const payload = {
    file: cb.dataset.todoFile,
    line: parseInt(cb.dataset.todoLine),
    hash: cb.dataset.todoHash,
    checked: cb.checked
  };
  try {
    const r = await fetch('/dash/todo/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    if (!r.ok) {
      cb.checked = !cb.checked;  // rollback
      const d = await r.json().catch(() => ({}));
      if (r.status === 409) {
        if (confirm('다른 세션이 이 파일을 편집했습니다.\n페이지를 새로고침하고 다시 시도할까요?')) {
          location.reload();
        }
      } else {
        alert('토글 실패: ' + (d.error || r.status));
      }
      return;
    }
    const d = await r.json();
    if (d.new_hash) cb.dataset.todoHash = d.new_hash;
    // 시각 효과: 옆 텍스트 line-through 토글
    const span = cb.nextElementSibling;
    if (span) {
      if (cb.checked) {
        span.style.textDecoration = 'line-through';
        span.style.color = 'var(--fg2)';
      } else {
        span.style.textDecoration = '';
        span.style.color = '';
      }
    }
  } catch (err) {
    cb.checked = !cb.checked;
    alert('네트워크 오류: ' + err.message);
  }
});

// 6. TODO 항목 추가 폼
document.addEventListener('submit', async (e) => {
  const form = e.target;
  if (!form.classList.contains('todo-add-form')) return;
  e.preventDefault();
  const text = form.querySelector('[name=text]').value.trim();
  if (!text) return;
  const payload = {file: form.dataset.file, text: text};
  try {
    const r = await fetch('/dash/todo/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert('추가 실패: ' + (d.error || r.status));
      return;
    }
    location.reload();
  } catch (err) {
    alert('네트워크 오류: ' + err.message);
  }
});

// 7. 투자판단 저장 폼
document.getElementById('decision-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const payload = {
    date: form.date.value,
    regime: form.regime.value,
    notes: form.notes.value,
    actions: form.actions.value,
    grades: form.grades.value
  };
  try {
    const r = await fetch('/dash/decisions/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert('저장 실패: ' + (d.error || r.status));
      return;
    }
    location.reload();
  } catch (err) {
    alert('네트워크 오류: ' + err.message);
  }
});
</script>"""


def _build_events_v2_html() -> str:
    """이벤트 D-day 카운트 + 과거/미래 분리.

    events.json 형식 지원:
      - {"FOMC": "2026-04-28"}  (key=이벤트명, val=날짜) ← 신규
      - {"2026-04-28": "FOMC"}  (key=날짜, val=이벤트명) ← 구버전 호환
      - {"이란": "진행중"}       (날짜 없음 → 기타)
    """
    events = load_json(f"{_DATA_DIR}/events.json", {})
    if not events:
        return "<p>등록된 이벤트 없음</p>"
    today = datetime.now(KST).date()
    future, past = [], []
    for key, val in events.items():
        # 키-값 중 어느 쪽이 날짜인지 판별
        if re.match(r'^\d{4}-\d{2}-\d{2}$', str(val)):
            event_name, date_str = key, str(val)
        elif re.match(r'^\d{4}-\d{2}-\d{2}$', str(key)):
            event_name, date_str = str(val), key
        else:
            # 날짜 없음 → 미래 목록 맨 뒤
            future.append((str(key), str(val), None))
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            delta = (d - today).days
            if delta >= 0:
                future.append((event_name, date_str, delta))
            else:
                past.append((event_name, date_str, delta))
        except Exception:
            future.append((event_name, date_str, None))

    # 미래: D-day 오름차순 (None은 맨 뒤)
    future.sort(key=lambda x: (x[2] is None, x[2] if x[2] is not None else 9999))
    # 과거: 최근 먼저 (delta 내림차순 → 절댓값 오름차순)
    past.sort(key=lambda x: x[2] if x[2] is not None else -9999, reverse=True)

    html = ""
    if future:
        html += '<div class="table-wrap"><table><thead><tr><th style="width:60px;min-width:60px;text-align:center">D-day</th><th>날짜</th><th>이벤트</th></tr></thead><tbody>'
        for event_name, ds, delta in future:
            if delta is None:
                dday_cls, dday_text = "dday", "—"
            elif delta == 0:
                dday_cls, dday_text = "dday-0", "D-DAY"
            else:
                dday_cls, dday_text = "dday", f"D-{delta}"
            display_name = _html.escape(event_name.replace("_", " "))
            html += f'<tr><td class="{dday_cls}">{dday_text}</td><td>{_html.escape(ds)}</td><td>{display_name}</td></tr>'
        html += '</tbody></table></div>'

    if past:
        html += f'<details><summary style="color:var(--fg2);font-size:0.85em;margin-top:12px;padding:4px 0">지난 이벤트 ({len(past)}건)</summary>'
        html += '<div class="table-wrap"><table><thead><tr><th>날짜</th><th>이벤트</th></tr></thead><tbody>'
        for event_name, ds, _ in past:
            display_name = _html.escape(event_name.replace("_", " "))
            html += f'<tr style="color:var(--fg2)"><td>{_html.escape(ds)}</td><td>{display_name}</td></tr>'
        html += '</tbody></table></div></details>'
    return html


_US_SECTOR_MAP = {
    "NVDA": "반도체", "AMD": "반도체", "AVGO": "반도체", "MRVL": "반도체", "ON": "반도체",
    "LITE": "광통신", "GLW": "광통신",
    "VRT": "전력기기", "ETN": "전력기기", "NVT": "전력기기", "MOD": "전력기기",
    "BWXT": "방산", "LEU": "원전",
    "TSLA": "자동차", "META": "인터넷/플랫폼", "PANW": "사이버보안",
    "UNH": "헬스케어", "ASTS": "통신/우주", "TTD": "광고/미디어",
    "XYL": "환경/수처리", "CRSP": "바이오",
}


def _build_watchalert_v2_html() -> str:
    """감시종목 섹터별 그룹핑 + 현재가 + 검색 + 등급 필터 + 뱃지."""
    from collections import defaultdict
    wa = load_json(WATCHALERT_FILE, {})
    if not wa:
        return "<p>감시 종목 없음</p>"

    # 현재가: WS 캐시(장중 실시간 + stoploss 갱신) → SQLite DB fallback
    cur_prices = {}
    # 1차: WS 캐시 (check_stoploss에서 10분마다 갱신됨)
    for ticker, _ in wa.items():
        cached = ws_manager.get_cached_price(ticker)
        if cached is not None:
            cur_prices[ticker] = cached
    # 2차: 캐시에 없는 종목은 SQLite DB에서 (KR 섹터 정보도 함께 수집)
    kr_sector_map: dict[str, str] = {}
    try:
        from db_collector import _get_db
        conn = _get_db()
        latest = conn.execute("SELECT MAX(trade_date) FROM daily_snapshot").fetchone()[0]
        if latest:
            rows = conn.execute("SELECT symbol, close FROM daily_snapshot WHERE trade_date=?", (latest,)).fetchall()
            for r in rows:
                if r["symbol"] not in cur_prices:
                    cur_prices[r["symbol"]] = r["close"]
        # 섹터 정보
        try:
            sec_rows = conn.execute("SELECT symbol, sector FROM stock_master").fetchall()
            kr_sector_map = {r["symbol"]: r["sector"] for r in sec_rows if r["sector"]}
        except Exception:
            pass
        conn.close()
    except Exception:
        pass

    # 종목별 섹터 부여 후 그룹핑
    groups: dict[str, list] = defaultdict(list)
    for ticker, info in wa.items():
        is_us = not ticker.isdigit()
        if is_us:
            sector = _US_SECTOR_MAP.get(ticker, "기타")
        else:
            sector = kr_sector_map.get(ticker, "기타")
        groups[sector].append((ticker, info))

    # 각 그룹 내 등급순 → 같은 등급 내 buy_price 내림차순
    for sector in groups:
        groups[sector].sort(key=lambda x: (
            _GRADE_ORDER.get(x[1].get("grade", ""), 7),
            -float(x[1].get("buy_price", 0) or 0),
        ))

    # 섹터 정렬: 종목 수 많은 순
    sorted_sectors = sorted(groups.keys(), key=lambda s: -len(groups[s]))

    total = sum(len(v) for v in groups.values())
    all_items = [(t, i) for s in sorted_sectors for t, i in groups[s]]

    # 검색 + 필터 UI
    html = '<input id="watch-search" class="search-box" placeholder="종목명 또는 코드 검색...">'
    html += '<div class="filter-bar">'
    html += '<button class="filter-btn active" data-filter="all">전체</button>'
    html += '<button class="filter-btn" data-filter="kr">🇰🇷</button>'
    html += '<button class="filter-btn" data-filter="us">🇺🇸</button>'
    grades = sorted(set(v.get("grade", "") for _, v in all_items if v.get("grade")))
    for g in grades:
        html += f'<button class="filter-btn" data-filter="{g}">{g}</button>'
    html += f'<span id="watch-count" style="margin-left:auto;color:var(--fg2);font-size:0.8em">{total}/{total}종목</span>'
    html += '</div>'

    def _render_row(ticker: str, info: dict) -> str:
        name = _html.escape(info.get("name", ticker))
        bp = float(info.get("buy_price", 0) or 0)
        grade = _html.escape(info.get("grade", ""))
        memo = _html.escape(str(info.get("memo", ""))[:60])
        ticker_esc = _html.escape(ticker)
        is_us = not ticker.isdigit()
        market = "us" if is_us else "kr"
        price_str = f"${bp:,.2f}" if is_us else f"{int(bp):,}원"
        cur = cur_prices.get(ticker, 0)
        if cur:
            cur_str = f"${float(cur):,.2f}" if is_us else f"{int(cur):,}원"
            gap_pct = (float(cur) - bp) / bp * 100 if bp else 0
            gap_cls = "pos" if gap_pct >= 0 else "neg"
            gap_str = f"<span class='{gap_cls}'>{gap_pct:+.1f}%</span>"
        else:
            cur_str = "-"
            gap_str = "-"
        reg_date = info.get("updated_at") or info.get("created", "")
        reg_date_esc = _html.escape(str(reg_date)[:10]) if reg_date else "-"
        grade_key = grade.replace("+", "p").replace("-", "m")
        badge_cls = f"badge-{grade_key}" if grade else ""
        grade_html = f'<span class="badge {badge_cls}">{grade}</span>' if grade else ""
        return (f'<tr data-name="{name}" data-ticker="{ticker_esc}" data-grade="{grade}" data-market="{market}">'
                f'<td>{name}</td><td>{ticker_esc}</td><td>{price_str}</td>'
                f'<td>{cur_str}</td><td>{gap_str}</td>'
                f'<td>{grade_html}</td>'
                f'<td style="font-size:0.8em;color:var(--fg2)">{reg_date_esc}</td>'
                f'<td style="font-size:0.8em;color:var(--fg2)">{memo}</td></tr>')

    # 섹터별 그룹 렌더링
    for sector in sorted_sectors:
        items = groups[sector]
        count = len(items)
        sector_esc = _html.escape(sector)
        html += f'<div class="sector-group" data-sector="{sector_esc}">'
        html += (f'<div class="sector-header">{sector_esc}'
                 f' <span style="color:var(--fg2);font-size:0.85em">({count}종목)</span></div>')
        html += ('<div class="table-wrap"><table class="watch-sector-table">'
                 '<thead><tr><th>종목</th><th>코드</th><th>감시가</th><th>현재가</th>'
                 '<th>괴리</th><th>등급</th><th>등록일</th><th>메모</th></tr></thead><tbody>')
        for ticker, info in items:
            html += _render_row(ticker, info)
        html += '</tbody></table></div></div>'

    return html


_DOC_META_V2 = {
    "TODO.md": ("📋", "할일 목록"),
    "INVESTMENT_RULES.md": ("📏", "투자 규칙"),
    "PROGRESS.md": ("🧭", "세션 인수인계"),
    "bot_guide.md": ("📖", "도구 사용법"),
    "bot_reference.txt": ("📘", "도구 파라미터"),
    "bot_scenarios.md": ("🎯", "활용 시나리오"),
    "bot_samples.md": ("🔬", "입출력 샘플"),
    "FILES.md": ("📁", "파일 설명서"),
    "krx_db_design.md": ("🗄️", "KRX DB 설계"),
    "regime_update_notes.md": ("📝", "레짐 수정노트"),
    "US_DEEPSEARCH_v3.md": ("🇺🇸", "미국주식 딥서치 v3"),
    "KR_DEEPSEARCH.md": ("🇰🇷", "한국주식 10 Step"),
}


def _build_docs_v2_html() -> str:
    """문서 카드 그리드 + research/ 서브폴더."""
    html = '<div class="doc-grid">'
    try:
        doc_files = sorted(
            f for f in os.listdir(_DATA_DIR)
            if f.endswith((".md", ".txt")) and not f.startswith(".")
        )
    except Exception:
        doc_files = []

    for f in doc_files:
        if f in ("TODO.md", "TODO_invest.md", "TODO_dev.md"):
            continue  # TODO 파일은 독립 탭에 있으므로 문서 카드에서 제외
        icon, desc = _DOC_META_V2.get(f, ("📄", ""))
        html += (f'<a href="/dash/file/{f}" class="doc-card">'
                 f'<div class="doc-icon">{icon}</div>'
                 f'<div class="doc-name">{f}</div>'
                 f'<div class="doc-desc">{desc}</div></a>')
    html += '</div>'

    for subdir, section_icon, section_label, card_icon, card_desc in (
        ("research", "📊", "종목 리서치", "📊", "딥리서치"),
        ("thesis", "💡", "투자 테제", "💡", "Thesis"),
    ):
        sub_path = os.path.join(_DATA_DIR, subdir)
        # 엔트리 수집: [(display_name, relative_path), ...]
        sub_entries: list[tuple[str, str]] = []
        if subdir == "research":
            # research/: {TICKER}/{file}.md 계층. TICKER 디렉토리 내부 파일을 카드로
            try:
                for ticker_dir in sorted(os.listdir(sub_path)) if os.path.isdir(sub_path) else []:
                    if ticker_dir.startswith("."):
                        continue
                    ticker_path = os.path.join(sub_path, ticker_dir)
                    if not os.path.isdir(ticker_path):
                        continue
                    try:
                        for f in sorted(os.listdir(ticker_path)):
                            if f.endswith(".md") and not f.startswith("."):
                                stem = f.replace(".md", "")
                                disp = ticker_dir if stem == "main" else f"{ticker_dir} / {stem}"
                                sub_entries.append((disp, f"{ticker_dir}/{f}"))
                    except Exception:
                        pass
            except Exception:
                pass
        else:
            # thesis/: flat 유지
            try:
                for f in sorted(os.listdir(sub_path)) if os.path.isdir(sub_path) else []:
                    if f.endswith(".md") and not f.startswith("."):
                        sub_entries.append((f.replace(".md", ""), f))
            except Exception:
                pass

        if sub_entries:
            html += f'<h3 style="margin-top:16px">{section_icon} {section_label}</h3><div class="doc-grid">'
            for disp, rel in sub_entries:
                html += (f'<a href="/dash/file/{subdir}/{rel}" class="doc-card">'
                         f'<div class="doc-icon">{card_icon}</div>'
                         f'<div class="doc-name">{disp}</div>'
                         f'<div class="doc-desc">{card_desc}</div></a>')
            html += '</div>'
    return html


def _build_whale_summary_html() -> str:
    """메인 대시보드 — Whale 섹션 요약 박스 (TOP 3씩 + 전용 페이지 링크)."""
    import sqlite3 as _s
    db_path = f"{_DATA_DIR}/stock.db"
    summary_cards = []

    # 1) NPS KR 풀포트 TOP 3
    try:
        from kis_api import fetch_nps_kr_full_holdings
        kr = fetch_nps_kr_full_holdings(top=3)
        rows = kr.get("rows", []) if not kr.get("error") else []
        body = ''
        for x in rows:
            sc = x.get("share_change_p")
            if x.get("data_missing") or sc is None:
                arrow = ''
            elif sc > 0.05:
                arrow = f' <span style="color:#4caf50">▲{sc:+.2f}p</span>'
            elif sc < -0.05:
                arrow = f' <span style="color:#e57373">▼{sc:+.2f}p</span>'
            else:
                arrow = ''
            body += (f'<div style="display:flex;justify-content:space-between;'
                     f'padding:3px 0;font-size:0.88em">'
                     f'<span>{_html.escape(x.get("name",""))}</span>'
                     f'<span style="color:var(--fg2)">{x.get("weight_pct",0):.2f}%{arrow}</span>'
                     f'</div>')
        if not body:
            body = '<p style="color:var(--fg2);font-size:0.85em">데이터 없음</p>'
        summary_cards.append((
            f'🇰🇷 NPS KR 풀포트',
            f'{kr.get("quarter_label","-")} | {kr.get("total_holdings",0)}종목',
            body,
        ))
    except Exception:
        summary_cards.append(('🇰🇷 NPS KR 풀포트', '?', '<p>로드 실패</p>'))

    # 2) NPS US 13F TOP 3
    try:
        from kis_api import fetch_nps_us_holdings
        us = fetch_nps_us_holdings(top=3, include_changes=True)
        rows = us.get("rows", []) if not us.get("error") else []
        body = ''
        for x in rows:
            sc = x.get("share_change_pct")
            status = x.get("status", "")
            if status == "NEW":
                arrow = ' <span style="color:#4caf50">🆕</span>'
            elif status == "UP" and sc is not None:
                arrow = f' <span style="color:#4caf50">▲{sc:+.1f}%</span>'
            elif status == "DOWN" and sc is not None:
                arrow = f' <span style="color:#e57373">▼{sc:+.1f}%</span>'
            else:
                arrow = ''
            val = x.get("value_usd", 0)
            val_str = f'${val/1e9:.1f}B' if val >= 1e9 else f'${val/1e6:.0f}M'
            body += (f'<div style="display:flex;justify-content:space-between;'
                     f'padding:3px 0;font-size:0.88em">'
                     f'<span>{_html.escape((x.get("name_of_issuer","") or "")[:22])}</span>'
                     f'<span style="color:var(--fg2)">{val_str}{arrow}</span>'
                     f'</div>')
        if not body:
            body = '<p style="color:var(--fg2);font-size:0.85em">데이터 없음</p>'
        summary_cards.append((
            f'🇺🇸 NPS US 13F',
            f'{us.get("quarter","-")} | {us.get("total_holdings",0)}종목',
            body,
        ))
    except Exception:
        summary_cards.append(('🇺🇸 NPS US 13F', '?', '<p>로드 실패</p>'))

    # 3) 연기금 5일 매수 TOP 3 (시총%)
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        dates = [r["trade_date"] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM pension_flow_daily ORDER BY trade_date DESC LIMIT 5"
        ).fetchall()]
        body = ''
        if dates:
            ph = ",".join("?" for _ in dates)
            agg = conn.execute(
                f"""SELECT pf.symbol, pf.name,
                          SUM(pf.net_amount_won) AS net_total
                   FROM pension_flow_daily pf
                   WHERE pf.trade_date IN ({ph})
                   GROUP BY pf.symbol HAVING net_total > 0""", dates
            ).fetchall()
            symbols = [r["symbol"] for r in agg]
            cap_map = {}
            if symbols:
                cph = ",".join("?" for _ in symbols)
                for cr in conn.execute(
                    f"""SELECT symbol, MAX(trade_date) AS d FROM daily_snapshot
                        WHERE symbol IN ({cph}) GROUP BY symbol""", symbols
                ).fetchall():
                    cap = conn.execute(
                        "SELECT market_cap FROM daily_snapshot WHERE symbol=? AND trade_date=?",
                        (cr["symbol"], cr["d"])
                    ).fetchone()
                    if cap and cap["market_cap"]:
                        cap_map[cr["symbol"]] = int(cap["market_cap"]) * 100_000_000
            enriched = []
            for r in agg:
                cap = cap_map.get(r["symbol"], 0)
                pct = (r["net_total"] * 100.0 / cap) if cap > 0 else 0
                enriched.append({"name": r["name"], "net": r["net_total"], "pct": pct, "cap": cap})
            top3 = sorted(enriched, key=lambda x: (-x["pct"] if x["cap"] else 0, -x["net"]))[:3]
            for e in top3:
                body += (f'<div style="display:flex;justify-content:space-between;'
                         f'padding:3px 0;font-size:0.88em">'
                         f'<span>🟢 {_html.escape(e["name"] or "")}</span>'
                         f'<span style="color:#4caf50">{e["net"]/1e8:+,.0f}억 '
                         f'({e["pct"]:+.2f}%)</span>'
                         f'</div>')
        conn.close()
        if not body:
            body = '<p style="color:var(--fg2);font-size:0.85em">데이터 없음</p>'
        summary_cards.append(('📊 연기금 5일 매수', '시총% 정렬', body))
    except Exception:
        summary_cards.append(('📊 연기금 5일', '?', '<p>로드 실패</p>'))

    # 4) 임원·5%↑ 최근 매매 TOP 3
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        cutoff = (datetime.now(KST) - timedelta(days=30)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """SELECT it.rcept_dt, sm.name, it.repror,
                      it.stock_irds_cnt, it.stock_rate
               FROM insider_transactions it
               LEFT JOIN stock_master sm ON sm.symbol = it.symbol
               WHERE it.rcept_dt >= ? AND it.stock_irds_cnt != 0 AND it.stock_rate >= 5
               ORDER BY it.rcept_dt DESC, ABS(it.stock_irds_rate) DESC LIMIT 3""",
            (cutoff,),
        ).fetchall()
        conn.close()
        body = ''
        for r in rows:
            irds = r["stock_irds_cnt"] or 0
            sign = '🟢' if irds > 0 else '🔴'
            color = '#4caf50' if irds > 0 else '#e57373'
            body += (f'<div style="display:flex;justify-content:space-between;'
                     f'padding:3px 0;font-size:0.88em">'
                     f'<span>{sign} {_html.escape(r["name"] or "")}</span>'
                     f'<span style="color:{color}">{irds:+,} ({(r["stock_rate"] or 0):.1f}%)</span>'
                     f'</div>')
        if not body:
            body = '<p style="color:var(--fg2);font-size:0.85em">최근 30일 없음</p>'
        summary_cards.append(('👤 임원·5%↑ 매매', '30일', body))
    except Exception:
        summary_cards.append(('👤 임원 매매', '?', '<p>로드 실패</p>'))

    # 헤더 + 4개 요약 박스 + 전용 페이지 링크
    cards_html = ''
    for title, sub, body in summary_cards:
        cards_html += (
            f'<div style="background:var(--bg2);border:1px solid var(--border);'
            f'border-radius:8px;padding:12px">'
            f'<div style="font-weight:600;margin-bottom:2px">{title}</div>'
            f'<div style="color:var(--fg2);font-size:0.78em;margin-bottom:8px">{sub}</div>'
            f'{body}</div>'
        )
    return (
        f'<h2 style="margin-bottom:6px">🐋 Whale Watch</h2>'
        f'<p style="color:var(--fg2);font-size:0.9em;margin:0 0 12px">'
        f'NPS·연기금·5%↑ 보유자 매매 통합 추적 — '
        f'<a href="/dash/whale" target="_blank" rel="noopener" '
        f'style="color:var(--accent);font-weight:600">전체 보기 ↗</a></p>'
        f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px">'
        f'{cards_html}</div>'
    )


def _build_whale_section_html() -> str:
    """🐋 Whale Watch 섹션 — NPS 5%룰 + 연기금 5일 + 10%룰 (insider 大주주).

    4개 카드:
      Card 1: NPS 5%룰 (분기 보고) — 최근 90일 report_date, 지분율↓ 정렬
      Card 2: 연기금 5일 매수 TOP — 시총% 정규화
      Card 3: 연기금 5일 매도 TOP — 시총% 정규화
      Card 4: 10%룰 임원·주요주주 매매 — 최근 30일, |stock_irds_cnt × stock_rate| 큰 순
    """
    import sqlite3 as _s
    db_path = f"{_DATA_DIR}/stock.db"
    parts = []

    # ── Card 1: NPS 5%룰 ──────────────────────────────────────
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        # 최신 분기 자동 식별
        latest_q_row = conn.execute(
            "SELECT quarter FROM nps_holdings_disclosed WHERE quarter != '' "
            "ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
        latest_q = latest_q_row["quarter"] if latest_q_row else ""
        # 직전 분기도 조회해서 비중 변화 ▲/▼ 산정
        prev_q_row = conn.execute(
            "SELECT DISTINCT quarter FROM nps_holdings_disclosed "
            "WHERE quarter != '' AND quarter < ? ORDER BY quarter DESC LIMIT 1",
            (latest_q,),
        ).fetchone() if latest_q else None
        prev_q = prev_q_row["quarter"] if prev_q_row else ""
        # 직전 분기 데이터: 동일 종목의 max 지분율 (한 분기 내 여러 보고 있을 수 있음)
        prev_map = {}  # symbol → max ratio_pct
        if prev_q:
            for pr in conn.execute(
                """SELECT symbol, MAX(ratio_pct) AS max_r
                   FROM nps_holdings_disclosed WHERE quarter = ? AND symbol != ''
                   GROUP BY symbol""",
                (prev_q,),
            ).fetchall():
                prev_map[pr["symbol"]] = float(pr["max_r"] or 0)
        rows = conn.execute(
            """SELECT report_date, company_name, symbol, ratio_pct
               FROM nps_holdings_disclosed
               WHERE quarter = ?
               ORDER BY ratio_pct DESC, report_date DESC
               LIMIT 30""",
            (latest_q,),
        ).fetchall() if latest_q else []
        conn.close()
        body = ''
        if rows:
            body = ('<table class="whale-tbl"><tr><th>일자</th><th>종목</th>'
                    '<th>지분%</th><th>전분기</th></tr>')
            for r in rows:
                bgs = ''
                if r["ratio_pct"] >= 10:
                    bgs = ' style="color:#e57373;font-weight:600"'  # 10%룰
                # 변화 분석
                cur_r = float(r["ratio_pct"] or 0)
                prev_r = prev_map.get(r["symbol"]) if r["symbol"] else None
                if prev_q and r["symbol"]:
                    if prev_r is None:
                        chg_html = '<span style="color:#4caf50;font-weight:600">🆕 NEW</span>'
                    elif cur_r > prev_r + 0.05:
                        chg_html = (f'<span style="color:#4caf50">▲ '
                                    f'{cur_r - prev_r:+.2f}p</span>')
                    elif cur_r < prev_r - 0.05:
                        chg_html = (f'<span style="color:#e57373">▼ '
                                    f'{cur_r - prev_r:+.2f}p</span>')
                    else:
                        chg_html = '<span style="color:var(--fg2)">—</span>'
                else:
                    chg_html = '<span style="color:var(--fg2)">—</span>'
                body += (f'<tr><td>{_html.escape(r["report_date"])}</td>'
                         f'<td>{_html.escape(r["company_name"])}'
                         f'{(f" ({r["symbol"]})") if r["symbol"] else ""}</td>'
                         f'<td{bgs}>{r["ratio_pct"]:.2f}</td>'
                         f'<td>{chg_html}</td></tr>')
            body += '</table>'
        else:
            body = '<p style="color:var(--fg2)">데이터 없음</p>'
        prev_note = f' | 비교: {prev_q}' if prev_q else ''
        parts.append(
            f'<div class="whale-card"><h3>🏛 NPS 5%룰 ({latest_q or "-"})</h3>'
            f'<p style="color:var(--fg2);font-size:0.85em;margin:0 0 8px">data.go.kr 분기 갱신, 빨강=10%룰{prev_note}</p>'
            f'{body}</div>'
        )
    except Exception as e:
        parts.append(f'<div class="whale-card"><h3>🏛 NPS 5%룰</h3><p>로드 실패: {_html.escape(str(e))}</p></div>')

    # ── Card 2 & 3: 연기금 5일 매수/매도 TOP (시총% 정규화) ──
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        # 최근 5 영업일 산정
        dates = [r["trade_date"] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM pension_flow_daily "
            "ORDER BY trade_date DESC LIMIT 5"
        ).fetchall()]
        if dates:
            placeholders = ",".join("?" for _ in dates)
            agg_rows = conn.execute(
                f"""SELECT pf.symbol, pf.name, pf.market,
                          SUM(pf.net_amount_won) AS net_total,
                          SUM(pf.buy_amount_won) AS buy_total,
                          SUM(pf.sell_amount_won) AS sell_total
                   FROM pension_flow_daily pf
                   WHERE pf.trade_date IN ({placeholders})
                   GROUP BY pf.symbol
                   HAVING net_total != 0""",
                dates,
            ).fetchall()
            # 시총 조회 — 최신 daily_snapshot에서
            symbols = [r["symbol"] for r in agg_rows]
            cap_map = {}
            if symbols:
                ph = ",".join("?" for _ in symbols)
                cap_rows = conn.execute(
                    f"""SELECT symbol, MAX(trade_date) AS d
                        FROM daily_snapshot WHERE symbol IN ({ph})
                        GROUP BY symbol""", symbols
                ).fetchall()
                for cr in cap_rows:
                    cap = conn.execute(
                        "SELECT market_cap FROM daily_snapshot WHERE symbol=? AND trade_date=?",
                        (cr["symbol"], cr["d"])
                    ).fetchone()
                    if cap and cap["market_cap"]:
                        # market_cap 단위 = 억원, net_total = 원
                        cap_map[cr["symbol"]] = int(cap["market_cap"]) * 100_000_000
        else:
            agg_rows = []
            cap_map = {}
        conn.close()

        enriched = []
        for r in agg_rows:
            cap = cap_map.get(r["symbol"], 0)
            pct = (r["net_total"] * 100.0 / cap) if cap > 0 else 0
            enriched.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "market": r["market"],
                "net_won": r["net_total"],
                "cap_won": cap,
                "pct": pct,
            })

        def _row(e):
            sign = '🟢' if e["net_won"] > 0 else '🔴'
            net_eok = e["net_won"] / 100_000_000
            pct_str = f'{e["pct"]:+.2f}%' if e["cap_won"] else '—'
            color = '#4caf50' if e["net_won"] > 0 else '#e57373'
            return (f'<tr><td>{sign} {_html.escape(e["name"])} '
                    f'<span style="color:var(--fg2);font-size:0.8em">{e["symbol"]}</span></td>'
                    f'<td style="color:{color}">{net_eok:+,.0f}억</td>'
                    f'<td style="color:{color};font-weight:600">{pct_str}</td></tr>')

        # 시총% 기준 정렬, 절대% 큰 순. 시총 모르는건 절대금액 fallback
        buy_top = sorted(
            [e for e in enriched if e["net_won"] > 0],
            key=lambda x: (-x["pct"] if x["cap_won"] else 0, -x["net_won"]),
        )[:20]
        sell_top = sorted(
            [e for e in enriched if e["net_won"] < 0],
            key=lambda x: (x["pct"] if x["cap_won"] else 0, x["net_won"]),
        )[:20]

        period = (f"{dates[-1][:4]}-{dates[-1][4:6]}-{dates[-1][6:]} ~ "
                  f"{dates[0][:4]}-{dates[0][4:6]}-{dates[0][6:]}") if dates else "-"

        buy_body = '<table class="whale-tbl"><tr><th>종목</th><th>순매수</th><th>시총%</th></tr>'
        for e in buy_top:
            buy_body += _row(e)
        buy_body += '</table>' if buy_top else '<p style="color:var(--fg2)">매수 없음</p>'
        parts.append(
            f'<div class="whale-card"><h3>🟢 연기금 5일 매수 TOP</h3>'
            f'<p style="color:var(--fg2);font-size:0.85em;margin:0 0 8px">{period} | 시총% 정렬</p>'
            f'{buy_body if buy_top else ""}</div>'
        )
        sell_body = '<table class="whale-tbl"><tr><th>종목</th><th>순매도</th><th>시총%</th></tr>'
        for e in sell_top:
            sell_body += _row(e)
        sell_body += '</table>' if sell_top else '<p style="color:var(--fg2)">매도 없음</p>'
        parts.append(
            f'<div class="whale-card"><h3>🔴 연기금 5일 매도 TOP</h3>'
            f'<p style="color:var(--fg2);font-size:0.85em;margin:0 0 8px">{period} | 시총% 정렬</p>'
            f'{sell_body if sell_top else ""}</div>'
        )
    except Exception as e:
        parts.append(f'<div class="whale-card"><h3>🟢 연기금 5일</h3><p>로드 실패: {_html.escape(str(e))}</p></div>')

    # ── Card 4: 10%룰 임원·주요주주 (insider_transactions) ──
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        cutoff = (datetime.now(KST) - timedelta(days=30)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """SELECT it.rcept_dt, it.symbol, sm.name,
                      it.repror, it.ofcps, it.main_shrholdr,
                      it.stock_irds_cnt, it.stock_rate, it.stock_irds_rate
               FROM insider_transactions it
               LEFT JOIN stock_master sm ON sm.symbol = it.symbol
               WHERE it.rcept_dt >= ?
                 AND it.stock_irds_cnt != 0
                 AND it.stock_rate >= 5  -- 5%룰 이상 보유자만
               ORDER BY it.rcept_dt DESC, ABS(it.stock_irds_rate) DESC
               LIMIT 30""",
            (cutoff,),
        ).fetchall()
        conn.close()
        body = ''
        if rows:
            body = ('<table class="whale-tbl"><tr><th>일자</th><th>종목</th>'
                    '<th>보고자</th><th>증감</th><th>지분%</th></tr>')
            for r in rows:
                irds = r["stock_irds_cnt"] or 0
                sign = '🟢' if irds > 0 else '🔴'
                color = '#4caf50' if irds > 0 else '#e57373'
                rate10 = ' style="color:#e57373;font-weight:600"' if (r["stock_rate"] or 0) >= 10 else ''
                role = (r["main_shrholdr"] or '') or (r["ofcps"] or '')
                body += (f'<tr><td>{_html.escape(r["rcept_dt"])}</td>'
                         f'<td>{_html.escape(r["name"] or "")}'
                         f' <span style="color:var(--fg2);font-size:0.8em">{r["symbol"]}</span></td>'
                         f'<td>{_html.escape(r["repror"] or "")}'
                         f' <span style="color:var(--fg2);font-size:0.78em">{_html.escape(role)}</span></td>'
                         f'<td style="color:{color}">{sign} {irds:+,}</td>'
                         f'<td{rate10}>{(r["stock_rate"] or 0):.2f}%</td></tr>')
            body += '</table>'
        else:
            body = '<p style="color:var(--fg2)">최근 30일 5%↑ 보유자 매매 없음</p>'
        parts.append(
            f'<div class="whale-card"><h3>👤 임원·5%↑ 주주 매매</h3>'
            f'<p style="color:var(--fg2);font-size:0.85em;margin:0 0 8px">DART insider, 30일 / 빨강=10%룰</p>'
            f'{body}</div>'
        )
    except Exception as e:
        parts.append(f'<div class="whale-card"><h3>👤 10%룰</h3><p>로드 실패: {_html.escape(str(e))}</p></div>')

    # ── Card 5: NPS 미국 13F 보유 TOP (가치 + 비중 변화 ▲/▼) ──
    try:
        from kis_api import fetch_nps_us_holdings
        us_data = fetch_nps_us_holdings(top=30, include_changes=True)
        if us_data.get("error"):
            parts.append(
                f'<div class="whale-card"><h3>🇺🇸 NPS 미국 13F</h3>'
                f'<p style="color:var(--fg2)">{_html.escape(us_data["error"])}</p></div>'
            )
        else:
            quarter = us_data.get("quarter", "?")
            period_end = us_data.get("period_end", "?")
            total_v = us_data.get("total_value_usd", 0)
            total_b = total_v / 1e9 if total_v else 0
            n_total = us_data.get("total_holdings", 0)

            body = ('<table class="whale-tbl"><tr><th>종목</th><th>가치</th>'
                    '<th>비중</th><th>주식변화</th></tr>')
            for x in us_data.get("rows", []):
                name = _html.escape((x.get("name_of_issuer") or "")[:28])
                val = x.get("value_usd", 0)
                val_str = f'${val/1e9:.2f}B' if val >= 1e9 else f'${val/1e6:.0f}M'
                weight = x.get("weight_pct", 0)
                status = x.get("status", "")
                sc = x.get("share_change_pct")
                if status == "NEW":
                    sc_html = '<span style="color:#4caf50;font-weight:600">🆕 NEW</span>'
                elif status == "UP":
                    sc_html = f'<span style="color:#4caf50">▲ {sc:+.1f}%</span>' if sc is not None else "▲"
                elif status == "DOWN":
                    sc_html = f'<span style="color:#e57373">▼ {sc:+.1f}%</span>' if sc is not None else "▼"
                else:
                    sc_html = '<span style="color:var(--fg2)">—</span>'
                body += (f'<tr><td>{name}</td>'
                         f'<td>{val_str}</td>'
                         f'<td>{weight:.2f}%</td>'
                         f'<td>{sc_html}</td></tr>')
            body += '</table>'

            # EXIT 종목 표시
            exits_html = ''
            exits = us_data.get("exits_top10", [])
            if exits:
                exits_html = '<details style="margin-top:8px"><summary style="cursor:pointer;color:var(--fg2);font-size:0.85em">전 분기 EXIT TOP 10 ▼</summary><table class="whale-tbl" style="margin-top:6px">'
                exits_html += '<tr><th>종목</th><th>직전 가치</th></tr>'
                for e in exits:
                    val = e.get("prev_value_usd", 0)
                    val_str = f'${val/1e9:.2f}B' if val >= 1e9 else f'${val/1e6:.0f}M'
                    exits_html += (f'<tr><td style="color:#e57373">'
                                   f'{_html.escape((e.get("name_of_issuer") or "")[:28])}</td>'
                                   f'<td>{val_str}</td></tr>')
                exits_html += '</table></details>'

            parts.append(
                f'<div class="whale-card"><h3>🇺🇸 NPS 미국 13F ({quarter})</h3>'
                f'<p style="color:var(--fg2);font-size:0.85em;margin:0 0 8px">'
                f'분기말 {period_end} | 총 ${total_b:.1f}B | {n_total}종목 | TOP 30, ▲▼=주식수 변화</p>'
                f'{body}{exits_html}</div>'
            )
    except Exception as e:
        parts.append(f'<div class="whale-card"><h3>🇺🇸 NPS 미국 13F</h3><p>로드 실패: {_html.escape(str(e))}</p></div>')

    # ── Card 6: NPS 한국 풀 포트 TOP 30 (whale-insight 미러, 200종목) ──
    try:
        from kis_api import fetch_nps_kr_full_holdings
        kr_full = fetch_nps_kr_full_holdings(top=30)
        if kr_full.get("error"):
            parts.append(
                f'<div class="whale-card"><h3>🇰🇷 NPS 한국 풀 포트</h3>'
                f'<p style="color:var(--fg2)">{_html.escape(kr_full["error"])}</p></div>'
            )
        else:
            quarter_lbl = kr_full.get("quarter_label", "?")
            snap = kr_full.get("snapshot_date", "?")
            n_tot = kr_full.get("total_holdings", 0)
            tot_eok = kr_full.get("total_valuation_eok", 0)
            body = ('<table class="whale-tbl"><tr><th>종목</th><th>비중</th>'
                    '<th>평가액</th><th>지분%</th><th>전년대비</th></tr>')
            for x in kr_full.get("rows", []):
                name = _html.escape((x.get("name") or "")[:18])
                sym = x.get("symbol") or ""
                sym_html = (f' <span style="color:var(--fg2);font-size:0.78em">{sym}</span>'
                            if sym else '')
                w = x.get("weight_pct", 0)
                eok = x.get("valuation_eok", 0)
                cur_share = x.get("share_curr_pct", 0)
                # 10%룰 빨강
                share_style = ' style="color:#e57373;font-weight:600"' if cur_share >= 10 else ''
                sc_p = x.get("share_change_p")
                if x.get("data_missing"):
                    sc_html = '<span style="color:var(--fg2)">—</span>'
                elif sc_p is None:
                    sc_html = '<span style="color:var(--fg2)">—</span>'
                elif sc_p > 0.05:
                    sc_html = f'<span style="color:#4caf50">▲ {sc_p:+.2f}p</span>'
                elif sc_p < -0.05:
                    sc_html = f'<span style="color:#e57373">▼ {sc_p:+.2f}p</span>'
                else:
                    sc_html = '<span style="color:var(--fg2)">—</span>'
                body += (f'<tr><td>{name}{sym_html}</td>'
                         f'<td>{w:.2f}%</td>'
                         f'<td>{eok:,}억</td>'
                         f'<td{share_style}>{cur_share:.2f}%</td>'
                         f'<td>{sc_html}</td></tr>')
            body += '</table>'
            parts.append(
                f'<div class="whale-card"><h3>🇰🇷 NPS 한국 풀 포트 ({quarter_lbl})</h3>'
                f'<p style="color:var(--fg2);font-size:0.85em;margin:0 0 8px">'
                f'스냅샷 {snap} | 총 {tot_eok:,}억 | {n_tot}종목 | TOP 30, ▲▼=지분율 전년 대비, '
                f'출처: <a href="https://whale-insight.com" target="_blank" '
                f'style="color:var(--accent)">whale-insight.com</a></p>'
                f'{body}</div>'
            )
    except Exception as e:
        parts.append(f'<div class="whale-card"><h3>🇰🇷 NPS 한국 풀 포트</h3><p>로드 실패: {_html.escape(str(e))}</p></div>')

    return (
        '<style>'
        '.whale-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:14px;margin-top:8px}'
        '.whale-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px}'
        '.whale-card h3{margin:0 0 6px;font-size:1em}'
        '.whale-tbl{width:100%;border-collapse:collapse;font-size:0.85em}'
        '.whale-tbl th{text-align:left;color:var(--fg2);font-weight:500;border-bottom:1px solid var(--border);padding:4px 6px}'
        '.whale-tbl td{padding:4px 6px;border-bottom:1px solid var(--border)}'
        '.whale-tbl tr:last-child td{border-bottom:none}'
        '</style>'
        '<div class="whale-grid">' + ''.join(parts) + '</div>'
    )


async def _handle_dash_v2(request: web.Request) -> web.Response:
    """GET /dash-v2 — 개선된 대시보드 v2."""
    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Stock Bot Dashboard v2</title>{_DASH_V2_CSS}</head><body>')
    html += '<h1>📊 Stock Bot</h1>'
    html += ('<div class="refresh-bar">'
             '<span>갱신: <span id="refresh-time">-</span></span>'
             '<span id="refresh-toggle" class="toggle">⏸ 자동갱신 끄기</span>'
             '</div>')
    html += ('<nav class="tab-nav">'
             '<a href="#portfolio" class="active">💰 포트폴리오</a>'
             '<a href="#events">📅 이벤트</a>'
             '<a href="#watch">👀 감시종목</a>'
             '<a href="/dash/whale" target="_blank" rel="noopener">🐋 Whale ↗</a>'
             '<a href="/v40" target="_blank" rel="noopener">🤖 v40 ↗</a>'
             '<a href="#decision">📝 투자판단</a>'
             '<a href="#trade">💼 매매</a>'
             '<a href="#invest">📈 투자</a>'
             '<a href="#dev">🔧 봇개발</a>'
             '<a href="#reports">📄 리포트</a>'
             '<a href="#docs">📚 문서</a>'
             '</nav>')

    # 1. 포트폴리오
    try:
        html += f'<div class="section" id="portfolio"><h2>💰 포트폴리오</h2>{await _build_portfolio_v2_html()}</div>'
    except Exception:
        html += '<div class="section" id="portfolio"><h2>💰 포트폴리오</h2><p>로드 실패</p></div>'

    # 2. 이벤트
    try:
        html += f'<div class="section" id="events"><h2>📅 이벤트</h2>{_build_events_v2_html()}</div>'
    except Exception:
        html += '<div class="section" id="events"><h2>📅 이벤트</h2><p>로드 실패</p></div>'

    # 3. 감시종목
    try:
        html += f'<div class="section" id="watch"><h2>👀 감시종목</h2>{_build_watchalert_v2_html()}</div>'
    except Exception:
        html += '<div class="section" id="watch"><h2>👀 감시종목</h2><p>로드 실패</p></div>'

    # 3.5 Whale Watch — 요약 박스 (전용 페이지 링크)
    try:
        html += f'<div class="section" id="whale">{_build_whale_summary_html()}</div>'
    except Exception as _e:
        html += f'<div class="section" id="whale"><h2>🐋 Whale Watch</h2><p>로드 실패: {_html.escape(str(_e))}</p></div>'

    # 4. 투자판단
    try:
        dl = load_json(f"{_DATA_DIR}/decision_log.json", {})
        total_decisions = len(dl) if dl else 0
        cards_html = ""
        if dl:
            recent = sorted(dl.items(), key=lambda x: x[0], reverse=True)[:5]
            for idx, (date, entry) in enumerate(recent):
                regime_raw = str(entry.get("regime", "?"))
                regime_esc = _html.escape(regime_raw)
                # 레짐 뱃지 클래스
                if "강세" in regime_raw or "bull" in regime_raw.lower():
                    badge_cls = "badge-bull"
                elif "약세" in regime_raw or "bear" in regime_raw.lower():
                    badge_cls = "badge-bear"
                else:
                    badge_cls = "badge-neutral"
                # 액션 목록
                actions_list = entry.get("actions", [])
                if not actions_list and entry.get("summary"):
                    actions_list = [str(entry["summary"])]
                # 프리뷰: 첫 액션 축약
                preview_txt = _html.escape((actions_list[0] if actions_list else "")[:60])
                # 액션 li
                actions_html = ""
                for act in actions_list:
                    actions_html += f"<li>{_html.escape(str(act))}</li>"
                actions_block = f'<ul class="decision-actions">{actions_html}</ul>' if actions_html else ""
                # notes
                notes_raw = entry.get("notes", "")
                notes_block = (f'<div class="decision-notes">{_html.escape(str(notes_raw))}</div>'
                               if notes_raw else "")
                # grades
                grades = entry.get("grades", {})
                grades_lines = ""
                if isinstance(grades, dict):
                    for ticker, ginfo in grades.items():
                        if isinstance(ginfo, dict):
                            g = _html.escape(str(ginfo.get("grade", "")))
                            reason = _html.escape(str(ginfo.get("reason", "")))
                            grades_lines += f'<div><strong>{_html.escape(ticker)}</strong>: <span class="badge badge-{g}">{g}</span> {reason}</div>'
                        else:
                            grades_lines += f'<div><strong>{_html.escape(ticker)}</strong>: {_html.escape(str(ginfo))}</div>'
                grades_block = f'<div class="decision-grades">{grades_lines}</div>' if grades_lines else ""
                open_attr = " open" if idx == 0 else ""
                cards_html += (
                    f'<details class="decision-card"{open_attr}>'
                    f'<summary>'
                    f'<span class="decision-date">{_html.escape(date)}</span>'
                    f'<span class="badge {badge_cls}">{regime_esc}</span>'
                    f'<span class="decision-preview">{preview_txt}</span>'
                    f'</summary>'
                    f'<div class="decision-body">'
                    f'{actions_block}'
                    f'{notes_block}'
                    f'{grades_block}'
                    f'</div>'
                    f'</details>'
                )

        # 투자판단 작성 폼 (날짜 기본값: 오늘 KST)
        _today_kst = datetime.now(KST).strftime("%Y-%m-%d")
        decision_form = (
            f'<details class="decision-new" style="margin:12px 0;background:var(--bg);padding:12px;border-radius:6px;border:1px dashed var(--accent)">'
            f'<summary style="cursor:pointer;color:var(--accent);font-weight:600">➕ 새 투자판단 기록</summary>'
            f'<form id="decision-form" style="margin-top:12px;display:flex;flex-direction:column;gap:10px">'
            f'<label>날짜 <input type="date" name="date" value="{_today_kst}" required></label>'
            f'<label>레짐 '
            f'<select name="regime" required>'
            f'<option value="🟢 공격">🟢 공격</option>'
            f'<option value="🟡 경계">🟡 경계</option>'
            f'<option value="🔴 위기">🔴 위기</option>'
            f'</select>'
            f'</label>'
            f'<label>메모 (notes)'
            f'<textarea name="notes" rows="3" maxlength="5000" placeholder="오늘 시장 관찰, 포지션 조정 근거..." '
            f'style="width:100%;padding:8px;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:4px"></textarea>'
            f'</label>'
            f'<label>액션 (한 줄에 하나씩)'
            f'<textarea name="actions" rows="3" maxlength="5000" placeholder="HD현대일렉 1주 추가 매수&#10;삼성전자 감시가 72000 → 70000 하향" '
            f'style="width:100%;padding:8px;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:4px"></textarea>'
            f'</label>'
            f'<label>등급 (티커:등급:이유, 한 줄에 하나씩)'
            f'<textarea name="grades" rows="3" maxlength="5000" placeholder="005930:A:thesis 유효&#10;066570:B+:실적 개선" '
            f'style="width:100%;padding:8px;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:4px"></textarea>'
            f'</label>'
            f'<button type="submit" style="padding:8px 16px;background:var(--accent);color:#000;border:none;border-radius:4px;cursor:pointer;align-self:flex-start;font-weight:600">저장</button>'
            f'</form>'
            f'</details>'
        )

        html += (f'<div class="section" id="decision">'
                 f'<div style="display:flex;justify-content:space-between;align-items:center">'
                 f'<h2 style="margin:0">📝 최근 투자판단</h2>'
                 f'<a href="/dash/decisions" style="color:var(--accent);text-decoration:none;font-size:0.85em">'
                 f'전체 {total_decisions}건 보기 →</a>'
                 f'</div>'
                 f'{decision_form}'
                 f'{cards_html}'
                 f'</div>')
    except Exception:
        pass

    # 5. 매매기록
    try:
        tl = load_json(f"{_DATA_DIR}/trade_log.json", [])
        trades = tl if isinstance(tl, list) else tl.get("trades", [])
        if trades:
            total_trades = len(trades)
            recent_t = list(reversed(trades))[:5]
            trade_cards = ""
            for idx, t in enumerate(recent_t):
                trade_cards += _build_trade_card(t, is_open=(idx == 0))
            html += (f'<div class="section" id="trade">'
                     f'<div style="display:flex;justify-content:space-between;align-items:center">'
                     f'<h2 style="margin:0">💼 최근 매매</h2>'
                     f'<a href="/dash/trades" style="color:var(--accent);text-decoration:none;font-size:0.85em">'
                     f'전체 {total_trades}건 보기 →</a>'
                     f'</div>'
                     f'{trade_cards}'
                     f'</div>')
    except Exception:
        pass

    # 6. 투자 TODO (체크박스 토글 + 항목 추가)
    try:
        invest_path = os.path.join(_DATA_DIR, "TODO_invest.md")
        if os.path.exists(invest_path):
            with open(invest_path, encoding="utf-8") as f:
                _invest_md = f.read()
            html += (
                f'<div class="section" id="invest"><h2>📈 투자</h2>'
                f'{_md_to_html_editable(_invest_md, "invest")}'
                f'<details class="todo-add" style="margin-top:16px;background:var(--bg);padding:12px;border-radius:6px;border:1px dashed var(--border)">'
                f'<summary style="cursor:pointer;color:var(--accent);font-size:0.9em">➕ 항목 추가</summary>'
                f'<form class="todo-add-form" data-file="invest" style="margin-top:12px;display:flex;flex-direction:column;gap:8px">'
                f'<input type="text" name="text" placeholder="새 TODO 항목..." required maxlength="500" '
                f'style="padding:8px;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:4px">'
                f'<button type="submit" style="padding:6px 12px;background:var(--accent);color:#000;border:none;border-radius:4px;cursor:pointer;align-self:flex-start">추가</button>'
                f'</form>'
                f'</details>'
                f'</div>'
            )
    except Exception:
        pass

    # 6b. 봇개발 TODO (체크박스 토글 + 항목 추가)
    try:
        dev_path = os.path.join(_DATA_DIR, "TODO_dev.md")
        if os.path.exists(dev_path):
            with open(dev_path, encoding="utf-8") as f:
                _dev_md = f.read()
            html += (
                f'<div class="section" id="dev"><h2>🔧 봇개발</h2>'
                f'{_md_to_html_editable(_dev_md, "dev")}'
                f'<details class="todo-add" style="margin-top:16px;background:var(--bg);padding:12px;border-radius:6px;border:1px dashed var(--border)">'
                f'<summary style="cursor:pointer;color:var(--accent);font-size:0.9em">➕ 항목 추가</summary>'
                f'<form class="todo-add-form" data-file="dev" style="margin-top:12px;display:flex;flex-direction:column;gap:8px">'
                f'<input type="text" name="text" placeholder="새 TODO 항목..." required maxlength="500" '
                f'style="padding:8px;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:4px">'
                f'<button type="submit" style="padding:6px 12px;background:var(--accent);color:#000;border:none;border-radius:4px;cursor:pointer;align-self:flex-start">추가</button>'
                f'</form>'
                f'</details>'
                f'</div>'
            )
    except Exception:
        pass

    # 7. 리포트
    try:
        import sqlite3 as _sqlite3_rpt
        rpt_conn = _sqlite3_rpt.connect(REPORT_DB_PATH, timeout=10)
        rpt_conn.execute("PRAGMA cache_size = -65536;")
        rpt_conn.execute("PRAGMA temp_store = MEMORY;")
        rpt_conn.execute("PRAGMA mmap_size = 268435456;")
        rpt_conn.execute("PRAGMA busy_timeout = 30000;")
        rpt_conn.row_factory = _sqlite3_rpt.Row
        ticker_counts = rpt_conn.execute("""
            SELECT ticker, name, COUNT(*) as cnt, MAX(date) as latest
            FROM reports GROUP BY ticker ORDER BY cnt DESC
        """).fetchall()
        rpt_conn.close()
        html += '<div class="section" id="reports"><h2>📄 리포트</h2>'
        if ticker_counts:
            html += '<div class="doc-grid">'
            for tc in ticker_counts:
                html += (f'<a href="/dash/reports/{_html.escape(tc["ticker"])}" class="doc-card">'
                         f'<div class="doc-icon">📄</div>'
                         f'<div class="doc-name">{_html.escape(tc["name"])}</div>'
                         f'<div class="doc-desc">{tc["cnt"]}건 | 최신 {_html.escape(tc["latest"])}</div>'
                         f'</a>')
            html += '</div>'
        else:
            html += '<p style="color:var(--fg2)">리포트 없음</p>'
        html += '</div>'
    except Exception:
        pass

    # 8. 문서
    try:
        html += f'<div class="section" id="docs"><h2>📚 문서</h2>{_build_docs_v2_html()}</div>'
    except Exception:
        html += '<div class="section" id="docs"><h2>📚 문서</h2><p>로드 실패</p></div>'

    html += _dash_v2_js()
    html += "</body></html>"
    return web.Response(text=html, content_type="text/html")


async def _handle_dash_research_file(request: web.Request) -> web.Response:
    """GET /dash/file/research/{TICKER}/{filename} 또는 /dash/file/thesis/{filename}.
    research는 2단계 (TICKER 디렉토리), thesis는 flat."""
    try:
        filename = request.match_info.get("filename", "")
        subdir = "thesis" if "/thesis/" in request.path else "research"
        # research만 1회 "/" 허용 (TICKER/file 형식). 나머지 path traversal 방어.
        if ".." in filename or "\\" in filename:
            return web.Response(text="Forbidden", status=403)
        max_slashes = 1 if subdir == "research" else 0
        if filename.count("/") > max_slashes:
            return web.Response(text="Forbidden", status=403)
        if filename.endswith((".py", ".env", ".sh")):
            return web.Response(text="Forbidden", status=403)

        filepath = os.path.join(_DATA_DIR, subdir, filename)
        # realpath 검증: 최종 경로가 subdir 하위여야 함 (심볼릭 링크 등 방어)
        real_base = os.path.realpath(os.path.join(_DATA_DIR, subdir))
        real_target = os.path.realpath(filepath)
        if not real_target.startswith(real_base + os.sep):
            return web.Response(text="Forbidden", status=403)
        if not os.path.isfile(filepath):
            return web.Response(text="Not Found", status=404)
        if os.path.getsize(filepath) > 500 * 1024:
            return web.Response(text="File too large", status=413)

        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{filename}</title>{_DASH_CSS}</head><body>'
                f'<div class="nav"><a href="/dash-v2">← 대시보드 v2</a></div>'
                f'<h1>{filename}</h1>')

        if filename.endswith(".md") or filename.endswith(".txt"):
            html += _md_to_html(content)
        elif filename.endswith(".json"):
            try:
                html += _json_to_table(json.loads(content))
            except Exception:
                html += f"<pre>{_html.escape(content[:10000])}</pre>"
        else:
            html += f"<pre>{_html.escape(content[:10000])}</pre>"

        html += "</body></html>"
        return web.Response(text=html, content_type="text/html")
    except Exception as e:
        import traceback
        print(f"[Dash] research file 오류: {e}\n{traceback.format_exc()}")
        return web.Response(text=f"Error: {e}", status=500)


async def _handle_dash_reports(request: web.Request) -> web.Response:
    """GET /dash/reports/{ticker} — 종목별 리포트 목록."""
    ticker = request.match_info.get("ticker", "")
    if ".." in ticker or "/" in ticker or "\\" in ticker:
        return web.Response(status=400, text="Invalid ticker")

    import sqlite3 as _sqlite3_rpt2
    try:
        conn = _sqlite3_rpt2.connect(REPORT_DB_PATH, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _sqlite3_rpt2.Row
        rows = conn.execute("""
            SELECT date, source, analyst, title, pdf_path, extraction_status,
                   COALESCE(target_price, 0) AS target_price,
                   COALESCE(opinion, '') AS opinion
            FROM reports WHERE ticker=? ORDER BY date DESC
        """, (ticker,)).fetchall()
        name_row = conn.execute(
            "SELECT name FROM reports WHERE ticker=? LIMIT 1", (ticker,)
        ).fetchone()
        name = name_row["name"] if name_row else ticker
        conn.close()
    except Exception as e:
        return web.Response(status=500, text=f"DB 오류: {e}")

    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_html.escape(name)} 리포트</title>{_DASH_V2_CSS}'
            f'<style>'
            f'.rpt-wrap{{overflow-x:auto;-webkit-overflow-scrolling:touch}}'
            f'.rpt-table{{width:100%;border-collapse:collapse;font-size:0.88em}}'
            f'.rpt-table th{{background:var(--card);color:var(--fg2);font-weight:600;'
            f'padding:8px 10px;border-bottom:1px solid var(--border);white-space:nowrap;text-align:left}}'
            f'.rpt-table td{{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:middle}}'
            f'.rpt-table tr:hover td{{background:var(--card)}}'
            f'.rpt-date{{white-space:nowrap;color:var(--fg2);font-size:0.85em}}'
            f'.rpt-title{{max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}'
            f'.op-buy{{color:var(--green)}}.op-sell{{color:var(--red)}}.op-neutral{{color:var(--fg2)}}'
            f'</style>'
            f'</head><body>')
    html += (f'<div style="margin-bottom:16px">'
             f'<a href="/dash#reports" style="color:var(--accent);text-decoration:none">← 대시보드</a>'
             f'</div>')
    html += (f'<h1>📄 {_html.escape(name)} ({_html.escape(ticker)}) '
             f'리포트 ({len(rows)}건)</h1>')

    if not rows:
        html += '<p style="color:var(--fg2)">리포트 없음</p>'
    else:
        html += '<div class="rpt-wrap"><table class="rpt-table">'
        html += ('<thead><tr>'
                 '<th>날짜</th><th>증권사</th><th>애널리스트</th>'
                 '<th>제목</th><th>목표가</th><th>투자의견</th><th>PDF</th>'
                 '</tr></thead><tbody>')

        for r in rows:
            date = _html.escape(r["date"] or "")
            source = _html.escape(r["source"] or "")
            analyst = _html.escape(r["analyst"] or "")
            title = _html.escape(r["title"] or "")
            pdf_path = r["pdf_path"] or ""
            target_price = r["target_price"] or 0
            opinion = r["opinion"] or ""

            # 목표가 셀
            tp_cell = f'🎯 {target_price:,}원' if target_price else '<span style="color:var(--fg2)">—</span>'

            # 투자의견 셀
            if opinion == "매수":
                op_cell = f'<span class="op-buy">{_html.escape(opinion)}</span>'
            elif opinion == "매도":
                op_cell = f'<span class="op-sell">{_html.escape(opinion)}</span>'
            elif opinion:
                op_cell = f'<span class="op-neutral">{_html.escape(opinion)}</span>'
            else:
                op_cell = '<span style="color:var(--fg2)">—</span>'

            # PDF 셀
            if pdf_path:
                fname = os.path.basename(pdf_path)
                pdf_cell = (f'<a href="/dash/pdf/{_html.escape(ticker)}/{_html.escape(fname)}" '
                            f'target="_blank" style="color:var(--accent);text-decoration:none">PDF</a>')
            else:
                pdf_cell = '<span style="color:var(--fg2)">—</span>'

            html += (f'<tr>'
                     f'<td class="rpt-date">{date}</td>'
                     f'<td>{source}</td>'
                     f'<td style="color:var(--fg2);font-size:0.85em">{analyst}</td>'
                     f'<td class="rpt-title" title="{title}">{title}</td>'
                     f'<td style="white-space:nowrap">{tp_cell}</td>'
                     f'<td>{op_cell}</td>'
                     f'<td>{pdf_cell}</td>'
                     f'</tr>')

        html += '</tbody></table></div>'

    html += "</body></html>"
    return web.Response(text=html, content_type="text/html")


async def _handle_dash_pdf(request: web.Request) -> web.Response:
    """GET /dash/pdf/{ticker}/{filename} — PDF 파일 직접 서빙."""
    ticker = request.match_info.get("ticker", "")
    filename = request.match_info.get("filename", "")

    # 보안: path traversal 방지
    if ".." in ticker or "/" in ticker or "\\" in ticker:
        return web.Response(status=400, text="Invalid ticker")
    if ".." in filename or "/" in filename or "\\" in filename:
        return web.Response(status=400, text="Invalid filename")
    if not filename.lower().endswith(".pdf"):
        return web.Response(status=400, text="PDF only")

    pdf_dir = os.path.join(os.environ.get("DATA_DIR", "data"), "report_pdfs")
    fpath = os.path.join(pdf_dir, ticker, filename)

    if not os.path.isfile(fpath):
        return web.Response(status=404, text="PDF not found")

    with open(fpath, "rb") as f:
        content = f.read()
    return web.Response(body=content, content_type="application/pdf")


def _build_trade_card(t: dict, is_open: bool = False) -> str:
    """trade_log 1건 → details 카드 HTML."""
    ticker = t.get("ticker", "")
    is_us = bool(ticker) and not ticker.isdigit()
    side_cls = "badge-buy" if t.get("side") == "buy" else "badge-sell"
    side_txt = "매수" if t.get("side") == "buy" else "매도"
    price = t.get("price", 0)
    try:
        price_str = f"${float(price):,.2f}" if is_us else f"{int(price):,}원"
    except (TypeError, ValueError):
        price_str = str(price)
    qty = t.get("qty", 0)
    name = _html.escape(str(t.get("name", ticker)))
    date = _html.escape(str(t.get("date", "?")))
    grade = _html.escape(str(t.get("grade_at_trade", "")))
    reason = _html.escape(str(t.get("reason", "")))
    target = t.get("target_price", 0)
    stop = t.get("stop_price", 0)

    open_attr = " open" if is_open else ""

    # grade 뱃지
    grade_key = grade.replace("+", "p").replace("-", "m")
    grade_html = f'<span class="badge badge-{grade_key}">{grade}</span>' if grade else ""

    # 목표/손절 메타
    meta_parts = []
    if grade:
        meta_parts.append(f"등급: {grade_html}")
    if target:
        try:
            t_str = f"${float(target):,.2f}" if is_us else f"{int(target):,}원"
        except (TypeError, ValueError):
            t_str = str(target)
        meta_parts.append(f"목표: {t_str}")
    if stop:
        try:
            s_str = f"${float(stop):,.2f}" if is_us else f"{int(stop):,}원"
        except (TypeError, ValueError):
            s_str = str(stop)
        meta_parts.append(f"손절: {s_str}")
    meta_html = (f'<div style="font-size:0.85em;margin-bottom:6px">'
                 f'{" | ".join(meta_parts)}</div>') if meta_parts else ""

    reason_html = f'<div class="decision-notes">{reason}</div>' if reason else ""

    return (f'<details class="decision-card"{open_attr}><summary>'
            f'<span class="decision-date">{date}</span>'
            f'<span class="badge {side_cls}">{side_txt}</span>'
            f'<span style="font-weight:600">{name}</span>'
            f'<span style="color:var(--fg2);font-size:0.85em">{price_str} × {qty}</span>'
            f'</summary><div class="decision-body">'
            f'{meta_html}{reason_html}'
            f'</div></details>')


async def _handle_dash_whale(request: web.Request) -> web.Response:
    """GET /dash/whale — 🐋 Whale Watch (whale-insight 디자인 미러).

    Tailwind CDN + Pretendard + Lucide icons, 라이트 모드, 모바일 우선.
    """
    page = (page_name := request.query.get("p", "home"))
    if page == "kr_full":
        body = _whale_render_kr_full()
        title = "NPS 한국 풀 포트"
    elif page == "us_13f":
        body = _whale_render_us_13f()
        title = "NPS 미국 13F"
    elif page == "kr_5pct":
        body = _whale_render_kr_5pct()
        title = "NPS 한국 5%룰"
    elif page == "pension":
        body = _whale_render_pension_flow()
        title = "연기금 5일 흐름"
    elif page == "insider":
        body = _whale_render_insider()
        title = "임원·5%↑ 매매"
    else:
        body = _whale_render_home()
        title = "Whale Watch"

    html = f'''<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>🐋 {_html.escape(title)} | Whale Watch</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/lucide@latest"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Pretendard:wght@400;600;800&display=swap');
body{{font-family:'Pretendard',sans-serif;background-color:#f8fafc;}}
.whale-card{{transition:all 0.2s ease;border:1px solid #f1f5f9;background-color:#ffffff;}}
.whale-card:hover{{transform:translateY(-3px);border-color:#3b82f6;box-shadow:0 10px 15px -3px rgba(0,0,0,0.05);}}
.trade-card{{background:#ffffff;border-left:4px solid #3b82f6;transition:transform 0.1s;}}
.trade-card:active{{transform:scale(0.98);}}
.hide-scrollbar::-webkit-scrollbar{{display:none;}}
.hide-scrollbar{{-ms-overflow-style:none;scrollbar-width:none;}}
.tabular-nums{{font-variant-numeric:tabular-nums;}}
.sticky-name{{position:sticky;left:0;z-index:20;background-color:#fff !important;box-shadow:4px 0 8px -4px rgba(0,0,0,0.1);white-space:normal;word-break:break-all;max-width:120px;}}
thead th.sticky-name{{background-color:#f8fafc !important;z-index:30;}}
section{{scroll-margin-top:80px;}}
</style>
</head>
<body class="text-slate-900 pb-10">

<header class="h-14 bg-white/80 backdrop-blur-md border-b border-slate-200 sticky top-0 z-50 px-4 flex items-center justify-between">
    <button onclick="location.href='/dash/whale'" class="flex items-center gap-1 text-slate-500 font-bold text-sm">
        <i data-lucide="chevron-left" class="w-5 h-5"></i> {("Whale" if page != "home" else "")}
    </button>
    <h2 class="text-base font-extrabold text-slate-800">🐋 {_html.escape(title)}</h2>
    <a href="/dash" class="text-[11px] font-bold text-slate-400 hover:text-blue-600">메인 ↗</a>
</header>

<main class="max-w-screen-md mx-auto p-4 space-y-6">
{body}

<div class="mt-8 text-center">
    <p class="text-[10px] text-slate-400 font-medium tracking-tight uppercase">
        Stock Bot Whale Watch • Mirror of <a href="https://whale-insight.com" target="_blank" class="text-blue-500 hover:underline">whale-insight.com</a>
    </p>
</div>
</main>

<script>lucide.createIcons();</script>
</body></html>'''
    return web.Response(text=html, content_type="text/html")


def _whale_render_home() -> str:
    """Whale 홈 — whale-insight 메인 페이지 미러 (NPS 카드 2개 + 최근 알림 2개 + 5%룰)."""
    import sqlite3 as _s
    db_path = f"{_DATA_DIR}/stock.db"

    # NPS 5%룰 / 10%↑ 카운트 (NPS 단독, 최신 분기)
    total_5pct = 0
    total_10pct = 0
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        latest_q_row = conn.execute(
            "SELECT quarter FROM nps_holdings_disclosed WHERE quarter != '' "
            "ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
        if latest_q_row:
            lq = latest_q_row["quarter"]
            total_5pct = conn.execute(
                "SELECT COUNT(*) AS n FROM nps_holdings_disclosed WHERE quarter=?", (lq,)
            ).fetchone()["n"]
            total_10pct = conn.execute(
                "SELECT COUNT(*) AS n FROM nps_holdings_disclosed "
                "WHERE quarter=? AND ratio_pct >= 10", (lq,)
            ).fetchone()["n"]
        conn.close()
    except Exception:
        pass
    recent_5pct = total_5pct
    recent_10pct = total_10pct

    return f'''
    <section class="space-y-3">
        <div class="px-1">
            <h4 class="text-xl font-black text-slate-900 tracking-tight">지금 국민연금은 무엇을 사고 있을까?</h4>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div onclick="location.href='/dash/whale?p=kr_5pct'"
                 class="trade-card p-5 rounded-2xl shadow-sm cursor-pointer hover:bg-blue-50/50 transition-all border border-slate-100">
                <div class="flex justify-between items-center mb-2">
                    <span class="text-[10px] font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded-full border border-blue-100">지분 5%↑</span>
                    <i data-lucide="arrow-up-right" class="w-4 h-4 text-blue-400"></i>
                </div>
                <h5 class="font-extrabold text-slate-900 text-base">NPS 5%↑ 보유 종목</h5>
                <p class="text-[11px] text-slate-500 leading-relaxed mt-1">
                    <span class="font-bold text-blue-600">{recent_5pct}건</span> · 국민연금 5%↑ 지분 보고
                </p>
            </div>
            <div onclick="location.href='/dash/whale?p=insider'"
                 class="trade-card p-5 rounded-2xl shadow-sm cursor-pointer hover:bg-indigo-50/50 transition-all border border-slate-100"
                 style="border-left-color: #6366f1;">
                <div class="flex justify-between items-center mb-2">
                    <span class="text-[10px] font-bold text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-full border border-indigo-100">지분 10%↑</span>
                    <i data-lucide="shield-check" class="w-4 h-4 text-indigo-400"></i>
                </div>
                <h5 class="font-extrabold text-slate-900 text-base">NPS 10%↑ 핵심 보유</h5>
                <p class="text-[11px] text-slate-500 leading-relaxed mt-1">
                    <span class="font-bold text-indigo-600">{recent_10pct}건</span> · 국민연금 10%↑ 핵심 종목
                </p>
            </div>
        </div>
    </section>

    <section class="space-y-3">
        <div class="px-1">
            <h4 class="text-lg font-black text-slate-800">국민연금 포트폴리오</h4>
            <p class="text-slate-500 text-[11px] font-medium">자산 규모 1,000조, 거대 자본이 선택한 핵심 우량주</p>
        </div>
        <div class="grid grid-cols-2 gap-3">
            <div onclick="location.href='/dash/whale?p=kr_full'"
                 class="bg-slate-900 p-6 rounded-3xl cursor-pointer text-white shadow-xl relative overflow-hidden group">
                <span class="text-[9px] bg-white/10 px-2 py-0.5 rounded-full font-bold mb-3 inline-block border border-white/10">KOSPI &amp; KOSDAQ</span>
                <h3 class="text-lg font-black mb-0.5">국내 포트폴리오</h3>
                <span class="text-[10px] text-slate-400 font-bold opacity-80 leading-none">상위 200개 종목 (분기별)</span>
                <i data-lucide="trending-up" class="absolute -bottom-2 -right-2 w-16 h-16 text-white/5 transition-transform group-hover:scale-110"></i>
            </div>
            <div onclick="location.href='/dash/whale?p=us_13f'"
                 class="bg-blue-700 p-6 rounded-3xl cursor-pointer text-white shadow-xl relative overflow-hidden group">
                <span class="text-[9px] bg-white/10 px-2 py-0.5 rounded-full font-bold mb-3 inline-block border border-white/10">NASDAQ &amp; NYSE</span>
                <h3 class="text-lg font-black mb-0.5">해외 포트폴리오</h3>
                <span class="text-[10px] text-slate-300 font-bold opacity-80 leading-none">SEC 13F-HR 풀 561종목</span>
                <i data-lucide="globe" class="absolute -bottom-2 -right-2 w-16 h-16 text-white/5 transition-transform group-hover:rotate-12"></i>
            </div>
        </div>
    </section>

    <section class="space-y-3">
        <div class="px-1">
            <h4 class="text-lg font-black text-slate-800">단기 매매 흐름</h4>
            <p class="text-slate-500 text-[11px] font-medium">5일 누적 시총% 기준 매수/매도 시그널</p>
        </div>
        <div onclick="location.href='/dash/whale?p=pension'"
             class="whale-card p-5 rounded-2xl shadow-sm cursor-pointer flex items-center justify-between">
            <div class="flex flex-col">
                <span class="text-[9px] font-black text-emerald-600 mb-0.5 bg-emerald-50 px-2 py-0.5 rounded-full w-fit border border-emerald-100">pykrx</span>
                <h5 class="text-base font-black text-slate-900">연기금 5일 매수/매도</h5>
                <p class="text-[11px] text-slate-500 mt-0.5">매일 16:30 자동 수집 · 시총% 정렬</p>
            </div>
            <div class="bg-slate-50 p-2.5 rounded-xl text-slate-300"><i data-lucide="chevron-right" class="w-4 h-4"></i></div>
        </div>
    </section>
    '''


def _whale_render_kr_full() -> str:
    """NPS 한국 풀 포트 200종목 — whale-insight nps_kr.html 완전 미러."""
    try:
        from kis_api import fetch_nps_kr_full_holdings
        data = fetch_nps_kr_full_holdings(top=200)
    except Exception as e:
        return f'<div class="p-4 bg-red-50 text-red-600 rounded-xl">로드 실패: {_html.escape(str(e))}</div>'
    if data.get("error"):
        return f'<div class="p-4 bg-amber-50 text-amber-700 rounded-xl">{_html.escape(data["error"])}</div>'

    rows_html = ''
    for idx, x in enumerate(data.get("rows", []), start=1):
        name = _html.escape((x.get("name") or "")[:24])
        sym = x.get("symbol") or ""
        weight = x.get("weight_pct", 0)
        share_curr = x.get("share_curr_pct", 0)
        share_prev = x.get("share_prev_pct", 0)
        sc_p = x.get("share_change_p")
        # whale-insight: ▲ 빨강(red-600), ▼ 파랑(blue-600). 한국 관습.
        if x.get("data_missing") or sc_p is None:
            arrow_html = '<span class="text-slate-300">—</span>'
        elif sc_p > 0.05:
            arrow_html = f'<span class="text-red-600 font-black"><span class="text-[10px] mr-0.5">▲</span>{abs(sc_p):.2f}%p</span>'
        elif sc_p < -0.05:
            arrow_html = f'<span class="text-blue-600 font-black"><span class="text-[10px] mr-0.5">▼</span>{abs(sc_p):.2f}%p</span>'
        else:
            arrow_html = '<span class="text-slate-400">—</span>'

        sym_lbl = (f' <span class="text-[10px] text-slate-400">{sym}</span>'
                   if sym else '')
        rows_html += f'''
        <tr class="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
            <td class="py-4 px-3 text-slate-300 text-center font-bold text-[10px]">{str(idx).zfill(2)}</td>
            <td class="py-4 px-3 font-bold text-slate-800 text-left sticky-name">{name}{sym_lbl}</td>
            <td class="py-4 px-2 text-center text-slate-500 tabular-nums text-[11px]">{weight:.2f}%</td>
            <td class="py-4 px-2 text-center tabular-nums bg-blue-50/10">
                <div class="text-[12px] font-black text-slate-900">{share_curr:.2f}%</div>
                <div class="text-[9px] text-slate-400 font-medium">전년: {share_prev:.2f}%</div>
            </td>
            <td class="py-4 px-4 text-center tabular-nums">{arrow_html}</td>
        </tr>'''

    quarter = data.get("quarter_label", "?")
    snap = data.get("snapshot_date", "?")
    n_tot = data.get("total_holdings", 0)
    tot_eok = data.get("total_valuation_eok", 0)

    return f'''
    <div class="px-4 py-3 bg-blue-50/50 border border-blue-100 rounded-2xl">
        <p class="text-[11px] text-blue-700 leading-relaxed font-medium">
            <i data-lucide="alert-circle" class="w-3 h-3 inline-block mr-1 -mt-0.5"></i>
            사업보고서 + 5%룰 기반 지분율. 자동화 과정에서 일부 오차 가능. <br>
            데이터 출처: <a href="https://whale-insight.com" target="_blank" class="font-bold underline">whale-insight.com</a> · 스냅샷 {snap}
        </p>
    </div>

    <div class="grid grid-cols-3 gap-2">
        <div class="bg-white rounded-xl border border-slate-100 p-3 text-center">
            <div class="text-[10px] text-slate-400 font-bold">분기</div>
            <div class="text-base font-black text-slate-900">{quarter}</div>
        </div>
        <div class="bg-white rounded-xl border border-slate-100 p-3 text-center">
            <div class="text-[10px] text-slate-400 font-bold">종목</div>
            <div class="text-base font-black text-slate-900">{n_tot}</div>
        </div>
        <div class="bg-white rounded-xl border border-slate-100 p-3 text-center">
            <div class="text-[10px] text-slate-400 font-bold">총 평가액</div>
            <div class="text-base font-black text-slate-900">{tot_eok:,}억</div>
        </div>
    </div>

    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
        <div class="overflow-x-auto hide-scrollbar">
            <table class="w-full text-left border-collapse min-w-full">
                <thead>
                    <tr class="bg-slate-50 border-b border-slate-200 text-slate-500">
                        <th class="py-3 px-3 font-bold text-[10px] text-center w-10">#</th>
                        <th class="py-3 px-3 font-bold text-[10px] text-left sticky-name">종목명</th>
                        <th class="py-3 px-2 font-bold text-[10px] text-center">
                            <div class="flex flex-col items-center justify-center"><span>비중</span><span class="text-[9px] font-medium opacity-80">({quarter})</span></div>
                        </th>
                        <th class="py-2 px-2 font-bold text-[10px] text-center bg-blue-50/50 text-blue-600">
                            <div class="flex flex-col items-center justify-center"><span>지분율</span><span class="text-[9px] font-medium opacity-80">({quarter})</span></div>
                        </th>
                        <th class="py-3 px-4 font-bold text-[10px] text-center">변동</th>
                    </tr>
                </thead>
                <tbody class="text-[11px]">{rows_html}</tbody>
            </table>
        </div>
    </div>

    <div class="p-4 bg-slate-100 rounded-2xl border border-slate-200">
        <div class="flex items-center gap-2 mb-2">
            <i data-lucide="info" class="w-4 h-4 text-slate-500"></i>
            <h4 class="font-bold text-xs text-slate-700">투자 지표</h4>
        </div>
        <div class="grid grid-cols-2 gap-2 text-[10px]">
            <p class="flex items-center gap-1.5 text-slate-600"><span class="w-2 h-2 bg-red-500 rounded-full"></span> <b>비중 확대</b> (▲)</p>
            <p class="flex items-center gap-1.5 text-slate-600"><span class="w-2 h-2 bg-blue-500 rounded-full"></span> <b>비중 축소</b> (▼)</p>
        </div>
    </div>
    '''


def _whale_render_us_13f() -> str:
    """NPS 미국 13F TOP 100 — whale-insight 스타일."""
    try:
        from kis_api import fetch_nps_us_holdings
        data = fetch_nps_us_holdings(top=100, include_changes=True)
    except Exception as e:
        return f'<div class="p-4 bg-red-50 text-red-600 rounded-xl">로드 실패: {_html.escape(str(e))}</div>'
    if data.get("error"):
        return f'<div class="p-4 bg-amber-50 text-amber-700 rounded-xl">{_html.escape(data["error"])}</div>'

    rows_html = ''
    for idx, x in enumerate(data.get("rows", []), start=1):
        name = _html.escape((x.get("name_of_issuer") or "")[:32])
        val = x.get("value_usd", 0)
        val_str = f'${val/1e9:.2f}B' if val >= 1e9 else f'${val/1e6:.0f}M'
        weight = x.get("weight_pct", 0)
        status = x.get("status", "")
        sc = x.get("share_change_pct")
        if status == "NEW":
            arrow_html = '<span class="text-emerald-600 font-black text-[11px]">🆕 NEW</span>'
        elif status == "UP" and sc is not None:
            arrow_html = f'<span class="text-red-600 font-black"><span class="text-[10px] mr-0.5">▲</span>{abs(sc):.1f}%</span>'
        elif status == "DOWN" and sc is not None:
            arrow_html = f'<span class="text-blue-600 font-black"><span class="text-[10px] mr-0.5">▼</span>{abs(sc):.1f}%</span>'
        else:
            arrow_html = '<span class="text-slate-400">—</span>'
        rows_html += f'''
        <tr class="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
            <td class="py-4 px-3 text-slate-300 text-center font-bold text-[10px]">{str(idx).zfill(3)}</td>
            <td class="py-4 px-3 font-bold text-slate-800 text-left sticky-name">{name}</td>
            <td class="py-4 px-2 text-center font-black text-slate-900 tabular-nums text-[11px]">{val_str}</td>
            <td class="py-4 px-2 text-center text-slate-500 tabular-nums text-[11px]">{weight:.2f}%</td>
            <td class="py-4 px-4 text-center tabular-nums">{arrow_html}</td>
        </tr>'''

    quarter = data.get("quarter", "?")
    period_end = data.get("period_end", "?")
    n_total = data.get("total_holdings", 0)
    total_b = data.get("total_value_usd", 0) / 1e9

    # EXIT 종목 카드
    exits_html = ''
    exits = data.get("exits_top10", [])
    if exits:
        exit_rows = ''
        for e in exits:
            val = e.get("prev_value_usd", 0)
            val_str = f'${val/1e9:.2f}B' if val >= 1e9 else f'${val/1e6:.0f}M'
            exit_rows += f'''
            <div class="flex justify-between items-center py-2 border-b border-slate-50 last:border-0">
                <span class="text-[12px] text-slate-700 font-bold">{_html.escape((e.get("name_of_issuer") or "")[:32])}</span>
                <span class="text-[11px] text-blue-600 font-black tabular-nums">{val_str}</span>
            </div>'''
        exits_html = f'''
        <details class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
            <summary class="px-4 py-3 cursor-pointer flex items-center justify-between bg-slate-50 hover:bg-slate-100">
                <span class="font-bold text-sm text-slate-700">전 분기 EXIT TOP 10</span>
                <i data-lucide="chevron-down" class="w-4 h-4 text-slate-400"></i>
            </summary>
            <div class="px-4 py-2">{exit_rows}</div>
        </details>'''

    return f'''
    <div class="px-4 py-3 bg-blue-50/50 border border-blue-100 rounded-2xl">
        <p class="text-[11px] text-blue-700 leading-relaxed font-medium">
            <i data-lucide="alert-circle" class="w-3 h-3 inline-block mr-1 -mt-0.5"></i>
            SEC EDGAR Form 13F-HR 자동 수집. 분기말 +45일 후 제출. <br>
            데이터 출처: <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001608046&type=13F" target="_blank" class="font-bold underline">SEC EDGAR (CIK 0001608046)</a>
        </p>
    </div>

    <div class="grid grid-cols-3 gap-2">
        <div class="bg-white rounded-xl border border-slate-100 p-3 text-center">
            <div class="text-[10px] text-slate-400 font-bold">분기</div>
            <div class="text-base font-black text-slate-900">{quarter}</div>
        </div>
        <div class="bg-white rounded-xl border border-slate-100 p-3 text-center">
            <div class="text-[10px] text-slate-400 font-bold">종목</div>
            <div class="text-base font-black text-slate-900">{n_total}</div>
        </div>
        <div class="bg-white rounded-xl border border-slate-100 p-3 text-center">
            <div class="text-[10px] text-slate-400 font-bold">총 가치</div>
            <div class="text-base font-black text-slate-900">${total_b:.1f}B</div>
        </div>
    </div>

    <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
        <div class="overflow-x-auto hide-scrollbar">
            <table class="w-full text-left border-collapse min-w-full">
                <thead>
                    <tr class="bg-slate-50 border-b border-slate-200 text-slate-500">
                        <th class="py-3 px-3 font-bold text-[10px] text-center w-12">#</th>
                        <th class="py-3 px-3 font-bold text-[10px] text-left sticky-name">종목명</th>
                        <th class="py-3 px-2 font-bold text-[10px] text-center">평가액</th>
                        <th class="py-3 px-2 font-bold text-[10px] text-center">비중</th>
                        <th class="py-3 px-4 font-bold text-[10px] text-center">주식변화</th>
                    </tr>
                </thead>
                <tbody class="text-[11px]">{rows_html}</tbody>
            </table>
        </div>
    </div>

    {exits_html}

    <p class="text-[10px] text-slate-400 text-center">분기말 {period_end} · TOP 100 표시 (전체 {n_total}종목)</p>
    '''


def _whale_render_kr_5pct() -> str:
    """NPS 5%룰 — data.go.kr NPS 단독 5%↑ 보유 분기 보고.

    데이터: nps_holdings_disclosed (data.go.kr 공공데이터, NPS 보고만).
    전 분기 대비 ▲/▼ 변동 표시.
    """
    import sqlite3 as _s
    import json as _json
    db_path = f"{_DATA_DIR}/stock.db"
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        latest_q_row = conn.execute(
            "SELECT quarter FROM nps_holdings_disclosed WHERE quarter != '' "
            "ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
        latest_q = latest_q_row["quarter"] if latest_q_row else ""
        prev_q_row = conn.execute(
            "SELECT DISTINCT quarter FROM nps_holdings_disclosed "
            "WHERE quarter != '' AND quarter < ? ORDER BY quarter DESC LIMIT 1",
            (latest_q,),
        ).fetchone() if latest_q else None
        prev_q = prev_q_row["quarter"] if prev_q_row else ""
        prev_map = {}
        if prev_q:
            for pr in conn.execute(
                "SELECT symbol, MAX(ratio_pct) AS max_r FROM nps_holdings_disclosed "
                "WHERE quarter = ? AND symbol != '' GROUP BY symbol",
                (prev_q,),
            ).fetchall():
                prev_map[pr["symbol"]] = float(pr["max_r"] or 0)
        raw = conn.execute(
            """SELECT report_date, company_name, symbol, ratio_pct
               FROM nps_holdings_disclosed
               WHERE quarter = ?""",
            (latest_q,),
        ).fetchall() if latest_q else []
        conn.close()
    except Exception as e:
        return f'<div class="p-4 bg-red-50 text-red-600 rounded-xl">로드 실패: {_html.escape(str(e))}</div>'

    items = []
    buy_cnt = 0
    sell_cnt = 0
    new_cnt = 0
    for r in raw:
        cur = float(r["ratio_pct"] or 0)
        prev = prev_map.get(r["symbol"]) if r["symbol"] else None
        is_new = (prev is None and prev_q != "")
        change = (cur - prev) if prev is not None else cur
        items.append({
            "company": r["company_name"],
            "symbol": r["symbol"] or "",
            "date": r["report_date"],
            "ratio": cur,
            "prev_ratio": prev or 0,
            "change": round(change, 2),
            "is_new": is_new,
        })
        if change > 0:
            buy_cnt += 1
        elif change < 0:
            sell_cnt += 1
        if is_new:
            new_cnt += 1

    items_json = _json.dumps(items, ensure_ascii=False)
    period_label = f"{latest_q} 분기" + (f" · 비교: {prev_q}" if prev_q else "")

    return f'''
    <div class="bg-slate-900 text-white -mx-4 -mt-4 px-6 py-6 rounded-b-3xl shadow-inner mb-4">
        <div class="flex items-center gap-2 mb-1">
            <span class="text-[10px] font-bold text-blue-300 bg-blue-900/40 px-2 py-0.5 rounded-full border border-blue-800">지분 5%↑</span>
            <span class="text-[10px] text-slate-400 font-medium">{period_label}</span>
        </div>
        <h2 class="text-2xl font-black tracking-tight mb-2">대량 보유 변동</h2>
        <div class="grid grid-cols-3 gap-2 mt-4">
            <div class="bg-white/5 rounded-xl p-3 border border-white/10">
                <div class="text-[10px] text-slate-400 font-bold">총 보고</div>
                <div class="text-xl font-black tabular-nums">{len(items)}</div>
            </div>
            <div class="bg-white/5 rounded-xl p-3 border border-white/10">
                <div class="text-[10px] text-red-300 font-bold">비중 확대 ▲</div>
                <div class="text-xl font-black text-red-400 tabular-nums">{buy_cnt}</div>
            </div>
            <div class="bg-white/5 rounded-xl p-3 border border-white/10">
                <div class="text-[10px] text-blue-300 font-bold">비중 축소 ▼</div>
                <div class="text-xl font-black text-blue-400 tabular-nums">{sell_cnt}</div>
            </div>
        </div>
    </div>

    <div class="px-4 py-3 bg-blue-50/50 border border-blue-100 rounded-2xl">
        <p class="text-[11px] text-slate-500 leading-relaxed">
            직전 분기 대비 지분율 증감을 추적합니다. <br>
            데이터 출처: <a href="https://www.data.go.kr/data/15106890/fileData.do" target="_blank" class="text-indigo-600 font-bold underline">data.go.kr 공공데이터</a>
        </p>
    </div>

    <div class="sticky top-14 z-40 bg-slate-50 border-y border-slate-200 -mx-4 px-4 py-3 flex gap-2 overflow-x-auto hide-scrollbar items-center">
        <button onclick="changeSort('rate')" id="btn-rate"
                class="filter-btn px-4 py-1.5 bg-white border border-slate-200 text-slate-500 text-xs font-bold rounded-full whitespace-nowrap transition-all">증감율순</button>
        <button onclick="changeSort('date')" id="btn-date"
                class="filter-btn px-4 py-1.5 bg-white border border-slate-200 text-slate-500 text-xs font-bold rounded-full whitespace-nowrap transition-all">최신순</button>
        <div class="w-[1px] h-4 bg-slate-200 mx-1 flex-shrink-0"></div>
        <button onclick="filterType('buy')" id="btn-buy"
                class="filter-btn px-4 py-1.5 bg-white border border-slate-200 text-slate-500 text-xs font-bold rounded-full whitespace-nowrap transition-all">비중 확대</button>
        <button onclick="filterType('sell')" id="btn-sell"
                class="filter-btn px-4 py-1.5 bg-white border border-slate-200 text-slate-500 text-xs font-bold rounded-full whitespace-nowrap transition-all">비중 축소</button>
    </div>

    <div id="stock-list" class="space-y-3"></div>

    <div class="p-4 bg-white rounded-2xl border border-dashed border-slate-300">
        <p class="text-[11px] text-slate-500 leading-relaxed">
            <span class="font-bold text-slate-800">⚠️ 주의사항:</span> <br>
            본 데이터는 data.go.kr 공공데이터를 기반으로 자동 수집됩니다. <br>
            5% 미만 보유 종목은 공시 의무가 없어 표시되지 않습니다.
        </p>
    </div>

    <script>
    const KR5PCT_DATA = {items_json};
    let curSort = 'rate';
    let curFilter = 'all';

    function changeSort(s) {{ curSort = s; render(); }}
    function filterType(f) {{ curFilter = (curFilter === f) ? 'all' : f; render(); }}

    function render() {{
        let data = [...KR5PCT_DATA];
        if (curSort === 'rate') {{
            data.sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
        }} else {{
            data.sort((a, b) => new Date(b.date) - new Date(a.date));
        }}
        const filtered = data.filter(x => {{
            if (curFilter === 'buy') return x.change >= 0;
            if (curFilter === 'sell') return x.change < 0;
            return true;
        }});
        const list = document.getElementById('stock-list');
        if (filtered.length === 0) {{
            list.innerHTML = '<div class="py-20 text-center text-slate-400 font-medium">데이터 없음</div>';
        }} else {{
            list.innerHTML = filtered.map(x => {{
                const isBuy = x.change >= 0;
                const badgeCls = isBuy
                    ? 'text-red-600 bg-red-50 border-red-100'
                    : 'text-blue-600 bg-blue-50 border-blue-100';
                const badgeTxt = isBuy ? '비중 확대' : '비중 축소';
                const rateCls = isBuy ? 'text-red-600' : 'text-blue-600';
                const arrow = isBuy ? '▲' : '▼';
                const ratio10 = x.ratio >= 10 ? 'text-red-600' : 'text-slate-700';
                const symHtml = x.symbol ? `<span class="text-[10px] text-slate-400 font-bold ml-1">${{x.symbol}}</span>` : '';
                const qty = Math.abs(x.stkqy).toLocaleString();
                const qtyIrds = (x.stkqy_irds >= 0 ? '+' : '-') + Math.abs(x.stkqy_irds).toLocaleString();
                return `<div class="bg-white p-4 rounded-2xl shadow-sm border border-slate-100 active:scale-[0.98] transition-transform">
                    <div class="flex justify-between items-start mb-3">
                        <div class="flex-1 min-w-0 pr-2">
                            <div class="flex items-center gap-2 mb-1.5">
                                <span class="text-[10px] font-bold px-1.5 py-0.5 rounded border ${{badgeCls}}">${{badgeTxt}}</span>
                                <span class="text-[10px] text-slate-400 font-bold">제출일 ${{x.date}}</span>
                            </div>
                            <h3 class="text-lg font-extrabold text-slate-900">${{x.company}}${{symHtml}}</h3>
                        </div>
                        <div class="text-right flex-shrink-0">
                            <span class="${{rateCls}} text-lg font-black"><span class="text-xs">${{arrow}}</span> ${{Math.abs(x.change).toFixed(2)}}%p</span>
                            <p class="text-[10px] text-slate-400 font-bold mt-0.5">최종지분 <span class="${{ratio10}} font-black">${{x.ratio.toFixed(2)}}%</span></p>
                        </div>
                    </div>
                    <div class="bg-slate-50 p-2 rounded-xl mb-2">
                        <p class="text-[9px] text-slate-400 font-bold mb-0.5">보고자</p>
                        <p class="text-xs font-bold text-slate-700">${{x.repror}}</p>
                    </div>
                    <div class="grid grid-cols-2 gap-2 pt-3 border-t border-slate-50">
                        <div class="bg-slate-50 p-2 rounded-xl">
                            <p class="text-[9px] text-slate-400 font-bold mb-0.5">보유주식수</p>
                            <p class="text-xs font-bold text-slate-700">${{qty}}주</p>
                            <p class="text-[10px] ${{rateCls}} font-bold">${{qtyIrds}}</p>
                        </div>
                        <div class="bg-slate-50 p-2 rounded-xl">
                            <p class="text-[9px] text-slate-400 font-bold mb-0.5">변동사유</p>
                            <p class="text-xs font-bold text-slate-700 line-clamp-2">${{x.report_resn}}</p>
                        </div>
                    </div>
                </div>`;
            }}).join('');
        }}
        // active button style
        document.querySelectorAll('.filter-btn').forEach(b => {{
            b.className = 'filter-btn px-4 py-1.5 bg-white border border-slate-200 text-slate-500 text-xs font-bold rounded-full whitespace-nowrap transition-all';
        }});
        const sb = document.getElementById('btn-' + curSort);
        if (sb) {{ sb.classList.remove('bg-white','text-slate-500','border-slate-200'); sb.classList.add('bg-slate-900','text-white','border-slate-900'); }}
        if (curFilter !== 'all') {{
            const fb = document.getElementById('btn-' + curFilter);
            const cls = (curFilter === 'buy') ? ['bg-red-500','text-white','border-red-500'] : ['bg-blue-500','text-white','border-blue-500'];
            if (fb) {{ fb.classList.remove('bg-white','text-slate-500','border-slate-200'); fb.classList.add(...cls); }}
        }}
    }}
    render();
    </script>
    '''


def _whale_render_pension_flow() -> str:
    """연기금 5일 매수/매도 — 매수 + 매도 통합."""
    import sqlite3 as _s
    db_path = f"{_DATA_DIR}/stock.db"
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        dates = [r["trade_date"] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM pension_flow_daily ORDER BY trade_date DESC LIMIT 5"
        ).fetchall()]
        if dates:
            ph = ",".join("?" for _ in dates)
            agg_rows = conn.execute(
                f"""SELECT pf.symbol, pf.name, pf.market,
                          SUM(pf.net_amount_won) AS net_total
                   FROM pension_flow_daily pf
                   WHERE pf.trade_date IN ({ph})
                   GROUP BY pf.symbol HAVING net_total != 0""", dates
            ).fetchall()
            symbols = [r["symbol"] for r in agg_rows]
            cap_map = {}
            if symbols:
                cph = ",".join("?" for _ in symbols)
                for cr in conn.execute(
                    f"SELECT symbol, MAX(trade_date) AS d FROM daily_snapshot WHERE symbol IN ({cph}) GROUP BY symbol",
                    symbols,
                ).fetchall():
                    cap = conn.execute(
                        "SELECT market_cap FROM daily_snapshot WHERE symbol=? AND trade_date=?",
                        (cr["symbol"], cr["d"]),
                    ).fetchone()
                    if cap and cap["market_cap"]:
                        cap_map[cr["symbol"]] = int(cap["market_cap"]) * 100_000_000
        else:
            agg_rows = []
            cap_map = {}
        conn.close()
    except Exception as e:
        return f'<div class="p-4 bg-red-50 text-red-600 rounded-xl">로드 실패: {_html.escape(str(e))}</div>'

    enriched = []
    for r in agg_rows:
        cap = cap_map.get(r["symbol"], 0)
        pct = (r["net_total"] * 100.0 / cap) if cap > 0 else 0
        enriched.append({
            "symbol": r["symbol"], "name": r["name"], "market": r["market"],
            "net_won": r["net_total"], "cap_won": cap, "pct": pct,
        })
    buy_top = sorted(
        [e for e in enriched if e["net_won"] > 0],
        key=lambda x: (-x["pct"] if x["cap_won"] else 0, -x["net_won"]),
    )[:50]
    sell_top = sorted(
        [e for e in enriched if e["net_won"] < 0],
        key=lambda x: (x["pct"] if x["cap_won"] else 0, x["net_won"]),
    )[:50]
    period = (f"{dates[-1][:4]}-{dates[-1][4:6]}-{dates[-1][6:]} ~ "
              f"{dates[0][:4]}-{dates[0][4:6]}-{dates[0][6:]}") if dates else "-"

    def _row(e, idx, is_buy=True):
        net_eok = e["net_won"] / 100_000_000
        pct_str = f'{e["pct"]:+.2f}%' if e["cap_won"] else '—'
        color_cls = 'text-red-600' if is_buy else 'text-blue-600'  # 한국식 (매수=빨강)
        sign = '▲' if is_buy else '▼'
        return f'''
        <tr class="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
            <td class="py-3 px-3 text-slate-300 text-center font-bold text-[10px]">{str(idx).zfill(2)}</td>
            <td class="py-3 px-3 font-bold text-slate-800 text-left sticky-name">{_html.escape(e["name"] or "")} <span class="text-[10px] text-slate-400">{e["symbol"]}</span></td>
            <td class="py-3 px-2 text-right {color_cls} font-black tabular-nums text-[11px]">{net_eok:+,.0f}억</td>
            <td class="py-3 px-3 text-right {color_cls} font-black tabular-nums text-[12px]"><span class="text-[10px] mr-0.5">{sign}</span>{abs(e["pct"]):.2f}%</td>
        </tr>'''

    buy_rows = ''.join(_row(e, i, True) for i, e in enumerate(buy_top, start=1))
    sell_rows = ''.join(_row(e, i, False) for i, e in enumerate(sell_top, start=1))

    def _make_table(title, color_cls, rows, label_pct):
        if not rows:
            return f'<div class="p-4 bg-slate-50 text-slate-400 rounded-2xl text-center text-[12px]">{title} 데이터 없음</div>'
        return f'''
        <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
            <div class="px-4 py-3 border-b border-slate-100 flex items-center justify-between bg-{color_cls}-50/30">
                <h4 class="font-extrabold text-sm text-{color_cls}-600">{title}</h4>
                <span class="text-[10px] text-slate-400 font-bold">시총% 정렬</span>
            </div>
            <div class="overflow-x-auto hide-scrollbar">
                <table class="w-full text-left border-collapse min-w-full">
                    <thead>
                        <tr class="bg-slate-50 border-b border-slate-200 text-slate-500">
                            <th class="py-2 px-3 font-bold text-[10px] text-center w-10">#</th>
                            <th class="py-2 px-3 font-bold text-[10px] text-left sticky-name">종목</th>
                            <th class="py-2 px-2 font-bold text-[10px] text-right">{label_pct}</th>
                            <th class="py-2 px-3 font-bold text-[10px] text-right">시총%</th>
                        </tr>
                    </thead>
                    <tbody class="text-[11px]">{rows}</tbody>
                </table>
            </div>
        </div>'''

    return f'''
    <div class="px-4 py-3 bg-emerald-50/50 border border-emerald-100 rounded-2xl">
        <p class="text-[11px] text-emerald-700 leading-relaxed font-medium">
            <i data-lucide="alert-circle" class="w-3 h-3 inline-block mr-1 -mt-0.5"></i>
            연기금 단독 매매 (KRX 8개 투자자 분류 중 '연기금'). NPS가 60~80% 비중. <br>
            기간: <b>{period}</b> · 매일 16:30 자동 수집 (pykrx)
        </p>
    </div>

    {_make_table('🟢 매수 TOP 50', 'red', buy_rows, '순매수')}
    {_make_table('🔴 매도 TOP 50', 'blue', sell_rows, '순매도')}

    <div class="p-4 bg-slate-100 rounded-2xl border border-slate-200">
        <p class="text-[10px] text-slate-500 leading-relaxed">
            * 시총% = 5일 누적 순매수(매도) ÷ 시가총액. 작은 회사에서 큰 % 변화는 강한 시그널.
        </p>
    </div>
    '''


def _whale_render_insider() -> str:
    """NPS 핵심 보유 (지분 10%↑) — nps_holdings_disclosed에서 NPS 10%↑ 종목만.

    NPS는 임원·주요주주 보고 안 함 (기관투자자라 D002 적용 X).
    "핵심 주주 거래" 의미를 NPS 10% 이상 보유 종목으로 재정의.
    """
    import sqlite3 as _s
    import json as _json
    db_path = f"{_DATA_DIR}/stock.db"
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        latest_q_row = conn.execute(
            "SELECT quarter FROM nps_holdings_disclosed WHERE quarter != '' "
            "ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
        latest_q = latest_q_row["quarter"] if latest_q_row else ""
        prev_q_row = conn.execute(
            "SELECT DISTINCT quarter FROM nps_holdings_disclosed "
            "WHERE quarter != '' AND quarter < ? ORDER BY quarter DESC LIMIT 1",
            (latest_q,),
        ).fetchone() if latest_q else None
        prev_q = prev_q_row["quarter"] if prev_q_row else ""
        prev_map = {}
        if prev_q:
            for pr in conn.execute(
                "SELECT symbol, MAX(ratio_pct) AS max_r FROM nps_holdings_disclosed "
                "WHERE quarter = ? AND symbol != '' GROUP BY symbol",
                (prev_q,),
            ).fetchall():
                prev_map[pr["symbol"]] = float(pr["max_r"] or 0)
        # 10%↑ 만 필터 (NPS 핵심 보유)
        raw = conn.execute(
            """SELECT report_date, company_name, symbol, ratio_pct,
                      COALESCE(stkqy, 0) AS stkqy,
                      COALESCE(stkqy_irds, 0) AS stkqy_irds,
                      COALESCE(report_resn, '') AS report_resn,
                      COALESCE(source, 'data.go.kr') AS source
               FROM nps_holdings_disclosed
               WHERE quarter = ? AND ratio_pct >= 10""",
            (latest_q,),
        ).fetchall() if latest_q else []
        conn.close()
    except Exception as e:
        return f'<div class="p-4 bg-red-50 text-red-600 rounded-xl">로드 실패: {_html.escape(str(e))}</div>'

    items = []
    buy_cnt = 0
    sell_cnt = 0
    for r in raw:
        cur = float(r["ratio_pct"] or 0)
        prev = prev_map.get(r["symbol"]) if r["symbol"] else None
        rate_chg = (cur - prev) if prev is not None else cur
        qty_chg = int(r["stkqy_irds"] or 0)
        items.append({
            "company": r["company_name"],
            "symbol": r["symbol"] or "",
            "date": r["report_date"],
            "reporter": "국민연금공단",
            "role": "10%이상주주",
            "qty": qty_chg,
            "stkqy": int(r["stkqy"] or 0),
            "rate": cur,
            "rate_chg": round(rate_chg, 2),
        })
        if rate_chg > 0:
            buy_cnt += 1
        elif rate_chg < 0:
            sell_cnt += 1

    items_json = _json.dumps(items, ensure_ascii=False)

    return f'''
    <div class="bg-indigo-950 text-white -mx-4 -mt-4 px-6 py-6 rounded-b-3xl shadow-inner mb-4">
        <div class="flex items-center gap-2 mb-1">
            <span class="text-[10px] font-bold text-indigo-200 bg-indigo-900/60 px-2 py-0.5 rounded-full border border-indigo-800">지분 10%↑</span>
            <span class="text-[10px] text-slate-400 font-medium">최근 90일</span>
        </div>
        <h2 class="text-2xl font-black tracking-tight mb-2">핵심 주주 거래 보고</h2>
        <div class="grid grid-cols-3 gap-2 mt-4">
            <div class="bg-white/5 rounded-xl p-3 border border-white/10">
                <div class="text-[10px] text-slate-400 font-bold">총 보고</div>
                <div class="text-xl font-black tabular-nums">{len(items)}</div>
            </div>
            <div class="bg-white/5 rounded-xl p-3 border border-white/10">
                <div class="text-[10px] text-red-300 font-bold">매수 ▲</div>
                <div class="text-xl font-black text-red-400 tabular-nums">{buy_cnt}</div>
            </div>
            <div class="bg-white/5 rounded-xl p-3 border border-white/10">
                <div class="text-[10px] text-blue-300 font-bold">매도 ▼</div>
                <div class="text-xl font-black text-blue-400 tabular-nums">{sell_cnt}</div>
            </div>
        </div>
    </div>

    <div class="px-4 py-3 bg-indigo-50/50 border border-indigo-100 rounded-2xl">
        <p class="text-[11px] text-slate-500 leading-relaxed">
            상장법인 주요주주 소유주식 변동 보고. 10%↑ 보유자 매매는 강한 시그널. <br>
            데이터 출처: <a href="https://opendart.fss.or.kr" target="_blank" class="text-indigo-600 font-bold underline">DART 임원·주요주주 보고</a> · 5분마다 자동 수집
        </p>
    </div>

    <div class="sticky top-14 z-40 bg-slate-50 border-y border-slate-200 -mx-4 px-4 py-3 flex gap-2 overflow-x-auto hide-scrollbar items-center">
        <button onclick="changeSort('rate')" id="btn-rate"
                class="filter-btn px-4 py-1.5 bg-white border border-slate-200 text-slate-500 text-xs font-bold rounded-full whitespace-nowrap transition-all">증감율순</button>
        <button onclick="changeSort('date')" id="btn-date"
                class="filter-btn px-4 py-1.5 bg-white border border-slate-200 text-slate-500 text-xs font-bold rounded-full whitespace-nowrap transition-all">최신순</button>
        <div class="w-[1px] h-4 bg-slate-200 mx-1 flex-shrink-0"></div>
        <button onclick="filterType('buy')" id="btn-buy"
                class="filter-btn px-4 py-1.5 bg-white border border-slate-200 text-slate-500 text-xs font-bold rounded-full whitespace-nowrap transition-all">매수만</button>
        <button onclick="filterType('sell')" id="btn-sell"
                class="filter-btn px-4 py-1.5 bg-white border border-slate-200 text-slate-500 text-xs font-bold rounded-full whitespace-nowrap transition-all">매도만</button>
    </div>

    <div id="stock-list" class="space-y-3"></div>

    <div class="p-4 bg-white rounded-2xl border border-dashed border-slate-300">
        <p class="text-[11px] text-slate-500 leading-relaxed">
            <span class="font-bold text-slate-800">⚠️ 주의사항:</span> <br>
            보고일은 DART 접수일 기준. 실제 매매 시점과 차이 가능. 5%↑ 보유자만 표시.
        </p>
    </div>

    <script>
    const INSIDER_DATA = {items_json};
    let curSort = 'date';
    let curFilter = 'all';

    function changeSort(s) {{ curSort = s; render(); }}
    function filterType(f) {{ curFilter = (curFilter === f) ? 'all' : f; render(); }}

    function render() {{
        let data = [...INSIDER_DATA];
        if (curSort === 'rate') {{
            data.sort((a, b) => Math.abs(b.rate_chg) - Math.abs(a.rate_chg));
        }} else {{
            data.sort((a, b) => (b.date > a.date) ? 1 : -1);
        }}
        const filtered = data.filter(x => {{
            if (curFilter === 'buy') return x.qty > 0;
            if (curFilter === 'sell') return x.qty < 0;
            return true;
        }});
        const list = document.getElementById('stock-list');
        if (filtered.length === 0) {{
            list.innerHTML = '<div class="py-20 text-center text-slate-400 font-medium">데이터 없음</div>';
        }} else {{
            list.innerHTML = filtered.map(x => {{
                const isBuy = x.qty > 0;
                const badgeCls = isBuy
                    ? 'text-red-600 bg-red-50 border-red-100'
                    : 'text-blue-600 bg-blue-50 border-blue-100';
                const badgeTxt = isBuy ? '매수' : '매도';
                const rateCls = isBuy ? 'text-red-600' : 'text-blue-600';
                const arrow = isBuy ? '▲' : '▼';
                const rate10 = x.rate >= 10 ? 'text-red-600' : 'text-slate-700';
                const symHtml = x.symbol ? `<span class="text-[10px] text-slate-400 font-bold ml-1">${{x.symbol}}</span>` : '';
                const qtyAbs = Math.abs(x.qty).toLocaleString();
                const sign = isBuy ? '+' : '-';
                return `<div class="bg-white p-4 rounded-2xl shadow-sm border border-slate-100 active:scale-[0.98] transition-transform">
                    <div class="flex justify-between items-start mb-3">
                        <div>
                            <div class="flex items-center gap-2 mb-1.5">
                                <span class="text-[10px] font-bold px-1.5 py-0.5 rounded border ${{badgeCls}}">${{badgeTxt}}</span>
                                <span class="text-[10px] text-slate-400 font-bold">${{x.date}}</span>
                            </div>
                            <h3 class="text-lg font-extrabold text-slate-900">${{x.company}}${{symHtml}}</h3>
                        </div>
                        <div class="text-right">
                            <span class="${{rateCls}} text-lg font-black"><span class="text-xs">${{arrow}}</span> ${{x.rate_chg.toFixed(2)}}%p</span>
                            <p class="text-[10px] text-slate-400 font-bold mt-0.5">최종지분 <span class="${{rate10}} font-black">${{x.rate.toFixed(2)}}%</span></p>
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-2 pt-3 border-t border-slate-50">
                        <div class="bg-slate-50 p-2 rounded-xl">
                            <p class="text-[9px] text-slate-400 font-bold mb-0.5">보유주식수</p>
                            <p class="text-xs font-bold text-slate-700">${{x.stkqy.toLocaleString()}}주</p>
                            <p class="text-[10px] ${{rateCls}} font-bold">${{sign}}${{qtyAbs}}</p>
                        </div>
                        <div class="bg-slate-50 p-2 rounded-xl">
                            <p class="text-[9px] text-slate-400 font-bold mb-0.5">보고자</p>
                            <p class="text-xs font-bold text-slate-700">${{x.reporter}}</p>
                        </div>
                    </div>
                </div>`;
            }}).join('');
        }}
        document.querySelectorAll('.filter-btn').forEach(b => {{
            b.className = 'filter-btn px-4 py-1.5 bg-white border border-slate-200 text-slate-500 text-xs font-bold rounded-full whitespace-nowrap transition-all';
        }});
        const sb = document.getElementById('btn-' + curSort);
        if (sb) {{ sb.classList.remove('bg-white','text-slate-500','border-slate-200'); sb.classList.add('bg-slate-900','text-white','border-slate-900'); }}
        if (curFilter !== 'all') {{
            const fb = document.getElementById('btn-' + curFilter);
            const cls = (curFilter === 'buy') ? ['bg-red-500','text-white','border-red-500'] : ['bg-blue-500','text-white','border-blue-500'];
            if (fb) {{ fb.classList.remove('bg-white','text-slate-500','border-slate-200'); fb.classList.add(...cls); }}
        }}
    }}
    render();
    </script>
    '''


def _build_whale_full_html() -> str:
    """Whale 전용 페이지 — 카드별 풀 데이터 (TOP 30 → 100), anchor 추가."""
    import sqlite3 as _s
    db_path = f"{_DATA_DIR}/stock.db"
    parts = []

    # ── 1) NPS KR 풀 포트 (200종목 모두 표시 — 스크롤) ──
    try:
        from kis_api import fetch_nps_kr_full_holdings
        kr_full = fetch_nps_kr_full_holdings(top=200)
        if kr_full.get("error"):
            parts.append(
                f'<div class="whale-card" id="nps-kr-full"><h3>🇰🇷 NPS 한국 풀 포트</h3>'
                f'<p style="color:var(--fg2)">{_html.escape(kr_full["error"])}</p></div>'
            )
        else:
            quarter_lbl = kr_full.get("quarter_label", "?")
            snap = kr_full.get("snapshot_date", "?")
            n_tot = kr_full.get("total_holdings", 0)
            tot_eok = kr_full.get("total_valuation_eok", 0)
            body = ('<div class="scroll-tbl"><table class="whale-tbl">'
                    '<tr><th>#</th><th>종목</th><th>비중</th>'
                    '<th>평가액</th><th>지분%</th><th>전년대비</th></tr>')
            for idx, x in enumerate(kr_full.get("rows", []), start=1):
                name = _html.escape((x.get("name") or "")[:24])
                sym = x.get("symbol") or ""
                sym_html = (f' <span style="color:var(--fg2);font-size:0.8em">{sym}</span>'
                            if sym else '')
                w = x.get("weight_pct", 0)
                eok = x.get("valuation_eok", 0)
                cur_share = x.get("share_curr_pct", 0)
                share_style = ' style="color:#e57373;font-weight:600"' if cur_share >= 10 else ''
                sc_p = x.get("share_change_p")
                if x.get("data_missing") or sc_p is None:
                    sc_html = '<span style="color:var(--fg2)">—</span>'
                elif sc_p > 0.05:
                    sc_html = f'<span style="color:#4caf50">▲ {sc_p:+.2f}p</span>'
                elif sc_p < -0.05:
                    sc_html = f'<span style="color:#e57373">▼ {sc_p:+.2f}p</span>'
                else:
                    sc_html = '<span style="color:var(--fg2)">—</span>'
                body += (f'<tr><td style="color:var(--fg2)">{idx}</td>'
                         f'<td>{name}{sym_html}</td>'
                         f'<td>{w:.2f}%</td>'
                         f'<td>{eok:,}억</td>'
                         f'<td{share_style}>{cur_share:.2f}%</td>'
                         f'<td>{sc_html}</td></tr>')
            body += '</table></div>'
            parts.append(
                f'<div class="whale-card" id="nps-kr-full">'
                f'<h3>🇰🇷 NPS 한국 풀 포트 ({quarter_lbl}) — {n_tot}종목</h3>'
                f'<p style="color:var(--fg2);font-size:0.85em;margin:0 0 8px">'
                f'스냅샷 {snap} | 총 평가액 {tot_eok:,}억 | 지분 10%↑ 빨강 | '
                f'출처: <a href="https://whale-insight.com" target="_blank" '
                f'style="color:var(--accent)">whale-insight.com</a></p>'
                f'{body}</div>'
            )
    except Exception as e:
        parts.append(f'<div class="whale-card" id="nps-kr-full"><h3>🇰🇷 NPS 한국 풀 포트</h3><p>로드 실패: {_html.escape(str(e))}</p></div>')

    # ── 2) NPS US 13F 풀 (TOP 100) ──
    try:
        from kis_api import fetch_nps_us_holdings
        us_data = fetch_nps_us_holdings(top=100, include_changes=True)
        if us_data.get("error"):
            parts.append(
                f'<div class="whale-card" id="nps-us-13f"><h3>🇺🇸 NPS 미국 13F</h3>'
                f'<p style="color:var(--fg2)">{_html.escape(us_data["error"])}</p></div>'
            )
        else:
            quarter = us_data.get("quarter", "?")
            period_end = us_data.get("period_end", "?")
            total_v = us_data.get("total_value_usd", 0)
            total_b = total_v / 1e9 if total_v else 0
            n_total = us_data.get("total_holdings", 0)
            body = ('<div class="scroll-tbl"><table class="whale-tbl">'
                    '<tr><th>#</th><th>종목</th><th>가치</th>'
                    '<th>비중</th><th>주식변화</th></tr>')
            for idx, x in enumerate(us_data.get("rows", []), start=1):
                name = _html.escape((x.get("name_of_issuer") or "")[:32])
                val = x.get("value_usd", 0)
                val_str = f'${val/1e9:.2f}B' if val >= 1e9 else f'${val/1e6:.0f}M'
                weight = x.get("weight_pct", 0)
                status = x.get("status", "")
                sc = x.get("share_change_pct")
                if status == "NEW":
                    sc_html = '<span style="color:#4caf50;font-weight:600">🆕 NEW</span>'
                elif status == "UP" and sc is not None:
                    sc_html = f'<span style="color:#4caf50">▲ {sc:+.1f}%</span>'
                elif status == "DOWN" and sc is not None:
                    sc_html = f'<span style="color:#e57373">▼ {sc:+.1f}%</span>'
                else:
                    sc_html = '<span style="color:var(--fg2)">—</span>'
                body += (f'<tr><td style="color:var(--fg2)">{idx}</td>'
                         f'<td>{name}</td>'
                         f'<td>{val_str}</td>'
                         f'<td>{weight:.2f}%</td>'
                         f'<td>{sc_html}</td></tr>')
            body += '</table></div>'

            exits_html = ''
            exits = us_data.get("exits_top10", [])
            if exits:
                exits_html = ('<details style="margin-top:10px"><summary '
                              'style="cursor:pointer;color:var(--fg2)">전 분기 EXIT TOP 10 ▼</summary>'
                              '<table class="whale-tbl" style="margin-top:6px">'
                              '<tr><th>종목</th><th>직전 가치</th></tr>')
                for e in exits:
                    val = e.get("prev_value_usd", 0)
                    val_str = f'${val/1e9:.2f}B' if val >= 1e9 else f'${val/1e6:.0f}M'
                    exits_html += (f'<tr><td style="color:#e57373">'
                                   f'{_html.escape((e.get("name_of_issuer") or "")[:32])}</td>'
                                   f'<td>{val_str}</td></tr>')
                exits_html += '</table></details>'

            parts.append(
                f'<div class="whale-card" id="nps-us-13f">'
                f'<h3>🇺🇸 NPS 미국 13F ({quarter}) — TOP 100 / {n_total}종목</h3>'
                f'<p style="color:var(--fg2);font-size:0.85em;margin:0 0 8px">'
                f'분기말 {period_end} | 총 ${total_b:.1f}B | 출처: '
                f'<a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001608046&type=13F" '
                f'target="_blank" style="color:var(--accent)">SEC EDGAR</a></p>'
                f'{body}{exits_html}</div>'
            )
    except Exception as e:
        parts.append(f'<div class="whale-card" id="nps-us-13f"><h3>🇺🇸 NPS 미국 13F</h3><p>로드 실패: {_html.escape(str(e))}</p></div>')

    # ── 3) NPS KR 5%룰 (현 분기 전체) ──
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        latest_q_row = conn.execute(
            "SELECT quarter FROM nps_holdings_disclosed WHERE quarter != '' "
            "ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
        latest_q = latest_q_row["quarter"] if latest_q_row else ""
        prev_q_row = conn.execute(
            "SELECT DISTINCT quarter FROM nps_holdings_disclosed "
            "WHERE quarter != '' AND quarter < ? ORDER BY quarter DESC LIMIT 1",
            (latest_q,),
        ).fetchone() if latest_q else None
        prev_q = prev_q_row["quarter"] if prev_q_row else ""
        prev_map = {}
        if prev_q:
            for pr in conn.execute(
                "SELECT symbol, MAX(ratio_pct) AS max_r FROM nps_holdings_disclosed "
                "WHERE quarter = ? AND symbol != '' GROUP BY symbol",
                (prev_q,),
            ).fetchall():
                prev_map[pr["symbol"]] = float(pr["max_r"] or 0)
        rows = conn.execute(
            """SELECT report_date, company_name, symbol, ratio_pct
               FROM nps_holdings_disclosed WHERE quarter = ?
               ORDER BY ratio_pct DESC, report_date DESC""",
            (latest_q,),
        ).fetchall() if latest_q else []
        conn.close()

        body = ''
        if rows:
            body = ('<div class="scroll-tbl"><table class="whale-tbl">'
                    '<tr><th>#</th><th>일자</th><th>종목</th>'
                    '<th>지분%</th><th>전분기</th></tr>')
            for idx, r in enumerate(rows, start=1):
                bgs = ''
                if r["ratio_pct"] >= 10:
                    bgs = ' style="color:#e57373;font-weight:600"'
                cur_r = float(r["ratio_pct"] or 0)
                prev_r = prev_map.get(r["symbol"]) if r["symbol"] else None
                if prev_q and r["symbol"]:
                    if prev_r is None:
                        chg_html = '<span style="color:#4caf50;font-weight:600">🆕 NEW</span>'
                    elif cur_r > prev_r + 0.05:
                        chg_html = f'<span style="color:#4caf50">▲ {cur_r-prev_r:+.2f}p</span>'
                    elif cur_r < prev_r - 0.05:
                        chg_html = f'<span style="color:#e57373">▼ {cur_r-prev_r:+.2f}p</span>'
                    else:
                        chg_html = '<span style="color:var(--fg2)">—</span>'
                else:
                    chg_html = '<span style="color:var(--fg2)">—</span>'
                body += (f'<tr><td style="color:var(--fg2)">{idx}</td>'
                         f'<td>{_html.escape(r["report_date"])}</td>'
                         f'<td>{_html.escape(r["company_name"])}'
                         f'{(f" ({r["symbol"]})") if r["symbol"] else ""}</td>'
                         f'<td{bgs}>{r["ratio_pct"]:.2f}</td>'
                         f'<td>{chg_html}</td></tr>')
            body += '</table></div>'
        else:
            body = '<p style="color:var(--fg2)">데이터 없음</p>'
        prev_note = f' | 비교: {prev_q}' if prev_q else ''
        parts.append(
            f'<div class="whale-card" id="nps-kr-5pct">'
            f'<h3>🏛 NPS 한국 5%룰 ({latest_q or "-"}) — {len(rows)}건</h3>'
            f'<p style="color:var(--fg2);font-size:0.85em;margin:0 0 8px">'
            f'5%↑ 지분 신규/변동 보고 | 10%↑ 빨강{prev_note} | 출처: '
            f'<a href="https://www.data.go.kr/data/15106890/fileData.do" target="_blank" '
            f'style="color:var(--accent)">data.go.kr</a></p>'
            f'{body}</div>'
        )
    except Exception as e:
        parts.append(f'<div class="whale-card" id="nps-kr-5pct"><h3>🏛 NPS 한국 5%룰</h3><p>로드 실패: {_html.escape(str(e))}</p></div>')

    # ── 4) 연기금 5일 매수+매도 (한 카드 통합) ──
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        dates = [r["trade_date"] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM pension_flow_daily "
            "ORDER BY trade_date DESC LIMIT 5"
        ).fetchall()]
        if dates:
            placeholders = ",".join("?" for _ in dates)
            agg_rows = conn.execute(
                f"""SELECT pf.symbol, pf.name, pf.market,
                          SUM(pf.net_amount_won) AS net_total
                   FROM pension_flow_daily pf
                   WHERE pf.trade_date IN ({placeholders})
                   GROUP BY pf.symbol HAVING net_total != 0""",
                dates,
            ).fetchall()
            symbols = [r["symbol"] for r in agg_rows]
            cap_map = {}
            if symbols:
                ph = ",".join("?" for _ in symbols)
                cap_rows = conn.execute(
                    f"""SELECT symbol, MAX(trade_date) AS d FROM daily_snapshot
                        WHERE symbol IN ({ph}) GROUP BY symbol""", symbols
                ).fetchall()
                for cr in cap_rows:
                    cap = conn.execute(
                        "SELECT market_cap FROM daily_snapshot WHERE symbol=? AND trade_date=?",
                        (cr["symbol"], cr["d"])
                    ).fetchone()
                    if cap and cap["market_cap"]:
                        cap_map[cr["symbol"]] = int(cap["market_cap"]) * 100_000_000
        else:
            agg_rows = []
            cap_map = {}
        conn.close()

        enriched = []
        for r in agg_rows:
            cap = cap_map.get(r["symbol"], 0)
            pct = (r["net_total"] * 100.0 / cap) if cap > 0 else 0
            enriched.append({
                "symbol": r["symbol"], "name": r["name"], "market": r["market"],
                "net_won": r["net_total"], "cap_won": cap, "pct": pct,
            })

        def _row(e, idx):
            sign = '🟢' if e["net_won"] > 0 else '🔴'
            net_eok = e["net_won"] / 100_000_000
            pct_str = f'{e["pct"]:+.2f}%' if e["cap_won"] else '—'
            color = '#4caf50' if e["net_won"] > 0 else '#e57373'
            return (f'<tr><td style="color:var(--fg2)">{idx}</td>'
                    f'<td>{sign} {_html.escape(e["name"])} '
                    f'<span style="color:var(--fg2);font-size:0.8em">{e["symbol"]}</span></td>'
                    f'<td style="color:{color}">{net_eok:+,.0f}억</td>'
                    f'<td style="color:{color};font-weight:600">{pct_str}</td></tr>')

        buy_top = sorted(
            [e for e in enriched if e["net_won"] > 0],
            key=lambda x: (-x["pct"] if x["cap_won"] else 0, -x["net_won"]),
        )[:50]
        sell_top = sorted(
            [e for e in enriched if e["net_won"] < 0],
            key=lambda x: (x["pct"] if x["cap_won"] else 0, x["net_won"]),
        )[:50]

        period = (f"{dates[-1][:4]}-{dates[-1][4:6]}-{dates[-1][6:]} ~ "
                  f"{dates[0][:4]}-{dates[0][4:6]}-{dates[0][6:]}") if dates else "-"
        buy_body = ('<h4 style="margin:8px 0 4px;color:#4caf50">🟢 매수 TOP 50</h4>'
                    '<div class="scroll-tbl"><table class="whale-tbl">'
                    '<tr><th>#</th><th>종목</th><th>순매수</th><th>시총%</th></tr>')
        for i, e in enumerate(buy_top, start=1):
            buy_body += _row(e, i)
        buy_body += '</table></div>' if buy_top else '<p style="color:var(--fg2)">매수 없음</p>'
        sell_body = ('<h4 style="margin:14px 0 4px;color:#e57373">🔴 매도 TOP 50</h4>'
                     '<div class="scroll-tbl"><table class="whale-tbl">'
                     '<tr><th>#</th><th>종목</th><th>순매도</th><th>시총%</th></tr>')
        for i, e in enumerate(sell_top, start=1):
            sell_body += _row(e, i)
        sell_body += '</table></div>' if sell_top else '<p style="color:var(--fg2)">매도 없음</p>'
        parts.append(
            f'<div class="whale-card" id="pension-flow">'
            f'<h3>📊 연기금 5일 흐름 — 매수/매도 양방향</h3>'
            f'<p style="color:var(--fg2);font-size:0.85em;margin:0 0 8px">'
            f'기간: {period} | 시총% 정규화 | 출처: pykrx 연기금 단독 수급</p>'
            f'{buy_body}{sell_body}</div>'
        )
    except Exception as e:
        parts.append(f'<div class="whale-card" id="pension-flow"><h3>📊 연기금 5일</h3><p>로드 실패: {_html.escape(str(e))}</p></div>')

    # ── 5) 임원·5%↑ 주주 매매 (전체, 90일) ──
    try:
        conn = _s.connect(db_path, timeout=10)
        conn.execute("PRAGMA cache_size = -65536;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = _s.Row
        cutoff = (datetime.now(KST) - timedelta(days=90)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """SELECT it.rcept_dt, it.symbol, sm.name,
                      it.repror, it.ofcps, it.main_shrholdr,
                      it.stock_irds_cnt, it.stock_rate, it.stock_irds_rate
               FROM insider_transactions it
               LEFT JOIN stock_master sm ON sm.symbol = it.symbol
               WHERE it.rcept_dt >= ? AND it.stock_irds_cnt != 0 AND it.stock_rate >= 5
               ORDER BY it.rcept_dt DESC, ABS(it.stock_irds_rate) DESC""",
            (cutoff,),
        ).fetchall()
        conn.close()
        body = ''
        if rows:
            body = ('<div class="scroll-tbl"><table class="whale-tbl">'
                    '<tr><th>#</th><th>일자</th><th>종목</th>'
                    '<th>보고자</th><th>증감</th><th>지분%</th></tr>')
            for idx, r in enumerate(rows, start=1):
                irds = r["stock_irds_cnt"] or 0
                sign = '🟢' if irds > 0 else '🔴'
                color = '#4caf50' if irds > 0 else '#e57373'
                rate10 = ' style="color:#e57373;font-weight:600"' if (r["stock_rate"] or 0) >= 10 else ''
                role = (r["main_shrholdr"] or '') or (r["ofcps"] or '')
                body += (f'<tr><td style="color:var(--fg2)">{idx}</td>'
                         f'<td>{_html.escape(r["rcept_dt"])}</td>'
                         f'<td>{_html.escape(r["name"] or "")}'
                         f' <span style="color:var(--fg2);font-size:0.8em">{r["symbol"]}</span></td>'
                         f'<td>{_html.escape(r["repror"] or "")}'
                         f' <span style="color:var(--fg2);font-size:0.78em">{_html.escape(role)}</span></td>'
                         f'<td style="color:{color}">{sign} {irds:+,}</td>'
                         f'<td{rate10}>{(r["stock_rate"] or 0):.2f}%</td></tr>')
            body += '</table></div>'
        else:
            body = '<p style="color:var(--fg2)">최근 90일 5%↑ 보유자 매매 없음</p>'
        parts.append(
            f'<div class="whale-card" id="insider">'
            f'<h3>👤 임원·5%↑ 주주 매매 ({len(rows)}건)</h3>'
            f'<p style="color:var(--fg2);font-size:0.85em;margin:0 0 8px">'
            f'최근 90일 | 10%↑ 빨강 | 출처: DART 임원·주요주주 보고</p>'
            f'{body}</div>'
        )
    except Exception as e:
        parts.append(f'<div class="whale-card" id="insider"><h3>👤 임원 매매</h3><p>로드 실패: {_html.escape(str(e))}</p></div>')

    return f'<div class="whale-page-grid">{"".join(parts)}</div>'


async def _handle_dash_trades(request: web.Request) -> web.Response:
    """GET /dash/trades — 매매 기록 전체."""
    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>매매 기록</title>{_DASH_V2_CSS}</head><body>'
            f'<div style="margin-bottom:16px">'
            f'<a href="/dash-v2" style="color:var(--accent);text-decoration:none">← 대시보드</a>'
            f'</div>')
    try:
        tl = load_json(f"{_DATA_DIR}/trade_log.json", [])
        trades = tl if isinstance(tl, list) else tl.get("trades", [])
        total = len(trades)
        html += f'<h1>💼 매매 기록 ({total}건)</h1>'
        if trades:
            for t in reversed(trades):
                html += _build_trade_card(t, is_open=False)
        else:
            html += '<p>매매 기록이 없습니다.</p>'
    except Exception as e:
        html += f'<p style="color:red">로드 실패: {_html.escape(str(e))}</p>'
    html += "</body></html>"
    return web.Response(text=html, content_type="text/html")


async def _handle_dash_decisions(request: web.Request) -> web.Response:
    """GET /dash/decisions — 투자판단 전체 로그."""
    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>투자판단 기록</title>{_DASH_V2_CSS}</head><body>'
            f'<div style="margin-bottom:16px">'
            f'<a href="/dash-v2" style="color:var(--accent);text-decoration:none">← 대시보드</a>'
            f'</div>')

    try:
        dl = load_json(f"{_DATA_DIR}/decision_log.json", {})
        total = len(dl)
        html += f'<h1>📝 투자판단 기록 ({total}건)</h1>'

        for date in sorted(dl.keys(), reverse=True):
            entry = dl[date]
            regime_raw = str(entry.get("regime", "?"))
            regime_esc = _html.escape(regime_raw)

            if "강세" in regime_raw or "bull" in regime_raw.lower():
                badge_cls = "badge-bull"
            elif "약세" in regime_raw or "bear" in regime_raw.lower():
                badge_cls = "badge-bear"
            else:
                badge_cls = "badge-neutral"

            actions_list = entry.get("actions", [])
            if not actions_list and entry.get("summary"):
                actions_list = [str(entry["summary"])]

            preview_txt = _html.escape((actions_list[0] if actions_list else "")[:60])

            actions_html = ""
            for act in actions_list:
                actions_html += f"<li>{_html.escape(str(act))}</li>"
            actions_block = f'<ul class="decision-actions">{actions_html}</ul>' if actions_html else ""

            notes_raw = entry.get("notes", "")
            notes_block = (f'<div class="decision-notes">{_html.escape(str(notes_raw))}</div>'
                           if notes_raw else "")

            grades = entry.get("grades", {})
            grades_lines = ""
            if isinstance(grades, dict):
                for ticker, ginfo in grades.items():
                    if isinstance(ginfo, dict):
                        g = _html.escape(str(ginfo.get("grade", "")))
                        reason = _html.escape(str(ginfo.get("reason", "")))
                        grades_lines += (f'<div><strong>{_html.escape(ticker)}</strong>: '
                                         f'<span class="badge badge-{g}">{g}</span> {reason}</div>')
                    else:
                        grades_lines += f'<div><strong>{_html.escape(ticker)}</strong>: {_html.escape(str(ginfo))}</div>'
            grades_block = f'<div class="decision-grades">{grades_lines}</div>' if grades_lines else ""

            html += (
                f'<details class="decision-card">'
                f'<summary>'
                f'<span class="decision-date">{_html.escape(date)}</span>'
                f'<span class="badge {badge_cls}">{regime_esc}</span>'
                f'<span class="decision-preview">{preview_txt}</span>'
                f'</summary>'
                f'<div class="decision-body">'
                f'{actions_block}'
                f'{notes_block}'
                f'{grades_block}'
                f'</div>'
                f'</details>'
            )
    except Exception as e:
        html += f'<p style="color:red">로드 실패: {_html.escape(str(e))}</p>'

    html += "</body></html>"
    return web.Response(text=html, content_type="text/html")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 대시보드 편집 POST 핸들러 (TODO 토글/추가, 투자판단 저장)
# Cloudflare Access 가 /dash/* 앞단 인증. backend 가드는 입력 검증만 수행.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _handle_dash_todo_toggle(request: web.Request) -> web.Response:
    """POST /dash/todo/toggle — TODO 체크박스 [ ] ↔ [x] 토글."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    file_key = body.get("file")
    line_num = body.get("line")
    req_hash = body.get("hash")
    checked = body.get("checked")

    if file_key not in _TODO_FILE_MAP:
        return web.json_response({"error": "unknown file key"}, status=400)
    if not isinstance(line_num, int) or line_num < 1:
        return web.json_response({"error": "invalid line"}, status=400)
    if not isinstance(req_hash, str) or len(req_hash) != 12:
        return web.json_response({"error": "invalid hash"}, status=400)
    if not isinstance(checked, bool):
        return web.json_response({"error": "invalid checked"}, status=400)

    filename = _TODO_FILE_MAP[file_key]
    filepath = os.path.join(_DATA_DIR, filename)

    if not os.path.isfile(filepath):
        return web.json_response({"error": "file not found"}, status=404)
    try:
        if os.path.getsize(filepath) > 500 * 1024:
            return web.json_response({"error": "file too large"}, status=413)
    except OSError as e:
        return web.json_response({"error": f"stat failed: {e}"}, status=500)

    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return web.json_response({"error": f"read failed: {e}"}, status=500)

    lines = content.split("\n")
    idx = line_num - 1
    if idx < 0 or idx >= len(lines):
        return web.json_response({"error": "line out of range"}, status=400)

    orig_line = lines[idx]
    cur_hash = hashlib.sha1(orig_line.encode("utf-8")).hexdigest()[:12]
    if cur_hash != req_hash:
        return web.json_response({"error": "hash mismatch (file changed)"}, status=409)

    # 코드블록 내부 라인은 편집 거부 (critic #2)
    # lines[0..idx-1] 에서 ``` 개수가 홀수면 idx 는 코드블록 내부
    fence_count = 0
    for prev_line in lines[:idx]:
        if prev_line.strip().startswith("```"):
            fence_count += 1
    if fence_count % 2 == 1:
        return web.json_response(
            {"error": "line is inside code block, edit refused"}, status=400
        )

    # 체크박스 패턴 확인
    if checked:
        # [ ] → [x]
        if "[ ]" not in orig_line:
            return web.json_response({"error": "no [ ] found on line"}, status=400)
        new_line = orig_line.replace("[ ]", "[x]", 1)
    else:
        # [x] or [X] → [ ]
        if "[x]" in orig_line:
            new_line = orig_line.replace("[x]", "[ ]", 1)
        elif "[X]" in orig_line:
            new_line = orig_line.replace("[X]", "[ ]", 1)
        else:
            return web.json_response({"error": "no [x]/[X] found on line"}, status=400)

    lines[idx] = new_line
    new_content = "\n".join(lines)

    try:
        _atomic_write(filepath, new_content)
    except Exception as e:
        return web.json_response({"error": f"write failed: {e}"}, status=500)

    new_hash = hashlib.sha1(new_line.encode("utf-8")).hexdigest()[:12]
    return web.json_response({"ok": True, "new_hash": new_hash})


async def _handle_dash_todo_add(request: web.Request) -> web.Response:
    """POST /dash/todo/add — 파일 상단 첫 ## 섹션 바로 다음에 `- [ ] {text}` 삽입."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    file_key = body.get("file")
    text = body.get("text", "")

    if file_key not in _TODO_FILE_MAP:
        return web.json_response({"error": "unknown file key"}, status=400)
    if not isinstance(text, str):
        return web.json_response({"error": "text must be string"}, status=400)
    text = text.strip()
    if not text:
        return web.json_response({"error": "text empty"}, status=400)
    if len(text) > 500:
        return web.json_response({"error": "text too long (max 500)"}, status=400)
    if "\n" in text or "\r" in text:
        return web.json_response({"error": "newline not allowed"}, status=400)

    filename = _TODO_FILE_MAP[file_key]
    filepath = os.path.join(_DATA_DIR, filename)

    if not os.path.isfile(filepath):
        return web.json_response({"error": "file not found"}, status=404)
    try:
        if os.path.getsize(filepath) > 500 * 1024:
            return web.json_response({"error": "file too large"}, status=413)
    except OSError as e:
        return web.json_response({"error": f"stat failed: {e}"}, status=500)

    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return web.json_response({"error": f"read failed: {e}"}, status=500)

    lines = content.split("\n")
    new_item = f"- [ ] {text}"

    # 첫 ## 헤더 찾기 → 그 다음 빈 줄 뒤에 삽입
    insert_at = None
    for i, ln in enumerate(lines):
        if ln.startswith("## "):
            # ## 다음 빈 줄 찾기
            j = i + 1
            while j < len(lines) and lines[j].strip() != "":
                j += 1
            # 빈 줄이 있으면 그 뒤에, 없으면 파일 끝에
            insert_at = j + 1 if j < len(lines) else len(lines)
            break

    if insert_at is None:
        # ## 없으면 파일 최상단에 삽입
        insert_at = 0

    lines.insert(insert_at, new_item)
    new_content = "\n".join(lines)

    try:
        _atomic_write(filepath, new_content)
    except Exception as e:
        return web.json_response({"error": f"write failed: {e}"}, status=500)

    return web.json_response({"ok": True})


async def _handle_dash_decision_add(request: web.Request) -> web.Response:
    """POST /dash/decisions/add — decision_log.json 에 새 엔트리 추가/병합."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    date = body.get("date", "")
    regime = body.get("regime", "")
    notes = body.get("notes", "")
    actions_raw = body.get("actions", "")
    grades_raw = body.get("grades", "")

    # 입력 검증
    if not isinstance(date, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return web.json_response({"error": "invalid date (YYYY-MM-DD)"}, status=400)
    if not isinstance(regime, str) or not regime.strip():
        return web.json_response({"error": "regime required"}, status=400)
    if len(regime) > 200:
        return web.json_response({"error": "regime too long"}, status=400)
    for field_name, field_val in [("notes", notes), ("actions", actions_raw), ("grades", grades_raw)]:
        if not isinstance(field_val, str):
            return web.json_response({"error": f"{field_name} must be string"}, status=400)
        if len(field_val) > 5000:
            return web.json_response({"error": f"{field_name} too long (max 5000)"}, status=400)

    # actions 파싱
    actions_list = [ln.strip() for ln in actions_raw.split("\n") if ln.strip()]

    # grades 파싱: "티커:등급:이유" 형식, 콜론 부족 라인 무시
    grades_dict = {}
    for ln in grades_raw.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        parts = ln.split(":", 2)
        if len(parts) < 2:
            continue  # 콜론 부족 → 무시
        ticker = parts[0].strip()
        grade = parts[1].strip()
        reason = parts[2].strip() if len(parts) >= 3 else ""
        if not ticker or not grade:
            continue
        grades_dict[ticker] = {"grade": grade, "reason": reason}

    # decision_log.json 로드
    filepath = os.path.join(_DATA_DIR, "decision_log.json")
    try:
        if os.path.isfile(filepath):
            with open(filepath, encoding="utf-8") as f:
                dl = json.load(f)
            if not isinstance(dl, dict):
                dl = {}
        else:
            dl = {}
    except Exception as e:
        return web.json_response({"error": f"load failed: {e}"}, status=500)

    # 병합 or 신규
    existing = dl.get(date)
    if isinstance(existing, dict):
        # 기존 entry 와 병합
        # notes: 기존 + "\n---\n" + 새 (새 notes 있을 때만 구분자 append)
        old_notes = str(existing.get("notes", ""))
        if notes.strip():
            merged_notes = old_notes + ("\n---\n" if old_notes else "") + notes
        else:
            merged_notes = old_notes
        # actions: list 연장
        old_actions = existing.get("actions", [])
        if not isinstance(old_actions, list):
            old_actions = []
        merged_actions = old_actions + actions_list
        # grades: dict 병합 (새 값 우선)
        old_grades = existing.get("grades", {})
        if not isinstance(old_grades, dict):
            old_grades = {}
        merged_grades = dict(old_grades)
        merged_grades.update(grades_dict)
        # regime: 새 값으로 덮어쓰기 (비어있으면 유지)
        merged_regime = regime if regime.strip() else existing.get("regime", "")

        dl[date] = {
            "regime": merged_regime,
            "notes": merged_notes,
            "actions": merged_actions,
            "grades": merged_grades,
        }
    else:
        dl[date] = {
            "regime": regime,
            "notes": notes,
            "actions": actions_list,
            "grades": grades_dict,
        }

    # 저장 (atomic)
    try:
        _atomic_write(filepath, json.dumps(dl, ensure_ascii=False, indent=2))
    except Exception as e:
        return web.json_response({"error": f"save failed: {e}"}, status=500)

    return web.json_response({"ok": True})


def register_routes(app: web.Application) -> None:
    """대시보드 라우트를 aiohttp Application 에 등록.

    main.py 의 _run_all() 에서 1줄로 호출.
    호출 후 app.router 에 14개 엔드포인트가 등록된다.
    """
    app.router.add_get("/dash", _handle_dash_v2)
    app.router.add_get("/dash/file/{filename}", _handle_dash_file)
    app.router.add_get("/dash-v2", _handle_dash_v2)
    app.router.add_get("/dash/decisions", _handle_dash_decisions)
    app.router.add_get("/dash/trades", _handle_dash_trades)
    app.router.add_get("/dash/whale", _handle_dash_whale)
    app.router.add_get("/dash/file/research/{filename:.+}", _handle_dash_research_file)
    app.router.add_get("/dash/file/thesis/{filename:.+}", _handle_dash_research_file)
    app.router.add_get("/dash/reports/{ticker}", _handle_dash_reports)
    app.router.add_get("/dash/pdf/{ticker}/{filename}", _handle_dash_pdf)
    app.router.add_post("/dash/todo/toggle", _handle_dash_todo_toggle)
    app.router.add_post("/dash/todo/add", _handle_dash_todo_add)
    app.router.add_post("/dash/decisions/add", _handle_dash_decision_add)


__all__ = ["register_routes"]
