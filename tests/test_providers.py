import dataclasses

import requests

from xhs_workflow.providers import XProvider, YouTubeProvider, _ai_score


def test_ai_score_uses_word_boundaries_for_latin_keywords():
    keywords = ["ai", "llm", "人工智能"]
    assert _ai_score("email said the train is available", keywords) == 0
    assert _ai_score("AI agents are changing workflows", keywords) > 0
    assert _ai_score("关于人工智能的最新进展", keywords) > 0


def test_ai_score_rewards_multiple_hits():
    keywords = ["ai", "llm", "machine learning"]
    single = _ai_score("an ai product", keywords)
    multiple = _ai_score("an ai product using an llm and machine learning", keywords)
    assert multiple > single


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _tweet(post_id, text, author_id, likes=10, referenced=None, media_keys=None):
    data = {
        "id": post_id,
        "text": text,
        "author_id": author_id,
        "created_at": "2026-10-23T08:00:00Z",
        "public_metrics": {
            "like_count": likes,
            "retweet_count": 2,
            "reply_count": 1,
            "quote_count": 1,
        },
    }
    if referenced:
        data["referenced_tweets"] = referenced
    if media_keys:
        data["attachments"] = {"media_keys": media_keys}
    return data


def test_x_priority_streams_and_ai_search_are_merged(test_settings, monkeypatch):
    config = dataclasses.replace(
        test_settings,
        x_bearer_token="token",
        x_list_id="https://x.com/i/lists/123456?foo=bar",
        x_priority_accounts=["@OpenAI", "AnthropicAI"],
        x_queries=["AI agents"],
        x_priority_all=True,
        x_priority_boost=4,
    )
    list_payload = {
        "data": [
            _tweet("1", "We are launching a new model", "u1", likes=80, media_keys=["m1"]),
            _tweet("2", "A retweet", "u1", referenced=[{"type": "retweeted", "id": "r1"}]),
        ],
        "includes": {
            "users": [{"id": "u1", "username": "OpenAI"}],
            "media": [{"media_key": "m1", "type": "photo", "url": "https://img.example/cover.jpg"}],
        },
    }
    priority_payload = {
        "data": [
            _tweet("3", "Hello world", "u2", likes=5),
            _tweet("1", "duplicate from search", "u1", likes=1),
        ],
        "includes": {"users": [{"id": "u2", "username": "AnthropicAI"}]},
    }
    search_payload = {
        "data": [
            _tweet("4", "AI agents are here", "u3", likes=20),
            _tweet("3", "Hello world", "u2", likes=1000),
        ],
        "includes": {"users": [{"id": "u3", "username": "researcher"}]},
    }
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append((url, params))
        if "/lists/" in url:
            return FakeResponse(list_payload)
        if "from:" in params["query"]:
            return FakeResponse(priority_payload)
        return FakeResponse(search_payload)

    monkeypatch.setattr("xhs_workflow.providers.requests.get", fake_get)
    items = XProvider(config).fetch(10)

    by_id = {item.source_id: item for item in items}
    assert set(by_id) == {"1", "3", "4"}
    assert by_id["1"].priority is True
    assert by_id["1"].heat_label == "关注"
    assert by_id["1"].author == "@OpenAI"
    assert by_id["1"].media_url == "https://img.example/cover.jpg"
    assert by_id["3"].priority is True
    assert by_id["3"].author == "@AnthropicAI"
    assert by_id["4"].priority is False

    list_calls = [call for call in calls if "/lists/" in call[0]]
    assert list_calls and "/lists/123456/tweets" in list_calls[0][0]
    priority_queries = [params["query"] for _, params in calls if "from:" in (params or {}).get("query", "")]
    assert "(from:OpenAI OR from:AnthropicAI) -is:retweet" in priority_queries
    assert any("AI agents" in (params or {}).get("query", "") for _, params in calls)


def test_youtube_error_redacts_api_key(test_settings):
    config = dataclasses.replace(test_settings, youtube_api_key="secret-youtube-key")

    class FakeResponse:
        status_code = 403

        def json(self):
            return {
                "error": {
                    "status": "PERMISSION_DENIED",
                    "message": "Requests to this API youtube method are blocked.",
                    "errors": [{"reason": "forbidden"}],
                }
            }

    error = requests.HTTPError(
        "https://www.googleapis.com/youtube/v3/search?key=secret-youtube-key",
        response=FakeResponse(),
    )
    message = YouTubeProvider(config)._error_message(error)
    assert "secret-youtube-key" not in message
    assert "403" in message
    assert "forbidden" in message


def test_x_priority_items_can_still_require_ai_filter(test_settings, monkeypatch):
    config = dataclasses.replace(
        test_settings,
        x_bearer_token="token",
        x_priority_accounts=["OpenAI"],
        x_queries=[],
        x_priority_all=False,
    )
    payload = {
        "data": [_tweet("9", "unrelated news", "u1")],
        "includes": {"users": [{"id": "u1", "username": "OpenAI"}]},
    }
    monkeypatch.setattr(
        "xhs_workflow.providers.requests.get",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    item = XProvider(config).fetch(10)[0]
    assert item.heat_label == "关注"
    assert item.priority is False
