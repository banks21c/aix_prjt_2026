from django.db import migrations

# 회원 권한 등급 5단계 초기값. 등급 자체는 이후 Admin 화면(MemberGrade)에서 자유롭게
# 추가/수정/삭제할 수 있으며, 이 5개는 어디까지나 시작점이다.
GRADES = [
    {'level': 1, 'name': '브론즈', 'description': '기본 등급 (가입 시 기본값)'},
    {'level': 2, 'name': '실버', 'description': ''},
    {'level': 3, 'name': '골드', 'description': ''},
    {'level': 4, 'name': '플래티넘', 'description': ''},
    {'level': 5, 'name': '다이아몬드', 'description': '최상위 등급'},
]


def seed_grades(apps, schema_editor):
    MemberGrade = apps.get_model('articles', 'MemberGrade')
    for grade in GRADES:
        MemberGrade.objects.get_or_create(level=grade['level'], defaults=grade)


def remove_seeded_grades(apps, schema_editor):
    MemberGrade = apps.get_model('articles', 'MemberGrade')
    names = [grade['name'] for grade in GRADES]
    MemberGrade.objects.filter(name__in=names).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0036_membergrade'),
    ]

    operations = [
        migrations.RunPython(seed_grades, remove_seeded_grades),
    ]
