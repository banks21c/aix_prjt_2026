"""회원별 자동 포스팅(워드프레스/블로거 블로그) 공통 로직.

각 플랫폼 커맨드(post_to_wordpress 등)와 마이페이지의 수동 포스팅 화면(post_articles_view)이
공유하는 "발행 대상 기사 선정", "포스팅용 콘텐츠 빌드", "실제 플랫폼별 발행 API 호출"을
한 곳에 모아, 자동/수동 두 경로에서 발행 로직이 서로 다르게 갈라지지 않도록 한다.
"""
import logging
import os
import re
from datetime import datetime, time, timedelta, timezone as dt_timezone

import requests
from django.conf import settings
from django.utils import timezone

from .models import (
    AnalyzedArticle, BlogPostingAccount, NaverPostMigration, PostedArticle, StockPrediction,
    UserSubscription,
)
from .utils import resolve_thumbnail_stock

logger = logging.getLogger(__name__)

# blog_content는 두 가지 출처가 섞여 있다: (1) RSS 자동 수집 파이프라인(collect_keyword_news 등)이
# 만드는 개행(\n) 기반 평문, (2) news_scrape → news_edit에서 Toast UI Editor(WYSIWYG)로 작성/수정한
# <h3>/<p>/<ul> 등 블록 태그 포함 HTML. 이미 블록 태그가 있으면 에디터가 만든 구조를 그대로 신뢰하고,
# 없으면 평문으로 간주해 기존처럼 줄바꿈만 <br>로 살린다.
_BLOCK_HTML_RE = re.compile(r'<(p|h[1-6]|ul|ol|li|div|blockquote|table|img|br)\b', re.IGNORECASE)

# build_post_content가 본문 맨 앞에 붙이는 썸네일 <img>와 정확히 같은 패턴 — 워드프레스는
# 이제 대표 이미지(featured_media)를 별도로 설정하므로, 이 인라인 이미지까지 본문에 남겨두면
# 테마가 대표 이미지와 본문 이미지를 각각 그려 같은 사진이 한 글에 두 번 나온다(단일 글
# 화면에서 확인됨, deepsleepway.com). 블로거는 대표 이미지 개념이 없어 본문 첫 이미지를
# 목록 썸네일로 자동 추출하므로 이 인라인 이미지가 여전히 필요하다 — 그래서 제거는
# publish_to_wordpress/update_to_wordpress에서만, featured_media 설정에 성공했을 때만 한다.
_LEADING_THUMBNAIL_IMG_RE = re.compile(
    r'^<p><img src="[^"]*" alt="[^"]*" '
    r'style="max-width:100%; height:auto; border-radius:10px; margin-bottom:20px;"></p>'
)

WP_POST_STATUS = "publish"  # 검증 완료 후 바로 공개 발행으로 전환.
BLOGGER_IS_DRAFT = False  # 검증 완료 후 바로 공개 발행으로 전환.

# 발행 대상 계정 1개가 하루에 올릴 권장 최대 건수. 회원 등급 한도(MemberGrade.daily_post_limit,
# 과금/혜택 개념)와 목적이 다르다 — 이쪽은 "대상 플랫폼이 우리를 스팸 봇으로 보지 않게" 하는
# 안전장치다. 숫자 근거는 이 프로젝트에서 실제로 맞은 제재들이다:
#   BLOGGER  — 이력 없는 블로그에 40건을 몰아 올리다 계정 단위로 글 생성이 영구 차단됐다
#              (403 PERMISSION_DENIED, OAuth 재연결로도 안 풀림). 가장 보수적으로 잡는다.
#   WORDPRESS— 자체 호스팅이라 플랫폼 차단 위험은 없지만, Cloudflare/EasyWP 앞단이 버스트에
#              429를 냈다. 총량보다 간격 문제라 한도는 넉넉히 두고 간격으로 푼다.
# 계정별로 BlogPostingAccount.daily_post_limit에 값을 넣으면 그 값이 우선한다(0=무제한).
RECOMMENDED_DAILY_POST_LIMIT = {
    'WORDPRESS': 10,
    'BLOGGER': 3,
}
DEFAULT_DAILY_POST_LIMIT = 5  # 위 표에 없는 새 플랫폼이 생겼을 때의 보수적 기본값

