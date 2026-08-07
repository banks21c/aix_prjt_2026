from urllib.parse import urlencode

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from .models import (
    StockItem, StockDailyPrice, StockPrediction, AnalyzedArticle, UserSubscription, SocialAccount,
    NewsSource, NewsKeyword, MarketIndex, KisAccessToken, MarketHoliday, ChatMessage,
    LoginLog, MenuAccessLog, UserPreference, BlogPostingAccount, PostedArticle,
    StockRealtimePrice, NewsletterSubscriber, NewsletterIssue, Menu, ConsultRequest,
    FinancialConsultSheet, MemberGrade, MediaOutlet, RankedMover, GlobalMarketQuote,
    ExchangeRateSnapshot, SubscriptionOrder,
)

# 이 서버엔 다른 프로젝트(phishcut) admin도 함께 떠 있어서, 기본 "Django administration"
# 문구 대신 어느 프로젝트 관리자 화면인지 바로 알아볼 수 있게 브랜딩한다.
admin.site.site_header = "NextFinUp administration"
admin.site.site_title = "NextFinUp admin"
admin.site.index_title = "NextFinUp 관리"

# /admin/(이 파일에 등록된 시스템 전체 — 파이프라인/ML/회원계정/자격증명 등)은 슈퍼유저 전용으로
# 잠근다. 업무(상담·구독) 담당자는 articles/business_admin.py의 별도 /staff/ 사이트를 쓴다 —
# 거기는 기본 권한 체크(is_staff)만 요구해 스태프면 누구나 들어오지만, is_superuser가 아닌
# 스태프는 여기(/admin/) 로그인 자체가 막힌다. 이미 만들어진 admin.site 싱글턴의 __class__를
# 바꿔치기하는 방식이라(공식 문서에도 나오는 패턴), 아래 @admin.register들은 손댈 필요 없다.
class _SuperuserOnlyAdminSite(admin.AdminSite):
    def has_permission(self, request):
        return super().has_permission(request) and request.user.is_superuser


admin.site.__class__ = _SuperuserOnlyAdminSite


# 0-0-3. 회원 권한 등급 생성/수정/삭제 화면 (5단계로 시작, Admin에서 자유롭게 추가·수정·삭제 가능)
@admin.register(MemberGrade)
class MemberGradeAdmin(admin.ModelAdmin):
    list_display = ('level', 'name', 'daily_scrape_limit', 'daily_post_limit', 'description', 'member_count')
    ordering = ('level',)
    search_fields = ('name',)

    @admin.display(description='보유 회원 수')
    def member_count(self, obj):
        return obj.members.count()

# 0-0-2. 기본 User admin에 마이페이지에서 등록한 전화번호(UserPreference)와 권한 등급 컬럼 추가.
# grade는 ForeignKey라 Django가 자동으로 콤보박스(단일 선택 <select>)로 렌더링한다.
class UserPreferenceInline(admin.StackedInline):
    model = UserPreference
    can_delete = False
    fields = ('phone_number', 'grade')

# User 편집 화면에서 바로 프리미엄 구독 여부를 켜고 끌 수 있도록 UserSubscription도 인라인으로
# 붙인다. 결제 연동이 없어 이 체크박스가 유일한 프리미엄 부여 수단이라, 별도 "User subscriptions"
# 화면을 따로 찾아가지 않아도 되게 한다.
class UserSubscriptionInline(admin.StackedInline):
    model = UserSubscription
    can_delete = False
    fields = ('is_active_premium', 'subscribed_at', 'expired_at')

admin.site.unregister(User)

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    inlines = (UserPreferenceInline, UserSubscriptionInline)
    list_display = UserAdmin.list_display + ('phone_number', 'member_grade', 'is_premium')
    list_filter = UserAdmin.list_filter + ('preference__grade', 'subscription__is_active_premium')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('preference', 'preference__grade', 'subscription')

    @admin.display(description='전화번호')
    def phone_number(self, obj):
        return getattr(obj.preference, 'phone_number', '') if hasattr(obj, 'preference') else ''

    @admin.display(description='권한 등급')
    def member_grade(self, obj):
        grade = getattr(obj.preference, 'grade', None) if hasattr(obj, 'preference') else None
        return grade if grade else '-'

    @admin.display(description='프리미엄', boolean=True)
    def is_premium(self, obj):
        return getattr(obj.subscription, 'is_active_premium', False) if hasattr(obj, 'subscription') else False

