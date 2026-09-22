"""/tools/ 유틸 목록의 단일 소스.

유틸이 17개까지 늘면서 상단 탭이 가로 스크롤 한 줄로는 훑어볼 수 없게 돼, 탭을 카테고리
2단(카테고리 줄 + 그 카테고리의 유틸 줄)으로 바꾸면서 만든 파일이다. 그 전에는 같은 목록이
_tools_tabs.html(탭)과 tools_hub.html(카드)에 두 벌로 하드코딩돼 있어서 유틸 하나를 추가할
때마다 두 곳을 따로 고쳐야 했고 설명 문구도 서로 어긋나기 쉬웠다.

이제 새 유틸을 추가할 때는 (1) 여기 CATEGORIES에 한 줄, (2) 뷰 함수, (3) urls.py 경로
세 곳만 건드리면 탭과 허브 카드가 함께 만들어진다.

각 항목:
    url_name — config/urls.py의 path name (템플릿에서 {% url %}로 쓰고, 활성 탭 판정 키가 된다)
    tab      — 탭에 쓰는 짧은 이름
    title    — 허브 카드 제목
    icon     — Font Awesome solid 아이콘 클래스
    desc     — 허브 카드 설명
"""

CATEGORIES = [
    {
        'key': 'finance',
        'label': '금융 계산기',
        'tools': [
            {
                'url_name': 'currency_converter',
                'tab': '환율 계산기',
                'title': '환율 계산기',
                'icon': 'fa-money-bill-transfer',
                'desc': '미국·일본·유럽 등 여행·송금에 자주 쓰는 11개국 환율로 원화와 상호 환산해볼 수 있어요.',
            },
            {
                'url_name': 'loan_calculator',
                'tab': '대출 이자',
                'title': '대출 이자 계산기',
                'icon': 'fa-building-columns',
                'desc': '원리금균등·원금균등·만기일시의 월 상환액과 총 이자를 비교하고 회차별 상환 스케줄까지 볼 수 있어요.',
            },
            {
                'url_name': 'savings_calculator',
                'tab': '예금·적금',
                'title': '예금·적금 이자 계산기',
                'icon': 'fa-piggy-bank',
                'desc': '정기예금·적금의 만기 수령액을 이자소득세 15.4%까지 공제해 계산하고 실제 수익률을 알려드려요.',
            },
            {
                'url_name': 'net_pay_calculator',
                'tab': '급여 실수령액',
                'title': '급여 실수령액 계산기',
                'icon': 'fa-sack-dollar',
                'desc': '연봉과 부양가족 수를 넣으면 4대보험료·소득세를 공제한 예상 월 실수령액을 계산해요.',
            },
            {
                'url_name': 'severance_calculator',
                'tab': '퇴직금',
                'title': '퇴직금 계산기',
                'icon': 'fa-briefcase',
                'desc': '입사일·퇴사일과 최근 3개월 급여만 넣으면 근로기준법 기준 평균임금과 예상 퇴직금을 계산해요.',
            },
            {
                'url_name': 'unemployment_benefit_calculator',
                'tab': '실업급여',
                'title': '실업급여 계산기',
                'icon': 'fa-hand-holding-dollar',
                'desc': '나이·가입기간·최근 급여를 넣으면 고용보험법 기준 구직급여 일액과 총 예상 수급액을 계산해요.',
            },
            {
                'url_name': 'korean_amount',
                'tab': '한글 금액',
                'title': '한글 금액 변환기',
                'icon': 'fa-won-sign',
                'desc': '숫자를 계약서용 ‘일금 삼천오백만원정’ 표기로 바꿔드려요. 갖은자 한자·영문 표기와 반대 변환도 됩니다.',
            },
            {
                'url_name': 'annual_leave_calculator',
                'tab': '연차수당',
                'title': '연차수당 계산기',
                'icon': 'fa-umbrella-beach',
                'desc': '입사일과 통상임금을 넣으면 근로기준법 기준 발생 연차일수와 미사용 연차수당을 계산해요.',
            },
        ],
    },
    {
        'key': 'file',
        'label': '이미지·파일',
        'tools': [
            {
                'url_name': 'image_resizer',
                'tab': '이미지 리사이저',
                'title': '이미지 리사이저',
                'icon': 'fa-image',
                'desc': '이미지 크기를 조정하고 PNG·JPEG·WEBP로 확장자도 함께 변환할 수 있어요.',
            },
            {
                'url_name': 'image_converter',
                'tab': '확장자 변환',
                'title': '이미지 확장자 변환',
                'icon': 'fa-right-left',
                'desc': 'HEIC·JPG·PNG·WEBP·BMP를 서로 바꿔드려요. 여러 장을 한 번에 변환하고 ZIP으로 받을 수 있어요.',
            },
            {
                'url_name': 'image_compressor',
                'tab': '용량 줄이기',
                'title': '이미지 용량 줄이기',
                'icon': 'fa-compress',
                'desc': '"500KB 이하로" 목표 용량만 정하면 화질을 자동으로 맞춰 사진 용량을 줄여드려요. 여러 장을 한 번에 줄이고 ZIP으로 받을 수 있어요.',
            },
            {
                'url_name': 'image_mosaic',
                'tab': '모자이크',
                'title': '모자이크 · 가리기',
                'icon': 'fa-eye-slash',
                'desc': '사진에서 가릴 곳을 드래그하면 모자이크·블러·단색 칠로 지워드려요. 주민번호·계좌번호·얼굴 가리기에 쓰세요.',
            },
            {
                'url_name': 'background_remover',
                'tab': '배경 제거',
                'title': '배경 제거',
                'icon': 'fa-eraser',
                'desc': 'AI가 브라우저에서 바로 배경을 지워 투명 PNG(누끼)로 만들어드려요. 첫 사용 시 모델 다운로드로 다소 시간이 걸려요.',
            },
            {
                'url_name': 'screen_recorder',
                'tab': '화면 녹화',
                'title': '화면 녹화기',
                'icon': 'fa-video',
                'desc': '프로그램 설치 없이 브라우저만으로 화면을 녹화해요. 시스템 소리·마이크도 함께 담을 수 있어요.',
            },
            {
                'url_name': 'pdf_tools',
                'tab': 'PDF 도구',
                'title': 'PDF 도구',
                'icon': 'fa-file-pdf',
                'desc': '여러 PDF를 하나로 합치고, 필요한 페이지만 뽑아내거나 삭제하고, 이미지 여러 장을 PDF로 묶을 수 있어요.',
            },
            {
                'url_name': 'qrcode_generator',
                'tab': 'QR코드',
                'title': 'QR코드 생성기',
                'icon': 'fa-qrcode',
                'desc': '텍스트나 URL을 입력하면 QR코드를 바로 만들어 PNG로 다운로드할 수 있어요.',
            },
        ],
    },
    {
        'key': 'text',
        'label': '텍스트·기타',
        'tools': [
            {
                'url_name': 'char_counter',
                'tab': '글자수 세기',
                'title': '글자수 세기',
                'icon': 'fa-font',
                'desc': '공백 포함/제외 글자수, 바이트, 단어·줄·문단 수를 실시간으로 확인하고 글자수 제한도 체크할 수 있어요.',
            },
            {
                'url_name': 'reading_time',
                'tab': '낭독 시간',
                'title': '낭독·발표 시간 계산기',
                'icon': 'fa-microphone-lines',
                'desc': '원고를 넣고 말하기 속도(1분에 몇 자)를 정하면 낭독에 몇 분이 걸리는지, 목표 시간에 맞추려면 몇 자여야 하는지 알려드려요.',
            },
            {
                'url_name': 'spell_checker',
                'tab': '맞춤법 검사',
                'title': '맞춤법 검사기',
                'icon': 'fa-spell-check',
                'desc': 'AI가 맞춤법·띄어쓰기 오류를 찾아 교정해드려요. 다른 유틸과 달리 텍스트가 서버로 전송됩니다.',
            },
            {
                'url_name': 'text_diff',
                'tab': '텍스트 비교',
                'title': '텍스트 비교 (Diff)',
                'icon': 'fa-code-compare',
                'desc': '두 글을 나란히 놓고 달라진 줄과 그 안에서 바뀐 단어까지 짚어드려요. 계약서·원고 수정본 대조에 좋아요.',
            },
            {
                'url_name': 'json_formatter',
                'tab': 'JSON 포맷터',
                'title': 'JSON 포맷터',
                'icon': 'fa-code',
                'desc': 'JSON 텍스트를 예쁘게 정렬하거나 한 줄로 압축하고, 문법 오류가 있으면 바로 알려드려요.',
            },
            {
                'url_name': 'world_clock',
                'tab': '세계 시간',
                'title': '세계 시간 변환기',
                'icon': 'fa-globe',
                'desc': '해외 회의·통화 시간을 도시별로 비교하고, 주요 증시 개장·마감 시각을 한국 시간으로 확인할 수 있어요.',
            },
            {
                'url_name': 'unit_converter',
                'tab': '단위 변환기',
                'title': '단위 변환기',
                'icon': 'fa-ruler-combined',
                'desc': '길이·무게·넓이(평 포함)·부피·속도·온도를 서로 다른 단위로 바로 환산해드려요.',
            },
        ],
    },
]


