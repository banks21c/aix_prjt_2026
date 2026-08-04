import logging
from datetime import date, datetime, timedelta, timezone as dt_timezone

import pandas as pd
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone

from .. import kis_client
from ..ml.features import compute_display_indicators
from ..models import AnalyzedArticle, StockDailyPrice, StockItem, StockPrediction, StockRealtimePrice

logger = logging.getLogger(__name__)

KST = dt_timezone(timedelta(hours=9))


def stock_detail_view(request, ticker):
    stock = get_object_or_404(StockItem, ticker=ticker)

    prices = StockDailyPrice.objects.filter(stock=stock).order_by('-date')
    latest_price = prices.first()
    latest_pred = StockPrediction.objects.filter(stock=stock).order_by('-date').first()
    if latest_pred and latest_pred.up_probability is not None:
        # up_probability는 0.0~1.0 소수로 저장되므로, 화면 표시용(퍼센트)은 여기서 미리 계산해둔다
        # (blog_posting.py/chatbot_client.py도 같은 관례로 ×100해서 보여준다).
        latest_pred.up_probability_pct = round(latest_pred.up_probability * 100, 1)

    history = list(prices[:180])  # 최근 180거래일 정도만 차트에 표시
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

    # 기술적 지표(이동평균/RSI/MACD/볼린저밴드/거래량비율 등)는 거래량이 있는 자체 수집 데이터
    # (history)가 있을 때만 계산한다. KIS 온디맨드 조회는 거래량을 안 줘서 계산할 수 없다.
    indicators = None
    latest_indicators = None
    if history:
        ind_df = compute_display_indicators(pd.DataFrame({
            'date': [p.date for p in history],
            'close': [float(p.close_price) for p in history],
            'volume': [p.volume for p in history],
        }))

        def _series(col):
            return [
                {'time': row.date.strftime('%Y-%m-%d'), 'value': round(float(getattr(row, col)), 4)}
                for row in ind_df.itertuples()
                if pd.notna(getattr(row, col))
            ]

        indicators = {col: _series(col) for col in (
            'ma5', 'ma20', 'ma60', 'bb_upper', 'bb_lower',
            'rsi_14', 'macd', 'macd_signal', 'macd_hist', 'volume_ratio_20',
        )}

        last = ind_df.iloc[-1]
        latest_indicators = {
            col: (None if pd.isna(last[col]) else round(float(last[col]), 4))
            for col in ('ma5', 'ma20', 'ma60', 'bb_upper', 'bb_lower', 'rsi_14',
                        'macd_hist', 'volume_ratio_20')
        }
        # 전일 대비 수익률/20일 변동성은 소수 비율(0.0274=2.74%)이라, 앱 전반의 관례(퍼센트 값 저장)에
        # 맞춰 100을 곱해 퍼센트 단위로 저장한다.
        for col in ('ret_1d', 'vol_20'):
            latest_indicators[col] = None if pd.isna(last[col]) else round(float(last[col]) * 100, 2)

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
            'change_abs': abs(realtime_cache.change),
            'change_pct': realtime_cache.change_pct,
            'change_pct_abs': abs(realtime_cache.change_pct) if realtime_cache.change_pct is not None else None,
            'updated_at': realtime_cache.updated_at,
        }
    else:
        # 코스피200·코스닥150 밖이라 5분 주기 캐시(StockRealtimePrice) 대상이 아닌 종목은,
        # chatbot_client.py/generate_featured_stock_briefing.py와 같은 방식으로 KIS에
        # 온디맨드 조회해 등락률/전일대비를 채운다 — 이게 없으면 종가만 보이고 등락 정보가
        # 통째로 빠진다.
        try:
            fetched = kis_client.get_stock_close_price(stock.ticker)
            realtime = {
                'close': fetched['close'],
                'open': fetched['open'],
                'high': fetched['high'],
                'low': fetched['low'],
                'change': fetched['change'],
                'change_abs': abs(fetched['change']),
                'change_pct': fetched['change_pct'],
                'change_pct_abs': abs(fetched['change_pct']),
                'updated_at': timezone.now(),
            }
        except Exception:
            logger.exception("KIS 온디맨드 현재가 조회 실패: %s", stock.ticker)

    context = {
        'site_title': f'NextFinUp - {stock.name}',
        'stock': stock,
        'latest_price': latest_price,
        'latest_pred': latest_pred,
        'ohlc': ohlc,
        'news': news,
        'realtime': realtime,
        'indicators': indicators,
        'latest_indicators': latest_indicators,
    }
    return render(request, 'articles/stock_detail.html', context)


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


