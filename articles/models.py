from django.db import models
from django.contrib.auth.models import User

class AnalyzedArticle(models.Model):
    # 1. 원본 뉴스 정보
    title = models.CharField(max_length=255, verbose_name="원본 제목")
    original_url = models.URLField(unique=True, verbose_name="원본 기사 링크")
    source_media = models.CharField(max_length=50, verbose_name="언론사")
    scraped_at = models.DateTimeField(auto_now_add=True, verbose_name="수집 일시")

    # 2. AI 에이전트 재가공 데이터 (텍스트 분석 결과)
    ai_summary = models.TextField(verbose_name="AI 3줄 요약")
    ai_analysis = models.TextField(verbose_name="AI 투자 관점 분석")
    blog_content = models.TextField(verbose_name="블로그 포스팅용 원고")

    # 3. 비즈니스 모델(BM) 및 자동화 관리 트리거
    is_premium = models.BooleanField(default=False, verbose_name="유료 구독자 전용 여부")
    is_posted = models.BooleanField(default=False, verbose_name="블로그 자동 발행 완료 여부")

    def __str__(self):
        return f"[{self.source_media}] {self.title}"


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
    updated_at = models.DateTimeField(auto_now=True, verbose_name="갱신 시각")

    class Meta:
        unique_together = ('rank_type', 'rank')
        ordering = ['rank_type', 'rank']

    def __str__(self):
        return f"[{self.get_rank_type_display()} {self.rank}위] {self.name} ({self.change_pct}%)"


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

    class Meta:
        unique_together = ('market_type', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"{self.get_market_type_display()} {self.date} ({self.close_price})"


# ==========================================
# 2. 일봉 가격 및 AI 주가 예측 결과 테이블
# ==========================================
class StockPrediction(models.Model):
    stock = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="predictions", verbose_name="종목")
    date = models.DateField(verbose_name="날짜") # YYYY-MM-DD

    # 기초 훈련 데이터 (10년치 일봉 수집용)
    open_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="시가")
    high_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="고가")
    low_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="저가")
    close_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="종가")
    volume = models.BigIntegerField(verbose_name="거래량")
    trading_amount = models.BigIntegerField(null=True, blank=True, verbose_name="거래금액")

    # ML/AI 분석 결과 데이터 (내일 및 5일 예측값)
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
        # 한 종목에 대해 하루에 하나의 예측/가격 데이터만 쌓이도록 고유값 설정
        unique_together = ('stock', 'date')
        ordering = ['-date']
        # 어드민 목록 정렬(-date, stock, -id: Django 어드민이 안정적 페이지네이션을 위해 pk를 자동으로 덧붙임)과
        # 완전히 일치하는 인덱스를 걸어야 대량 데이터에서도 파일소트 없이 조회됨
        indexes = [
            models.Index(fields=['-date', 'stock', '-id'], name='stockpred_date_stock_idx'),
        ]

    def __str__(self):
        return f"{self.stock.name} - {self.date} 예측"


# ==========================================
# 3-0. 키워드 기반 뉴스 수집 - RSS 소스 및 감지 키워드 관리
# ==========================================
class NewsSource(models.Model):
    name = models.CharField(max_length=50, verbose_name="언론사/매체명")
    rss_url = models.URLField(unique=True, verbose_name="RSS 피드 주소")
    is_active = models.BooleanField(default=True, verbose_name="수집 활성화 여부")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class NewsKeyword(models.Model):
    keyword = models.CharField(max_length=50, unique=True, verbose_name="감지 키워드")
    # 키워드를 특정 종목과 연결해두면, 매칭된 기사가 해당 종목 뉴스로도 분류됨 (선택 사항)
    linked_stock = models.ForeignKey(
        StockItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="news_keywords", verbose_name="연결된 종목(선택)"
    )
    is_active = models.BooleanField(default=True, verbose_name="수집 활성화 여부")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.keyword


# ==========================================
# 3. 증권 뉴스 및 AI 에이전트 가공 테이블
# ==========================================
class AnalyzedArticle(models.Model):
    # 특정 종목 키워드와 연동 (키워드 매칭이 안 되면 공백 가능)
    stock = models.ForeignKey(StockItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="news", verbose_name="관련 종목")
    # 키워드 기반 수집(collect_keyword_news)으로 들어온 경우, 매칭된 키워드 기록
    matched_keyword = models.ForeignKey(
        NewsKeyword, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="articles", verbose_name="매칭된 키워드"
    )

    # 원본 뉴스 메타 데이터
    title = models.CharField(max_length=255, verbose_name="원본 제목")
    original_url = models.URLField(unique=True, verbose_name="원본 기사 링크")
    source_media = models.CharField(max_length=50, verbose_name="언론사")
    scraped_at = models.DateTimeField(auto_now_add=True, verbose_name="수집 일시")

    # AI 에이전트 텍스트 분석 및 블로그 원고 데이터
    ai_summary = models.TextField(verbose_name="AI 3줄 요약")
    ai_analysis = models.TextField(verbose_name="AI 투자 관점 분석")
    blog_content = models.TextField(verbose_name="블로그/티스토리 포스팅용 원고")

    TEMPLATE_CHOICES = [
        ('T1', '템플릿 1 (뉴스 요약형)'),
        ('T2', '템플릿 2 (종목 분석형)'),
        ('T3', '템플릿 3 (카드뉴스 대본형)'),
    ]
    applied_template = models.CharField(max_length=5, choices=TEMPLATE_CHOICES, default='T1', verbose_name="적용된 템플릿")

    # 시스템 관리용 트리거
    is_premium = models.BooleanField(default=False, verbose_name="유료 회원 전용 콘텐츠")
    is_posted = models.BooleanField(default=False, verbose_name="블로그 자동 발행 완료")

    def __str__(self):
        return f"[{self.source_media}] {self.title}"


# ==========================================
# 4. 유저 구독 정보 테이블 (월 1만원 비즈니스 모델용)
# ==========================================
class UserSubscription(models.Model):
    # 장고 내장 기본 유저 모델과 1:1 매칭
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="subscription")

    # 구독 상태 관리
    is_active_premium = models.BooleanField(default=False, verbose_name="프리미엄 구독 활성화")
    subscribed_at = models.DateTimeField(null=True, blank=True, verbose_name="구독 시작일")
    expired_at = models.DateTimeField(null=True, blank=True, verbose_name="구독 만료일")

    def __str__(self):
        status = "유료회원" if self.is_active_premium else "일반회원"
        return f"{self.user.username} ({status})"


# ==========================================
# 5. 소셜 로그인(카카오/구글) 연동 계정 테이블
# ==========================================
class SocialAccount(models.Model):
    PROVIDER_CHOICES = [
        ('KAKAO', '카카오톡'),
        ('GOOGLE', '구글'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="social_accounts", verbose_name="연결된 유저")
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES, verbose_name="가입 경로")
    provider_uid = models.CharField(max_length=191, verbose_name="소셜 서비스 고유 ID")
    email = models.EmailField(blank=True, null=True, verbose_name="소셜 계정 이메일")
    connected_at = models.DateTimeField(auto_now_add=True, verbose_name="연동 일시")

    class Meta:
        unique_together = ('provider', 'provider_uid')

    def __str__(self):
        return f"{self.user.username} - {self.get_provider_display()}"

