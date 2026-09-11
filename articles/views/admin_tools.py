import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_POST
from google import genai
from google.genai import types as genai_types
from openai import BadRequestError, OpenAI

from django.contrib.auth.models import User

from .. import blog_posting
from ..image_series import (
    DEFAULT_REF_MODE, MAX_SCENES, REF_MODES, REF_MODE_LABELS,
    read_progress, series_prompt_path, split_prompts,
)
from ..models import AdminUpload, AnalyzedArticle, BlogPostingAccount, FinancialConsultSheet, GeneratedImage, NaverBlogPost, PostedArticle, ThemeColor
from ..utils import limit_label
from .performance import build_ai_performance_context

HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{3}$|^#[0-9a-fA-F]{4}$|^#[0-9a-fA-F]{6}$|^#[0-9a-fA-F]{8}$')

KST = dt_timezone(timedelta(hours=9))

LOG_TAIL_BYTES = 8000  # 로그 파일이 계속 append되며 커질 수 있어(로테이션 미설정) 끝부분만 읽는다
_EXCEPTION_LINE = re.compile(r'^[\w.]*(Error|Exception)\b.*:')  # 트레이스백 마지막 줄 패턴

# collect_stock_data/run_stock_prediction은 무겁고 비정기적이라 cron에 없고, 지금까지 SSH로
# manage.py를 직접 실행해야만 했다. 웹에서 트리거하는 건 임의 커맨드 실행으로 이어질 수 있어
# 위험하므로, 여기 화이트리스트에 있는 것만 (staff 전용 화면에서) 실행 가능하게 제한한다.
PIPELINE_COMMANDS = {
    'collect_stock_data': {
        'label': '주가 데이터 수집 (전 종목, 증분)',
        'command': 'collect_stock_data',
        'args': ['--all'],
        'log_prefix': 'collect_stock_data_manual',
        'note': (
            '종목마다 이미 저장된 최신 날짜 이후 신규 거래일만 받아옵니다(전체 10년치 재수집 아님). '
            '종목당 1.5~3초 대기가 있어 전 종목 기준 수십 분 정도 걸릴 수 있습니다. 데이터 정합성 '
            '재점검 등으로 전체를 통째로 다시 받아야 하면 SSH로 --full을 직접 붙여 실행하세요.'
        ),
    },
    'run_stock_prediction': {
        'label': 'AI 주가 예측 모델 재학습 (전 종목)',
        'command': 'run_stock_prediction',
        'args': ['--all'],
        'log_prefix': 'run_stock_prediction_manual',
        'note': '전 종목(is_active) LightGBM+RandomForest 앙상블 재학습 — CPU를 많이 씁니다.',
    },
    'featured_briefing_midday': {
        'label': '특징주 브리핑 생성 (장중)',
        'command': 'generate_featured_stock_briefing',
        'args': ['--session=midday', '--force'],
        'log_prefix': 'generate_featured_stock_briefing_manual_midday',
        'note': 'AI 1회 호출로 수 초~수십 초 내 끝납니다. --force로 오늘 이미 생성된 브리핑도 덮어씁니다.',
    },
    'featured_briefing_close': {
        'label': '특징주 브리핑 생성 (마감후)',
        'command': 'generate_featured_stock_briefing',
        'args': ['--session=close', '--force'],
        'log_prefix': 'generate_featured_stock_briefing_manual_close',
        'note': 'AI 1회 호출로 수 초~수십 초 내 끝납니다. --force로 오늘 이미 생성된 브리핑도 덮어씁니다.',
    },
    'post_wordpress': {
        'label': '워드프레스 자동 포스팅 즉시 발행',
        'command': 'post_to_wordpress',
        'args': ['--limit', '1'],
        'log_prefix': 'post_to_wordpress_manual',
        'note': (
            '자동 포스팅을 켠 회원 계정마다 아직 발행 안 된 기사 중 가장 최근 것 1건을 실제 '
            '워드프레스에 라이브로 발행합니다(초안 아님). 정기 크론(13:05/15:45)이 실패했을 때 '
            '수동으로 대신 실행하는 용도 — 여러 건 밀렸으면 여러 번 눌러서 하나씩 발행하세요.'
        ),
    },
    'post_blogger': {
        'label': '블로거 자동 포스팅 즉시 발행',
        'command': 'post_to_blogger',
        'args': ['--limit', '1'],
        'log_prefix': 'post_to_blogger_manual',
        'note': (
            '자동 포스팅을 켠 회원 계정마다 아직 발행 안 된 기사 중 가장 최근 것 1건을 실제 '
            '블로거에 라이브로 발행합니다(초안 아님). 정기 크론(13:05/15:45)이 실패했을 때 '
            '수동으로 대신 실행하는 용도 — 여러 건 밀렸으면 여러 번 눌러서 하나씩 발행하세요.'
        ),
    },
    'newsletter_draft': {
        'label': '뉴스레터 초안 즉시 생성',
        'command': 'generate_newsletter_draft',
        'args': [],
        'log_prefix': 'generate_newsletter_draft_manual',
        'note': (
            '그날 마감 특징주 브리핑을 이메일 옷을 입혀 NewsletterIssue(status=READY)로 만듭니다. '
            '마감 브리핑이 아직 없거나 이미 READY 상태 초안이 있으면 아무것도 만들지 않고 '
            '건너뜁니다 — 안전하게 여러 번 눌러도 됩니다.'
        ),
    },
    'newsletter_send': {
        'label': '뉴스레터 즉시 발송',
        'command': 'send_newsletter',
        'args': [],
        'log_prefix': 'send_newsletter_manual',
        'note': (
            'READY 상태인 뉴스레터를 활성 구독자 전원에게 지금 바로 이메일로 발송하고 SENT로 '
            '표시합니다(정기 발송은 21:00). READY 상태가 없으면 아무 것도 보내지 않습니다.'
        ),
    },
}

# 배포 방식이 서버마다 다를 수 있어(이 서버는 Cloudflare Origin 인증서, 예전 서버는 certbot) 존재하는
# 첫 번째 경로를 사용한다 — 둘 다 없으면 그냥 "인증서 없음"으로 표시하고 에러를 내지 않는다.
SSL_CERT_CANDIDATES = (
    '/etc/nginx/cloudflare/nextfinup_origin.pem',
    '/etc/letsencrypt/live/nextfinup.com/fullchain.pem',
)
HEALTH_SYSTEMD_SERVICES = ('nextfinup', 'nginx', 'cron')


def _tail_file(path, max_bytes=LOG_TAIL_BYTES):
    """로그 파일 전체를 읽지 않고 끝에서 max_bytes만 읽어 최근 실행 내용만 반환한다."""
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            chunk = f.read()
    except OSError:
        return ''
    text = chunk.decode('utf-8', errors='replace')
    if size > max_bytes:
        text = text.split('\n', 1)[-1]  # 잘린 첫 줄은 버림
    return text.strip()


def _log_status(tail_text):
    """가장 최근 실행의 '마지막 줄들'만 보고 판단한다 — tail 전체에서 'Traceback'을 찾으면
    이전 실행의 에러가 이후 성공 실행 뒤에도 여전히 tail 범위 안에 남아있어 오탐할 수 있다."""
    if not tail_text:
        return 'unknown'
    last_lines = [l for l in tail_text.splitlines() if l.strip()][-5:]
    for line in last_lines:
        if 'Traceback (most recent call last):' in line or _EXCEPTION_LINE.match(line.strip()):
            return 'error'
    return 'ok'


