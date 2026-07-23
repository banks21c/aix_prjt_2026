import json

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .. import chatbot_client
from ..models import ChatMessage


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

    answer = chatbot_client.ask(question, history)

    ChatMessage.objects.bulk_create([
        ChatMessage(user=user, session_key=session_key, role='user', content=question),
        ChatMessage(user=user, session_key=session_key, role='assistant', content=answer),
    ])

    return JsonResponse({'answer': answer})