# 하루의 경계는 KST 기준으로 센다. settings.TIME_ZONE은 UTC라 timezone.localdate()가 커맨드
# (미들웨어 없음)에서는 UTC 날짜를 주는데, 그러면 KST 00~09시 발행분이 전날로 잡혀 한도가
# 실제보다 헐거워진다. 운영자도 크론도 KST로 생각하므로 여기서만 명시적으로 KST로 고정한다.
KST_FOR_QUOTA = dt_timezone(timedelta(hours=9))


def account_posting_quota(account):
    """이 발행 계정의 오늘 발행 현황과 남은 건수.

    {'limit', 'used_today', 'remaining', 'is_unlimited', 'source'} 를 돌려준다.
    source는 한도 숫자가 어디서 왔는지('계정 설정' / '플랫폼 권장')로, 화면·로그에서
    "이 숫자를 내가 정한 건가 기본값인가"를 구분하기 위한 표시용이다.

    오늘 건수는 일반 발행(PostedArticle)과 네이버 이관 발행(NaverPostMigration 성공분)을
    합쳐서 센다 — 플랫폼 입장에선 둘 다 똑같이 "이 블로그에 오늘 올라온 글"이라, 한쪽만
    세면 이관 배치를 도는 날 한도가 두 배로 헐거워진다."""
    if account.daily_post_limit is None:
        limit = RECOMMENDED_DAILY_POST_LIMIT.get(account.platform, DEFAULT_DAILY_POST_LIMIT)
        source = '플랫폼 권장'
    else:
        limit = account.daily_post_limit
        source = '계정 설정'

    today = timezone.now().astimezone(KST_FOR_QUOTA).date()
    start = datetime.combine(today, time.min, tzinfo=KST_FOR_QUOTA)
    end = start + timedelta(days=1)

    used = (
        PostedArticle.objects.filter(blog_account=account, posted_at__gte=start, posted_at__lt=end).count()
        + NaverPostMigration.objects.filter(
            blog_account=account, status='SUCCESS', published_at__gte=start, published_at__lt=end,
        ).count()
    )

    is_unlimited = limit == 0
    return {
        'limit': limit,
        'used_today': used,
        'remaining': None if is_unlimited else max(0, limit - used),
        'is_unlimited': is_unlimited,
        'source': source,
    }


def posting_stats(user):
    """뉴스 게시판에 표시할 회원의 포스팅 현황.
    하루 발행 가능 건수는 회원 등급(MemberGrade.daily_post_limit)을 기준으로 계산한다 — 등급이
    없거나 한도가 비어있으면(NULL) 무제한. 프리미엄 구독(UserSubscription.is_active_premium)은
    등급과 무관하게 하루 UserSubscription.PREMIUM_DAILY_POST_LIMIT건으로 고정한다 — AI 요약
    한도(ai_summarize_stats)와 같은 숫자로 맞춰야 "AI 요약은 10건인데 발행은 무제한"처럼
    한쪽만 열려서 사실상 못 쓰는 반쪽짜리 혜택이 되지 않는다. 관리자(is_staff/is_superuser)는
    등급·프리미엄과 무관하게 항상 무제한.

    카운트 자체도 AI가 실제로 개입한 발행만 센다 — article.ai_summarized_by가 채워진(=AI
    요약을 거친) AnalyzedArticle의 발행만 today_count/total_count에 넣고, 직접 작성해 AI 호출
    없이 그대로 발행한 글(news_write_view의 '바로 포스팅')은 셈에서 빼서 무제한으로 둔다 —
    이쪽은 애초에 비용이 들지 않으므로 한도를 걸 이유가 없다."""
    subscription, _ = UserSubscription.objects.get_or_create(user=user)
    grade = getattr(getattr(user, 'preference', None), 'grade', None)
    is_admin = user.is_staff or user.is_superuser
    if subscription.is_active_premium:
        limit = UserSubscription.PREMIUM_DAILY_POST_LIMIT
    else:
        limit = grade.daily_post_limit if grade else None
    is_unlimited = is_admin or limit is None
    ai_posted = PostedArticle.objects.filter(blog_account__user=user, article__ai_summarized_by__isnull=False)
    today_count = ai_posted.filter(posted_at__date=timezone.localdate()).count()
    total_count = ai_posted.count()
    remaining = None if is_unlimited else max(0, limit - today_count)
    return {
        'is_premium': subscription.is_active_premium,
        'is_admin': is_admin,
        'grade': grade,
        'limit': limit,
        'remaining': remaining,
        'today_count': today_count,
        'total_count': total_count,
    }


