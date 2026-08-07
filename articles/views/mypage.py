import logging
import secrets

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render, redirect
from django.urls import reverse
from requests_oauthlib import OAuth1Session

from ..email_utils import send_verification_email
from ..forms import BlogAccountForm, UserContactForm, UserPreferenceForm
from ..models import BlogPostingAccount, MemberGrade, PostedArticle, UserPreference

logger = logging.getLogger(__name__)


# ==========================================
# 마이페이지 - 뉴스 구독 / 자동 포스팅 설정
# ==========================================

@login_required
def my_page_view(request):
    preference, _ = UserPreference.objects.get_or_create(
        user=request.user, defaults={'grade': MemberGrade.default_grade()}
    )

    accounts = {}
    for platform_code, _label in BlogPostingAccount.PLATFORM_CHOICES:
        accounts[platform_code], _ = BlogPostingAccount.objects.get_or_create(
            user=request.user, platform=platform_code
        )
    # ModelForm.is_valid()는 검증 과정에서 바로 instance에 값을 채워 넣으므로(construct_instance),
    # 기존 값들은 폼 바인딩 이전에 미리 따로 떼어둬야 함. site_url/account_id/credential은
    # 블로거(OAuth 연동)처럼 화면에 입력창 없이 자동으로만 채워지는 필드가 있어, 빈 값으로
    # 제출되면 기존 값을 덮어쓰지 않고 그대로 유지한다.
    sticky_fields = ('site_url', 'account_id', 'credential')
    original_values = {
        code: {field: getattr(acc, field) for field in sticky_fields}
        for code, acc in accounts.items()
    }

    if request.method == 'POST':
        # UserContactForm.is_valid()가 construct_instance()로 request.user.email을 즉시
        # (DB 저장 전에) 메모리상에서 바꿔버리므로, 비교용 원본 값은 그 전에 미리 떼어둬야 함
        original_email = request.user.email

        contact_form = UserContactForm(request.POST, instance=request.user)
        pref_form = UserPreferenceForm(request.POST, instance=preference)
        account_forms = {
            platform_code: BlogAccountForm(request.POST, instance=accounts[platform_code], prefix=platform_code)
            for platform_code, _label in BlogPostingAccount.PLATFORM_CHOICES
        }

        if contact_form.is_valid() and pref_form.is_valid() and all(f.is_valid() for f in account_forms.values()):
            # construct_instance()가 이미 request.user.first_name을 메모리상에 채워뒀으므로,
            # 이메일과 달리 인증 절차가 필요 없는 이름은 이 컬럼만 바로 저장한다.
            request.user.save(update_fields=['first_name'])
            new_email = contact_form.cleaned_data['email']
            if new_email and new_email != original_email:
                # 이메일은 바로 반영하지 않고, 인증 완료 후에만 실제로 변경됨
                # preference를 넘겨야 아래 pref_form.save()가 이 값을 덮어쓰지 않음
                send_verification_email(request, request.user, new_email, preference=preference)
                messages.info(
                    request,
                    f"{new_email}로 인증 메일을 보냈습니다. 메일함에서 링크를 눌러야 이메일 변경이 완료됩니다."
                )
            pref_form.save()
            for platform_code, f in account_forms.items():
                account = f.save(commit=False)
                for field in sticky_fields:
                    if not f.cleaned_data.get(field):
                        setattr(account, field, original_values[platform_code][field])
                account.save()
            messages.success(request, "설정이 저장되었습니다.")
            return redirect('my_page')
    else:
        contact_form = UserContactForm(instance=request.user)
        pref_form = UserPreferenceForm(instance=preference)
        account_forms = {
            platform_code: BlogAccountForm(instance=accounts[platform_code], prefix=platform_code)
            for platform_code, _label in BlogPostingAccount.PLATFORM_CHOICES
        }

    context = {
        'site_title': 'NextFinUp - 내 정보 관리',
        'contact_form': contact_form,
        'pref_form': pref_form,
        'pending_email': preference.pending_email,
        'platform_forms': [
            (platform_code, label, account_forms[platform_code])
            for platform_code, label in BlogPostingAccount.PLATFORM_CHOICES
        ],
    }
    return render(request, 'articles/my_page.html', context)


