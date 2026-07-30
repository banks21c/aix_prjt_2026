# 전문가 상담 페이지 설계

작성일: 2026-07-30

## 배경

상단 메뉴에 전문가를 소개하는 항목을 만들고, 그 페이지에서 자산관리 상담 신청까지
이어지게 한다.

현재 상태를 조사한 결과 두 가지가 확인됐다.

1. **상담 접수 인프라는 이미 동작한다.** `ConsultRequest` 모델과 `/api/consult/`
   엔드포인트(`consult_request_view`)가 있고, 검증·허니팟 스팸 필터·DB 저장·관리자
   알림 메일 발송까지 처리한다.
2. **정작 asset-management 허브의 상담 폼은 데모다.** `submitConsult()`가 아무것도
   전송하지 않고 성공 메시지만 표시하며, 화면에도 "데모 화면 — 실제 접수는 되지
   않습니다"라고 적혀 있다.

따라서 "상담으로 연결"은 링크를 거는 문제가 아니라, 실제 접수되는 폼을 어디에 둘지
결정하는 문제다.

## 결정 사항

| 항목 | 결정 |
|---|---|
| 페이지 성격 | 전문가 소개 중심 (경력·자격으로 신뢰를 만들고 상담으로 유도) |
| 상담 연결 방식 | 소개 페이지 하단에 실제 폼을 내장하고 기존 `/api/consult/`에 물린다 |
| 전문가 데이터 | 1명, 템플릿에 직접 작성 (모델·관리자 화면 만들지 않음) |
| 메뉴명 | `전문가 상담` |
| 메뉴 노출 | 랜딩(INDEX)과 내부 헤더(HEADER) 양쪽, `order=9` |
| 상담 분류 | `PRODUCT_CHOICES`에 `ASSET`(자산관리 종합) 추가 |
| URL 경로 | `/experts/` |

### 채택하지 않은 대안

- **전용 POST 뷰 + Django Form** — JS 없이 동작하고 CSRF가 적용되는 장점이 있으나,
  `consult_request_view`의 검증·허니팟·메일 발송 로직을 두 번째로 구현하게 된다.
  공통 로직 추출 리팩터링이 함께 필요해 비용이 크고, 이 사이트 방문자 환경에서 얻는
  이득이 작다.
- **허브 폼 마크업 복사** — asset-management의 CSS 체계가 Django 템플릿의 부트스트랩
  기반과 별개라 스타일이 충돌하고, 복사본이 원본과 갈라진다.
- **전용 모델 분리** — 필드가 `ConsultRequest`와 거의 같아 중복이고, 상담 내역을 관리자
  화면 두 곳에서 나눠 보게 된다.

## 범위

### 포함

- 공개 페이지 `/experts/` 신설 (뷰 + 템플릿)
- 실제 접수되는 상담 폼 (기존 `/api/consult/` 재사용)
- `ConsultRequest.PRODUCT_CHOICES`에 `ASSET` 추가
- `Menu` 행 2개 시드 (INDEX·HEADER)
- 사이트맵·메뉴 접근 로그 연동
- 테스트 4건

### 제외

- asset-management 허브의 데모 상담 폼은 수정하지 않는다. 결과적으로 사이트에 상담 폼이
  두 개(`/experts/`는 실제 접수, 허브는 데모) 공존한다. **의도된 잔여 불일치**이며 별건으로
  정리해야 한다.
- 전문가를 여러 명 관리하는 모델·관리자 화면
- 상담 진행 상태 관리(배정·완료 처리 등) 기능

## 라우팅과 뷰

`config/urls.py`:

```python
path('experts/', expert_consult_view, name='expert_consult'),
```

`articles/views/public.py`에 추가한다. 이 프로젝트의 공개 안내 페이지 패턴
(`terms_of_service_view`, `privacy_policy_view`)을 그대로 따르는 단순 `render` 뷰다.

```python
def expert_consult_view(request):
    return render(request, 'articles/expert_consult.html',
                  {'site_title': 'NextFinUp - 전문가 상담'})
```

`articles/views/__init__.py`에서 재export하고, `config/urls.py`의 import 목록에 추가한다.

## 템플릿 구조

`articles/templates/articles/expert_consult.html`, 위에서 아래로 3블록.

| 블록 | 내용 |
|---|---|
| ① 히어로 | 헤드라인 + 상담 폼으로 앵커 이동하는 CTA 버튼 |
| ② 전문가 프로필 | 사진 / 이름·직함 / 보유 자격 / 경력 요약 / 전문 분야 태그 / 상담 철학 한마디 |
| ③ 상담 폼 | `id="consult"` — ①의 CTA 대상 |

기존 템플릿과 같은 부트스트랩 기반 마크업을 쓰고, 공통 헤더/푸터 포함 방식도 다른
공개 페이지와 동일하게 맞춘다.

### 프로필 문구는 구현 시 입력받는다

②의 실제 문구(성명, 직함, 자격 명칭·번호, 경력 연차와 이력, 전문 분야)는 **사이트
운영자가 제공한다.** 실존 인물의 자격과 실적을 다루는 내용이므로 임의로 생성하지
않는다.

문구를 받기 전까지 템플릿에는 눈에 띄게 표시된 자리표시자(예:
`[[전문가 성명을 입력하세요]]`)를 넣는다. 자리표시자가 남은 상태로 운영 배포하면
안 되며, 배포 전 확인해야 한다.

프로필 사진은 `articles/static/articles/`에 두고 `{% static %}`으로 참조한다.
사진을 받지 못한 상태에서는 **사람 사진처럼 보이는 스톡 이미지를 쓰지 않는다** —
이니셜을 넣은 원형 도형 같은 중립적 도형으로 자리를 잡아, 실제 인물 사진으로
오인될 여지를 남기지 않는다.

