from django.db import models
from django.contrib.auth.models import User

from .market import StockItem
from .members import BlogPostingAccount


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
    original_content = models.TextField(blank=True, default='', verbose_name="원문 본문(스크래핑)")

    TEMPLATE_CHOICES = [
        ('T1', '템플릿 1 (뉴스 요약형)'),
        ('T2', '템플릿 2 (종목 분석형)'),
        ('T3', '템플릿 3 (카드뉴스 대본형)'),
    ]
    applied_template = models.CharField(max_length=5, choices=TEMPLATE_CHOICES, default='T1', verbose_name="적용된 템플릿")

    # 시스템 관리용 트리거
    is_premium = models.BooleanField(default=False, verbose_name="유료 회원 전용 콘텐츠")
    is_posted = models.BooleanField(default=False, verbose_name="블로그 자동 발행 완료")

    # RSS 자동 수집(scraped_ai_news 등)은 채우지 않고 비워둔다. news_scrape_view에서 회원이
    # 직접 URL을 등록한 경우에만 채워져, 등급별 일일 스크래핑 한도 계산에 쓰인다.
    scraped_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="manual_scrapes", verbose_name="수동 스크래핑 등록 회원",
    )

    def __str__(self):
        return f"[{self.source_media}] {self.title}"


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
