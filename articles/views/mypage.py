import logging
import secrets

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from ..email_utils import send_verification_email
from ..forms import BlogAccountForm, UserContactForm, UserPreferenceForm
from ..models import BlogPostingAccount, MemberGrade, PostedArticle, UserPreference, UserSubscription

logger = logging.getLogger(__name__)

FREE_MAX_BLOG_ACCOUNTS = 1  # 무료(비프리미엄) 회원이 동시에 연결할 수 있는 블로그 플랫폼 수


def _connected_platform_count(user, exclude_platform=None):
    """credential이 채워진(=실제로 연결된) BlogPostingAccount 개수. site_url/account_id만
    있고 credential이 비어 있으면(예: 워드프레스 폼에 URL만 입력하고 저장은 안 한 상태)
    연결로 치지 않는다 — my_page.html의 '연결됨' 배지(연동 credential 존재)와 같은 기준."""
    qs = BlogPostingAccount.objects.filter(user=user).exclude(credential='')
    if exclude_platform:
        qs = qs.exclude(platform=exclude_platform)
    return qs.count()


def _can_connect_blog_account(user, platform):
    """무료 회원은 블로그를 FREE_MAX_BLOG_ACCOUNTS(1)개까지만 연결할 수 있다 — 이미 연결된
    플랫폼을 다시 저장(비밀번호 변경 등)하는 건 개수가 늘지 않으므로 항상 허용되고, 아직
    연결 안 된 새 플랫폼을 추가로 연결하려 할 때만 이 제한이 걸린다. 프리미엄 구독자와
    staff/superuser는 무제한(posting_stats의 관리자 예외와 동일한 기준)."""
    if user.is_staff or user.is_superuser:
        return True
    subscription, _ = UserSubscription.objects.get_or_create(user=user)
    if subscription.is_active_premium:
        return True
    return _connected_platform_count(user, exclude_platform=platform) < FREE_MAX_BLOG_ACCOUNTS


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
        accounts_valid = all(f.is_valid() for f in account_forms.values())
        # is_valid()가 통과한 폼은 이 시점에 이미 construct_instance()로 instance에 빈 site_url/
        # account_id/credential을 채워 넣은 상태다(비밀번호 입력창은 항상 빈 값으로 렌더링되므로).
        # 아래 전체 저장 조건(contact_form 등)이 실패해 실제로는 저장하지 않고 폼을 다시 그릴
        # 때도 이 instance가 그대로 쓰이므로, 여기서 바로 sticky_fields를 복원해둬야 "연결 안 됨"
        # 으로 잘못 보이는 걸 막을 수 있다(실측 확인 — 이메일 중복 등으로 contact_form만 실패해도
        # 화면엔 블로그 계정이 전부 연결 해제된 것처럼 나왔었음. 실제 DB는 안 바뀌었었음).
        if accounts_valid:
            for platform_code, f in account_forms.items():
                for field in sticky_fields:
                    if not f.cleaned_data.get(field):
                        setattr(f.instance, field, original_values[platform_code][field])

        if contact_form.is_valid() and pref_form.is_valid() and accounts_valid:
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
            blocked_platforms = []
            for platform_code, f in account_forms.items():
                account = f.save(commit=False)  # sticky_fields는 위에서 이미 instance에 복원해뒀음
                newly_connecting = not original_values[platform_code]['credential'] and account.credential
                if newly_connecting and not _can_connect_blog_account(request.user, platform_code):
                    blocked_platforms.append(dict(BlogPostingAccount.PLATFORM_CHOICES)[platform_code])
                    continue
                account.save()
            if blocked_platforms:
                messages.warning(
                    request,
                    f"무료 회원은 블로그를 {FREE_MAX_BLOG_ACCOUNTS}개까지만 연결할 수 있어 "
                    f"{', '.join(blocked_platforms)} 연결은 저장되지 않았습니다. "
                    "프리미엄으로 업그레이드하면 여러 플랫폼을 동시에 연결할 수 있습니다."
                )
            messages.success(request, "설정이 저장되었습니다.")
            return redirect('my_page')
        else:
            # 필드별 에러는 각 입력창 아래 표시되지만 위치가 화면 하단(자동 포스팅)과 멀어
            # 눈에 안 띄기 쉬우므로, 저장 자체가 안 됐다는 걸 놓치지 않게 배너로도 알린다.
            messages.error(request, "일부 항목에 오류가 있어 설정이 저장되지 않았습니다. 아래 빨간색 안내를 확인해주세요.")
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
        'temp_password_active': preference.temp_password_active,
        'platform_forms': [
            (platform_code, label, account_forms[platform_code])
            for platform_code, label in BlogPostingAccount.PLATFORM_CHOICES
        ],
    }
    return render(request, 'articles/my_page.html', context)


