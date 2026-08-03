import logging
import subprocess

from django.conf import settings
from django.db.models import Max

from .models import (
    AnalyzedArticle, MarketIndex, RankedMover, StockDailyPrice, StockItem, StockPrediction,
    StockRealtimePrice,
)

logger = logging.getLogger(__name__)

# collect_stock_data(주가 재수집)/run_stock_prediction(예측 재학습)은 무겁고 비정기적으로
# 수동 실행되는데, 돌아가는 도중엔 아직 갱신 전인 예측값이 최신인 것처럼 보일 수 있다.
# 프로세스가 떠 있는지 확인해, 돌고 있으면 그 사실을 답변에 반영한다.
_PIPELINE_COMMANDS = ('collect_stock_data', 'run_stock_prediction')


def _pipeline_is_running():
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'manage.py (' + '|'.join(_PIPELINE_COMMANDS) + ')'],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

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
- "지금/현재 가격이 얼마냐"는 질문에는 반드시 [실시간 현재가] 줄의 값을 쓰세요. [AI 예측] 줄의
  "기준일 종가"는 그 예측이 계산된 날짜의 종가일 뿐 오늘 가격이 아니므로, "지금 가격"으로
  혼동해서 답하지 마세요 — 두 값이 다르면(예측 데이터 갱신 지연 등) 그 사실도 짧게 언급하세요.
  단, [실시간 현재가] 줄에 괄호로 붙어있는 갱신 시각(예: "2026-08-03 06:25 기준 갱신")은
  내부 참고용일 뿐이니 답변 문장에 그대로 옮겨 적지 마세요 ("~시각 기준으로 갱신된 것입니다"
  같은 문구 금지) — 가격/등락률만 말하세요.
- 질문이 현재가만 물었더라도, [제공 데이터]에 그 종목의 [AI 예측] 줄(또는 "갱신 중" 안내)이
  있으면 답변에 같이 붙이세요 — 사용자가 매번 "내일 예상가는?"을 따로 다시 묻지 않아도 되도록,
  종목이 언급되면 현재가와 AI 예측(또는 갱신 상태)을 한 번에 답하는 게 기본입니다.
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
    pipeline_running = _pipeline_is_running() if mentioned else False
    for ticker, name in mentioned:
        # 실시간 현재가(5분 주기로 갱신)는 AI 예측용 일봉/예측 데이터(무거워서 수동 실행 주기)보다
        # 훨씬 자주 갱신되므로, "지금 얼마냐"는 질문엔 이 값을 써야 한다 — 둘을 섞으면 며칠 지난
        # 예측 기준일 종가를 "지금 가격"으로 잘못 답하게 된다.
        realtime = StockRealtimePrice.objects.filter(stock__ticker=ticker).first()
        if realtime:
            lines.append(
                f"- [{name}] 실시간 현재가 {realtime.close_price:,.0f}원 "
                f"({realtime.change_pct:+.2f}%, {realtime.updated_at:%Y-%m-%d %H:%M} 기준 갱신)"
            )

        if pipeline_running:
            # 재수집/재학습이 진행 중이면 기존 예측은 곧 낡은 값이 될 걸 알면서 보여주는 셈이라,
            # 예측 수치 대신 갱신 중이라는 사실만 전달한다 (실시간 현재가는 위에서 이미 보여줬다).
            lines.append(f"- [{name}] AI 예측 데이터를 지금 갱신(재학습)하는 중입니다. 잠시 후 다시 확인해주세요.")
        else:
            pred = (
                StockPrediction.objects
                .filter(stock__ticker=ticker)
                .order_by('-date')
                .first()
            )
            if pred:
                # StockPrediction엔 예측값만 있고 실가격은 없다(StockDailyPrice가 분리 보관) —
                # 기준일 종가는 그쪽에서 따로 조회해야 한다.
                daily = StockDailyPrice.objects.filter(stock__ticker=ticker, date=pred.date).first()
                base_close = f"{daily.close_price:,.0f}원" if daily else "정보 없음"
                next_close = f"{pred.pred_next_close:,.0f}원" if pred.pred_next_close is not None else "정보 없음"
                up_prob = f"{pred.up_probability * 100:.1f}%" if pred.up_probability is not None else "정보 없음"
                down_prob = f"{pred.down_probability * 100:.1f}%" if pred.down_probability is not None else "정보 없음"
                lines.append(
                    f"- [{name}] AI 예측(기준일 {pred.date} 종가 {base_close}): "
                    f"내일 예상종가 {next_close}, "
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
