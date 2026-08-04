def format_won(amount):
    return f"{amount:,.0f}원"


def format_signed_won(amount):
    return f"{amount:+,.0f}원"


def format_signed_pct(pct):
    return f"{pct:+.2f}%"


def format_volume(volume):
    return f"{volume:,.0f}주"


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


def _find_mentioned_stocks(text, max_count=20):
    """장 마감 시황처럼 여러 종목명이 한꺼번에 나오는 기사 원문에서, 실시간 시세 캐시가 있는
    is_major_index 종목만 후보로 놓고 실제로 언급된 종목을 찾는다. "SK"가 "SK하이닉스"/"SK스퀘어"
    안에서 잘못 매칭되는 것을 막기 위해, 이름이 긴 종목부터 먼저 매칭하고 앞뒤 글자가 한글
    음절/영숫자로 이어지면(=다른 종목명의 일부) 그 매칭은 버린다."""
    import re
    from .models import StockItem

    if not text:
        return []

    candidates = list(StockItem.objects.filter(is_active=True, is_major_index=True).only('id', 'ticker', 'name'))
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

    stocks = _find_mentioned_stocks(text)
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
    """뉴스 게시판의 'AI 요약' 버튼(news_ai_summarize_view)에 표시할 회원의 오늘 사용 현황.
    scraping_stats와 동일한 패턴으로 MemberGrade.daily_ai_summarize_limit 기준 계산한다 —
    등급이 없거나 한도가 비어있으면(NULL) 무제한. 관리자는 등급과 무관하게 항상 무제한."""
    from django.utils import timezone
    from .models import AnalyzedArticle

    grade = getattr(getattr(user, 'preference', None), 'grade', None)
    is_admin = user.is_staff or user.is_superuser
    limit = grade.daily_ai_summarize_limit if grade else None
    is_unlimited = is_admin or limit is None
    today_count = AnalyzedArticle.objects.filter(
        ai_summarized_by=user, ai_summarized_at__date=timezone.localdate()
    ).count()
    remaining = None if is_unlimited else max(0, limit - today_count)
    return {
        'grade': grade,
        'is_admin': is_admin,
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
