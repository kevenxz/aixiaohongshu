from __future__ import annotations

import dataclasses

from fastapi.testclient import TestClient

from xhs_workflow.accounts import AccountConfig, MediaRules, PublishSpec, SourceSpec, legacy_account
from xhs_workflow.app import create_app
from xhs_workflow.db import Database
from xhs_workflow.models import Draft, SourceItem
from xhs_workflow.publish_service import PublishService
from xhs_workflow.publisher import ManualPublisher
from xhs_workflow.runtime import AccountRuntime


class StubPipeline:
    def __init__(self):
        self.calls = []

    def run_once(self, trigger: str = "manual") -> dict[str, object]:
        self.calls.append(trigger)
        return {"trigger": trigger, "created": 0, "errors": [], "skipped": False}


def _star_account(settings) -> AccountConfig:
    return AccountConfig(
        slug="star-gallery",
        name="明星图集",
        avatar="星",
        content_style="gallery",
        target_posts_per_day=5,
        media=MediaRules(rights_mode="editorial", max_images=9, min_images=3),
        sources=SourceSpec(weibo={"uids": []}, instagram={"feeds": []}),
        publish=PublishSpec(mode="manual"),
    )


def _add_post(db: Database, account_id: int, title: str, platform: str) -> int:
    source_id = db.insert_source(SourceItem(
        platform=platform, source_id=title, source_url=f"https://example.com/{title}",
        author="tester", title=title, text=f"{title} body", ai_score=1.0,
    ), account_id)
    post_id = db.insert_post(
        source_id, Draft(title, f"{title} body", ["tag"], "来源", []), account_id=account_id
    )
    return post_id


def test_account_isolation_and_routing(test_settings):
    db = Database(test_settings.database_path)
    ai = legacy_account(test_settings)
    star = _star_account(test_settings)
    ids = db.sync_accounts([ai, star])
    ai.id = ids["ai-signals"]
    star.id = ids["star-gallery"]

    ai_post = _add_post(db, ai.id, "ai post", "reddit")
    star_post = _add_post(db, star.id, "star post", "weibo")

    ai_pipeline = StubPipeline()
    star_pipeline = StubPipeline()
    runtimes = [
        AccountRuntime(ai, ai_pipeline, PublishService(test_settings, db, ManualPublisher(test_settings, ai), ai)),
        AccountRuntime(star, star_pipeline, PublishService(test_settings, db, ManualPublisher(test_settings, star), star)),
    ]
    app = create_app(test_settings, db, runtimes, enable_scheduler=False)

    with TestClient(app) as client:
        accounts = client.get("/accounts").json()
        assert [item["slug"] for item in accounts] == ["ai-signals", "star-gallery"]
        assert accounts[1]["platforms"] == ["weibo", "instagram"]

        assert [post["id"] for post in client.get("/hot?account=ai-signals").json()] == [ai_post]
        assert [post["id"] for post in client.get("/hot?account=star-gallery").json()] == [star_post]

        ai_stats = client.get("/stats?account=ai-signals").json()
        star_stats = client.get("/stats?account=star-gallery").json()
        assert ai_stats["captured_today"] == 1
        assert star_stats["captured_today"] == 1
        assert star_stats["target"] == 5
        assert ai_stats["target"] == test_settings.target_posts_per_day

        assert client.post("/run?account=star-gallery").status_code == 200
        assert star_pipeline.calls == ["manual"]
        assert ai_pipeline.calls == []

        assert client.post(f"/posts/{star_post}/approve").status_code == 200
        assert client.post(f"/posts/{star_post}/publish").status_code == 200
        exported = test_settings.export_dir / "star-gallery" / f"post-{star_post}.md"
        assert exported.exists()
        assert db.get_post(ai_post)["status"] == "draft"
        assert client.get("/status?account=nope").status_code == 404


def test_schedule_is_per_account(test_settings):
    db = Database(test_settings.database_path)
    ai = legacy_account(test_settings)
    star = _star_account(test_settings)
    ids = db.sync_accounts([ai, star])
    ai.id, star.id = ids["ai-signals"], ids["star-gallery"]
    runtimes = [
        AccountRuntime(ai, StubPipeline(), PublishService(test_settings, db, ManualPublisher(test_settings, ai), ai)),
        AccountRuntime(star, StubPipeline(), PublishService(test_settings, db, ManualPublisher(test_settings, star), star)),
    ]
    app = create_app(test_settings, db, runtimes, enable_scheduler=False)

    with TestClient(app) as client:
        response = client.patch("/settings/schedule?account=star-gallery", json={
            "collect_time": "11:30",
            "auto_publish_time": "21:40",
            "auto_publish_limit": 2,
        })
        assert response.status_code == 200
        assert response.json()["collect_cron"] == "30 11 * * *"

        ai_status = client.get("/status?account=ai-signals").json()
        star_status = client.get("/status?account=star-gallery").json()
        assert star_status["app"]["collect_cron"] == "30 11 * * *"
        assert ai_status["app"]["collect_cron"] != "30 11 * * *"


def test_legacy_client_still_works(test_settings):
    db = Database(test_settings.database_path)
    app = create_app(test_settings, db, StubPipeline(), ManualPublisher(test_settings), enable_scheduler=False)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/accounts").json()[0]["slug"] == "ai-signals"