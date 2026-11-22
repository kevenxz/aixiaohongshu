from __future__ import annotations

import dataclasses

import pytest

from xhs_workflow.publisher import MCPPublisher


class FakeResponse:
    content = b'{"jsonrpc":"2.0","result":{}}'

    def raise_for_status(self):
        return None

    def json(self):
        return {"jsonrpc": "2.0", "result": {}}


def test_mcp_publisher_sends_absolute_image_paths(test_settings, monkeypatch, tmp_path):
    config = dataclasses.replace(
        test_settings,
        publisher="mcp",
        mcp_server_url="http://mcp.local/mcp",
        mcp_tool_name="publish_content",
        mcp_auth_token="local-token",
    )
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake-image")
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("xhs_workflow.publisher.requests.post", fake_post)
    result = MCPPublisher(config).publish({
        "id": 42,
        "title": "标题",
        "body": "正文",
        "tags": ["AI"],
        "source_note": "来源：https://example.com",
        "media_path": str(image),
    })

    assert result["mode"] == "mcp"
    assert captured["url"] == "http://mcp.local/mcp"
    assert captured["headers"]["Authorization"] == "Bearer local-token"
    params = captured["payload"]["params"]
    assert params["name"] == "publish_content"
    assert params["arguments"]["content"] == "正文"
    assert params["arguments"]["images"] == [str(image.resolve())]
    assert params["arguments"]["visibility"] == "公开可见"
    assert "source_note" not in params["arguments"]


def test_mcp_publisher_uses_public_media_url(test_settings, monkeypatch, tmp_path):
    config = dataclasses.replace(
        test_settings,
        publisher="mcp",
        mcp_server_url="http://mcp.local/mcp",
        xhs_media_base_url="http://app:8000/media",
        app_media_token="media-token",
    )
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake-image")
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr("xhs_workflow.publisher.requests.post", fake_post)
    MCPPublisher(config).publish({
        "id": 45, "title": "标题", "body": "正文", "tags": ["AI"],
        "source_note": "来源", "media_path": str(image),
    })
    assert captured["payload"]["params"]["arguments"]["images"] == [
        "http://app:8000/media/cover.jpg?token=media-token"
    ]


def test_mcp_publisher_raises_on_tool_error(test_settings, monkeypatch, tmp_path):
    class ErrorResponse:
        content = b'{"jsonrpc":"2.0","result":{"isError":true}}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"jsonrpc": "2.0", "result": {
                "isError": True,
                "content": [{"type": "text", "text": "发布失败: 未实名认证"}],
            }}

    config = dataclasses.replace(
        test_settings,
        publisher="mcp",
        mcp_server_url="http://mcp.local/mcp",
    )
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"fake-image")
    monkeypatch.setattr("xhs_workflow.publisher.requests.post", lambda *a, **k: ErrorResponse())
    with pytest.raises(RuntimeError, match="未实名认证"):
        MCPPublisher(config).publish({
            "id": 44, "title": "标题", "body": "正文", "tags": ["AI"],
            "source_note": "来源", "media_path": str(image),
        })


def test_mcp_publisher_requires_image(test_settings):
    config = dataclasses.replace(
        test_settings,
        publisher="mcp",
        mcp_server_url="http://mcp.local/mcp",
    )
    publisher = MCPPublisher(config)
    with pytest.raises(RuntimeError, match="至少需要一张配图"):
        publisher.publish({
            "id": 43, "title": "标题", "body": "正文", "tags": ["AI"],
            "source_note": "来源：https://example.com", "media_path": None,
        })
