from django.core.management.base import BaseCommand

from articles.kis_client import get_news_titles
from articles.models import AnalyzedArticle, StockItem, StockRealtimePrice


def _price_note(realtime):
    """StockRealtimePrice 캐시가 있으면 '(현재가 N원, 전일대비 +N.NN%)' 형태의 문구를 만든다.
    KIS 시황_공시(제목) API 자체는 '왜' 특징주인지는 안 주지만, 종목코드는 주므로, 우리가 이미
    collect_stock_realtime_price로 5분마다 갱신해둔 캐시(is_major_index 종목만 커버)와 join하면
    추가 API 호출 없이 등락 폭이라도 붙일 수 있다. 캐시가 없는 종목(비index 종목)은 그냥 라벨만
    남긴다 — 숫자를 지어내지 않기 위해 추가 API 호출로 그때그때 채우진 않는다."""
    if realtime is None:
        return ''
    return f" (현재가 {realtime.close_price:,.0f}원, 전일대비 {realtime.change_pct:+.2f}%)"


class Command(BaseCommand):
    help = (
        '한국투자증권(KIS) 종합 시황_공시(제목) API로 전 종목 뉴스/공시 제목을 조회하여, '
        '모니터링 중인 종목코드가 매칭되면 AnalyzedArticle에 저장합니다 '
        '(RSS 키워드 수집(collect_keyword_news)과 병행되는 별도 소스). 매칭된 종목에 '
        'StockRealtimePrice 캐시(is_major_index 종목만 5분마다 갱신됨)가 있으면 추가 API 호출 '
        '없이 그 시점 등락률을 같이 저장합니다.'
    )

    def handle(self, *args, **options):
        stock_by_ticker = {s.ticker: s for s in StockItem.objects.filter(is_active=True)}
        if not stock_by_ticker:
            self.stdout.write(self.style.WARNING("[-] 활성화된 모니터링 종목이 없습니다."))
            return

        realtime_by_stock_id = {
            rp.stock_id: rp for rp in StockRealtimePrice.objects.all()
        }

        self.stdout.write(self.style.SUCCESS("🚀 KIS 종합 시황_공시(제목) 수집을 시작합니다."))

        try:
            rows = get_news_titles()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[-] KIS 뉴스/공시 조회 실패: {e}"))
            return

        count = 0
        for row in rows:
            matched_stock = next(
                (stock_by_ticker[ticker] for ticker in row['tickers'] if ticker in stock_by_ticker),
                None,
            )
            if matched_stock is None:
                continue

            # 원문 링크가 없는 API라, 내용 조회용 일련번호(serial_no)로 중복 저장 방지용 고유 URL을 대신 구성
            pseudo_url = f"kis-news://{row['serial_no']}"
            if AnalyzedArticle.objects.filter(original_url=pseudo_url).exists():
                continue

            note = _price_note(realtime_by_stock_id.get(matched_stock.pk))

            self.stdout.write(self.style.SUCCESS(
                f"    ↳ [KIS 뉴스/공시 매칭] {matched_stock.name} ➔ {row['title'][:30]}...{note}"
            ))

            AnalyzedArticle.objects.create(
                stock=matched_stock,
                title=row['title'],
                original_url=pseudo_url,
                source_media=row['source'],
                source_type=AnalyzedArticle.SOURCE_KIS,
                applied_template='T1',
                is_premium=False,
                is_posted=False,
            )
            count += 1

        if count == 0:
            self.stdout.write(self.style.WARNING("[-] 이번 수집에서 모니터링 종목에 매칭되는 새 뉴스/공시가 없습니다."))
        else:
            self.stdout.write(self.style.SUCCESS(f"🎉 총 {count}건의 KIS 뉴스/공시를 저장했습니다."))
