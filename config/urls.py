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
    chatbot_ask_view,
    signup_view, login_view, logout_view,
    kakao_login_view, kakao_callback_view,
    google_login_view, google_callback_view,
)  # ◀ 우리가 만든 뷰 임포트

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', landing_page_view, name='landing_page'),  # ◀ 메인 홈페이지(랜딩 페이지)
    path('dashboard/', main_dashboard_view, name='main_dashboard'),  # ◀ AI 예측/뉴스 대시보드

    path('api/chatbot/', chatbot_ask_view, name='chatbot_ask'),  # ◀ 주식/경제 챗봇 위젯 API

    path('signup/', signup_view, name='signup'),
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),

    path('accounts/kakao/login/', kakao_login_view, name='kakao_login'),
    path('accounts/kakao/callback/', kakao_callback_view, name='kakao_callback'),
    path('accounts/google/login/', google_login_view, name='google_login'),
    path('accounts/google/callback/', google_callback_view, name='google_callback'),
]

