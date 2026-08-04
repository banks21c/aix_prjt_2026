"""한국수출입은행(koreaexim.go.kr) OpenAPI 클라이언트.

KIS Open API에는 은행 고시환율(하나은행 기준 같은 "환전 고시 환율") 데이터가 없어서, 헤더 지수
티커의 그 항목만 이 API로 따로 받아온다. KIS와 달리 OAuth 토큰 발급 없이 authkey 하나로 바로
호출하는 단순한 REST API다.
"""
import requests
from django.conf import settings

EXCHANGE_URL = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"

# 헤더 티커에 노출할 통화만 골라서 표시 라벨을 붙인다. cur_unit 값은 실제 호출로 확인한 것
# (환율API.txt 예시 응답 참고) — 중국은 역외위안화(CNH)만 제공되고 국내 CNY 고시는 없다.
FX_FIXING_ITEMS = {
    'USD': '미국 USD',
    'JPY(100)': '일본 JPY(100엔)',
    'EUR': '유럽연합 EUR',
    'CNH': '중국 CNH',
}


def get_exchange_rates(search_date=None):
    """현재환율 API(AP01)로 환율 목록을 조회합니다. search_date는 'YYYYMMDD' 문자열(생략 시
    당일). 주말/공휴일 등 고시가 없는 날은 빈 리스트가 돌아온다(RESULT!=1 항목은 걸러냄).
    반환값: [{'cur_unit':, 'cur_nm':, 'deal_bas_r': float}, ...]
    """
    params = {"authkey": settings.EXIM_AUTH_KEY, "data": "AP01"}
    if search_date:
        params["searchdate"] = search_date
    res = requests.get(EXCHANGE_URL, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    rows = []
    for row in data:
        if row.get('result') != 1:
            continue
        try:
            rows.append({
                'cur_unit': row['cur_unit'],
                'cur_nm': row['cur_nm'],
                'deal_bas_r': float(row['deal_bas_r'].replace(',', '')),
            })
        except (KeyError, ValueError):
            continue
    return rows
