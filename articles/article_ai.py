import base64
import json
import logging

from django.conf import settings
from openai import BadRequestError, OpenAI

logger = logging.getLogger(__name__)

# 기사 3줄 요약/투자 분석/블로그 초안(generate_draft)에 쓰는 모델. 한때 Gemini(gemini-flash-latest)를
# 썼으나 무료 티어 일일 한도(gemini-3.6-flash 기준 20건/일)가 실사용 중 반복적으로 소진돼(429
# RESOURCE_EXHAUSTED) generate_featured_briefing/썸네일 생성과 같은 OpenAI로 통일했다.
DRAFT_MODEL = "gpt-4o-mini"

SIMULATION_SUMMARY = (
    "AI 요약 기능은 현재 준비 중입니다. 관리자가 AI 요약용 API 키를 설정하면 "
    "실시간 AI 요약이 제공됩니다. (현재 시뮬레이션 모드)"
)
SIMULATION_ANALYSIS = "AI 관점 분석 기능은 현재 준비 중입니다. (현재 시뮬레이션 모드)"
ERROR_SUMMARY = "일시적인 오류로 AI 요약을 생성하지 못했습니다. 아래 원문을 참고해 직접 작성해주세요."
ERROR_ANALYSIS = "일시적인 오류로 AI 분석을 생성하지 못했습니다."

SYSTEM_PROMPT = """당신은 NextFinUp의 금융 뉴스 AI 에디터입니다. 주어진 기사 원문을 바탕으로
블로그(티스토리/워드프레스 등) 포스팅용 원고를 작성합니다. 애드센스 등 광고 심사를 통과하려면
너무 짧은 콘텐츠는 안 되므로, blog_content는 A4 용지 한 장 분량(공백 포함 한글 1,800~2,500자
정도)의 충실한 글로 작성하세요.
- [원문]에 없는 특정 수치·실적·사건을 지어내거나 추측하지 마세요. 분량을 채우기 위한 방법은
  사실 날조가 아니라, [원문] 내용을 여러 섹션으로 나눠 자세히 풀어 설명하고(무슨 일이 있었는지,
  왜 중요한지), 기사에 언급된 용어·산업/기업 배경을 독자가 이해하기 쉽게 일반적인 지식 수준에서
  보충 설명하는 것입니다.
- [원문]이 실제로 투자·증시·기업 실적 등과 관련된 내용이면 투자자 관점의 시사점과 유의할 점을
  충분히 서술하세요. 반대로 문학·문화·인물 소개 등 투자와 무관한 내용이라면, 억지로 재무/투자
  용어에 빗대거나 없는 투자 시사점을 지어내지 마세요 — 그 경우 ai_analysis와 blog_content의
  해당 섹션에는 원문 자체의 핵심 의미나 시사점을 다루고, 투자와는 직접 관련이 없는 내용임을
  자연스럽게 밝히세요.
- [제목]을 그대로 베끼지 말고, 같은 핵심 의미는 유지하면서 더 명확하고 자연스러운 한국어 제목으로
  다듬으세요. 원문에 없는 사실을 새로 넣거나 과장하지 말고, 자극적인 낚시성 표현("충격", "경악",
  느낌표 남발 등)도 쓰지 마세요. [연재 회차 번호]나 [카테고리 태그] 같은 대괄호 장식, 매체명
  반복은 정리해도 되지만, 제목에 속담·관용구(예: "아는 길도 물어가라")가 들어있으면 그건 그
  기사의 핵심 비유·주제이므로 절대 빼지 말고 다듬은 제목에 그대로 살리거나 자연스럽게 녹여내세요.
- 한국어로, 명확하고 자연스럽게 작성하세요. 같은 문장을 반복하며 억지로 늘리지 마세요.
- 반드시 아래 JSON 형식으로만 답하세요. 그 외 설명이나 마크다운 코드블록은 절대 붙이지 마세요.
{
  "ai_title": "원문 제목을 다듬은 한 줄 제목 문자열",
  "ai_summary": "핵심 내용을 3줄로 요약한 문자열 (줄바꿈 문자로 구분)",
  "ai_analysis": "투자 관련 내용이면 투자자 관점의 시사점을, 아니면 원문 자체의 핵심 시사점을 3~5문장으로 분석한 문자열",
  "blog_content": "블로그 포스팅용 본문 HTML. <h3>/<p>/<ul><li> 등 간단한 태그만 사용해 여러 소제목 섹션(기사 핵심 정리, 배경/맥락, 시사점 등)으로 구성하고 공백 포함 한글 1,800~2,500자 분량으로 작성"
}
"""

# utils.detect_reuse_restriction이 원문에서 "무단전재 배포금지, AI 학습 및 활용 금지" 류 문구를
# 감지한 기사용 시스템 프롬프트. 이런 기사는 원문 본문을 절대 프롬프트에 넣지 않고(호출부에서부터
# content 자체를 전달하지 않음) 제목/구조화된 사실(관련 종목명 등)만으로 NextFinUp 자체 해설을
# 작성하게 한다 — 원문 문장의 재구성/의역이 아니라 독자적인 배경 설명·투자 시사점 위주 원고.
RESTRICTED_SYSTEM_PROMPT = """당신은 NextFinUp의 금융 뉴스 AI 에디터입니다. 이 기사는 언론사가
'무단전재 배포금지, AI 학습 및 활용 금지'를 명시해 원문 본문을 전달받지 못했습니다. 당신에게는
[기사 제목]과 [참고 정보](관련 종목 등 구조화된 사실)만 주어집니다.
- 원문 본문을 읽은 적이 없다는 전제로 작성하세요. [기사 제목]/[참고 정보]에 없는 구체적인 수치·
  발언·사건 경위를 원문에서 가져온 것처럼 지어내거나 추측하지 마세요.
- 특정 문장을 재구성하거나 의역하려 하지 말고, 제목이 가리키는 주제에 대해 일반적으로 알려진
  배경지식과 맥락 위주로 NextFinUp만의 독자적인 해설을 작성하세요. 제목이 실제로 투자·증시·
  기업과 관련된 주제면 산업/기업 맥락과 투자자 관점에서 통상적으로 짚어볼 시사점·유의점을
  다루고, 문학·문화·인물 소개 등 투자와 무관한 주제라면 억지로 재무/투자 용어에 빗대지 말고
  주제 자체의 핵심 의미를 다루세요. "기사에 따르면", "원문에서는" 같이 원문을 인용하는 듯한
  표현은 쓰지 마세요.
- [기사 제목]을 그대로 베끼지 말고, 같은 주제·핵심 의미는 유지하면서 더 명확하고 자연스러운
  한국어 제목으로 다듬으세요. 자극적인 낚시성 표현("충격", "경악", 느낌표 남발 등)은 쓰지 마세요.
  [연재 회차 번호]나 [카테고리 태그] 같은 대괄호 장식은 정리해도 되지만, 제목에 속담·관용구
  (예: "아는 길도 물어가라")가 들어있으면 그건 그 기사의 핵심 비유·주제이므로 절대 빼지 말고
  다듬은 제목에 그대로 살리거나 자연스럽게 녹여내세요.
- 한국어로, 명확하고 자연스럽게 작성하세요. 같은 문장을 반복하며 억지로 늘리지 마세요.
- 반드시 아래 JSON 형식으로만 답하세요. 그 외 설명이나 마크다운 코드블록은 절대 붙이지 마세요.
{
  "ai_title": "기사 제목을 다듬은 한 줄 제목 문자열",
  "ai_summary": "제목이 가리키는 주제를 3줄로 정리한 문자열 (줄바꿈 문자로 구분, 원문 문장 재구성 금지)",
  "ai_analysis": "투자 관련 주제면 투자자 관점의 시사점을, 아니면 주제 자체의 핵심 의미를 3~5문장으로 정리한 문자열",
  "blog_content": "블로그 포스팅용 본문 HTML. <h3>/<p>/<ul><li> 등 간단한 태그만 사용해 배경/맥락/시사점 위주 섹션으로 구성하고(원문 인용·재구성 없이) 공백 포함 한글 1,200~1,800자 분량으로 작성"
}
"""


