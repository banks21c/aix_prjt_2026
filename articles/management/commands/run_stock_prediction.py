from django.core.management.base import BaseCommand
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import mean_absolute_error

from articles.models import StockPrediction
from articles.ml.features import FEATURE_COLUMNS, build_feature_dataframe

# 최근 이 거래일 구간은 학습에서 제외하고 검증(홀드아웃)용으로만 사용합니다.
HOLDOUT_DAYS = 40
# target_5d_return은 5거래일 뒤 가격을 내다보는 라벨이라, 홀드아웃 바로 앞 구간까지 학습에
# 포함시키면 홀드아웃 기간의 가격 정보가 라벨을 통해 학습에 새어 들어갑니다(leakage).
# 그래서 학습/홀드아웃 사이에 5일치 간격(embargo)을 비워둡니다.
EMBARGO_DAYS = 5
# 검증이 의미를 가지려면 학습 표본이 최소 이 정도는 있어야 합니다.
MIN_TRAIN_ROWS = 120

RF_PARAMS = dict(n_estimators=100, max_depth=6, min_samples_leaf=20, random_state=42)


class Command(BaseCommand):
    help = (
        'KOSPI200/KOSDAQ150 종목의 일봉 데이터를 학습하여 내일 방향성/수익률을 예측합니다. '
        '원본 가격 대신 수익률·기술적 지표 피처를 쓰고, 최근 구간을 홀드아웃으로 떼어내 '
        '단순 기준선(baseline) 대비 모델이 실제로 더 나은지 검증한 뒤 신호를 결정합니다.'
    )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("🚀 AI 주가 예측 머신러닝 모델 학습 및 예측을 시작합니다."))

        feature_df = build_feature_dataframe()
        if feature_df.empty:
            self.stdout.write(self.style.WARNING("[-] 학습할 데이터가 없습니다 (KOSPI200/KOSDAQ150 종목 OHLCV 미수집)."))
            return

        target_cols = ['target_next_return', 'target_5d_return', 'target_up']

        for stock_id, group in feature_df.groupby('stock_id'):
            group = group.sort_values('date').reset_index(drop=True)
            name, ticker = group['name'].iloc[-1], group['ticker'].iloc[-1]

            latest_row = group.iloc[[-1]]
            if latest_row[FEATURE_COLUMNS].isnull().values.any():
                self.stdout.write(self.style.WARNING(f"[-] {name}({ticker}) 최신 거래일 지표 결측으로 패스"))
                continue

            labeled = group.dropna(subset=FEATURE_COLUMNS + target_cols).copy()
            labeled['target_up'] = labeled['target_up'].astype(int)

            required_rows = MIN_TRAIN_ROWS + EMBARGO_DAYS + HOLDOUT_DAYS
            if len(labeled) < required_rows:
                self.stdout.write(self.style.WARNING(
                    f"[-] {name}({ticker}) 데이터 부족으로 패스 (학습 가능 {len(labeled)}행, 최소 {required_rows}행 필요)"
                ))
                continue

            # 시간순으로 앞부분만 학습에 쓰고, 뒷부분은 embargo(라벨 누출 방지)를 두고
            # 그 다음 구간을 홀드아웃 검증에 씁니다. 이 학습셋을 실전 예측에도 그대로 씁니다
            # (가장 최근 데이터를 조금 희생하는 대신, 저장되는 모델이 검증받은 바로 그 모델이 되도록).
            train = labeled.iloc[: -(HOLDOUT_DAYS + EMBARGO_DAYS)]
            holdout = labeled.iloc[-HOLDOUT_DAYS:]

            X_train, X_holdout = train[FEATURE_COLUMNS], holdout[FEATURE_COLUMNS]

            direction_model = RandomForestClassifier(class_weight='balanced', **RF_PARAMS)
            direction_model.fit(X_train, train['target_up'])

            holdout_acc = direction_model.score(X_holdout, holdout['target_up'])
            # "항상 다수 클래스로 찍는다"는 순진한 기준선. 이걸 못 이기면 방향성 예측은 의미가 없습니다.
            baseline_acc = max(holdout['target_up'].mean(), 1 - holdout['target_up'].mean())
            beats_baseline = holdout_acc > baseline_acc

            return5_model = RandomForestRegressor(**RF_PARAMS)
            return5_model.fit(X_train, train['target_5d_return'])
            model_mae = mean_absolute_error(holdout['target_5d_return'], return5_model.predict(X_holdout))
            naive_mae = holdout['target_5d_return'].abs().mean()  # "5일간 수익률 0%로 찍는다" 기준선

            return1_model = RandomForestRegressor(**RF_PARAMS)
            return1_model.fit(X_train, train['target_next_return'])

            X_latest = latest_row[FEATURE_COLUMNS]
            prob_down, prob_up = direction_model.predict_proba(X_latest)[0]
            pred_ret1 = float(return1_model.predict(X_latest)[0])
            pred_ret5 = float(return5_model.predict(X_latest)[0])

            latest_close = float(latest_row['close'].iloc[0])
            pred_close = latest_close * (1 + pred_ret1)

            if not beats_baseline:
                signal = 'HOLD'
            elif prob_up >= 0.60 and pred_ret5 > 0.01:
                signal = 'BUY'
            elif prob_down >= 0.60 and pred_ret5 < -0.01:
                signal = 'SELL'
            else:
                signal = 'HOLD'

            record = StockPrediction.objects.get(id=int(latest_row['id'].iloc[0]))
            record.pred_next_close = round(pred_close, 2)
            record.pred_5day_return = round(pred_ret5 * 100, 2)
            record.up_probability = round(float(prob_up), 4)
            record.down_probability = round(float(prob_down), 4)
            record.trading_signal = signal
            record.save()

            validation_note = (
                f"검증정확도 {holdout_acc:.1%}(기준 {baseline_acc:.1%}) "
                f"{'✅베스트기준선' if beats_baseline else '⚠️기준선미달→HOLD고정'} | "
                f"5일수익률MAE {model_mae:.2%}(기준 {naive_mae:.2%})"
            )
            self.stdout.write(self.style.SUCCESS(
                f"   [+] {name} 예측 완료 ➔ 내일종가 {record.pred_next_close}원 | "
                f"상승확률 {prob_up:.1%} | 5일수익률 {record.pred_5day_return}% | 신호 {signal} | {validation_note}"
            ))

        self.stdout.write(self.style.SUCCESS("🎉 모든 종목에 대한 AI 주가 예측 분석 데이터 적재가 완료되었습니다!"))
