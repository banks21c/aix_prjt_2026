from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import article_ai, blog_posting, thumbnail
from ..forms import NewsArticleEditForm, NewsScrapeForm
from ..models import AnalyzedArticle, BlogPostingAccount, PostedArticle
from ..utils import (
    ai_summarize_stats, build_mentioned_stocks_table, detect_reuse_restriction,
    fetch_article_metadata, scraping_stats, search_news_by_keyword,
)


def news_board_view(request):
    query = request.GET.get('q', '').strip()

    # 원문이 없으면(KIS 시황_공시 API 수집분, 스크래핑 실패 RSS) AI 요약도 포스팅도 할 수 없으니
    # 게시판에는 원문이 있는 기사만 노출한다.
    articles = (
        AnalyzedArticle.objects
        .exclude(original_content='')
        .select_related('stock', 'matched_keyword')
        .order_by('-scraped_at')
    )
    if query:
        articles = articles.filter(
            Q(title__icontains=query) | Q(stock__name__icontains=query) | Q(matched_keyword__keyword__icontains=query)
        )

    paginator = Paginator(articles, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    # 페이지 번호를 10개 단위 블록으로 묶어 보여준다 (<< < 1..10 > >>).
    page_block_size = 10
    current_block = (page_obj.number - 1) // page_block_size
    page_block_start = current_block * page_block_size + 1
    page_block_end = min(page_block_start + page_block_size - 1, paginator.num_pages)
    page_range = range(page_block_start, page_block_end + 1)
    prev_block_page = page_block_start - 1 if page_block_start > 1 else None
    next_block_page = page_block_end + 1 if page_block_end < paginator.num_pages else None

    user_blog_accounts = []
    posted_article_ids = set()
    posting_stats = None
    summarize_stats = None
    if request.user.is_authenticated:
        summarize_stats = ai_summarize_stats(request.user)
        user_blog_accounts = [a for a in request.user.posting_accounts.all() if a.is_connected()]
        posting_stats = blog_posting.posting_stats(request.user)
        if user_blog_accounts:
            # 계정 체크박스는 포스팅 폼 안에 직접 있어(적용 버튼 없이) 제출 시점의 체크 상태를
            # 그대로 읽어 루프를 돈다 — 그래서 페이지 렌더링 시점엔 "어떤 계정이 체크될지" 알 수
            # 없으므로, 배지는 "연결된 계정 전부에 발행됐는가"로 고정한다. 이미 발행된 계정×기사
            # 조합은 publish_article이 알아서 건너뛰므로, 일부 계정만 체크하고 다시 눌러도 안전하다.
            page_article_ids = [a.pk for a in page_obj.object_list]
            posted_counts = (
                PostedArticle.objects
                .filter(blog_account__in=user_blog_accounts, article_id__in=page_article_ids)
                .values('article_id')
                .annotate(account_count=Count('blog_account', distinct=True))
            )
            posted_article_ids = {
                row['article_id'] for row in posted_counts
                if row['account_count'] == len(user_blog_accounts)
            }

    next_params = request.GET.copy()
    next_url = f"{request.path}?{next_params.urlencode()}" if next_params else request.path

    context = {
        'site_title': 'NextFinUp - 경제 동향',
        'page_obj': page_obj,
        'page_range': page_range,
        'prev_block_page': prev_block_page,
        'next_block_page': next_block_page,
        'query': query,
        'user_blog_accounts': user_blog_accounts,
        'posted_article_ids': posted_article_ids,
        'next_url': next_url,
        'posting_stats': posting_stats,
        'summarize_stats': summarize_stats,
    }
    return render(request, 'articles/news_board.html', context)


@login_required
@require_POST
def post_articles_view(request):
    """뉴스 게시판에서 회원이 직접 고른 기사들을 체크한 계정 전부(1개 이상)에 즉시 발행한다.
    계정을 여러 개 체크했으면 기사마다 각 계정에 순서대로 발행을 반복한다(루프) — 이미 발행된
    계정×기사 조합은 blog_posting.publish_article이 자체적으로 걸러낸다(PostedArticle 유니크)."""
    account_ids = request.POST.getlist('account_ids')
    accounts = list(BlogPostingAccount.objects.filter(pk__in=account_ids, user=request.user))
    if not accounts:
        messages.warning(request, "포스팅할 계정을 하나 이상 선택해주세요.")
        return redirect(request.POST.get('next') or 'news_board')

    connected_accounts = [a for a in accounts if a.is_connected()]
    not_connected = [a for a in accounts if not a.is_connected()]
    for account in not_connected:
        messages.error(request, f"{account.get_platform_display()} 계정이 아직 연동되지 않아 건너뛰었습니다. 마이페이지에서 먼저 연동해주세요.")
    if not connected_accounts:
        return redirect(request.POST.get('next') or 'news_board')

    # 개별 행의 '포스팅' 버튼은 체크박스 상태와 무관하게 그 기사 하나만 발행하도록,
    # 제출 버튼 자체에 실린 solo_article_id를 우선 사용한다 (없으면 체크박스로 고른 전체 발행).
    solo_article_id = request.POST.get('solo_article_id')
    article_ids = [solo_article_id] if solo_article_id else request.POST.getlist('article_ids')

    if not article_ids:
        messages.warning(request, "포스팅할 기사를 하나 이상 선택해주세요.")
        return redirect(request.POST.get('next') or 'news_board')

    stats = blog_posting.posting_stats(request.user)
    remaining = stats['remaining']
    if remaining is not None:
        grade_name = stats['grade'].name if stats['grade'] else '일반'
        daily_limit = stats['grade'].daily_post_limit if stats['grade'] else 0
        if remaining <= 0:
            messages.error(
                request,
                f"{grade_name} 등급은 하루 {daily_limit}건까지만 포스팅할 수 있습니다. "
                "오늘 가능한 건수를 모두 사용했어요 — 내일 다시 시도하거나 등급 업그레이드를 문의해주세요.",
            )
            return redirect(request.POST.get('next') or 'news_board')
        # 계정을 여러 개 체크하면 기사 하나당 실제 발행 시도가 계정 수만큼 반복되므로,
        # 하루 한도는 "기사 수 × 계정 수" 기준으로 넘는지 확인해야 한다.
        planned_total = len(article_ids) * len(connected_accounts)
        if planned_total > remaining:
            max_articles = max(1, remaining // len(connected_accounts))
            messages.warning(
                request,
                f"{grade_name} 등급은 하루 {daily_limit}건까지만 가능해서, 이번엔 {len(connected_accounts)}개 계정 × "
                f"{max_articles}건만 발행합니다.",
            )
            article_ids = article_ids[:max_articles]

    articles = AnalyzedArticle.objects.filter(pk__in=article_ids)
    not_summarized_count = articles.filter(ai_generated=False).count()
    if not_summarized_count:
        messages.warning(
            request,
            f"AI 요약이 안 된 기사 {not_summarized_count}건은 건너뛰었습니다. "
            "먼저 'AI 요약' 버튼으로 요약을 생성한 뒤 포스팅해주세요.",
        )
        articles = articles.filter(ai_generated=True)

    success_count = 0
    for account in connected_accounts:
        for article in articles:
            # 개별 포스팅은 목록 화면의 포스팅완료 버튼 상태로 바로 드러나므로 성공 메시지가 필요 없지만,
            # '선택 포스팅' 일괄 처리는 몇 건이 실제로 끝났는지 바로 안 보이므로 건수를 안내해준다.
            ok, result = blog_posting.publish_article(account, article)
            if ok:
                success_count += 1
            else:
                messages.error(request, f"[{account.get_platform_display()} · {article.title[:30]}] {result}")

    if success_count and not solo_article_id:
        messages.add_message(
            request, messages.SUCCESS,
            f"{success_count}건의 포스팅이 완료되었습니다.",
            extra_tags='post-count',
        )

    return redirect(request.POST.get('next') or 'news_board')


def news_detail_view(request, pk):
    article = get_object_or_404(AnalyzedArticle.objects.select_related('stock', 'matched_keyword'), pk=pk)

    user_blog_accounts = []
    is_posted = False
    posting_stats = None
    summarize_stats = None
    if request.user.is_authenticated:
        summarize_stats = ai_summarize_stats(request.user)
        user_blog_accounts = [a for a in request.user.posting_accounts.all() if a.is_connected()]
        posting_stats = blog_posting.posting_stats(request.user)
        if user_blog_accounts:
            # 뉴스 게시판(news_board_view)과 같은 규칙 — 계정을 여러 개 체크박스로 고를 수 있어
            # "포스팅완료"는 연결된 계정 전부에 발행됐을 때만 뜬다. 일부만 발행됐어도 다시 누르면
            # publish_article이 이미 발행된 계정×기사 조합은 알아서 건너뛰므로 안전하다.
            posted_count = PostedArticle.objects.filter(blog_account__in=user_blog_accounts, article=article).count()
            is_posted = posted_count == len(user_blog_accounts)

    context = {
        'site_title': f'NextFinUp - {article.title}',
        'article': article,
        'user_blog_accounts': user_blog_accounts,
        'is_posted': is_posted,
        'posting_stats': posting_stats,
        'summarize_stats': summarize_stats,
    }
    return render(request, 'articles/news_detail.html', context)


@login_required
def news_search_view(request):
    """news_scrape_view의 '검색어로 찾기' 모드가 쓰는 AJAX 엔드포인트. 검색 결과는 등록된
    언론사 RSS 안에서만 찾은 것이라 회원이 그 중 하나를 고르면, 실제 스크래핑/AI 초안 생성은
    기존 URL 등록 폼에 링크를 채워 넣어 news_scrape_view의 POST 플로우를 그대로 재사용한다."""
    query = request.GET.get('q', '').strip()
    results = search_news_by_keyword(query) if query else []
    return JsonResponse({'results': results})


@login_required
def news_article_preview_view(request, pk):
    """news_scrape_view의 '검색어로 찾기' 결과에서 '이미 등록됨'으로 표시된 기사를 클릭했을 때,
    페이지 이동 없이 이미 저장된 내용을 화면 하단에 바로 보여주기 위한 AJAX 엔드포인트."""
    article = get_object_or_404(AnalyzedArticle, pk=pk)
    return JsonResponse({
        'title': article.title,
        'source_media': article.source_media,
        'scraped_at': article.scraped_at.strftime('%Y-%m-%d %H:%M'),
        'ai_summary': article.ai_summary,
        'ai_analysis': article.ai_analysis,
        'detail_url': reverse('news_detail', args=[article.pk]),
        'edit_url': reverse('news_edit', args=[article.pk]),
    })


@login_required
@require_POST
def news_ai_summarize_view(request, pk):
    """뉴스 게시판에서 아직 진짜 AI 요약이 안 된(ai_generated=False) 자동 수집 기사를 포스팅하기
    직전, 회원이 'AI 요약' 버튼으로 그 기사 하나만 실제 article_ai로 요약을 생성하도록 트리거하는
    엔드포인트. 이미 ai_generated=True인 기사는 한도를 쓰지 않고 그대로 편집 화면으로 보낸다 —
    특징주 브리핑처럼 이미 실제 AI 요약이 있는 기사를 다시 호출해 토큰을 낭비하지 않기 위함."""
    article = get_object_or_404(AnalyzedArticle, pk=pk)

    if not article.ai_generated:
        # KIS 종합 시황_공시 API로 들어온 기사는 원문 링크가 없어 original_content가 항상
        # 비어있다 — 이 경우 article_ai.generate_draft는 실제 AI를 호출하지 않고 시뮬레이션
        # 문구를 그대로 반환하므로, 그걸 '진짜 AI 요약'으로 잘못 표시하지 않도록 여기서 막는다.
        if not article.original_content:
            if article.source_type == AnalyzedArticle.SOURCE_KIS:
                reason = "이 기사는 원문 링크가 없어(KIS 시황_공시 API 수집분) AI가 요약할 원문이 없습니다."
            else:
                reason = "이 기사는 원문 스크래핑에 실패해 AI가 요약할 원문이 없습니다."
            messages.error(request, f"{reason} 편집 화면에서 직접 요약을 작성한 뒤 저장해주세요.")
            return redirect('news_edit', pk=article.pk)

        stats = ai_summarize_stats(request.user)
        if stats['remaining'] == 0:
            grade_name = stats['grade'].name if stats['grade'] else '일반'
            messages.error(
                request,
                f"{grade_name} 등급은 하루 {stats['grade'].daily_ai_summarize_limit}건까지만 AI 요약을 사용할 수 있습니다. "
                "오늘 가능한 건수를 모두 사용했어요.",
            )
            return redirect('news_board')

        draft = article_ai.generate_draft(
            article.title,
            article.original_content,
            restricted=article.has_reuse_restriction,
            related_stock_name=article.stock.name if article.stock else None,
        )
        if draft['ai_summary'] in (article_ai.SIMULATION_SUMMARY, article_ai.ERROR_SUMMARY):
            # OPENAI_API_KEY 미설정이거나 호출 자체가 실패한 경우 — 한도를 쓰지 않고 알려준다.
            messages.error(request, "AI 요약 생성에 실패했습니다. 잠시 후 다시 시도해주세요.")
            return redirect('news_board')

        article.ai_summary = draft['ai_summary']
        article.ai_analysis = draft['ai_analysis']
        article.blog_content = draft['blog_content'] + build_mentioned_stocks_table(article.original_content)
        article.ai_generated = True
        article.ai_summarized_by = request.user
        article.ai_summarized_at = timezone.now()
        article.thumbnail = thumbnail.build_thumbnail_file(
            article.title, article.stock, article.matched_keyword, ai_summary=draft['ai_summary'],
        )
        article.save(update_fields=[
            'ai_summary', 'ai_analysis', 'blog_content',
            'ai_generated', 'ai_summarized_by', 'ai_summarized_at', 'thumbnail',
        ])
        messages.success(request, "AI 요약이 완료됐습니다. 내용을 검토하고 필요하면 수정한 뒤 저장해주세요.")

    return redirect('news_edit', pk=article.pk)


@login_required
def news_scrape_view(request):
    """회원이 임의의 기사 URL을 입력하면 (1) trafilatura로 본문을 스크래핑하고,
    (2) article_ai로 3줄 요약/투자 분석/블로그 초안을 생성한 뒤, (3) 바로 편집 화면(news_edit)으로
    넘겨 검토·수정 후 저장하게 하는 수동 등록 진입점. RSS 자동 수집(scraped_ai_news 등)과 달리
    회원이 임의 사이트를 직접 골라 등록할 때 쓴다. 등급별 일일 한도(MemberGrade.daily_scrape_limit)
    로 제한되며, 관리자(is_staff/is_superuser)는 무제한이다."""
    form = NewsScrapeForm(request.POST or None)
    stats = scraping_stats(request.user)

    if request.method == 'POST' and form.is_valid():
        url = form.cleaned_data['url']

        existing = AnalyzedArticle.objects.filter(original_url=url).first()
        if existing:
            messages.info(request, "이미 등록된 URL입니다. 기존 기사를 편집합니다.")
            return redirect('news_edit', pk=existing.pk)

        if stats['remaining'] == 0:
            grade_name = stats['grade'].name if stats['grade'] else '일반'
            messages.error(
                request,
                f"{grade_name} 등급은 하루 {stats['grade'].daily_scrape_limit}건까지만 등록할 수 있습니다. "
                "오늘 가능한 건수를 모두 사용했어요 — 내일 다시 시도하거나 등급 업그레이드를 문의해주세요.",
            )
            return redirect('news_scrape')

        scraped = fetch_article_metadata(url)
        if not scraped['content']:
            messages.error(request, "본문을 스크래핑하지 못했습니다. URL을 확인하거나 다른 기사로 시도해주세요.")
        else:
            restricted = detect_reuse_restriction(scraped['content'])
            draft = article_ai.generate_draft(scraped['title'], scraped['content'], restricted=restricted)
            article_title = scraped['title'] or url
            article = AnalyzedArticle.objects.create(
                title=article_title,
                original_url=url,
                source_media=scraped['source_media'] or '수동 등록',
                source_type=AnalyzedArticle.SOURCE_RSS,
                original_content=scraped['content'],
                has_reuse_restriction=restricted,
                ai_summary=draft['ai_summary'],
                ai_analysis=draft['ai_analysis'],
                blog_content=draft['blog_content'] + build_mentioned_stocks_table(scraped['content']),
                thumbnail=thumbnail.build_thumbnail_file(article_title, ai_summary=draft['ai_summary']),
                applied_template='T1',
                scraped_by=request.user,
                ai_generated=True,
                ai_summarized_by=request.user,
                ai_summarized_at=timezone.now(),
            )
            messages.success(request, "스크래핑 및 AI 초안 생성이 완료되었습니다. 내용을 검토하고 저장해주세요.")
            return redirect('news_edit', pk=article.pk)

    context = {
        'site_title': 'NextFinUp - 뉴스 포스팅',
        'form': form,
        'scraping_stats': stats,
    }
    return render(request, 'articles/news_scrape.html', context)


@login_required
def news_edit_view(request, pk):
    """staff는 모든 기사를, 일반 회원은 본인이 news_scrape_view로 직접 등록한 기사를 편집할 수
    있다. scraped_by가 없는 기사(RSS/KIS 자동 수집분, 특징주 브리핑)는 특정 회원 소유가 아니라
    누구나 포스팅 전에 검토·수정할 수 있는 공용 콘텐츠라 로그인한 회원이면 누구나 편집 가능하다."""
    article = get_object_or_404(AnalyzedArticle, pk=pk)
    is_shared_article = article.scraped_by_id is None
    if not (request.user.is_staff or article.scraped_by_id == request.user.id or is_shared_article):
        messages.error(request, "본인이 등록한 기사만 수정할 수 있습니다.")
        return redirect('news_board')

    if request.method == 'POST':
        form = NewsArticleEditForm(request.POST, instance=article, is_staff=request.user.is_staff)
        if form.is_valid():
            form.save()
            messages.success(request, "기사가 수정되었습니다.")
            return redirect('news_detail', pk=article.pk)
    else:
        form = NewsArticleEditForm(instance=article, is_staff=request.user.is_staff)

    context = {
        'site_title': f'NextFinUp - {article.title} 수정',
        'article': article,
        'form': form,
    }
    return render(request, 'articles/news_edit.html', context)