@login_required
def my_posted_articles_view(request):
    """마이페이지 '4. 자동 포스팅'에서 링크로 들어오는, 내 블로그 계정으로 발행한 글 이력.
    PostedArticle이 계정×기사 단위로 이미 기록해두고 있어(발행된 글 실제 URL 포함) 화면만
    새로 만들면 된다."""
    postings = (
        PostedArticle.objects
        .filter(blog_account__user=request.user)
        .select_related('article', 'blog_account')
        .order_by('-posted_at')
    )

    paginator = Paginator(postings, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'site_title': 'NextFinUp - 내 포스팅 이력',
        'page_obj': page_obj,
    }
    return render(request, 'articles/my_posted_articles.html', context)


# ==========================================
# 마이페이지 - 블로거(Blogger) OAuth 연동
# 사이트 로그인용 구글 소셜 로그인과는 별개의 플로우: 이미 로그인된 회원이
# 자동 포스팅용으로 자신의 블로거 블로그를 연결(scope=Blogger API)하는 용도
# ==========================================

@login_required
def blogger_connect_view(request):
    state = secrets.token_urlsafe(16)
    request.session['blogger_oauth_state'] = state

    redirect_uri = request.build_absolute_uri(reverse('blogger_callback'))
    authorize_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=https://www.googleapis.com/auth/blogger"
        "&access_type=offline"
        "&prompt=consent"  # 리프레시 토큰을 매번 새로 받기 위해 재동의 강제
        f"&state={state}"
    )
    return redirect(authorize_url)


@login_required
def blogger_callback_view(request):
    code = request.GET.get('code')
    state = request.GET.get('state')
    expected_state = request.session.pop('blogger_oauth_state', None)
    if not code or not state or state != expected_state:
        messages.error(request, "블로거 연동이 취소되었거나 유효하지 않은 요청입니다.")
        return redirect('my_page')

    redirect_uri = request.build_absolute_uri(reverse('blogger_callback'))
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
    refresh_token = token_res.get('refresh_token')
    if not access_token:
        messages.error(request, f"블로거 연동 실패: {token_res.get('error_description', '알 수 없는 오류')}")
        return redirect('my_page')

    blogs_res = requests.get(
        "https://www.googleapis.com/blogger/v3/users/self/blogs",
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=10,
    ).json()
    logger.warning("Blogger blogs.list response for %s: %s", request.user.username, blogs_res)

    if 'error' in blogs_res:
        error_detail = blogs_res['error'].get('message', str(blogs_res['error']))
        messages.error(request, f"블로거 API 오류: {error_detail}")
        return redirect('my_page')

    blogs = blogs_res.get('items') or []
    if not blogs:
        messages.error(request, "연결 가능한 블로거 블로그가 없습니다. blogger.com에서 블로그를 먼저 만들어주세요.")
        return redirect('my_page')

    blog = blogs[0]  # 여러 개의 블로그가 있으면 첫 번째 블로그를 사용
    account, _ = BlogPostingAccount.objects.get_or_create(user=request.user, platform='BLOGGER')
    account.is_enabled = True
    account.account_id = blog.get('id', '')
    account.site_url = blog.get('url', '')
    if refresh_token:  # prompt=consent라 보통 항상 내려오지만, 방어적으로 있을 때만 갱신
        account.credential = refresh_token
    account.save()

    messages.success(request, f"블로거 '{blog.get('name', '')}' 블로그가 연결되었습니다!")
    return redirect('my_page')


# ==========================================
# 마이페이지 - 텀블러(Tumblr) OAuth 1.0a 연동
# 텀블러 API v2는 OAuth 2.0이 아니라 구식 OAuth 1.0a(3-legged: request token → 사용자 승인 →
# access token 교환)를 쓴다 — 블로거처럼 인가 코드 하나로 끝나지 않고, 임시 request
# token/secret을 세션에 잠깐 보관했다가 콜백에서 access token으로 교환해야 한다. 발급받은
# access token(oauth_token)과 그 짝인 oauth_token_secret 둘 다 있어야 이후 API 호출 서명이
# 유효해서(articles/blog_posting.py의 _tumblr_auth), 두 값 다 BlogPostingAccount에 저장한다.
# ==========================================

