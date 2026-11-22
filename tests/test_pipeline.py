from __future__ import annotations

import dataclasses
import uuid

from xhs_workflow.db import Database
from xhs_workflow.models import Draft, SourceItem
from xhs_workflow.pipeline import Pipeline


def _item(title: str, heat: float, text: str | None = None, url: str | None = None) -> SourceItem:
    return SourceItem(
        platform="fake", source_id=str(uuid.uuid4()), source_url=url or f"https://example.com/{uuid.uuid4()}",
        author="author", title=title, text=text or f"{title} body", heat_score=heat, ai_score=1.0,
    )


class FakeProvider:
    name = "fake"

    def __init__(self, items: list[SourceItem]):
        self.items = items
        self.calls = 0

    def fetch(self, limit: int) -> list[SourceItem]:
        self.calls += 1
        return self.items[:limit]


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def transform(self, item: SourceItem, account=None) -> Draft:
        self.calls += 1
        return Draft(f"{item.title} draft", "整理后的正文", ["ai"], f"来源：{item.source_url}", [])


class FailingLLM:
    def transform(self, item: SourceItem, account=None) -> Draft:
        raise RuntimeError("gateway down")


def test_daily_quota_selects_hottest_items(test_settings):
    config = dataclasses.replace(test_settings, target_posts_per_day=2)
    db = Database(config.database_path)
    provider = FakeProvider([_item("low", 1), _item("mid", 5), _item("hot", 9)])
    pipeline = Pipeline(config, db, llm=FakeLLM(), providers=[provider])

    result = pipeline.run_once()

    assert result["fetched"] == 3
    assert result["created"] == 2
    assert result["quota_reached"] is True
    assert result["remaining_today"] == 0
    assert {post["title"] for post in db.list_posts()} == {"hot draft", "mid draft"}
    assert db.stats(config.app_timezone, target=2)["last_run"]["created"] == 2


def test_duplicate_content_is_not_created_again(test_settings):
    config = dataclasses.replace(test_settings, target_posts_per_day=10)
    db = Database(config.database_path)
    original = _item("same story", 5, text="the same body")
    Pipeline(config, db, llm=FakeLLM(), providers=[FakeProvider([original])]).run_once()

    duplicate = _item("same story", 9, text="the same body")
    result = Pipeline(config, db, llm=FakeLLM(), providers=[FakeProvider([duplicate])]).run_once()

    assert result["created"] == 0
    assert result["skipped"] == 1
    assert len(db.list_posts()) == 1


def test_batch_duplicates_are_skipped_before_writing(test_settings):
    config = dataclasses.replace(test_settings, target_posts_per_day=10)
    db = Database(config.database_path)
    a = _item("same", 5, url=f"https://example.com/{uuid.uuid4()}")
    b = _item("same", 4, url=f"https://example.com/{uuid.uuid4()}")
    pipeline = Pipeline(config, db, llm=FakeLLM(), providers=[FakeProvider([a, b])])

    result = pipeline.run_once()

    assert result["fetched"] == 2
    assert result["created"] == 1
    assert len(db.list_posts()) == 1


def test_llm_failure_skips_post_and_source(test_settings):
    config = dataclasses.replace(test_settings, target_posts_per_day=10)
    db = Database(config.database_path)
    pipeline = Pipeline(config, db, llm=FailingLLM(), providers=[FakeProvider([_item("failed", 5)])])

    result = pipeline.run_once()

    assert result["created"] == 0
    assert result["skipped"] == 1
    assert any(error.startswith("llm:") for error in result["errors"])
    assert db.list_posts() == []


def test_priority_items_bypass_ai_filter(test_settings):
    config = dataclasses.replace(test_settings, target_posts_per_day=10, ai_only=True, min_ai_score=0.9)
    db = Database(config.database_path)
    priority_item = _item("priority", 5)
    priority_item.ai_score = 0.0
    priority_item.priority = True
    normal_item = _item("normal", 6)
    normal_item.ai_score = 0.0
    pipeline = Pipeline(config, db, llm=FakeLLM(), providers=[FakeProvider([priority_item, normal_item])])

    result = pipeline.run_once()

    assert result["created"] == 1
    assert [post["title"] for post in db.list_posts()] == ["priority draft"]


def test_zero_target_stops_before_fetching(test_settings):
    config = dataclasses.replace(test_settings, target_posts_per_day=0)
    db = Database(config.database_path)
    provider = FakeProvider([_item("ignored", 5)])
    pipeline = Pipeline(config, db, llm=FakeLLM(), providers=[provider])

    result = pipeline.run_once()

    assert provider.calls == 0
    assert result["fetched"] == 0
    assert result["quota_reached"] is True
    assert db.stats(config.app_timezone, target=0)["last_run"]["quota_reached"] is True