def enabled_accounts(platform, category=None):
    """해당 플랫폼에서 '자동 포스팅 사용' + '이 플랫폼 사용'을 모두 켠 회원 계정 목록.

    category(선택)를 넘기면 그 카테고리(UserPreference.news_subscription)를 구독한 회원의
    계정만 추린다 — 경제 브리핑 cron 시간대에 건강 구독자 계정까지 함께 돌면서 그 계정의
    다른 카테고리 백로그가 얹혀 발행 빈도가 의도치 않게 늘어나는 걸 막기 위해서다(반대도
    마찬가지). select_candidates 자체도 카테고리로 걸러주지만, 이건 애초에 그 시간대 cron이
    처리할 '계정 범위'를 좁히는 것이라 두 필터의 목적이 다르다."""
    qs = (
        BlogPostingAccount.objects
        .filter(platform=platform, is_enabled=True, user__preference__auto_posting_enabled=True)
        .select_related('user', 'user__preference')
    )
    if category is not None:
        qs = qs.filter(user__preference__news_subscription=category)
    return qs


def _match_keywords(article, keywords):
    haystack = article.title
    if article.stock:
        haystack += f" {article.stock.name}"
    return any(kw and kw in haystack for kw in keywords)


# 종목/증시 관심 키워드 개념이 없는, 캘린더(articles/content_calendar.py) 기반 자동 생성
# 카테고리 — select_candidates에서 키워드 필터링을 건너뛰는 기준으로 쓴다.
_CALENDAR_DRIVEN_CATEGORIES = (
    AnalyzedArticle.CATEGORY_HEALTH, AnalyzedArticle.CATEGORY_FOOD, AnalyzedArticle.CATEGORY_TRAVEL,
)


def select_candidates(account, preference, limit=None):
    """이 계정에 아직 발행되지 않은 기사 중, 관심 키워드(또는 전체 발행 설정)에 맞는 기사 목록.
    ai_generated=False(RSS/KIS 자동 수집 직후의 placeholder 요약)는 제외한다 — 회원의 실제
    블로그에 원문 truncate/고정 문구를 그대로 발행하지 않기 위함. 회원이 뉴스 게시판에서 'AI 요약'
    버튼으로 직접 요약을 생성했거나, 특징주 통합 브리핑처럼 이미 실제 AI 요약이 있는 기사만 대상."""
    candidates = (
        AnalyzedArticle.objects
        .filter(ai_generated=True, content_category=preference.news_subscription)
        .select_related('stock', 'matched_keyword')
        .exclude(postings__blog_account=account)
        .order_by('-scraped_at')
    )

    if preference.news_subscription in _CALENDAR_DRIVEN_CATEGORIES:
        # 건강/의학·음식/영양처럼 종목/증시 관심 키워드 개념이 없는 캘린더 기반 발행은, 카테고리를
        # 고른 것 자체를 "이 카테고리는 전부 받는다"는 의사표시로 취급한다(전체 발행 토글과 무관).
        candidates = list(candidates)
    elif preference.post_all_articles:
        candidates = list(candidates)
    else:
        keywords = [kw.strip() for kw in preference.interested_keywords.split(',') if kw.strip()]
        if not keywords:
            return []
        candidates = [a for a in candidates if _match_keywords(a, keywords)]

    if limit is not None:
        candidates = candidates[:limit]

    # 마지막에 계정별 하루 한도로 한 번 더 자른다. 호출부가 --limit을 안 줬거나(=미발행 백로그
    # 전체가 대상) 크게 줬을 때도 하루 상한을 넘지 않게 하는 안전장치라, 여기가 마지막 관문이다.
    quota = account_posting_quota(account)
    if quota['remaining'] is not None and len(candidates) > quota['remaining']:
        candidates = candidates[:quota['remaining']]
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


