from __future__ import annotations

import sqlite3
from pathlib import Path

from xhs_workflow.db import Database
from xhs_workflow.models import Draft, SourceItem


def _create_v1(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE source_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_url TEXT NOT NULL UNIQUE,
            author TEXT,
            title TEXT,
            text TEXT,
            published_at TEXT,
            media_url TEXT,
            media_type TEXT,
            rights_status TEXT NOT NULL DEFAULT 'unknown',
            heat_score REAL NOT NULL DEFAULT 0,
            heat_label TEXT NOT NULL DEFAULT '',
            ai_score REAL NOT NULL DEFAULT 0,
            content_hash TEXT,
            raw_json TEXT,
            fetched_at TEXT NOT NULL
        );
        CREATE TABLE posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_item_id INTEGER NOT NULL UNIQUE,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            source_note TEXT NOT NULL,
            risk_flags_json TEXT NOT NULL,
            media_path TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT,
            FOREIGN KEY(source_item_id) REFERENCES source_items(id)
        );
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            fetched INTEGER NOT NULL DEFAULT 0,
            created INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            quota_reached INTEGER NOT NULL DEFAULT 0,
            errors_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX idx_posts_status ON posts(status);
        CREATE INDEX idx_posts_created_at ON posts(created_at);
        CREATE INDEX idx_runs_finished_at ON runs(finished_at);
        CREATE UNIQUE INDEX idx_source_content_hash
            ON source_items(content_hash) WHERE content_hash IS NOT NULL;
        """
    )
    conn.execute(
        """INSERT INTO source_items
        (platform, source_id, source_url, author, title, text, rights_status,
         heat_score, heat_label, ai_score, content_hash, raw_json, fetched_at)
        VALUES ('reddit', 's1', 'https://example.com/1', 'a', 'old', 'old body',
                'unknown', 5, '稳定', 1, 'hash-1', '{}', '2026-01-01T00:00:00+00:00')"""
    )
    conn.execute(
        """INSERT INTO posts
        (source_item_id, title, body, tags_json, source_note, risk_flags_json,
         media_path, status, created_at, updated_at)
        VALUES (1, 'old', 'old body', '[]', 'source', '[]', 'data/media/old.jpg',
                'draft', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"""
    )
    conn.execute(
        """INSERT INTO runs
        (trigger, started_at, finished_at, fetched, created, skipped, quota_reached, errors_json)
        VALUES ('manual', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 1, 1, 0, 0, '[]')"""
    )
    conn.commit()
    conn.close()


def test_v1_database_migrates_to_v2(tmp_path):
    path = tmp_path / "workflow.db"
    _create_v1(path)

    db = Database(path)
    backups = list(tmp_path.glob("workflow.db.bak-v1-*"))
    assert backups
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
        indexes = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_source_content_hash" in indexes

    post = db.get_post(1)
    assert post is not None
    assert post["account_id"] == 1
    assert post["media_paths"] == ["data/media/old.jpg"]
    assert db.stats(timezone_name="Asia/Shanghai", target=10)["last_run"]["fetched"] == 1

    item = SourceItem(
        platform="reddit", source_id="s2", source_url="https://example.com/1",
        author="a", title="old", text="old body",
    )
    assert db.insert_source(item, account_id=1) is None
    assert db.insert_source(item, account_id=2) is not None


def test_insert_post_writes_multiple_media_rows(tmp_path):
    db = Database(tmp_path / "workflow.db")
    source_id = db.insert_source(SourceItem(
        platform="weibo", source_id="1", source_url="https://weibo.com/1",
        author="a", title="t", text="body", ai_score=1,
    ))
    post_id = db.insert_post(
        source_id,
        Draft("title", "body", ["tag"], "source", []),
        media_paths=["data/media/a.jpg", "data/media/b.jpg"],
    )
    assert post_id is not None
    post = db.get_post(post_id)
    assert post["media_count"] == 2
    assert post["media_files"] == ["a.jpg", "b.jpg"]
    assert post["media_file"] == "a.jpg"

    assert db.replace_post_media(post_id, ["data/media/c.jpg"], prepared=True)
    updated = db.get_post(post_id)
    assert updated["media_paths"] == ["data/media/c.jpg"]
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM post_media WHERE post_id=?", (post_id,)).fetchone()[0]
    assert count == 1