def _simulation_draft(content):
    return {
        'ai_title': '',
        'ai_summary': SIMULATION_SUMMARY,
        'ai_analysis': SIMULATION_ANALYSIS,
        'blog_content': f"<p>{(content or '')[:300]}</p>",
    }


def _error_draft(content):
    return {
        'ai_title': '',
        'ai_summary': ERROR_SUMMARY,
        'ai_analysis': ERROR_ANALYSIS,
        'blog_content': f"<p>{(content or '')[:300]}</p>",
    }


def _call_openai_json(system_prompt, user_prompt, max_tokens):
    """OpenAI Chat Completions에 system+user 메시지를 보내 JSON 객체로 파싱해 반환한다.
    response_format=json_object로 JSON만 반환하도록 강제한다. 429/5xx 재시도는 openai
    SDK 자체 내장 재시도(기본 max_retries=2)에 맡긴다."""
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=DRAFT_MODEL,
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.4,
        response_format={'type': 'json_object'},
    )
    return json.loads(response.choices[0].message.content)


def generate_draft(title, content, restricted=False, related_stock_name=None):
    """스크래핑한 기사 (제목, 본문)으로부터 다듬은 제목(ai_title) / AI 3줄 요약 / 투자 관점
    분석 / 블로그 포스팅용 HTML 원고를 생성한다. ai_title은 원문 제목(title)을 그대로 옮기지
    않고 같은 핵심 의미를 유지한 채 자연스럽게 다듬은 버전 — 실패/시뮬레이션 모드에서는 빈
    문자열을 돌려줘 호출부가 AnalyzedArticle.display_title(ai_title 없으면 title로 폴백)로
    처리하게 한다. OpenAI API 키가 없으면 chatbot_client와 동일하게 시뮬레이션 모드로 동작해,
    스크래핑~편집 화면 진입 흐름 자체는 항상 끊기지 않게 한다.

    restricted=True(utils.detect_reuse_restriction으로 원문에서 "무단전재 배포금지, AI 학습
    및 활용 금지" 류 문구가 감지된 경우)면 원문 본문은 아예 참조하지 않고 제목/관련 종목명만으로
    별도 프롬프트(RESTRICTED_SYSTEM_PROMPT)를 태워 NextFinUp 자체 해설을 생성한다 — 원문
    인용·재구성 없이, 출처(source_media/original_url) 표시는 그대로 유지한 채 발행된다."""
    if restricted:
        return _generate_restricted_draft(title, related_stock_name)

    if not content:
        return _simulation_draft(content)

    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return _simulation_draft(content)

    user_prompt = f"[제목]\n{title}\n\n[원문]\n{content[:6000]}"

    try:
        data = _call_openai_json(SYSTEM_PROMPT, user_prompt, max_tokens=3000)
        return {
            'ai_title': (data.get('ai_title') or '').strip(),
            'ai_summary': (data.get('ai_summary') or '').strip() or SIMULATION_SUMMARY,
            'ai_analysis': (data.get('ai_analysis') or '').strip() or SIMULATION_ANALYSIS,
            'blog_content': (data.get('blog_content') or '').strip() or f"<p>{content[:300]}</p>",
        }
    except Exception:
        logger.exception("기사 AI 초안 생성 실패 (title=%r)", title)
        return _error_draft(content)


def _generate_restricted_draft(title, related_stock_name=None):
    """재사용 제한 문구가 감지된 기사용 분기. 원문 본문은 프롬프트에 절대 포함하지 않고
    제목(+ 있으면 관련 종목명)만 근거로 NextFinUp 자체 해설을 생성한다."""
    if not title:
        return _simulation_draft('')

    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return _simulation_draft('')

    user_prompt = f"[기사 제목]\n{title}"
    if related_stock_name:
        user_prompt += f"\n\n[참고 정보]\n관련 종목: {related_stock_name}"

    try:
        data = _call_openai_json(RESTRICTED_SYSTEM_PROMPT, user_prompt, max_tokens=3000)
        return {
            'ai_title': (data.get('ai_title') or '').strip(),
            'ai_summary': (data.get('ai_summary') or '').strip() or SIMULATION_SUMMARY,
            'ai_analysis': (data.get('ai_analysis') or '').strip() or SIMULATION_ANALYSIS,
            'blog_content': (data.get('blog_content') or '').strip() or f"<p>{title}</p>",
        }
    except Exception:
        logger.exception("제한 기사 AI 초안 생성 실패 (title=%r)", title)
        return _error_draft('')


HEALTH_DISCLAIMER = (
    "<p><em>이 글은 일반적인 건강 정보 제공을 목적으로 하며, 특정 질환의 진단이나 치료를 "
    "대신하지 않습니다. 개인의 증상이나 복용 중인 약물에 대해서는 반드시 의사·약사 등 "
    "전문 의료진과 상담하시기 바랍니다.</em></p>"
)

