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


# 국내업종 시간별지수(분) TR_ID (국내주식-119) - 저장소에 있는 KIS API 문서 엑셀
# ("국내업종 시간별지수(분)[국내주식-119].xlsx")로 확인한 값.
# 이 API는 개별 종목 분봉과 달리 한 번 호출에 당일 09:00~조회시각의 전체 구간을
# FID_INPUT_HOUR_1에 넣은 간격(초 단위, 60/300/600)으로 몰아서 돌려주므로 페이지네이션이 필요없고,
# 봉별 시가/고가/저가 없이 해당 시각의 지수 값(bstp_nmix_prpr) 하나만 내려온다 (라인차트용).
INDEX_MINUTE_PRICE_TR_ID = "FHPUP02110200"


def get_today_index_minute_prices(market_type, interval_seconds=60):
    """
    국내업종 시간별지수(분) API로 당일 09:00부터 현재까지의 지수 값을 interval_seconds
    간격으로 한 번에 조회합니다. (오래된 시각 순으로 정렬해서 반환)
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
