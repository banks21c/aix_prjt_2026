"""시스템 관리(articles/admin.py, /admin/, 슈퍼유저 전용)와 분리된 업무(상담·구독) 관리 전용
어드민 사이트. /staff/에 마운트한다(config/urls.py). 여기 등록된 ModelAdmin 클래스는
articles/admin.py에서 이미 정의된 것을 그대로 재사용한다 — 승인 액션/save_model 커스텀 로직
같은 걸 두 곳에 중복 구현하면 어긋나기 쉬워서다. 사이트 자체는 is_staff이기만 하면(슈퍼유저가
아니어도) 로그인 가능 — 기본 AdminSite.has_permission 그대로 사용. 다만 구독 관련 두 모델
(SubscriptionOrder, UserSubscription)은 결제/프리미엄 권한이 걸려 있어 일반 업무 스태프(상담
담당자)에게는 노출하지 않고 슈퍼유저만 보게, 아래 _SuperuserOnlyMixin으로 모델 단위로 다시
잠근다 — 상담신청관리/재무상담시트관리 두 모델만 스태프 전원에게 열려 있다."""
from django.contrib import admin

from .admin import (
    ConsultRequestAdmin, FinancialConsultSheetAdmin, SubscriptionOrderAdmin, UserSubscriptionAdmin,
)
from .models import ConsultRequest, FinancialConsultSheet, SubscriptionOrder, UserSubscription


class BusinessAdminSite(admin.AdminSite):
    site_header = "NextFinUp 업무관리"
    site_title = "NextFinUp 업무관리"
    index_title = "상담 · 구독 관리"


class _SuperuserOnlyMixin:
    def has_module_permission(self, request):
        return super().has_module_permission(request) and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and request.user.is_superuser

    def has_add_permission(self, request):
        return super().has_add_permission(request) and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and request.user.is_superuser


class _SuperuserOnlySubscriptionOrderAdmin(_SuperuserOnlyMixin, SubscriptionOrderAdmin):
    pass


class _SuperuserOnlyUserSubscriptionAdmin(_SuperuserOnlyMixin, UserSubscriptionAdmin):
    pass


business_admin_site = BusinessAdminSite(name='business_admin')
business_admin_site.register(ConsultRequest, ConsultRequestAdmin)
business_admin_site.register(FinancialConsultSheet, FinancialConsultSheetAdmin)
business_admin_site.register(SubscriptionOrder, _SuperuserOnlySubscriptionOrderAdmin)
business_admin_site.register(UserSubscription, _SuperuserOnlyUserSubscriptionAdmin)
