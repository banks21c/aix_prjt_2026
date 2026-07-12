"""회원별 자동 포스팅(워드프레스/티스토리/네이버 블로그) 공통 로직.

각 플랫폼 커맨드(post_to_wordpress, post_to_tistory, post_to_naver)가 공유하는
"발행 대상 기사 선정"과 "포스팅용 콘텐츠 빌드"를 한 곳에 모아, 3개 커맨드에서
동일한 HTML 템플릿/필터링 로직이 중복되지 않도록 한다.
"""
from .models import AnalyzedArticle, BlogPostingAccount, StockPrediction


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
    """이 계정에 아직 발행되지 않은 기사 중, 관심 키워드(또는 전체 발행 설정)에 맞는 기사 목록."""
    candidates = (
        AnalyzedArticle.objects
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


def build_post_content(article):
    """기사 + 최신 ML 예측을 결합한 블로그 포스팅용 (제목, HTML 본문, 종목/키워드 라벨) 반환."""
    latest_pred = StockPrediction.objects.filter(stock=article.stock).order_by('-date').first() if article.stock else None

    safe_summary = article.ai_summary.replace('\n', '<br>')
    safe_blog_content = article.blog_content.replace('\n', '<br>')

    pred_html = ""
    if latest_pred and latest_pred.pred_next_close is not None:
        signal_color = "#E53935" if latest_pred.trading_signal == 'BUY' else ("#1E88E5" if latest_pred.trading_signal == 'SELL' else "#757575")
        pred_html = f"""
        <div style="padding: 20px; border: 2px solid #EEE; border-radius: 10px; background-color: #FAFAFA; margin-bottom: 20px;">
            <h3 style="margin-top: 0; color: #333;">🤖 NextFinUp 머신러닝 주가 추론 브리핑</h3>
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

    full_html_content = f"""
    {pred_html}
    <div style="line-height: 1.8; font-size: 16px; color: #333;">
        <h3 style="color: #0D47A1; border-left: 5px solid #0D47A1; padding-left: 10px;">📰 AI 에이전트 뉴스 실시간 요약</h3>
        <blockquote style="background: #F9F9F9; border-left: 10px solid #CCC; margin: 1.5em 10px; padding: 0.5em 10px;">
            {safe_summary}
        </blockquote>

        <h3 style="color: #0D47A1; border-left: 5px solid #0D47A1; padding-left: 10px;">💡 전문 투자 관점 분석</h3>
        <p>{article.ai_analysis}</p>

        <hr style="border: 0; height: 1px; background: #CCC; margin: 30px 0;">

        <h3 style="color: #0D47A1; border-left: 5px solid #0D47A1; padding-left: 10px;">🚀 실전 투자 가이드 브리핑</h3>
        <p>{safe_blog_content}</p>

        <p style="font-size: 12px; color: #888; margin-top: 5px;">본 포스팅은 NextFinUp 시스템의 머신러닝 알고리즘과 AI 에이전트가 자동으로 가공한 경제 정보 콘텐츠이며, 투자 참고용으로만 사용하시기 바랍니다.</p>
    </div>
    """

    subject_label = article.stock.name if article.stock else (
        article.matched_keyword.keyword if article.matched_keyword else "경제"
    )
    blog_title = f"[NextFinUp AI 분석] {subject_label} 관련 핵심 뉴스 브리핑"

    return blog_title, full_html_content, subject_label
