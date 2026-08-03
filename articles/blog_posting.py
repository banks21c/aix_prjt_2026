"""회원별 자동 포스팅(워드프레스/블로거 블로그) 공통 로직.

각 플랫폼 커맨드(post_to_wordpress 등)와 마이페이지의 수동 포스팅 화면(post_articles_view)이
공유하는 "발행 대상 기사 선정", "포스팅용 콘텐츠 빌드", "실제 플랫폼별 발행 API 호출"을
한 곳에 모아, 자동/수동 두 경로에서 발행 로직이 서로 다르게 갈라지지 않도록 한다.
"""
import re

import requests
from django.conf import settings
from django.utils import timezone

from .models import AnalyzedArticle, BlogPostingAccount, PostedArticle, StockPrediction, UserSubscription

# blog_content는 두 가지 출처가 섞여 있다: (1) RSS 자동 수집 파이프라인(collect_keyword_news 등)이
# 만드는 개행(\n) 기반 평문, (2) news_scrape → news_edit에서 Toast UI Editor(WYSIWYG)로 작성/수정한
# <h3>/<p>/<ul> 등 블록 태그 포함 HTML. 이미 블록 태그가 있으면 에디터가 만든 구조를 그대로 신뢰하고,
# 없으면 평문으로 간주해 기존처럼 줄바꿈만 <br>로 살린다.
_BLOCK_HTML_RE = re.compile(r'<(p|h[1-6]|ul|ol|li|div|blockquote|table|img|br)\b', re.IGNORECASE)

WP_POST_STATUS = "publish"  # 검증 완료 후 바로 공개 발행으로 전환.
BLOGGER_IS_DRAFT = False  # 검증 완료 후 바로 공개 발행으로 전환.

def posting_stats(user):
    """뉴스 게시판에 표시할 회원의 포스팅 현황.
    하루 발행 가능 건수는 회원 등급(MemberGrade.daily_post_limit)을 기준으로 계산한다 — 등급이
    없거나 한도가 비어있으면(NULL) 무제한. 프리미엄 구독(UserSubscription.is_active_premium)과
    관리자(is_staff/is_superuser)는 등급과 무관하게 항상 무제한으로 취급한다."""
    subscription, _ = UserSubscription.objects.get_or_create(user=user)
    grade = getattr(getattr(user, 'preference', None), 'grade', None)
    is_admin = user.is_staff or user.is_superuser
    limit = grade.daily_post_limit if grade else None
    is_unlimited = subscription.is_active_premium or is_admin or limit is None
    today_count = PostedArticle.objects.filter(
        blog_account__user=user, posted_at__date=timezone.localdate()
    ).count()
    total_count = PostedArticle.objects.filter(blog_account__user=user).count()
    remaining = None if is_unlimited else max(0, limit - today_count)
    return {
        'is_premium': subscription.is_active_premium,
        'is_admin': is_admin,
        'grade': grade,
        'remaining': remaining,
        'today_count': today_count,
        'total_count': total_count,
    }


def enabled_accounts(platform):
    """해당 플랫폼에서 '자동 포스팅 사용' + '이 플랫폼 사용'을 모두 켠 회원 계정 목록."""
    return (
        BlogPostingAccount.objects
        .filter(platform=platform, is_enabled=True, user__preference__auto_posting_enabled=True)
        .select_related('user', 'user__preference')
    )


def _match_keywords(article, keywords):
    haystack = article.title
    if article.stock:
        haystack += f" {article.stock.name}"
    return any(kw and kw in haystack for kw in keywords)


def select_candidates(account, preference, limit=None):
    """이 계정에 아직 발행되지 않은 기사 중, 관심 키워드(또는 전체 발행 설정)에 맞는 기사 목록.
    ai_generated=False(RSS/KIS 자동 수집 직후의 placeholder 요약)는 제외한다 — 회원의 실제
    블로그에 원문 truncate/고정 문구를 그대로 발행하지 않기 위함. 회원이 뉴스 게시판에서 'AI 요약'
    버튼으로 직접 요약을 생성했거나, 특징주 통합 브리핑처럼 이미 실제 AI 요약이 있는 기사만 대상."""
    candidates = (
        AnalyzedArticle.objects
        .filter(ai_generated=True)
        .select_related('stock', 'matched_keyword')
        .exclude(postings__blog_account=account)
        .order_by('-scraped_at')
    )

    if preference.post_all_articles:
        candidates = list(candidates)
    else:
        keywords = [kw.strip() for kw in preference.interested_keywords.split(',') if kw.strip()]
        if not keywords:
            return []
        candidates = [a for a in candidates if _match_keywords(a, keywords)]

    if limit is not None:
        candidates = candidates[:limit]
    return candidates


