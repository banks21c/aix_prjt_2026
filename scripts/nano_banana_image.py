"""나노바나나(Gemini 2.5 Flash Image)로 참고 이미지를 바탕삼아 새 이미지를 생성하는 1회성
스크립트. articles/article_ai.py의 gpt-image-2 썸네일 생성 파이프라인과는 별개의 실험용이며
크론/커맨드 어디에도 연결되어 있지 않다. .env의 GEMINI_API_KEY(config/settings.py와 동일한 키)를
그대로 사용한다.

사용법:
  venv/bin/python scripts/nano_banana_image.py <참고_이미지_URL_또는_경로> "<프롬프트>" [출력파일]

예시:
  venv/bin/python scripts/nano_banana_image.py \\
    "https://www.chosun.com/resizer/v2/JXV4HAQYS5FA3BMNLISQETBAT4.jpg?auth=...&width=1280" \\
    "이 상차림 사진과 같은 구도·조명으로, 같은 반찬 종류를 유지한 채 새로 그려줘" \\
    output.png

모델은 원조 나노바나나인 gemini-2.5-flash-image가 기본값이다. 이후 세대(예:
gemini-3-pro-image, gemini-3.1-flash-image)를 쓰려면 NANO_BANANA_MODEL 환경변수로 덮어쓴다.
"""
import os
import sys
from io import BytesIO

import requests
from dotenv import load_dotenv
from google import genai
from PIL import Image

load_dotenv()

MODEL = os.environ.get("NANO_BANANA_MODEL", "gemini-2.5-flash-image")


def load_reference_image(source):
    if source.startswith("http://") or source.startswith("https://"):
        resp = requests.get(source, timeout=30)
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content))
    return Image.open(source)


def main():
    if len(sys.argv) < 3:
        print(f'usage: {sys.argv[0]} <참고_이미지_URL_또는_경로> "<프롬프트>" [출력파일=output.png]')
        sys.exit(1)

    source, prompt = sys.argv[1], sys.argv[2]
    out_path = sys.argv[3] if len(sys.argv) > 3 else "output.png"

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key == "YOUR_GEMINI_API_KEY_HERE":
        print("GEMINI_API_KEY가 설정되어 있지 않습니다 (.env 확인)")
        sys.exit(1)

    reference_image = load_reference_image(source)
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=MODEL,
        contents=[prompt, reference_image],
    )

    saved = False
    for part in response.candidates[0].content.parts:
        if part.text:
            print(part.text)
        elif part.inline_data:
            Image.open(BytesIO(part.inline_data.data)).save(out_path)
            saved = True
            print(f"저장 완료: {out_path}")

    if not saved:
        print("이미지가 생성되지 않았습니다 (모델 응답에 inline_data 없음)")
        sys.exit(1)


if __name__ == "__main__":
    main()
