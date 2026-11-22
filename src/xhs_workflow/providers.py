from __future__ import annotations

import base64
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from . import rss
from .accounts import AccountConfig, legacy_account
from .config import Settings
from .models import SourceItem
from .scoring import ai_score as _ai_score
from .scoring import community_ai_score as _community_ai_score
from .scoring import heat_label as _heat_label

log = logging.getLogger(__name__)


def _env(name: str, fallback: str = "") -> str:
    return os.getenv(name, "") or fallback


class Provider:
    name = "base"

    def __init__(self, settings: Settings, account: Optional[AccountConfig] = None):
        self.s = settings
        self.account = account or legacy_account(settings)
        self.keywords = self.account.keywords

    def fetch(self, limit: int) -> list[SourceItem]:
        raise NotImplementedError


class RedditProvider(Provider):
    name = "reddit"

    def __init__(self, settings: Settings, account: Optional[AccountConfig] = None):
        super().__init__(settings, account)
        self.cfg = self.account.sources.reddit or {}
        self.token: Optional[str] = None
        self.client_id = _env(str(self.cfg.get("client_id_env") or ""), settings.reddit_client_id)
        self.client_secret = _env(str(self.cfg.get("client_secret_env") or ""), settings.reddit_client_secret)
        self.user_agent = str(self.cfg.get("user_agent") or settings.reddit_user_agent)
        self.subreddits = list(self.cfg.get("subreddits") or settings.reddit_subreddits)
        self.sort = str(self.cfg.get("sort") or settings.reddit_sort)
        self.top_time = str(self.cfg.get("top_time") or settings.reddit_top_time)
        self.mode = str(self.cfg.get("mode") or settings.reddit_mode).lower()

    def _access_token(self) -> str:
        if self.token:
            return self.token
        if not self.client_id or not self.client_secret:
            raise RuntimeError("缺少 REDDIT_CLIENT_ID 或 REDDIT_CLIENT_SECRET")
        auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        res = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            headers={"Authorization": f"Basic {auth}", "User-Agent": self.user_agent},
            data={"grant_type": "client_credentials"}, timeout=20,
        )
        res.raise_for_status()
        self.token = res.json()["access_token"]
        return self.token

    def fetch(self, limit: int) -> list[SourceItem]:
        use_api = self.mode == "api" or (
            self.mode == "auto" and bool(self.client_id) and bool(self.client_secret)
        )
        if use_api:
            return self._fetch_api(limit)
        return self._fetch_rss(limit)

    def _fetch_api(self, limit: int) -> list[SourceItem]:
        token = self._access_token()
        output: list[SourceItem] = []
        per_sub = max(1, limit // max(1, len(self.subreddits)))
        for subreddit in self.subreddits:
            res = requests.get(
                f"https://oauth.reddit.com/r/{subreddit}/{self.sort}",
                headers={"Authorization": f"Bearer {token}", "User-Agent": self.user_agent},
                params={"limit": min(per_sub, 100), "raw_json": 1, "t": self.top_time}, timeout=20,
            )
            res.raise_for_status()
            for child in res.json().get("data", {}).get("children", []):
                data = child.get("data", {})
                url = data.get("url") or f"https://www.reddit.com{data.get('permalink', '')}"
                media_url = data.get("url_overridden_by_dest") if data.get("post_hint") == "image" else None
                ai_score = max(
                    _ai_score(f"{data.get('title', '')} {data.get('selftext', '')}", self.keywords),
                    _community_ai_score(subreddit, self.keywords),
                )
                engagement = math.log1p(max(0, int(data.get("score") or 0))) + 0.7 * math.log1p(max(0, int(data.get("num_comments") or 0)))
                heat_score = engagement + (ai_score * 3)
                output.append(SourceItem(
                    platform=self.name, source_id=str(data.get("id", "")), source_url=url,
                    author=str(data.get("author", "")), title=data.get("title", ""),
                    text=data.get("selftext", "") or data.get("title", ""),
                    published_at=None, media_url=media_url, media_type="image" if media_url else None,
                    media_urls=[media_url] if media_url else [],
                    rights_status="unknown", heat_score=heat_score, heat_label=_heat_label(heat_score),
                    ai_score=ai_score, metadata=data,
                ))
        return sorted(output, key=lambda item: item.heat_score, reverse=True)[:limit]

    def _fetch_rss(self, limit: int) -> list[SourceItem]:
        subreddits = [sub for sub in self.subreddits if sub]
        if not subreddits:
            return []
        # One combined feed request avoids per-subreddit RSS rate limits.
        url = f"https://www.reddit.com/r/{'+'.join(subreddits)}/{self.sort}/.rss"
        params = {"limit": min(100, max(10, limit * 3))}
        if self.sort == "top":
            params["t"] = self.top_time
        try:
            xml_text = rss.fetch_feed(url, self.user_agent)
        except Exception as exc:
            log.warning("reddit rss failed: %s", exc)
            raise RuntimeError(str(exc)) from exc
        ai_floor = max(
            _community_ai_score(sub, self.keywords) for sub in subreddits
        )
        items = rss.parse_feed(
            xml_text,
            feed_label=f"r/{'+'.join(subreddits)}",
            feed_url=url,
            platform=self.name,
            ai_keywords=self.keywords,
            ai_floor=ai_floor,
        )
        items.sort(key=lambda item: item.heat_score, reverse=True)
        return items[:limit]


class XProvider(Provider):
    name = "x"
    api_base = "https://api.x.com/2"
    priority_share = 0.6
    max_priority_queries = 6
    account_query_budget = 400

    def __init__(self, settings: Settings, account: Optional[AccountConfig] = None):
        super().__init__(settings, account)
        self.cfg = self.account.sources.x or {}
        self.bearer_token = _env(str(self.cfg.get("bearer_token_env") or ""), settings.x_bearer_token)
        self.queries = list(self.cfg.get("queries") or [])
        self.priority_accounts = list(self.cfg.get("priority_accounts") or [])
        self.list_id = str(self.cfg.get("list_id") or "")
        self.priority_boost = float(self.cfg.get("priority_boost") or settings.x_priority_boost)
        self.priority_all = bool(self.cfg.get("priority_all", settings.x_priority_all))

    def fetch(self, limit: int) -> list[SourceItem]:
        if not self.bearer_token:
            raise RuntimeError("缺少 X_BEARER_TOKEN")
        limit = max(1, limit)
        priority_limit = max(1, round(limit * self.priority_share)) if self.queries else limit
        errors: list[str] = []
        priority_items: list[SourceItem] = []

        if self.list_id:
            try:
                priority_items.extend(self._fetch_list(limit))
            except Exception as exc:
                log.warning("x list timeline failed: %s", exc)
                errors.append(f"list: {exc}")

        if self.priority_accounts:
            try:
                for query in self._priority_queries():
                    priority_items.extend(self._search(
                        query, max(10, min(100, limit * 2)), priority=True, stream="priority_accounts",
                    ))
            except Exception as exc:
                log.warning("x priority account search failed: %s", exc)
                errors.append(f"priority accounts: {exc}")

        priority_items = self._dedupe(priority_items)
        priority_items.sort(key=lambda item: item.heat_score, reverse=True)
        selected_priority = priority_items[:priority_limit]

        search_items: list[SourceItem] = []
        if self.queries:
            per_query = max(10, min(100, limit // max(1, len(self.queries))))
            for query in self.queries:
                try:
                    search_items.extend(self._search(query, per_query, priority=False, stream="search"))
                except Exception as exc:
                    log.warning("x ai search failed for %r: %s", query, exc)
                    errors.append(f"search {query!r}: {exc}")

        combined = self._dedupe([*selected_priority, *search_items])
        if len(combined) < limit:
            # Search can fail or return fewer items; backfill the remaining slots
            # with priority posts that were held back by the 60% share.
            combined = self._dedupe([*combined, *priority_items])
        combined.sort(key=lambda item: item.heat_score, reverse=True)
        result = combined[:limit]
        if not result and errors:
            raise RuntimeError("; ".join(errors))
        return result

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"}

    def _params(self, max_results: int) -> dict[str, Any]:
        return {
            "max_results": max(10, min(100, max_results)),
            "tweet.fields": "created_at,author_id,public_metrics,attachments,referenced_tweets",
            "expansions": "attachments.media_keys,author_id",
            "media.fields": "url,preview_image_url,type",
            "user.fields": "username,name",
        }

    def _fetch_list(self, limit: int) -> list[SourceItem]:
        res = requests.get(
            f"{self.api_base}/lists/{self.list_id}/tweets",
            headers=self._headers(),
            params=self._params(max(10, limit * 2)),
            timeout=20,
        )
        res.raise_for_status()
        return self._parse_posts(res.json(), priority=True, stream="list")

    def _search(self, query: str, max_results: int, priority: bool, stream: str) -> list[SourceItem]:
        res = requests.get(
            f"{self.api_base}/tweets/search/recent",
            headers=self._headers(),
            params={**self._params(max_results), "query": f"({query}) -is:retweet"},
            timeout=20,
        )
        res.raise_for_status()
        return self._parse_posts(res.json(), priority=priority, stream=stream)

    def _priority_queries(self) -> list[str]:
        chunks: list[list[str]] = []
        current: list[str] = []
        length = 0
        for handle in self.priority_accounts:
            token = f"from:{handle}"
            addition = len(token) + (4 if current else 0)
            if current and length + addition > self.account_query_budget:
                chunks.append(current)
                current, length = [], 0
                addition = len(token)
            current.append(token)
            length += addition
        if current:
            chunks.append(current)
        if len(chunks) > self.max_priority_queries:
            log.warning(
                "x priority accounts require %s queries; only %s are used per run",
                len(chunks), self.max_priority_queries,
            )
            chunks = chunks[:self.max_priority_queries]
        return [" OR ".join(chunk) for chunk in chunks]

    def _parse_posts(self, payload: dict[str, Any], priority: bool, stream: str) -> list[SourceItem]:
        includes = payload.get("includes", {})
        media_by_key = {m.get("media_key"): m for m in includes.get("media", [])}
        users_by_id = {user.get("id"): user for user in includes.get("users", [])}
        output: list[SourceItem] = []
        for post in payload.get("data", []):
            if not post.get("id") or not post.get("text"):
                continue
            referenced = post.get("referenced_tweets") or []
            if any(ref.get("type") == "retweeted" for ref in referenced):
                continue
            media = None
            attachments = post.get("attachments", {}).get("media_keys", [])
            if attachments:
                media = media_by_key.get(attachments[0], {})
            media_url = (media or {}).get("url") or (media or {}).get("preview_image_url")
            metrics = post.get("public_metrics", {})
            engagement = (metrics.get("like_count", 0) + 2 * metrics.get("retweet_count", 0)
                          + metrics.get("reply_count", 0) + 2 * metrics.get("quote_count", 0))
            ai_score = _ai_score(post.get("text", ""), self.keywords)
            heat_score = math.log1p(max(0, engagement)) + ai_score * 3
            if priority:
                heat_score += self.priority_boost
            author_data = users_by_id.get(post.get("author_id"), {})
            username = author_data.get("username")
            author = f"@{username}" if username else str(post.get("author_id", ""))
            output.append(SourceItem(
                platform=self.name, source_id=str(post["id"]),
                source_url=f"https://x.com/i/web/status/{post['id']}",
                author=author, title="", text=post.get("text", ""),
                published_at=post.get("created_at"), media_url=media_url,
                media_type=(media or {}).get("type"), media_urls=[media_url] if media_url else [],
                rights_status="unknown", heat_score=heat_score,
                heat_label="关注" if priority else _heat_label(heat_score), ai_score=ai_score,
                priority=priority and self.priority_all,
                metadata={**post, "stream": stream, "priority": priority},
            ))
        return output

    @staticmethod
    def _dedupe(items: list[SourceItem]) -> list[SourceItem]:
        by_id: dict[str, SourceItem] = {}
        for item in items:
            existing = by_id.get(item.source_id)
            if existing is None or (item.priority, item.heat_score) > (existing.priority, existing.heat_score):
                by_id[item.source_id] = item
        return list(by_id.values())


class YouTubeProvider(Provider):
    name = "youtube"
    api_base = "https://www.googleapis.com/youtube/v3"

    def __init__(self, settings: Settings, account: Optional[AccountConfig] = None):
        super().__init__(settings, account)
        self.cfg = self.account.sources.youtube or {}
        self.api_key = _env(str(self.cfg.get("api_key_env") or ""), settings.youtube_api_key)
        self.queries = list(self.cfg.get("queries") or [])
        self.days = int(self.cfg.get("days") or settings.youtube_days)
        self.video_duration = str(self.cfg.get("video_duration") or settings.youtube_video_duration)
        self.region_code = str(self.cfg.get("region_code") or settings.youtube_region_code)
        self.language = str(self.cfg.get("language") or settings.youtube_language)

    def _api_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            res = requests.get(
                f"{self.api_base}/{path}",
                params=params,
                headers={"X-Goog-Api-Key": self.api_key},
                timeout=20,
            )
            res.raise_for_status()
            return res.json()
        except requests.RequestException as exc:
            raise RuntimeError(self._error_message(exc)) from None

    def _error_message(self, exc: requests.RequestException) -> str:
        response = getattr(exc, "response", None)
        if response is None:
            return f"YouTube API request failed ({type(exc).__name__})"
        detail = ""
        try:
            error = response.json().get("error", {})
            reasons = [item.get("reason") for item in error.get("errors", []) if item.get("reason")]
            message = str(error.get("message") or "").strip()
            detail = ": ".join(part for part in (", ".join(reasons), message) if part)
        except Exception:
            pass
        text = f"YouTube API {response.status_code}" + (f": {detail}" if detail else "")
        return text.replace(self.api_key, "<redacted>") if self.api_key else text

    def fetch(self, limit: int) -> list[SourceItem]:
        if not self.api_key:
            raise RuntimeError("缺少 YOUTUBE_API_KEY")
        output: list[SourceItem] = []
        published_after = (datetime.now(timezone.utc) - timedelta(days=self.days)).isoformat().replace("+00:00", "Z")
        per_query = max(1, min(50, limit // max(1, len(self.queries))))
        for query in self.queries:
            payload = self._api_get("search", {
                "part": "snippet", "type": "video", "order": "viewCount",
                "publishedAfter": published_after, "maxResults": per_query, "q": query,
                "videoDuration": self.video_duration,
                "regionCode": self.region_code,
                "relevanceLanguage": self.language,
                "safeSearch": "moderate",
            })
            items = payload.get("items", [])
            video_ids = [item.get("id", {}).get("videoId") for item in items]
            details_by_id: dict[str, dict[str, Any]] = {}
            if video_ids:
                details_payload = self._api_get("videos", {
                    "part": "snippet,statistics", "id": ",".join(x for x in video_ids if x),
                })
                details_by_id = {
                    item.get("id"): item for item in details_payload.get("items", [])
                }
            for item in items:
                video_id = item.get("id", {}).get("videoId")
                if not video_id:
                    continue
                detail = details_by_id.get(video_id, {})
                snippet = detail.get("snippet") or item.get("snippet", {})
                thumb = snippet.get("thumbnails", {}).get("high", {}).get("url")
                stats = detail.get("statistics", {})
                views = int(stats.get("viewCount", 0) or 0)
                likes = int(stats.get("likeCount", 0) or 0)
                comments = int(stats.get("commentCount", 0) or 0)
                engagement = math.log1p(views) + 0.35 * math.log1p(likes) + 0.5 * math.log1p(comments)
                ai_score = _ai_score(f"{snippet.get('title', '')} {snippet.get('description', '')}", self.keywords)
                heat_score = engagement + ai_score * 3
                output.append(SourceItem(
                    platform=self.name, source_id=video_id,
                    source_url=f"https://www.youtube.com/watch?v={video_id}",
                    author=snippet.get("channelTitle", ""), title=snippet.get("title", ""),
                    text=snippet.get("description", ""), published_at=snippet.get("publishedAt"),
                    media_url=thumb, media_type="image", media_urls=[thumb] if thumb else [],
                    rights_status="unknown", heat_score=heat_score,
                    heat_label=_heat_label(heat_score), ai_score=ai_score,
                    metadata={**item, "details": detail, "statistics": stats},
                ))
        by_id: dict[str, SourceItem] = {}
        for item in output:
            existing = by_id.get(item.source_id)
            if existing is None or item.heat_score > existing.heat_score:
                by_id[item.source_id] = item
        return sorted(by_id.values(), key=lambda item: item.heat_score, reverse=True)[:limit]


class FeedProvider(Provider):
    name = "web"

    def __init__(self, settings: Settings, account: Optional[AccountConfig] = None):
        super().__init__(settings, account)
        self.feeds = list(self.account.sources.rss)

    def fetch(self, limit: int) -> list[SourceItem]:
        if not self.feeds:
            return []
        output: list[SourceItem] = []
        errors: list[str] = []
        for feed in self.feeds:
            label, url = feed.get("label", ""), feed.get("url", "")
            try:
                xml_text = rss.fetch_feed(url, "xhs-workflow/0.3 (personal feed reader)")
                output.extend(rss.parse_feed(
                    xml_text,
                    feed_label=label or url,
                    feed_url=url,
                    platform=self.name,
                    ai_keywords=self.keywords,
                    ai_floor=0.35,
                    max_images=max(1, self.account.media.max_images),
                ))
            except Exception as exc:
                log.warning("rss feed failed for %s: %s", label, exc)
                errors.append(f"{label}: {exc}")
        by_url: dict[str, SourceItem] = {}
        for item in output:
            by_url.setdefault(item.source_url, item)
        items = sorted(by_url.values(), key=lambda item: item.heat_score, reverse=True)[:limit]
        if not items and errors:
            raise RuntimeError("; ".join(errors))
        return items


def build_providers(account: AccountConfig, settings: Settings) -> list[Provider]:
    providers: list[Provider] = []
    if account.sources.reddit is not None:
        providers.append(RedditProvider(settings, account))
    if account.sources.x is not None:
        providers.append(XProvider(settings, account))
    if account.sources.youtube is not None:
        providers.append(YouTubeProvider(settings, account))
    if account.sources.rss:
        providers.append(FeedProvider(settings, account))

    from .social_providers import InstagramFeedProvider, WeiboFeedProvider, WeiboProvider
    if account.sources.weibo is not None:
        providers.append(WeiboProvider(settings, account))
        if (account.sources.weibo or {}).get("feeds"):
            providers.append(WeiboFeedProvider(settings, account))
    if account.sources.instagram is not None:
        providers.append(InstagramFeedProvider(settings, account))
    return providers