TUMBLR_REQUEST_TOKEN_URL = "https://www.tumblr.com/oauth/request_token"
TUMBLR_AUTHORIZE_URL = "https://www.tumblr.com/oauth/authorize"
TUMBLR_ACCESS_TOKEN_URL = "https://www.tumblr.com/oauth/access_token"


@login_required
def tumblr_connect_view(request):
    callback_uri = request.build_absolute_uri(reverse('tumblr_callback'))
    tumblr = OAuth1Session(
        settings.TUMBLR_CONSUMER_KEY, client_secret=settings.TUMBLR_CONSUMER_SECRET,
        callback_uri=callback_uri,
    )
    try:
        request_token = tumblr.fetch_request_token(TUMBLR_REQUEST_TOKEN_URL)
    except Exception as e:
        messages.error(request, f"텀블러 연동 요청 실패: {e}")
        return redirect('my_page')

    # access token 교환(콜백)까지는 요청 토큰 자체가 인증에 필요한 자격정보라, 계정 테이블이
    # 아니라 임시로 세션에만 둔다(블로거의 state 파라미터와 같은 역할 — 콜백이 진짜 이 흐름에서
    # 이어진 요청인지 검증).
    request.session['tumblr_request_token'] = request_token.get('oauth_token')
    request.session['tumblr_request_token_secret'] = request_token.get('oauth_token_secret')

    return redirect(tumblr.authorization_url(TUMBLR_AUTHORIZE_URL))


@login_required
def tumblr_callback_view(request):
    oauth_token = request.GET.get('oauth_token')
    oauth_verifier = request.GET.get('oauth_verifier')
    saved_token = request.session.pop('tumblr_request_token', None)
    saved_secret = request.session.pop('tumblr_request_token_secret', None)
    if not oauth_verifier or not saved_token or oauth_token != saved_token:
        messages.error(request, "텀블러 연동이 취소되었거나 유효하지 않은 요청입니다.")
        return redirect('my_page')

    tumblr = OAuth1Session(
        settings.TUMBLR_CONSUMER_KEY, client_secret=settings.TUMBLR_CONSUMER_SECRET,
        resource_owner_key=saved_token, resource_owner_secret=saved_secret,
        verifier=oauth_verifier,
    )
    try:
        access_token_res = tumblr.fetch_access_token(TUMBLR_ACCESS_TOKEN_URL)
    except Exception as e:
        messages.error(request, f"텀블러 연동 실패: {e}")
        return redirect('my_page')

    access_token = access_token_res.get('oauth_token')
    access_token_secret = access_token_res.get('oauth_token_secret')
    if not access_token or not access_token_secret:
        messages.error(request, "텀블러 연동 실패: 액세스 토큰을 받지 못했습니다.")
        return redirect('my_page')

    # fetch_access_token()이 세션 내부 토큰을 access token으로 갱신해두므로, 뒤이은 호출은
    # 새로 딴 access token으로 서명된다(요청 토큰이 아니라).
    try:
        info_res = tumblr.get("https://api.tumblr.com/v2/user/info", timeout=10).json()
    except Exception as e:
        messages.error(request, f"텀블러 블로그 목록 조회 실패: {e}")
        return redirect('my_page')

    blogs = ((info_res.get('response') or {}).get('user') or {}).get('blogs') or []
    if not blogs:
        messages.error(request, "연결 가능한 텀블러 블로그가 없습니다. tumblr.com에서 블로그를 먼저 만들어주세요.")
        return redirect('my_page')

    blog = blogs[0]  # 여러 개의 블로그가 있으면 첫 번째(기본) 블로그를 사용
    account, _ = BlogPostingAccount.objects.get_or_create(user=request.user, platform='TUMBLR')
    account.is_enabled = True
    account.account_id = blog.get('name', '')  # API 호출에 쓰는 blog-identifier
    account.site_url = blog.get('url', '')
    account.credential = access_token
    account.oauth_token_secret = access_token_secret
    account.save()

    messages.success(request, f"텀블러 '{blog.get('title') or blog.get('name', '')}' 블로그가 연결되었습니다!")
    return redirect('my_page')