# 0-0. 한국투자증권(KIS) 접근 토큰 캐시 조회용 (읽기 전용)
@admin.register(KisAccessToken)
class KisAccessTokenAdmin(admin.ModelAdmin):
    list_display = ('id', 'issued_at', 'expires_at')
    ordering = ('-issued_at',)

    def has_add_permission(self, request):
        return False  # 토큰은 kis_client.get_access_token()을 통해서만 발급/저장

# 0-0-1. 국내휴장일조회 캐시 조회용 (읽기 전용)
@admin.register(MarketHoliday)
class MarketHolidayAdmin(admin.ModelAdmin):
    list_display = ('date', 'is_market_open', 'is_business_day', 'is_trading_day', 'is_settlement_day')
    list_filter = ('is_market_open',)
    ordering = ('-date',)

    def has_add_permission(self, request):
        return False  # kis_client.is_market_open()을 통해서만 채워짐

# 0-1. 키워드 기반 뉴스 수집용 RSS 사이트 관리
@admin.register(NewsSource)
class NewsSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'rss_url', 'is_active', 'created_at')
    list_editable = ('is_active',)
    search_fields = ('name', 'rss_url')
    list_filter = ('is_active',)

# 0-1-1. 기사 URL 도메인 → 언론사명 매핑 (source_media 폴백용)
@admin.register(MediaOutlet)
class MediaOutletAdmin(admin.ModelAdmin):
    list_display = ('domain', 'name', 'created_at')
    search_fields = ('domain', 'name')

# 0-2. 키워드 기반 뉴스 수집용 감지 키워드 관리
@admin.register(NewsKeyword)
class NewsKeywordAdmin(admin.ModelAdmin):
    list_display = ('keyword', 'linked_stock', 'is_active', 'created_at')
    list_editable = ('is_active',)
    search_fields = ('keyword', 'linked_stock__name')
    list_filter = ('is_active',)
    autocomplete_fields = ('linked_stock',)  # 종목 입력해서 검색 가능하게

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'linked_stock':
            kwargs['queryset'] = StockItem.objects.order_by('name')  # 종목명 오름차순 정렬
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

# 1. 주식 종목 관리
@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ('ticker', 'name', 'market_type', 'market_cap', 'is_major_index', 'is_active', 'created_at')
    list_editable = ('is_active',) # 목록에서 바로 모니터링 끄고 켤 수 있게 설정
    search_fields = ('ticker', 'name')
    list_filter = ('market_type', 'is_active', 'is_major_index')
    ordering = ('-market_cap',)

    def get_search_results(self, request, queryset, search_term):
        # 검색/자동완성(예: NewsKeyword의 연결된 종목 검색창) 결과는 종목명 오름차순으로 정렬
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        return queryset.order_by('name'), use_distinct

# 1-1. 코스피/코스닥 지수 시계열 관리
@admin.register(MarketIndex)
class MarketIndexAdmin(admin.ModelAdmin):
    list_display = ('market_type', 'date', 'open_price', 'high_price', 'low_price', 'close_price', 'change', 'change_pct', 'volume', 'foreign_net_qty', 'institution_net_qty', 'retail_net_qty')
    list_filter = ('market_type',)
    ordering = ('-date', 'market_type')

# 1-0-1-1. 종목 실시간 현재가 캐시 조회용 (읽기 전용)
@admin.register(StockRealtimePrice)
class StockRealtimePriceAdmin(admin.ModelAdmin):
    list_display = ('stock', 'close_price', 'change', 'change_pct', 'volume', 'updated_at')
    search_fields = ('stock__name', 'stock__ticker')
    ordering = ('-updated_at',)

    def has_add_permission(self, request):
        return False  # collect_stock_realtime_price 명령을 통해서만 생성됨

