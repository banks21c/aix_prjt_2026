import requests
from django.core.management.base import BaseCommand
from articles.blog_posting import build_post_content, enabled_accounts, select_candidates
from articles.models import PostedArticle

# 첫 포스팅이라 바로 공개되지 않도록 임시저장으로 올림. 검증 끝나면 "publish"로 변경.
WP_POST_STATUS = "draft"


class Command(BaseCommand):
    help = (
        'AI가 가공한 투자 분석 원고와 ML 주가 예측 데이터를 결합하여, '
        '마이페이지에서 워드프레스 자동 포스팅을 등록/활성화한 회원들의 각자 사이트에 발행합니다.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help='계정 1개당 한 번에 발행할 최대 건수 (생략 시 미발행 기사 전체)',
        )

    def handle(self, *args, **options):
        limit = options.get('limit')

        accounts = enabled_accounts('WORDPRESS')
        if not accounts.exists():
            self.stdout.write(self.style.WARNING("[-] 워드프레스 자동 포스팅을 활성화한 회원이 없습니다."))
            return

        for account in accounts:
            candidates = select_candidates(account, account.user.preference, limit)
            if not candidates:
                self.stdout.write(self.style.WARNING(f"[-] {account.user.username}: 새로 발행할 기사가 없습니다."))
                continue

            self.stdout.write(self.style.SUCCESS(
                f"🚀 {account.user.username} ({account.site_url}) 워드프레스 발행을 시작합니다. 대상: {len(candidates)}건"
            ))

            for article in candidates:
                blog_title, full_html_content, subject_label = build_post_content(article)
                payload = {"title": blog_title, "content": full_html_content, "status": WP_POST_STATUS}
                try:
                    res = requests.post(
                        f"{account.site_url}/wp-json/wp/v2/posts",
                        auth=(account.account_id, account.credential),
                        json=payload,
                        timeout=15,
                    )
                    if res.status_code == 201:
                        post = res.json()
                        PostedArticle.objects.create(
                            blog_account=account, article=article, external_url=post.get('link', ''),
                        )
                        self.stdout.write(self.style.SUCCESS(
                            f"    ↳ [발행 성공] {subject_label} 글이 {account.user.username}의 워드프레스에 {WP_POST_STATUS}(으)로 등록되었습니다: {post.get('link')}"
                        ))
                    else:
                        self.stdout.write(self.style.ERROR(
                            f"    ↳ [{account.user.username}] 워드프레스 API 응답 에러 ({res.status_code}): {res.text[:300]}"
                        ))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"    ↳ [{account.user.username}] 네트워크 연동 실패: {str(e)}"))

        self.stdout.write(self.style.SUCCESS("🎉 오늘의 모든 회원 워드프레스 발행 프로세스가 종료되었습니다!"))
