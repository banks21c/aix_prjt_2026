"""[실험용, 1회성] — 트리 앙상블(run_stock_prediction)과 공유 LSTM(feature/lstm-shared-model의
train_lstm_shared_model.py)을 같은 (종목, 날짜) 홀드아웃 표본에서 나란히 평가하고, 두 모델의
확률을 블렌딩(가중평균)했을 때 정확도가 둘 중 하나만 쓸 때보다 나아지는지 검증한다.

기존 LSTM 실험은 홀드아웃 결과를 (종목, 날짜) 식별자 없이 풀링해버려서 트리 앙상블의 같은
홀드아웃 표본과 나중에 짝지을 수 없었다 — 이 스크립트는 그 부분만 고쳐서 두 모델을 정확히
같은 표본에서 비교/블렌딩할 수 있게 한다. 결과가 개선되면 실제 파이프라인에 반영을 검토하고,
아니면 폐기한다(운영 크론에는 올리지 않음)."""
import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand
from lightgbm import LGBMClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split

from articles.ml.features import FEATURE_COLUMNS, get_eligible_stock_ids, build_feature_dataframe_for_stock

HOLDOUT_DAYS = 40
EMBARGO_DAYS = 5
SEQ_LEN = 30
MIN_TRAIN_ROWS = 120

LGB_PARAMS = dict(
    n_estimators=100, max_depth=6, num_leaves=31, min_child_samples=20,
    random_state=42, verbosity=-1,
)
RF_PARAMS = dict(n_estimators=100, max_depth=6, min_samples_leaf=20, random_state=42, n_jobs=1)


