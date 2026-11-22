from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from PIL import Image

from .accounts import AccountConfig, MediaRules
from .config import Settings
from .models import SourceItem

log = logging.getLogger(__name__)

PLATFORM_HEADERS = {
    "weibo": {"Referer": "https://weibo.com/"},
}
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def download_media_list(item: SourceItem, account: AccountConfig,
                        settings: Settings) -> list[str]:
    urls = item.image_urls[: max(1, account.media.max_images)]
    if not urls:
        return []
    if not _rights_allowed(item, account.media, settings):
        return []

    paths: list[str] = []
    for url in urls:
        try:
            path = _download_one(url, item.platform, settings)
        except Exception as exc:
            log.warning("media download failed for %s: %s", url, exc)
            continue
        if not path:
            continue
        width = _image_width(path)
        if account.media.min_width and width and width < account.media.min_width:
            log.info("skip image narrower than %s px: %s", account.media.min_width, url)
            Path(path).unlink(missing_ok=True)
            continue
        if account.media.min_width and width is None:
            Path(path).unlink(missing_ok=True)
            continue
        paths.append(path)
    return paths


def download_media(item: SourceItem, settings: Settings,
                   account: Optional[AccountConfig] = None) -> Optional[str]:
    if account is None:
        account = _legacy_account(settings)
    paths = download_media_list(item, account, settings)
    return paths[0] if paths else None


def _legacy_account(settings: Settings) -> AccountConfig:
    from .accounts import legacy_account
    return legacy_account(settings)


def _rights_allowed(item: SourceItem, media: MediaRules, settings: Settings) -> bool:
    if item.rights_status in ("authorized", "cc"):
        return True
    if media.rights_mode in ("editorial", "unverified"):
        return True
    return bool(settings.allow_unverified_repost)


def _download_one(url: str, platform: str, settings: Settings) -> Optional[str]:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        suffix = ".jpg"
    filename = hashlib.sha256(url.encode("utf-8")).hexdigest() + suffix
    target = settings.media_dir / filename
    if target.exists() and target.stat().st_size > 0:
        return str(target)

    settings.media_dir.mkdir(parents=True, exist_ok=True)
    headers = {**DEFAULT_HEADERS, **PLATFORM_HEADERS.get(platform, {})}
    res = requests.get(url, headers=headers, timeout=settings.media_timeout_seconds, stream=True)
    res.raise_for_status()
    content_type = res.headers.get("content-type", "")
    if content_type and not content_type.startswith("image/"):
        return None
    declared_size = int(res.headers.get("content-length") or 0)
    if declared_size and declared_size > settings.media_max_bytes:
        return None

    partial = target.with_suffix(target.suffix + ".part")
    written = 0
    try:
        with partial.open("wb") as handle:
            for chunk in res.iter_content(1024 * 64):
                if not chunk:
                    continue
                written += len(chunk)
                if written > settings.media_max_bytes:
                    raise ValueError("media exceeds MEDIA_MAX_BYTES")
                handle.write(chunk)
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return str(target)


def _image_width(path: str) -> Optional[int]:
    try:
        with Image.open(path) as image:
            return int(image.size[0])
    except Exception:
        return None