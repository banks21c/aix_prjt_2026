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
블로그(티스토리/워드프레스 등) 포스팅용 원고를 작성합니다. 애드센스 등 광고 심사를 통과하려면
너무 짧은 콘텐츠는 안 되므로, blog_content는 A4 용지 한 장 분량(공백 포함 한글 1,800~2,500자
정도)의 충실한 글로 작성하세요.
- [원문]에 없는 특정 수치·실적·사건을 지어내거나 추측하지 마세요. 분량을 채우기 위한 방법은
  사실 날조가 아니라, [원문] 내용을 여러 섹션으로 나눠 자세히 풀어 설명하고(무슨 일이 있었는지,
  왜 중요한지), 기사에 언급된 용어·산업/기업 배경을 독자가 이해하기 쉽게 일반적인 지식 수준에서
  보충 설명하고, 투자자 관점의 시사점과 유의할 점을 충분히 서술하는 것입니다.
- 한국어로, 명확하고 자연스럽게 작성하세요. 같은 문장을 반복하며 억지로 늘리지 마세요.
- 반드시 아래 JSON 형식으로만 답하세요. 그 외 설명이나 마크다운 코드블록은 절대 붙이지 마세요.
{
  "ai_summary": "핵심 내용을 3줄로 요약한 문자열 (줄바꿈 문자로 구분)",
  "ai_analysis": "투자자 관점에서의 시사점을 3~5문장으로 분석한 문자열",
  "blog_content": "블로그 포스팅용 본문 HTML. <h3>/<p>/<ul><li> 등 간단한 태그만 사용해 여러 소제목 섹션(기사 핵심 정리, 배경/맥락, 투자자 관점 시사점 등)으로 구성하고 공백 포함 한글 1,800~2,500자 분량으로 작성"
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
            max_tokens=3000,
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


BRIEFING_SYSTEM_PROMPT = """당신은 NextFinUp의 금융 뉴스 AI 에디터입니다. 아래 [특징주 목록](KIS 등락률
상위 종목, 가격/등락률만 제공됨)을 바탕으로, 개별 기사가 아니라 하루 {session_label}용 통합 시황
브리핑 1건을 작성합니다. 애드센스 등 광고 심사를 통과하려면 너무 짧은 콘텐츠는 안 되므로,
blog_content는 A4 용지 한 장 분량(공백 포함 한글 1,800~2,500자 정도)의 충실한 글로 작성하세요.
- [특징주 목록]에 있는 종목명/가격/등락률 수치, 그리고 각 종목 줄 아래 "↳ 관련 증권사 리포트"로
  달려있는 내용 외의 사실을 지어내지 마세요. 실적 발표, 공시, 뉴스, 수급 주체(외국인/기관) 등
  제공되지 않은 구체적인 원인은 절대 언급하지 마세요.
- "↳ 관련 증권사 리포트"가 달려있는 종목은 그 리포트 내용을 등락의 참고 배경으로 자연스럽게
  녹여서 설명하세요. 단, 리포트 시점이 오늘 등락과 인과관계가 명확히 확인된 것은 아니므로
  "오늘 상승은 이 리포트 때문이다"처럼 단정하지 말고 "증권사에서는 ~라는 의견을 냈다" 식으로
  참고 정보로만 소개하세요. "↳ 관련 증권사 리포트"가 없는 종목은 원인을 절대 추측하지 마세요.
- [오늘의 증권사 리포트 (특징주 외)] 섹션이 있으면, 이는 오늘 특징주 등락과 직접 연결되지 않은
  별도의 증권사 리포트 모음이니 마지막에 "오늘의 증권사 리포트" 같은 별도 소제목 섹션으로 간단히
  정리하세요 (특징주의 등락 원인으로 엮지 마세요).
- 분량을 채우는 방법은 사실 날조가 아니라: (1) 상승/하락 상위 종목 각각의 등락률·가격 수치를 표나
  목록으로 자세히 정리, (2) "등락률 상위 종목"이 시장에서 어떤 의미를 갖는지, KIS 등락률 순위가
  무엇을 보여주는지 등 일반적인 투자 지식 수준의 배경 설명 보충, (3) 단기 급등락 종목 투자 시
  일반적으로 유의할 점(변동성, 추격매수 위험 등)에 대한 원론적인 조언으로 채우세요.
- 날짜는 언급하지 마세요 (제목에도 넣지 마세요) — 별도로 시스템이 붙입니다.
- 한국어로, 명확하고 자연스럽게 작성하세요. 같은 문장을 반복하며 억지로 늘리지 마세요.
- 반드시 아래 JSON 형식으로만 답하세요. 그 외 설명이나 마크다운 코드블록은 절대 붙이지 마세요.
{{
  "ai_summary": "핵심 내용을 3줄로 요약한 문자열 (줄바꿈 문자로 구분)",
  "ai_analysis": "상승/하락 상위 종목의 등락률 분포와 (있다면) 관련 증권사 리포트에 대한 3~5문장 설명 (제공되지 않은 원인 추측 금지)",
  "blog_content": "블로그 포스팅용 본문 HTML. <h3>/<p>/<ul><li> 등 간단한 태그만 사용해 여러 소제목 섹션으로 구성하고 공백 포함 한글 1,800~2,500자 분량으로 작성"
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
        lines += [f"- {r['name']}({r['ticker']}): {r['text']}" for r in others]

    return "\n".join(lines)


def _simulation_briefing(session_label, movers_text):
    return {
        'ai_summary': SIMULATION_SUMMARY,
        'ai_analysis': SIMULATION_ANALYSIS,
        'blog_content': f"<p>{movers_text.replace(chr(10), '<br>')}</p>",
    }


def _error_briefing(session_label, movers_text):
    return {
        'ai_summary': ERROR_SUMMARY,
        'ai_analysis': ERROR_ANALYSIS,
        'blog_content': f"<p>{movers_text.replace(chr(10), '<br>')}</p>",
    }


def generate_featured_briefing(session_label, movers, reports=None):
    """RankedMover(GAINER/LOSER) 상위 종목 목록으로 개별 기사가 아닌 통합 특징주 브리핑
    1건을 생성한다. movers는 [{'rank_type','ticker','name','price','change_pct'}, ...] 형태.
    reports는 그날의 [리포트 브리핑] 기사를 [{'ticker','name','text'}, ...]로 넘긴 것으로,
    ticker가 movers와 겹치면 해당 종목의 등락 참고 근거로, 안 겹치면 별도 섹션으로 반영된다
    (movers_to_text 참고). 하루 두 번(장중/마감후)만 호출되므로 매 기사마다 AI를 호출하는
    기존 방식보다 토큰 비용이 훨씬 적다."""
    movers_text = movers_to_text(movers, reports)
    if not movers:
        return _simulation_briefing(session_label, movers_text)

    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return _simulation_briefing(session_label, movers_text)

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {'role': 'system', 'content': BRIEFING_SYSTEM_PROMPT.format(session_label=session_label)},
                {'role': 'user', 'content': f"[특징주 목록]\n{movers_text}"},
            ],
            max_tokens=3000,
            temperature=0.4,
            response_format={'type': 'json_object'},
        )
        data = json.loads(response.choices[0].message.content)
        return {
            'ai_summary': (data.get('ai_summary') or '').strip() or SIMULATION_SUMMARY,
            'ai_analysis': (data.get('ai_analysis') or '').strip() or SIMULATION_ANALYSIS,
            'blog_content': (data.get('blog_content') or '').strip() or f"<p>{movers_text}</p>",
        }
    except Exception:
        logger.exception("특징주 브리핑 AI 생성 실패 (session=%r)", session_label)
        return _error_briefing(session_label, movers_text)
