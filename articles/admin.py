from django.contrib import admin
from .models import (
    StockItem, StockPrediction, AnalyzedArticle, UserSubscription, SocialAccount,
    NewsSource, NewsKeyword, MarketIndex, KisAccessToken, MarketHoliday, ChatMessage,
    LoginLog, MenuAccessLog, UserPreference, BlogPostingAccount, PostedArticle,
    StockRealtimePrice,
)

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
    list_display = ('market_type', 'date', 'open_price', 'high_price', 'low_price', 'close_price', 'change', 'change_pct')
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

# 2. 일봉 가격 및 AI 주가 예측 결과 관리
@admin.register(StockPrediction)
class StockPredictionAdmin(admin.ModelAdmin):
    list_display = ('stock', 'date', 'close_price', 'pred_next_close', 'trading_signal', 'up_probability')
    list_filter = ('trading_signal', 'date', 'stock__name')
    search_fields = ('stock__name', 'stock__ticker')
    ordering = ('-date', 'stock')
    list_select_related = ('stock',)  # list_display의 stock 표시가 매 행마다 추가 쿼리 안 나가게
    show_full_result_count = False    # "전체 N건" 카운트 쿼리 생략 (75만+ 행에서 매우 느림)
    list_per_page = 100

# 3. 증권 뉴스 및 AI 에이전트 가공 기사 관리
@admin.register(AnalyzedArticle)
class AnalyzedArticleAdmin(admin.ModelAdmin):
    list_display = ('id', 'source_media', 'title', 'stock', 'matched_keyword', 'applied_template', 'is_premium', 'is_posted', 'scraped_at')
    list_display_links = ('id', 'title')
    list_filter = ('source_media', 'is_premium', 'is_posted', 'applied_template')
    search_fields = ('title', 'ai_summary', 'blog_content', 'stock__name', 'matched_keyword__keyword')
    ordering = ('-scraped_at',)

# 4-1. 마이페이지 - 뉴스구독/자동포스팅 환경설정 관리
@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'news_subscription', 'auto_posting_enabled', 'post_all_articles', 'interested_keywords', 'updated_at')
    list_filter = ('news_subscription', 'auto_posting_enabled', 'post_all_articles')
    search_fields = ('user__username', 'interested_keywords')

# 4-2. 마이페이지 - 블로그 자동 포스팅 계정 관리
@admin.register(BlogPostingAccount)
class BlogPostingAccountAdmin(admin.ModelAdmin):
    list_display = ('user', 'platform', 'is_enabled', 'site_url', 'account_id', 'updated_at')
    list_filter = ('platform', 'is_enabled')
    search_fields = ('user__username', 'account_id', 'site_url')
    # credential(비밀번호/API Key)은 목록/폼 어디에도 평문 노출하지 않고, 재입력할 때만 갱신
    exclude = ('credential',)

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
    list_display = ('user', 'is_active_premium', 'subscribed_at', 'expired_at')
    list_editable = ('is_active_premium',)
    list_filter = ('is_active_premium',)
    search_fields = ('user__username', 'user__email')

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

