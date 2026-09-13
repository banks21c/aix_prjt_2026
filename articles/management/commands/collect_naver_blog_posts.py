"""네이버 블로그 글을 NaverBlogPost 테이블로 수집한다(이관 1단계).

RSS로 글 목록을 먼저 만들고(행 생성/갱신), 이어서 본문이 아직 없는 글만 PostView에서
긁어 본문 HTML + 재호스팅한 이미지를 채운다. (blog_id, log_no)로 기존 행을 찾아
갱신하므로 몇 번을 다시 돌려도 안전하다.

    python manage.py collect_naver_blog_posts soonks21
    python manage.py collect_naver_blog_posts soonks21 --owner soonks21@gmail.com --limit 5
    python manage.py collect_naver_blog_posts soonks21 --rescrape      # 이미 받은 본문도 다시
    python manage.py collect_naver_blog_posts soonks21 --list-only     # 목록만(본문/이미지 생략)
"""

import time

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from articles import naver_migration
from articles.models import NaverBlogPost


class Command(BaseCommand):
    help = '네이버 블로그(RSS + PostView)를 긁어 NaverBlogPost 테이블에 원문을 적재합니다.'

    def add_arguments(self, parser):
        parser.add_argument('blog_id', help='네이버 블로그 ID (예: soonks21)')
        parser.add_argument('--owner', default='', help='이 블로그를 소유한 회원의 username 또는 이메일')
        parser.add_argument('--limit', type=int, default=0, help='본문을 수집할 최대 글 수(0=제한 없음)')
        parser.add_argument('--log-no', default='', help='이 글 번호 하나만 다시 수집(개별 실패 재시도용)')
        parser.add_argument('--rescrape', action='store_true', help='이미 본문이 있는 글도 다시 수집')
        parser.add_argument('--list-only', action='store_true', help='RSS 목록만 적재하고 본문은 건너뜀')
        parser.add_argument('--no-images', action='store_true', help='이미지 재호스팅 없이 본문만 저장')
        # 과거 이관 때 요청 간격을 1초로 뒀다가 Cloudflare 429를 맞은 적이 있어(발행 쪽이긴
        # 하지만 같은 성격) 기본값을 넉넉히 잡는다.
        parser.add_argument('--sleep', type=float, default=2.0, help='글 사이 대기 시간(초)')

    def handle(self, *args, **options):
        blog_id = options['blog_id'].strip()
        owner = self._resolve_owner(options['owner'])
        session = naver_migration._session()

        self.stdout.write(self.style.SUCCESS(f'🚀 [{blog_id}] RSS 목록 수집 시작'))
        try:
            items = naver_migration.fetch_post_list(blog_id, session=session)
        except Exception as e:
            raise CommandError(f'RSS 목록 수집 실패: {e}')

        if not items:
            self.stdout.write(self.style.WARNING('[-] RSS에서 글을 찾지 못했습니다. 블로그 ID를 확인하세요.'))
            return

        created = 0
        for item in items:
            post, is_new = NaverBlogPost.objects.get_or_create(
                blog_id=blog_id, log_no=item['log_no'],
                defaults={
                    'title': item['title'], 'source_url': item['source_url'],
                    'category_name': item['category_name'], 'posted_at': item['posted_at'],
                    'owner': owner, 'status': 'LISTED',
                },
            )
            created += int(is_new)
            if not is_new:
                # 제목/카테고리는 네이버에서 수정될 수 있으니 목록 정보는 매번 최신으로 덮는다.
                # 본문(content_html)은 여기서 건드리지 않는다.
                post.title = item['title'] or post.title
                post.category_name = item['category_name'] or post.category_name
                post.posted_at = item['posted_at'] or post.posted_at
                if owner and not post.owner_id:
                    post.owner = owner
                post.save(update_fields=['title', 'category_name', 'posted_at', 'owner', 'updated_at'])

        self.stdout.write(f'[-] 목록 {len(items)}건 확인 (신규 {created}건)')

        if options['list_only']:
            self.stdout.write(self.style.SUCCESS('✅ --list-only: 본문 수집은 건너뜁니다.'))
            return

        targets = NaverBlogPost.objects.filter(blog_id=blog_id)
        if options['log_no']:
            # 개별 재시도(이미지 다운로드가 일시적으로 실패한 글 등) — 상태와 무관하게 이 글만.
            targets = targets.filter(log_no=options['log_no'])
        elif not options['rescrape']:
            targets = targets.exclude(status='SCRAPED')
        targets = targets.order_by('posted_at', 'id')
        if options['limit']:
            targets = targets[:options['limit']]
        targets = list(targets)

        self.stdout.write(self.style.SUCCESS(f'📝 본문 수집 대상 {len(targets)}건'))
        ok = fail = 0
        for idx, post in enumerate(targets, start=1):
            try:
                detail = naver_migration.fetch_post_detail(
                    blog_id, post.log_no, session=session,
                    download_images=not options['no_images'],
                )
            except Exception as e:
                fail += 1
                post.status = 'FAILED'
                post.error_message = str(e)[:500]
                post.save(update_fields=['status', 'error_message', 'updated_at'])
                self.stdout.write(self.style.ERROR(f'  [{idx}/{len(targets)}] {post.log_no} 실패: {e}'))
            else:
                ok += 1
                post.title = detail['title'] or post.title
                post.content_html = detail['content_html']
                post.original_content_html = detail['original_html']
                post.excerpt = detail['excerpt']
                # --no-images로 돌린 회차가 기존 매핑을 지워버리지 않게 병합한다.
                post.image_map = {**(post.image_map or {}), **detail['image_map']}
                post.image_count = detail['image_count']
                post.thumbnail_url = detail['thumbnail_url'] or post.thumbnail_url
                post.status = 'SCRAPED'
                post.error_message = ''
                post.scraped_at = timezone.now()
                post.save()
                self.stdout.write(
                    f"  [{idx}/{len(targets)}] {post.log_no} OK "
                    f"(이미지 {detail['image_count']}장, 본문 {len(detail['content_html'])}자)"
                )
            if idx < len(targets) and options['sleep'] > 0:
                time.sleep(options['sleep'])

        self.stdout.write(self.style.SUCCESS(f'✅ 완료 — 성공 {ok}건 / 실패 {fail}건'))

    def _resolve_owner(self, value):
        if not value:
            return None
        user = User.objects.filter(username=value).first() or User.objects.filter(email=value).first()
        if not user:
            raise CommandError(f'회원을 찾지 못했습니다: {value}')
        return user
