from django.db import migrations

# 0091에서 "유틸" 메뉴를 char_counter로 직접 연결하고 호버 드롭다운으로 만들었었는데, 사용자
# 요청으로 자산관리(/asset-management/)와 같은 방식 — 클릭하면 허브 페이지(/tools/)로 가고,
# 그 페이지 안에서 상단 탭(_tools_tabs.html)으로 개별 유틸을 전환 — 으로 바꾼다. 그래서 메뉴
# 링크만 tools_hub로 바꾸면 되고(드롭다운 마크업은 _nav_menu_loop.html에서 제거), 이후 유틸을
# 추가할 때도 이 메뉴 자체는 건드릴 필요가 없다.
MENU_NAME = '유틸'
OLD_URL_NAME = 'char_counter'
NEW_URL_NAME = 'tools_hub'


def point_to_hub(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=MENU_NAME).update(url_name=NEW_URL_NAME)


def point_to_char_counter(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=MENU_NAME).update(url_name=OLD_URL_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0091_seed_utools_menu_and_hide_ai_performance'),
    ]

    operations = [
        migrations.RunPython(point_to_hub, point_to_char_counter),
    ]
