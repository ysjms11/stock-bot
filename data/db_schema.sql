-- stock-bot SQLite DB 스키마 v1.5 (F/M/FCF Phase3: daily_snapshot fscore/mscore/FCF 3종)
-- 5테이블 + 1뷰: stock_master + daily_snapshot + financial_quarterly + consensus_history + reports + v_daily_scan
-- 확정 API: FHKST01010100 / FHPTJ04160001 / FHPST04830000 / FHPST02320000 / FHKST66430200 / FHKST66430100

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━
-- 1. 종목 마스터
-- ━━━━━━━━━━━━━━━━━━━━━━━━━
CREATE TABLE IF NOT EXISTS stock_master (
    symbol          TEXT PRIMARY KEY,           -- 종목코드 6자리
    name            TEXT NOT NULL,              -- 종목명
    market          TEXT NOT NULL,              -- kospi / kosdaq
    sector          TEXT DEFAULT '',            -- 실용 섹터 (반도체/조선/전력기기 등 92개)
    sector_krx      TEXT DEFAULT '',            -- KRX 원본 업종 (전기·전자 등 29개)
    std_code        TEXT DEFAULT '',            -- 표준산업분류코드 6자리
    listing_shares  INTEGER DEFAULT 0,          -- 상장주식수
    updated_at      TEXT DEFAULT ''             -- 최종 갱신일시
);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━
-- 2. 일별 스냅샷 (스캔용 메인 테이블)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━
CREATE TABLE IF NOT EXISTS daily_snapshot (
    trade_date      TEXT NOT NULL,              -- YYYYMMDD
    symbol          TEXT NOT NULL,              -- 종목코드

    -- ── API 1: 기본시세+밸류 (FHKST01010100) ──
    close           INTEGER DEFAULT 0,          -- 종가 (stck_prpr)
    open            INTEGER DEFAULT 0,          -- 시가 (stck_oprc)
    high            INTEGER DEFAULT 0,          -- 고가 (stck_hgpr)
    low             INTEGER DEFAULT 0,          -- 저가 (stck_lwpr)
    change_pct      REAL DEFAULT 0,             -- 등락률% (prdy_ctrt)
    volume          INTEGER DEFAULT 0,          -- 거래량 (acml_vol)
    trade_value     INTEGER DEFAULT 0,          -- 거래대금 (acml_tr_pbmn)
    market_cap      INTEGER DEFAULT 0,          -- 시총 억원 (hts_avls)
    per             REAL DEFAULT 0,             -- PER
    pbr             REAL DEFAULT 0,             -- PBR
    eps             REAL DEFAULT 0,             -- EPS
    bps             REAL DEFAULT 0,             -- BPS
    div_yield       REAL DEFAULT 0,             -- 배당수익률
    w52_high        INTEGER DEFAULT 0,          -- 52주 최고 (w52_hgpr)
    w52_low         INTEGER DEFAULT 0,          -- 52주 최저 (w52_lwpr)
    foreign_own_pct REAL DEFAULT 0,             -- 외인보유비율% (hts_frgn_ehrt)
    listing_shares  INTEGER DEFAULT 0,          -- 상장주식수 (lstn_stcn)
    turnover        REAL DEFAULT 0,             -- 회전율%
    loan_balance_rate REAL DEFAULT 0,           -- 신용잔고비율% (whol_loan_rmnd_rate, kis_stock_price) (vol_tnrt)

    -- ── API 2: 투자자 수급 (FHPTJ04160001) ──
    foreign_net_qty INTEGER DEFAULT 0,          -- 외인 순매수 수량
    foreign_net_amt INTEGER DEFAULT 0,          -- 외인 순매수 금액
    inst_net_qty    INTEGER DEFAULT 0,          -- 기관 순매수 수량
    inst_net_amt    INTEGER DEFAULT 0,          -- 기관 순매수 금액
    indiv_net_qty   INTEGER DEFAULT 0,          -- 개인 순매수 수량
    indiv_net_amt   INTEGER DEFAULT 0,          -- 개인 순매수 금액

    -- ── API 3: 공매도 (FHPST04830000) ──
    short_volume    INTEGER DEFAULT 0,          -- 공매도 수량
    short_ratio     REAL DEFAULT 0,             -- 공매도 비중%

    -- ── API 4: 시간외 (FHPST02320000) ──
    ovtm_close      INTEGER DEFAULT 0,          -- 시간외 종가
    ovtm_change_pct REAL DEFAULT 0,             -- 시간외 등락률%
    ovtm_volume     INTEGER DEFAULT 0,          -- 시간외 거래량

    -- ── FnGuide: 컨센서스 ──
    consensus_target INTEGER DEFAULT 0,         -- 목표주가
    consensus_count  INTEGER DEFAULT 0,         -- 커버 증권사 수
    consensus_gap    REAL DEFAULT 0,            -- 괴리율%

    -- ── 계산: 이평선 ──
    ma5             REAL,
    ma10            REAL,
    ma20            REAL,
    ma60            REAL,
    ma120           REAL,
    ma200           REAL,
    ma_spread       REAL,                       -- (MA5-MA200)/MA200 * 100

    -- ── 계산: RSI / 볼린저 ──
    rsi14           REAL,
    bb_upper        REAL,
    bb_lower        REAL,
    bb_width        REAL,                       -- (bb_upper - bb_lower) / bb_mid

    -- ── 계산: MACD ──
    macd            REAL,                       -- MACD line
    macd_signal     REAL,                       -- Signal line
    macd_hist       REAL,                       -- Histogram

    -- ── 계산: 변동성 ──
    atr14           REAL,                       -- ATR(14)
    volatility_20d  REAL,                       -- 20일 변동성

    -- ── 계산: 52주 / YTD ──
    w52_position    REAL,                       -- (현재가-52주저) / (52주고-52주저)
    ytd_return      REAL,                       -- 연초대비 수익률%

    -- ── 계산: 매물대 (Volume Profile) ──
    vp_poc_60d      REAL,
    vp_va_high_60d  REAL,
    vp_va_low_60d   REAL,
    vp_position_60d REAL,
    vp_poc_250d     REAL,
    vp_va_high_250d REAL,
    vp_va_low_250d  REAL,
    vp_position_250d REAL,

    -- ── 계산: 추세 점수 ──
    volume_ratio_5d  REAL,
    volume_ratio_10d REAL,
    volume_ratio_20d REAL,
    ma_spread_change_10d REAL,
    ma_spread_change_30d REAL,
    rsi_change_5d   REAL,
    rsi_change_20d  REAL,
    eps_change_90d  REAL,
    earnings_gap    REAL,

    -- ── 계산: 수급 추세 ──
    foreign_trend_5d  REAL,
    foreign_trend_20d REAL,
    foreign_trend_60d REAL,
    foreign_ratio     REAL,                     -- 외인순매수/시총 %
    inst_ratio        REAL,                     -- 기관순매수/시총 %
    fi_ratio          REAL,                     -- (외인+기관)/시총 %

    -- ── 계산: 공매도 변화 ──
    short_change_5d   REAL,
    short_change_20d  REAL,

    -- ── 계산: 섹터 ──
    sector_rel_strength REAL,                   -- 종목등락률 - 섹터평균
    sector_rank         INTEGER,                -- 섹터 내 순위

    -- ── 계산: 재무 파생 (financial_quarterly 최신 분기 기반) ──
    revenue             REAL,                   -- 최신 분기 매출
    operating_profit    REAL,                   -- 최신 분기 영업이익
    net_income          REAL,                   -- 최신 분기 순이익
    total_assets        REAL,                   -- 최신 분기 자산총계
    total_liabilities   REAL,                   -- 최신 분기 부채총계
    total_equity        REAL,                   -- 최신 분기 자본총계
    operating_margin    REAL,                   -- 영업이익률%
    net_margin          REAL,                   -- 순이익률%
    debt_ratio          REAL,                   -- 부채비율%
    roe                 REAL,                   -- ROE%
    revenue_growth      REAL,                   -- 매출 성장률% (QoQ)
    op_growth           REAL,                   -- 영업이익 성장률% (QoQ)

    -- ── F/M/FCF Phase3 (알파 메트릭) ──
    fscore              INTEGER,                -- Piotroski F-Score (0~9)
    mscore              REAL,                   -- Beneish M-Score
    fcf_to_assets       REAL,                   -- FCF / 총자산 (%)
    fcf_yield_ev        REAL,                   -- FCF / EV (%)
    fcf_conversion      REAL,                   -- FCF / 순이익 (%)

    -- ── 확장 여유 (ALTER TABLE 없이 지표 추가용) ──
    tech_01             REAL,
    tech_02             REAL,
    tech_03             REAL,
    tech_04             REAL,
    tech_05             REAL,
    tech_06             REAL,
    tech_07             REAL,
    tech_08             REAL,
    tech_09             REAL,
    tech_10             REAL,
    tech_11             REAL,
    tech_12             REAL,
    tech_13             REAL,
    tech_14             REAL,
    tech_15             REAL,
    tech_16             REAL,
    tech_17             REAL,
    tech_18             REAL,
    tech_19             REAL,
    tech_20             REAL,

    -- ── 메타 ──
    collected_at    TEXT DEFAULT '',             -- 수집 시각

    PRIMARY KEY (trade_date, symbol),
    FOREIGN KEY (symbol) REFERENCES stock_master(symbol)
);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━
-- 3. 분기 재무 원본 (주 1회 수집)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━
CREATE TABLE IF NOT EXISTS financial_quarterly (
    symbol          TEXT NOT NULL,              -- 종목코드
    report_period   TEXT NOT NULL,              -- YYYYMM (예: 202512, 202509)

    -- ── 손익계산서 (FHKST66430200) ──
    revenue         REAL,                       -- 매출액 (sale_account)
    cost_of_sales   REAL,                       -- 매출원가 (sale_cost)
    gross_profit    REAL,                       -- 매출총이익 (sale_totl_prfi)
    operating_profit REAL,                      -- 영업이익 (bsop_prti)
    op_profit       REAL,                       -- 경상이익 (op_prfi)
    net_income      REAL,                       -- 당기순이익 (thtr_ntin)

    -- ── 대차대조표 (FHKST66430100) ──
    current_assets  REAL,                       -- 유동자산 (cras)
    fixed_assets    REAL,                       -- 고정자산 (fxas)
    total_assets    REAL,                       -- 자산총계 (total_aset)
    current_liab    REAL,                       -- 유동부채 (flow_lblt)
    fixed_liab      REAL,                       -- 고정부채 (fix_lblt)
    total_liab      REAL,                       -- 부채총계 (total_lblt)
    capital         REAL,                       -- 자본금 (cpfn)
    total_equity    REAL,                       -- 자본총계 (total_cptl)

    -- ── 현금흐름표 / F·M·FCF 확장 (v1.4, DART fnlttSinglAcntAll) ──
    cfo             INTEGER,                    -- 영업활동 현금흐름 (원)
    capex           INTEGER,                    -- 유형자산 취득 (원, 절대값)
    fcf             INTEGER,                    -- CFO - abs(CapEx)
    depreciation    INTEGER,                    -- 감가상각비 + 무형자산상각비
    sga             INTEGER,                    -- 판매비와관리비
    receivables     INTEGER,                    -- 매출채권 (유동자산 중)
    inventory       INTEGER,                    -- 재고자산
    shares_out      INTEGER,                    -- 발행주식수 (보고 기준)
    net_income_parent INTEGER,                  -- 지배주주 귀속 순이익
    equity_parent   INTEGER,                    -- 지배주주 귀속 자본
    fs_source       TEXT,                       -- CFS / OFS / OFS_HOLDCO

    -- ── 메타 ──
    collected_at    TEXT DEFAULT '',             -- 수집 시각

    PRIMARY KEY (symbol, report_period),
    FOREIGN KEY (symbol) REFERENCES stock_master(symbol)
);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━
-- 4. 컨센서스 히스토리 (매일/주간 누적)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━
CREATE TABLE IF NOT EXISTS consensus_history (
    trade_date    TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    target_avg    REAL,
    target_high   REAL,
    target_low    REAL,
    buy_count     INTEGER DEFAULT 0,
    hold_count    INTEGER DEFAULT 0,
    sell_count    INTEGER DEFAULT 0,
    collected_at  TEXT DEFAULT '',
    PRIMARY KEY (trade_date, symbol)
);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━
-- 5. 증권사 리포트 (영구 보관)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━
CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    name            TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    analyst         TEXT DEFAULT '',
    title           TEXT DEFAULT '',
    pdf_url         TEXT DEFAULT '',
    full_text       TEXT DEFAULT '',
    pdf_path        TEXT DEFAULT '',         -- 로컬 PDF 저장 경로
    extraction_status TEXT DEFAULT '',
    collected_at    TEXT DEFAULT '',
    UNIQUE(date, source, ticker)
);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━
-- 6. 내부자 거래 (DART elestock.json)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━
CREATE TABLE IF NOT EXISTS insider_transactions (
    rcept_no        TEXT NOT NULL,              -- DART 접수번호
    symbol          TEXT NOT NULL,              -- 종목코드
    corp_code       TEXT DEFAULT '',            -- DART 고유번호
    rcept_dt        TEXT NOT NULL,              -- 접수일자 (YYYY-MM-DD)
    repror          TEXT DEFAULT '',            -- 보고자 (임원 이름)
    ofcps           TEXT DEFAULT '',            -- 직위 (상무/이사/대표 등)
    rgist           TEXT DEFAULT '',            -- 등기/비등기 여부
    main_shrholdr   TEXT DEFAULT '',            -- 주요주주 여부
    stock_cnt       INTEGER DEFAULT 0,          -- 특정증권등 소유수 (총)
    stock_irds_cnt  INTEGER DEFAULT 0,          -- 소유수 증감 (+매수/-매도)
    stock_rate      REAL DEFAULT 0,             -- 소유비율%
    stock_irds_rate REAL DEFAULT 0,             -- 소유비율 증감%
    collected_at    TEXT DEFAULT '',
    PRIMARY KEY (rcept_no, repror),
    FOREIGN KEY (symbol) REFERENCES stock_master(symbol)
);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━
-- 미국 애널 레이팅
-- ━━━━━━━━━━━━━━━━━━━━━━━━━
CREATE TABLE IF NOT EXISTS us_analyst_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    rating_date TEXT NOT NULL,
    rating_time TEXT,
    firm TEXT,
    analyst TEXT,
    analyst_slug TEXT,
    action TEXT,
    rating_new TEXT,
    rating_old TEXT,
    pt_now REAL,
    pt_old REAL,
    pt_change_pct REAL,
    stars REAL,
    success_rate REAL,
    avg_return REAL,
    total_ratings INTEGER,
    fetched_at TEXT NOT NULL,
    UNIQUE(ticker, rating_date, rating_time, firm, analyst_slug)
);
CREATE INDEX IF NOT EXISTS idx_us_ratings_ticker_date ON us_analyst_ratings(ticker, rating_date DESC);
CREATE INDEX IF NOT EXISTS idx_us_ratings_slug_date ON us_analyst_ratings(analyst_slug, rating_date DESC);
CREATE INDEX IF NOT EXISTS idx_us_ratings_date ON us_analyst_ratings(rating_date DESC);

