from __future__ import annotations

import dataclasses

from fastapi.testclient import TestClient

from xhs_workflow.app import create_app
from xhs_workflow.db import Database
from xhs_workflow.publisher import ManualPublisher


class StubPipeline:
    def run_once(self, trigger: str = "manual") -> dict[str, object]:
        return {"trigger": trigger, "created": 0, "errors": [], "skipped": False}


def test_basic_auth_and_media_token(test_settings):
    config = dataclasses.replace(
        test_settings,
        app_username="admin",
        app_password="secret-pass",
        app_media_token="media-token",
    )
    db = Database(config.database_path)
    app = create_app(config, db, StubPipeline(), ManualPublisher(config), enable_scheduler=False)
    media = config.media_dir / "image.png"
    media.write_bytes(b"fake-png")

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/posts").status_code == 401
        assert client.get("/posts", auth=("admin", "wrong")).status_code == 401
        assert client.get("/posts", auth=("admin", "secret-pass")).status_code == 200

        assert client.get("/media/image.png").status_code == 401
        assert client.get("/media/image.png?token=wrong").status_code == 401
        assert client.get("/media/image.png?token=media-token").status_code == 200
        assert client.get("/media/image.png", auth=("admin", "secret-pass")).status_code == 200
