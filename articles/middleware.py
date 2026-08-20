from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import MenuAccessLog
from .utils import get_client_ip

KST = ZoneInfo('Asia/Seoul')


class KSTMiddleware:
    """settings.TIME_ZONE은 UTC로 고정돼 있다(deploy/crontab이 UTC 기준이라 서버/Django
    시간대가 어긋나면 안 됨) — 하지만 DB에는 그대로 UTC로 저장해두고(USE_TZ=True), 템플릿에
    보여줄 때만 이 미들웨어로 요청 스레드의 활성 시간대를 KST로 바꿔치기한다. 이걸 안 해두면
    {{ article.scraped_at }} 같은 표시가 UTC 그대로 나가 한국 사용자 눈엔 미국 시간처럼 보인다
    (실제 신고 사례: "마감후 특징주 브리핑"의 06:40이 실제론 KST 15:40인데 그대로 노출됨)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        timezone.activate(KST)
        return self.get_response(request)

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
    'faq_board',
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
