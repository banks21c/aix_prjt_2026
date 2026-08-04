from django.core.management.base import BaseCommand

from articles.kis_client import GLOBAL_QUOTE_ITEMS, INTEREST_RATE_ITEMS, get_interest_rates, get_overseas_index_price
from articles.models import GlobalMarketQuote


class Command(BaseCommand):
    help = (
        '한국투자증권(KIS) API로 해외지수(다우/나스닥/홍콩H/니케이 등)/국제 시장 환율/금리를 '
        '조회하여 GlobalMarketQuote에 저장합니다 (헤더 지수 티커용). 코스피/코스닥과 달리 해외 '
        '시장은 한국 휴장일과 무관하게 항상 갱신합니다.'
    )

    def handle(self, *args, **options):
        order = 0
        for category, items in GLOBAL_QUOTE_ITEMS.items():
            for code, (mrkt_div, label) in items.items():
                order += 1
                try:
                    quote = get_overseas_index_price(mrkt_div, code)
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"    ↳ [{category}/{code}] 조회 실패: {e}"))
                    continue
                GlobalMarketQuote.objects.update_or_create(
                    category=category, code=code,
                    defaults=dict(name=label, price=quote['price'], change_pct=quote['change_pct'], order=order),
                )
                self.stdout.write(f"    ↳ [{category}] {label}: {quote['price']} ({quote['change_pct']}%)")

        try:
            rates = get_interest_rates()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"    ↳ [INTEREST_RATE] 조회 실패: {e}"))
            rates = {}

        for i, (bcdt_code, label) in enumerate(INTEREST_RATE_ITEMS.items(), start=1):
            rate = rates.get(bcdt_code)
            if not rate:
                self.stdout.write(self.style.WARNING(f"    ↳ [INTEREST_RATE] {label}({bcdt_code}) 응답에 없음 - 건너뜀"))
                continue
            GlobalMarketQuote.objects.update_or_create(
                category='INTEREST_RATE', code=bcdt_code,
                defaults=dict(name=label, price=rate['price'], change_pct=rate['change_pct'], order=i),
            )
            self.stdout.write(f"    ↳ [INTEREST_RATE] {label}: {rate['price']} ({rate['change_pct']}%)")

        self.stdout.write(self.style.SUCCESS("🎉 해외지수/환율/금리 수집이 완료되었습니다."))
