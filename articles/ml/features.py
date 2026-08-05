"""
articles/ml/features.py 로 저장하세요.

StockDailyPrice(일봉 OHLCV)로부터 학습/예측용 피처와 라벨을 계산하는 공용 모듈입니다.
train_model.py, predict_stock.py 둘 다 이 모듈을 그대로 임포트해서 씁니다.
(학습 때 쓴 피처 계산 로직과 예측 때 쓴 로직이 어긋나면 안 되므로 반드시 한 곳에서만 정의합니다.)
"""
import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    'ret_1d', 'ret_5d', 'ret_10d', 'ret_20d',
    'ma5_gap', 'ma20_gap', 'ma60_gap',
    'vol_20',
    'rsi_14',
    'macd', 'macd_signal', 'macd_hist',
    'volume_ratio_20',
    'bb_pct',
]

# StockInvestorFlow(종목별 투자자매매동향, collect_investor_flow) 기반 수급 피처. FEATURE_COLUMNS와
# 별도 목록으로 두는 이유는 run_stock_prediction이 "기본 피처만" 모델과 "기본+수급" 모델을 같은
# 종목·같은 날짜에 나란히 학습해 StockPrediction의 *_flow 필드로 비교할 수 있게 하기 위함이다
# (수급 데이터가 1년치뿐이라 전체 히스토리 대비 최근 구간에서만 계산 가능 — add_investor_flow_features
# 참고).
# 하루치 순매수비율(_net_ratio)은 하루 노이즈에 취약해 1차 비교(홀드아웃 정확도 47.51%→46.83%)에서
# 신호로 못 잡혔다. 기관/외국인 수급은 하루가 아니라 며칠에 걸쳐 매집·매도가 이어지는 경향이 있어
# _5d(5거래일 누적) 컬럼을 추가로 넣어, 지속적인 순매수/순매도 추세를 모델이 볼 수 있게 한다.
FLOW_FEATURE_COLUMNS = [
    'foreign_net_ratio', 'institution_net_ratio', 'pension_net_ratio',
    'foreign_net_ratio_5d', 'institution_net_ratio_5d', 'pension_net_ratio_5d',
]

MIN_HISTORY_DAYS = 60  # 60거래일(ma60 등 계산에 필요한 최소 길이) 미만 종목은 제외


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    macd_hist = macd - macd_signal
    return macd, macd_signal, macd_hist


def add_features_for_one_stock(df: pd.DataFrame) -> pd.DataFrame:
    """단일 종목의 OHLCV DataFrame(컬럼: date, open, high, low, close, volume)에
    피처 컬럼과 라벨 컬럼을 추가해서 반환합니다. 반드시 한 종목 데이터만 넣어야 합니다
    (여러 종목을 섞으면 이동평균/수익률 계산이 종목 경계를 넘어가 오염됩니다).
    """
    df = df.sort_values('date').reset_index(drop=True)
    close = df['close']
    volume = df['volume']

    df['ret_1d'] = close.pct_change(1)
    df['ret_5d'] = close.pct_change(5)
    df['ret_10d'] = close.pct_change(10)
    df['ret_20d'] = close.pct_change(20)

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    df['ma5_gap'] = close / ma5 - 1
    df['ma20_gap'] = close / ma20 - 1
    df['ma60_gap'] = close / ma60 - 1

    df['vol_20'] = df['ret_1d'].rolling(20).std()
    df['rsi_14'] = _rsi(close, 14)

    macd, macd_signal, macd_hist = _macd(close)
    df['macd'] = macd
    df['macd_signal'] = macd_signal
    df['macd_hist'] = macd_hist

    vol_ma20 = volume.rolling(20).mean()
    df['volume_ratio_20'] = volume / vol_ma20.replace(0, np.nan)

    bb_mid = ma20
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    df['bb_pct'] = (close - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)

    # ---- 라벨: 미래 정보를 사용하므로 학습 시에만 사용합니다 ----
    # 마지막 1행(target_next_return)/마지막 5행(target_5d_return)은 미래 데이터가
    # 없어 자동으로 NaN이 되며, 이는 정상입니다(추론 시 그 최신 행을 예측 대상으로 씁니다).
    df['target_next_return'] = close.shift(-1) / close - 1
    df['target_5d_return'] = close.shift(-5) / close - 1
    target_up = (df['target_next_return'] > 0).astype('Int64')
    # NaN > 0 은 False로 계산되므로, .mask()로 원래 NaN이었던 자리를 다시 결측으로 되돌립니다.
    df['target_up'] = target_up.mask(df['target_next_return'].isna())

    return df


