# articles/views.py였던 것을 도메인별로 쪼갠 패키지. config/urls.py가 기존에 `from articles.views
# import (...)`로 가져오던 이름들을 여기서 그대로 재수출해, urls.py는 손댈 필요가 없다.
from .public import (
    landing_page_view, main_dashboard_view, newsletter_subscribe_view, newsletter_unsubscribe_view,
    privacy_policy_view, terms_of_service_view, email_collection_refusal_view, insurance_compare_view,
    blog_connect_guide_view, adsense_guide_view,
    consult_request_view, header_fragment_view, expert_consult_view, expert_consult_apply_view, ticker_data_view, ticker_stocks_view,
    subscribe_view, subscribe_apply_view,
)
from .admin_tools import (
    cron_status_view, financial_consult_sheet_view, financial_consult_sheet_save_view,
    financial_consult_sheet_search_view,
    integration_status_view, pipeline_status_view, pipeline_trigger_view, server_health_view,
)
from .news import (
    news_ai_summarize_view, news_article_preview_view, news_board_view, news_detail_view,
    news_edit_view, news_scrape_view, news_write_view, post_articles_view,
    repost_article_view, republish_article_view,
)
from .stocks import (
    stock_detail_view, stock_minute_chart_view, stock_period_chart_view, market_index_minute_chart_view,
    stock_quote_view, stock_search_suggest_view,
)
from .chatbot import chatbot_ask_view
from .auth import (
    signup_view, login_view, logout_view, delete_account_view, verify_email_view,
    kakao_login_view, kakao_callback_view,
    google_login_view, google_callback_view,
    naver_login_view, naver_callback_view,
)
from .mypage import my_page_view, my_posted_articles_view, blogger_connect_view, blogger_callback_view

__all__ = [
    'landing_page_view', 'main_dashboard_view', 'newsletter_subscribe_view',
    'newsletter_unsubscribe_view', 'cron_status_view', 'privacy_policy_view',
    'terms_of_service_view', 'insurance_compare_view', 'consult_request_view',
    'header_fragment_view', 'expert_consult_view', 'expert_consult_apply_view', 'subscribe_view', 'subscribe_apply_view',
    'financial_consult_sheet_view', 'financial_consult_sheet_save_view',
    'financial_consult_sheet_search_view',
    'integration_status_view', 'pipeline_status_view', 'pipeline_trigger_view', 'server_health_view',
    'news_ai_summarize_view', 'news_article_preview_view', 'news_board_view', 'news_detail_view',
    'news_edit_view', 'news_scrape_view', 'news_write_view', 'post_articles_view',
    'repost_article_view', 'republish_article_view', 'stock_detail_view', 'stock_minute_chart_view',
    'stock_period_chart_view',
    'market_index_minute_chart_view', 'stock_quote_view', 'stock_search_suggest_view', 'chatbot_ask_view',
    'signup_view', 'login_view', 'logout_view', 'delete_account_view',
    'kakao_login_view', 'kakao_callback_view',
    'google_login_view', 'google_callback_view',
    'naver_login_view', 'naver_callback_view',
    'blogger_connect_view', 'blogger_callback_view',
    'my_page_view', 'my_posted_articles_view', 'verify_email_view',
]
