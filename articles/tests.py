# 커버: 회원가입->이메일인증->로그인 플로우, 외부 API 호출 없는 공개 뷰(랜딩/대시보드/뉴스게시판/
# 종목상세), 등급별 일일 한도 계산(utils.scraping_stats/blog_posting.posting_stats), 핵심 모델 제약.
# 아직 커버하지 않음(외부 서비스 의존이라 별도 mocking 전략이 필요): yfinance/FinanceDataReader/KIS를
# 부르는 관리 커맨드, 카카오/구글/네이버/블로거 OAuth 뷰, run_stock_prediction(모델 학습).
from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from . import blog_posting, utils
from .models import (
    AnalyzedArticle, ConsultRequest, MemberGrade, StockDailyPrice, StockItem, StockPrediction,
    UserPreference, UserSubscription,
)

STRONG_PASSWORD = "N3xtF1nUp-test-only!"

# migrations/0037_seed_membergrades.py + 0039_seed_grade_limits_and_admin_grade.py가 level 1~6을
# 이미 시딩해두므로, 테스트에서 만드는 MemberGrade는 그 범위 밖의 level을 써야 unique 충돌이 없다.
TEST_GRADE_LEVEL_START = 900

# 테스트 클라이언트는 일반 http로 요청하는데, settings.SECURE_SSL_REDIRECT = not DEBUG이고
# Django 테스트 러너가 DEBUG를 강제로 False로 만들기 때문에 override 없이는 모든 요청이
# 301(https로 리다이렉트)이 된다 — 실제 운영에서는 프록시의 X-Forwarded-Proto 덕분에 문제 없음.
@override_settings(SECURE_SSL_REDIRECT=False)
class AuthFlowTests(TestCase):
    """signup -> 이메일 인증 -> login 흐름. LoginForm.confirm_login_allowed()가
    is_active=False 계정을 정확한 한국어 메시지로 막는 이유는 settings.AUTHENTICATION_BACKENDS를
    AllowAllUsersModelBackend로 바꿔둔 것과 짝이므로, 이 조합이 계속 맞물려 동작하는지 지킨다."""

    def _signup(self):
        return self.client.post(reverse('signup'), {
            'username': 'newmember',
            'email': 'newmember@example.com',
            'password1': STRONG_PASSWORD,
            'password2': STRONG_PASSWORD,
        })

    def test_signup_creates_inactive_user_and_sends_verification_email(self):
        response = self._signup()

        user = User.objects.get(username='newmember')
        self.assertRedirects(response, reverse('login'))
        self.assertFalse(user.is_active)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('newmember@example.com', mail.outbox[0].to)

    def test_login_blocks_unverified_account(self):
        self._signup()

        response = self.client.post(reverse('login'), {
            'username': 'newmember',
            'password': STRONG_PASSWORD,
        })

        self.assertEqual(response.status_code, 200)  # re-renders the form, no redirect
        self.assertContains(response, "이메일 인증이 완료되지 않은")
        self.assertFalse(self.client.session.get('_auth_user_id'))

    def test_verify_email_activates_account_and_allows_login(self):
        self._signup()
        user = User.objects.get(username='newmember')
        preference = UserPreference.objects.get(user=user)
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

        response = self.client.get(reverse('verify_email', args=[uidb64, preference.email_verification_token]))
        self.assertRedirects(response, reverse('landing_page'))

        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(user.email, 'newmember@example.com')

        login_ok = self.client.login(username='newmember', password=STRONG_PASSWORD)
        self.assertTrue(login_ok)

    def test_verify_email_rejects_invalid_token(self):
        self._signup()
        user = User.objects.get(username='newmember')
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

        response = self.client.get(reverse('verify_email', args=[uidb64, 'not-the-real-token']))
        self.assertRedirects(response, reverse('login'))

        user.refresh_from_db()
        self.assertFalse(user.is_active)


