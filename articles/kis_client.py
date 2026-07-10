from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .models import KisAccessToken, MarketHoliday

# 만료 시각 이 정도 전부터는 미리 새 토큰을 받아 온다 (경계 시점 요청 실패 방지)
EXPIRY_BUFFER = timedelta(minutes=10)


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
        "fid_prc_cls_code": "0",
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
