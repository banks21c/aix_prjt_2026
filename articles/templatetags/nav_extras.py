from django import template

register = template.Library()


@register.filter
def startswith(text, prefix):
    """네비게이션 탭 활성화 판정용 — request.path가 메뉴의 get_url로 시작하면(하위 상세
    페이지 포함) 그 탭을 활성 상태로 표시한다. Django 템플릿에 기본 내장된 startswith가
    없어서 직접 추가."""
    return str(text).startswith(str(prefix))
