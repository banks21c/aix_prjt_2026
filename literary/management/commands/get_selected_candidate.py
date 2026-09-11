"""현재 '다음 작품'으로 선택된 LiteraryCandidate를 author|work 형태로 한 줄 출력한다.
선택된 게 없으면 아무것도 출력하지 않는다(exit code 0). produce-episode 파이프라인이
자동 선정 전에 SSH로 이 커맨드를 실행해 사용자가 nextfinup 관리자 화면에서 미리
지정해둔 작가·작품이 있는지 확인하는 용도."""
from django.core.management.base import BaseCommand

from literary.models import LiteraryCandidate


class Command(BaseCommand):
    help = "현재 선택된(is_selected=True) 작가·작품 후보를 author|work 형태로 출력한다."

    def handle(self, *args, **options):
        candidate = LiteraryCandidate.objects.filter(is_selected=True).first()
        if candidate:
            self.stdout.write(f"{candidate.author}|{candidate.work}")
