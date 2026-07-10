import requests
from django.core.management.base import BaseCommand
from articles.models import AnalyzedArticle, StockPrediction

class Command(BaseCommand):
    help = 'AI가 가공한 투자 분석 원고와 ML 주가 예측 데이터를 결합하여 티스토리 블로그에 자동 발행합니다.'

    def handle(self, *args, **options):
        # 아직 블로그에 발행되지 않은 최신 AI 가공 기사들을 가져옵니다.
        unpublished_articles = AnalyzedArticle.objects.filter(is_posted=False).select_related('stock')
        
        if not unpublished_articles.exists():
            self.stdout.write(self.style.WARNING("[-] 오늘 새로 발행할 AI 분석 원고가 없습니다."))
            return

        self.stdout.write(self.style.SUCCESS("🚀 티스토리 블로그 AI 카드뉴스 자동 발행을 시작합니다."))

        # 티스토리 오픈 API 연동 키 (추후 환경변수 설정 권장)
        TISTORY_ACCESS_TOKEN = "YOUR_TISTORY_ACCESS_TOKEN_HERE"
        TISTORY_BLOG_NAME = "YOUR_BLOG_NAME" # ex) nextfinup (URL의 .tistory.com 앞부분)

        for article in unpublished_articles:
            # 해당 종목의 가장 최신 AI 머신러닝 예측 데이터 추출
            latest_pred = StockPrediction.objects.filter(stock=article.stock).order_by('-date').first()
            
            # f-string 내부 역슬래시 충돌 우회를 위해 외부에서 문자열 미리 가공
            safe_summary = article.ai_summary.replace('\n', '<br>')
            safe_blog_content = article.blog_content.replace('\n', '<br>')
            
            # ----------------------------------------------------
            # 1. 기획안 스펙 반영: 포스팅용 고품격 HTML 템플릿 빌드
            # ----------------------------------------------------
            pred_html = ""
            if latest_pred:
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

            # 최종 블로그 본문 내용 합성
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

            # ----------------------------------------------------
            # 2. 티스토리 오픈 API 실제 아웃바운드 발행 처리
            # ----------------------------------------------------
            # 종목과 연결되지 않은 키워드 기반 기사(article.stock=None)도 있으므로 안전하게 제목/태그용 이름을 결정
            subject_label = article.stock.name if article.stock else (
                article.matched_keyword.keyword if article.matched_keyword else "경제"
            )
            blog_title = f"[NextFinUp AI 분석] {subject_label} 관련 핵심 뉴스 브리핑"

            if TISTORY_ACCESS_TOKEN != "YOUR_TISTORY_ACCESS_TOKEN_HERE":
                url = "https://tistory.com"
                payload = {
                    "access_token": TISTORY_ACCESS_TOKEN,
                    "output": "json",
                    "blogName": TISTORY_BLOG_NAME,
                    "title": blog_title,
                    "content": full_html_content,
                    "visibility": 3, # 3: 발행(공개), 0: 비공개
                    "category": 0,    # 블로그 내 카테고리 ID 번호
                    "tag": f"{subject_label}, 경제뉴스, AI투자, 테크핀"
                }
                try:
                    res = requests.post(url, data=payload, timeout=15).json()
                    if "tistory" in res and res["tistory"]["status"] == "200":
                        article.is_posted = True
                        article.save()
                        self.stdout.write(self.style.SUCCESS(f"    ↳ [발행 성공] {article.stock.name} 글이 티스토리에 등록되었습니다!"))
                    else:
                        self.stdout.write(self.style.ERROR(f"    ↳ 티스토리 API 응답 에러: {res}"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"    ↳ 네트워크 연동 실패: {str(e)}"))
            else:
                # API 토큰 세팅 전, 개발 검증용 가상 로그 출력
                self.stdout.write(self.style.SUCCESS(f"    ↳ [시뮬레이션 완료] 티스토리 API 미연동 상태로 가상 템플릿 빌드 성공!"))
                self.stdout.write(f"    [제목] {blog_title}")
                # 테스트 목적으로 포스팅 성공 상태로 마킹 변경 처리
                article.is_posted = True
                article.save()

        self.stdout.write(self.style.SUCCESS("🎉 오늘의 모든 AI 재가공 글 발행 프로세스가 종료되었습니다!"))

