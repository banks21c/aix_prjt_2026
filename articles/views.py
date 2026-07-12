import json
import logging
import secrets
from datetime import date, datetime, timedelta, timezone as dt_timezone

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.http import require_POST

from . import chatbot_client, kis_client
from .email_utils import TOKEN_VALID_HOURS, send_verification_email
from .forms import SignUpForm, UserPreferenceForm, BlogAccountForm, UserContactForm
from .models import (
    StockItem, StockPrediction, AnalyzedArticle, UserSubscription, SocialAccount,
    MarketIndex, RankedMover, ChatMessage, LoginLog, UserPreference, BlogPostingAccount,
    StockRealtimePrice,
)
from .utils import get_client_ip

logger = logging.getLogger(__name__)

def landing_page_view(request):
    active_stock_count = StockItem.objects.filter(is_active=True).count()
    latest_articles = AnalyzedArticle.objects.select_related('stock').order_by('-scraped_at')[:3]

    context = {
        'site_title': 'NextFinUp - AI 차세대 자산 분석 포털',
        'active_stock_count': active_stock_count,
        'latest_articles': latest_articles,
    }
    return render(request, 'articles/index.html', context)


def _build_index_chart(market_type, days=90):
    """코스피/코스닥 지수 일봉 차트용 데이터: 최근 N일 OHLC 시계열 + 최신 지수/등락/등락% 값"""
    rows = list(MarketIndex.objects.filter(market_type=market_type).order_by('-date')[:days])
    rows.reverse()  # 차트는 과거 -> 현재 순으로 그려야 하므로 날짜 오름차순으로 뒤집음
    latest = rows[-1] if rows else None
    ohlc = [
        {
            'time': r.date.strftime('%Y-%m-%d'),
            'open': float(r.open_price if r.open_price is not None else r.close_price),
            'high': float(r.high_price if r.high_price is not None else r.close_price),
            'low': float(r.low_price if r.low_price is not None else r.close_price),
            'close': float(r.close_price),
        }
        for r in rows
    ]
    return {
        'ohlc': ohlc,
        'latest': latest,
    }


def main_dashboard_view(request):
    # ---- 1행: 코스피/코스닥 지수 차트(지수/등락/등락%) ----
    kospi_index = _build_index_chart('KOSPI')
    kosdaq_index = _build_index_chart('KOSDAQ')

    # ---- 2행: 주요뉴스 (전체 종목 통틀어 가장 최근 수집된 기사) ----
    major_news = AnalyzedArticle.objects.select_related('stock', 'matched_keyword').order_by('-scraped_at')[:6]

    # ---- 2행: 특징종목 (한국투자증권 등락률 순위 API 기준 상승률 상위 5개 + 하락률 상위 5개) ----
    top_gainers = list(RankedMover.objects.filter(rank_type='GAINER').order_by('rank'))
    top_losers = list(RankedMover.objects.filter(rank_type='LOSER').order_by('rank'))
    featured_stocks = top_gainers + top_losers

    latest_pred_date = StockPrediction.objects.aggregate(m=Max('date'))['m']

    # ---- 3행: 파이썬(Django ORM)이 집계한 분석 그래프 ----
    # (a) 특징종목(상승률/하락률 상위) 등락률 바 차트
    mover_labels = [p.name for p in featured_stocks]
    mover_values = [p.change_pct for p in featured_stocks]

    # (b) 활성 종목 매매 시그널(매수/매도/관망) 분포
    signal_values = [0, 0, 0]
    if latest_pred_date:
        signal_counts = (
            StockPrediction.objects
            .filter(date=latest_pred_date, stock__is_active=True)
            .values('trading_signal')
            .annotate(count=Count('id'))
        )
        signal_map = {row['trading_signal']: row['count'] for row in signal_counts}
        signal_values = [signal_map.get('BUY', 0), signal_map.get('SELL', 0), signal_map.get('HOLD', 0)]

    context = {
        'site_title': 'NextFinUp - AI 차세대 자산 분석 포털',
        'kospi_index': kospi_index,
        'kosdaq_index': kosdaq_index,
        'major_news': major_news,
        'top_gainers': top_gainers,
        'top_losers': top_losers,
        'kospi_ohlc': kospi_index['ohlc'],
        'kosdaq_ohlc': kosdaq_index['ohlc'],
        'mover_labels': mover_labels,
        'mover_values': mover_values,
        'signal_labels': ['매수', '매도', '관망'],
        'signal_values': signal_values,
    }
    return render(request, 'articles/dashboard.html', context)


