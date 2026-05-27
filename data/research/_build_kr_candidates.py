#!/usr/bin/env python3
"""
Phase 1a — KR 후보 풀 250개 구축
Builds kr_candidates.json by consolidating all MCP scan results.
"""
import json
import glob
import os
import re
from datetime import datetime
from collections import defaultdict

DATA_DIR = '/Users/kreuzer/stock-bot/data'
TOOL_RESULTS_DIR = '/Users/kreuzer/.claude/projects/-Users-kreuzer-stock-bot/3682051b-a493-4e47-95ef-b00bac22c256/tool-results'

# ============================================================
# Sector classification helper
# ============================================================
SECTOR_KEYWORDS = {
    '반도체': ['반도체','전자','전기','HBM','메모리','ISC','이오테크닉스','한미반도체','이수페타시스','심텍','대덕전자','하나마이크론','LB세미콘','네패스','테크윙','하이닉스','삼성전자','LG디스플레이','LG이노텍','삼성전기','파두','제주반도체','SK스퀘어','솔브레인','이엔에프','피에스케이','원익','매커스','아나패스','솔루스첨단','RF머트리얼즈','에이팩트','오픈엣지','삼양엔씨켐','다원넥스뷰','피엔에이치테크','한미','솔루엠','테스','광전자','KEC','LX세미콘','심텍홀딩스','엠케이전자','한솔아이원스','뷰웍스','동진쎄미켐','SK아이이테크','솔브레인홀딩스','후성','네오위즈홀','매커스','HPSP','샘씨엔에스','코미코','피델릭스','하이브이젼','EUV','SOL AI','삼화전기','한국전자홀딩스','대성에너지','TIGER AI','대덕','한양디지텍','프로텍','이엠에스','코아시아','코미코','와이솔','와이지엔터','동도','이엔플러스','넥서스','광전자'],
    '방산': ['방산','LIG','한화에어로','한화시스템','현대로템','풍산','퍼스텍','한일단조','휴니드','TIGER 방산','S&T','SNT다이내믹스','STX엔진','한화','LIG디펜스'],
    '전력기기': ['전력','HD현대일렉트릭','효성중공업','LS ELECTRIC','LS에코','대원전선','일진전기','선도전기','한국가스공사','한국전력','한전기술','두산에너빌리티','두산퓨얼셀','보성파워','한전','대성에너지','가온전선','SP삼화','SNT에너지','경동인베스트','삼천리','경동도시가스','대성홀딩스','SK가스','E1','서울가스','삼화전기','삼화콘덴서','한국전력기술','KODEX 전력','SGC에너지','한국전자홀딩스','LS Industrial','우진엔텍'],
    '조선': ['조선','HD한국조선해양','HD현대중공업','HD현대미포','삼성중공업','한화오션','HJ중공업','동성화인텍','세진중공업','STX엔진','대한조선','HD현대마린엔진','KODEX 조선','한국카본'],
    '자동차': ['자동차','현대차','기아','현대모비스','HL만도','현대글로비스','현대위아','SNT모티브','넥센타이어','한국타이어','새론오토','지엠비','코리아에프티','에스텍','HL홀딩스','피에이치에이','삼익','삼보모터스','넥센','서연이화','서연','평화홀딩스','현대공업','현대무벡스','현대오토에버','동도','한국무브넥스','대원전선','한라캐스트','새론오토모티브','대성산업','대주전자재료','이수페타시스'],
    'K뷰티': ['뷰티','화장품','코스메카','한국콜마','아모레','LG생활건강','잉글우드','클리오','아이패밀리','콜마비앤에이치','실리콘투','달바','코스맥스','펌텍','파마리서치','아이패밀리','삐아','APR','에이피알','네오팜'],
    '바이오': ['바이오','제약','약품','헬스케어','삼성바이오','셀트리온','한미약품','대웅','녹십자','동아','종근당','휴젤','클래시스','메디톡스','파마리서치','에스티팜','유한양행','리가켐','알테오젠','HK이노엔','에이비엘','오스코텍','오름테라퓨틱','한올바이오','펄어비스','HLB','삼아알미늄','HK이노엔','메지온','한송네오텍','네오펙트','시지메드','펩트론','동국제약','대한약품','부광약품','메지온','지놈앤컴퍼니','싸이토젠','애드바이오텍','케어젠','동구바이오','대한약품','펌텍코리아','아이진','메디톡스','한송네오텍','삐아','동성제약','동아쏘시오','대웅제약','한솔아이원스','대원제약','일성건설','삼진제약','안국약품','한신공영','삼일제약','파미셀','수젠텍','휴마시스','J2K바이오','동성제약','동국제약','우진비앤지','동방아그로','대정화금','알피바이오','선바이오','위더스제약','한독크린텍','동성제약','진원생명과학','녹십자엠에스','애드바이오텍','넥스트바이오','한스바이오메드','씨어스','바이오에프디엔씨','HK이노엔','로킷헬스케어'],
    '2차전지': ['2차전지','배터리','LG에너지솔루션','삼성SDI','SK이노','포스코퓨처엠','엘앤에프','에코프로','대주전자재료','한솔케미칼','동화기업','상신이디피','신흥에스이씨','SK아이이테크놀로지','한국카본','솔루스첨단소재','피엔티','TCC스틸','SKC','코스모','OCI'],
    '소재화학': ['소재','화학','LG화학','SK이노베이션','롯데케미칼','한화솔루션','금호석유','대한유화','SKC','효성티앤씨','효성','코오롱인더','동진쎄미','솔브레인','후성','OCI','삼양','삼양홀딩스','KPX케미칼','한솔케미칼','SK디스커버리','PI첨단소재','감성코퍼레이션','이수화학','솔루스첨단','OCI홀딩스','대한유화','이엔에프','HS효성','롯데케미칼','휴켐스'],
    '철강': ['철강','POSCO','현대제철','동국제강','세아베스틸','세아홀딩스','KG스틸','고려아연','풍산홀딩스','경남스틸','동남합성','DSR제강','금강철강','대한제강','한국철강','동부제철'],
    'IT/SW': ['IT','소프트웨어','SI','시스템','LG씨엔에스','삼성에스디에스','NAVER','카카오','크래프톤','넷마블','NC','NCSOFT','펄어비스','시큐브','SK스퀘어','LG','SK','넥슨','한글과컴퓨터','컴투스','네오위즈','솔트룩스','셀바스AI','한국기업평가','나이스디앤비','나이스정보통신','쿠콘','한국정보통신','코나아이','AP시스템','로체시스템즈','한국전자홀딩스','윈스테크넷','HS애드','폴라리스오피스','액토즈','웹젠','갤럭시아머니','데이터솔루션','한컴','솔트룩스','쏠리드','메가스터디','윈스테크넷','다우데이타','다우키움','한솔아이원스','피플바이오','크라우드웍스','한국정보인증','쏠리드','이오리아','한송네오텍','오픈엣지','폴라리스오피스','EO','LG씨엔에스','네오위즈홀딩스'],
    '엔터/미디어': ['엔터','미디어','콘텐츠','JYP','SM','YG','하이브','CJ ENM','스튜디오드래곤','콘텐트리','쇼박스','에스엠','와이지엔터','크래프톤','펄어비스','컴투스','넷마블','NC','웹젠','네오위즈','갤럭시아','액토즈','케이팝','SAMG'],
    '식음료': ['식','음료','농심','오뚜기','대상','CJ제일제당','오리온','삼양식품','GS리테일','BGF','GS','롯데쇼핑','이마트','신세계','크라운','크라운제과','매일유업','매일홀딩스','농심홀딩스','풀무원','샘표','농심','대상홀딩스','CJ프레시웨이','SPC','롯데웰푸드','롯데','롯데지주','롯데칠성','동원','오뚜기','동원F&B','신세계푸드','신세계','이마트','롯데쇼핑','BGF','GS리테일','GS','농심','오리온','파스토리','선진','우양','크라운제과','보락','삼양식품','일성건설','대상','동방아그로','매일홀딩스','삼양홀딩스','하림','하림지주','대한제분','조광ILI','동원'],
    '건설/부동산': ['건설','부동산','삼성E&A','현대건설','대우건설','GS건설','DL이앤씨','HDC','한신공영','계룡건설','동부건설','동원개발','HL D&I','자이에스앤디','HJ중공업','삼부토건','범양건영','한일현대시멘트','KCC건설','한국자산신탁','롯데리츠','한미글로벌','우원개발','쌍용C&E','KCC','벽산','한솔','보락','한라'],
    '금융/증권/보험': ['금융','증권','보험','은행','KB금융','신한지주','하나금융','우리금융','기업은행','JB금융','BNK','DGB','메리츠','삼성카드','한국금융지주','미래에셋증권','NH투자증권','삼성증권','대신증권','교보증권','신영증권','한화투자','유화증권','SK증권','코리아에셋','부국증권','다올투자증권','삼성생명','한화생명','삼성화재','현대해상','DB손해보험','메리츠화재','한화손해','코리안리','흥국화재','롯데손해','한국재보험','우리종합금융','메리츠금융지주','BNK금융','DGB금융','한국토지신탁','한국자산신탁','LB세미콘','BGF','코웨이','롯데렌탈','SK렌터카','스튜디오스','SK','한화','롯데','두산','LG','GS','CJ','SK스퀘어','한국기업평가','HK','한국금융','우리종합','한투','대신','삼성','메리츠','신영스팩','KBG','쿠콘','부광','애플러스','뷰웍스','다우키움'],
    '소비재/유통/패션': ['유통','패션','신세계','롯데쇼핑','이마트','GS리테일','BGF','쿠팡','한세','한섬','F&F','휠라','LF','신세계인터','휠라코리아','코웰','삼성물산','한세실업','한섬','LF','F&F홀딩스','신성통상','SG세계물산','일신방직','패션플랫폼','한세엠케이','신영와코루','경방','동방아그로','경동','롯데','BGF','GS','CJ','한국정보통신','코리아에프티','메가스터디교육','농심홀딩스','감성코퍼레이션','코웨이','삐아','케이카','달바글로벌','삼양홀딩스','대원화성','케이엔솔','대성산업','다이소','이브이씨','마이크로컨텍솔'],
    '통신/네트워크': ['통신','네트워크','KT','SK텔레콤','LG유플러스','한국전기','TIGER','네이버','카카오','다우데이타','와이솔','쏠리드','LG씨엔에스','이노인스트루먼트'],
    '기계/산업재': ['기계','산업재','두산','두산밥캣','한온시스템','HD현대인프라','한국정밀','삼익THK','SNT다이내믹스','로보스타','로보티즈','뉴로메카','레인보우로보틱스','엔젤로보틱스','피앤에스로보틱스','휴림로보틱스','휴림로봇','라온로보틱스','한미반도체','솔브레인','와이지원','피엠티','삼익THK','피아이이','대모','도우인시스','로체시스템즈','경인양행','대정화금','삼화전기','삼화콘덴서','지엠비','파트론','지아이에스','이랜시스','베셀','삼익악기','로지시스'],
    '리츠/지주사': ['리츠','지주','SK','LG','GS','한화','롯데','CJ','두산','삼양','HD현대','LX','SK스퀘어','롯데지주','신세계','삼성물산','SK디스커버리','동원시스템즈','한국타이어','한국타이어월드와이드','농심홀딩스','매일홀딩스','LG','오리온','대한제분','삼성전자','LG','SK','GS','CJ','롯데','한화','두산'],
    '에너지/유틸리티': ['에너지','석유','가스','전력','S-Oil','SK이노베이션','GS','한국가스공사','한국전력','SK가스','E1','서울가스','경동도시가스','삼천리','대성홀딩스','경동인베스트','미창석유','SP삼화','SGC에너지','OCI홀딩스','보성파워텍','중앙에너비스','대성에너지','HD현대','한국전력기술','GS','S-Oil','한국석유','KH','KH 미래물산'],
}