# 1-0-1-2. 등락률 순위(특징종목) 캐시 조회용 (읽기 전용)
@admin.register(RankedMover)
class RankedMoverAdmin(admin.ModelAdmin):
    list_display = ('rank_type', 'rank', 'ticker', 'name', 'price', 'change_pct', 'updated_at')
    list_filter = ('rank_type',)
    search_fields = ('ticker', 'name')
    ordering = ('rank_type', 'rank')

    def has_add_permission(self, request):
        return False  # collect_fluctuation_ranking 명령을 통해서만 생성됨

# 1-0-1-3. 해외지수/국제환율/금리 캐시 조회용 (읽기 전용)
@admin.register(GlobalMarketQuote)
class GlobalMarketQuoteAdmin(admin.ModelAdmin):
    list_display = ('category', 'name', 'code', 'price', 'change_pct', 'order', 'updated_at')
    list_filter = ('category',)
    search_fields = ('name', 'code')
    ordering = ('category', 'order')

    def has_add_permission(self, request):
        return False  # collect_global_market_data 명령을 통해서만 생성됨

# 1-0-1-4. 환전 고시 환율 일별 이력 조회용 (읽기 전용)
@admin.register(ExchangeRateSnapshot)
class ExchangeRateSnapshotAdmin(admin.ModelAdmin):
    list_display = ('currency_code', 'currency_name', 'date', 'deal_bas_r')
    list_filter = ('currency_code',)
    ordering = ('currency_code', '-date')

    def has_add_permission(self, request):
        return False  # collect_exchange_rate_fixing 명령을 통해서만 생성됨

# 2-1. 일봉 가격(실제 OHLCV) 관리
@admin.register(StockDailyPrice)
class StockDailyPriceAdmin(admin.ModelAdmin):
    list_display = ('stock', 'date', 'open_price', 'high_price', 'low_price', 'close_price', 'volume')
    list_filter = ('date', 'stock__name')
    search_fields = ('stock__name', 'stock__ticker')
    ordering = ('-date', 'stock')
    list_select_related = ('stock',)  # list_display의 stock 표시가 매 행마다 추가 쿼리 안 나가게
    show_full_result_count = False    # "전체 N건" 카운트 쿼리 생략 (90만+ 행에서 매우 느림)
    list_per_page = 100

# 2-2. AI 주가 예측 결과 관리
@admin.register(StockPrediction)
class StockPredictionAdmin(admin.ModelAdmin):
    list_display = ('stock', 'date', 'pred_next_close', 'trading_signal', 'up_probability')
    list_filter = ('trading_signal', 'date', 'stock__name')
    search_fields = ('stock__name', 'stock__ticker')
    ordering = ('-date', 'stock')
    list_select_related = ('stock',)  # list_display의 stock 표시가 매 행마다 추가 쿼리 안 나가게
    show_full_result_count = False
    list_per_page = 100

# 3. 증권 뉴스 및 AI 에이전트 가공 기사 관리
@admin.register(AnalyzedArticle)
class AnalyzedArticleAdmin(admin.ModelAdmin):
    list_display = ('id', 'source_media', 'title', 'stock', 'matched_keyword', 'applied_template', 'is_premium', 'is_posted', 'ai_generated', 'scraped_by', 'scraped_at')
    list_display_links = ('id', 'title')
    list_filter = ('ai_generated', 'source_media', 'is_premium', 'is_posted', 'applied_template')
    search_fields = ('title', 'ai_summary', 'blog_content', 'stock__name', 'matched_keyword__keyword')
    ordering = ('-scraped_at',)

# 4-1. 마이페이지 - 뉴스구독/자동포스팅 환경설정 관리
@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'grade', 'news_subscription', 'auto_posting_enabled', 'post_all_articles', 'interested_keywords', 'updated_at')
    list_filter = ('grade', 'news_subscription', 'auto_posting_enabled', 'post_all_articles')
    search_fields = ('user__username', 'interested_keywords')