# ==========================================
# 뉴스 게시판 (수집된 AI 분석 기사 목록/상세)
# ==========================================

def news_board_view(request):
    query = request.GET.get('q', '').strip()

    articles = AnalyzedArticle.objects.select_related('stock', 'matched_keyword').order_by('-scraped_at')
    if query:
        articles = articles.filter(
            Q(title__icontains=query) | Q(stock__name__icontains=query) | Q(matched_keyword__keyword__icontains=query)
        )

    paginator = Paginator(articles, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'site_title': 'NextFinUp - 뉴스 게시판',
        'page_obj': page_obj,
        'query': query,
    }
    return render(request, 'articles/news_board.html', context)


def news_detail_view(request, pk):
    article = get_object_or_404(AnalyzedArticle.objects.select_related('stock', 'matched_keyword'), pk=pk)
    context = {
        'site_title': f'NextFinUp - {article.title}',
        'article': article,
    }
    return render(request, 'articles/news_detail.html', context)


# ==========================================
# 종목 상세 (자체 수집한 10년치 일봉 + 최신 AI 예측 + 관련 뉴스)
# ==========================================

def stock_detail_view(request, ticker):
    stock = get_object_or_404(StockItem, ticker=ticker)

    predictions = StockPrediction.objects.filter(stock=stock).order_by('-date')
    latest = predictions.first()

    history = list(predictions[:180])  # 최근 180거래일 정도만 차트에 표시
    history.reverse()
    ohlc = [
        {
            'time': p.date.strftime('%Y-%m-%d'),
            'open': float(p.open_price),
            'high': float(p.high_price),
            'low': float(p.low_price),
            'close': float(p.close_price),
        }
        for p in history
    ]

    # 코스피200/코스닥150 밖이라 collect_stock_data로 10년치를 수집해두지 않은 종목은,
    # KIS 기간별시세 API로 최근 일봉만 온디맨드로 가져와서 보여준다.
    if not ohlc:
        try:
            end_date = date.today()
            start_date = end_date - timedelta(days=180)
            kis_rows = kis_client.get_stock_daily_price(
                stock.ticker, start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d')
            )
            ohlc = [
                {
                    'time': f"{row['date'][:4]}-{row['date'][4:6]}-{row['date'][6:]}",
                    'open': row['open'], 'high': row['high'], 'low': row['low'], 'close': row['close'],
                }
                for row in kis_rows
            ]
        except Exception:
            logger.exception("KIS 종목 일봉 조회 실패: %s", stock.ticker)

    news = AnalyzedArticle.objects.filter(stock=stock).select_related('matched_keyword').order_by('-scraped_at')[:10]

    # KIS를 매 요청마다 직접 호출하지 않고, collect_stock_realtime_price 명령이 주기적으로
    # 갱신해둔 캐시(StockRealtimePrice)만 읽는다.
    realtime_cache = StockRealtimePrice.objects.filter(stock=stock).first()
    realtime = None
    if realtime_cache:
        realtime = {
            'close': realtime_cache.close_price,
            'open': realtime_cache.open_price,
            'high': realtime_cache.high_price,
            'low': realtime_cache.low_price,
            'change': realtime_cache.change,
            'change_pct': realtime_cache.change_pct,
            'updated_at': realtime_cache.updated_at,
        }

    context = {
        'site_title': f'NextFinUp - {stock.name}',
        'stock': stock,
        'latest': latest,
        'ohlc': ohlc,
        'news': news,
        'realtime': realtime,
    }
    return render(request, 'articles/stock_detail.html', context)


KST = dt_timezone(timedelta(hours=9))


def stock_minute_chart_view(request, ticker):
    """종목 상세 페이지의 '분봉' 토글이 눌렸을 때만 호출되는 온디맨드 당일 분봉 API."""
    stock = get_object_or_404(StockItem, ticker=ticker)
    try:
        rows = kis_client.get_today_minute_prices(stock.ticker)
    except Exception:
        logger.exception("KIS 당일분봉 조회 실패: %s", stock.ticker)
        return JsonResponse({'error': '당일 분봉을 불러오지 못했습니다.'}, status=502)

    ohlc = []
    for row in rows:
        # KIS 시각은 한국시간(KST) 벽시계 값이라, UTC 오프셋(+9)을 명시해야 올바른 유닉스 타임스탬프가 나옴
        kst_dt = datetime.strptime(f"{row['date']}{row['time']}", '%Y%m%d%H%M%S').replace(tzinfo=KST)
        ohlc.append({
            'time': int(kst_dt.timestamp()),
            'open': row['open'], 'high': row['high'], 'low': row['low'], 'close': row['close'],
        })
    return JsonResponse({'ohlc': ohlc})


