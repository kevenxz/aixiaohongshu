from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import requests

from .accounts import AccountConfig, legacy_account
from .config import Settings


def render_markdown(post: dict[str, Any]) -> str:
    tags = " ".join(f"#{tag.lstrip('#')}" for tag in post.get("tags", []))
    text = f"# {post['title']}\n\n{post['body']}\n\n{post['source_note']}\n\n{tags}\n"
    media_paths = post.get("media_paths") or (
        [post["media_path"]] if post.get("media_path") else []
    )
    if media_paths:
        text += "\n配图：\n" + "\n".join(f"- {path}" for path in media_paths) + "\n"
    return text


class Publisher:
    def publish(self, post: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class ManualPublisher(Publisher):
    def __init__(self, settings: Settings, account: Optional[AccountConfig] = None):
        self.settings = settings
        self.account = account

    def publish(self, post: dict[str, Any]) -> dict[str, Any]:
        export_dir = self.settings.export_dir
        if self.account and self.account.slug:
            export_dir = export_dir / self.account.slug
        export_dir.mkdir(parents=True, exist_ok=True)
        path = export_dir / f"post-{post['id']}.md"
        path.write_text(render_markdown(post), encoding="utf-8")
        return {"mode": "manual", "path": str(path)}


class WebhookPublisher(Publisher):
    """通用发布网关适配器，不冒充小红书官方接口。"""

    def __init__(self, settings: Settings, account: Optional[AccountConfig] = None):
        self.settings = settings
        self.account = account

    def publish(self, post: dict[str, Any]) -> dict[str, Any]:
        url = (
            self.account.publish.resolved_webhook_url()
            if self.account else self.settings.publish_webhook_url
        )
        if not url:
            raise RuntimeError("PUBLISH_WEBHOOK_URL 未配置")
        res = requests.post(url, json=post, timeout=30)
        res.raise_for_status()
        return {"mode": "webhook", "response": _json_or_text(res)}


class MCPPublisher(Publisher):
    """通过用户自己的 MCP HTTP 网关调用发布工具。

    xpzouying/xiaohongshu-mcp 的 publish_content 需要
    title / content / images，images 支持本地绝对路径或可访问 URL。
    """

    def __init__(self, settings: Settings, account: Optional[AccountConfig] = None):
        self.settings = settings
        self.account = account
        publish = account.publish if account else None
        self.url = publish.mcp_url if publish else settings.mcp_server_url
        self.tool_name = publish.mcp_tool_name if publish else settings.mcp_tool_name
        self.auth_token = publish.token() if publish else settings.mcp_auth_token
        self.visibility = publish.visibility if publish else settings.xhs_visibility
        self.media_base_url = settings.xhs_media_base_url
        self.media_token = settings.app_media_token

    def publish(self, post: dict[str, Any]) -> dict[str, Any]:
        if not self.url:
            raise RuntimeError("XHS_MCP_SERVER_URL 未配置")
        paths = post.get("media_paths") or (
            [post["media_path"]] if post.get("media_path") else []
        )
        paths = [path for path in paths if path][:9]
        if not paths:
            raise RuntimeError("小红书图文发布至少需要一张配图；请先补充封面或源图片")

        images: list[str] = []
        for raw in paths:
            image_path = Path(raw).resolve()
            if not image_path.exists():
                raise RuntimeError(f"配图文件不存在: {image_path}")
            if self.media_base_url:
                image_ref = f"{self.media_base_url.rstrip('/')}/{image_path.name}"
                if self.media_token:
                    image_ref += f"?token={quote(self.media_token, safe='')}"
            else:
                image_ref = str(image_path)
            images.append(image_ref)

        payload = {
            "jsonrpc": "2.0",
            "id": f"xhs-post-{post['id']}",
            "method": "tools/call",
            "params": {"name": self.tool_name, "arguments": {
                "title": post["title"],
                "content": post["body"],
                "tags": post.get("tags", []),
                "images": images,
                "visibility": self.visibility,
            }},
        }
        headers = {"Accept": "application/json, text/event-stream"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        res = requests.post(self.url, json=payload, headers=headers, timeout=180)
        res.raise_for_status()
        response = _json_or_text(res)
        if isinstance(response, dict) and response.get("error"):
            raise RuntimeError(f"MCP error: {response['error']}")
        result = response.get("result") if isinstance(response, dict) else None
        if isinstance(result, dict) and result.get("isError"):
            text = " ".join(
                str(item.get("text", "")) for item in result.get("content", [])
                if isinstance(item, dict)
            ).strip()
            raise RuntimeError(f"MCP publish failed: {text or 'unknown error'}")
        return {"mode": "mcp", "response": response}


def _json_or_text(res: requests.Response) -> Any:
    if not res.content:
        return {}
    try:
        return res.json()
    except ValueError:
        return {"text": res.text[:2000]}


def build_publisher(settings: Settings, account: Optional[AccountConfig] = None) -> Publisher:
    account = account or legacy_account(settings)
    mode = account.publish.mode or settings.publisher
    if mode == "mcp":
        return MCPPublisher(settings, account)
    if mode == "webhook":
        return WebhookPublisher(settings, account)
    return ManualPublisher(settings, account)