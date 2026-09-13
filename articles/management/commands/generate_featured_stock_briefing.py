import time
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from articles import article_ai, thumbnail
from articles.kis_client import get_stock_close_price
from articles.models import AnalyzedArticle, MarketHoliday, RankedMover

SESSION_LABELS = {'midday': '장중', 'close': '마감후'}


def _find_sign_mismatches(movers):
    """GAINER인데 등락률이 0% 이하, LOSER인데 0% 이상인 항목을 찾는다.

    2026-08-03 실제 장애(c9e732f/2c4891f)의 원인이 KIS 조회 파라미터 오류로 RankedMover 자체가
    '하락률 상위'에 상승 종목을 잘못 편입시킨 것이었다 — AI 요약이 아니라 원본 데이터가
    깨진 경우라, 발행 직전에 원본 데이터 수준에서 한 번 더 검증해야 재발을 잡을 수 있다.
    """
    return [
        m for m in movers
        if m['change_pct'] is not None and (
            (m['rank_type'] == 'GAINER' and m['change_pct'] <= 0)
            or (m['rank_type'] == 'LOSER' and m['change_pct'] >= 0)
        )
    ]


def _report_thesis_text(title, stock_name):
    """collect_kis_news가 저장한 '[리포트 브리핑]종목명, '한줄평' 목표가 N원 - 증권사' 형태
    제목에서 앞의 태그와 중복되는 종목명을 떼어내, movers_to_text가 종목명을 다시 붙일 때
    "삼성전자: 삼성전자, '...'" 식으로 겹치지 않게 한다."""
    text = title.removeprefix('[리포트 브리핑]').strip()
    prefix = f"{stock_name},"
    if text.startswith(prefix):
        text = text[len(prefix):].strip()
    return text