HEALTH_SYSTEM_PROMPT = """당신은 NextFinUp의 건강·의학 정보 에디터입니다. 아래 [카테고리]와 [주제],
[오늘 다룰 각도]를 바탕으로 블로그에 발행할 건강 정보 원고를 작성합니다.
- 이 글은 특정 환자를 진단하거나 치료법을 처방하는 게 아니라, 일반 독자를 위한 교육·정보 목적의
  글입니다. "이렇게 하면 낫는다"처럼 치료 효과를 단정하거나, 구체적인 약물 용량·처방을 제시하지
  마세요. 실제 증상이 있는 사람은 반드시 병원/약국에 문의해야 한다는 취지를 자연스럽게 담되,
  과도한 공포 조성("방치하면 사망")이나 "충격", "경악" 같은 낚시성 표현은 쓰지 마세요.
- 특정 논문·통계 수치·기관명을 지어내 인용하지 마세요. 구체적인 출처가 필요한 수치보다는
  "일반적으로 알려진 바로는", "의학적으로는" 처럼 일반적 지식 수준에서 설명하세요.
- [오늘 다룰 각도]가 "원인·개념"이면 그 주제가 무엇이고 왜 생기는지·왜 중요한지를 이해하기
  쉽게 풀어 설명하고, "관리·실천"이면 일상에서 바로 적용할 수 있는 구체적이고 실용적인 생활
  수칙·팁 위주로 작성하세요.
- 한국어로, 명확하고 자연스럽게 작성하세요. 같은 문장을 반복하며 억지로 늘리지 마세요.
- 반드시 아래 JSON 형식으로만 답하세요. 그 외 설명이나 마크다운 코드블록은 절대 붙이지 마세요.
{
  "ai_summary": "핵심 내용을 3줄로 요약한 문자열 (줄바꿈 문자로 구분)",
  "ai_analysis": "이 주제를 왜 알아둬야 하는지 3~5문장으로 설명한 문자열",
  "blog_content": "블로그 포스팅용 본문 HTML. <h3>/<p>/<ul><li> 등 간단한 태그만 사용해 2~4개 소제목 섹션으로 구성하고 공백 포함 한글 1,500~2,200자 분량으로 작성 (마지막에 병원 상담 권유 문장 포함, 면책 문구는 시스템이 별도로 붙이니 본문 끝에 또 넣지 않아도 됨)"
}
"""


def _simulation_health_draft(title):
    return {
        'ai_summary': SIMULATION_SUMMARY,
        'ai_analysis': SIMULATION_ANALYSIS,
        'blog_content': f"<p>{title}</p>" + HEALTH_DISCLAIMER,
    }


def _error_health_draft(title):
    return {
        'ai_summary': ERROR_SUMMARY,
        'ai_analysis': ERROR_ANALYSIS,
        'blog_content': f"<p>{title}</p>" + HEALTH_DISCLAIMER,
    }


def generate_health_article(category_name, topic, angle_label, title):
    """건강/의학 정보 캘린더(articles/health_calendar.py)의 (카테고리, 주제, 오전/오후 각도)로
    독립된 블로그 글 1건을 생성한다. 스크래핑 원문이 없는 순수 AI 생성 콘텐츠라는 점에서
    generate_featured_briefing과 같은 계열이지만, 여러 종목을 뭉치는 대신 주제 하나를 깊이
    다룬다는 점은 _generate_restricted_draft(제목만으로 쓰는 경로)와 더 가깝다.
    HEALTH_DISCLAIMER는 AI 응답과 무관하게 항상 본문 끝에 고정으로 붙여, 면책 문구 누락으로
    의료 자문처럼 읽히는 걸 코드 레벨에서 막는다."""
    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return _simulation_health_draft(title)

    user_prompt = f"[카테고리]\n{category_name}\n\n[주제]\n{topic}\n\n[오늘 다룰 각도]\n{angle_label}\n\n[제목]\n{title}"

    try:
        data = _call_openai_json(HEALTH_SYSTEM_PROMPT, user_prompt, max_tokens=3000)
        blog_content = (data.get('blog_content') or '').strip() or f"<p>{title}</p>"
        return {
            'ai_summary': (data.get('ai_summary') or '').strip() or SIMULATION_SUMMARY,
            'ai_analysis': (data.get('ai_analysis') or '').strip() or SIMULATION_ANALYSIS,
            'blog_content': blog_content + HEALTH_DISCLAIMER,
        }
    except Exception:
        logger.exception("건강정보 AI 생성 실패 (title=%r)", title)
        return _error_health_draft(title)


FOOD_SYSTEM_PROMPT = """당신은 NextFinUp의 음식·요리·영양 정보 에디터입니다. 아래 [카테고리]와
[주제], [오늘 다룰 각도]를 바탕으로 블로그에 발행할 음식/영양 정보 원고를 작성합니다.
- 특정 논문·통계 수치·기관명을 지어내 인용하지 마세요. 영양 관련 수치가 필요하면 "일반적으로
  알려진 바로는" 처럼 일반적 지식 수준에서 설명하고, 특정 질환의 치료·예방 효과를 단정하지
  마세요("이 음식을 먹으면 병이 낫는다" 같은 표현 금지) — 영양/맛/조리 정보이지 의학적 조언이
  아닙니다.
- [오늘 다룰 각도]가 "정보·이해"면 그 주제가 무엇이고 왜 알아두면 좋은지(유래, 특징, 영양학적
  배경 등)를 이해하기 쉽게 풀어 설명하고, "실천·레시피"면 실제로 따라 할 수 있는 구체적인
  손질법·조리 순서·보관법·장보기 팁 등 실용적인 내용 위주로 작성하세요. 레시피라면 재료와
  순서를 <ol>/<li>로 명확히 나열하세요.
- 과장된 표현("최고", "무조건", "충격") 없이 담백하게, 한국어로 자연스럽게 작성하세요. 같은
  문장을 반복하며 억지로 늘리지 마세요.
- 반드시 아래 JSON 형식으로만 답하세요. 그 외 설명이나 마크다운 코드블록은 절대 붙이지 마세요.
{
  "ai_summary": "핵심 내용을 3줄로 요약한 문자열 (줄바꿈 문자로 구분)",
  "ai_analysis": "이 주제를 왜 알아두면 좋은지 3~5문장으로 설명한 문자열",
  "blog_content": "블로그 포스팅용 본문 HTML. <h3>/<p>/<ul><li>/<ol><li> 등 간단한 태그만 사용해 2~4개 소제목 섹션으로 구성하고 공백 포함 한글 1,500~2,200자 분량으로 작성"
}
"""


