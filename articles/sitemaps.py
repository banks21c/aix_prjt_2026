from django.contrib.sitemaps import Sitemap
from django.db.models import Max
from django.urls import reverse

from .models import AnalyzedArticle, StockItem


class StaticViewSitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.6
    protocol = 'https'

    # (url name, priority) — 로그인/마이페이지 등 회원 전용·비공개 페이지는 검색엔진에 노출할
    # 이유가 없어 제외하고, 공개된 콘텐츠 페이지만 등록한다.
    pages = [
        ('landing_page', 1.0),
        ('main_dashboard', 0.9),
        ('news_board', 0.8),
        ('expert_consult', 0.7),
        ('faq_board', 0.6),
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
    protocol = 'https'
    limit = 5000

    def items(self):
        # order_by가 없으면 Paginator가 UnorderedObjectListWarning을 내고 페이지 간
        # 항목이 중복/누락될 수 있다(실제로 gunicorn 로그에 매번 찍히고 있었다).
        return StockItem.objects.filter(is_active=True).only('ticker').order_by('ticker')

    def location(self, obj):
        return reverse('stock_detail', args=[obj.ticker])


class NewsSitemap(Sitemap):
    changefreq = 'never'
    priority = 0.5
    protocol = 'https'
    # 사이트맵 규격상 파일당 50,000 URL이 상한이다. 기사가 이미 5만 건을 넘어
    # 기본값(50,000)으로는 초과분이 통째로 누락되므로, 인덱스로 나눠 싣는다.
    # 5,000이면 파일 하나가 ~600KB로 떨어져 크롤러 한 번이 워커를 오래 잡지 않는다.
    limit = 5000

    def items(self):
        # 페이지네이션 안정성을 위해 pk 역순으로 정렬한다. scraped_at은 동일 시각 값이
        # 많아(수집 배치 단위) 페이지 경계에서 순서가 흔들릴 수 있다.
        return AnalyzedArticle.objects.only('id', 'scraped_at').order_by('-id')

    def location(self, obj):
        return reverse('news_detail', args=[obj.pk])

    def lastmod(self, obj):
        return obj.scraped_at

    def get_latest_lastmod(self):
        # Django 기본 구현은 max(self.lastmod(item) for item in self.items())로
        # 전체 5만여 건을 모델 인스턴스로 만들어 훑는다 — 인덱스(sitemap.xml)를 여는
        # 것만으로 그 비용이 든다. 집계 쿼리 한 번으로 대체한다.
        return AnalyzedArticle.objects.aggregate(latest=Max('scraped_at'))['latest']
