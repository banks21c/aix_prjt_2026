from datetime import datetime, timedelta, timezone as dt_timezone

import requests
from django.conf import settings
from django.utils import timezone

from .models import KisAccessToken, MarketHoliday

# 만료 시각 이 정도 전부터는 미리 새 토큰을 받아 온다 (경계 시점 요청 실패 방지)
EXPIRY_BUFFER = timedelta(minutes=10)

KST = dt_timezone(timedelta(hours=9))

# 정규장 시간(KST). 이 구간 밖에서는 주식현재가 조회(FHKST01010100)의 stck_prpr가 시간외단일가 등
# 정규장 종가가 아닌 값으로 바뀔 수 있어, 종가 기준 시세가 필요한 호출부는 get_stock_close_price를
# 통해 국내주식기간별시세(일봉)의 정산된 종가를 대신 써야 한다.
MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE = 9, 0
MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE = 15, 30


def is_regular_session_open():
    """지금이 정규장 시간(09:00~15:30 KST)인지 여부를 반환합니다. 주말/공휴일 여부는 보지
    않으므로, 개장일 여부까지 확인하려면 is_market_open()과 함께 써야 합니다."""
    now_kst = datetime.now(KST)
    open_t = now_kst.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    close_t = now_kst.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return open_t <= now_kst < close_t


def get_access_token():
    """
    캐싱된 유효한 KIS 접근 토큰을 반환합니다. 없거나 만료가 임박했을 때만 새로 발급받아
    DB에 저장합니다 (KIS 정책: 접근 토큰은 1일 1회 발급 원칙, 잦은 재발급 시 이용 제한).
    """
    cached = KisAccessToken.objects.order_by('-issued_at').first()
    if cached and cached.expires_at > timezone.now() + EXPIRY_BUFFER:
        return cached.access_token

    url = f"{settings.KIS_BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
    }
    res = requests.post(url, json=body, headers={"content-type": "application/json"}, timeout=10)
    res.raise_for_status()
    data = res.json()

    expires_at = timezone.now() + timedelta(seconds=data.get('expires_in', 86400))
    KisAccessToken.objects.create(access_token=data['access_token'], expires_at=expires_at)
    return data['access_token']


# 등락률 순위 조회 TR_ID (국내주식 등락률 순위, v1_국내주식-088)
FLUCTUATION_TR_ID = "FHPST01700000"

# 시장 범위 코드: 0000 전체, 0001 코스피, 1001 코스닥, 2001 코스피200
MARKET_SCOPE_ALL = "0000"

# 정렬 구분: 0 상승율순, 1 하락율순
SORT_GAINERS = "0"
SORT_LOSERS = "1"