def stock_period_chart_view(request, ticker, period):
    """종목 상세 페이지의 '주봉'/'월봉' 토글이 눌렸을 때만 호출되는 온디맨드 API.
    일봉 차트(최근 180거래일)와 달리, collect_stock_data가 쌓아둔 최대 10년치 일봉
    전체를 pandas로 리샘플링해 장기 추세를 보여준다."""
    if period not in ('weekly', 'monthly'):
        return JsonResponse({'error': '잘못된 기간 구분입니다.'}, status=400)
    stock = get_object_or_404(StockItem, ticker=ticker)

    rows = list(
        StockDailyPrice.objects.filter(stock=stock).order_by('date')
        .values('date', 'open_price', 'high_price', 'low_price', 'close_price')
    )
    if not rows:
        return JsonResponse({'ohlc': []})

    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    rule = 'W-FRI' if period == 'weekly' else 'ME'
    agg = df.resample(rule).agg({
        'open_price': 'first', 'high_price': 'max', 'low_price': 'min', 'close_price': 'last',
    }).dropna(subset=['open_price'])

    # 이동평균은 일봉 기준(ma5/ma20/ma60)이 아니라, 주봉/월봉 자체 종가로 다시 계산한
    # 봉 개수 기준 이동평균이다 — 그래야 차트에 겹쳤을 때 봉 간격과 어긋나지 않는다.
    for window in (5, 20, 60):
        agg[f'ma{window}'] = agg['close_price'].rolling(window).mean()

    ohlc = [
        {
            'time': idx.strftime('%Y-%m-%d'),
            'open': float(row.open_price), 'high': float(row.high_price),
            'low': float(row.low_price), 'close': float(row.close_price),
        }
        for idx, row in agg.iterrows()
    ]

    def _ma_series(col):
        return [
            {'time': idx.strftime('%Y-%m-%d'), 'value': round(float(row[col]), 4)}
            for idx, row in agg.iterrows()
            if pd.notna(row[col])
        ]

    return JsonResponse({
        'ohlc': ohlc,
        'ma5': _ma_series('ma5'), 'ma20': _ma_series('ma20'), 'ma60': _ma_series('ma60'),
    })


def market_index_minute_chart_view(request, market_type):
    """대시보드 코스피/코스닥 차트의 '1일' 버튼이 눌렸을 때만 호출되는 온디맨드 당일 지수 API.
    (개별 종목 분봉과 달리 봉별 시가/고가/저가가 없어 시각별 지수값 하나만 내려오므로 라인차트용 데이터로 반환)"""
    if market_type not in ('KOSPI', 'KOSDAQ'):
        return JsonResponse({'error': '잘못된 시장 구분입니다.'}, status=400)
    try:
        rows = kis_client.get_today_index_minute_prices(market_type)
    except Exception:
        logger.exception("KIS 업종 당일 시간별지수 조회 실패: %s", market_type)
        return JsonResponse({'error': '당일 지수를 불러오지 못했습니다.'}, status=502)

    today = datetime.now(KST).strftime('%Y%m%d')
    line = []
    for row in rows:
        kst_dt = datetime.strptime(f"{today}{row['time']}", '%Y%m%d%H%M%S').replace(tzinfo=KST)
        line.append({'time': int(kst_dt.timestamp()), 'value': row['value']})
    return JsonResponse({'line': line})
