"""기사의 AI 3줄 요약/블로그 포스팅용 원고에 붙일 썸네일 이미지를 서버에서 직접 생성한다.
원문 기사의 사진/이미지는 절대 재사용하지 않는다(무단전재 이슈) — 종목명·시세·등락 시그널 등
NextFinUp이 이미 갖고 있는 데이터만으로 매번 새로 그리는 카드 이미지다."""
import re
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
    """현재가/전일대비/등락률/전일가/거래량/거래대금(+ AI 예상종가가 있으면 3행째)을
    3열 그리드 패널로 그린다."""
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
    pred_next_close = market_data.get('pred_next_close')
    if pred_next_close is not None:
        # 다른 종목 기사와 섞였을 때 예측값 하나만 있는 줄이 휑해 보이지 않도록 나머지
        # 두 칸은 비워둔다(값이 없을 때만 대신 채울 게 마땅치 않아서 — 억지로 채우지 않음).
        cells.append(("AI 예상종가", format_won(pred_next_close), SIGNAL_COLORS['HOLD']))

    draw.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=PANEL_BG, outline=PANEL_BORDER, width=2)

    rows = 3 if pred_next_close is not None else 2
    col_w = (x1 - x0) / 3
    row_h = (y1 - y0) / rows
    label_font = _font(FONT_REGULAR, 22)
    value_font = _font(FONT_BOLD, 30)

    for col in (1, 2):
        lx = x0 + col_w * col
        draw.line([(lx, y0 + 12), (lx, y1 - 12)], fill=GRID_LINE, width=1)
    for row in range(1, rows):
        ly = y0 + row_h * row
        draw.line([(x0 + 12, ly), (x1 - 12, ly)], fill=GRID_LINE, width=1)

    for i, (label, value, color) in enumerate(cells):
        row, col = divmod(i, 3)
        cx0 = x0 + col_w * col + 28
        cy0 = y0 + row_h * row + 16
        draw.text((cx0, cy0), label, font=label_font, fill=SUBTITLE_COLOR)
        draw.text((cx0, cy0 + 28), value, font=value_font, fill=color)


def _draw_index_summary_boxes(draw, x0, y0, x1, y1, index_rows):
    """특정 종목이 없는 카드(예: 특징주 브리핑)용 — 코스피/코스닥을 좌/우로 나란히 놓인
    별도 박스 2개로 그린다(이전 버전은 위아래로 쌓았다). 헤드라인이 제목으로 대체되며 생긴
    여유 공간 덕분에 글자 크기를 이전 대비 큼직하게 키웠다.
    index_rows: [{'label','close','change','change_pct','volume','flows'}, ...] (코스피, 코스닥 순).
    flows: {'foreign','institution','retail'} (수량, 주) — 없으면 그 줄들은 생략."""
    n = max(len(index_rows), 1)
    gap = 20
    box_w = (x1 - x0 - gap * (n - 1)) / n

    label_font = _font(FONT_BOLD, 34)
    value_font = _font(FONT_BOLD, 44)
    pct_font = _font(FONT_BOLD, 32)
    sub_font = _font(FONT_REGULAR, 24)

    def _signed_qty(v):
        return f"{v:+,.0f}주" if v is not None else "-"

    for i, idx in enumerate(index_rows):
        bx0 = x0 + (box_w + gap) * i
        bx1 = bx0 + box_w
        draw.rounded_rectangle([bx0, y0, bx1, y1], radius=16, fill=PANEL_BG, outline=PANEL_BORDER, width=2)

        change = idx['change']
        move_color = SIGNAL_COLORS['BUY'] if change > 0 else SIGNAL_COLORS['SELL'] if change < 0 else NEUTRAL_COLOR
        cx = bx0 + 30
        cy = y0 + 30

        # 시장명
        draw.text((cx, cy), idx['label'], font=label_font, fill=SUBTITLE_COLOR)
        # 지수값 + 등락률
        cy += 44
        draw.text((cx, cy), f"{idx['close']:,.2f}", font=value_font, fill=TITLE_COLOR)
        cy += 56
        draw.text((cx, cy), format_signed_pct(idx['change_pct']), font=pct_font, fill=move_color)
        # 거래량
        cy += 48
        vol_text = f"거래량 {format_volume(idx['volume'])}" if idx.get('volume') else "거래량 -"
        draw.text((cx, cy), vol_text, font=sub_font, fill=SUBTITLE_COLOR)

        # 외인/기관/개인 순매수 수량 (있을 때만, 각각 한 줄씩)
        flows = idx.get('flows')
        if flows:
            cy += 40
            for f_label, f_key in (('외인', 'foreign'), ('기관', 'institution'), ('개인', 'retail')):
                draw.text((cx, cy), f"{f_label} {_signed_qty(flows.get(f_key))}", font=sub_font, fill=SUBTITLE_COLOR)
                cy += 34


