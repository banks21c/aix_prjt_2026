"""기사의 AI 3줄 요약/블로그 포스팅용 원고에 붙일 썸네일 이미지를 서버에서 직접 생성한다.
원문 기사의 사진/이미지는 절대 재사용하지 않는다(무단전재 이슈) — 종목명·시세·등락 시그널 등
NextFinUp이 이미 갖고 있는 데이터만으로 매번 새로 그리는 카드 이미지다."""
from datetime import date

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .utils import format_signed_pct, format_signed_won, format_trading_value, format_volume, format_won

CANVAS_SIZE = (1200, 630)  # 소셜 공유용 표준 OG 이미지 비율
BG_COLOR_TOP = "#1c2b4d"
BG_COLOR_BOTTOM = "#0e1526"
TITLE_COLOR = "#f4f6f9"
SUBTITLE_COLOR = "#c7d2e3"
FOOTER_COLOR = "#5b6b8c"
PANEL_BG = "#101c36"
PANEL_BORDER = "#2a3b5c"
GRID_LINE = "#22314f"
NEUTRAL_COLOR = "#8b98b8"

SIGNAL_COLORS = {
    'BUY': "#e53935",   # 국내 관례대로 상승=빨강
    'SELL': "#1e88e5",  # 하락=파랑
    'HOLD': "#f2b134",
}
SIGNAL_LABELS = {'BUY': '매수', 'SELL': '매도', 'HOLD': '관망'}
DEFAULT_SIGNAL_COLOR = "#8b98b8"  # 시그널 없음/중립

FONT_BOLD = "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"


def _font(path, size):
    return ImageFont.truetype(path, size)


def _paint_gradient(img):
    """단색 배경 대신 위(밝은 남색)→아래(짙은 남색)로 은은하게 번지는 배경을 그린다."""
    from PIL import ImageColor
    top_rgb = ImageColor.getrgb(BG_COLOR_TOP)
    bottom_rgb = ImageColor.getrgb(BG_COLOR_BOTTOM)
    draw = ImageDraw.Draw(img)
    height = img.height
    for y in range(height):
        t = y / height
        color = tuple(int(top_rgb[i] + (bottom_rgb[i] - top_rgb[i]) * t) for i in range(3))
        draw.line([(0, y), (img.width, y)], fill=color)