# ==========================================
# 주식/경제 챗봇 (인덱스 페이지 플로팅 위젯)
# ==========================================

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


# ==========================================
# 회원가입 / 로그인 / 소셜 로그인(카카오·구글)
# ==========================================

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
        user = User.objects.create_user(
            username=form.cleaned_data['username'],
            password=form.cleaned_data['password1'],
        )
        _create_subscription_if_missing(user)
        send_verification_email(request, user, form.cleaned_data['email'])  # 인증 완료 전까지 User.email은 비워둠
        login(request, user)
        _log_login(request, user, 'SIGNUP')
        messages.success(
            request,
            f"회원가입이 완료되었습니다. {form.cleaned_data['email']}로 인증 메일을 보냈습니다 — "
            "메일함에서 링크를 눌러 이메일 인증을 완료해주세요."
        )
        return redirect('landing_page')

    return render(request, 'articles/signup.html', {'form': form, 'site_title': 'NextFinUp - 회원가입'})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('landing_page')

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        _log_login(request, form.get_user(), 'GENERAL')
        messages.success(request, f"{form.get_user().username}님, 환영합니다!")
        return redirect('landing_page')

    return render(request, 'articles/login.html', {'form': form, 'site_title': 'NextFinUp - 로그인'})


def logout_view(request):
    logout(request)
    return redirect('landing_page')


# ==========================================
# 마이페이지 - 뉴스 구독 / 자동 포스팅 설정
# ==========================================

@login_required
def my_page_view(request):
    preference, _ = UserPreference.objects.get_or_create(user=request.user)

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


# ==========================================
# 이메일 인증 (회원가입 / 마이페이지 이메일 변경 공용)
# ==========================================

def verify_email_view(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        messages.error(request, "유효하지 않은 인증 링크입니다.")
        return redirect('login')

    preference, _ = UserPreference.objects.get_or_create(user=user)
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

    user.email = preference.pending_email
    user.save(update_fields=['email'])

    preference.is_email_verified = True
    preference.pending_email = ''
    preference.email_verification_token = ''
    preference.email_verification_sent_at = None
    preference.save()

    messages.success(request, "이메일 인증이 완료되었습니다!")
    return redirect('my_page' if request.user.is_authenticated else 'login')


def kakao_login_view(request):
    redirect_uri = request.build_absolute_uri(reverse('kakao_callback'))
    authorize_url = (
        "https://kauth.kakao.com/oauth/authorize"
        f"?client_id={settings.KAKAO_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=profile_nickname"
    )
    return redirect(authorize_url)


def kakao_callback_view(request):
    code = request.GET.get('code')
    if not code:
        messages.error(request, "카카오 로그인이 취소되었습니다.")
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
    messages.success(request, f"{user.first_name or user.username}님, 카카오 계정으로 로그인되었습니다!")
    return redirect('landing_page')


def google_login_view(request):
    redirect_uri = request.build_absolute_uri(reverse('google_callback'))
    authorize_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=openid%20email%20profile"
    )
    return redirect(authorize_url)


def google_callback_view(request):
    code = request.GET.get('code')
    if not code:
        messages.error(request, "구글 로그인이 취소되었습니다.")
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
    messages.success(request, f"{user.first_name or user.username}님, 구글 계정으로 로그인되었습니다!")
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
    messages.success(request, f"{user.first_name or user.username}님, 네이버 계정으로 로그인되었습니다!")
    return redirect('landing_page')


# ==========================================
# 마이페이지 - 블로거(Blogger) OAuth 연동
# 사이트 로그인용 구글 소셜 로그인과는 별개의 플로우: 이미 로그인된 회원이
# 자동 포스팅용으로 자신의 블로거 블로그를 연결(scope=Blogger API)하는 용도
# ==========================================

@login_required
def blogger_connect_view(request):
    redirect_uri = request.build_absolute_uri(reverse('blogger_callback'))
    authorize_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=https://www.googleapis.com/auth/blogger"
        "&access_type=offline"
        "&prompt=consent"  # 리프레시 토큰을 매번 새로 받기 위해 재동의 강제
    )
    return redirect(authorize_url)


@login_required
def blogger_callback_view(request):
    code = request.GET.get('code')
    if not code:
        messages.error(request, "블로거 연동이 취소되었습니다.")
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

