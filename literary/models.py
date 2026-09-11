from django.db import models


class LiteraryCandidate(models.Model):
    """유튜브 롱폼 고전문학 채널의 작가·작품 후보 1건. 고전문학거장50인 목록 중 채널이
    아직 다루지 않은 것만 골라 등록해두고, is_selected로 "다음에 만들 작품"을 표시한다.
    한 번에 하나만 선택 상태를 유지한다 — LiteraryCandidateAdmin.mark_as_selected 액션이
    선택 시 다른 행의 is_selected를 함께 해제한다."""
    author = models.CharField(max_length=100, verbose_name="작가")
    work = models.CharField(max_length=200, verbose_name="작품")
    source = models.CharField(max_length=100, blank=True, default="고전문학거장50인",
                               verbose_name="출처")
    is_selected = models.BooleanField(default=False, verbose_name="다음 작품으로 선택됨")
    selected_at = models.DateTimeField(null=True, blank=True, verbose_name="선택 일시")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="등록 일시")

    class Meta:
        ordering = ['author', 'work']
        verbose_name = "고전문학 작가·작품 후보 (LiteraryCandidate)"
        verbose_name_plural = "고전문학 작가·작품 후보 관리 (LiteraryCandidate)"

    def __str__(self):
        return f"{self.author} - 『{self.work}』"