class Command(BaseCommand):
    help = (
        '기사 하나하나를 AI로 요약하는 대신, RankedMover(KIS 등락률 상위 종목)를 모아 하루 '
        '두 번(장중/마감후)만 AI 특징주 통합 브리핑 1건을 생성해 AnalyzedArticle로 저장합니다. '
        '같은 날 수집된 [리포트 브리핑](증권사 목표가 리포트, collect_kis_news 수집분)도 함께 '
        '모아, 특징주와 종목코드가 겹치면 등락 참고 근거로 붙이고 안 겹치면 별도 섹션으로 '
        '정리합니다. 실시간 RSS/KIS 수집(collect_keyword_news, collect_kis_news)은 그대로 AI '
        '호출 없이 수집·매칭만 하고, 개별 기사 AI 요약은 회원이 포스팅 직전 직접 요청할 때만 '
        '이뤄집니다.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--session', choices=sorted(SESSION_LABELS), required=True,
            help='midday(장중) 또는 close(마감후)',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='MarketHoliday상 비거래일이거나 이미 오늘 같은 세션 브리핑이 있어도 강제로 생성합니다.',
        )

    def handle(self, *args, **options):
        session = options['session']
        session_label = SESSION_LABELS[session]
        today = timezone.localdate()

        holiday = MarketHoliday.objects.filter(date=today).first()
        if not options['force'] and holiday is not None and not holiday.is_market_open:
            self.stdout.write(self.style.WARNING(f"[-] {today}은 휴장일이라 브리핑을 건너뜁니다."))
            return

        pseudo_url = f"internal://featured-briefing/{today}/{session}"
        if not options['force'] and AnalyzedArticle.objects.filter(original_url=pseudo_url).exists():
            self.stdout.write(self.style.WARNING(f"[-] 오늘 {session_label} 브리핑이 이미 있습니다."))
            return

        movers_qs = RankedMover.objects.filter(rank_type__in=('GAINER', 'LOSER')).order_by('rank_type', 'rank')
        movers = [
            {'rank_type': m.rank_type, 'ticker': m.ticker, 'name': m.name, 'price': m.price, 'change_pct': m.change_pct}
            for m in movers_qs
        ]
        if not movers:
            self.stdout.write(self.style.WARNING("[-] RankedMover 데이터가 없습니다. collect_fluctuation_ranking이 먼저 돌아야 합니다."))
            return

        mismatches = _find_sign_mismatches(movers)
        if mismatches:
            detail = ', '.join(f"{m['name']}({m['rank_type']} {m['change_pct']}%)" for m in mismatches)
            raise CommandError(
                f"RankedMover 정합성 오류로 {session_label} 브리핑 생성을 중단합니다 — "
                f"등락 방향과 순위 구분이 어긋난 종목: {detail}. collect_fluctuation_ranking 데이터를 "
                f"먼저 확인하세요(발행하지 않고 종료합니다)."
            )

        reports_qs = (
            AnalyzedArticle.objects
            .filter(title__startswith='[리포트 브리핑]', scraped_at__date=today)
            .exclude(stock=None)
            .select_related('stock')
            .order_by('scraped_at')
        )
        reports_list = list(reports_qs)

        # StockRealtimePrice(5분 캐시)는 정규장 마감 직전 마지막 틱(정산 전 값)에 멈춰 있어
        # 마감 후 조회하면 실제 종가와 어긋난다(2026-08-06 심텍 사례: 캐시 106,700/+3.59% vs
        # 실제 종가 106,000/+2.91%) — 대시보드 매수/매도 신호 표에서 같은 문제를 캐시를 아예
        # 쓰지 않고 get_stock_close_price 온디맨드 조회로 고친 것과 동일한 방식으로 맞춘다
        # (정규장 중엔 실시간가, 마감 후엔 정산된 종가를 알아서 골라 줌). KIS 일봉 조회가 동시
        # 요청에 약해 4개씩 병렬 + 재시도로 조회한다.
        def _fetch_price(r, attempts=3):
            for attempt in range(attempts):
                try:
                    fetched = get_stock_close_price(r.stock.ticker)
                    return r.pk, fetched['close'], fetched['change_pct']
                except Exception:
                    if attempt == attempts - 1:
                        return r.pk, None, None
                    time.sleep(0.3)
            return r.pk, None, None

        price_map = {}
        if reports_list:
            with ThreadPoolExecutor(max_workers=4) as executor:
                for pk, price, change_pct in executor.map(_fetch_price, reports_list):
                    price_map[pk] = (price, change_pct)

        def _report_dict(r):
            price, change_pct = price_map.get(r.pk, (None, None))
            return {
                'ticker': r.stock.ticker,
                'name': r.stock.name,
                'text': _report_thesis_text(r.title, r.stock.name),
                'price': price,
                'change_pct': change_pct,
            }

        reports = [_report_dict(r) for r in reports_list]

        self.stdout.write(self.style.SUCCESS(
            f"🚀 {today} {session_label} AI 특징주 브리핑을 생성합니다. (증권사 리포트 {len(reports)}건 포함)"
        ))
        draft = article_ai.generate_featured_briefing(session_label, movers, reports)
        title = f"{today:%Y-%m-%d} {session_label} 특징주 브리핑"

        AnalyzedArticle.objects.update_or_create(
            original_url=pseudo_url,
            defaults=dict(
                stock=None,
                matched_keyword=None,
                title=title,
                source_media="NextFinUp AI 브리핑",
                source_type=AnalyzedArticle.SOURCE_AI_BRIEFING,
                ai_summary=draft['ai_summary'],
                ai_analysis=draft['ai_analysis'],
                blog_content=draft['blog_content'],
                original_content=article_ai.movers_to_text(movers, reports),
                thumbnail=thumbnail.build_thumbnail_file(
                    title, category_label="특징주 브리핑", is_economic_news=True,
                ),
                applied_template='T1',
                is_premium=False,
                is_posted=False,
                ai_generated=True,
                ai_summarized_by=None,
                ai_summarized_at=timezone.now(),
            ),
        )
        self.stdout.write(self.style.SUCCESS(f"🎉 {session_label} 브리핑 저장 완료: {title}"))
