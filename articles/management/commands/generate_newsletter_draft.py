from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from articles.models import AnalyzedArticle, NewsletterIssue

MAX_ARTICLES_PER_ISSUE = 10


def _build_body(articles):
    lines = ["오늘의 AI 뉴스 브리핑\n"]
    for article in articles:
        stock_label = f" ({article.stock.name})" if article.stock else ""
        lines.append(f"- {article.title}{stock_label}")
        if article.ai_summary:
            lines.append(f"  {article.ai_summary[:150]}")
        lines.append("")
    lines.append("NextFinUp 드림")
    return "\n".join(lines)


class Command(BaseCommand):
    help = (
        '최근 수집된 AI 뉴스 기사로 뉴스레터 초안(NewsletterIssue, status=DRAFT)을 자동 작성합니다. '
        '실제 발송은 하지 않으며, 관리자가 Admin 화면에서 제목/본문을 검토·수정한 뒤 상태를 '
        'READY로 바꿔야 send_newsletter 커맨드가 발송합니다. 이미 검토 대기 중인 초안이 있으면 '
        '새로 만들지 않고 건너뜁니다(초안이 중복으로 쌓이는 것을 방지).'
    )

    def handle(self, *args, **options):
        pending = NewsletterIssue.objects.filter(status='DRAFT').first()
        if pending:
            self.stdout.write(self.style.WARNING(
                f"[-] 이미 검토 대기 중인 초안이 있어 새로 생성하지 않습니다: \"{pending.subject}\" (id={pending.pk})"
            ))
            return

        last_issue = NewsletterIssue.objects.order_by('-created_at').first()
        since = last_issue.created_at if last_issue else timezone.now() - timedelta(days=1)

        articles = list(
            AnalyzedArticle.objects
            .select_related('stock')
            .filter(scraped_at__gt=since)
            .order_by('-scraped_at')[:MAX_ARTICLES_PER_ISSUE]
        )
        if not articles:
            self.stdout.write(self.style.WARNING("[-] 지난 초안 이후 새로 수집된 기사가 없어 초안을 생성하지 않습니다."))
            return

        issue = NewsletterIssue.objects.create(
            subject=f"[NextFinUp] 오늘의 AI 뉴스 브리핑 ({timezone.localdate():%Y-%m-%d})",
            body=_build_body(articles),
            article_count=len(articles),
        )
        self.stdout.write(self.style.SUCCESS(
            f"📝 초안 생성 완료 (id={issue.pk}, 기사 {len(articles)}건) — Admin에서 검토 후 상태를 READY로 바꿔주세요."
        ))