def _simulation_food_draft(title):
    return {
        'ai_summary': SIMULATION_SUMMARY,
        'ai_analysis': SIMULATION_ANALYSIS,
        'blog_content': f"<p>{title}</p>",
    }


def _error_food_draft(title):
    return {
        'ai_summary': ERROR_SUMMARY,
        'ai_analysis': ERROR_ANALYSIS,
        'blog_content': f"<p>{title}</p>",
    }


def generate_food_article(category_name, topic, angle_label, title):
    """음식/영양 정보 캘린더(articles/food_calendar.py)의 (카테고리, 주제, 오전/오후 각도)로
    독립된 블로그 글 1건을 생성한다. generate_health_article과 완전히 같은 구조이지만, 음식은
    의료 리스크가 낮아 고정 면책 문구(HEALTH_DISCLAIMER)는 붙이지 않는다 — 대신 프롬프트에서
    질환 치료 효과 단정을 금지해 과장된 건강 정보처럼 읽히지 않게 한다."""
    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return _simulation_food_draft(title)

    user_prompt = f"[카테고리]\n{category_name}\n\n[주제]\n{topic}\n\n[오늘 다룰 각도]\n{angle_label}\n\n[제목]\n{title}"

    try:
        data = _call_openai_json(FOOD_SYSTEM_PROMPT, user_prompt, max_tokens=3000)
        return {
            'ai_summary': (data.get('ai_summary') or '').strip() or SIMULATION_SUMMARY,
            'ai_analysis': (data.get('ai_analysis') or '').strip() or SIMULATION_ANALYSIS,
            'blog_content': (data.get('blog_content') or '').strip() or f"<p>{title}</p>",
        }
    except Exception:
        logger.exception("음식/영양정보 AI 생성 실패 (title=%r)", title)
        return _error_food_draft(title)


BRIEFING_SYSTEM_PROMPT = """당신은 NextFinUp의 금융 뉴스 AI 에디터입니다. 아래 [특징주 목록](KIS 등락률
상위 종목, 가격/등락률만 제공됨)을 바탕으로, 개별 기사가 아니라 하루 {session_label}용 통합 시황
브리핑에 들어갈 해설 문단들을 작성합니다.
- 종목별 가격·등락률 수치가 나열된 목록(상승률 상위/하락률 상위/증권사 리포트)은 시스템이 원본
  데이터에서 직접, 정확한 값으로 별도 렌더링합니다 — 당신은 그 목록을 다시 나열하지 마세요.
  숫자를 옮겨적다 실수하면(부호가 바뀌는 등) 그대로 발행되므로, 개별 종목의 정확한 가격/등락률
  수치를 본문에 직접 인용하지 말고 "상승률 상위 5개 종목", "하락 폭이 큰 종목들" 처럼 뭉뚱그려
  서술하세요. 예외적으로 대표 종목 1~2개의 이름과 대략적인 방향(상승/하락)만 언급하는 것은
  괜찮지만, 정확한 %/원 수치는 쓰지 마세요 (시스템이 렌더링하는 목록과 중복·불일치 위험).
- [특징주 목록]에 있는 종목명, 그리고 각 종목 줄 아래 "↳ 관련 증권사 리포트"로 달려있는 내용
  외의 사실을 지어내지 마세요. 실적 발표, 공시, 뉴스, 수급 주체(외국인/기관) 등 제공되지 않은
  구체적인 원인은 절대 언급하지 마세요.
- "↳ 관련 증권사 리포트"가 달려있는 종목은 그 리포트 내용을 등락의 참고 배경으로 자연스럽게
  녹여서 설명하세요. 단, 리포트 시점이 오늘 등락과 인과관계가 명확히 확인된 것은 아니므로
  "오늘 상승은 이 리포트 때문이다"처럼 단정하지 말고 "증권사에서는 ~라는 의견을 냈다" 식으로
  참고 정보로만 소개하세요. "↳ 관련 증권사 리포트"가 없는 종목은 원인을 절대 추측하지 마세요.
  [오늘의 증권사 리포트 (특징주 외)] 섹션의 리포트는 시스템이 별도 목록으로 렌더링하니 본문에서
  다시 나열하지 마세요.
- 분량을 채우는 방법은 사실 날조가 아니라: (1) "등락률 상위 종목"이 시장에서 어떤 의미를 갖는지,
  KIS 등락률 순위가 무엇을 보여주는지 등 일반적인 투자 지식 수준의 배경 설명, (2) 단기 급등락
  종목 투자 시 일반적으로 유의할 점(변동성, 추격매수 위험 등)에 대한 원론적인 조언으로 채우세요.
- 날짜는 언급하지 마세요 (제목에도 넣지 마세요) — 별도로 시스템이 붙입니다.
- 한국어로, 명확하고 자연스럽게 작성하세요. 같은 문장을 반복하며 억지로 늘리지 마세요.
- 반드시 아래 JSON 형식으로만 답하세요. 그 외 설명이나 마크다운 코드블록은 절대 붙이지 마세요.
{{
  "ai_summary": "핵심 내용을 3줄로 요약한 문자열 (줄바꿈 문자로 구분, 정확한 수치 인용 금지)",
  "ai_analysis": "상승/하락 상위 종목의 등락률 분포와 (있다면) 관련 증권사 리포트에 대한 3~5문장 설명 (제공되지 않은 원인 추측 금지, 정확한 수치 인용 금지)",
  "intro": "오늘 시황을 여는 1~2문단 (정확한 수치 인용 금지)",
  "gainers_meaning": "상승률 상위 종목의 의미를 설명하는 1문단 (정확한 수치 인용 금지)",
  "losers_meaning": "하락률 상위 종목의 의미를 설명하는 1문단 (정확한 수치 인용 금지)",
  "investment_notes": "단기 급등락 종목 투자 시 유의사항 1문단"
}}
"""


