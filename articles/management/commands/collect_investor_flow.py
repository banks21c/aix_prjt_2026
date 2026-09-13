import time
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand

from articles.kis_client import KST, get_investor_trade_by_stock
from articles.models import StockInvestorFlow, StockItem

# KIS 문서: "당일 데이터는 15:40 이후에 집계·산출되어 조회 가능" — 그 전에 오늘 날짜로 조회하면
# "TIME LIMIT 00:00 ~ 15:40" 에러가 남(실측 확인). 그 시간 전에는 어제까지만 요청한다.
DATA_READY_HOUR, DATA_READY_MINUTE = 15, 40


def _latest_queryable_date():
    now_kst = datetime.now(KST)
    cutoff = now_kst.replace(hour=DATA_READY_HOUR, minute=DATA_READY_MINUTE, second=0, microsecond=0)
    return now_kst.date() if now_kst >= cutoff else now_kst.date() - timedelta(days=1)


class Command(BaseCommand):
    help = (
        'KIS 종목별 투자자매매동향(일별) API로 종목×날짜별 투자자 주체별(외국인/개인/기관계/'
        '기금/투자신탁/사모펀드/증권) 순매수 수량·금액을 StockInvestorFlow에 적재합니다. '
        'run_stock_prediction의 수급 피처용 데이터 수집 커맨드입니다. '
        '기본적으로 is_major_index=True 종목만 대상으로, 최근 30영업일(API 1회 호출분)만 '
        '갱신합니다(매일 도는 증분 수집용). --backfill-days N을 주면 종목별로 N일 전까지 '
        '반복 호출해 과거 데이터를 채웁니다(최초 1회성 백필용 — 호출 수가 많아 시간이 오래 걸림).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true',
                             help='is_major_index 여부와 무관하게 is_active=True 전체 종목을 대상으로 합니다.')
        parser.add_argument('--backfill-days', type=int, default=None,
                             help='지정하면 종목별로 오늘부터 N일 전까지 반복 호출해 백필합니다. '
                                  '생략하면 종목당 1회 호출(최근 30영업일)만 수행합니다.')
        parser.add_argument('--sleep', type=float, default=0.3,
                             help='호출 간 대기 시간(초, KIS 호출 제한 방지). 기본 0.3')
        parser.add_argument('--tickers', type=str, default=None,
                             help='쉼표로 구분한 특정 종목코드만 대상으로 합니다(테스트/재시도용).')

    def handle(self, *args, **options):
        collect_all = options['all']
        backfill_days = options['backfill_days']
        sleep_sec = options['sleep']
        tickers_arg = options['tickers']

        stocks = StockItem.objects.filter(is_active=True)
        if not collect_all:
            stocks = stocks.filter(is_major_index=True)
        if tickers_arg:
            wanted = [t.strip() for t in tickers_arg.split(',') if t.strip()]
            stocks = stocks.filter(ticker__in=wanted)
        stocks = list(stocks)

        total = len(stocks)
        if total == 0:
            self.stdout.write(self.style.WARNING("[-] 대상 종목이 없습니다."))
            return

        target_start_date = date.today() - timedelta(days=backfill_days) if backfill_days else None
        mode_desc = f"백필(최근 {backfill_days}일)" if backfill_days else "증분(최근 30영업일)"
        self.stdout.write(self.style.SUCCESS(f"🚀 {total}개 종목 투자자 수급 수집 시작 ({mode_desc})"))

        success_count = 0
        row_count = 0
        for i, stock in enumerate(stocks, start=1):
            try:
                saved = self._collect_one(stock, target_start_date, sleep_sec)
                row_count += saved
                success_count += 1
                self.stdout.write(f"   [{i}/{total}] {stock.name}({stock.ticker}) {saved}건 저장")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"   [{i}/{total}] [{stock.ticker}] {stock.name} 실패: {e}"))

            if i < total:
                time.sleep(sleep_sec)

        self.stdout.write(self.style.SUCCESS(
            f"🎉 투자자 수급 수집 완료: {success_count}/{total}개 종목, 총 {row_count}건 저장/갱신"
        ))

    def _collect_one(self, stock, target_start_date, sleep_sec):
        """target_start_date가 None이면 1회 호출(최근 30영업일)만, 지정돼 있으면 그 날짜
        이전까지 반복 호출한다. 각 호출이 돌려준 가장 이른 날짜를 다음 호출의 기준일로 삼아
        (30영업일씩 뒤로) 겹침 없이 과거로 이동한다."""
        end_date = _latest_queryable_date()
        saved = 0
        seen_earliest = None

        while True:
            rows = get_investor_trade_by_stock(stock.ticker, end_date.strftime('%Y%m%d'))
            if not rows:
                break

            for row in rows:
                row_date = datetime.strptime(row['date'], '%Y%m%d').date()
                StockInvestorFlow.objects.update_or_create(
                    stock=stock, date=row_date,
                    defaults={k: v for k, v in row.items() if k != 'date'},
                )
                saved += 1

            earliest = datetime.strptime(rows[0]['date'], '%Y%m%d').date()
            if target_start_date is None or earliest <= target_start_date:
                break
            if seen_earliest is not None and earliest >= seen_earliest:
                # 다음 호출인데도 더 과거로 못 갔다(상장일 도달 등) — 무한루프 방지
                break
            seen_earliest = earliest

            end_date = earliest - timedelta(days=1)
            time.sleep(sleep_sec)

        return saved
