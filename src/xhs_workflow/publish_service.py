from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

from PIL import Image, UnidentifiedImageError

from .accounts import AccountConfig, legacy_account
from .config import Settings
from .cover import generate_cover
from .db import Database, start_of_local_day_iso
from .publisher import Publisher

log = logging.getLogger(__name__)

MAX_PUBLISH_DIMENSION = 2560
MAX_PUBLISH_BYTES = 8 * 1024 * 1024
MAX_PUBLISH_IMAGES = 9
SAFE_IMAGE_FORMATS = ("JPEG", "PNG")
SAFE_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


def prepare_publish_image(source: Path, media_dir: Path, post_id: int,
                          index: int = 0) -> tuple[Path, list[str]]:
    """Convert GIF/oversize/unsupported images to a static XHS-friendly file."""
    notes: list[str] = []
    with Image.open(source) as image:
        image.seek(0)
        source_format = (image.format or "").upper()
        width, height = image.size
        too_large = source.stat().st_size > MAX_PUBLISH_BYTES or max(width, height) > MAX_PUBLISH_DIMENSION
        unsafe = source_format not in SAFE_IMAGE_FORMATS or source.suffix.lower() not in SAFE_IMAGE_SUFFIXES
        if not unsafe and not too_large:
            return source, notes

        if source_format == "GIF":
            notes.append("GIF 已转换为静态首帧图片")
        elif unsafe:
            notes.append(f"{source_format or '未知'} 图片已转换为 PNG 以便发布")
        if too_large:
            notes.append("图片已压缩以适配小红书发布")

        keep_alpha = source_format == "PNG" and "A" in image.getbands()
        if keep_alpha:
            prepared = image.convert("RGBA")
            suffix, image_format = ".png", "PNG"
        else:
            prepared = image.convert("RGB")
            suffix, image_format = ".jpg", "JPEG"
        if max(prepared.size) > MAX_PUBLISH_DIMENSION:
            prepared.thumbnail((MAX_PUBLISH_DIMENSION, MAX_PUBLISH_DIMENSION), Image.LANCZOS)

        stem = f"publish-{post_id}" + (f"-{index}" if index else "")
        target = media_dir / f"{stem}{suffix}"
        if image_format == "PNG":
            prepared.save(target, "PNG", optimize=True)
        else:
            prepared.save(target, "JPEG", quality=88, optimize=True)
    return target, notes


class PublishService:
    def __init__(self, settings: Settings, db: Database, publisher: Publisher,
                 account: Optional[AccountConfig] = None):
        self.settings = settings
        self.db = db
        self.publisher = publisher
        self.account = account or legacy_account(settings)
        if self.account.id == 0:
            ids = db.sync_accounts([self.account])
            self.account.id = ids.get(self.account.slug, self.account.id)
        self.auto_publish_limit = settings.auto_publish_limit
        self._lock = threading.Lock()

    def _ensure_media(self, post: dict[str, Any]) -> dict[str, Any]:
        if (self.account.publish.mode or self.settings.publisher) != "mcp":
            return post
        paths = [path for path in (post.get("media_paths") or []) if path]
        if not paths and post.get("media_path"):
            paths = [str(post["media_path"])]

        prepared_paths: list[Path] = []
        notes: list[str] = []
        source_failed = False
        for index, raw in enumerate(paths[:MAX_PUBLISH_IMAGES]):
            try:
                prepared, image_notes = prepare_publish_image(
                    Path(raw), self.settings.media_dir, post["id"], index
                )
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                log.warning("invalid publish image for post %s: %s", post["id"], exc)
                source_failed = True
                continue
            prepared_paths.append(prepared)
            for note in image_notes:
                if note not in notes:
                    notes.append(note)

        if not prepared_paths:
            prepared_paths = [generate_cover(post, self.settings.media_dir, self.account)]
            notes.append(
                "源图片无法用于发布，已改为自动封面"
                if paths or source_failed else "封面由系统自动生成（标题卡），不含来源图片"
            )

        flags = list(post.get("risk_flags") or [])
        for note in notes:
            if note not in flags:
                flags.append(note)
        self.db.update_post(post["id"], risk_flags=flags)
        self.db.replace_post_media(post["id"], [str(path) for path in prepared_paths], prepared=True)
        return self.db.get_post(post["id"]) or post

    def publish_one(self, post: dict[str, Any]) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("a publish run is already in progress")
        try:
            post = self._ensure_media(post)
            result = self.publisher.publish(post)
            self.db.mark_published(post["id"])
            return result
        finally:
            self._lock.release()

    def publish_queue(self, limit: int = 10, today_only: bool = False) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "skipped": True, "reason": "a publish run is already in progress",
                    "published": 0, "failed": 0, "results": []}
        try:
            posts = self.db.list_posts(
                status="approved", limit=max(1, min(limit, 50)), account_id=self.account.id
            )
            if today_only:
                day_start = start_of_local_day_iso(self.settings.app_timezone)
                posts = [post for post in posts if (post.get("created_at") or "") >= day_start]
            results = []
            for post in posts:
                try:
                    post = self._ensure_media(post)
                    result = self.publisher.publish(post)
                    self.db.mark_published(post["id"])
                    results.append({"post_id": post["id"], "ok": True, "result": result})
                except Exception as exc:
                    log.exception("publish failed for post %s", post["id"])
                    results.append({"post_id": post["id"], "ok": False, "error": str(exc)})
            published = sum(1 for item in results if item["ok"])
            failed = len(results) - published
            return {"ok": failed == 0, "published": published, "failed": failed, "results": results}
        finally:
            self._lock.release()

    def run_auto(self) -> dict[str, Any]:
        result = self.publish_queue(limit=self.auto_publish_limit, today_only=True)
        log.info("auto publish %s: %s", self.account.slug, result)
        return result