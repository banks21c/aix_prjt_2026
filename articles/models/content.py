from django.db import models
from django.contrib.auth.models import User


# ==========================================
# 8. 홈페이지 뉴스레터 구독 (비회원도 이메일만으로 신청 가능)
# ==========================================
class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True, verbose_name="이메일")
    is_active = models.BooleanField(default=True, verbose_name="구독 활성화 여부")
    subscribed_at = models.DateTimeField(auto_now_add=True, verbose_name="구독 신청일")

    class Meta:
        ordering = ['-subscribed_at']
        verbose_name = "뉴스레터 구독자 (NewsletterSubscriber)"
        verbose_name_plural = "뉴스레터 구독자 (NewsletterSubscriber)"

    def __str__(self):
        return self.email


class NewsletterIssue(models.Model):
    """뉴스레터 발행 1회분. generate_newsletter_draft 커맨드가 최근 기사로 초안(DRAFT)을 자동
    작성해두면, 관리자가 Admin 화면에서 제목/본문을 직접 확인·수정한 뒤 상태를 READY로 바꾼다.
    send_newsletter 커맨드는 READY 상태인 건만 골라 발송하고 SENT로 넘긴다."""
    STATUS_CHOICES = [
        ('DRAFT', '초안(검토 대기)'),
        ('READY', '발송 대기(다음 자동발송 때 전송)'),
        ('SENT', '발송 완료'),
    ]

    subject = models.CharField(max_length=255, verbose_name="제목")
    body = models.TextField(verbose_name="본문")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='DRAFT', verbose_name="상태")
    article_count = models.PositiveIntegerField(default=0, verbose_name="초안 작성 시 포함된 기사 수")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="초안 생성일")
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name="발송 일시")
    recipient_count = models.PositiveIntegerField(default=0, verbose_name="발송 성공 인원")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "뉴스레터 발행 (NewsletterIssue)"
        verbose_name_plural = "뉴스레터 발행 (NewsletterIssue)"

    def __str__(self):
        return f"[{self.get_status_display()}] {self.subject}"


# ==========================================
# 9. 메뉴(내비게이션) 관리 테이블
# ==========================================
class Menu(models.Model):
    """상단 내비게이션에 노출되는 메뉴 항목. 랜딩 페이지(index)와 내부 앱 공통 헤더(header)는
    노출되는 메뉴 구성이 달라서 menu_type으로 구분한다 — 새 화면이 생기면 MENU_TYPE_CHOICES에
    값만 추가하면 확장된다. 로그인/로그아웃/마이페이지/관리자 링크처럼 로그인 상태에 따라
    달라지는 항목은 이 테이블이 아니라 템플릿에 그대로 남겨둔다."""
    MENU_TYPE_CHOICES = [
        ('INDEX', '랜딩 페이지(index)'),
        ('HEADER', '내부 앱 공통 헤더'),
    ]

    name = models.CharField(max_length=50, verbose_name="메뉴명")
    url_name = models.CharField(max_length=100, blank=True, verbose_name="URL name(urls.py의 name)")
    external_url = models.CharField(max_length=255, blank=True, verbose_name="직접 URL (url_name이 없을 때 사용)")
    badge_text = models.CharField(max_length=20, blank=True, verbose_name="배지 텍스트 (예: DEMO)")
    menu_type = models.CharField(max_length=10, choices=MENU_TYPE_CHOICES, verbose_name="노출 화면")
    order = models.PositiveIntegerField(default=0, verbose_name="정렬 순서")
    is_active = models.BooleanField(default=True, verbose_name="사용 여부(숨김 처리)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['menu_type', 'order', 'id']
        verbose_name = "메뉴 (Menu)"
        verbose_name_plural = "메뉴 관리 (Menu)"

    def __str__(self):
        return f"[{self.get_menu_type_display()}] {self.name}"

    def get_url(self):
        if self.url_name:
            try:
                from django.urls import reverse
                return reverse(self.url_name)
            except Exception:
                return '#'
        return self.external_url or '#'


# ==========================================
# 10. 상담 신청 (ISA/IRP/연금저축 등 독립 정적 비교 페이지 공용)
# ==========================================
class ConsultRequest(models.Model):
    """IRP/ISA/연금저축 비교 페이지(nextfinup에서 분리된 정적 사이트)의 상담 신청 폼 제출을
    저장한다. 페이지 자체는 Django 밖에 있지만, 같은 도메인(nextfinup.com)에서 이 API로
    fetch 요청을 보내 저장한다."""
    PRODUCT_CHOICES = [
        ('ISA', 'ISA'),
        ('IRP', 'IRP'),
        ('PENSION', '연금저축'),
        ('INSURANCE', '보험'),
        ('ASSET', '자산관리 종합'),
    ]

    product = models.CharField(max_length=20, choices=PRODUCT_CHOICES, verbose_name="상품 유형")
    name = models.CharField(max_length=50, verbose_name="이름")
    phone = models.CharField(max_length=20, verbose_name="연락처")
    interest = models.CharField(max_length=100, blank=True, verbose_name="관심 기관/상품")
    goal = models.CharField(max_length=200, blank=True, verbose_name="목표")
    message = models.TextField(blank=True, verbose_name="문의사항")
    source_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="접수 IP")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="접수 일시")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "상담 신청 (ConsultRequest)"
        verbose_name_plural = "상담 신청 관리 (ConsultRequest)"

    def __str__(self):
        return f"[{self.get_product_display()}] {self.name} ({self.created_at:%Y-%m-%d %H:%M})"


# ==========================================
# 11. 종합 재무상담 시트 (FC/PB 내부 전용 상담 기록)
# ==========================================
class FinancialConsultSheet(models.Model):
    """financial_consult_sheet.html(14개 섹션, 가변 행 표 다수)의 제출 데이터를 저장한다.
    섹션/표 구조가 자주 바뀔 수 있어 원본 전체는 JSONField(data)에 통째로 보관하고,
    목록 조회·검색에 필요한 핵심 항목만 별도 컬럼으로 뽑아둔다."""
    customer_name = models.CharField(max_length=50, blank=True, verbose_name="고객 성명")
    customer_phone = models.CharField(max_length=20, blank=True, verbose_name="고객 연락처")
    consultant_name = models.CharField(max_length=50, blank=True, verbose_name="상담자 (FC/PB)")
    consult_date = models.DateField(null=True, blank=True, verbose_name="상담일자")
    data = models.JSONField(verbose_name="상담 시트 데이터")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="작성자 계정")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="저장 일시")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "재무상담 시트 (FinancialConsultSheet)"
        verbose_name_plural = "재무상담 시트 관리 (FinancialConsultSheet)"

    def __str__(self):
        return f"{self.customer_name or '(무기명)'} ({self.created_at:%Y-%m-%d %H:%M})"
