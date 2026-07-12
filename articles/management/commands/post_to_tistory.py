import requests
from django.core.management.base import BaseCommand
from articles.blog_posting import build_post_content, enabled_accounts, select_candidates
from articles.models import PostedArticle

TISTORY_WRITE_URL = "https://www.tistory.com/apis/post/write"


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
                blog_title, full_html_content, subject_label = build_post_content(article)
                payload = {
                    # 티스토리 오픈 API: account_id=블로그 이름(예: nextfinup), credential=API 액세스 토큰
                    "access_token": account.credential,
                    "output": "json",
                    "blogName": account.account_id,
                    "title": blog_title,
                    "content": full_html_content,
                    "visibility": 3,  # 3: 발행(공개), 0: 비공개
                    "category": 0,
                    "tag": f"{subject_label}, 경제뉴스, AI투자, 테크핀",
                }
                try:
                    res = requests.post(TISTORY_WRITE_URL, data=payload, timeout=15).json()
                    tistory_res = res.get("tistory", {})
                    if tistory_res.get("status") == "200":
                        PostedArticle.objects.create(
                            blog_account=account, article=article, external_url=tistory_res.get('url', ''),
                        )
                        self.stdout.write(self.style.SUCCESS(
                            f"    ↳ [발행 성공] {subject_label} 글이 {account.user.username}의 티스토리에 등록되었습니다: {tistory_res.get('url')}"
                        ))
                    else:
                        self.stdout.write(self.style.ERROR(
                            f"    ↳ [{account.user.username}] 티스토리 API 응답 에러: {res}"
                        ))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"    ↳ [{account.user.username}] 네트워크 연동 실패: {str(e)}"))

        self.stdout.write(self.style.SUCCESS("🎉 오늘의 모든 회원 티스토리 발행 프로세스가 종료되었습니다!"))
