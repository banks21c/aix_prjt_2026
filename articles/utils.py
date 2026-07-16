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
