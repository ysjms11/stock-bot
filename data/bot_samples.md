# MCP 도구 입출력 샘플
> 업데이트: 2026-04-16 | 실제 호출 결과 (33개 도구)
>
> **이 문서**: 실제 호출 시 반환 JSON/메시지가 어떻게 생겼는지. 도구 용도 → `bot_guide.md`, 파라미터 → `bot_reference.txt`, 실전 조합 → `bot_scenarios.md`.

---

## 1. get_rank
**입력**: `{"type": "price", "sort": "rise", "n": 3}`
**출력**:
```json
{
  "sort": "rise",
  "market": "all",
  "count": 0,
  "items": [],
  "note": "장중 등락률 순위 미제공. get_scan(preset='momentum' 또는 'oversold')으로 KRX DB 기반 전일 데이터를 조회하세요."
}
```
> 장중에는 데이터 미제공. 전일 KRX DB 기반 스캔 사용 권장.

---

## 2. get_stock_detail
**입력**: `{"ticker": "005930"}`
**출력**:
```json
{
  "ticker": "005930",
  "market": "KR",
  "price": "206000",
  "chg": "0.98",
  "vol": "18244459",
  "w52h": "223000",
  "w52l": "53700",
  "per": "31.38",
  "pbr": "3.22",
  "eps": "6564.00",
  "bps": "63997.00",
  "investor": [
    {
      "stck_bsop_date": "20260410",
      "stck_clpr": "206000",
      "prsn_ntby_qty": "-1938572",
      "frgn_ntby_qty": "465171",
      "orgn_ntby_qty": "-475614"
    }
  ]
}
```

### 2b. get_stock_detail (orderbook)
**입력**: `{"ticker": "005930", "mode": "orderbook"}`
**출력**:
```json
{
  "ticker": "005930",
  "asks": [
    {"price": 206000, "volume": 260920},
    {"price": 206500, "volume": 77200},
    {"price": 207000, "volume": 121440},
    "..."
  ],
  "bids": [
    {"price": 205500, "volume": 119484},
    {"price": 205000, "volume": 240557},
    "..."
  ]
}
```

---

## 3. get_supply
**입력**: `{"mode": "daily", "ticker": "005930"}`
**출력**:
```json
{
  "ticker": "005930",
  "date": "20260410",
  "is_live": false,
  "foreign": {"buy": 6167959, "sell": 5702788, "net": 465171},
  "institution": {"buy": 6084001, "sell": 6559615, "net": -475614},
  "individual": {"buy": 3926546, "sell": 5865118, "net": -1938572}
}
```

### 3b. get_supply (history)
**입력**: `{"mode": "history", "ticker": "005930", "days": 3}`
**출력**:
```json
{
  "ticker": "005930",
  "days": 3,
  "history": [],
  "note": "수급 히스토리는 장 마감 후 데이터만 제공됩니다. 장중에는 get_supply(mode='estimate')로 추정 수급을 확인하세요."
}
```

---

## 4. get_consensus
**입력**: `{"ticker": "005930", "brief": true}`
**출력**:
```json
{
  "ticker": "005930",
  "name": "삼성전자",
  "consensus_target": {
    "avg": 288000,
    "high": 400000,
    "low": 210000
  },
  "opinion": {"buy": 25, "hold": 0, "sell": 0},
  "reports": [
    {"broker": "유안타증권", "date": "2026-04-09", "target": 330000, "title": "삼성전자-1H26 예상 영업이익 139.4조원"},
    {"broker": "KB증권", "date": "2026-04-08", "target": 360000, "title": "지금은 미답(未踏)의 상승 구간"},
    "..."
  ],
  "broker_targets": [
    {"broker": "유안타증권", "date": "2026-04-09", "target": 330000, "opinion": "매수"},
    "..."
  ],
  "updated": "2026-04-09"
}
```

---

## 5. get_news
**입력**: `{"ticker": "005930"}`
**출력**:
```json
{
  "ticker": "005930",
  "count": 10,
  "items": [
    {"date": "20260410", "time": "183517", "title": "10일, 기관 거래소에서 SK하이닉스(+2.91%), 삼성전자(+0.98%) 등 순매도", "source": "한국경제신문"},
    {"date": "20260410", "time": "172929", "title": "[카드뉴스] 삼성전자, 베트남에 6조 원 규모 '반도체 패키징' 거점 구축", "source": "인포스탁"},
    "..."
  ]
}
```

---

