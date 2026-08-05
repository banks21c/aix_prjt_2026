import time
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from articles.exim_client import FX_FIXING_ITEMS, get_exchange_rates
from articles.models import ExchangeRateSnapshot, GlobalMarketQuote

# 고시가 없는 날(주말/공휴일)을 만나면 이만큼 과거로 거슬러 올라가며 가장 최근 영업일을 찾는다.
MAX_LOOKBACK_DAYS = 7

# 한국수출입은행 API가 SSL/연결 오류로 순간적으로 실패하는 경우(실측: 2026-08-05 00:00~02:30
# 사이 반복 실패 후 자연 복구됨)를 대비해, 날짜 하나당 이만큼 짧게 재시도한 뒤에도 안 되면
# MAX_LOOKBACK_DAYS의 다음 날짜로 넘어간다 — 무한 재시도 방지를 위해 횟수를 제한한다.
FETCH_RETRY_ATTEMPTS = 3
FETCH_RETRY_BACKOFF_SECONDS = 3


class Command(BaseCommand):
    help = (
        '한국수출입은행 OpenAPI(환전 고시 환율)로 미국/일본/유럽연합/중국 매매기준율을 조회하여 '
        'ExchangeRateSnapshot에 날짜별로 쌓고, 직전 영업일 대비 등락률을 계산해 '
        'GlobalMarketQuote(category=FX_FIXING)로 올립니다(헤더 지수 티커용).'
    )

    def _fetch_with_retry(self, date_str):
        """SSL/연결 오류 등 순간적인 실패는 짧게 재시도하고, FETCH_RETRY_ATTEMPTS번 다 실패하면
        마지막 예외를 그대로 올려서 호출부가 다음 날짜로 넘어가게 한다."""
        last_error = None
        for attempt in range(1, FETCH_RETRY_ATTEMPTS + 1):
            try:
                return get_exchange_rates(date_str)
            except Exception as e:
                last_error = e
                if attempt < FETCH_RETRY_ATTEMPTS:
                    self.stdout.write(self.style.WARNING(
                        f"    ↳ {date_str} 조회 실패({attempt}/{FETCH_RETRY_ATTEMPTS}), "
                        f"{FETCH_RETRY_BACKOFF_SECONDS}초 후 재시도: {e}"
                    ))
                    time.sleep(FETCH_RETRY_BACKOFF_SECONDS)
        raise last_error

    def handle(self, *args, **options):
        today = date.today()
        rows, quote_date = None, None
        for offset in range(MAX_LOOKBACK_DAYS):
            check_date = today - timedelta(days=offset)
            try:
                fetched = self._fetch_with_retry(check_date.strftime('%Y%m%d'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f"    ↳ {check_date} 조회 실패(재시도 {FETCH_RETRY_ATTEMPTS}회 모두 실패): {e}"
                ))
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
