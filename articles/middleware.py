from .models import MenuAccessLog
from .utils import get_client_ip

# 로그인 사용자의 접속을 "메뉴 접속 로그"로 남길 화면(URL name) 목록.
# 신규 메뉴(페이지)가 추가되면 여기에 url_name만 추가하면 됨.
MENU_URL_NAMES = {
    'landing_page',
    'main_dashboard',
    'my_page',
    'news_board',
    'news_detail',
    'stock_detail',
    'expert_consult',
}


class MenuAccessLogMiddleware:
    """로그인한 사용자가 메뉴(페이지)에 접속할 때마다 MenuAccessLog를 기록."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if (
            request.method == 'GET'
            and request.user.is_authenticated
            and request.resolver_match
            and request.resolver_match.url_name in MENU_URL_NAMES
        ):
            MenuAccessLog.objects.create(
                user=request.user,
                menu_name=request.resolver_match.url_name,
                path=request.path,
                ip_address=get_client_ip(request),
            )

        return response
