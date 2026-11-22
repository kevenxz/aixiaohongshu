from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .config import Settings

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ENV_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-([^}]*))?\}")
CONTENT_STYLES = ("article", "gallery")
RIGHTS_MODES = ("verified", "editorial", "unverified")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match) -> str:
            resolved = os.getenv(match.group(1), "")
            return resolved or (match.group(2) or "")
        return ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class MediaRules:
    rights_mode: str = "verified"
    max_images: int = 1
    min_images: int = 1
    min_width: int = 0

    @property
    def allow_unverified(self) -> bool:
        return self.rights_mode in ("editorial", "unverified")


@dataclass
class ScheduleSpec:
    collect_cron: str = "0 8 * * *"
    auto_publish: bool = False
    auto_publish_cron: str = "30 9 * * *"
    auto_publish_limit: int = 3


@dataclass
class PublishSpec:
    mode: str = "manual"
    mcp_url: str = ""
    mcp_tool_name: str = "publish_content"
    token_env: str = "XHS_MCP_AUTH_TOKEN"
    visibility: str = "公开可见"
    webhook_url: str = ""
    webhook_url_env: str = ""

    def token(self) -> str:
        if not self.token_env:
            return ""
        return os.getenv(self.token_env, "")

    def resolved_webhook_url(self) -> str:
        if self.webhook_url_env:
            return os.getenv(self.webhook_url_env, "") or self.webhook_url
        return self.webhook_url


@dataclass
class PromptSpec:
    persona: str = ""
    extra_rules: str = ""


@dataclass
class SourceSpec:
    reddit: Optional[dict[str, Any]] = None
    x: Optional[dict[str, Any]] = None
    youtube: Optional[dict[str, Any]] = None
    rss: list[dict[str, str]] = field(default_factory=list)
    weibo: Optional[dict[str, Any]] = None
    instagram: Optional[dict[str, Any]] = None


@dataclass
class AccountConfig:
    slug: str
    name: str
    id: int = 0
    avatar: str = "S"
    accent: str = "#c9f967"
    enabled: bool = True
    content_style: str = "article"
    tagline: str = "把世界的下一条灵感，带回你的 feed."
    description: str = ""
    relevance_only: bool = True
    keywords: list[str] = field(default_factory=list)
    min_score: float = 0.15
    target_posts_per_day: int = 30
    media: MediaRules = field(default_factory=MediaRules)
    sources: SourceSpec = field(default_factory=SourceSpec)
    schedule: ScheduleSpec = field(default_factory=ScheduleSpec)
    publish: PublishSpec = field(default_factory=PublishSpec)
    prompt: PromptSpec = field(default_factory=PromptSpec)

    @property
    def is_gallery(self) -> bool:
        return self.content_style == "gallery"

    @property
    def platforms(self) -> list[str]:
        output: list[str] = []
        if self.sources.reddit is not None:
            output.append("reddit")
        if self.sources.x is not None:
            output.append("x")
        if self.sources.youtube is not None:
            output.append("youtube")
        if self.sources.rss:
            output.append("web")
        if self.sources.weibo is not None:
            output.append("weibo")
        if self.sources.instagram is not None:
            output.append("instagram")
        return output

    @property
    def display_tagline(self) -> str:
        return self.tagline or self.name


def accounts_path(settings: Settings) -> Path:
    configured = Path(settings.accounts_file)
    if configured.is_absolute():
        return configured
    return PROJECT_ROOT / configured


def load_accounts(settings: Settings) -> list[AccountConfig]:
    path = accounts_path(settings)
    if path.exists():
        accounts = _load_yaml_accounts(path, settings)
        if accounts:
            return accounts
        log.warning("no valid accounts in %s; falling back to .env account", path)
    return [legacy_account(settings)]


