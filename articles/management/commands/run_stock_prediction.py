import gc

from django.core.management.base import BaseCommand
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import mean_absolute_error

from articles.models import StockItem, StockPrediction
from articles.ml.features import (
    FEATURE_COLUMNS, FLOW_FEATURE_COLUMNS, get_eligible_stock_ids,
    build_feature_dataframe_for_stock, add_investor_flow_features,
)
from articles.utils import round_to_krx_tick

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


def _train_and_predict(feature_cols, labeled, latest_row):
    """labeled(피처+라벨 결측 없는 행들)로 방향성/1일/5일 수익률 앙상블(LightGBM+RandomForest)을
    학습하고, 시간순 뒤쪽 HOLDOUT_DAYS로 검증한 뒤 latest_row에 대한 예측을 반환한다.
    표본이 부족하면(required_rows 미달) None. baseline(FEATURE_COLUMNS)과 수급 반영
    (FEATURE_COLUMNS+FLOW_FEATURE_COLUMNS) 두 변형이 이 함수 하나를 그대로 재사용해, 피처 구성만
    다르고 나머지 학습/검증 로직은 완전히 동일하게 맞춘다(그래야 비교가 의미 있음)."""
    required_rows = MIN_TRAIN_ROWS + EMBARGO_DAYS + HOLDOUT_DAYS
    if len(labeled) < required_rows:
        return None, f"데이터 부족 (학습 가능 {len(labeled)}행, 최소 {required_rows}행 필요)"

    train = labeled.iloc[: -(HOLDOUT_DAYS + EMBARGO_DAYS)]
    holdout = labeled.iloc[-HOLDOUT_DAYS:]
    X_train, X_holdout = train[feature_cols], holdout[feature_cols]

    direction_lgb = LGBMClassifier(class_weight='balanced', **LGB_PARAMS)
    direction_lgb.fit(X_train, train['target_up'])
    direction_rf = RandomForestClassifier(class_weight='balanced', **RF_PARAMS)
    direction_rf.fit(X_train, train['target_up'])

    holdout_proba = (
        direction_lgb.predict_proba(X_holdout) + direction_rf.predict_proba(X_holdout)
    ) / 2
    holdout_acc = ((holdout_proba[:, 1] >= 0.5).astype(int) == holdout['target_up'].values).mean()
    baseline_acc = max(holdout['target_up'].mean(), 1 - holdout['target_up'].mean())
    beats_baseline = holdout_acc > baseline_acc

    return5_lgb = LGBMRegressor(**LGB_PARAMS)
    return5_lgb.fit(X_train, train['target_5d_return'])
    return5_rf = RandomForestRegressor(**RF_PARAMS)
    return5_rf.fit(X_train, train['target_5d_return'])
    holdout_pred_5d = (return5_lgb.predict(X_holdout) + return5_rf.predict(X_holdout)) / 2
    model_mae = mean_absolute_error(holdout['target_5d_return'], holdout_pred_5d)
    naive_mae = holdout['target_5d_return'].abs().mean()

    return1_lgb = LGBMRegressor(**LGB_PARAMS)
    return1_lgb.fit(X_train, train['target_next_return'])
    return1_rf = RandomForestRegressor(**RF_PARAMS)
    return1_rf.fit(X_train, train['target_next_return'])

    X_latest = latest_row[feature_cols]
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

    validation_note = (
        f"검증정확도 {holdout_acc:.1%}(기준 {baseline_acc:.1%}) "
        f"{'✅베스트기준선' if beats_baseline else '⚠️기준선미달→HOLD고정'} | "
        f"5일수익률MAE {model_mae:.2%}(기준 {naive_mae:.2%})"
    )
    result = {
        'pred_close': round_to_krx_tick(pred_close),
        'pred_ret5': round(pred_ret5 * 100, 2),
        'prob_up': round(float(prob_up), 4),
        'prob_down': round(float(prob_down), 4),
        'signal': signal,
        'holdout_acc': round(float(holdout_acc), 4),
    }
    return result, validation_note


