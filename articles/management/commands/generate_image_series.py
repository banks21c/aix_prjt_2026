"""여러 장의 프롬프트를 한 번에, 서로 일관된 그림체/인물로 이어서 생성하는 커맨드.

image_generator_view(관리자 화면)는 폼 제출 1회당 1장만 만들고, 그마저도 텍스트 프롬프트만
모델에 넘긴다 — 같은 인물 묘사를 프롬프트에 똑같이 써 넣어도 매 호출이 독립이라 얼굴·의상·
조명이 장마다 달라진다. 이 커맨드는 그 대신 "앞 장을 레퍼런스 이미지로 물려서 다음 장을
생성"하는 방식으로 시리즈를 뽑는다(나노바나나 계열이 지원하는 멀티모달 입력 사용).

관리자 화면의 "시리즈 생성" 탭도 이 커맨드를 백그라운드로 띄워 쓴다(image_series_start_view).
요청 안에서 직접 돌리지 않는 이유는 5장 2K가 약 94초 걸리는데 앞단 타임아웃이 Cloudflare
100초(무료 플랜이라 조정 불가) / gunicorn 120초 / nginx 130초로 걸려 있어서다. 화면은
--series-key로 넘긴 키를 물고 진행 상황 사이드카를 폴링한다.

사용법:
    python manage.py generate_image_series --file prompts.txt
    python manage.py generate_image_series --file prompts.txt --image-size 2K --dry-run

프롬프트 파일은 `#1`, `#2` … 로 시작하는 줄에서 장이 나뉜다(번호 표시는 프롬프트에서 제외).
빈 줄은 무시하지 않고 같은 장 안의 줄바꿈으로 유지한다.
"""
import os
import time
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from google import genai
from google.genai import types as genai_types

from articles.image_series import (
    MAX_SCENES,
    REF_MODES,
    REFERENCE_INSTRUCTION,
    reference_indexes,
    split_prompts,
    write_progress,
)
from articles.models import GeneratedImage
from articles.views.admin_tools import (
    IMAGE_GEN_SUBDIR,
    KST,
    NANO_BANANA_IMAGE_SIZES,
    NANO_BANANA_MODELS,
    _compute_gemini_cost_usd,
    _image_dimensions_label,
)

