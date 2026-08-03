import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.http import require_POST

from ..email_utils import TOKEN_VALID_HOURS, send_verification_email
from ..forms import LoginForm, SignUpForm
from ..models import LoginLog, MemberGrade, SocialAccount, UserPreference, UserSubscription
from ..utils import get_client_ip


def _create_subscription_if_missing(user):
    UserSubscription.objects.get_or_create(user=user)


def _log_login(request, user, method):
    LoginLog.objects.create(
        user=user,
        login_method=method,
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:255],
    )


def _get_or_create_social_user(provider, provider_uid, email, nickname):
    """provider+provider_uid로 기존 연동 유저를 찾고, 없으면 새 User/구독/소셜계정 레코드를 생성합니다."""
    social_account = SocialAccount.objects.filter(provider=provider, provider_uid=provider_uid).first()
    if social_account:
        user = social_account.user
        if nickname and user.first_name != nickname[:30]:
            user.first_name = nickname[:30]
            user.save(update_fields=['first_name'])
        return user

    base_username = f"{provider.lower()}_{provider_uid}"
    username = base_username
    suffix = 1
    while User.objects.filter(username=username).exists():
        suffix += 1
        username = f"{base_username}_{suffix}"

    user = User.objects.create_user(username=username, email=email or '')
    user.set_unusable_password()  # 소셜 로그인 전용 계정은 자체 비밀번호를 사용하지 않음
    if nickname:
        user.first_name = nickname[:30]
    user.save()

    SocialAccount.objects.create(provider=provider, provider_uid=provider_uid, user=user, email=email)
    _create_subscription_if_missing(user)
    return user


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('landing_page')

    form = SignUpForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        # 이메일 인증 전까지는 로그인할 수 없도록 비활성 상태로 생성 (인증 완료 시 verify_email_view에서 활성화)
        user = User.objects.create_user(
            username=form.cleaned_data['username'],
            password=form.cleaned_data['password1'],
            is_active=False,
        )
        _create_subscription_if_missing(user)
        send_verification_email(request, user, form.cleaned_data['email'])
        messages.success(
            request,
            f"{form.cleaned_data['email']}로 인증 메일을 보냈습니다 — "
            "메일함에서 링크를 눌러야 회원가입이 완료됩니다."
        )
        return redirect('login')

    return render(request, 'articles/signup.html', {'form': form, 'site_title': 'NextFinUp - 회원가입'})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('landing_page')

    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        _log_login(request, form.get_user(), 'GENERAL')
        messages.success(request, f"{form.get_user().username}님, 환영합니다!")
        return redirect('landing_page')

    return render(request, 'articles/login.html', {'form': form, 'site_title': 'NextFinUp - 로그인'})


def logout_view(request):
    logout(request)
    return redirect('landing_page')


@login_required
@require_POST
def delete_account_view(request):
    # User를 지우면 관련 테이블(UserSubscription/UserPreference/BlogPostingAccount/SocialAccount/
    # LoginLog/MenuAccessLog/ChatMessage)이 전부 on_delete=CASCADE로 함께 삭제된다.
    user = request.user
    logout(request)
    user.delete()
    messages.success(request, "회원 탈퇴가 완료되었습니다. 그동안 이용해주셔서 감사합니다.")
    return redirect('landing_page')


def verify_email_view(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        messages.error(request, "유효하지 않은 인증 링크입니다.")
        return redirect('login')

    preference, _ = UserPreference.objects.get_or_create(
        user=user, defaults={'grade': MemberGrade.default_grade()}
    )
    is_valid_token = (
        preference.pending_email
        and preference.email_verification_token
        and preference.email_verification_token == token
        and preference.email_verification_sent_at
        and timezone.now() - preference.email_verification_sent_at < timedelta(hours=TOKEN_VALID_HOURS)
    )
    if not is_valid_token:
        messages.error(request, "인증 링크가 만료되었거나 유효하지 않습니다. 마이페이지에서 다시 시도해주세요.")
        return redirect('my_page' if request.user.is_authenticated else 'login')

    # 아직 비활성 상태(is_active=False)라면 최초 회원가입 인증 링크 — 이 시점에 계정을 활성화하고 로그인시킨다.
    is_first_signup_verification = not user.is_active
    user.email = preference.pending_email
    if is_first_signup_verification:
        user.is_active = True
        user.save(update_fields=['email', 'is_active'])
    else:
        user.save(update_fields=['email'])

    preference.is_email_verified = True
    preference.pending_email = ''
    preference.email_verification_token = ''
    preference.email_verification_sent_at = None
    preference.save()

    if is_first_signup_verification:
        login(request, user)
        _log_login(request, user, 'SIGNUP')
        messages.success(request, "이메일 인증이 완료되어 회원가입이 완료되었습니다! 환영합니다.")
        return redirect('landing_page')

    messages.success(request, "이메일 인증이 완료되었습니다!")
    return redirect('my_page' if request.user.is_authenticated else 'login')


def kakao_login_view(request):
    state = secrets.token_urlsafe(16)
    request.session['kakao_oauth_state'] = state

    redirect_uri = request.build_absolute_uri(reverse('kakao_callback'))
    authorize_url = (
        "https://kauth.kakao.com/oauth/authorize"
        f"?client_id={settings.KAKAO_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=profile_nickname"
        f"&state={state}"
    )
    return redirect(authorize_url)


def kakao_callback_view(request):
    code = request.GET.get('code')
    state = request.GET.get('state')
    expected_state = request.session.pop('kakao_oauth_state', None)
    if not code or not state or state != expected_state:
        messages.error(request, "카카오 로그인이 취소되었거나 유효하지 않은 요청입니다.")
        return redirect('login')

    redirect_uri = request.build_absolute_uri(reverse('kakao_callback'))
    token_payload = {
        'grant_type': 'authorization_code',
        'client_id': settings.KAKAO_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'code': code,
    }
    if settings.KAKAO_CLIENT_SECRET:
        # 카카오 콘솔에서 Client Secret을 "사용함"으로 설정한 경우에만 전달
        # (미설정 상태에서 값을 보내면 KOE320 오류로 거부됩니다)
        token_payload['client_secret'] = settings.KAKAO_CLIENT_SECRET

    token_res = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data=token_payload,
        timeout=10,
    ).json()

    access_token = token_res.get('access_token')
    if not access_token:
        messages.error(request, f"카카오 로그인 실패: {token_res.get('error_description', '알 수 없는 오류')}")
        return redirect('login')

    profile_res = requests.get(
        "https://kapi.kakao.com/v2/user/me",
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=10,
    ).json()

    provider_uid = str(profile_res.get('id'))
    kakao_account = profile_res.get('kakao_account', {})
    email = kakao_account.get('email')
    nickname = kakao_account.get('profile', {}).get('nickname')

    user = _get_or_create_social_user('KAKAO', provider_uid, email, nickname)
    login(request, user)
    _log_login(request, user, 'KAKAO')
    return redirect('landing_page')