## 6. get_market_signal
**입력**: `{"mode": "short_sale", "ticker": "005930", "days": 5}`
**출력**:
```json
{
  "ticker": "005930",
  "market": "KR",
  "count": 5,
  "items": [
    {"date": "20260410", "short_vol": 770766, "total_vol": 18244459, "short_ratio": 4.22, "close": 206000},
    {"date": "20260409", "short_vol": 1259030, "total_vol": 42320839, "short_ratio": 2.97, "close": 204000},
    {"date": "20260408", "short_vol": 1064040, "total_vol": 35890973, "short_ratio": 2.96, "close": 210500},
    {"date": "20260407", "short_vol": 2938105, "total_vol": 30848053, "short_ratio": 9.52, "close": 196500},
    {"date": "20260406", "short_vol": 1446968, "total_vol": 20635958, "short_ratio": 7.01, "close": 193100}
  ]
}
```

---

## 7. get_macro
**입력**: `{}`
**출력**:
```json
{
  "kospi": {"index": "5858.87", "chg": "1.40"},
  "kosdaq": {"index": "1093.63", "chg": "1.64"},
  "usd_krw": {"price": 1482.7, "chg_pct": 0.55}
}
```
> 기본 모드는 간략 지수 + 환율. mode="dashboard" 시 전체 매크로 대시보드 반환.

---

## 8. get_sector
**입력**: `{"mode": "flow"}`
**출력**:
```json
{
  "date": "20260411",
  "top_inflow": [
    {"sector": "반도체", "frgn": 0, "orgn": 0},
    {"sector": "조선", "frgn": 0, "orgn": 0},
    {"sector": "전력기기", "frgn": 0, "orgn": 0}
  ],
  "top_outflow": [
    {"sector": "바이오", "frgn": 0, "orgn": 0},
    {"sector": "건설", "frgn": 0, "orgn": 0},
    {"sector": "2차전지", "frgn": 0, "orgn": 0}
  ],
  "note": "장중 업종별 수급 데이터 미제공 — ETF 시세로 섹터 동향을 확인하세요.",
  "etf_prices": [
    {"code": "140710", "name": "KODEX 조선", "price": "6850", "chg": "3.47"},
    {"code": "305720", "name": "KODEX 2차전지", "price": "18120", "chg": "-0.33"},
    {"code": "469150", "name": "TIGER AI반도체", "price": "48155", "chg": "1.65"},
    "..."
  ]
}
```

---

## 9. get_finance_rank
**입력**: `{"mode": "roe", "n": 3}`
**출력**:
```json
{
  "sort": "수익성",
  "year": "2025",
  "quarter": "3",
  "count": 3,
  "stocks": [
    {
      "rank": 1,
      "ticker": "033560",
      "name": "블루콤",
      "price": 3505,
      "chg_pct": 1.89,
      "capital_profit_rate": 313.42,
      "capital_net_rate": 8.14,
      "sales_gross_rate": 65.26,
      "sales_net_rate": 281.86,
      "equity_ratio": 84.7,
      "debt_ratio": 18.06,
      "revenue_growth": 14.09,
      "net_profit_growth": 589.01
    },
    "..."
  ]
}
```

---

## 10. get_highlow
**입력**: `{"mode": "high", "n": 3}`
**출력**:
```json
{
  "mode": "52주 신고가 근접",
  "gap_range": "0%~10%",
  "count": 3,
  "stocks": [
    {"rank": 1, "ticker": "000300", "name": "DH오토넥스", "price": 4200, "chg_pct": 0.0, "new_high": 4200, "high_gap_pct": 0.0},
    {"rank": 2, "ticker": "001470", "name": "삼부토건", "price": 347, "chg_pct": 0.0, "new_high": 347, "high_gap_pct": 0.0},
    {"rank": 3, "ticker": "001570", "name": "금양", "price": 9900, "chg_pct": 0.0, "new_high": 9900, "high_gap_pct": 0.0}
  ]
}
```

---

## 11. get_broker
**입력**: `{"ticker": "005930"}`
**출력**:
```json
{
  "ticker": "005930",
  "buy_members": [
    {"name": "KB증권", "volume": 3045093, "ratio": 16.69},
    {"name": "골드만", "volume": 1807458, "ratio": 9.91},
    {"name": "미래에셋증권", "volume": 1597872, "ratio": 8.76},
    {"name": "BNK증권", "volume": 1186662, "ratio": 6.5},
    {"name": "NH투자증권", "volume": 1087331, "ratio": 5.96}
  ],
  "sell_members": [
    {"name": "미래에셋증권", "volume": 2155482, "ratio": 11.81},
    {"name": "한국증권", "volume": 1814155, "ratio": 9.94},
    {"name": "KB증권", "volume": 1490396, "ratio": 8.17},
    {"name": "신한증권", "volume": 1297022, "ratio": 7.11},
    {"name": "BNK증권", "volume": 1195675, "ratio": 6.55}
  ]
}
```

---

