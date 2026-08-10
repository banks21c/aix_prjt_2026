from django.shortcuts import render

from ..models import MarketIndex, PredictionAccuracySnapshot, StockDailyPrice, StockPrediction


def _resolve_recent_signals():
    """실제로 발동됐던 매수/매도 시그널이 다음 거래일에 실제로 맞았는지 확인한다. 표본이
    작아(전체 이력 통틀어 수백 건 수준 — compute_prediction_accuracy 커맨드가 캐싱하는
    신뢰도 구간별 표(수만 건)와 달리) 페이지 요청마다 라이브로 계산해도 가볍다(실측 0.5초
    미만, 2026-08-09 기준)."""
    preds = list(
        StockPrediction.objects.exclude(trading_signal='HOLD')
        .filter(up_probability__isnull=False)
        .select_related('stock')
        .order_by('-date')
    )
    if not preds:
        return []

    # 다음 거래일 계산은 종목별 가격 이력 전체를 훑지 않고 MarketIndex의 거래일 달력만 쓴다
    # (compute_prediction_accuracy와 동일한 이유/방식).
    trading_dates = sorted(MarketIndex.objects.filter(market_type='KOSPI').values_list('date', flat=True))
    date_to_next = {d: trading_dates[i + 1] for i, d in enumerate(trading_dates[:-1])}

    needed_dates = set()
    for p in preds:
        needed_dates.add(p.date)
        next_date = date_to_next.get(p.date)
        if next_date:
            needed_dates.add(next_date)

    stock_ids = {p.stock_id for p in preds}
    price_rows = (
        StockDailyPrice.objects.filter(stock_id__in=stock_ids, date__in=needed_dates)
        .values('stock_id', 'date', 'close_price')
    )
    price_map = {(r['stock_id'], r['date']): float(r['close_price']) for r in price_rows}

    resolved = []
    for p in preds:
        next_date = date_to_next.get(p.date)
        if not next_date:
            continue  # 가장 최근 시그널은 다음 거래일이 아직 안 지나 결과 미확정 — 표에서 제외
        close_on_date = price_map.get((p.stock_id, p.date))
        next_close = price_map.get((p.stock_id, next_date))
        if close_on_date is None or next_close is None:
            continue
        actual_up = next_close > close_on_date
        predicted_up = p.trading_signal == 'BUY'
        resolved.append({
            'date': p.date,
            'stock': p.stock,
            'trading_signal': p.trading_signal,
            'up_probability_pct': round(p.up_probability * 100, 1),
            'actual_return_pct': round((next_close - close_on_date) / close_on_date * 100, 2),
            'correct': actual_up == predicted_up,
        })
    return resolved


def build_ai_performance_context():
    """AI 예측 성과 데이터를 두 축으로 구성해 context dict로 반환한다. 공개 트랙레코드 페이지
    (ai_performance_view)와 admin 내 운영 도구 화면(admin_tools.ai_performance_admin_view)이
    이 계산 로직을 그대로 공유한다 — 화면(템플릿/레이아웃)만 다르고 데이터는 같아야 하므로.
    (1) 신뢰도(확신도) 구간별 실현 정확도 — PredictionAccuracySnapshot 캐시(매일 배치 갱신)를
        그대로 읽는다. SIGNAL_PROB_THRESHOLD(run_stock_prediction.py) 임계값 선택 근거와
        동일한 데이터.
    (2) 실제로 발동됐던 매수/매도 시그널 각각의 결과 — 라이브 계산.
    """
    snapshot = PredictionAccuracySnapshot.objects.order_by('-computed_at').first()

    # buckets는 JSONField(accuracy가 0.0~1.0 소수)라, 앱 전반의 관례(표시는 퍼센트)에 맞춰
    # 템플릿에 넘기기 전에 변환해둔다. 표본 40건 미만은 신뢰하기 어렵다는 기준(원래 임계값
    # 재측정 때와 동일)도 함께 표시할 수 있도록 플래그를 붙인다.
    buckets_display = []
    if snapshot:
        for b in snapshot.buckets:
            buckets_display.append({
                'threshold': b['threshold'],
                'n': b['n'],
                'accuracy_pct': round(b['accuracy'] * 100, 1) if b['accuracy'] is not None else None,
                'low_sample': b['n'] < 40,
            })

    signals = _resolve_recent_signals()
    buy_signals = [s for s in signals if s['trading_signal'] == 'BUY']
    sell_signals = [s for s in signals if s['trading_signal'] == 'SELL']

    def _summarize(rows):
        total = len(rows)
        correct = sum(1 for r in rows if r['correct'])
        return {
            'total': total,
            'correct': correct,
            'accuracy_pct': round(correct / total * 100, 1) if total else None,
        }

    return {
        'snapshot': snapshot,
        'buckets_display': buckets_display,
        'recent_signals': signals[:30],
        'summary_all': _summarize(signals),
        'summary_buy': _summarize(buy_signals),
        'summary_sell': _summarize(sell_signals),
    }


def ai_performance_view(request):
    """AI 예측 성과 공개 트랙레코드 페이지 (로그인 불필요 — 대외 공개용)."""
    context = {'site_title': 'NextFinUp - AI 예측 성과', **build_ai_performance_context()}
    return render(request, 'articles/ai_performance.html', context)
