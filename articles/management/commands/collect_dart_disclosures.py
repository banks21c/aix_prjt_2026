import time
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand

from articles.dart_client import search_disclosures, MAX_RANGE_DAYS
from articles.models import StockDisclosure, StockItem

ALL_PBLNTF_TYPES = [code for code, _ in StockDisclosure.PBLNTF_TYPE_CHOICES]


def _chunk_date_range(start: date, end: date, max_days: int):
    """(start, end)를 max_days 이하 구간들로 쪼갠다. DART list.json이 corp_code 없는 조회는
    검색기간을 3개월로 제한하기 때문(백필 시 한 번에 넘기면 API가 거부한다)."""
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=max_days - 1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


class Command(BaseCommand):
    help = (
        'DART(전자공시시스템) Open API 공시검색으로 코스피/코스닥 상장사 공시 메타데이터를 '
        'StockDisclosure에 수집합니다. corp_code 매핑 없이 날짜+공시유형만으로 시장 전체를 '
        '조회하고, 응답의 stock_code로 StockItem에 매칭합니다. 인자 없이 실행하면 오늘 하루치 '
        '주요사항보고(B)만 수집합니다(매일 도는 증분 수집용).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, default=None,
                             help='단일 날짜(YYYYMMDD). 생략 시 오늘(KST).')
        parser.add_argument('--start', type=str, default=None, help='시작일(YYYYMMDD). --end와 함께 사용.')
        parser.add_argument('--end', type=str, default=None, help='종료일(YYYYMMDD). --start와 함께 사용.')
        parser.add_argument('--days-back', type=int, default=None,
                             help='오늘부터 N일 전까지 백필(--start/--end 대신 사용 가능).')
        parser.add_argument('--types', type=str, default='B',
                             help='쉼표구분 공시유형 코드(A~J). 기본 B(주요사항보고) — 유상증자/무상증자/'
                                  '자기주식취득·처분/합병결정 등 주가에 직접 영향 큰 이벤트가 여기 속함. '
                                  '전체 조회하려면 --types=all')
        parser.add_argument('--sleep', type=float, default=0.3, help='호출 간 대기 시간(초). 기본 0.3')

    def handle(self, *args, **options):
        start, end = self._resolve_range(options)
        # list.json 응답에는 pblntf_ty가 그대로 안 들어있어(요청 필터값일 뿐 echo 안 됨), 여러
        # 유형을 한 번에 조회할 방법이 없다 — --types=all이어도 코드별로 따로 호출해야
        # StockDisclosure.pblntf_ty를 정확히 채울 수 있다.
        types = ALL_PBLNTF_TYPES if options['types'] == 'all' else [
            t.strip() for t in options['types'].split(',') if t.strip()
        ]
        sleep_sec = options['sleep']

        active_tickers = set(StockItem.objects.filter(is_active=True).values_list('ticker', flat=True))
        self.stdout.write(self.style.SUCCESS(
            f"🚀 DART 공시 수집 시작: {start}~{end} / 유형 {types or '전체'}"
        ))

        total_saved, total_matched, total_seen = 0, 0, 0
        for chunk_start, chunk_end in _chunk_date_range(start, end, MAX_RANGE_DAYS):
            bgn_de = chunk_start.strftime('%Y%m%d')
            end_de = chunk_end.strftime('%Y%m%d')
            for pblntf_ty in types:
                saved, matched, seen = self._collect_one(bgn_de, end_de, pblntf_ty, active_tickers, sleep_sec)
                total_saved += saved
                total_matched += matched
                total_seen += seen

        self.stdout.write(self.style.SUCCESS(
            f"🎉 DART 공시 수집 완료: 조회 {total_seen}건 / 저장(신규·갱신) {total_saved}건 "
            f"/ 종목 매칭 {total_matched}건"
        ))

    def _resolve_range(self, options):
        if options['start'] and options['end']:
            return (
                datetime.strptime(options['start'], '%Y%m%d').date(),
                datetime.strptime(options['end'], '%Y%m%d').date(),
            )
        if options['days_back']:
            today = date.today()
            return today - timedelta(days=options['days_back']), today
        if options['date']:
            d = datetime.strptime(options['date'], '%Y%m%d').date()
            return d, d
        today = date.today()
        return today, today

    def _collect_one(self, bgn_de, end_de, pblntf_ty, active_tickers, sleep_sec):
        saved, matched, seen = 0, 0, 0
        page_no = 1
        while True:
            rows, total_page = search_disclosures(bgn_de, end_de, pblntf_ty=pblntf_ty, page_no=page_no)
            if not rows:
                break
            seen += len(rows)

            for row in rows:
                stock_code = (row.get('stock_code') or '').strip()
                if not stock_code:
                    continue  # 미상장/기타법인 공시는 종목 서비스 대상이 아니므로 건너뜀

                stock = None
                if stock_code in active_tickers:
                    stock = StockItem.objects.filter(ticker=stock_code, is_active=True).first()
                    matched += 1

                rcept_dt = datetime.strptime(row['rcept_dt'], '%Y%m%d').date()
                StockDisclosure.objects.update_or_create(
                    rcept_no=row['rcept_no'],
                    defaults={
                        'stock': stock,
                        'corp_name': row.get('corp_name', ''),
                        'stock_code': stock_code,
                        'corp_cls': row.get('corp_cls', ''),
                        'report_nm': row.get('report_nm', ''),
                        'rcept_dt': rcept_dt,
                        'pblntf_ty': pblntf_ty,
                        'flr_nm': row.get('flr_nm', ''),
                        'rm': row.get('rm', ''),
                    },
                )
                saved += 1

            if page_no >= total_page:
                break
            page_no += 1
            time.sleep(sleep_sec)

        return saved, matched, seen