## 12. get_scan
**입력**: `{"preset": "value", "n": 3}`
**출력**:
```json
{
  "date": "20260407",
  "preset": "value",
  "preset_description": "PER>0 AND PER<10 AND PBR>0 AND PBR<1 AND 시총>1000억 (저평가)",
  "total_matched": 272,
  "count": 3,
  "results": [
    {"ticker": "460850", "name": "동국씨엠", "market": "kospi", "close": 5210, "chg_pct": -1.14, "market_cap": 1558, "per": 2.44, "pbr": 0.15},
    {"ticker": "151860", "name": "KG에코솔루션", "market": "kosdaq", "close": 6020, "chg_pct": 0.0, "market_cap": 2866, "per": 3.21, "pbr": 0.19},
    {"ticker": "011370", "name": "서한", "market": "kosdaq", "close": 1009, "chg_pct": -1.27, "market_cap": 1018, "per": 5.15, "pbr": 0.19}
  ]
}
```

---

## 13. get_change_scan
**입력**: `{"preset": "golden_cross", "n": 3}`
**출력**:
```json
{
  "date": "20260407",
  "preset": "golden_cross",
  "preset_description": "골든크로스(MA5>MA20전환)",
  "sort": "ma_spread",
  "market": "all",
  "total_matched": 79,
  "count": 3,
  "results": [
    {
      "ticker": "402490",
      "name": "그린리소스",
      "market": "kosdaq",
      "close": 14800,
      "chg_pct": -0.67,
      "market_cap": 2452,
      "per": 104.96,
      "pbr": 4.12,
      "rsi14": 66.44,
      "ma_spread": 30.33,
      "volume_ratio_5d": 0.79,
      "w52_position": 0.763
    },
    "..."
  ]
}
```

---

## 14. get_backtest
**입력**: `{"ticker": "005930", "strategy": "ma_cross"}`
**출력**:
```json
{
  "ticker": "005930",
  "market": "KR",
  "strategy": "ma_cross",
  "period": "D250",
  "date_range": "20250115~20260410",
  "total_return_pct": 128.41,
  "benchmark_bh_pct": 283.2,
  "alpha_pct": -154.79,
  "win_rate": 33.3,
  "trade_count": 9,
  "wins": 3,
  "losses": 6,
  "max_drawdown_pct": 15.86,
  "avg_hold_days": 22.0,
  "costs": {"buy_pct": 0.115, "sell_pct": 0.295, "note": "한국: 수수료+거래세+슬리피지"},
  "trades": [
    {"entry_date": "20250319", "entry_price": 57466.01, "exit_date": "20250407", "exit_price": 53142.76, "pnl_pct": -7.52, "hold_days": 13},
    "..."
  ]
}
```

---

## 15. get_regime
**입력**: `{}`
**출력**:
```json
{
  "regime": "🟡 중립",
  "regime_en": "neutral",
  "indicators": {
    "sp500_vs_200ma": {"price": 6816.89, "sma200": 6662.62, "distance_pct": 2.32, "sma200_slope": "rising", "signal": "🟡"},
    "vix": {"value": 19.23, "vix3m": 21.86, "term_ratio": 0.8797, "backwardation": false, "signal": "🟢"},
    "usd_krw": {"value": 1482.7, "note": "참고용 (레짐 판정에 미사용)"}
  },
  "tranche_level": null,
  "debounce": {"current": "neutral", "days": 6, "confirmed": true, "text": "🟡 중립 6일차 (확정)"},
  "logic": "S&P +2.32% from 200MA AND VIX 19.2 AND → 🟡 Neutral",
  "date": "2026-04-11"
}
```

---

## 16. get_portfolio
**입력**: `{}`
**출력**:
```json
{
  "kr": {
    "holdings": [
      {"ticker": "000660", "name": "SK하이닉스", "qty": 4, "avg_price": 851000, "cur_price": 1027000, "eval_amt": 4108000, "pnl": 704000, "pnl_pct": 20.68, "chg_today": "2.91"},
      {"ticker": "005930", "name": "삼성전자", "qty": 18, "avg_price": 178100, "cur_price": 206000, "eval_amt": 3708000, "pnl": 502200, "pnl_pct": 15.67, "chg_today": "0.98"},
      {"ticker": "009540", "name": "HD조선해양", "qty": 50, "avg_price": 413590, "cur_price": 394000, "eval_amt": 19700000, "pnl": -979500, "pnl_pct": -4.74},
      "..."
    ]
  },
  "us": {
    "holdings": [
      "..."
    ]
  },
  "cash_krw": 12000000,
  "cash_usd": 5000.0
}
```

---

