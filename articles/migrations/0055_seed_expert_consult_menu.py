from django.db import migrations

# 자산관리(order=8) 오른쪽에 상담 유입 메뉴를 붙인다. external_url이 아니라 url_name을 쓰므로
# /experts/ 경로가 바뀌어도 Menu.get_url()의 reverse()가 따라간다.
EXPERT_CONSULT_MENU = {'name': '전문가 상담', 'url_name': 'expert_consult', 'order': 9}


def seed_menu(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    for menu_type in ('INDEX', 'HEADER'):
        Menu.objects.get_or_create(
            menu_type=menu_type, name=EXPERT_CONSULT_MENU['name'],
            defaults={
                'url_name': EXPERT_CONSULT_MENU['url_name'],
                'order': EXPERT_CONSULT_MENU['order'],
            },
        )


def remove_menu(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(
        name=EXPERT_CONSULT_MENU['name'], url_name=EXPERT_CONSULT_MENU['url_name'],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0054_consultrequest_asset_product'),
    ]

    operations = [
        migrations.RunPython(seed_menu, remove_menu),
    ]
