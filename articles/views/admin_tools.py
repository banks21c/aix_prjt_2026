import json
import re
import subprocess
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from ..models import FinancialConsultSheet

KST = dt_timezone(timedelta(hours=9))

LOG_TAIL_BYTES = 8000  # 로그 파일이 계속 append되며 커질 수 있어(로테이션 미설정) 끝부분만 읽는다
_EXCEPTION_LINE = re.compile(r'^[\w.]*(Error|Exception)\b.*:')  # 트레이스백 마지막 줄 패턴

# collect_stock_data/run_stock_prediction은 무겁고 비정기적이라 cron에 없고, 지금까지 SSH로
# manage.py를 직접 실행해야만 했다. 웹에서 트리거하는 건 임의 커맨드 실행으로 이어질 수 있어
# 위험하므로, 여기 화이트리스트에 있는 것만 (staff 전용 화면에서) 실행 가능하게 제한한다.
PIPELINE_COMMANDS = {
    'collect_stock_data': {
        'label': '주가 데이터 재수집 (전 종목 10년치 OHLCV)',
        'command': 'collect_stock_data',
        'args': ['--all'],
        'log_prefix': 'collect_stock_data_manual',
        'note': '종목당 1.5~3초 대기 + 실패 시 재시도가 있어 종목 수에 따라 수십 분 이상 걸릴 수 있습니다.',
    },
    'run_stock_prediction': {
        'label': 'AI 주가 예측 모델 재학습 (전 종목)',
        'command': 'run_stock_prediction',
        'args': [],
        'log_prefix': 'run_stock_prediction_manual',
        'note': '전 종목 RandomForest 재학습 — CPU를 많이 씁니다.',
    },
}


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


@staff_member_required
def pipeline_status_view(request):
    """collect_stock_data/run_stock_prediction처럼 무겁고 비정기적인 파이프라인 커맨드를
    버튼으로 백그라운드 실행하고, 마지막 실행 상태/로그를 확인하는 화면 (지금까지는 SSH로
    manage.py를 직접 실행해야만 볼 수 있었다)."""
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

        pipelines.append({
            'key': key,
            'label': conf['label'],
            'note': conf['note'],
            'running': _pipeline_is_running(conf['command']),
            'last_run': last_run,
            'last_run_ago_minutes': last_run_ago_minutes,
            'log_tail': log_tail,
            'status': status,
        })

    context = {
        'site_title': 'NextFinUp - 파이프라인 수동 실행',
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
        'site_title': 'NextFinUp - 크론 작업 현황',
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
    OPENAI_API_KEY/GEMINI_API_KEY가 미설정/플레이스홀더면 article_ai.py/chatbot_client.py가
    조용히 '시뮬레이션 모드'로 폴백하는데, 그 상태를 확인할 화면이 지금까지 없었다."""
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
                    'name': 'OpenAI (챗봇)',
                    'status': _integration_status(settings.OPENAI_API_KEY, placeholder='YOUR_OPENAI_API_KEY_HERE'),
                    'detail': '미설정/플레이스홀더면 챗봇이 시뮬레이션 모드로 응답합니다.',
                },
                {
                    'name': 'Gemini (뉴스 AI 요약·블로그 초안)',
                    'status': _integration_status(settings.GEMINI_API_KEY, placeholder='YOUR_GEMINI_API_KEY_HERE'),
                    'detail': '미설정/플레이스홀더면 AI 요약·블로그 초안 생성이 시뮬레이션 모드로 동작합니다.',
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
        'site_title': 'NextFinUp - 외부 연동 상태',
        'groups': groups,
    }
    return render(request, 'articles/integration_status.html', context)


@staff_member_required
def financial_consult_sheet_view(request):
    # FC/PB가 상담 중 사용하는 내부 전용 종합 재무상담 시트.
    # "저장" 버튼을 누르면 financial_consult_sheet_save_view로 전체 입력값을 JSON으로 전송해
    # FinancialConsultSheet에 기록하고, 별도로 인쇄/PDF 저장도 가능하다.
    return render(request, 'articles/financial_consult_sheet.html', {'site_title': 'NextFinUp - 종합 재무상담 시트'})


@staff_member_required
@require_POST
def financial_consult_sheet_save_view(request):
    """financial_consult_sheet.html에서 저장 버튼 클릭 시 fetch로 전송하는 전체 시트 데이터를
    FinancialConsultSheet에 저장한다. 목록/검색용 핵심 컬럼(고객명·연락처·상담자·상담일자)만
    최상위로 뽑고, 나머지 세부 항목은 원본 그대로 JSONField에 보관한다."""
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({'ok': False, 'error': 'invalid_payload'}, status=400)

    meta = payload.get('meta') or {}
    s1 = payload.get('s1') or {}

    consult_date = meta.get('consult_date') or None
    if consult_date:
        try:
            datetime.strptime(consult_date, '%Y-%m-%d')
        except ValueError:
            consult_date = None

    sheet = FinancialConsultSheet.objects.create(
        customer_name=(s1.get('name') or '')[:50],
        customer_phone=(s1.get('phone') or '')[:20],
        consultant_name=(meta.get('consultant') or '')[:50],
        consult_date=consult_date,
        data=payload,
        created_by=request.user,
    )
    return JsonResponse({'ok': True, 'id': sheet.id})
