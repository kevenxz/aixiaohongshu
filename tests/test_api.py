from __future__ import annotations


def test_health_and_status(client):
    health = client.get("/health").json()
    assert health["ok"] is True
    assert health["scheduler_running"] is False

    status = client.get("/status").json()
    assert status["publisher"]["mode"] == "manual"
    assert status["publisher"]["configured"] is True
    assert "providers" in status
    assert status["app"]["scheduler_running"] is False


def test_stats_reflects_posts_and_status(client, db, make_post):
    first = make_post("first", platform="reddit")
    make_post("second", platform="youtube")
    client.post(f"/posts/{first}/approve")

    stats = client.get("/stats").json()
    assert stats["captured_today"] == 2
    assert stats["drafts"] == 1
    assert stats["approved"] == 1
    assert stats["by_platform"]["reddit"] == 1
    assert stats["by_platform"]["youtube"] == 1
    assert len(stats["hourly"]) == 12


def test_edit_approve_publish_and_markdown(client, make_post, test_settings):
    post_id = make_post("original title")

    draft_publish = client.post(f"/posts/{post_id}/publish")
    assert draft_publish.status_code == 409

    edited = client.patch(f"/posts/{post_id}", json={
        "title": "edited title",
        "body": "edited body",
        "tags": ["#AI", "工具", "工具"],
    })
    assert edited.status_code == 200
    post = edited.json()["post"]
    assert post["title"] == "edited title"
    assert post["tags"] == ["AI", "工具"]

    assert client.post(f"/posts/{post_id}/approve").status_code == 200
    published = client.post(f"/posts/{post_id}/publish")
    assert published.status_code == 200
    body = published.json()
    assert body["result"]["mode"] == "manual"
    exported = test_settings.export_dir / f"post-{post_id}.md"
    assert exported.exists()
    assert "edited title" in exported.read_text(encoding="utf-8")

    detail = client.get(f"/posts/{post_id}").json()
    assert detail["status"] == "published"

    markdown = client.get(f"/posts/{post_id}/markdown")
    assert markdown.status_code == 200
    assert "# edited title" in markdown.text
    assert "attachment" in markdown.headers["content-disposition"]


def test_reject_removes_post_from_hot_queue(client, db, make_post):
    post_id = make_post("to reject")
    assert len(client.get("/hot").json()) == 1

    rejected = client.post(f"/posts/{post_id}/reject")
    assert rejected.status_code == 200
    assert client.get("/hot").json() == []
    rejected_list = client.get("/posts", params={"status": "rejected"}).json()
    assert rejected_list[0]["id"] == post_id


def test_validation_and_missing_post(client, make_post):
    post_id = make_post("validate")
    assert client.patch(f"/posts/{post_id}", json={"title": "   "}).status_code == 422
    assert client.patch(f"/posts/{post_id}", json={}).status_code == 422
    assert client.patch("/posts/9999", json={"title": "x"}).status_code == 404
    assert client.get("/posts/9999").status_code == 404
    assert client.post("/posts/9999/approve").status_code == 404


def test_media_file_is_served_and_exposed(client, make_post, test_settings):
    media_path = test_settings.media_dir / "cover.png"
    media_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    post_id = make_post("with image", media_path=str(media_path))

    hot = client.get("/hot").json()
    post = next(item for item in hot if item["id"] == post_id)
    assert post["media_file"] == "cover.png"

    served = client.get("/media/cover.png")
    assert served.status_code == 200
    assert served.content.startswith(b"\x89PNG")


def test_run_endpoint_returns_pipeline_result(client):
    result = client.post("/run").json()
    assert result["created"] == 0
    assert result["skipped"] is False


def test_update_schedule_persists_times(client, test_settings):
    response = client.patch("/settings/schedule", json={
        "collect_time": "07:30",
        "auto_publish_time": "10:15",
        "auto_publish_limit": 2,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["collect_cron"] == "30 7 * * *"
    assert body["auto_publish_cron"] == "15 10 * * *"
    assert body["auto_publish_limit"] == 2
    assert (test_settings.database_path.parent / "schedule.json").exists()

    status = client.get("/status").json()
    assert status["app"]["collect_cron"] == "30 7 * * *"
    assert status["automation"]["auto_publish_cron"] == "15 10 * * *"
    assert status["automation"]["auto_publish_limit"] == 2


def test_update_schedule_rejects_invalid_time(client):
    response = client.patch("/settings/schedule", json={"collect_time": "25:99"})
    assert response.status_code == 422


def test_publish_queue_exports_and_marks_posts(client, make_post, test_settings):
    first = make_post("queue one")
    second = make_post("queue two")
    for post_id in (first, second):
        assert client.post(f"/posts/{post_id}/approve").status_code == 200

    result = client.post("/queue/publish").json()
    assert result["published"] == 2
    assert result["failed"] == 0
    assert (test_settings.export_dir / f"post-{first}.md").exists()
    assert (test_settings.export_dir / f"post-{second}.md").exists()
    assert client.get(f"/posts/{first}").json()["status"] == "published"
    assert client.get(f"/posts/{second}").json()["status"] == "published"

    again = client.post("/queue/publish").json()
    assert again["published"] == 0
