from django.db import migrations

# 기존 5개 등급에 일일 스크래핑/포스팅 한도를 부여하고, 두 값 모두 무제한(NULL)인 관리자 등급을 추가한다.
GRADE_LIMITS = {
    1: 3,   # 브론즈
    2: 6,   # 실버
    3: 9,   # 골드
    4: 12,  # 플래티넘
    5: 15,  # 다이아몬드
}

ADMIN_GRADE = {'level': 6, 'name': '관리자', 'description': '스크래핑/포스팅 무제한'}


def seed_limits(apps, schema_editor):
    MemberGrade = apps.get_model('articles', 'MemberGrade')
    for level, limit in GRADE_LIMITS.items():
        MemberGrade.objects.filter(level=level).update(
            daily_scrape_limit=limit, daily_post_limit=limit,
        )
    MemberGrade.objects.get_or_create(
        level=ADMIN_GRADE['level'],
        defaults={
            'name': ADMIN_GRADE['name'],
            'description': ADMIN_GRADE['description'],
            'daily_scrape_limit': None,
            'daily_post_limit': None,
        },
    )


def revert_limits(apps, schema_editor):
    MemberGrade = apps.get_model('articles', 'MemberGrade')
    MemberGrade.objects.filter(level__in=GRADE_LIMITS.keys()).update(
        daily_scrape_limit=None, daily_post_limit=None,
    )
    MemberGrade.objects.filter(level=ADMIN_GRADE['level'], name=ADMIN_GRADE['name']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0038_grade_limits_and_scraped_by'),
    ]

    operations = [
        migrations.RunPython(seed_limits, revert_limits),
    ]
