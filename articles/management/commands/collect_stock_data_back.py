import yfinance as yf
from django.core.management.base import BaseCommand
from articles.models import StockItem, StockDailyPrice

class Command(BaseCommand):
    help = 'DB의 StockItem 테이블을 읽어서 활성화된 모든 종목의 10년 치 일봉 데이터를 초고속 벌크 적재합니다.'

    def handle(self, *args, **options):
        # [구조 혁신] 소스코드 하드코딩 배제, 오직 DB 테이블을 읽어와서 타겟팅 대상을 선정합니다.
        db_stocks = StockItem.objects.filter(is_active=True)
        total_count = db_stocks.count()
        
        if total_count == 0:
            self.stdout.write(self.style.WARNING("[-] 현재 DB(StockItem) 테이블에 활성화된 종목이 없습니다."))
            return

        self.stdout.write(self.style.SUCCESS(f"🚀 총 {total_count}개 종목에 대한 10년 시계열 벌크 수집을 개시합니다."))

        for idx, stock in enumerate(db_stocks, 1):
            # 시장 타입에 맞춰 야후 파이낸스용 접미사 자동 분기 매핑 (.KS / .KQ)
            suffix = ".KS" if stock.market_type == "KOSPI" else ".KQ"
            yf_ticker_str = f"{stock.ticker}{suffix}"
            
            self.stdout.write(f"[{idx}/{total_count}] {stock.name}({stock.ticker}) 10년 데이터 패치 개시...")
            
            try:
                ticker_data = yf.Ticker(yf_ticker_str)
                df = ticker_data.history(period="10y", interval="1d")
                
                if df.empty:
                    continue

                # [속도 혁명 핵심] 매 행마다 DB를 누르지 않고 메모리에 리스트로 모아 한 방에 저장합니다.
                bulk_list = []
                
                # 이미 이 종목에 대해 저장된 날짜셋을 가져와 중복 삽입을 완벽 무력화 차단합니다.
                existing_dates = set(
                    StockDailyPrice.objects.filter(stock=stock).values_list('date', flat=True)
                )

                for index, row in df.iterrows():
                    record_date = index.date()
                    if row['Volume'] == 0 or record_date in existing_dates:
                        continue

                    # 객체만 생성하여 리스트에 축적
                    bulk_list.append(
                        StockDailyPrice(
                            stock=stock,
                            date=record_date,
                            open_price=round(row['Open'], 2),
                            high_price=round(row['High'], 2),
                            low_price=round(row['Low'], 2),
                            close_price=round(row['Close'], 2),
                            volume=int(row['Volume']),
                            trading_amount=int(row['Volume'] * row['Close'])
                        )
                    )

                # 단 한 번의 커밋으로 2,400일 치 일봉 밀어 넣기
                if bulk_list:
                    StockDailyPrice.objects.bulk_create(bulk_list, batch_size=500)
                    self.stdout.write(self.style.SUCCESS(f"    ↳ 성공: 신규 일봉 {len(bulk_list)}개 초고속 벌크 적재 완료"))
                else:
                    self.stdout.write(f"    ↳ 동기화 상태 완벽 (추가 데이터 없음)")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"    ↳ {stock.name} 통신 오류 패스: {str(e)}"))
                continue

        self.stdout.write(self.style.SUCCESS('🎉 데이터베이스 연동형 초고속 대량 데이터 축적이 완벽히 완수되었습니다!'))

