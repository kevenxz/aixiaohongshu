from __future__ import annotations

import dataclasses

from PIL import Image

from xhs_workflow.publish_service import PublishService


class FakePublisher:
    def __init__(self):
        self.calls = []

    def publish(self, post):
        self.calls.append(post["id"])
        return {"mode": "fake"}


def test_auto_publish_only_today_and_marks_published(db, test_settings, make_post):
    today = make_post("today post")
    old = make_post("old post")
    db.update_status(today, "approved")
    db.update_status(old, "approved")
    with db.connect() as conn:
        conn.execute("UPDATE posts SET created_at='2020-01-01T00:00:00+00:00' WHERE id=?", (old,))

    publisher = FakePublisher()
    service = PublishService(
        dataclasses.replace(test_settings, publisher="manual"), db, publisher
    )
    result = service.publish_queue(limit=5, today_only=True)

    assert result["published"] == 1
    assert publisher.calls == [today]
    assert db.get_post(today)["status"] == "published"
    assert db.get_post(old)["status"] == "approved"


def test_auto_publish_respects_limit(db, test_settings, make_post):
    ids = [make_post(f"post {index}") for index in range(3)]
    for post_id in ids:
        db.update_status(post_id, "approved")
    publisher = FakePublisher()
    service = PublishService(
        dataclasses.replace(test_settings, publisher="manual"), db, publisher
    )

    result = service.publish_queue(limit=2, today_only=True)

    assert result["published"] == 2
    assert len(publisher.calls) == 2


def test_mcp_publish_generates_cover_when_missing(db, test_settings, make_post):
    post_id = make_post("需要封面的 Reddit 帖子")
    db.update_status(post_id, "approved")
    publisher = FakePublisher()
    service = PublishService(
        dataclasses.replace(test_settings, publisher="mcp"), db, publisher
    )

    result = service.publish_queue(limit=1, today_only=True)

    assert result["published"] == 1
    post = db.get_post(post_id)
    assert post["media_file"] == f"cover-{post_id}.png"
    assert post["status"] == "published"


def test_mcp_publish_converts_gif_before_upload(db, test_settings, make_post):
    test_settings.media_dir.mkdir(parents=True, exist_ok=True)
    gif_path = test_settings.media_dir / "source.gif"
    Image.new("RGB", (140, 139), (210, 45, 45)).save(gif_path, "GIF")
    post_id = make_post("GIF 图片帖子", media_path=str(gif_path))
    db.update_status(post_id, "approved")
    publisher = FakePublisher()
    service = PublishService(
        dataclasses.replace(test_settings, publisher="mcp"), db, publisher
    )

    result = service.publish_queue(limit=1, today_only=True)

    assert result["published"] == 1
    post = db.get_post(post_id)
    assert post["media_file"] == f"publish-{post_id}.jpg"
    assert any("GIF 已转换为静态首帧" in flag for flag in post["risk_flags"])
    with Image.open(test_settings.media_dir / post["media_file"]) as image:
        assert image.format == "JPEG"