# 4-2. 마이페이지 - 회원이 SNS/블로그 업로드용으로 등록한 계정 목록 관리
class BlogAccountConnectionFilter(admin.SimpleListFilter):
    """BlogPostingAccount.is_connected()는 플랫폼별로 필요한 필드 조합이 달라 DB 컬럼 하나로
    판단할 수 없는 파이썬 로직이라, 목록 필터에서 쓰려면 같은 조건을 쿼리셋으로 옮겨와야 한다."""
    title = '연동 상태'
    parameter_name = 'connected'

    def lookups(self, request, model_admin):
        return (('yes', '연동됨(자격정보 등록 완료)'), ('no', '미연동(등록 전/불완전)'))

    def queryset(self, request, queryset):
        if self.value() not in ('yes', 'no'):
            return queryset
        connected_q = (
            (Q(platform__in=('WORDPRESS', 'BLOGGER')) & ~Q(site_url='') & ~Q(account_id='') & ~Q(credential=''))
            | (Q(platform='TUMBLR') & ~Q(account_id='') & ~Q(credential='') & ~Q(oauth_token_secret=''))
        )
        return queryset.filter(connected_q) if self.value() == 'yes' else queryset.exclude(connected_q)


@admin.register(BlogPostingAccount)
class BlogPostingAccountAdmin(admin.ModelAdmin):
    list_display = ('user', 'platform', 'connection_status', 'is_enabled', 'site_url_link', 'account_id', 'updated_at')
    list_filter = ('platform', 'is_enabled', BlogAccountConnectionFilter)
    search_fields = ('user__username', 'user__email', 'account_id', 'site_url')
    list_select_related = ('user',)
    ordering = ('user', 'platform')
    # credential(비밀번호/API Key/OAuth 토큰)과 oauth_token_secret(텀블러 전용 짝 시크릿)은
    # 목록/폼 어디에도 평문 노출하지 않고, 재입력할 때만 갱신 — 등록 여부만 has_credential로
    # 별도 표시한다.
    exclude = ('credential', 'oauth_token_secret')
    readonly_fields = ('has_credential',)

    @admin.display(description='연동 상태')
    def connection_status(self, obj):
        if obj.is_connected():
            return format_html('<span style="color:#2e7d32;font-weight:bold;">연동됨</span>')
        return format_html('<span style="color:#999;">미연동</span>')

    @admin.display(description='자격정보(비밀번호/API Key) 등록 여부')
    def has_credential(self, obj):
        return '등록됨' if obj.credential else '미등록'

    @admin.display(description='사이트 주소')
    def site_url_link(self, obj):
        if not obj.site_url:
            return '-'
        return format_html('<a href="{0}" target="_new" rel="noopener noreferrer">{0}</a>', obj.site_url)

# 4-3. 회원별 발행 이력 조회용 (읽기 전용)
@admin.register(PostedArticle)
class PostedArticleAdmin(admin.ModelAdmin):
    list_display = ('id', 'blog_account', 'article', 'external_url', 'posted_at')
    list_filter = ('blog_account__platform',)
    search_fields = ('blog_account__user__username', 'article__title')
    ordering = ('-posted_at',)

    def has_add_permission(self, request):
        return False  # 발행 커맨드(post_to_wordpress 등)를 통해서만 생성됨

# 4. 유저 프리미엄 구독 정보 관리
@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'user_email', 'is_active_premium', 'subscribed_at', 'expired_at')
    list_editable = ('is_active_premium',)
    list_filter = ('is_active_premium',)
    search_fields = ('user__username', 'user__email')

    @admin.display(description='이메일')
    def user_email(self, obj):
        return obj.user.email

# 5. 소셜 로그인(카카오/구글) 연동 계정 관리
@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = ('user', 'provider', 'provider_uid', 'email', 'connected_at')
    list_filter = ('provider',)
    search_fields = ('user__username', 'provider_uid', 'email')

