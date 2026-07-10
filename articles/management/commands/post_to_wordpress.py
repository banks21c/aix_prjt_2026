import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from articles.models import AnalyzedArticle, StockPrediction

class Command(BaseCommand):
    help = 'AI가 가공한 투자 분석 원고와 ML 주가 예측 데이터를 결합하여 워드프레스 블로그에 발행합니다.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help='한 번에 발행할 최대 건수 (생략 시 미발행 기사 전체)',
        )

    def handle(self, *args, **options):
        unpublished_articles = AnalyzedArticle.objects.filter(is_posted=False).select_related('stock')
        limit = options.get('limit')
        if limit is not None:
            unpublished_articles = unpublished_articles[:limit]

        if not unpublished_articles.exists():
            self.stdout.write(self.style.WARNING("[-] 오늘 새로 발행할 AI 분석 원고가 없습니다."))
            return

        self.stdout.write(self.style.SUCCESS("🚀 워드프레스 블로그 AI 카드뉴스 발행을 시작합니다."))

        # 워드프레스 REST API 연동 정보 (Application Password 방식, config/settings.py에서 .env로 관리)
        WP_SITE_URL = settings.WP_SITE_URL
        WP_USERNAME = settings.WP_USERNAME
        WP_APP_PASSWORD = settings.WP_APP_PASSWORD
        # 첫 포스팅이라 바로 공개되지 않도록 임시저장으로 올림. 검증 끝나면 "publish"로 변경.
        WP_POST_STATUS = "draft"

        for article in unpublished_articles:
            latest_pred = StockPrediction.objects.filter(stock=article.stock).order_by('-date').first()

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

            payload = {
                "title": blog_title,
                "content": full_html_content,
                "status": WP_POST_STATUS,
            }
            try:
                res = requests.post(
                    f"{WP_SITE_URL}/wp-json/wp/v2/posts",
                    auth=(WP_USERNAME, WP_APP_PASSWORD),
                    json=payload,
                    timeout=15,
                )
                if res.status_code == 201:
                    post = res.json()
                    article.is_posted = True
                    article.save()
                    self.stdout.write(self.style.SUCCESS(
                        f"    ↳ [발행 성공] {subject_label} 글이 워드프레스에 {WP_POST_STATUS}(으)로 등록되었습니다: {post.get('link')}"
                    ))
                else:
                    self.stdout.write(self.style.ERROR(f"    ↳ 워드프레스 API 응답 에러 ({res.status_code}): {res.text[:300]}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"    ↳ 네트워크 연동 실패: {str(e)}"))

        self.stdout.write(self.style.SUCCESS("🎉 오늘의 모든 AI 재가공 글 발행 프로세스가 종료되었습니다!"))