def get_eligible_stock_ids(min_history_days: int = MIN_HISTORY_DAYS, stock_ids=None,
                            include_all: bool = False) -> list:
    """학습 대상 종목 id 목록을, 일봉 데이터를 메모리에 전혀 올리지 않고 DB 집계(COUNT)만으로 뽑아냅니다.

    메모리가 빠듯한 서버에서 350개 종목(코스피200/코스닥150) x 10년치 일봉을 한 번에 하나의
    DataFrame으로 합치면(구 build_feature_dataframe) 700만 행 가까이 쌓여 스왑을 다 채우고
    서버가 멎을 수 있습니다. 그래서 이 함수로 "학습 가능한 종목 id"만 가볍게 먼저 뽑고,
    run_stock_prediction이 build_feature_dataframe_for_stock()으로 종목을 하나씩 순차 처리합니다.

    기본은 collect_stock_data와 동일하게 is_major_index=True(코스피200/코스닥150)만 대상으로
    좁히고, include_all=True(run_stock_prediction --all)일 때만 is_active=True 전체 종목을
    대상으로 합니다.
    """
    from django.db.models import Count
    from articles.models import StockDailyPrice

    stock_filter = {'stock__is_active': True}
    if not include_all:
        stock_filter['stock__is_major_index'] = True
    qs = StockDailyPrice.objects.filter(**stock_filter)
    if stock_ids is not None:
        qs = qs.filter(stock_id__in=stock_ids)

    counts = (
        qs.values('stock_id')
        .annotate(cnt=Count('id'))
        .filter(cnt__gte=min_history_days)
        .order_by('stock_id')
    )
    return [row['stock_id'] for row in counts]


def build_feature_dataframe_for_stock(stock_id: int) -> pd.DataFrame:
    """단일 종목의 일봉(StockDailyPrice)만 DB에서 읽어 피처/라벨을 계산해 반환합니다.

    이전 build_feature_dataframe()처럼 전체 종목을 한 DataFrame으로 합치지 않는 것이 핵심입니다.
    호출 측(run_stock_prediction)이 종목 id 하나씩 이 함수를 호출해 학습을 끝낸 뒤 결과를 버리므로,
    피크 메모리 사용량이 "종목 1개의 10년치 일봉" 규모로 제한됩니다.
    """
    from articles.models import StockDailyPrice

    qs = (
        StockDailyPrice.objects
        .filter(stock_id=stock_id)
        .values(
            'stock_id', 'stock__ticker', 'stock__name', 'stock__market_type',
            'date', 'open_price', 'high_price', 'low_price', 'close_price', 'volume',
        )
    )
    raw = pd.DataFrame.from_records(qs)
    if raw.empty:
        return raw

    raw = raw.rename(columns={
        'stock__ticker': 'ticker', 'stock__name': 'name', 'stock__market_type': 'market_type',
        'open_price': 'open', 'high_price': 'high', 'low_price': 'low', 'close_price': 'close',
    })
    # DecimalField -> float (그렇지 않으면 rolling/ewm 연산이 매우 느리거나 실패합니다)
    for col in ('open', 'high', 'low', 'close'):
        raw[col] = raw[col].astype(float)
    raw['volume'] = raw['volume'].astype(float)

    return add_features_for_one_stock(raw)


