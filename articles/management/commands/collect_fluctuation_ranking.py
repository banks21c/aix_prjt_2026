from django.core.management.base import BaseCommand

from articles.kis_client import get_fluctuation_ranking, is_market_open, SORT_GAINERS, SORT_LOSERS
from articles.models import RankedMover


class Command(BaseCommand):
    help = '한국투자증권(KIS) 등락률 순위 API로 상승률/하락률 상위 5종목을 조회하여 RankedMover에 저장합니다 (대시보드 특징종목용).'

    def handle(self, *args, **options):
        if not is_market_open():
            self.stdout.write(self.style.WARNING("[-] 오늘은 휴장일입니다. 등락률 순위 수집을 건너뜁니다."))
            return

        self._collect('GAINER', SORT_GAINERS)
        self._collect('LOSER', SORT_LOSERS)
        self.stdout.write(self.style.SUCCESS("🎉 등락률 순위 수집이 완료되었습니다."))

    def _collect(self, rank_type, sort_cls_code):
        label = 'GAINER' if rank_type == 'GAINER' else 'LOSER'
        self.stdout.write(f"[-] {label} 순위 조회 중...")

        try:
            rows = get_fluctuation_ranking(sort_cls_code, count=5)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"    ↳ 조회 실패: {e}"))
            return

        for row in rows:
            rank = int(row['data_rank'])
            RankedMover.objects.update_or_create(
                rank_type=rank_type,
                rank=rank,
                defaults=dict(
                    ticker=row['stck_shrn_iscd'],
                    name=row['hts_kor_isnm'],
                    price=float(row['stck_prpr']),
                    change_pct=float(row['prdy_ctrt']),
                ),
            )

        self.stdout.write(self.style.SUCCESS(f"    ↳ {label}: {len(rows)}건 저장 완료"))