def movers_to_text(movers, reports=None):
    """movers(RankedMover 목록)를 프롬프트/원문 감사용 텍스트로 직렬화한다. reports가 있으면
    같은 종목코드(ticker)를 가진 [리포트 브리핑] 기사를 해당 종목 줄 아래에 근거로 붙이고,
    매칭되지 않은 나머지 리포트는 '특징주 외' 섹션으로 별도 정리한다 — 등락률 상위 종목과
    그날 나온 증권사 리포트는 서로 다른 모집단(급등락은 중소형/테마주, 리포트는 대형주 위주)이라
    실제로 겹치는 날이 드물므로, 매칭 안 된 종목에 리포트를 억지로 갖다 붙이지 않는다."""
    reports = reports or []
    reports_by_ticker = {}
    for r in reports:
        reports_by_ticker.setdefault(r['ticker'], []).append(r['text'])
    matched_tickers = set()

    def mover_line(m):
        line = f"- {m['name']}({m['ticker']}): {m['price']:,.0f}원, {m['change_pct']:+.2f}%"
        matched = reports_by_ticker.get(m['ticker'])
        if matched:
            matched_tickers.add(m['ticker'])
            line += "\n  ↳ 관련 증권사 리포트: " + " / ".join(matched)
        return line

    gainers = [m for m in movers if m['rank_type'] == 'GAINER']
    losers = [m for m in movers if m['rank_type'] == 'LOSER']
    lines = ["[상승률 상위]"]
    lines += [mover_line(m) for m in gainers]
    lines.append("[하락률 상위]")
    lines += [mover_line(m) for m in losers]

    others = [r for r in reports if r['ticker'] not in matched_tickers]
    if others:
        lines.append("")
        lines.append("[오늘의 증권사 리포트 (특징주 외)]")
        lines += [f"- {_report_line_prefix(r)}{r['text']}" for r in others]

    return "\n".join(lines)


def _report_line_prefix(report):
    """리포트 종목의 당일 등락 정보를 앞에 붙인다. 실시간 시세가 없는 종목(휴장일 수집 실패 등)은
    가격 없이 이름만 붙인다."""
    price = report.get('price')
    change_pct = report.get('change_pct')
    if price is None or change_pct is None:
        return f"{report['name']}({report['ticker']}): "
    return f"{report['name']}({report['ticker']}, {price:,.0f}원 {change_pct:+.2f}%): "


# articles/thumbnail.py SIGNAL_COLORS의 BUY/SELL 색상과 동일 — 국내 관례대로 상승=빨강, 하락=파랑
POSITIVE_COLOR = "#e53935"
NEGATIVE_COLOR = "#1e88e5"
NEUTRAL_COLOR = "#6c757d"


def _colored_pct(change_pct):
    color = POSITIVE_COLOR if change_pct > 0 else NEGATIVE_COLOR if change_pct < 0 else NEUTRAL_COLOR
    return f'<span style="color:{color};font-weight:600;">{change_pct:+.2f}%</span>'


def _naver_finance_link(ticker, label):
    url = f"https://stock.naver.com/domestic/stock/{ticker}/price"
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'


def _movers_list_html(movers_subset):
    """상승률/하락률 상위 목록을 원본 RankedMover 값에서 직접 렌더링한다 — AI가 이 수치를
    산문으로 옮겨적다 부호를 틀리는 사고(예: 하락 목록에 +% 종목이 섞이는 것)를 원천 차단하기
    위해, 목록 자체는 AI를 거치지 않고 시스템이 그린다. 외부 블로그(Blogger 등)에 그대로
    발행되므로 클래스가 아닌 인라인 style로 색을 입힌다. 종목명은 네이버 증권 종목 페이지로 연결."""
    items = "".join(
        f"<li>{_naver_finance_link(m['ticker'], m['name'])}: {m['price']:,.0f}원, {_colored_pct(m['change_pct'])}</li>"
        for m in movers_subset
    )
    return f"<ul>{items}</ul>"


def _reports_list_html(reports_subset):
    def report_item(r):
        price = r.get('price')
        change_pct = r.get('change_pct')
        link = _naver_finance_link(r['ticker'], r['name'])
        if price is None or change_pct is None:
            return f"<li>{link}: {r['text']}</li>"
        return f"<li>{link} ({price:,.0f}원, {_colored_pct(change_pct)}): {r['text']}</li>"

    items = "".join(report_item(r) for r in reports_subset)
    return f"<ul>{items}</ul>"


def _assemble_blog_content(sections, movers, reports):
    """AI가 쓴 해설 문단(sections)과, 시스템이 원본 데이터에서 직접 그린 종목/리포트 목록을
    합쳐 최종 blog_content HTML을 만든다."""
    reports = reports or []
    gainers = [m for m in movers if m['rank_type'] == 'GAINER']
    losers = [m for m in movers if m['rank_type'] == 'LOSER']
    matched_tickers = {r['ticker'] for r in reports} & {m['ticker'] for m in movers}
    others = [r for r in reports if r['ticker'] not in matched_tickers]

    parts = [
        "<h3>오늘 주식 시장 시황</h3>",
        f"<p>{sections['intro']}</p>",
    ]
    if gainers:
        parts += ["<h3>상승률 상위 종목</h3>", _movers_list_html(gainers)]
    if losers:
        parts += ["<h3>하락률 상위 종목</h3>", _movers_list_html(losers)]
    if gainers:
        parts += ["<h3>상승률 상위 종목의 의미</h3>", f"<p>{sections['gainers_meaning']}</p>"]
    if losers:
        parts += ["<h3>하락률 상위 종목의 의미</h3>", f"<p>{sections['losers_meaning']}</p>"]
    parts += ["<h3>투자 시 유의사항</h3>", f"<p>{sections['investment_notes']}</p>"]
    if others:
        parts += ["<h3>오늘의 증권사 리포트</h3>", _reports_list_html(others)]

    return "\n".join(parts)


def _simulation_briefing(session_label, movers, reports):
    sections = {
        'intro': SIMULATION_ANALYSIS,
        'gainers_meaning': SIMULATION_ANALYSIS,
        'losers_meaning': SIMULATION_ANALYSIS,
        'investment_notes': SIMULATION_ANALYSIS,
    }
    return {
        'ai_summary': SIMULATION_SUMMARY,
        'ai_analysis': SIMULATION_ANALYSIS,
        'blog_content': _assemble_blog_content(sections, movers, reports),
    }


def _error_briefing(session_label, movers, reports):
    sections = {
        'intro': ERROR_ANALYSIS,
        'gainers_meaning': ERROR_ANALYSIS,
        'losers_meaning': ERROR_ANALYSIS,
        'investment_notes': ERROR_ANALYSIS,
    }
    return {
        'ai_summary': ERROR_SUMMARY,
        'ai_analysis': ERROR_ANALYSIS,
        'blog_content': _assemble_blog_content(sections, movers, reports),
    }


