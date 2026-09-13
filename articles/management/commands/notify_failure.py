from django.core.management.base import BaseCommand

from articles.models import SystemErrorLog


class Command(BaseCommand):
    help = (
        'cron 파이프라인 작업이 0이 아닌 종료 코드로 끝났을 때 SystemErrorLog에 기록합니다 '
        '(예전엔 관리자에게 메일을 보냈으나, 봇 트래픽발 알림 메일 스팸 문제로 DB 저장 후 '
        '/admin/에서 조회하는 방식으로 바뀜). deploy/run_job.sh가 실패를 감지했을 때만 호출합니다.'
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

        job = options['job']
        exit_code = options['exit_code']
        SystemErrorLog.objects.create(
            level='ERROR',
            logger_name=f'cron.{job}',
            message=f"파이프라인 실패: {job} (exit {exit_code})",
            traceback=tail,
        )
        self.stdout.write(self.style.WARNING(f"[!] 실패 알림 기록: {job} (exit {exit_code})"))
