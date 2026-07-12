"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from articles.views import (
    landing_page_view, main_dashboard_view,
    news_board_view, news_detail_view, stock_detail_view, stock_minute_chart_view,
    chatbot_ask_view,
    signup_view, login_view, logout_view,
    kakao_login_view, kakao_callback_view,
    google_login_view, google_callback_view,
    naver_login_view, naver_callback_view,
    blogger_connect_view, blogger_callback_view,
    my_page_view, verify_email_view,
)  # ◀ 우리가 만든 뷰 임포트

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', landing_page_view, name='landing_page'),  # ◀ 메인 홈페이지(랜딩 페이지)
    path('dashboard/', main_dashboard_view, name='main_dashboard'),  # ◀ AI 예측/뉴스 대시보드
    path('news/', news_board_view, name='news_board'),  # ◀ 수집된 뉴스 게시판
    path('news/<int:pk>/', news_detail_view, name='news_detail'),
    path('stocks/<str:ticker>/', stock_detail_view, name='stock_detail'),  # ◀ 종목 상세(일봉 차트/AI 예측/관련 뉴스)
    path('stocks/<str:ticker>/minute-chart/', stock_minute_chart_view, name='stock_minute_chart'),  # ◀ 당일 분봉 온디맨드 API

    path('api/chatbot/', chatbot_ask_view, name='chatbot_ask'),  # ◀ 주식/경제 챗봇 위젯 API

    path('signup/', signup_view, name='signup'),
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('mypage/', my_page_view, name='my_page'),  # ◀ 내 정보 관리(뉴스구독/자동포스팅 설정)
    path('verify-email/<uidb64>/<token>/', verify_email_view, name='verify_email'),  # ◀ 이메일 인증 링크

    path('accounts/kakao/login/', kakao_login_view, name='kakao_login'),
    path('accounts/kakao/callback/', kakao_callback_view, name='kakao_callback'),
    path('accounts/google/login/', google_login_view, name='google_login'),
    path('accounts/google/callback/', google_callback_view, name='google_callback'),
    path('accounts/naver/login/', naver_login_view, name='naver_login'),
    # 네이버 개발자센터에 등록된 Callback URL(https://www.nextfinup.com/api/member/oauth2c)과 정확히 일치해야 함
    path('api/member/oauth2c', naver_callback_view, name='naver_callback'),

    path('accounts/blogger/connect/', blogger_connect_view, name='blogger_connect'),  # ◀ 마이페이지 - 블로거 자동포스팅 연동
    path('accounts/blogger/callback/', blogger_callback_view, name='blogger_callback'),
]