def generate_featured_briefing(session_label, movers, reports=None):
    """RankedMover(GAINER/LOSER) 상위 종목 목록으로 개별 기사가 아닌 통합 특징주 브리핑
    1건을 생성한다. movers는 [{'rank_type','ticker','name','price','change_pct'}, ...] 형태.
    reports는 그날의 [리포트 브리핑] 기사를 [{'ticker','name','text'}, ...]로 넘긴 것으로,
    ticker가 movers와 겹치면 해당 종목의 등락 참고 근거로, 안 겹치면 별도 섹션으로 반영된다
    (movers_to_text 참고). 하루 두 번(장중/마감후)만 호출되므로 매 기사마다 AI를 호출하는
    기존 방식보다 토큰 비용이 훨씬 적다.
    상승률/하락률 상위 종목 목록과 증권사 리포트 목록은 AI에게 다시 쓰게 하지 않고 원본 데이터로
    직접 렌더링한다 — AI는 그 사이에 들어갈 해설 문단만 작성한다."""
    reports = reports or []
    movers_text = movers_to_text(movers, reports)
    if not movers:
        return _simulation_briefing(session_label, movers, reports)

    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return _simulation_briefing(session_label, movers, reports)

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {'role': 'system', 'content': BRIEFING_SYSTEM_PROMPT.format(session_label=session_label)},
                {'role': 'user', 'content': f"[특징주 목록]\n{movers_text}"},
            ],
            max_tokens=2000,
            temperature=0.4,
            response_format={'type': 'json_object'},
        )
        data = json.loads(response.choices[0].message.content)
        sections = {
            'intro': (data.get('intro') or '').strip() or SIMULATION_ANALYSIS,
            'gainers_meaning': (data.get('gainers_meaning') or '').strip() or SIMULATION_ANALYSIS,
            'losers_meaning': (data.get('losers_meaning') or '').strip() or SIMULATION_ANALYSIS,
            'investment_notes': (data.get('investment_notes') or '').strip() or SIMULATION_ANALYSIS,
        }
        return {
            'ai_summary': (data.get('ai_summary') or '').strip() or SIMULATION_SUMMARY,
            'ai_analysis': (data.get('ai_analysis') or '').strip() or SIMULATION_ANALYSIS,
            'blog_content': _assemble_blog_content(sections, movers, reports),
        }
    except Exception:
        logger.exception("특징주 브리핑 AI 생성 실패 (session=%r)", session_label)
        return _error_briefing(session_label, movers, reports)


WEEKLY_BRIEFING_SYSTEM_PROMPT = """당신은 NextFinUp의 금융 뉴스 AI 에디터입니다. 아래 [지수 등락]
(이번 주 코스피/코스닥 주간 등락률)과 [주간 급등락 종목](StockDailyPrice 기준으로 계산된 주간
수익률 상위/하위 종목, 가격/등락률만 제공됨)을 바탕으로, 토요일에 발행할 "주간 시황 정리"에
들어갈 해설 문단들을 작성합니다.
- 지수·종목의 정확한 등락률/가격 수치가 나열된 목록은 시스템이 원본 데이터에서 직접, 정확한
  값으로 별도 렌더링합니다 — 당신은 그 목록을 다시 나열하지 마세요. 숫자를 옮겨적다 실수하면
  (부호가 바뀌는 등) 그대로 발행되므로, 본문에 정확한 %/원 수치를 직접 인용하지 말고 "이번 주
  코스피는 상승 마감", "상승 폭이 컸던 종목들" 처럼 뭉뚱그려 서술하세요. 예외적으로 대표 종목
  1~2개의 이름과 대략적인 방향(상승/하락)만 언급하는 것은 괜찮지만, 정확한 수치는 쓰지 마세요.
- [지수 등락]/[주간 급등락 종목]에 없는 사실을 지어내지 마세요. 이번 주에 있었던 구체적인
  실적 발표, 공시, 이벤트, 수급 주체(외국인/기관) 등 제공되지 않은 원인은 절대 언급하지
  마세요 — "이번 주 상승은 ~때문이다"처럼 원인을 단정하지 마세요.
- 분량을 채우는 방법은 사실 날조가 아니라: (1) 주간 단위로 시장을 보는 것이 왜 유용한지, 일간
  변동성과 달리 주간 추세가 어떤 의미를 갖는지 등 일반적인 투자 지식 수준의 배경 설명,
  (2) 한 주간 급등락한 종목에 투자할 때 일반적으로 유의할 점(단기 추세 추종의 위험, 다음 주
  변동성 등)에 대한 원론적인 조언으로 채우세요.
- 날짜(구체적인 연/월/일)는 언급하지 마세요 — 별도로 시스템이 붙입니다. "이번 주"처럼만
  표현하세요.
- 한국어로, 명확하고 자연스럽게 작성하세요. 같은 문장을 반복하며 억지로 늘리지 마세요.
- 반드시 아래 JSON 형식으로만 답하세요. 그 외 설명이나 마크다운 코드블록은 절대 붙이지 마세요.
{{
  "ai_summary": "이번 주 핵심 내용을 3줄로 요약한 문자열 (줄바꿈 문자로 구분, 정확한 수치 인용 금지)",
  "ai_analysis": "이번 주 지수 흐름과 급등락 종목 분포에 대한 3~5문장 설명 (제공되지 않은 원인 추측 금지, 정확한 수치 인용 금지)",
  "intro": "이번 주 시황을 여는 1~2문단 (정확한 수치 인용 금지)",
  "index_meaning": "코스피/코스닥 주간 등락이 갖는 의미를 설명하는 1문단 (정확한 수치 인용 금지)",
  "gainers_meaning": "이번 주 상승률 상위 종목의 의미를 설명하는 1문단 (정확한 수치 인용 금지)",
  "losers_meaning": "이번 주 하락률 상위 종목의 의미를 설명하는 1문단 (정확한 수치 인용 금지)",
  "investment_notes": "주간 급등락 종목 투자 시 유의사항 1문단"
}}
"""


