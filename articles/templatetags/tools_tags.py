from django import template
from django.conf import settings
from django.urls import reverse

from ..tools_catalog import CATEGORIES, category_of, tool_by_url_name

register = template.Library()


@register.inclusion_tag('articles/_tools_tabs.html')
def tools_tabs(active_tool=''):
    """/tools/ 계열 페이지 공통 상단 탭(카테고리 2단). 각 유틸 템플릿이
    {% tools_tabs 'image_converter' %}처럼 자기 url_name을 넘긴다 — 허브는 'hub'.
    include 대신 태그인 이유는 목록 데이터(tools_catalog)를 템플릿이 직접 가져와야 해서다."""
    return {
        'categories': CATEGORIES,
        'active_tool': active_tool,
        'active_category': category_of(active_tool) or CATEGORIES[0]['key'],
    }


@register.inclusion_tag('articles/_tools_cards.html')
def tools_cards():
    """허브(/tools/)의 카테고리별 카드 그리드 — 탭과 같은 tools_catalog를 쓴다."""
    return {'categories': CATEGORIES}


@register.inclusion_tag('articles/_tool_meta.html')
def tool_meta(url_name):
    """유틸 페이지의 <meta name="description">·canonical·og 태그. 설명 문구는 허브 카드와
    같은 tools_catalog를 쓰므로 둘이 어긋날 수 없다.

    canonical을 넣는 이유: nextfinup.com과 www.nextfinup.com이 둘 다 서빙돼(robots_txt_view
    주석 참고) 같은 페이지가 두 주소로 색인될 수 있다. settings.SITE_URL(비-www) 한쪽으로 모은다.
    """
    tool = tool_by_url_name(url_name)
    if not tool:
        return {'tool': None}
    return {
        'tool': tool,
        'canonical': settings.SITE_URL.rstrip('/') + reverse(tool['url_name']),
    }
