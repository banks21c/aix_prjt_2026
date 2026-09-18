import json
import re
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import Author, Work


def combo_authors(response):
    """작가 콤보(입력칸 아래 목록)에 들어간 작가 이름 — 화면이 JS에 넘기는 author-options JSON."""
    return json.loads(re.search(r'<script id="author-options"[^>]*>(.*?)</script>',
                                response.content.decode(), re.S).group(1))


# articles/tests.py와 같은 이유 — 테스트 러너가 DEBUG=False로 돌려 SECURE_SSL_REDIRECT가 켜지면 모든 요청이 301이 된다.
@override_settings(SECURE_SSL_REDIRECT=False)
class WorkSummaryAndDetailTests(TestCase):
    databases = {'default', 'autovi'}  # 로그인 계정은 default, 작가·작품은 autovi_db(config/db_routers.py)
    def setUp(self):
        # 테스트 DB에도 0002~0006 데이터 마이그레이션이 넣은 작가·작품이 있다 — 빈 상태에서 시작한다.
        Work.objects.all().delete()
        Author.objects.all().delete()
        user = get_user_model().objects.create_superuser('boss', password='pw')
        self.client.force_login(user)
        self.author = Author.objects.create(name="기 드 모파상", summary="프랑스의 소설가.")
        self.work = Work.objects.create(author=self.author, title="벨아미", summary="야심 찬 청년의 출세기.",
                                        kind="장편소설")
        Work.objects.create(author=self.author, title="여자의 일생")

    def get(self, url):
        return self.client.get(url, HTTP_HOST='localhost')

    def test_list_rows_show_summary_on_hover_and_link_to_detail(self):
        for url in ('/admin-tools/literary-picker/',):
            resp = self.get(url)
            self.assertContains(resp, 'title="야심 찬 청년의 출세기."')
            self.assertContains(resp, 'title="프랑스의 소설가."')
            self.assertContains(resp, f'data-href="/admin-tools/works/{self.work.pk}/?next=')
            self.assertContains(resp, '<td class="kind">장편소설</td>')
            self.assertContains(resp, '<td class="kind">—</td>')

    def test_detail_page_and_select(self):
        resp = self.get(f'/admin-tools/works/{self.work.pk}/?next=/admin-tools/literary-picker/%3Fpage%3D3')
        self.assertContains(resp, "『벨아미』")
        self.assertContains(resp, "야심 찬 청년의 출세기.")
        self.assertContains(resp, "프랑스의 소설가.")
        self.assertContains(resp, "『여자의 일생』")
        self.assertContains(resp, "<dt>분류</dt><dd>장편소설 · 국외</dd>")
        self.assertContains(resp, 'href="/admin-tools/literary-picker/?page=3"')

        resp = self.client.post(f'/admin-tools/works/{self.work.pk}/', {'next': '/admin-tools/literary-picker/?page=3'},
                                HTTP_HOST='localhost')
        self.assertRedirects(resp, '/admin-tools/literary-picker/?page=3', fetch_redirect_response=False)
        self.work.refresh_from_db()
        self.assertTrue(self.work.is_selected)

    def test_detail_ignores_offsite_next(self):
        resp = self.get(f'/admin-tools/works/{self.work.pk}/?next=https://evil.example/')
        self.assertContains(resp, 'href="/admin-tools/literary-picker/"')

    def test_load_summaries_fills_blanks_only(self):
        data = {"authors": {"기 드 모파상": "새 소개"}, "works": {"기 드 모파상|여자의 일생": "한 여인의 일생.",
                                                              "기 드 모파상|벨아미": "덮어쓰면 안 됨", "없는|작품": "x"}}
        path = Path(tempfile.mkdtemp()) / "s.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        out = StringIO()
        call_command('load_summaries', str(path), stdout=out)
        self.author.refresh_from_db()
        self.work.refresh_from_db()
        self.assertEqual(self.author.summary, "프랑스의 소설가.")
        self.assertEqual(self.work.summary, "야심 찬 청년의 출세기.")
        self.assertEqual(Work.objects.get(title="여자의 일생").summary, "한 여인의 일생.")
        self.assertIn("없는|작품", out.getvalue())
        call_command('load_summaries', str(path), '--overwrite', stdout=StringIO())
        self.work.refresh_from_db()
        self.assertEqual(self.work.summary, "덮어쓰면 안 됨")

    def test_load_summaries_fills_kinds(self):
        data = {"kinds": {"기 드 모파상|여자의 일생": "장편소설", "기 드 모파상|벨아미": "희곡",
                          "기 드 모파상|없는 작품": "단편소설"}}
        Work.objects.create(author=self.author, title="목걸이")
        data["kinds"]["기 드 모파상|목걸이"] = "짧은 이야기"
        path = Path(tempfile.mkdtemp()) / "s.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        out = StringIO()
        call_command('load_summaries', str(path), stdout=out)
        self.assertEqual(Work.objects.get(title="여자의 일생").kind, "장편소설")
        self.assertEqual(Work.objects.get(title="벨아미").kind, "장편소설")  # 이미 있는 값은 그대로
        self.assertEqual(Work.objects.get(title="목걸이").kind, "")          # 선택지에 없는 값은 건너뜀
        self.assertIn("알 수 없는 값 짧은 이야기", out.getvalue())
        self.assertIn("없는 작품", out.getvalue())

    def test_origin_filter(self):
        korean = Author.objects.create(name="김유정")
        Work.objects.create(author=korean, title="봄봄", is_domestic=True)
        for url in ('/admin-tools/literary-picker/',):
            resp = self.get(url + '?origin=domestic')
            self.assertContains(resp, "『봄봄』")
            self.assertNotContains(resp, "『벨아미』")
            self.assertContains(resp, 'id="filter-origin-domestic" value="domestic" checked')
            self.assertNotIn("기 드 모파상", combo_authors(resp))          # 작가 콤보도 국내 작가만
            resp = self.get(url + '?origin=foreign')
            self.assertContains(resp, "『벨아미』")
            self.assertNotContains(resp, "『봄봄』")
        # 국외 작가를 고른 채 국내로 바꾸면 작가 조건은 풀린다.
        resp = self.get('/admin-tools/literary-picker/?author=기 드 모파상&origin=domestic')
        self.assertContains(resp, "『봄봄』")

    def test_kind_filter(self):
        Work.objects.create(author=self.author, title="목걸이", kind="단편소설")
        for url in ('/admin-tools/literary-picker/',):
            resp = self.get(url + '?kind=단편소설')
            self.assertContains(resp, "『목걸이』")
            self.assertNotContains(resp, "『벨아미』")
            self.assertContains(resp, '<optgroup label="소설">')
            self.assertContains(resp, '<option value="단편소설" selected>')
        # 선택지에 없는 값은 조건 없이 전체
        resp = self.get('/admin-tools/literary-picker/?kind=없는분류')
        self.assertContains(resp, "『벨아미』")
        self.assertContains(resp, "『목걸이』")

    def test_nationality_in_list_and_detail(self):
        for url in ('/admin-tools/literary-picker/',):
            self.assertContains(self.get(url), '<td class="nationality">—</td>')
        self.author.nationality = "프랑스"
        self.author.save()
        for url in ('/admin-tools/literary-picker/',):
            self.assertContains(self.get(url), '<td class="nationality">프랑스</td>')
        self.assertContains(self.get(f'/admin-tools/works/{self.work.pk}/'), '· 프랑스</span>')

    def test_load_summaries_fills_nationalities(self):
        Author.objects.create(name="레프 톨스토이", nationality="러시아")
        path = Path(tempfile.mkdtemp()) / "s.json"
        path.write_text(json.dumps({"nationalities": {"기 드 모파상": "프랑스", "레프 톨스토이": "덮어쓰면 안 됨",
                                                      "없는 작가": "x"}}, ensure_ascii=False), encoding="utf-8")
        out = StringIO()
        call_command('load_summaries', str(path), stdout=out)
        self.author.refresh_from_db()
        self.assertEqual(self.author.nationality, "프랑스")
        self.assertEqual(Author.objects.get(name="레프 톨스토이").nationality, "러시아")
        self.assertIn("국적 없는 작가", out.getvalue())

    def test_load_summaries_marks_domestic(self):
        korean = Author.objects.create(name="김유정")
        Work.objects.create(author=korean, title="봄봄")
        path = Path(tempfile.mkdtemp()) / "s.json"
        path.write_text(json.dumps({"domestic": ["김유정|봄봄", "없는|작품"]}, ensure_ascii=False), encoding="utf-8")
        out = StringIO()
        call_command('load_summaries', str(path), stdout=out)
        self.assertTrue(Work.objects.get(title="봄봄").is_domestic)
        self.assertFalse(Work.objects.get(title="벨아미").is_domestic)
        self.assertIn("국내 없는|작품", out.getvalue())

    def test_picker_select_returns_trigger_and_keeps_one_selected(self):
        other = Work.objects.get(title="여자의 일생")
        other.select()
        resp = self.client.post('/admin-tools/literary-picker/', {'candidate_id': self.work.pk},
                                HTTP_HOST='localhost', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.json(), {'ok': True, 'trigger_text': "/produce-episode 기 드 모파상의 『벨아미』로 진행해줘"})
        self.assertEqual(list(Work.objects.filter(is_selected=True)), [self.work])
        self.assertContains(self.get('/admin-tools/literary-picker/'), "기 드 모파상의 『벨아미』로 진행해줘")

    def test_get_selected_candidate(self):
        out = StringIO()
        call_command('get_selected_candidate', stdout=out)
        self.assertEqual(out.getvalue(), "")
        self.work.select()
        out = StringIO()
        call_command('get_selected_candidate', stdout=out)
        self.assertEqual(out.getvalue().strip(), "기 드 모파상|벨아미")

    def test_literary_lives_in_autovi_db(self):
        # config/db_routers.py — 작가·작품은 autovi_db, 로그인 계정 등은 nextfinup_db
        self.assertEqual(Work.objects.db, 'autovi')
        self.assertEqual(Author.objects.db, 'autovi')
        self.assertEqual(get_user_model().objects.db, 'default')

    def test_author_filter_accepts_typed_text(self):
        Work.objects.create(author=Author.objects.create(name="에밀 졸라"), title="목로주점")
        # 정확한 이름이면 그 작가만, 작품 콤보도 켜진다
        resp = self.get('/admin-tools/literary-picker/?author=에밀 졸라')
        self.assertContains(resp, "『목로주점』")
        self.assertNotContains(resp, "『벨아미』")
        self.assertContains(resp, 'id="filter-author" value="에밀 졸라"')
        self.assertNotContains(resp, 'id="filter-work" class="form-select form-select-sm" disabled')
        # 일부만 입력하면 그 글자가 든 작가 전부, 작품 조건은 쓰지 않는다
        resp = self.get('/admin-tools/literary-picker/?author=모파&work=목로주점')
        self.assertContains(resp, "『벨아미』")
        self.assertContains(resp, "『여자의 일생』")
        self.assertNotContains(resp, "『목로주점』")
        self.assertContains(resp, 'id="filter-work" class="form-select form-select-sm" disabled')
        # 아무 작가에도 없는 글자면 결과 없음
        self.assertContains(self.get('/admin-tools/literary-picker/?author=없는작가'), "0건")
