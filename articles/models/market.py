from django.db import models


# ==========================================
# 1. 주식 종목 테이블 (KOSPI 200, KOSDAQ 200 관리)
# ==========================================
class StockItem(models.Model):
    MARKET_CHOICES = [
        ('KOSPI', '코스피'),
        ('KOSDAQ', '코스닥'),
    ]

    ticker = models.CharField(max_length=10, unique=True, verbose_name="종목코드") # ex) 005930
    name = models.CharField(max_length=50, verbose_name="종목명") # ex) 삼성전자
    market_type = models.CharField(max_length=10, choices=MARKET_CHOICES, verbose_name="시장 종류")
    is_active = models.BooleanField(default=True, verbose_name="AI 모니터링 활성화 여부")
    created_at = models.DateTimeField(auto_now_add=True)
    is_major_index = models.BooleanField(default=False, verbose_name="코스피200/코스닥150 편입 여부")
    # KRX CSV의 '상장시가총액' 원본 값을 그대로 저장 (정렬/랭킹 용도, 단위는 KRX 원본 표기 기준)
    market_cap = models.BigIntegerField(null=True, blank=True, verbose_name="상장시가총액")

    class Meta:
        verbose_name = "종목 (StockItem)"
        verbose_name_plural = "종목 마스터 (StockItem)"

    def __str__(self):
        return f"[{self.ticker}] {self.name}"


# ==========================================
# 1-0. 한국투자증권(KIS) Open API 접근 토큰 캐시
# KIS 정책상 접근 토큰은 1일 1회 발급이 원칙이라(잦은 재발급 시 이용 제한),
# 발급받은 토큰을 DB에 캐싱해두고 만료 임박 전까지 재사용합니다.
# ==========================================
class KisAccessToken(models.Model):
    access_token = models.TextField(verbose_name="접근 토큰")
    expires_at = models.DateTimeField(verbose_name="만료 시각")
    issued_at = models.DateTimeField(auto_now_add=True, verbose_name="발급 시각")

    class Meta:
        verbose_name = "KIS 접근 토큰 (KisAccessToken)"
        verbose_name_plural = "KIS 접근 토큰 (KisAccessToken)"

    def __str__(self):
        return f"KIS 토큰 (만료: {self.expires_at})"


# ==========================================
# 1-0-1. 한국투자증권(KIS) 등락률 순위 API로 받아온 특징종목 캐시
# (대시보드 조회마다 KIS API를 직접 부르지 않도록, collect_fluctuation_ranking 명령이
#  주기적으로 갱신해서 저장해두고 대시보드는 이 테이블만 읽음)
# ==========================================
class RankedMover(models.Model):
    RANK_TYPE_CHOICES = [
        ('GAINER', '상승률 상위'),
        ('LOSER', '하락률 상위'),
    ]

    rank_type = models.CharField(max_length=10, choices=RANK_TYPE_CHOICES, verbose_name="구분")
    rank = models.PositiveSmallIntegerField(verbose_name="순위")
    ticker = models.CharField(max_length=10, verbose_name="종목코드")
    name = models.CharField(max_length=50, verbose_name="종목명")
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="현재가")
    change_pct = models.FloatField(verbose_name="전일 대비율(%)")
    change_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name="전일 대비 금액(원, 부호 포함)"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="갱신 시각")

    class Meta:
        unique_together = ('rank_type', 'rank')
        ordering = ['rank_type', 'rank']
        verbose_name = "급등락 순위 (RankedMover)"
        verbose_name_plural = "급등락 순위 (RankedMover)"

    def __str__(self):
        return f"[{self.get_rank_type_display()} {self.rank}위] {self.name} ({self.change_pct}%)"


# ==========================================
# 1-0-1-0-1. 해외지수/국제환율/금리 캐시 (헤더 지수 티커용, collect_global_market_data가
#  5분 주기로 갱신)
# ==========================================
class GlobalMarketQuote(models.Model):
    CATEGORY_CHOICES = [
        ('FOREIGN_INDEX', '해외지수'),
        ('FX_RATE', '국제 시장 환율'),
        ('FX_FIXING', '환전 고시 환율'),
        ('INTEREST_RATE', '금리'),
    ]

    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, verbose_name="구분")
    code = models.CharField(max_length=20, verbose_name="코드")
    name = models.CharField(max_length=50, verbose_name="이름")
    price = models.DecimalField(max_digits=14, decimal_places=4, verbose_name="현재가")
    change_pct = models.FloatField(verbose_name="전일 대비율(%)")
    order = models.PositiveIntegerField(default=0, verbose_name="정렬 순서")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="갱신 시각")

    class Meta:
        unique_together = ('category', 'code')
        ordering = ['category', 'order']
        verbose_name = "해외지수/환율/금리 (GlobalMarketQuote)"
        verbose_name_plural = "해외지수/환율/금리 (GlobalMarketQuote)"

    def __str__(self):
        return f"[{self.get_category_display()}] {self.name} ({self.price})"


