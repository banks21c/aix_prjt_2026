import urllib.request
import xml.etree.ElementTree as ET
from django.core.management.base import BaseCommand
from articles.models import NewsSource, NewsKeyword, AnalyzedArticle
from articles.utils import detect_reuse_restriction, fetch_article_content, find_mentioned_stocks


class Command(BaseCommand):
    help = (
        '등록된 RSS 사이트를 수집하여, 등록된 키워드 또는 코스피200/코스닥150(is_major_index) '
        '종목명에 매칭되는 기사만 DB에 저장합니다. --full-universe를 주면 is_major_index 제한 '
        '없이 활성 전종목명으로 매칭 범위를 넓힌다(하루 수집량 실측용 임시 옵션 — 확인 후 '
        'crontab에서 플래그만 떼면 원래 범위로 되돌아간다).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--full-universe', action='store_true',
            help='is_major_index 350종목 대신 활성 전종목(약 2,783개)명으로 매칭 범위를 넓힌다.',
        )

    def handle(self, *args, **options):
        full_universe = options['full_universe']
        sources = NewsSource.objects.filter(is_active=True)
        keywords = list(NewsKeyword.objects.filter(is_active=True))

        if not sources.exists():
            self.stdout.write(self.style.WARNING("[-] 등록된 RSS 사이트가 없습니다. (관리자 페이지에서 NewsSource를 등록하세요)"))
            return

        scope_label = "활성 전종목" if full_universe else "코스피·코스닥 주요종목(350개)"
        self.stdout.write(self.style.SUCCESS(
            f"🚀 RSS 사이트 {sources.count()}곳 / 키워드 {len(keywords)}개 + {scope_label}명으로 "
            "뉴스 수집을 시작합니다."
        ))

        count = 0

        for source in sources:
            self.stdout.write(f"[-] [{source.name}] RSS 수집 시작...")

            try:
                req = urllib.request.Request(
                    source.rss_url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                )
                with urllib.request.urlopen(req, timeout=12) as response:
                    xml_content = response.read()

                root = ET.fromstring(xml_content)
                items = root.findall('.//item')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"    ↳ {source.name} 수집 실패: {e}"))
                continue

            if not items:
                self.stdout.write(self.style.WARNING(f"    ↳ {source.name} 피드에 기사가 없습니다."))
                continue

            for item in items:
                title_el = item.find('title')
                link_el = item.find('link')
                if title_el is None or link_el is None or not title_el.text or not link_el.text:
                    continue

                title = title_el.text.strip()
                link = link_el.text.strip()

                if AnalyzedArticle.objects.filter(original_url=link).exists():
                    continue

                desc_el = item.find('description')
                description = desc_el.text.strip() if desc_el is not None and desc_el.text else ''

                haystack = f"{title} {description}"

                # 등록된 감지 키워드를 먼저 보고, 안 걸리면 코스피200/코스닥150 종목명 언급
                # 여부로도 잡는다 — 관리자가 키워드를 일일이 등록하지 않아도 주요종목 기사는
                # 넓게 수집되게 하기 위함(키워드 3개뿐이라 본문 있는 기사가 너무 적다는 문제).
                matched_keyword = next((k for k in keywords if k.keyword in haystack), None)
                matched_stock = matched_keyword.linked_stock if matched_keyword else None
                match_label = f"키워드 '{matched_keyword.keyword}'" if matched_keyword else None

                if matched_keyword is None:
                    mentioned = find_mentioned_stocks(haystack, max_count=1, major_index_only=not full_universe)
                    if mentioned:
                        matched_stock = mentioned[0]
                        match_label = f"종목명 '{matched_stock.name}'"

                if match_label is None:
                    continue

                self.stdout.write(self.style.SUCCESS(f"    ↳ [{match_label} 매칭] {title[:30]}..."))

                fetched = fetch_article_content(link)
                content = fetched['content']
                AnalyzedArticle.objects.create(
                    stock=matched_stock,
                    matched_keyword=matched_keyword,
                    title=title,
                    original_url=link,
                    source_media=source.name,
                    source_type=AnalyzedArticle.SOURCE_RSS,
                    original_content=content,
                    has_reuse_restriction=detect_reuse_restriction(content),
                    applied_template='T1',
                    is_premium=fetched['is_premium'],
                    is_posted=False,
                )
                count += 1

        if count == 0:
            self.stdout.write(self.style.WARNING("[-] 이번 수집에서 매칭되는 새 기사가 없습니다."))
        else:
            self.stdout.write(self.style.SUCCESS(f"🎉 총 {count}건의 매칭 기사를 저장했습니다."))
