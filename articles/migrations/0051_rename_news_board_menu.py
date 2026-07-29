from django.db import migrations

# 0027_seed_menu에서 시드한 '뉴스 게시판' 메뉴명을 '경제 동향'으로 바꾼다.
# 이미 적용된 0027을 직접 고치면 기존 설치본에는 반영되지 않으므로, 별도 데이터
# 마이그레이션으로 기존 Menu 레코드(INDEX/HEADER 두 건)의 name만 갱신한다.
OLD_NAME = '뉴스 게시판'
NEW_NAME = '경제 동향'


def rename_forward(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=OLD_NAME).update(name=NEW_NAME)


def rename_backward(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=NEW_NAME).update(name=OLD_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0050_seed_asset_management_menu'),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
