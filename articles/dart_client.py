"""DART(전자공시시스템) Open API 클라이언트. https://opendart.fss.or.kr

공시검색(list.json)만 사용한다. corp_code 없이 날짜(bgn_de~end_de)+공시유형(pblntf_ty)만
넣으면 시장 전체를 대상으로 조회되고, 응답에 stock_code가 바로 들어있어 종목별로 corp_code를
먼저 매핑할 필요가 없다(collect_investor_flow처럼 종목마다 순회 호출하지 않아도 됨).
KIS와 달리 OAuth 토큰 없이 인증키(crtfc_key) 하나로 바로 호출하는 단순 REST API다.
"""
import requests
from django.conf import settings

LIST_URL = "https://opendart.fss.or.kr/api/list.json"

# corp_code 없는 조회는 검색기간이 3개월로 제한된다(DART 문서 명시) — 그보다 긴 백필 요청은
# 호출부(collect_dart_disclosures)가 이 크기로 쪼개서 여러 번 호출한다.
MAX_RANGE_DAYS = 90


def search_disclosures(bgn_de, end_de, pblntf_ty=None, page_no=1, page_count=100):
    """공시검색 API 1페이지를 조회합니다. bgn_de/end_de는 'YYYYMMDD' 문자열.
    pblntf_ty를 생략하면 전체 공시유형이 조회됩니다.
    반환값: (rows, total_page) — rows는 list.json의 'list' 배열 그대로(dict 리스트).
    """
    params = {
        "crtfc_key": settings.DART_API_KEY,
        "bgn_de": bgn_de,
        "end_de": end_de,
        "page_no": page_no,
        "page_count": page_count,
    }
    if pblntf_ty:
        params["pblntf_ty"] = pblntf_ty

    res = requests.get(LIST_URL, params=params, timeout=15)
    res.raise_for_status()
    data = res.json()

    status = data.get('status')
    if status == '013':
        # "조회된 데이터가 없습니다" — 정상 케이스(그 기간에 해당 유형 공시가 없음)
        return [], 0
    if status != '000':
        raise RuntimeError(f"DART 공시검색 실패 (status={status}): {data.get('message')}")

    return data.get('list', []), int(data.get('total_page', 0))
