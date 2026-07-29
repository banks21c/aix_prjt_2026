from django.db import migrations

# totalasset(국민연금·연금저축·IRP·ISA·보험·채권·부동산 통합 콘텐츠)을 /home/ubuntu/asset-management/
# 로 정적 이식하고 nginx가 /asset-management/ 경로로 서빙한다(저축/보험 정적 페이지와 동일한
# alias 방식). 당장은 기존 "저축"/"보험" 메뉴를 대체하지 않고, 그 오른쪽에 새 메뉴만 추가한다.
ASSET_MANAGEMENT_MENU = {'name': '자산관리', 'external_url': '/asset-management/', 'order': 8}


def seed_menu(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    for menu_type in ('INDEX', 'HEADER'):
        Menu.objects.get_or_create(
            menu_type=menu_type, name=ASSET_MANAGEMENT_MENU['name'],
            defaults={
                'external_url': ASSET_MANAGEMENT_MENU['external_url'],
                'order': ASSET_MANAGEMENT_MENU['order'],
            },
        )


def remove_menu(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=ASSET_MANAGEMENT_MENU['name']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0049_alter_blogpostingaccount_platform'),
    ]

    operations = [
        migrations.RunPython(seed_menu, remove_menu),
    ]
