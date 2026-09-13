from django.db import migrations

# 이미 top-level 폴더가 있어 "완전 자동 선정" 후보(0001/0002)에서는 제외됐던 작가들이지만,
# 다작 작가라 대표작 몇 편을 제외한 나머지는 이 채널에서 아직 다루지 않았다. 로컬
# D:\1.VREW 파일 검색으로 이미 제작된(숏폼/롱폼 불문) 작품과 겹치지 않는 것만 골랐다.
CANDIDATES = [
    ("오노레 드 발자크", "나귀 가죽"),
    ("오노레 드 발자크", "절대의 탐구"),
    ("오노레 드 발자크", "농민들"),
    ("표도르 도스토옙스키", "학대받은 사람들"),
    ("레프 톨스토이", "크로이체르 소나타"),
    ("레프 톨스토이", "신부 세르게이"),
    ("레프 톨스토이", "하지 무라트"),
    ("레프 톨스토이", "악마"),
    ("레프 톨스토이", "가정의 행복"),
]


def seed_candidates(apps, schema_editor):
    LiteraryCandidate = apps.get_model('literary', 'LiteraryCandidate')
    for author, work in CANDIDATES:
        LiteraryCandidate.objects.get_or_create(author=author, work=work)


def remove_seeded_candidates(apps, schema_editor):
    LiteraryCandidate = apps.get_model('literary', 'LiteraryCandidate')
    for author, work in CANDIDATES:
        LiteraryCandidate.objects.filter(author=author, work=work, is_selected=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('literary', '0002_seed_candidates'),
    ]

    operations = [
        migrations.RunPython(seed_candidates, remove_seeded_candidates),
    ]
