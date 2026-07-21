from django.db import migrations

# ISA/연금저축/IRP 3개 개별 메뉴를 "저축" 드롭다운 메뉴 하나로 묶는다. 드롭다운 하위 링크는
# insurance-guide 드롭다운과 동일하게 템플릿에 하드코딩하므로(_header.html/index.html), 개별
# 메뉴 3개는 is_active=False로 숨기고(삭제 아님 — 필요하면 관리자 화면에서 다시 켤 수 있음),
# "저축"이라는 새 메뉴가 그 자리(order=4)를 대신하며 기본 이동 경로는 ISA로 둔다.
CHILD_NAMES = ['ISA', '연금저축', 'IRP']
SAVINGS_MENU = {'name': '저축', 'external_url': '/isa/', 'order': 4}
INSURANCE_RENAME = ('보험 가이드', '보험')


def apply_changes(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name__in=CHILD_NAMES).update(is_active=False)
    Menu.objects.filter(name=INSURANCE_RENAME[0]).update(name=INSURANCE_RENAME[1])
    for menu_type in ('INDEX', 'HEADER'):
        Menu.objects.get_or_create(
            menu_type=menu_type, name=SAVINGS_MENU['name'],
            defaults={'external_url': SAVINGS_MENU['external_url'], 'order': SAVINGS_MENU['order']},
        )


def revert_changes(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=SAVINGS_MENU['name']).delete()
    Menu.objects.filter(name=INSURANCE_RENAME[1]).update(name=INSURANCE_RENAME[0])
    Menu.objects.filter(name__in=CHILD_NAMES).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0039_seed_grade_limits_and_admin_grade'),
    ]

    operations = [
        migrations.RunPython(apply_changes, revert_changes),
    ]
