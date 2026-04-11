# MCP 도구 입출력 샘플
> 업데이트: 2026-04-11 | 실제 호출 결과 (33개 도구)

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
