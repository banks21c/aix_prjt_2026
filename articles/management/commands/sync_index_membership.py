import csv

from django.core.management.base import BaseCommand, CommandError
from articles.models import StockItem

# KRX(data.krx.co.kr)에서 직접 다운로드한 코스피200 / 코스닥150 구성종목 CSV를 읽어
# StockItem.is_major_index 플래그와 market_cap(상장시가총액)을 동기화합니다.
# KRX CSV는 상장시가총액 내림차순으로 정렬되어 있으므로, 대시보드의
# "코스피/코스닥 상위 N개" 노출은 이 market_cap 컬럼을 기준으로 정렬합니다.
#
# 사용법:
#   python manage.py sync_index_membership \
#       --kospi200 /path/to/data_1931_20260708.csv \
#       --kosdaq150 /path/to/data_2000_20260708.csv
#
# CSV는 KRX가 EUC-KR 인코딩으로 내려주며, 컬럼은 아래와 같습니다.
#   종목코드,종목명,종가,대비,등락률,상장시가총액


class Command(BaseCommand):
    help = 'KRX에서 다운받은 코스피200/코스닥150 구성종목 CSV로 StockItem.is_major_index/market_cap을 동기화합니다.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--kospi200', required=True,
            help='코스피200 구성종목 CSV 경로 (KRX 다운로드 파일)',
        )
        parser.add_argument(
            '--kosdaq150', required=True,
            help='코스닥150 구성종목 CSV 경로 (KRX 다운로드 파일)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🚀 코스피200 / 코스닥150 편입종목 동기화를 시작합니다.'))

        file_map = {
            'KOSPI': (options['kospi200'], 'KOSPI200'),
            'KOSDAQ': (options['kosdaq150'], 'KOSDAQ150'),
        }

        for market_type, (path, label) in file_map.items():
            try:
                tickers = self._read_tickers(path)
            except Exception as e:
                raise CommandError(f"{path} 읽기 실패: {e}")

            if not tickers:
                self.stdout.write(self.style.WARNING(f"⚠️ {label}: 파일에서 종목코드를 하나도 읽지 못했습니다 ({path})"))
                continue

            self._sync_market(market_type, label, tickers)

        total_members = StockItem.objects.filter(is_major_index=True).count()
        self.stdout.write(self.style.SUCCESS(f"🎉 동기화 완료. 현재 편입종목(is_major_index=True) 총 {total_members}개."))

    def _read_tickers(self, path):
        # 값: {종목코드: 상장시가총액(원, KRX CSV 원본 단위)}. KRX CSV는 이미
        # 상장시가총액 내림차순으로 정렬되어 있어, 이 순서를 그대로 "시가총액 순위"로 씁니다.
        tickers = {}
        with open(path, encoding='euc-kr') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get('종목코드') or '').strip().zfill(6)
                # 대부분 6자리 숫자지만, 스팩합병/신규상장 임시코드 등은 문자가
                # 섞이기도 하므로(예: 0126Z0) 숫자 여부가 아닌 존재 여부로만 판단합니다.
                if not code:
                    continue
                try:
                    market_cap = int(float(row.get('상장시가총액') or 0))
                except ValueError:
                    market_cap = None
                tickers[code] = market_cap
        return tickers

    def _sync_market(self, market_type, label, tickers):
        qs = StockItem.objects.filter(market_type=market_type)

        # 1) 이번 목록에 더 이상 없는 종목은 편입 해제 (지수 정기변경 반영)
        cleared = qs.filter(is_major_index=True).exclude(ticker__in=tickers.keys()).update(is_major_index=False)

        # 2) 이번 목록에 있는 종목은 편입 처리 + 시가총액 갱신
        matched_qs = qs.filter(ticker__in=tickers.keys())
        added = matched_qs.exclude(is_major_index=True).update(is_major_index=True)

        for item in matched_qs:
            market_cap = tickers.get(item.ticker)
            if market_cap is not None and item.market_cap != market_cap:
                item.market_cap = market_cap
                item.save(update_fields=['market_cap'])

        found_tickers = set(matched_qs.values_list('ticker', flat=True))
        missing = sorted(set(tickers.keys()) - found_tickers)

        self.stdout.write(self.style.SUCCESS(
            f"✅ {label}: 신규 편입 {added}건, 편입 해제 {cleared}건, "
            f"DB에 없어 건너뜀 {len(missing)}건 (파일 내 {len(tickers)}종목)"
        ))
        if missing:
            self.stdout.write(self.style.WARNING(
                f"    ↳ DB에 없는 종목코드: {', '.join(missing)} "
                f"(먼저 전체 종목 동기화 명령을 실행했는지 확인하세요)"
            ))
