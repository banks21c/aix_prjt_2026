from django.core.management.base import BaseCommand
from django.utils import timezone

from articles import article_ai, thumbnail
from articles.models import AnalyzedArticle, MarketIndex, StockDailyPrice

TOP_N = 5


class Command(BaseCommand):
    help = (
        '지난 5거래일(월~금)의 코스피/코스닥 주간 등락과, is_major_index 종목의 주간 수익률 '
        '상위/하위 종목을 모아 "주간 시황 정리" 브리핑 1건을 생성해 AnalyzedArticle로 저장합니다. '
        '토요일 아침에 실행하도록 설계되었습니다(generate_featured_stock_briefing과 달리 휴장일 '
        '체크 없이, 그냥 최근 거래일 데이터를 기준으로 계산합니다).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='이번 주 브리핑이 이미 있어도 강제로 다시 생성합니다.',
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        pseudo_url = f"internal://weekly-briefing/{today}"
        if not options['force'] and AnalyzedArticle.objects.filter(original_url=pseudo_url).exists():
            self.stdout.write(self.style.WARNING(f"[-] {today} 주간 시황 정리가 이미 있습니다."))
            return

        index_summary, week_start, week_end = self._build_index_summary()
        if not index_summary:
            self.stdout.write(self.style.WARNING(
                "[-] MarketIndex 데이터가 부족합니다 (최소 6거래일치 필요). collect_market_index가 "
                "먼저 충분히 쌓여야 합니다."
            ))
            return

        movers = self._build_weekly_movers(week_start, week_end)
        if not movers:
            self.stdout.write(self.style.WARNING(
                "[-] StockDailyPrice 데이터가 부족해 주간 급등락 종목을 계산하지 못했습니다."
            ))

        self.stdout.write(self.style.SUCCESS(
            f"🚀 {week_start} ~ {week_end} 주간 시황 정리를 생성합니다. (종목 {len(movers)}건)"
        ))
        draft = article_ai.generate_weekly_market_briefing(index_summary, movers)
        title = f"{today:%Y-%m-%d} 주간 시황 정리"

        thumb_index_summary = [
            {
                'label': '코스피' if idx['market_type'] == 'KOSPI' else '코스닥',
                'close': idx['close_price'],
                'change': idx['change'],
                'change_pct': idx['change_pct'],
                'week_closes': idx['week_closes'],
            }
            for idx in index_summary
        ]

        AnalyzedArticle.objects.update_or_create(
            original_url=pseudo_url,
            defaults=dict(
                stock=None,
                matched_keyword=None,
                title=title,
                source_media="NextFinUp AI 브리핑",
                source_type=AnalyzedArticle.SOURCE_AI_BRIEFING,
                ai_summary=draft['ai_summary'],
                ai_analysis=draft['ai_analysis'],
                blog_content=draft['blog_content'],
                original_content=article_ai.weekly_movers_to_text(index_summary, movers),
                thumbnail=thumbnail.build_thumbnail_file(
                    title, category_label="주간 시황 정리", is_economic_news=True,
                    index_summary=thumb_index_summary,
                ),
                applied_template='T1',
                is_premium=False,
                is_posted=False,
                ai_generated=True,
                ai_summarized_by=None,
                ai_summarized_at=timezone.now(),
            ),
        )
        self.stdout.write(self.style.SUCCESS(f"🎉 주간 시황 정리 저장 완료: {title}"))

    def _build_index_summary(self):
        """KOSPI/KOSDAQ 각각 최근 6거래일치 날짜(기준일 1개 + 이번 주 5거래일)를 모아, 기준일
        종가 대비 이번 주 마지막 거래일 종가의 등락률을 계산한다. 달력상 월~금이 아니라 실제
        MarketIndex에 쌓인 거래일 기준이라 공휴일이 껴도 안전하다.
        week_closes(이번 주 5거래일 종가 목록)도 함께 담아, 썸네일 카드에 주간 추이 스파크라인을
        그릴 수 있게 한다(thumbnail._draw_sparkline)."""
        summary = []
        week_start = week_end = None
        for market_type, _ in MarketIndex.MARKET_CHOICES:
            rows = list(
                MarketIndex.objects
                .filter(market_type=market_type)
                .order_by('-date')[:6]
            )
            if len(rows) < 6:
                continue
            rows.reverse()  # 오래된 → 최신
            baseline, this_week_rows = rows[0], rows[1:]
            latest = this_week_rows[-1]
            if not baseline.close_price or not latest.close_price:
                continue
            if any(r.close_price is None for r in this_week_rows):
                continue
            change = float(latest.close_price - baseline.close_price)
            change_pct = change / float(baseline.close_price) * 100
            summary.append({
                'market_type': market_type,
                'close_price': float(latest.close_price),
                'change': change,
                'change_pct': change_pct,
                'week_closes': [float(r.close_price) for r in this_week_rows],
            })
            week_start, week_end = this_week_rows[0].date, this_week_rows[-1].date
        return summary, week_start, week_end

    def _build_weekly_movers(self, week_start, week_end):
        if not week_start or not week_end:
            return []

        rows = (
            StockDailyPrice.objects
            .filter(stock__is_major_index=True, date__in=(week_start, week_end))
            .select_related('stock')
            .values('stock_id', 'stock__ticker', 'stock__name', 'date', 'close_price')
        )
        by_stock = {}
        for r in rows:
            by_stock.setdefault(r['stock_id'], {})[r['date']] = r

        changes = []
        for stock_id, by_date in by_stock.items():
            start_row, end_row = by_date.get(week_start), by_date.get(week_end)
            if not start_row or not end_row or not start_row['close_price']:
                continue
            change_pct = float(
                (end_row['close_price'] - start_row['close_price']) / start_row['close_price'] * 100
            )
            changes.append({
                'ticker': end_row['stock__ticker'],
                'name': end_row['stock__name'],
                'close_price': float(end_row['close_price']),
                'change_pct': change_pct,
            })

        changes.sort(key=lambda c: c['change_pct'], reverse=True)
        # top_n을 표본의 절반 이하로 제한해, 종목 수가 아주 적을 때 상승/하락 상위 목록이
        # 겹치지 않게 한다(예: 종목 6개면 상위 5/하위 5가 대부분 같은 종목을 가리키게 됨).
        top_n = min(TOP_N, len(changes) // 2)
        if top_n == 0:
            return []
        gainers = [dict(c, rank_type='GAINER') for c in changes[:top_n]]
        losers = [dict(c, rank_type='LOSER') for c in changes[-top_n:][::-1]]
        return gainers + losers