def classify_sector(name, ticker):
    """Return sector label given name."""
    if not name:
        return '기타'
    lname = name.lower()
    # Direct high-confidence matches
    for sector, kws in SECTOR_KEYWORDS.items():
        for kw in kws:
            if kw and kw.lower() in lname:
                return sector
            if kw == name:
                return sector
    return '기타'


def market_cap_bucket(cap_eok):
    """대형/중형/소형 — cap_eok = 시총 (억원)"""
    if cap_eok is None or cap_eok == 0:
        return '소형'
    cap_jo = cap_eok / 10000  # 억 → 조
    if cap_jo >= 10:
        return '대형'
    if cap_jo >= 1:
        return '중형'
    return '소형'


def format_market_cap(cap_eok):
    if cap_eok is None or cap_eok == 0:
        return None
    cap_jo = cap_eok / 10000
    if cap_jo >= 1:
        return f"{cap_jo:.1f}조"
    return f"{cap_eok:.0f}억"


# ============================================================
# Theme matching (5 themes from research_log.md)
# ============================================================
THEME_TICKERS = {
    'AI반도체': set([
        '005930','000660','042700','058470','240810','108320','058610','039030','357780',
        '195870','119860','115960','064290','088800','317330','403870','161580','272290',
        '440110','058470','396270','035600','053610','068240','267850','353200','005870',
        '402340','009150','011070','034220','108320','323410','032500','321820','295310',
        '058470','322000','402340','456040','295310','295310','222800','353200',
        '067310','080220','058610','159010','425040','053610','004380','219130','009155',
        '482630','327260','252990','299900','228760','085370','045390','383310','166090',
        '383310','290510','108490','396270','280360','053610','058610','395400',
    ]),
    'K방산': set([
        '012450','272210','064350','079550','103140','329180','008350','272450',
        '255440','010820','005870','003570','402030','045970','022100','373220',
        '143540','015750'
    ]),
    '전력기기': set([
        '267260','298040','010120','229640','034020','267290','015760','052690',
        '083650','457550','054540','000990','088790','000500','100840','006345',
        '007610','024840','058430','036460','053610','SP삼화','SNT에너지'
    ]),
    'K조선': set([
        '009540','329180','010140','042660','010620','077970','075580','033500',
        '439260','071970','017960','053610'
    ]),
    'K뷰티': set([
        '241710','161890','090430','051900','950140','237880','114840','200130',
        '278470','257720','483650','053660','093520','347700','444090','002390',
        '051905','445090','445090'
    ]),
}


