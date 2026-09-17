"""literary/catalog_data.py의 작가·작품 카탈로그를 Author/Work로 넣는다.
이미 있는 작가·작품(공백·문장부호를 뺀 제목이 같은 것)은 건드리지 않으므로 여러 번 실행해도 된다."""
import re
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import router, transaction

from literary.catalog_data import (
    CATALOG, FOLKTALES, FOLKTALES_SOURCE, FOREIGN_FOLKTALES, FOREIGN_FOLKTALES_SOURCE,
    ILDANGBAEK, ILDANGBAEK_SOURCE, NOTES, PRODUCED,
)
from literary.models import Author, Work


def _norm(title):
    return re.sub(r"[\s\W_]", "", title)


class Command(BaseCommand):
    help = "작가·작품 카탈로그(literary/catalog_data.py)를 DB에 넣는다."

    def handle(self, *args, **options):
        # 트랜잭션은 Work가 저장되는 DB에 건다(nextfinup에서는 라우터가 autovi_db로 보낸다).
        with transaction.atomic(using=router.db_for_write(Work)):
            return self._handle(*args, **options)

    def _handle(self, *args, **options):
        catalog_keys = {(a, w) for _, authors in CATALOG for a, works in authors for w in works}
        unknown = (set(PRODUCED) | set(NOTES)) - catalog_keys
        if unknown:
            raise CommandError(f"PRODUCED/NOTES에 있지만 CATALOG에 없는 항목: {sorted(unknown)}")

        new_authors = new_works = 0
        kept_unproduced = []  # 이미 있던 작품인데 채널 업로드 기록상 제작된 것
        for source, authors in CATALOG:
            for author_name, titles in authors:
                author, created = Author.objects.get_or_create(name=author_name, defaults={'source': source})
                new_authors += created
                existing = {_norm(w.title): w for w in author.works.all()}
                for title in titles:
                    found = existing.get(_norm(title))
                    produced = PRODUCED.get((author_name, title))
                    if found:
                        if produced and not found.is_produced:
                            kept_unproduced.append(f"{author_name} 『{found.title}』(업로드 {produced[0]})")
                        continue
                    uploaded_at, produced_at, note = produced or (None, None, NOTES.get((author_name, title), ""))
                    work = Work.objects.create(
                        author=author, title=title,
                        is_produced=bool(produced),
                        uploaded_at=date.fromisoformat(uploaded_at) if uploaded_at else None,
                        produced_at=date.fromisoformat(produced_at) if produced_at else None,
                        note=note,
                    )
                    existing[_norm(title)] = work
                    new_works += 1

        # 일당백 작품목록: 같은 작품이 여러 회차에 나오면 회차 번호를 모아 메모 하나로 남긴다.
        ildangbaek = {}
        for no, author_name, title in ILDANGBAEK:
            ildangbaek.setdefault((author_name, title), []).append(no)
        ildangbaek_new = ildangbaek_existing = 0
        for (author_name, title), nos in ildangbaek.items():
            author, created = Author.objects.get_or_create(name=author_name, defaults={'source': ILDANGBAEK_SOURCE})
            new_authors += created
            if any(_norm(w.title) == _norm(title) for w in author.works.all()):
                ildangbaek_existing += 1
                continue
            Work.objects.create(author=author, title=title,
                                note="일당백 작품목록 v3 " + ", ".join(f"#{n}" for n in nos))
            ildangbaek_new += 1
        new_works += ildangbaek_new
        self.stdout.write(f"일당백 작품목록: {len(ildangbaek)}건 중 새로 추가 {ildangbaek_new}건, 이미 있음 {ildangbaek_existing}건")

        # 한글 전래동화 3대 원전: 엮은이를 작가로, 각 편을 작품으로. 전부 우리 전래동화라 분류(동화)와
        # 국내 표시를 summaries.json에 171줄씩 늘어놓는 대신 여기서 바로 정한다.
        folk_new = folk_existing = 0
        for book, no, compiler, title, remark in FOLKTALES:
            author, created = Author.objects.get_or_create(name=compiler, defaults={'source': FOLKTALES_SOURCE})
            new_authors += created
            if any(_norm(w.title) == _norm(title) for w in author.works.all()):
                folk_existing += 1
                continue
            note = f"{book} {no}번" + (f" — {remark}" if remark else "")
            Work.objects.create(author=author, title=title, kind='동화', is_domestic=True, note=note)
            folk_new += 1
        new_works += folk_new
        self.stdout.write(f"한글 전래동화 3대 원전: {len(FOLKTALES)}편 중 새로 추가 {folk_new}건, 이미 있음 {folk_existing}건")

        # 외국 무서운 전래동화: 그림·페로·아파나시예프 원전에서 무섭거나 잔혹한 편만. 한글 3대 원전과 같이
        # 전래동화 제작 화면에 나온다(작가의 출처로 골라낸다). 엮은이가 이미 다른 출처로 있으면
        # (그림 형제·샤를 페로는 일당백 작품목록에도 있다) 출처를 이쪽으로 옮겨 그 화면에 함께 나오게 한다.
        foreign_new = foreign_existing = foreign_reprioritized = 0
        moved = []
        for book, no, compiler, title, remark, priority in FOREIGN_FOLKTALES:
            author, created = Author.objects.get_or_create(name=compiler,
                                                           defaults={'source': FOREIGN_FOLKTALES_SOURCE})
            new_authors += created
            if not created and author.source != FOREIGN_FOLKTALES_SOURCE:
                moved.append(f"{author.name}({author.source} → {FOREIGN_FOLKTALES_SOURCE})")
                author.source = FOREIGN_FOLKTALES_SOURCE
                author.save(update_fields=['source'])
            found = next((w for w in author.works.all() if _norm(w.title) == _norm(title)), None)
            if found:
                foreign_existing += 1
                # 작품은 그대로 두되 우선순위는 이 표가 정본이라 바뀌면 맞춰 준다.
                if found.priority != priority:
                    found.priority = priority
                    found.save(update_fields=['priority', 'updated_at'])
                    foreign_reprioritized += 1
                continue
            note = " ".join(part for part in (book, no) if part) + (f" — {remark}" if remark else "")
            Work.objects.create(author=author, title=title, kind='동화', is_domestic=False, note=note,
                                priority=priority)
            foreign_new += 1
        new_works += foreign_new
        self.stdout.write(f"외국 무서운 전래동화: {len(FOREIGN_FOLKTALES)}편 중 새로 추가 {foreign_new}건, "
                          f"이미 있음 {foreign_existing}건(우선순위 갱신 {foreign_reprioritized}건)")
        if moved:
            self.stdout.write("전래동화 출처로 옮긴 작가: " + ", ".join(dict.fromkeys(moved)))

        self.stdout.write(self.style.SUCCESS(
            f"작가 {new_authors}명·작품 {new_works}건 추가 → 전체 작가 {Author.objects.count()}명, "
            f"작품 {Work.objects.count()}건(제작완료 {Work.objects.filter(is_produced=True).count()}건)"))
        if kept_unproduced:
            self.stdout.write("이미 있던 작품이라 그대로 둔 것 중 채널 업로드 기록이 있는 작품(미제작 → 확인 필요):")
            for line in kept_unproduced:
                self.stdout.write(f"  {line}")
