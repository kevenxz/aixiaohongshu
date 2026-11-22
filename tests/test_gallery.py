from __future__ import annotations

import dataclasses

from xhs_workflow.accounts import AccountConfig, MediaRules, PublishSpec, SourceSpec
from xhs_workflow.publisher import MCPPublisher


class FakeResponse:
    content = b'{"jsonrpc":"2.0","result":{}}'

    def raise_for_status(self):
        return None

    def json(self):
        return {"jsonrpc": "2.0", "result": {}}


def _gallery_account(settings) -> AccountConfig:
    return AccountConfig(
        slug="star-gallery",
        name="明星图集",
        content_style="gallery",
        target_posts_per_day=9,
        media=MediaRules(rights_mode="editorial", max_images=9, min_images=3),
        sources=SourceSpec(weibo={"uids": []}),
        publish=PublishSpec(
            mode="mcp",
            mcp_url="http://mcp.star/mcp",
            token_env="TEST_STAR_TOKEN",
            visibility="仅自己可见",
        ),
    )


def test_mcp_publisher_sends_all_gallery_images(test_settings, monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_STAR_TOKEN", "star-token")
    account = _gallery_account(test_settings)
    first = tmp_path / "a.jpg"
    second = tmp_path / "b.jpg"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("xhs_workflow.publisher.requests.post", fake_post)
    MCPPublisher(test_settings, account).publish({
        "id": 7,
        "title": "明星A活动造型",
        "body": "正文",
        "tags": ["明星A", "活动"],
        "media_paths": [str(first), str(second)],
    })

    arguments = captured["payload"]["params"]["arguments"]
    assert arguments["images"] == [str(first.resolve()), str(second.resolve())]
    assert arguments["visibility"] == "仅自己可见"
    assert captured["url"] == "http://mcp.star/mcp"
    assert captured["headers"]["Authorization"] == "Bearer star-token"


def test_mcp_publisher_uses_public_urls_for_gallery(test_settings, monkeypatch, tmp_path):
    config = dataclasses.replace(
        test_settings, xhs_media_base_url="http://app:8000/media", app_media_token="media-token"
    )
    account = _gallery_account(config)
    images = []
    for name in ("a.jpg", "b.jpg"):
        path = tmp_path / name
        path.write_bytes(b"x")
        images.append(str(path))
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr("xhs_workflow.publisher.requests.post", fake_post)
    MCPPublisher(config, account).publish({
        "id": 8, "title": "t", "body": "b", "tags": [], "media_paths": images,
    })
    refs = captured["payload"]["params"]["arguments"]["images"]
    assert refs == [
        "http://app:8000/media/a.jpg?token=media-token",
        "http://app:8000/media/b.jpg?token=media-token",
    ]