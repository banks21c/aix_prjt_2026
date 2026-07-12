import secrets

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import UserPreference

TOKEN_VALID_HOURS = 24


def send_verification_email(request, user, new_email, preference=None):
    """new_email을 인증 대기 상태로 등록하고, 인증 링크가 담긴 메일을 발송한다.

    preference를 이미 들고 있는 호출부(my_page_view 등)는 반드시 그 인스턴스를 넘겨야 한다.
    여기서 새로 조회해서 저장하면, 호출부가 나중에 같은 UserPreference 행을 다시 저장할 때
    (예: pref_form.save()) 이 함수가 방금 기록한 값을 오래된 값으로 덮어써버리기 때문이다.
    """
    if preference is None:
        preference, _ = UserPreference.objects.get_or_create(user=user)
    token = secrets.token_urlsafe(32)
    preference.pending_email = new_email
    preference.email_verification_token = token
    preference.email_verification_sent_at = timezone.now()
    preference.save()

    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    verify_url = request.build_absolute_uri(reverse('verify_email', args=[uidb64, token]))

    send_mail(
        subject="[NextFinUp] 이메일 인증을 완료해주세요",
        message=(
            "NextFinUp 이메일 인증 요청입니다.\n\n"
            f"아래 링크를 눌러 이메일 인증을 완료해주세요 (24시간 이내에 유효):\n{verify_url}\n\n"
            "본인이 요청한 것이 아니라면 이 메일을 무시하셔도 됩니다."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[new_email],
        fail_silently=False,
    )
