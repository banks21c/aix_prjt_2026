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

    class Meta:
        verbose_name = "RSS 언론사 (NewsSource)"
        verbose_name_plural = "RSS 언론사 (NewsSource)"

    def __str__(self):
        return self.name


class MediaOutlet(models.Model):
    """기사 URL의 도메인 → 언론사명 매핑. trafilatura가 sitename 메타데이터를 못 뽑아내면
    fetch_article_metadata가 도메인 문자열(예: mk.co.kr)을 그대로 언론사명으로 써버려 뜻이
    안 통하는데, 여기 등록된 도메인이면 그 대신 사람이 읽을 수 있는 언론사명을 쓴다."""
    domain = models.CharField(max_length=100, unique=True, verbose_name="도메인 (예: mk.co.kr)")
    name = models.CharField(max_length=50, verbose_name="언론사명 (예: 매일경제)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "언론사 도메인 매핑 (MediaOutlet)"
        verbose_name_plural = "언론사 도메인 매핑 (MediaOutlet)"

    def __str__(self):
        return f"{self.domain} → {self.name}"


class NewsKeyword(models.Model):
    keyword = models.CharField(max_length=50, unique=True, verbose_name="감지 키워드")
    # 키워드를 특정 종목과 연결해두면, 매칭된 기사가 해당 종목 뉴스로도 분류됨 (선택 사항)
    linked_stock = models.ForeignKey(
        StockItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="news_keywords", verbose_name="연결된 종목(선택)"
    )
    is_active = models.BooleanField(default=True, verbose_name="수집 활성화 여부")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "감지 키워드 (NewsKeyword)"
        verbose_name_plural = "감지 키워드 (NewsKeyword)"

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
    # article_ai.generate_draft가 ai_summary 등과 함께 만드는, 원문 제목을 다듬은 버전
    # (낚시성 표현 정리, 매체 특유 말투 제거 등). AI 요약을 아직 안 돌린 기사는 비어있다 —
    # display_title에서 title로 폴백한다. 원본 title은 스크래핑 시점 그대로 보존한다
    # (편집 화면에서 "원본 제목"으로 계속 노출/비교할 수 있도록).
    ai_title = models.CharField(max_length=255, blank=True, default='', verbose_name="AI 가공 제목")
    original_url = models.URLField(unique=True, verbose_name="원본 기사 링크")
    source_media = models.CharField(max_length=50, verbose_name="언론사")
    scraped_at = models.DateTimeField(auto_now_add=True, verbose_name="수집 일시")

    # 어느 수집 경로로 들어왔는지 명시하는 구분자. 이 필드가 생기기 전에는
    # original_url이 'kis-news://'로 시작하는지, 'internal://featured-briefing/'으로
    # 시작하는지, original_content가 비어있는지 같은 문자열/상태 추론으로 소스를 구분했는데
    # (뷰/템플릿/커맨드 여러 곳에 흩어져 있었음), 매번 같은 판별 로직을 반복하고 어긋나기
    # 쉬워서 필드로 명시했다.
    SOURCE_RSS = 'RSS'
    SOURCE_KIS = 'KIS'
    SOURCE_AI_BRIEFING = 'AI_BRIEFING'
    SOURCE_MANUAL = 'MANUAL'
    SOURCE_CHOICES = [
        (SOURCE_RSS, 'RSS 수집 / 회원 URL 등록 (원문 링크 있음)'),
        (SOURCE_KIS, 'KIS 종합 시황_공시 (원문 링크 없음)'),
        (SOURCE_AI_BRIEFING, 'AI 특징주 통합 브리핑'),
        (SOURCE_MANUAL, '회원 직접 작성 (원문 링크 없음)'),
    ]
    source_type = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default=SOURCE_RSS, verbose_name="수집 소스",
    )

    # 마이페이지 "뉴스 구독"에서 회원이 고른 카테고리(UserPreference.news_subscription)와
    # 매칭해 blog_posting.select_candidates가 발행 후보를 거르는 데 쓴다. 기존 기사는 전부
    # 경제/증시 콘텐츠였으므로 default=ECONOMY로 소급 적용해도 의미가 맞는다 — 건강 콘텐츠는
    # generate_health_briefing이 HEALTH로 명시해서 생성한다.
    CATEGORY_ECONOMY = 'ECONOMY'
    CATEGORY_HEALTH = 'HEALTH'
    CATEGORY_FOOD = 'FOOD'
    CATEGORY_TRAVEL = 'TRAVEL'
    CATEGORY_CHOICES = [
        (CATEGORY_ECONOMY, '경제'),
        (CATEGORY_HEALTH, '건강/의학'),
        (CATEGORY_FOOD, '음식/영양'),
        (CATEGORY_TRAVEL, '여행/관광'),
    ]
    content_category = models.CharField(
        max_length=10, choices=CATEGORY_CHOICES, default=CATEGORY_ECONOMY, verbose_name="콘텐츠 카테고리",
    )

    # AI 에이전트 텍스트 분석 및 블로그 원고 데이터. 자동 수집(KIS/RSS)은 실제 AI를 호출하지
    # 않으므로(토큰 비용) 이 세 필드를 비워둔 채 저장한다 — ai_generated=True로 바뀌는 시점
    # (news_ai_summarize_view/news_scrape_view/특징주 브리핑)에만 실제 값이 채워진다.
    ai_summary = models.TextField(blank=True, default='', verbose_name="AI 3줄 요약")
    ai_analysis = models.TextField(blank=True, default='', verbose_name="AI 관점 분석")
    blog_content = models.TextField(blank=True, default='', verbose_name="블로그/티스토리 포스팅용 원고")
    original_content = models.TextField(blank=True, default='', verbose_name="원문 본문(스크래핑)")

    # articles/thumbnail.py로 AI 요약과 함께 생성되는 발행용 썸네일. 원문 기사의 사진은 절대
    # 재사용하지 않고(무단전재 이슈), 종목명/등락 시그널 등 자체 데이터로 매번 새로 그린다.
    thumbnail = models.ImageField(upload_to='thumbnails/%Y/%m/', blank=True, null=True, verbose_name="발행용 썸네일")

    # utils.detect_reuse_restriction으로 스크래핑 시점에 판별해 저장 ("무단전재 배포금지,
    # AI 학습 및 활용 금지" 류의 문구 감지 여부). True인 기사는 article_ai.generate_draft가
    # original_content를 AI 프롬프트에 전혀 넣지 않고 제목/구조화된 사실만으로 NextFinUp
    # 자체 해설을 생성하는 분기를 탄다 — 원문 인용/재구성 없이 attribution(출처)은 그대로 유지.
    has_reuse_restriction = models.BooleanField(default=False, verbose_name="원문 재사용 제한 문구 감지")

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

    # 자동 수집(KIS/RSS)은 ai_summary/ai_analysis/blog_content를 비워둔 채 저장한다. news_scrape_view
    # (수동 등록), 특징주 통합 브리핑, news_ai_summarize_view(포스팅 직전 개별 요약) 세 경로만 실제
    # article_ai 호출 결과를 채우고 이 값을 True로 세운다. 자동/수동 발행 모두 이 값이 True인 기사만 발행한다.
    ai_generated = models.BooleanField(default=False, verbose_name="실제 AI 요약 여부")
    ai_summarized_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="ai_summarized_articles", verbose_name="AI 요약 실행 회원",
    )
    ai_summarized_at = models.DateTimeField(null=True, blank=True, verbose_name="AI 요약 실행 일시")

    class Meta:
        verbose_name = "수집 기사 (AnalyzedArticle)"
        verbose_name_plural = "수집 기사 (AnalyzedArticle)"

    def __str__(self):
        return f"[{self.source_media}] {self.title}"

    @property
    def display_title(self):
        """게시판/발행/썸네일 등 실제로 노출하는 제목. AI 가공 제목이 있으면 그걸 쓰고,
        아직 AI 요약 전이면 원본 제목(title)으로 폴백한다."""
        return self.ai_title or self.title


