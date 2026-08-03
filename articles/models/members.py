from django.db import models
from django.contrib.auth.models import User

from articles.fields import EncryptedCharField


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

    class Meta:
        verbose_name = "프리미엄 구독 (UserSubscription)"
        verbose_name_plural = "프리미엄 구독 (UserSubscription)"

    def __str__(self):
        status = "유료회원" if self.is_active_premium else "일반회원"
        return f"{self.user.username} ({status})"


# ==========================================
# 5. 회원 권한 등급 (Admin 화면에서 자유롭게 생성·수정·삭제하는 등급 체계)
# ==========================================
class MemberGrade(models.Model):
    name = models.CharField(max_length=50, unique=True, verbose_name="등급명")
    level = models.PositiveSmallIntegerField(unique=True, verbose_name="등급 순위(숫자가 클수록 상위 등급)")
    description = models.CharField(max_length=255, blank=True, verbose_name="설명")
    # 비워두면(NULL) 무제한. 관리자 등급은 두 값 모두 비워서 무제한으로 둔다.
    daily_scrape_limit = models.PositiveIntegerField(null=True, blank=True, verbose_name="일일 스크래핑 가능 건수(공란=무제한)")
    daily_post_limit = models.PositiveIntegerField(null=True, blank=True, verbose_name="일일 포스팅 가능 건수(공란=무제한)")
    daily_ai_summarize_limit = models.PositiveIntegerField(null=True, blank=True, verbose_name="일일 AI 요약 가능 건수(공란=무제한)")

    class Meta:
        ordering = ['level']
        verbose_name = "회원 등급 (MemberGrade)"
        verbose_name_plural = "회원 등급 (MemberGrade)"

    def __str__(self):
        return f"{self.level}. {self.name}"

    @classmethod
    def default_grade(cls):
        """신규 회원에게 자동 부여할 기본 등급(가장 낮은 level). 등급이 하나도 없으면 None."""
        return cls.objects.order_by('level').first()


# ==========================================
# 6. 마이페이지 - 뉴스 구독 / 자동 포스팅 설정 테이블
# ==========================================
class UserPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="preference", verbose_name="사용자")
    phone_number = models.CharField(max_length=20, blank=True, verbose_name="전화번호")
    # UserSubscription(무료/프리미엄 결제 상태)과는 별개의 권한 체계. 회원 목록(Admin User 화면)에서
    # 콤보박스로 하나만 골라 부여하며, 등급 자체는 MemberGrade 화면에서 자유롭게 추가/수정/삭제한다.
    grade = models.ForeignKey(
        MemberGrade, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="members", verbose_name="권한 등급",
    )

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

    class Meta:
        verbose_name = "회원 환경설정 (UserPreference)"
        verbose_name_plural = "회원 환경설정 (UserPreference)"

    def __str__(self):
        return f"{self.user.username} 환경설정"


class BlogPostingAccount(models.Model):
    PLATFORM_CHOICES = [
        ('WORDPRESS', '워드프레스'),
        ('BLOGGER', '블로거(Blogger)'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="posting_accounts", verbose_name="사용자")
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES, verbose_name="포스팅 플랫폼")
    is_enabled = models.BooleanField(default=False, verbose_name="이 플랫폼으로 자동 포스팅 사용")
    # 자기 호스팅 워드프레스는 사이트마다 REST API 엔드포인트가 다르므로 필요
    # 블로거는 연동된 블로그의 URL을 OAuth 연동 시 자동으로 채워넣음(사용자 직접 입력 아님)
    site_url = models.URLField(blank=True, verbose_name="사이트 주소(워드프레스/블로거)")
    # 워드프레스는 계정 ID+PW 방식
    # 블로거는 OAuth 연동이라 account_id에 블로그 ID를 자동으로 채워넣음(사용자 직접 입력 아님)
    account_id = models.CharField(max_length=150, blank=True, verbose_name="계정 ID / 블로그 ID")
    # 블로거는 비밀번호가 아니라 구글 OAuth 리프레시 토큰을 저장(구글 로그인 연동 시 자동으로 채워넣음)
    # DB에는 Fernet으로 암호화되어 저장되고(articles/fields.py), 파이썬 쪽에는 평문으로 노출된다.
    # 암호화 오버헤드 때문에 실제 저장 길이가 평문보다 길어져 max_length를 넉넉히 잡았다.
    credential = EncryptedCharField(max_length=1024, blank=True, verbose_name="비밀번호 / API Key / OAuth 리프레시 토큰")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="수정 일시")

    class Meta:
        unique_together = ('user', 'platform')
        ordering = ['user', 'platform']
        verbose_name = "블로그 발행 계정 (BlogPostingAccount)"
        verbose_name_plural = "블로그 발행 계정 (BlogPostingAccount)"

    def __str__(self):
        return f"{self.user.username} - {self.get_platform_display()}"

    def is_connected(self):
        """마이페이지 방문 시 모든 플랫폼에 빈 stub 행이 자동 생성되므로(get_or_create),
        실제로 발행에 쓸 수 있는 계정인지(자격 정보가 채워졌는지)는 따로 확인해야 한다."""
        if self.platform in ('WORDPRESS', 'BLOGGER'):
            return bool(self.site_url and self.account_id and self.credential)
        return False


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
        verbose_name = "로그인 기록 (LoginLog)"
        verbose_name_plural = "로그인 기록 (LoginLog)"

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
        verbose_name = "메뉴 접속 기록 (MenuAccessLog)"
        verbose_name_plural = "메뉴 접속 기록 (MenuAccessLog)"

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
        verbose_name = "챗봇 대화 (ChatMessage)"
        verbose_name_plural = "챗봇 대화 (ChatMessage)"

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
        verbose_name = "소셜 로그인 계정 (SocialAccount)"
        verbose_name_plural = "소셜 로그인 계정 (SocialAccount)"

    def __str__(self):
        return f"{self.user.username} - {self.get_provider_display()}"