def _build_pred_html(stock, latest_pred):
    """StockPrediction이 있으면(제목에서 실제로 종목이 인식됐고 예측이 이미 돌아간 경우) ML
    예측 표 HTML, 없으면 빈 문자열. 3개 템플릿이 모두 공유하는 블록이라 여기서 한 번만 만든다.
    stock은 article.stock이 아니라 resolve_thumbnail_stock(article)로 받은, 제목 기반으로
    확인된 종목이어야 한다 — article.stock은 NewsKeyword.linked_stock에서 온 분류용 값이라
    기사가 실제로 그 종목을 다룬다는 보장이 없다(예: "AI" 키워드로 잡힌 스페이스X 기사가
    article.stock=이스트소프트라, 예전엔 이스트소프트의 ML 예측이 붙어 나갔음)."""
    if not (stock and latest_pred and latest_pred.pred_next_close is not None):
        return ""
    signal_color = "#E53935" if latest_pred.trading_signal == 'BUY' else ("#1E88E5" if latest_pred.trading_signal == 'SELL' else "#757575")
    return f"""
    <div style="padding: 20px; border: 2px solid #EEE; border-radius: 10px; background-color: #FAFAFA; margin-bottom: 20px;">
        <h3 style="margin-top: 0; color: #333;">🤖 머신러닝 주가 추론 브리핑</h3>
        <p><b>🎯 분석 기준 종목:</b> {stock.name} ({stock.ticker})</p>
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


def _subject_label(article, stock):
    """stock은 resolve_thumbnail_stock(article)로 받은 제목 기반 종목 — article.stock(분류용
    키워드 연결 값)을 쓰면 _build_pred_html과 같은 오귀속 문제가 생긴다."""
    return stock.name if stock else (
        article.matched_keyword.keyword if article.matched_keyword else "경제"
    )


def _is_investment_related(article):
    """관련 종목/매칭 키워드가 있으면 증시·기업과 직접 연관된 기사로 본다 — 종목/키워드 매칭
    없이 등록된 기사(회원이 임의 URL을 스크랩했거나 '직접작성하기'로 쓴 글)는 문학·에세이 등
    투자와 무관한 내용일 수 있어(예: 개인 에세이, 소설 줄거리), 그런 글까지 "투자 인사이트"/
    "실전 투자 가이드" 같은 금융 톤을 강제로 씌우지 않기 위한 판정이다.
    thumbnail.build_thumbnail_file의 is_economic_news 기본값과 같은 기준을 쓴다."""
    return bool(article.stock or article.matched_keyword)


def _render_t1(article, safe_summary, blog_content_body, pred_html, resolved_stock):
    """템플릿 1 (뉴스 요약형): 뉴스 핵심 요약을 가장 먼저 보여주고, 투자 분석 → 실전 가이드
    순으로 이어지는 원래(기본) 레이아웃. 투자와 무관한 글(문학/에세이 등)이거나 AI 요약 없이
    '바로 포스팅'한 글은 해당 필드가 비어있을 수 있어 그 섹션 자체를 생략하고, 내용이 있어도
    투자 무관 글이면 제목을 "투자 관점 분석" 대신 "핵심 시사점"처럼 중립적으로 바꾼다."""
    investment_related = _is_investment_related(article)
    summary_html = f"""
        {_section_h3('📰 오늘의 뉴스 핵심 요약' if investment_related else '📝 핵심 요약')}
        <blockquote style="background: #F9F9F9; border-left: 10px solid #CCC; margin: 1.5em 10px; padding: 0.5em 10px;">
            {safe_summary}
        </blockquote>
    """ if article.ai_summary else ""
    analysis_html = f"""
        {_section_h3('💡 전문 투자 관점 분석' if investment_related else '💡 핵심 시사점')}
        <p>{article.ai_analysis}</p>
    """ if article.ai_analysis else ""
    disclaimer_html = _DISCLAIMER_HTML if investment_related else ""
    return f"""
    {pred_html}
    <div style="line-height: 1.8; font-size: 16px; color: #333;">
        {summary_html}
        {analysis_html}

        {_HR_HTML if (summary_html or analysis_html) else ""}

        {_section_h3('🚀 실전 투자 가이드 브리핑' if investment_related else '📝 본문')}
        {blog_content_body}

        {disclaimer_html}
    </div>
    """


def _render_t2(article, safe_summary, blog_content_body, pred_html, resolved_stock):
    """템플릿 2 (종목 분석형): 종목명을 헤더로 내세우고 ML 예측/투자 분석을 먼저 배치, 원본
    뉴스 요약은 맨 뒤에 참고 자료로 축소해서 붙인다 — 뉴스 자체보다 종목 분석이 중심."""
    investment_related = _is_investment_related(article)
    subject = _subject_label(article, resolved_stock)
    header = f"""
    <div style="padding: 16px 20px; background: #0D47A1; color: #fff; border-radius: 10px; margin-bottom: 20px;">
        <h2 style="margin: 0; font-size: 20px;">📊 종목 분석 리포트: {subject}</h2>
    </div>
    """
    analysis_html = f"""
        {_section_h3('💡 전문 투자 관점 분석' if investment_related else '💡 핵심 시사점')}
        <p>{article.ai_analysis}</p>

        {_HR_HTML}
    """ if article.ai_analysis else ""
    summary_html = f"""
        {_HR_HTML}

        {_section_h3('📰 관련 뉴스 요약 (참고)' if investment_related else '📝 요약 (참고)')}
        <blockquote style="background: #F9F9F9; border-left: 10px solid #CCC; margin: 1.5em 10px; padding: 0.5em 10px; font-size: 14px; color: #555;">
            {safe_summary}
        </blockquote>
    """ if article.ai_summary else ""
    disclaimer_html = _DISCLAIMER_HTML if investment_related else ""
    return f"""
    {header}
    {pred_html}
    <div style="line-height: 1.8; font-size: 16px; color: #333;">
        {analysis_html}

        {_section_h3('🚀 실전 투자 가이드 브리핑' if investment_related else '📝 본문')}
        {blog_content_body}

        {summary_html}

        {disclaimer_html}
    </div>
    """


def _render_t3(article, safe_summary, blog_content_body, pred_html, resolved_stock):
    """템플릿 3 (카드뉴스 대본형): 제목/3줄 요약/투자 시사점을 카드뉴스 슬라이드처럼 짧고
    굵은 카드 단위로 나열한 뒤, 실전 가이드 본문을 이어 붙인다."""
    investment_related = _is_investment_related(article)
    cards = [("HOOK", article.display_title)]
    if article.ai_summary:
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

    disclaimer_html = _DISCLAIMER_HTML if investment_related else ""
    return f"""
    {pred_html}
    <div style="line-height: 1.8; font-size: 16px; color: #333;">
        {_section_h3('🎬 카드뉴스 대본')}
        {card_html}

        {_HR_HTML}

        {_section_h3('🚀 실전 투자 가이드 브리핑' if investment_related else '📝 본문')}
        {blog_content_body}

        {disclaimer_html}
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
    값이 비어있거나 알 수 없는 경우 기본값인 T1(뉴스 요약형)으로 처리.
    ML 예측/종목 라벨은 article.stock이 아니라 resolve_thumbnail_stock(article)로 제목에서
    다시 확인한 종목을 쓴다 — article.stock은 NewsKeyword.linked_stock 분류값이라 기사 본문의
    실제 주제와 다를 수 있다(예: "AI" 키워드로 잡힌 스페이스X 기사의 article.stock=이스트소프트).
    thumbnail.build_thumbnail_file도 같은 이유로 이미 이 함수를 쓴다."""
    resolved_stock = resolve_thumbnail_stock(article)
    latest_pred = StockPrediction.objects.filter(stock=resolved_stock).order_by('-date').first() if resolved_stock else None

    safe_summary = article.ai_summary.replace('\n', '<br>')
    blog_content_body = _blog_content_body(article)
    pred_html = _build_pred_html(resolved_stock, latest_pred)

    renderer = _TEMPLATE_RENDERERS.get(article.applied_template, _render_t1)
    full_html_content = renderer(article, safe_summary, blog_content_body, pred_html, resolved_stock)

    if article.thumbnail:
        # 워드프레스/블로거는 외부 URL로 이미지를 그대로 fetch하므로 절대 URL이 필요하다
        # (article.thumbnail.url은 MEDIA_URL 기준 상대경로).
        thumbnail_url = f"{settings.SITE_URL}{article.thumbnail.url}"
        thumbnail_html = (
            f'<p><img src="{thumbnail_url}" alt="{article.display_title}" '
            'style="max-width:100%; height:auto; border-radius:10px; margin-bottom:20px;"></p>'
        )
        full_html_content = thumbnail_html + full_html_content

    subject_label = _subject_label(article, resolved_stock)
    # 게시판에 뜨는 표시 제목(article.display_title — AI 가공 제목이 있으면 그걸, 없으면 원본
    # 제목으로 폴백)을 그대로 살려서, 회원이 블로그 관리자 화면에서 봤을 때 게시판의 어느 기사가
    # 발행된 건지 바로 알아볼 수 있게 한다. 접두사는 "NextFinUp이 만든 콘텐츠"라는 걸 밝히지
    # 않도록 중립적인 표현만 붙인다 — 애드센스를 붙일 회원 본인의 블로그 글처럼 보여야 하기
    # 때문. 투자와 무관한 글(문학/에세이 등 '직접작성하기'로 쓴 글 포함)까지 "투자 인사이트"를
    # 붙이면 어색해서, 그런 경우엔 접두사를 붙이지 않는다.
    blog_title = f"[투자 인사이트] {article.display_title}" if _is_investment_related(article) else article.display_title

    return blog_title, full_html_content, subject_label


WP_CATEGORY_NAME = "BASIC"  # 카테고리를 안 넘기면 워드프레스가 전부 Uncategorized로 넣는다.


def _get_or_create_wp_category(account):
    """이 계정 사이트에 WP_CATEGORY_NAME 카테고리가 있으면 그 id, 없으면 새로 만들어 id를
    반환한다. 조회/생성 둘 다 실패하면 None — 호출부가 categories 없이(=Uncategorized로)
    발행을 계속 진행하게 하기 위함(카테고리 하나 때문에 발행 자체를 막지 않는다)."""
    try:
        res = requests.get(
            f"{account.site_url}/wp-json/wp/v2/categories",
            auth=(account.account_id, account.credential),
            params={'search': WP_CATEGORY_NAME, 'per_page': 100},
            timeout=10,
        )
        if res.status_code == 200:
            for cat in res.json():
                if cat.get('name') == WP_CATEGORY_NAME:
                    return cat['id']
    except Exception:
        pass

    try:
        res = requests.post(
            f"{account.site_url}/wp-json/wp/v2/categories",
            auth=(account.account_id, account.credential),
            json={'name': WP_CATEGORY_NAME},
            timeout=10,
        )
        body = res.json()
        if res.status_code == 201:
            return body.get('id')
        # 동시 요청 등으로 그 사이 이미 만들어졌으면 워드프레스가 term_exists 에러와 함께
        # 기존 id를 같이 돌려준다.
        return (body.get('data') or {}).get('term_id')
    except Exception:
        return None


def _upload_wp_featured_media(account, article):
    """article.thumbnail을 워드프레스 미디어 라이브러리에 업로드하고 미디어 ID를 반환한다.
    Astra 등 대부분의 테마는 블로그 목록/아카이브 그리드에 본문 속 <img>가 아니라 이 '대표
    이미지(featured_media)'만 인식해서 그려준다(timelessculturelab.com 확인 — featured_media가
    0이라 그리드에 썸네일이 전혀 안 보이던 문제) — build_post_content가 본문 맨 위에 넣는
    <img> 태그(단일 글 화면용)와는 별개로, 목록 화면 썸네일을 위해 항상 같이 올려야 한다.
    thumbnail이 없거나 업로드가 실패해도 None을 반환할 뿐 예외를 던지지 않는다 — 대표 이미지
    하나 때문에 글 발행 자체가 막히면 안 된다."""
    if not article.thumbnail:
        return None
    try:
        with article.thumbnail.open('rb') as f:
            image_bytes = f.read()
        filename = os.path.basename(article.thumbnail.name) or f"thumbnail-{article.pk}.png"
        res = requests.post(
            f"{account.site_url}/wp-json/wp/v2/media",
            auth=(account.account_id, account.credential),
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'image/png',
            },
            data=image_bytes,
            timeout=20,
        )
        if res.status_code == 201:
            return res.json().get('id')
        logger.warning(
            "워드프레스 대표 이미지 업로드 응답 에러 (account=%s, article=%s, status=%s): %s",
            account.pk, article.pk, res.status_code, res.text[:300],
        )
    except Exception:
        logger.exception("워드프레스 대표 이미지 업로드 실패 (account=%s, article=%s)", account.pk, article.pk)
    return None


def publish_to_wordpress(account, blog_title, content, article=None):
    """반환: (성공 여부, 발행된 글 URL, 글 ID, 실패 사유). 글 ID는 나중에 republish_article이
    같은 글을 업데이트(새 글 생성이 아니라)하는 데 쓴다."""
    payload = {"title": blog_title, "content": content, "status": WP_POST_STATUS}
    category_id = _get_or_create_wp_category(account)
    if category_id:
        payload["categories"] = [category_id]
    if article is not None:
        media_id = _upload_wp_featured_media(account, article)
        if media_id:
            payload["featured_media"] = media_id
            payload["content"] = _LEADING_THUMBNAIL_IMG_RE.sub('', payload["content"], count=1)

    try:
        res = requests.post(
            f"{account.site_url}/wp-json/wp/v2/posts",
            auth=(account.account_id, account.credential),
            json=payload,
            timeout=15,
        )
    except Exception as e:
        return False, '', '', f"네트워크 연동 실패: {e}"

    if res.status_code == 201:
        body = res.json()
        return True, body.get('link', ''), str(body.get('id', '')), None
    return False, '', '', f"워드프레스 API 응답 에러 ({res.status_code}): {res.text[:300]}"


def update_to_wordpress(account, post_id, blog_title, content, article=None):
    """반환: (성공 여부, 발행된 글 URL, 실패 사유). 워드프레스 REST API는 기존 글 엔드포인트에
    POST하면 update로 처리된다(PUT 대신 POST여도 동작 — 워드프레스 REST API 관례)."""
    payload = {"title": blog_title, "content": content, "status": WP_POST_STATUS}
    category_id = _get_or_create_wp_category(account)
    if category_id:
        payload["categories"] = [category_id]
    if article is not None:
        media_id = _upload_wp_featured_media(account, article)
        if media_id:
            payload["featured_media"] = media_id
            payload["content"] = _LEADING_THUMBNAIL_IMG_RE.sub('', payload["content"], count=1)

    try:
        res = requests.post(
            f"{account.site_url}/wp-json/wp/v2/posts/{post_id}",
            auth=(account.account_id, account.credential),
            json=payload,
            timeout=15,
        )
    except Exception as e:
        return False, '', f"네트워크 연동 실패: {e}"

    if res.status_code == 200:
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
    """반환: (성공 여부, 발행된 글 URL, 글 ID, 실패 사유). 글 ID는 나중에 republish_article이
    같은 글을 업데이트(새 글 생성이 아니라)하는 데 쓴다."""
    access_token, error = _get_blogger_access_token(account)
    if not access_token:
        return False, '', '', f"블로거 액세스 토큰 갱신 실패: {error or '알 수 없는 오류'} (마이페이지에서 블로거를 다시 연결해야 할 수 있습니다)"

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
        return False, '', '', f"네트워크 연동 실패: {e}"

    if res.status_code in (200, 201):
        return True, body.get('url', ''), str(body.get('id', '')), None
    return False, '', '', f"블로거 API 응답 에러 ({res.status_code}): {str(body)[:300]}"


def update_to_blogger(account, post_id, blog_title, content, article=None):
    """반환: (성공 여부, 발행된 글 URL, 실패 사유). Blogger API v3 게시물 수정은 PUT.
    article은 UPDATERS 딕셔너리를 워드프레스와 같은 시그니처로 맞추기 위한 파라미터일 뿐,
    Blogger 쪽은 대표 이미지 개념이 없어(블로거 기본 템플릿이 본문 첫 이미지를 목록 썸네일로
    자동 추출) 쓰지 않는다."""
    access_token, error = _get_blogger_access_token(account)
    if not access_token:
        return False, '', f"블로거 액세스 토큰 갱신 실패: {error or '알 수 없는 오류'} (마이페이지에서 블로거를 다시 연결해야 할 수 있습니다)"

    try:
        res = requests.put(
            f"https://www.googleapis.com/blogger/v3/blogs/{account.account_id}/posts/{post_id}",
            headers={'Authorization': f'Bearer {access_token}'},
            json={"title": blog_title, "content": content},
            timeout=15,
        )
        body = res.json()
    except Exception as e:
        return False, '', f"네트워크 연동 실패: {e}"

    if res.status_code == 200:
        return True, body.get('url', ''), None
    return False, '', f"블로거 API 응답 에러 ({res.status_code}): {str(body)[:300]}"


PUBLISHERS = {
    'WORDPRESS': lambda account, title, content, subject_label, article: publish_to_wordpress(account, title, content, article),
    'BLOGGER': lambda account, title, content, subject_label, article: publish_to_blogger(account, title, content),
}

UPDATERS = {
    'WORDPRESS': update_to_wordpress,
    'BLOGGER': update_to_blogger,
}


def _do_publish(account, article):
    """실제 플랫폼 발행 API 호출 한 번. publish_article/publish_article_force가 공유하는
    핵심 로직만 떼어낸 것 — '이미 발행됐는지' 가드는 호출부에서 각자 다르게 처리한다.
    반환: (성공 여부, 글 URL, 글 ID, 실패 사유)"""
    publisher = PUBLISHERS.get(account.platform)
    if publisher is None:
        return False, '', '', "지원하지 않는 플랫폼입니다."

    blog_title, content, subject_label = build_post_content(article)
    return publisher(account, blog_title, content, subject_label, article)


def publish_article(account, article):
    """기사 1건을 계정의 플랫폼에 맞춰 실제로 발행하고, 성공 시 PostedArticle을 기록한다.
    이미 이 계정×기사 조합으로 발행된 적 있으면 건너뛴다(중복 발행 방지) — 다시 새 글로
    발행하고 싶으면 publish_article_force를 쓴다.
    반환: (성공 여부, 발행된 글 URL 또는 실패 사유 메시지)"""
    if PostedArticle.objects.filter(blog_account=account, article=article).exists():
        return False, "이미 이 계정에 발행된 기사입니다."

    ok, url, post_id, error = _do_publish(account, article)
    if not ok:
        return False, error or "발행에 실패했습니다."

    PostedArticle.objects.create(blog_account=account, article=article, external_url=url, external_post_id=post_id or '')
    return True, url


def publish_article_force(account, article):
    """이미 이 계정에 발행된 기사여도 '이미 발행됨' 가드 없이 새 글로 다시 발행한다 — 실제
    발행 로직은 publish_article과 완전히 동일하다(포스팅 기능 그대로). 블로그에는 예전 글과
    별개로 새 글이 하나 더 생기고(예전 글은 그대로 남는다), PostedArticle은 계정×기사 조합당
    한 행만 추적 가능해서(unique_together) 이 최신 글을 가리키도록 갱신한다 — 예전 글 자체는
    이후 이 레코드로는 더 이상 추적되지 않지만 블로그에서 삭제되진 않는다.
    반환: (성공 여부, 발행된 글 URL 또는 실패 사유 메시지)"""
    ok, url, post_id, error = _do_publish(account, article)
    if not ok:
        return False, error or "발행에 실패했습니다."

    PostedArticle.objects.update_or_create(
        blog_account=account, article=article,
        defaults={'external_url': url, 'external_post_id': post_id or ''},
    )
    return True, url


def republish_article(account, article):
    """기사를 수정한 뒤(예: 제목/본문 템플릿 로직을 고친 뒤) 이미 발행된 계정에 새 글을 또
    만들지 않고, 원래 발행했던 그 글을 최신 내용으로 덮어쓴다. 이 계정에 발행된 적이 없으면
    (재발행이 아니라 최초 발행이므로) publish_article로 그대로 넘긴다.
    반환: (성공 여부, 발행된 글 URL 또는 실패 사유 메시지)"""
    posted = PostedArticle.objects.filter(blog_account=account, article=article).first()
    if not posted:
        return publish_article(account, article)

    if not posted.external_post_id:
        return False, "이 글은 발행 ID가 기록되기 전에 발행돼 재발행할 수 없습니다. 블로그에서 직접 수정해주세요."

    updater = UPDATERS.get(account.platform)
    if updater is None:
        return False, "지원하지 않는 플랫폼입니다."

    blog_title, content, subject_label = build_post_content(article)
    ok, url, error = updater(account, posted.external_post_id, blog_title, content, article)
    if not ok:
        return False, error or "재발행에 실패했습니다."

    posted.external_url = url or posted.external_url
    posted.save(update_fields=['external_url', 'updated_at'])
    return True, url