def _add_glow(img, accent_hex, center, radius):
    """카드가 밋밋해 보이지 않도록, 시그널 색상을 흐릿하게 번진 원형 광원으로 우측 상단에 얹는다."""
    from PIL import ImageColor
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    r, g, b = ImageColor.getrgb(accent_hex)
    glow_draw.ellipse(
        [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
        fill=(r, g, b, 70),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    return Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")


def _draw_signal_badge(draw, x, y, signal_key, accent):
    """매수/매도/관망 뱃지: 방향 삼각형 아이콘 + 텍스트를 둥근 알약 모양 배경 위에 그린다."""
    label = SIGNAL_LABELS[signal_key]
    font = _font(FONT_BOLD, 26)
    text_w = draw.textlength(label, font=font)

    icon_w = 14
    pad_x = 16
    height = 42
    width = pad_x + icon_w + 8 + text_w + pad_x
    x0, y0 = x, y
    x1, y1 = x0 + width, y0 + height
    draw.rounded_rectangle([x0, y0, x1, y1], radius=height / 2, fill=accent)

    cy = (y0 + y1) / 2
    icon_x = x0 + pad_x
    if signal_key == 'BUY':
        draw.polygon([(icon_x, cy + 7), (icon_x + icon_w, cy + 7), (icon_x + icon_w / 2, cy - 8)], fill="white")
    elif signal_key == 'SELL':
        draw.polygon([(icon_x, cy - 7), (icon_x + icon_w, cy - 7), (icon_x + icon_w / 2, cy + 8)], fill="white")
    else:
        draw.rectangle([icon_x, cy - 2, icon_x + icon_w, cy + 2], fill="white")

    draw.text((icon_x + icon_w + 8, cy), label, font=font, fill="white", anchor="lm")
    return width


def _draw_market_grid(draw, x0, y0, x1, y1, market_data):
    """현재가/전일대비/등락률/전일가/거래량/거래대금을 3열×2행 그리드 패널로 그린다."""
    change = market_data['change']
    change_pct = market_data['change_pct']
    move_color = SIGNAL_COLORS['BUY'] if change > 0 else SIGNAL_COLORS['SELL'] if change < 0 else NEUTRAL_COLOR

    cells = [
        ("현재가", format_won(market_data['current_price']), TITLE_COLOR),
        ("전일대비", format_signed_won(change), move_color),
        ("등락률", format_signed_pct(change_pct), move_color),
        ("전일가", format_won(market_data['prev_close']), TITLE_COLOR),
        ("거래량", format_volume(market_data['volume']), SUBTITLE_COLOR),
        ("거래대금", format_trading_value(market_data['trading_value']), SUBTITLE_COLOR),
    ]

    draw.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=PANEL_BG, outline=PANEL_BORDER, width=2)

    col_w = (x1 - x0) / 3
    row_h = (y1 - y0) / 2
    label_font = _font(FONT_REGULAR, 22)
    value_font = _font(FONT_BOLD, 30)

    for col in (1, 2):
        lx = x0 + col_w * col
        draw.line([(lx, y0 + 12), (lx, y1 - 12)], fill=GRID_LINE, width=1)
    draw.line([(x0 + 12, y0 + row_h), (x1 - 12, y0 + row_h)], fill=GRID_LINE, width=1)

    for i, (label, value, color) in enumerate(cells):
        row, col = divmod(i, 3)
        cx0 = x0 + col_w * col + 28
        cy0 = y0 + row_h * row + 16
        draw.text((cx0, cy0), label, font=label_font, fill=SUBTITLE_COLOR)
        draw.text((cx0, cy0 + 28), value, font=value_font, fill=color)


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


def generate_thumbnail_image(title, subject_label, ticker=None, signal_color_key=None,
                              category_label=None, market_data=None):
    """title: 기사 제목, subject_label: 종목명 또는 키워드(없으면 "경제 뉴스"),
    ticker: 종목코드(선택), signal_color_key: 'BUY'/'SELL'/'HOLD'/None,
    category_label: 상단 태그(예: "종목 분석 리포트", "특징주 브리핑"),
    market_data: {'current_price','prev_close','change','change_pct','volume','trading_value'}
    (있으면 하단에 시세 그리드를 추가로 그린다. 종목 실시간 시세가 없으면 None).
    반환: PNG 바이트."""
    accent = SIGNAL_COLORS.get(signal_color_key, DEFAULT_SIGNAL_COLOR)

    img = Image.new("RGB", CANVAS_SIZE, BG_COLOR_BOTTOM)
    _paint_gradient(img)
    img = _add_glow(img, accent, center=(CANVAS_SIZE[0] - 120, 80), radius=260)
    draw = ImageDraw.Draw(img)

    # 상단 액센트 바
    draw.rectangle([0, 0, CANVAS_SIZE[0], 10], fill=accent)

    margin = 70

    # 우측 상단: 날짜
    date_text = date.today().strftime("%Y.%m.%d")
    draw.text((CANVAS_SIZE[0] - margin, 46), date_text, font=_font(FONT_REGULAR, 22), fill=FOOTER_COLOR, anchor="rm")

    # 우측 상단(날짜 아래): 카테고리 태그
    if category_label:
        tag_font = _font(FONT_BOLD, 22)
        tag_w = draw.textlength(category_label, font=tag_font)
        pad_x, pad_y = 16, 8
        tag_h = 22 + pad_y * 2
        x1 = CANVAS_SIZE[0] - margin
        x0 = x1 - tag_w - pad_x * 2
        y0 = 68
        y1 = y0 + tag_h
        draw.rounded_rectangle([x0, y0, x1, y1], radius=tag_h / 2, outline=accent, width=2)
        draw.text(((x0 + x1) / 2, (y0 + y1) / 2), category_label, font=tag_font, fill=accent, anchor="mm")

    # 종목명(+티커) 또는 키워드
    subject_font = _font(FONT_BOLD, 56)
    subject_text = f"{subject_label} ({ticker})" if ticker else subject_label
    draw.text((margin, 118), subject_text, font=subject_font, fill=accent)
    subject_w = draw.textlength(subject_text, font=subject_font)

    # 매수/매도/관망 시그널 뱃지 (종목 기사에만 존재)
    if signal_color_key in SIGNAL_LABELS:
        _draw_signal_badge(draw, margin + subject_w + 24, 136, signal_color_key, accent)

    # 종목명 아래 구분선
    draw.line([(margin, 203), (CANVAS_SIZE[0] - margin, 203)], fill="#2a3b5c", width=2)

    # 기사 제목 (최대 3줄, 넘치면 말줄임)
    title_font = _font(FONT_REGULAR, 42)
    max_text_width = CANVAS_SIZE[0] - margin * 2
    lines = _wrap_by_width(draw, title, title_font, max_text_width, max_lines=3)
    y = 235
    for line in lines:
        draw.text((margin, y), line, font=title_font, fill=TITLE_COLOR)
        y += 58

    # 시세 그리드 (현재가/전일대비/등락률/전일가/거래량/거래대금) — 실시간 시세가 있을 때만
    if market_data:
        _draw_market_grid(draw, margin, 435, CANVAS_SIZE[0] - margin, 600, market_data)

    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_thumbnail_file(title, stock=None, matched_keyword=None, category_label=None):
    """news_ai_summarize_view/news_scrape_view/generate_featured_stock_briefing에서 AI 요약이
    만들어지는 시점에 호출. stock이 있으면 종목명/티커/최신 매매 시그널·실시간 시세로, 없으면
    매칭 키워드(또는 '경제 뉴스')로 카드를 그려 ImageField에 바로 할당 가능한 ContentFile을 반환한다.
    category_label을 넘기면 상단 태그를 기본값(종목 분석 리포트/AI 요약 리포트) 대신 그 값으로 쓴다
    (예: 특징주 브리핑 커맨드는 "특징주 브리핑"을 넘김)."""
    from django.core.files.base import ContentFile

    from .models import StockPrediction, StockRealtimePrice

    if stock:
        subject_label = stock.name
        ticker = stock.ticker
        latest_pred = StockPrediction.objects.filter(stock=stock).order_by('-date').first()
        signal_color_key = latest_pred.trading_signal if latest_pred else None
        category_label = category_label or "종목 분석 리포트"

        market_data = None
        realtime = StockRealtimePrice.objects.filter(stock=stock).first()
        if realtime:
            market_data = {
                'current_price': realtime.close_price,
                'prev_close': realtime.close_price - realtime.change,
                'change': realtime.change,
                'change_pct': realtime.change_pct,
                'volume': realtime.volume,
                'trading_value': realtime.close_price * realtime.volume,
            }
    else:
        subject_label = matched_keyword.keyword if matched_keyword else "경제 뉴스"
        ticker = None
        signal_color_key = None
        market_data = None
        category_label = category_label or "AI 요약 리포트"

    image_bytes = generate_thumbnail_image(
        title, subject_label, ticker, signal_color_key,
        category_label=category_label,
        market_data=market_data,
    )
    return ContentFile(image_bytes, name="thumbnail.png")


if __name__ == "__main__":
    # 수동 테스트용: python articles/thumbnail.py 로 실행하면 /tmp에 샘플 카드를 그려본다.
    samples = [
        ("삼성전자, 목표주가 55만원→37만원…33% 하향한 이유는[클릭e종목]", "삼성전자", "005930", "SELL",
         "종목 분석 리포트",
         {'current_price': 71500, 'prev_close': 73200, 'change': -1700, 'change_pct': -2.32,
          'volume': 18234567, 'trading_value': 71500 * 18234567}),
        ("현대차, 3분기 영업이익 시장 예상치 크게 웃돌아…목표주가 줄상향", "현대차", "005380", "BUY",
         "종목 분석 리포트",
         {'current_price': 245500, 'prev_close': 236000, 'change': 9500, 'change_pct': 4.03,
          'volume': 3456789, 'trading_value': 245500 * 3456789}),
        ("코스피, 미 연준 금리 동결 소식에 강보합 마감", "코스피", None, None, "AI 요약 리포트", None),
        ("2026-07-31 장중 특징주 브리핑", "경제 뉴스", None, None, "특징주 브리핑", None),
    ]
    for i, (title, subject, ticker, signal, category, market_data) in enumerate(samples):
        data = generate_thumbnail_image(title, subject, ticker, signal, category, market_data)
        path = f"/tmp/thumb_sample_{i}.png"
        with open(path, "wb") as f:
            f.write(data)
        print("wrote", path)
