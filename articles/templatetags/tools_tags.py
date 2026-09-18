from django import template

from ..tools_catalog import CATEGORIES, category_of

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
