from django.contrib import messages
from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import LiteraryCandidate


def _trigger_text_for(author, work):
    """'로'/'으로' 조사는 work의 마지막 한글 음절 받침 유무로 정한다(닫는 낫표 『』 뒤에 조사가
    붙으므로, 실제로 읽히는 마지막 소리는 낫표 앞 글자다)."""
    particle = "으로"
    for ch in reversed(work):
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            particle = "으로" if (code - 0xAC00) % 28 != 0 else "로"
            break
    return f"/produce-episode {author}의 『{work}』{particle} 진행해줘"


@staff_member_required
def literary_picker_view(request):
    """작가·작품 후보를 표로 보여주고 한 번의 클릭으로 "다음 작품"을 고르는 화면.
    /admin/의 기본 목록+액션 드롭다운 대신, 필사실(claude.ai 대시보드)의 피커와 같은
    사용성을 nextfinup 쪽에도 그대로 두기 위해 만든 커스텀 admin-tools 화면이다.
    선택 시 트리거 문구(/produce-episode ...)를 서버가 만들어 돌려주고, 화면(JS)이 그걸
    상단에 띄우면서 클립보드에 바로 복사한다 — 클릭 한 번으로 "선택 + 복사"가 끝난다."""
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if request.method == 'POST':
        candidate = LiteraryCandidate.objects.filter(pk=request.POST.get('candidate_id')).first()
        if not candidate:
            if is_ajax:
                return JsonResponse({'ok': False, 'error': '해당 후보를 찾을 수 없습니다.'}, status=404)
            return redirect('literary_picker')

        LiteraryCandidate.objects.filter(is_selected=True).update(is_selected=False, selected_at=None)
        candidate.is_selected = True
        candidate.selected_at = timezone.now()
        candidate.save(update_fields=['is_selected', 'selected_at'])
        trigger_text = _trigger_text_for(candidate.author, candidate.work)

        if is_ajax:
            return JsonResponse({'ok': True, 'trigger_text': trigger_text})
        messages.success(request, f"'{candidate}'을(를) 다음 작품으로 선택했습니다.")
        return redirect('literary_picker')

    candidates = list(LiteraryCandidate.objects.all())
    selected = next((c for c in candidates if c.is_selected), None)
    authors = sorted({c.author for c in candidates})
    context = {
        **admin_site.each_context(request),
        'title': '작가·작품 후보 선택',
        'candidates': candidates,
        'authors': authors,
        'candidates_data': [{'id': c.pk, 'author': c.author, 'work': c.work} for c in candidates],
        'trigger_text': _trigger_text_for(selected.author, selected.work) if selected else '',
    }
    return render(request, 'literary/picker.html', context)
