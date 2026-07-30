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


def search_news_by_keyword(query, limit=20):
    """뉴스 포스팅 화면(news_scrape_view)의 '검색어로 찾기' 모드가 쓰는 실시간 검색.
    별도 뉴스 검색 API 없이, collect_keyword_news가 주기 수집에 쓰는 등록된 NewsSource RSS
    피드들을 그 자리에서 병렬로 다시 조회해 제목/설명에 검색어가 포함된 항목만 추려 돌려준다 —
    그래서 검색 결과 링크는 collect_keyword_news와 동일하게 언론사 원문 직링크라, 회원이 결과를
    고르면 곧바로 기존 URL 스크래핑 플로우(fetch_article_metadata)로 넘길 수 있다."""
    import urllib.request
    import xml.etree.ElementTree as ET
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .models import NewsSource, AnalyzedArticle

    query = (query or '').strip()
    if not query:
        return []

    sources = list(NewsSource.objects.filter(is_active=True))
    if not sources:
        return []

    existing_ids_by_url = dict(AnalyzedArticle.objects.values_list('original_url', 'id'))

    def _fetch_one(source):
        found = []
        try:
            req = urllib.request.Request(
                source.rss_url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'},
            )
            with urllib.request.urlopen(req, timeout=6) as response:
                xml_content = response.read()
            root = ET.fromstring(xml_content)
            for item in root.findall('.//item'):
                title_el = item.find('title')
                link_el = item.find('link')
                if title_el is None or link_el is None or not title_el.text or not link_el.text:
                    continue
                title = title_el.text.strip()
                desc_el = item.find('description')
                description = desc_el.text.strip() if desc_el is not None and desc_el.text else ''
                if query.lower() not in f"{title} {description}".lower():
                    continue
                link = link_el.text.strip()
                pub_el = item.find('pubDate')
                found.append({
                    'title': title,
                    'link': link,
                    'source': source.name,
                    'pub_date': pub_el.text.strip() if pub_el is not None and pub_el.text else '',
                    'already_registered': link in existing_ids_by_url,
                    'article_id': existing_ids_by_url.get(link),
                })
        except Exception:
            pass
        return found

    results = []
    with ThreadPoolExecutor(max_workers=len(sources)) as executor:
        futures = [executor.submit(_fetch_one, source) for source in sources]
        for future in as_completed(futures):
            results.extend(future.result())

    return results[:limit]


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
