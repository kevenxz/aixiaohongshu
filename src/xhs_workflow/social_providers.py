from __future__ import annotations

import logging
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

from . import rss
from .accounts import AccountConfig
from .config import Settings
from .models import SourceItem
from .providers import Provider
from .scoring import ai_score as _ai_score
from .scoring import heat_label as _heat_label

log = logging.getLogger(__name__)

WEIBO_API = "https://m.weibo.cn/api/container/getIndex"
WEIBO_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 "
    "Safari/604.1 Weibo (iPhone14,2)"
)
FEED_UA = "xhs-workflow/0.3 (personal social feed reader)"


class SocialFeedProvider(Provider):
    platform = "web"

    def __init__(self, settings: Settings, account: AccountConfig, feeds: list[dict[str, str]]):
        super().__init__(settings, account)
        self.feeds = feeds

    def fetch(self, limit: int) -> list[SourceItem]:
        if not self.feeds:
            return []
        output: list[SourceItem] = []
        errors: list[str] = []
        for feed in self.feeds:
            label = feed.get("label", "")
            url = feed.get("url", "")
            try:
                xml_text = rss.fetch_feed(url, FEED_UA)
                items = rss.parse_feed(
                    xml_text,
                    feed_label=label or url,
                    feed_url=url,
                    platform=self.platform,
                    ai_keywords=self.keywords,
                    ai_floor=0.5,
                    max_images=max(1, self.account.media.max_images),
                )
            except Exception as exc:
                log.warning("%s feed failed for %s: %s", self.platform, label or url, exc)
                errors.append(f"{label or url}: {exc}")
                continue
            for item in items:
                item.priority = True
                item.heat_label = "关注"
                output.append(item)
        by_url: dict[str, SourceItem] = {}
        for item in output:
            by_url.setdefault(item.source_url, item)
        items = sorted(by_url.values(), key=lambda item: item.heat_score, reverse=True)[:limit]
        if not items and errors:
            raise RuntimeError("; ".join(errors))
        return items


class InstagramFeedProvider(SocialFeedProvider):
    name = "instagram"
    platform = "instagram"

    def __init__(self, settings: Settings, account: AccountConfig):
        cfg = account.sources.instagram or {}
        super().__init__(settings, account, list(cfg.get("feeds") or []))


class WeiboFeedProvider(SocialFeedProvider):
    name = "weibo-feed"
    platform = "weibo"

    def __init__(self, settings: Settings, account: AccountConfig):
        cfg = account.sources.weibo or {}
        super().__init__(settings, account, list(cfg.get("feeds") or []))