_LEADING_MARKER_RE = re.compile(r'^\s*(?:[0-9]+[.)]|[-•·])\s*')


def _draw_summary_panel(draw, x0, y0, x1, y1, summary_lines):
    """종목 시세도, 코스피/코스닥 지수 요약도 그릴 게 없는 카드(=경제 뉴스가 아닌 일반 기사)용 —
    빈 공간을 그대로 두는 대신 AI 3줄 요약을 한 줄당 한 줄로 짧게 줄여 채운다."""
    draw.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=PANEL_BG, outline=PANEL_BORDER, width=2)

    label_font = _font(FONT_REGULAR, 22)
    line_font = _font(FONT_REGULAR, 26)
    max_width = (x1 - x0) - 56

    draw.text((x0 + 28, y0 + 16), "핵심 요약", font=label_font, fill=SUBTITLE_COLOR)

    y = y0 + 52
    line_gap = 36
    for raw_line in summary_lines[:3]:
        text = _LEADING_MARKER_RE.sub('', raw_line).strip()
        if not text:
            continue
        [wrapped] = _wrap_by_width(draw, text, line_font, max_width, max_lines=1) or [text]
        draw.text((x0 + 28, y), f"· {wrapped}", font=line_font, fill=TITLE_COLOR)
        y += line_gap


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


