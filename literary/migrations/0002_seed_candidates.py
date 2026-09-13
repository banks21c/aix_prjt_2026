from django.db import migrations

# 고전문학거장50인.txt 50명 중 채널이 이미 다룬(기존 작가 폴더/업로드 이력이 있는) 작가를
# 제외하고 남은 25개 작가·작품 후보. D:\1.VREW\.claude\skills\produce-episode\SKILL.md
# 1단계와 같은 목록.
CANDIDATES = [
    ("제인 오스틴", "오만과 편견"),
    ("제인 오스틴", "이성과 감성"),
    ("마르셀 프루스트", "잃어버린 시간을 찾아서"),
    ("제임스 조이스", "율리시스"),
    ("제임스 조이스", "젊은 예술가의 초상"),
    ("토마스 만", "마의 산"),
    ("토마스 만", "부덴브로크가의 사람들"),
    ("너새니얼 호손", "주홍글씨"),
    ("버지니아 울프", "등대로"),
    ("버지니아 울프", "댈러웨이 부인"),
    ("조지 엘리엇", "미들마치"),
    ("블라디미르 나보코프", "롤리타"),
    ("윌리엄 포크너", "음향과 분노"),
    ("나쓰메 소세키", "마음"),
    ("나쓰메 소세키", "나는 고양이로소이다"),
    ("이광수", "무정"),
    ("루쉰", "아Q정전"),
    ("로런스 스턴", "트리스트럼 샌디"),
    ("헨리 제임스", "여인의 초상"),
    ("이디스 워튼", "순수의 시대"),
    ("D.H. 로런스", "채털리 부인의 연인"),
    ("미하일 불가코프", "거장과 마르가리타"),
    ("앙드레 지드", "좁은 문"),
    ("펄 벅", "대지"),
    ("마거릿 미첼", "바람과 함께 사라지다"),
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
        ('literary', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_candidates, remove_seeded_candidates),
    ]