class WeiboProvider(Provider):
    name = "weibo"

    def __init__(self, settings: Settings, account: AccountConfig):
        super().__init__(settings, account)
        self.cfg = dict(account.sources.weibo or {})
        self.uids = list(self.cfg.get("uids") or [])
        self.cookie = os.getenv(str(self.cfg.get("cookie_env") or "WEIBO_COOKIE"), "")
        self.days = max(1, int(self.cfg.get("days") or 3))
        self.include_retweets = bool(self.cfg.get("include_retweets", False))
        self.max_images = max(1, account.media.max_images)
        self.request_delay = float(self.cfg.get("request_delay") or 0.8)
        self.timezone = _timezone(settings.app_timezone)

    def fetch(self, limit: int) -> list[SourceItem]:
        if not self.uids:
            return []
        output: list[SourceItem] = []
        errors: list[str] = []
        per_uid = max(1, math.ceil(limit / max(1, len(self.uids))))
        for index, entry in enumerate(self.uids):
            uid = str(entry.get("uid") or "").strip()
            if not uid:
                continue
            if index and self.request_delay:
                time.sleep(self.request_delay)
            try:
                output.extend(self._fetch_uid(uid, str(entry.get("name") or ""), per_uid))
            except Exception as exc:
                log.warning("weibo uid %s failed: %s", uid, exc)
                errors.append(f"{uid}: {exc}")
        by_id: dict[str, SourceItem] = {}
        for item in output:
            existing = by_id.get(item.source_id)
            if existing is None or item.heat_score > existing.heat_score:
                by_id[item.source_id] = item
        items = sorted(by_id.values(), key=lambda item: item.heat_score, reverse=True)[:limit]
        if not items and errors:
            raise RuntimeError("; ".join(errors))
        return items

    def _fetch_uid(self, uid: str, name: str, limit: int) -> list[SourceItem]:
        headers = {
            "User-Agent": WEIBO_UA,
            "Referer": f"https://m.weibo.cn/u/{uid}",
            "X-Requested-With": "XMLHttpRequest",
            "MWeibo-Pwa": "1",
            "Accept": "application/json, text/plain, */*",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        res = requests.get(
            WEIBO_API,
            params={"type": "uid", "value": uid, "containerid": f"107603{uid}", "page": 1},
            headers=headers,
            timeout=20,
        )
        if res.status_code in (403, 432):
            raise RuntimeError(
                f"微博接口返回 {res.status_code}；请在 .env 配置或更新 WEIBO_COOKIE，"
                "或改用 RSSHub feed"
            )
        res.raise_for_status()
        payload = res.json()
        cards = ((payload.get("data") or {}).get("cards") or []) if isinstance(payload, dict) else []
        if not cards:
            message = payload.get("msg") if isinstance(payload, dict) else ""
            raise RuntimeError(f"微博返回空时间线{(': ' + str(message)) if message else ''}")

        output: list[SourceItem] = []
        for card in _flatten_cards(cards):
            mblog = card.get("mblog") if isinstance(card, dict) else None
            if not isinstance(mblog, dict):
                continue
            item = self._parse_mblog(mblog, uid, name)
            if item is None:
                continue
            output.append(item)
            if len(output) >= limit:
                break
        return output

    def _parse_mblog(self, mblog: dict[str, Any], uid: str, account_name: str) -> Optional[SourceItem]:
        if mblog.get("deleted"):
            return None
        if mblog.get("retweeted_status") and not self.include_retweets:
            return None

        text = rss.strip_html(str(mblog.get("text") or ""))
        pics = mblog.get("pics") or []
        page_info = mblog.get("page_info") or {}
        if page_info.get("type") == "video" and not pics:
            return None

        images: list[str] = []
        for pic in pics:
            if not isinstance(pic, dict):
                continue
            url = ((pic.get("large") or {}).get("url") or pic.get("url") or "").strip()
            if url.startswith("//"):
                url = "https:" + url
            if url and url not in images:
                images.append(url)
            if len(images) >= self.max_images:
                break
        if not images and not text:
            return None

        published_at, published_dt = _parse_weibo_time(
            str(mblog.get("created_at") or ""), self.timezone
        )
        if published_dt and published_dt < datetime.now(timezone.utc) - timedelta(days=self.days):
            return None

        reposts = int(mblog.get("reposts_count") or 0)
        comments = int(mblog.get("comments_count") or 0)
        attitudes = int(mblog.get("attitudes_count") or 0)
        engagement = math.log1p(attitudes + 2 * reposts + comments)
        recency = _recency(published_dt)
        ai_score = _ai_score(text, self.keywords)
        heat = engagement + recency + (2.0 if images else 0.0)
        source_id = str(mblog.get("id") or mblog.get("bid") or "")
        if not source_id:
            return None
        user = mblog.get("user") or {}
        author = str(user.get("screen_name") or account_name or name_for_uid(uid))
        title = text[:80] or author
        return SourceItem(
            platform=self.name,
            source_id=source_id,
            source_url=f"https://m.weibo.cn/detail/{source_id}",
            author=author,
            title=title,
            text=text,
            published_at=published_at,
            media_url=images[0] if images else None,
            media_type="image" if images else None,
            media_urls=images,
            rights_status="unknown",
            heat_score=heat,
            heat_label="关注",
            ai_score=ai_score,
            priority=True,
            metadata={
                "stream": "weibo",
                "uid": uid,
                "account_name": account_name,
                "images": images,
                "reposts_count": reposts,
                "comments_count": comments,
                "attitudes_count": attitudes,
                "bid": mblog.get("bid"),
            },
        )


def name_for_uid(uid: str) -> str:
    return f"微博 {uid}"


def _flatten_cards(cards: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        output.append(card)
        for child in card.get("card_group") or []:
            if isinstance(child, dict):
                output.append(child)
    return output


def _timezone(name: str):
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _recency(published: Optional[datetime]) -> float:
    if published is None:
        return 1.0
    age_hours = max(0.0, (datetime.now(timezone.utc) - published).total_seconds() / 3600)
    return max(0.0, 4.0 - age_hours / 24.0)


def _parse_weibo_time(raw: str, tz) -> tuple[Optional[str], Optional[datetime]]:
    raw = (raw or "").strip()
    if not raw:
        return None, None
    now = datetime.now(tz)

    try:
        if raw == "刚刚":
            parsed = now
        elif match := re.fullmatch(r"(\d+)秒前", raw):
            parsed = now - timedelta(seconds=int(match.group(1)))
        elif match := re.fullmatch(r"(\d+)分钟前", raw):
            parsed = now - timedelta(minutes=int(match.group(1)))
        elif match := re.fullmatch(r"(\d+)小时前", raw):
            parsed = now - timedelta(hours=int(match.group(1)))
        elif match := re.fullmatch(r"(\d+)天前", raw):
            parsed = now - timedelta(days=int(match.group(1)))
        elif match := re.fullmatch(r"今天 (\d{1,2}):(\d{2})", raw):
            parsed = now.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
        elif match := re.fullmatch(r"昨天 (\d{1,2}):(\d{2})", raw):
            parsed = (now - timedelta(days=1)).replace(
                hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0
            )
        elif match := re.fullmatch(r"(\d{1,2})月(\d{1,2})日(?: (\d{1,2}):(\d{2}))?", raw):
            month, day = int(match.group(1)), int(match.group(2))
            hour = int(match.group(3) or 0)
            minute = int(match.group(4) or 0)
            parsed = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if parsed > now + timedelta(days=1):
                parsed = parsed.replace(year=parsed.year - 1)
        elif match := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", raw):
            parsed = datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3)),
                int(match.group(4)), int(match.group(5)), int(match.group(6) or 0), tzinfo=tz,
            )
        else:
            parsed = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError, OSError):
        return None, None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(), parsed