_SECTION_H3_STYLE = "color: #0D47A1; border-left: 5px solid #0D47A1; padding-left: 10px;"
_HR_HTML = '<hr style="border: 0; height: 1px; background: #CCC; margin: 30px 0;">'
_DISCLAIMER_HTML = (
    '<p style="font-size: 12px; color: #888; margin-top: 5px;">본 콘텐츠는 공개된 시장 데이터와 '
    '뉴스를 참고하여 작성되었으며, 투자 판단 및 그 결과에 대한 책임은 전적으로 투자자 본인에게 '
    '있습니다.</p>'
)


def _section_h3(text):
    return f'<h3 style="{_SECTION_H3_STYLE}">{text}</h3>'


def _build_pred_html(article, latest_pred):
    """StockPrediction이 있으면(관련 종목이 있고 예측이 이미 돌아간 경우) ML 예측 표 HTML,
    없으면 빈 문자열. 3개 템플릿이 모두 공유하는 블록이라 여기서 한 번만 만든다."""
    if not (latest_pred and latest_pred.pred_next_close is not None):
        return ""
    signal_color = "#E53935" if latest_pred.trading_signal == 'BUY' else ("#1E88E5" if latest_pred.trading_signal == 'SELL' else "#757575")
    return f"""
    <div style="padding: 20px; border: 2px solid #EEE; border-radius: 10px; background-color: #FAFAFA; margin-bottom: 20px;">
        <h3 style="margin-top: 0; color: #333;">🤖 머신러닝 주가 추론 브리핑</h3>
        <p><b>🎯 분석 기준 종목:</b> {article.stock.name} ({article.stock.ticker})</p>
        <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
            <tr style="background-color: #F5F5F5;"><th style="padding: 8px; border: 1px solid #DDD;">예측 항목</th><th style="padding: 8px; border: 1px solid #DDD;">AI 추론 결과</th></tr>
            <tr><td style="padding: 8px; border: 1px solid #DDD;">내일 예상 종가</td><td style="padding: 8px; border: 1px solid #DDD; font-weight: bold;">{latest_pred.pred_next_close:,.0f} 원</td></tr>
            <tr><td style="padding: 8px; border: 1px solid #DDD;">다음날 상승 확률</td><td style="padding: 8px; border: 1px solid #DDD; color: #E53935;">{latest_pred.up_probability * 100:.1f}%</td></tr>
            <tr><td style="padding: 8px; border: 1px solid #DDD;">향후 5일 예상 수익률</td><td style="padding: 8px; border: 1px solid #DDD;">{latest_pred.pred_5day_return}%</td></tr>
            <tr><td style="padding: 8px; border: 1px solid #DDD;"><b>최종 투자 시그널</b></td><td style="padding: 8px; border: 1px solid #DDD; font-weight: bold; color: {signal_color};">{latest_pred.get_trading_signal_display()}</td></tr>
        </table>
    </div>
    """


def _blog_content_body(article):
    """blog_content는 두 가지 출처가 섞여 있다(모듈 상단 주석 참고) — 이미 블록 태그가 있는
    HTML은 그대로, 평문(레거시 RSS 수집분)은 줄바꿈만 <br>로 살려 <p>로 감싼다."""
    blog_content = article.blog_content or ''
    if _BLOCK_HTML_RE.search(blog_content):
        return blog_content
    safe_blog_content = blog_content.replace('\n', '<br>')
    return f"<p>{safe_blog_content}</p>"


def _subject_label(article):
    return article.stock.name if article.stock else (
        article.matched_keyword.keyword if article.matched_keyword else "경제"
    )


def _render_t1(article, safe_summary, blog_content_body, pred_html):
    """템플릿 1 (뉴스 요약형): 뉴스 핵심 요약을 가장 먼저 보여주고, 투자 분석 → 실전 가이드
    순으로 이어지는 원래(기본) 레이아웃."""
    return f"""
    {pred_html}
    <div style="line-height: 1.8; font-size: 16px; color: #333;">
        {_section_h3('📰 오늘의 뉴스 핵심 요약')}
        <blockquote style="background: #F9F9F9; border-left: 10px solid #CCC; margin: 1.5em 10px; padding: 0.5em 10px;">
            {safe_summary}
        </blockquote>

        {_section_h3('💡 전문 투자 관점 분석')}
        <p>{article.ai_analysis}</p>

        {_HR_HTML}

        {_section_h3('🚀 실전 투자 가이드 브리핑')}
        {blog_content_body}

        {_DISCLAIMER_HTML}
    </div>
    """


