"""네이버 블로그 → 이 사이트 DB 이관을 위한 스크래핑/이미지 재호스팅 로직.

수집 커맨드(management/commands/collect_naver_blog_posts.py)와, 나중에 붙일 발행
커맨드가 같은 함수를 쓰도록 여기 모아 두었다(blog_posting.py가 발행 쪽에서 하는 역할과
같은 위치).

네이버 특유의 제약 두 가지가 이 모듈 대부분의 이유다:

1. 글 본문은 `blog.naver.com/<blogId>/<logNo>`를 그냥 받아오면 iframe 껍데기만 온다.
   실제 본문은 `PostView.naver?blogId=..&logNo=..`에 있는데, 이쪽은 **Referer 헤더가
   없으면 빈 stub을 돌려준다**. 그래서 REFERER_TEMPLATE을 항상 같이 보낸다.
2. 본문 이미지(postfiles.pstatic.net)는 외부 도메인 Referer로 요청하면 403이다. 즉
   원본 src를 그대로 둔 채 다른 블로그에 옮기면 이미지가 전부 깨진다. 그래서 서버에서
   미리 내려받아(같은 Referer 규칙 적용) media/naver_migration/ 아래로 재호스팅하고
   src를 우리 도메인 URL로 바꾼다. 또 쿼리스트링 없는 bare URL은 작은 썸네일이라,
   원본 화질을 받으려면 `?type=w2000`을 붙여야 한다.
"""

import hashlib
import mimetypes
import os
import re
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings

RSS_TEMPLATE = 'https://rss.blog.naver.com/{blog_id}.xml'
POST_VIEW_TEMPLATE = 'https://blog.naver.com/PostView.naver?blogId={blog_id}&logNo={log_no}'
REFERER_TEMPLATE = 'https://blog.naver.com/{blog_id}'
CANONICAL_TEMPLATE = 'https://blog.naver.com/{blog_id}/{log_no}'

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
TIMEOUT = 20

# 재호스팅한 이미지가 쌓이는 곳. 과거 이관(2026-08) 때 쓰던 경로를 그대로 이어 쓴다 —
# 그때 올린 이미지들이 지금도 timelessculturelab.com/deepsleepway.com 글에서 이 URL을
# 참조하고 있어서, 디렉터리를 옮기면 그 사이트들의 이미지가 다 깨진다.
IMAGE_SUBDIR = 'naver_migration'

# 본문 컨테이너 후보. 스마트에디터 ONE(.se-main-container)이 지금 대부분이지만, 오래된
# 글은 구 에디터라 컨테이너 클래스가 다르다 — 앞에서부터 처음 걸리는 것을 쓴다.
BODY_SELECTORS = ['.se-main-container', '#postViewArea', '.post-view', '#viewTypeSelector']
TITLE_SELECTORS = ['.se-title-text', '.pcol1 .itemSubjectBoldfont', '.htitle', 'title']


def _session():
    s = requests.Session()
    s.headers.update({'User-Agent': USER_AGENT})
    return s


def fetch_post_list(blog_id, session=None):
    """RSS 피드에서 글 목록을 뽑아 dict 리스트로 돌려준다(본문은 포함하지 않는다).

    네이버 RSS는 전체 글을 한 번에 주는 게 아니라 최신 N건만 주는 경우가 있어, 여기서
    나온 목록이 블로그 전체와 일치한다는 보장은 없다. 다만 실측(soonks21, 43건)에서는
    전량이 나왔고, 공개 API가 따로 없어 현실적인 대안이 이것뿐이다."""
    session = session or _session()
    resp = session.get(RSS_TEMPLATE.format(blog_id=blog_id), timeout=TIMEOUT)
    resp.raise_for_status()

    # RSS는 XML이지만 네이버 쪽 인코딩/네임스페이스가 들쭉날쭉해 lxml-xml 파서로 관대하게 읽는다.
    soup = BeautifulSoup(resp.content, 'lxml-xml')
    items = []
    for item in soup.find_all('item'):
        link = (item.link.get_text(strip=True) if item.link else '')
        log_no = extract_log_no(link)
        if not log_no:
            continue
        items.append({
            'log_no': log_no,
            'title': (item.title.get_text(strip=True) if item.title else '')[:500],
            'source_url': CANONICAL_TEMPLATE.format(blog_id=blog_id, log_no=log_no),
            'category_name': (item.category.get_text(strip=True) if item.category else '')[:100],
            'posted_at': _parse_pub_date(item.pubDate.get_text(strip=True) if item.pubDate else ''),
        })
    return items


def extract_log_no(url):
    """네이버 글 URL에서 logNo(글 번호)를 뽑는다. `?logNo=` 쿼리형과 `/<blogId>/<logNo>`
    경로형 둘 다 실제로 쓰이므로 양쪽을 다 본다."""
    if not url:
        return ''
    parsed = urlparse(url)
    from_query = parse_qs(parsed.query).get('logNo')
    if from_query:
        return from_query[0]
    m = re.search(r'/(\d{6,})', parsed.path)
    return m.group(1) if m else ''


