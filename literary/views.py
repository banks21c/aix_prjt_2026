from django.contrib import messages
from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .models import KIND_CHOICES, Author, Work

KINDS = {value for _, options in KIND_CHOICES for value, _ in options}
PAGE_SIZE = 20
PAGE_BLOCK_SIZE = 10


@staff_member_required
def literary_picker_view(request):
    """작가·작품 후보를 표로 보여주고 한 번의 클릭으로 "다음 작품"을 고르는 화면.
    /admin/의 기본 목록+액션 드롭다운 대신, 필사실(claude.ai 대시보드)의 피커와 같은
    사용성을 nextfinup 쪽에도 그대로 두기 위해 만든 커스텀 admin-tools 화면이다.
    선택 시 트리거 문구(/produce-episode ...)를 서버가 만들어 돌려주고, 화면(JS)이 그걸
    상단에 띄우면서 클립보드에 바로 복사한다 — 클릭 한 번으로 "선택 + 복사"가 끝난다.
    작가/작품/분류/우선순위/제작상태/국내·국외 필터 + 10페이지 블록 페이지네이션.
    목록·작품 상세 화면은 로컬 autovi(~/autovi/literary)와 같은 구성이다."""
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if request.method == 'POST':
        work = Work.objects.select_related('author').filter(pk=request.POST.get('candidate_id')).first()
        if not work:
            if is_ajax:
                return JsonResponse({'ok': False, 'error': '해당 작품을 찾을 수 없습니다.'}, status=404)
            return redirect('literary_picker')

        work.select()

        if is_ajax:
            return JsonResponse({'ok': True, 'trigger_text': work.trigger_text})
        messages.success(request, f"'{work}'을(를) 다음 작품으로 선택했습니다.")
        return redirect('literary_picker')

    context = {
        **admin_site.each_context(request),
        'title': '작가·작품 후보 선택',
        **build_picker_context(request),
    }
    return render(request, 'literary/picker.html', context)


def build_picker_context(request):
    """작가·작품 목록(필터 + 페이지네이션 + 선택된 작품)."""
    all_works = list(Work.objects.select_related('author'))
    selected = next((w for w in all_works if w.is_selected), None)
    authors = list(Author.objects.values_list('name', flat=True))

    filter_author = request.GET.get('author', '').strip()
    filter_work = request.GET.get('work', '').strip()
    filter_produced = request.GET.get('produced', '')
    if filter_produced not in ('', 'true', 'false'):
        filter_produced = ''
    filter_origin = request.GET.get('origin', '')
    if filter_origin not in ('', 'domestic', 'foreign'):
        filter_origin = ''
    filter_kind = request.GET.get('kind', '')
    if filter_kind not in KINDS:
        filter_kind = ''
    filter_priority = request.GET.get('priority', '')
    if filter_priority not in ('', 'none', '1', '2', '3', '4', '5'):
        filter_priority = ''

    works = Work.objects.select_related('author')
    if filter_origin:
        works = works.filter(is_domestic=(filter_origin == 'domestic'))
        # 작가 콤보도 국내/국외에 맞는 작가만 남긴다.
        authors = list(Author.objects.filter(works__in=works).distinct().values_list('name', flat=True))
        if filter_author not in authors:  # 국외 작가를 고른 채 '국내'로 바꾸면 작가·작품 조건은 푼다
            filter_author = filter_work = ''
    if filter_author:
        works = works.filter(author__name=filter_author)
    if filter_work:
        works = works.filter(title=filter_work)
    if filter_produced:
        works = works.filter(is_produced=(filter_produced == 'true'))
    if filter_kind:
        works = works.filter(kind=filter_kind)
    if filter_priority:  # none은 '우선순위를 정하지 않음'(0)
        works = works.filter(priority=0 if filter_priority == 'none' else int(filter_priority))

    paginator = Paginator(works, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    current_block = (page_obj.number - 1) // PAGE_BLOCK_SIZE
    page_block_start = current_block * PAGE_BLOCK_SIZE + 1
    page_block_end = min(page_block_start + PAGE_BLOCK_SIZE - 1, paginator.num_pages)
    page_range = range(page_block_start, page_block_end + 1)
    prev_block_page = page_block_start - 1 if page_block_start > 1 else None
    next_block_page = page_block_end + 1 if page_block_end < paginator.num_pages else None

    return {
        'page_obj': page_obj,
        'page_range': page_range,
        'prev_block_page': prev_block_page,
        'next_block_page': next_block_page,
        'authors': authors,
        'filter_author': filter_author,
        'filter_work': filter_work,
        'filter_produced': filter_produced,
        'filter_origin': filter_origin,
        'filter_kind': filter_kind,
        'filter_priority': filter_priority,
        'kind_choices': KIND_CHOICES,
        # 우선순위 칸·필터는 순위를 매겨 둔 작품이 있을 때만 보여 준다.
        'show_priority': any(w.priority for w in all_works),
        'priority_choices': [(n, f"{n}순위") for n in range(1, 6)],
        # 작가 선택 시 작품 콤보를 채우는 JS용 — 페이지네이션과 무관하게 전체 목록이 필요하다.
        'candidates_data': [{'id': w.pk, 'author': w.author.name, 'work': w.title, 'produced': w.is_produced} for w in all_works],
        'trigger_text': selected.trigger_text if selected else '',
    }


@staff_member_required
def work_detail_view(request, pk):
    """작품 상세 — 작가·작가 소개·작품·한 줄 요약·제작 상태. 목록 행을 클릭하면 온다.
    ?next=로 돌아갈 목록 주소(페이지·필터 포함)를 받는다."""
    work = get_object_or_404(Work.objects.select_related('author'), pk=pk)
    back_url = request.POST.get('next') or request.GET.get('next') or reverse('literary_picker')
    if not url_has_allowed_host_and_scheme(back_url, allowed_hosts={request.get_host()}):
        back_url = reverse('literary_picker')

    if request.method == 'POST':
        work.select()
        messages.success(request, f"'{work}'을(를) 다음 작품으로 선택했습니다.")
        return redirect(back_url)

    context = {
        **admin_site.each_context(request),
        'title': f"{work.author.name} 『{work.title}』",
        'work': work,
        'back_url': back_url,
        'other_works': work.author.works.exclude(pk=work.pk).order_by('title'),
    }
    return render(request, 'literary/work_detail.html', context)