def get_fluctuation_ranking(sort_cls_code, market_scope=MARKET_SCOPE_ALL, count=5):
    """국내주식 등락률 순위 API로 상승률/하락률 상위 종목을 조회합니다."""
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/ranking/fluctuation"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": FLUCTUATION_TR_ID,
        "custtype": "P",
    }
    params = {
        "fid_rsfl_rate2": "",
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": "20170",
        "fid_input_iscd": market_scope,
        "fid_rank_sort_cls_code": sort_cls_code,
        "fid_input_cnt_1": "0",
        # fid_prc_cls_code는 정렬 기준(sort_cls_code)에 따라 뜻이 달라진다: 상승률순일 때
        # 0=저가대비/1=종가대비, 하락률순일 때 0=고가대비/1=종가대비. "1"(종가대비)로 고정해야
        # 두 방향 모두 우리가 실제로 표시하는 전일 대비율(prdy_ctrt) 기준으로 순위가 뽑힌다.
        # "0"으로 두면 하락률 상위가 "오늘 고점 대비 낙폭 상위"로 뽑혀, 전일 대비로는 보합/상승인
        # 종목이 하락률 상위에 섞여 나올 수 있다(장중 급등 후 급락한 종목 등).
        "fid_prc_cls_code": "1",
        "fid_input_price_1": "",
        "fid_input_price_2": "",
        "fid_vol_cnt": "",
        "fid_trgt_cls_code": "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code": "0",
        "fid_rsfl_rate1": "",
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 등락률 순위 조회 실패: {data.get('msg1')}")

    return data.get('output', [])[:count]


# 국내업종 현재지수 조회 TR_ID (v1_국내주식-063)
INDEX_PRICE_TR_ID = "FHPUP02100000"

# 코스피/코스닥 지수 코드 (FID_INPUT_ISCD)
INDEX_CODE_MAP = {
    'KOSPI': '0001',
    'KOSDAQ': '1001',
}


def get_index_price(market_type):
    """국내업종 현재지수 API로 코스피/코스닥 지수의 실시간 현재가(시가/고가/저가/전일대비 포함)를 조회합니다."""
    index_code = INDEX_CODE_MAP[market_type]
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": INDEX_PRICE_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": index_code,
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 지수 현재가 조회 실패: {data.get('msg1')}")

    output = data['output']
    return {
        'close': float(output['bstp_nmix_prpr']),
        'open': float(output['bstp_nmix_oprc']),
        'high': float(output['bstp_nmix_hgpr']),
        'low': float(output['bstp_nmix_lwpr']),
        'change': float(output['bstp_nmix_prdy_vrss']),
        'change_pct': float(output['bstp_nmix_prdy_ctrt']),
        'volume': int(output.get('acml_vol') or 0),
    }


# 시장별 투자자매매동향(시세) TR_ID (v1_국내주식-074) - 시장 전체 외국인/개인/기관계 순매수 조회
INVESTOR_TREND_TR_ID = "FHPTJ04030000"
# FID_INPUT_ISCD(시장구분)는 지수 코드(0001/1001)와 별개로 KSP/KSQ를 쓴다
MARKET_ISCD_MAP = {
    'KOSPI': 'KSP',
    'KOSDAQ': 'KSQ',
}


def get_investor_trend(market_type):
    """시장별 투자자매매동향(시세) API로 코스피/코스닥 시장 전체의 외국인/개인/기관계
    순매수 수량 + 순매수 금액(억원)을 조회합니다. 종목이 특정되지 않는 카드(특징주 브리핑,
    헤더 지수 티커 팝업 등)의 시장 요약용. 금액 필드(*_ntby_tr_pbmn)는 백만원 단위로 확인됨
    (실측: 원시값 818662 → 실제 8,186.62억원 → 100으로 나누면 억원)."""
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": INVESTOR_TREND_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_INPUT_ISCD": MARKET_ISCD_MAP[market_type],
        "FID_INPUT_ISCD_2": INDEX_CODE_MAP[market_type],
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 투자자매매동향 조회 실패: {data.get('msg1')}")

    output = data.get('output')
    if isinstance(output, list):
        output = output[0] if output else {}

    return {
        'foreign_net_qty': int(output.get('frgn_ntby_qty') or 0),
        'institution_net_qty': int(output.get('orgn_ntby_qty') or 0),
        'retail_net_qty': int(output.get('prsn_ntby_qty') or 0),
        'foreign_net_amount': int(output.get('frgn_ntby_tr_pbmn') or 0) / 100,
        'institution_net_amount': int(output.get('orgn_ntby_tr_pbmn') or 0) / 100,
        'retail_net_amount': int(output.get('prsn_ntby_tr_pbmn') or 0) / 100,
    }


INVESTOR_TRADE_BY_STOCK_TR_ID = "FHPTJ04160001"


def get_investor_trade_by_stock(ticker, end_date):
    """종목별 투자자매매동향(일별) API로 개별 종목의 투자자 주체별(외국인/개인/기관계 +
    증권/투자신탁/사모펀드/은행/보험/종금/기금/기타) 순매수 수량·금액을 조회합니다.
    end_date('YYYYMMDD') 기준으로 최근 30영업일치가 한 번에 내려옵니다(과거로 더 가려면
    end_date를 그만큼 앞선 거래일로 바꿔 다시 호출 — collect_investor_flow의 백필 로직 참고).
    금액 필드(*_ntby_tr_pbmn)는 백만원 단위(KIS 문서 명시)로 그대로 반환합니다.
    "기금(fund)"이 국민연금 등 연기금류가 잡히는 가장 가까운 분류입니다(KIS가 국민연금을
    별도 항목으로 분리해 주지 않음). 장마감(15:40 KST) 이후에만 당일 데이터가 조회됩니다."""
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": INVESTOR_TRADE_BY_STOCK_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": end_date,
        "FID_ORG_ADJ_PRC": "",
        "FID_ETC_CLS_CODE": "1",
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 종목별 투자자매매동향 조회 실패: {data.get('msg1')}")

    rows = []
    for row in data.get('output2', []):
        bsop_date = row.get('stck_bsop_date')
        if not bsop_date:
            continue
        rows.append({
            'date': bsop_date,  # 'YYYYMMDD'
            'foreign_net_qty': int(row.get('frgn_ntby_qty') or 0),
            'foreign_net_amount': int(row.get('frgn_ntby_tr_pbmn') or 0),
            'retail_net_qty': int(row.get('prsn_ntby_qty') or 0),
            'retail_net_amount': int(row.get('prsn_ntby_tr_pbmn') or 0),
            'institution_net_qty': int(row.get('orgn_ntby_qty') or 0),
            'institution_net_amount': int(row.get('orgn_ntby_tr_pbmn') or 0),
            'pension_net_qty': int(row.get('fund_ntby_qty') or 0),
            'pension_net_amount': int(row.get('fund_ntby_tr_pbmn') or 0),
            'trust_net_qty': int(row.get('ivtr_ntby_qty') or 0),
            'trust_net_amount': int(row.get('ivtr_ntby_tr_pbmn') or 0),
            'pe_fund_net_qty': int(row.get('pe_fund_ntby_vol') or 0),
            'pe_fund_net_amount': int(row.get('pe_fund_ntby_tr_pbmn') or 0),
            'securities_net_qty': int(row.get('scrt_ntby_qty') or 0),
            'securities_net_amount': int(row.get('scrt_ntby_tr_pbmn') or 0),
        })

    rows.sort(key=lambda r: r['date'])
    return rows


# 금리 종합(국내채권/금리) TR_ID (국내주식-155) - 저장소에 있는 KIS API 문서 엑셀
# ("kis_api/금리 종합(국내채권_금리) [국내주식-155].xlsx")로 확인한 값. output1은 해외금리지표
# (미국 국채 등), output2는 국내채권/금리(국고채/회사채/CD/콜 등) — 헤더 티커는 output2만 쓴다.
INTEREST_RATE_TR_ID = "FHPST07020000"

# output2의 bcdt_code(자료코드) 중 헤더 티커에 보여줄 4개와 표시 라벨.
# 실제 호출 결과 국고채(Y0101)/회사채(Y0102)는 응답 자체가 누락되거나 인코딩이 깨져서 와
# (2026-08-04 확인, 재현됨 — API 쪽 데이터 이슈로 보임) 정상 수신되는 2개만 우선 노출한다.
INTEREST_RATE_ITEMS = {
    'Y0112': 'CD(91일)',
    'Y0114': '콜금리',
}


def get_interest_rates():
    """금리 종합(국내채권/금리) API로 CD(91일)/콜금리/국고채(3년)/회사채(3년) 등을 조회합니다.
    {bcdt_code: {'name':, 'price':, 'change_pct':}} 형태로, output2에 실린 항목 전부를 반환합니다
    (호출부가 INTEREST_RATE_ITEMS로 필요한 것만 골라 씀)."""
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/comp-interest"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": INTEREST_RATE_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "I",
        "FID_COND_SCR_DIV_CODE": "20702",
        "FID_DIV_CLS_CODE": "1",
        "FID_DIV_CLS_CODE1": "",
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 금리 종합 조회 실패: {data.get('msg1')}")

    # 앞쪽 일부 행은 필드가 밀려서 오는 데이터 이슈가 있어(2026-08-04 확인, 국고채/회사채가
    # 이 구간에 걸림) 행 하나가 깨졌다고 나머지까지 못 쓰게 되지 않도록 개별적으로 건너뛴다.
    result = {}
    for row in data.get('output2', []):
        try:
            result[row['bcdt_code']] = {
                'name': row['hts_kor_isnm'],
                'price': float(row['bond_mnrt_prpr']),
                'change_pct': float(row['bstp_nmix_prdy_ctrt']),
            }
        except (KeyError, ValueError):
            continue
    return result


# 해외지수분봉조회 TR_ID (v1_해외주식-031). 이름과 달리 output1에 해당 종목/지수/환율의
# "현재가 한 건"이 항상 같이 오므로(output2가 분봉 히스토리), 헤더 티커처럼 현재가만 필요할 땐
# output1만 읽고 페이지네이션은 하지 않는다. FID_COND_MRKT_DIV_CODE: N=해외지수, X=환율(달러 기준
# 국제시장), 종목코드(FID_INPUT_ISCD) 값은 실제 호출로 확인한 것들만 GLOBAL_QUOTE_ITEMS에 정리.
OVERSEAS_INDEX_TR_ID = "FHKST03030200"

# (category, code): 표시 라벨 — category는 GlobalMarketQuote.category, code는 그대로
# get_overseas_index_price(mrkt_div_code, code)에 넘길 FID_INPUT_ISCD.
GLOBAL_QUOTE_ITEMS = {
    'FOREIGN_INDEX': {
        # 심천(001001 등)은 시도해본 코드가 전부 결과 없음(0.00)으로 나와 아직 못 찾음 — 정확한
        # FID_INPUT_ISCD 값을 확인하면 여기 추가.
        '.DJI': ('N', '다우존스'),
        'COMP': ('N', '나스닥'),
        'SPX': ('N', 'S&P500'),
        'HSCE': ('N', '홍콩H'),
        'JP#NI225': ('N', '니케이225'),
        'SHANG': ('N', '상해종합'),
        'SX5E': ('N', '유로스톡스50'),
    },
    'FX_RATE': {
        'FX@JPY': ('X', '달러/엔'),
        'FX@EUR': ('X', '유로/달러'),
        'FX@GBP': ('X', '파운드/달러'),
    },
}


def get_overseas_index_price(mrkt_div_code, iscd):
    """해외지수분봉조회 API로 해외지수/환율의 현재가 한 건을 조회합니다(output1만 사용).
    mrkt_div_code: N(해외지수)/X(환율). iscd: '.DJI', 'FX@JPY' 등 GLOBAL_QUOTE_ITEMS 참고."""
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": OVERSEAS_INDEX_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": mrkt_div_code,
        "FID_INPUT_ISCD": iscd,
        "FID_HOUR_CLS_CODE": "0",
        "FID_PW_DATA_INCU_YN": "N",
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 해외지수/환율 조회 실패({iscd}): {data.get('msg1')}")

    output = data.get('output1') or {}
    if not output.get('ovrs_nmix_prpr'):
        raise RuntimeError(f"KIS 해외지수/환율 조회 결과 없음: {iscd}")

    return {
        'name': output.get('hts_kor_isnm') or iscd,
        'price': float(output['ovrs_nmix_prpr']),
        'change_pct': float(output['prdy_ctrt']),
    }


# 국내업종 일자별지수 조회 TR_ID (v1_국내주식-065) - 한 번에 최대 100건(영업일 기준)
INDEX_DAILY_PRICE_TR_ID = "FHPUP02120000"


def get_index_daily_price(market_type, base_date):
    """
    국내업종 일자별지수 API로 기준일(base_date, 'YYYYMMDD')로부터 과거 최대 100영업일치
    코스피/코스닥 지수 OHLC를 조회합니다. 오래된 날짜 순으로 정렬해서 반환합니다.
    """
    index_code = INDEX_CODE_MAP[market_type]
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-daily-price"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": INDEX_DAILY_PRICE_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_PERIOD_DIV_CODE": "D",
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": index_code,
        "FID_INPUT_DATE_1": base_date,
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 일자별지수 조회 실패: {data.get('msg1')}")

    rows = []
    for row in data.get('output2', []):
        if not row.get('stck_bsop_date'):
            continue
        rows.append({
            'date': row['stck_bsop_date'],  # 'YYYYMMDD'
            'open': float(row['bstp_nmix_oprc']),
            'high': float(row['bstp_nmix_hgpr']),
            'low': float(row['bstp_nmix_lwpr']),
            'close': float(row['bstp_nmix_prpr']),
            'change': float(row['bstp_nmix_prdy_vrss']),
            'change_pct': float(row['bstp_nmix_prdy_ctrt']),
        })

    rows.sort(key=lambda r: r['date'])
    return rows


# 국내업종 시간별지수(분) TR_ID (국내주식-119) - 저장소에 있는 KIS API 문서 엑셀
# ("국내업종 시간별지수(분)[국내주식-119].xlsx")로 확인한 값.
# 이 API는 개별 종목 분봉과 달리 한 번 호출에 당일 09:00~조회시각의 전체 구간을
# FID_INPUT_HOUR_1에 넣은 간격(초 단위, 60/300/600)으로 몰아서 돌려주므로 페이지네이션이 필요없고,
# 봉별 시가/고가/저가 없이 해당 시각의 지수 값(bstp_nmix_prpr) 하나만 내려온다 (라인차트용).
INDEX_MINUTE_PRICE_TR_ID = "FHPUP02110200"


def get_today_index_minute_prices(market_type, interval_seconds=300):
    """
    국내업종 시간별지수(분) API로 당일 09:00부터 현재까지의 지수 값을 interval_seconds
    간격으로 한 번에 조회합니다. (오래된 시각 순으로 정렬해서 반환)

    interval_seconds 기본값을 300(5분)으로 둔 이유: 이 API는 페이지네이션 없이 응답을
    최근 ~100건으로만 잘라서 준다(실측 확인, 문서에도 페이지네이션 파라미터가 없음). 60초
    (1분) 간격이면 100건이 겨우 100분(1.6시간)치라 장 마감 무렵엔 13:5x~15:30처럼 당일의
    극히 일부만 표시되는 버그가 있었다. 300초 간격이면 100건이 500분(8.3시간)치라 정규장
    전체(09:00~15:30, 390분)를 한 번의 호출로 다 담을 수 있다.
    """
    index_code = INDEX_CODE_MAP[market_type]
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-timeprice"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": INDEX_MINUTE_PRICE_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": index_code,
        "FID_INPUT_HOUR_1": str(interval_seconds),
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 업종 시간별지수 조회 실패: {data.get('msg1')}")

    rows = []
    for row in data.get('output', []):
        # 정규 장시간(09:00~15:30) 밖의 값(응답 끝에 붙는 888888/999999 같은 더미 시각 포함)은 제외
        if not row.get('bsop_hour') or not ("090000" <= row['bsop_hour'] <= "153000"):
            continue
        rows.append({
            'time': row['bsop_hour'],  # 'HHMMSS'
            'value': float(row['bstp_nmix_prpr']),
        })
    rows.sort(key=lambda r: r['time'])
    return rows


# 국내주식기간별시세(일/주/월/년) TR_ID (v1_국내주식-016) - 한 번에 최대 100건(영업일 기준)
STOCK_DAILY_PRICE_TR_ID = "FHKST03010100"


def get_stock_daily_price(ticker, start_date, end_date):
    """
    국내주식기간별시세 API로 개별 종목의 일봉(OHLC)을 조회합니다.
    start_date/end_date는 'YYYYMMDD' 문자열이며, 한 번에 최대 100영업일치까지 내려옵니다.
    로컬에 10년치 데이터가 없는(코스피200/코스닥150 밖) 종목의 상세 페이지에서
    온디맨드로 최근 일봉을 보여주는 용도로 사용합니다.
    """
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": STOCK_DAILY_PRICE_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 종목 일봉 조회 실패: {data.get('msg1')}")

    rows = []
    for row in data.get('output2', []):
        if not row.get('stck_bsop_date'):
            continue
        rows.append({
            'date': row['stck_bsop_date'],  # 'YYYYMMDD'
            'open': float(row['stck_oprc']),
            'high': float(row['stck_hgpr']),
            'low': float(row['stck_lwpr']),
            'close': float(row['stck_clpr']),
            'volume': int(row.get('acml_vol') or 0),
        })

    rows.sort(key=lambda r: r['date'])
    return rows