@override_settings(SECURE_SSL_REDIRECT=False)
class PublicViewSmokeTests(TestCase):
    """외부 API 호출 없이(순수 ORM만으로) 렌더링되는 공개 페이지들이 최소 데이터로도
    200을 반환하는지 확인. main_dashboard_view는 MarketIndex가 비어 있어도 안전하게
    동작해야 하므로 일부러 만들지 않는다."""

    @classmethod
    def setUpTestData(cls):
        cls.stock = StockItem.objects.create(
            ticker='005930', name='삼성전자', market_type='KOSPI', is_active=True, is_major_index=True,
        )
        StockDailyPrice.objects.create(
            stock=cls.stock, date=timezone.localdate(),
            open_price=70000, high_price=71000, low_price=69500, close_price=70500, volume=1000000,
        )
        AnalyzedArticle.objects.create(
            stock=cls.stock, title='삼성전자 관련 뉴스', original_url='https://example.com/news/1',
            source_media='테스트뉴스', ai_summary='요약', ai_analysis='분석', blog_content='본문',
            original_content='원문 본문 테스트용 텍스트',
        )

    def test_landing_page(self):
        response = self.client.get(reverse('landing_page'))
        self.assertEqual(response.status_code, 200)

    def test_main_dashboard(self):
        response = self.client.get(reverse('main_dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_news_board(self):
        response = self.client.get(reverse('news_board'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '삼성전자 관련 뉴스')

    def test_stock_detail_existing_ticker(self):
        response = self.client.get(reverse('stock_detail', args=[self.stock.ticker]))
        self.assertEqual(response.status_code, 200)

    def test_stock_detail_unknown_ticker_404s(self):
        response = self.client.get(reverse('stock_detail', args=['999999']))
        self.assertEqual(response.status_code, 404)


class GradeLimitTests(TestCase):
    """MemberGrade의 daily_scrape_limit/daily_post_limit이 NULL이면 무제한, 관리자/프리미엄은
    등급과 무관하게 항상 무제한이라는 규칙(articles.utils.scraping_stats,
    articles.blog_posting.posting_stats)을 지킨다 — 프레임워크가 대신 검증해주지 않는 로직."""

    def test_scraping_stats_respects_grade_limit(self):
        grade = MemberGrade.objects.create(name='일반', level=TEST_GRADE_LEVEL_START + 1, daily_scrape_limit=3)
        user = User.objects.create_user(username='limited', password='x')
        UserPreference.objects.create(user=user, grade=grade)
        AnalyzedArticle.objects.create(
            title='기사1', original_url='https://example.com/a', source_media='m', scraped_by=user,
        )

        stats = utils.scraping_stats(user)

        self.assertFalse(stats['is_admin'])
        self.assertEqual(stats['today_count'], 1)
        self.assertEqual(stats['remaining'], 2)

    def test_scraping_stats_null_limit_is_unlimited(self):
        grade = MemberGrade.objects.create(name='무제한등급', level=TEST_GRADE_LEVEL_START + 2, daily_scrape_limit=None)
        user = User.objects.create_user(username='unlimited', password='x')
        UserPreference.objects.create(user=user, grade=grade)

        stats = utils.scraping_stats(user)

        self.assertIsNone(stats['remaining'])

    def test_scraping_stats_admin_always_unlimited(self):
        grade = MemberGrade.objects.create(name='제한등급', level=TEST_GRADE_LEVEL_START + 3, daily_scrape_limit=0)
        user = User.objects.create_user(username='staffuser', password='x', is_staff=True)
        UserPreference.objects.create(user=user, grade=grade)

        stats = utils.scraping_stats(user)

        self.assertTrue(stats['is_admin'])
        self.assertIsNone(stats['remaining'])

    def test_posting_stats_premium_is_unlimited_regardless_of_grade(self):
        grade = MemberGrade.objects.create(name='제한등급', level=TEST_GRADE_LEVEL_START + 4, daily_post_limit=1)
        user = User.objects.create_user(username='premiumuser', password='x')
        UserPreference.objects.create(user=user, grade=grade)
        UserSubscription.objects.create(user=user, is_active_premium=True)

        stats = blog_posting.posting_stats(user)

        self.assertTrue(stats['is_premium'])
        self.assertIsNone(stats['remaining'])


class ModelBasicsTests(TestCase):
    """마이그레이션/모델 정의가 예상대로인지 확인하는 값싼 회귀 테스트."""

    def test_stock_daily_price_unique_together_on_stock_and_date(self):
        stock = StockItem.objects.create(ticker='000660', name='SK하이닉스', market_type='KOSPI')
        today = timezone.localdate()
        StockDailyPrice.objects.create(
            stock=stock, date=today, open_price=1, high_price=1, low_price=1, close_price=1, volume=1,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            StockDailyPrice.objects.create(
                stock=stock, date=today, open_price=2, high_price=2, low_price=2, close_price=2, volume=2,
            )

    def test_stock_prediction_unique_together_on_stock_and_date(self):
        stock = StockItem.objects.create(ticker='035420', name='NAVER', market_type='KOSPI')
        today = timezone.localdate()
        StockPrediction.objects.create(stock=stock, date=today)

        with self.assertRaises(IntegrityError), transaction.atomic():
            StockPrediction.objects.create(stock=stock, date=today)

    def test_analyzed_article_str(self):
        article = AnalyzedArticle.objects.create(
            title='제목', original_url='https://example.com/b', source_media='매체',
        )
        self.assertEqual(str(article), '[매체] 제목')

    def test_member_grade_str(self):
        grade = MemberGrade.objects.create(name='VIP', level=TEST_GRADE_LEVEL_START + 5)
        self.assertEqual(str(grade), f'{grade.level}. VIP')


@override_settings(SECURE_SSL_REDIRECT=False)
class ExpertConsultTests(TestCase):
    """전문가 상담 페이지(/experts/)와 그 폼이 물고 있는 기존 /api/consult/ 계약을 지킨다.
    페이지는 순수 render라 외부 API 의존이 없고, 상담 접수는 consult_request_view가
    전부 처리하므로 여기서는 그 뷰가 ASSET 유형을 받아주는지까지만 확인한다."""

    def test_consult_api_accepts_asset_product(self):
        response = self.client.post(reverse('consult_request'), data={
            'product': 'ASSET',
            'name': '홍길동',
            'phone': '010-1234-5678',
            'interest': '전체 자산 진단',
            'goal': '세액공제 한도를 다 채우고 싶어요',
            'message': '문의 내용',
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        consult = ConsultRequest.objects.get()
        self.assertEqual(consult.product, 'ASSET')
        self.assertEqual(consult.get_product_display(), '자산관리 종합')
        self.assertEqual(consult.interest, '전체 자산 진단')