@login_required
@require_POST
def change_password_view(request):
    """비밀번호 변경 — 임시 비밀번호로 로그인한 회원이 이 화면에서 새 비밀번호를 설정하면
    UserPreference.temp_password_active가 꺼져 로그인 시 안내 배너가 더 이상 뜨지 않는다.
    old_password를 요구하는 Django 기본 PasswordChangeForm을 그대로 쓴다 — 임시 비밀번호로
    막 로그인한 사용자도 그 비밀번호를 알고 있으므로 문제없다."""
    form = PasswordChangeForm(user=request.user, data=request.POST)
    if form.is_valid():
        form.save()
        update_session_auth_hash(request, form.user)  # 비밀번호 변경으로 세션이 끊기지 않게 유지

        preference, _ = UserPreference.objects.get_or_create(
            user=request.user, defaults={'grade': MemberGrade.default_grade()}
        )
        if preference.temp_password_active:
            preference.temp_password_active = False
            preference.save(update_fields=['temp_password_active'])

        messages.success(request, "비밀번호가 변경되었습니다.")
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect('my_page')


@login_required
@require_POST
def blog_account_disconnect_view(request, platform):
    """연결된 블로그 계정(credential 등)을 비운다. 워드프레스는 BlogAccountForm이 sticky_fields
    설계상 폼에서 빈 값을 제출해도 기존 값을 지우지 않으므로(연동 필드를 실수로 비우는 사고
    방지), 연결 해제는 이 별도 버튼/엔드포인트로만 가능하다 — 무료 회원이 다른 플랫폼으로
    바꾸려 해도 지금까지는 관리자가 /admin/에서 지워줘야 했던 것도 이걸로 해결된다."""
    valid_platforms = dict(BlogPostingAccount.PLATFORM_CHOICES)
    if platform not in valid_platforms:
        raise Http404

    account = BlogPostingAccount.objects.filter(user=request.user, platform=platform).first()
    if account and account.credential:
        account.credential = ''
        account.account_id = ''
        account.site_url = ''
        account.is_enabled = False
        account.save()
        messages.success(request, f"{valid_platforms[platform]} 연결을 해제했습니다.")
    return redirect('my_page')


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
    if not _can_connect_blog_account(request.user, 'BLOGGER'):
        messages.error(
            request,
            f"무료 회원은 블로그를 {FREE_MAX_BLOG_ACCOUNTS}개까지만 연결할 수 있습니다. "
            "프리미엄으로 업그레이드하면 여러 플랫폼을 동시에 연결할 수 있습니다."
        )
        return redirect('my_page')

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
    if not account.credential and not _can_connect_blog_account(request.user, 'BLOGGER'):
        messages.error(
            request,
            f"무료 회원은 블로그를 {FREE_MAX_BLOG_ACCOUNTS}개까지만 연결할 수 있습니다. "
            "프리미엄으로 업그레이드하면 여러 플랫폼을 동시에 연결할 수 있습니다."
        )
        return redirect('my_page')
    account.is_enabled = True
    account.account_id = blog.get('id', '')
    account.site_url = blog.get('url', '')
    if refresh_token:  # prompt=consent라 보통 항상 내려오지만, 방어적으로 있을 때만 갱신
        account.credential = refresh_token
    account.save()

    messages.success(request, f"블로거 '{blog.get('name', '')}' 블로그가 연결되었습니다!")
    return redirect('my_page')
