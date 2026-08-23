import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .. import kis_client
from ..exim_client import FX_CONVERTER_ITEMS
from ..forms import NewsletterForm, SubscriptionOrderForm
from ..models import (
    AnalyzedArticle, ConsultRequest, ExchangeRateSnapshot, Faq, GlobalMarketQuote, MarketIndex, NewsletterIssue,
    NewsletterSubscriber, RankedMover, StockDisclosure, StockItem, StockPrediction, StockRealtimePrice,
    SubscriptionOrder, UserSubscription, Watchlist,
)
from ..utils import format_trading_value, format_volume, get_client_ip

logger = logging.getLogger(__name__)


def landing_page_view(request):
    # 히어로 문구가 "전종목의 다음날 종가를 예측"이라고 명시하므로, 이 옆의 카운터도 실제 AI
    # 예측 대상 범위(is_active 전체)와 일치해야 한다. collect_stock_data/run_stock_prediction이
    # 매일 새벽 --all(is_active 전체)로 돌아 최신 유지된다(deploy/crontab 02:00/04:30 KST).
    active_stock_count = StockItem.objects.filter(is_active=True).count()
    # collect_kis_news(원문 링크 없는 KIS 시황_공시 API)로 들어온 기사는 본문이 항상 비어있어
    # "본문없음"만 반복 노출된다 — 본문이 실제로 있는 기사만 골라 보여준다.
    latest_articles = (
        AnalyzedArticle.objects.select_related('stock')
        .exclude(original_content='')
        .order_by('-scraped_at')[:6]
    )

    context = {
        'site_title': 'NextFinUp - AI 차세대 자산 분석 포털',
        'active_stock_count': active_stock_count,
        'latest_articles': latest_articles,
    }
    return render(request, 'articles/index.html', context)


@require_POST
def newsletter_subscribe_view(request):
    form = NewsletterForm(request.POST)
    if form.is_valid():
        email = form.cleaned_data['email']
        _, created = NewsletterSubscriber.objects.get_or_create(email=email)
        if created:
            messages.success(request, f"{email} 구독 신청이 완료되었습니다. 감사합니다!")
        else:
            messages.info(request, "이미 구독 중인 이메일입니다.")
    else:
        messages.error(request, "올바른 이메일 주소를 입력해주세요.")
    return redirect(f"{reverse('landing_page')}#newsletter")


def newsletter_sample_view(request):
    """랜딩 페이지 뉴스레터 구독 폼 옆 '샘플 보기' 링크가 여는, 실제 발송 이력 중 가장 최근
    것을 그대로 보여주는 읽기 전용 페이지. 가짜 샘플을 따로 만들어 유지하지 않고, 실제
    send_newsletter가 발송한 NewsletterIssue.body(이메일에 실제로 나간 HTML)를 재사용한다."""
    issue = NewsletterIssue.objects.filter(status='SENT').order_by('-sent_at').first()
    context = {
        'site_title': 'NextFinUp - 뉴스레터 샘플',
        'issue': issue,
    }
    return render(request, 'articles/newsletter_sample.html', context)


def newsletter_unsubscribe_view(request, token):
    try:
        subscriber_id = signing.loads(token, salt='newsletter-unsubscribe')
        subscriber = NewsletterSubscriber.objects.get(pk=subscriber_id)
    except (signing.BadSignature, NewsletterSubscriber.DoesNotExist):
        messages.error(request, "유효하지 않은 수신거부 링크입니다.")
        return redirect('landing_page')

    subscriber.is_active = False
    subscriber.save(update_fields=['is_active'])
    messages.success(request, f"{subscriber.email}의 뉴스레터 수신이 해지되었습니다.")
    return redirect('landing_page')


def privacy_policy_view(request):
    return render(request, 'articles/privacy_policy.html', {'site_title': 'NextFinUp - 개인정보처리방침'})


def terms_of_service_view(request):
    return render(request, 'articles/terms_of_service.html', {'site_title': 'NextFinUp - 이용약관'})


def email_collection_refusal_view(request):
    return render(request, 'articles/email_collection_refusal.html', {'site_title': 'NextFinUp - 전자우편 무단수집거부'})


def blog_connect_guide_view(request):
    return render(request, 'articles/blog_connect_guide.html', {'site_title': 'NextFinUp - 블로그 연결 가이드'})


def adsense_guide_view(request):
    return render(request, 'articles/adsense_guide.html', {'site_title': 'NextFinUp - 구글 애드센스 신청 가이드'})


def _content_calendar_view(request, build_sample, site_title, emoji, heading, intro):
    """마이페이지 "뉴스 구독"에서 캘린더 기반 카테고리(건강/의학, 음식/영양)를 고려 중인 회원에게
    "이런 식으로 발행됩니다"를 보여주는 공개 안내 페이지. 실제 364일 전체 스케줄(build_one_cycle)은
    노출하지 않는다 — 통째로 공개하면 그대로 퍼가서 베낄 수 있다는 우려가 있어(회원 지적사항),
    요일(카테고리)별 예시 1개씩(build_sample, 7건)만 보여준다. health_content_calendar_view/
    food_content_calendar_view가 이 헬퍼를 공유한다."""
    return render(request, 'articles/content_calendar.html', {
        'site_title': site_title,
        'emoji': emoji,
        'heading': heading,
        'intro': intro,
        'samples': build_sample(),
    })