# 6. 챗봇 대화 기록 관리 (읽기 전용 조회 용도)
@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'session_key', 'role', 'short_content', 'created_at')
    list_filter = ('role',)
    search_fields = ('content', 'user__username', 'session_key')
    ordering = ('-created_at',)

    def short_content(self, obj):
        return obj.content[:50]
    short_content.short_description = '내용'

    def has_add_permission(self, request):
        return False  # 챗봇 API를 통해서만 생성됨

# 7. 로그인 로그 관리 (읽기 전용 조회 용도)
@admin.register(LoginLog)
class LoginLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'login_method', 'ip_address', 'created_at')
    list_filter = ('login_method',)
    search_fields = ('user__username', 'ip_address')
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False  # 로그인 시점에만 자동 생성됨

# 8. 메뉴 접속 로그 관리 (읽기 전용 조회 용도)
@admin.register(MenuAccessLog)
class MenuAccessLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'menu_name', 'path', 'ip_address', 'accessed_at')
    list_filter = ('menu_name',)
    search_fields = ('user__username', 'path', 'ip_address')
    ordering = ('-accessed_at',)

    def has_add_permission(self, request):
        return False  # 메뉴 접속 미들웨어를 통해서만 생성됨

# 9. 홈페이지 뉴스레터 구독자 관리
@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ('email', 'is_active', 'subscribed_at')
    list_editable = ('is_active',)
    search_fields = ('email',)
    list_filter = ('is_active',)
    ordering = ('-subscribed_at',)

# 10. 뉴스레터 발행호 관리 — generate_newsletter_draft가 자동으로 초안(DRAFT)을 만들어두면,
# 여기서 제목/본문을 검토·수정한 뒤 상태를 READY로 바꿔야 send_newsletter가 발송한다.
@admin.register(NewsletterIssue)
class NewsletterIssueAdmin(admin.ModelAdmin):
    list_display = ('subject', 'status', 'article_count', 'created_at', 'sent_at', 'recipient_count')
    list_filter = ('status',)
    search_fields = ('subject', 'body')
    ordering = ('-created_at',)
    readonly_fields = ('article_count', 'created_at', 'sent_at', 'recipient_count')
    actions = ['mark_ready']

    def get_readonly_fields(self, request, obj=None):
        # 이미 발송된 건은 제목/본문도 더 이상 수정할 수 없게 잠근다 (발송 내용과 화면 내용이 어긋나는 것 방지)
        if obj and obj.status == 'SENT':
            return self.readonly_fields + ('subject', 'body', 'status')
        return self.readonly_fields

    def has_add_permission(self, request):
        return False  # generate_newsletter_draft 커맨드를 통해서만 생성됨

    @admin.action(description="선택한 초안을 발송 대기(READY)로 표시")
    def mark_ready(self, request, queryset):
        updated = queryset.filter(status='DRAFT').update(status='READY')
        self.message_user(request, f"{updated}건을 발송 대기 상태로 변경했습니다.")

# 11. 상단 내비게이션 메뉴 관리
@admin.register(Menu)
class MenuAdmin(admin.ModelAdmin):
    list_display = ('name', 'menu_type', 'order', 'is_active', 'url_name', 'external_url', 'badge_text')
    list_editable = ('order', 'is_active')
    list_filter = ('menu_type', 'is_active')
    search_fields = ('name', 'url_name', 'external_url')
    ordering = ('menu_type', 'order')

# 12. 상담 신청 (ISA/IRP/연금저축 등 분리된 정적 페이지에서 들어오는 리드)
@admin.register(ConsultRequest)
class ConsultRequestAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'product', 'name_with_sheet_link', 'phone', 'interest', 'goal')
    list_filter = ('product', 'created_at')
    search_fields = ('name', 'phone', 'interest', 'goal', 'message')
    readonly_fields = ('product', 'name', 'phone', 'interest', 'goal', 'message', 'source_ip', 'created_at')
    ordering = ('-created_at',)

    @admin.display(description='이름')
    def name_with_sheet_link(self, obj):
        # FC/PB가 리드를 클릭하면 바로 종합 재무상담 시트를 새 탭으로 열 수 있게 연결.
        # ConsultRequest는 오직 사이트의 온라인 상담 신청 폼(consult_request_view)을 통해서만
        # 생성되므로 신청경로는 항상 "온라인 상담신청"으로 넘긴다.
        params = urlencode({
            'name': obj.name,
            'phone': obj.phone,
            'apply_date': obj.created_at.strftime('%Y-%m-%d'),
            'channel': '온라인 상담신청',
        })
        url = f"{reverse('financial_consult_sheet')}?{params}"
        return format_html('<a href="{}" target="_blank">{}</a>', url, obj.name)


