import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from articles.blog_posting import build_post_content, enabled_accounts, select_candidates
from articles.models import PostedArticle

# 검증 전까지는 바로 공개되지 않도록 임시저장(비공개 초안)으로 올림
BLOGGER_IS_DRAFT = True


def _get_access_token(account):
    """계정에 저장된 OAuth 리프레시 토큰으로 새 액세스 토큰을 발급."""
    res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            'grant_type': 'refresh_token',
            'client_id': settings.GOOGLE_CLIENT_ID,
            'client_secret': settings.GOOGLE_CLIENT_SECRET,
            'refresh_token': account.credential,
        },
        timeout=10,
    ).json()
    return res.get('access_token'), res.get('error_description')


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

    def handle(self, *args, **options):
        limit = options.get('limit')

        accounts = enabled_accounts('BLOGGER')
        if not accounts.exists():
            self.stdout.write(self.style.WARNING("[-] 블로거 자동 포스팅을 연동/활성화한 회원이 없습니다."))
            return

        for account in accounts:
            candidates = select_candidates(account, account.user.preference, limit)
            if not candidates:
                self.stdout.write(self.style.WARNING(f"[-] {account.user.username}: 새로 발행할 기사가 없습니다."))
                continue

            access_token, error = _get_access_token(account)
            if not access_token:
                self.stdout.write(self.style.ERROR(
                    f"[{account.user.username}] 블로거 액세스 토큰 갱신 실패: {error or '알 수 없는 오류'} "
                    f"(마이페이지에서 블로거를 다시 연결해야 할 수 있습니다)"
                ))
                continue

            self.stdout.write(self.style.SUCCESS(
                f"🚀 {account.user.username} ({account.site_url}) 블로거 발행을 시작합니다. 대상: {len(candidates)}건"
            ))

            for article in candidates:
                blog_title, full_html_content, subject_label = build_post_content(article)
                try:
                    res = requests.post(
                        f"https://www.googleapis.com/blogger/v3/blogs/{account.account_id}/posts",
                        params={'isDraft': 'true' if BLOGGER_IS_DRAFT else 'false'},
                        headers={'Authorization': f'Bearer {access_token}'},
                        json={"title": blog_title, "content": full_html_content},
                        timeout=15,
                    )
                    body = res.json()
                    if res.status_code in (200, 201):
                        PostedArticle.objects.create(
                            blog_account=account, article=article, external_url=body.get('url', ''),
                        )
                        self.stdout.write(self.style.SUCCESS(
                            f"    ↳ [발행 성공] {subject_label} 글이 {account.user.username}의 블로거에 등록되었습니다: {body.get('url', '(비공개 초안)')}"
                        ))
                    else:
                        self.stdout.write(self.style.ERROR(
                            f"    ↳ [{account.user.username}] 블로거 API 응답 에러 ({res.status_code}): {str(body)[:300]}"
                        ))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"    ↳ [{account.user.username}] 네트워크 연동 실패: {str(e)}"))

        self.stdout.write(self.style.SUCCESS("🎉 오늘의 모든 회원 블로거 발행 프로세스가 종료되었습니다!"))
