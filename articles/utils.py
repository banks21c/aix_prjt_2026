def html_to_plain_text(html):
    """Toast UI Editor(위지윅) 출력처럼 <p>/<br> 등으로 문단이 구분된 HTML을, 문단 구조는
    줄바꿈으로 보존한 평문으로 변환한다. news_write_view의 original_content(원문 전문 표시용
    — news_detail.html이 그대로 {{ }}로 이스케이프해서 보여줌), AI 프롬프트 입력, 썸네일
    미리보기 텍스트는 전부 평문을 기대하는데, 에디터 도입 후 HTML을 그대로 넣으면 태그가
    화면에 글자 그대로 노출된다(신고: "원문 전문에 태그가 붙어서 나오네")."""
    import re
    from django.utils.html import strip_tags

    text = re.sub(r'</(p|div|h[1-6]|li|blockquote)>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = strip_tags(text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def round_to_krx_tick(price):
    """KRX(코스피/코스닥) 실제 호가단위로 스냅한다. 가격대별 최소 호가 간격이 달라서
    (예: 5만원대는 100원 단위, 20만원대는 500원 단위, 50만원 이상은 1000원 단위로만 실제
    체결 가능) run_stock_prediction의 회귀 모델이 뱉는 연속값(예: 42903.28원)을 그대로
    쓰면 실제로는 존재할 수 없는 가격이 예측가로 나온다 — 실측 신고로 확인됨."""
    price = float(price)
    if price < 2000:
        tick = 1
    elif price < 5000:
        tick = 5
    elif price < 20000:
        tick = 10
    elif price < 50000:
        tick = 50
    elif price < 200000:
        tick = 100
    elif price < 500000:
        tick = 500
    else:
        tick = 1000
    return round(price / tick) * tick


def format_won(amount):
    return f"{amount:,.0f}원"


def format_signed_won(amount):
    return f"{amount:+,.0f}원"


def format_signed_pct(pct):
    return f"{pct:+.2f}%"


def format_volume(volume):
    return f"{volume:,.0f}주"


def limit_label(stats):
    """scraping_stats/ai_summarize_stats/posting_stats 결과의 등급 표시명. 프리미엄은 등급이
    아니라 UserSubscription 쪽 고정 한도를 쓰므로, 한도 초과 안내 문구에는 등급명 대신
    '프리미엄'을 보여준다."""
    if stats.get('is_premium'):
        return '프리미엄'
    return stats['grade'].name if stats['grade'] else '일반'


def format_trading_value(amount):
    """거래대금은 원 단위 그대로면 자릿수가 너무 많아, 조/억/만 단위로 줄여서 보여준다."""
    amount = float(amount)
    if amount >= 1_0000_0000_0000:
        return f"{amount / 1_0000_0000_0000:.1f}조원"
    if amount >= 1_0000_0000:
        return f"{amount / 1_0000_0000:.0f}억원"
    if amount >= 1_0000:
        return f"{amount / 1_0000:.0f}만원"
    return f"{amount:,.0f}원"


def find_mentioned_stocks(text, max_count=20, major_index_only=True):
    """장 마감 시황처럼 여러 종목명이 한꺼번에 나오는 기사 원문에서 실제로 언급된 종목을 찾는다.
    "SK"가 "SK하이닉스"/"SK스퀘어" 안에서 잘못 매칭되는 것을 막기 위해, 이름이 긴 종목부터 먼저
    매칭하고 앞뒤 글자가 한글 음절/영숫자로 이어지면(=다른 종목명의 일부) 그 매칭은 버린다.
    major_index_only=True(기본값)면 실시간 시세 캐시가 있는 is_major_index 종목만 후보로
    놓는다 — resolve_thumbnail_stock/build_mentioned_stocks_table은 그 캐시(StockRealtimePrice)에
    의존해서 이 기본값이 맞아야 한다. collect_keyword_news --full-universe처럼 시세 표시 없이
    "본문에 이 종목명이 등장하는지"만 보면 되는 호출부는 False로 넘겨 활성 전종목을 후보로 쓴다.

    언론사 저작권 안내문(예: "<ⓒ투자가를 위한 경제콘텐츠 플랫폼, 아시아경제. 무단전재 배포금지...>")은
    매칭 전에 제거한다 — "아시아경제"가 실제로 상장된 종목명(127710)이라, 이 문구가 실린 기사는
    본문에 다른 종목이 전혀 언급되지 않아도 매번 아시아경제로 잘못 매칭되는 문제가 있었다
    (2026-08-06 확인: 오늘 수집분 중 이 문구 때문에 잘못 매칭된 기사 5건)."""
    import re
    from .models import StockItem

    if not text:
        return []

    text = re.sub(r'<ⓒ[^\n]*?>', ' ', text)

    stock_qs = StockItem.objects.filter(is_active=True)
    if major_index_only:
        stock_qs = stock_qs.filter(is_major_index=True)
    candidates = list(stock_qs.only('id', 'ticker', 'name'))
    candidates.sort(key=lambda s: len(s.name), reverse=True)

    boundary_re = re.compile(r'[가-힣a-zA-Z0-9]')
    found = []
    seen_names = set()
    for stock in candidates:
        name = stock.name
        if not name or name in seen_names:
            continue
        # 한국어는 명사 뒤에 조사가 공백 없이 바로 붙는다("삼성전자도", "SK하이닉스와")라,
        # 첫 등장 위치만 보면 조사 때문에 뒤쪽 경계 검사에서 걸러질 수 있다. 그래서 모든 등장
        # 위치를 훑어 경계 검사를 통과하는 첫 번째 위치를 채택한다.
        for match in re.finditer(re.escape(name), text):
            idx = match.start()
            before = text[idx - 1] if idx > 0 else ''
            after = text[match.end()] if match.end() < len(text) else ''
            if boundary_re.match(before) or boundary_re.match(after):
                continue
            seen_names.add(name)
            found.append((idx, stock))
            break

    found.sort(key=lambda pair: pair[0])
    return [stock for _, stock in found[:max_count]]


def resolve_thumbnail_stock(article):
    """썸네일 가격 카드에 실제로 보여줄 종목을 정한다. 제목에서만 국내 종목명을 찾는다 —
    본문까지 보면 기사의 진짜 주제가 아닌 종목(예: 해외 기업 기사에서 문맥상 스쳐 지나가듯
    언급되는 국내 공급망 종목)이 잡혀 엉뚱한 시세가 썸네일에 나가기 쉽다(실측 사례:
    스페이스X 실적 기사 본문에 "하이닉스"가 한 번 언급됐다는 이유로 썸네일에 스페이스X가 아닌
    하이닉스 시세가 붙음). 제목은 그 기사가 실제로 어떤 종목을 다루는지 훨씬 신뢰도 높은
    신호라, 제목에 국내 종목명이 없으면 이 기사는 특정 종목을 다루는 게 아니라고 보고 None을
    반환한다 — 호출부(build_thumbnail_file)가 종목 시세 카드 대신 매칭 키워드/AI 요약 기반의
    종목 없는 카드로 자연스럽게 폴백한다."""
    title = article.title or ''
    mentioned = find_mentioned_stocks(title, max_count=20)
    if not mentioned:
        return None
    if len(mentioned) == 1:
        return mentioned[0]

    import re
    # 긴 이름부터 먼저 훑어 이미 매칭된 구간은 다시 세지 않는다 — "카카오페이는"처럼 조사가
    # 바로 붙는 게 정상적인 한국어라("카카오페이" 뒤 글자가 한글이라고 거절하면 사실상 모든
    # 언급이 걸러진다), 자리 겹침만으로 "카카오페이" 안의 "카카오"를 걸러내고 그 외엔 전부
    # 실제 등장으로 센다.
    ordered = sorted(mentioned, key=lambda s: len(s.name), reverse=True)
    counts = {stock.id: 0 for stock in mentioned}
    covered = bytearray(len(title))
    for stock in ordered:
        for m in re.finditer(re.escape(stock.name), title):
            if any(covered[m.start():m.end()]):
                continue
            counts[stock.id] += 1
            for i in range(m.start(), m.end()):
                covered[i] = 1
    return max(mentioned, key=lambda stock: counts[stock.id])


def build_mentioned_stocks_table(text):
    """AI 요약 시점에 원문에서 언급된 종목들의 시세를 표로 정리한 HTML을 만든다
    (종목명(코드)/현재가/전일대비/등락률/거래량/거래대금). 장 마감 시황처럼 여러 종목이 한
    기사에 등장하는 경우를 위한 기능. 정규장 중에는 is_major_index 종목에 한해
    collect_stock_realtime_price가 5분마다 채워둔 StockRealtimePrice 캐시를 그대로 쓴다(빠름).
    정규장이 끝난 뒤에는 이 캐시를 쓰지 않는다 — collect_stock_realtime_price는 정규장
    마감(15:30 KST) 직전 마지막 5분 주기 스냅샷(예: 15:25 KST)에서 멈추기 때문에, 실제 15:30
    종가보다 몇 분 이른 값이라 종목마다 마감 직전 변동폭만큼 오차가 생긴다(실측 사례: 같은
    표 안에서 종목별로 캐시-실제 종가 차이가 제각각이라 "가격이 조금씩 안 맞는다"는 문의로
    확인됨). 그래서 마감 후에는 전 종목을 KIS에 온디맨드로 다시 조회한다 —
    get_stock_close_price가 정규장 중이면 실시간가를, 마감 후~다음 개장 전에는 시간외단일가가
    아닌 정확한 정규장 종가를 돌려주도록 알아서 분기한다. 온디맨드 조회가 실패한 종목은 숫자를
    지어내지 않기 위해 조용히 표에서 빠진다."""
    from .kis_client import get_stock_close_price, is_regular_session_open
    from .models import StockRealtimePrice

    stocks = find_mentioned_stocks(text)
    if not stocks:
        return ''

    # 정규장 중에만 캐시를 신뢰한다 — 마감 후에는 위 사유로 전부 온디맨드 재조회.
    cached_prices = (
        {p.stock_id: p for p in StockRealtimePrice.objects.filter(stock__in=stocks)}
        if is_regular_session_open() else {}
    )
    cell_base = "border:1px solid #e9ecef;padding:8px 10px;text-align:center"
    rows = []
    for stock in stocks:
        cached = cached_prices.get(stock.id)
        if cached:
            close_price, change, change_pct, volume = (
                cached.close_price, cached.change, cached.change_pct, cached.volume,
            )
        else:
            try:
                fetched = get_stock_close_price(stock.ticker)
            except Exception:
                continue
            close_price, change, change_pct, volume = (
                fetched['close'], fetched['change'], fetched['change_pct'], fetched['volume'],
            )

        color = '#e53935' if change > 0 else '#1e88e5' if change < 0 else '#495057'
        move_style = f'{cell_base};color:{color}'
        trading_value = close_price * volume
        naver_url = f"https://finance.naver.com/item/main.naver?code={stock.ticker}"
        rows.append(
            '<tr>'
            f'<td style="{cell_base}">'
            f'<a href="{naver_url}" target="_blank" rel="noopener noreferrer" style="color:inherit">{stock.name}({stock.ticker})</a>'
            '</td>'
            f'<td style="{cell_base}">{format_won(close_price)}</td>'
            f'<td style="{move_style}">{format_signed_won(change)}</td>'
            f'<td style="{move_style}">{format_signed_pct(change_pct)}</td>'
            f'<td style="{cell_base}">{format_volume(volume)}</td>'
            f'<td style="{cell_base}">{format_trading_value(trading_value)}</td>'
            '</tr>'
        )

    if not rows:
        return ''

    header = (
        '<tr>'
        f'<th style="{cell_base}">종목명(코드)</th><th style="{cell_base}">현재가</th><th style="{cell_base}">전일대비</th>'
        f'<th style="{cell_base}">등락률</th><th style="{cell_base}">거래량</th><th style="{cell_base}">거래대금</th>'
        '</tr>'
    )
    return (
        '<h3>📊 관련 종목 시세</h3>'
        '<table style="width:100%;border-collapse:collapse;font-size:14px;margin:12px 0">'
        f'<thead style="background:#0d47a1;color:#fff">{header}</thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
    )


def scraping_stats(user):
    """URL로 기사 등록 화면(news_scrape_view)에 표시할 회원의 스크래핑 현황.
    하루 등록 가능 건수는 회원 등급(MemberGrade.daily_scrape_limit)을 기준으로 계산한다 — 등급이
    없거나 한도가 비어있으면(NULL) 무제한. 관리자(is_staff/is_superuser)는 등급과 무관하게 항상 무제한."""
    from django.utils import timezone
    from .models import AnalyzedArticle

    grade = getattr(getattr(user, 'preference', None), 'grade', None)
    is_admin = user.is_staff or user.is_superuser
    limit = grade.daily_scrape_limit if grade else None
    is_unlimited = is_admin or limit is None
    today_count = AnalyzedArticle.objects.filter(
        scraped_by=user, scraped_at__date=timezone.localdate()
    ).count()
    remaining = None if is_unlimited else max(0, limit - today_count)
    return {
        'grade': grade,
        'is_admin': is_admin,
        'remaining': remaining,
        'today_count': today_count,
    }


def ai_summarize_stats(user):
    """뉴스 게시판의 'AI 요약' 버튼(news_ai_summarize_view) — 수집 기사 AI 요약, URL 스크랩 후
    AI 요약, 직접 작성 후 AI 요약(news_write_view) 세 경로가 모두 ai_summarized_by/
    ai_summarized_at을 채우므로 이 함수 하나로 셋을 함께 카운트한다.
    scraping_stats와 동일한 패턴으로 MemberGrade.daily_ai_summarize_limit 기준 계산하되,
    프리미엄 구독(UserSubscription.is_active_premium)은 등급과 무관하게 하루
    UserSubscription.PREMIUM_DAILY_AI_SUMMARIZE_LIMIT건으로 고정한다 — gpt-4o-mini 호출
    비용 때문에 등급처럼 비워서(NULL) 무제한으로 두지 않는다. 관리자는 여전히 항상 무제한."""
    from django.utils import timezone
    from .models import AnalyzedArticle, UserSubscription

    subscription, _ = UserSubscription.objects.get_or_create(user=user)
    grade = getattr(getattr(user, 'preference', None), 'grade', None)
    is_admin = user.is_staff or user.is_superuser
    if subscription.is_active_premium:
        limit = UserSubscription.PREMIUM_DAILY_AI_SUMMARIZE_LIMIT
    else:
        limit = grade.daily_ai_summarize_limit if grade else None
    is_unlimited = is_admin or limit is None
    today_count = AnalyzedArticle.objects.filter(
        ai_summarized_by=user, ai_summarized_at__date=timezone.localdate()
    ).count()
    remaining = None if is_unlimited else max(0, limit - today_count)
    return {
        'grade': grade,
        'is_admin': is_admin,
        'is_premium': subscription.is_active_premium,
        'limit': limit,
        'remaining': remaining,
        'today_count': today_count,
    }


def get_client_ip(request):
    """프록시(X-Forwarded-For) 뒤에 있는 경우까지 고려해 실제 접속 IP를 추출."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def detect_reuse_restriction(text):
    """스크래핑한 기사 본문에 'ⓒ...무단전재 배포금지, AI 학습 및 활용 금지' 류의 재사용 제한
    문구가 있는지 감지한다. 감지되면 article_ai.generate_draft가 원문 본문을 AI 프롬프트에
    전혀 넣지 않고 제목/구조화된 사실만으로 NextFinUp 자체 해설을 생성하는 분기를 타게 하는
    판별 함수 (AnalyzedArticle.has_reuse_restriction에 저장)."""
    import re

    if not text:
        return False
    pattern = (
        r'무단\s*전재|무단\s*배포|무단\s*복제|재배포\s*금지|전재\s*금지|'
        r'AI\s*학습|AI\s*활용\s*금지|AI\s*학습\s*및?\s*활용'
    )
    return bool(re.search(pattern, text, re.IGNORECASE))


def fetch_article_content(url):
    """뉴스 원문 URL에서 기사 본문 텍스트를 스크래핑한다. 언론사마다 HTML 구조가 달라
    사이트별 셀렉터 대신 trafilatura의 범용 추출을 사용한다. 실패해도 수집 파이프라인
    자체가 끊기면 안 되므로 예외를 삼키고 빈 문자열을 반환한다."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ''
        text = trafilatura.extract(downloaded)
        return (text or '').strip()
    except Exception:
        return ''


def _domain_from_url(url):
    from urllib.parse import urlparse
    return urlparse(url).netloc.removeprefix('www.')


def _media_name_fallback(url):
    """trafilatura가 sitename 메타데이터를 못 뽑아냈을 때 쓰는 언론사명 폴백.
    도메인 그대로(mk.co.kr 등)는 회원에게 의미가 없으니, MediaOutlet에 등록된 도메인이면
    그 언론사명을 대신 쓴다 — 없으면 (관리자가 admin에서 나중에 매핑을 추가할 수 있도록)
    도메인을 그대로 반환한다."""
    from .models import MediaOutlet

    domain = _domain_from_url(url)
    outlet = MediaOutlet.objects.filter(domain=domain).first()
    return outlet.name if outlet else domain


def fetch_article_metadata(url):
    """관리자가 URL을 직접 입력해 기사를 등록하는 화면(news_scrape_view)에서 쓰는 스크래퍼.
    fetch_article_content와 달리 제목/매체명까지 한 번에 뽑아야 해서 trafilatura의 메타데이터
    포함 추출을 사용한다. 실패해도 예외를 삼키고 빈 값을 반환해 호출부가 사용자 메시지로
    처리하게 한다."""
    import json
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return {'title': '', 'content': '', 'source_media': _media_name_fallback(url)}

        raw = trafilatura.extract(downloaded, with_metadata=True, output_format='json')
        if not raw:
            return {'title': '', 'content': '', 'source_media': _media_name_fallback(url)}

        data = json.loads(raw)
        return {
            'title': (data.get('title') or '').strip(),
            'content': (data.get('text') or '').strip(),
            'source_media': (data.get('sitename') or '').strip() or _media_name_fallback(url),
        }
    except Exception:
        return {'title': '', 'content': '', 'source_media': _media_name_fallback(url)}