# 12-1. 구독 신청 (/subscribe/apply/) — PG 연동 전까지는 여기서 승인 액션으로 프리미엄을 켜준다.
@admin.register(SubscriptionOrder)
class SubscriptionOrderAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'name', 'phone', 'payment_method', 'status', 'reviewed_at')
    # 목록 줄 끝에서 바로 콤보박스로 처리 상태를 바꾸고 한 번에 저장할 수 있게 한다(요청:
    # "목록 라인 맨 끝에 저장 버튼"). Django가 list_editable을 쓰면 목록 첫 컬럼을 자동으로
    # 상세화면 링크로 돌려주고, 그 아래 "Save" 버튼 하나로 변경된 행을 한꺼번에 저장한다 —
    # 이 저장 경로도 save_model()을 그대로 타므로 승인 시 프리미엄 활성화 로직이 똑같이 적용된다.
    list_editable = ('status',)
    list_filter = ('status', 'payment_method', 'referral_source', 'motivation')
    search_fields = ('name', 'phone', 'user__username', 'user__email')
    readonly_fields = ('user', 'name', 'phone', 'referral_source', 'motivation', 'payment_method', 'created_at')
    ordering = ('-created_at',)
    actions = ['approve_orders', 'reject_orders']

    @staticmethod
    def _activate_premium(order):
        """승인 처리(액션이든 상세 화면에서 상태를 직접 바꿔 저장하든) 시 프리미엄 구독을
        켜는 공통 로직. 액션 메서드에만 있으면, 상세 화면에서 처리 상태 드롭다운을 "승인
        완료"로 바꾸고 Save만 눌러도 상태만 바뀌고 실제 구독은 활성화 안 되는 불일치가
        생긴다 — 그래서 save_model에서도 똑같이 타도록 뽑아뒀다."""
        subscription, _ = UserSubscription.objects.get_or_create(user=order.user)
        now = timezone.now()
        subscription.is_active_premium = True
        subscription.subscribed_at = subscription.subscribed_at or now
        subscription.expired_at = now + timezone.timedelta(days=30)
        subscription.save(update_fields=['is_active_premium', 'subscribed_at', 'expired_at'])
        return now

    @staticmethod
    def _deactivate_premium(order):
        """반려 처리 시(액션이든 상세 화면 저장이든) 프리미엄을 끈다. _activate_premium과
        대칭 — 승인 후 반려로 뒤집는 경우(예: 결제 확인 전 실수로 승인) is_active_premium이
        REJECTED 상태에서도 True로 남아있던 버그(2026-08-07, 다비드 계정에서 실측: 주문은
        REJECTED인데 구독 페이지엔 "이미 프리미엄 구독 중"으로 표시됨) 재발 방지.
        UserSubscription이 어느 주문으로 활성화됐는지 FK로 추적하진 않으므로, 이 사용자의
        다른 주문이 이미 APPROVED 상태라면(정상적으로 유효한 별개의 구독) 끄지 않는다."""
        if SubscriptionOrder.objects.filter(user=order.user, status='APPROVED').exclude(pk=order.pk).exists():
            return
        UserSubscription.objects.filter(user=order.user).update(is_active_premium=False)

    @admin.action(description="선택한 신청을 승인하고 프리미엄 구독을 활성화")
    def approve_orders(self, request, queryset):
        approved = 0
        for order in queryset.filter(status='PENDING'):
            now = self._activate_premium(order)
            order.status = 'APPROVED'
            order.reviewed_at = now
            order.save(update_fields=['status', 'reviewed_at'])
            approved += 1
        self.message_user(request, f"{approved}건을 승인하고 프리미엄을 활성화했습니다.")

    @admin.action(description="선택한 신청을 반려")
    def reject_orders(self, request, queryset):
        rejected = 0
        for order in queryset.exclude(status='REJECTED'):
            self._deactivate_premium(order)
            order.status = 'REJECTED'
            order.reviewed_at = timezone.now()
            order.save(update_fields=['status', 'reviewed_at'])
            rejected += 1
        self.message_user(request, f"{rejected}건을 반려했습니다.")

    def save_model(self, request, obj, form, change):
        # 상세(변경) 화면에서 처리 상태를 직접 바꿔 저장한 경우에도, 목록의 승인/반려
        # 액션과 동일하게 프리미엄 활성화/비활성화가 같이 반영되게 한다.
        if change and 'status' in form.changed_data:
            if obj.status == 'APPROVED':
                now = self._activate_premium(obj)
                if not obj.reviewed_at:
                    obj.reviewed_at = now
            elif obj.status == 'REJECTED':
                self._deactivate_premium(obj)
                if not obj.reviewed_at:
                    obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)