class Command(BaseCommand):
    help = "프롬프트 파일의 여러 장을 앞 장을 레퍼런스로 물려가며 일관되게 연속 생성합니다."

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, help="'#1 ...' 형식의 프롬프트 파일 경로")
        parser.add_argument('--model', default='gemini-3-pro-image',
                            help="나노바나나 계열 모델명 (기본: gemini-3-pro-image)")
        parser.add_argument('--aspect', default='9:16', help="비율 (기본: 9:16)")
        parser.add_argument('--image-size', default='2K', help="생성 크기 1K/2K/4K (기본: 2K)")
        parser.add_argument('--ref-mode', default='first+prev', choices=REF_MODES,
                            help="레퍼런스로 넘길 앞 장을 고르는 방식 (기본: first+prev)")
        parser.add_argument('--sleep', type=float, default=2.0,
                            help="장 사이 대기 초 — 레이트리밋 회피용 (기본: 2.0)")
        parser.add_argument('--series-key', default='',
                            help="생성된 행을 묶을 시리즈 키 — 화면에서 백그라운드로 띄울 때 넘긴다")
        parser.add_argument('--dry-run', action='store_true',
                            help="API를 호출하지 않고 장 분리 결과와 레퍼런스 구성만 출력")

    def handle(self, *args, **options):
        model = options['model']
        series_key = options['series_key']
        if model not in NANO_BANANA_MODELS:
            raise CommandError(
                f"레퍼런스 이미지 입력은 나노바나나 계열에서만 지원합니다. "
                f"가능한 값: {', '.join(NANO_BANANA_MODELS)}"
            )

        path = Path(options['file'])
        if not path.exists():
            raise CommandError(f"프롬프트 파일이 없습니다: {path}")
        prompts = split_prompts(path.read_text(encoding='utf-8'))
        if not prompts:
            raise CommandError("프롬프트 파일에서 '#1' 형식의 장 구분자를 찾지 못했습니다.")
        if len(prompts) > MAX_SCENES:
            raise CommandError(f"한 번에 생성할 수 있는 장 수는 최대 {MAX_SCENES}장입니다 (읽은 장: {len(prompts)}).")
        self.stdout.write(f"프롬프트 {len(prompts)}장을 읽었습니다.")

        # 지원하지 않는 생성 크기는 그 모델이 받는 가장 큰 값으로 낮춘다 — 화면 쪽
        # _generate_with_nano_banana와 같은 규칙.
        supported = NANO_BANANA_IMAGE_SIZES.get(model) or ['1K']
        image_size = options['image_size']
        if image_size not in supported:
            self.stdout.write(self.style.WARNING(
                f"{model}은(는) {image_size}를 지원하지 않아 {supported[-1]}로 생성합니다."))
            image_size = supported[-1]

        write_progress(
            series_key, total=len(prompts), done=0, failed=[], finished=False,
            error=None, pid=os.getpid(), model=model, image_size=image_size,
            aspect=options['aspect'],
        )

        if options['dry_run']:
            for idx, prompt in enumerate(prompts):
                refs = reference_indexes(idx, options['ref_mode'])
                label = ', '.join(f"#{r + 1}" for r in refs) or '(없음 — 첫 장)'
                self.stdout.write(f"\n#{idx + 1} 레퍼런스: {label}")
                self.stdout.write(f"  {prompt[:120]}…")
            self.stdout.write(self.style.SUCCESS(
                f"\n[dry-run] {model} / {options['aspect']} / {image_size} — 호출하지 않았습니다."))
            return

        if not settings.GEMINI_API_KEY or settings.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
            msg = "GEMINI_API_KEY가 설정되어 있지 않습니다 (.env 확인)."
            write_progress(series_key, finished=True, error=msg)
            raise CommandError(msg)

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        config = genai_types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=genai_types.ImageConfig(
                aspect_ratio=options['aspect'], image_size=image_size),
        )

        images_dir = Path(settings.MEDIA_ROOT) / IMAGE_GEN_SUBDIR
        images_dir.mkdir(parents=True, exist_ok=True)

        produced = []   # 장별 PNG 바이트 — 다음 장의 레퍼런스로 쓰인다
        failed = []
        total_cost = 0
        for idx, prompt in enumerate(prompts):
            ref_indexes = reference_indexes(idx, options['ref_mode'])
            # 앞 장이 실패해 비어 있을 수 있으므로 실제로 만들어진 것만 추린다.
            ref_bytes = [produced[r] for r in ref_indexes if r < len(produced) and produced[r]]

            contents = []
            if ref_bytes:
                contents.append(REFERENCE_INSTRUCTION + prompt)
                contents.extend(
                    genai_types.Part.from_bytes(data=b, mime_type='image/png') for b in ref_bytes
                )
            else:
                contents.append(prompt)

            ref_label = ', '.join(f"#{r + 1}" for r in ref_indexes) or '없음'
            self.stdout.write(f"\n#{idx + 1}/{len(prompts)} 생성 중 (레퍼런스: {ref_label}) …")

            try:
                response = client.models.generate_content(
                    model=model, contents=contents, config=config)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"  #{idx + 1} 호출 실패: {e}"))
                produced.append(None)
                failed.append(idx + 1)
                write_progress(series_key, failed=failed, last_error=str(e)[:300])
                continue

            image_bytes = self._extract_image(response)
            if image_bytes is None:
                self.stderr.write(self.style.ERROR(
                    f"  #{idx + 1} 이미지 없음 (안전 필터에 걸렸을 수 있습니다) — 건너뜁니다."))
                produced.append(None)
                failed.append(idx + 1)
                write_progress(
                    series_key, failed=failed,
                    last_error=f"#{idx + 1}: 모델이 이미지를 반환하지 않음 (안전 필터 가능성)")
                continue

            cost_usd, input_tokens, output_tokens = _compute_gemini_cost_usd(
                model, response.usage_metadata)
            source_size = _image_dimensions_label(image_bytes)

            filename = (f"{timezone.now().astimezone(KST):%Y%m%d_%H%M%S}"
                        f"_s{idx + 1:02d}_{uuid4().hex[:8]}.png")
            (images_dir / filename).write_bytes(image_bytes)
            relative_path = f"{IMAGE_GEN_SUBDIR}/{filename}"

            GeneratedImage.objects.create(
                model_name=model,
                size=f"{source_size} ({options['aspect']})({image_size})",
                source_size=source_size,
                quality='-',            # 나노바나나엔 품질 옵션이 없다
                prompt=prompt,          # 레퍼런스 지시문은 빼고 원본 프롬프트만 남긴다
                file_path=relative_path,
                cost_usd=cost_usd,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                series_key=series_key,
                series_index=idx + 1,
            )

            produced.append(image_bytes)
            if cost_usd:
                total_cost += cost_usd
            write_progress(series_key, done=sum(1 for b in produced if b), failed=failed)
            cost_msg = f"약 ${cost_usd:.4f}" if cost_usd is not None else "비용 미상"
            self.stdout.write(self.style.SUCCESS(
                f"  저장: {relative_path}  ({source_size}, {cost_msg})"))

            if options['sleep'] and idx < len(prompts) - 1:
                time.sleep(options['sleep'])

        ok = sum(1 for b in produced if b)
        write_progress(series_key, done=ok, failed=failed, finished=True,
                       total_cost=round(float(total_cost), 6))
        self.stdout.write(self.style.SUCCESS(
            f"\n완료: {ok}/{len(prompts)}장 생성, 합계 약 ${total_cost:.4f}. "
            f"목록: /admin-tools/generated-images/"))

    def _extract_image(self, response):
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content else None) or []:
                if part.inline_data and part.inline_data.data:
                    return part.inline_data.data
        return None