class Command(BaseCommand):
    help = (
        '기본적으로 is_major_index=True(코스피200/코스닥150) 종목의 일봉 데이터를 학습하여 내일 '
        '방향성/수익률을 예측합니다. --all 지정 시 is_active 전체 종목 대상. 원본 가격 대신 '
        '수익률·기술적 지표 피처를 쓰고, 최근 구간을 홀드아웃으로 떼어내 단순 기준선(baseline) '
        '대비 모델이 실제로 더 나은지 검증한 뒤 신호를 결정합니다.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help='한 번에 학습할 최대 종목 수 (생략 시 전체 종목). 메모리가 넉넉하지 않은 서버에서 '
                 '테스트 삼아 일부만 돌려볼 때 사용.',
        )
        parser.add_argument(
            '--all', action='store_true',
            help='is_major_index 여부와 무관하게 is_active=True 전체 종목을 대상으로 학습합니다.',
        )
        parser.add_argument(
            '--tickers', type=str, default=None,
            help='쉼표로 구분한 특정 종목코드만 대상으로 합니다(테스트/재시도용). --limit/--all보다 우선.',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("🚀 AI 주가 예측 머신러닝 모델 학습 및 예측을 시작합니다."))

        include_all = options.get('all', False)
        stock_ids = None
        tickers_arg = options.get('tickers')
        if tickers_arg:
            wanted = [t.strip() for t in tickers_arg.split(',') if t.strip()]
            stock_ids = list(StockItem.objects.filter(ticker__in=wanted).values_list('id', flat=True))
        limit = options.get('limit')
        if limit is not None and stock_ids is None:
            # DB 조회/피처 계산 단계에서부터 대상 종목을 줄여야 메모리가 부족한 서버에서
            # --limit이 실제로 효과가 있습니다(전체 종목을 다 로드한 뒤 자르면 의미가 없음).
            stock_filter = {'is_active': True}
            if not include_all:
                stock_filter['is_major_index'] = True
            stock_ids = list(
                StockItem.objects
                .filter(**stock_filter)
                .order_by('id')
                .values_list('id', flat=True)[:limit]
            )

        # 350개 종목 x 10년치 일봉을 하나의 DataFrame으로 한 번에 합치면(구 버전) 메모리가
        # 빠듯한 서버에서 스왑을 다 채우고 멎어버릴 수 있어, 학습 가능한 종목 id만 가볍게(DB
        # COUNT 집계만으로) 먼저 뽑고 종목을 하나씩 순차 처리한다(피크 메모리 = 종목 1개 분량).
        eligible_ids = get_eligible_stock_ids(stock_ids=stock_ids, include_all=include_all)
        if not eligible_ids:
            scope = "is_active=True" if include_all else "KOSPI200/KOSDAQ150"
            self.stdout.write(self.style.WARNING(f"[-] 학습할 데이터가 없습니다 ({scope} 종목 OHLCV 미수집)."))
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

                base_result, base_note = _train_and_predict(FEATURE_COLUMNS, labeled, latest_row)
                if base_result is None:
                    self.stdout.write(self.style.WARNING(f"[-] {name}({ticker}) {base_note}"))
                    continue

                # 수급 반영(flow) 변형 — 같은 종목에 StockInvestorFlow 기반 피처를 추가로 붙여
                # 같은 방식으로 한 번 더 학습/검증한다. 수급 데이터가 최근 1년치뿐이라 표본이
                # 부족하면(required_rows 미달) 조용히 건너뛰고 baseline 결과만 저장한다 —
                # "피처를 추가하면 더 나빠지는지도" 포함해 비교하는 게 목적이라, 여기서 실패해도
                # 전체 커맨드를 막지 않는다.
                flow_group = add_investor_flow_features(group, stock_id)
                flow_cols = FEATURE_COLUMNS + FLOW_FEATURE_COLUMNS
                flow_latest_row = flow_group.iloc[[-1]]
                flow_result, flow_note = None, "수급 데이터 없음"
                if not flow_latest_row[FLOW_FEATURE_COLUMNS].isnull().values.any():
                    flow_labeled = flow_group.dropna(subset=flow_cols + target_cols).copy()
                    flow_labeled['target_up'] = flow_labeled['target_up'].astype(int)
                    flow_result, flow_note = _train_and_predict(flow_cols, flow_labeled, flow_latest_row)

                # StockDailyPrice(가격)와 StockPrediction(예측)이 분리된 뒤로는 공유 id가 없어,
                # 가격 쪽에서 읽어온 (종목, 날짜)로 예측 행을 찾거나 새로 만든다.
                defaults = {
                    'pred_next_close': base_result['pred_close'],
                    'pred_5day_return': base_result['pred_ret5'],
                    'up_probability': base_result['prob_up'],
                    'down_probability': base_result['prob_down'],
                    'trading_signal': base_result['signal'],
                    'holdout_accuracy': base_result['holdout_acc'],
                }
                if flow_result:
                    defaults.update({
                        'pred_next_close_flow': flow_result['pred_close'],
                        'pred_5day_return_flow': flow_result['pred_ret5'],
                        'up_probability_flow': flow_result['prob_up'],
                        'down_probability_flow': flow_result['prob_down'],
                        'trading_signal_flow': flow_result['signal'],
                        'holdout_accuracy_flow': flow_result['holdout_acc'],
                    })

                record, _ = StockPrediction.objects.update_or_create(
                    stock_id=int(latest_row['stock_id'].iloc[0]),
                    date=latest_row['date'].iloc[0],
                    defaults=defaults,
                )

                flow_summary = (
                    f"수급반영 검증정확도 {flow_result['holdout_acc']:.1%}"
                    if flow_result else f"수급반영 스킵({flow_note})"
                )
                self.stdout.write(self.style.SUCCESS(
                    f"   [+] {name} 예측 완료 ➔ 내일종가 {record.pred_next_close}원 | "
                    f"상승확률 {base_result['prob_up']:.1%} | 5일수익률 {base_result['pred_ret5']}% | "
                    f"신호 {base_result['signal']} | {base_note} | {flow_summary}"
                ))
            finally:
                del group
                gc.collect()

        self.stdout.write(self.style.SUCCESS("🎉 모든 종목에 대한 AI 주가 예측 분석 데이터 적재가 완료되었습니다!"))
