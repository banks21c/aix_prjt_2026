from datetime import date, datetime

from django.core.management.base import BaseCommand

from articles.kis_client import get_index_daily_price, get_index_price, get_investor_trend, is_market_open
from articles.models import MarketIndex

MARKET_TYPES = ['KOSPI', 'KOSDAQ']


class Command(BaseCommand):
    help = (
        '한국투자증권(KIS) API로 코스피/코스닥 종합지수 일별 시계열 + 오늘자 실시간 값을 '
        'MarketIndex에 저장합니다 (대시보드 지수 차트용).'
    )

    def handle(self, *args, **options):
        if not is_market_open():
            self.stdout.write(self.style.WARNING("[-] 오늘은 휴장일입니다. 지수 수집을 건너뜁니다."))
            return

        for market_type in MARKET_TYPES:
            self.stdout.write(f"[-] {market_type} 지수 일자별 데이터 수집 중...")
            self._backfill_daily(market_type)

            # 일봉에는 오늘자 행이 한 번 생기고 나면 갱신되지 않으므로, KIS 실시간 지수로 오늘자를 계속 upsert
            self._update_today_from_kis(market_type)

        self.stdout.write(self.style.SUCCESS("🎉 지수 데이터 수집이 완료되었습니다."))

    def _backfill_daily(self, market_type):
        """국내업종 일자별지수 API로 최근 100영업일치 과거 데이터를 채웁니다."""
        try:
            rows = get_index_daily_price(market_type, date.today().strftime('%Y%m%d'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"    ↳ 수집 실패: {e}"))
            return

        if not rows:
            self.stdout.write(self.style.WARNING(f"    ↳ {market_type} 수신 데이터 없음"))
            return

        existing_dates = set(
            MarketIndex.objects.filter(market_type=market_type).values_list('date', flat=True)
        )

        created = 0
        for row in rows:
            record_date = datetime.strptime(row['date'], '%Y%m%d').date()
            if record_date in existing_dates:
                continue

            MarketIndex.objects.create(
                market_type=market_type,
                date=record_date,
                open_price=round(row['open'], 2),
                high_price=round(row['high'], 2),
                low_price=round(row['low'], 2),
                close_price=round(row['close'], 2),
                change=round(row['change'], 2),
                change_pct=round(row['change_pct'], 2),
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"    ↳ {market_type}: 신규 {created}건 저장 완료"))

    def _update_today_from_kis(self, market_type):
        """장중 실시간 갱신: 한국투자증권 국내업종 현재지수 API로 오늘자 시가/고가/저가/현재가를 upsert합니다."""
        try:
            quote = get_index_price(market_type)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"    ↳ {market_type} KIS 실시간 갱신 실패: {e}"))
            return

        defaults = dict(
            open_price=round(quote['open'], 2),
            high_price=round(quote['high'], 2),
            low_price=round(quote['low'], 2),
            close_price=round(quote['close'], 2),
            change=round(quote['change'], 2),
            change_pct=round(quote['change_pct'], 2),
            volume=quote['volume'],
        )

        # 별도 API라 독립적으로 실패할 수 있음 — 실패해도 지수 자체 갱신은 계속 진행
        try:
            trend = get_investor_trend(market_type)
            defaults.update(
                foreign_net_qty=trend['foreign_net_qty'],
                institution_net_qty=trend['institution_net_qty'],
                retail_net_qty=trend['retail_net_qty'],
                foreign_net_amount=round(trend['foreign_net_amount'], 2),
                institution_net_amount=round(trend['institution_net_amount'], 2),
                retail_net_amount=round(trend['retail_net_amount'], 2),
            )
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"    ↳ {market_type} 투자자매매동향 조회 실패: {e}"))

        today = date.today()
        _, created = MarketIndex.objects.update_or_create(
            market_type=market_type,
            date=today,
            defaults=defaults,
        )
        action = '신규' if created else '갱신'
        self.stdout.write(self.style.SUCCESS(
            f"    ↳ {market_type} 오늘({today}) KIS 실시간 {action}: {quote['close']} ({quote['change_pct']}%)"
        ))
