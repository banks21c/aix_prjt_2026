import json

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.db.models import Count, Max
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import chatbot_client
from .forms import SignUpForm
from .models import StockItem, StockPrediction, AnalyzedArticle, UserSubscription, SocialAccount, MarketIndex, RankedMover

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

    answer = chatbot_client.ask(question, history)
    return JsonResponse({'answer': answer})


# ==========================================
# 회원가입 / 로그인 / 소셜 로그인(카카오·구글)
# ==========================================

def _create_subscription_if_missing(user):
    UserSubscription.objects.get_or_create(user=user)


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
            email=form.cleaned_data['email'],
            password=form.cleaned_data['password1'],
        )
        _create_subscription_if_missing(user)
        login(request, user)
        messages.success(request, "회원가입이 완료되었습니다. NextFinUp에 오신 것을 환영합니다!")
        return redirect('landing_page')

    return render(request, 'articles/signup.html', {'form': form, 'site_title': 'NextFinUp - 회원가입'})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('landing_page')

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        messages.success(request, f"{form.get_user().username}님, 환영합니다!")
        return redirect('landing_page')

    return render(request, 'articles/login.html', {'form': form, 'site_title': 'NextFinUp - 로그인'})


def logout_view(request):
    logout(request)
    return redirect('landing_page')


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
    messages.success(request, f"{user.first_name or user.username}님, 구글 계정으로 로그인되었습니다!")
    return redirect('landing_page')