def get_stock_close_price(ticker):
    """
    종가 기준 시세가 필요한 호출부(AI 요약 언급 종목 시세표 등)를 위한 헬퍼.
    정규장 중(is_regular_session_open())엔 주식현재가 조회(get_stock_current_price)를 그대로 쓰고,
    정규장 마감 후~다음 개장 전에는 대신 국내주식기간별시세(get_stock_daily_price)의 정산된 종가
    (stck_clpr)를 쓴다 — 주식현재가 조회는 이 구간에서 시간외단일가 등 정규장 종가가 아닌 값을
    돌려줄 수 있기 때문. 전일대비/등락률은 API가 주는 값 대신, 함께 받아온 최근 2개 영업일
    종가로 직접 계산해 일봉 응답의 필드명에 의존하지 않는다.
    """
    if is_regular_session_open():
        return get_stock_current_price(ticker)

    end_date = datetime.now(KST).date()
    start_date = end_date - timedelta(days=14)
    rows = get_stock_daily_price(ticker, start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d'))
    if len(rows) < 2:
        raise RuntimeError(f"{ticker}: 종가를 계산할 만큼의 일봉 데이터가 없습니다.")

    today_row, prev_row = rows[-1], rows[-2]
    close, prev_close = today_row['close'], prev_row['close']
    change = close - prev_close
    change_pct = (change / prev_close * 100) if prev_close else 0.0
    return {
        'close': close,
        'open': today_row['open'],
        'high': today_row['high'],
        'low': today_row['low'],
        'change': change,
        'change_pct': change_pct,
        'volume': today_row['volume'],
    }


# 주식당일분봉조회 TR_ID (v1_국내주식-022) - 한 번 호출에 최근 30건(1분 간격)만 내려옴
MINUTE_PRICE_TR_ID = "FHKST03010200"


def get_stock_minute_price(ticker, input_hour):
    """
    주식당일분봉조회 API로 input_hour('HHMMSS') 시각 기준 직전 최근 30개 분봉을 조회합니다.
    (장이 닫혀있으면 가장 최근 거래일의 분봉을 돌려줍니다.)
    """
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": MINUTE_PRICE_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_HOUR_1": input_hour,
        "FID_PW_DATA_INCU_YN": "Y",
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 당일분봉 조회 실패: {data.get('msg1')}")

    rows = []
    for row in data.get('output2', []):
        if not row.get('stck_cntg_hour'):
            continue
        rows.append({
            'date': row['stck_bsop_date'],  # 'YYYYMMDD'
            'time': row['stck_cntg_hour'],  # 'HHMMSS'
            'open': float(row['stck_oprc']),
            'high': float(row['stck_hgpr']),
            'low': float(row['stck_lwpr']),
            'close': float(row['stck_prpr']),
        })
    return rows


def get_today_minute_prices(ticker, max_pages=14):
    """
    장 시작(09:00)부터 조회 시점까지의 당일 분봉 전체를, 위 API를 여러 번(최대 max_pages회)
    호출해 이어붙여서 만듭니다. 한 번에 30개씩만 내려오고 매 호출마다 커서를 앞으로
    당겨가며 조회하는 방식이라, 세션당 API 호출량 부담을 막기 위해 최대 호출 횟수를 둔다.
    """
    all_rows = {}
    target_date = None
    cursor = None

    for _ in range(max_pages):
        input_hour = cursor or "153000"  # 첫 호출은 장마감 시각부터 역순으로 최근 데이터를 받음
        page = get_stock_minute_price(ticker, input_hour)
        if not page:
            break

        if target_date is None:
            target_date = page[0]['date']  # 최초 호출로 받아온 거래일을 기준으로 고정

        # 커서가 09:00을 넘어가면 KIS가 이전 거래일 데이터를 섞어 내려주는 경우가 있어,
        # 같은 거래일(target_date)의 행만 남긴다.
        for row in page:
            if row['date'] == target_date:
                all_rows[row['time']] = row

        earliest = min(page, key=lambda r: r['time'])
        if earliest['time'] <= "090000":
            break

        # 다음 호출은 이번 배치의 가장 이른 시각 이전부터 이어서 조회
        hh, mm = int(earliest['time'][:2]), int(earliest['time'][2:4])
        total_minutes = hh * 60 + mm - 1
        if total_minutes < 9 * 60:
            break
        cursor = f"{total_minutes // 60:02d}{total_minutes % 60:02d}00"

    rows = [row for row in all_rows.values() if row['time'] >= "090000"]
    rows.sort(key=lambda r: r['time'])
    return rows


# 국내휴장일조회 TR_ID (국내주식-040)
HOLIDAY_TR_ID = "CTCA0903R"


def get_market_holidays(base_date):
    """
    국내휴장일조회 API로 기준일(base_date, 'YYYYMMDD')부터 이후 영업일/거래일/개장일/결제일
    여부를 조회합니다. 한 번의 호출로 여러 날짜치가 반환됩니다 (KIS 정책상 1일 1회 호출 권장).
    """
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/chk-holiday"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": HOLIDAY_TR_ID,
        "custtype": "P",
    }
    params = {
        "BASS_DT": base_date,
        "CTX_AREA_NK": "",
        "CTX_AREA_FK": "",
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 국내휴장일조회 실패: {data.get('msg1')}")

    rows = []
    for row in data.get('output', []):
        rows.append({
            'date': row['bass_dt'],  # 'YYYYMMDD'
            'is_business_day': row['bzdy_yn'] == 'Y',
            'is_trading_day': row['tr_day_yn'] == 'Y',
            'is_market_open': row['opnd_yn'] == 'Y',
            'is_settlement_day': row['sttl_day_yn'] == 'Y',
        })
    return rows


def is_market_open(check_date=None):
    """
    주어진 날짜(check_date, 없으면 오늘)가 개장일인지 MarketHoliday 캐시로 확인합니다.
    캐시에 없으면 국내휴장일조회 API를 한 번 호출해 받아온 날짜치를 전부 캐싱한 뒤 판단합니다.
    """
    check_date = check_date or timezone.localdate()
    cached = MarketHoliday.objects.filter(date=check_date).first()
    if cached is None:
        rows = get_market_holidays(check_date.strftime('%Y%m%d'))
        for row in rows:
            MarketHoliday.objects.update_or_create(
                date=datetime.strptime(row['date'], '%Y%m%d').date(),
                defaults={
                    'is_business_day': row['is_business_day'],
                    'is_trading_day': row['is_trading_day'],
                    'is_market_open': row['is_market_open'],
                    'is_settlement_day': row['is_settlement_day'],
                },
            )
        cached = MarketHoliday.objects.filter(date=check_date).first()

    # API 응답에 기준일이 포함되지 않는 등 캐싱 실패 시에는 안전하게 개장으로 간주하고 진행
    return cached.is_market_open if cached is not None else True


# 주식현재가 시세 TR_ID (국내주식-008)
CURRENT_PRICE_TR_ID = "FHKST01010100"


def get_stock_current_price(ticker):
    """주식현재가 시세 API로 개별 종목의 실시간 현재가(시가/고가/저가/전일대비/거래량)를 조회합니다."""
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": CURRENT_PRICE_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 주식현재가 조회 실패: {data.get('msg1')}")

    output = data['output']
    return {
        'close': float(output['stck_prpr']),
        'open': float(output['stck_oprc']),
        'high': float(output['stck_hgpr']),
        'low': float(output['stck_lwpr']),
        'change': float(output['prdy_vrss']),
        'change_pct': float(output['prdy_ctrt']),
        'volume': int(output['acml_vol']),
    }


