"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import path
from articles.business_admin import business_admin_site  # ◀ 업무(상담·구독) 전용 어드민, /staff/
from articles.sitemaps import StaticViewSitemap, StockSitemap, NewsSitemap
from articles.views import (
    landing_page_view, main_dashboard_view, newsletter_subscribe_view, newsletter_unsubscribe_view,
    newsletter_sample_view, cron_status_view,
    privacy_policy_view, terms_of_service_view, email_collection_refusal_view, blog_connect_guide_view, adsense_guide_view, health_content_calendar_view, food_content_calendar_view, insurance_compare_view, consult_request_view, header_fragment_view, cookie_banner_fragment_view, ticker_fragment_view,
    ticker_data_view, ticker_stocks_view,
    expert_consult_view,
    expert_consult_apply_view,
    faq_board_view,
    tools_hub_view, char_counter_view, currency_converter_view, severance_calculator_view,
    unemployment_benefit_calculator_view,
    subscribe_view,
    subscribe_apply_view,
    financial_consult_sheet_view, financial_consult_sheet_save_view, financial_consult_sheet_search_view,
    integration_status_view,
    pipeline_status_view, pipeline_trigger_view, server_health_view,
    operations_overview_view, ai_performance_admin_view, theme_settings_view, theme_css_view,
    image_generator_view, generated_image_list_view, generated_image_delete_view,
    news_ai_summarize_view, news_article_preview_view, news_board_view, news_detail_view, news_edit_view, news_scrape_view, news_write_view, post_articles_view, repost_article_view, republish_article_view, stock_detail_view, stock_minute_chart_view, stock_period_chart_view, watchlist_toggle_view,
    market_index_minute_chart_view, stock_quote_view, stock_search_suggest_view,
    chatbot_ask_view,
    ai_performance_view,
    signup_view, login_view, logout_view, delete_account_view, find_password_view,
    kakao_login_view, kakao_callback_view,
    google_login_view, google_callback_view,
    naver_login_view, naver_callback_view,
    blogger_connect_view, blogger_callback_view,
    tumblr_connect_view, tumblr_callback_view,
    my_page_view, my_posted_articles_view, blog_account_disconnect_view, verify_email_view, change_password_view,
)  # ◀ 우리가 만든 뷰 임포트

sitemaps = {
    'static': StaticViewSitemap,
    'stocks': StockSitemap,
    'news': NewsSitemap,
}

