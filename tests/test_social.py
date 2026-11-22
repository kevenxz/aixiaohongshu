from __future__ import annotations

import dataclasses

from xhs_workflow.accounts import AccountConfig, MediaRules, PublishSpec, SourceSpec
from xhs_workflow.social_providers import WeiboProvider
from xhs_workflow.models import SourceItem
from xhs_workflow.rss import parse_feed


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _account(settings) -> AccountConfig:
    return AccountConfig(
        slug="star-gallery",
        name="明星图集",
        content_style="gallery",
        target_posts_per_day=9,
        media=MediaRules(rights_mode="editorial", max_images=9, min_images=3),
        sources=SourceSpec(weibo={
            "uids": [{"uid": "123", "name": "明星A工作室"}],
            "cookie_env": "TEST_WEIBO_COOKIE",
            "days": 3,
            "request_delay": 0,
        }),
    )


def test_weibo_provider_parses_gallery_items(test_settings, monkeypatch):
    payload = {
        "ok": 1,
        "data": {"cards": [
            {"card_type": 9, "mblog": {
                "id": "1001", "bid": "B1001",
                "text": "<a>明星A</a> 今日活动造型",
                "created_at": "今天 10:30",
                "user": {"screen_name": "明星A工作室"},
                "pics": [
                    {"large": {"url": "https://wx1.example/a.jpg"}},
                    {"large": {"url": "https://wx1.example/b.jpg"}},
                ],
                "attitudes_count": 100,
                "comments_count": 10,
                "reposts_count": 5,
            }},
            {"card_type": 9, "mblog": {
                "id": "1002", "text": "转发微博",
                "retweeted_status": {"id": "x"},
                "user": {"screen_name": "明星A工作室"},
            }},
            {"card_type": 9, "mblog": {
                "id": "1003", "text": "视频",
                "page_info": {"type": "video"},
                "user": {"screen_name": "明星A工作室"},
            }},
        ]},
    }
    monkeypatch.setattr(
        "xhs_workflow.social_providers.requests.get",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    items = WeiboProvider(test_settings, _account(test_settings)).fetch(9)

    assert len(items) == 1
    item = items[0]
    assert item.platform == "weibo"
    assert item.source_id == "1001"
    assert item.source_url == "https://m.weibo.cn/detail/1001"
    assert item.media_urls == ["https://wx1.example/a.jpg", "https://wx1.example/b.jpg"]
    assert item.priority is True
    assert item.heat_label == "关注"


def test_weibo_provider_reports_cookie_requirement(test_settings, monkeypatch):
    monkeypatch.setattr(
        "xhs_workflow.social_providers.requests.get",
        lambda *args, **kwargs: FakeResponse({}, status_code=432),
    )
    try:
        WeiboProvider(test_settings, _account(test_settings)).fetch(9)
    except RuntimeError as exc:
        assert "WEIBO_COOKIE" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_rss_parser_extracts_multiple_images():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>star</title>
      <item>
        <title>明星A 活动图集</title>
        <link>https://example.com/post/1</link>
        <pubDate>Sat, 14 Nov 2026 08:00:00 +0800</pubDate>
        <description><![CDATA[
          <p>活动现场</p>
          <img src="https://img.example/1.jpg" />
          <img src="https://img.example/2.jpg" />
        ]]></description>
        <enclosure url="https://img.example/3.jpg" type="image/jpeg" />
      </item>
    </channel></rss>"""
    items = parse_feed(
        xml, feed_label="明星A", feed_url="https://example.com/feed.xml",
        platform="instagram", ai_keywords=["明星"], max_images=3,
    )
    assert len(items) == 1
    assert items[0].media_urls == [
        "https://img.example/3.jpg", "https://img.example/1.jpg", "https://img.example/2.jpg",
    ]
    assert items[0].media_url == "https://img.example/3.jpg"


def test_gallery_llm_prompt_and_risk_flag(test_settings, monkeypatch):
    from xhs_workflow.llm import GALLERY_RISK_FLAG, LLM

    settings = dataclasses.replace(
        test_settings, llm_api_key="key", llm_model="model", llm_throttle_seconds=0
    )
    account = _account(settings)
    account.prompt.persona = "明星图集编辑"
    captured = {}

    def fake_request(payload):
        captured["payload"] = payload
        return {"choices": [{"message": {"content": __import__("json").dumps({
            "title": "明星A活动造型",
            "body": "明星A今日出席活动，造型清爽。" * 6,
            "tags": ["明星A", "活动", "造型", "写真", "红毯", "风格"],
            "risk_flags": [],
        }, ensure_ascii=False)}}]}

    llm = LLM(settings)
    monkeypatch.setattr(llm, "_request", fake_request)
    item = SourceItem(
        platform="weibo", source_id="1", source_url="https://weibo.com/1",
        author="明星A工作室", title="活动图集", text="今日活动造型", ai_score=1,
    )
    draft = llm.transform(item, account)

    system = captured["payload"]["messages"][0]["content"]
    prompt = captured["payload"]["messages"][1]["content"]
    assert "明星图集编辑" in system
    assert "小红书明星图集" in prompt
    assert GALLERY_RISK_FLAG in draft.risk_flags
    assert len(draft.tags) == 6