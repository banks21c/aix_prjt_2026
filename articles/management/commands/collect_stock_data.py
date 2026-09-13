import random
import time
from datetime import datetime, timedelta

import yfinance as yf
from django.core.management.base import BaseCommand
from django.db.models import Max

from articles.kis_client import KST, get_stock_daily_price
from articles.models import StockItem, StockDailyPrice

# 시장 구분 -> 야후 파이낸스 티커 접미사 (신규 종목 최초 전체 수집에만 사용)
SUFFIX_MAP = {'KOSPI': '.KS', 'KOSDAQ': '.KQ'}

# 429(Too Many Requests) 등 야후의 차단/속도제한을 나타내는 신호 키워드
RATE_LIMIT_HINTS = ('429', 'rate limit', 'too many requests', 'rate-limited')

# KIS 국내주식기간별시세(inquire-daily-itemchartprice)는 한 번에 최대 100영업일까지만 내려주므로,
# 매일 도는 증분 수집(며칠~몇 주 공백)은 문제 없지만 이 이상 벌어진 공백은 여러 번 나눠 받아야 한다.
KIS_MAX_DAYS_PER_CALL = 90


class Command(BaseCommand):
    help = (
        '기본적으로 is_major_index=True(코스피200/코스닥150) 종목만 대상으로 일봉 데이터를 '
        '적재합니다. --all 지정 시 is_active 전체 종목 대상. 종목마다 이미 저장된 최신 날짜가 '
        '있으면 그 이후분만 한국투자증권(KIS) API로 증분 수집하고(코스닥 소형주 등 야후 파이낸스 '
        '데이터가 며칠씩 지연되는 종목이 많아 KIS를 씀), 아직 데이터가 전혀 없는 신규 종목은 '
        '야후 파이낸스로 --period만큼 전체를 수집합니다. --full을 주면 기존 데이터 유무와 무관하게 '
        '전 종목을 야후 파이낸스로 --period만큼 통째로 다시 받아옵니다.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--sleep-min', type=float, default=1.5,
                             help='(야후 파이낸스 경로) 종목 간 최소 대기 시간(초). 기본 1.5')
        parser.add_argument('--sleep-max', type=float, default=3.0,
                             help='(야후 파이낸스 경로) 종목 간 최대 대기 시간(초). 기본 3.0')
        parser.add_argument('--kis-sleep', type=float, default=0.3,
                             help='(KIS 증분 경로) 종목 간 대기 시간(초, KIS 호출 제한 방지). 기본 0.3')
        parser.add_argument('--max-retries', type=int, default=3,
                             help='종목 1개당 최대 재시도 횟수. 기본 3')
        parser.add_argument('--max-consecutive-failures', type=int, default=5,
                             help='연속 실패 허용 횟수. 초과 시 차단으로 간주하고 작업 중단. 기본 5')
        parser.add_argument('--period', type=str, default='10y',
                             help='신규 종목(또는 --full) 야후 파이낸스 전체 수집 기간. 기본 10y')
        parser.add_argument('--all', action='store_true',
                             help='is_major_index 여부와 무관하게 is_active=True 전체 종목을 대상으로 수집합니다.')
        parser.add_argument('--full', action='store_true',
                             help='이미 데이터가 있는 종목도 KIS 증분 수집하지 않고 야후 파이낸스로 --period만큼 '
                                  '전체를 다시 받아옵니다 (데이터 정합성 재점검 등 예외적인 경우에만 사용).')

    def handle(self, *args, **options):
        sleep_min = options['sleep_min']
        sleep_max = options['sleep_max']
        kis_sleep = options['kis_sleep']
        max_retries = options['max_retries']
        max_consecutive_failures = options['max_consecutive_failures']
        period = options['period']
        collect_all = options['all']
        force_full = options['full']

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
        mode_label = f"전체(야후 {period}) 재수집" if force_full else "증분(KIS) 수집"
        self.stdout.write(self.style.SUCCESS(
            f"🚀 {scope_label} 총 {total_count}개 종목 {mode_label}을 시작합니다."
        ))

        consecutive_failures = 0
        success_count = 0
        skipped_count = 0
        failed_count = 0

        try:
            for idx, stock in enumerate(db_stocks, 1):
                latest_date = None
                if not force_full:
                    latest_date = (
                        StockDailyPrice.objects.filter(stock=stock).aggregate(Max('date'))['date__max']
                    )

                use_kis = latest_date is not None
                fetch_desc = f"{latest_date} 이후 증분(KIS)" if use_kis else f"전체(야후 {period})"
                self.stdout.write(f"[{idx}/{total_count}] {stock.name}({stock.ticker}) 데이터 조회 중... ({fetch_desc})")

                try:
                    if use_kis:
                        rows = self._fetch_incremental_kis(stock.ticker, latest_date, max_retries)
                    else:
                        suffix = SUFFIX_MAP.get(stock.market_type)
                        if suffix is None:
                            self.stdout.write(self.style.WARNING(
                                f"    ↳ 알 수 없는 market_type({stock.market_type}) - 건너뜁니다."
                            ))
                            skipped_count += 1
                            continue
                        rows = self._fetch_full_yfinance(f"{stock.ticker}{suffix}", period, max_retries)
                except Exception as e:
                    consecutive_failures += 1
                    failed_count += 1
                    self.stdout.write(self.style.ERROR(f"    ↳ 최종 실패(재시도 소진): {e}"))

                    if consecutive_failures >= max_consecutive_failures:
                        source = "KIS" if use_kis else "야후 파이낸스"
                        self.stdout.write(self.style.ERROR(
                            f"🛑 연속 {consecutive_failures}회 실패했습니다. {source} 측에서 "
                            f"IP/앱키를 차단·제한했을 가능성이 높아 안전하게 작업을 중단합니다. "
                            f"시간을 두고(예: 30분~수 시간 후) 다시 실행해주세요. "
                            f"(현재까지 성공 {success_count}건, 건너뜀 {skipped_count}건, 실패 {failed_count}건)"
                        ))
                        return

                    self._sleep(kis_sleep if use_kis else None, sleep_min, sleep_max)
                    continue

                # 성공했으므로 연속 실패 카운트 초기화
                consecutive_failures = 0

                if not rows:
                    if use_kis:
                        self.stdout.write("    ↳ 신규 거래일 없음 (이미 최신)")
                    else:
                        self.stdout.write("    ↳ 수신 데이터 없음(상장폐지/신규상장 등) - 건너뜀")
                    skipped_count += 1
                    self._sleep(kis_sleep if use_kis else None, sleep_min, sleep_max)
                    continue

                created = self._save_history(stock, rows, min_date=latest_date)
                if created > 0:
                    self.stdout.write(self.style.SUCCESS(f"    ↳ 성공: 신규 일봉 {created}개 적재 완료"))
                else:
                    self.stdout.write("    ↳ 동기화 상태 완료 (추가 데이터 없음)")
                success_count += 1

                self._sleep(kis_sleep if use_kis else None, sleep_min, sleep_max)

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

    def _fetch_incremental_kis(self, ticker, since_date, max_retries):
        """KIS 국내주식기간별시세로 since_date 다음날부터 오늘(KST)까지 일봉을 받아온다.

        한 번에 최대 100영업일까지만 내려주는 API라, 공백이 KIS_MAX_DAYS_PER_CALL(달력일 기준
        여유있게 90일)을 넘으면 여러 번 나눠 호출해 이어붙인다 — 매일 도는 증분 수집에서는
        거의 항상 1회 호출로 끝나고, 한동안 못 돌렸다가 재개하는 경우에만 여러 번 호출된다.
        반환값은 [{'date': date, 'open':, 'high':, 'low':, 'close':, 'volume':}, ...] 형태.
        """
        today = datetime.now(KST).date()
        start = since_date + timedelta(days=1)
        if start > today:
            return []

        all_rows = []
        chunk_start = start
        while chunk_start <= today:
            chunk_end = min(chunk_start + timedelta(days=KIS_MAX_DAYS_PER_CALL - 1), today)
            all_rows.extend(self._fetch_kis_chunk(ticker, chunk_start, chunk_end, max_retries))
            chunk_start = chunk_end + timedelta(days=1)

        normalized = []
        for row in all_rows:
            normalized.append({
                'date': datetime.strptime(row['date'], '%Y%m%d').date(),
                'open': row['open'], 'high': row['high'], 'low': row['low'],
                'close': row['close'], 'volume': row['volume'],
            })
        return normalized

    def _fetch_kis_chunk(self, ticker, start_date, end_date, max_retries):
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                return get_stock_daily_price(ticker, start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d'))
            except Exception as e:
                last_exc = e
                if attempt >= max_retries:
                    break
                wait = 2.0 * (2 ** (attempt - 1)) + random.uniform(0, 1.0)
                self.stdout.write(self.style.WARNING(
                    f"    ↳ KIS 조회 오류({attempt}/{max_retries}), {wait:.1f}초 대기 후 재시도: {e}"
                ))
                time.sleep(wait)
        raise last_exc

    def _fetch_full_yfinance(self, yf_ticker_str, period, max_retries):
        """신규 종목(DB에 데이터가 전혀 없는 경우) 또는 --full일 때만 쓰는 야후 파이낸스 전체 수집.
        반환값은 _fetch_incremental_kis와 동일한 정규화된 dict 리스트."""
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                ticker_data = yf.Ticker(yf_ticker_str)
                # auto_adjust=False: 배당/액면분할로 과거 종가가 조정되지 않도록 원본 값 유지
                df = ticker_data.history(period=period, interval="1d", auto_adjust=False)
                df = df.dropna(subset=['Open', 'High', 'Low', 'Close', 'Volume'])
                return [
                    {
                        'date': index.date(),
                        'open': float(row['Open']), 'high': float(row['High']),
                        'low': float(row['Low']), 'close': float(row['Close']),
                        'volume': float(row['Volume']),
                    }
                    for index, row in df.iterrows()
                ]
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

    def _save_history(self, stock, rows, min_date=None):
        """정규화된 OHLCV dict 리스트를 검증 후 StockDailyPrice로 벌크 적재합니다.

        min_date(증분 수집 시 이미 저장된 최신 날짜)가 있으면 dedup 조회를 그 이후 구간으로만
        좁혀, 종목마다 몇 년치 date 집합을 통째로 다시 긁어오지 않게 한다.
        """
        date_filter = {'stock': stock}
        if min_date is not None:
            date_filter['date__gte'] = min_date
        existing_dates = set(
            StockDailyPrice.objects.filter(**date_filter).values_list('date', flat=True)
        )

        bulk_list = []
        for row in rows:
            record_date = row['date']
            if row['volume'] <= 0 or record_date in existing_dates:
                continue

            try:
                bulk_list.append(
                    StockDailyPrice(
                        stock=stock,
                        date=record_date,
                        open_price=round(float(row['open']), 2),
                        high_price=round(float(row['high']), 2),
                        low_price=round(float(row['low']), 2),
                        close_price=round(float(row['close']), 2),
                        volume=int(row['volume']),
                        trading_amount=int(row['volume'] * row['close']),
                    )
                )
            except (TypeError, ValueError):
                # 개별 행 파싱 실패는 그 행만 건너뛰고 나머지는 계속 진행
                continue

        if not bulk_list:
            return 0

        # unique_together=(stock, date) 위반분은 조용히 무시 (existing_dates와 이중 방어)
        StockDailyPrice.objects.bulk_create(bulk_list, batch_size=500, ignore_conflicts=True)
        return len(bulk_list)

    @staticmethod
    def _sleep(kis_sleep, sleep_min, sleep_max):
        if kis_sleep is not None:
            time.sleep(kis_sleep)
        else:
            time.sleep(random.uniform(sleep_min, sleep_max))
