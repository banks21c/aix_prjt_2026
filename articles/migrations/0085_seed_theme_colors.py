from django.db import migrations

# articles/static/articles/theme.css에 있던 값을 그대로 옮긴 초기 시드 데이터.
# (var 이름, 색상값, 설명, 그룹, 정렬순서)
SEED = [
    ("--brand-primary", "#0d47a1", "브랜드 기본색 (버튼/링크/제목)", "BRAND", 1),
    ("--brand-primary-hover", "#0b3c8a", "브랜드 기본색 hover", "BRAND", 2),
    ("--brand-accent", "#1565c0", "브랜드 보조색 (밝은 파랑)", "BRAND", 3),
    ("--brand-navy-start", "#0a2647", "히어로/CTA 그라데이션 시작", "BRAND", 4),
    ("--brand-navy-end", "#144272", "히어로/CTA 그라데이션 끝", "BRAND", 5),
    ("--brand-navy-hover-start", "#081d38", "히어로/CTA 그라데이션 hover 시작", "BRAND", 6),
    ("--brand-navy-hover-end", "#0f3359", "히어로/CTA 그라데이션 hover 끝", "BRAND", 7),
    ("--brand-hero-text", "#dce8fb", "네이비 배경 위 밝은 텍스트", "BRAND", 8),

    ("--bg-page", "#f4f6f9", "페이지 배경", "BG", 1),
    ("--color-white", "#fff", "카드/흰 배경", "BG", 2),
    ("--bg-subtle", "#f0f0f0", "옅은 배경(차트 그리드 등)", "BG", 3),
    ("--bg-subtle-2", "#eee", "옅은 배경 2", "BG", 4),
    ("--bg-hover", "#f1f3f5", "hover 배경", "BG", 5),
    ("--bg-info-soft", "#e3f2fd", "정보 배지 연한 배경", "BG", 6),

    ("--text-primary", "#212529", "본문 기본 텍스트", "TEXT", 1),
    ("--text-secondary", "#495057", "보조 텍스트", "TEXT", 2),
    ("--text-muted", "#6c757d", "흐린(메타) 텍스트", "TEXT", 3),
    ("--text-body-alt", "#343a40", "본문 텍스트(진한 회색)", "TEXT", 4),
    ("--text-faint", "#868e96", "더 흐린 보조 텍스트", "TEXT", 5),
    ("--text-disabled", "#adb5bd", "비활성/구분자 텍스트", "TEXT", 6),
    ("--text-gray", "#616161", "일반 회색 텍스트", "TEXT", 7),

    ("--border-light", "#e9ecef", "옅은 테두리", "BORDER", 1),
    ("--border-input", "#ced4da", "입력창 테두리", "BORDER", 2),
    ("--border-subtle", "#dee2e6", "은은한 테두리", "BORDER", 3),
    ("--border-chart", "#e0e0e0", "차트 테두리", "BORDER", 4),

    ("--color-danger", "#c62828", "위험/오류 텍스트", "DANGER", 1),
    ("--color-danger-alt", "#dc3545", "위험(부트스트랩 레드)", "DANGER", 2),
    ("--color-danger-alt2", "#ef4444", "위험(대체색)", "DANGER", 3),
    ("--bg-danger-soft", "#fdecea", "위험 연한 배경", "DANGER", 4),
    ("--bg-danger-soft-2", "#ffebee", "위험 연한 배경 2", "DANGER", 5),

    ("--color-success", "#1e7e34", "성공 텍스트", "SUCCESS", 1),
    ("--color-success-alt", "#22c55e", "성공(대체색)", "SUCCESS", 2),
    ("--bg-success-soft", "#e6f4ea", "성공 연한 배경", "SUCCESS", 3),

    ("--bg-warning-soft", "#fff3cd", "경고 연한 배경", "OTHER", 1),
    ("--text-warning", "#856404", "경고 텍스트", "OTHER", 2),
    ("--color-warning-alt", "#f57c00", "경고(대체색)", "OTHER", 3),
    ("--color-gold", "#facc15", "골드 강조색", "OTHER", 4),
    ("--color-info", "#00749c", "정보 강조색", "OTHER", 5),
]


def seed_theme_colors(apps, schema_editor):
    ThemeColor = apps.get_model('articles', 'ThemeColor')
    for name, value, label, group, order in SEED:
        ThemeColor.objects.get_or_create(
            name=name,
            defaults={'value': value, 'label': label, 'group': group, 'order': order},
        )


def remove_theme_colors(apps, schema_editor):
    ThemeColor = apps.get_model('articles', 'ThemeColor')
    ThemeColor.objects.filter(name__in=[row[0] for row in SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0084_themecolor'),
    ]

    operations = [
        migrations.RunPython(seed_theme_colors, remove_theme_colors),
    ]