def _load_yaml_accounts(path: Path, settings: Settings) -> list[AccountConfig]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.error("failed to read %s: %s", path, exc)
        return []
    raw_accounts = payload.get("accounts") if isinstance(payload, dict) else payload
    if not isinstance(raw_accounts, list):
        log.error("accounts file %s must contain an accounts list", path)
        return []

    accounts: list[AccountConfig] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_accounts, start=1):
        if not isinstance(raw, dict):
            log.error("account #%s in %s is not a mapping", index, path)
            continue
        try:
            account = _parse_account(raw, settings)
        except Exception as exc:
            log.error("invalid account #%s in %s: %s", index, path, exc)
            continue
        if account.slug in seen:
            log.error("duplicate account slug %s in %s", account.slug, path)
            continue
        seen.add(account.slug)
        accounts.append(account)
    return accounts


def _parse_account(raw: dict[str, Any], settings: Settings) -> AccountConfig:
    raw = _expand_env(raw)
    slug = str(raw.get("slug") or "").strip().lower()
    if not SLUG_PATTERN.match(slug):
        raise ValueError(f"invalid slug: {slug!r}")
    name = str(raw.get("name") or slug).strip()
    content_style = str(raw.get("content_style") or "article").strip().lower()
    if content_style not in CONTENT_STYLES:
        raise ValueError(f"invalid content_style: {content_style!r}")

    relevance = _as_dict(raw.get("relevance"))
    media = _parse_media(raw.get("media"), settings)
    schedule = _parse_schedule(raw.get("schedule"), settings)
    publish = _parse_publish(raw.get("publish"), settings)
    sources = _parse_sources(raw.get("sources"), settings)

    keywords = _as_list(relevance.get("keywords")) or list(settings.ai_keywords)
    tagline = str(raw.get("tagline") or "把世界的下一条灵感，带回你的 feed.").strip()
    description = str(raw.get("description") or "").strip()
    if not description:
        description = (
            "从微博与 Instagram 追踪明星最新写真、活动与造型，整理成可直接发布的小红书图集。"
            if content_style == "gallery"
            else "只捕捉 AI 世界的高热信号：从 Reddit、X、YouTube 找到正在升温的模型、工具和观点。"
        )
    avatar = str(raw.get("avatar") or name[:1] or "S").strip()[:2]
    accent = str(raw.get("accent") or "#c9f967").strip()

    return AccountConfig(
        slug=slug,
        name=name,
        avatar=avatar,
        accent=accent,
        enabled=_as_bool(raw.get("enabled"), True),
        content_style=content_style,
        tagline=tagline,
        description=description,
        relevance_only=_as_bool(relevance.get("only"), settings.ai_only),
        keywords=keywords,
        min_score=_as_float(relevance.get("min_score"), settings.min_ai_score),
        target_posts_per_day=_as_int(raw.get("target_posts_per_day"), settings.target_posts_per_day),
        media=media,
        sources=sources,
        schedule=schedule,
        publish=publish,
        prompt=PromptSpec(
            persona=str(_as_dict(raw.get("prompt")).get("persona") or "").strip(),
            extra_rules=str(_as_dict(raw.get("prompt")).get("extra_rules") or "").strip(),
        ),
    )


def _parse_media(raw: Any, settings: Settings) -> MediaRules:
    data = _as_dict(raw)
    default_rights = "unverified" if settings.allow_unverified_repost else "verified"
    rights_mode = str(data.get("rights_mode") or default_rights).strip().lower()
    if rights_mode not in RIGHTS_MODES:
        rights_mode = default_rights
    return MediaRules(
        rights_mode=rights_mode,
        max_images=_as_int(data.get("max_images"), 1, minimum=1),
        min_images=_as_int(data.get("min_images"), 1, minimum=0),
        min_width=_as_int(data.get("min_width"), 0),
    )


def _parse_schedule(raw: Any, settings: Settings) -> ScheduleSpec:
    data = _as_dict(raw)
    return ScheduleSpec(
        collect_cron=str(data.get("collect_cron") or settings.collect_cron),
        auto_publish=_as_bool(data.get("auto_publish"), settings.auto_publish),
        auto_publish_cron=str(data.get("auto_publish_cron") or settings.auto_publish_cron),
        auto_publish_limit=_as_int(
            data.get("auto_publish_limit"), settings.auto_publish_limit, minimum=1
        ),
    )