def weekly_movers_to_text(index_summary, movers):
    """index_summary([{'market_type','change_pct','close_price'}, ...])와 movers(주간 수익률
    상위/하위 종목, [{'rank_type','ticker','name','close_price','change_pct'}, ...])를
    프롬프트/원문 감사용 텍스트로 직렬화한다. movers_to_text와 같은 이유로 목록 자체는
    AI에게 다시 쓰게 하지 않고 시스템이 직접 렌더링한다."""
    lines = ["[지수 등락]"]
    for idx in index_summary:
        label = '코스피' if idx['market_type'] == 'KOSPI' else '코스닥'
        lines.append(f"- {label}: {idx['close_price']:,.2f} ({idx['change_pct']:+.2f}%)")

    gainers = [m for m in movers if m['rank_type'] == 'GAINER']
    losers = [m for m in movers if m['rank_type'] == 'LOSER']
    lines.append("")
    lines.append("[주간 상승률 상위 종목]")
    lines += [f"- {m['name']}({m['ticker']}): {m['close_price']:,.0f}원, {m['change_pct']:+.2f}%" for m in gainers]
    lines.append("[주간 하락률 상위 종목]")
    lines += [f"- {m['name']}({m['ticker']}): {m['close_price']:,.0f}원, {m['change_pct']:+.2f}%" for m in losers]
    return "\n".join(lines)


def _weekly_index_html(index_summary):
    items = "".join(
        f"<li>{'코스피' if idx['market_type'] == 'KOSPI' else '코스닥'}: {idx['close_price']:,.2f}"
        f" ({_colored_pct(idx['change_pct'])})</li>"
        for idx in index_summary
    )
    return f"<ul>{items}</ul>"


def _weekly_movers_list_html(movers_subset):
    items = "".join(
        f"<li>{_naver_finance_link(m['ticker'], m['name'])}: {m['close_price']:,.0f}원, {_colored_pct(m['change_pct'])}</li>"
        for m in movers_subset
    )
    return f"<ul>{items}</ul>"


def _assemble_weekly_blog_content(sections, index_summary, movers):
    gainers = [m for m in movers if m['rank_type'] == 'GAINER']
    losers = [m for m in movers if m['rank_type'] == 'LOSER']

    parts = [
        "<h3>이번 주 주식 시장 시황</h3>",
        f"<p>{sections['intro']}</p>",
        "<h3>코스피 · 코스닥 주간 등락</h3>",
        _weekly_index_html(index_summary),
        f"<p>{sections['index_meaning']}</p>",
    ]
    if gainers:
        parts += ["<h3>주간 상승률 상위 종목</h3>", _weekly_movers_list_html(gainers), f"<p>{sections['gainers_meaning']}</p>"]
    if losers:
        parts += ["<h3>주간 하락률 상위 종목</h3>", _weekly_movers_list_html(losers), f"<p>{sections['losers_meaning']}</p>"]
    parts += ["<h3>투자 시 유의사항</h3>", f"<p>{sections['investment_notes']}</p>"]

    return "\n".join(parts)


def _simulation_weekly_briefing(index_summary, movers):
    sections = {
        'intro': SIMULATION_ANALYSIS,
        'index_meaning': SIMULATION_ANALYSIS,
        'gainers_meaning': SIMULATION_ANALYSIS,
        'losers_meaning': SIMULATION_ANALYSIS,
        'investment_notes': SIMULATION_ANALYSIS,
    }
    return {
        'ai_summary': SIMULATION_SUMMARY,
        'ai_analysis': SIMULATION_ANALYSIS,
        'blog_content': _assemble_weekly_blog_content(sections, index_summary, movers),
    }


def _error_weekly_briefing(index_summary, movers):
    sections = {
        'intro': ERROR_ANALYSIS,
        'index_meaning': ERROR_ANALYSIS,
        'gainers_meaning': ERROR_ANALYSIS,
        'losers_meaning': ERROR_ANALYSIS,
        'investment_notes': ERROR_ANALYSIS,
    }
    return {
        'ai_summary': ERROR_SUMMARY,
        'ai_analysis': ERROR_ANALYSIS,
        'blog_content': _assemble_weekly_blog_content(sections, index_summary, movers),
    }


def generate_weekly_market_briefing(index_summary, movers):
    """MarketIndex 주간 등락(index_summary)과 StockDailyPrice 기준 주간 수익률 상위/하위
    종목(movers)으로 토요일용 "주간 시황 정리" 브리핑 1건을 생성한다.
    generate_featured_briefing과 같은 이유로, 지수/종목의 정확한 수치 목록은 원본 데이터로
    직접 렌더링하고 AI는 그 사이에 들어갈 해설 문단만 작성한다."""
    movers_text = weekly_movers_to_text(index_summary, movers)
    if not index_summary and not movers:
        return _simulation_weekly_briefing(index_summary, movers)

    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return _simulation_weekly_briefing(index_summary, movers)

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {'role': 'system', 'content': WEEKLY_BRIEFING_SYSTEM_PROMPT},
                {'role': 'user', 'content': movers_text},
            ],
            max_tokens=2000,
            temperature=0.4,
            response_format={'type': 'json_object'},
        )
        data = json.loads(response.choices[0].message.content)
        sections = {
            'intro': (data.get('intro') or '').strip() or SIMULATION_ANALYSIS,
            'index_meaning': (data.get('index_meaning') or '').strip() or SIMULATION_ANALYSIS,
            'gainers_meaning': (data.get('gainers_meaning') or '').strip() or SIMULATION_ANALYSIS,
            'losers_meaning': (data.get('losers_meaning') or '').strip() or SIMULATION_ANALYSIS,
            'investment_notes': (data.get('investment_notes') or '').strip() or SIMULATION_ANALYSIS,
        }
        return {
            'ai_summary': (data.get('ai_summary') or '').strip() or SIMULATION_SUMMARY,
            'ai_analysis': (data.get('ai_analysis') or '').strip() or SIMULATION_ANALYSIS,
            'blog_content': _assemble_weekly_blog_content(sections, index_summary, movers),
        }
    except Exception:
        logger.exception("주간 시황 브리핑 AI 생성 실패")
        return _error_weekly_briefing(index_summary, movers)


