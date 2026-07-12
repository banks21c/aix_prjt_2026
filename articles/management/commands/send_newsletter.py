from django.conf import settings
from django.core import signing
from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from articles.models import NewsletterIssue, NewsletterSubscriber


def _unsubscribe_url(subscriber):
    token = signing.dumps(subscriber.pk, salt='newsletter-unsubscribe')
    return f"{settings.SITE_URL}{reverse('newsletter_unsubscribe', args=[token])}"


class Command(BaseCommand):
    help = (
        '상태가 READY인 뉴스레터 초안(NewsletterIssue)을 활성 구독자에게 발송하고 SENT로 표시합니다. '
        '초안은 generate_newsletter_draft 커맨드가 자동 작성하고, 관리자가 Admin 화면에서 내용을 '
        '검토·수정한 뒤 상태를 READY로 바꿔야 이 커맨드의 발송 대상이 됩니다.'
    )

    def handle(self, *args, **options):
        ready_issues = list(NewsletterIssue.objects.filter(status='READY'))
        if not ready_issues:
            self.stdout.write(self.style.WARNING("[-] 발송 대기(READY) 상태인 초안이 없습니다."))
            return

        subscribers = list(NewsletterSubscriber.objects.filter(is_active=True))
        if not subscribers:
            self.stdout.write(self.style.WARNING("[-] 활성화된 뉴스레터 구독자가 없습니다."))
            return

        connection = get_connection()
        connection.open()
        try:
            for issue in ready_issues:
                sent_count = 0
                for subscriber in subscribers:
                    try:
                        body = (
                            f"{issue.body}\n\n"
                            "---\n"
                            f"더 이상 수신을 원치 않으시면 아래 링크를 눌러 수신을 거부할 수 있습니다:\n"
                            f"{_unsubscribe_url(subscriber)}"
                        )
                        EmailMessage(
                            subject=issue.subject,
                            body=body,
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            to=[subscriber.email],
                            connection=connection,
                        ).send(fail_silently=False)
                        sent_count += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"    ↳ [발송 실패] {subscriber.email}: {e}"))

                issue.status = 'SENT'
                issue.sent_at = timezone.now()
                issue.recipient_count = sent_count
                issue.save(update_fields=['status', 'sent_at', 'recipient_count'])
                self.stdout.write(self.style.SUCCESS(
                    f"🎉 \"{issue.subject}\" 발송 완료 — 구독자 {sent_count}/{len(subscribers)}명"
                ))
        finally:
            connection.close()
