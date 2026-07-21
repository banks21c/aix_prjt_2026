from .models import Menu


def menu_items(request):
    """모든 템플릿에서 {{ index_menus }} / {{ header_menus }}로 활성 메뉴를 바로 쓸 수 있게 주입.
    Menu.menu_type으로 랜딩(index) 페이지와 내부 앱 공통 헤더의 노출 목록을 구분한다."""
    menus = Menu.objects.filter(is_active=True)
    return {
        'index_menus': [m for m in menus if m.menu_type == 'INDEX'],
        'header_menus': [m for m in menus if m.menu_type == 'HEADER'],
    }
