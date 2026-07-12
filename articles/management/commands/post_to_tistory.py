from django.core.management.base import BaseCommand
from articles.blog_posting import enabled_accounts, publish_article, select_candidates


class Command(BaseCommand):
    help = (
        'AI가 가공한 투자 분석 원고와 ML 주가 예측 데이터를 결합하여, '
        '마이페이지에서 티스토리 자동 포스팅을 등록/활성화한 회원들의 각자 블로그에 발행합니다.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help='계정 1개당 한 번에 발행할 최대 건수 (생략 시 미발행 기사 전체)',
        )

    def handle(self, *args, **options):
        limit = options.get('limit')

        accounts = enabled_accounts('TISTORY')
        if not accounts.exists():
            self.stdout.write(self.style.WARNING("[-] 티스토리 자동 포스팅을 활성화한 회원이 없습니다."))
            return

        for account in accounts:
            candidates = select_candidates(account, account.user.preference, limit)
            if not candidates:
                self.stdout.write(self.style.WARNING(f"[-] {account.user.username}: 새로 발행할 기사가 없습니다."))
                continue

            self.stdout.write(self.style.SUCCESS(
                f"🚀 {account.user.username} (블로그: {account.account_id}) 티스토리 발행을 시작합니다. 대상: {len(candidates)}건"
            ))

            for article in candidates:
                ok, result = publish_article(account, article)
                if ok:
                    self.stdout.write(self.style.SUCCESS(
                        f"    ↳ [발행 성공] {article.title[:40]} 글이 {account.user.username}의 티스토리에 등록되었습니다: {result}"
                    ))
                else:
                    self.stdout.write(self.style.ERROR(f"    ↳ [{account.user.username}] {result}"))

        self.stdout.write(self.style.SUCCESS("🎉 오늘의 모든 회원 티스토리 발행 프로세스가 종료되었습니다!"))
