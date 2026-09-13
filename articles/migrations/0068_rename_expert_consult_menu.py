from django.db import migrations

# 0055_seed_expert_consult_menu가 심어둔 메뉴명을 "전문가 상담"에서 "전문가 소개"로 바꾼다.
# url_name/order는 그대로이므로 링크는 변하지 않는다.
OLD_NAME = '전문가 상담'
NEW_NAME = '전문가 소개'
URL_NAME = 'expert_consult'


def rename_menu(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=OLD_NAME, url_name=URL_NAME).update(name=NEW_NAME)


def revert_menu(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=NEW_NAME, url_name=URL_NAME).update(name=OLD_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0067_marketindex_foreign_net_amount_and_more'),
    ]

    operations = [
        migrations.RunPython(rename_menu, revert_menu),
    ]
