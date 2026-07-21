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


def get_client_ip(request):
    """프록시(X-Forwarded-For) 뒤에 있는 경우까지 고려해 실제 접속 IP를 추출."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


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
            return {'title': '', 'content': '', 'source_media': _domain_from_url(url)}

        raw = trafilatura.extract(downloaded, with_metadata=True, output_format='json')
        if not raw:
            return {'title': '', 'content': '', 'source_media': _domain_from_url(url)}

        data = json.loads(raw)
        return {
            'title': (data.get('title') or '').strip(),
            'content': (data.get('text') or '').strip(),
            'source_media': (data.get('sitename') or '').strip() or _domain_from_url(url),
        }
    except Exception:
        return {'title': '', 'content': '', 'source_media': _domain_from_url(url)}