def _render_t2(article, safe_summary, blog_content_body, pred_html):
    """템플릿 2 (종목 분석형): 종목명을 헤더로 내세우고 ML 예측/투자 분석을 먼저 배치, 원본
    뉴스 요약은 맨 뒤에 참고 자료로 축소해서 붙인다 — 뉴스 자체보다 종목 분석이 중심."""
    subject = _subject_label(article)
    header = f"""
    <div style="padding: 16px 20px; background: #0D47A1; color: #fff; border-radius: 10px; margin-bottom: 20px;">
        <h2 style="margin: 0; font-size: 20px;">📊 종목 분석 리포트: {subject}</h2>
    </div>
    """
    return f"""
    {header}
    {pred_html}
    <div style="line-height: 1.8; font-size: 16px; color: #333;">
        {_section_h3('💡 전문 투자 관점 분석')}
        <p>{article.ai_analysis}</p>

        {_HR_HTML}

        {_section_h3('🚀 실전 투자 가이드 브리핑')}
        {blog_content_body}

        {_HR_HTML}

        {_section_h3('📰 관련 뉴스 요약 (참고)')}
        <blockquote style="background: #F9F9F9; border-left: 10px solid #CCC; margin: 1.5em 10px; padding: 0.5em 10px; font-size: 14px; color: #555;">
            {safe_summary}
        </blockquote>

        {_DISCLAIMER_HTML}
    </div>
    """


def _render_t3(article, safe_summary, blog_content_body, pred_html):
    """템플릿 3 (카드뉴스 대본형): 제목/3줄 요약/투자 시사점을 카드뉴스 슬라이드처럼 짧고
    굵은 카드 단위로 나열한 뒤, 실전 가이드 본문을 이어 붙인다."""
    cards = [("HOOK", article.title)]
    cards += [(f"CARD {i}", line) for i, line in enumerate(
        (line.strip() for line in article.ai_summary.split('\n') if line.strip()), start=1
    )]
    if article.ai_analysis:
        cards.append(("INSIGHT", article.ai_analysis))

    card_html = "".join(f"""
        <div style="padding: 18px 20px; margin-bottom: 12px; background: linear-gradient(135deg, #0D47A1, #1565C0); color: #fff; border-radius: 12px;">
            <div style="font-size: 12px; opacity: 0.75; letter-spacing: 1px; margin-bottom: 6px;">{label}</div>
            <div style="font-size: 18px; font-weight: 700; line-height: 1.5;">{text}</div>
        </div>
    """ for label, text in cards)

    return f"""
    {pred_html}
    <div style="line-height: 1.8; font-size: 16px; color: #333;">
        {_section_h3('🎬 카드뉴스 대본')}
        {card_html}

        {_HR_HTML}

        {_section_h3('🚀 실전 투자 가이드 브리핑')}
        {blog_content_body}

        {_DISCLAIMER_HTML}
    </div>
    """


_TEMPLATE_RENDERERS = {
    'T1': _render_t1,
    'T2': _render_t2,
    'T3': _render_t3,
}


