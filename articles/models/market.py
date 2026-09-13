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
    # 밸류에이션 지표(종목 상세 페이지 표시용). KIS 주식현재가 조회 API가 실시간가와 함께 내려주는
    # 값이라 collect_stock_realtime_price가 5분 주기로 갱신할 때 추가 API 호출 없이 같이 채운다.
    # null=True: 이 필드가 생기기 전에 만들어진 기존 행, 그리고 아직 한 번도 갱신 안 된 신규 행 대비.
    per = models.FloatField(null=True, blank=True, verbose_name="PER")
    pbr = models.FloatField(null=True, blank=True, verbose_name="PBR")
    eps = models.FloatField(null=True, blank=True, verbose_name="EPS")
    bps = models.FloatField(null=True, blank=True, verbose_name="BPS")
    market_cap = models.BigIntegerField(null=True, blank=True, verbose_name="시가총액(원)")
    week52_high = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="52주 최고가")
    week52_low = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="52주 최저가")
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


class StockInvestorFlow(models.Model):
    """collect_investor_flow가 KIS 종목별 투자자매매동향(일별) API로 채우는, 종목×날짜별
    투자자 주체별 순매수 수량/금액. run_stock_prediction의 피처로 쓰기 위한 수급 데이터다
    (사용자 요청: 국민연금/기관/외국인이 얼마나 사고파는지도 예측에 반영). 금액은 KIS 원본
    단위인 백만원 그대로 저장한다(변환 없음). "기금(pension)"이 국민연금 등 연기금류가
    잡히는 가장 가까운 KIS 분류다 — KIS가 국민연금만 따로 떼어 주지 않는다."""
    stock = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="investor_flows", verbose_name="종목")
    date = models.DateField(verbose_name="날짜")

    foreign_net_qty = models.BigIntegerField(verbose_name="외국인 순매수 수량")
    foreign_net_amount = models.BigIntegerField(verbose_name="외국인 순매수 금액(백만원)")
    retail_net_qty = models.BigIntegerField(verbose_name="개인 순매수 수량")
    retail_net_amount = models.BigIntegerField(verbose_name="개인 순매수 금액(백만원)")
    institution_net_qty = models.BigIntegerField(verbose_name="기관계 순매수 수량")
    institution_net_amount = models.BigIntegerField(verbose_name="기관계 순매수 금액(백만원)")
    pension_net_qty = models.BigIntegerField(verbose_name="기금(연기금류) 순매수 수량")
    pension_net_amount = models.BigIntegerField(verbose_name="기금(연기금류) 순매수 금액(백만원)")
    trust_net_qty = models.BigIntegerField(verbose_name="투자신탁 순매수 수량")
    trust_net_amount = models.BigIntegerField(verbose_name="투자신탁 순매수 금액(백만원)")
    pe_fund_net_qty = models.BigIntegerField(verbose_name="사모펀드 순매수 수량")
    pe_fund_net_amount = models.BigIntegerField(verbose_name="사모펀드 순매수 금액(백만원)")
    securities_net_qty = models.BigIntegerField(verbose_name="증권 순매수 수량")
    securities_net_amount = models.BigIntegerField(verbose_name="증권 순매수 금액(백만원)")

    class Meta:
        unique_together = ('stock', 'date')
        ordering = ['-date']
        indexes = [
            models.Index(fields=['-date', 'stock', '-id'], name='investorflow_date_stock_idx'),
        ]
        verbose_name = "종목별 투자자 수급 (StockInvestorFlow)"
        verbose_name_plural = "종목별 투자자 수급 (StockInvestorFlow)"

    def __str__(self):
        return f"{self.stock.name} {self.date} 외국인 {self.foreign_net_qty:+,}주"


