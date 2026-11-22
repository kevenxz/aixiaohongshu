from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1080, 1440
PAPER = (241, 237, 229)
INK = (18, 19, 16)
MUTED = (92, 93, 86)
LIME = (201, 249, 103)
CORAL = (255, 120, 94)
BLUE = (113, 139, 255)
WHITE = (255, 253, 249)

PLATFORMS = {
    "reddit": ("REDDIT", CORAL),
    "youtube": ("YOUTUBE", LIME),
    "x": ("X / TWITTER", BLUE),
    "web": ("OFFICIAL", LIME),
    "weibo": ("WEIBO", CORAL),
    "instagram": ("INSTAGRAM", BLUE),
}

FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)
MONO_CANDIDATES = (
    "C:/Windows/Fonts/consola.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)

CJK = re.compile(r"[\u3400-\u9fff]")
PUNCTUATION = set("，。！？；：、）》」』】…,.!?;:)]}")
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _load_font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _clean(text: str) -> str:
    text = EMOJI.sub("", text or "").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\u3400-\u9fff]|[A-Za-z0-9][A-Za-z0-9'’._+#-]*|[^\s]", text)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for token in _tokens(text):
        is_cjk = bool(CJK.match(token))
        separator = ""
        if current and not is_cjk and not CJK.search(current[-1]) and current[-1] not in "，。！？；：、（":
            separator = " "
        candidate = f"{current}{separator}{token}"
        if current and draw.textlength(candidate, font=font) > max_width:
            if token in PUNCTUATION:
                current = candidate
                continue
            lines.append(current)
            current = token
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _fit_title(draw: ImageDraw.ImageDraw, title: str) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    for size in range(104, 54, -6):
        font = _load_font(FONT_CANDIDATES, size)
        lines = _wrap(draw, title, font, WIDTH - 160)
        if len(lines) <= 5 and all(draw.textlength(line, font=font) <= WIDTH - 160 for line in lines):
            return font, lines
    font = _load_font(FONT_CANDIDATES, 54)
    return font, _wrap(draw, title, font, WIDTH - 160)[:6]


def generate_cover(post: dict[str, Any], media_dir: Path, account: Any = None) -> Path:
    media_dir.mkdir(parents=True, exist_ok=True)
    is_gallery = bool(getattr(account, "is_gallery", False))
    fallback_title = "明星图集" if is_gallery else "AI 信号"
    title = _clean(str(post.get("title") or fallback_title))
    body = _clean(str(post.get("body") or ""))
    platform = str(post.get("platform") or "web")
    platform_label, accent = PLATFORMS.get(platform, ("SIGNAL", CORAL))
    author = _clean(str(post.get("author") or ""))
    brand = _clean(str(getattr(account, "name", "") or "STAR GALLERY")) if is_gallery else "SIGNAL / ATELIER"
    subtitle = "STAR GALLERY" if is_gallery else "DAILY AI CURATION"

    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((44, 44, WIDTH - 44, HEIGHT - 44), outline=INK, width=2)

    mono = _load_font(MONO_CANDIDATES, 24)
    mono_small = _load_font(MONO_CANDIDATES, 19)
    small_cjk = _load_font(FONT_CANDIDATES, 21)
    draw.text((82, 80), brand, font=mono, fill=INK)
    draw.text((82, 114), subtitle, font=mono_small, fill=MUTED)

    badge_font = _load_font(MONO_CANDIDATES, 20)
    badge_w = draw.textlength(platform_label, font=badge_font) + 34
    draw.rounded_rectangle((WIDTH - 82 - badge_w, 76, WIDTH - 82, 124), radius=24, fill=accent, outline=INK, width=2)
    draw.text((WIDTH - 82 - badge_w + 17, 88), platform_label, font=badge_font, fill=INK)

    draw.line((82, 196, WIDTH - 82, 196), fill=INK, width=2)
    draw.rectangle((82, 188, 126, 204), fill=CORAL)
    draw.text((82, 230), f"{'STAR GALLERY' if is_gallery else 'AI SIGNAL'}  /  中文整理", font=small_cjk, fill=CORAL)

    title_font, lines = _fit_title(draw, title)
    y = 292
    for line in lines:
        draw.text((82, y), line, font=title_font, fill=INK)
        y += int(title_font.size * 1.3)

    y += 18
    meta = f"SOURCE · {platform_label}" + (f"  /  {author}" if author else "")
    draw.text((82, y), _clean(meta)[:90], font=small_cjk, fill=MUTED)
    y += 42
    draw.line((82, y, 520, y), fill=INK, width=2)

    snippet_lines: list[str] = []
    if body:
        body_font = _load_font(FONT_CANDIDATES, 28)
        snippet_lines = _wrap(draw, body, body_font, 580)[:3]
        snippet_y = y + 30
        for line in snippet_lines:
            draw.text((82, snippet_y), line, font=body_font, fill=MUTED)
            snippet_y += 44

    # Editorial orbit motif, drawn without source imagery.
    draw.ellipse((720, 960, 1000, 1240), outline=INK, width=2)
    draw.ellipse((720, 1080, 920, 1240), outline=CORAL, width=2)
    draw.ellipse((820, 1000, 960, 1140), fill=LIME, outline=INK, width=2)
    draw.ellipse((720, 960, 744, 984), fill=CORAL, outline=INK, width=2)
    draw.line((650, 992, 1000, 992), fill=BLUE, width=2)

    tags = [str(tag).strip().lstrip("#") for tag in (post.get("tags") or []) if str(tag).strip()]
    tag_font = _load_font(FONT_CANDIDATES, 23)
    tag_x, tag_y = 82, HEIGHT - 230
    for tag in tags[:3]:
        label = f"#{tag}"
        tag_w = draw.textlength(label, font=tag_font) + 26
        if tag_x + tag_w > 650:
            break
        draw.rounded_rectangle((tag_x, tag_y, tag_x + tag_w, tag_y + 42), radius=21, fill=WHITE, outline=INK, width=1)
        draw.text((tag_x + 13, tag_y + 8), label, font=tag_font, fill=INK)
        tag_x += tag_w + 10

    draw.line((82, HEIGHT - 160, WIDTH - 82, HEIGHT - 160), fill=INK, width=2)
    draw.text((82, HEIGHT - 132), f"{brand} / CONTENT STUDIO", font=small_cjk, fill=MUTED)
    page = f"NO. {int(post.get('id') or 0):03d}"
    draw.text((WIDTH - 82 - draw.textlength(page, font=mono_small), HEIGHT - 132), page, font=mono_small, fill=MUTED)

    path = media_dir / f"cover-{post.get('id') or abs(hash(title)) % 100000}.png"
    image.save(path, "PNG", optimize=True)
    return path
