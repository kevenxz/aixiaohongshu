from __future__ import annotations

import dataclasses

import pytest

from xhs_workflow.providers import FeedProvider, RedditProvider
from xhs_workflow.rss import parse_feed

ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>r/artificial</title>
  <entry>
    <title>AI agents are learning to use tools</title>
    <link rel="alternate" href="https://www.reddit.com/r/artificial/comments/abc/post/"/>
    <author><name>/u/tester</name><uri>https://www.reddit.com/user/tester</uri></author>
    <published>2026-10-23T08:00:00+00:00</published>
    <updated>2026-10-23T08:00:00+00:00</updated>
    <content type="html">&lt;p&gt;A deep dive into &lt;b&gt;AI agents&lt;/b&gt; and LLM tools.&lt;/p&gt;&lt;img src="https://preview.redd.it/demo.jpg" /&gt;</content>
    <media:thumbnail xmlns:media="http://search.yahoo.com/mrss/" url="https://preview.redd.it/demo.jpg"/>
    <category term="AI"/>
  </entry>
</feed>
"""

RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>OpenAI News</title>
    <item>
      <title>New tools for building agents</title>
      <link>https://openai.com/index/new-tools-for-agents</link>
      <description>A practical guide to AI agents and tools.</description>
      <dc:creator>OpenAI</dc:creator>
      <pubDate>Fri, 23 Oct 2026 08:00:00 GMT</pubDate>
      <category>Product</category>
    </item>
  </channel>
</rss>
"""


def test_parse_atom_feed_strips_html_and_scores_ai():
    items = parse_feed(
        ATOM_FEED, feed_label="r/artificial",
        feed_url="https://www.reddit.com/r/artificial/hot/.rss",
        platform="reddit", ai_keywords=["ai", "llm"], ai_floor=0.35,
    )
    assert len(items) == 1
    item = items[0]
    assert item.title == "AI agents are learning to use tools"
    assert item.source_url.startswith("https://www.reddit.com/r/artificial/comments/")
    assert item.author == "/u/tester"
    assert "<b>" not in item.text
    assert "AI agents" in item.text
    assert item.ai_score >= 0.35
    assert item.platform == "reddit"
    assert item.media_url == "https://preview.redd.it/demo.jpg"
    assert item.metadata["feed"] == "r/artificial"


def test_parse_rss_feed_extracts_creator_and_date():
    items = parse_feed(
        RSS_FEED, feed_label="OpenAI",
        feed_url="https://openai.com/news/rss.xml",
        platform="web", ai_keywords=["ai", "agents"],
    )
    assert len(items) == 1
    item = items[0]
    assert item.title == "New tools for building agents"
    assert item.author == "OpenAI"
    assert item.published_at is not None
    assert item.published_at.startswith("2026-10-23")
    assert item.metadata["categories"] == ["Product"]
    assert item.platform == "web"


def test_reddit_rss_fallback_without_credentials(test_settings, monkeypatch):
    config = dataclasses.replace(
        test_settings,
        reddit_mode="auto",
        reddit_client_id="",
        reddit_client_secret="",
        reddit_subreddits=["artificial"],
        reddit_sort="hot",
    )
    monkeypatch.setattr("xhs_workflow.rss.fetch_feed", lambda url, user_agent: ATOM_FEED)
    items = RedditProvider(config).fetch(5)
    assert len(items) == 1
    assert items[0].platform == "reddit"
    assert items[0].ai_score >= 0.35


def test_reddit_api_mode_requires_credentials(test_settings):
    config = dataclasses.replace(
        test_settings,
        reddit_mode="api",
        reddit_client_id="",
        reddit_client_secret="",
    )
    with pytest.raises(RuntimeError, match="缺少 REDDIT_CLIENT_ID"):
        RedditProvider(config).fetch(1)


def test_feed_provider_returns_web_items(test_settings, monkeypatch):
    config = dataclasses.replace(
        test_settings,
        rss_feeds=["OpenAI|https://openai.com/news/rss.xml"],
        ai_keywords=["ai", "agents"],
    )
    monkeypatch.setattr("xhs_workflow.rss.fetch_feed", lambda url, user_agent: RSS_FEED)
    items = FeedProvider(config).fetch(5)
    assert len(items) == 1
    assert items[0].platform == "web"
    assert items[0].ai_score >= 0.35
