from django.db import migrations

# FAQ 바로 뒤에 새 "유틸" 메뉴를 추가한다(order=12, 저축/보험처럼 드롭다운으로 확장 —
# 지금은 글자수세기 하나뿐이지만 이후 유틸을 하나씩 추가하는 진입점). 동시에 ai_performance_view가
# staff_member_required로 바뀌어(2026-08-23) 일반 방문자에게는 접근 불가능해졌으므로, 클릭하면
# 관리자 로그인으로 튕기기만 하는 "AI 예측 성과" 공개 메뉴 링크는 숨긴다(삭제 대신 다른 deprecated
# 항목들처럼 is_active=False로 — 필요하면 관리자 화면에서 되살릴 수 있게).
UTOOLS_MENU = {'name': '유틸', 'url_name': 'char_counter', 'order': 12}
AI_PERFORMANCE_MENU_NAME = 'AI 예측 성과'


def migrate_menus(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    for menu_type in ('INDEX', 'HEADER'):
        Menu.objects.get_or_create(
            menu_type=menu_type, name=UTOOLS_MENU['name'],
            defaults={'url_name': UTOOLS_MENU['url_name'], 'order': UTOOLS_MENU['order']},
        )
    Menu.objects.filter(name=AI_PERFORMANCE_MENU_NAME).update(is_active=False)


def revert_menus(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=UTOOLS_MENU['name']).delete()
    Menu.objects.filter(name=AI_PERFORMANCE_MENU_NAME).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0090_generatedimage'),
    ]

    operations = [
        migrations.RunPython(migrate_menus, revert_menus),
    ]
