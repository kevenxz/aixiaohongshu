from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import requests

from .models import SourceItem
from .scoring import ai_score, heat_label

MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 8000


class FeedError(RuntimeError):
    pass


def fetch_feed(url: str, user_agent: str, timeout: int = 20) -> str:
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
    }
    for attempt in range(2):
        res = requests.get(url, headers=headers, timeout=timeout)
        if res.status_code == 429 and attempt == 0:
            retry_after = res.headers.get("Retry-After", "2")
            try:
                delay = min(10, max(1, int(retry_after)))
            except ValueError:
                delay = 2
            time.sleep(delay)
            continue
        res.raise_for_status()
        if len(res.content) > MAX_FEED_BYTES:
            raise FeedError(f"feed exceeds {MAX_FEED_BYTES} bytes")
        return res.text
    raise FeedError("feed request rate limited")


def parse_feed(xml_text: str, *, feed_label: str, feed_url: str, platform: str,
               ai_keywords: list[str], ai_floor: float = 0.0,
               max_images: int = 1) -> list[SourceItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FeedError(f"invalid feed XML: {exc}") from exc

    root_name = _local(root.tag)
    if root_name == "feed":
        entries = [child for child in root if _local(child.tag) == "entry"]
    elif root_name == "rss":
        channel = next((child for child in root if _local(child.tag) == "channel"), None)
        entries = [child for child in channel if _local(child.tag) == "item"] if channel is not None else []
    elif root_name == "rdf":
        entries = [child for child in root if _local(child.tag) == "item"]
    else:
        raise FeedError(f"unsupported feed root: {root_name}")

    output: list[SourceItem] = []
    for entry in entries:
        link = _entry_link(entry)
        title = _first_text(entry, ("title",))
        if not link:
            continue
        link = urljoin(feed_url, link)
        content_html = _first_text(entry, ("content", "encoded", "summary", "description"))
        body = strip_html(content_html)
        body = body[:MAX_TEXT_CHARS]
        if not title and not body:
            continue

        published_at, published_dt = _published(entry)
        author = _author(entry) or feed_label
        categories = _categories(entry)
        image_urls = _entry_images(entry, content_html, feed_url, max_images)
        image_url = image_urls[0] if image_urls else None
        score = max(ai_score(f"{title} {body}", ai_keywords), ai_floor)
        recency = _recency(published_dt)
        heat = score * 3 + recency
        output.append(SourceItem(
            platform=platform,
            source_id=link,
            source_url=link,
            author=author,
            title=title or body[:80],
            text=body or title,
            published_at=published_at,
            media_url=image_url,
            media_type="image" if image_url else None,
            media_urls=image_urls,
            rights_status="unknown",
            heat_score=heat,
            heat_label=heat_label(heat),
            ai_score=score,
            metadata={
                "source_type": "rss",
                "feed": feed_label,
                "feed_url": feed_url,
                "categories": categories[:10],
                "images": image_urls,
            },
        ))
    return output


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _first_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in element:
        if _local(child.tag) in names:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def _entry_link(entry: ET.Element) -> str:
    fallback = ""
    for child in entry:
        if _local(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        rel = child.attrib.get("rel", "alternate").strip().lower()
        if href and rel in ("alternate", ""):
            return href
        if href and not fallback:
            fallback = href
        text = (child.text or "").strip()
        if text:
            return text
    return fallback


def _author(entry: ET.Element) -> str:
    for child in entry:
        if _local(child.tag) not in ("author", "creator"):
            continue
        for sub in child:
            if _local(sub.tag) in ("name", "username"):
                name = (sub.text or "").strip()
                if name:
                    return name
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return ""


def _entry_image(entry: ET.Element, content_html: str, feed_url: str) -> str | None:
    images = _entry_images(entry, content_html, feed_url, 1)
    return images[0] if images else None


def _entry_images(entry: ET.Element, content_html: str, feed_url: str,
                  max_images: int = 1) -> list[str]:
    if max_images <= 0:
        return []
    images: list[str] = []

    def add(value: str) -> None:
        url = urljoin(feed_url, html.unescape(value.strip()))
        if url and url not in images:
            images.append(url)

    for child in entry:
        local = _local(child.tag)
        candidates = list(child) if local == "group" else [child]
        for candidate in candidates:
            candidate_local = _local(candidate.tag)
            if candidate_local not in ("thumbnail", "content", "enclosure"):
                continue
            url = (candidate.attrib.get("url") or "").strip()
            if not url:
                continue
            content_type = (candidate.attrib.get("type") or "").lower()
            medium = (candidate.attrib.get("medium") or "").lower()
            if candidate_local == "thumbnail" or content_type.startswith("image/") or medium == "image":
                add(url)
                if len(images) >= max_images:
                    return images[:max_images]

    for match in re.finditer(
        r'<img[^>]+src=["\']([^"\']+)["\']', html.unescape(content_html or ""), re.IGNORECASE
    ):
        add(match.group(1))
        if len(images) >= max_images:
            break
    return images[:max_images]


def _categories(entry: ET.Element) -> list[str]:
    categories = []
    for child in entry:
        if _local(child.tag) == "category":
            value = (child.attrib.get("term") or child.text or "").strip()
            if value and value not in categories:
                categories.append(value)
    return categories


def _published(entry: ET.Element) -> tuple[str | None, datetime | None]:
    raw = _first_text(entry, ("published", "updated", "pubdate", "date"))
    if not raw:
        return None, None
    try:
        if "," in raw:
            parsed = parsedate_to_datetime(raw)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat(), parsed
    except (TypeError, ValueError):
        return raw, None


def _recency(published: datetime | None) -> float:
    if published is None:
        return 1.0
    age_hours = max(0.0, (datetime.now(timezone.utc) - published).total_seconds() / 3600)
    return max(0.0, 4.0 - age_hours / 24.0)


def strip_html(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


_strip_html = strip_html
