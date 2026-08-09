import pandas as pd
from django.core.management.base import BaseCommand

from articles.models import MarketIndex, PredictionAccuracySnapshot, StockDailyPrice, StockPrediction

# SIGNAL_PROB_THRESHOLD(run_stock_prediction.py) 재측정과 동일한 방법론 — 신뢰도
# (confidence = |up_probability-0.5|*2) 구간별 누적 정확도. 이 목록에 임계값을 추가/변경하면
# run_stock_prediction.py의 SIGNAL_PROB_THRESHOLD 재측정 때도 같은 값을 참고하면 된다.
PROB_THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]


class Command(BaseCommand):
    help = (
        'StockPrediction 전체 이력과 StockDailyPrice를 조인해, 신뢰도 구간별 실현 정확도를 '
        '계산하고 PredictionAccuracySnapshot에 저장합니다 (/performance/ 트랙레코드 페이지가 '
        '읽는 캐시 — 이 조인이 실측 약 6초 걸려 페이지 요청마다 라이브로 돌리지 않습니다). '
        '매일 새벽 run_stock_prediction 이후 크론으로 실행하세요.'
    )

    def handle(self, *args, **options):
        preds = pd.DataFrame.from_records(
            StockPrediction.objects.filter(up_probability__isnull=False)
            .values('stock_id', 'date', 'up_probability')
        )
        if preds.empty:
            self.stdout.write(self.style.WARNING("[-] up_probability가 채워진 예측이 없습니다. 건너뜁니다."))
            return

        # 다음 거래일 계산은 종목별 StockDailyPrice 전체를 훑는 대신, 훨씬 작은 MarketIndex의
        # 거래일 달력을 기준으로 삼는다 — 필요한 날짜만(예측일 + 그 다음 거래일) 추려서
        # StockDailyPrice를 그 날짜 집합으로만 필터링하면 조인 대상이 크게 줄어든다(실측:
        # 종목 전체 이력 530만 행 조인 24초 -> 필요한 날짜만 필터링 6초).
        trading_dates = sorted(MarketIndex.objects.filter(market_type='KOSPI').values_list('date', flat=True))
        date_to_next = {d: trading_dates[i + 1] for i, d in enumerate(trading_dates[:-1])}
        preds['next_date'] = preds['date'].map(date_to_next)

        needed_dates = set(preds['date']) | set(preds['next_date'].dropna())
        stock_ids = preds['stock_id'].unique().tolist()
        prices = pd.DataFrame.from_records(
            StockDailyPrice.objects.filter(stock_id__in=stock_ids, date__in=needed_dates)
            .values('stock_id', 'date', 'close_price')
        )
        prices['close_price'] = prices['close_price'].astype(float)

        merged = preds.merge(
            prices.rename(columns={'close_price': 'close_on_date'}), on=['stock_id', 'date'], how='inner'
        )
        merged = merged.merge(
            prices.rename(columns={'date': 'next_date', 'close_price': 'next_close'}),
            on=['stock_id', 'next_date'], how='inner',
        )

        if merged.empty:
            self.stdout.write(self.style.WARNING("[-] 결과가 확정된(다음 거래일 종가가 있는) 예측이 없습니다."))
            return

        merged['actual_up'] = merged['next_close'] > merged['close_on_date']
        merged['predicted_up'] = merged['up_probability'] >= 0.5
        merged['correct'] = merged['actual_up'] == merged['predicted_up']
        merged['confidence'] = (merged['up_probability'] - 0.5).abs() * 2

        buckets = []
        for threshold in PROB_THRESHOLDS:
            conf = (threshold - 0.5) * 2
            subset = merged[merged['confidence'] >= conf]
            n = len(subset)
            buckets.append({
                'threshold': threshold,
                'confidence': round(conf, 2),
                'n': n,
                'accuracy': round(float(subset['correct'].mean()), 4) if n else None,
            })

        snapshot = PredictionAccuracySnapshot.objects.create(
            total_resolved=len(merged),
            overall_accuracy=round(float(merged['correct'].mean()), 4),
            buckets=buckets,
        )
        self.stdout.write(self.style.SUCCESS(
            f"🎉 정확도 스냅샷 저장 완료 (id={snapshot.id}, 표본 {len(merged)}건, "
            f"전체 정확도 {snapshot.overall_accuracy:.1%})"
        ))
