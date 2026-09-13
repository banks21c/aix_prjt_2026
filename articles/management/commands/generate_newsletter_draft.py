from django.core.management.base import BaseCommand
from django.utils import timezone

from articles.models import AnalyzedArticle, NewsletterIssue


def _wrap_email_html(date_label, briefing_html):
    """generate_featured_stock_briefing이 만든 blog_content(h3/ul 등 블로그용 HTML 조각)를
    그대로 이메일 본문으로 옮겨쓰지 않고, 헤더/푸터를 더해 뉴스레터 옷을 입힌다."""
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Malgun Gothic\',sans-serif;'
        'max-width:640px;margin:0 auto;color:#222;">'
        '<div style="padding:20px 0;border-bottom:2px solid #1e88e5;">'
        '<h2 style="margin:0;color:#1e88e5;">NextFinUp 마감 시황 브리핑</h2>'
        f'<p style="margin:4px 0 0;color:#666;font-size:14px;">{date_label}</p>'
        '</div>'
        f'<div style="padding:20px 0;">{briefing_html}</div>'
        '<div style="padding:16px 0;border-top:1px solid #eee;color:#999;font-size:12px;">'
        'NextFinUp 드림</div>'
        '</div>'
    )


class Command(BaseCommand):
    help = (
        '그날 장마감 후 생성된 AI 특징주 브리핑(generate_featured_stock_briefing --session=close)의 '
        'blog_content를 뉴스레터 이메일 옷을 입혀 NewsletterIssue(HTML)로 만들고, 관리자 검토 없이 '
        '바로 status=READY로 저장해 다음 send_newsletter 실행 때 자동 발송되도록 합니다. 그날 마감 '
        '브리핑이 아직 없거나 아직 발송되지 않은(READY) 이슈가 남아 있으면 새로 만들지 않고 '
        '건너뜁니다(중복 발송 방지).'
    )

    def handle(self, *args, **options):
        pending = NewsletterIssue.objects.filter(status='READY').first()
        if pending:
            self.stdout.write(self.style.WARNING(
                f"[-] 아직 발송되지 않은 이슈가 있어 새로 생성하지 않습니다: \"{pending.subject}\" (id={pending.pk})"
            ))
            return

        today = timezone.localdate()
        pseudo_url = f"internal://featured-briefing/{today}/close"
        briefing = AnalyzedArticle.objects.filter(original_url=pseudo_url).first()
        if not briefing:
            self.stdout.write(self.style.WARNING(
                f"[-] 오늘({today}) 마감 특징주 브리핑이 아직 없어 뉴스레터를 생성하지 않습니다. "
                "generate_featured_stock_briefing --session=close가 먼저 돌아야 합니다."
            ))
            return

        date_label = f"{today:%Y년 %m월 %d일} 장마감 브리핑"
        issue = NewsletterIssue.objects.create(
            subject=f"[NextFinUp] {today:%Y-%m-%d} 마감 특징주 브리핑",
            body=_wrap_email_html(date_label, briefing.blog_content),
            article_count=1,
            status='READY',
        )
        self.stdout.write(self.style.SUCCESS(
            f"📝 마감 브리핑 뉴스레터 생성 완료 (id={issue.pk}) — 다음 send_newsletter 실행 때 자동 발송됩니다."
        ))
