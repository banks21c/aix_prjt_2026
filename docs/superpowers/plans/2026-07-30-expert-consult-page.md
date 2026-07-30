# 전문가 상담 페이지 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 공개 페이지 `/experts/`를 신설해 전문가 프로필을 보여주고, 하단 상담 폼을 기존 `/api/consult/` 엔드포인트에 물려 실제 접수되게 한다.

**Architecture:** 이 프로젝트의 공개 안내 페이지 패턴을 그대로 따른다 — `articles/views/public.py`에 단순 `render` 뷰 하나, 독립 HTML 템플릿 하나(부트스트랩 CDN + `_favicon`/`_header`/`_cookie_banner` include). 상담 접수는 새로 만들지 않고 이미 검증·허니팟·관리자 메일 발송을 처리하는 `consult_request_view`를 fetch로 호출한다. 전문가는 1명이므로 모델을 만들지 않고 템플릿에 직접 작성한다.

**Tech Stack:** Django 5.2.16, Bootstrap 5.3.3 (jsdelivr CDN), 바닐라 JS `fetch`, MySQL(운영/CI) · sqlite(로컬 미리보기)

## Global Constraints

스펙(`docs/superpowers/specs/2026-07-30-expert-consult-page-design.md`)의 프로젝트 전역 요구사항. 모든 태스크에 암묵적으로 적용된다.

- URL 경로는 `/experts/`, URL name은 `expert_consult`.
- 메뉴명은 정확히 `전문가 상담`. `menu_type`은 `INDEX`와 `HEADER` 양쪽, `order=9`.
- 상담 분류값은 `('ASSET', '자산관리 종합')`. 폼에서 숨김 필드로 고정 전송하며 사용자가 고르지 않는다.
- 관심 분야 select 옵션은 정확히 이 9개: `국민연금`, `연금저축`, `IRP`, `ISA`, `보험`, `채권`, `부동산`, `청년저축`, `전체 자산 진단`.
- 폼은 CSRF 토큰을 전송하지 않는다. `consult_request_view`는 정적 HTML 호출부를 위해 `csrf_exempt`로 설계돼 있고 스팸 방어는 같은 뷰의 허니팟이 담당한다.
- 허니팟 필드명은 `website`.
- **전문가 프로필의 실제 문구(성명·직함·자격·경력)를 지어내지 않는다.** 템플릿에는 `[[...]]` 형태의 눈에 띄는 자리표시자를 넣고, 운영자가 채운다.
- **프로필 사진 자리에 사람 사진처럼 보이는 스톡 이미지를 쓰지 않는다.** 이니셜을 넣은 원형 도형으로 자리를 잡는다.
- **asset-management 허브의 데모 상담 폼은 수정하지 않는다.** 스펙이 "의도된 잔여 불일치"로 명시한 범위 밖 항목이다.
- 마이그레이션 번호는 `0054`, `0055` (현재 최신은 `0053`).
- 새 테스트 클래스에는 `@override_settings(SECURE_SSL_REDIRECT=False)`가 **필수**다. `settings.SECURE_SSL_REDIRECT = not DEBUG`이고 테스트 러너가 `DEBUG=False`를 강제하므로, 없으면 모든 요청이 301이 된다.

### 로컬 테스트 실행 방법

이 개발 PC에는 MySQL이 없고 `config/settings.py`는 MySQL 하드코딩이다. **`config/settings.py`를
고치지 말고**, 리포지토리 밖(스크래치패드 등 임의의 디렉터리)에 아래 오버라이드 모듈을
만들어 쓴다.

`<임의경로>/dev_sqlite_settings.py`:

```python
from pathlib import Path

from config.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': Path(__file__).resolve().parent / 'preview.sqlite3',
    }
}
DEBUG = True
ALLOWED_HOSTS = ['*']
```

그 디렉터리를 `PYTHONPATH`에 올리고 **모든** 테스트/서버 명령에 `--settings=dev_sqlite_settings`를
붙인다:

```bash
export PYTHONPATH="<위 모듈이 있는 디렉터리>:$PWD"
```

의존성은 `requirements.txt` 전체가 아니라 `Django python-dotenv pandas requests trafilatura openai`
만 있으면 된다 (`mysqlclient`·`yfinance`·`scikit-learn`·`lightgbm`·`playwright`는 파이프라인 관리
커맨드 전용). `.env`에 개발용 `SECRET_KEY`와 `DEBUG=True`가 필요하다.

