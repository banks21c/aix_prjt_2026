from django.contrib import admin
from .models import (
    StockItem, StockPrediction, AnalyzedArticle, UserSubscription, SocialAccount,
    NewsSource, NewsKeyword, MarketIndex, KisAccessToken, MarketHoliday,
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

