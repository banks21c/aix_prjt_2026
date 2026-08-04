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
            # prdy_vrss(전일 대비)는 실제로는 이미 부호가 포함된 문자열로 온다(하락 종목은
            # "-10900" 식). prdy_vrss_sign(4=하한/5=하락)을 보고 또 부호를 뒤집으면 이미 음수인
            # 값이 다시 양수로 바뀌는 이중 반전 버그가 생긴다(실제로 발생해 전일대비가 +로
            # 잘못 표시됨) — float() 파싱 결과를 그대로 쓴다.
            amount = float(row['prdy_vrss'])
            RankedMover.objects.update_or_create(
                rank_type=rank_type,
                rank=rank,
                defaults=dict(
                    ticker=row['stck_shrn_iscd'],
                    name=row['hts_kor_isnm'],
                    price=float(row['stck_prpr']),
                    change_pct=float(row['prdy_ctrt']),
                    change_amount=round(amount, 2),
                ),
            )

        self.stdout.write(self.style.SUCCESS(f"    ↳ {label}: {len(rows)}건 저장 완료"))