def themes_for(ticker):
    matched = []
    for theme, tickers in THEME_TICKERS.items():
        if ticker in tickers:
            matched.append(theme)
    return matched


# ============================================================
# 1. Load existing thesis
# ============================================================
with open(f'{DATA_DIR}/thesis/_index.json') as f:
    idx = json.load(f)

existing_thesis_tickers = set()
existing_thesis_meta = {}
for entry in idx.get('files', []):
    if entry.get('country') == 'KR':
        t = entry.get('ticker', '')
        if t:
            existing_thesis_tickers.add(t)
            existing_thesis_meta[t] = {
                'name': entry.get('name', ''),
                'sector': entry.get('sector', ''),
                'grade': entry.get('thesis_grade', ''),
            }

print(f"Existing KR thesis: {len(existing_thesis_tickers)}")


# ============================================================
# 2. Consolidate scan results into raw candidate dict
# ============================================================
# pool[ticker] = {name, market_cap, market, sources: set, raw_signals: dict}
pool = {}

def add(ticker, name, market_cap, market, source, signals=None):
    if not ticker or not name:
        return
    # Exclude 우 (preferred) and 스팩, ETF
    bad_kws = ['스팩','ETN','ETF','채권','액티브','KODEX','TIGER','SOL ','RISE','PLUS ','KoAct','HK ','우B','뱅크','금융채','금선물','은선물']
    if any(k in name for k in bad_kws):
        return
    # Exclude preferred stocks (보통주만): ticker ending in 5/7 with same prefix as common
    if ticker[-1] in ('5','7') and ticker.startswith('00') and 'B' not in ticker:
        # Many but not all are prefs — only filter if name contains '우'
        if name.endswith('우') or name.endswith('우B') or '우B' in name or '(전환)' in name or '2우' in name:
            return
    if '우' == name[-1:] and len(name) > 1:
        return
    if ticker not in pool:
        pool[ticker] = {
            'ticker': ticker,
            'name': name,
            'market_cap_eok': market_cap or 0,
            'market': market or '',
            'sources': set(),
            'raw_signals': {},
        }
    pool[ticker]['sources'].add(source)
    if signals:
        for k, v in signals.items():
            if v is not None and v != 0:
                pool[ticker]['raw_signals'][k] = v
    # Keep max market_cap (some sources have higher quality)
    if market_cap and market_cap > pool[ticker]['market_cap_eok']:
        pool[ticker]['market_cap_eok'] = market_cap