def build_post_content(article):
    """기사 + 최신 ML 예측을 결합한 블로그 포스팅용 (제목, HTML 본문, 종목/키워드 라벨) 반환.
    article.applied_template(T1/T2/T3)에 따라 서로 다른 레이아웃(_TEMPLATE_RENDERERS)을 적용한다 —
    값이 비어있거나 알 수 없는 경우 기본값인 T1(뉴스 요약형)으로 처리."""
    latest_pred = StockPrediction.objects.filter(stock=article.stock).order_by('-date').first() if article.stock else None

    safe_summary = article.ai_summary.replace('\n', '<br>')
    blog_content_body = _blog_content_body(article)
    pred_html = _build_pred_html(article, latest_pred)

    renderer = _TEMPLATE_RENDERERS.get(article.applied_template, _render_t1)
    full_html_content = renderer(article, safe_summary, blog_content_body, pred_html)

    if article.thumbnail:
        # 워드프레스/블로거는 외부 URL로 이미지를 그대로 fetch하므로 절대 URL이 필요하다
        # (article.thumbnail.url은 MEDIA_URL 기준 상대경로).
        thumbnail_url = f"{settings.SITE_URL}{article.thumbnail.url}"
        thumbnail_html = (
            f'<p><img src="{thumbnail_url}" alt="{article.title}" '
            'style="max-width:100%; height:auto; border-radius:10px; margin-bottom:20px;"></p>'
        )
        full_html_content = thumbnail_html + full_html_content

    subject_label = _subject_label(article)
    # 게시판에 뜨는 원본 기사 제목(article.title)을 그대로 살려서, 회원이 블로그 관리자 화면에서
    # 봤을 때 게시판의 어느 기사가 발행된 건지 바로 알아볼 수 있게 한다. 접두사는 "NextFinUp이
    # 만든 콘텐츠"라는 걸 밝히지 않도록 중립적인 표현만 붙인다 — 애드센스를 붙일 회원 본인의
    # 블로그 글처럼 보여야 하기 때문.
    blog_title = f"[투자 인사이트] {article.title}"

    return blog_title, full_html_content, subject_label


def publish_to_wordpress(account, blog_title, content):
    """반환: (성공 여부, 발행된 글 URL, 실패 사유)"""
    try:
        res = requests.post(
            f"{account.site_url}/wp-json/wp/v2/posts",
            auth=(account.account_id, account.credential),
            json={"title": blog_title, "content": content, "status": WP_POST_STATUS},
            timeout=15,
        )
    except Exception as e:
        return False, '', f"네트워크 연동 실패: {e}"

    if res.status_code == 201:
        return True, res.json().get('link', ''), None
    return False, '', f"워드프레스 API 응답 에러 ({res.status_code}): {res.text[:300]}"


def _get_blogger_access_token(account):
    """계정에 저장된 OAuth 리프레시 토큰으로 새 액세스 토큰을 발급."""
    res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            'grant_type': 'refresh_token',
            'client_id': settings.GOOGLE_CLIENT_ID,
            'client_secret': settings.GOOGLE_CLIENT_SECRET,
            'refresh_token': account.credential,
        },
        timeout=10,
    ).json()
    return res.get('access_token'), res.get('error_description')


def publish_to_blogger(account, blog_title, content):
    """반환: (성공 여부, 발행된 글 URL, 실패 사유)"""
    access_token, error = _get_blogger_access_token(account)
    if not access_token:
        return False, '', f"블로거 액세스 토큰 갱신 실패: {error or '알 수 없는 오류'} (마이페이지에서 블로거를 다시 연결해야 할 수 있습니다)"

    try:
        res = requests.post(
            f"https://www.googleapis.com/blogger/v3/blogs/{account.account_id}/posts",
            params={'isDraft': 'true' if BLOGGER_IS_DRAFT else 'false'},
            headers={'Authorization': f'Bearer {access_token}'},
            json={"title": blog_title, "content": content},
            timeout=15,
        )
        body = res.json()
    except Exception as e:
        return False, '', f"네트워크 연동 실패: {e}"

    if res.status_code in (200, 201):
        return True, body.get('url', ''), None
    return False, '', f"블로거 API 응답 에러 ({res.status_code}): {str(body)[:300]}"


PUBLISHERS = {
    'WORDPRESS': lambda account, title, content, subject_label: publish_to_wordpress(account, title, content),
    'BLOGGER': lambda account, title, content, subject_label: publish_to_blogger(account, title, content),
}


def publish_article(account, article):
    """기사 1건을 계정의 플랫폼에 맞춰 실제로 발행하고, 성공 시 PostedArticle을 기록한다.
    반환: (성공 여부, 발행된 글 URL 또는 실패 사유 메시지)"""
    if PostedArticle.objects.filter(blog_account=account, article=article).exists():
        return False, "이미 이 계정에 발행된 기사입니다."

    publisher = PUBLISHERS.get(account.platform)
    if publisher is None:
        return False, "지원하지 않는 플랫폼입니다."

    blog_title, content, subject_label = build_post_content(article)
    ok, url, error = publisher(account, blog_title, content, subject_label)
    if not ok:
        return False, error or "발행에 실패했습니다."

    PostedArticle.objects.create(blog_account=account, article=article, external_url=url)
    return True, url
