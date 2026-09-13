from django.db import migrations
from django.db.models import F

# 지금까지 모든 MemberGrade가 daily_ai_summarize_limit=NULL(무제한)로 비어 있었다 — gpt-4o-mini
# 호출 비용이 드는 AI 요약이 무료 등급조차 무제한이었던 셈이라, 프리미엄(하루 10건 고정)보다
# 무료 회원이 오히려 더 관대해지는 역전이 생긴다. daily_scrape_limit(3/6/9/12/15, 관리자는
# NULL 유지)과 같은 값으로 채워 등급 체계를 다시 앞뒤가 맞게 만든다. 이미 관리자가 값을
# 넣어둔 행(NULL이 아닌 행)은 건드리지 않는다.


def set_default_limits(apps, schema_editor):
    MemberGrade = apps.get_model('articles', 'MemberGrade')
    MemberGrade.objects.filter(daily_ai_summarize_limit__isnull=True).update(
        daily_ai_summarize_limit=F('daily_scrape_limit')
    )


def noop_reverse(apps, schema_editor):
    # 원래 값이 전부 NULL이었다는 것만 알 뿐 어떤 행이 이번에 바뀌었는지 구분할 수 없어
    # 정확히 되돌릴 수 없다 — 되돌릴 필요가 생기면 관리자 화면에서 직접 비우면 된다.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0070_seed_subscribe_menu'),
    ]

    operations = [
        migrations.RunPython(set_default_limits, noop_reverse),
    ]
