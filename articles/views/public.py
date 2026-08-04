import json
import logging

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.core.mail import send_mail
from django.db.models import Count, Max
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ..forms import NewsletterForm
from ..models import (
    AnalyzedArticle, ConsultRequest, GlobalMarketQuote, MarketIndex, NewsletterSubscriber, RankedMover,
    StockItem, StockPrediction,
)
from ..utils import get_client_ip

logger = logging.getLogger(__name__)


def landing_page_view(request):
    # 히어로 문구가 "전종목의 다음날 종가를 예측"이라고 명시하므로, 이 옆의 카운터도 실제 AI
    # 예측 대상 범위(is_active 전체)와 일치해야 한다. collect_stock_data/run_stock_prediction이
    # 매일 새벽 --all(is_active 전체)로 돌아 최신 유지된다(deploy/crontab 02:00/04:30 KST).
    active_stock_count = StockItem.objects.filter(is_active=True).count()
    latest_articles = AnalyzedArticle.objects.select_related('stock').order_by('-scraped_at')[:6]

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


def expert_consult_view(request):
    return render(request, 'articles/expert_consult.html', {'site_title': 'NextFinUp - 전문가 상담'})


def insurance_compare_view(request):
    # 해외여행자보험 비교 데모(프로토타입) — 상품/가격은 전부 예시 데이터이며 실 서비스 아님
    return render(request, 'articles/insurance_compare.html', {'site_title': 'NextFinUp - 보험 비교(데모)'})


def header_fragment_view(request):
    """nginx가 alias로 직접 서빙하는 정적 페이지(/insurance-guide/ 등)가 fetch로 불러와
    최상단에 붙이는 공통 헤더 조각. _header.html 자체를 그대로 렌더링해 반환한다."""
    return render(request, 'articles/_header.html')


def ticker_data_view(request):
    """_header.html의 첫 번째(시세) 티커가 폴링하는 JSON API. 국내지수(MarketIndex) +
    해외지수/국제 시장 환율/금리(GlobalMarketQuote, 5분 주기 collect_global_market_data)를
    내려준다. 유가/금시세/원자재는 KIS API에 데이터 자체가 없어 제외했다."""
    items = []

    for market_type, label in (('KOSPI', '코스피'), ('KOSDAQ', '코스닥')):
        idx = MarketIndex.objects.filter(market_type=market_type).order_by('-date').first()
        if idx:
            items.append({
                'name': label,
                'price': float(idx.close_price),
                'change_pct': idx.change_pct if idx.change_pct is not None else 0.0,
            })

    for quote in GlobalMarketQuote.objects.order_by('category', 'order'):
        items.append({
            'name': quote.name,
            'price': float(quote.price),
            'change_pct': quote.change_pct,
        })

    return JsonResponse({'items': items})


def ticker_stocks_view(request):
    """_header.html의 두 번째(개별 종목) 티커가 폴링하는 JSON API. 등락률 상위 5/하위 5
    (RankedMover, 5분 주기 collect_fluctuation_ranking)를 내려준다. ticker(종목코드)는 클릭 시
    뜨는 레이어 팝업의 "종목 상세보기" 링크(stock_detail_view)에 쓰인다."""
    items = [
        {
            'name': mover.name, 'price': float(mover.price), 'change_pct': mover.change_pct,
            'ticker': mover.ticker, 'change_amount': float(mover.change_amount),
        }
        for mover in RankedMover.objects.order_by('rank_type', 'rank')
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
    # 3년치를 한 번에 내려보내, 클라이언트에서 3개월/1년/3년 버튼을 누르면 다시 조회하지 않고
    # 이미 받은 배열을 기간만큼 잘라서 그린다. 당일(1일) 분봉은 별도 온디맨드 API로 받는다.
    kospi_index = _build_index_chart('KOSPI', days=1095)
    kosdaq_index = _build_index_chart('KOSDAQ', days=1095)

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
