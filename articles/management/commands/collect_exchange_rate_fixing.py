from datetime import date, timedelta

from django.core.management.base import BaseCommand

from articles.exim_client import FX_FIXING_ITEMS, get_exchange_rates
from articles.models import ExchangeRateSnapshot, GlobalMarketQuote

# 고시가 없는 날(주말/공휴일)을 만나면 이만큼 과거로 거슬러 올라가며 가장 최근 영업일을 찾는다.
MAX_LOOKBACK_DAYS = 7


class Command(BaseCommand):
    help = (
        '한국수출입은행 OpenAPI(환전 고시 환율)로 미국/일본/유럽연합/중국 매매기준율을 조회하여 '
        'ExchangeRateSnapshot에 날짜별로 쌓고, 직전 영업일 대비 등락률을 계산해 '
        'GlobalMarketQuote(category=FX_FIXING)로 올립니다(헤더 지수 티커용).'
    )

    def handle(self, *args, **options):
        today = date.today()
        rows, quote_date = None, None
        for offset in range(MAX_LOOKBACK_DAYS):
            check_date = today - timedelta(days=offset)
            try:
                fetched = get_exchange_rates(check_date.strftime('%Y%m%d'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"    ↳ {check_date} 조회 실패: {e}"))
                continue
            if fetched:
                rows, quote_date = fetched, check_date
                break

        if not rows:
            self.stdout.write(self.style.WARNING(f"[-] 최근 {MAX_LOOKBACK_DAYS}일간 고시 데이터가 없습니다."))
            return

        by_unit = {r['cur_unit']: r for r in rows}
        saved = 0
        for i, (cur_unit, label) in enumerate(FX_FIXING_ITEMS.items(), start=1):
            row = by_unit.get(cur_unit)
            if not row:
                self.stdout.write(self.style.WARNING(f"    ↳ {label}({cur_unit}) 이번 응답에 없음 - 건너뜀"))
                continue

            ExchangeRateSnapshot.objects.update_or_create(
                currency_code=cur_unit, date=quote_date,
                defaults=dict(currency_name=row['cur_nm'], deal_bas_r=row['deal_bas_r']),
            )

            prev = (
                ExchangeRateSnapshot.objects
                .filter(currency_code=cur_unit, date__lt=quote_date)
                .order_by('-date')
                .first()
            )
            change_pct = 0.0
            if prev and prev.deal_bas_r:
                change_pct = float((row['deal_bas_r'] - float(prev.deal_bas_r)) / float(prev.deal_bas_r) * 100)

            GlobalMarketQuote.objects.update_or_create(
                category='FX_FIXING', code=cur_unit,
                defaults=dict(name=label, price=row['deal_bas_r'], change_pct=round(change_pct, 2), order=i),
            )
            saved += 1
            self.stdout.write(f"    ↳ {label}: {row['deal_bas_r']} ({change_pct:+.2f}%)")

        self.stdout.write(self.style.SUCCESS(f"🎉 환전 고시 환율 수집 완료 ({quote_date} 기준, {saved}건)."))