# 허브(/tools/) 자체도 검색 대상이라 같은 자리에서 제목/설명을 관리한다.
HUB = {
    'url_name': 'tools_hub',
    'title': '무료 온라인 유틸 모음',
    'desc': '환율·대출·퇴직금 계산기부터 이미지 용량 줄이기·PDF·모자이크·텍스트 비교까지, 설치 없이 브라우저에서 바로 쓰는 무료 도구 모음입니다.',
}


def tool_by_url_name(url_name):
    """url_name으로 유틸 항목을 찾는다(허브는 'hub'/'tools_hub' 둘 다 받는다).
    <meta name="description">·canonical을 만드는 tool_meta 태그가 쓴다 — 설명 문구를
    카드/탭과 한 곳에서 관리하려는 것."""
    if url_name in ('hub', 'tools_hub'):
        return HUB
    for category in CATEGORIES:
        for tool in category['tools']:
            if tool['url_name'] == url_name:
                return tool
    return None


def category_of(url_name):
    """활성 유틸이 속한 카테고리 key를 돌려준다(없으면 None) — 탭이 열릴 때 그 카테고리 줄을
    펼쳐 보여주기 위한 것."""
    for category in CATEGORIES:
        for tool in category['tools']:
            if tool['url_name'] == url_name:
                return category['key']
    return None
