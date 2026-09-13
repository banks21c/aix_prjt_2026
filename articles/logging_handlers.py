import logging
import traceback as tb_module


class DBErrorLogHandler(logging.Handler):
    """Django 기본 mail_admins 핸들러 대신 쓰는 로깅 핸들러 — django.* ERROR 로그(500 에러,
    잘못된 Host 헤더 등)를 이메일로 보내지 않고 SystemErrorLog 테이블에 저장한다. config/
    settings.py의 LOGGING이 'django' 로거의 핸들러를 이걸로 바꿔치기한다. 모델 임포트를
    지연시키는 이유는 로깅 설정이 앱 레지스트리보다 먼저 구성될 수 있어서다."""

    def emit(self, record):
        try:
            from articles.models import SystemErrorLog

            request = getattr(record, 'request', None)
            tb_text = ''.join(tb_module.format_exception(*record.exc_info)) if record.exc_info else ''
            SystemErrorLog.objects.create(
                level=record.levelname,
                logger_name=record.name,
                message=record.getMessage(),
                traceback=tb_text,
                request_path=getattr(request, 'path', '') if request else '',
                request_method=getattr(request, 'method', '') if request else '',
                status_code=getattr(record, 'status_code', None),
            )
        except Exception:
            self.handleError(record)
