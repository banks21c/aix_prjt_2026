"""literary 앱(작가·작품 카탈로그)은 nextfinup_db가 아니라 같은 서버의 autovi_db에 둔다.

autovi(로컬 영상 제작 파이프라인)가 SSH 터널로 autovi_db에 직접 붙어 작가·작품과 제작 원장을 쓰고,
nextfinup은 이 라우터로 같은 테이블을 읽고 쓴다 — /admin-tools/literary-picker/와 관리자 화면이
autovi 대시보드와 같은 데이터를 본다. autovi_db의 스키마는 autovi 저장소의 마이그레이션이 정본이므로
literary 모델·마이그레이션은 두 저장소에서 똑같이 유지해야 한다.
그 밖의 앱(회원·뉴스·세션·관리자 기록 등)은 전부 default(nextfinup_db)에 남는다.
"""

AUTOVI_DB = 'autovi'
AUTOVI_APPS = {'literary'}


class AutoviRouter:
    def db_for_read(self, model, **hints):
        return AUTOVI_DB if model._meta.app_label in AUTOVI_APPS else None

    def db_for_write(self, model, **hints):
        return AUTOVI_DB if model._meta.app_label in AUTOVI_APPS else None

    def allow_relation(self, obj1, obj2, **hints):
        # DB가 다른 테이블 사이에는 외래키를 걸 수 없다 — literary끼리만 허용.
        in1, in2 = obj1._meta.app_label in AUTOVI_APPS, obj2._meta.app_label in AUTOVI_APPS
        if in1 or in2:
            return in1 and in2
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in AUTOVI_APPS:
            return db == AUTOVI_DB
        if db == AUTOVI_DB:
            return False
        return None
