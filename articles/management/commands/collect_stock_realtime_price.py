import time

from django.core.management.base import BaseCommand

from articles.kis_client import get_stock_current_price, is_market_open, is_regular_session_open
from articles.models import StockItem, StockRealtimePrice


class Command(BaseCommand):
    help = (
        '한국투자증권(KIS) 주식현재가 시세 API로 코스피200/코스닥150 종목의 실시간 현재가를 '
        '조회하여 StockRealtimePrice에 캐싱합니다 (종목 상세 페이지가 이 캐시를 읽음). '
        '정규장 마감(15:30 KST) 이후에는 시간외단일가로 캐시가 덮어써지지 않도록 수집을 건너뜁니다.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--sleep', type=float, default=0.3,
                             help='종목 간 대기 시간(초, KIS 호출 제한 방지). 기본 0.3')

    def handle(self, *args, **options):
        if not is_market_open():
            self.stdout.write(self.style.WARNING("[-] 오늘은 휴장일입니다. 실시간 현재가 수집을 건너뜁니다."))
            return

        if not is_regular_session_open():
            self.stdout.write(self.style.WARNING(
                "[-] 정규장 시간(09:00~15:30 KST)이 아닙니다. 시간외단일가 등으로 캐시가 덮어써지지 "
                "않도록 실시간 현재가 수집을 건너뜁니다 (마감가 캐시를 그대로 유지)."
            ))
            return

        sleep_sec = options['sleep']
        stocks = list(StockItem.objects.filter(is_active=True, is_major_index=True))
        total = len(stocks)

        if total == 0:
            self.stdout.write(self.style.WARNING(
                "[-] is_major_index=True 종목이 없습니다. sync_index_membership 명령을 먼저 실행했는지 확인하세요."
            ))
            return

        self.stdout.write(self.style.SUCCESS(f"🚀 {total}개 종목 실시간 현재가 수집을 시작합니다."))

        success_count = 0
        for i, stock in enumerate(stocks, start=1):
            try:
                price = get_stock_current_price(stock.ticker)
                StockRealtimePrice.objects.update_or_create(
                    stock=stock,
                    defaults=dict(
                        close_price=price['close'],
                        open_price=price['open'],
                        high_price=price['high'],
                        low_price=price['low'],
                        change=price['change'],
                        change_pct=price['change_pct'],
                        volume=price['volume'],
                        per=price['per'],
                        pbr=price['pbr'],
                        eps=price['eps'],
                        bps=price['bps'],
                        market_cap=price['market_cap'],
                        week52_high=price['week52_high'],
                        week52_low=price['week52_low'],
                    ),
                )
                success_count += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"    ↳ [{stock.ticker}] {stock.name} 조회 실패: {e}"))

            if i < total:
                time.sleep(sleep_sec)

        self.stdout.write(self.style.SUCCESS(f"🎉 실시간 현재가 수집 완료: {success_count}/{total}건 성공"))
