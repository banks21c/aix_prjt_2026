import json
import logging

from django.conf import settings
from openai import OpenAI

logger = logging.getLogger(__name__)

SIMULATION_SUMMARY = (
    "AI 요약 기능은 현재 준비 중입니다. 관리자가 OpenAI API 키를 설정하면 "
    "실시간 AI 요약이 제공됩니다. (현재 시뮬레이션 모드)"
)
SIMULATION_ANALYSIS = "AI 투자 관점 분석 기능은 현재 준비 중입니다. (현재 시뮬레이션 모드)"
ERROR_SUMMARY = "일시적인 오류로 AI 요약을 생성하지 못했습니다. 아래 원문을 참고해 직접 작성해주세요."
ERROR_ANALYSIS = "일시적인 오류로 AI 분석을 생성하지 못했습니다."

SYSTEM_PROMPT = """당신은 NextFinUp의 금융 뉴스 AI 에디터입니다. 주어진 기사 원문을 바탕으로
블로그 포스팅용 원고를 작성합니다.
- 아래 [원문]에 없는 사실을 지어내거나 추측하지 마세요.
- 한국어로, 간결하고 명확하게 작성하세요.
- 반드시 아래 JSON 형식으로만 답하세요. 그 외 설명이나 마크다운 코드블록은 절대 붙이지 마세요.
{
  "ai_summary": "핵심 내용을 3줄로 요약한 문자열 (줄바꿈 문자로 구분)",
  "ai_analysis": "투자자 관점에서의 시사점을 2~4문장으로 분석한 문자열",
  "blog_content": "블로그 포스팅용 본문 HTML. <h3>/<p>/<ul><li> 등 간단한 태그만 사용해 400~700자 분량으로 작성"
}
"""


def _simulation_draft(content):
    return {
        'ai_summary': SIMULATION_SUMMARY,
        'ai_analysis': SIMULATION_ANALYSIS,
        'blog_content': f"<p>{(content or '')[:300]}</p>",
    }


def _error_draft(content):
    return {
        'ai_summary': ERROR_SUMMARY,
        'ai_analysis': ERROR_ANALYSIS,
        'blog_content': f"<p>{(content or '')[:300]}</p>",
    }


def generate_draft(title, content):
    """스크래핑한 기사 (제목, 본문)으로부터 AI 3줄 요약 / 투자 관점 분석 / 블로그 포스팅용
    HTML 원고를 생성한다. OpenAI API 키가 없으면 chatbot_client와 동일하게 시뮬레이션
    모드로 동작해, 스크래핑~편집 화면 진입 흐름 자체는 항상 끊기지 않게 한다."""
    if not content:
        return _simulation_draft(content)

    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return _simulation_draft(content)

    user_prompt = f"[제목]\n{title}\n\n[원문]\n{content[:6000]}"

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_prompt},
            ],
            max_tokens=1200,
            temperature=0.4,
            response_format={'type': 'json_object'},
        )
        data = json.loads(response.choices[0].message.content)
        return {
            'ai_summary': (data.get('ai_summary') or '').strip() or SIMULATION_SUMMARY,
            'ai_analysis': (data.get('ai_analysis') or '').strip() or SIMULATION_ANALYSIS,
            'blog_content': (data.get('blog_content') or '').strip() or f"<p>{content[:300]}</p>",
        }
    except Exception:
        logger.exception("기사 AI 초안 생성 실패 (title=%r)", title)
        return _error_draft(content)