def health_content_calendar_view(request):
    from articles.health_calendar import build_sample

    return _content_calendar_view(
        request, build_sample,
        site_title='NextFinUp - 건강정보 포스팅 예시', emoji='🩺', heading='건강정보 자동 발행 예시',
        intro=(
            '마이페이지에서 "건강/의학"을 구독하면, 요일마다 정해진 주제 카테고리 안에서 AI가 매일 '
            '오전·오후 두 편씩 자동으로 글을 작성해 연결된 블로그에 발행합니다. 아래는 요일별 카테고리와 '
            '실제 발행되는 글 제목의 예시입니다(전체 1년 스케줄은 공개하지 않습니다). 각 글 하단에는 '
            '"일반적인 정보 제공 목적이며 진단·치료를 대신하지 않는다"는 안내 문구가 고정으로 붙습니다.'
        ),
    )


def food_content_calendar_view(request):
    from articles.food_calendar import build_sample

    return _content_calendar_view(
        request, build_sample,
        site_title='NextFinUp - 음식/영양 포스팅 예시', emoji='🍚', heading='음식/영양 자동 발행 예시',
        intro=(
            '마이페이지에서 "음식/영양"을 구독하면, 요일마다 정해진 주제 카테고리 안에서 AI가 매일 '
            '오전·오후 두 편씩 자동으로 글을 작성해 연결된 블로그에 발행합니다. 아래는 요일별 카테고리와 '
            '실제 발행되는 글 제목의 예시입니다(전체 1년 스케줄은 공개하지 않습니다).'
        ),
    )


