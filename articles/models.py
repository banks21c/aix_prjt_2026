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
# 6. 마이페이지 - 뉴스 구독 / 자동 포스팅 설정 테이블
# ==========================================
class UserPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="preference", verbose_name="사용자")
    phone_number = models.CharField(max_length=20, blank=True, verbose_name="전화번호")

    # 이메일 인증 (회원가입/마이페이지에서 직접 입력·수정한 이메일만 대상. 카카오/구글 소셜 로그인으로
    # 자동 채워지는 이메일은 해당 없음). 인증 전에는 User.email을 바로 바꾸지 않고 pending_email에 보관한다.
    is_email_verified = models.BooleanField(default=False, verbose_name="이메일 인증 여부")
    pending_email = models.CharField(max_length=254, blank=True, verbose_name="인증 대기 중인 이메일")
    email_verification_token = models.CharField(max_length=64, blank=True, verbose_name="이메일 인증 토큰")
    email_verification_sent_at = models.DateTimeField(null=True, blank=True, verbose_name="인증 메일 발송 시각")

    news_subscription = models.BooleanField(default=False, verbose_name="뉴스 구독 여부")
    interested_keywords = models.CharField(max_length=255, blank=True, verbose_name="관심 키워드(콤마로 구분)")
    # 체크 시 관심 키워드 필터를 무시하고 모든 미발행 기사를 발행 대상으로 삼음
    post_all_articles = models.BooleanField(default=False, verbose_name="전체 기사 발행(관심 키워드 무시)")
    auto_posting_enabled = models.BooleanField(default=False, verbose_name="자동 포스팅 사용 여부")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="수정 일시")

    def __str__(self):
        return f"{self.user.username} 환경설정"


class BlogPostingAccount(models.Model):
    PLATFORM_CHOICES = [
        ('WORDPRESS', '워드프레스'),
        ('TISTORY', '티스토리'),
        ('NAVER', '네이버 블로그'),
        ('BLOGGER', '블로거(Blogger)'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="posting_accounts", verbose_name="사용자")
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES, verbose_name="포스팅 플랫폼")
    is_enabled = models.BooleanField(default=False, verbose_name="이 플랫폼으로 자동 포스팅 사용")
    # 자기 호스팅 워드프레스는 사이트마다 REST API 엔드포인트가 다르므로 필요 (티스토리/네이버는 미사용, blank)
    # 블로거는 연동된 블로그의 URL을 OAuth 연동 시 자동으로 채워넣음(사용자 직접 입력 아님)
    site_url = models.URLField(blank=True, verbose_name="사이트 주소(워드프레스/블로거)")
    # 워드프레스/네이버 블로그는 계정 ID+PW, 티스토리는 API Key(액세스 토큰) 방식이라 하나의 필드로 겸용
    # 블로거는 OAuth 연동이라 account_id에 블로그 ID를 자동으로 채워넣음(사용자 직접 입력 아님)
    account_id = models.CharField(max_length=150, blank=True, verbose_name="계정 ID / 블로그 ID")
    # 블로거는 비밀번호가 아니라 구글 OAuth 리프레시 토큰을 저장(구글 로그인 연동 시 자동으로 채워넣음)
    credential = models.CharField(max_length=255, blank=True, verbose_name="비밀번호 / API Key / OAuth 리프레시 토큰")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="수정 일시")

    class Meta:
        unique_together = ('user', 'platform')
        ordering = ['user', 'platform']

    def __str__(self):
        return f"{self.user.username} - {self.get_platform_display()}"

    def is_connected(self):
        """마이페이지 방문 시 모든 플랫폼에 빈 stub 행이 자동 생성되므로(get_or_create),
        실제로 발행에 쓸 수 있는 계정인지(자격 정보가 채워졌는지)는 따로 확인해야 한다."""
        if self.platform in ('WORDPRESS', 'BLOGGER'):
            return bool(self.site_url and self.account_id and self.credential)
        if self.platform == 'TISTORY':
            return bool(self.account_id and self.credential)
        if self.platform == 'NAVER':
            return bool(self.account_id)
        return False


class PostedArticle(models.Model):
    """어떤 회원의 어떤 등록 계정(사이트)에 어떤 기사가 이미 발행됐는지 추적.

    AnalyzedArticle.is_posted는 단일 글로벌 사이트 기준의 기존 필드라 다중 회원 사이트를
    각각 독립적으로 발행/추적하기엔 맞지 않아, 계정×기사 단위로 별도 기록한다.
    """
    blog_account = models.ForeignKey(BlogPostingAccount, on_delete=models.CASCADE, related_name="posted_articles", verbose_name="발행 계정")
    article = models.ForeignKey(AnalyzedArticle, on_delete=models.CASCADE, related_name="postings", verbose_name="기사")
    external_url = models.URLField(blank=True, verbose_name="발행된 글 주소")
    posted_at = models.DateTimeField(auto_now_add=True, verbose_name="발행 일시")

    class Meta:
        unique_together = ('blog_account', 'article')
        ordering = ['-posted_at']

    def __str__(self):
        return f"{self.blog_account} -> {self.article.title[:30]}"