class ExchangeRateSnapshot(models.Model):
    """한국수출입은행 OpenAPI(환전 고시 환율)의 일별 매매기준율 이력. 이 API는 당일 값만 주고
    전일대비를 안 내려줘서, collect_exchange_rate_fixing이 날짜별로 하나씩 쌓아두고 그 직전
    영업일 값과 비교해 change_pct를 직접 계산한 뒤 GlobalMarketQuote(category=FX_FIXING)로
    올린다."""
    currency_code = models.CharField(max_length=20, verbose_name="통화 코드")
    currency_name = models.CharField(max_length=50, verbose_name="통화명")
    date = models.DateField(verbose_name="고시 일자")
    deal_bas_r = models.DecimalField(max_digits=14, decimal_places=4, verbose_name="매매기준율")

    class Meta:
        unique_together = ('currency_code', 'date')
        ordering = ['currency_code', '-date']
        verbose_name = "환전 고시 환율 이력 (ExchangeRateSnapshot)"
        verbose_name_plural = "환전 고시 환율 이력 (ExchangeRateSnapshot)"

    def __str__(self):
        return f"{self.currency_code} {self.date} = {self.deal_bas_r}"


# ==========================================
# 1-0-1-1. 한국투자증권(KIS) 주식현재가 시세 캐시
# 종목 상세 페이지를 열 때마다 KIS API를 직접 호출하면 트래픽이 늘었을 때 호출 제한에
# 걸리기 쉬워서, collect_stock_realtime_price 명령이 주기적으로 갱신해 저장해두고
# 종목 상세 페이지는 이 테이블만 읽는다 (RankedMover와 동일한 캐싱 패턴).
# ==========================================
class StockRealtimePrice(models.Model):
    stock = models.OneToOneField(StockItem, on_delete=models.CASCADE, related_name="realtime_price", verbose_name="종목")
    close_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="현재가")
    open_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="시가")
    high_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="고가")
    low_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="저가")
    change = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="전일 대비")
    change_pct = models.FloatField(verbose_name="전일 대비율(%)")
    volume = models.BigIntegerField(verbose_name="누적 거래량")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="갱신 시각")

    class Meta:
        verbose_name = "실시간 시세 (StockRealtimePrice)"
        verbose_name_plural = "실시간 시세 (StockRealtimePrice)"

    def __str__(self):
        return f"{self.stock.name} 현재가 {self.close_price} ({self.updated_at})"


# ==========================================
# 1-0-2. 한국투자증권(KIS) 국내휴장일조회 캐시
# 문서상 "1일 1회 호출" 권장 API라, 응답으로 한 번에 받아오는 여러 날짜치를 모두 캐싱해두고
# 이후에는 이 테이블만 조회해서 개장일 여부를 판단합니다.
# ==========================================
class MarketHoliday(models.Model):
    date = models.DateField(unique=True, verbose_name="기준일자")
    is_business_day = models.BooleanField(verbose_name="영업일 여부")  # bzdy_yn
    is_trading_day = models.BooleanField(verbose_name="거래일 여부")  # tr_day_yn
    is_market_open = models.BooleanField(verbose_name="개장일 여부")  # opnd_yn
    is_settlement_day = models.BooleanField(verbose_name="결제일 여부")  # sttl_day_yn

    class Meta:
        ordering = ['date']
        verbose_name = "휴장일 (MarketHoliday)"
        verbose_name_plural = "휴장일 (MarketHoliday)"

    def __str__(self):
        status = "개장" if self.is_market_open else "휴장"
        return f"{self.date} ({status})"


