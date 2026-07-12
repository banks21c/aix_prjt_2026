import urllib.request
import xml.etree.ElementTree as ET
from django.core.management.base import BaseCommand
from articles.models import NewsSource, NewsKeyword, AnalyzedArticle


class Command(BaseCommand):
    help = '등록된 RSS 사이트를 수집하여, 등록된 키워드에 매칭되는 기사만 DB에 저장합니다.'

    def handle(self, *args, **options):
        sources = NewsSource.objects.filter(is_active=True)
        keywords = list(NewsKeyword.objects.filter(is_active=True))

        if not sources.exists():
            self.stdout.write(self.style.WARNING("[-] 등록된 RSS 사이트가 없습니다. (관리자 페이지에서 NewsSource를 등록하세요)"))
            return

        if not keywords:
            self.stdout.write(self.style.WARNING("[-] 등록된 감지 키워드가 없습니다. (관리자 페이지에서 NewsKeyword를 등록하세요)"))
            return

        self.stdout.write(self.style.SUCCESS(f"🚀 RSS 사이트 {sources.count()}곳 / 키워드 {len(keywords)}개로 뉴스 수집을 시작합니다."))

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

                desc_el = item.find('description')
                description = desc_el.text.strip() if desc_el is not None and desc_el.text else ''

                haystack = f"{title} {description}"

                for keyword in keywords:
                    if keyword.keyword not in haystack:
                        continue

                    if AnalyzedArticle.objects.filter(original_url=link).exists():
                        break

                    self.stdout.write(self.style.SUCCESS(f"    ↳ [키워드 '{keyword.keyword}' 매칭] {title[:30]}..."))

                    AnalyzedArticle.objects.create(
                        stock=keyword.linked_stock,
                        matched_keyword=keyword,
                        title=title,
                        original_url=link,
                        source_media=source.name,
                        ai_summary=description[:300] if description else title,
                        ai_analysis=f"'{keyword.keyword}' 키워드로 {source.name}에서 수집된 기사입니다.",
                        blog_content=f'{title}\n\n원문 보기: <a href="{link}" target="_blank" rel="noopener noreferrer">{link}</a>',
                        applied_template='T1',
                        is_premium=False,
                        is_posted=False,
                    )
                    count += 1
                    break  # 기사 하나당 첫 매칭 키워드로만 저장

        if count == 0:
            self.stdout.write(self.style.WARNING("[-] 이번 수집에서 키워드에 매칭되는 새 기사가 없습니다."))
        else:
            self.stdout.write(self.style.SUCCESS(f"🎉 총 {count}건의 키워드 매칭 기사를 저장했습니다."))
