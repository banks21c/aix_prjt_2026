from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.utils import timezone

from articles import article_ai, thumbnail
from articles.content_calendar import get_topic_for_date
from articles.models import AnalyzedArticle

SESSION_LABELS = {'am': '오전', 'pm': '오후'}
KST = ZoneInfo('Asia/Seoul')


class Command(BaseCommand):
    help = (
        '여행/관광 정보 캘린더(articles/content_calendar.py, DB 기반)에서 오늘 이 세션(오전/오후)에 '
        '해당하는 주제를 찾아 AI로 글 1건을 생성하고 AnalyzedArticle(content_category=TRAVEL)로 '
        '저장합니다. generate_health_briefing/generate_food_briefing과 완전히 같은 구조입니다.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--session', choices=sorted(SESSION_LABELS), required=True,
            help='am(오전) 또는 pm(오후)',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='오늘 같은 세션 글이 이미 있어도 강제로 다시 생성합니다.',
        )

    def handle(self, *args, **options):
        session = options['session']
        session_label = SESSION_LABELS[session]
        # settings.TIME_ZONE은 UTC라(크론이 UTC 기준이라 그대로 둠) timezone.localdate()는
        # UTC 날짜를 준다 — KST 자정을 걸치는 시간대에 돌리면 하루 어긋난 날짜로 저장/조회될
        # 수 있어, 여기서는 KST로 직접 변환해서 쓴다.
        today = timezone.now().astimezone(KST).date()

        pseudo_url = f"internal://travel-briefing/{today}/{session}"
        if not options['force'] and AnalyzedArticle.objects.filter(original_url=pseudo_url).exists():
            self.stdout.write(self.style.WARNING(f"[-] 오늘 {session_label} 여행/관광정보 글이 이미 있습니다."))
            return

        info = get_topic_for_date('TRAVEL', today, session)
        self.stdout.write(self.style.SUCCESS(
            f"🚀 {today} {session_label} 여행/관광정보({info['category_name']} · {info['topic']})를 생성합니다."
        ))

        draft = article_ai.generate_travel_article(
            info['category_name'], info['topic'], info['angle'], info['title'],
        )

        AnalyzedArticle.objects.update_or_create(
            original_url=pseudo_url,
            defaults=dict(
                stock=None,
                matched_keyword=None,
                title=info['title'],
                source_media="NextFinUp 여행/관광정보",
                source_type=AnalyzedArticle.SOURCE_AI_BRIEFING,
                content_category=AnalyzedArticle.CATEGORY_TRAVEL,
                ai_summary=draft['ai_summary'],
                ai_analysis=draft['ai_analysis'],
                blog_content=draft['blog_content'],
                original_content=f"[카테고리] {info['category_name']}\n[주제] {info['topic']}\n[각도] {info['angle']}",
                thumbnail=thumbnail.build_thumbnail_file(
                    info['title'], category_label="여행/관광정보", is_economic_news=False,
                    content_category=AnalyzedArticle.CATEGORY_TRAVEL,
                    ai_summary=draft['ai_summary'],
                ),
                applied_template='T1',
                is_premium=False,
                is_posted=False,
                ai_generated=True,
                ai_summarized_by=None,
                ai_summarized_at=timezone.now(),
            ),
        )
        self.stdout.write(self.style.SUCCESS(f"🎉 {session_label} 여행/관광정보 저장 완료: {info['title']}"))
