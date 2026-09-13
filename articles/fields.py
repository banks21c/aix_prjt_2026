from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _fernet() -> Fernet:
    return Fernet(settings.CREDENTIAL_ENCRYPTION_KEY)


class EncryptedCharField(models.CharField):
    """DB에는 Fernet으로 암호화된 값을 저장하고, 파이썬 쪽에는 평문으로 노출하는 CharField.

    BlogPostingAccount.credential처럼 이미 여러 곳(폼/뷰/발행 로직)에서 평문 문자열로
    읽고 쓰는 코드가 있어, 그 코드를 건드리지 않고 저장 단계에서만 투명하게 암호화하기 위해
    도입했다. 값이 이미 암호화(Fernet 토큰)되어 있으면 그대로 복호화하고, 아직 평문인
    레거시 값이면 복호화 실패 시 원문을 그대로 반환한다(마이그레이션으로 일괄 암호화 처리).
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            # 아직 암호화 마이그레이션을 거치지 않은 레거시 평문 값
            return value
