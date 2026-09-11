from django.contrib import messages
from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import LiteraryCandidate

PAGE_SIZE = 20
PAGE_BLOCK_SIZE = 10


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
    상단에 띄우면서 클립보드에 바로 복사한다 — 클릭 한 번으로 "선택 + 복사"가 끝난다.
    작가/작품/제작상태 필터와 페이지네이션은 articles/views/news.py의 게시판 패턴(쿼리스트링
    기반 GET 필터 + 10페이지 블록 페이지네이션)을 그대로 따른다."""
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

    all_candidates = list(LiteraryCandidate.objects.all())
    selected = next((c for c in all_candidates if c.is_selected), None)
    authors = sorted({c.author for c in all_candidates})

    filter_author = request.GET.get('author', '').strip()
    filter_work = request.GET.get('work', '').strip()
    filter_produced = request.GET.get('produced', '')
    if filter_produced not in ('', 'true', 'false'):
        filter_produced = ''

    candidates = LiteraryCandidate.objects.all()
    if filter_author:
        candidates = candidates.filter(author=filter_author)
    if filter_work:
        candidates = candidates.filter(work=filter_work)
    if filter_produced:
        candidates = candidates.filter(is_produced=(filter_produced == 'true'))

    paginator = Paginator(candidates, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    current_block = (page_obj.number - 1) // PAGE_BLOCK_SIZE
    page_block_start = current_block * PAGE_BLOCK_SIZE + 1
    page_block_end = min(page_block_start + PAGE_BLOCK_SIZE - 1, paginator.num_pages)
    page_range = range(page_block_start, page_block_end + 1)
    prev_block_page = page_block_start - 1 if page_block_start > 1 else None
    next_block_page = page_block_end + 1 if page_block_end < paginator.num_pages else None

    filter_qs = ''
    if filter_author:
        filter_qs += f'&author={filter_author}'
    if filter_work:
        filter_qs += f'&work={filter_work}'
    if filter_produced:
        filter_qs += f'&produced={filter_produced}'

    context = {
        **admin_site.each_context(request),
        'title': '작가·작품 후보 선택',
        'page_obj': page_obj,
        'page_range': page_range,
        'prev_block_page': prev_block_page,
        'next_block_page': next_block_page,
        'filter_qs': filter_qs,
        'authors': authors,
        'filter_author': filter_author,
        'filter_work': filter_work,
        'filter_produced': filter_produced,
        # 작가 선택 시 작품 콤보를 채우는 JS용 — 페이지네이션과 무관하게 전체 목록이 필요하다.
        'candidates_data': [{'id': c.pk, 'author': c.author, 'work': c.work, 'produced': c.is_produced} for c in all_candidates],
        'trigger_text': _trigger_text_for(selected.author, selected.work) if selected else '',
    }
    return render(request, 'literary/picker.html', context)
