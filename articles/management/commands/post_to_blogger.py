from django.core.management.base import BaseCommand
from articles.blog_posting import enabled_accounts, publish_article, select_candidates


class Command(BaseCommand):
    help = (
        'AI가 가공한 투자 분석 원고와 ML 주가 예측 데이터를 결합하여, '
        '마이페이지에서 블로거(Blogger) 자동 포스팅을 연동/활성화한 회원들의 각자 블로그에 발행합니다.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help='계정 1개당 한 번에 발행할 최대 건수 (생략 시 미발행 기사 전체)',
        )
        parser.add_argument(
            '--category', choices=['ECONOMY', 'HEALTH', 'FOOD'], default=None,
            help='이 구독 카테고리를 고른 회원 계정만 대상으로 실행 (생략 시 전체 계정)',
        )

    def handle(self, *args, **options):
        limit = options.get('limit')

        accounts = enabled_accounts('BLOGGER', category=options.get('category'))
        if not accounts.exists():
            self.stdout.write(self.style.WARNING("[-] 블로거 자동 포스팅을 연동/활성화한 회원이 없습니다."))
            return

        for account in accounts:
            candidates = select_candidates(account, account.user.preference, limit)
            if not candidates:
                self.stdout.write(self.style.WARNING(f"[-] {account.user.username}: 새로 발행할 기사가 없습니다."))
                continue

            self.stdout.write(self.style.SUCCESS(
                f"🚀 {account.user.username} ({account.site_url}) 블로거 발행을 시작합니다. 대상: {len(candidates)}건"
            ))

            for article in candidates:
                ok, result = publish_article(account, article)
                if ok:
                    self.stdout.write(self.style.SUCCESS(
                        f"    ↳ [발행 성공] {article.title[:40]} 글이 {account.user.username}의 블로거에 등록되었습니다: {result or '(비공개 초안)'}"
                    ))
                else:
                    self.stdout.write(self.style.ERROR(f"    ↳ [{account.user.username}] {result}"))

        self.stdout.write(self.style.SUCCESS("🎉 오늘의 모든 회원 블로거 발행 프로세스가 종료되었습니다!"))