## 상담 폼

### 필드 매핑

기존 `ConsultRequest` 필드에 그대로 대응시켜 모델에 새 컬럼을 만들지 않는다.

| 폼 입력 | ConsultRequest 필드 | 필수 | 비고 |
|---|---|---|---|
| (고정값) | `product` | 예 | `'ASSET'`. 숨김 필드로 전송, 사용자가 고르지 않음 |
| 이름 | `name` | 예 | 서버가 50자로 절단 |
| 연락처 | `phone` | 예 | 서버가 20자로 절단 |
| 관심 분야 (select) | `interest` | 아니오 | 아래 9개 옵션. 서버가 100자로 절단 |
| 상담 목표 | `goal` | 아니오 | 서버가 200자로 절단 |
| 문의사항 | `message` | 아니오 | textarea |
| 웹사이트 (허니팟) | — | — | 필드명 `website`. 채워지면 서버가 저장하지 않고 `ok:true` 반환 |

관심 분야 select 옵션은 asset-management 허브 폼과 동일하게 맞춘다:
국민연금, 연금저축, IRP, ISA, 보험, 채권, 부동산, 청년저축, 전체 자산 진단.

### 데이터 흐름

```
폼 submit
  → fetch('/api/consult/', method POST, Content-Type: application/json,
           body {product:'ASSET', name, phone, interest, goal, message, website})
  → consult_request_view: 허니팟 검사 → product/name/phone 검증
                        → ConsultRequest 생성 → 관리자 알림 메일 발송
  → {ok:true}  : 폼 아래 성공 메시지 표시 후 form.reset()
    400 {ok:false, error}: 폼 위에 오류 문구 표시
```

CSRF 토큰은 전송하지 않는다. `consult_request_view`는 정적 HTML 호출부를 위해 이미
`csrf_exempt`로 설계돼 있고, 스팸 방어는 같은 뷰의 허니팟이 담당한다.

## 모델 변경

`articles/models/content.py`의 `ConsultRequest.PRODUCT_CHOICES`에 항목을 추가한다.

```python
('ASSET', '자산관리 종합'),
```

이 값이 없으면 `consult_request_view`의
`if product not in dict(ConsultRequest.PRODUCT_CHOICES)` 검사에서 400
(`invalid_product`)으로 튕긴다.

## 마이그레이션

기존 번호 다음 두 개를 추가한다 (현재 최신은 `0053`).

1. `0054_consultrequest_asset_product` — `AlterField(ConsultRequest.product)`.
   `choices` 변경이므로 DB 스키마는 바뀌지 않는다.
2. `0055_seed_expert_consult_menu` — `Menu` 행 2개 시드. `0050_seed_asset_management_menu`와
   같은 `RunPython(forward, reverse)` 형태로, reverse에서 삭제해 되돌릴 수 있게 한다.

시드할 `Menu` 행:

| menu_type | name | url_name | external_url | order |
|---|---|---|---|---|
| INDEX | 전문가 상담 | `expert_consult` | (빈 값) | 9 |
| HEADER | 전문가 상담 | `expert_consult` | (빈 값) | 9 |

`url_name`을 쓰므로 `Menu.get_url()`이 `reverse()`로 경로를 만든다. 경로가 바뀌어도
메뉴가 따라간다.

## 연동 지점

새 공개 페이지를 추가할 때 이 프로젝트가 요구하는 두 곳을 함께 수정한다.

- `articles/sitemaps.py` — `StaticViewSitemap.pages`에 `('expert_consult', 0.7)` 추가.
  검색 유입이 목적인 공개 페이지다.
- `articles/middleware.py` — `MENU_URL_NAMES`에 `'expert_consult'` 추가.
  `MenuAccessLogMiddleware`의 집계 대상이 된다.

## 에러 처리

서버 측은 이미 갖춰져 있어 새로 만들 것이 없다.

- 잘못된 product / 이름·연락처 누락 → 400 JSON (`invalid_product`,
  `name_phone_required`)
- 관리자 메일 발송 실패 → `fail_silently=True` + `logger.exception`으로 신청 저장과
  분리. 메일이 안 가도 신청은 남는다.
- 허니팟 채워짐 → 저장하지 않고 `ok:true` 반환 (봇에게 실패를 알리지 않는다)

프런트에 추가할 것은 세 가지다.

- 400 응답 시 폼 위에 오류 문구 표시
- `fetch` 자체 실패(네트워크 단절) 시 "잠시 후 다시 시도해주세요" 표시
- 전송 중 submit 버튼 비활성화로 이중 제출 방지

## 테스트

`articles/tests.py`에 추가한다. CI(`.github/workflows/ci.yml`)가 MySQL 컨테이너로
실행한다.

1. `GET /experts/` → 200, `articles/expert_consult.html` 사용
2. `product='ASSET'`로 `/api/consult/` POST → `ConsultRequest` 1행 생성, `product`가
   `'ASSET'`, `interest`가 전송값과 일치
3. 허니팟(`website`) 채운 POST → 행 생성 없음, 응답은 `ok:true`
4. `GET /sitemap.xml` → 본문에 `/experts/` 포함

## 완료 기준

- `/experts/`가 200으로 열리고 3블록이 모두 렌더링된다
- 폼 제출이 `ConsultRequest`에 `product='ASSET'` 행을 남긴다
- 랜딩과 내부 헤더 양쪽에 `전문가 상담` 메뉴가 자산관리 오른쪽에 표시된다
- 테스트 4건과 기존 테스트 전체가 통과한다
- 프로필 자리표시자가 남아 있는지 배포 전 확인한다
