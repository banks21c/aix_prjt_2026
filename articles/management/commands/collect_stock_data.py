import random
import time

import yfinance as yf
from django.core.management.base import BaseCommand
from articles.models import StockItem, StockPrediction

# 시장 구분 -> 야후 파이낸스 티커 접미사
SUFFIX_MAP = {'KOSPI': '.KS', 'KOSDAQ': '.KQ'}

# 429(Too Many Requests) 등 야후의 차단/속도제한을 나타내는 신호 키워드
RATE_LIMIT_HINTS = ('429', 'rate limit', 'too many requests', 'rate-limited')


class Command(BaseCommand):
    help = (
        '기본적으로 is_major_index=True(코스피200/코스닥150) 종목만 대상으로 10년치 일봉 데이터를 '
        '야후 파이낸스 차단을 피하며 안전하게 벌크 적재합니다. --all 지정 시 is_active 전체 종목 대상.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--sleep-min', type=float, default=1.5,
                             help='종목 간 최소 대기 시간(초). 기본 1.5')
        parser.add_argument('--sleep-max', type=float, default=3.0,
                             help='종목 간 최대 대기 시간(초). 기본 3.0')
        parser.add_argument('--max-retries', type=int, default=3,
                             help='종목 1개당 최대 재시도 횟수. 기본 3')
        parser.add_argument('--max-consecutive-failures', type=int, default=5,
                             help='연속 실패 허용 횟수. 초과 시 차단으로 간주하고 작업 중단. 기본 5')
        parser.add_argument('--period', type=str, default='10y',
                             help='수집 기간(yfinance period 문법). 기본 10y')
        parser.add_argument('--all', action='store_true',
                             help='is_major_index 여부와 무관하게 is_active=True 전체 종목을 대상으로 수집합니다.')

    def handle(self, *args, **options):
        sleep_min = options['sleep_min']
        sleep_max = options['sleep_max']
        max_retries = options['max_retries']
        max_consecutive_failures = options['max_consecutive_failures']
        period = options['period']
        collect_all = options['all']

        if sleep_min < 0 or sleep_max < sleep_min:
            self.stdout.write(self.style.ERROR("--sleep-min/--sleep-max 값이 올바르지 않습니다."))
            return

        # 기본은 is_major_index=True(코스피200/코스닥150)만 대상으로 좁혀서 요청량을 줄이고,
        # --all 지정 시에만 is_active 전체 종목을 대상으로 합니다.
        stock_filter = {'is_active': True}
        if not collect_all:
            stock_filter['is_major_index'] = True
        db_stocks = list(StockItem.objects.filter(**stock_filter))
        total_count = len(db_stocks)

        if total_count == 0:
            self.stdout.write(self.style.WARNING(
                "[-] 대상 종목이 없습니다. "
                "sync_index_membership 명령을 먼저 실행했는지 확인하세요."
            ))
            return

        scope_label = "전체 활성" if collect_all else "코스피200/코스닥150"
        self.stdout.write(self.style.SUCCESS(
            f"🚀 {scope_label} 총 {total_count}개 종목 10년 시계열 수집을 시작합니다. "
            f"(요청 간 {sleep_min}~{sleep_max}초 대기)"
        ))

        consecutive_failures = 0
        success_count = 0
        skipped_count = 0
        failed_count = 0

        try:
            for idx, stock in enumerate(db_stocks, 1):
                suffix = SUFFIX_MAP.get(stock.market_type)
                if suffix is None:
                    self.stdout.write(self.style.WARNING(
                        f"[{idx}/{total_count}] {stock.name}: 알 수 없는 market_type"
                        f"({stock.market_type}) - 건너뜁니다."
                    ))
                    skipped_count += 1
                    continue

                yf_ticker_str = f"{stock.ticker}{suffix}"
                self.stdout.write(f"[{idx}/{total_count}] {stock.name}({stock.ticker}) 데이터 조회 중...")

                try:
                    df = self._fetch_history(yf_ticker_str, period, max_retries)
                except Exception as e:
                    consecutive_failures += 1
                    failed_count += 1
                    self.stdout.write(self.style.ERROR(
                        f"    ↳ 최종 실패(재시도 소진): {e}"
                    ))

                    if consecutive_failures >= max_consecutive_failures:
                        self.stdout.write(self.style.ERROR(
                            f"🛑 연속 {consecutive_failures}회 실패했습니다. 야후 파이낸스 측에서 "
                            f"IP를 차단/제한했을 가능성이 높아 안전하게 작업을 중단합니다. "
                            f"시간을 두고(예: 30분~수 시간 후) 다시 실행해주세요. "
                            f"(현재까지 성공 {success_count}건, 건너뜀 {skipped_count}건, 실패 {failed_count}건)"
                        ))
                        return

                    self._sleep(sleep_min, sleep_max)
                    continue

                # 성공했으므로 연속 실패 카운트 초기화
                consecutive_failures = 0

                if df is None or df.empty:
                    self.stdout.write(f"    ↳ 수신 데이터 없음(상장폐지/신규상장 등) - 건너뜀")
                    skipped_count += 1
                    self._sleep(sleep_min, sleep_max)
                    continue

                created = self._save_history(stock, df)
                if created > 0:
                    self.stdout.write(self.style.SUCCESS(f"    ↳ 성공: 신규 일봉 {created}개 적재 완료"))
                else:
                    self.stdout.write("    ↳ 동기화 상태 완료 (추가 데이터 없음)")
                success_count += 1

                self._sleep(sleep_min, sleep_max)

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING(
                f"\n⏸️ 사용자가 작업을 중단했습니다. "
                f"(성공 {success_count}건, 건너뜀 {skipped_count}건, 실패 {failed_count}건)"
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"🎉 수집 완료. 성공 {success_count}건 / 건너뜀 {skipped_count}건 / 실패 {failed_count}건 "
            f"(전체 {total_count}종목)"
        ))

    def _fetch_history(self, yf_ticker_str, period, max_retries):
        """야후 파이낸스에서 일봉 데이터를 받아옵니다. 실패 시 지수 백오프로 재시도합니다."""
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                ticker_data = yf.Ticker(yf_ticker_str)
                # auto_adjust=False: 배당/액면분할로 과거 종가가 조정되지 않도록 원본 값 유지
                df = ticker_data.history(period=period, interval="1d", auto_adjust=False)
                return df
            except Exception as e:
                last_exc = e
                if attempt >= max_retries:
                    break

                is_rate_limited = any(hint in str(e).lower() for hint in RATE_LIMIT_HINTS)
                # 레이트리밋으로 의심되면 더 오래 대기 (지수 백오프 + 지터)
                base = 5.0 if is_rate_limited else 2.0
                wait = base * (2 ** (attempt - 1)) + random.uniform(0, 1.5)

                reason = "레이트리밋 의심" if is_rate_limited else "일시 오류"
                self.stdout.write(self.style.WARNING(
                    f"    ↳ {reason}({attempt}/{max_retries}), {wait:.1f}초 대기 후 재시도: {e}"
                ))
                time.sleep(wait)

        raise last_exc

    def _save_history(self, stock, df):
        """OHLCV 데이터프레임을 검증 후 StockPrediction으로 벌크 적재합니다."""
        # 결측치가 있는 행은 DB 제약(NOT NULL) 위반이나 잘못된 값 저장을 막기 위해 제외
        df = df.dropna(subset=['Open', 'High', 'Low', 'Close', 'Volume'])
        if df.empty:
            return 0

        existing_dates = set(
            StockPrediction.objects.filter(stock=stock).values_list('date', flat=True)
        )

        bulk_list = []
        for index, row in df.iterrows():
            record_date = index.date()
            if row['Volume'] <= 0 or record_date in existing_dates:
                continue

            try:
                bulk_list.append(
                    StockPrediction(
                        stock=stock,
                        date=record_date,
                        open_price=round(float(row['Open']), 2),
                        high_price=round(float(row['High']), 2),
                        low_price=round(float(row['Low']), 2),
                        close_price=round(float(row['Close']), 2),
                        volume=int(row['Volume']),
                        trading_amount=int(row['Volume'] * row['Close']),
                    )
                )
            except (TypeError, ValueError):
                # 개별 행 파싱 실패는 그 행만 건너뛰고 나머지는 계속 진행
                continue

        if not bulk_list:
            return 0

        # unique_together=(stock, date) 위반분은 조용히 무시 (existing_dates와 이중 방어)
        StockPrediction.objects.bulk_create(bulk_list, batch_size=500, ignore_conflicts=True)
        return len(bulk_list)

    @staticmethod
    def _sleep(sleep_min, sleep_max):
        time.sleep(random.uniform(sleep_min, sleep_max))
