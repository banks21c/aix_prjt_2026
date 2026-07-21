from django.db import migrations

# 기존에 _header.html / index.html에 하드코딩되어 있던 메뉴 4개를 그대로 시드해서,
# Menu 테이블로 전환한 뒤에도 화면에 보이는 메뉴 구성이 바뀌지 않도록 한다.
MENU_ITEMS = [
    {'name': '뉴스 게시판', 'url_name': 'news_board', 'order': 1},
    {'name': '대시보드', 'url_name': 'main_dashboard', 'order': 2},
    {'name': '보험 비교', 'url_name': 'insurance_compare', 'badge_text': 'DEMO', 'order': 3},
    {'name': 'ISA 비교', 'url_name': 'isa_compare', 'badge_text': 'DEMO', 'order': 4},
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
        ('articles', '0026_menu'),
    ]

    operations = [
        migrations.RunPython(seed_menus, remove_seeded_menus),
    ]
