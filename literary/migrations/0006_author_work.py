import django.db.models.deletion
from django.db import migrations, models

# LiteraryCandidate(작가·작품 문자열 한 줄)를 로컬 autovi와 같은 Author/Work 두 테이블로 나눈다.
# 기존 행의 제작완료·선택 상태는 그대로 옮기고, 소개·요약·분류·국적 등은 옮긴 뒤
# `manage.py seed_catalog` → `manage.py load_summaries`로 채운다.


def candidates_to_works(apps, schema_editor):
    LiteraryCandidate = apps.get_model('literary', 'LiteraryCandidate')
    Author = apps.get_model('literary', 'Author')
    Work = apps.get_model('literary', 'Work')
    authors = {}
    for c in LiteraryCandidate.objects.order_by('pk'):
        author = authors.get(c.author)
        if author is None:
            author = authors[c.author] = Author.objects.create(name=c.author, source=c.source)
        Work.objects.update_or_create(
            author=author, title=c.work,
            defaults={'is_produced': c.is_produced, 'is_selected': c.is_selected, 'selected_at': c.selected_at},
        )


def works_to_candidates(apps, schema_editor):
    LiteraryCandidate = apps.get_model('literary', 'LiteraryCandidate')
    Work = apps.get_model('literary', 'Work')
    for w in Work.objects.select_related('author'):
        LiteraryCandidate.objects.create(author=w.author.name, work=w.title, source=w.author.source,
                                         is_produced=w.is_produced, is_selected=w.is_selected,
                                         selected_at=w.selected_at)


class Migration(migrations.Migration):

    dependencies = [
        ('literary', '0005_produced_catalog_and_fixes'),
    ]

    operations = [
        migrations.CreateModel(
            name='Author',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True, verbose_name='작가')),
                ('source', models.CharField(blank=True, default='고전문학거장50인', max_length=100, verbose_name='출처')),
                ('summary', models.CharField(blank=True, max_length=300, verbose_name='작가 소개')),
                ('nationality', models.CharField(blank=True, help_text="목록의 작가 옆에 보인다. 두 나라에 걸치면 '러시아·미국'처럼, 고대는 '고대 그리스'처럼", max_length=50, verbose_name='국적')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='등록 일시')),
            ],
            options={
                'verbose_name': '작가',
                'verbose_name_plural': '작가 관리',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Work',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200, verbose_name='작품')),
                ('summary', models.CharField(blank=True, max_length=200, verbose_name='한 줄 요약')),
                ('kind', models.CharField(blank=True, choices=[('소설', [('장편소설', '장편소설'), ('중편소설', '중편소설'), ('단편소설', '단편소설'), ('연작소설', '연작소설'), ('단편집', '단편집'), ('운문소설', '운문소설')]), ('동화·설화', [('동화', '동화'), ('동화집', '동화집'), ('설화집', '설화집')]), ('시·희곡', [('서사시', '서사시'), ('시집', '시집'), ('시', '시'), ('희곡', '희곡')]), ('에세이·기록', [('에세이', '에세이'), ('논설', '논설'), ('자서전', '자서전'), ('회고록', '회고록'), ('기행문', '기행문')]), ('철학·사상', [('대화편', '대화편'), ('철학서', '철학서'), ('사상서', '사상서'), ('경전', '경전'), ('신앙서', '신앙서')]), ('역사·학술', [('역사서', '역사서'), ('학술서', '학술서'), ('교양서', '교양서'), ('평론', '평론')])], help_text='장편소설·단편소설·희곡·에세이처럼 작품의 갈래와 분량', max_length=10, verbose_name='분류')),
                ('is_domestic', models.BooleanField(default=False, help_text='한국 작가·한국 고전이면 체크(목록의 전체/국내/국외 필터)', verbose_name='국내 작품')),
                ('priority', models.PositiveSmallIntegerField(choices=[(0, '—'), (1, '1순위'), (2, '2순위'), (3, '3순위'), (4, '4순위'), (5, '5순위')], default=0, help_text='먼저 만들 차례(1순위가 가장 먼저). 0이면 정하지 않음', verbose_name='제작 우선순위')),
                ('is_produced', models.BooleanField(default=False, verbose_name='제작완료')),
                ('produced_at', models.DateField(blank=True, null=True, verbose_name='영상 제작일')),
                ('uploaded_at', models.DateField(blank=True, null=True, verbose_name='영상 업로드일')),
                ('youtube_video_id', models.CharField(blank=True, max_length=32, verbose_name='유튜브 videoId')),
                ('is_selected', models.BooleanField(default=False, verbose_name='다음 작품으로 선택됨')),
                ('selected_at', models.DateTimeField(blank=True, null=True, verbose_name='선택 일시')),
                ('note', models.TextField(blank=True, verbose_name='메모')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='등록 일시')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='수정 일시')),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='works', to='literary.author', verbose_name='작가')),
            ],
            options={
                'verbose_name': '작품',
                'verbose_name_plural': '작품 관리',
                'ordering': ['author__name', 'title'],
                'constraints': [models.UniqueConstraint(fields=('author', 'title'), name='unique_author_work')],
            },
        ),
        migrations.RunPython(candidates_to_works, works_to_candidates),
        migrations.DeleteModel(
            name='LiteraryCandidate',
        ),
    ]