기준선: 이 계획을 시작하는 시점에
`venv/bin/python manage.py test articles --settings=dev_sqlite_settings`가 **17개 통과**한다.

**CI는 다르다.** `.github/workflows/ci.yml`은 MySQL 컨테이너를 붙여 `--settings` 없이
`python manage.py test`를 그대로 돌린다. 아래 태스크의 명령에서 `--settings=dev_sqlite_settings`를
빼면 CI와 같은 실행이 된다.

## File Structure

| 파일 | 책임 | 신규/수정 |
|---|---|---|
| `articles/models/content.py` | `ConsultRequest.PRODUCT_CHOICES`에 `ASSET` 추가 | 수정 |
| `articles/migrations/0054_consultrequest_asset_product.py` | `product` 필드 `choices` 변경 | 신규 |
| `articles/views/public.py` | `expert_consult_view` — 단순 render | 수정 |
| `articles/views/__init__.py` | `expert_consult_view` 재export | 수정 |
| `config/urls.py` | `/experts/` 라우트 | 수정 |
| `articles/templates/articles/expert_consult.html` | 히어로 + 전문가 프로필 + 상담 폼 (마크업·CSS·JS 자체 보유) | 신규 |
| `articles/migrations/0055_seed_expert_consult_menu.py` | `Menu` 행 2개 시드 (reverse로 삭제 가능) | 신규 |
| `articles/sitemaps.py` | `StaticViewSitemap.pages`에 등록 | 수정 |
| `articles/middleware.py` | `MENU_URL_NAMES`에 등록 | 수정 |
| `articles/tests.py` | `ExpertConsultTests` 클래스 | 수정 |

---

### Task 1: ConsultRequest에 '자산관리 종합' 상품 유형 추가

`consult_request_view`는 `if product not in dict(ConsultRequest.PRODUCT_CHOICES)`로 검증하므로, 이 값이 없으면 `/experts/` 폼이 400 `invalid_product`로 튕긴다. 페이지보다 먼저 뚫어둔다.

**Files:**
- Modify: `articles/models/content.py` (`ConsultRequest.PRODUCT_CHOICES`)
- Create: `articles/migrations/0054_consultrequest_asset_product.py`
- Test: `articles/tests.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces: `ConsultRequest.PRODUCT_CHOICES`에 `('ASSET', '자산관리 종합')` 항목. Task 3의 폼이 `product: 'ASSET'`으로 전송하고, Task 4는 이 값을 쓰지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`articles/tests.py` 맨 아래에 클래스를 추가한다. 파일 상단 import에 `ConsultRequest`를 넣어야 한다 — 기존 import 블록을 이렇게 고친다:

```python
from .models import (
    AnalyzedArticle, ConsultRequest, MemberGrade, StockDailyPrice, StockItem, StockPrediction,
    UserPreference, UserSubscription,
)
```

그리고 파일 맨 아래에 추가한다:

```python
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `venv/bin/python manage.py test articles.tests.ExpertConsultTests -v 2 --settings=dev_sqlite_settings`

Expected: FAIL. `consult_request_view`가 `invalid_product`로 400을 돌려주므로
`AssertionError: 400 != 200`.

- [ ] **Step 3: 모델에 choices 추가**

`articles/models/content.py`의 `ConsultRequest.PRODUCT_CHOICES`를 이렇게 고친다:

```python
    PRODUCT_CHOICES = [
        ('ISA', 'ISA'),
        ('IRP', 'IRP'),
        ('PENSION', '연금저축'),
        ('INSURANCE', '보험'),
        ('ASSET', '자산관리 종합'),
    ]
```

- [ ] **Step 4: 마이그레이션 생성**

Run: `venv/bin/python manage.py makemigrations articles --name consultrequest_asset_product --settings=dev_sqlite_settings`

Expected: `articles/migrations/0054_consultrequest_asset_product.py` 생성. 내용은
`AlterField(model_name='consultrequest', name='product', field=models.CharField(choices=[...], max_length=20, verbose_name='상품 유형'))` 한 개.
`choices`만 바뀌므로 DB 스키마는 변하지 않는다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `venv/bin/python manage.py test articles.tests.ExpertConsultTests -v 2 --settings=dev_sqlite_settings`

Expected: PASS (1 test)

- [ ] **Step 6: 전체 테스트로 회귀 확인**

Run: `venv/bin/python manage.py test articles --settings=dev_sqlite_settings`

Expected: OK, 18 tests (기존 17 + 신규 1)

- [ ] **Step 7: 커밋**