urlpatterns = [
    path('admin/', admin.site.urls),  # ◀ 시스템 관리 — 슈퍼유저 전용(articles/admin.py에서 제한)
    path('staff/', business_admin_site.urls),  # ◀ 업무 관리(상담 신청/재무상담 시트/구독 신청/프리미엄 구독) — is_staff면 접근 가능
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='sitemap'),
    path('', landing_page_view, name='landing_page'),  # ◀ 메인 홈페이지(랜딩 페이지)
    path('newsletter/subscribe/', newsletter_subscribe_view, name='newsletter_subscribe'),
    path('newsletter/unsubscribe/<str:token>/', newsletter_unsubscribe_view, name='newsletter_unsubscribe'),
    path('newsletter/sample/', newsletter_sample_view, name='newsletter_sample'),
    path('admin-tools/cron/', cron_status_view, name='cron_status'),
    path('admin-tools/integrations/', integration_status_view, name='integration_status'),
    path('admin-tools/pipeline/', pipeline_status_view, name='pipeline_status'),
    path('admin-tools/pipeline/run/<str:key>/', pipeline_trigger_view, name='pipeline_trigger'),
    path('admin-tools/health/', server_health_view, name='server_health'),
    path('admin-tools/overview/', operations_overview_view, name='operations_overview'),
    path('admin-tools/performance/', ai_performance_admin_view, name='ai_performance_admin'),
    path('admin-tools/theme/', theme_settings_view, name='theme_settings'),
    path('admin-tools/image-generator/', image_generator_view, name='image_generator'),
    path('admin-tools/image-generator/list/', generated_image_list_view, name='generated_image_list'),
    path('admin-tools/image-generator/<int:pk>/delete/', generated_image_delete_view, name='generated_image_delete'),
    # ◀ /static/ 밖(동적) — nginx가 /static/만 직접 서빙하므로 여기 둬야 Django에 닿는다.
    # 확장자를 .css로 하지 않는 이유: Cloudflare가 .css로 끝나는 URL을 origin의 Cache-Control과
    # 무관하게 엣지에서 캐시해버리는 게 실측으로 확인돼(theme_css_view의 no-store 무시), 관리자가
    # 색을 바꿔도 몇 시간은 예전 색이 그대로 보였다 — 템플릿 쪽은 캐시 무효화용 ?v=(theme_version
    # 컨텍스트 프로세서) 쿼리스트링도 같이 붙여 이중으로 방어한다.
    path('theme-style', theme_css_view, name='theme_css'),
    path('admin-tools/consult-sheet/', financial_consult_sheet_view, name='financial_consult_sheet'),
    path('admin-tools/consult-sheet/save/', financial_consult_sheet_save_view, name='financial_consult_sheet_save'),
    path('admin-tools/consult-sheet/search/', financial_consult_sheet_search_view, name='financial_consult_sheet_search'),
    path('privacy-policy/', privacy_policy_view, name='privacy_policy'),
    path('terms/', terms_of_service_view, name='terms_of_service'),
    path('email-collection-refusal/', email_collection_refusal_view, name='email_collection_refusal'),
    path('guide/blog-connect/', blog_connect_guide_view, name='blog_connect_guide'),
    path('guide/adsense/', adsense_guide_view, name='adsense_guide'),
    path('health-calendar/', health_content_calendar_view, name='health_content_calendar'),
    path('food-calendar/', food_content_calendar_view, name='food_content_calendar'),
    path('faq/', faq_board_view, name='faq_board'),
    path('experts/', expert_consult_view, name='expert_consult'),
    path('experts/apply/', expert_consult_apply_view, name='expert_consult_apply'),  # ◀ 프로필/12가지 약속 없이 신청 폼만 있는 단독 페이지
    path('subscribe/', subscribe_view, name='subscribe'),
    path('subscribe/apply/', subscribe_apply_view, name='subscribe_apply'),
    path('insurance/', insurance_compare_view, name='insurance_compare'),
    path('tools/', tools_hub_view, name='tools_hub'),
    path('tools/character-counter/', char_counter_view, name='char_counter'),
    path('tools/currency-converter/', currency_converter_view, name='currency_converter'),
    path('tools/severance-calculator/', severance_calculator_view, name='severance_calculator'),
    path('tools/unemployment-benefit-calculator/', unemployment_benefit_calculator_view, name='unemployment_benefit_calculator'),
    path('partials/header/', header_fragment_view, name='header_fragment'),
    path('partials/cookie-banner/', cookie_banner_fragment_view, name='cookie_banner_fragment'),
    path('partials/ticker/', ticker_fragment_view, name='ticker_fragment'),
    path('api/ticker/', ticker_data_view, name='ticker_data'),  # ◀ 첫 번째(시세) 티커가 폴링하는 JSON
    path('api/ticker/stocks/', ticker_stocks_view, name='ticker_stocks'),  # ◀ 두 번째(개별 종목) 티커가 폴링하는 JSON
    path('api/consult/', consult_request_view, name='consult_request'),
    path('dashboard/', main_dashboard_view, name='main_dashboard'),  # ◀ AI 예측/뉴스 대시보드
    path('performance/', ai_performance_view, name='ai_performance'),  # ◀ AI 예측 성과 공개 트랙레코드
    path('market-index/<str:market_type>/minute-chart/', market_index_minute_chart_view, name='market_index_minute_chart'),  # ◀ 대시보드 지수차트 '1일' 온디맨드 API
    path('api/stock-quote/', stock_quote_view, name='stock_quote'),  # ◀ 대시보드 종목 검색 위젯이 호출하는 온디맨드 API
    path('api/stock-search/', stock_search_suggest_view, name='stock_search_suggest'),  # ◀ 검색창 자동완성 후보 목록 API
    path('news/', news_board_view, name='news_board'),  # ◀ 수집된 뉴스 게시판
    path('news/scrape/', news_scrape_view, name='news_scrape'),  # ◀ 관리자 전용: URL 입력 → 스크래핑
    path('post/write/', news_write_view, name='news_write'),  # ◀ 원문 링크 없이 제목+본문 직접 입력 → AI 요약
    path('news/post/', post_articles_view, name='post_articles'),  # ◀ 선택한 기사를 내 블로그 계정에 수동 발행
    path('news/<int:pk>/', news_detail_view, name='news_detail'),
    path('news/<int:pk>/preview/', news_article_preview_view, name='news_article_preview'),  # ◀ news_scrape에서 방금 스크랩/중복 등록된 기사를 같은 화면 아래에 보여주는 AJAX
    path('news/<int:pk>/edit/', news_edit_view, name='news_edit'),  # ◀ 관리자 전용 기사 수정
    path('news/<int:pk>/ai-summarize/', news_ai_summarize_view, name='news_ai_summarize'),  # ◀ 포스팅 직전 개별 기사 AI 요약 트리거
    path('news/<int:pk>/republish/', republish_article_view, name='republish_article'),  # ◀ 이미 발행된 계정에 최신 내용으로 재발행(같은 글 업데이트)
    path('news/<int:pk>/repost/', repost_article_view, name='repost_article'),  # ◀ 이미 발행된 계정에도 새 글로 다시 포스팅(포스팅 기능 그대로, 중복 발행 가드만 없음)
    path('stocks/<str:ticker>/', stock_detail_view, name='stock_detail'),  # ◀ 종목 상세(일봉 차트/AI 예측/관련 뉴스)
    path('stocks/<str:ticker>/minute-chart/', stock_minute_chart_view, name='stock_minute_chart'),  # ◀ 당일 분봉 온디맨드 API
    path('stocks/<str:ticker>/period-chart/<str:period>/', stock_period_chart_view, name='stock_period_chart'),  # ◀ 주봉/월봉 온디맨드 API
    path('stocks/<str:ticker>/watchlist/', watchlist_toggle_view, name='watchlist_toggle'),  # ◀ 관심종목 추가/삭제 토글 API

    path('api/chatbot/', chatbot_ask_view, name='chatbot_ask'),  # ◀ 주식/경제 챗봇 위젯 API

    path('signup/', signup_view, name='signup'),
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('find-password/', find_password_view, name='find_password'),  # ◀ 임시 비밀번호 발급(이메일 발송)
    path('accounts/delete/', delete_account_view, name='delete_account'),
    path('mypage/', my_page_view, name='my_page'),  # ◀ 내 정보 관리(뉴스구독/자동포스팅 설정)
    path('mypage/posted/', my_posted_articles_view, name='my_posted_articles'),  # ◀ 내가 발행한 글 이력
    path('mypage/password/', change_password_view, name='change_password'),  # ◀ 비밀번호 변경
    path('mypage/blog/<str:platform>/disconnect/', blog_account_disconnect_view, name='blog_account_disconnect'),
    path('verify-email/<uidb64>/<token>/', verify_email_view, name='verify_email'),  # ◀ 이메일 인증 링크

    path('accounts/kakao/login/', kakao_login_view, name='kakao_login'),
    path('accounts/kakao/callback/', kakao_callback_view, name='kakao_callback'),
    path('accounts/google/login/', google_login_view, name='google_login'),
    path('accounts/google/callback/', google_callback_view, name='google_callback'),
    path('accounts/naver/login/', naver_login_view, name='naver_login'),
    # 네이버 개발자센터에 등록된 Callback URL(https://www.nextfinup.com/api/member/oauth2c)과 정확히 일치해야 함
    path('api/member/oauth2c', naver_callback_view, name='naver_callback'),

    path('accounts/blogger/connect/', blogger_connect_view, name='blogger_connect'),  # ◀ 마이페이지 - 블로거 자동포스팅 연동
    path('accounts/blogger/callback/', blogger_callback_view, name='blogger_callback'),
    path('accounts/tumblr/connect/', tumblr_connect_view, name='tumblr_connect'),  # ◀ 마이페이지 - 텀블러 자동포스팅 연동
    path('accounts/tumblr/callback/', tumblr_callback_view, name='tumblr_callback'),
]

if settings.DEBUG:
    # 운영 환경은 nginx가 /media/를 직접 서빙(config/settings.py MEDIA_ROOT 주석 참고)하지만,
    # 로컬 개발 서버(runserver)에서는 Django가 직접 서빙해야 썸네일 이미지가 보인다.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

