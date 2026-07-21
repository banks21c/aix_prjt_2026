from django.db import migrations

# ISA 비교는 IRP(0030)와 같은 이유로 nextfinup Django 프로젝트에서 분리되어
# /home/ubuntu/isa(정적 HTML, nginx가 /isa/ 경로로 직접 서빙)로 옮겨졌다.
# 더 이상 Django 뷰/URL이 없으므로 url_name을 비우고 external_url로 전환해
# 메뉴 링크만 그대로 살려둔다.
TARGET_NAME = 'ISA 비교'


def switch_to_external_link(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=TARGET_NAME).update(url_name='', external_url='/isa/')


def switch_back_to_url_name(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=TARGET_NAME).update(url_name='isa_compare', external_url='')


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0030_irp_menu_external_link'),
    ]

    operations = [
        migrations.RunPython(switch_to_external_link, switch_back_to_url_name),
    ]
