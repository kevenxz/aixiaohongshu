from pathlib import Path

from xhs_workflow.db import Database
from xhs_workflow.models import Draft, SourceItem


def _item(source_url: str, title: str = "title", text: str = "text") -> SourceItem:
    return SourceItem(
        platform="test", source_id="1", source_url=source_url, author="a",
        title=title, text=text, heat_score=9.0, heat_label="爆发", ai_score=1.0,
    )


def test_insert_deduplicates_source(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    item = _item("https://example.com/1")
    first = db.insert_source(item)
    second = db.insert_source(item)
    assert first is not None
    assert second is None
    db.insert_post(first, Draft("t", "b", ["#x"], "s", []))
    posts = db.list_posts("draft")
    assert len(posts) == 1
    assert posts[0]["heat_label"] == "爆发"
    assert posts[0]["ai_score"] == 1.0


def test_content_hash_deduplicates_different_urls(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    assert db.insert_source(_item("https://example.com/1", "Same title", "Same body")) is not None
    assert db.insert_source(_item("https://example.com/2", "  Same   title ", "same body")) is None


def test_update_post_and_status(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    source_id = db.insert_source(_item("https://example.com/1"))
    post_id = db.insert_post(source_id, Draft("old", "body", ["ai"], "s", []))
    assert post_id is not None

    assert db.update_post(post_id, title="new", tags=["ml", "ai"])
    post = db.get_post(post_id)
    assert post is not None
    assert post["title"] == "new"
    assert post["tags"] == ["ml", "ai"]

    assert db.update_status(post_id, "rejected")
    assert db.list_posts(status="rejected")[0]["id"] == post_id
    assert db.list_posts(statuses=("draft", "approved")) == []


def test_stats_counts_today_and_statuses(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    source_id = db.insert_source(_item("https://example.com/1", "one", "body one"))
    post_id = db.insert_post(source_id, Draft("one", "body", ["ai"], "s", []))
    source_two = db.insert_source(_item("https://example.com/2", "two", "body two"))
    db.insert_post(source_two, Draft("two", "body", ["ai"], "s", []))
    db.update_status(post_id, "approved")

    stats = db.stats(timezone_name="Asia/Shanghai", target=10)
    assert stats["captured_today"] == 2
    assert stats["drafts"] == 1
    assert stats["approved"] == 1
    assert stats["total"] == 2
    assert stats["by_platform"]["test"] == 2
    assert len(stats["hourly"]) == 12
    assert stats["last_run"] is None


def test_run_history_and_daily_count(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    from xhs_workflow.db import start_of_local_day_iso
    from xhs_workflow.models import now_iso

    db.record_run("manual", now_iso(), now_iso(), fetched=4, created=2,
                  skipped=2, quota_reached=True, errors=["x"])
    stats = db.stats(timezone_name="Asia/Shanghai", target=10)
    assert stats["last_run"]["fetched"] == 4
    assert stats["last_run"]["quota_reached"] is True
    assert stats["last_run"]["errors"] == ["x"]
    assert db.count_posts_since(start_of_local_day_iso("Asia/Shanghai")) == 0
