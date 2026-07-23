from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from .. import article_ai, blog_posting
from ..forms import NewsArticleEditForm, NewsScrapeForm
from ..models import AnalyzedArticle, BlogPostingAccount, PostedArticle
from ..utils import fetch_article_metadata, scraping_stats


def news_board_view(request):
    query = request.GET.get('q', '').strip()

    articles = AnalyzedArticle.objects.select_related('stock', 'matched_keyword').order_by('-scraped_at')
    if query:
        articles = articles.filter(
            Q(title__icontains=query) | Q(stock__name__icontains=query) | Q(matched_keyword__keyword__icontains=query)
        )

    paginator = Paginator(articles, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    user_blog_accounts = []
    selected_account = None
    posted_article_ids = set()
    posting_stats = None
    if request.user.is_authenticated:
        user_blog_accounts = [a for a in request.user.posting_accounts.all() if a.is_connected()]
        posting_stats = blog_posting.posting_stats(request.user)
        account_id = request.GET.get('account')
        if account_id:
            selected_account = next((a for a in user_blog_accounts if str(a.pk) == account_id), None)
        if not selected_account and user_blog_accounts:
            selected_account = user_blog_accounts[0]
        if selected_account:
            page_article_ids = [a.pk for a in page_obj.object_list]
            posted_article_ids = set(
                PostedArticle.objects
                .filter(blog_account=selected_account, article_id__in=page_article_ids)
                .values_list('article_id', flat=True)
            )

    # 계정 선택이 URL에 없어도(첫 방문 시 기본값으로 골라준 경우 포함) 항상 유지되도록,
    # 목록/페이지네이션/기사별 포스팅 폼이 공유하는 "현재 조회 조건" URL을 여기서 한 번에 만든다.
    next_params = request.GET.copy()
    if selected_account:
        next_params['account'] = str(selected_account.pk)
    next_url = f"{request.path}?{next_params.urlencode()}" if next_params else request.path

    context = {
        'site_title': 'NextFinUp - 뉴스 게시판',
        'page_obj': page_obj,
        'query': query,
        'user_blog_accounts': user_blog_accounts,
        'selected_account': selected_account,
        'posted_article_ids': posted_article_ids,
        'next_url': next_url,
        'posting_stats': posting_stats,
    }
    return render(request, 'articles/news_board.html', context)


@login_required
@require_POST
def post_articles_view(request):
    """뉴스 게시판에서 회원이 직접 고른 기사들을 선택한 본인 블로그 계정에 즉시 발행한다."""
    account = get_object_or_404(BlogPostingAccount, pk=request.POST.get('account_id'), user=request.user)
    # 개별 행의 '포스팅' 버튼은 체크박스 상태와 무관하게 그 기사 하나만 발행하도록,
    # 제출 버튼 자체에 실린 solo_article_id를 우선 사용한다 (없으면 체크박스로 고른 전체 발행).
    solo_article_id = request.POST.get('solo_article_id')
    article_ids = [solo_article_id] if solo_article_id else request.POST.getlist('article_ids')

    if not account.is_connected():
        messages.error(request, f"{account.get_platform_display()} 계정이 아직 연동되지 않았습니다. 마이페이지에서 먼저 연동해주세요.")
        return redirect(request.POST.get('next') or 'news_board')

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
        if len(article_ids) > remaining:
            messages.warning(
                request,
                f"{grade_name} 등급은 하루 {daily_limit}건까지만 가능해서, 이번엔 {remaining}건만 발행합니다.",
            )
            article_ids = article_ids[:remaining]

    articles = AnalyzedArticle.objects.filter(pk__in=article_ids)
    success_count = 0
    for article in articles:
        # 개별 포스팅은 목록 화면의 포스팅완료 버튼 상태로 바로 드러나므로 성공 메시지가 필요 없지만,
        # '선택 포스팅' 일괄 처리는 몇 건이 실제로 끝났는지 바로 안 보이므로 건수를 안내해준다.
        ok, result = blog_posting.publish_article(account, article)
        if ok:
            success_count += 1
        else:
            messages.error(request, f"[{article.title[:30]}] {result}")

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
    selected_account = None
    is_posted = False
    posting_stats = None
    if request.user.is_authenticated:
        user_blog_accounts = [a for a in request.user.posting_accounts.all() if a.is_connected()]
        posting_stats = blog_posting.posting_stats(request.user)
        account_id = request.GET.get('account')
        if account_id:
            selected_account = next((a for a in user_blog_accounts if str(a.pk) == account_id), None)
        if not selected_account and user_blog_accounts:
            selected_account = user_blog_accounts[0]
        if selected_account:
            is_posted = PostedArticle.objects.filter(blog_account=selected_account, article=article).exists()

    context = {
        'site_title': f'NextFinUp - {article.title}',
        'article': article,
        'user_blog_accounts': user_blog_accounts,
        'selected_account': selected_account,
        'is_posted': is_posted,
        'posting_stats': posting_stats,
    }
    return render(request, 'articles/news_detail.html', context)


@login_required
def news_scrape_view(request):
    """회원이 임의의 기사 URL을 입력하면 (1) trafilatura로 본문을 스크래핑하고,
    (2) OpenAI로 3줄 요약/투자 분석/블로그 초안을 생성한 뒤, (3) 바로 편집 화면(news_edit)으로
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
            draft = article_ai.generate_draft(scraped['title'], scraped['content'])
            article = AnalyzedArticle.objects.create(
                title=scraped['title'] or url,
                original_url=url,
                source_media=scraped['source_media'] or '수동 등록',
                original_content=scraped['content'],
                ai_summary=draft['ai_summary'],
                ai_analysis=draft['ai_analysis'],
                blog_content=draft['blog_content'],
                applied_template='T1',
                scraped_by=request.user,
            )
            messages.success(request, "스크래핑 및 AI 초안 생성이 완료되었습니다. 내용을 검토하고 저장해주세요.")
            return redirect('news_edit', pk=article.pk)

    context = {
        'site_title': 'NextFinUp - URL로 기사 등록',
        'form': form,
        'scraping_stats': stats,
    }
    return render(request, 'articles/news_scrape.html', context)


@login_required
def news_edit_view(request, pk):
    """staff는 모든 기사를, 일반 회원은 본인이 news_scrape_view로 직접 등록한 기사만 편집할 수 있다."""
    article = get_object_or_404(AnalyzedArticle, pk=pk)
    if not (request.user.is_staff or article.scraped_by_id == request.user.id):
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
