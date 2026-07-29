import logging

from django.conf import settings
from django.db.models import Max

from .models import AnalyzedArticle, MarketIndex, RankedMover, StockItem, StockPrediction

logger = logging.getLogger(__name__)

SIMULATION_ANSWER = (
    "챗봇 기능은 현재 준비 중입니다. 관리자가 OpenAI API 키를 설정하면 "
    "실시간 AI 답변이 제공됩니다. (현재 시뮬레이션 모드)"
)
ERROR_ANSWER = "일시적인 오류로 답변을 생성하지 못했습니다. 잠시 후 다시 시도해주세요."

SYSTEM_PROMPT = """당신은 NextFinUp의 AI 주식/경제 챗봇입니다.
- 아래 [제공 데이터]에 있는 내용만 근거로 답변하세요. 데이터에 없는 내용은 추측하지 말고 모른다고 답하세요.
- 주식, 증시, 경제, 투자와 무관한 질문에는 정중히 답변을 거절하고 주식/경제 관련 질문을 유도하세요.
- 한국어로, 간결하고 명확하게 답변하세요.
- 원화(원) 가격을 말할 때는 소수점 없이 정수로만 표시하세요 (예: 254,250원, 254,250.00원 금지).
- 답변 말미에 "본 답변은 투자 참고용이며 투자 손실에 대한 법적 책임을 지지 않습니다."를 짧게 덧붙이세요.

[제공 데이터]
{context}
"""

MAX_HISTORY_TURNS = 6
MAX_HISTORY_CONTENT_LEN = 500


# 그룹 접두사를 뺀 약칭으로도 종목을 언급하는 경우가 많다 (예: "SK하이닉스" → "하이닉스").
# 접두사를 뗀 나머지가 너무 짧으면(예: LG전자 → "전자") 흔한 단어와 겹쳐 오매칭될 수 있어 3자 이상일 때만 허용한다.
_STOCK_GROUP_PREFIXES = ('SK', 'LG', 'GS', 'CJ', 'KT', 'LS', 'DB', 'HD', 'HDC')


def _find_mentioned_stocks(question, limit=3):
    matched = []
    for ticker, name in StockItem.objects.filter(is_active=True).values_list('ticker', 'name'):
        candidates = [name]
        for prefix in _STOCK_GROUP_PREFIXES:
            if name.startswith(prefix) and len(name) - len(prefix) >= 3:
                candidates.append(name[len(prefix):])
        if ticker in question or any(candidate in question for candidate in candidates):
            matched.append((ticker, name))
        if len(matched) >= limit:
            break
    return matched


def _build_context(question):
    lines = []

    for market_type in ('KOSPI', 'KOSDAQ'):
        latest = MarketIndex.objects.filter(market_type=market_type).order_by('-date').first()
        if latest:
            lines.append(
                f"- {latest.get_market_type_display()} 지수({latest.date}): "
                f"{latest.close_price} ({latest.change_pct}% )"
            )

    gainers = RankedMover.objects.filter(rank_type='GAINER').order_by('rank')[:5]
    if gainers:
        gainer_str = ", ".join(f"{m.name}({m.change_pct}%)" for m in gainers)
        lines.append(f"- 상승률 상위 종목: {gainer_str}")

    losers = RankedMover.objects.filter(rank_type='LOSER').order_by('rank')[:5]
    if losers:
        loser_str = ", ".join(f"{m.name}({m.change_pct}%)" for m in losers)
        lines.append(f"- 하락률 상위 종목: {loser_str}")

    mentioned = _find_mentioned_stocks(question)
    for ticker, name in mentioned:
        pred = (
            StockPrediction.objects
            .filter(stock__ticker=ticker)
            .order_by('-date')
            .first()
        )
        if pred:
            next_close = f"{pred.pred_next_close:,.0f}원" if pred.pred_next_close is not None else "정보 없음"
            up_prob = f"{pred.up_probability * 100:.1f}%" if pred.up_probability is not None else "정보 없음"
            down_prob = f"{pred.down_probability * 100:.1f}%" if pred.down_probability is not None else "정보 없음"
            lines.append(
                f"- [{name}] {pred.date} 종가 {pred.close_price:,.0f}원, "
                f"AI 내일 예상종가 {next_close}, "
                f"상승확률 {up_prob}, 하락확률 {down_prob}, 매매신호 {pred.get_trading_signal_display()}"
            )
        articles = (
            AnalyzedArticle.objects
            .filter(stock__ticker=ticker)
            .order_by('-scraped_at')[:2]
        )
        for a in articles:
            suffix = f": {a.ai_summary}" if a.ai_summary else ""
            lines.append(f"- [{name} 관련 뉴스] {a.title}{suffix}")

    latest_news = AnalyzedArticle.objects.select_related('stock').order_by('-scraped_at')[:5]
    if latest_news:
        lines.append("- 최신 경제 뉴스:")
        for a in latest_news:
            stock_name = a.stock.name if a.stock else '종목 미매칭'
            suffix = f": {a.ai_summary}" if a.ai_summary else ""
            lines.append(f"  · [{stock_name}] {a.title}{suffix}")

    return "\n".join(lines) if lines else "제공할 데이터가 없습니다."


def _sanitize_history(history):
    cleaned = []
    for item in (history or [])[-MAX_HISTORY_TURNS:]:
        role = item.get('role')
        content = str(item.get('content', ''))[:MAX_HISTORY_CONTENT_LEN]
        if role in ('user', 'assistant') and content:
            cleaned.append({'role': role, 'content': content})
    return cleaned


def ask(question, history=None):
    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        return SIMULATION_ANSWER

    context = _build_context(question)
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT.format(context=context)}]
    messages.extend(_sanitize_history(history))
    messages.append({'role': 'user', 'content': question})

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=500,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logger.exception("챗봇 OpenAI 호출 실패 (question=%r)", question)
        return ERROR_ANSWER
