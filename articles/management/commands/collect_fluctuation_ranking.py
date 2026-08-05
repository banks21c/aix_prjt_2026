import FinanceDataReader as fdr
from django.core.management.base import BaseCommand

from articles.kis_client import get_fluctuation_ranking, is_market_open, SORT_GAINERS, SORT_LOSERS
from articles.models import RankedMover

# 대시보드 "⭐ 특징종목"에서 요청대로 제외할 대상 개수 — 필터링 후에도 5개를 채우려면 KIS에
# 원래보다 더 많이(최대 30건) 요청해야 한다.
FETCH_COUNT = 30
KEEP_COUNT = 5


def _get_etf_tickers():
    """FinanceDataReader의 'ETF/KR' 목록으로 국내 ETF 종목코드 집합을 가져온다. ETN은 KIS
    등락률 순위 응답에 별도 구분 필드가 없어 이 방법을 못 쓰지만, ETN은 KRX 작명 규칙상
    이름에 항상 "ETN"이 들어가므로(예: "삼성 블룸버그 레버리지 WTI원유선물 ETN B") 이름
    검사만으로 충분히 걸러진다."""
    try:
        df = fdr.StockListing('ETF/KR')
        return set(df['Symbol'].astype(str).str.zfill(6))
    except Exception:
        return set()


class Command(BaseCommand):
    help = (
        '한국투자증권(KIS) 등락률 순위 API로 상승률/하락률 상위 5종목을 조회하여 RankedMover에 '
        '저장합니다 (대시보드 특징종목용). ETF/ETN은 순위에서 제외하고 일반 상장 종목만 채운다.'
    )

    def handle(self, *args, **options):
        if not is_market_open():
            self.stdout.write(self.style.WARNING("[-] 오늘은 휴장일입니다. 등락률 순위 수집을 건너뜁니다."))
            return

        etf_tickers = _get_etf_tickers()
        if not etf_tickers:
            self.stdout.write(self.style.WARNING("    ↳ ETF 목록을 못 가져와 ETN 이름 필터만 적용됩니다."))

        self._collect('GAINER', SORT_GAINERS, etf_tickers)
        self._collect('LOSER', SORT_LOSERS, etf_tickers)
        self.stdout.write(self.style.SUCCESS("🎉 등락률 순위 수집이 완료되었습니다."))

    def _collect(self, rank_type, sort_cls_code, etf_tickers):
        label = 'GAINER' if rank_type == 'GAINER' else 'LOSER'
        self.stdout.write(f"[-] {label} 순위 조회 중...")

        try:
            rows = get_fluctuation_ranking(sort_cls_code, count=FETCH_COUNT)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"    ↳ 조회 실패: {e}"))
            return

        excluded = 0
        kept = []
        for row in rows:
            ticker = row['stck_shrn_iscd']
            name = row['hts_kor_isnm']
            if ticker in etf_tickers or 'ETN' in name:
                excluded += 1
                continue
            kept.append(row)
            if len(kept) >= KEEP_COUNT:
                break

        # 필터링 후 순서대로 다시 순위를 매긴다 — KIS 원본 data_rank는 제외된 자리만큼
        # 구멍이 생겨서(1,3,4,7...) 그대로 쓰면 RankedMover.rank가 1~5로 안 채워진다.
        for rank, row in enumerate(kept, start=1):
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

        # 필터링 후 5개를 못 채운 날(이론상 가능)에는 이전 실행분이 그 아래 순위에 그대로
        # 남아있지 않도록 정리한다.
        RankedMover.objects.filter(rank_type=rank_type, rank__gt=len(kept)).delete()

        self.stdout.write(self.style.SUCCESS(
            f"    ↳ {label}: {len(kept)}건 저장 완료 (ETF/ETN {excluded}건 제외)"
        ))