## 17. get_portfolio_history
**입력**: `{"days": 5, "brief": true}`
**출력**:
```json
{
  "days": 5,
  "snapshot_count": 4,
  "snapshots": [
    {"date": "2026-04-06", "total_asset_krw": 63674899, "cash_weight_pct": 24.7, "kr_eval": 35939800, "us_eval_krw": 12032900},
    {"date": "2026-04-07", "total_asset_krw": 63908666, "cash_weight_pct": 24.5, "kr_eval": 36258000, "us_eval_krw": 11964628},
    {"date": "2026-04-08", "total_asset_krw": 65869448, "cash_weight_pct": 23.4, "kr_eval": 38560000, "us_eval_krw": 11867131},
    {"date": "2026-04-09", "total_asset_krw": 66312734, "cash_weight_pct": 23.4, "kr_eval": 38282000, "us_eval_krw": 12524928}
  ],
  "drawdown": {
    "snapshot_count": 8,
    "weekly_return_pct": 6.05,
    "consecutive_stops": 1,
    "trading_suspended": false,
    "cash_weight_pct": 23.4,
    "alerts": []
  }
}
```

---

## 18. get_trade_stats
**입력**: `{}`
**출력**:
```json
{
  "period": "2026-04",
  "total_trades": 0,
  "wins": 0,
  "losses": 0,
  "win_rate_pct": null,
  "total_pnl": 0,
  "avg_pnl_per_trade": null,
  "best_trade": null,
  "worst_trade": null,
  "avg_holding_days": null,
  "grade_accuracy": {},
  "consecutive_losses": 0,
  "trades": []
}
```

---

## 19. simulate_trade
**입력**: `{"buys": [{"ticker": "005930", "qty": 1}]}`
**출력**:
```json
{
  "trades": ["매수 005930 1주 @206,000원"],
  "kr_holdings": [
    {"ticker": "009540", "name": "HD조선해양", "qty": 50, "eval": 19700000, "weight_pct": 29.3},
    {"ticker": "005930", "name": "삼성전자", "qty": 19, "eval": 3914000, "weight_pct": 5.8},
    "..."
  ],
  "us_holdings": [
    {"ticker": "AMD", "name": "AMD", "qty": 17, "eval": 4165.68, "weight_pct": 9.2},
    "..."
  ],
  "total_krw": 67000000,
  "cash_krw_after": 11794000
}
```

---

## 20. get_alerts
**입력**: `{"brief": true}`
**출력**:
```json
{
  "alerts": [
    {"ticker": "009540", "name": "HD조선해양", "gap_pct": -16.24, "target_pct": 42.13},
    {"ticker": "298040", "name": "효성중공업", "gap_pct": -25.17, "target_pct": 12.24},
    {"ticker": "000660", "name": "SK하이닉스", "gap_pct": -26.97, "target_pct": 46.06},
    {"ticker": "005930", "name": "삼성전자", "gap_pct": -24.76, "target_pct": 45.63},
    {"ticker": "CRSP", "name": "CRISPR Therapeutics", "gap_pct": -31.67, "target_pct": 46.43},
    {"ticker": "AMD", "name": "AMD", "gap_pct": -28.58, "target_pct": 14.27},
    "..."
  ]
}
```
> gap_pct = 손절가까지 거리(%), target_pct = 목표가까지 거리(%)

---

## 21. set_alert
**스킵 (데이터 변경)**
**스키마**:
```json
{
  "name": "set_alert",
  "description": "손절가/목표가 등록, 매수감시, 투자판단 기록, 매매기록, 알림삭제. log_type으로 모드 선택",
  "inputSchema": {
    "properties": {
      "log_type": {"type": "string", "description": "모드: 생략=stop/buy, 'decision'=투자판단, 'compare'=종목비교, 'trade'=매매기록, 'delete'=알림삭제"},
      "ticker": {"type": "string"},
      "name": {"type": "string"},
      "stop_price": {"type": "number", "description": "손절가"},
      "target_price": {"type": "number", "description": "목표가"},
      "buy_price": {"type": "number", "description": "매수감시 희망가"},
      "market": {"type": "string", "description": "KR 또는 US"}
    },
    "required": ["ticker"]
  }
}
```

---

## 22. manage_watch
**스킵 (데이터 변경)**
**스키마**:
```json
{
  "name": "manage_watch",
  "description": "워치리스트 관리. action별: add=종목추가(변동이력자동기록), remove=종목제거",
  "inputSchema": {
    "properties": {
      "action": {"type": "string", "enum": ["add", "remove"]},
      "ticker": {"type": "string", "description": "종목코드 또는 미국 티커"},
      "name": {"type": "string", "description": "종목명 (add 시 필수)"},
      "alert_type": {"type": "string", "description": "remove 시 삭제 대상: 'watchlist'(기본) 또는 'buy_alert'"}
    },
    "required": ["action", "ticker"]
  }
}
```

---

