import FinanceDataReader as fdr
from django.core.management.base import BaseCommand
from articles.models import StockItem

# 참고: kind.krx.co.kr 직접 크롤링은 KRX가 서버/클라우드 IP를 봇으로 판단해
# 빈 응답(0건)을 돌려주는 경우가 많아, 대신 FinanceDataReader 라이브러리를 사용합니다.
# 이 라이브러리는 종목 목록 데이터를 GitHub(raw.githubusercontent.com)에 미러링된
# 캐시에서 받아오기 때문에 KRX의 IP 차단/봇 탐지 영향을 받지 않습니다.
# 설치: pip install finance-datareader

MARKETS = ['KOSPI', 'KOSDAQ']


class Command(BaseCommand):
    help = 'FinanceDataReader를 통해 KOSPI/KOSDAQ 전 종목을 StockItem에 적재합니다.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🚀 KOSPI/KOSDAQ 전 종목 동기화를 시작합니다.'))

        for market_type in MARKETS:
            try:
                df = fdr.StockListing(market_type)
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f"💥 {market_type} 종목 목록 수신 실패: {e}"
                ))
                continue

            if df is None or df.empty:
                self.stdout.write(self.style.WARNING(
                    f"⚠️ {market_type}: 수신된 종목이 0건입니다. 응답 내용을 확인해 주세요."
                ))
                continue

            created, updated = self._save_rows(df, market_type)
            self.stdout.write(self.style.SUCCESS(
                f"✅ {market_type}: 신규 {created}건, 갱신 {updated}건 (총 {len(df)}건 수신)"
            ))

        total_in_db = StockItem.objects.filter(is_active=True).count()
        self.stdout.write(self.style.SUCCESS(
            f"🎉 동기화 완료. 현재 DB의 총 활성 종목 수: {total_in_db}개"
        ))

    def _save_rows(self, df, market_type):
        created_count = 0
        updated_count = 0
        for _, row in df.iterrows():
            ticker = str(row['Code']).strip().zfill(6)
            name = str(row['Name']).strip()
            if not ticker or not name:
                continue

            _, created = StockItem.objects.update_or_create(
                ticker=ticker,
                defaults={
                    'name': name,
                    'market_type': market_type,
                    'is_active': True,
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1
        return created_count, updated_count
