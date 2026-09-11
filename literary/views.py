from django.contrib import messages
from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import LiteraryCandidate


@staff_member_required
def literary_picker_view(request):
    """작가·작품 후보를 표로 보여주고 한 번의 클릭으로 "다음 작품"을 고르는 화면.
    /admin/의 기본 목록+액션 드롭다운 대신, 필사실(claude.ai 대시보드)의 피커와 같은
    사용성을 nextfinup 쪽에도 그대로 두기 위해 만든 커스텀 admin-tools 화면이다."""
    if request.method == 'POST':
        candidate = LiteraryCandidate.objects.filter(pk=request.POST.get('candidate_id')).first()
        if candidate:
            LiteraryCandidate.objects.filter(is_selected=True).update(is_selected=False, selected_at=None)
            candidate.is_selected = True
            candidate.selected_at = timezone.now()
            candidate.save(update_fields=['is_selected', 'selected_at'])
            messages.success(request, f"'{candidate}'을(를) 다음 작품으로 선택했습니다.")
        return redirect('literary_picker')

    context = {
        **admin_site.each_context(request),
        'title': '작가·작품 후보 선택',
        'candidates': LiteraryCandidate.objects.all(),
    }
    return render(request, 'literary/picker.html', context)
