# articles/models.py였던 것을 도메인별로 쪼갠 패키지. 모델을 실제 모델 코드 옆이 아니라
# 여기서 재수출하는 이유는, articles 앱 밖 어디에서든 `from articles.models import X`
# (또는 `from .models import X`)가 예전과 똑같이 동작해야 하기 때문 — 마이그레이션/관리
# 커맨드/admin.py 등 기존 임포트 구문을 하나도 건드리지 않고 그대로 재사용할 수 있다.
from .market import (
    StockItem, KisAccessToken, RankedMover, StockRealtimePrice, MarketHoliday, MarketIndex,
    StockDailyPrice, StockPrediction, GlobalMarketQuote, ExchangeRateSnapshot, StockInvestorFlow,
    StockDisclosure,
)
from .members import (
    UserSubscription, SubscriptionOrder, MemberGrade, UserPreference, BlogPostingAccount, LoginLog,
    MenuAccessLog, ChatMessage, SocialAccount,
)
from .news import NewsSource, NewsKeyword, AnalyzedArticle, PostedArticle, MediaOutlet
from .content import (
    NewsletterSubscriber, NewsletterIssue, Menu, ConsultRequest, FinancialConsultSheet,
)

__all__ = [
    'StockItem', 'KisAccessToken', 'RankedMover', 'StockRealtimePrice', 'MarketHoliday',
    'MarketIndex', 'StockDailyPrice', 'StockPrediction', 'GlobalMarketQuote', 'ExchangeRateSnapshot',
    'StockInvestorFlow', 'StockDisclosure',
    'UserSubscription', 'SubscriptionOrder', 'MemberGrade', 'UserPreference', 'BlogPostingAccount',
    'LoginLog', 'MenuAccessLog', 'ChatMessage', 'SocialAccount',
    'NewsSource', 'NewsKeyword', 'AnalyzedArticle', 'PostedArticle', 'MediaOutlet',
    'NewsletterSubscriber', 'NewsletterIssue', 'Menu', 'ConsultRequest',
    'FinancialConsultSheet',
]