def google_login_view(request):
    state = secrets.token_urlsafe(16)
    request.session['google_oauth_state'] = state

    redirect_uri = request.build_absolute_uri(reverse('google_callback'))
    authorize_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=openid%20email%20profile"
        f"&state={state}"
    )
    return redirect(authorize_url)


def google_callback_view(request):
    code = request.GET.get('code')
    state = request.GET.get('state')
    expected_state = request.session.pop('google_oauth_state', None)
    if not code or not state or state != expected_state:
        messages.error(request, "구글 로그인이 취소되었거나 유효하지 않은 요청입니다.")
        return redirect('login')

    redirect_uri = request.build_absolute_uri(reverse('google_callback'))
    token_res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            'grant_type': 'authorization_code',
            'client_id': settings.GOOGLE_CLIENT_ID,
            'client_secret': settings.GOOGLE_CLIENT_SECRET,
            'redirect_uri': redirect_uri,
            'code': code,
        },
        timeout=10,
    ).json()

    access_token = token_res.get('access_token')
    if not access_token:
        messages.error(request, f"구글 로그인 실패: {token_res.get('error_description', '알 수 없는 오류')}")
        return redirect('login')

    profile_res = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=10,
    ).json()

    provider_uid = profile_res.get('sub')
    email = profile_res.get('email')
    nickname = profile_res.get('name')

    user = _get_or_create_social_user('GOOGLE', provider_uid, email, nickname)
    login(request, user)
    _log_login(request, user, 'GOOGLE')
    return redirect('landing_page')


def naver_login_view(request):
    state = secrets.token_urlsafe(16)
    request.session['naver_oauth_state'] = state

    redirect_uri = request.build_absolute_uri(reverse('naver_callback'))
    authorize_url = (
        "https://nid.naver.com/oauth2.0/authorize"
        "?response_type=code"
        f"&client_id={settings.NAVER_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    return redirect(authorize_url)


def naver_callback_view(request):
    code = request.GET.get('code')
    state = request.GET.get('state')
    expected_state = request.session.pop('naver_oauth_state', None)
    if not code or not state or state != expected_state:
        messages.error(request, "네이버 로그인이 취소되었거나 유효하지 않은 요청입니다.")
        return redirect('login')

    redirect_uri = request.build_absolute_uri(reverse('naver_callback'))
    token_res = requests.post(
        "https://nid.naver.com/oauth2.0/token",
        data={
            'grant_type': 'authorization_code',
            'client_id': settings.NAVER_CLIENT_ID,
            'client_secret': settings.NAVER_CLIENT_SECRET,
            'redirect_uri': redirect_uri,
            'code': code,
            'state': state,
        },
        timeout=10,
    ).json()

    access_token = token_res.get('access_token')
    if not access_token:
        messages.error(request, f"네이버 로그인 실패: {token_res.get('error_description', '알 수 없는 오류')}")
        return redirect('login')

    profile_res = requests.get(
        "https://openapi.naver.com/v1/nid/me",
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=10,
    ).json()
    naver_account = profile_res.get('response', {})

    provider_uid = naver_account.get('id')
    email = naver_account.get('email')
    nickname = naver_account.get('nickname') or naver_account.get('name')

    user = _get_or_create_social_user('NAVER', provider_uid, email, nickname)
    login(request, user)
    _log_login(request, user, 'NAVER')
    return redirect('landing_page')