# ==========================================
# 7. 로그인 로그 / 메뉴 접속 로그 테이블
# ==========================================
class LoginLog(models.Model):
    LOGIN_METHOD_CHOICES = [
        ('GENERAL', '일반 로그인'),
        ('SIGNUP', '회원가입'),
        ('KAKAO', '카카오'),
        ('GOOGLE', '구글'),
        ('NAVER', '네이버'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="login_logs", verbose_name="사용자")
    login_method = models.CharField(max_length=10, choices=LOGIN_METHOD_CHOICES, verbose_name="로그인 방식")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="접속 IP")
    user_agent = models.CharField(max_length=255, blank=True, verbose_name="User-Agent")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="로그인 일시")

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.get_login_method_display()} ({self.created_at})"


class MenuAccessLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="menu_access_logs", verbose_name="사용자")
    menu_name = models.CharField(max_length=100, verbose_name="접속 메뉴(URL name)")
    path = models.CharField(max_length=255, verbose_name="요청 경로")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="접속 IP")
    accessed_at = models.DateTimeField(auto_now_add=True, verbose_name="접속 일시")

    class Meta:
        ordering = ['-accessed_at']
        indexes = [
            models.Index(fields=['user', '-accessed_at']),
            models.Index(fields=['menu_name', '-accessed_at']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.menu_name} ({self.accessed_at})"


# ==========================================
# 7. 주식/경제 챗봇 대화 기록 테이블
# ==========================================
class ChatMessage(models.Model):
    ROLE_CHOICES = [
        ('user', '사용자'),
        ('assistant', '챗봇'),
    ]

    # 로그인 사용자는 user로, 비로그인 사용자는 session_key로 대화를 구분
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True,
        related_name="chat_messages", verbose_name="로그인 사용자(비로그인 시 null)"
    )
    session_key = models.CharField(max_length=40, verbose_name="세션 키(비로그인 사용자 구분용)")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, verbose_name="발화자")
    content = models.TextField(verbose_name="메시지 내용")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="전송 시각")

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['session_key', 'created_at']),
            models.Index(fields=['user', 'created_at']),
        ]

    def __str__(self):
        return f"[{self.get_role_display()}] {self.content[:30]}"


# ==========================================
# 5. 소셜 로그인(카카오/구글/네이버) 연동 계정 테이블
# ==========================================
class SocialAccount(models.Model):
    PROVIDER_CHOICES = [
        ('KAKAO', '카카오톡'),
        ('GOOGLE', '구글'),
        ('NAVER', '네이버'),
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


# ==========================================
# 8. 홈페이지 뉴스레터 구독 (비회원도 이메일만으로 신청 가능)
# ==========================================
class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True, verbose_name="이메일")
    is_active = models.BooleanField(default=True, verbose_name="구독 활성화 여부")
    subscribed_at = models.DateTimeField(auto_now_add=True, verbose_name="구독 신청일")

    class Meta:
        ordering = ['-subscribed_at']

    def __str__(self):
        return self.email


class NewsletterIssue(models.Model):
    """뉴스레터 발행 1회분. generate_newsletter_draft 커맨드가 최근 기사로 초안(DRAFT)을 자동
    작성해두면, 관리자가 Admin 화면에서 제목/본문을 직접 확인·수정한 뒤 상태를 READY로 바꾼다.
    send_newsletter 커맨드는 READY 상태인 건만 골라 발송하고 SENT로 넘긴다."""
    STATUS_CHOICES = [
        ('DRAFT', '초안(검토 대기)'),
        ('READY', '발송 대기(다음 자동발송 때 전송)'),
        ('SENT', '발송 완료'),
    ]

    subject = models.CharField(max_length=255, verbose_name="제목")
    body = models.TextField(verbose_name="본문")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='DRAFT', verbose_name="상태")
    article_count = models.PositiveIntegerField(default=0, verbose_name="초안 작성 시 포함된 기사 수")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="초안 생성일")
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name="발송 일시")
    recipient_count = models.PositiveIntegerField(default=0, verbose_name="발송 성공 인원")

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.get_status_display()}] {self.subject}"

