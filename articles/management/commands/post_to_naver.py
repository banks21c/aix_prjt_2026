from django.core.management.base import BaseCommand
from articles.blog_posting import enabled_accounts, publish_article, select_candidates


class Command(BaseCommand):
    help = (
        '(시뮬레이션) AI가 가공한 투자 분석 원고를 마이페이지에서 네이버 블로그 자동 포스팅을 '
        '등록/활성화한 회원 계정 기준으로 콘텐츠까지 빌드합니다. '
        '네이버는 개인 블로그 글쓰기를 지원하는 공식 오픈 API가 없어(검색 API만 공개), '
        '실제 네트워크 발행 없이 결과만 기록/로그로 남깁니다.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help='계정 1개당 한 번에 처리할 최대 건수 (생략 시 미발행 기사 전체)',
        )

    def handle(self, *args, **options):
        limit = options.get('limit')

        accounts = enabled_accounts('NAVER')
        if not accounts.exists():
            self.stdout.write(self.style.WARNING("[-] 네이버 블로그 자동 포스팅을 활성화한 회원이 없습니다."))
            return

        for account in accounts:
            candidates = select_candidates(account, account.user.preference, limit)
            if not candidates:
                self.stdout.write(self.style.WARNING(f"[-] {account.user.username}: 새로 발행할 기사가 없습니다."))
                continue

            self.stdout.write(self.style.SUCCESS(
                f"🚀 {account.user.username} (네이버 계정: {account.account_id}) 콘텐츠 빌드를 시작합니다. 대상: {len(candidates)}건"
            ))

            for article in candidates:
                ok, result = publish_article(account, article)
                if ok:
                    self.stdout.write(self.style.WARNING(
                        f"    ↳ [시뮬레이션] {account.user.username} 계정용 글 빌드 완료: {article.title[:40]} "
                        f"(네이버 공식 글쓰기 API 부재로 실제 발행은 지원되지 않습니다 - 수동 게시 필요)"
                    ))
                else:
                    self.stdout.write(self.style.ERROR(f"    ↳ [{account.user.username}] {result}"))

        self.stdout.write(self.style.SUCCESS("🎉 오늘의 모든 회원 네이버 블로그 콘텐츠 빌드가 종료되었습니다!"))
