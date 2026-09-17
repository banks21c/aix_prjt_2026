from django.db import models, router, transaction
from django.utils import timezone


def trigger_text_for(author, work):
    """'로'/'으로' 조사는 work의 마지막 한글 음절 받침 유무로 정한다(닫는 낫표 『』 뒤에 조사가
    붙으므로, 실제로 읽히는 마지막 소리는 낫표 앞 글자다)."""
    particle = "으로"
    for ch in reversed(work):
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            particle = "으로" if (code - 0xAC00) % 28 != 0 else "로"
            break
    return f"/produce-episode {author}의 『{work}』{particle} 진행해줘"


class Author(models.Model):
    """작가. 예전 LiteraryCandidate.author 문자열을 별도 테이블로 분리했다(literary 0006 마이그레이션)."""
    name = models.CharField(max_length=100, unique=True, verbose_name="작가")
    source = models.CharField(max_length=100, blank=True, default="고전문학거장50인",
                              verbose_name="출처")
    summary = models.CharField(max_length=300, blank=True, verbose_name="작가 소개")
    nationality = models.CharField(max_length=50, blank=True, verbose_name="국적",
                                   help_text="목록의 작가 옆에 보인다. 두 나라에 걸치면 '러시아·미국'처럼, 고대는 '고대 그리스'처럼")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="등록 일시")

    class Meta:
        ordering = ['name']
        verbose_name = "작가"
        verbose_name_plural = "작가 관리"

    def __str__(self):
        return self.name


KIND_CHOICES = [
    ("소설", [(k, k) for k in ("장편소설", "중편소설", "단편소설", "연작소설", "단편집", "운문소설")]),
    ("동화·설화", [(k, k) for k in ("동화", "동화집", "설화집")]),
    ("시·희곡", [(k, k) for k in ("서사시", "시집", "시", "희곡")]),
    ("에세이·기록", [(k, k) for k in ("에세이", "논설", "자서전", "회고록", "기행문")]),
    ("철학·사상", [(k, k) for k in ("대화편", "철학서", "사상서", "경전", "신앙서")]),
    ("역사·학술", [(k, k) for k in ("역사서", "학술서", "교양서", "평론")]),
]


# 제작 우선순위 — 0은 정하지 않음. 전래동화처럼 만들 차례를 미리 정해 둔 목록에서 쓴다.
PRIORITY_CHOICES = [(0, "—")] + [(n, f"{n}순위") for n in range(1, 6)]


class Work(models.Model):
    """작품 1건 = 유튜브 롱폼 영상 1편의 후보. is_selected로 "다음에 만들 작품"을 표시하며,
    한 번에 하나만 선택 상태를 유지한다(Work.select)."""
    author = models.ForeignKey(Author, on_delete=models.PROTECT, related_name='works',
                               verbose_name="작가")
    title = models.CharField(max_length=200, verbose_name="작품")
    summary = models.CharField(max_length=200, blank=True, verbose_name="한 줄 요약")
    kind = models.CharField(max_length=10, blank=True, choices=KIND_CHOICES, verbose_name="분류",
                            help_text="장편소설·단편소설·희곡·에세이처럼 작품의 갈래와 분량")
    # 작가가 아니라 작품에 둔다 — '작자 미상'처럼 한 작가 이름 아래 국내·국외 작품이 섞일 수 있다.
    is_domestic = models.BooleanField(default=False, verbose_name="국내 작품",
                                      help_text="한국 작가·한국 고전이면 체크(목록의 전체/국내/국외 필터)")
    priority = models.PositiveSmallIntegerField(default=0, choices=PRIORITY_CHOICES,
                                               verbose_name="제작 우선순위",
                                               help_text="먼저 만들 차례(1순위가 가장 먼저). 0이면 정하지 않음")
    is_produced = models.BooleanField(default=False, verbose_name="제작완료")
    produced_at = models.DateField(null=True, blank=True, verbose_name="영상 제작일")
    uploaded_at = models.DateField(null=True, blank=True, verbose_name="영상 업로드일")
    youtube_video_id = models.CharField(max_length=32, blank=True, verbose_name="유튜브 videoId")
    is_selected = models.BooleanField(default=False, verbose_name="다음 작품으로 선택됨")
    selected_at = models.DateTimeField(null=True, blank=True, verbose_name="선택 일시")
    note = models.TextField(blank=True, verbose_name="메모")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="등록 일시")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="수정 일시")

    class Meta:
        ordering = ['author__name', 'title']
        constraints = [
            models.UniqueConstraint(fields=['author', 'title'], name='unique_author_work'),
        ]
        verbose_name = "작품"
        verbose_name_plural = "작품 관리"

    def __str__(self):
        return f"{self.author.name} - 『{self.title}』"

    @property
    def trigger_text(self):
        return trigger_text_for(self.author.name, self.title)

    def select(self):
        """이 작품을 "다음 작품"으로 표시하고 다른 작품의 선택은 해제한다.
        트랜잭션은 이 모델이 저장되는 DB에 건다 — nextfinup에서는 라우터가 autovi_db로 보낸다."""
        with transaction.atomic(using=router.db_for_write(Work)):
            Work.objects.filter(is_selected=True).exclude(pk=self.pk).update(is_selected=False, selected_at=None)
            self.is_selected = True
            self.selected_at = timezone.now()
            self.save(update_fields=['is_selected', 'selected_at', 'updated_at'])
