import base64
import json
import logging
import time

from django.conf import settings
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from openai import OpenAI

logger = logging.getLogger(__name__)

# Gemini가 "high demand"로 503을 반환하는 건 구글 쪽의 일시적 과부하이지 요청 자체의 문제가
# 아니라, google-genai SDK 내부 재시도(tenacity)가 이미 실패한 뒤에도 여기서 한 번 더
# 짧은 간격으로 재시도한다 — 회원이 'AI 요약' 버튼을 눌렀을 때 흔한 순간적 스파이크로 바로
# 실패 처리되는 걸 줄이기 위함(실측: 같은 기사에서 몇 분 간격으로 반복 실패한 사례 확인).
GEMINI_RETRY_ATTEMPTS = 3
GEMINI_RETRY_BACKOFF_SECONDS = 3

# 기사 3줄 요약/투자 분석/블로그 초안(generate_draft)에 쓰는 모델. 별칭(latest)을 써서
# 특정 날짜 버전이 신규 사용자에게 막히거나(예: gemini-2.5-flash) 무료 쿼터가 갑자기 0으로
# 바뀌는 문제(예: gemini-2.0-flash)를 피한다 — 실제 테스트에서 확인된 이슈.
GEMINI_MODEL = "gemini-flash-latest"

SIMULATION_SUMMARY = (
    "AI 요약 기능은 현재 준비 중입니다. 관리자가 AI 요약용 API 키를 설정하면 "
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
  배경지식과 산업/기업 맥락, 투자자 관점에서 통상적으로 짚어볼 시사점·유의점 위주로 NextFinUp만의
  독자적인 해설을 작성하세요. "기사에 따르면", "원문에서는" 같이 원문을 인용하는 듯한 표현은
  쓰지 마세요.
- 한국어로, 명확하고 자연스럽게 작성하세요. 같은 문장을 반복하며 억지로 늘리지 마세요.
- 반드시 아래 JSON 형식으로만 답하세요. 그 외 설명이나 마크다운 코드블록은 절대 붙이지 마세요.
{
  "ai_summary": "제목이 가리키는 주제를 3줄로 정리한 문자열 (줄바꿈 문자로 구분, 원문 문장 재구성 금지)",
  "ai_analysis": "투자자 관점에서 통상적으로 짚어볼 시사점을 3~5문장으로 정리한 문자열",
  "blog_content": "블로그 포스팅용 본문 HTML. <h3>/<p>/<ul><li> 등 간단한 태그만 사용해 배경/산업맥락/투자 시사점 위주 섹션으로 구성하고(원문 인용·재구성 없이) 공백 포함 한글 1,200~1,800자 분량으로 작성"
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


def _call_gemini_json(system_prompt, user_prompt, max_output_tokens):
    """Gemini에 system_instruction+user prompt를 보내 JSON 객체로 파싱해 반환한다.
    response_mime_type='application/json'으로 OpenAI의 response_format=json_object와
    동일하게 JSON만 반환하도록 강제한다. 503(과부하) 등 일시적 오류는 SDK 내부 재시도가 이미
    실패한 뒤에도 여기서 짧게 한 번 더 재시도한다."""
    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    last_error = None
    for attempt in range(1, GEMINI_RETRY_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type='application/json',
                    max_output_tokens=max_output_tokens,
                    temperature=0.4,
                ),
            )
            return json.loads(response.text)
        except genai_errors.ServerError as exc:
            last_error = exc
            if attempt < GEMINI_RETRY_ATTEMPTS:
                logger.warning(
                    "Gemini 서버 오류(%s), %d/%d회 재시도 대기 중: %s",
                    exc.code, attempt, GEMINI_RETRY_ATTEMPTS, exc,
                )
                time.sleep(GEMINI_RETRY_BACKOFF_SECONDS * attempt)
    raise last_error


def generate_draft(title, content, restricted=False, related_stock_name=None):
    """스크래핑한 기사 (제목, 본문)으로부터 AI 3줄 요약 / 투자 관점 분석 / 블로그 포스팅용
    HTML 원고를 생성한다. Gemini API 키가 없으면 chatbot_client와 동일하게 시뮬레이션
    모드로 동작해, 스크래핑~편집 화면 진입 흐름 자체는 항상 끊기지 않게 한다.

    restricted=True(utils.detect_reuse_restriction으로 원문에서 "무단전재 배포금지, AI 학습
    및 활용 금지" 류 문구가 감지된 경우)면 원문 본문은 아예 참조하지 않고 제목/관련 종목명만으로
    별도 프롬프트(RESTRICTED_SYSTEM_PROMPT)를 태워 NextFinUp 자체 해설을 생성한다 — 원문
    인용·재구성 없이, 출처(source_media/original_url) 표시는 그대로 유지한 채 발행된다."""
    if restricted:
        return _generate_restricted_draft(title, related_stock_name)

    if not content:
        return _simulation_draft(content)

    if not settings.GEMINI_API_KEY or settings.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        return _simulation_draft(content)

    user_prompt = f"[제목]\n{title}\n\n[원문]\n{content[:6000]}"

    try:
        # Gemini(gemini-flash-latest)는 응답 전에 내부적으로 "thinking" 토큰을 먼저 소비하고
        # 그것도 max_output_tokens에 포함된다 — 실측 결과 thinking에만 2,500~3,500토큰 정도
        # 쓰였다. 여유 없이 잡으면 thinking만 끝나고 실제 JSON 출력이 중간에 잘린다(finish_reason
        # MAX_TOKENS), 그래서 OpenAI 때보다 훨씬 넉넉하게 잡는다.
        data = _call_gemini_json(SYSTEM_PROMPT, user_prompt, max_output_tokens=6000)
        return {
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

    if not settings.GEMINI_API_KEY or settings.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        return _simulation_draft('')

    user_prompt = f"[기사 제목]\n{title}"
    if related_stock_name:
        user_prompt += f"\n\n[참고 정보]\n관련 종목: {related_stock_name}"

    try:
        data = _call_gemini_json(RESTRICTED_SYSTEM_PROMPT, user_prompt, max_output_tokens=6000)
        return {
            'ai_summary': (data.get('ai_summary') or '').strip() or SIMULATION_SUMMARY,
            'ai_analysis': (data.get('ai_analysis') or '').strip() or SIMULATION_ANALYSIS,
            'blog_content': (data.get('blog_content') or '').strip() or f"<p>{title}</p>",
        }
    except Exception:
        logger.exception("제한 기사 AI 초안 생성 실패 (title=%r)", title)
        return _error_draft('')


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
    url = f"https://finance.naver.com/item/main.naver?code={ticker}"
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


def generate_thumbnail_image_bytes(title, ai_summary):
    """경제 뉴스가 아닌 일반 기사(관련 종목·매칭 키워드가 없는 기사)의 썸네일을 gpt-image-2로
    직접 그려 PNG 바이트로 반환한다. 종목 시세·코스피/코스닥 지수처럼 정확한 수치를 보여줘야
    하는 카드는 여기 쓰지 않는다 — AI 이미지 생성은 숫자를 정확히 보장할 수 없어, 그런 카드는
    thumbnail.py가 실제 데이터로 직접 그린다(_draw_market_grid/_draw_index_summary_boxes).
    비용을 낮게 유지하기 위해 quality="low"만 사용한다. OPENAI_API_KEY가 없거나(플레이스홀더
    포함) 호출이 실패하면 None을 반환해, 호출부(thumbnail.build_thumbnail_file)가 조용히 기존
    PIL 텍스트 패널 카드로 폴백하게 한다."""
    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return None

    prompt = (
        "Create a clean, modern editorial illustration to use as a Korean news article's cover "
        "thumbnail. Absolutely no text, letters, numbers, charts, graphs, or logos anywhere in "
        "the image — illustration only.\n"
        f"Headline: {title}\n"
        f"Summary: {(ai_summary or '')[:400]}\n"
        "Style: flat editorial illustration, muted navy/blue color palette, high contrast, "
        "professional news-site cover art."
    )
    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.images.generate(
            model="gpt-image-2",
            prompt=prompt,
            size="1536x1024",
            quality="low",
        )
        return base64.b64decode(response.data[0].b64_json)
    except Exception:
        logger.exception("gpt-image-2 썸네일 생성 실패 (title=%r)", title)
        return None
