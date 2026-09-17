import json
from datetime import datetime, time

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import chatbot_client
from ..models import ChatbotSetting, ChatMessage
from ..utils import get_client_ip


def chat_limit_reached(request, session_key, ip_address, limit):
    """오늘(한국 시간 0시부터) 보낸 질문 수가 한도에 닿았는지. 로그인 회원은 계정별로 세고,
    비로그인은 세션별·IP별로 세어 둘 중 하나라도 닿으면 막는다(쿠키를 지워 새 세션을 만들어도
    같은 IP면 막힌다). 로그인 회원을 IP로 막지 않는 건 회사·학교처럼 IP를 나눠 쓰는 경우 때문이다.
    한도 0과 스태프 계정은 무제한."""
    if not limit or request.user.is_staff:
        return False
    today_start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    asked = ChatMessage.objects.filter(role='user', created_at__gte=today_start)
    if request.user.is_authenticated:
        return asked.filter(user=request.user).count() >= limit
    if asked.filter(session_key=session_key).count() >= limit:
        return True
    return bool(ip_address) and asked.filter(ip_address=ip_address).count() >= limit


@require_POST
def chatbot_ask_view(request):
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'error': '잘못된 요청입니다.'}, status=400)

    question = (payload.get('message') or '').strip()
    history = payload.get('history') or []

    if not question:
        return JsonResponse({'error': '질문을 입력해주세요.'}, status=400)
    if len(question) > 500:
        return JsonResponse({'error': '질문은 500자 이내로 입력해주세요.'}, status=400)

    if not request.session.session_key:
        request.session.save()
    session_key = request.session.session_key
    user = request.user if request.user.is_authenticated else None
    ip_address = get_client_ip(request)

    setting = ChatbotSetting.load()
    if chat_limit_reached(request, session_key, ip_address, setting.daily_chat_limit):
        return JsonResponse(
            {'error': f'오늘 질문 가능 횟수({setting.daily_chat_limit}회)를 모두 사용했습니다. 내일 다시 이용해주세요.'},
            status=429,
        )

    answer = chatbot_client.ask(question, history, setting=setting)

    ChatMessage.objects.bulk_create([
        ChatMessage(user=user, session_key=session_key, ip_address=ip_address, role='user', content=question),
        ChatMessage(user=user, session_key=session_key, ip_address=ip_address, role='assistant', content=answer),
    ])

    return JsonResponse({'answer': answer})
