"""기사의 AI 3줄 요약/블로그 포스팅용 원고에 붙일 썸네일 이미지를 서버에서 직접 생성한다.
원문 기사의 사진/이미지는 절대 재사용하지 않는다(무단전재 이슈) — 종목명·등락 시그널 등
NextFinUp이 이미 갖고 있는 데이터만으로 매번 새로 그리는 브랜드 카드 이미지다."""
import textwrap

from PIL import Image, ImageDraw, ImageFont

CANVAS_SIZE = (1200, 630)  # 소셜 공유용 표준 OG 이미지 비율
BG_COLOR = "#16213a"
BRAND_COLOR = "#9fc0ff"
TITLE_COLOR = "#f4f6f9"
SUBTITLE_COLOR = "#c7d2e3"
FOOTER_COLOR = "#5b6b8c"

SIGNAL_COLORS = {
    'BUY': "#e53935",   # 국내 관례대로 상승=빨강
    'SELL': "#1e88e5",  # 하락=파랑
}
DEFAULT_SIGNAL_COLOR = "#8b98b8"  # 시그널 없음/중립

FONT_BOLD = "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"


def _font(path, size):
    return ImageFont.truetype(path, size)


def _wrap_by_width(draw, text, font, max_width, max_lines):
    """Pillow는 자동 줄바꿈이 없어서, 실제 렌더 폭을 기준으로 직접 줄바꿈한다.
    max_lines를 넘으면 마지막 줄을 "…"으로 잘라 붙인다."""
    words = list(text)  # 한글은 띄어쓰기 단위보다 글자 단위 줄바꿈이 잘림이 자연스럽다
    lines = []
    current = ""
    for ch in words:
        trial = current + ch
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = ch
            if len(lines) == max_lines:
                break
    else:
        if current:
            lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]

    if len(lines) == max_lines:
        last = lines[-1]
        while draw.textlength(last + "…", font=font) > max_width and len(last) > 1:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"

    return lines


def generate_thumbnail_image(title, subject_label, ticker=None, signal_color_key=None):
    """title: 기사 제목, subject_label: 종목명 또는 키워드(없으면 "경제 뉴스"),
    ticker: 종목코드(선택), signal_color_key: 'BUY'/'SELL'/None(latest_pred.trading_signal).
    반환: PNG 바이트."""
    img = Image.new("RGB", CANVAS_SIZE, BG_COLOR)
    draw = ImageDraw.Draw(img)

    accent = SIGNAL_COLORS.get(signal_color_key, DEFAULT_SIGNAL_COLOR)

    # 상단 액센트 바
    draw.rectangle([0, 0, CANVAS_SIZE[0], 10], fill=accent)

    margin = 70

    # 브랜드 워드마크
    brand_font = _font(FONT_BOLD, 32)
    draw.text((margin, 55), "NextFinUp", font=brand_font, fill=BRAND_COLOR)

    # 종목명(+티커) 또는 키워드
    subject_font = _font(FONT_BOLD, 56)
    subject_text = f"{subject_label} ({ticker})" if ticker else subject_label
    draw.text((margin, 150), subject_text, font=subject_font, fill=accent)

    # 종목명 아래 구분선
    draw.line([(margin, 235), (CANVAS_SIZE[0] - margin, 235)], fill="#2a3b5c", width=2)

    # 기사 제목 (최대 3줄, 넘치면 말줄임)
    title_font = _font(FONT_REGULAR, 42)
    max_text_width = CANVAS_SIZE[0] - margin * 2
    lines = _wrap_by_width(draw, title, title_font, max_text_width, max_lines=3)
    y = 275
    for line in lines:
        draw.text((margin, y), line, font=title_font, fill=TITLE_COLOR)
        y += 62

    # 하단 도메인 워터마크
    footer_font = _font(FONT_REGULAR, 26)
    draw.text((margin, CANVAS_SIZE[1] - 70), "nextfinup.com", font=footer_font, fill=FOOTER_COLOR)

    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_thumbnail_file(title, stock=None, matched_keyword=None):
    """news_ai_summarize_view/news_scrape_view에서 AI 요약이 만들어지는 시점에 호출.
    stock이 있으면 종목명/티커/최신 StockPrediction 시그널로, 없으면 매칭 키워드(또는 '경제
    뉴스')로 카드를 그려 ImageField에 바로 할당 가능한 ContentFile을 반환한다."""
    from django.core.files.base import ContentFile

    from .models import StockPrediction

    if stock:
        subject_label = stock.name
        ticker = stock.ticker
        latest_pred = StockPrediction.objects.filter(stock=stock).order_by('-date').first()
        signal_color_key = latest_pred.trading_signal if latest_pred else None
    else:
        subject_label = matched_keyword.keyword if matched_keyword else "경제 뉴스"
        ticker = None
        signal_color_key = None

    image_bytes = generate_thumbnail_image(title, subject_label, ticker, signal_color_key)
    return ContentFile(image_bytes, name="thumbnail.png")


if __name__ == "__main__":
    # 수동 테스트용: python articles/thumbnail.py 로 실행하면 /tmp에 샘플 3장을 그려본다.
    samples = [
        ("삼성전자, 목표주가 55만원→37만원…33% 하향한 이유는[클릭e종목]", "삼성전자", "005930", "SELL"),
        ("현대차, 3분기 영업이익 시장 예상치 크게 웃돌아…목표주가 줄상향", "현대차", "005380", "BUY"),
        ("코스피, 미 연준 금리 동결 소식에 강보합 마감", "코스피", None, None),
    ]
    for i, (title, subject, ticker, signal) in enumerate(samples):
        data = generate_thumbnail_image(title, subject, ticker, signal)
        path = f"/tmp/thumb_sample_{i}.png"
        with open(path, "wb") as f:
            f.write(data)
        print("wrote", path)