## 23. manage_report
**입력**: `{"action": "list", "ticker": "005930", "brief": true}`
**출력**:
```json
{
  "count": 10,
  "days": 7,
  "reports": [
    {"date": "2026-04-09", "ticker": "005930", "name": "삼성전자", "source": "유안타증권", "title": "1H26 예상 영업이익 139.4조원", "extraction_status": "meta_only"},
    {"date": "2026-04-08", "ticker": "005930", "name": "삼성전자", "source": "한화투자증권", "title": "심각한 숏티지, 더 강력해지는 이익 체력", "extraction_status": "partial"},
    {"date": "2026-04-08", "ticker": "005930", "name": "삼성전자", "source": "IBK투자증권", "title": "판도가 바뀌었다!", "extraction_status": "partial"},
    "..."
  ]
}
```

---

## 24. get_dart
**입력**: `{}` (기본: 워치리스트 최근 공시)
**출력**:
```json
[]
```
> 공시가 없거나 이미 알림 발송된 경우 빈 배열 반환.

---

## 25. backup_data
**입력**: `{"action": "status"}`
**출력**:
```json
{
  "ok": true,
  "gist_id": "dcf25df9a4af59e68efa9331bb90cf36",
  "updated_at": "2026-04-10T13:00:03Z",
  "description": "stock-bot /data/ backup 2026-04-10 22:00 KST",
  "files": [
    "consensus_cache.json",
    "decision_log.json",
    "portfolio.json",
    "portfolio_history.json",
    "regime_state.json",
    "reports.json",
    "stoploss.json",
    "trade_log.json",
    "us_watchlist.json",
    "watchalert.json",
    "watchlist.json",
    "watchlist_log.json"
  ],
  "file_count": 12
}
```

---

## 26. read_file
**입력**: `{"path": "data/TODO.md"}`
**출력** (앞 5줄):
```
# TODO — 2026-04-11 최종
> 레포: ysjms11/stock-bot | 서버: 맥미니 M4 + Cloudflare Tunnel (arcbot-server.org)

---

## 🔴 즉시 (다음 세션)
```
> 경로는 stock-bot 디렉토리 기준 상대경로. ../ 차단. 허용: .md/.py/.json/.txt (100KB 제한)

---

## 27. write_file
**스킵 (데이터 변경)**
**스키마**:
```json
{
  "name": "write_file",
  "description": "stock-bot 디렉토리 내 파일 쓰기. 허용 확장자: .md/.json/.txt (.py/.env 불가), 최대 200KB. ../ 경로 차단.",
  "inputSchema": {
    "properties": {
      "path": {"type": "string", "description": "stock-bot 디렉토리 기준 상대경로 (예: TODO.md, data/events.json)"},
      "content": {"type": "string", "description": "파일에 쓸 내용"}
    },
    "required": ["path", "content"]
  }
}
```

---

## 28. list_files
**입력**: `{"path": "data"}`
**출력**:
```json
{
  "path": "data",
  "entries": [
    {"name": "FILES.md", "size": 3652, "modified": "2026-04-11 17:11", "type": "file"},
    {"name": "HANDOVER.md", "size": 5547, "modified": "2026-04-10 12:00", "type": "file"},
    {"name": "TODO.md", "size": 4417, "modified": "2026-04-11 16:52", "type": "file"},
    {"name": "bot_guide.md", "size": 10320, "modified": "2026-04-11 17:11", "type": "file"},
    {"name": "consensus_cache.json", "size": 180000, "modified": "2026-04-11 xx:xx", "type": "file"},
    {"name": "portfolio.json", "size": 2800, "modified": "2026-04-10 xx:xx", "type": "file"},
    "..."
  ]
}
```

---

## 29. git_status
**입력**: `{}`
**출력**:
```json
{
  "branch": "main",
  "clean": false,
  "staged": [],
  "modified": [],
  "untracked": [
    "data/INVESTMENT_RULES.md",
    "data/bot_scenarios.md",
    "data/regime_update_notes.md",
    "data/research/"
  ]
}
```

---

## 30. git_diff
**입력**: `{}`
**출력**:
```json
{
  "staged": false,
  "path": null,
  "diff": "",
  "truncated": false
}
```

---

## 31. git_log
**입력**: `{"n": 3}`
**출력**:
```json
{
  "n": 3,
  "path": null,
  "commits": [
    {"hash": "7b00f92", "message": "docs: 문서 4개 전체 업데이트 — 33개 도구/알림/섹터/파일 동기화"},
    {"hash": "0c1c751", "message": "feat: Git MCP 도구 5개 추가 — Claude에서 커밋/push 가능"},
    {"hash": "053c2cd", "message": "feat: 섹터명 보강 — KRX 29개 업종 → 92개 실용 섹터 분류"}
  ]
}
```

