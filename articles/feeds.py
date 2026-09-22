from django.conf import settings
from django.contrib.syndication.views import Feed
from django.urls import reverse
from django.utils.html import strip_tags

from .models import AnalyzedArticle


class LatestArticlesFeed(Feed):
    """/rss.xml — 경제 동향 게시판의 최신 글.

    네이버 서치어드바이저는 사이트맵과 별개로 RSS를 받아 새 글 수집을 훨씬 빨리 돌린다.
    그동안 이 사이트에는 RSS가 없어 매일 쌓이는 기사가 사이트맵 재수집을 기다려야 했다.

    본문 없는 글(KIS 종합시황 헤드라인 등)은 상세 페이지에 보여줄 내용이 사실상 없어 빼고,
    유료 회원 전용 글도 뺀다 — 피드는 비로그인 크롤러가 읽는 것이라 공개분만 담는다.
    """

    title = 'NextFinUp 경제 동향'
    description = 'AI가 요약한 국내 증시·경제 뉴스와 종목 분석을 매일 전해드립니다.'
    language = 'ko'
    item_count = 30

    def link(self):
        return reverse('news_board')

    def feed_url(self):
        return reverse('rss_feed')

    def items(self):
        return (AnalyzedArticle.objects
                .exclude(original_content='')
                .filter(is_premium=False)
                .only('id', 'title', 'ai_title', 'ai_summary', 'original_content', 'source_media', 'scraped_at')
                .order_by('-scraped_at', '-id')[:self.item_count])

    def item_title(self, item):
        return item.ai_title or item.title

    def item_description(self, item):
        # AI 요약이 있으면 그걸, 없으면 원문 앞부분을 쓴다. 원문을 통째로 싣지 않는 것은
        # 언론사 저작권 문제도 있고, 피드를 그대로 퍼가는 스크래퍼에 전문을 넘길 이유도 없어서다.
        summary = (item.ai_summary or '').strip()
        if summary:
            return summary
        # 스크래핑 원문은 줄바꿈·연속 공백이 그대로 남아 있어(기사 소제목, 사이드 메뉴 잔재 등)
        # 피드 리더에서 지저분하게 보인다. 공백을 한 칸으로 눌러 한 문단처럼 만든다.
        body = ' '.join(strip_tags(item.original_content or '').split())
        return body[:200] + ('…' if len(body) > 200 else '')

    def item_link(self, item):
        return reverse('news_detail', args=[item.pk])

    def item_pubdate(self, item):
        return item.scraped_at

    def item_author_name(self, item):
        return item.source_media

    def item_guid(self, item):
        # 링크가 바뀌어도 같은 글로 인식되도록 고정 식별자를 쓴다
        return f"{settings.SITE_URL.rstrip('/')}/news/{item.pk}/"

    def item_guid_is_permalink(self, item):
        return False
