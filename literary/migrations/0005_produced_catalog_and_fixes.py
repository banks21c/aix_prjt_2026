# -*- coding: utf-8 -*-
from django.db import migrations

# 이미 제작 완료된 작품(is_produced=True). D:\1.VREW 실제 폴더/파일(완성본 mp4, srt, vrew 등)을
# 기준으로 조사했다 — "품질이 낮아 재제작할 수도 있다"는 이유로 DB에서 제외하지 않고 넣어두되
# 상태만 구분한다.
PRODUCED = {
    "에밀 졸라": [
        "루공가의 행운", "사냥감", "파리의 배", "플라상스의 정복", "무레 사제의 과오",
        "외젠 루공 각하", "목로주점", "사랑의 한 페이지", "나나", "포트부이유",
        "여인들의 행복", "삶의 기쁨", "제르미날", "작품", "대지", "꿈", "인간짐승",
        "돈", "패주", "파스칼 박사", "테레즈 라캥", "루르드", "나는 고발한다", "로마",
        "파리", "풍요", "노동", "진리", "클로드의 고백", "마들렌 페라", "물레방앗간 습격",
        "뷔를 대위", "코케빌의 축제", "올리비에 베카유의 죽음", "낭타스", "마르세유의 비밀",
    ],
    "오노레 드 발자크": [
        "환멸", "고급 창부의 영광과 비참", "고리오 영감", "골짜기의 백합", "곱세크",
        "라부이외즈", "마법 가죽", "사촌 퐁스", "사촌 베트", "샤베르 대령", "외제니 그랑데",
    ],
    "표도르 도스토옙스키": [
        "가난한 사람들", "분신", "백야", "백치", "악령", "지하로부터의 수기", "미성년",
        "죽음의 집의 기록", "나쁜 이야기", "악어", "농부 마레이", "크리스마스 트리와 결혼식",
        "약한 마음", "작은 영웅", "프로하르친 씨", "폴준코프", "아홉 통의 편지로 된 소설",
        "남의 아내와 침대 밑 남편", "여주인", "네토치카 네즈바노바", "아저씨의 꿈",
        "스쩨빤치꼬보", "상처받은 사람들", "노름꾼", "죄와 벌", "영원한 남편", "보보크",
        "온순한 여인", "우스운 인간의 꿈", "카라마조프가의 형제들",
    ],
    "레프 톨스토이": [
        "대자", "두 노인", "바보 이반", "부활", "사람에게는 얼마만큼의 땅이 필요한가",
        "이반 일리치의 죽음", "안나 카레니나", "참회록",
    ],
}

# 0003에서 "미제작"으로 잘못 넣은 두 건 — 실제로는 이미 제작된 작품의 다른 번역 제목이라
# 중복이었다: "학대받은 사람들"=이미 있는 "상처받은 사람들"과 동일 작품(Униженные и
# оскорблённые), "나귀 가죽"=이미 있는 "마법 가죽"과 동일 작품(La Peau de chagrin).
DUPLICATE_FIXES = [
    ("표도르 도스토옙스키", "학대받은 사람들"),
    ("오노레 드 발자크", "나귀 가죽"),
]

# 안 다룬 톨스토이 작품 목록에서 빠져 있던 것 — 톨스토이의 가장 유명한 작품인데도 실제로는
# 이 채널에서 아직 제작한 적이 없다.
ADDITIONAL_UNPRODUCED = [
    ("레프 톨스토이", "전쟁과 평화"),
]


def apply_changes(apps, schema_editor):
    LiteraryCandidate = apps.get_model('literary', 'LiteraryCandidate')

    for author, work in DUPLICATE_FIXES:
        LiteraryCandidate.objects.filter(author=author, work=work, is_selected=False).delete()

    for author, work in ADDITIONAL_UNPRODUCED:
        LiteraryCandidate.objects.get_or_create(author=author, work=work, defaults={'is_produced': False})

    for author, works in PRODUCED.items():
        for work in works:
            obj, created = LiteraryCandidate.objects.get_or_create(
                author=author, work=work, defaults={'is_produced': True}
            )
            if not created and not obj.is_produced:
                obj.is_produced = True
                obj.save(update_fields=['is_produced'])


def revert_changes(apps, schema_editor):
    LiteraryCandidate = apps.get_model('literary', 'LiteraryCandidate')
    for author, works in PRODUCED.items():
        for work in works:
            LiteraryCandidate.objects.filter(author=author, work=work, is_selected=False).delete()
    for author, work in ADDITIONAL_UNPRODUCED:
        LiteraryCandidate.objects.filter(author=author, work=work, is_selected=False).delete()
    for author, work in DUPLICATE_FIXES:
        LiteraryCandidate.objects.get_or_create(author=author, work=work, defaults={'is_produced': False})


class Migration(migrations.Migration):

    dependencies = [
        ('literary', '0004_literarycandidate_is_produced'),
    ]

    operations = [
        migrations.RunPython(apply_changes, revert_changes),
    ]