class StockDisclosure(models.Model):
    """collect_dart_disclosures가 DART(전자공시시스템) Open API list.json으로 채우는 종목별
    공시 원본 메타데이터. corp_code 없이 날짜+공시유형만으로 시장 전체를 조회하면 응답에
    stock_code가 바로 포함돼 있어, 종목별로 순회할 필요 없이 하루 단위로 한 번에 수집한다
    (kis_client의 종목별 순회 방식과 다름 — investor_flow보다 훨씬 가벼운 호출 구조).
    rcept_no(접수번호)가 DART 쪽 공시 고유 식별자라 이걸로 dedup한다. AI 요약/호재·악재
    분류는 이 모델에 넣지 않고(원자재 원칙: 수집과 AI 분석 분리 — articles_ai.py 참고),
    필요해지면 이 모델을 참조하는 별도 단계에서 처리한다."""
    PBLNTF_TYPE_CHOICES = [
        ('A', '정기공시'), ('B', '주요사항보고'), ('C', '발행공시'), ('D', '지분공시'),
        ('E', '기타공시'), ('F', '외부감사관련'), ('G', '펀드공시'), ('H', '자산유동화'),
        ('I', '거래소공시'), ('J', '공정위공시'),
    ]

    stock = models.ForeignKey(
        StockItem, on_delete=models.CASCADE, null=True, blank=True,
        related_name="disclosures", verbose_name="종목",
        help_text="종목코드가 StockItem과 매칭되는 경우만 연결 (미상장/코넥스 등은 NULL)",
    )
    corp_name = models.CharField(max_length=100, verbose_name="회사명")
    stock_code = models.CharField(max_length=6, blank=True, verbose_name="종목코드")
    corp_cls = models.CharField(max_length=1, blank=True, verbose_name="법인구분(Y/K/N/E)")
    report_nm = models.CharField(max_length=500, verbose_name="보고서명")
    rcept_no = models.CharField(max_length=14, unique=True, verbose_name="접수번호")
    rcept_dt = models.DateField(verbose_name="접수일자")
    pblntf_ty = models.CharField(max_length=1, choices=PBLNTF_TYPE_CHOICES, verbose_name="공시유형")
    pblntf_detail_ty = models.CharField(max_length=10, blank=True, verbose_name="공시상세유형")
    flr_nm = models.CharField(max_length=100, blank=True, verbose_name="제출인명")
    rm = models.CharField(max_length=20, blank=True, verbose_name="비고")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-rcept_dt', '-rcept_no']
        indexes = [
            models.Index(fields=['-rcept_dt', 'stock'], name='disclosure_date_stock_idx'),
        ]
        verbose_name = "종목 공시 (StockDisclosure)"
        verbose_name_plural = "종목 공시 (StockDisclosure)"

    def __str__(self):
        return f"{self.rcept_dt} {self.corp_name} {self.report_nm}"

    @property
    def viewer_url(self):
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.rcept_no}"


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
    # 홀드아웃 검증 정확도(방향성). run_stock_prediction 로그에만 있던 값을 필드로도 남겨,
    # 아래 *_flow 필드와 나중에 집계 비교(어느 쪽이 실제로 더 나았는지)할 수 있게 한다.
    holdout_accuracy = models.FloatField(null=True, blank=True, verbose_name="검증 정확도(기본 피처)")

    # 위 5개 필드(pred_next_close~holdout_accuracy)는 기존 피처(가격/기술지표)만으로 학습한
    # 기본 모델 결과다. 아래는 여기에 종목별 수급 피처(StockInvestorFlow 기반 외국인/기관/기금
    # 순매수 비율)를 추가로 넣어 학습한 "수급 반영" 모델 결과 — 같은 행에 나란히 저장해 사용자가
    # 요청한 대로 "피처 추가 전/후"를 필드 단위로 바로 비교할 수 있게 한다. 수급 데이터가
    # 1년치뿐이라 학습 표본이 부족한 종목은 널로 남는다.
    pred_next_close_flow = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="AI 내일 예상종가(수급 반영)")
    pred_5day_return_flow = models.FloatField(null=True, blank=True, verbose_name="AI 향후 5일 예상수익률(수급 반영)")
    up_probability_flow = models.FloatField(null=True, blank=True, verbose_name="상승 확률(수급 반영)")
    down_probability_flow = models.FloatField(null=True, blank=True, verbose_name="하락 확률(수급 반영)")
    trading_signal_flow = models.CharField(max_length=5, choices=SIGNAL_CHOICES, null=True, blank=True, verbose_name="매매 신호(수급 반영)")
    holdout_accuracy_flow = models.FloatField(null=True, blank=True, verbose_name="검증 정확도(수급 반영)")

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


# ==========================================
# AI 예측 성과 트랙레코드 페이지용 스냅샷. 신뢰도(확신도) 구간별 실현 정확도를 매일 배치로
# 미리 계산해 저장한다 — StockPrediction 전체 이력(만 건 이상)과 StockDailyPrice를 조인해
# "다음날 실제로 올랐는지"를 계산하는 작업이 실측 약 6초 걸려(2026-08-09 기준, 전종목 대상
# StockDailyPrice 조인), 공개 페이지 요청마다 라이브로 돌리기엔 무겁다. 반면 이미 발동된
# 매수/매도 시그널만 다시 결과 확인하는 건 표본이 훨씬 작아(수백 건) 라이브 계산해도 가벼워서,
# 그쪽은 이 스냅샷 없이 ai_performance_view가 매 요청마다 직접 계산한다.
# ==========================================
class PredictionAccuracySnapshot(models.Model):
    computed_at = models.DateTimeField(auto_now_add=True, verbose_name="계산 시각")
    total_resolved = models.IntegerField(verbose_name="결과 확정된 예측 수(신뢰도 무관 전체)")
    overall_accuracy = models.FloatField(verbose_name="전체 방향성 정확도(신뢰도 무관)")
    # [{'threshold': 0.55, 'confidence': 0.10, 'n': 8597, 'accuracy': 0.558}, ...] 형태.
    # 신뢰도 구간별 누적(threshold 이상) 표본수/정확도 — compute_prediction_accuracy 커맨드가
    # 채우는 필드/구조와 반드시 맞춰야 한다.
    buckets = models.JSONField(verbose_name="신뢰도 구간별 누적 정확도")

    class Meta:
        ordering = ['-computed_at']
        verbose_name = "AI 예측 정확도 스냅샷 (PredictionAccuracySnapshot)"
        verbose_name_plural = "AI 예측 정확도 스냅샷 (PredictionAccuracySnapshot)"

    def __str__(self):
        return f"{self.computed_at:%Y-%m-%d %H:%M} 기준 (표본 {self.total_resolved}건)"
