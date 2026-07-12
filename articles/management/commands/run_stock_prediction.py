import gc

from django.core.management.base import BaseCommand
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import mean_absolute_error

from articles.models import StockItem, StockPrediction
from articles.ml.features import FEATURE_COLUMNS, get_eligible_stock_ids, build_feature_dataframe_for_stock

# 최근 이 거래일 구간은 학습에서 제외하고 검증(홀드아웃)용으로만 사용합니다.
HOLDOUT_DAYS = 40
# target_5d_return은 5거래일 뒤 가격을 내다보는 라벨이라, 홀드아웃 바로 앞 구간까지 학습에
# 포함시키면 홀드아웃 기간의 가격 정보가 라벨을 통해 학습에 새어 들어갑니다(leakage).
# 그래서 학습/홀드아웃 사이에 5일치 간격(embargo)을 비워둡니다.
EMBARGO_DAYS = 5
# 검증이 의미를 가지려면 학습 표본이 최소 이 정도는 있어야 합니다.
MIN_TRAIN_ROWS = 120

# 그래디언트 부스팅(LightGBM)과 배깅(RandomForest)은 편향/분산 특성이 달라, 두 모델의 예측을
# 평균 내는 앙상블이 홀드아웃 40일치처럼 표본이 적은 검증에서 한쪽 모델의 우연한 과적합에
# 흔들리지 않고 더 안정적인 결과를 준다. num_leaves=31은 LightGBM 기본값(max_depth=6과 궁합이
# 맞는 과적합 방지값), min_child_samples는 RandomForest의 min_samples_leaf에 대응하는 값.
LGB_PARAMS = dict(
    n_estimators=100, max_depth=6, num_leaves=31, min_child_samples=20,
    random_state=42, verbosity=-1,
)
# n_jobs=1: RandomForest 기본값(-1)은 코어 수만큼 프로세스를 병렬로 띄우는데, 메모리가 빠듯한
# 서버(코어 2개/RAM 1GB)에서 트리 학습이 프로세스별로 중복 적재되면 다시 스왑을 채울 수 있어
# 종목별 순차 처리라는 메모리 절약 취지에 맞춰 단일 코어로 고정한다.
RF_PARAMS = dict(n_estimators=100, max_depth=6, min_samples_leaf=20, random_state=42, n_jobs=1)


class Command(BaseCommand):
    help = (
        'KOSPI200/KOSDAQ150 종목의 일봉 데이터를 학습하여 내일 방향성/수익률을 예측합니다. '
        '원본 가격 대신 수익률·기술적 지표 피처를 쓰고, 최근 구간을 홀드아웃으로 떼어내 '
        '단순 기준선(baseline) 대비 모델이 실제로 더 나은지 검증한 뒤 신호를 결정합니다.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help='한 번에 학습할 최대 종목 수 (생략 시 전체 종목). 메모리가 넉넉하지 않은 서버에서 '
                 '테스트 삼아 일부만 돌려볼 때 사용.',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("🚀 AI 주가 예측 머신러닝 모델 학습 및 예측을 시작합니다."))

        stock_ids = None
        limit = options.get('limit')
        if limit is not None:
            # DB 조회/피처 계산 단계에서부터 대상 종목을 줄여야 메모리가 부족한 서버에서
            # --limit이 실제로 효과가 있습니다(전체 종목을 다 로드한 뒤 자르면 의미가 없음).
            stock_ids = list(
                StockItem.objects
                .filter(is_major_index=True, is_active=True)
                .order_by('id')
                .values_list('id', flat=True)[:limit]
            )

        # 350개 종목 x 10년치 일봉을 하나의 DataFrame으로 한 번에 합치면(구 버전) 메모리가
        # 빠듯한 서버에서 스왑을 다 채우고 멎어버릴 수 있어, 학습 가능한 종목 id만 가볍게(DB
        # COUNT 집계만으로) 먼저 뽑고 종목을 하나씩 순차 처리한다(피크 메모리 = 종목 1개 분량).
        eligible_ids = get_eligible_stock_ids(stock_ids=stock_ids)
        if not eligible_ids:
            self.stdout.write(self.style.WARNING("[-] 학습할 데이터가 없습니다 (KOSPI200/KOSDAQ150 종목 OHLCV 미수집)."))
            return

        target_cols = ['target_next_return', 'target_5d_return', 'target_up']

        for stock_id in eligible_ids:
            group = build_feature_dataframe_for_stock(stock_id)
            if group.empty:
                continue
            # try/finally로 감싸 continue든 정상 완료든 종목마다 반드시 메모리를 반납하고 넘어간다
            # (여기서 안 놓으면 350개 종목을 거치는 동안 이전 종목들의 DataFrame/모델이 계속 쌓인다).
            try:
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

                # 방향성(내일 상승/하락): LightGBM + RandomForest 확률을 평균한 앙상블.
                direction_lgb = LGBMClassifier(class_weight='balanced', **LGB_PARAMS)
                direction_lgb.fit(X_train, train['target_up'])
                direction_rf = RandomForestClassifier(class_weight='balanced', **RF_PARAMS)
                direction_rf.fit(X_train, train['target_up'])

                holdout_proba = (
                    direction_lgb.predict_proba(X_holdout) + direction_rf.predict_proba(X_holdout)
                ) / 2
                holdout_acc = ((holdout_proba[:, 1] >= 0.5).astype(int) == holdout['target_up'].values).mean()
                # "항상 다수 클래스로 찍는다"는 순진한 기준선. 이걸 못 이기면 방향성 예측은 의미가 없습니다.
                baseline_acc = max(holdout['target_up'].mean(), 1 - holdout['target_up'].mean())
                beats_baseline = holdout_acc > baseline_acc

                # 5일 후 수익률: 마찬가지로 두 회귀 모델의 예측을 평균.
                return5_lgb = LGBMRegressor(**LGB_PARAMS)
                return5_lgb.fit(X_train, train['target_5d_return'])
                return5_rf = RandomForestRegressor(**RF_PARAMS)
                return5_rf.fit(X_train, train['target_5d_return'])
                holdout_pred_5d = (return5_lgb.predict(X_holdout) + return5_rf.predict(X_holdout)) / 2
                model_mae = mean_absolute_error(holdout['target_5d_return'], holdout_pred_5d)
                naive_mae = holdout['target_5d_return'].abs().mean()  # "5일간 수익률 0%로 찍는다" 기준선

                # 내일 종가 산출용 1일 수익률도 동일하게 앙상블.
                return1_lgb = LGBMRegressor(**LGB_PARAMS)
                return1_lgb.fit(X_train, train['target_next_return'])
                return1_rf = RandomForestRegressor(**RF_PARAMS)
                return1_rf.fit(X_train, train['target_next_return'])

                X_latest = latest_row[FEATURE_COLUMNS]
                latest_proba = (direction_lgb.predict_proba(X_latest) + direction_rf.predict_proba(X_latest)) / 2
                prob_down, prob_up = latest_proba[0]
                pred_ret1 = float(((return1_lgb.predict(X_latest) + return1_rf.predict(X_latest)) / 2)[0])
                pred_ret5 = float(((return5_lgb.predict(X_latest) + return5_rf.predict(X_latest)) / 2)[0])

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
            finally:
                del group
                gc.collect()

        self.stdout.write(self.style.SUCCESS("🎉 모든 종목에 대한 AI 주가 예측 분석 데이터 적재가 완료되었습니다!"))