def add_investor_flow_features(df: pd.DataFrame, stock_id: int) -> pd.DataFrame:
    """build_feature_dataframe_for_stock()이 만든 df(date/volume 컬럼 포함)에 StockInvestorFlow
    기반 수급 비율 피처(FLOW_FEATURE_COLUMNS)를 좌측 조인으로 덧붙인다. 순매수 수량을 그날
    거래량으로 나눠 종목 규모와 무관하게 비교 가능한 비율로 만든다(원본 수량은 시가총액이 크게
    다른 종목끼리 그대로 비교할 수 없다). StockInvestorFlow는 최근 1년치만 있어 그 이전 날짜는
    NaN이 된다 — 정상이며, 호출부(run_stock_prediction)가 이 컬럼이 채워진 행만 골라 별도로
    학습한다."""
    from articles.models import StockInvestorFlow

    df = df.copy()
    flow_qs = StockInvestorFlow.objects.filter(stock_id=stock_id).values(
        'date', 'foreign_net_qty', 'institution_net_qty', 'pension_net_qty',
    )
    flow_df = pd.DataFrame.from_records(flow_qs)
    if flow_df.empty:
        for col in FLOW_FEATURE_COLUMNS:
            df[col] = np.nan
        return df

    df['date'] = pd.to_datetime(df['date'])
    flow_df['date'] = pd.to_datetime(flow_df['date'])
    merged = df.merge(flow_df, on='date', how='left').sort_values('date').reset_index(drop=True)

    safe_volume = merged['volume'].replace(0, np.nan)
    merged['foreign_net_ratio'] = merged['foreign_net_qty'] / safe_volume
    merged['institution_net_ratio'] = merged['institution_net_qty'] / safe_volume
    merged['pension_net_ratio'] = merged['pension_net_qty'] / safe_volume

    # 5거래일 누적 순매수수량 / 5거래일 누적 거래량 — 하루 단위 비율보다 지속적인 수급 추세에
    # 덜 민감하게 흔들린다. min_periods=3으로 둬 수급 데이터 시작 직후 며칠도 바로 값이 생기게
    # 한다(그 이전 구간은 net_qty 자체가 NaN이라 rolling sum도 자연히 NaN이 된다).
    safe_volume_5d = merged['volume'].rolling(5, min_periods=3).sum().replace(0, np.nan)
    for base, qty_col in (
        ('foreign', 'foreign_net_qty'),
        ('institution', 'institution_net_qty'),
        ('pension', 'pension_net_qty'),
    ):
        merged[f'{base}_net_ratio_5d'] = (
            merged[qty_col].rolling(5, min_periods=3).sum() / safe_volume_5d
        )

    return merged.drop(columns=['foreign_net_qty', 'institution_net_qty', 'pension_net_qty'])


def compute_display_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """종목 상세 페이지 차트/지표 카드에 화면 표시용(원본 값) 기술지표를 계산해 반환합니다.
    df는 단일 종목의 OHLCV(date, open, high, low, close, volume)만 있으면 됩니다.

    add_features_for_one_stock()의 피처(ma20_gap 등)는 모델 입력용 '비율'이라 화면에 그대로
    보여주기 어려우므로, RSI/MACD 계산 로직(_rsi/_macd)만 그대로 재사용하고 이동평균·볼린저밴드·
    거래량비율은 여기서 원본 값(가격/배수 단위)으로 따로 계산합니다.
    """
    df = df.sort_values('date').reset_index(drop=True)
    close = df['close']
    volume = df['volume']

    out = pd.DataFrame({'date': df['date']})
    out['ma5'] = close.rolling(5).mean()
    out['ma20'] = close.rolling(20).mean()
    out['ma60'] = close.rolling(60).mean()

    bb_std = close.rolling(20).std()
    out['bb_upper'] = out['ma20'] + 2 * bb_std
    out['bb_lower'] = out['ma20'] - 2 * bb_std

    out['rsi_14'] = _rsi(close, 14)

    macd, macd_signal, macd_hist = _macd(close)
    out['macd'] = macd
    out['macd_signal'] = macd_signal
    out['macd_hist'] = macd_hist

    vol_ma20 = volume.rolling(20).mean()
    out['volume_ratio_20'] = volume / vol_ma20.replace(0, np.nan)

    out['ret_1d'] = close.pct_change(1)
    out['vol_20'] = out['ret_1d'].rolling(20).std()

    return out