def _parse_publish(raw: Any, settings: Settings) -> PublishSpec:
    data = _as_dict(raw)
    mode = str(data.get("mode") or settings.publisher).strip().lower()
    if mode not in ("manual", "webhook", "mcp"):
        mode = "manual"
    return PublishSpec(
        mode=mode,
        mcp_url=str(data.get("mcp_url") or settings.mcp_server_url),
        mcp_tool_name=str(data.get("mcp_tool_name") or settings.mcp_tool_name),
        token_env=str(data.get("token_env") or "XHS_MCP_AUTH_TOKEN"),
        visibility=str(data.get("visibility") or settings.xhs_visibility),
        webhook_url=str(data.get("webhook_url") or settings.publish_webhook_url),
        webhook_url_env=str(data.get("webhook_url_env") or ""),
    )


def _parse_sources(raw: Any, settings: Settings) -> SourceSpec:
    if raw is None:
        return legacy_sources(settings)
    data = _as_dict(raw)
    return SourceSpec(
        reddit=_parse_reddit(data.get("reddit"), settings) if data.get("reddit") is not None else None,
        x=_parse_x(data.get("x"), settings) if data.get("x") is not None else None,
        youtube=_parse_youtube(data.get("youtube"), settings) if data.get("youtube") is not None else None,
        rss=_parse_feeds(data.get("rss")),
        weibo=_parse_weibo(data.get("weibo")) if data.get("weibo") is not None else None,
        instagram=_parse_instagram(data.get("instagram")) if data.get("instagram") is not None else None,
    )


def _parse_reddit(raw: Any, settings: Settings) -> dict[str, Any]:
    data = _as_dict(raw)
    return {
        "mode": str(data.get("mode") or settings.reddit_mode).lower(),
        "subreddits": _as_list(data.get("subreddits")) or list(settings.reddit_subreddits),
        "sort": str(data.get("sort") or settings.reddit_sort),
        "top_time": str(data.get("top_time") or settings.reddit_top_time),
        "client_id_env": str(data.get("client_id_env") or "REDDIT_CLIENT_ID"),
        "client_secret_env": str(data.get("client_secret_env") or "REDDIT_CLIENT_SECRET"),
        "user_agent": str(data.get("user_agent") or settings.reddit_user_agent),
    }


def _parse_x(raw: Any, settings: Settings) -> dict[str, Any]:
    data = _as_dict(raw)
    accounts = [handle.strip().lstrip("@") for handle in _as_list(data.get("priority_accounts")) if handle.strip()]
    list_id = str(data.get("list_id") or settings.x_list_id).strip()
    if "/lists/" in list_id:
        list_id = list_id.rsplit("/lists/", 1)[-1].split("?")[0].strip("/")
    return {
        "bearer_token_env": str(data.get("bearer_token_env") or "X_BEARER_TOKEN"),
        "queries": _as_list(data.get("queries")) or list(settings.x_queries),
        "priority_accounts": accounts,
        "list_id": list_id,
        "priority_boost": _as_float(data.get("priority_boost"), settings.x_priority_boost),
        "priority_all": _as_bool(data.get("priority_all"), settings.x_priority_all),
    }


def _parse_youtube(raw: Any, settings: Settings) -> dict[str, Any]:
    data = _as_dict(raw)
    return {
        "api_key_env": str(data.get("api_key_env") or "YOUTUBE_API_KEY"),
        "queries": _as_list(data.get("queries")) or list(settings.youtube_queries),
        "days": _as_int(data.get("days"), settings.youtube_days, minimum=1),
        "video_duration": str(data.get("video_duration") or settings.youtube_video_duration),
        "region_code": str(data.get("region_code") or settings.youtube_region_code),
        "language": str(data.get("language") or settings.youtube_language),
    }


def _parse_feeds(raw: Any) -> list[dict[str, str]]:
    feeds: list[dict[str, str]] = []
    if raw is None:
        return feeds
    items = raw if isinstance(raw, list) else [raw]
    for item in items:
        if isinstance(item, str):
            if "|" in item:
                label, url = item.split("|", 1)
            else:
                label, url = "", item
        elif isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or "")
            url = str(item.get("url") or "")
        else:
            continue
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            log.warning("ignoring feed without an http(s) URL: %s", url or item)
            continue
        feeds.append({"label": label.strip() or url, "url": url})
    return feeds


