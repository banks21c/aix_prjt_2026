"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()

# get_wsgi_application()은 django.setup()까지만 하고 URLconf는 건드리지 않는다. 그래서
# config.urls 임포트(2.5초 — 대부분 articles.article_ai가 끌어오는 google.genai와 openai)가
# 각 워커의 "첫 요청"에 얹혀, 재시작 직후 운 나쁜 사용자 3명이 매번 2~3초를 기다렸다.
# 여기서 미리 로드해두면 gunicorn --preload가 이 비용까지 fork 전에 끝낸다.
from django.urls import get_resolver  # noqa: E402  (application 생성 후여야 한다)

get_resolver().url_patterns
