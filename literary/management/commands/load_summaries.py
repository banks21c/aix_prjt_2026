"""literary/data/summaries.json의 작가 소개·작품 한 줄 요약을 DB에 넣는다.
기본은 비어 있는 칸만 채운다(관리자 화면에서 직접 고친 내용을 덮어쓰지 않음). --overwrite면 파일 내용으로 덮어쓴다.

summaries.json 형식: {"authors": {"작가": "소개"}, "nationalities": {"작가": "프랑스"},
                    "works": {"작가|작품": "한 줄 요약"}, "kinds": {"작가|작품": "장편소설"},
                    "domestic": ["작가|작품", ...]}
kinds 값은 Work.kind의 선택지 중 하나, domestic은 국내 작품(Work.is_domestic) 목록."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from literary.models import KIND_CHOICES, Author, Work

DEFAULT_PATH = Path(settings.BASE_DIR) / 'literary' / 'data' / 'summaries.json'


class Command(BaseCommand):
    help = "작가 소개·작품 한 줄 요약·분류(literary/data/summaries.json)를 DB에 넣는다."

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', default=str(DEFAULT_PATH))
        parser.add_argument('--overwrite', action='store_true', help="이미 있는 내용도 파일 내용으로 덮어쓴다")

    @transaction.atomic
    def handle(self, *args, **options):
        data = json.loads(Path(options['path']).read_text(encoding='utf-8'))
        overwrite = options['overwrite']
        filled = {'authors': 0, 'works': 0}
        missing = []

        authors = {a.name: a for a in Author.objects.all()}
        for name, summary in data.get('authors', {}).items():
            author = authors.get(name)
            if not author:
                missing.append(f"작가 {name}")
            elif summary and (overwrite or not author.summary):
                author.summary = summary[:300]
                author.save(update_fields=['summary'])
                filled['authors'] += 1

        filled['nationalities'] = 0
        for name, nationality in data.get('nationalities', {}).items():
            author = authors.get(name)
            if not author:
                missing.append(f"국적 {name}")
            elif nationality and (overwrite or not author.nationality):
                author.nationality = nationality[:50]
                author.save(update_fields=['nationality'])
                filled['nationalities'] += 1

        works = {f"{w.author.name}|{w.title}": w for w in Work.objects.select_related('author')}
        for key, summary in data.get('works', {}).items():
            work = works.get(key)
            if not work:
                missing.append(f"작품 {key}")
            elif summary and (overwrite or not work.summary):
                work.summary = summary[:200]
                work.save(update_fields=['summary', 'updated_at'])
                filled['works'] += 1

        valid_kinds = {value for _, group in KIND_CHOICES for value, _ in group}
        filled['kinds'] = 0
        for key, kind in data.get('kinds', {}).items():
            work = works.get(key)
            if not work:
                missing.append(f"분류 {key}")
            elif kind not in valid_kinds:
                missing.append(f"분류 {key}(알 수 없는 값 {kind})")
            elif overwrite or not work.kind:
                work.kind = kind
                work.save(update_fields=['kind', 'updated_at'])
                filled['kinds'] += 1

        # 국내 작품 목록 — 목록에 있는 작품만 국내로 표시한다(없는 작품을 국외로 되돌리지는 않음).
        filled['domestic'] = 0
        for key in data.get('domestic', []):
            work = works.get(key)
            if not work:
                missing.append(f"국내 {key}")
            elif not work.is_domestic:
                work.is_domestic = True
                work.save(update_fields=['is_domestic', 'updated_at'])
                filled['domestic'] += 1

        self.stdout.write(self.style.SUCCESS(
            f"작가 소개 {filled['authors']}명·국적 {filled['nationalities']}명·작품 요약 {filled['works']}건·"
            f"분류 {filled['kinds']}건·국내 표시 {filled['domestic']}건 채움 — 비어 있는 작가 "
            f"{Author.objects.filter(summary='').count()}명, 국적 {Author.objects.filter(nationality='').count()}명, 작품 요약 {Work.objects.filter(summary='').count()}건, "
            f"분류 {Work.objects.filter(kind='').count()}건"))
        if missing:
            self.stdout.write(f"DB에 없어 건너뛴 항목 {len(missing)}개: " + ", ".join(missing[:20]))