# 종합 시황_공시(제목) TR_ID (국내주식-141)
NEWS_TITLE_TR_ID = "FHKST01011800"


def get_news_titles():
    """
    종합 시황_공시(제목) API로 전 종목 대상 최신 뉴스/공시 제목을 조회합니다.
    각 항목에는 매칭된 종목코드(최대 10개, iscd1~iscd10)가 함께 내려옵니다.
    """
    token = get_access_token()
    url = f"{settings.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/news-title"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.KIS_APP_KEY,
        "appsecret": settings.KIS_APP_SECRET,
        "tr_id": NEWS_TITLE_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_NEWS_OFER_ENTP_CODE": "",
        "FID_COND_MRKT_CLS_CODE": "",
        "FID_INPUT_ISCD": "",
        "FID_TITL_CNTT": "",
        "FID_INPUT_DATE_1": "",
        "FID_INPUT_HOUR_1": "",
        "FID_RANK_SORT_CLS_CODE": "",
        "FID_INPUT_SRNO": "",
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    if data.get('rt_cd') != '0':
        raise RuntimeError(f"KIS 종합 시황_공시(제목) 조회 실패: {data.get('msg1')}")

    rows = []
    for item in data.get('output', []):
        tickers = [item.get(f'iscd{i}', '').strip() for i in range(1, 11)]
        rows.append({
            'serial_no': item['cntt_usiq_srno'],
            'title': item['hts_pbnt_titl_cntt'].strip(),
            'source': item['dorg'].strip(),
            'tickers': [t for t in tickers if t],
        })
    return rows