def _pipeline_is_running(command_name):
    try:
        result = subprocess.run(
            ['pgrep', '-f', f'manage.py {command_name}'], capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _latest_pipeline_log(logs_dir, log_prefix):
    candidates = sorted(logs_dir.glob(f'{log_prefix}_*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


# run_stock_prediction --all이 매일 새벽 도는 것 자체가 (stock, date) 유니크 StockPrediction에
# 날짜별로 새 행을 쌓는 구조라, 정확도 이력을 위한 별도 테이블 없이 이 값을 날짜별로 GROUP BY만
# 하면 그대로 시계열이 나온다. is_major_index/--all 스케일업 이전(2026-08-03 이전)은 종목 수가
# 1~5개뿐인 테스트성 데이터라 평균이 크게 흔들리므로, 차트에는 MIN_STOCKS_FOR_TREND 이상인
# 날짜만 신뢰 가능한 것으로 표시한다 — 다만 표에는 전부 보여줘서 숨기지 않는다.
MIN_STOCKS_FOR_TREND = 50


def _prediction_accuracy_history(days=90):
    from django.db.models import Avg, Count
    from ..models import StockPrediction

    since = (datetime.now(KST) - timedelta(days=days)).date()
    rows = (
        StockPrediction.objects
        .filter(date__gte=since)
        .exclude(holdout_accuracy__isnull=True)
        .values('date')
        .annotate(
            n=Count('id'),
            avg_acc=Avg('holdout_accuracy'),
            avg_acc_flow=Avg('holdout_accuracy_flow'),
        )
        .order_by('date')
    )
    history = []
    for row in rows:
        history.append({
            'date': row['date'].strftime('%Y-%m-%d'),
            'n': row['n'],
            'avg_acc_pct': round(row['avg_acc'] * 100, 2) if row['avg_acc'] is not None else None,
            'avg_acc_flow_pct': round(row['avg_acc_flow'] * 100, 2) if row['avg_acc_flow'] is not None else None,
            'reliable': row['n'] >= MIN_STOCKS_FOR_TREND,
        })
    return history


def _blog_publish_preview(platform):
    """post_to_wordpress/post_to_blogger를 지금 실행하면 계정별로 무엇이 발행될지 미리 계산.
    실제 커맨드(post_to_wordpress.py 등)가 쓰는 select_candidates(limit=1)를 그대로 재사용해서
    미리보기가 실제 발행 대상과 어긋나지 않게 한다."""
    from ..blog_posting import enabled_accounts, select_candidates

    rows = []
    for account in enabled_accounts(platform):
        candidates = select_candidates(account, account.user.preference, limit=1)
        rows.append({
            'username': account.user.username,
            'site_url': account.site_url,
            'article_title': candidates[0].title if candidates else None,
        })
    return rows


def _newsletter_draft_preview():
    """generate_newsletter_draft.py와 완전히 같은 판정(READY 이슈 존재 여부 → 오늘 마감
    브리핑 존재 여부)을 실행 전에 미리 보여준다. timezone.localdate()는 명령어 쪽과 동일하게
    맞춘다 — 서버/장고 TIME_ZONE이 UTC라 KST 자정 근처엔 날짜가 하루 어긋날 수 있음."""
    from django.utils import timezone

    from ..models import AnalyzedArticle, NewsletterIssue

    pending = NewsletterIssue.objects.filter(status='READY').first()
    if pending:
        return {'skip_reason': f'이미 발송 대기 중인 초안이 있어 새로 만들지 않고 건너뜁니다: "{pending.subject}"'}

    today = timezone.localdate()
    briefing_exists = AnalyzedArticle.objects.filter(
        original_url=f"internal://featured-briefing/{today}/close",
    ).exists()
    return {
        'skip_reason': None if briefing_exists else f'오늘({today}) 마감 특징주 브리핑이 아직 없어 건너뜁니다.',
    }


def _newsletter_send_preview():
    from ..models import NewsletterIssue, NewsletterSubscriber

    return {
        'ready_issues': list(NewsletterIssue.objects.filter(status='READY').values('subject', 'article_count')),
        'subscriber_count': NewsletterSubscriber.objects.filter(is_active=True).count(),
    }


_PREVIEW_BUILDERS = {
    'post_wordpress': lambda: {'type': 'blog', 'rows': _blog_publish_preview('WORDPRESS')},
    'post_blogger': lambda: {'type': 'blog', 'rows': _blog_publish_preview('BLOGGER')},
    'newsletter_draft': lambda: {'type': 'newsletter_draft', **_newsletter_draft_preview()},
    'newsletter_send': lambda: {'type': 'newsletter_send', **_newsletter_send_preview()},
}


@staff_member_required
def pipeline_status_view(request):
    """collect_stock_data/run_stock_prediction처럼 무겁고 비정기적인 파이프라인 커맨드를
    버튼으로 백그라운드 실행하고, 마지막 실행 상태/로그를 확인하는 화면 (지금까지는 SSH로
    manage.py를 직접 실행해야만 볼 수 있었다). 발행/발송 계열 4개(post_wordpress/post_blogger/
    newsletter_draft/newsletter_send)는 실제로 회원 블로그·구독자에게 라이브로 나가는 동작이라,
    누르기 전에 "지금 누르면 무엇이 나갈지" _PREVIEW_BUILDERS로 미리 계산해 보여준다."""
    logs_dir = Path(settings.BASE_DIR) / 'logs'
    pipelines = []
    for key, conf in PIPELINE_COMMANDS.items():
        log_file = _latest_pipeline_log(logs_dir, conf['log_prefix'])
        last_run = None
        last_run_ago_minutes = None
        log_tail = ''
        status = 'unknown'
        if log_file is not None:
            last_run = datetime.fromtimestamp(log_file.stat().st_mtime, tz=KST)
            last_run_ago_minutes = int((datetime.now(KST) - last_run).total_seconds() // 60)
            log_tail = _tail_file(log_file)
            status = _log_status(log_tail)

        preview_builder = _PREVIEW_BUILDERS.get(key)

        pipelines.append({
            'key': key,
            'label': conf['label'],
            'note': conf['note'],
            'running': _pipeline_is_running(conf['command']),
            'last_run': last_run,
            'last_run_ago_minutes': last_run_ago_minutes,
            'log_tail': log_tail,
            'status': status,
            'preview': preview_builder() if preview_builder else None,
        })

    context = {
        **admin_site.each_context(request),
        'title': '⚙️ 파이프라인 수동 실행',
        'pipelines': pipelines,
    }
    return render(request, 'articles/pipeline_status.html', context)


@staff_member_required
@require_POST
def pipeline_trigger_view(request, key):
    conf = PIPELINE_COMMANDS.get(key)
    if conf is None:
        raise Http404

    if _pipeline_is_running(conf['command']):
        messages.warning(request, f"{conf['label']}은(는) 이미 실행 중입니다.")
        return redirect(reverse('pipeline_status'))

    logs_dir = Path(settings.BASE_DIR) / 'logs'
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S')
    log_path = logs_dir / f"{conf['log_prefix']}_{timestamp}.log"
    python_bin = Path(settings.BASE_DIR) / 'venv' / 'bin' / 'python'

    with open(log_path, 'ab') as log_fh:
        subprocess.Popen(
            [str(python_bin), 'manage.py', conf['command'], *conf['args']],
            cwd=str(settings.BASE_DIR),
            stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,  # gunicorn 요청/워커가 끝나도 백그라운드에서 계속 돌게
        )

    messages.success(request, f"{conf['label']} 실행을 시작했습니다. 로그: {log_path.name}")
    return redirect(reverse('pipeline_status'))


def _systemctl_is_active(name):
    try:
        result = subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True, timeout=5)
        return result.stdout.strip() or 'unknown'
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 'unknown'


def _cert_expiry(path):
    """openssl로 인증서 만료일(notAfter)만 읽어온다 — 인증서 내용 자체는 다루지 않는다."""
    try:
        result = subprocess.run(
            ['openssl', 'x509', '-enddate', '-noout', '-in', str(path)],
            capture_output=True, text=True, timeout=5,
        )
        line = result.stdout.strip()
        if not line.startswith('notAfter='):
            return None
        date_str = line[len('notAfter='):].strip()
        if date_str.endswith(' GMT'):
            date_str = date_str[:-4]
        return datetime.strptime(date_str, '%b %d %H:%M:%S %Y').replace(tzinfo=dt_timezone.utc)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None


@staff_member_required
def server_health_view(request):
    """nginx/gunicorn(nextfinup.service)/mysql/cron 서비스 상태, 디스크·메모리·로드, SSL 인증서
    만료일을 보여준다 — 지금까지는 이걸 확인하려면 SSH로 서버에 직접 들어가야 했다.
    배포 환경마다 달라질 수 있는 값(로컬 DB 여부, 인증서 경로)은 하드코딩하지 않고 실제 설정/파일
    존재 여부를 보고 판단해서, 이 코드가 다른 서버에서 돌아도 에러 없이 "해당 없음"으로 표시된다."""
    db_host = settings.DATABASES['default'].get('HOST', '')
    service_names = list(HEALTH_SYSTEMD_SERVICES)
    if db_host in ('127.0.0.1', 'localhost', ''):
        service_names.append('mysql')
    services = [{'name': name, 'active': _systemctl_is_active(name)} for name in service_names]

    disk = shutil.disk_usage('/')

    meminfo = {}
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                key, _, rest = line.partition(':')
                if rest:
                    meminfo[key.strip()] = int(rest.strip().split()[0])  # kB
    except OSError:
        pass
    mem_total_mb = meminfo.get('MemTotal', 0) // 1024
    mem_available_mb = meminfo.get('MemAvailable', 0) // 1024
    mem_used_mb = max(mem_total_mb - mem_available_mb, 0)

    load1, load5, load15 = os.getloadavg()

    cert_path = next((p for p in SSL_CERT_CANDIDATES if Path(p).exists()), None)
    cert_expiry = _cert_expiry(cert_path) if cert_path else None
    cert_days_left = (cert_expiry - datetime.now(dt_timezone.utc)).days if cert_expiry else None

    context = {
        **admin_site.each_context(request),
        'title': '🩺 서버 상태',
        'services': services,
        'cpu_count': os.cpu_count(),
        'load1': load1, 'load5': load5, 'load15': load15,
        'disk_total_gb': round(disk.total / 1024**3, 1),
        'disk_used_gb': round(disk.used / 1024**3, 1),
        'disk_used_pct': round(disk.used / disk.total * 100, 1) if disk.total else 0,
        'mem_total_mb': mem_total_mb,
        'mem_used_mb': mem_used_mb,
        'mem_used_pct': round(mem_used_mb / mem_total_mb * 100, 1) if mem_total_mb else 0,
        'cert_path': cert_path,
        'cert_expiry': cert_expiry,
        'cert_days_left': cert_days_left,
    }
    return render(request, 'articles/server_health.html', context)


# articles/content_calendar.py의 build_one_cycle()이 "generate_*_briefing/관리자 화면 등
# 내부용"이라고 이미 문서화해뒀던 화면 — 공개 페이지(health_content_calendar_view 등)는
# 회원이 그대로 퍼갈 수 있다는 우려로 요일별 예시 1개씩만 보여주지만(build_sample), 관리자는
# 실제 364일 전체가 언제 무엇으로 나갈지 알아야 하므로 여기서는 전체를 노출한다.
_CONTENT_CALENDAR_KINDS = {
    'health': {'category': 'HEALTH', 'title': '🩺 건강/의학 발행 캘린더 (전체 1년)'},
    'food': {'category': 'FOOD', 'title': '🍚 음식/영양 발행 캘린더 (전체 1년)'},
    'travel': {'category': 'TRAVEL', 'title': '✈️ 여행/관광 발행 캘린더 (전체 1년)'},
}


@staff_member_required
def content_calendar_admin_view(request, kind):
    conf = _CONTENT_CALENDAR_KINDS.get(kind)
    if conf is None:
        raise Http404

    from django.utils import timezone

    from articles import content_calendar

    context = {
        **admin_site.each_context(request),
        'title': conf['title'],
        'rows': content_calendar.build_one_cycle(conf['category']),
        'today': timezone.localdate(),
    }
    return render(request, 'articles/content_calendar_admin.html', context)


def _expand_hex_for_color_input(value):
    """<input type=color>는 #rrggbb 6자리만 받아들이므로(3자리 축약형/알파 채널 불가), 미리보기용
    value 속성만 6자리로 맞춰준다 — 실제 저장/제출되는 값은 옆의 텍스트 입력칸(color.value) 그대로."""
    v = value.lstrip('#')
    if len(v) == 3:
        v = ''.join(c * 2 for c in v)
    elif len(v) == 4:
        v = ''.join(c * 2 for c in v[:3])
    elif len(v) == 8:
        v = v[:6]
    return '#' + v[:6].ljust(6, '0')


@staff_member_required
def theme_settings_view(request):
    """사이트 색상(articles/static/articles/theme.css로 시작했던 CSS 변수)을 화면에서 바꾸는
    관리자 전용 화면. 저장하면 ThemeColor 테이블만 바뀌고, theme_css_view가 다음 요청부터
    바로 새 값으로 CSS를 내려주므로 재배포/재시작이 필요 없다."""
    colors = list(ThemeColor.objects.all())

    if request.method == 'POST':
        errors = []
        for color in colors:
            new_value = request.POST.get(f'color_{color.pk}', '').strip()
            if not new_value or not HEX_COLOR_RE.match(new_value):
                errors.append(f"{color.name}: 유효하지 않은 색상값 '{new_value}' (예: #0d47a1)")
                continue
            if new_value != color.value:
                color.value = new_value
                color.save(update_fields=['value', 'updated_at'])
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            messages.success(request, "테마 색상이 저장되었습니다. 새로고침하면 바로 반영됩니다.")
        return redirect('theme_settings')

    for color in colors:
        color.value_6 = _expand_hex_for_color_input(color.value)

    groups = []
    for group_code, group_label in ThemeColor.GROUP_CHOICES:
        group_colors = [c for c in colors if c.group == group_code]
        if group_colors:
            groups.append((group_label, group_colors))

    context = {
        **admin_site.each_context(request),
        'title': '🎨 테마 색상 설정',
        'groups': groups,
    }
    return render(request, 'articles/theme_settings.html', context)


def theme_css_view(request):
    """ThemeColor 테이블 값을 :root { --이름: 값; } CSS로 렌더링한다. 로그인 여부와 무관하게
    모든 페이지의 <head>에서 스타일시트로 로드하므로 인증을 요구하지 않는다.
    Cloudflare가 .css 확장자를 기준으로 origin의 Cache-Control과 무관하게 엣지에서 캐시해버려
    관리자가 색을 바꿔도 최대 4시간 동안 예전 색이 보이는 문제가 있었다 — no-store로 명시해
    (Cloudflare가 존중하는 한) 저장 즉시 반영되게 한다."""
    colors = ThemeColor.objects.all()
    lines = [f"    {c.name}: {c.value};" for c in colors]
    css = ":root {\n" + "\n".join(lines) + "\n}\n"
    response = HttpResponse(css, content_type='text/css')
    response['Cache-Control'] = 'no-store, must-revalidate'
    return response


def _describe_cron_schedule(minute, hour, day, month, weekday):
    if minute.startswith('*/') and hour == day == month == weekday == '*':
        return f"{minute[2:]}분마다"
    if minute.isdigit() and hour.isdigit() and day == month == weekday == '*':
        return f"매일 {int(hour):02d}:{int(minute):02d}"
    return f"{minute} {hour} {day} {month} {weekday}"


@staff_member_required
def cron_status_view(request):
    """서버에 등록된 crontab 내용을 그대로 읽어와 사람이 보기 좋게 표로 보여준다.
    (읽기 전용 — 여기서 크론을 추가/수정하지는 않음, 수정은 서버에서 crontab -e로 직접)"""
    jobs = []
    error = None
    try:
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=5)
        raw = result.stdout if result.returncode == 0 else ''
        if result.returncode != 0 and result.stderr.strip():
            error = result.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raw = ''
        error = str(e)

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        minute, hour, day, month, weekday, command = parts

        cmd_match = re.search(r'manage\.py\s+(\S+)', command)
        command_name = cmd_match.group(1) if cmd_match else command[:60]

        log_match = re.search(r'>>\s*(\S+)', command)
        log_path = log_match.group(1) if log_match else None

        last_run = None
        last_run_ago_minutes = None
        log_tail = ''
        status = 'unknown'  # unknown(로그 없음) / ok / error
        if log_path:
            try:
                log_file = Path(log_path)
                if log_file.exists():
                    last_run = datetime.fromtimestamp(log_file.stat().st_mtime, tz=KST)
                    last_run_ago_minutes = int((datetime.now(KST) - last_run).total_seconds() // 60)
                    log_tail = _tail_file(log_file)
                    status = _log_status(log_tail)
            except OSError:
                pass

        jobs.append({
            'schedule_human': _describe_cron_schedule(minute, hour, day, month, weekday),
            'schedule_raw': f"{minute} {hour} {day} {month} {weekday}",
            'command_name': command_name,
            'log_path': log_path,
            'last_run': last_run,
            'last_run_ago_minutes': last_run_ago_minutes,
            'log_tail': log_tail,
            'status': status,
        })

    context = {
        **admin_site.each_context(request),
        'title': '⏱ 크론 작업 현황',
        'jobs': jobs,
        'error': error,
    }
    return render(request, 'articles/cron_status.html', context)


def _integration_status(*values, placeholder=None):
    """값이 하나라도 비어있으면 'missing', 플레이스홀더 값 그대로면 'placeholder',
    다 채워져 있으면 'configured'. 실제 키/비밀번호 값은 반환하지 않는다 — 상태만 판정."""
    if any(not v for v in values):
        return 'missing'
    if placeholder and placeholder in values:
        return 'placeholder'
    return 'configured'


@staff_member_required
def integration_status_view(request):
    """.env로만 관리되는 외부 연동 키(소셜로그인/AI/KIS/SMTP 등)가 실제로 설정됐는지, 비어있는지,
    코드에 박힌 플레이스홀더 그대로인지 한눈에 보여준다 (읽기 전용, 값 자체는 절대 노출 안 함).
    OPENAI_API_KEY가 미설정/플레이스홀더면 article_ai.py/chatbot_client.py가 조용히
    '시뮬레이션 모드'로 폴백하는데, 그 상태를 확인할 화면이 지금까지 없었다."""
    db = settings.DATABASES['default']

    groups = [
        {
            'title': '데이터베이스',
            'items': [
                {
                    'name': 'MySQL 접속 정보',
                    'status': _integration_status(db.get('USER'), db.get('PASSWORD')),
                    'detail': f"{db.get('USER')}@{db.get('HOST')}:{db.get('PORT')}/{db.get('NAME')}",
                },
            ],
        },
        {
            'title': '소셜 로그인',
            'items': [
                {'name': '카카오', 'status': _integration_status(settings.KAKAO_CLIENT_ID, settings.KAKAO_CLIENT_SECRET)},
                {'name': '구글', 'status': _integration_status(settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET)},
                {'name': '네이버', 'status': _integration_status(settings.NAVER_CLIENT_ID, settings.NAVER_CLIENT_SECRET)},
            ],
        },
        {
            'title': 'AI',
            'items': [
                {
                    'name': 'OpenAI (챗봇·뉴스 AI 요약·블로그 초안·특징주 브리핑)',
                    'status': _integration_status(settings.OPENAI_API_KEY, placeholder='YOUR_OPENAI_API_KEY_HERE'),
                    'detail': '미설정/플레이스홀더면 챗봇·AI 요약·블로그 초안 생성이 시뮬레이션 모드로 동작합니다.',
                },
            ],
        },
        {
            'title': '한국투자증권(KIS)',
            'items': [
                {
                    'name': 'KIS Open API',
                    'status': _integration_status(settings.KIS_APP_KEY, settings.KIS_APP_SECRET),
                    'detail': '실시간 시세·등락률 순위·휴장일 조회에 사용됩니다.',
                },
            ],
        },
        {
            'title': '이메일(SMTP)',
            'items': [
                {
                    'name': 'Gmail SMTP',
                    'status': _integration_status(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD),
                    'detail': '회원가입 인증메일·뉴스레터 발송에 사용됩니다.',
                },
            ],
        },
    ]

    context = {
        **admin_site.each_context(request),
        'title': '🔌 외부 연동 상태',
        'groups': groups,
    }
    return render(request, 'articles/integration_status.html', context)


# article_ai.generate_thumbnail_image_bytes는 기사 썸네일용으로 모델/사이즈/품질이
# gpt-image-2·1536x1024·medium으로 고정돼 있다. 여기 화면은 그 3가지를 직접 골라가며 자유
# 프롬프트로 이미지를 생성해보는 별도 도구다 — OpenAI가 gpt-image 계열에서 실제 지원하는
# 값만 선택지로 제한해, 잘못된 조합으로 API를 호출해 요금만 나가는 걸 막는다.
IMAGE_GEN_MODELS = ['gpt-image-2', 'gpt-image-1']
IMAGE_GEN_SIZES = ['auto', '1024x1024', '1024x1536', '1536x1024']
IMAGE_GEN_QUALITIES = ['auto', 'low', 'medium', 'high']
IMAGE_GEN_SUBDIR = 'generated_images'  # MEDIA_ROOT 아래 저장 위치

# 나노바나나(Gemini 이미지 생성) 계열 — 기사 썸네일이 이미 쓰고 있는
# article_ai.GEMINI_IMAGE_MODEL과 같은 계열이라, 여기서 프롬프트를 시험해보고 그 결과를 그대로
# 썸네일 쪽에 반영할 수 있다. gpt-image와 호출 규격이 달라(size/quality 대신 aspect_ratio를
# config로 넘기고 품질 옵션 자체가 없다) _generate_with_nano_banana에서 따로 처리한다.
NANO_BANANA_MODELS = {
    'gemini-3.1-flash-image': '나노바나나 2',
    'gemini-3.1-flash-lite-image': '나노바나나 2 플래시 라이트',
    'gemini-3-pro-image': '나노바나나 프로',
    'gemini-2.5-flash-image': '나노바나나 1 (1세대, 비교용)',
}
ALL_IMAGE_GEN_MODELS = list(NANO_BANANA_MODELS) + IMAGE_GEN_MODELS

# 콤보박스 기본 선택값 — 기사 썸네일에도 쓰는 나노바나나 2를 기본으로 둔다(gpt-image 계열보다
# 결과물이 낫다고 판단해 2026-09-02 썸네일을 전환한 것과 같은 이유).
DEFAULT_IMAGE_GEN_MODEL = 'gemini-3.1-flash-image'

# 유튜브(16:9)/쇼츠(9:16)용 프리셋. 비율과 생성 크기(1K/2K)를 한 항목으로 묶어, 고르는
# 즉시 결과 픽셀이 확정되게 했다 — 그래서 프리셋을 고르면 '생성 크기' 콤보는 비활성화되고
# 여기 image_size가 대신 쓰인다.
#
# nano_label의 픽셀 값은 나노바나나가 실제로 뱉은 크기다(2026-09-03 실측: 1K 16:9 =
# 1376x768, 2K는 정확히 그 2배). 정확한 16:9(1365.33x768)가 아니라 0.8% 어긋나는데, 모델이
# 32픽셀 격자에 맞춰 내주기 때문이다. 9:16은 같은 값을 뒤집은 것.
#
# gpt-image 계열은 1K/2K 개념이 없고 픽셀 크기도 다르게 나온다 — API가 받는 size가
# IMAGE_GEN_SIZES 4개뿐이라 가장 가까운 비율(1536x1024=3:2, 1024x1536=2:3)로 생성한 뒤
# 가운데를 잘라(중앙 기준 크롭) output(풀HD)으로 리사이즈하는 우회로를 탄다. 그래서 화면에
# 보여줄 이름을 gpt_label로 따로 두고, 2K 프리셋은 gpt-image를 고르면 아예 감춘다(1K와
# 결과가 똑같아서). 크롭 후 크기(1536x864 / 864x1536)보다 output이 커 확대가 일어나므로
# 나노바나나만큼 선명하진 않다. 나노바나나는 이 크롭 경로를 타지 않는다(image_generator_view).
ASPECT_PRESETS = {
    'ratio_16_9_1k': {
        'nano_label': '1376x768 (16:9)(1K)', 'gpt_label': '16:9 (유튜브)',
        'ratio': '16:9', 'image_size': '1K',
        'api_size': '1536x1024', 'output': (1920, 1080),
    },
    'ratio_16_9_2k': {
        'nano_label': '2752x1536 (16:9)(2K)', 'gpt_label': '16:9 (유튜브)',
        'ratio': '16:9', 'image_size': '2K',
        'api_size': '1536x1024', 'output': (1920, 1080),
    },
    'ratio_9_16_1k': {
        'nano_label': '768x1376 (9:16)(1K)', 'gpt_label': '9:16 (쇼츠)',
        'ratio': '9:16', 'image_size': '1K',
        'api_size': '1024x1536', 'output': (1080, 1920),
    },
    'ratio_9_16_2k': {
        'nano_label': '1536x2752 (9:16)(2K)', 'gpt_label': '9:16 (쇼츠)',
        'ratio': '9:16', 'image_size': '2K',
        'api_size': '1024x1536', 'output': (1080, 1920),
    },
}
IMAGE_GEN_SIZE_CHOICES = IMAGE_GEN_SIZES + list(ASPECT_PRESETS.keys())

# 나노바나나 계열은 픽셀 해상도가 아니라 비율만 지정받으므로(실제 픽셀 크기는 생성 크기가
# 정한다), gpt-image용 픽셀 해상도 선택지를 그에 대응하는 비율로 옮겨준다. 16:9/9:16은
# ASPECT_PRESETS가 자기 'ratio'로 직접 들고 있어 여기 없고, 'auto'도 없다(gpt-image 전용
# 값이라 나노바나나를 고르면 콤보에서 숨겨지고, 서버에서도 1024x1024로 바꿔 받는다).
GEMINI_ASPECT_RATIOS = {
    '1024x1024': '1:1',
    '1024x1536': '2:3',
    '1536x1024': '3:2',
}

# 나노바나나 계열의 생성 크기(Gemini image_config.image_size). 비율과 달리 이건 실제 픽셀
# 크기를 좌우한다 — 1K는 긴 변 1024 안팎, 2K는 그 두 배 수준이라 16:9면 대략 2304x1296이
# 나온다. 유튜브 썸네일처럼 풀HD가 필요할 때 1K로 만들어 확대하면 뭉개지므로 2K로 뽑아
# 줄이는 편이 낫다(대신 출력 토큰이 늘어 비용도 함께 오른다 — 아래 단가표로 자동 반영).
# 모델마다 받는 값이 달라 목록을 따로 둔다: 지원하지 않는 값을 넘기면 API가 거부하므로,
# 선택값이 목록에 없으면 그 모델이 지원하는 가장 큰 값으로 낮춰 호출하고 화면에 알린다.
NANO_BANANA_IMAGE_SIZES = {
    'gemini-3.1-flash-image': ['1K', '2K'],
    'gemini-3.1-flash-lite-image': ['1K', '2K'],
    'gemini-3-pro-image': ['1K', '2K', '4K'],
    'gemini-2.5-flash-image': ['1K'],  # 1세대는 크기 지정 자체가 없다
}
IMAGE_GEN_RESOLUTIONS = ['1K', '2K', '4K']
DEFAULT_IMAGE_GEN_RESOLUTION = '1K'


# OpenAI 공식 가격표(2026-08 기준, $ per 1M tokens) — quality/size 조합별 고정 단가표 대신
# 매 호출의 실제 응답(response.usage)에 있는 입출력 토큰 수를 이 단가로 환산한다. auto로
# 호출하면 실제 해상도/품질을 호출 전엔 알 수 없어 고정表로는 추정이 부정확한데, usage 기반이면
# auto든 뭐든 결과와 무관하게 항상 정확하다(청구서와 반올림 수준 차이만 있을 수 있는 추정치).
IMAGE_MODEL_PRICING = {
    'gpt-image-1': {'text_input': 5.00, 'image_input': 10.00, 'output': 40.00},
    'gpt-image-2': {'text_input': 5.00, 'image_input': 8.00, 'output': 30.00},
}

# 나노바나나 계열 단가(Gemini API 공식 가격표, 2026-09 기준, $ per 1M tokens). Gemini는
# 입력 텍스트/이미지 단가가 같고, 이미지 출력은 candidates 토큰으로 계산된다
# (실측: gemini-3.1-flash-image 16:9 1장 ≈ $0.09).
GEMINI_IMAGE_PRICING = {
    'gemini-3.1-flash-image': {'input': 0.50, 'output': 60.00},
    'gemini-3.1-flash-lite-image': {'input': 0.25, 'output': 30.00},
    'gemini-3-pro-image': {'input': 2.00, 'output': 120.00},
    'gemini-2.5-flash-image': {'input': 0.30, 'output': 30.00},
}


def _usage_summary(cost_usd, input_tokens, output_tokens, **extra):
    """생성 함수들이 공통으로 돌려주는 과금 정보 묶음. 비용만 저장하면 나중에 "왜 이 장이
    비쌌나"를 되짚을 수 없어 근거가 된 토큰 수까지 함께 들고 다닌다."""
    return {'cost_usd': cost_usd, 'input_tokens': input_tokens, 'output_tokens': output_tokens, **extra}


def _compute_image_cost_usd(model, usage):
    """response.usage(입력 텍스트/이미지 토큰 + 출력 토큰)를 모델별 단가로 환산해
    (USD 비용, 입력 토큰, 출력 토큰)을 돌려준다. usage가 없거나 모델 단가를 모르면 비용은
    None(미상)이 되고, 그래도 토큰 수는 알 수 있으면 함께 반환한다 — 호출부는 저장은 하되
    화면에 "비용 미상"으로 표시한다."""
    if usage is None:
        return None, None, None
    d = usage.input_tokens_details
    input_tokens = d.text_tokens + d.image_tokens
    prices = IMAGE_MODEL_PRICING.get(model)
    if not prices:
        return None, input_tokens, usage.output_tokens
    cost = (
        (d.text_tokens / 1_000_000) * prices['text_input']
        + (d.image_tokens / 1_000_000) * prices['image_input']
        + (usage.output_tokens / 1_000_000) * prices['output']
    )
    return round(cost, 6), input_tokens, usage.output_tokens


def _compute_gemini_cost_usd(model, usage):
    """나노바나나 계열 응답의 usage_metadata(입력 토큰 + 출력 이미지 토큰)를 모델별 단가로
    환산해 (USD 비용, 입력 토큰, 출력 토큰)을 돌려준다. gpt-image 쪽 _compute_image_cost_usd와
    같은 취지지만, Gemini는 usage 구조가 다르고(입력 텍스트/이미지 단가가 하나로 통일) 필드명도
    달라 함수를 나눴다. 출력 토큰(candidates)에는 이미지 자체 말고 모델이 함께 낸 부수 토큰도
    섞여 있어, 같은 해상도라도 장마다 수백 토큰씩 달라진다 — 요금이 매번 조금씩 다른 이유다."""
    if usage is None:
        return None, None, None
    input_tokens = usage.prompt_token_count or 0
    output_tokens = usage.candidates_token_count or 0
    prices = GEMINI_IMAGE_PRICING.get(model)
    if not prices:
        return None, input_tokens, output_tokens
    cost = (input_tokens / 1_000_000) * prices['input'] + (output_tokens / 1_000_000) * prices['output']
    return round(cost, 6), input_tokens, output_tokens


def _generate_with_gpt_image(model, prompt, api_size, quality):
    """gpt-image 계열 호출 → (PNG 바이트, 과금 정보 묶음)."""
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.images.generate(model=model, prompt=prompt, size=api_size, quality=quality)
    cost_usd, input_tokens, output_tokens = _compute_image_cost_usd(model, response.usage)
    return base64.b64decode(response.data[0].b64_json), _usage_summary(cost_usd, input_tokens, output_tokens)


def _generate_with_nano_banana(model, prompt, size, image_size):
    """나노바나나(Gemini) 계열 호출 → (이미지 바이트, 과금 정보 묶음). 묶음에는 비용·토큰 수와
    함께 실제 적용된 생성 크기(image_size)가 들어간다.
    품질 파라미터는 이 계열에 없어 화면에서 고른 값과 무관하게 무시된다. 생성 크기는 모델이
    지원하지 않는 값이면 지원하는 최대값으로 낮춰서 호출하고, 그 값을 세 번째로 돌려줘 호출부가
    "요청과 다르게 나갔다"고 알릴 수 있게 한다. 안전 필터에 걸리면 예외 없이 이미지 파트가 없는
    응답이 오므로, 그 경우를 명시적으로 에러로 올려 호출부가 사용자에게 이유를 보여주게 한다."""
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    preset = ASPECT_PRESETS.get(size)
    aspect_ratio = preset['ratio'] if preset else GEMINI_ASPECT_RATIOS.get(size)
    supported = NANO_BANANA_IMAGE_SIZES.get(model) or [DEFAULT_IMAGE_GEN_RESOLUTION]
    effective_image_size = image_size if image_size in supported else supported[-1]
    config = genai_types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=genai_types.ImageConfig(aspect_ratio=aspect_ratio, image_size=effective_image_size),
    )
    response = client.models.generate_content(model=model, contents=prompt, config=config)
    cost_usd, input_tokens, output_tokens = _compute_gemini_cost_usd(model, response.usage_metadata)
    usage = _usage_summary(cost_usd, input_tokens, output_tokens, image_size=effective_image_size)
    for candidate in response.candidates or []:
        parts = (candidate.content.parts if candidate.content else None) or []
        for part in parts:
            if part.inline_data and part.inline_data.data:
                return part.inline_data.data, usage
    raise RuntimeError("모델이 이미지를 반환하지 않았습니다 (안전 필터에 걸렸을 수 있습니다).")


def _image_dimensions_label(image_bytes):
    """생성된 이미지의 실제 픽셀 크기를 읽어 "WxH" 문자열로 돌려준다. 화면에서 고른 해상도
    값으로는 알 수 없는 경우가 많아서다 — 나노바나나 계열은 비율만 지정받고 픽셀 크기는 모델이
    정하며, gpt-image의 'auto'도 마찬가지다. GeneratedImage.source_size에 이 값을 남겨두면
    나중에 모델이 요청한 비율을 지켰는지 기록만으로 판정할 수 있다."""
    from io import BytesIO
    from PIL import Image

    with Image.open(BytesIO(image_bytes)) as img:
        return f"{img.width}x{img.height}"


def _crop_to_aspect_and_resize(image_bytes, output_size):
    """이미지를 output_size와 같은 비율로 가운데를 잘라낸 뒤 정확히 그 해상도로 리사이즈."""
    from io import BytesIO
    from PIL import Image

    target_w, target_h = output_size
    with Image.open(BytesIO(image_bytes)) as img:
        img = img.convert('RGB')
        src_w, src_h = img.size
        target_ratio = target_w / target_h
        src_ratio = src_w / src_h

        if src_ratio > target_ratio:
            new_w = round(src_h * target_ratio)
            left = (src_w - new_w) // 2
            box = (left, 0, left + new_w, src_h)
        else:
            new_h = round(src_w / target_ratio)
            top = (src_h - new_h) // 2
            box = (0, top, src_w, top + new_h)

        cropped = img.crop(box).resize(output_size, Image.LANCZOS)
        buf = BytesIO()
        cropped.save(buf, format='PNG')
        return buf.getvalue()


@staff_member_required
def image_generator_view(request):
    """관리자가 모델(gpt-image-1/2)·해상도·품질을 직접 골라 프롬프트로 이미지를 생성해보는
    화면. 매번 실제 과금되는 API 호출이라 폼 재제출을 막기 위한 세션 보관 등은 하지 않고,
    결과를 그 요청의 응답에만 담아 보여준다(새로고침하면 결과가 사라지고 다시 눌러야 함 —
    의도된 동작). 생성된 PNG는 MEDIA_ROOT/generated_images/에 그대로 남아 URL로 재사용 가능.
    16:9/9:16(유튜브·쇼츠용)은 ASPECT_PRESETS로 API 생성 후 크롭/리사이즈해서 만든다."""
    result = None

    if request.method == 'POST':
        prompt = (request.POST.get('prompt') or '').strip()
        model = request.POST.get('model') or DEFAULT_IMAGE_GEN_MODEL
        size = request.POST.get('size') or 'auto'
        quality = request.POST.get('quality') or 'auto'
        image_size = request.POST.get('image_size') or DEFAULT_IMAGE_GEN_RESOLUTION
        aspect_preset = ASPECT_PRESETS.get(size)
        is_nano_banana = model in NANO_BANANA_MODELS
        if aspect_preset:
            # 프리셋은 비율과 생성 크기를 한 항목으로 묶은 것이라(1376x768(16:9)(1K) 등),
            # 화면에서 따로 고른 생성 크기보다 프리셋 쪽이 우선한다. 콤보도 프리셋을 고르면
            # 비활성화돼 값이 전송되지 않지만, 그걸 우회한 요청까지 여기서 맞춰준다.
            image_size = aspect_preset['image_size']
        if is_nano_banana and size == 'auto':
            # 'auto'는 gpt-image 계열에만 있는 값이다(모델이 알아서 해상도를 고름). 나노바나나
            # 쪽에선 aspect_ratio를 안 넘기는 것과 같아 결국 모델 기본값 1:1이 나오는데, 화면엔
            # 뭔가 알아서 맞춰줄 것처럼 보여 혼동만 준다 — 실제 동작 그대로 1:1로 못박는다.
            # (콤보에서도 나노바나나를 고르면 auto가 숨겨지므로, 여긴 그걸 우회한 요청 대비용.)
            size = '1024x1024'

        if is_nano_banana:
            key_missing = not settings.GEMINI_API_KEY or settings.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE"
            key_error = "GEMINI_API_KEY가 설정되어 있지 않습니다 (.env 확인)."
        else:
            key_missing = not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE"
            key_error = "OPENAI_API_KEY가 설정되어 있지 않습니다 (.env 확인)."

        if key_missing:
            messages.error(request, key_error)
        elif not prompt:
            messages.error(request, "프롬프트를 입력하세요.")
        elif (model not in ALL_IMAGE_GEN_MODELS or size not in IMAGE_GEN_SIZE_CHOICES
              or quality not in IMAGE_GEN_QUALITIES or image_size not in IMAGE_GEN_RESOLUTIONS):
            messages.error(request, "지원하지 않는 모델/해상도/품질 조합입니다.")
        else:
            try:
                if is_nano_banana:
                    image_bytes, usage = _generate_with_nano_banana(model, prompt, size, image_size)
                    if usage['image_size'] != image_size:
                        messages.warning(
                            request,
                            f"{model}은(는) {image_size} 생성을 지원하지 않아 "
                            f"{usage['image_size']}로 생성했습니다.",
                        )
                else:
                    api_size = aspect_preset['api_size'] if aspect_preset else size
                    image_bytes, usage = _generate_with_gpt_image(model, prompt, api_size, quality)
                cost_usd = usage['cost_usd']
                source_size = _image_dimensions_label(image_bytes)
                if aspect_preset and not is_nano_banana:
                    # gpt-image 계열만 이 경로를 탄다 — 16:9/9:16을 API에 직접 넘길 수 없어
                    # 근사 비율로 생성한 뒤 잘라내야 하기 때문이다. 나노바나나는 aspect_ratio를
                    # API가 그대로 지켜주므로 후처리가 얻는 게 없고, 오히려 모델이 낸 1K 원본을
                    # 풀HD로 다시 손대면서 화질만 깎는다(비율을 무시하고 1:1을 낸 모델의
                    # 경우엔 576x1024로 잘린 뒤 확대까지 돼 눈에 띄게 뭉갠다).
                    image_bytes = _crop_to_aspect_and_resize(image_bytes, aspect_preset['output'])

                images_dir = Path(settings.MEDIA_ROOT) / IMAGE_GEN_SUBDIR
                images_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{datetime.now(KST).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.png"
                (images_dir / filename).write_bytes(image_bytes)

                relative_path = f"{IMAGE_GEN_SUBDIR}/{filename}"
                if is_nano_banana:
                    size_label = source_size
                elif aspect_preset:
                    size_label = aspect_preset['gpt_label']
                else:
                    size_label = size
                quality_label = '-' if is_nano_banana else quality  # 나노바나나엔 품질 옵션이 없음
                generated = GeneratedImage.objects.create(
                    created_by=request.user, model_name=model,
                    size=size_label, source_size=source_size, quality=quality_label,
                    prompt=prompt, file_path=relative_path, cost_usd=cost_usd,
                    input_tokens=usage['input_tokens'], output_tokens=usage['output_tokens'],
                )

                result = {
                    'pk': generated.pk,
                    'url': f"{settings.MEDIA_URL}{relative_path}",
                    'prompt': prompt, 'model': model,
                    'size': size_label,
                    'source_size': source_size,
                    'quality': quality_label,
                    'cost_usd': cost_usd,
                    'input_tokens': usage['input_tokens'],
                    'output_tokens': usage['output_tokens'],
                }
                cost_msg = f" (약 ${cost_usd:.4f})" if cost_usd is not None else ""
                messages.success(request, f"이미지를 생성했습니다.{cost_msg}")
            except BadRequestError as e:
                messages.error(request, f"요청이 거부되었습니다 (모더레이션 등): {e}")
            except Exception as e:
                messages.error(request, f"이미지 생성 실패: {e}")

    submitted = request.POST if request.method == 'POST' else request.GET

    # 픽셀 해상도만 봐서는 비율이 한눈에 안 들어와서 "1024x1536(2:3)"처럼 같이 적어준다.
    # 비율 문자열은 GEMINI_ASPECT_RATIOS를 그대로 재사용한다(auto는 매핑이 없어 그대로 표시).
    # 프리셋은 모델에 따라 결과 픽셀이 달라 라벨 두 벌(나노바나나용/gpt-image용)을 함께 넘기고,
    # 어느 쪽을 보여줄지는 화면 JS가 고른 모델에 맞춰 바꾼다. is_preset_2k는 gpt-image에서
    # 2K 항목을 감추는 데 쓴다(1K 항목과 결과가 같아 보여줄 이유가 없다).
    def plain_size_option(s):
        return {
            'value': s,
            'nano_label': f"{s}({GEMINI_ASPECT_RATIOS[s]})" if s in GEMINI_ASPECT_RATIOS else s,
            'gpt_label': s,
            'is_preset_2k': False,
        }

    # 콤보 순서: auto → 유튜브/쇼츠 프리셋 → 정사각/세로/가로 픽셀 크기. 실제로 거의 항상
    # 고르는 건 프리셋(16:9/9:16)이라 위로 올리고, 픽셀 크기 3종은 아래로 내렸다.
    # ('auto'는 gpt-image 전용이라 나노바나나를 고르면 화면에서 숨겨진다.)
    size_options = (
        [plain_size_option(s) for s in IMAGE_GEN_SIZES if s == 'auto']
        + [
            {
                'value': k,
                'nano_label': v['nano_label'],
                'gpt_label': v['gpt_label'],
                'is_preset_2k': v['image_size'] == '2K',
            }
            for k, v in ASPECT_PRESETS.items()
        ]
        + [plain_size_option(s) for s in IMAGE_GEN_SIZES if s != 'auto']
    )
    model_options = (
        [(m, f"{m} ({label})") for m, label in NANO_BANANA_MODELS.items()]
        + [(m, m) for m in IMAGE_GEN_MODELS]
    )

    from ..models import ExchangeRateSnapshot

    totals = GeneratedImage.objects.aggregate(total_cost=Sum('cost_usd'), total_count=Count('id'))
    total_cost_usd = totals['total_cost'] or 0
    usd_krw = (
        ExchangeRateSnapshot.objects.filter(currency_code='USD').order_by('-date').values_list('deal_bas_r', flat=True).first()
    )

    context = {
        **admin_site.each_context(request),
        'title': '🖼 AI 이미지 생성',
        'model_options': model_options,
        'nano_banana_models': list(NANO_BANANA_MODELS),
        'size_options': size_options,
        'qualities': IMAGE_GEN_QUALITIES,
        'resolutions': IMAGE_GEN_RESOLUTIONS,
        'preset_image_sizes': {k: v['image_size'] for k, v in ASPECT_PRESETS.items()},
        'nano_banana_image_sizes': NANO_BANANA_IMAGE_SIZES,
        'result': result,
        'total_cost_usd': total_cost_usd,
        'total_count': totals['total_count'],
        'total_cost_krw': (total_cost_usd * usd_krw) if usd_krw else None,
        'recent_generations': GeneratedImage.objects.all()[:10],
        'media_url': settings.MEDIA_URL,
        # 시리즈 생성 탭. active_series_key가 있으면(방금 시작했거나 URL로 들어왔으면)
        # 화면이 그 키로 진행률 폴링을 시작한다.
        'ref_mode_labels': REF_MODE_LABELS,
        'default_ref_mode': DEFAULT_REF_MODE,
        'max_scenes': MAX_SCENES,
        'avg_cost_by_model': _avg_cost_by_model_and_size(),
        'active_series_key': request.GET.get('series', ''),
        'series_prompts': submitted.get('series_prompts', ''),
        # 생성 직후엔 방금 보낸 값을 그대로 다시 채워 넣고(연달아 조금씩 고쳐가며 뽑는 흐름),
        # GET으로 들어올 땐 쿼리스트링을 읽는다 — 목록 화면의 "이 프롬프트로 생성" 링크가
        # ?prompt=...&model=... 로 넘겨주기 때문이다.
        'form_values': {
            'prompt': submitted.get('prompt', ''),
            'model': submitted.get('model', DEFAULT_IMAGE_GEN_MODEL),
            'size': submitted.get('size', 'auto'),
            'quality': submitted.get('quality', 'auto'),
            'image_size': submitted.get('image_size', DEFAULT_IMAGE_GEN_RESOLUTION),
        },
    }
    return render(request, 'articles/image_generator.html', context)


IMAGE_GEN_LIST_PAGE_SIZE = 24


@staff_member_required
def generated_image_list_view(request):
    """image_generator_view는 폼 옆에 최근 10건만 미리보기로 보여주는데(한 화면에 다 넣기엔
    과함), 지금까지 생성한 전체 이미지를 훑어보고 다운로드하려면 여기 별도 갤러리 목록에서
    페이지를 넘겨가며 봐야 한다. 생성 자체는 이 화면에서 하지 않고 image_generator로 보낸다."""
    qs = GeneratedImage.objects.select_related('created_by').all()
    paginator = Paginator(qs, IMAGE_GEN_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    totals = GeneratedImage.objects.aggregate(total_cost=Sum('cost_usd'), total_count=Count('id'))

    context = {
        **admin_site.each_context(request),
        'title': '🖼 AI 이미지 생성 목록',
        'page_obj': page_obj,
        'total_cost_usd': totals['total_cost'] or 0,
        'total_count': totals['total_count'],
        'media_url': settings.MEDIA_URL,
    }
    return render(request, 'articles/generated_image_list.html', context)


# 커맨드가 첫 진행 상황을 쓰기까지 기다려주는 시간. 이 안에 사이드카가 안 생기면 기동 실패로
# 보고 화면에 '중단됨'을 띄운다(Django 기동 + import에 몇 초 걸릴 수 있어 넉넉히 잡는다).
STARTUP_GRACE_SECONDS = 60


def _avg_cost_by_model_and_size():
    """지금까지 생성한 기록에서 (모델, 생성 크기)별 장당 평균 비용을 뽑는다.

    시리즈 탭에서 "N장이면 대략 얼마" 를 제출 전에 보여주기 위한 것. 단가표로 계산하지 않고
    실측 평균을 쓰는 이유는, 비용을 좌우하는 출력 토큰 수가 같은 해상도에서도 장마다 달라
    단가표만으로는 배 가까이 빗나가기 때문이다(2026-09-05 실측: flash 2K가 장당 $0.117~0.131).

    생성 크기는 행에 따로 저장돼 있지 않아 원본 픽셀(source_size)의 긴 변으로 되짚는다 —
    단일 생성 행과 시리즈 행이 size 필드를 다르게 채우는 반면 source_size는 양쪽 다 같은
    "WxH" 형식이라 공통 키로 쓸 수 있다."""
    buckets = {}
    rows = (
        GeneratedImage.objects
        .filter(cost_usd__isnull=False)
        .exclude(source_size='')
        .values_list('model_name', 'source_size', 'cost_usd')
    )
    for model_name, source_size, cost in rows:
        try:
            long_edge = max(int(n) for n in source_size.lower().split('x'))
        except (ValueError, TypeError):
            continue
        bucket = '1K' if long_edge < 1600 else ('2K' if long_edge < 3200 else '4K')
        buckets.setdefault(model_name, {}).setdefault(bucket, []).append(float(cost))
    return {
        model_name: {b: round(sum(v) / len(v), 4) for b, v in per_size.items()}
        for model_name, per_size in buckets.items()
    }


@staff_member_required
@require_POST
def image_series_start_view(request):
    """시리즈 생성을 백그라운드로 띄운다 — 요청 안에서 직접 생성하지 않는다.

    5장 2K 한 묶음이 약 94초 걸리는데, 앞단 타임아웃이 Cloudflare 100초(무료 플랜이라 조정
    불가) / gunicorn 120초 / nginx 130초로 걸려 있어 동기 처리는 장 수를 조금만 늘려도 524로
    끊긴다. 그래서 pipeline_trigger_view와 똑같이 Popen(start_new_session=True)으로 떼어놓고
    즉시 리다이렉트한 뒤, 화면이 image_series_status_view를 폴링해 진행률을 그린다."""
    prompts_text = request.POST.get('series_prompts') or ''
    model = request.POST.get('model') or DEFAULT_IMAGE_GEN_MODEL
    size = request.POST.get('size') or '1024x1024'
    image_size = request.POST.get('image_size') or DEFAULT_IMAGE_GEN_RESOLUTION
    ref_mode = request.POST.get('ref_mode') or DEFAULT_REF_MODE

    prompts = split_prompts(prompts_text)
    aspect_preset = ASPECT_PRESETS.get(size)
    if aspect_preset:
        # 단일 생성과 같은 규칙 — 프리셋이 비율과 생성 크기를 함께 정하므로 콤보값보다 우선한다.
        image_size = aspect_preset['image_size']
    aspect = aspect_preset['ratio'] if aspect_preset else GEMINI_ASPECT_RATIOS.get(size, '1:1')

    error = None
    if model not in NANO_BANANA_MODELS:
        # 레퍼런스 이미지를 입력으로 받는 경로가 나노바나나 계열에만 있다. gpt-image로는
        # 장끼리 인물을 맞출 수단이 없어, 애초에 시리즈를 시작하지 못하게 막는다.
        error = "시리즈 생성은 나노바나나(Gemini) 계열 모델에서만 가능합니다."
    elif not prompts:
        error = "프롬프트에서 '#1' 형식의 장 구분자를 찾지 못했습니다. 각 장을 #1, #2 … 로 시작하세요."
    elif len(prompts) < 2:
        error = "시리즈 생성은 2장 이상일 때 의미가 있습니다. 1장이면 위 단일 생성을 쓰세요."
    elif len(prompts) > MAX_SCENES:
        error = f"한 번에 생성할 수 있는 장 수는 최대 {MAX_SCENES}장입니다 (입력한 장: {len(prompts)}장)."
    elif ref_mode not in REF_MODES:
        error = "지원하지 않는 레퍼런스 방식입니다."
    elif not settings.GEMINI_API_KEY or settings.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        error = "GEMINI_API_KEY가 설정되어 있지 않습니다 (.env 확인)."

    if error:
        messages.error(request, error)
        return redirect(reverse('image_generator'))

    series_key = uuid4().hex
    # 프롬프트를 파일로 넘긴다 — 인자로 붙이면 장문 5개가 명령행 길이 제한에 걸리고,
    # 나중에 "이 시리즈를 무슨 프롬프트로 돌렸나" 되짚을 기록도 남지 않는다.
    series_prompt_path(series_key).write_text(prompts_text, encoding='utf-8')

    log_path = Path(settings.BASE_DIR) / 'logs' / f'image_series_{series_key}.log'
    python_bin = Path(settings.BASE_DIR) / 'venv' / 'bin' / 'python'
    with open(log_path, 'ab') as log_fh:
        subprocess.Popen(
            [
                str(python_bin), 'manage.py', 'generate_image_series',
                '--file', str(series_prompt_path(series_key)),
                '--model', model, '--aspect', aspect, '--image-size', image_size,
                '--ref-mode', ref_mode, '--series-key', series_key,
            ],
            cwd=str(settings.BASE_DIR),
            stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,  # gunicorn 요청/워커가 끝나도 백그라운드에서 계속 돌게
        )

    messages.success(request, f"{len(prompts)}장 시리즈 생성을 시작했습니다. 완성되는 대로 아래에 표시됩니다.")
    return redirect(f"{reverse('image_generator')}?series={series_key}")


@staff_member_required
def image_series_status_view(request):
    """진행 중인 시리즈의 상태를 JSON으로 — 화면이 몇 초 간격으로 폴링한다.

    완성된 장은 GeneratedImage 행에서(=실제로 저장까지 끝난 것만) 읽고, 전체 장 수·실패·종료
    여부는 커맨드가 쓰는 사이드카에서 읽는다. 사이드카가 finished를 못 남기고 프로세스가
    죽는 경우(OOM 등)를 위해 pid 생존까지 확인해, 화면이 영원히 '생성 중'으로 남지 않게 한다."""
    series_key = request.GET.get('key') or ''
    if not re.fullmatch(r'[0-9a-f]{32}', series_key):
        raise Http404

    # 프롬프트 파일은 Popen 직전에 쓰므로, 없으면 애초에 시작된 적 없는 키다(오래된 북마크 등).
    # 이걸 구분하지 않으면 화면이 'starting'만 받으며 영원히 폴링한다.
    prompt_path = series_prompt_path(series_key)
    if not prompt_path.exists():
        raise Http404

    progress = read_progress(series_key)
    if progress is None:
        # 커맨드가 아직 첫 사이드카를 쓰기 전(기동 직후 1~2초)일 수 있으므로 보통은 정상이다.
        # 다만 그 상태가 STARTUP_GRACE_SECONDS를 넘겼다면 프로세스 기동 자체가 실패한 것으로 본다.
        age = time.time() - prompt_path.stat().st_mtime
        state = 'starting' if age < STARTUP_GRACE_SECONDS else 'aborted'
        return JsonResponse({'state': state, 'total': 0, 'done': 0, 'images': []})

    images = [
        {
            'pk': g.pk,
            'index': g.series_index,
            'url': f"{settings.MEDIA_URL}{g.file_path}",
            'jpg_url': reverse('generated_image_jpg', args=[g.pk]),
            'source_size': g.source_size,
            'cost_usd': float(g.cost_usd) if g.cost_usd is not None else None,
            'prompt': g.prompt,
        }
        for g in GeneratedImage.objects.filter(series_key=series_key).order_by('series_index')
    ]

    finished = bool(progress.get('finished'))
    state = 'running'
    if finished:
        state = 'finished'
    elif not _pid_alive(progress.get('pid')):
        # 사이드카는 아직 진행 중이라는데 프로세스가 없다 = 비정상 종료.
        state = 'aborted'

    return JsonResponse({
        'state': state,
        'total': progress.get('total') or 0,
        'done': len(images),
        'failed': progress.get('failed') or [],
        'error': progress.get('error'),
        'last_error': progress.get('last_error'),
        'total_cost': progress.get('total_cost'),
        'images': images,
    })


def _pid_alive(pid):
    """해당 pid의 프로세스가 아직 살아있는지. 신호를 보내지 않는 확인용 kill(pid, 0)."""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


@staff_member_required
@require_POST
def generated_image_delete_view(request, pk):
    """목록 화면에서 이미지 1건을 지운다 — 비용 기록(GeneratedImage 행)과 실제 파일
    (MEDIA_ROOT/generated_images/*.png) 둘 다 지운다. 파일이 이미 없어도(수동으로 지워졌거나
    등) 에러 없이 DB 행만 정리한다. 누적 비용 합계는 지운 행만큼 자동으로 줄어든다 — 과거에
    쓴 돈 자체가 사라지는 건 아니지만, "지금 남아있는 이미지 기준" 합계로 보는 게 이 화면의
    목적에 맞는다고 판단."""
    image = get_object_or_404(GeneratedImage, pk=pk)
    file_path = Path(settings.MEDIA_ROOT) / image.file_path
    file_path.unlink(missing_ok=True)
    image.delete()
    messages.success(request, "이미지를 삭제했습니다.")

    page = request.POST.get('page')
    url = reverse('generated_image_list')
    if page:
        url = f"{url}?page={page}"
    return redirect(url)


# JPG 변환 품질 — 90이면 썸네일 용도로 눈에 띄는 손실 없이 PNG의 1/3~1/4 크기가 된다.
JPG_DOWNLOAD_QUALITY = 90


@staff_member_required
def generated_image_jpg_view(request, pk):
    """저장된 PNG를 그때그때 JPG로 변환해 내려준다. 2K로 뽑으면 PNG가 3MB 가까이 나오는데
    유튜브 썸네일 업로드 한도가 2MB라 그대로는 못 올리기 때문이다 — 해상도는 그대로 두고
    포맷만 바꿔 용량을 줄인다. 변환본을 디스크에 남기지 않는 건 원본 1장당 파일이 2개로
    늘어나는 걸 피하려는 것이고, 덕분에 이 기능이 생기기 전에 만든 이미지에도 그대로 쓸 수
    있다. 투명 PNG는 JPG에 알파 채널이 없어 흰 배경 위에 합성한다."""
    from io import BytesIO
    from PIL import Image

    image = get_object_or_404(GeneratedImage, pk=pk)
    source_path = Path(settings.MEDIA_ROOT) / image.file_path
    if not source_path.exists():
        raise Http404("원본 이미지 파일이 없습니다.")

    with Image.open(source_path) as img:
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGBA')
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            img = img.convert('RGB')
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=JPG_DOWNLOAD_QUALITY, optimize=True)

    filename = Path(image.file_path).with_suffix('.jpg').name
    response = HttpResponse(buf.getvalue(), content_type='image/jpeg')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


OVERVIEW_DAYS = 60  # 회원가입/뉴스/발행/상담 4개 차트가 공유하는 조회 기간


def _daily_counts(queryset, date_field, days=OVERVIEW_DAYS, group_field=None):
    """queryset을 date_field 기준 날짜별 건수로 집계. group_field를 주면(예: 'blog_account__platform')
    그 값별로 나눠(스택형 차트용) {날짜: {그룹값: 건수}} 형태까지 같이 만든다. 4개 차트 전부
    "특정 시점 이후 하루 단위 카운트" 패턴이 같아서, 각 뷰마다 집계 로직을 따로 안 짜도 되게
    여기 한 곳에 모았다 — _prediction_accuracy_history와 같은 이유(GROUP BY 재사용)."""
    from django.db.models.functions import TruncDate

    since = datetime.now(KST) - timedelta(days=days)
    qs = queryset.filter(**{f'{date_field}__gte': since}).annotate(_day=TruncDate(date_field))

    if group_field:
        rows = qs.values('_day', group_field).annotate(n=Count('id')).order_by('_day')
        by_day = {}
        groups = set()
        for row in rows:
            day = row['_day'].strftime('%Y-%m-%d')
            g = row[group_field] or '기타'
            groups.add(g)
            by_day.setdefault(day, {})[g] = row['n']
        return by_day, sorted(groups)

    rows = qs.values('_day').annotate(n=Count('id')).order_by('_day')
    return {row['_day'].strftime('%Y-%m-%d'): row['n'] for row in rows}


def _date_range_labels(days=OVERVIEW_DAYS):
    today = datetime.now(KST).date()
    return [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days - 1, -1, -1)]


@staff_member_required
def operations_overview_view(request):
    """cron/pipeline/health/integrations는 전부 '지금 상태'만 보여주는데, 한 달 넘게 쌓인 실제
    운영 데이터(가입·수집·발행·상담)는 로그/테이블로만 존재하고 추이를 볼 화면이 없었다.
    4개 지표를 하루 단위로 집계해 한 화면에서 라인/스택 바 차트로 보여준다."""
    from django.contrib.auth.models import User
    from ..models import AnalyzedArticle, ConsultRequest, PostedArticle

    labels = _date_range_labels()

    signups = _daily_counts(User.objects.all(), 'date_joined')
    collected = _daily_counts(AnalyzedArticle.objects.all(), 'scraped_at')
    summarized = _daily_counts(AnalyzedArticle.objects.exclude(ai_summarized_at__isnull=True), 'ai_summarized_at')
    posted_by_platform, platform_groups = _daily_counts(
        PostedArticle.objects.all(), 'posted_at', group_field='blog_account__platform',
    )
    consult_by_product, product_groups = _daily_counts(
        ConsultRequest.objects.all(), 'created_at', group_field='product',
    )

    context = {
        **admin_site.each_context(request),
        'title': '📊 운영 현황',
        'overview_days': OVERVIEW_DAYS,
        'labels_json': json.dumps(labels),
        'signups_json': json.dumps([signups.get(d, 0) for d in labels]),
        'collected_json': json.dumps([collected.get(d, 0) for d in labels]),
        'summarized_json': json.dumps([summarized.get(d, 0) for d in labels]),
        'platform_groups_json': json.dumps(platform_groups),
        'posted_by_platform_json': json.dumps(
            {g: [posted_by_platform.get(d, {}).get(g, 0) for d in labels] for g in platform_groups}
        ),
        'product_groups_json': json.dumps(product_groups),
        'consult_by_product_json': json.dumps(
            {g: [consult_by_product.get(d, {}).get(g, 0) for d in labels] for g in product_groups}
        ),
        'total_signups': User.objects.count(),
        'total_collected': AnalyzedArticle.objects.count(),
        'total_posted': PostedArticle.objects.count(),
        'total_consults': ConsultRequest.objects.count(),
        'accuracy_history': _prediction_accuracy_history(),
        'min_stocks_for_trend': MIN_STOCKS_FOR_TREND,
    }
    return render(request, 'articles/operations_overview.html', context)


@staff_member_required
def ai_performance_admin_view(request):
    """공개 트랙레코드 페이지(/performance/, ai_performance_view)와 같은 데이터를 admin
    레이아웃 안에서 보는 화면. 공개 페이지는 로그인 없이 누구나 보므로 그대로 두고, staff가
    admin 좌측 메뉴에서 바로 확인할 수 있도록 여기 별도로 추가했다 — build_ai_performance_context()로
    데이터 계산 로직만 공유하고 템플릿/레이아웃은 분리."""
    context = {
        **admin_site.each_context(request),
        'title': '🎯 AI 예측 성과',
        **build_ai_performance_context(),
    }
    return render(request, 'articles/ai_performance_admin.html', context)


@staff_member_required
def financial_consult_sheet_view(request):
    # FC/PB가 상담 중 사용하는 내부 전용 종합 재무상담 시트.
    # "저장" 버튼을 누르면 financial_consult_sheet_save_view로 전체 입력값을 JSON으로 전송해
    # FinancialConsultSheet에 기록하고, 별도로 인쇄/PDF 저장도 가능하다.
    # ConsultRequest 관리자 목록의 이름 링크(articles/admin.py name_with_sheet_link)가
    # name/phone/apply_date/channel을 쿼리스트링으로 넘겨, 그 리드의 기본정보를 미리 채워준다.
    #
    # ?load=<FinancialConsultSheet id>가 있으면 그 시트에 저장된 전체 데이터를 폼에 채워
    # 넣는다(재상담 시 이전 입력 이어보기용). 이때 저장 버튼은 새로 만들지 않고 이 레코드를
    # 업데이트하도록, 로드된 시트의 id를 JS에 함께 넘긴다.
    context = {
        'site_title': 'NextFinUp - 종합 재무상담 시트',
        'prefill_name': request.GET.get('name', ''),
        'prefill_phone': request.GET.get('phone', ''),
        'prefill_apply_date': request.GET.get('apply_date', ''),
        'prefill_channel': request.GET.get('channel', ''),
        'load_sheet_id': '',
        'load_sheet_data_json': 'null',
    }

    load_id = request.GET.get('load')
    if load_id:
        sheet = FinancialConsultSheet.objects.filter(pk=load_id).first()
        if sheet is not None:
            context['load_sheet_id'] = sheet.id
            # </script>로 HTML 파서가 태그를 조기 종료하지 않도록 '<'만 이스케이프해 안전하게 삽입
            context['load_sheet_data_json'] = json.dumps(sheet.data or {}).replace('<', '\\u003c')

    return render(request, 'articles/financial_consult_sheet.html', context)


@staff_member_required
def financial_consult_sheet_search_view(request):
    """상단 "불러오기" 검색창이 호출하는 AJAX 엔드포인트. 고객명/전화번호로 이전 시트를
    찾아 최근 20건까지 반환한다 (값 자체는 여기서 넘기지 않고 목록만 — 실제 데이터는
    선택 후 financial_consult_sheet_view의 ?load=<id>로 다시 로드할 때 채워진다)."""
    q = (request.GET.get('q') or '').strip()
    qs = FinancialConsultSheet.objects.all()
    if q:
        qs = qs.filter(Q(customer_name__icontains=q) | Q(customer_phone__icontains=q))
    results = [
        {
            'id': s.id,
            'customer_name': s.customer_name,
            'customer_phone': s.customer_phone,
            'consultant_name': s.consultant_name,
            'consult_date': s.consult_date.isoformat() if s.consult_date else None,
            'created_at': s.created_at.strftime('%Y-%m-%d %H:%M'),
        }
        for s in qs.order_by('-created_at')[:20]
    ]
    return JsonResponse({'results': results})


@staff_member_required
@require_POST
def financial_consult_sheet_save_view(request):
    """financial_consult_sheet.html에서 저장 버튼 클릭 시 fetch로 전송하는 전체 시트 데이터를
    FinancialConsultSheet에 저장한다. 목록/검색용 핵심 컬럼(고객명·연락처·상담자·상담일자)만
    최상위로 뽑고, 나머지 세부 항목은 원본 그대로 JSONField에 보관한다.
    payload에 sheet_id가 있으면(불러온 시트를 이어 작성/수정) 새로 만들지 않고 그 레코드를
    업데이트한다 — 없으면(새 상담) 새 레코드를 만든다."""
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({'ok': False, 'error': 'invalid_payload'}, status=400)

    sheet_id = payload.pop('sheet_id', None)
    meta = payload.get('meta') or {}
    s1 = payload.get('s1') or {}

    consult_date = meta.get('consult_date') or None
    if consult_date:
        try:
            datetime.strptime(consult_date, '%Y-%m-%d')
        except ValueError:
            consult_date = None

    fields = dict(
        customer_name=(s1.get('name') or '')[:50],
        customer_phone=(s1.get('phone') or '')[:20],
        consultant_name=(meta.get('consultant') or '')[:50],
        consult_date=consult_date,
        data=payload,
    )

    if sheet_id:
        sheet = FinancialConsultSheet.objects.filter(pk=sheet_id).first()
        if sheet is None:
            return JsonResponse({'ok': False, 'error': 'not_found'}, status=404)
        for key, value in fields.items():
            setattr(sheet, key, value)
        sheet.save()
    else:
        sheet = FinancialConsultSheet.objects.create(created_by=request.user, **fields)

    return JsonResponse({'ok': True, 'id': sheet.id})


# nginx의 client_max_body_size(현재 20MB)와 맞춰둔다 — 이보다 큰 요청은 Django까지 오지도
# 못하고 nginx가 413으로 끊어버리므로, 앱 쪽 한도를 그보다 크게 잡아봐야 의미가 없다. 더 큰
# 파일이 필요해지면 nginx 설정도 같이 올려야 한다.
ADMIN_UPLOAD_MAX_BYTES = 20 * 1024 * 1024


@staff_member_required
def file_upload_view(request):
    """FTP 계정 없이 브라우저에서 서버로 파일을 옮기기 위한 관리자 전용 도구. 저장 위치는
    STATIC_ROOT/MEDIA_ROOT와 분리된 settings.ADMIN_UPLOAD_ROOT — nginx가 그 경로를 직접
    서빙하도록 설정돼 있지 않아, 올린 파일은 공개 URL 없이 file_upload_download_view를 통해서만
    (스태프 로그인 상태로) 내려받을 수 있다. 여러 파일을 한 번에 올릴 수 있고, 파일명은
    충돌·경로 조작을 막기 위해 서버에 저장할 때 uuid를 붙여 새로 만든다."""
    upload_root = Path(settings.ADMIN_UPLOAD_ROOT)
    upload_root.mkdir(parents=True, exist_ok=True)

    if request.method == 'POST':
        files = request.FILES.getlist('files')
        note = (request.POST.get('note') or '').strip()
        if not files:
            messages.error(request, "업로드할 파일을 선택해주세요.")
        else:
            saved, skipped = 0, []
            for f in files:
                if f.size > ADMIN_UPLOAD_MAX_BYTES:
                    skipped.append(f"{f.name} (파일당 최대 {ADMIN_UPLOAD_MAX_BYTES // (1024*1024)}MB, {f.size / (1024*1024):.1f}MB)")
                    continue
                # get_valid_filename이 경로 구분자·위험 문자를 제거해주지만(경로 조작 방지),
                # 그래도 원본 파일명을 그대로 저장 파일명으로 쓰지 않는다 — 동시에 같은
                # 이름으로 여러 번 올려도 서로 덮어쓰지 않도록 uuid 접두를 붙인 별도 이름으로
                # 저장하고, 원본 파일명은 DB에만 보관해 화면 표시/다운로드 시 복원한다.
                # stored_filename의 max_length(255)를 넘지 않도록 uuid 접두 뒤 남는 길이만큼만
                # 자른다 — 원본 파일명이 아주 길면 DB INSERT가 그냥 에러로 죽을 수 있어서다.
                safe_name = get_valid_filename(f.name) or 'file'
                prefix = f"{uuid4().hex}_"
                safe_name = safe_name[-(255 - len(prefix)):]
                stored_name = prefix + safe_name
                dest = upload_root / stored_name
                with dest.open('wb') as out:
                    for chunk in f.chunks():
                        out.write(chunk)
                AdminUpload.objects.create(
                    uploaded_by=request.user, original_filename=f.name[:255],
                    stored_filename=stored_name, size_bytes=f.size, note=note,
                )
                saved += 1

            if saved:
                messages.success(request, f"{saved}개 파일을 업로드했습니다.")
            for reason in skipped:
                messages.error(request, f"건너뜀: {reason}")
        return redirect('file_upload')

    uploads = AdminUpload.objects.select_related('uploaded_by').all()
    total_bytes = uploads.aggregate(total=Sum('size_bytes'))['total'] or 0

    context = {
        **admin_site.each_context(request),
        'title': '📁 파일 업로드',
        'uploads': uploads,
        'total_bytes': total_bytes,
        'max_mb': ADMIN_UPLOAD_MAX_BYTES // (1024 * 1024),
    }
    return render(request, 'articles/file_upload.html', context)


@staff_member_required
def file_upload_download_view(request, pk):
    """업로드된 파일을 스태프 로그인 상태에서만 내려받게 한다. Content-Disposition을
    attachment로 강제해(브라우저가 inline으로 렌더링하지 않게) 업로드된 HTML/SVG 등이
    관리자 세션에서 그대로 실행되는 저장형 XSS 경로를 막는다."""
    upload = get_object_or_404(AdminUpload, pk=pk)
    file_path = Path(settings.ADMIN_UPLOAD_ROOT) / upload.stored_filename
    if not file_path.exists():
        raise Http404("파일을 찾을 수 없습니다 (서버에서 삭제되었을 수 있습니다).")

    content_type = mimetypes.guess_type(upload.original_filename)[0] or 'application/octet-stream'
    response = FileResponse(file_path.open('rb'), content_type=content_type, as_attachment=True, filename=upload.original_filename)
    return response


@staff_member_required
@require_POST
def file_upload_delete_view(request, pk):
    upload = get_object_or_404(AdminUpload, pk=pk)
    (Path(settings.ADMIN_UPLOAD_ROOT) / upload.stored_filename).unlink(missing_ok=True)
    upload.delete()
    messages.success(request, "파일을 삭제했습니다.")
    return redirect('file_upload')


@staff_member_required
def publish_for_member_view(request):
    """관리자가 특정 회원을 골라, 그 회원 명의의 블로그 계정에 AI 요약이 끝난 기사를 대신
    수동 발행한다. 회원이 자동 포스팅을 켜두지 않았거나 직접 발행하기 어려운 상황(문의 대응
    등)에 관리자가 대신 처리할 수 있게 하는 화면 — 발행 자체는 news_board의 '선택 포스팅'과
    똑같이 blog_posting.publish_article을 그대로 쓰므로 중복 발행 방지/PostedArticle 기록은
    거기서 그대로 재사용된다. 발행 한도는 관리자가 아니라 '대상 회원' 기준으로 적용한다 —
    실제로 그 회원 계정에 쌓이는 발행이기 때문."""
    target_user = None
    user_id = request.POST.get('user') or request.GET.get('user')
    if user_id:
        target_user = User.objects.filter(pk=user_id).first()

    q = request.GET.get('q', '').strip()
    user_results = []
    if q:
        user_results = list(
            User.objects.filter(
                Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q)
            ).select_related('preference').order_by('username')[:20]
        )

    if request.method == 'POST':
        if not target_user:
            messages.error(request, "발행 대상 회원을 먼저 선택해주세요.")
            return redirect('publish_for_member')

        redirect_url = f"{reverse('publish_for_member')}?user={target_user.pk}"

        account_ids = request.POST.getlist('account_ids')
        accounts = list(BlogPostingAccount.objects.filter(pk__in=account_ids, user=target_user))
        connected_accounts = [a for a in accounts if a.is_connected()]
        for account in accounts:
            if not account.is_connected():
                messages.error(request, f"{account.get_platform_display()} 계정이 아직 연동되지 않아 건너뛰었습니다.")

        article_ids = request.POST.getlist('article_ids')
        if not connected_accounts or not article_ids:
            messages.warning(request, "발행할 계정과 기사를 하나 이상 선택해주세요.")
            return redirect(redirect_url)

        stats = blog_posting.posting_stats(target_user)
        remaining = stats['remaining']
        if remaining is not None:
            if remaining <= 0:
                messages.error(request, f"{target_user.username} 회원은 {limit_label(stats)} 등급 기준 오늘 발행 가능 건수를 모두 사용했습니다.")
                return redirect(redirect_url)
            planned_total = len(article_ids) * len(connected_accounts)
            if planned_total > remaining:
                max_articles = max(1, remaining // len(connected_accounts))
                messages.warning(request, f"{target_user.username} 회원의 남은 발행 가능 건수({remaining}건)에 맞춰 {len(connected_accounts)}개 계정 × {max_articles}건만 발행합니다.")
                article_ids = article_ids[:max_articles]

        articles = AnalyzedArticle.objects.filter(pk__in=article_ids)
        not_summarized_count = articles.filter(ai_generated=False).count()
        if not_summarized_count:
            messages.warning(request, f"AI 요약이 안 된 기사 {not_summarized_count}건은 건너뛰었습니다.")
            articles = articles.filter(ai_generated=True)

        success_count = 0
        for account in connected_accounts:
            for article in articles:
                ok, result = blog_posting.publish_article(account, article)
                if ok:
                    success_count += 1
                else:
                    messages.error(request, f"[{account.get_platform_display()} · {article.title[:30]}] {result}")

        if success_count:
            messages.success(request, f"{target_user.username} 회원의 블로그에 {success_count}건 발행했습니다.")

        return redirect(redirect_url)

    accounts = []
    stats = None
    articles_page = None
    posted_article_ids = set()
    article_q = request.GET.get('aq', '').strip()
    category = request.GET.get('category', '')

    if target_user:
        accounts = list(BlogPostingAccount.objects.filter(user=target_user).order_by('platform'))
        stats = blog_posting.posting_stats(target_user)

        article_list = AnalyzedArticle.objects.filter(ai_generated=True).order_by('-scraped_at')
        if article_q:
            article_list = article_list.filter(Q(title__icontains=article_q) | Q(ai_title__icontains=article_q))
        if category:
            article_list = article_list.filter(content_category=category)

        paginator = Paginator(article_list, 20)
        articles_page = paginator.get_page(request.GET.get('page'))

        posted_article_ids = set(
            PostedArticle.objects.filter(
                blog_account__user=target_user, article__in=list(articles_page)
            ).values_list('article_id', flat=True)
        )

    context = {
        **admin_site.each_context(request),
        'title': '📤 회원 대신 블로그 발행',
        'q': q,
        'user_results': user_results,
        'target_user': target_user,
        'accounts': accounts,
        'stats': stats,
        'limit_label': limit_label(stats) if stats else '',
        'articles_page': articles_page,
        'posted_article_ids': posted_article_ids,
        'category_choices': AnalyzedArticle.CATEGORY_CHOICES,
        'article_q': article_q,
        'category': category,
    }
    return render(request, 'articles/publish_for_member.html', context)


# ==========================================
# 네이버 블로그 이관 — 수집된 원문 목록/상세 (스태프 전용, 읽기 화면)
# ==========================================
NAVER_MIGRATION_PAGE_SIZE = 20


@staff_member_required
def naver_migration_list_view(request):
    """collect_naver_blog_posts가 적재한 NaverBlogPost 목록.

    이 화면은 조회 전용이다 — 수집은 커맨드(또는 크론)로만 돌린다. 화면에서 스크래핑을
    걸 수 있게 하면 글 수십 건 × 이미지 수백 장을 받는 동안 요청이 살아 있어야 하는데,
    Cloudflare 100초 상한에 그대로 걸린다(image_generator가 백그라운드 Popen + 폴링을
    쓰는 것과 같은 이유). 대신 상단에 그때그때 복사해 쓸 커맨드를 적어 둔다."""
    qs = NaverBlogPost.objects.select_related('owner').prefetch_related('migrations__blog_account')

    blog_id = request.GET.get('blog_id', '').strip()
    status = request.GET.get('status', '').strip()
    q = request.GET.get('q', '').strip()
    if blog_id:
        qs = qs.filter(blog_id=blog_id)
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(excerpt__icontains=q) | Q(log_no__icontains=q))

    paginator = Paginator(qs, NAVER_MIGRATION_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    all_posts = NaverBlogPost.objects.all()
    stats = {
        'total': all_posts.count(),
        'scraped': all_posts.filter(status='SCRAPED').count(),
        'failed': all_posts.filter(status='FAILED').count(),
        'listed': all_posts.filter(status='LISTED').count(),
        # 어느 대상 블로그로든 한 번이라도 발행 성공한 원문 수(중복 제거).
        'migrated': all_posts.filter(migrations__status='SUCCESS').distinct().count(),
    }

    context = {
        **admin_site.each_context(request),
        'title': '📥 네이버 블로그 이관 — 원문 목록',
        'page_obj': page_obj,
        'stats': stats,
        'blog_ids': list(
            NaverBlogPost.objects.values_list('blog_id', flat=True).distinct().order_by('blog_id')
        ),
        'status_choices': NaverBlogPost.STATUS_CHOICES,
        'blog_id': blog_id,
        'status': status,
        'q': q,
    }
    return render(request, 'articles/naver_migration_list.html', context)


@staff_member_required
def naver_migration_detail_view(request, pk):
    """원문 1건 상세 — 이관용 본문 HTML을 그대로 렌더링해 이미지가 제대로 재호스팅됐는지
    (네이버 CDN 403으로 깨지지 않는지) 눈으로 확인하는 게 이 화면의 핵심 용도다."""
    post = get_object_or_404(
        NaverBlogPost.objects.select_related('owner').prefetch_related('migrations__blog_account'), pk=pk
    )

    # 이전/다음 글(목록과 같은 정렬: 작성일 내림차순). 같은 블로그 안에서만 이동한다.
    siblings = list(
        NaverBlogPost.objects.filter(blog_id=post.blog_id).values_list('id', flat=True)
    )
    try:
        idx = siblings.index(post.id)
    except ValueError:
        idx = -1
    prev_id = siblings[idx - 1] if idx > 0 else None
    next_id = siblings[idx + 1] if 0 <= idx < len(siblings) - 1 else None

    context = {
        **admin_site.each_context(request),
        'title': f'📄 {post.title}',
        'post': post,
        'migrations': post.migrations.select_related('blog_account').all(),
        'image_items': sorted((post.image_map or {}).items()),
        'media_url': settings.MEDIA_URL,
        'prev_id': prev_id,
        'next_id': next_id,
    }
    return render(request, 'articles/naver_migration_detail.html', context)
