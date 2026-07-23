import json
import re
import subprocess
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from ..models import FinancialConsultSheet

KST = dt_timezone(timedelta(hours=9))


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
        if log_path:
            try:
                log_file = Path(log_path)
                if log_file.exists():
                    last_run = datetime.fromtimestamp(log_file.stat().st_mtime, tz=KST)
                    last_run_ago_minutes = int((datetime.now(KST) - last_run).total_seconds() // 60)
            except OSError:
                pass

        jobs.append({
            'schedule_human': _describe_cron_schedule(minute, hour, day, month, weekday),
            'schedule_raw': f"{minute} {hour} {day} {month} {weekday}",
            'command_name': command_name,
            'log_path': log_path,
            'last_run': last_run,
            'last_run_ago_minutes': last_run_ago_minutes,
        })

    context = {
        'site_title': 'NextFinUp - 크론 작업 현황',
        'jobs': jobs,
        'error': error,
    }
    return render(request, 'articles/cron_status.html', context)


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
