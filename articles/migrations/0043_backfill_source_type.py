from django.db import migrations


def backfill_source_type(apps, schema_editor):
    AnalyzedArticle = apps.get_model('articles', 'AnalyzedArticle')
    AnalyzedArticle.objects.filter(original_url__startswith='kis-news://').update(source_type='KIS')
    AnalyzedArticle.objects.filter(original_url__startswith='internal://featured-briefing/').update(source_type='AI_BRIEFING')
    # 나머지(RSS/회원 직접 등록)는 필드 default가 이미 'RSS'라 손댈 것이 없다.


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0042_analyzedarticle_source_type'),
    ]

    operations = [
        migrations.RunPython(backfill_source_type, noop_reverse),
    ]
