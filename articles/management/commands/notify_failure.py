from django.core.mail import mail_admins
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'cron 파이프라인 작업이 0이 아닌 종료 코드로 끝났을 때 관리자(settings.ADMINS)에게 '
        '알림 메일을 보냅니다. deploy/run_job.sh가 실패를 감지했을 때만 호출합니다.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--job', required=True, help='실패한 작업 이름(로그 파일명 등)')
        parser.add_argument('--exit-code', required=True, help='종료 코드')
        parser.add_argument('--log-tail', required=True, help='실패 시점 출력이 담긴 임시 파일 경로')

    def handle(self, *args, **options):
        try:
            with open(options['log_tail'], encoding='utf-8', errors='replace') as f:
                tail = f.read()[-4000:]
        except OSError:
            tail = '(로그를 읽을 수 없습니다)'

        subject = f"파이프라인 실패: {options['job']} (exit {options['exit_code']})"
        message = (
            f"작업: {options['job']}\n종료 코드: {options['exit_code']}\n\n"
            f"--- 출력 마지막 4000자 ---\n{tail}"
        )
        mail_admins(subject, message, fail_silently=True)
        self.stdout.write(self.style.WARNING(f"[!] 실패 알림 메일 발송 시도: {subject}"))
