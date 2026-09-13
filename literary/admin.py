from django.contrib import admin
from django.utils import timezone

from .models import LiteraryCandidate


@admin.register(LiteraryCandidate)
class LiteraryCandidateAdmin(admin.ModelAdmin):
    list_display = ('author', 'work', 'is_produced', 'is_selected', 'source', 'selected_at')
    list_filter = ('is_produced', 'is_selected', 'source')
    search_fields = ('author', 'work')
    ordering = ('-is_selected', 'author', 'work')
    actions = ['mark_as_selected', 'clear_selection']

    @admin.action(description="선택한 항목 1개를 '다음 작품'으로 표시 (다른 항목은 해제)")
    def mark_as_selected(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "정확히 1개 항목만 선택해서 실행하세요.", level='error')
            return
        candidate = queryset.first()
        LiteraryCandidate.objects.filter(is_selected=True).update(is_selected=False, selected_at=None)
        candidate.is_selected = True
        candidate.selected_at = timezone.now()
        candidate.save(update_fields=['is_selected', 'selected_at'])
        self.message_user(request, f"'{candidate}'을(를) 다음 작품으로 표시했습니다.")

    @admin.action(description="선택 해제")
    def clear_selection(self, request, queryset):
        updated = queryset.update(is_selected=False, selected_at=None)
        self.message_user(request, f"{updated}건 선택 해제했습니다.")
