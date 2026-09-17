from django.contrib import admin
from django.db.models import Count, Q

from .models import Author, Work


class WorkInline(admin.TabularInline):
    model = Work
    fields = ('title', 'kind', 'priority', 'summary', 'is_produced', 'produced_at', 'uploaded_at',
              'youtube_video_id', 'is_selected')
    readonly_fields = ('is_selected',)
    extra = 0


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ('name', 'nationality', 'summary', 'work_count', 'produced_count', 'source')
    list_filter = ('source', 'nationality')
    search_fields = ('name', 'summary')
    inlines = [WorkInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _work_count=Count('works'),
            _produced_count=Count('works', filter=Q(works__is_produced=True)),
        )

    @admin.display(description="작품 수", ordering='_work_count')
    def work_count(self, obj):
        return obj._work_count

    @admin.display(description="제작완료", ordering='_produced_count')
    def produced_count(self, obj):
        return obj._produced_count


@admin.register(Work)
class WorkAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'kind', 'priority', 'summary', 'is_produced', 'produced_at',
                    'uploaded_at', 'is_selected')
    list_editable = ('priority',)  # 목록에서 바로 순위를 고칠 수 있게
    list_filter = ('is_domestic', 'kind', 'priority', 'is_produced', 'is_selected', 'author')
    search_fields = ('title', 'author__name', 'summary')
    list_select_related = ('author',)
    autocomplete_fields = ('author',)
    ordering = ('-is_selected', 'author__name', 'title')
    readonly_fields = ('selected_at', 'created_at', 'updated_at')
    actions = ['mark_as_selected', 'clear_selection']

    @admin.action(description="선택한 항목 1개를 '다음 작품'으로 표시 (다른 항목은 해제)")
    def mark_as_selected(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "정확히 1개 항목만 선택해서 실행하세요.", level='error')
            return
        work = queryset.first()
        work.select()
        self.message_user(request, f"'{work}'을(를) 다음 작품으로 표시했습니다.")

    @admin.action(description="선택 해제")
    def clear_selection(self, request, queryset):
        updated = queryset.update(is_selected=False, selected_at=None)
        self.message_user(request, f"{updated}건 선택 해제했습니다.")