class PostedArticle(models.Model):
    """어떤 회원의 어떤 등록 계정(사이트)에 어떤 기사가 이미 발행됐는지 추적.

    AnalyzedArticle.is_posted는 단일 글로벌 사이트 기준의 기존 필드라 다중 회원 사이트를
    각각 독립적으로 발행/추적하기엔 맞지 않아, 계정×기사 단위로 별도 기록한다.
    """
    blog_account = models.ForeignKey(BlogPostingAccount, on_delete=models.CASCADE, related_name="posted_articles", verbose_name="발행 계정")
    article = models.ForeignKey(AnalyzedArticle, on_delete=models.CASCADE, related_name="postings", verbose_name="기사")
    # 한글 제목이 슬러그로 퍼센트 인코딩되면 기본 URLField max_length(200)를 쉽게 넘겨서
    # (워드프레스/블로거/티스토리 공통) 저장이 실패했었다 — 여유 있게 늘려둔다.
    external_url = models.URLField(max_length=500, blank=True, verbose_name="발행된 글 주소")
    # 워드프레스 post ID / 블로거 post ID. 재발행(republish_article)이 새 글을 또 만드는 대신
    # 이 ID로 기존 글을 업데이트하는 데 쓴다. 이 필드가 생기기 전 발행분은 빈 값이라, 그런 경우
    # 재발행은 (기존 글을 못 찾으니) 블로그에서 직접 수정하라고 안내한다.
    external_post_id = models.CharField(max_length=100, blank=True, verbose_name="발행된 글 ID(플랫폼 내부)")
    posted_at = models.DateTimeField(auto_now_add=True, verbose_name="발행 일시")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="최근 재발행 일시")

    class Meta:
        unique_together = ('blog_account', 'article')
        ordering = ['-posted_at']
        verbose_name = "발행 기록 (PostedArticle)"
        verbose_name_plural = "발행 기록 (PostedArticle)"

    def __str__(self):
        return f"{self.blog_account} -> {self.article.title[:30]}"