---

## 32. git_commit
**스킵 (데이터 변경)**
**스키마**:
```json
{
  "name": "git_commit",
  "description": "지정한 파일을 staging하고 커밋. .py/.env 파일은 커밋 불가. message는 최대 500자.",
  "inputSchema": {
    "properties": {
      "message": {"type": "string", "description": "커밋 메시지 (최대 500자)"},
      "files": {"type": "array", "items": {"type": "string"}, "description": "staging할 파일 경로 목록 (.py/.env 불가)"}
    },
    "required": ["message", "files"]
  }
}
```

---

## 33. git_push
**스킵 (데이터 변경)**
**스키마**:
```json
{
  "name": "git_push",
  "description": "origin main 브랜치에 push. main 브랜치일 때만 허용.",
  "inputSchema": {
    "properties": {},
    "required": []
  }
}
```

---

## 요약 메모

| 도구 | 특이사항 |
|------|----------|
| get_rank | 장중 등락률 미제공 → KRX DB 기반 get_scan 사용 |
| get_supply history | 장중에는 빈 배열 → estimate 모드 사용 |
| get_sector flow | 장중 수급 미제공 → ETF 시세로 근사 |
| get_dart | 이미 알림 발송된 공시는 제외 → 빈 배열 가능 |
| get_trade_stats | 이번 달 거래 없으면 모두 null |
| read_file | 경로는 data/TODO.md 처럼 stock-bot 루트 기준 상대경로 |
| set_alert / manage_watch / write_file / git_commit / git_push | 데이터 변경 도구 — 스키마만 수록 |

---

# 데이터 파일 구조
> 각 파일의 실제 레코드 1개 샘플. 전체 필드 확인용.

## KRX DB 레코드 (1종목)
> data/krx_db/YYYYMMDD.json → stocks.{ticker} (전체 필드)
```json
{
  "ticker": "005930",
  "name": "삼성전자",
  "market": "kospi",
  "close": 196500,
  "chg_pct": 1.76,
  "volume": 30848053,
  "trade_value": 6062043969600,
  "market_cap": 1163208851673000,
  "per": 39.7,
  "pbr": 3.39,
  "eps": 4950.0,
  "bps": 57951.0,
  "div_yield": 0.74,
  "foreign_net_qty": -2727272,
  "foreign_net_amt": -538002024900,
  "inst_net_qty": 577919,
  "inst_net_amt": 112930735950,
  "indiv_net_qty": 275532,
  "indiv_net_amt": 58473003200,
  "foreign_hold_ratio": 0.0,
  "foreign_exhaust_rate": 0.0,
  "credit_balance": 0,
  "consensus_target": 268720,
  "consensus_count": 25,
  "consensus_gap": 36.8,
  "short_balance": 0,
  "short_ratio": 0,
  "lending_balance": 0,
  "sector_name": "반도체",
  "list_shares": 0,
  "turnover": 0.5211,
  "foreign_ratio": -0.0463,
  "inst_ratio": 0.0097,
  "fi_ratio": -0.0365,
  "ma5": 188760.0,
  "ma10": 183610.0,
  "ma20": 188225.0,
  "ma60": 174661.67,
  "ma120": 140016.67,
  "ma200": 111925.0,
  "rsi14": 65.29,
  "bb_upper": 206494.0,
  "bb_lower": 169956.0,
  "ma_spread": 8.07,
  "w52_high": 218000,
  "w52_low": 53900,
  "w52_position": 0.869,
  "ytd_return": 63.89,
  "sector_rel_strength": null,
  "volume_ratio_5d": 0.98,
  "volume_ratio_20d": 0.72,
  "volume_ratio_10d": 1.2,
  "ma_spread_change_10d": -11.5,
  "ma_spread_change_30d": -30.12,
  "rsi_change_5d": 4.48,
  "rsi_change_20d": 0.95,
  "eps_change_90d": 0.0,
  "earnings_gap": -63.89,
  "foreign_trend_5d": 0.4,
  "foreign_trend_20d": 0.2,
  "foreign_trend_60d": 0.2833,
  "short_change_5d": null,
  "short_change_20d": null,
  "credit_change_5d": null,
  "credit_change_20d": null,
  "foreign_hold_change_5d": null,
  "vp_poc_60d": 187850,
  "vp_va_high_60d": 193880,
  "vp_va_low_60d": 137600,
  "vp_position_60d": 1.0466,
  "vp_poc_250d": 58002,
  "vp_va_high_250d": 193385,
  "vp_va_low_250d": 53900,
  "vp_position_250d": 1.0223,
  "vp_poc": 58002,
  "vp_va_high": 193385,
  "vp_va_low": 53900,
  "vp_position": 1.0223,
  "sector_rank": null,
  "sector_krx": "전기·전자"
}
```