# 13. 종합 재무상담 시트 (financial_consult_sheet.html 저장 버튼으로 제출된 기록)
@admin.register(FinancialConsultSheet)
class FinancialConsultSheetAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'name_with_load_link', 'customer_phone', 'consultant_name', 'consult_date', 'created_by')
    list_display_links = ('name_with_load_link',)  # created_at은 더 이상 (기본 admin 변경화면으로 가는) 링크가 아니게
    list_filter = ('created_at', 'consult_date')
    search_fields = ('customer_name', 'customer_phone', 'consultant_name')
    readonly_fields = ('customer_name', 'customer_phone', 'consultant_name', 'consult_date', 'data', 'created_by', 'created_at')
    ordering = ('-created_at',)

    @admin.display(description='고객명')
    def name_with_load_link(self, obj):
        # 예전엔 이름이 아니라 저장 일시(created_at, list_display 첫 컬럼이라 기본 admin이
        # 자동으로 링크를 건다)를 클릭하면 원본 JSONField를 그대로 보여주는 기본 변경화면으로
        # 갔었다 — 이름을 눌렀을 때 실제 시트 화면(financial_consult_sheet_view)이 그 데이터로
        # 채워진 채 열리도록 ?load=<id>로 바꾼다.
        url = f"{reverse('financial_consult_sheet')}?load={obj.pk}"
        return format_html('<a href="{}" target="_blank">{}</a>', url, obj.customer_name or '(이름 없음)')


# 14. Django Admin 목록(NextFinUp 관리 앱)에서 발행 기록(PostedArticle) 바로 아래에 파이프라인
# 수동 실행 화면(/admin-tools/pipeline/, articles.views.admin_tools.pipeline_status_view) 링크를
# 끼워 넣는다. 실제 모델/DB 테이블은 없는 화면이라 ModelAdmin으로 등록할 수 없어, Django가
# 앱 목록을 만들 때 쓰는 get_app_list를 감싸서 모델 항목처럼 보이는 dict 하나를 삽입한다.
_original_get_app_list = admin.site.get_app_list


def _get_app_list_with_pipeline_link(request, app_label=None):
    app_list = _original_get_app_list(request, app_label=app_label)
    for app in app_list:
        if app['app_label'] != 'articles':
            continue
        models = app['models']
        idx = next((i for i, m in enumerate(models) if m['object_name'] == 'PostedArticle'), None)
        if idx is None:
            continue
        models.insert(idx + 1, {
            'name': '발행 파이프라인 즉시 실행',
            'object_name': 'PipelineTrigger',
            'admin_url': reverse('pipeline_status'),
            'add_url': None,
            'view_only': True,
        })
    return app_list


admin.site.get_app_list = _get_app_list_with_pipeline_link