# Load from saved change_scan files
files = sorted(glob.glob(f'{TOOL_RESULTS_DIR}/mcp-bb4ac0ce-*get_change_scan-1779407*.txt'))
for f in files:
    try:
        with open(f) as fp:
            data = json.load(fp)
    except Exception:
        continue
    preset = data.get('preset', '?')
    for r in data.get('results', []):
        t = r.get('ticker', '')
        n = r.get('name', '')
        mc = r.get('market_cap', 0)
        mkt = r.get('market', '')
        signals = {
            'chg_pct': r.get('chg_pct'),
            'rsi14': r.get('rsi14'),
            'w52_position': r.get('w52_position'),
            'foreign_trend_5d': r.get('foreign_trend_5d'),
            'consensus_gap': r.get('consensus_gap'),
            'earnings_gap': r.get('earnings_gap'),
            'ma_spread': r.get('ma_spread'),
            'volume_ratio_5d': r.get('volume_ratio_5d'),
            'eps_change_90d': r.get('eps_change_90d'),
            'vp_position': r.get('vp_position'),
            'sector_rel_strength': r.get('sector_rel_strength'),
            'per': r.get('per'),
            'pbr': r.get('pbr'),
        }
        # Skip stocks with 0 market cap (likely 스팩/etc)
        if not mc or mc == 0:
            continue
        add(t, n, mc, mkt, f'change_scan:{preset}', signals)


# ============================================================
# Inject get_scan results (already in-memory data we got)
# ============================================================
# Use a separate function to add from JSON-inline
def add_inline(items, preset_name):
    for r in items:
        t = r.get('ticker','')
        n = r.get('name','')
        mc = r.get('market_cap', 0)
        mkt = r.get('market','')
        sig = {
            'chg_pct': r.get('chg_pct'),
            'per': r.get('per'),
            'pbr': r.get('pbr'),
            'foreign_ratio': r.get('foreign_ratio'),
            'fi_ratio': r.get('fi_ratio'),
            'turnover': r.get('turnover'),
        }
        if r.get('cum_foreign_ratio') is not None:
            sig['cum_foreign_ratio'] = r['cum_foreign_ratio']
        if not mc or mc == 0:
            continue
        add(t, n, mc, mkt, f'scan:{preset_name}', sig)


# Inline scan results — paste the data we received above as Python dicts via JSON
scan_data_files = {
    # All scan presets - hardcode the tickers we already see
}

# Read the assistant's inline data through env: we'll dump them inline below
EOF
echo "Script created (template only)"