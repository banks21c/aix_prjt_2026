"""여러 장을 서로 일관되게 이어 생성하는 "시리즈 생성" 기능의 공유 로직.

generate_image_series 커맨드(실제 생성)와 admin_tools 뷰(화면에서 백그라운드로 띄우고 진행률을
폴링) 양쪽이 함께 쓰는 것들만 모아둔다 — blog_posting.py가 post_to_* 커맨드와 수동 발행 뷰
사이에서 하는 역할과 같다. 커맨드가 뷰를 import하고 있어서, 뷰가 다시 커맨드를 import하면
순환이 되기 때문에 공유분은 반드시 여기에 둔다.

핵심 아이디어: 같은 프롬프트를 N번 호출하면 매 호출이 독립이라 인물·화풍이 장마다 달라진다.
그래서 앞 장의 "이미지 자체"를 다음 장 호출에 레퍼런스로 함께 넘겨 고정한다.
"""
import json
import re
from pathlib import Path

from django.conf import settings

# 장을 나누는 구분자. 줄 맨 앞의 `#1 ` / `#1.` / `# 1)` 등을 모두 받아준다.
SCENE_MARKER = re.compile(r'^\s*#\s*(\d+)\s*[.):]?\s*', re.MULTILINE)

# 한 번에 돌릴 수 있는 최대 장 수. 실수로 수십 장짜리를 붙여넣어 요금이 크게 나가는 것과,
# 백그라운드 프로세스가 수십 분씩 도는 것을 함께 막는 상한이다.
MAX_SCENES = 12

# 레퍼런스로 넘길 이미지를 고르는 방식.
#   first+prev : 1번(캐릭터 기준)과 직전 장을 함께 넘긴다 — 인물 고정과 장면 연속성을 둘 다 잡는
#                기본값. 1번에 없던 인물이 2번에서 등장해도 직전 장을 통해 이어진다.
#   first      : 항상 1번만. 장면이 크게 바뀌어도 1번 화풍만 따라가게 하고 싶을 때.
#   prev       : 직전 장만. 순차적으로 흘러가는 컷 연결에 유리하나, 여러 장을 지나며 인물이
#                조금씩 흘러가는(drift) 위험이 가장 크다.
#   all        : 지금까지 만든 전부. 가장 강하게 고정되지만 입력 토큰이 장마다 늘어 비용도 는다.
REF_MODES = ['first+prev', 'first', 'prev', 'all']
REF_MODE_LABELS = [
    ('first+prev', '1번 + 직전 장 (권장 — 인물 고정과 장면 연속성 모두)'),
    ('first', '1번만 (첫 장의 화풍만 따라가게)'),
    ('prev', '직전 장만 (컷 연결 우선, 인물이 서서히 흘러갈 수 있음)'),
    ('all', '지금까지 전부 (가장 강하게 고정, 입력 토큰·비용 증가)'),
]
DEFAULT_REF_MODE = 'first+prev'

REFERENCE_INSTRUCTION = (
    "Use the attached reference image(s) as the definitive visual reference for the recurring "
    "characters, their faces, hair, clothing, the setting, and the lighting and color grading. "
    "Keep them consistent with the references. Render this new scene:\n\n"
)


def split_prompts(text):
    """`#1 ...` 형식의 본문을 장별 프롬프트 리스트로 나눈다. 구분자가 없으면 빈 리스트."""
    parts = SCENE_MARKER.split(text)
    # split 결과는 [머리말, 번호, 본문, 번호, 본문, ...] — 머리말은 버린다.
    if len(parts) < 3:
        return []
    return [body for i in range(1, len(parts) - 1, 2) if (body := parts[i + 1].strip())]


def reference_indexes(idx, ref_mode):
    """idx번째 장을 만들 때 레퍼런스로 넘길 앞 장들의 인덱스(0-based, 순서 유지)."""
    if idx == 0:
        return []
    if ref_mode == 'first':
        return [0]
    if ref_mode == 'prev':
        return [idx - 1]
    if ref_mode == 'all':
        return list(range(idx))
    return [0] if idx == 1 else [0, idx - 1]   # first+prev


def series_job_dir():
    """진행 상황 사이드카와 프롬프트 파일을 두는 곳. 기존 파이프라인 로그와 같은 logs/ 아래."""
    path = Path(settings.BASE_DIR) / 'logs'
    path.mkdir(exist_ok=True)
    return path


def series_progress_path(series_key):
    return series_job_dir() / f'image_series_{series_key}.json'


def series_prompt_path(series_key):
    return series_job_dir() / f'image_series_{series_key}.txt'


def write_progress(series_key, **fields):
    """진행 상황 사이드카를 갱신한다(기존 내용에 병합).

    GeneratedImage 행만 세면 "총 몇 장인지"와 "실패/중단"을 알 수 없어 따로 둔다. pid를 함께
    적는 건, 프로세스가 OOM 등으로 죽으면 finished가 영영 False로 남아 화면이 무한히
    "생성 중"으로 보이기 때문 — 읽는 쪽에서 pid 생존을 확인해 중단으로 판정한다.
    """
    if not series_key:
        return
    path = series_progress_path(series_key)
    state = read_progress(series_key) or {}
    state.update(fields)
    # 원자적 교체 — 화면이 폴링으로 아무 때나 읽으므로 반쯤 쓰인 파일을 보면 안 된다.
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


def read_progress(series_key):
    """사이드카를 읽어 dict로. 아직 없거나 깨졌으면 None."""
    if not series_key:
        return None
    path = series_progress_path(series_key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None