CREATE TABLE IF NOT EXISTS us_consensus_snapshot (
    ticker TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    analyst_count INTEGER,
    consensus_rating TEXT,
    target_avg REAL,
    PRIMARY KEY(ticker, snapshot_date)
);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━
-- 인덱스 (최소주의 — 느린 쿼리 확인 후 추가)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━
-- PK (trade_date, symbol)는 자동 인덱스

-- daily_snapshot: 날짜별 전종목 조회용
CREATE INDEX IF NOT EXISTS idx_ds_date ON daily_snapshot(trade_date);

-- stock_master: 시장/섹터 필터
CREATE INDEX IF NOT EXISTS idx_sm_market ON stock_master(market);
CREATE INDEX IF NOT EXISTS idx_sm_sector ON stock_master(sector);

-- financial_quarterly: 분기별 조회
CREATE INDEX IF NOT EXISTS idx_fq_period ON financial_quarterly(report_period);

-- consensus_history: 종목별 히스토리 조회
CREATE INDEX IF NOT EXISTS idx_ch_symbol ON consensus_history(symbol);

-- reports: 종목별·날짜별 조회
CREATE INDEX IF NOT EXISTS idx_rpt_ticker ON reports(ticker);
CREATE INDEX IF NOT EXISTS idx_rpt_date ON reports(date);