def _compose_ai_image_background(ai_image_bytes):
    """gpt-image-2가 만든 이미지(비율이 카드와 정확히 안 맞을 수 있음)를 1200x630 캔버스에
    꽉 차게(cover) 리사이즈한 뒤 남는 부분을 가운데 기준으로 잘라낸다."""
    from io import BytesIO
    src = Image.open(BytesIO(ai_image_bytes)).convert("RGB")
    target_w, target_h = CANVAS_SIZE
    if src.width / src.height > target_w / target_h:
        new_h = target_h
        new_w = round(src.width * (target_h / src.height))
    else:
        new_w = target_w
        new_h = round(src.height * (target_w / src.width))
    resized = src.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _draw_bottom_gradient(img, height=300):
    """AI 이미지 위에 얹는 제목 텍스트가 배경과 상관없이 항상 읽히도록, 카드 하단에 어두운
    그라데이션을 덧씌운다."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for i in range(height):
        alpha = int(210 * (i / height))
        y = img.height - height + i
        draw.line([(0, y), (img.width, y)], fill=(14, 21, 38, alpha))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _render_ai_hero_card(title, category_label, ai_image_bytes):
    """gpt-image-2로 생성한 일러스트를 배경 전체에 깔고, 그 위에 액센트 바/날짜/카테고리
    태그/제목만 얹는 단순한 레이아웃 — 경제 뉴스가 아닌 일반 기사용. 종목 시세·코스피/코스닥
    카드와 달리 정확한 수치를 보여줄 게 없으므로 그 자리를 이미지 자체가 대신한다."""
    img = _compose_ai_image_background(ai_image_bytes)
    img = _draw_bottom_gradient(img)
    draw = ImageDraw.Draw(img)
    accent = DEFAULT_SIGNAL_COLOR
    margin = 70

    draw.rectangle([0, 0, CANVAS_SIZE[0], 10], fill=accent)

    date_text = date.today().strftime("%Y.%m.%d")
    draw.text((CANVAS_SIZE[0] - margin, 46), date_text, font=_font(FONT_REGULAR, 22), fill="#e7ecf5", anchor="rm")

    if category_label:
        tag_font = _font(FONT_BOLD, 22)
        tag_w = draw.textlength(category_label, font=tag_font)
        pad_x, pad_y = 16, 8
        tag_h = 22 + pad_y * 2
        x1 = CANVAS_SIZE[0] - margin
        x0 = x1 - tag_w - pad_x * 2
        y0 = 68
        y1 = y0 + tag_h
        draw.rounded_rectangle([x0, y0, x1, y1], radius=tag_h / 2, fill=(14, 21, 38, 180), outline="white", width=2)
        draw.text(((x0 + x1) / 2, (y0 + y1) / 2), category_label, font=tag_font, fill="white", anchor="mm")

    title_font = _font(FONT_BOLD, 44)
    max_text_width = CANVAS_SIZE[0] - margin * 2
    lines = _wrap_by_width(draw, title, title_font, max_text_width, max_lines=2)
    y = CANVAS_SIZE[1] - 56 - len(lines) * 56
    for line in lines:
        draw.text((margin, y), line, font=title_font, fill="white")
        y += 56

    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_thumbnail_image(title, subject_label, ticker=None, signal_color_key=None,
                              category_label=None, market_data=None, index_summary=None,
                              summary_lines=None, ai_background_bytes=None):
    """title: 기사 제목, subject_label: 종목명 또는 키워드(없으면 "AI 요약"),
    ticker: 종목코드(선택), signal_color_key: 'BUY'/'SELL'/'HOLD'/None,
    category_label: 상단 태그(예: "종목 분석 리포트", "특징주 브리핑"),
    market_data: {'current_price','prev_close','change','change_pct','volume','trading_value'}
    (있으면 하단에 시세 그리드를 추가로 그린다. 종목 실시간 시세가 없으면 None).
    index_summary: [{'label','close','change','change_pct','volume'}, ...] (코스피/코스닥 등
    특정 종목이 없는 카드용 — market_data가 없을 때만 대신 그린다).
    summary_lines: AI 3줄 요약 줄 목록 — market_data도 index_summary도 없는(=경제 뉴스가
    아닌) 카드에서, 빈 하단 공간에 짧게 줄인 요약을 대신 그릴 때 쓴다(ai_background_bytes가
    없을 때의 폴백).
    ai_background_bytes: gpt-image-2로 생성한 PNG 바이트 — 있으면 이 함수의 나머지 인자를
    전부 무시하고 _render_ai_hero_card로 그린다(경제 뉴스가 아닌 기사용 대표 레이아웃).
    반환: PNG 바이트."""
    if ai_background_bytes:
        return _render_ai_hero_card(title, category_label, ai_background_bytes)

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

    if index_summary:
        # 특정 종목이 없는 카드(특징주 브리핑 등)는 "경제 뉴스" 같은 자리표시자 라벨 대신
        # 기사 제목을 큼직한 헤드라인으로 바로 보여주고, 아래 코스피/코스닥 박스를 훨씬 위로
        # 끌어올려 크게(좌/우 박스로 나란히) 그린다 — 세로 공간이 넉넉해진 만큼 글자도 키운다.
        headline_font = _font(FONT_BOLD, 50)
        max_text_width = CANVAS_SIZE[0] - margin * 2
        lines = _wrap_by_width(draw, title, headline_font, max_text_width, max_lines=2)
        y = 122
        for line in lines:
            draw.text((margin, y), line, font=headline_font, fill=TITLE_COLOR)
            y += 62

        divider_y = y + 16
        draw.line([(margin, divider_y), (CANVAS_SIZE[0] - margin, divider_y)], fill="#2a3b5c", width=2)

        _draw_index_summary_boxes(draw, margin, divider_y + 30, CANVAS_SIZE[0] - margin, 612, index_summary)
    else:
        # 종목명(+티커) — 날짜 줄에서 30px 아래로 내려 배치.
        subject_font = _font(FONT_BOLD, 44)
        subject_text = f"{subject_label} ({ticker})" if ticker else subject_label
        draw.text((margin, 76), subject_text, font=subject_font, fill=accent, anchor="lm")
        subject_w = draw.textlength(subject_text, font=subject_font)

        # 매수/매도/관망 시그널 뱃지 (종목 기사에만 존재)
        if signal_color_key in SIGNAL_LABELS:
            _draw_signal_badge(draw, margin + subject_w + 24, 55, signal_color_key, accent)

        # 종목명 아래 구분선
        draw.line([(margin, 130), (CANVAS_SIZE[0] - margin, 130)], fill="#2a3b5c", width=2)

        # 기사 제목 (최대 2줄, 넘치면 말줄임) — 종목명을 따라 30px 아래로 내린 데다 아래
        # 그리드/요약 패널까지 50px 위로 끌어올려서, 3줄까지 쓰면 패널과 겹친다.
        title_font = _font(FONT_REGULAR, 42)
        max_text_width = CANVAS_SIZE[0] - margin * 2
        lines = _wrap_by_width(draw, title, title_font, max_text_width, max_lines=2)
        y = 165
        for line in lines:
            draw.text((margin, y), line, font=title_font, fill=TITLE_COLOR)
            y += 58

        # 시세 그리드(종목 기사) > 없으면 AI 3줄 요약 패널(경제 뉴스가 아닌 일반 기사) 순으로.
        # 제목과 그리드 사이 위쪽 여백이 아래쪽 여백보다 훨씬 넓어 보여, 박스 전체를 50px
        # 위로 끌어올린다(높이는 그대로 유지 — 아래쪽 경계도 같이 50px 위로).
        # AI 예상종가가 있으면 그리드가 3행이 되므로 아래쪽으로 더 늘려 잡는다(행당 5px씩
        # 여유를 더 줘 라벨/값 텍스트가 구분선에 겹치지 않게 함).
        grid_top = 295
        grid_bottom = 535 if market_data and market_data.get('pred_next_close') is not None else 460
        if market_data:
            _draw_market_grid(draw, margin, grid_top, CANVAS_SIZE[0] - margin, grid_bottom, market_data)
        elif summary_lines:
            _draw_summary_panel(draw, margin, grid_top, CANVAS_SIZE[0] - margin, 430, summary_lines)

    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_thumbnail_file(title, stock=None, matched_keyword=None, category_label=None,
                          ai_summary=None, is_economic_news=None):
    """news_ai_summarize_view/news_scrape_view/generate_featured_stock_briefing에서 AI 요약이
    만들어지는 시점에 호출. stock이 있으면 종목명/티커/최신 매매 시그널·실시간 시세로, 없으면
    매칭 키워드(또는 'AI 요약')로 카드를 그려 ImageField에 바로 할당 가능한 ContentFile을 반환한다.
    category_label을 넘기면 상단 태그를 기본값(종목 분석 리포트/AI 요약 리포트) 대신 그 값으로 쓴다
    (예: 특징주 브리핑 커맨드는 "특징주 브리핑"을 넘김).
    is_economic_news를 지정하지 않으면 stock 또는 matched_keyword가 있을 때만 경제/시황 기사로
    보고 코스피/코스닥 지수 요약을 그린다 — 종목·키워드 매칭 없이 회원이 임의 URL을 스크랩한
    기사(예: 사회 이슈 기사)까지 지수 카드가 붙는 걸 막기 위함. 특징주 브리핑처럼 종목/키워드가
    없어도 확실히 시황 콘텐츠인 경우엔 True로 강제한다. 경제 뉴스가 아니라 지수 요약을 그리지
    않는 경우, 그 자리엔 대신 ai_summary(3줄 요약)를 짧게 줄여 채운다."""
    from django.core.files.base import ContentFile

    from .models import MarketIndex, StockPrediction, StockRealtimePrice

    if is_economic_news is None:
        is_economic_news = bool(stock or matched_keyword)

    index_summary = None
    summary_lines = None
    ai_background_bytes = None
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
                'pred_next_close': latest_pred.pred_next_close if latest_pred else None,
            }
    else:
        subject_label = matched_keyword.keyword if matched_keyword else "AI 요약"
        ticker = None
        signal_color_key = None
        market_data = None
        category_label = category_label or "AI 요약 리포트"

        if is_economic_news:
            # 특정 종목이 없어 카드가 휑해 보이므로, 대신 오늘자 코스피/코스닥 지수 요약을 보여준다.
            today_indices = {
                row.market_type: row
                for row in MarketIndex.objects.filter(date=date.today(), market_type__in=['KOSPI', 'KOSDAQ'])
            }
            if today_indices:
                labels = {'KOSPI': '코스피', 'KOSDAQ': '코스닥'}
                index_summary = [
                    {
                        'label': labels[market_type],
                        'close': float(today_indices[market_type].close_price),
                        'change': float(today_indices[market_type].change or 0),
                        'change_pct': today_indices[market_type].change_pct or 0,
                        'volume': today_indices[market_type].volume,
                        'flows': {
                            'foreign': today_indices[market_type].foreign_net_qty,
                            'institution': today_indices[market_type].institution_net_qty,
                            'retail': today_indices[market_type].retail_net_qty,
                        },
                    }
                    for market_type in ('KOSPI', 'KOSDAQ') if market_type in today_indices
                ]

        if not index_summary:
            if not is_economic_news:
                # 경제 뉴스가 아닌 일반 기사는 코스피/코스닥 대신 gpt-image-2로 그린 일러스트를
                # 대표 이미지로 쓴다. 실패/미설정 시 None이 돌아와 아래 summary_lines 폴백으로
                # 자연스럽게 이어진다.
                from . import article_ai
                ai_background_bytes = article_ai.generate_thumbnail_image_bytes(title, ai_summary)
            if not ai_background_bytes:
                summary_lines = [l.strip() for l in (ai_summary or '').split('\n') if l.strip()][:3] or None

    image_bytes = generate_thumbnail_image(
        title, subject_label, ticker, signal_color_key,
        category_label=category_label,
        market_data=market_data,
        index_summary=index_summary,
        summary_lines=summary_lines,
        ai_background_bytes=ai_background_bytes,
    )
    return ContentFile(image_bytes, name="thumbnail.png")


if __name__ == "__main__":
    # 수동 테스트용: python articles/thumbnail.py 로 실행하면 /tmp에 샘플 카드를 그려본다.
    samples = [
        ("삼성전자, 목표주가 55만원→37만원…33% 하향한 이유는[클릭e종목]", "삼성전자", "005930", "SELL",
         "종목 분석 리포트",
         {'current_price': 71500, 'prev_close': 73200, 'change': -1700, 'change_pct': -2.32,
          'volume': 18234567, 'trading_value': 71500 * 18234567}, None),
        ("현대차, 3분기 영업이익 시장 예상치 크게 웃돌아…목표주가 줄상향", "현대차", "005380", "BUY",
         "종목 분석 리포트",
         {'current_price': 245500, 'prev_close': 236000, 'change': 9500, 'change_pct': 4.03,
          'volume': 3456789, 'trading_value': 245500 * 3456789}, None),
        ("코스피, 미 연준 금리 동결 소식에 강보합 마감", "코스피", None, None, "AI 요약 리포트", None, None),
        ("2026-07-31 장중 특징주 브리핑", "경제 뉴스", None, None, "특징주 브리핑", None, [
            {'label': '코스피', 'close': 3187.42, 'change': -12.5, 'change_pct': -0.39, 'volume': 412345678,
             'flows': {'foreign': -1523000, 'institution': -842000, 'retail': 2365000}},
            {'label': '코스닥', 'close': 812.7, 'change': 6.2, 'change_pct': 0.77, 'volume': 987654321,
             'flows': {'foreign': 341000, 'institution': -120000, 'retail': -221000}},
        ]),
    ]
    for i, (title, subject, ticker, signal, category, market_data, index_summary) in enumerate(samples):
        data = generate_thumbnail_image(title, subject, ticker, signal, category, market_data, index_summary)
        path = f"/tmp/thumb_sample_{i}.png"
        with open(path, "wb") as f:
            f.write(data)
        print("wrote", path)
