from django.db import migrations

# ISA 비교(0027_seed_menu)에 이어, 연금저축 비교 메뉴를 동일한 방식으로 추가한다.
MENU_ITEMS = [
    {'name': '연금저축 비교', 'url_name': 'pension_compare', 'badge_text': 'DEMO', 'order': 5},
]


def seed_menus(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    for menu_type in ('INDEX', 'HEADER'):
        for item in MENU_ITEMS:
            Menu.objects.create(menu_type=menu_type, **item)


def remove_seeded_menus(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    names = [item['name'] for item in MENU_ITEMS]
    Menu.objects.filter(name__in=names).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0027_seed_menu'),
    ]

    operations = [
        migrations.RunPython(seed_menus, remove_seeded_menus),
    ]