---

## portfolio.json
> {ticker: {name, qty, avg_price}, us_stocks: {ticker: {...}}, cash_krw, cash_usd}
```json
{
  "000660": {"name": "SK하이닉스", "qty": 4, "avg_price": 851000},
  "005930": {"name": "삼성전자", "qty": 18, "avg_price": 178100},
  "...": "...",
  "us_stocks": {
    "NVDA": {"name": "NVIDIA", "qty": 12, "avg_price": 183.68},
    "AMD": {"name": "AMD", "qty": 17, "avg_price": 201.67},
    "...": "..."
  },
  "cash_krw": 4112719.0,
  "cash_usd": 5491.48
}
```

---

## watchalert.json
> {ticker: {name, buy_price, memo, grade, market, created_at, updated_at, created}}
```json
{
  "103140": {
    "name": "풍산",
    "buy_price": 85000.0,
    "memo": "방산탄약+구리 듀얼. 11명전원매수 목표156K(+80%). PER10배...",
    "grade": "",
    "market": "KR",
    "created_at": "2026-04-01",
    "updated_at": "",
    "created": "2026-04-01 06:24"
  }
}
```

---

## stoploss.json
> {ticker: {name, stop_price, entry_price, target_price}, us_stocks: {ticker: {...}}}
```json
{
  "009540": {
    "name": "HD조선해양",
    "stop_price": 330000.0,
    "entry_price": 0,
    "target_price": 560000.0
  },
  "us_stocks": {
    "NVDA": {
      "name": "NVIDIA",
      "stop_price": 140.0,
      "target_price": 274.0
    }
  }
}
```

---

## decision_log.json
> {날짜: {date, regime, grades, actions, watchlist, notes, saved_at}}
```json
{
  "2026-03-18": {
    "date": "2026-03-18",
    "regime": "경계",
    "grades": {
      "HD한국조선해양": "A",
      "효성중공업": "B",
      "LS ELECTRIC": "A",
      "AMD": "B",
      "CRSP": "C"
    },
    "actions": [
      "LS 목표가 850000→970000 봇 업데이트 완료",
      "LS 2주 시간외 익절 완료 (833,000)"
    ],
    "watchlist": [
      "NVDA $155 (AI GPU1위, RR1:2.3)",
      "풍산 100,000"
    ],
    "notes": "야간점검: 폭등일(KOSPI+5%) 추격매수 금지.",
    "saved_at": "2026-03-18 19:24"
  }
}
```

---

## trade_log.json
> {trades: [{id, ticker, name, market, side, qty, price, date, grade_at_trade, reason, target_price, linked_buy_id}]}
```json
{
  "trades": [
    {
      "id": "T004",
      "ticker": "NVDA",
      "name": "NVIDIA",
      "market": "US",
      "side": "buy",
      "qty": 12,
      "price": 183.68,
      "date": "2026-04-10",
      "grade_at_trade": "A",
      "reason": "FwdPE 21.9x 동종대비 20%할인, PEG 0.32, Big4 CapEx $600B+ 사이클",
      "target_price": 274.0,
      "linked_buy_id": null
    }
  ]
}
```

---

## events.json
> {이벤트명: "YYYY-MM-DD"} — 매크로 이벤트 캘린더
```json
{
  "FOMC": "2026-04-28",
  "CPI": "2026-04-10",
  "PPI": "2026-04-11"
}
```

---

## portfolio_history.json
> [{date, total_eval_krw, cash_krw, cash_usd, usd_krw_rate, total_asset_krw, kr_eval, us_eval_krw, holdings, cash_weight_pct}]
```json
{
  "date": "2026-04-09",
  "total_eval_krw": 50806928,
  "cash_krw": 4112719,
  "cash_usd": 7695.64,
  "usd_krw_rate": 1480.5,
  "total_asset_krw": 66312734,
  "kr_eval": 38282000,
  "us_eval_krw": 12524928,
  "holdings": {
    "009540": {"price": 391000, "qty": 50, "eval": 19550000, "weight_pct": 29.5},
    "NVDA": {"price": 0, "qty": 12, "eval_usd": 0, "weight_pct": 0.0},
    "...": "..."
  },
  "cash_weight_pct": 23.4
}
```

---

## consensus_cache.json
> {updated: ISO8601, kr: {ticker: {name, avg, high, low, buy, hold, sell, prev_avg}}}
```json
{
  "updated": "2026-04-05T07:05:32+0900",
  "kr": {
    "000660": {
      "name": "SK하이닉스",
      "avg": 1360800,
      "high": 1700000,
      "low": 970000,
      "buy": 25,
      "hold": 0,
      "sell": 0
    },
    "005930": {
      "name": "삼성전자",
      "avg": 258320,
      "high": 320000,
      "low": 190000,
      "buy": 25,
      "hold": 0,
      "sell": 0
    }
  }
}
```

