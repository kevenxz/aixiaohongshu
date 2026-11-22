from __future__ import annotations

import dataclasses
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from fastapi.testclient import TestClient

from xhs_workflow.app import create_app
from xhs_workflow.config import settings
from xhs_workflow.db import Database
from xhs_workflow.models import Draft, SourceItem
from xhs_workflow.publisher import ManualPublisher


@pytest.fixture
def test_settings(tmp_path: Path):
    return dataclasses.replace(
        settings,
        database_path=tmp_path / "workflow.db",
        media_dir=tmp_path / "media",
        export_dir=tmp_path / "exports",
        accounts_file=tmp_path / "accounts.yaml",
        target_posts_per_day=10,
        ai_only=False,
        publisher="manual",
        rss_feeds=[],
        xhs_visibility="公开可见",
        auto_publish=False,
        app_password="",
        app_media_token="",
    )


@pytest.fixture
def db(test_settings) -> Database:
    return Database(test_settings.database_path)


@pytest.fixture
def make_post(db):
    counter = {"value": 0}

    def factory(title: str = "AI signal", platform: str = "reddit", ai_score: float = 1.0,
                media_path: str | None = None) -> int:
        counter["value"] += 1
        index = counter["value"]
        item = SourceItem(
            platform=platform,
            source_id=f"source-{index}",
            source_url=f"https://example.com/{uuid.uuid4()}",
            author="tester",
            title=title,
            text=f"{title} body",
            heat_score=10 - index,
            heat_label="爆发",
            ai_score=ai_score,
        )
        source_id = db.insert_source(item)
        assert source_id is not None
        post_id = db.insert_post(
            source_id,
            Draft(f"{title} draft", f"{title} body", ["ai", "tools"], "来源：https://example.com", ["需人工确认"]),
            media_path=media_path,
        )
        assert post_id is not None
        return post_id

    return factory


@pytest.fixture
def client(test_settings, db):
    class StubPipeline:
        def run_once(self, trigger: str = "manual") -> dict[str, object]:
            return {"trigger": trigger, "created": 0, "errors": [], "skipped": False}

    app = create_app(test_settings, db, StubPipeline(), ManualPublisher(test_settings), enable_scheduler=False)
    with TestClient(app) as test_client:
        yield test_client