-- insider_transactions: 종목별·날짜별 클러스터 집계
CREATE INDEX IF NOT EXISTS idx_ins_symbol_date ON insider_transactions(symbol, rcept_dt);
CREATE INDEX IF NOT EXISTS idx_ins_date ON insider_transactions(rcept_dt);

-- 필요시 추가 후보 (데이터 수십만행 이상 시):
-- CREATE INDEX IF NOT EXISTS idx_ds_change ON daily_snapshot(trade_date, change_pct);
-- CREATE INDEX IF NOT EXISTS idx_ds_mcap ON daily_snapshot(trade_date, market_cap);
-- CREATE INDEX IF NOT EXISTS idx_ds_foreign ON daily_snapshot(trade_date, foreign_net_qty);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━
-- 스캔용 뷰 (master JOIN 미리 처리)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━
CREATE VIEW IF NOT EXISTS v_daily_scan AS
SELECT
    d.trade_date,
    d.symbol,
    m.name,
    m.market,
    m.sector,
    d.close,
    d.change_pct,
    d.volume,
    d.trade_value,
    d.market_cap,
    d.per,
    d.pbr,
    d.eps,
    d.bps,
    d.foreign_own_pct,
    d.foreign_net_qty,
    d.inst_net_qty,
    d.fi_ratio,
    d.short_ratio,
    d.ovtm_close,
    d.ovtm_change_pct,
    d.consensus_target,
    d.consensus_gap,
    d.rsi14,
    d.macd_hist,
    d.ma_spread,
    d.w52_position,
    d.vp_position_250d,
    d.sector_rel_strength,
    d.operating_margin,
    d.debt_ratio,
    d.roe,
    d.revenue_growth,
    d.op_growth
FROM daily_snapshot d
JOIN stock_master m ON d.symbol = m.symbol;