class Command(BaseCommand):
    help = '[실험용] 트리 앙상블 + 공유 LSTM 블렌딩이 둘 중 하나만 쓸 때보다 나은지 검증'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None, help='테스트용 종목 수 제한')
        parser.add_argument('--epochs', type=int, default=30)

    def handle(self, *args, **options):
        import tensorflow as tf  # noqa: F401 (지역 임포트 — 다른 커맨드 기동 속도에 영향 없게)
        from tensorflow import keras
        from tensorflow.keras import layers

        limit = options['limit']
        epochs = options['epochs']

        eligible_ids = get_eligible_stock_ids(include_all=False)
        if limit:
            eligible_ids = eligible_ids[:limit]
        self.stdout.write(f"대상 종목: {len(eligible_ids)}개")

        tree_holdout_rows = []
        lstm_train_X, lstm_train_y = [], []
        lstm_holdout_X, lstm_holdout_keys = [], []

        required_rows = MIN_TRAIN_ROWS + EMBARGO_DAYS + HOLDOUT_DAYS
        used, skipped = 0, 0

        for i, stock_id in enumerate(eligible_ids, start=1):
            df = build_feature_dataframe_for_stock(stock_id)
            if df.empty:
                skipped += 1
                continue
            df = df.sort_values('date').reset_index(drop=True)
            clean = df.dropna(subset=FEATURE_COLUMNS + ['target_up']).reset_index(drop=True)
            n = len(clean)
            if n < required_rows + SEQ_LEN:
                skipped += 1
                continue

            # ---- 트리 앙상블: run_stock_prediction._train_and_predict와 동일한 분할 ----
            train = clean.iloc[: -(HOLDOUT_DAYS + EMBARGO_DAYS)]
            holdout = clean.iloc[-HOLDOUT_DAYS:]
            X_train_tree = train[FEATURE_COLUMNS]
            X_holdout_tree = holdout[FEATURE_COLUMNS]

            lgb = LGBMClassifier(class_weight='balanced', **LGB_PARAMS)
            lgb.fit(X_train_tree, train['target_up'])
            rf = RandomForestClassifier(class_weight='balanced', **RF_PARAMS)
            rf.fit(X_train_tree, train['target_up'])

            tree_proba = (lgb.predict_proba(X_holdout_tree) + rf.predict_proba(X_holdout_tree)) / 2
            holdout_dates = holdout['date'].values
            holdout_labels = holdout['target_up'].values
            for j in range(len(holdout)):
                tree_holdout_rows.append({
                    'stock_id': stock_id,
                    'date': holdout_dates[j],
                    'tree_prob': float(tree_proba[j, 1]),
                    'actual': int(holdout_labels[j]),
                })

            # ---- LSTM용 시퀀스 윈도우 (train_lstm_shared_model.py와 동일 로직 + 홀드아웃에
            # (stock_id, date) 식별자를 남긴다는 점만 다름) ----
            features = clean[FEATURE_COLUMNS].values.astype('float32')
            labels = clean['target_up'].values.astype('int32')
            dates = clean['date'].values
            holdout_start_idx = n - HOLDOUT_DAYS
            embargo_start_idx = holdout_start_idx - EMBARGO_DAYS

            for label_idx in range(SEQ_LEN - 1, n):
                window = features[label_idx - SEQ_LEN + 1: label_idx + 1]
                label = labels[label_idx]
                if label_idx >= holdout_start_idx:
                    lstm_holdout_X.append(window)
                    lstm_holdout_keys.append((stock_id, dates[label_idx]))
                elif label_idx < embargo_start_idx:
                    lstm_train_X.append(window)
                    lstm_train_y.append(label)

            used += 1
            if i % 50 == 0:
                self.stdout.write(
                    f"   [{i}/{len(eligible_ids)}] 처리 중 (트리 홀드아웃 누적 {len(tree_holdout_rows)}건)"
                )
            del df, clean

        self.stdout.write(self.style.SUCCESS(
            f"1단계 완료: {used}개 종목 트리 앙상블 홀드아웃 확보(총 {len(tree_holdout_rows)}건) / "
            f"{skipped}개 제외 / LSTM 학습윈도우 {len(lstm_train_X)}개 / 홀드아웃윈도우 {len(lstm_holdout_X)}개"
        ))

        n_features = len(FEATURE_COLUMNS)
        X_train = np.stack(lstm_train_X)
        y_train = np.array(lstm_train_y)
        X_holdout = np.stack(lstm_holdout_X)
        del lstm_train_X, lstm_train_y

        scaler = StandardScaler()
        scaler.fit(X_train.reshape(-1, n_features))
        X_train = scaler.transform(X_train.reshape(-1, n_features)).reshape(X_train.shape)
        X_holdout = scaler.transform(X_holdout.reshape(-1, n_features)).reshape(X_holdout.shape)

        X_tr, X_val, y_tr, y_val = train_test_split(
            X_train, y_train, test_size=0.1, random_state=42, stratify=y_train
        )
        pos_rate = y_tr.mean()
        class_weight = {0: 0.5 / (1 - pos_rate), 1: 0.5 / pos_rate}

        model = keras.Sequential([
            layers.Input(shape=(SEQ_LEN, n_features)),
            layers.Bidirectional(layers.LSTM(64, return_sequences=True)),
            layers.Dropout(0.3),
            layers.LSTM(32),
            layers.Dropout(0.3),
            layers.Dense(16, activation='relu'),
            layers.Dense(1, activation='sigmoid'),
        ])
        model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
        early_stop = keras.callbacks.EarlyStopping(monitor='val_loss', patience=5, restore_best_weights=True)
        model.fit(
            X_tr, y_tr, validation_data=(X_val, y_val), epochs=epochs, batch_size=256,
            class_weight=class_weight, callbacks=[early_stop], verbose=2,
        )

        lstm_proba = model.predict(X_holdout, batch_size=256).flatten()

        lstm_df = pd.DataFrame({
            'stock_id': [k[0] for k in lstm_holdout_keys],
            'date': [k[1] for k in lstm_holdout_keys],
            'lstm_prob': lstm_proba,
        })
        tree_df = pd.DataFrame(tree_holdout_rows)
        tree_df['date'] = pd.to_datetime(tree_df['date'])
        lstm_df['date'] = pd.to_datetime(lstm_df['date'])

        merged = tree_df.merge(lstm_df, on=['stock_id', 'date'], how='inner')
        self.stdout.write(self.style.SUCCESS(
            f"매칭된 (종목,날짜) 홀드아웃 표본: {len(merged)}건 (트리 {len(tree_df)}건 / LSTM {len(lstm_df)}건 중)"
        ))

        def acc(pred_prob):
            return ((pred_prob >= 0.5).astype(int) == merged['actual']).mean()

        baseline = max(merged['actual'].mean(), 1 - merged['actual'].mean())
        results = [
            ('트리 단독', acc(merged['tree_prob'])),
            ('LSTM 단독', acc(merged['lstm_prob'])),
        ]
        for w in (0.3, 0.5, 0.7):
            blend = merged['tree_prob'] * w + merged['lstm_prob'] * (1 - w)
            results.append((f'블렌드(트리{w:.0%}/LSTM{1 - w:.0%})', acc(blend)))

        self.stdout.write(self.style.SUCCESS(f"\n=== 결과 (표본 {len(merged)}건, 단순기준선 {baseline:.2%}) ==="))
        for name, a in results:
            self.stdout.write(f"  {name}: {a:.2%}")

        merged.to_csv('/home/ubuntu/nextfinup/ml_experiments/ensemble_blend_holdout.csv', index=False)
        self.stdout.write(self.style.SUCCESS(
            "원본 표본은 ml_experiments/ensemble_blend_holdout.csv 에 저장했습니다."
        ))