def _parse_weibo(raw: Any) -> dict[str, Any]:
    data = _as_dict(raw)
    uids: list[dict[str, str]] = []
    for item in data.get("uids") or data.get("accounts") or []:
        if isinstance(item, dict):
            uid = str(item.get("uid") or item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
        else:
            uid, name = str(item).strip(), ""
        if uid:
            uids.append({"uid": uid, "name": name})
    return {
        "uids": uids,
        "cookie_env": str(data.get("cookie_env") or "WEIBO_COOKIE"),
        "days": _as_int(data.get("days"), 3, minimum=1),
        "include_retweets": _as_bool(data.get("include_retweets"), False),
        "feeds": _parse_feeds(data.get("feeds")),
    }


def _parse_instagram(raw: Any) -> dict[str, Any]:
    data = _as_dict(raw)
    return {"feeds": _parse_feeds(data.get("feeds"))}


def legacy_sources(settings: Settings) -> SourceSpec:
    return SourceSpec(
        reddit={
            "mode": settings.reddit_mode,
            "subreddits": list(settings.reddit_subreddits),
            "sort": settings.reddit_sort,
            "top_time": settings.reddit_top_time,
            "client_id_env": "REDDIT_CLIENT_ID",
            "client_secret_env": "REDDIT_CLIENT_SECRET",
            "user_agent": settings.reddit_user_agent,
        },
        x={
            "bearer_token_env": "X_BEARER_TOKEN",
            "queries": list(settings.x_queries),
            "priority_accounts": list(settings.x_priority_accounts),
            "list_id": settings.x_list_id,
            "priority_boost": settings.x_priority_boost,
            "priority_all": settings.x_priority_all,
        },
        youtube={
            "api_key_env": "YOUTUBE_API_KEY",
            "queries": list(settings.youtube_queries),
            "days": settings.youtube_days,
            "video_duration": settings.youtube_video_duration,
            "region_code": settings.youtube_region_code,
            "language": settings.youtube_language,
        },
        rss=_parse_feeds(settings.rss_feeds),
    )


def legacy_account(settings: Settings) -> AccountConfig:
    return AccountConfig(
        slug="ai-signals",
        name="AI 情报站",
        avatar="AI",
        accent="#c9f967",
        enabled=True,
        content_style="article",
        tagline="把世界的\n下一条灵感，\n带回你的 feed.",
        description=(
            "只捕捉 AI 世界的高热信号：从 Reddit、X、YouTube 找到正在升温的模型、工具和观点，"
            "交给模型翻译、重组、打磨，再由你决定它如何抵达小红书。"
        ),
        relevance_only=settings.ai_only,
        keywords=list(settings.ai_keywords),
        min_score=settings.min_ai_score,
        target_posts_per_day=settings.target_posts_per_day,
        media=MediaRules(
            rights_mode="unverified" if settings.allow_unverified_repost else "verified",
            max_images=1,
            min_images=1,
        ),
        sources=legacy_sources(settings),
        schedule=ScheduleSpec(
            collect_cron=settings.collect_cron,
            auto_publish=settings.auto_publish,
            auto_publish_cron=settings.auto_publish_cron,
            auto_publish_limit=settings.auto_publish_limit,
        ),
        publish=PublishSpec(
            mode=settings.publisher,
            mcp_url=settings.mcp_server_url,
            mcp_tool_name=settings.mcp_tool_name,
            token_env="XHS_MCP_AUTH_TOKEN",
            visibility=settings.xhs_visibility,
            webhook_url=settings.publish_webhook_url,
        ),
    )


def assign_account_ids(accounts: list[AccountConfig], ids: dict[str, int]) -> list[AccountConfig]:
    for account in accounts:
        account.id = ids.get(account.slug, account.id)
    return accounts


def account_by_slug(accounts: list[AccountConfig], slug: str) -> Optional[AccountConfig]:
    return next((account for account in accounts if account.slug == slug), None)


def default_account(accounts: list[AccountConfig]) -> Optional[AccountConfig]:
    return next((account for account in accounts if account.enabled), None)


def env_value(name: str, fallback: str = "") -> str:
    return os.getenv(name, fallback) if name else fallback