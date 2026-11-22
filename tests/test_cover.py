from __future__ import annotations

from PIL import Image

from xhs_workflow.cover import generate_cover


def test_generate_cover_for_text_post(tmp_path):
    post = {
        "id": 8,
        "platform": "reddit",
        "title": "GPT性别偏见实验，结果很反直觉",
        "body": "一项关于 GPT 性别偏见的实验，结论引发讨论。",
        "author": "/u/tester",
        "tags": ["AI偏见", "GPT", "性别议题"],
    }
    path = generate_cover(post, tmp_path)
    assert path.exists()
    assert path.suffix == ".png"
    with Image.open(path) as image:
        assert image.size == (1080, 1440)
        assert image.format == "PNG"


def test_generate_cover_handles_long_title(tmp_path):
    post = {
        "id": 9,
        "platform": "web",
        "title": "这是一个非常长的标题" * 8,
        "body": "用于验证长标题封面不会崩溃。",
        "author": "OpenAI",
        "tags": [],
    }
    path = generate_cover(post, tmp_path)
    with Image.open(path) as image:
        assert image.size == (1080, 1440)
