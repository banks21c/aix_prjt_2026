# 실험 기록: 풀링 공유 LSTM vs 트리 앙상블 (2026-08-05 ~ 2026-08-07)

## 배경 / 동기

교수님 피드백 — "트리 앙상블(RandomForest/LightGBM) 대신 딥러닝을 써보면 어떤가" — 에 대응해,
현재 운영 중인 `run_stock_prediction`의 종목별 개별 트리 앙상블 모델을 딥러닝(LSTM)으로
대체하거나 보완했을 때 방향성(다음날 상승/하락) 예측 정확도가 실제로 개선되는지 검증한
1회성 비교 실험. 운영 파이프라인에는 반영하지 않는 것을 전제로 별도 브랜치
(`feature/lstm-shared-model`)와 실험용 커맨드에서만 진행했다.

## 결론부터

**개선 없음 — 현재 운영 중인 트리 앙상블(`run_stock_prediction`)을 그대로 유지한다.**
공유 LSTM 단독도, 트리+LSTM 블렌딩도 방향성 예측에서 유의미한 개선을 보이지 못했고, 두
실험 모두 결과가 사실상 동전 던지기(50%) 수준이거나 그보다 낮았다. `feature/lstm-shared-model`
브랜치는 머지하지 않고, `eval_ensemble_blend.py`도 운영 크론에 올리지 않는다(원래 두 스크립트
docstring에 명시된 대로).

## 실험 1 — 풀링 공유 LSTM (`train_lstm_shared_model.py`, `feature/lstm-shared-model` 브랜치)

### 설계

- **접근 방식**: 종목별로 따로 모델을 두는 기존 방식과 달리, 코스피200/코스닥150(주요지수)
  전종목의 피처 시퀀스를 하나로 풀링해 LSTM 하나를 학습. 피처가 원본 가격이 아니라
  수익률/비율 기반(`articles/ml/features.py`의 `FEATURE_COLUMNS`)이라 종목마다 스케일이
  크게 다르지 않아 풀링에 적합하다고 판단.
- **입력**: 과거 30거래일(SEQ_LEN) 피처 시퀀스 → 마지막 날의 `target_up`(다음날 상승 여부)
  이진분류. `run_stock_prediction`의 direction 모델과 라벨이 동일해 직접 비교 가능.
- **홀드아웃 분리**: 종목별로 최근 40일(HOLDOUT_DAYS)은 검증용, 그 앞 5일(EMBARGO_DAYS)은
  완전히 제외(미래 라벨 누출 방지) — `run_stock_prediction._train_and_predict`와 동일한 방식을
  시퀀스(윈도우) 단위로 적용.
- **모델 구조**: `Bidirectional(LSTM(64, return_sequences=True))` → `Dropout(0.3)` →
  `LSTM(32)` → `Dropout(0.3)` → `Dense(16, relu)` → `Dense(1, sigmoid)`. 파라미터 61,601개.
- **학습 설정**: Adam, `binary_crossentropy`, `class_weight`로 상승/하락 비율 불균형 보정,
  `EarlyStopping(monitor='val_loss', patience=5)`, 최대 30 epoch, batch_size 256.

### 실행 결과 (`logs/train_lstm_shared_model.log`, 2026-08-05)

- 대상 종목 350개 전부 사용(제외 0개) → 학습 윈도우 714,172개, 홀드아웃 윈도우 14,000개.
- 23 epoch에서 조기 종료(최대 30). 학습 정확도 52%→55.4%, val_accuracy 51%→54%대로 완만히
  개선되다가 정체.
- **홀드아웃 정확도: 50.42%** (해당 홀드아웃 표본의 단순 다수클래스 기준선: **54.39%**)
- 비교 대상: `run_stock_prediction` 트리 앙상블의 (별도 표본 기준) 평균 홀드아웃 정확도 **47.51%**

### 해석

숫자만 보면 LSTM(50.42%)이 트리 앙상블(47.51%)보다 높아 보이지만, **같은 홀드아웃 표본에서
아무것도 안 하고 다수클래스만 찍어도 54.39%가 나온다** — 즉 LSTM은 자기 표본의 "찍기"보다도
못한 성능이다. 표본이 달라 트리 앙상블과의 직접 비교 자체가 엄밀하지 않다는 문제도 있어,
실험 2에서 같은 표본으로 다시 맞대본다.

## 실험 2 — 트리+LSTM 블렌딩, 동일 표본 비교 (`eval_ensemble_blend.py`, 2026-08-07)

### 설계

실험 1의 한계(표본이 달라 직접 비교 불가)를 보완하기 위해, 트리 앙상블과 공유 LSTM을
**정확히 같은 (종목, 날짜) 홀드아웃 표본**에서 나란히 평가하고, 두 모델의 확률을 가중평균
블렌딩했을 때 개선되는지 확인. `run_stock_prediction`과 동일한 RandomForest/LightGBM 설정
사용.

### 실행 결과 (`ml_experiments/ensemble_blend_holdout.csv`, 347개 종목 × 13,880건,
2026-05-29 ~ 2026-08-05)

| 방식 | 정확도 |
|---|---|
| 트리 앙상블만 | 48.72% |
| 공유 LSTM만 | 50.09% |
| 블렌딩(트리 10%+LSTM 90%) | 50.06% |
| 블렌딩(트리 20%+LSTM 80%, 최적) | **50.08%** |
| 블렌딩(트리 50%+LSTM 50%) | 49.37% |
| 블렌딩(트리 90%+LSTM 10%) | 48.82% |

- 트리 비중이 높아질수록 정확도가 낮아지는 경향(48.7~50.1% 사이)이 뚜렷하지만, 최고값(LSTM
  단독 50.09%)과 최적 블렌딩(50.08%)의 차이는 0.01%p로 사실상 동일 — **블렌딩은 LSTM 단독
  대비 아무 이득이 없다.**
- 이 표본 기준 두 모델 다 50% 안팎으로, 동전 던지기 수준에서 벗어나지 못한다.

## 남겨둔 산출물

- `ml_experiments/lstm_shared_model.keras` — 실험 1에서 학습된 모델 파일
- `ml_experiments/ensemble_blend_holdout.csv` — 실험 2의 원본 예측 결과(재계산 가능하도록 보존)
- `logs/train_lstm_shared_model.log` — 실험 1 학습 로그(epoch별 정확도/손실 전체)
- 소스: `feature/lstm-shared-model` 브랜치(`train_lstm_shared_model.py`),
  `articles/management/commands/eval_ensemble_blend.py`(현재 브랜치, 운영 크론에는 미등록)

## 향후 참고

- 두 실험 모두 "전종목 풀링" 또는 "종목별 트리" 수준에서 정체됐다 — 다음에 딥러닝을 다시
  시도한다면 (a) 시퀀스 길이/피처 조합을 바꾸거나 (b) 종목을 업종/시가총액 등으로 묶어
  풀링 단위를 좁히는 방향이 이 실험보다 나은 시작점일 수 있다.
- "다수클래스 기준선보다 낮다"는 비교를 매번 같이 보고해야 한다 — 정확도 단독 수치(50%대)만
  보면 개선처럼 보이기 쉽다.
