import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.mail import send_mail
from django.db.models import Count, Max
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .. import kis_client
from ..forms import NewsletterForm, SubscriptionOrderForm
from ..models import (
    AnalyzedArticle, ConsultRequest, GlobalMarketQuote, MarketIndex, NewsletterSubscriber, RankedMover,
    StockItem, StockPrediction, StockRealtimePrice, SubscriptionOrder, UserSubscription,
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


def header_fragment_view(request):
    """nginx가 alias로 직접 서빙하는 정적 페이지(/insurance-guide/ 등)가 fetch로 불러와
    최상단에 붙이는 공통 헤더 조각. _header.html 자체를 그대로 렌더링해 반환한다."""
    return render(request, 'articles/_header.html')


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
    # ---- 1행: 코스피/코스닥 지수 차트(지수/등락/등락%) ----
    # 3년치를 한 번에 내려보내, 클라이언트에서 3개월/1년/3년 버튼을 누르면 다시 조회하지 않고
    # 이미 받은 배열을 기간만큼 잘라서 그린다. 당일(1일) 분봉은 별도 온디맨드 API로 받는다.
    kospi_index = _build_index_chart('KOSPI', days=1095)
    kosdaq_index = _build_index_chart('KOSDAQ', days=1095)

    # ---- 2행: 주요뉴스 (전체 종목 통틀어 가장 최근 수집된 기사) ----
    major_news = AnalyzedArticle.objects.select_related('stock', 'matched_keyword').order_by('-scraped_at')[:7]

    # ---- 2행: 특징종목 (한국투자증권 등락률 순위 API 기준 상승률 상위 5개 + 하락률 상위 5개) ----
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
            mover.detail_url = f"https://finance.naver.com/item/main.naver?code={naver_code}"
            mover.detail_external = True

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
            p.naver_url = f"https://finance.naver.com/item/main.naver?code={naver_code}"

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
        'buy_signals': buy_signals,
        'sell_signals': sell_signals,
    }
    return render(request, 'articles/dashboard.html', context)