def _parse_pub_date(raw):
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def fetch_post_detail(blog_id, log_no, session=None, download_images=True):
    """글 1건의 본문을 긁어 dict로 돌려준다.

    반환: {'title', 'original_html', 'content_html', 'excerpt', 'image_map',
           'image_count', 'thumbnail_url'}
    본문 컨테이너를 못 찾으면 ValueError를 올린다(호출부가 status='FAILED'로 기록한다)."""
    session = session or _session()
    url = POST_VIEW_TEMPLATE.format(blog_id=blog_id, log_no=log_no)
    resp = session.get(url, headers={'Referer': REFERER_TEMPLATE.format(blog_id=blog_id)}, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or 'utf-8'

    soup = BeautifulSoup(resp.text, 'html.parser')

    body = None
    for selector in BODY_SELECTORS:
        body = soup.select_one(selector)
        if body:
            break
    if body is None:
        raise ValueError('본문 컨테이너를 찾지 못했습니다(비공개 글이거나 에디터 형식이 다를 수 있음)')

    title = ''
    for selector in TITLE_SELECTORS:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            title = node.get_text(strip=True)
            break

    original_html = str(body)
    image_map = {}
    if download_images:
        image_map = rehost_images(body, blog_id, session=session)
    else:
        _normalize_image_tags(body)

    content_html = str(body)
    text = body.get_text(' ', strip=True)
    thumb = ''
    first_img = body.find('img')
    if first_img and first_img.get('src'):
        thumb = first_img['src']

    return {
        'title': title[:500],
        'original_html': original_html,
        'content_html': content_html,
        'excerpt': re.sub(r'\s+', ' ', text)[:300],
        'image_map': image_map,
        'image_count': len(body.find_all('img')),
        'thumbnail_url': thumb[:500],
    }


def _normalize_image_tags(body):
    """네이버는 실제 주소를 src가 아니라 data-lazy-src에 넣고 src에는 1x1 placeholder를
    두는 경우가 있다. 재호스팅을 하든 안 하든 먼저 진짜 주소를 src로 끌어올린다."""
    for img in body.find_all('img'):
        real = img.get('data-lazy-src') or img.get('data-src') or img.get('src')
        if not real:
            continue
        img['src'] = real
        for attr in ('data-lazy-src', 'data-src', 'srcset', 'data-linkdata', 'onerror'):
            img.attrs.pop(attr, None)


# 블로그에 직접 올린 사진이 사는 호스트. 이 호스트들만 ?type= 파라미터로 화질을 고를 수
# 있다 — 같은 pstatic.net이라도 dthumb-phinf(외부 링크 카드 썸네일 프록시)는 원본 주소를
# 쿼리스트링(?src=...)에 담고 있어서, 쿼리를 떼고 type을 붙이면 URL 자체가 깨진다.
IMAGE_QUALITY_HOSTS = ('postfiles.pstatic.net', 'blogfiles.pstatic.net', 'blogfiles.naver.net')


def original_quality_url(url):
    """네이버 이미지 URL을 원본 화질(w2000)로 바꾼다. 쿼리 없는 bare URL은 작은 기본
    썸네일이 내려오기 때문에 반드시 붙여야 한다. 화질 파라미터를 받지 않는 호스트는
    그대로 둔다."""
    base = url.split('?')[0]
    if urlparse(base).netloc not in IMAGE_QUALITY_HOSTS:
        return url
    return f'{base}?type=w2000'


def rehost_images(body, blog_id, session=None):
    """본문 안 <img>를 전부 서버로 내려받아 media/naver_migration/ 아래 저장하고, src를
    우리 도메인 절대 URL로 바꾼다. {원본 URL: media 기준 상대경로}를 돌려준다.

    파일명은 원본 URL의 sha1 앞 16자라, 같은 이미지를 다시 만나면 이미 받아둔 파일을
    그대로 재사용한다(재수집이 싸다). 개별 이미지 실패는 건너뛰고 원본 src를 남긴다 —
    이미지 한 장 때문에 글 전체 수집을 실패로 만들지 않기 위해서다."""
    session = session or _session()
    _normalize_image_tags(body)

    dest_dir = os.path.join(settings.MEDIA_ROOT, IMAGE_SUBDIR)
    os.makedirs(dest_dir, exist_ok=True)

    image_map = {}
    for img in body.find_all('img'):
        src = img.get('src') or ''
        if not src.startswith('http'):
            continue
        target = original_quality_url(src)
        try:
            rel_path = _download_image(target, blog_id, dest_dir, session)
        except Exception:
            continue  # 원본 src를 그대로 둔다(대상 사이트에서 깨질 수 있으나 글은 살린다)
        image_map[src] = rel_path
        img['src'] = f"{settings.SITE_URL.rstrip('/')}/media/{rel_path}"
    return image_map


def _download_image(url, blog_id, dest_dir, session):
    name = hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]
    existing = [f for f in os.listdir(dest_dir) if f.startswith(name + '.')]
    if existing:
        return f'{IMAGE_SUBDIR}/{existing[0]}'

    resp = session.get(url, headers={'Referer': REFERER_TEMPLATE.format(blog_id=blog_id)}, timeout=TIMEOUT)
    resp.raise_for_status()
    ext = mimetypes.guess_extension(resp.headers.get('Content-Type', '').split(';')[0].strip()) or '.jpg'
    if ext == '.jpe':
        ext = '.jpg'
    filename = f'{name}{ext}'
    with open(os.path.join(dest_dir, filename), 'wb') as fh:
        fh.write(resp.content)
    return f'{IMAGE_SUBDIR}/{filename}'