```bash
git add articles/models/content.py articles/migrations/0054_consultrequest_asset_product.py articles/tests.py
git commit -m "$(cat <<'EOF'
Add ASSET product type to ConsultRequest

/experts/ 페이지의 상담 폼이 자산관리 종합 상담으로 접수되게 하려면 이 값이 필요하다.
consult_request_view가 PRODUCT_CHOICES에 없는 값을 400 invalid_product로 거르기 때문이다.
choices만 바뀌므로 DB 스키마는 변하지 않는다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: /experts/ 페이지 신설 (히어로 + 전문가 프로필)

폼은 다음 태스크에서 붙인다. 이 태스크의 산출물은 "메뉴에서 도달 가능하고 프로필이 보이는 페이지"다.

**Files:**
- Modify: `articles/views/public.py`
- Modify: `articles/views/__init__.py:3-7`
- Modify: `config/urls.py` (import 블록과 `urlpatterns`)
- Create: `articles/templates/articles/expert_consult.html`
- Test: `articles/tests.py` (`ExpertConsultTests`)

**Interfaces:**
- Consumes: Task 1의 `ASSET` choices (이 태스크에서는 쓰지 않지만 Task 3이 쓴다)
- Produces: URL name `expert_consult` (`reverse('expert_consult')` → `/experts/`), 템플릿 `articles/expert_consult.html`. Task 3은 이 템플릿에 폼을 추가하고, Task 4는 이 URL name을 메뉴·사이트맵·접근로그에 등록한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`ExpertConsultTests` 클래스에 메서드를 추가한다:

```python
    def test_expert_consult_page_renders(self):
        response = self.client.get(reverse('expert_consult'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'articles/expert_consult.html')
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `venv/bin/python manage.py test articles.tests.ExpertConsultTests.test_expert_consult_page_renders -v 2 --settings=dev_sqlite_settings`

Expected: FAIL with `django.urls.exceptions.NoReverseMatch: Reverse for 'expert_consult' not found.`

- [ ] **Step 3: 뷰 추가**

`articles/views/public.py`의 `terms_of_service_view` 바로 아래에 추가한다:

```python
def expert_consult_view(request):
    return render(request, 'articles/expert_consult.html', {'site_title': 'NextFinUp - 전문가 상담'})
```

- [ ] **Step 4: 재export 추가**

`articles/views/__init__.py`의 `from .public import (...)` 블록에 `expert_consult_view`를 넣는다:

```python
from .public import (
    landing_page_view, main_dashboard_view, newsletter_subscribe_view, newsletter_unsubscribe_view,
    privacy_policy_view, terms_of_service_view, insurance_compare_view, consult_request_view,
    header_fragment_view, expert_consult_view,
)
```

- [ ] **Step 5: URL 등록**

`config/urls.py`의 `from articles.views import (...)` 블록에서 `header_fragment_view,`가 있는 줄 끝에 `expert_consult_view,`를 추가하고, `urlpatterns`의 `path('terms/', ...)` 아래에 라우트를 넣는다:

```python
    path('experts/', expert_consult_view, name='expert_consult'),
```

- [ ] **Step 6: 템플릿 작성**

`articles/templates/articles/expert_consult.html`을 만든다. 상담 폼 자리는 다음 태스크에서 채운다.

```html
{% load static %}
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ site_title }}</title>
    <meta name="description" content="국민연금부터 부동산까지, 자산 전체를 함께 점검해드립니다. 전문가에게 무료로 상담을 신청하세요.">
    {% include "articles/_favicon.html" %}
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background-color: #f4f6f9;
            color: #212529;
            font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }
        .ec-hero {
            background: linear-gradient(135deg, #0d47a1, #1565c0);
            color: #fff;
            padding: 64px 20px 72px;
            text-align: center;
        }
        .ec-hero h1 { font-size: 32px; font-weight: 800; margin: 0 0 14px; }
        .ec-hero p { font-size: 16px; color: #dce8fb; margin: 0 auto 28px; max-width: 560px; line-height: 1.7; }
        .ec-body { max-width: 860px; margin: -36px auto 80px; padding: 0 20px; }
        .ec-card {
            background: #fff;
            border-radius: 14px;
            box-shadow: 0 6px 18px rgba(0,0,0,0.06);
            padding: 36px 40px;
            margin-bottom: 28px;
        }
        .ec-card h2 { font-size: 19px; font-weight: 800; color: #0d47a1; margin: 0 0 20px; }
        /* 전문가 사진을 아직 받지 못했다. 사람 사진처럼 보이는 스톡 이미지를 쓰지 않기 위해
           이니셜을 넣은 중립적인 원형 도형으로 자리만 잡아둔다. 실제 사진을 받으면
           articles/static/articles/ 에 넣고 이 div를 <img>로 교체한다. */
        .ec-photo {
            width: 108px; height: 108px; border-radius: 50%;
            background: #e3ecfa; color: #0d47a1;
            display: flex; align-items: center; justify-content: center;
            font-size: 34px; font-weight: 800; flex: 0 0 auto;
        }
        .ec-profile { display: flex; gap: 28px; align-items: flex-start; flex-wrap: wrap; }
        .ec-profile-name { font-size: 22px; font-weight: 800; margin: 0 0 4px; }
        .ec-profile-title { font-size: 14px; color: #6c757d; margin: 0 0 18px; }
        .ec-profile dl { margin: 0; }
        .ec-profile dt { font-size: 13px; font-weight: 700; color: #0d47a1; margin-top: 14px; }
        .ec-profile dd { font-size: 14px; color: #343a40; margin: 4px 0 0; line-height: 1.7; }
        .ec-tags { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; }
        .ec-tag {
            background: #eef3fb; color: #0d47a1; border-radius: 999px;
            padding: 4px 12px; font-size: 12px; font-weight: 700;
        }
        .ec-quote {
            border-left: 3px solid #0d47a1; padding-left: 16px; margin: 22px 0 0;
            font-size: 14px; color: #495057; line-height: 1.8; font-style: italic;
        }
        /* 운영자가 채워야 하는 자리표시자를 눈에 띄게 표시한다 — 이 배경색이 화면에 보이면
           아직 실제 문구가 들어오지 않은 것이므로 운영 배포해서는 안 된다. */
        .ec-todo { background: #fff3cd; color: #664d03; padding: 0 4px; border-radius: 3px; }
        @media (max-width: 576px) {
            .ec-hero h1 { font-size: 25px; }
            .ec-card { padding: 26px 22px; }
        }
    </style>
</head>
<body>
{% include "articles/_header.html" %}

<div class="ec-hero">
    <h1>자산관리, 혼자 판단하지 마세요</h1>
    <p>국민연금·연금저축·IRP·ISA부터 보험·채권·부동산까지, 흩어진 자산을 한자리에서 함께 점검합니다.</p>
    <a href="#consult" class="btn btn-light btn-lg fw-bold">무료 상담 신청하기</a>
</div>

<div class="ec-body">
    <div class="ec-card">
        <h2>상담을 맡는 전문가</h2>
        <div class="ec-profile">
            <div class="ec-photo">NF</div>
            <div style="flex: 1; min-width: 260px;">
                <p class="ec-profile-name"><span class="ec-todo">[[전문가 성명]]</span></p>
                <p class="ec-profile-title"><span class="ec-todo">[[직함 — 예: 종합자산관리 FC]]</span></p>
                <dl>
                    <dt>보유 자격</dt>
                    <dd><span class="ec-todo">[[자격 명칭과 번호를 입력하세요]]</span></dd>
                    <dt>경력</dt>
                    <dd><span class="ec-todo">[[경력 연차와 주요 이력을 입력하세요]]</span></dd>
                    <dt>전문 분야</dt>
                    <dd>
                        <div class="ec-tags">
                            <span class="ec-tag">국민연금</span>
                            <span class="ec-tag">연금저축·IRP</span>
                            <span class="ec-tag">ISA</span>
                            <span class="ec-tag">보험 리모델링</span>
                            <span class="ec-tag">채권·부동산</span>
                        </div>
                    </dd>
                </dl>
                <p class="ec-quote"><span class="ec-todo">[[상담 철학 한마디를 입력하세요]]</span></p>
            </div>
        </div>
    </div>
</div>

{% include "articles/_cookie_banner.html" %}
</body>
</html>
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `venv/bin/python manage.py test articles.tests.ExpertConsultTests -v 2 --settings=dev_sqlite_settings`

Expected: PASS (2 tests)

- [ ] **Step 8: 실제 화면 확인**

Run: `venv/bin/python manage.py runserver 0.0.0.0:8000 --settings=dev_sqlite_settings` 실행 후 별 창에서
`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/experts/`

Expected: `200`. 브라우저에서 http://127.0.0.1:8000/experts/ 를 열어 상단 네비게이션, 히어로,
프로필 카드가 보이는지, 노란 배경 자리표시자가 표시되는지 눈으로 확인한다.

- [ ] **Step 9: 커밋**

```bash
git add articles/views/public.py articles/views/__init__.py config/urls.py articles/templates/articles/expert_consult.html articles/tests.py
git commit -m "$(cat <<'EOF'
Add /experts/ expert profile page

privacy_policy/terms와 같은 단순 render 뷰 + 독립 템플릿 패턴을 따른다.
전문가가 1명이라 모델을 만들지 않고 템플릿에 직접 작성한다.

프로필 문구와 사진은 실존 인물의 자격·실적이라 임의로 채우지 않았다. 노란 배경
자리표시자([[...]])로 남겨두었고, 이게 화면에 보이는 상태로 운영 배포해서는 안 된다.
사진 자리는 사람 사진으로 오인될 스톡 이미지 대신 이니셜 도형으로 두었다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 상담 폼을 페이지에 내장하고 /api/consult/에 연결

**Files:**
- Modify: `articles/templates/articles/expert_consult.html` (`.ec-body` 안, 프로필 카드 아래에 폼 카드 추가 + `<style>`에 폼 CSS + `</body>` 앞에 `<script>`)
- Test: `articles/tests.py` (`ExpertConsultTests`)

**Interfaces:**
- Consumes: Task 1의 `('ASSET', '자산관리 종합')`, Task 2의 템플릿 `articles/expert_consult.html`과 히어로의 `#consult` 앵커
- Produces: 없음 (마지막 소비자)

- [ ] **Step 1: 실패하는 테스트 작성**

`ExpertConsultTests`에 두 메서드를 추가한다. 하나는 템플릿이 폼을 실제로 담고 있는지, 하나는 허니팟 계약이다.

```python
    def test_expert_consult_page_contains_consult_form(self):
        response = self.client.get(reverse('expert_consult'))

        # 히어로 CTA가 가리키는 앵커와 폼 필드가 실제로 렌더링되는지
        self.assertContains(response, 'id="consult"')
        self.assertContains(response, 'id="ecName"')
        self.assertContains(response, 'id="ecPhone"')
        self.assertContains(response, 'id="ecWebsite"')  # 허니팟
        self.assertContains(response, '전체 자산 진단')   # 관심 분야 9번째 옵션
        self.assertContains(response, '/api/consult/')

    def test_consult_api_ignores_honeypot_submission(self):
        response = self.client.post(reverse('consult_request'), data={
            'product': 'ASSET',
            'name': '봇',
            'phone': '010-0000-0000',
            'website': 'http://spam.example.com',  # 허니팟에 값이 채워짐
        })

        # 봇에게 실패를 알리지 않으려고 ok:true를 돌려주지만 저장은 하지 않는다
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(ConsultRequest.objects.count(), 0)
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `venv/bin/python manage.py test articles.tests.ExpertConsultTests -v 2 --settings=dev_sqlite_settings`

Expected: `test_expert_consult_page_contains_consult_form`이 FAIL
(`Couldn't find 'id="consult"' in response`). `test_consult_api_ignores_honeypot_submission`은
서버 측 허니팟이 이미 구현돼 있어 PASS한다 — 기존 동작을 고정하는 회귀 테스트다.

- [ ] **Step 3: 폼 CSS 추가**

`expert_consult.html`의 `<style>` 안, `.ec-todo` 규칙 **아래**에 추가한다:

```css
        .ec-form label { font-size: 13px; font-weight: 700; color: #343a40; margin-bottom: 6px; }
        .ec-form .form-control, .ec-form .form-select { font-size: 14px; }
        /* 허니팟 — 사람에게는 보이지 않고 봇만 채우도록 화면 밖으로 밀어낸다.
           display:none은 일부 봇이 건너뛰므로 쓰지 않는다. */
        .ec-honeypot { position: absolute; left: -9999px; width: 1px; height: 1px; overflow: hidden; }
        .ec-alert { display: none; font-size: 14px; margin-bottom: 18px; }
        .ec-privacy { font-size: 12px; color: #6c757d; margin: 14px 0 0; line-height: 1.7; }
```

- [ ] **Step 4: 폼 마크업 추가**

`expert_consult.html`에서 프로필 카드(`</div>` 세 개로 닫히는 `ec-card`) 바로 아래, `.ec-body` div가 닫히기 **전에** 삽입한다:

```html
    <div class="ec-card" id="consult">
        <h2>상담 신청</h2>
        <div class="alert alert-danger ec-alert" id="ecError" role="alert"></div>
        <div class="alert alert-success ec-alert" id="ecSuccess" role="alert">
            신청이 접수되었습니다. 남겨주신 연락처로 순차적으로 연락드리겠습니다.
        </div>
        <form class="ec-form" id="ecForm" novalidate>
            <div class="row g-3">
                <div class="col-md-6">
                    <label for="ecName" class="form-label">이름 <span class="text-danger">*</span></label>
                    <input type="text" class="form-control" id="ecName" required maxlength="50" placeholder="홍길동">
                </div>
                <div class="col-md-6">
                    <label for="ecPhone" class="form-label">연락처 <span class="text-danger">*</span></label>
                    <input type="tel" class="form-control" id="ecPhone" required maxlength="20" placeholder="010-1234-5678">
                </div>
                <div class="col-md-6">
                    <label for="ecInterest" class="form-label">관심 분야</label>
                    <select class="form-select" id="ecInterest">
                        <option>국민연금</option>
                        <option>연금저축</option>
                        <option>IRP</option>
                        <option>ISA</option>
                        <option>보험</option>
                        <option>채권</option>
                        <option>부동산</option>
                        <option>청년저축</option>
                        <option selected>전체 자산 진단</option>
                    </select>
                </div>
                <div class="col-md-6">
                    <label for="ecGoal" class="form-label">상담 목표</label>
                    <input type="text" class="form-control" id="ecGoal" maxlength="200"
                           placeholder="예: 세액공제 한도를 다 채우고 싶어요">
                </div>
                <div class="col-12">
                    <label for="ecMessage" class="form-label">문의사항</label>
                    <textarea class="form-control" id="ecMessage" rows="3"
                              placeholder="궁금한 점을 자유롭게 남겨주세요."></textarea>
                </div>
                <div class="ec-honeypot">
                    <label for="ecWebsite">웹사이트</label>
                    <input type="text" id="ecWebsite" tabindex="-1" autocomplete="off">
                </div>
            </div>
            <button type="submit" class="btn btn-primary btn-lg w-100 mt-4 fw-bold" id="ecSubmit">상담 신청하기</button>
            <p class="ec-privacy">
                남겨주신 정보는 상담 목적으로만 사용하며, 자세한 내용은
                <a href="{% url 'privacy_policy' %}" target="_blank">개인정보처리방침</a>을 확인해주세요.
            </p>
        </form>
    </div>
```

- [ ] **Step 5: 전송 스크립트 추가**

`{% include "articles/_cookie_banner.html" %}` **위**에 추가한다:

```html
<script>
    // 상담 접수는 기존 /api/consult/ 엔드포인트가 검증·허니팟·관리자 메일 발송까지 전부
    // 처리한다. 그 뷰는 정적 HTML 호출부를 위해 csrf_exempt이므로 토큰을 보내지 않는다.
    document.getElementById('ecForm').addEventListener('submit', async function (e) {
        e.preventDefault();

        const errorBox = document.getElementById('ecError');
        const successBox = document.getElementById('ecSuccess');
        const submitBtn = document.getElementById('ecSubmit');
        errorBox.style.display = 'none';
        successBox.style.display = 'none';

        const name = document.getElementById('ecName').value.trim();
        const phone = document.getElementById('ecPhone').value.trim();
        if (!name || !phone) {
            errorBox.textContent = '이름과 연락처를 입력해주세요.';
            errorBox.style.display = 'block';
            return;
        }

        // 이중 제출 방지
        submitBtn.disabled = true;
        submitBtn.textContent = '접수 중...';

        try {
            const response = await fetch('/api/consult/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    product: 'ASSET',
                    name: name,
                    phone: phone,
                    interest: document.getElementById('ecInterest').value,
                    goal: document.getElementById('ecGoal').value.trim(),
                    message: document.getElementById('ecMessage').value.trim(),
                    website: document.getElementById('ecWebsite').value.trim(),
                }),
            });
            const data = await response.json();

            if (response.ok && data.ok) {
                successBox.style.display = 'block';
                this.reset();
            } else {
                errorBox.textContent = data.error === 'name_phone_required'
                    ? '이름과 연락처를 입력해주세요.'
                    : '접수에 실패했습니다. 잠시 후 다시 시도해주세요.';
                errorBox.style.display = 'block';
            }
        } catch (err) {
            // 네트워크 단절이나 JSON 파싱 실패
            errorBox.textContent = '통신에 실패했습니다. 잠시 후 다시 시도해주세요.';
            errorBox.style.display = 'block';
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = '상담 신청하기';
        }
    });
</script>
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `venv/bin/python manage.py test articles.tests.ExpertConsultTests -v 2 --settings=dev_sqlite_settings`

Expected: PASS (4 tests)

- [ ] **Step 7: 브라우저에서 실제 접수 확인**

개발서버를 띄운 상태에서 http://127.0.0.1:8000/experts/ 를 열고, 히어로의 "무료 상담 신청하기"가
폼으로 스크롤되는지 확인한 뒤 이름·연락처를 넣고 실제로 제출한다. 초록 성공 메시지가 뜨면
아래 명령으로 DB에 남았는지 확인한다:

```bash
venv/bin/python manage.py shell --settings=dev_sqlite_settings -c "
from articles.models import ConsultRequest
for c in ConsultRequest.objects.all():
    print(c.product, c.name, c.phone, c.interest)
"
```

Expected: `ASSET <입력한 이름> <입력한 연락처> <선택한 관심분야>` 한 줄.

- [ ] **Step 8: 커밋**

```bash
git add articles/templates/articles/expert_consult.html articles/tests.py
git commit -m "$(cat <<'EOF'
Wire /experts/ consult form to the real /api/consult/ endpoint

검증·허니팟·관리자 메일 발송이 이미 consult_request_view에 다 있어 재사용한다.
전용 POST 뷰를 새로 만들면 그 로직을 두 번째로 구현하게 되므로 택하지 않았다.

허니팟은 display:none 대신 화면 밖 배치로 숨긴다(일부 봇이 display:none을 건너뜀).
전송 중 버튼을 비활성화해 이중 제출을 막고, 400 응답과 fetch 실패를 구분해 안내한다.

asset-management 허브의 데모 폼은 손대지 않았다 — 결과적으로 상담 폼이 두 개 공존하며,
스펙에 의도된 잔여 불일치로 기록해두었다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 메뉴 시드 + 사이트맵 + 접근 로그 연동

페이지를 실제로 발견 가능하게 만드는 마지막 조각이다.

**Files:**
- Create: `articles/migrations/0055_seed_expert_consult_menu.py`
- Modify: `articles/sitemaps.py` (`StaticViewSitemap.pages`)
- Modify: `articles/middleware.py` (`MENU_URL_NAMES`)
- Test: `articles/tests.py` (`ExpertConsultTests`)

**Interfaces:**
- Consumes: Task 2의 URL name `expert_consult`
- Produces: 없음 (마지막 태스크)

- [ ] **Step 1: 실패하는 테스트 작성**

`ExpertConsultTests`에 추가한다. `Menu`를 파일 상단 import에 넣어야 한다:

```python
from .models import (
    AnalyzedArticle, ConsultRequest, MemberGrade, Menu, StockDailyPrice, StockItem,
    StockPrediction, UserPreference, UserSubscription,
)
```

```python
    def test_expert_consult_menu_is_seeded_for_both_menu_types(self):
        menus = Menu.objects.filter(name='전문가 상담')

        self.assertEqual(menus.count(), 2)
        self.assertEqual({m.menu_type for m in menus}, {'INDEX', 'HEADER'})
        for menu in menus:
            self.assertEqual(menu.url_name, 'expert_consult')
            self.assertEqual(menu.order, 9)
            self.assertTrue(menu.is_active)
            # url_name을 쓰므로 경로가 바뀌어도 메뉴가 따라간다
            self.assertEqual(menu.get_url(), reverse('expert_consult'))

    def test_expert_consult_page_is_in_sitemap(self):
        response = self.client.get(reverse('sitemap'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('expert_consult'))
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `venv/bin/python manage.py test articles.tests.ExpertConsultTests -v 2 --settings=dev_sqlite_settings`

Expected: 두 테스트 FAIL. 메뉴 쪽은 `AssertionError: 0 != 2`, 사이트맵 쪽은
`Couldn't find '/experts/' in response`.

- [ ] **Step 3: 메뉴 시드 마이그레이션 작성**

`articles/migrations/0055_seed_expert_consult_menu.py`를 만든다. `0050_seed_asset_management_menu.py`와
같은 `RunPython(forward, reverse)` 형태다.

```python
from django.db import migrations

# 자산관리(order=8) 오른쪽에 상담 유입 메뉴를 붙인다. external_url이 아니라 url_name을 쓰므로
# /experts/ 경로가 바뀌어도 Menu.get_url()의 reverse()가 따라간다.
EXPERT_CONSULT_MENU = {'name': '전문가 상담', 'url_name': 'expert_consult', 'order': 9}


def seed_menu(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    for menu_type in ('INDEX', 'HEADER'):
        Menu.objects.get_or_create(
            menu_type=menu_type, name=EXPERT_CONSULT_MENU['name'],
            defaults={
                'url_name': EXPERT_CONSULT_MENU['url_name'],
                'order': EXPERT_CONSULT_MENU['order'],
            },
        )


def remove_menu(apps, schema_editor):
    Menu = apps.get_model('articles', 'Menu')
    Menu.objects.filter(name=EXPERT_CONSULT_MENU['name']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0054_consultrequest_asset_product'),
    ]

    operations = [
        migrations.RunPython(seed_menu, remove_menu),
    ]
```

- [ ] **Step 4: 사이트맵 등록**

`articles/sitemaps.py`의 `StaticViewSitemap.pages` 리스트에서 `('news_board', 0.8),` 아래에 추가한다:

```python
        ('expert_consult', 0.7),
```

- [ ] **Step 5: 접근 로그 등록**

`articles/middleware.py`의 `MENU_URL_NAMES` 집합에 추가한다:

```python
MENU_URL_NAMES = {
    'landing_page',
    'main_dashboard',
    'my_page',
    'news_board',
    'news_detail',
    'stock_detail',
    'expert_consult',
}
```

- [ ] **Step 6: 마이그레이션 적용**

Run: `venv/bin/python manage.py migrate --settings=dev_sqlite_settings`

Expected: `Applying articles.0055_seed_expert_consult_menu... OK`

- [ ] **Step 7: 테스트 통과 확인**

Run: `venv/bin/python manage.py test articles.tests.ExpertConsultTests -v 2 --settings=dev_sqlite_settings`

Expected: PASS (6 tests)

- [ ] **Step 8: 전체 테스트로 회귀 확인**

Run: `venv/bin/python manage.py test articles --settings=dev_sqlite_settings`

Expected: OK, 23 tests (기존 17 + 신규 6)

- [ ] **Step 9: 메뉴가 실제로 보이는지 확인**

개발서버를 띄우고 확인한다:

```bash
curl -s http://127.0.0.1:8000/ | grep -o 'href="/experts/"[^>]*>[^<]*'
curl -s http://127.0.0.1:8000/partials/header/ | grep -o 'href="/experts/"[^>]*>[^<]*'
```

Expected: 두 명령 모두 `전문가 상담` 텍스트를 포함한 링크를 출력한다 (랜딩 = INDEX 메뉴,
헤더 프래그먼트 = HEADER 메뉴).

- [ ] **Step 10: 마이그레이션 되돌리기가 동작하는지 확인**

```bash
venv/bin/python manage.py migrate articles 0054 --settings=dev_sqlite_settings
venv/bin/python manage.py shell --settings=dev_sqlite_settings -c "
from articles.models import Menu; print('전문가 상담 행:', Menu.objects.filter(name='전문가 상담').count())
"
venv/bin/python manage.py migrate articles --settings=dev_sqlite_settings
```

Expected: 되돌린 뒤 `전문가 상담 행: 0`, 다시 적용하면 `0055` OK.

- [ ] **Step 11: 커밋**

```bash
git add articles/migrations/0055_seed_expert_consult_menu.py articles/sitemaps.py articles/middleware.py articles/tests.py
git commit -m "$(cat <<'EOF'
Seed 전문가 상담 menu and register /experts/ in sitemap and access log

메뉴는 external_url이 아니라 url_name으로 심어, 경로가 바뀌어도 Menu.get_url()의
reverse()가 따라가게 한다. 자산관리(order=8) 오른쪽인 order=9에 INDEX/HEADER 양쪽 배치.

새 공개 페이지를 추가할 때 이 프로젝트가 요구하는 두 곳(StaticViewSitemap.pages,
MENU_URL_NAMES)도 함께 등록했다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 배포 전 확인 (구현 완료 후)

- [ ] `articles/templates/articles/expert_consult.html`에 `ec-todo` 자리표시자가 남아 있는지 확인한다.
  `grep -c 'ec-todo' articles/templates/articles/expert_consult.html`이 0이 아니면 **운영 배포하지 않는다** —
  운영자에게 전문가 프로필 문구를 받아 채운 뒤 배포한다.
- [ ] 운영 배포 시 `python manage.py migrate`로 `0054`, `0055`가 MySQL에 적용되는지 확인한다.
- [ ] 별건으로 남긴 것: asset-management 허브의 데모 상담 폼. 같은 사이트에 접수되는 폼과
  접수되지 않는 폼이 공존하는 상태이므로 정리 여부를 결정해야 한다.