# ==========================================
# 1-1. 코스피/코스닥 지수 자체의 일별 시계열 (대시보드 지수 차트용)
# ==========================================
class MarketIndex(models.Model):
    MARKET_CHOICES = [
        ('KOSPI', '코스피'),
        ('KOSDAQ', '코스닥'),
    ]

    market_type = models.CharField(max_length=10, choices=MARKET_CHOICES, verbose_name="시장 종류")
    date = models.DateField(verbose_name="날짜")
    open_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="지수 시가")
    high_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="지수 고가")
    low_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="지수 저가")
    close_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="지수 종가")
    change = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="전일 대비 등락")
    change_pct = models.FloatField(null=True, blank=True, verbose_name="전일 대비 등락률(%)")
    volume = models.BigIntegerField(null=True, blank=True, verbose_name="거래량")
    foreign_net_qty = models.BigIntegerField(null=True, blank=True, verbose_name="외국인 순매수 수량")
    institution_net_qty = models.BigIntegerField(null=True, blank=True, verbose_name="기관계 순매수 수량")
    retail_net_qty = models.BigIntegerField(null=True, blank=True, verbose_name="개인 순매수 수량")
    # KIS 투자자매매동향 API의 *_ntby_tr_pbmn 필드는 백만원 단위라, 100으로 나누면 억원 단위가
    # 된다(실측 검증: 원시값 818662 → 818,662,000,000원 → 8,186.62억원, 화면 표기 "8,186억"과 일치).
    foreign_net_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="외국인 순매수 금액(억원)")
    institution_net_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="기관계 순매수 금액(억원)")
    retail_net_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="개인 순매수 금액(억원)")

    class Meta:
        unique_together = ('market_type', 'date')
        ordering = ['-date']
        verbose_name = "시장 지수 (MarketIndex)"
        verbose_name_plural = "시장 지수 (MarketIndex)"

    def __str__(self):
        return f"{self.get_market_type_display()} {self.date} ({self.close_price})"


# ==========================================
# 2-1. 일봉 가격(실제 OHLCV) 테이블
# ==========================================
class StockDailyPrice(models.Model):
    """collect_stock_data가 야후 파이낸스에서 받아온 실제 일봉(OHLCV)만 담는 테이블.
    이전엔 StockPrediction 한 테이블에 이 실가격과 AI 예측값이 같이 있었는데, 전체 90만+ 행 중
    예측값이 채워진 행은 0.1%도 안 돼(대부분 그냥 가격 이력) 이름과 실제 용도가 어긋나 혼란을
    줬다. 그래서 실가격/예측을 테이블로 분리한다."""
    stock = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="daily_prices", verbose_name="종목")
    date = models.DateField(verbose_name="날짜")  # YYYY-MM-DD

    open_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="시가")
    high_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="고가")
    low_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="저가")
    close_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="종가")
    volume = models.BigIntegerField(verbose_name="거래량")
    trading_amount = models.BigIntegerField(null=True, blank=True, verbose_name="거래금액")

    class Meta:
        # 한 종목에 대해 하루에 하나의 일봉만 쌓이도록 고유값 설정
        unique_together = ('stock', 'date')
        ordering = ['-date']
        # 어드민 목록 정렬(-date, stock, -id: Django 어드민이 안정적 페이지네이션을 위해 pk를 자동으로 덧붙임)과
        # 완전히 일치하는 인덱스를 걸어야 대량 데이터에서도 파일소트 없이 조회됨
        indexes = [
            models.Index(fields=['-date', 'stock', '-id'], name='dailyprice_date_stock_idx'),
        ]
        verbose_name = "일별 시세 (StockDailyPrice)"
        verbose_name_plural = "일별 시세 (StockDailyPrice)"

    def __str__(self):
        return f"{self.stock.name} {self.date} 종가 {self.close_price}"


# ==========================================
# 2-2. AI 주가 예측 결과 테이블
# ==========================================
class StockPrediction(models.Model):
    """run_stock_prediction이 산출한 예측값만 담는다. 실가격은 StockDailyPrice를 본다
    (기준일 종가는 StockDailyPrice.objects.get(stock=.., date=이 행의 date).close_price)."""
    stock = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="predictions", verbose_name="종목")
    date = models.DateField(verbose_name="예측 기준일(이 날짜 종가까지의 데이터로 다음날을 예측)")

    pred_next_close = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="AI 내일 예상종가")
    pred_5day_return = models.FloatField(null=True, blank=True, verbose_name="AI 향후 5일 예상수익률")

    up_probability = models.FloatField(null=True, blank=True, verbose_name="상승 확률 (0.0~1.0)")
    down_probability = models.FloatField(null=True, blank=True, verbose_name="하락 확률 (0.0~1.0)")

    SIGNAL_CHOICES = [
        ('BUY', '매수'),
        ('SELL', '매도'),
        ('HOLD', '관망'),
    ]
    trading_signal = models.CharField(max_length=5, choices=SIGNAL_CHOICES, default='HOLD', verbose_name="매매 신호")

    class Meta:
        # 한 종목에 대해 하루에 하나의 예측만 쌓이도록 고유값 설정
        unique_together = ('stock', 'date')
        ordering = ['-date']
        indexes = [
            models.Index(fields=['-date', 'stock', '-id'], name='stockpred_date_stock_idx'),
        ]
        verbose_name = "AI 예측 (StockPrediction)"
        verbose_name_plural = "AI 예측 (StockPrediction)"

    def __str__(self):
        return f"{self.stock.name} - {self.date} 예측"
