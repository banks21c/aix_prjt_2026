"""시스템 관리(articles/admin.py, /admin/, 슈퍼유저 전용)와 분리된 업무(상담·구독) 관리 전용
어드민 사이트. /staff/에 마운트한다(config/urls.py). 여기 등록된 ModelAdmin 클래스는
articles/admin.py에서 이미 정의된 것을 그대로 재사용한다 — 승인 액션/save_model 커스텀 로직
같은 걸 두 곳에 중복 구현하면 어긋나기 쉬워서다. is_staff이기만 하면(슈퍼유저가 아니어도)
로그인 가능 — 기본 AdminSite.has_permission 그대로 사용."""
from django.contrib import admin

from .admin import (
    ConsultRequestAdmin, FinancialConsultSheetAdmin, SubscriptionOrderAdmin, UserSubscriptionAdmin,
)
from .models import ConsultRequest, FinancialConsultSheet, SubscriptionOrder, UserSubscription


class BusinessAdminSite(admin.AdminSite):
    site_header = "NextFinUp 업무관리"
    site_title = "NextFinUp 업무관리"
    index_title = "상담 · 구독 관리"


business_admin_site = BusinessAdminSite(name='business_admin')
business_admin_site.register(ConsultRequest, ConsultRequestAdmin)
business_admin_site.register(FinancialConsultSheet, FinancialConsultSheetAdmin)
business_admin_site.register(SubscriptionOrder, SubscriptionOrderAdmin)
business_admin_site.register(UserSubscription, UserSubscriptionAdmin)