def _strip_proper_nouns(client, title, ai_summary):
    """헤드라인/요약에서 실존 인물·캐릭터·작품·브랜드명 같은 고유명사를 gpt-4o-mini로 제거한
    한두 문장을 반환한다. generate_thumbnail_image_bytes가 모더레이션 차단을 받았을 때만 쓰는
    재시도용 헬퍼 — 실패 시 빈 문자열을 반환해 호출부가 재시도를 포기하게 한다."""
    try:
        response = client.chat.completions.create(
            model=DRAFT_MODEL,
            messages=[
                {'role': 'system', 'content': (
                    "다음 한국어 뉴스 제목/요약을, 사람 이름·캐릭터명·영화/드라마/게임 제목·"
                    "기업/브랜드명 등 고유명사를 모두 일반적인 표현으로 바꿔 한두 문장으로 "
                    "다시 써라. 전반적인 주제와 분위기는 유지하되, 특정 실존 인물이나 저작권 "
                    "있는 캐릭터/작품을 특정할 수 있는 단어는 절대 남기지 마라. 반드시 아래 "
                    "JSON 형식으로만 답하라.\n"
                    '{"generic_summary": "고유명사를 제거한 한두 문장 요약"}'
                )},
                {'role': 'user', 'content': f"제목: {title}\n요약: {(ai_summary or '')[:400]}"},
            ],
            max_tokens=200,
            temperature=0.3,
            response_format={'type': 'json_object'},
        )
        data = json.loads(response.choices[0].message.content)
        return (data.get('generic_summary') or '').strip()
    except Exception:
        logger.exception("고유명사 제거 재작성 실패 (title=%r)", title)
        return ''


def generate_thumbnail_image_bytes(title, ai_summary):
    """경제 뉴스가 아닌 일반 기사(관련 종목·매칭 키워드가 없는 기사)의 썸네일을 gpt-image-2로
    직접 그려 PNG 바이트로 반환한다. 종목 시세·코스피/코스닥 지수처럼 정확한 수치를 보여줘야
    하는 카드는 여기 쓰지 않는다 — AI 이미지 생성은 숫자를 정확히 보장할 수 없어, 그런 카드는
    thumbnail.py가 실제 데이터로 직접 그린다(_draw_market_grid/_draw_index_summary_boxes).
    비용을 낮게 유지하기 위해 quality="low"만 사용한다. OPENAI_API_KEY가 없거나(플레이스홀더
    포함) 호출이 실패하면 None을 반환해, 호출부(thumbnail.build_thumbnail_file)가 조용히 기존
    PIL 텍스트 패널 카드로 폴백하게 한다.
    헤드라인에 실존 인물/캐릭터/작품명 등 고유명사가 있으면 gpt-image-2 자체 출력 모더레이션에
    걸려 BadRequestError(moderation_blocked)가 날 수 있다 — 이 경우 한 번만, 고유명사를 뺀
    일반화된 문장으로 재시도한다."""
    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return None

    def build_prompt(headline_text, summary_text):
        return (
            "Create a clean, modern editorial illustration to use as a Korean news article's cover "
            "thumbnail. Absolutely no text, letters, numbers, charts, graphs, or logos anywhere in "
            "the image — illustration only.\n"
            f"Headline: {headline_text}\n"
            f"Summary: {summary_text}\n"
            "Style: flat editorial illustration, muted navy/blue color palette, high contrast, "
            "professional news-site cover art."
        )

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        response = client.images.generate(
            model="gpt-image-2",
            prompt=build_prompt(title, (ai_summary or '')[:400]),
            size="1536x1024",
            quality="low",
        )
        return base64.b64decode(response.data[0].b64_json)
    except BadRequestError:
        logger.warning(
            "gpt-image-2 썸네일 생성이 모더레이션에 걸림 — 고유명사 제거 후 재시도 (title=%r)", title,
        )
    except Exception:
        logger.exception("gpt-image-2 썸네일 생성 실패 (title=%r)", title)
        return None

    generic_summary = _strip_proper_nouns(client, title, ai_summary)
    if not generic_summary:
        return None
    try:
        response = client.images.generate(
            model="gpt-image-2",
            prompt=build_prompt(generic_summary, ''),
            size="1536x1024",
            quality="low",
        )
        return base64.b64decode(response.data[0].b64_json)
    except Exception:
        logger.exception("gpt-image-2 썸네일 재시도(고유명사 제거) 실패 (title=%r)", title)
        return None


# ==========================================
# /tools/spell-checker/ — 맞춤법 검사기. 다른 유틸(글자수세기, 환율 계산기 등)과 달리 진짜
# 한국어 맞춤법 교정은 브라우저만으로 할 수 없어(형태소 분석·사전이 필요) 이 기능만 텍스트가
# 서버(OpenAI)로 전송된다 — 화면에 그 사실을 분명히 안내해야 한다(views.spell_checker_view 참고).
# ==========================================

SIMULATION_SPELL_CHECK = (
    "맞춤법 검사 기능은 현재 준비 중입니다. 관리자가 AI 요약용 API 키를 설정하면 "
    "실시간 맞춤법 검사가 제공됩니다. (현재 시뮬레이션 모드)"
)
ERROR_SPELL_CHECK = "일시적인 오류로 맞춤법 검사를 완료하지 못했습니다. 잠시 후 다시 시도해주세요."

SPELL_CHECK_SYSTEM_PROMPT = """당신은 한국어 맞춤법·띄어쓰기 교정 전문가입니다. 주어진 텍스트의
맞춤법, 띄어쓰기, 표준어 오류만 교정하세요 — 문체, 어조, 존댓말/반말 여부는 절대 바꾸지 마세요.
오류가 없으면 corrections를 빈 배열로 반환하고 corrected_text는 원문과 동일하게 반환하세요.
반드시 아래 JSON 형식으로만 답하세요:
{
  "corrected_text": "전체 교정된 텍스트 (원문의 줄바꿈 구조 유지)",
  "corrections": [
    {"original": "원래 표현", "suggestion": "교정된 표현", "reason": "간단한 설명(한 문장)"}
  ]
}
"""

SPELL_CHECK_MAX_CHARS = 2000


def check_spelling(text):
    """맞춤법 검사 결과를 {'corrected_text', 'corrections', 'simulated'} 형태로 반환한다.
    입력이 비었거나 API 키가 없으면(시뮬레이션 모드) corrected_text에 원문을 그대로 돌려줘
    호출부 화면이 항상 뭔가는 보여줄 수 있게 한다."""
    text = (text or '').strip()
    if not text:
        return {'corrected_text': '', 'corrections': [], 'simulated': False}

    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return {'corrected_text': text, 'corrections': [], 'simulated': True, 'message': SIMULATION_SPELL_CHECK}

    try:
        data = _call_openai_json(SPELL_CHECK_SYSTEM_PROMPT, text[:SPELL_CHECK_MAX_CHARS], max_tokens=3000)
        return {
            'corrected_text': (data.get('corrected_text') or text).strip(),
            'corrections': data.get('corrections') or [],
            'simulated': False,
        }
    except Exception:
        logger.exception("맞춤법 검사 실패 (len=%d)", len(text))
        return {'corrected_text': text, 'corrections': [], 'simulated': False, 'error': ERROR_SPELL_CHECK}
