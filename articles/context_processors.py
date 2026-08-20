from django.db.models import Max

from .models import Menu, ThemeColor


def menu_items(request):
    """모든 템플릿에서 {{ index_menus }} / {{ header_menus }}로 활성 메뉴를 바로 쓸 수 있게 주입.
    Menu.menu_type으로 랜딩(index) 페이지와 내부 앱 공통 헤더의 노출 목록을 구분한다."""
    menus = Menu.objects.filter(is_active=True)
    return {
        'index_menus': [m for m in menus if m.menu_type == 'INDEX'],
        'header_menus': [m for m in menus if m.menu_type == 'HEADER'],
    }


def theme_version(request):
    """<link href="{% url 'theme_css' %}?v={{ theme_version }}">에 붙이는 캐시 무효화 값.
    Cloudflare가 .css로 끝나는 URL을 origin의 Cache-Control과 무관하게 엣지에서 캐시해버려서
    (theme_css_view의 no-store가 무시됨, 실측 확인) 관리자가 테마 색을 바꿔도 최대 몇 시간은
    예전 캐시가 그대로 보이는 문제가 있었다 — 색이 하나라도 바뀌면(ThemeColor.updated_at 갱신)
    쿼리스트링 자체가 달라져 Cloudflare 입장에서 아예 새 URL이 되므로, 캐시 설정을 못 건드리는
    상황에서도 우회해서 즉시 반영시킬 수 있다."""
    latest = ThemeColor.objects.aggregate(latest=Max('updated_at'))['latest']
    return {'theme_version': int(latest.timestamp()) if latest else 0}
