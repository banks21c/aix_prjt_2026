from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import AnalyzedArticle, StockItem


class StaticViewSitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.6

    # (url name, priority) — 로그인/마이페이지 등 회원 전용·비공개 페이지는 검색엔진에 노출할
    # 이유가 없어 제외하고, 공개된 콘텐츠 페이지만 등록한다.
    pages = [
        ('landing_page', 1.0),
        ('main_dashboard', 0.9),
        ('news_board', 0.8),
        ('insurance_compare', 0.4),
        ('privacy_policy', 0.2),
        ('terms_of_service', 0.2),
    ]

    def items(self):
        return self.pages

    def location(self, item):
        return reverse(item[0])

    def priority(self, item):
        return item[1]


class StockSitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.7

    def items(self):
        return StockItem.objects.filter(is_active=True).only('ticker')

    def location(self, obj):
        return reverse('stock_detail', args=[obj.ticker])


class NewsSitemap(Sitemap):
    changefreq = 'never'
    priority = 0.5

    def items(self):
        return AnalyzedArticle.objects.order_by('-scraped_at').only('id', 'scraped_at')

    def location(self, obj):
        return reverse('news_detail', args=[obj.pk])

    def lastmod(self, obj):
        return obj.scraped_at
