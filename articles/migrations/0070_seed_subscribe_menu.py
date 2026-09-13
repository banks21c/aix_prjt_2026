from django.db import migrations

# 0055_seed_expert_consult_menu와 같은 패턴 — 전문가 상담(order=9) 옆에 구독 메뉴를 붙인다.
SUBSCRIBE_MENU = {'name': '구독', 'url_name': 'subscribe', 'order': 10}


def seed_menu(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    for menu_type in ('INDEX', 'HEADER'):
        Menu.objects.get_or_create(
            menu_type=menu_type, name=SUBSCRIBE_MENU['name'],
            defaults={
                'url_name': SUBSCRIBE_MENU['url_name'],
                'order': SUBSCRIBE_MENU['order'],
            },
        )


def remove_menu(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(
        name=SUBSCRIBE_MENU['name'], url_name=SUBSCRIBE_MENU['url_name'],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0069_subscriptionorder'),
    ]

    operations = [
        migrations.RunPython(seed_menu, remove_menu),
    ]
