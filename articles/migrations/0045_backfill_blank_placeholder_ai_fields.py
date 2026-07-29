from django.db import migrations


def blank_placeholder_ai_fields(apps, schema_editor):
    AnalyzedArticle = apps.get_model('articles', 'AnalyzedArticle')
    # ai_generated=False인 기사는 실제 AI가 아니라 수집기가 채워 넣은 placeholder 문구였다 —
    # 이제 수집기가 더 이상 채우지 않으므로, 기존 데이터도 비워서 일관되게 만든다.
    AnalyzedArticle.objects.filter(ai_generated=False).update(
        ai_summary='', ai_analysis='', blog_content='',
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0044_alter_analyzedarticle_ai_analysis_and_more'),
    ]

    operations = [
        migrations.RunPython(blank_placeholder_ai_fields, noop_reverse),
    ]