---

## regime_state.json
> {history: [{date, regime, ...scores}], current: {current, days_in_regime, confirmed, indicators}}
```json
{
  "history": [
    {"date": "2026-04-09", "regime": "neutral", "sp_distance_pct": -0.51, "vix": 21.37},
    {"date": "2026-04-10", "regime": "neutral", "sp_distance_pct": 2.49, "vix": 19.49}
  ],
  "current": {
    "current": "neutral",
    "days_in_regime": 6,
    "debounce_count": 6,
    "confirmed": true,
    "tranche_level": null,
    "pending_regime": null,
    "last_updated": "2026-04-11",
    "indicators": {
      "sp500_vs_200ma": {"price": 6816.89, "sma200": 6662.62, "distance_pct": 2.32, "sma200_slope": "rising", "signal": "🟡"},
      "vix": {"value": 19.23, "vix3m": 21.86, "term_ratio": 0.8797, "backwardation": false, "signal": "🟢"},
      "usd_krw": {"value": 1482.7, "note": "참고용 (레짐 판정에 미사용)"}
    }
  }
}
```

---

## std_sector_map.json
> {ticker: {std_code, std_name}} — KRX 표준산업분류 매핑
```json
{
  "006840": {"std_code": "116409", "std_name": "기타 금융업"},
  "282330": {"std_code": "074701", "std_name": "종합 소매업"},
  "000120": {"std_code": "084903", "std_name": "도로 화물 운송업"}
}
```

---

## reports.json
> {reports: [{date, ticker, name, source, title, pdf_url, full_text, extraction_status, collected_at}]}
```json
{
  "reports": [
    {
      "date": "2026-04-10",
      "ticker": "298040",
      "name": "효성중공업",
      "source": "SK증권",
      "title": "765kV 모멘텀 본격화",
      "pdf_url": "https://consensus.hankyung.com/analysis/downpdf?report_idx=648267",
      "full_text": "2026-04-10\n효성중공업 (298040/KS)\n765kV 모멘텀 본격화...(전문)",
      "extraction_status": "success",
      "collected_at": "2026-04-11T02:19:17.118218+09:00"
    }
  ]
}
```

---

## 신규 API 함수 (db_collector용)

### kis_overtime_daily
**입력**: `ticker="005930", token`
**출력**:
```json
{"ovtm_close": 206000, "ovtm_change_pct": 0.0, "ovtm_volume": 0}
```

### kis_income_statement
**입력**: `ticker="005930", token`
**출력**:
```json
[{"report_period": "202512", "revenue": 3336059.0, "cost_of_sales": 2022355.0, "gross_profit": 1313704.0, "operating_profit": 436011.0, "op_profit": 494815.0, "net_income": 452068.0}]
```

### kis_balance_sheet
**입력**: `ticker="005930", token`
**출력**:
```json
[{"report_period": "202512", "current_assets": 2476846.0, "fixed_assets": 3192575.0, "total_assets": 5669421.0, "current_liab": 1064113.0, "fixed_liab": 242104.0, "total_liab": 1306218.0, "capital": 8975.0, "total_equity": 4363203.0}]
```

---

## data/research/NVDA/main.md (파일 포맷 예시)
> 종목별 리서치 메모. 2단계 계층(4/18~): `data/research/{TICKER}_{NAME}/{종류}_{YYYYMMDD}.md` 또는 `main.md`. 마크다운 자유형식.
```markdown
# NVIDIA (NVDA) — 확신등급 A

## 포지션
- 매수일: 2026-04-10
- 수량: 12주 @ $183.68 (투자원금 $2,204)
- 손절: $140 (-24%) | 목표: $274 (+49%)

## 핵심 Thesis (이게 깨지면 판다)
1. AI 인프라 독점: CUDA 생태계(개발자400만+, 앱3000+) 대체 불가. 학습 점유율 90%+
2. CapEx 슈퍼사이클: Big4 합산 $588~640B (YoY +63~78%). 수요>공급 상태 지속
3. 제품 전환 모멘텀: Blackwell 풀스로틀 + Rubin H2 2026 양산. 백로그 $1T

## 밸류에이션 (2026-04-10 기준)
| 지표 | 수치 |
|------|------|
| FY2027E 매출 | $360.7B (+67%) |
| FY2027E EPS | $8.30 (GAAP) |
| FwdPE (FY2027) | 21.9x (동종 중위수 26.8x 대비 20% 할인) |
| PEG (단년) | 0.32 |
| 애널리스트 컨센 | $274 (43명, Strong Buy) |
```
