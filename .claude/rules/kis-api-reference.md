# KIS API 호출 패턴 & TR_ID 참조

## 호출 패턴

모든 신규 함수는 `_kis_get()` 래퍼 사용:

```python
async def kis_some_api(ticker: str, token: str) -> dict:
    async with aiohttp.ClientSession() as s:
        _, d = await _kis_get(s, "/uapi/domestic-stock/v1/...",
            "TR_ID_HERE", token, {"param1": "val1"})
        return d.get("output", {})
```

## 국내 TR_ID (31개)

| TR_ID | 용도 | 함수 |
|-------|------|------|
| `FHKST01010100` | 국내 현재가 | `kis_stock_price()` |
| `FHKST01010200` | 호가 잔량 (10호가) | `kis_asking_price()` |
| `FHKST01010600` | 신용잔고 / 거래원 | `kis_credit_balance()` / `kis_inquire_member()` |
| `FHKST01010700` | 공매도 | `kis_short_selling()` |
| `FHKST01010900` | 국내 수급 | `kis_investor_trend()` |
| `FHKST01011800` | 종목 뉴스 | `kis_news_title()` |
| `FHKST03010100` | 일봉 차트 | `kis_daily_volumes()` / `kis_daily_closes()` |
| `FHPST01390000` | VI 발동 | `kis_vi_status()` |
| `FHPST01680000` | 체결강도 상위 | `kis_volume_power_rank()` |
| `FHPST01700000` | 등락률 순위 | `kis_fluctuation_rank()` |
| `FHPST01710000` | 거래량 상위 | `kis_volume_rank_api()` |
| `FHPST01740000` | 시총 상위 | `fetch_universe_from_krx()` |
| `FHPST01750000` | 재무비율 순위 | `kis_finance_ratio_rank()` |
| `FHPST01860000` | 증권사별 매매종목 | `kis_traded_by_company()` |
| `FHPST01870000` | 52주 신고저 근접 | `kis_near_new_highlow()` |
| `FHPST02300000` | 시간외 현재가 | `kis_overtime_price()` |
| `FHPST02340000` | 시간외 등락률 | `kis_overtime_fluctuation()` |
| `FHPST04760000` | 신용잔고 일별추이 | `kis_daily_credit_balance()` |
| `FHPST04830000` | 공매도 일별추이 | `kis_daily_short_sale()` |
| `FHPTJ04060100` | 외국인 순매수 상위 | `kis_foreigner_trend()` |
| `FHPTJ04160001` | 투자자별 수급 히스토리 | `kis_investor_trend_history()` |
| `FHPTJ04400000` | 외인+기관 합산 | `kis_foreign_institution_total()` |
| `FHPUP02100000` | KOSPI/KOSDAQ 지수 | `get_kis_index()` |
| `FHKUP03500100` | 업종별 시세 | `kis_sector_price()` |
| `CTPF1002R` | 종목 기본정보 | `kis_stock_info()` |
| `HHPPG046600C1` | 프로그램매매 | `kis_program_trade_today()` |
| `HHPTJ04160200` | 장중 추정 수급 | `kis_investor_trend_estimate()` |
| `HHKST668300C0` | 종목추정실적 | `kis_estimate_perform()` |
| `HHPST074500C0` | 대차거래 일별추이 | `kis_daily_loan_trans()` |
| `HHKDB13470100` | 배당수익률 순위 | `kis_dividend_rate_rank()` |

## 해외 TR_ID (3개)

| TR_ID | 용도 | 함수 |
|-------|------|------|
| `HHDFS00000300` | 해외 현재가 | `kis_us_stock_price()` |
| `HHDFS76200200` | 해외 현재가상세 | `kis_us_stock_detail()` |
| `HHDFS76290000` | 해외 등락률 | `kis_us_updown_rate()` |

## 해외 응답 주요 필드

- 현재가: `last`(현재가), `rate`(등락률%), `tvol`(거래량), `base`(전일종가)
- 상세: `perx`(PER), `pbrx`(PBR), `epsx`(EPS), `tomv`(시총), `h52p`/`l52p`(52주고저)