def faq_board_view(request):
    query = request.GET.get('q', '').strip()
    category = request.GET.get('category', 'all')
    valid_categories = [c[0] for c in Faq.CATEGORY_CHOICES]
    if category not in valid_categories:
        category = 'all'

    faqs = Faq.objects.filter(is_active=True)
    if category != 'all':
        faqs = faqs.filter(category=category)
    if query:
        faqs = faqs.filter(Q(question__icontains=query) | Q(answer__icontains=query))

    paginator = Paginator(faqs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    # 다른 게시판(news_board 등)과 동일한 10개 단위 페이지 블록 방식
    page_block_size = 10
    current_block = (page_obj.number - 1) // page_block_size
    page_block_start = current_block * page_block_size + 1
    page_block_end = min(page_block_start + page_block_size - 1, paginator.num_pages)
    page_range = range(page_block_start, page_block_end + 1)
    prev_block_page = page_block_start - 1 if page_block_start > 1 else None
    next_block_page = page_block_end + 1 if page_block_end < paginator.num_pages else None

    context = {
        'site_title': 'NextFinUp - 자주 묻는 질문',
        'page_obj': page_obj,
        'page_range': page_range,
        'prev_block_page': prev_block_page,
        'next_block_page': next_block_page,
        'query': query,
        'category': category,
        'categories': Faq.CATEGORY_CHOICES,
    }
    return render(request, 'articles/faq_board.html', context)


def expert_consult_view(request):
    return render(request, 'articles/expert_consult.html', {'site_title': 'NextFinUp - 전문가 상담'})


def expert_consult_apply_view(request):
    """expert_consult.html 중 상담 신청 폼만 떼어낸 단독 페이지. 프로필/12가지 약속 없이
    바로 신청서로 진입하고 싶은 링크(광고, 배너 등)를 위한 것 — 제출 로직은 동일한
    /api/consult/(consult_request_view)를 그대로 쓴다."""
    return render(request, 'articles/expert_consult_apply.html', {'site_title': 'NextFinUp - 상담 신청'})


def subscribe_view(request):
    """구독 소개/가격 페이지. 로그인 여부·구독 상태에 따라 버튼이 로그인/신청/이미 구독중으로 갈린다.
    아직 PG(결제대행사) 연동 전이라 실제 결제는 받지 않고, 신청 접수 → 관리자 승인 방식으로 운영한다
    (SubscriptionOrder/subscribe_apply_view 참고)."""
    subscription = None
    pending_order = None
    if request.user.is_authenticated:
        subscription = getattr(request.user, 'subscription', None)
        pending_order = SubscriptionOrder.objects.filter(user=request.user, status='PENDING').first()

    return render(request, 'articles/subscribe.html', {
        'site_title': 'NextFinUp - 구독',
        'subscription': subscription,
        'pending_order': pending_order,
    })


@login_required
def subscribe_apply_view(request):
    subscription, _ = UserSubscription.objects.get_or_create(user=request.user)
    if subscription.is_active_premium:
        messages.info(request, "이미 프리미엄 구독 중입니다.")
        return redirect('subscribe')

    pending_order = SubscriptionOrder.objects.filter(user=request.user, status='PENDING').first()
    if pending_order:
        messages.info(request, "이미 신청이 접수되어 검토 중입니다. 확인 후 프리미엄이 활성화됩니다.")
        return redirect('subscribe')

    if request.method == 'POST':
        form = SubscriptionOrderForm(request.POST)
        if form.is_valid():
            order = form.save(commit=False)
            order.user = request.user
            order.save()

            try:
                send_mail(
                    subject=f"[NextFinUp] 구독 신청 - {order.name}",
                    message=(
                        f"아이디: {request.user.username}\n"
                        f"이메일: {request.user.email or '-'}\n"
                        f"구독자명: {order.name}\n"
                        f"휴대전화번호: {order.phone}\n"
                        f"가입 경로: {order.get_referral_source_display()}\n"
                        f"구독 동기: {order.get_motivation_display()}\n"
                        f"결제 수단: {order.get_payment_method_display()}\n"
                        f"신청 일시: {order.created_at:%Y-%m-%d %H:%M}\n"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[settings.EMAIL_HOST_USER],
                    fail_silently=True,
                )
            except Exception:
                logger.exception("구독 신청 알림 메일 발송 실패 (신청 자체는 저장됨, order id=%s)", order.id)

            messages.success(request, "구독 신청이 접수되었습니다. 담당자 확인 후 프리미엄이 활성화됩니다.")
            return redirect('subscribe')
    else:
        form = SubscriptionOrderForm(initial={'name': request.user.first_name})

    return render(request, 'articles/subscribe_apply.html', {
        'site_title': 'NextFinUp - 구독 신청',
        'form': form,
    })


def insurance_compare_view(request):
    # 해외여행자보험 비교 데모(프로토타입) — 상품/가격은 전부 예시 데이터이며 실 서비스 아님
    return render(request, 'articles/insurance_compare.html', {'site_title': 'NextFinUp - 보험 비교(데모)'})


def tools_hub_view(request):
    # /asset-management/의 "종합" 탭과 같은 역할 — 유틸 메뉴를 눌렀을 때 처음 보이는 허브
    # 페이지. 각 유틸 도구는 asset-management의 am-tabs처럼 상단 탭(_tools_tabs.html)으로
    # 전환하며, 지금은 카드/탭 둘 다 글자수세기 하나뿐이지만 이후 유틸을 추가할 때 이 카드
    # 그리드에 한 장씩 늘리면 된다.
    return render(request, 'articles/tools_hub.html', {'site_title': 'NextFinUp - 유틸'})


def char_counter_view(request):
    # 사람인/알바몬 글자수 세기 도구와 동일한 컨셉의 유틸리티 페이지. 입력 텍스트가 서버로
    # 전송되지 않고 브라우저에서만 계산되도록 순수 클라이언트 사이드 JS로 구현한다(자기소개서 등
    # 민감한 텍스트를 붙여넣는 용도라 서버 전송/저장이 없어야 함).
    return render(request, 'articles/char_counter.html', {'site_title': 'NextFinUp - 글자수 세기'})


def currency_converter_view(request):
    # 여행/송금/직구 수요가 큰 11개국(exim_client.FX_CONVERTER_ITEMS) 환율 계산기. 매매기준율은
    # collect_exchange_rate_fixing(30분 주기)이 쌓아둔 ExchangeRateSnapshot 최신값을 그대로
    # 쓴다 — 은행 실시간 시세가 아니라 하루 단위로 고시되는 값이라 서버에서 통화별 최신 1건만
    # 뽑아 JS에 넘기고, 실제 금액 환산(입력→결과)은 글자수세기와 같은 이유로 클라이언트에서 한다.
    rates = []
    latest_date = None
    for code, label in FX_CONVERTER_ITEMS.items():
        snapshot = (
            ExchangeRateSnapshot.objects.filter(currency_code=code).order_by('-date').first()
        )
        if not snapshot:
            continue
        unit = 100 if '(100)' in code else 1
        rate_per_unit = float(snapshot.deal_bas_r) / unit
        rates.append({
            'code': code.split('(')[0],
            'name': label,
            'rate_per_unit': rate_per_unit,
        })
        if latest_date is None or snapshot.date > latest_date:
            latest_date = snapshot.date

    return render(request, 'articles/currency_converter.html', {
        'site_title': 'NextFinUp - 환율 계산기',
        'rates': rates,
        'rates_json': json.dumps(rates),
        'latest_date': latest_date,
    })


def severance_calculator_view(request):
    # 근로기준법 제34조 기준 퇴직금 계산기(1일 평균임금 × 30일 × 재직일수/365). 입력값(급여 등
    # 민감 정보)이 서버로 전송되지 않도록 다른 유틸과 마찬가지로 계산 자체는 클라이언트에서 하고,
    # 이 뷰는 빈 화면만 렌더링한다.
    return render(request, 'articles/severance_calculator.html', {'site_title': 'NextFinUp - 퇴직금 계산기'})


def unemployment_benefit_calculator_view(request):
    # 고용보험법 기준 구직급여(실업급여) 계산기(이직 전 1일 평균임금의 60% × 소정급여일수).
    # 상한액/하한액은 최저임금 등 정부 고시로 매년 바뀌는 값이라 템플릿에 하드코딩하지 않고,
    # 화면에서 기본값을 보여주되 사용자가 직접 수정할 수 있는 입력 필드로 둔다. 다른 유틸과
    # 마찬가지로 급여 정보가 서버로 전송되지 않도록 계산은 클라이언트에서 한다.
    return render(request, 'articles/unemployment_benefit_calculator.html', {
        'site_title': 'NextFinUp - 실업급여 계산기',
    })


def net_pay_calculator_view(request):
    # 연봉(세전) → 4대보험료 공제 후 실수령액 계산기. 소득세는 국세청 근로소득 간이세액표 자체를
    # (수만 행짜리 표라) 그대로 담는 대신, 그 표가 산정되는 원리인 근로소득공제→인적공제→
    # 종합소득세 누진세율표→근로소득세액공제 절차를 그대로 구현해 근사한다(자녀세액공제 등은
    # 미반영 — 화면에 안내 문구로 명시). 4대보험료율/세율표 상수는 매년 소폭 조정되는 고시값이라
    # 실업급여 계산기와 같은 방식으로 화면에서 기본값을 보여주되 수정 가능한 입력 필드로 둔다.
    return render(request, 'articles/net_pay_calculator.html', {
        'site_title': 'NextFinUp - 급여 실수령액 계산기',
    })


def annual_leave_calculator_view(request):
    # 근로기준법 제60조 기준 연차수당 계산기. 입사 1년 미만은 개근 월 1일씩(최대 11일), 1년
    # 이상은 15일 + 2년마다 1일 가산(최대 25일)으로 발생 연차일수를 자동 계산해 보여주되, 회사마다
    # 회계연도 기준 등 운영 방식이 달라 사용자가 값을 직접 수정할 수 있게 한다. 1일 통상임금은
    # 월 통상임금 ÷ 월 소정근로시간(기본 209시간) × 8시간으로 계산한다. 다른 유틸과 마찬가지로
    # 급여 정보가 서버로 전송되지 않도록 계산은 클라이언트에서 한다.
    return render(request, 'articles/annual_leave_calculator.html', {
        'site_title': 'NextFinUp - 연차수당 계산기',
    })


def json_formatter_view(request):
    # JSON 예쁘게 출력/압축/검증 도구. 개발용 텍스트(API 응답, 설정값 등)가 서버로 전송/저장되지
    # 않도록 다른 유틸과 마찬가지로 파싱·포맷팅 전부 브라우저(JS 내장 JSON.parse/stringify)에서만
    # 처리한다.
    return render(request, 'articles/json_formatter.html', {'site_title': 'NextFinUp - JSON 포맷터'})


def image_resizer_view(request):
    # 이미지 리사이즈 + 확장자 변환(PNG/JPEG/WEBP) 도구. 업로드한 이미지가 서버로 전송되지 않도록
    # <canvas>.toBlob으로 리사이즈/포맷 변환을 전부 브라우저에서 처리하고, 결과물도 브라우저가
    # 바로 다운로드시킨다(서버 저장 없음).
    return render(request, 'articles/image_resizer.html', {'site_title': 'NextFinUp - 이미지 리사이저'})


def header_fragment_view(request):
    """nginx가 alias로 직접 서빙하는 정적 페이지(/insurance-guide/ 등)가 fetch로 불러와
    최상단에 붙이는 공통 헤더 조각. _header.html 자체를 그대로 렌더링해 반환한다."""
    return render(request, 'articles/_header.html')


def cookie_banner_fragment_view(request):
    """header_fragment_view와 같은 패턴 — nginx가 직접 서빙하는 정적 페이지(예:
    /asset-management/)가 fetch로 불러와 쓰는 공통 쿠키 배너 조각. 정적 페이지마다 배너
    HTML/문구를 따로 박아두면 Django 쪽 _cookie_banner.html이 바뀔 때 정적 페이지가
    누락되어 서로 다른 버전이 보이는 문제가 생기므로, 이 엔드포인트로 단일 소스를 공유한다."""
    return render(request, 'articles/_cookie_banner.html')


def ticker_fragment_view(request):
    """header_fragment_view/cookie_banner_fragment_view와 같은 패턴 — nginx가 직접 서빙하는
    정적 페이지(/asset-management/ 등)가 fetch로 불러와 쓰는 공통 실시간 시세 티커 조각.
    _header.html도 같은 _ticker.html을 include해서 쓰므로 단일 소스다."""
    return render(request, 'articles/_ticker.html')


def ticker_data_view(request):
    """_header.html의 첫 번째(시세) 티커가 폴링하는 JSON API. 국내지수(MarketIndex) +
    해외지수/국제 시장 환율/금리(GlobalMarketQuote, 5분 주기 collect_global_market_data)를
    내려준다. 유가/금시세/원자재는 KIS API에 데이터 자체가 없어 제외했다.
    코스피/코스닥 항목에는 flows(개인/외국인/기관 순매수 금액, 억원)를 함께 실어, 헤더의
    지수 클릭 팝업이 그 지수만의 수급 주체별 순매수를 바로 보여줄 수 있게 한다."""
    items = []

    for market_type, label in (('KOSPI', '코스피'), ('KOSDAQ', '코스닥')):
        idx = MarketIndex.objects.filter(market_type=market_type).order_by('-date').first()
        if idx:
            item = {
                'name': label,
                'price': float(idx.close_price),
                'change_pct': idx.change_pct if idx.change_pct is not None else 0.0,
                'change': float(idx.change) if idx.change is not None else 0.0,
            }
            if idx.retail_net_amount is not None:
                item['flows'] = {
                    'retail': float(idx.retail_net_amount),
                    'foreign': float(idx.foreign_net_amount) if idx.foreign_net_amount is not None else 0.0,
                    'institution': float(idx.institution_net_amount) if idx.institution_net_amount is not None else 0.0,
                }
            items.append(item)

    for quote in GlobalMarketQuote.objects.order_by('category', 'order'):
        items.append({
            'name': quote.name,
            'price': float(quote.price),
            'change_pct': quote.change_pct,
        })

    return JsonResponse({'items': items})


def ticker_stocks_view(request):
    """_header.html의 두 번째(개별 종목) 티커가 폴링하는 JSON API. 등락률 상위/하위 10개만
    보여주던 이전 버전(RankedMover 기반)은 "특징주만 나오고 전체 종목이 안 나온다"는 신고로,
    실시간 시세를 수집하는 전체 범위(StockRealtimePrice, 코스피200/코스닥150 350종목,
    collect_stock_realtime_price 5분 주기)로 교체했다. StockRealtimePrice.stock은 StockItem에
    대한 실제 FK라 RankedMover와 달리 ETN 등 마스터에 없는 상품이 섞일 수 없으므로
    has_detail은 항상 True.
    ticker(종목코드)는 클릭 시 뜨는 레이어 팝업의 "종목 상세보기" 링크(stock_detail_view)에 쓰인다."""
    prices = list(
        StockRealtimePrice.objects.select_related('stock').order_by('stock__ticker')
    )
    items = [
        {
            'name': p.stock.name, 'price': float(p.close_price), 'change_pct': p.change_pct,
            'ticker': p.stock.ticker, 'change_amount': float(p.change),
            'has_detail': True,
        }
        for p in prices
    ]
    return JsonResponse({'items': items})


@csrf_exempt
@require_POST
def consult_request_view(request):
    """IRP/ISA/연금저축(nextfinup에서 분리된 정적 페이지) 상담 신청 폼을 저장한다.
    호출부가 Django가 렌더링하지 않는 별도 정적 HTML이라 CSRF 토큰을 발급할 수 없어 csrf_exempt로
    열어둔 대신, 봇 스팸 방지용 허니팟 필드(website)로 최소한의 필터링만 한다."""
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)
    else:
        data = request.POST

    if (data.get('website') or '').strip():
        # 허니팟에 값이 채워졌으면 봇으로 간주 — 저장하지 않고 정상 응답만 돌려준다
        return JsonResponse({'ok': True})

    product = (data.get('product') or '').strip().upper()
    name = (data.get('name') or '').strip()
    phone = (data.get('phone') or '').strip()

    if product not in dict(ConsultRequest.PRODUCT_CHOICES):
        return JsonResponse({'ok': False, 'error': 'invalid_product'}, status=400)
    if not name or not phone:
        return JsonResponse({'ok': False, 'error': 'name_phone_required'}, status=400)

    consult = ConsultRequest.objects.create(
        product=product,
        name=name[:50],
        phone=phone[:20],
        interest=(data.get('interest') or '').strip()[:100],
        goal=(data.get('goal') or '').strip()[:200],
        message=(data.get('message') or '').strip(),
        source_ip=get_client_ip(request),
    )

    try:
        send_mail(
            subject=f"[NextFinUp] {consult.get_product_display()} 상담 신청 - {consult.name}",
            message=(
                f"상품: {consult.get_product_display()}\n"
                f"이름: {consult.name}\n"
                f"연락처: {consult.phone}\n"
                f"관심 기관/상품: {consult.interest or '-'}\n"
                f"목표: {consult.goal or '-'}\n"
                f"문의사항: {consult.message or '-'}\n"
                f"접수 IP: {consult.source_ip or '-'}\n"
                f"접수 일시: {consult.created_at:%Y-%m-%d %H:%M}\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.EMAIL_HOST_USER],
            fail_silently=True,
        )
    except Exception:
        logger.exception("상담 신청 알림 메일 발송 실패 (신청 자체는 저장됨, consult id=%s)", consult.id)

    return JsonResponse({'ok': True})


def _build_index_chart(market_type, days=90):
    """코스피/코스닥 지수 일봉 차트용 데이터: 최근 N일 OHLC 시계열 + 최신 지수/등락/등락% 값"""
    rows = list(MarketIndex.objects.filter(market_type=market_type).order_by('-date')[:days])
    rows.reverse()  # 차트는 과거 -> 현재 순으로 그려야 하므로 날짜 오름차순으로 뒤집음
    latest = rows[-1] if rows else None
    if latest and latest.change_pct is not None:
        # change_pct는 KIS 원본이 이미 부호 포함 — 대시보드가 화살표(▲/▼)로 부호를 따로
        # 표시하므로, 절대값을 미리 계산해 템플릿에서 이중 부호(▼-0.44%) 없이 쓸 수 있게 한다.
        latest.change_pct_abs = abs(latest.change_pct)
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
    # ---- 0행: 관심종목 (로그인 회원 전용). 코스피200/코스닥150 밖 종목도 저장할 수 있어
    # 실시간 캐시(StockRealtimePrice)가 없을 수 있다 — 그런 경우만 온디맨드로 조회한다. 회원별
    # 목록이라 보통 몇 개 안 되므로(수십 개 규모의 매수/매도 신호 표와 달리) 스레드풀 없이
    # 순차 조회로 충분하다.
    watchlist_items = []
    if request.user.is_authenticated:
        watchlist_items = list(
            Watchlist.objects.filter(user=request.user).select_related('stock').order_by('-created_at')[:10]
        )
        for w in watchlist_items:
            cached = StockRealtimePrice.objects.filter(stock=w.stock).first()
            if cached:
                w.price, w.change, w.change_pct = cached.close_price, cached.change, cached.change_pct
            else:
                try:
                    fetched = kis_client.get_stock_close_price(w.stock.ticker)
                    w.price, w.change, w.change_pct = fetched['close'], fetched['change'], fetched['change_pct']
                except Exception:
                    logger.exception("관심종목 온디맨드 시세 조회 실패: %s", w.stock.ticker)
                    w.price, w.change, w.change_pct = None, None, None
            w.change_pct_abs = abs(w.change_pct) if w.change_pct is not None else None

    # ---- 1행: 코스피/코스닥 지수 차트(지수/등락/등락%) ----
    # 3년치를 한 번에 내려보내, 클라이언트에서 3개월/1년/3년 버튼을 누르면 다시 조회하지 않고
    # 이미 받은 배열을 기간만큼 잘라서 그린다. 당일(1일) 분봉은 별도 온디맨드 API로 받는다.
    kospi_index = _build_index_chart('KOSPI', days=1095)
    kosdaq_index = _build_index_chart('KOSDAQ', days=1095)

    # ---- 2행: 주요뉴스 (전체 종목 통틀어 가장 최근 수집된 기사) ----
    # collect_kis_news(원문 링크 없는 KIS 시황_공시 API)로 들어온 기사는 본문이 항상 비어있어
    # "본문없음"만 반복 노출된다 — 본문이 실제로 있는 기사만 골라 보여준다.
    major_news = (
        AnalyzedArticle.objects.select_related('stock', 'matched_keyword')
        .exclude(original_content='')
        .order_by('-scraped_at')[:7]
    )

    # ---- 2행: 등락률 상위 (한국투자증권 등락률 순위 API 기준 상승률 상위 5개 + 하락률 상위 5개) ----
    top_gainers = list(RankedMover.objects.filter(rank_type='GAINER').order_by('rank'))
    top_losers = list(RankedMover.objects.filter(rank_type='LOSER').order_by('rank'))
    featured_stocks = top_gainers + top_losers
    # KIS 등락률 순위에는 ETN(레버리지/인버스 원자재 등, "Q" 코드) 등 StockItem 마스터에 없는
    # 상품도 섞여 나온다 — insert_stock_master가 일반 상장 종목만 채워서다. 그런 티커로
    # {% url 'stock_detail' %}를 걸면 404가 나므로(신고: "치명적인 에러"), 마스터에 없으면
    # 우리 상세 페이지 대신 네이버 증권으로 보낸다.
    existing_tickers = set(
        StockItem.objects.filter(ticker__in=[m.ticker for m in featured_stocks]).values_list('ticker', flat=True)
    )
    for mover in featured_stocks:
        # change_amount/change_pct는 이미 부호 포함(KIS prdy_vrss/prdy_ctrt) — 화살표로 부호를
        # 따로 표시하므로 템플릿에서 abs() 없이 바로 쓸 수 있게 여기서 절대값을 미리 계산해둔다
        # (그대로 쓰면 하락 종목에서 "▼-21.91%" 처럼 이중 부호가 남).
        mover.change_amount_abs = abs(mover.change_amount)
        mover.change_pct_abs = abs(mover.change_pct)
        if mover.ticker in existing_tickers:
            mover.detail_url = reverse('stock_detail', args=[mover.ticker])
            mover.detail_external = False
        else:
            # ETN 코드는 KIS에서 "Q" 접두사가 붙어 오지만(예: Q760027), 네이버 증권은 접두사 없는
            # 코드(760027)라야 종목 상세로 가고 붙이면 증권 메인으로 튕긴다 — 실측 확인.
            naver_code = mover.ticker[1:] if mover.ticker.startswith('Q') else mover.ticker
            mover.detail_url = f"https://stock.naver.com/domestic/stock/{naver_code}/price"
            mover.detail_external = True

    # ---- 2행: 거래대금/거래량 상위 5종목 (실시간 시세 캐시 StockRealtimePrice 기준 — 등락률
    # 상위(RankedMover)와 달리 코스피200/코스닥150 전체(350종목)가 후보군이라 시장별(코스피/
    # 코스닥) 상위 5개를 그 안에서 직접 뽑는다. StockRealtimePrice는 정규장 중에만 5분마다
    # 갱신되고 마감 후엔 마감 직전 캐시가 그대로 남으므로, 이 랭킹도 마감 후엔 마감 시점 기준이
    # 된다. 거래대금 필드가 따로 없어 종가*거래량으로 근사한다(build_mentioned_stocks_table과
    # 동일한 방식).
    realtime_rows = list(
        StockRealtimePrice.objects.filter(stock__is_active=True, volume__gt=0)
        .select_related('stock')
    )
    value_leaders = {'KOSPI': [], 'KOSDAQ': []}
    volume_leaders = {'KOSPI': [], 'KOSDAQ': []}
    for market in ('KOSPI', 'KOSDAQ'):
        rows = [r for r in realtime_rows if r.stock.market_type == market]
        for r in rows:
            r.trading_value = r.close_price * r.volume
            r.trading_value_label = format_trading_value(r.trading_value)
            r.volume_label = format_volume(r.volume)
            r.change_abs = abs(r.change)
            r.change_pct_abs = abs(r.change_pct)
        value_leaders[market] = sorted(rows, key=lambda r: r.trading_value, reverse=True)[:5]
        # 거래량이 동률인 경우(드묾) 시가총액이 큰 종목을 우선한다("시가총액 우선순위").
        volume_leaders[market] = sorted(
            rows, key=lambda r: (r.volume, r.stock.market_cap or 0), reverse=True
        )[:5]

    # ---- 2행: 진짜 특징종목 (등락률만 보는 위 순위와 달리, 오늘 실제 뉴스·공시가 붙어 "왜
    # 움직였는지 설명되는" 종목만 추린다). 등락률 상위(RankedMover)는 상승률/하락률 5개뿐이라
    # 후보가 너무 좁아 뉴스/공시와 거의 안 겹치므로, 후보군 자체를 실시간 시세 캐시(코스피200/
    # 코스닥150, 350종목) 전체로 넓히고 거기서 오늘 뉴스나 DART 공시가 매칭된 종목만 남긴 뒤
    # 등락률 절댓값 상위를 뽑는다 — "많이 움직였다"가 아니라 "많이 움직였고 이유도 있다"가 기준.
    today = timezone.localdate()
    news_stock_ids = set(
        AnalyzedArticle.objects.filter(scraped_at__date=today).exclude(stock=None)
        .values_list('stock_id', flat=True)
    )
    disclosure_stock_ids = set(
        StockDisclosure.objects.filter(rcept_dt=today).exclude(stock=None)
        .values_list('stock_id', flat=True)
    )
    explainable_ids = news_stock_ids | disclosure_stock_ids

    REAL_FEATURED_MIN_CHANGE_PCT = 2.0
    real_featured_stocks = []
    if explainable_ids:
        candidates = list(
            StockRealtimePrice.objects.filter(stock_id__in=explainable_ids)
            .select_related('stock')
        )
        candidates = [c for c in candidates if abs(c.change_pct) >= REAL_FEATURED_MIN_CHANGE_PCT]
        candidates.sort(key=lambda c: abs(c.change_pct), reverse=True)
        candidates = candidates[:10]

        for c in candidates:
            c.change_abs = abs(c.change)
            c.change_pct_abs = abs(c.change_pct)
            reasons = []
            reason_link, reason_link_external, reason_text = None, False, None

            if c.stock_id in news_stock_ids:
                reasons.append('뉴스')
                latest_news = (
                    AnalyzedArticle.objects.filter(stock_id=c.stock_id, scraped_at__date=today)
                    .order_by('-scraped_at').first()
                )
                if latest_news:
                    reason_link = reverse('news_detail', args=[latest_news.pk])
                    reason_link_external = False
                    reason_text = latest_news.title
            if c.stock_id in disclosure_stock_ids:
                reasons.append('공시')
                if reason_link is None:  # 뉴스 링크가 없을 때만 공시(외부 DART) 링크로 대체
                    latest_disc = (
                        StockDisclosure.objects.filter(stock_id=c.stock_id, rcept_dt=today)
                        .order_by('-rcept_no').first()
                    )
                    if latest_disc:
                        reason_link = latest_disc.viewer_url
                        reason_link_external = True
                        reason_text = latest_disc.report_nm

            c.reason_label = '+'.join(reasons)
            c.reason_link = reason_link
            c.reason_link_external = reason_link_external
            c.reason_text = reason_text
            real_featured_stocks.append(c)

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

    # ---- 4행: 매수/매도 신호 종목 표 (홀드아웃이 종목 자체 기준선을 넘겨 실제로 신호를 낸
    # 종목만 — run_stock_prediction이 기준선 미달 종목은 이미 HOLD로 걸러둔 상태) ----
    buy_signals, sell_signals = [], []
    if latest_pred_date:
        buy_signals = list(
            StockPrediction.objects
            .filter(date=latest_pred_date, stock__is_active=True, trading_signal='BUY')
            .select_related('stock').order_by('-up_probability')
        )
        sell_signals = list(
            StockPrediction.objects
            .filter(date=latest_pred_date, stock__is_active=True, trading_signal='SELL')
            .select_related('stock').order_by('-down_probability')
        )
        # 사용자 요구: 여기 나오는 가격은 무조건 "오늘" 가격이어야 한다 — 어제 종가를
        # "며칠자 종가"라고 라벨만 붙여 보여주는 건 의미가 없다는 피드백. StockDailyPrice는
        # collect_stock_data가 KST 02:00에 한 번만 돌아 오늘 마감분이 내일에야 들어오고,
        # StockRealtimePrice 캐시도 코스피200/코스닥150 종목만 있고 정규장 마감 후엔 마감 직전
        # 마지막 틱(정산 전 값)에 멈춰 있어 실제 종가와 어긋날 수 있다 — 그래서 이 표는 DB
        # 캐시를 아예 쓰지 않고, 신호가 난 종목 전부(대개 수십 개) get_stock_close_price로
        # 온디맨드 조회한다(정규장 중엔 실시간가, 마감 후엔 정산된 종가를 알아서 골라 줌).
        # 종목 수가 많아 순차 호출하면 페이지 로딩이 느려지므로 스레드풀로 병렬 조회한다.
        signal_predictions = buy_signals + sell_signals
        realtime_map = {}

        def _fetch_today_price(pred, attempts=3):
            # KIS 일봉 조회(inquire-daily-itemchartprice)가 동시 요청 부하에 약해서, 병렬로
            # 8개씩 그냥 쏘면 일부가 500 Internal Server Error로 실패했다(실측: 53건 중 12건
            # 실패). 동시 실행 수를 4개로 낮추고 실패 시 살짝 쉬었다 재시도하니 53/53 성공.
            for attempt in range(attempts):
                try:
                    return pred.stock_id, kis_client.get_stock_close_price(pred.stock.ticker)
                except Exception:
                    if attempt == attempts - 1:
                        logger.exception("대시보드 매수/매도 신호 종가 온디맨드 조회 실패: %s", pred.stock.ticker)
                    else:
                        time.sleep(0.3)
            return pred.stock_id, None

        if signal_predictions:
            # 장중이든 마감 후든 "그 시점의 실시간 가격"이라는 의미로 항상 같은 라벨을 쓴다
            # ("오늘 종가"라는 표현은 낮에 조회해도 마치 장마감가처럼 보여 혼동을 줌).
            price_label = '실시간'
            with ThreadPoolExecutor(max_workers=4) as executor:
                for stock_id, fetched in executor.map(_fetch_today_price, signal_predictions):
                    if fetched:
                        realtime_map[stock_id] = {
                            'close': fetched['close'], 'change': fetched['change'],
                            'change_pct': fetched['change_pct'], 'label': price_label,
                        }

        for p in signal_predictions:
            realtime = realtime_map.get(p.stock_id)
            if realtime:
                p.price = realtime['close']
                p.change = realtime['change']
                p.change_pct = realtime['change_pct']
                p.price_label = realtime['label']
            else:
                # 온디맨드 조회 자체가 실패한 극히 드문 경우에만 쓰는 최후의 fallback.
                p.price = None
                p.change = None
                p.change_pct = None
                p.price_label = '조회 실패'

            # 화살표(▲/▼)로 부호를 따로 표시하므로, 절대값을 미리 계산해 템플릿에서 이중
            # 부호(▼-500) 없이 쓸 수 있게 한다 — 대시보드 다른 표들과 같은 관례.
            p.change_abs = abs(p.change) if p.change is not None else None
            p.change_pct_abs = abs(p.change_pct) if p.change_pct is not None else None
            p.up_probability_pct = round(p.up_probability * 100, 1) if p.up_probability is not None else None
            p.down_probability_pct = round(p.down_probability * 100, 1) if p.down_probability is not None else None
            p.holdout_accuracy_pct = round(p.holdout_accuracy * 100, 1) if p.holdout_accuracy is not None else None
            # ETN 등 마스터 밖 코드는 여기 안 걸리지만(예측 자체가 StockItem 있는 종목만 도니까),
            # 혹시 모를 "Q" 접두사 코드에 대비해 특징종목과 동일한 방어 로직을 둔다.
            naver_code = p.stock.ticker[1:] if p.stock.ticker.startswith('Q') else p.stock.ticker
            p.naver_url = f"https://stock.naver.com/domestic/stock/{naver_code}/price"

    context = {
        'site_title': 'NextFinUp - AI 차세대 자산 분석 포털',
        'watchlist_items': watchlist_items,
        'kospi_index': kospi_index,
        'kosdaq_index': kosdaq_index,
        'major_news': major_news,
        'top_gainers': top_gainers,
        'top_losers': top_losers,
        'value_leaders': value_leaders,
        'volume_leaders': volume_leaders,
        'real_featured_stocks': real_featured_stocks,
        'kospi_ohlc': kospi_index['ohlc'],
        'kosdaq_ohlc': kosdaq_index['ohlc'],
        'mover_labels': mover_labels,
        'mover_values': mover_values,
        'signal_labels': ['매수', '매도', '관망'],
        'signal_values': signal_values,
        'buy_signals': buy_signals,
        'sell_signals': sell_signals,
    }
    return render(request, 'articles/dashboard.html', context)
