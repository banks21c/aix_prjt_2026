"""건강/의학·음식/영양·여행/관광 자동 발행용 1년 주제 캘린더 — DB(ContentCalendarTheme/
ContentCalendarTopic) 기반. generate_health_briefing/generate_food_briefing/
generate_travel_briefing이 매일 이 모듈을 통해 오늘의 주제를 읽어간다.

예전에는 카테고리별 파이썬 모듈(health_calendar.py/food_calendar.py)에 CATEGORIES/TOPICS
딕셔너리가 하드코딩돼 있었지만, 관리자가 /admin/에서 주제 문구를 직접 수정할 수 있도록 DB로
옮겼다(articles/management/commands/seed_content_calendar.py가 최초 데이터를 채운다).
계산 방식(364일 주기, 요일 고정 테마, 52주 주제 순환) 자체는 그대로다.

요일마다 테마를 고정하고, 테마마다 52개 주제를 준비해 그 요일이 52번(1년) 도는 동안 하나씩만
쓴다. 52주(364일)를 넘어가면 START부터 다시 같은 순서로 돌아간다(뉴스처럼 시의성이 없어
재활용해도 무방하다는 전제) — 그래서 이 캘린더는 별도 갱신 없이 계속 돌아간다. 오전/오후는
같은 주제를 서로 다른 두 각도로 나눠 하루 두 편이 자연스럽게 이어지도록 한다.
"""
import datetime

START_DATES = {
    'HEALTH': datetime.date(2026, 8, 19),
    'FOOD': datetime.date(2026, 8, 19),
    'TRAVEL': datetime.date(2026, 8, 19),
}
CYCLE_DAYS = 52 * 7  # 364일 주기

SESSION_LABELS = {'am': '오전', 'pm': '오후'}
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def get_topic_for_date(category, d, session):
    """주어진 카테고리·날짜(d)·세션('am'|'pm')에 해당하는 (테마명, 주제, 제목, 각도라벨)을
    계산한다. START 이전 날짜가 들어오면 START 기준으로 취급한다(첫 실행일 방어)."""
    from articles.models import ContentCalendarTheme, ContentCalendarTopic

    if session not in SESSION_LABELS:
        raise ValueError(f"unknown session: {session!r}")

    start = START_DATES[category]
    days_since_start = max((d - start).days, 0)
    cycle_day = days_since_start % CYCLE_DAYS
    cycle_date = start + datetime.timedelta(days=cycle_day)
    weekday = cycle_date.weekday()
    occurrence = cycle_day // 7  # 그 요일이 몇 번째로 돌아왔는지(0-indexed) = 주제 인덱스

    theme = ContentCalendarTheme.objects.get(category=category, weekday=weekday)
    topic_row = ContentCalendarTopic.objects.get(category=category, weekday=weekday, week_number=occurrence)
    topic = topic_row.topic
    if session == 'am':
        title = theme.am_title_template.format(t=topic)
        angle = theme.am_angle
    else:
        title = theme.pm_title_template.format(t=topic)
        angle = theme.pm_angle
    return dict(category_name=theme.name, topic=topic, title=title, angle=angle)


def build_one_cycle(category):
    """해당 카테고리의 52주(364일) 전체를 (날짜, 요일, 테마명, 오전 제목, 오후 제목) 리스트로
    펼친다. generate_*_briefing/관리자 화면 등 내부용으로만 쓴다 — 공개 페이지는 전체 스케줄이
    그대로 퍼가기 당하지 않도록 build_sample()의 요약본만 보여준다."""
    from articles.models import ContentCalendarTheme, ContentCalendarTopic

    start = START_DATES[category]
    themes = {t.weekday: t for t in ContentCalendarTheme.objects.filter(category=category)}
    topics = {(t.weekday, t.week_number): t.topic for t in ContentCalendarTopic.objects.filter(category=category)}

    rows = []
    for i in range(CYCLE_DAYS):
        d = start + datetime.timedelta(days=i)
        weekday = d.weekday()
        occurrence = i // 7
        theme = themes[weekday]
        topic = topics[(weekday, occurrence)]
        rows.append(dict(
            date=d, weekday=WEEKDAY_KR[weekday], category_name=theme.name,
            am_title=theme.am_title_template.format(t=topic),
            pm_title=theme.pm_title_template.format(t=topic),
        ))
    return rows


def build_sample(category):
    """요일(테마)별로 예시 1개씩(그 요일의 첫 번째 주제)만 뽑아 7건을 반환한다. 실제 364일
    전체 스케줄을 공개하면 통째로 퍼가서 그대로 베낄 수 있어(회원이 실제로 우려한 지점), 공개
    페이지는 "이런 식으로 발행됩니다"를 보여줄 예시만 노출하고 나머지 51/52는 비공개로 둔다."""
    start = START_DATES[category]
    rows = []
    for wd in range(7):
        offset = (wd - start.weekday()) % 7  # START 이후 그 요일이 처음 오는 날짜까지의 거리
        d = start + datetime.timedelta(days=offset)
        am = get_topic_for_date(category, d, 'am')
        pm = get_topic_for_date(category, d, 'pm')
        rows.append(dict(
            weekday=WEEKDAY_KR[wd], category_name=am['category_name'],
            am_title=am['title'], pm_title=pm['title'],
        ))
    return rows
